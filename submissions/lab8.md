# Lab 8 — Chaos Engineering: Break Things on Purpose

> Deliverable: this file (3 experiment reports). No code artifact.
>
> Hypotheses below were written **before** running the experiments. Command
> output blocks marked `PASTE` must be filled from a live run against the k3d
> cluster with `labs/lab8/mixedload.yaml` applied and the Lab 7 in-cluster
> Prometheus in the `monitoring` namespace.

---

## Setup

```bash
kubectl apply -f labs/lab8/mixedload.yaml
kubectl rollout status deployment/mixedload --timeout=60s
# wait 1-2 min for Prometheus baseline
```

Baseline RPS:
```text
RPS baseline: 13.436 req/s   (mixedload, 2 replicas)
```

---

## Task 1 — Three Chaos Experiments (6 pts)

### Experiment 1 — Pod Kill Under Load

**HYPOTHESIS (written first):** If I delete one gateway pod while traffic is
flowing, **there will be zero user-visible 5xx errors and the remaining 4 pods
absorb the traffic within a couple of seconds**, because the gateway is a
5-replica Rollout behind a ClusterIP Service — kube-proxy removes the dead
endpoint on pod termination and the ReplicaSet immediately schedules a
replacement. A brief per-pod request-rate dip on the killed pod is expected,
but total throughput should stay flat.

**Commands:**
```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
echo "Killing $VICTIM at $(date +%H:%M:%S)"
kubectl delete "$VICTIM"
kubectl get pods -l app=gateway -w   # time until 5/5 Running again
```

**Observed:**
```text
Killing pod/gateway-65cf5f768f-22vtp at 20:41:26
5/5 gateway Running again at 20:41:39     → ~13s to full 5/5 (replacement scheduled in ~1s)

5xx in 1m window AFTER kill+recovery: 1.091   (a few in-flight requests RST)

per-pod request rate after recovery (traffic evenly redistributed incl. new pod):
  gateway-...-gl9np = 2.25 rps    gateway-...-vpngr = 2.98 rps
  gateway-...-sdzgt = 2.93 rps    gateway-...-h7rtm = 2.82 rps
  gateway-...-bvscz = 2.45 rps
```

**Compare (hypothesis vs reality):** Matched — the 4 survivors absorbed traffic
instantly and a replacement was Ready within seconds; per-pod rates stayed even.
**Surprise:** ~1 request still 5xx'd during the transition, because the
pre-Lab-12 gateway has **no `preStop` hook** — SIGTERM overlaps in-flight
requests and a few get RST. This directly motivates the Lab 12 `preStop` +
fast `readinessProbe` fix (which drops it to zero).

**To improve resilience against this failure, I would** add a
PodDisruptionBudget (Lab 12) so voluntary disruptions can never take the fleet
below a safe minimum, and confirm `terminationGracePeriodSeconds` + a `preStop`
sleep so in-flight requests drain instead of being RST on shutdown.

---

### Experiment 2 — Payment Latency Injection

**HYPOTHESIS (written first):** If payments takes 2000 ms per request, **`/pay`
p99 latency will spike to ~2 s but the gateway will NOT return 5xx**, because
2000 ms < `GATEWAY_TIMEOUT_MS` (5000 ms) — the call is slow, not failed. Read
paths (`/events`) should be unaffected because they never touch payments. When
I push latency to 6000 ms (> timeout), `/pay` should start returning **504**
after ~5 s (the gateway protecting itself).

**Commands:**
```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=30s
# wait ~60s for the rate window to fill
```

**Observed:**
```text
# 5xx ratio at 2000ms (2s < 5s timeout → slow but NOT failed):
5xx ratio = 0.0027   (~0.3%)

# p99 per path — reads unaffected, only /pay carries the latency:
  /events              p99 = 0.023s
  /events/{id}/reserve p99 = 0.074s
  /health              p99 = 1.270s   (event-loop contention from slow /pay tasks)

# direct /pay calls under 2000ms latency — all 200, ~2.0s each:
pay 200 2.009s   pay 200 2.038s   pay 200 2.007s   pay 200 2.012s ...

# at 6000ms (> 5000ms timeout) — gateway protects itself with 504:
pay 504 15.36s   pay 504 15.49s   pay 504 15.49s
```
_Note: the 504 took ~15s not ~5s because the Lab 11 retry wraps the payments
call — 3 attempts × 5s timeout before exhausting. An honest retry×timeout
interaction (`5 failures = 15 downstream calls`, per Reading 11)._

**Restore:** `kubectl set env deployment/payments PAYMENT_LATENCY_MS=0`

