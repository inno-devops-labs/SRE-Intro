# QuickTicket Reliability Review

## 1. SLO Compliance

Source SLOs are from Lab 3:

| SLO | Target | Observed in Lab 10 | Status |
| --- | --- | --- | --- |
| Gateway availability | 99.5% of gateway requests are not 5xx over 7 days | 10 users: 100%; 50 users: 75.09%; 100 users: 33.73% | Pass at light load, fail at 50+ users |
| Gateway latency | 95% of gateway requests complete under 500ms | 10 users p95: 16ms; 50 users p95: 1000ms; 100 users p95: 3200ms | Pass at light load, fail at 50+ users |
| Canary safety | Abort canary when gateway 5xx error rate is above 5% | Lab 7 AnalysisTemplate aborted the bad canary and returned to stable in about 5s | Pass |

Summary: the application is healthy at normal/small traffic, but the current deployment is not safe at the first measured breaking point. The 50-user run exceeds both the 0.5% 5xx budget and the 500ms p99 latency threshold.

## 2. Load Test Results

Locust ran inside the cluster against `http://gateway:8080`, with Redis flushed between runs. The root `locustfile.py` uses the provided task mix and reserves across events 3 and 5.

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 inventory |
| ----: | ---: | --: | --: | --: | --: | -------------: | ------------: |
| 10 | 2/s | 7.64 | 11ms | 16ms | 36ms | 0.00% | 0 |
| 50 | 5/s | 27.20 | 450ms | 1000ms | 1300ms | 24.91% | 0 |
| 100 | 10/s | 32.26 | 1700ms | 3200ms | 3700ms | 66.27% | 0 |

Extra bracketing run from the same cluster:

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 inventory |
| ----: | ---: | --: | --: | --: | --: | -------------: | ------------: |
| 25 | 5/s | 19.23 | 12ms | 56ms | 380ms | 0.00% | 0 |

Breaking point:

```text
First observed breaking point: 50 users, 27.20 RPS
Last healthy measured point:   25 users, 19.23 RPS
Reason: 5xx exceeded 0.5% and p99 exceeded 500ms.
```

The 409 inventory count stayed at 0 in these runs. The errors were real 5xx responses: mainly `GET /events` 502s, `/health` 503s, and reserve-path 500/502s.

## 3. DORA Metrics

Source commands and current state:

```text
git log --oneline main | wc -l                 -> 65
git log --merges --oneline main | wc -l        -> 11
kubectl get rs -l app=gateway ... | wc -l      -> 1
kubectl get analysisrun                        -> No resources found
kubectl get rollout gateway                    -> phase=Healthy, stableRS=79bc69dcf6, ready=5/5
```

The current cluster was recreated, so it only retains the latest healthy gateway ReplicaSet and no AnalysisRun objects. Historical canary data is preserved in Lab 7.

| Metric | Source data | Result | Interpretation |
| --- | --- | --- | --- |
| Deployment Frequency | 9 lab feature PRs merged, 11 merge commits total, 65 commits on `main` | About one deployable change per lab, on demand during the course | Good for a solo course project |
| Lead Time for Changes | GitHub Actions build/push plus ArgoCD poll/sync | Approximately 3-8 minutes from push to cluster sync | Well below 1 day |
| Change Failure Rate | Lab 7 had one bad canary caught by AnalysisTemplate; current cluster has no retained AnalysisRuns | 1 failed change out of 9 lab feature changes, about 11% | Inside the DORA elite 0-15% band, but sample is small |
| Recovery Time | Lab 7 rollout abort to stable traffic: about 5s; Git revert plus ArgoCD sync: about 3 min; Lab 9 Postgres PVC restart RTO: 26s | Seconds to minutes | Well below 1 hour |

## 4. Top 3 Reliability Risks

1. The `events` path is a single-replica bottleneck. At 50 users the gateway starts returning 502/503 while CPU is not exhausted, which points to queueing, dependency timeouts, or DB-pool pressure around `events`. Fix: scale `events`, cap and observe DB pool usage, add HPA, and add per-path latency alerts.
2. Dependency health and readiness can remove too much capacity. Lab 8 showed a Redis outage can make the gateway path unavailable even when read traffic could otherwise work. Fix: separate process readiness from dependency health, keep partial service online, and page on dependency degradation through metrics.
3. State is still concentrated in single Redis and single Postgres instances. The PVC and backup CronJob improved Postgres recovery, but Redis holds and Postgres writes remain single-pod failure domains. Fix: Redis HA or managed Redis for production, regular restore drills, Postgres connection pooling, and eventually managed/replicated Postgres.

## 5. Toil Identification

