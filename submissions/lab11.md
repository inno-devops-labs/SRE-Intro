# Lab 11 — Bonus: Advanced Microservice Patterns

## Setup

The k3d cluster is in the state Lab 10 left it in: the gateway is an Argo Rollout with 5 replicas (canary + `gateway-error-rate` AnalysisTemplate from Lab 7), `events` and `payments` run 1 replica each, Postgres is on a PVC (Lab 9), and the in-cluster Prometheus from Lab 7 Bonus scrapes every gateway pod every 5 s. Every app container has `requests: 50m/64Mi`, `limits: 200m/256Mi`.

All experiments ran on 2026-10-20 between 22:30 and 23:05 MSK, with `labs/lab8/mixedload.yaml` (2 replicas, reserve → pay on event 1) running in the background unless stated otherwise. Timestamps in pod logs are UTC (MSK − 3 h).

### What I had to fix before the first deploy

- **The `ghcr-secret` pull secret no longer exists in the cluster** (`kubectl get secret ghcr-secret` → `NotFound`), while every app manifest still uses `imagePullPolicy: Always` + that secret. Running pods were fine (images cached on the node), but any restart would have had to re-pull from GHCR. For this lab the gateway runs a locally built image anyway, so the Rollout got `imagePullPolicy: IfNotPresent` and no `imagePullSecrets`. I also switched `payments` to `IfNotPresent`, because every `kubectl set env deployment/payments` below restarts that pod.
- **Image tags.** The lab suggests rebuilding with the same tag `quickticket-gateway:v1`. With the same tag, `kubectl argo rollouts set image` doesn't change the pod spec, so nothing rolls. I used unique tags instead (`lab11-v1`, `lab11-v2`). Every gateway change below is a real canary rollout (20 % → AnalysisRun → 50 % → 100 %), and all of them passed analysis.
- **`kubectl run … curlimages/curl:latest` took over 60 s to start**, because `:latest` implies `imagePullPolicy: Always`. I added `--image-pull-policy=IfNotPresent` to every test pod.
- ArgoCD auto-sync is still off (Lab 7/10), so my `kubectl` changes to the live objects are not reverted.

```bash
docker build -t quickticket-notifications:v1 ./app/notifications
docker build -t quickticket-gateway:lab11-v2 ./app/gateway
k3d image import -c quickticket quickticket-notifications:v1 quickticket-gateway:lab11-v2
kubectl apply -f k8s/notifications.yaml
kubectl patch rollout gateway --type=json --patch-file gw-patch.json   # image, pull policy, the env vars below
kubectl argo rollouts status gateway --timeout=360s                    # Healthy
```

```
$ kubectl argo rollouts get rollout gateway
Status:          ✔ Healthy
Strategy:        Canary  Step: 6/6  SetWeight: 100  ActualWeight: 100
Images:          quickticket-gateway:lab11-v1 (stable)
│  └──α gateway-75b945f6dd-12-2      AnalysisRun  ✔ Successful  2m21s  ✔ 3

$ curl http://gateway:8080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","notifications":"ok","circuit_payments":"CLOSED"}}
```

> **Repo vs cluster.** In this branch (cut from `main`) `k8s/gateway.yaml` is still the Lab 4 `Deployment`; the Rollout version lives on `feature/lab7`. I added the Lab 11 env vars to `k8s/gateway.yaml` and applied the same values to the live Rollout with `kubectl patch`. The final live env is listed at the end of this report.

Before the first deploy I ran a quick unit check of all four primitives inside the built gateway image (flaky 500 → retried to success, 404 → 1 attempt, CB opens/fast-fails/closes, `RateLimiter(3)` → `[T,T,T,F,F]`, `Bulkhead(2)` with 4 concurrent callers → exactly 2 `BulkheadFullError`). Output: `ALL OK`.

---

## Task 1 — Notifications Service + Retries

### 11.1: `app/notifications/`

`main.py` is the payments service with the port, names and metrics changed. The one deliberate difference: latency is injected with `await asyncio.sleep()`, not `time.sleep()`. A slow notification then doesn't hold one of the 40 worker threads (that difference matters in the bonus task).

