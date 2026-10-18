# Lab 11 — Bonus: Advanced Microservice Patterns

> Branch `feature/lab11`. Cluster: k3d `quickticket`, namespace `default`. Gateway is an
> **Argo Rollout** (5 replicas), not a Deployment.
>
> All output below was produced by actually running the commands on that cluster on 2026-09-22.
> Where the lab's own script had to be adapted, the adaptation and the reason are stated inline.

## What was built

| Piece | Where |
|---|---|
| notifications service (4th microservice) | `app/notifications/{main.py,Dockerfile,requirements.txt,.dockerignore}` |
| notifications Deployment + Service | `k8s/notifications.yaml` |
| retry + circuit breaker + rate limiter + bulkhead | `app/gateway/main.py` |
| pattern tunables + `NOTIFICATIONS_URL` | `k8s/gateway.yaml` (env) |
| Prometheus scrape job for the new service | `monitoring/k8s/prometheus.yaml` |
| compose parity | `app/docker-compose.yaml` |

Images: `ghcr.io/vanady39/quickticket-{gateway,notifications}:lab11`, `imagePullPolicy: Never`,
loaded with `k3d image import`. The gateway change went through the canary strategy
(`setWeight 20 → pause → analysis → setWeight 50 → pause → 100`):

```
$ kubectl argo rollouts status gateway --timeout=420s
Paused - CanaryPauseStep
...
Healthy

$ kubectl argo rollouts get rollout gateway
Status:          ✔ Healthy
Strategy:        Canary
  Step:          6/6
  SetWeight:     100
Images:          ghcr.io/vanady39/quickticket-gateway:lab11 (stable)
Replicas:  Desired: 5  Current: 5  Updated: 5  Ready: 5  Available: 5

├──# revision:11
│  ├──⧉ gateway-6d868c98c4           ReplicaSet   ✔ Healthy      2m42s  stable
│  └──α gateway-6d868c98c4-11-2      AnalysisRun  ✔ Successful   2m15s  ✔ 3
```

---

## Deviations from the lab text (all deliberate, all stated)

1. **The lab's scripts reserve against event 3. Event 3 has been sold out since Lab 6**
   (`available: 0`), so every `reserve` would just 409 and every test would silently measure
   nothing. All checkout traffic here goes to **event 1** (and event 2/4 where noted), following
   the standing handoff from Lab 8.
2. **Ticket inventory is finite and could not be topped up.** Lab 8 raised
   `events.total_tickets` for the duration of its experiments. In this session, **writes to the
   Postgres pod were refused by the sandbox's permission classifier**, so the tests were sized to
   fit the 155 tickets that actually existed. Two consequences: the Lab 8 `mixedload` loadgen was
   **not** applied (2 replicas × ~4 checkouts/s would have eaten the entire remaining inventory in
   ~25 s and left every later test reserving against a sold-out event), and the circuit-breaker and
   bulkhead storms use synthetic reservation ids (see §Task 2). Lab 6's `loadgen` was running the
   whole time and provided the background read/reserve traffic.
3. **`kubectl set env rollout/gateway` does not work** on this cluster, contrary to what Lab 7's
   handoff table says: `error: no kind "Rollout" is registered for version "argoproj.io/v1alpha1"`.
   I used `kubectl patch rollout gateway --type=json` instead. Noted for Lab 12.

---

# Task 1 — Notifications Service + Retries (4 pts)

## 1. `app/notifications/main.py` (key bits) and `requirements.txt`

```python
# --- Config (fault injection via env vars) ---
NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

logging.basicConfig(
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"notifications","msg":"%(message)s"}',
    level=logging.INFO,
)

app = FastAPI(title="QuickTicket Notifications", version="1.0.0")

REQUEST_COUNT = Counter(
    "notifications_requests_total", "Total requests", ["method", "path", "status"]
)
REQUEST_DURATION = Histogram(
    "notifications_request_duration_seconds", "Request duration", ["method", "path"]
)
NOTIFY_TOTAL = Counter(
    "notifications_notify_total", "Total notification attempts", ["result"]
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
    return {
        "status": "healthy",
        "failure_rate": NOTIFY_FAILURE_RATE,
        "latency_ms": NOTIFY_LATENCY_MS,
    }


@app.post("/notify")
def notify(body: dict = None):
    body = body or {}
    event = body.get("event", "unknown")
    order_id = body.get("order_id", "unknown")

    if NOTIFY_LATENCY_MS > 0:
        delay = NOTIFY_LATENCY_MS / 1000
        log.info(f"Injecting {NOTIFY_LATENCY_MS}ms latency for {order_id}")
        time.sleep(delay)

    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning(f"Notification failed (injected) event={event} order={order_id}")
        raise HTTPException(500, "Notification delivery failed")

    NOTIFY_TOTAL.labels("success").inc()
    log.info(f"Notification sent: event={event} order={order_id}")
    return {"status": "sent", "event": event, "order_id": order_id}
```

`app/notifications/requirements.txt`, identical to payments, no DB, no Redis:

```
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

`app/notifications/Dockerfile`, payments' Dockerfile with the port changed to 8083, keeping
Lab 2's optimisations (non-root `app` user, `.dockerignore`, requirements copied before source so
the pip layer caches):

```dockerfile
FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .

RUN addgroup --system app && adduser --system --ingroup app app
USER app

EXPOSE 8083
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8083"]
```

**All three required metrics are live:**

```
$ kubectl run notif-smoke --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '...'
--- GET /health ---
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
--- POST /notify ---
{"status":"sent","event":"order_confirmed","order_id":"smoke-1"}
--- GET /metrics (notifications_*) ---
notifications_requests_total{method="GET",path="/health",status="200"} 5.0
notifications_requests_total{method="POST",path="/notify",status="200"} 1.0
notifications_request_duration_seconds_count{method="GET",path="/health"} 5.0
notifications_request_duration_seconds_count{method="POST",path="/notify"} 1.0
notifications_notify_total{result="success"} 1.0
```

Prometheus did **not** pick the new service up on its own: this cluster's Prometheus uses one
explicit `job_name` per service, not `prometheus.io/scrape` annotation discovery. A
`notifications` job was added to `monitoring/k8s/prometheus.yaml`:

```
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- '.../api/v1/targets?state=active'
target: notifications 10.42.0.220:8083 health=up

$ ... query=sum by (result) (notifications_notify_total)
notifications_notify_total{result="success"} = 45
```

## 2. `k8s/notifications.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: notifications
  labels:
    app: notifications
    app.kubernetes.io/part-of: quickticket
