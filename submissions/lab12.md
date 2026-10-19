# Lab 12 — Advanced Kubernetes Resilience

Tested on 19 October 2026 (UTC). Tasks 1 and 2 are complete. The expand-and-contract bonus was executed under live traffic. The ungraded optional HPA observation was not attempted.

Prerequisites: the existing single-node k3d cluster, in-cluster Prometheus, Lab 9 PostgreSQL PVC and Alembic revisions, and the gateway/notifications images built and imported from [Lab 11](https://github.com/inno-devops-labs/SRE-Intro/pull/671). This PR carries forward the complete Kubernetes manifests and migration history; its migrations add the concurrent index (`0003`) and execute the rename (`0004`–`0006`). The locally imported images use `imagePullPolicy: Never`.

One in-cluster mixedload client repeatedly called `/events`, reserved one ticket, and paid, then waited 0.5s. Requests used fresh connections through the gateway Service. A separate test event prevented inventory exhaustion. Client HTTP statuses and transport errors were captured alongside Prometheus; the load remained active through all experiments. ArgoCD automation was temporarily paused for the PDB override and manifest changes. During the initial configuration rollout, one client connection was refused while old gateway pods were being replaced. The experiment measurements below start after the rollout became Healthy.

## Task 1 — Replicas, failover and PDBs

### Replica counts

`kubectl get deployment,rollout` after deployment:

```text
NAME                              READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/events            2/2     2            2           28d
deployment.apps/lab12-mixedload   1/1     1            1           3m19s
deployment.apps/notifications     2/2     2            2           13m
deployment.apps/payments          2/2     2            2           28d
deployment.apps/postgres          1/1     1            1           28d
deployment.apps/redis             1/1     1            1           28d

NAME                          DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
rollout.argoproj.io/gateway   5         5         5            5           14d
```

Events, Payments and Notifications each have two replicas; gateway has five.

### Coordinated pod deletion

The initial coordinated deletion produced **one client HTTP 502 on reserve**, even though the Prometheus windows showed zero. A request served by a terminating pod can be missed between scrapes; the server query alone was not sufficient proof. I added a five-second `preStop` sleep to the Events container and repeated the deletion after its rollout settled.

At **08:35:47.650 UTC**, one gateway and one Events pod were deleted with `--wait=false`. The surviving replicas continued serving requests. Replacement pods were all Ready after **9.78s**.

| Measurement | Query window | Gateway 5xx |
|---|---|---:|
| Before pod deletion | Previous 3 minutes | 0 |
| After replacements were Ready + 15s | Previous 1 minute | 0 |

Query: `sum(increase(gateway_requests_total{status=~"5.."}[3m])) or vector(0)` (use `[1m]` for the second sample). This is the repeated test with the Events fix. Client counts during this repeat were:

```json
{
  "read": {
    "200": 47
  },
  "reserve": {
    "200": 47
  },
  "pay": {
    "200": 47
  }
}
```

Client failures: **0**. This is measured recovery in this cluster, not a guarantee that every future deletion is error-free.

### PodDisruptionBudgets

[`k8s/pdb.yaml`](../k8s/pdb.yaml):

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: gateway-pdb
spec:
  minAvailable: 2
  selector:
    matchLabels:
      app: gateway
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: events-pdb
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: events
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: payments-pdb
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: payments
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: notifications-pdb
spec:
  maxUnavailable: 1
  selector:
    matchLabels:
      app: notifications
```

`kubectl get pdb` with normal budgets:

```text
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb          1               N/A               1                     2m54s
gateway-pdb         2               N/A               3                     2m54s
notifications-pdb   N/A             1                 1                     2m54s
payments-pdb        1               N/A               1                     2m54s
```

To test enforcement, I temporarily set `events-pdb.minAvailable=2`, waited for two healthy pods and zero allowed disruptions, and sent a real `policy/v1` Eviction request to the pod eviction API. The response was **HTTP 429**:

```json
{
  "kind": "Status",
  "apiVersion": "v1",
  "metadata": {},
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

The pod was not marked for deletion. `minAvailable=1` was restored afterwards. A direct `kubectl delete pod` bypasses PDB checks, so the earlier deletion test and this eviction test prove different things. [Kubernetes disruption documentation](https://kubernetes.io/docs/concepts/workloads/pods/disruptions/) describes the scope of PDB protection.

**Budget questions:** three healthy replicas with `minAvailable: 1` allow at most two simultaneous voluntary evictions. With five gateway replicas, `minAvailable: 2` allows up to three while retaining two serving pods. It does not guarantee enough remaining capacity for arbitrary traffic or protect against complete node loss.

### Topology spread

Live `spec.template.spec.topologySpreadConstraints`:

```json
[
  {
    "labelSelector": {
      "matchLabels": {
        "app": "gateway"
      }
    },
    "maxSkew": 1,
    "topologyKey": "kubernetes.io/hostname",
    "whenUnsatisfiable": "ScheduleAnyway"
  }
]
```

Actual placement:

```text
NAME                       READY   STATUS    RESTARTS   AGE     IP            NODE                       NOMINATED NODE   READINESS GATES
gateway-56d997c5f6-7l7mq   1/1     Running   0          2m53s   10.42.0.227   k3d-quickticket-server-0   <none>           <none>
gateway-56d997c5f6-crwvn   1/1     Running   0          11s     10.42.0.231   k3d-quickticket-server-0   <none>           <none>
gateway-56d997c5f6-s4t66   1/1     Running   0          49s     10.42.0.229   k3d-quickticket-server-0   <none>           <none>
gateway-56d997c5f6-v9w9s   1/1     Running   0          7s      10.42.0.232   k3d-quickticket-server-0   <none>           <none>
gateway-56d997c5f6-wxlfk   1/1     Running   0          45s     10.42.0.230   k3d-quickticket-server-0   <none>           <none>
```

All gateway pods are on the only available node. With three eligible nodes, balanced placement would be **2/2/1 for five pods**, or **3/2/2 for seven**. `ScheduleAnyway` expresses a scheduling preference; it does not enforce these counts under all competing constraints. [Kubernetes topology-spread documentation](https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/).

## Task 2 — Graceful restart and concurrent migration

### Gateway termination

The following fields are installed in the gateway pod/container specification:

```yaml
terminationGracePeriodSeconds: 40
lifecycle:
  preStop:
    exec:
      command:
      - sh
      - -c
      - sleep 10
readinessProbe:
  httpGet:
    path: /health
    port: 8080
  periodSeconds: 2
  failureThreshold: 1
  timeoutSeconds: 3
```

The ten-second preStop delay gives endpoint removal time to propagate before SIGTERM; the remaining grace period gives in-flight work time to finish. Readiness uses `/health` as required by this task. Liveness still uses the process-level `/openapi.json`. Dependency-sensitive readiness can spread a dependency failure, so the Lab 8 caveat remains relevant.

After the configuration's canary analysis passed, I ran `kubectl argo rollouts restart gateway`. I checked that **all five old pod UIDs were replaced with Ready pods**, rather than trusting an initially Healthy rollout status. A restart replaces pods of the same ReplicaSet; this is distinct from the earlier configuration-triggered canary.

| Phase | UTC | Gateway 5xx |
|---|---|---:|
| Before restart, `[1m]` | 08:32:48.635 | 0 |
| All five replacements Ready | 08:33:16.172 | — |
| After 10s, `[3m]` | 08:33:26.183 | 0 |

Measured replacement time: **27.54s**.

### CREATE INDEX CONCURRENTLY

[`0003_index_events_event_date.py`](../migrations/versions/0003_index_events_event_date.py):

```python
"""Index event dates without blocking normal reads and writes."""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.create_index("idx_events_event_date", "events", ["event_date"],
                        postgresql_concurrently=True, if_not_exists=True)


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index("idx_events_event_date", table_name="events",
                      postgresql_concurrently=True, if_exists=True)
```

The autocommit block puts concurrent index DDL outside Alembic's usual transaction. `alembic upgrade head` took **0.352s**, measured around the Alembic process with a monotonic timer.

| Measurement | Before | After |
|---|---:|---:|
| Gateway total 5xx counter | 0 | 0 |
| Gateway 5xx in previous minute | 0 | 0 |
| Orders under ongoing load | 473 | 493 |

Schema excerpt after migration (`\d events`):

```text
Table "public.events"
    Column     |           Type           | Collation | Nullable |              Default
---------------+--------------------------+-----------+----------+------------------------------------
 id            | integer                  |           | not null | nextval('events_id_seq'::regclass)
 name          | text                     |           | not null |
 venue         | text                     |           | not null |
 event_date    | timestamp with time zone |           | not null |
 total_tickets | integer                  |           | not null |
 price_cents   | integer                  |           | not null |
 email         | character varying(255)   |           |          |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
Referenced by:
    TABLE "orders" CONSTRAINT "orders_event_id_fkey" FOREIGN KEY (event_id) REFERENCES events(id)
```

`pg_index` check (`index name | indisvalid | indisready`):

```text
idx_events_event_date|t|t
```

I also ran `alembic downgrade 0002`, verified the index was absent, and upgraded again. At the end of Task 2, the revision was **0003**, the index is valid, and the one-minute 5xx increase remained **0**. History:

```text
0002 -> 0003 (head), Index event dates without blocking normal reads and writes.
0001 -> 0002, Add an optional contact email to events.
<base> -> 0001, Baseline for the existing app/seed.sql schema; no DDL is executed.
```

The table contains only six rows during testing, so this is a syntax/reversibility and live-traffic check, not a large-table benchmark. Standard PostgreSQL index creation blocks writes, not ordinary reads; `CONCURRENTLY` avoids blocking normal reads and writes while building. An interrupted concurrent build can leave an invalid index: inspect validity before retrying, because `IF NOT EXISTS` alone would not repair it. See [PostgreSQL CREATE INDEX](https://www.postgresql.org/docs/17/sql-createindex.html).

### Expand-and-contract rename — design only

Three migrations and two application deployments, in order:

1. **Migration 1 — expand:** `ALTER TABLE events ADD COLUMN scheduled_at timestamptz;`. Keep `event_date` so the old application still works.
2. **Code A — compatibility:** write both fields in the same transaction; read `COALESCE(scheduled_at, event_date)` and preserve the existing API response shape. Update every writer, including seed/import scripts. Wait for all old-only writers to be replaced before backfill. QuickTicket currently has no runtime event-date write endpoint, so this concerns SQL seed/admin writers.
3. **Migration 2 — backfill:** `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL;`. The predicate makes repeated batches safe and avoids overwriting values already written by Code A. For a large table, use bounded primary-key batches and short transactions. Validate that the new values are complete. Since the existing `event_date` is NOT NULL, drop that old constraint in this phase before Code B starts inserting only `scheduled_at`; otherwise its inserts would fail. Enforce the new field's required-value constraint once validation permits it.
4. **Code B — switch:** read and write only `scheduled_at`, keeping the public `date` response unchanged. During this rollout, Code A remains compatible because it reads the new value first. Wait for every Code A pod and all old-column readers to disappear.
5. **Migration 3 — contract:** drop `event_date` only after Code B is fully deployed and old-column usage is absent. Recreate/move any needed date index onto `scheduled_at` before dropping the old column; the existing date index depends on it.

Rollback remains straightforward while both fields are maintained. Once Code B writes only the new field, reverting to an old-only application needs data reconciliation first. After dropping the old column, restore compatibility before any application rollback. The rename was not executed on the database.

### End-to-end traffic evidence

During the rolling restart, index upgrade, downgrade and re-upgrade (the corrected pod-deletion test is recorded separately above):

```json
{
  "read": {
    "200": 112
  },
  "reserve": {
    "200": 112
  },
  "pay": {
    "200": 112
  }
}
```

Transport errors: **0**. Client status recording complements Prometheus because failed connections may never reach the gateway counter.

## Bonus — live expand-and-contract rename

A fresh backup was taken before the bonus. The gateway was first updated to the Lab 11 bulkhead image. Its canary analysis initially had no `/events` traffic and returned an empty result; after adding a read-only load client and retrying, the normal analysis passed. The migration measurement starts after the gateway is Healthy, with all five replicas on the new image.

Throughout the five transitions, a dedicated mixedload client used the gateway Service for reads, reservations and payments every 0.5 seconds. A separate synthetic event avoided stock exhaustion. No Events runtime endpoint creates or updates event dates, so there is no runtime dual-write path to change. The final boot-time seed uses the new column.

### Three migrations

Each migration was applied individually, with the compatible Events deployment between steps. Upgrade bodies:

`0004_add_events_scheduled_at.py`:

```python
def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column("events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))
```

`0005_backfill_events_scheduled_at.py`:

```python
def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)
    op.alter_column("events", "event_date", nullable=True)
```

`0006_drop_events_event_date.py`:

```python
def upgrade():
    # Keep the date index when its old column is removed.
    with op.get_context().autocommit_block():
        op.create_index("idx_events_scheduled_at", "events", ["scheduled_at"],
                        postgresql_concurrently=True, if_not_exists=True)
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column("events", "event_date")
```

Migration 0005 also removes the old column's NOT NULL constraint so new-only inserts can work before contraction. Migration 0006 builds a replacement date index concurrently before dropping the old column (which automatically removes its dependent index). Schema locks have a five-second acquisition timeout.

### Deploy A → Deploy B

Deploy A used `COALESCE(e.scheduled_at, e.event_date) AS event_date` in both SELECTs and `ORDER BY COALESCE(e.scheduled_at, e.event_date)`. It was built/imported as `quickticket-events:lab12-a`, deployed after 0004, and fully Ready before backfill. Deploy B was built/imported as `quickticket-events:lab12-b`, deployed after 0005, and fully Ready before 0006. The final manifest and committed source use B. The response field remains `date`; IDs and date values were checked against the baseline after every transition.

The A-to-B diff:

```diff
--- Deploy A
+++ Deploy B
@@ -143,6 +143,6 @@
         cur.execute("""
-            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date, e.total_tickets, e.price_cents,
+            SELECT e.id, e.name, e.venue, e.scheduled_at AS event_date, e.total_tickets, e.price_cents,
                    COALESCE(SUM(o.quantity), 0) as confirmed
             FROM events e LEFT JOIN orders o ON e.id = o.event_id
-            GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
+            GROUP BY e.id ORDER BY e.scheduled_at
         """)
@@ -168,3 +168,3 @@
         cur.execute("""
-            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date, e.total_tickets, e.price_cents,
+            SELECT e.id, e.name, e.venue, e.scheduled_at AS event_date, e.total_tickets, e.price_cents,
                    COALESCE(SUM(o.quantity), 0) as confirmed
```

### Schema evidence

Before migration 0004:

```text
                                        Table "public.events"
    Column     |           Type           | Collation | Nullable |              Default
---------------+--------------------------+-----------+----------+------------------------------------
 id            | integer                  |           | not null | nextval('events_id_seq'::regclass)
 name          | text                     |           | not null |
 venue         | text                     |           | not null |
 event_date    | timestamp with time zone |           | not null |
 total_tickets | integer                  |           | not null |
 price_cents   | integer                  |           | not null |
 email         | character varying(255)   |           |          |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
Referenced by:
    TABLE "orders" CONSTRAINT "orders_event_id_fkey" FOREIGN KEY (event_id) REFERENCES events(id)

```

After migration 0006:

```text
                                        Table "public.events"
    Column     |           Type           | Collation | Nullable |              Default
---------------+--------------------------+-----------+----------+------------------------------------
 id            | integer                  |           | not null | nextval('events_id_seq'::regclass)
 name          | text                     |           | not null |
 venue         | text                     |           | not null |
 total_tickets | integer                  |           | not null |
 price_cents   | integer                  |           | not null |
 email         | character varying(255)   |           |          |
 scheduled_at  | timestamp with time zone |           | not null |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_scheduled_at" btree (scheduled_at)
Referenced by:
    TABLE "orders" CONSTRAINT "orders_event_id_fkey" FOREIGN KEY (event_id) REFERENCES events(id)

```

Backfill check (all dates equal and non-NULL):

```text
 id |          event_date           |         scheduled_at
----+-------------------------------+-------------------------------
  1 | 2026-09-15 09:00:00+00        | 2026-09-15 09:00:00+00
  2 | 2026-10-01 18:00:00+00        | 2026-10-01 18:00:00+00
  3 | 2026-11-20 10:00:00+00        | 2026-11-20 10:00:00+00
  4 | 2026-09-22 14:00:00+00        | 2026-09-22 14:00:00+00
  5 | 2026-10-10 10:00:00+00        | 2026-10-10 10:00:00+00
 10 | 2026-11-19 08:56:10.595239+00 | 2026-11-19 08:56:10.595239+00
(6 rows)

```

### Live results

| Transition complete (UTC) | Step | Gateway 5xx counter |
|---|---|---:|
| 08:56:37 | Baseline | 0 |
| 08:56:46 | M1: add scheduled_at | 0 |
| 08:57:10 | Deploy A: fallback reads | 0 |
| 08:57:19 | M2: backfill + NOT NULL | 0 |
| 08:57:42 | Deploy B: new column only | 0 |
| 08:57:51 | M3: drop event_date | 0 |

Prometheus query: `sum(gateway_requests_total{status=~"5..",pod=~"gateway-.*"}) or vector(0)`. The selector excludes the earlier temporary Lab 11 comparison pods. No gateway pods restarted during these five transitions.

```text
5xx.baseline: 0
5xx.final:    0
diff 5xx.baseline 5xx.final: no output (exit 0)
```

Client evidence: **187 complete checkout cycles / 561 HTTP requests, all HTTP 200, zero transport errors** between 08:56:11 and 08:57:52 UTC. The 5xx delta remained zero after each transition, not only at the end.

### Design answers

**Which step cannot run earlier?** Dropping `event_date` before every pod runs Deploy B would break old reads, including Deploy A's COALESCE: PostgreSQL still resolves both referenced columns. Deploy A also requires migration 0004 first; Deploy B requires completed backfill. The destructive contraction must be last.

**Production batching:** the lab has only six rows during testing. On a large table, backfill small primary-key ranges and commit between batches; keep all writers dual-writing until backfill and validation finish.

```text
last_id = 0
while True:
    ids = SELECT id FROM events WHERE id > last_id ORDER BY id LIMIT 10000
    if not ids: break
    BEGIN
    UPDATE events SET scheduled_at = event_date
      WHERE id > last_id AND id <= max(ids) AND scheduled_at IS NULL
    COMMIT
    last_id = max(ids)
    sleep(0.1)
```

Afterwards verify no NULLs and enforce the constraint separately. Keep transactions short, monitor latency/replication lag, and pause or reduce the batch size if needed.

**Why is the downgrade alone insufficient?** Recreating `event_date` and copying once only produces a snapshot. A running Deploy B writer could insert new-only rows or update `scheduled_at` afterwards, leaving the old value NULL or stale. Stop date writes or restore atomic dual-writing, reconcile the two columns, verify all values, and only then roll back readers and remove the new column. Revision 0006's downgrade restores the nullable old column required by 0005; 0005's downgrade reconciles its values and restores the old NOT NULL constraint. Neither operation alone coordinates application writers.

### Fresh setup and rollback validation

`app/seed.sql` now creates `scheduled_at`, the nullable email column inherited from Lab 9, and `idx_events_scheduled_at`. On a fresh empty database, run the final seed and stamp revision 0006; do not replay the old-column migration chain against the final schema. Existing installations follow the five-step order in `migrations/README`.

An isolated temporary database passed upgrade 0001 → 0006, downgrade → 0003, and re-upgrade → 0006 with all five seeded event dates preserved. A separate fresh-seed run verified five dates, the email column, a valid new date index, and revision 0006. The temporary database was removed. The live database was not rolled back during the traffic experiment.

## Final state

Synthetic load and its event/orders were removed. Five seed events and zero orders remain; Alembic is at `0006`, `event_date` is absent, and `idx_events_scheduled_at` is valid. Gateway has five replicas and the three downstream application services have two each. All PDBs are restored to their committed values. Data/backup PVCs and the backup CronJob remain enabled. ArgoCD manages `feature/lab12` with self-healing restored. Kubernetes server-side manifest validation and Python migration syntax checks passed.