```python
NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

REQUEST_COUNT = Counter("notifications_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("notifications_request_duration_seconds", "Request duration", ["method", "path"])
NOTIFY_TOTAL = Counter("notifications_notify_total", "Total notification attempts", ["result"])

@app.get("/health")
def health():
    return {"status": "healthy", "failure_rate": NOTIFY_FAILURE_RATE, "latency_ms": NOTIFY_LATENCY_MS}

@app.post("/notify")
async def notify(body: dict = None):
    body = body or {}
    event = body.get("event", "unknown")
    order_id = body.get("order_id", "unknown")

    if NOTIFY_LATENCY_MS > 0:
        await asyncio.sleep(NOTIFY_LATENCY_MS / 1000)

    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning(f"Notification failed (injected) event={event} order={order_id}")
        raise HTTPException(500, "Notification delivery failed")

    NOTIFY_TOTAL.labels("success").inc()
    log.info(f"Notification sent event={event} order={order_id}")
    return {"status": "sent", "event": event, "order_id": order_id}
```

The metrics middleware and `/metrics` are copied from payments unchanged.

`requirements.txt` (same as payments, no DB/Redis):

```
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

The `Dockerfile` and `.dockerignore` are copied from payments with `EXPOSE`/`--port` set to 8083. I also added the service to `app/docker-compose.yaml` and set `NOTIFICATIONS_URL` on the compose gateway.

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

(Flow-style maps shortened here; the file uses block style like the other manifests.)

```
$ kubectl get pods -l app=notifications
notifications-dccd599cc-jg7lm   1/1     Running   0          3m29s
```

### 11.3: Gateway → notifications

Added to `k8s/gateway.yaml` (and to the live Rollout):

```yaml
- name: NOTIFICATIONS_URL
  value: "http://notifications:8083"
# Lab 11 resilience tunables (change via kubectl set env, no rebuild)
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

**Bug found in the pre-wired helper.** In the first Test #1 run, `notifications` counted 42 failed `/notify` calls, but the gateway logged **0** `notify failed` warnings. `_notify_order_confirmed` only catches *exceptions*. httpx doesn't raise on a 500 response, so a notification that failed with a 500 was silently treated as delivered. I added one line:

```python
        resp = await client.post(f"{NOTIFICATIONS_URL}/notify", json={...}, timeout=2.0)
        resp.raise_for_status()  # a 5xx from notifications is a failure too, not just network errors
```

After rebuilding (`lab11-v2`), the gateway logs every failed notification, while the user flow is still unaffected (Test #1 below):

```
[pod/gateway-7b886b8dc5-2qw9t/gateway] {"level":"WARNING","service":"gateway","msg":"notify failed (non-critical)
  order=c02efe2a-7d18-4c1a-a331-1c4ae68c154d err=Server error '500 Internal Server Error' for url 'http://notifications:8083/notify'
```

Side note: `kubectl logs -l app=gateway` without `--tail=-1` shows only the last 10 lines *per pod* when a selector is used. My first count of these warnings was wrong because of that.

### 11.4: `call_with_retry`

```python
def _is_retryable(exc: Exception) -> bool:
    """Transient errors only: network/timeout, 5xx, 408, 429. Other 4xx won't fix themselves."""
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code >= 500 or code in (408, 429)
    return False


async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    base_delay = RETRY_BASE_DELAY_MS / 1000
    for attempt in range(max_retries):
        try:
            result = await func()
            if attempt > 0:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result
        except Exception as e:
            if not _is_retryable(e):
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                raise
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                log.error(f"retry[{target}] exhausted after {max_retries} attempts: {e!r}")
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            log.warning(f"retry[{target}] attempt={attempt + 1} failed ({e!r}), sleeping {delay:.3f}s")
            await asyncio.sleep(delay)
```

`RETRY_MAX=3` means 3 attempts in total, so a failing call waits 100–200 ms after attempt 1 and 200–300 ms after attempt 2: ~0.35 s of backoff before it gives up. Note that `CircuitOpenError` and `BulkheadFullError` are not retryable by this definition. That matters for the composition question below.

### 11.5: Test #1 — fire-and-forget under notify failure

```bash
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
```

Checkout burst, 30 reserve → pay chains on event 3 (lab script):

```
result: ok=30 fail=0
```

`/pay` latency from Prometheus, `histogram_quantile(0.99, sum by (le,path) (rate(gateway_request_duration_seconds_bucket[2m])))`:

| path | p99 (run 1, `lab11-v1`) | p99 (run 2, `lab11-v2`) |
|---|---:|---:|
| `/reserve/{id}/pay` | 85 ms | **25 ms** |
| `/events/{id}/reserve` | 98 ms | 25 ms |
| `/events` | 63 ms | 24 ms |

Every notification took ~300 ms (`notifications_request_duration_seconds_sum / _count` = 386.1 s / 1282 = **0.301 s**), but `/pay` p99 stayed **below 100 ms** in both runs. If the notification were on the request path, `/pay` p99 would be at least 300 ms. `/pay` is also no slower than the paths that never call notifications.

Real failure rate, from the notifications pod's own `/metrics`:

```
notifications_notify_total{result="success"} 892.0
notifications_notify_total{result="failed"} 390.0       → 390 / 1282 = 30.4 % (configured: 30 %)
```

(During the 30-chain burst alone: +93 success / +33 failed. The rest is mixedload traffic.) The gateway logged 125 `notify failed (non-critical)` warnings in the same 3 minutes. The failures happened, they are visible in logs, and 0 of them reached a user.

Restored with `NOTIFY_FAILURE_RATE=0.0 NOTIFY_LATENCY_MS=0`.

### 11.6: Test #2 — retries under 30 % payment failure

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
```

```
result: ok=30 fail=0
```

`sum by (target,result) (gateway_retry_total)` — before the test the series did not exist; after:

```
{target="payments", result="retried"}                39
{target="payments", result="succeeded_after_retry"}  29
{target="payments", result="exhausted"}               2
```

Gateway log, one of the recovered calls:

```
{"level":"WARNING","service":"gateway","msg":"retry[payments] attempt=1 failed (HTTPStatusError("Server error
  '500 Internal Server Error' for url 'http://payments:8082/charge' ...)), sleeping 0.137s"}
