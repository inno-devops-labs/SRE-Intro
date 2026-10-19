# Lab 11 — Advanced Microservice Patterns

Tested on 19 October 2026 (UTC) in the existing k3d cluster. Tasks 1 and 2 are complete. The bulkhead bonus is also complete.

The existing Lab 9 manifests are carried forward so ArgoCD manages a complete application, including the data and backup PVCs. Gateway remains a five-replica Rollout. I built `quickticket-gateway:lab11` and `quickticket-notifications:v1`, imported both into k3d, and deployed with `imagePullPolicy: Never`. A normal canary analysis passed before fault injection. Rebuilding/importing these local images is required on a fresh cluster.

One mixedload pod exercised browsing, reservations and payments on a dedicated 100,000-ticket event. Additional checkout clients ran inside the cluster and opened new connections through the gateway Service. Fault settings were restored after the fault tests. ArgoCD self-healing was paused for fault injection and restored on `feature/lab11` afterwards.

## Task 1 — Notifications and retries

### Notifications service

[`app/notifications/main.py`](../app/notifications/main.py) exposes `/notify`, `/health` and `/metrics`. The synchronous notification handler runs in FastAPI's worker pool. Its latency injection does not block the gateway's background task scheduling.

Key handler:

```python
@app.post("/notify")
def notify(body: dict):
    event = body.get("event")
    order_id = body.get("order_id")
    if not event or not order_id:
        raise HTTPException(422, "event and order_id are required")
    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning(f"Notification failed (injected) event={event} order={order_id}")
        raise HTTPException(500, "Notification failed")
    NOTIFY_TOTAL.labels("success").inc()
    log.info(f"Notification sent event={event} order={order_id}")
    return {"status": "sent", "event": event, "order_id": order_id}
```

The middleware emits `notifications_requests_total{method,path,status}` and `notifications_request_duration_seconds{method,path}`; the handler emits `notifications_notify_total{result}`.

`requirements.txt` (same dependencies as Payments):

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

Deployment and Service:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: notifications
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
          value: '0.0'
        - name: NOTIFY_LATENCY_MS
          value: '0'
        resources:
          requests:
            cpu: 50m
            memory: 64Mi
          limits:
            cpu: 200m
            memory: 256Mi
        livenessProbe:
          httpGet:
            path: /health
            port: 8083
          initialDelaySeconds: 10
          periodSeconds: 10
          failureThreshold: 3
        readinessProbe:
          httpGet:
            path: /health
            port: 8083
          periodSeconds: 5
          failureThreshold: 2
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

Gateway sets `NOTIFICATIONS_URL=http://notifications:8083`. Successful order confirmation schedules `_notify_order_confirmed()` using `asyncio.create_task`; the response does not await notification delivery. HTTP notification failures are logged with `raise_for_status()` and remain non-critical. Notifications are reported by `/health` but do not determine its critical verdict.

### Retry implementation

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    """Run up to max_retries total attempts, retrying transient failures only."""
    if max_retries < 1:
        raise ValueError("max_retries must be positive")
    base_delay = RETRY_BASE_DELAY_MS / 1000
    for attempt in range(max_retries):
        try:
            result = await func()
        except Exception as exc:
            retryable = isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                retryable = 500 <= status < 600 or status in (408, 429)
            if not retryable:
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                raise
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(base_delay * (2 ** attempt) + random.uniform(0, base_delay))
        else:
            if attempt > 0:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result
