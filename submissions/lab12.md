# Lab 12 — Advanced Kubernetes Resilience

PR checklist:

```text
- [x] Task 1 done — multi-replica failover + 4 PDBs + topology spread + real eviction-API block
- [x] Task 2 done — preStop + zero-error rolling restart + CONCURRENTLY migration + expand-and-contract sketch
- [x] Bonus Task done — expand-and-contract executed live (3 migrations + 2 deploys, zero 5xx, `event_date` dropped)
- [x] (Optional) 12.9 HPA observation
```

---

## Task 1 — Multi-Replica Failover + PDBs

### 1. `kubectl get deploy,rollout` at target replica counts

```
NAME                            READY   UP-TO-DATE   AVAILABLE
deployment.apps/events          2/2     2            2
deployment.apps/notifications   2/2     2            2
deployment.apps/payments        2/2     2            2

NAME                          DESIRED   CURRENT   UP-TO-DATE   AVAILABLE
rollout.argoproj.io/gateway   5         5         5            5
```

> Note: `k8s/events.yaml` and `k8s/payments.yaml` Deployments don't carry a top-level `metadata.labels.app` (only their pod templates do), so the lab's suggested `kubectl get deploy -l 'app in (events,payments,notifications)'` only matches `notifications`. Verified all three by name instead — not something in scope to fix.

### 2. Before/after 5xx around the coordinated pod-kill

- Before (3m window): `0`
- Killed one `gateway` pod and one `events` pod (`kubectl delete pod <name> --wait=false`); both replacements were `1/1 Running` within ~27s.
- Immediately after (1m window): `~1.09` — one `503` on `/health`, `0` on the checkout path `/events/{id}/reserve`. This is a real, honestly-reported transient blip: at this point in the lab sequence, `preStop`/`readinessProbe` tuning (Task 2, §12.6) hadn't been applied yet, so a raw `kubectl delete pod` still has a brief in-flight-request race. Rechecked ~90s later: back to `0`. The rolling-restart test after Task 2 (below) shows this gap closed once graceful shutdown is wired in.

### 3. `k8s/pdb.yaml`

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

`kubectl get pdb`:

```
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb          1               N/A               1                     0s
gateway-pdb         2               N/A               3                     0s
notifications-pdb   N/A             1                 1                     0s
payments-pdb        1               N/A               1                     0s
```

### 4. Topology spread — live spec + placement

```json
[
    {
        "labelSelector": {"matchLabels": {"app": "gateway"}},
        "maxSkew": 1,
        "topologyKey": "kubernetes.io/hostname",
        "whenUnsatisfiable": "ScheduleAnyway"
    }
]
```

`kubectl get pod -l app=gateway -o wide`: all 5 pods scheduled on `k3d-quickticket-server-0` (the only node) — expected on single-node k3d; the constraint is correctly live in the spec and ready for a real multi-node cluster.

### 5. HTTP 429 from the tightened-PDB eviction test

Tightened `events-pdb` to `minAvailable: 2` (2 replicas, zero tolerance) — `ALLOWED DISRUPTIONS` dropped to `0`. Fired one eviction via `kubectl proxy` + `curl` against `/api/v1/namespaces/default/pods/<name>/eviction`:

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

Restored `events-pdb` to `minAvailable: 1` afterward.

### 6. Design question — `minAvailable` sizing

> *With 3 gateway replicas and `minAvailable: 1`, what's the maximum number of pods that can be evicted simultaneously? Why is `gateway-pdb` set to `minAvailable: 2` with 5 replicas?*

With 3 replicas and `minAvailable: 1`, the PDB guarantees at least 1 pod stays up, so at most **2** pods can be evicted simultaneously (`replicas - minAvailable = 3 - 1 = 2`).

