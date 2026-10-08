# QuickTicket Reliability Review

Capstone reliability review for QuickTicket. Load tests run **in-cluster** as Kubernetes Jobs (Locust 2.43.4)
against `http://gateway:8080` so kube-proxy load-balances across all 5 gateway replicas. Redis `FLUSHDB` between
runs so stale reservation holds don't pollute inventory. Metrics cross-checked against in-cluster Prometheus.

---

## 1. SLO Compliance

| SLO | Target | Observed | Status |
|-----|--------|----------|--------|
| Availability (gateway 5xx rate) | ≥ 99.5 % (≤ 0.5 % 5xx) | 0 % 5xx up to 100 users; **31 % 5xx at 200 users** | ✅ within capacity / ❌ beyond it |
| Latency (p99, read path) | < 500 ms | 21 ms @10u · 52 ms @50u · 100 ms @100u · **700 ms @200u** | ✅ ≤100u / ❌ @200u |
| Migration safety | 0 downtime | email migration ran in 0.216 s, **0** added 5xx (Lab 9) | ✅ |
| DB recovery (RTO) | < 5 min | 43 s (dump restore) → **10 s** with PVC (Lab 9) | ✅ |

The SLOs hold comfortably at the design load (≤ 100 concurrent users) and are violated only past the capacity
ceiling identified below.

## 2. Load Test Results

FLUSHDB before every run. 409 = inventory exhausted on the reserved events (expected product behaviour, **not**
a system failure); the SLO tracks 5xx only.

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) |
|------:|-----:|----:|----:|----:|----:|---------------:|----------------:|
| 10    | 2/s  | 7.7 | 11 ms | 16 ms | 21 ms  | 0 %            | 0               |
| 50    | 5/s  | 36.4| 6 ms  | 21 ms | 52 ms  | 0 %            | 24              |
| 100   | 10/s | 73.7| 4 ms  | 23 ms | 100 ms | ~0 % (2 transient) | 298         |
| 200   | 20/s | 129.8| 170 ms | 490 ms | 700 ms | **31 %** (502) | 106          |

**Breaking point (10.2):** between 100 and 200 users. At **100 users / ~74 RPS** the system is healthy
(0 % 5xx, p99 100 ms). At **200 users / ~130 RPS** it collapses — Prometheus status breakdown during the 200u
run:

```
status=200  62.3 %
status=502  31.4 %   <-- gateway can't reach a saturated events service
status=503   4.8 %
status=504   0.03 %
status=409   1.4 %
```

**Capacity ceiling ≈ 100 concurrent users / ~74 RPS** with the current single-replica events service.

## 3. DORA Metrics

Derived from Git history + Argo Rollouts + PR history of this project.

| Metric | Value | Source / method |
|--------|-------|-----------------|
| Deployment Frequency | ~1 per lab (6 lab PRs merged to `main`, 10 `feat(lab…)` commits) | `gh pr list --state merged`, `git log --grep 'feat(lab'` |
| Lead Time for Changes | **< 10 min** commit→prod | CI build (~2–3 min) + ArgoCD 3-min poll interval (Lab 5) |
| Change Failure Rate | **low** — the one bad canary in Lab 7 was auto-aborted by the AnalysisRun **before** reaching 100 % (never served full traffic) | Lab 7 `AnalysisRun` Failed → rollout auto-abort |
| Time to Restore (MTTR) | **seconds** for a bad deploy (`argo rollouts abort` ≈ 2–3 s, Lab 7) vs ~3 min for a `git revert`→ArgoCD path; **10 s** for a DB pod loss with the PVC (Lab 9) | Labs 7 & 9 measurements |

Against the DORA 2023 elite bands (deploy on-demand, lead time < 1 day, CFR 0–15 %, restore < 1 h) the project
lands in the **elite** range on lead time and restore time — expected for a solo GitOps setup with progressive
delivery, not a claim of production maturity.

## 4. Top 3 Reliability Risks

1. **Single-replica, CPU-bound `events` service is the capacity ceiling.** At 200u the events pod hit ~178m of
   its 200m CPU limit (89 %) and started returning errors that surface as gateway 502s (31 % 5xx). *Fix:* run
   `events` with ≥ 2–3 replicas + an HPA on CPU, and raise its CPU limit. It is the first thing to scale.
2. **Redis is a single point of failure for the whole read path.** Lab 8 showed that when Redis is down,
   `/events` (a "read-only" endpoint) times out to 504 because availability calculation calls Redis
   synchronously — a cascading outage, not a degraded reserve. *Fix:* fail-fast Redis timeout + treat
   unavailable Redis as `held = 0`; longer term, a replicated Redis (Sentinel).
