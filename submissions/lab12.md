# Lab 12 — Advanced Kubernetes Resilience

PR checklist:

- [x] Task 1 done — multi-replica failover + 4 PDBs + topology spread + real eviction-API block
- [x] Task 2 done — preStop + zero-error rolling restart + CONCURRENTLY migration + expand-and-contract sketch
- [x] Bonus Task done — expand-and-contract executed live (3 migrations + 2 deploys, zero 5xx, `event_date` dropped)
- [x] (Optional) 12.9 HPA observation

---

## Task 1 — Multi-Replica Failover + PDBs

### 1. Replica counts (`kubectl get deploy,rollout`)

```text
NAME                            READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/events          2/2     2            2           14d
deployment.apps/mixedload       1/1     1            1           11d
deployment.apps/notifications   2/2     2            2           8d
deployment.apps/payments        2/2     2            2           14d
deployment.apps/postgres        1/1     1            1           12d
deployment.apps/redis           1/1     1            1           14d

NAME                             DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
rollout.argoproj.io/gateway      5         5         5            5           12d
```

### 2. Pod-kill under mixedload — 5xx before / after

Before (3m window):

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1760815801.12,"0"]}]}}
```

Killed:

- `kubectl delete pod gateway-7f8c9d6b5-xk2n4 --wait=false`
- `kubectl delete pod events-6c4d8f7b9-p2q8r --wait=false`

Replacement pods were `1/1 Running` within ~5s. Service endpoints kept routing to the surviving replicas.

After (1m window):

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1760815868.40,"0"]}]}}
```

Both counts are 0.

### 3. `kubectl get pdb`

```text
NAME                 MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb           1               N/A               1                     12m
gateway-pdb          2               N/A               3                     12m
notifications-pdb    N/A             1                 1                     12m
payments-pdb         1               N/A               1                     12m
```

### 4. Topology spread (live spec + placement)

```text
[{"maxSkew":1,"topologyKey":"kubernetes.io/hostname","whenUnsatisfiable":"ScheduleAnyway","labelSelector":{"matchLabels":{"app":"gateway"}}}]
```

`kubectl get pod -l app=gateway -o wide`:

```text
NAME                       READY   STATUS    RESTARTS   AGE   IP            NODE                    NOMINATED NODE   READINESS GATES
gateway-7f8c9d6b5-2k9lm    1/1     Running   0          8m    10.42.0.18    k3d-quickticket-server-0   <none>           <none>
gateway-7f8c9d6b5-4n3qp    1/1     Running   0          8m    10.42.0.19    k3d-quickticket-server-0   <none>           <none>
gateway-7f8c9d6b5-7r1vw    1/1     Running   0          8m    10.42.0.21    k3d-quickticket-server-0   <none>           <none>
gateway-7f8c9d6b5-9t5xy    1/1     Running   0          8m    10.42.0.22    k3d-quickticket-server-0   <none>           <none>
gateway-7f8c9d6b5-xk2n4    1/1     Running   0          8m    10.42.0.23    k3d-quickticket-server-0   <none>           <none>
```

All 5 pods on the only k3d node — expected. The YAML is correct for a multi-node cluster.

### 5. PDB eviction rejection (HTTP 429)

Patched `events-pdb` to `minAvailable: 2` so `ALLOWED DISRUPTIONS = 0`, then POSTed an Eviction against one events pod via `kubectl proxy`:

```json
{
  "kind": "Status",
  "apiVersion": "v1",
  "metadata": {},
  "status": "Failure",
  "message": "Cannot evict pod as it would violate the pod's disruption budget. events-pdb needs 2 healthy pods and has 2 currently",
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

Restored `events-pdb` to `minAvailable: 1` afterwards.

### 6. Answer — PDB math

With 3 gateway replicas and `minAvailable: 1`, **2 pods** can be evicted at once (`replicas - minAvailable`).

`gateway-pdb` is `minAvailable: 2` with 5 replicas so we can lose 3 pods during a drain (enough to empty a node) while still keeping ~40% of gateway capacity on the critical path. `minAvailable: 4` would only allow one eviction at a time and would stall cluster autoscaler / node replacement.

### 7. Answer — topology spread on 3 nodes

`maxSkew: 1` means the busiest node and the quietest node may differ by at most 1 gateway pod.

- 5 pods on 3 nodes → **2 / 2 / 1**
- 7 pods on 3 nodes → **3 / 2 / 2**

Never 5/0/0 or 4/1/0 (skew 5 or 3).

---

## Task 2 — Graceful Shutdown + Zero-Downtime Migration

### preStop / readinessProbe (from `k8s/gateway.yaml`)

```yaml
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels:
              app: gateway
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

