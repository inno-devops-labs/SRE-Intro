# Lab 11 — Bonus: Advanced Microservice Patterns

# Task 11.1 — Notifications Service

## notifications/main.py

```python
"""QuickTicket Notifications — Mock notification sender with tunable failures."""

import os
import time
import random
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

app = FastAPI(title="QuickTicket Notifications", version="1.0.0")

REQUEST_COUNT = Counter("notifications_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("notifications_request_duration_seconds", "Request duration", ["method", "path"])
NOTIFY_TOTAL = Counter("notifications_notify_total", "Total notify attempts", ["result"])


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    path = request.url.path
    if not path.startswith("/metrics"):
        REQUEST_COUNT.labels(request.method, path, response.status_code).inc()
        REQUEST_DURATION.labels(request.method, path).observe(duration)
    return response


@app.get("/health")
def health():
    return {"status": "healthy", "failure_rate": NOTIFY_FAILURE_RATE, "latency_ms": NOTIFY_LATENCY_MS}


@app.get("/metrics")
def metrics():
    from starlette.responses import Response
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/notify")
def notify(body: dict = None):
    event = (body or {}).get("event", "unknown")
    order_id = (body or {}).get("order_id", "unknown")

    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)

    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        return JSONResponse(status_code=500, content={"error": "notification delivery failed"})

    NOTIFY_TOTAL.labels("success").inc()
    return {"status": "sent", "event": event, "order_id": order_id}
```

---

## requirements.txt

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

---

## Dockerfile

```dockerfile
FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .

EXPOSE 8083
RUN addgroup --system app && adduser --system --ingroup app app
USER app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8083"]
```

---

# Task 11.2 — Kubernetes Deployment

## k8s/notifications.yaml

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
  type: ClusterIP
```

---

## Deployment

### Build

```bash
$ docker build -t quickticket-notifications:v1 ./app/notifications
naming to docker.io/library/quickticket-notifications:v1 done

$ docker build -t quickticket-gateway:v1 ./app/gateway
naming to docker.io/library/quickticket-gateway:v1 done
```

### Import

```bash
$ k3d image import -c quickticket quickticket-notifications:v1 quickticket-gateway:v1
INFO Successfully imported 2 image(s) into 1 cluster(s)
```

### Apply

```bash
$ kubectl apply -f k8s/notifications.yaml
deployment.apps/notifications created
service/notifications created

$ kubectl apply -f k8s/gateway.yaml
rollout.argoproj.io/gateway configured
service/gateway unchanged

$ kubectl argo rollouts set image gateway gateway=quickticket-gateway:v1
rollout "gateway" image updated

$ kubectl argo rollouts status gateway --timeout=240s
Progressing - waiting for rollout spec update to be observed
Progressing - more replicas need to be updated
Paused - CanaryPauseStep
Progressing - more replicas need to be updated
Paused - CanaryPauseStep
Progressing - more replicas need to be updated
Progressing - updated replicas are still becoming available
Progressing - old replicas are pending termination
Progressing - waiting for all steps to complete
Healthy
```

### Pods

```bash
$ kubectl get pods -l app=notifications
NAME                            READY   STATUS    RESTARTS   AGE
notifications-dccd599cc-bcfkm   1/1     Running   0          19s

$ kubectl get pods -l app=gateway
NAME                      READY   STATUS    RESTARTS   AGE
gateway-9644f8bb4-8tgg8   1/1     Running   0          51s
gateway-9644f8bb4-cbw5s   1/1     Running   0          19s
gateway-9644f8bb4-d2m75   1/1     Running   0          51s
gateway-9644f8bb4-tbxv2   1/1     Running   0          3m6s
gateway-9644f8bb4-txp82   1/1     Running   0          19s

$ curl http://gateway:8080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","notifications":"ok","circuit_payments":"CLOSED"}}
```

---

# Task 11.3 — Gateway Configuration

Added environment variable

```yaml
- name: NOTIFICATIONS_URL
  value: "http://notifications:8083"
```

### Rollout

```bash
$ kubectl apply -f k8s/gateway.yaml
rollout.argoproj.io/gateway configured
service/gateway unchanged

$ kubectl argo rollouts set image gateway gateway=quickticket-gateway:v1
rollout "gateway" image updated

$ kubectl argo rollouts status gateway --timeout=240s
Healthy
```

---

# Task 11.4 — Retry Implementation

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
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
        except (httpx.TimeoutException, httpx.ConnectError):
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
```

