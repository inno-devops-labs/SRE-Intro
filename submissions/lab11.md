# Lab 11 — Advanced Microservice Patterns

## Task 1 — Notifications service and retries

### Implementation

`app/notifications/` contains a FastAPI service with `POST /notify`, `GET /health`, and `GET /metrics`. The service accepts the `NOTIFY_FAILURE_RATE` and `NOTIFY_LATENCY_MS` fault-injection variables. Its Prometheus metrics are:

- `notifications_requests_total{method,path,status}`;
- `notifications_request_duration_seconds{method,path}`;
- `notifications_notify_total{result}`.

The service dependency file is `app/notifications/requirements.txt`, identical to the payments dependency file. The deployment definition is `k8s/notifications.yaml`. The key implementation shape is:

```python
@app.post("/notify")
def notify(body: dict = None):
    payload = body or {}
    event = payload.get("event", "unknown")
    order_id = payload.get("order_id", "unknown")
    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        return JSONResponse(status_code=500, content={"error": "Notification failed"})
    NOTIFY_TOTAL.labels("success").inc()
    return {"status": "sent", "event": event, "order_id": order_id}
```

`k8s/notifications.yaml` deploys one `quickticket-notifications:v1` pod on port 8083 and exposes it through the `notifications` ClusterIP Service. The gateway is configured with `NOTIFICATIONS_URL=http://notifications:8083`.

The payment handler schedules `_notify_order_confirmed()` with `asyncio.create_task()`. Notification failures are logged and swallowed after the user-facing payment response has been produced, so the notification dependency is best-effort.

### Retry implementation

`call_with_retry()` performs up to `RETRY_MAX` attempts. It retries `httpx.TimeoutException`, `httpx.ConnectError`, and HTTP 5xx/408/429 responses. Other HTTP errors are recorded as `non_retryable` and raised immediately. Before another attempt it records `retried` and sleeps with exponential backoff plus jitter. A successful attempt after a retry records `succeeded_after_retry`; exhaustion records `exhausted`.

The implemented function is:

```python
base_delay = RETRY_BASE_DELAY_MS / 1000
for attempt in range(max_retries):
    try:
        result = await func()
        if attempt > 0:
            RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
        return result
    except Exception as exc:
        retryable = isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            retryable = status >= 500 or status in (408, 429)
        if not retryable:
            RETRY_TOTAL.labels(target, "non_retryable").inc()
            raise
        if attempt == max_retries - 1:
            RETRY_TOTAL.labels(target, "exhausted").inc()
            raise
        RETRY_TOTAL.labels(target, "retried").inc()
        delay = base_delay * (2**attempt) + random.uniform(0, base_delay)
        await asyncio.sleep(delay)
```

The composition in `/pay` is `payments_cb.call(lambda: call_with_retry(_charge, "payments"))`. This keeps all retry attempts inside one circuit-breaker decision. Reversing the order would cause the outer retry loop to retry a fast `CircuitOpenError`, defeating the circuit breaker's purpose.

### Test 1 — fire-and-forget notification failures

With `NOTIFY_FAILURE_RATE=0.3` and `NOTIFY_LATENCY_MS=300`, the checkout burst produced:

```text
result: ok=30 fail=0
```

The notification service recorded 22 successful and 8 failed notification attempts:

```text
notifications_notify_total{result="success"} 22.0
notifications_notify_total{result="failed"} 8.0
```

The checkout result was `ok=30 fail=0`. The gateway p99 for `/reserve/{id}/pay` was `0.23889493772382342` seconds. The no-injection baseline was `0.2352027224932444` seconds. The difference was approximately 3.7 ms while notifications were injected with 300 ms latency, demonstrating that notification latency did not enter the `/pay` critical path. The absolute `<100 ms` target was not reached, but the p99 remained unchanged by the notification delay.

### Test 2 — retries under payment failure

With `PAYMENT_FAILURE_RATE=0.3`, the checkout burst produced:

```text
result: ok=29 fail=1
```

The gateway retry counters were non-zero:

```text
gateway_retry_total{target="payments",result="retried"} 9
gateway_retry_total{target="payments",result="succeeded_after_retry"} 6
gateway_retry_total{target="payments",result="exhausted"} 1
```

This shows that transient payment failures caused retries, most retried requests recovered, and one request exhausted its attempts. Payment failure injection was restored to `0.0` after the test.

Notifications should be non-blocking because they are secondary to confirming the payment. A slow or unavailable notification service must not add its latency to `/pay` or turn a successful payment into a user-visible failure. The event can be retried or reconciled separately.

## Task 2 — Circuit breaker and rate limiter

### Circuit breaker

`CircuitBreaker.call()` implements the `CLOSED → OPEN → HALF_OPEN → CLOSED` state machine. It fast-fails while open, probes once the cooldown expires, resets failures after success, and records transitions with `gateway_circuit_breaker_transitions_total{to}`. `CircuitOpenError` is mapped to HTTP 503 by `/pay`.

