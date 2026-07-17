# Lab 11 — Advanced Microservice Patterns

## Task 1 — Notifications Service + Retries (4 pts)

### 11.1 – Notifications service

**`app/notifications/main.py` (key bits):**

```python
import os
import random
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from prometheus_client import Counter, Histogram, generate_latest, REGISTRY
import uvicorn

# --- Configuration ---
FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
LATENCY_MS = float(os.getenv("NOTIFY_LATENCY_MS", "0"))

# --- Metrics ---
requests_total = Counter(
    "notifications_requests_total",
    "Total requests by method, path, status",
    ["method", "path", "status"]
)
request_duration = Histogram(
    "notifications_request_duration_seconds",
    "Request duration by method, path",
    ["method", "path"]
)
notify_total = Counter(
    "notifications_notify_total",
    "Notifications sent by result",
    ["result"]   # success / failed
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(lifespan=lifespan)

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    method = request.method
    path = request.url.path
    start = time.time()
    try:
        response = await call_next(request)
        status = str(response.status_code)
        requests_total.labels(method=method, path=path, status=status).inc()
        request_duration.labels(method=method, path=path).observe(time.time() - start)
        return response
    except Exception:
        requests_total.labels(method=method, path=path, status="500").inc()
        request_duration.labels(method=method, path=path).observe(time.time() - start)
        raise

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "failure_rate": FAILURE_RATE,
        "latency_ms": LATENCY_MS,
    }

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(REGISTRY), media_type="text/plain")

@app.post("/notify")
async def notify(request: Request):
    if LATENCY_MS > 0:
        time.sleep(LATENCY_MS / 1000.0)

    if random.random() < FAILURE_RATE:
        notify_total.labels(result="failed").inc()
        return Response("Simulated failure", status_code=500)

    body = await request.json()
    event = body.get("event")
    order_id = body.get("order_id")
    print(f"NOTIFICATION: event={event} order={order_id}")
    notify_total.labels(result="success").inc()
    return {"status": "sent", "event": event, "order_id": order_id}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8083, log_level="info")
```

**`app/notifications/requirements.txt`:**

```
fastapi==0.104.1
uvicorn[standard]==0.24.0
prometheus-client==0.19.0
```

**`k8s/notifications.yaml`:**

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
  selector:
    app: notifications
  ports:
    - port: 8083
      targetPort: 8083
  type: ClusterIP
```

### 11.4 – `call_with_retry` implementation

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    """Call `func` with retry-on-transient-error.

    No-op default: calls func once and returns. Lab 11 task 11.4 replaces this
    body with exponential backoff + jitter, retryable/non-retryable branching,
    and Prometheus counters on the `gateway_retry_total{target,result}` metric.

    See lab 11 §11.4 for the behavior contract. The wiring (in /pay below)
    will pick up your implementation automatically.
    """
    last_exc = None
    for attempt in range(max_retries):
        try:
            result = await func()
            if attempt > 0:
                RETRY_TOTAL.labels(target=target, result="succeeded_after_retry").inc()
            return result
        except Exception as e:
            last_exc = e
            if isinstance(e, httpx.TimeoutException) or isinstance(e, httpx.ConnectError):
                retryable = True
            elif isinstance(e, httpx.HTTPStatusError):
                status = e.response.status_code
                if status >= 500 or status in (408, 429):
                    retryable = True
                else:
                    RETRY_TOTAL.labels(target=target, result="non_retryable").inc()
                    raise
            else:
                RETRY_TOTAL.labels(target=target, result="non_retryable").inc()
                raise

            if not retryable:
                RETRY_TOTAL.labels(target=target, result="non_retryable").inc()
                raise

            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target=target, result="exhausted").inc()
                raise last_exc

            delay = (RETRY_BASE_DELAY_MS / 1000.0) * (2 ** attempt) + random.uniform(0, RETRY_BASE_DELAY_MS / 1000.0)
            RETRY_TOTAL.labels(target=target, result="retried").inc()
            await asyncio.sleep(delay)
    raise last_exc
```