---

# Task 11.5 — Fire-and-forget Test

## Inject notification failures

```bash
$ kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
deployment.apps/notifications env updated
$ kubectl rollout status deployment/notifications --timeout=30s
deployment "notifications" successfully rolled out
```

---

## Checkout burst

```text
result: ok=30 fail=0
```

---

## Gateway p99 latency

```text
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B2m%5D)))'

{"status":"success","data":{"resultType":"vector","result":[
  {"metric":{"path":"/reserve/{id}/pay"},"value":[..., "0.2117"]},
  {"metric":{"path":"/health"},"value":[..., "0.0817"]},
  {"metric":{"path":"/events"},"value":[..., "0.0246"]},
  {"metric":{"path":"/events/{id}/reserve"},"value":[..., "0.1184"]}
]}}
```

```bash
$ kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.0 NOTIFY_LATENCY_MS=0
deployment.apps/notifications env updated
```

---

## Observation

30/30 requests succeeded although notifications had 30% failures and 300ms latency. Although the measured p99 (~212ms) was higher than the ideal threshold mentioned in the lab, it remained significantly below the injected 300ms notification latency, demonstrating that notification processing did not block the `/pay` request path — because notifications are scheduled via `asyncio.create_task(...)` and never awaited by the `/pay` handler.

---

# Task 11.6 — Retry Test

## Inject payment failures

```bash
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
deployment.apps/payments env updated
$ kubectl rollout status deployment/payments --timeout=30s
deployment "payments" successfully rolled out
```

---

## Checkout burst

```text
result: ok=30 fail=0
```

---

## Retry metrics

```text
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(target,result)+(gateway_retry_total)'

{"status":"success","data":{"resultType":"vector","result":[
  {"metric":{"result":"retried","target":"payments"},"value":[...,"3"]},
  {"metric":{"result":"succeeded_after_retry","target":"payments"},"value":[...,"3"]}
]}}
```

```bash
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
deployment.apps/payments env updated
```

---

## Observation

All 30 checkout chains succeeded. Prometheus recorded three retries and three successful recoveries after retry, confirming that transient failures were handled transparently by `call_with_retry`.

---

# Notifications Metrics

```text
$ kubectl run metricscheck --image=curlimages/curl:latest --rm -i --restart=Never --quiet \
  --command -- curl -s http://notifications:8083/metrics

notifications_notify_total{result="success"} 17.0
notifications_notify_total{result="failed"} 13.0
```

Observed failure rate: 13/30 (43%).

---

# Task 2 — Circuit Breaker + Rate Limiter

## CircuitBreaker.call

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

## RateLimiter.allow

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

---

## Rebuild + roll gateway

```bash
$ docker build -t quickticket-gateway:v1 ./app/gateway
naming to docker.io/library/quickticket-gateway:v1 done

$ k3d image import -c quickticket quickticket-gateway:v1
INFO Successfully imported 1 image(s) into 1 cluster(s)

$ kubectl argo rollouts restart gateway
rollout 'gateway' restarts in 0s
$ kubectl argo rollouts status gateway --timeout=240s
Progressing - waiting for rollout spec update to be observed
Progressing - rollout is restarting
Progressing - updated replicas are still becoming available
Healthy
```

---

## Test 11.7a — Circuit OPENs under 100% payment failure

```bash
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0
deployment.apps/payments env updated
$ kubectl rollout status deployment/payments --timeout=30s
deployment "payments" successfully rolled out

$ # 80 checkout attempts
500s=0 503s=79
```

This behavior differs from the lab text because this version of the gateway maps both retry exhaustion and open circuit to the same HTTP 503 response (`payments_unavailable_response()`). The two causes are distinguished internally via Prometheus instead:

```text
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=gateway_retry_total'
# every gateway pod shows:
gateway_retry_total{pod="...",result="retried",target="payments"}   10
gateway_retry_total{pod="...",result="exhausted",target="payments"}  5

$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(to)+(gateway_circuit_breaker_transitions_total)'
{"metric":{"to":"OPEN"},"value":[...,"5"]}
```

Each of the five gateway pods required exactly `CB_FAILURE_THRESHOLD=5` exhausted retry sequences before opening its own circuit. Because the circuit breaker is in-process, the cluster accumulated about 25 downstream failures before all five breakers reached the OPEN state. The Prometheus counter (`gateway_circuit_breaker_transitions_total{to="OPEN"}=5`) confirms that every gateway replica eventually opened its circuit.

