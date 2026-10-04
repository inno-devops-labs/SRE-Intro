# Lab 8 — Chaos Engineering

## Environment and baseline

Measurements below use the local k3d QuickTicket cluster, five gateway replicas, in-cluster Prometheus, and the Lab 8 mixedload deployment. Unless specified otherwise, abbreviated times refer to **2026-10-05, UTC+03:00**.

Before the experiments, `kubectl get rollout gateway` showed 5 desired and 5 available replicas; `kubectl get deployment mixedload` showed 2/2. At 01:20:42, a `/health` probe returned HTTP 200 with `events: ok` and `payments: ok`. At **01:20:32**, the Prometheus queries `sum(rate(gateway_requests_total[1m]))` and `sum(rate(gateway_requests_total{status=~"5.."}[1m])) / sum(rate(gateway_requests_total[1m]))` returned **12.6274 RPS** and **0**, respectively. The gateway p99 query `histogram_quantile(0.99, sum by (le,path) (rate(gateway_request_duration_seconds_bucket[1m])))` returned `/events` 0.0248 s, `/events/{id}/reserve` 0.0960 s, and `/reserve/{id}/pay` 0.0729 s.

An important workload check after Experiment 1 found that event 1 had 50 confirmed orders and `event:1:held=50`; a direct reserve attempt returned HTTP 409, `Not enough tickets (available: 0)`. Thus the original mixedload could not reliably reach `/pay`. I inserted a dedicated event 6 with 100,000 tickets and changed the Lab 8 mixedload manifest to reserve that event during this run. A direct reserve then returned a reservation ID, and the test event had 57 confirmed orders by 01:22:46. This setup change was made **after** Experiment 1 and before Experiment 2. The committed manifest makes `EVENT_ID` configurable and defaults to the original event 1. For a replay, create the test event below, apply the manifest, then run `kubectl set env deployment/mixedload EVENT_ID=6` before collecting a baseline. The test orders and event are removed in cleanup.

```bash
kubectl exec deployment/postgres -- psql -U quickticket -d quickticket -c \
  "INSERT INTO events (id,name,venue,event_date,total_tickets,price_cents) VALUES (6,'Lab 8 Load Event','Test Venue','2026-12-01T12:00:00Z',100000,100)"
kubectl apply -f labs/lab8/mixedload.yaml
kubectl set env deployment/mixedload EVENT_ID=6
kubectl rollout status deployment/mixedload --timeout=90s
```

## Experiment 1 — Pod kill under load

### Hypothesis (recorded before injection)

If one gateway pod is deleted under traffic, the remaining four ready endpoints will continue serving requests with few or no 5xx errors, and the Rollout controller will replace the pod. Per-pod traffic should shift to the survivors during replacement.

### Method and observations

At **2026-10-05T01:20:54+03:00**, I ran `kubectl delete pod gateway-585c8c7867-289b5 --wait=false` while mixedload was 2/2. `kubectl get pods -l app=gateway -w --output-watch-events` showed the victim terminating and `gateway-585c8c7867-dsxhd` entering `ContainerCreating` immediately. `kubectl wait --for=condition=Ready pod/gateway-585c8c7867-dsxhd --timeout=90s` completed by **01:21:03+03:00**, approximately **9 seconds** after deletion. `kubectl get pods -l app=gateway` then showed five Running, Ready pods.

At 01:20:32, `sum by (pod) (rate(gateway_requests_total[1m]))` returned **2.37–2.74 RPS** for each of the original five pods. At 01:21:09, the four surviving pods returned **2.60–2.85 RPS** and the new pod **0.25 RPS**. The deleted pod still appeared at **1.85 RPS** because the one-minute rate window included pre-deletion samples; this is not live traffic to a terminated pod. Aggregate RPS was **12.99**. The one-minute 5xx ratio was **0.00140**; `sum by (path,status) (rate(gateway_requests_total{status=~"5.."}[1m]))` attributed the sampled 5xx solely to `/health` (503 at 0.0182 RPS), not to `/events` or checkout paths. The three-minute `increase` query returned 3.08, but its window started before pod deletion, so it cannot by itself count failures caused by this experiment.

### Hypothesis vs reality

Replacement and traffic redistribution matched the hypothesis. There was a small sampled 503 rate on `/health`, so the strict prediction of zero 5xx was not met. The available metrics do not establish that the pod deletion caused those health responses; no 5xx was observed on the customer paths in the sampled one-minute window. Pod creation was nearly immediate and readiness returned in about nine seconds.

