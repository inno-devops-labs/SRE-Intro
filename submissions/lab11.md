# Lab 11: Advanced Microservice Patterns

**Project:** QuickTicket  
**Branch:** `feature/lab11`  
**Cluster:** `k3d` cluster named `quickticket`  
**Gateway replicas:** 5  
**Student deliverable:** Notifications service, retry, circuit breaker, rate limiter, bulkhead isolation, Kubernetes manifests, Docker Compose updates, and test evidence

---

## 1. Goal of the Lab

The goal of this lab was to add a fourth service to QuickTicket and improve the reliability of communication between services.

The new service is the Notifications service. It receives an `order_confirmed` event after a successful checkout.

The Gateway was extended with the following resilience patterns:

1. Retry with exponential backoff and jitter
2. Circuit breaker for the Payments service
3. Sliding-window rate limiter for incoming requests
4. Fire-and-forget notification delivery
5. Bulkhead isolation for slow Payments calls

All patterns were tested in a real `k3d` Kubernetes cluster using fault injection and Prometheus metrics.

---

## 2. Final Architecture

```text
Client
  |
  v
Gateway (5 replicas)
  |
  +-- Rate limiter on incoming requests
  |
  +-- Bulkhead -> Circuit Breaker -> Retry -> Payments
  |
  +-- Events service
  |
  +-- Fire-and-forget -> Notifications service
```

The Notifications service is not part of the critical checkout path. A notification failure must not fail a successful payment or reservation confirmation.

---

# Task 1: Notifications Service and Retry

## 3. Notifications Service Implementation

### 3.1 File structure

```text
app/notifications/
├── Dockerfile
├── main.py
└── requirements.txt
```

### 3.2 `app/notifications/main.py`

```python
"""QuickTicket Notifications: Mock notification service with fault injection."""

import logging
import os
import random
import time

from fastapi import FastAPI, HTTPException, Request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.responses import Response


NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))


logging.basicConfig(
    format=(
        '{"time":"%(asctime)s","level":"%(levelname)s",'
        '"service":"notifications","msg":"%(message)s"}'
    ),
    level=logging.INFO,
)

log = logging.getLogger("notifications")


app = FastAPI(
    title="QuickTicket Notifications",
    version="1.0.0",
)


REQUEST_COUNT = Counter(
    "notifications_requests_total",
    "Total notifications service requests",
    ["method", "path", "status"],
)

REQUEST_DURATION = Histogram(
    "notifications_request_duration_seconds",
    "Notifications request duration",
    ["method", "path"],
)

NOTIFY_TOTAL = Counter(
    "notifications_notify_total",
    "Total notification attempts",
    ["result"],
)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.time()

    response = await call_next(request)

    duration = time.time() - start
    path = request.url.path

    if not path.startswith("/metrics"):
        REQUEST_COUNT.labels(
            request.method,
            path,
            str(response.status_code),
        ).inc()

        REQUEST_DURATION.labels(
            request.method,
            path,
        ).observe(duration)

    return response


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "failure_rate": NOTIFY_FAILURE_RATE,
        "latency_ms": NOTIFY_LATENCY_MS,
    }


@app.get("/metrics")
def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.post("/notify")
def notify(body: dict | None = None):
    payload = body or {}

    event = payload.get("event")
    order_id = payload.get("order_id")

    if not event or not order_id:
        raise HTTPException(
            status_code=422,
            detail="event and order_id are required",
        )

    if NOTIFY_LATENCY_MS > 0:
        delay_seconds = NOTIFY_LATENCY_MS / 1000

        log.info(
            "Injecting %sms latency for event=%s order=%s",
            NOTIFY_LATENCY_MS,
            event,
            order_id,
        )

        time.sleep(delay_seconds)

    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()

        log.warning(
            "Notification failed (injected): event=%s order=%s",
            event,
            order_id,
        )

        raise HTTPException(
            status_code=500,
            detail="Notification delivery failed",
        )

    NOTIFY_TOTAL.labels("success").inc()

    log.info(
        "Notification sent: event=%s order=%s",
        event,
        order_id,
    )

    return {
        "status": "sent",
        "event": event,
        "order_id": order_id,
    }
```

