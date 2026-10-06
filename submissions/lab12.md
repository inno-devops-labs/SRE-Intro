# Lab 12 — Bonus: Advanced Kubernetes Resilience

## Summary

Made QuickTicket resilient to node maintenance and rolling-deploy events:

- scaled `events` / `payments` / `notifications` to 2 replicas (gateway already 5 via Rollout)
- added `k8s/pdb.yaml` with 4 PodDisruptionBudgets
- added `topologySpreadConstraints` to the gateway Rollout
- proved a PDB rejects an eviction at the API level (HTTP 429, `reason: DisruptionBudget`)
- added `preStop` + fast `readinessProbe` + 40s grace period to the gateway — zero-downtime rolling restarts
- wrote and ran a `CREATE INDEX CONCURRENTLY` Alembic migration under live load
- executed the full expand-and-contract rename `events.event_date` → `events.scheduled_at` live (3 migrations + 2 code deploys) with zero 5xx across all 5 transitions

One environment note: before starting, the 3-minute 5xx baseline was not zero — kubelet probes fanning out to upstream `/health` with a 2s timeout occasionally produced gateway `/health` 503s (~0.5% of probe traffic, a steady-state artifact of CPU-throttled single-node k3d). I paused `mixedload`, waited for the metric window to clear to 0, then resumed traffic before taking any baseline.

A second note, discovered mid-bonus: the first `events` rolling update (Deploy A) produced 3 transient 502s on `/events` — the classic endpoint-propagation gap, because the `events` Deployment had no `preStop`. I applied the same graceful-shutdown pattern (10s `preStop` + `periodSeconds: 2`/`failureThreshold: 1` readiness + 40s grace) to `k8s/events.yaml`, reset the 5xx clock (gateway restart), re-baselined, and every transition from that point ran with zero 5xx.

---

## Task 1 — Multi-Replica Failover + PDBs

### 1. Replica counts (`kubectl get deploy,rollout`)

```text
NAME                            READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/events          2/2     2            2           9d
deployment.apps/mixedload       2/2     2            2           19h
deployment.apps/notifications   2/2     2            2           20h
deployment.apps/payments        2/2     2            2           9d
deployment.apps/postgres        1/1     1            1           9d
deployment.apps/redis           1/1     1            1           9d

NAME                          DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
rollout.argoproj.io/gateway   5         5         5            5           26h
```

### 2. 5xx before / after the coordinated pod kill (mixedload running)

Killed one gateway pod and one events pod at 00:17:14 with `--wait=false`; both replacements were Ready within ~9s.

```text
# before: sum(increase(gateway_requests_total{status=~"5.."}[3m]))
0

# after:  sum(increase(gateway_requests_total{status=~"5.."}[1m]))
0
```

Replacement pods (new names, same ReplicaSet):

```text
events-97ccb8c6f-bcbx8   1/1   0
gateway-5bfdcf55f8-cwmh6 1/1   0
```

Zero 5xx: during the gap the Service endpoints rerouted traffic to the surviving replicas.

### 3. `kubectl get pdb`

```text
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb          1               N/A               1                     1s
gateway-pdb         2               N/A               3                     1s
notifications-pdb   N/A             1                 1                     1s
payments-pdb        1               N/A               1                     1s
```

Exactly the expected values: gateway tolerates 3 simultaneous evictions, events/payments keep 1 live, notifications is best-effort.

### 4. Topology spread — live spec + actual placement

```bash
$ kubectl get rollout gateway -o jsonpath='{.spec.template.spec.topologySpreadConstraints}'
[{"labelSelector":{"matchLabels":{"app":"gateway"}},"maxSkew":1,"topologyKey":"kubernetes.io/hostname","whenUnsatisfiable":"ScheduleAnyway"}]
```