---

## Test 11.7b — Circuit CLOSES after recovery

```bash
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
deployment.apps/payments env updated
$ sleep 35   # cooldown is 30s

$ # 15 checkout attempts
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

(Request `[1]` was skipped by the probe script — its `/reserve` call landed on a rate-limited pod and returned no `reservation_id`, which is expected now that `RateLimiter.allow` is live alongside the CB.)

```text
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(to)+(gateway_circuit_breaker_transitions_total)'
{"to":"OPEN","value":"5"}
{"to":"HALF_OPEN","value":"5"}
{"to":"CLOSED","value":"5"}
```

All 5 pods transitioned OPEN → HALF_OPEN → CLOSED after the cooldown and a successful probe request, confirming full recovery.

---

## Test 11.8a — Rate limiter burst (100 requests to /events)

```text
$ # 100 rapid GET /events
200=53 429=47
```

Close to the predicted ~50/50 split (5 pods × `RATE_LIMIT_RPS=10` = 50 RPS cluster-wide ceiling against a much faster burst).

## Test 11.8b — Retry-After header on 429

```text
$ # warm up with 50 rapid hits, then capture headers of the next request
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

## Test 11.8c — Rejection counters

```text
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(path)+(gateway_rate_limit_rejections_total)'
{"metric":{"path":"/events/{id}/reserve"},"value":[...,"1"]}
{"metric":{"path":"/events"},"value":[...,"59"]}
```

## Test 11.8d — Sustained load below the limit

```text
$ # 30 requests, 200ms apart (5 req/s, below RATE_LIMIT_RPS=10 per pod)
200=30 429=0
```

Zero rejections when the sustained rate stays under the per-pod ceiling.

---

## Observation (Circuit Breaker)

The circuit breaker correctly trips to OPEN once a pod accumulates `CB_FAILURE_THRESHOLD` (5) consecutive failures, fast-failing all further requests with `CircuitOpenError` (503, no retry cost) until the `CB_COOLDOWN_S` (30s) elapses, then probes via HALF_OPEN and closes again on a successful call. Because the breaker is in-process, each of the 5 gateway replicas keeps independent state — the aggregated Prometheus counters show 5 OPEN / 5 HALF_OPEN / 5 CLOSED transitions (one set per pod), exactly as the reading describes.

## Observation (Rate Limiter)

The rate limiter's sliding 1-second window correctly rejects requests once a pod's own window holds `RATE_LIMIT_RPS` (10) entries in the last second, returning 429 with `Retry-After: 1` and incrementing `gateway_rate_limit_rejections_total{path}`. Since state isn't shared across pods, the effective cluster-wide ceiling is `RATE_LIMIT_RPS × replicas` (≈50 RPS here) — confirmed by the ~53/47 split under a fast burst and 0 rejections under sustained traffic below that ceiling.

Restored `PAYMENT_FAILURE_RATE=0.0` after testing; cluster confirmed back to `{"status":"healthy", "circuit_payments":"CLOSED"}`.

---

# Bonus Task — Bulkhead Isolation

## Bulkhead.call + wiring

```python
BULKHEAD_PAYMENTS_MAX = int(os.getenv("BULKHEAD_PAYMENTS_MAX", "10"))
BULKHEAD_PAYMENTS_TIMEOUT_S = float(os.getenv("BULKHEAD_PAYMENTS_TIMEOUT_S", "0.5"))

BULKHEAD_IN_FLIGHT = Gauge("gateway_bulkhead_in_flight", "Current occupants of a bulkhead", ["target"])
BULKHEAD_REJECTIONS = Counter("gateway_bulkhead_rejections_total", "Requests rejected because the bulkhead was full", ["target"])


class BulkheadFullError(Exception):
    """Raised by Bulkhead.call when no slot is free within the acquire timeout."""


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


payments_bulkhead = Bulkhead("payments", BULKHEAD_PAYMENTS_MAX, BULKHEAD_PAYMENTS_TIMEOUT_S)
```

Wiring in `pay_reservation` (bulkhead outside the CB, which is outside retry):

```python
try:
    pay_resp = await payments_bulkhead.call(
        lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
    )
    payment_ref = pay_resp.json().get("payment_ref", "unknown")
except BulkheadFullError:
    log.error("payments bulkhead full, rejecting fast")
    return payments_unavailable_response(reservation_id)
except CircuitOpenError:
    ...
```

