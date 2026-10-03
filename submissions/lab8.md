# Lab 8 — Chaos Engineering: Break Things on Purpose

## Overview

This report documents three chaos experiments, one combined-failure scenario, and one resilience improvement for QuickTicket.

The experiments were executed against the Kubernetes/k3d deployment with:

- `gateway` behind a Kubernetes Service and managed by Argo Rollouts
- `events`, `payments`, PostgreSQL, and Redis running in the cluster
- in-cluster Prometheus in the `monitoring` namespace
- `mixedload` generating `/events`, `/events/{id}/reserve`, and `/reserve/{id}/pay` traffic

Before the final experiment runs, the PostgreSQL schema was restored from `app/seed.sql` because the `events` and `orders` tables were missing. After restoration, `/events` returned HTTP 200 and the baseline gateway 5xx rate dropped to approximately zero.

---

# Task 1 — Three Chaos Experiments

## Experiment 1 — Pod Kill Under Load

### Hypothesis

If I delete one gateway pod while traffic is flowing, user requests will continue to be served with little or no interruption because the Kubernetes Service will route traffic to the remaining healthy gateway replicas while Kubernetes creates a replacement pod.

### Method

A 5xx baseline was captured immediately before the experiment:

```text
20:38:23
5xx rate: 0.01818181818181818 RPS
```

The victim pod was selected and deleted:

```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
echo "Killing $VICTIM at $(date +%H:%M:%S)"
kubectl delete "$VICTIM"
```

Observed deletion:

```text
Killing pod/gateway-556d6d4566-pnxcb at 20:38:46
pod "gateway-556d6d4566-pnxcb" deleted from default namespace
```

A second terminal watched pod replacement:

```bash
kubectl get pods -l app=gateway -w
```

### Observations

The replacement pod was:

```text
gateway-556d6d4566-s8vfc
```

Observed recovery timeline:

```text
Pending             ~0 s
ContainerCreating   ~0 s
Running             ~3 s
Ready 1/1           ~10 s
```

After replacement, the per-pod request rates were approximately:

```text
gateway-695f674c8f-mqgfp   2.09 RPS
gateway-695f674c8f-4z5c4   2.05 RPS
gateway-695f674c8f-drspq   2.31 RPS
gateway-695f674c8f-4jbw4   2.53 RPS
gateway-695f674c8f-vsgjl   1.87 RPS
gateway-556d6d4566-s8vfc   1.23 RPS
```

The previous victim still appeared in the one-minute Prometheus rate window with a small value because historical samples from that pod were still inside the query window.

The 5xx increase over the one-minute window was approximately:

```text
3.2727
```

Breaking it down by path and status showed:

```text
/health                 503   ~3.27
/events                 502   0
/events/{id}/reserve    502   0
```

No user-facing `/events` or `/reserve` requests failed during the replacement. The only observed failures were health-check responses.

### Comparison with hypothesis

The hypothesis was correct. Kubernetes recreated the deleted gateway pod in approximately 10 seconds, while the Service continued routing user traffic to the remaining healthy replicas.

The most notable result was that health checks briefly returned HTTP 503 while user-facing traffic continued successfully.

### Resilience improvement

To improve resilience against pod termination, I would keep sufficient replica capacity and use readiness probes together with graceful termination so terminating pods are removed from Service endpoints before they stop accepting requests.

---

## Experiment 2 — Payment Latency Injection

### Hypothesis

If the payments service takes 2 seconds per request, the payment path will become significantly slower, but requests should still succeed because the injected latency of 2000 ms is below the gateway timeout of 5000 ms. Read-only endpoints such as `/events` should remain mostly unaffected.

### Method

The artificial payment latency was enabled:

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=30s
```

The deployment rolled out successfully.

To ensure there was enough payment traffic, old reservation state was cleared before the final measurement:

```bash
kubectl scale deployment/mixedload --replicas=0
kubectl exec deployment/redis -- redis-cli FLUSHALL
kubectl exec deployment/postgres -- \
  psql -U quickticket -d quickticket \
  -c 'TRUNCATE TABLE orders RESTART IDENTITY;'
kubectl scale deployment/mixedload --replicas=2
```

### Observations

Prometheus p99 latency by path showed:

```text
/events                   ~0.085 s
/events/{id}/reserve      ~0.358 s
/reserve/{id}/pay         ~4.806 s
```

A direct reservation followed by payment produced:

```text
Reserve response:
{"reservation_id":"b814c94d-41de-44c8-8468-207ce83658c3", ...}

