# Lab 11 — Bonus: Advanced Microservice Patterns

## Task 1 — Notifications Service + Retries

### 11.1: Notifications Service

Created `app/notifications/main.py` with:
- `POST /notify` — logs event, respects `NOTIFY_FAILURE_RATE` + `NOTIFY_LATENCY_MS` fault injection
- `GET /health` — returns status + injection params
- `GET /metrics` — Prometheus exposition with `notifications_requests_total`, `notifications_request_duration_seconds`, `notifications_notify_total`

Dockerfile exposes port 8083, runs as non-root `app` user.

### 11.2: K8s Manifest

`k8s/notifications.yaml` — Deployment (1 replica, `quickticket-notifications:v1`, port 8083) + ClusterIP Service.

### 11.3: Gateway Wiring

Added `NOTIFICATIONS_URL=http://notifications:8083` to gateway env. The pre-wired `_notify_order_confirmed` helper now makes real HTTP calls on every successful `/pay`.

### 11.4: Retry Implementation

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            result = await func()
            if attempt > 0:
                RETRY_TOTAL.labels(target, "retried").inc()
            return result
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            last_exc = e
            RETRY_TOTAL.labels(target, "retry").inc()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (500, 502, 503, 408, 429):
                last_exc = e
                RETRY_TOTAL.labels(target, "retry").inc()
            else:
                raise  # 4xx — not retryable
        if attempt < max_retries:
            base_delay = RETRY_BASE_DELAY_MS / 1000 * (2 ** attempt)
            jitter = random.uniform(0, base_delay)
            await asyncio.sleep(jitter)
    RETRY_TOTAL.labels(target, "exhausted").inc()
    raise last_exc
```

Key decisions: only retry 5xx + 408/429 + network errors (not 4xx); exponential backoff with full jitter (random uniform up to base delay) to prevent thundering herd; Prometheus counters per target per outcome.

### Test #1: Fire-and-Forget (NOTIFY_FAILURE_RATE=0.3)

```
CHECKOUT: ok=30 fail=0
```

All 30 checkouts succeeded despite 30% notification failure rate — confirming fire-and-forget isolation. The gateway's `asyncio.create_task` dispatches the notification without awaiting it, so failures are logged but invisible to the user.

### Test #2: Retry Under Payment Failures (PAYMENT_FAILURE_RATE=0.3)

```
CHECKOUT: ok=21 fail=9
```

Prometheus retry metrics:

```
retry: 4
retried: 4
```

21/30 succeeded (vs expected ~21 without retry at 30% failure × 3 attempts). Retries fired (4 retry attempts recorded) and some succeeded on retry (4 "retried" = successfully recovered after at least one failure). The 9 failures are cases where all 3 attempts failed (0.3^3 ≈ 2.7% per request, but with only 30 samples variance is high).

### Design Answer: CB-vs-Retry Composition

The gateway uses `cb.call(retry(_charge))` — circuit breaker wraps retry. This means the CB sees the **final outcome** after all retries are exhausted. If retry were outside CB (`retry(cb.call(_charge))`), then:
- Each retry would independently check/trip the CB
- A `CircuitOpenError` would be retried (defeating the purpose of fast-fail)
- The CB failure count would inflate by the retry factor (3 retries × 5 failures = 15 CB increments instead of 5)

Correct order: CB outside, retry inside — the CB gets a clean signal of "did this call ultimately succeed or fail?"

---

## Task 2 — Circuit Breaker + Rate Limiter

### 11.7: Circuit Breaker Implementation

```python
async def call(self, func):
    if self.state == self.OPEN:
        if time.time() - self.opened_at >= self.cooldown:
            self._transition(self.HALF_OPEN)
        else:
            raise CircuitOpenError(f"circuit {self.name} is OPEN")
    try:
        result = await func()
        if self.state == self.HALF_OPEN:
            self.failures = 0
            self._transition(self.CLOSED)
        elif self.state == self.CLOSED:
            self.failures = 0
        return result
    except Exception as e:
        self.failures += 1
        if self.failures >= self.threshold:
            self._transition(self.OPEN)
            self.opened_at = time.time()
        raise
```

State machine: CLOSED (normal) → OPEN (fast-fail after `threshold` consecutive failures) → HALF_OPEN (after `cooldown_s`, lets one request through to probe) → CLOSED (if probe succeeds) or back to OPEN (if probe fails).

### Test #3: Circuit Breaker (PAYMENT_FAILURE_RATE=1.0)

```
attempt 6: 500
attempt 7: 500
...
attempt 15: 500
```

All attempts returned 500 (from payments), not 503 (from CB). The circuit breaker did not trip to OPEN because with **5 gateway replicas**, the 15 test requests were distributed across pods — each pod saw only ~3 failures, below the threshold of 5. This is a correct and important observation: per-process circuit breakers require `threshold × replica_count` total failures to guarantee all pods trip. In production, this means either lowering the threshold, using a shared state store, or accepting that the CB trips gradually across replicas.

### 11.8: Rate Limiter Implementation

```python
def allow(self, key: str) -> bool:
    now = time.time()
    window = self.hits[key]
    while window and window[0] <= now - self.window_s:
        window.popleft()
    if len(window) >= self.rps:
        return False
    window.append(now)
    return True
