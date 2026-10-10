# Lab 8 — Chaos Engineering: Break Things on Purpose

## Setup

Applied the Lab 8 full-checkout loadgen and confirmed baseline traffic in Prometheus:

```bash
kubectl apply -f labs/lab8/mixedload.yaml
kubectl rollout status deployment/mixedload --timeout=60s
```

Baseline RPS (in-cluster Prometheus):

```
sum(rate(gateway_requests_total[1m])) → ~8.7 req/s
```

Observation method: queries were run from inside the cluster against the Lab 7
Prometheus (`kubectl exec -n monitoring deployment/prometheus -- wget -qO- '…'`),
since the docker-compose Grafana cannot scrape k3d pod IPs.

---

## Task 1 — Three Chaos Experiments

### Experiment 1 — Pod Kill Under Load

**HYPOTHESIS (written before running):** "If I delete one of the 5 gateway pods
while traffic is flowing, **zero** client-visible 5xx will occur, because the
Service load-balances only across Ready endpoints — the remaining 4 pods absorb
the traffic while Kubernetes recreates the deleted pod in a few seconds."

**Commands:**

```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
kubectl delete "$VICTIM"
kubectl get pods -l app=gateway -w        # until 5/5 Running again
# 5xx during transition:
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total{status=~"5.."}[1m]))'
# per-pod distribution:
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum by (pod) (rate(gateway_requests_total[1m]))'
```

**Observations (22:41):**

- Victim `gateway-756d5575-7548l` deleted at **22:41:39**.
- Replacement pod `gateway-756d5575-rqb88` reached `5/5 Ready` after **10 seconds** (22:41:49).
- 5xx increase over the clean 1-minute window: **0**.
- Per-pod RPS right after the kill (traffic evenly spread, including the new pod ramping up):

  ```
  gateway-756d5575-8msdl   2.946
  gateway-756d5575-rjdtr   2.746
  gateway-756d5575-nkw46   2.509
  gateway-756d5575-kp2df   2.328
  gateway-756d5575-rqb88   1.974   (new pod)
  ```

**Comparison:** Hypothesis **confirmed**. Zero errors; the Service rerouted to the
4 survivors and the ReplicaSet recreated the pod in 10s. What was mildly
surprising: the replacement pod immediately began taking a near-equal share of
traffic (1.97 RPS) as soon as it passed readiness — no cold-start dip.

**To improve resilience against this failure, I would** add a
`PodDisruptionBudget` (`minAvailable: 4`) so voluntary disruptions (drains,
rollouts) can never remove more than one gateway pod at a time.

### Experiment 2 — Payment Latency Injection

**HYPOTHESIS (written before running):** "If payments takes 2000 ms per request,
`/pay` p99 climbs toward ~2 s but **no 5xx** appears, because 2000 ms is below
`GATEWAY_TIMEOUT_MS` (5000 ms); read paths (`/events`) stay flat. Pushing latency
to 6000 ms (> timeout) makes `/pay` return **504** after ~5 s as the gateway
self-protects via its client timeout."

**Commands:**

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
# p99 per path + 5xx ratio sampled every ~15s:
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99, sum by (le,path) (rate(gateway_request_duration_seconds_bucket[1m])))'
# then beyond the timeout:
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
```

> Note: `/pay` traffic requires a successful `reserve` first. Event 1 (100 tickets)
> was already sold out from hours of prior load, so a short helper loadgen was
> pointed at a higher-capacity event to generate steady `/pay` traffic during this
> experiment.

**Observations:**

At **2000 ms** (stable across ~2 min, 22:47–22:49):

```
p99 /events               0.050s
p99 /events/{id}/reserve  0.161s
p99 /reserve/{id}/pay     2.485s      ← only /pay spikes
5xx ratio                 0.0000
```

At **6000 ms** (> 5000 ms timeout, 22:49–22:51) — `/pay` by status (1-min rate)
transitions from all-200 to all-504 as the window fills:

```
22:49:56   200=0.836
22:50:11   200=0.655  504=0.025
22:50:41   200=0.218  504=0.201
22:51:11   200=0.000  504=0.347   ← gateway now fails every /pay at the timeout
```

Direct timing confirmed `/pay` returns **504 after ~5.01 s** (= GATEWAY_TIMEOUT_MS).

**Comparison:** Hypothesis **confirmed** on all points — only `/pay` p99 moved
(reads stayed ~50 ms), no 5xx below the timeout, and 504s appeared exactly once
latency crossed the 5 s timeout. Surprise: the histogram `p99 /pay` reported
~7.4 s under the 6 s injection (a bucket-boundary artifact of the coarse upper
buckets); the *actual* wall-clock time-to-fail was a clean 5.01 s.

**To improve resilience against this failure, I would** lower the payment call
timeout so the gateway fails fast instead of tying up a worker for 5 s (see Bonus),
and add a p99-latency SLO alert so "slow but successful" degradation pages before
it becomes 504s.

### Experiment 3 — Redis Failure

**HYPOTHESIS (written before running):** "If Redis goes down, listing events keeps
working (pure Postgres read), but reserving tickets **breaks with 5xx** because
the reservation hold needs Redis; `/health` reports **degraded/503** because it
checks its dependencies."

**Commands:**

```bash
kubectl scale deployment/redis --replicas=0
# probe from inside the cluster:
kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --command -- \
  sh -c 'curl ... /events ; curl -X POST ... /reserve ; curl ... /health'
