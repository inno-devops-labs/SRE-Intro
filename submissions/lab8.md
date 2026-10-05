# Lab 8 — Chaos Engineering

## Setup and hypotheses

Experiments ran on 5 October 2026 (UTC), using the five-replica gateway Rollout and in-cluster Prometheus from [Lab 7](https://github.com/inno-devops-labs/SRE-Intro/pull/571). ArgoCD self-healing was temporarily disabled so it would not undo fault injections.

Two mixedload replicas exercised reads, reservations, and payments. The supplied workload was adapted to use a dedicated 100,000-ticket test event (ID 6), log HTTP status/duration, and bound each curl request to 12 seconds. This avoided running out of tickets and exposed transport errors as well as gateway responses. The same workload was used for the latency comparison before and after the monitoring improvement.

Written before fault injection on 5 October 2026 (all report timestamps are UTC).

1. **Pod deletion:** deleting one of five gateway pods should leave the other four serving traffic while Argo Rollouts creates a replacement. A request already using the deleted pod may fail, but there should be no sustained outage.
2. **Payment latency:** injecting 2000 ms latency should raise checkout latency without sustained gateway 5xx, because the gateway timeout is 5000 ms. Event reads should stay fast.
3. **Redis failure:** listing events should remain possible because it uses PostgreSQL, while reservations should fail because they need Redis. Health should show degraded dependencies. Dependency-sensitive probes may spread the failure beyond reservations.
4. **Combined scenario:** 30% payment failure, 500 ms payment latency, and an events connection pool of three under three load generators should expose payment errors and possibly contention in reservations. The payment path should have the largest latency increase unless the database pool becomes the bottleneck.

Prometheus queries used for the measurements:

```promql
sum(rate(gateway_requests_total[1m]))

(sum(rate(gateway_requests_total{status=~"5.."}[1m])) or vector(0))
/ sum(rate(gateway_requests_total[1m]))

sum(increase(gateway_requests_total{status=~"5.."}[3m])) or vector(0)

sum by (pod)(rate(gateway_requests_total[1m]))

histogram_quantile(0.99,
  sum by (le,path)(rate(gateway_request_duration_seconds_bucket[1m])))
```

The aggregate error ratio includes probe requests; endpoint results and client logs are also checked. Histogram p99 values are bucket estimates, not exact maxima.

## Experiment 1 — Delete one gateway pod

At **06:55:48**:

```bash
kubectl delete pod gateway-5fb7f75577-2dpgq --wait=false
```

The replacement `gateway-5fb7f75577-22ckh` was first observed after **0.65s** (creation timestamp `2026-10-05T06:55:49Z`). All five pods were ready at **06:56:19**, after **30.62s**.

During the replacement gap, the 10-second per-pod rate query showed traffic on the four remaining pods:

```text
gateway-5fb7f75577-929tm  3.40 requests/s
gateway-5fb7f75577-dxcqn  4.00 requests/s
gateway-5fb7f75577-ttvp2  3.20 requests/s
gateway-5fb7f75577-l8cxw  4.40 requests/s
```

At 06:57:24, the gateway 5xx ratio was **0.000**, and the 3-minute 5xx increase was **0.000**. Client logs over the captured 100-second interval recorded 536 successful reads, 536 successful reservations, and 535 successful payments; no non-200 responses or transport failures were recorded.

**Comparison:** the hypothesis held. The replacement object appeared quickly, but readiness took much longer; the other four replicas maintained service during that gap. This test does not guarantee zero failures for every possible in-flight request.

**To improve resilience against this failure, I would** add graceful request draining during termination to protect in-flight requests.

## Experiment 2 — Payment latency

Injection started at 2026-10-05T06:57:25.323589+00:00.

Commands:

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=120s
# After observing:
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
kubectl rollout status deployment/payments --timeout=120s
```

| Sample (UTC) | Read p99 (s) | Reserve p99 (s) | Pay p99 (s) | 5xx ratio |
|---|---:|---:|---:|---:|
| 06:55:47 (baseline) | 0.010 | 0.025 | 0.038 | 0.000 |
| 06:59:24 (latency-2000) | 0.022 | 0.025 | 2.485 | 0.000 |
| 07:01:24 (latency-restored) | 0.010 | 0.025 | 0.025 | 0.000 |

**Comparison:** the hypothesis held. The payment p99 rose to about 2.49 seconds while the error ratio stayed zero; reads and reservations stayed fast. Health remained healthy. The 2-second injected delay is below the gateway's 5-second timeout. No latency alert existed in the in-cluster Prometheus (`/api/v1/rules` returned `groups: []`).

**To improve resilience against this failure, I would** alert on payment latency as well as 5xx errors. The bonus below tests this improvement.

## Experiment 3 — Redis failure

Injection started at 2026-10-05T07:01:24.093715+00:00.

Commands:

```bash
kubectl scale deployment/redis --replicas=0
kubectl wait --for=delete pod -l app=redis --timeout=90s
# After observing:
kubectl scale deployment/redis --replicas=1
kubectl rollout status deployment/redis --timeout=120s
```

Direct HTTP probes early in the failure:

```text
2026-10-05T07:01:24.944431+00:00
GET /events
200 0.010823s
POST /reserve
{"detail":"Events service timeout"}
504 5.009055s
GET /health
{"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}
503
```

After readiness checks reacted:

```text
2026-10-05T07:02:17.543553+00:00
GET /events
502 1.049156s
POST /reserve
{"detail":"Events service unavailable"}
502 1.035154s
GET /health
{"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}
503
```

At 07:02:20, the aggregate gateway error ratio was **28.36%**. Reserve p99 was **7.381s** (histogram estimate). The Events pod became unready because its `/health` depends on Redis.

After restoring Redis, Events recovered without a manual restart. At 07:04:04, health was healthy, reservations succeeded, and the one-minute error ratio was **0.000**.

**Comparison:** the hypothesis only partly held. Reads initially worked and reservations timed out, but later reads also failed with 502 because the dependency-sensitive Events readiness check removed the service endpoint. The failure spread beyond the Redis-dependent path.

**To improve resilience against this failure, I would** separate process readiness from optional dependency checks and fail reservations quickly with a clear Redis-unavailable response while preserving reads.

## Task 2 — Combined failure

The scenario combined 30% payment failures and 500 ms latency with `DB_MAX_CONNS=3` and three workload replicas. It tests checkout dependency failures together with reduced database capacity.

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
```

All changes were active by 2026-10-05T07:06:28.093432+00:00. Measurements covered three minutes:

| UTC | 5xx ratio | Read p99 (s) | Reserve p99 (s) | Pay p99 (s) |
|---|---:|---:|---:|---:|
| 07:06:28 | 5.82% | 0.020 | 0.025 | 0.747 |
| 07:06:58 | 6.44% | 0.019 | 0.025 | 0.748 |
| 07:07:28 | 8.56% | 0.011 | 0.025 | 0.748 |
| 07:07:58 | 8.77% | 0.019 | 0.025 | 0.748 |
| 07:08:28 | 9.74% | 0.020 | 0.025 | 0.748 |
| 07:08:58 | 8.82% | 0.016 | 0.025 | 0.748 |
| 07:09:28 | 7.85% | 0.015 | 0.025 | 0.748 |

Errors and latency first changed in the same 5-second Prometheus sample at **07:05:29 UTC**: error ratio 0.001074 and pay p99 0.661s, versus 0 and 0.047s before injection. The sampling resolution does not establish which changed first.

**Weakest link:** payments in this test. Pay p99 reached 0.748s (about 16 times the pre-test 0.047s), while read and reserve p99 remained below 0.025s. Over the captured 180-second interval, client logs recorded 620 successful reads, 620 successful reservations, 434 successful payments, and 184 failed payments (HTTP 500). The sampled Events logs did not show pool exhaustion. The three-connection pool did not become a demonstrated bottleneck at this load.

The combined hypothesis was partly confirmed: payment errors and latency rose, but expected database contention was not observed. I would protect checkout with explicit timeout/latency monitoring and payment idempotency before considering retries, since blind payment retries can amplify load or duplicate charges.

Restore commands:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0
kubectl set env deployment/events DB_MAX_CONNS=10
kubectl scale deployment/mixedload --replicas=2
```

At 07:11:19, health was healthy and the error ratio returned to 0.000.

## Bonus — Detect slow successful payments

Added [a Prometheus latency rule](../monitoring/prometheus/lab8-config.yaml): payment p99 above **1 second** for **30 seconds**, with warning severity. The ConfigMap preserves the existing gateway scrape configuration and adds the rule file. `promtool check config` passed, then Prometheus reloaded its configuration with SIGHUP.

The 2000 ms payment-latency experiment was repeated with the same two load generators and a 90-second observation period after the payments rollout.

| Observation | Before rule | After rule |
|---|---|---|
| Pay p99 | 2.485s | 2.485s |
| Gateway 5xx ratio | 0.000 | 0.000 |
| Latency alert | No rules configured | Firing at 07:13:21 UTC |

Actual Prometheus rule-state excerpt:

```json
{
  "name": "QuickTicketPaymentLatencyHigh",
  "state": "firing",
  "health": "ok",
  "query": "histogram_quantile(0.99, sum by (le) (rate(gateway_request_duration_seconds_bucket{path=\"/reserve/{id}/pay\"}[1m]))) > 1",
  "duration": 30
}
```

After restoring latency to zero, the rule returned to inactive at the 07:15:46 check.

**Trade-off:** the rule detects a previously invisible degradation, but a short window can be noisy at low traffic. It improves detection, not payment speed; no notification delivery is claimed.

## Cleanup

Payment failure rate and latency are zero, `DB_MAX_CONNS=10`, Redis has one replica, and all five gateway replicas are ready. The mixedload was deleted, synthetic test data was removed, and ArgoCD self-healing was restored on `feature/lab7`. The monitoring rule remains installed for future experiments.
