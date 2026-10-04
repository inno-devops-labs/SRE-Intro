# Lab 8 — Chaos Engineering

## Experiment 1 — Pod Kill Under Load

### Hypothesis

If I delete one gateway pod while traffic is flowing, the application should
continue serving requests with little or no 5xx impact because the Kubernetes
Service will route traffic to the remaining four healthy gateway pods and
Kubernetes will automatically create a replacement pod.

I expect a temporary reduction in available gateway capacity, but not a complete
service outage.


### Method

A gateway pod was deleted while the `mixedload` deployment was continuously
sending traffic through the application.

```text
Victim: gateway-684b75c5f5-cp4kv
Start: 2026-10-04T21:14:57.558929+03:00
Replacement: gateway-684b75c5f5-bg7wj
Replacement creation time: 0.687 seconds
Full recovery to 5/5 Ready: 4.692 seconds
Recovery: 2026-10-04T21:15:02.251155+03:00
```

### Observations

All five gateway replicas were Ready again after approximately 4.7 seconds.

Prometheus reported:

```text
5xx in last 1m: 0
```

The surviving gateway pods continued receiving traffic while the replacement
pod started:

```text
gateway-684b75c5f5-fbpvp  3.109 req/s
gateway-684b75c5f5-dfqzw  2.927 req/s
gateway-684b75c5f5-x5758  2.436 req/s
gateway-684b75c5f5-jqbtc  2.582 req/s
gateway-684b75c5f5-bg7wj  0.672 req/s
```

The deleted pod still appeared temporarily in the one-minute Prometheus rate
window because historical samples were still included.

### Comparison With Hypothesis

The hypothesis was correct. Kubernetes created a replacement almost
immediately, and the Service continued routing requests to the remaining
healthy gateway replicas. No 5xx failures were observed during the experiment.

The main surprise was how quickly the ReplicaSet created the replacement pod:
the replacement object appeared in under one second and full readiness returned
in under five seconds.

### Resilience Improvement

To improve resilience further, I would use a PodDisruptionBudget and pod
anti-affinity so voluntary disruptions and node failures are less likely to
reduce gateway capacity significantly.

## Experiment 2 — Payment Latency Injection

### Hypothesis

If the payments service takes 2 seconds per request, `/pay` latency should rise
significantly, but the gateway should not return 5xx responses because the
configured 2000 ms payment delay is still below the gateway timeout of 5000 ms.

I expect `/events` read requests to remain largely unaffected because they do
not depend on the payments service.


### Method

A baseline was measured with normal payment latency, then the payments
deployment was updated with:

```text
PAYMENT_LATENCY_MS=2000
```

The `mixedload` deployment continued generating full checkout traffic.

Experiment start:

```text
2026-10-04T21:25:26+03:00
```

Observation time:

```text
2026-10-04T21:26:44+03:00
```

### Baseline

Before latency injection, the payment endpoint p99 was approximately:

```text
/reserve/{id}/pay  0.00995 seconds
```

### Observations

The payment path continued receiving traffic:

```text
pay RPS: 0.8546
```

The gateway 5xx ratio remained:

```text
0
```

Observed p99 latencies were:

```text
/events                  0.00883 seconds
/events/{id}/reserve     0.02470 seconds
/reserve/{id}/pay        2.485 seconds
/health                  0.00974 seconds
```

The payment endpoint increased from approximately 0.01 seconds to 2.485
seconds, while the read path remained below 0.01 seconds.

### Comparison With Hypothesis

The hypothesis was correct. Injecting 2000 ms of payment latency dramatically
increased `/pay` latency without causing gateway 5xx responses because the
delay remained below the configured 5000 ms gateway timeout.

The `/events` read path was effectively unaffected because it does not depend
on the payments service.

The most notable result was that latency degradation can be severe while the
error rate remains zero. Monitoring only availability or 5xx rates would miss
this type of partial degradation.

### Resilience Improvement

To improve resilience against slow dependencies, I would add a latency SLO
alert for the `/pay` endpoint so high p99 latency is detected even when requests
are still returning successful status codes.

## Experiment 3 — Redis Failure

### Hypothesis

If Redis goes down, users should still be able to list events because the read
path does not require Redis, but ticket reservations should fail because the
reservation hold depends on Redis.

I also expect the service health endpoint to report a degraded dependency state
while Redis is unavailable.


### Method

Redis was scaled to zero while `mixedload` continued sending traffic:

```text
Experiment start: 2026-10-04T21:28:13+03:00
kubectl scale deployment/redis --replicas=0
```

The Redis pod disappeared completely from the default namespace.

