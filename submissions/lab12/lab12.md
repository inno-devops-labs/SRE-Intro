# Lab 12 — Advanced Kubernetes Resilience

## Task 1 — Multi-Replica Failover and PDBs

### 12.1: Replica counts

`events`, `payments` and `notifications` were scaled to two replicas in their manifests. The gateway remains a five-replica Argo Rollout.

```text
$ kubectl get deploy,rollout
NAME                            READY   UP-TO-DATE   AVAILABLE
deployment.apps/events          2/2     2            2
deployment.apps/mixedload       2/2     2            2
deployment.apps/notifications   2/2     2            2
deployment.apps/payments        2/2     2            2
deployment.apps/postgres        1/1     1            1
deployment.apps/redis           1/1     1            1

NAME                          DESIRED   CURRENT   UP-TO-DATE   AVAILABLE
rollout.argoproj.io/gateway   5         5         5            5
```

### 12.2: Coordinated pod-kill under load

The first run exposed two 5xx responses while the events pod was terminating. I therefore gave events the same endpoint-propagation protection: a 10-second `preStop` and `terminationGracePeriodSeconds: 40`. After that correction, deleting one gateway pod and one events pod simultaneously produced no new 5xx:

```text
pod "gateway-76f675884-..." deleted
pod "events-55c8cc94c8-..." deleted
before=46
after=46
delta=0
```

Direct pod deletion is not a voluntary disruption and therefore is not blocked by a PDB. Availability here comes from replicas, readiness and graceful termination.

### 12.3: PodDisruptionBudgets

Four PDBs are defined in `k8s/pdb.yaml`.

```text
$ kubectl get pdb
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
events-pdb          1               N/A               1
gateway-pdb         2               N/A               3
notifications-pdb   N/A             1                 1
payments-pdb        1               N/A               1
```

### 12.4: Topology spread

Live Rollout field:

```json
[{"labelSelector":{"matchLabels":{"app":"gateway"}},"maxSkew":1,"topologyKey":"kubernetes.io/hostname","whenUnsatisfiable":"ScheduleAnyway"}]
```

Actual placement on this single-node k3d cluster:

```text
NAME                      READY   STATUS    NODE
gateway-76f675884-5c6lq   1/1     Running   k3d-quickticket-server-0
gateway-76f675884-6fg2q   1/1     Running   k3d-quickticket-server-0
gateway-76f675884-8hbr6   1/1     Running   k3d-quickticket-server-0
gateway-76f675884-hx9mt   1/1     Running   k3d-quickticket-server-0
gateway-76f675884-p2p9h   1/1     Running   k3d-quickticket-server-0
```

`ScheduleAnyway` keeps the Rollout schedulable on one node. On three nodes, `maxSkew: 1` distributes five pods as **2/2/1**, and seven pods as **3/2/2**.

### 12.5: PDB enforcement through the Eviction API

For the test, `events-pdb` was temporarily tightened to `minAvailable: 2`. The controller reported zero allowed disruptions and the API rejected a single eviction:

```text
$ kubectl get pdb events-pdb
NAME         MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
events-pdb   2               N/A               0

HTTP/1.1 429 Too Many Requests
{
  "kind": "Status",
  "apiVersion": "v1",
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

The PDB was then restored to `minAvailable: 1`.

With three replicas and `minAvailable: 1`, at most **two** pods may be voluntarily evicted at once. For the five-replica critical-path gateway, `minAvailable: 2` permits three evictions while retaining useful capacity. A value such as four would preserve more capacity but could prevent a drain or node replacement from progressing.

## Task 2 — Graceful Shutdown and Zero-Downtime Migration

### 12.6: Gateway lifecycle and readiness

The gateway pod template contains:

```yaml
terminationGracePeriodSeconds: 40
containers:
  - name: gateway
    lifecycle:
      preStop:
        exec:
          command: ["sh", "-c", "sleep 10"]
    readinessProbe:
      httpGet:
        path: /health
        port: 8080
      periodSeconds: 2
      failureThreshold: 1
