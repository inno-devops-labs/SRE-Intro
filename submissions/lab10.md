# QuickTicket Reliability Review

## 1. SLO Compliance

| SLO | Target | Observed | Status |
| --- | --- | --- | --- |
| 5xx error rate | <0.5% | ~0.0% at 10/50/100 users | Pass |
| p99 latency | <500 ms | <500 ms at 10/50/100 users | Pass |
| Availability | 99.9% | 100% during steady-state load tests | Pass |

## 2. Load Test Results

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) |
|------:|-----:|----:|----:|----:|----:|---------------:|----------------:|
| 10    | 2/s  | 8   | 20 ms | 50 ms | 90 ms | 0% | 0 |
| 50    | 5/s  | 40  | 75 ms | 180 ms | 260 ms | 0% | 2 |
| 100   | 10/s | 80  | 130 ms | 240 ms | 380 ms | 0% | 14 |
| 200   | 20/s | 19.61 | 5.4 s | 16 s | 25 s | 84.24% | 0 |

The breaking-point run was performed in-cluster with the 200-user, 20-user/s-ramp, 60-second Locust Job. It completed 1,161 requests (19.61 requests/s) with 1,028 Locust failures (88.54%). Of these, 978 were HTTP 5xx responses (84.24% of requests) and 50 were connection-refused failures. The p99 was approximately 25 seconds, far over the 500ms objective. No 409s were recorded in this run. Gateway pods restarted during the test, so the run demonstrates overload/failure rather than sustainable throughput; the earlier 10/50/100-user figures are the prior baseline results.

## 3. DORA Metrics

| Metric | Value | Evidence |
| --- | --- | --- |
| Deployment Frequency | 5 | Distinct rollout/image operations in Git history |
| Lead Time | ~3 minutes + build time | Argo CD sync polling interval and build time |
| Change Failure Rate | 0% | No failed AnalysisRuns or aborted rollouts observed |
| Recovery Time | ~1 minute | Rollout abort + rollback or revert quick recovery |

## 4. Top 3 Reliability Risks

1. Single Postgres pod without durable storage. This was the highest-severity risk because one restart could wipe the database and take the app offline. Fix: add a PVC and mount it at /var/lib/postgresql/data.
2. Gateway dependency on payments and Redis without circuit breaking / timeouts. Slow or broken downstream calls amplify into user-visible errors. Fix: circuit breaking, retry budget, and timeouts with fail-fast semantics.
3. Manual operational toil from repeated seeding and port-forward management. Fix: persistent storage, stable service DNS, and automation scripts for backup/reseed flows.

## 5. Toil Identification

| Toil | How often | Automation | Time saved |
| --- | --- | --- | --- |
| Reseeding Postgres after pod restarts | Daily or multiple times per week before PVC | Keep PVC and add init/restore automation | 10-20 minutes per incident |
| Recreating kubectl port-forwards after pod changes | Several times per lab | Use a shell script or systemd service | 5-10 minutes per session |
| Manually watching rollouts in `kubectl argo rollouts get rollout --watch` | For every deployment | Add AnalysisRun + GitOps alerting | 5-15 minutes per rollout |

## 6. Monitoring Gaps

- During the Lab 8 chaos work, I wished we had latency and saturation signals for the downstream payment path, not only error totals.
- I would alert on p95/p99 latency plus the `/reserve` 409 rate separately from 5xx, because a slow but successful path is still a user-impacting failure mode.
- The most useful alert would have been: gateway p99 latency >500 ms for 5 minutes OR 5xx rate >0.5% over 5 minutes.

## 7. Capacity Plan

- **Measured headroom at the 200-user breaking point:** sampled gateway pods reached 181m CPU against a 200m limit; Events reached 169m/200m; Payments peaked at 21m/200m. Gateway and Events are the constrained services; Payments has substantial CPU headroom. Gateway pods restarted and Events temporarily lost metrics availability under this load.
- **Safe baseline and target:** the earlier 100-user run measured about 80 RPS with p99 380ms and no 5xx. Treat 80 RPS as the current demonstrated safe level; 2x planning target is approximately 160 RPS, not the failed 200-user run's throughput.

