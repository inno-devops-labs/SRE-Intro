# Lab 12 — Advanced Kubernetes resilience

## Task 1 — More replicas, failover, and PDBs

### 12.1: Scale the services

I set `replicas: 2` in `k8s/events.yaml`, `k8s/payments.yaml`, and `k8s/notifications.yaml`. The gateway Rollout stayed at five replicas. The final live state was:

```text
events          2/2
payments        2/2
notifications   2/2
gateway         desired 5, current 5, available 5
```

The events and payments manifests use locally built images with `imagePullPolicy: Never`. Their old GHCR images could not be pulled on this device. `scripts/lab12_build_images.ps1` builds and imports the local images used in the lab.

### 12.2: Kill pods under load

Before the test, `sum(increase(gateway_requests_total{status=~"5.."}[1m]))` was **0**. I deleted one gateway pod and one events pod with `--wait=false`. One live replica kept the events Service ready, and the controllers made replacement pods. After the replacement, the same one-minute 5xx query was **0**. Both workloads returned to their target replica counts.

### 12.3: PodDisruptionBudgets

`k8s/pdb.yaml` contains four `policy/v1` budgets. Each selector uses the service's `app` label. The live output was:

```text
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
events-pdb          1               N/A               1
gateway-pdb         2               N/A               3
notifications-pdb   N/A             1                 1
payments-pdb        1               N/A               1
```

With three gateway pods and `minAvailable: 1`, at most **two** healthy pods can be evicted at once. This lab keeps at least two of five gateway pods so the service still has more capacity while a node is replaced. Setting the minimum to four could block planned maintenance for too long.

### 12.4: Topology spread

The live gateway pod template showed:

```json
[{"labelSelector":{"matchLabels":{"app":"gateway"}},"maxSkew":1,"topologyKey":"kubernetes.io/hostname","whenUnsatisfiable":"ScheduleAnyway"}]
```

`kubectl get pods -l app=gateway -o wide` showed all five gateway pods on `k3d-quickticket-server-0`. This is expected on one node. With three nodes, five pods should spread **2/2/1**; seven pods should spread **3/2/2**. `ScheduleAnyway` lets the lab work on one node while still asking the scheduler to prefer an even spread on more nodes.

### 12.5: A PDB blocks eviction

I temporarily set `events-pdb` to `minAvailable: 2`. It then showed zero allowed disruptions. A real request to the pod eviction API returned:

```json
{
  "status": "Failure",
  "message": "Cannot evict pod as it would violate the pod's disruption budget.",
  "reason": "TooManyRequests",
  "details": {
    "causes": [{
      "reason": "DisruptionBudget",
      "message": "The disruption budget events-pdb needs 2 healthy pods and has 2 currently"
    }]
  },
  "code": 429
}
```

I restored `minAvailable: 1`; `kubectl get pdb events-pdb` again showed one allowed disruption.

## Task 2 — Graceful shutdown and a live index migration

### 12.6: Gateway shutdown and restart

The gateway pod template now has:

```yaml
terminationGracePeriodSeconds: 40
containers:
  - name: gateway
    lifecycle:
      preStop:
        exec:
          command: ["sh", "-c", "sleep 10"]
    readinessProbe:
      httpGet: {path: /health, port: 8080}
      periodSeconds: 2
      failureThreshold: 1
```

The short readiness interval helps remove a pod from Service endpoints quickly. The ten-second preStop gives endpoint changes time to reach clients before Uvicorn stops. I restarted the Rollout through `spec.restartAt` because the `kubectl argo rollouts` plugin was not installed on this device. Before and after the full restart, the three-minute Prometheus 5xx increase was **0**. All five new gateway pods became Ready.

### 12.7: Concurrent index migration

The migration `c11800000001_index_events_event_date_concurrently.py` uses:

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date", "events", ["event_date"],
            postgresql_concurrently=True, if_not_exists=True,
        )

def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date", table_name="events",
            postgresql_concurrently=True, if_exists=True,
        )
