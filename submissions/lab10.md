# QuickTicket Reliability Review

## 1. SLO Compliance

For this lab, I use the acceptance thresholds of no more than 0.5% 5xx responses and p99 latency below 500 ms.

| Load | 5xx rate | p99 | Result |
|---:|---:|---:|---|
| 10 users, 7.70 RPS | 0% | 38 ms | Meets both thresholds |
| 50 users, 26.01 RPS | about 35.0% | 1,200 ms | Exceeds both thresholds |
| 100 users, 28.47 RPS | about 60.4% | 13,000 ms | Severe degradation |

The first tested breaking point was 50 users at 26.01 RPS. The 100-user run was worse. The safe ceiling is somewhere above 7.70 RPS and below 26.01 RPS; I did not test intermediate levels.

## 2. Load Test Results

Locust ran inside the cluster and sent requests to the Gateway Service. Redis was flushed between runs. No 409 inventory conflicts appeared in the captured error reports.

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx rate | 409 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 2/s | 7.70 | 14 ms | 20 ms | 38 ms | 0% | 0 |
| 50 | 5/s | 26.01 | 500 ms | 1,000 ms | 1,200 ms | about 35.0% | 0 |
| 100 | 10/s | 28.47 | 1,300 ms | 6,500 ms | 13,000 ms | about 60.4% | 0 |

At 50 users, Locust reported 546 failed requests out of 1,552 (35.18%). Of these, 543 were HTTP 5xx and 3 were connection refusals. A repeated 50-user run produced 490 HTTP 5xx out of 1,544 requests (31.74%), with p99 still at 1,200 ms.

At 100 users, 1,028 of 1,702 requests were HTTP 5xx (60.4%); 69 more failed with connection refusals. Locust's overall failure rate was 64.45%.

Evidence: [10-user run](lab10-evidence/load-10.txt), [50-user run](lab10-evidence/load-50.txt), [100-user run](lab10-evidence/load-100.txt), [repeat 50-user run](lab10-evidence/load-50-capacity.txt).

## 3. DORA Metrics

| Metric | Lab result | Source and limits |
|---|---|---|
| Deployment frequency | 6 Gateway ReplicaSets created in 59 minutes 32 seconds, about 6.05 per hour | Sandbox rollout history; includes test rollouts and is not a production rate |
| Lead time for changes | About 8 days, 2 hours, 29 minutes | Proxy from image-tag commit `ca597bd` (2026-09-26 22:08:10 UTC) to the stable ReplicaSet (2026-10-05 00:37:25 UTC) |
| Change failure rate | 1 failed AnalysisRun of 2 observed, 50% | Very small sample; includes a deliberately tested canary |
| Recovery time | 0.741 seconds | Lab 7 measured time from abort to stable-only endpoints |

These are measurements from a single learning environment. The lead-time proxy spans a long gap between the image-tag commit and the observed ReplicaSet, so it may include time when no release was intended.

Evidence: [rollout, commit, and analysis history](lab10-evidence/dora-and-recovery.txt), [Lab 7 abort timing](https://github.com/DamirBayazitov/SRE-Intro/blob/feature/lab7/submissions/lab7-evidence/abort-timing.txt).

## 4. Top 3 Reliability Risks

1. **The Events request path degrades under load.** At the first failing level, the Gateway returned 500, 502, and 503 responses. Events used 104m CPU during the repeat 50-user run, against a 200m limit. Add replicas and headroom, then repeat the test while also measuring Postgres CPU, connection usage, and CPU throttling before identifying a single root cause.
2. **Gateway pods restarted during heavy load.** The 50- and 100-user tests included connection refusals, and Gateway restart counts increased. Add alerts for restarts, readiness failures, and resource pressure; investigate pod logs and termination reasons after each test.
3. **Postgres and Redis remain single-instance dependencies.** Postgres now has a PVC and scheduled backups, which protect against a pod restart. The local-path volume and single-node cluster do not protect against node or cluster loss. Use off-node backups and evaluate managed or replicated data services for production.

## 5. Toil Identification

| Manual task | Frequency observed across Labs 1–9 | Automation | Expected saving |
|---|---|---|---|
| Recreating port-forwards for Prometheus and Postgres | At least 4 sessions | Add Makefile targets or use in-cluster Jobs for repeatable queries | A few minutes per session |
| Running database seed, backup, restore, and verification commands | More than 4 separate recovery/check steps | Keep the backup CronJob and add a documented restore Job with post-restore checks | Several minutes per recovery |
| Watching rollouts and querying metrics by hand during experiments | More than 4 rollout/chaos checks | Script rollout status, Prometheus queries, and evidence capture; keep Argo AnalysisTemplates for canaries | Several minutes per deployment |

The frequencies are based on the repeated manual commands in the lab evidence and terminal history, not on automated toil telemetry.

## 6. Monitoring Gaps

- The load tests showed 500, 502, 503, 504, and connection-refused failures. Add an alert when the overall 5xx ratio exceeds 0.5% for a sustained window.
- The Lab 8 payment p99 alert covers one endpoint. Add p99 alerts for event listing and reservations as well.
- Capture pod restarts, readiness failures, CPU throttling, and memory pressure alongside request metrics.
- Add Postgres connection-pool, active-connection, query-latency, and storage metrics. The Events CPU sample suggests pressure on that path but does not establish whether Postgres was the cause.
- The load-test runs did not record whether an alert fired during the overload. Make alert state part of the next test's evidence.

## 7. Capacity Plan

The first measured failure was about 26 RPS. I would use 52 RPS as the initial 2x test target after changes, then increase traffic gradually while watching the 0.5% 5xx and 500 ms p99 thresholds.

During the repeated 50-user test, per-pod CPU was Gateway 5–14m, Events 104m, and Payments 6m. This was one sample during a degraded run. Events had the highest measured CPU; Gateway and Payments appeared low at that instant.

| Component | Current | First 2x test plan |
|---|---|---|
| Gateway | 5 replicas; 50m CPU / 200m limit, 64Mi memory / 256Mi limit each | 10 replicas; initially keep current per-pod settings and retest |
| Events | 1 replica; 50m CPU / 200m limit, 64Mi memory / 256Mi limit | 2 replicas; request 150m CPU and 128Mi memory, limit 500m CPU and 512Mi memory each |
| Payments | 1 replica; 50m CPU / 200m limit, 64Mi memory / 256Mi limit | 2 replicas; keep current settings for the first test |
| Postgres | 1 replica with a PVC | Keep one writer; monitor connections and query latency, and test PgBouncer before raising application connection pools |
| Redis | 1 pod | Keep for the 2x lab test while measuring it; use a managed or properly failover-tested HA setup for production |

The proposed added workloads are 5 Gateway replicas, 1 Events replica, and 1 Payments replica: 7 additional pods. At the lab's rough assumption of $5 per pod per month, that is about **$35/month**, excluding persistent storage, monitoring, and managed database/cache costs. The single-node k3d environment has no meaningful cloud bill, so this is a planning estimate.

## Evidence and recovery state

The Gateway Rollout returned to Healthy and all application pods were Ready after the load tests. The cluster showed increased restart counts on Gateway and Events during the overload.

Evidence: [CPU and memory during the 50-user run](lab10-evidence/load-50-capacity-resources.txt), [post-load health](lab10-evidence/post-load-health.txt), [final rollout and cluster evidence](lab10-evidence/dora-and-recovery.txt).
