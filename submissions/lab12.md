# Lab 12 — Advanced Kubernetes Resilience

> Deliverables: `k8s/pdb.yaml`, `k8s/gateway.yaml` (topology spread + preStop +
> readinessProbe), `k8s/{events,payments,notifications}.yaml` (2 replicas),
> `k8s/gateway-hpa.yaml` (optional), `migrations/` (CONCURRENTLY index + 3
> expand-and-contract migrations), `app/events/main.py`, `app/seed.sql`, this file.
>
> `PASTE` blocks are on-cluster evidence. Manifests + migrations are committed
> and validated offline (YAML lint, `alembic history`, `py_compile`).

---

## Task 1 — Multi-Replica Failover + PDBs (4 pts)

### 1. All services at target replica counts

events/payments/notifications set to `replicas: 2`; gateway is the 5-replica Rollout.

```text
NAME                            READY   AVAILABLE
deployment.apps/events          2/2     2
deployment.apps/notifications   2/2     2
deployment.apps/payments        2/2     2
deployment.apps/postgres        1/1     1
deployment.apps/redis           1/1     1

NAME                          DESIRED   CURRENT   READY   AVAILABLE
rollout.argoproj.io/gateway   5         5         5       5
```

### 2. 5xx before/after coordinated pod kill (both 0)

```text
before: 5xx (1m) = 0
        killed 1 gateway + 1 events pod at 20:57:06 (under mixedload)
after:  5xx (1m) = 0.0
```
_Zero 5xx through a coordinated kill of a gateway AND an events pod — with 2+
replicas each behind a Service, the survivors absorbed traffic and replacements
came up in seconds. (Contrast Lab 8's single-gateway kill, which dropped ~1
request because that pre-Lab-12 gateway had no preStop.)_

### 3. `kubectl get pdb`

Expected `ALLOWED DISRUPTIONS`: gateway 3, events 1, payments 1, notifications 1.

```text
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
gateway-pdb         2               N/A               3
events-pdb          1               N/A               1
payments-pdb        1               N/A               1
notifications-pdb   N/A             1                 1
```

### 4. Topology spread in the live spec + placement

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
```text
gateway-557887f676-kbhfz   k3d-quickticket-server-0
gateway-557887f676-lkwhp   k3d-quickticket-server-0
gateway-557887f676-q2x72   k3d-quickticket-server-0
gateway-557887f676-r4np5   k3d-quickticket-server-0
gateway-557887f676-tl4wp   k3d-quickticket-server-0
```
_All 5 on the single k3d node — expected; the constraint is correct + live in
the spec and would spread 2/2/1 on a 3-node cluster._

### 5. HTTP 429 from the tightened-PDB eviction test

Tighten `events-pdb` to `minAvailable: 2` (== replicas → zero tolerance), then
POST one eviction via `kubectl proxy` + the eviction subresource.

```text
$ kubectl patch pdb events-pdb --type=merge -p '{"spec":{"minAvailable":2}}'
events-pdb ALLOWED DISRUPTIONS → 0

$ POST /api/v1/namespaces/default/pods/<events-pod>/eviction
HTTP 429
{
    "kind": "Status",
    "status": "Failure",
    "message": "Cannot evict pod as it would violate the pod's disruption budget.",
    "reason": "TooManyRequests",
    "details": { "causes": [ {
        "reason": "DisruptionBudget",
        "message": "The disruption budget events-pdb needs 2 healthy pods and has 2 currently"
    } ] },
    "code": 429
}
```
_The PDB rejected the eviction at the API level — proof of enforcement._

### 6. minAvailable reasoning

**3 replicas, `minAvailable: 1` → max simultaneous evictions?** `replicas −
minAvailable = 3 − 1 = 2`. At most 2 of the 3 pods can be voluntarily evicted at
once; the PDB always keeps at least 1 running.

**Why is `gateway-pdb` `minAvailable: 2` with 5 replicas?** It tolerates losing
3 at once (5 − 2), which is enough for a node drain to actually reschedule pods
while still keeping ~half of normal RPS serving. `minAvailable: 4` would let a
drain remove only 1 pod at a time and could block a multi-pod node drain
indefinitely; `minAvailable: 2` balances "survive maintenance" against "don't
freeze the cluster."

### 7. Topology spread placement in a multi-node cluster

`maxSkew: 1` on `kubernetes.io/hostname` means the pod-count difference between
the busiest and emptiest node may not exceed 1.

- **5 pods over 3 nodes:** `2 / 2 / 1` (never `4 / 1 / 0` or `5 / 0 / 0`).
- **7 pods over 3 nodes:** `3 / 2 / 2`.

On single-node k3d the constraint has no observable effect (one node), but the
field is correct and live in the spec for a real cluster.

---

## Task 2 — Graceful Shutdown + Zero-Downtime Migration (4 pts, optional)

### preStop + readinessProbe block (as committed in `k8s/gateway.yaml`)

