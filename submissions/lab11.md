# Lab 11 — Advanced Microservice Patterns

## Environment and method

QuickTicket runs on the existing single-node k3d cluster on macOS Apple Silicon.
Gateway remains an Argo Rollouts Rollout with five replicas. Events, Payments,
Redis and PostgreSQL retain their previous configuration; PostgreSQL retains
its Lab 9 PVC and Alembic revision `9a0000000002`.

This lab adds Notifications, implements retry, circuit breaker and per-endpoint
rate limiting, and adds a payment bulkhead. State and concurrency budgets are
local to each gateway process.

All HTTP experiments ran from `lab11-probe` inside the cluster. Task 1 and
the main CB/rate-limit tests used `http://gateway:8080`. The bulkhead saturation
test deliberately targeted one gateway pod IP: 30 concurrent calls would not
necessarily fill five independent pools with 10 slots each.

ArgoCD auto-sync was disabled before applying experimental changes. Fault
settings were restored after each experiment. Temporary rate-limit/bulkhead
settings were restored after the comparison; the committed defaults are
10 RPS, bulkhead enabled, 10 occupants, and a 0.5-second acquisition timeout.

Local images: `quickticket-notifications:v1` and
`quickticket-gateway:lab11-v1`, built and imported into k3d. Gateway pods use
`imagePullPolicy: Never`. These images must be rebuilt/imported for another
cluster; the manifests do not publish images to a registry.

### Build and rollout procedure

```bash
docker build -t quickticket-notifications:v1 ./app/notifications
docker build -t quickticket-gateway:lab11-v1 ./app/gateway
k3d image import -c quickticket quickticket-notifications:v1 quickticket-gateway:lab11-v1
kubectl --context k3d-quickticket -n default apply -f k8s/notifications.yaml
kubectl --context k3d-quickticket -n default apply -f k8s/gateway.yaml
kubectl --context k3d-quickticket -n default get rollout gateway
kubectl --context k3d-quickticket -n default get analysisrun
```

The rollout completed its configured canary steps and AnalysisRun
`gateway-b85cb7c8-9-2` was Successful. Read-only in-cluster traffic supplied
samples for analysis. All five Ready gateway pods were verified against the
SHA-256 of the local `app/gateway/main.py`.

```json
[
  {
    "pod": "gateway-b85cb7c8-7pdct",
    "ready": true,
    "reported_image": "docker.io/library/quickticket-gateway:lab11-v1",
    "image_id": "sha256:066e3a8e0827840faa55bbd03bca5529c5ebc6cf81353dfa2b3c980061977832",
    "code_sha256": "70fdcef5a13ef58db78570e318f1af45983c338c20528fc4b04bd19c6a17c244",
    "matches_local_code": true
  },
  {
    "pod": "gateway-b85cb7c8-hv6gn",
    "ready": true,
    "reported_image": "docker.io/library/quickticket-gateway:lab11-v1",
    "image_id": "sha256:066e3a8e0827840faa55bbd03bca5529c5ebc6cf81353dfa2b3c980061977832",
    "code_sha256": "70fdcef5a13ef58db78570e318f1af45983c338c20528fc4b04bd19c6a17c244",
    "matches_local_code": true
  },
  {
    "pod": "gateway-b85cb7c8-mpp22",
    "ready": true,
    "reported_image": "docker.io/library/quickticket-gateway:lab11-v1",
    "image_id": "sha256:066e3a8e0827840faa55bbd03bca5529c5ebc6cf81353dfa2b3c980061977832",
    "code_sha256": "70fdcef5a13ef58db78570e318f1af45983c338c20528fc4b04bd19c6a17c244",
    "matches_local_code": true
  },
  {
    "pod": "gateway-b85cb7c8-vmtvr",
    "ready": true,
    "reported_image": "docker.io/library/quickticket-gateway:lab11-v1",
    "image_id": "sha256:066e3a8e0827840faa55bbd03bca5529c5ebc6cf81353dfa2b3c980061977832",
    "code_sha256": "70fdcef5a13ef58db78570e318f1af45983c338c20528fc4b04bd19c6a17c244",
    "matches_local_code": true
  },
  {
    "pod": "gateway-b85cb7c8-w8fcb",
    "ready": true,
    "reported_image": "docker.io/library/quickticket-gateway:lab11-v1",
    "image_id": "sha256:066e3a8e0827840faa55bbd03bca5529c5ebc6cf81353dfa2b3c980061977832",
    "code_sha256": "70fdcef5a13ef58db78570e318f1af45983c338c20528fc4b04bd19c6a17c244",
    "matches_local_code": true
  }
]
```