```

`RETRY_MAX=3` means **three total attempts**, not three additional retries. Delay is 100ms × 2^attempt plus 0–100ms jitter. Timeout, connection errors, 5xx, 408 and 429 can retry; other failures are raised immediately.

### HTTP results

| Experiment | Response counts | Client p99 (ms) |
|---|---|---:|
| Notify: 30% failures + 300ms | {'200': 30} | 25.2 |
| Payments: 30% failures | {'200': 28, '500': 2} | 5203.1 |
| Payments: 100% failures | {'500': 16, '503': 64} | 508.1 |
| Payments restored, cooldown elapsed | {'200': 30} | 70.9 |
| 100-request burst | {'200': 90, '429': 10} | 288.1 |
| 30 requests at 5 requests/s | {'200': 30} | 38.3 |

The checkout rows measure `/pay` latency after successful reservation. Burst and low-rate rows measure `/events`. Client p99 uses the nearest-rank sample percentile; small bursts should not be interpreted as long-term SLO measurements.

During notification fault injection, Prometheus `/pay` p99 was:

```text
{} 0.03958333333333327
```

Query: `histogram_quantile(0.99,sum by(le)(rate(gateway_request_duration_seconds_bucket{path="/reserve/{id}/pay"}[1m])))` (seconds). The notification delay was 300ms; checkout remained non-blocking.

Actual notification counters, including background mixedload:

```text
notifications_notify_total{result="success"} 123.0
notifications_notify_total{result="failed"} 45.0
```

Observed notification failure rate: **26.79%**. The configured probability is 30%; a finite sample need not equal it exactly.

Prometheus retry counters after the transient-payment experiment:

```text
{'result': 'retried', 'target': 'payments'} 22
{'result': 'succeeded_after_retry', 'target': 'payments'} 14
{'result': 'exhausted', 'target': 'payments'} 2
```

Query: `sum by (target,result)(gateway_retry_total)`. Nonzero `retried` and `succeeded_after_retry` show actual recovery, not merely successful first attempts. These counters include mixedload. Two of the 30 burst requests exhausted their attempts; a 30% random fault probability does not guarantee the example outcome of at most one failure. The 5.2s client tail also shows that retries do not guarantee low latency: each upstream attempt retains its five-second timeout.

**Why non-blocking notifications?** An email failure must not turn a confirmed purchase into a failed checkout or add its latency to the critical path. In-memory background tasks are best-effort and can be lost during process termination; durable delivery would need a queue/outbox.

**Why retry inside the circuit breaker?** `cb.call(retry(...))` counts one final checkout failure after its bounded attempts and skips retry work when already open. Reversing the wrappers counts each downstream attempt separately and opens the breaker too early. This implementation does not classify `CircuitOpenError` as retryable, so it would not blindly retry that exception even in the reverse order. Retrying real charges also requires payment idempotency; this lab uses the supplied mock payment processor.

## Task 2 — Circuit breaker and rate limiter

### Implementations

```python
class CircuitBreaker:
    """Per-process breaker counting final outcomes after retries."""

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
        """Record a state change. Use this from your .call implementation
        so transitions show up in Prometheus."""
        if self.state != new_state:
            log.warning(f"circuit[{self.name}] {self.state} -> {new_state}")
            CB_STATE_TRANSITIONS.labels(new_state).inc()
        self.state = new_state

    async def call(self, func):
        if self.state == self.OPEN:
            if time.time() - self.opened_at < self.cooldown:
                raise CircuitOpenError(f"circuit[{self.name}] OPEN")
            self._transition(self.HALF_OPEN)
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

```python
class RateLimiter:
    """Per-endpoint, per-process one-second sliding window."""

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

Defaults are five consecutive final failures, 30s cooldown, and 10 requests/s per normalized endpoint **per gateway process**. The simple lab breaker follows the specified state machine; it does not serialize concurrent HALF_OPEN probes or share state between replicas.

The 80-checkout failure test above distinguishes retry-exhausted HTTP 500 from immediate circuit-open HTTP 503. After restoring Payments and waiting 35 seconds, the recovery burst succeeded.

Circuit transitions after the failure test:

```text
{'to': 'OPEN'} 5
```

After recovery:

```text
{'to': 'OPEN'} 5
{'to': 'HALF_OPEN'} 5
{'to': 'CLOSED'} 5
```

Query: `sum by (to)(gateway_circuit_breaker_transitions_total)`. Aggregated counts reflect independent state on five pods. Mixedload also contributed failures, so the 80-request burst alone does not account for every breaker transition.

For the rate test, mixedload was stopped to isolate the burst. The 100 requests used five concurrent client tasks; the exact 200/429 split depends on connection distribution and the sliding window, rather than being guaranteed 50/50.

Observed rejected-response header:

```text
{
  "status": 429,
  "retry-after": "1"
}
```

Prometheus rejections:

```text
{'path': '/events'} 10
```

Query: `sum by (path)(gateway_rate_limit_rejections_total)`. The subsequent 5 requests/s test verifies that normal traffic is allowed.

## Validation and cleanup

Eleven unit tests passed inside the built gateway image: transient retry classification/backoff, immediate non-retryable errors, final error propagation, cancellation, breaker fast-fail/recovery, failed HALF_OPEN probe, failure reset, and rate-window expiration/path isolation. Kubernetes server-side manifest validation passed.

Fault probabilities and injected latency are zero. Temporary clients, mixedload, and their synthetic event/orders were removed. The gateway and notifications remain deployed for Lab 12; database persistence and scheduled backups remain enabled.

## Bonus — payment bulkhead

The final image is `quickticket-gateway:lab11-bulkhead`, built from `app/gateway` and imported into k3d; `k8s/gateway.yaml` uses this tag. The gateway now limits concurrent payment operations to 10 per process, with a 0.5-second acquire timeout. A timed-out request gets HTTP 503 with `Payment service busy (bulkhead full)`.

```python
class Bulkhead:
    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        if max_concurrent < 1 or acquire_timeout_s <= 0:
            raise ValueError("Bulkhead capacity and timeout must be positive")
        self.name = name
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.acquire_timeout_s = acquire_timeout_s

    async def call(self, func):
        try:
            await asyncio.wait_for(self.semaphore.acquire(), self.acquire_timeout_s)
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full") from None
        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self.semaphore.release()
