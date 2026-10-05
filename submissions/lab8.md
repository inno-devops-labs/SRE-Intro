# Lab 8 - Chaos Engineering: Break Things on Purpose

**Student:** Diana Kalugina

**Date:** 2026-10-05

## Environment and baseline

I ran the experiments on the local `k3d-quickticket` cluster with the Lab 7 Gateway Rollout, the in-cluster Prometheus instance, and the Lab 8 `mixedload` workload.

Before injecting failures, I waited for the baseline window to fill. Gateway 5xx ratio was 0. The baseline p99 was approximately 0.060 s for `/reserve/{id}/pay`, 0.023 s for `/events`, and 0.052 s for `/events/{id}/reserve`.

The hypotheses were written before the failure injection and are included in `submissions/lab8-evidence/hypotheses-before-experiments.md`.

## Experiment 1 - Delete a Gateway pod under load

**Hypothesis:** If I delete one Gateway pod while mixed traffic is running, the remaining replicas will continue serving through the Kubernetes Service. Kubernetes should create a replacement quickly and I expect no significant increase in 5xx responses.

**Method:**

```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
kubectl delete "$VICTIM"
```

I watched the Gateway Pods and queried Prometheus while `mixedload` remained active.

**Observed:** The deletion started at `07:41:12 UTC`. Four replicas remained Ready immediately. A fifth Ready replacement appeared after approximately **9.8 seconds**. Prometheus reported zero estimated Gateway 5xx responses over the observation interval.

The old Pod name still appeared briefly in a one-minute `rate()` query because that range contained samples from before the deletion.

**Comparison:** The hypothesis was correct. Kubernetes self-healing and Service load balancing kept the application available while a replacement Pod started.

**To improve resilience against this failure, I would** add a PodDisruptionBudget with an appropriate `minAvailable` value so multiple voluntary disruptions cannot remove too much Gateway capacity at once.

Evidence: `submissions/lab8-evidence/experiment1-pod-kill.txt`.

## Experiment 2 - Payment latency injection

**Hypothesis:** If Payments adds 2 seconds to every charge, the p99 latency for `/reserve/{id}/pay` will increase by roughly 2 seconds. Other paths should remain fast, and because 2 seconds is lower than the Gateway 5-second timeout, the Gateway should not return 5xx because of the delay alone.

**Method:**

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=30s
```

After the one-minute Prometheus window filled, I queried Gateway error ratio and p99 latency by path.

**Observed:** Gateway 5xx ratio remained 0. Payment p99 increased to approximately **2.43 seconds**. `/events` stayed near **0.025 s**, `/events/{id}/reserve` near **0.081 s**, and `/health` near **0.031 s**.

I then restored `PAYMENT_LATENCY_MS=0`.

**Comparison:** The result matched the hypothesis. The payment path became much slower without causing Gateway timeouts, while read traffic remained almost unchanged.

**To improve resilience against this failure, I would** alert on slow successful payment requests, because a system can violate latency expectations while still returning HTTP 200.

Evidence: `submissions/lab8-evidence/experiment2-payment-latency.txt`.

## Experiment 3 - Redis outage

**Hypothesis:** If Redis is unavailable, ticket reservation will fail because Events needs Redis for reservation holds. Listing events should remain available because it does not require Redis. Health should indicate degraded dependency state.

**Method:**

```bash
kubectl scale deployment/redis --replicas=0
```

I checked `/events`, `/events/1/reserve`, and `/health` from inside the cluster, then restored Redis to one replica.

**Observed:** `/events` continued returning HTTP 200. Reservation requests failed at the probe timeout. `/health` returned HTTP 503 and showed Events as degraded while Payments remained healthy. The Events Pod itself stayed Ready during the short test.

Prometheus showed approximately **21.6** Gateway 5xx responses in the overlapping three-minute range. Because the window also included concurrent `mixedload` traffic, I did not attribute the whole count only to Redis.

**Comparison:** The hypothesis was partly correct. Reservations failed and health became degraded, while read-only event listing stayed available. The surprising part was that the Events Pod stayed Ready during the observed outage.

**To improve resilience against this failure, I would** separate dependency health from readiness so that a Redis problem is visible without unnecessarily removing Events from service for read operations.

Evidence: `submissions/lab8-evidence/experiment3-redis-outage.txt`.

## Task 2 - Combined failure scenario

### Scenario

I combined three stressors:

- Payments failure rate: **25%**
- Payments latency: **600 ms**
- Events database pool: `DB_MAX_CONNS=4`
- `mixedload`: 3 replicas

Commands:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.25 PAYMENT_LATENCY_MS=600
kubectl set env deployment/events DB_MAX_CONNS=4
kubectl scale deployment/mixedload --replicas=3
```

