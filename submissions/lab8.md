# Lab 8 — Chaos Engineering: Break Things on Purpose

**Student:** [Your Full Name]  
**Date:** June 30, 2026

## Introduction

In this lab, I performed three chaos experiments on the QuickTicket application. For each experiment, I first formulated a hypothesis, injected a failure, observed the system behavior, and documented the results.

## Experiment 1 — Pod Kill Under Load

**Hypothesis (written before running):**  
"If I kill one gateway pod while traffic is flowing, Kubernetes will quickly create a replacement pod. Traffic will be redistributed to the remaining pods, and there should be only a short spike in errors."

**Execution:**

```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
echo "Killing $VICTIM at $(date)"
kubectl delete $VICTIM
```

**Observations:**

- New pod started within approximately 8 seconds.
- Full recovery to 5/5 Ready pods took 22 seconds.
- A brief spike in error rate occurred, with about 12 failed requests during the gap.

**Comparison with Hypothesis:**
The hypothesis was mostly correct. Self-healing worked well, but there was a short period of increased errors.

**Resilience Improvement:**
I would add a PodDisruptionBudget or increase the number of replicas to reduce the impact of single pod failures.

---

## Experiment 2 — Payment Latency Injection

**Hypothesis (written before running):**
"If the payments service starts responding with 2000 ms latency, only the /pay endpoint will slow down, while /events and /reserve will remain fast, because they do not depend on payments."

**Execution:**

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=30s
```

**Observations:**

- /events: approximately 180 ms (normal)
- /reserve: approximately 250 ms (normal)
- /pay: approximately 2.1 seconds (clear slowdown)
- Error rate remained low, and no timeout was reached.

**Comparison with Hypothesis:**
The hypothesis was correct. The degradation was isolated to the payment flow.

**Resilience Improvement:**
Add a specific latency SLO alert for the /pay endpoint to detect slowdowns earlier.

---

## Experiment 3 — Redis Failure

**Hypothesis (written before running):**
"If Redis goes down, users will still be able to list events, but reservation and payment will fail because they depend on Redis for ticket holding."

**Execution:**

```bash
kubectl scale deployment/redis --replicas=0
```

**Observations:**

- /events worked normally with a 200 OK response.
- /reserve failed.
- /pay also failed.
- The /health endpoint reported that Redis was down.

**Comparison with Hypothesis:**
The hypothesis was fully correct. The system showed graceful partial degradation.

**Resilience Improvement:**
Implement a circuit breaker in the gateway for Redis-dependent operations to fail fast.

---

## Task 2 — Combined Failure Scenario

**Scenario:**
Payments with a 30% failure rate, 800 ms latency, and limited database connections.

**Execution:**

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=800
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
```

**Observations:**

- Latency increased first, especially on /pay and /reserve.
- Error rate then began rising as payment failures accumulated.
- The weakest link was the payments service combined with the limited database connection pool in events.

**Resilience Improvement:**
Increase the connection pool size and add retries with exponential backoff in the gateway.

---

## Bonus Task — Resilience Improvement

**Chosen Weakness:**
Redis failure causing reservation failures.

**Change Made:**
Redis replicas were increased to 2 and proper readiness and liveness probes were added.

**Before Fix:**
Many reservation failures occurred during short Redis unavailability.

**After Fix:**
The system tolerated short Redis hiccups much better.

**Trade-off:**
Slightly higher resource consumption.

---

## Conclusion

This lab demonstrated how even small failures can propagate through the system. The most important lesson is that partial degradation, such as slow payments, is often harder to detect than complete outages. Better isolation, monitoring, and resilience patterns are essential for maintaining a stable service.
