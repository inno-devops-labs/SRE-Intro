# Lab 11 — Advanced Microservice Patterns

Notifications service + gateway retry / circuit breaker / rate limiter / bulkhead. Tested on the k3d `quickticket` cluster with Lab 8 mixedload running.

---

## Task 1 — Notifications + retries (4 pts)

### 1. `app/notifications/main.py` (key bits) and `requirements.txt`

```python
NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

REQUEST_COUNT = Counter("notifications_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("notifications_request_duration_seconds", "Request duration", ["method", "path"])
NOTIFY_TOTAL = Counter("notifications_notify_total", "Total notify attempts", ["result"])

@app.get("/health")
def health():
    return {"status": "healthy", "failure_rate": NOTIFY_FAILURE_RATE, "latency_ms": NOTIFY_LATENCY_MS}

@app.post("/notify")
def notify(body: dict = None):
    body = body or {}
    event = body.get("event", "unknown")
    order_id = body.get("order_id", "unknown")
    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        raise HTTPException(500, "Notification delivery failed")
    NOTIFY_TOTAL.labels("success").inc()
    return {"status": "sent", "event": event, "order_id": order_id}
```

`requirements.txt`:

```
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

Dockerfile exposes **8083**. Full source is in `app/notifications/`.

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

Gateway `k8s/gateway.yaml` env:

```yaml
- name: NOTIFICATIONS_URL
  value: "http://notifications:8083"
```

Pod was `1/1 Ready`. `/health` still only requires events + payments for `critical_ok`.

### 3. `call_with_retry()`

```python
def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status >= 500 or status in (408, 429)
    return False

async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    base_delay = RETRY_BASE_DELAY_MS / 1000.0
    last_exc = None
    for attempt in range(max_retries):
        try:
            result = await func()
            if attempt > 0:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result
        except Exception as e:
            last_exc = e
            if not _is_retryable(e):
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                raise
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
    raise last_exc
```

### 4. Test #1 — fire-and-forget under notify failure

```
NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
checkout-burst result: ok=30 fail=0
```

`/pay` p99 during the injection (not inflated by the 300ms notify sleep):

```
histogram_quantile(0.99, sum by (le,path) (rate(gateway_request_duration_seconds_bucket[2m])))
```

```json
{"status":"success","data":{"resultType":"vector","result":[
  {"metric":{"path":"/reserve/{id}/pay"},"value":[1760822144.11,"0.0418"]},
  {"metric":{"path":"/events/{id}/reserve"},"value":[1760822144.11,"0.0286"]},
  {"metric":{"path":"/events"},"value":[1760822144.11,"0.0124"]},
  {"metric":{"path":"/health"},"value":[1760822144.11,"0.0197"]}
]}}
```

`/pay` p99 = **41.8ms** (≪ 300ms). Notify latency is off the user path.

### 5. Test #2 — retries under transient payment failure

```
PAYMENT_FAILURE_RATE=0.3
retry-test result: ok=29 fail=1
```

`0.3³ ≈ 2.7%` all-attempts-fail matches the single failure. Prometheus:

```
sum by (target,result) (gateway_retry_total)
```

```json
{"status":"success","data":{"resultType":"vector","result":[
  {"metric":{"target":"payments","result":"retried"},"value":[1760822289.40,"47"]},
  {"metric":{"target":"payments","result":"succeeded_after_retry"},"value":[1760822289.40,"18"]},
  {"metric":{"target":"payments","result":"exhausted"},"value":[1760822289.40,"2"]},
  {"metric":{"target":"payments","result":"non_retryable"},"value":[1760822289.40,"0"]}
]}}
```

Both `retried` and `succeeded_after_retry` are non-zero — retries actually fired.

### 6. Real notify failure rate

From the notifications pod `/metrics` after Test #1 (burst + mixedload):

```
notifications_notify_total{result="failed"} 11
notifications_notify_total{result="success"} 24
```

Injected rate 0.3; observed `11/(11+24) = 31.4%`. User checkouts still `ok=30` because notify is fire-and-forget.

### 7. Why should notifications be non-blocking (fire-and-forget)?

By the time `/pay` notifies, money is charged and the reservation is confirmed. Email/push is best-effort. Awaiting `/notify` would put 300ms (or a timeout) onto `/pay` p99, and a dead notifications pod would turn successful checkouts into 5xx. Fire-and-forget (`asyncio.create_task`) keeps the critical path to events + payments. `/health` reports notifications but does not gate `critical_ok` on it.

### 8. Why `cb.call(retry(...))` and not `retry(lambda: cb.call(...))`?

The breaker should count **one user-visible attempt**, after retries have already absorbed transients. `cb.call(retry(_charge))` trips OPEN only when the retry budget is exhausted.

The reverse, `retry(cb.call(_charge))`, retries past `CircuitOpenError`: clients sleep through backoff and keep hitting a tripped breaker, which is the opposite of fast-fail. HALF_OPEN would also get a burst of probes instead of one trial. One CB “failure” can be up to `RETRY_MAX` downstream calls; that is intended — the breaker tracks outcomes the user would have seen, not every 500 that retry already hid.

---

## Task 2 — Circuit breaker + rate limiter (4 pts)

### CircuitBreaker and RateLimiter

```python
class CircuitBreaker:
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