```

These counters include mixedload traffic. Both `exhausted` cases were mixedload calls (the burst itself was 30/30); with p = 0.3, 0.3³ = 2.7 % of calls should fail all 3 attempts. Overall `/pay` status counts over the test: **749 × 200, 2 × 500, 1 × 503**. Without retries, ~30 % of those would have been 500s.

The single 503 came from restoring payments. `kubectl set env` replaces the only payments pod, and one call hit the terminating pod 3 times in a row:

```
retry[payments] exhausted after 3 attempts: ConnectError('All connection attempts failed')
payments unreachable
```

The retries' ~0.35 s budget covered most of the pod swap, but not all of it. A second payments replica (or a PDB + preStop, which is Lab 12) would close that gap.

### Answers

**Why should notifications be non-blocking (fire-and-forget)?**
A notification is a side effect of a completed order, not part of the order. At the moment we would send it, the money is charged and the reservation is confirmed. If `/pay` waited for it, the user would get notifications' latency (+300 ms in Test #1) and, worse, its errors: a failed email would turn a successful purchase into a 5xx, the user would retry and might pay twice. With `asyncio.create_task` the user's SLO depends only on the critical path (events + payments). Notifications can be slow, broken, or not deployed, and the order still completes. The same reasoning keeps notifications out of the `/health` verdict. The trade-off: a fire-and-forget task is lost if the gateway pod dies before it finishes, and there is no retry. A real system would put the notification on a durable queue (outbox pattern) and add a failure counter on the gateway side; today the only signal is the log line.

**Why is `cb.call(retry(...))` the right composition, not `retry(lambda: cb.call(...))`?**
- **What the breaker counts.** With `cb.call(retry(...))`, the breaker sees one outcome per user request, the result *after* retries. A transient blip that a retry fixes (Test #2: 29 recoveries) never counts against payments. Only "still failing after 3 attempts" does, and that is the signal the breaker exists for. With the reverse order, every individual attempt is a CB event, so short flakiness opens the circuit even though retries would have hidden it.
- **What happens when the circuit is open.** `retry(cb.call(...))` puts the fast-fail inside the retry loop. A generic retry (one that retries any exception) sleeps and retries the `CircuitOpenError`, so the "fast" fail now takes 0.35 s, and every retry in `HALF_OPEN` is another probe at a service that is just recovering. In my implementation `CircuitOpenError` is non-retryable, so it would re-raise at once; the composition would then be harmless but pointless.
- **Load amplification.** Retry outside the breaker multiplies traffic in exactly the situation where the breaker should cut it to zero.

One side effect of the correct order, which the lab's pitfalls list also mentions: with `CB_FAILURE_THRESHOLD=5` and 3 attempts, it takes **15 failed downstream calls per gateway pod** (75 cluster-wide) to open every circuit. That is visible in the Task 2 sequence below.

---

## Task 2 — Circuit Breaker + Rate Limiter

### 11.7: `CircuitBreaker.call`

```python
    async def call(self, func):
        """Run func with circuit-breaker protection; raises CircuitOpenError when open."""
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

