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

## Pattern 3.5: Composition — order matters

`Retry` and `CircuitBreaker` are the two patterns from this reading you'll most often want to combine. The order is not arbitrary.

```
  ✅  cb.call( lambda: retry(_charge) )       — retry inside CB
  ❌  retry( lambda: cb.call(_charge) )        — CB inside retry
```

**Why retry-inside-CB is the right shape:**

- The CB cares about whether the *target service* is healthy. If you retry inside the CB call, three failed retries register as **one failure** against the CB — which is correct, because the breaker tracks "the request as a whole couldn't get through."
- The CB lives outside, so once it OPENs, retries are skipped entirely. You go from "wait 3 × timeout × backoff" to "fast-fail in microseconds."

**Why CB-inside-retry is wrong:**

- Each retry attempt asks the breaker "are you closed?" — and on a single timeout the retry loop will keep asking. The breaker never gets to fast-fail your call chain because the retry loop hides the OPEN state.
- A truly down service that has tripped the breaker still costs you `max_retries` × CB-state-checks instead of one.

> 🤔 You'll see this exact composition in [`app/gateway/main.py`](../app/gateway/main.py) in Lab 11 as `payments_cb.call(lambda: call_with_retry(_charge, "payments"))`. The lab asks you to write up *why* — read the bullets above carefully and put it in your own words.

A subtler consequence: **one CB failure = N internal retries**. If you have `max_retries=3` and `failure_threshold=5`, you need 5 *outer* failures (= 15 *downstream* calls in the worst case) before the breaker trips. Tune the threshold with the retry count in mind, otherwise the breaker is too sluggish to actually protect anything.

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

## Pattern 4.5: Fire-and-Forget (Non-Blocking Side Effects)

**Problem:** Some downstream calls aren't on the critical path of the user's request. A failed *email notification* shouldn't fail an order. A flaky *audit log write* shouldn't make checkouts time out.

**Solution:** Don't `await` the call. Schedule it in the background, return the user's response immediately, and let the side-effect call fail (or retry, or get queued) without affecting the caller.

```python
# ❌ Blocking — every checkout pays the notification call's latency budget
@app.post("/pay")
async def pay():
    result = await charge_payment(...)
    await notify_user(result)        # 300 ms tax on every successful pay
    return result

# ✅ Fire-and-forget — return as soon as the critical work is done
@app.post("/pay")
async def pay():
    result = await charge_payment(...)
    asyncio.create_task(notify_user(result))   # background; user sees no delay
    return result
```

**Where you'd reach for this pattern:**

| Use fire-and-forget for… | Don't use fire-and-forget for… |
|---|---|
| Notifications, emails, SMS | Payment authorization |
| Analytics events, audit logs | Inventory reservation |
| Cache warm-up | Anything the user is waiting to see in the response |
| Webhook delivery to third parties | Any operation whose failure must roll back state |

**Critical caveats:**

- **Failures are silent by default.** You *must* emit metrics inside the background coroutine — if it raises and nobody catches it, the request looks healthy and the side effect just disappears. The lab measures real notify success rate by reading the destination service's `/metrics`, not the gateway's.
- **Process death drops in-flight tasks.** `asyncio.create_task` runs in-memory; a pod restart loses any task that hadn't completed. If the side effect must survive crashes, write to a queue (Redis, Kafka, RabbitMQ) instead of `create_task`.
- **In synchronous frameworks** (Flask, Django without async), `asyncio.create_task` doesn't apply. Use a queue + worker process, or a thread pool with a strict size cap.
- **Idempotency still matters.** A queue-based fire-and-forget retries on its own; the destination must handle "I've already seen this notification" correctly.

> 💡 In Lab 11, the gateway's notifications call is fire-and-forget but the *destination service* still emits Prometheus counters, so failures stay observable even though they're invisible to the user request.

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

## Bridge to Lab 11

Map the concepts above to where they live in your lab:

| Reading section | Lab 11 task | What you'll do |
|---|---|---|
| Pattern 1 — Retries | 11.4 `call_with_retry` | Exponential backoff + jitter; classify retryable vs non-retryable status codes |
| Pattern 2 — Timeouts | (already wired) | `httpx.AsyncClient(timeout=…)` is set in `app/gateway/main.py`; observe its effect |
| Pattern 3 — Circuit Breaker | 11.7 `CircuitBreaker.call` | CLOSED → OPEN → HALF_OPEN state machine in front of payments |
| Pattern 3.5 — Composition | 11.4 design prompt + 11.7 wiring | Defend `cb.call(retry(...))` in your submission |
| Pattern 4 — Fallback | (concept) | The `/pay` 503 path is already a graceful degradation when the breaker opens |
| Pattern 4.5 — Fire-and-Forget | 11.5 Test #1 | Notifications failures must not affect `/pay` p99 |
| Pattern 5 — Rate Limiting | 11.8 `RateLimiter.allow` | Sliding-window check, returns 429 + `Retry-After` |
| Patterns 6–7 — Bulkhead, Load Shed | concept-only | Reading material; you won't implement them, but recognise them on a runbook |

The `# TODO (Lab 11)` markers in `app/gateway/main.py` show you exactly which three function bodies to implement; everything else in the gateway (counters, middleware hookup, `cb.call(retry(...))` composition, fire-and-forget task creation) is already wired so you can focus on the algorithms.

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

### ⚠️ Per-replica state vs cluster-wide state

If you implement CB or rate limiter **in application code**, the state lives **inside the process**. Each replica keeps its own counter, its own breaker. There is no shared view.

```
  Gateway pod 1:  [breaker CLOSED]   [bucket: 7/10 used]
  Gateway pod 2:  [breaker CLOSED]   [bucket: 4/10 used]
  Gateway pod 3:  [breaker OPEN]     [bucket: 10/10 used]
  Gateway pod 4:  [breaker CLOSED]   [bucket: 0/10 used]
  Gateway pod 5:  [breaker CLOSED]   [bucket: 9/10 used]
```

This has two real consequences you'll see during the lab:

1. **Circuit breaker — needs N × replicas failures to fully open.** With a `failure_threshold=5` per pod and 5 replicas, **25 failures** can occur cluster-wide before *every* pod's breaker has tripped. Until they all open, some traffic still hits the dying service. Plan your test request volume accordingly.
2. **Rate limiter — cluster-wide ceiling = per-pod RPS × replicas.** A 10 RPS limit per pod, with 5 pods, lets through ~50 RPS cluster-wide. For real DDoS protection, the limiter has to live somewhere shared: at the **ingress** (NGINX, Traefik), in a **WAF** (Cloudflare, AWS WAF), or backed by **Redis** so all pods read/write the same bucket.

A service mesh fixes the breaker problem but not the rate-limit one (each Envoy proxy still has local state by default — global rate limiting needs an explicit `RateLimitService` like Lyft's `ratelimit`).

> 🤔 **Think:** if your circuit breaker logic was in **Redis** instead of in-process, how would the picture above change? (Trade-off: aggregated state, but you've now made every request hit Redis — and a Redis outage kills the breaker.)

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
