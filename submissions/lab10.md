# QuickTicket Reliability Review
**Student:** Valerii Tiniakov
**Group:** B24-SD-03

## 1. SLO Compliance
| SLO | Target | Observed | Status |
| :--- | :--- | :--- | :--- |
| Availability (5xx rate) | < 0.5% | 9.47% | FAIL |
| Latency (p99) | < 500ms | 1400ms | FAIL |

## 2. Load Test Results
| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) |
|------:|-----:|----:|----:|----:|----:|---------------:|----------------:|
| 10    | 2/s  | 7.75 | 9ms | 18ms | 28ms | 0% | 0 |
| 50    | 5/s  | 32.43 | 78ms | 660ms | 1400ms | 9.47% | 4 |
| 100   | 10/s | 40.07 | 750ms | 5000ms | 5000ms | 64.07% | 0 |
| 50    | 5/s  | 32.43 | 78ms | 660ms | 1400ms | 9.47% | 4 |

## 3. DORA Metrics
| Metric | Value |
| :--- | :--- |
| Deployment Frequency | 1 per week |
| Lead Time for Changes | ~7 minutes |
| Change Failure Rate | 20% |
| Time to Restore Service | ~3 minutes |

## 4. Top 3 Reliability Risks
1. **CPU Bottleneck in `events` Service** — The service runs as a single pod and becomes CPU-constrained under high load, causing connection timeouts (504) and 502 Bad Gateway errors. — *Fix: Implement Horizontal Pod Autoscaler (HPA) and increase resource limits.*
2. **Postgres Single Point of Failure** — Currently, the database lacks read/write replicas, creating a risk of data unavailability if the underlying node or pod fails. — *Fix: Transition to a Managed Database or deploy a highly available StatefulSet with synchronous replication.*
3. **Lack of Rate Limiting** — The system is vulnerable to traffic spikes from individual clients, which can saturate resources and lead to cascading failures. — *Fix: Implement request rate limiting at the Gateway (Ingress) level to protect downstream services.*

## 5. Toil Identification
| Task | Frequency | Automate via | Time Saved |
| :--- | :--- | :--- | :--- |
| Postgres manual seeding | >5 times | PVC/VolumeMounts | 5 min/week |
| Manual port-forwarding | >10 times | In-cluster LoadTesting | 2 min/week |
| Watching Rollouts | >3 times | AnalysisTemplates | 10 min/week |

## 6. Monitoring Gaps
- **Lack of Latency-based Alerting:** Our existing alerts focus solely on error rates (5xx). This creates a "blind spot" where service performance degrades significantly (high latency) before the service actually crashes. We need alerts for p99 latency thresholds.
- **Resource Saturation Visibility:** During load testing, we lacked real-time dashboards for per-pod CPU/Memory saturation. We need to integrate Grafana dashboards visualizing resource request vs. usage to identify bottlenecks before they impact user traffic.

## 7. Capacity Plan (incl. Task 2)
**Per-pod CPU at breaking point:**
- `gateway` (x5): ~25m CPU per pod
- `events` (x1): 74m CPU
- `payments` (x1): 15m CPU

**Bottleneck Analysis:** The `events` service is severely CPU-constrained compared to the rest of the cluster. It is currently a single pod trying to handle traffic from 5 gateway replicas, leading to connection timeouts and 502/504 errors.

- **Current ceiling:** 32 RPS (at 50 users, before 5xx spikes)
- **2x Traffic Plan (for ~65 RPS):**
    - **Replicas:** Scale `events` to 3 replicas. Keep `gateway` at 5 (they have plenty of headroom). Keep `payments` at 1 or 2 (it is lightly used).
    - **Resources:** Set CPU requests/limits for `events` to 100m/250m.
    - **Redis & DB:** Redis can remain a single instance (it easily handles thousands of RPS). PostgreSQL needs connection pooling (e.g., PgBouncer) because scaling `events` to 3 replicas will triple the number of open DB connections.
- **Cost Estimate:** ~9 pods total. At $5/pod/mo = **$45/mo** compute cost.