`gateway-pdb` uses `minAvailable: 2` with 5 replicas (allowing 3 simultaneous evictions) rather than `minAvailable: 4` (which would only allow 1) because gateway is the critical path but also the highest-replica-count service — a node drain needs to be able to *actually make progress*. `minAvailable: 4` would mean the drain could only ever evict one gateway pod at a time across the whole rollout, which for a real multi-node cluster performing a rolling node replacement would serialize the drain down to a crawl (or block it outright if multiple pods happen to be co-located on the node being drained). `minAvailable: 2` keeps roughly 40% of capacity guaranteed (2/5) while still letting the cluster actually evict/reschedule the other 3 during maintenance — a deliberate trade-off between "stay fully available" and "let the cluster do its job."

### 7. Design question — topology spread at scale

> *Your topology-spread constraint has no observable effect on single-node k3d. In a 3-node cluster, what placement would `maxSkew: 1` produce for 5 gateway pods? What about for 7?*

`maxSkew: 1` with `topologyKey: kubernetes.io/hostname` means the pod-count difference between the most-loaded and least-loaded node can never exceed 1.

- **5 pods over 3 nodes**: the scheduler distributes as evenly as possible — `2 / 2 / 1`. Never `3 / 1 / 1` (skew of 2) or `5 / 0 / 0`.
- **7 pods over 3 nodes**: `3 / 2 / 2`. Never `4 / 2 / 1` (skew of 3) or `7 / 0 / 0`.

---

## Task 2 — Graceful Shutdown + Zero-Downtime Migration

### `preStop` / `readinessProbe` block (as it appears in `k8s/gateway.yaml`)

```yaml
    spec:
      # Give in-flight requests time to finish after SIGTERM (10s preStop + up to 30s drain).
      terminationGracePeriodSeconds: 40
      ...
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
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            periodSeconds: 2
            failureThreshold: 1
```

### 5xx before / after the rolling restart

```bash
kubectl argo rollouts restart gateway
```

- Before (1m window): `0`
- After (3m window, 12s settle): `0`
- All 5 gateway pods rotated to a fresh ReplicaSet hash, confirming a real restart (not a no-op).

This is the direct improvement over the Task 1 pod-kill test above: with `preStop` + fast `readinessProbe` in place, a full rolling restart of all 5 gateway replicas produces **zero** 5xx, whereas a raw `kubectl delete pod` (no graceful shutdown) produced a brief single-request blip.

### Migration code — `CREATE INDEX CONCURRENTLY`

`migrations/versions/e999114930be_index_events_event_date_concurrently.py`:

