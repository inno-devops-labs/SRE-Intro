# Lab 11 — Advanced Microservice Patterns

## Overview

This lab added the `notifications` microservice and implemented resilience patterns in the QuickTicket gateway:

- retry with exponential backoff and jitter
- circuit breaker
- sliding-window rate limiter
- bulkhead isolation

All Task 1, Task 2, and Bonus Task requirements were implemented and tested on the k3d cluster.

## Task 1 — Notifications Service and Retries

### Notifications service

The notifications service runs on port 8083 and provides:

- POST /notify
- GET /health
- GET /metrics
- configurable notification failure rate
- configurable artificial latency

The service exposes these Prometheus metrics:

    notifications_requests_total{method,path,status}
    notifications_request_duration_seconds{method,path}
    notifications_notify_total{result}

The Kubernetes deployment uses one replica with:

    NOTIFY_FAILURE_RATE=0.0
    NOTIFY_LATENCY_MS=0

The gateway was configured with:

    NOTIFICATIONS_URL=http://notifications:8083

Gateway health confirmed the new dependency:

    status: healthy
    events: ok
    payments: ok
    notifications: ok
    circuit_payments: CLOSED

### Fire-and-forget test

Notification fault injection:

    NOTIFY_FAILURE_RATE=0.3
    NOTIFY_LATENCY_MS=300

Thirty checkout chains were executed.

Result:

    ok=30
    fail=0

Gateway /pay p99 during the test was:

    0.04275696 seconds
    approximately 42.8 ms

This stayed well below 100 ms despite notifications having 300 ms of injected latency.

Notification metrics showed:

    notifications_notify_total{result="success"} 26
    notifications_notify_total{result="failed"} 14

Observed notification failure fraction:

    14 / 40 = 35%

This demonstrates that notification latency and failures did not affect the user-facing checkout path.

### Why should notifications be non-blocking?

Notifications are a non-critical side effect of a completed transaction. A successful payment and order confirmation should not fail merely because an email, push notification, or other notification destination is unavailable.

Fire-and-forget behavior keeps notification work outside the critical request path and prevents a non-critical dependency from reducing checkout availability.

### Retry implementation

The gateway retry implementation:

- retries transient HTTP/network failures
- retries HTTP 5xx, 408, and 429
- does not retry other 4xx errors
- uses exponential backoff
- adds random jitter
- records Prometheus retry outcomes

The delay is calculated as:

    base_delay * (2 ** attempt) + random.uniform(0, base_delay)

### Retry fault-injection test

Injected payment failure:

    PAYMENT_FAILURE_RATE=0.3

Thirty checkout attempts produced:

    ok=29
    fail=1

Prometheus recorded:

    gateway_retry_total{target="payments",result="retried"} 7
    gateway_retry_total{target="payments",result="succeeded_after_retry"} 5
    gateway_retry_total{target="payments",result="exhausted"} 1

This demonstrates that retries executed and successfully recovered several transient failures.

### Why is cb.call(retry(...)) the correct composition?

The circuit breaker should observe the final result of one logical payment operation after its internal retry attempts.

The intended composition is:

    circuit breaker
        retry
            downstream call

If retry wrapped the circuit breaker instead, retry logic could repeatedly invoke an already-open circuit. That conflicts with the circuit breaker's fast-fail purpose.

## Task 2 — Circuit Breaker and Rate Limiter

### Circuit breaker

The circuit breaker implements:

    CLOSED -> OPEN -> HALF_OPEN -> CLOSED

When OPEN, calls fail immediately until the cooldown expires.

After the cooldown, the circuit enters HALF_OPEN and allows a test request. A successful request returns it to CLOSED.

### Circuit breaker failure test

Injected:

    PAYMENT_FAILURE_RATE=1.0

The test generated approximately 80 checkout attempts.

Results:

    500s=25
    503s=53
    other=0

The 500 responses were payment calls that exhausted their retries before the circuit opened.

The 503 responses were circuit-breaker fast failures after the circuit entered OPEN.

Prometheus recorded five OPEN transitions:

    gateway_circuit_breaker_transitions_total{to="OPEN"} 5

This matches the five gateway replicas, because each process has its own in-memory circuit breaker.

After restoring:

    PAYMENT_FAILURE_RATE=0.0

and waiting longer than the 30-second cooldown, 15 recovery requests produced:

    15 x HTTP 200

Prometheus subsequently showed OPEN, HALF_OPEN, and CLOSED transitions.

This proves that the breakers opened under failure and recovered after the downstream service recovered.

### Rate limiter

The gateway implements a one-second sliding-window rate limiter per normalized endpoint.

