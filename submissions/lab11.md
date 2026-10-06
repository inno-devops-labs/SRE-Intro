# Lab 11 — Advanced Microservice Patterns

## Summary

Added a fourth QuickTicket service (`notifications`) and implemented the gateway resilience patterns:

- retry with exponential backoff + jitter
- circuit breaker in front of payments
- per-endpoint sliding-window rate limiter
- bonus bulkhead around the payments call path

Final `/pay` composition (outside → inside):

```text
bulkhead → circuit breaker → retry → payments /charge
```

---

## Task 1 — Notifications service + retries

### `app/notifications/main.py`

```python
"""QuickTicket Notifications — Mock notifier with tunable failures (Lab 11)."""

import os
import time
import random
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

logging.basicConfig(
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"notifications","msg":"%(message)s"}',
    level=logging.INFO,
)
log = logging.getLogger("notifications")

app = FastAPI(title="QuickTicket Notifications", version="1.0.0")

REQUEST_COUNT = Counter("notifications_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("notifications_request_duration_seconds", "Request duration", ["method", "path"])
NOTIFY_TOTAL = Counter("notifications_notify_total", "Total notify attempts", ["result"])


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


@app.get("/metrics")
def metrics():
    from starlette.responses import Response
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/notify")
def notify(body: dict = None):
    body = body or {}
    event = body.get("event", "unknown")
    order_id = body.get("order_id", "unknown")

    if NOTIFY_LATENCY_MS > 0:
        delay = NOTIFY_LATENCY_MS / 1000
        log.info(f"Injecting {NOTIFY_LATENCY_MS}ms latency for {order_id}")
        time.sleep(delay)

    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning(f"Notification failed (injected) event={event} order={order_id}")
        raise HTTPException(500, "Notification processing failed")

    NOTIFY_TOTAL.labels("success").inc()
    log.info(f"Notification sent: event={event} order={order_id}")
    return {"status": "sent", "event": event, "order_id": order_id}
```

### `app/notifications/requirements.txt`

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

### `k8s/notifications.yaml`

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
          env:
            - name: NOTIFY_FAILURE_RATE
              value: "0.0"
            - name: NOTIFY_LATENCY_MS
              value: "0"
          ports:
            - containerPort: 8083
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
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 256Mi
---
apiVersion: v1
kind: Service
metadata:
  name: notifications
  labels:
    app: notifications
spec:
  selector:
    app: notifications
  ports:
    - port: 8083
      targetPort: 8083
```

### Gateway notification wiring

`k8s/gateway.yaml` sets:

```yaml
- name: NOTIFICATIONS_URL
  value: "http://notifications:8083"
```

The gateway still treats notifications as non-critical:

```python
critical_ok = checks["events"] == "ok" and checks["payments"] == "ok"
```

### `call_with_retry()` implementation

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    base_delay = RETRY_BASE_DELAY_MS / 1000.0
    last_exc = None
    for attempt in range(max_retries):
        try:
            result = await func()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if not (status in (408, 429) or 500 <= status < 600):
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                raise
            last_exc = e
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            last_exc = e
        else:
            if attempt > 0:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result
        if attempt == max_retries - 1:
            RETRY_TOTAL.labels(target, "exhausted").inc()
            raise last_exc
        delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
        RETRY_TOTAL.labels(target, "retried").inc()
        await asyncio.sleep(delay)
    raise last_exc
```

### Test #1 — fire-and-forget under notify failure

Injection:

```bash
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
kubectl rollout status deployment/notifications --timeout=60s
```

Checkout burst:

```bash
kubectl run checkout-burst-2 --image=curlimages/curl:latest --rm -i --restart=Never --quiet \
  --command -- sh -c '
ok=0; fail=0
for i in $(seq 1 30); do
  RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
  RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\([^\"]*\).*/\1/p")
  if [ -z "$RID" ]; then fail=$((fail+1)); continue; fi
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
  if [ "$CODE" = "200" ]; then ok=$((ok+1)); else fail=$((fail+1)); fi
  sleep 0.1
done
echo "result: ok=$ok fail=$fail"
'
```

Result:

```text
result: ok=30 fail=0
```

`/pay` p99 during the 300ms notify-latency injection:

```promql
histogram_quantile(0.99,
  sum by (le) (
    rate(gateway_request_duration_seconds_bucket{path="/reserve/{id}/pay"}[2m])
  )
)
```

```text
0.024750493410474576
```

That is ~24.8ms, so the injected 300ms notification latency did not inflate the user-facing `/pay` path.

Notifications pod `/metrics`:

```text
notifications_notify_total{result="success"} 47.0
notifications_notify_total{result="failed"} 13.0
```

