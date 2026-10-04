# QuickTicket Reliability Review

## 1. SLO Compliance

| SLO | Target | Observed | Status |
|---|---:|---|---|
| Availability | 99.5% of gateway requests without 5xx over 7 days | The 10-user, 60-second Locust run had 0 5xx in 460 requests. The 50-user boundary run had 188 5xx in 1,901 requests (9.89%). | Not established for 7 days. The short test is evidence for a load window, not a weekly SLO. |
| Latency | 95% of gateway requests below 500 ms | At 10 users, Locust aggregate p95 was 24 ms. At the first 50-user run it was 740 ms. | Met only in the 10-user test window; violated at 50 users. |

The SLO targets come from the Lab 3 availability and latency definitions. The in-cluster Prometheus gateway target was up at the baseline check on `2026-10-04T21:42:01.4033414Z`; it did not expose events and payments under the same job selector, so this review does not claim three in-cluster application scrape targets.

## 2. Load Test Results

All runs used `locustio/locust:2.43.4` inside Kubernetes against `http://gateway:8080`, not a gateway port-forward. The ConfigMap was created from the committed root `locustfile.py`; it uses reads, health requests, and reservations spread across events 3 and 5. Redis was flushed before each run as required for the test setup. Latency values are milliseconds.

| Users | Ramp | Actual users | Runtime | RPS | p50 | p95 | p99 | 5xx | 409 inventory |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 2/s | 10 | 60 s | 7.74 | 9 | 24 | 270 | 0 / 460 (0.00%) | 0 (0.00%) |
| 50 | 5/s | 50 | 60 s | 31.84 | 18 | 740 | 2100 | 188 / 1901 (9.89%) | 0 (0.00%) |
| 100 | 10/s | 100 | 60 s | 47.47 | 630 | 1500 | 2800 | 1656 / 2837 (58.37%) | 0 (0.00%) |

Locust logs explicitly showed `All users spawned` for 10, 50, and 100 users. The 10-user log showed 460 requests and no failures. For the first 50-user run, the matching error report was 104 `/events` `502` + 22 `/health` `503` + 48 event-3 reserve `500` + 9 event-5 reserve `500` + 2 event-3 reserve `502` + 3 event-5 reserve `502` = 188 5xx; `188 / 1901 = 9.89%`. For the 100-user run, it was 1,087 `/events` `502` + 130 `/health` `503` + 252 event-3 reserve `500` + 90 event-5 reserve `500` + 69 event-3 reserve `502` + 28 event-5 reserve `502` = 1,656 5xx; `1656 / 2837 = 58.37%`. There were no 409s in either initial failure report. The CSV snapshots were emitted immediately during shutdown; the table deliberately uses each matching final Locust summary and error report rather than mixing the earlier CSV snapshot with the final totals.

The 50-user boundary was repeated while capturing capacity samples. It confirmed the result rather than treating a noisy single run as a ceiling:

```text
50-user repeat: 1,927 requests, 232 Locust failures
5xx: 228 / 1,927 = 11.83%
409: 4 / 1,927 = 0.21%
p50=120 ms, p95=640 ms, p99=810 ms, RPS=32.30
```

The embedded CSV from the repeat separately listed the four `409 Conflict` responses and the 500/502/503 responses. Thus Locust's aggregate failure percentage was not used as the 5xx percentage.

**Breaking point.** The last passing level was 10 users at 7.74 RPS. The first failing level was 50 users at 31.84 RPS: both 5xx rate exceeded 0.5% and p99 exceeded 500 ms. This is a measured range, not an exact capacity ceiling; no 200-user run was needed because 50 already failed clearly.

Prometheus confirmed that all five gateway pods received traffic during the 100-user window. `sum by(pod)(increase(gateway_requests_total[90s]))` returned positive values for all five pods (165.18 to 329.31 requests in that queried window). The window is shorter than the whole test and is used as distribution evidence, not as the Locust total.

