# Lab 11 — Advanced Microservice Patterns

Completed Tasks 1 and 2 plus the bulkhead bonus (the lab rubric totals **10/10**, not 12). Experiments ran on the local `k3d-quickticket` cluster on **15 October 2026 (Europe/Moscow)**. Results below are measured; expected outcomes in the lab are not substituted for observations.

## Implementation and deployment

- [Notifications service](../app/notifications/main.py), [dependencies](../app/notifications/requirements.txt), [Dockerfile](../app/notifications/Dockerfile), and [Deployment + Service](../k8s/notifications.yaml): port 8083, one replica, local image, fault injection, health, and all three required metric families. Notification latency uses asynchronous sleep. Invalid event/order IDs return 422.
- [Gateway](../app/gateway/main.py): three total attempts by default; exponential backoff with jitter; only timeouts, connection errors, 408, 429 and 5xx are retried. Permanent errors return immediately. Breaker counts the final result of a retry sequence, permits only one half-open probe, and ignores stale in-flight results after a state transition.
- Sliding-window rate limiting uses normalized endpoint paths, excludes health/metrics, and returns 429 with `Retry-After: 1`. Metrics middleware surrounds the limiter so rejected requests also enter the HTTP counters.
- Bulkhead holds one of ten payment slots through the complete breaker/retry operation, releases slots on errors and cancellation, and returns a distinct 503 on admission timeout. Notification tasks are retained until completion; HTTP notification failures are logged without affecting checkout.
- [Gateway manifest](../k8s/gateway.yaml) exposes all tunables. [Compose](../app/docker-compose.yaml) also includes notifications for local use; the experiments here used Kubernetes.

The cluster initially had a stopped Docker node, an inactive five-replica Rollout (missing `gateway-error-rate` AnalysisTemplate), and a one-replica gateway Deployment. Started the existing cluster, installed the provided Lab 7 AnalysisTemplate, applied the Rollout, and scaled the obsolete Deployment to zero after the Rollout became healthy. Existing database/PVC data was preserved.

```sh
k3d cluster start quickticket
docker build -t quickticket-gateway:lab11-final app/gateway
docker build -t quickticket-notifications:v1 app/notifications
k3d image import -c quickticket quickticket-gateway:lab11-final quickticket-notifications:v1
kubectl apply -f labs/lab7/analysis-template.yaml -f labs/lab7/prometheus.yaml
kubectl apply -f k8s/notifications.yaml -f k8s/gateway.yaml
kubectl wait rollout/gateway --for=jsonpath='{.status.phase}'=Healthy --timeout=240s
kubectl scale deployment/gateway --replicas=0
kubectl apply -f labs/lab8/mixedload.yaml
```

The first experiment image was `quickticket-gateway:lab11`. Final review changed only middleware registration order to include 429s in HTTP metrics, then built `lab11-final` and ran regression tests in that image. Both revisions have the same resilience primitives. Evidence files retain the original observations.

## Task 1 — Notifications and retry evidence

The reproducible [in-cluster probe](../labs/lab11/probe.py) runs inside a pod containing httpx:

```sh
kubectl run lab11-probe --image=quickticket-gateway:lab11-final --image-pull-policy=Never --restart=Never --command -- sleep 7200
kubectl wait pod/lab11-probe --for=condition=Ready --timeout=60s
kubectl exec -i lab11-probe -- python - checkout < labs/lab11/probe.py
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
kubectl rollout status deployment/notifications --timeout=60s
kubectl exec -i lab11-probe -- python - checkout < labs/lab11/probe.py
kubectl exec -i lab11-probe -- python - metrics http://notifications:8083 < labs/lab11/probe.py
kubectl exec -i lab11-probe -- python - prometheus < labs/lab11/probe.py
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.0 NOTIFY_LATENCY_MS=0
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
kubectl rollout status deployment/payments --timeout=60s
kubectl exec -i lab11-probe -- python - checkout < labs/lab11/probe.py
kubectl exec -i lab11-probe -- python - prometheus < labs/lab11/probe.py
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
```

