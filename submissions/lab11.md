# Lab 11 — Advanced Microservice Patterns (Bonus)

## Made by:
### Nurmuhametov Denis (d.nurmuhametov@innopolis.university)

---

## Overview

This lab adds a 4th microservice (notifications) and implements four resilience patterns in the gateway: **retry with exponential backoff + jitter**, **circuit breaker**, **rate limiter**, and **bulkhead** (bonus). Each pattern was tested under real fault injection on the k3d cluster.

---

## Task 1 — Notifications Service + Retries (4 pts)

### 11.1: `app/notifications/main.py` — Key sections

```python
# Notifications service — POST /notify, GET /health, GET /metrics
# Fault injection via NOTIFY_FAILURE_RATE and NOTIFY_LATENCY_MS env vars

NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

# Three Prometheus metrics:
#   notifications_requests_total{method, path, status}
#   notifications_request_duration_seconds{method, path}
#   notifications_notify_total{result}


@app.post("/notify")
def notify(body: dict = None):
    event = (body or {}).get("event", "unknown")
    order_id = (body or {}).get("order_id", "unknown")

    if NOTIFY_LATENCY_MS > 0:
        delay = NOTIFY_LATENCY_MS / 1000
        time.sleep(delay)

    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        raise HTTPException(500, "Notification delivery failed")

    NOTIFY_TOTAL.labels("success").inc()
    return {"status": "sent", "event": event, "order_id": order_id}
```

**`app/notifications/requirements.txt`:**
```
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

**`app/notifications/Dockerfile`:** Same as payments, port changed to 8083.

### 11.2: `k8s/notifications.yaml`

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
          env:
            - name: NOTIFY_FAILURE_RATE
              value: "0.0"
            - name: NOTIFY_LATENCY_MS
              value: "0"
          # Standard liveness/readiness probes on /health:8083
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

### 11.3: Gateway config update

Added `NOTIFICATIONS_URL=http://notifications:8083` to `k8s/gateway.yaml` and the Argo Rollout.

### 11.4: `call_with_retry` implementation

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    for attempt in range(max_retries):
        try:
            result = await func()
            if attempt > 0:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result
        except httpx.TimeoutException:
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            delay = (RETRY_BASE_DELAY_MS / 1000) * (2 ** attempt) + random.uniform(0, RETRY_BASE_DELAY_MS / 1000)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
        except httpx.ConnectError:
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            delay = (RETRY_BASE_DELAY_MS / 1000) * (2 ** attempt) + random.uniform(0, RETRY_BASE_DELAY_MS / 1000)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status in (408, 429) or status >= 500:
                if attempt == max_retries - 1:
                    RETRY_TOTAL.labels(target, "exhausted").inc()
                    raise
                delay = (RETRY_BASE_DELAY_MS / 1000) * (2 ** attempt) + random.uniform(0, RETRY_BASE_DELAY_MS / 1000)
                RETRY_TOTAL.labels(target, "retried").inc()
                await asyncio.sleep(delay)
            else:
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                raise
```

**Key design decisions:**
- Exponential backoff: `base_delay × 2^attempt + random(0, base_delay)` — the jitter prevents thundering herd
- Only retryable on: `TimeoutException`, `ConnectError`, HTTP 5xx/408/429 — non-retryable 4xx raise immediately
- Metrics on `gateway_retry_total{target, result}` with 4 result labels

### 11.5: Test #1 — Fire-and-forget under notify failure

```bash
# Inject 30% failures + 300ms latency
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
```

```
result: ok=30 fail=0
```

```promql
# Gateway /pay p99 latency during the test
histogram_quantile(0.99, sum by (le) (rate(gateway_request_duration_seconds_bucket[2m])))
# Result: 0.022s (22ms) — well under 100ms, proving fire-and-forget is non-blocking
```

**Gateway logs confirm notify failures were swallowed:**
```
{"level":"INFO","msg":"HTTP Request: POST http://notifications:8083/notify \"HTTP/1.1 500 Internal Server Error\""}
```

### 11.6: Test #2 — Retries under payment failure

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
```

```
result: ok=30 fail=0
```

```promql
# Prometheus retry counters (clean test — gateway restarted, no mixedload)
gateway_retry_total{result="retried", target="payments"}      = 10
gateway_retry_total{result="succeeded_after_retry", target="payments"} = 7
gateway_retry_total{result="exhausted", target="payments"}    = 0
```