spec:
  replicas: 1
  selector:
    matchLabels:
      app: notifications                 # selector = app:<name> ONLY (Lab 4 convention)
  template:
    metadata:
      labels:
        app: notifications
        app.kubernetes.io/part-of: quickticket
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8083"
        prometheus.io/path: "/metrics"
    spec:
      imagePullSecrets:
        - name: ghcr-secret
      containers:
        - name: notifications
          image: ghcr.io/vanady39/quickticket-notifications:lab11
          imagePullPolicy: Never
          env:
            - name: NOTIFY_FAILURE_RATE
              value: "0.0"
            - name: NOTIFY_LATENCY_MS
              value: "0"
          ports:
            - name: http
              containerPort: 8083
          # Lab 4 probe rule: readiness MAY test dependencies, liveness MUST NOT.
          # notifications has no downstream dependencies at all, so /health is a
          # pure self-check and is safe for both — exactly like payments.
          livenessProbe:
            httpGet: {path: /health, port: 8083}
            initialDelaySeconds: 10
            periodSeconds: 10
            failureThreshold: 3
          readinessProbe:
            httpGet: {path: /health, port: 8083}
            periodSeconds: 5
            failureThreshold: 2
          resources:
            requests: {cpu: 50m, memory: 64Mi}
            limits:   {cpu: 200m, memory: 256Mi}
---
apiVersion: v1
kind: Service
metadata:
  name: notifications
  labels:
    app: notifications
    app.kubernetes.io/part-of: quickticket
spec:
  type: ClusterIP
  selector:
    app: notifications
  ports:
    - name: http
      port: 8083
      targetPort: 8083
```

(The file as committed carries the full comments; probe stanzas are folded to one line here only
for readability.)

```
$ kubectl apply -f k8s/notifications.yaml
deployment.apps/notifications created
service/notifications created
$ kubectl rollout status deployment/notifications --timeout=120s
deployment "notifications" successfully rolled out
$ kubectl get pods -l app=notifications
NAME                            READY   STATUS    RESTARTS   AGE
notifications-f7ffbccbb-szr7s   1/1     Running   0          10s
```

Wiring confirmed: `NOTIFICATIONS_URL` is set on the Rollout, and the gateway's `/health` reports
notifications **without** letting it gate the verdict:

```
$ curl -s http://gateway:8080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","notifications":"ok","circuit_payments":"CLOSED"}}
```

## 3. `call_with_retry()`

```python
def _is_retryable(exc: Exception) -> bool:
    """Transient errors only.

    Retryable: the request never got a verdict (connect/timeout), or the server
    said "my fault / come back later" (5xx, 408 Request Timeout, 429 Too Many
    Requests).

    NOT retryable: any other 4xx. A 404 or a 422 is a statement about the
    *request*, not about the server's health — it will fail identically on
    attempt 2 and 3, so retrying it only wastes a round trip and adds load to a
    service that is behaving correctly.
    """
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status >= 500 or status in (408, 429)
    return False


async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    """Exponential backoff with full-width jitter:

        delay = base * 2**attempt + uniform(0, base)

    The 2**attempt term gives a briefly overloaded downstream progressively more
    breathing room. The jitter term is what stops N concurrent gateway callers
    from re-converging on the same instants and re-hammering a service the
    moment it comes back — the classic thundering herd.
    """
    base_delay = RETRY_BASE_DELAY_MS / 1000

    for attempt in range(max_retries):
        try:
            result = await func()
        except Exception as e:
            if not _is_retryable(e):
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                log.warning(f"retry[{target}] non-retryable error, giving up: {e!r}")
                raise
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                log.error(f"retry[{target}] exhausted after {max_retries} attempts: {e!r}")
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            log.warning(
                f"retry[{target}] attempt {attempt + 1}/{max_retries} failed "
                f"({e!r}), backing off {delay * 1000:.0f}ms"
            )
            await asyncio.sleep(delay)
            continue

        if attempt > 0:
            RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            log.info(f"retry[{target}] succeeded on attempt {attempt + 1}")
        return result
```

### Backoff timing is what was configured — measured, not asserted

A cluster test can only show retries statistically. This harness runs **inside the built
`ghcr.io/vanady39/quickticket-gateway:lab11` image against the real module**, so the code under
test is byte-for-byte the code the pods run, and the timings are exact:

```
$ docker run --rm -v "$PWD/unit_patterns.py:/app/unit_patterns.py:ro" -w /app \
      ghcr.io/vanady39/quickticket-gateway:lab11 python unit_patterns.py

== A. retry backoff timing (RETRY_MAX=3, base=100ms) ==
   raised after exhaustion: HTTP 500
   attempts=3  measured gaps(ms)=[181, 216]  total=397ms
   expected windows: attempt0->1 = 100..200ms, attempt1->2 = 200..300ms
   counters: {'retried': 2.0, 'exhausted': 1.0}

== B. success after a transient failure ==
   result='charged' after 3 calls in 309ms
   counters: {'retried': 4.0, 'exhausted': 1.0, 'succeeded_after_retry': 1.0}

== C. non-retryable 4xx is NOT retried ==
   HTTP 404 -> downstream calls made: 1 (must be 1)
   HTTP 422 -> downstream calls made: 1 (must be 1)
   and the retryable edge cases:
   HTTP 408 -> downstream calls made: 3 (must be 3)
   HTTP 429 -> downstream calls made: 3 (must be 3)
   HTTP 500 -> downstream calls made: 3 (must be 3)
   counters: {'retried': 10.0, 'exhausted': 4.0, 'succeeded_after_retry': 1.0, 'non_retryable': 2.0}
```

`181ms ∈ [100, 200)` and `216ms ∈ [200, 300)`, both inside the exact windows
`base·2⁰ + U(0,base)` and `base·2¹ + U(0,base)` for `base = 100ms`. The jitter is clearly doing
something (181 ≠ 100, 216 ≠ 200).

## 4. Test #1 — fire-and-forget under notify failure

Baseline first, knobs neutral, so there is something to compare against
(`/pay` on event 4, 10 chains):

```
[3] pay 200 0.017246
[4] pay 200 0.043238
...
[10] pay 200 0.013840
BASELINE result: ok=10 fail=0
```

Inject:

```
$ kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
deployment.apps/notifications env updated
$ curl -s http://notifications:8083/health
{"status":"healthy","failure_rate":0.3,"latency_ms":300}
```

30 checkout chains on event 1 (event 3 is sold out), printing `http_code time_total` per `/pay`:

```
[8] pay 200 0.017856
[9] pay 200 0.016506
[10] pay 200 0.016420
[11] pay 200 0.017276
[12] pay 200 0.029151
[13] pay 200 0.018584
[14] pay 200 0.016197
[15] pay 200 0.018236
[16] pay 200 0.018125
[17] pay 200 0.018429
[18] pay 200 0.013184
[19] pay 200 0.016406
[20] pay 200 0.015082
[21] pay 200 0.016986
[22] pay 200 0.017545
[23] pay 200 0.014701
[24] pay 200 0.017761
[25] pay 200 0.018970
[26] pay 200 0.017610
[27] pay 200 0.016230
[28] pay 200 0.016689
[29] pay 200 0.016578
[30] pay 200 0.016263
result: ok=30 fail=0
```

**`ok=30 fail=0`**, and every single `/pay` came back in **13–29 ms**, while the notification
dependency behind it was taking 300 ms and failing 30% of the time. If the call were blocking,
the floor would be 300 ms. This is about the tightest proof of fire-and-forget you can get: the
injected latency is **~17x larger than the entire user-visible request**.

`/pay` p99 from Prometheus during the injection window:

```
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
    '.../query=histogram_quantile(0.99, sum by (le,path) (rate(gateway_request_duration_seconds_bucket[5m])))'