```python
def upgrade() -> None:
    """Upgrade schema."""
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

### 5xx before / after the migration

- Before: `sum(gateway_requests_total{status=~"5.."})` → empty result vector (== 0; no 5xx series exists at all)
- `time alembic upgrade head`: **0.296s** total (essentially instant on a 5-row table)
- After (5s later): empty result vector (== 0) — identical to before

### `\d events` showing the new index

```
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
```

### Expand-and-contract sketch (`events.event_date` → `events.scheduled_at`)

1. **Migration 1 (expand):** `ALTER TABLE events ADD COLUMN scheduled_at TIMESTAMPTZ NULL;` — nullable, no default, so it's an instant metadata-only change even on a huge table (no table rewrite, no meaningful lock).
2. **Code deploy A (dual-write, fallback-read):** every write path that sets `event_date` also sets `scheduled_at` to the same value; every read path selects `COALESCE(scheduled_at, event_date) AS event_date`. Old rows have `scheduled_at = NULL`; new/updated rows have both set. Both old and new pods keep working because the read side tolerates NULL on either side.
3. **Migration 2 (backfill):** `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL;` then `ALTER COLUMN scheduled_at SET NOT NULL`. Safe under live traffic because Deploy A's `COALESCE` already tolerates both NULL and non-NULL `scheduled_at` — the backfill just fills in a value the read path already knows how to fall back around, and it's idempotent (`WHERE scheduled_at IS NULL`), so an interrupted/retried backfill can't corrupt anything.
4. **Code deploy B (switch to new column only):** read paths drop the `COALESCE` and select `scheduled_at` directly; write paths write only to `scheduled_at`. `event_date` is no longer touched by application code — it just sits there, unused.
5. **Migration 3 (contract):** `ALTER TABLE events DROP COLUMN event_date;`. This must come strictly after Deploy B is *fully* rolled out (see the design answer below).

### Design question — why must migration 3 come after Deploy B?

> *In your expand-and-contract sketch, why MUST migration 3 (drop old column) come after deploy B has fully rolled out, never before?*

At every point in the sequence up to and including Deploy A, some fraction of live pods may still be running Deploy A's code (which reads/writes `event_date` via `COALESCE`) — a rolling deploy doesn't switch every pod instantaneously. If migration 3 ran while *any* Deploy-A pod is still serving traffic, that pod's next query referencing `e.event_date` would fail outright (`column "event_date" does not exist`), turning every request that pod handles into a `500`. Only once `kubectl rollout status` confirms Deploy B has fully replaced every pod is it guaranteed that no running code path references `event_date` anymore — at that point, and only that point, dropping the column is safe.

---

## Optional — 12.9 HPA observation

`k8s/gateway-hpa.yaml`:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: gateway
spec:
  scaleTargetRef:
    apiVersion: argoproj.io/v1alpha1
    kind: Rollout
    name: gateway
  minReplicas: 5
  maxReplicas: 12
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

Drove load with a 200-user / 20-per-second-ramp / 120s Locust job (reusing `labs/lab10/locustfile.py`). `kubectl get hpa gateway` progression: `21% → 31% → 138%` CPU (target 70%):

```
NAME      REFERENCE         TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 115%/70%   5         12        9          2m4s
```

`kubectl describe hpa gateway` event: `SuccessfulRescale ... New size: 9; reason: cpu resource utilization (percentage of request) above target`.

As expected on single-node k3d, all 9 pods scheduled on the same node (no real elasticity) — the point was watching the controller compute utilization and make a scaling decision, which it did correctly. Cleaned up the load job / HPA / scaled gateway back to 5 replicas afterward, since this was a non-graded demo; `k8s/gateway-hpa.yaml` itself is still committed as the deliverable.

---

## Bonus Task — Execute the Expand-and-Contract Rename Live

### 1. The three migrations (`upgrade()` bodies)

`migrations/versions/ffbc3dfe96aa_add_events_scheduled_at_column.py` (M1 — expand):

```python
def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
```

`migrations/versions/15d87976e4ed_backfill_events_scheduled_at.py` (M2 — backfill):

```python
def upgrade() -> None:
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)
```

`migrations/versions/5a9acf7364c9_drop_events_event_date.py` (M3 — contract):

```python
def upgrade() -> None:
    op.drop_column("events", "event_date")
```

### 2. `app/events/main.py` diff — Deploy A → Deploy B

**Deploy A** (dual-write/fallback-read — applied against `list_events()` and `get_event()`, the two SELECTs + the one ORDER BY):

```diff
-            SELECT e.id, e.name, e.venue, e.event_date, e.total_tickets, e.price_cents,
+            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date,
+                   e.total_tickets, e.price_cents,
                    COALESCE(SUM(o.quantity), 0) as confirmed
             FROM events e LEFT JOIN orders o ON e.id = o.event_id
-            GROUP BY e.id ORDER BY e.event_date
+            GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
```

(same `SELECT` change applied to `get_event()`'s query, no `ORDER BY` there)

**Deploy B** (clean switch — no alias kept, since the JSON response key is a separately-hardcoded `"date"` field, not derived from the SQL alias):

```diff
-            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date,
-                   e.total_tickets, e.price_cents,
+            SELECT e.id, e.name, e.venue, e.scheduled_at,
+                   e.total_tickets, e.price_cents,
                    COALESCE(SUM(o.quantity), 0) as confirmed
             FROM events e LEFT JOIN orders o ON e.id = o.event_id
