# Lab 8 — Chaos Engineering and Resilience

Date: 2026-10-05. Author: Walkerino. Timestamps in evidence are UTC (Moscow
is UTC+3). Environment: isolated `k3d-quickticket-lab78`, Kubernetes
v1.33.6-k3s1; healthy gateway Rollout with five replicas, in-cluster
Prometheus and Grafana from Lab 7. ArgoCD self-heal is absent in this experiment
cluster so it cannot revert injected failures. This PR includes prerequisites
for independent checkout; the Lab 7 submission is in its separate PR.

## Method and baseline

[run-lab8.py](../scripts/run-lab8.py) saves each hypothesis with a timestamp
**before** injecting its fault. It keeps two mixedload replicas active, waits
90s before measurement, and restores health plus a fresh 1m metrics window
between experiments. Commands and real output: [session.txt](evidence/lab8/session.txt).

The load generator derives from `labs/lab8/mixedload.yaml`, pins the actually
used curl image by digest, bounds requests to 12s, and logs HTTP status/duration.
It performs read → reserve → pay against dedicated event **7808**, with
1,000,000 zero-cost synthetic tickets, avoiding the default event's 100-ticket
stock limit. [Seed SQL](../scripts/lab8-seed.sql), [mixedload manifest](../k8s/mixedload.yaml).
Probe reservations use event 1; no real payments or users are involved.

Prometheus snapshots include:

```promql
sum(rate(gateway_requests_total[1m]))
(sum(rate(gateway_requests_total{status=~"5.."}[1m])) or vector(0))
  / sum(rate(gateway_requests_total[1m]))
sum by(pod)(rate(gateway_requests_total[1m]))
histogram_quantile(0.99,
  sum by(le,path)(rate(gateway_request_duration_seconds_bucket[1m])))
sum(increase(gateway_requests_total{status=~"5.."}[3m]))
```

Metrics count health probes and are estimates over scrape windows. Histogram
p99 is interpolated from buckets, not an exact request duration. The 3m
increase can retain an earlier incident after the 1m error ratio recovers.
Client-side network failures can occur before HTTP instrumentation records a
response, so [client load logs](evidence/lab8/client-load.jsonl) and direct
HTTP probes accompany server metrics. Invalid/empty Prometheus data is not
converted into successful operation.

## Task 1 — Three experiments

### 1. Pod kill under load

Hypothesis: deleting one of five gateway pods should leave four endpoints
serving and trigger a replacement within seconds; in-flight connections may
fail. [Hypothesis written before injection](evidence/lab8/pod-kill-hypothesis.json).

```bash
kubectl get pods -l app=gateway -o json
kubectl delete pod <recorded-victim> --wait=false
kubectl get pods -l app=gateway -o json
```

At **08:20:01 UTC**, baseline traffic was **16.891 RPS**, 1m error
ratio **0**, and direct reads/reservation/payment/health all returned 200.
The baseline 3m 5xx increase was **87.36**, retained from the deliberately bad
Lab 7 canary; it was not a new baseline error. [Baseline](evidence/lab8/baseline.json).

The victim was `gateway-5b45bc8f9f-9wnxp`. Replacement was observed after
**0.163s**; all five non-terminating pods were Running/Ready after **6.478s**.
[Measured timing](evidence/lab8/pod-kill-timing.json). These durations include
CLI polling overhead; they bound observation rather than exact scheduler events.

At **08:21:24 UTC**, traffic was **16.891 RPS**, error ratio **0**, and
`sum(increase(...5xx...[3m]))` was empty because no current error series
remained. All HTTP probes returned 200. The replacement pod served **3.127 RPS**
and four survivors approximately **3.38–3.49 RPS** each.
[Snapshot](evidence/lab8/pod-kill.json), [5s per-pod rate history](evidence/lab8/pod-kill-rate-range.json).
The victim's old series became absent rather than necessarily a literal zero;
new samples appeared for the replacement. No `HTTP 000` transport failures
were seen in the collected client windows around this transition, but this
low-load sample does not prove that no in-flight request can ever be lost.

The availability hypothesis held: four endpoints continued serving while
Kubernetes recreated the fifth. I would improve resilience by adding graceful
termination/draining and a PodDisruptionBudget for voluntary disruptions;
a PDB would not prevent this direct pod deletion.

### 2. Payment latency

Hypothesis: payments latency of 2000ms remains below the gateway's 5000ms
timeout: pay p99 rises, reads stay fast, and successful slow payments produce
no 5xx. [Pre-injection hypothesis](evidence/lab8/latency-hypothesis.json).

```bash
kubectl set env deploy/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deploy/payments --timeout=120s
# Wait 90s, query per-path p99 and 5xx ratio.
kubectl set env deploy/payments PAYMENT_LATENCY_MS=6000
kubectl rollout status deploy/payments --timeout=120s
# Wait 90s and repeat: timeout protection should return 504 around 5s.
kubectl set env deploy/payments PAYMENT_LATENCY_MS=0
```

