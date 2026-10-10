# Lab 8 — Chaos Engineering — Submission

**Student:** jakefish18
**Repo:** https://github.com/jakefish18/SRE-Intro
**Branch:** `feature/lab8`

PR checklist:
```text
- [x] Task 1 done — 3 chaos experiments with hypotheses
- [x] Task 2 done — combined failure scenario
- [x] Bonus Task done — resilience improvement with before/after proof
```

> **Setup:** QuickTicket on k3d — `gateway` is an Argo Rollouts Rollout (5 replicas,
> from Lab 7), `events`/`payments`/`postgres`/`redis` are Deployments, in-cluster
> Prometheus in ns `monitoring` (Lab 7 bonus). The Lab 8 mixed loadgen
> (`labs/lab8/mixedload.yaml`, 2 replicas) drives the full checkout flow
> (`/events` → `/reserve` → `/pay`). Observations are Prometheus queries run via
> `kubectl exec -n monitoring deploy/prometheus -- wget -qO- '…'`. All hypotheses
> below were written **before** executing.

**Baseline (mixed load, steady state):**
```
RPS(1m) = 12.38          error_ratio(1m) = 0.0
per-path RPS: /events 5.69   /events/{id}/reserve 5.75   /health 1.0   /reserve/{id}/pay 0.0*
p99 latency: /events 0.016s   /events/{id}/reserve 0.098s   /health 0.086s
```
> *At baseline, `/pay` was 0 because the mixed loadgen only reserves **event 1**,
> whose inventory is fully consumed by outstanding Redis holds → all reserves return
> 409 → no `reservation_id` → no pay. For experiments that need real pay traffic
> (Exp 2, Task 2) I added a `paygen` deployment that reserves+pays the 500-ticket
> **event 3** (which the mixed loadgen never touches) and reset stock between runs
> (`DELETE FROM orders` + Redis `FLUSHALL`).

---

## Task 1 — Three Chaos Experiments

### Experiment 1 — Pod Kill Under Load

**HYPOTHESIS (written first):** *If I delete one gateway pod while traffic is
flowing, Kubernetes will schedule a replacement within a few seconds and **no
requests will fail (0 new 5xx)**, because the gateway runs 5 replicas behind a
ClusterIP Service — kube-proxy immediately stops routing to the deleted pod and
load-balances across the remaining 4 Ready pods, which have spare capacity. I
expect the surviving pods' per-pod request rate to rise ~25% during the gap, then
rebalance to 5 pods once the replacement is Ready.*

**Commands:**
```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
kubectl delete "$VICTIM"          # at T0
kubectl get pods -l app=gateway -w # watch replacement
# 5xx during transition + per-pod rate:
#   sum(increase(gateway_requests_total{status=~"5.."}[3m]))
#   sum by (pod)(rate(gateway_requests_total[1m]))
```

**Observations:**
```
T0 = 12:05:07Z   victim = gateway-784f5b8dcc-67c4h
RECOVERY: rollout back to 5/5 Ready after 11s (replacement pod scheduled + Ready)

5xx during the kill window:  sum(increase(gateway_requests_total{status=~"5.."}[3m])) = ~2.06
overall error_ratio (1m) after recovery = 0.0

per-pod request rate (1m) after recovery — traffic spread across all pods:
  gateway-...-l4n28  2.93 req/s
  gateway-...-9xt4j  2.85 req/s
  gateway-...-nj4g4  2.49 req/s
  gateway-...-r2b7t  2.44 req/s
  gateway-...-bxpk2  0.73 req/s   ← the replacement pod, ramping up
```

**Hypothesis vs reality:** Mostly correct — recovery was fast (11s) and the 4
survivors + Service load-balancing absorbed the traffic (no sustained error rate,
error_ratio back to 0). **The surprise:** it was *not* perfectly zero-failure —
`~2` requests returned 5xx during the kill. Deleting a pod is not a graceful drain:
the requests already in flight on the victim were dropped before kube-proxy removed
it from the endpoints. So "5 replicas = zero-downtime pod loss" is *almost* true, but
in-flight requests on the killed pod are collateral.

**To improve resilience against this failure, I would** add a `preStop` hook (a few
seconds `sleep`) + a sensible `terminationGracePeriodSeconds` to the gateway pod so
it is removed from Service endpoints and drains in-flight requests *before* the
process exits — turning the ~2 dropped requests into zero.

### Experiment 2 — Payment Latency Injection

**HYPOTHESIS (written first):** *If payments takes 2000 ms per request, the `/pay`
path's p99 latency will climb to ~2 s but there will be **no 5xx**, because
2000 ms < `GATEWAY_TIMEOUT_MS` (5000 ms) so the gateway waits and still gets a 200.
Read paths (`/events`) and `/reserve` should be **unaffected** (they don't call
payments). When I push latency to 6000 ms (> 5000 ms timeout), `/pay` should flip to
**504** after ~5 s — the gateway protecting itself with a timeout.*

