# Lab 10 — SRE Portfolio & Reliability Review

## Setup

The k3d cluster is in the state Lab 9 left it in: the gateway is an Argo Rollout with 5 replicas, `events` and `payments` have 1 replica each, Postgres runs on a PVC, and the `postgres-backup` CronJob runs every 5 min. Every app container has the same resources: `requests: 50m/64Mi`, `limits: 200m/256Mi`.

```bash
cp labs/lab10/locustfile.py locustfile.py
kubectl create configmap locustfile --from-file=locustfile.py=locustfile.py \
  --dry-run=client -o yaml | kubectl apply -f -

# mixedload from Lab 8/9 was still running (~17 RPS of background checkout traffic).
# Stopped it so that Locust is the only traffic source and the numbers are clean:
kubectl scale deployment/mixedload --replicas=0
```

Before every run, `redis-cli FLUSHDB`. Each run is a Job generated from `labs/lab10/locust-runner.yaml` with `-u/-r` changed and `-t 60s`. While each run was going, I sampled `kubectl top pods` every 15 s.

Two things I checked before trusting the numbers:
- **The rate limiter is a no-op.** `RateLimiter.allow()` returns `True` until Lab 11 (`app/gateway/main.py`), so Locust coming from a single pod IP is not throttled. All failures below are real.
- **`locustfile.py` was used unchanged.** Reserves are spread across events 3 and 5 (`random.choice([3, 3, 3, 5])`).

One operational note: Locust exits with code 1 when any request failed, so a run with errors ends as a **Failed** Job, not Complete. `kubectl wait --for=condition=Complete` then just hangs until its timeout. My runner script waits for either `Complete` or `Failed`.

---

## Task 1 — Load Testing & Reliability Review

### 10.1 / 10.2: Load levels and breaking point

Aggregated Locust results, 60 s per run. All times are in ms:

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) | Verdict |
|------:|-----:|----:|----:|----:|----:|---------------:|----------------:|---|
| 10  | 2/s  | 7.8  | 11  | 31   | 88   | 0.00 % (0/463)       | 0 | healthy |
| 20  | 2/s  | 14.6 | 14  | 77   | 170  | 0.00 % (0/873)       | 0 | healthy |
| **25** | 3/s | **18.6** | 12 | 74 | **200** | **0.00 % (0/1110)** | 0 | **last healthy level** |
| **30** | 3/s | **21.7** | 12 | 330 | **480** | **0.93 % (12/1296)** | 0 | **broken (5xx > 0.5 %)** |
| 50  | 5/s  | 28.9 | 370 | 830  | 1100 | 22.78 % (392/1721)   | 0 | broken |
| 100 | 10/s | 44.0 | 700 | 2600 | 4900 | 58.49 % (1536/2626)  | 0 | collapsed |

I added the 20/25/30 runs to find the breaking point. The lab suggests trying 200 users "if 100 looked healthy", but 100 was already far past the limit.

**Breaking point: 30 users / ~22 RPS.** At that level the 5xx rate crosses the 0.5 % threshold, and p99 (480 ms) is right at the 500 ms limit. **Capacity ceiling: ~25 users / ~19 RPS** with 0 errors and p99 = 200 ms. That is very low for 5 gateway replicas, and the reason is not the gateway.

Every failure in every run was a 5xx; there was **not a single 409**. Inventory was never the limiting factor. Holds were flushed between runs, and the most event 5 (80 tickets) ever sold was 41 seats in the 50u run.

The error reports from the failing runs look like this (100u):

```
1012  GET /events:              502 Bad Gateway
 182  POST /events/3/reserve:   500 Internal Server Error
 159  GET /health:              503 Service Unavailable
  78  POST /events/5/reserve:   500 Internal Server Error
  71  POST /events/3/reserve:   502 Bad Gateway
  32  POST /events/5/reserve:   502 Bad Gateway
   2  …                         504 Gateway Timeout
```

**Why it breaks.** I found the root cause after the 50u run:

```
$ kubectl logs deploy/events --since=5m | grep -c "PoolError: connection pool exhausted"
235

$ kubectl exec deploy/postgres -- psql … -c "EXPLAIN ANALYZE SELECT e.id, COALESCE(SUM(o.quantity),0)
    FROM events e LEFT JOIN orders o ON e.id=o.event_id GROUP BY e.id ORDER BY e.event_date"
 Execution Time: 84.850 ms          (idle DB, no load)

$ … SELECT count(*) FROM orders  →  11317     (no index on orders.event_id)
```

CPU during the runs, `kubectl top`, peak values. Each container's limit is 200m:

| | 25u (healthy) | 50u | 100u |
|---|---:|---:|---:|
| gateway (sum of 5 pods) | 114m (~23m/pod) | 199m (~40m/pod) | 300m (~60m/pod) |
| events (1 pod) | 71m | 151m | **195m** |
| postgres | 94m | **196m** | 176m |

The chain of failure:
1. **Every `GET /events` (70 % of the traffic mix) aggregates the whole `orders` table.** It's a full seq scan + `SUM` over 11k rows, and that table only grows (`mixedload` had been creating orders for hours).
2. **Postgres is CPU-capped at 200m** and hits that cap first (196m at 50u). Queries slow down and start to pile up.
3. **The `events` pool (`DB_MAX_CONNS=10`) does not queue.** `ThreadedConnectionPool.getconn()` raises `PoolError` immediately when all 10 connections are busy (Lab 8, Task 2 found the same behaviour). FastAPI runs sync handlers in a 40-thread pool, so up to 40 concurrent handlers race for 10 connections. The losers get a 500, which the gateway turns into a 502 (or into an uncounted 500 on `/reserve`, see §6).
4. **Feedback loop through the probes.** The `events` readiness probe is `GET /events`, the same heavy query. Under load it fails as well (`Readiness probe failed: HTTP probe failed with statuscode: 500` on `events-559695ff74-rbcfs` during the 100u run). The only `events` pod is then pulled from the Service, so *every* request, including the cheap ones, gets a 502. Gateway `/health` returns 503 when `events` is degraded, so the 159 `/health` failures are a symptom of the same thing.

The **gateway itself never got above ~30 % of its CPU limit**. Adding gateway replicas would not move the ceiling at all.

The ceiling also **depends on how much data there is, not only on traffic**. The `/events` cost grows linearly with `orders`. The same cluster with an empty `orders` table would handle much more, and it will get worse every day the shop is open. Before Lab 9's PVC, every restart wiped `orders` and hid this.

### 10.3: DORA metrics

Sources: `git log --all`, `gh run list -R G-0-rG/SRE-Intro`, `kubectl argo rollouts get rollout gateway`, `kubectl get analysisrun`. This is a solo course fork, and most "deploys" were exercises, so the numbers describe the *process*, not real product delivery.

| Metric | Data | Value | DORA band |
|---|---|---|---|
| **Deployment Frequency** | 3 GitOps deploys via CI → ArgoCD (2026-09-27: `1527363`, `a468d56`, `e82db0b`, all within 26 min) + 11 Argo Rollout revisions (2026-10-04, Lab 7, mostly `kubectl argo rollouts set image`/manifest edits). 14 deploys over the 4-week course, clustered on 2 days. | ~3.5 / week on average, on-demand when needed | High (on-demand capable, but not continuous) |
| **Lead Time for Changes** | `340a6ba` pushed 13:03:45Z → CI built and pushed images and committed the new tag `e82db0b` at 13:04:33Z (CI runs: 44–57 s). ArgoCD polls every ≤ 3 min. The canary strategy from Lab 7 then needs ~4.5 min to reach 100 %. | **~1 min to "image ready"; ~5–9 min commit → 100 % traffic** | Elite (< 1 day) |
| **Change Failure Rate** | AnalysisRuns: 2 Successful, **1 Failed** (`gateway-d9c4f69bb-9-2`: `error-rate failed (2) > failureLimit (1)`). Of the 11 Rollout revisions, 3 were bad versions (Lab 7 attempt A `EVENTS_URL=broken`, attempt B `GATEWAY_TIMEOUT_MS=1`, revision 9). All 3 were deliberate. 0 of the 3 GitOps deploys failed. | **27 % (3/11) of Rollout revisions; 33 % of AnalysisRuns.** 0 % unintentional. | Medium on paper (all failures were planned tests) |
| **Time to Restore** | Lab 7 `abort`: canary out of the endpoints in **0.35 s**, full capacity in 18.4 s. `git revert` path: CI ~50 s + ArgoCD poll ≤ 3 min ≈ **~4 min**. Non-deploy incidents: Lab 9 Postgres loss 70.8 s (no PVC) → ~20 s (PVC); Lab 8 Redis outage ~3 min; Lab 6 incident 28 min to the first delivered alert. | **< 1 min for bad deploys (abort); minutes for infrastructure failures** | Elite (< 1 hour) |