Configuration:

    RATE_LIMIT_RPS=10

Because there are five gateway replicas and state is local to each process, the approximate cluster-wide ceiling is 50 requests per second.

### Burst test

100 rapid /events requests produced:

    200=50
    429=50
    other=0

A rejected request included:

    HTTP/1.1 429 Too Many Requests
    retry-after: 1

Prometheus recorded:

    gateway_rate_limit_rejections_total{path="/events"} 50

### Sustained traffic test

Thirty requests were sent with a 0.2 second pause.

Result:

    200=30
    429=0
    other=0

This demonstrates that burst traffic is limited while sustained below-limit traffic remains unaffected.

## Bonus Task — Bulkhead Isolation

### Implementation

The payment bulkhead was configured with:

    BULKHEAD_PAYMENTS_MAX=10
    BULKHEAD_PAYMENTS_TIMEOUT_S=0.5

It uses an asyncio.Semaphore to limit concurrent payment operations.

The resilience composition is:

    bulkhead
        circuit breaker
            retry
                payment call

The following Prometheus metrics were added:

    gateway_bulkhead_in_flight{target}
    gateway_bulkhead_rejections_total{target}

### Without-bulkhead observation

Before enabling the bulkhead, payment latency was set to:

    PAYMENT_LATENCY_MS=3000

The observed result was:

    WITHOUT BULKHEAD: EVENTS ok=30 slow=0

The test environment therefore did not reproduce the greater-than-two-second /events slowdown predicted by the lab.

The gateway uses asynchronous HTTP calls, so slow payment requests did not fully block unrelated event requests in this particular run.

The real observed result is reported rather than replacing it with the expected result.

### With-bulkhead experiment

The test was directed to a single gateway process so all payment calls shared the same in-process semaphore.

Thirty reservations were created before payment requests were launched concurrently.

Result:

    Reservations created: 30

    PAYMENTS:
    total=30
    200=10
    503=20

    WITH BULKHEAD:
    EVENTS ok=30
    EVENTS slow=0

Exactly ten calls obtained bulkhead slots and twenty were rejected with HTTP 503 after the 0.5-second acquisition timeout.

Prometheus recorded:

    gateway_bulkhead_rejections_total{target="payments"} 20

Because the initial three-second occupancy was shorter than the Prometheus scrape interval, an additional multi-wave test was used so Prometheus could observe the maximum occupancy.

Six waves of ten concurrent payment requests were generated.

Prometheus then recorded:

    max_over_time(
        gateway_bulkhead_in_flight{target="payments"}[5m]
    ) = 10

This proves that the configured concurrency maximum of ten actually bound.

### Why does the bulkhead wrap the circuit breaker?

The bulkhead controls admission to the complete logical downstream payment operation.

The composition is:

    bulkhead -> circuit breaker -> retry -> call

With retries inside the bulkhead, a logical payment operation occupies one slot for its entire lifetime, including retry attempts.

Putting the bulkhead deeper inside the retry chain could allow separate retry attempts to acquire their own slots, weakening the intended concurrency bound.

### Bulkhead vs rate limiter

A rate limiter controls how many requests may arrive during a time interval.

A bulkhead controls how many operations may concurrently occupy resources associated with a particular dependency.

The rate limiter protects against excessive arrival rate or traffic bursts.

The bulkhead protects against dependency saturation and prevents one slow downstream service from consuming unbounded concurrent resources.

## Final Results

Task 1:

    Notifications service ........ PASS
    Fire-and-forget test ......... PASS
    Retry implementation ......... PASS
    Retry failure injection ...... PASS

Task 2:

    Circuit breaker .............. PASS
    OPEN fast-fail ............... PASS
    Recovery after cooldown ...... PASS
    Rate limiter burst ........... PASS
    Retry-After header ........... PASS
    Below-limit traffic .......... PASS

Bonus:

    Bulkhead semaphore ........... PASS
    Fast rejection ............... PASS
    /events isolation ............ PASS
    Rejection metric ............. PASS
    Maximum occupancy = 10 ....... PASS

After testing, the system was restored to normal configuration:

    PAYMENT_FAILURE_RATE=0.0
    PAYMENT_LATENCY_MS=0
    RATE_LIMIT_RPS=10


---

# Implementation Appendix

The complete implementations are committed in the repository. The main Lab 11
files are:

- `app/notifications/main.py`
- `app/notifications/requirements.txt`
- `app/notifications/Dockerfile`
- `k8s/notifications.yaml`
- `app/gateway/main.py`
- `k8s/gateway.yaml`

## Notifications requirements

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

## Notifications service

