# Lab 12 — Advanced Kubernetes Resilience

Date: 2026-07-15  
Cluster: single-node k3d `quickticket` (`k3d-quickticket-server-0`)

## Task 1 — Multi-Replica Failover + PDBs

### 1. Target replica counts

```text
$ kubectl get deploy,rollout
NAME                            READY   UP-TO-DATE   AVAILABLE
deployment.apps/events          2/2     2            2
deployment.apps/notifications   2/2     2            2
deployment.apps/payments        2/2     2            2
deployment.apps/postgres        1/1     1            1
deployment.apps/redis           1/1     1            1

NAME                          DESIRED   CURRENT   UP-TO-DATE   AVAILABLE
rollout.argoproj.io/gateway   5         5         5            5
```

### 2. Coordinated pod-kill under mixedload

`mixedload` ran with 2/2 replicas throughout the test. One gateway pod and one
events pod were deleted together with `--wait=false`; the events Deployment
returned to 2/2 and the gateway Rollout returned `Healthy`.

```text
before, sum(increase(gateway_requests_total{status=~"5.."}[3m])):
0

kubectl delete pod gateway-74b4c4bc5b-5mwvb --wait=false
pod "gateway-74b4c4bc5b-5mwvb" deleted
kubectl delete pod events-7cc57d9d64-g2f9m --wait=false
pod "events-7cc57d9d64-g2f9m" deleted

after, sum(increase(gateway_requests_total{status=~"5.."}[1m])):
raw Prometheus result: []
normalized with `or vector(0)`: 0
```

An empty vector means no matching 5xx time series existed in that window; the
normalized value is zero. No 5xx was produced by the coordinated pod kill.

### 3. PodDisruptionBudgets

```text
$ kubectl get pdb
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
events-pdb          1               N/A               1
gateway-pdb         2               N/A               3
notifications-pdb   N/A             1                 1
payments-pdb        1               N/A               1
```

### 4. Topology spread and actual placement

```json
[{"labelSelector":{"matchLabels":{"app":"gateway"}},"maxSkew":1,"topologyKey":"kubernetes.io/hostname","whenUnsatisfiable":"ScheduleAnyway"}]
```

```text
$ kubectl get pod -l app=gateway -o wide
NAME                       READY   STATUS    NODE
gateway-74b4c4bc5b-4vssv   1/1     Running   k3d-quickticket-server-0
gateway-74b4c4bc5b-65v5h   1/1     Running   k3d-quickticket-server-0
gateway-74b4c4bc5b-jw264   1/1     Running   k3d-quickticket-server-0
gateway-74b4c4bc5b-n5gwt   1/1     Running   k3d-quickticket-server-0
gateway-74b4c4bc5b-v87cv   1/1     Running   k3d-quickticket-server-0
```

All five pods are on the only node, as expected for single-node k3d. The live
Rollout spec contains the production-ready constraint.

### 5. Real eviction API rejection

For the proof only, `events-pdb` was tightened to `minAvailable: 2`, which made
`ALLOWED DISRUPTIONS` equal to zero. A single eviction request then returned:

```json
{
  "kind": "Status",
  "apiVersion": "v1",
  "status": "Failure",
  "message": "Cannot evict pod as it would violate the pod's disruption budget.",
  "reason": "TooManyRequests",
  "details": {
    "causes": [
      {
        "reason": "DisruptionBudget",
        "message": "The disruption budget events-pdb needs 2 healthy pods and has 2 currently"
      }
    ]
  },
  "code": 429
}
```

HTTP status: `429`. The PDB was restored to `minAvailable: 1` immediately after
the test.

### 6. PDB capacity answer

With 3 gateway replicas and `minAvailable: 1`, at most 2 pods can be evicted
simultaneously: one must remain available. With 5 replicas, `minAvailable: 2`
allows 3 simultaneous voluntary disruptions while retaining roughly half of
the normal serving capacity. A stricter value such as 4 could block a drain or
node replacement for too long.

### 7. Topology placement answer