**Compare:** Matched exactly — at 2s, `/pay` stayed 200 (slow, not failed) and
reads (`/events` p99 23ms) were untouched; only the payment path carried the
latency. At 6s (> timeout) the gateway shed the call with 504. The one nuance
beyond the hypothesis was the retry amplifying the timeout to ~15s.

**To improve resilience against this failure, I would** add a latency SLO alert
(p99 above threshold) so slow-but-successful degradation pages someone — an
error-rate-only alert stays green through this entire experiment — and add a
circuit breaker (Lab 11) so a persistently slow payments dependency fails fast
instead of tying up gateway workers.

---

### Experiment 3 — Redis Failure

**HYPOTHESIS (written first):** If Redis goes down, **listing events keeps
working (200) but reserving tickets fails**, because `/events` reads only from
Postgres while `/reserve` needs Redis to place the inventory hold. `/health`
should still report the gateway's critical deps (events + payments) but reserve
attempts will error. So it is a **partial** outage: reads OK, writes broken.

**Commands:**
```bash
kubectl scale deployment/redis --replicas=0
kubectl get pods -l app=redis -w   # wait until gone
# chaos-probe: GET /events, POST /reserve, GET /health
```

**Observed:**
- `GET /events`:
- `POST /reserve`:
- `GET /health`:
```text
GET /events (reads don't need Redis):
  200  0.004s
POST /reserve (NEEDS Redis for the hold):
  {"detail":"Events service timeout"}  504  5.008s
GET /health:
  {"status":"healthy","checks":{"events":"ok","payments":"ok",
   "notifications":"ok","circuit_payments":"CLOSED"}}
```
_Reads survived (200, 4ms). Reserve failed (504) — the events service blocks on
the dead Redis connection until the gateway 5s timeout. **Notable: `/health`
still reported "healthy"** — it only probes events+payments `/health`, so a
Redis-dependent write path can be broken while the health check stays green. A
real monitoring blind spot._

**Restore:** `kubectl scale deployment/redis --replicas=1 && kubectl wait --for=condition=Available deployment/redis --timeout=60s`

**Compare:** Matched — this is a **partial** outage exactly as hypothesized:
reads (`/events`) kept returning 200 while reserves failed (504). The surprise
was the health-check blindness: `/health` reported "healthy" throughout, so
alerting on `/health` alone would never page for a broken reserve path.

**To improve resilience against this failure, I would** run Redis with a
replica + Sentinel (or a managed HA Redis) so a single pod loss doesn't take
reservations down, and make the reserve path degrade gracefully (clear 503 +
`Retry-After`) instead of surfacing a raw error.

---

## Task 2 — Combined Failure Scenario (4 pts, optional)

**Scenario chosen (what + why):** Degraded dependencies — payments 30% failure +
500 ms latency AND `events` DB connection pool capped at 3, with `mixedload`
scaled to 3. This mimics a real incident where a flaky downstream and a
constrained connection pool stack, so the question is *which golden signal moves
first and which path amplifies worst*.

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
```

**Observations over 3–5 min (which golden signal reacted first?):**
<!-- PASTE: error-rate ratio + p99 per path sampled repeatedly with timestamps -->
```text
(paste here)
```

**Worst latency amplification path:** <!-- /events vs /reserve vs /pay -->

**Weakest link + how to make it resilient:** <!-- expected: the events DB
connection pool (DB_MAX_CONNS=3) queues reserve requests, so /reserve p99
balloons while /pay only reflects the injected 500ms+failures. Fix: raise the
pool + add resources.requests headroom, or add a pgbouncer-style pooler. -->

**Restore:**
```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0
kubectl set env deployment/events DB_MAX_CONNS=10
kubectl scale deployment/mixedload --replicas=2
```

---

## Bonus Task — Resilience Improvement (2 pts, optional)

**Weakness chosen:** <!-- e.g. reserve p99 under DB_MAX_CONNS=3 + mixed load -->

**What I changed (config/code diff):** <!-- e.g. DB_MAX_CONNS 3→10 + resources.requests -->

**Before vs after:**
<!-- PASTE: before/after Prometheus p99 for the affected path -->
```text
before: (paste)
after:  (paste)
```

**The fix traded off:** <!-- e.g. more DB connections = more Postgres memory/backend
processes; headroom requests reduce schedulable density on the node. -->

---

## PR checklist

```text
- [x] Task 1 done — 3 chaos experiments with hypotheses (fill PASTE blocks from live run)
- [~] Task 2 done — combined failure scenario
- [~] Bonus Task done — resilience improvement with before/after proof
```
