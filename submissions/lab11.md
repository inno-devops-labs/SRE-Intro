# Lab 11 — Advanced Microservice Patterns

Author: Walkerino. Completed Task 1, Task 2 and the bulkhead bonus.

## Environment and reproduction

Fresh k3d `quickticket-lab1112`: one server and two agents, Kubernetes v1.35.5+k3s1, Argo Rollouts v1.9.0, in-cluster Prometheus. The previously configured cluster was unavailable, so a separate cluster was created. Prerequisite manifests from my earlier labs are included because upstream intentionally does not ship student infrastructure. Gateway: five replicas; other services: one replica for these tests. All services use locally built/imported images.

```sh
for svc in gateway events payments notifications; do
  docker build -t quickticket-$svc:v1 ./app/$svc
  k3d image import -c quickticket-lab1112 quickticket-$svc:v1
done
# Database bootstrap and controller prerequisites: k8s/README.md
kubectl apply -k k8s
kubectl apply -f labs/lab7/prometheus.yaml -f labs/lab8/mixedload.yaml
kubectl exec deployment/postgres -- psql -U quickticket -d quickticket \
  -c 'UPDATE events SET total_tickets=100000'
kubectl run lab-probe --image=quickticket-gateway:v1 --image-pull-policy=Never \
  --restart=Never --command -- sleep 3600
python -m unittest discover -s tests -v
```

The stock increase is test preparation, preventing sold-out responses from obscuring resilience behavior. Five deterministic tests pass, covering retry classification/budget, circuit recovery and single probe, sliding windows, and bulkhead cancellation/release.

## Implementation

- [Notifications implementation](../app/notifications/main.py), [requirements](../app/notifications/requirements.txt), [Dockerfile](../app/notifications/Dockerfile), [Deployment + Service](../k8s/notifications.yaml).
- [Gateway implementation](../app/gateway/main.py): retry, CLOSED/OPEN/HALF_OPEN circuit breaker, normalized per-endpoint sliding-window rate limiter, payment bulkhead and metrics.
- [Gateway manifest](../k8s/gateway.yaml) exposes all tuning variables and configures `NOTIFICATIONS_URL=http://notifications:8083`.

Notifications accepts `POST /notify` with string `event` and `order_id`, logs the event and returns `status=sent`. Faults use `NOTIFY_FAILURE_RATE` and `NOTIFY_LATENCY_MS`; `/health` reports both. Prometheus exposes `notifications_requests_total{method,path,status}`, `notifications_request_duration_seconds{method,path}`, and `notifications_notify_total{result}`. Dependencies match payments:

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

Retry budget means **three total attempts**, matching the lab's probability calculation. Timeout/connection errors and HTTP 5xx/408/429 retry with exponential backoff plus uniform jitter. Other errors fail immediately; each result increments its labeled counter. The circuit counts the final result after the retry budget, fast-fails while OPEN, and admits a single HALF_OPEN probe after cooldown. The bulkhead wraps the entire CB/retry operation and releases its semaphore even when cancelled. Rate limiting uses normalized paths, excludes health/metrics, and returns `429` with `Retry-After: 1`. Metrics middleware also records rejected responses.

## Actual fault-injection results

The executable probe is [tools/lab11_probe.py](../tools/lab11_probe.py). It opens new connections to exercise all five gateway replicas. Raw observations are committed in [evidence/lab11](evidence/lab11).

```sh
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
kubectl rollout status deployment/notifications
kubectl exec -i lab-probe -- python - checkout 30 < tools/lab11_probe.py
```

```text
statuses: 200=30; pay p99=20.98ms
notifications_notify_total{result="failed"} 70
notifications_notify_total{result="success"} 101
```

The empirical notification failure fraction at this snapshot is 70/171 = **40.94%** (random injection, includes background mixedload and warm-up). This is an observed counter ratio, not the configured 30%. Client-observed payment p99 remains below 100ms while notifications sleep 300ms. The histogram is also exported for Prometheus; the p99 above is the actual probe sample maximum, not a fabricated histogram quantile.

