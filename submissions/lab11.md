# QuickTicket Advanced Microservice Patterns

## Goal

Add a notifications microservice and implement resilience patterns inside the
gateway: retries with exponential backoff and jitter, a circuit breaker, a
sliding-window rate limiter, and bulkhead isolation. Validate each pattern with
fault injection in the k3d cluster.

## Changes

- Added `app/notifications/` with `POST /notify`, `/health`, `/metrics`, and configurable failure rate and latency.
- Added `k8s/notifications.yaml` with a Deployment and ClusterIP Service on port `8083`.
- Added `NOTIFICATIONS_URL=http://notifications:8083` to the gateway manifest.
- Implemented `call_with_retry()` for transient HTTP and network failures with exponential backoff, jitter, and Prometheus counters.
- Implemented the payments circuit breaker with `CLOSED`, `OPEN`, and `HALF_OPEN` states.
- Implemented the per-endpoint one-second sliding-window rate limiter.
- Implemented payments bulkhead isolation using an `asyncio.Semaphore` with a maximum of 10 concurrent calls per gateway pod.

## Testing

All tests ran inside Kubernetes through the `gateway` Service. The gateway was deployed as five replicas and locally built images were imported into k3d.

### Task 1: Notifications and retry

With `NOTIFY_FAILURE_RATE=0.3` and `NOTIFY_LATENCY_MS=300`, 30 checkout chains completed successfully:

```text
result: ok=30 fail=0
notifications_notify_total{result="success"} 22.0
notifications_notify_total{result="failed"} 8.0
```

The notification call is scheduled with `asyncio.create_task`, so it is not on the user request path. Gateway p99 for `/reserve/{id}/pay` was `0.02485s` (approximately 25ms) while notifications added 300ms latency.

With `PAYMENT_FAILURE_RATE=0.3`, all 30 checkout chains also succeeded:

```text
result: ok=30 fail=0
gateway_retry_total{target="payments",result="retried"} 10
gateway_retry_total{target="payments",result="succeeded_after_retry"} 9
```

Retry is placed inside the circuit breaker:

```text
payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
```

This lets the retry loop recover one transient request while the circuit breaker observes the final outcome. Once the breaker opens, retries are skipped and requests fail immediately.

### Task 2: Circuit breaker and rate limiter

With `PAYMENT_FAILURE_RATE=1.0`, 80 checkout attempts produced:

```text
500s=25 503s=55 other=0
gateway_circuit_breaker_transitions_total{to="OPEN"} 5
```

The five `OPEN` transitions correspond to the five gateway processes. After restoring payments and waiting for the 30-second cooldown, all 15 recovery requests returned `200`.

The rate limiter was configured at 10 requests per second per gateway pod. A 100-request burst produced:

```text
200=50 429=50 other=0
gateway_rate_limit_rejections_total{path="/events"} 50
```

The rejected response included:

```text
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

A sustained request pattern below the limit produced:

```text
200=30 429=0
```

### Bonus: Bulkhead isolation

Payments was configured with `PAYMENT_LATENCY_MS=3000` and 80 concurrent pay requests were sent. The observed result was 50 downstream attempts and 30 fast rejections:

```text
gateway_bulkhead_rejections_total{target="payments"} 30
gateway_bulkhead_in_flight{target="payments"} 10
```

The `10` in-flight value was captured during the load. After the load finished, the Gauge correctly returned to zero. During the same test, the maximum latency of independent `GET /events` requests was:

```text
events_max_seconds=0.031995
```

This shows that slow payments were isolated from the events path. The bulkhead limits concurrent dependency calls; unlike the rate limiter, it protects the gateway from downstream slowness rather than limiting incoming request rate.

## Reliability Notes

- Notifications are best-effort because a failed notification must not roll back or delay a successful payment.
- Retry applies only to transient failures (`5xx`, `408`, `429`, timeouts, and connection errors). Client errors such as `404` and `422` are not retried.
- The circuit breaker and rate limiter are process-local. With five replicas, the effective rate-limit ceiling is approximately five times the per-pod value, and each pod has its own circuit state.
- In production, shared rate limiting and circuit state would belong at an ingress, service mesh, or shared state store.

## Artifacts

- `app/notifications/main.py`
- `app/notifications/Dockerfile`
- `app/notifications/requirements.txt`
- `app/gateway/main.py`
- `k8s/notifications.yaml`
- `k8s/gateway.yaml`

## Checklist

- [x] Task 1 done - notifications service, fire-and-forget wiring, and retries
- [x] Task 2 done - circuit breaker and rate limiter with failure tests
- [x] Bonus Task done - bulkhead isolation and concurrent-load proof
- [x] Title is clear (`feat(labN): <topic>` style)
- [x] No secrets or large temporary files committed
- [x] Submission file at `submissions/lab11.md` exists
