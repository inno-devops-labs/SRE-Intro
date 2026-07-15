# Lab 11 — Advanced Microservice Patterns

Repository: https://github.com/Ars5njo/SRE-Intro  
Branch: `feature/lab11`  
Date: 2026-07-15  
Timezone: Europe/Moscow (UTC+03:00)

## Environment

- Local k3d cluster: `quickticket`, Kubernetes `v1.35.5+k3s1`.
- Gateway: 5 Argo Rollouts pods for Tasks 1-2; one pod during the per-process
  bulkhead saturation experiment so the documented `MAX=10` cap could bind.
- In-cluster Prometheus: `prom/prometheus:v3.11.2`, 5-second scrape interval.
- Tested local images: `quickticket-gateway:v1`, `quickticket-events:v1`,
  `quickticket-payments:v1`, and `quickticket-notifications:v1`.
- Final state: gateway 5/5 Ready, every application dependency Ready, fault
  injection restored to zero, and `/health` returned:

```json
{"status":"healthy","checks":{"events":"ok","payments":"ok","notifications":"ok","circuit_payments":"CLOSED"}}
```

## Task 1 — Notifications and Retries

### Notifications service

Key parts of `app/notifications/main.py`:

```python
NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

REQUEST_COUNT = Counter(
    "notifications_requests_total", "Total requests", ["method", "path", "status"]
)
REQUEST_DURATION = Histogram(
    "notifications_request_duration_seconds", "Request duration", ["method", "path"]
)
NOTIFY_TOTAL = Counter(
    "notifications_notify_total", "Total notification attempts", ["result"]
)

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

The live `/metrics` endpoint exposed all required metric families:

```text
notifications_requests_total{method="GET",path="/health",status="200"} 56.0
notifications_request_duration_seconds_count{method="GET",path="/health"} 56.0
notifications_notify_total{result="success"} 45.0
notifications_notify_total{result="failed"} 20.0
```

The observed injected failure rate was `20 / (45 + 20) = 30.77%`, close to
the configured 30%.

### Kubernetes manifest

`k8s/notifications.yaml`:

```yaml
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
    - name: http
      port: 8083
      targetPort: 8083
---
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
              name: http
          env:
            - name: NOTIFY_FAILURE_RATE
              value: "0.0"
            - name: NOTIFY_LATENCY_MS
              value: "0"
          readinessProbe:
            httpGet:
              path: /health
              port: http
          livenessProbe:
            httpGet:
              path: /health
              port: http
```

Live proof:

```text
notifications-5bd48c57d4-kjgv2   1/1   Running
```

The gateway manifest sets `NOTIFICATIONS_URL=http://notifications:8083`. The
gateway schedules `_notify_order_confirmed()` with `asyncio.create_task`, so
the notification is outside the user-visible critical path.

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

### Test 1 — Fire-and-forget under notification failure

Fault: `NOTIFY_FAILURE_RATE=0.3`, `NOTIFY_LATENCY_MS=300`.

```text
result: ok=30 fail=0
```

Prometheus p99 during the 2-minute window:

```text
path="/reserve/{id}/pay"  p99=0.0776452463 seconds (77.65 ms)
```

This is below the lab's 100 ms threshold even though the destination slept for
300 ms. Notification failures were not visible to checkout callers.

Notifications should be non-blocking because delivery of email/SMS is a
best-effort side effect, not part of payment authorization or inventory
confirmation. Making it critical would add its latency and availability to
every checkout. For durable production delivery, an outbox/queue should replace
in-memory `create_task`, because a pod restart can lose an unfinished task.

### Test 2 — Retries under transient payment failures

Fault: `PAYMENT_FAILURE_RATE=0.3`.

```text
result: ok=29 fail=1
gateway_retry_total{target="payments",result="retried"} = 10
gateway_retry_total{target="payments",result="succeeded_after_retry"} = 6
gateway_retry_total{target="payments",result="exhausted"} = 1
```

Why `cb.call(retry(_charge))` is correct: the circuit breaker observes the
final outcome of one logical payment request, while transient attempts remain
inside that request. Once the circuit is OPEN, the outer breaker fast-fails
without starting a retry loop. With `retry(cb.call(_charge))`, every retry
re-enters the breaker and can repeatedly encounter/retry a fast-fail signal,
defeating the intended short circuit and distorting failure accounting.

## Task 2 — Circuit Breaker and Rate Limiter

### Implementations

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

### Circuit breaker evidence

At `PAYMENT_FAILURE_RATE=1.0`, across 80 checkout attempts:

```text
500s=25 503s=55 other=0
```

The 500s were retry-exhausted calls before each per-process circuit reached
its threshold. The 503s were fast-fails after circuits opened. After restoring
payments and waiting for the 30-second cooldown:

```text
[1] 200
...
[15] 200
recovery: 200=15 other=0
```

Prometheus aggregated all five gateway processes:

```text
gateway_circuit_breaker_transitions_total{to="OPEN"} = 5
gateway_circuit_breaker_transitions_total{to="HALF_OPEN"} = 5
gateway_circuit_breaker_transitions_total{to="CLOSED"} = 5
```

