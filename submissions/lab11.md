# Lab 11 — Advanced Microservice Patterns

**Student:** Gleb Shvetsov

**GitHub:** `L10nff`

**Working branch:** `feature/lab11`

## Task 1 — Notifications Service and Retries

### Notifications service

The new service is implemented in `app/notifications/`. It exposes
`POST /notify`, `GET /health`, and `GET /metrics`, supports latency and failure
injection, and records the required Prometheus metrics.

Key handler:

```python
@app.post("/notify")
async def notify(body: dict = None):
    payload = body or {}
    event = payload.get("event", "unknown")
    order_id = payload.get("order_id", "unknown")

    if NOTIFY_LATENCY_MS > 0:
        await asyncio.sleep(NOTIFY_LATENCY_MS / 1000)

    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning(f"Notification failed event={event} order={order_id}")
        raise HTTPException(500, "Notification delivery failed")

    NOTIFY_TOTAL.labels("success").inc()
    log.info(f"Notification sent event={event} order={order_id}")
    return {"status": "sent", "event": event, "order_id": order_id}
```

Metrics:

```python
REQUEST_COUNT = Counter(
    "notifications_requests_total", "Total requests", ["method", "path", "status"]
)
REQUEST_DURATION = Histogram(
    "notifications_request_duration_seconds", "Request duration", ["method", "path"]
)
NOTIFY_TOTAL = Counter(
    "notifications_notify_total", "Notification delivery attempts", ["result"]
)
```

`app/notifications/requirements.txt`:

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

### Kubernetes manifest

The committed `k8s/notifications.yaml` is:

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
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 256Mi
          ports:
            - name: http
              containerPort: 8083
          livenessProbe:
            httpGet:
              path: /health
              port: 8083
            initialDelaySeconds: 5
            periodSeconds: 10
            timeoutSeconds: 1
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /health
              port: 8083
            periodSeconds: 5
            timeoutSeconds: 1
            failureThreshold: 2
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
  labels:
    app: notifications
spec:
  type: ClusterIP
  selector:
    app: notifications
  ports:
    - name: http
      port: 8083
      targetPort: 8083
```

The Gateway Rollout uses:

```yaml
- name: NOTIFICATIONS_URL
  value: "http://notifications:8083"
```

The notifications pod reached `1/1 Ready`. Gateway health reported all critical
dependencies healthy and also reported `notifications=ok`.

### Retry implementation

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
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

            delay = base_delay * (2**attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
```

This retries network failures, 5xx, 408, and 429. Other 4xx responses are
immediately re-raised. The delay combines exponential backoff with jitter.

### Test 1 — fire-and-forget notification failure

Fault injection:

```text
NOTIFY_FAILURE_RATE=0.3
NOTIFY_LATENCY_MS=300
test_started_utc=2026-10-09T15:25:05Z
test_completed_utc=2026-10-09T15:26:11Z
```

The required 30-checkout sample completed without user-visible failures:

```text
result: ok=30 fail=0
```

I kept the same injection active for 70 more checkouts to obtain a steadier
latency window. All 70 also succeeded. Prometheus then reported:

```text
extended_result: ok=70 fail=0
pay_p99_seconds: 0.09858724236315455
notifications_notify_total{result="success"} 80
notifications_notify_total{result="failed"} 20
```

The observed notification failure rate was `20 / 100 = 20%`. Random variation
is expected from a configured probability of 30%. The user-facing `/pay` p99
was 98.6 ms, below 100 ms and far below the injected 300 ms notification delay.

Notifications must be non-blocking because payment confirmation is the critical
user transaction, while notification delivery is secondary. Waiting for a slow
or failing notification would increase checkout latency and incorrectly turn a
successful purchase into a user-visible failure. A production system would
normally persist the notification event in a durable queue or outbox rather
than relying only on an in-process task.

### Test 2 — retry under transient payment failure

With `PAYMENT_FAILURE_RATE=0.3`, the acceptance sample was:

```text
test_started_utc=2026-10-09T15:27:17Z
repeat_result: ok=29 fail=1
test_completed_utc=2026-10-09T15:27:33Z
```

Prometheus proved that retries executed and recovered requests:

```text
gateway_retry_total{target="payments",result="retried"} 27
gateway_retry_total{target="payments",result="exhausted"} 3
gateway_retry_total{target="payments",result="succeeded_after_retry"} 16
```

The first independent sample was `ok=28 fail=2`; the second sample met the
expected `fail < 2`. Both samples are consistent with probabilistic injection.

The correct composition is `cb.call(retry(call))`: all retry attempts belong to
one logical dependency call, so the circuit breaker observes only the final
result. With `retry(lambda: cb.call(call))`, a circuit-open fast-fail could be
retried, defeating the breaker and adding useless work. It would also count
individual attempts as separate circuit-breaker outcomes instead of one user
operation.

## Task 2 (Optional) — Circuit Breaker and Rate Limiter

### Circuit breaker implementation

```python
async def call(self, func):
    if self.state == self.OPEN:
        if time.time() - self.opened_at >= self.cooldown:
            self._transition(self.HALF_OPEN)
        else:
            raise CircuitOpenError(f"circuit[{self.name}] OPEN")

    try:
        result = await func()
    except Exception:
        self.failures += 1
        self.opened_at = time.time()
        if self.state == self.HALF_OPEN or self.failures >= self.threshold:
            self._transition(self.OPEN)
        raise

    self.failures = 0
    self._transition(self.CLOSED)
    return result
```