#### OPEN under 100 % payment failure

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0
```

I extended the lab probe to also print the order of the results:

```
first  /pay: 500|0.453636 {"detail":"Payment failed"}
sequence (x=500 retry-exhausted, o=503 circuit-open): xxxxxxxxooxoooooxooooooooooooooooooooooooooooooooooooooooooooooooooooo
500s=10 503s=60  elapsed=6s
```

- The first `/pay` took **0.45 s**: 3 attempts plus the backoff.
- After the 10th 500 every pod's circuit is open, and the rest are **503s that return in milliseconds**. 70 `/pay` calls finished in 6 s in total.
- It took only 10 visible 500s, not the 25 you might expect for 5 pods × threshold 5, because mixedload was failing too and fed the same breakers.
- 80 iterations gave 70 results. The other 10 were rate-limited (8 reserves + 2 pays → 429, see 11.8): the probe fires requests back-to-back and some pods briefly got > 10 req/s for one path.

All five pods opened within 3.2 s of each other (one `CircuitBreaker` per process):

```
19:38:59,775 gateway-7b886b8dc5-2s65n circuit[payments] CLOSED -> OPEN
19:39:00,984 gateway-7b886b8dc5-2qw9t circuit[payments] CLOSED -> OPEN
19:39:01,399 gateway-7b886b8dc5-4jv4z circuit[payments] CLOSED -> OPEN
19:39:02,392 gateway-7b886b8dc5-67pdc circuit[payments] CLOSED -> OPEN
19:39:02,999 gateway-7b886b8dc5-ws9c9 circuit[payments] CLOSED -> OPEN
```

```
sum by (to) (gateway_circuit_breaker_transitions_total)   →  {to="OPEN"} 5

$ curl http://gateway:8080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","notifications":"ok","circuit_payments":"OPEN"}}
```

`/health` stays `healthy` while the circuit is open. `payments`' own `/health` is fine; only `/charge` fails. Two consequences: the readiness probes (on `/metrics` since Lab 8) don't pull the gateway pods out, which is correct; and the health check can't tell anyone that checkout is down. Only `circuit_payments` shows it, and only for whichever pod answered the request.

#### HALF_OPEN → OPEN, then CLOSED after recovery

I restored payments ~10 s *after* the 30 s cooldown had already expired, so the logs show both outcomes of `HALF_OPEN`:

```
19:39:29,799 gateway-7b886b8dc5-2s65n circuit[payments] OPEN -> HALF_OPEN
19:39:30,298 gateway-7b886b8dc5-2s65n circuit[payments] HALF_OPEN -> OPEN      ← payments still at 100 %, probe failed, re-open
   … same for the other 4 pods between 19:39:31 and 19:39:35 …
(19:39:40 PAYMENT_FAILURE_RATE=0.0, then sleep 35)
19:40:02,039 gateway-7b886b8dc5-2qw9t circuit[payments] OPEN -> HALF_OPEN
19:40:02,047 gateway-7b886b8dc5-2qw9t circuit[payments] HALF_OPEN -> CLOSED    ← probe succeeded in 8 ms
19:40:04,143 gateway-7b886b8dc5-67pdc circuit[payments] OPEN -> HALF_OPEN
19:40:04,148 gateway-7b886b8dc5-67pdc circuit[payments] HALF_OPEN -> CLOSED
   … 2s65n, ws9c9, 4jv4z closed by 19:40:06,983 …
```

Recovery probe (lab script, 15 chains):

```
[1] 200  [2] 200  [3] 200  [4] 200  [5] 200  [6] 200  [7] 200  [8] 200
[9] 200  [10] 200 [11] 200 [12] 200 [13] 200 [14] 200 [15] 200
```

```
sum by (to) (gateway_circuit_breaker_transitions_total)
  {to="OPEN"}       10      (5 initial + 5 re-opens from a failed HALF_OPEN probe)
  {to="HALF_OPEN"}  10
  {to="CLOSED"}      5
```

```
{"status":"healthy","checks":{"events":"ok","payments":"ok","notifications":"ok","circuit_payments":"CLOSED"}}
```

**Limitations I observed:**
- **One breaker per process.** Five pods learn that payments is down five separate times, and the 15-calls-per-pod cost to open (see Task 1) is paid five times over. During a real payments outage that is up to ~75 extra failing calls cluster-wide, every 30 s cooldown.
- **`HALF_OPEN` isn't limited to one probe.** Between the `HALF_OPEN` transition and the first result, every concurrent `/pay` on that pod also goes through to payments. At this traffic level the window was 5–500 ms. Under heavy traffic it would be a small thundering herd at a service that is just recovering. A production breaker lets exactly *N* probe calls through in `HALF_OPEN`.

### 11.8: `RateLimiter.allow`

```python
    def allow(self, key: str) -> bool:
        """Return True if the request should be allowed."""
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

#### Burst results