### Resilience improvement

To improve resilience against a pod loss, I would add a PodDisruptionBudget and spread gateway replicas across nodes when the cluster has more than one node, so maintenance and node failures are less likely to remove several endpoints together.

## Experiment 2 — Payment latency injection

### Hypothesis (recorded before injection)

If each payment request gains 2000 ms of latency, `/pay` p99 should rise substantially while `/events` and reservation latency stay near baseline. Gateway 5xx should remain low because this is below its 5000 ms downstream timeout. At 6000 ms, `/pay` should instead time out around 5000 ms.

### Method and observations

At **01:22:46+03:00**, after checkout traffic was restored, the one-minute gateway 5xx ratio was **0**. The p99 values were `/events` **0.0553 s**, `/events/{id}/reserve` **0.0947 s**, and `/reserve/{id}/pay` **0.0641 s**. The two mixedload pods were Ready.

At **01:22:58+03:00**, I ran `kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000` and `kubectl rollout status deployment/payments --timeout=60s`. At **01:24:44+03:00**, after the rate window filled, the same Prometheus p99 query returned `/events` **0.1855 s**, `/events/{id}/reserve` **0.0711 s**, and `/reserve/{id}/pay` **2.4850 s**. The one-minute 5xx ratio was **0**. Aggregate RPS fell from **15.51** before injection to **3.96** while the two mixedload clients waited on slow checkout calls. `GET /api/v1/rules?type=alert` returned an empty `groups` array: no in-cluster alert rule detected the latency-only degradation.

At **01:24:54+03:00**, I raised the injected delay with `kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000`, followed by `kubectl rollout status deployment/payments --timeout=60s`.

At **01:25:43+03:00**, the one-minute 5xx ratio was **0.0614** and the path/status query showed `/reserve/{id}/pay` HTTP 504 at **0.1761 RPS**. A direct in-cluster reserve-then-pay probe returned **HTTP 504 in 5.061735 s**. The histogram p99 estimate for `/reserve/{id}/pay` was **7.45 s**; this is bucket interpolation from the default Prometheus histogram, whereas the direct HTTP probe shows the timeout close to five seconds.

I restored with `kubectl set env deployment/payments PAYMENT_LATENCY_MS=0` at **01:25:58+03:00** and waited for `kubectl rollout status deployment/payments --timeout=60s`. The immediately following `/health` probe was HTTP 503 (`payments: down`) during the rollout transition, so recovery was checked again after the old pod terminated.

### Hypothesis vs reality

The 2000 ms delay produced partial degradation: payment p99 rose from 0.0641 s to 2.4850 s with zero sampled 5xx at 01:24:44, while the read and reserve paths remained far faster. The higher delay produced an actual 504 at about five seconds, as predicted. The gateway p99 histogram overestimated the direct timeout because its coarse buckets interpolate within the 5–10 s bucket. A further effect was reduced throughput from the serial mixedload loops.

### Resilience improvement

To improve resilience against slow but successful payments, I would alert on sustained `/pay` latency, not only the aggregate 5xx ratio, so partial degradation becomes visible before requests begin timing out.

## Experiment 3 — Redis failure

### Hypothesis (recorded before injection)

If Redis becomes unavailable, `/events` should still work because event listing uses the database, while reservations should fail because their holds require Redis. The gateway `/health` response should show a degraded dependency.

### Method and observations

Before injection, at **01:27:27+03:00**, `/health` returned HTTP 200, the one-minute gateway 5xx ratio was **0**, and p99 was `/events` **0.0244 s**, `/events/{id}/reserve` **0.0707 s**, `/reserve/{id}/pay` **0.0710 s**. I ran `kubectl scale deployment/redis --replicas=0` at **01:27:39+03:00** and `kubectl wait --for=delete pod -l app=redis --timeout=60s`; Redis had no remaining pod.

An in-cluster probe at **01:27:53+03:00** used these requests from `kubectl exec deployment/mixedload -- sh -c '...'`:

```bash
curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' http://gateway:8080/events
curl -s -o /tmp/probe-body -w '%{http_code} %{time_total}s\n' \
  -X POST -H 'Content-Type: application/json' -d '{"quantity":1}' \
  http://gateway:8080/events/6/reserve
curl -s -w '\nHTTP %{http_code}\n' http://gateway:8080/health
```

