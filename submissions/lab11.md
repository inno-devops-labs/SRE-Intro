# Lab 11. Advanced microservice patterns

**Student:** Kirill Fadeev
**Email:** ki.fadeev@innopolis.university
**Environment:** WSL2 (kernel 6.18.33.2-microsoft-standard-WSL2, x86_64), Docker 29.8.0, k3d v5.9.0, k3s v1.35.5+k3s1, kubectl v1.37.0, Argo Rollouts v1.10.0 (gateway is the 5 replica canary Rollout from Lab 7), in-cluster Prometheus v3.11.2 scraping gateway pods every 5 s, Python 3.13.16, httpx 0.28.1, FastAPI 0.136.0

All numbers come from capture files taken on 2026-10-16 between 13:55 and 14:22 local time (UTC+3). Every test ran from a long-lived `curlimages/curl` pod inside the cluster against `http://gateway:8080`. The loops are the lab's own (`checkout-burst`, `retry-test`, `cb-probe`, `rl-burst`, `bulkhead-probe`) with one addition: `curl -w "%{http_code} %{time_total}"` on the `/pay` call, so every outcome has a client-side time next to it.

Two checks guarded every run. After each `kubectl set env` the script read the service's `/health` and refused to continue unless it reported the injected `failure_rate` or `latency_ms`. After each gateway rollout it compared `md5sum main.py` inside every gateway pod with the file in the repo, so no test ran against a stale image.

---

## Task 1. Notifications service and retries

### 1.1 `app/notifications/`

A copy of the payments shape: a sync handler, fault injection from env vars, and the same metrics middleware.

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
        time.sleep(NOTIFY_LATENCY_MS / 1000)
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning(f"Notify failed (injected) event={event} order={order_id}")
        raise HTTPException(500, "Notification delivery failed")
    NOTIFY_TOTAL.labels("success").inc()
    log.info(f"Notification sent event={event} order={order_id}")
    return {"status": "sent", "event": event, "order_id": order_id}
```

`requirements.txt` is identical to payments, and the Dockerfile is the payments one with port 8083:

```plaintext
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

`app/docker-compose.yaml` got a `notifications` service and `NOTIFICATIONS_URL=http://notifications:8083` on the gateway, so the compose stack from the early labs matches the cluster.

### 1.2 `k8s/notifications.yaml`

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
            limits: {cpu: 200m, memory: 256Mi}
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

(Probes and resources are shown in flow style here; the committed file uses block style.) `/health` of this service has no dependencies, so unlike events and gateway it is safe for liveness as well.

```plaintext
$ kubectl get deploy,svc notifications -o wide
NAME                            READY   UP-TO-DATE   AVAILABLE   AGE     CONTAINERS      IMAGES                         SELECTOR
deployment.apps/notifications   1/1     1            1           4m25s   notifications   quickticket-notifications:v1   app=notifications
NAME                    TYPE        CLUSTER-IP   EXTERNAL-IP   PORT(S)    AGE     SELECTOR
service/notifications   ClusterIP   10.43.89.0   <none>        8083/TCP   4m25s   app=notifications

$ curl http://notifications:8083/health
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
$ curl -X POST http://notifications:8083/notify -d '{"event":"order_confirmed","order_id":"smoke"}'
{"status":"sent","event":"order_confirmed","order_id":"smoke"}
notifications_requests_total{method="POST",path="/notify",status="200"} 2.0
notifications_request_duration_seconds_count{method="POST",path="/notify"} 2.0
notifications_notify_total{result="success"} 2.0
```

### 1.3 Gateway wiring

The course `main` has no `k8s/` directory, so `k8s/gateway.yaml` arrives in this PR as a new file: the Lab 7 Rollout from my `feature/lab8` branch with three changes: the image is the locally built `quickticket-gateway:v1`, `NOTIFICATIONS_URL` is set, and the pattern tunables are listed with their defaults so they can be changed without a rebuild.