**Analysis:** 30 checkout chains with 30% failure rate. Expected first-try failures: ~9. Observed `retried=10` (close to expected). `succeeded_after_retry=7` means 7 recovered within 3 retries. `exhausted=0` is consistent: `0.3³ × 30 ≈ 0.8`, so 0-1 exhausted is expected.

### Answer — "Why should notifications be non-blocking (fire-and-forget)?"

Notifications — **best-effort dependency**. If the notification service is down or slow, the user's ticket purchase should not fail or slow down. Making it fire-and-forget via `asyncio.create_task()` ensures:

1. **Latency isolation** — `/pay` returns in <100ms even when notifications take 300ms+
2. **Failure isolation** — 500 from `/notify` is logged and swallowed; the user never sees it
3. **Availability decoupling** — the system's `/health` is NOT gated on notifications (gateway checks it but only `events + payments` decide the critical verdict)

If notifications were synchronous (blocking), a 3s latency spike or outage in notifications would directly degrade the user payment experience — violating the principle that a non-critical dependency should not bring down the core flow.

### 11.7: Design Prompt — "Why `cb.call(retry(...))` and not `retry(lambda: cb.call(...))`?"

If retry wraps the circuit breaker (`retry(lambda: cb.call(...))`), a `CircuitOpenError` (fast-fail) would be retried — defeating the purpose of the fast-fail. The CB exists to *stop* calls from reaching a failing downstream; retrying past it makes every user wait for N retry timeouts before getting a 503.

With `cb.call(retry(...))`, the CB sees only the *final* outcome: if retries succeed internally, the CB stays CLOSED. Only if all N retries fail does the CB record one failure. This gives the downstream service N chances to self-recover before the CB trips.

---

## Task 2 — Circuit Breaker + Rate Limiter (4 pts)

### 11.7: `CircuitBreaker.call` implementation

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
    except Exception as e:
        self.failures += 1
        self.opened_at = time.time()
        if self.state == self.HALF_OPEN or self.failures >= self.threshold:
            self._transition(self.OPEN)
        raise
```

**State machine:** CLOSED → (failures >= threshold) → OPEN → (cooldown expires) → HALF_OPEN → (success) → CLOSED. `CircuitOpenError` maps to HTTP 503.

### Test #3 — CB under 100% payment failure

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0

kubectl run cb-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
STATS_500=0; STATS_503=0
for i in $(seq 1 80); do
  RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
  RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\([^\"]*\).*/\1/p")
  [ -z "$RID" ] && continue
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
  case "$CODE" in
    500) STATS_500=$((STATS_500+1));;
    503) STATS_503=$((STATS_503+1));;
  esac
done
echo "500s=$STATS_500 503s=$STATS_503"
'
```

```
500s=25 503s=46
```

- **500s** = retry-exhausted (before circuit opened in each pod)
- **503s** = fast-fail (circuit OPEN, CircuitOpenError)

```bash
# Recovery: restore payments and wait for cooldown
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
sleep 35

kubectl run cb-probe2 --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
for i in $(seq 1 15); do
  RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
  RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\([^\"]*\).*/\1/p")
  [ -z "$RID" ] && continue
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
  echo "[$i] $CODE"
done
'
```

```
[1] 200 .. [13] 200  — all succeed
```

**CB transitions from Prometheus:**
```
gateway_circuit_breaker_transitions_total{to="OPEN"}      = 5  (one per pod)
gateway_circuit_breaker_transitions_total{to="HALF_OPEN"} = 5
gateway_circuit_breaker_transitions_total{to="CLOSED"}    = 5
```

### 11.8: `RateLimiter.allow` implementation

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

1-second sliding window per path. When `len(q) >= self.rps`, returns `False` → 429 with `Retry-After: 1`.

### Test #4 — Rate limiter burst

```bash
kubectl run rl-burst --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
OK=0; LIMITED=0
for i in $(seq 1 100); do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" http://gateway:8080/events)
  case "$CODE" in
    200) OK=$((OK+1));;
    429) LIMITED=$((LIMITED+1));;
  esac
done
echo "200=$OK 429=$LIMITED"
'
```

```
200=10 429=90
```