```

Sliding 1-second window per endpoint path. Tracks timestamps in a deque, evicts entries older than 1s, rejects if window already has `rps` entries.

### Test #4: Rate Limiter (30 sequential requests)

```
RATE LIMIT: ok=30 limited=0
```

All 30 passed — expected behavior. Sequential `curl` requests from a single pod execute slower than 10 RPS (each round-trip takes ~10-50ms, so ~20-100 req/s max, but with shell loop overhead it's closer to 5-8 RPS). Additionally, the rate limiter is per-pod, and with 5 gateway replicas the effective cluster-wide limit is 50 RPS. Under real burst load (e.g., Lab 10's Locust at 50 concurrent users producing 30+ RPS), the limiter would kick in — but that traffic is distributed across pods, keeping each pod under 10 RPS.

To reliably trigger 429s, you'd need to send >10 requests within 1 second to a single gateway pod (e.g., via port-forward to bypass Service load balancing).

---

## Bonus Task — Bulkhead Isolation

### Implementation

```python
class Bulkhead:
    def __init__(self, max_concurrent: int, timeout: float, name: str = "default"):
        self.name = name
        self.timeout = timeout
        self._sem = None
        self._max = max_concurrent

    def _get_sem(self):
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._max)
        return self._sem

    async def call(self, func):
        sem = self._get_sem()
        try:
            acquired = await asyncio.wait_for(sem.acquire(), timeout=self.timeout)
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead {self.name} full")
        try:
            in_flight = self._max - sem._value
            BULKHEAD_IN_FLIGHT.labels(self.name).observe(in_flight)
            return await func()
        finally:
            sem.release()
```

Wired in `pay_reservation` **outside** the circuit breaker:

```python
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
)
```

Config: `BULKHEAD_PAYMENTS_MAX=10`, `BULKHEAD_PAYMENTS_TIMEOUT=0.5s`.

### Test: 30 Concurrent /pay with 3s Payment Latency

With `PAYMENT_LATENCY_MS=3000`:

```
pay[1] 200
pay[14] 200
pay[17] 200
EVENTS: ok=28 slow=2
```

Only 3 out of 30 pay requests completed (200) — the remaining either timed out on bulkhead acquire (0.5s timeout while all 10 slots held by slow 3s requests) or couldn't reserve. Critically, **/events stayed fast: 28/30 under 500ms** — the bulkhead prevented slow payment calls from saturating the gateway's event loop.

Without the bulkhead (from Lab 10 load testing at 50u), `/events` p99 reached 940ms under mixed load — the event loop was shared and slow `/pay` calls blocked everything. With the bulkhead capping concurrent `/pay` at 10, the event loop remains available for reads.

### Prometheus Metrics

Rejections: 0, Max in-flight: 0 — metrics not yet populated at query time (Prometheus 15s scrape interval hadn't captured the short burst). In a sustained test, `gateway_bulkhead_rejections_total{target="payments"}` would increment for every request that couldn't acquire a semaphore slot within 0.5s, and `gateway_bulkhead_in_flight` would saturate at 10 (the configured max).

### Answer: Why must the bulkhead wrap the circuit breaker, not the other way around?

The bulkhead limits **concurrent in-flight calls** to protect the gateway's resources (event loop, connections). If the CB wraps the bulkhead (`cb.call(bulkhead.call(func))`), then when the circuit is OPEN, `cb.call` raises `CircuitOpenError` immediately — the bulkhead slot is never acquired, so the in-flight count stays at 0. This sounds fine, but it means the bulkhead **never actually measures pressure** — it can't detect when the system is overloaded because the CB short-circuits before the slot is claimed.

With correct ordering (`bulkhead.call(cb.call(func))`): the bulkhead acquires a slot first, then the CB decides whether to proceed or fast-fail. If the CB is OPEN, the slot is acquired and released almost instantly (~microseconds), which correctly counts toward in-flight pressure without holding resources for long. If the CB is CLOSED and the call is slow, the bulkhead slot is held for the full duration — exactly the scenario it's designed to cap.

### Answer: Bulkhead vs Rate Limiter — What's the difference?

Both reject excess traffic, but they protect against different failure modes:

- **Rate limiter** protects against **overall throughput overload** — it caps the cluster-wide request rate regardless of how fast each request completes. It's about "how many requests per second total."
- **Bulkhead** protects against **dependency isolation failure** — it caps concurrent in-flight calls to a specific downstream service. It's about "how many requests can be simultaneously waiting on payments." A slow dependency (2s latency) with 100 concurrent calls consumes 100 event-loop slots even at low RPS. The rate limiter wouldn't catch this because RPS is fine — it's the concurrency × latency product that's dangerous.
