# Lab 12 — Advanced Kubernetes Resilience

Environment: local k3d cluster `quickticket` (single node). Gateway is a 5-replica Argo Rollouts
`Rollout`; events / payments / notifications scaled to 2 replicas; Postgres + Redis + in-cluster
Prometheus. `labs/lab8/mixedload.yaml` drives checkout traffic throughout. Alembic runs from the host
against Postgres via `kubectl port-forward svc/postgres 5432:5432`.

---

## Task 1 — Multi-Replica Failover + PDBs

### 1. Services at target replica counts

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl get deploy,rollout
NAME                            READY   UP-TO-DATE   AVAILABLE
deployment.apps/events          2/2     2            2
deployment.apps/notifications   2/2     2            2
deployment.apps/payments        2/2     2            2

NAME                          DESIRED   CURRENT   READY   AVAILABLE
rollout.argoproj.io/gateway   5         5         5       5
```

### 2. Zero 5xx during a coordinated pod-kill under load

```console
# before
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  '.../query?query=sum(increase(gateway_requests_total{status=~"5.."}[3m]))'
  5xx = 0

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl delete pod $(kubectl get pod -l app=gateway -o jsonpath='{.items[0].metadata.name}') --wait=false
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl delete pod $(kubectl get pod -l app=events  -o jsonpath='{.items[0].metadata.name}') --wait=false
# both replacement pods Ready within ~5s; Service endpoints reroute to survivors during the gap

# after (1m window covers the kill)
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  '.../query?query=sum(increase(gateway_requests_total{status=~"5.."}[1m]))'
  5xx = 0
```

The surviving replica of each service absorbed traffic while the killed pod was replaced — zero 5xx.

### 3. `kubectl get pdb`

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl get pdb
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
events-pdb          1               N/A               1
gateway-pdb         2               N/A               3
notifications-pdb   N/A             1                 1
payments-pdb        1               N/A               1
```

Matches the design: gateway tolerates 3 simultaneous evictions (5 − 2), events/payments keep ≥1,
notifications is best-effort (`maxUnavailable: 1`).

### 4. Topology spread — in the live spec, placement on single-node k3d

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl get rollout gateway -o jsonpath='{.spec.template.spec.topologySpreadConstraints}' | python3 -m json.tool
[
    {
        "labelSelector": {"matchLabels": {"app": "gateway"}},
        "maxSkew": 1,
        "topologyKey": "kubernetes.io/hostname",
        "whenUnsatisfiable": "ScheduleAnyway"
    }
]

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl get pod -l app=gateway -o wide
gateway-...-5z4vh   k3d-quickticket-server-0
gateway-...-9lhdw   k3d-quickticket-server-0
gateway-...-dtgdx   k3d-quickticket-server-0
gateway-...-g477b   k3d-quickticket-server-0
gateway-...-sdcwb   k3d-quickticket-server-0
```

All 5 pods land on the single node — expected on single-node k3d. The YAML is correct and live in the
spec; `ScheduleAnyway` (not `DoNotSchedule`) is what keeps them schedulable here.

### 5. PDB actually blocks an eviction (HTTP 429)

Tightened `events-pdb` to `minAvailable: 2` (zero tolerance with 2 replicas), then fired one eviction
through the API via `kubectl proxy`:

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl patch pdb events-pdb --type=merge -p '{"spec":{"minAvailable":2}}'
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl get pdb events-pdb    # ALLOWED DISRUPTIONS = 0
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ curl -s -X POST .../pods/$POD/eviction -d '{"apiVersion":"policy/v1","kind":"Eviction",...}'
{
  "status": "Failure",
  "message": "Cannot evict pod as it would violate the pod's disruption budget.",
  "reason": "TooManyRequests",
  "details": {"causes": [{"reason": "DisruptionBudget",
      "message": "The disruption budget events-pdb needs 2 healthy pods and has 2 currently"}]},
  "code": 429
}
```

`HTTP 429`, `reason: DisruptionBudget` — the PDB rejected the voluntary disruption. Restored to
`minAvailable: 1` afterward.

### 6. PDB math

With **3 replicas** and `minAvailable: 1`, the API allows up to **2** pods evicted simultaneously
(3 − 1). My `gateway-pdb` uses `minAvailable: 2` with **5 replicas** → tolerates **3** evictions at once,
which is enough to let a node drain make progress (reschedule ~3 pods) while still keeping ~40% of gateway
capacity serving. Setting it to `4` would only ever allow 1 eviction at a time and a multi-pod node drain
could stall.

### 7. Topology spread in a real 3-node cluster

`maxSkew: 1` bounds the difference between the most- and least-loaded node.
- **5 pods / 3 nodes:** placement **2 / 2 / 1** (skew 1). Never 4/1/0 or 5/0/0.
- **7 pods / 3 nodes:** placement **3 / 2 / 2** (skew 1). Never 4/2/1 (skew 2).

---

## Task 2 — Graceful Shutdown + Zero-Downtime Migration

### `preStop` + `readinessProbe` (gateway Rollout)

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

