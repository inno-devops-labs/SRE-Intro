# Lab 8 — Chaos Engineering

## Hypotheses recorded before injection

1. **Gateway pod kill:** Deleting one of five gateway pods under mixed checkout traffic should leave four serving endpoints and trigger a replacement pod. Most requests should succeed, but a request already connected to the terminating pod may fail. Replacement creation and Ready time are different measurements.
2. **Payment latency:** With payments delayed by 2000ms and a 5000ms gateway timeout, successful payments should take about two seconds, while reads remain much faster. Some timeouts or confirmation failures would challenge this hypothesis. At 6000ms, the gateway should return 504 near its timeout rather than wait indefinitely.
3. **Redis outage:** Redis loss should prevent reservations, while PostgreSQL-only reads should remain available. However, the current events readiness/liveness probes use dependency-aware `/health`, so Kubernetes may remove or restart the entire events pod and amplify the outage to reads. I expect this configuration to challenge the read-availability hypothesis.

## Combined scenario design recorded before injection

Scale Redis to zero while payments injects 2000ms latency, and observe for at least three minutes under mixed load. Redis failures should dominate checkout availability; payment latency should increase successful charge time for pre-created holds. Reservation failures may stop the sequential checkout load from reaching payments, so use a controlled payment request on a previously created reservation to distinguish masked traffic from healthy payments.

## Planned resilience improvement

If events probes remove reads from service during Redis failure, change only events readiness/liveness to process probes at `/metrics`. Re-run the same Redis outage at the same load and compare read success, endpoint availability and reservation failures. The tradeoff is that Ready then means process availability rather than full dependency health; `/health` must remain a separate diagnostic signal.

## Environment and observation method

All timestamps below are UTC on 2026-10-03. Tests ran on local `k3d-quickticket`, with five gateway Rollout replicas, two mixedload replicas, one events replica, PostgreSQL, Redis, payments, and the in-cluster Prometheus from Lab 7. Gateway timeout was 5000ms; DB_MAX_CONNS was 10; PAYMENT_FAILURE_RATE was 0.0.

The supplied load generator was adapted into `loadgen/lab8-mixedload.yaml`. It exercises list → reserve → pay against a dedicated event (id 6, one million tickets), avoiding exhaustion of the original demo inventory. The original lab template is unchanged. ImagePullPolicy=IfNotPresent was used locally for cached images because registry pulls intermittently timed out; this is not a resilience fix.

```sql
INSERT INTO events(name, venue, event_date, total_tickets, price_cents)
VALUES ('Lab 8 chaos test', 'Local test', '2027-01-01', 1000000, 100)
RETURNING id;
-- Returned 6. Set EVENT_ID to your returned id before applying the loadgen.
```

```bash
kubectl apply -f loadgen/lab8-mixedload.yaml
kubectl rollout status deployment/mixedload --timeout=60s
kubectl port-forward -n monitoring svc/prometheus 9091:9090
# In another terminal; prints real Prometheus API results every 15 seconds:
python3 loadgen/lab8-observe.py baseline 90
```

The observer records total RPS, per-pod RPS, user-path 5xx ratio, p99 by path, status rates, and 3-minute 5xx increase. User-error ratio excludes diagnostic `/health`; total RPS and the requested 3-minute increase include it. PromQL is in the script, notably:

```promql
(sum(rate(gateway_requests_total{status=~"5..",path!="/health"}[1m])) or vector(0))
/ sum(rate(gateway_requests_total{path!="/health"}[1m]))

histogram_quantile(0.99, sum by (le,path)
  (rate(gateway_request_duration_seconds_bucket{path!="/health"}[1m])))

sum by (pod) (rate(gateway_requests_total[1m]))
sum(increase(gateway_requests_total{status=~"5.."}[3m]))
```

Baseline at 22:12:10: RPS 8.3842, user 5xx ratio 0, 3-minute 5xx increase 0. p99 seconds: `/events` 0.4695, reserve 1.3743, pay 0.6556. Histogram quantiles are bucket-interpolated estimates, not exact request durations. This closed-loop workload waits for checkout completion: lower RPS during latency injection does not independently prove lost capacity. It is not a saturation benchmark; resource saturation was not measured.