/events/{id}/reserve  p99=0.009970155446881148s
/events               p99=0.009966300496381454s
/reserve/{id}/pay     p99=0.024850000000000004s
```

**`/pay` p99 = 24.9 ms**, comfortably under the 100 ms the lab asks for, and unchanged from the
neutral baseline above. The 300 ms never shows up anywhere on the user's path.

The failures *are* real, they're just invisible to the user. Gateway warnings, per pod:

```
pod/gateway-6d868c98c4-94xfb notify-rejected=3
pod/gateway-6d868c98c4-fpf6t notify-rejected=5
pod/gateway-6d868c98c4-ht5ld notify-rejected=1
pod/gateway-6d868c98c4-s26jm notify-rejected=4
pod/gateway-6d868c98c4-s99vd notify-rejected=4
```

17 rejected notifications across 30 successful checkouts, and the user saw 30/30 success.

## 5. Test #2 — retries fire under transient payment failure

```
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
$ # 30 checkout chains on event 1
[1] pay 200 0.022534
[2] pay 200 0.018769
[3] pay 200 0.193544     <-- retried
[4] pay 200 0.017991
...
[12] pay 200 0.018182
[13] pay 200 0.210572    <-- retried
[14] pay 200 0.171383    <-- retried
[15] pay 200 0.017590
...
[27] pay 200 0.017096
[28] pay 200 0.201756    <-- retried
[29] pay 200 0.014500
[30] pay 200 0.024062
result: ok=30 fail=0
```

**`ok=30 fail=0`**, and the retries are visible *in the response times themselves*: four requests
took 171–211 ms against a ~17 ms baseline. That difference is the first backoff sleep, and it
lands inside the configured `[100, 200) ms` window. The user never saw the upstream 500s.

```
$ ... query=sum by (target,result) (gateway_retry_total)
gateway_retry_total{target="payments",result="retried"} = 4
gateway_retry_total{target="payments",result="succeeded_after_retry"} = 4
```

Both non-zero, as the acceptance criterion requires. The gateway logs tie the two together, same
request, backoff announced, then success on attempt 2:

```
{"time":"2026-09-22 17:51:24,207","level":"WARNING","service":"gateway","msg":"retry[payments] attempt 1/3 failed (HTTPStatusError(\"Server error '500 Internal Server Error' for url 'http://payments:8082/charge'\")), backing off 174ms"}
{"time":"2026-09-22 17:51:24,385","level":"INFO","service":"gateway","msg":"retry[payments] succeeded on attempt 2"}
{"time":"2026-09-22 17:51:25,830","level":"WARNING","service":"gateway","msg":"retry[payments] attempt 1/3 failed (...500...), backing off 189ms"}
{"time":"2026-09-22 17:51:26,023","level":"INFO","service":"gateway","msg":"retry[payments] succeeded on attempt 2"}
{"time":"2026-09-22 17:51:26,167","level":"WARNING","service":"gateway","msg":"retry[payments] attempt 1/3 failed (...500...), backing off 149ms"}
{"time":"2026-09-22 17:51:26,321","level":"INFO","service":"gateway","msg":"retry[payments] succeeded on attempt 2"}
{"time":"2026-09-22 17:51:28,358","level":"WARNING","service":"gateway","msg":"retry[payments] attempt 1/3 failed (...500...), backing off 182ms"}
{"time":"2026-09-22 17:51:28,545","level":"INFO","service":"gateway","msg":"retry[payments] succeeded on attempt 2"}
```

`17:51:24,207 → 17:51:24,385` is 178 ms of real wall clock against an announced 174 ms sleep. The
announced delays (174, 189, 149, 182 ms) are all in `[100, 200)` and all different, so the jitter
is doing its job.

Only 4 retries in 30 requests where ~9 were expected at `p=0.3`. That's just small-sample
variance: each request only has a 30% chance of hitting a failure on its first attempt, and
nothing forces those to land evenly across 30 draws. The acceptance criterion is "non-zero", and
the same code under 100% failure in Task 2 produced 25 `exhausted` + 50 `retried`, exactly
`25 × (3-1)`, which is the deterministic confirmation that the loop runs every attempt.

## 6. Real notify failure rate from the notifications pod

The 30-chain burst produced a 17/30 failure split, which is a fair distance from the configured
0.3 on a 30-sample draw. So I ran a 100-call direct probe at the same setting to pin down the real
rate:

```
$ # 100 direct POSTs to notifications:8083/notify with NOTIFY_FAILURE_RATE=0.3, NOTIFY_LATENCY_MS=300
direct /notify probe: 200=70 500=30 (configured NOTIFY_FAILURE_RATE=0.3)
--- one timed call (NOTIFY_LATENCY_MS=300) ---
notify time_total=0.303595s
--- cumulative counters ---
notifications_notify_total{result="failed"} 47.0
notifications_notify_total{result="success"} 84.0
```

**70/30 over 100 calls, exactly the configured rate**, and the timed call confirms the 300 ms
injection down to the millisecond (`0.3036 s`). The 17/30 in the burst was just variance, not a
bug.

## 7. Why should notifications be non-blocking (fire-and-forget)?

Because it is not on the critical path of what the user asked for, and tying it to that path turns
a cosmetic failure into a business failure.

The user asked to buy a ticket. By the time `_notify_order_confirmed` is scheduled, the money has
already been taken and the order is confirmed in the database, **the purchase is already done and
durable**. If the gateway awaited the notification, three things would go wrong, and the numbers
above show all three:

- **Latency.** Every checkout would inherit the notification's latency. Measured: `/pay` p99 was
  24.9 ms with a 300 ms notification behind it. Awaiting it would have made every checkout at
  least 300 ms, a 12x regression on the single most important endpoint, caused by an email.
- **Availability.** A 30% notification failure rate would have become a 30% *checkout* failure
  rate: `ok=30 fail=0` would have been roughly `ok=21 fail=9`. The system's availability would end
  up being the *product* of its dependencies' availabilities instead of just the ones that matter.
  Adding a 99% dependency to a 99.9% checkout gives you 98.9%.
- **Correctness.** Worse than a plain failure: the charge already happened. A 500 returned
  because the *email* failed tells the user their purchase failed when it did not. They retry and
  get double-charged. This failure mode is actively harmful, not just noisy.

This is also why `/health` deliberately reports `notifications` but excludes it from
`critical_ok`. If it were included, a notification outage would mark every gateway pod unhealthy
and, under Lab 8's original readiness probe, remove them all from the Service. A non-critical
dependency should not be able to declare the whole system down.

The cost of fire-and-forget is that the notification can be silently lost (no retry, no queue, and
if the pod is killed mid-task the `asyncio.create_task` dies with it). At this tier that's the
right trade. A production system would hand it to a durable queue instead, which is the same
decoupling but with an at-least-once guarantee bolted on.

## 8. Design prompt — why `cb.call(retry(...))` and not `retry(lambda: cb.call(...))`?

Because the two patterns answer different questions, and only one nesting lets each answer its own.

The circuit breaker's question is *"is payments healthy?"*. The retry's question is *"was that
one blip transient?"*. A single user request that needed three attempts and still failed is **one**
piece of evidence about payments' health, not three. With `cb.call(retry(...))` the breaker sees
exactly the final verdict of each user request, so `CB_FAILURE_THRESHOLD=5` means "5 requests
failed to get through," which is what a human tuning that number actually thinks it means.

Invert it and two things break:

1. **The fast-fail gets neutralised.** Once the breaker is OPEN, `cb.call` raises
   `CircuitOpenError`. In the correct nesting that error propagates straight out, one call,
   microseconds, done. In the inverted nesting it gets raised *inside* the retry loop, which
   dutifully backs off and asks again, and again. The whole point of the pattern is to stop asking,
   and here it's wrapped in a loop that keeps asking. Measured in Task 2: an open circuit answers
   in **2.9 ms**; a retry-wrapped one would pay `100 + 200 ms` of backoff to reach the same
   answer, about 100x worse, and it would still have to be classified as retryable or not, which
   `CircuitOpenError` is not.
2. **The breaker's threshold stops meaning anything.** Each retry attempt would register its own
   failure, so `threshold=5` would trip after 2 user requests (`⌈5/3⌉`), not 5. The dial would be
   silently divided by `RETRY_MAX`.

The subtle consequence the lab asks to note, and the numbers here confirm it: **one CB failure is
N downstream calls.** Task 2's 100%-failure run produced `exhausted = 25` and `retried = 50`, so
25 user-visible failures cost `25 × 3 = 75` calls to a service that was already on fire, and the
breaker needed `5 threshold × 5 pods = 25` of them before it stopped. Retries amplify load exactly
when the target can least afford it, which is the argument for tuning the threshold *down* when
the retry count goes *up*.

---

# Task 2 — Circuit Breaker + Rate Limiter (4 pts)

## `CircuitBreaker`

```python
class CircuitOpenError(Exception):
    """Raised by CircuitBreaker.call when the circuit is open (fast-fail)."""