| Experiment | Observed result | Evidence |
|---|---|---|
| Initial baseline | 30/30 confirmed; client pay p99 196.38 ms | [baseline.txt](lab11-evidence/baseline.txt) |
| 30% notification failures + 300 ms latency | **30/30 confirmed; client pay p99 53.56 ms** | [notify-checkout.txt](lab11-evidence/notify-checkout.txt) |
| Notification destination metrics | success 22, failed 8; observed failure fraction **8/30 = 26.67%** | [notify-metrics.txt](lab11-evidence/notify-metrics.txt) |
| 30% payment failures | **30/30 confirmed** | [retry-checkout.txt](lab11-evidence/retry-checkout.txt) |
| Retry Prometheus counters | `retried=12`, `succeeded_after_retry=8` | [retry-prometheus.jsonl](lab11-evidence/retry-prometheus.jsonl) |

Client p99 uses nearest rank; with 30 samples this is the maximum. The first notification Prometheus 2-minute p99 was **168.66 ms**, because its window also included the slower baseline. It must not be described as an isolated <100 ms notification window. The injected 300 ms delay did not appear on the measured checkout path, and a deterministic test also proves checkout returns while the notification task is still blocked. Small sample latency differences are not a statistical claim of improvement.

The payment retry sample had a 5466.62 ms maximum. Successful retry does not imply low latency: each attempt has its own 5-second httpx timeout, and rollout/connection transients can consume it. The captured evidence does not identify the cause of that individual delay. `GATEWAY_TIMEOUT_MS` is not a total end-to-end deadline.

Mixedload ran during the main experiments, but its event 1 reservations returned 409 because the preserved inventory was exhausted. The explicit event 3 checkout probes supplied real successful reserve/pay traffic; background mixedload supplied event reads and attempted reservations.

**Why fire-and-forget?** Once payment and confirmation have succeeded, a notification failure must not turn the successful order into a user-visible error or add notification latency to checkout. This best-effort implementation can lose notifications on process death; durable delivery would require an outbox or queue.

**Why `cb.call(retry(...))`?** The breaker records one final outcome per logical payment, rather than counting each transient attempt as a separate service failure. An open breaker skips the retry operation altogether. With this implementation, `CircuitOpenError` is non-retryable even in the reversed composition; the lab's suggestion that it must always be retried is not universally true. Reversing the order still changes failure accounting and can trip the breaker prematurely.

## Task 2 — Circuit breaker and rate limiting

Eight checkout attempts were directed to **each of the five gateway pod IPs**, concurrently across pods, to avoid uneven Service load balancing. Total 40 attempts is sufficient to observe all five per-process breakers.

```sh
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0
kubectl rollout status deployment/payments --timeout=60s
# Supply each ready gateway pod's http://POD_IP:8080 as a separate argument.
kubectl exec -i lab11-probe -- python - cb URL1 URL2 URL3 URL4 URL5 < labs/lab11/probe.py
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
kubectl rollout status deployment/payments --timeout=60s
# Wait at least 30 seconds since opening before the recovery probe.
sleep 35
kubectl exec -i lab11-probe -- python - recovery URL1 URL2 URL3 URL4 URL5 < labs/lab11/probe.py
kubectl exec -i lab11-probe -- python - rate < labs/lab11/probe.py
kubectl exec -i lab11-probe -- python - rate URL1 < labs/lab11/probe.py
kubectl exec -i lab11-probe -- python - prometheus < labs/lab11/probe.py
```

| Check | Actual result |
|---|---|
| Complete payment failure | **25 HTTP 500, 15 HTTP 503**; each pod reports 5 exhausted operations and one OPEN transition |
| Recovery after cooldown | **15/15 HTTP 200** |
| Cluster-wide 100-request burst | 20 HTTP 200, 54 HTTP 429, **26 HTTP 502** |
| Directed single-pod burst | 9 HTTP 200, 91 HTTP 429; background traffic can consume a slot |
| Header on rejected requests | **`Retry-After: 1`** |
| Five requests at 5 RPS after cooldown | **5 HTTP 200** |

Evidence: [CB failure](lab11-evidence/cb-open.txt), [per-pod counters](lab11-evidence/cb-open-metrics.txt), [recovery](lab11-evidence/cb-recovery.txt), [cluster burst](lab11-evidence/rate-limit.txt), [single-pod burst](lab11-evidence/rate-limit-single-pod.txt), [Prometheus](lab11-evidence/patterns-prometheus.jsonl).

The captured Prometheus snapshot shows OPEN=5, HALF_OPEN=3, CLOSED=3 and `/events` rejection count=24. It was taken before the next scrape had collected all recent increments, so these are sampled counters rather than exact totals of the immediately preceding probes. The synchronous client observations and direct pod counters are retained separately.

