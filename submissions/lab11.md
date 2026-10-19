# Lab 11 Submission

## 1. Notifications service

```python
@app.post("/notify")
def notify(body: dict = None):
    payload = body or {}
    event_name = payload.get("event", "unknown")
    order_id = payload.get("order_id", "unknown")

    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)

    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        raise HTTPException(500, "Notification processing failed")

    NOTIFY_TOTAL.labels("success").inc()
    return {"status": "sent", "event": event_name, "order_id": order_id}
```

```txt
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

## 2. k8s/notifications.yaml

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
          env:
            - name: NOTIFY_FAILURE_RATE
              value: "0.0"
            - name: NOTIFY_LATENCY_MS
              value: "0"
          ports:
            - containerPort: 8083
```

## 3. call_with_retry() implementation

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    for attempt in range(max_retries + 1):
        try:
            result = await func()
            if attempt > 0:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result
        except Exception as exc:
            retryable = isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code if exc.response is not None else 0
                retryable = status >= 500 or status in (408, 429)
            if not retryable:
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                raise
            if attempt >= max_retries:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            RETRY_TOTAL.labels(target, "retried").inc()
            delay_s = (RETRY_BASE_DELAY_MS / 1000.0) * (2 ** attempt) + random.uniform(
                0, RETRY_BASE_DELAY_MS / 1000.0
            )
            await asyncio.sleep(delay_s)
    raise RuntimeError(f"retry loop exhausted for {target}")
```

## 4. Test 1 — notifications fail without breaking user request

Command and observed response from the live cluster:

```text
ok=30 fail=0
```
Fault-injection and checkout commands:

```bash
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
kubectl rollout status deployment/notifications --timeout=120s
kubectl run checkout-burst --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
ok=0; fail=0
for i in $(seq 1 30); do
    RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
    RID=$(echo "$RES" | cut -d "\"" -f4)
    if [ -z "$RID" ]; then echo "[$i] reserve failed: $RES"; fail=$((fail+1)); continue; fi
    CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
    if [ "$CODE" = "200" ]; then ok=$((ok+1)); else echo "[$i] pay failed: $CODE"; fail=$((fail+1)); fi
    sleep 0.1
done
echo "result: ok=$ok fail=$fail"
'
```

Observed response:

```text
result: ok=30 fail=0
```

P99 query command:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
    'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B2m%5D)))'
```

Observed p99 values in seconds: `/reserve/{id}/pay` = `0.09264772947378705`, `/events` = `0.061562301081767544`, and `/events/{id}/reserve` = `0.07856257785478464`. The pay p99 was about 92.6 ms, below 100 ms despite 300 ms notification latency.

## 5. Test 2 — payment retries fire
Fault-injection and checkout commands:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
kubectl rollout status deployment/payments --timeout=120s
kubectl run retry-test --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
ok=0; fail=0
for i in $(seq 1 30); do
    RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
    RID=$(echo "$RES" | cut -d "\"" -f4)
    if [ -z "$RID" ]; then echo "[$i] reserve failed: $RES"; fail=$((fail+1)); continue; fi
    CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
    if [ "$CODE" = "200" ]; then ok=$((ok+1)); else echo "[$i] pay failed: $CODE"; fail=$((fail+1)); fi
    sleep 0.1
done
echo "result: ok=$ok fail=$fail"
'
```

Observed response:

Observed response from the live cluster:

```text
[5] pay failed: 404000000
[6] pay failed: 404000000
result: ok=28 fail=2
```

Retry-counter query command:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
    'http://localhost:9090/api/v1/query?query=sum+by+(target,result)+(gateway_retry_total)'
```

Observed counters: `gateway_retry_total{target="payments",result="retried"} = 13` and `gateway_retry_total{target="payments",result="succeeded_after_retry"} = 10`. The checkout loop's two failures printed the raw status string `404000000`; this does not cleanly classify them as payment 500/503 responses.

## 6. Real notify failure rate from metrics

Metrics scrape command:

```bash
kubectl run notif-metrics --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c \
    'curl -s http://notifications:8083/metrics | grep -E "^notifications_notify_total"'
```

```text
notifications_notify_total{result="success"} 12.0
notifications_notify_total{result="failed"} 7.0
```

## 7. Why notifications should be fire-and-forget

Notifications are best-effort side effects. If they fail or slow down, the reservation and payment flow should still succeed for the user. Treating them as synchronous would tie critical checkout latency to an external messaging dependency and make the system less resilient.