### 3.3 `app/notifications/requirements.txt`

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

### 3.4 `app/notifications/Dockerfile`

```dockerfile
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8083

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8083"]
```

---

## 4. Notifications Kubernetes Manifest

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
  labels:
    app: notifications
spec:
  type: ClusterIP
  selector:
    app: notifications
  ports:
    - name: http
      port: 8083
      targetPort: 8083
```

### Build and deploy commands

```bash
docker build -t quickticket-notifications:v1 ./app/notifications
k3d image import -c quickticket quickticket-notifications:v1
kubectl apply -f k8s/notifications.yaml
kubectl rollout status deployment/notifications --timeout=60s
```

### Deployment result

```text
deployment "notifications" successfully rolled out
```

```text
NAME                             READY   STATUS    RESTARTS   AGE
notifications-84d99c9f94-p2mk6   1/1     Running   0          6s
```

### Service result

```text
NAME            TYPE        CLUSTER-IP     EXTERNAL-IP   PORT(S)    AGE
notifications   ClusterIP   10.43.221.65   <none>        8083/TCP   9s
```

### Direct in-cluster verification

Command:

```bash
kubectl run notify-check \
  --image=curlimages/curl:latest \
  --rm -i \
  --restart=Never \
  --quiet \
  --command -- sh -c '
curl -s http://notifications:8083/health
echo
curl -s -X POST http://notifications:8083/notify \
  -H "Content-Type: application/json" \
  -d "{\"event\":\"order_confirmed\",\"order_id\":\"test-123\"}"
echo
'
```

Output:

```json
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
{"status":"sent","event":"order_confirmed","order_id":"test-123"}
```

---

## 5. Gateway Configuration

The Gateway Rollout uses 5 replicas.

The following environment variables were added to `k8s/gateway.yaml`:

```yaml
- name: EVENTS_URL
  value: "http://events:8081"
- name: PAYMENTS_URL
  value: "http://payments:8082"
- name: GATEWAY_TIMEOUT_MS
  value: "5000"
- name: NOTIFICATIONS_URL
  value: "http://notifications:8083"
- name: RETRY_MAX
  value: "3"
- name: RETRY_BASE_DELAY_MS
  value: "100"
- name: CB_FAILURE_THRESHOLD
  value: "5"
- name: CB_COOLDOWN_S
  value: "30"
- name: RATE_LIMIT_RPS
  value: "10"
- name: BULKHEAD_PAYMENTS_MAX
  value: "10"
- name: BULKHEAD_PAYMENTS_TIMEOUT_S
  value: "0.5"
```

Runtime verification from a Gateway pod:

```text
NOTIFICATIONS_URL=http://notifications:8083
RETRY_MAX=3
RATE_LIMIT_RPS=10
RETRY_BASE_DELAY_MS=100
CB_FAILURE_THRESHOLD=5
CB_COOLDOWN_S=30
BULKHEAD_PAYMENTS_MAX=10
BULKHEAD_PAYMENTS_TIMEOUT_S=0.5
```

---

## 6. Fire-and-Forget Notification Delivery

The Gateway sends the notification after payment and reservation confirmation:

```python
asyncio.create_task(
    _notify_order_confirmed(reservation_id)
)
```

The helper performs the actual HTTP request:

```python
async def _notify_order_confirmed(reservation_id: str):
    if not NOTIFICATIONS_URL:
        return

    try:
        await client.post(
            f"{NOTIFICATIONS_URL}/notify",
            json={
                "event": "order_confirmed",
                "order_id": reservation_id,
            },
            timeout=2.0,
        )
    except Exception as e:
        log.warning(
            f"notify failed (non-critical) "
            f"order={reservation_id} err={e}"
        )
