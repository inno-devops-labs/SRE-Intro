# Lab 11 Report

## Task 1 — Notifications Service + Retries

### 11.1: Notifications Service

I created `app/notifications/` based on the payments template, with three Prometheus metrics (`notifications_requests_total`, `notifications_request_duration_seconds`, `notifications_notify_total`), tunable fault injection via `NOTIFY_FAILURE_RATE` and `NOTIFY_LATENCY_MS` env vars, and `/health` + `/metrics` endpoints.

`app/notifications/requirements.txt` is identical to payments (fastapi, uvicorn, prometheus-client). The Dockerfile exposes port 8083.

### 11.2: Kubernetes Manifest

I wrote `k8s/notifications.yaml` — a Deployment (1 replica, `imagePullPolicy: Never`) + ClusterIP Service on port 8083, following the lab-4 pattern from `k8s/payments.yaml`.

### 11.3: Gateway Wiring

I added `NOTIFICATIONS_URL=http://notifications:8083` to `k8s/gateway.yaml` env. The gateway's `_notify_order_confirmed` helper (already pre-wired) started making real HTTP calls to the notifications service.

### 11.4: `call_with_retry` Implementation

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    base_delay = RETRY_BASE_DELAY_MS / 1000
    last_exc = None
    for attempt in range(max_retries):
        try:
            result = await func()
            if attempt > 0:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result
        except Exception as e:
            last_exc = e
            is_retryable = False
            if isinstance(e, (httpx.TimeoutException, httpx.ConnectError)):
                is_retryable = True
            elif isinstance(e, httpx.HTTPStatusError):
                if e.response.status_code >= 500 or e.response.status_code in (408, 429):
                    is_retryable = True

            if not is_retryable:
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                raise

            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise

            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
```

### 11.5: Test #1 — Fire-and-Forget Under Notify Failure

I injected 30% notification failures + 300ms latency:

```bash
user@MacBook-Air sre-intro % kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
deployment "notifications" env updated
```

I ran 30 checkout chains from inside the cluster:

```bash
user@MacBook-Air sre-intro % kubectl run checkout-burst --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
> ok=0; fail=0
> for i in $(seq 1 30); do
>   RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
>   RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\\([^\"]*\\).*/\\1/p")
>   if [ -z "$RID" ]; then echo "[$i] reserve failed"; fail=$((fail+1)); continue; fi
>   CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
>   if [ "$CODE" = "200" ]; then ok=$((ok+1)); else echo "[$i] pay failed: $CODE"; fail=$((fail+1)); fi
>   sleep 0.1
> done
> echo "result: ok=$ok fail=$fail"
> '
result: ok=30 fail=0
```

**Result:** `ok=30 fail=0` — notification failures are invisible to the user.

I confirmed `/pay` p99 is NOT inflated by the 300ms notification latency:

```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B2m%5D)))'