```yaml
          image: quickticket-gateway:v1
          env:
            # ...EVENTS_URL, PAYMENTS_URL, GATEWAY_TIMEOUT_MS as before
            - name: NOTIFICATIONS_URL
              value: "http://notifications:8083"
            - name: RETRY_MAX
              value: "3"
            - name: RETRY_BASE_DELAY_MS
              value: "100"
            - name: CB_FAILURE_THRESHOLD
              value: "5"
            - name: CB_COOLDOWN_S
              value: "30"
            - name: RATE_LIMIT_RPS
              value: "10"
            - name: BULKHEAD_PAYMENTS_MAX
              value: "10"
            - name: BULKHEAD_PAYMENTS_TIMEOUT_S
              value: "0.5"
```

```plaintext
$ kubectl apply -f k8s/gateway.yaml && kubectl argo rollouts status gateway
Healthy
Images:          quickticket-gateway:v1 (stable)
pod/gateway-f8bc49cf-cfc87: main.py md5 matches repo, NOTIFICATIONS_URL=http://notifications:8083
... (all 5 pods)
$ curl http://gateway:8080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","notifications":"ok","circuit_payments":"CLOSED"}}
```

### 1.4 `call_with_retry`

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    base_delay = RETRY_BASE_DELAY_MS / 1000
    for attempt in range(max_retries):
        try:
            result = await func()
            if attempt > 0:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
            if isinstance(e, httpx.HTTPStatusError):
                status = e.response.status_code
                if not (status >= 500 or status in (408, 429)):
                    # 404, 422, ... will not fix themselves: fail fast.
                    RETRY_TOTAL.labels(target, "non_retryable").inc()
                    raise
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            log.warning(f"retry target={target} attempt={attempt + 1} delay={delay:.3f}s err={e!r}")
            await asyncio.sleep(delay)
```

With the defaults the waits are 100 to 200 ms after the first failure and 200 to 300 ms after the second. Before anything reached the cluster, a small test ran the primitives inside the built image with fake callables:

```plaintext
$ docker run --rm -i quickticket-gateway:v1 python - < unit_test.py
retry: OK (2 retries then success, 404 not retried, 408/429/503 exhausted after 3)
circuit breaker: OK (opens at threshold, fast-fails, half-open probe reopens, success closes)
rate limiter: OK (10 of 15 in one window, keys independent, window slides)
bulkhead: OK (3 of 5 admitted, 2 rejected after 0.1s, gauge back to 0)
UNIT TESTS PASSED
```

The 404 case matters here because nothing in the cluster can make payments answer with a 4xx, so the non-retryable branch is only covered by this test.

### 1.5 Test #1: fire-and-forget under notify failure

The lab 8 `mixedload` was running throughout. First the same burst with no injection, as a control:

```plaintext
burst baseline (NOTIFY 0.0 / 0 ms):
result: ok=30 fail=0
client-side /pay latency over 30 calls: p50=19.2ms p90=30.6ms max=75.8ms
gateway p99 {"path": "/reserve/{id}/pay"} => 0.0487
```

Then the lab injection, verified through `/health` before the burst:

```plaintext
$ kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
{"status":"healthy","failure_rate":0.3,"latency_ms":300}
burst test1:
result: ok=30 fail=0
client-side /pay latency over 30 calls: p50=18.2ms p90=98.4ms max=178.6ms
gateway p99 by path (lab query, 2m window)
   {"path": "/reserve/{id}/pay"} => 0.22029189072227912
   {"path": "/events/{id}/reserve"} => 0.09761137708091247
   {"path": "/events"} => 0.024040125005684282