### Rolling restart 5xx (`kubectl argo rollouts restart gateway`)

Before (1m):

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1760816102.01,"0"]}]}}
```

After (3m, after `sleep 10`):

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1760816388.77,"0"]}]}}
```

Both 0. Canary analysis passed; rollout Healthy.

### Concurrent index migration

```python
def upgrade():
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )
```

`alembic upgrade head` finished in ~0.4s (`real 0m0.412s`). `diff /tmp/5xx.before /tmp/5xx.after` showed no change — both Prometheus snapshots were:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1760816501.00,"0"]}]}}
```

(`sum(gateway_requests_total{status=~"5.."})` stayed at 0.)

### `\d events` after concurrent index (before expand-and-contract)

```text
                                     Table "public.events"
    Column     |           Type           | Collation | Nullable |              Default
---------------+--------------------------+-----------+----------+-----------------------------------
 id            | integer                  |           | not null | nextval('events_id_seq'::regclass)
 name          | text                     |           | not null |
 venue         | text                     |           | not null |
 event_date    | timestamp with time zone |           | not null |
 total_tickets | integer                  |           | not null |
 price_cents   | integer                  |           | not null |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
```

### 12.8 Expand-and-contract sketch (`event_date` → `scheduled_at`)

At every intermediate point, **both the old code and the new code must work**. That means a brief overlap where the column has **both names**.

1. **Migration 1 (expand):** `ALTER TABLE events ADD COLUMN scheduled_at TIMESTAMPTZ NULL;`  
   Nullable, no default — instant, no table rewrite, existing rows stay valid.

2. **Code deploy A:** write to **both** columns (when a write path exists); read `COALESCE(scheduled_at, event_date) AS event_date` and `ORDER BY COALESCE(scheduled_at, event_date)`. Response shape stays the same so gateway/clients do not change.

3. **Migration 2 (backfill):** `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL;` then `ALTER COLUMN scheduled_at SET NOT NULL`.  
   Safe under live traffic because Deploy A already tolerates NULL via COALESCE. The `WHERE scheduled_at IS NULL` predicate is idempotent. Dual-writers from Deploy A keep new rows consistent, so the backfill cannot race into a hole.

4. **Code deploy B:** read and write **only** `scheduled_at` (still alias as `event_date` in the JSON if you want the API unchanged). Wait until **every** old pod is gone (`rollout status` complete).

5. **Migration 3 (contract):** `ALTER TABLE events DROP COLUMN event_date;`  
   Must run **after** Deploy B is fully rolled out. Any leftover Deploy A pod would 500 because `COALESCE(scheduled_at, event_date)` references a missing column.

Shape to memorize: **add → dual-write → backfill → switch read → drop old**.

### Why `CREATE INDEX CONCURRENTLY` matters

On a 10M-row table, plain `CREATE INDEX` takes an `ACCESS EXCLUSIVE` lock for minutes: every read and write on `events` blocks. `CONCURRENTLY` uses `SHARE UPDATE EXCLUSIVE`, which still allows reads and writes. Alembic must wrap it in `autocommit_block()` because Postgres rejects `CREATE INDEX CONCURRENTLY` inside a transaction.

### Why migration 3 must wait for Deploy B

If you drop `event_date` while any Deploy A replica is still serving, that replica's SQL still names `event_date` and every `/events` request 500s. Deploy B no longer references the old column, so the drop is safe only after the rollout has finished and no Deploy A pods remain.

---

## 12.9 Optional HPA

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

Gateway resources (already present from Lab 4):

```text
{"limits":{"cpu":"200m","memory":"256Mi"},"requests":{"cpu":"50m","memory":"64Mi"}}
```

Under Locust `-u 200 -r 20 -t 120s`:

```text
NAME      REFERENCE           TARGETS   MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway     83%/70%   5         12        8          6m
```

On single-node k3d the extra pods land on the same node — HPA controller decisions are visible, elasticity is not real.

---

## Bonus Task — Expand-and-Contract executed live

Events has **no runtime INSERT** of `event_date` (only `app/seed.sql` at bootstrap). Dual-write in Deploy A was therefore a **no-op**; seed was updated in Deploy B (12.14) so a fresh cluster boots on the new schema.

### 1. Migration upgrade() bodies

**M1 — add column (`b2d3e4f5a6c7`):**

```python
def upgrade():
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
```

**M2 — backfill + NOT NULL (`c3e4f5a6b7d8`):**

```python
def upgrade():
    op.execute(
        "UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL"
    )
    op.alter_column("events", "scheduled_at", nullable=False)
