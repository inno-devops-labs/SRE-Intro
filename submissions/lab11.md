# Lab 11 — Advanced Microservice Patterns

All tests were run on the **k3d cluster** `quickticket`: 5-pod gateway as an Argo
Rollouts `Rollout`, `payments`/`events`/`notifications` Deployments, the Lab 8
`mixedload` for background checkout traffic, and the Lab 7 in-cluster Prometheus
(`monitoring/prometheus`). Locally-built images `quickticket-gateway:v1` and
`quickticket-notifications:v1` were imported with `k3d image import`.

> **Note on `k8s/gateway.yaml`.** Task 11.3 only requires adding the
> `NOTIFICATIONS_URL` env var. To actually run the Lab 11 gateway code in k3d I
> also switched the image to the locally-built `quickticket-gateway:v1` with
> `imagePullPolicy: Never` (the previous ghcr image + `Always` can't resolve a
> locally-imported tag). Same choice as the reference PRs.

---

## Task 1 — Notifications Service + Retries

### 1. `app/notifications/main.py` (key bits) + `requirements.txt`

Copied the payments template; fault injection via `NOTIFY_FAILURE_RATE` +
`NOTIFY_LATENCY_MS`, three Prometheus metrics.

```python
NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

REQUEST_COUNT = Counter("notifications_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("notifications_request_duration_seconds", "Request duration", ["method", "path"])
NOTIFY_TOTAL = Counter("notifications_notify_total", "Total notify attempts", ["result"])

@app.get("/health")
def health():
    return {"status": "healthy", "failure_rate": NOTIFY_FAILURE_RATE, "latency_ms": NOTIFY_LATENCY_MS}

@app.post("/notify")
def notify(body: dict = None):
    body = body or {}
    event = body.get("event", "unknown")
    order_id = body.get("order_id", "unknown")
    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)              # inject latency
    if random.random() < NOTIFY_FAILURE_RATE:             # inject failures
        NOTIFY_TOTAL.labels("failed").inc()
        raise HTTPException(500, "Notification delivery failed")
    NOTIFY_TOTAL.labels("success").inc()
    return {"status": "sent", "notification_ref": f"NOTIF-{uuid.uuid4().hex[:8].upper()}",
            "event": event, "order_id": order_id}
```

`requirements.txt` (identical to payments — no DB, no Redis):

```
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

`Dockerfile` is the payments Dockerfile with the port changed to 8083.

### 2. `k8s/notifications.yaml`

Deployment (1 replica, `quickticket-notifications:v1`, `imagePullPolicy: Never`,
port 8083, `NOTIFY_*` env defaults, `app=notifications` labels) + ClusterIP
Service (8083 → 8083). Copied from `k8s/payments.yaml`.

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
            httpGet: { path: /health, port: 8083 }
            initialDelaySeconds: 10
            periodSeconds: 10
            failureThreshold: 3
          readinessProbe:
            httpGet: { path: /health, port: 8083 }
            periodSeconds: 5
            failureThreshold: 2
          resources:
            requests: { cpu: 50m, memory: 64Mi }
            limits: { cpu: 200m, memory: 256Mi }
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

```
$ kubectl apply -f k8s/notifications.yaml
deployment.apps/notifications created
service/notifications created
deployment "notifications" successfully rolled out
$ kubectl get deploy notifications
notifications ready=1/1
```

### 3. `call_with_retry()` implementation

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    base_delay = RETRY_BASE_DELAY_MS / 1000
    for attempt in range(max_retries):
        try:
            result = await func()
            if attempt > 0:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result
        except Exception as e:
            retryable = isinstance(e, (httpx.TimeoutException, httpx.ConnectError))
            if isinstance(e, httpx.HTTPStatusError):
                status = e.response.status_code
                retryable = status >= 500 or status in (408, 429)
            if not retryable:                       # any other 4xx / non-transient
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                raise
            if attempt == max_retries - 1:          # give up
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
```

`NOTIFICATIONS_URL=http://notifications:8083` set on `k8s/gateway.yaml` (and
`app/docker-compose.yaml`). Gateway `/health` after wiring — notifications
reported but **not** gating `critical_ok`:

```
{"status":"healthy","checks":{"events":"ok","payments":"ok","notifications":"ok","circuit_payments":"CLOSED"}}
```

### 4. Test #1 — fire-and-forget under notify failure

`kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300`,
then a 30-checkout burst from an in-cluster curl pod:

```
result: ok=30 fail=0
```