## Experiment 1 — Gateway pod kill

**Hypothesis:** See hypothesis 1 above, recorded before injection.

```bash
kubectl get pods -l app=gateway
kubectl delete pod gateway-79795445b6-29zzx --wait=false
kubectl get pods -l app=gateway -w
python3 loadgen/lab8-observe.py pod-kill 60
```

Observed sequence:

- 22:12:40.366: deletion requested. Four existing Ready endpoints remained.
- Replacement `gateway-79795445b6-wt9hr` appeared after 1.376 seconds.
- 22:13:10.130: all five replicas Ready, 29.764 seconds after deletion.
- Before the next injection at 22:13:20, mixedload logs contained 106 read HTTP 200 and 108 pay HTTP 200, with no other read/pay status. Prometheus 3-minute 5xx increase stayed 0.
- Before deletion per-pod RPS was 1.6690, 1.5207, 1.9177, 1.9175, 1.3592. Remaining pods continued serving while the replacement started; total RPS remained about 7.99 near recovery. The deleted pod's rolling 1-minute rate decayed rather than instantly becoming zero, because the query still includes earlier samples.

**Comparison:** Self-healing and Service load balancing matched the hypothesis. Creation was fast, but readiness took almost 30 seconds. No client failures were observed in this run; that does not guarantee every possible in-flight request survives deletion.

**Improvement:** To improve resilience against this failure, I would add graceful connection draining and a PodDisruptionBudget for planned disruptions (a PDB does not prevent a direct pod deletion).

## Experiment 2 — Payment latency

**Hypothesis:** See hypothesis 2 above, recorded before injection.

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=90s
python3 loadgen/lab8-observe.py payments-2000ms 90
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
kubectl rollout status deployment/payments --timeout=90s
python3 loadgen/lab8-observe.py payments-6000ms 60
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
kubectl rollout status deployment/payments --timeout=90s
```

2000ms injection started at 22:13:20. Snapshot at 22:15:18:

```text
RPS: 2.39887; user 5xx ratio: 0; 3-minute 5xx increase: 0
p99 /events: 0.21329s
p99 /events/{id}/reserve: 0.44212s
p99 /reserve/{id}/pay: 2.485s
22:14:28.205 reserve: HTTP 200, 0.051497s
22:14:30.498 pay: HTTP 200, 2.021204s, confirmed order
```

6000ms injection started at 22:15:34. During rollout one controlled payment returned 502 in 0.018201s; this transient is recorded separately, not presented as a timeout measurement. Stable mixedload requests then showed:

```text
22:15:59 pay 504 4.929139
22:16:04 pay 504 4.919382
22:16:09 pay 504 4.914563
22:16:58 snapshot: RPS 1.04925, user 5xx ratio 0.291709
p99 seconds: reads 0.22007, reserve 0.23500, pay 7.44452
```

**Comparison:** At 2 seconds, payments remained successful and only pay latency increased. At 6 seconds, the gateway bounded observed request duration near five seconds. The histogram's interpolated p99 exceeds the individual ~4.92s samples; it must not be interpreted as their exact duration. Reads and reservations stayed fast. After restoration, by 22:18:51 gateway `/health` was HTTP 200 healthy; at 22:18:51 the user-path 1-minute error ratio was 0.

**Improvement:** To improve resilience against this failure, I would add a pay-latency SLO alert, because successful but slow payments are invisible to an error-only alert.

## Experiment 3 — Redis outage, before fix

**Hypothesis:** See hypothesis 3 above, recorded before injection.

```bash
kubectl scale deployment/redis --replicas=0
python3 loadgen/lab8-observe.py redis-before-fix 90
kubectl get pods -l app=events
kubectl get endpointslices -l kubernetes.io/service-name=events
# curl was executed inside the existing mixedload pod:
kubectl exec deployment/mixedload -- curl -s -w '\n%{http_code} %{time_total}' http://gateway:8080/events
```

Redis was scaled down at 22:18:51. Actual HTTP and Kubernetes evidence:

```text
22:18:58 GET /events: 200, 1.343286s (early in outage)
22:19:03 POST reserve: 504, 4.922854s, Events service timeout
22:19:04 gateway /health: 503, events degraded, payments ok
22:19:05 events pod /health: 503, postgres ok, redis down
22:19:13 GET /events: 502, 0.086254s, Events service unavailable
22:19:13 POST reserve: 502, 0.023992s
events endpoint conditions: ready=false, serving=false, terminating=false
kubelet: Container events failed liveness probe, will be restarted
22:20:00 events: 0/1 Ready, restart count 1
22:20:06 user 5xx ratio: 1; read HTTP 200 rate: 0
```

**Comparison:** Reservations failed as expected, but Redis-independent reads were also removed from service. Dependency-aware readiness amplified the incident; dependency-aware liveness restarted a process that could not repair Redis. The initial reserve p99 rose above six seconds in bucket interpolation, then failed-fast 502 responses reduced latency without restoring availability. Pay traffic disappeared after reserve failures; a NaN pay p99 means no observations, not healthy checkout.

**Improvement:** To improve resilience against this failure, I would separate process probes from dependency diagnostics so Redis failure cannot remove the PostgreSQL-only read path.

Recovery commands: scale Redis back to one, wait for its rollout, and restart events after Redis is available. The events client is initialized on startup; restarting during Redis loss can leave its Redis client unset, so simply restoring Redis is insufficient to prove full checkout recovery. A successful reserve-and-pay request and a fresh zero-error rate window were checked before the combined experiment.

## Task 2 — Combined Redis loss and slow payments

**Design:** The plan above was recorded before injection. Keep the same two mixedload replicas; inject 2000ms payment latency, create a hold while Redis is still available, then scale Redis to zero for a three-minute observation window. This tests both dependency failure and a slow downstream path without changing load or DB limits.

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=90s
# Reserve once while Redis is available; save the returned reservation_id.
kubectl scale deployment/redis --replicas=0
python3 loadgen/lab8-observe.py combined 180
# Also POST /reserve/<saved-id>/pay from the mixedload pod.
```

