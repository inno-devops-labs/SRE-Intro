# Lab 11 — Advanced Microservice Patterns

**Project:** QuickTicket  
**Branch:** `feature/lab11`  
**Environment:** k3d `quickticket`, Kubernetes, Argo Rollouts (5 Gateway replicas), Prometheus  
**Scope:** Task 1 (notifications and retries), Task 2 (circuit breaker and rate limiter), Bonus (bulkhead isolation)

## 1. Architecture and implementation

Request flow:

```text
Client
  |
  v
Gateway -- rate limiter (per endpoint, per Gateway process)
  |
  +--> Events
  |
  +--> Bulkhead(payments, 10 slots)
  |      +--> Circuit breaker (threshold=5, cooldown=30s)
  |             +--> Retry (max=3, exponential backoff + jitter)
  |                    +--> Payments
  |
  +--> Notifications (async fire-and-forget after confirmed payment)
```

### 1.1. Notifications service

Added `app/notifications/` with `main.py`, `Dockerfile`, and `requirements.txt`.

- `POST /notify`: accepts an `order_confirmed` event with an `order_id`, logs the result, and returns `{"status":"sent", ...}` when successful.
- `GET /health`: reports `failure_rate` and `latency_ms`.
- `GET /metrics`: exposes the Prometheus exposition endpoint.
- Fault injection uses `NOTIFY_FAILURE_RATE` and `NOTIFY_LATENCY_MS`.

Key application code:

```python
NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

REQUEST_COUNT = Counter(
    "notifications_requests_total", "Total HTTP requests",
    ["method", "path", "status"],
)
REQUEST_DURATION = Histogram(
    "notifications_request_duration_seconds", "HTTP request duration",
    ["method", "path"],
)
NOTIFY_TOTAL = Counter(
    "notifications_notify_total", "Notification attempts", ["result"],
)

@app.post("/notify")
def notify(body: dict):
    event = body.get("event")
    order_id = body.get("order_id")
    if event != "order_confirmed" or not order_id:
        raise HTTPException(422, "Expected event=order_confirmed and order_id")
    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning(f"Notification failed for order={order_id}")
        raise HTTPException(500, "Notification delivery failed")
    NOTIFY_TOTAL.labels("success").inc()
    log.info(f"Notification sent: event={event} order={order_id}")
    return {"status": "sent", "event": event, "order_id": order_id}
```

The HTTP metrics middleware records `notifications_requests_total{method,path,status}` and `notifications_request_duration_seconds{method,path}` for requests except `/metrics`.

`app/notifications/requirements.txt`:

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

`app/notifications/Dockerfile` uses `python:3.13-slim`, installs those requirements, runs as an unprivileged `app` user, exposes port **8083**, and starts `uvicorn main:app --host 0.0.0.0 --port 8083`.

### 1.2. Kubernetes manifest

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
  type: ClusterIP
  selector:
    app: notifications
  ports:
    - port: 8083
      targetPort: 8083
```

The service was built and imported using `docker build -t quickticket-notifications:v1 ./app/notifications` and `k3d image import -c quickticket quickticket-notifications:v1`. Kubernetes reported **1/1 Running**.

Direct endpoint validation:

```text
GET /health
{"status":"healthy","failure_rate":0.0,"latency_ms":0}

POST /notify {"event":"order_confirmed","order_id":"TEST-001"}
{"status":"sent","event":"order_confirmed","order_id":"TEST-001"}

notifications_requests_total{method="GET",path="/health",status="200"} 1.0
notifications_requests_total{method="POST",path="/notify",status="200"} 1.0
notifications_request_duration_seconds_count{method="POST",path="/notify"} 1.0
notifications_notify_total{result="success"} 1.0
```

The Gateway Rollout sets `NOTIFICATIONS_URL=http://notifications:8083`. Its `/health` response included `"notifications":"ok"`, alongside `"events":"ok"` and `"payments":"ok"`.

### 1.3. Retry with exponential backoff and jitter