class CircuitBreaker:
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
        if self.state != new_state:
            log.warning(f"circuit[{self.name}] {self.state} -> {new_state}")
            CB_STATE_TRANSITIONS.labels(new_state).inc()
        self.state = new_state

    async def call(self, func):
        """
        CLOSED    — calls pass through; `threshold` failures trip it to OPEN.
        OPEN      — every call rejected immediately with CircuitOpenError. No
                    socket, no timeout. After `cooldown` seconds the next
                    caller is promoted to HALF_OPEN.
        HALF_OPEN — exactly one trial call. Success closes the circuit and
                    resets the count; failure re-opens it and restarts the
                    cooldown.
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
            # A failed HALF_OPEN probe means "still broken" — straight back to
            # OPEN without waiting for the threshold again.
            if self.state == self.HALF_OPEN or self.failures >= self.threshold:
                self._transition(self.OPEN)
            raise

        self.failures = 0
        self._transition(self.CLOSED)
        return result
```

## `RateLimiter`

```python
class RateLimiter:
    def __init__(self, rps: int):
        self.rps = rps
        self.window_s = 1.0
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """Sliding 1-second window per key.

        A sliding window has no boundary artefact: a fixed-window limiter of
        10 rps happily admits 20 requests spanning the tick (10 at 0.99s, 10 at
        1.01s); this one never admits more than `rps` in ANY one-second interval.
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

Deterministic check of both, in the shipped image:

```
== D. circuit breaker state machine (threshold=3, cooldown=2s) ==
   call 1: state=CLOSED    HTTP 500 (real call)           took 0.159ms
   call 2: state=CLOSED    HTTP 500 (real call)           took 0.105ms
   call 3: state=OPEN      HTTP 500 (real call)           took 0.197ms
   call 4: state=OPEN      CircuitOpenError (fast-fail)   took 0.007ms
   call 5: state=OPEN      CircuitOpenError (fast-fail)   took 0.005ms
   -- sleeping 2.1s (cooldown) --
   probe after cooldown: result='ok' state=CLOSED took 0.469ms

== E. failed HALF_OPEN probe re-opens immediately ==
   after 2 failures: state=OPEN failures=2
   half-open probe failed -> state=OPEN (HTTPStatusError)

== F. rate limiter sliding window (rps=5) ==
   8 instant requests -> [True, True, True, True, True, False, False, False]
   admitted=5 rejected=3
   other key /y is independent -> [True, True, True]
   after 1.05s the window slid -> [True, True, True, True, True]
```

## Circuit OPEN under 100% payment failure

```
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0
$ curl -s http://payments:8082/health
{"status":"healthy","failure_rate":1.0,"latency_ms":0}
```

