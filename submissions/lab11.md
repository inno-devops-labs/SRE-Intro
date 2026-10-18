# Lab 11 — Advanced Microservice Patterns

## Overview

Implemented Task 1, Task 2 and the Bulkhead bonus. The solution branch is
`feature/lab11`, created directly from `main` at
`cf2ad63f3d3d56546c3bd5a18b09867ffb74b200`. The base has no student `k8s/`
manifests, so this change adds the Gateway Rollout/Service and Notifications
Deployment/Service, using the existing cluster configuration as reference.
Other lab branches were not merged or used as the branch base.

```text
client → per-path rate limiter → Gateway
    checkout → payments bulkhead → circuit breaker → retry → Payments
             → Events confirmation
             → background Notifications task
```

The cluster uses five Gateway processes, each with its own limiter, breaker
and semaphore. HTTP experiments address one pod directly so limits cannot be
hidden by Service load balancing. Target: `gateway-f44d6dfc8-4jf7p`.
Experiments ran from 2026-10-18T08:31:56.867508+00:00 to 2026-10-18T08:34:31.902454+00:00 (UTC;
Moscow time is UTC+3). All HTTP traffic originated inside k3d from a Python
probe using the same gateway image and `httpx`.

A dedicated event with 10,000 tickets (ID `7`) avoided stock
exhaustion and changes to pre-existing events. Each `/pay` used a real
reservation and confirmed through Events/PostgreSQL/Redis. No unrelated
mixedload deployment was added; the probe generated the complete checkout
traffic itself. The dedicated event, its orders and reservation keys were
removed after the experiments. The additional retry check used event ID 8
on 2026-10-18 from 08:36:16 to 08:36:41 UTC and also cleaned up its data.

## Task 1 — Notifications and retries (4 points)

### Notifications service

`app/notifications/main.py` validates `event` and `order_id`, logs JSON, exposes
health and Prometheus metrics, and supports asynchronous latency/failure
injection. No database or Redis dependency is needed. Both result counters are
initialized to zero. Key endpoint implementation:

```python
async def notify(body: Notification):
    if NOTIFY_LATENCY_MS > 0:
        await asyncio.sleep(NOTIFY_LATENCY_MS / 1000)
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning("Notification failed (injected): event=%s order=%s", body.event, body.order_id)
        raise HTTPException(500, "Notification delivery failed")
    NOTIFY_TOTAL.labels("success").inc()
    log.info("Notification sent: event=%s order=%s", body.event, body.order_id)
    return {"status": "sent", "event": body.event, "order_id": body.order_id}
```

Observability and health:

```python
async def metrics_middleware(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    if request.url.path != "/metrics":
        path = request.url.path if request.url.path in ("/notify", "/health") else "other"
        REQUEST_COUNT.labels(request.method, path, response.status_code).inc()
        REQUEST_DURATION.labels(request.method, path).observe(time.monotonic() - start)
    return response

async def health():
    return {"status": "healthy", "failure_rate": NOTIFY_FAILURE_RATE,
            "latency_ms": NOTIFY_LATENCY_MS}

async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

Dependencies (`app/notifications/requirements.txt`, identical to Payments):

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

Kubernetes Deployment and Service (`k8s/notifications.yaml`):

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
              name: http
          env:
            - name: NOTIFY_FAILURE_RATE
              value: "0.0"
            - name: NOTIFY_LATENCY_MS
              value: "0"
          readinessProbe:
            httpGet:
              path: /health
              port: http
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
  type: ClusterIP
  selector:
    app: notifications
  ports:
    - name: http
      port: 8083
      targetPort: 8083
```

### Gateway wiring and retry implementation

`NOTIFICATIONS_URL=http://notifications:8083` is set in the Rollout and Compose.
After payment and confirmation, `asyncio.create_task` schedules notification;
a set retains task references until completion. HTTP 500 responses are checked
with `raise_for_status()` and logged as non-critical failures. Notifications
never determine the Gateway health verdict: Events and Payments remain critical.

The following implementation uses three **total attempts**, 100ms base delay,
exponential growth and uniform jitter. Ordinary 4xx and unexpected exceptions
fail immediately. Monotonic time is used for local interval measurements.

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    """Retry transient failures; max_retries is the TOTAL number of attempts."""
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")
    base_delay = RETRY_BASE_DELAY_MS / 1000
    for attempt in range(max_retries):
        try:
            result = await func()
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as exc:
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                if not (500 <= status < 600 or status in (408, 429)):
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

### Reproduce the deployment and experiments

From the repository root, with the existing k3d cluster and dependencies:

