# Lab 12 — Advanced Kubernetes Resilience

All tests were run against the `k3d-quickticket` cluster while the Lab 8
`mixedload` deployment continuously exercised the QuickTicket API. Timestamps
below are UTC.

## Task 1 — Multi-Replica Failover and PodDisruptionBudgets

### 12.1 Target replica counts

The Events, Payments, and Notifications deployments were changed to two
replicas. Gateway remained an Argo Rollouts Rollout with five replicas.

```text
$ kubectl get deployments events payments notifications
NAME            READY   UP-TO-DATE   AVAILABLE
events          2/2     2            2
payments        2/2     2            2
notifications   2/2     2            2

$ kubectl get rollout gateway
NAME      DESIRED   CURRENT   UP-TO-DATE   AVAILABLE
gateway   5         5         5            5
```

### 12.2 Coordinated pod-kill failover under load

At `2026-10-09T16:05:17Z`, one Gateway pod and one Events pod were deleted at
the same time while mixedload was active.

```text
pod_kill_started_utc=2026-10-09T16:05:17Z
before_5xx_3m=0
gateway_victim=gateway-6bc7d8bdc8-7cqmr
events_victim=events-548cbf7f9b-ntd4k
replacement_ready_seconds=11
gateway_ready=5
events_ready=2
after_5xx_1m=0
pod_kill_completed_utc=2026-10-09T16:05:39Z
```

Both Services continued routing requests through their surviving endpoints.
Kubernetes recreated both pods in 11 seconds, and the observed 5xx count was
zero before and after the failure.

### 12.3 PDB configuration and live state

The four budgets are defined in `k8s/pdb.yaml`.

```text
$ kubectl get pdb
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
events-pdb          1               N/A               1
gateway-pdb         2               N/A               3
notifications-pdb   N/A             1                 1
payments-pdb        1               N/A               1
```

To prove enforcement, I temporarily tightened `events-pdb` to
`minAvailable: 2`, making its allowed disruption count zero, and sent a real
Eviction API request.

```text
pdb_test_started_utc=2026-10-09T16:04:43Z
target=events-548cbf7f9b-ntd4k
allowed_disruptions=0

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
http_status=429

restored_min_available=1
restored_allowed_disruptions=1
pdb_test_completed_utc=2026-10-09T16:04:45Z
```

This is an actual rejection by the eviction subresource, not a drain dry-run.

### 12.4 Topology spread

Live Rollout field:

```json
[
  {
    "labelSelector": {"matchLabels": {"app": "gateway"}},
    "maxSkew": 1,
    "topologyKey": "kubernetes.io/hostname",
    "whenUnsatisfiable": "ScheduleAnyway"
  }
]
```

Actual single-node placement:

```text
$ kubectl get pods -l app=gateway -o wide
NAME                       READY   STATUS    NODE
gateway-6bc7d8bdc8-2jsg7   1/1     Running   k3d-quickticket-server-0
gateway-6bc7d8bdc8-f24f9   1/1     Running   k3d-quickticket-server-0
gateway-6bc7d8bdc8-fpr56   1/1     Running   k3d-quickticket-server-0
gateway-6bc7d8bdc8-hxxgv   1/1     Running   k3d-quickticket-server-0
gateway-6bc7d8bdc8-mhv8k   1/1     Running   k3d-quickticket-server-0
```

This is expected because k3d has only one node. `ScheduleAnyway` keeps the
workload schedulable while preserving the preferred distribution policy for a
real multi-node cluster.

### Task 1 design answers

With three Gateway replicas and `minAvailable: 1`, at most two pods may be
voluntarily evicted simultaneously: one healthy pod must remain. The submitted
Gateway budget uses `minAvailable: 2` with five replicas, allowing three
evictions while retaining two serving replicas. This balances availability
with the need for a drain or autoscaler to make progress; requiring four would
make maintenance much easier to block.

With `maxSkew: 1` across three nodes, five Gateway pods would be placed
`2/2/1`. Seven pods would be placed `3/2/2`. In both cases the difference
between the most-loaded and least-loaded node is at most one.

## Task 2 (Optional) — Graceful Shutdown and Zero-Downtime Migration

### 12.6 Gateway lifecycle configuration

The following block is present in `k8s/gateway.yaml`:

```yaml
spec:
  template:
    spec:
      terminationGracePeriodSeconds: 40
      containers:
        - name: gateway
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            periodSeconds: 2
            timeoutSeconds: 2
            failureThreshold: 1
          lifecycle:
            preStop:
              exec:
                command: ["sh", "-c", "sleep 10"]
```