```

On a 10M-row table this UPDATE would be batched (see prompt 6). QuickTicket's 5 seed rows finish instantly.

**M3 — drop old column (`d4f5a6b7c8e9`):**

```python
def upgrade():
    op.drop_column("events", "event_date")
```

### 2. Deploy A vs Deploy B (events SQL)

**Deploy A (COALESCE fallback):**

```sql
SELECT e.id, e.name, e.venue, COALESCE(scheduled_at, event_date) AS event_date, ...
GROUP BY e.id ORDER BY COALESCE(scheduled_at, event_date)
```

and the same COALESCE in `GET /events/{id}`.

**Deploy B (new column only; response shape preserved via alias):**

```sql
SELECT e.id, e.name, e.venue, e.scheduled_at AS event_date, ...
GROUP BY e.id ORDER BY e.scheduled_at
```

Choice documented: keep JSON field `date` by aliasing `scheduled_at AS event_date` so gateway/clients do not change.

### 3. `\d events` before M1 and after M3

**Before M1** (index already present from 12.7):

```text
                                     Table "public.events"
    Column     |           Type           | Collation | Nullable |              Default
---------------+--------------------------+-----------+----------+-----------------------------------
 id            | integer                  |           | not null | nextval('events_id_seq'::regclass)
 name          | text                     |           | not null |
 venue         | text                     |           | not null |
 event_date    | timestamp with time zone |           | not null |
 total_tickets | integer                  |           | not null |
 price_cents   | integer                  |           | not null |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
```

**After M3:**

```text
                                     Table "public.events"
    Column     |           Type           | Collation | Nullable |              Default
---------------+--------------------------+-----------+----------+-----------------------------------
 id            | integer                  |           | not null | nextval('events_id_seq'::regclass)
 name          | text                     |           | not null |
 venue         | text                     |           | not null |
 total_tickets | integer                  |           | not null |
 price_cents   | integer                  |           | not null |
 scheduled_at  | timestamp with time zone |           | not null |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
```

`event_date` is gone; `scheduled_at` is `NOT NULL`. (The concurrent index on `event_date` dropped with the column.)

Backfill check after M2:

```text
 id |       event_date        |      scheduled_at
----+-------------------------+-------------------------
  1 | 2026-09-15 09:00:00+00  | 2026-09-15 09:00:00+00
  2 | 2026-10-01 18:00:00+00  | 2026-10-01 18:00:00+00
  3 | 2026-11-20 10:00:00+00  | 2026-11-20 10:00:00+00
  4 | 2026-09-22 14:00:00+00  | 2026-09-22 14:00:00+00
  5 | 2026-10-10 10:00:00+00  | 2026-10-10 10:00:00+00
```

### 4. 5xx baseline vs final (zero delta)

`/tmp/5xx.baseline`:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1760817000.11,"0"]}]}}
```

`/tmp/5xx.final` (after M1, Deploy A, M2, Deploy B, M3):

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1760817488.55,"0"]}]}}
```

`diff /tmp/5xx.baseline /tmp/5xx.final` — no change in the counter value (`0` → `0`). Zero 5xx across all five transitions.

### 5. Which reordered step would 5xx?

**Migration 3 (drop `event_date`) if it ran before Deploy B finished.** That is the only step that *removes* a column still referenced by live SQL. Deploy A’s `COALESCE(scheduled_at, event_date)` would fail on every `/events` request.

Reordering the others is either a no-op or a lock/data issue, not an immediate 5xx: M1 only adds a nullable column; Deploy A only adds COALESCE around a column that still exists; M2 fills NULLs that COALESCE already handles.

### 6. Batched backfill at 10M rows

```text
last_id = 0
batch   = 10000
loop:
  rows = UPDATE events
         SET scheduled_at = event_date
         WHERE scheduled_at IS NULL
           AND id > last_id
           AND id <= last_id + batch
  COMMIT
  if rows == 0 and no remaining NULLs: break
  last_id += batch
  sleep 100ms          # let replicas / autovacuum catch up
then ALTER COLUMN scheduled_at SET NOT NULL
```

Each transaction stays small; writers are not blocked for minutes by one giant UPDATE.

### 7. Why M3 downgrade is not a true prod rollback

Re-adding `event_date` and backfilling from `scheduled_at` restores the **schema**, but Deploy B pods **do not read or write `event_date`**. Rolling the DB back while Deploy B is live does not restore old-code behavior; it only recreates a column nobody uses. True rollback safety requires **rolling the application back to Deploy A first** (code that still understands both columns), *then* running the DB downgrade — and you must not have already dropped data that Deploy A needs. Once Deploy B is the only version in production and M3 has dropped `event_date`, a DB-only downgrade is not sufficient.
