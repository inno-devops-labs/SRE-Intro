# Lab 11 — Advanced Microservice Patterns

PR checklist:

```text
- [x] Task 1 done — notifications service, k8s manifest, fire-and-forget wiring, retry with backoff (Tests #1 + #2)
- [x] Task 2 done — circuit breaker + rate limiter, tested under failure
- [x] Bonus Task done — bulkhead isolation, concurrent /pay vs /events test, cap proven to bind
```

---

## Task 1 — Notifications Service + Retries

### 1. `app/notifications/main.py` (key bits)

```python
NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

REQUEST_COUNT = Counter("notifications_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("notifications_request_duration_seconds", "Request duration", ["method", "path"])
NOTIFY_TOTAL = Counter("notifications_notify_total", "Total notification attempts", ["result"])

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    path = request.url.path
    if not path.startswith("/metrics"):
        REQUEST_COUNT.labels(request.method, path, response.status_code).inc()
        REQUEST_DURATION.labels(request.method, path).observe(duration)
    return response

@app.get("/health")
def health():
    return {"status": "healthy", "failure_rate": NOTIFY_FAILURE_RATE, "latency_ms": NOTIFY_LATENCY_MS}

@app.post("/notify")
def notify(body: dict = None):
    event = (body or {}).get("event", "unknown")
    order_id = (body or {}).get("order_id", "unknown")

    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)

    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        raise HTTPException(500, "Notification processing failed")

    notification_id = f"NTF-{uuid.uuid4().hex[:8].upper()}"
    NOTIFY_TOTAL.labels("success").inc()
    return {"status": "sent", "notification_id": notification_id}
```

`requirements.txt` (identical to payments — no DB, no Redis):

```
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

### 2. `k8s/notifications.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: notifications
  labels:
    app: notifications
spec:
  replicas: 1
  selector:
    matchLabels:
      app: notifications
  template:
    metadata:
      labels:
        app: notifications
    spec:
      containers:
        - name: notifications
          image: quickticket-notifications:v1
          imagePullPolicy: Never
          ports:
            - containerPort: 8083
          env:
            - name: NOTIFY_FAILURE_RATE
              value: "0.0"
            - name: NOTIFY_LATENCY_MS
              value: "0"
          livenessProbe:
            httpGet: { path: /health, port: 8083 }
            initialDelaySeconds: 10
            periodSeconds: 10
            failureThreshold: 3
          readinessProbe:
            httpGet: { path: /health, port: 8083 }
            periodSeconds: 5
            failureThreshold: 2
          resources:
            requests: { cpu: 50m, memory: 64Mi }
            limits: { cpu: 200m, memory: 256Mi }
---
apiVersion: v1
kind: Service
metadata:
  name: notifications
spec:
  type: ClusterIP
  selector:
    app: notifications
  ports:
    - port: 8083
      targetPort: 8083
```

Gateway wiring: `NOTIFICATIONS_URL=http://notifications:8083` added to `k8s/gateway.yaml`'s env block (also added to `app/docker-compose.yaml` for local dev parity).

### 3. `call_with_retry()` implementation

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    base_delay = RETRY_BASE_DELAY_MS / 1000
    last_exc: Exception | None = None

    for attempt in range(max_retries):
        try:
            result = await func()
            if attempt > 0:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if not (status >= 500 or status in (408, 429)):
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                raise
            last_exc = e
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            last_exc = e

        if attempt == max_retries - 1:
            RETRY_TOTAL.labels(target, "exhausted").inc()
            raise last_exc

        delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
        RETRY_TOTAL.labels(target, "retried").inc()
        await asyncio.sleep(delay)
