# Lab 11 — Advanced Microservice Patterns

Environment: local k3d cluster `quickticket`. Gateway is an Argo Rollouts `Rollout` (5 replicas)
from Lab 7, plus events / payments / postgres / redis and the new **notifications** service. In-cluster
Prometheus (Lab 7 Bonus) scrapes the gateway pods. Checkout traffic from `labs/lab8/mixedload.yaml`
runs throughout. All four patterns (retry, circuit breaker, rate limiter, bulkhead) were implemented in
`app/gateway/main.py` and exercised with real fault injection.

---

## Task 1 — Notifications Service + Retries

### 1. `app/notifications/main.py` (key bits) + `requirements.txt`

The notifications service copies the payments template: a mock destination with tunable fault injection
(`NOTIFY_FAILURE_RATE`, `NOTIFY_LATENCY_MS`) and the three required Prometheus metrics.

```python
NOTIFY_TOTAL = Counter("notifications_notify_total", "Total notify attempts", ["result"])

@app.post("/notify")
def notify(body: dict = None):
    event = (body or {}).get("event", "unknown")
    order_id = (body or {}).get("order_id", "unknown")
    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        raise HTTPException(500, "Notification delivery failed")
    NOTIFY_TOTAL.labels("success").inc()
    return {"status": "sent", "event": event, "order_id": order_id}
```

Metrics emitted: `notifications_requests_total{method,path,status}`,
`notifications_request_duration_seconds{method,path}`, `notifications_notify_total{result}`.

`requirements.txt` (identical to payments — no DB, no Redis):

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

`Dockerfile` is the payments Dockerfile with the port changed to 8083.

### 2. `k8s/notifications.yaml`

Deployment + Service in one file: 1 replica, `image: quickticket-notifications:v1`,
`imagePullPolicy: Never`, container/Service port 8083, `NOTIFY_FAILURE_RATE`/`NOTIFY_LATENCY_MS` env
defaults, `app=notifications` selector/labels. (Full file committed in the PR.)

The gateway learns about it via one env var added to `k8s/gateway.yaml`:

```yaml
- name: NOTIFICATIONS_URL
  value: "http://notifications:8083"
```

Pod comes up 1/1 Ready:

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl get pod -l app=notifications
NAME                            READY   STATUS    RESTARTS   AGE
notifications-dccd599cc-8jgfr   1/1     Running   0          2m
```

### 3. `call_with_retry()` implementation

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    base_delay = RETRY_BASE_DELAY_MS / 1000
    last_exc = None
    for attempt in range(max_retries):
        try:
            result = await func()
            if attempt > 0:
                RETRY_TOTAL.labels(target=target, result="succeeded_after_retry").inc()
            return result
        except Exception as exc:
            last_exc = exc
            retryable = isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                retryable = status >= 500 or status in (408, 429)
            if not retryable:                       # 404, 422 — won't fix itself
                RETRY_TOTAL.labels(target=target, result="non_retryable").inc()
                raise
            if attempt == max_retries - 1:          # out of attempts
                RETRY_TOTAL.labels(target=target, result="exhausted").inc()
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)  # backoff + jitter
            RETRY_TOTAL.labels(target=target, result="retried").inc()
            await asyncio.sleep(delay)
    raise last_exc
```

### 4. Test #1 — fire-and-forget under notify failure

Injected 30% notification failures + 300ms latency, then fired 30 checkout chains:

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
deployment.apps/notifications env updated

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl run checkout-burst ... -- sh -c '...30 reserve+pay...'
result: ok=30 fail=0
```

`/pay` p99 was **not** inflated by the injected 300ms — proving the notify call is genuinely
non-blocking:

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B2m%5D)))'
  path=/reserve/{id}/pay  =>  0.0219 s   (~22 ms, well under 100 ms)
```

### 5. Test #2 — retries fire under transient payment failure

Injected 30% payment failures, fired 30 more checkouts:

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl run retry-test ... -- sh -c '...30 reserve+pay...'
result: ok=30 fail=0
```

30% first-try failure × 3 attempts ⇒ all-three-fail ≈ 0.3³ ≈ 2.7%, so ~30/30 succeed. Retries
actually fired (both counters non-zero):

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(target,result)+(gateway_retry_total)'
  target=payments result=retried                => 13
  target=payments result=succeeded_after_retry  => 11
```

