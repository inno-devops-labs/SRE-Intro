# QuickTicket Reliability Review

## 1. SLO Compliance

| SLO | Target | Observed | Status |
|---|---|---|---|
| Availability | 99.5% non-5xx responses | 100% at `10u`, 95.98% at `50u`, 47.81% at `100u` | Failed above `50u` |
| Latency | 95% of requests under `500ms` | `p95=23ms` at `10u`, `p95=510ms` and `p99=860ms` at `50u` | Failed at `50u` |
| Error budget | 35 failed requests per week (from Lab 3 SLO) | One `100u` run produced `1429` failed requests in `60s` | At risk |

## 2. Load Test Results

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 2/s | 7.71 | 14ms | 23ms | 50ms | 0.00% | 0 |
| 50 | 5/s | 34.47 | 10ms | 510ms | 860ms | 4.02% | 15 |
| 100 | 10/s | 45.75 | 690ms | 1900ms | 4500ms | 52.19% | 0 |
| Breaking point | 5/s | 34.47 | 10ms | 510ms | 860ms | 4.02% | 15 |

The system was healthy at `10u`. The first real breaking point was already `50u`, because both conditions were violated there: `5xx > 0.5%` and `p99 > 500ms`.

## 3. DORA Metrics

| Metric | My data | Result |
|---|---|---|
| Deployment Frequency | `3` gateway ReplicaSets in cluster history, `71` commits on `main` | For this project I deployed regularly during the labs, about once per lab step |
| Lead Time for Changes | CI build time + ArgoCD poll interval | About `4-6 min` |
| Change Failure Rate | `1` bad rollout aborted in Lab 7 out of `3` observed rollout revisions | About `33%` |
| Mean Time to Recovery | `kubectl argo rollouts abort gateway` returned traffic in `2495ms`; GitOps revert path is about `3-4 min` | Fast with rollout abort, slower with Git revert |

## 4. Top 3 Reliability Risks

1. Postgres can come back empty after restart, and then `events` table is missing, so the whole app starts returning `502/500`. I saw this before the load test and had to restore `app/seed.sql`. The fix is PVC-backed Postgres plus automated restore checks.
2. The capacity ceiling is low. At only `50u` the system already broke the latency and availability targets. The fix is to scale `gateway` and `events`, profile the slow path, and add better backpressure.
3. Read traffic depends too much on internal dependencies. In Lab 8 Redis failure made the whole gateway unhealthy, and at `100u` even `/health` returned many `503`. The fix is to separate read-path readiness from reserve-path dependencies and add graceful degradation.

## 5. Toil Identification

| Toil | How often | How to automate | What it would save |
|---|---:|---|---|
| Re-seeding Postgres with `kubectl exec ... < app/seed.sql` after DB resets or disasters | 4+ times | Init job or startup migration/seed job | Less manual recovery and fewer broken baselines |
| Manual rollout watching with `kubectl argo rollouts get/status` and checking logs by hand | 4+ times | Better AnalysisTemplate and automatic promotion/abort rules | Less operator time during deploys |
| Manual Prometheus queries and `kubectl logs` during chaos/load experiments | 5+ times | A dashboard for golden signals plus ready alerts for latency, 5xx, Redis, and DB health | Faster diagnosis and less copy-paste work |

## 6. Monitoring Gaps

- I wanted an alert for DB schema/data readiness. The thing that really broke before load testing was not pod health, but missing `events` table.
- I wanted a per-path latency alert for `/events` and `/reserve`, not only a general 5xx alert.
- I wanted a Redis dependency alert that shows when reserve-path dependencies are down before the whole gateway becomes useless.
- I wanted a dashboard that separates `409` inventory exhaustion from real `5xx` failures during load.

## 7. Capacity Plan

- Current ceiling: about `34-35 RPS` (`50u` run). After that the service already violates both latency and availability SLOs.
- For 2x traffic, I would scale `gateway` from `5` to `10` replicas, `events` from `1` to `3`, and `payments` from `1` to `2`.
- I would keep one Postgres instance but only with PVC and connection-pool monitoring. Redis can stay single-pod for this student setup, but it needs clear alerts and backup/restart procedures.
- Rough cost estimate with `$5/pod/month`: current app tier is about `9` pods = `$45/month`; the 2x plan is about `17` pods = `$85/month`.
