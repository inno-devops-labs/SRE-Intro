# Lab 12 — Advanced Kubernetes Resilience

**Branch:** `feature/lab12`  
**Environment:** single-node k3d (`k3d-quickticket-server-0`, Kubernetes v1.35.5+k3s1)  
**Application:** QuickTicket  
**Traffic generator:** `mixedload`, 2/2 replicas  
**Submission scope:** Task 1, Task 2, and the executed expand-and-contract bonus. Optional HPA exercise not attempted.

## Summary

Implemented multi-replica services, four PodDisruptionBudgets (PDBs), gateway topology spreading, graceful termination, and an online PostgreSQL index migration. Also executed the five-step schema migration from `events.event_date` to `events.scheduled_at` with three Alembic revisions and two Events code deployments.

**Important qualification:** The bonus's strict requirement of **zero HTTP 5xx across all five transitions was not achieved**. HTTP 502/503 responses were recorded around the two Events rolling deployments. The three schema-migration steps themselves produced no observed increase in the sampled 5xx counters. Details appear below.

## Task 1 — Multi-Replica Failover, PDBs, and Topology Spread (4 pts)

### 12.1 — Service replicas

Updated `k8s/events.yaml`, `k8s/payments.yaml`, and `k8s/notifications.yaml` to `spec.replicas: 2`. Gateway remained an Argo Rollout with five replicas.

Observed status:

```text
NAME            READY   UP-TO-DATE   AVAILABLE
events          2/2     2            2
payments        2/2     2            2
notifications   2/2     2            2
mixedload       2/2     2            2

NAME      DESIRED   CURRENT   UP-TO-DATE   AVAILABLE
gateway   5         5         5            5
```

The original command `kubectl get deploy -l 'app in (events,payments,notifications)'` returned no resources because the labels were on the **Pod templates**, not the Deployment metadata. Queried the deployments by name instead. PDB selectors match the Pod labels successfully.

### 12.2 — Pod-kill failover under load

While `mixedload` was running at 2/2 replicas, deleted one Gateway Pod and one Events Pod:

```text
Deleting gateway: gateway-758f9fb87-6j6z7
Deleting events: events-6cd74cf698-ct975
pod "gateway-758f9fb87-6j6z7" deleted from default namespace
pod "events-6cd74cf698-ct975" deleted from default namespace
```

Replacement Gateway Pod became Ready approximately **7 seconds** after creation; replacement Events Pod approximately **9 seconds** after creation. Controllers returned to Gateway **5/5** and Events **2/2**.

Prometheus snapshots:

| Metric | Before | After | Interpretation |
|---|---:|---:|---|
| All gateway 5xx, including `/health` | ~5.143 over 3 min | ~1.091 over 1 min | Pre-existing intermittent `/health` 503s; windows differ and are not directly comparable |
| Gateway 5xx excluding `/health` | 0 over 3 min | 0 over 1 min | No user-route 5xx observed in sampled windows |

This test shows rapid Pod replacement and user-route availability in the observed metric windows, **but it does not establish the lab's stronger zero-5xx criterion for the entire operation**, since `/health` 503s were already present.

**Note:** `kubectl delete pod` bypasses PDB admission. PDB enforcement was verified separately using the Eviction API in 12.5.

### 12.3 — PodDisruptionBudgets

Committed manifest: `k8s/pdb.yaml` (four `policy/v1` PDBs).

| PDB | Policy | Healthy / desired | Allowed disruptions |
|---|---|---|---:|
| `gateway-pdb` | `minAvailable: 2` | 5 / 2 | 3 |
| `events-pdb` | `minAvailable: 1` | 2 / 1 | 1 |
| `payments-pdb` | `minAvailable: 1` | 2 / 1 | 1 |
| `notifications-pdb` | `maxUnavailable: 1` | 2 replicas | 1 |

Observed `kubectl get pdb`:

```text
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
events-pdb          1               N/A               1
gateway-pdb         2               N/A               3
notifications-pdb   N/A             1                 1
payments-pdb        1               N/A               1
```

### 12.4 — Topology spreading

Added the following to `k8s/gateway.yaml` under `spec.template.spec`:

```yaml
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        app: gateway
```

The live Rollout JSON confirmed the values:

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