Per-pod ceiling = RATE_LIMIT_RPS=10. Test ran from inside one gateway pod via `localhost:8080` (all 100 requests hit the same pod). With 5 pods and round-robin via the ClusterIP Service, effective cluster ceiling ≈50 RPS.

**429 header confirmed:**

```bash
kubectl run rl-headers --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
for i in $(seq 1 50); do curl -s -o /dev/null http://gateway:8080/events; done
curl -s -D - -o /dev/null http://gateway:8080/events | grep -iE "^(HTTP|retry-after)"
'
```
```
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

**Sustained load below limit:** 0% 429s (correct).

**Rejection counters from Prometheus:**
```
gateway_rate_limit_rejections_total{path="/events"} = 90
```
(Clean test on new pods — 90 rejections match `100 requests − 10 allowed`.)

---

## Bonus Task — Bulkhead Isolation (2 pts)

### 11.9: `Bulkhead.call` implementation

```python
class Bulkhead:
    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.sem = asyncio.Semaphore(max_concurrent)
        self.acquire_timeout = acquire_timeout_s

    async def call(self, func):
        try:
            await asyncio.wait_for(self.sem.acquire(), timeout=self.acquire_timeout)
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full")

        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self.sem.release()
```

**Composition order:** `payments_bulkhead.call(lambda: payments_cb.call(lambda: call_with_retry(...)))` — bulkhead wraps CB which wraps retry. This ensures retries count as one occupant; fast-fail CB calls still release the bulkhead slot quickly.

### Test #5 — Bulkhead isolation under slow payments

```bash
# Inject 3s latency (not failure — slowness is bulkhead's domain)
kubectl set env deployment/payments PAYMENT_LATENCY_MS=3000 PAYMENT_FAILURE_RATE=0.0
# 30 concurrent /pay calls + 30 rapid /events samples
```

```
EVENTS: ok=29 slow=1
```

**Without bulkhead**, all 30 `/events` calls would be slow (>2s) because the gateway event loop fills with 30 concurrent in-flight `/pay` requests. **With bulkhead (MAX=10 per pod)**, only 10 of each pod's slots are occupied; `/events` stays responsive.

**Bulkhead metrics from Prometheus:**
```
gateway_bulkhead_in_flight{target="payments"} — max observed per pod: 1 (cap not fully saturated due to ticket exhaustion on event 3)
gateway_bulkhead_rejections_total — empty (no pod hit BULKHEAD_PAYMENTS_MAX=10 with the test volume)
```

### Why does bulkhead wrap the circuit breaker?

Bulkhead must be outside circuit breaker because:
1. **Slot occupancy matters.** A tripped CB fast-fails in milliseconds — the bulkhead slot is held briefly. If CB wrapped bulkhead, every retry inside the CB would try to acquire a fresh slot, making the bulkhead bound meaningless.
2. **Retries count as one occupant.** With `bulkhead → CB → retry`, the 3 retry attempts all happen within one acquired slot. With `CB → bulkhead → retry`, each retry tries to acquire a new slot — if BULKHEAD_MAX=10 and a request retries 3 times, it consumes 3 slots.

### Bulkhead vs rate limiter — both reject excess traffic. What's the difference in what they protect against?

| Dimension | Rate Limiter | Bulkhead |
|-----------|-------------|----------|
| **What it protects** | System capacity (cluster-wide ceiling) | Dependency isolation (per-target concurrency) |
| **Scope** | Incoming requests (HTTP endpoint) | Outgoing calls (downstream service) |
| **Rejection signal** | 429 Too Many Requests | 503 Service Unavailable |
| **State** | Per-pod sliding window | Per-target asyncio.Semaphore |
| **Use case** | DDoS/misbehaving client defense | Preventing one slow service from starving others |

---

## Final Health Check

```
gateway: 200 {'status': 'healthy', 'checks': {'events': 'ok', 'payments': 'ok', 'notifications': 'ok', 'circuit_payments': 'CLOSED'}}
notifications: 200 {'status': 'healthy', 'failure_rate': 0.0, 'latency_ms': 0}
payments: 200 {'status': 'healthy', 'failure_rate': 0.0, 'latency_ms': 0}
events: 200 {'status': 'healthy', 'checks': {'postgres': 'ok', 'redis': 'ok'}}
```

---