Implemented `call_with_retry()` in `app/gateway/main.py`:

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
            retryable = (
                isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))
                or (
                    isinstance(exc, httpx.HTTPStatusError)
                    and (exc.response.status_code >= 500
                         or exc.response.status_code in (408, 429))
                )
            )
            if not retryable:
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                raise
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
```

Configuration: `RETRY_MAX=3`, `RETRY_BASE_DELAY_MS=100`. Network timeouts/connect failures, HTTP 5xx, 408, and 429 are retryable; other 4xx are not. Jitter reduces synchronized retry storms.

### 1.4. Fire-and-forget integration

After successful payment and reservation confirmation, Gateway schedules the notification without awaiting its completion:

```python
asyncio.create_task(_notify_order_confirmed(reservation_id))
return result
```

The helper issues `POST /notify` using `httpx` and handles transport exceptions without propagating them back to the checkout request. Notifications do not determine the critical `/health` verdict.

## 2. Task 1 test evidence — Notifications and retries (4 pts)

### 2.1. Test #1: nonblocking notifications under injected failure

Injected:

```bash
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
```

Ran 30 reservation + checkout chains:

```text
result: ok=30 fail=0
notifications_notify_total{result="success"} 23.0
notifications_notify_total{result="failed"} 7.0
```

Every user checkout completed even though **7/30 notifications failed**. The observed failure proportion was **23.3%** (random fault injection, so it need not equal 30% exactly).

Measured `/reserve/{id}/pay` p99 using `histogram_quantile` over a 2-minute rate window:

| Condition | p99 |
|---|---:|
| Notifications: 30% failure, 300 ms latency | **237.87 ms** |
| Notifications: 0% failure, 0 ms latency (new 30-checkout baseline) | **205.00 ms** |
| Difference between observed windows | **32.87 ms** |

**Limitation:** The lab's requested p99 threshold of **<100 ms was not met**. These measurements used separate traffic windows and therefore do not prove an exact causal effect. They are consistent with fire-and-forget not adding the full 300 ms notification delay to `/pay`. The successful 30/30 checkouts and failed-notification counter provide direct evidence of error isolation.

Restored `NOTIFY_FAILURE_RATE=0.0` and `NOTIFY_LATENCY_MS=0`.

### 2.2. Test #2: retries under transient payment failures

Injected:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
```

Observed for 30 checkouts:

```text
[2] pay failed: 500
result: ok=29 fail=1
```

Prometheus `sum by (target,result) (gateway_retry_total)`:

```text
gateway_retry_total{target="payments",result="retried"} 15
gateway_retry_total{target="payments",result="succeeded_after_retry"} 10
gateway_retry_total{target="payments",result="exhausted"} 1
```

**Interpretation:** 10 calls recovered after retries; one exhausted all permitted attempts. Both required retry counters were nonzero. Payments was restored to `PAYMENT_FAILURE_RATE=0.0` afterward.

**Why notifications must be nonblocking:** Notification delivery is a secondary side effect, not a prerequisite for a confirmed payment. Waiting for it would add downstream latency and allow email/push outages to fail otherwise-successful checkouts. Fire-and-forget isolates this dependency; in production a durable queue or outbox would also be needed to guarantee eventual delivery if the process crashes.

**Why `cb.call(retry(...))`, not `retry(cb.call(...))`:** A circuit breaker should account for the final outcome of a logical payment operation after its permitted internal retries. The reverse causes individual retry attempts to count separately toward opening the circuit and can interfere with the breaker’s fast-fail behavior. In our implementation `CircuitOpenError` is not retryable, but the reversed ordering would still misrepresent failures and could open the circuit prematurely.

## 3. Task 2 — Circuit breaker and rate limiter (4 pts)

### 3.1. Circuit breaker implementation

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

Configured `CB_FAILURE_THRESHOLD=5` and `CB_COOLDOWN_S=30`. Each of the five Gateway pods maintains an independent breaker.

