# Lab 12 — Advanced Kubernetes Resilience — Submission

**Student:** jakefish18
**Repo:** https://github.com/jakefish18/SRE-Intro · **Branch:** `feature/lab12`

PR checklist:
```text
- [x] Task 1 done — multi-replica failover + 4 PDBs + topology spread + real eviction-API block
- [x] Task 2 done — preStop + zero-error rolling restart + CONCURRENTLY migration + expand-and-contract sketch
- [x] Bonus Task done — expand-and-contract executed live (3 migrations + 2 deploys, zero 5xx, event_date dropped)
```

---

## Task 1 — Multi-Replica Failover + PDBs

### 1. All services at target replica counts
```
events          2/2      payments        2/2      notifications   2/2
gateway (Rollout) 5/5 Healthy
```

### 2. Pod-kill failover under mixedload (5xx before/after)
Killed one gateway pod **and** one events pod simultaneously under live traffic:
```
5xx before (increase 3m): 0.0
killed gateway-d484b5c48-2vxbt + events-546c988467-d8pfc
5xx after  (increase 3m): 0.0
```
**Zero 5xx** — the Service rerouted to surviving replicas while replacements came up
(each Ready within a few seconds).

### 3. `kubectl get pdb`
```
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
gateway-pdb         2               N/A               3
events-pdb          1               N/A               1
payments-pdb        1               N/A               1
notifications-pdb   N/A             1                 1
```

### 4. Topology spread — live in spec + actual placement
```
$ kubectl get rollout gateway -o jsonpath='{.spec.template.spec.topologySpreadConstraints}'
[{"labelSelector":{"matchLabels":{"app":"gateway"}},"maxSkew":1,
  "topologyKey":"kubernetes.io/hostname","whenUnsatisfiable":"ScheduleAnyway"}]

$ kubectl get pod -l app=gateway -o wide
  all 5 pods on NODE k3d-quickticket-server-0   ← single-node k3d: no observable spread (expected)
```
The constraint is correct and live; on a multi-node cluster it would balance placement.

### 5. PDB enforcement — real eviction-API rejection
Tightened `events-pdb` to `minAvailable: 2` (2 replicas → **0** allowed disruptions), then
POSTed one eviction to `/api/v1/namespaces/default/pods/<pod>/eviction`:
```
HTTP 429
"status": "Failure",
"reason": "TooManyRequests",
"message": "Cannot evict pod as it would violate the pod's disruption budget.",
"details.causes": { "reason": "DisruptionBudget",
                    "message": "The disruption budget events-pdb needs 2 healthy pods and has 2 currently" }
"code": 429
```

### 6. PDB math
**3 gateway replicas + `minAvailable: 1` → at most `3 − 1 = 2` pods can be evicted
simultaneously** (one must always stay). My `gateway-pdb` uses `minAvailable: 2` with 5
replicas so it tolerates **3** simultaneous evictions — enough to drain a node while
keeping ~half of normal RPS serving. Not `minAvailable: 4`, because that leaves only 1
tolerable eviction and a node drain would **block forever** (can't reschedule enough pods
at once) — the PDB has to permit real maintenance, not just protect availability.

### 7. Topology spread placement (multi-node)
With `maxSkew: 1` the most- and least-loaded node differ by at most 1 gateway pod:
- **5 pods over 3 nodes → 2 / 2 / 1** (never 4/1/0 or 5/0/0).
- **7 pods over 3 nodes → 3 / 2 / 2.**

---

## Task 2 — Graceful Shutdown + Zero-Downtime Migration

### preStop + readinessProbe (as in `k8s/gateway.yaml`)
```yaml
      terminationGracePeriodSeconds: 40      # covers 10s preStop + up to 30s drain
      containers:
        - name: gateway
          readinessProbe:
            httpGet: { path: /health, port: 8080 }
            periodSeconds: 2
            failureThreshold: 1
          lifecycle:
            preStop:
              exec: { command: ["sh", "-c", "sleep 10"] }
```
> **Note on the Lab 8 tension:** Lab 8 switched readiness to a shallow `tcpSocket` to
> avoid a Redis outage cascading all pods to NotReady. Lab 12 uses `httpGet /health`
> because it flips NotReady fast on termination (kube-proxy drops the endpoint before
> uvicorn stops). These pull in opposite directions; the production reconciliation is a
> dedicated shallow `/livez` (process-up only) for *readiness* while keeping deep
> `/health` for dashboards. Here I follow the Lab 12 spec.

### Zero-5xx rolling restart under load
```
5xx before (increase 1m): 0.0
$ kubectl argo rollouts restart gateway     # (NOT kubectl rollout restart — it's a Rollout)
Healthy
5xx after  (increase 3m): 0.0
```

### `CREATE INDEX CONCURRENTLY` migration
```python
def upgrade() -> None:
    with op.get_context().autocommit_block():          # DDL runs OUTSIDE Alembic's txn
        op.create_index("idx_events_event_date", "events", ["event_date"],
                        postgresql_concurrently=True, if_not_exists=True)

def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("idx_events_event_date", table_name="events",
                      postgresql_concurrently=True, if_exists=True)
```
```
5xx before: 0    time alembic upgrade head → 0.245s    5xx after: 0    (delta 0.0)
\d events → Indexes: "idx_events_event_date" btree (event_date)
```