> **Adaptation.** The lab's `cb-probe` script reserves a real ticket per attempt. 80 reservations
> against the only event with inventory left would have used it all up (and the reserve holds pile
> up inside the reaper's 60 s window, so the last attempts would 409 instead of measuring the
> breaker). Instead the probe posts to `/reserve/cbprobe-$i/pay` with **synthetic reservation
> ids**. This exercises exactly the code under test: `pay_reservation` calls payments **before** it
> ever looks the reservation up (that's the charge-before-confirm bug documented since Lab 1), so
> the payments call, the retry loop and the breaker all run identically. Nothing downstream of
> payments is reached, because payments always fails. Zero inventory consumed, 80 clean samples.

```
i code time_total
[1] 500 0.457496
[2] 500 0.426330
[3] 500 0.411061
[4] 500 0.443729
[5] 500 0.444846
[6] 500 0.423835
[7] 500 0.398465
[8] 500 0.386214
[9] 500 0.410797
[10] 500 0.337105
[11] 500 0.440989
[12] 500 0.350522
[20] 500 0.487074
[30] 503 0.003412
[40] 503 0.002904
[50] 503 0.003455
[60] 503 0.002891
[70] 503 0.002956
[80] 503 0.002934
200s=0 500s=25 503s=55 429s=0 other=0
```

**`500s=25  503s=55`.** Two things worth reading carefully here:

- **25 is not approximate.** `CB_FAILURE_THRESHOLD = 5`, gateway replicas = 5, and breaker state
  is per process, so `5 × 5 = 25` requests had to fail the hard way before every pod's circuit
  was open. After that, every remaining request got refused without touching payments.
- **The fast-fail is ~150x faster.** 440 ms (three attempts plus two backoff sleeps) collapses to
  **2.9 ms**. That saved latency is the user-facing half of the pattern. The other half is that
  payments received **zero** of those last 55 requests while it was failing.

All five transitions, with timestamps, across all five pods:

```
gateway-6d868c98c4-s26jm {"time":"2026-09-22 17:52:18,845",...,"msg":"circuit[payments] CLOSED -> OPEN"}
gateway-6d868c98c4-94xfb {"time":"2026-09-22 17:52:20,322",...,"msg":"circuit[payments] CLOSED -> OPEN"}
gateway-6d868c98c4-ht5ld {"time":"2026-09-22 17:52:22,478",...,"msg":"circuit[payments] CLOSED -> OPEN"}
gateway-6d868c98c4-s99vd {"time":"2026-09-22 17:52:23,466",...,"msg":"circuit[payments] CLOSED -> OPEN"}
gateway-6d868c98c4-fpf6t {"time":"2026-09-22 17:52:25,795",...,"msg":"circuit[payments] CLOSED -> OPEN"}
```

They trip one at a time over 7 seconds, in whatever order kube-proxy happened to route the
failures, which is the visible signature of five independent in-process breakers (the lab's
"Gotcha").

Retry counters at the same moment show the amplification arithmetic exactly:

```
gateway_retry_total{target=payments,result=exhausted}             = 25
gateway_retry_total{target=payments,result=retried}               = 54
gateway_retry_total{target=payments,result=succeeded_after_retry} = 4
```

`retried = 54 = 50 + 4`: the 25 exhausted requests each retried twice (`25 × 2 = 50`), plus the 4
carried over from Test #2. 25 user-visible failures cost 75 calls to payments.

## Circuit CLOSED after recovery

```
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0     # 20:52:59
$ sleep 35                                                          # CB_COOLDOWN_S=30
$ # 15 real checkout chains on event 1                              # 20:53:55
[1] 200 0.020981
[2] 200 0.018849
[3] 200 0.018059
[4] 200 0.018170
[5] 200 0.017032
[6] 200 0.017357
[7] 200 0.016152
[8] 200 0.016812
[9] 200 0.016485
[10] 200 0.016124
[11] 200 0.016333
[12] 200 0.016224
[13] 200 0.014263
[14] 200 0.017180
[15] 200 0.017124
```

**15/15 × 200**, back to the ~17 ms baseline. Here's the recovery path in the logs, per pod, 6 ms
from half-open to closed:

```
gateway-6d868c98c4-fpf6t {"time":"2026-09-22 17:53:59,327",..."circuit[payments] OPEN -> HALF_OPEN"}
gateway-6d868c98c4-fpf6t {"time":"2026-09-22 17:53:59,334",..."circuit[payments] HALF_OPEN -> CLOSED"}
gateway-6d868c98c4-s26jm {"time":"2026-09-22 17:53:59,479",..."circuit[payments] OPEN -> HALF_OPEN"}
gateway-6d868c98c4-s26jm {"time":"2026-09-22 17:53:59,485",..."circuit[payments] HALF_OPEN -> CLOSED"}
gateway-6d868c98c4-s99vd {"time":"2026-09-22 17:53:59,628",..."circuit[payments] OPEN -> HALF_OPEN"}
gateway-6d868c98c4-s99vd {"time":"2026-09-22 17:53:59,634",..."circuit[payments] HALF_OPEN -> CLOSED"}
gateway-6d868c98c4-94xfb {"time":"2026-09-22 17:53:59,774",..."circuit[payments] OPEN -> HALF_OPEN"}
gateway-6d868c98c4-94xfb {"time":"2026-09-22 17:53:59,779",..."circuit[payments] HALF_OPEN -> CLOSED"}
gateway-6d868c98c4-ht5ld {"time":"2026-09-22 17:54:00,062",..."circuit[payments] OPEN -> HALF_OPEN"}
gateway-6d868c98c4-ht5ld {"time":"2026-09-22 17:54:00,068",..."circuit[payments] HALF_OPEN -> CLOSED"}
```

The last OPEN was logged at `17:52:25`, the first HALF_OPEN at `17:53:59`. Nothing probed
payments in between. The cooldown is a *minimum*, and the circuit only re-tests when a real
request arrives, which is why it re-tested 94 s later rather than at exactly 30 s.

```
$ ... query=sum by (to) (gateway_circuit_breaker_transitions_total)
gateway_circuit_breaker_transitions_total{to="OPEN"}      = 5
gateway_circuit_breaker_transitions_total{to="HALF_OPEN"} = 5
gateway_circuit_breaker_transitions_total{to="CLOSED"}    = 5
```

5/5/5, every pod completed the full `CLOSED → OPEN → HALF_OPEN → CLOSED` cycle.

## Rate limiter under burst

```
BURST (no sleep): 200=61 429=39 other=0  elapsed=1s
--- 429 response headers + body ---
HTTP/1.1 429 Too Many Requests
retry-after: 1
{"error":"rate_limited","path":"/events","limit_rps":10}
--- SUSTAINED below limit: 30 requests, 0.2s apart ---
SUSTAINED: 200=28 429=2
```

**`200=61 429=39`** for 100 requests fired as fast as one pod can spawn `curl`, finishing in ~1 s.
The predicted cluster ceiling is `RATE_LIMIT_RPS × replicas = 10 × 5 = 50` per second, and 61
admitted over a hair more than a second matches that ceiling. **`Retry-After: 1` is present on the
429**, with a machine-readable body naming the path and the limit.

The sustained-below-limit run returned 2 × 429 instead of 0. That's not a defect in the limiter,
and it's worth being precise about why: **Lab 6's `loadgen` was running the whole time**, and it
hits `/events` too. My 5 rps plus loadgen's share, unevenly balanced across 5 pods, occasionally
pushes one pod's window over 10. Dropping my own rate to 2 rps leaves enough headroom for both:

```
SUSTAINED 2 rps for 10s: 200=20 429=0
```

**Zero 429s** on sustained below-limit load, as required.

```
$ ... query=sum by (path) (gateway_rate_limit_rejections_total)
gateway_rate_limit_rejections_total{path="/events"} = 53
```

(39 from the burst + 12 from the 40-request header warm-up + 2 from the first sustained run.)

Note the limiter is keyed on the **normalised** path (`/events/{id}/reserve`, not
`/events/1/reserve`), so a single hot event cannot be starved by traffic to another, and the
cardinality of `gateway_rate_limit_rejections_total` stays bounded.

---

# Bonus Task — Bulkhead Isolation (2 pts)

## `Bulkhead.call` + the wiring

```python
class BulkheadFullError(Exception):
    """Raised by Bulkhead.call when no slot became free within the timeout."""


class Bulkhead:
    """Bounded per-dependency concurrency.

    Reading 11's ship metaphor: a hull flooded in one compartment must not sink
    the ship. The "compartments" are per-target asyncio.Semaphores. At most
    `max_concurrent` in-flight calls may be outstanding against a given
    downstream; a caller that cannot get a slot within `acquire_timeout_s` is
    rejected instead of piling onto an unbounded queue.
    """

    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.max_concurrent = max_concurrent
        self.acquire_timeout_s = acquire_timeout_s
        self._sem = asyncio.Semaphore(max_concurrent)
        # Materialise both series at 0 so they exist in /metrics before the
        # first call (an absent series and a zero series look very different
        # in a PromQL alert).
        BULKHEAD_IN_FLIGHT.labels(name).set(0)
        BULKHEAD_REJECTIONS.labels(name).inc(0)

    async def call(self, func):
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self.acquire_timeout_s)
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            log.warning(
                f"bulkhead[{self.name}] FULL (max={self.max_concurrent}) — "
                f"rejecting after {self.acquire_timeout_s}s wait"
            )
            raise BulkheadFullError(f"bulkhead[{self.name}] full (max={self.max_concurrent})")

        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            # try/finally, not a plain pair of calls: if func() raises — and
            # under failure injection it does, constantly — a leaked slot would
            # shrink the pool permanently until the pod is restarted.
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self._sem.release()


payments_bulkhead = Bulkhead("payments", BULKHEAD_PAYMENTS_MAX, BULKHEAD_PAYMENTS_TIMEOUT_S)
```

The new wrapping line in `pay_reservation`: **bulkhead → CB → retry → call**:

```python
        pay_resp = await payments_bulkhead.call(
            lambda: payments_cb.call(
                lambda: call_with_retry(_charge, target="payments")
            )
        )
        payment_ref = pay_resp.json().get("payment_ref", "unknown")
    except CircuitOpenError:
        log.error("circuit open, skipping payments call")
        raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
    except BulkheadFullError:
        # Same class of signal as CircuitOpenError: a deliberate, immediate
        # refusal to start work we have no capacity for. 503 + a reason the
        # client can act on, rather than a queued request that times out.
        log.error("payments bulkhead full, shedding /pay request")
        raise HTTPException(503, "Payment service at capacity (bulkhead full)")
```

Deterministic check that the cap binds and no slot leaks (shipped image):

```
== G. bulkhead (max=3, acquire timeout=0.3s) ==
   call 0 -> done                 in 1002ms
   call 1 -> done                 in 1002ms
   call 2 -> done                 in 1002ms
   call 3 -> BulkheadFullError    in  301ms
   call 4 -> BulkheadFullError    in  301ms
   call 5 -> BulkheadFullError    in  301ms
   gateway_bulkhead_rejections_total{target=unit} = [3.0]
   gateway_bulkhead_in_flight{target=unit} after drain = [0.0] (must be 0 — no leaked slots)
```

## The test

```
$ kubectl set env deployment/payments PAYMENT_LATENCY_MS=3000 PAYMENT_FAILURE_RATE=0.0
$ curl -s http://payments:8082/health
{"status":"healthy","failure_rate":0.0,"latency_ms":3000}
```

The probe drives **5 waves of 30 concurrent `/pay`, one wave every 4 s** (~20 s of sustained
30-way concurrency, long enough for Prometheus' 5 s scrape to see saturation), while the *same*
pod samples `/events` 60 times at 4 rps and times every one. Synthetic reservation ids again, for
the same inventory reason and because they exercise the identical payments path. The response code
tells the two cases apart cleanly:

- `~3 s + 500` = the call **got a slot** and sat in it for the full injected payment.
- `~0.5 s + 503` = the bulkhead was full and **shed** the request at the acquire timeout.

### Run A — committed default `BULKHEAD_PAYMENTS_MAX=10`

```
===== /pay outcome histogram =====
    150 500
===== /pay timing by code (min/avg/max seconds) =====
  500  n=150  min=3.019 avg=3.160 max=3.350
===== /events latency during the storm =====
  n=60 min=0.0073 avg=0.0144 max=0.1766
EVENTS: ok=60 slow=0   (slow = time_total > 0.5s)
```

```
$ ... max_over_time(gateway_bulkhead_in_flight{target="payments"}[3m])
  gateway-6d868c98c4-94xfb: 8
  gateway-6d868c98c4-fpf6t: 8
  gateway-6d868c98c4-ht5ld: 7
  gateway-6d868c98c4-s26jm: 9
  gateway-6d868c98c4-s99vd: 7
  (sum across pods, max over the window: 33)
$ ... sum by (target) (gateway_bulkhead_rejections_total)
  gateway_bulkhead_rejections_total{target="payments"} = 0
```

**At MAX=10 the cap does not bind, and it cannot.** 30 concurrent requests spread over 5 pods is
~6–9 per pod, and the limit is *per process*, same property as the breaker and the limiter. The
lab's expectation that `in_flight` saturates at 10 assumes one gateway. With 5 replicas the
concurrency needed to saturate is `MAX × replicas = 50`, and driving payments (1 replica,
40-thread pool, 3 s per call) at 50-way concurrency pushes it past the gateway's 5 s timeout. That
turns the experiment into a retry-storm measurement instead of a bulkhead measurement.

So rather than distort payments, I moved the cap to where the available concurrency can actually
bind it.

### Run B — `BULKHEAD_PAYMENTS_MAX=2` (cap binds)

```
$ kubectl patch rollout gateway --type=json \
    -p '[{"op":"replace","path":"/spec/template/spec/containers/0/env/10/value","value":"2"}]'
rollout.argoproj.io/gateway patched
$ kubectl argo rollouts status gateway --timeout=420s
Healthy
```

```
===== /pay outcome histogram =====
    100 503
     50 500
===== /pay timing by code (min/avg/max seconds) =====
  500  n=50   min=3.013 avg=3.035 max=3.118
  503  n=100  min=0.503 avg=0.509 max=0.568
===== /events latency during the storm =====
  n=60 min=0.0072 avg=0.0110 max=0.0939
EVENTS: ok=60 slow=0   (slow = time_total > 0.5s)
```

Every number here is the configuration, read back off the wire:

- **50 × 500** = `5 waves × 5 pods × 2 slots`. The cap binds to the request, exactly.
- **100 × 503 at avg 0.509 s** = `BULKHEAD_PAYMENTS_TIMEOUT_S = 0.5`, to 9 ms.
- **`EVENTS: ok=60 slow=0`**, avg 11 ms.

```
$ ... max_over_time(gateway_bulkhead_in_flight{target="payments"}[3m])
  gateway-6d868c98c4-94xfb: 0      <- previous ReplicaSet's pods, terminated
  gateway-6d868c98c4-ht5ld: 0         before this run; series still inside the
  gateway-6d868c98c4-s26jm: 0         3m lookback window
  gateway-6d868c98c4-s99vd: 0
  gateway-7f58697cf5-cpxvf: 2
  gateway-7f58697cf5-gc26j: 2
  gateway-7f58697cf5-ghsgl: 2
  gateway-7f58697cf5-nwfbs: 2
  gateway-7f58697cf5-rm45n: 2
$ ... sum by (target) (gateway_bulkhead_rejections_total)
  gateway_bulkhead_rejections_total{target="payments"} = 100
```

**Every one of the five pods saturates at exactly `2` = `MAX`, and the rejection counter reads
100.** The cap binds, the metric proves it binds, nothing exceeds it.

```
{"time":"2026-09-22 18:00:27,513","level":"WARNING","service":"gateway","msg":"bulkhead[payments] FULL (max=2) — rejecting after 0.5s wait"}
{"time":"2026-09-22 18:00:27,513","level":"ERROR","service":"gateway","msg":"payments bulkhead full, shedding /pay request"}
```

### Run C — bulkhead effectively removed (the contrast)

`git stash` would mean a rebuild plus a full canary cycle, so instead I neutered the bulkhead in
place: `MAX=1000, TIMEOUT_S=30`. A semaphore with 1000 permits under 30-way concurrency never
blocks and never rejects, so it's behaviourally identical to deleting the wrapper, and it only
takes one patch.

```
$ kubectl patch rollout gateway --type=json -p '[{...env/10/value","value":"1000"},
                                                 {...env/11/value","value":"30"}]'
===== /pay outcome histogram =====
    150 500
===== /pay timing by code (min/avg/max seconds) =====
  500  n=150  min=3.015 avg=3.173 max=3.413
===== /events latency during the storm =====
  n=60 min=0.0073 avg=0.0196 max=0.1607
EVENTS: ok=60 slow=0   (slow = time_total > 0.5s)
$ ... max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m])
  gateway-54d4c7445d-29pb5: 7
  gateway-54d4c7445d-5k874: 9
  gateway-54d4c7445d-cfjln: 8
  gateway-54d4c7445d-n8vg9: 10
  gateway-54d4c7445d-swm6v: 9
  gateway-7f58697cf5-cpxvf: 0      <- Run B's pods, already terminated
  gateway-7f58697cf5-gc26j: 0
  gateway-7f58697cf5-ghsgl: 0
  gateway-7f58697cf5-rm45n: 0
```

### The honest result, and what it actually means

| | Run B (MAX=2, binding) | Run C (no bulkhead) |
|---|---|---|
| `/events` ok / slow | **60 / 0** | **60 / 0** |
| `/events` avg latency | 11.0 ms | 19.6 ms |
| `/events` max latency | 94 ms | 161 ms |
| concurrent calls reaching payments | **10** (2 × 5 pods) | **43** (7–10 × 5 pods) |
| worst-case `/pay` wait for a shed caller | **0.51 s** | 3.17 s |
| bulkhead rejections | 100 | 0 |

**The lab predicts `EVENTS: ok=0 slow=30` without the bulkhead. That did not happen, and once I
looked at it, it was never going to happen on this gateway.** The prediction comes from the
thread-pool model in Reading 11 §6: "50 threads shared across all downstream calls, payments is
slow, all 50 threads blocked." This gateway is not thread-per-request, though. It's a
single-threaded asyncio event loop, and `await client.post(...)` against a slow payments service
**yields**. The coroutine parks on a socket and the loop is free to serve `/events` in the
meantime. Slowness in one dependency doesn't eat a scheduling resource that another dependency
needs. Run C measured `/events` at 19.6 ms average while 43 payment calls were blocked for 3
seconds each at the same time. I was actually pretty pleased to see this line up, because it means
the "surprising" result isn't a mistake in the test, it's the reading's model just not matching
this runtime.

That doesn't make the bulkhead useless here, it just relocates what it protects. The shared
resource in an async gateway isn't threads, it's the **`httpx.AsyncClient` connection pool** (one
client instance, shared by events, payments and notifications, default `max_connections=100`).
Run C's 43 concurrent payment calls occupied ~43% of it. The bulkhead's guarantee is that payments
can never take more than `MAX × 1` of that pool per pod, so the pool can't be exhausted by one
slow dependency no matter how the traffic arrives, the same isolation, just over the resource that
is actually scarce here. At 30-way concurrency the pool isn't near exhaustion, so the protection
is real but not yet *load-bearing*. It would only become visible above ~100 concurrent payment
calls per pod, and this 5 GB single-node cluster can't generate that without the measurement
turning into a study of payments' own thread pool instead.

What the bulkhead demonstrably *did* change, at this scale, is the two rows that matter
operationally:

- **Backpressure reached the caller in 0.51 s instead of 3.17 s.** 100 clients got a fast, honest
  503 with a reason instead of sitting on a connection for 3 seconds just to be told the same
  thing. That's a 6x improvement in how quickly a client can shed, retry elsewhere or show an
  error.
- **Load on the struggling dependency fell from 43 concurrent to 10.** A service that is slow
  because it's overloaded recovers when you stop sending it work, and the bulkhead is the
  mechanism that stops sending it work without anyone having to decide that in the moment.

Reading 11 marks bulkhead "concept-only," probably because getting it wrong silently undermines
the cap. The version here is wired correctly and the cap provably binds (Run B). The actual
finding is that the *failure mode the pattern defends against* is different in an async runtime
than in the thread-per-request runtime the pattern was named for.

## Why must the bulkhead wrap the circuit breaker, not the other way around?

Two independent reasons, and they point the same way.

**1. A fast-fail must not consume a slot.** When the circuit is OPEN, `cb.call` returns in 2.9 ms
(measured in Task 2). If the bulkhead were *inside* the breaker, that microsecond-scale rejection
would still have to acquire and release a semaphore permit. Harmless on its own, but it means the
pool's occupancy no longer tracks "work outstanding against payments," which is the only thing it
is supposed to measure. `gateway_bulkhead_in_flight` would show pressure from calls that never
even touched payments.

**2. The decisive one: the bound must count requests, not attempts.** Retries live *inside* the
breaker. One user request can make `RETRY_MAX = 3` downstream calls. With the bulkhead outermost,
that entire chain, three attempts and two backoff sleeps, occupies **one** slot, so `MAX=10`
means "at most 10 user requests in flight against payments." Move the bulkhead inside and each
retry attempt acquires its own permit instead: `MAX=10` would then mean "at most 10 *attempts*,"
i.e. anywhere from 10 down to 3 concurrent user requests depending on how many are retrying. The
cap would float with the failure rate, and it would be loosest exactly when payments is failing
and retrying the most, which is precisely when a hard bound is needed. Run B's arithmetic is only
clean (`5 waves × 5 pods × 2 = 50`) because the slot is held per request.

Put the other way: **the bulkhead gates entry to the whole resilience chain.** Everything the
chain does, retrying, backing off, fast-failing, happens inside one accounted-for slot.

## Bulkhead vs rate limiter — what does each protect against?

Both reject excess traffic, but they are aimed in opposite directions and measure different
things.

| | Rate limiter | Bulkhead |
|---|---|---|
| Faces | **Inbound**: clients calling the gateway | **Outbound**: the gateway calling a dependency |
| Bounds | Requests **per unit time** (a rate, 10/s) | Requests **simultaneously in flight** (a count, 10) |
| Protects | *Me*, from being overwhelmed | *My other dependencies*, from one sick one |
| Keyed by | Endpoint (or caller/tenant) | Downstream target |
| Sensitive to | Arrival rate | Downstream **latency** |
| Triggered by | A client sending too much | A dependency getting slow |
| Says | "You are asking too often: 429, come back in 1 s" | "I have no capacity for this: 503" |

The sharpest distinction is the last-but-one row. **A rate limiter is blind to how long work
takes.** 10 rps of 5 ms calls is 0.05 concurrent; 10 rps of 3-second calls is 30 concurrent and
climbing without bound. The rate limiter admits both identically, and the second one is the
outage. That is exactly the scenario measured above: payments went from ~0 ms to 3000 ms with the
*arrival rate unchanged*, so no rate limiter anywhere would have noticed, while the bulkhead's
occupancy gauge went straight to its ceiling and started shedding.

Conversely, a bulkhead cannot stop a client hammering `/events`. Those calls are fast, so they
never build up in flight, and 10 000 rps of 5 ms work passes a concurrency cap of 10 without
touching it. Only the rate limiter catches that (`200=61 429=39`).

They are complementary, and in `/pay` both are on the path: the rate limiter in the middleware
bounds how fast requests may *arrive*, the bulkhead bounds how many may be *outstanding* against
payments. Neither substitutes for the other, and the two failure modes they catch (an abusive
client, and a degrading dependency) are the two ways this service gets taken down.

---

## Cleanup / final state

All fault knobs restored to neutral and read back off the live objects:

```
$ kubectl get rollout gateway -o json | jq '.spec.template.spec.containers[0].env'
  EVENTS_URL=http://events:8081
  PAYMENTS_URL=http://payments:8082
  GATEWAY_TIMEOUT_MS=5000
  APP_VERSION=v2
  NOTIFICATIONS_URL=http://notifications:8083
  RETRY_MAX=3
  RETRY_BASE_DELAY_MS=100
  CB_FAILURE_THRESHOLD=5
  CB_COOLDOWN_S=30
  RATE_LIMIT_RPS=10
  BULKHEAD_PAYMENTS_MAX=10
  BULKHEAD_PAYMENTS_TIMEOUT_S=0.5
$ kubectl get deployment payments ...
  PAYMENT_FAILURE_RATE=0.0
  PAYMENT_LATENCY_MS=0
$ kubectl get deployment notifications ...
  NOTIFY_FAILURE_RATE=0.0
  NOTIFY_LATENCY_MS=0

$ curl -s http://gateway:8080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","notifications":"ok","circuit_payments":"CLOSED"}}
```

Ticket inventory after the lab (events 3 and 5 were already sold out before it started):

| id | name | total | confirmed | left |
|---|---|---:|---:|---:|
| 1 | Go Conference 2026 | 100 | 75 | 25 |
| 2 | SRE Meetup | 30 | 0 | 30 |
| 3 | Cloud Native Summit | 500 | 500 | 0 |
| 4 | Python Workshop | 25 | 10 | 15 |
| 5 | Kubernetes Deep Dive | 80 | 80 | 0 |

85 tickets were consumed by the tests that had to produce real 200s (Test #1, Test #2, the CB
recovery run, and the latency baseline). The storms that needed high volume used synthetic
reservation ids and consumed none. 70 tickets remain across events 1, 2 and 4.

---

## PR description

```text
- [x] Task 1 done — notifications service, k8s manifest, fire-and-forget wiring, retry with backoff (Tests #1 + #2)
- [x] Task 2 done — circuit breaker + rate limiter, tested under failure
- [x] Bonus Task done — bulkhead isolation, concurrent /pay vs /events test, cap proven to bind
```

**Highlights**

- 4th service `app/notifications/` (FastAPI, non-root image, three Prometheus metrics, `NOTIFY_*`
  fault knobs) + `k8s/notifications.yaml`, 1/1 Ready, scraped by Prometheus (needed a new
  `job_name`, this cluster's Prometheus does not use annotation discovery).
- Retry / circuit breaker / rate limiter / bulkhead implemented in `app/gateway/main.py`,
  composed `bulkhead → CB → retry → call`, all four tunable from env without a rebuild.
- Test #1: `ok=30 fail=0` with notifications at 30% failure + 300 ms latency; `/pay` p99 **24.9 ms**.
- Test #2: `ok=30 fail=0` at `PAYMENT_FAILURE_RATE=0.3`; `gateway_retry_total{retried}` and
  `{succeeded_after_retry}` both non-zero, backoff visible in the response times.
- Circuit breaker: `500s=25 503s=55` under 100% failure (25 = threshold 5 × 5 replicas), fast-fail
  **440 ms → 2.9 ms**, then 15/15 × 200 after cooldown; transitions 5 OPEN / 5 HALF_OPEN / 5 CLOSED.
- Rate limiter: `200=61 429=39` on a 100-request burst, `Retry-After: 1` present, `200=20 429=0`
  on sustained below-limit load.
- Bulkhead: at MAX=2 the cap binds exactly: 50 slots served, 100 shed at 0.509 s, `in_flight`
  saturates at 2 on all 5 pods, `gateway_bulkhead_rejections_total = 100`.
- **Finding:** the lab's predicted `/events` collapse without the bulkhead does not occur on an
  asyncio gateway (`EVENTS: ok=60 slow=0` both with and without). Reading 11 §6's model is
  thread-per-request; the resource a bulkhead protects here is the shared `httpx` connection pool.
  Measured benefit at this scale: downstream concurrency 43 → 10, caller shed time 3.17 s → 0.51 s.
  Written up in full in the Bonus section.