### Observations

The application behaved differently depending on the request path.

Prometheus showed the read path continuing successfully:

```text
/events 200 4.1821 req/s
```

A reservation request failed with:

```text
504 5.003627s
```

The gateway health endpoint reported:

```json
{
  "status": "degraded",
  "checks": {
    "events": "degraded",
    "payments": "ok",
    "circuit_payments": "CLOSED"
  }
}
```

Prometheus also observed timeout responses on the reservation path:

```text
/events/{id}/reserve 504 0.0658 req/s
```

### Comparison With Hypothesis

The hypothesis was correct.

Users could still read the event list because the `/events` path depends on
PostgreSQL but not Redis.

Reservation operations degraded because ticket holds depend on Redis. The
surprising part was that the failure did not return immediately. The request
waited for approximately five seconds and then returned HTTP 504, matching the
gateway timeout.

The `/health` endpoint also became degraded because the events service includes
Redis in its dependency health check.

### Resilience Improvement

To improve resilience against Redis failure, I would fail reservation requests
quickly when Redis is unavailable instead of allowing them to wait until the
five-second gateway timeout. This would reduce user-visible latency and prevent
blocked requests from consuming gateway capacity.

---

# Task 2 — Combined Failure Scenario

## Scenario Design

I combined two dependency degradations:

- Payments: 30% injected failure rate and 500 ms latency.
- Events: database connection pool limited to 3 connections.
- Load: `mixedload` increased from 2 to 3 replicas.

I chose this scenario because it combines downstream service unreliability with
database resource contention under increased traffic.

I expect the payment path to show errors and elevated latency immediately due to
the injected payment failures and delay. I also expect reservation latency to
increase if the restricted database connection pool becomes saturated.

The goal is to identify which dependency becomes the weakest link and which
golden signal reacts first.


## Combined Scenario Observations

The combined failure scenario started at:

```text
2026-10-04T21:30:52+03:00
```

The following conditions were active:

```text
Payments:
  PAYMENT_FAILURE_RATE=0.3
  PAYMENT_LATENCY_MS=500

Events:
  DB_MAX_CONNS=3

Load:
  mixedload replicas=3
```

Measurements were collected once per minute.

### Sample 1 — 21:31:01

```text
5xx ratio: 0

p99 /health                 0.00978 s
p99 /events                 0.00950 s
p99 /events/{id}/reserve    0.02476 s
p99 /reserve/{id}/pay       0.58900 s
```

### Sample 2 — 21:32:01

```text
5xx ratio: 0.08985

p99 /health                 0.01255 s
p99 /events                 0.00754 s
p99 /events/{id}/reserve    0.01023 s
p99 /reserve/{id}/pay       0.74750 s
```

### Sample 3 — 21:33:02

```text
5xx ratio: 0.08197

p99 /health                 0.00985 s
p99 /events                 0.00495 s
p99 /events/{id}/reserve    0.00956 s
p99 /reserve/{id}/pay       0.74750 s
```

### Sample 4 — 21:34:03

```text
5xx ratio: 0.08444

p99 /health                 0.00989 s
p99 /events                 0.00497 s
p99 /events/{id}/reserve    0.00972 s
p99 /reserve/{id}/pay       0.74750 s
```

## Which Golden Signal Reacted First?

Latency reacted first.

In the first sample, the overall 5xx ratio was still zero, but payment p99
latency had already increased to approximately 0.589 seconds.

By the second sample, the gateway 5xx ratio had increased to approximately 9%.

This demonstrates that latency can provide an earlier indication of dependency
degradation than the aggregate error rate.

## Worst Latency Amplification

The `/reserve/{id}/pay` path experienced the largest latency increase.

Its p99 latency stabilized at approximately:

```text
0.7475 seconds
```

In comparison, `/events` and `/events/{id}/reserve` remained around only a few
milliseconds.

## Weakest Link

The payments service was the weakest link in this experiment.

The injected payment failure rate and latency caused both elevated payment
latency and an overall gateway 5xx ratio of approximately 8-9%.

Reducing the events database connection pool to three connections did not
produce a significant increase in `/events` or reservation latency under the
observed load.

This suggests that, for this workload, the payment dependency became a
bottleneck before the events database connection pool did.

## Resilience Improvement

I would make the payments path more resilient by combining:

- latency-based alerting,
- circuit breaking,
- fast failure for unhealthy payment dependencies,
- and carefully bounded retries for transient failures.

This would reduce the amount of time requests spend waiting on a degraded
payments service and limit cascading load.

The database connection pool should still be monitored under higher load, but it
was not the limiting component in this experiment.