```bash
docker build -t quickticket-gateway:lab11 app/gateway
docker build -t quickticket-notifications:v1 app/notifications
k3d image import -c quickticket quickticket-gateway:lab11 quickticket-notifications:v1
kubectl apply -f k8s/notifications.yaml -f k8s/gateway.yaml
kubectl wait --for=condition=Completed rollout/gateway --timeout=240s
.venv/bin/python scripts/lab11_experiments.py --run
```

Install test/driver dependencies with `python -m pip install -r tests/requirements.txt`.
The driver merges the Notifications scrape job from
`monitoring/prometheus/lab11-jobs.yaml` into the existing Prometheus ConfigMap,
retaining gateway discovery and existing alerts. It reloads Prometheus with
SIGHUP only after the projected file updates. Temporary A/B gateway scrape jobs
use 1s intervals and are removed afterward. Normal gateway/notification scraping
uses 5s. Reusing an image tag requires a Rollout restart after rebuild/import;
changing the tag/template triggers a rollout automatically. The first deploy
changed the old GHCR image to the local `:lab11` image. The canary analysis
completed with three successful measurements, each error ratio `0`.

Gateway image index:
`sha256:9174fa4389b9fdfc4a0c08ff0449330bf9bfe2a4f779fc42bb0ecae723d878cb`.
Notifications image index:
`sha256:a529acbf2d45d6230825eb7253bc351d065502a6baf5553e933bdb3ca2cf1f24`.
Live source SHA-256 checks and final resource state are saved in
[verification.txt](lab11/verification.txt).

### Test #1 — Notifications failure and latency

Each run contains 30 full reserve/pay chains, paced by 130ms after each checkout
so the 10 RPS limiter cannot mask notification behavior. The measured `/pay`
p99 below is the nearest-rank percentile of client elapsed times, including
Payments and Events confirmation, but excluding reservation creation.

| Scenario | HTTP outcomes | `/pay` p99 |
|---|---|---:|
| Baseline, all fault settings zero | {'200': 30} | 35.545 ms |
| Notify failure 0.3, latency 300ms | {'200': 30} | 19.693 ms |

Real notification counters after the injected run:

```text
notifications_notify_total{result="success"} 20.0
notifications_notify_total{result="failed"} 10.0
```

Raw before/after counters and every response timing are in
[01-baseline.json](lab11/01-baseline.json) and
[02-notify-failure.json](lab11/02-notify-failure.json). The injected rollout started fresh counters: 10 failures out of 30 attempts
(33.3% observed versus 30% configured). This uses the destination counters,
rather than inferring success from completed `httpx.post()` transport calls. The Prometheus snapshot is in
[02-prometheus.json](lab11/02-prometheus.json):

```json
{
  "notifications": [
    {
      "labels": {
        "result": "success"
      },
      "value": "20"
    },
    {
      "labels": {
        "result": "failed"
      },
      "value": "10"
    }
  ],
  "pay_p99": [
    {
      "labels": {},
      "value": "0.03157092145392747"
    }
  ]
}
```

Prometheus histogram p99 is an estimate over the preceding two minutes, with
bucket interpolation and baseline traffic in the same window. It is not the
exact percentile of the 30 injected checkouts. The client samples provide the
experiment-specific comparison.

**Why non-blocking notifications?** Confirmation is the critical result. A slow
or failed notification must not add its delivery delay to checkout or turn an
already confirmed order into a reported failure. Metrics/logs still expose
notification loss. In-memory tasks can be lost on process exit; a durable queue
or transactional outbox is needed for guaranteed eventual delivery.

### Test #2 — Payments at 30% failure

With `PAYMENT_FAILURE_RATE=0.3`, 30 checkout outcomes were
`{'200': 28, '500': 2}`; `/pay` p99 was 482.081ms.
One additional run (all results retained) produced `{'200': 30}`;
its `/pay` p99 was 472.694ms. Reproduce that check with
`.venv/bin/python scripts/lab11_experiments.py --run --retry-only`.
See [11-payment-retry-repeat.json](lab11/11-payment-retry-repeat.json),
[11-prometheus.json](lab11/11-prometheus.json) and
[summary-retry-repeat.json](lab11/summary-retry-repeat.json).
The first run's two exhausted payments are not omitted: at p=0.027 per
logical checkout, observing at least two failures in 30 has probability about 19%.
The deterministic tests independently verify attempt count and classification.

Gateway counters at the end of the first experiment:

```text
gateway_retry_total{result="retried",target="payments"} 13.0
gateway_retry_total{result="succeeded_after_retry",target="payments"} 7.0
gateway_retry_total{result="exhausted",target="payments"} 2.0
gateway_bulkhead_in_flight{target="payments"} 0.0
gateway_bulkhead_rejections_total{target="payments"} 0.0
```

The non-zero `retried` and `succeeded_after_retry` values prove that successful
HTTP outcomes were recovered through actual retry attempts. Random injection
can exhaust all three attempts with probability approximately `0.3³ = 2.7%`;
30/30 success is not guaranteed. Raw responses are in
[03-payment-retry.json](lab11/03-payment-retry.json). Prometheus values:

```json
[
  {
    "labels": {
      "result": "retried",
      "target": "payments"
    },
    "value": "13"
  },
  {
    "labels": {
      "result": "succeeded_after_retry",
      "target": "payments"
    },
    "value": "7"
  },
  {
    "labels": {
      "result": "exhausted",
      "target": "payments"
    },
    "value": "2"
  }
]
```

**Why `cb.call(retry(...))`?** The breaker counts the final outcome of one
logical payment, after transient failures have had their bounded retries.
With threshold 5 and three attempts, complete failure can produce 15 downstream
calls before OPEN. An OPEN breaker rejects before entering the retry loop.
Reversing the order counts individual failed attempts and may open the breaker
before a logical payment can recover. A correctly classified retry loop would
not retry `CircuitOpenError` itself; claiming it necessarily does would be
inaccurate. Real payment processors also require stable idempotency keys to
avoid duplicate charges after ambiguous network outcomes; this lab retains
the existing mock processor contract.

## Task 2 — Circuit breaker and rate limiter (4 points)

### Circuit breaker

Admission and state updates run without `await`, making them atomic on the
single application event loop. HALF_OPEN admits only one probe. A generation
number prevents stale in-flight results from changing a newer circuit state.
Cancelling a recovery probe reopens the breaker and starts a new cooldown.

```python
class CircuitBreaker:
    """Per-process breaker with a single recovery probe and stale-call fencing."""

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
        self._generation = 0

    def _transition(self, new_state: str):
        """Record a state change. Use this from your .call implementation
        so transitions show up in Prometheus."""
        if self.state != new_state:
            log.warning(f"circuit[{self.name}] {self.state} -> {new_state}")
            CB_STATE_TRANSITIONS.labels(new_state).inc()
            self._generation += 1
        self.state = new_state

    async def call(self, func):
        # Admission and state updates contain no awaits: they are atomic within
        # this process's event loop. Never hold a lock during downstream I/O.
        if self.state == self.OPEN:
            if time.monotonic() - self.opened_at < self.cooldown:
                raise CircuitOpenError(f"circuit[{self.name}] OPEN")
            self._transition(self.HALF_OPEN)
        elif self.state == self.HALF_OPEN:
            raise CircuitOpenError(f"circuit[{self.name}] recovery probe in flight")

        generation = self._generation
        probe = self.state == self.HALF_OPEN
        try:
            result = await func()
        except asyncio.CancelledError:
            # A cancelled probe must not strand the circuit in HALF_OPEN.
            if probe and generation == self._generation:
                self.opened_at = time.monotonic()
                self._transition(self.OPEN)
            raise
        except Exception:
            if generation == self._generation:
                self.failures += 1
                if probe or self.failures >= self.threshold:
                    self.opened_at = time.monotonic()
                    self._transition(self.OPEN)
            raise
        else:
            # A request admitted before another request opened the breaker
            # cannot close it again with a late success.
            if generation == self._generation:
                self.failures = 0
                self._transition(self.CLOSED)
            return result
```

Under 100% payment failure, the 30 checkout statuses were `{'500': 5, '503': 25}`.
The first five logical requests exhausted their retries; the remaining OPEN
requests returned 503 without invoking Payments. After restoring Payments and
waiting 31 seconds (cooldown 30s), recovery produced `{'200': 15}`.

See [04-circuit-open.json](lab11/04-circuit-open.json) and
[05-circuit-recovery.json](lab11/05-circuit-recovery.json), including per-request
timings and explicit `circuit open` reasons.

### Sliding-window rate limiter

```python
class RateLimiter:
    """Per-process, per-path sliding one-second window."""

    def __init__(self, rps: int):
        self.rps = rps
        self.window_s = 1.0
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        cutoff = now - self.window_s
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.rps:
            return False
        q.append(now)
        return True
```

The window is one second per normalized path per process. UUID reservation
paths share `/reserve/{id}/pay`; IDs do not create separate limiter buckets.
Health and metrics endpoints are exempt.