The readiness probe quickly removes an unhealthy endpoint. The pre-stop delay
then gives endpoint changes time to propagate before the process receives
SIGTERM, while the 40-second grace period covers that delay plus in-flight
request draining.

### Rolling restart under live traffic

```text
restart_started_utc=2026-10-09T16:05:54Z
before_restart_5xx_1m=0
rollout 'gateway' restarted
Progressing - updated replicas are still becoming available
Healthy
after_restart_5xx_3m=0
restart_completed_utc=2026-10-09T16:07:22Z
```

The five-replica rolling restart completed in approximately 88 seconds with no
observed 5xx responses.

### 12.7 Concurrent-index migration

Migration `5764af6d3802_index_events_event_date_concurrently.py` contains:

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            unique=False,
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

Execution under mixedload:

```text
migration_started_utc=2026-10-09T16:08:21Z
alembic_current_before=a5400e67ce10 (head)
before_total_5xx=0
INFO  [alembic.runtime.migration] Running upgrade a5400e67ce10 -> 5764af6d3802
real 0.75
user 0.50
sys  0.05
after_total_5xx=0
five_xx_delta=0
migration_completed_utc=2026-10-09T16:08:30Z
```

Index verification before the later contract migration removed the legacy
column and its index:

```text
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
```

`CREATE INDEX CONCURRENTLY` matters because a normal index build on a large
table blocks writes while it scans and builds the index. On a table with ten
million rows that lock may last minutes, queue requests, and create an outage.
The concurrent form performs extra phases but permits normal reads and writes.
PostgreSQL forbids it inside a transaction, which is why Alembic's
`autocommit_block()` is essential.

### 12.8 Expand-and-contract design

1. **Migration 1 — expand:** add nullable `scheduled_at TIMESTAMPTZ`. This is
   backward-compatible and avoids rewriting existing rows.
2. **Deploy A — compatibility mode:** new application pods read
   `COALESCE(scheduled_at, event_date)` and write both columns. Old and new pods
   can run together. QuickTicket has no runtime event-date insert path, so the
   dual-write part is a no-op here; only `seed.sql` inserts events.
3. **Migration 2 — backfill:** copy `event_date` into `scheduled_at` only where
   the new value is null, then set the new column `NOT NULL`. Deploy A tolerates
   rows on either side of the backfill.
4. **Deploy B — new-column mode:** read and write only `scheduled_at`. Wait for
   the Deployment rollout to finish so no Deploy-A pod remains.
5. **Migration 3 — contract:** drop `event_date` only after Deploy B is fully
   active.

Migration 3 must come last. If it runs while any Deploy-A pod still uses
`COALESCE(scheduled_at, event_date)`, PostgreSQL rejects that query because the
old column no longer exists, producing 5xx responses. Waiting for Deploy B to
fully roll out proves there are no remaining readers or writers of the old
column.

## 12.9 Optional HPA Observation

The submitted `k8s/gateway-hpa.yaml` targets the Gateway Rollout, keeps at least
five replicas, allows up to twelve, and targets 70% average CPU utilization.

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

Observed while increasing mixedload:

```text
UTC        CPU target/current   REPLICAS
16:19:42   56% / 70%            5
16:19:57   62% / 70%            5
16:20:12   66% / 70%            5
16:20:28   70% / 70%            5
16:21:25   81% / 70%            5
16:21:40   76% / 70%            6
16:21:55   79% / 70%            6
16:22:10   72% / 70%            6
```

The controller reacted after CPU exceeded the target and scaled the Rollout
from five to six replicas. As expected on single-node k3d, this demonstrates
the HPA decision but not node-level elasticity. After observation, the live
test HPA was removed and Gateway was returned to five replicas.

## Bonus Task — Live Expand-and-Contract Execution

Mixedload remained active for all five transitions. The starting cumulative
5xx counter at `2026-10-09T16:08:48Z` was zero.

### Migration 1 — add the nullable column

```python
def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
```

```text
M1_started_utc=2026-10-09T16:09:03Z
Running upgrade 5764af6d3802 -> 11faeb648614, add events.scheduled_at column
M1_total_5xx=0
scheduled_at_is_nullable=YES
M1_completed_utc=2026-10-09T16:09:10Z
```

### Deploy A — fallback read

The temporary compatibility deployment used:

```sql
SELECT ..., COALESCE(e.scheduled_at, e.event_date) AS event_date, ...
...
ORDER BY COALESCE(e.scheduled_at, e.event_date)
```

There is no API path that inserts or updates an event date, so runtime
dual-write was not applicable. The only event insertion is the bootstrap seed,
which was updated before the contract completed.

```text
deploy_a_rollout=success
events_ready=2/2
deploy_a_total_5xx=0
deploy_a_completed_utc=2026-10-09T16:16:58Z
```