-            GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
+            GROUP BY e.id ORDER BY e.scheduled_at
```

No runtime write path for `event_date` exists — QuickTicket only ever writes it via `seed.sql` at container startup (no `INSERT`/`UPDATE` endpoint touches it), so the "dual-write" half of Deploy A was a no-op by construction, per the lab's own note for this exact case. `app/seed.sql` was updated in Deploy B (`CREATE TABLE` + `INSERT` columns renamed to `scheduled_at`) so a freshly-bootstrapped cluster lands directly on the final schema.

### 3. `\d events` before migration 1 / after migration 3

Before (captured just before M3, i.e. after M1 + Deploy A + M2 + Deploy B — both columns present):

```
    Column     |           Type           | Nullable
---------------+--------------------------+----------
 id            | integer                  | not null
 name          | text                     | not null
 venue         | text                     | not null
 event_date    | timestamp with time zone | not null
 total_tickets | integer                  | not null
 price_cents   | integer                  | not null
 email         | character varying(255)   |
 scheduled_at  | timestamp with time zone | not null
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
```

After M3:

```
    Column     |           Type           | Nullable
---------------+--------------------------+----------
 id            | integer                  | not null
 name          | text                     | not null
 venue         | text                     | not null
 total_tickets | integer                  | not null
 price_cents   | integer                  | not null
 email         | character varying(255)   |
 scheduled_at  | timestamp with time zone | not null
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
```

`event_date` is gone (and `idx_events_event_date`, the Task-12.7 index, went with it automatically — Postgres drops indexes on a column when the column is dropped); `scheduled_at` is `NOT NULL`.

### 4. 5xx baseline / final / diff

```
baseline: 414
final:    414
diff /tmp/5xx.baseline /tmp/5xx.final   →   identical (only the query timestamp differs; counter value unchanged)
```

**Zero 5xx across the entire 5-step sequence** (M1 → Deploy A → M2 → Deploy B → M3), each individually re-verified against the running `mixedload` traffic.

> Note on the baseline value: `sum(gateway_requests_total{status=~"5.."})` is a cumulative counter, not a rate — `414` reflects historical 5xx accumulated over the whole session (including the Task-1 pod-kill blip and earlier lab-11 testing), not "414 errors during this bonus task." What matters, per the lab's own methodology, is that the value is **identical before and after** — i.e. the delta across all 5 transitions is exactly zero.

### 5. Design question — which single step would have caused 5xx if reordered earlier?

> *You ran 5 transitions (M1, Deploy A, M2, Deploy B, M3) under live traffic. Which single step would have caused 5xx if you'd reordered it earlier?*

**M3 (drop `event_date`)** is the one step that cannot move earlier without causing errors — if it ran before Deploy B had fully rolled out, any still-running Deploy-A pod would 500 on its next `/events` request (its query still references `e.event_date`, which would no longer exist). Every other step is safe to run early because it only *adds* capability (M1 adds a nullable column; Deploy A adds a fallback read path; M2 fills in a value the fallback already tolerates) — M3 is the only step that *removes* something a currently-running code path might still depend on, which is exactly why "drop" is always the last step in expand-and-contract, never an early one.

### 6. Design question — batching the backfill at production scale

> *Write the batching pattern (5-10 lines of pseudocode) that keeps each transaction small.*

```python
BATCH_SIZE = 10_000
last_id = 0
while True:
    result = op.execute(f"""
        UPDATE events SET scheduled_at = event_date
        WHERE id > {last_id} AND id <= {last_id + BATCH_SIZE}
          AND scheduled_at IS NULL
    """)
    if result.rowcount == 0 and last_id >= max_id:
        break
    last_id += BATCH_SIZE
    time.sleep(0.1)   # let other transactions/replication catch up between batches
