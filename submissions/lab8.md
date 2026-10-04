# Lab 8 — Chaos Engineering: Break Things on Purpose

**Student:** Gleb Shvetsov

**GitHub:** `L10nff`

**Working branch:** `feature/lab8`

## Environment and baseline

The experiments used the existing `k3d-quickticket` cluster, the five-replica
Gateway Argo Rollout, the Lab 7 in-cluster Prometheus, and the provided Lab 8
`mixedload` Deployment. Argo CD automated synchronization was temporarily
disabled so that deliberate runtime mutations were not reverted during the
experiments.

All timestamps in this report are UTC. The three Task 1 hypotheses were written
before failure injection at `2026-10-04T13:44:07Z` and saved with SHA-256:

```text
9ab5ef6529a340ff7809045fe933fcdd79313074e62c79a84cd2f4c5557bc312
```

The persistent Event 1 fixture initially had 100 total tickets, 50 confirmed
tickets, and a stale Redis held counter of 50. Before testing the complete
checkout path, its runtime-only capacity was expanded and the stale held
counter was cleared. This prevented data retained from previous labs from
turning every reservation into an unrelated HTTP 409.

Baseline at `2026-10-04T13:47:16Z`:

```text
RPS: 8.5271
Gateway 5xx ratio: 0
/events p99: 0.0922 s
/events/{id}/reserve p99: 0.0955 s
/health p99: 0.2925 s
```

## Task 1 — Three Chaos Experiments

### Experiment 1 — Gateway Pod Kill Under Load

#### Hypothesis

> If one of five Gateway pods is deleted under load, requests will continue
> through the remaining four pods without a significant 5xx increase, because
> the Service removes the terminating endpoint and Argo Rollouts creates a
> replacement.

#### Method

The victim was selected from the current Gateway pods and deleted without
waiting for termination:

```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
kubectl delete "$VICTIM" --wait=false
```

Pod names, timestamps, Ready counts, the two-minute 5xx increase, and per-pod
request rates were sampled until five Ready replicas were restored.

#### Observations

```text
experiment_started: 2026-10-04T13:50:00Z
victim: gateway-9dfff959b-fk27s
replacement: gateway-9dfff959b-blf9f
replacement_creation_seconds: 0.22
five_ready_pods: 2026-10-04T13:50:10Z
replacement_ready_seconds: 9.94
gateway_5xx_increase_2m: 0
```

Traffic continued on the four surviving replicas while the replacement became
Ready. The new Pod had already reached approximately `0.762 RPS` by the final
sample, while the surviving replicas handled approximately `1.33–1.55 RPS`
each. The final Rollout state was `Healthy`, with five desired, Ready, and
available replicas.

#### Hypothesis versus reality

The hypothesis was correct. Kubernetes created the replacement almost
immediately, service capacity returned to five Ready replicas in under ten
seconds, and no Gateway 5xx responses were measured. The main surprise was how
quickly the controller created the new Pod: `0.22s` after deletion.

To improve resilience further, I would add topology spread constraints and a
PodDisruptionBudget so the five replicas cannot be concentrated on one failure
domain or removed together during voluntary disruption.

### Experiment 2 — Payment Latency Injection

#### Hypothesis

> If Payments adds 2000 ms latency, `/pay` p99 latency will rise to
> approximately two seconds but requests will remain successful because the
> delay stays below the Gateway 5000 ms timeout; read-only paths should remain
> unaffected.

#### Method

```bash
kubectl set env deployment/payments \
  PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=90s
```

After 90 seconds of mixed checkout traffic, the Gateway error ratio, p99 by
path, status counts, and a direct checkout were measured. A second observation
used `PAYMENT_LATENCY_MS=6000` to cross the Gateway timeout boundary. Payments
was then restored to zero injected latency.

#### Observations at 2000 ms

The experiment began at `2026-10-04T13:53:53Z`.

```text
Gateway 5xx ratio: 0
/reserve/{id}/pay p99: 2.485 s
/events p99: 0.0942 s
/events/{id}/reserve p99: 0.0442 s
direct pay HTTP status: 200
direct pay duration: 2.024793 s
```

The one-minute window contained successful traffic on all three workload
paths, including approximately 25 successful payment requests. Slow Payments
did not propagate latency to event reads or reservations.

#### Observation beyond the timeout

The 6000 ms stage began at `2026-10-04T13:55:38Z`.

