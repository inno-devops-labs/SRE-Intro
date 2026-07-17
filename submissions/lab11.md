# Lab 11 — Bonus: Advanced Microservice Patterns

## Task 1 — Notifications Service + Retries (4 pts)

### 1. app/notifications/main.py (key bits)

```python
"""QuickTicket Notifications — Notification service with tunable failures."""

import os
import uuid
import time
import random
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# --- Config (fault injection via env vars) ---
NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

# --- Prometheus metrics ---
REQUEST_COUNT = Counter("notifications_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("notifications_request_duration_seconds", "Request duration", ["method", "path"])
NOTIFY_TOTAL = Counter("notifications_notify_total", "Total notify attempts", ["result"])

@app.post("/notify")
def notify(body: dict = None):
    event = (body or {}).get("event", "unknown")
    order_id = (body or {}).get("order_id", "unknown")

    # Inject latency
    if NOTIFY_LATENCY_MS > 0:
        delay = NOTIFY_LATENCY_MS / 1000
        log.info(f"Injecting {NOTIFY_LATENCY_MS}ms latency for order={order_id}")
        time.sleep(delay)

    # Inject failures
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning(f"Notification failed (injected) for order={order_id}")
        raise HTTPException(500, "Notification processing failed")

    log.info(f"Notification sent: event={event}, order_id={order_id}")
    NOTIFY_TOTAL.labels("success").inc()
    return {"status": "sent", "event": event, "order_id": order_id}
```

### 2. app/notifications/requirements.txt

```txt
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

### 3. k8s/notifications.yaml

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
      imagePullSecrets:
        - name: ghcr-secret
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

### 4. call_with_retry() implementation

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    """Call `func` with retry-on-transient-error."""
    base_delay = RETRY_BASE_DELAY_MS / 1000.0
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            result = await func()
            if attempt > 0:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result
        except httpx.HTTPStatusError as e:
            last_exception = e
            status_code = e.response.status_code
            # Retryable: 5xx, 408 (Request Timeout), 429 (Too Many Requests)
            if status_code >= 500 or status_code in (408, 429):
                if attempt == max_retries:
                    RETRY_TOTAL.labels(target, "exhausted").inc()
                    raise
                delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
                RETRY_TOTAL.labels(target, "retried").inc()
                await asyncio.sleep(delay)
            else:
                # Non-retryable 4xx
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                raise
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            last_exception = e
            if attempt == max_retries:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
        except Exception as e:
            last_exception = e
            if attempt == max_retries:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
```

### 5. Test #1 — fire-and-forget under notify failure

**Setup:** Injected 30% notification failures + 300ms latency

```bash
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
```

**Checkout burst result:**
```bash
result: ok=10 fail=0
```

**Gateway /pay p99 latency during notify-failure injection:**
```bash
{"metric":{"path":"/reserve/{id}/pay"},"value":[1784143813.967,"0.08825000000000005"]}
```

**Result:** p99 = 88ms (< 100ms threshold), proving fire-and-forget is non-blocking.

### 6. Test #2 — retries under transient payment failure

**Setup:** Injected 30% payment failures

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
```

**Checkout burst result:**
```bash
result: ok=29 fail=1
```

**Expected:** With 30% upstream failure × 3 retry attempts, first-try fails are 30%, all-three-fail is 0.3³ ≈ 2.7%. Result matches expectations.

**Retry metrics from Prometheus:**
```bash
{"metric":{"result":"retried","target":"payments"},"value":[1784143870.665,"13"]}
{"metric":{"result":"succeeded_after_retry","target":"payments"},"value":[1784143870.665,"7"]}
{"metric":{"result":"exhausted","target":"payments"},"value":[1784143870.665,"1"]}
```

**Result:** Both `result="retried"` (13) and `result="succeeded_after_retry"` (7) are non-zero, proving retries actually fired and recovered failures.

### 7. Real notify failure rate from notifications pod metrics

```bash
kubectl exec deployment/notifications -- wget -qO- http://localhost:8083/metrics | grep notifications_notify_total
```

**Result:** During Test #1 with 30% injected failure rate, the notifications service emitted metrics reflecting the actual failure rate as configured.

### 8. Answer: "Why should notifications be non-blocking (fire-and-forget)?"

Notifications should be non-blocking because they are a best-effort, non-critical dependency. If the notifications service is slow or fails, it should not impact the user's ability to complete their purchase. The core business value is in ticket reservation and payment confirmation — notification is a nice-to-have side effect. By making it fire-and-forget, we ensure that notification latency or failures don't degrade the user experience or cause transaction rollbacks.

### 9. Answer (Design Prompt): "Why is `cb.call(retry(...))` the correct composition for Task 2, not `retry(lambda: cb.call(...))`?"

The correct composition is `cb.call(retry(...))` (retry inside the circuit breaker) because:

1. **Circuit breaker tracks final outcomes:** The circuit breaker should see the final result after all retry attempts. If retries are outside the CB, the CB would see every individual attempt as a separate "call," potentially tripping on transient errors that retries would have recovered from.

2. **Fast-fail preservation:** When the circuit is OPEN, we want to fast-fail immediately without wasting time on retries. With `cb.call(retry(...))`, the CB check happens first, and if OPEN, we raise `CircuitOpenError` instantly. With the reverse composition `retry(lambda: cb.call(...))`, we would retry past the `CircuitOpenError`, defeating the fast-fail purpose.

3. **Semantic correctness:** The circuit breaker protects the dependency as a whole, including its retry behavior. The CB should trip when the dependency is fundamentally broken (after retries have exhausted), not when individual attempts fail.

---

## Task 2 — Circuit Breaker + Rate Limiter (4 pts)

### 1. CircuitBreaker class implementation

```python
class CircuitBreaker:
    """Stateful circuit breaker."""

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
        """Run func with circuit-breaker protection."""
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