notifications_notify_total{result="success"} 21.0
notifications_notify_total{result="failed"} 9.0
$ curl http://gateway:8080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","notifications":"ok","circuit_payments":"CLOSED"}}
```

Every checkout succeeded and the real notify failure rate was 9 of 30, 30 %. The `/pay` p99 of 220 ms, however, is above the 100 ms the lab expects, so I did not take the first run at face value and measured whether notify latency leaks into `/pay` at all. If the gateway awaited the notification, every `/pay` would take at least the injected latency. Four runs of 30 checkouts with `NOTIFY_FAILURE_RATE=0.0` and different latencies:

```plaintext
condition A: NOTIFY_LATENCY_MS=0     client-side /pay over 30 calls: p50=17.9ms p90=23.1ms max=53.3ms   >100ms: 0
condition B: NOTIFY_LATENCY_MS=300   client-side /pay over 30 calls: p50=20.8ms p90=77.9ms max=85.6ms   >100ms: 0
condition C: NOTIFY_LATENCY_MS=1500  client-side /pay over 30 calls: p50=18.1ms p90=31.1ms max=146.6ms  >100ms: 1
condition D: NOTIFY_LATENCY_MS=0     client-side /pay over 30 calls: p50=17.3ms p90=23.2ms max=37.5ms   >100ms: 0
```

With 1500 ms injected, the slowest of 30 `/pay` calls took 147 ms, a tenth of the injected delay. The tail moves by tens of milliseconds between runs with no relation to the injected value (300 ms gave a higher p90 than 1500 ms). I read it as scheduling noise of a WSL VM with 200m CPU limits. A repeat of the exact Test #1 injection later in the session:

```plaintext
$ kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
result: ok=30 fail=0
  200: n=30 min=12ms median=14ms max=71ms
histogram_quantile(0.99, ... {path="/reserve/{id}/pay"}[1m])  => 0.0675
increase(... le="0.05")  => 31.6     increase(... le="0.1") => 32.7     increase(... le="0.5") => 32.7
notifications_notify_total{result="success"} 21.0
notifications_notify_total{result="failed"} 9.0
```

p99 67.5 ms, every call under 100 ms. The 220 ms of the first run is also an artifact of the method. Only two of its 30 calls took longer than 100 ms (105 ms and 179 ms in the burst file). With a few dozen samples, p99 is decided by the slowest one or two requests, and `histogram_quantile` places it by linear interpolation inside the 100 to 250 ms bucket.

> The gateway logged nothing about the 9 failed notifications: `notify failed warnings across pods: 0`. `_notify_order_confirmed` never calls `raise_for_status()`, so a 500 from notifications is a successful `client.post` and only a timeout or connection error reaches the `except`. The only place where lost notifications are visible is `notifications_notify_total` on the notifications pod, and that pod is not in the Prometheus scrape config. As deployed, the system can lose 30 % of its order confirmations without a single signal on the side that sends them.

### 1.6 Test #2: retries under 30 % payment failure

```plaintext
retry counters before:   (empty result)
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
verified payments /health failure_rate=0.3
[12] pay failed: 500
[16] pay failed: 500
result: ok=28 fail=2
client-side /pay latency over 30 calls: p50=23.6ms p90=383.9ms max=527.1ms
retry counters after (lab query)
   {"result": "retried", "target": "payments"} => 13
   {"result": "succeeded_after_retry", "target": "payments"} => 7
   {"result": "exhausted", "target": "payments"} => 2
