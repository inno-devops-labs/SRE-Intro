# Lab 10 — SRE Portfolio & Reliability Review



## Task 1 — Load Testing & Reliability Review

##  QuickTicket Reliability Review

## 1. SLO Compliance

| SLO | Target | Observed | Status |
|------|--------|----------|--------|
| availability | >= 99.5% | 100% - no 5xx errors at the supported load | Checkmark |
| /events p99 latency | <= 500 ms | ~81 ms at 10 users | Checkmark |
| 5xx error rate | <0.5% | 0% - 5xx errors appear only when the user count is too high | Checkmark |

The application meets SLO's at <50 users

## 2. Load Test Results

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx Error Rate | 409 Conflict |
|-------:|-----:|----:|----:|----:|----:|---------------:|-------------:|
| 10 | 2/s | 8 | 20 ms | 46 ms | 81 ms | 0.00% | 0 |
| 50 | 5/s | 23 | 600 ms | 1600 ms | 2200 ms | 15% | 0 |
| 100 | 10/s | 27 | 2000 ms | 5000 ms | 7000 ms | 68% | 0 |

Breaking point: 50 users or 23 RPS

## 3. DORA Metrics

| Metric | Value |
|---------|------|
| Deployment Frequency | 67 commits, 11 CI runs, 4 gateway ReplicaSets |
| Lead Time | ~5 minutes |
| Change Failure Rate | 25% - 1/4 |
| Mean Time to Recovery | From 10 seconds to 5 minutes depending on situation |

## 4. Top 3 Reliability Risks

1. Single events replica as the critical bottleneck - under load (50+ users), events CPU saturates at its limit while gateway replicas remain underutilized. This leads to 5xx errors and degrades the entire product. Fix: Scale events to more replicas, raise CPU limits, add HPA based on CPU, and increase DB connection pooling to prevent queueing

2. Cascading failures due to dependency-heavy readiness probes - as seen in lab 8, Redis degradation caused gateway and events pods to become NotReady, removing all endpoints from the Service and resulting in a full outage from a partial dependency failure. Fix: Use shallow (TCP) readiness probes and expose detailed dependency health via /health endpoints with separate alerts

3. Missing proactive latency and saturation alerts - current alerting only covers 5xx errors. Lab 8 demonstrated that a 2s payment slowdown produced successful but slow responses (p99 latency breach) with no alert, and Lab 6 showed CPU throttling on events without any notification. Fix: Add Prometheus alerts for `histogram_quantile(0.99, ...)` on gateway latency and CPU saturation on events

## 5. Toil Identification

| Manual Task | Frequency | Automation Opportunity | Estimated Time Saved |
|-------------|-----------|------------------------|----------------------|
| Redis flushing | every load test | automate cleanup using a Kubernetes Job or helper script | 2 minutes |
| Babysitting canary rollouts | every deployment test | keep AnalysisTemplates for auto-promote/auto-abort; add notifications for rollout status | 5 minutes |
| Manual pod status and logs inspection while troubleshooting | almost all labs | health dashboards & centralized logging | ~5 minutes |

## 6. Monitoring Gaps

- no latency SLO alerts - during lab 8 payments injection caused 2s+ latency with 0% errors, so no alert fired. What was missing: `histogram_quantile(0.99, ...)` per endpoint plus a burn-rate alert. A p99 latency > 500ms for 2 minutes would have caught this before users noticed

- no saturation signals for critical services - load tests showed events pinned at its CPU limit, returning 5xx before any alert triggered. Missing metrics: container CPU usage relative to limit (usage/limit > 0.8) and Postgres connection pool saturation (`pool_in_use / pool_max > 85%`). An alert on either would have fired minutes before the 5xx cascade at 75 users

- symptom-based alerts, not cause-based - Redis degradation caused readiness cascades and a full outage, but alerts only fired on gateway 5xx. Missing dependency-specific health checks: Redis up, Postgres pod readiness (`kube_pod_status_ready == 0`), and events application health. Alerts on these dependencies would have pointed to root cause immediately rather than just indicating something is wrong

## 7. Capacity Plan

**Current supported capacity:** ~17 RPS or 30 users.

**Breaking point:** ~23 RPS or 50 users

**For 2× traffic:**

- add a HPA (Horizontal Pod Autoscaler) for `events` service
- increase base `events` service replicas count from 1 to 3
- increase `payments` service replicas from 1 to 2
- increase postgres max connection pool

**Cost:** ~1.5x current cost, as the number of pods increases almost 1.5 times

## Task 2 — Capacity Plan with Numbers 

```bash
[ustkost@prime SRE-Intro]$ kubectl top pods -l app=gateway
NAME                       CPU(cores)   MEMORY(bytes)   
gateway-7598dd5fc4-bxkhh   11m          41Mi            
gateway-7598dd5fc4-czw8m   8m           43Mi            
gateway-7598dd5fc4-f9pjs   5m           42Mi            
gateway-7598dd5fc4-mtfmf   9m           42Mi            
gateway-7598dd5fc4-nd4wb   12m          42Mi            
[ustkost@prime SRE-Intro]$ kubectl top pods -l app=events
NAME                      CPU(cores)   MEMORY(bytes)   
events-647b978885-hwrsw   19m          44Mi            
[ustkost@prime SRE-Intro]$ kubectl top pods -l app=payments
NAME                        CPU(cores)   MEMORY(bytes)   
payments-8449db899c-9d4vb   11m          37Mi          
```

### Which service is the CPU-constrained one? Which is idle?
No idle services found; They all maintain consistent load. `events` service has the highest load and is the bottleneck of the application - main candidate for scaling.

### My 2x scaling plan
| Service | Current | 2x plan | Requests / limits | Reason |
|-----------|---------|---------|-------------------|--------|
| gateway | 5 replicas | 6 replicas | 100m request; 300m limit; 128Mi request; 256Mi limit | gateway is good, but we can add one more pod for safety |
| events | 1 replica | 3 replicas | 100m request; 300m limit; 128Mi request; 256Mi limit | events service is the main bottleneck of the application, it showed the highest CPU load |
| payments | 1 replica | 2 replicas | 50m request; 200m limit; 64Mi request; 256Mi limit | moderate CPU load, one more pod to ensure safety although this is not the most active service |
| Redis | 1 pod | 1 pod | 100m request; 300m limit; 128Mi request; 256Mi limit | no huge load was discovered on Redis pod, keep it 1 to save resources |
| Postgres | 1 pod | 1 pod and PgBouncer | 250m request; 500m limit; 512Mi request; 1Gi limit | adding connection multiplexing for events service pods to reduce bottleneck |

### Cost ($5/pod/mo): 
6 gateway, 3 events, 2 payments, 1 Redis, 1 Postgres = ~$65/mo (~$45/mo before scaling)