At **08:24:23 UTC**, the 2000ms experiment had **0** 5xx ratio.
Pay p99 increased from **0.070s** to **2.485s**; read p99 stayed **0.023s**
and reserve p99 **0.025s**. Direct pay returned **200 in 2.019s**, reads
200 in 0.014s, and health remained 200. [Raw metrics/probes](evidence/lab8/latency-2000.json).
The p99 bucket estimate exceeds the direct duration, as expected from
histogram interpolation. The successful-slow-payment hypothesis held.

RPS decreased from **16.891** to **2.582** because two sequential loadgen
workers wait for pay before starting the next loop. This is a closed workload,
so its demand falls as latency rises; it does not demonstrate capacity under
fixed incoming RPS.

At **08:26:03 UTC**, 6000ms latency caused **504 in 5.012s** on pay,
with `{"detail":"Payment service timeout"}`. Read and reserve still returned
200 in 0.014s and 0.005s; `/health` remained 200. Gateway 5xx ratio was
**31.68%**, RPS **1.064**, and pay histogram p99 **7.475s**.
[Raw metrics/probes](evidence/lab8/latency-6000.json). The 7.475s quantile
interpolates the broad 5–7.5s bucket and must not be read as a measured
7.475s timeout. The direct response verifies the 5s protection boundary.
Read p99 remained **0.024s**. Latency was restored to zero before Redis
injection and a fresh healthy rate window was allowed to accumulate.

I would improve resilience by adding a payment-path latency SLO alert and
idempotent payment handling: an HTTP timeout does not prove that a downstream
charge never completed, so blindly retrying it can duplicate side effects.

### 3. Redis failure

Hypothesis: Redis outage should break reservations and health while DB-only
event lists remain functional. Dependency-based events readiness may instead
remove the entire events Service. [Pre-injection hypothesis](evidence/lab8/redis-hypothesis.json).

```bash
kubectl scale deploy/redis --replicas=0
# Wait 90s; probe /events, /events/1/reserve, /health; query Prometheus.
kubectl scale deploy/redis --replicas=1
kubectl rollout status deploy/redis --timeout=120s
```

At **08:29:00 UTC**, 1m 5xx ratio reached **1.0 (100%)** and measured
traffic fell to **4.128 RPS**. `/events` returned **504 after 5.016s**,
reserve returned **502**, and health returned **503**, with events down and
payments ok. [Raw snapshot](evidence/lab8/redis-before-fix.json).
The events pod was Running but not Ready; [endpoints](evidence/lab8/redis-before-endpoints.yaml)
show no ready events backend while gateway backends remained present.
Some unavailable-Service connections timed out rather than failing immediately.

Read histogram p99 was **2.487s**, reserve p99 **6.775s**, and pay quantile
was **NaN** because no completed checkout requests populated the current
window. NaN is absence of measurable traffic, not low latency or success.

The reservation/health part of the hypothesis held, but user-facing event
listing did **not** survive: dependency readiness converted a partial failure
into a read outage. After Redis was restored, direct events listing and
health again returned 200. [Recovery probe](evidence/lab8/redis-after-recovery-direct-events.json).
I would improve resilience by gating events readiness on PostgreSQL for its
available read paths while retaining Redis failures in `/health` and booking
SLIs; the bonus implements and tests that change.

## Task 2 — Combined failure

Scenario: **Redis down + payments latency 2000ms**, measured over a three-minute
window. This tests independent degradation in reservation and payment paths
and whether readiness turns partial failure into a full read outage.
[Pre-injection hypothesis](evidence/lab8/combined-hypothesis.json).

```bash
kubectl set env deploy/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deploy/payments --timeout=120s
kubectl scale deploy/redis --replicas=0
# Sample after 60, 120 and 180 seconds.
kubectl scale deploy/redis --replicas=1
kubectl set env deploy/payments PAYMENT_LATENCY_MS=0
```

In the 5s-resolution [Prometheus onset replay](evidence/lab8/combined-onset-range.json),
errors reacted first: the 1m ratio changed from zero to **0.244% at 08:30:38 UTC**.
Reserve p99 then crossed 1s at **08:30:43 UTC** (bucket estimate **5.950s**),
aggregate errors exceeded 5% at **08:30:53 UTC** (**6.60%**), and read p99
rose to **2.675s at 08:30:58 UTC**. These are replay sample times and chosen
thresholds, not exact request-event timestamps or an alert firing claim.

| UTC snapshot | Gateway RPS | 5xx ratio | Read p99 | Reserve p99 | Pay p99 |
|---|---:|---:|---:|---:|---|
| [08:31:30](evidence/lab8/combined-1.json) | 3.400 | 97.86% | 5.150s | 7.203s | NaN |
| [08:32:31](evidence/lab8/combined-2.json) | 4.309 | 100% | 4.033s | 4.042s | NaN |
| [08:33:32](evidence/lab8/combined-3.json) | 4.382 | 100% | 6.000s | 4.895s | NaN |

The first-to-third sample interval is 122s, with the first sample about 60s
after both faults were active: total observation lasted over three minutes.
Read and reserve probes returned 502 at all three snapshots; `/health` was
503. Both dependencies were restored before applying the bonus improvement.