The cluster burst's 502s are real: events logs reported `psycopg2.pool.PoolError: connection pool exhausted`. Five independent gateway limiters can admit roughly 50 requests at once, exceeding the existing events pool of ten. The directed single-pod test isolates limiter behavior. These experiments do not claim that per-process rate limiting supplies a global capacity guarantee.

## Bonus — Bulkhead isolation

A temporary gateway pod uses the same application image with `RATE_LIMIT_RPS=1000`, payment MAX=10 and acquisition timeout=0.5 s. Requests target its IP directly. The production Service is temporarily restricted to the healthy Rollout hash, preventing background clients from reaching the test pod. The pod retains `app=gateway` so the existing Prometheus configuration scrapes it.

The probe pre-creates 30 reservations, then starts 30 concurrent payments and samples event reads separately. Pre-reserving avoids measuring reservation admission instead of payment concurrency. This avoids two confounders in the suggested lab command: five pods provide 50 slots total, and the default 10 RPS limiter may reject requests before the bulkhead does.

```sh
# Set selector to the actual current Rollout hash, not a historical hash.
kubectl patch svc gateway --type=merge -p '{"spec":{"selector":{"rollouts-pod-template-hash":"CURRENT_HASH"}}}'
kubectl apply -f labs/lab11/bulkhead-pod.yaml
kubectl wait pod/lab11-bulkhead --for=condition=Ready --timeout=60s
kubectl set env deployment/payments PAYMENT_LATENCY_MS=3000 PAYMENT_FAILURE_RATE=0.0
kubectl rollout status deployment/payments --timeout=60s
kubectl exec -i lab11-probe -- python - bulkhead http://TEST_POD_IP:8080 < labs/lab11/probe.py
kubectl exec -i lab11-probe -- python - prometheus < labs/lab11/probe.py
# Remove and recreate the test pod for the control experiment.
kubectl delete pod lab11-bulkhead
kubectl apply -f labs/lab11/no-bulkhead-pod.yaml
kubectl wait pod/lab11-bulkhead --for=condition=Ready --timeout=60s
kubectl exec -i lab11-probe -- python - bulkhead http://NEW_TEST_POD_IP:8080 < labs/lab11/probe.py
kubectl delete pod lab11-bulkhead
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0 PAYMENT_FAILURE_RATE=0.0
kubectl patch svc gateway --type=merge -p '{"spec":{"selector":{"rollouts-pod-template-hash":null}}}'
```

| Measurement | With bulkhead | Without bulkhead |
|---|---:|---:|
| Payment outcomes | **10 HTTP 200, 20 HTTP 503** | 15 HTTP 200, 15 HTTP 500 |
| Admission rejection p99, client observed | 522.39 ms | No admission limit |
| Event-read HTTP outcomes | **30 HTTP 200** | 29 HTTP 200, 1 HTTP 502 |
| Event latency p99 | **253.66 ms** | 828.89 ms |
| `EVENTS: ok / slow` (500 ms latency threshold) | **30 / 0** | 29 / 1 |
| Peak occupied payment slots | **10** | Wrapper bypassed |

[With-bulkhead output](lab11-evidence/bulkhead-with.txt), [control output](lab11-evidence/bulkhead-without.txt), [direct metrics](lab11-evidence/bulkhead-metrics.txt), [Prometheus evidence](lab11-evidence/bulkhead-prometheus.jsonl).

Prometheus independently observed **`gateway_bulkhead_rejections_total{target="payments"}=20`** and **`max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m])=10`** for `pod="lab11-bulkhead"`.

A second attempted with-bulkhead run stopped during reservation setup with an upstream 500; it did not execute the concurrency test. The successful first run is preserved. Background mixedload was stopped before the control run, so the initial pair is illustrative rather than a perfectly controlled latency comparison. No claim of the lab's predicted `EVENTS: ok=0 slow=30` is made: asynchronous network waits yield the event loop. Resource/connection pressure and synchronized confirmations can still affect events, but 30 awaits alone do not prove event-loop starvation.

**Why bulkhead outside the breaker?** The requested `bulkhead → CB → retry → call` composition holds one admission slot for the entire logical operation, including retry backoff. Admission timeout occurs outside the breaker, so local saturation does not count as downstream failure. An open breaker briefly acquires and releases a slot; if the pool is full it can wait for admission first. `CB → bulkhead → retry` could also retain a slot across retries, but requires explicitly excluding admission failures from breaker accounting. It is inaccurate to say every alternative ordering necessarily allocates one slot per retry.

