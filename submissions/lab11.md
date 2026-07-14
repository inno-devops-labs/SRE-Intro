# Lab 11 — Bonus: Advanced Microservice Patterns

## Task 1 — Notifications Service + Retries

- Your app/notifications/main.py (the key bits) and requirements.txt.

app/notifications/main.py
``` 
"""QuickTicket Notifications — Mock notification service with fault injection."""

import os
import time
import random
import logging
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# --- Config (fault injection via env vars) ---
NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

# --- Logging ---
logging.basicConfig(
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"notifications","msg":"%(message)s"}',
    level=logging.INFO,
)
log = logging.getLogger("notifications")

# --- App ---
app = FastAPI(title="QuickTicket Notifications", version="1.0.0")

# --- Prometheus metrics ---
REQUEST_COUNT = Counter(
    "notifications_requests_total", "Total requests", ["method", "path", "status"]
)
REQUEST_DURATION = Histogram(
    "notifications_request_duration_seconds", "Request duration", ["method", "path"]
)
NOTIFY_TOTAL = Counter(
    "notifications_notify_total", "Total notify attempts", ["result"]
)


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
    if body is None:
        body = {}
    event = body.get("event", "unknown")
    order_id = body.get("order_id", "unknown")

    log.info(f"Notify request: event={event}, order_id={order_id}")

    # Inject latency
    if NOTIFY_LATENCY_MS > 0:
        delay = NOTIFY_LATENCY_MS / 1000
        log.info(f"Injecting {NOTIFY_LATENCY_MS}ms latency")
        time.sleep(delay)

    # Inject failures
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning(f"Notify failed (injected) for {order_id}")
        raise HTTPException(500, "Notification processing failed")

    NOTIFY_TOTAL.labels("success").inc()
    notification_id = f"NOTIFY-{uuid.uuid4().hex[:8].upper()}"
    log.info(f"Notify success: {notification_id} for {order_id}")
    return {"status": "sent", "notification_id": notification_id}

```


app/notifications/requirements.txt
```
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```


- Your k8s/notifications.yaml.
```
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
  type: ClusterIP

```

- Your call_with_retry() implementation.
```
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    """Call `func` with retry-on-transient-error."""
    base_delay = RETRY_BASE_DELAY_MS / 1000.0
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            result = await func()
            if attempt > 0:
                RETRY_TOTAL.labels(target=target, result="succeeded_after_retry").inc()
            return result
        except Exception as e:
            last_exception = e
            is_retryable = False

            # Retryable: timeout, connection errors, 5xx, 408, 429
            if isinstance(e, (httpx.TimeoutException, httpx.ConnectError)):
                is_retryable = True
            elif isinstance(e, httpx.HTTPStatusError):
                status = e.response.status_code
                if status >= 500 or status in (408, 429):
                    is_retryable = True
                else:
                    RETRY_TOTAL.labels(target=target, result="non_retryable").inc()
                    raise

            if is_retryable and attempt < max_retries:
                RETRY_TOTAL.labels(target=target, result="retried").inc()
                delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
                await asyncio.sleep(delay)
            else:
                RETRY_TOTAL.labels(target=target, result="exhausted").inc()
                raise

    raise last_exception
```


- Test #1 — ok=30 fail=0 result + /pay p99 < 100ms during the notify-failure injection (proves fire-and-forget).
```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab11)
$ kubectl run checkout-burst --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
ok=0; fail=0
for i in $(seq 1 30); do
  RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
  RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\([^\"]*\).*/\1/p")
  if [ -z "$RID" ]; then echo "[$i] reserve failed"; fail=$((fail+1)); continue; fi
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
  if [ "$CODE" = "200" ]; then ok=$((ok+1)); else echo "[$i] pay failed: $CODE"; fail=$((fail+1)); fi
  sleep 0.1
done
echo "result: ok=$ok fail=$fail"
'
result: ok=30 fail=0

axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab11)
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B2m%5D)))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/health"},"value":[1784046520.088,"0.02482540931186815"]},{"metric":{"path":"/events"},"value":[1784046520.088,"0.010085744513347662"]},{"metric":{"path":"/events/{id}/reserve"},"value":[1784046520.088,"0.03670835542613765"]},{"metric":{"path":"/reserve/{id}/pay"},"value":[1784046520.088,"0.09249999130774905"]}]}}
```

- Test #2 — ok≈30 fail<2 result + gateway_retry_total{result="retried"} and result="succeeded_after_retry" both non-zero (proves retries actually fire).
```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab11)
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
kubectl rollout status deployment/payments --timeout=30s
deployment "payments" successfully rolled out

axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab11)
$ kubectl run retry-test --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
ok=0; fail=0
for i in $(seq 1 30); do
  RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
  RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\([^\"]*\).*/\1/p")
  [ -z "$RID" ] && { fail=$((fail+1)); continue; }
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
  [ "$CODE" = "200" ] && ok=$((ok+1)) || fail=$((fail+1))
  sleep 0.1
done
echo "result: ok=$ok fail=$fail"
'

result: ok=28 fail=2

axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab11)
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(target,result)+(gateway_retry_total)'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"result":"retried","target":"payments"},"value":[1784046946.924,"18"]},{"metric":{"result":"succeeded_after_retry","target":"payments"},"value":[1784046946.924,"9"]},{"metric":{"result":"exhausted","target":"payments"},"value":[1784046946.924,"1"]}]}}
```

