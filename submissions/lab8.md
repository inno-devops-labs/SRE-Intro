# Lab 8 - Chaos Engineering and Resilience

## Goal

Test QuickTicket with controlled failures: define steady state, write a
hypothesis, inject a failure with a limited blast radius, observe Prometheus and
Kubernetes, restore the system, and document the resilience gap.

## Environment and steady state

Experiments used the k3d cluster with the five-replica Argo Rollouts gateway,
in-cluster Prometheus, PostgreSQL, Redis, events, payments, and the provided
`labs/lab8/mixedload.yaml` load generator. The initial baseline was approximately
10 RPS with no 5xx responses and a `Healthy` gateway Rollout with 5/5 replicas.

The Docker Compose Grafana datasource was not used for these measurements because
it scrapes the Compose Prometheus, while the experiment traffic ran through k3d.
Prometheus queries were executed inside the in-cluster Prometheus instead.

## Task 1 - Three Chaos Experiments

### Experiment 1 - Gateway Pod Kill Under Load

**Steady state:** five gateway replicas ready, no elevated 5xx rate, and traffic
continuing through the gateway Service.

**Hypothesis:** If one gateway Pod is deleted while traffic is flowing, Kubernetes
will create a replacement and the other four Pods will continue serving traffic,
because the Rollout maintains five replicas and the Service load-balances across
ready endpoints.

**Method and observation:**

```text
victim=gateway-7df587747d-6vt65
killed_at=2026-09-26T17:49:04+03:00
ready=4 at 17:49:04
ready=5 at 17:49:27
recovery_seconds=23
5xx_last_3m: empty result
```

Per-Pod request rates after recovery were approximately:

```text
2.38, 2.31, 2.31, 1.29, 2.11 requests/second
```

**Comparison:** The hypothesis was correct. The blast radius was one of five
gateway Pods and no 5xx increase was observed. The replacement took 23 seconds,
so a production runbook should track replacement readiness time.

**Improvement:** I would add a dashboard and alert for slow Pod replacement and
temporarily reduce rollout capacity only when remaining replicas can satisfy the
SLO.

### Experiment 2 - Payment Latency Injection

**Steady state:** payment requests complete within the gateway timeout and read
paths remain fast.

**Hypothesis:** If payments adds 2000ms latency, `/pay` p99 will increase by roughly
two seconds while `/events` and reserve remain mostly unaffected, because only the
payment dependency is delayed and the gateway timeout is 5000ms.

The first attempt was invalid because the load generator had already created 50
orders and exhausted event 1. I reset `orders` and Redis, enabled latency before
starting one loadgen Pod, and repeated the experiment.

**Method and observation:**

```text
PAYMENT_LATENCY_MS=2000
started_at=2026-09-26T18:20:13+03:00
observed_at=2026-09-26T18:21:58+03:00
orders=42
pay_rps=0.399998
error_ratio: empty result
p99 /events = 0.097s
p99 /events/{id}/reserve = 0.095s
p99 /reserve/{id}/pay = 2.485s
```

**Comparison:** The hypothesis was correct. `/pay` became the slow path while
read and reserve paths stayed near normal latency, with no observed 5xx.

**Improvement:** I would alert on p95/p99 latency for `/pay`, not only on 5xx,
because slow successful payments can burn the user experience without triggering
an error-rate alert.

### Experiment 3 - Redis Failure

**Steady state:** Redis is ready, events health is healthy, reservations can be
created, and checkout can proceed.

**Hypothesis:** If Redis is scaled to zero, read-only event listing will be less
affected than reservation and payment confirmation, because PostgreSQL stores the
event catalogue but Redis stores temporary reservations.

**Method and observation:**

```text
kubectl scale deployment/redis --replicas=0
redis_down_at=2026-09-26T18:26:39+03:00
```

After approximately one minute of mixedload:

```text
/events: 200 = 0.145 RPS, 502 = 0.370 RPS
/events/{id}/reserve: 504 = 0.158 RPS, 502 = 0.336 RPS
/health: 200 = 0.073 RPS, 503 = 0.464 RPS
/reserve/{id}/pay: no successful traffic
```