```

The `/pay` request does not await the helper directly. It schedules the helper as a background asyncio task. Because of this, notification latency and notification failures do not block the user response.

---

## 7. Retry Implementation

### `call_with_retry`

```python
async def call_with_retry(
    func,
    target: str,
    max_retries: int = RETRY_MAX,
):
    base_delay = RETRY_BASE_DELAY_MS / 1000.0

    for attempt in range(max_retries):
        try:
            result = await func()

            if attempt > 0:
                RETRY_TOTAL.labels(
                    target=target,
                    result="succeeded_after_retry",
                ).inc()

            return result

        except Exception as exc:
            retryable = isinstance(
                exc,
                (
                    httpx.TimeoutException,
                    httpx.ConnectError,
                ),
            )

            if isinstance(exc, httpx.HTTPStatusError):
                status_code = exc.response.status_code

                if status_code >= 500 or status_code in (408, 429):
                    retryable = True
                else:
                    RETRY_TOTAL.labels(
                        target=target,
                        result="non_retryable",
                    ).inc()
                    raise

            if not retryable:
                RETRY_TOTAL.labels(
                    target=target,
                    result="non_retryable",
                ).inc()
                raise

            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(
                    target=target,
                    result="exhausted",
                ).inc()
                raise

            delay = (
                base_delay * (2 ** attempt)
                + random.uniform(0, base_delay)
            )

            RETRY_TOTAL.labels(
                target=target,
                result="retried",
            ).inc()

            log.warning(
                "retrying target=%s attempt=%s/%s delay=%.3fs error=%s",
                target,
                attempt + 1,
                max_retries,
                delay,
                exc,
            )

            await asyncio.sleep(delay)

    raise RuntimeError("retry loop exited unexpectedly")
```

### Retryable failures

The implementation retries:

```text
httpx.TimeoutException
httpx.ConnectError
HTTP 5xx
HTTP 408
HTTP 429
```

It does not retry normal client errors such as 404 or 422.

### Backoff calculation

```python
delay = (
    base_delay * (2 ** attempt)
    + random.uniform(0, base_delay)
)
```

The exponential part increases the delay after every failure. The random part adds jitter and reduces synchronized retry storms.

---

## 8. Test 1: Fire-and-Forget Under Notification Failure

### Fault injection

```bash
kubectl set env deployment/notifications \
  NOTIFY_FAILURE_RATE=0.3 \
  NOTIFY_LATENCY_MS=300
```

Health output:

```json
{"status":"healthy","failure_rate":0.3,"latency_ms":300}
```

### Checkout test result

Thirty complete checkout chains were executed.

Exact output:

```text
result: ok=30 fail=0
```

This shows that all user requests succeeded even though the Notifications service had a 30% configured failure rate and 300 ms configured latency.

### Notification metrics

Exact output:

```text
notifications_notify_total{result="success"} 20.0
notifications_notify_total{result="failed"} 10.0
```

Observed failure rate:

```text
10 / (20 + 10) = 0.3333 = 33.3%
```

The measured rate is close to the configured random failure rate of 30%.

### `/pay` p99 latency

Prometheus query:

```promql
histogram_quantile(
  0.99,
  sum by (le) (
    rate(
      gateway_request_duration_seconds_bucket{
        path="/reserve/{id}/pay"
      }[2m]
    )
  )
)
```

Exact Prometheus output:

```json
{
  "status":"success",
  "data":{
    "resultType":"vector",
    "result":[
      {
        "metric":{},
        "value":[1784229868.040,"0.009938868625108843"]
      }
    ]
  }
}
```

Result:

```text
p99 = 0.0099388686 seconds
p99 = approximately 9.94 ms
```

This is far below the injected 300 ms notification latency and below the required 100 ms limit.

### Average `/pay` latency

Exact Prometheus result:

```json
{
  "status":"success",
  "data":{
    "resultType":"vector",
    "result":[
      {
        "metric":{},
        "value":[1784229875.013,"0.006212954890279092"]
      }
    ]
  }
}
```

Result:

```text
Average = approximately 6.21 ms
```

### Example notification log output

```text
Notification sent: event=order_confirmed order=cdbdaf48-d5b4-41e4-86f2-f818d07e4d55
Notification failed (injected): event=order_confirmed order=77993d7c-d035-4bb2-b735-eec1a6af6197
Notification sent: event=order_confirmed order=062c1c45-aa6e-4467-b718-181f5430da00
Notification failed (injected): event=order_confirmed order=c4652b82-d04c-4853-bf5c-dfd0b061cd05
```

### Conclusion for Test 1

The notification call is genuinely non-blocking.

The user checkout path completed successfully in all 30 cases. The 300 ms notification latency did not appear in `/pay` latency, and notification failures did not affect the user result.

---

## 9. Test 2: Retry Under Payment Failure

### Fault injection

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
```