The weakest link was the **events readiness policy coupled to Redis**:
removing a reservation dependency removed the Service needed for DB-only
reads. [Events EndpointSlices](evidence/lab8/combined-events-endpointslices.yaml)
show non-ready backends; [events logs](evidence/lab8/combined-events-logs.txt)
include Redis connection timeouts and failed health checks. Among the three scheduled samples, reserve had the largest absolute
p99 (**7.203s**, about **288x** its 25ms baseline). Read p99 reached **6.000s**,
about **381x** its 15.7ms baseline, the larger relative amplification. Pay eventually became NaN because reservations no longer produced
IDs, masking the simultaneously slow payments service rather than proving it
healthy. Direct payment health is process health, not a checkout SLI.

This disproved independent graceful degradation: the earlier Redis failure
prevented the load from reaching payments. I would decouple read readiness
from Redis, and retain reservation/pay-specific error, latency and traffic
alerts so a missing checkout path cannot look successful.

## Bonus — Preserve reads during Redis failure

Weakness: events readiness uses `/health`, which reports 503 when Redis is
down even though PostgreSQL can still serve `/events`. Kubernetes consequently
removes events endpoints and the gateway loses DB-only read access.

The [events manifest](../k8s/events.yaml) replaces the readiness HTTP status
check with an exec probe that checks the local TCP listener, then connects directly
to PostgreSQL and requires
`SELECT 1` to succeed. It uses a 2s connection deadline and a 1s statement
timeout within the 3s probe timeout; exceptions fail the probe. This avoids
calling `/health`, whose Redis retries can themselves exceed a probe deadline. Liveness remains
TCP-based; `/health` continues reporting actual dependency degradation.

The runner records the original readiness first, applies the fix, waits for
rollout/baseline, then repeats **the same Redis scale-to-zero experiment**.
[Hypothesis before re-run](evidence/lab8/redis-after-fix-hypothesis.json).

The same Redis scale-to-zero failure was held for 90s both times, under
the same two mixedload workers with payments latency zero and unchanged DB
pool/resources. At **08:38:02 UTC** after the fix, `/events` returned **200
in 0.0145s**, while reserve returned **504 in 5.011s** and `/health` **503**.
The events pod remained Ready with Redis absent.
[After-fix snapshot](evidence/lab8/redis-after-fix.json),
[ready EndpointSlice](evidence/lab8/redis-after-endpointslices.yaml),
[client statuses](evidence/lab8/redis-after-client-sample.txt).

| Impact during Redis outage | Before fix | After fix |
|---|---:|---:|
| Read `/events` HTTP probe | 504 / 5.016s | 200 / 0.0145s |
| Read 1m 5xx ratio | 1 (100%) | 0 (0%) |
| Read p99 | 2.487s | 0.0246s |
| Aggregate 1m 5xx ratio | 100% | 50% |
| Health | 503 | 503 |

[Read-specific Prometheus results at the recorded outage times](evidence/lab8/read-sli-before-after.json)
show **~101x lower read p99**. [Comparison script](../scripts/compare-lab8-reads.py)
queries the historical timestamps rather than comparing a failed service
with an already recovered one. Both outage snapshots include pod state;
the fix preserves reads rather than masking the remaining booking failure.

The hypothesis held: partial availability improved, while reservation errors
remained visible. Pay is still NaN during this outage because no valid
reservation reaches it; nothing in this comparison claims checkout recovery.

Tradeoff: an extra short DB connection is opened every five seconds; events accepts traffic for working read paths while bookings fail;
clients and alerts must use endpoint SLIs rather than equating readiness with
full transaction health. The existing Redis init container still blocks a
cold start during an outage, so this change only improves a running service.

## Reproduction and cleanup

See [k8s/README.md](../k8s/README.md). The runner restores Redis to one replica,
payments latency to zero, keeps the improved events readiness, and removes
mixedload in its finally block. Baseline changes are deliberately temporary;
the committed configuration contains the verified improvement.


Validation: Kubernetes server-side dry-run, live Prometheus `promtool` check,
Python compilation, shell syntax and the real repeated failure experiment.
The DB-only probe also succeeded against the running events container while
Redis was unavailable, whereas its original HTTP readiness was failing.

- [x] Task 1: three hypotheses, fault injection, timestamped observations and comparisons.
- [x] Task 2: two concurrent failures, three-minute observations and weakest-link analysis.
- [x] Bonus: readiness improvement and before/after HTTP + Prometheus evidence.

Final cleanup at **2026-10-05T08:39:19.266992+00:00**: all read/reserve/pay/health probes
returned 200, Redis had one ready replica, payments latency was zero, and
[the gateway was Healthy with five ready replicas](evidence/lab8/final-rollout.txt).
Mixedload was deleted after the [final healthy metrics](evidence/lab8/final.json)
were recorded. Prometheus/Grafana remain available for later labs. Terminal
padding is stripped from text captures without changing values or timestamps.
