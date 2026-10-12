# QuickTicket Reliability Review (Lab 10)

**Branch:** `feature/lab10` (from upstream `main`)
**Cluster:** k3d `quickticket`, 1 node (11 vCPU, 8 GiB, idle 2 % CPU), gateway = Argo Rollout ×5, events / payments / redis / postgres ×1, Postgres on a PVC (Lab 9). Every container: requests 50m / 64Mi, **limits 200m / 256Mi**.
**Load generator:** `locustfile.py` at the repo root (the course scenario, unchanged: 70 % `GET /events`, 20 % `POST /events/{3|5}/reserve` at 3:1, 10 % `GET /health`), run **in-cluster** as a Job (`locustio/locust:2.43.4`, `--host=http://gateway:8080`), so kube-proxy spreads load over all 5 gateway pods. `redis-cli FLUSHDB` before every run.
**Raw output:** `raw.log`, section `=== LAB 10 ===`. All times UTC, 2026-10-12.

- [x] Task 1 — load tests, DORA, toil, reliability review (all 7 sections)
- [x] Task 2 — detailed capacity plan with numbers
- [x] Bonus Task — SRE handbook (`submissions/runbooks/quickticket-handbook.md`)

---

## 1. SLO Compliance

SLOs are from Lab 3 (§3.8): **availability** = share of gateway requests not returning 5xx, target **99.5 %**; **latency** = share of requests faster than 500 ms, target **95 %**; window 7 days. Measured from the in-cluster Prometheus (data since 2026-10-05 12:59, so the window covers Labs 7–10):

| SLO | Target | Observed, 7 d, user paths (`path!="/health"`) | Observed, 7 d, all paths | Steady state (mixedload only, 2026-10-11 13:28–13:40) | Status |
|---|---|---|---|---|---|
| Availability (non-5xx) | 99.5 % | **92.92 %** (7 986 5xx of 112 900) | 94.70 % (10 030 of 189 200) | **100 %** (0 of 11 340) | ❌ over the window, ✅ at steady state |
| Latency (< 500 ms) | 95 % | **93.7 %** | 95.94 % | **100 %** | ❌ over the window, ✅ at steady state |

The 7-day numbers are dominated by failures I injected on purpose. 5xx per day (user paths) were 92 → 111 → 938 → 268 → 1 610 → **4 967**, the last day being this lab's overload runs. Earlier days include Lab 8 (payments at 6 s latency: every `/pay` timed out) and Lab 9 (DROP/kill). The error budget at 99.5 % allows 565 failed requests out of 112 900. The overload tests alone spent it ~9× over (4 967 / 565). The steady-state window shows the system meets both SLOs when it is not being broken or overloaded. Two things the SLOs miss: the gateway's own `/health` returns intermittent 503s at idle (2 044 in 7 days, found in Lab 8), and some 5xx are never counted at all (see §6).

## 2. Load Test Results

### 10.1 / 10.2 Locust table

Locust summaries are in `raw.log`. 5xx and 409 are separated from Locust's **error report**: 5xx = `HTTPError('5xx Server Error …')`, 409 = `HTTPError('409 Client Error: Conflict …')`, plus TCP errors (`ConnectionRefused` / `ConnectionReset`). Goodput = successful responses per second.

| Users | Ramp | RPS | Goodput | p50 | p95 | p99 | 5xx error rate | 409 (inventory) | TCP errors | Locust failures |
|------:|-----:|----:|--------:|----:|----:|----:|---------------:|----------------:|-----------:|----------------:|
| 10 | 2/s | 7.79 | 7.79 | 16 ms | 25 ms | 82 ms | **0 %** (0/466) | 0 | 0 | 0 % |
| 20 | 4/s | 15.23 | 15.23 | 12 ms | 64 ms | 200 ms | **0 %** (0/911) | 0 | 0 | 0 % |
| 30 | 5/s | 22.60 | 22.60 | 10 ms | 120 ms | **280 ms** | **0 %** (0/1353) | 0 | 0 | 0 % |
| **40** | 5/s | 28.73 | 27.98 | 10 ms | 410 ms | **610 ms** | **1.69 %** (29/1720) | 16 (0.93 %) | 0 | 2.62 % |
| 40 (repeat, 10.7) | 5/s | 27.86 | 27.11 | 13 ms | — | — | **2.70 %** (45/1669) | 0 | 0 | 2.70 % |
| 50 | 5/s | 31.23 | 26.13 | 200 ms | 620 ms | 790 ms | **16.33 %** (305/1868) | 0 | 0 | 16.33 % |
| 100 | 10/s | 61.25 | 29.49 | 250 ms | 660 ms | 840 ms | **51.28 %** (1878/3662) | 0 | 21 | 51.86 % |
| 200 | 20/s | 60.64 | 18.80 | 1 700 ms | 4 500 ms | 8 100 ms | **68.14 %** (2472/3628) | 0 | 31 | 68.99 % |