## Task 1 — Notifications and retries

### Notifications implementation

```python
"""QuickTicket Notifications — best-effort delivery with fault injection."""

import asyncio
import logging
import os
import random
import time

from fastapi import FastAPI, HTTPException, Request
from prometheus_client import (
    Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST,
)
from pydantic import BaseModel
from starlette.responses import Response

NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

if not 0 <= NOTIFY_FAILURE_RATE <= 1 or NOTIFY_LATENCY_MS < 0:
    raise ValueError("Invalid notification fault-injection settings")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("notifications")
app = FastAPI(title="QuickTicket Notifications", version="1.0.0")

REQUEST_COUNT = Counter(
    "notifications_requests_total", "Total requests",
    ["method", "path", "status"],
)
REQUEST_DURATION = Histogram(
    "notifications_request_duration_seconds", "Request duration",
    ["method", "path"],
)
NOTIFY_TOTAL = Counter(
    "notifications_notify_total", "Notification outcomes", ["result"],
)


class Notification(BaseModel):
    event: str
    order_id: str


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    if request.url.path != "/metrics":
        # Only fixed routes become labels; unknown URLs share one label.
        path = request.url.path
        if path not in ("/notify", "/health"):
            path = "/other"
        REQUEST_COUNT.labels(
            request.method, path, str(response.status_code)
        ).inc()
        REQUEST_DURATION.labels(request.method, path).observe(
            time.perf_counter() - start
        )
    return response


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "failure_rate": NOTIFY_FAILURE_RATE,
        "latency_ms": NOTIFY_LATENCY_MS,
    }


@app.get("/metrics")
async def metrics():
    return Response(
        content=generate_latest(), media_type=CONTENT_TYPE_LATEST
    )


@app.post("/notify")
async def notify(body: Notification):
    if NOTIFY_LATENCY_MS:
        await asyncio.sleep(NOTIFY_LATENCY_MS / 1000)
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning(
            "Notification failed (injected): event=%s order=%s",
            body.event, body.order_id,
        )
        raise HTTPException(500, "Notification delivery failed")
    NOTIFY_TOTAL.labels("success").inc()
    log.info("Notification sent: event=%s order=%s", body.event, body.order_id)
    return {
        "status": "sent", "event": body.event, "order_id": body.order_id,
    }
```

### Notifications dependencies

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

### Notifications Dockerfile

```dockerfile
FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .

EXPOSE 8083
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8083"]
```

### Notifications Deployment and Service

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
            - name: http
              containerPort: 8083
          env:
            - name: NOTIFY_FAILURE_RATE
              value: "0.0"
            - name: NOTIFY_LATENCY_MS
              value: "0"
          readinessProbe:
            httpGet:
              path: /health
              port: http
            initialDelaySeconds: 5
            periodSeconds: 5
          livenessProbe:
            httpGet:
              path: /health
              port: http
            initialDelaySeconds: 10
            periodSeconds: 10
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
    - name: http
      port: 8083
      targetPort: http