```text
$ kubectl get pod -l app=gateway -o wide
NAME                       READY   STATUS        RESTARTS   AGE   IP            NODE                       NOMINATED NODE   READINESS GATES
gateway-5bfdcf55f8-jfwxf   1/1     Terminating   0          17h   10.42.0.208   k3d-quickticket-server-0   <none>           <none>
gateway-655d66495-cbbc7    1/1     Running       0          35s   10.42.0.224   k3d-quickticket-server-0   <none>           <none>
gateway-655d66495-f65vk    1/1     Running       0          11h   10.42.0.223   k3d-quickticket-server-0   <none>           <none>
gateway-655d66495-mgx82    1/1     Running       0          35s   10.42.0.225   k3d-quickticket-server-0   <none>           <none>
gateway-655d66495-t6694    1/1     Running       0          7s    10.42.0.227   k3d-quickticket-server-0   <none>           <none>
gateway-655d66495-z7hvj    1/1     Running       0          7s    10.42.0.226   k3d-quickticket-server-0   <none>           <none>
```

All 5 pods on the only node — expected on single-node k3d; the constraint is correct and live in the spec, ready for a multi-node cluster.

### 5. PDB eviction rejection (HTTP 429)

Tightened `events-pdb` to `minAvailable: 2` (ALLOWED DISRUPTIONS → 0), opened `kubectl proxy --port=8901`, and POSTed one eviction to the API:

```bash
$ curl -s -X POST -H 'Content-Type: application/json' \
    -d '{"apiVersion":"policy/v1","kind":"Eviction","metadata":{"name":"events-97ccb8c6f-bcbx8","namespace":"default"}}' \
    http://localhost:8901/api/v1/namespaces/default/pods/events-97ccb8c6f-bcbx8/eviction
# HTTP 429
```

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

PDB then restored to `minAvailable: 1`.

### 6. Answer — PDB eviction math

With 3 replicas and `minAvailable: 1`, the maximum number of pods that can be evicted simultaneously is **2** (3 − 1). One replica must stay available at all times, so at most two may be disrupted in parallel.

My `gateway-pdb` is `minAvailable: 2` with 5 replicas (tolerates 3 evictions) because the gateway is the critical path but we still have to be able to replace nodes: `minAvailable: 4` would let only 1 pod be evicted at a time, and a node drain of 4 pods would stall on the PDB forever — the cluster autoscaler / drain could never make progress. `minAvailable: 2` keeps ~half of normal capacity while a rolling drain reschedules the rest, so maintenance is always possible.

### 7. Answer — maxSkew: 1 placements

- **5 pods, 3 nodes:** 2/2/1. The most-loaded and least-loaded nodes may differ by at most 1.
- **7 pods, 3 nodes:** 3/2/2. (7 = 3×2 + 1, so one node takes the extra pod; 3−2 = 1 = maxSkew.)

---

## Task 2 — Graceful Shutdown + Zero-Downtime Migration

### 1. `preStop` / `readinessProbe` block as it appears in `k8s/gateway.yaml`

```yaml
      # Give in-flight requests time to finish after SIGTERM (10s preStop + up to 30s drain).
      terminationGracePeriodSeconds: 40
      containers:
        - name: gateway
          ...
          lifecycle:
            # Sleep BEFORE SIGTERM reaches the app. Gives kube-proxy / endpoints
            # controllers time to propagate this pod's NotReady state to every
            # node's iptables, so new traffic stops routing here BEFORE uvicorn
            # shuts down. Without this, there's a ~5-10s window where SIGTERM
            # + incoming traffic overlap and requests get RST.
            preStop:
              exec:
                command: ["sh", "-c", "sleep 10"]
          ...
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            periodSeconds: 2
            failureThreshold: 1
```

Applied via canary (analysis template passed, Rollout → Healthy). Verified live in the spec: `terminationGracePeriodSeconds=40`, `preStop=["sh","-c","sleep 10"]`.

### 2. 5xx before / after the rolling restart (mixedload running)

```text
# before: sum(increase(gateway_requests_total{status=~"5.."}[1m]))
0

$ kubectl argo rollouts restart gateway
rollout 'gateway' restarts in 0s
# ... canary ...
Healthy

# after (sleep 10): sum(increase(gateway_requests_total{status=~"5.."}[3m]))
0
```

All 5 pods cycled to the new ReplicaSet (`gateway-65cb9b46d7-*`, ages 54–76s at check time) with zero 5xx.

### 3. Migration code (`migrations/versions/5e0902edb4c9_index_events_event_date_concurrently.py`)

