# Lab 11 — Bonus: Advanced Microservice Patterns

![difficulty](https://img.shields.io/badge/difficulty-advanced-red)
![topic](https://img.shields.io/badge/topic-Microservice%20Patterns-blue)
![points](https://img.shields.io/badge/points-10-orange)
![tech](https://img.shields.io/badge/tech-Python%20%2B%20httpx-informational)

> **Goal:** Add a 4th service to QuickTicket, implement inter-service resilience patterns (retries, timeouts, circuit breaker, rate limiter), and test them under real failure injection.
> **Deliverable:** A PR from `feature/lab11` with the new service, gateway changes, updated K8s manifests, and `submissions/lab11.md`. Submit PR link via Moodle.

> 📖 **Read first:** [`lectures/reading11.md`](../lectures/reading11.md) — covers the patterns.

---

## Overview

In this lab you:

- Write a **notifications** service (4th microservice), following the payments template.
- Add **retry with exponential backoff + jitter** to the gateway's critical-path calls.
- Add a **circuit breaker** in front of payments.
- Add a simple **per-endpoint rate limiter** to the gateway.
- Test each pattern by injecting real faults on your k3d cluster.

This is a bonus lab, so the scaffolding is lighter than earlier weeks — you're expected to write most of the code yourself. Each task block lists explicit requirements and behavior contracts; the actual implementations are yours.

---

## Project State

**You should have from previous labs:**

- QuickTicket on k3d with 5 gateway replicas (from Lab 7, as an Argo Rollouts Rollout).
- `labs/lab8/mixedload.yaml` loadgen (reserve + pay traffic) from Lab 8.
- In-cluster Prometheus from Lab 7 Bonus — you'll query it to verify retry/CB/rate-limit behavior.

**This lab adds:**

- `app/notifications/` — new microservice.
- A beefed-up `app/gateway/main.py` with retry + CB + rate limiter.
- `k8s/notifications.yaml` — Deployment + Service for the new pod.
- Extra env vars on `k8s/gateway.yaml` to tune the patterns without rebuilding.

> 📚 **Reading-vs-lab scope.** Reading 11 covers seven resilience patterns (retry, timeout, circuit breaker, fallback, rate-limit, **bulkhead, load-shedding**). This lab implements the first five. Bulkhead and load-shedding are concept-only — see Reading 11 §6-§7 for when you'd reach for them.

---

## Build & Deploy Workflow (read this once)

After every code change to `app/notifications/` or `app/gateway/`, you need to rebuild + import the image into k3d, then re-apply the manifest:

```bash
# Rebuild affected images
docker build -t quickticket-notifications:v1 ./app/notifications
docker build -t quickticket-gateway:v1 ./app/gateway       # rebuild whenever you edit gateway

# Import into the k3d cluster
k3d image import -c quickticket quickticket-notifications:v1 quickticket-gateway:v1

# Roll the pods so they pick up the new image
kubectl apply -f k8s/notifications.yaml
kubectl argo rollouts set image gateway gateway=quickticket-gateway:v1   # gateway is a Rollout
kubectl argo rollouts status gateway --timeout=240s
```

Skip this and your `kubectl apply` will succeed but the pods will run stale code (or `ErrImageNeverPull` for first-time deploys). Mentioning it once here so the lab text below doesn't repeat it.

---

## Task 1 — Notifications Service + Retries (6 pts)

### 11.1: Write `app/notifications/`

Follow the payments template as your reference (`app/payments/main.py`). Your notifications service needs:

```python
# app/notifications/main.py — YOUR TASK
#
# Requirements:
#   POST /notify
#     body: {"event": "order_confirmed", "order_id": "..."}
#     logs the event; returns {"status": "sent", ...} on 200
#     RESPECTS fault injection: NOTIFY_FAILURE_RATE + NOTIFY_LATENCY_MS env vars
#     (same pattern as payments — see app/payments/main.py)
#
#   GET /health   → {"status": "healthy", "failure_rate": ..., "latency_ms": ...}
#   GET /metrics  → Prometheus exposition (copy the middleware from payments)
#
# Requirements for metrics:
#   notifications_requests_total{method, path, status}   (Counter)
#   notifications_request_duration_seconds{method, path} (Histogram)
#   notifications_notify_total{result}                   (Counter, result=success|failed)
```

Also write `app/notifications/Dockerfile` (copy from `app/payments/`, change the port to 8083) and `app/notifications/requirements.txt` (identical to payments — no DB, no Redis).

### 11.2: Write `k8s/notifications.yaml`

Following the lab-4 pattern, write a Deployment + Service in a single file:

```yaml
# k8s/notifications.yaml — YOUR TASK
#
# Write a Deployment + Service for the notifications pod.
#
# Requirements (Deployment):
#   - 1 replica (we'll scale in lab 12)
#   - image: quickticket-notifications:v1
#   - imagePullPolicy: Never           (locally-imported image)
#   - container port 8083
#   - env vars (with sane defaults — your gateway tunes them via kubectl set env):
#       NOTIFY_FAILURE_RATE = "0.0"
#       NOTIFY_LATENCY_MS   = "0"
#   - selector + labels: app=notifications
#
# Requirements (Service):
#   - ClusterIP (default)
#   - port 8083 → targetPort 8083
#   - selector app=notifications
#
# Hint: copy k8s/payments.yaml and edit the names + port. Lecture 4 slide 7-8.
```

### 11.3: Wire into the gateway (don't block user flow)

In `app/gateway/main.py`, after a successful `pay_reservation`:

- Add `NOTIFICATIONS_URL` to the config section.
- After the payment + confirmation succeed, kick off a **fire-and-forget** notify call:

  ```python
  asyncio.create_task(_notify_order_confirmed(reservation_id))
  ```

  The helper itself logs warnings but **must not** raise, must not add latency to the user path:

  ```python
  async def _notify_order_confirmed(reservation_id: str):
      try:
          await client.post(f"{NOTIFICATIONS_URL}/notify",
                            json={"event": "order_confirmed", "order_id": reservation_id},
                            timeout=2.0)
      except Exception as e:
          log.warning(f"notify failed (non-critical) order={reservation_id} err={e}")
  ```

Why fire-and-forget: you'll answer this fully in your submission (Q7) — but in short, if the user's payment succeeded and their reservation is confirmed, a failed SMS shouldn't make them see a 500.

> 💡 **Gotcha:** Your gateway `/health` handler previously gated "healthy" on `events AND payments`. Don't add notifications to that gate — a broken notifier shouldn't flip the system to degraded from the operator's POV.

### 11.4: Add retry with backoff + jitter

Implement `call_with_retry(func, target, max_retries)` in the gateway and wire it into the `/reserve/{id}/pay` handler's payments call.

```python
# app/gateway/main.py — YOUR TASK
#
# Function signature:
#     async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX): ...
#
# Behaviour:
#   • Loop up to max_retries; each iteration, await func()
#   • On success: if attempt > 0, increment `gateway_retry_total{target, result="succeeded_after_retry"}`. Return.
#   • On exception:
#       - retryable transient errors:  TimeoutException, ConnectError,
#         HTTPStatusError where status is 5xx OR exactly 408/429
#       - non-retryable: any other 4xx (404, 422, …) — increment
#         `gateway_retry_total{result="non_retryable"}` and re-raise immediately.
#   • Final iteration: increment `result="exhausted"`, re-raise the last exception.
#   • Otherwise: compute delay = base_delay * (2 ** attempt) + uniform(0, base_delay).
#     Increment `result="retried"`. Sleep delay. Continue.
#
# Tunables (env vars):
#   RETRY_MAX             default 3
#   RETRY_BASE_DELAY_MS   default 100
#
# Wire-in:
#     pay_resp = await call_with_retry(_charge, target="payments")
#   where _charge() does the actual httpx.AsyncClient.post(...).raise_for_status()
#
# (Task 2 will wrap call_with_retry in a circuit breaker — design call_with_retry
#  to compose cleanly with that.)
```

> 🤔 **Design prompt.** In Task 2 you'll wrap this in a circuit breaker as `cb.call(lambda: call_with_retry(_charge, "payments"))`. Why is `cb.call(retry(...))` correct, and `retry(lambda: cb.call(...))` would be wrong? (Answer this in your submission alongside the implementation.)

### 11.5: Test #1 — fire-and-forget under notify failure

Make sure the Lab 8 mixedload is running (provides checkout traffic):

```bash
kubectl apply -f labs/lab8/mixedload.yaml
kubectl rollout status deployment/mixedload --timeout=30s
```

Inject 30% notification failures + 300ms latency:

```bash
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
kubectl rollout status deployment/notifications --timeout=30s
```

Fire 30 checkout chains from inside the cluster and count user-level outcomes:

```bash
kubectl run checkout-burst --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
ok=0; fail=0
for i in $(seq 1 30); do
  RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
  RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\\([^\"]*\\).*/\\1/p")
  if [ -z "$RID" ]; then echo "[$i] reserve failed"; fail=$((fail+1)); continue; fi
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
  if [ "$CODE" = "200" ]; then ok=$((ok+1)); else echo "[$i] pay failed: $CODE"; fail=$((fail+1)); fi
  sleep 0.1
done
echo "result: ok=$ok fail=$fail"
'
```

Expect `ok=30 fail=0`. Also confirm gateway `/pay` p99 latency is NOT inflated by the injected 300ms:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B2m%5D)))'
```

That proves the fire-and-forget is genuinely non-blocking. Restore notifications when done:

```bash
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.0 NOTIFY_LATENCY_MS=0
```

### 11.6: Test #2 — retries fire under transient payment failure

This is the test that proves your `call_with_retry` works. Inject 30% payment failures (transient — retries should mostly recover):

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
kubectl rollout status deployment/payments --timeout=30s
```

Run another checkout burst:

```bash
kubectl run retry-test --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
ok=0; fail=0
for i in $(seq 1 30); do
  RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
  RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\\([^\"]*\\).*/\\1/p")
  [ -z "$RID" ] && { fail=$((fail+1)); continue; }
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
  [ "$CODE" = "200" ] && ok=$((ok+1)) || fail=$((fail+1))
  sleep 0.1
done
echo "result: ok=$ok fail=$fail"
'
```

With 30% upstream failure × 3 retry attempts, *first-try* fails are 30%, *all-three-fail* is `0.3³ ≈ 2.7%`. Expect `ok ≈ 29-30, fail ≈ 0-1`. Now check that retries actually fired:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(target,result)+(gateway_retry_total)'
```

Expect non-zero values for `result="retried"` and `result="succeeded_after_retry"`. If both are zero, your retry isn't wired in — go back to 11.4. Restore:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
```

### Proof of work

**Paste into `submissions/lab11.md`:**

1. Your `app/notifications/main.py` (the key bits) and `requirements.txt`.
2. Your `k8s/notifications.yaml`.
3. Your `call_with_retry()` implementation.
4. **Test #1** — `ok=30 fail=0` result + `/pay` p99 < 100ms during the notify-failure injection (proves fire-and-forget).
5. **Test #2** — `ok≈30 fail<2` result + `gateway_retry_total{result="retried"}` and `result="succeeded_after_retry"` both non-zero (proves retries actually fire).
6. Real notify failure rate from the notifications pod's `/metrics` (`notifications_notify_total{result}`).
7. Answer: "Why should notifications be non-blocking (fire-and-forget)?"
8. Answer (Design Prompt from 11.4): "Why is `cb.call(retry(...))` the correct composition for Task 2, not `retry(lambda: cb.call(...))`?"

---

## Task 2 — Circuit Breaker + Rate Limiter (4 pts)

> ⏭️ This task is optional.

### 11.7: Circuit breaker for the payments call

```python
# app/gateway/main.py — YOUR TASK
#
# class CircuitBreaker:
#   states: CLOSED → OPEN → HALF_OPEN → CLOSED|OPEN
#   Constructor: threshold (default 5), cooldown_s (default 30), name (for logs+metrics)
#
#   .call(func):
#     if OPEN and cooldown elapsed → transition to HALF_OPEN, proceed
#     if OPEN and cooldown not elapsed → raise CircuitOpenError (fast-fail)
#     try func():
#       on success → failures = 0, state = CLOSED, return
#       on failure → failures += 1; if in HALF_OPEN OR failures >= threshold: OPEN; raise
#
# Metrics:
#   gateway_circuit_breaker_transitions_total{to} — increment on state change
#
# Wire into /pay:
#   pay_resp = await payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
#
# Make threshold (CB_FAILURE_THRESHOLD) and cooldown (CB_COOLDOWN_S) env-configurable.
# Return 503 to the user on CircuitOpenError — NOT 500. It's a different cause.
```

**Test that circuits OPEN under 100% failure:**

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0
kubectl rollout status deployment/payments --timeout=30s

# Run ~80 checkout attempts, count 500s (retry-exhausted) vs 503s (fast-fail = circuit open)
kubectl run cb-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
STATS_500=0; STATS_503=0
for i in $(seq 1 80); do
  RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
  RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\\([^\"]*\\).*/\\1/p")
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

**Test that circuits CLOSE after recovery:**

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
sleep 35      # cooldown is 30s

kubectl run cb-probe2 --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
for i in $(seq 1 15); do
  RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
  RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\\([^\"]*\\).*/\\1/p")
  [ -z "$RID" ] && continue
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
  echo "[$i] $CODE"
done
'
```

Expect: mostly 200s after the cooldown, Prometheus shows `gateway_circuit_breaker_transitions_total{to="CLOSED"}` increments.

> 💡 **Gotcha — real observation:** you have 5 gateway pods, each with its own per-process circuit breaker instance. With only 20 test requests, each pod sees ~4 failures and never hits the threshold of 5. You need at least ~40-80 requests before every pod's circuit opens. Metric counters aggregated across pods will show multiple OPEN transitions (one per pod). This is a legitimate limitation of in-process circuit breakers; production systems use Redis-backed state or a service mesh to aggregate.

### 11.8: Per-endpoint rate limiter

```python
# app/gateway/main.py — YOUR TASK
#
# class RateLimiter:
#   sliding-window, keyed by path, configurable RPS (RATE_LIMIT_RPS env)
#   .allow(key) → bool
#
# Implement as a second @app.middleware("http"):
#   - normalize the path (reuse the _normalize_path helper)
#   - exempt /metrics and /health from rate limiting
#   - if .allow(path) returns False:
#       increment gateway_rate_limit_rejections_total{path}
#       return JSONResponse(429, {"error":"rate_limited","path":...,"limit_rps":...},
#                            headers={"Retry-After": "1"})
#   - else: proceed to call_next(request)
```

**Test under burst:**

```bash
# 100 rapid requests — with 5 pods × RATE_LIMIT_RPS=10, expect ~50 succeed, ~50 429
# (Cluster-wide ceiling = per-pod RPS × replicas, because each pod keeps its own
#  sliding-window counter. There's no shared state across pods. For real DDoS
#  protection you'd put the limiter at the ingress instead.)
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

Verify the 429 response includes a `Retry-After` header (clients use it to back off):

```bash
kubectl run rl-headers --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
# warm up the limiter with rapid hits
for i in $(seq 1 50); do curl -s -o /dev/null http://gateway:8080/events; done
# next request should 429 — capture headers
curl -s -D - -o /dev/null http://gateway:8080/events | grep -iE "^(HTTP|retry-after)"
'
```

Expect `HTTP/1.1 429 Too Many Requests` and `retry-after: 1`. Also confirm the rejection counter is incrementing:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(path)+(gateway_rate_limit_rejections_total)'
```

Sustained load below the limit should see **zero** 429s (`for i in 1..30; do curl … ; sleep 0.2; done`).

### Proof of work

**Paste into `submissions/lab11.md`:**

- Your `CircuitBreaker` and `RateLimiter` class code.
- 500s/503s breakdown from the CB test under 100% payment failure.
- 200s after recovery showing the circuit closed.
- 200/429 split from the rate-limit burst test.
- The `Retry-After: 1` header observed on a 429 response.
- `gateway_circuit_breaker_transitions_total{to}` and `gateway_rate_limit_rejections_total{path}` from Prometheus.

---

## How to Submit

```bash
git switch -c feature/lab11
git add app/notifications/ app/gateway/main.py app/docker-compose.yaml k8s/notifications.yaml k8s/gateway.yaml submissions/lab11.md
git commit -m "feat(lab11): add notifications service and resilience patterns"
git push -u origin feature/lab11
```

PR checklist:

```text
- [x] Task 1 done — notifications service, k8s manifest, fire-and-forget wiring, retry with backoff (Tests #1 + #2)
- [ ] Task 2 done — circuit breaker + rate limiter, tested under failure
```

> 📝 **No "Bonus Task" in this lab.** Lab 11 is itself a bonus lab — Task 1 + Task 2 *are* the challenge. The lab's full 10 pts contribute toward your bonus-labs grade weight (see the course README).

---

## Acceptance Criteria

### Task 1 (6 pts)
- ✅ `app/notifications/` service runs and emits the three Prometheus metrics.
- ✅ `k8s/notifications.yaml` Deployment + Service committed; pod 1/1 Ready.
- ✅ `/pay` calls notifications in fire-and-forget mode (no latency hit, failures invisible).
- ✅ `call_with_retry()` with exponential backoff + jitter, retryable/non-retryable branch, metrics.
- ✅ Test #1 evidence: checkout succeeds 30/30 under `NOTIFY_FAILURE_RATE=0.3`; `/pay` p99 unchanged.
- ✅ Test #2 evidence: checkout still succeeds ~30/30 under `PAYMENT_FAILURE_RATE=0.3` AND `gateway_retry_total{result="retried"}` is non-zero (retries actually fired).
- ✅ Submission answers the design prompt about CB-vs-retry composition.

### Task 2 (4 pts)
- ✅ Circuit breaker class implemented, wired into the `/pay` path.
- ✅ Evidence of OPEN under 100% payment failure (fast-fail 503s).
- ✅ Evidence of CLOSED after cooldown + recovery (200s resume).
- ✅ Rate limiter middleware; burst returns 429s; sustained below-limit load doesn't.

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — Notifications + retries | **6** | Service + manifest written, fire-and-forget wired, retry correctly implemented, both tests passing including Prometheus retry-counter evidence |
| **Task 2** — Circuit breaker + rate limiter | **4** | Both patterns work; Prometheus metrics; real failure-injection evidence |
| **Total** | **10** | Task 1 + Task 2 |

---

## Resources

<details>
<summary>📚 Documentation</summary>

- [Reading 11](../lectures/reading11.md) — the patterns you're implementing, with history and tradeoffs.
- [httpx retries](https://www.python-httpx.org/advanced/#retries) — the library's built-in `Retry` transport (not used here because we want observability per-target).
- [Martin Fowler — Circuit Breaker](https://martinfowler.com/bliki/CircuitBreaker.html)
- [AWS — Exponential Backoff and Jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/)
- [Stripe — Rate Limiters](https://stripe.com/blog/rate-limiters)

</details>

<details>
<summary>⚠️ Common Pitfalls</summary>

- **Retrying 4xx.** A 404 or 422 won't fix itself; retrying it is a bug + wasted load. Only retry 5xx + 408/429 + network errors.
- **Missing jitter.** Without `random.uniform(0, base_delay)`, all retrying clients sync on the same intervals and hammer the recovering service simultaneously — the classic "thundering herd".
- **Fire-and-forget via `asyncio.create_task`** works but needs the event loop. In a synchronous Flask/Django handler, use a queue instead.
- **Circuit breaker is per-process.** With 5 gateway replicas you need ~5× the failures to trip every pod's circuit. Plan test volume accordingly.
- **Rate limiter is per-process.** Cluster-wide limit = RPS × replicas. For real DDoS protection put a shared limiter upstream (ingress / WAF / Envoy).
- **`/health` should NOT gate on notifications.** The whole point of fire-and-forget is that notifications being down doesn't mean the system is down. Gate only on events + payments.
- **Retries interact with the circuit breaker.** A single "failure" the CB sees is N internal retries; 5 external failures = 15 downstream calls. That's not wrong but easy to mis-reason about — note this in your submission.

</details>