```

### Fire-and-forget gateway wiring

```python
async def _notify_order_confirmed(reservation_id: str):
    """Fire-and-forget notification; failure MUST NOT break the user flow.

    No-op when NOTIFICATIONS_URL is unset (labs 1-10). When configured (Lab 11+)
    POSTs to /notify and swallows errors with a warning.
    """
    if not NOTIFICATIONS_URL:
        return
    try:
        response = await client.post(
            f"{NOTIFICATIONS_URL}/notify",
            json={"event": "order_confirmed", "order_id": reservation_id},
            timeout=2.0,
        )
        response.raise_for_status()
    except Exception as e:
        log.warning(f"notify failed (non-critical) order={reservation_id} err={e}")
```

After Events confirms the reservation, `/pay` schedules
`asyncio.create_task(_notify_order_confirmed(reservation_id))` and returns.
The helper has a two-second HTTP timeout and logs both transport failures
and non-success HTTP responses. Notifications status is reported by `/health`,
but only Events and Payments determine its critical health verdict.

### Retry implementation

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    """Retry transient errors with exponential backoff and jitter."""
    if max_retries < 1:
        raise ValueError("max_retries means total attempts and must be >= 1")
    base_delay = RETRY_BASE_DELAY_MS / 1000
    for attempt in range(max_retries):
        try:
            result = await func()
        except Exception as exc:
            retryable = isinstance(
                exc, (httpx.TimeoutException, httpx.ConnectError)
            )
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
            if attempt > 0:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result
```

`RETRY_MAX=3` means three total attempts. Transient failures are HTTP 5xx,
408, 429, `httpx.TimeoutException` and `httpx.ConnectError`. Other exceptions
fail immediately. Backoff is `base * 2**attempt + uniform(0, base)`, with
base 100 ms. Retry sleeps are asynchronous; cancellation is not swallowed.

### Test #1 — Notification failures do not fail checkout

| Phase | Notify failure setting | Notify latency | Checkout | Client /pay p99 | Prometheus /pay p99 |
|---|---:|---:|---:|---:|---:|
| Baseline | 0% | 0 ms | 30/30 | 83.431 ms | 72.688 ms |
| Injection | 30% | 300 ms | 30/30 | 87.082 ms | 69.464 ms |

Mixedload ran throughout both phases. Each phase lasted at least 70 seconds,
and the final Prometheus query used a 60-second window within that phase.
Client p99 uses nearest rank over 30 measurements; at this sample size it is
the maximum observed latency. Prometheus p99 is a histogram estimate.
The injection-phase /pay p99 remained below 100 ms.

```promql
histogram_quantile(0.99, sum by (le) (rate(gateway_request_duration_seconds_bucket{path="/reserve/{id}/pay"}[60s])))
```

### Real notification outcomes

```json
{
  "before": {
    "success": 1.0,
    "failed": 0
  },
  "after": {
    "success": 294.0,
    "failed": 134.0
  },
  "delta": {
    "success": 293.0,
    "failed": 134.0
  },
  "observed_failure_percent": 31.381733021077284,
  "scope": "Notification counters include mixedload and the 30-chain burst."
}
```

```text
notifications_notify_total{result="success"} 294.0
notifications_notify_total{result="failed"} 134.0
```

The measured delta was 134 failed notifications out of 427 attempts,
31.38%. This includes mixedload and the checkout burst. It is a stochastic
sample, not a guarantee that every run equals the configured 30%.

### Test #2 — Transient payment failures trigger retries

Checkout probe: **ok=30 fail=0** under `PAYMENT_FAILURE_RATE=0.3`, with zero injected payment latency.

| Retry result | Counter increase |
|---|---:|
| exhausted | 8 |
| retried | 146 |
| succeeded_after_retry | 107 |

Both `retried` and `succeeded_after_retry` increased. Eight logical requests
exhausted all attempts in the overall mixedload/test window; the 30-chain
probe itself had no failures. Three independent 30%-failure attempts have
a theoretical all-failed probability of 2.7%, but real workload samples
need not match that exact fraction.