{
  "status": "success",
  "data": {"resultType": "vector", "result": [
    {"metric": {"path": "/events/{id}/reserve"}, "value": [..., "0.40977304264563824"]},
    {"metric": {"path": "/health"}, "value": [..., "0.5450646494172472"]},
    {"metric": {"path": "/events"}, "value": [..., "0.2181587501726575"]},
    {"metric": {"path": "/reserve/{id}/pay"}, "value": [..., "0.23249782204649902"]}
  ]}
}
```

**`/reserve/{id}/pay` p99 = 232ms** — well below 300ms injected notification latency, proving fire-and-forget works.

I restored notifications:

```bash
user@MacBook-Air sre-intro % kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.0 NOTIFY_LATENCY_MS=0
```

### 11.6: Test #2 — Retries Under Transient Payment Failure

I injected 30% payment failures:

```bash
user@MacBook-Air sre-intro % kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
user@MacBook-Air sre-intro % kubectl rollout status deployment/payments --timeout=30s
deployment "payments" successfully rolled out
```

I ran another 30 checkout chains:

```bash
user@MacBook-Air sre-intro % kubectl run retry-test --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
> ok=0; fail=0
> for i in $(seq 1 30); do
>   RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
>   RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\\([^\"]*\\).*/\\1/p")
>   [ -z "$RID" ] && { fail=$((fail+1)); continue; }
>   CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
>   [ "$CODE" = "200" ] && ok=$((ok+1)) || fail=$((fail+1))
>   sleep 0.1
> done
> echo "result: ok=$ok fail=$fail"
> '
result: ok=30 fail=0
```

**Result:** `ok=30 fail=0` — retries recovered all transient failures.

I verified retries actually fired via Prometheus:

```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(target,result)+(gateway_retry_total)'

{
  "status": "success",
  "data": {"resultType": "vector", "result": [
    {"metric": {"result": "retried", "target": "payments"}, "value": [..., "4"]},
    {"metric": {"result": "succeeded_after_retry", "target": "payments"}, "value": [..., "4"]}
  ]}
}
```

Both `retried=4` and `succeeded_after_retry=4` are non-zero, confirming retries actually fired.

I restored payments:

```bash
user@MacBook-Air sre-intro % kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
```

### Notify Failure Metrics

After injecting 100% notification failure and sending 10 checkout requests:

```bash
user@MacBook-Air sre-intro % kubectl exec -i $(kubectl get pod -l app=notifications -o name) -- python3 -c "
import urllib.request
print(urllib.request.urlopen('http://localhost:8083/metrics').read().decode())
" | grep notifications_notify_total

# HELP notifications_notify_total Total notify attempts
# TYPE notifications_notify_total counter
notifications_notify_total{result="failed"} 10.0
```

### Why Notifications Should Be Non-Blocking (Fire-and-Forget)

Notifications are not on the critical path of the user's request. The user pays for a ticket and expects an immediate response — whether or not an email notification was sent doesn't affect the transaction. Blocking on notifications would add latency to every `/pay` response (300ms per call in our test), and notification failures would propagate back as 500 errors to the user. Fire-and-forget ensures the user gets their ticket immediately while the notification is delivered asynchronously in the background.

### Design Prompt: Why `cb.call(retry(...))` Not `retry(cb.call(...))`

`cb.call(retry(_charge))` — retry inside CB — is correct because the circuit breaker should track the *final outcome* of a request including all its retry attempts. If a single retry attempt fails 3 times internally, that counts as one failure against the CB. Once the CB opens, retries are skipped entirely — you go from "wait 3 × timeout × backoff" to "fast-fail in microseconds."

The reverse `retry(lambda: cb.call(_charge))` is wrong because each retry iteration would ask the CB "are you open?" separately. The CB never gets a chance to fast-fail the entire call chain because the retry loop keeps asking. A truly down service would still cost `max_retries × CB-state-checks` instead of one fast-fail.

---

## Task 2 — Circuit Breaker + Rate Limiter

### 11.7: Circuit Breaker Implementation

```python
async def call(self, func):
    if self.state == self.OPEN:
        if time.time() - self.opened_at >= self.cooldown:
            self._transition(self.HALF_OPEN)
        else:
            raise CircuitOpenError(f"circuit[{self.name}] OPEN")

    try:
        result = await func()
        self.failures = 0
        self._transition(self.CLOSED)
        return result
    except Exception:
        self.failures += 1
        self.opened_at = time.time()
        if self.state == self.HALF_OPEN or self.failures >= self.threshold:
            self._transition(self.OPEN)
        raise
```

### CB Test — 100% Payment Failure

I injected 100% payment failure and ran 80 checkout attempts:

```bash
user@MacBook-Air sre-intro % kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0
deployment "payments" env updated
user@MacBook-Air sre-intro % kubectl rollout status deployment/payments --timeout=30s
deployment "payments" successfully rolled out

user@MacBook-Air sre-intro % kubectl run cb-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
> STATS_500=0; STATS_503=0
> for i in $(seq 1 80); do
>   RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
>   RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\\([^\"]*\\).*/\\1/p")
>   [ -z "$RID" ] && continue
>   CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
>   case "$CODE" in
>     500) STATS_500=$((STATS_500+1));;
>     503) STATS_503=$((STATS_503+1));;
>   esac
> done
> echo "500s=$STATS_500 503s=$STATS_503"
> '
500s=25 503s=53
```

**Result:** `500s=25` (retry-exhausted before CB tripped) + `503s=53` (circuit open, fast-fail). The CB opened and started protecting the system from wasted timeouts.

### CB Recovery — Circuit Closes After Cooldown

I restored payments to 0% failure, waited 35s (cooldown = 30s), and ran 15 requests:

```bash
user@MacBook-Air sre-intro % kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
deployment "payments" env updated
user@MacBook-Air sre-intro % sleep 35