**Commands:**
```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
# observe error ratio + p99 per path; then:
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000   # > timeout
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0      # restore
```

**Observations:**
```
BEFORE (latency=0):   error_ratio=0.0   /pay p99=0.193s

PAYMENT_LATENCY_MS=2000  (12:15:15Z):
  error_ratio = 0.0                         ← no 5xx (2000ms < 5000ms timeout)
  p99 /reserve/{id}/pay = 2.485s            ← /pay latency spiked to ~2.5s
  p99 /events = 0.043s, /reserve = 0.097s, /health = 0.086s   ← reads UNAFFECTED
  /pay status: 200 still flowing (no errors)

PAYMENT_LATENCY_MS=6000  (12:16:54Z, > 5000ms timeout):
  error_ratio = 0.0277                      ← elevated
  /pay status: 504 @ 0.368/s                ← gateway TIMED OUT and returned 504
  p99 /reserve/{id}/pay = 7.475s
  p99 /events = 0.018s, /reserve = 0.097s   ← reads STILL unaffected

RESTORE (latency=0): error_ratio decays back toward 0.
```

**Hypothesis vs reality:** **Fully confirmed.** At 2000ms the `/pay` p99 rose to ~2.5s
with **zero 5xx** (below the 5s timeout) and reads were completely unaffected — only
the path that calls payments degraded. At 6000ms (above the timeout) `/pay` flipped
to **504** and the overall error rate rose. **The most important lesson:** the 2000ms
degradation was *invisible to error-rate monitoring* (0% errors the whole time) — a
service can be badly slow while every request is technically "200 OK." Partial
degradation only shows up in **latency** signals, not error counts.

**To improve resilience against this failure, I would** add a **p99 latency SLO
alert** on `gateway_request_duration_seconds` for the `/pay` path (e.g. p99 > 1s for
2m) so slow-but-successful degradation pages someone — and add a circuit breaker on
the gateway→payments call so a slow payments dependency fast-fails instead of tying
up gateway workers.

### Experiment 3 — Redis Failure

