# Lab 11 — Bonus: Advanced Microservice Patterns

**Author:** Anton Bugaev  
**Date:** 2026-07-04  
**Cluster:** k3d `quickticket` (gateway Rollout ×5, in-cluster Prometheus, Lab 11 notifications service)

---

## Files Used
- `app/gateway/main.py` — retry, circuit breaker, and rate limiter implementations.
- `app/notifications/main.py` — new notifications service.
- `app/notifications/Dockerfile` — local image build for the new service.
- `app/notifications/requirements.txt` — FastAPI + Prometheus dependencies.
- `k8s/notifications.yaml` — raw Deployment + Service for notifications.
- `k8s/chart/templates/gateway.yaml` — gateway env wiring for Lab 11.
- `k8s/chart/templates/notifications.yaml` — chart template for notifications.
- `k8s/chart/values.yaml` — Lab 11 tunables for the chart.
- `app/gateway/main.py` also includes the bonus bulkhead isolation layer.

---

## What Was Checked
- Notifications fire-and-forget under injected notify latency/failures.
- Retry behavior under transient payment failures.
- Circuit-breaker fast-fail under persistent payment failures.
- Rate-limiter behavior under burst traffic.

---

## Task 1 — Notifications Service + Retries

### Implemented

#### `app/notifications/main.py`
- `POST /notify` logs the event, respects `NOTIFY_FAILURE_RATE` and `NOTIFY_LATENCY_MS`, and returns `{"status": "sent", ...}`.
- `GET /health` returns the configured fault-injection values.
- `GET /metrics` exposes Prometheus metrics.
- Metrics:
  - `notifications_requests_total{method, path, status}`
  - `notifications_request_duration_seconds{method, path}`
  - `notifications_notify_total{result}`

#### `app/gateway/main.py`
- `call_with_retry()` now retries transient failures with exponential backoff + jitter.
- `CircuitBreaker.call()` now implements CLOSED / OPEN / HALF_OPEN transitions.
- `RateLimiter.allow()` now implements a 1-second sliding window.
- `NOTIFICATIONS_URL` is wired into `/pay` as fire-and-forget.

### Test #1 — fire-and-forget under notify failure

Injected notifications faults:

```bash
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
```

Burst result:

```text
result: ok=20 fail=0
```

Request timing stayed flat with and without notification latency:

```text
no-notify:    ~0.005-0.008 s per /reserve/{id}/pay
notify-300ms: ~0.005-0.007 s per /reserve/{id}/pay
```

That shows the notify path is non-blocking for the user request.

Notifications metrics from the service:

```text
notifications_notify_total{result="failed"} 6
notifications_notify_total{result="success"} 4
```

### Test #2 — retries fire under transient payment failure

Injected payment faults:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
```

Burst result:

```text
result: ok=29 fail=1
```

Retry metrics:

```text
gateway_retry_total{target="payments",result="retried"} 55
gateway_retry_total{target="payments",result="succeeded_after_retry"} 5
gateway_retry_total{target="payments",result="exhausted"} 25
```

That proves retries were actually exercised and sometimes recovered a transient failure.

### Design answer — why `cb.call(retry(...))` and not `retry(cb.call(...))`?

`cb.call(retry(...))` is correct because the circuit breaker should observe the final outcome of one logical payment attempt, while the retry loop handles transient upstream errors inside that boundary. If the order is reversed, a tripped circuit breaker would be retried again and again, which defeats fast-fail behavior and makes the breaker much less effective.

---

## Task 2 — Circuit Breaker + Rate Limiter

### Circuit breaker under 100% payment failure

Injected payment faults:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0
```

Circuit-breaker burst result:

```text
500s=26 503s=54
```

Circuit-breaker metrics:

```text
gateway_circuit_breaker_transitions_total{to="OPEN"} 5
```

That shows the breaker opened and fast-failed once the threshold was reached.

### Circuit close after recovery

After returning payments to healthy behavior and waiting for cooldown, the circuit closed again and requests recovered normally.

### Rate limiter under burst

Restored the intended rate limit:

```bash
kubectl patch rollout gateway ... RATE_LIMIT_RPS=10
```

Burst result:

```text
200=93 429=7
```

The 429 response uses `Retry-After: 1`, and the rejection counter increments:

```text
gateway_rate_limit_rejections_total{path="/events"} 7
```

That confirms the per-endpoint sliding-window limiter is active.

---

## Bonus / Notes
- The bonus bulkhead task is implemented as a bounded concurrency layer around payments.
- The new notifications service is deployed as `k8s/notifications.yaml` and is reachable in-cluster at `http://notifications:8083`.
- The gateway still behaves normally for labs 1-10 when `NOTIFICATIONS_URL` is unset.

---

## Verification Checklist
- [x] Notifications service added and wired into gateway
- [x] Retry with backoff + jitter implemented and validated
- [x] Circuit breaker implemented and validated
- [x] Rate limiter implemented and validated
- [x] Prometheus metrics captured for retry / CB / rate limiting
- [x] Answered the composition-order design question
- [x] Bonus bulkhead task completed
