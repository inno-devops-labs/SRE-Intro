# Lab 8 — Chaos Engineering: Break Things on Purpose

Environment: local k3d cluster `quickticket`, `gateway` as an Argo Rollouts Rollout with 5 replicas (from Lab 7),
in-cluster Prometheus in the `monitoring` namespace. Load driven by `labs/lab8/mixedload.yaml` (2 replicas)
exercising `/events`, `/events/{id}/reserve` and `/reserve/{id}/pay` together. Baseline ≈ 13 RPS.

> Observation method: Prometheus queries via `kubectl exec -n monitoring deployment/prometheus -- wget -qO- …`.

---

## Task 1 — Three Chaos Experiments

### Experiment 1 — Pod Kill Under Load

**HYPOTHESIS (written before running):** "If I delete one gateway pod while traffic is flowing, a few
in-flight requests may fail and the request rate will briefly dip, then Kubernetes recreates the pod within
~10–20s and the remaining 4 pods keep serving because the Service load-balances only over Ready endpoints."

**Execute:**

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ echo "KILL $VICTIM at $(date +%H:%M:%S)"; kubectl delete "$VICTIM"
KILL pod/gateway-bb5476b6-5ptkw at 22:13:24
pod "gateway-bb5476b6-5ptkw" deleted
```

**Observed:**

```console
# Recovery time — loop until 5/5 Running again
back to 5/5 Running at 22:13:27 — recovery ~3s

# 5xx over a 3m window spanning the kill (BEFORE vs AFTER)
5xx in 3m BEFORE: 1.03
5xx in 3m AFTER:  1.03      # → ZERO new errors caused by the kill

# Per-pod RPS after recovery — traffic evenly spread over all 5 (incl. the fresh replacement s8ht7)
  gateway-bb5476b6-8lcq7 -> 2.73 rps
  gateway-bb5476b6-7g9l2 -> 2.60 rps
  gateway-bb5476b6-pj8cg -> 2.49 rps
  gateway-bb5476b6-p922f -> 2.36 rps
  gateway-bb5476b6-s8ht7 -> 3.16 rps   # ← replacement pod
```

**Compare (hypothesis vs reality):** Recovery was much faster than my 10–20s guess (~3s — the image is already
cached on the node) and there were **zero** additional 5xx, not "a few failed requests". The Service silently
dropped the terminating pod from its endpoints and the other 4 absorbed the load with no client-visible impact.
The surprise was how completely invisible a single pod loss is under 5 replicas.

**To improve resilience against this failure, I would** add a `PodDisruptionBudget` (`minAvailable: 4`) so
voluntary disruptions (node drains, rollouts) can never take more than one gateway pod at a time.

---

### Experiment 2 — Payment Latency Injection

**HYPOTHESIS (written before running):** "If payments takes 2000ms per request, the gateway will NOT return
5xx (2000ms < `GATEWAY_TIMEOUT_MS` of 5000ms), only the `/pay` p99 latency will spike toward ~2s while the read
paths (`/events`, `/reserve`) stay fast, because latency is isolated to the payments dependency."

**Execute & observe (2000ms):**

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl rollout status deployment/payments --timeout=40s
deployment "payments" successfully rolled out

# BASELINE p99 per path (before)          # WITH 2000ms latency
  /events                -> p99 24.5 ms      /events                -> p99   24.5 ms
  /events/{id}/reserve   -> p99 24.9 ms      /events/{id}/reserve   -> p99   38.0 ms
  /health                -> p99 29.0 ms      /health                -> p99   24.7 ms
  /reserve/{id}/pay      -> p99 20.3 ms      /reserve/{id}/pay      -> p99 2485.0 ms   # ← spike

# Error ratio (5xx / total):  0.0 %          # no errors — 2000ms < 5000ms timeout
```

**Bonus observation — push latency beyond the timeout (6000ms > 5000ms):**

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
# /pay status breakdown after the window fills:
  /reserve/{id}/pay status=200 -> 0.000 rps
  /reserve/{id}/pay status=504 -> 0.382 rps   # ← gateway self-protects: times out at 5000ms → 504