**Bulkhead versus rate limiter?** A bulkhead limits concurrent in-flight work for a dependency; a rate limiter bounds incoming arrivals per time window. Slow payments can occupy all slots even at a modest arrival rate. Both implementations here are per-process, so neither is a cluster-wide ceiling without shared coordination. The payment semaphore protects payment concurrency within the shared HTTP client; it does not create separate connection pools for every downstream.

## Review and validation

```sh
.venv/bin/python -m unittest discover -s tests -v
docker run --rm -v "$PWD:/workspace:ro" -w /workspace quickticket-gateway:lab11-final python -m unittest discover -s tests -v
docker compose -f app/docker-compose.yaml config --quiet
kubectl apply --dry-run=server -f k8s/gateway.yaml -f k8s/notifications.yaml
git diff --check
```

**12 tests pass** locally and in Python 3.13: retry classifications/backoff/exhaustion, permanent failures, breaker recovery/concurrency/stale completions, cancelled probes, bulkhead timeout/release/cancellation, limiter boundary/header/metrics, notifications failure/latency/validation, non-blocking checkout and notification-independent health. See [local test output](lab11-evidence/unit-tests.txt) and [container test output](lab11-evidence/container-tests.txt).

Review retained these scope limits: mock payments lack durable idempotency (real payment retries need it); notification delivery is best effort; httpx timeouts are per attempt; breaker/limiter state is per process; the existing events DB pool is still susceptible to overload. No production-readiness claim is made.

## Final-image verification

After the canary passed, the final image completed another baseline of 30/30 checkouts (client p99 114.08 ms) and an injected-notification run of **30/30 checkouts at p99 20.54 ms**. The subsequent repeat completed ten more checkouts before twenty reservations returned 409. Destination counters recorded **28 sent and 12 failed (30%)** across the forty confirmed orders. The final Prometheus 2-minute `/pay` p99 was **86.77 ms**, below 100 ms.

Evidence: [baseline](lab11-evidence/final-baseline.txt), [notification runs](lab11-evidence/final-notify.txt), [destination metrics](lab11-evidence/final-notify-metrics.txt), [Prometheus](lab11-evidence/final-notify-prometheus.jsonl). The 409s are preserved rather than counted as successful checkouts. Existing events code increments Redis `event:{id}:held` on reservation but does not decrement it on confirmation or expiration; repeated probes can exhaust apparent availability even when the cached event listing still shows tickets. This pre-existing inventory issue is outside the resilience implementation. No data was flushed to conceal it.

A final controlled bulkhead pair used the final image, event 5, and background mixedload stopped for **both** runs. With the bulkhead: 9 payments returned 200, 1 returned 500, and 20 were rejected with 503 (p99 521.93 ms); all 30 event reads returned 200, p99 465.36 ms, `EVENTS: ok=30 slow=0`, and directly sampled occupancy reached 10. Without the bulkhead: 18 payments returned 200 and 12 returned 500; event reads were 29 HTTP 200 and one 502, p99 954.67 ms, `EVENTS: ok=29 slow=1`. The remaining admitted-payment 500 shows that payment admission does not eliminate downstream confirmation failures.

See [final with-bulkhead](lab11-evidence/final-bulkhead-with.txt) and [final control](lab11-evidence/final-bulkhead-without.txt). The final Prometheus scrape captured 20 rejections but missed the short occupancy peak (5-second scrapes versus roughly 3-second occupancy); the earlier [Prometheus capture](lab11-evidence/bulkhead-prometheus.jsonl) independently observed MAX=10. A preceding repeat stopped at event 3 reservation setup with 409, triggering cleanup; switching the explicit probe to `env LAB11_EVENT_ID=5 python - bulkhead ...` avoided changing or deleting stored data.

[Final cluster evidence](lab11-evidence/final-cluster.txt) records a healthy five-replica Rollout, notifications ready, payment/notification fault rates and latency reset to zero, successful canary analysis, and Service selector restored to `app=gateway`. Temporary measurement pods were removed. Mixedload was restored to its lab-manifest replica count of two; the obsolete gateway Deployment remains scaled to zero.

## Implementation excerpts required by the lab

<details>
<summary>app/notifications/main.py</summary>

