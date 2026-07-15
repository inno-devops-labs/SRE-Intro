*Emil Nabiullin, e.nabiullin@innopolis.university*
---
# Lab 11 - Advanced Microservice Patterns

## Repository state

This branch contains the full Lab 11 implementation, including the internal bonus task:

- `app/notifications/` was added with `main.py`, `Dockerfile`, and `requirements.txt`
- `app/gateway/main.py` now implements retry, circuit breaker, rate limiter, and bulkhead
- `app/docker-compose.yaml` was updated to include `notifications` and the new gateway env vars
- `k8s/notifications.yaml` was added
- `k8s/gateway.yaml` was updated with `NOTIFICATIONS_URL` and the pattern tuning env vars
- `submissions/lab11.md` was added

The local verification environment still had the same course-project limitation as earlier labs: the committed gateway manifest points at a GHCR image, but the new Lab 11 gateway code and the brand-new notifications service do not exist in GHCR until after merge + CI. For honest local verification I built `quickticket-gateway:v1` and `quickticket-notifications:v1`, imported them into k3d, and applied a temporary live-only copy of `k8s/gateway.yaml` with `imagePullPolicy: IfNotPresent`. I did not keep that runtime-only manifest in the repository diff.

Two more live-only adjustments were needed to keep the tests meaningful:

- for the fire-and-forget test, I used a temporary copy of `labs/lab8/mixedload.yaml` that targets `event 3` instead of `event 1`, and I raised `event 3`'s ticket pool to `100000` so inventory would not dominate the results
- for the bulkhead bonus contrast, I temporarily raised `RATE_LIMIT_RPS` to `1000` in the live gateway manifest so the rate limiter would not hide the bulkhead signal; for the "without bulkhead" contrast run I also temporarily neutralized the cap by setting live `BULKHEAD_PAYMENTS_MAX=1000` without changing the committed repo code

## Setup

I started from the merged Lab 10 state on `main`, built the two affected images, imported them into k3d, deployed `notifications`, and rolled the gateway onto the locally built image:

```bash
docker build -t quickticket-notifications:v1 ./app/notifications
docker build -t quickticket-gateway:v1 ./app/gateway

k3d image import -c quickticket quickticket-notifications:v1 quickticket-gateway:v1

kubectl apply -f k8s/notifications.yaml
kubectl apply -f /tmp/emil-lab11-gateway-local.yaml
kubectl-argo-rollouts promote gateway --full
kubectl-argo-rollouts status gateway --timeout 300s
```

The final local rollout state before testing was:

```text
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Images:          quickticket-gateway:v1 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5
```

The notifications pod was healthy as well:

```text
NAME                             READY   STATUS    RESTARTS   AGE
notifications-774d484cfd-hdwzc   1/1     Running   0          26s
```

## Task 1 - Notifications service and retries

### Notifications service

The `notifications` service follows the `payments` template shape and respects the required fault-injection env vars:

```python
NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

REQUEST_COUNT = Counter("notifications_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("notifications_request_duration_seconds", "Request duration", ["method", "path"])
NOTIFY_TOTAL = Counter("notifications_notify_total", "Total notify attempts", ["result"])

@app.post("/notify")
def notify(body: dict = None):
    payload = body or {}
    event = payload.get("event", "unknown")
    order_id = payload.get("order_id", "unknown")

    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)

    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        raise HTTPException(500, "Notification failed")

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
      imagePullSecrets:
        - name: ghcr-secret
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
spec:
  selector:
    app: notifications
  ports:
    - port: 8083
      targetPort: 8083
```

The fire-and-forget wiring in the gateway uses the pre-wired helper plus `asyncio.create_task(...)` in `/pay`, so notify failures never block or fail the user request:

```python
async def _notify_order_confirmed(reservation_id: str):
    if not NOTIFICATIONS_URL:
        return
    try:
        await client.post(
            f"{NOTIFICATIONS_URL}/notify",
            json={"event": "order_confirmed", "order_id": reservation_id},
            timeout=2.0,
        )
    except Exception as e:
        log.warning(f"notify failed (non-critical) order={reservation_id} err={e}")

asyncio.create_task(_notify_order_confirmed(reservation_id))
```

### Retry implementation

`call_with_retry()` now does exponential backoff with jitter and only retries transient failures:

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
            status_code = e.response.status_code
            retryable = status_code >= 500 or status_code in (408, 429)
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

