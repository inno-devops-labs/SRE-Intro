# Lab 11 — Advanced Microservice Patterns

In this lab I added a fourth QuickTicket service and made the payment path more resilient. Besides the required notification service and retries, I also completed Task 2 with a circuit breaker and rate limiter, as well as the bonus bulkhead task.

## Task 1 — Notifications Service and Retries

### Notifications service

I created the new service in `app/notifications/`, using the payments service as a reference. It accepts an order event through `POST /notify`, logs it and returns a successful delivery response. For testing failures, the service reads `NOTIFY_FAILURE_RATE` and `NOTIFY_LATENCY_MS` from the environment.

The service also exposes:

- `GET /health`, which shows the current failure rate and injected latency;
- `GET /metrics`, which returns Prometheus metrics;
- request counters and duration histograms;
- `notifications_notify_total{result="success|failed"}` for delivery outcomes.

I added a Dockerfile for port 8083 and the same lightweight dependency set used by payments. I also included notifications in `app/docker-compose.yaml`.

### Kubernetes and gateway integration

The manifest in `k8s/notifications.yaml` contains a one-replica Deployment and a ClusterIP Service. It uses the local `quickticket-notifications:v1` image with `imagePullPolicy: Never`, exposes port 8083 and has safe fault-injection defaults.

In `k8s/gateway.yaml` I configured:

```yaml
- name: NOTIFICATIONS_URL
  value: "http://notifications:8083"
```

After a successful payment and reservation confirmation, the gateway starts notification delivery with `asyncio.create_task()`. This means the user does not have to wait for a non-critical notification request. I kept notifications out of the critical health verdict as required.

### Retry implementation

I replaced the `call_with_retry()` stub with exponential backoff and jitter. Timeouts, connection errors, HTTP 5xx, 408 and 429 are treated as temporary failures. Other HTTP 4xx responses fail immediately because retrying them would not normally change the outcome.

The implementation records whether a request was retried, recovered after a retry, exhausted all attempts or failed with a non-retryable error.

### Test 1 — notification failure does not break checkout

I injected 30% notification failures and 300ms latency:

```text
NOTIFY_FAILURE_RATE=0.3
NOTIFY_LATENCY_MS=300
```

All 30 checkout attempts still succeeded:

```text
result: ok=30 fail=0
```

At the same time, Prometheus reported the following `/pay` p99:

```text
path="/reserve/{id}/pay"  0.024841191230095677 seconds
```

The p99 was about 24.8ms, well below both the required 100ms and the injected 300ms notification delay. This confirmed that notification delivery was genuinely outside the user-facing response path.

When I scraped the notification service, 26 background tasks had finished:

```text
notifications_notify_total{result="success"} 19.0
notifications_notify_total{result="failed"} 7.0
```

That is an observed failure rate of 26.9%, which is reasonable for a small random sample configured for 30% failures.

Notifications should be non-blocking because sending them is not required to finish a paid reservation. If checkout waited for the notifier, a slow or unavailable secondary service would create unnecessary latency and user-visible failures. In a production system I would likely combine this approach with a durable queue or transactional outbox, so delivery could be retried without losing the event.

### Test 2 — retries recover transient payment failures

Next, I set `PAYMENT_FAILURE_RATE=0.3` and ran another 30 checkouts:

```text
result: ok=30 fail=0
```

Prometheus confirmed that retries really happened rather than the test simply getting lucky:

```text
gateway_retry_total{target="payments",result="retried"} 14
gateway_retry_total{target="payments",result="succeeded_after_retry"} 10
```

The composition `cb.call(retry(...))` is important here. The circuit breaker should see the final result of one logical payment operation after its bounded retry attempts. If the order were reversed, every individual attempt would count against the breaker, it could open too quickly, and later retry attempts could run into `CircuitOpenError`, undermining the intended fast-fail behavior.

## Task 2 — Circuit Breaker and Rate Limiter

### Circuit breaker

I implemented the CLOSED, OPEN and HALF_OPEN states in `CircuitBreaker.call()`. Once the failure threshold is reached, the breaker opens and rejects new payment calls immediately. After the cooldown it allows a probe call; a successful probe closes the circuit, while another failure opens it again.

