# QuickTicket Reliability Review

2026-10-12, Walkerino. Isolated `k3d-quickticket-lab78`, Kubernetes v1.33.6-k3s1,
five gateway replicas, one events/payments/Redis/Postgres replica, PostgreSQL PVC,
in-cluster Prometheus. All evidence timestamps UTC (Moscow +3h). These PRs include
prerequisite manifests for independent checkout; CI/GitOps from Lab 5 is historical,
not deployed in this isolated experiment cluster. Lab 9 is a separate PR.

## 1. SLO Compliance

| SLO | Target | Observed | Status |
|---|---|---|---|
| Gateway availability (7 days, Lab 3) | ≥99.5% non-5xx | 10u: 100%; 50u: 99.685%; 100u: 58.277% | Short test passes at 10/50u, fails at 100u; 7-day compliance unproven |
| Latency (7 days, Lab 3) | ≥95% ≤500ms | p95 20/140/730ms at 10/50/100u | Short test fails at 100u; 7-day compliance unproven |
| Lab 10 capacity gate | 5xx ≤0.5%, p99 ≤500ms | 50u: 0.315%, 300ms; 100u: 41.723%, 1100ms | First failing tested level: 100u |

The capacity gate uses p99; the earlier latency SLO uses a 95% threshold. Those
are different objectives. Full-week request-weighted data is unavailable; a
one-minute run cannot establish weekly compliance. Health probes and synthetic
health tasks can dilute aggregate errors, so monitor customer endpoints separately.
[Prior SLO definition](evidence/lab10/prior-lab3-report.md).

## 2. Load Test Results

| Users | Ramp | RPS | p50 ms | p95 ms | p99 ms | 5xx rate | 409 count |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 2/s | 7.73 | 10 | 20 | 33 | 0/463 = 0.000% | 0 |
| 50 | 5/s | 37.07 | 6 | 140 | 300 | 7/2221 = 0.315% | 20 |
| 100 | 10/s | 59.49 | 290 | 730 | 1100 | 1487/3564 = 41.723% | 0 |

[Machine-readable results](evidence/lab10/results.json), [command transcript](evidence/lab10/session.txt),
[10u log](evidence/lab10/lab10-load-10.log), [50u](evidence/lab10/lab10-load-50.log),
[100u](evidence/lab10/lab10-load-100.log). Each run lasted approximately 60s,
including ramp-up. RPS = completed requests / measured elapsed time. Percentiles
are Locust's rounded histogram estimates, aggregated across all tasks and statuses.