(The 40-user repeat in 10.7 was run with `kubectl top` and cgroup sampling around it. Its percentile line is in `raw.log`. Only the aggregate row is shown here.)

**Breaking point: 40 users / ~28.7 offered RPS.** Both criteria trip there (5xx 1.69 % > 0.5 %, p99 610 ms > 500 ms), reproducibly (2.70 % 5xx on the repeat). **The last healthy level is 30 users / 22.6 RPS** (0 errors, p99 280 ms). Beyond 40 users goodput stays flat at **26–29 RPS**, and every extra request becomes a fast 502. At 200 users even goodput falls (18.80 RPS) and p99 reaches 8.1 s.

What breaks, measured, from the bottom up:

1. **Postgres hits its own CPU limit.** `kubectl top`: postgres 154–157m of 200m at 40 users, 180–192m at 50–100 users. cgroup `cpu.stat` during the 40-user repeat: **+263 throttled periods, 58.8 s throttled**. events: +18 periods / 0.8 s. gateway: 0. The node itself was 98 % idle.
2. **Why Postgres is busy:** `GET /events` runs `events LEFT JOIN orders … GROUP BY` over the whole `orders` table. That is 19 735 rows left behind by the Lab 8/9 mixedload, with no index used, `Seq Scan on orders` → `HashAggregate`. `EXPLAIN ANALYZE`: **5.3 ms of CPU per call on an idle DB**. At 20+ `/events` per second that alone is >100m of CPU, and it grows linearly with every order ever sold.
3. **events' pool fails instead of queueing.** `ThreadedConnectionPool(maxconn=10).getconn()` raises immediately when empty. In the 50-user run events logged **268 × `psycopg2.pool.PoolError: connection pool exhausted`**. The gateway turned that into `502` (`events service error: Server error '500 …'`) on `/events` and `500` on `/reserve`.
4. **Then the health-check cascade from Lab 8.** events' `/health` also needs a pool connection → 503 → readiness and **liveness** fail → events restarted (2× over the runs). Every gateway `/health` depends on events → gateway pods went NotReady (`ConnectionRefused` at 100/200 users) and were restarted by liveness (gateway restarts 10 → 11–13).

