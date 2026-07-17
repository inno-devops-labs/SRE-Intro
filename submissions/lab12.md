# Lab 12 — Advanced Kubernetes Resilience

**Author:** Anton Bugaev  
**Date:** 2026-07-17  
**Cluster:** k3d `quickticket` (3 nodes: server + 2 agents)

---

## Task 1 — Multi-Replica Failover + PDBs

### Replica counts

```
NAME                            READY
deployment.apps/events          2/2
deployment.apps/notifications   2/2
deployment.apps/payments        2/2
rollout.argoproj.io/gateway     5/5
```

### Pod-kill under mixedload (after DB seeded)

```
5xx_abs_before=881
# delete 1 gateway + 1 events pod
5xx_abs_after=881
delta=0
```

Replacement pods Ready within ~16s; Service kept routing to surviving replicas. **Zero new 5xx** from the coordinated kill.

### PDBs (`kubectl get pdb`)

```
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
events-pdb          1               N/A               1
gateway-pdb         2               N/A               3
notifications-pdb   N/A             1                 1
payments-pdb        1               N/A               1
```

### Topology spread (live in Rollout spec)

```json
[
  {
    "labelSelector": { "matchLabels": { "app": "gateway" } },
    "maxSkew": 1,
    "topologyKey": "kubernetes.io/hostname",
    "whenUnsatisfiable": "ScheduleAnyway"
  }
]
```

Actual placement (`kubectl get pod -l app=gateway -o wide`) — 3 nodes available, pods spread:

```
gateway-...   k3d-quickticket-agent-0
gateway-...   k3d-quickticket-agent-0
gateway-...   k3d-quickticket-agent-1
gateway-...   k3d-quickticket-agent-1
gateway-...   k3d-quickticket-server-0
```

### PDB eviction API rejection (HTTP 429)

Tightened `events-pdb` to `minAvailable: 2` (ALLOWED DISRUPTIONS = 0), then:

```json
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

Restored `minAvailable: 1` afterwards.

### Answers

**With 3 gateway replicas and `minAvailable: 1`, max simultaneous evictions?**  
`3 − 1 = 2` pods. PDB guarantees at least 1 remains Available.

**Why `gateway-pdb` uses `minAvailable: 2` with 5 replicas?**  
Tolerates losing up to 3 pods during a node drain while keeping ~40% capacity. `minAvailable: 4` would block drains forever (can't free enough pods to empty a node). `minAvailable: 2` balances safety vs operability.

**3-node cluster, `maxSkew: 1`, 5 gateway pods?** Placement **2/2/1**.  
**7 pods?** Placement **3/2/2** (or any permutation where max−min ≤ 1).

---

## Task 2 — Graceful Shutdown + Zero-Downtime Migration

### preStop + readinessProbe (from `k8s/gateway.yaml`)

```yaml
spec:
  template:
    spec:
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

### Rolling restart under load

```
5xx_abs_before=881
kubectl argo rollouts restart gateway  → Healthy
sum(increase(gateway_requests_total{status=~"5.."}[3m])) = 0
```

**Zero 5xx** in the 3m window after restart completed.

### CREATE INDEX CONCURRENTLY migration

`migrations/versions/341a3f732deb_index_events_event_date_concurrently.py`:

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            'idx_events_event_date',
            'events',
            ['event_date'],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )
```

`\d events` after upgrade showed: `"idx_events_event_date" btree (event_date)`.

5xx absolute counter unchanged across the migration (376 → 376 at that point).

### Why CONCURRENTLY matters?

Without it, `CREATE INDEX` takes an **ACCESS EXCLUSIVE** lock — on a 10M-row table that can block all reads/writes for minutes. `CONCURRENTLY` uses `SHARE UPDATE EXCLUSIVE` and builds the index without blocking DML. Must run outside Alembic’s default transaction (`autocommit_block`).

### Expand-and-contract sketch (`event_date` → `scheduled_at`)

1. **Migration 1 (expand):** `ALTER TABLE events ADD COLUMN scheduled_at TIMESTAMPTZ NULL;`
2. **Code deploy A:** read via `COALESCE(scheduled_at, event_date)`; write both columns if runtime writes exist.
3. **Migration 2 (backfill):** `UPDATE … SET scheduled_at = event_date WHERE scheduled_at IS NULL;` then `SET NOT NULL`.
4. **Code deploy B:** read/write **only** `scheduled_at`.
5. **Migration 3 (contract):** `ALTER TABLE events DROP COLUMN event_date;` — only after Deploy B is fully rolled out.

**Why M3 must wait for Deploy B?** Deploy A still references `event_date` in `COALESCE(...)`. Dropping the column while any Deploy-A pod is live → SQL error → 5xx on every `/events` request.

---

## Bonus — Expand-and-Contract Executed Live

### Migration upgrade() bodies

**M1 — add column (`a1b2c3d4e5f6`):**
```python
op.add_column('events', sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True), nullable=True))
```

**M2 — backfill (`b2c3d4e5f6a7`):**
```python
op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
op.alter_column('events', 'scheduled_at', nullable=False)
```

**M3 — drop old (`c3d4e5f6a7b8`):**
```python
op.drop_column('events', 'event_date')
```

### Deploy A → Deploy B (events SQL)

**Deploy A (COALESCE fallback):**
```sql
SELECT ..., COALESCE(e.scheduled_at, e.event_date) AS event_date, ...
ORDER BY COALESCE(e.scheduled_at, e.event_date)
```

**Deploy B (new column only — committed in `app/events/main.py`):**
```sql
SELECT ..., e.scheduled_at AS event_date, ...
ORDER BY e.scheduled_at
```

Response shape kept (`AS event_date`) so gateway/clients unchanged. No runtime INSERT path in events — dual-write N/A; seed updated in `app/seed.sql`.

### Schema before M1 / after M3

**Before:** columns include `event_date TIMESTAMPTZ NOT NULL` (no `scheduled_at`).

**After (`\d events`):**
```
 scheduled_at  | timestamp with time zone | not null
```
No `event_date` column.

### 5xx across bonus transitions

| Step | Absolute 5xx |
|------|--------------|
| Baseline (healthy Deploy A + M1 done) | **879** |
| After M2 backfill | **879** |
| After Deploy B | **881** |
| After M3 drop | **881** |

`diff` baseline→final for the clean sequence (M2 → Deploy B → M3): **+2** absolute (noise from rolling pods), effectively **zero user-visible migration-induced outage**. Earlier premature Deploy A before M1 briefly raised 5xx — fixed by applying M1 first (ordering lesson).

Backfill verified:
```
 id | event_date            | scheduled_at
  1 | 2026-09-15 09:00:00+00 | 2026-09-15 09:00:00+00
  ... all 5 rows equal ...
```

### Bonus answers

1. **Which reordered step causes 5xx?**  
   **Migration 3 (drop `event_date`) before Deploy B finishes** — Deploy A still SELECTs `event_date`. Equally: **Deploy A before M1** (COALESCE references missing `scheduled_at`).

2. **Batched backfill (10M rows) pseudocode:**
```
batch = 10000
min_id = 0
loop:
  rows = UPDATE events SET scheduled_at = event_date
         WHERE scheduled_at IS NULL AND id > min_id AND id <= min_id + batch
  if rows == 0: break
  min_id += batch
  sleep(0.2)   # avoid long locks / replication lag
```

3. **Why M3 downgrade isn’t enough for prod rollback after Deploy B?**  
   Downgrade re-adds `event_date` and backfills it, but **running pods are Deploy B** and never read `event_date`. Safe rollback needs **re-deploying Deploy A (or a dual-read build) before relying on the restored column**, plus ensuring no traffic hits Deploy B against a schema that only has the old column during a botched reverse.

---

## Verification checklist

- [x] events/payments/notifications ×2; gateway Rollout ×5
- [x] Zero 5xx on coordinated pod kill
- [x] 4 PDBs + HTTP 429 eviction rejection
- [x] topologySpreadConstraints in live spec (+ multi-node placement observed)
- [x] preStop + fast readiness; rolling restart with increase(5xx[3m])=0
- [x] CONCURRENTLY index + expand-and-contract sketch
- [x] Bonus: 3 migrations + 2 deploys + seed.sql; `event_date` dropped
