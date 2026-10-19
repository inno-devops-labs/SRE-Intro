# Lab 11 — Advanced microservice patterns

## Task 1 — Notifications and retries

### 11.1: Notifications service

`app/notifications/main.py` accepts `POST /notify` with `event=order_confirmed` and an `order_id`. It logs the result, returns `status: sent` on success, and uses `NOTIFY_FAILURE_RATE` and `NOTIFY_LATENCY_MS` for fault tests. `/health` shows both settings. `/metrics` exposes `notifications_requests_total{method,path,status}`, `notifications_request_duration_seconds{method,path}`, and `notifications_notify_total{result}`.

The main part of the handler is:

```python
if NOTIFY_LATENCY_MS > 0:
    time.sleep(NOTIFY_LATENCY_MS / 1000)
if random.random() < NOTIFY_FAILURE_RATE:
    NOTIFY_TOTAL.labels("failed").inc()
    log.warning("Notification failed (injected) for order %s", order_id)
    raise HTTPException(500, "Notification failed")
NOTIFY_TOTAL.labels("success").inc()
log.info("Sent %s for order %s", event, order_id)
return {"status": "sent", "event": event, "order_id": order_id}
```

`app/notifications/requirements.txt` contains:

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

The Dockerfile runs Uvicorn on port 8083.

### 11.2: Kubernetes Deployment and Service

For Lab 11 I started with one replica. Lab 12 later changed the current manifest to two replicas. The important settings in `k8s/notifications.yaml` are:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: notifications
spec:
  replicas: 1  # Lab 11 state; Lab 12 changes this to 2
  selector:
    matchLabels: {app: notifications}
  template:
    metadata:
      labels: {app: notifications}
    spec:
      containers:
        - name: notifications
          image: quickticket-notifications:v1
          imagePullPolicy: Never
          ports: [{containerPort: 8083}]
          env:
            - {name: NOTIFY_FAILURE_RATE, value: "0.0"}
            - {name: NOTIFY_LATENCY_MS, value: "0"}
---
apiVersion: v1
kind: Service
metadata:
  name: notifications
spec:
  type: ClusterIP
  selector: {app: notifications}
  ports: [{port: 8083, targetPort: 8083}]
```

The real manifest also has health probes and resource limits. The service was Ready during the tests.

### 11.3: Gateway call

`NOTIFICATIONS_URL=http://notifications:8083` is set in `k8s/gateway.yaml` and `app/docker-compose.yaml`. After a successful payment, `/pay` starts `_notify_order_confirmed` with `asyncio.create_task`. The client does not wait for the notification. The gateway health check reports notification status, but only events and payments decide whether the gateway is healthy.

### 11.4: Retry with backoff and jitter

The retry body in `app/gateway/main.py` is:

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    for attempt in range(max(1, max_retries)):
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
            if attempt == max(1, max_retries) - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            delay = RETRY_BASE_DELAY_MS / 1000 * (2 ** attempt)
            delay += random.uniform(0, RETRY_BASE_DELAY_MS / 1000)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
```

The circuit breaker wraps the retry call. It sees one final result for a checkout. If retry wrapped the breaker, it could call an already open breaker again and weaken fast failure.

### 11.5: Test 1 — slow and failing notifications

I set `NOTIFY_FAILURE_RATE=0.3` and `NOTIFY_LATENCY_MS=300`. Two 30-checkout runs both gave `pay_200=30`. During the second run, the two notification pods reported 18 successes and 12 injected failures in total. That is 12/30 observed failures; random tests do not need to equal exactly 30%.

Prometheus reported `/pay` p99 at **0.0453 seconds**, below the lab's 100 ms target, while notifications took 300 ms. A failed or slow message did not change the payment response. This is why notification work should be fire-and-forget: the customer should not lose a paid order because a later message fails.

### 11.6: Test 2 — transient payment failures

With `PAYMENT_FAILURE_RATE=0.3`, the first 30-checkout run gave 28 successes and 2 payment 500s. A second run gave **29 successes and 1 payment 500**. After both runs, Prometheus showed `retried=29`, `succeeded_after_retry=21`, and `exhausted=3` for payments. The nonzero counters prove the retry code ran. The failure setting was restored to `0.0` after this test.

## Task 2 — Circuit breaker and rate limiter

### 11.7: Circuit breaker

`CircuitBreaker.call` uses CLOSED, OPEN, and HALF_OPEN:

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

At 100% payment failure, 80 checkouts through one gateway pod returned **6 payment 500s and 74 fast 503s**. Prometheus showed `OPEN=2` and `HALF_OPEN=1` at that point. I restored payments, waited more than the 30-second cooldown, then got **15/15 payment 200s**. The transition counters then showed `OPEN=2`, `HALF_OPEN=2`, and `CLOSED=1`. The breaker is local to each gateway process; another pod has its own state.

### 11.8: Rate limiter

The limiter keeps one second of request times for each normalized path:

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

One gateway pod received 100 concurrent `/events` requests: **10 returned 200 and 90 returned 429**. The 429 response had `Retry-After: 1`. Prometheus showed `gateway_rate_limit_rejections_total{path="/events"}=90` after that burst. A slow run of 30 requests, spaced by 0.2 seconds, gave **30/30 200** and zero 429s. The limit is per pod, so 5 gateway pods do not share one 10 RPS ceiling.

## Bonus Task — Bulkhead isolation

### 11.9: Bounded payment calls

`Bulkhead.call` owns an `asyncio.Semaphore`. Its core is:

```python
async def call(self, func):
    try:
        await asyncio.wait_for(self.semaphore.acquire(), self.acquire_timeout_s)
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

The `/pay` call is wrapped as `payments_bulkhead.call(lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments")))`. A full bulkhead returns HTTP 503. One checkout holds one slot for all of its payment retry attempts. The breaker is inside the bulkhead, so a full pool is not counted as a payment failure by the breaker. If the breaker is already open, its call returns quickly and releases the slot.

### 11.10: Isolation test

I sent 30 payments at once to one gateway pod with `PAYMENT_LATENCY_MS=3000` and a temporary 100 RPS rate limit, so the rate limiter did not hide the bulkhead result.

| Setup | Payments | `/events` under 500 ms | Slow `/events` | Highest `/events` time |
|---|---:|---:|---:|---:|
| Bulkhead, max 10 | 10 × 200, 20 × 503 | 30/30 | 0 | 484 ms |
| Temporary image without bulkhead | 30 × 200 | 27/30 | 3 | 1,125 ms |

The first run raised `gateway_bulkhead_rejections_total{target="payments"}` by 20. With 3-second payments, a 5-second Prometheus scrape saw at most 7 slots in use. I ran a separate longer saturation probe and Prometheus recorded **`max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m]) = 10`**. The control result was less extreme than the lab's example: Python's async HTTP calls still let many `/events` requests run without a bulkhead. The measured difference above is the real result. I restored the gateway image, 10 RPS setting, and payment latency afterward.

A rate limiter protects an endpoint from too many incoming requests per second. A bulkhead protects work for one dependency from taking every available concurrency slot. The local limiter is not a true cluster-wide ceiling; an ingress or shared store would be needed for that.