Caveat for Lead Time: **ArgoCD auto-sync is currently off** (paused in Lab 7 so that it wouldn't fight the Rollout, and the Application is `OutOfSync`). As it stands, a merge to `main` doesn't reach the cluster until someone syncs by hand. The real lead time today is "until a human remembers", so turning auto-sync back on is part of the risk list.

### 10.4: Toil

| # | Toil (manual, repeated > 3×) | How often | How to automate | What it saves |
|---|---|---|---|---|
| 1 | **Re-seeding Postgres after every restart** (`psql < seed.sql`, `UPDATE events SET total_tickets=1000000`, `redis-cli DEL event:1:held`), plus diagnosing *why* the app is broken first (`relation "events" does not exist`) | Lab 7 (7.4), Lab 8 setup, Lab 9 setup, Lab 9 Task 2 (restore), Lab 9 bonus migration: **5×**, ~5–10 min each, including the diagnosis | **Done in Lab 9:** PVC for Postgres + `postgres-backup` CronJob. Remaining: run `alembic upgrade head` + an idempotent seed as an init Job/ArgoCD PreSync hook instead of `seed.sql` by hand | ~40 min across the course and, more importantly, no more silent data loss: the "empty DB" state also made the Lab 8/9 baselines unreliable |
| 2 | **Querying Prometheus through `kubectl exec … wget -qO- 'http://localhost:9090/api/v1/query?query=<urlencoded>'`**, URL-encoding PromQL by hand or with a wrapper, copying numbers into tables | Every experiment in Labs 7–10: **dozens of times per lab** | A Grafana instance in the cluster with the Lab 3 golden-signals dashboard, provisioned as code. Recording rules for the SLIs (from Lab 3) in the in-cluster Prometheus. A small `promq` script in the repo for the CLI cases | ~1–2 min per query; also fewer mistakes, e.g. the `increase()`-on-new-series trap in Lab 9 that hid a failed `/pay` |
| 3 | **Resetting load-test state and babysitting load generators**: `redis-cli FLUSHDB`/`DEL event:1:held`, bumping `total_tickets`, scaling `mixedload` up/down, `kubectl wait` + `kubectl logs job/…` for every run | Lab 8 (3 experiments), Lab 9, Lab 10 (6 Locust runs): **> 10×** | A single parametrised script/Makefile target (`make load USERS=50`) that resets state, creates the Job, waits for Complete/Failed and stores the summary. Longer term, give `reserve` holds a TTL and decrement on confirm/expire (the Lab 6 postmortem bug), so the state doesn't need resetting at all | ~3–5 min per run, and runs become reproducible (same starting state every time) |

Honourable mentions, not repeated > 3× but painful: re-creating port-forwards (ArgoCD in Lab 5, Postgres in Lab 9), and pausing/resuming ArgoCD auto-sync around manual Rollout work.

### 10.5: Reliability Review

# QuickTicket Reliability Review

*Scope: the k3d deployment as of 2026-10-11, evidence from Labs 1–10.*

## 1. SLO Compliance

SLOs from Lab 3: **availability 99.5 %** (gateway non-5xx), **latency 95 % < 500 ms**. In-cluster Prometheus started at 13:31 UTC today, so a 7-day window doesn't exist (see §6). I computed the SLIs (excluding `/health`) for two windows:

| SLO | Target | Normal traffic (20 min, 17:05–17:25, ~16 RPS `mixedload`) | Whole session (75 min: Lab 9 DR drills + Lab 10 load tests) | Status |
|---|---|---|---|---|
| Availability | 99.5 % | **100 %** (0 / 19 389 failed) | **87.98 %** (5 878 / 48 906 failed) | Met at normal load, **budget blown** under stress |
| Latency < 500 ms | 95 % | **100 %** (p99 = 89 ms) | **94.81 %** (p99 = 2.2 s) | Met at normal load, **missed** under stress |

With the Lab 3 budget (35 failed requests/week at ~1000 req/day), the session burned the error budget ~170× over. That's expected, since the session consisted of deliberate disasters and overload. The useful conclusion: **at its current ~17 RPS steady state, QuickTicket meets both SLOs with ~10 % headroom before the ~19 RPS ceiling.** One traffic spike of +30 % is enough to break the availability SLO. And as the next sections show, the SLI itself under-counts failures.

## 2. Load Test Results

See the table in 10.1. Summary: **healthy up to ~19 RPS (25 users), broken at ~22 RPS (30 users)**. The bottleneck is Postgres + the single `events` pod, both CPU-capped at 200m. The trigger is a full-table aggregation on every `GET /events`. The gateway uses ≤ 30 % of its CPU even at 100 users.

## 3. DORA Metrics

See the table in 10.3. Deploy mechanics are strong (≈1 min CI, canary + automated analysis, sub-second abort). The weak points are process gaps: ArgoCD auto-sync off, and the 4 most recent labs' branches not merged to `main`, so `main` does not describe what's running.

## 4. Top 3 Reliability Risks

1. **`events` + Postgres are a capacity and availability single point, and their capacity shrinks as data grows.**
   *Why it matters:* the ceiling is ~19 RPS today and falls further with every order (the `/events` query cost is O(orders)). Above it, the non-queuing pool turns overload into hard 5xx, and the readiness probe on the same heavy endpoint takes the only `events` pod out of the Service. A slowdown becomes a full outage.
   *Fix:* (a) stop recomputing availability on every read: keep a `sold` counter per event (updated in the order transaction) or cache the `/events` response in Redis for a few seconds. At minimum, add an index on `orders(event_id)`. (b) Make the pool block with a timeout instead of raising (a semaphore around `getconn`), so overload means latency, not errors. (c) Run ≥ 2 `events` replicas, and change readiness to a cheap `/ready` (`SELECT 1`), not `/events`. (d) Raise Postgres's CPU limit and add the requests/limits to match.

2. **Health checks cascade dependency failures into site-wide outages.**
   *Why it matters:* Lab 8: Redis down → events `/health` 503 → probes failed → **6 pods restart-looping and a ~3 min total outage** for a cache failure. Lab 10: `events` overload → readiness fails → 100 % 502. Gateway `/health` reports 503 whenever any dependency is degraded, so every dependency blip looks like "gateway down".
   *Fix:* separate liveness (`/livez`: the process responds), readiness (only what *this* pod needs in order to serve anything) and dependency health (`/health` for dashboards/alerts, never for probes). Reserve should fail fast with a clean 503 when Redis is down, instead of taking reads down with it.

3. **Observability under-reports the failures it exists to catch.**
   *Why it matters:* (a) The gateway's `reserve_tickets` calls `e.response.json()` on a non-JSON 500 from `events`. That raises inside the `except`, escapes `metrics_middleware`, and the request is **never counted**. Seen in Lab 8 (2 errors), Lab 9 (32), and Lab 10: **260 of 1536 failures (17 %) in the 100u run and 103 of 392 (26 %) in the 50u run are missing from `gateway_requests_total`** (Locust's client-side count compared with Prometheus counter deltas over the same window). (b) In-cluster Prometheus has no PVC, so all history is lost on every restart; today's data starts at 13:31 UTC, and a 7-day SLO can't be computed. (c) Lab 6 alerting only covers error rate / burn rate and lives in the docker-compose Grafana, not the cluster.
   *Fix:* use `e.response.text` the way `get_event` already does, and count in a `try/finally` in the middleware. Add a PVC for Prometheus (or remote-write). Move alert rules into the in-cluster Prometheus/Alertmanager. Add an external black-box probe (or ingress-level metrics) so the SLI doesn't depend on the process that is failing.