```python
"""QuickTicket Notifications — mock notification service with fault injection."""

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

# --- Config ---
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
    "notifications_requests_total",
    "Total notification service requests",
    ["method", "path", "status"],
)

REQUEST_DURATION = Histogram(
    "notifications_request_duration_seconds",
    "Notification service request duration",
    ["method", "path"],
)

NOTIFY_TOTAL = Counter(
    "notifications_notify_total",
    "Notification attempts",
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
            response.status_code,
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
    from starlette.responses import Response

    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.post("/notify")
def notify(body: dict = None):
    body = body or {}

    event = body.get("event", "unknown")
    order_id = body.get("order_id", "unknown")

    # Fault-injected latency
    if NOTIFY_LATENCY_MS > 0:
        delay = NOTIFY_LATENCY_MS / 1000
        log.info(
            f"Injecting {NOTIFY_LATENCY_MS}ms notification latency "
            f"for order={order_id}"
        )
        time.sleep(delay)

    # Fault-injected failure
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning(
            f"Notification failed (injected): "
            f"event={event} order_id={order_id}"
        )
        raise HTTPException(500, "Notification delivery failed")

    NOTIFY_TOTAL.labels("success").inc()

    log.info(
        f"Notification sent: event={event} order_id={order_id}"
    )

    return {
        "status": "sent",
        "event": event,
        "order_id": order_id,
    }
```

## Notifications Kubernetes manifest

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

## Gateway resilience implementation

The retry, circuit breaker, rate limiter, and bulkhead implementations are
contained in `app/gateway/main.py`.

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    """Call `func` with retry-on-transient-error.

    No-op default: calls func once and returns. Lab 11 task 11.4 replaces this
    body with exponential backoff + jitter, retryable/non-retryable branching,
    and Prometheus counters on the `gateway_retry_total{target,result}` metric.

    See lab 11 §11.4 for the behavior contract. The wiring (in /pay below)
    will pick up your implementation automatically.
    """
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
            retryable = False

            if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
                retryable = True

            elif isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code

                if status >= 500 or status in (408, 429):
                    retryable = True
                else:
                    RETRY_TOTAL.labels(
                        target=target,
                        result="non_retryable",
                    ).inc()
                    raise

            else:
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

            await asyncio.sleep(delay)

    raise RuntimeError("retry loop exited unexpectedly")


class CircuitOpenError(Exception):
    """Raised by CircuitBreaker.call when the circuit is open (fast-fail)."""


class CircuitBreaker:
    """Stateful circuit breaker. Lab 11 task 11.7.

    No-op default: state is always CLOSED, .call just calls func. Replace the
    body of .call with a real CLOSED → OPEN → HALF_OPEN state machine that
    fast-fails with CircuitOpenError once `failures >= threshold`, recovers
    after `cooldown_s`, and emits `gateway_circuit_breaker_transitions_total`.
    """

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
        """Run func with circuit-breaker protection.

        No-op default: just calls func. Lab 11 task 11.7 replaces this with
        the state machine. Raise `CircuitOpenError` when the circuit is open.
        """
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


class RateLimiter:
    """Per-key sliding-window rate limiter. Lab 11 task 11.8.

    No-op default: .allow always returns True. Replace it with a sliding
    1-second window that tracks request timestamps per key and rejects
    once `len(window) >= self.rps`.
    """

    def __init__(self, rps: int):
        self.rps = rps
        self.window_s = 1.0
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """Return True if the request should be allowed.

        No-op default: always True. Lab 11 task 11.8 replaces this body.
        """
        now = time.time()
        q = self.hits[key]
        cutoff = now - self.window_s

        while q and q[0] < cutoff:
            q.popleft()

        if len(q) >= self.rps:
            return False

        q.append(now)
        return True


class BulkheadFullError(Exception):
    """Raised when a bulkhead slot cannot be acquired in time."""


class Bulkhead:
    """Bound concurrent calls to one downstream dependency."""

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
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(target=self.name).inc()
            raise BulkheadFullError(
                f"bulkhead[{self.name}] full"
            )

        BULKHEAD_IN_FLIGHT.labels(target=self.name).inc()

        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(target=self.name).dec()
            self.semaphore.release()


payments_cb = CircuitBreaker(CB_FAILURE_THRESHOLD, CB_COOLDOWN_S, name="payments")
rate_limiter = RateLimiter(RATE_LIMIT_RPS)

payments_bulkhead = Bulkhead(
    "payments",
    BULKHEAD_PAYMENTS_MAX,
    BULKHEAD_PAYMENTS_TIMEOUT_S,
)


```
