# Lab 11 — Advanced Microservice Patterns — Submission

**Student:** jakefish18
**Repo:** https://github.com/jakefish18/SRE-Intro · **Branch:** `feature/lab11`

PR checklist:
```text
- [x] Task 1 done — notifications service, k8s manifest, fire-and-forget wiring, retry (Tests #1 + #2)
- [x] Task 2 done — circuit breaker + rate limiter, tested under failure
- [x] Bonus Task done — bulkhead isolation, cap proven to bind
```

> The gateway runs as a locally-built image (`quickticket-gateway:v1`,
> `imagePullPolicy: Never`, `k3d image import`) so the pattern code is live; the same
> for `quickticket-notifications:v1`. `k8s/gateway.yaml` gained `NOTIFICATIONS_URL` +
> the pattern tunables as env vars.

---

## Task 1 — Notifications Service + Retries

### 1. `app/notifications/main.py` (key bits) + `requirements.txt`

A fire-and-forget destination copied from the payments template — `POST /notify`,
`/health`, `/metrics`, with fault injection via `NOTIFY_FAILURE_RATE` / `NOTIFY_LATENCY_MS`:
```python
NOTIFY_TOTAL = Counter("notifications_notify_total", "Total notify attempts", ["result"])

@app.post("/notify")
def notify(body: dict = None):
    body = body or {}
    event = body.get("event", "unknown"); order_id = body.get("order_id", "unknown")
    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        raise HTTPException(500, "Notification delivery failed")
    NOTIFY_TOTAL.labels("success").inc()
    return {"status": "sent", "event": event, "order_id": order_id}
```
`requirements.txt`: `fastapi==0.136.0`, `uvicorn==0.44.0`, `prometheus-client==0.25.0`
(identical to payments — no DB, no Redis). Also emits `notifications_requests_total`
and `notifications_request_duration_seconds` via the copied metrics middleware.

### 2. `k8s/notifications.yaml`
Deployment (1 replica, `quickticket-notifications:v1`, `imagePullPolicy: Never`,
container port 8083, `NOTIFY_FAILURE_RATE`/`NOTIFY_LATENCY_MS` env, `app=notifications`)
+ ClusterIP Service (8083→8083, `selector app=notifications`). Full file committed.

### 3. `call_with_retry()` implementation
```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    base_delay = RETRY_BASE_DELAY_MS / 1000
    for attempt in range(max_retries):
        try:
            result = await func()
            if attempt > 0:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result
        except Exception as e:
            retryable = False
            if isinstance(e, (httpx.TimeoutException, httpx.ConnectError)):
                retryable = True
            elif isinstance(e, httpx.HTTPStatusError):
                sc = e.response.status_code
                retryable = sc >= 500 or sc in (408, 429)
            if not retryable:
                RETRY_TOTAL.labels(target, "non_retryable").inc(); raise
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc(); raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
```

### 4. Test #1 — fire-and-forget under notify failure
Injected `NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300`, ran 30 checkout chains:
```
result: ok=30 fail=0

/pay p99 during the injection (histogram_quantile over gateway_request_duration_seconds):
   /reserve/{id}/pay = 20.0 ms      ← NOT inflated by the 300ms notify latency
   /events           = 22.4 ms
   /events/{id}/reserve = 99.9 ms
```
All 30 checkouts succeeded and `/pay` p99 stayed at ~20ms — the 300ms notify latency and
the 30% notify failures never touched the user path. **Fire-and-forget proven.**

### 5. Test #2 — retries fire under transient payment failure
Injected `PAYMENT_FAILURE_RATE=0.3`, ran 30 checkout chains:
```
result: ok=30 fail=0

sum by (target,result) (gateway_retry_total):
   payments  retried               = 8
   payments  succeeded_after_retry = 7
```
30% of first `/charge` attempts failed, but the retry loop recovered them — **8 retries
fired, 7 checkouts succeeded only because of a retry**, and 0 users saw a failure
(expected all-3-fail rate is 0.3³ ≈ 2.7%). Both counters non-zero ⇒ retries are wired in.

### 6. Real notify failure rate (`notifications_notify_total{result}`)
```
notifications_notify_total{result="success"} 18.0
notifications_notify_total{result="failed"}  12.0
```
~40% of notify calls failed under the injection — yet every checkout still returned 200,
because the gateway never waits on (or fails on) the notification result.