The preceding recovery snapshot at 22:22:20 had user error ratio 0. The combined window began at 22:23:05.682. Samples from the actual Prometheus responses (p99 in seconds):

| UTC | Total RPS | User 5xx ratio | Read p99 | Reserve p99 | Pay p99 |
|---|---:|---:|---:|---:|---:|
| 22:23:05 | 4.9203 | 0 | 2.6657 | 2.8512 | 2.0616 |
| 22:23:22 | 2.8860 | 0.0990 | 3.6818 | 6.7252 | 7.0987 |
| 22:23:37 | 2.7903 | 0.5369 | 3.4278 | 6.7100 | 7.2917 |
| 22:24:07 | 4.0851 | 0.9912 | 2.3712 | 6.1311 | 7.4750 |
| 22:24:22 | 4.2470 | 1 | 3.5623 | 4.0223 | NaN |
| 22:25:37 | 4.3570 | 1 | 4.2501 | 4.5006 | NaN |
| 22:26:05 | 4.4559 | 1 | 4.1994 | 2.3670 | NaN |

The final sample was at 22:26:05.684, 180.002 seconds after the initial sample. Redis remained absent and payment latency stayed 2000ms throughout this window; recovery began afterwards.

Controlled payment evidence for the hold created at 22:23:05:

```text
reservation_id: f900b7ff-cffc-48ae-9213-1a4916cdfc55
22:23:13.645 payments log: Injecting 2000ms latency
22:23:15.646 payments log: Payment success: PAY-3B04062E
22:23:20.526 gateway pay response: HTTP 500, 6.937198s
detail: Payment succeeded but confirmation failed — contact support
22:23:21 GET /events: 502, 0.022758s
22:23:21 reserve: 502, 0.017331s
22:23:21 gateway /health: 503, events down, payments ok
22:23:21 direct events /health: 503, postgres ok, redis down
22:24:45 events pod: 0/1 Ready, 2 restarts
```

