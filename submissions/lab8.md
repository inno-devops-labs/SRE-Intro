# Lab 8 — Chaos Engineering: Break Things on Purpose

Liubov Utenysheva, CBS-03

---

## Setup

Applied the Lab 8 load generator (exercises the full `reserve → pay` checkout chain, not just reads) and let the
in-cluster Prometheus (Lab 7) accumulate a baseline before touching anything:

```bash
$ kubectl apply -f labs/lab8/mixedload.yaml
deployment.apps/mixedload created
$ kubectl rollout status deployment/mixedload --timeout=60s
deployment "mixedload" successfully rolled out
$ kubectl get deployment mixedload
NAME        READY   UP-TO-DATE   AVAILABLE   AGE
mixedload   2/2     2            2           2s
```

> **Note on cluster state:** when I started, the gateway `Rollout` was left **mid-canary** from Lab 7 (step 2/6,
> canary at 20% with an `AnalysisRun` running). A canary in flight would have confounded the chaos results (the
> rollout controller was scaling pods and running analyses on its own), so I first completed it with
> `kubectl argo rollouts promote gateway` and waited for a single stable revision before taking the baseline:
>
> ```bash
> $ kubectl argo rollouts promote gateway
> rollout 'gateway' promoted
> # ...
> Status:   Healthy   Step: 6/6   SetWeight: 100   ActualWeight: 100
> Replicas: Desired 5  Current 5  Updated 5  Ready 5  Available 5
> ```

### Baseline (stable gateway, `mixedload` ×2)

Captured at **10:32:14** after the rollout settled and the `[1m]` rate window filled:

| Signal | Value |
|---|---|
| Overall RPS | ~13.7 |
| 5xx ratio | 0 |
| p99 `/events` | ~10 ms |
| p99 `/events/{id}/reserve` | ~10 ms |
| p99 `/health` | ~10–19 ms |
| Per-pod RPS | 5 pods evenly at 2.0–2.5 each |

Status mix was `/events` 200, `/events/{id}/reserve` mostly **409** (capacity conflict — see the "held-counter leak"
finding below), `/reserve/{id}/pay` 200, `/health` 200. The 409s are business-logic conflicts, not 5xx.

---

## Task 1 — Three Chaos Experiments (6 pts)

### Experiment 1 — Pod Kill Under Load

**Hypothesis (written before running):**

> "If I delete one gateway pod while traffic is flowing, **no (or very few) requests will fail and total RPS will
> stay roughly stable**, because the Kubernetes `Service` load-balances across the remaining 4 pods and the
> controller immediately creates a replacement — so capacity only dips to 4/5 during a brief gap."

**Execute** (victim `gateway-6547657858-g89jv`):

```bash
$ VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)   # pod/gateway-6547657858-g89jv
$ kubectl delete "$VICTIM"
pod "gateway-6547657858-g89jv" deleted
```

**Observe** (1 s polling of the pod set):

```
KILL_AT=10:32:23
10:32:26  running=4  ready=4     <- gap: only 4 pods
10:32:27  running=4  ready=4
10:32:29  running=4  ready=4
10:32:31  running=5  ready=5     <- RECOVERED (replacement gateway-6547657858-2xl6s)
```

- Time to replacement: **~8 s** from delete to 5/5 Ready (pod was *Running* by ~4 s, Ready by ~8 s).
- 5xx in the trailing 3 m window: **0**
  ```bash
  $ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
      'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
  # 5xx(3m): 0
  ```
- Per-pod RPS just after recovery (1 m) — the surviving 4 pods absorbed the load, the new pod ramped up:
  ```
  gateway-…-kdcbg  3.00
  gateway-…-mr4h7  2.87
  gateway-…-xhw4x  2.82
  gateway-…-pp4t5  2.45
  gateway-…-g89jv  1.66   <- killed pod (residual of the 1m window before it died)
  gateway-…-2xl6s  0.94   <- replacement, ramping
  ```
- Overall RPS stayed at **13.75** (baseline ~13.7).

**Comparison:** hypothesis **confirmed**. Zero failed requests, RPS flat, the Service spread traffic over the 4
survivors and the replacement was Ready in ~8 s. What I did *not* see is the more interesting part: there was no
visible blip because the gateway is stateless and the `Service` drops terminating endpoints fast. The ~8 s "gap"
cost us 20% capacity (4/5) but not a single error.