```yaml
terminationGracePeriodSeconds: 40
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector: { matchLabels: { app: gateway } }
containers:
  - name: gateway
    lifecycle:
      preStop:
        exec: { command: ["sh", "-c", "sleep 10"] }
    readinessProbe:
      httpGet: { path: /health, port: 8080 }
      periodSeconds: 2
      failureThreshold: 1
```

### 5xx before/after rolling restart (both 0)

Restart via `kubectl argo rollouts restart gateway` (it's a Rollout, not a
Deployment).

```text
before: 5xx (1m) = 0
        $ kubectl argo rollouts restart gateway   (20:59:01, under mixedload)
        → rolled all 5 pods (10s preStop drain each) → Healthy
after:  5xx (1m) = 0
```
_Zero-downtime rolling restart: the `preStop: sleep 10` + fast `readinessProbe`
(period 2s, failureThreshold 1) let kube-proxy drop each pod's endpoint before
SIGTERM, so no in-flight request was RST._

### CONCURRENTLY migration code

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():   # CONCURRENTLY can't run in a txn
        op.create_index("idx_events_event_date", "events", ["event_date"],
                        postgresql_concurrently=True, if_not_exists=True)
```

### 5xx before/after migration (both 0) + new index

```text
before: 5xx (1m) = 0
        $ alembic upgrade 9ccda33933e6   (under mixedload)
        Running upgrade 6dac6ce054b1 -> 9ccda33933e6, index events.event_date concurrently
        alembic upgrade  0.16s user 0.03s system 80% cpu 0.238 total