| Service | Current replicas | Proposed replicas for ~2x | Proposed request / limit per pod | Rationale |
| --- | ---: | ---: | --- | --- |
| Gateway | 5 | 10 | 150m / 400m CPU; 128Mi / 512Mi | Scale the critical path horizontally and raise the CPU limit above the measured 181m/pod saturation point. |
| Events | 1 | 4 | 200m / 500m CPU; 256Mi / 512Mi | Was near its current CPU limit; extra replicas spread read/reservation work. Cap each DB pool at 5 initially (20 connections total), then tune from Postgres metrics. |
| Payments | 1 | 2 | 50m / 250m CPU; 64Mi / 256Mi | CPU is currently low, but a second replica adds availability and modest traffic headroom. |
| Redis | 1 | 3-node HA/managed service | 100m / 250m CPU; 128Mi / 512Mi | One pod is a single point of failure; use a supported HA topology for production rather than simply adding independent, unsynchronized replicas. |
| PostgreSQL | 1 | 1 primary (PVC-backed) | 200m / 1 CPU; 512Mi / 2Gi | Keep the PVC and monitor connection/IO saturation; introduce PgBouncer before raising aggregate app pool limits. |

Redis is acceptable as a single pod only for this lab's small test; it is not an HA design. The current Events pools allow up to 10 connections each; four replicas at that setting could request 40 total connections, so the initial 5-connection pool cap keeps the planned aggregate near 20. Verify PostgreSQL's `max_connections` and use PgBouncer if connection churn or saturation appears.

**Rough monthly estimate:** using the lab's $5/pod/month assumption, 10 gateway + 4 Events + 2 Payments + 3 Redis + 1 PostgreSQL + 1 Prometheus + 1 Grafana = 22 pods, or about **$110/month**. Add approximately **$10/month** for two small PVCs in this estimate: **about $120/month total**, excluding ingress, backups outside the cluster, and provider-specific managed-service fees. The plan assumes a multi-node cluster; the current single-node k3d cluster cannot provide node-level HA.

## Bonus — SRE Handbook

Completed the Option B handbook at [submissions/runbooks/quickticket-handbook.md](runbooks/quickticket-handbook.md). It includes an architecture diagram, GitOps deploy/rollback flow, monitoring queries, incident triage, and backup/restore steps.

---

## Appendix: load-test evidence

```text
10 users: 8 RPS, p99 90 ms, 5xx 0%
50 users: 40 RPS, p99 260 ms, 5xx 0%
100 users: 80 RPS, p99 380 ms, 5xx 0%
200 users (fresh breaking-point run): 19.61 RPS, p50 5.4 s, p95 16 s, p99 25 s, 84.24% HTTP 5xx, 88.54% total Locust failures
```

### Task 2 — per-pod CPU at breaking point

`kubectl top pods` samples taken while the 200-user Locust Job was active showed the following highest observed values during the run. Current pod limits are 200m CPU and 256Mi memory for each of these services.

| Service / pod | Highest sampled CPU during run | Current CPU limit | Interpretation |
| --- | ---: | ---: | --- |
| Gateway `plxsl` | 181m | 200m | Closest to the limit; pod restarted during the run. |
| Gateway `strfq` | 129m | 200m | High utilization in an earlier active sample; later restarted. |
| Gateway `wrf26` | 157m | 200m | High utilization; pod restarted during the run. |
| Gateway `wvpr2` | 137m | 200m | High utilization in an active sample. |
| Gateway `z72xn` | 154m | 200m | High utilization in an active sample; pod restarted. |
| Events | 169m | 200m | Near its limit; downstream bottleneck candidate. |
| Payments | 21m | 200m | Relatively idle; CPU is not the current bottleneck. |

The Locust job reported 1,161 total requests at 19.61 RPS, 1,028 failures overall, and an approximate 25-second aggregate p99. HTTP 5xx accounted for 978 requests (84.24%); the remaining 50 failures were connection-refused errors. This is the measured breaking point, not a healthy target operating point.
