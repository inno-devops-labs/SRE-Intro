# Lab 12 — Advanced Kubernetes Resilience (Bonus)

## Made by:
### Nurmuhametov Denis (d.nurmuhametov@innopolis.university)

---

## Overview

This lab makes QuickTicket resilient to node maintenance and rolling-deploy events. Task 1 scaled the stateless services to 2 replicas, added PodDisruptionBudgets, topology spread constraints, and proved PDB enforcement via the eviction API. Task 2 added graceful shutdown (preStop + readinessProbe) and ran a zero-downtime `CREATE INDEX CONCURRENTLY` migration under live load. The Bonus renamed `events.event_date` → `events.scheduled_at` using the expand-and-contract pattern — 3 migrations + 2 code deploys with zero 5xx delta.

---

## Task 1 — Multi-Replica Failover + PDBs (4 pts)

### 12.1: Scale services to 2 replicas

Edited `k8s/events.yaml`, `k8s/payments.yaml`, `k8s/notifications.yaml`: `replicas: 1` → `2`.

```
kubectl get deploy notifications
NAME            READY   UP-TO-DATE   AVAILABLE   AGE
notifications   2/2     2            2           17d

kubectl get deploy -l 'app in (events,payments,notifications)'
NAME       READY   UP-TO-DATE   AVAILABLE   AGE
events     2/2     2            2           17d
payments   2/2     2            2           17d
```

All services at target counts: **gateway 5, events 2, payments 2, notifications 2**.

### 12.2: Failover test — kill pods under mixedload

Mixedload deployed (`labs/lab8/mixedload.yaml`). Before/after Prometheus query:

```
=== 5xx BEFORE pod kill ===
{"result":[{"metric":{},"value":[...,"0"]}]}

=== 5xx AFTER pod kill ===
{"result":[{"metric":{},"value":[...,"0"]}]}
```

**Result:** 0 → 0. Replacement pods came up within ~7 seconds; Service endpoints rerouted traffic to surviving replicas during the gap. No errors recorded.

### 12.3: PodDisruptionBudgets

`k8s/pdb.yaml` — 4 PDBs written and applied:

| PDB | Strategy | Value | Replicas | ALLOWED DISRUPTIONS |
|---|---|---|---|---|
| `gateway-pdb` | minAvailable | 2 | 5 | 3 |
| `events-pdb` | minAvailable | 1 | 2 | 1 |
| `payments-pdb` | minAvailable | 1 | 2 | 1 |
| `notifications-pdb` | maxUnavailable | 1 | 2 | 1 |

```
kubectl get pdb
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
gateway-pdb         2               N/A               3
events-pdb          1               N/A               1
payments-pdb        1               N/A               1
notifications-pdb   N/A             1                 1
```

### 12.4: Topology spread — gateway Rollout

Added `topologySpreadConstraints` to `spec.template.spec` in `k8s/gateway.yaml`:

```yaml
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        app: gateway
```

Verified in live spec:

```
kubectl get rollout gateway -o jsonpath='{.spec.template.spec.topologySpreadConstraints}' | python3 -m json.tool
[
    {
        "maxSkew": 1,
        "topologyKey": "kubernetes.io/hostname",
        "whenUnsatisfiable": "ScheduleAnyway",
        "labelSelector": { "matchLabels": { "app": "gateway" } }
    }
]
```

**Placement (single-node k3d):**

```
kubectl get pod -l app=gateway -o wide
NAME                     IP            NODE
gateway-69d77476-267np   10.42.0.216   k3d-quickticket-server-0
gateway-69d77476-gp2jq   10.42.0.219   k3d-quickticket-server-0
gateway-69d77476-vcgkx   10.42.0.218   k3d-quickticket-server-0
gateway-69d77476-vdt59   10.42.0.217   k3d-quickticket-server-0
gateway-69d77476-whkgp   10.42.0.220   k3d-quickticket-server-0
```

All 5 on the same node — expected. The YAML is correct and would produce a 2/2/1 spread on a 3-node cluster.

### 12.5: Drain dry-run + PDB eviction proof

**Drain dry-run:** `kubectl drain --dry-run=server` shows all QuickTicket pods as eviction candidates. That's NOT a PDB failure — drain serializes evictions and checks each pod against its PDB one at a time. On a single-node cluster dry-run passes each individually; a real drain would hang because there's nowhere to reschedule.