With `PAYMENT_FAILURE_RATE=1.0`, 80 checkout attempts produced this split:

```text
500s=35 503s=45 other=0
```

The 500 responses were payment calls that exhausted their retries before the local breaker opened. The 503 responses were fast failures from already-open circuit breakers. Since the gateway has five replicas, each replica owns its own breaker and needs to reach the threshold independently.

I then restored payments, waited longer than the 30-second cooldown and tried 15 more checkouts:

```text
200s=15 other=0
```

This showed that the HALF_OPEN probes succeeded and normal traffic resumed.

### Rate limiter

The rate limiter uses a one-second sliding window for each normalized endpoint. Old timestamps are removed from the deque, and requests beyond `RATE_LIMIT_RPS` are rejected with HTTP 429.

During a 100-request burst I observed:

```text
200=69 429=31 other=0
```

The rejected response included the expected backoff hint:

```text
HTTP/1.1 429 Too Many Requests
retry-after: 1
```

I also sent 30 requests at a slower, sustained rate. None of them were incorrectly limited:

```text
sustained: ok=30 429=0
```

This was the behavior I wanted: short bursts above the per-pod limit are rejected, while ordinary traffic continues normally.

## Bonus Task — Bulkhead Isolation

For the bonus task I added a payment bulkhead based on `asyncio.Semaphore`. It limits the number of concurrent payment workflows per gateway process and waits only 0.5 seconds for a free slot. If no slot becomes available, the gateway increments the rejection counter and returns HTTP 503. The in-flight gauge is always decremented and the semaphore released in a `finally` block.

The final composition is:

```text
bulkhead → circuit breaker → retry → payment call
```

The bulkhead stays outside the retry operation so a complete logical payment, including all retries, occupies exactly one slot. If it were placed inside, every retry could acquire a new slot and the concurrency bound would no longer describe complete payment workflows. An open circuit still holds the outer slot only for the very short fast-fail operation.

### Isolation experiment

I injected three seconds of payment latency. Because there are five gateway replicas with ten slots each, the 30-call example from the lab is not enough to guarantee saturation: the approximate cluster-wide capacity is 50 concurrent calls. I therefore used 100 prepared reservations for the pressure test.

```text
reservations=100
EVENTS: ok=29 slow=1
     35 200
     17 500
     32 503
     16 504
```

Despite the deliberately heavy payment load, 29 of the 30 simultaneous `/events` samples remained below 500ms. Prometheus showed that the bulkhead was genuinely responsible for shedding work and that its configured limit was reached:

```text
gateway_bulkhead_rejections_total{target="payments"} 32
max(max_over_time(gateway_bulkhead_in_flight{target="payments"}[5m])) 10
```

The 32 rejections show that calls were refused when no slot became available, and the observed maximum of 10 exactly matches `BULKHEAD_PAYMENTS_MAX`.

Although a rate limiter and a bulkhead can both reject traffic, they solve different problems. The rate limiter controls how many requests arrive during a time window. The bulkhead controls how much downstream work may be in progress at the same time. A low request rate can still exhaust a dependency if every call is very slow, which is the situation the bulkhead protects against.

## Final verification

I added focused automated tests for the notification API and metrics, transient and non-retryable retry behavior, circuit-breaker recovery, sliding-window expiry and bulkhead saturation.

```text
$ .venv/bin/pytest -q tests/test_lab11.py
.....                                                                    [100%]
5 passed, 1 warning in 0.32s
```

Both Docker images built successfully, the Compose configuration validated, and the images were imported into k3d. During verification, notifications reached `1/1 Running` and the gateway Rollout reached `Healthy`.

After the experiments I restored every injected fault and temporary setting:

```text
NOTIFY_FAILURE_RATE=0.0
NOTIFY_LATENCY_MS=0
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
RATE_LIMIT_RPS=10
BULKHEAD_PAYMENTS_MAX=10
```

The committed Rollout still contains the intentional manual canary pause introduced in Lab 7. All Lab 11 application changes, manifests, metrics, tests, experiments and design questions are complete.
