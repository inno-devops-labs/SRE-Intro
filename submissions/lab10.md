# QuickTicket Reliability Review
**Student:** Valerii Tiniakov
**Group:** B24-SD-03

## 1. SLO Compliance
| SLO | Target | Observed | Status |
| :--- | :--- | :--- | :--- |
| Availability (5xx rate) | < 0.5% | [X]% | [OK/FAIL] |
| Latency (p99) | < 500ms | [X]ms | [OK/FAIL] |

## 2. Load Test Results
| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) |
|------:|-----:|----:|----:|----:|----:|---------------:|----------------:|
| 10    | 2/s  |   7.75  |  9ms   |     |     |                |                 |
| 50    | 5/s  |  32.43   |   78ms  |     |     |                |                 |
| 100   | 10/s |   40.07  |  750ms   |     |     |                |                 |
| 50   | 5/s|  32.43   |   78ms  |     |     |                |                 |

## 3. DORA Metrics
| Metric | Value |
| :--- | :--- |
| Deployment Frequency | [X] per week |
| Lead Time for Changes | [X] minutes |
| Change Failure Rate | [X]% |
| Time to Restore Service | [X] minutes |

## 4. Top 3 Reliability Risks
1. **[Risk Name]** — [Impact] — [Fix]
2. **[Risk Name]** — [Impact] — [Fix]
3. **[Risk Name]** — [Impact] — [Fix]

## 5. Toil Identification
| Task | Frequency | Automate via | Time Saved |
| :--- | :--- | :--- | :--- |
| [Task 1] | [X] times | [Method] | [X] min/week |
| [Task 2] | [X] times | [Method] | [X] min/week |
| [Task 3] | [X] times | [Method] | [X] min/week |

## 6. Monitoring Gaps
- [Gap 1]
- [Gap 2]

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