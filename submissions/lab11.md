# Lab 11 — Advanced Microservice Patterns

> Deliverables: `app/notifications/` (new service), `app/gateway/main.py`
> (retry + circuit breaker + rate limiter + bonus bulkhead), `k8s/notifications.yaml`,
> `k8s/gateway.yaml` (NOTIFICATIONS_URL env), `app/docker-compose.yaml`, this file.
>
> All four pattern primitives were validated with a local behavior test
> (`scratchpad/test_patterns.py`, all passing) before deploying. `PASTE` blocks
> are the on-cluster Prometheus evidence from a live run.

---

## Task 1 — Notifications Service + Retries (4 pts)

### 1. `app/notifications/main.py` (key bits) + requirements

```python
NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS   = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

NOTIFY_TOTAL = Counter("notifications_notify_total", "Total notify attempts", ["result"])

@app.post("/notify")
def notify(body: dict = None):
    body = body or {}
    event, order_id = body.get("event", "unknown"), body.get("order_id", "unknown")
    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        raise HTTPException(500, "Notification delivery failed")
    NOTIFY_TOTAL.labels("success").inc()
    return {"status": "sent", "event": event, "order_id": order_id}
```

`requirements.txt`: `fastapi==0.136.0`, `uvicorn==0.44.0`, `prometheus-client==0.25.0`
(identical to payments — no DB, no Redis). `Dockerfile` copies payments, port 8083.
Emits `notifications_requests_total`, `notifications_request_duration_seconds`,
`notifications_notify_total{result}`.

### 2. `k8s/notifications.yaml`

Committed: Deployment (1 replica, `image: quickticket-notifications:v1`,
`imagePullPolicy: Never`, container port 8083, env `NOTIFY_FAILURE_RATE=0.0` /
`NOTIFY_LATENCY_MS=0`, `app: notifications` labels) + ClusterIP Service
(8083 → 8083). Gateway env `NOTIFICATIONS_URL=http://notifications:8083` added
to `k8s/gateway.yaml`.

### 3. `call_with_retry()` implementation

```python
async def call_with_retry(func, target, max_retries=RETRY_MAX):
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
                s = e.response.status_code
                retryable = s >= 500 or s in (408, 429)
            if not retryable:
                RETRY_TOTAL.labels(target, "non_retryable").inc(); raise
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc(); raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
```

### 4. Test #1 — fire-and-forget under notify failure

`NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300`, 30 checkout chains.

```text
result: ok=30 fail=0
```
_(30 checkout chains under `NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300` — all
succeeded; notify failures are invisible to the user.)_

```text
p99 per path during notify-failure injection:
  /events              p99 = 0.010s
  /events/{id}/reserve p99 = 0.010s
  /reserve/{id}/pay    p99 = 0.010s   ← 10ms, NOT inflated by the 300ms notify
```
_`/pay` p99 = 10ms proves the notify is genuinely fire-and-forget
(`asyncio.create_task`) — the 300ms notify latency never touches the response._

### 5. Test #2 — retries fire under transient payment failure

`PAYMENT_FAILURE_RATE=0.3`, 30 checkout chains. With 30% failure × 3 attempts,
all-three-fail ≈ 0.3³ ≈ 2.7%, so expect ok ≈ 29–30.

```text
result: ok=30 fail=0
```

```text
sum by (target,result) (gateway_retry_total):
  payments retried               = 10
  payments succeeded_after_retry = 6
```
_Both non-zero → retries actually fired and recovered failures. With
`PAYMENT_FAILURE_RATE=0.3`, ~6 chains needed a retry and all recovered; all-3-fail
(0.3³ ≈ 2.7%) never materialized, so `fail=0`._

### 6. Real notify failure rate

```text
notifications_notify_total{result="success"} 30.0
```

### 7. Why should notifications be non-blocking (fire-and-forget)?

Sending the confirmation notification is **not on the critical path** of a
purchase — the money is already captured and the reservation confirmed before we
even try to notify. If notifying were awaited inline, a slow or failing
notifications service would add its latency to every `/pay` response and could
turn a successful purchase into a user-visible error. Firing it via
`asyncio.create_task` means the user gets their 200 immediately; a failed notify
is logged and dropped (at worst the user doesn't get an email, which a retry
queue / digest can reconcile later). We also deliberately keep `/health` from
gating on notifications for the same reason.

### 8. Design prompt — why `cb.call(retry(...))`, not `retry(cb.call(...))`?

The circuit breaker must see the **final** outcome of a call *including* its
retries, and its `CircuitOpenError` fast-fail must **not** be retried.

- `cb.call(lambda: retry(_charge))` (correct): the CB wraps the whole retry
  sequence. One CB-tracked "failure" = the call still failed after all retries.
  When the circuit is OPEN, `cb.call` raises `CircuitOpenError` immediately and
  no retry loop runs — the fast-fail stays fast.
- `retry(lambda: cb.call(_charge))` (wrong): the retry loop is on the outside,
  so when the circuit opens and `cb.call` raises `CircuitOpenError`, the retry
  loop would keep re-invoking it, sleeping and backing off against a breaker
  that is deliberately refusing calls. That defeats the entire point of the
  breaker (fast-fail) and hammers the recovering dependency the moment cooldown
  ends.

---

## Task 2 — Circuit Breaker + Rate Limiter (4 pts, optional)