### 7. Why should notifications be non-blocking (fire-and-forget)?

Notifications are a **best-effort side-effect**, not part of the money-path transaction.
Once payment succeeded and the order was confirmed, the purchase is *done* from the
user's perspective — whether the confirmation email goes out is irrelevant to their
outcome. Blocking the `/pay` response on notifications would (a) add the notification's
latency (300ms here) to **every** checkout, and (b) **fail the purchase whenever
notifications is down** — promoting a non-critical dependency to a critical one. Fire-
and-forget (`asyncio.create_task`) decouples them: the user gets a fast 200, delivery
happens out-of-band, and failures are logged, not surfaced. (That's also why `/health`
reports notifications status but never gates the `critical_ok` verdict on it.)

### 8. Design prompt — why `cb.call(retry(...))`, not `retry(cb.call(...))`?

The retry loop must live **inside** the circuit breaker:

- **Correct (`cb.call(retry(_charge))`):** the CB sees **one** outcome per logical call —
  the final result after all internal retries. One user checkout = one CB observation.
- **Wrong (`retry(cb.call(_charge))`):** two failures compound.
  1. Each retry attempt increments the CB's failure counter separately, so N retries of
     one call trip the breaker **N× faster** than the threshold intends.
  2. Once the CB is OPEN it fast-fails with `CircuitOpenError` — but a retry loop *outside*
     would treat that as another failure and **retry the fast-fail**, spinning the retry
     budget on instant errors and never letting the dependency rest. The whole point of
     the breaker (stop calling a dead dependency) is defeated.

> Note (from the pitfalls): one CB-observed "failure" is actually up to `RETRY_MAX`
> downstream calls, so 5 CB failures = up to 15 `/charge` attempts. Correct, but worth
> keeping in mind when reasoning about downstream load.

---

## Task 2 — Circuit Breaker + Rate Limiter

### CircuitBreaker.call + RateLimiter.allow
```python
async def call(self, func):                      # CircuitBreaker
    if self.state == self.OPEN:
        if time.time() - self.opened_at >= self.cooldown:
            self._transition(self.HALF_OPEN)     # cooldown elapsed → allow one probe
        else:
            raise CircuitOpenError(f"circuit[{self.name}] OPEN")   # fast-fail
    try:
        result = await func()
    except Exception:
        self.failures += 1; self.opened_at = time.time()
        if self.state == self.HALF_OPEN or self.failures >= self.threshold:
            self._transition(self.OPEN)
        raise
    else:
        self.failures = 0; self._transition(self.CLOSED); return result

def allow(self, key: str) -> bool:               # RateLimiter (1s sliding window)
    now = time.time(); q = self.hits[key]; cutoff = now - self.window_s
    while q and q[0] < cutoff: q.popleft()
    if len(q) >= self.rps: return False
    q.append(now); return True
```

### Circuit breaker — OPEN under 100% payment failure (500s vs 503s)
```
80 checkout attempts under PAYMENT_FAILURE_RATE=1.0:
  500s=25  503s=47  200s=0
```
(500 = retry-exhausted real failure; **503 = fast-fail because the circuit is OPEN**.)
The **25** 500s = 5 gateway pods × the per-pod threshold of 5 — each pod's breaker takes
exactly 5 exhausted calls to trip, then every subsequent call on that pod fast-fails 503
(**47** of them) without touching the dead payments service.

### Circuit breaker — CLOSED after cooldown + recovery
```
after PAYMENT_FAILURE_RATE=0.0 + 35s cooldown, 15 requests:
  200 200 200 200 ...   ← 200s resume (payments healthy again)
```

### Rate limiter — burst 200/429 split + `Retry-After` header
```
burst of 100 rapid GET /events:   200=46  429=54     (≈ per-pod 10rps × 5 pods ceiling)

429 response headers:
  HTTP/1.1 429 Too Many Requests
  retry-after: 1

sustained load at 5 rps (below the limit):   429s = 0
```

### Prometheus — CB transitions + rate-limit rejections
```
sum by (to) (gateway_circuit_breaker_transitions_total):
  to=OPEN=5   to=HALF_OPEN=3   to=CLOSED=3

sum by (path) (gateway_rate_limit_rejections_total):
  /events=81   /events/{id}/reserve=5   /reserve/{id}/pay=3
```

---

## Bonus Task — Bulkhead Isolation

