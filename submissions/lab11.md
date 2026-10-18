# Lab 11 — Resilience Patterns

## Overview

This lab extends the QuickTicket application with resilience patterns for service-to-service communication. The implementation adds a Notifications service and introduces retries, a Circuit Breaker, a Rate Limiter, and Bulkhead Isolation in the Gateway.

## Implementation

### 1. Notifications service

Added `app/notifications/` with:
- `main.py` — FastAPI service exposing health, metrics, and notification endpoints.
- `Dockerfile` and `requirements.txt` — container build and runtime dependencies.
- `k8s/notifications.yaml` — Kubernetes Deployment and ClusterIP Service.

The Gateway is configured to call the Notifications service using `NOTIFICATIONS_URL=http://notifications:8083`.

### 2. Retries

The Gateway retries transient failures when communicating with the Payments service. Retry behavior is configurable through environment variables, including `RETRY_MAX` and `RETRY_BASE_DELAY_MS`. Retry attempts use exponential backoff. Metrics are exposed through `gateway_retry_total`, with target and result labels.

### 3. Circuit Breaker

A Circuit Breaker protects the Payments dependency. It tracks failures and transitions between `CLOSED`, `OPEN`, and `HALF_OPEN`. When the circuit is open, calls are rejected temporarily; after the cooldown, a probe can determine whether the dependency has recovered. Transitions are exposed through `gateway_circuit_breaker_transitions_total`.

### 4. Rate Limiter

The Gateway applies a configurable request-rate limit using `RATE_LIMIT_RPS`. Requests above the limit are rejected with HTTP `429`. The final manifest sets `RATE_LIMIT_RPS` to `10`.

### 5. Bulkhead Isolation (bonus)

The Payments call is protected by a semaphore-based Bulkhead. The final manifest sets `BULKHEAD_PAYMENTS_MAX=10` and `BULKHEAD_PAYMENTS_TIMEOUT_S=0.5`. When all slots are occupied and a request cannot obtain a slot before the timeout, the request is rejected.

## Validation and test results

The following results were observed during testing in the local Kubernetes cluster:

- `GET /health` returned HTTP `200 OK`; the Gateway reported `events`, `payments`, and `notifications` as healthy, with `circuit_payments=CLOSED`.
- Notification failure injection was enabled (`NOTIFY_FAILURE_RATE=0.3`, `NOTIFY_LATENCY_MS=300`). A successful payment remained successful even though the Notifications service returned HTTP `500`. Metrics recorded `notifications_requests_total{method="POST",path="/notify",status="500"} 1.0` and `notifications_notify_total{result="failed"} 1.0`.
- Under Payments failure injection (`PAYMENT_FAILURE_RATE=0.3`), retry metrics recorded retried requests and successes after retry. During this test, rate limiting also returned HTTP `429` and `gateway_rate_limit_rejections_total` increased.
- With Payments failure injection set to `1.0`, repeated failures caused the Circuit Breaker to open. Metrics recorded transitions to `OPEN`, `HALF_OPEN`, and `CLOSED`. After resetting the failure rate to `0.0` and waiting through the cooldown, a payment succeeded and the circuit returned to `CLOSED`.
- For Bulkhead validation, 12 reservation payment requests were sent concurrently while the Payments service had `PAYMENT_LATENCY_MS=3000`. The observed responses were 10 HTTP `200` and 2 HTTP `503`. The metric `gateway_bulkhead_rejections_total{target="payments"}` reached `2.0`, confirming two requests were rejected by the Bulkhead. After the test, `gateway_bulkhead_in_flight{target="payments"}` returned to `0.0`.
- The Payments deployment was restored to `PAYMENT_FAILURE_RATE=0.0` and `PAYMENT_LATENCY_MS=0`.
- The Gateway Argo Rollout completed successfully: status `Healthy`, Canary step `10/10`, traffic weight `100%`, and all 5 of 5 replicas ready. The associated AnalysisRun was successful.

## Final configuration

The Gateway manifest `k8s/gateway.yaml` contains the following final values:

| Variable | Value |
|---|---:|
| `RATE_LIMIT_RPS` | `10` |
| `BULKHEAD_PAYMENTS_MAX` | `10` |
| `BULKHEAD_PAYMENTS_TIMEOUT_S` | `0.5` |
| `NOTIFICATIONS_URL` | `http://notifications:8083` |

The Payments service was returned to normal test settings (`PAYMENT_FAILURE_RATE=0.0`, `PAYMENT_LATENCY_MS=0`). The Notifications failure-injection settings were used only for testing.

## Conclusion

The Notifications integration and the Gateway resilience mechanisms were implemented and exercised in Kubernetes. The observed metrics and HTTP responses demonstrate notification-failure isolation, retry behavior, Circuit Breaker transitions, Rate Limiter rejections, and Bulkhead rejections. The Gateway rollout completed successfully with all replicas ready.

## Submission checklist

- [ ] Review this report and ensure it matches the final code and manifests.
- [ ] Commit only Lab 11 files on branch `feature/lab11`; do not include unrelated Lab 1, Lab 6, or Lab 10 changes.
- [ ] Open a Pull Request and submit its URL through Moodle.