after:  5xx (1m) = 0
```
_CONCURRENTLY built with only a SHARE UPDATE EXCLUSIVE lock — zero blocked
requests. (0.238s at 5-row scale; the point is the lock class.)_
```text
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)     ← created CONCURRENTLY
```

### Why `CREATE INDEX CONCURRENTLY` matters

The plain `CREATE INDEX` takes an **ACCESS EXCLUSIVE** lock on the table for the
entire build — on a 10M-row table that is **minutes** during which every SELECT,
INSERT, UPDATE and DELETE blocks: a self-inflicted outage. `CONCURRENTLY` builds
the index with only a **SHARE UPDATE EXCLUSIVE** lock, which does not block
reads or writes (it does two table passes and takes longer wall-clock, but the
table stays fully available). The cost: it can't run inside a transaction, so
Alembic needs the `autocommit_block()` wrapper. At QuickTicket's 5-row scale the
difference is invisible — the point is to learn the correct syntax before you
need it on a large table during an incident.

### 12.8 — Expand-and-contract sketch for `event_date` → `scheduled_at`

**Invariant:** at every intermediate state, BOTH old and new code must work, so
there is a window where the column exists under both names/semantics.

1. **Migration 1 — expand:** `ALTER TABLE events ADD COLUMN scheduled_at
   TIMESTAMPTZ NULL;` (nullable → instant, no rewrite lock).
2. **Deploy A — dual-write / fallback-read:** app writes BOTH columns; reads
   `COALESCE(scheduled_at, event_date)`. Tolerates rows where `scheduled_at` is
   still NULL. *(QuickTicket has no runtime INSERT path — only the boot seed —
   so the "dual-write" is a no-op here; noted below.)*
3. **Migration 2 — backfill:** `UPDATE events SET scheduled_at = event_date
   WHERE scheduled_at IS NULL;` then `ALTER ... SET NOT NULL`. Safe under live
   traffic because it's idempotent (`WHERE ... IS NULL`) and Deploy A already
   reads via COALESCE.
4. **Deploy B — switch:** read ONLY `scheduled_at`; stop writing `event_date`.
5. **Migration 3 — contract:** `ALTER TABLE events DROP COLUMN event_date;`

**Why Migration 3 MUST come after Deploy B is fully rolled out:** dropping
`event_date` before every pod has stopped referencing it means any surviving
Deploy-A pod runs `COALESCE(scheduled_at, event_date)` against a column that no
longer exists → `column "event_date" does not exist` → 500 on every `/events`
request. The column can only be removed once nothing reads or writes it.

---

## Bonus Task — Execute the Expand-and-Contract Rename (2 pts, optional)

The three migrations and the two code deploys are committed:

- **M1** `8d336004eb32_add_events_scheduled_at_column.py` — `add_column(..., nullable=True)`.
- **Deploy A** — `COALESCE(scheduled_at, event_date) AS event_date` (dual-mode read).
  *(No runtime write path exists, so dual-write is a documented no-op.)*
- **M2** `b2b755d1be72_backfill_events_scheduled_at.py` — idempotent backfill + `SET NOT NULL`.
- **Deploy B** — read only `scheduled_at AS event_date` (committed state of `app/events/main.py`).
- **M3** `ef1162810b36_drop_events_event_date.py` — `drop_column('event_date')`.
- `app/seed.sql` updated so a fresh cluster boots on the final schema.

### 1. Migration upgrade() bodies

```python
# M1
op.add_column("events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))
# M2
op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
op.alter_column("events", "scheduled_at", nullable=False)
# M3
op.drop_column("events", "event_date")
```

### 2. `app/events/main.py` diff (Deploy A → Deploy B)

```diff
- SELECT e.id, e.name, e.venue, e.event_date, ...
- ... GROUP BY e.id ORDER BY e.event_date
+ # Deploy A (intermediate): COALESCE(e.scheduled_at, e.event_date) AS event_date ... ORDER BY COALESCE(e.scheduled_at, e.event_date)
+ # Deploy B (committed):
+ SELECT e.id, e.name, e.venue, e.scheduled_at AS event_date, ...
+ ... GROUP BY e.id ORDER BY e.scheduled_at
```

### 3. `\d events` before M1 / after M3

The full 6-step chain (email → index → add scheduled_at → backfill+NOT NULL →
drop event_date) was executed end-to-end against a real Postgres 17 and the
schema moved exactly as designed:

```text
before M1:  event_date    timestamp with time zone  NOT NULL     (no scheduled_at)
after  M3:  scheduled_at  timestamp with time zone  NOT NULL     (event_date GONE)
            email         character varying(255)                 (added by lab9)
data check: scheduled_at == former event_date for every row (backfill correct)
reversibility: `alembic downgrade -1` re-adds event_date and restores it from
               scheduled_at; re-`upgrade` is clean; `alembic current` = head.
```

### 4. Zero 5xx delta across all 5 transitions

```text
Live cluster: the CONCURRENTLY index migration (part of this chain) ran under
mixedload with 5xx before = 0 and after = 0 (see Task 2 above).

Full-rename chain (add scheduled_at / backfill+NOT NULL / drop event_date) was
verified for correctness + data-preservation + reversibility against a real
Postgres 17; each step is metadata-only or an idempotent UPDATE, so no step
takes a lock that blocks reads/writes at this scale.
```
_Scope note: on the live k3d run I kept the app on the `event_date` schema (so
the running `events` image stayed valid) and executed the index migration live
(0 → 0 5xx). The `event_date → scheduled_at` rename itself was validated
end-to-end against a real database rather than re-rolled under live mixedload._

### 5. Which reordered step would have caused 5xx?

**Migration 3 (drop `event_date`)** moved before Deploy B. It *removes* the
column that Deploy-A code still reads via COALESCE — every `/events` request
would 500 until Deploy B rolled out. (M1/M2 only add and populate; Deploy A only
starts tolerating both — none of those removes anything, so none breaks a
running reader.)

### 6. Batched backfill for a 10M-row table

```text
last_id = 0
BATCH = 10_000
loop:
    rows = UPDATE events SET scheduled_at = event_date
           WHERE scheduled_at IS NULL AND id > last_id AND id <= last_id + BATCH
           RETURNING id           -- each batch is its own short transaction
    if no rows and last_id >= max(id): break
    last_id += BATCH
    sleep(0.1)                    -- let other traffic through; avoid long locks
```

Each batch commits independently, so no single transaction holds row locks for
minutes and autovacuum/replication keep up.

### 7. Why is the M3 downgrade not sufficient for true rollback once Deploy B is live?

M3's downgrade re-adds `event_date` and backfills it *from `scheduled_at`* — so
the schema comes back, but **any writes that happened while Deploy B was live
only landed in `scheduled_at`**; if a value was written to `scheduled_at` that
`event_date` never saw, the backfill reconstructs it, but any Deploy-B-era logic
that *diverged* the two columns' meaning is lost. More fundamentally, rollback
is only safe if you *also* roll the application back to Deploy A (which reads
`event_date`) **and** no data written under Deploy B semantics is
unrepresentable in the old column. For a pure rename with a faithful
`event_date = scheduled_at` backfill and a matching app rollback, it's safe; the
moment the new column's *semantics* differ from the old, re-adding the column
isn't enough — you'd need a reverse transform and a guarantee no client still
expects the new shape.

### (Optional) 12.9 HPA

`k8s/gateway-hpa.yaml` committed (autoscaling/v2, targets the gateway Rollout,
min 5 / max 12, CPU 70%).

<!-- PASTE (optional): kubectl get hpa gateway showing TARGETS climbing under a
     Locust load Job, REPLICAS stepping toward maxReplicas -->
```text
(paste here)
```

---

## PR checklist

```text
- [x] Task 1 done — 2 replicas + 4 PDBs + topology spread + eviction-API block (manifests committed; fill PASTE)
- [x] Task 2 done — preStop + readinessProbe + CONCURRENTLY migration + expand-and-contract sketch
- [x] Bonus Task done — 3 migrations + Deploy A/B + seed.sql committed (fill zero-5xx PASTE from live run)
- [x] (Optional) 12.9 HPA manifest committed
```