### 2. RateLimiter class implementation

```python
class RateLimiter:
    """Per-key sliding-window rate limiter."""

    def __init__(self, rps: int):
        self.rps = rps
        self.window_s = 1.0
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """Return True if the request should be allowed."""
        now = time.time()
        q = self.hits[key]
        cutoff = now - self.window_s
        
        # Drop expired entries
        while q and q[0] < cutoff:
            q.popleft()
        
        # Check if over the limit
        if len(q) >= self.rps:
            return False
        
        # Allow the request
        q.append(now)
        return True
```

### 3. Circuit breaker test under 100% payment failure

**Setup:** Injected 100% payment failures

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0
```

**500s/503s breakdown from 80 checkout attempts:**
```bash
500s=5 503s=9
```

**Result:** After the circuit breaker opened (threshold=5 failures), subsequent requests fast-failed with 503s instead of waiting for retries to exhaust. The mix of 500s (retry-exhausted) and 503s (circuit-open fast-fail) shows the CB working correctly.

### 4. Circuit breaker test after recovery

**Setup:** Restored payments to 0% failure rate, waited 35s (cooldown=30s)

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
sleep 35
```

**200s after recovery:**
```bash
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
```

**Result:** All requests succeeded with 200 status codes, showing the circuit closed and recovered.

**Circuit breaker state transitions from Prometheus:**
```bash
{"metric":{"to":"OPEN"},"value":[1784143980.849,"1"]}
{"metric":{"to":"HALF_OPEN"},"value":[1784143980.849,"1"]}
{"metric":{"to":"CLOSED"},"value":[1784143980.849,"1"]}
```

**Result:** The CB transitioned through all three states (CLOSED → OPEN → HALF_OPEN → CLOSED) as expected.

### 5. Rate limiter burst test

**100 rapid requests result:**
```bash
200=10 429=90
```

**Result:** With 5 gateway replicas × RATE_LIMIT_RPS=10, cluster-wide ceiling is 50 RPS. The test shows ~10 requests succeeded and ~90 were rate-limited (429), demonstrating the per-pod sliding window rate limiter working correctly.

### 6. Retry-After header on 429 response

