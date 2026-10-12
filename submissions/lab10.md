# QuickTicket Reliability Review

Tested on 12 October 2026, UTC. The existing single-node k3d cluster has five gateway pods, one each of Events, Payments, Redis and PostgreSQL, plus the data/backup PVCs from [Lab 9](https://github.com/inno-devops-labs/SRE-Intro/pull/638). No new application infrastructure was installed.

## 1. SLO Compliance

The [Lab 3 targets](https://github.com/nikitadev-work/SRE-Intro/blob/feature/lab3/submissions/lab3.md) are 99.5% non-5xx availability over seven days and at least 95% of requests within 500ms. These short tests assess individual load windows; they cannot establish seven-day compliance.

| SLO / test gate | Target | Observed | Status |
|---|---|---|---|
| Availability | ≥99.5% non-5xx | 10u: 100%; 50u: 95.74%; 100u: 46.35% | Pass at 10u; fail at 50u/100u |
| Latency | ≥95% within 500ms | p95: 16ms / 560ms / 1900ms at 10u/50u/100u | Pass at 10u; fail at 50u/100u |
| Lab 10 capacity gate | p99 ≤500ms and 5xx ≤0.5% | First failure: 50u, p99 780ms and 4.26% 5xx | Capacity limit reached |
| Seven-day availability | ≥99.5% | No continuous seven-day measurement from this session | Not established |

The p99 capacity gate is stricter than the original p95 latency objective. Expected inventory conflicts are not counted as 5xx. Percentiles are Locust histogram estimates across all completed requests, including errors.

## 2. Load Test Results

[`locustfile.py`](../locustfile.py) preserves the supplied 7:2:1 read/reserve/health mix, 0.5–2s wait time and reservation selection `[3, 3, 3, 5]`. A small [Locust event listener](https://docs.locust.io/en/2.43.4/extending-locust.html) prints status counts separately from Locust's combined failure total.

Jobs used `locustio/locust:2.43.4`, the supplied `labs/lab10/locust-runner.yaml` pattern, and `http://gateway:8080` **inside the cluster**. Each run lasted 60 seconds including ramp-up. Redis was flushed before each run and after testing. No payment requests or database order writes are part of this scenario.

| Users | Ramp | RPS | p50 (ms) | p95 (ms) | p99 (ms) | 5xx error rate | 409 count |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 2/s | 7.78 | 11 | 16 | 22 | 0.00% (0/465) | 0 |
| 50 | 5/s | 34.47 | 13 | 560 | 780 | 4.26% (88/2064) | 29 |
| 100 | 10/s | 45.36 | 740 | 1900 | 3800 | 53.65% (1455/2712) | 0 |

Status counts from the final `LAB10_SUMMARY` lines:

| Users | Requests | 200 | 409 | 500 | 502 | 503 | 504 | Transport failures |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 465 | 465 | 0 | 0 | 0 | 0 | 0 | 0 |
| 50 | 2064 | 1947 | 29 | 25 | 59 | 4 | 0 | 0 |
| 100 | 2712 | 1257 | 0 | 284 | 1025 | 145 | 1 | 0 |

Run completion checks: 09:02:20 (10u), 09:03:25 (50u), 09:04:32 (100u). The failed Kubernetes Job status at 50u/100u reflects Locust's nonzero exit on request failures; both completed their full duration and produced summaries.

**First tested breaking point: 50 users at 34.47 RPS.** Both limits failed. The last passing tested level was 10 users at 7.78 RPS, so the precise boundary is somewhere between those tested workloads. 34.47 RPS is not a safe operating target. The required 100-user run confirmed degradation; a 200-user run was unnecessary.

During the 50-user run, Events logged **72** `PoolError: connection pool exhausted` exceptions. Example:

```text
2026-10-12T09:02:29.665757912Z psycopg2.pool.PoolError: connection pool exhausted
```

The Events pool has ten connections. The reserve path holds one while `_get_available()` borrows another, increasing connection pressure. Readiness also queries dependencies. At 100 users, Events CPU reached 199m against a 200m limit. This points to Events pool/concurrency and CPU as the limiting area, rather than Payments.

A one-minute per-pod request-rate sample after the 50-user run showed all five gateway pods receiving traffic: **8.13, 6.63, 7.04, 7.85 and 5.39 RPS**. This Prometheus sample includes `/health` traffic and uses a different window from Locust, so it is distribution evidence rather than an exact cross-check of its RPS.

After testing, load Jobs and their ConfigMap were deleted. Redis holds were cleared; `/health` returned healthy, `/events` returned 200, all application pods were ready, and ArgoCD remained Synced/Healthy on `feature/lab9`.

## 3. DORA Metrics

The deployment/failure cohort is the five planned gateway change experiments from **5 October**, recorded in [Lab 7](https://github.com/inno-devops-labs/SRE-Intro/pull/571). Recovery-only reapplications and setup revisions are excluded from both numerator and denominator.

| Rollout revision | Change | Outcome |
|---:|---|---|
| 7 | Manual good canary | Promoted |
| 8 | Broken Events URL | Manual abort |
| 10 | Multi-step good canary | Promoted |
| 11 | Good automatic canary | Analysis Successful; promoted |
| 12 | Bad automatic canary | Analysis Failed; aborted |

| Metric | Result | Source / calculation |
|---|---|---|
| Deployment frequency | 5 planned changes / 1 active lab day = **5/day** | Cohort above; this is one observed lab day, not a long-term production rate |
| Lead time for changes | **222s (3m42s)** for one measured release | App commit `85be3a9` at 2026-09-28 08:06:22 → ArgoCD sync of manifest commit `053bdb8` at 08:10:04 |
| Change failure rate | **2/5 = 40%** | One manual abort + one failed automatic analysis; intentionally injected failures |
| Failed-change recovery time | **1.28s** manual canary abort; **7.21s** Git revert | Lab 7: abort to stable-only ready endpoints; [Lab 5](https://github.com/inno-devops-labs/SRE-Intro/pull/519): revert start to Synced/Healthy |

The [CI run](https://github.com/nikitadev-work/SRE-Intro/actions/runs/36395386978) ran from 08:06:26 to 08:07:25; its image-tag commit was made at 08:07:19. The retained ArgoCD history records `053bdb8` synchronized at 08:10:04 without a manual refresh. This gives measured lead time rather than assuming the polling interval.

The current cluster retains **six** gateway-labelled ReplicaSets and two AnalysisRuns (**one Successful, one Failed**). ReplicaSet retention is not a deployment ledger, and the two analyses exclude the earlier manual abort. Counting upstream course commits would also inflate deployment frequency. Recovery timings have different endpoints and too few samples for a meaningful overall mean.

## 4. Top 3 Reliability Risks

1. **Events overload and pool exhaustion.** Already causes 5xx at 50 users; CPU reaches its cap at 100. Reuse the existing connection in the reserve path, bound request concurrency, monitor pool use/errors, and scale Events with an explicit database connection budget.
2. **Single-node data and backup storage.** Lab 9 prevented pod-loss data loss, but both PVCs remain on the same node. Copy backups off-node, alert on backup age, and regularly test restoration; use WAL/PITR if a five-minute recovery-point interval is insufficient.
3. **Redis failure spreads to event browsing.** [Lab 8](https://github.com/inno-devops-labs/SRE-Intro/pull/572) showed Redis loss making Events unready, so reads eventually failed too. Separate process readiness from dependency diagnostics, fail reservations quickly, and preserve reads. Before multiple Events replicas, make inventory check-and-hold atomic to avoid overselling races.

## 5. Toil Identification

Counts below are conservative counts of repeated operator-invoked checks/steps documented in prior experiments. Time savings are planning estimates, not stopwatch measurements.

| Repeated work | Observed frequency | Automation | Estimated saving |
|---|---|---|---|
| Inspect health, logs and API responses after each recovery | More than 6 recovery checks across Labs 6, 8 and 9 | One smoke command for dependencies plus a disposable reserve/pay flow, with cleanup and a clear exit status | About 1 minute per check |
| Watch canary state and copy pause snapshots | Four explicit 20/40/60/80% observations in Lab 7, plus manual good/bad canaries | Use the AnalysisTemplate for gating; automatically collect rollout and analysis evidence | About 3 minutes of active attention per rollout |
| Restore normal settings after fault drills | Four Lab 8 scenarios: latency, Redis, combined failure, repeated latency | Wrap each experiment in cleanup that reapplies Git configuration and verifies health; GitOps self-healing is already part of the recovery | About 2 minutes per drill and fewer forgotten fault settings |

## 6. Monitoring Gaps

- **Slow successful checkout:** Lab 8 payment p99 reached 2.485s while 5xx stayed zero. The installed `QuickTicketPaymentLatencyHigh` rule (>1s for 30s) caught the repeated injection. Add notification routing; a firing rule alone is not delivered to an operator.
- **Events saturation:** the current in-cluster Prometheus scrapes gateway metrics, not the Events pool metric. Scrape Events, count pool-acquisition errors, and alert on pool saturation plus request latency. Add CPU throttling and pod readiness/restart metrics; `kubectl top` is only a sampled average.
- **Endpoint-specific availability:** aggregate health/read traffic can hide payment errors. Add payment-specific error and latency alerts and a synthetic reserve/pay check. This Locust workload does not exercise payment processing.
- **Data protection and SLO history:** alert when no successful backup is newer than ten minutes, on failed jobs and PVC space. Retain continuous SLI history. The separate Compose Grafana rules from Lab 6 are not evidence of equivalent Kubernetes alert coverage.

## 7. Capacity Plan

### Measured headroom

`kubectl top pods` during the first failing level (50 users), at **09:03:14 UTC**, with the load-generator row omitted:

```text
NAME                        CPU(cores)   MEMORY(bytes)
events-7bb679f9b8-hdwrw     146m         48Mi
gateway-5fb7f75577-4nr86    59m          42Mi
gateway-5fb7f75577-b97zq    39m          47Mi
gateway-5fb7f75577-hjtxn    51m          48Mi
gateway-5fb7f75577-m42sv    48m          44Mi
gateway-5fb7f75577-sqz67    41m          41Mi
payments-cd749857b-wdqt2    10m          41Mi
postgres-6cccc67985-4l2h8   78m          29Mi
redis-6fcfb5475d-tq78n      11m          13Mi
```

At this sample, Events used 146/200m (73% of its CPU limit), versus 39–59m per gateway and 10m for Payments. This sample alone does not prove CPU saturation: the pool errors are direct evidence of the earlier bottleneck. At 100 users, **09:04:19**, Events reached **199/200m**, gateway pods used **52–76m**, Payments **12m**, PostgreSQL **117m**, and Redis **9m**. Events becomes CPU-constrained too; Payments only handles health checks in this test.

### Proposed configuration for 2× traffic

Target roughly **69 RPS** (2 × 34.47), after correcting the Events connection handling. This is a proposal requiring a new load test, not a measured capacity guarantee.

| Service | Replicas now → planned | CPU request / limit per pod | Memory request / limit per pod | Reason |
|---|---|---|---|---|
| Gateway | 5 → 5 | 100m / 250m | 64Mi / 256Mi | Twice the 50u peak of 59m is about 118m; current replica count has room |
| Events | 1 → 2 | 200m / 500m | 128Mi / 256Mi | Split concurrency and raise CPU headroom; budget up to 20 DB connections per pod |
| Payments | 1 → 1 | 50m / 200m | 64Mi / 256Mi | No payment-processing capacity conclusion from this scenario; test checkout separately |
| Redis | 1 → 1 | 50m / 200m | 64Mi / 256Mi | CPU is low; one pod is sufficient for this throughput test, but remains a failure point |
| PostgreSQL | 1 → 1 | 100m / 500m | 128Mi / 512Mi | More CPU/working-set room for two Events pools; preserve the data PVC |

There is **no external pooler/PgBouncer** in the current deployment: each Events process has a psycopg2 pool connecting directly to PostgreSQL. The server reports `max_connections=100`. Two pools capped at 20 give at most 40 application connections, leaving headroom for probes, backups and administration. Reusing connections and bounding concurrency comes before blindly increasing the pool; database CPU and query latency must be retested.

Redis replication/failover is a separate availability improvement, not needed merely to double this measured CPU load. Multiple application replicas require atomic reservation handling; extra replicas alone do not fix inventory correctness.

Using the lab's **$5 per persistent application pod/month** assumption: current 9 pods cost **$45/month**; planned 10 cost **$50/month**, an increase of **$5/month**. This excludes monitoring/controllers, transient backup/load Jobs, storage and traffic charges. Planned aggregate requests are **1.1 CPU and 832Mi memory**; verify node headroom before applying. The plan was not applied to the user's cluster.

Bonus: [QuickTicket SRE handbook](runbooks/quickticket-handbook.md) covers architecture, the current GitOps promotion flow, monitoring, incident response, and backup/restore.
