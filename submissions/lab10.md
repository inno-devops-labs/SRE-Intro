# QuickTicket Reliability Review — Lab 10 Capstone

> Deliverables: `locustfile.py` (repo root, committed) + this review.
>
> Load-test numbers, DORA counts, and `kubectl top` output are filled from a
> live run: Locust runs **in-cluster** as a Job against `http://gateway:8080`
> (never through `kubectl port-forward` — that pins one pod), with a
> `redis-cli FLUSHDB` between runs.

---

## 1. SLO Compliance

| SLO | Target | Observed | Status |
|-----|--------|----------|--------|
| Availability (non-5xx) | ≥ 99.5% | <!-- from load test --> | |
| Latency p99 (`/events`) | < 500 ms | <!-- --> | |
| Latency p99 (`/pay`) | < 1 s | <!-- --> | |
| Error budget (5xx) | < 0.5% | <!-- --> | |

<!-- Fill "Observed"/"Status" from your breaking-point run in §2. -->

## 2. Load Test Results

<!-- PASTE: `kubectl logs job/load-<N> | tail -40` per level. FLUSHDB between runs. -->

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) |
|------:|-----:|----:|-----:|-----:|------:|---------------:|----------------:|
| 10    | 2/s  | 7.9 | 8ms  | 15ms | 23ms  | 0%             | 0               |
| 50    | 5/s  | 37.0| 5ms  | 27ms | 140ms | 0.14% (3/2218) | 50 (event 5)    |
| 100   | 10/s | 69.0| 6ms  | 410ms| 610ms | **13.8% (570/4136)** | 205       |

_(Load-gen ran in-cluster as a Job → `http://gateway:8080`, kube-proxy
load-balanced across all 5 gateway pods; `FLUSHDB` between runs. Rate limiter
disabled for capacity testing.)_

**Breaking point:** the first level where **5xx > 0.5%** OR **p99 > 500 ms**.
Record the user count AND the RPS at that point — that RPS is the capacity
ceiling. Note: **409 on `/reserve` is expected product behaviour** (event 5 has
only 80 tickets and sells out) — it is NOT counted against the SLO; only 5xx is.

**Breaking point = 100 users / ~69 RPS.** At 100u the system crosses *both*
thresholds: **p99 = 610ms (> 500ms)** and **real 5xx = 13.8%** (570 of 4136 —
360× 502, 136× 500, 58× 503 on `/health`; the remaining 205 "failures" are 409
inventory sold-outs on events 3 & 5, which do NOT count). 50u is still healthy
(p99 140ms, 0.14% 5xx). So the **healthy ceiling is ~50u / ~37 RPS**; 100u
saturates the single-replica `events` service (its DB pool + CPU), which is the
bottleneck (see §Task 2 CPU).

## 3. DORA Metrics

<!-- PASTE the source commands' output; then summarize in the table. -->

| Metric | Value | Source |
|--------|-------|--------|
| Deployment Frequency | 4 rollout revisions (rs 2–6) over the session; 39 commits on branch | `kubectl get rs -l app=gateway` = 4; `git rev-list --count HEAD` = 39 |
| Lead Time (commit→prod) | ~CI build + ~3 min ArgoCD poll | Lab 5 GitOps pipeline |
| Change Failure Rate | 1 failed deploy of 4 rollouts ≈ 25% (the Lab 7 `v3-bad` abort); 0 AnalysisRuns this run | `kubectl get analysisrun` = 0; 1 observed rollout abort |
| Recovery Time (MTTR) | canary abort ≈ **3 s** (measured, Lab 7); `git revert`→sync ≈ 3 min | Lab 7 vs Lab 5 |

DORA elite reference (2023): deploy on-demand, lead time < 1 day, CFR 0–15%,
recovery < 1 hour. As a solo student most of these land in the elite band on
speed but the sample size is tiny — report honestly.

## 4. Top 3 Reliability Risks

1. **Single-pod Redis is a hard dependency for reservations.** Lab 8 showed
   `/reserve` fails entirely when Redis is scaled to 0, while `/events` keeps
   serving. A Redis pod loss = no ticket sales. *Fix:* HA Redis (replica +
   Sentinel) or managed Redis; degrade reserve gracefully with `Retry-After`.