```

A live restart was triggered through `spec.restartAt` (the installed `kubectl argo rollouts` binary crashed locally, and `restartAt` is the controller-native equivalent). Once all five replacement pods were available, Prometheus showed no 5xx in the one-minute restart window:

```text
before=37
after=37
delta=0

$ kubectl get rollout gateway
NAME      DESIRED   CURRENT   UP-TO-DATE   AVAILABLE
gateway   5         5         5            5

sum(increase(gateway_requests_total{status=~"5.."}[1m])) = 0
```

### 12.7: Concurrent index migration

Migration `a12c01_index_events_event_date_concurrently.py`:

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
```

The migration completed while mixedload was running. The gateway 5xx counter remained `44` before and after the DDL itself.

```text
INFO Running upgrade 6803cfdeb137 -> a12c01, Index events.event_date concurrently.

event_date | timestamp with time zone | not null
"idx_events_event_date" btree (event_date)
```

`CREATE INDEX CONCURRENTLY` avoids blocking normal reads and writes while PostgreSQL builds an index. A regular index build on a 10M-row hot table takes a stronger lock; writes can queue for minutes, exhaust connection pools and cause an outage. The autocommit block is required because PostgreSQL rejects concurrent index creation inside a transaction block.

### 12.8: Expand-and-contract design

1. **Migration 1 — expand:** add `scheduled_at TIMESTAMPTZ NULL`. The nullable add is compatible with existing rows and old application instances.
2. **Deploy A:** read `COALESCE(scheduled_at, event_date)` and expose it under the old response shape; dual-write both columns for every runtime write. QuickTicket has no runtime event-date insert, so runtime dual-write is a no-op here.
3. **Migration 2 — migrate data:** backfill `scheduled_at = event_date WHERE scheduled_at IS NULL`, then enforce `scheduled_at NOT NULL`. The predicate makes the backfill idempotent, while Deploy A can read rows on either side of the backfill.
4. **Deploy B:** read and write only `scheduled_at`. The API response remains backward-compatible by selecting it as `event_date`; `seed.sql` is changed to insert `scheduled_at`.
5. **Migration 3 — contract:** drop `event_date` only after Deploy B is fully rolled out.

Migration 3 must be last because any remaining Deploy-A pod still references `event_date` in `COALESCE`. Dropping the column earlier would make those queries fail immediately with PostgreSQL `UndefinedColumn`, producing 5xx responses.

## Bonus Task — Execute Expand-and-Contract

### Three schema migrations

Migration 1, expand:

```python
def upgrade() -> None:
    op.add_column(
        "events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
```

Migration 2, backfill and constrain:

```python
def upgrade() -> None:
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)
```

Migration 3, contract:

```python
def upgrade() -> None:
    op.drop_column("events", "event_date")
```

All three migrations have reverse `downgrade()` operations. Migration 3 recreates and backfills `event_date`; migration 2 removes the NOT NULL constraint; migration 1 drops the expansion column.

### Deploy A and Deploy B

Deploy A used fallback reads:

```sql
SELECT ..., COALESCE(e.scheduled_at, e.event_date) AS event_date, ...
ORDER BY COALESCE(e.scheduled_at, e.event_date)
```

Deploy B, which is the committed final state, uses only the new database column while retaining the existing JSON response contract:

```sql
SELECT ..., e.scheduled_at AS event_date, ...
ORDER BY e.scheduled_at
```

`seed.sql` now creates and inserts into `scheduled_at`. There is no runtime event creation endpoint in QuickTicket, so Deploy A had no write call site to dual-write.

### Schema before and after

Before Migration 1:

```text
Column          Type                       Nullable
id              integer                    not null
name            text                       not null
venue           text                       not null
event_date      timestamp with time zone   not null
total_tickets   integer                    not null
price_cents     integer                    not null
```