The implementation tracks `failures`, `state`, and `opened_at`; it transitions to `HALF_OPEN` after the cooldown, closes after a successful probe, and reopens after a failed probe or after reaching the threshold.

During the 100% payment-failure experiment, the Prometheus transition metric recorded four `OPEN` transitions across the gateway replicas. Circuit-breaker state is per gateway process, so not every process necessarily reaches its threshold in one run.

The Prometheus status-rate query included both 500 and 503 responses:

```text
status=200  0.1662379010854216
status=500  0.06992152186646282
status=503  0.1448517666999548
```

The test output was:

```text
500s=22 503s=3 reserve_fail=25 other=0
```

After restoring payments and waiting through the 30-second cooldown, a separate event with available tickets produced:

```text
recovery 200s=15 failures=0 503s=0
```

The Prometheus transition query returned four transitions for each state:

```text
to="OPEN"      4
to="HALF_OPEN" 4
to="CLOSED"    4
```

### Rate limiter

`RateLimiter.allow()` maintains a one-second sliding window per normalized endpoint. The middleware excludes `/health` and `/metrics`, returns HTTP 429 when the window is full, sets `Retry-After: 1`, and increments `gateway_rate_limit_rejections_total{path}`.

The window implementation removes timestamps older than one second, rejects when `len(window) >= RATE_LIMIT_RPS`, and appends the current timestamp for accepted requests.

The burst test produced:

```text
200=97 429=3
```

Prometheus recorded three `/events` rejections. With the limiter temporarily tuned to one request per second for a deterministic header check, the response contained:

```text
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

The rejection query returned:

```text
gateway_rate_limit_rejections_total{path="/events"} 3
```

The temporary tuning was removed and the gateway manifest was reapplied afterward.

A sustained below-limit sequence produced:

```text
sustained 200=30 429=0
```

The burst and sustained results must be interpreted per gateway process. With five replicas, each process owns its own one-second window; the effective cluster ceiling is approximately the per-process limit multiplied by the number of active replicas.

## Bonus — Bulkhead isolation

`Bulkhead.call()` uses a per-target `asyncio.Semaphore`, an acquisition timeout, the `gateway_bulkhead_in_flight{target}` gauge, and the `gateway_bulkhead_rejections_total{target}` counter. `/pay` wraps the circuit breaker and retry chain as:

```python
payments_bulkhead.call(
    lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
)
```

This order keeps one slot occupied across all retry attempts while allowing the circuit breaker and retry logic to remain inside the dependency-concurrency boundary. A bulkhead protects dependency concurrency; a rate limiter protects request admission over time. They reject different overload dimensions.

The bulkhead acquires the semaphore with `asyncio.wait_for`, increments the in-flight gauge, awaits the nested call, and decrements/releases in `finally`. Acquisition timeout raises `BulkheadFullError`, which `/pay` maps to 503.

With `PAYMENT_LATENCY_MS=3000`, the concurrent probe observed:

```text
EVENTS: ok=29 slow=1
```

The saturation run produced 20 fast-failures and 10 successful slow calls:

```text
EVENTS: ok=28 slow=2 ids=30
```

The rejection counter reached 20:

```text
gateway_bulkhead_rejections_total{target="payments"} 20
```

The final saturation run kept payments calls active long enough for Prometheus to observe the configured cap:

```text
max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m]) = 10
gateway_bulkhead_rejections_total{target="payments"} = 10
```

This proves that the semaphore reached its maximum and rejected additional callers.

A comparison run with the bulkhead limit temporarily raised to 100 produced:

```text
WITHOUT_BULKHEAD EVENTS: ok=20 slow=0
```

The comparison used one gateway replica and the same slow-payments injection. No `/events` degradation was observed after raising the concurrency limit, so the expected slow-event contrast was not reproduced.

### Proof-of-work code excerpts

The notifications dependency file contains:

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

The Kubernetes resource uses one local image-pinned replica and a ClusterIP Service:

```yaml
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

The implemented `CircuitBreaker.call()` body is:

```python
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
```

The implemented `RateLimiter.allow()` body is:

```python
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

The implemented `Bulkhead.call()` body is:

```python
try:
    await asyncio.wait_for(
        self.semaphore.acquire(), timeout=self.acquire_timeout_s
    )
except asyncio.TimeoutError as exc:
    BULKHEAD_REJECTIONS.labels(self.name).inc()
    raise BulkheadFullError(f"bulkhead[{self.name}] is full") from exc

BULKHEAD_IN_FLIGHT.labels(self.name).inc()
try:
    return await func()
finally:
    BULKHEAD_IN_FLIGHT.labels(self.name).dec()
    self.semaphore.release()
```

All injected payment and notification settings were restored to zero after the experiments. The final cluster check showed five gateway pods, one notifications pod, and one payments pod Ready.

The experiment outputs are stored under `submissions/assets/lab11/`, including the circuit-breaker counts and recovery output, the sustained rate-limit result, the notification metrics, and the final bulkhead rejection/cap queries. The final bulkhead cap query observed 10.