Observed notify failure ratio: `13 / 60 = 21.7%`, consistent with a stochastic 30% injection over a small sample.

### Test #2 — retries under transient payment failure

Injection:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
kubectl rollout status deployment/payments --timeout=60s
```

Checkout burst:

```text
result: ok=29 fail=1
```

Prometheus retry counters:

```promql
sum by (target, result) (increase(gateway_retry_total[2m]))
```

```text
target="payments", result="retried"                 5.218
target="payments", result="succeeded_after_retry"   4.419
```

Both `retried` and `succeeded_after_retry` are non-zero, so retries fired and recovered failed payment attempts.

---

## Task 2 — Circuit breaker + rate limiter

### `CircuitBreaker` implementation

```python
class CircuitOpenError(Exception):
    """Raised by CircuitBreaker.call when the circuit is open (fast-fail)."""


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

### Circuit breaker OPEN under 100% payment failure

Injection:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0
kubectl rollout status deployment/payments --timeout=60s
```

80-attempt probe, counting only attempts that reached `/pay`:

```text
500s=24 503s=36
```

The `503`s are the fast-fail response mapped from `CircuitOpenError`.

### Circuit breaker CLOSED after recovery

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
kubectl rollout status deployment/payments --timeout=60s
sleep 35
```

Recovery probe:

```text
recovery: ok=15 fail=0
```

Prometheus transitions:

```promql
sum by (to) (gateway_circuit_breaker_transitions_total)
```

```text
to="OPEN"       5
to="HALF_OPEN"  5
to="CLOSED"     5
```

Five transitions per state are expected because each of the 5 gateway replicas keeps its own in-process circuit breaker.

### `RateLimiter` implementation

```python
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

### Rate limiter burst

100 rapid `/events` requests:

```text
200=50 429=50
```

This matches the expected cluster ceiling of `5 pods × 10 rps/pod = 50 rps`.

`Retry-After` header observed on a 429:

```text
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

Sustained below-limit load:

```text
sustained 200=30 429=0
```

Prometheus rejection counter:

```promql
sum by (path) (gateway_rate_limit_rejections_total)
```

```text
path="/events"              55
path="/reserve/{id}/pay"    6
path="/events/{id}/reserve" 6
```

---

## Bonus Task — Bulkhead isolation

### `Bulkhead` implementation

```python
class BulkheadFullError(Exception):
    """Raised by Bulkhead.call when the pool is full and acquire times out."""


class Bulkhead:
    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.sem = asyncio.Semaphore(max_concurrent)
        self.acquire_timeout_s = acquire_timeout_s

    async def call(self, func):
        try:
            await asyncio.wait_for(self.sem.acquire(), timeout=self.acquire_timeout_s)
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full")
        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self.sem.release()
```

Configuration:

```python
BULKHEAD_PAYMENTS_MAX = int(os.getenv("BULKHEAD_PAYMENTS_MAX", "10"))
BULKHEAD_PAYMENTS_TIMEOUT_S = float(os.getenv("BULKHEAD_PAYMENTS_TIMEOUT_S", "0.5"))
payments_bulkhead = Bulkhead("payments", BULKHEAD_PAYMENTS_MAX, BULKHEAD_PAYMENTS_TIMEOUT_S)
```

Wired into `/pay`:

```python
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
)
```

Fast-fail mapping:

```python
except (CircuitOpenError, BulkheadFullError):
    log.error("payments fast-failed (circuit open or bulkhead full)")
    raise HTTPException(503, "Payment service temporarily unavailable (fast-fail)")
```

### With bulkhead: slow payments do not slow `/events`