### 12.8 — Expand-and-contract sketch (rename `event_date` → `scheduled_at`)
3 migrations + 2 code deploys, interleaved so **both old and new code work at every step**:
1. **Migration 1 (expand):** `ADD COLUMN scheduled_at TIMESTAMPTZ NULL`. Nullable = instant, no rewrite/lock. Old code untouched.
2. **Deploy A (dual-write, fallback-read):** write BOTH columns; read `COALESCE(scheduled_at, event_date)`. Tolerates rows where `scheduled_at` is still NULL.
3. **Migration 2 (backfill):** `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL`, then `ALTER COLUMN scheduled_at SET NOT NULL`. Safe because Deploy A already reads via COALESCE; the `WHERE … IS NULL` makes it idempotent.
4. **Deploy B (switch):** read/write only `scheduled_at`. No pod references `event_date` anymore.
5. **Migration 3 (contract):** `DROP COLUMN event_date`. Safe **only now** — nothing reads or writes it.

### Answers
- **Why `CREATE INDEX CONCURRENTLY`?** A plain `CREATE INDEX` takes an **ACCESS EXCLUSIVE**
  lock for the whole build — on a 10M-row table that's **minutes** during which *every*
  read and write on `events` blocks: a full outage. `CONCURRENTLY` takes only a **SHARE
  UPDATE EXCLUSIVE** lock and builds the index in two passes, so reads and writes keep
  flowing. Omit it on 10M rows and you've turned a routine index add into a multi-minute
  stall. (At QuickTicket's 5-row scale both are instant — you learn the right syntax now
  so you don't learn it during an incident.)
- **Why must Migration 3 come after Deploy B is fully rolled out?** Dropping `event_date`
  while any Deploy-A pod is still live would break that pod: its `COALESCE(scheduled_at,
  event_date)` read references a column that no longer exists → **every `/events` request
  500s**. The column can only be dropped once no running code reads or writes it, i.e.
  after `kubectl rollout status` confirms Deploy B has replaced every Deploy-A pod.

---

## Bonus Task — Execute the Expand-and-Contract Rename (live)

Ran the full `event_date` → `scheduled_at` rename on the live cluster under mixedload —
3 Alembic migrations + 2 `events` code deploys, in order: **M1 → Deploy A → M2 → Deploy B → M3**.

### 1. The three migrations (upgrade bodies)
```python
# M1 — expand (add nullable column: instant, no rewrite/lock)
op.add_column('events', sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True), nullable=True))

# M2 — backfill, then NOT NULL (idempotent; Deploy A already reads via COALESCE)
op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
op.alter_column('events', 'scheduled_at', nullable=False)

# M3 — contract (drop old column: only safe once no code reads/writes it)
op.drop_column('events', 'event_date')
```

### 2. `app/events/main.py` diff — Deploy A → Deploy B
```diff
  # Deploy A (dual-write is a no-op — QuickTicket only inserts via seed.sql; reads fall back):
- SELECT e.id, e.name, e.venue, e.event_date, ...
+ SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date, ...
- GROUP BY e.id ORDER BY e.event_date
+ GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)

  # Deploy B (single-mode on scheduled_at; alias kept so the response shape is unchanged):
- SELECT ..., COALESCE(e.scheduled_at, e.event_date) AS event_date, ...
+ SELECT ..., e.scheduled_at AS event_date, ...
- ORDER BY COALESCE(e.scheduled_at, e.event_date)
+ ORDER BY e.scheduled_at
```
`app/seed.sql` was also updated (`event_date` → `scheduled_at` in the `CREATE TABLE` and
`INSERT`) so a freshly-recreated cluster bootstraps on the new schema.

### 3. `\d events` before M1 vs after M3
```
BEFORE:  event_date   timestamptz  not null        (+ idx_events_event_date)
AFTER:   scheduled_at timestamptz  not null        (event_date column GONE)
```

### 4. Zero 5xx across the whole sequence
```
5xx baseline (sum gateway_requests_total{status=~"5.."}): 0
  after M1        : 0        after Deploy A : 0
  after M2        : 0        after Deploy B : 0
  after M3 (final): 0
diff(baseline, final) = 0    → zero 5xx through all 5 transitions
```

### 5. Which single reordered step would have caused 5xx?

**Moving M3 (drop `event_date`) before Deploy B is fully rolled out.** Any pod still on
Deploy A reads `COALESCE(scheduled_at, event_date)` — dropping the column makes that
reference invalid, so **every `/events` request 500s** until the last Deploy-A pod is
gone. (Running Deploy B before M2's backfill is also unsafe — it would read a still-NULL
`scheduled_at` — but M3-before-Deploy-B is the irreversible one: a `DROP COLUMN` with live
readers.)

### 6. Batching the backfill on a 10M-row table
```
last_id = 0
while True:
    ids = execute("""UPDATE events SET scheduled_at = event_date
                     WHERE id > :last AND scheduled_at IS NULL
                     ORDER BY id LIMIT 10000 RETURNING id""", last=last_id)  # own txn
    if not ids: break
    last_id = max(ids)
    sleep(0.1)   # let other transactions through; avoid long locks + replication lag
```
Each batch is a small, short transaction (10k rows) that commits and releases its locks —
no multi-minute `ACCESS EXCLUSIVE`-style stall, no table bloat, replicas stay caught up.

### 7. Why the M3 downgrade isn't true rollback safety once Deploy B is in prod

The M3 downgrade re-adds `event_date` and backfills it from `scheduled_at` — but that only
repairs the **schema at one instant**. Once Deploy B has been serving traffic, it wrote
*only* `scheduled_at`; the re-added `event_date` is a one-shot snapshot, and any writes that
happened under Deploy B never touched `event_date`. Rolling the **code** back to Deploy A
(which reads/writes `event_date`) would then serve/observe stale data for those rows. True
rollback safety requires that no data was ever written that the old code can't reconstruct —
i.e. you must keep **dual-writing both columns** (Deploy A behaviour) until you are certain
you will *not* roll back. The moment you drop `event_date` and stop writing it, forward is
the only safe direction; the downgrade is a schema-repair, not a data-integrity guarantee.