Also on the radar: `postgres-data` and `postgres-backups` are `local-path` volumes on the same single node (losing the node loses the DB *and* its backups), and the payment path isn't idempotent (Lab 8: 33 charges with a 504 to the user and no order).

## 5. Toil Identification

See the table in 10.4. Top items: manual DB re-seeding (fixed by the PVC in Lab 9), hand-written Prometheus queries through `kubectl exec`, and manual load-test state resets.

## 6. Monitoring Gaps

- **During the Lab 8 chaos experiments I wished I had:**
  - **Client-side / edge error metrics.** In Experiment 1, a killed pod's in-flight requests would never reach `gateway_requests_total`, and `mixedload` discarded curl exit codes. Traefik metrics or a black-box prober would close that gap.
  - **Per-dependency metrics from the gateway**: latency/errors of calls to `events` and `payments`, and circuit-breaker state as a time series. In Experiment 2, the "504 to the user, but the charge succeeded" case was visible only by correlating logs.
  - **Pod restart / readiness signals in Prometheus** (kube-state-metrics: `kube_pod_container_status_restarts_total`, `kube_pod_status_ready`). The Lab 8 Redis cascade was a restart storm that I saw only in `kubectl get pods -w`.
  - **`events` DB pool metrics.** `events_db_pool_size` exists, but there is no "pool exhausted" counter. `PoolError` was visible only in logs (Lab 8 Task 2, Lab 10).
