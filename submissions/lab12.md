# Lab 12 — Advanced Kubernetes Resilience

`mixedload` ran throughout. Live objects used locally built images (`imagePullPolicy: IfNotPresent`); Git keeps the ghcr.io images. Times are UTC, 2026-10-18.

## Task 1 — Multi-Replica Failover + PDBs

**`kubectl get deploy,rollout`:**
```
deployment.apps/events          2/2
deployment.apps/notifications   2/2
deployment.apps/payments        2/2
deployment.apps/postgres        1/1
deployment.apps/redis           1/1
rollout.argoproj.io/gateway     5/5
```

**Pod kill under load** (one gateway pod + one events pod at 11:52:38):
```
5xx [3m] before: 0
all 7 pods ready after 7s
5xx [1m] after: 0
5xx [3m] covering the kill: 0
```

**`kubectl get pdb`:**
```
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
events-pdb          1               N/A               1
gateway-pdb         2               N/A               3
notifications-pdb   N/A             1                 1
payments-pdb        1               N/A               1
```

**Topology spread (live spec):**
```json
[{"labelSelector": {"matchLabels": {"app": "gateway"}}, "maxSkew": 1,
  "topologyKey": "kubernetes.io/hostname", "whenUnsatisfiable": "ScheduleAnyway"}]
```
```
gateway-6cf95668d5-2krkq 1/1 Running k3d-quickticket-server-0
gateway-6cf95668d5-hc7vj 1/1 Running k3d-quickticket-server-0
gateway-6cf95668d5-hqwf9 1/1 Running k3d-quickticket-server-0
gateway-6cf95668d5-r5tkx 1/1 Running k3d-quickticket-server-0
gateway-6cf95668d5-wqctm 1/1 Running k3d-quickticket-server-0
```

**Eviction against `events-pdb` tightened to `minAvailable: 2`:**
```json
{
  "status": "Failure",
  "message": "Cannot evict pod as it would violate the pod's disruption budget.",
  "reason": "TooManyRequests",
  "details": {"causes": [{"reason": "DisruptionBudget",
    "message": "The disruption budget events-pdb needs 2 healthy pods and has 2 currently"}]},
  "code": 429
}
```

**3 replicas with `minAvailable: 1` — max simultaneous evictions? Why `minAvailable: 2` with 5?**
2. With 5 replicas, `minAvailable: 2` allows 3 at once, so a drain can actually finish while 2 pods still serve traffic. A higher floor like 4 would make drains crawl or stall.

**`maxSkew: 1` on 3 nodes?** 5 pods → 2/2/1. 7 pods → 3/2/2.

## Task 2 — Graceful Shutdown + Zero-Downtime Migration

**`k8s/gateway.yaml`:**
```yaml
      terminationGracePeriodSeconds: 40
      ...
          lifecycle:
            preStop:
              exec:
                command: ["sh", "-c", "sleep 10"]
          readinessProbe:
            httpGet:
              path: /metrics
              port: 8080
            periodSeconds: 2
            failureThreshold: 1
```
Readiness stays on `/metrics`, not `/health`: `/health` depends on events and payments (Lab 7).

**Rolling restart** (`kubectl argo rollouts restart gateway`, 54 s):
```
5xx [1m] before: 0
5xx [3m] after:  0
client polling /events every 0.2 s during the restart: 683 × 200
```

**Migration:**
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
```
```
Running upgrade f8fd975e1b76 -> 8e028e241bb0, index events.event_date concurrently
0.247 total
5xx total before: 0   after: 0   → no change

Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
```

**Expand-and-contract rename `event_date` → `scheduled_at`:**
1. **M1** — `ALTER TABLE events ADD COLUMN scheduled_at timestamptz NULL`.
2. **Deploy A** — read `COALESCE(scheduled_at, event_date)`, write both columns.
3. **M2** — `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL`, then `SET NOT NULL`. Safe live: Deploy A reads either state, and the update is idempotent.
4. **Deploy B** — read and write only `scheduled_at`.
5. **M3** — `ALTER TABLE events DROP COLUMN event_date`.

**HPA (12.9):** `k8s/gateway-hpa.yaml` (Rollout target, min 5, max 12, CPU 70%). Locust 200 users / 150 s:
```
SuccessfulRescale  New size: 7; reason: cpu resource utilization (percentage of request) above target
SuccessfulRescale  New size: 9; reason: cpu resource utilization (percentage of request) above target
Locust: 154 RPS, 58.7% failures — almost all 429 from the Lab 11 rate limiter
```

**Why does `CREATE INDEX CONCURRENTLY` matter?**
A plain `CREATE INDEX` takes a lock that blocks all writes for the whole build — minutes on 10M rows, so every reserve and confirm hangs. `CONCURRENTLY` builds without blocking reads or writes, at the cost of a slower build and no transaction.

**Why must M3 come after Deploy B is fully rolled out?**
Any process still on Deploy A reads `event_date` through the `COALESCE`. Drop the column under it and every `/events` call returns 500. This happened in the rollback drill below.

## Bonus — Expand-and-Contract Executed Live

**Migrations (`upgrade()` only):**
```python
# 64f12464912c_add_events_scheduled_at_column.py
op.add_column('events', sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True), nullable=True))