The lab's burst (100 sequential `GET /events` through the Service) did **not** reproduce the expected ~50/50 split:

```
200=99 429=1 elapsed=5s
```

One `curl` at a time took 5 s for 100 requests, so ~20 req/s across 5 pods, ~4 req/s per pod. That is under `RATE_LIMIT_RPS=10`, so the limiter correctly let almost everything through. A sequential client can't exceed a per-pod limit when kube-proxy spreads it across 5 pods. To actually test the limiter I ran three more bursts:

| Test | Result | What it shows |
|---|---|---|
| 100 sequential requests **to one pod IP** (`10.42.0.41:8080`) | **200=18, 429=82** in ~1.8 s | ~10 allowed per sliding second; the per-pod limit holds exactly |
| 30 requests at 5 req/s (`sleep 0.2`) | **200=30, 429=0** | Sustained load under the limit is never rejected |
| 100 requests, **25 parallel** workers, through the Service, `RATE_LIMIT_RPS=10` | 200=29, **429=41**, **502=30** (run 2; run 1: 37/31/32) | The limiter kicks in, but so does something else: see below |

`Retry-After` on a 429 (single pod after 15 rapid hits):

```
HTTP/1.1 429 Too Many Requests
retry-after: 1
{"error":"rate_limited","path":"/events","limit_rps":10}
```

`sum by (path) (gateway_rate_limit_rejections_total)` after these tests:

```
{path="/events"}               43
{path="/events/{id}/reserve"}   8      (from the CB probe in 11.7)
{path="/reserve/{id}/pay"}      2
```

Rejections are spread unevenly across pods (`/events`: 13, 13, 8, 5, 4). kube-proxy picks a random backend per connection, so a parallel burst doesn't split evenly, and the busiest pod hits its 10/s much sooner than the average.

#### The 502s: the limit is above what `events` can serve

The 502s in the parallel burst are not from the limiter:

```
$ kubectl logs deploy/events --since=2m | grep … | sort | uniq -c
     74 psycopg2.pool.PoolError: connection pool exhausted
```

This is the Lab 10 bottleneck: the `events` pool (`DB_MAX_CONNS=10`) raises instead of queueing, and the `events` + Postgres pair breaks at ~19–22 RPS. The gateway's limit is **10 req/s per path per pod × 5 pods = 50 req/s** for `/events`, **2.5× more than `events` can serve**. With 10 parallel workers (~6 req/s per pod), the limiter never fired at all, and `events` still returned errors:

```
100 requests, 10 workers, RATE_LIMIT_RPS=10:      90 × 200, 10 × 502, 0 × 429
```

So with the default setting, the rate limiter doesn't protect anything in this system: the downstream fails before the limit is reached. I changed only the env var (no rebuild; another canary rollout) to size the limit from the Lab 10 capacity number, **4 req/s × 5 pods = 20 req/s**:

```
100 requests, 25 workers, RATE_LIMIT_RPS=4:       13 × 200, 84 × 429, 3 × 502    (events PoolError: 6, was 74)
```

The same burst now gets mostly cheap 429s with `Retry-After: 1` (the client knows to back off) instead of 502s, and `events` stays mostly healthy. 3 × 502 remain because `mixedload` was also hitting `/events` from other pods. The lesson: **a rate limit is a capacity number.** It has to come from the load test of the weakest dependency behind it, not from a round default, and it has to be divided by the replica count because the state is per pod.

I set `RATE_LIMIT_RPS` back to 10 (the value in the manifest) after the bonus task. Lowering it permanently is a decision for after the Lab 10 `events` fixes, because those fixes would raise the ceiling.

(The first parallel run right after that rollout also returned **26 × `000`** (connection refused). Old gateway pods were still terminating and receiving new connections. That is the "no preStop hook" gap Lab 12 deals with. I waited 20 s and re-ran.)

#### Observability gap: 429s are invisible in `gateway_requests_total`

During the CB probe, `gateway_rate_limit_rejections_total` showed 8 rejected reserves, but `sum by (status) (increase(gateway_requests_total{path="/events/{id}/reserve"}[2m]))` showed **only `status="200"`**. The cause is the middleware order. Starlette makes the middleware declared *last* the *outermost* one, and `rate_limit_middleware` is declared after `metrics_middleware`. So a 429 is returned before the metrics middleware runs, and it never gets a status or a latency observation. Consequences:
- The SLI dashboards from Lab 3 and the canary AnalysisTemplate from Lab 7 don't see throttled users at all. A deploy that sets the limit too low would pass canary analysis.
- The only signal is the separate rejections counter.

