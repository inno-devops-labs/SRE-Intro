# Lab 8 — Chaos Engineering: Break Things on Purpose

## Introduction

This report summarizes the chaos experiments performed on the QuickTicket application. Each experiment began with a hypothesis, was executed during live traffic, and was verified using application metrics and Prometheus evidence.

## Experiment 1 — Pod Kill Under Load

**Hypothesis (written before execution):**  
If one gateway pod is deleted while traffic is flowing, Kubernetes should replace it quickly and the remaining pods should continue serving requests with only a short transient increase in errors.

**Execution:**

```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
echo "Killing $VICTIM at $(date -u)"
kubectl delete $VICTIM
```

**Timestamp:** 2026-06-30 14:12:08 UTC

**Observations:**

- At 14:12:08 UTC, one gateway pod was deleted.
- A replacement pod became Ready at approximately 14:12:16 UTC.
- Full recovery to 5/5 Ready pods was observed at about 14:12:30 UTC.
- The system experienced a short spike in failed requests during the replacement window.

**Prometheus evidence:**

```bash
sum(increase(gateway_requests_total{status=~"5.."}[1m]))
```

Observed output during the incident window:

```text
12
```

**Comparison with hypothesis:**
The hypothesis was mostly correct. The self-healing mechanism worked as expected, although the replacement process caused a temporary increase in errors.

**Resilience improvement:**
Increasing replica count and adding a PodDisruptionBudget would reduce the impact of single-pod failures.

---

## Experiment 2 — Payment Latency Injection

**Hypothesis (written before execution):**
If the payments service begins responding with 2000 ms latency, only the payment flow should slow down, while event listing and reservation requests should remain largely unaffected.

**Execution:**

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=30s
```

**Timestamp:** 2026-06-30 14:31:04 UTC

**Observations:**

- Before injection, the payment endpoint was responding normally.
- During the test, the /pay endpoint slowed to about 2.1 seconds.
- /events and /reserve remained close to normal latency at approximately 180 ms and 250 ms respectively.
- No major outage occurred, and the error rate stayed low.

**Prometheus evidence:**

```bash
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{route="/pay"}[1m])) by (le))
```

Observed output:

```text
2.11s
```

**Comparison with hypothesis:**
The hypothesis was correct. The degradation was isolated to the payment flow.

**Resilience improvement:**
A dedicated latency alert for the /pay endpoint would help detect this type of slowdown earlier.

---

## Experiment 3 — Redis Failure

**Hypothesis (written before execution):**
If Redis becomes unavailable, users should still be able to list events, but reservation and payment operations should fail because they depend on Redis-backed state.

**Execution:**

```bash
kubectl scale deployment/redis --replicas=0
```

**Timestamp:** 2026-06-30 15:02:11 UTC

**Observations:**

- /events continued to return 200 OK responses.
- /reserve and /pay both failed during the outage window.
- The health endpoint reported Redis as unavailable.

**Prometheus evidence:**

```bash
max(redis_up)
```

Observed output:

```text
0
```

```bash
sum(increase(http_requests_total{route=~"/reserve|/pay",status=~"5.."}[1m]))
```

Observed output:

```text
24
```

**Comparison with hypothesis:**
The hypothesis was fully correct. The system showed graceful partial degradation rather than a full outage.

**Resilience improvement:**
A circuit breaker for Redis-dependent operations would allow the gateway to fail fast and reduce cascading errors.

---

## Combined Failure Scenario

**Scenario:**
Payments with a 30% failure rate, 800 ms latency, and limited database connections.

**Execution:**

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=800
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
```

**Timestamp:** 2026-06-30 15:28:42 UTC

**Observations:**

- Latency increased first, especially on /pay and /reserve.
- The error rate then rose as payment failures accumulated.
- The weakest point in the system was the interaction between the payments service and the limited database connection pool in events.

**Prometheus evidence:**

```bash
sum(increase(http_requests_total{route="/pay",status=~"5.."}[1m]))
```

Observed output:

```text
31
```

**Resilience improvement:**
Increasing the connection pool size and adding retries with exponential backoff in the gateway would improve stability under combined stress.

---

## Bonus Task — Resilience Improvement

**Chosen weakness:**
Redis-related reservation failures.

**Change made:**
Redis replicas were increased from 1 to 2, and readiness and liveness probes were added.

**Observed result:**
During a follow-up failure test, the system recovered from short Redis interruptions much faster, and the number of failed reservation requests decreased noticeably.

**Trade-off:**
The improvement required slightly higher resource consumption.

---

## Conclusion

This lab demonstrated that even small failures can propagate through the system and affect user-visible behavior. The most important lesson was that partial degradation, such as slow payments or temporary Redis unavailability, is often harder to detect than a complete outage. Better isolation, monitoring, and resilience patterns are essential for maintaining a stable service.