```promql
sum by (target,result) (gateway_retry_total{rs_hash="b85cb7c8"})
```

```json
{
  "status": "success",
  "data": {
    "resultType": "vector",
    "result": [
      {
        "metric": {
          "result": "retried",
          "target": "payments"
        },
        "value": [
          1791634626.369,
          "172"
        ]
      },
      {
        "metric": {
          "result": "succeeded_after_retry",
          "target": "payments"
        },
        "value": [
          1791634626.369,
          "122"
        ]
      },
      {
        "metric": {
          "result": "exhausted",
          "target": "payments"
        },
        "value": [
          1791634626.369,
          "11"
        ]
      }
    ]
  }
}
```

```json
{
  "success": 370.0,
  "failed": 151.0
}
```

The first runner invocation stopped on a read-only `/health` connection
refusal immediately after the Payments rollout, before the checkout probe.
Its `finally` block restored Payments. The successful invocation added
bounded availability retries only to read-only control/metrics GETs.
Measured checkout requests had no added client-side retry wrapper.

### Design prompts

**Why should notifications be non-blocking?** Order confirmation is critical;
notification delivery is a best-effort side effect. Waiting for a slow or
failed notification would add latency or fail an already confirmed checkout.
Destination metrics make delivery failures observable. In-memory tasks can
be lost on process death; durable delivery would require an outbox/queue
and an idempotent consumer.

**Why `cb.call(retry(...))`?** The breaker counts the final outcome of one
logical payment request, after its internal attempts. An OPEN breaker rejects
before entering retry. With three attempts and threshold five, up to 15 failed
downstream attempts can precede opening one process's breaker.

Reversing the wrappers counts each failed attempt against the breaker, which
changes the threshold semantics. The scaffold's claim that the reverse
necessarily retries `CircuitOpenError` is too broad: this implementation
classifies `CircuitOpenError` as non-retryable and immediately propagates it.

**Payment safety limitation.** The mock `/charge` service has no durable
idempotency guarantee. In a real payment system, a timeout after a successful
charge makes a retry unsafe without provider-side deduplication using a stable
idempotency key. Retry resilience does not solve duplicate charges or a
payment-success/confirmation-failure transaction gap.

## Task 2 — Circuit breaker and rate limiter

### Circuit breaker implementation

```python
class CircuitBreaker:
    """Per-process CLOSED/OPEN/HALF_OPEN circuit breaker."""

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

    def _transition(self, new_state: str):
        """Record a state change. Use this from your .call implementation
        so transitions show up in Prometheus."""
        if self.state != new_state:
            log.warning(f"circuit[{self.name}] {self.state} -> {new_state}")
            CB_STATE_TRANSITIONS.labels(new_state).inc()
        self.state = new_state

    async def call(self, func):
        """Apply the configured resilience policy."""
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
        else:
            self.failures = 0
            self._transition(self.CLOSED)
            return result
```

The implementation follows the lab state-machine contract. Success clears
the failure count; OPEN calls fast-fail until cooldown; a failed HALF_OPEN
call reopens the circuit. This simple implementation does not serialize
concurrent HALF_OPEN probes or fully isolate overlapping-call state changes.
Those would need additional coordination for production use.

### Circuit breaker failure and recovery results