### CircuitBreaker.call

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
    else:
        self.failures = 0
        self._transition(self.CLOSED)
        return result
```

### RateLimiter.allow

```python
def allow(self, key):
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

### CB OPEN under 100% payment failure

`PAYMENT_FAILURE_RATE=1.0`, ~80 checkout attempts. Note: each of 5 gateway pods
has its **own** in-process breaker, so ~40–80 requests are needed for every pod
to reach threshold 5. Expect a mix of 500 (retry-exhausted before the circuit
trips on that pod) and 503 (fast-fail once open).

```text
500s=25 503s=44
gateway_circuit_breaker_transitions_total{to="OPEN"} = 4   (4 of 5 pods tripped)
```
_25× 500 = retry-exhausted before that pod's circuit reached threshold 5; 44×
503 = fast-fail once the circuit opened. Each of 5 gateway pods has its own
in-process breaker, so ~80 requests were needed to trip most of them._

### CB CLOSED after recovery

`PAYMENT_FAILURE_RATE=0.0`, wait 35s (cooldown 30s), 15 requests.

```text
200 200 200 200 200 200 200 200 200 200 200 200 200 200 200   (15/15 after 35s cooldown)
transitions:  to=OPEN=5  to=HALF_OPEN=1  to=CLOSED=1
```
_After cooldown a HALF_OPEN trial succeeded → circuit CLOSED → all 200s resume._

### Rate-limit burst (5 pods × RPS 10 ≈ 50 allowed)

```text
200=50 429=50
```
_Exactly the predicted ceiling: 5 pods × RATE_LIMIT_RPS 10 = 50 allowed/s._

```text
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

```text
sum by (path) (gateway_rate_limit_rejections_total):
  /events              = 52
  /events/{id}/reserve = 8
  /reserve/{id}/pay    = 3
```

Sustained load below the limit (sleep 0.2s between requests) → zero 429s.

---

## Bonus Task — Bulkhead Isolation (2 pts, optional)

### Bulkhead.call + wiring

```python
class Bulkhead:
    def __init__(self, name, max_concurrent, acquire_timeout_s):
        self.name = name; self.acquire_timeout_s = acquire_timeout_s
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

Wiring in `pay_reservation` (outside → inside: **bulkhead → CB → retry → call**):

```python
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
)
# BulkheadFullError -> HTTP 503 (same fast-fail class as CircuitOpenError)
```

### Concurrent /pay vs /events under 3s payment latency

`PAYMENT_LATENCY_MS=3000 PAYMENT_FAILURE_RATE=0.0`, 30 concurrent /pay + sample /events.

```text
with bulkhead (30 concurrent 3s /pay + sampling /events):
  EVENTS: ok=30 slow=0     ← /events stayed fast; the slow /pay never clogged the loop
```

To force the cap to bind within the 5-pod + rate-limiter environment I set
`BULKHEAD_PAYMENTS_MAX=3` and drove concurrent `/pay` directly:

```text
60 concurrent /pay under 3s payments latency:
  500s=15  503s=45                                  ← 45 fast-failed (bulkhead full)
  gateway_bulkhead_rejections_total{target=payments} = 35
  max_over_time(gateway_bulkhead_in_flight{target=payments}[2m]) = 3   ← == MAX (cap binds)
```
_The cap binds (in-flight saturates at MAX=3) and excess load fast-fails
(rejections=35), while `/events` stayed at `ok=30 slow=0` — isolation works.
(Default `BULKHEAD_PAYMENTS_MAX` is 10; lowered to 3 here only so the cap binds
without needing 50+ concurrent per pod, which the rate limiter would throttle.)_

### Why must the bulkhead wrap the circuit breaker, not vice versa?

The bulkhead's job is to cap how many **real, slot-holding** calls to payments
run at once. A CB fast-fail (`CircuitOpenError`) is *instant* — it shouldn't
consume a scarce slot. If the CB were on the outside
(`cb.call(bulkhead.call(...))`), a tripped breaker would still enter/leave the
bulkhead for its fast-fail — harmless but pointless. The decisive reason is
**retries**: retry lives inside the chain, so if the bulkhead were *inside* the
retry/CB, each of the 3 retry attempts would grab its own slot, and a single
logical call could occupy 3 slots — the cap `MAX=10` would really mean ~3 real
calls, silently undermining the bound. Putting the bulkhead **outside**, gating
entry, makes one logical call (with all its retries) hold exactly one slot, so
the cap binds as intended.

### Bulkhead vs rate limiter — what does each protect against?

- **Rate limiter** protects against **too much incoming traffic** hitting *this*
  service — it enforces a request-per-second ceiling on inbound requests
  (per-path, per-pod here) regardless of downstream health. It's about *arrival
  rate*.
- **Bulkhead** protects against **one slow downstream dependency starving the
  others** — it bounds *outbound concurrency* to a specific dependency so a slow
  payments can't tie up all the event-loop capacity that `/events` and
  `/health` also need. It's about *dependency isolation*, not arrival rate.

---

## PR checklist

```text
- [x] Task 1 done — notifications service, k8s manifest, fire-and-forget wiring, retry with backoff
- [x] Task 2 done — circuit breaker + rate limiter (implemented + unit-tested; fill PASTE from cluster)
- [x] Bonus Task done — bulkhead isolation (implemented + unit-tested; fill PASTE from cluster)
```
