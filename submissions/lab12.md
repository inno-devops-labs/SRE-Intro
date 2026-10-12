# Lab 12 — Advanced Kubernetes Resilience

**Branch:** `feature/lab12` (from upstream `main`). The first commit promotes the files this lab builds on: `k8s/{events,payments}.yaml` (fork `main`, Lab 5), `k8s/{gateway,notifications}.yaml` (Lab 11), and `alembic.ini` + `migrations/` (Lab 9). `kubectl diff` against the live objects was empty for gateway/notifications. For events/payments it differed only by the ArgoCD `tracking-id` annotation.
**Cluster:** single-node k3d, gateway = Argo Rollout ×5 with the Lab 7 canary + AnalysisRun. mixedload ran during every test. Alembic via `kubectl port-forward svc/postgres 15432:5432` (host 5432 belongs to the docker-compose Postgres, Lab 9).
**Raw output:** `raw.log`, section `=== LAB 12 ===`. All times UTC, 2026-10-12.

- [x] Task 1 — multi-replica failover + 4 PDBs + topology spread + real eviction-API block
- [x] Task 2 — preStop + zero-error rolling restart + CONCURRENTLY migration + expand-and-contract sketch
- [x] Bonus Task — expand-and-contract executed live (3 migrations + 2 deploys, `event_date` dropped). **2 × 5xx, not 0**: see B.4
- [x] (Optional) 12.9 HPA observation

## 12.0 Things the lab text does not mention

| # | What happened | What I did |
|---|---|---|
| 1 | **Prometheus cannot see the failures this lab is about.** A connection refused while a pod shuts down never reaches a gateway, so `gateway_requests_total{status=~"5.."}` stays 0. | Every kill/restart test also ran a **client-side prober**: an in-cluster pod doing `GET /events` every ~0.12 s with `--max-time 2`, logging every non-200 with a timestamp. |
| 2 | The spec's "expect 0" queries count **all** 5xx, including the gateway's own `/health` 503 noise from Lab 8 (5.17 per 3 min at the start of this lab, 0 on user paths). | Showed both "all" and `path!="/health"`. The noise disappeared once events had 2 replicas (12.2). That is a correlation, not a proven cause. |
| 3 | events and payments are ArgoCD-managed. `kubectl apply -f k8s/events.yaml` would strip ArgoCD's `tracking-id` annotation. | Scaled them with `kubectl scale` and later patched events with `kubectl patch`. The manifests in git were updated to match the live state (`kubectl diff` = only that annotation). |
| 4 | The spec's bonus uses `kubectl rollout restart deployment/events` after importing `quickticket-events:v1`. The live events Deployment runs `ghcr.io/…:2fed1f9` with `imagePullPolicy: Always`, so a restart would just pull the old image again. | Unique local tags `quickticket-events:lab12-a` / `lab12-b` + `imagePullPolicy: IfNotPresent` via `kubectl patch`. Each patch is a normal rolling update (2 replicas, maxSurge 1, maxUnavailable 0). |
| 5 | Dropping `event_date` (migration 3) silently drops the `idx_events_event_date` index created in Task 2. | Migration 1 also creates `idx_events_scheduled_at` **concurrently**. Migration 3's downgrade recreates the old index. |
| 6 | Event 1 was sold out again (held-counter leak, Lab 8), so mixedload would not produce `/pay` traffic. | `UPDATE events SET total_tickets=100000000 WHERE id=1` for the lab (restored to 100 in cleanup). |

---

## Task 1 — Multi-Replica Failover + PDBs

### 1. Replica counts (12.1)