```python
def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction block, so it
    # must run outside Alembic's default transactional DDL wrapper.
    with op.get_context().autocommit_block():
        op.create_index(
            'idx_events_event_date',
            'events',
            ['event_date'],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index('idx_events_event_date', table_name='events', if_exists=True)
```

The `autocommit_block()` wrapper is the key detail — without it Postgres rejects the DDL with `ActiveSqlTransaction: CREATE INDEX CONCURRENTLY cannot run inside a transaction block`.

### 4. 5xx before / after the migration (live mixedload traffic)

```text
# before: sum(gateway_requests_total{status=~"5.."})  → no 5xx series (0)
$ time alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade cf66c14f522d -> 5e0902edb4c9, index events.event_date concurrently
real	0m0.641s

# after:  sum(gateway_requests_total{status=~"5.."})  → no 5xx series (0)
$ diff /tmp/5xx.before /tmp/5xx.after   # no output — identical
```

Zero 5xx during the migration.

### 5. `\d events` showing the new index

```text
    "idx_events_event_date" btree (event_date)
```

(Full `\d events` at that point showed `event_date` plus the index; the index was later dropped automatically when the Bonus task dropped its column — expected end state.)

### 6. Expand-and-contract sketch: `events.event_date` → `events.scheduled_at`

1. **Migration 1 (expand):** `ALTER TABLE events ADD COLUMN scheduled_at TIMESTAMPTZ NULL;` — nullable, so it's a metadata-only change, instant, no table rewrite. Old and new code both work.
2. **Code deploy A:** write to BOTH columns; read `COALESCE(scheduled_at, event_date)` so rows not yet backfilled (NULL) still resolve. Response shape unchanged.
3. **Migration 2 (backfill):** `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL;` then `ALTER TABLE events ALTER COLUMN scheduled_at SET NOT NULL;` — safe under live traffic because deploy A tolerates NULL via COALESCE, and the UPDATE is idempotent.
4. **Code deploy B:** read and write ONLY `scheduled_at`. Old code is no longer needed; both column names still exist in the schema.
5. **Migration 3 (contract):** `ALTER TABLE events DROP COLUMN event_date;` — only after deploy B is fully rolled out, so nothing references the old column anymore.

At every intermediate point, both the old and the new code work; the brief overlap where the column has both names is what makes the whole sequence reversible at each step.

### 7. Answer — why `CREATE INDEX CONCURRENTLY` matters

Without `CONCURRENTLY`, `CREATE INDEX` takes an `ACCESS EXCLUSIVE` lock on the table for the entire build. On a 10M-row table that lock is held for minutes: every read AND write to the table blocks, the app effectively stops, and long-blocked clients time out — a user-visible outage caused by a DDL statement. `CONCURRENTLY` builds the index in two passes under a much milder `SHARE UPDATE EXCLUSIVE` lock that blocks neither reads nor writes, so traffic flows through the whole build. The cost is a longer, non-transactional build (and a possible INVALID index if it fails, which must be dropped and retried) — but that's invisible to users.

### 8. Answer — why migration 3 must come after deploy B

Migration 3 removes `event_date` from the schema. Deploy A's queries reference it (`COALESCE(scheduled_at, event_date)`), so if the drop ran while any deploy-A pod was still serving, every `/events` request from that pod would fail with `column "event_date" does not exist` → 500s. Deploy B is the first version that never touches `event_date`, so the column can only be dropped once deploy B is fully rolled out and no deploy-A pods remain.

---

## Bonus Task — Executed Expand-and-Contract Rename

Ran the full 5-transition sequence (M1 → Deploy A → M2 → Deploy B → M3) under live mixedload. Zero 5xx delta across the entire sequence.

Setup notes:

- **Dual-write skipped (by design):** QuickTicket's events service has no runtime INSERT path into `events` — rows only come from `seed.sql` at cluster boot. So the dual-write half of Deploy A is a no-op; only the read paths needed the COALESCE change. The seed was updated in step 4 (Deploy B) so a fresh cluster boots on the new schema.
- **Deploy B response-shape choice:** I kept the `AS event_date` alias in the SELECT, so the `/events` JSON response (`"date": ...`) is byte-for-byte identical to before. Clients don't notice the rename.
- **events Deployment got the graceful-shutdown treatment** (`preStop` 10s + fast readiness + 40s grace in `k8s/events.yaml`) because the first naive rollout produced 3 transient 502s from the endpoint-propagation gap. After that fix, both deploy rollouts ran clean.