**PDB rejection via eviction API:**

Tightened `events-pdb` to `minAvailable: 2` (zero tolerance with 2 replicas):

```
kubectl get pdb events-pdb
NAME         MIN AVAILABLE   ALLOWED DISRUPTIONS
events-pdb   2               0
```

Called the eviction API directly:

```json
POST /api/v1/namespaces/default/pods/<pod>/eviction
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

HTTP 429 with `reason: DisruptionBudget` proves PDB enforcement works at the API level. Restored `events-pdb` to `minAvailable: 1`.

### With 3 gateway replicas and `minAvailable: 1`, what's the maximum number of pods that can be evicted simultaneously? Why is your `gateway-pdb` set to `minAvailable: 2` with 5 replicas?

2 pods can be evicted simultaneously (3 − 1 = 2 must remain). With 5 replicas, `minAvailable: 2` allows evicting up to 3 pods at once — enough for a node drain. Setting `minAvailable: 4` would block any eviction because the drain would always need 4 healthy pods on a 5-replica set.

### Your topology-spread constraint has no observable effect on single-node k3d. In a 3-node cluster, what placement would `maxSkew: 1` produce for 5 gateway pods? What about for 7?

5 pods = 2/2/1 (two nodes get 2, one node gets 1). 7 pods → 3/2/2. The scheduler distributes pods so that the difference between the most-loaded and least-loaded node never exceeds 1.

---

## Task 2 — Graceful Shutdown + Zero-Downtime Migration (4 pts)

### 12.6: preStop + readinessProbe

Added to `k8s/gateway.yaml`:

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

**Rolling restart under mixedload:**

```
=== 5xx BEFORE restart ===
{"result":[]}

=== 5xx AFTER restart ===
{"result":[]}
```

Both queries returned 0. The preStop sleep gives kube-proxy time to remove the pod from endpoints before SIGTERM reaches uvicorn. The fast-readiness probe (period=2s, threshold=1) ensures new pods join the service within ~2s.

### 12.7: `CREATE INDEX CONCURRENTLY` migration

Migration code:

```python
def upgrade():
    with op.get_context().autocommit_block():
        op.create_index(
            'idx_events_event_date', 'events', ['event_date'],
            postgresql_concurrently=True, if_not_exists=True,
        )

def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            'idx_events_event_date', table_name='events',
            postgresql_concurrently=True, if_exists=True,
        )
```

The `autocommit_block` wrapper is mandatory — PostgreSQL rejects `CREATE INDEX CONCURRENTLY` inside a transaction block.

**Before/after 5xx:**

```
=== 5xx BEFORE migration ===
{"result":[]}
=== 5xx AFTER migration ===
{"result":[]}
diff /tmp/5xx.before /tmp/5xx.after
(empty)
```

**Index verified:**

```
\d events
    "idx_events_event_date" btree (event_date)
```

### Why does `CREATE INDEX CONCURRENTLY` matter? What happens if you omit it on a table with 10M rows?

Without `CONCURRENTLY`, PostgreSQL takes an `ACCESS EXCLUSIVE` lock for the entire index build duration — on a 10M-row table that's minutes of blocking every query (SELECT, INSERT, UPDATE, DELETE). With `CONCURRENTLY`, the lock is `SHARE UPDATE EXCLUSIVE` — reads and writes continue uninterrupted. The trade-off is longer build time and more resource consumption, but on a production table there is no acceptable alternative.

### 12.8: Expand-and-contract sketch (design)

To rename `events.event_date` → `events.scheduled_at` with zero downtime:

1. **M1** — `ALTER TABLE events ADD COLUMN scheduled_at TIMESTAMPTZ NULL` (instant, no lock on existing rows)
2. **Deploy A** — Code reads via `COALESCE(scheduled_at, event_date) AS event_date`; writes to both columns
3. **M2** — `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL`; then `ALTER COLUMN scheduled_at SET NOT NULL`
4. **Deploy B** — Code reads/writes only `scheduled_at`; update `seed.sql` schema
5. **M3** — `ALTER TABLE events DROP COLUMN event_date`

### In your expand-and-contract sketch, why MUST migration 3 (drop old column) come after deploy B has fully rolled out? What goes wrong if it runs before?

If M3 (`DROP event_date`) runs while any Deploy A pod is still serving traffic, that pod's `COALESCE(scheduled_at, event_date)` references a column that no longer exists — every request to `/events` returns a 500 error. Deploy B reads only `scheduled_at`, so once it is fully rolled out, dropping `event_date` is safe. The constraint is: never remove a column that a still-running code version references.

---

## Bonus Task — Execute Expand-and-Contract Rename (2 pts)

All 5 transitions executed under live mixedload traffic. Every intermediate state was verified with Prometheus — **zero 5xx across all 5 steps**.

### M1: Add `scheduled_at` column

```python
def upgrade():
    op.add_column('events', sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True), nullable=True))
