# QuickTicket Reliability Review

## Task 1 — Load Testing & Reliability Review

### 1. SLO Compliance

The load tests were executed with Locust running inside the Kubernetes cluster against `http://gateway:8080`, so traffic went through the `gateway` Service and was distributed across the five gateway replicas.

| SLO | Target | Observed | Status |
| --- | ---: | ---: | --- |
| Gateway 5xx/error rate at low load | Below 0.5% | 0.00% at 10 users | Met |
| Gateway p99 latency at low load | Below 500 ms | 150 ms at 10 users | Met |
| Gateway 5xx/error rate at breaking-point load | Below 0.5% | 58.41% at 50 users | Not met |
| Gateway p99 latency at breaking-point load | Below 500 ms | 5300 ms at 50 users | Not met |

The system met the reliability target at 10 users. It crossed the lab breaking-point criteria at 50 users because both failure rate and p99 latency exceeded the thresholds.

### 2. Load Test Results

Redis was flushed between runs with `redis-cli FLUSHDB`, as required by the lab. The table below records the official in-cluster Locust scenario from `locustfile.py`.

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 inventory |
| ----: | ---: | --: | --: | --: | --: | -------------: | ------------: |
| 10 | 2/s | 7.59 | 17 ms | 57 ms | 150 ms | 0.00% | 0 |
| 50 | 5/s | 15.03 | 1600 ms | 3700 ms | 5300 ms | 58.41% | 0 |
| 100 | 10/s | 15.99 | 5100 ms | 6900 ms | 12000 ms | 98.30% | 0 |

Breaking point: 50 users at approximately 15.03 RPS. This was the first tested level where `5xx` failures exceeded 0.5% and p99 latency exceeded 500 ms.

The failures were system failures, not inventory exhaustion. The Locust error reports contained `500`, `502`, `503`, `504`, and connection refused errors. They did not report `409 Conflict` responses. The database inventory also still had tickets for the target events before the high-load runs:

```text
 id |         name         | total_tickets | ordered | remaining 
----+----------------------+---------------+---------+-----------
  1 | Go Conference 2026   |           100 |      26 |        74
  2 | SRE Meetup           |            30 |       0 |        30
  3 | Cloud Native Summit  |           500 |       0 |       500
  4 | Python Workshop      |            25 |       0 |        25
  5 | Kubernetes Deep Dive |            80 |       0 |        80
(5 rows)
```

Representative Locust outputs:

```text
load-10 aggregated: 450 requests, 0 failures, 7.59 req/s, p50 17 ms, p95 57 ms, p99 150 ms.
load-50 aggregated: 904 requests, 528 failures (58.41%), 15.03 req/s, p50 1600 ms, p95 3700 ms, p99 5300 ms.
load-100 aggregated: 942 requests, 926 failures (98.30%), 15.99 req/s, p50 5100 ms, p95 6900 ms, p99 12000 ms.
```

The `events` service log showed the main failure mode:

```text
psycopg2.pool.PoolError: connection pool exhausted
```

This points to the `events` service database connection pool as the weakest link under load.

### 3. DORA Metrics

| Metric | Source | Observed value | Interpretation |
| --- | --- | ---: | --- |
| Deployment frequency | Gateway ReplicaSet creation timestamps | 11 rollout ReplicaSets from 2026-10-04 08:54:03 UTC to 2026-10-04 11:19:17 UTC | 11 rollout revisions across a 2h25m lab rollout window, or about 4.5 rollout revisions/hour during the exercise. This is a lab exercise rate, not a production steady-state rate. |
| Lead time | Latest CI run duration plus ArgoCD poll interval | ~3m50s | Latest CI took about 50s; adding the 3-minute ArgoCD poll gives about 3m50s. |
| Change failure rate | AnalysisRun phase counts | 3 Failed, 2 Successful | 3 of 5 AnalysisRuns failed, so the lab proxy CFR was 60%. Some failures were intentionally induced during the canary exercises, so this is not representative of an organic production-team CFR. |
| Recovery time | Lab 7 abort/rollback observation | Observed by the next Rollout status check; exact seconds not available from retained evidence | After the bad canary was aborted, the next captured Rollout state showed SetWeight 0, ActualWeight 0, Updated 0, Ready 5, Available 5, with the stable image serving. The retained evidence does not include a precise timestamp for that recovered status, so no exact DORA recovery-time duration is claimed from this evidence. |
| Canary failure-detection window | Failed canary AnalysisRun timestamps from Lab 7 Bonus | 80s | The bad canary AnalysisRun started at 2026-10-04 11:20:05 UTC and completed failed at 2026-10-04 11:21:25 UTC. This measures automated canary failure detection, not recovery time. |