After canary rollout status became `Healthy`, the five active Gateway Pods were all `1/1 Running` on `k3d-quickticket-server-0`. This is expected on the single-node cluster. The scheduler uses this constraint as a preference (`ScheduleAnyway`), not a hard placement guarantee.

### 12.5 — Eviction API proof

Temporarily tightened `events-pdb` to `minAvailable: 2` while two Events Pods were healthy, reducing `ALLOWED DISRUPTIONS` to **0**. Sent a `policy/v1` Eviction POST to the Pod subresource through `kubectl proxy`.

Actual HTTP response: **`429 Too Many Requests`**.

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

The targeted Pod (`events-6cd74cf698-b5b96`) remained `1/1 Running`. Restored `events-pdb` to `minAvailable: 1`; its allowed disruptions returned to **1**.

### Task 1 conceptual answers

**Q: With three gateway replicas and `minAvailable: 1`, how many can be evicted simultaneously? Why use `minAvailable: 2` with five replicas?**  
**A:** Two of the three could be voluntarily evicted if all three are healthy. With five replicas, `minAvailable: 2` protects two gateway replicas while allowing up to three simultaneous voluntary evictions, balancing availability with node-maintenance flexibility.

**Q: What would `maxSkew: 1` produce for five and seven gateway Pods across three nodes?**  
**A:** The balanced target is **2/2/1** for five Pods and **3/2/2** for seven. With `ScheduleAnyway`, these are scheduler preferences, not strict guarantees; other constraints or uneven node eligibility can affect actual placement.

---

## Task 2 — Graceful Shutdown and Online Migration (4 pts)

### 12.6 — Gateway graceful termination and readiness

Updated the Argo Rollout's Pod template:

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

Existing container settings (image, liveness, environment, resource requests, and rollout strategy) were retained. Kubernetes live spec confirmed the above values. Argo Rollouts finished `Healthy`; five Gateway Pods were `1/1 Running`.

Under `mixedload` (`2/2`), executed `kubectl argo rollouts restart gateway`. Restart finished `Healthy` and all five replacement Pods were ready.

Prometheus checks:

| Gateway 5xx | Before restart | After restart |
|---|---:|---:|
| All routes | 0 over 1 minute | 0 over 3 minutes |
| User routes (excluding `/health`) | 0 over 1 minute | 0 over 3 minutes |

The sampled windows recorded **zero HTTP 5xx**. The zero-error observed result is consistent with successful graceful restart, although short-window Prometheus snapshots are not complete tracing of every request.

### 12.7 — `CREATE INDEX CONCURRENTLY`

Restored the Alembic configuration and prior migrations from `feature/lab9`, then confirmed the database was at `0f25adc89825`. The active forward was `kubectl port-forward svc/postgres 15432:5432`.