The 409s appear only at 40 users (16, event 5's 80 tickets gone, since Locust never pays and the held counter only grows, see Lab 8). At 50+ users, `/reserve` fails with 5xx *before* it can reach the inventory check, so the 409 column drops back to 0. It is masked, not fixed. At 10–30 users reserves stayed below 80+500, so no 409.

Prometheus cross-check (gateway counters over each run, in `raw.log`): load-50 `/events` 502 = 187.6, `/health` 503 = 52. load-100 `/events` 502 = 1 296, `/reserve` 502 = 111.5, `/health` 503 = 247.8. **The `/reserve` 500s that Locust saw (68+21 at 50 users, 336 at 100 users) do not exist in Prometheus.** That is the gateway error-handler bug from Lab 8 (§6).

## 3. DORA Metrics

Source data (`raw.log`): `git log upstream/main..origin/main` (the GitOps source), `gh run list` (CI), ArgoCD `status.history`, Argo Rollouts ReplicaSets and AnalysisRuns, and the timings measured in Labs 5–9.

| Metric | Value | How it was derived | DORA band (2023) |
|---|---|---|---|
| **Deployment frequency** | **17 production changes in 15 days** (≈1.1/day, bursty). 8 GitOps syncs of fork `main` in 13 h (ArgoCD history ids 0–7, 2026-09-27 18:14 → 09-28 07:21) + 9 Argo Rollouts revisions of gateway on 2026-10-05 (revisions 1–9, 6 ReplicaSets retained) | ArgoCD `status.history`; `kubectl get rs -l app=gateway` (6 RS; the spec's `… \| tr ' ' '\n' \| wc -l` prints **5** because the last name has no trailing newline) | On-demand capable (deploys are one commit/command); actual cadence is per lab session |
| **Lead time for changes** | **92 s** for the full pipeline: push `2fed1f9` 07:20:23 → CI build + bot commit `d343061` → ArgoCD synced 07:21:55. Other changes: `dd50f70` 2 min 26 s (Lab 5 measured 141 s to new pods), `7cfb905` 5 min 32 s, revert `b2d7978` 3 min 20 s (Lab 5: **211 s** to Healthy) | CI `createdAt` vs ArgoCD `deployedAt`. Variance = ArgoCD's 3-min poll, not CI (CI runs 34–98 s) | Elite (< 1 day) |
| **Change failure rate** | **1 / 8 = 12.5 %** of GitOps changes needed remediation (`7cfb905` "deploy new gateway version" → reverted by `b2d7978`). Argo Rollouts: 3 of 9 revisions aborted (2 manual aborts of `v3-bad`, 1 automatic). AnalysisRuns: **1 Successful, 1 Failed** (`Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)`). These 3 were deliberately bad versions | `git log`, `kubectl get analysisrun` | 12.5 % → Elite band (0–15 %); the rollout aborts are drills, not failures |
| **Time to restore service** | **< 1 s** canary abort (traffic off the bad pod; 5/5 stable after 12 s), 104 s automatic abort by analysis (60 s is `initialDelay`), **211 s** `git revert` via GitOps, 240 s detection + < 1 s fix for the Lab 6 payments incident, **154 s** Redis outage (Lab 8), **194 s** Postgres pod loss without PVC → **13 s** with PVC (Lab 9) | Labs 5–9 measurements | Elite (< 1 h) |

Caveat: one person, one cluster, 15 days, many deliberately broken deploys. The numbers show what the pipeline *can* do (one-commit deploys in ~1.5–5 min, seconds-level rollback), not a steady-state team cadence.

## 4. Top 3 Reliability Risks

1. **The read path collapses at ~28 RPS, and the ceiling falls as data grows.**
   - *Why it matters:* §2 shows `GET /events` aggregates the whole `orders` table on a Postgres capped at 200m CPU. The events pool raises instead of waiting, so overload becomes 5xx immediately and then a restart cascade. Every order sold makes the ceiling lower.
   - *Fix:* stop aggregating per request. Keep a `sold` counter per event, updated in the same transaction as the order insert, or cache `/events` for 1–2 s. Make the pool block with a timeout instead of raising. Raise the Postgres limit (the node was 98 % idle). Add `resources` that match measured usage (Task 2).
2. **Deep health checks wired into liveness turn any slow or missing dependency into a site-wide outage plus restart storms.**
   - *Why it matters:* events `/health` = Postgres + Redis, and gateway `/health` = events + payments. Both readiness *and* liveness use them. Measured twice: Lab 8, Redis down → 27 s to total outage, 27 restarts, 154 s recovery. Lab 10, overload → events and gateway killed by liveness in the middle of the load test, which made it worse.
   - *Fix:* liveness = "process answers" only (`/metrics` or a trivial `/livez`). Readiness = own critical dependencies only (events: Postgres). No transitive checks in the gateway. The events part of this was implemented and proven in Lab 8's bonus (gateway 0/5 → 5/5 during a Redis outage, recovery 116 s → 11 s).
3. **The live system is not what Git says, and automated sync is on.**
   - *Why it matters:* fork `main` (ArgoCD's source, `automated: {}`) still has gateway as a plain Deployment and Postgres **without** a volume. The real gateway Rollout and the Postgres PVC exist only in open lab PRs and in the cluster. Any commit to `main`, including the CI image-tag bot, triggers a sync. It would create a second `app=gateway` Deployment beside the Rollout and drop Postgres' volume mount, so the next Postgres pod starts empty (Lab 9: 746 orders lost, 194 s).
   - *Fix:* merge the Rollout + PVC manifests into the GitOps source (or point ArgoCD at a branch that has them), turn on `selfHeal` once Git is right, and add a CI check that fails if `k8s/` diverges from what is deployed.

Next in line: payments is not idempotent (Lab 8: 3 081 charges, 3 orders, under a 6 s latency), and the held-ticket counter only ever grows (sold out at 50 % real sales).

## 5. Toil Identification

Manual work repeated more than three times across Labs 1–9. The counts are matching command/output lines per lab in `raw.log` (an approximation of how often I did it), plus the submissions.

| Toil | How often (evidence) | How to automate | What it saves |
|---|---|---|---|
| **Post-experiment "is everything back?" verification:** diff each workload's spec against a saved reference, check pods Ready, gateway `/health`, error ratio back to baseline, wait 90 s | After every chaos step in Lab 8 and every DR step in Lab 9: **36** `restore check` lines (Lab 8: 32, Lab 9: 4), ~10 separate runs | One `steady-state` script (or a Litmus/Chaos Mesh steady-state probe) that compares live specs with Git (`argocd app diff`), queries Prometheus for error ratio and p99 vs. baseline, and exits non-zero. Better still: `selfHeal` so drift cannot persist | ~3–5 min per experiment and, more importantly, the class of mistake from Lab 8 where payments stayed at 6 s latency for 3.7 days unnoticed |
| **Unblocking ticket inventory** (`UPDATE events SET total_tickets=…` / `redis-cli FLUSHDB`) because the `event:{id}:held` counter never decrements | Lab 8 (1×), Lab 9 (1×), Lab 10 (**10** FLUSHDB lines, one before every load run): **12** in total | It is a bug, not a chore: decrement `held` on confirm and on reservation expiry (or derive "held" from live `reservation:*` keys). Until then, a pre-test hook in the load-test Job | All of it. It also removes a whole category of misleading 409s from load tests |
| **Manually watching canaries** (`kubectl argo rollouts get rollout gateway --watch`, promote/abort by hand) | Lab 7: **12** `argo rollouts get` lines; promote/abort decisions by eye at each step | Done in Lab 7's bonus: `AnalysisTemplate` on error rate auto-promotes and auto-aborts (aborted the bad version 104 s after creation, without a human). Remaining step: add a latency metric to the template and alert on `RolloutAborted` | A human per rollout for 2–5 min; Lab 7 measured how each extra manual step multiplied failed requests 1.6–3.4× |
| **Hand-run `pg_dump` / `kubectl cp` / `pg_restore`** | Lab 9: **20** lines (dumps, copies, restores, restore tests) | Lab 9 bonus CronJob (every 5 min, keep 5, verified rotation) + a `restore.sh` that waits for the *real* server (not `pg_isready`, see Lab 9) and runs a restore test into a scratch DB nightly | Recovery that does not depend on a file on my laptop. RPO capped at 5 min |

Not toil by this definition: re-seeding Postgres after restarts (only **2** times: Lab 4 and Lab 7, before the PVC) and port-forwards (once, in Lab 9).

## 6. Monitoring Gaps

What I wished I had been monitoring during the Lab 8 chaos experiments, and since:

- **Any alerting at all for the k3d deployment.** The Lab 6 alert rules (error rate, burn rate) live in the docker-compose Grafana stack. The in-cluster Prometheus that watched Labs 7–10 has **0 rule groups and no Alertmanager** (`/api/v1/rules` → `[]`, `/api/v1/alertmanagers` → `activeAlertmanagers: []`). Every outage in Labs 8–10 was found by me looking, not by a page. → port the Lab 6 rules plus the alerts below into a `PrometheusRule`/rules file and deploy Alertmanager in `monitoring`.
- **Latency.** The Lab 6 alerts are error rate (`for: 2m`) and SLO burn rate (`for: 5m`), both on 5xx only. Exp 2 (payments +2 s) produced **0 errors** while completed purchases fell 85 %. Nothing would page. → `histogram_quantile(0.99, … path="/reserve/{id}/pay") > 0.5` for 5 m.
- **Throughput drop.** Same experiment: user RPS 18.3 → 4.1 with error rate exactly 0. → alert when `sum(rate(gateway_requests_total{path!="/health"}[5m]))` falls below 50 % of the same time last hour.
- **Availability as the user sees it.** In Lab 8 Exp 3 the site was completely down, but `up{job="gateway"}` stayed at 5 (Prometheus scrapes pods by IP, not through the Service) and the gateway recorded **zero** user errors, because requests never reached it. → blackbox probe of `http://gateway:8080/events` through the Service (`probe_success == 0` for 1 m) + `kube_endpoint_address_available{endpoint="gateway"} == 0` from kube-state-metrics.
- **5xx that are never counted.** The gateway's reserve handler calls `e.response.json()` on events' plain-text 500. The exception escapes `metrics_middleware`, so those 500s are invisible: Lab 8 Task 2 (4 hidden), Lab 9 DROP (all `/reserve` 500s), Lab 10 (**425 hidden `/reserve` 500s** at 50–100 users). → fix the handler. Until then, count from an outer layer (ingress or blackbox).
- **Saturation of the real bottleneck.** No Postgres or pool metrics at all. `events_db_pool_size` is always 0. → `postgres_exporter`, a `PoolError` counter in events, and `rate(container_cpu_cfs_throttled_periods_total{pod=~"postgres.*"}[5m]) > 0.25`.
- **Business invariants.** Payments charged while orders failed (Lab 8: 3 081 vs 3). → `increase(payments_requests_total{path="/charge",status="200"}[10m]) − increase(events_orders_total[10m]) > 5`.
- **Redis** has no probes and no exporter. Its failure was only visible as a cascade.

**Which alert would have caught what actually broke?** In Lab 8 Exp 3 the outage was total, yet the error-rate alert saw only probe traffic. A **blackbox `probe_success` on the gateway Service** would have paged within a minute. In Lab 10 the root cause was CPU throttling of Postgres. A **throttling alert on Postgres** (or a pool-exhaustion counter) fires *before* the 5xx start, at the 40-user level, where errors were still 1.7 %.

## 7. Capacity Plan

- **Current ceiling: 22.6 RPS with zero errors (30 users), breaking at ~28.7 offered RPS (40 users)**, goodput saturating at **26–29 RPS**. The limiting resource is Postgres CPU (200m limit) driven by the `/events` aggregate, then the events pool.
- **For 2× traffic (≈ 45 RPS with no errors and p99 < 500 ms):** details and arithmetic in Task 2 below. In short: fix the `/events` query first (it is O(orders)), raise Postgres to 500m, events ×2 at 300m, gateway ×4 (it is mostly idle), payments ×2 for redundancy, Redis unchanged.
- **Rough cost** at $5/pod/month: today 9 pods = **$45/mo**. 2× plan = 10 pods + a larger Postgres = **≈ $55/mo** (+$5 if Redis gets a replica). The query fix costs nothing and moves the ceiling more than any replica count does.

---

## Task 2 — Capacity Plan with Numbers

### 10.7 Per-pod CPU at the breaking point (40 users)

`kubectl top pods -l app=<svc>` sampled 30–50 s into the 40-user repeat (01:05:12, 01:05:22, 01:05:32; full output in `raw.log`):

```text
gateway-6458cb677d-mwrz7   28m → 36m → 36m
gateway-6458cb677d-p8wwz   22m → 23m → 23m
gateway-6458cb677d-pbd54   22m → 20m → 20m
gateway-6458cb677d-wpkwk   19m → 19m → 19m
gateway-6458cb677d-xt6rm   16m → 18m → 18m
events-76bc7c449f-zpc6w    17m → 82m → 82m
payments-75659bdfd4-95zds   7m →  9m →  9m
postgres-68466c5ccd-6mzfw  83m → 157m → 157m
redis-6fcfb5475d-d4qf2      7m →  6m →  6m
```

cgroup `cpu.stat` deltas over the same run (01:04:42 → 01:09:03):

| Pod | CPU used | throttled periods | time throttled |
|---|---|---|---|
| **postgres** | 9.25 s | **+263** | **58.8 s** |
| events | 6.36 s | +18 | 0.8 s |
| gateway-mwrz7 | 3.39 s | 0 | 0 |
| payments | 2.01 s | 0 | 0 |

From the sampler during the other runs: postgres 180–192m at 50/100 users (pinned at the 200m limit), events 129–177m, gateway 15–92m per pod, payments 7–12m, Redis 5–11m.

- **CPU-constrained: Postgres** (at its limit and heavily throttled), then **events** (82–177m of 200m, plus a 10-connection pool that raises).
- **Idle: payments** (< 10m: the Locust mix never pays), **Redis** (< 11m) and **the gateway** (5 pods at 16–36m each = ~120m total for ~28 RPS, i.e. ~15 % of their 1 000m combined limit).

### 10.8 2× plan (target ≈ 45 RPS with no errors, p99 < 500 ms, same request mix)

Scaling factors from the 40-user measurement (~28 RPS): Postgres ≈ 5.5m per RPS, events ≈ 3m per RPS, all gateway pods together ≈ 4.1m per RPS (116m at 01:05:22). At 45–57 RPS, without changing code:

| Service | Today | Needed at 2× (measured × 2, +40 % headroom) | Plan | requests / limits |
|---|---|---|---|---|
| **postgres** | 1 × 200m | 57 RPS × 5.5m ≈ 310m → ~430m | **1 replica, 500m limit**, plus the `/events` fix (below). With the fix, Postgres load drops to the per-request cost of a primary-key lookup, and 200m would likely suffice | 250m / **500m**, 256Mi / 512Mi |
| **events** | 1 × 200m, pool 10 | 57 × 3m ≈ 170m → ~240m; a single pod is also a SPOF | **2 replicas**, pool 10 each (20 connections ≪ Postgres `max_connections` 100), pool blocks with a 2 s timeout instead of raising | 150m / **300m**, 128Mi / 256Mi |
| **gateway** | 5 × 200m (~15 % used) | 57 × 4.1m ≈ 235m total → fits in 2 pods | **4 replicas**: capacity for ~3× in CPU; 4 keeps one pod of slack per node failure and lets the canary use 25/50/75 % steps | 50m / 200m, 64Mi / 256Mi |
| **payments** | 1 × 200m (< 10m used) | negligible CPU | **2 replicas** for availability, not load (Lab 6 incident: one pod = full checkout outage) | 50m / 200m |
| **redis** | 1 × 200m (< 11m used) | negligible CPU, a few hundred keys | **Stays single-pod** for capacity. It is still a SPOF (Lab 8), but with the Lab 8 events fix its loss only blocks `/reserve`. Add persistence (AOF on a PVC). Sentinel/replica only if reservations must survive a node loss | 50m / 200m |

**DB connections:** the single pool → single Postgres path is a bottleneck today only because of *how* it fails (immediate `PoolError`) and *what* runs on it (the aggregate). At 2× with two events replicas it is 20 connections against `max_connections=100`, so that is fine. A pooler (PgBouncer) becomes worth it past ~4–5 events replicas.

**What actually doubles capacity, in order of leverage:**
1. Replace the per-request `SUM(orders)` with a maintained `sold` counter (or a 1–2 s cache of `/events`). That removes the O(orders) work that pins Postgres.
2. Raise Postgres to 500m. The node had 10+ idle cores, and the 200m limit was the first wall.
3. Make the events pool block with a timeout, and split liveness from readiness (Risk 2), so a short overload degrades latency instead of producing restarts.
4. Only then add replicas: events ×2, payments ×2, gateway down to 4.

**Cost at $5/pod/month:** today 5 gateway + events + payments + redis + postgres = **9 pods → $45/mo**. Plan: 4 gateway + 2 events + 2 payments + 1 redis + 1 postgres = **10 pods → $50/mo**, + ~$5 for Postgres at 2.5× CPU = **≈ $55/mo** (+$5 for a Redis replica if wanted). Backups (Lab 9 CronJob, 5 × ~0.7 MB) are noise.

To verify the plan, re-run the same `locustfile.py` at 60 and 80 users after each step. The success criterion is the breaking-point rule from 10.2 (5xx < 0.5 %, p99 < 500 ms) at ≥ 45 RPS.

---

## Bonus — SRE handbook

Option B: [`submissions/runbooks/quickticket-handbook.md`](runbooks/quickticket-handbook.md).

---

## Things the lab text does not mention

| # | What happened | What I did |
|---|---|---|
| 1 | The system broke at **40 users**, below the 50/100/200 levels the lab suggests. The cause is data volume: 19 735 orders from Lab 8/9 mixedload make every `GET /events` a 5 ms aggregate on a 200m-limited Postgres. A freshly seeded DB would show a much higher ceiling. | Added 20/30/40-user runs to locate the breaking point, and documented the cause with `EXPLAIN ANALYZE`, `kubectl top` and cgroup throttling. |
| 2 | Locust exits with code 1 whenever any request failed, so with `backoffLimit: 0` the Job ends **`Failed`**, not `Complete`, and `kubectl wait --for=condition=Complete` just times out. | My runner waited for the timeout, then read `kubectl logs job/load-N`. The pods stay until `ttlSecondsAfterFinished`. |
| 3 | Locust counts 409 as a failure. At ≥ 50 users the 409 column is 0 because `/reserve` fails with 5xx before it reaches the inventory check. That is masking, not absence. | 5xx, 409 and TCP errors are separated from the Locust error report. |
| 4 | `kubectl get rs … \| tr ' ' '\n' \| wc -l` (the spec's deployment count) prints 5 for 6 ReplicaSets: no trailing newline. | Counted from the listing. |
| 5 | Two of my own runner invocations (`load-100`/`load-200` in a zsh `for` loop) did not run, because zsh does not word-split `set -- $L`. | They are left in `raw.log` with a note. The runs were then executed explicitly. |