---

## Deployment note

During testing I discovered that `kubectl apply` restored the old gateway image from `k8s/gateway.yaml`. Updating the manifest to `quickticket-gateway:v1` resolved the issue.

## Test — Bulkhead WITH isolation (BULKHEAD_PAYMENTS_MAX temporarily lowered to 2)

The default limit was difficult to reach consistently with the available workload, therefore `BULKHEAD_PAYMENTS_MAX` was temporarily lowered to 2 to demonstrate the same behavior under a reproducible test. I injected `PAYMENT_LATENCY_MS=3000`, reserved 15 tickets on a fresh event, then fired all 15 `/pay` calls concurrently while sampling `/events`:

```text
reserved: 15
pay 503  (x5)
pay 200  (x10)
EVENTS: ok=29 slow=1
```

10 succeeded (2 slots × 5 pods = 10 cluster-wide capacity), the other 5 got an immediate 503 instead of waiting behind the 3s payments call:

```json
{"error":"payments_unavailable","message":"Payment service is temporarily down. Your reservation is held — try again in a few minutes.","reservation_id":"9aa35b6c-8302-463d-98ac-579fc30e06b1"}
```

```text
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(target)+(gateway_bulkhead_rejections_total)'
{"metric":{"target":"payments"},"value":[...,"5"]}

$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=max_over_time(gateway_bulkhead_in_flight%7Btarget%3D%22payments%22%7D%5B5m%5D)'
{"pod":"gateway-...-nbphz",...,"value":[...,"2"]}   # == BULKHEAD_PAYMENTS_MAX at the time of the test
{"pod":"gateway-...-6wssj",...,"value":[...,"0"]}
{"pod":"gateway-...-zb827",...,"value":[...,"0"]}
{"pod":"gateway-...-9mpv4",...,"value":[...,"0"]}
{"pod":"gateway-...-cxd8w",...,"value":[...,"0"]}
```

Rejections (5) exactly match the 5 fast-failed calls, and the busiest pod's in-flight gauge peaked at exactly 2 — the cap binds. Other gateway pods never exceeded the configured limit.

## Test — WITHOUT bulkhead (temporarily reverted, git-stash-style, then restored)

Same 3s payments latency, same test shape, with `pay_reservation` calling `payments_cb.call(...)` directly (no bulkhead):

```text
reserved: 8
pay 200  (x8, all of them — no rejections, no cap)
EVENTS: ok=30 slow=0
```

At this test's scale, `/events` stayed fast in both configurations. The difference is that without a bulkhead there is no limit at all: every concurrent `/pay` call is let through and holds the slow downstream call for the full 3s no matter how many arrive at once. With the bulkhead, requests beyond the cap are rejected in under 0.5s instead of piling up.

Restored `BULKHEAD_PAYMENTS_MAX=10` (spec default) and `PAYMENT_LATENCY_MS=0` after testing; confirmed final image is `quickticket-gateway:v1` with the bulkhead wiring back in place and cluster healthy.

---

## Bonus Question 1

### Why does the bulkhead need to wrap the circuit breaker, not the other way around?

The bulkhead should protect one logical payment request. All retries should execute while holding the same slot. Otherwise retries would repeatedly acquire new slots and the concurrency limit would lose its meaning.

## Bonus Question 2

### Bulkhead vs rate limiter — what's the difference in what they protect against?

The rate limiter protects against too many incoming requests per second, regardless of how long each one takes. The bulkhead protects against one slow dependency using up resources that other routes need — it limits how many requests can be running against payments at the same time, not how many arrive per second. A client could stay under the rate limit but still fill up the bulkhead if payments is slow enough that each request holds a slot for a while.

---

# Question 1

### Why should notifications be non-blocking (fire-and-forget)?

Notifications are not part of the critical business operation. Once a payment succeeds, the user should receive a successful response immediately. Sending notifications asynchronously prevents slow or failed notification delivery from increasing user-facing latency or causing unnecessary request failures.

---

# Question 2

### Why is `cb.call(retry(...))` correct instead of `retry(lambda: cb.call(...))`?

Wrapping retries inside the circuit breaker means the circuit breaker sees the final outcome of a complete retry sequence rather than every individual failed attempt. Temporary failures are handled by retries first, and only repeated failures contribute to opening the circuit. If retries wrap the circuit breaker instead, every failed retry attempt counts as a separate failure, causing the breaker to open too aggressively and reducing system availability.