### CB OPEN under 100% payment failure

```
PAYMENT_FAILURE_RATE=1.0
cb-probe (80 checkouts): 500s=27 503s=51
```

First hits per pod exhaust retries → **500**. After each of 5 in-process breakers hits threshold 5 → **503** fast-fail. ~80 requests needed because the breaker is per-pod, not cluster-wide.

### CB CLOSE after recovery

```
PAYMENT_FAILURE_RATE=0.0
sleep 35  # cooldown 30s
cb-probe2:
[1] 200
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

```
sum by (to) (gateway_circuit_breaker_transitions_total)
```

```json
{"status":"success","data":{"resultType":"vector","result":[
  {"metric":{"to":"OPEN"},"value":[1760822510.02,"5"]},
  {"metric":{"to":"HALF_OPEN"},"value":[1760822510.02,"5"]},
  {"metric":{"to":"CLOSED"},"value":[1760822510.02,"5"]}
]}}
```

Five OPEN (one per gateway replica), then HALF_OPEN → CLOSED after cooldown.

### Rate-limit burst

```
rl-burst (100 rapid GET /events): 200=47 429=53
```

Cluster ceiling ≈ 5 pods × 10 RPS. Header after warm-up:

```
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

```
sum by (path) (gateway_rate_limit_rejections_total)
```

```json
{"status":"success","data":{"resultType":"vector","result":[
  {"metric":{"path":"/events"},"value":[1760822598.77,"53"]},
  {"metric":{"path":"/events/{id}/reserve"},"value":[1760822598.77,"4"]},
  {"metric":{"path":"/reserve/{id}/pay"},"value":[1760822598.77,"2"]}
]}}
```

Sustained `for i in 1..30; curl /events; sleep 0.2` → **zero 429s**.

---

## Bonus — Bulkhead isolation (2 pts)

### `Bulkhead.call` + wrap in `pay_reservation`

```python
class Bulkhead:
    async def call(self, func):
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self.acquire_timeout_s)
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full")
        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self._sem.release()

pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
)
```

`BulkheadFullError` → HTTP 503, same as `CircuitOpenError`.

Composition: **bulkhead → CB → retry → call**.

### Concurrent `/pay` vs `/events` (`PAYMENT_LATENCY_MS=3000`)

**With bulkhead (MAX=10, acquire timeout 0.5s):**

```
EVENTS: ok=29 slow=1
```

Most `/pay` beyond the cap returned 503 within ~500ms. `/events` stayed near baseline (one sample just over 0.5s).

**Without bulkhead** (temporarily unwrapped `payments_cb.call(retry)` only):

```
EVENTS: ok=2 slow=28
```

Slow `/pay` tasks occupied the event loop; `/events` p99 climbed into seconds.

### Prometheus

```
sum by (target) (gateway_bulkhead_rejections_total)
```

```json
{"status":"success","data":{"resultType":"vector","result":[
  {"metric":{"target":"payments"},"value":[1760822788.15,"18"]}
]}}
```

```
max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m])
```

```json
{"status":"success","data":{"resultType":"vector","result":[
  {"metric":{"target":"payments"},"value":[1760822791.03,"10"]}
]}}
```

Cap binds at **MAX=10**. Restored `PAYMENT_LATENCY_MS=0`.

### Why does the bulkhead wrap the circuit breaker, not the other way around?

Retries must share **one** slot. If retry wrapped the bulkhead, three attempts could take three semaphore permits and a cap of 10 would secretly allow 30 downstream calls.

Bulkhead **outside** gates entry to the whole CB+retry sequence: one `/pay` = one occupant for the slow work. OPEN fast-fail still takes a slot, but only for microseconds. The thing that actually drowns the event loop is a 3s payments call, not a 503. CB outside bulkhead would also serialize HALF_OPEN probes behind a full semaphore.

### Bulkhead vs rate limiter — both reject excess. What is different?

- **Rate limiter:** incoming surface. Caps requests per path per second so a client burst cannot overwhelm this process. Cluster ceiling = RPS × replicas unless the limiter lives at ingress.
- **Bulkhead:** dependency isolation. Caps concurrent work toward one downstream so payments slowness cannot starve `/events` and `/health` on the same event loop.

Rate limiter is “too many clients.” Bulkhead is “one compartment is flooding.”

---

## Checklist

- [x] Task 1 — notifications service, k8s manifest, fire-and-forget, retry with backoff (Tests #1 + #2)
- [x] Task 2 — circuit breaker + rate limiter, tested under failure
- [x] Bonus Task — bulkhead isolation, concurrent `/pay` vs `/events`, cap proven to bind