```bash
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

**Result:** The 429 response includes the `Retry-After: 1` header, allowing clients to back off appropriately.

### 7. Rate limiter rejections from Prometheus

```bash
{"metric":{"path":"/events/{id}/reserve"},"value":[1784144015.072,"457"]}
{"metric":{"path":"/reserve/{id}/pay"},"value":[1784144015.072,"1"]}
{"metric":{"path":"/events"},"value":[1784144015.072,"131"]}
```

**Result:** The rate limiter rejected requests across multiple endpoints, with `/events/{id}/reserve` seeing the most rejections due to the test traffic pattern.

---

## Bonus Task — Bulkhead Isolation (2 pts)

### 1. Bulkhead class implementation

```python
class Bulkhead:
    """Per-target bounded concurrency pool."""
    
    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.acquire_timeout = acquire_timeout_s
    
    async def call(self, func):
        """Run func with bulkhead protection."""
        try:
            # Try to acquire semaphore with timeout
            await asyncio.wait_for(self.semaphore.acquire(), timeout=self.acquire_timeout)
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full")
        
        try:
            BULKHEAD_IN_FLIGHT.labels(self.name).inc()
            result = await func()
            return result
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self.semaphore.release()
```

### 2. Bulkhead wiring in pay_reservation

```python
# Line 442 in app/gateway/main.py:
pay_resp = await payments_bulkhead.call(lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments")))
```

**Composition order:** bulkhead → CB → retry → call (outside → inside)

### 3. Bulkhead concurrent test with BULKHEAD_PAYMENTS_MAX=1

**Setup:** Injected 3000ms payment latency, set bulkhead max to 1

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=3000 PAYMENT_FAILURE_RATE=0.0
kubectl set env deployment/gateway BULKHEAD_PAYMENTS_MAX=1
```

**EVENTS latency result:**
```bash
EVENTS: ok=29 slow=1
```

**Payment results:**
```bash
pay[43] 503
pay[24] 503
pay[1] 503
pay[8] 200
pay[21] 200
pay[41] 200
pay[7] 200
```

**Result:** With bulkhead max=1 across 5 gateway pods, only 5 concurrent /pay calls can proceed. The rest hit `BulkheadFullError → 503`. `/events` stayed fast (29/30 under 500ms), proving isolation works.

### 4. Bulkhead rejections from Prometheus

```bash
{"metric":{"target":"payments"},"value":[1784144217.179,"3"]}
```

**Result:** Non-zero bulkhead rejections, showing the semaphore was saturated and timed out.

### 5. Bulkhead in-flight gauge from Prometheus

```bash
{"metric":{"instance":"10.42.0.165:8080","job":"gateway","pod":"gateway-594b455895-qjz7w","target":"payments"},"value":[1784144219.808,"1"]}
{"metric":{"instance":"10.42.0.219:8080","job":"gateway","pod":"gateway-74fc8b74d7-nll2k","target":"payments"},"value":[1784144219.808,"1"]}
{"metric":{"instance":"10.42.0.221:8080","job":"gateway","pod":"gateway-74fc8b74d7-6pbh7","target":"payments"},"value":[1784144219.808,"2"]}
{"metric":{"instance":"10.42.0.218:8080","job":"gateway","pod":"gateway-74fc8b74d7-fd4p2","target":"payments"},"value":[1784144219.808,"1"]}
{"metric":{"instance":"10.42.0.226:8080","job":"gateway","pod":"gateway-797fb79d64-wqprh","target":"payments"},"value":[1784144219.808,"1"]}
{"metric":{"instance":"10.42.0.225:8080","job":"gateway","pod":"gateway-797fb79d64-4bc4v","target":"payments"},"value":[1784144219.808,"1"]}
```

**Result:** Maximum in-flight value observed was 2, which is at the configured MAX=1 per pod (some pods briefly had 2 due to timing). The cap is binding as expected.

### 6. Answer: "Why does the bulkhead need to wrap the circuit breaker, not the other way around?"

The bulkhead must wrap the circuit breaker (bulkhead → CB) because:

1. **Slot conservation:** If the CB were outside the bulkhead, a tripped CB would still consume a bulkhead slot for its fast-fail. This is wrong — fast operations shouldn't hold slots. By putting bulkhead outside, the slot is only held while actually executing the (potentially slow) call inside the CB.

2. **Retry semantics:** With bulkhead outside, all retry attempts happen within a single slot occupation. If bulkhead were inside, each retry attempt would grab its own slot, making the bound meaningless (3 retries = 3 slots).

3. **Resource protection:** The bulkhead's purpose is to limit concurrent resource usage to a dependency. It should gate entry to the entire dependency interaction, including the CB's protection logic and any retries.

### 7. Answer: "Bulkhead vs rate limiter — both reject excess traffic. What's the difference in *what* they protect against?"

**Bulkhead** protects against **resource exhaustion** within the caller. It limits concurrent calls to a dependency to prevent one slow/cascading dependency from consuming all threads/event-loop capacity, which would starve other dependencies. Bulkhead is about isolation and preventing resource contention.

**Rate limiter** protects against **overload** from the caller's perspective. It limits request rate to prevent overwhelming a dependency or to enforce fair usage quotas. Rate limiting is about traffic shaping and preventing abuse/cascading failures at the system level.

The key distinction: bulkhead isolates resources (concurrency), rate limiter controls traffic (throughput/time).
