# 📖 Reading 11 — Advanced Microservice Patterns

> **Self-study material** for Bonus Lab 11. Read before starting the lab.

---

## Why Microservice Patterns Matter

Your QuickTicket has 3 services communicating via HTTP. In production systems with 10-100+ services, new failure modes emerge that don't exist in monoliths:

- **Cascading failures** — one slow service makes everything slow
- **Partial outages** — some features work, others don't
- **Dependency chains** — A calls B calls C calls D — where does the timeout go?
- **Network unreliability** — packets get lost, connections drop, DNS fails
- **Retry storms** — 1,000 clients retrying 3x each = 3,000 extra requests on a struggling service

> 💬 *"The first rule of distributed systems is: don't build distributed systems."* — tongue-in-cheek, because the network is the least reliable part of your stack.

This reading covers the patterns that make microservice communication reliable. Every pattern here maps to something in the lab.

---

## The 8 Fallacies of Distributed Computing

Coined by **L. Peter Deutsch** and others at Sun Microsystems (1994). Every microservice failure mode is a violation of one of these assumptions:

1. The network is reliable. ❌
2. Latency is zero. ❌
3. Bandwidth is infinite. ❌
4. The network is secure. ❌
5. Topology doesn't change. ❌
6. There is one administrator. ❌
7. Transport cost is zero. ❌
8. The network is homogeneous. ❌

Every pattern below exists to cope with at least one of these realities.

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
            # Exponential + jitter
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            time.sleep(delay)
```

**Key decisions:**
- How many retries? (3 is typical — more than that usually means giving up is right)
- Which errors to retry? (5xx yes, 4xx no — client errors won't fix themselves)
- Total timeout budget? (retries must fit within the caller's timeout)

### ⚠️ Idempotency: the retry prerequisite

**Retrying non-idempotent requests corrupts data.** If `POST /charge` succeeds but the response times out, retrying charges the card twice.

**Make operations idempotent** with a client-generated idempotency key:

```
POST /charge
Idempotency-Key: order-abc123-attempt-1
```

The server stores the key for 24h and returns the cached response on retry. Stripe, GitHub, and most serious APIs require this.

> 📖 **Deep dive:** [AWS Architecture Blog — Exponential Backoff and Jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/) — the canonical reference.

---

## Pattern 2: Timeouts

**Problem:** A downstream service hangs — your service waits forever, consuming a connection/thread. Worse — callers on *your* service do the same, and you run out of workers.

**Solution:** Set explicit timeouts on every remote call.

```python
import httpx

# Never do this:
response = httpx.get("http://payments:8082/charge")  # No timeout — waits forever

# Always do this:
response = httpx.get("http://payments:8082/charge", timeout=3.0)  # 3 second deadline
```

### Timeout budget across a call chain

```
Gateway (5s total timeout)
  → Events (3s timeout)
    → PostgreSQL (2s timeout)
  → Payments (3s timeout)
```

The gateway's total timeout must be larger than any single downstream call. But if Events and Payments are called sequentially, the gateway needs ≥6s. If called in parallel, ≥3s.

### Deadline propagation

Advanced systems pass a deadline (not just a timeout) down the call chain:

```
Client → Gateway: "respond by 15:00:05.000"
Gateway → Events: "respond by 15:00:04.500"   ← gateway has 500ms of its own work
Events → Postgres: "respond by 15:00:04.200"
```

gRPC, OpenTelemetry, and modern service meshes support deadline propagation natively.

> 📖 *Release It!* — Michael Nygard, 2nd ed. 2018, Chapter 5 — "Stability Patterns" — the best single source on timeouts.

---

## Pattern 3: Circuit Breaker

**Problem:** A downstream service is down. Every request waits for the timeout (3 seconds), then fails. You're wasting 3 seconds × every request, and filling your thread pool.

**Solution:** After N consecutive failures, stop calling the service entirely (circuit "opens"). Return a fast error. Periodically try one request (half-open). If it succeeds, close the circuit.

```
CLOSED (normal) → failures exceed threshold → OPEN (fast-fail)
OPEN            → after cooldown               → HALF-OPEN (try one request)
HALF-OPEN       → success                      → CLOSED
HALF-OPEN       → failure                      → OPEN
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

