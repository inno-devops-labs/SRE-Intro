# Lab 8 — Chaos Engineering: Break Things on Purpose

## Environment and baseline

Experiments ran on the local `k3d-quickticket` cluster with the Lab 7 Gateway Rollout, in-cluster Prometheus, and the Lab 8 `mixedload` deployment.

Before the experiments, event 1 had 100 tickets and no sales. To keep the sustained checkout load from exhausting this test fixture, its local capacity was raised to 100,000 tickets. The `mixedload` workload used two replicas except during the combined scenario, when it used three.

The recorded hypotheses are in [hypotheses-before-experiments.md](lab8-evidence/hypotheses-before-experiments.md). Baseline measurements are in [baseline-metrics.txt](lab8-evidence/baseline-metrics.txt): Gateway 5xx ratio was 0; p99 was about 63 ms for `/reserve/{id}/pay`, 22 ms for `/events`, and 56 ms for `/events/{id}/reserve`.

## Experiment 1 — Delete a Gateway pod under load

**Hypothesis, written before the experiment:** If I delete one Gateway pod while traffic is flowing, the other replicas will continue serving requests through the Kubernetes Service. Kubernetes will replace the deleted pod, with no or only a brief increase in 5xx responses.

**Method:** Kept the two-replica `mixedload` deployment running, deleted one pod with `kubectl delete pod`, and watched Gateway readiness and Prometheus metrics.

**Observed:** The pod deletion began at `2026-10-05 04:09:00 +03:00`. Five non-victim Gateway pods were Ready again 11.23 seconds later. The EndpointSlice returned to five endpoints. Prometheus estimated zero Gateway 5xx responses in the three-minute range. The trailing one-minute per-pod rate still showed samples for the deleted pod; that range included traffic from before deletion.

**Comparison:** The hypothesis was supported. The remaining pods continued serving traffic while Kubernetes created a replacement. The old pod’s nonzero rate was a rate-window effect, not evidence that the deleted pod continued receiving traffic.

**Resilience improvement:** I would add a PodDisruptionBudget with an appropriate `minAvailable` value to limit simultaneous voluntary disruption of Gateway replicas.

Evidence: [experiment1-pod-kill.txt](lab8-evidence/experiment1-pod-kill.txt).

## Experiment 2 — Inject 2 seconds of Payments latency

**Hypothesis, written before the experiment:** If Payments adds 2 seconds to each charge, p99 latency for `/reserve/{id}/pay` will rise by about 2 seconds. Reads should remain fast, and the 2-second delay should stay below the Gateway’s 5-second timeout, so it should not cause 5xx responses.

**Method:** Set `PAYMENT_FAILURE_RATE=0.0` and `PAYMENT_LATENCY_MS=2000` on the Payments Deployment, waited for its rollout, and measured for one minute with `mixedload` running.

**Observed:** The 5xx ratio was 0. The p99 latency for `/reserve/{id}/pay` reached 2.485 seconds. The other p99 values remained much lower: `/events` 0.024 seconds, `/events/{id}/reserve` 0.088 seconds, and `/health` 0.029 seconds. Payments latency was then reset to zero and its Deployment became Ready.

**Comparison:** The hypothesis was supported. The payment path became much slower, while reads stayed fast and the Gateway did not time out.

**Resilience improvement:** I would alert on payment-path p99 latency so operators can detect slow successful requests before they become timeouts or failures.

Evidence: [experiment2-payment-latency.txt](lab8-evidence/experiment2-payment-latency.txt).

## Experiment 3 — Stop Redis

**Hypothesis, written before the experiment:** If Redis goes down, reservations will fail because Events uses Redis for reservation holds. Events health may become degraded, and because Gateway health checks Events, Gateway health may also become degraded. Listing events may remain available because it does not need Redis.

**Method:** Scaled Redis to zero, checked the Events and Gateway EndpointSlices, and issued requests to `/events`, `/events/1/reserve`, and `/health` from inside the cluster. Redis was then scaled back to one replica.