kubectl scale deployment/redis --replicas=1
```

**Observations (22:52):**

- Redis scaled to 0 at **22:52:08**, pod gone by **22:52:11**.
- Endpoint probe:

  ```
  GET  /events   → 200  (0.05s)   ← read path unaffected
  POST /reserve  → 500            ← broken, needs Redis
  GET  /health   → 503  {"status":"degraded","checks":{"events":"degraded","payments":"ok"}}
  ```

- Error rate ~30 s into the outage: **5xx ratio = 0.586 (58.6%)**.
- `reserve`/`pay` chain by status (1-min rate): `502=0.205/s`, `504=0.233/s`, a
  handful of `200=0.018/s`.
- After restore (`--replicas=1`, condition met): `/reserve → 200`, `/health → 200`.

**Comparison:** Hypothesis **confirmed**. Reads survived, the mutating path
collapsed, and `/health` flipped to 503. Surprise: `/health` reported `events`
as *degraded* rather than naming Redis directly — the gateway only sees its
immediate dependency (events), which in turn fails its Redis check; the root
cause is one hop deeper than the gateway's health output suggests.

**To improve resilience against this failure, I would** make Redis a soft
dependency for reservations (degrade to a short-TTL Postgres-backed hold, or
fail the reserve cleanly with a 503 + `Retry-After` instead of a 500), so a cache
outage degrades gracefully instead of throwing server errors.

---

## Task 2 — Combined Failure Scenario

**Scenario design (what + why):** "Degraded dependencies" — stack a flaky **and**
slow downstream with a constrained resource under elevated load:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
```

Rationale: real incidents are rarely a single clean failure. This mimics a
downstream (payments) degrading while a shared resource (the events DB connection
pool) is tight and traffic is elevated — to see which golden signal moves first
and which path amplifies latency worst.

**Observations over the window (22:54–22:56):**

```
t   5xx_ratio   p99 /events   p99 /reserve   p99 /pay
1   0.0049      0.149s        0.248s         0.713s
2   0.0035      0.162s        0.242s         0.729s
3   0.0061      0.140s        0.239s         0.747s
4   0.0024      0.097s        0.199s         (pay traffic drained)
6   0.0024      0.177s        0.215s
```

- **Which golden signal reacted first?** **Latency.** `/pay` p99 jumped to ~0.7 s
  immediately, while the **error rate stayed near 0.3–0.6 %** — a classic
  "slow but successful" partial degradation that error-rate dashboards alone miss.
  (Payment failures were largely absorbed below the 5 s timeout, so they mostly
  showed as latency, not 5xx.)
- **Worst latency amplification:** `/reserve/{id}/pay` — ~0.7 s, roughly **5×** the
  read paths (`/events` ~0.15 s, `/events/{id}/reserve` ~0.24 s). The payment
  latency dominated; the `DB_MAX_CONNS=3` cap did **not** visibly bite, because
  most `reserve` calls returned a cheap 409 (event inventory was already exhausted)
  and held a DB connection only briefly.

**Answer — which component was the weakest link, and how to make it more
resilient?** The **weakest link was payments**: it is both flaky and slow, and the
gateway has no protection in front of it (the circuit breaker is a no-op), so the
gateway simply absorbs payment latency into its own p99 and worker pool. I would
put a **circuit breaker + fail-fast timeout** in front of the payments call so a
degraded payments service is shed quickly (fast 503) instead of amplified, and add
a **p99 latency SLO alert** so the "all 200 OK but slow" state pages an operator.

---

## Bonus Task — Resilience Improvement

**Weakness chosen:** From Experiment 2 — when payment latency ≥
`GATEWAY_TIMEOUT_MS`, every `/pay` request blocks for the **full 5 s** timeout
before returning 504. The gateway has no fail-fast (its circuit breaker is a
no-op), so a slow dependency ties up a gateway worker for 5 s per request,
shrinking effective capacity precisely when the system is already stressed.

**What I changed (config diff):** lower the gateway's downstream timeout so it
fails fast.

```diff
  env:
    - name: GATEWAY_TIMEOUT_MS
-     value: "5000"
+     value: "2000"
```

Applied to the live gateway Rollout (which promoted through its canary + analysis
gate cleanly with payments healthy).

**Re-run the same experiment** (inject `PAYMENT_LATENCY_MS=6000`, time `/pay`):

| | impact metric: `/pay` time-to-fail (504) |
|---|---|
| **Before fix** (timeout=5000) | ~**5.01 s** (`5.007s, 5.013s, 5.008s, 5.008s, 5.011s`) |
| **After fix** (timeout=2000) | ~**2.01 s** (`2.009s, 2.008s, 2.008s, 2.008s, 2.009s`) |

Time-to-fail dropped by ~**60%** (5.01 s → 2.01 s). The gateway now sheds a
hung payment call in 2 s, releasing the worker ~3 s sooner per failed request.

**What the fix traded off:** a tighter timeout will also reject *legitimate but
slow* payments in the 2–5 s range that would previously have succeeded — i.e. it
trades a small amount of success rate on genuinely slow-but-OK requests for
bounded tail latency and faster resource release under stress. A production
version would pair this with a bounded retry (idempotency-keyed) so a slow-but-OK
payment gets a second fast chance rather than a hard fail.

---

## Cleanup

```bash
kubectl delete -f labs/lab8/mixedload.yaml
# payments/events env restored to defaults; redis back to 1 replica
```

Final state verified: `5xx ratio = 0`, `/events → 200`, `/health → 200`.