```

I ran this revision under mixedload. In the stable replay, the gateway 5xx counter was **0 before and 0 after**. `\d events` showed `"idx_events_event_date" btree (event_date)`. The later bonus migration dropped `event_date`, so PostgreSQL also removed this index in the final schema.

`CONCURRENTLY` lets normal reads and writes continue while PostgreSQL builds an index. A normal index build on a table with ten million rows can block writes for a long time. The `autocommit_block` is required because PostgreSQL does not allow this command inside a transaction block.

### 12.8: Expand-and-contract design

1. Migration 1 adds nullable `scheduled_at`. Old code still uses `event_date`.
2. Deploy A writes both columns when there is a write path. It reads `COALESCE(scheduled_at, event_date)`.
3. Migration 2 fills missing `scheduled_at` values from `event_date`, then makes `scheduled_at` `NOT NULL`.
4. Deploy B reads and writes only `scheduled_at`. It still sends the JSON field `event_date` so clients keep the same response shape.
5. Migration 3 drops `event_date` only after every Deploy A pod has stopped.

QuickTicket has no runtime event-date write endpoint; it only inserts events from `seed.sql` at first boot. So the dual-write step had no runtime write to change. I updated the seed for new installations. Dropping `event_date` before Deploy B was fully rolled out would make Deploy A's `COALESCE` query fail with a missing-column error.

### 12.9: HPA observation

I also applied `k8s/gateway-hpa.yaml` and ran the 200-user, 120-second Locust Job in `labs/lab12/load-hpa.yaml`. The HPA first showed `cpu: 98%/70%` and requested seven replicas. Later it showed `cpu: 137%/70%` and requested twelve, its configured maximum. This shows the HPA controller reacted to CPU use. The single k3d node could not provide real extra capacity: the load test produced many 429 and 5xx responses, and the Locust Job finished Failed. This optional stress run happened **after** the zero-error migration proof above. I deleted the HPA and Job and returned the gateway to five replicas.

## Bonus Task — Execute the rename under live traffic

### 12.10–12.11: Baseline and migration 1

The first attempt used old GHCR image references, and the new pods could not pull them. I fixed the manifests to use local images, saved a database backup at `submissions/evidence/lab12/pre-rerun.dump`, reversed the safe migration steps, and ran the whole five-step sequence again. The numbers below are from this clean replay with all images ready and the gateway 5xx counter at **0**.

Migration 1 adds `scheduled_at` as nullable. Its upgrade body is:

```python
op.add_column("events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))
```

The 5xx count after migration 1 was **0**. The old `event_date` remained available.

### 12.12: Deploy A

I rolled both events pods to the `quickticket-events:lab12-a` image. Its two SELECTs and sort used the fallback:

```sql
COALESCE(e.scheduled_at, e.event_date) AS event_date
ORDER BY COALESCE(e.scheduled_at, e.event_date)
```

Both new pods were Ready. The 5xx count after Deploy A was **0**.

### 12.13: Migration 2

The backfill upgrade body is:

```python
op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
op.alter_column("events", "scheduled_at", nullable=False)
```

The query `SELECT count(*) FROM events WHERE scheduled_at IS NULL OR scheduled_at <> event_date` returned **0**. The 5xx count after migration 2 was **0**. Deploy A can read either value while the backfill runs.

### 12.14: Deploy B

I rolled both events pods to `quickticket-events:lab12-b`. Its reads now use:

```sql
e.scheduled_at AS event_date
ORDER BY e.scheduled_at
```

The alias keeps the public JSON response unchanged. I changed the table and INSERT column in `app/seed.sql` to `scheduled_at`. When both Deploy B pods had replaced Deploy A, the 5xx count was still **0**.

### 12.15: Migration 3 and final proof

The contract migration drops the old column. Its upgrade body is `op.drop_column("events", "event_date")`. Its downgrade adds a nullable `event_date`, copies data back from `scheduled_at`, then makes it `NOT NULL`.

| Checkpoint | Gateway 5xx counter |
|---|---:|
| Baseline | 0 |
| After migration 1 | 0 |
| After Deploy A | 0 |
| After migration 2 | 0 |
| After Deploy B | 0 |
| After migration 3 | 0 |

The final `\d events` showed `scheduled_at timestamp with time zone NOT NULL` and no `event_date`. Alembic reported revision `c11800000004`. The gateway still had five Ready pods, and events, payments, and notifications each had two Ready pods.

Migration 3 is the step that would break the service if moved earlier: Deploy A still names `event_date` in its SQL. For a large table, I would backfill in small transactions:

```text
last_id = 0
repeat:
    rows = SELECT id FROM events WHERE id > last_id
           AND scheduled_at IS NULL ORDER BY id LIMIT 10000
    if rows is empty: stop
    UPDATE events SET scheduled_at = event_date WHERE id in rows
    commit
    last_id = largest id in rows
    sleep briefly
check for new NULL rows, then add NOT NULL
```

Migration 3's database downgrade alone is not a safe app rollback after Deploy B. Deploy B writes only `scheduled_at`, so an old-code pod could read stale `event_date` after the copy. A safe rollback needs a period when both versions can read and write both fields, or a reverse sync that stays active until every old pod is gone. Then the code and schema can be rolled back in the correct order.