payments_charges_total{result="success"} 28.0
payments_charges_total{result="failed"} 15.0
```

The counters add up on both sides. 9 checkouts failed their first attempt (7 recovered, 2 exhausted), 4 of those also failed the second, 2 failed all three: 9 + 4 = 13 retries, 30 + 13 = 43 charge attempts, 9 + 4 + 2 = 15 failed charges, exactly what payments counted. The 90th percentile of 384 ms is the backoff made visible: a checkout that needed a retry paid 100 to 300 ms of sleep.

The result is `fail=2`, one more than the lab's "fail < 2". With a per-checkout failure probability of 0.3³ = 2.7 %, 30 checkouts give 0 or 1 failures with probability 81 %, so 2 is a normal outcome, about one run in five. Instead of re-running until the number looked right, I ran a larger sample:

```plaintext
burst retry100: 100 checkouts on event 3   (stopped after 42 by the ticket counter bug, see Side findings)
result: ok=41 fail=59     <- 58 of the 59 are "Not enough tickets" at reserve, 1 is a pay failure
retry counters: retried 13 -> 31, succeeded_after_retry 7 -> 18, exhausted 2 -> 3
payments_charges_total{result="success"} 41.0
payments_charges_total{result="failed"} 19.0
```

Across both runs: 72 checkouts, 3 exhausted, 4.2 % against 2.7 % expected, with 34 of 103 charge attempts failed (33 %). Without retries the same injection would fail 30 % of checkouts; with three attempts it failed 4 %.

### 1.7 Answers

**Why should notifications be non-blocking?** Sending the confirmation is not part of the purchase: the money is taken and the order row is written before the gateway calls notifications. If `/pay` awaited it, the notify latency would be added to every checkout and a notifications outage would turn into a checkout outage, so the availability SLO of payments would depend on a service nobody needs to complete a purchase. Condition C above shows the decoupling: 1500 ms of notify latency, `/pay` max 147 ms. The price is loss: the 9 failed notifications are gone, nothing retries them, and the gateway does not even see them fail. A real system would write an outbox row in the same transaction as the order and deliver from a queue with its own retries.

**Why `cb.call(retry(...))` and not `retry(lambda: cb.call(...))`?** With retry inside, the breaker sees one outcome per user request, after the retries had their chance. That is the signal it should act on: payments is failing even after we tried three times. With the order reversed:

- The breaker counts attempts instead of requests. A threshold of 5 opens after two failing checkouts, and during a 30 % transient failure, where retries rescue 97 % of checkouts, it would keep opening for nothing.
- An open breaker raises `CircuitOpenError` on the first attempt. A retry that treats it as retryable keeps sleeping and re-asking, and the fast-fail stops being fast. My `call_with_retry` only catches httpx errors, so it would re-raise, but the composition should not depend on that detail.
- In `HALF_OPEN` one probe is supposed to test the dependency. With retry outside, a failed probe immediately produces more attempts against a service the breaker has just declared sick.

The cost of the correct order is amplification that is easy to misread, as the lab's pitfall notes: in the Task 2 test below the breakers opened after 25 user failures, and payments counted 75 failed charges for them.

---

## Task 2. Circuit breaker and rate limiter

### 2.1 `CircuitBreaker.call` and `RateLimiter.allow`

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

### 2.2 The circuit opens under 100 % payment failure

```plaintext
transitions before:  (empty result)
per-pod circuit state before: 10.42.0.25 CLOSED, 10.42.0.26 CLOSED, 10.42.0.22 CLOSED, 10.42.0.24 CLOSED, 10.42.0.23 CLOSED
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0
verified payments /health failure_rate=1.0
loop took 13s
[65] reserve failed: {"error":"rate_limited","path":"/events/{id}/reserve","limit_rps":10}
... (10 reserves rate limited, see below)
500s=25 503s=44 200s=0 other=1
  429: n=1 min=2ms median=2ms max=2ms
  500: n=25 min=350ms median=412ms max=507ms
  503: n=44 min=2ms median=2ms max=4ms