`k8s/{events,payments,notifications}.yaml`: `replicas: 1 → 2`. Notifications: `kubectl apply`. events/payments: `kubectl scale deployment events payments --replicas=2` (12.0 #3).

```text
$ kubectl get deploy,rollout                                  # 01:54:40
NAME                            READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/events          2/2     2            2           14d
deployment.apps/mixedload       2/2     2            2           95s
deployment.apps/notifications   2/2     2            2           31m
deployment.apps/payments        2/2     2            2           14d
deployment.apps/postgres        1/1     1            1           14d
deployment.apps/redis           1/1     1            1           14d

NAME                          DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
rollout.argoproj.io/gateway   5         5         5            5           6d13h
```

### 2. Failover under load (12.2)

```text
BEFORE  sum(increase(5xx[3m])) = 5.133 (all /health 503)   user paths = 0
$ kubectl delete pod gateway-fcbb87fdf-4nm8r --wait=false ; kubectl delete pod events-76bc7c449f-cgqcz --wait=false   # 01:54:51
01:54:51 cgqcz Terminating, 4nm8r Terminating
01:54:52 events vhnxr ContainerCreating, gateway b6qsl 0/1 Running
01:54:54 events vhnxr 1/1                     ← events replacement Ready in 3 s
01:54:57 gateway b6qsl 1/1                    ← gateway replacement Ready in 6 s
PROBER (client side, 60 s across the kill): ok=426 fail=0
AFTER   sum(increase(5xx[1m])) = 0   [3m] = 0   user paths [3m] = 0
```

### 3. PDBs (12.3)

```yaml
# PodDisruptionBudgets: how many pods of each service a *voluntary* disruption
# (node drain, eviction API, cluster upgrade) may take down at once.
# They do not protect against crashes or node failure; replicas do that.
#
# Apply with: kubectl apply -f k8s/pdb.yaml
---
# gateway: 5 replicas on the critical path. 2 must stay up, so a drain may take
# 3 at once. minAvailable: 4 would let a drain proceed only one pod at a time and
# block it entirely whenever a pod is already unhealthy.
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
# events: 2 replicas, every read and reserve goes through it. Keep 1.
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
# payments: 2 replicas, checkout depends on it. Keep 1.
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
# notifications: best-effort (fire-and-forget from the gateway, Lab 11).
# maxUnavailable scales with the replica count instead of pinning a minimum.
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

```text
$ kubectl get pdb
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb          1               N/A               1                     3s
gateway-pdb         2               N/A               3                     3s
notifications-pdb   N/A             1                 1                     3s
payments-pdb        1               N/A               1                     3s
```

### 4. Topology spread (12.4)

```yaml
      # Room for the 10 s preStop sleep plus up to 30 s of in-flight drain after SIGTERM.
      terminationGracePeriodSeconds: 40
      # Spread gateway pods across nodes: the per-node pod count may differ by at
      # most 1 (5 pods on 3 nodes → 2/2/1). ScheduleAnyway = preference, so a
      # single-node cluster (k3d here) still schedules all pods instead of Pending.
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels:
              app: gateway
```

(The `terminationGracePeriodSeconds` line above was added in 12.6. Topology spread went out on its own canary at 01:56:49 → `Healthy` 01:59:25, AnalysisRun `gateway-f9df776b8-14-2` Successful.)

```text
$ kubectl get rollout gateway -o jsonpath='{.spec.template.spec.topologySpreadConstraints}' | python3 -m json.tool
[
    {
        "labelSelector": {"matchLabels": {"app": "gateway"}},
        "maxSkew": 1,
        "topologyKey": "kubernetes.io/hostname",
        "whenUnsatisfiable": "ScheduleAnyway"
    }
]
$ kubectl get pod -l app=gateway -o wide
NAME                      READY   STATUS    RESTARTS   AGE     IP            NODE
gateway-f9df776b8-7mfms   1/1     Running   0          34s     10.42.0.198   k3d-quickticket-server-0
gateway-f9df776b8-7pr4c   1/1     Running   0          12s     10.42.0.199   k3d-quickticket-server-0
gateway-f9df776b8-8sk24   1/1     Running   0          12s     10.42.0.200   k3d-quickticket-server-0
gateway-f9df776b8-9dznx   1/1     Running   0          2m37s   10.42.0.196   k3d-quickticket-server-0
gateway-f9df776b8-xdnsh   1/1     Running   0          34s     10.42.0.197   k3d-quickticket-server-0
$ kubectl get nodes → k3d-quickticket-server-0 (the only node)
```

### 5. PDB blocks eviction (12.5)

Via `kubectl proxy --port=8901` (run under `timeout`, killed by PID trap) and `POST /api/v1/namespaces/default/pods/<pod>/eviction`.

**(a) Tightened PDB** (`events-pdb minAvailable: 2`, ALLOWED DISRUPTIONS 0), 01:59:51:

```json
HTTP 429
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

Both events pods stayed `Running`. Restored to `minAvailable: 1`.

**(b) The normal PDB, doing its real job:** two evictions back to back, as a node drain would issue them:

```text
01:59:54  evict events-76bc7c449f-vhnxr -> HTTP 201  Status Success
01:59:54  evict events-76bc7c449f-zpc6w -> HTTP 429  Cannot evict pod as it would violate the pod's disruption budget.
          events-pdb  ALLOWED DISRUPTIONS 0
01:59:54  fksmp ContainerCreating, vhnxr Completed, zpc6w Running
01:59:59  fksmp 1/1 Running             → events-pdb ALLOWED DISRUPTIONS back to 1
```

The first eviction is allowed (1 of 2 may go). The second, 0 s later, is refused until the replacement is Ready, which is exactly how a drain is throttled.

### 6. 3 replicas with `minAvailable: 1`, and why `minAvailable: 2` at 5 replicas

With 3 replicas and `minAvailable: 1`, at most **2** pods can be evicted at the same time (3 − 1). `gateway-pdb` is `minAvailable: 2` at 5 replicas, so a drain may take **3** at once (ALLOWED DISRUPTIONS 3 above):

- 2 gateway pods keep the site up: in Lab 10 two pods' worth of capacity (~16–36m CPU each at ~28 RPS for all 5) is plenty, the bottleneck is Postgres.
- A tighter value (`minAvailable: 4`) would let a drain move only one pod at a time. If any gateway pod is already NotReady (e.g. a canary that failed analysis, or a CrashLoop from Lab 8), the budget is already exhausted and **every** node drain blocks indefinitely.

The PDB should protect availability without making maintenance impossible.

### 7. `maxSkew: 1` on 3 nodes

5 pods → **2 / 2 / 1** (any skew > 1, like 3/1/1, is avoided). 7 pods → **3 / 2 / 2**. With `ScheduleAnyway` this is a preference that the scheduler scores, not a hard rule. With `DoNotSchedule` an extra pod would stay Pending rather than create a skew of 2. On this single-node cluster every placement is 5/0/0 by necessity.

---

## Task 2 — Graceful Shutdown + Zero-Downtime Migration

### preStop / readinessProbe (12.6), as in `k8s/gateway.yaml`

```yaml
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            periodSeconds: 2
            failureThreshold: 1
          lifecycle:
            # Keep serving for 10 s after the pod is marked Terminating. The
            # endpoints controller removes a terminating pod from the Service at
            # once, but kube-proxy needs a moment to update every node's rules;
            # SIGTERM reaches uvicorn only after that, so no new connection is
            # sent to a socket that is already closed.
            preStop:
              exec:
                command: ["sh", "-c", "sleep 10"]
```

plus `terminationGracePeriodSeconds: 40` (shown in 12.4). Applied 02:03:43 → canary → AnalysisRun `gateway-79c6744678-15-2` Successful → `Healthy` 02:06:12.

### 5xx around `kubectl argo rollouts restart gateway`. Same test without and with preStop

| | Without preStop (Lab 11 spec, 02:00:41) | With preStop + readiness (02:07:01) |
|---|---|---|
| restart duration (`argo rollouts status`) | 16 s | **55 s** (10 s preStop per pod) |
| Prometheus `sum(increase(5xx[1m]))` before | 0 (no series) | 0 (no series) |
| Prometheus `sum(increase(5xx[3m]))` after | **0** (no series) | **0** (no series) |
| **client-side prober** (`GET /events` ~8 rps, 150 s) | **ok=1058 fail=6**: 5 × `code=000 curl_rc=7` (connection refused) at 02:00:45, :48, :52, :54, :55 + 1 × `429` | **ok=1076 fail=0** |

**Prometheus said "0 errors" for both runs.** Only the client saw the 5 refused connections without preStop. That is the whole argument for preStop, and also for measuring at the client (12.0 #1).

Why it works: when a pod gets deletion, the endpoints controller removes it from the Service right away, regardless of readiness. kube-proxy on each node then needs a moment to reprogram. The preStop sleep keeps uvicorn serving through that window, and SIGTERM arrives only after it. The faster readinessProbe (2 s × 1) matters for a pod whose `/health` actually fails, not for the shutdown path. The trade-off: with the gateway's deep `/health`, one failed probe now removes a pod. That would make the Lab 8 cascades react even faster.

### Migration with `CREATE INDEX CONCURRENTLY` (12.7)

```python
def upgrade() -> None:
    """Upgrade schema."""
    # CONCURRENTLY builds the index under SHARE UPDATE EXCLUSIVE instead of the
    # SHARE lock of a plain CREATE INDEX, so reads and writes keep flowing.
    # Postgres refuses it inside a transaction block, and Alembic runs every
    # migration in one, so step out with autocommit_block().
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
```

```text
$ sum(gateway_requests_total{status=~"5.."}) > 5xx.before      # 02:10:08 → {"result":[]}
$ time alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade 5fcf405ffc77 -> 66ae269dd593, index events.event_date concurrently
timeout 60 .venv/bin/alembic upgrade head  0.14s user 0.03s system 63% cpu 0.275 total
$ psql \d events | grep idx_events
    "idx_events_event_date" btree (event_date)
$ SELECT indexrelid::regclass, indisvalid, indisready … → idx_events_event_date|t|t
$ sum(gateway_requests_total{status=~"5.."}) > 5xx.after        # +5 s → {"result":[]}
diff: before None, after None (no 5xx series on any gateway pod) ; user-path 5xx [1m] = 0
$ alembic history
5fcf405ffc77 -> 66ae269dd593 (head), index events.event_date concurrently
```

`indisvalid = t` matters: a failed `CREATE INDEX CONCURRENTLY` leaves an INVALID index behind, which the planner ignores but writes still maintain. That is why `if_not_exists` alone is not a full re-run strategy. An invalid index has to be dropped first.

### Expand-and-contract sketch for `event_date` → `scheduled_at` (12.8)

1. **Migration 1 (expand):** `ALTER TABLE events ADD COLUMN scheduled_at timestamptz NULL` (+ `CREATE INDEX CONCURRENTLY idx_events_scheduled_at`). Old code does not know the column and keeps working.
2. **Deploy A (dual-write, fallback-read):** reads `COALESCE(scheduled_at, event_date)` and writes both columns. Works whether or not a row is backfilled, and keeps the response shape.
3. **Migration 2 (backfill):** `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL` (batched on big tables), then `SET NOT NULL`. Safe with live traffic because Deploy A reads either column, and the `WHERE … IS NULL` makes it idempotent.
4. **Deploy B (switch):** reads and writes **only** `scheduled_at`. Every pod must be on B before step 5.
5. **Migration 3 (contract):** `ALTER TABLE events DROP COLUMN event_date`. Nothing references it any more.

At every point between two steps, both the code before and the code after the step work on the schema at that moment. Each step is reversible on its own until step 5.

### Why does `CREATE INDEX CONCURRENTLY` matter? What if you omit it on 10M rows?

A plain `CREATE INDEX` takes a **SHARE** lock on the table for the whole build: reads continue, but every `INSERT/UPDATE/DELETE` blocks. On 10M rows that is minutes of blocked writes, and blocked writers queue up and exhaust connection pools. Lab 10 showed how fast that cascades in this system. `CONCURRENTLY` takes **SHARE UPDATE EXCLUSIVE**, which does not block reads or writes. It scans the table twice and waits for older transactions, so it is slower, cannot run in a transaction (hence `autocommit_block()`), and can leave an INVALID index if it fails. At 5 rows both take < 1 s (0.275 s here). The difference only shows at scale, which is exactly when you can no longer afford to learn it.

### Why must migration 3 come after Deploy B is fully rolled out?

Because until then some pod still runs code that names `event_date`. If migration 3 ran while a Deploy-A pod (or an old pod) was still serving, every `/events` and `/events/{id}` on that pod would fail with `column e.event_date does not exist` → 500. With 2 replicas that is half of all reads. A rolling update keeps old pods serving until the new ones are Ready, so "the deploy started" is not enough. It has to be **finished** (`kubectl rollout status`, no old ReplicaSet pods left). I saw the mirror-image failure in the bonus: Deploy A's query run before migration 1 fails with `ERROR: column e.scheduled_at does not exist` (postgres log, 02:11:21).

---

## Bonus — Expand-and-contract executed live

### B.1 Migrations (upgrade bodies)

```python
# fb08cf47a9dd_add_events_scheduled_at_column.py
def upgrade() -> None:
    """Upgrade schema."""
    # Expand: new nullable column. Nullable + no default = metadata-only, instant
    # even on a big table; the old code never sees it.
    op.add_column(
        "events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    # Index it now, concurrently: migration 3 drops event_date and with it
    # idx_events_event_date (Task 2), so the replacement must exist before then.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_scheduled_at",
            "events",
            ["scheduled_at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
```

```python
# 41d677444ecf_backfill_events_scheduled_at.py
def upgrade() -> None:
    """Upgrade schema."""
    # Safe under live traffic: Deploy A reads COALESCE(scheduled_at, event_date),
    # so rows are correct before and after this UPDATE, and WHERE ... IS NULL makes
    # it idempotent. On a big table this would be batched (see submission).
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)
```

```python
# ec4613a31045_drop_events_event_date.py
def upgrade() -> None:
    """Upgrade schema."""
    # Contract: only after Deploy B (no reads or writes of event_date) is fully
    # rolled out. Also drops idx_events_event_date; idx_events_scheduled_at
    # (migration 1) replaces it.
    op.drop_column("events", "event_date")
```

Migration 3's downgrade:

```python
def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "events", sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column("events", "event_date", nullable=False)
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
```

### B.2 `app/events/main.py`: Deploy A → Deploy B

Deploy A (relative to upstream) changes the two SELECTs to `COALESCE(e.scheduled_at, e.event_date) AS event_date` and the `ORDER BY` to the same expression. **There is no runtime write path for the date:** events are only inserted by `app/seed.sql`, so the dual-write part of Deploy A is a no-op in code and the seed is updated with Deploy B. Diff A → B:

```diff
@@ -142,11 +142,11 @@
         cur = conn.cursor()
         cur.execute("""
             SELECT e.id, e.name, e.venue,
-                   COALESCE(e.scheduled_at, e.event_date) AS event_date,
+                   e.scheduled_at AS event_date,
                    e.total_tickets, e.price_cents,
                    COALESCE(SUM(o.quantity), 0) as confirmed
             FROM events e LEFT JOIN orders o ON e.id = o.event_id
-            GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
+            GROUP BY e.id ORDER BY e.scheduled_at
         """)
         rows = cur.fetchall()
         cur.close()
@@ -169,7 +169,7 @@
         cur = conn.cursor()
         cur.execute("""
             SELECT e.id, e.name, e.venue,
-                   COALESCE(e.scheduled_at, e.event_date) AS event_date,
+                   e.scheduled_at AS event_date,
                    e.total_tickets, e.price_cents,
                    COALESCE(SUM(o.quantity), 0) as confirmed
             FROM events e LEFT JOIN orders o ON e.id = o.event_id
```

**Choice:** Deploy B keeps the alias `AS event_date`, so the JSON field stays `"date"` and neither the gateway nor any client changes. A rename in the response would be its own expand-and-contract on the API. `app/seed.sql` now creates and inserts `scheduled_at`, so a fresh cluster boots on the new schema.

### B.3 `\d events` before migration 1 and after migration 3

```text
BEFORE (02:10:39)                                         AFTER (02:14:34)
 id            | integer                  | not null      id            | integer                  | not null
 name          | text                     | not null      name          | text                     | not null
 venue         | text                     | not null      venue         | text                     | not null
 event_date    | timestamp with time zone | not null      total_tickets | integer                  | not null
 total_tickets | integer                  | not null      price_cents   | integer                  | not null
 price_cents   | integer                  | not null      email         | character varying(255)   |
 email         | character varying(255)   |               scheduled_at  | timestamp with time zone | not null
Indexes:                                                  Indexes:
    "events_pkey" PRIMARY KEY, btree (id)                     "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)                "idx_events_scheduled_at" btree (scheduled_at)
```

After migration 2: all 5 rows `scheduled_at = event_date` (`same = t`), both columns `NOT NULL`, both indexes present. `alembic current` → `ec4613a31045 (head)`. `GET /events` after migration 3 → `[{"id":1,"name":"Go Conference 2026",…,"date":"2026-09-15T09:00:00+00:00",…`.

### B.4 5xx: baseline, every step, final

gateway pods did not restart during the sequence (all `r0`), so the raw counter is comparable. Client prober `GET /events` ran from 02:11:43 for 600 s.

| Step | Time | `sum(gateway_requests_total{status=~"5.."})` after |
|---|---|---|
| baseline | 02:10:39 | `[]` (0) |
| Migration 1 (add column + concurrent index) | 02:11:51 | 0 |
| **Deploy A** (image `lab12-a`, rolling update) | 02:11:57 → 02:12:17 | **1** |
| Migration 2 (backfill + NOT NULL) | 02:12:17 | **2** |
| Deploy A′: same code, **+ preStop `sleep 5` on events** | 02:13:10 → 02:13:32 | 2 |
| **Deploy B** (image `lab12-b`) | 02:14:02 → 02:14:28 | 2 |
| Migration 3 (drop `event_date`) | 02:14:28 | 2 |

```text
$ diff 5xx.baseline 5xx.final
< []
> [{'metric': {}, 'value': [1791771274.27, '2']}]
$ sum by (pod,path,status) (gateway_requests_total{status=~"5.."})
  path=/reserve/{id}/pay,pod=gateway-79c6744678-t6x9c,status=500: 1
  path=/reserve/{id}/pay,pod=gateway-79c6744678-rvl2t,status=500: 1
gateway log t6x9c 02:12:12.180  ERROR confirm error after payment: All connection attempts failed
gateway log rvl2t 02:12:17.513  ERROR confirm error after payment:
PROBER (GET /events, 02:11:43 + 600 s, covering every step): ok=4288 fail=0
```

**The delta is 2, not 0, and neither error came from the schema.** Both are `/pay` → events `/reservations/{id}/confirm` calls that hit the *old* events pods while Deploy A's rolling update stopped them (02:12:12, refused connection; 02:12:17.5, the old pod `zpc6w` was terminating, so a keep-alive connection was cut mid-request). The second error has the same timestamp as migration 2, but the postgres and events logs show no SQL error, and its signature is the connection one. The events pods had no preStop: the graceful shutdown from 12.6 was added only to the gateway. As in Labs 8–11, the user was charged and got no order.

The fix was applied mid-sequence: Deploy A′ rolled out the same code with a 5 s preStop (`k8s/events.yaml` now has it). After that, **Deploy B and migration 3 added 0** (counter stayed at 2), as did migration 1 and Deploy A′. The schema transitions themselves produced zero errors. The deploys did until graceful shutdown covered the service being deployed.

### B.5 Which step would have caused 5xx if reordered earlier?

**Migration 3 (drop `event_date`).** It is the only step that *removes* something code depends on. Run before Deploy B is complete, every read on a pod still running old or Deploy-A code fails with `column e.event_date does not exist`. The other steps only add (M1), fill (M2) or change code that tolerates both states (A, B). There is a second, symmetric hazard: **Deploy A before migration 1** references `scheduled_at` before it exists. I ran that exact query at 02:11:21 and got `ERROR: column e.scheduled_at does not exist`.

### B.6 Batched backfill at 10M rows

```python
last_id = 0
while True:
    with conn.begin():                                    # one short transaction per batch
        rows = conn.execute("""
            UPDATE events SET scheduled_at = event_date
            WHERE id IN (SELECT id FROM events
                         WHERE id > :last AND scheduled_at IS NULL
                         ORDER BY id LIMIT 10000)
            RETURNING id""", last=last_id).fetchall()
    if not rows: break
    last_id = max(r.id for r in rows)
    time.sleep(0.1)                                       # let replication / vacuum / traffic breathe
# then NOT NULL without a long ACCESS EXCLUSIVE scan:
#   ALTER TABLE events ADD CONSTRAINT scheduled_at_nn CHECK (scheduled_at IS NOT NULL) NOT VALID;
#   ALTER TABLE events VALIDATE CONSTRAINT scheduled_at_nn;      -- SHARE UPDATE EXCLUSIVE, online
#   ALTER TABLE events ALTER COLUMN scheduled_at SET NOT NULL;   -- PG12+: uses the valid CHECK, no scan
```

Each batch locks at most 10k rows for milliseconds, and progress survives interruption (the `IS NULL` filter makes it resumable). My migration 2 does a single `UPDATE` + `SET NOT NULL`. That is fine at 5 rows, but at 10M rows the `SET NOT NULL` alone would scan the table under ACCESS EXCLUSIVE.

### B.7 Why the migration-3 downgrade is not real rollback safety once Deploy B is live

The downgrade restores the column's *existence*, not the data written meanwhile, and not the code. After Deploy B, new or changed events are written only to `scheduled_at`. The downgrade copies `scheduled_at` back into `event_date` once, at downgrade time, and anything written after that copy by Deploy B code goes to `scheduled_at` only. Rolling the *code* back to Deploy A or older would then read stale or missing `event_date` values (old code reads `event_date` only). Worse, if the downgrade runs while Deploy B is still serving, it works fine, and nothing tells you the two columns are drifting. For a rollback to be safe:

- **the code in production must write both columns again** (redeploy A, the dual-write version) *before* any old reader comes back;
- the backfill `event_date = scheduled_at` must run *after* that dual-write is live, so no write falls into the gap;
- there must be no data in the new shape that the old schema cannot represent.

In practice teams keep the old column (or a trigger that syncs both) for a while after Deploy B. Migration 3 is the point of no return, and it should be delayed until rollback to A is no longer needed.

---

## 12.9 (Optional) HPA

`k8s/gateway-hpa.yaml`:

```yaml
# HorizontalPodAutoscaler for the gateway Rollout (Lab 12, optional 12.9).
# HPA works on any resource with a scale subresource; Argo Rollouts has one.
# Utilization is measured against the container's CPU *request* (50m).
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: gateway
spec:
  scaleTargetRef:
    apiVersion: argoproj.io/v1alpha1
    kind: Rollout
    name: gateway
  minReplicas: 5    # never below the Lab 7 base (canary steps assume 5)
  maxReplicas: 12
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

Gateway requests are 50m CPU, and utilization is measured against them. Load: the Lab 10 Locust scenario as a Job, `-u 200 -r 20 -t 120s`, started 02:22:44 (mixedload kept running).

```text
$ kubectl get hpa gateway            # before load
gateway   Rollout/gateway   cpu: 37%/70%   5   12   5   30s
time      cpu (of request)   Rollout spec/ready
02:22:44  37%/70%            5/5
02:23:15  97%/70%            7/4      ← scale-up 1
02:23:35  148%/70%           11/1     ← scale-up 2
02:24:05  104%/70%           11/3
02:24:36  69%/70%            11/1
02:24:46  120%/70%           11/5
02:25:06  107%/70%           11/11
02:25:37  24%/70%            11/11    ← load over (Locust ran 120 s)
$ kubectl describe hpa gateway
  Normal  SuccessfulRescale  New size: 7; reason: cpu resource utilization (percentage of request) above target
  Normal  SuccessfulRescale  New size: 11; reason: cpu resource utilization (percentage of request) above target
Locust: Aggregated 14343 requests, 11554 failures (80.55 %), 119.61 req/s
```

(I sampled the table with `kubectl get hpa` and `kubectl get rollout` every 10 s. One awk column in `raw.log` is shifted because `TARGETS` contains a space. The values above are read from that raw output.)

The HPA did what it is told: utilization > 70 % → 5 → 7 → 11 within 50 s. **Users did not benefit.** 80.6 % of Locust requests failed, because the bottleneck is not the gateway. Lab 10 showed Postgres CPU-throttled at its 200m limit and events' 10-connection pool. Scaling the gateway sends *more* concurrent requests at that bottleneck. The "ready" column also dropped to 1/11 several times: with the 12.6 readiness (2 s × **1** failure) on the gateway's *deep* `/health`, an overloaded events made gateway pods flap out of the Service, and liveness restarted gateway pods 18 times across the 11 (events: 2). An HPA on the gateway would need a CPU-independent signal (RPS per pod, or latency) and, above all, the data tier fixed first. After the test: HPA deleted, Locust Job and ConfigMap removed, `kubectl apply -f k8s/gateway.yaml` → back to 5/5 `Healthy`, `kubectl diff` empty.

---

## Cleanup / final state

- mixedload deleted, event 1 `total_tickets` back to 100, port-forward 15432 and `kubectl proxy` stopped (`pgrep` clean).
- **Every live change matches git** (`kubectl diff -f k8s/{gateway,events,payments,notifications,pdb}.yaml` → 0 lines apart from ArgoCD's `tracking-id` annotation). Compared with the pre-lab snapshot:
  - gateway: `terminationGracePeriodSeconds: 40`, preStop, readiness 2 s × 1, topology spread;
  - events: 2 replicas, image `quickticket-events:lab12-b` (`IfNotPresent`), preStop 5 s;
  - payments and notifications: 2 replicas;
  - redis and postgres: identical.
- PDBs stay. Schema at `ec4613a31045` (`event_date` dropped, `scheduled_at NOT NULL`, `idx_events_scheduled_at`).
- **Consequence to be aware of:** any events build that still reads `event_date` (upstream `main`, the ghcr image in fork `main`'s `k8s/events.yaml`) now fails on `/events` against this database. ArgoCD (`automated: {}`, no sync since 2026-09-28) syncing fork `main` would roll events back to such an image. This is the same GitOps drift risk as in Labs 9–11, now with a schema dependency.