**HYPOTHESIS (written first):** *If Redis goes down, listing events (`GET /events`)
will **still work** (it reads from Postgres and doesn't touch Redis), but reserving
tickets (`POST /events/{id}/reserve`) will **fail with 5xx** because the reservation
hold is stored in Redis, and `GET /health` will report **degraded/503** because the
events health check includes Redis connectivity. So a single dependency loss causes
**partial** degradation — reads survive, writes break.*

**Commands:**
```bash
kubectl scale deployment/redis --replicas=0
kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never -- \
  sh -c 'curl .../events; curl -X POST .../events/1/reserve; curl .../health'
kubectl scale deployment/redis --replicas=1   # restore
```

**Observations:**
```
BEFORE (redis up):   5 gateway pods READY 1/1,  gateway Service endpoints = 5

~25s AFTER `kubectl scale deployment/redis --replicas=0`:
   all 5 gateway pods -> READY 0/1  (NotReady)
   gateway Service endpoints (ready) = 0        ← gateway pulled from the Service!

probe from an already-running pod (DNS warm):
   GET  /events   -> HTTP 000 (curl exit 7, connection failed)
   POST /reserve  -> HTTP 000
   GET  /health   -> HTTP 000
prometheus /reserve status (1m): 200=0, 409=5.18, 504=0.236, 502=0.098  (no successful reserves)

AFTER `kubectl scale deployment/redis --replicas=1`:
   all 5 gateway pods -> READY 1/1,  endpoints = 5   (full recovery)
```

**Hypothesis vs reality:** **Wrong — and the surprise is the whole point.** I expected
*partial* degradation (list works, reserve breaks). Instead Redis dying caused a
**total gateway outage**: `events`' `/health` reports `redis: down` → the gateway's
`/health` returns 503 → but the gateway's **readiness probe *is* `/health`**, so all 5
gateway pods failed readiness, were removed from the Service endpoints (5 → 0), and
the entire gateway became unreachable — even `GET /events`, which doesn't touch Redis
at all. A single **non-critical** dependency failure **cascaded** into a full outage
via the readiness-probe design.

**To improve resilience against this failure, I would** decouple the gateway's
**readiness** from deep-dependency health — use a shallow readiness check (TCP / a
`/livez` that only reports "the process is up") so a Redis outage degrades *only* the
Redis-dependent path (`/reserve`) while `/events` keeps serving. (Implemented in the
Bonus below.)

---

## Task 2 — Combined Failure Scenario

**Scenario design (what + why):** *Degraded dependencies stacked* — payments at 30%
failure **and** 500 ms latency, `events` DB pool capped at `DB_MAX_CONNS=3`, and the
mixed load scaled to 3 replicas. Rationale: real incidents are rarely a single clean
failure; this stacks a *partial* backend failure (payments) with a *capacity*
constraint (DB pool) under *elevated load* to see which golden signal breaks first
and where latency amplifies.

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
# sample error ratio + p99 per path over 3–5 min
```

**Observations (3–5 min window):**
```
Injected 12:47:05Z. RPS steady ~22–23 (mixedload×3 + paygen). Samples:
  12:48:00Z  error_ratio=0.0227   p99: /pay 0.746s  /reserve 0.092s  /events 0.071s  /health 0.043s
  12:48:46Z  error_ratio=0.0245   p99: /pay 0.748s  /reserve 0.083s  /events 0.045s
  12:49:31Z  error_ratio=0.0165   p99: /pay 0.748s  /reserve 0.078s  /events 0.023s
  12:50:16Z  error_ratio=0.0039   p99: (/pay traffic tapered as event-3 stock sold out) /reserve 0.084s
  12:51:02Z  error_ratio=0.0016   p99: /reserve 0.081s  /events 0.03s
```
- **Which golden signal reacted first?** *Errors* — the moment payments started
  failing 30% of charges, `/pay` 5xx pushed the overall error ratio to ~2.4%
  immediately. *Latency* reacted on the `/pay` path only (p99 0.75s ≈ the injected
  500ms + processing). *Traffic* stayed flat (~22 RPS) and *saturation* (the DB pool)
  never visibly bit.
- **Worst latency amplification:** `/reserve/{id}/pay` — p99 ~**0.75s** vs a flat
  ~0.08s for `/events/{id}/reserve` and ~0.03s for `/events`. The payments latency
  amplified *only* the path that calls payments; reads and reserves were untouched.
- **Surprise:** capping `events` at `DB_MAX_CONNS=3` did **not** amplify `/reserve`
  latency at this load (~22 RPS) — the pool had enough headroom for the short
  queries, so the DB was *not* the bottleneck here. Payments dominated.

**Weakest link + how to make it resilient:** The **payments dependency** is the
weakest link — its failure *directly* became the user-facing error rate and its
latency was the only thing that amplified a p99. To make it more resilient: put a
**circuit breaker + bounded retry** on the gateway→payments call so a failing/slow
payments *fast-fails* instead of tying up gateway workers and burning error budget,
add a **p99 latency SLO alert** on `/pay`, and ideally make charging **asynchronous**
(enqueue the charge, confirm out-of-band) so a degraded payments service no longer
fails checkout synchronously.

---

## Bonus Task — Resilience Improvement

**Weakness chosen:** The Experiment 3 **cascade** — a Redis outage took down the
*entire* gateway (Service endpoints 5 → 0, every path returned `000`), because the
gateway's **readiness probe was the dependency-aware `/health`**. A non-critical
dependency failing made all gateway pods NotReady.

**What I changed (diff):** `k8s/gateway.yaml` — gateway readiness probe from the deep
`/health` to a shallow TCP check:
```diff
       readinessProbe:
-        httpGet:
-          path: /health
-          port: 8080
+        tcpSocket:
+          port: 8080
         periodSeconds: 5
         failureThreshold: 2
```

**Before vs after** (same experiment: `kubectl scale deployment/redis --replicas=0`):
```
                         BEFORE fix (/health readiness)     AFTER fix (tcpSocket readiness)
gateway pods ready       5/5 -> 0/5                         5/5 -> 5/5  (stays Ready)
gateway Service endpoints 5 -> 0                            5 -> 5      (stays in rotation)
GET /events during outage HTTP 000 (unreachable)            HTTP 200 @ 2.9/s  (~92% success)
outcome                  TOTAL outage (all paths 000)       PARTIAL degradation (reads work,
                                                            only Redis-dependent /reserve fails)
```
The fix converts a **total outage into partial degradation** — exactly the behavior I
originally (wrongly) hypothesized for Experiment 3.
> Honest note: the after-fix `/events` still showed a residual ~8% 502 — because the
> **events** Deployment uses the *same* dependency-aware readiness probe, so it flaps
> NotReady under the Redis outage one layer down. A complete fix applies the same
> shallow-readiness change to `events` too.

**What the fix traded off:** shallow readiness no longer auto-ejects a genuinely
broken gateway pod — a pod with all downstreams dead stays in rotation and returns
errors for the affected paths instead of being pulled. We trade "fail-fast pod
removal" for "stay up and keep serving what still works." (The cleaner long-term
answer is per-path load-shedding / graceful degradation rather than a binary
pod-level readiness gate that couples every endpoint to the health of one dependency.)