### 1. The three migration files (upgrade() bodies)

**M1 — `09ebb6ce241b_add_events_scheduled_at_column.py`**

```python
def upgrade() -> None:
    # Expand: add the new column as NULLABLE. A NOT NULL column (even with a
    # default) would force a full table rewrite under an ACCESS EXCLUSIVE lock;
    # a nullable add is metadata-only and instant.
    op.add_column(
        'events',
        sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True), nullable=True),
    )
```

**M2 — `b4d051a85a3f_backfill_events_scheduled_at.py`**

```python
def upgrade() -> None:
    # Backfill is idempotent (WHERE scheduled_at IS NULL) — safe to re-run,
    # and safe under live traffic because Deploy A reads via COALESCE and
    # tolerates both NULL and non-NULL scheduled_at.
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    # Only after every row is populated can the column become NOT NULL.
    op.alter_column('events', 'scheduled_at', nullable=False)
```

**M3 — `77526c98f7f3_drop_events_event_date.py`**

```python
def upgrade() -> None:
    # Contract: safe ONLY now, because Deploy B is fully rolled out and no
    # code reads or writes event_date anymore. Any surviving Deploy-A pod
    # would 500 on every /events request (COALESCE on a missing column).
    op.drop_column('events', 'event_date')
```

### 2. Diff of `app/events/main.py` between Deploy A and Deploy B

```diff
--- main.py (Deploy A)
+++ main.py (Deploy B)
@@ list_events()
             SELECT e.id, e.name, e.venue,
-                   COALESCE(e.scheduled_at, e.event_date) AS event_date,
+                   e.scheduled_at AS event_date,
                    e.total_tickets, e.price_cents,
                    COALESCE(SUM(o.quantity), 0) as confirmed
             FROM events e LEFT JOIN orders o ON e.id = o.event_id
-            GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
+            GROUP BY e.id ORDER BY e.scheduled_at
@@ get_event()
             SELECT e.id, e.name, e.venue,
-                   COALESCE(e.scheduled_at, e.event_date) AS event_date,
+                   e.scheduled_at AS event_date,
                    e.total_tickets, e.price_cents,
                    COALESCE(SUM(o.quantity), 0) as confirmed
             FROM events e LEFT JOIN orders o ON e.id = o.event_id
             WHERE e.id = %s GROUP BY e.id
```

Both deploys were shipped as `docker build -t quickticket-events:v1 ./app/events` + `k3d image import` + `kubectl rollout (restart) deployment/events`; `k8s/events.yaml` now pins `image: quickticket-events:v1` with `imagePullPolicy: Never`.

### 3. `\d events` before M1 and after M3

**Before M1:**

```text
Table "public.events"
    Column     |           Type           | Collation | Nullable |              Default
---------------+--------------------------+-----------+--------------+------------------------------------
 id            | integer                  |           | not null     | nextval('events_id_seq'::regclass)
 name          | text                     |           | not null     |
 venue         | text                     |           | not null     |
 event_date    | timestamp with time zone |           | not null     |
 total_tickets | integer                  |           | not null     |
 price_cents   | integer                  |           | not null     |
 email         | character varying(255)   |           |              |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
```

**After M3:**

```text
Table "public.events"
    Column     |           Type           | Collation | Nullable |              Default
---------------+--------------------------+-----------+--------------+------------------------------------
 id            | integer                  |           | not null     | nextval('events_id_seq'::regclass)
 name          | text                     |           | not null     |
 venue         | text                     |           | not null     |
 total_tickets | integer                  |           | not null     |
 price_cents   | integer                  |           | not null     |
 email         | character varying(255)   |           |              |
 scheduled_at  | timestamp with time zone |           | not null     |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
```