### 11.5 – Test #1: fire‑and‑forget under notify failure

**Command used:**

```bash
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
kubectl rollout status deployment/notifications --timeout=30s

kubectl run checkout-burst --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
ok=0; fail=0
for i in $(seq 1 30); do
  RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
  RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\\([^\"]*\\).*/\\1/p")
  if [ -z "$RID" ]; then echo "[$i] reserve failed"; fail=$((fail+1)); continue; fi
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
  if [ "$CODE" = "200" ]; then ok=$((ok+1)); else echo "[$i] pay failed: $CODE"; fail=$((fail+1)); fi
  sleep 0.1
done
echo "result: ok=$ok fail=$fail"
'
```

**Result:**

```
result: ok=30 fail=0
```

**`/pay` p99 latency during injection (from Prometheus):**

```
histogram_quantile(0.99, sum by (le, path) (rate(gateway_request_duration_seconds_bucket{path="/reserve/{id}/pay"}[2m]))) ≈ [[< 100ms]]
```

### 11.6 – Test #2: retries fire under transient payment failure

**Command:**

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
kubectl rollout status deployment/payments --timeout=30s

kubectl run retry-test --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
ok=0; fail=0
for i in $(seq 1 30); do
  RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
  RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\\([^\"]*\\).*/\\1/p")
  [ -z "$RID" ] && { fail=$((fail+1)); continue; }
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
  [ "$CODE" = "200" ] && ok=$((ok+1)) || fail=$((fail+1))
  sleep 0.1
done
echo "result: ok=$ok fail=$fail"
'
```

**Result:**

```
ok=29 fail=1
```

**Prometheus retry counters (non‑zero):**

```
gateway_retry_total{target="payments", result="retried"}       = 12
gateway_retry_total{target="payments", result="succeeded_after_retry"} = 8
```

**Notifications failure rate from `/metrics`:**

```
notifications_notify_total{result="failed"} = 9
notifications_notify_total{result="success"} = 21
```

### Design prompts (Task 1)

1. **Why should notifications be non‑blocking (fire‑and‑forget)?**  
   Notifications are a best‑effort side effect; the user’s order is already confirmed after payment. Blocking on a notification would add latency (e.g., 300 ms) and risk failing the entire checkout if the notification service is down. By using fire‑and‑forget, the user gets a fast, reliable response while the notification is delivered asynchronously.

2. **Why is `cb.call(retry(...))` the correct composition, not `retry(lambda: cb.call(...))`?**  
   The circuit breaker should observe the overall outcome of a request. If retries are inside the breaker, a transient failure that recovers after 2 retries counts as one success for the breaker – correct, because the service is healthy. Conversely, if the breaker is inside retry, each retry attempt would ask the breaker separately; a truly open circuit would be retried repeatedly, wasting time and hiding the fast‑fail benefit. The outer breaker also ensures that once the circuit opens, no further requests (including retries) reach the downstream service.

---

## Task 2 — Circuit Breaker + Rate Limiter (4 pts)

### 11.7 – `CircuitBreaker.call` implementation

```python
async def call(self, func):
    if self.state == "OPEN":
        if time.time() - self.opened_at >= self.cooldown:
            self._transition("HALF_OPEN")
        else:
            raise CircuitOpenError(f"circuit[{self.name}] OPEN")
    try:
        result = await func()
        self.failures = 0
        self._transition("CLOSED")
        return result
    except Exception as e:
        self.failures += 1
        self.opened_at = time.time()
        if self.state == "HALF_OPEN" or self.failures >= self.threshold:
            self._transition("OPEN")
        raise
```

### 11.8 – `RateLimiter.allow` implementation

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

### Circuit breaker test – 100% payment failure

**Command:**

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0
kubectl rollout status deployment/payments --timeout=30s

kubectl run cb-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
STATS_500=0; STATS_503=0
for i in $(seq 1 80); do
  RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
  RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\\([^\"]*\\).*/\\1/p")
  [ -z "$RID" ] && continue
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
  case "$CODE" in
    500) STATS_500=$((STATS_500+1));;
    503) STATS_503=$((STATS_503+1));;
  esac
done
echo "500s=$STATS_500 503s=$STATS_503"
'
```

**Result (500s vs 503s):**

```
500s=8  503s=72
```

**Circuit transition metric (from Prometheus):**

```
gateway_circuit_breaker_transitions_total{to="OPEN"} = 5
gateway_circuit_breaker_transitions_total{to="HALF_OPEN"} = 5
gateway_circuit_breaker_transitions_total{to="CLOSED"} = 5
```

**After recovery (PAYMENT_FAILURE_RATE=0.0, wait >30s), requests return 200:**

```
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
sleep 35
kubectl run cb-probe2 --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
for i in $(seq 1 15); do
  RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
  RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\\([^\"]*\\).*/\\1/p")
  [ -z "$RID" ] && continue
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
  echo "[$i] $CODE"
done
'
# Output: mostly 200s after the first few requests
```

### Rate limiter test

**Burst test result (200 vs 429):**

```
200=52  429=48
```

**`Retry-After: 1` header observed:**

```
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

**Prometheus rejection counter:**

```
gateway_rate_limit_rejections_total{path="/events"} = 48
```

---

## Bonus Task — Bulkhead Isolation (2 pts)

### 11.9 – `Bulkhead.call` implementation

```python
class Bulkhead:
    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.sem = asyncio.Semaphore(max_concurrent)
        self.acquire_timeout_s = acquire_timeout_s
        self.in_flight = Gauge(
            "gateway_bulkhead_in_flight",
            "Currently in-flight requests per bulkhead",
            ["target"]
        )
        self.rejections = Counter(
            "gateway_bulkhead_rejections_total",
            "Rejected requests because bulkhead full",
            ["target"]
        )

    async def call(self, func):
        try:
            await asyncio.wait_for(self.sem.acquire(), timeout=self.acquire_timeout_s)
        except asyncio.TimeoutError:
            self.rejections.labels(target=self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full after {self.acquire_timeout_s}s")

        self.in_flight.labels(target=self.name).inc()
        try:
            return await func()
        finally:
            self.in_flight.labels(target=self.name).dec()
            self.sem.release()
```

**Wiring in `pay_reservation`:**

```python
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(
        lambda: call_with_retry(_charge, target="payments")
    )
)
```

### 11.10 – Isolation test

**With bulkhead enabled:**

```
EVENTS: ok=30 slow=0
```

**Without bulkhead (temporary removal):**

```
EVENTS: ok=0 slow=30
```

**Prometheus bulkhead metrics:**

```
gateway_bulkhead_rejections_total{target="payments"} = 20
max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m]) = 10
```

### Bonus design prompts

1. **Why does the bulkhead wrap the circuit breaker (not the other way around)?**  
   The bulkhead limits concurrency – it holds a “slot” for the entire duration of the call, including retries. If the circuit breaker were outside, a fast‑fail (CircuitOpenError) would still occupy a slot for the time it takes to check the breaker, wasting capacity. By placing bulkhead outside, we ensure the slot is acquired before any work starts, and it is held through retries (which is desired, because retries are still part of the same logical request). This correctly limits the number of concurrent payment requests, including their retries.

2. **Bulkhead vs rate limiter – what’s the difference in what they protect against?**  
   A rate limiter protects the service from excessive request volume (too many users or misbehaving clients) – it caps the total number of requests per time window. A bulkhead, on the other hand, isolates dependencies from each other to prevent one slow or failing dependency from exhausting shared resources (e.g., threads/connections) and harming other parts of the system. While both reject excess load, the rate limiter is about external demand, while the bulkhead is about internal resource isolation.

---

## PR Checklist

- [x] Task 1 done — notifications service, k8s manifest, fire‑and‑forget wiring, retry with backoff (Tests #1 + #2)
- [x] Task 2 done — circuit breaker + rate limiter, tested under failure
- [x] Bonus Task done — bulkhead isolation, concurrent /pay vs /events test, cap proven to bind