payment status=200 time=2.037040s
```

The overall 5xx ratio at that moment was approximately:

```text
0.0101
```

A path-level breakdown showed that payment requests were not generating 5xx errors:

```text
/health                 503   0.01818 RPS
/events                 502   0
/events/{id}/reserve    502   0
```

The read path stayed fast, while payment latency increased strongly.

The Prometheus p99 estimate was higher than the direct 2.04-second request because histogram quantiles are estimated from buckets over a moving time window.

### Bonus observation inside Experiment 2 — latency beyond gateway timeout

The payment latency was increased beyond the gateway timeout:

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
kubectl rollout status deployment/payments --timeout=30s
```

A direct payment probe then returned:

```text
payment status=504 time=5.013266s
```

The gateway did not wait for the full 6-second downstream delay. It terminated the request after approximately its configured 5-second timeout.

The payment latency was then restored:

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
kubectl rollout status deployment/payments --timeout=30s
```

### Comparison with hypothesis

The hypothesis was correct. With 2000 ms of artificial payment latency, the payment path became much slower but still returned HTTP 200 because the request completed before the 5000 ms gateway timeout.

The additional 6000 ms test confirmed that the gateway timeout protects the system from excessively slow downstream payment calls by returning HTTP 504 after approximately 5 seconds.

### Resilience improvement

To improve resilience against slow payment dependencies, I would add latency-based alerting and a circuit breaker so sustained slowness is detected and isolated before it turns into widespread timeouts.

---

## Experiment 3 — Redis Failure

### Hypothesis

If Redis goes down, listing events will continue to work because the read path can use PostgreSQL without Redis, but ticket reservations will fail because Redis is required to store temporary reservation holds. The health endpoint should report Redis as down.

### Method

Redis was scaled to zero replicas:

```bash
kubectl scale deployment/redis --replicas=0
kubectl get pods -l app=redis
```

Result:

```text
No resources found in default namespace.
```

The main paths were then tested from inside the cluster.

### Observations

Read-only event listing continued to work:

```text
GET /events:
200 0.022913s
```

Reservation failed:

```text
POST /reserve:
{"detail":"Events service timeout"}
504 5.011104s
```

The events service health endpoint reported a degraded state with PostgreSQL still healthy and Redis down:

```text
HTTP 503
{"status":"degraded","checks":{"postgres":"ok","redis":"down"}}
```

Prometheus also showed the degradation:

```text
/health                  503   ~1.109 RPS
/events                  502   0
/events/{id}/reserve     504   ~0.0206 RPS
```

The read path remained available, but the reservation path timed out at approximately the gateway timeout.

Redis was restored after the experiment:

```bash
kubectl scale deployment/redis --replicas=1
kubectl wait --for=condition=Available deployment/redis --timeout=60s
```

The Redis pod returned to `1/1 Running`.

### Comparison with hypothesis

The hypothesis was correct.

The `/events` read path remained available without Redis, while `/reserve` failed because the reservation flow depends on Redis for temporary ticket holds. The health endpoint correctly reported Redis as down.

A notable observation was that the reservation failure surfaced to the client as a gateway HTTP 504 after approximately 5 seconds rather than failing immediately.

### Resilience improvement

To improve resilience against Redis failure, I would fail reservation requests faster when Redis is unavailable and add a circuit breaker or explicit dependency-health check so requests do not wait for the full gateway timeout.

---

# Task 2 — Combined Failure Scenario

## Scenario design

I used the degraded-dependencies scenario with three simultaneous stressors:

- payments failure rate: 30%
- payments artificial latency: 500 ms
- events database connection pool limited to 3 connections
- mixed load increased to 3 replicas

Before the experiment, old reservation/order state was cleared and event 1 capacity was temporarily increased to ensure the full checkout path would continue running for several minutes.

The scenario started at approximately:

```text
20:56:17
```

Commands:

```bash
kubectl set env deployment/payments \
  PAYMENT_FAILURE_RATE=0.3 \
  PAYMENT_LATENCY_MS=500

kubectl set env deployment/events DB_MAX_CONNS=3

kubectl scale deployment/mixedload --replicas=3