**Golden signals:** Payment latency was affected first by the staged 2000ms injection. After Redis shutdown, the first 15-second sample showed both growing errors and reserve/pay latency; this sampling interval cannot establish their sub-second ordering. Later traffic shifted toward fast failed reads/reservations and checkout traffic stopped. Lower latency during failed-fast responses was not recovery. The worst observed histogram amplification was pay (~7.475s), supported by the controlled 6.937s request: payment latency plus a failed confirmation. Reserve p99 also exceeded six seconds during the transition. No pay samples later means NaN, not zero latency.

CPU/memory spot checks at 22:23:21 showed events 139m CPU / 102Mi against limits 200m / 256Mi, and gateway pods 31–66m CPU / 86–90Mi against 200m / 256Mi limits. Later events metrics were temporarily unavailable during restart. These snapshots do not prove absence of throttling, connection-pool pressure or saturation; no dedicated saturation time-series was collected.

**Weakest link:** Redis-dependent health checks coupled the whole events Service to Redis. Losing Redis therefore broke unrelated reads and made paid orders fail confirmation. I would separate process/read readiness from dependency diagnostics, fail closed when a hold cannot be stored, and use idempotent payment/reconciliation for charge-success/confirm-failure. The bonus below addresses only probe-driven read outage, not payment consistency or all Redis failure modes.

## Bonus — Separate process probes from dependency health

**Chosen weakness:** Experiment 3 demonstrated that a Redis outage removed the Redis-independent list path from service and restarted events unnecessarily.

**Implemented change:** In `k8s/events.yaml`, both events readiness and liveness now use `/metrics` instead of `/health`. `/metrics` checks that the HTTP process responds without making a Redis or PostgreSQL request. `/health` still reports dependency health and is still used by gateway diagnostics. Probe timings, resources, images, Redis timeout, DB pool limit and application code are unchanged.

```diff
 readinessProbe:
   httpGet:
-    path: /health
+    path: /metrics
 livenessProbe:
   httpGet:
-    path: /health
+    path: /metrics
```

The same two path changes were applied to the running events Deployment with `kubectl patch`, preserving its local cached-image override. Redis was available during this rollout, so the Redis client initialized normally. At 22:28:24 a stable control request returned reserve HTTP 200 in 1.576001s and confirmed pay HTTP 200 in 0.126052s. An earlier rollout-transition reserve returned 502 and was not treated as a successful control.

**Re-test:** Repeat the same Redis scale-to-zero experiment for 90 seconds, with two mixedload replicas, event 6, payments latency 0, failure rate 0.0, DB_MAX_CONNS 10 and gateway timeout 5000ms. Check client responses, endpoint conditions and restart count as well as Prometheus.

```bash
kubectl scale deployment/redis --replicas=0
python3 loadgen/lab8-observe.py redis-after-fix 90
kubectl get pods -l app=events
kubectl get endpointslices -l kubernetes.io/service-name=events
kubectl exec deployment/mixedload -- curl -s -w '\n%{http_code} %{time_total}' http://gateway:8080/events
kubectl scale deployment/redis --replicas=1
kubectl rollout status deployment/redis --timeout=60s
```

**Tradeoff:** Ready now means the process can accept requests, not that every dependency or checkout path is healthy; dependency failures require separate `/health` monitoring, and a dedicated read-readiness endpoint would be preferable for production. This is a read-availability fix, not a complete reservation-safety fix: the existing startup path can leave `redis_client=None`, and reservation code can then return a reservation without storing a hold. Fail-closed reservation handling and Redis-client reconnection remain separate follow-up work. The re-test starts with an initialized client and does not force a new events pod to start while Redis is absent.

The pre-injection snapshot at 22:28:57 had user 5xx ratio 0. Redis was removed again at about 22:29:00. The observer ran from 22:29:00.141 through 22:30:30.145, for 90.004 seconds.

| Measurement | Before: 22:20:21 | After: 22:30:30 |
|---|---|---|
| GET /events successful fraction in final 1-minute rate window | 0% | 100% |
| Events endpoint Ready / serving | false / false | true / true |
| Events pod readiness | 0/1 | 1/1 |
| Events restarts observed during outage | At least 1 | 0 |
| User-path 5xx ratio | 1.0000 | 0.510782 |
| Read p99 (seconds) | 3.474998, failed reads | 0.473954, successful reads |
| Successful reservation rate | 0 | 0 |