```sh
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.0 NOTIFY_LATENCY_MS=0
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
kubectl rollout status deployment/payments
kubectl exec -i lab-probe -- python - checkout 30 < tools/lab11_probe.py
```

```text
statuses: 200=28, 500=2; pay p99=480.12ms
Prometheus gateway_retry_total, summed across replicas:
  result=retried                85
  result=succeeded_after_retry  22
  result=exhausted              29
```

The retry counters above were captured after the subsequent 100%-failure experiment as well; they are cumulative, not exclusively the 30% test. Both recovery counters are nonzero. Two exhausted requests in 30 attempts are possible: P(all three fail)=0.3^3=2.7%; this is probabilistic, not a guarantee of fewer than two errors.

```sh
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0
kubectl rollout status deployment/payments
kubectl exec -i lab-probe -- python - checkout 80 < tools/lab11_probe.py
# 500=6, 503=74
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
kubectl rollout status deployment/payments
sleep 31
kubectl exec -i lab-probe -- python - checkout 15 < tools/lab11_probe.py
# 200=15; p99=10.76ms
```

Prometheus recorded five transitions each to OPEN, HALF_OPEN and CLOSED. Background mixedload also contributes failures toward the threshold, explaining why this probe alone has fewer than 25 initial 500s.

```sh
kubectl exec -i lab-probe -- python - burst 100 < tools/lab11_probe.py
# 200=46, 429=54; Retry-After: 1 asserted on every 429
```

The rejection counter was 58 in the earlier cumulative Prometheus snapshot and increases with each additional burst. An exploratory **100-way concurrent** burst also produced 23 upstream 502s because the single events pod exhausted its DB pool. The lab's sequential burst above has zero 502s. Rate limiting is per process (approximately 5*10 admissions/second cluster-wide), not a shared global limit; unbounded concurrency can still overwhelm events.

## Bonus: real bulkhead isolation test

Set payments to `PAYMENT_LATENCY_MS=3000`, wait for its rollout, and run a dedicated gateway probe pod with the same image, `RATE_LIMIT_RPS=1000`, `BULKHEAD_PAYMENTS_MAX=10`, `BULKHEAD_PAYMENTS_TIMEOUT_S=0.5`. The elevated limiter isolates the bulkhead experiment from rate-limit rejections; ordinary gateway pods keep the lab defaults. Expose this pod as `bulkhead-probe:8080`, then run [lab11_bulkhead_probe.py](../tools/lab11_bulkhead_probe.py) inside `lab-probe`.

```text
30 concurrent payments: 200=10, 503=20
30 concurrent /events observations: ok=30, slower than 500ms=0
/events sample p99=268.49ms
gateway_bulkhead_in_flight{target="payments"} 0
gateway_bulkhead_rejections_total{target="payments"} 20
```

The pool returns to zero occupants after completion. Payment latency was restored to zero and the temporary gateway pod/service removed. A no-bulkhead comparison was not measured; asynchronous sleeps alone do not necessarily starve an event loop, so the reading's claimed >2s baseline should not be assumed. The demonstrated result is bounded payment occupancy and timely rejection while reads remain responsive.

## Design answers

**Why fire-and-forget?** Email/push delivery is best effort and should not change the result or latency of an already confirmed purchase. A production system should use a durable queue/outbox because an in-process task can be lost when a gateway pod exits. Notifications status does not affect the critical health verdict.

**Why CB outside retry?** A recovered retry sequence is one successful logical payment and should not contribute failures to the circuit. Reversing them counts transient attempts separately and can open a circuit despite recoverable calls. This implementation does not retry `CircuitOpenError` at all; an overly broad retry loop would additionally defeat fast-fail by retrying that error.

**Bulkhead placement and limits.** The outer bulkhead holds one slot across retries/backoff, so retries cannot escape the concurrency bound. Slots, circuit state and rate-limit timestamps are local to each process. A production payment retry also needs an idempotency key; the mock processor does not implement a real monetary side effect.