[Committed scenario](../locustfile.py): 7:2:1 list/reserve/health, wait 0.5–2s,
reserve events 3/5 in a 3:1 mix. Each Kubernetes Job targets
`http://gateway:8080` through the Service, never a gateway port-forward. Redis
FLUSHDB between runs is restricted to the disposable training cluster. 409 is
marked expected for Locust while the request hook records its actual HTTP status.
5xx and transport failures are counted separately; transport failures were 0.
[Locust request hooks](https://docs.locust.io/en/stable/extending-locust.html).

All five gateway pods have nonzero rates in the
[100u per-pod Prometheus snapshot](evidence/lab10/lab10-load-100-pod-rps.json).
Readiness churn during overload can temporarily remove events endpoints; it is
part of the observed failure, not evidence of absent client traffic.

**Capacity:** highest passing tested point **37.07 RPS at 50u**; first failing
point **59.49 offered/completed RPS at 100u**, with only 2077/3564 responses 200.
That failing throughput is not healthy capacity. The threshold lies between
these sampled levels; an exact boundary needs intermediate tests and repeats.
No 200u run is necessary because 100u already exceeds both stop thresholds.
The tiny fixed course inventory and growing Redis holds affect the scenario;
a long soak needs replenished synthetic inventory and a realistic checkout mix.
This scenario reserves but does not pay, so it does not benchmark payment capacity.

## 3. DORA Metrics

These are **training-history proxies**, not a production DORA score. Source
window: prior Lab 7 session on 2026-10-05, plus Lab 5 CI on 2026-09-27.
[Git history](evidence/lab10/dora-git.txt), [GitHub workflow runs](evidence/lab10/dora-runs.json),
[prior rollout report](evidence/lab10/prior-lab7-report.md),
[raw AnalysisRuns](evidence/lab10/lab7-analysis-both.yaml),
[abort timing](evidence/lab10/lab7-abort-timing.json).

| Metric | Calculation / observation | Scope and limitation |
|---|---|---|
| Deployment frequency | 5 deliberate rollout experiments in the Oct 5 session: manual good, manual bad, multistep good, automatic good, automatic bad | 5/session; excludes bootstrap and recovery restores; not commits/day |
| Lead time | CI runs 100s and 111s + assumed 180s ArgoCD polling = 280–291s (~4.7–4.9min) | Estimate, not measured commit-to-production duration |
| Change failure rate | 2 deliberately bad attempts / 5 experiments = 40% | Fault-injection sample; auto analysis alone: 1 failed / 2 = 50%; neither estimates real production risk |
| Recovery time | Manual abort 2.161s; historical GitOps revert/refresh/sync 5.982s; Lab 9 DB recovery 11.980s | Different boundaries; DB drill is operational RTO, not a release-recovery DORA measurement |

The fresh cluster snapshot has only its bootstrap gateway ReplicaSet and no
AnalysisRuns: [current snapshot](evidence/lab10/dora-cluster.json). A retained
ReplicaSet count is not deployment frequency; Git commit count is not deployed
change count. GitHub runs include an automation workflow failure which is
excluded from service-release failure calculations. The manual hard refresh
recovery is faster than a normal three-minute automatic polling cycle.

## 4. Top 3 Reliability Risks

1. **Events CPU and DB pool exhaustion.** At 100u events used 199m of its 200m
   CPU limit; logs contain `psycopg2.pool.PoolError: connection pool exhausted`.
   Reservation handling borrows a connection and `_get_available` borrows another;
   health failures can leak borrowed connections on exception paths. Increase
   events CPU/replicas as an initial mitigation, then refactor connection ownership,
   ensure rollback/return on every path, add bounded backpressure and measure pool
   use. Increasing max connections alone can overload PostgreSQL.
2. **Shared state has single failure domains.** DB local-path PVC survives pod
   restart but not host loss; Redis holds are ephemeral. Backup PVC shares the
   cluster failure domain and five five-minute snapshots cover only ~20 minutes
   between oldest and newest. Add off-cluster backup copies, WAL/PITR, restore
   testing, backup-age alerts and Redis HA/persistence according to reservation RPO.
3. **Inventory integrity under concurrent checkout.** Availability check and
   reservation creation are separate operations; holds counters do not decrement
   on confirmation or reservation expiry. Under stress this can cause overselling
   or false sold-out responses. Use atomic Redis/Lua hold management, expiry
   cleanup and a transactional DB inventory constraint plus idempotent confirmation.

Risk 1 is directly observed; code-path causes and risks 2/3 are inspection-based
inferences requiring focused verification. [Actual error log](evidence/lab10/events-load-errors.log),
[actual resource configuration](evidence/lab10/resources.yaml).

## 5. Toil Identification

Recurring operational actions visible across the prior reports and this run;
counts below are conservative workflow occurrences, not audited shell history.
Manual effort estimates are planning assumptions, not stopwatch measurements.

| Toil (>3 occurrences) | Frequency / evidence | Automation | Estimated saving |
|---|---|---|---|
| Open/check/recreate port-forwards | Used in Labs 3, 5, 6, 7 and 9; DB pod replacement in Lab 9 broke its forward | Supervised forward process with context check/restart, or run DB tools in-cluster | ~1min per session, ~5min across five sessions |
| Seed/restore after ephemeral DB lifecycle | Labs 4/5/7 bootstrap plus multiple Lab 9 recovery attempts | PVC plus versioned bootstrap/migration Job; recurring backup and restore verification | ~2min per reseed, ≥8min over four occurrences |
| Observe paused canary and collect metrics manually | Lab 7 20/40/60/80/100 gates and separate manual/auto experiments | AnalysisTemplate and evidence runner; keep approval only for meaningful release decisions | ~1min active attention per gate, ≥5min/session |

Some repetition is educational rather than avoidable production toil. The current
runners already automate collection; recurring backups and persistence remove
reseed work without claiming that disaster recovery should run unsupervised.

## 6. Monitoring Gaps

Lab 8 observed Redis-dependent reservations fail while DB-only reads could be
preserved, and slow payments consumed latency budget. Aggregate gateway metrics
alone hide operation-specific impact. [Lab 8 findings](evidence/lab10/prior-lab8-report.md).

- Add per-endpoint availability/latency burn alerts with minimum traffic and explicit
  no-data/scrape alerts; 409 has its own inventory dashboard, never the 5xx numerator.
- Measure active/waiting DB connections, pool acquisition failures and PostgreSQL
  connections/long transactions. An alert on pool exhaustion would have detected
  the actual Lab 10 error; pool health plus request errors are stronger than CPU alone.
- Track CPU throttled seconds and event-loop/worker saturation. `kubectl top` at
  199m suggests saturation but alone does not prove a throttle rate.
- Add successful reservation→payment→confirmation synthetic probes, Redis held
  inventory consistency and reservation expiry/confirmation cleanup checks.
- Track backup timestamp, success/failure, volume free space and restore-drill age;
  a CronJob schedule alone gives no guarantee that recent usable backups exist.

## 7. Capacity Plan

**Task 2 measured headroom:** sampled ~45s into each run. At the failing 100u
level, CPU: gateway **38/38/41/45/37m**, events **199m**, payments **8m**,
Postgres **127m**, Redis **6m**, Locust **60m**. Events memory 55Mi, DB 44Mi.
[Raw kubectl top](evidence/lab10/lab10-load-100-cpu.txt). Gateway has substantial
CPU headroom; payments is idle because the chosen task mix never pays.

Target 2× **highest tested healthy throughput** = **74.14 RPS**, not twice the
failing throughput. Initial plan for a new capacity validation:

| Service | Replicas | CPU request / limit per pod | Memory request / limit |
|---|---:|---|---|
| Gateway | 5 | 50m / 200m | 64Mi / 256Mi |
| Events | 3 | 300m / 500m | 128Mi / 256Mi |
| Payments | 1 | 50m / 200m | 64Mi / 256Mi |
| PostgreSQL | 1 | 250m / 500m | 512Mi / 1Gi |
| Redis | 1 | 50m / 200m | 64Mi / 256Mi |
| Prometheus / Grafana | 1 each | 100m / 500m each | 256Mi / 512Mi each |

Three events replicas provide CPU margin and reduce per-pod pool contention;
keep DB_MAX_CONNS=10 initially (30 app connections total), reserve capacity for
health/admin/backups and verify actual `max_connections`. There is no PgBouncer
in the current manifests: connections go directly to PostgreSQL. Add a pooler
if larger replica counts or transaction concurrency require it. Remove nested
connection acquisition/leaks before relying on horizontal scaling; benchmark the
plan rather than assuming replicas yield linear capacity.

Redis single-pod CPU is adequate for this throughput sample, but single-pod HA
is not adequate for a stronger availability target. Replica/Sentinel or a managed
service is an availability change rather than a CPU capacity requirement.
Payments needs its own checkout load test before making capacity claims.

Using the lab's **assumed $5/pod/month**, 13 always-on service/monitoring pods
cost **$65/month** vs 11 pods ($55) today, **+$10/month**. Optional three-pod
Redis instead of one adds $10. This is illustrative arithmetic, not a cloud quote;
it excludes cluster/controller, backup Job/inspector, storage, network, loadgen
and managed-service costs. Validate 50/75/100u repeatedly with the new limits,
then run a replenished-inventory checkout soak and measure pool use/throttling.

## Bonus — Operational handbook and validation

[Two-page-style SRE handbook](runbooks/quickticket-handbook.md) covers architecture,
GitOps deployment, monitoring, incidents/escalation and backup/restore. Markdown
has no fixed printed pagination; it is a concise operator handoff document.

Python compilation and Kubernetes server-side dry-run passed. All three actual
Jobs completed; a post-load API smoke returned 200 and Redis was cleared in the
training cluster. [Recovery output](evidence/lab10/recovery.txt). Lab 9's CronJob
was suspended after its seven-run check to preserve the measured backup listing;
committed manifest is enabled. No fault-injection environment settings were added.

- [x] Task 1: 10/50/100u, observed breaking point, all seven review sections.
- [x] Task 2: real per-pod CPU, replica/resource/connection plan and cost arithmetic.
- [x] Bonus: SRE handbook.