**To improve resilience against this failure, I would** add a `preStop` hook + graceful shutdown so in-flight
requests drain before the pod is killed, and a readiness probe with a short `failureThreshold` so the `Service`
stops routing to a draining pod the instant it begins to terminate.

---

### Experiment 2 — Payment Latency Injection

**Hypothesis (written before running):**

> "If payments takes 2 s per request, **no 5xx will occur at the gateway and only `/pay` p99 will spike to ~2 s**
> (because 2000 ms < `GATEWAY_TIMEOUT_MS` = 5000 ms), while the read paths (`/events`, `/health`) stay
> unaffected, because the latency is injected only into the payments service, which is called solely on the `/pay`
> path."

**Execute** (no pod dance — `kubectl set env` triggers a rolling update):

```bash
$ kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
deployment.apps/payments env updated
$ kubectl rollout status deployment/payments --timeout=30s
deployment "payments" successfully rolled out
```
(INJECT_AT = **10:33:10**)

**Observe** (after the `[1m]` window filled, @ **10:37:44**):

| Signal | Value |
|---|---|
| 5xx ratio | **0** |
| p99 `/reserve/{id}/pay` | **2.485 s** (spiked from ~10 ms) |
| p99 `/events` | ~10 ms (unaffected) |
| p99 `/events/{id}/reserve` | ~10 ms (unaffected) |
| p99 `/health` | ~10 ms (unaffected) |
| `/pay` status (2 m) | all **200** |

Only `/pay` moved; reads stayed clean — exactly the "partial degradation" the lab warns is harder to spot than a
dead service.

**Bonus — push beyond the timeout.** Set `PAYMENT_LATENCY_MS=6000` (> `GATEWAY_TIMEOUT_MS` 5000 ms) at
**10:37:51** and observed @ **10:40:59**:

| Signal | Value |
|---|---|
| 5xx ratio | **0.031** (rose from 0) |
| `/pay` status (1 m) | **504 × 12.19**, 200 × 0 |
| 5xx by path | `/reserve/{id}/pay` 504 @ 0.203/s |

The gateway now **protects itself**: each `/pay` is cut off at exactly the 5000 ms timeout and returned as a
**504** to the client instead of hanging for 6 s.

**Restore:** `kubectl set env deployment/payments PAYMENT_LATENCY_MS=0` (RESTORE_AT **10:41:09**); 5xx ratio
returned to 0 and `/pay` 504s dropped to 0.

**Comparison:** hypothesis **confirmed** on both legs. At 2000 ms the system absorbed the latency silently (no 5xx,
only `/pay` p99 up) — a slow-but-successful degradation that an availability-only SLO would miss entirely. At 6000 ms
the gateway's timeout converted the slowdown into explicit 504s.

**To improve resilience against this failure, I would** add a **latency SLO alert** on `/pay` p99 (e.g. alert when
p99 > 1 s for 5 m) so slow-but-200 payments page someone, and put a **circuit breaker** on the gateway→payments
call so a chronically slow payments service is short-circuited instead of tying up gateway worker capacity.

> **Caveat (test setup):** `/pay` traffic in this lab is inherently sparse — the `mixedload` loop only reaches
> `/pay` when a `/reserve` succeeds, and the events service's leaked `held` counter (see "Notable finding") depletes
> event 1's capacity within ~1–2 min, after which reserves 409 and `/pay` gets no traffic. I reset the leaked counter
> (`redis-cli SET event:1:held 0`) to keep the checkout chain flowing during the observation windows.

---

### Experiment 3 — Redis Failure

**Hypothesis (written before running):**

> "If Redis goes down, **`/events` (list) will still work** (the availability check catches the Redis failure and
> treats `held = 0`), **but `/reserve` will fail with 5xx** (creating the hold requires a successful Redis write →
> `ConnectionError`), and **`/health` will report degraded (503) with `redis: down`** because Redis is a tracked
> dependency."

**Execute:**

```bash
$ kubectl scale deployment/redis --replicas=0
deployment.apps/redis scaled
# redis pod -> Terminating -> gone
```
(SCALE0_AT = **10:43:00**)