## 3. DORA Metrics

| Metric | Definition and period | Real source and calculation | Result | Limitation |
|---|---|---|---|---|
| Deployment frequency | Retained Argo CD history, 2026-10-04 11:41:02Z to 20:00:33Z | Application `quickticket` retained 10 automated sync records (history IDs 7–16) over 8 h 19 m 31 s. | 1.20 retained syncs/hour | Argo keeps only 10 records here; the set includes course experiments and is not a complete production deployment history. |
| Lead time for changes | Git commit to Argo CD deployed time for one tracked manifest change | `a072078` was committed at `2026-10-04T20:00:03Z`; Argo deployed that revision at `20:00:33Z`. | 30 s | This change reused an existing image and excludes CI build/package time. |
| Failed AnalysisRun fraction (not Change Failure Rate) | Failed automated analyses divided by AnalysisRuns retained in cluster | `kubectl get analysisrun` returned 6 runs: 4 `Successful`, 2 `Failed`. | `2 / 6 = 33.3%` retained failed-run fraction | This is not a DORA Change Failure Rate: `gateway-74d8846df-5-2` failed after a PostgreSQL restart because the `events` table was absent, while `gateway-69957fc8bb-6-2` was the intentional Lab 7 bad-canary experiment. AnalysisRun retries and measurements are not deployments, and the retained list is not complete deployment history. |
| Recovery time | Incident start to confirmed checkout | Lab 9 ephemeral PostgreSQL recovery: 57.449 s. Lab 9 PVC PostgreSQL recovery: 32.130 s. | Two controlled values; mean 44.790 s | These are database recovery drills, not an organization-wide MTTR. |

## 4. Top 3 Reliability Risks

1. **Events database connection pool exhaustion.** The 50/100-user Logs contain `psycopg2.pool.PoolError: connection pool exhausted`; gateway then returned 500/502/503. Add a pool-usage metric and queue/backpressure, then test two Events replicas with an explicit PostgreSQL connection budget.
2. **Single PostgreSQL instance remains a dependency risk.** The Lab 9 PVC avoided a restore after a pod restart, but it is still one local-path volume and one database process. Keep tested backups, test restore regularly, and use a managed/replicated database before relying on node-level availability.
3. **Slow or unreachable dependencies can be under-observed.** Lab 8 showed slow payments and the Redis probe cascade. Gateway metrics do not count transport failures that never reach gateway, and the current dashboard lacks a usable database-pool utilization series. Add dependency latency, connection-pool, and client-side transport-error metrics and alerts.

## 5. Toil Identification

| Manual activity | Evidence/frequency | Automation | Expected saving |
|---|---|---|---|
| Final port-forward plus health/checkout verification | Evidenced in final checks for Labs 4, 5, 7, 8, 9, and this lab (at least six exercises). | An in-cluster synthetic checkout CronJob with Prometheus metrics and a dashboard link. | Removes repeated local port-forward setup and produces comparable evidence. |
| Redis test-state clearing before load/chaos runs | Performed before the 10, 50, 100, and 50-repeat Locust jobs (four runs). | Isolated test Redis/database or a test Job setup step that records/reset only test keys. | Avoids manual commands and prevents stale holds from changing results. |
| Watching and deciding canary progression | Lab 7 required manual observation at 20%, 40%, 60%, and later promotion/abort actions. | Keep the AnalysisTemplate gate and add notification on AnalysisRun result. | Replaces repeated terminal watching with an auditable automatic decision. |

## 6. Monitoring Gaps