2. **Latency has no alert.** Lab 6 alerts fire on error rate, not on p99. A slow
   dependency (Lab 8 payment-latency experiment) degrades users while every
   dashboard stays green. *Fix:* a p99 latency SLO alert per path.
3. **Postgres capacity is a single vertical instance with a small connection
   pool.** Under the Lab 8 `DB_MAX_CONNS=3` + mixed-load scenario, `/reserve`
   p99 balloons from pool queueing. *Fix:* raise pool + add a pgbouncer pooler;
   longer term a read replica.

## 5. Toil Identification

| Toil task | How often | How to automate | What it saves |
|-----------|-----------|-----------------|---------------|
| Re-seed Postgres after every pod restart (`psql < seed.sql`) | Every restart pre-PVC (>3×) | PVC (Lab 9 Bonus) so data survives restarts | ~2 min/restart + human attention |
| Re-create `kubectl port-forward` after pod churn | Many times/lab | In-cluster Jobs (loadgen/locust) + Prometheus in-cluster | avoids the port-forward-pins-one-pod trap entirely |
| Manually watching a canary and clicking `promote` | Every deploy | AnalysisTemplate auto-gate (Lab 7 Bonus) | removes a human from the deploy loop |

## 6. Monitoring Gaps

- **What I wished I'd been monitoring in Lab 8:** per-path p99 latency and a
  saturation signal (DB connection-pool utilization). The pod-kill and
  Redis-down experiments were visible in error rate, but the payment-latency
  experiment was invisible to error-rate-only monitoring.
- **The alert that would have caught the real break:** a p99-latency SLO burn
  alert. It catches "slow but successful" — the failure mode that error-rate
  alerting structurally cannot see.

## 7. Capacity Plan

- **Current ceiling:** ~37 RPS healthy (50u); breaks at ~69 RPS (100u).
- **For 2× traffic:** <!-- replica counts per service from §Task 2 -->
- **Rough cost:** <!-- pods × $5/pod/mo -->

---

## Task 2 — Capacity Plan with Numbers (4 pts, optional)

### Per-pod CPU at breaking point

```text
Sampled every 15s through the load run (peak during 50u→100u ramp):
  gateway  ~30m/pod  (peak 159m across 5 pods)   → plenty of headroom per pod
  events   ~64–91m   (SINGLE replica)            → the CPU-constrained service
  payments ~5–8m     (idle)
```
_The **events service (1 replica) is the bottleneck** — it's the CPU-heavy path
(DB queries + pool) and the source of the 5xx at 100u. Gateway spreads across 5
pods and stays lightly loaded; payments is idle. → to raise the ceiling, scale
`events` (and its DB pool) first, not gateway._

Which service is CPU-constrained vs idle → that's what to scale.

### 2× plan

- **Replicas:** gateway <!-- ? -->, events <!-- ? -->, payments <!-- ? -->.
- **Resource requests/limits:** <!-- based on observed CPU -->.
- **Redis:** single-pod is the availability risk (see §4.1); for 2× throughput a
  single Redis likely still has headroom (it's an in-memory hold store), so the
  driver to replicate is *availability*, not CPU.
- **DB connections:** the single-pooler→single-Postgres path is the first
  bottleneck under write-heavy load — size the pool to `replicas(events) ×
  per-pod-conns` and keep it under Postgres `max_connections`.
- **Cost:** <!-- total pods × $5/pod/mo -->.

---

## Bonus Task — Walkthrough (2 pts, optional)

Chose: <!-- Option A (video link) OR Option B (handbook at submissions/runbooks/quickticket-handbook.md) -->

<!-- If Option A: paste unlisted YouTube link.
     If Option B: create submissions/runbooks/quickticket-handbook.md. -->

---

## PR checklist

```text
- [x] locustfile.py committed at repo root
- [~] Task 1 done — load tests, DORA, toil, reliability review (fill PASTE from live run)
- [~] Task 2 done — detailed capacity plan with numbers
- [ ] Bonus Task done — demo video OR SRE handbook
```