With 3 nodes, 5 gateway pods and `maxSkew: 1`, placement is `2/2/1`. With 7
pods it is `3/2/2`. In both cases the difference between the most- and
least-loaded eligible node is at most one.

## Task 2 — Graceful Shutdown + Concurrent Migration

### 1. Gateway lifecycle and readiness configuration

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
        port: http
      periodSeconds: 2
      failureThreshold: 1
```

Live-spec verification returned the same values (`40`, the 10-second preStop,
and readiness `periodSeconds: 2`, `failureThreshold: 1`).

### 2. Rolling restart under mixedload

```text
before, sum(increase(gateway_requests_total{status=~"5.."}[1m])): 0

$ kubectl argo rollouts restart gateway
rollout 'gateway' restarts in 0s
Progressing - rollout is restarting
Healthy

after, sum(increase(gateway_requests_total{status=~"5.."}[3m])): 0
```

### 3. `CREATE INDEX CONCURRENTLY` migration

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

The migration completed in `0.918 s` under live mixedload.

```text
5xx counter before: 0
5xx counter after:  0

Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
```

`CONCURRENTLY` matters because a normal index build on a large table can block
writes for the duration of the build and turn a deployment into an outage. On
a 10M-row table that can mean minutes of queued or timed-out requests.
`CREATE INDEX CONCURRENTLY` uses weaker locking so reads and writes can
continue, at the cost of a slower, more I/O-intensive build. The Alembic
`autocommit_block` is required because PostgreSQL rejects concurrent index
creation inside a transaction block.

### 4. Expand-and-contract design

1. Migration 1 — add nullable `scheduled_at TIMESTAMPTZ`. Adding it nullable
   is fast and remains compatible with old code.
2. Deploy A — write both `event_date` and `scheduled_at`; read
   `COALESCE(scheduled_at, event_date)`. In QuickTicket there is no runtime
   event INSERT path, so dual-write is a no-op and the seed is updated later.
3. Migration 2 — backfill rows with
   `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL`,
   then make `scheduled_at` NOT NULL. The fallback read tolerates both states;
   the WHERE clause makes the backfill idempotent.
4. Deploy B — write and read only `scheduled_at` (the API response field stays
   aliased to the backward-compatible `date` shape).
5. Migration 3 — drop `event_date`, but only after every Deploy-A pod is gone.

Migration 3 must come after Deploy B is fully rolled out because Deploy A and
older code still reference `event_date`. Dropping it earlier makes every such
pod fail its SQL queries with `column event_date does not exist`, producing
5xx.

## Bonus Task — Executed Expand-and-Contract Rename

### 1. Three smallest reversible migration steps

Migration 1, expand:

```python
def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
```

Migration 2, backfill + constraint:

```python
def upgrade() -> None:
    op.execute(
        "UPDATE events SET scheduled_at = event_date "
        "WHERE scheduled_at IS NULL"
    )
    op.alter_column("events", "scheduled_at", nullable=False)
```

Migration 3, contract:

```python
def upgrade() -> None:
    op.drop_column("events", "event_date")
```

Each file also has a downgrade: M1 drops the new column, M2 relaxes NOT NULL,
and M3 re-adds/backfills the old column.

### 2. Deploy A → Deploy B

Deploy A read both schemas:

```sql
SELECT ..., COALESCE(e.scheduled_at, e.event_date) AS event_date, ...
ORDER BY COALESCE(e.scheduled_at, e.event_date)
```

Both Deploy-A replicas became Ready while existing rows still had
`scheduled_at = NULL`. There is no runtime INSERT of events in this service,
so no application dual-write call site exists.

Deploy B switched to the new schema only:

```sql
SELECT ..., e.scheduled_at AS event_date, ...
ORDER BY e.scheduled_at
```

Before M3, both live pods were verified on `quickticket-events:lab12-b` with
`Ready=True`. `app/seed.sql` and the Kubernetes seed ConfigMap now use
`scheduled_at` so fresh environments match Deploy B.

### 3. Schema before and after

Before M1:

```text
event_date    | timestamp with time zone | not null
email         | character varying(255)   | nullable
idx_events_event_date btree (event_date)
```

After M3:

```text
Column          | Type                     | Nullable
scheduled_at    | timestamp with time zone | not null
email           | character varying(255)   | yes