```

### 4. Test #1 — fire-and-forget under notify failure

`NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300`, 30-request checkout burst:

```
result: ok=30 fail=0
```

`/pay` p99 right after the burst (`histogram_quantile(0.99, ...gateway_request_duration_seconds_bucket...)`):

```
{"path":"/reserve/{id}/pay"} = 0.0248s   (~25ms, well under the 100ms bar)
```

This proves fire-and-forget is genuinely non-blocking: 30% notify failures + 300ms injected notify latency have zero effect on `/pay` latency or success rate.

> Note: I ran this burst against `event_id=5` instead of the lab script's `event_id=3` — see "Operational notes" at the bottom for why.

### 5. Test #2 — retries fire under transient payment failure

`PAYMENT_FAILURE_RATE=0.3`, 30-request checkout burst (event 3):

```
result: ok=29 fail=1
```

(one request hit the ~2.7% all-three-retries-fail case — within the expected ok≈29-30/fail≈0-1 range)

`gateway_retry_total` (`sum by (target,result)`):

```
{target="payments", result="retried"}              = 50
{target="payments", result="succeeded_after_retry"} = 35
{target="payments", result="exhausted"}             = 2
```

Both `retried` and `succeeded_after_retry` are non-zero — retries are firing and recovering most transient failures.

### 6. Real notify failure rate (from `notifications` `/metrics`)

```
notifications_notify_total{result="success"} = 60
notifications_notify_total{result="failed"}  = 34
```

≈36% observed (injected 30% + background `mixedload` traffic contributing extra samples over the window) — consistent with the injected rate.

### 7. Why should notifications be non-blocking (fire-and-forget)?

Notifications are not on the critical path of a checkout: the user cares whether their ticket is reserved and paid for, not whether an email/SMS confirmation was dispatched at the exact moment `/pay` returns. If the gateway `await`ed the notify call, every checkout would inherit the notification service's latency and failure budget — a 300ms-slow or 30%-flaky notifier would directly degrade `/pay`'s SLO even though it has nothing to do with whether the payment actually succeeded. `asyncio.create_task(...)` lets the response return the instant the critical work (charge + confirm) is done, while the notify call runs in the background; a failure there is logged and counted in Prometheus but never surfaces to the user or blocks their request.

### 8. Design prompt — why `cb.call(retry(...))`, not `retry(lambda: cb.call(...))`?

`payments_cb.call(lambda: call_with_retry(_charge, "payments"))` is correct because the circuit breaker needs to see **one outcome per logical request**, not one outcome per HTTP attempt. With retry nested inside the CB call, three failed attempts collapse into a single failure as far as the breaker's failure counter is concerned — which is the right signal, since "the request as a whole couldn't get through" is what the breaker is meant to track, and it lets `CB_FAILURE_THRESHOLD` be tuned in terms of *user-visible* failures.

The reverse composition, `retry(lambda: cb.call(_charge))`, breaks in two ways:
1. **It retries past an open circuit.** Once the breaker trips, `cb.call` raises `CircuitOpenError` on every invocation — but the outer retry loop doesn't know that's a "stop trying" signal, it just sees "an exception, retry it." You'd burn all `RETRY_MAX` attempts re-asking a breaker that already told you no, turning a microsecond fast-fail into `RETRY_MAX` wasted round-trips (with backoff sleeps in between).
2. **It hides the CB's failure signal from the CB itself.** Each retry attempt is a *separate* call to `cb.call`, so a single logical request now increments the breaker's failure counter up to `max_retries` times instead of once. With `max_retries=3` and `threshold=5`, the breaker would trip after less than 2 "real" failed requests instead of 5 — the threshold no longer means what it says, and (per Reading 11 §3.5) the actual multiplier works the other way when retry is properly nested inside CB: 5 *outer* CB-visible failures already represent up to 15 *downstream* calls, so getting the nesting backwards makes tuning the threshold nearly impossible to reason about.

---

## Task 2 — Circuit Breaker + Rate Limiter

### `CircuitBreaker` and `RateLimiter`

```python
class CircuitBreaker:
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    HALF_OPEN = "HALF_OPEN"

    def __init__(self, threshold: int, cooldown_s: float, name: str = "cb"):
        self.threshold = threshold
        self.cooldown = cooldown_s
        self.name = name
        self.failures = 0
        self.state = self.CLOSED
        self.opened_at = 0.0

    def _transition(self, new_state: str):
        if self.state != new_state:
            log.warning(f"circuit[{self.name}] {self.state} -> {new_state}")
            CB_STATE_TRANSITIONS.labels(new_state).inc()
        self.state = new_state

    async def call(self, func):
        if self.state == self.OPEN:
            if time.time() - self.opened_at >= self.cooldown:
                self._transition(self.HALF_OPEN)
            else:
                raise CircuitOpenError(f"circuit[{self.name}] OPEN")

        try:
            result = await func()
            self.failures = 0
            self._transition(self.CLOSED)
            return result
        except Exception:
            self.failures += 1
            self.opened_at = time.time()
            if self.state == self.HALF_OPEN or self.failures >= self.threshold:
                self._transition(self.OPEN)
            raise


class RateLimiter:
    def __init__(self, rps: int):
        self.rps = rps
        self.window_s = 1.0
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.time()
        q = self.hits[key]
        cutoff = now - self.window_s
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.rps:
            return False
        q.append(now)
        return True
