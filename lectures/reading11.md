# 📖 Reading 11 — Advanced Microservice Patterns

> **Self-study material** for Bonus Lab 11. Read before starting the lab.

---

## Why Microservice Patterns Matter

Your QuickTicket has 3 services communicating via HTTP. In production systems with 10-100+ services, new failure modes emerge that don't exist in monoliths:

- **Cascading failures** — one slow service makes everything slow
- **Partial outages** — some features work, others don't
- **Dependency chains** — A calls B calls C calls D — where does the timeout go?
- **Network unreliability** — packets get lost, connections drop, DNS fails

This reading covers the patterns that make microservice communication reliable.

---

## Pattern 1: Retries with Exponential Backoff

**Problem:** A request fails due to a transient error (network blip, temporary overload).

**Solution:** Retry the request, but with increasing delays:

```
Attempt 1: fail → wait 100ms
Attempt 2: fail → wait 200ms
Attempt 3: fail → wait 400ms
Attempt 4: fail → give up, return error
```

**Add jitter** (random offset) to prevent thundering herd — when 1000 clients all retry at the same intervals, they overwhelm the recovering service:

```python
import random, time

def retry_with_backoff(func, max_retries=3, base_delay=0.1):
    for attempt in range(max_retries):
        try:
            return func()
        except Exception:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            time.sleep(delay)
```

**Key decisions:**
- How many retries? (3 is typical)
- Which errors to retry? (5xx yes, 4xx no — client errors won't fix themselves)
- Total timeout budget? (retries must fit within the caller's timeout)

📖 **Read more:** [AWS Architecture Blog — Exponential Backoff and Jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/)

---

## Pattern 2: Timeouts

**Problem:** A downstream service hangs — your service waits forever, consuming a connection/thread.

**Solution:** Set explicit timeouts on every remote call.

```python
import httpx

# Never do this:
response = httpx.get("http://payments:8082/charge")  # No timeout — waits forever

# Always do this:
response = httpx.get("http://payments:8082/charge", timeout=3.0)  # 3 second deadline
```

**Timeout budget across a call chain:**
```
Gateway (5s total timeout)
  → Events (3s timeout)
    → PostgreSQL (2s timeout)
  → Payments (3s timeout)
```

The gateway's total timeout must be larger than any single downstream call. But if Events and Payments are called sequentially, the gateway needs ≥6s. If called in parallel, ≥3s.

📖 **Read more:** *Release It!* by Michael Nygard, Chapter 5 — "Stability Patterns"

---

## Pattern 3: Circuit Breaker

**Problem:** A downstream service is down. Every request waits for the timeout (3 seconds), then fails. You're wasting 3 seconds × every request.

**Solution:** After N consecutive failures, stop calling the service entirely (circuit "opens"). Return a fast error. Periodically try one request (half-open). If it succeeds, close the circuit.

```
CLOSED (normal) → failures exceed threshold → OPEN (fast-fail)
OPEN → after cooldown → HALF-OPEN (try one request)
HALF-OPEN → success → CLOSED / failure → OPEN
```

```python
# Simplified circuit breaker
class CircuitBreaker:
    def __init__(self, failure_threshold=5, cooldown=30):
        self.failures = 0
        self.threshold = failure_threshold
        self.cooldown = cooldown
        self.state = "CLOSED"
        self.last_failure_time = 0

    def call(self, func):
        if self.state == "OPEN":
            if time.time() - self.last_failure_time > self.cooldown:
                self.state = "HALF_OPEN"
            else:
                raise CircuitOpenError("Service unavailable")

        try:
            result = func()
            self.failures = 0
            self.state = "CLOSED"
            return result
        except Exception:
            self.failures += 1
            self.last_failure_time = time.time()
            if self.failures >= self.threshold:
                self.state = "OPEN"
            raise
```

📖 **Read more:** Martin Fowler — [Circuit Breaker](https://martinfowler.com/bliki/CircuitBreaker.html)

---

## Pattern 4: Fallback / Graceful Degradation

**Problem:** A dependency is down. Do you return a 500 error, or can you give a partial response?

**Solution:** Return degraded but functional response:

| Dependency Down | 500 Error | Graceful Degradation |
|-----------------|-----------|---------------------|
| Recommendations engine | "Service unavailable" | Show "most popular items" |
| Payment service | "Cannot process order" | "Your reservation is held — pay later" |
| Search service | "Search is broken" | Show all items (unfiltered) |

Your QuickTicket gateway already does this for some endpoints (listing events works without payments). The lab extends this pattern.

---

## Pattern 5: Rate Limiting

**Problem:** A burst of traffic overwhelms your service. One client sends 1000 requests/second.

**Solution:** Limit requests per client/endpoint:

- **Token bucket** — each client gets N tokens per interval. Each request costs 1 token. No tokens = 429 Too Many Requests.
- **Common limits:** 100 req/min per IP, 1000 req/min per API key, 10 req/sec burst.

Rate limiting protects your service from:
- Misbehaving clients
- DDoS attacks
- Cascading load from retry storms

📖 **Read more:** [Stripe Engineering — Rate Limiters](https://stripe.com/blog/rate-limiters)

---

## How These Patterns Connect

```
Retry → Timeout → Circuit Breaker → Fallback → Rate Limit

1. Request fails → RETRY (with backoff)
2. Retries keep failing → TIMEOUT (don't wait forever)
3. Many timeouts → CIRCUIT OPENS (stop trying)
4. Circuit open → FALLBACK (degraded response)
5. Too many requests → RATE LIMIT (protect the service)
```

These patterns work together as layers of defense. In the lab, you'll implement some of these in your QuickTicket gateway.

---

## Key Books

- *Release It!* — Michael Nygard (2nd ed. 2018) — definitive guide to stability patterns
- *Microservices Patterns* — Chris Richardson (2018) — comprehensive microservice architecture
- *Building Microservices* — Sam Newman (2nd ed. 2021) — practical microservice design