| Experiment on one pod | HTTP outcomes |
|---|---|
| 100 rapid GET `/events` requests | `{'200': 10, '429': 90}` |
| 30 GET `/events`, 200ms pacing (below 10 RPS) | `{'200': 30}` |

Every rejected burst request recorded `Retry-After: 1`. Example captured response:

```text
HTTP 429
retry-after: 1
```

Full response statuses and headers:
[06-rate-burst.json](lab11/06-rate-burst.json),
[07-rate-sustained.json](lab11/07-rate-sustained.json).
Prometheus transitions/rejections:

```json
{
  "transitions": [
    {
      "labels": {
        "to": "OPEN"
      },
      "value": "1"
    },
    {
      "labels": {
        "to": "HALF_OPEN"
      },
      "value": "1"
    },
    {
      "labels": {
        "to": "CLOSED"
      },
      "value": "1"
    }
  ],
  "rate_limit": [
    {
      "labels": {
        "path": "/events"
      },
      "value": "90"
    }
  ]
}
```

The nominal cluster-wide rate ceiling is roughly `replicas × RPS`, but actual
accepted traffic depends on request distribution and timing. These are local
controls, not a global quota; production global limits need a shared upstream
limiter or shared state.

## Bonus — Bulkhead isolation (2 points)

Implementation:

```python
class Bulkhead:
    """Bound concurrent logical operations, including their retries/backoff."""

    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        if max_concurrent < 1 or acquire_timeout_s <= 0:
            raise ValueError("bulkhead capacity and timeout must be positive")
        self.name = name
        self.semaphore = asyncio.BoundedSemaphore(max_concurrent)
        self.acquire_timeout_s = acquire_timeout_s
        self.in_flight = BULKHEAD_IN_FLIGHT.labels(name)
        self.rejections = BULKHEAD_REJECTIONS.labels(name)

    async def call(self, func):
        try:
            await asyncio.wait_for(self.semaphore.acquire(), self.acquire_timeout_s)
        except asyncio.TimeoutError as exc:
            self.rejections.inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full") from exc
        self.in_flight.inc()
        try:
            return await func()
        finally:
            self.in_flight.dec()
            self.semaphore.release()
```

Payment composition and explicit overload response:

```python
try:
    pay_resp = await payments_bulkhead.call(
        lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
    )
except BulkheadFullError:
    raise HTTPException(503, "Payment service busy (bulkhead full)")
```

### Controlled A/B concurrency experiment

Two temporary pods ran the same gateway image, environment and resource
settings, with `RATE_LIMIT_RPS=1000` only on those probes. The main five-replica
Rollout retained 10 RPS throughout. Each test pre-created distinct reservations
before starting 30 simultaneous `/pay` requests, while independently sampling
`/events` every 100ms. Payments injected 3000ms latency with no failures.
The bounded test ran three waves so Prometheus could observe saturation.

The comparison pod bypassed only `payments_bulkhead.call` in its startup command
(`lambda func: func()`); source code, retry, circuit breaker and HTTP client
remained identical. That pod was deleted afterward. No source revert or stash
was needed.

| Scenario | Payment statuses | Events ok / slow (>500ms) | Events p99 | Peak occupants |
|---|---|---|---:|---:|
| Events baseline, no payment load | — | 28 / 0 | 11.93 ms | 0.0 |
| Bulkhead MAX=10, three waves | `{'200': 30, '503': 60}` | 129 / 0 | 157.49 ms | 10.0 |
| Bulkhead bypass, one wave | `{'200': 17, '500': 13}` | 26 / 1 | 503.796 ms | not instrumented |

The bounded rejection p99 was 549.631ms including HTTP and
scheduling overhead around the 500ms admission timeout. Rejection details state
`bulkhead full`, rather than `circuit open`. The gauge returned to zero after
all operations. Direct gateway metrics after the bounded test:

```text
gateway_retry_total{result="retried",target="payments"} 7.0
gateway_retry_total{result="succeeded_after_retry",target="payments"} 7.0
gateway_bulkhead_in_flight{target="payments"} 0.0
gateway_bulkhead_rejections_total{target="payments"} 60.0
```

Prometheus captured both actual saturation and overload rejections:

```json
{
  "rejections": [
    {
      "labels": {
        "__name__": "gateway_bulkhead_rejections_total",
        "instance": "10.42.0.19:8080",
        "job": "lab11-gateway",
        "pod": "lab11-bounded",
        "target": "payments"
      },
      "value": "60"
    }
  ],
  "max_in_flight": [
    {
      "labels": {
        "instance": "10.42.0.19:8080",
        "job": "lab11-gateway",
        "pod": "lab11-bounded",
        "target": "payments"
      },
      "value": "10"
    }
  ]
}
```