```text
Gateway 5xx ratio: 0.0831029
/reserve/{id}/pay p99: 7.475 s
/events p99: 0.0725 s
/events/{id}/reserve p99: 0.0475 s
direct pay HTTP status: 504
direct pay duration: 5.012531 s
response: {"detail":"Payment service timeout"}
```

Payments was restored to `PAYMENT_LATENCY_MS=0` and
`PAYMENT_FAILURE_RATE=0.0`. Gateway health then returned HTTP 200.

#### Hypothesis versus reality

The 2000 ms hypothesis was correct: payment p99 increased while all sampled
requests remained successful and read paths stayed fast. At 6000 ms, Gateway
protected itself at approximately its configured five-second timeout. The
rolling p99 nevertheless reached `7.475s`, showing that an aggregate histogram
can retain slower and retried requests even when one direct request stops near
five seconds.

To improve resilience, I would alert on payment-path p99 latency in addition to
5xx rate and use a bounded retry budget so slow downstream calls do not amplify
load.

### Experiment 3 — Redis Failure

#### Hypothesis

> If Redis is unavailable, event listing will remain available, reservations
> will fail or time out, and Gateway health will become degraded because Redis
> is required for reservation holds but not PostgreSQL-backed reads.

#### Method

```bash
kubectl scale deployment/redis --replicas=0
kubectl wait --for=delete pod -l app=redis --timeout=60s
```

After the five-second Events health cache expired, in-cluster probes exercised
event listing, reservation creation, and Gateway health. Prometheus status
counts and p99 values were collected for one minute. Redis was then scaled back
to one replica.

#### Observations

The failure started at `2026-10-04T13:59:40Z`.

```text
Redis pods after injection: none
GET /events: successful HTTP 200 traffic remained visible
POST /events/{id}/reserve: HTTP 504 in 5.014764 s
GET /health: HTTP 503 in 0.011623 s
Gateway health: events=degraded, payments=ok
/events p99: 0.0247 s
/events/{id}/reserve p99: 7.475 s
```

Prometheus observed successful `/events` requests while reservation requests
produced 504 responses. Events logs also showed a new server startup during the
outage, followed by `Redis connection failed`, revealing that dependency-aware
Kubernetes probes restarted Events even though its PostgreSQL-backed read path
could still work.

Recovery started at `2026-10-04T14:00:43Z`:

```text
Redis PING: PONG
Gateway health: HTTP 200 in 0.007941 s
Reservation: HTTP 200 in 0.006850 s
Redis restore duration: 17 s
```

#### Hypothesis versus reality

The functional part of the hypothesis was correct: reads remained possible,
reservations timed out, and Gateway health became degraded. The surprise was
that `/health` was also used for liveness and readiness. Redis failure therefore
caused Kubernetes to treat the whole Events process as unhealthy, which is
unnecessary for read-only traffic.

To improve resilience, I would separate process health from dependency health,
keep Events alive for read traffic, and make Redis operations fail fast with a
short bounded connection retry policy. The probe separation was implemented
and tested in the Bonus Task.

## Task 2 — Combined Failure Scenario

### Scenario and hypothesis

The combined scenario was recorded at `2026-10-04T14:05:34Z` before injection:

- Payments failure rate: 30%.
- Payments latency: 500 ms.
- Events DB connection limit: 3.
- Mixedload replicas: 3.

> The payment path will be the weakest link. Payment latency will react first,
> retries will amplify downstream calls, and some final 5xx responses will
> appear. Event reads and reservations may slow under DB pool contention but
> should remain more available than payments.

The scenario was applied with:

```bash
kubectl set env deployment/payments \
  PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
```

The clean baseline immediately before injection was:

```text
Gateway error ratio: 0
/reserve/{id}/pay p99: 0.0979 s
/events p99: 0.1390 s
/events/{id}/reserve p99: 0.0927 s
```

### Three-minute observation

| Time (UTC) | Error ratio | Pay p99 | Events p99 | Reserve p99 |
|---|---:|---:|---:|---:|
| 14:06:03 | 0.0000 | 0.6838 s | 0.2333 s | 0.1685 s |
| 14:06:34 | 0.0425 | 0.7458 s | 0.3812 s | 0.2025 s |
| 14:07:05 | 0.0937 | 0.7475 s | 0.0702 s | 0.0591 s |
| 14:07:35 | 0.1064 | 0.7475 s | 0.0629 s | 0.0767 s |
| 14:08:07 | 0.0943 | 0.7475 s | 0.0505 s | 0.0884 s |
| 14:08:37 | 0.0897 | 0.7475 s | 0.0754 s | 0.0423 s |
| 14:09:07 | 0.1086 | 0.7475 s | 0.0243 s | 0.0770 s |