Health output:

```json
{"status":"healthy","failure_rate":0.3,"latency_ms":0}
```

### Exact checkout result

```text
[28] pay failed: 500
result: ok=29 fail=1
```

This result is consistent with three total attempts per payment. Most transient failures were recovered by retry.

### Exact retry metrics per Gateway pod

```text
===== pod/gateway-7c76d49544-hgr7x =====
gateway_retry_total{result="retried",target="payments"} 3.0
gateway_retry_total{result="succeeded_after_retry",target="payments"} 2.0

===== pod/gateway-7c76d49544-pcv24 =====
gateway_retry_total{result="retried",target="payments"} 4.0
gateway_retry_total{result="succeeded_after_retry",target="payments"} 4.0

===== pod/gateway-7c76d49544-wk6jp =====
gateway_retry_total{result="retried",target="payments"} 5.0
gateway_retry_total{result="succeeded_after_retry",target="payments"} 2.0
gateway_retry_total{result="exhausted",target="payments"} 1.0

===== pod/gateway-7c76d49544-zrg7v =====
gateway_retry_total{result="retried",target="payments"} 2.0
gateway_retry_total{result="succeeded_after_retry",target="payments"} 2.0
```

### Aggregated Prometheus result

Exact output:

```json
{
  "status":"success",
  "data":{
    "resultType":"vector",
    "result":[
      {
        "metric":{
          "result":"retried",
          "target":"payments"
        },
        "value":[1784230298.474,"14"]
      },
      {
        "metric":{
          "result":"succeeded_after_retry",
          "target":"payments"
        },
        "value":[1784230298.474,"10"]
      },
      {
        "metric":{
          "result":"exhausted",
          "target":"payments"
        },
        "value":[1784230298.474,"1"]
      }
    ]
  }
}
```

Summary:

```text
retried = 14
succeeded_after_retry = 10
exhausted = 1
```

### Conclusion for Test 2

The retry logic was active and recovered 10 checkout operations after at least one failed payment attempt. Only one operation exhausted all attempts.

---

## 10. Why Notifications Must Be Non-Blocking

Notifications are not part of the core transaction.

The critical business operation is:

1. Reserve tickets
2. Charge payment
3. Confirm the reservation

The notification is a secondary action. It informs the user after the transaction has already succeeded.

If the Gateway waited for notifications synchronously, a slow or unavailable Notifications service would make successful checkouts slow or fail completely. That would couple a non-critical service to the critical user path.

Fire-and-forget delivery keeps the checkout responsive and prevents notification failures from reducing system availability.

In a larger production system, a durable message queue would be safer than an in-process asyncio task because the task can be lost if the Gateway pod exits. For this lab, `asyncio.create_task` correctly demonstrates the latency and failure isolation behavior.

---

## 11. Why `cb.call(retry(...))` Is Correct

The implemented order is:

```python
payments_cb.call(
    lambda: call_with_retry(_charge, "payments")
)
```

The retry operation is inside the circuit breaker.

This means one user payment operation, including all internal attempts, produces one final result for the circuit breaker.

If one of the retry attempts succeeds, the circuit breaker sees the operation as successful.

If all retry attempts fail, the circuit breaker records one failed user operation.

The reverse order would be:

```python
retry(
    lambda: payments_cb.call(_charge)
)
```

This is incorrect because the retry wrapper could retry a `CircuitOpenError`. That would work against the purpose of the circuit breaker, which is to fail immediately while the downstream service is considered unhealthy.

It would also make every internal retry visible to the circuit breaker as a separate failure, which would open the circuit faster than intended.

---

# Task 2: Circuit Breaker and Rate Limiter

## 12. Circuit Breaker Implementation

```python
class CircuitOpenError(Exception):
    """Raised when the circuit is open."""


class CircuitBreaker:
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    HALF_OPEN = "HALF_OPEN"

    def __init__(
        self,
        threshold: int,
        cooldown_s: float,
        name: str = "cb",
    ):
        self.threshold = threshold
        self.cooldown = cooldown_s
        self.name = name
        self.failures = 0
        self.state = self.CLOSED
        self.opened_at = 0.0

    def _transition(self, new_state: str):
        if self.state != new_state:
            log.warning(
                f"circuit[{self.name}] "
                f"{self.state} -> {new_state}"
            )

            CB_STATE_TRANSITIONS.labels(new_state).inc()

        self.state = new_state

    async def call(self, func):
        if self.state == self.OPEN:
            if time.time() - self.opened_at >= self.cooldown:
                self._transition(self.HALF_OPEN)
            else:
                raise CircuitOpenError(
                    f"circuit[{self.name}] OPEN"
                )

        try:
            result = await func()

        except Exception:
            self.failures += 1
            self.opened_at = time.time()

            if (
                self.state == self.HALF_OPEN
                or self.failures >= self.threshold
            ):
                self._transition(self.OPEN)

            raise

        self.failures = 0
        self._transition(self.CLOSED)

        return result
```

---

## 13. Circuit Breaker Failure Test

### Fault injection

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0
```

### Exact test output

```text
500s=24 502s=0 503s=42 other=4
```

Interpretation:

- The 500 responses happened while the circuit was still CLOSED and retries were being exhausted.
- The 503 responses happened after the circuit opened and the Gateway began fast-failing.
- The additional responses were caused by other request-path behavior during the 80-request run, including rate limiting on normalized endpoints.

### Circuit state per Gateway pod

Each of the five Gateway pods opened its own circuit:

```text
gateway_circuit_breaker_transitions_total{to="OPEN"} 1.0
```

This appeared on every Gateway pod.

### Aggregated Prometheus output

```json
{
  "status":"success",
  "data":{
    "resultType":"vector",
    "result":[
      {
        "metric":{"to":"OPEN"},
        "value":[1784230391.903,"5"]
      }
    ]
  }
}
```

Result:

```text
OPEN transitions = 5
```

This matches the five Gateway replicas. Each process keeps its own in-memory circuit breaker state.

---

## 14. Circuit Breaker Recovery Test

Payments were restored:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
```

After waiting longer than the 30-second cooldown, 20 new checkout attempts were executed.

### Exact output

```text
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
[16] 200
[17] 200
[18] 200
[19] 200
[20] 200
recovery: ok=20 fail=0
```

### Exact Prometheus output

```json
{
  "status":"success",
  "data":{
    "resultType":"vector",
    "result":[
      {
        "metric":{"to":"OPEN"},
        "value":[1784230457.866,"5"]
      },
      {
        "metric":{"to":"HALF_OPEN"},
        "value":[1784230457.866,"5"]
      },
      {
        "metric":{"to":"CLOSED"},
        "value":[1784230457.866,"5"]
      }
    ]
  }
}
```

