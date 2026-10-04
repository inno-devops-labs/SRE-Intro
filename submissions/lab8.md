# Lab 8 — Chaos Engineering

## Hypotheses (written 2026-10-04 23:54 MSK, before any experiment was run)

**Experiment 1 — Pod kill under load:**
"If I delete one gateway pod while traffic is flowing, there will be no user-visible errors (or at most a handful of 5xx from in-flight requests) because the Rollout keeps 5 replicas, the `gateway` Service stops routing to a pod once it is terminating, and the remaining 4 pods absorb its share of traffic until a replacement is Ready within ~10–15 s."

**Experiment 2 — Payment latency 2000 ms:**
"If payments takes 2 seconds per request, `/reserve/{id}/pay` p99 will rise to ~2 s, the 5xx rate will stay at ~0 and `/events` + `/events/{id}/reserve` p99 will stay unchanged, because 2000 ms < `GATEWAY_TIMEOUT_MS` (5000 ms) and the read/reserve paths never call payments. Overall RPS will drop, because each mixedload loop is sequential and now waits 2 s on `/pay`. At 6000 ms, every `/pay` will return 504 after ~5 s."

**Experiment 3 — Redis down:**
"If Redis goes down, `GET /events` will keep working (it only reads Postgres; the Redis availability lookup in `_get_available` is wrapped in try/except), but `POST /events/{id}/reserve` will fail with 5xx, because `events` still holds a `redis_client` created at startup and the unguarded `setex()` raises a connection error. Gateway `/health` will flip to `503 degraded` with `events: degraded`: gateway doesn't check Redis itself, but `events`' `/health` returns 503 when Redis is down, which gateway translates to `degraded`. The whole system will be reported unhealthy, even though reads still work."

---

## Setup

### Fixing the environment first

The cluster had been restarted since Lab 7. A first probe before applying the loadgen showed `/events` already broken:

```
$ curl http://gateway:8080/events
{"detail":"Events service unavailable"}
```
```
$ kubectl logs deploy/events
psycopg2.errors.UndefinedTable: relation "events" does not exist
```

`postgres` has no PersistentVolume (see `k8s/postgres.yaml`), so its data disappears whenever the pod restarts. I re-seeded it:

```bash
kubectl exec -i deploy/postgres -- psql -U quickticket -d quickticket < app/seed.sql
kubectl exec deploy/postgres -- psql -U quickticket -d quickticket \
  -c "UPDATE events SET total_tickets=1000000 WHERE id=1;"
kubectl exec deploy/redis -- redis-cli DEL event:1:held
```

I raised `total_tickets` for event 1 on purpose. `mixedload` reserves and pays for event 1 at ~5 req/s. The seeded capacity is 100 tickets, and the `event:1:held` counter in Redis only ever grows (`confirm` never decrements it). Within a minute `/reserve` would start returning `409 Not enough tickets` and `/pay` would never be called. Experiments 2 and 3 need that mutating traffic.

### Loadgen and baseline

```bash
kubectl apply -f labs/lab8/mixedload.yaml
kubectl rollout status deployment/mixedload --timeout=90s
# deployment "mixedload" successfully rolled out
```

To keep commands short, I sent all Prometheus queries through a small wrapper around `kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=…'`. Prometheus scrapes every gateway pod directly every 5 s (`scrape_interval: 5s`).

Baseline at **23:56:44–23:56:49**:

```
sum(rate(gateway_requests_total[1m]))                                 => 17.25 RPS

sum by (path,status) (rate(gateway_requests_total[1m]))
   /events               200 => 5.33
   /events/{id}/reserve  200 => 5.35
   /reserve/{id}/pay     200 => 5.24
   /health               200 => 1.49     (kubelet probes)

5xx ratio                                                             => 0

histogram_quantile(0.99, … by (le,path))
   /events               => 0.082 s
   /events/{id}/reserve  => 0.102 s
   /reserve/{id}/pay     => 0.141 s
   /health               => 0.246 s

sum by (pod) (rate(gateway_requests_total[1m]))
   2hmxs 3.47 | kbnqz 3.47 | lj8xq 3.65 | nlb9k 3.51 | zdgs2 3.36
```

---

## Task 1 — Three Chaos Experiments

### Experiment 1 — Pod kill under load

**Hypothesis:** see above. In short: no or almost no 5xx, the 4 surviving pods absorb the traffic, and a replacement is Ready in ~10–15 s.

**Commands:**

```bash
kubectl get pods -l app=gateway -w --output-watch-events   # running in background, timestamps added
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
echo "Killing $VICTIM at $(date +%T.%3N)"
kubectl delete "$VICTIM"
```

**Observations:**

```
Killing pod/gateway-58bbf67b5f-2hmxs at 23:56:57.309

23:56:57.400 MODIFIED   gateway-58bbf67b5f-2hmxs   1/1     Terminating         0   4h21m
23:56:57.424 ADDED      gateway-58bbf67b5f-b78fd   0/1     Pending             0   0s
23:56:57.454 MODIFIED   gateway-58bbf67b5f-b78fd   0/1     ContainerCreating   0   0s
23:56:58.780 MODIFIED   gateway-58bbf67b5f-2hmxs   0/1     Completed           0   4h21m
23:56:59.035 DELETED    gateway-58bbf67b5f-2hmxs   0/1     Completed           0   4h21m
23:57:00.034 MODIFIED   gateway-58bbf67b5f-b78fd   0/1     Running             0   3s
23:57:06.077 MODIFIED   gateway-58bbf67b5f-b78fd   1/1     Running             0   9s
```