Injected `PAYMENT_FAILURE_RATE=1.0` and ran 80 checkout attempts:

```text
500s=25 503s=55
```

Prometheus reported:

```text
gateway_circuit_breaker_transitions_total{to="OPEN"} 5
```

After restoring `PAYMENT_FAILURE_RATE=0.0` and allowing cooldown, all 15 checkouts returned HTTP 200. State transitions became:

```text
gateway_circuit_breaker_transitions_total{to="OPEN"} 5
gateway_circuit_breaker_transitions_total{to="HALF_OPEN"} 5
gateway_circuit_breaker_transitions_total{to="CLOSED"} 5
```

Gateway health subsequently showed `"circuit_payments":"CLOSED"`. This demonstrates `CLOSED → OPEN → HALF_OPEN → CLOSED` on all five replicas.

### 3.2. Rate limiter implementation

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

Default limit: `RATE_LIMIT_RPS=10` for each normalized endpoint **per Gateway process**, over a one-second sliding window. Rejected requests get HTTP 429, `Retry-After: 1`, and an increment of `gateway_rate_limit_rejections_total{path}`.

Test results:

```text
Sequential 100-request burst: 200=92 429=8
Normal paced load:          200=30 429=0
```

Prometheus after the burst:

```text
gateway_rate_limit_rejections_total{path="/events"} 10
```

Direct test on one Gateway pod, with 30 parallel `/events` requests:

```text
Status codes: {200: 10, 429: 20}
HTTP: 429
Retry-After: 1
```

Repeating the direct test yielded the same status split and header. The original sequential burst did not saturate each pod consistently because traffic was spread over five separate limiters and multiple sliding windows. Direct parallel testing proved the 10 RPS per-pod limit.

## 4. Bonus Task — Bulkhead isolation (2 pts)

### 4.1. Implementation and composition

Implemented a per-Payments semaphore with maximum concurrency **10** and acquisition timeout **0.5 s**, plus Prometheus Gauge and Counter:

```python
BULKHEAD_IN_FLIGHT = Gauge(
    "gateway_bulkhead_in_flight", "Current requests inside the bulkhead", ["target"]
)
BULKHEAD_REJECTIONS = Counter(
    "gateway_bulkhead_rejections_total", "Requests rejected by the bulkhead", ["target"]
)

class BulkheadFullError(Exception):
    pass

class Bulkhead:
    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.acquire_timeout_s = acquire_timeout_s

    async def call(self, func):
        try:
            await asyncio.wait_for(
                self.semaphore.acquire(), timeout=self.acquire_timeout_s
            )
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(target=self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full")

        BULKHEAD_IN_FLIGHT.labels(target=self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(target=self.name).dec()
            self.semaphore.release()
```

Wiring in `pay_reservation()`:

```python
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(
        lambda: call_with_retry(_charge, target="payments")
    )
)
```

`BulkheadFullError` is mapped to HTTP 503. For the test, `RATE_LIMIT_RPS` was temporarily increased to `100` so HTTP 429 would not prevent load from reaching the Bulkhead. It was restored to `10` afterward.

### 4.2. Failure injection and measurement

Injected `PAYMENT_LATENCY_MS=3000`, `PAYMENT_FAILURE_RATE=0.0`; created 30 reservations and launched 30 concurrent checkouts against the same Gateway pod.

**With Bulkhead:**

```text
Reservations created: 30
PAYMENTS: {'200': 10, '503': 20}
EVENTS max latency: 808.24 ms
EVENTS under 500ms: 28 /30
BULKHEAD peak in-flight: 10.0
gateway_bulkhead_rejections_total{target="payments"} 20.0
```

Prometheus evidence:

```text
sum by (target) (gateway_bulkhead_rejections_total)
  target="payments" => 20

max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m])
  pod="gateway-5b57985675-82zjl", target="payments" => 10
```