### Migration 2 — backfill and enforce the invariant

```python
def upgrade() -> None:
    op.execute(
        "UPDATE events SET scheduled_at = event_date "
        "WHERE scheduled_at IS NULL"
    )
    op.alter_column(
        "events",
        "scheduled_at",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=False,
    )
```

```text
M2_started_utc=2026-10-09T16:17:23Z
Running upgrade 11faeb648614 -> bf079646f310, backfill events.scheduled_at
M2_total_5xx=0
rows_checked=5
scheduled_at_not_null_rows=5
scheduled_at_equals_event_date_rows=5
M2_completed_utc=2026-10-09T16:17:30Z
```

### Deploy B — new-column-only read

Deploy B replaced the fallback expression with:

```sql
SELECT ..., e.scheduled_at AS event_date, ...
...
ORDER BY e.scheduled_at
```

The SQL alias deliberately preserves the external JSON field name
`event_date`, so existing Gateway and client contracts do not break. The
database and seed schema now use only `scheduled_at`.

```text
deploy_b_rollout=success
events_ready=2/2
deploy_b_total_5xx=0
deploy_b_completed_utc=2026-10-09T16:18:17Z
```

Deploy A to Deploy B logical diff:

```diff
- COALESCE(e.scheduled_at, e.event_date) AS event_date
+ e.scheduled_at AS event_date
...
- ORDER BY COALESCE(e.scheduled_at, e.event_date)
+ ORDER BY e.scheduled_at
```

### Migration 3 — remove the old column

```python
def upgrade() -> None:
    op.drop_column("events", "event_date")
```

Before applying it, the running Events pods were verified to contain Deploy B
code. The migration was then applied at `2026-10-09T16:18:46Z`.

```text
Running upgrade bf079646f310 -> 74297cef1dae, drop events.event_date
final_alembic_revision=74297cef1dae
final_total_5xx=0
baseline_total_5xx=0
five_xx_delta=0
events_http_code=200
events_time_seconds=0.016907
bonus_completed_utc=2026-10-09T16:18:58Z
```

The equivalent baseline/final comparison was empty because the values were
identical:

```text
$ diff /tmp/5xx.baseline /tmp/5xx.final
# no output; exit status 0
```

### Schema before and after

Before Migration 1:

```text
Table "public.events"
Column         Type                       Nullable
id             integer                    not null
name           text                       not null
venue          text                       not null
event_date     timestamp with time zone   not null
total_tickets  integer                    not null
price_cents    integer                    not null
email          character varying(255)
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
```

After Migration 3:

```text
Table "public.events"
Column         Type                       Nullable
id             integer                    not null
name           text                       not null
venue          text                       not null
total_tickets  integer                    not null
price_cents    integer                    not null
email          character varying(255)
scheduled_at   timestamp with time zone   not null
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
```

The final API probe returned HTTP 200, and `/health` reported Events, Payments,
and Notifications as healthy.

### Bonus design answers

The dangerous reordering is moving Migration 3 earlier. It removes
`event_date`; Deploy A still names that column in its fallback expression, so
even though the new column is populated, its SQL would fail immediately and
produce 5xx responses.

For ten million rows, I would backfill in short, resumable primary-key ranges:

```text
last_id = 0
repeat:
    begin transaction
    rows = UPDATE events
           SET scheduled_at = event_date
           WHERE id > last_id AND scheduled_at IS NULL
           ORDER BY id LIMIT 10000
           RETURNING id
    commit
    if rows is empty: stop
    last_id = max(rows.id)
    sleep briefly and record progress
```

In PostgreSQL I would implement the limited batch with a CTE selecting the next
10,000 IDs, then update by joining that CTE. Each transaction stays small,
reducing lock duration, WAL bursts, and replica lag. The `IS NULL` predicate
makes retries idempotent.

Migration 3's downgrade is not sufficient for true application rollback once
Deploy B is live. It recreates and backfills `event_date` only at one instant;
Deploy B continues writing only `scheduled_at`, so subsequent writes would
diverge and rolled-back Deploy-A code could read stale or null old-column data.
A safe rollback requires either Deploy B to dual-write both columns for the
entire rollback window, a database trigger that mirrors writes, or paused
writes followed by a final synchronized backfill before old code returns. Both
schemas must remain mutually consistent until rollback is no longer possible.

## Final Verification

```text
events          2/2
payments        2/2
notifications   2/2
gateway         5/5
alembic_head    74297cef1dae
GET /events     HTTP 200
GET /health     healthy
final_5xx       0
```

Task 1, optional Task 2, the internal Bonus Task, and the optional HPA
observation were all completed using measured cluster behavior rather than
manifest-only validation.