The `preStop` sleep holds the container alive after the pod is marked NotReady, giving kube-proxy /
endpoints controllers time to drop this pod from every node's routing **before** uvicorn gets SIGTERM —
so no request is sent to a shutting-down pod. `terminationGracePeriodSeconds: 40` covers the 10s preStop
plus in-flight drain.

### Zero 5xx during a rolling restart under load

```console
# before
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ ... sum(increase(gateway_requests_total{status=~"5.."}[1m]))
  5xx = 0

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl argo rollouts restart gateway
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl argo rollouts status gateway --timeout=240s
Healthy

# after
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ ... sum(increase(gateway_requests_total{status=~"5.."}[3m]))
  5xx = 0
```

(`kubectl rollout restart deployment/gateway` would fail — gateway is a `rollout.argoproj.io`, not a
`deployment.apps`. Use `kubectl argo rollouts restart gateway`.)

### `CREATE INDEX CONCURRENTLY` migration

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date", "events", ["event_date"],
            unique=False, postgresql_concurrently=True, if_not_exists=True,
        )

def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("idx_events_event_date", table_name="events",
                      postgresql_concurrently=True, if_exists=True)
```

The `autocommit_block()` is the key detail — Alembic wraps migrations in a transaction by default, but
`CREATE INDEX CONCURRENTLY cannot run inside a transaction block`.

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ time alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade b8891ec9e21a -> 304972d3e8bf, index events.event_date concurrently
real    0m0.296s

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- psql -U quickticket -d quickticket -c '\d events' | grep idx_events
    "idx_events_event_date" btree (event_date)
```

5xx before = after = 0 (identical) — no impact under live traffic.

### Why `CREATE INDEX CONCURRENTLY` matters

A plain `CREATE INDEX` takes an **ACCESS EXCLUSIVE** lock on the table for the whole build — every read
and write blocks until it finishes. On a 10M-row table that can be **minutes** of full-table stall = a
user-visible outage. `CONCURRENTLY` builds the index with a milder **SHARE UPDATE EXCLUSIVE** lock that
does not block reads or writes (it does two table scans and takes longer, but online). The trade-off:
it can't run inside a transaction, and an interrupted build can leave an `INVALID` index behind — hence
`if_not_exists=True` to keep it re-runnable. At QuickTicket's 5-row scale the difference is invisible;
you learn the right syntax now so you don't learn it during an outage.

### 12.8 — Expand-and-contract rename sketch (`event_date` → `scheduled_at`)

**3 migrations + 2 code deploys, interleaved. Invariant: at every step both the old and the new code
must work.**

1. **Migration 1 (expand):** `ALTER TABLE events ADD COLUMN scheduled_at TIMESTAMPTZ NULL;` — nullable so
   it's an instant metadata-only change. Old code ignores it; new column is all NULL.
2. **Code deploy A (dual-write / fallback-read):** read `COALESCE(scheduled_at, event_date)`; write to
   **both** columns. Tolerates rows where `scheduled_at` is still NULL.
3. **Migration 2 (backfill):** `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL;`
   then `ALTER COLUMN scheduled_at SET NOT NULL`. Idempotent; safe because Deploy A already tolerates both
   NULL and non-NULL via COALESCE. (At scale, batch the UPDATE.)
4. **Code deploy B (switch):** read and write **only** `scheduled_at`. No code references `event_date`.
5. **Migration 3 (contract):** `ALTER TABLE events DROP COLUMN event_date;` — safe only now, because no
   running code touches `event_date` anymore.

### Why migration 3 must come after deploy B is fully rolled out

Migration 3 removes `event_date`. If it ran while any Deploy-A pod were still live, that pod's
`COALESCE(scheduled_at, event_date)` (and its dual-write) would reference a column that no longer exists
→ every `/events` request on that pod 500s. The column may only be dropped once **no** running code reads
or writes it — i.e. after `kubectl rollout status` confirms Deploy B is 100% rolled out. Drop-before-switch
is the classic way to turn a "zero-downtime" rename into an outage.

> **12.9 HPA:** skipped — it's the explicitly-optional, non-graded observation, and `k8s/gateway-hpa.yaml`
> is not in the lab's submit file list, so it's left out to keep the PR to exactly the requested files.

---

## Bonus Task — Execute the Expand-and-Contract Rename (live)

Ran the full sequence on the live cluster under `mixedload`. **First hardened the `events` Deployment
with a `preStop` hook + `terminationGracePeriodSeconds: 40`** — without it, an events rolling restart
briefly routes traffic to a terminating pod (I measured 2×502 on an early attempt), because unlike the
gateway the events Deployment had no graceful-drain window. This is the Task-2 graceful-shutdown lesson
applied to `events`, and it's what makes the two code deploys genuinely zero-downtime.

### The three migration `upgrade()` bodies