- Real notify failure rate from the notifications pod's /metrics (notifications_notify_total{result}).

```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab11)
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B2m%5D)))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/health"},"value":[1784046520.088,"0.02482540931186815"]},{"metric":{"path":"/events"},"value":[1784046520.088,"0.010085744513347662"]},{"metric":{"path":"/events/{id}/reserve"},"value":[1784046520.088,"0.03670835542613765"]},{"metric":{"path":"/reserve/{id}/pay"},"value":[1784046520.088,"0.09249999130774905"]}]}}


axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab11)
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(target,result)+(gateway_retry_total)'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"result":"retried","target":"payments"},"value":[1784046946.924,"18"]},{"metric":{"result":"succeeded_after_retry","target":"payments"},"value":[1784046946.924,"9"]},{"metric":{"result":"exhausted","target":"payments"},"value":[1784046946.924,"1"]}]}}
```


- Answer: "Why should notifications be non-blocking (fire-and-forget)?"

Notifications should be non-blocking because they are not critical to the user's immediate request. If notifications fail, the user should still receive their order confirmation. Blocking on notifications would add unnecessary latency to the critical path and could cause user-facing errors if the notification service is slow or unavailable.

- Answer (Design Prompt from 11.4): "Why is cb.call(retry(...)) the correct composition for Task 2, not retry(lambda: cb.call(...))?"

`cb.call(retry(...))` is correct because the circuit breaker should count the final outcome of the retry attempt, not each individual retry. If we put retry outside the circuit breaker (`retry(lambda: cb.call(...))`), the circuit breaker would fast-fail immediately on the first attempt, and retry would keep retrying a fast-failing circuit, defeating the purpose of the circuit breaker. The circuit breaker should only allow requests through when the circuit is closed, and retries should happen inside that single attempt.


## Task 2 — Circuit Breaker + Rate Limiter 

- Your CircuitBreaker and RateLimiter class code.

CircuitBreaker

```
async def call(self, func):
    if self.state == self.OPEN:
        if time.time() - self.opened_at >= self.cooldown:
            self._transition(self.HALF_OPEN)
        else:
            raise CircuitOpenError(f"circuit[{self.name}] OPEN")

    try:
        result = await func()
        self.failures = 0
        if self.state != self.CLOSED:
            self._transition(self.CLOSED)
        return result
    except Exception as e:
        self.failures += 1
        self.opened_at = time.time()
        if self.state == self.HALF_OPEN or self.failures >= self.threshold:
            self._transition(self.OPEN)
        raise
```

RateLimiter


```
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

- 500s/503s breakdown from the CB test under 100% payment failure.

```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab11)
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0
kubectl rollout status deployment/payments --timeout=30s
deployment.apps/payments env updated
Waiting for deployment "payments" rollout to finish: 0 out of 1 new replicas have been updated...
Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
deployment "payments" successfully rolled out

axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab11)
$ kubectl run cb-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
STATS_500=0; STATS_503=0
for i in $(seq 1 80); do
  RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
  RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\([^\"]*\).*/\1/p")
  [ -z "$RID" ] && continue
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
  case "$CODE" in
    500) STATS_500=$((STATS_500+1));;
    503) STATS_503=$((STATS_503+1));;
  esac