### 6. Real notify failure rate (from the notifications pod `/metrics`)

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ curl -s http://notifications:8083/metrics | grep '^notifications_notify_total'
notifications_notify_total{result="success"} 20.0
notifications_notify_total{result="failed"} 10.0
```

10/30 ≈ **33%** real failure — matching the injected 0.3 — yet every one of the 30 checkouts still
returned 200. The failed notifications were swallowed by the fire-and-forget task.

### 7. Why should notifications be non-blocking (fire-and-forget)?

Sending a confirmation e-mail/push is **not** part of the money-and-inventory transaction. The payment
has already been charged and the order confirmed by the time we notify. If the notification call were
`await`-ed inline, then (a) the injected 300ms latency would be added to every user's `/pay` p99, and
(b) a notification-service outage (30% here) would turn into user-visible 5xx on checkout — the tail
would wag the dog. By running it as `asyncio.create_task(...)` the user request returns immediately
after the confirm step; the notification is best-effort. Test #1 proves both: p99 stayed at ~22ms and
ok=30/30 despite 33% notify failures. This is also why the gateway `/health` deliberately does **not**
gate `critical_ok` on notifications — a best-effort dependency being down must not mark the system down.

### 8. Design prompt (11.4) — why `cb.call(retry(_charge))`, not `retry(cb.call(_charge))`?

The composition is **retry INSIDE the circuit breaker**:
`payments_cb.call(lambda: call_with_retry(_charge, "payments"))`.

- The circuit breaker should see the **final** outcome of a fully-retried call as *one* success or
  *one* failure. That keeps its failure count meaningful: "5 real failures trip the circuit," where each
  "failure" already means "we tried 3 times and still couldn't reach payments."
- The reverse, `retry(lambda: cb.call(_charge))`, is wrong because once the circuit is **OPEN** it
  fast-fails with `CircuitOpenError`. The retry loop would treat that as just another error and keep
  retrying — hammering a breaker whose whole purpose is to *stop* traffic. Worse, a `CircuitOpenError`
  isn't even one of our retryable transient types, so the retry would (correctly) re-raise it, but the
  intent is still inverted: you'd be retrying *around* the breaker instead of letting the breaker gate
  the retries. The breaker must be the outermost guard so an OPEN circuit short-circuits the entire
  retry budget in one fast-fail.

> Note (from Pitfalls): because retry is inside the CB, one "failure" the CB counts is up to 3 downstream
> calls. Under the CB test below, 5 external failures per pod = up to 15 real payment calls per pod. That
> amplification is expected; it's the cost of retry-then-trip.

---

## Task 2 — Circuit Breaker + Rate Limiter

### `CircuitBreaker.call` implementation

```python
async def call(self, func):
    if self.state == self.OPEN:
        if time.time() - self.opened_at >= self.cooldown:
            self._transition(self.HALF_OPEN)          # cooldown elapsed → probe
        else:
            raise CircuitOpenError(f"circuit[{self.name}] OPEN")   # fast-fail
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

### `RateLimiter.allow` implementation

```python
def allow(self, key: str) -> bool:
    now = time.time()
    q = self.hits[key]
    cutoff = now - self.window_s
    while q and q[0] < cutoff:      # evict timestamps older than 1s
        q.popleft()
    if len(q) >= self.rps:
        return False
    q.append(now)
    return True
```

### Circuit OPENs under 100% payment failure

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl run cb-probe ... 80 attempts ...
500s=25 503s=43
```

- **500** = retry-exhausted (all 3 attempts failed, circuit still counting up).
- **503** = fast-fail because the circuit is OPEN — 43 of 68 attempts short-circuited without touching
  payments.

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(to)+(gateway_circuit_breaker_transitions_total)'
  to=OPEN => 5
```

One OPEN transition per gateway pod — with 5 replicas each has its own per-process breaker, exactly as
the lab's gotcha predicts (that's why we need ≥40–80 requests before every pod's circuit trips).

### Circuit CLOSES after recovery

Restored payments to 0% failure, waited past the 30s cooldown, probed 15 checkouts:

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl run cb-probe2 ...  # sleep 35 then 15 pays
[1] 200
[2] 200
...
[15] 200

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(to)+(gateway_circuit_breaker_transitions_total)'
  to=OPEN      => 5
  to=HALF_OPEN => 5
  to=CLOSED    => 5
```

Full state cycle proven across all 5 pods: OPEN → (cooldown) → HALF_OPEN → (trial succeeds) → CLOSED.

### Rate limiter — burst returns 429, sustained load doesn't

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl run rl-burst ... 100 rapid /events ...
200=44 429=56
```

Cluster ceiling = per-pod `RATE_LIMIT_RPS` (10) × 5 replicas ≈ 50 rps; a 100-request burst gets roughly
half limited. The 429 carries the `Retry-After` header clients use to back off:

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl run rl-hdr2 ... curl -D - ... | grep -i 429
HTTP/1.1 429 Too Many Requests retry-after: 1
```

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(path)+(gateway_rate_limit_rejections_total)'
  path=/events              => 59
  path=/events/{id}/reserve => 10
  path=/reserve/{id}/pay    => 3
```