```python
# M1 — add_events_scheduled_at
op.add_column("events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))

# M2 — backfill_events_scheduled_at
op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
op.alter_column("events", "scheduled_at", nullable=False)

# M3 — drop_events_event_date
op.drop_column("events", "event_date")
```

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ alembic history
a454c61be623 (head), drop events.event_date
d95c13f14ad7,      backfill events.scheduled_at
ed097f0d82b9,      add events.scheduled_at column
304972d3e8bf,      index events.event_date concurrently
b8891ec9e21a,      add email column to events
8da3f9006d53,      baseline - pre-existing schema
```

### `app/events/main.py` diff — Deploy A → Deploy B (the read path)

```diff
-        # Deploy A: prefer scheduled_at, fall back to event_date (COALESCE).
-                   COALESCE(e.scheduled_at, e.event_date) AS event_date,
-            GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
+        # Deploy B: scheduled_at is backfilled + NOT NULL, read it directly.
+                   e.scheduled_at AS event_date,
+            GROUP BY e.id ORDER BY e.scheduled_at
```

I kept the `AS event_date` alias through Deploy B so the `/events` response shape stays byte-for-byte
identical — the gateway and any client never see the rename. QuickTicket has no runtime INSERT of event
rows (the only writer is `app/seed.sql` at boot), so there was no dual-**write** path to change; the seed
was updated to `scheduled_at` in Deploy B. Noted here per the lab.

### `\d events` before M1 and after M3

```console
# BEFORE (has event_date, NOT NULL, plus idx_events_event_date)
 event_date    | timestamp with time zone | not null
    "idx_events_event_date" btree (event_date)

# AFTER M3 (event_date gone; scheduled_at is NOT NULL; index dropped with its column)
 scheduled_at  | timestamp with time zone | not null
```

### Zero 5xx across all 5 transitions

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ cat /tmp/5xx.baseline   # snapshot before M1
2
# ... M1 → Deploy A → M2 → Deploy B → M3, each checked: delta 0 ...
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ cat /tmp/5xx.final
2
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ diff /tmp/5xx.baseline /tmp/5xx.final
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$    # (no output = identical = zero 5xx delta)
```

(The baseline of `2` is residual from an earlier non-graceful events restart, baked into the snapshot;
the point is the **delta** across the five transitions is **0**. Final checkout `reserve`→`pay` = 200.)

### Bonus design answers

**Which single step, if reordered earlier, would have caused 5xx?**
**Migration 3 (drop `event_date`).** Every other step only *adds* capability: M1 adds a nullable column
(no reader depends on it), Deploy A adds a fallback read, M2 backfills (Deploy A already tolerates both
states), Deploy B narrows to the new column (M2 guaranteed it's populated + NOT NULL). Only M3 *removes*
something. Run before Deploy B is fully out, any surviving Deploy-A pod's `COALESCE(..., event_date)`
references a dropped column and 500s on every `/events` call.

**Batching the backfill on a 10M-row table (pseudocode):**
```text
last_id = 0
loop:
    rows = UPDATE events SET scheduled_at = event_date
           WHERE scheduled_at IS NULL AND id > last_id
           ORDER BY id LIMIT 10000
           RETURNING id            # each batch = its own short transaction
    if rows is empty: break
    last_id = max(id in rows)
    sleep 100ms                    # let autovacuum + replication catch up
```
Each batch commits independently, so no single long-running transaction holds locks or bloats WAL, and
replicas don't fall behind. A single unbatched `UPDATE` of 10M rows would hold row locks and a huge
transaction for minutes.

**Why re-adding + backfilling `event_date` in M3's downgrade isn't sufficient for true rollback once
Deploy B is live in production:**
The downgrade restores the *column and its current data*, but rollback safety is about the *code*, not
just the schema. Once Deploy B has been live, it has been writing **only** `scheduled_at` — so any rows
created/updated during the Deploy-B window have a correct `scheduled_at` but the re-added `event_date`
is only a point-in-time copy taken by the downgrade's `UPDATE`. If you then roll application code back to
Deploy A (which dual-writes and reads `COALESCE`), it works for existing rows but there's a **gap**: writes
that happened under Deploy B after the schema downgrade but before the code rollback would have updated
`scheduled_at` only, leaving `event_date` stale — reads that fall through to `event_date` would return
wrong values. For the rollback to be truly safe you'd need: (a) the old code still present and able to
run, (b) a re-backfill of `event_date` from `scheduled_at` performed *after* the code rollback (not
before), and (c) confidence no client cached the dropped column's absence. In practice, once the contract
migration has run and Deploy B has taken production writes, the clean path is *forward* (fix-forward),
not down — which is exactly why the drop is the point of no easy return.

---

## PR checklist

```text
- [x] Task 1 — multi-replica failover + 4 PDBs + topology spread + real eviction-API block
- [x] Task 2 — preStop + zero-error rolling restart + CONCURRENTLY migration + expand-and-contract sketch
- [x] Bonus Task — expand-and-contract executed live (3 migrations + 2 deploys, zero 5xx, event_date dropped)
- [ ] (Optional) 12.9 HPA observation — skipped (non-graded, not in submit file list)
```