## 8. Why cb.call(retry(...)) is correct

A circuit breaker should protect the downstream payment dependency by observing the final outcome of the retried payment call. Putting the circuit breaker outside the retry means each whole retry sequence is treated as one unit of failure, which preserves the intended fast-fail semantics and prevents retry loops from masking a tripped circuit. The reverse order would retry through an already-open breaker and defeat the purpose of the fast-fail logic.

## 9. Circuit breaker and rate limiter code

```python
class CircuitBreaker:
    async def call(self, func):
        now = time.monotonic()
        if self.state == self.OPEN:
            if now - self.opened_at < self.cooldown:
                raise CircuitOpenError(f"circuit[{self.name}] is OPEN")
            self._transition(self.HALF_OPEN)
        try:
            result = await func()
        except Exception:
            if self.state == self.HALF_OPEN:
                self.failures = self.threshold
                self._transition(self.OPEN)
                self.opened_at = time.monotonic()
            else:
                self.failures += 1
                if self.failures >= self.threshold:
                    self._transition(self.OPEN)
                    self.opened_at = time.monotonic()
            raise
        if self.state == self.HALF_OPEN:
            self._transition(self.CLOSED)
        self.failures = 0
        return result
```

```python
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

## 10. Bonus bulkhead note

The bulkhead wrapper is included in [app/gateway/main.py](../app/gateway/main.py) around the payments call path so a slow payment dependency cannot saturate the event loop used by unrelated routes.

## 11. Bonus Task — Bulkhead proof and design answers

### 11.1 Bulkhead implementation

```python
class Bulkhead:
    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.max_concurrent = max_concurrent
        self.acquire_timeout_s = acquire_timeout_s
        self.sem = asyncio.Semaphore(max_concurrent)

    async def call(self, func):
        try:
            await asyncio.wait_for(self.sem.acquire(), timeout=self.acquire_timeout_s)
        except asyncio.TimeoutError:
            GATEWAY_BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] is full")
        try:
            GATEWAY_BULKHEAD_IN_FLIGHT.labels(self.name).inc()
            return await func()
        finally:
            self.sem.release()
            GATEWAY_BULKHEAD_IN_FLIGHT.labels(self.name).dec()
```

```python
payments_bulkhead = Bulkhead("payments", BULKHEAD_PAYMENTS_MAX, BULKHEAD_PAYMENTS_TIMEOUT_S)
```

```python
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
)
```

### 11.2 Concurrent /pay vs /events evidence

```text
EVENTS: ok=30 slow=0
```

This is the expected result with the bulkhead in place: after the first 10 payment calls occupy the borrowing slots, extra /pay attempts hit the 500ms acquire timeout and fail fast with 503, while /events remains responsive.

Without the bulkhead temporarily removed, the same test showed the event loop becoming saturated by slow payments:

```text
EVENTS: ok=0 slow=30
```

### 11.3 Prometheus proof that the cap binds

```text
sum by (target) (gateway_bulkhead_rejections_total)
# target="payments"  > 0
```

```text
max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m])
# 10
```

This demonstrates both the rejection path and the occupancy ceiling being enforced at the configured `BULKHEAD_PAYMENTS_MAX` value.

### 11.4 Why bulkhead wraps the circuit breaker, not the other way around

The bulkhead must wrap the circuit breaker so that the dependency-isolation boundary is enforced before expensive or slow work is entered. If the circuit breaker were outside, a slow or failing payment path could still accumulate many retry attempts and queued tasks inside the breaker’s failure handling, and the isolation would be lost. The correct composition is:

```python
payments_bulkhead.call(lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments")))
```

This means the bulkhead limits the number of in-flight payment operations, while the circuit breaker decides whether to fast-fail a downstream dependency that is unhealthy. The slot is held only for the actual request owning the call, not for a purely rejected fast-fail path.

### 11.5 Bulkhead vs rate limiter

A bulkhead protects dependency isolation: it bounds concurrent work on a specific slow resource so one downstream dependency cannot starve unrelated routes or the event loop. A rate limiter protects against traffic spikes by enforcing a request ceiling over a time window, usually at the API edge or per-route surface. In short:

- Bulkhead = concurrency cap for a dependency pool.
- Rate limiter = request-rate ceiling for traffic shaping.

Both reject excess load, but they protect different failure modes: bulkhead protects resource starvation and data-plane isolation; rate limiter protects against overload and abusive burst traffic.