```

Nullability is key — a `NOT NULL` column with no default would fail on existing rows.

### Deploy A: Dual-write, fallback-read

```
# Before:                   # After (Deploy A):
e.event_date                COALESCE(e.scheduled_at, e.event_date) AS event_date
ORDER BY e.event_date       ORDER BY COALESCE(e.scheduled_at, e.event_date)
```

The `AS event_date` alias keeps the response shape backward-compatible.

### M2: Backfill + NOT NULL

```python
def upgrade():
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column('events', 'scheduled_at', nullable=False)
```

**Backfill verified — every row populated:**

```
 id |       event_date       |      scheduled_at
----+------------------------+------------------------
  1 | 2026-09-15 09:00:00+00 | 2026-09-15 09:00:00+00
  2 | 2026-10-01 18:00:00+00 | 2026-10-01 18:00:00+00
  3 | 2026-11-20 10:00:00+00 | 2026-11-20 10:00:00+00
  4 | 2026-09-22 14:00:00+00 | 2026-09-22 14:00:00+00
  5 | 2026-10-10 10:00:00+00 | 2026-10-10 10:00:00+00
```

### Deploy B: Switch to `scheduled_at` only

```
# Deploy A:                              # Deploy B:
COALESCE(e.scheduled_at, e.event_date)   e.scheduled_at AS event_date
ORDER BY COALESCE(...)                   ORDER BY e.scheduled_at
```

`app/seed.sql` updated: `event_date` → `scheduled_at` in both CREATE TABLE and INSERT statements.

### M3: Drop `event_date`

```python
def upgrade():
    op.drop_column('events', 'event_date')
```

### Final schema verification

```
\d events
    Column     |           Type           | Nullable
---------------+--------------------------+----------
 id            | integer                  | not null
 name          | text                     | not null
 venue         | text                     | not null
 total_tickets | integer                  | not null
 price_cents   | integer                  | not null
 email         | varchar(255)             |
 scheduled_at  | timestamp with time zone | not null
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
```

`event_date` is gone, `scheduled_at` is `NOT NULL`.

### Zero 5xx delta

```
diff /tmp/5xx.baseline /tmp/5xx.final
(empty — identical)
```

**5xx baseline:** `{"result":[]}` before M1.
**5xx final:** `{"result":[]}` after M3.
**Delta: 0.**

### Which single step would have caused 5xx if you'd reordered it earlier?

M2 (backfill + `ALTER COLUMN scheduled_at SET NOT NULL`) before Deploy A. If `scheduled_at` is `NOT NULL` before the code writes to it, any INSERT that doesn't specify `scheduled_at` fails — including the seed.sql bootstrap. The backfill must come after the code has started writing to the new column.

### Write the batching pattern for backfill on a 10M-row table.

A batched `UPDATE` loop keeps each transaction small and avoids long-running locks:
```sql
DO $$
DECLARE batch_size CONSTANT INT := 1000;
BEGIN
  LOOP
    UPDATE events SET scheduled_at = event_date
    WHERE scheduled_at IS NULL
    AND ctid IN (SELECT ctid FROM events WHERE scheduled_at IS NULL LIMIT batch_size);
    EXIT WHEN NOT FOUND;
    COMMIT;
    PERFORM pg_sleep(0.1);
  END LOOP;
END $$;
```

### Why isn't the downgrade from migration 3 sufficient for true rollback safety once Deploy B is live?

The downgrade re-adds `event_date` and backfills it, but Deploy B code reads only `scheduled_at` — it never queries `event_date`. The data is there but invisible to the running code. A true rollback needs a forward plan (add column → dual-write → switch read → drop old), not a single `git revert` of migration 3. Deploy B first needs to be reverted to Deploy A before M3 can be safely undone.

---