```

The `/pay` composition is:

```python
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
)
```

### Live comparison

Tested on 19 October 2026, around 08:47 UTC. Payments had `PAYMENT_LATENCY_MS=3000` and failure rate 0. Two temporary gateway pods used the same settings and downstream services (two events and two payments replicas). One ran the previous image without a bulkhead; the other ran the new image. Each test addressed one pod directly: distributing 30 requests over five pods would not reliably fill a per-pod limit of 10. Both test pods used `RATE_LIMIT_RPS=1000` so rate limiting did not mask bulkhead behavior; the application default remains 10.

For each variant, three batches each used 30 pre-created reservations, 30 concurrent payments, and a separate client sampling 40 `/events` requests at 0.1-second intervals. Reservations were created before the concurrent phase to isolate payment pressure. p99 is the nearest-rank client measurement across 120 samples, including error responses.

| Measurement | Without bulkhead | With bulkhead |
|---|---:|---:|
| Payments HTTP 200 | 90 | 30 |
| Payments HTTP 503 | 0 | 60 |
| `/events` HTTP 200 | 119 | 120 |
| `/events` HTTP 502 | 1 | 0 |
| `/events` p99 | 458.39 ms | 18.84 ms |
| `EVENTS: ok / slow` (duration ≤ / > 500 ms) | 120 / 0 | 120 / 0 |

The timing-only `ok` count includes the 502; status counts above distinguish it. Events logs identify the unbounded run's failure as `psycopg2.pool.PoolError: connection pool exhausted` during the confirmation burst. No >2-second event latency occurred: asynchronous waiting does not inherently block the event loop, so the lab's illustrative prediction is not guaranteed. The bounded run admitted 10 payments and rejected 20 per batch; client rejection times were 525–551 ms including HTTP overhead.

Prometheus results for the bounded pod:

```text
gateway_bulkhead_rejections_total{pod="lab11-bounded",target="payments"} = 60
max_over_time(gateway_bulkhead_in_flight{pod="lab11-bounded",target="payments"}[2m]) = 10
```

After completion the in-flight gauge returned to 0. The temporary scrape selector was restored after collecting evidence.

### Design answers

**Why outside the circuit breaker?** One slot covers the whole operation, including retries and backoff, while the breaker sees the final outcome. Admission failures occur before the breaker and therefore do not count as payment failures. An already-open breaker briefly acquires and immediately releases a slot; it performs no slow call. Putting the breaker outside would instead let bulkhead acquisition timeouts affect its failure count. Releasing the slot in `finally` handles success, errors, and cancellation.

**Bulkhead versus rate limiter:** the bulkhead bounds outstanding operations to a slow dependency; the rate limiter bounds arrivals per second per endpoint. Low request rates can still accumulate many slow operations. Both implementations here are per process, not cluster-wide quotas.

Three additional unit tests cover capacity/timeouts, release on exceptions/cancellation, and cancellation of a waiting caller. All 11 tests passed in the built image. Temporary gateway/client pods and their synthetic data were removed, and payments latency restored to zero.