Exact Prometheus status-rate values from the final after-fix sample:

```text
/events 200: 0.38641098075384017 requests/s
/events 502: 0
/events 504: 0
/events/{id}/reserve 200: 0
/events/{id}/reserve 504: 0.40344345653032515 requests/s
user_error_ratio: 0.510782034620866
p99 /events: 0.4739539678422874
p99 reserve: 7.454051763179266
p99 pay: NaN
```

Direct probes independently confirmed the difference:

```text
22:29:10 GET /events: 200, 0.569943s
22:29:15 reserve: 504, 4.930647s
22:29:43 GET /events: 200, 0.203014s
22:29:48 reserve: 504, 4.926920s
22:29:51 gateway /health: 503, events down, payments ok
22:29:51 direct events /health: 503, postgres ok, redis down
22:30:38 events-558d8b4c97-4pb7p: 1/1 Ready, 0 restarts
```

One earlier direct health response at 22:29:19 incorrectly reported redis ok during the outage. The current cached Redis check can expose a stale success while another check is in flight; later direct checks reported redis down. This diagnostic limitation was observed, not fixed or hidden by the probe change.

**Result:** The fix preserved reads and stopped dependency-triggered restarts in the same outage. It did not repair Redis or make checkout successful. Aggregate error ratio and throughput are affected by changed traffic composition (failed reservation stops pay), so read success and endpoint/restart evidence are the primary comparison, not a claim of a universal 49% reliability improvement.

Mixedload logs contained 35 read HTTP 200 responses in the re-test window (22:29:00–22:30:30), and no failed reads.

## Evidence and cleanup

`submissions/lab8-evidence.jsonl` preserves 37 timestamped Prometheus snapshots with original result arrays, labels, sample timestamps and value strings. It includes the baseline, pod-kill samples before the next injection, final payment samples, all Redis and combined-scenario samples, recovery snapshots and the final restored state. `NaN` is retained where no pay observations exist. Long transition logs are summarized above; they are not fabricated successful results.

Redis was restored after the 90-second re-test, and events recovered **without restarting**. At 22:32:15 the fresh user-path 5xx ratio was 0 and total RPS was 6.74565. Final HTTP checks:

```text
22:32:16 GET /events: 200, 0.028060s
22:32:18 gateway /health: 200 healthy, events ok, payments ok
22:32:19 direct events /health: 200 healthy, postgres ok, redis ok
22:32:19 reserve: 200, 0.258954s
22:32:20 pay: 200, 1.044891s, confirmed order
events: 1/1 Ready, restart count 0 throughout the re-test and recovery
```

```bash
kubectl delete -f loadgen/lab8-mixedload.yaml
kubectl get deployment events payments redis postgres
kubectl get rollouts gateway
kubectl get deployment -n monitoring prometheus grafana-lab7
```

Final state: mixedload deleted; events, payments, Redis and PostgreSQL each 1/1 Ready; gateway 5/5 available, Healthy; Prometheus and Grafana each 1/1 Ready. PAYMENT_LATENCY_MS=0, PAYMENT_FAILURE_RATE=0.0, DB_MAX_CONNS=10. The dedicated test event and its local test-order history remain in the database; original demo events were not consumed. The probe fix remains applied for later labs.

ArgoCD auto-sync remains disabled as in Lab 7: the fork's current main still contains older desired state. Do not enable it until the intended Lab 7/8 manifests are merged into the fork's main. Local cached-image overrides for events/payments remain IfNotPresent; their source manifests retain Always.

Validation passed: server-side dry-run of both changed Kubernetes manifests, Python observer syntax check, observer execution against live Prometheus, and `git diff --check`. Moodle submission is a separate final step.

## Submission checklist

- [x] Task 1: three experiments with hypotheses recorded before injection, commands, timestamped evidence, comparisons and improvement suggestions.
- [x] Task 2: two simultaneous failures, three-minute observation window and weakest-link analysis.
- [x] Bonus: implemented probe fix, same experiment repeated, before/after proof and explicit tradeoffs.
- [x] Faults restored and mixedload removed; Rollout and monitoring left running.
