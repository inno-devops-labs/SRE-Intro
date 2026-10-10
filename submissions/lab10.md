# Lab 10 — SRE Portfolio & Reliability Review

## Task 1 — Load Testing & Reliability Review

##  QuickTicket Reliability Review

## 1. SLO Compliance

| SLO | Target | Observed | Status |
|------|--------|----------|--------|
| Availability | ≥99.5% | No 5xx errors up to the supported capacity (20 concurrent users). | Met |
| p99 Latency | ≤500 ms | p99 remained below 500 ms up to the supported capacity (170 ms at 20 users). | Met |
| 5xx Error Rate | <0.5% | No 5xx errors up to the supported capacity. The threshold is exceeded only after the capacity ceiling is reached. | Met |

---

## 2. Load Test Results

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx Error Rate | 409 Conflict |
|-------:|-----:|----:|----:|----:|----:|---------------:|-------------:|
| 10 | 2/s | 8.45 | 10 ms | 16 ms | 24 ms | 0.00% | 0 |
| 20 | 4/s | 16.96 | 15 ms | 35 ms | 170 ms | 0.00% | 0 |
| 25 | 5/s | 20.46 | 18 ms | 220 ms | 620 ms | 0.09% | 0 |
| 30 | 5/s | 24.09 | 16 ms | 180 ms | 990 ms | 0.68% | 0 |
| 50 | 5/s | 25.45 | 660 ms | 1900 ms | 3000 ms | 33.94% | 0 |
| 100 | 10/s | 34.74 | 1000 ms | 5000 ms | 7200 ms | 66.75% | 0 |

The application remained stable up to approximately **20 concurrent users**. At **25 users**, the p99 latency exceeded the 500 ms SLO, making this the practical capacity ceiling. Increasing the load further resulted in rapidly growing latency and 5xx error rates, indicating application-level saturation under concurrent load.

---

## 3. DORA Metrics

| Metric | Value |
|---------|------|
| Deployment Frequency | 10 gateway ReplicaSets / 53 commits on main |
| Lead Time | Approximately 3–5 minutes (CI build + ArgoCD polling interval) |
| Change Failure Rate | 1 failed AnalysisRun out of 3 total AnalysisRuns (33.3%) |
| Mean Time to Recovery | Under 1 minute for rollout abort, approximately 3–5 minutes for Git revert + ArgoCD sync |

---

## 4. Top 3 Reliability Risks

1. **Gateway saturation under moderate load.**  
   The gateway exceeds the latency SLO at approximately 25 concurrent users, after which both latency and 5xx errors increase rapidly. Scaling the gateway and optimizing request handling would improve reliability.

2. **Lack of automatic horizontal scaling.**  
   The system currently relies on a fixed number of gateway replicas. Introducing a Horizontal Pod Autoscaler would allow the application to adapt to traffic spikes automatically.

3. **Limited observability during incidents.**  
   Troubleshooting relied mainly on manual inspection of Kubernetes resources and logs. More detailed dashboards, distributed tracing, and application-level metrics would reduce diagnosis time.

---

## 5. Toil Identification

| Manual Task | Frequency | Automation Opportunity | Estimated Time Saved |
|-------------|-----------|------------------------|----------------------|
| Flushing Redis before every experiment | Before every load or chaos test | Automate cleanup using a Kubernetes Job or helper script | ~1–2 minutes per experiment |
| Manually monitoring deployments and rollouts | During almost every deployment | Use automated rollout monitoring with notifications | ~3–5 minutes per deployment |
| Manually inspecting pod status and logs during troubleshooting | Throughout Labs 1–9 | Use centralized logging and health dashboards | ~5–10 minutes per incident |

---

## 6. Monitoring Gaps

During the load and chaos experiments, the following monitoring capabilities would have been useful:

- Per-endpoint latency dashboards (especially p95 and p99 latency).
- Alerts for increasing 5xx error rates before they become user-visible.
- Gateway saturation metrics (request queue length or concurrent requests).
- CPU and memory utilization dashboards for each gateway replica.
- Alerts for repeated readiness and liveness probe failures.

---

## 7. Capacity Plan

**Current supported capacity:** approximately **17 RPS (20 concurrent users)**.

**Breaking point:** approximately **20 RPS (25 concurrent users)**, where p99 latency exceeded 500 ms.

To support approximately **2× traffic**, I would:

- Increase the number of gateway replicas from **5 to 10**.
- Configure a Horizontal Pod Autoscaler for the gateway.
- Review CPU and memory requests and limits based on production utilization.
- Continue using canary deployments with automated AnalysisTemplates before full rollouts.

**Estimated cost:** approximately **2× the current gateway compute resources**, while Redis, PostgreSQL, and monitoring components are expected to remain sufficient until they become the next bottleneck.