The fix is to swap the declaration order of the two middlewares, so metrics wraps the limiter. I didn't change it in this lab, because every experiment above was measured with the current order, but it belongs on the backlog.

---

## Bonus Task — Bulkhead Isolation

### 11.9: `Bulkhead` and the wiring

```python
BULKHEAD_PAYMENTS_MAX = int(os.getenv("BULKHEAD_PAYMENTS_MAX", "10"))
BULKHEAD_PAYMENTS_TIMEOUT_S = float(os.getenv("BULKHEAD_PAYMENTS_TIMEOUT_S", "0.5"))

BULKHEAD_IN_FLIGHT = Gauge(
    "gateway_bulkhead_in_flight", "Calls currently holding a bulkhead slot", ["target"]
)
BULKHEAD_REJECTIONS = Counter(
    "gateway_bulkhead_rejections_total", "Calls rejected because the bulkhead was full", ["target"]
)


class BulkheadFullError(Exception):
    """Raised by Bulkhead.call when no slot frees up within the acquire timeout."""


class Bulkhead:
    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.max_concurrent = max_concurrent
        self.acquire_timeout = acquire_timeout_s
        self._sem = asyncio.Semaphore(max_concurrent)

    async def call(self, func):
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self.acquire_timeout)
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

In `pay_reservation`:

```python
        pay_resp = await payments_bulkhead.call(
            lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
        )
        payment_ref = pay_resp.json().get("payment_ref", "unknown")
    except BulkheadFullError:
        log.error("payments bulkhead full, rejecting /pay")
        raise HTTPException(503, "Payment service busy (bulkhead full) — try again shortly")
    except CircuitOpenError:
        ...
```

`release()` and the gauge decrement are in `finally`, so a slot is returned on success, on an exception, and on cancellation (client disconnect). The semaphore is only acquired if `wait_for` succeeded, so a timed-out waiter never releases a slot it doesn't hold.

### 11.10: Proving the isolation

**Method.** To compare the two variants without a code change, I used the same image (`lab11-v2`) for both and changed only `BULKHEAD_PAYMENTS_MAX`: **10** (bulkhead on) vs **100000** (effectively no bulkhead). Each change was a normal canary rollout. For both runs I also set `RATE_LIMIT_RPS=1000`. Otherwise the limiter (max 10 `/pay` per pod per second) would stop requests before the bulkhead does and hide what I was measuring. Payments: `PAYMENT_LATENCY_MS=3000`, `PAYMENT_FAILURE_RATE=0.0`.

I ran two probes in each variant:

1. **The lab probe**: 30 concurrent `/pay` through the Service, plus 30 `/events` samples. I changed one thing: reservations are created *sequentially* first, then the 30 pays fire concurrently. The original version creates 30 concurrent reserves, and in my first run they exhausted the `events` DB pool (Lab 10 again), so only 4 pays happened. I also used event 1 (≈ 988k tickets) instead of event 3, to have enough stock for repeated runs.
2. **A pinned stress probe**: 150 concurrent `/pay` sent to **one gateway pod's IP**, with `/events` sampled every 0.1 s **on the same pod** and that pod's `/metrics` sampled every 0.1 s for `gateway_bulkhead_in_flight`. The lab probe spreads 30 pays across 5 pods (~6 per pod, below `MAX=10`), so the cap never binds there. Pinning to one pod is the only way to really overload one pod's bulkhead.

#### Results

| | **With bulkhead (MAX=10)** | **Without bulkhead** |
|---|---|---|
| **Lab probe** — `/pay` codes | 20 × 200, 10 × 500 | 19 × 200, 11 × 500 |
| **Lab probe** — `EVENTS:` | **ok=30 slow=0** (median 13 ms, max 357 ms) | ok=29 slow=1 (median 13 ms, max 796 ms) |
| **Pinned** — `/pay` codes (150) | **10 × 200, 140 × 503** | 35 × 200, 18 × 500, 2 × 503, **95 × 504** |
| **Pinned** — `/pay` time | median **0.92 s**, max 3.8 s | median **21.9 s**, max 27.6 s |
| **Pinned** — peak `in_flight` on that pod | **10.0** | **151.0** |
| **Pinned** — `EVENTS:` (same pod; idle baseline 11 ms) | **ok=30 slow=0** (median 11 ms, max 233 ms) | **ok=23 slow=7** (median 22 ms, **max 6.0 s**) |
| Retries during the run | — | **202 retried, 120 exhausted, 0 succeeded** |
| payments pod | fine | **killed by its liveness probe** |

```
### WITH bulkhead (MAX=10) — 150 concurrent /pay pinned to gateway-74cd8cf675-4rvs5
reserved: 150
baseline /events (idle pod): median=0.010918s max=0.011879s
PAY codes:      10 200     140 503
PAY time:  median=0.924558s max=3.790048s
in_flight sampled from pod /metrics every 0.1s: max=10.0  samples=40
EVENTS: ok=30 slow=0  median=0.011245s max=0.233125s