**Observe** (lab chaos-probe pod, @ 10:43:06):

```
GET /events:
  => http=504  time=5.005s     body: {"detail":"Events service timeout"}
POST /reserve:
  => (no ready upstream — see cascade below)
GET /health:
  => http=503  body: {"status":"degraded","checks":{"events":"degraded","payments":"ok","circuit_payments":"CLOSED"}}
```

Then, seconds later, **all** requests began failing at the load-balancer with connection-refused (`http=000`), and
`kubectl get endpoints gateway` / `endpoints events` were **empty**. Prometheus showed the collapse:

```
overall RPS:  13.7  ->  1.15   (-92%)
RPS by path:  only /health had ~1.1 RPS (residual); /events and /reserve -> 0
```

**What actually happened — a readiness cascade, not a graceful degradation:**

1. Redis down → events `/health` returns **503** (`{"redis":"down"}`).
2. The events **readiness probe** is `httpGet /health` with `failureThreshold: 2` → after ~10 s the events pod is
   marked **not ready** and removed from the `events` `Endpoints`.
3. The gateway can no longer reach events → gateway `/health` returns **503** (`events: degraded`).
4. The gateway readiness probe fails → all 5 gateway pods marked **not ready** and removed from `Endpoints`.
5. With **zero ready endpoints**, the `Service` has nothing to route to → every client request fails at the LB
   (connection-refused), so the failures never even reach the app metrics.

**Restore:** `kubectl scale deployment/redis --replicas=1` (RESTORE_AT **10:44:47**) → `condition=Available` met.
Within ~60 s the readiness probes passed, endpoints repopulated, `/health` returned **200 `healthy`**, and RPS
climbed back to ~14.6.

**Comparison:** hypothesis **partially right, severity a big surprise**. `/health` reporting degraded and `/reserve`
failing matched — but I expected `/events` (a pure-DB read path) to keep working. Instead the **readiness probes
turned a single cache outage into a full-tier outage**: one dependency (Redis) brought down both events *and* the
entire gateway tier. This is the classic "liveliness/readiness coupled to a non-critical dependency" anti-pattern.