Summary:

```text
OPEN = 5
HALF_OPEN = 5
CLOSED = 5
```

Each Gateway replica moved from OPEN to HALF_OPEN and then returned to CLOSED after a successful payment probe.

---

## 15. Rate Limiter Implementation

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

The middleware skips `/health` and `/metrics`.

When a request is rejected, the Gateway returns:

```json
{
  "error": "rate_limited",
  "path": "/events",
  "limit_rps": 10
}
```

with:

```text
HTTP 429
Retry-After: 1
```

---

## 16. Rate Limiter Burst Test

One hundred rapid requests were sent to `/events`.

Exact output:

```text
200=50 429=50 other=0
```

The result matches the expected cluster-wide behavior:

```text
5 Gateway pods * 10 requests per second = approximately 50 accepted requests
```

The remaining requests were rejected with HTTP 429.

### Header verification

Exact output:

```text
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

### Prometheus output

```json
{
  "status":"success",
  "data":{
    "resultType":"vector",
    "result":[
      {
        "metric":{"path":"/events/{id}/reserve"},
        "value":[1784230488.926,"10"]
      },
      {
        "metric":{"path":"/reserve/{id}/pay"},
        "value":[1784230488.926,"4"]
      },
      {
        "metric":{"path":"/events"},
        "value":[1784230488.926,"53"]
      }
    ]
  }
}
```

The `/events` endpoint had 53 accumulated rate-limit rejections at query time.

---

## 17. Sustained Traffic Test

Thirty requests were sent with a delay of 0.25 seconds between requests.

Exact output:

```text
sustained: 200=30 limited=0
```

This shows that traffic below the configured per-pod rate limit is not rejected.

---

# Bonus Task: Bulkhead Isolation

## 18. Bulkhead Configuration

```python
BULKHEAD_PAYMENTS_MAX = int(
    os.getenv("BULKHEAD_PAYMENTS_MAX", "10")
)

BULKHEAD_PAYMENTS_TIMEOUT_S = float(
    os.getenv("BULKHEAD_PAYMENTS_TIMEOUT_S", "0.5")
)
```

---

## 19. Bulkhead Metrics

```python
BULKHEAD_IN_FLIGHT = Gauge(
    "gateway_bulkhead_in_flight",
    "Current requests occupying a bulkhead slot",
    ["target"],
)

BULKHEAD_REJECTIONS = Counter(
    "gateway_bulkhead_rejections_total",
    "Requests rejected because the bulkhead was full",
    ["target"],
)
```

---

## 20. Bulkhead Implementation

```python
class BulkheadFullError(Exception):
    """Raised when a bulkhead slot cannot be acquired before timeout."""


class Bulkhead:
    def __init__(
        self,
        name: str,
        max_concurrent: int,
        acquire_timeout_s: float,
    ):
        self.name = name
        self.acquire_timeout_s = acquire_timeout_s
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def call(self, func):
        try:
            await asyncio.wait_for(
                self.semaphore.acquire(),
                timeout=self.acquire_timeout_s,
            )

        except asyncio.TimeoutError as exc:
            BULKHEAD_REJECTIONS.labels(
                target=self.name
            ).inc()

            raise BulkheadFullError(
                f"bulkhead[{self.name}] full"
            ) from exc

        BULKHEAD_IN_FLIGHT.labels(
            target=self.name
        ).inc()

        try:
            return await func()

        finally:
            BULKHEAD_IN_FLIGHT.labels(
                target=self.name
            ).dec()

            self.semaphore.release()
```

Instantiation:

```python
payments_bulkhead = Bulkhead(
    name="payments",
    max_concurrent=BULKHEAD_PAYMENTS_MAX,
    acquire_timeout_s=BULKHEAD_PAYMENTS_TIMEOUT_S,
)
```

---

## 21. Final Resilience Composition

```python
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(
        lambda: call_with_retry(
            _charge,
            target="payments",
        )
    )
)
```

The final order is:

```text
Bulkhead -> Circuit Breaker -> Retry -> Payments call
```

### Error mapping

```python
except BulkheadFullError:
    log.error("payments bulkhead full")

    raise HTTPException(
        503,
        "Payment service temporarily unavailable "
        "(bulkhead full)",
    )