It found `GET /events` **HTTP 200 in 0.0221 s**, `POST /events/6/reserve` **HTTP 504 in 5.0913 s** (`Events service timeout`), and `GET /health` **HTTP 503**, reporting `events: down`. At 01:28:01, the one-minute 5xx ratio was **0.0278**; `/events/{id}/reserve` p99 was **6.63 s** (histogram estimate), and the path/status query showed reservation HTTP 504 at **0.0442 RPS**. `kubectl get pods -l app=events` showed **0/1 Ready**.

At **01:28:11+03:00**, a second probe returned curl status **000** for `/events`, reservation, and `/health` because connection to the gateway Service was refused. `kubectl get endpoints gateway events` showed **no endpoints for either Service**. `kubectl get pods -l app=gateway` showed **all five 0/1 Ready**, with one restart each. The Redis dependency's health failure propagated through events health to gateway readiness/liveness, turning a reservation failure into a complete public endpoint outage.

I restored with `kubectl scale deployment/redis --replicas=1` at **01:28:32+03:00**, then waited for Redis availability and for events and all gateway pods to become Ready. At **01:29:10+03:00**, `kubectl get endpoints gateway events` again showed gateway and events addresses; `/events` returned HTTP 200 and gateway `/health` returned HTTP 200 with healthy dependencies.

### Hypothesis vs reality

The first probe partly matched the hypothesis: event listing remained available briefly, reservation timed out, and health degraded. The later loss of every gateway endpoint disproved the expectation that reads would remain available throughout the Redis outage. The unexpected mechanism was health-check coupling: events' Redis health made events unready, then gateway health made every gateway pod unready and triggered restarts.

### Resilience improvement

To improve resilience against Redis failure, I would separate liveness from dependency health and keep the gateway read path ready while Redis-dependent checkout is degraded. A liveness probe should show whether a process needs restarting; it should not restart healthy processes merely because a downstream dependency is unavailable.

## Task 2 — Combined failure scenario

### Scenario and hypothesis (recorded before injection)

Inject a 30% payment failure probability and 500 ms payment delay, reduce the events database connection limit to three, and raise mixedload to three replicas. I expect payment errors to appear quickly and reservation latency to increase if the smaller database pool queues requests. The measured first signal and weakest component will be determined from the time series.

### Method and observations