**Hypothesis:** Payments should become the weakest link. I expected `/reserve/{id}/pay` to have the largest 5xx ratio and p99 latency, while event reads should remain much less affected.

### Observations

I sampled the system across a three-minute steady-state window.

| UTC time | `/pay` 5xx | `/pay` p99 | `/events` p99 | `/reserve` p99 |
|---|---:|---:|---:|---:|
| 07:55:20 | 24.7% | 0.846 s | 0.024 s | 0.030 s |
| 07:56:20 | 27.9% | 0.848 s | 0.023 s | 0.027 s |
| 07:57:20 | 23.8% | 0.847 s | 0.022 s | 0.026 s |

The payment path reacted first and remained the most degraded. The reduced Events DB pool did not create a meaningful reservation latency increase at this load.

**Weakest link:** Payments was the weakest component in this scenario. The injected failure rate directly created checkout failures, and payment latency was much higher than the read and reservation paths.

**How I would improve it:** I would add latency alerting and use safe failure handling for payment requests. I would avoid blind retries unless the payment operation has idempotency protection.

Evidence: `submissions/lab8-evidence/combined-scenario.txt`.

## Bonus - Resilience improvement

### Weakness chosen

The main weakness was that slow-but-successful payment requests were not detected by an alert.

### Change

I updated `labs/lab7/prometheus.yaml` with a Prometheus alert named `GatewayPayP99High`.

The rule fires when payment-path p99 is above **0.6 seconds** for **40 seconds**:

```yaml
- alert: GatewayPayP99High
  expr: |
    histogram_quantile(
      0.99,
      sum by (le) (
        rate(gateway_request_duration_seconds_bucket{path="/reserve/{id}/pay"}[2m])
      )
    ) > 0.6
  for: 40s
```

### Before and after

Before adding the rule, querying `ALERTS{alertname="GatewayPayP99High"}` returned no matching series because the alert did not exist.

After applying the rule and repeating the combined scenario:

```text
07:59:20  payment_p99=0.842  alert=pending
08:00:20  payment_p99=0.847  alert=firing
08:01:20  payment_p99=0.848  alert=firing
```

The improvement is detection, not latency reduction. The service still behaves the same, but an operator is now notified about sustained slow payments before the latency grows into a timeout problem.

**Tradeoff:** A lower latency threshold gives earlier warning but can create noisy alerts during harmless traffic variation, so the value should be tuned using real production data.

Evidence: `submissions/lab8-evidence/bonus-alert-after.txt`.

## Recovery

After the experiments I restored:

```text
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
DB_MAX_CONNS=10
mixedload replicas=2
redis replicas=1
```

Payments, Events, Redis, and mixedload completed recovery successfully. The Gateway Rollout returned to Healthy with five Ready replicas.

## Final verification

- Task 1: three hypothesis-driven chaos experiments completed
- Experiment 1: Gateway Pod deletion recovered automatically
- Experiment 2: 2-second payment latency observed without Gateway 5xx
- Experiment 3: Redis outage degraded reservation while event listing remained available
- Task 2: combined multi-failure scenario completed
- Weakest link identified as Payments
- Bonus: payment p99 Prometheus alert added
- Alert observed transitioning from pending to firing
- Recovery settings restored after the experiments