New migration: `5769432bac65_index_events_event_date_concurrently.py`:

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
```

Executed `alembic upgrade head` under mixedload in approximately **1.14 seconds**. Alembic current then reported `5769432bac65 (head)`. PostgreSQL `\d events` showed:

```text
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
```

Prometheus 5xx counter **0 before / 0 after**, with empty `diff`.

**Q: Why does `CREATE INDEX CONCURRENTLY` matter on a table with 10 million rows?**  
**A:** Ordinary index creation can block concurrent writes for a long time while the index is built. PostgreSQL's concurrent form allows normal reads and writes during most of the build by performing additional scanning/synchronization at the cost of more work and execution time. It cannot run inside a transaction block, hence Alembic's `autocommit_block()`. Concurrency does not eliminate all operational risks (e.g., waiting on old transactions or invalid indexes after failure).

### 12.8 — Expand-and-contract design (three migrations, two code deploys)

1. **Migration 1 — Expand:** `ALTER TABLE events ADD COLUMN scheduled_at TIMESTAMPTZ NULL;` Old code still reads `event_date`.
2. **Deploy A — Compatibility:** Read `COALESCE(scheduled_at, event_date)` and preserve the external API shape. If runtime date writes exist, write to both columns. QuickTicket's Events application has no runtime date insert/update path; initialization writes were handled separately by `seed.sql`.
3. **Migration 2 — Backfill:** `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL;`; validate data and then apply `NOT NULL` when the write path is compatible. Deploy A tolerates either null or populated new values.
4. **Deploy B — Switch:** Read/write only `scheduled_at`; wait for the rollout to finish and verify no old Pods remain. Keep the JSON API response field `date` for backward compatibility.
5. **Migration 3 — Contract:** Drop `event_date` only after Deploy B is fully deployed and no client references the old column.

**Q: Why must Migration 3 happen after Deploy B?**  
**A:** Deploy A still references `event_date` in the fallback expression, while older code reads it directly. Dropping the column early would make these queries fail with SQL undefined-column errors and surface 5xx responses. All old Pods and other consumers must stop referencing it first.

---

## Bonus — Executed Expand-and-Contract Rename (2 pts)

### Source and deployment changes

**Migration 1 (`fdd871fb2ab5`) — Expand:** Added `events.scheduled_at` as nullable `TIMESTAMPTZ`. Existing five rows initially had null new values. Approximate duration **0.77 seconds**. 5xx counter **0 → 0**.

```python
def upgrade() -> None:
    op.add_column(
        "events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
```

**Deploy A — Fallback reads:** Events SQL selected `COALESCE(e.scheduled_at, e.event_date) AS event_date` in both list/detail queries, and ordered the list with `COALESCE(e.scheduled_at, e.event_date)`. External JSON used its existing `date` field. Events was deployed as local k3d image `quickticket-events:lab12-deploy-a` under two replicas. Both GET endpoints returned 200 after rollout. No runtime event-date write call existed, so dual-write code was not needed for this implementation.

**Migration 2 (`591db45e6159`) — Backfill / constraint:** Populated the new column, then made it NOT NULL. Duration approximately **0.82 seconds**. All five rows had identical `event_date` and `scheduled_at` values, with zero mismatches. 5xx counter **3 → 3**.

```python
def upgrade() -> None:
    op.execute(
        "UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL"
    )
    op.alter_column(
        "events", "scheduled_at",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=False,
    )
```

**Deploy B — New-column-only reads:** Events SQL selected `e.scheduled_at AS event_date` in both queries and sorted by `e.scheduled_at`. The alias is internal; the HTTP response still uses `date`. Updated `app/seed.sql` to create and insert into `scheduled_at`. Both active Events Pods reported `quickticket-events:lab12-deploy-b`, were Ready, and returned HTTP 200 for `/events` and `/events/1`. Events Deployment used `RollingUpdate`, `maxSurge: 1`, `maxUnavailable: 0`, `terminationGracePeriodSeconds: 40`, and `preStop: sleep 10`.

**Migration 3 (`191ef7ac8eff`) — Contract:** Before dropping the old column, made a 14 KB custom-format PostgreSQL backup at `/tmp/lab12-before-contract.dump`. Both Events Pods were confirmed running Deploy B. Applied the migration in approximately **0.77 seconds**. `\d events` confirmed `event_date` absent and `scheduled_at TIMESTAMPTZ NOT NULL`; the old `idx_events_event_date` index was dropped automatically with its column. GET `/events` and `/events/1` both returned 200 afterward. 5xx counter **9 → 9**.

```python
def upgrade() -> None:
    op.drop_column("events", "event_date")


def downgrade() -> None:
    op.add_column(
        "events",
        sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column(
        "events", "event_date",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=False,
    )
```

### Before and after schema evidence

**Before Migration 1:**

```text
Column       Type                         Nullable
id           integer                      not null
name         text                         not null
venue        text                         not null
event_date   timestamp with time zone     not null
total_tickets integer                     not null
price_cents  integer                      not null
email        character varying(255)       (nullable)
Indexes: events_pkey, idx_events_event_date
```

**After Migration 3:**

```text
Column       Type                         Nullable
id           integer                      not null
name         text                         not null
venue        text                         not null
total_tickets integer                     not null
price_cents  integer                      not null
email        character varying(255)       (nullable)
scheduled_at timestamp with time zone     not null
Indexes: events_pkey
```

`alembic current` and `alembic heads` both reported **`191ef7ac8eff`** after the contract migration.

### HTTP 5xx observations and limitation

| Transition | Counter before | Counter after | Observed increase |
|---|---:|---:|---:|
| M1: add nullable column | 0 | 0 | 0 |
| Deploy A: fallback reads | 0 | 2 | +2 |
| M2: backfill and NOT NULL | 3 | 3 | 0 |
| Deploy B: new-column reads | 3 | 9 | +6 |
| M3: drop old column | 9 | 9 | 0 |

A later Prometheus breakdown reported accumulated 5xx of **5 on `/events` (502)**, **1 on `/events/{id}/reserve` (502)**, and **3 on `/health` (503)**. The reported total was 9; at least six failures affected user-facing routes. The Deploy A interval recorded two errors and another error was present by the M2 baseline. Thus **the complete run did not satisfy the zero-5xx bonus criterion**. The precise cause was not conclusively established from the available logs. In particular, timestamp-matching with a broad `grep` of `502`/`503` matched millisecond fragments and did not prove root cause.

These were instant-query snapshots of aggregate counters. If Pods or metric series are restarted/reset or disappear, a raw counter comparison alone is not a rigorous guarantee of zero errors. A production-grade rerun should use per-series counter-aware `increase()` (accounting for resets), a continuous client-side request/error ledger, and timestamps spanning every transition. Do not replace these actual measurements with claimed zero-error results.

### Bonus conceptual answers

**Q: Which single step would cause 5xx if reordered earlier?**  
**A:** Migration 3 (dropping `event_date`). Before Deploy B is fully rolled out, Deploy A or legacy consumers still reference the old column. PostgreSQL would reject those queries, causing errors in the Events service and 5xx upstream.

**Q: How would the backfill be batched for 10 million rows?**  
**A:** Small committed batches, optionally with a short pause, avoid a single long-running transaction and reduce lock pressure. Example pseudocode:

```python
while True:
    begin_transaction()
    ids = select_ids("events", where="scheduled_at IS NULL",
                     order_by="id", limit=10_000, for_update_skip_locked=True)
    if not ids:
        rollback()
        break
    execute("UPDATE events SET scheduled_at = event_date WHERE id = ANY(:ids)", ids=ids)
    commit()
    sleep(0.1)
```

For production, keep writes compatible during backfill, monitor replication lag, ensure each batch commits, verify that no null values remain, and plan constraint validation carefully.

**Q: Why isn't re-adding/backfilling `event_date` sufficient for rollback after Deploy B is live?**  
**A:** A database-only downgrade does not coordinate application Pods or restore compatible write behavior. Existing Deploy B Pods may still be writing only `scheduled_at`; newly restarted legacy Pods may read or write `event_date`, resulting in stale or diverging values. A safe rollback needs an agreed application/database compatibility window (often renewed dual-write), the backfilled columns synchronized, rollout ordering verified for all consumers, and ideally a tested restoration strategy.

---

## Final state and operational caveats

- Events, Payments, Notifications: **2/2 Ready** each; Gateway Rollout: **5/5**; mixedload: **2/2**.
- Four PDBs active, expected allowed disruptions **3 / 1 / 1 / 1** (gateway / events / payments / notifications).
- Final Alembic head: **`191ef7ac8eff`**; `scheduled_at` is the only date column in `events`.
- `app/seed.sql` was updated to use `scheduled_at` both in `CREATE TABLE` and `INSERT`, and includes the `email VARCHAR(255)` field from Lab 9.
- The live Events Deployment currently uses the **local-only image** `quickticket-events:lab12-deploy-b`. The Git manifest (`k8s/events.yaml`) has been restored to an **older GHCR tag** solely as an input for the GitHub Actions retagging step. **Do not apply that old manifest to the migrated database.** The updated CI workflow is configured to build/push Events, Gateway, Payments, and Notifications and rewrite manifest tags on pushes to `feature/lab12` and `main`; successful publishing and retagging must be verified after push, before ArgoCD reconciliation.
- ArgoCD automated synchronization was temporarily disabled (`spec.syncPolicy.automated.enabled: false`) to avoid deploying an incompatible older image while the live database has already dropped `event_date`. Re-enable only after checking the tracked Git revision, published images, and schema compatibility.
- The new `seed.sql` represents the **final schema**. A fresh seeded database must not blindly replay migrations intended for the **old** `event_date` schema. Define and document the bootstrap/stamp strategy, or separate fresh bootstrap schema from upgrade migrations before claiming clean-cluster reproducibility.
- The old date index disappeared when `event_date` was dropped. If production queries need an index for `scheduled_at`, add a new online index on the new column in a further migration.
- Optional HPA observation (12.9) was not performed.

**Outcome:** Main tasks' configuration and online-migration demonstrations completed; all five bonus transitions executed and the final schema verified. The bonus's all-transitions-zero-5xx acceptance criterion remains **unmet** due to observed failures during Events deployments.