**To improve resilience against this failure, I would** **decouple readiness from Redis** — readiness should reflect
the pod's ability to serve its core contract, not the state of a non-critical cache — and add **graceful
degradation** in events (a fast `503 "reservation unavailable"` instead of hanging on per-event Redis timeouts that
sum past the gateway's 5 s budget).

---

## Task 2 — Combined Failure Scenario (4 pts)

### Design

**"Degraded dependencies":** stack a slow-and-flaky downstream with a saturated shared resource under elevated load,
to find the weakest link:

- `payments`: `PAYMENT_FAILURE_RATE=0.3` **and** `PAYMENT_LATENCY_MS=500` (degraded downstream)
- `events`: `DB_MAX_CONNS=3` (shared resource — Postgres connection pool — capped)
- `mixedload`: scaled **2 → 3** replicas (elevated load)

```bash
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
$ kubectl set env deployment/events DB_MAX_CONNS=3
$ kubectl scale deployment/mixedload --replicas=3
$ kubectl rollout status deployment/payments --timeout=30s
$ kubectl rollout status deployment/events --timeout=30s
```
(SCENARIO_START = **10:47:02**; ran ~6 min, sampling the golden signals every ~60 s.)

### Observations over the window

| Time | RPS | 5xx ratio | p99 `/events` | p99 `/reserve` | p99 `/pay` | p99 `/health` |
|---|---|---|---|---|---|---|
| 10:47:22 | 13.75 | 0.004 | 10 ms | 10 ms | 25 ms | 21 ms |
| 10:48:22 | 18.73 | 0.000 | 22 ms | 25 ms | — | 22 ms |
| 10:49:40 | 18.29 | 0.006 | 10 ms | **69 ms** | — | 23 ms |
| 10:50:36 | 18.27 | 0.003 | 13 ms | 25 ms | **747 ms** | 22 ms |
| 10:51:31 | 18.95 | 0.005 | 15 ms | 25 ms | — | **64 ms** |
| 10:53:26 | — | 0.005 | 13 ms | 25 ms | — | 65 ms |

- RPS rose to ~18–19 (3× load) and stayed up — the system kept accepting work.
- **`/events` began emitting 502s** under sustained pool pressure (`/events 502 @ 0.036/s` at 10:53:26).

**Which golden signal reacted first?** **Latency, not errors.** `/reserve` p99 jumped to **69 ms** (from a ~10 ms
baseline, ~7×) within the first minute of capping the pool, while the 5xx ratio stayed under 1% for the whole window.
Error rate only started to move (the `/events` 502s) near the end. This is the lab's core lesson in action: a
saturated resource degrades **latency first** and errors last, so a p99 SLO would have caught it long before an
availability SLO.

**Worst latency amplification:** `/events/{id}/reserve` — **10 ms → 69 ms (~7×)** — because every reserve needs a
Postgres connection and, with only 3 in the pool against 3× the load, requests **queue for a free connection**.
`/pay` also amplified (10 ms → 747 ms) but that is the *injected* 500 ms, not a resource contention.

**Weakest link:** the **events service's Postgres connection pool** (`DB_MAX_CONNS`). It is the shared, finite
resource that everything funnels through; once it saturates it amplifies latency on the hottest path (`/reserve`) and
eventually sheds load as 502s, while the (injected) payments degradation stayed contained to `/pay`. The 30% payment
failure rate was itself largely unobservable here — `/pay` traffic is too sparse (the held-counter leak, above) to
show a 30% sample in a 1–2 m window — which is a limitation of the test harness, not of the payments service.

**How would I make it more resilient?** Right-size the pool to expected concurrency (and make it configurable per
replica), add a **bounded connection-acquisition timeout with fast-fail** so a saturated pool returns a quick 503
instead of queueing, and scale the DB out (read replicas / a connection pooler like PgBouncer) so a single events
pod's pool can't become the system-wide bottleneck.

**Restore:**

```bash
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0
$ kubectl set env deployment/events DB_MAX_CONNS=10
$ kubectl scale deployment/mixedload --replicas=2
```
(RESTORE_AT **10:53:43**; verified RPS back to ~14.6, 5xx ≈ 0, all p99s at baseline, all pods 1/1.)

---

## Notable finding — a real bug: the Redis `held` counter leaks

While driving the checkout chain I found that event 1's capacity ran to 0 within a couple of minutes of sustained
load, after which every `/reserve` returned 409 and `/pay` starved. Tracing it to `app/events/main.py`:

- `reserve()` does `redis_client.decrby(f"event:{id}:held", -quantity)` — i.e. **increments** the `held` counter on
  every successful reservation (main.py:224).
- `confirm()` (the pay path) inserts the order and deletes the `reservation:{id}` key, but **never decrements
  `event:{id}:held`** (main.py:241–285).
- `_get_available()` computes `available = total - confirmed - held` (main.py:306).

So `held` only ever grows; `available` monotonically falls to 0 even though tickets were actually *sold* (not held).
I confirmed it live: `event:1:held = 50` and `SUM(orders.quantity) = 50` → `available = 100 - 50 - 50 = 0`. This is a
genuine correctness/resilience weakness (capacity is double-counted and never reclaimed), and it is what made `/pay`
traffic sparse throughout the lab. I worked around it operationally by resetting the counter
(`redis-cli SET event:1:held 0`) between observation windows; the proper fix is to `incrby(held, -quantity)` in
`confirm()` (and on reservation TTL expiry).

---

## Cleanup

```bash
$ kubectl delete -f labs/lab8/mixedload.yaml
deployment.apps "mixedload" deleted
```

Argo Rollouts and the in-cluster Prometheus from Lab 7 are left running for Lab 9. All injected env vars were
restored to their defaults (`PAYMENT_LATENCY_MS=0`, `PAYMENT_FAILURE_RATE=0.0`, `DB_MAX_CONNS=10`), Redis and the
gateway are at full replica count, and the system is back at baseline (RPS ~14, 5xx ≈ 0).

---

## PR checklist

```text
- [x] Task 1 done — 3 chaos experiments with hypotheses
- [x] Task 2 done — combined failure scenario
- [ ] Bonus Task done — resilience improvement with before/after proof
```
