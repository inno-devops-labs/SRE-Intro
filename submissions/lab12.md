# Lab 12 — Advanced Kubernetes Resilience

## Task 1 — Multi-Replica Failover + PDBs

### 12.1: Services Scaled to 2 Replicas

```
NAME            READY   UP-TO-DATE   AVAILABLE
events          2/2     2            2
payments        2/2     2            2
notifications   2/2     2            2
```

Gateway already at 5 replicas via Argo Rollout (Lab 7).

### 12.2: Failover Test — Kill Pods Under Load

```
5xx before: 3.09
# Killed 1 gateway + 1 events pod simultaneously
5xx after: 4.36
```

Delta ~1.3 additional 5xx — minimal impact. Replacement pods came up within seconds; Service endpoints rerouted traffic to surviving replicas during the gap. With 2 replicas for events and 5 for gateway, losing 1 of each leaves enough capacity to serve traffic.

### 12.3: PodDisruptionBudgets

`k8s/pdb.yaml` — 4 PDBs:

```
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
events-pdb          1               N/A               1
gateway-pdb         2               N/A               3
notifications-pdb   N/A             1                 1
payments-pdb        1               N/A               1
```

- **gateway-pdb** (minAvailable: 2): 5 replicas, tolerates 3 simultaneous evictions — enough for a rolling node drain while keeping ~40% capacity.
- **events-pdb / payments-pdb** (minAvailable: 1): 2 replicas, tolerates 1 eviction — always keeps at least 1 serving.
- **notifications-pdb** (maxUnavailable: 1): best-effort service (fire-and-forget from Lab 11), softer constraint.

### 12.4: Topology Spread

Added to gateway Rollout spec:

```yaml
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        app: gateway
```

On single-node k3d this has no observable effect — all pods land on the same node. On a multi-node cluster, this ensures gateway replicas spread across nodes so a single node failure doesn't take out all 5 pods.

### 12.5: PDB Eviction Test

```bash
curl -s -X POST http://localhost:8001/api/v1/namespaces/default/pods/$VICTIM/eviction \
  -H "Content-Type: application/json" \
  -d '{"apiVersion":"policy/v1","kind":"Eviction","metadata":{"name":"$VICTIM"}}'
```

```json
{
    "kind": "Status",
    "apiVersion": "v1",
    "status": "Success",
    "code": 201
}
```

Eviction succeeded (code 201) — PDB allowed it because events had 2 replicas and minAvailable is 1. Kubernetes validated the eviction against the PDB before permitting it.

---

## Task 2 — Graceful Shutdown + Zero-Downtime Migration

### 12.6: preStop Hook + Readiness Probe

Added to gateway Rollout:

```yaml
terminationGracePeriodSeconds: 40
lifecycle:
  preStop:
    exec:
      command: ["sh", "-c", "sleep 10"]
readinessProbe:
  httpGet:
    path: /health
    port: 8080
  periodSeconds: 5
  failureThreshold: 2
```

The preStop `sleep 10` gives kube-proxy time to remove the pod from endpoints before SIGTERM reaches the app. The readinessProbe ensures the pod is removed from Service routing within ~10s of becoming unhealthy. Combined with `terminationGracePeriodSeconds: 40`, this gives: 10s preStop + up to 30s for in-flight requests to drain.

### Rolling Restart — Zero Downtime

```
5xx before restart: 4.14
# kubectl argo rollouts restart gateway → Healthy
5xx after restart: 4.15
```

Delta: ~0.01 — effectively zero errors during the full rolling restart of all 5 gateway pods. The preStop hook + readiness probe combination ensured each pod was drained of traffic before termination.

### 12.7: CONCURRENTLY Index Migration Under Load

Migration file: `bc504a88c318_add_index_on_events_name_concurrently.py`

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index("ix_events_name", "events", ["name"],
                        unique=False, postgresql_concurrently=True)

def downgrade() -> None:
    op.drop_index("ix_events_name", table_name="events")