order of codes (first 40): 500 500 500 500 500 500 500 500 500 500 500 500 500 500 500 500 500 503 500 503 500 500 500 500 503 500 500 503 500 503 503 503 503 503 503 503 503 503 503 503
transitions after:  {"to": "OPEN"} => 5
per-pod circuit state: all five OPEN
retry outcomes: retried 31 -> 81, exhausted 3 -> 28
payments_charges_total{result="failed"} 75.0
{"level":"WARNING","service":"gateway","msg":"circuit[payments] CLOSED -> OPEN"}   (one line per pod, 11:10:38 to 11:10:42)
```

25 is exactly 5 pods times the threshold of 5: every pod had to fail five checkouts on its own before it stopped calling payments, which is the per-process limitation from the lab's gotcha, here counted precisely. Each 500 cost about 400 ms, two backoff sleeps plus three calls, and each 503 cost 2 ms. The retry and payments counters agree: 25 exhausted requests, 50 retries, 75 failed charges.

> The breakers changed the client's behavior. Once 503s came back in 2 ms instead of 400 ms, the sequential test loop sped up to several checkouts per second per pod, and the rate limiter began rejecting its reserves: between iterations 65 and 80, 10 reserves and 1 pay got 429. A fast-failing dependency turns a well-behaved client into a burst, so the limiter and the breaker are not independent knobs.

### 2.3 The circuit closes after recovery

```plaintext
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
per-pod circuit state right after payments recovered: all five OPEN
$ sleep 35
[1] 200  23ms   [2] 200  14ms   [3] 200  13ms   [4] 200  13ms   [5] 200  13ms
[6] 200  14ms   [7] 200  96ms   [8] 200  13ms   [9] 200  13ms   [10] 200  14ms
[11] 200  12ms  [12] 200  14ms  [13] 200  13ms  [14] 200  13ms  [15] 200  12ms
500s=0 503s=0 200s=15 other=0
transitions after recovery
   {"to": "OPEN"} => 5
   {"to": "HALF_OPEN"} => 5
   {"to": "CLOSED"} => 5
per-pod circuit state after recovery: all five CLOSED
```

Payments was healthy again several seconds before the breakers noticed, and they stayed open until the first request after the cooldown. A breaker that gets no traffic never runs its probe: an idle pod would report `OPEN` in `/health` indefinitely, so that field says nothing about the dependency's current health.

### 2.4 Rate limiter under burst

```plaintext
$ for i in $(seq 1 100); do curl ... http://gateway:8080/events; done
200=46 429=54 in 1s
$ for i in $(seq 1 30); do curl ...; sleep 0.2; done
sustained: 200=30 429=0
rejections (Prometheus): {"path": "/events"} => 96, {"path": "/events/{id}/reserve"} => 30, {"path": "/reserve/{id}/pay"} => 1
```

46 is close to the expected ceiling of 5 pods × 10 rps = 50 per path. The sustained run at 5 rps across 5 pods saw no rejections.

The lab's `rl-headers` command returned `HTTP/1.1 200 OK` in my run. The 50 warm-up requests are spread randomly over 5 pods, and the request that reads the headers lands on a random pod, which often still has budget in its window. Looping until the first 429 instead:

```plaintext
first 429 after 35 requests:
HTTP/1.1 429 Too Many Requests
retry-after: 1
content-type: application/json
body: {"error":"rate_limited","path":"/events","limit_rps":10}
```

> The 30 `reserve` rejections in that list came from the circuit-breaker test. The limiter keys on the path alone, so the test loop and the two mixedload pods draw from one shared 10 rps budget for `/events/{id}/reserve` on each pod, and a single fast client can use up the budget of every other client on that path.

---

## Bonus. Bulkhead isolation

### 3.1 `Bulkhead.call` and the wiring

```python
BULKHEAD_PAYMENTS_MAX = int(os.getenv("BULKHEAD_PAYMENTS_MAX", "10"))
BULKHEAD_PAYMENTS_TIMEOUT_S = float(os.getenv("BULKHEAD_PAYMENTS_TIMEOUT_S", "0.5"))
BULKHEAD_IN_FLIGHT = Gauge("gateway_bulkhead_in_flight", "Calls currently holding a bulkhead slot", ["target"])
BULKHEAD_REJECTIONS = Counter("gateway_bulkhead_rejections_total", "Calls rejected because the bulkhead was full", ["target"])

class Bulkhead:
    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.max_concurrent = max_concurrent
        self.acquire_timeout_s = acquire_timeout_s
        self._sem = asyncio.Semaphore(max_concurrent)

    async def call(self, func):
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self.acquire_timeout_s)
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full ({self.max_concurrent} in flight)")
        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self._sem.release()

payments_bulkhead = Bulkhead("payments", BULKHEAD_PAYMENTS_MAX, BULKHEAD_PAYMENTS_TIMEOUT_S)
```

```python
        pay_resp = await payments_bulkhead.call(
            lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
        )
    ...
    except BulkheadFullError as e:
        log.error(f"{e}, rejecting payment")
        raise HTTPException(503, "Payment service temporarily unavailable (bulkhead full)")