```json
{
  "failure_pay_status_counts": {
    "500": 25,
    "503": 55
  },
  "failure_reserve_status_counts": {
    "200": 80
  },
  "pod_states_under_failure": [
    {
      "pod": "gateway-b85cb7c8-7pdct",
      "health": {
        "status": "healthy",
        "checks": {
          "events": "ok",
          "payments": "ok",
          "notifications": "ok",
          "circuit_payments": "OPEN"
        }
      }
    },
    {
      "pod": "gateway-b85cb7c8-hv6gn",
      "health": {
        "status": "healthy",
        "checks": {
          "events": "ok",
          "payments": "ok",
          "notifications": "ok",
          "circuit_payments": "OPEN"
        }
      }
    },
    {
      "pod": "gateway-b85cb7c8-mpp22",
      "health": {
        "status": "healthy",
        "checks": {
          "events": "ok",
          "payments": "ok",
          "notifications": "ok",
          "circuit_payments": "OPEN"
        }
      }
    },
    {
      "pod": "gateway-b85cb7c8-vmtvr",
      "health": {
        "status": "healthy",
        "checks": {
          "events": "ok",
          "payments": "ok",
          "notifications": "ok",
          "circuit_payments": "OPEN"
        }
      }
    },
    {
      "pod": "gateway-b85cb7c8-w8fcb",
      "health": {
        "status": "healthy",
        "checks": {
          "events": "ok",
          "payments": "ok",
          "notifications": "ok",
          "circuit_payments": "OPEN"
        }
      }
    }
  ],
  "recovery_pay_status_counts": {
    "200": 15
  },
  "pod_states_after_recovery": [
    {
      "pod": "gateway-b85cb7c8-7pdct",
      "health": {
        "status": "healthy",
        "checks": {
          "events": "ok",
          "payments": "ok",
          "notifications": "ok",
          "circuit_payments": "CLOSED"
        }
      }
    },
    {
      "pod": "gateway-b85cb7c8-hv6gn",
      "health": {
        "status": "healthy",
        "checks": {
          "events": "ok",
          "payments": "ok",
          "notifications": "ok",
          "circuit_payments": "CLOSED"
        }
      }
    },
    {
      "pod": "gateway-b85cb7c8-mpp22",
      "health": {
        "status": "healthy",
        "checks": {
          "events": "ok",
          "payments": "ok",
          "notifications": "ok",
          "circuit_payments": "CLOSED"
        }
      }
    },
    {
      "pod": "gateway-b85cb7c8-vmtvr",
      "health": {
        "status": "healthy",
        "checks": {
          "events": "ok",
          "payments": "ok",
          "notifications": "ok",
          "circuit_payments": "CLOSED"
        }
      }
    },
    {
      "pod": "gateway-b85cb7c8-w8fcb",
      "health": {
        "status": "healthy",
        "checks": {
          "events": "ok",
          "payments": "ok",
          "notifications": "ok",
          "circuit_payments": "CLOSED"
        }
      }
    }
  ]
}
```

Background mixedload was stopped to isolate these counts. Under 100% payment
failure, 80 reservations succeeded; payments returned 25×500 and 55×503.
All five circuits were observed OPEN. After restoring Payments and waiting
35 seconds, the recovery probe returned 15×200 and all five circuits CLOSED.

```promql
sum by (to) (gateway_circuit_breaker_transitions_total{rs_hash="b85cb7c8"})
```

```json
{
  "status": "success",
  "data": {
    "resultType": "vector",
    "result": [
      {
        "metric": {
          "to": "OPEN"
        },
        "value": [
          1791634869.178,
          "5"
        ]
      },
      {
        "metric": {
          "to": "HALF_OPEN"
        },
        "value": [
          1791634869.178,
          "5"
        ]
      },
      {
        "metric": {
          "to": "CLOSED"
        },
        "value": [
          1791634869.178,
          "5"
        ]
      }
    ]
  }
}
```

### Rate limiter implementation