Injection:

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=3000 PAYMENT_FAILURE_RATE=0.0
kubectl rollout status deployment/payments --timeout=60s
```

Saturation client: 155 `/pay` requests + 30 concurrent `/events` samples. The saturation test intentionally uses synthetic reservation IDs so it does not exhaust real Redis ticket holds. The `500`s below are post-payment confirmation failures for those synthetic IDs; they occur after the payments call and do not affect the bulkhead measurement.

Result:

```json
{
  "pay_total": 155,
  "pay_status": {
    "500": 62,
    "429": 5,
    "503": 88
  },
  "events_total": 30,
  "events_ok": 30,
  "events_p95_s": 0.02808856964111328,
  "max_in_flight": 50.0,
  "per_pod_max_in_flight": {
    "18081": 10.0,
    "18082": 10.0,
    "18083": 10.0,
    "18084": 10.0,
    "18085": 10.0
  },
  "bulkhead_rejections_delta": 88.0
}
```

Interpretation:

- `/events` p95 stayed at ~28ms while payments were injected with 3s latency.
- Each gateway pod saturated at exactly `BULKHEAD_PAYMENTS_MAX=10`.
- Total cluster in-flight payments calls saturated at `5 pods × 10 = 50`.
- Extra payment requests fast-failed with `503` after the 500ms acquire timeout.

Prometheus:

```promql
sum by (target) (gateway_bulkhead_rejections_total)
```

```text
target="payments"  88
```

```promql
max_over_time(gateway_bulkhead_in_flight{target="payments"}[5m])
```

Prometheus observed the cap binding at `10` on multiple gateway pods:

```text
pod="gateway-5bfdcf55f8-jfwxf"  10
pod="gateway-5bfdcf55f8-nv9w4"  10
pod="gateway-5bfdcf55f8-ccrls"  10
```

The direct per-pod `/metrics` sampler recorded `10` on all five pods.

### Without bulkhead contrast

The bulkhead wiring was temporarily bypassed, the gateway image was rebuilt/reimported, and payments were injected with `PAYMENT_LATENCY_MS=10000`.

Result:

```json
{
  "pay_total": 480,
  "pay_status": {
    "504": 427,
    "502": 53
  },
  "events_total": 96,
  "events_ok": 96,
  "events_slow": 0,
  "events_p95_s": 0.04268503189086914
}
```

In this scaffold, removing the bulkhead did not make `/events` slow because the gateway uses non-blocking `httpx.AsyncClient`; slow payments occupy awaited I/O tasks, not the Python event loop. The bulkhead therefore still matters because it bounds concurrent outbound payment calls, prevents unbounded growth of in-flight dependency work, and converts dependency saturation into a deterministic fast-fail `503` instead of timeout/retry amplification.

The bulkhead wiring was restored after this experiment.

---

## Design answers

### Why should notifications be non-blocking (fire-and-forget)?

Notifications are a best-effort side effect after the user-critical checkout path has succeeded. The user cares that the reservation was paid and confirmed; they do not need the email/push notification to complete before getting their response.

If the gateway awaited notifications:

- every `/pay` would inherit notification latency;
- notification outages would turn into checkout failures;
- `/health` would need to decide whether a non-critical service makes the whole system unhealthy.

With `asyncio.create_task(_notify_order_confirmed(...))`, the `/pay` response returns immediately. Notification failures are logged and visible in `notifications_notify_total`, but they do not break the order flow. The tradeoff is that in-process fire-and-forget is not a delivery guarantee; production would usually use a queue, transactional outbox, or durable notification worker.

### Why is `cb.call(retry(...))` correct, not `retry(lambda: cb.call(...))`?

The circuit breaker should observe the final outcome of one logical operation, not every internal retry attempt.

With `cb.call(retry(_charge))`:

- transient payment failures are retried inside the breaker;
- the breaker only counts a failure if all retries are exhausted;
- an open circuit fast-fails immediately without retrying.

With `retry(lambda: cb.call(_charge))`:

- a `CircuitOpenError` would look like another transient exception;
- the retry loop would keep calling the open circuit;
- the system would retry exactly when the breaker is trying to stop load;
- the breaker's failure threshold would be polluted by fast-fail signals rather than real downstream failures.

This also keeps the amplification factor understandable: one external payment failure can represent up to 3 downstream `/charge` attempts, but it is still one failure from the circuit breaker's perspective.

### Why does the bulkhead need to wrap the circuit breaker?

The bulkhead bounds real in-flight work to a slow dependency. It wraps the circuit breaker so that:

1. One logical `/pay` request holds exactly one bulkhead slot for its entire lifetime, including all internal retries.
2. `BulkheadFullError` is a local resource-exhaustion signal, not a payments-service failure. If the bulkhead were inside the circuit breaker, bulkhead saturation could increment the breaker's failure count and open the circuit even while payments is healthy but busy.
3. The system fast-fails before allowing unbounded concurrent calls to accumulate.

The desired order is therefore:

```text
bulkhead → circuit breaker → retry → payments
```

### Bulkhead vs rate limiter — both reject excess traffic. What is the difference?

The rate limiter limits arrival rate. It answers: "How many requests per second may enter this endpoint?" It protects the service from traffic volume, bursts, and DDoS-like load.

The bulkhead limits concurrent in-flight dependency calls. It answers: "How many calls may currently be waiting on this downstream service?" It protects the gateway's event loop, connection pool, and worker resources from one slow dependency.

A request can be below the rate limit and still be rejected by the bulkhead if previous requests are still in flight. For example, at 10 rps with 3s payment latency, ~30 calls can be in flight; a bulkhead of 10 rejects the excess even though the arrival rate is unchanged.

---

## Final state

All fault injection was restored:

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0 PAYMENT_FAILURE_RATE=0.0
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.0 NOTIFY_LATENCY_MS=0
```

`mixedload` was left running at 2 replicas.