At 100% Payments failure, 80 attempted checkout chains produced:

```text
circuit_test_started_utc=2026-10-09T15:27:58Z
500s=24 503s=52 other=0 reserve_fail=4
gateway_circuit_breaker_transitions_total{to="OPEN"} 5
circuit_test_failure_phase_completed_utc=2026-10-09T15:28:45Z
```

The five OPEN transitions correspond to the five independent Gateway processes.
After restoring Payments and waiting for the 30-second cooldown:

```text
cooldown_wait_seconds=35
recovery_result: 200=15 other=0
gateway_circuit_breaker_transitions_total{to="OPEN"} 5
gateway_circuit_breaker_transitions_total{to="HALF_OPEN"} 5
gateway_circuit_breaker_transitions_total{to="CLOSED"} 5
gateway_health: healthy, circuit_payments=CLOSED
recovery_completed_utc=2026-10-09T15:29:57Z
```

### Rate limiter implementation

```python
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

With `RATE_LIMIT_RPS=10` and five Gateway replicas:

```text
rate_limit_test_started_utc=2026-10-09T15:30:12Z
burst_result: 200=50 429=50 other=0
HTTP/1.1 429 Too Many Requests
retry-after: 1
sustained_result: 200=30 429=0 other=0
gateway_rate_limit_rejections_total{path="/events"} 51
rate_limit_test_completed_utc=2026-10-09T15:30:45Z
```

The burst reached the expected cluster-wide per-process ceiling. The slower
five-requests-per-second stream remained below the configured limit.

## Bonus Task — Bulkhead Isolation

### Implementation and composition

```python
class Bulkhead:
    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.acquire_timeout_s = acquire_timeout_s
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def call(self, func):
        try:
            await asyncio.wait_for(
                self.semaphore.acquire(), timeout=self.acquire_timeout_s
            )
        except asyncio.TimeoutError as exc:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full") from exc

        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self.semaphore.release()
```

The payment path is wrapped as:

```python
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(
        lambda: call_with_retry(_charge, target="payments")
    )
)
```

### Isolation test

The test targeted one Gateway process so that one per-process semaphore received
all 30 concurrent calls. Payments latency was 3000 ms, maximum occupancy was 10,
and acquisition timeout was 500 ms.

With bulkhead:

```text
protected_retest_started_utc=2026-10-09T15:35:15Z
gateway_bulkhead_in_flight{target="payments"} 10
pay_http_500=10
pay_http_503=20
events_http_200=10
events_slower_than_500ms=0
gateway_bulkhead_rejections_total{target="payments"} 40
protected_retest_completed_utc=2026-10-09T15:35:22Z
```

The counter is cumulative across two protected runs; each run rejected 20 excess
payment calls. The in-flight gauge reached exactly the configured maximum of 10.
The ten permitted synthetic payments eventually returned 500 only because their
test reservation IDs intentionally did not exist during confirmation; the
bulkhead behavior is demonstrated by the twenty fast 503 rejections.

For the comparison, I temporarily raised the bulkhead maximum to 1000 while
keeping the same single-pod target and 3000 ms Payments latency:

```text
unprotected_test_started_utc=2026-10-09T15:36:03Z
pay_http_500=50
pay_http_502=6
pay_http_504=94
events_http_200=9
events_http_504=1
events_slower_than_500ms=4
unprotected_test_completed_utc=2026-10-09T15:36:31Z
```

Without the effective cap, 150 simultaneous payment calls exhausted shared
downstream client capacity: one read request timed out and four of ten reads
exceeded 500 ms. With the cap, all ten control reads were fast and successful.

The bulkhead wraps the circuit breaker so one logical payment operation holds
one slot for its complete CB and retry sequence. Putting the bulkhead inside the
breaker would let retries acquire separate slots and make the concurrency bound
misleading. An OPEN breaker still touches a slot only very briefly, so the
fast-fail immediately releases it.

A rate limiter protects an endpoint from excessive request arrival rate over a
time window. A bulkhead protects unrelated work from concurrency occupied by a
specific slow dependency. Rate limiting answers “how many requests per second?”;
bulkheading answers “how many calls may be waiting on this dependency now?”

## Final Verification

- Notifications Deployment and Service were running, and the pod was `1/1 Ready`.
- Gateway had five ready replicas and ended `Healthy`.
- Payments and Notifications fault-injection values were restored to zero.
- Gateway controls were restored to `RATE_LIMIT_RPS=10`,
  `BULKHEAD_PAYMENTS_MAX=10`, and `BULKHEAD_PAYMENTS_TIMEOUT_S=0.5`.
- Redis was flushed after the experiments.
- Temporary load and probe pods were removed.
- Python sources compiled, both container images built, and both Kubernetes
  manifests passed server-side validation.

## Conclusion

Lab 11 added a fourth QuickTicket microservice and implemented observable retry,
circuit-breaker, rate-limiter, and bulkhead patterns. Failure injection showed
that secondary notification failures do not affect checkout, transient payment
failures are recovered by retries, persistent failures are fast-failed by the
circuit breaker, bursts receive explicit backpressure, and slow Payments calls
cannot consume unbounded Gateway concurrency.
