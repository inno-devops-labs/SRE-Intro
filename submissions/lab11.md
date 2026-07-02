# Lab 11 — Advanced Microservice Patterns

## Task 1 — Notifications Service + Retries

I created a notifications service (based on payments service with a `/notify` endpoint).

I also added a `call_with_retry` function with exponential backoff and jitter.

### Test 1 — Fire-and-forget notifications

- Set `NOTIFY_FAILURE_RATE=0.4`
- Ran 30 checkout requests
- All 30 requests completed successfully (ok=30, fail=0)
- `/pay` latency stayed low because notifications were not blocking the request

### Test 2 — Retries

- Set `PAYMENT_FAILURE_RATE=0.3`
- Most payments succeeded after retries
- Metric `gateway_retry_total{result="retried"}` increased

## Task 2 — Circuit Breaker + Rate Limiter

### Circuit Breaker

Implemented a simple state machine:

- CLOSED
- OPEN
- HALF_OPEN

Results:

- When payments failed 100% of the time, the circuit opened
- Gateway returned fast 503 responses instead of waiting
- After recovery and 30 second cooldown, the circuit closed again

### Rate Limiter

Implemented a sliding window rate limiter with a limit of 10 RPS.

Results:

- Sent a burst of 30 requests
- Many requests returned `429 Too Many Requests`
- `Retry-After: 1` header was present

## Bonus Task — Bulkhead Isolation

Implemented a bulkhead for payments by limiting concurrent requests.

### Test

- Set `PAYMENT_LATENCY_MS=3000`

Without bulkhead:

- `/events` also became slow
- Event loop resources were affected

With bulkhead:

- `/events` stayed fast
- `/pay` returned 503 when limit was reached

### Observation

Bulkhead helped protect other parts of the system from a slow dependency.

## Conclusion

In this lab I implemented four important resilience patterns:

- Retry with backoff and jitter
- Circuit Breaker
- Rate Limiter
- Bulkhead

These changes made the system more reliable when downstream services fail or become slow.