```

---

## 22. Bulkhead Test Setup

Payments latency was set to 3 seconds:

```bash
kubectl set env deployment/payments \
  PAYMENT_LATENCY_MS=3000 \
  PAYMENT_FAILURE_RATE=0.0
```

The rate limit was temporarily increased to 1000 requests per second during this isolated test. This prevented the incoming rate limiter from rejecting reservation requests before they reached the bulkhead test.

One hundred reservations were created first. Then the 100 payment calls were sent concurrently.

At the same time, the `/events` endpoint was sampled 30 times.

---

## 23. Bulkhead Test Results

Exact output:

```text
reservations_created=100
Starting concurrent payment calls...
PAYMENTS: total=100 200=29 429=0 500=21 502=0 503=50 504=0 000=0
EVENTS: ok=30 slow=0
```

Interpretation:

- 50 requests were rejected by the bulkhead with HTTP 503.
- 50 requests were admitted into per-pod bulkhead slots.
- Of the admitted requests, 29 completed the full payment and confirmation flow with HTTP 200.
- 21 admitted requests returned HTTP 500 later in the checkout flow.
- The 500 responses were not counted as bulkhead rejections.
- No request was rejected by the rate limiter because it was temporarily raised for this test.
- All 30 `/events` requests remained below the 0.5-second threshold.

### Bulkhead rejection metric

Exact Prometheus output:

```json
{
  "status":"success",
  "data":{
    "resultType":"vector",
    "result":[
      {
        "metric":{"target":"payments"},
        "value":[1784230979.372,"50"]
      }
    ]
  }
}
```

Result:

```text
gateway_bulkhead_rejections_total{target="payments"} = 50
```

### Maximum occupancy metric

Exact Prometheus output:

```json
{
  "status":"success",
  "data":{
    "resultType":"vector",
    "result":[
      {
        "metric":{"target":"payments"},
        "value":[1784230986.781,"10"]
      }
    ]
  }
}
```

Result:

```text
max gateway_bulkhead_in_flight{target="payments"} = 10
```

This proves that the configured cap was reached and never exceeded.

### Per-pod rejection counters

```text
gateway-8f4f67665-hrrkn: 13
gateway-8f4f67665-ml9c4: 6
gateway-8f4f67665-pz5ml: 12
gateway-8f4f67665-sjv5x: 12
gateway-8f4f67665-x6d7k: 7
```

Total:

```text
13 + 6 + 12 + 12 + 7 = 50
```

---

## 24. Why the Bulkhead Wraps the Circuit Breaker

The bulkhead must wrap the complete Payments operation:

```text
Bulkhead -> Circuit Breaker -> Retry -> Call
```

The main reason is that retries must remain inside one bulkhead slot.

A user payment request can make up to three downstream attempts. If every retry acquired a new slot, one user request could consume multiple concurrency slots and the configured maximum would no longer represent the number of concurrent user operations.

With the current order, one user request acquires one slot and keeps it during all internal retries.

The circuit breaker remains inside the bulkhead in this implementation because the bulkhead is the entry gate for the entire payment workflow.

A circuit-open call occupies a slot only for a very short time because it fails immediately. A real slow payment call keeps the slot for the full downstream operation.

---

## 25. Bulkhead Compared with Rate Limiter

Both patterns can reject traffic, but they solve different problems.

### Rate limiter

The rate limiter protects an API endpoint from too many requests over time.

It answers:

```text
How many requests may enter this endpoint during one second?
```

It protects against bursts, abuse, accidental loops, and excessive request rates.

### Bulkhead

The bulkhead protects the Gateway from too many concurrent operations against one dependency.

It answers:

```text
How many payment operations may be active at the same time?
```

It protects against slow dependencies, blocked tasks, connection exhaustion, and cascading failure.

A request rate can be low while concurrency is high if every request takes several seconds. This is why a rate limiter does not replace a bulkhead.

---

## 26. Docker Compose Update

The Notifications service and all Gateway configuration variables were added to `app/docker-compose.yaml`.

```yaml
services:
  gateway:
    build: ./gateway
    ports:
      - "3080:8080"
    environment:
      - EVENTS_URL=http://events:8081
      - PAYMENTS_URL=http://payments:8082
      - NOTIFICATIONS_URL=http://notifications:8083
      - GATEWAY_TIMEOUT_MS=5000
      - RETRY_MAX=3
      - RETRY_BASE_DELAY_MS=100
      - CB_FAILURE_THRESHOLD=5
      - CB_COOLDOWN_S=30
      - RATE_LIMIT_RPS=10
      - BULKHEAD_PAYMENTS_MAX=10
      - BULKHEAD_PAYMENTS_TIMEOUT_S=0.5
    depends_on:
      - events
      - payments
      - notifications

  notifications:
    build: ./notifications
    ports:
      - "8083:8083"
    environment:
      - NOTIFY_FAILURE_RATE=${NOTIFY_FAILURE_RATE:-0.0}
      - NOTIFY_LATENCY_MS=${NOTIFY_LATENCY_MS:-0}
