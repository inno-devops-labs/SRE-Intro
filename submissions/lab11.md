# Lab 11

I added the fourth service, wired the gateway resilience patterns, and then verified each pattern with real fault injection on the `k3d` cluster. As in the recent submissions in this repository, the focus below is on the required code fragments and the concrete evidence.

## Task 1

The new notifications service follows the payments template and adds the required fault injection knobs and Prometheus metrics:

```python
NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

REQUEST_COUNT = Counter("notifications_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("notifications_request_duration_seconds", "Request duration", ["method", "path"])
NOTIFY_TOTAL = Counter("notifications_notify_total", "Total notify attempts", ["result"])


@app.get("/health")
def health():
    return {"status": "healthy", "failure_rate": NOTIFY_FAILURE_RATE, "latency_ms": NOTIFY_LATENCY_MS}


@app.get("/metrics")
def metrics():
    from starlette.responses import Response
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


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
    return {"status": "sent", "event": event, "order_id": order_id}
```

`app/notifications/requirements.txt`:

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

`k8s/notifications.yaml`:

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
  selector:
    app: notifications
  ports:
    - port: 8083
      targetPort: 8083
```

The retry body in `app/gateway/main.py` uses exponential backoff with jitter and records the required Prometheus counters:

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    base_delay = RETRY_BASE_DELAY_MS / 1000

    for attempt in range(max_retries):
        try:
            result = await func()
            if attempt > 0:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            retryable = status >= 500 or status in (408, 429)
            if not retryable:
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                raise
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            RETRY_TOTAL.labels(target, "retried").inc()
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            await asyncio.sleep(delay)
        except (httpx.TimeoutException, httpx.ConnectError):
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            RETRY_TOTAL.labels(target, "retried").inc()
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            await asyncio.sleep(delay)
```

For the fire-and-forget check, I injected `NOTIFY_FAILURE_RATE=0.3` and `NOTIFY_LATENCY_MS=300`. The checkout flow still completed successfully:

```text
result: ok=30 fail=0
```

The `/pay` p99 stayed low even during notification failures, which shows that notifications were not blocking the user path:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784307508.795,"0.03379999999999993"]}]}}
```

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784307527.646,"0.06224999999999986"]}]}}
```

The notifications pod also recorded real injected failures in its own metrics:

```text
notifications_notify_total{result="failed"} 53.0
notifications_notify_total{result="success"} 85.0
```

Notifications should be fire-and-forget because they are a best-effort side effect, not part of the checkout critical path. A slow or failing notification service should not delay or fail a successful payment and confirmation flow.

For retry verification, I injected `PAYMENT_FAILURE_RATE=0.3`. The checkout burst still mostly succeeded:

```text
result: ok=29 fail=1
```

Prometheus shows that retries actually fired and that some calls succeeded after retry:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"result":"retried","target":"payments"},"value":[1784307657.239,"62"]},{"metric":{"result":"succeeded_after_retry","target":"payments"},"value":[1784307657.239,"38"]},{"metric":{"result":"exhausted","target":"payments"},"value":[1784307657.239,"6"]}]}}
```

`cb.call(retry(...))` is the correct composition because the circuit breaker should observe one logical payment attempt and its final outcome after internal retries. The reverse composition would turn `CircuitOpenError` into retryable application work and would undermine the fast-fail behavior.

## Task 2

The circuit breaker body in `app/gateway/main.py` is:

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

The rate limiter body is:

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

Under `PAYMENT_FAILURE_RATE=1.0`, the circuit breaker test produced both retry-exhausted `500` responses and fast-fail `503` responses after the circuit opened:

```text
500s=25 503s=55
```

After recovery and the cooldown wait, successful `200` responses returned again:

```text
[11] 200
[12] 200
[13] 200
[14] 200
[15] 200
```

Prometheus transitions confirm the breaker opened and then later went through `HALF_OPEN` back to `CLOSED`:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"to":"OPEN"},"value":[1784307740.496,"5"]}]}}
```

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"to":"OPEN"},"value":[1784307802.732,"5"]},{"metric":{"to":"HALF_OPEN"},"value":[1784307802.732,"2"]},{"metric":{"to":"CLOSED"},"value":[1784307802.732,"2"]}]}}
```

For the rate limiter, a rapid burst produced the expected mix of success and rejection:

```text
200=63 429=37
```

The `429` response included the required backoff hint:

```text
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

The Prometheus rejection counter for `/events` increased:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/events"},"value":[1784307855.986,"59"]}]}}
```

Below the limit, the same path stayed clean:

```text
200=30 429=0
```

## Bonus Task

For the bonus, I added a payments bulkhead and verified it on one stable gateway pod through `port-forward`, because the cap is per-process and has to be measured on a single pod.

The bulkhead implementation is:

```python
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
```

And the `/pay` path now wraps payments like this:

```python
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
)
```

With `BULKHEAD_PAYMENTS_MAX=10` and `PAYMENT_LATENCY_MS=3000`, the slow payments load was cut off quickly and `/events` mostly stayed fast:

```text
PAY: 200=10 503=110 other=0
EVENTS: ok=29 slow=1
```

With the same test but an effectively disabled cap (`BULKHEAD_PAYMENTS_MAX=1000`), `/events` degraded more and no fast-fail `503` responses appeared:

```text
PAY: 200=89 503=0 other=31
EVENTS: ok=24 slow=6
```

Prometheus on the tested pod shows both slot pressure and a hard cap at `10`:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"instance":"10.42.0.49:8080","job":"gateway","pod":"gateway-7485cb9c88-rtldf","rs_hash":"7485cb9c88","target":"payments"},"value":[1784310462.213,"110"]}]}}
```

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"instance":"10.42.0.49:8080","job":"gateway","pod":"gateway-7485cb9c88-rtldf","rs_hash":"7485cb9c88","target":"payments"},"value":[1784310462.212,"10"]}]}}
```

The bulkhead wraps the circuit breaker because one logical `/pay` attempt, including its retries, should occupy at most one concurrency slot. If the bulkhead were placed inside, retries could contend for slots independently and the bound would stop reflecting real payment concurrency.

The rate limiter and the bulkhead both reject excess work, but they protect different things. The rate limiter protects the gateway entrypoint from too much request volume on a path, while the bulkhead isolates one slow downstream dependency so it cannot drown unrelated traffic.