Latency was the first golden signal to react. At the first observation the
error ratio was still zero, but payment p99 had already increased from
`0.0979s` to `0.6838s`. Errors appeared by the second sample and then remained
near 9–11%.

The three-minute aggregate contained approximately:

```text
/events HTTP 200: 637.70
/events/{id}/reserve HTTP 200: 630.50
/reserve/{id}/pay HTTP 200: 418.62
/reserve/{id}/pay HTTP 500: 214.97
```

The fractional values are expected from Prometheus counter interpolation at
the range boundaries.

### Weakest link and improvement

Payments was the weakest link. `/pay` showed the largest sustained latency
amplification and was the only workload path with material 5xx responses. The
three-connection Events pool caused only a short transient increase on reads
and reservations; it did not dominate the scenario.

I would make Payments more resilient by limiting retry amplification, adding a
latency-aware circuit breaker, and alerting on payment-path p99 before the
error-rate SLO is consumed. All injected variables and mixedload replica counts
were restored, and Gateway health returned HTTP 200 at
`2026-10-04T14:09:36Z`.

## Bonus Task — Resilience Improvement

### Weakness selected

The Redis experiment showed that Events used dependency-aware `/health` for
both Kubernetes liveness and readiness. Redis failure could therefore restart
or remove the entire Events service even though event listing only needs
PostgreSQL.

The Bonus hypothesis was recorded before the change at
`2026-10-04T14:12:17Z`.

### Configuration fix

Both Kubernetes probes were changed to the process-level `/metrics` endpoint.
The existing `/health` endpoint remains available for dependency monitoring:

```diff
 livenessProbe:
   httpGet:
-    path: /health
+    path: /metrics
 readinessProbe:
   httpGet:
-    path: /health
+    path: /metrics
```

This change is committed in `k8s/events.yaml`.

### Repeated experiment and comparison

The corrected repeat began at `2026-10-04T14:16:38Z`. The active Pod was
selected only after the rollout completed and terminating predecessors were
excluded.

| Measurement | Before fix | After fix |
|---|---|---|
| Events behavior after Redis failure | Events process started again during the outage | Same Pod remained running |
| Pod UID stability | Not captured in the first run | `6f7f77f5-58ba-4a48-ab88-f76cac13aa3c` before and after |
| Restart evidence | A new Events server startup was logged during the outage | `0` restarts before and `0` after the 35 s observation |
| Ready after old 30 s liveness window | Dependency probe could remove Events | `true` after 35 s |
| Reservation behavior | HTTP 504 in `5.014764s` | HTTP 504 in `5.070306s` |
| Gateway health | HTTP 503, Events degraded | HTTP 503, Events degraded |

After the fix:

```text
events_pod_identity_preserved=yes
events_restart_count_unchanged=yes
active_events_ready_after=true
```

The fix achieved its intended process-level improvement: Redis degradation no
longer caused Kubernetes to restart or remove Events. The second part of the
hypothesis was not achieved—reservation latency did not improve because the
Redis client still blocked until the Gateway timeout. A subsequent improvement
should bound Redis retries and return a fast explicit dependency error.

The trade-off is that Kubernetes now considers Events ready during a partial
Redis outage. This intentionally preserves read availability, but it makes
external `/health` monitoring and path-specific alerts essential for detecting
the degraded reservation capability.

Redis was restored successfully, `PING` returned `PONG`, and final Gateway
health returned HTTP 200 at `2026-10-04T14:17:43Z`.

## Final verification

- Three Task 1 hypotheses were written before injection.
- All three experiments include commands, UTC timestamps, Prometheus or HTTP
  evidence, hypothesis comparisons, and resilience improvements.
- The combined scenario ran for three minutes with seven timed samples.
- Payments was identified as the weakest component in the combined scenario.
- The Events probe weakness was fixed and re-tested with before/after evidence.
- Payments and Redis were restored, and final Gateway health was HTTP 200.
- No repository secrets, access tokens, or webhook URLs are included in the
  committed files.

## Conclusion

The experiments showed that replica-level self-healing handled a Pod loss with
no observed user-visible errors, while partial dependency degradation was much
more revealing. Slow Payments first appeared as latency and only later as 5xx
responses. Redis failure preserved some read traffic but exposed an incorrect
coupling between dependency health and Kubernetes process probes. Separating
those probes improved Pod stability, while the remaining five-second
reservation timeout identified the next resilience improvement: bounded,
fail-fast Redis behavior.