At **01:30:26+03:00**, before injection, the one-minute 5xx ratio was **0**, aggregate RPS **18.05**, and p99 was `/events` **0.0230 s**, `/events/{id}/reserve` **0.0747 s**, `/reserve/{id}/pay` **0.0749 s**. At **01:30:37+03:00**, I ran:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
kubectl rollout status deployment/payments --timeout=90s
kubectl rollout status deployment/events --timeout=90s
```

Both updated deployments became available, and mixedload was 3/3. Repeated measurements use the same one-minute Prometheus error ratio and p99 queries from Experiment 2. Values are seconds for p99; early samples include pre-injection traffic in their one-minute rate windows.

| Timestamp (+03:00) | 5xx ratio | RPS | `/events` p99 | `/reserve` p99 | `/pay` p99 | Observation |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 01:30:53 | 0.00165 | 18.23 | 0.0403 | 0.1552 | 0.6644 | First sampled error was reserve 502 (0.0301 RPS); `/pay` latency already rose. |
| 01:31:39 | 0.07066 | 13.01 | 0.0685 | 0.1683 | 0.7468 | `/pay` 500 at 0.9013 RPS; reserve 502 at 0.0181 RPS. |
| 01:32:38 | 0.09285 | 11.94 | 0.0230 | 0.0241 | 0.7475 | `/pay` 500 at 1.1090 RPS; no sampled events or reserve 5xx. |
| 01:33:44 | 0.07887 | 12.22 | 0.0510 | 0.0260 | 0.7487 | `/pay` 500 at 0.9636 RPS; other paths had no sampled 5xx. |
| 01:34:48 | 0.09299 | 11.93 | 0.0760 | 0.0766 | 0.7475 | `/pay` 500 at 1.1091 RPS; other paths had no sampled 5xx. |

The observation window ran from **01:30:37 to 01:34:48+03:00** (4 min 11 s). I restored with `kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0`, `kubectl set env deployment/events DB_MAX_CONNS=10`, and `kubectl scale deployment/mixedload --replicas=2` at **01:34:58+03:00**. The payments and events rollouts completed, mixedload was 2/2, and gateway remained 5/5 available.

### Golden signals and weakest link

The first sample at 01:30:53 already contained both a latency rise (`/pay` p99 0.0749 → 0.6644 s) and a small reserve 502 rate. The 60-second rate windows cannot establish which changed first within the initial 16 seconds; **latency and errors were first observed in the same sample**. By the next sample, the dominant error signal was `/pay` 500. Throughput fell from 18.05 to about 12 RPS as serial clients waited for payments.

Payments was the weakest link in this run: its path stayed near **0.75 s p99**, about ten times the 0.0749 s baseline, and generated **0.96–1.11 HTTP 500/s** in later samples. `/events` and reservation p99 remained mostly below 0.08 s after rollout settled, and their sampled 5xx rates were zero. The events pool limit of three did not produce sustained latency amplification at this load. The early reserve 502 appeared during the deployment transition, not throughout the steady observation period.

### Resilience improvement

I would add payment-specific latency and error alerts and reduce synchronous checkout blocking. For the immediate improvement below, I address the detection gap for slow but successful payment requests.

## Bonus — Resilience improvement

### Weakness, change, and before/after evidence

The 2000 ms payment experiment exposed slow but successful checkout: `/pay` p99 reached **2.4850 s** at 01:24:44 while the one-minute 5xx ratio was **0**, and Prometheus `/api/v1/rules?type=alert` returned `groups: []`. There was no in-cluster alert for this failure mode.

I added `GatewayPaymentP99LatencyHigh` to `labs/lab7/prometheus.yaml`, with `rule_files` loading `/etc/prometheus/alerts.yml`. Its expression is:

```promql
histogram_quantile(0.99, sum by (le) (rate(gateway_request_duration_seconds_bucket{path="/reserve/{id}/pay"}[1m]))) > 1
```

The rule requires 30 seconds above 1 second and evaluates every 5 seconds. This threshold is well above the observed healthy `/pay` p99 (0.064–0.075 s) and below the measured degraded p99 (~2.48 s). I applied the manifest, restarted Prometheus to load the new ConfigMap, and verified `promtool check config` and `promtool check rules`: both succeeded, with one rule found. At **01:36:21+03:00**, the Prometheus rules API showed the rule as **inactive** and `/pay` p99 **0.0714 s**.

I then repeated the **same** `PAYMENT_LATENCY_MS=2000` injection at **01:36:29+03:00** under two mixedload replicas. At **01:37:34+03:00**, `/pay` p99 was **2.4805 s** and the rule state was **firing**; Prometheus reported `activeAt=2026-10-04T22:36:45Z` (01:36:45+03:00). At 01:37:42, `/pay` p99 was **2.485 s**, the rule remained **firing**, and `/pay` HTTP 500 rate was **0**. The aggregate 5xx ratio of 0.00461 at that moment came from `/health` 503 at 0.0182 RPS, not payment failures. The fix detected a slow checkout that an error-only signal would miss. It did not reduce latency itself.

I restored with `kubectl set env deployment/payments PAYMENT_LATENCY_MS=0` at **01:37:54+03:00** and verified gateway `/health` returned HTTP 200.

At **01:39:20+03:00**, the rule was back to **inactive**, the one-minute 5xx ratio was **0**, and `/pay` p99 had fallen below the 1-second alert threshold (0.5250 s in that sampled window).

### Trade-off

The one-minute p99 window plus 30-second `for` period introduces a detection delay, and low-volume payment traffic could make histogram p99 noisy. The threshold should be tuned against normal production traffic. This deployment has Prometheus rule evaluation but no Alertmanager notification route, so `firing` is visible in Prometheus but does not page an operator.

## Cleanup

At **01:39:38+03:00**, I ran `kubectl delete -f labs/lab8/mixedload.yaml` and waited until both mixedload pods were deleted. I removed only test-event data with a database transaction: **3,311** orders with `event_id=6` and the single event 6 row. I deleted `event:6:held` and checked for unexpired reservation keys belonging to event 6 (none remained). Final database checks returned zero event 6 rows and zero event 6 orders; the original event 1 still had **50** confirmed orders. Redis, events and payments were 1/1 available, and gateway was 5/5 available. The Prometheus latency rule remains installed for subsequent labs.