**Observed:** During the short outage, the Events pod remained Ready and stayed in its Service EndpointSlice. `/events` returned HTTP 200. Reservation requests timed out at the probe’s 2-second client limit. `/health` returned HTTP 503 with `events: degraded` and `payments: ok`. After Redis was restored, the Redis, Events, and Gateway pods were Ready, and the Gateway Rollout was Healthy.

Prometheus reported an estimated 24.8 Gateway 5xx responses over a three-minute range. This range overlapped time outside the brief Redis outage and included concurrent `mixedload` traffic, so it cannot be attributed solely to Redis.

**Comparison:** The hypothesis was partly supported. Reservations failed and health reported degradation, but event listing remained available and the Events endpoint was not removed during the observed outage.

**Resilience improvement:** I would separate dependency health from readiness so a Redis outage can be reported without unnecessarily removing Events from service for operations that do not require Redis.

Evidence: [experiment3-redis-outage.txt](lab8-evidence/experiment3-redis-outage.txt).

## Task 2 — Combined failure scenario

**Scenario and reason:** Injected a 30% Payments failure rate and 500 ms Payments latency, limited Events to three database connections, and increased `mixedload` to three replicas. This combined dependency failures with a capacity limit to identify the weakest component.

**Hypothesis, written before the experiment:** The payment path would have the highest 5xx ratio and p99 latency. Reservation latency might also rise if the smaller database pool became contended; ordinary event reads should be less affected.

**Observed over a three-minute run:** The stable samples were collected at `04:26:20`, `04:27:20`, and `04:28:20` local time. The `/reserve/{id}/pay` 5xx ratios were 32.1%, 33.3%, and 25.1%; its p99 was approximately 0.748 seconds in each sample. `/events` and `/events/{id}/reserve` had no 5xx responses in those samples; their p99 latencies remained around 20–25 ms. The first sample, shortly after rollout, was transitional and is recorded in the evidence.

**Comparison and weakest link:** The hypothesis was supported for Payments. It was the weakest link: injected payment failures produced sustained checkout errors, while the reduced Events connection limit did not produce a visible reservation latency increase at this load. Payment p99 and errors were the signals that exposed the degradation.

**Resilience improvement:** I would add a payment-path latency alert and separately investigate safe payment failure handling; I would not add blind retries to a payment operation without idempotency protection.

Evidence: [combined-scenario.txt](lab8-evidence/combined-scenario.txt) and the longer repeated run in [bonus-alert-after.txt](lab8-evidence/bonus-alert-after.txt).

## Bonus — Alert on slow payment requests

**Weakness chosen:** Slow payment requests were not observable through an alert. The combined scenario showed `/reserve/{id}/pay` p99 around 0.748 seconds, above the 0.5-second threshold.

**Change:** Updated `labs/lab7/prometheus.yaml` to load a Prometheus rule named `GatewayPayP99High`. It fires when the two-minute p99 for `/reserve/{id}/pay` exceeds 0.5 seconds for 30 seconds. `promtool check config` and `promtool check rules` both passed.

**Before and after:** Before adding the rule, querying `ALERTS{alertname="GatewayPayP99High"}` returned no series because the rule did not exist. After applying the rule and repeating the combined scenario, the alert was observed as `pending` at `04:25:20` and `firing` at `04:26:20`. The payment p99 remained about 0.748 seconds and payment 5xx remained around 25–33%; the change improved detection, not the service’s latency or availability.

**Tradeoff:** This warning detects slow payments but does not prevent failures, and the 0.5-second threshold may need tuning to avoid alerts during acceptable traffic variation.

Evidence: [prometheus-config-before.yaml](lab8-evidence/prometheus-config-before.yaml) and [bonus-alert-after.txt](lab8-evidence/bonus-alert-after.txt). The rule and its configuration change are in `labs/lab7/prometheus.yaml`.

## Recovery

After the experiments, Payments was restored to `PAYMENT_FAILURE_RATE=0.0` and `PAYMENT_LATENCY_MS=0`, Events to `DB_MAX_CONNS=10`, and `mixedload` to two replicas. Their Deployments completed rollout successfully, and the Gateway Rollout was Healthy.