```

**Restore:** `kubectl set env deployment/payments PAYMENT_LATENCY_MS=0`

**Compare (hypothesis vs reality):** Exactly as hypothesised for 2000ms — `/pay` p99 jumped from 20ms to
**2485ms**, reads stayed at ~24ms, and **zero** 5xx. This is the most instructive case: a partial degradation
that a pure error-rate SLO would completely miss (everything is still `200 OK`, just slow). At 6000ms the
gateway's timeout kicked in and converted the slow calls into fast **504**s — and throughput collapsed
(0.38 rps) because each request now blocks for the full 5s timeout.

**To improve resilience against this failure, I would** add a p99-latency SLO alert on
`gateway_request_duration_seconds` per path, so "slow but 200 OK" payment degradation pages someone before it
silently ruins the checkout experience.

---

### Experiment 3 — Redis Failure

**HYPOTHESIS (written before running):** "If Redis goes down, listing events (`/events`) will keep working
because the read path only needs Postgres, but reserving tickets (`/events/{id}/reserve`) will fail because the
hold is stored in Redis, and `/health` will report degraded."

**Execute:**

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl scale deployment/redis --replicas=0
deployment.apps/redis scaled
redis gone at 22:21:36
```

**Observed (Prometheus, request counts over a 2m window with Redis down):**

```console
  /events              status=200 -> 11.1
  /events              status=502 ->  5.3    # ← list ALSO degrades
  /events/{id}/reserve status=200 ->  3.3
  /events/{id}/reserve status=504 ->  4.9    # ← reserve fails
  /reserve/{id}/pay    status=504 ->  4.5
  /health              status=200 -> 32.0
  /health              status=503 -> 120.9   # ← health mostly degraded

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ curl -s http://gateway:8080/health   # (via mixedload pod)
{"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}
```

The mixedload throughput collapsed from ~13 RPS to near zero: each `/events` call blocks ~5s on the Redis
connection attempt inside the availability calculation (`_get_available` reads `event:{id}:held` from Redis)
before the gateway times out and returns 504.

**Restore:** `kubectl scale deployment/redis --replicas=1` → within ~70s `/health` back to 200, overall error
ratio back to ~0.13 % (baseline).

**Compare (hypothesis vs reality):** The reserve/health parts matched, but the big surprise **contradicted** my
hypothesis: `/events` (list) did **not** stay healthy. Availability computation calls Redis synchronously, and
with Redis unreachable that call blocks on the connection timeout, so the "read-only" list path times out (504)
and drags overall throughput down — a **cascading failure** far wider than the reserve path I expected. Redis
turned out to be a hidden hard dependency of the read path, not just the write path.

**To improve resilience against this failure, I would** make the Redis call in `_get_available` fail fast
(short connect timeout, e.g. 200ms) and treat "Redis unavailable" as `held = 0` so listing degrades gracefully
to Postgres-only instead of timing out — turning a cascading outage into a minor accuracy loss.

---

## Task 2 — Combined Failure Scenario

**Scenario design (what + why):** *Degraded dependencies* — payments returns 30 % errors AND +500 ms latency,
events' Postgres pool is capped at `DB_MAX_CONNS=3`, and load is raised to `mixedload` × 3. This models a real
incident where a downstream (payments) is flaky/slow at the same time a resource limit (DB connections) is
tightened — testing whether failures on one dimension mask or amplify the other.

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl set env deployment/events DB_MAX_CONNS=3
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl scale deployment/mixedload --replicas=3
```

**Observations over the 4-minute window** (error ratio + p99 per path, sampled every ~70 s):

```console
SAMPLE 1  22:25:44   error 0.12 %   /pay p99 723 ms   /events 25   /reserve 25   /health 2158 ms*
SAMPLE 2  22:26:54   error 5.99 %   /pay p99 748 ms   /events 24   /reserve 21   /health 24
SAMPLE 3  22:28:05   error 9.75 %   /pay p99 748 ms   /events 24   /reserve 19   /health 24
SAMPLE 4  22:29:16   error 9.73 %   /pay p99 748 ms   /events 23   /reserve 51   /health 30
  (* sample 1 /health spike = payments pods still rolling; gone by sample 2)