The query was
`max_over_time(gateway_bulkhead_in_flight{job="lab11-gateway",pod="lab11-bounded",target="payments"}[2m])`.
Filtering the probe prevents accidental aggregation across independent processes.
Raw observations are in [08-events-baseline.json](lab11/08-events-baseline.json),
[09-bulkhead.json](lab11/09-bulkhead.json), and
[10-without-bulkhead.json](lab11/10-without-bulkhead.json).

**Interpretation.** The async HTTP client yields the event loop while waiting
for Payments, so 30 slow requests do not necessarily make Events take >2s even
without a bulkhead. The measured A/B latency is reported as observed; the lab's
predicted event-loop starvation is not assumed. The direct evidence for the
bulkhead is the bound of 10 active logical operations, overload 503s near the
admission deadline, successful admitted payments and responsive Events.

The comparison also exposed the existing Events database connection limit:
13 payment responses failed at confirmation, after charging had succeeded,
and one Events request returned 502. Events logged
`psycopg2.pool.PoolError: connection pool exhausted`; the captured evidence is
[events-pool-errors.txt](lab11/events-pool-errors.txt). The bounded run completed
all 30 admitted checkouts and all 129 sampled Events requests. Its Events p99
rose above baseline to 157.49ms, while no sample exceeded 500ms. Seven admitted
payments also needed retries (see gateway counters), consistent with the longer
8.216s checkout p99; the transient error type was not separately instrumented.
The 500ms acquisition timeout is not an end-to-end HTTP deadline: measured
rejection p99 includes approximately 50ms of scheduling/HTTP overhead.
No claim is made that this semaphore creates a separate HTTP connection pool.

**Why outside the breaker?** Bulkhead admission happens before the protected
payment and holds one slot across its retries and sleeps. A full-pool rejection
is a local capacity signal, not a downstream failure; keeping the breaker
inside prevents it counting that rejection. An OPEN breaker still briefly
acquires/releases a slot, but fails without downstream work. If admission is
already queued, that request can wait up to the admission deadline. The required
ordering prioritizes a clear bound around the whole logical operation.

**Bulkhead vs rate limiter.** Bulkhead limits concurrent operations against a
particular dependency. It contains resource usage caused by slow calls even
when arrival rate is modest. Rate limiting bounds arrivals per time window and
protects against bursts or sustained request volume. Either can be per-process;
cluster-wide behavior is a separate state-sharing/deployment decision. A rate
limiter alone does not bound active work when operation duration increases.

## Verification and cleanup

```text
31 tests passed (see verification.txt)
Python compilation: passed
YAML parse: passed
Docker Compose config: passed
Kubernetes server dry-run (Gateway, Notifications): passed
Docker builds (Gateway, Notifications): passed
```

`tests/test_lab11.py` covers transient classification, exact attempt count,
backoff/jitter/counters, breaker failure and recovery, single-probe concurrency,
stale success fencing, cancellation, window expiration, independent paths,
HTTP headers/exemptions, bulkhead concurrency/cleanup and composition,
Notifications validation/counters/latency, and fire-and-forget integration.

Cleanup restored dependency fault settings to failure rate 0.0 and latency 0;
the Gateway Rollout stayed at 5 replicas with RATE_LIMIT_RPS=10, MAX=10,
timeout=0.5s, RETRY_MAX=3, base delay 100ms, threshold=5, cooldown=30s.
Temporary client/A/B pods and their scrape job were removed. Notifications
scraping remains enabled. Test event, test orders and known reservation keys
were removed. Cleanup errors from the driver: `[]`.
Final health, counters and source hashes are in
[verification.txt](lab11/verification.txt).

Existing ArgoCD Application `quickticket` already had a ComparisonError because
remote `main` lacks `k8s/`. It was not repointed or repaired: the requested work
is local and has not been pushed. The existing Rollout strategy and canary
AnalysisTemplate remain in use. The k8s files here extend the existing cluster;
this lab branch does not recreate the whole infrastructure from previous labs.

Rubric evidence:

- Task 1: Notifications endpoints/metrics/manifests, non-blocking checkout under
  notification faults, real retry recovery and counters, both design answers.
- Task 2: OPEN fast-fail, successful cooldown recovery, state metrics, burst
  429/Retry-After evidence, zero below-limit rejections.
- Bonus: bounded concurrency, timeout 503s, Prometheus maximum of 10, concurrent
  Events measurements with/without the bound, and both design answers.

No push or PR was performed.