### History

- **Michael Nygard** coined the name in *Release It!* (2007)
- **Netflix Hystrix** (2012) made it famous — later deprecated (2018) in favor of **Resilience4j** + service meshes
- Modern implementations: **Polly** (.NET), **Resilience4j** (JVM), **Envoy** (service mesh), **failsafe-go**

> 📖 [Martin Fowler — Circuit Breaker](https://martinfowler.com/bliki/CircuitBreaker.html) — original pattern write-up.

---

## Pattern 4: Fallback / Graceful Degradation

**Problem:** A dependency is down. Do you return a 500 error, or can you give a partial response?

**Solution:** Return a degraded-but-functional response:

| Dependency Down | 500 Error | Graceful Degradation |
|-----------------|-----------|---------------------|
| Recommendations engine | "Service unavailable" | Show "most popular items" |
| Payment service | "Cannot process order" | "Your reservation is held — pay later" |
| Search service | "Search is broken" | Show all items (unfiltered) |
| Image CDN | Broken layout | Show placeholder + cached thumbnails |

**Netflix's classic example:** if the personalization service is down, show the generic "Popular on Netflix" row. Users see a movie grid instead of an error page.

Your QuickTicket gateway already does this for some endpoints (listing events works without payments). The lab extends this pattern.

> 💬 *"A partial answer in 200ms beats a full answer in 30 seconds."*

---

## Pattern 5: Rate Limiting

**Problem:** A burst of traffic overwhelms your service. One client sends 1000 requests/second.

**Solution:** Limit requests per client/endpoint:

### Token bucket

Each client gets N tokens. Each request costs 1 token. Bucket refills at R tokens/second. No tokens = 429 Too Many Requests.

```
Bucket size: 100 tokens
Refill rate: 10 tokens/s (sustained)
Burst:       100 requests allowed instantly
Sustained:   10 RPS
```

### Leaky bucket

Requests enter a queue; queue drains at a fixed rate. Overflow = rejection. Smoother than token bucket but less burst-friendly.

### Client-side vs server-side

| 🏷️ Where | 💡 Pros | ⚠️ Cons |
|-----------|--------|--------|
| Client SDK | Prevents load on server | Clients can cheat |
| Gateway/LB | Central, enforceable | One more hop |
| Service | Self-protecting | Work already wasted |

**Common limits:** 100 req/min per IP, 1000 req/min per API key, 10 req/sec burst.

Rate limiting protects your service from:
- Misbehaving clients
- DDoS attacks
- Cascading load from retry storms

> 📖 [Stripe Engineering — Rate Limiters](https://stripe.com/blog/rate-limiters) — the classic write-up.

---

## Pattern 6: Bulkhead

**Problem:** One slow dependency blocks threads meant for other dependencies. Ship sinking via one compartment.

**Solution:** Isolate resources per dependency — separate thread pools, connection pools, queues.

```
❌ Without bulkhead:
  50 threads shared across all downstream calls
  Payments is slow → all 50 threads blocked → everything else fails

✅ With bulkhead:
  30 threads for Events
  15 threads for Payments
  5 threads for Analytics (fire-and-forget)
  Payments slow → 15 threads blocked, others continue
```

Named after ship construction — a hull flooded in one compartment doesn't sink the ship.

---

## Pattern 7: Load Shedding

**Problem:** You're at capacity. Queuing more work means every request gets slower — *including* the ones that would succeed.

**Solution:** Reject excess requests immediately (429 or 503) rather than queue them.

```
Queue depth > threshold?  →  Return 503 with Retry-After header
CPU utilization > 80%?    →  Start dropping non-critical requests
```

**Priority-based shedding** — drop low-priority requests first:
- `/health` from an internal probe? Keep.
- `/recommendations` for an anonymous user? Drop first.
- `/checkout`? Keep as long as possible.

> 💡 Google's RPC framework **gRPC** has built-in deadline + load-shedding semantics. Envoy service mesh supports "overload manager."

---

## How These Patterns Connect

```
Retry → Timeout → Circuit Breaker → Fallback → Rate Limit → Bulkhead → Load Shed

1. Request fails            → RETRY (with backoff + idempotency key)
2. Retries keep failing     → TIMEOUT (don't wait forever)
3. Many timeouts            → CIRCUIT OPENS (stop trying)
4. Circuit open             → FALLBACK (degraded response)
5. Too many requests        → RATE LIMIT (protect the service)
6. Slow dependency          → BULKHEAD (isolate impact)
7. Overloaded anyway        → LOAD SHED (reject, don't queue)
```

These patterns work together as layers of defense. The lab implements a subset in your QuickTicket gateway.

---

## Where Do These Patterns Live?

You can implement resilience at three layers — and in real systems, **all three** coexist:

| 🏷️ Layer | ✅ Good for | ❌ Not great for |
|----------|------------|-----------------|
| 🧑‍💻 **In application code** (Resilience4j, Polly, Tenacity) | Custom logic, business-aware retries | Duplication across languages, per-team drift |
| 🛰️ **In a library / sidecar** (Envoy, gRPC) | Consistent language-agnostic behavior | Extra process to manage |
| 🌐 **In a service mesh** (Istio, Linkerd) | Zero-code-change, org-wide policy | Complexity, debugging gets harder |

> 💡 For a small team (like yours, running QuickTicket), **in-code** works fine. For a platform team running 50+ services, a **mesh** pays for itself by standardizing behavior.

---

## Observability for These Patterns

Patterns without observability are invisible. Emit metrics for each:

| 🛡️ Pattern | 📊 Key metrics |
|-----------|---------------|
| Retry | retry count by reason, retry success rate |
| Timeout | timeout count by endpoint |
| Circuit breaker | state transitions (CLOSED→OPEN→HALF_OPEN), rejections |
| Rate limit | rejections per key, current bucket fill |
| Bulkhead | pool saturation, queue depth per dependency |
| Load shed | drops per priority, shed reasons |

Dashboards for these should sit next to your golden-signals dashboard from Week 3.

---

## Real-World Incident Gallery

Every pattern above exists because someone suffered without it:

- **AWS Dynamo paper (2007)** — one of the first public descriptions of retry storms and jitter in distributed systems.
- **Facebook Oct 2021** — 6-hour outage because BGP misconfig took down DNS; internal tools also depended on the same DNS. Missing: **bulkhead** of auth from DNS.
- **Reddit 2023** — hours-long outage when a slow dependency saturated thread pools. Missing: **bulkhead** + **timeouts**.
- **Retry storm at GitHub (2018)** — MySQL failover caused thousands of jobs to retry simultaneously, preventing recovery. Missing: **jitter** + **rate limiting**.
- **Knight Capital (2012)** — runaway trading loop ($460M in 45 min). Missing: **circuit breaker** on order rate.

> 🤔 **Think:** In QuickTicket, if payments returns 500s for 60 seconds, what happens to gateway threads? (Answer: they block on the 5s timeout, then retry, blocking more. Circuit breaker would turn this from a cascade into a fast failure.)

---

## Key Books

- *Release It!* — **Michael Nygard** (2nd ed. 2018) — the definitive guide to stability patterns
- *Microservices Patterns* — **Chris Richardson** (2018) — comprehensive microservice architecture
- *Building Microservices* — **Sam Newman** (2nd ed. 2021) — practical microservice design
- *Designing Data-Intensive Applications* — **Martin Kleppmann** (2017) — covers distributed-systems theory behind these patterns
- *The Art of Scalability* — Abbott & Fisher (2nd ed. 2015) — org + architecture scaling together

## Key Talks (free)

- [Ben Christensen — Mastering Chaos at Netflix (Hystrix talk)](https://www.youtube.com/watch?v=CZ3wIuvmHeM)
- [Ariel Tseitlin — Chaos & Resilience at Netflix (QCon)](https://www.infoq.com/presentations/chaos-engineering-discipline/)
- [Randy Shoup — Service Architectures at Scale](https://www.youtube.com/watch?v=bKAOf2ftpI0)