**Control: without Bulkhead.** A `quickticket-gateway:v5-control` image was built with only the `payments_bulkhead.call(...)` wrapper removed. A separate temporary `gateway-control` pod was tested with 30 reservations, 30 concurrent payments, `PAYMENT_LATENCY_MS=3000`, and `RATE_LIMIT_RPS=100`:

```text
Reservations created: 30
PAYMENTS: {'200': 18, '500': 12}
EVENTS: ok= 30 slow= 0
EVENTS max latency: 119.03 ms
```

Comparison:

| Measurement | Bulkhead enabled | Bulkhead disabled |
|---|---:|---:|
| Payment HTTP 200 | 10 | 18 |
| Payment HTTP 503 | 20 | 0 |
| Payment HTTP 500 | 0 | 12 |
| `/events` below 500 ms | 28/30 | 30/30 |
| `/events` maximum observed latency | 808.24 ms | 119.03 ms |
| Peak payment bulkhead occupancy | 10 | Not applicable |

**Interpretation and limitation:** The experiment conclusively demonstrated a concurrency cap and fast rejection of excess calls, backed by Prometheus. It **did not show lower `/events` latency with Bulkhead**: `/events` was actually faster in this control run. An asynchronous Gateway can keep serving independent routes while many payment coroutines await I/O; the lab’s suggested slow-`/events` outcome without isolation did not occur here. The 12 HTTP 500 responses in the control run were observed but not root-caused. Thus the latency-isolation comparison is inconclusive, even though concurrency limiting is verified. HTTP 503 is the expected explicit overload response from the Bulkhead.

### 4.3. Design answers

**Why does Bulkhead wrap Circuit Breaker?** The semaphore is the outer admission gate for a logical payment operation, including its internal retry sequence. This ensures every admitted payment consumes at most one slot throughout the whole call and the concurrency bound is enforced consistently. If the circuit is OPEN, the nested breaker rejects quickly and the outer `finally` releases the slot. This ordering is the one required by the lab; a different ordering would alter which requests are counted as occupying the pool.

**Bulkhead versus rate limiter:** A rate limiter caps *arrivals over time* (e.g., 10 requests per second to `/events`), protecting against sustained request-rate spikes. A Bulkhead caps *simultaneously active operations* (e.g., 10 outstanding Payments calls), protecting resources and isolating a slow dependency. Both here use per-process state; a cluster-wide guarantee would require shared coordination or upstream enforcement.

## 5. Final deployment state

After all tests:

- Payments settings restored to `PAYMENT_LATENCY_MS=0`, `PAYMENT_FAILURE_RATE=0.0`.
- Notifications settings restored to `NOTIFY_LATENCY_MS=0`, `NOTIFY_FAILURE_RATE=0.0`.
- Temporary `gateway-control` pod deleted; `mixedload` scaled to zero for these isolation tests.
- Gateway restored to `quickticket-gateway:v4` with Bulkhead **enabled** and `RATE_LIMIT_RPS=10`.
- Argo Rollouts reported `Healthy`, stable image `quickticket-gateway:v4`, **5/5 Ready and Available**; latest AnalysisRun successful.

## 6. Summary

| Component | Evidence | Result |
|---|---|---|
| Notifications | Health, API, three metric families, 1/1 pod | Functional |
| Fire-and-forget | 30/30 checkout successes despite 7 notification failures | Functional; p99 target not met |
| Retry | 29/30 successes; 15 retry events, 10 recovered, 1 exhausted | Verified |
| Circuit breaker | 25 HTTP 500, 55 HTTP 503; five OPEN/HALF_OPEN/CLOSED transitions | Verified |
| Rate limiter | Parallel 10 HTTP 200 / 20 HTTP 429; Retry-After: 1 | Verified |
| Bulkhead | 10 slots occupied, 20 rejected; Prometheus confirmed cap | Verified |
| Bulkhead impact on `/events` | Control comparison showed no latency improvement | Not demonstrated |

**Files for submission:** `app/notifications/`, `app/gateway/main.py`, `k8s/notifications.yaml`, `k8s/gateway.yaml`, and this report (`submissions/lab11.md`).
