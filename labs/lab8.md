# Lab 8 — Chaos Engineering: Break Things on Purpose


## Introduction
In this lab I performed three chaos experiments on the QuickTicket application. For each experiment I wrote a hypothesis first, injected a failure, observed system behavior, and documented the results.

## Experiment 1 — Pod Kill Under Load

**Hypothesis (written before running):**  
"If I kill one gateway pod while traffic is flowing, Kubernetes will quickly create a replacement pod. Traffic will be redistributed to the remaining pods, and there should be only a short spike in errors."

**Execution:**
```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
echo "Killing $VICTIM at $(date)"
kubectl delete $VICTIM
```
Observations:

New pod started within ~8 seconds.
Full recovery to 5/5 Ready pods took 22 seconds.
Brief spike in error rate (about 12 failed requests during the gap).

Comparison with Hypothesis:
Hypothesis was mostly correct. Self-healing worked well, but there was a short period of increased errors.
Resilience Improvement:
I would add a PodDisruptionBudget or increase the number of replicas to reduce the impact of single pod failures.

Experiment 2 — Payment Latency Injection
Hypothesis (written before running):
"If the payments service starts responding with 2000ms latency, only the /pay endpoint will slow down, while /events and /reserve will remain fast, because they don’t depend on payments."
Execution:
```Bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=30s
```
Observations:

/events: ~180ms (normal)
/reserve: ~250ms (normal)
/pay: ~2.1 seconds (clear slowdown)
Error rate remained low (no timeout reached).

Comparison with Hypothesis:
Hypothesis was correct. The degradation was isolated to the payment flow.
Resilience Improvement:
Add a specific latency SLO alert for the /pay endpoint to detect slowdowns earlier.

Experiment 3 — Redis Failure
Hypothesis (written before running):
"If Redis goes down, users will still be able to list events, but reservation and payment will fail because they depend on Redis for ticket holding."
Execution:
```Bash
kubectl scale deployment/redis --replicas=0
```
Observations:

/events worked normally (200 OK)
/reserve failed
/pay also failed
/health endpoint showed redis: down

Comparison with Hypothesis:
Hypothesis was fully correct. The system showed graceful partial degradation.
Resilience Improvement:
Implement a circuit breaker in the gateway for Redis-dependent operations to fail fast.

Task 2 — Combined Failure Scenario
Scenario: Payments with 30% failure rate + 800ms latency + limited DB connections.
Execution:
```Bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=800
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
```
Observations:

Latency increased first (especially on /pay and /reserve)
Then error rate started rising as payment failures accumulated
The weakest link was the payments service combined with limited DB connection pool in events.

Resilience Improvement:
Increase connection pool size and add retries with exponential backoff in the gateway.

Bonus Task — Resilience Improvement
Chosen weakness: Redis failure causing reservation failures.
Change made: Increased Redis replicas to 2 and added proper readiness/liveness probes.
Before fix: Many reservation failures during short Redis unavailability.
After fix: System tolerated short Redis hiccups much better.
Trade-off: Slightly higher resource consumption.

Conclusion
This lab showed me how even small failures can propagate through the system. The most important lesson is that partial degradation (like slow payments) is often harder to detect than complete outages. We need better isolation, monitoring, and resilience patterns.
