# Lab 11 — Advanced Microservice Patterns

Images were built locally and imported into k3d for testing (`imagePullPolicy: IfNotPresent` on the live objects only). In Git, `k8s/notifications.yaml` points to ghcr.io like the other services, and CI now builds `notifications` too. ArgoCD auto-sync was off during the lab.

## Task 1 — Notifications + Retries

**`app/notifications/main.py` (key parts):**
```python
NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

NOTIFY_TOTAL = Counter("notifications_notify_total", "Total notify attempts", ["result"])

@app.post("/notify")
def notify(body: dict = None):
    event = (body or {}).get("event", "unknown")
    order_id = (body or {}).get("order_id", "unknown")

    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)

    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning(f"Notify failed (injected) event={event} order={order_id}")
        raise HTTPException(500, "Notification delivery failed")

    NOTIFY_TOTAL.labels("success").inc()
    log.info(f"Notification sent event={event} order={order_id}")
    return {"status": "sent", "event": event, "order_id": order_id}
```

**`requirements.txt`:**
```
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

**`k8s/notifications.yaml`:**
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
      imagePullSecrets:
        - name: ghcr-secret
      containers:
        - name: notifications
          image: ghcr.io/meliman1000-7/quickticket-notifications:latest
          imagePullPolicy: Always
          ports:
            - containerPort: 8083
          env:
            - name: NOTIFY_FAILURE_RATE
              value: "0.0"
            - name: NOTIFY_LATENCY_MS
              value: "0"
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

Gateway env: `NOTIFICATIONS_URL=http://notifications:8083`.

**`call_with_retry`:**
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
            if isinstance(e, httpx.HTTPStatusError):
                code = e.response.status_code
                retryable = code >= 500 or code in (408, 429)
            else:
                retryable = isinstance(e, (httpx.TimeoutException, httpx.ConnectError))
            if not retryable:
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                raise
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
```

**Test #1** — `NOTIFY_FAILURE_RATE=0.3`, `NOTIFY_LATENCY_MS=300`:
```
result: ok=30 fail=0
/pay p99 [2m]: 0.024s   (/events 0.022s, /reserve 0.025s)
notifications_notify_total{result="success"} 306.0
notifications_notify_total{result="failed"} 140.0      → real failure rate 31%
```

**Test #2** — `PAYMENT_FAILURE_RATE=0.3`:
```
result: ok=30 fail=0
gateway_retry_total{target="payments",result="retried"}               66
gateway_retry_total{target="payments",result="succeeded_after_retry"} 49
gateway_retry_total{target="payments",result="exhausted"}              4
```
The 4 exhausted calls came from mixedload, close to 0.3³ ≈ 2.7%.

**Why should notifications be fire-and-forget?**
The order is already paid and confirmed; a notification is a side effect. If `/pay` waited for it, notifier latency would add to checkout and notifier errors would fail paid orders. Here 31% notify failures and +300 ms produced zero checkout errors and no latency change.

**Why `cb.call(retry(...))` and not `retry(cb.call(...))`?**
With retry inside, the breaker sees one outcome per user request and fast-fails without touching payments when open. With retry outside, the retry loop catches `CircuitOpenError` and retries it, so the fast-fail turns into 3 attempts with backoff, and each inner failure counts separately, tripping the breaker 3× faster. Side effect of the correct order: one failure the breaker sees = up to 3 calls to payments.

## Task 2 — Circuit Breaker + Rate Limiter

**`CircuitBreaker.call`:**
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
```

**`RateLimiter.allow`:**
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

**CB under `PAYMENT_FAILURE_RATE=1.0`** (80 attempts):
```
500s=6 503s=43 other=8
```
- 500 = retries exhausted before a pod's breaker opened; 503 = fast-fail with the breaker open.
- "other" (8) and 23 skipped reservations were 429s. Fast 503s sped the loop up past 10 RPS per pod, so the rate limiter kicked in (`rejections: /events/{id}/reserve 25, /reserve/{id}/pay 8`).
- Side finding: 429s don't appear in `gateway_requests_total`. The rate-limit middleware wraps the metrics middleware and answers before it.

**After recovery** (`PAYMENT_FAILURE_RATE=0`, 35 s wait):
```
[1] 200  [2] 200  [3] 200 ... [15] 200          (15/15)

gateway_circuit_breaker_transitions_total{to="OPEN"}      5
gateway_circuit_breaker_transitions_total{to="HALF_OPEN"} 5
gateway_circuit_breaker_transitions_total{to="CLOSED"}    5
```
5 of each = one breaker per gateway pod (per-process state).

**Rate limiter:**
```
burst 100 × /events:   200=45 429=55
HTTP/1.1 429 Too Many Requests
retry-after: 1
steady 30 × /events every 0.2 s:   200=30 429=0
gateway_rate_limit_rejections_total{path="/events"} 67
```

## Bonus — Bulkhead

**`Bulkhead`:**
```python
class Bulkhead:

    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.timeout = acquire_timeout_s
        self.sem = asyncio.Semaphore(max_concurrent)

    async def call(self, func):
        try:
            await asyncio.wait_for(self.sem.acquire(), timeout=self.timeout)
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

**Wiring in `pay_reservation`:**
```python
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
)
...
except BulkheadFullError:
    raise HTTPException(503, "Payment service busy (bulkhead full)")
```

`PAYMENT_LATENCY_MS=3000`. Two changes to the lab's probe script:
- reservations are made sequentially before the concurrent `/pay` calls, because parallel reserves exhausted the events DB pool (Lab 10);
- event 1 is used, for inventory.

| Run | Gateway | Concurrent `/pay` | `/pay` results | `/events` (30 samples) |
|---|---|---|---|---|
| A1 | 5 pods, bulkhead | 30 | 18×200, 12×500 | ok=29 slow=1, max 0.51 s |
| B1 | 5 pods, no bulkhead | 30 | 22×200, 8×500 | ok=30 slow=0, max 0.13 s |
| B2 | 1 pod, no bulkhead | 150 | 50×200, 26×500, 6×503, 68×504 | ok=28 slow=2, max **5.92 s** |
| A2 | 1 pod, bulkhead | 150 | 10×200, **140×503** | ok=30 slow=0, max **0.12 s** |

For B2/A2, `RATE_LIMIT_RPS=1000` so the limiter didn't reject `/pay` first.

**A2 metrics:**
```
gateway_bulkhead_rejections_total{target="payments"} 140
max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m]) 10
```

- At the lab's scale (30 calls over 5 pods, ~6 per pod) the bulkhead never fills, and `/events` stays fast without it. The gateway is async, so slow calls don't block the event loop.
- The resource that actually gets exhausted is the shared httpx connection pool (100 per pod). At 150 calls on one pod it did: without the bulkhead, `/events` stalled up to 5.9 s and payments got 68 timeouts plus retries piling on. With it, 10 calls held slots, 140 failed fast and `/events` stayed at ~0.1 s.
- The 500s in A1/B1 came from 30 simultaneous `/confirm` calls exhausting events' DB pool — the next bulkhead candidate.

**Why does the bulkhead wrap the circuit breaker, not the other way around?**
Gating at entry means one user request = one slot, no matter how many retries run inside. With the bulkhead inside the breaker, every retry attempt would grab and release its own slot, so the limit wouldn't bound concurrent requests.

**Bulkhead vs rate limiter?**
The rate limiter caps incoming request rate per endpoint, protecting the gateway and everything behind it from too much traffic. The bulkhead caps concurrent outgoing calls to one dependency, so a slow dependency can't use up resources shared with healthy ones. One limits load, the other isolates failure.
