# Lab 12 — Advanced Kubernetes Resilience

Executed locally on `k3d-quickticket` on 2026-10-16. Experiment timestamps below are UTC. Task 1, Task 2 and the live expand-and-contract bonus are complete. The ungraded HPA observation was skipped.

## Scope, safety and source of truth

Events owns the database/API date contract; seed.sql is the only supplied date insertion path. The application has no runtime event-date writer. Orders, gateway consumers and the public JSON `date` key remain unchanged. All live traffic used the existing high-capacity event 6, preserving original event inventory.

Initial state: six events, 4,749 orders, revision `0002_events_email`, PostgreSQL on the existing data PVC. A custom-format safety dump was saved both inside PostgreSQL's pod and on the host:

```text
/tmp/lab12-before-1792155189278.dump
```

The archive was inspected and restored into a disposable database. All four new revisions passed upgrade → downgrade to `0002_events_email` → upgrade, preserving all 4,749 original orders. No live database downgrade was performed. Evidence: `lab12-safety.json`, `lab12-isolated-migrations.txt`.

ArgoCD auto-sync remained disabled, as in prior labs; local image tags were built/imported into k3d, not published to GHCR. Moodle submission is a separate final step.

## Task 1 — Multi-replica failover, PDB and topology

### Final replicas and budgets

```text
NAME            READY   UP-TO-DATE   AVAILABLE
events          2/2     2            2
payments        2/2     2            2
notifications   2/2     2            2

ROLLOUT   DESIRED   CURRENT   UP-TO-DATE   AVAILABLE
gateway   5         5         5            5
```

`k8s/pdb.yaml` contains four `policy/v1` budgets selecting the corresponding `app` pod label. Final live output:

```text
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
events-pdb          1               N/A               1
gateway-pdb         2               N/A               3
notifications-pdb   N/A             1                 1
payments-pdb        1               N/A               1
```

Events also has a ten-second preStop and a 40-second termination grace period. Its Deployment uses `maxUnavailable: 0`, `maxSurge: 1` so the two code deploys do not intentionally reduce its serving replica count.

### Real eviction rejection

I tightened events-pdb to `minAvailable: 2`, waited for zero allowed disruptions, then POSTed a `policy/v1` Eviction to the pod subresource through an owned temporary kubectl proxy. Actual HTTP status was **429**, with this JSON body:

```json
{
  "kind": "Status",
  "apiVersion": "v1",
  "metadata": {},
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

`DisruptionBudget` is nested in `details.causes` on this server, not the top-level reason. The budget was restored to 1 and the proxy stopped in `finally`. The raw response and tightened-budget status are in the `evict` record of `lab12-transitions.jsonl`. No node was drained/cordoned. Direct pod deletion in the failover test bypasses PDB; the separate rejected eviction proves API enforcement.

### Spread constraint

The live gateway template contains:

```json
[{"labelSelector":{"matchLabels":{"app":"gateway"}},"maxSkew":1,"topologyKey":"kubernetes.io/hostname","whenUnsatisfiable":"ScheduleAnyway"}]
```

All five final gateway pods were on `k3d-quickticket-server-0`; their actual names, IPs and placement are in `lab12-final-state.txt`. This single-node environment does not prove node-failure resilience.

With three healthy replicas and `minAvailable: 1`, at most **two** voluntary evictions can be admitted together, assuming no existing disruption. With five gateway replicas, `minAvailable: 2` permits three while retaining two serving pods; this is a maintenance/availability tradeoff, not a measured capacity guarantee for two pods. On three equally eligible nodes, balanced placements for five/seven pods are **2/2/1** and **3/2/2**. `ScheduleAnyway` is a soft preference, not a guarantee of those placements under competing constraints. See [Kubernetes topology spread semantics](https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/).

### Initial failure and corrected proof

The reference `/health` readiness check reintroduced dependency-coupled readiness from the earlier labs. During the first coordinated delete, Prometheus recorded `/health` 503s and clients observed connection failures plus failed reservations. Those observations are retained, not presented as zero-downtime success. The first restart's aggregate counter also changed as old pods disappeared; a decreasing sum is not evidence of zero errors.

I restored process-only `/metrics` readiness, retaining `/health` for dependency diagnostics and the faster two-second readiness interval. After that rollout's automatic AnalysisRun succeeded and all five replicas became Healthy, the coordinated delete/restart were repeated at the **same mixedload rate and original CPU limits**, without retries added to the load generator.

| Corrected experiment | Replacement-ready time | Prometheus 5xx total before → after | Recorded client responses | Errors |
|---|---:|---:|---:|---:|
| Delete one gateway and one events pod together | 8.526s | 0 → 0 | 254 × 200 | 0 |
| Restart all five gateway pods | 21.285s | 0 → 0 | 386 × 200 | 0 |

The corresponding records have `value: process-readiness`. One-minute and three-minute 5xx increases also stayed zero. `lab12-client-summary.json` contains both failed initial windows and successful corrected windows; `lab12-client.log` retains actual client lines, including the initial HTTP `000` transport failures. Receipts count reads/payments; successful reservations are implicit in successful payment chains, and every logged reservation failure is classified separately.

## Task 2 — Graceful restart and concurrent index

### Actual gateway shutdown/readiness configuration

```yaml
terminationGracePeriodSeconds: 40
containers:
  - name: gateway
    command: [uvicorn]
    args: ["main:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-graceful-shutdown", "30"]
    lifecycle:
      preStop:
        exec:
          command: [sh, -c, "sleep 10"]
    readinessProbe:
      httpGet:
        path: /metrics
        port: 8080
      periodSeconds: 2
      failureThreshold: 1
