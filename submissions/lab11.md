# Lab 11 — Advanced Microservice Patterns

## Task 1 — Notifications and retries

### Notifications service

Added the Notifications FastAPI service with `/health`, `/notify`, and Prometheus metrics. Gateway sends notifications in a fire-and-forget task, so notification failures do not fail checkout.

With `NOTIFY_FAILURE_RATE=0.3` and `NOTIFY_LATENCY_MS=300`, all 30 checkout attempts succeeded. Notifications recorded 18 successes and 12 injected failures. Observed Gateway `/pay` p99 was 24.85 ms.

Evidence: [checkout test](lab11-evidence/notifications-checkout-test.txt), [summary](lab11-evidence/notifications-test-summary.txt), [p99 query](lab11-evidence/notifications-pay-p99.json).

### Payment retries

Implemented exponential backoff with jitter. Timeouts, connection errors, HTTP 5xx, 408, and 429 are retried; other 4xx errors are not.

With `PAYMENT_FAILURE_RATE=0.3`, 28 of 30 checkout attempts succeeded. Two returned HTTP 500 after retries were exhausted. Prometheus recorded 14 retry attempts, 7 successes after retry, and 2 exhausted requests.

Evidence: [retry test](lab11-evidence/payments-retry-test.txt), [retry metrics](lab11-evidence/payments-retry-metrics.json), [summary](lab11-evidence/payments-retry-summary.txt).

The circuit breaker wraps retries so it observes the final outcome of a checkout. If retry wrapped the circuit breaker, it could retry an open-circuit fast failure and defeat fast-fail behavior.

## Task 2 — Circuit breaker and rate limiter

### Circuit breaker

Implemented CLOSED, OPEN, and HALF_OPEN states. With payment failures set to 100%, 80 payment requests returned 24 HTTP 500 responses followed by 56 fast-fail HTTP 503 responses. After cooldown and Payments recovery, all 30 checkout attempts succeeded. Prometheus recorded five transitions to each of OPEN, HALF_OPEN, and CLOSED.

Evidence: [open-circuit test](lab11-evidence/circuit-breaker-open-test.txt), [recovery test](lab11-evidence/circuit-breaker-recovery-test.txt), [transitions](lab11-evidence/circuit-breaker-transitions.json).

### Rate limiter

Implemented a per-endpoint sliding one-second window. A burst of 100 `/events` requests produced 40 HTTP 429 responses, each with `Retry-After: 1`. The burst also produced 23 HTTP 502 responses. At about 3.3 requests per second, all 10 requests returned HTTP 200. Prometheus recorded 40 rate-limit rejections for `/events`.

Evidence: [burst test](lab11-evidence/rate-limiter-burst.txt), [steady-load test](lab11-evidence/rate-limiter-steady-test.txt), [rejection metric](lab11-evidence/rate-limiter-metrics.json).

## Bonus — Bulkhead isolation

Added a Payments bulkhead using an `asyncio.Semaphore`: at most 10 concurrent calls, with a 0.5-second slot-acquisition timeout. Timed-out requests return HTTP 503. The bulkhead wraps the circuit breaker and retries, so one checkout holds one slot for its full retry sequence.

With Payments latency set to 3 seconds, 30 concurrent payment attempts sent to one Gateway pod produced 10 HTTP 200 and 20 HTTP 503 responses. All 30 concurrent `/events` probes completed within 500 ms; measured p99 was 209.1 ms. Prometheus recorded 20 bulkhead rejections and a maximum in-flight value of 10.

Without the bulkhead wrapper, 22 payments returned HTTP 200 and 8 returned HTTP 500. For `/events`, 28 probes were below 500 ms and 2 were slower; measured p99 was 997.4 ms.

```python
async def call(self, func):
    try:
        await asyncio.wait_for(
            self.semaphore.acquire(),
            timeout=self.acquire_timeout_s,
        )
    except asyncio.TimeoutError as exc:
        BULKHEAD_REJECTIONS.labels(self.name).inc()
        raise BulkheadFullError(f"bulkhead[{self.name}] is full") from exc

    BULKHEAD_IN_FLIGHT.labels(self.name).inc()
    try:
        return await func()
    finally:
        BULKHEAD_IN_FLIGHT.labels(self.name).dec()
        self.semaphore.release()

pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(
        lambda: call_with_retry(_charge, target="payments")
    )
)
```

The bulkhead wraps the circuit breaker so one payment flow, including retries, holds one concurrency slot. A rate limiter controls request arrivals per time window; a bulkhead controls concurrent calls to a dependency.
Evidence: [with bulkhead](lab11-evidence/bulkhead-with-isolation.txt), [without bulkhead](lab11-evidence/bulkhead-without-isolation.txt), [rejections](lab11-evidence/bulkhead-rejections.json), [maximum in-flight](lab11-evidence/bulkhead-max-in-flight.json).


## Recovery state

Reset payment and notification fault injection and temporary Payments latency. Restored the Gateway rate limit to 10 RPS. The Gateway Rollout returned to Healthy.