```

**Which golden signal reacted first?** *Latency* — `/pay` p99 was already at 723 ms in the very first sample
(the 500 ms injection is immediate), while the *error* ratio ramped up over ~2 minutes as the `[1m]` rate
window filled with the 30 % payment failures, settling around **~9.7 %**.

**Worst latency amplification by path:**
- `/reserve/{id}/pay`: 20 ms → **748 ms** (~37×) — dominant.
- `/events/{id}/reserve`: 25 ms → 51 ms (~2×) — mild, DB-pool queueing starting to show under load.
- `/events` (read): unchanged (~24 ms).

**Weakest link + how to make it more resilient:** **payments** was the weakest link — it was the sole source of
both the error rate (its 30 % failures) and the p99 blow-up (its 500 ms latency); the `DB_MAX_CONNS=3` cap only
produced a mild `/reserve` bump at this load. To make it more resilient I'd put the gateway→payments call
behind a **circuit breaker with fail-fast** (open after N consecutive failures so we stop waiting 500 ms per
doomed call) plus a **bulkhead/timeout** budget, so a degraded payments service sheds load quickly instead of
tying up gateway workers and dragging the whole checkout p99 with it.

---

## Bonus Task — Resilience Improvement

**Weakness chosen:** From Experiment 2 — when the payments dependency is slower than the gateway's timeout
(`PAYMENT_LATENCY_MS=6000` > `GATEWAY_TIMEOUT_MS=5000`), every doomed `/pay` call still occupies a gateway
worker for the *full 5 s* before returning 504. That ties up capacity and collapses `/pay` throughput — a slow
dependency turning into a gateway-side resource exhaustion.

> Note: I first tried the DB connection-pool weakness (`DB_MAX_CONNS=3`), but under load ×10 the reserve p99
> stayed ~48 ms and raising the pool to 20 produced no clean improvement (noise dominated) — this app's queries
> are too cheap for 3 connections to be the bottleneck. So I picked a weakness with a deterministic fix instead.

**Fix (runtime config only — no repo files changed):** shorten the gateway timeout so it *fails fast* on the
doomed calls, recycling workers ~3× sooner. Applied as an env change on the Rollout:

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl patch rollout gateway --type json \
    -p '[{"op":"replace","path":"/spec/template/spec/containers/0/env/2/value","value":"1500"}]'
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl argo rollouts promote gateway --full
```

Config diff: `GATEWAY_TIMEOUT_MS: "5000"  →  "1500"` (payments held at 6000 ms latency for both runs).

**Before vs after** (Prometheus, `/pay` under a 6000 ms payments brownout):

```console
                            BEFORE (timeout 5000)      AFTER (timeout 1500)
  /pay p99 latency          7475 ms                    2485 ms          # fails ~3x sooner
  /pay throughput (504)     0.564 rps                  1.636 rps        # ~2.9x more requests recycled/s
  /events p99 (read)        25 ms                      25 ms            # reads unaffected in both
```

```console
# BEFORE
   p99 /reserve/{id}/pay 7475.0 ms
    /reserve/{id}/pay status=504 0.564 rps
# AFTER
   p99 /reserve/{id}/pay 2485.0 ms
    /reserve/{id}/pay status=504 1.636 rps
```

**What the fix traded off:** availability of *slow-but-would-eventually-succeed* requests — with a 1500 ms
timeout, any payment that legitimately needs 1.5–5 s is now killed with a 504 that a 5000 ms timeout would have
let through. In exchange we stop a slow dependency from exhausting gateway workers, so the blast radius of a
payments brownout is contained and healthy read traffic keeps flowing. (In production you'd pair a tight
timeout with a retry/circuit-breaker budget so transient slowness still gets a second chance.)