```

Liveness remains on `/metrics`. The deliberate readiness-path difference from the reference avoids the observed dependency cascade; it does not remove the requested readiness probe. Termination marks endpoints not Ready independently of making `/health` fail. The hook runs before SIGTERM and uses part of the 40-second budget; Uvicorn then has an explicit 30-second graceful-shutdown timeout. See [Kubernetes pod termination](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination).

The installed plugin was not available, so the real Argo restart used its documented API operation: patch `.spec.restartAt` to an RFC3339 UTC timestamp, then wait for all original pod UIDs to be replaced by five Ready pods. This is **not** a Deployment restart or a pod-template annotation workaround. Argo restart replaces pods in the existing ReplicaSet rather than repeating a new-image canary rollout; see [Argo restart documentation](https://argoproj.github.io/argo-rollouts/features/restart/). The template updates separately passed `gateway-674f9fc4bc-11-2` automatic canary analysis.

### Concurrent index migration

`0003_event_date_index`:

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index("idx_events_event_date", "events", ["event_date"], postgresql_concurrently=True, if_not_exists=True)

def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("idx_events_event_date", table_name="events", postgresql_concurrently=True, if_exists=True)
```

Live Alembic CLI execution took **0.517s**, with 5xx **0 → 0**, and 128 recorded client responses all 200 in its observed window. The live schema showed `idx_events_event_date` on `event_date`, with `pg_index.indisvalid=true`; the abridged schema is in `lab12-index-schema.txt`. Six rows cannot demonstrate a large-table lock-duration advantage.

The runner now uses one transaction per migration and session-scoped `lock_timeout=3s`, `statement_timeout=30s`; these limits survive the autocommit block. An autocommit block can commit preceding work, so live upgrades use explicit revision targets with application deploy gates, never `upgrade head` across all five transitions. `IF NOT EXISTS` does not repair an invalid leftover concurrent index; inspect validity after interruption.

