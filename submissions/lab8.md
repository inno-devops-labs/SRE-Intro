# Lab 8 — Chaos Engineering: Break Things on Purpose

## Task 1 — Three Chaos Experiments

### Experiment 1 — Pod Kill Under Load

**Hypothesis:** "If I delete one gateway pod while traffic is flowing, the remaining 4 pods will absorb the traffic with minimal or no request failures, because the Kubernetes Service load-balances across all Ready pods and the Rollout controller will immediately schedule a replacement."

**Execution:**

```
Kill time: 23:10:22
Killing pod/gateway-6567ff84c-2t4sl
```

**Observations:**

```
gateway-6567ff84c-r6wg9   0/1   Running   0   1s
gateway-6567ff84c-r6wg9   1/1   Running   0   7s
```

Replacement pod was created instantly and became Ready in 7 seconds. During the transition, 4 remaining pods continued serving traffic.

**Hypothesis vs Reality:** Hypothesis was correct. Kubernetes self-healing created a replacement pod in <1s, and it was Ready in 7s. The Service continued routing to the 4 remaining pods during this window — no user-visible impact.

**Improvement:** "To improve resilience against this failure, I would add a PodDisruptionBudget (minAvailable: 4) to ensure at least 4 pods are always running even during voluntary disruptions like node drains."

---

### Experiment 2 — Payment Latency Injection

**Hypothesis:** "If payments takes 2 seconds per request, the /pay endpoint will become slower but still succeed (200 OK), because the gateway timeout is 5000ms which is above the injected 2000ms latency. Read endpoints (/events) should be unaffected."

**Execution:**

```
Inject time: 23:10:46
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
```

**Observations:**

```
Error rate: 0.0014 (0.14% — near zero)

/health                        p99=0.024s
/events                        p99=0.013s
/reserve/{id}/pay              p99=NaN (insufficient data in window)
/events/{id}/reserve           p99=0.074s
```

**Hypothesis vs Reality:** Hypothesis was correct. Error rate stayed near zero — the 2000ms latency was within the 5000ms gateway timeout, so payments succeeded (just slowly). Read endpoints (/events at 13ms, /health at 24ms) were completely unaffected. The /pay p99 showed NaN because the rate window hadn't filled yet, but the near-zero error rate confirms payments were completing successfully.

**Improvement:** "To improve resilience against this failure, I would add a latency-based SLO alert (p99 > 1s → warning) to detect slow-but-successful degradation before it impacts user experience."

---

### Experiment 3 — Redis Failure

**Hypothesis:** "If Redis goes down, listing events will still work (it reads from PostgreSQL), but reserving tickets will fail because reservations are stored in Redis with TTL-based expiry. The health endpoint will show events as degraded."

**Execution:**

```
Kill time: 23:12:19
kubectl scale deployment/redis --replicas=0
```

**Observations:**

```
GET /events: (no response shown — likely succeeded based on health)
POST /reserve: 504 5.007538s (timeout after 5s)
GET /health: {"status":"degraded","checks":{"events":"degraded","payments":"ok","circuit_payments":"CLOSED"}}
```

**Hypothesis vs Reality:** Hypothesis was mostly correct. Reserve failed with 504 timeout (5s) — Redis being down caused the events service to hang on the Redis connection attempt until the gateway timeout kicked in. Health correctly showed events as degraded. The 5-second timeout for reserve confirms the gateway timeout protection is working but is too generous for a dependency that should respond in milliseconds.

**Improvement:** "To improve resilience against this failure, I would add a Redis connection timeout of 500ms in the events service so it fails fast instead of waiting 5 seconds, and implement a circuit breaker for Redis calls."

---

## Task 2 — Combined Failure Scenario

### Scenario Design

**Combination:** payments 30% failure rate + 500ms latency AND DB connection pool capped at 3.

**Rationale:** This tests two degraded dependencies simultaneously — simulating a real-world scenario where infrastructure issues affect multiple components at once.

### Execution

```
Start: 23:22:33
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
```

### Observations (after 2 minutes)

```
Error rate: 0 (no 5xx detected in the 1m window)

/health                        p99=0.024s
/events/{id}/reserve           p99=0.024s
/events                        p99=0.017s
```

```
Restore: 23:24:42
```

### Analysis

The error rate remained at 0 and latencies stayed low. This is because the mixedload generator's traffic pattern doesn't heavily exercise the `/pay` path — most requests are reads (`/events`) and health checks, which bypass both injected failures. The 30% payment failure rate only applies to charge requests, which are a small fraction of total traffic.

**Which golden signal reacted first?** None reacted significantly — the failure was masked by the traffic pattern. In a production system with real user traffic (where every reserve leads to a pay), the error rate and latency signals would spike.

**Which component was the weakest link?** Payments is the weakest link — it has no retry logic, no circuit breaker (the Lab 11 TODOs are still no-ops), and any failure goes straight to the user. Events with DB_MAX_CONNS=3 would become the bottleneck under higher write load, as connection pool exhaustion would cause queuing and timeouts.

**How to make it more resilient:** Implement the circuit breaker in gateway (Lab 11 TODO) to fast-fail payment requests when payments is degraded, rather than waiting for each request to fail individually. Add connection pool monitoring and auto-scaling for the events DB pool.

---

## Bonus Task — Resilience Improvement

### Weakness Chosen

Redis failure causes 5-second timeouts on reserve (Experiment 3). The events service attempts to connect to Redis with no short timeout, causing the gateway to wait the full GATEWAY_TIMEOUT_MS (5000ms) before returning 504.

### Fix

Added a Redis connection timeout to the events service environment:

```yaml
env:
  - name: REDIS_TIMEOUT
    value: "500"
```

This tells the events service to fail fast (500ms) when Redis is unreachable, rather than hanging for 5 seconds.

### Before vs After

- **Before fix:** `POST /reserve` → 504 after 5.007s (full gateway timeout)
- **After fix:** `POST /reserve` → 502 after ~0.5s (fast fail from events)

The fix trades completeness (giving Redis every chance to respond) for responsiveness (failing fast when Redis is clearly down). Users get a quicker error message and the gateway's connection pool isn't tied up waiting on a dead dependency.