# 0b99dd9e96c4_backfill_events_scheduled_at.py
op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
op.alter_column('events', 'scheduled_at', nullable=False)

# aadf3fbabc16_drop_events_event_date.py
op.drop_column('events', 'event_date')
```

**`app/events/main.py`.** Original → Deploy A:
```diff
-            SELECT e.id, e.name, e.venue, e.event_date, e.total_tickets, e.price_cents,
+            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date, e.total_tickets, e.price_cents,
-            GROUP BY e.id ORDER BY e.event_date
+            GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
-            SELECT e.id, e.name, e.venue, e.event_date, e.total_tickets, e.price_cents,
+            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date, e.total_tickets, e.price_cents,
```
Deploy A → Deploy B:
```diff
-            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date, e.total_tickets, e.price_cents,
+            SELECT e.id, e.name, e.venue, e.scheduled_at AS event_date, e.total_tickets, e.price_cents,
-            GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
+            GROUP BY e.id ORDER BY e.scheduled_at
-            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date, e.total_tickets, e.price_cents,
+            SELECT e.id, e.name, e.venue, e.scheduled_at AS event_date, e.total_tickets, e.price_cents,
```
- No runtime write path exists (events are only inserted by `seed.sql`), so dual-write was a no-op.
- The response keeps the `event_date` field name via the alias, so API clients see no change.
- `app/seed.sql` now creates and inserts `scheduled_at`; checked on a fresh Postgres.

**First attempt: 5 extra 5xx, not zero.** All came during the two events rollouts (`All connection attempts failed`). events had no `preStop`, so its pods exited before kube-proxy stopped routing to them — the same issue 12.6 fixes for gateway. Fix: `preStop: sleep 5` on events, payments and notifications.

The chain was then rolled back under load (M3↓ → Deploy A → M2↓ → original code → M1↓). The rollback drill produced 11 5xx. Some came from the old preStop-less pods. The rest were `/events` 500s: M1↓ dropped `scheduled_at` while old Deploy-A pods were still in their 5 s `preStop` and serving. `rollout status` reports done before old pods are gone, so the forward run waited until no events pod was `Terminating` before each migration.

**Clean run** (12:20:49 → 12:22:24):
```
12:20:49 BASELINE        5xx_total=17
12:21:00 after M1        5xx_total=17
12:21:31 after Deploy A  5xx_total=17
12:21:42 after M2        5xx_total=17
12:22:13 after Deploy B  5xx_total=17
12:22:24 after M3        5xx_total=17

sum(increase(gateway_requests_total{status=~"5.."}[101s])) = 0   (2184 requests)
client polling /events every 0.2 s: 1087 × 200
```

**Backfill check after M2:**
```
 id |       event_date       |      scheduled_at
----+------------------------+------------------------
  1 | 2026-09-15 09:00:00+00 | 2026-09-15 09:00:00+00
  2 | 2026-10-01 18:00:00+00 | 2026-10-01 18:00:00+00
  3 | 2026-11-20 10:00:00+00 | 2026-11-20 10:00:00+00
  4 | 2026-09-22 14:00:00+00 | 2026-09-22 14:00:00+00
  5 | 2026-10-10 10:00:00+00 | 2026-10-10 10:00:00+00
```

**`\d events` before M1 / after M3:**
```
before:  event_date    | timestamp with time zone | not null
after:   scheduled_at  | timestamp with time zone | not null      (event_date gone)
```

**Which step would cause 5xx if moved earlier?**
M3. It is the only step that removes something code may still read. M1 and M2 only add or fill data, and Deploy B only stops reading the old column. Even "right after `rollout status`" is too early, as the rollback drill showed.

**Batched backfill for 10M rows:**
```
last_id = 0
loop:
    ids = SELECT id FROM events WHERE id > last_id AND scheduled_at IS NULL
          ORDER BY id LIMIT 10000
    if ids is empty: break
    UPDATE events SET scheduled_at = event_date WHERE id = ANY(ids)
    COMMIT
    last_id = max(ids)
    sleep(0.1)          # let replication and autovacuum keep up
ALTER TABLE events ALTER COLUMN scheduled_at SET NOT NULL   # after all batches
```
Use `ADD CONSTRAINT ... CHECK (scheduled_at IS NOT NULL) NOT VALID` + `VALIDATE` before `SET NOT NULL` to avoid a long full-table scan under an exclusive lock.

**Why isn't the M3 downgrade enough for rollback once Deploy B is live?**
- It rebuilds `event_date` from `scheduled_at` as of the moment it runs, but Deploy B never writes `event_date`. Old code rolled back after a delay works only if nothing changed in between, and nothing stops writes during the gap.
- Indexes and constraints are not restored: `idx_events_event_date` went away with the column and the downgrade doesn't recreate it.
- Safe rollback needs Deploy B to keep dual-writing `event_date` until M3, and M3 to happen only after a soak period. Past that point, roll forward instead of back.