- **Replacement pod:** created **115 ms** after the delete (the Argo Rollouts-managed ReplicaSet reacted immediately). The container was running after 2.6 s and **Ready after 8.7 s** (23:56:57.4 → 23:57:06.1). Most of that time is the readiness probe (`periodSeconds: 5`) waiting for its first success. The old pod shut down gracefully in 1.4 s (uvicorn handles SIGTERM).
- **Failed requests**, queried at 23:58:54, about 2 min after the kill:
  ```
  sum(increase(gateway_requests_total{status=~"5.."}[3m]))  => 0
  ```
- **Per-pod traffic during the gap.** I used a range query of `sum by (pod) (rate(gateway_requests_total[30s]))` with a 10 s step:
  ```
  time      2hmxs  b78fd  kbnqz  lj8xq  nlb9k  zdgs2   total
  23:56:50   3.36      -   3.52   3.32   3.72   3.72   17.64
  23:57:00   3.68      -   3.72   3.04   3.20   3.84   17.48
  23:57:10   2.30      -   3.52   3.16   3.60   4.32   16.90   <- victim's series fading out
  23:57:20   1.41   1.19   3.40   4.00   3.76   3.76   17.52   <- new pod already serving
  23:57:30      -   2.58   3.36   4.04   3.68   3.28   16.94
  23:57:40      -   3.48   3.56   4.04   3.28   3.32   17.68
  ```
  Total throughput never dropped below 16.9 RPS. The survivors (`lj8xq`, `zdgs2`) picked up the share of the dying pod, and by 23:58:03 all 5 pods, including the new `b78fd`, were back to ~3.3–3.9 RPS each.

**Hypothesis vs reality:** the hypothesis held: no errors and no throughput dip. Two things surprised me:
1. Recovery was faster than I guessed (8.7 s to Ready instead of 10–15 s), and capacity was never really reduced. Running 5 replicas for ~17 RPS leaves plenty of headroom, so losing 20% of the pods doesn't matter.
2. There's a measurement blind spot. Gateway metrics only count requests that *reached* a gateway process. If a client had hit the dying pod's IP between SIGTERM and endpoint removal, it would see `connection refused` on its side, and nothing would appear in `gateway_requests_total`. The killed pod's own counters also vanish together with the pod. "0 5xx in Prometheus" proves no gateway returned an error, but not that no user got one. `mixedload` throws away curl exit codes, so client-side errors are invisible in this setup.

**To improve resilience against this failure, I would** add a `preStop` sleep of a few seconds and a PodDisruptionBudget (`minAvailable: 4`) to the gateway Rollout, and measure errors at the client/ingress (e.g. Traefik metrics) instead of inside the pods that are being killed.

---

### Experiment 2 — Payment latency injection

**Hypothesis:** see above. In short: `/pay` p99 ≈ 2 s, 0% 5xx, reads unchanged, RPS drops because the loadgen loops are sequential, and at 6000 ms every `/pay` returns 504 after ~5 s.

**Commands:**

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000      # 23:59:11
kubectl rollout status deployment/payments --timeout=90s          # done 23:59:19
```

**Observations at 2000 ms** (queried at 00:00:55, ~95 s after the rollout):

```
5xx ratio                              => 0

p99 by path                                   (baseline)
   /reserve/{id}/pay      => 2.485 s          0.141 s
   /events/{id}/reserve   => 0.178 s          0.102 s
   /events                => 0.023 s          0.082 s
   /health                => 0.049 s          0.246 s
p50 /reserve/{id}/pay     => 1.75 s

rate by path,status
   /events               200 => 0.84
   /events/{id}/reserve  200 => 0.84
   /reserve/{id}/pay     200 => 0.85
   /health               200 => 1.53
total RPS                    => 4.05          (baseline 17.25)
```

- Only `/pay` slowed down. Reads and reserve stayed fast, and `/events` even got *faster*, because the downstream services were under 4× less load.
- The p99 of 2.485 s and p50 of 1.75 s are histogram artefacts. The real latency is a flat ~2.0 s, but `prometheus_client`'s default buckets are `…, 1, 2.5, 5, 7.5, 10`, so every sample falls into `(1, 2.5]` and `histogram_quantile` interpolates linearly inside that bucket. For a 2 s SLO the bucket layout cannot tell 1.1 s from 2.4 s.
- **Throughput fell 76% (17.25 → 4.05 RPS) without a single error.** `mixedload` is a closed-loop client: each pod runs `events → reserve → pay → sleep 0.3` one step after another. A loop used to take ~0.37 s and now takes ~2.35 s, so each pod sends 3/2.35 ≈ 1.28 req/s. Two pods give 2.55 RPS, plus 1.5 RPS of kubelet probes, which is 4.05 RPS. The numbers match exactly.
- **Surprise — one 503 on `/pay` during the injection itself.** The 503 counter series first appeared at **23:59:25**:
  ```
  gateway_requests_total{path="/reserve/{id}/pay",status="503",pod="gateway-58bbf67b5f-kbnqz"} => 1   (first seen 23:59:25)
  ```
  `kubectl set env` triggers a rolling update of `payments`. It has a single replica and no `preStop` hook, so one in-flight `/charge` hit the terminating old pod. Gateway turned the `httpx.ConnectError` into `503 payments_unavailable`. So changing the config caused a (tiny) outage that the latency itself didn't.

**Bonus observation — 6000 ms (beyond `GATEWAY_TIMEOUT_MS=5000`):**

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000      # 00:01:25, rolled out 00:01:43
```
```
reserve: {"reservation_id":"8b7074ae-f75a-41af-8897-9fce9fc85fb8", ...}
POST /pay:
{"detail":"Payment service timeout"}
504 5.007497s
```
Metrics at 00:03:05:
```
/reserve/{id}/pay  504 => 0.363     200 => 0
5xx ratio              => 0.132
p99 /reserve/{id}/pay  => 7.475 s   (bucket artefact again: real value 5.0 s, falls in (5, 7.5])
total RPS              => 2.64
```