Sustained load **below** the limit (5 rps, `sleep 0.2`) sees zero rejections:

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl run rl-slow ... 30 reqs @ 5rps ...
sustained(5rps): 200=30 429=0
```

---

## Bonus Task — Bulkhead Isolation

### `Bulkhead.call` + wiring

```python
class Bulkhead:
    def __init__(self, name, max_concurrent, acquire_timeout_s):
        self.name = name
        self.timeout = acquire_timeout_s
        self.sem = asyncio.Semaphore(max_concurrent)

    async def call(self, func):
        try:
            await asyncio.wait_for(self.sem.acquire(), timeout=self.timeout)
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(target=self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full")
        BULKHEAD_IN_FLIGHT.labels(target=self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(target=self.name).dec()
            self.sem.release()
```

Wired in `pay_reservation` **outside** the circuit breaker:

```python
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
)
...
except BulkheadFullError:
    raise HTTPException(503, "Payment service temporarily unavailable (bulkhead full)")
```

Composition outside→inside: **bulkhead → circuit breaker → retry → call.**

### Test setup

To make a *per-process* bulkhead observable I scaled the gateway to **1 replica** (otherwise 30 concurrent
`/pay` spread over 5 pods = 6 each, never reaching MAX=10 — the same per-process limitation as the circuit
breaker in Task 2) and set `RATE_LIMIT_RPS=1000` so the burst's reserve/pay calls weren't throttled before
reaching the bulkhead. Injected `PAYMENT_LATENCY_MS=3000`.

### With bulkhead (MAX=10) — cap binds, events stay fast

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl run bulkhead-probe ... 30 concurrent /pay + sample /events ...
EVENTS: ok=30 slow=0
pay 200: 10      # grabbed the 10 slots, completed after ~3s
pay 503: 12      # bulkhead full → fast-fail within the 500ms acquire timeout

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  '.../query?query=sum+by+(target)+(gateway_bulkhead_rejections_total)'
  target=payments => 12

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  '.../query?query=max_over_time(gateway_bulkhead_in_flight{target="payments"}[5m])'
  in_flight max = 10        # == BULKHEAD_PAYMENTS_MAX — the cap actually binds
```

### Without effective bulkhead (MAX=1000) — contrast

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl patch rollout gateway ... BULKHEAD_PAYMENTS_MAX=1000 ...
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl run bulkhead-probe2 ... same probe ...
EVENTS: ok=30 slow=0
pay 200: 21      # all reservations that succeeded proceeded; no 503s
```

### Honest finding

`/events` stayed **fast in both cases**. On this gateway that is the *correct* result: FastAPI + uvicorn
+ `httpx.AsyncClient` is fully asynchronous, so 30 concurrently-`await`-ed 3-second payment calls do **not**
block the event loop — asyncio just schedules them all and the loop stays free to serve `/events`. The
lab's "without bulkhead → `/events` slow=30" prediction assumes a **blocking / thread-pool** stack (sync
Flask/Django, or a saturated connection pool), where each slow call ties up a worker thread. It does not
reproduce on a non-blocking stack, and I did not fabricate a result to force it.

What the bulkhead *did* demonstrably buy us here is **load-shedding**: with the cap, 12 of the excess
requests fast-failed with 503 in under 500ms instead of hanging for 3 seconds, and in-flight was held at
exactly 10. That's the real, measured value of the bulkhead on an async gateway — bounded resource use and
fast rejection under overload, rather than event-loop protection that async already provides for free.

### Bonus design answers

**Why must the bulkhead wrap the circuit breaker, not the other way around?**
The bulkhead gates *entry* to the whole payments call. Because retries live *inside* the bulkhead slot, all
3 retry attempts for one logical `/pay` count as **one** occupant — so `MAX=10` really means "10 in-flight
payment operations," not "10 attempts" (which 3 retries would blow through instantly). If the bulkhead were
*inside* the CB, a CB fast-fail (`CircuitOpenError`) — which is meant to be instant and free — would still
have consumed a bulkhead slot on the way in. Anything that fast-fails must not hold a slot. Outermost
bulkhead = a slot is held only for the duration of a real, in-flight downstream call.

**Bulkhead vs rate limiter — what does each protect against?**
The **rate limiter** caps the *incoming request rate per endpoint* — it protects the system (and the
downstream) from too many requests per second, a cluster-wide throughput ceiling (`RPS × replicas`). The
**bulkhead** caps *concurrent in-flight calls to one specific dependency* — it protects the *other*
dependencies from a single slow one by isolating its resource pool. Rate limiter = "too many requests,
slow down" (429). Bulkhead = "this one dependency is saturated, shed load for it so the rest keep working"
(503). One bounds arrival rate; the other bounds concurrency per downstream.

---

## PR checklist

```text
- [x] Task 1 done — notifications service, k8s manifest, fire-and-forget wiring, retry with backoff (Tests #1 + #2)
- [x] Task 2 done — circuit breaker + rate limiter, tested under failure
- [x] Bonus Task done — bulkhead isolation, concurrent /pay vs /events test, cap proven to bind
```