### `Bulkhead.call` + the wrapping line in `pay_reservation`
```python
class Bulkhead:
    def __init__(self, name, max_concurrent, acquire_timeout_s):
        self.name = name
        self.sem = asyncio.Semaphore(max_concurrent)
        self.acquire_timeout = acquire_timeout_s
    async def call(self, func):
        try:
            await asyncio.wait_for(self.sem.acquire(), timeout=self.acquire_timeout)
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full")
        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec(); self.sem.release()

# in pay_reservation — composition bulkhead → CB → retry → call:
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
)
# BulkheadFullError is mapped to HTTP 503 alongside CircuitOpenError.
```

### Concurrent `/pay` vs `/events` isolation test (payments latency 3s)

80 concurrent `POST /reserve/<dummy>/pay` (the pay handler calls payments *before* the
reservation lookup, so a dummy RID still exercises the bulkhead), while a second client
samples `/events` latency. `RATE_LIMIT_RPS` was raised for this test so the front-door
rate limiter (10rps) wouldn't mask the bulkhead (also 10).

```
WITH bulkhead (BULKHEAD_PAYMENTS_MAX=10):
  PAY:  503 (bulkhead-full fast-fail) = 30    500 (got a slot → charged, dummy-RID confirm failed) = 50
  EVENTS during the /pay storm:  ok(<0.5s)=30   slow(>0.5s)=0     ← /events stayed FAST

CONTRAST — bulkhead effectively disabled (MAX=200), same storm:
  PAY:  503 (bulkhead) = 0     500 = 50          ← zero rejections: nothing is capped
  EVENTS (no cap):  ok(<0.5s)=30   slow(>0.5s)=0
  in_flight max per pod: [10, 10, 10, 2, 0]      (throughput-limited, not cap-limited)
```
> **Honest observation:** `/events` stayed fast in *both* runs. The classic bulkhead
> failure — "one slow dependency starves the threads meant for the others" — is a
> **threaded/sync-server** problem. FastAPI here is **async**: a slow `/pay` is just a
> pending `await`, so it never blocks the event loop serving `/events`. The bulkhead's
> concrete, *measured* effect in this async server is therefore **bounded concurrency +
> fast-fail** — 30 rejections with the cap vs 0 without — which protects against
> connection-pool / task pile-up under sustained slowness (and would be the difference
> between up and down on a threaded server). The cap binding at exactly 10 is the proof
> it works; the isolation is real but shows up as admission control, not event-loop rescue.

### Cap binds — rejections + in-flight saturation
```
sum by (target) (gateway_bulkhead_rejections_total):   payments = 30
max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m]) per pod:  [10, 10, 4, 2, 0]
```
The busiest pods saturate at **exactly 10 = `BULKHEAD_PAYMENTS_MAX`** — the cap binds —
and the 30 over-cap calls fast-fail (`gateway_bulkhead_rejections_total{payments}=30`)
rather than piling up. Meanwhile `/events` never slowed (ok=30, slow=0): the bulkhead
kept the slow payments calls in their own bounded compartment.

### Why must the bulkhead wrap the circuit breaker (not the reverse)?

The bulkhead must be **outermost** so that **one logical `/pay` call = one occupied
slot**, held across *all* of its internal retries. If the bulkhead were inside the
retry/CB, every retry attempt would acquire its own slot — so a single call with
`RETRY_MAX=3` would consume up to 3 slots, and the concurrency bound would count *retry
attempts*, not *logical calls*, making the cap ~3× looser and effectively meaningless.
Putting it outside also means a fast-fail (open CB inside) only borrows a slot for the
microsecond it takes to raise — the slot is immediately released — so slots stay
reserved for genuinely in-flight slow calls, which is exactly what we want to bound.

### Bulkhead vs rate limiter — what does each protect against?

They reject excess traffic for **opposite reasons**:
- **Rate limiter = inbound admission control.** It caps how fast *clients* hit the
  gateway (a request-rate ceiling; per-pod × replicas cluster-wide). It protects against
  traffic **volume** — bursts, thundering herds, crude DDoS — at the front door.
- **Bulkhead = outbound resource isolation.** It caps how many *concurrent calls the
  gateway makes to one downstream*. It protects against a **slow/failing dependency**
  monopolizing shared resources and starving the *other* dependencies (one leaky
  compartment shouldn't sink the ship).

In short: the rate limiter defends the gateway from its callers; the bulkhead defends the
gateway's other dependencies from one misbehaving dependency.