- **The alert that would have caught what actually broke:**
  - **Latency SLO burn rate.** Lab 6 alerts only on error rate. The Lab 8 payment-latency experiment (p99 2.5 s, 0 % 5xx) would have paged no one. Alert: `1 - gateway:sli_latency_500ms:ratio_rate5m > 14.4 × 0.05` for 2 m (fast burn) plus a slow-burn pair.
  - **`events` readiness / Service endpoints.** `kube_endpoint_address_available{endpoint="events"} == 0` for 30 s would have fired within seconds in the Lab 8 Redis outage, the Lab 9 Postgres loss, and the Lab 10 overload. Each of those was "all `events` endpoints gone".
  - **Pod restart storm**: `increase(kube_pod_container_status_restarts_total[5m]) > 3`.
  - **Uncounted-error detector**: gateway log-based `Exception in ASGI application` count > 0, or simply the black-box availability probe disagreeing with the in-process SLI.
  - **Saturation**: `container_cpu_cfs_throttled_periods_total` ratio for `events`/`postgres` > 25 %. This would have warned *before* the Lab 10 cliff, since Postgres sat at 196m/200m at 50 users.

## 7. Capacity Plan

- **Current ceiling:** ~19 RPS with 0 errors and p99 200 ms (25 Locust users). It breaks at ~22 RPS. Normal traffic is ~17 RPS, so headroom is ~10 %.
- **Bottleneck:** Postgres (200m CPU limit) + 1 `events` pod (200m) with a 10-connection non-queuing pool. The gateway (5 × 200m) is at ≤ 30 %.
- **For 2× traffic (~35–40 RPS)**, in order of leverage:
  1. Fix the `/events` query (counter column or 2–5 s Redis cache). This removes the O(orders) scan on 70 % of requests and should raise the ceiling far more than any scaling.
  2. `events`: 1 → **3 replicas** (the gateway → events hop needs redundancy anyway), cheap readiness probe, and a blocking pool with a ~1 s wait.
  3. Postgres: CPU limit 200m → **1 core** (request 500m), and `max_connections` sized for 3 × `DB_MAX_CONNS`.
  4. Gateway: stay at **5 replicas**. It isn't the bottleneck, and its requests (50m) already exceed actual use.
  5. Re-run this Locust ladder after each step: the next ceiling is unknown until measured.
