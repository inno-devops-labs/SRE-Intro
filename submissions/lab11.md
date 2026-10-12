# Lab 11 — Advanced Microservice Patterns

**Branch:** `feature/lab11` (from upstream `main`). The first commit promotes the Lab 7 `k8s/gateway.yaml` (Argo Rollout) unchanged (`kubectl diff` against the live Rollout was empty), so the next commits show only the Lab 11 changes.
**Cluster:** k3d `quickticket`, gateway = Argo Rollout ×5 with the Lab 7 canary (20 % → AnalysisRun `gateway-error-rate` → 50 % → 100 %), in-cluster Prometheus.
**Raw output:** `raw.log`, section `=== LAB 11 ===`. All times UTC, 2026-10-12.

- [x] Task 1 — notifications service, k8s manifest, fire-and-forget wiring, retry with backoff (Tests #1 + #2)
- [x] Task 2 — circuit breaker + rate limiter, tested under failure
- [x] Bonus Task — bulkhead isolation, concurrent /pay vs /events test, cap proven to bind

## 11.0 Things the lab text does not mention

| # | What happened | What I did |
|---|---|---|
| 1 | The gateway Rollout had `imagePullPolicy: Always` (from Lab 5). The spec's `kubectl argo rollouts set image gateway gateway=quickticket-gateway:v1` with a locally imported image would end in `ErrImagePull` (docker.io has no such image). | `k8s/gateway.yaml` now uses `imagePullPolicy: IfNotPresent` and a unique local tag per build (`quickticket-gateway:lab11-1`, `…:lab11-nobh`): re-setting the same tag would not start a new rollout. Every gateway change went through the canary + AnalysisRun. All 4 runs were `Successful` (each ~2 min 40 s). |
| 2 | **The spec's bulkhead test cannot reach the bulkhead here.** Its 30 *concurrent* `POST /reserve` calls exhaust the events DB pool (`maxconn=10`, and `/reserve` holds 2 connections): **25 of 30 reserves → 500**, events logged 56 × `PoolError`. Only 5 `/pay` calls were left to run concurrently (peak in-flight 2 per pod). | Ran the spec test as written, then a variant that reserves first at ~7/s and fires only the `/pay` calls concurrently (`PRE=1`). |
| 3 | Bulkhead, circuit breaker and rate limiter are **per gateway pod**. 30 concurrent `/pay` over 5 pods ≈ 6 per pod, below `BULKHEAD_PAYMENTS_MAX=10`, so the spec's 30-request test cannot produce rejections. At ≥ 100 requests the per-pod **rate limiter (10 rps per path)** rejects the burst before it reaches the bulkhead. | For the bulkhead runs only, set `RATE_LIMIT_RPS=1000` (via a canary rollout) and used N = 100 concurrent `/pay`. Restored to 10 from `k8s/gateway.yaml` afterwards. |
| 4 | **429s are invisible in `gateway_requests_total`.** `rate_limit_middleware` is registered after `metrics_middleware`, so it is the outer middleware, and its 429 short-circuits before any request metric is recorded. `sum by (path) (gateway_requests_total{status="429"})` → empty, while `gateway_rate_limit_rejections_total` counted 96. | Used the rejection counter and gateway access logs. Noted as a monitoring gap. |
| 5 | **Notification failures are invisible to the gateway.** `_notify_order_confirmed` never calls `raise_for_status()`, so an HTTP 500 from notifications counts as "sent". Test #1: notifications failed **87** requests, but the gateway logged only **2** `notify failed` warnings (both connection errors during the notifications pod swap). | Reported. Not changed, because the lab says not to touch the helper's semantics. |
| 6 | Event 1 was sold out again (held-counter leak, Lab 8), so mixedload would produce no `/pay` traffic, and the canary analysis needs canary traffic. Event 3, used by the test bursts, carried `event:3:held=260` from Lab 10. | `UPDATE events SET total_tickets=100000000 WHERE id=1` for the lab (restored to 100 in cleanup). `redis-cli DEL event:3:held` before each burst. That is targeted: `FLUSHDB` would have broken mixedload's in-flight reservations. |

---

## Task 1 — Notifications Service + Retries

### 1. `app/notifications/main.py` (key parts) and `requirements.txt`

`Dockerfile` = payments' with port 8083. `requirements.txt`, identical to payments (no DB, no Redis):

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

```python
# --- Config (fault injection via env vars) ---
NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

# --- Prometheus metrics ---
REQUEST_COUNT = Counter("notifications_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("notifications_request_duration_seconds", "Request duration", ["method", "path"])
NOTIFY_TOTAL = Counter("notifications_notify_total", "Notification send attempts", ["result"])

...

@app.get("/health")
def health():
    return {"status": "healthy", "failure_rate": NOTIFY_FAILURE_RATE, "latency_ms": NOTIFY_LATENCY_MS}

...

@app.post("/notify")
def notify(body: dict = None):
    event = (body or {}).get("event", "unknown")
    order_id = (body or {}).get("order_id", "unknown")

    # Inject latency
    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)

    # Inject failures
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning(f"Notification failed (injected) event={event} order={order_id}")
        raise HTTPException(500, "Notification delivery failed")

    notification_id = f"NTF-{uuid.uuid4().hex[:8].upper()}"
    NOTIFY_TOTAL.labels("success").inc()
    log.info(f"Notification sent: {notification_id} event={event} order={order_id}")
    return {"status": "sent", "notification_id": notification_id, "event": event, "order_id": order_id}
```

### 2. `k8s/notifications.yaml`

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
          imagePullPolicy: Never          # imported with `k3d image import`
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
  type: ClusterIP
  selector:
    app: notifications
  ports:
    - port: 8083
      targetPort: 8083
```

Deployed with `docker build -t quickticket-notifications:v1 ./app/notifications` → `k3d image import` → `kubectl apply -f k8s/notifications.yaml`. Pod `1/1 Ready` a few seconds after the apply (rollout started 01:22:3x, pod age 7 s at the check). In-cluster smoke test: `/health` → `{"status":"healthy","failure_rate":0.0,"latency_ms":0}`, `/notify` → `{"status":"sent","notification_id":"NTF-EEE5BC2F",…}`, and `/metrics` exposes `notifications_requests_total`, `notifications_request_duration_seconds`, `notifications_notify_total`.

### 11.3 Gateway → notifications

`k8s/gateway.yaml` gains `NOTIFICATIONS_URL=http://notifications:8083` plus explicit env for every Lab 11 tunable (`RETRY_MAX=3`, `RETRY_BASE_DELAY_MS=100`, `CB_FAILURE_THRESHOLD=5`, `CB_COOLDOWN_S=30`, `RATE_LIMIT_RPS=10`, `BULKHEAD_PAYMENTS_MAX=10`, `BULKHEAD_PAYMENTS_TIMEOUT_S=0.5`), and the local image. `kubectl apply -f k8s/gateway.yaml` at 01:23:36 → canary → AnalysisRun `gateway-fcbb87fdf-10-2` **Successful** → `Healthy` 5/5 at 01:26:15. Within 3 minutes notifications had received **203** `order_confirmed` events from mixedload. Gateway `/health` now shows `"notifications":"ok"`, but still decides only on events + payments.

### 3. `call_with_retry()`

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    """Call `func` with retry-on-transient-error.

    No-op default: calls func once and returns. Lab 11 task 11.4 replaces this
    body with exponential backoff + jitter, retryable/non-retryable branching,
    and Prometheus counters on the `gateway_retry_total{target,result}` metric.

    See lab 11 §11.4 for the behavior contract. The wiring (in /pay below)
    will pick up your implementation automatically.
    """
    base_delay = RETRY_BASE_DELAY_MS / 1000
    for attempt in range(max_retries):
        try:
            result = await func()
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
            if isinstance(e, httpx.HTTPStatusError):
                status = e.response.status_code
                if not (status >= 500 or status in (408, 429)):
                    RETRY_TOTAL.labels(target, "non_retryable").inc()
                    raise
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            # Exponential backoff with jitter, so retrying clients don't
            # synchronise and hit a recovering service in waves.
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            log.warning(f"retry {target} attempt={attempt + 1}/{max_retries} in {delay:.3f}s: {e!r}")
            await asyncio.sleep(delay)
            continue
        if attempt > 0:
            RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
        return result
```

`max_retries` is the number of **attempts** (3), matching the spec's arithmetic (`0.3³`). Backoff: 100–200 ms before attempt 2 and 200–300 ms before attempt 3. Before deploying, I unit-tested all primitives locally against the real module (`raw.log`): `500,500,ok` → 3 calls, `retried=2`, `succeeded_after_retry=1`. `404` → 1 call, `non_retryable`. `ConnectError ×3` → `exhausted`. `429,ok` → 2 calls.

### 4. Test #1 — fire-and-forget under notify failure (`NOTIFY_FAILURE_RATE=0.3`, `NOTIFY_LATENCY_MS=300`)

```text
$ kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300   # 01:26:50
$ kubectl run checkout-burst … (30 × reserve event 3 → pay)                               # 01:27:13
result: ok=30 fail=0
$ histogram_quantile(0.99, sum by (le,path) (rate(gateway_request_duration_seconds_bucket[2m])))
  path=/reserve/{id}/pay: 0.0614      ← p99 61 ms, despite 300 ms on every notification
  path=/events: 0.02495   path=/events/{id}/reserve: 0.09468   path=/health: 0.0641
/pay p50 0.0072 s, p95 0.0196 s; /pay in the last 2 m: status=200 461.5, status=500 0
```

`/pay` p99 = **61 ms < 100 ms** while every notification took ≥ 300 ms. The notification runs in `asyncio.create_task` after the response is built.

### 6. Real notify failure rate (notifications pod `/metrics`)

```text
notifications_notify_total{result="failed"} 87.0
notifications_notify_total{result="success"} 172.0
notifications_requests_total{method="POST",path="/notify",status="500"} 87.0
notifications_requests_total{method="POST",path="/notify",status="200"} 172.0
```

87 / 259 = **33.6 %** (configured 30 %). Users saw none of them. The gateway saw only 2 (11.0 #5).

### 5. Test #2 — retries under 30 % payment failure (`PAYMENT_FAILURE_RATE=0.3`)

```text
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3     # 01:27:57
$ kubectl run retry-test … (30 chains)                              # 01:28:16
result: ok=30 fail=0
$ sum by (target,result) (gateway_retry_total)        # includes mixedload's /pay traffic
  result=retried,target=payments: 82
  result=succeeded_after_retry,target=payments: 55
  result=exhausted,target=payments: 7
$ /pay in the last 2 m: status=200 614.9, status=500 6.0 ; /pay p99 0.4175 s
$ payments_charges_total{result="success"} 201.0, {result="failed"} 98.0
$ gateway_circuit_breaker_transitions_total → (no series: the circuit never changed state)
```

Payments really failed **98 / 299 = 32.8 %** of calls. Retries turned that into **7 exhausted out of 208 `/pay` (3.4 %, theory 0.3³ = 2.7 %)**. The burst saw 30/30. The cost is latency: `/pay` p99 went from 61 ms to **418 ms** (backoff sleeps).

### 7. Why should notifications be non-blocking?

Because a notification is not part of the purchase. The user's outcome (money taken, ticket confirmed) is final once events confirms the order. Making `/pay` wait for notifications would put a non-critical dependency on the critical path, adding its latency to every checkout and its failures to the checkout error budget. Test #1 measured this: 33.6 % notify failures and +300 ms per notification, yet 30/30 checkouts succeeded and `/pay` p99 stayed at 61 ms. Fire-and-forget also has a price. It is **at-most-once and silent**: 87 notifications were lost, and the gateway noticed 2 of them. In production I would write an outbox row in the same transaction as the order and let a worker deliver it with retries and a dead-letter queue. That keeps checkout non-blocking without losing messages.

### 8. Why `cb.call(retry(...))` and not `retry(lambda: cb.call(...))`?

The circuit breaker should judge **the dependency's health per user request**, after transient errors have had their chance to be absorbed:

- **Retry inside the CB (correct):** the CB sees one outcome per `/pay`. A blip that the second attempt fixes is a *success* for the CB, and `failures` resets. In Test #2, 30 % of attempts failed, yet the CB never opened (0 transitions), because only 7 requests exhausted their retries and successes kept resetting the counter.
- **Retry outside the CB (wrong):** every failed *attempt* counts toward `CB_FAILURE_THRESHOLD`, so a 30 % blip trips the breaker ~3× faster. Once the circuit is OPEN, the retry loop keeps calling into it. With a classifier that retries everything, it would spin on `CircuitOpenError` and add backoff latency to what should be a fast fail. In HALF_OPEN, one user request would send up to 3 probes instead of 1.
- **The reverse consequence of the correct order (from the pitfalls list):** one CB "failure" = up to 3 downstream calls. With the threshold at 5, payments takes up to 15 calls per pod before the circuit opens. Measured in the CB test: payments received only **84** calls in total while 80 probe attempts + mixedload ran against 5 pods.

---

## Task 2 — Circuit Breaker + Rate Limiter

### Code

```python
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

```python
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
```

Local unit test (`raw.log`): threshold 3 → `fail(CLOSED), fail(CLOSED), fail(OPEN), fastfail, fastfail`. After the cooldown a failing probe goes HALF_OPEN → OPEN. After the next cooldown a good call → `CLOSED`, `failures=0`. Rate limiter at 5 rps: 8 rapid calls → 5 allowed / 3 rejected. After 1 s → allowed again. Keys are independent.

### CB under 100 % payment failure (`PAYMENT_FAILURE_RATE=1.0`, 01:29:02)

```text
$ kubectl run cb-probe … (80 checkout attempts)          # 01:29:06–01:29:15
500s=11 503s=38 other=6  (5s)
$ sum by (to) (gateway_circuit_breaker_transitions_total)
  to=OPEN: 5          ← one per gateway pod (dfrhc, zljmv, 6xmc7, f6mck, g2lkx)
$ /pay in the last 2 m (probe + mixedload): status=200 532.2, status=500 36.86, status=503 36.04
$ gateway /health → "circuit_payments":"OPEN"
$ payments_charges_total{result="failed"} 84.0             ← all the calls payments got
```

- **500** = retries exhausted while the pod's circuit was still CLOSED (≤ 5 per pod before it opened).
- **503** = fast-fail, circuit OPEN (`Payment service temporarily unavailable (circuit open)`).
- `other=6`, plus 25 attempts with no reservation: **429 from the rate limiter** (gateway logs: 25 × `POST /events/3/reserve 429`, 6 × `POST /reserve/<id>/pay 429`; `gateway_rate_limit_rejections_total`: reserve 27, pay 6). Once the circuit opened, `/pay` answered instantly, the probe loop sped up to ~16 iterations/s, and individual pods exceeded 10 rps on `/reserve`. **The patterns interact:** fast-fail makes clients faster, which pushes them into the rate limiter.

### CB closes after recovery

```text
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0     # 01:29:52, then sleep 35
$ kubectl run cb-probe2 … (15 attempts)                             # 01:30:31
[1] 200 … [15] 200                                                  ← 15/15
$ sum by (to) (gateway_circuit_breaker_transitions_total)
  to=OPEN: 10   to=HALF_OPEN: 10   to=CLOSED: 5                     (2 / 2 / 1 per pod)
$ gateway /health → "circuit_payments":"CLOSED"
```

The full cycle on one pod (gateway-fcbb87fdf-6xmc7 log):

```text
01:29:11,433  circuit[payments] CLOSED -> OPEN
01:29:41,877  circuit[payments] OPEN -> HALF_OPEN      ← 30 s cooldown; payments still at 100 %
01:29:42,327  circuit[payments] HALF_OPEN -> OPEN      ← failed probe re-opens immediately
01:30:13,014  circuit[payments] OPEN -> HALF_OPEN      ← payments restored at 01:29:52
01:30:13,017  circuit[payments] HALF_OPEN -> CLOSED    ← first mixedload /pay succeeded
```

Every pod shows the same five lines within 01:29:11–01:30:19. Each pod has its own breaker, hence 10/10/5 in the aggregated counter. In HALF_OPEN this implementation (per the contract) lets every concurrent request through as a probe, not just one. That is acceptable at this traffic level, but a production breaker would admit a single probe.

### Rate limiter

```text
$ kubectl run rl-burst … (100 rapid GET /events)          # 01:30:54, took ~1 s
200=51 429=49 other=0          ← 5 pods × 10 rps = 50 (+1 at a window edge)
$ kubectl run rl-headers … (50 warm-up hits, then 5 requests with headers)
HTTP/1.1 200 OK
HTTP/1.1 429 Too Many Requests retry-after: 1 | body: {"error":"rate_limited","path":"/events","limit_rps":10}
HTTP/1.1 429 Too Many Requests retry-after: 1 | …   (×4)
$ 30 × GET /events with sleep 0.2 (below the limit)
200=30 429=0
$ sum by (path) (gateway_rate_limit_rejections_total)
  path=/events: 63   path=/events/{id}/reserve: 27   path=/reserve/{id}/pay: 6
$ sum by (pod) (…{path="/events"}): dfrhc 26, 6xmc7 19, zljmv 9, g2lkx 7, f6mck 2
$ sum by (path) (gateway_requests_total{status="429"}) → (empty)       ← 11.0 #4
```

The per-pod split (26 vs 2) shows kube-proxy's random spread: the cluster-wide ceiling is "≈ 10 × replicas", not a hard 50.

---

## Bonus — Bulkhead

### Code and wiring

```python
class BulkheadFullError(Exception):
    """Raised by Bulkhead.call when no slot frees up within the acquire timeout."""


class Bulkhead:
    """Bounded concurrency for one downstream (Lab 11 bonus).

    At most `max_concurrent` calls to the target are in flight at once; a call
    that cannot get a slot within `acquire_timeout_s` is rejected instead of
    queueing behind a slow dependency.
    """

    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.acquire_timeout_s = acquire_timeout_s
        self._sem = asyncio.Semaphore(max_concurrent)

    async def call(self, func):
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self.acquire_timeout_s)
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full")
        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self._sem.release()
```

`pay_reservation`:

```python
    try:
        # bulkhead → CB → retry → call: the bulkhead gates entry, so one /pay holds
        # one slot for all of its retries, and only real calls occupy slots.
        pay_resp = await payments_bulkhead.call(
            lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
        )
        payment_ref = pay_resp.json().get("payment_ref", "unknown")
    except CircuitOpenError:
        log.error("circuit open, skipping payments call")
        raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
    except BulkheadFullError:
        log.error("payments bulkhead full, rejecting /pay")
        raise HTTPException(503, "Payment service busy (bulkhead full)")
    except httpx.TimeoutException:
        raise HTTPException(504, "Payment service timeout")
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, "Payment failed")
    except Exception as e:
        log.error(f"payment error: {e}")
        raise HTTPException(502, "Payment service unavailable")
```

New metrics: `gateway_bulkhead_in_flight{target}` (Gauge) and `gateway_bulkhead_rejections_total{target}`. Local unit test: 10 concurrent 200 ms calls, max 3, timeout 50 ms → 3 ok, 7 rejected, peak in-flight 3, gauge back to 0.

### Results (`PAYMENT_LATENCY_MS=3000`; `/events` sampled 30× from the same pod, "slow" = > 0.5 s)

| Run | Gateway | concurrent `/pay` | `EVENTS` | `/pay` codes | `/pay` time | bulkhead rejections | max in-flight (any pod) |
|---|---|---|---|---|---|---|---|
| spec script as written | with bulkhead, RL 10 | 30 *incl. reserve* | ok=30 slow=0 (max 0.26 s) | 5×200, **25 reserve 500** (events `PoolError`) | 3.02 s | 0 | 2 |
| pre-reserved | with bulkhead, RL 10 | 30 | ok=30 slow=0 (max 0.30 s) | 20×200, 10×500 | 3.02–3.56 s | 0 | **10** (one pod) |
| pre-reserved | **with bulkhead**, RL 1000 | **100** | **ok=29 slow=1** (max 0.54 s) | 37×200, 13×500, **50×503** | 503s in **0.507 s**; rest 3.0–8.2 s | **50** | **10** |
| pre-reserved | with bulkhead, RL 1000 | 30 | ok=30 slow=0 (max 0.39 s) | 19×200, 11×500 | 3.02–3.50 s | 0 | 10 |
| pre-reserved | **without bulkhead** (`lab11-nobh`), RL 1000 | **100** | **ok=30 slow=0** (max 0.28 s) | 83×200, 14×500, **3×504** | 3.0–**15.7 s** | — | — |
| pre-reserved | without bulkhead, RL 1000 | 30 | ok=30 slow=0 (max 0.21 s) | 29×200, 1×500 | 3.01–3.52 s | — | — |

- `gateway_bulkhead_rejections_total{target="payments"}`: **0 → 50** in the 100-request run.
- `max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m])`: **10** on three pods (5 and 0 on the others: kube-proxy spread), i.e. the cap binds at `BULKHEAD_PAYMENTS_MAX`.
- "Without bulkhead" image = the same code with only the wrapping line reverted (`payments_cb.call(lambda: call_with_retry(...))`, diff in `raw.log`). It went through the same canary (AnalysisRun `gateway-84f9d874d5-12-2` Successful).
- The `/pay` 500s in every run are **`confirm error after payment: Server error '500 …' for url 'http://events:8081/reservations/<id>/confirm'`** (10 in the 30-request run). When the slow payments calls return together, events' 10-connection pool is exhausted in `confirm_reservation` (`PoolError` ×10). **The customer was charged and got no ticket.** That is the Lab 8/10 pool weakness again, now on the money path.

### What the comparison shows

**The spec's prediction did not reproduce: without the bulkhead `/events` stayed fast** (ok=30 slow=0 at 100 concurrent 3-second `/pay`). The gateway is asyncio + `httpx.AsyncClient`. A slow `await client.post()` parks a coroutine and does not block the event loop, so slow payments do not starve `/events` at this concurrency. What the bulkhead changed was on the **payments side**:

- with it, at most 10 calls per pod (≤ 50 cluster-wide) reached payments, and the excess got a clear **503 in 0.5 s**;
- without it, all 100 piled into payments' sync thread pool, `/pay` latency grew to **15.7 s**, and 3 requests hit the gateway's 5 s read timeout (504).

The resource that *would* couple `/pay` to `/events` here is the **single shared `httpx.AsyncClient` pool** (httpx default: 100 connections). With > 100 slow payments calls in one pod, `/events` would wait for a connection (pool timeout 5 s). The bulkhead (10 ≪ 100) prevents exactly that. My test (≤ ~25 per pod) did not reach that point, so this part is reasoning, not measurement.

### Why does the bulkhead wrap the circuit breaker, not the other way around?

- **A slot must be held by real downstream work, for its whole duration, once per request.** With bulkhead → CB → retry, one `/pay` holds one slot across all 3 attempts and backoff sleeps, so `MAX=10` means 10 in-flight user requests. A fast-fail from an OPEN circuit passes through the bulkhead in microseconds, so it effectively never holds a slot. If the retry loop sat outside the bulkhead, each attempt would re-acquire a slot, and the bound would no longer describe concurrency.
- **The CB must not trip because we are busy.** With CB outside the bulkhead, every `BulkheadFullError` would count as a CB failure. A saturated bulkhead (local back-pressure) would open the circuit and cut off a dependency that is healthy but slow, mixing "we are full" with "payments is broken". With the bulkhead outside, the CB only ever sees real payments outcomes.

### Bulkhead vs. rate limiter: what does each protect against?

- **Rate limiter:** an **ingress ceiling**. It protects the gateway and everything behind it from too many *incoming* requests per endpoint per second, regardless of downstream health. Measured: it rejected bursts at ~10 rps per pod per path even when payments was perfectly healthy (`200=51 429=49`).
- **Bulkhead:** **dependency isolation**. It limits how many *in-flight* calls may wait on *one* downstream, so a slow dependency cannot consume shared capacity (connections, tasks, threads) needed by other paths. It does nothing while that dependency is fast (0 rejections at 30 requests) and acts only when it slows down (50 rejections at 100 requests × 3 s).

A rate limiter counts requests per second, a bulkhead counts concurrent waits. Under a slow dependency a low RPS can still mean high concurrency. Only the bulkhead catches that.

---

## Cleanup / final state

- Gateway back on `k8s/gateway.yaml` (`quickticket-gateway:lab11-1`, `RATE_LIMIT_RPS=10`), `kubectl diff -f k8s/gateway.yaml` empty, `Healthy` 5/5. All 4 AnalysisRuns of the lab are `Successful` (revisions 10–13).
- notifications kept running (1/1; needed for Lab 12). payments back to `0.0` / `0`.
- events / payments / redis / postgres specs identical to the pre-lab reference. mixedload deleted. Event 1 `total_tickets` back to 100.
- ArgoCD unchanged (`automated: {}`, no sync since 2026-09-28). `raw.log` section closed at 01:48:06Z.

**Remaining risks found here:** retrying `/pay` on `httpx.TimeoutException` can double-charge, because payments is not idempotent (Lab 8 showed charges completing after a gateway timeout), so the retry needs an idempotency key (the reservation id). Events' non-blocking 10-connection pool fails `reserve` under concurrency and fails `confirm` *after* a successful charge.
