# Lab 12 — Advanced Kubernetes Resilience

## Task 1 — Multi-replica failover and PDBs

Events, Payments, and Notifications were scaled to two replicas. Mixedload ran during the pod-kill test. I deleted one Gateway pod and one Events pod; replacement pods became Ready. Prometheus showed 0 5xx before the test. The after-test query returned an empty vector, meaning it had no matching series in that one-minute window.

Evidence: [final deployments and rollout](lab12-evidence/final-state.txt), [5xx before pod kill](lab12-evidence/5xx-before-pod-kill.json), [5xx after pod kill](lab12-evidence/5xx-after-pod-kill.json).

Created four PDBs: Gateway `minAvailable: 2`, Events and Payments `minAvailable: 1`, Notifications `maxUnavailable: 1`. Tightening Events to `minAvailable: 2` blocked an eviction with HTTP 429 and cause `DisruptionBudget`; I then restored it to `minAvailable: 1`.

Evidence: [PDB status](lab12-evidence/pdbs.txt), [eviction rejection](lab12-evidence/pdb-eviction-test.txt).

Added the Gateway topology spread constraint with `maxSkew: 1`, hostname topology, and `ScheduleAnyway`. The single-node k3d cluster placed all five pods on its only node, as expected.

Evidence: [topology constraint and placement](lab12-evidence/topology-spread.txt).

With three Gateway replicas and `minAvailable: 1`, at most two can be evicted while satisfying the budget. With five replicas, `minAvailable: 2` permits up to three disruptions while retaining two available replicas. Across three nodes, `maxSkew: 1` allows placement `2/2/1` for five pods and `3/2/2` for seven.

## Task 2 — Graceful shutdown and concurrent index

Configured a 10-second `preStop` sleep, a 40-second termination grace period, and a readiness probe with a two-second period and failure threshold of one. The Argo Rollout restart completed Healthy. The Prometheus 5xx query returned 0 before and after.

Evidence: [shutdown settings](lab12-evidence/gateway-graceful-shutdown.txt), [5xx before restart](lab12-evidence/5xx-before-gateway-restart.json), [5xx after restart](lab12-evidence/5xx-after-gateway-restart.json).

Added `idx_events_event_date` with `CREATE INDEX CONCURRENTLY`, using Alembic's `autocommit_block()` because PostgreSQL disallows concurrent index creation inside a transaction. The migration completed successfully; the index appeared in `\d events`. The saved 5xx values before and after the index migration were both 0.

Evidence: [migration run](lab12-evidence/index-migration-run.txt), [index in schema](lab12-evidence/events-index.txt), [5xx before](lab12-evidence/5xx-before-index-migration.txt), [5xx after](lab12-evidence/5xx-after-index-migration.txt).

On a large table, ordinary index creation can block writes while it runs. `CONCURRENTLY` reduces that impact, allowing normal inserts, updates, and deletes during index construction.

### Expand-and-contract design

1. Migration 1: add nullable `scheduled_at`.
2. Deploy A: read `COALESCE(scheduled_at, event_date)` and, where runtime writes exist, write both columns. This service has no runtime event-date insert path; seed data is handled separately.
3. Migration 2: backfill `scheduled_at` from `event_date`, then set it `NOT NULL`.
4. Deploy B: read only `scheduled_at`. I preserved the existing API response field name with `scheduled_at AS event_date`; the seed now inserts into `scheduled_at`.
5. Migration 3: drop `event_date` after Deploy B is fully rolled out.

Migration 3 before Deploy B would break Deploy A: its query still references `event_date`, causing requests to fail.

## Bonus — Executed expand-and-contract rename

Applied all three migrations and both Events deploys under mixedload. The migration files are [M1: add nullable column](../migrations/versions/3abd6eb9fff7_add_events_scheduled_at_column.py), [M2: backfill and set NOT NULL](../migrations/versions/6c15edff59d9_backfill_events_scheduled_at.py), and [M3: drop old column](../migrations/versions/6c2154cb9ed7_drop_events_event_date.py). Deploy diffs: [Deploy A](lab12-evidence/deploy-a-diff.txt), [Deploy B and seed](lab12-evidence/deploy-b-diff.txt).