```

Compose validation output:

```text
docker-compose config is valid
```

---

## 27. Final Validation Summary

| Test | Exact result |
|---|---:|
| Notifications checkout success | `ok=30 fail=0` |
| Notification success metric | `20` |
| Notification failure metric | `10` |
| Observed notification failure rate | `33.3%` |
| `/pay` p99 under 300 ms notification latency | `9.94 ms` |
| Retry checkout result | `ok=29 fail=1` |
| Retry attempts metric | `14` |
| Succeeded after retry metric | `10` |
| Retry exhausted metric | `1` |
| Circuit breaker 500 responses | `24` |
| Circuit breaker 503 responses | `42` |
| Circuit OPEN transitions | `5` |
| Recovery test | `ok=20 fail=0` |
| HALF_OPEN transitions | `5` |
| CLOSED transitions | `5` |
| Rate limiter burst | `200=50 429=50` |
| Retry-After header | `1` |
| Sustained rate test | `200=30 limited=0` |
| Bulkhead reservations created | `100` |
| Bulkhead HTTP 503 responses | `50` |
| Bulkhead rejection metric | `50` |
| Bulkhead maximum in-flight | `10` |
| `/events` during slow Payments | `ok=30 slow=0` |

---

## 28. Final Conclusion

The lab requirements were implemented and verified in a real Kubernetes environment.

The Notifications service was added as the fourth QuickTicket microservice and exposed the required health and Prometheus endpoints.

Fire-and-forget notification delivery kept notification latency and failures outside the critical checkout response.

Retry recovered most transient Payments failures and produced the required Prometheus counters.

The circuit breaker opened on every Gateway replica during a complete Payments outage, returned fast-fail HTTP 503 responses, entered HALF_OPEN after the cooldown, and returned to CLOSED after recovery.

The sliding-window rate limiter rejected burst traffic with HTTP 429 and returned the required `Retry-After: 1` header while allowing sustained traffic below the limit.

The bulkhead limited each Gateway process to 10 concurrent Payments operations. Under 100 concurrent Payments requests, the Prometheus maximum reached exactly 10 and 50 requests were rejected by the bulkhead. At the same time, all `/events` requests remained responsive.

One optional comparison run with the bulkhead code temporarily removed was not captured during the experiment. All implemented bulkhead behavior required by the code, rejection metric, in-flight cap, and protected `/events` result was collected and is included above.