```

For the "without" runs I built a second image, `quickticket-gateway:v1-nobh`, from the same file with only the wrapping line reverted (a one-hunk `diff` in the capture), and switched the Rollout between the two tags. That is the lab's `git stash` experiment without rebuilding in the middle of a test.

### 3.2 The test as written

With `PAYMENT_LATENCY_MS=3000` the lab's `bulkhead-probe` (30 background jobs, each reserving and then paying) gave:

```plaintext
with bulkhead:     28 of 30 "reserve failed"   pay 200: n=2    EVENTS: ok=29 slow=1
without bulkhead:  27 of 30 "reserve failed"   pay 200: n=3    EVENTS: ok=29 slow=1
```

The probe never got to the part it is meant to test. The reason is in events:

```plaintext
30 concurrent POST /events/3/reserve:
      3 200
      1 429
     26 500     Internal Server Error
events log, last minute:
     28 psycopg2.pool.PoolError: connection pool exhausted
DB_MAX_CONNS=10
```

`reserve_tickets` holds a pooled connection while `_get_available` asks for a second one, so ten parallel reserves each hold one connection and wait for another, and the pool of 10 is exhausted at about five concurrent requests. So I moved the reservations out of the measured part: the script reserves sequentially first and then fires all `/pay` calls at once (`bhprobe2.sh`, same `/events` sampler as the lab).

### 3.3 Same load, reservations taken first: no starvation to protect against

5 replicas, all defaults, 30 simultaneous `/pay` calls against 3 s payments:

```plaintext
WITH bulkhead (v1)
  pay 200: n=15 min=3113ms median=4515ms max=4606ms
  pay 429: n=2
  pay 500: n=12 min=3996ms median=4313ms max=4506ms
  /events over 30 calls: p50=9ms p90=61ms max=877ms         EVENTS: ok=28 slow=2
  gateway_bulkhead_rejections_total: (empty result)
  max_over_time(gateway_bulkhead_in_flight[2m]) > 0: 3, 10, 6 on three pods

WITHOUT bulkhead (v1-nobh)
  pay 200: n=17 min=3036ms median=4128ms max=4145ms
  pay 500: n=13 min=3632ms median=3822ms max=4038ms
  /events over 30 calls: p50=11ms p90=18ms max=1090ms       EVENTS: ok=29 slow=1
```

`/events` stays fast in both runs. The lab predicts `ok=0 slow=30` without the bulkhead because "the event loop is starved", but the gateway is async: a `/pay` waiting 3 s for payments is a suspended coroutine that holds a socket and no thread, and the loop keeps serving `/events` in between. Six slow calls per pod do not starve anything. For the same reason the cap did not bind: one pod reached 10 in flight, no request was rejected. (The in-flight values are scrape-time samples every 5 s, so they are lower bounds.) The 500s are not payments failures. All 30 charges finish at about the same moment, the gateway then sends 30 confirms to events at once, and they hit the same pool limit. The events access log has exactly these failures:

```plaintext
$ kubectl logs deploy/events --timestamps | grep 'confirm HTTP/1.1" 500' | cut -c1-19 | uniq -c
     10 2026-10-16T11:18:22
      2 2026-10-16T11:18:23      <- 12 x 500 in the run with the bulkhead
     13 2026-10-16T11:19:02      <- 13 x 500 in the run without it
```

Each of these customers was charged and got "Payment succeeded but confirmation failed".

### 3.4 Control experiment: a shared resource that slow payments can exhaust

The resource a slow dependency can actually take from other paths is the gateway's single `httpx.AsyncClient` connection pool, shared by events, payments and notifications. Its default is 100 connections per pod, which this cluster would only exhaust with about 500 concurrent slow payments. To reach that state at test scale I added one env var, `GATEWAY_MAX_CONNECTIONS` (default 100, the httpx default, so nothing changes unless it is set), and ran on 1 replica with a pool of 20 and `RATE_LIMIT_RPS=1000`, so that the limiter does not hide the effect. Both images ran the same load: 80 reserved checkouts, paid in 4 waves of 20 every 2 s, with the in-flight gauge sampled directly from the pod every 0.2 s next to the lab's `/events` sampler.

```plaintext
WITHOUT bulkhead (v1-nobh)
  pay 200: n=80 min=3868ms median=8237ms max=9942ms
  /events over 30 calls: p50=12ms p90=3379ms max=3709ms
  EVENTS: ok=25 slow=5
  in_flight sampled max: 0

