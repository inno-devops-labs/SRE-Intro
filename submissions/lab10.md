# QuickTicket Reliability Review

## 1. SLO Compliance

The course SLOs are 99.5% availability and 95% of requests completing in under 500ms.

| SLO | Target | Observed at breaking point | Status |
|---|---:|---:|---|
| Availability (non-5xx) | 99.5% | 99.84% in the first 100-user run (6 real 5xx / 3857 requests) | Met |
| Latency under 500ms | 95% | p95 550ms and p99 780ms in the first 100-user run | Not met |

The 100-user run reached the breaking point because p99 exceeded the 500ms limit. The 409 responses were inventory conflicts, not service failures, and were counted separately from 5xx responses.

## 2. Load Test Results

Locust ran inside the Kubernetes cluster against `http://gateway:8080`, so the gateway Service distributed traffic across all five gateway replicas. Redis was flushed between runs.

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 inventory |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 2/s | 7.66 | 25ms | 110ms | 200ms | 0% | 0 |
| 50 | 5/s | 35.75 | 23ms | 150ms | 260ms | 0% | 48 |
| 100 | 10/s | 64.43 | 140ms | 550ms | 780ms | 0.16% | 193 |

The 100-user run produced six real 5xx responses: three `502` responses from `GET /events` and three `500` responses from reservation requests. The remaining 193 Locust failures were expected `409 Conflict` responses caused by ticket contention.

The repeated 100-user run was worse under the same workload: `61.62 RPS`, p50 `160ms`, p95 `830ms`, and p99 `1200ms`. This confirms that 100 users is beyond the latency SLO even though the CPU snapshot did not reach the configured limits.

### Breaking point

```text
Breaking point: 100 concurrent users
Observed load: approximately 64 RPS
Trigger: p99 latency 780ms > 500ms
```

## 3. DORA Metrics

These are lab-scale measurements derived from the repository history and ArgoCD/Argo Rollouts evidence, not a long-term production baseline.

| Metric | Observed value | Source/interpretation |
|---|---:|---|
| Deployment frequency | 8 gateway-affecting history entries from Sep 17-26 | `git log --all -- k8s/gateway.yaml`; some entries are CI/image or rollback changes |
| Lead time for changes | Approximately CI build time plus ArgoCD polling, under a few minutes for the lab flow | Git push followed by ArgoCD refresh/sync evidence |
| Change failure rate | 1 intentionally broken gateway deployment in the observed Lab 5 exercise | Broken image reached `Progressing`, then was reverted successfully |
| Mean time to recovery | Approximately 4m42s from broken commit to revert commit; ArgoCD returned Healthy about 30s after refresh | Git timestamps and ArgoCD `Synced/Healthy` evidence |

The history shows the value of immutable image tags, GitOps reconciliation, and Git revert as the recovery mechanism.

## 4. Top 3 Reliability Risks

1. **Single-node stateful dependencies.** Redis and PostgreSQL are single replicas. PostgreSQL now has a PVC and automated dumps, but Redis still has no replica or failover path. Use a managed/replicated Redis service and a PostgreSQL operator or managed database for production.
2. **Latency degrades before the error-rate alert fires.** At 100 users, p99 crossed 500ms while the 5xx ratio remained below 0.5%. Add latency SLO alerts and dashboards, and inspect database connection wait time and downstream request duration.
3. **The gateway depends synchronously on downstream services.** Events, Redis, PostgreSQL, and payments are on the request path. Add explicit timeouts, bounded retries only for safe failures, queueing for asynchronous work, and load shedding before downstream saturation becomes user-visible latency.

## 5. Toil Identification

| Toil | Repetition observed | Automation proposal | Benefit |
|---|---|---|---|
| Recreating port-forwards after Pod/service changes | Repeated across migration and recovery work | Add a Makefile target or documented helper script that checks and recreates the port-forward | Fewer connection mistakes during operations |
| Manually importing images because the cluster cannot pull through the corporate TLS proxy | Repeated for curl, Git, BusyBox, Alpine, and Locust images | Add an offline image preload script or internal registry mirror | Removes repeated image troubleshooting and setup time |
| Manually checking rollout, backup, restore, and health state | Repeated across Labs 5-9 | Add verification Jobs and CI checks for rollout health, backup validity, and API smoke tests | Makes failures visible and repeatable instead of operator-dependent |

## 6. Monitoring Gaps

- The existing alerting focused on error rate and SLO burn; it did not reliably alert on latency before the 5xx threshold was crossed.
- A latency alert should track gateway p95/p99 and separate `/events`, `/reserve`, and `/pay` paths.
- The `events_db_pool_size` query returned no active series during the Lab 8 combined experiment, so connection-pool saturation was not directly observable.
- 409 inventory conflicts should be tracked separately from 5xx so expected product contention does not hide service failures.
- Backup success needs an alert based on the age of the newest dump and a periodic restore verification, not only CronJob completion.

## 7. Capacity Plan

At the 100-user breaking point, the sampled CPU was:

| Service | Observed CPU sample | Configured limit | Interpretation |
|---|---:|---:|---|
| Gateway, busiest Pod | 94m | 300m | Highest observed service usage, but not CPU-limited |
| Events | 23m | 300m | Low CPU; investigate DB/Redis wait and pool behaviour |
| Payments | 24m | 300m | Low CPU in this scenario |

For 2x the observed traffic, the initial capacity plan is:

- Gateway: increase from 5 to 10 replicas; keep the canary strategy and add latency-based analysis.
- Events: increase from 1 to 2 replicas after verifying safe reservation concurrency and database pool limits.
- Payments: increase from 1 to 2 replicas because it is stateless and on the checkout path.
- PostgreSQL: keep one writer with the PVC, but move to a managed/operated PostgreSQL setup before production; size connection limits and poolers from measured wait time rather than CPU alone.
- Redis: replace the single Pod with a replicated or managed Redis deployment with a documented failover path.
- Rough incremental small-cloud estimate: 5 additional gateway Pods plus one events and one payments Pod, approximately `$35/month` at the course assumption of `$5/pod/month`, excluding managed database, Redis, storage, and network costs.

## Artifacts

- `locustfile.py`
- `submissions/lab10.md`
- `submissions/lab9.md`
- `k8s/postgres.yaml` with PVC-backed PostgreSQL
- `k8s/backup-cronjob.yaml`

## Checklist

- [x] Task 1 done - load tests, DORA, toil, and reliability review
- [x] Task 2 done - CPU measurements and a concrete 2x capacity plan
- [ ] Bonus Task done - five-minute walkthrough or SRE handbook
- [x] Title is clear (`feat(labN): <topic>` style)
- [x] No secrets or large temporary files committed
- [x] Submission file at `submissions/lab10.md` exists