- `events_db_pool_size` was unavailable in the Lab 8 Prometheus query, yet Lab 10 logs proved pool exhaustion. Pool in-use, waiting, max, and acquisition failures are the most important missing signals.
- A slow-but-successful payment needs a latency alert; error-only alerts do not page for that condition.
- Gateway request metrics do not represent transport failures that never reach a gateway process.
- Redis dependency behavior needs operation-level metrics. The Lab 8 probe fix preserved reads while dependency health was degraded, so Ready status alone is not the full customer view.
- A local PostgreSQL PVC helps pod restarts but says nothing about node/PVC loss. Backup freshness, restore duration, and backup Job failure alerts are needed.

## 7. Capacity Plan

### Measured evidence

At the 50-user repeat (`2026-10-04T21:51:06.5282479Z`), live `kubectl top` values were:

| Component | CPU | Memory |
|---|---:|---:|
| Gateway pods | 24m, 43m, 38m, 44m, 25m | 44–48Mi each |
| Events | 127m | 64Mi |
| Payments | 10m | 41Mi |
| PostgreSQL | 156m | 31Mi |
| Redis | 5m | 9Mi |
| Locust runner | 52m | 39Mi |

At the 100-user run, Events reached 184m CPU and PostgreSQL 170m; both were below their configured 200m limits in that sample. No CPU-throttling or memory-pressure metric was captured, so CPU saturation is not asserted. The confirmed bottleneck is the Events application pool, not the Locust runner: it had only 52m CPU at the 50-user sample and logs showed `PoolError` from Events.

### Forecast for approximately 2× the first-failure traffic

This is a plan, not a claim that replicas exactly double throughput. Re-test it with the same Locust scenario after implementation.

| Component | Current | Proposed | Request / limit | Rationale |
|---|---:|---:|---|---|
| Gateway Rollout | 5 | 10 | 50m/64Mi request; 200m/256Mi limit per pod | Keep modest per-pod headroom and spread connection pressure. |
| Events | 1 | 2 | 100m/128Mi request; 300m/256Mi limit per pod | Split request concurrency and expose pool metrics. |
| Payments | 1 | 2 | 50m/64Mi request; 200m/256Mi limit per pod | Avoid a single checkout dependency. |
| PostgreSQL | 1 | 1 primary, HA/managed follow-up | 250m/512Mi request; 500m/512Mi limit | Size for the observed 170m sample and protect the service with a connection budget. |
| Redis | 1 | 2-node replicated/Sentinel or managed equivalent | 25m/64Mi request; 200m/256Mi limit | Current usage is low, but a single Redis pod is an availability risk. |

With two Events replicas and `DB_MAX_CONNS=15`, the Events maximum is 30 PostgreSQL connections. Reserve at least 20 connections for PostgreSQL maintenance, backup, and other clients; this remains within PostgreSQL's usual 100-connection default, but must be validated against the actual server setting before rollout. The proposed runtime requests total about **1,100m CPU and 1,664Mi memory**. At the exercise assumption of **$5 per pod/month**, 10 gateway + 2 Events + 2 Payments + 1 PostgreSQL + 2 Redis pods cost roughly **$85/month**, excluding monitoring, storage, network, and managed-database costs.

## Final state and acceptance checklist

Load Jobs and the temporary `locustfile` ConfigMap were deleted after logs/CSV evidence was captured. No Lab 7 or Lab 8 load generators remained.

```text
final_health_utc=2026-10-04T21:54:30.9751449Z
gateway HTTP 200: healthy, events=ok, payments=ok
events HTTP 200: postgres=ok, redis=ok
payments HTTP 200: failure_rate=0.0, latency_ms=0
final reserve HTTP 200 and confirmed pay HTTP 200
Argo CD: target=feature/lab10, Synced, Healthy
gateway Rollout: desired=5, current=5, available=5
quickticket-backup: schedule */5 * * * *, suspend=False
```

- [x] 10/50/100 in-cluster load tests and a real first failing level
- [x] DORA calculations with sources and limitations
- [x] All seven review sections, risks, toil, monitoring gaps, and plan
- [x] Task 2 live resource samples at the failure boundary and numeric 2× plan
- [x] Bonus Option B handbook