user@MacBook-Air sre-intro % kubectl run cb-probe2 --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
> for i in $(seq 1 15); do
>   RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
>   RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\\([^\"]*\\).*/\\1/p")
>   [ -z "$RID" ] && continue
>   CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
>   echo "[$i] $CODE"
> done
> '
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

**Result:** All 200s — the circuit closed after cooldown and the HALF_OPEN probe succeeded.

### CB Prometheus Metrics

```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(to)+(gateway_circuit_breaker_transitions_total)'

{
  "status": "success",
  "data": {"resultType": "vector", "result": [
    {"metric": {"to": "OPEN"}, "value": [..., "5"]},
    {"metric": {"to": "HALF_OPEN"}, "value": [..., "5"]},
    {"metric": {"to": "CLOSED"}, "value": [..., "5"]}
  ]}
}
```

Multiple OPEN transitions (one per gateway pod) — each of the 5 pods independently tripped its own circuit breaker.

### 11.8: Rate Limiter Implementation

```python
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

### Rate Limiter — Burst Test

I fired 100 rapid requests at `/events`:

```bash
user@MacBook-Air sre-intro % kubectl run rl-burst --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
> OK=0; LIMITED=0
> for i in $(seq 1 100); do
>   CODE=$(curl -s -o /dev/null -w "%{http_code}" http://gateway:8080/events)
>   case "$CODE" in
>     200) OK=$((OK+1));;
>     429) LIMITED=$((LIMITED+1));;
>   esac
> done
> echo "200=$OK 429=$LIMITED"
> '
200=47 429=53
```

**Result:** `200=47, 429=53` — with 5 pods × 10 RPS = ~50 cluster-wide ceiling, about half the burst was rejected. Per-pod sliding window limits enforced correctly.

### Retry-After Header

```bash
user@MacBook-Air sre-intro % kubectl run rl-headers --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
> for i in $(seq 1 50); do curl -s -o /dev/null http://gateway:8080/events; done
> curl -s -D - -o /dev/null http://gateway:8080/events | grep -iE "^(HTTP|retry-after)"
> '
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

### Rate Limit Prometheus Metrics

```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(path)+(gateway_rate_limit_rejections_total)'

{
  "status": "success",
  "data": {"resultType": "vector", "result": [
    {"metric": {"path": "/reserve/{id}/pay"}, "value": [..., "2"]},
    {"metric": {"path": "/events"}, "value": [..., "68"]}
  ]}
}
```

Both endpoints show non-zero rejections. Sustained load below the limit (sleep 0.2 between requests) produces zero 429s.

---

## Bonus Task — Bulkhead Isolation

### 11.9: Bulkhead Implementation

```python
class BulkheadFullError(Exception):
    """Raised by Bulkhead.call when the concurrency cap is reached."""


class Bulkhead:
    """Per-target concurrency limiter using asyncio.Semaphore."""

    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.acquire_timeout = acquire_timeout_s

    async def call(self, func):
        try:
            await asyncio.wait_for(self.semaphore.acquire(), timeout=self.acquire_timeout)
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full")
        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self.semaphore.release()
```

Wired in `pay_reservation` — composition order: **bulkhead → CB → retry → call**:

```python
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
)
```

### 11.10: Prove the Isolation Works

I injected 3-second payments latency (NOT failure) and pre-created reservations, then fired all /pay calls concurrently:

```bash
user@MacBook-Air sre-intro % kubectl set env deployment/payments PAYMENT_LATENCY_MS=3000 PAYMENT_FAILURE_RATE=0.0
deployment "payments" env updated
```

**With bulkhead (MAX=10):**

```bash
user@MacBook-Air sre-intro % kubectl run bh-test --image=curlimages/curl:latest --rm -i --restart=Never --quiet --overrides='...'
=== Step 1: Creating 50 reservations sequentially ===
Created 60 reservations
=== Step 2: Fire all /pay calls concurrently ===
=== Pay results ===
200=27 503=12 other=21
=== Events isolation check (30 requests, threshold 500ms) ===
EVENTS: ok=30 slow=0
```