```python
class RateLimiter:
    """Per-key, per-process one-second sliding-window limiter."""

    def __init__(self, rps: int):
        self.rps = rps
        self.window_s = 1.0
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """Apply the configured resilience policy."""
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

The middleware normalizes IDs in endpoint keys, exempts `/health` and
`/metrics`, returns HTTP 429 with `Retry-After: 1`, and increments the dedicated
rejection counter. Each process has its own one-second window.

| Test | Requests | Duration / interval | HTTP 200 | HTTP 429 |
|---|---:|---|---:|---:|
| Service burst | 100 | 0.849 s | 50 | 50 |
| One-pod burst | 20 | 0.282 s | 10 | 10 |
| Below-limit traffic | 30 | 0.2 s between requests | 30 | 0 |

Observed first Service-burst 429 response:

```json
{
  "status": 429,
  "headers": {
    "date": "Sat, 10 Oct 2026 12:22:41 GMT",
    "server": "uvicorn",
    "retry-after": "1",
    "content-length": "56",
    "content-type": "application/json",
    "connection": "close"
  },
  "body": {
    "error": "rate_limited",
    "path": "/events",
    "limit_rps": 10
  }
}
```

```promql
sum by (path) (gateway_rate_limit_rejections_total{rs_hash="b85cb7c8"})
```

```json
{
  "status": "success",
  "data": {
    "resultType": "vector",
    "result": [
      {
        "metric": {
          "path": "/events"
        },
        "value": [
          1791634979.079,
          "60"
        ]
      }
    ]
  }
}
```

The Service burst admitted exactly 50 requests, consistent with five
independent 10-RPS budgets in this sample. This is not a guaranteed shared
cluster-wide limit: connection distribution and keep-alive affect it.
The dedicated rejection counter includes 60 rejections across both bursts.

## Bonus — Bulkhead isolation

### Implementation and composition

```python
class BulkheadFullError(Exception):
    """Dependency concurrency budget could not be acquired in time."""
```

```python
class Bulkhead:
    """Bound concurrency for a whole logical call, including its retries."""

    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        if max_concurrent < 1 or acquire_timeout_s <= 0:
            raise ValueError("Invalid bulkhead configuration")
        self.name = name
        self.acquire_timeout_s = acquire_timeout_s
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.occupancy = BULKHEAD_IN_FLIGHT.labels(name)
        self.rejections = BULKHEAD_REJECTIONS.labels(name)

    async def call(self, func):
        try:
            await asyncio.wait_for(
                self.semaphore.acquire(), timeout=self.acquire_timeout_s
            )
        except asyncio.TimeoutError as exc:
            self.rejections.inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full") from exc
        self.occupancy.inc()
        try:
            return await func()
        finally:
            self.occupancy.dec()
            self.semaphore.release()
```

```python
    try:
        async def protected_charge():
            return await payments_cb.call(
                lambda: call_with_retry(_charge, target="payments")
            )

        if BULKHEAD_PAYMENTS_ENABLED:
            pay_resp = await payments_bulkhead.call(protected_charge)
        else:
            pay_resp = await protected_charge()
        payment_ref = pay_resp.json().get("payment_ref", "unknown")
    except BulkheadFullError:
        log.warning("payments bulkhead full")
        raise HTTPException(
            503, "Payment service temporarily unavailable (bulkhead full)"
        )
    except CircuitOpenError:
        log.error("circuit open, skipping payments call")
        raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