### Test 1 - notify failure stays invisible to checkout

With `NOTIFY_FAILURE_RATE=0.3` and `NOTIFY_LATENCY_MS=300`, the 30-checkout burst still completed successfully:

```text
result: ok=30 fail=0
```

Prometheus p99 baseline before the notify fault injection, with the same `mixedload` running:

```json
{"status":"success","data":{"resultType":"vector","result":[
  {"metric":{"path":"/events"},"value":[1784149282.079,"0.20168310445527787"]},
  {"metric":{"path":"/events/{id}/reserve"},"value":[1784149282.079,"0.222702146108823"]},
  {"metric":{"path":"/reserve/{id}/pay"},"value":[1784149282.079,"0.33347142443404026"]},
  {"metric":{"path":"/health"},"value":[1784149282.079,"0.6974999999999998"]}
]}}
```

Prometheus p99 after enabling `NOTIFY_FAILURE_RATE=0.3` and `NOTIFY_LATENCY_MS=300`:

```json
{"status":"success","data":{"resultType":"vector","result":[
  {"metric":{"path":"/health"},"value":[1784149306.594,"0.6993750000000001"]},
  {"metric":{"path":"/events"},"value":[1784149306.594,"0.4288590287972297"]},
  {"metric":{"path":"/events/{id}/reserve"},"value":[1784149306.594,"0.24796515645273212"]},
  {"metric":{"path":"/reserve/{id}/pay"},"value":[1784149306.594,"0.37389432107876025"]}
]}}
```

The important part is that the `/pay` p99 stayed in the same range before and after the notify injection (`~333ms -> ~374ms`) instead of absorbing the extra `300ms` end-to-end. That is the direct evidence that notifications are not on the user-facing critical path.

Real notify success/failure counts from the notifications service metrics:

```text
# HELP notifications_notify_total Total notify attempts
# TYPE notifications_notify_total counter
notifications_notify_total{result="success"} 121.0
notifications_notify_total{result="failed"} 51.0
```

Why should notifications be non-blocking (fire-and-forget)?

The user-facing `/pay` request is complete as soon as payment succeeds and the reservation is confirmed in `events`. Notification delivery is a best-effort side effect. If it blocks or fails the user flow, then a non-critical dependency would directly reduce checkout availability and latency. That is exactly the kind of coupling the pattern is supposed to avoid.

### Test 2 - retries recover transient payment failures

Under `PAYMENT_FAILURE_RATE=0.3`, the clean retry burst succeeded end-to-end:

```text
result: ok=30 fail=0
```

Prometheus proves that retries actually fired:

```json
{"status":"success","data":{"resultType":"vector","result":[
  {"metric":{"result":"retried","target":"payments"},"value":[1784146762.697,"12"]},
  {"metric":{"result":"succeeded_after_retry","target":"payments"},"value":[1784146762.697,"9"]}
]}}
```

Why is `cb.call(retry(...))` correct, but `retry(lambda: cb.call(...))` wrong?

`cb.call(retry(...))` means the circuit breaker sees one logical payment attempt whose internal retries are invisible outside the retry wrapper; the CB only records the final post-retry outcome. The reverse composition would retry `CircuitOpenError` itself, which defeats the fast-fail behavior of an already open circuit and wastes load on a path that was explicitly trying to stop more work.

## Task 2 - Circuit breaker and rate limiter

### Circuit breaker implementation

The `CircuitBreaker.call()` body now implements the required `CLOSED -> OPEN -> HALF_OPEN -> CLOSED` behavior:

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

Under `PAYMENT_FAILURE_RATE=1.0`, the 80-attempt probe produced both retry-exhausted `500`s and fast-fail `503`s:

```text
500s=25 503s=55
```

After restoring payments and waiting out the cooldown, the circuit closed again and `/pay` returned healthy responses:

```text
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

Prometheus state-transition counters:

```json
{"status":"success","data":{"resultType":"vector","result":[
  {"metric":{"to":"OPEN"},"value":[1784147011.828,"5"]},
  {"metric":{"to":"HALF_OPEN"},"value":[1784147011.828,"4"]},
  {"metric":{"to":"CLOSED"},"value":[1784147011.828,"4"]}
]}}
```

The `5` OPEN transitions line up with the 5 gateway pods: each pod has its own in-process circuit breaker instance.

### Rate limiter implementation

The sliding-window rate limiter is now active:

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

Burst test:

```text
200=96 429=4
```

Below-limit sustained load:

```text
sustained: 200=30 429=0
```

Direct header proof from a single gateway pod:

```text
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