## Task 2 — Capacity Plan with Numbers

### Gateway

Command

```bash
kubectl top pods -l app=gateway
```

Output

```text
NAME                       CPU(cores)   MEMORY(bytes)   
gateway-5bdd8bb7fd-4dfgr   21m          40Mi            
gateway-5bdd8bb7fd-f5l8h   7m           40Mi            
gateway-5bdd8bb7fd-fcm2b   6m           39Mi            
gateway-5bdd8bb7fd-gtcrt   30m          45Mi            
gateway-5bdd8bb7fd-rjx4j   7m           53Mi  
```

---

### Events

Command

```bash
kubectl top pods -l app=events
```

Output

```text
NAME                      CPU(cores)   MEMORY(bytes)   
events-664dbfb59b-gnw2l   10m          50Mi    
```

---

### Payments

Command

```bash
kubectl top pods -l app=payments
```

Output

```text
NAME                       CPU(cores)   MEMORY(bytes)   
payments-fcb5b8945-fn254   8m           36Mi     
```

---

## Bottleneck Analysis

The CPU samples at the breaking-point load level show that none of the services were fully CPU-saturated. Gateway CPU usage was distributed across five replicas and stayed between 6m and 30m per pod. The `events` service used around 10m CPU, and the `payments` service used around 8m CPU

This suggests that the observed latency and 5xx errors were not caused by raw CPU exhaustion alone. The bottleneck is likely related to request handling, service-to-service communication, readiness failures, connection limits, or application-level behavior under concurrent load

For 2× traffic, the gateway should still be scaled because it is the public entry point and handles all incoming requests. The `events` service should also be scaled because most load-test traffic goes through `/events` and `/events/{id}/reserve`. The `payments` service can remain at one replica unless payment traffic increases

---

## Capacity Plan for 2× Traffic

### Replica Plan

| Service | Current Replicas | Proposed Replicas | Reason |
|----------|-----------------:|------------------:|--------|
| Gateway | 5 | 8–10 | Keep request fan-out balanced and reduce per-pod latency under higher traffic |
| Events | 1 | 2–3 | Most load-test traffic goes through `/events` and reservation endpoints |
| Payments | 1 | 1–2 | Low CPU usage; scale later only if payment traffic increases |
| Redis | 1 | 1 | No evidence that Redis is the bottleneck |
| PostgreSQL | 1 | 1 | No evidence that PostgreSQL is the bottleneck at current load |

---

### Resource Requests and Limits

| Service | CPU Request | CPU Limit | Memory Request | Memory Limit |
|----------|------------:|----------:|---------------:|-------------:|
| Gateway | 100m | 500m | 128Mi | 512Mi |
| Events | 100m | 500m | 128Mi | 512Mi |
| Payments | 50m | 250m | 64Mi | 256Mi |
| Redis | 100m | 250m | 128Mi | 256Mi |
| PostgreSQL | 250m | 1000m | 512Mi | 1Gi |

---

### Redis

A single Redis instance is sufficient for the estimated 2× traffic in this lab. The load tests did not show evidence that Redis was the main bottleneck. However, for production reliability, Redis should eventually be moved to a replicated or managed setup because it is used for reservation holds and is therefore important for availability.

---

### PostgreSQL

The current single PostgreSQL instance is acceptable for the estimated 2× traffic in this lab. There was no direct evidence from the collected metrics that PostgreSQL was the primary bottleneck. However, the system should monitor database connection count, query latency, and slow queries because the single database path can become a bottleneck as traffic grows.

---

### Estimated Monthly Cost

Assuming approximately **$5 per pod per month**, estimate the infrastructure cost.

| Service | Replicas | Estimated Cost |
|----------|----------:|---------------:|
| Gateway | 10 | $50/month |
| Events | 3 | $15/month |
| Payments | 1 | $5/month |
| Redis | 1 | $5/month |
| PostgreSQL | 1 | $5/month |

**Total estimated monthly cost:** approximately **$80/month**.

---

## Analysis

To support approximately 2× the current traffic, I would scale the gateway from 5 replicas to 10 replicas and the events service from 1 replica to 2–3 replicas. The gateway should be scaled because it receives all incoming traffic, while the events service should be scaled because most Locust requests target `/events` and reservation endpoints.

The payments service can remain at one replica initially because its CPU usage was low during the test. Redis and PostgreSQL can also remain single instances for this lab-level capacity target, but both should be monitored closely. For a production setup, Redis replication and better PostgreSQL connection monitoring would be important next steps.

The estimated monthly cost for this plan is approximately **$80/month**, assuming **$5 per pod per month**.

## Bonus Task

I completed **Option B: 2-page SRE handbook**.

Handbook path:

```text
submissions/runbooks/quickticket-handbook.md
```