```

Each `UPDATE` only locks the rows in its `id` range for the duration of that one small transaction, instead of holding row locks across the entire 10M-row table for the full backfill's duration — keeping replication lag, lock contention, and the risk of a long-running transaction blocking `VACUUM` all bounded.

### 7. Design question — why isn't the migration-3 downgrade sufficient for true rollback safety?

> *Your downgrade from migration 3 re-adds `event_date` and backfills it. Why is that not sufficient for true rollback safety once Deploy B is live in production? What would have to be true for the rollback to be safe?*

The downgrade re-adds `event_date` and copies `scheduled_at`'s *current* values into it — but by the time Deploy B is live, `event_date` has been sitting frozen since the moment Deploy B stopped writing to it. Any row created or modified while Deploy B was live only ever had `scheduled_at` set; the downgrade's backfill would populate `event_date` correctly for *those* rows too (since it copies from `scheduled_at`, which is authoritative), so data loss isn't actually the problem — the real problem is **code**. The downgrade only reverses the *schema*; it does nothing to redeploy Deploy A's application code. If you ran the migration-3 downgrade but left Deploy B's code running, Deploy B doesn't read or write `event_date` at all, so the newly-restored column would just sit there, silently stale, doing nothing — not a real rollback of behavior. For the rollback to be *safe* (i.e., to actually restore Deploy-A-era behavior), you'd need the migration-3 downgrade to run **and then** a code rollback to Deploy A (or earlier) to also be deployed — and that rollback deploy would need to happen while both columns still validly exist, i.e. before any further schema changes remove `scheduled_at`. In other words, true rollback safety requires treating the schema downgrade and the code rollback as one coordinated operation, not independent steps — exactly mirroring why the forward migration was split into 5 careful steps in the first place.

---

## Operational notes (transparency)

- **CI image-tag race during Task 1:** branched `feature/lab12` from `main` right before a CI bot commit (`ci: update image tags to b2ec4a4...`, bumping `k8s/{gateway,events,payments}.yaml` after the lab11 merge) landed. My branch's `k8s/gateway.yaml` (and `events.yaml`/`payments.yaml`, applied for the 12.1 replica-count change) still referenced the older pre-lab11 image tag, and applying them briefly rolled the live cluster back to pre-lab11 code. Caught it via `kubectl argo rollouts get rollout gateway` showing the stale tag; fixed by fetching, fast-forwarding the branch onto the now-updated `origin/main` (no unique commits yet, so a clean fast-forward — stashed the in-progress edits first, which merged back cleanly since the bot's `image:` lines and my `replicas:`/`topologySpreadConstraints` lines don't overlap), and re-applying. **Zero 5xx** across the whole incident+recovery window.
- **Live Postgres wasn't at migration head:** discovered the k3d cluster's Postgres had never actually had Lab 9's `add_email_column_to_events` migration applied (raw seed-baseline schema, no `alembic_version` table). Ran `alembic upgrade head` to catch it up (adds a nullable `email` column, zero risk) before creating/running any lab12 migrations.
- **Local Postgres port conflict:** this workstation runs its own native `postgresql.service` bound to `127.0.0.1:5432`, so `kubectl port-forward svc/postgres 5432:5432` (as the lab suggests) can't bind. Used port `5433` instead; temporarily pointed `alembic.ini` at `:5433` while running each migration, then reverted it to the committed `:5432` form immediately after every single migration — `alembic.ini` is not in the lab's file list and shows no diff in the final commit.
- **CI-managed `events` image during Deploy A/B:** `k8s/events.yaml`'s `image:` field is bot-managed (ghcr tag bumped on merge, `imagePullPolicy: Always`), same pattern as gateway. For local Deploy A/B testing (per the lab's documented `docker build` + `k3d image import` + `kubectl rollout restart` workflow), used `kubectl patch deployment events` to temporarily point at the local `quickticket-events:v1` tag with `imagePullPolicy: Never`, without touching the committed YAML — that field stays CI-owned and will get the correct ghcr tag once this PR merges and CI rebuilds.