```

### Circuit breaker — open under 100% payment failure

`PAYMENT_FAILURE_RATE=1.0`, 80 checkout attempts:

```
500s=24 503s=47
```

(500 = retry-exhausted while a pod's breaker was still closing in; 503 = fast-fail once it opened)

`gateway_circuit_breaker_transitions_total{to="OPEN"}`: `1` on **every one of the 5 gateway pods** — matches the documented "5 independent per-process breakers" behavior (5×`CB_FAILURE_THRESHOLD`=25 failures needed before every replica has tripped).

### Circuit breaker — closes after cooldown

`PAYMENT_FAILURE_RATE=0.0`, waited 35s (cooldown = 30s), 15 requests:

```
[1] 200  [2] 200  [3] 200  [4] 200  [5] 200
[6] 200  [7] 200  [8] 200  [9] 200  [10] 200
[11] 200 [12] 200 [13] 200 [14] 200 [15] 200
```

`gateway_circuit_breaker_transitions_total` — every pod shows `to="HALF_OPEN"=1` and `to="CLOSED"=1` in addition to the earlier `to="OPEN"=1`: a full CLOSED→OPEN→HALF_OPEN→CLOSED cycle observed on all 5 replicas.

### Rate limiter — burst

100 rapid `GET /events`:

```
200=46 429=54
```

(expected ~50/50 with `RATE_LIMIT_RPS=10` × 5 pods ≈ 50 RPS cluster-wide ceiling)

`Retry-After` header on a 429:

```
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

`gateway_rate_limit_rejections_total` (`sum by (path)`):

```
{path="/events"}               = 70
{path="/events/{id}/reserve"}  = 11
```

Sustained load below the limit (30 requests, 200ms apart ≈ 5 RPS < 10 RPS/pod): `200=30 429=0` — no false positives under normal load.

---

## Bonus Task — Bulkhead Isolation

### `Bulkhead.call` + wiring

```python
BULKHEAD_PAYMENTS_MAX = int(os.getenv("BULKHEAD_PAYMENTS_MAX", "10"))
BULKHEAD_PAYMENTS_TIMEOUT_S = float(os.getenv("BULKHEAD_PAYMENTS_TIMEOUT_S", "0.5"))

class BulkheadFullError(Exception):
    """Raised by Bulkhead.call when no slot is free within the acquire timeout."""

class Bulkhead:
    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.acquire_timeout_s = acquire_timeout_s
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def call(self, func):
        try:
            await asyncio.wait_for(self.semaphore.acquire(), timeout=self.acquire_timeout_s)
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full")

        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self.semaphore.release()

payments_bulkhead = Bulkhead("payments", BULKHEAD_PAYMENTS_MAX, BULKHEAD_PAYMENTS_TIMEOUT_S)
```

Wiring in `pay_reservation` (bulkhead outside CB outside retry):

```python
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
)
...
except BulkheadFullError:
    raise HTTPException(503, "Payment service temporarily unavailable (bulkhead full)")
```

### Proving the cap binds