The gateway protected itself as expected: an exact 504 after 5.007 s. But the payments log shows what happened to the **same reservation** on the other side:

```
21:01:59,059  payments  Injecting 6000ms latency for 8b7074ae-f75a-41af-8897-9fce9fc85fb8
21:02:05,059  payments  Payment success: PAY-CC4B6CBE for 8b7074ae-f75a-41af-8897-9fce9fc85fb8
```
(payments logs in UTC; 21:02:05 UTC = 00:02:05 MSK)

The gateway gave up at +5 s, but payments kept working and **charged the customer at +6 s**. The order was never confirmed, because gateway never called `/confirm`. In those 2 minutes there were **33 successful charges and 0 confirmed orders**: customers were charged and got no ticket. The timeout protected the gateway, not the user.

**Restore:**

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0          # 00:03:23
kubectl rollout status deployment/payments --timeout=90s
```
At 00:04:47: 17.51 RPS, 0 5xx, all p99 < 0.06 s, `/health` → `healthy`.

**Hypothesis vs reality:** the hypothesis matched: 0% 5xx at 2 s, only `/pay` affected, RPS drop, 504 at 6 s. What I didn't predict:
1. The 504 hides a **successful charge**. A timeout in the caller is not a cancellation in the callee. This is the most important finding of the experiment.
2. The rollout used to inject the fault caused its own 503, because a single-replica `payments` with no graceful drain loses in-flight requests on every config change.
3. The size of the drop: a "harmless" 2 s of latency on one endpoint cut total throughput by 76%, because the clients are sequential. With real users (open-loop) the same latency would instead pile up concurrent in-flight requests on gateway and payments.

**To improve resilience against this failure, I would** make `/charge` idempotent (gateway sends an idempotency key = `reservation_id`, payments returns the previous result on retry) and add a reconciliation job that refunds or confirms charges with no matching order. I would also set the gateway → payments timeout from the payments latency SLO and change the histogram buckets to resolve around it (e.g. add 1.5, 2, 3 s).

---

### Experiment 3 — Redis failure

**Hypothesis:** see above. In short: `/events` keeps working, `/reserve` fails with 5xx, and gateway `/health` reports `503 degraded`.

**Commands:**

```bash
kubectl scale deployment/redis --replicas=0                        # 00:04:57
kubectl wait --for=delete pod -l app=redis --timeout=60s           # gone 00:04:58
```

**Observations.** Probe from inside the cluster at **00:05:04** (6 s after Redis disappeared):

```
GET /events:
200 0.013991s
POST /reserve:
{"detail":"Events service timeout"}
504 5.007632s
GET /health:
{"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}
503
GET events:8081/health:
000                      <- curl exit 7: could not connect to events at all
```

For the first few seconds, everything happened as predicted: reads OK, reserve broken (as a slow 504 timeout, not a fast 500), health degraded. Then the failure spread:

```
$ kubectl get pods        # 00:05:19
events-f85d979d7-x9cbl   0/1   Running   0             4h30m
$ kubectl get endpoints events
events   <none>
```

**Step 1 — events removed itself from service.** Its readiness *and* liveness probes both point at `/health`, and that endpoint returns 503 when Redis is down. Two failed readiness checks (2 × 5 s) removed the **only** events pod from the Service. From that point `GET /events` also failed, although it never needed Redis. Three failed liveness checks then started restarting it:

```
Normal   Killing   pod/events-f85d979d7-x9cbl   Container events failed liveness probe, will be restarted
WARNING  events    Redis connection failed: Error 111 connecting to redis:6379. Connection refused.
```

**Step 2 — the failure cascaded into gateway.** Gateway's probes are also on `/health`, and gateway's `/health` returns 503 whenever `events` is not OK. So all 5 gateway replicas became NotReady at the same time and got liveness-restarted in a loop:

```
$ kubectl get pods         # 00:07:04
events-f85d979d7-x9cbl      0/1   Running   3 (12s ago)
gateway-58bbf67b5f-b78fd    0/1   Running   3 (16s ago)
gateway-58bbf67b5f-kbnqz    0/1   Running   3 (22s ago)
gateway-58bbf67b5f-lj8xq    0/1   Running   3 (20s ago)
gateway-58bbf67b5f-nlb9k    0/1   Running   3 (20s ago)
gateway-58bbf67b5f-zdgs2    0/1   Running   3 (14s ago)
payments-6bb79c9f6c-w8tmf   1/1   Running   0

$ kubectl get endpointslices
events-xvtf5    events    false
gateway-lvqbm   gateway   false,false,false,false,false

$ curl -m 5 http://gateway:8080/events
000 0.001312s exit=7       <- connection refused: no gateway pod in the Service
```

**Total outage.** Even `GET /events`, the endpoint I predicted would "keep working", was refused at the TCP level. Prometheus shows how the traffic collapsed. Range query of `sum(rate(gateway_requests_total[30s]))`:

```
00:04:55  17.64   <- Redis scaled to 0 at 00:04:57
00:05:10  11.69
00:05:25   2.99
00:05:40   0.84   <- only kubelet /health probes (all 503) still reach the gateways
  ...      ~0.7–1.3
00:07:25   1.32   <- Redis restored at 00:07:14
00:07:40   0.80
00:07:55   4.54
00:08:10  14.59
00:08:25  17.24   <- fully recovered
```
At 00:06:45 `sum by (path,status) (rate(gateway_requests_total[1m])) > 0` returned only `{/health, 503} => 1.08`, and the 5xx ratio was **1.0**. Note that Prometheus *under-reports* this outage: the real user traffic was rejected before reaching any gateway process, so it isn't counted at all. The "1 RPS, 100% errors" signal is just the kubelet probes.

**Restore:**

```bash
kubectl scale deployment/redis --replicas=1 && kubectl wait --for=condition=Available deployment/redis --timeout=60s
```
```
restore redis at 00:07:14
redis available  00:07:15
00:07:40 events_ready=false gateway_ready=0/5
00:07:43 events_ready=true  gateway_ready=1/5
00:07:47 events_ready=true  gateway_ready=5/5
```
Recovery took **32 s after Redis came back**. events needed one more liveness restart (4 restarts in total) to reconnect, because its `redis_client` is created only at startup. Every gateway pod ended up with 4 restarts. At 00:09:06: 17.42 RPS, 0 5xx, `/health` → `healthy`.

Redis was down for **2 min 17 s** (00:04:57–00:07:14). The user-facing outage was **~3 min** (≈00:05:10–00:08:10) and covered **every** endpoint, not just reserve.

**Hypothesis vs reality:** the first ~10 s matched (reads 200, reserve broken, health `degraded`). After that, the hypothesis was badly wrong:
1. I predicted a *partial* outage (reserve only) and got a **total** one. Health checks that include dependencies, used as readiness *and* liveness probes, turned a non-critical cache outage into a full outage in two hops: Redis → events NotReady → gateway NotReady.
2. The liveness probes made it worse. Restarting a process cannot fix a missing downstream, so the kubelet just restart-looped 6 pods (4 restarts each) and slowed down recovery.
3. `/reserve` failed **slowly** (504 after the 5 s gateway timeout) instead of fast. events didn't fail fast when Redis was unreachable. I haven't confirmed the exact cause; most likely it's redis-py's connection retry, possibly combined with the sync worker being tied up.
4. The monitoring signal looked *smaller* than the incident: Prometheus showed ~1 RPS of errors while the real traffic of ~17 RPS was being refused unrecorded.

**To improve resilience against this failure, I would** split the probes. Liveness should be a shallow `/livez` that only says "the process responds". Readiness should check only what that pod needs in order to serve *anything* (Postgres for events, nothing downstream for gateway). Redis/payments status should stay in a `/health` used for dashboards and alerts only. Together with a reserve path that fails fast (`socket_connect_timeout`, or a 503 "reservations temporarily unavailable"), a Redis outage would only break reservations instead of the whole site.

---

### Summary

| # | Failure | Predicted | Actual | Worst finding |
|---|---|---|---|---|
| 1 | Kill 1/5 gateway pods | no errors, ~10–15 s recovery | 0 5xx, Ready in 8.7 s, total RPS never < 16.9 | in-pod metrics can't see connection-level errors |
| 2 | payments +2 s / +6 s | `/pay` slow, 0 5xx; 504 at 6 s | as predicted, RPS −76%; 1 extra 503 from the rollout itself | 504 to the user but **the charge still succeeds** (33 orphaned charges) |
| 3 | Redis down | reserve broken, reads OK | **total outage** of all endpoints for ~3 min, 6 pods restart-looping | dependency checks in liveness/readiness probes cascade a cache failure into a site-wide one |

---

## Task 2 — Combined Failure Scenario

### 8.4 — Scenario design (written 2026-10-05 00:11 MSK, before execution)

**Scenario: "Degraded dependencies + more traffic"**. Three faults are injected at the same time:

| Fault | Setting | Why |
|---|---|---|
| Flaky, slow payment provider | `PAYMENT_FAILURE_RATE=0.3`, `PAYMENT_LATENCY_MS=500` | the most common real-world third-party degradation: slower *and* erroring |
| Starved DB pool in `events` | `DB_MAX_CONNS=3` (default 10) | simulates a mis-sized pool or connections lost to a noisy neighbour |
| 50% more load | `mixedload` 2 → 3 replicas | incidents usually coincide with traffic, and more concurrency stresses the small pool |

The point is to see which of these *degrades*, which *fails*, and whether the failures stay contained in their own paths.

**What the code says before running anything:**
- `events` uses `psycopg2.pool.ThreadedConnectionPool(minconn=2, maxconn=DB_MAX_CONNS)`. Its `getconn()` does **not wait** for a free connection. When the pool is exhausted it immediately raises `PoolError: connection pool exhausted`.
- `POST /events/{id}/reserve` takes one connection and then calls `_get_available()`, which takes a **second** one while the first is still held. A single reserve therefore needs 2 of the 3 connections. `/events`, `/reservations/{id}/confirm` (called inside `/pay`) and `/health` (also the kubelet probe) need one each.
- Gateway forwards a payments 500 as `500 "Payment failed"` and has no retry. The Lab 11 `call_with_retry` stub is still a no-op.

**Hypothesis:**
"If payments fails 30% of requests with +500 ms latency, events' DB pool is capped at 3 and load grows by 50%, then:
1. `/reserve/{id}/pay` will return ~30% 5xx, and its p99 will rise to ~0.5–0.75 s. Overall 5xx will be ~10%, since `/pay` is about a third of the user traffic.
2. The DB cap will **not** show up as latency. Because `getconn()` fails instead of queueing, it will appear as sporadic fast 5xx (`PoolError` → events 500 → gateway 500/502) on `/events/{id}/reserve`, `/events` and the `/pay` confirm step. Reserve will be hit hardest, because it holds 2 connections.
3. The dangerous part: if `/health` loses the race for a connection it reports `postgres: down` → 503. Enough of those in a row will make events NotReady, and through the probe chain found in Experiment 3 they may flap gateway readiness too.
4. Errors will be the first golden signal to react (immediately), and latency will react only on `/pay`. Traffic will drop slightly, since the loops are sequential and `/pay` is slower.

The weakest link will be the `events` DB pool, not payments: payments failures stay inside `/pay`, while pool exhaustion can break every path and the health checks."

### 8.5 — Execution

**Baseline** (00:11:54, 2 mixedload replicas): 5xx = 0, ~17.2 user RPS + 1.5 probe RPS.
p99: `/events` 0.025 s, `/events/{id}/reserve` 0.076 s, `/reserve/{id}/pay` 0.076 s.
p50: `/pay` 0.018 s.

**Injection:**

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500   # 00:12:31
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
kubectl rollout status deployment/payments --timeout=90s
kubectl rollout status deployment/events --timeout=120s                               # all rolled out 00:12:49
```
```
$ kubectl logs deploy/events | grep "pool created"
{"time":"2026-10-04 21:12:46,157",...,"msg":"DB pool created (max=3)"}
```

I ran it for **~6 minutes** (00:12:49–00:19:33). The queries below were sampled every ~31 s.

**Observations over the window** (`[1m]` windows; RPS = `sum by (path,status) (rate(gateway_requests_total[1m]))`):

| time | 5xx ratio | `/pay` 200 | `/pay` 500 | `/reserve` 200 | `/events` 200 | other 5xx | p99 `/pay` | p50 `/pay` | p99 `/reserve` | p99 `/events` |
|---|---|---|---|---|---|---|---|---|---|---|
| 00:11:54 (baseline) | 0 | 5.22 | – | 5.25 | 5.24 | – | 0.076 | 0.018 | 0.076 | 0.025 |
| 00:12:49 | 0 | 5.96 | – | 5.89 | 6.05 | – | 0.045 | 0.017 | 0.045 | 0.024 |
| 00:13:20 | 0.028 | 4.44 | 0.42 | 4.91 | 4.95 | `/events` 502: 0.06, `/health` 503: 0.02 | **0.743** | 0.022 | 0.043 | 0.024 |
| 00:13:52 | 0.079 | 2.49 | 0.93 | 3.44 | 3.40 | – | 0.748 | **0.625** | 0.053 | 0.025 |
| 00:14:24 | **0.096** | 2.29 | 1.18 | 3.42 | 3.49 | – | 0.748 | 0.625 | 0.053 | 0.025 |
| 00:14:57 | 0.091 | 2.35 | 1.07 | 3.40 | 3.44 | – | 0.748 | 0.625 | 0.053 | 0.024 |
| 00:15:28 | 0.085 | 2.42 | 1.00 | 3.38 | 3.42 | – | 0.748 | 0.625 | 0.110 | 0.025 |
| 00:15:59 | 0.087 | 2.40 | 1.02 | 3.45 | 3.38 | – | 0.748 | 0.625 | 0.053 | 0.029 |
| 00:16:30 | 0.077 | 2.51 | 0.89 | 3.40 | 3.36 | – | 0.748 | 0.625 | 0.077 | 0.052 |
| 00:17:02 | 0.078 | 2.51 | 0.91 | 3.38 | 3.44 | – | 0.748 | 0.625 | 0.077 | 0.064 |
| 00:17:33 | 0.084 | 2.38 | 0.96 | 3.45 | 3.45 | `/health` 503: 0.02 | 0.748 | 0.625 | 0.069 | 0.028 |
| 00:18:04 | 0.084 | 2.51 | 0.95 | 3.36 | 3.40 | `/health` 503: 0.02 | 0.748 | 0.625 | 0.054 | 0.028 |

All latencies are in seconds. The first row after injection (00:12:49) still shows baseline values because the 1 m window hadn't filled yet. Its higher RPS is the 3rd loadgen replica before any slowdown. All pods stayed `1/1 Running` for the whole window with no new restarts.

Totals over the window, `sum by (path,status) (increase(gateway_requests_total[6m]))` at ~00:18:40:
```
/events               200 => 1230
/events/{id}/reserve  200 => 1230
/reserve/{id}/pay     200 =>  885
/reserve/{id}/pay     500 =>  344      -> 28.0% of /pay failed (configured: 30%)
/events               502 =>    3   (7 in total, see below)
/health               503 =>    1–2
```

**What the DB pool cap actually did.** It produced **2** `PoolError`s in ~6 min, both on reserve:

```
$ kubectl logs deploy/events | grep -B1 exhausted ...
INFO:  10.42.0.140:37158 - "POST /events/1/reserve HTTP/1.1" 500 Internal Server Error   (~00:17:07)
    raise PoolError("connection pool exhausted")
INFO:  10.42.0.128:43794 - "POST /events/1/reserve HTTP/1.1" 500 Internal Server Error   (~00:18:35)
    raise PoolError("connection pool exhausted")
```

**These 2 errors are not in Prometheus at all.** The table has no `/events/{id}/reserve` 5xx row. The gateway logs show why:

```
File "/app/main.py", line 284, in reserve_tickets
    r.raise_for_status()
httpx.HTTPStatusError: Server error '500 Internal Server Error' for url 'http://events:8081/events/1/reserve'
File "/app/main.py", line 289, in reserve_tickets
    raise HTTPException(e.response.status_code, e.response.json())
    raise JSONDecodeError("Expecting value", s, err.value) from None
File "/app/main.py", line 183, in metrics_middleware
ERROR:    Exception in ASGI application
INFO:     10.42.0.148:47080 - "POST /events/1/reserve HTTP/1.1" 500 Internal Server Error
```

events answers the unhandled `PoolError` with a plain-text `Internal Server Error`. Gateway's `HTTPStatusError` handler calls `e.response.json()` on it and crashes with `JSONDecodeError`. The exception escapes through `metrics_middleware` *before* `REQUEST_COUNT.inc()`, so Starlette's last-resort handler returns 500 to the user and **the request is never counted**. Any non-JSON error from events on the reserve path is invisible to the SLI.

**Other 5xx:**
- `/events` 502 ×7 and `/health` 503 ×1, all at **00:12:55**. This is the single `events` pod being replaced by `kubectl set env` (old pod `Completed` at 00:12:49, the new one `Readiness probe failed: connection refused` while starting). It's the same "config change = mini outage" effect as in Experiment 2, now on the read path.
- `/health` 503 ×1 at **00:17:30**: a single gateway health check got a non-OK answer from a dependency. I could not find a matching error in the events or payments logs, so it's most likely one probe hitting the 2 s health-check timeout. Unconfirmed.

**Restore:**

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0     # 00:19:33
kubectl set env deployment/events DB_MAX_CONNS=10
kubectl scale deployment/mixedload --replicas=2
```
At 00:21:02: 5xx = 0, ~15.9 user RPS + 1.5 probe RPS, all p99 < 0.08 s, `/health` → `healthy`.

### Analysis

**Which golden signal reacted first?** **Errors and latency together**, in the first sample with a full window (00:13:20): 5xx went 0 → 2.8% and `/pay` p99 went 0.045 → 0.743 s. In relative terms latency moved much more (p99 ×16), but the p99 is a tail metric. The p50 only caught up at 00:13:52 (0.018 → 0.625 s, ×35), once all old fast samples had left the window. Errors levelled off at 8–10% (28% of `/pay`, and `/pay` is ~⅓ of the requests). **Traffic** reacted as a consequence: with 3 loaders instead of 2, total RPS (incl. ~1.5 probe RPS) *fell* from ~19.4 to ~11.7, because each sequential loop now spends ≥0.5 s in `/pay`. **Saturation** of the thing I expected to break (events' pool) was not observable at all: `events_db_pool_size` exists, but this Prometheus only scrapes gateway pods.

**Which path shows the worst latency amplification?** `/reserve/{id}/pay` by far: p50 0.018 → 0.625 s (**×35**), p99 0.076 → 0.748 s (**×10**). The real value is a flat ~0.52 s. The 0.625/0.7475 figures are linear interpolation inside the `(0.5, 0.75]` histogram bucket, the same artefact as in Experiment 2. `/events/{id}/reserve` p99 moved only 0.045 → 0.053–0.110 s (≤×2.5), and `/events` stayed at ~0.025 s with two blips to 0.05–0.06 s. The pool cap **did not cause queueing latency**, because psycopg2's pool doesn't queue: it fails.

**Hypothesis vs reality:**
1. ✅ `/pay`: 28% 5xx (predicted ~30%), p99 in the 0.5–0.75 s range, total 5xx ~8–10% (predicted ~10%).
2. ✅ The pool cap showed up as *fast errors, not latency*, and only on reserve (the path that holds 2 connections). ❌ But there were far fewer of them than I expected: **2 in 6 minutes**. With a closed-loop load of 3 sequential clients there are rarely more than 3 requests in flight in events, so 3 connections are *just* enough. Exhaustion only happens when a reserve (2 conns) overlaps with another request and a probe.
3. ❌ No readiness flapping and no cascade: `/health` never lost the race often enough to fail 2 probes in a row. The Experiment 3 cascade path still exists, it just wasn't triggered at this load.
4. ✅ Errors and latency first, traffic as a consequence.
5. Surprise: **the pool errors that did happen were invisible to monitoring** because of the gateway `e.response.json()` bug. If the pool had been exhausted all the time, the Prometheus 5xx ratio would have shown *nothing* on `/reserve`. That would be a real outage with a green dashboard.

### Which component was the weakest link? How would I make it more resilient?

**By measured user impact, the weakest link was `payments` combined with how gateway calls it.** 344 of the ~355 5xx in the window came from `/pay`. The rest were 7 `/events` 502s from the events rollout, 2 `/health` 503s and the 2 invisible reserve 500s. Payments' failures are random and transient, and gateway passes every one of them straight to the user: there is no retry (the `call_with_retry` stub is a no-op) and no circuit breaker. Each failure also leaves the reservation held in Redis, so failed checkouts quietly consume inventory. To make it more resilient:
- Retry `/charge` up to 3 times with backoff. With an independent 30% failure rate that cuts user-facing failures to ~0.3³ ≈ 2.7%. This is only safe together with an **idempotency key** (`reservation_id`), because Experiment 2 showed that a "failed" call to payments can still have charged the customer.
- Add a circuit breaker so a payments outage fails fast instead of holding gateway connections for 0.5–5 s.

**The more dangerous weakness, though, is the `events` DB pool path.** It barely fired at this load, but it fails in the worst possible way: it errors instead of waiting, it double-books connections per reserve, and its errors are invisible to the SLI. To make it more resilient:
- Fix gateway's handler to fall back to `e.response.text` when the body isn't JSON, so every error is counted.
- Make `_get_available` reuse the caller's connection instead of taking a second one (halves pool demand on reserve).
- Use a pool that waits with a timeout (or catch `PoolError` → `503 Retry-After`) instead of throwing an unhandled 500.
- Scrape `events` (and its `events_db_pool_size`) in Prometheus so saturation is visible *before* it turns into errors.

---

## Bonus Task — Resilience Improvement

### B.1 — Weakness chosen

**The Experiment 3 cascade: dependency-aware health checks used as Kubernetes probes.** It had by far the worst impact of everything I tested. Losing Redis, a dependency only *reservations* need, caused a ~3 min **total** outage of every endpoint:
- `events` and all 5 `gateway` pods went NotReady and restart-looped (4 restarts each).
- Users got `connection refused` even on `GET /events`.
- Prometheus barely saw the outage, because the requests never reached a gateway process.

Root cause: both services point **liveness and readiness at `/health`**, and `/health` is a *deep* check. events' `/health` fails when Redis is down, and gateway's `/health` fails when events' `/health` fails. One non-critical dependency could therefore remove every pod from every Service and make the kubelet restart them, which can't help, because the problem isn't inside the pod.

### B.2 — The fix (config only, no image rebuild)

Separate the three questions a health endpoint was answering at once:

| Question | Who asks | New check |
|---|---|---|
| *Is the process alive?* | liveness → restart | `/metrics` on both services: served by the app itself, no downstream calls |
| *Can this pod serve traffic?* | readiness → Service endpoints | gateway: `/metrics` (it handles downstream failures per request). events: `GET /events`, which runs a real Postgres query (every events path needs Postgres) but does not touch Redis |
| *Is the whole system healthy?* | humans, dashboards, alerts | `/health`, unchanged; it now only *reports* |

```diff
--- a/k8s/gateway.yaml
+++ b/k8s/gateway.yaml
@@ -28,16 +28,20 @@ spec:
+          # Liveness = "is this process alive?" only. /metrics has no downstream
+          # dependencies; restarting a pod can't fix a missing dependency (Lab 8, Exp 3).
           livenessProbe:
             httpGet:
-              path: /health
+              path: /metrics
               port: 8080
@@
+          # Readiness = "can this pod serve anything?". Gateway handles dependency
+          # failures per request, so it must not leave the Service because of them.
           readinessProbe:
             httpGet:
-              path: /health
+              path: /metrics
               port: 8080
--- a/k8s/events.yaml
+++ b/k8s/events.yaml
@@ -41,16 +41,20 @@ spec:
+          # Liveness = "is this process alive?" only. /metrics has no downstream
+          # dependencies; restarting a pod can't fix a missing dependency (Lab 8, Exp 3).
           livenessProbe:
             httpGet:
-              path: /health
+              path: /metrics
               port: 8081
@@
+          # Readiness checks Postgres (which every events path needs) but not Redis,
+          # so a Redis outage breaks only reservations instead of the whole service.
           readinessProbe:
             httpGet:
-              path: /health
+              path: /events
               port: 8081
```

**Applying it to the cluster.** In the cluster, gateway is the Lab 7 Argo Rollout, while `k8s/gateway.yaml` on this branch (cut from `main`) is still the pre-Lab 7 Deployment. A plain `kubectl apply` of these files would also strip the ArgoCD tracking annotations (`kubectl diff` showed it). So I applied exactly the probe change as a JSON patch:

```bash
kubectl patch deployment events --type=json -p '[
  {"op":"replace","path":"/spec/template/spec/containers/0/livenessProbe/httpGet/path","value":"/metrics"},
  {"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/events"}]'