done
echo "500s=$STATS_500 503s=$STATS_503"
'
500s=25 503s=52
```


- 200s after recovery showing the circuit closed.

```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab11)
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
sleep 35
kubectl run cb-probe2 --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
for i in $(seq 1 15); do
  RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
  RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\([^\"]*\).*/\1/p")
  [ -z "$RID" ] && continue
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
  echo "[$i] $CODE"
done
'
deployment.apps/payments env updated
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

- 200/429 split from the rate-limit burst test.

```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab11)
$ kubectl run rl-burst --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
OK=0; LIMITED=0
for i in $(seq 1 100); do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" http://gateway:8080/events)
  case "$CODE" in
    200) OK=$((OK+1));;
    429) LIMITED=$((LIMITED+1));;
  esac
done
echo "200=$OK 429=$LIMITED"
'
200=47 429=53
```

- The Retry-After: 1 header observed on a 429 response.
```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab11)
$ kubectl run rl-headers --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
for i in $(seq 1 50); do curl -s -o /dev/null http://gateway:8080/events; done
curl -s -D - -o /dev/null http://gateway:8080/events | grep -iE "^(HTTP|retry-after)"
'
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

- gateway_circuit_breaker_transitions_total{to} and gateway_rate_limit_rejections_total{path} from Prometheus.
```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab11)
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=gateway_circuit_breaker_transitions_total'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"__name__":"gateway_circuit_breaker_transitions_total","instance":"10.42.0.30:8080","job":"gateway","pod":"gateway-7545b4b9b8-m9xtl","rs_hash":"7545b4b9b8","to":"OPEN"},"value":[1784050194.799,"1"]},{"metric":{"__name__":"gateway_circuit_breaker_transitions_total","instance":"10.42.0.28:8080","job":"gateway","pod":"gateway-7545b4b9b8-gjwml","rs_hash":"7545b4b9b8","to":"OPEN"},"value":[1784050194.799,"1"]},{"metric":{"__name__":"gateway_circuit_breaker_transitions_total","instance":"10.42.0.27:8080","job":"gateway","pod":"gateway-7545b4b9b8-5922t","rs_hash":"7545b4b9b8","to":"OPEN"},"value":[1784050194.799,"1"]},{"metric":{"__name__":"gateway_circuit_breaker_transitions_total","instance":"10.42.0.29:8080","job":"gateway","pod":"gateway-7545b4b9b8-xptmg","rs_hash":"7545b4b9b8","to":"OPEN"},"value":[1784050194.799,"1"]},{"metric":{"__name__":"gateway_circuit_breaker_transitions_total","instance":"10.42.0.26:8080","job":"gateway","pod":"gateway-7545b4b9b8-skn2n","rs_hash":"7545b4b9b8","to":"OPEN"},"value":[1784050194.799,"1"]},{"metric":{"__name__":"gateway_circuit_breaker_transitions_total","instance":"10.42.0.28:8080","job":"gateway","pod":"gateway-7545b4b9b8-gjwml","rs_hash":"7545b4b9b8","to":"HALF_OPEN"},"value":[1784050194.799,"1"]},{"metric":{"__name__":"gateway_circuit_breaker_transitions_total","instance":"10.42.0.28:8080","job":"gateway","pod":"gateway-7545b4b9b8-gjwml","rs_hash":"7545b4b9b8","to":"CLOSED"},"value":[1784050194.799,"1"]},{"metric":{"__name__":"gateway_circuit_breaker_transitions_total","instance":"10.42.0.27:8080","job":"gateway","pod":"gateway-7545b4b9b8-5922t","rs_hash":"7545b4b9b8","to":"HALF_OPEN"},"value":[1784050194.799,"1"]},{"metric":{"__name__":"gateway_circuit_breaker_transitions_total","instance":"10.42.0.27:8080","job":"gateway","pod":"gateway-7545b4b9b8-5922t","rs_hash":"7545b4b9b8","to":"CLOSED"},"value":[1784050194.799,"1"]},{"metric":{"__name__":"gateway_circuit_breaker_transitions_total","instance":"10.42.0.30:8080","job":"gateway","pod":"gateway-7545b4b9b8-m9xtl","rs_hash":"7545b4b9b8","to":"HALF_OPEN"},"value":[1784050194.799,"1"]},{"metric":{"__name__":"gateway_circuit_breaker_transitions_total","instance":"10.42.0.30:8080","job":"gateway","pod":"gateway-7545b4b9b8-m9xtl","rs_hash":"7545b4b9b8","to":"CLOSED"},"value":[1784050194.799,"1"]},{"metric":{"__name__":"gateway_circuit_breaker_transitions_total","instance":"10.42.0.26:8080","job":"gateway","pod":"gateway-7545b4b9b8-skn2n","rs_hash":"7545b4b9b8","to":"HALF_OPEN"},"value":[1784050194.799,"1"]},{"metric":{"__name__":"gateway_circuit_breaker_transitions_total","instance":"10.42.0.26:8080","job":"gateway","pod":"gateway-7545b4b9b8-skn2n","rs_hash":"7545b4b9b8","to":"CLOSED"},"value":[1784050194.799,"1"]},{"metric":{"__name__":"gateway_circuit_breaker_transitions_total","instance":"10.42.0.29:8080","job":"gateway","pod":"gateway-7545b4b9b8-xptmg","rs_hash":"7545b4b9b8","to":"HALF_OPEN"},"value":[1784050194.799,"1"]},{"metric":{"__name__":"gateway_circuit_breaker_transitions_total","instance":"10.42.0.29:8080","job":"gateway","pod":"gateway-7545b4b9b8-xptmg","rs_hash":"7545b4b9b8","to":"CLOSED"},"value":[1784050194.799,"1"]}]}}


axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab11)
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(path)+(gateway_rate_limit_rejections_total)'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/events/{id}/reserve"},"value":[1784050200.972,"4"]},{"metric":{"path":"/events"},"value":[1784050200.972,"66"]}]}}
```