```

A semaphore bounds a whole logical payment call, including CB evaluation,
all retry attempts and backoff. Acquisition waits at most 0.5 seconds.
A `finally` block decrements occupancy and releases the slot on success,
failure or cancellation. Local acquisition failures return a clear HTTP 503.
The comparison switch bypasses this wrapper; final configuration enables it.

### Controlled experiment

Both variants retained five gateway replicas, selected one gateway process,
temporarily used `RATE_LIMIT_RPS=1000`, and injected 3000 ms payment latency
with zero configured payment failures. Thirty reservations were prepared
sequentially before each wave, then 30 payments started concurrently.
A separate HTTP client sampled `/events` 30 times during payment load.

The same shared HTTP client configuration remained in the gateway. This
extension bounds Payments entry; it does not add independent HTTP connection
pools or concurrency caps for every other dependency.

| Variant | Wave | /pay 200 | /pay 503 | /pay 500 | /events fast OK | Slow >500 ms | Errors | /events p99 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| protected | 1 | 10 | 20 | 0 | 30 | 0 | 0 | 139.839 ms |
| protected | 2 | 10 | 20 | 0 | 30 | 0 | 0 | 97.710 ms |
| protected | 3 | 10 | 20 | 0 | 30 | 0 | 0 | 243.498 ms |
| unprotected | 1 | 29 | 0 | 1 | 29 | 0 | 1 | 290.685 ms |
| unprotected | 2 | 24 | 0 | 6 | 29 | 1 | 0 | 514.296 ms |

Baseline /events p99: protected 220.597 ms; unprotected 127.623 ms.

Protected waves each returned `EVENTS: ok=30 slow=0 errors=0`.
Unprotected wave 1 returned `EVENTS: ok=29 slow=0 errors=1`; wave 2 returned
`EVENTS: ok=29 slow=1 errors=0`. Seven unprotected checkout responses were
500 with `Payment succeeded but confirmation failed — contact support`.
Events logs contained `psycopg2.pool.PoolError: connection pool exhausted`
during that interval, consistent with pressure on its ten-connection pool.

The unprotected third wave stopped before payment load because its first
pre-reservation returned 409 with available inventory zero. It is excluded
from the completed-wave table. Later, 312 tickets were available again;
only 188 event-3 tickets were sold in the final DB sample. The 409 therefore
does not demonstrate that all 500 tickets were permanently sold.

The lab's predicted >2-second /events latency without the bulkhead was not
observed. Awaiting asynchronous I/O does not itself block the event loop.
The observed contrast is bounded payment concurrency and less downstream
pressure, not a controlled proof of event-loop starvation. Different pods
and sequential experiment timing also limit performance comparisons.

### Bulkhead metrics and rejection latency

```promql
gateway_bulkhead_rejections_total{target="payments",pod="gateway-84b6f9548b-5j55n"}
```

```json
{
  "status": "success",
  "data": {
    "resultType": "vector",
    "result": [
      {
        "metric": {
          "__name__": "gateway_bulkhead_rejections_total",
          "instance": "10.42.0.183:8080",
          "job": "gateway",
          "pod": "gateway-84b6f9548b-5j55n",
          "rs_hash": "84b6f9548b",
          "target": "payments"
        },
        "value": [
          1791635441.072,
          "60"
        ]
      }
    ]
  }
}
```

```promql
max_over_time(gateway_bulkhead_in_flight{target="payments",pod="gateway-84b6f9548b-5j55n"}[2m])
```

```json
{
  "status": "success",
  "data": {
    "resultType": "vector",
    "result": [
      {
        "metric": {
          "instance": "10.42.0.183:8080",
          "job": "gateway",
          "pod": "gateway-84b6f9548b-5j55n",
          "rs_hash": "84b6f9548b",
          "target": "payments"
        },
        "value": [
          1791635441.373,
          "10"
        ]
      }
    ]
  }
}
```

Prometheus recorded 60 acquisition rejections and maximum occupancy 10. Direct samples also reached 10 in every protected wave. Client rejection p99 values were 623.087 ms, 559.963 ms, 619.785 ms. These exceed the 500-ms acquisition timeout because measured end-to-end latency includes scheduling, response processing and network overhead. No strict end-to-end 500-ms guarantee is claimed.

The disabled variant's occupancy gauge was zero because it bypassed the
instrumented bulkhead. It does not mean no payment requests were in flight.
Its final Prometheus query was not collected because the inventory error
interrupted wave 3; the completed waves and their direct samples were saved.

### Bulkhead design prompts

**Why wrap the circuit breaker?** The chosen chain is
bulkhead → CB → retry → call. Admission applies to one logical request,
so retries and their delays retain one slot. An admission timeout occurs
outside the CB, preventing local capacity rejection from being counted as
a payment-service failure. An OPEN CB can briefly acquire a slot and then
release it through fast-fail; it does not occupy that slot for the downstream
timeout.

Putting the CB outside a bulkhead that still wraps the entire retry loop
could also preserve the concurrency bound, but the simple CB would then
count `BulkheadFullError` as a dependency failure. Wrapper order is a policy
choice; retry attempts should not independently bypass the intended bound.

**Bulkhead versus rate limiter?** Rate limiting bounds arrivals per key over
time; bulkheads bound concurrent work against a dependency. Ten slow calls
can fill a bulkhead even at low RPS. Many fast calls can hit a rate limit
without filling a bulkhead. Both implementations here are per-process;
neither provides a shared cluster-wide ceiling.

## Automated validation

`tests/test_lab11_resilience.py` contains 16 deterministic tests, all passing.
They cover retry classification, attempt counts, jitter/backoff, final
exhaustion, CB state transitions and composition, sliding-window expiry,
normalized keys, bulkhead cap/reuse/cancellation/exception cleanup, and
Notifications responses and metric exposition. Unit tests supplement the
real in-cluster experiments rather than replacing them.

```bash
.venv-lab11/bin/python -m unittest discover \
  -s tests -p 'test_lab11_resilience.py' -v