- **Rough cost estimate:** the change adds about +2 events pods (2 × 200m/256Mi) and +0.8 vCPU for Postgres, so **~+1.2 vCPU / +0.6 GiB** in total. On a typical cloud node (~$25–35 per vCPU-month incl. memory), that's on the order of **~$30–45/month**. Fix #1 is free and probably worth more than all the hardware together. (The exact per-pod headroom numbers for the 2× plan are Task 2.)

---

## Task 2 — Capacity Plan with Numbers

### 10.7: Per-pod CPU at the breaking point

I re-ran the breaking-point level (**30 users**) for 120 s and sampled `kubectl top pods -l app=<svc>` every 10 s during the steady phase. Result of this run: 22.1 RPS, p99 620 ms, 17 × 5xx out of 2647 (0.64 %), plus 39 × 409 (event 5 sold out in the longer run).

```
### 17:46:48          (representative sample; the other 5 samples are within ±15 %)
gateway-69c5b9bbd9-6jpnn   29m   43Mi
gateway-69c5b9bbd9-89sfp   25m   48Mi
gateway-69c5b9bbd9-g4vsm   34m   52Mi
gateway-69c5b9bbd9-hdhl2   21m   42Mi
gateway-69c5b9bbd9-mvtd7   18m   43Mi
events-559695ff74-rbcfs    79m   60Mi
payments-6bb79c9f6c-6pgrz   7m   39Mi
postgres-598c57c4cf-jthmd 125m   33Mi
redis-6fcfb5475d-prs5s      8m    5Mi
```

At first glance nothing is near its 200m limit. `kubectl top` averages over its scrape window, though, and the failures here come in bursts. So I read CFS throttling straight from each container's cgroup (`/sys/fs/cgroup/cpu.stat`), before and after a 60 s run at 30 users:

