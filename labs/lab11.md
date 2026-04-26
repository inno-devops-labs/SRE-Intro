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

This is a bonus lab, so the scaffolding is lighter than earlier weeks — you're expected to write most of the code yourself. Requirements, hints, and a Python skeleton are provided; the actual implementations are yours.

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

### 11.2: Wire into the gateway (don't block user flow)

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

Why fire-and-forget: see answer in 11.4 below — but in short, if the user's payment succeeded and their reservation is confirmed, a failed SMS shouldn't make them see a 500.

> 💡 **Gotcha:** Your gateway `/health` handler previously gated "healthy" on `events AND payments`. Don't add notifications to that gate — a broken notifier shouldn't flip the system to degraded from the operator's POV.

### 11.3: Add retry with backoff + jitter

Implement `call_with_retry(func, target, max_retries)` in the gateway:

```python
# app/gateway/main.py — YOUR TASK
#
# Requirements:
#   - Exponential backoff: base_delay * 2^attempt
#   - Add jitter: random.uniform(0, base_delay) to avoid thundering herd
#   - Retry ONLY on transient errors (httpx.TimeoutException, ConnectError,
#     5xx HTTPStatusError, plus 408/429 which are retryable 4xx)
#   - Do NOT retry other 4xx — they won't fix themselves
#   - Emit Prometheus metrics for observability:
#       gateway_retry_total{target, result}  — result ∈ {retried, succeeded_after_retry, exhausted, non_retryable}
#   - Make attempts (RETRY_MAX) and base delay (RETRY_BASE_DELAY_MS) env-configurable
#
# Wire it into the /reserve/{id}/pay handler's payments call.
```

Hint structure (you can copy and flesh out):

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    base = RETRY_BASE_DELAY_MS / 1000
    last_exc = None
    for attempt in range(max_retries):
        try:
            # TODO: call func(), return result (bump success-after-retry metric if attempt > 0)
            ...
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as exc:
            # TODO:
            #   - save last_exc
            #   - if it's a 4xx (not 408/429): bump non_retryable, raise
            #   - if this is the final attempt: bump exhausted, raise
            #   - compute delay = base * 2^attempt + jitter
            #   - bump retried, await asyncio.sleep(delay)
            ...
    raise last_exc  # unreachable
```

### 11.4: Test under failure

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

Fire off a batch of checkout chains from inside the cluster and count how many user-level checkouts succeed:

```bash
kubectl run checkout-burst --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
ok=0; fail=0
for i in $(seq 1 30); do
  RES=$(curl -s -X POST http://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}")
  RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\\([^\"]*\\).*/\\1/p")
  [ -z "$RID" ] && { fail=$((fail+1)); continue; }
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://gateway:8080/reserve/$RID/pay)
  [ "$CODE" = "200" ] && ok=$((ok+1)) || fail=$((fail+1))
  sleep 0.1
done
echo "ok=$ok fail=$fail"
'
```

Also check gateway `/pay` p99 latency should **not** be inflated by the injected 300ms (fire-and-forget works):

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B2m%5D)))'
```

### Proof of work

**Paste into `submissions/lab11.md`:**

1. Your `app/notifications/main.py` (the key bits) and `requirements.txt`.
2. Your `call_with_retry()` implementation.
3. The `ok=N fail=0` result from the checkout-burst under `NOTIFY_FAILURE_RATE=0.3`.
4. Gateway `/pay` p99 latency during the test (should NOT include the 300ms notify delay).
5. Real failure rate seen on the notifications side (`notifications_notify_total{result}` from `/metrics` on the notifications pod).
6. Answer: "Why should notifications be non-blocking (fire-and-forget)? What would happen if the gateway waited for a notification response before returning to the user?"

---

## Task 2 — Circuit Breaker + Rate Limiter (4 pts)

> ⏭️ This task is optional.

### 11.5: Circuit breaker for the payments call

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

### 11.6: Per-endpoint rate limiter

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

Also sustained load below the limit should see zero 429s (`for i in 1..30; do curl … ; sleep 0.2; done`).

### Proof of work

**Paste into `submissions/lab11.md`:**

- Your `CircuitBreaker` and `RateLimiter` class code.
- 500s/503s breakdown from the CB test under 100% payment failure.
- 200s after recovery showing the circuit closed.
- 200/429 split from the rate-limit burst test.
- `gateway_circuit_breaker_transitions_total` and `gateway_rate_limit_rejections_total` from Prometheus.

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
- [x] Task 1 done — notifications service, fire-and-forget wiring, retry with backoff
- [ ] Task 2 done — circuit breaker + rate limiter, tested under failure
```

> 📝 **No "Bonus Task" in this lab.** Lab 11 is itself a bonus lab — Task 1 + Task 2 *are* the challenge. The lab's full 10 pts contribute toward your bonus-labs grade weight (see the course README).

---

## Acceptance Criteria

### Task 1 (6 pts)
- ✅ `app/notifications/` service runs and emits Prometheus metrics.
- ✅ `/pay` calls notifications in fire-and-forget mode (no latency hit, failures invisible).
- ✅ `call_with_retry()` with exponential backoff + jitter, retryable/non-retryable branch, metrics.
- ✅ Test evidence: checkout succeeds at 100% under NOTIFY_FAILURE_RATE=0.3.

### Task 2 (4 pts)
- ✅ Circuit breaker class implemented, wired into the `/pay` path.
- ✅ Evidence of OPEN under 100% payment failure (fast-fail 503s).
- ✅ Evidence of CLOSED after cooldown + recovery (200s resume).
- ✅ Rate limiter middleware; burst returns 429s; sustained below-limit load doesn't.

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — Notifications + retries | **6** | Service written, fire-and-forget wired, retry correctly implemented and tested |
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