**Comparison:** The hypothesis was directionally correct: checkout was the first
meaningful casualty and health became degraded. The read path was not fully
isolated because events health and Redis interactions affected gateway behavior.

**Improvement:** Reservations should fail closed with an explicit dependency
error when Redis is unavailable. The service must not imply that a reservation
exists unless it was actually written to Redis.

Redis was restored to one replica and events was restarted for a clean reconnect.

## Task 2 - Combined Failure Scenario

### Scenario

The combined scenario stacked two dependency failures and increased concurrency:

```text
payments: PAYMENT_FAILURE_RATE=0.3, PAYMENT_LATENCY_MS=500
events: DB_MAX_CONNS=3
mixedload: 3 replicas
```

The loadgen was temporarily pointed at event 3 instead of event 1 so the
observation window would not immediately exhaust the small event-1 inventory. The
repository file was not modified.

### Observations

Prometheus samples over the run were:

```text
18:36:29 error_ratio=0.0081, p99 events=0.443s, reserve=0.625s, health=0.678s
18:37:10 error_ratio=0.0097, p99 events=0.468s, reserve=0.683s, health=0.928s
18:38:07 error_ratio=0.0319, p99 events=0.467s, reserve=0.700s
```

The error ratio rose but stayed below the existing 5% alert threshold. The
`events_db_pool_size` query returned no series, so DB-pool saturation was not
claimed as a measured result; this gauge was not available in the active scrape
configuration.

The final status breakdown was:

```text
/events: 200 = 6.49 RPS, 502 = 0.20 RPS
/events/{id}/reserve: 409 = 6.40 RPS, 502 = 0.018 RPS
/reserve/{id}/pay: no traffic in the last minute
orders accumulated: 221
```

The first visible signal was the gradual error-rate increase, followed by higher
reserve and health latency. The weakest link was the checkout dependency chain:
payment failures and latency reduced successful confirmations, while repeated
reservation attempts produced expected `409` contention as inventory became
scarce. Better dependency-specific metrics are needed to distinguish DB pressure
from inventory contention.

All injected values were restored afterwards:

```text
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
DB_MAX_CONNS=10
orders=0
Redis=1/1
mixedload stopped
```

## Bonus Task - Resilience Improvement

### Weakness and fix

Before the fix, Redis outage surfaced as ambiguous gateway `502/504` responses and
could spend time attempting a reservation that could not be confirmed. The fix in
`app/events/main.py` checks Redis before reservation work, returns
`503 Reservation store unavailable` when Redis is unreachable, and catches Redis
write errors. It fails before returning a reservation that was not persisted.

The fix was built and deployed as:

```text
quickticket-events:lab8-resilience
OS=linux ARCH=arm64
```

### Before/after proof

Before the fix, the Redis experiment produced gateway `502/504` reserve responses
and degraded health. After the fix, with Redis scaled to zero, an in-cluster
probe returned:

```text
events_http=503
{"detail":"Reservation store unavailable"}
gateway_http=503
{"detail":{"detail":"Reservation store unavailable"}}
```

Redis was restored to `1/1` and events was restarted after the test. The trade-off
is immediate explicit checkout failure during Redis outage instead of a misleading
or delayed reservation response; data consistency and diagnosability improve.

## Artifacts

- `submissions/lab8.md` - experiment evidence and analysis
- `app/events/main.py` - Redis fail-closed resilience improvement
- Kubernetes runtime image was imported locally for the test; the repository
  manifest keeps the published GHCR image reference
- Screenshots: none; CLI output and Prometheus responses are the primary evidence

## Checklist

- [x] Task 1 done - three chaos experiments with hypotheses and recovery
- [x] Task 2 done - combined failure scenario with weakest-link analysis
- [x] Bonus Task done - Redis fail-closed fix with before/after proof
- [x] Title is clear (`feat(labN): <topic>` style)
- [x] No secrets or large temporary files committed
- [x] Submission file at `submissions/lab8.md` exists