Prometheus rejection counter:

```json
{"status":"success","data":{"resultType":"vector","result":[
  {"metric":{"path":"/events"},"value":[1784147078.338,"4"]}
]}}
```

The 100-request burst only produced `4` rejections because the limiter is per-process and the Service spreads requests across 5 gateway pods. That is consistent with the lab's warning that the real cluster-wide ceiling is `RATE_LIMIT_RPS * replicas`.

## Bonus Task - Bulkhead isolation

### Bulkhead implementation and wiring

I added the bonus `Bulkhead` primitive:

```python
class Bulkhead:
    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.acquire_timeout_s = acquire_timeout_s

    async def call(self, func):
        try:
            await asyncio.wait_for(
                self.semaphore.acquire(),
                timeout=self.acquire_timeout_s,
            )
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] FULL")

        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self.semaphore.release()
```

and wired it around the existing payment chain:

```python
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(
        lambda: call_with_retry(_charge, target="payments")
    )
)
```

### Contrast run without the cap

For the contrast run I temporarily neutralized the live bulkhead by setting `BULKHEAD_PAYMENTS_MAX=1000` and raised the live rate limit to `1000` so the rate limiter would not dominate the result. I then targeted a single gateway pod directly, created 30 reservations, launched 30 concurrent `/pay` calls against that same pod, and sampled `/events` from that same pod.

Without the cap, under `GATEWAY_TIMEOUT_MS=5000`, `PAYMENT_LATENCY_MS=3000`, and 100 concurrent `/pay` attempts directed at one gateway pod:

```text
OFF5000 EVENTS: ok=25 slow=5
```

Prometheus showed that there were no rejections and that in-flight payment work was allowed to grow well past the intended cap:

```json
{"status":"success","data":{"resultType":"vector","result":[]}}
```

```json
{"status":"success","data":{"resultType":"vector","result":[
  {"metric":{"instance":"10.42.0.53:8080","job":"gateway","pod":"gateway-7bff8b64f8-d5p5f","rs_hash":"7bff8b64f8","target":"payments"},"value":[1784147404.658,"30"]}
]}}
```

That was the exact failure mode the bonus task was trying to isolate: one downstream consumed a large number of concurrent slots in the same process.

### Run with the bulkhead enabled

I then restored the real `BULKHEAD_PAYMENTS_MAX=10` and repeated the same single-pod stress test with the same `GATEWAY_TIMEOUT_MS=5000` and `PAYMENT_LATENCY_MS=3000`.

Results:

```text
ON5000 PAY: 200=10 503=50 other=0
ON5000 EVENTS: ok=28 slow=2
```

The `50` fast-fail `503`s are exactly what I wanted from the bulkhead: after the first `10` payment calls occupied the slots, the rest were rejected within the acquire timeout instead of being allowed to pile up indefinitely. The `10` successful `200`s were the admitted requests that finished within the temporary `5000ms` gateway timeout.

Prometheus confirms both required bulkhead metrics:

```json
{"status":"success","data":{"resultType":"vector","result":[
  {"metric":{"target":"payments"},"value":[1784149816.577,"51"]}
]}}
```

```json
{"status":"success","data":{"resultType":"vector","result":[
  {"metric":{"instance":"10.42.0.57:8080","job":"gateway","pod":"gateway-6679bcf9d-7fk4n","rs_hash":"6679bcf9d","target":"payments"},"value":[1784147558.333,"10"]}
]}}
```

So the cap actually bound at `10`, and once it did, the extra payment load was shed as fast-fail `503`s instead of turning into unbounded concurrency.

Why does the bulkhead need to wrap the circuit breaker, not the other way around?

The concurrency cap should apply to the whole logical payment attempt, including retries and circuit-breaker logic, so one incoming `/pay` request consumes at most one slot. If the bulkhead were inside the circuit breaker, each retry could acquire its own slot and the cap would stop representing real request concurrency.

Bulkhead vs rate limiter — both reject excess traffic. What is the difference in what they protect against?

The rate limiter protects the service from too many incoming requests per endpoint over time. The bulkhead protects the rest of the process from one specific slow dependency by capping concurrent in-flight work on that dependency. One is an arrival-rate defense; the other is an isolation boundary around shared capacity.