Evidence: [schema after M1](lab12-evidence/events-after-migration-1.txt), [rows and schema after M2](lab12-evidence/events-after-migration-2.txt), [schema before M3](lab12-evidence/events-before-migration-3.txt), [schema after M3](lab12-evidence/events-after-migration-3.txt). After M3, `event_date` was absent and `scheduled_at` was `NOT NULL`.

Prometheus's cumulative 5xx sum was 0 at the initial baseline, 0 after M1 and Deploy A, 2 after M2, remained 2 through Deploy B, and was 4 after M3. Thus the cumulative values did not remain unchanged across the full sequence. However, the later 10-minute `increase` query returned 0 for `/health`, `/pay`, and `/events`; Gateway and Events log searches returned no matching error lines. The reason for the cumulative-counter discrepancy is unresolved, so I do not claim zero 5xx delta across all five transitions.

Evidence: [initial baseline](lab12-evidence/expand-contract-5xx-baseline.txt), [after M1](lab12-evidence/expand-contract-after-m1.txt), [after Deploy A](lab12-evidence/expand-contract-after-deploy-a.txt), [after M2](lab12-evidence/expand-contract-after-m2.txt), [before Deploy B](lab12-evidence/expand-contract-before-deploy-b.txt), [after Deploy B](lab12-evidence/expand-contract-after-deploy-b.txt), [after M3](lab12-evidence/expand-contract-after-m3.txt), [last 10-minute increase query](lab12-evidence/expand-contract-5xx-after-m3-window.json).

For a 10-million-row backfill, process small batches in separate transactions:

1. Select up to 10,000 rows where `scheduled_at IS NULL`.
2. Update only those rows from `event_date`.
3. Commit the batch.
4. Sleep briefly to reduce sustained database pressure.
5. Repeat until no rows remain; then verify no NULLs before setting `NOT NULL`.

Re-adding and backfilling `event_date` in M3's downgrade is not by itself sufficient for a safe application rollback. Deploy B writes only `scheduled_at`; a rollback needs compatible application code and a period of dual-write or equivalent synchronization so writes made during the rollback window cannot leave the two columns divergent. Keep the old column until the rollback window has passed.

## Optional HPA — CPU-based scaling

Created an HPA targeting the Gateway Rollout with a 70% CPU target, minimum 5 and maximum 12 replicas. Under a Locust run configured for 200 users, a spawn rate of 20 users/second, and 120 seconds, CPU rose as high as 194% of target and the HPA scaled Gateway from 5 to 10 and then 12 replicas.

The Locust job completed its timed run but exited with code 1: 5,236 of 6,490 requests failed (80.68%). The failure report included rate-limit responses, Gateway 5xx/timeouts, and connection errors. The HPA scaling was observed, but the application did not sustain this load successfully.

After collecting the observation, I deleted the Job and live HPA and returned the Rollout to five replicas. The HPA manifest remains in the repository.

Evidence: [HPA before load](lab12-evidence/hpa-before-load.txt), [replica and CPU observations](lab12-evidence/hpa-observation.txt), [Locust summary](lab12-evidence/hpa-load-test.txt), [final state](lab12-evidence/final-state-after-hpa.txt), [5xx total after load](lab12-evidence/hpa-final-5xx-total.json), [5xx increase during the load window](lab12-evidence/hpa-5xx-during-load.json).

## Recovery state

Payments and Notifications fault injection and temporary Payments latency were reset during Lab 11. Gateway rate limiting was restored to 10 RPS. After the HPA exercise, I deleted the live HPA and load Job and set the Gateway Rollout back to five replicas. See the final cluster snapshot for the current health and pod state.