### WITHOUT bulkhead — 150 concurrent /pay pinned to gateway-74977d675f-2bj2r
reserved: 150
baseline /events (idle pod): median=0.011336s max=0.012538s
PAY codes:      35 200      18 500       2 503      95 504
PAY time:  median=21.898085s max=27.630610s
in_flight sampled from pod /metrics every 0.1s: max=151.0  samples=40
EVENTS: ok=23 slow=7  median=0.021699s max=6.003709s
```

Prometheus:

```
sum by (target) (gateway_bulkhead_rejections_total)           → {target="payments"} 140   (280 after the 2nd run)
"bulkhead full" log lines on the target pod                   → 140
```

`max_over_time(gateway_bulkhead_in_flight{target="payments"}[3m])` in Prometheus showed only **2** for the target pod, not 10. The 10 slots were held for ~3 s, and Prometheus scrapes every 5 s, so the scrapes missed the peak. A gauge only shows its value at scrape time. That's why I sampled the pod's `/metrics` directly every 0.1 s, which gave **max = 10.0** with the bulkhead and **151.0** without it (150 + one mixedload call). With the bulkhead on, the cap binds exactly at `MAX`. A peak like this should be measured with a counter or a histogram (e.g. time spent waiting for a slot), not with a gauge.

#### What happens without a bulkhead

The lab expects asyncio's event loop to be the shared resource that gets starved. In this gateway it isn't: an `await` on a slow httpx call costs nothing, which is why the 30-call lab probe looks almost identical in both variants (one slow `/events` sample out of 30). The shared resources that actually ran out in the pinned run were:

1. **The gateway's httpx connection pool.** One `httpx.AsyncClient` per pod is shared by *all* downstreams, with the default `max_connections=100`. 150 slow `/charge` calls took all 100 connections, so `/events` requests on that pod **waited in the same pool** for a free connection, up to 6.0 s. That is the "ship sinks through one compartment" scenario: `/events` has nothing to do with payments, yet it got slow.
2. **Payments' worker threads.** `/charge` is a sync handler with `time.sleep(3)`, run in FastAPI's 40-thread pool. 100 concurrent charges means 40 are served and 60 queue. Queued calls go past the gateway's 5 s timeout → `ReadTimeout` → **retried**. Every retry adds another queued charge, a retry storm: 202 retries, 120 exhausted, 0 recovered.
3. **Payments' liveness probe.** `GET /health` is also a sync handler and needs one of the same 40 threads. It couldn't get one in time:

```
Warning Unhealthy pod/payments-57b9dfd678-m5595  Liveness probe failed: Get "http://10.42.0.99:8082/health":
                                                 context deadline exceeded (Client.Timeout exceeded while awaiting headers)
Normal  Killing   pod/payments-57b9dfd678-m5595  Container payments failed liveness probe, will be restarted
```

So the *gateway* overloaded payments until Kubernetes **killed the payments pod**: a slowdown became an outage for every gateway pod, including the 4 that weren't under load (`ConnectError … payments unreachable`, circuits opening). In the end 95 users waited ~22 s for a 504, and some of them may have been charged anyway (a timed-out charge can still complete on the payments side).

With the bulkhead, the same 150 calls on the same pod meant: 10 calls in flight to payments, 140 told "busy, retry shortly" (503) within ~0.5–0.9 s, payments never saturated, `/events` at its baseline.

#### Another finding: "charged but not confirmed"

The 10 × 500 in the **lab probe** (both variants) are not payment failures:

```
     10 confirm error after payment: Server error '500 Internal Server Error' for url 'http://events:8081/reservations/…/confirm'