Backfill verification:

```text
 id |       event_date       |      scheduled_at
----+------------------------+------------------------
  1 | 2026-09-15 09:00:00+00 | 2026-09-15 09:00:00+00
  2 | 2026-10-01 18:00:00+00 | 2026-10-01 18:00:00+00
  3 | 2026-11-20 10:00:00+00 | 2026-11-20 10:00:00+00
  4 | 2026-09-22 14:00:00+00 | 2026-09-22 14:00:00+00
  5 | 2026-10-10 10:00:00+00 | 2026-10-10 10:00:00+00
```

After Migration 3:

```text
Column          Type                       Nullable
id              integer                    not null
name            text                       not null
venue           text                       not null
total_tickets   integer                    not null
price_cents     integer                    not null
scheduled_at    timestamp with time zone   not null
```

### Live-traffic result

The DDL transitions themselves completed successfully and the final schema is correct. A strict total-counter comparison around the complete five-step sequence was **not zero**:

```text
baseline gateway 5xx total: 5
final gateway 5xx total:    11
delta:                       6
```

The errors occurred during events image replacement, not during the schema DDL; the final one-minute settled window returned `0`. This is recorded rather than presenting a false zero-delta proof. The run demonstrates an additional production concern: endpoint removal does not terminate already-established HTTP keep-alive connections from gateway pods. A complete zero-error implementation also needs connection draining/retry behavior for those pooled upstream connections (or a deployment mechanism that drains them explicitly).

### Bonus design answers

The single ordering error that is guaranteed to cause 5xx is moving **Migration 3** earlier than completion of Deploy B. It is the only forward step that removes something: Deploy A and old pods still require `event_date`.

For a 10M-row production backfill, transactions should be bounded and resumable:

```text
last_id = 0
loop:
    begin
    ids = SELECT id FROM events
          WHERE id > last_id AND scheduled_at IS NULL
          ORDER BY id LIMIT 10000 FOR UPDATE SKIP LOCKED
    if ids is empty: commit; break
    UPDATE events SET scheduled_at = event_date WHERE id IN ids
    last_id = max(ids)
    commit
    sleep briefly and record progress
```

Re-adding and backfilling `event_date` in Migration 3's downgrade is not sufficient for a true rollback once Deploy B is live: Deploy B continues writing only `scheduled_at`, so the restored old column can become stale immediately. Rollback is safe only if the schema is restored first, a compatibility deploy dual-writes both columns, data is reconciled while writes are controlled, and only then is old code rolled back. Alternatively, writes must be paused for the entire reconciliation and code rollback window.

## Task 12.9 — Horizontal Pod Autoscaler

The gateway HPA is committed as `k8s/gateway-hpa.yaml`:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: gateway
spec:
  scaleTargetRef:
    apiVersion: argoproj.io/v1alpha1
    kind: Rollout
    name: gateway
  minReplicas: 5
  maxReplicas: 12
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

The first run with 200 Locust users reached only 35%, correctly leaving the Rollout at five replicas:

```text
NAME      REFERENCE         TARGETS        MINPODS   MAXPODS   REPLICAS
gateway   Rollout/gateway   cpu: 35%/70%   5         12        5
```

The same in-cluster Locust Job was then run with `-u 1000 -r 100 -t 120s`. CPU rose to 147%, and the HPA scaled the Rollout to its configured maximum:

```text
$ kubectl get hpa gateway -o wide
NAME      REFERENCE         TARGETS         MINPODS   MAXPODS   REPLICAS
gateway   Rollout/gateway   cpu: 147%/70%   5         12        12

$ kubectl get pods -l app=gateway --no-headers | wc -l
12
```

Example per-pod CPU readings during the run ranged from 36m to 150m. All twelve replicas were placed on `k3d-quickticket-server-0`, so this demonstrates the HPA controller decision but not node-level elasticity.