Raw DORA evidence:

```text
kubectl get rs -l app=gateway -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.creationTimestamp}{"\n"}{end}' | sort -k2
gateway-c987d78d9    2026-10-04T08:54:03Z
gateway-7d94f9f46f   2026-10-04T09:09:55Z
gateway-759f6cdcb    2026-10-04T09:42:07Z
gateway-747fb7dbf    2026-10-04T09:56:37Z
gateway-7546455ff4   2026-10-04T10:02:28Z
gateway-66cbd58496   2026-10-04T10:54:16Z
gateway-5dfb746c8b   2026-10-04T10:58:13Z
gateway-6d69f5f9fb   2026-10-04T11:00:31Z
gateway-8bc999bd     2026-10-04T11:04:14Z
gateway-7bfbbf78c8   2026-10-04T11:11:10Z
gateway-74d4cc445f   2026-10-04T11:19:17Z

kubectl get analysisrun -o jsonpath='{.items[*].status.phase}' | tr ' ' '\n' | sort | uniq -c
      3 Failed
      2 Successful

git log --oneline main | wc -l
82
```

The `82` commits are repository-history context. Commit count is not used as Deployment Frequency, because Deployment Frequency should count deployed changes over a time window.

Latest CI evidence for lead time:

```text
workflowName=CI
headBranch=main
displayTitle=docs(lab9): refine reliability report and manifests
createdAt=2026-10-10T09:14:37Z
updatedAt=2026-10-10T09:15:27Z
conclusion=success
```

Canary failure-detection source data from the failed AnalysisRun:

```text
name: gateway-74d4cc445f-12-2
creationTimestamp: "2026-10-04T11:20:05Z"
status:
  startedAt: "2026-10-04T11:20:05Z"
  completedAt: "2026-10-04T11:21:25Z"
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  phase: Failed
```

Observed stable-serving Rollout state after abort:

```text
Status:          Degraded
Message:         RolloutAborted: Rollout aborted update to revision 12: Step-based analysis phase error/failed
Step:            0/6
SetWeight:       0
ActualWeight:    0
Images:          quickticket-gateway:v2 (stable)
Updated:         0
Ready:           5
Available:       5
```

### 4. Top 3 Reliability Risks

1. **Events service database connection pool exhaustion.** At 50 users, the `events` service exhausted its psycopg2 connection pool and started returning 5xx through the gateway. This directly broke event listing, reservation, and health checks. The fix is to introduce proper connection-pool sizing, request timeouts, and a pooler such as PgBouncer, then load-test the new limit.
2. **Single `events` replica.** Gateway has five replicas, but `events` has one pod. The gateway can fan out more concurrent requests than the single `events` pod and its DB pool can handle. The fix is to scale `events` horizontally and make sure DB connection limits are sized for the total replica count.
3. **Single Redis and single Postgres path.** Lab 9 added persistence for Postgres, but the runtime path still depends on single Redis and Postgres instances. The fix is to define backup/restore ownership, monitor DB/Redis saturation, and plan a replicated or managed database/Redis setup for production.

### 5. Toil Identification

| Toil item from Labs 1-9 | Frequency observed | Automation proposal | Expected saving |
| --- | ---: | --- | --- |
| Re-running Kubernetes status checks after every experiment | More than 3 times | Add a `make status` or script that prints pods, rollouts, services, jobs, and key endpoints. | Saves several manual commands per lab step. |
| Recreating load or traffic generators manually | More than 3 times | Keep reusable Job templates for Locust, mixed load, and checkout-focused tests with variables for users/ramp/duration. | Reduces copy/paste errors and speeds repeatable experiments. |
| Manually collecting Prometheus queries for reports | More than 3 times | Add scripts for common golden-signal queries and table extraction. | Produces consistent evidence and reduces report formatting time. |

### 6. Monitoring Gaps

During the Lab 8 chaos experiments and this load test, the most useful missing signals were dependency and pool-level saturation metrics. Error-rate alerts catch the failure after users are affected, but they do not explain why the service failed.

Useful additions:

- `events` database connection pool usage and pool-exhaustion count.
- Gateway p95/p99 latency alerts, not only 5xx alerts.
- Per-service 5xx panels for gateway, events, payments, Redis, and Postgres dependency errors.
- DB connection count and query latency.
- Redis latency and connection errors.

The alert that would have caught the actual Lab 10 failure earliest is an `events` pool exhaustion alert or a dependency-latency alert on calls from gateway to events.

### 7. Capacity Plan

Current measured ceiling: approximately 15 RPS at 50 users, but this is not a healthy capacity ceiling because it was already failing with 58.41% errors and p99 of 5.3s. The healthy measured point was 10 users at 7.59 RPS with 0 failures and p99 of 150 ms.

For 2x traffic from the healthy point, the target is roughly 15 RPS with p99 below 500 ms and 5xx below 0.5%. The current system reached about that RPS but failed badly, so the first scaling target is not more gateway capacity; it is fixing the `events` database connection bottleneck.

Recommended 2x plan:

- Keep gateway at 5 replicas initially; CPU was low during the breaking-point test.
- Increase `events` from 1 to 3 replicas only after introducing a DB pooler or increasing DB connection capacity safely.
- Keep payments at 1 replica for now; payments was not the limiting service in this test.
- Add explicit resource requests/limits for the expected 2x load and validate with another Locust run.
- Add a DB pooler such as PgBouncer before increasing events replicas, otherwise more pods may multiply DB connections and move the failure to Postgres.
- Keep Redis single-pod for the next controlled test, but monitor Redis latency and plan replication/managed Redis before production use.

## Task 2 — Capacity Plan with Numbers

CPU was sampled during a repeated 50-user breaking-point run.

```text
2026-10-10 15:09:27 UTC
NAME                       CPU(cores)   MEMORY(bytes)   
gateway-6d69f5f9fb-24zkq   26m          42Mi            
gateway-6d69f5f9fb-hnghx   16m          42Mi            
gateway-6d69f5f9fb-jqhmg   18m          42Mi            
gateway-6d69f5f9fb-rxh2h   15m          43Mi            
gateway-6d69f5f9fb-zqqp4   18m          42Mi            
NAME                     CPU(cores)   MEMORY(bytes)   
events-64cb5f5d7-4sg4f   18m          40Mi            
NAME                        CPU(cores)   MEMORY(bytes)   
payments-54844698b7-htcxz   20m          36Mi            
```

CPU was not saturated. The weakest link was the `events` service connection pool to Postgres, shown by the `psycopg2.pool.PoolError: connection pool exhausted` error and by the high 5xx rate while CPU remained low.

| Service | Current replicas | Recommended 2x replicas | Resource request | Resource limit | Reason |
| --- | ---: | ---: | --- | --- | --- |
| gateway | 5 | 5 | 50m CPU / 64Mi memory | 200m CPU / 256Mi memory | CPU was low; gateway was not the bottleneck. |
| events | 1 | 3 after DB pool fix | 100m CPU / 128Mi memory | 300m CPU / 512Mi memory | This service owns the failing DB pool path. |
| payments | 1 | 1 | 50m CPU / 64Mi memory | 200m CPU / 256Mi memory | Payment CPU stayed low and payment failures were not the observed bottleneck. |
| Redis | 1 | 1 for lab-scale, replicated for production | 50m CPU / 64Mi memory | 200m CPU / 256Mi memory | No Redis bottleneck was observed, but single-pod Redis is a production risk. |
| Postgres | 1 | 1 with PVC for lab, managed/replicated for production | 250m CPU / 512Mi memory | 1 CPU / 1Gi memory | The DB connection path must be protected before increasing events replicas. |

Rough pod-cost estimate using the lab assumption of `$5/pod/month`:

| Component | Pods | Monthly estimate |
| --- | ---: | ---: |
| gateway | 5 | $25/month |
| events | 3 | $15/month |
| payments | 1 | $5/month |
| redis | 1 | $5/month |
| postgres | 1 | $5/month |
| **Total** | **11** | **$55/month** |

This estimate excludes managed database, managed Redis, storage, ingress, and observability costs. A production plan should price those separately.

## Bonus Task — SRE Handbook

Bonus Option B is completed with a concise SRE handbook at `submissions/runbooks/quickticket-handbook.md`. It covers architecture, deployment, monitoring, incident response, and backup/restore procedures derived from the course labs.