| Pod | CPU used in run | Avg | Limit | Throttled periods | Verdict |
|---|---:|---:|---:|---:|---|
| **postgres** | 7.3 s | ~100m | 200m | **206 / 604 (34.1 %)**, 24.3 s throttled | **CPU-constrained** |
| events | 4.4 s | ~60m | 200m | 16 / 629 (2.5 %) | close behind |
| gateway (×5, 2 sampled) | 1.3–1.9 s | ~20–30m | 200m | 0–2 / ~450 (≤ 0.5 %) | idle |
| payments | 0.4 s | ~7m | 200m | 0 / 369 | idle (Locust doesn't call `/pay`) |
| redis | — | ~8m | 200m | — | idle |

**Postgres is the constrained service.** It spends a third of all 100 ms CFS periods waiting for quota, even though its *average* is only half the limit. Each `/events` aggregation is a CPU burst that runs into the 20 ms-per-100 ms quota. The gateway (5 pods), payments and redis are idle. **So the thing to scale is Postgres CPU first, and then `events`.** The gateway doesn't need scaling.

### Validating the plan instead of guessing

I tested the proposed changes on the live cluster, one at a time, then reverted. (I used `kubectl set resources` / `kubectl scale`, not `kubectl apply`: `k8s/postgres.yaml` on this branch still predates the Lab 9 PVC, and applying it would have dropped the volume.)

| Config | Users | RPS | p50 | p95 | p99 | 5xx | 409 | Bottleneck (throttled periods) |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| **Baseline** (pg 200m, events ×1) | 25 | 18.6 | 12 | 74 | 200 | 0 % | 0 | — |
| Baseline | 30 | 21.7 | 12 | 330 | 480 | 0.93 % | 0 | postgres 34 % |
| Baseline | 50 | 28.9 | 370 | 830 | 1100 | 22.8 % | 0 | postgres |
| **A:** pg **1 CPU** (req 500m), events ×1 | 50 | **36.9** | 10 | 35 | **140** | **0 %** | 39 | — |
| A | 100 | 49.6 | 580 | 1400 | 2200 | 49.2 % | 0 | **events 84.3 %** (201m/200m), postgres 0 % |
| **B:** pg 1 CPU, **events ×3** | 100 | **74.0** | 11 | 38 | **110** | **0 %** | 284 | — |
| B | 200 | 109.6 | 430 | 1000 | 1300 | 32.1 % | 121 | events 83–89 % ×3, gateway 35 %, postgres 19 % (769m) |

(5xx % here excludes 409s, which are inventory sell-outs and expected product behaviour.)

What the steps show:
- **Step A** (Postgres 200m → 1 CPU, nothing else changed): 50 users went from 22.8 % 5xx to **0 %**, and p99 fell from 1100 to 140 ms. The ceiling roughly doubled. As predicted, the bottleneck then **moved to `events`**: at 100u its single pod sat at its 200m cap (84 % of periods throttled), and `PoolError` came back (762 in its log). Postgres had 0 % throttling.
- **Step B** (+ `events` 1 → 3): 100 users / **74 RPS with 0 % 5xx and p99 110 ms**, about **4× the original 19 RPS ceiling**. At 200u everything saturates at once: all three `events` pods at 200m, the gateway starting to throttle (35 %), Postgres at 769m. The ceiling of config B is therefore somewhere between 74 and 110 RPS.
- **Uneven `events` load.** At 100u the three `events` pods served 2439 / 1064 / 713 requests. The gateway's `httpx.AsyncClient` reuses keep-alive connections, and kube-proxy balances *connections*, not requests, so a newly scaled pod gets traffic only when new connections are opened. Scaling out helps less than linearly unless the gateway's connection pool is recycled (e.g. `max_keepalive_connections`/keep-alive expiry) or a request-level balancer (service mesh / headless + client-side LB) is used.
- **Postgres connections are not a bottleneck.** `pg_stat_activity` showed **7** connections at 100u with 3 `events` pods (each pool lazily opens up to `DB_MAX_CONNS=10`, so ≤ 30 worst case). PostgreSQL's default `max_connections` is 100. The limiting factor is the non-queuing *client-side* pool: a burst of more than 10 concurrent handlers in one pod fails immediately instead of waiting.

All changes were reverted afterwards. Postgres went back to the identical ReplicaSet `598c57c4cf` with its PVC, and `orders` = 11317 rows, so no data was lost. `events` was scaled back to 1 replica. Smoke test: `/events=200`.

### 10.8: Plan for 2× traffic

Target: 2× today's traffic. Normal load is ~17 RPS and the healthy ceiling is ~19 RPS, so **2× = ~35–40 RPS with SLO-compliant latency**, plus headroom for one pod failure.

**Replicas and resources:**

| Service | Now | 2× plan | requests | limits | Why |
|---|---|---|---|---|---|
| gateway | 5 × (50m / 200m) | **5** (unchanged) | cpu **100m**, mem 64Mi | cpu 300m, mem 256Mi | ≤ 30 % used at 100u. Raise the request to the real ~60–80m usage so scheduling/HPA math is honest. Raise the limit because the gateway started throttling at 200u |
| events | 1 × (50m / 200m) | **3** (HPA min 3, max 6 on CPU 70 %) | cpu **200m**, mem 128Mi | cpu 500m, mem 256Mi | The second bottleneck after Postgres. ×3 measured at 74 RPS with 0 % 5xx. 3 replicas also remove the single-pod outage seen in Labs 8–10 |
| payments | 1 × (50m / 200m) | **2** | cpu 50m, mem 64Mi | cpu 200m, mem 256Mi | 7–15m CPU. The second replica is for availability (Lab 3/6 outages were payments-down), not capacity |
| postgres | 1 × (50m / 200m) | **1** (vertical) | cpu **500m**, mem 256Mi | cpu **1000m**, mem 512Mi | The primary bottleneck. 1 CPU measured: 0 % throttling at 100u, 769m at 200u. Keep `Recreate` + PVC from Lab 9 |
| redis | 1 × (50m / 200m) | **1** | cpu 50m, mem 64Mi | cpu 200m, mem 128Mi | 8–9m CPU at 100u |

**Redis: is a single pod still OK?** For **capacity, yes**: 9m CPU and 5Mi of memory at 74 RPS, which is about 2 % of its limit. For **availability, no, but replication is the wrong fix.** Lab 8 showed that a Redis outage took the *whole site* down for ~3 min through health-probe cascades. The cheaper and stronger fix is to make Redis non-critical: probes that don't depend on Redis, and a reserve path that fails fast with a 503 while `/events` keeps working. Once that's done, a single Redis with a PVC (so holds survive restarts) is acceptable. Redis Sentinel/replica is worth it only when reservations themselves need an availability SLO.

**DB connections: is the single pooler → single Postgres path a bottleneck?** Not on the Postgres side: 7 server connections at 74 RPS, against 100 allowed. The bottleneck is **inside each `events` pod**: `ThreadedConnectionPool` with `maxconn=10` raises `PoolError` instead of waiting, while FastAPI runs up to 40 sync handlers concurrently. Plan: (1) keep `DB_MAX_CONNS=10` per pod. With 3–6 pods that's 30–60 connections, still under `max_connections=100`, so **no PgBouncer needed yet**. (2) Wrap `getconn()` in a semaphore with a ~1 s wait, so that overload shows up as latency, not 500s. (3) Add PgBouncer (transaction mode) only when `events` goes beyond ~8 replicas or more services start talking to the DB.

**Cost** (lab assumption: **$5 per pod-month** for a small pod ≈ 200m CPU; I count the 1-CPU Postgres as 5 small-pod slots, since it reserves 5× the compute):

| | Pods | Slot-equivalents | $/month |
|---|---|---:|---:|
| Today | gateway 5 + events 1 + payments 1 + postgres 1 + redis 1 = 9 | 9 | **$45** |
| 2× plan | gateway 5 + events 3 + payments 2 + postgres 1 (×5 slots) + redis 1 | 5 + 3 + 2 + 5 + 1 = 16 | **$80** |
| 2× plan, peak HPA (events 6) | | 19 | $95 |

**+$35/month (+78 %) buys ~4× the measured ceiling** (19 → 74 RPS at 0 % 5xx). The software fixes cost nothing and come first: the `/events` aggregation (a `sold` counter column or a 2–5 s cache), the blocking pool, and a cheap readiness probe. With the counter column, `/events` stops scanning `orders` altogether, the ceiling stops shrinking as `orders` grows, and part of the Postgres upsizing could probably be rolled back. Re-run the Locust ladder after each change: the next bottleneck (the gateway at ~110 RPS, per the 200u run) only shows up under measurement.

---

## Bonus Task — SRE Handbook (Option B)

**[`submissions/runbooks/quickticket-handbook.md`](runbooks/quickticket-handbook.md)**: a 2-page handbook for a new on-call engineer:

| Required section | What's in it |
|---|---|
| **Architecture** | Diagram of the k3d cluster (gateway Rollout → events/payments → Postgres on PVC + Redis; Prometheus, ArgoCD, Argo Rollouts, backup CronJob) + bullets with the SLOs and the known capacity ceiling |
| **How to deploy** | The exact GitOps flow: PR → CI (~50 s, image tags committed back) → ArgoCD → canary 20/50/100 with the `gateway-error-rate` AnalysisRun. Abort-then-revert rollback, the Alembic rules for schema changes, and the ArgoCD auto-sync caveat from Lab 7 |
| **Monitoring** | The PromQL to answer "errors? where? slow? burning budget? how much traffic? is the canary bad? saturated?", plus the known blind spots from Labs 8–10 (uncounted 500s, no Prometheus persistence, no latency alert) |
| **Incident response** | The Lab 6 runbook distilled to a 5-step first-5-minutes checklist and a symptom → cause → mitigation table covering every failure mode actually observed in Labs 6–10; 10-minute escalation + postmortem rule |
| **Backup/restore** | The Lab 9 procedure condensed: CronJob checks, pre-change manual backup, the "PV intact → do nothing" case, and a restore script that stops on a failed `pg_restore` (the Lab 9 runbook bug), plus the measured RTO/RPO numbers |

I ran every `kubectl` command in it against the current cluster before committing. For example, `kubectl exec deploy/gateway` doesn't work because the gateway is a Rollout, not a Deployment, so the handbook selects a pod by label.