**Why CONCURRENTLY?** A normal PostgreSQL index build holds a SHARE lock that blocks writes, but normally allows reads; it is not an ACCESS EXCLUSIVE lock blocking every query. On a 10M-row table, waiting writers and accumulated backlog can cause an outage. Concurrent construction permits ordinary reads/writes during the build, at the cost of extra work and waiting phases, and cannot run inside a transaction block. This corrects the stronger lock claim in the reading; source: [PostgreSQL 17 CREATE INDEX](https://www.postgresql.org/docs/17/sql-createindex.html).

## Expand-and-contract sketch — three migrations, two deploys

1. **M1:** add nullable `scheduled_at TIMESTAMPTZ`, leaving `event_date` intact. Old readers remain compatible with the added column.
2. **Deploy A:** prefer `COALESCE(scheduled_at, event_date)` in both SELECTs and ORDER BY. A real date writer must write both columns; QuickTicket has no such runtime writer, so no fake write endpoint was added. No seed/administrative date inserts ran during this migration window.
3. **M2:** backfill only NULL `scheduled_at` values, verify parity, then make it NOT NULL. Wait for A to be fully deployed before this gate if writers exist. A real application switching to new-column-only INSERTs must also relax the old NOT NULL requirement here and coordinate all writers; this read-only-date lab does not exercise that additional writer path.
4. **Deploy B:** read only `scheduled_at`, preserve the public API `date` key, and switch any real writers to the new field. Update the fresh database seed. Wait until every A pod, including terminating ones, is gone.
5. **M3:** remove `event_date` only after that gate and any required rollback window. Build a replacement index on `scheduled_at` before dropping the old column, which automatically removes the old-column index.

Dropping the old column **before B is fully rolled out** breaks both legacy reads and A's COALESCE: a missing-column reference fails SQL parsing even when the other COALESCE argument is populated. B deployed before backfill is also unsafe because its reader assumes a populated new field. The supplied sequence is safe here because event dates have no concurrent runtime writer; generic dual-writer migrations need the additional writer/constraint coordination described above.

## Bonus — Executed under live checkout traffic

Two original mixedload workers remained running with their 0.3s pause throughout the sequence. No resource limits were increased and traffic was not stopped between gates.

| Transition | UTC window, including observation | CLI/deploy time | 5xx total before → after | Client responses |
|---|---|---:|---:|---:|
| M1: `0004_add_scheduled_at` | 13:03:27–13:03:38 | 0.481s | 0 → 0 | 112 × 200 |
| Deploy A: `quickticket-events:lab12-a` | 13:03:54–13:04:19 | 14.717s | 0 → 0 | 230 × 200 |
| M2: `0005_backfill_scheduled_at` | 13:05:12–13:05:23 | 0.586s | 0 → 0 | 152 × 200 |
| Deploy B: `quickticket-events:lab12-b` | 13:05:42–13:06:09 | 14.402s | 0 → 0 | 340 × 200 |
| M3: `0006_drop_event_date` | 13:06:42–13:06:54 | 0.619s | 0 → 0 | 164 × 200 |

Across the **whole continuous interval**, including the gaps between gates, **2,246 read/payment receipts were all 200**, with no logged reservation failures. After A, all six rows still had scheduled_at=NULL, proving fallback reads were exercised. M2 then produced six matching, non-NULL dates; `lab12-backfill.txt` records them. Before M3, both live events pods used B and no A pod remained. The actual image-derived A→B SQL diff is in `lab12-deploy-a-to-b.patch`.

Baseline and final scalar 5xx values are both **0**. Actual comparison:

```bash
diff -u submissions/lab12-5xx-before.txt submissions/lab12-5xx-after.txt
# no output; exit status 0
```

Raw API snapshots, including their different timestamps, are in `lab12-bonus-counters.json`. I compared normalized counter values, not timestamped API JSON blobs. Aggregate counters can decrease when pods disappear; independent client receipts and one-/three-minute increases were checked too. The observer also records target health and positive user traffic, so an empty lazy 5xx series is not mistaken for an unobserved system.

### Actual bonus upgrade bodies

```python
# 0004_add_scheduled_at
def upgrade() -> None:
    op.add_column("events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))

# 0005_backfill_scheduled_at
def upgrade() -> None:
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)

# 0006_drop_event_date
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index("idx_events_scheduled_at", "events", ["scheduled_at"], postgresql_concurrently=True, if_not_exists=True)
    op.drop_column("events", "event_date")
```

The final schema is:

```text
id            integer                  NOT NULL
name          text                     NOT NULL
venue         text                     NOT NULL
total_tickets integer                  NOT NULL
price_cents   integer                  NOT NULL
email         character varying(255)    nullable
scheduled_at  timestamp with time zone NOT NULL

events_pkey PRIMARY KEY (id)
idx_events_scheduled_at btree (scheduled_at), valid=true
```

There is no `event_date` column. `lab12-final-state.txt` contains the full actual `\d events` output. Its old index was replaced deliberately, not silently lost.

### Production backfill and rollback answers

For 10M rows, use a stable key, small transactions, bounded locks, progress checkpoints and lag/latency throttling:

```text
cursor = saved_cursor
while true:
    ids = next_ids_after(cursor, limit=10000)
    if not ids: break
    BEGIN
    UPDATE events SET scheduled_at=event_date WHERE id IN ids AND scheduled_at IS NULL
    COMMIT
    cursor = max(ids); save_checkpoint(cursor)
    throttle_on_replication_lag_and_latency()
```

Verify no NULLs remain before enforcing the constraint. For large tables, use staged constraint validation and short lock timeouts rather than blindly running a blocking full-table validation. A single UPDATE retains locks on its updated rows and generates WAL/bloat until commit; it does not necessarily block every unrelated write. `WHERE scheduled_at IS NULL` avoids overwriting already-migrated values; safety also depends on all active writers obeying the dual-write contract.

M3 downgrade restores/backfills `event_date`, NOT NULL and its index, and removes the replacement index. That is schema reconstruction, not a sufficient production rollback guarantee. B may keep writing while a backfill runs; fields can diverge, extra writers/jobs can lag, and application expectations must match constraints. Safe rollback requires coordinated writes/dual-write or equivalent synchronization, verified parity, bounded DDL, and a compatible application rollout. Restoring the pre-lab dump alone would lose newer orders. In this drill dates were not edited at runtime, and the complete downgrade/upgrade cycle was tested only on a restored copy.

## Fresh bootstrap, data preservation and cleanup

`app/seed.sql` now creates the final scheduled_at schema, email column and replacement index, and inserts dates using scheduled_at. Because the historical baseline represents the old schema, a verified fresh final seed is stamped at `0006_drop_event_date`, not upgraded through old event_date migrations. Existing legacy databases follow the gated migration/deploy path instead. `migrations/README.md` documents both cases and prohibits reseeding a populated database.

A separate fresh database bootstrapped successfully and the B image's startup/health/list handlers returned healthy and five seeded events. The first ad-hoc pod failed because Kubernetes service links supplied a URL-shaped REDIS_PORT; the corrected test explicitly set 6379, matching the real Deployment. Both initial setup output and passing output are retained in the fresh-bootstrap artifacts.

Final verification: six events, **9,210 orders**, head `0006_drop_event_date`; comparison with the restored pre-lab copy found **zero missing or changed original orders** and all original event-date values preserved in scheduled_at. PostgreSQL data PVC remains Bound. Health reports events/payments/notifications OK, circuit CLOSED; final actual reserve→pay→confirm returned 200 and the API still exposes `date`.

Mixedload and smoke pods were removed, test holds flushed, both temporary databases dropped, observer/proxy and the owned port-forward stopped. The original safety dump remains recoverable on the host and in the PostgreSQL pod; local A/B images remain cached. Intended PDBs, replica counts, topology/shutdown settings and the final schema/code remain applied. ArgoCD auto-sync remains disabled; nothing was posted externally.

## Evidence and validation

- `lab12-transitions.jsonl`: raw before/after Prometheus snapshots, exact actions, replacement pod identities, timings and eviction response.
- `lab12-metrics.jsonl`: continuous Kubernetes/Prometheus observations, including failed initial setup and corrected phases.
- `lab12-client.log`, `lab12-client-summary.json`: actual mixedload receipts and per-window classification; initial failures are not hidden.
- `lab12-final-state.txt`, `lab12-data-preservation.json`, `lab12-smoke.txt`: final readiness, schema, data and end-to-end verification.
- Python syntax, migration history, server-side manifest dry-runs and Compose configuration passed. Index validity and isolated upgrade/downgrade/upgrade were checked against PostgreSQL 17.11.
- Initial health-readiness canary analysis and final process-readiness canary analysis both completed Successful; no failed analysis was silently promoted.

## Checklist

- [x] Task 1 — two replicas per backend, four PDBs, real rejected eviction, live topology constraint and corrected zero-error failover.
- [x] Task 2 — preStop, explicit shutdown budget, process readiness, zero-error Argo restart, concurrent index migration and five-step design sketch.
- [x] Bonus — all three migrations and two code deploys executed under continuous load; zero 5xx across the five transitions; old column removed and fresh seed verified.
- [ ] Optional 12.9 — HPA observation skipped; ungraded.