### Rate limiter evidence

One 100-request burst through five gateway replicas:

```text
burst: 200=46 429=54 other=0
HTTP/1.1 429 Too Many Requests
retry-after: 1
gateway_rate_limit_rejections_total{path="/events"} = 82
```

The counter includes the burst, header warm-up, and background lab traffic.
At a sustained five requests per second, below the configured per-pod limit:

```text
sustained: 200=30 429=0
```

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
        except TimeoutError as exc:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full") from exc

        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self.semaphore.release()

pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(
        lambda: call_with_retry(_charge, target="payments")
    )
)
```

`BulkheadFullError` is mapped to HTTP 503 with a distinct `bulkhead full`
message.

### Isolation evidence

For the exact lab-sized experiment, the gateway was temporarily scaled to one
pod so the per-process `MAX=10` cap could bind. Thirty reservation IDs were
created first, then all 30 payment calls were launched concurrently with
`PAYMENT_LATENCY_MS=3000`:

```text
10 payment calls completed with 200
20 payment calls fast-failed with 503 (bulkhead full)
EVENTS: ok=30 slow=0
gateway_bulkhead_rejections_total{target="payments"} = 20
max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m]) = 10
```

The direct gateway metric agreed:

```text
gateway_bulkhead_in_flight{target="payments"} 0.0
gateway_bulkhead_rejections_total{target="payments"} 20.0
```

### Comparison and an observed lab limitation

With the cap temporarily changed from 10 to 1000 (effectively no bulkhead), the
lab's exact 30-call comparison produced:

```text
WITHOUT BULKHEAD EVENTS: ok=30 slow=0
```

This does **not** match the lab text's prediction of 30 slow requests. The
gateway uses non-blocking `httpx.AsyncClient`; 30 coroutines waiting on 3-second
downstream I/O do not starve the asyncio event loop. The client's default
connection ceiling is also larger than 30. I did not replace this real result
with the expected value.

An additional 120-concurrent test was used to put pressure on the shared client
pool. It showed a measurable difference:

```text
WITHOUT BULKHEAD STRESS EVENTS: ok=28 slow=2
WITH BULKHEAD STRESS EVENTS:    ok=30 slow=0
```

Why bulkhead wraps the breaker/retry chain: admission happens once for a whole
logical payment flow, so every retry stays inside one concurrency slot. If each
retry acquired independently, a single user request could consume several
slots and the cap would no longer represent concurrent payment flows. An OPEN
circuit occupies the outer slot only for its immediate fast-fail; no slow
downstream work is admitted.

Bulkhead versus rate limiter: the rate limiter controls arrival rate over time
(requests per second) and protects an endpoint from bursts or abusive clients.
The bulkhead controls simultaneous in-flight work for one dependency and keeps
a slow dependency from consuming resources needed by unrelated paths. Low-RPS
requests can still need a bulkhead if each request is very slow; a high burst
can need rate limiting even when calls complete quickly.

## PR Checklist

```text
- [x] Task 1 done — notifications service, k8s manifest, fire-and-forget wiring, retry with backoff (Tests #1 + #2)
- [x] Task 2 done — circuit breaker + rate limiter, tested under failure
- [x] Bonus Task done — bulkhead isolation, concurrent /pay vs /events test, cap proven to bind
```

## Acceptance Criteria Checklist

### Task 1 (4 pts)

- [x] `app/notifications/` runs and emits request count, duration, and notify-result metrics.
- [x] `k8s/notifications.yaml` contains Deployment + Service; pod observed 1/1 Ready.
- [x] `/pay` schedules notifications fire-and-forget; injected latency/failures did not affect checkout outcome.
- [x] Retry implements exponential backoff, jitter, transient/non-retryable branches, exhaustion, and metrics.
- [x] Test 1: 30/30 checkout success with 30% notify failures; `/pay` p99 77.65 ms.
- [x] Test 2: 29/30 checkout success with 30% payment failures; retry and recovery counters non-zero.
- [x] CB/retry composition design prompt answered.

### Task 2 (4 pts)

- [x] Circuit breaker implemented and wired into `/pay`.
- [x] OPEN proven under 100% payment failure: 55 fast-fail 503 responses.
- [x] HALF_OPEN → CLOSED recovery proven: 15/15 responses returned 200 after cooldown.
- [x] Sliding-window rate limiter implemented; burst returned 54 HTTP 429 responses.
- [x] `Retry-After: 1` observed and rejection counter non-zero.
- [x] Sustained below-limit traffic returned 30/30 HTTP 200 and zero 429.

### Bonus Task (2 pts)

- [x] `Bulkhead.call` uses a per-target semaphore, acquire timeout, gauge, and rejection counter.
- [x] `/pay` composition is `bulkhead → circuit breaker → retry → call`.
- [x] With bulkhead, `/events` remained fast during slow concurrent payments (`30/30`).
- [x] Rejections reached 20 and observed in-flight occupancy reached exactly MAX=10.
- [x] Exact no-bulkhead comparison and higher-pressure diagnostic comparison recorded honestly.
- [x] Both bulkhead design prompts answered.
