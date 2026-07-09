# QuickTicket Reliability Review

## 1. SLO Compliance
| SLO | Target | Observed | Status |
| --- | ------ | -------- | ------ |
| Gateway 5xx Error Rate | < 0.5% | 20.57% (at 100 users) | Missed |
| Gateway p99 Latency | < 500ms | 2100 ms (at 100 users) | Missed |

## 2. Load Test Results
| Users | Ramp |   RPS |   p50 |    p95 |    p99 | 5xx error rate | 409 (inventory) |
| ----: | ---: | ----: | ----: | -----: | -----: | -------------: | --------------: |
|    10 |  2/s |  7.60 |  19ms |  100ms |  480ms |          0.00% |               0 |
|    50 |  5/s | 35.96 |  14ms |   63ms |  100ms |          0.00% |              23 |
|   100 | 10/s | 58.02 | 210ms | 1300ms | 2100ms |        19.27%* |              45 |

*\*Note: 5xx error rate calculated by subtracting 45 (409 Conflict) from 713 total failures = 668 actual 5xx errors (19.27% of total 3467 requests).*

**Breaking Point Analysis:**
The system's capacity ceiling is approximately **36-40 RPS** at **50 concurrent users**. At 100 users, the system experienced a large degradation: p99 latency reached **2100ms** and the 5xx error rate soared to **19.27%** due to a flood of `502 Bad Gateway`, `503 Service Unavailable`, and `Connection refused` errors, signaling cascading service failure.

## 3. DORA Metrics
| Metric                      | Value      | Source / Calculation                                                                       |
| --------------------------- | ---------- | ------------------------------------------------------------------------------------------ |
| **Deployment Frequency**    | 9 rollouts | Total count of ReplicaSets (`kubectl get rs`) across all services.                         |
| **Lead Time for Changes**   | ~5 minutes | CI build time (~2 min) + ArgoCD poll interval (~3 min).                                    |
| **Change Failure Rate**     | 0%         | No failed Argo Rollouts AnalysisRuns detected in the current cluster state.                |
| **Time to Restore Service** | ~3 minutes | Time to execute `git revert` and wait for ArgoCD sync, or instant abort via Argo Rollouts. |

## 4. Top 3 Reliability Risks
1. **Ephemeral Database Storage** — Without a PVC, a single Postgres pod restart wipes all order data. *Fix: PersistentVolumeClaims.* 
2. **Unbounded Database Connection Pool** — High user load directly spawns new Postgres connections, causing services to hit `Connection refused` and crash. *Fix: Implement PgBouncer.*
3. **Single Point of Failure in Redis** — A single Redis instance handling all ticket reservations limits capacity and acts as a SPOF. *Fix: Deploy a Redis cluster/sentinel setup.*

## 5. Toil Identification
| Toil Task                                          | Frequency                | Proposed Automation                                              | Time Saved               |
| -------------------------------------------------- | ------------------------ | ---------------------------------------------------------------- | ------------------------ |
| Re-seeding DB manually after Postgres pod restarts | Every restart (Labs 1-8) | Attach a PVC so data persists across pod restarts (Lab 9 Bonus). | ~2 mins per incident     |
| Re-establishing `kubectl port-forward`             | Constantly during dev    | Expose services via Ingress controller / NodePort.               | Interruptions eliminated |
| Staring at terminal during canary rollouts         | Every deploy (Lab 7)     | Argo Rollouts + Prometheus AnalysisRuns to auto-promote/abort.   | ~5 mins per deploy       |

## 6. Monitoring Gaps
- **Missing Dependency Latency Alerts:** During Lab 8 chaos experiments, if the database became slow (but didn't throw 5xx), our error-rate SLO wouldn't catch it until the gateway completely timed out. We needed latency-specific alerts for downstream services (events/payments).
- **Missing Redis Capacity Alerts:** If Redis runs out of memory or connections during a ticket spike, we currently don't have an alert for it until users start seeing 500s from the gateway.

## 7. Capacity Plan

**Per-pod metrics at breaking point (100 users / ~58 RPS, measured during 3-min sustained load):**
- **Gateway (5 replicas):** ~100m CPU, 42Mi Mem (per pod)
- **Events (1 replica):** ~413m CPU, 51Mi Mem
- **Payments (1 replica):** ~23m CPU, 36Mi Mem

**Analysis of Bottleneck:**
Sustained load testing revealed that the single `events` pod is the main bottleneck, spiking to over 400m CPU. The system failure at 100 users (502 Bad Gateway` and `Connection refused`) `is caused by the events service struggling to process the concurrency queue. It either hits CPU throttling limits or exhausts its internal application thread/connection pool, causing upstream requests from the gateway to time out.` 

**Scaling Plan for 2x Traffic (~80-100 RPS target):**
- **Events Service:** Scale from 1 to 3 replicas to distribute the processing load. Set explicit resources requests/limits (e.g., Request: 200m, Limit: 500m).
- **Payments Service:** Scale from 1 to 2 replicas (primarily for High Availability, as CPU usage is very low).
- **Gateway Service:** Keep at 5 replicas (100m CPU per pod is perfectly healthy and well-distributed).
- **Database:** Introduce **PgBouncer** before scaling `events` to 3 replicas, to prevent the newly scaled pods from overwhelming the Postgres `max_connections` limit.
- **Cache:** Move Redis to a Sentinel/Cluster setup to remove the single point of failure.

**Rough Cost Estimate:**
Assuming small cloud containers (e.g., ~$5/pod per month):
- 5 Gateway + 3 Events + 2 Payments + 1 Postgres + 3 Redis = 14 total pods.
- **Estimated Total Cost:** ~$70 / month for the compute footprint.