`/pay` p99 from **in-cluster Prometheus** (`histogram_quantile(0.99, … gateway_request_duration_seconds_bucket{path="/reserve/{id}/pay"} …)`),
paired 40-pay bursts:

```
baseline  (notify=0)          /pay p99 = 0.190 s
injected  (notify 0.3/300ms)  /pay p99 = 0.221 s
```

Baseline ≈ injected, and both are **< the 300 ms** injected notify latency — the
300 ms never lands on `/pay`, proving the notify is genuinely fire-and-forget.
(The absolute ~200 ms is the natural `/pay` baseline on this single-node k3d under
`mixedload` contention — payments charge + events confirm — not inflation.)

### 5. Test #2 — retries fire under transient payment failure

`kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3`, 30-checkout burst:

```
result: ok=29 fail=1
```

`sum by (target,result) (gateway_retry_total)` from Prometheus:

```
{target="payments", result="retried"}               17
{target="payments", result="succeeded_after_retry"}  9
{target="payments", result="exhausted"}              1
```

`retried` and `succeeded_after_retry` are both non-zero → retries actually fired
and recovered. The single `fail=1` matches the single `exhausted` (all-three-fail
≈ `0.3³ ≈ 2.7 %`).

### 6. Real notify failure rate (notifications pod `/metrics`)

Read directly from the pod (Prometheus doesn't scrape notifications):

```
notifications_notify_total{result="success"} 28.0
notifications_notify_total{result="failed"}  12.0
```

→ 12/40 = **30 %** actual failure rate (matches the injected `0.3`), while user
checkout stayed 40/40 — failures invisible to the user.

### 7. Why should notifications be non-blocking (fire-and-forget)?

A confirmation notification is a **best-effort side effect**, not part of the
purchase transaction. By the time we notify, payment already succeeded and the
reservation is confirmed. Awaiting it would (a) add the notifications service's
latency to *every* checkout, and (b) fail a fully-paid order just because a
non-critical downstream is slow or down. `asyncio.create_task` returns the user
response immediately and swallows notify errors with a log line — which is also
why `/health` gates `critical_ok` on **events + payments only**.

### 8. Design prompt — why `cb.call(retry(...))`, not `retry(cb.call(...))`?

The breaker must see one fully-retried call as a **single** success/failure, and
retry logic must **never** retry a `CircuitOpenError`.

- **`cb.call(retry(_charge))` (correct):** retries happen *inside*; the CB records
  one outcome per exhausted retry sequence, and when OPEN it fast-fails instantly
  — the retry loop never runs.
- **`retry(cb.call(_charge))` (wrong):** when OPEN, `cb.call` raises
  `CircuitOpenError` and the retry loop would keep hammering a breaker that exists
  precisely to *stop* calls, defeating the fast-fail; each attempt would also
  register as its own CB failure.

> One CB failure = up to N internal retries: 5 external CB failures ≈ 15 downstream
> calls. Not wrong, but worth stating.

---

## Task 2 — Circuit Breaker + Rate Limiter

### `CircuitBreaker.call` and `RateLimiter.allow`

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

### Circuit OPEN under 100 % payment failure

`PAYMENT_FAILURE_RATE=1.0`, ~80 checkout attempts:

```
500s=25 503s=49
gateway_circuit_breaker_transitions_total{to="OPEN"} 4
```

Textbook per-process behavior with 5 pods: each pod's own CB needs `threshold(5)`
failures, so ~25 `500`s (≈ 5 pods × 5) trip the circuits, after which they
fast-fail `503`. 4 of 5 pods reached the threshold within the 80 requests (LB
distribution) — matching the lab's "you have 5 pods, each with its own
per-process circuit breaker" gotcha.

### Circuit CLOSED after recovery

`PAYMENT_FAILURE_RATE=0.0`, `sleep 35` (cooldown 30 s), 20 checkouts:

```
after recovery: 200=20 non200=0
gateway_circuit_breaker_transitions_total{to="OPEN"}      5
gateway_circuit_breaker_transitions_total{to="HALF_OPEN"} 5
gateway_circuit_breaker_transitions_total{to="CLOSED"}    5
```

All 5 pods walked the full `OPEN → HALF_OPEN → CLOSED` cycle and normal traffic
resumed.

### Rate limiter

100 rapid `/events` (5 pods × `RPS=10` ≈ 50 ceiling):

```
200=61 429=39
```

`Retry-After` header on a 429:

```
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

Sustained load below the limit (`sleep 0.25`) — zero rejections:

```
200=30 429=0
```

Rejection counter (`sum by (path) (gateway_rate_limit_rejections_total)`):

```
{path="/events"}            65
{path="/events/{id}/reserve"} 5
{path="/reserve/{id}/pay"}    2
```

---

## Bonus Task — Bulkhead Isolation

### `Bulkhead.call` + wiring

```python
class BulkheadFullError(Exception):
    """Raised by Bulkhead.call when the pool is full and acquire timed out."""

class Bulkhead:
    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.sem = asyncio.Semaphore(max_concurrent)
        self.acquire_timeout_s = acquire_timeout_s

    async def call(self, func):
        try:
            await asyncio.wait_for(self.sem.acquire(), timeout=self.acquire_timeout_s)
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full")
        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self.sem.release()

payments_bulkhead = Bulkhead("payments", BULKHEAD_PAYMENTS_MAX, BULKHEAD_PAYMENTS_TIMEOUT_S)
```

Wrapping line in `pay_reservation` (bulkhead **outside** CB, outside retry):

```python
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
)
...
except BulkheadFullError:
    raise HTTPException(503, "Payment service temporarily unavailable (bulkhead full)")