WITH bulkhead (v1, MAX=10)
  pay 200: n=20 min=3029ms median=3401ms max=3604ms
  pay 503: n=60 min=506ms median=581ms max=606ms
  /events over 30 calls: p50=9ms p90=172ms max=353ms
  EVENTS: ok=30 slow=0
  in_flight sampled max: 10
  sum by (target) (gateway_bulkhead_rejections_total)                 {"target": "payments"} => 60
  max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m])    {"pod": "gateway-69d65dbc5b-c59bg", ...} => 10
{"level":"ERROR","service":"gateway","msg":"bulkhead[payments] full (10 in flight), rejecting payment"}
```

Without the bulkhead every payment eventually succeeded, but they queued for the 20 connections: median 8.2 s instead of 3 s, and `GET /events`, which has nothing to do with payments, waited in the same queue for up to 3.7 s. With the bulkhead, payments could hold at most 10 connections, the other 10 stayed free, `/events` kept its p50 of 9 ms, and the 60 checkouts beyond the cap were refused after the 0.5 s acquire timeout instead of waiting 8 s. The gauge reached exactly `BULKHEAD_PAYMENTS_MAX` in Prometheus and in the direct samples, so the cap binds.

An earlier run of the same comparison with 60 payments fired at once gave the same split (without: `/events` p90 2582 ms, `ok=26 slow=4`; with: `ok=30 slow=0`, 50 rejections, 503s in 654 to 762 ms), but Prometheus recorded an in-flight maximum of 0 there. The pod was 3 s old when the run started and the slots were full for about 3.5 s, shorter than one scrape interval. The waves and the 20 s wait for target discovery in the second run exist to fix exactly that.

> The bulkhead had nothing to fix in the scenario the lab describes and a lot to fix in a neighboring one. Isolation pays off where two paths share a finite resource. In this gateway that resource is the HTTP connection pool, and the bulkhead's MAX only helps while it stays below the size of that pool.

### 3.5 Answers

**Why does the bulkhead wrap the circuit breaker and not the other way around?** The bulkhead bounds the number of slow, real calls in flight, so it has to gate entry to the whole chain: one `/pay` takes one slot and keeps it through all its retries, which is what the cap is supposed to count. A breaker fast-fail inside the bulkhead holds its slot for about 2 ms (the 503s in 2.2), so it costs nothing. The reverse order has a concrete failure mode with this code: `CircuitBreaker.call` counts every exception as a failure, `BulkheadFullError` included. In the 3.4 run, with the breaker outside, the first wave alone produced 10 rejections, enough to open the circuit. While it is open, every payment on the pod is refused, including the ones the bulkhead had room for. Then the slow calls already admitted finish successfully and close it again, so the breaker flaps with each wave. Saturation would be read as an outage of a payments service that was only slow, and the transitions metric would report outages that never happened.

**Bulkhead vs rate limiter: what does each protect?** The rate limiter caps how often requests arrive at a path (10 per second per pod, so a cluster ceiling of 50 per path), whatever the dependencies are doing. It protects the gateway and everything behind it from client volume. The bulkhead caps how many calls to one dependency are in flight at once, whatever the arrival rate. It protects the other paths from one slow dependency. The two are linked by Little's law, in flight = rate × latency: at the default 10 rps per pod and 3 s payments latency the limiter admits 30 concurrent payment calls per pod, more than the pool of 20 in the control run, so the limiter alone would not have prevented the starvation in 3.4. When latency rises, a fixed rate turns into growing concurrency, and only the bulkhead bounds that.

---

## Side findings

These are outside the lab's scope, but each one changed how a test had to be run.

1. **`event:<id>:held` only grows.** `reserve_tickets` adds the quantity to this Redis counter, and nothing ever subtracts it: not `confirm_reservation`, not the reservation's TTL. A paid ticket is counted once in `orders` and again in `held`, and `_get_available` subtracts both. After about 250 checkouts event 3 refused reservations with `Not enough tickets (available: 0)` while `GET /events/3` reported `"available":251`. Before Task 2 and before each bonus run I set the counter to the true number of live reservation keys (`held counter=252, live unconfirmed reservations=1`).
2. **The events connection pool is exhausted at about 5 concurrent reserves** (3.2): a nested `getconn()` inside a handler that already holds a connection, with `DB_MAX_CONNS=10`.
3. **Retrying a read timeout on `/charge` is not safe.** The lab's contract retries `httpx.TimeoutException`, which includes read timeouts, where the charge may already have happened. Payments takes no idempotency key and does not deduplicate by `reservation_id`, so a timeout followed by a successful retry can charge twice. I kept the contract and note the risk here. The fix I would make is an idempotency key in payments, so that retries stay safe for every timeout.

To keep the bonus volume within capacity I temporarily set event 3 to 2000 tickets with `UPDATE events SET total_tickets=2000 WHERE id=3` and restored it to 500 afterwards. The `probe` pod and the mixedload Deployment were removed at the end.

---

## Summary

| Check | Expected | Observed | Status |
|---|---|---|---|
| notifications pod Ready, three metrics exported | 1/1, metrics present | 1/1, `requests_total`, `request_duration_seconds`, `notify_total` | met |
| Test #1 checkouts under 30 % notify failure | 30/30 | 30/30 in both runs | met |
| Test #1 `/pay` p99 | < 100 ms | 220 ms in run 1 (one 179 ms request, interpolated), 67.5 ms in the repeat; with 1500 ms injected, max 147 ms | met in repeat, decoupling shown by the latency series |
| Real notify failure rate | about 30 % | 9 of 30 in both runs | met |
| Test #2 checkouts under 30 % payment failure | about 30/30, fail < 2 | 28/30, then 41/42; 3 of 72 overall (4.2 %, expected 2.7 %) | met in aggregate |
| `gateway_retry_total` retried / succeeded_after_retry | non-zero | 13 / 7, later 31 / 18 | met |
| Circuit opens under 100 % failure | 500s then fast 503s | 25 × 500 (about 400 ms), 44 × 503 (2 ms), 5 OPEN transitions | met |
| Circuit closes after cooldown | 200s, CLOSED transitions | 15/15 × 200, 5 HALF_OPEN and 5 CLOSED | met |
| Rate limiter burst / sustained | about 50/50, zero 429 below limit | 46/54, 30/0 | met |
| `Retry-After: 1` on 429 | present | present (after replacing the lab's fixed warm-up with a loop) | met |
| Bulkhead keeps `/events` fast under slow payments | ok about 30, slow 0 | 30/0 with, 25/5 without, in the pool-bound control | met |
| Bulkhead rejections and saturation | non-zero, max = 10 | 60 rejections, `max_over_time` = 10 | met |

The patterns are simple to write. Almost every result in this lab depended on where the state lives and what is actually shared. All four primitives keep their state per process, so the numbers come out in multiples of five: 25 failures before all breakers open, a cluster rate limit of 46 instead of 10, a bulkhead that never fills when the load is spread over five pods. They also interact. Fast-fail 503s turned a slow client into a burst for the rate limiter, retries multiplied 25 user failures into 75 charges, and a bulkhead placed inside the breaker would have tripped it. The lab's two loudest predictions, an event loop starved by slow payments and a p99 inflated by notifications, did not happen in this async gateway. The control runs show where the real coupling is: the shared HTTP connection pool for slow dependencies, and a notification path that fails without a trace on the sending side.