| Toil | How often it happened | Automation | What it saves |
| --- | --- | --- | --- |
| Re-seeding Postgres after restarts or empty pods with `psql < app/seed.sql` | More than 3 times across Labs 4, 8, and 9 | Use Alembic migrations, idempotent seed Job, PVC, and restore automation | 5-10 minutes per reset and fewer false 502s from missing schema |
| Recreating port-forwards for gateway, Prometheus, Postgres, ArgoCD, and Grafana | More than 3 times across monitoring, GitOps, chaos, and DB labs | Add `make port-forward-*` targets or a small script that starts and labels all forwards | 2-5 minutes per lab session and less context switching |
| Manually watching rollouts and copying status/log commands | More than 3 times in Labs 5-8 | Let Argo Rollouts AnalysisTemplate gate canaries, add notifications, and keep a standard rollout dashboard | 5-15 minutes per deploy and faster failure detection |

## 6. Monitoring Gaps

- Latency alerting is the biggest gap. Lab 8 showed slow payments can hurt checkout while 5xx stays near zero.
- Add p95/p99 alerts per path: `/events`, `/events/{id}/reserve`, and `/reserve/{id}/pay`.
- Add dependency-specific alerts for payments 5xx, Redis availability, Postgres availability, and DB connection pool saturation.
- Add an alert when the `gateway` Service has zero ready endpoints.
- Track 409 separately from 5xx so sold-out inventory does not look like infrastructure failure.
- Add a synthetic checkout probe that reserves and pays periodically, because read-heavy traffic can hide payment failures.

## 7. Capacity Plan

Current capacity:

```text
Safe measured ceiling: 19.23 RPS at 25 users
First broken point:    27.20 RPS at 50 users
Planning target:       about 40 RPS, roughly 2x the safe measured ceiling
```

CPU sampled during the 50-user breaking-point run:

| Component | CPU | Memory | Notes |
| --- | ---: | ---: | --- |
| gateway pod 1 | 6m | 44Mi | Below 200m limit |
| gateway pod 2 | 5m | 45Mi | Below 200m limit |
| gateway pod 3 | 23m | 45Mi | Hottest gateway pod |
| gateway pod 4 | 4m | 44Mi | Below 200m limit |
| gateway pod 5 | 18m | 45Mi | Below 200m limit |
| events | 50m | 45Mi | Hottest service in this scenario |
| payments | 3m | 36Mi | Idle; Locust scenario does not exercise pay |
| redis | 8m | 5Mi | Light CPU, but still a stateful SPOF |
| postgres | 8m | 29Mi | Light CPU at the 50-user sample |

CPU was not hard-throttling any pod at 50 users. The constrained path is still `gateway -> events -> postgres/redis`, because all list and reserve traffic funnels through one `events` pod and its DB/Redis calls. `payments` is idle for this Locust mix.

2x plan:

| Service | Current | 2x plan | Requests / limits | Reason |
| --- | ---: | ---: | --- | --- |
| gateway | 5 replicas | 5 replicas | 50m/64Mi request, 200m/256Mi limit | Gateway CPU headroom is large; keep 5 for rollout granularity |
| events | 1 replica | 3 replicas | 100m/128Mi request, 300m/256Mi limit | Remove the single events bottleneck and spread read/reserve traffic |
| payments | 1 replica | 2 replicas | 50m/64Mi request, 200m/256Mi limit | It is idle in this test, but checkout needs HA |
| redis | 1 replica | 1 replica for lab, 3-node/managed Redis for production | 50m/64Mi request, 200m/256Mi limit | CPU is fine; availability is the risk |
| postgres | 1 replica with PVC | 1 replica with PVC and stricter connection limits | 250m/512Mi request, 500m/1Gi limit | Single Postgres is enough for this lab scale, but DB connections need a cap |

DB connection plan: keep `DB_MAX_CONNS=10` per `events` pod for a total of 30 app connections. If traffic grows beyond this 2x plan, add PgBouncer before increasing events replicas further.

Rough cost at `$5/pod/month`:

```text
Current app runtime: 5 gateway + events + payments + redis + postgres = 9 pods = $45/month
2x app runtime:      5 gateway + 3 events + 2 payments + redis + postgres = 12 pods = $60/month
With Redis HA:       +2 Redis pods = 14 pods = $70/month
Monitoring/backup:   Prometheus + backup-inspector add about $10/month
```

## 8. Proof of Work

Locust ConfigMap was loaded from the root `locustfile.py`:

```bash
kubectl create configmap locustfile \
  --from-file=locustfile.py=locustfile.py \
  --dry-run=client -o yaml | kubectl apply -f -
```

Fresh Lab 10 jobs:

```text
load-10-lab10       -> Completed, 7.64 RPS, 0.00% failures
load-50-lab10-cpu   -> Failed as expected at breaking point, 27.20 RPS, 24.91% failures
load-100-lab10      -> Failed as expected, 32.26 RPS, 66.27% failures
```

Cluster proof after the runs:

```text
gateway rollout: desired=5 current=5 available=5
analysis template: gateway-error-rate present
prometheus: running in monitoring namespace
postgres-backup CronJob: schedule */5 * * * *, last schedule observed
postgres-data and postgres-backups PVCs: Bound
```

Bonus Option B is completed in `submissions/runbooks/quickticket-handbook.md`.