```

### Test setup note

On k3d the per-pod rate limit (`RPS=10`) and per-pod bulkhead (`MAX=10`) have the
same ceiling, so the rate limiter would mask the bulkhead. Since the lab makes
these env-tunable, I raised `RATE_LIMIT_RPS=1000` on the gateway Rollout for this
test only (imperative `kubectl patch`, reverted afterward — the committed manifest
keeps the default) so the bulkhead is the binding constraint. `payments` injected
with `PAYMENT_LATENCY_MS=3000` (slowness, not failure — the bulkhead's home turf).

### Cap binds + rejections

90 concurrent `/pay` (pre-created reservations), 5 pods × `MAX=10` = 50 capacity:

```
pay codes:  37 × 200    13 × 500    40 × 503
```

- **40 × 503** = bulkhead fast-fails. `sum by (target) (gateway_bulkhead_rejections_total)` → `{payments} 40`, and CB transitions stayed empty, so **all 40 are bulkhead rejections**, not circuit-open.
- **13 × 500** = the single-replica `payments` pod (blocking `time.sleep`) saturating its thread pool under 90 concurrent 3 s calls — an artifact of the load target, unrelated to the bulkhead.
- Occupancy binds at the cap. `max by (instance) (max_over_time(gateway_bulkhead_in_flight{target="payments"}[5m]))`:

```
pod-1 = 10   pod-2 = 10   pod-3 = 10   pod-4 = 10   pod-5 = 7
cluster-wide max concurrent = 47
```

Every pod peaks at **MAX = 10** (one at 7 by LB skew) — the cap actually binds.

### `/events` isolation (with vs. without the cap)

Same 90-concurrent-`/pay` load, sampling `/events` latency simultaneously:

```
WITH bulkhead (MAX=10):       EVENTS: ok=28 slow=2
WITHOUT bulkhead (MAX=100000): EVENTS: ok=30 slow=0
```

**Honest observation:** `/events` stayed fast in *both* cases. This gateway is a
single-worker **asyncio** app, and awaiting a slow `httpx` call does not block the
event loop — other coroutines (`/events`) keep running — so the event-loop
*starvation* the lab pictures needs thread/worker-pool exhaustion, which pure
I/O-bound waits don't cause here. The bulkhead's real, measured effect is what the
metrics show: it **bounds concurrency to the slow dependency at 10 and fast-fails
the excess (40 × 503)** instead of letting all 90 pile onto payments — the
resource-isolation guarantee, even though `/events` latency didn't visibly move.

### Why must the bulkhead wrap the circuit breaker, not vice versa?

A slot must represent **one logical call** held for its whole lifetime. Retries
live *inside* the CB, so if the bulkhead were on the inside (per attempt) each of
the 3 retry attempts would grab its own slot and `MAX` would be meaningless. With
the bulkhead **outside**, one `/pay` = one occupant through its entire retry+CB
sequence, so `MAX` truly bounds concurrent occupants. (A CB fast-fail does briefly
pass through a slot, but returns in microseconds; the slow *real* calls are what
the cap protects against.)

### Bulkhead vs. rate limiter — what does each protect against?

- **Rate limiter** guards against too many **incoming** requests per unit time — a
  throughput ceiling (req/s), cluster/endpoint-wide, protecting against
  overload/DDoS. *How fast requests arrive.*
- **Bulkhead** guards against one slow/failing **downstream dependency**
  monopolizing shared concurrency and starving calls to *other* dependencies —
  dependency isolation via bounded concurrent resource. *How much concurrent
  resource one dependency may hold.*