kubectl rollout status deployment/payments --timeout=30s
kubectl rollout status deployment/events --timeout=30s
```

## Hypothesis

If the payments service has a 30% failure rate and 500 ms latency while the events database connection pool is limited to 3, the payment path will show the first and strongest increase in errors. Reservation latency may also increase due to database contention, while read-only `/events` requests should degrade less severely.

## Observations

### Early sample

At approximately `20:56:38`, shortly after the scenario started:

```text
Overall 5xx ratio: ~1.08%
```

Errors were already visible on the payment path:

```text
/reserve/{id}/pay HTTP 500   ~0.140 RPS
/events HTTP 502             0
```

Observed p99 latency:

```text
/events                   ~0.138 s
/events/{id}/reserve      ~0.247 s
/reserve/{id}/pay         ~0.746 s
```

### Later sample

At approximately `20:59:15`, after the failure conditions had fully propagated:

```text
Overall 5xx ratio: ~8.21%
```

Path-level errors:

```text
/health                  503   0
/events                  502   0
/reserve/{id}/pay        503   0
/reserve/{id}/pay        500   ~0.927 RPS
```

Observed p99 latency:

```text
/events                   ~0.163 s
/events/{id}/reserve      ~0.281 s
/reserve/{id}/pay         ~0.749 s
```

Observed request rates:

```text
/events                   ~3.22 RPS
/events/{id}/reserve      ~3.24 RPS
/reserve/{id}/pay         ~3.16 RPS
```

The application-level `events_db_pool_size` metric was observed as:

```text
events_db_pool_size 0.0
```

This metric reflects connections in use at scrape time. Because the events and reservation paths remained successful with comparatively low latency, the `DB_MAX_CONNS=3` limit did not become the dominant bottleneck under this workload.

## Which golden signal reacted first?

The error signal reacted most clearly.

The overall 5xx ratio increased from approximately 1.08% shortly after failure injection to approximately 8.21% after several minutes.

Latency also increased, especially on the payment path, but the error signal showed the strongest degradation.

## Which path had the worst latency amplification?

The worst affected path was:

```text
/reserve/{id}/pay
```

Its p99 latency was approximately 0.75 seconds, while `/events` remained around 0.16 seconds and `/events/{id}/reserve` around 0.28 seconds.

## Weakest link

The payments service was the weakest link.

The injected 30% payment failure rate propagated directly into gateway HTTP 500 responses, while limiting the events database pool to 3 connections did not cause visible application failures at the tested load.

To make the payment path more resilient, I would use idempotency for payment requests, bounded retries only for safe transient failures, and a circuit breaker so a degraded payments dependency does not repeatedly consume gateway capacity.

## Recovery

The environment was restored after the experiment:

```bash
kubectl set env deployment/payments \
  PAYMENT_FAILURE_RATE=0.0 \
  PAYMENT_LATENCY_MS=0

kubectl set env deployment/events DB_MAX_CONNS=10

kubectl scale deployment/mixedload --replicas=2
```

The temporary event capacity increase and generated state were also reset.

After recovery, the gateway 5xx ratio returned to:

```text
0
```

and all services were `Running`.

---

# Bonus Task — Resilience Improvement

## Weakness chosen

Experiment 2 showed a hidden degradation mode: the payment service could become slow while requests still returned HTTP 200.

With:

```text
PAYMENT_LATENCY_MS=2000
```

a direct payment request took approximately:

```text
2.037 s
```

and Prometheus showed strongly increased payment p99 latency.

However, before the fix there was no dedicated alert for slow-but-successful payment requests.

## Fix implemented

I added a Prometheus alert rule named:

```text
HighPaymentLatency
```

The rule monitors the payment path p99 latency:

```promql
histogram_quantile(
  0.99,
  sum by (le, path) (
    rate(
      gateway_request_duration_seconds_bucket{
        path="/reserve/{id}/pay"
      }[1m]
    )
  )
) > 1
```

The condition must remain above 1 second for 30 seconds.

The rule was added in:

```text
labs/lab8/payment-latency-rule.yaml
```

The in-cluster Prometheus deployment was also updated to load:

```text
/etc/prometheus-rules/*.yml
```

The rule loaded successfully:

```text
HighPaymentLatency
```

## Re-run of the same experiment

The same degradation was reproduced:

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=30s
```

Observed payment p99:

```text
/reserve/{id}/pay p99 = 2.485 s
```

Prometheus reported:

```text
alertname: HighPaymentLatency
severity: warning
state: firing
value: 2.485
```

The Prometheus rule itself reported:

```text
health: ok
state: firing
```

## Before vs after

### Before fix

- Payment latency increased significantly.
- Direct payment requests could still return HTTP 200.
- The degradation was visible in metrics but no dedicated alert detected it.

### After fix

- The same 2-second artificial payment degradation was reproduced.
- Payment p99 reached approximately 2.485 seconds.
- `HighPaymentLatency` successfully entered the `firing` state.

The improvement does not reduce latency itself; it improves detection of a slow-but-still-successful downstream dependency.

## Trade-off

The fix improves observability rather than request latency itself. The alert threshold and duration may require tuning to avoid false positives during normal short-lived latency spikes.

---

# Final result

Completed:

- [x] Task 1 — 3 chaos experiments with hypotheses, commands, observations, comparisons, and resilience improvements
- [x] Task 2 — combined failure scenario with multiple simultaneous failures, timestamps, golden-signal observations, worst path, and weakest-link analysis
- [x] Bonus Task — resilience improvement with a Prometheus alert and before-vs-after proof