```

30 pays that started at the same time finish at the same time 3 s later, so 30 `confirm` calls hit `events` at once. Its 10-connection non-queueing pool (Lab 10) rejects a third of them. **The customer was charged, the reservation was not confirmed**, and the gateway returns "Payment succeeded but confirmation failed — contact support". The bulkhead doesn't prevent this (it protects payments, not `events`). The fixes are the Lab 10 ones (a blocking pool, more `events` replicas) plus a retry on `confirm`. `confirm` is the operation that must not be lost after a charge, so it should be idempotent and retried, or reconciled asynchronously.

Restored: `PAYMENT_LATENCY_MS=0`, `BULKHEAD_PAYMENTS_MAX=10`, `RATE_LIMIT_RPS=10`.

### Answers

**Why does the bulkhead need to wrap the circuit breaker, not the other way around?**
The bulkhead limits how much capacity one dependency can hold, so a slot must be held for the whole time we are waiting on payments: every retry attempt *and* the backoff sleeps between them. That only works when the bulkhead is the outermost layer: one `/pay` = one slot from first attempt to final outcome. A bulkhead inside the retry would be re-acquired per attempt, so retries would get around the cap, and a call in backoff would hold no slot while still being in progress.
Putting the bulkhead outside the CB costs nothing in the fast-fail case: when the circuit is open, `cb.call` raises `CircuitOpenError` in microseconds and the `finally` releases the slot at once. Only slow, real calls hold slots, which is the point. The reverse, `cb.call(bulkhead.call(...))`, has a real bug: `BulkheadFullError` would count as a *payments failure*. Five local rejections caused by the gateway's own traffic spike, with payments healthy but slow, would open the circuit and cut off *all* payments for 30 s. The breaker should react to the dependency's health, the bulkhead to our own concurrency, and the order keeps those two signals separate.

**Bulkhead vs rate limiter — both reject excess traffic. What's the difference in what they protect against?**
- The **rate limiter** limits **arrival rate** at the front door (requests/s per path), no matter how fast or slow requests are served. It protects the system from *too much incoming traffic*: clients, retry storms from outside, abuse. Cluster-wide its ceiling is `RPS × replicas` (50/s per path here), and as 11.8 showed, it is only useful if that number comes from downstream capacity.
- The **bulkhead** limits **concurrency per dependency** (calls in flight to payments). It doesn't care about the arrival rate; 10 requests/s is fine if payments answers in 10 ms and too much if it takes 3 s. It protects *the rest of the system from one slow dependency*: the shared connection pool, `/events`, and payments itself.

The two tests show the difference. During the bulkhead test the rate limiter was set to 1000 rps and let everything through, while the bulkhead rejected 140 calls; in 11.8 the limiter rejected 84 `/events` requests that no bulkhead would ever see. Little's law connects them: in-flight = rate × latency. The rate limiter fixes the rate. The bulkhead fixes the in-flight count, and that is the only one of the two that still holds when latency goes up by 300×.

---

## Final state

```
$ kubectl get rollout gateway -o json | … containers[0]
quickticket-gateway:lab11-v2 IfNotPresent
  EVENTS_URL = http://events:8081
  PAYMENTS_URL = http://payments:8082
  GATEWAY_TIMEOUT_MS = 5000
  APP_VERSION = v4
  NOTIFICATIONS_URL = http://notifications:8083
  RETRY_MAX = 3
  RETRY_BASE_DELAY_MS = 100
  CB_FAILURE_THRESHOLD = 5
  CB_COOLDOWN_S = 30
  RATE_LIMIT_RPS = 10
  BULKHEAD_PAYMENTS_MAX = 10
  BULKHEAD_PAYMENTS_TIMEOUT_S = 0.5

payments:      PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0
notifications: NOTIFY_FAILURE_RATE=0.0  NOTIFY_LATENCY_MS=0

$ curl http://gateway:8080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","notifications":"ok","circuit_payments":"CLOSED"}}
```

`mixedload` scaled back to 0 replicas, as Lab 10 left it.

## Backlog from this lab

1. **Middleware order:** count 429s in `gateway_requests_total` (swap the declarations), so SLIs and canary analysis see throttled users.
2. **Size `RATE_LIMIT_RPS` from capacity** (≈ 4/pod today, given the ~19 RPS `events` ceiling from Lab 10), and re-size it after the `events` fixes.
3. **Separate httpx clients (or `Limits`) per downstream** in the gateway. One shared pool of 100 connections is itself a missing bulkhead: payments could starve `/events` without the semaphore.
4. **`confirm` after charge must not fail silently:** retry it idempotently or reconcile asynchronously (10 "charged but not confirmed" in one 30-call burst).
5. **payments `/health` must not share a thread pool with slow `/charge` work** (make it `async def`, or move liveness to a trivial endpoint). Otherwise overload turns into a liveness kill (Lab 8/10 saw the same pattern with `events` readiness).
6. **CB `HALF_OPEN` should let exactly one probe through**, and the CB state should be shared (Redis / mesh) so 5 pods don't each pay 15 failed calls to learn that payments is down.
7. **Restore pull credentials** (`ghcr-secret` is gone) or make the GHCR packages public. Otherwise the next restart of an `Always`-pulled pod depends on luck.