The column moved: `event_date` is gone, `scheduled_at TIMESTAMPTZ NOT NULL` is in. (The `idx_events_event_date` index was dropped automatically with its column.)

Backfill verification after M2:

```text
 id |       event_date       |      scheduled_at
----+------------------------+------------------------
  1 | 2026-09-15 09:00:00+00 | 2026-09-15 09:00:00+00
  2 | 2026-10-01 18:00:00+00 | 2026-10-01 18:00:00+00
  3 | 2026-11-20 10:00:00+00 | 2026-11-20 10:00:00+00
  4 | 2026-09-22 14:00:00+00 | 2026-09-22 14:00:00+00
  5 | 2026-10-10 10:00:00+00 | 2026-10-10 10:00:00+00
```

### 4. 5xx baseline + final + diff

```text
# baseline (after clock reset, before M1): sum(gateway_requests_total{status=~"5.."})
{"status":"success","data":{"resultType":"vector","result":[]}}     # no 5xx series = 0

# final (after M3):
{"status":"success","data":{"resultType":"vector","result":[]}}     # no 5xx series = 0

$ diff /tmp/5xx.baseline /tmp/5xx.final
# (no output — identical)
```

Zero 5xx delta across all 5 transitions. (The cumulative counter is per-pod; the gateway fleet was restarted right before the baseline, so "no 5xx series" is exactly "zero 5xx since baseline".)

### 5. Answer — which step breaks if reordered earlier

**Migration 3 (drop `event_date`) run before Deploy B.** It is the only step that *removes* something the old code needs: deploy A's `COALESCE(scheduled_at, event_date)` references a column that no longer exists, so every `/events` request from any surviving deploy-A pod 500s. Every other step is additive or idempotent if done early: M1 (add nullable column) is harmless to old code, deploy A is backward-compatible by construction, M2's backfill is a no-op re-run, and deploy B only breaks if M1/M2 haven't populated `scheduled_at` — but in the intended order they always have.

### 6. Answer — batched backfill at 10M-row scale

A single `UPDATE` on 10M rows holds row locks (and WAL pressure) for minutes, blocking writers on those rows the whole time. Keep each transaction small:

```python
BATCH = 10_000
last_id = 0
while True:
    with engine.begin() as conn:          # each batch = its own transaction
        n = conn.execute(
            """UPDATE events
               SET scheduled_at = event_date
               WHERE scheduled_at IS NULL
                 AND id > :last
               ORDER BY id
               LIMIT :batch""",
            {"last": last_id, "batch": BATCH},
        ).rowcount
        last_id = conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM events WHERE id > :last",
            {"last": last_id},
        ).scalar()
    if n == 0:
        break
    time.sleep(0.05)                       # yield locks / let WAL catch up
```

Each transaction touches ~10k rows, commits, and releases its locks; writers only block on the tiny current batch. (In Postgres you'd typically use `ctid`- or PK-range batching; `UPDATE ... LIMIT` needs a CTE wrapper — the shape is what matters: small transactions, sleep between batches, idempotent predicate.)

### 7. Answer — why M3's downgrade is not true rollback safety

M3's downgrade can re-create the `event_date` column and re-populate it from `scheduled_at` at the schema level — but once Deploy B is live in production, the *code* no longer writes `event_date`. Any rows written after deploy B only ever touched `scheduled_at`; the downgraded column is a pure copy, so a schema-level rollback looks fine — until you also roll the code back to deploy A. Deploy A writes to BOTH columns, so the rollback sequence is only safe if: (a) no new rows are written between the schema downgrade and the code rollback (frozen writes), or (b) you accept that deploy A's dual-write will re-synchronize `event_date` going forward. More fundamentally: the rollback path itself takes new traffic — a deploy-A pod serving traffic after the downgrade will read `event_date` (now a copy) and that's fine, but any request that lands on a pod mid-rollout, or any writer that only ever ran as deploy B, leaves the two columns divergent in ways the downgrade cannot know about. True rollback safety requires the code rollback to be deployable at all — i.e., the application must still support the old column, which means you must keep a deploy-A-capable build (and its dual-write) around until the contract phase is irreversible. In practice that's why teams keep the old read path warm for an extra release cycle after the drop.
