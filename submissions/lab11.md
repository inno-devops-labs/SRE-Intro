# Lab 11

## Task 1

### `app/notifications/main.py` (key parts)

```python
NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

REQUEST_COUNT = Counter(
    "notifications_requests_total", "Total requests", ["method", "path", "status"]
)
REQUEST_DURATION = Histogram(
    "notifications_request_duration_seconds", "Request duration", ["method", "path"]
)
NOTIFY_TOTAL = Counter("notifications_notify_total", "Total notification attempts", ["result"])

@app.get("/health")
def health():
    return {"status": "healthy", "failure_rate": NOTIFY_FAILURE_RATE, "latency_ms": NOTIFY_LATENCY_MS}

@app.post("/notify")
def notify(body: dict = None):
    payload = body or {}
    event = payload.get("event", "unknown")
    order_id = payload.get("order_id", "unknown")

    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)

    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        raise HTTPException(500, "Notification delivery failed")

    NOTIFY_TOTAL.labels("success").inc()
    return JSONResponse(
        status_code=200,
        content={"status": "sent", "event": event, "order_id": order_id},
    )
```

### `app/notifications/requirements.txt`

```txt
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

### `call_with_retry()`

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
            retryable = False
            if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
                retryable = True
            elif isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                retryable = status >= 500 or status in (408, 429)
                if not retryable:
                    RETRY_TOTAL.labels(target, "non_retryable").inc()
                    raise

            if not retryable:
                raise

            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise

            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
```

### Test 1: notify failure is non-blocking

I set:

```bash
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
kubectl rollout status deployment/notifications --timeout=60s
```

Then I ran 30 checkout chains through the updated gateway canary pod:

```txt
result: ok=30 fail=0
```

Gateway p99 for `/reserve/{id}/pay` stayed low during this test:

```txt
path="/reserve/{id}/pay", pod="gateway-65dbfd974c-29s7s" -> 0.0248s
```

I also re-ran a short 12-request sample under the same fault injection and read the metrics from the notifications pod:

```txt
notifications_notify_total{result="failed"} 4.0
notifications_notify_total{result="success"} 8.0
```

This showed that notifications really failed in the background, but user-facing `/pay` still returned `200`.

### Test 2: retries under transient payment failure

I set:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=0
kubectl rollout status deployment/payments --timeout=60s
```

Then I ran 30 checkout chains again:

```txt
result: ok=30 fail=0
```

Prometheus showed that retries really fired:

```txt
gateway_retry_total{result="retried",target="payments"} 10
gateway_retry_total{result="succeeded_after_retry",target="payments"} 10
```

### Why notifications should be fire-and-forget

Notifications are non-critical. The user only cares that payment and reservation confirmation succeed. If notifications are slow or broken, I should not make the checkout path slower or fail the whole request because of that.

### Why `cb.call(retry(...))` is correct

With `cb.call(retry(...))`, the circuit breaker sees one logical payments operation and only sees the final result after retries. If I do `retry(lambda: cb.call(...))`, then retry will keep calling the circuit breaker itself, including after it is already open, so the fast-fail behavior is defeated.

## Task 2

### `CircuitBreaker.call()`

```python
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
```

### `RateLimiter.allow()`

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

### Circuit breaker open test

I set:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0 PAYMENT_LATENCY_MS=0
kubectl rollout status deployment/payments --timeout=60s
```

Then I reserved tickets directly in `events` and sent only `/pay` through the updated gateway pod, so the result was not mixed with rate limiting on reserve:

```txt
500s=5 503s=75
```

This is what I expected: first a few requests exhausted retries and returned `500`, then the circuit opened and most requests fast-failed with `503`.

Prometheus after that:

```txt
gateway_circuit_breaker_transitions_total{to="OPEN"} 1
```

### Circuit breaker recovery test

After restoring payments and waiting for the cooldown:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0
sleep 36
```

The next requests were healthy again:

```txt
[2] 200
[3] 200
[4] 200
[5] 200
[6] 200
[7] 200
[8] 200
[9] 200
[10] 200
[11] 200
[12] 200
[13] 200
[14] 200
[15] 200
```

Prometheus then showed the full transition path:

```txt
gateway_circuit_breaker_transitions_total{to="OPEN"} 1
gateway_circuit_breaker_transitions_total{to="HALF_OPEN"} 1
gateway_circuit_breaker_transitions_total{to="CLOSED"} 1
```

### Rate limiter test

I sent 100 rapid `GET /events` requests to the updated gateway canary pod:

```txt
200=10 429=90
```

The `429` response had the expected header:

```txt
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

Prometheus showed the rejection counters:

```txt
gateway_rate_limit_rejections_total{path="/events"} 90
gateway_rate_limit_rejections_total{path="/events/{id}/reserve"} 20
```

The `/events/{id}/reserve` rejections came from an earlier burst while I was still tuning the test pace. The dedicated rate-limit test for `/events` was the `200=10 429=90` run above.