**Gotcha discovered during testing:** `BULKHEAD_PAYMENTS_MAX=10` is a **per-pod** cap, same as the circuit breaker and rate limiter (Reading 11's "per-replica state" section). With 5 gateway replicas and only 30 concurrent `/pay` requests spread evenly (~6/pod), no single pod's bulkhead ever saturated — zero rejections. This is a real, reproducible limitation of in-process bulkheads, not a bug in the implementation.

To get a deterministic demonstration, I temporarily scaled the gateway to **1 replica** (`kubectl scale rollout gateway --replicas=1`, restored to 5 immediately after), injected `PAYMENT_LATENCY_MS=3000 PAYMENT_FAILURE_RATE=0.0`, made 30 reservations, then fired the 30 `/pay` calls staggered 100ms apart (a sustained arrival rate that stays under `RATE_LIMIT_RPS=10/s`, but cumulatively exceeds the bulkhead's 10 slots since each slot is held for the full 3s):

**With bulkhead:**

```
PAY codes: 13x200  2x429  15x503
EVENTS: ok=30 slow=0
```

**Without bulkhead** (same single-pod setup, contrast build from a temporarily-reverted `payments_cb.call(...)` line, reverted in the source tree immediately after building the throwaway image — no lasting code change):

```
PAY codes: 29x200  1x429
EVENTS: ok=30 slow=0
```

`gateway_bulkhead_rejections_total{target="payments"}` = **15** (non-zero, as required).

`max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m])` = **10** (== `BULKHEAD_PAYMENTS_MAX` exactly — the cap binds).

**Honest caveat on the `/events` comparison:** unlike the scenario Reading 11 §6 describes, `/events` p99 did **not** visibly degrade even *without* the bulkhead (`ok=30 slow=0` in both runs). The reason is that this gateway's `/pay` path is fully `async`/`await` — a slow downstream call suspends only the one coroutine handling that request; it does not block the shared asyncio event loop the way a thread-per-request or synchronous framework would. So the literal "event loop gets clogged, `/events` starts queuing" failure mode doesn't reproduce here. What the bulkhead *does* demonstrably do, and what I'd call its real value in this codebase, is bound the number of concurrent in-flight calls to a slow dependency and fail fast (≤500ms 503) instead of letting unbounded concurrent calls queue for the full 3s each — protecting downstream capacity (and connection-pool/memory pressure) and giving callers a fast, actionable failure instead of a long hang.

### Design prompt — why does the bulkhead wrap the circuit breaker, not the other way around?

`CircuitBreaker.call` catches a bare `except Exception`, incrementing `self.failures` for *anything* that goes wrong inside `func()`. If the bulkhead were the innermost primitive (`cb.call(lambda: bulkhead.call(...))`), then whenever the bulkhead is full, `BulkheadFullError` would propagate straight into the CB's except-block and get counted as a payments failure — even though it says nothing about payments' health, only about the gateway momentarily running out of concurrency slots. That would let a burst of legitimate traffic trip the circuit breaker for a problem payments doesn't have, and — worse — once tripped, the CB would fast-fail everyone, including requests that would have found a free bulkhead slot a moment later.

With the bulkhead outermost, a `BulkheadFullError` short-circuits before ever reaching `cb.call`, so it's invisible to the breaker's failure count — exactly right, since it's a gateway-side capacity signal, not a downstream-health signal. It also means the slot is held for the correct duration in the case that matters: a real (possibly slow) call to payments holds its bulkhead slot for the call's full duration, retries included, since the retry loop and the CB's success/failure bookkeeping all happen *inside* one bulkhead-guarded invocation; a CB fast-fail (`CircuitOpenError`, raised in microseconds) only ever holds a slot for that brief instant, so an open circuit doesn't waste bulkhead capacity either.

### Design prompt — bulkhead vs rate limiter, what's the difference in what they protect against?

The rate limiter is about a **cluster-wide ceiling on total request volume** into the gateway: it doesn't care which downstream a request will eventually call, only how many requests-per-second hit a given endpoint path. Its job is to protect the gateway (and everything behind it, in aggregate) from being overwhelmed by *volume* — a traffic spike, a retry storm, a misbehaving client.

The bulkhead is about **dependency isolation** regardless of volume: even at a request rate well under any sane rate limit, one downstream (payments) being *slow* can quietly consume all the concurrency the gateway would otherwise spend serving `/events`, `/health`, or anything else. The bulkhead doesn't ask "how many requests per second," it asks "how many requests are *currently in flight* to this one target," and caps that independently of how many requests are in flight to any other target. A gateway could be well under its rate limit and still get dragged down by a single stalled dependency — that's exactly the failure mode the rate limiter can't see and the bulkhead exists to catch.

---

## Operational notes

- **Pre-existing events-service issue found during testing:** the Redis `event:{id}:held` counter (`app/events/main.py`) increments on every `/reserve` call but is never decremented — not on `/confirm`, not on the reservation record's TTL expiry. Heavy test traffic accumulated across labs 8/9/10/11 had permanently exhausted the *reservable* capacity on events 1, 3, and 5, even though the `/events` listing endpoint still showed nonzero `available` (it computes availability differently — `total - confirmed orders`, ignoring redis holds entirely — while `/reserve` checks `total - confirmed - held`). This produced `409 Not enough tickets (available: 0)` on events whose public listing showed plenty of stock. Left unfixed — it's in `app/events/main.py`, out of scope for this lab — but with explicit approval I reset it (`TRUNCATE TABLE orders;` in Postgres + `redis-cli FLUSHALL`) whenever it blocked a required test; all data involved is synthetic seed/test fixture data (2026 event dates), not production state.
- Also found (and reset before Test #1/#2) leftover `PAYMENT_FAILURE_RATE=0.3`/`PAYMENT_LATENCY_MS=300` on the `payments` Deployment from a prior session that hadn't been restored to baseline.
- All fault-injection env vars (`NOTIFY_FAILURE_RATE`, `NOTIFY_LATENCY_MS`, `PAYMENT_FAILURE_RATE`, `PAYMENT_LATENCY_MS`) were restored to `0`/baseline, and the gateway rollout restored to `quickticket-gateway:v1` at 5 replicas, at the end of testing.