```python
"""QuickTicket Notifications — Mock notifier with tunable failures."""

import os
import asyncio
import time
import random
import logging

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
REQUEST_COUNT = Counter("notifications_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("notifications_request_duration_seconds", "Request duration", ["method", "path"])
NOTIFY_TOTAL = Counter("notifications_notify_total", "Total notification attempts", ["result"])


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
async def notify(body: dict):
    event, order_id = body.get("event"), body.get("order_id")
    if not isinstance(event, str) or not event or not isinstance(order_id, str) or not order_id:
        raise HTTPException(422, "event and order_id must be non-empty strings")
    if NOTIFY_LATENCY_MS > 0:
        await asyncio.sleep(NOTIFY_LATENCY_MS / 1000)
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning("Notification failed (injected) event=%s order=%s", event, order_id)
        raise HTTPException(500, "Notification delivery failed")
    NOTIFY_TOTAL.labels("success").inc()
    log.info("Notification sent event=%s order=%s", event, order_id)
    return {"status": "sent", "event": event, "order_id": order_id}
```

</details>

<details>
<summary>app/notifications/requirements.txt</summary>

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

</details>

<details>
<summary>k8s/notifications.yaml</summary>

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

</details>

<details>
<summary>Gateway resilience primitives and composition</summary>

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    """Bounded total attempts; retry only transient HTTP/transport failures."""
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")
    base_delay = RETRY_BASE_DELAY_MS / 1000
    for attempt in range(max_retries):
        try:
            result = await func()
        except Exception as exc:
            retryable = isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                retryable = 500 <= status < 600 or status in (408, 429)
            if not retryable:
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                raise
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
        else:
            if attempt:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result


class CircuitOpenError(Exception):
    """Raised by CircuitBreaker.call when the circuit is open (fast-fail)."""


class CircuitBreaker:
    """Per-process breaker with one recovery probe and stale-result protection."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    HALF_OPEN = "HALF_OPEN"

    def __init__(self, threshold: int, cooldown_s: float, name: str = "cb"):
        self.threshold = threshold
        self.cooldown = cooldown_s
        self.name = name
        self.failures = 0
        self.state = self.CLOSED
        self.opened_at = 0.0
        self.generation = 0

    def _transition(self, new_state: str):
        """Record a state change. Use this from your .call implementation
        so transitions show up in Prometheus."""
        if self.state != new_state:
            log.warning(f"circuit[{self.name}] {self.state} -> {new_state}")
            CB_STATE_TRANSITIONS.labels(new_state).inc()
            self.generation += 1
        self.state = new_state

    async def call(self, func):
        if self.state == self.OPEN:
            if time.time() - self.opened_at < self.cooldown:
                raise CircuitOpenError(f"circuit[{self.name}] OPEN")
            self._transition(self.HALF_OPEN)
        elif self.state == self.HALF_OPEN:
            raise CircuitOpenError(f"circuit[{self.name}] HALF_OPEN probe in progress")
        generation = self.generation
        try:
            result = await func()
        except asyncio.CancelledError:
            if self.state == self.HALF_OPEN and generation == self.generation:
                self.opened_at = time.time()
                self._transition(self.OPEN)
            raise
        except Exception:
            if generation == self.generation:
                self.failures += 1
                self.opened_at = time.time()
                if self.state == self.HALF_OPEN or self.failures >= self.threshold:
                    self._transition(self.OPEN)
            raise
        else:
            if generation == self.generation:
                self.failures = 0
                self._transition(self.CLOSED)
            return result


class RateLimiter:
    """Per-endpoint, per-process one-second sliding window."""

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


BULKHEAD_IN_FLIGHT = Gauge(
    "gateway_bulkhead_in_flight", "Current downstream occupants", ["target"]
)
BULKHEAD_REJECTIONS = Counter(
    "gateway_bulkhead_rejections_total", "Downstream admission timeouts", ["target"]
)


class BulkheadFullError(Exception):
    """Downstream concurrency capacity unavailable within the admission timeout."""


class Bulkhead:
    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        if max_concurrent < 1 or acquire_timeout_s <= 0:
            raise ValueError("bulkhead capacity and timeout must be positive")
        self.name = name
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.acquire_timeout_s = acquire_timeout_s

    async def call(self, func):
        try:
            await asyncio.wait_for(self.semaphore.acquire(), self.acquire_timeout_s)
        except TimeoutError as exc:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full") from exc
        try:
            BULKHEAD_IN_FLIGHT.labels(self.name).inc()
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self.semaphore.release()


payments_bulkhead = Bulkhead(
    "payments", BULKHEAD_PAYMENTS_MAX, BULKHEAD_PAYMENTS_TIMEOUT_S
)

# In pay_reservation:
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
)
```

</details>