kubectl patch rollout gateway --type=json -p '[
  {"op":"replace","path":"/spec/template/spec/containers/0/livenessProbe/httpGet/path","value":"/metrics"},
  {"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/metrics"}]'
```

The gateway change went through the Lab 7 canary (20% → pause → analysis → 50% → 100%), and the automated analysis passed:
```
⟳ gateway                            Rollout      ✔ Healthy
├──# revision:11
│  ├──⧉ gateway-69c5b9bbd9           ReplicaSet   ✔ Healthy   stable
│  └──α gateway-69c5b9bbd9-11-2      AnalysisRun  ✔ Successful  ✔ 3
```
(00:24:26 → 00:27:18)

Side effect: kubelet probes now hit `/metrics`, which `metrics_middleware` doesn't count. The ~1.5 RPS of `/health` probe traffic therefore disappears from `gateway_requests_total`, and the 5xx ratio is now measured over real user traffic only.

### B.3 — Re-running Experiment 3

The same procedure as before: `mixedload` (2 replicas) running, `kubectl scale deployment/redis --replicas=0`, the same in-cluster probe, Redis restored after ~2.5 min.

**Before:** the probe at 00:28:28 showed ~15.5 user RPS, 0 5xx, all pods Ready with 0 restarts.

```
scale redis 0 at 00:28:38
redis gone at 00:28:40
```

Probes at +5 s (00:28:45), +45 s (00:29:25) and +2 min (00:30:42). All three gave the same answers. The 00:30:42 one:
```
GET /events:
200 0.018246s
POST /reserve:
{"detail":"Events service timeout"}
504 5.010317s
GET /health:
{"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}
503
GET events:8081/health:
{"status":"degraded","checks":{"postgres":"ok","redis":"down"}}
503
```

At 00:28:45 events' `/health` still said `redis: ok`, because `_check_redis()` caches its result for 5 s.

Pods and endpoints during the outage (00:29:25 and 00:30:42):
```
events-7dc5cfc745-zxn6b    1/1   Running   0
gateway-69c5b9bbd9-7vxl9   1/1   Running   0
gateway-69c5b9bbd9-l2qwp   1/1   Running   0
gateway-69c5b9bbd9-n8k47   1/1   Running   0
gateway-69c5b9bbd9-tqtnr   1/1   Running   0
gateway-69c5b9bbd9-wxxmt   1/1   Running   0

SVC       READY
events    true
gateway   true,true,true,true,true
```

Prometheus at 00:30:20:
```
sum by (path,status) (rate(gateway_requests_total[1m])) > 0
   /events               200 => 0.42
   /events/{id}/reserve  504 => 0.38
5xx ratio                    => 0.477
p99 /events                  => 0.025 s
```

**Restore and recovery:**
```
restore redis at 00:31:05
redis available  00:31:06
00:31:08 reserve -> 200 0.017434        <- 2 s later, no pod restart (redis-py reconnects by itself)
```

Total throughput, `sum(rate(gateway_requests_total[30s]))`:
```
00:28:35  15.52   <- Redis scaled to 0 at 00:28:38
00:28:50  10.96
00:29:05   2.03
00:29:20 … 00:31:05   0.73–0.84
00:31:20   5.60   <- Redis back at 00:31:06
00:31:35  14.68
00:31:50  15.72
```

Totals over the run, `increase(...[4m])`: `/events` 200 → 519, `/events/{id}/reserve` 504 → 54, `/reserve/{id}/pay` 200 → 457. There were no 5xx on `/events` at all. Afterwards: 15.7 RPS, 0 5xx, `/health` → `healthy`.

The throughput drop is the closed-loop loadgen again. Each `mixedload` loop now waits 5 s on the failing reserve, so it sends only 2 requests per ~5.3 s. That gives 2 pods × 2 / 5.3 ≈ 0.75 RPS, which matches. The capacity to serve reads was never lost: every `/events` that was sent got a 200 in ~18 ms.

### Before vs after

| | Before fix (Exp 3, Redis down 00:04:57–00:07:14) | After fix (Redis down 00:28:38–00:31:05) |
|---|---|---|
| `GET /events` | **connection refused** (`000`, exit 7) from ~00:05:10 | **200 in ~18 ms** throughout |
| `POST /events/{id}/reserve` | 504, then connection refused | 504 after 5.0 s (expected: needs Redis) |
| events pod | NotReady, removed from Service, **4 restarts** | Ready, in Service, **0 restarts** |
| gateway pods | **0/5 Ready**, 4 restarts each (20 total) | **5/5 Ready, 0 restarts** |
| Blast radius | every endpoint (total outage) | reservations / checkout only |
| Visible in Prometheus? | no, user traffic never reached gateway; only ~1 RPS of probe 503s | yes, every reserve 504 counted (54), 5xx ratio 0.48 |
| `/health` | 503, *and* it took the pods out of service | 503 `degraded`, as information only |
| Recovery after Redis returns | 32 s to Ready (needed one more restart), traffic back after ~55 s | reserve 200 after **2 s**, traffic back after ~30 s (the loadgen's 5 s waits draining) |

### Trade-off

**What the fix traded off:** Kubernetes no longer acts on dependency failures. A pod whose downstream is broken now stays in rotation and returns errors per request instead of being taken out of the Service. A liveness check on `/metrics` won't catch a process that still serves metrics but is functionally stuck (e.g. a dead DB pool that never reconnects). So detecting those cases now depends on alerting on `/health` and the error-rate SLI rather than on automatic restarts. Also, events' readiness check now runs a real Postgres query every 5 s.

**Still open (next fix):** reserve without Redis still fails *slowly*. Events' logs show redis-py retrying and timing out (`ConnectionError: Connection closed by server` ×43, `TimeoutError: Timeout reading from socket` ×10) while the request holds a DB connection; that's also where 2 `PoolError`s came from during the run. Checking `_check_redis()` before `setex()` and returning `503` immediately, or setting `socket_connect_timeout` and `retry_on_timeout=False`, would turn the 5 s 504 into a fast 503.