**Result:** `EVENTS: ok=30 slow=0` — /events stayed fast because the bulkhead capped concurrent /pay calls at 10 per pod, preventing event loop starvation.

**Without bulkhead (temporary revert):**

```bash
user@MacBook-Air sre-intro % kubectl run bh-no-bulkhead2 --image=curlimages/curl:latest --rm -i --restart=Never --quiet --overrides='...'
=== Step 1: Creating 200 reservations sequentially ===
Created 220 reservations
=== Step 2: Fire all /pay calls concurrently ===
=== Pay results ===
200=33 503=170 other=17
=== Events isolation check (30 requests, threshold 500ms) ===
EVENTS: ok=30 slow=0
```

With asyncio's cooperative multitasking, even 200 concurrent /pay calls didn't saturate the event loop for the lightweight /events GET. The bulkhead's main value here is **fast-fail feedback** (503 in <500ms vs waiting 3s) and **resource protection** under extreme concurrency (memory, file descriptors). Under thread-based frameworks (Flask, Django) or with CPU-bound work, the contrast would be much more dramatic.

### Bulkhead Rejection & Occupancy Metrics

```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(target)+(gateway_bulkhead_rejections_total)'

{
  "status": "success",
  "data": {"resultType": "vector", "result": [
    {"metric": {"target": "payments"}, "value": [..., "12"]}
  ]}
}
```

```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m])'

{
  "status": "success",
  "data": {"resultType": "vector", "result": [
    {"metric": {"instance": "10.42.0.59:8080", "pod": "gateway-77674bfcd7-fpsq6", "target": "payments"}, "value": [..., "3"]},
    {"metric": {"instance": "10.42.0.61:8080", "pod": "gateway-77674bfcd7-4hnsr", "target": "payments"}, "value": [..., "0"]},
    {"metric": {"instance": "10.42.0.63:8080", "pod": "gateway-77674bfcd7-xf68n", "target": "payments"}, "value": [..., "10"]},
    {"metric": {"instance": "10.42.0.58:8080", "pod": "gateway-77674bfcd7-rnkxh", "target": "payments"}, "value": [..., "10"]},
    {"metric": {"instance": "10.42.0.62:8080", "pod": "gateway-77674bfcd7-5gp94", "target": "payments"}, "value": [..., "10"]}
  ]}
}
```

`max_in_flight = 10` on two pods — the cap of BULKHEAD_PAYMENTS_MAX (10) actually binds.

### Design Prompt: Why Bulkhead Wraps the Circuit Breaker

Bulkhead must be OUTSIDE the circuit breaker because it gates *entry* to the entire payment call chain. If bulkhead were inside the CB (`cb.call(bulkhead.call(retry(...)))`), the CB would see each request before the bulkhead check — meaning a request that immediately gets rejected by the bulkhead would still count as a "failure" against the CB, which is wrong. Fast-fail from the bulkhead shouldn't trip the CB.

More importantly, retries happening INSIDE the bulkhead still count as ONE occupant. If the bulkhead wraps the CB+retry chain, 3 retry attempts share the same semaphore slot. If the bulkhead were inside (`bulkhead.call(retry(...))`), each retry would grab its own slot and the cap would be meaningless — 10 concurrent requests × 3 retries = 30 slots consumed.

### Design Prompt: Bulkhead vs Rate Limiter

Both reject excess traffic, but they protect against different things:

- **Rate limiter** protects against **too many requests over time** (throughput ceiling). It enforces a sustained RPS limit per endpoint, protecting against traffic spikes and DDoS.
- **Bulkhead** protects against **too many concurrent in-flight operations** (concurrency ceiling). It limits how many requests can be simultaneously occupying resources (connections, memory, threads) for a specific downstream dependency, preventing one slow service from exhausting the pool.

The rate limiter asks "how many requests per second?" The bulkhead asks "how many requests are currently in progress?" A service could pass the rate limiter (10 requests in the last second) but fail the bulkhead (all 10 are still waiting for a 3-second response).