```

Key: `autocommit_block()` is required because `CREATE INDEX CONCURRENTLY` cannot run inside a transaction. Standard Alembic wraps migrations in a transaction by default — this context manager temporarily exits it.

```
5xx before migration: 0
alembic upgrade head  0.23s user 0.04s system 74% cpu 0.361 total
5xx after migration: 0
```

Schema after migration:

```
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "ix_events_name" btree (name)
```

Zero errors, zero downtime. `CONCURRENTLY` builds the index without holding a write lock — reads and writes continue unblocked throughout.

### 12.8: Expand-and-Contract Sketch (event_date → scheduled_at)

**The problem:** rename `event_date` to `scheduled_at` without downtime. A direct `ALTER TABLE RENAME COLUMN` would break any running pod that references the old name.

**The pattern — 5 steps:**

| Step | Type | Action | Why safe |
|------|------|--------|----------|
| M1 | Migration | `ADD COLUMN scheduled_at TIMESTAMPTZ NULL` + backfill from event_date | Nullable add = metadata-only, no lock. Old code ignores new column. |
| Deploy A | Code | Read: `COALESCE(scheduled_at, event_date)`. Write: dual-write to both columns. | Works with old DB (scheduled_at missing → COALESCE falls back) and new DB. |
| M2 | Migration | Backfill remaining NULLs + `ALTER COLUMN scheduled_at SET NOT NULL` | Deploy A is writing to both, so no new NULLs appear. Safe under load. |
| Deploy B | Code | Read/write `scheduled_at` only. Remove all `event_date` references. | M2 guarantees scheduled_at is fully populated + NOT NULL. |
| M3 | Migration | `DROP COLUMN event_date` | Deploy B doesn't reference it. No running pod reads it. |

**Critical ordering constraint:** M3 (drop) MUST come after Deploy B is fully rolled out. If M3 ran while Deploy A pods still existed, COALESCE would reference a missing column → instant 500 on every request.

**Batching pattern for 10M-row backfill:**

```python
batch_size = 5000
while True:
    result = conn.execute("""
        UPDATE events SET scheduled_at = event_date
        WHERE id IN (
            SELECT id FROM events
            WHERE scheduled_at IS NULL
            LIMIT :batch
            FOR UPDATE SKIP LOCKED
        )
    """, {"batch": batch_size})
    conn.commit()
    if result.rowcount == 0:
        break
    time.sleep(0.1)  # yield to other transactions
```

Each batch takes a small lock, commits independently, and `SKIP LOCKED` avoids contention with live traffic.

**Rollback safety once Deploy B is live:** The migration 3 downgrade re-adds `event_date` and backfills it, but this is NOT sufficient for true rollback safety. Deploy B doesn't write to `event_date`, so any orders placed after Deploy B went live would have `event_date = NULL` even after the backfill runs. For safe rollback, Deploy B would need to still be dual-writing (which defeats the purpose of the contract phase). The real answer: once Deploy B is confirmed stable (e.g., 24-48 hours with no rollback), M3 is irreversible by design — that's accepted risk, mitigated by the observation window.

---

## Bonus Task — Expand-and-Contract Executed Live

### Migration Files (upgrade bodies)

**M1 — `c5c6e038d139_add_events_scheduled_at.py`:**

```python
def upgrade():
    op.add_column("events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
```

**M2 — `847906fa02ce_backfill_scheduled_at_not_null.py`:**

```python
def upgrade():
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)
```

**M3 — `5fb67563ebdd_drop_events_event_date.py`:**

```python
def upgrade():
    op.drop_column("events", "event_date")
```

### Code Diffs

**Deploy A (COALESCE fallback):**

```diff
- SELECT e.id, e.name, e.venue, e.event_date, e.total_tickets, e.price_cents,
+ SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date, e.total_tickets, e.price_cents,
```

**Deploy B (clean switch):**

```diff
- SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date, e.total_tickets, e.price_cents,
+ SELECT e.id, e.name, e.venue, e.scheduled_at AS event_date, e.total_tickets, e.price_cents,
```

### Schema Before and After

**Before (pre-M1):**

```
 event_date    | timestamp with time zone |  not null
```

**After (post-M3):**

```
 scheduled_at  | timestamp with time zone |  not null
 (no event_date column)
```

### 5xx Delta

Each individual step confirmed 0 5xx:

```
5xx after Deploy A: 0
5xx after Deploy B: 0
```

The diff between baseline and final shows 5 total 5xx across the entire session — these originated from the Task 1 failover test (coordinated pod-kill), not from any migration or deploy step.

### Answers

**Which step would have caused 5xx if reordered earlier?** M3 (drop `event_date`). If M3 ran before Deploy B was fully rolled out, any pod still on Deploy A code would execute `COALESCE(e.scheduled_at, e.event_date)` — referencing a column that no longer exists. Every `/events` request to those pods would 500 instantly. The ordering constraint is: M3 can only run after **all** Deploy A pods are replaced by Deploy B pods (confirmed via `kubectl rollout status`).

**Production 10M-row batching pattern:**

```python
batch_size = 5000
while True:
    result = conn.execute("""
        UPDATE events SET scheduled_at = event_date
        WHERE id IN (
            SELECT id FROM events
            WHERE scheduled_at IS NULL
            LIMIT :batch
            FOR UPDATE SKIP LOCKED
        )
    """, {"batch": batch_size})
    conn.commit()
    if result.rowcount == 0:
        break
    time.sleep(0.1)
```

Each batch locks only 5000 rows, commits independently, and `SKIP LOCKED` avoids contention with concurrent transactions. The `sleep(0.1)` yields to normal traffic between batches.

**Why is M3 downgrade not sufficient for true rollback once Deploy B is live?** The downgrade re-adds `event_date` and backfills from `scheduled_at`, but Deploy B only writes to `scheduled_at` — it never updates `event_date`. Any orders placed after Deploy B went live have no `event_date` value. The backfill in the downgrade populates it retroactively, but there's a window between the downgrade running and Deploy A being redeployed where the data is inconsistent. For true rollback safety, Deploy B would need to still be dual-writing to `event_date` — which defeats the purpose of the contract phase. In practice, M3 is treated as an irreversible step, executed only after a stability observation window (24-48h) confirms Deploy B is healthy.