3. **Backup RPO is only as good as the dump cadence.** A single manual `pg_dump` loses everything written since
   the last dump. *Fix (partly done in Lab 9):* the 5-minute backup CronJob caps RPO at ≤ 5 min; for
   seconds-level RPO, add WAL archiving / streaming replication.

## 5. Toil Identification

| Toil task | Frequency | How to automate | What it saves |
|-----------|-----------|-----------------|---------------|
| Re-seed Postgres (`kubectl exec … psql < seed.sql`) after every pod restart | Every DB restart (many times across Labs 4–9) | PVC on Postgres (done in Lab 9 Bonus) + seed as an init Job/initContainer | Eliminates manual re-seed; data survives restarts |
| Re-create `kubectl port-forward` after pod restarts (Alembic, Prometheus UI) | Several times per lab session | A tiny wrapper script / `kubefwd`, or run Alembic as an in-cluster Job | ~1–2 min each time + lost-connection debugging |
| Manually watching a canary (`kubectl argo rollouts get rollout --watch`) and deciding promote/abort | Every rollout in Labs 7–8 | The AnalysisTemplate (Lab 7 Bonus) auto-promotes/auto-aborts on the error-rate metric | Removes a human from the deploy loop entirely |

## 6. Monitoring Gaps

- **Latency SLO alerting was missing.** Lab 6 alerts fired on error rate only. In Lab 8, injecting 2000 ms of
  payment latency produced **0 % errors** but a 2485 ms `/pay` p99 — a "slow but 200 OK" degradation that would
  never have paged anyone. The alert I actually needed: p99 of `gateway_request_duration_seconds` per path
  above a threshold.
- **No per-dependency saturation alert.** The events-CPU ceiling that caused the 200u collapse would have been
  visible ~minutes earlier as CPU approaching its limit — an alert on container CPU / throttling would catch it
  before users see 502s.
- **No "backup freshness" alert.** Nothing would tell me if the backup CronJob silently stopped producing
  dumps — an alert on the age of the newest `/backups/*.dump` would close the RPO blind spot.

## 7. Capacity Plan (see Task 2 for the detailed numbers)

- **Current ceiling:** ~100 concurrent users / **~74 RPS** at healthy SLO (0 % 5xx, p99 100 ms).
- **Bottleneck:** the single `events` pod (CPU-bound). Gateway, payments, redis all have large headroom.
- **For 2× traffic (~150 RPS):** scale `events` to 3 replicas + HPA and raise its CPU limit; leave gateway at 5
  (it was ~25 % utilised). Detailed numbers below.

---

# Task 2 — Capacity Plan with Numbers

### Per-pod CPU at the breaking-point load (200 users, ~130 RPS)

```
gateway (×5)   ~48–59m each   (limit 200m → ~25 % utilised)   → plenty of headroom
events (×1)    178m           (limit 200m → ~89 % utilised)   → THE bottleneck
payments (×1)  11m            (idle — Locust doesn't call /pay)
postgres (×1)  81m
redis (×1)     5m             (idle)
```

**CPU-constrained service: `events` (single replica at ~89 % of its limit). Idle: payments, redis.** That tells
us exactly what to scale — events first, nothing else urgently.

### Plan for 2× traffic (~150 RPS sustained, healthy SLO)

| Service | Now | 2× plan | Requests / Limits | Why |
|---------|-----|---------|-------------------|-----|
| events   | 1 replica | **3 replicas + HPA (target 70 % CPU)** | req 200m / limit 500m | The bottleneck; needs both more pods and more CPU per pod |
| gateway  | 5 replicas | **5 (unchanged)** | req 50m / limit 200m | Was only ~25 % utilised at 2× the target load |
| payments | 1 replica | **2 replicas** | req 50m / limit 200m | Idle now, but add 1 for redundancy once `/pay` traffic is real |
| postgres | 1 (PVC) | **1 + PVC (unchanged)** | req 100m / limit 500m | 81m at load; single primary is fine at this scale |
| redis    | 1 replica | **1, consider Sentinel** | req 50m / limit 100m | 5m CPU; capacity-fine, but it's a SPOF (see Risk #2) |

- **Redis:** single-pod is fine on *capacity*, but it's a reliability SPOF for the read path — a replicated
  Sentinel setup is a reliability upgrade, not a throughput one.
- **DB connections:** the single pooler→single-Postgres path was **not** a bottleneck in testing (Postgres
  ~81m, `DB_MAX_CONNS` never bound in Lab 8) — no change needed for 2×.
- **Rough cost:** ~+2 events pods and +1 payments pod = **+3 pods ≈ +$15/mo** at a $5/pod/mo small-cloud
  assumption. Everything else stays put.

---

## Bonus

See [`submissions/runbooks/quickticket-handbook.md`](./runbooks/quickticket-handbook.md) (Option B — 2-page SRE
handbook: architecture, deploy flow, monitoring, incident response, backup/restore).