No event_date column.
```

The backfill verification showed all five rows with
`scheduled_at = event_date` before the old column was removed.

### 4. Availability across all five transitions

```text
Transition                         5xx total
baseline                           0
M1 add nullable column             0
Deploy A fallback read             0
M2 backfill + NOT NULL             0
Deploy B new-column-only read      0
M3 drop old column                 0
final                              0

baseline == final; delta = 0
```

The one step that would have caused 5xx if moved earlier is M3, because it is
the only step that removes something. If any old or Deploy-A pod still queried
`event_date`, dropping the column would immediately break those reads.

### 5. Production-scale batching pattern

```text
while true:
    begin transaction
    ids = SELECT id FROM events
          WHERE scheduled_at IS NULL
          ORDER BY id LIMIT 10000 FOR UPDATE SKIP LOCKED
    if ids is empty: commit; break
    UPDATE events SET scheduled_at = event_date WHERE id IN ids
    commit transaction
    sleep briefly
```

Each transaction holds locks for only one small batch. Progress is restartable
because already-filled rows no longer match `scheduled_at IS NULL`.

### 6. Rollback-safety answer

Re-adding and backfilling `event_date` is not sufficient for true rollback
safety once Deploy B is live: Deploy B continues writing only
`scheduled_at`, so the restored old column can immediately become stale. A
safe rollback requires dual-write compatibility to be restored first (or a
trigger/synchronization mechanism), the old column to be fully backfilled and
kept current, and only then a rollout to old code. Every live writer and reader
must again support the overlap schema before the application rollback.

## Optional 12.9 — HPA Observation

Applied `k8s/gateway-hpa.yaml` and ran a 200-user, 20 users/s Locust Job for
120 seconds. The HPA observed CPU above target and requested a scale-up:

```text
$ kubectl get hpa gateway
NAME      REFERENCE         TARGETS         MINPODS   MAXPODS   REPLICAS
gateway   Rollout/gateway   cpu: 101%/70%   5         12        5

$ kubectl get rollout gateway
NAME      DESIRED   CURRENT   UP-TO-DATE   AVAILABLE
gateway   8         8         8            1
```

The HPA decision was 5 → 8 replicas. New pods shared the same single k3d node,
so this demonstrates controller behavior, not real node elasticity. The load
Job was removed after observation and the gateway was returned to 5/5 Healthy.

## Final Checklist

- [x] Task 1 done — multi-replica failover + 4 PDBs + topology spread + real eviction-API block
- [x] Task 2 done — preStop + zero-error rolling restart + CONCURRENTLY migration + expand-and-contract sketch
- [x] Bonus Task done — expand-and-contract executed live (3 migrations + 2 deploys, zero 5xx, `event_date` dropped)
- [x] (Optional) 12.9 HPA observation

## Acceptance Criteria

- [x] events / payments / notifications scaled to 2 replicas; manifests updated
- [x] Zero 5xx during coordinated gateway + events pod kill under mixedload
- [x] Four correct PDBs with expected allowed disruptions
- [x] Gateway topology spread present in the live Rollout spec
- [x] HTTP 429 with `reason: DisruptionBudget` captured
- [x] preStop, readiness probe, and 40-second termination grace configured
- [x] Zero 5xx during Argo Rollouts restart under mixedload
- [x] Concurrent index migration uses `autocommit_block`
- [x] Zero 5xx during index migration; index verified in `\d events`
- [x] Expand-and-contract design documented with ordering rationale
- [x] Three reversible bonus migrations executed in sequence
- [x] Deploy A and Deploy B executed between schema transitions
- [x] Seed schema updated to `scheduled_at`
- [x] Zero 5xx delta across all five live transitions
- [x] Final schema has no `event_date`; `scheduled_at` is NOT NULL
- [x] Ordering, batching, and rollback-safety prompts answered