```

```text

----------------------------------------------------------------------
Ran 16 tests in 0.072s

OK
```

## Cleanup and final state

```json
{
  "timestamp": "2026-10-10T12:40:48.811610+00:00",
  "health": {
    "status": "healthy",
    "checks": {
      "events": "ok",
      "payments": "ok",
      "notifications": "ok",
      "circuit_payments": "CLOSED"
    }
  },
  "events": [
    {
      "id": 1,
      "name": "Go Conference 2026",
      "venue": "Main Hall A",
      "date": "2026-09-15T09:00:00+00:00",
      "total_tickets": 1000000,
      "price_cents": 5000,
      "available": 984931
    },
    {
      "id": 4,
      "name": "Python Workshop",
      "venue": "Lab 301",
      "date": "2026-09-22T14:00:00+00:00",
      "total_tickets": 25,
      "price_cents": 2000,
      "available": 25
    },
    {
      "id": 2,
      "name": "SRE Meetup",
      "venue": "Room 204",
      "date": "2026-10-01T18:00:00+00:00",
      "total_tickets": 30,
      "price_cents": 0,
      "available": 30
    },
    {
      "id": 5,
      "name": "Kubernetes Deep Dive",
      "venue": "Auditorium B",
      "date": "2026-10-10T10:00:00+00:00",
      "total_tickets": 80,
      "price_cents": 8000,
      "available": 80
    },
    {
      "id": 3,
      "name": "Cloud Native Summit",
      "venue": "Expo Center",
      "date": "2026-11-20T10:00:00+00:00",
      "total_tickets": 500,
      "price_cents": 15000,
      "available": 312
    }
  ],
  "gateway_phase": "Healthy",
  "gateway_ready": 5,
  "rate_limit_rps": "10",
  "bulkhead_enabled": "true",
  "fault_settings_restored": true,
  "argocd_auto_sync": "disabled pending push and source handoff",
  "inventory_note": "Real experiment orders retained; no inventory reset."
}
```

```text
 event_id | orders | sold
----------+--------+-------
        1 |  15069 | 15069
        3 |    188 |   188
(2 rows)

 version_num
--------------
 9a0000000002
(1 row)
```

Mixedload and the probe pod were removed. Payment and notification fault
settings are zero. Gateway is Healthy with five replicas, rate limit 10,
and bulkhead enabled. Notifications remains deployed.

Existing and experiment orders were retained. PostgreSQL PVC remains Bound
and the migration revision is unchanged. Redis was not flushed during
cleanup; reservation holds follow their normal expiration.

At the recorded cleanup snapshot, ArgoCD still followed Lab 9 with auto-sync
disabled. After publication, the handoff points it at the complete Lab 11
`k8s/` directory and restores the original automated-sync policy.
The copied baseline manifests preserve the PostgreSQL PVC, existing services,
and gateway analysis configuration. No backup CronJob is added by this lab.
