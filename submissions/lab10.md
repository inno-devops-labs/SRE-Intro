# QuickTicket — SRE Portfolio & Reliability Review

---

## 1. SLO Compliance

| SLO                       | Target   | Observed | Status   |
|--------------------------|----------|----------|----------|
| Availability (error rate)| 99.5%    | ~84.66%  | Breached |
| Latency (p99 /pay)      | < 500 ms | 4.81 s   | Breached |
| System Health           | Healthy  | Degraded at 50 users | Breached |

**Conclusion:**
The system fails to meet both availability and latency SLOs under moderate load. The primary degradation threshold occurs at ~50 concurrent users (~31 RPS).

---

## 2. Load Test Results

Load testing was performed using Locust running inside the Kubernetes cluster to ensure proper load distribution across gateway replicas.

| Users | Ramp Rate | Duration | RPS  | p50   | p95   | p99   | 5xx Error Rate | 409 (Inventory) | Observation    |
|------|----------|----------|------|------|------|------|----------------|----------------|----------------|
| 10   | 2/s      | 60s      | 7.8  | 8 ms  | 16 ms | 37 ms | 0%             | 0%             | Stable         |
| 50   | 5/s      | 60s      | 31.2 | 170 ms| 740 ms| 990 ms| 15.34%         | 12%            | Degraded       |
| 100  | 10/s     | 60s      | 48.7 | 420 ms| 2.34 s| 4.81 s| 18.4%          | 31%            | Breaking point |

**Breaking Point Definition:**
At ~50 users, the system crosses both SLO thresholds:
- p99 latency > 500 ms
- 5xx error rate > 0.5%

---

## 3. DORA Metrics

| Metric                  | Value             | Notes |
|------------------------|------------------|------|
| Deployment Frequency   | 18 deployments    | Derived from Argo Rollouts + git history |
| Lead Time for Changes  | 4–6 minutes       | CI + ArgoCD sync interval |
| Change Failure Rate    | 11% (2/18)        | Failed canary + AnalysisRun failures |
| Time to Restore       | ~20 seconds       | Rollback via Argo Rollouts abort |

---

## 4. Top Reliability Risks

### 1. Downstream Service Dependency Failure
Failures in Payments or Redis propagate directly to Gateway causing cascading latency and errors.

**Mitigation:** Circuit breakers + graceful degradation.

---

### 2. Database Persistence Risk
Without persistent volumes, PostgreSQL data loss occurs on restart.

**Mitigation:** PersistentVolumeClaim + backup strategy (implemented in Lab 9).

---

### 3. Insufficient Observability
System lacked latency-based alerting and dependency-level visibility.

**Mitigation:** Add SLO-based alerting and per-service health metrics.

---

## 5. Toil Identification

| Manual Task                       | Frequency | Automation Strategy |
|----------------------------------|----------|---------------------|
| Port-forwarding to Prometheus    | Frequent | Scripted access / Makefile |
| Redis FLUSHDB before tests       | Every run | Pre-test Kubernetes Job |
| Manual canary rollout observation| Repeated | Replace with AnalysisTemplate alerts |

---

## 6. Monitoring Gaps

- Missing p99 latency alert for `/pay`
- No explicit Payments service health alert
- No Redis availability alert
- No circuit breaker activation metrics
- No SLO burn-rate alerting

---

## 7. Capacity Plan (2× Traffic)

### Current capacity ceiling:
~31–35 RPS at 50 users

### Scaling plan for ~60–70 RPS:

| Component   | Replicas / Scaling Strategy | CPU  | Memory |
|------------|-----------------------------|------|--------|
| Gateway    | 8–10 replicas               | 250m | 256Mi  |
| Events     | 4–5 replicas                | 150m | 128Mi  |
| Payments   | 4 replicas + circuit breaker| 200m | 256Mi  |
| Redis      | 3-node replication          | 100m | 128Mi  |
| PostgreSQL | PVC + backups              | 300m | 512Mi  |

**Estimated cost:** $35–55/month (small Kubernetes cluster)

---

## 8. Capacity Evidence (kubectl top pods)

At breaking point (50 users):

- Gateway pods: ~180–210m CPU each
- Events: lower utilization
- Payments: moderate utilization

**Conclusion:**
Gateway is the primary CPU bottleneck under load. Scaling should prioritize stateless Gateway replicas.

---

## 9. Monitoring Improvements (Prometheus Alerts)

- High latency alert:
  `p99 /pay > 500ms for 5m`

- Payment failure rate:
  `5xx rate > 5%`

- Redis health:
  `up == 0`

- Circuit breaker activation:
  `gateway_circuit_breaker_transitions_total > 0`

---

## 10. Final Reliability Summary

The system demonstrates:
- Clear load degradation curve under moderate concurrency
- Predictable bottleneck at Gateway layer
- Strong separation between 409 (business logic) and 5xx (system failure)

However, reliability is limited by:
- insufficient latency observability
- lack of dependency-level alerting
- absence of automated protection against cascading failures

---

## Final Conclusion

QuickTicket is a partially resilient microservice system with clear scaling boundaries. Under 2× projected load, horizontal scaling of stateless services combined with improved observability and circuit-breaking mechanisms is sufficient to restore SLO compliance.
