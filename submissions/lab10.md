# QuickTicket Reliability Review — Lab 10 Capstone

**Student:** jakefish18
**Repo:** https://github.com/jakefish18/SRE-Intro · **Branch:** `feature/lab10`

PR checklist:
```text
- [x] Task 1 done — load tests, DORA, toil, reliability review (all 7 sections)
- [x] Task 2 done — detailed capacity plan with numbers
- [x] Bonus Task done — 2-page SRE handbook (submissions/runbooks/quickticket-handbook.md)
```

> Load tests ran **in-cluster** as Kubernetes Jobs (`locustio/locust:2.43.4`) hitting
> `http://gateway:8080` through kube-proxy (so all 5 gateway replicas are exercised —
> not `port-forward`, which pins one pod). Redis was `FLUSHDB`-ed before every run so
> stale reservation-holds didn't pollute inventory. `locustfile.py` (repo root) uses a
> 7:2:1 list/reserve/health mix, reserving across events 3 (500 tickets) and 5 (80).

---

## 1. SLO Compliance

| SLO | Target | Observed | Status |
|-----|--------|----------|--------|
| Availability (non-5xx) | ≥ 99.5% (5xx < 0.5%) | 100% @10u · 99.86% @50u · 62% @75u · 53% @100u | ✅ ≤50u · ❌ >50u |
| Gateway request p99 | < 500ms | 34ms @10u · 390ms @50u · 1100ms @75u · 3200ms @100u | ✅ ≤50u · ❌ >50u |
| Inventory 409s (product, not SLO) | n/a | 0 @10u · 36 @50u (event 5 sold out) | expected behavior |

QuickTicket meets both SLOs up to **~50 concurrent users / ~37 RPS**; beyond that it
violates both simultaneously (sharp knee between 50u and 75u).

## 2. Load Test Results

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) |
|------:|-----:|----:|----:|----:|----:|---------------:|----------------:|
| 10  | 2/s  | 7.7  | 11ms  | 19ms   | 34ms   | **0%**            | 0 |
| 50  | 5/s  | 36.7 | 7ms   | 120ms  | 390ms  | **0.14%** (3/2199) | 36 (event 5) |
| 75  | 8/s  | 42.1 | 400ms | 860ms  | 1100ms | **37.6%** (944/2509) | 0 |
| 100 | 10/s | 51.5 | 500ms | 1200ms | 3200ms | **47.2%** (1457/3085) | 0 |

**Breaking point:** between **50 and 75 users**. At 50u the system is healthy (p99
390ms, 5xx 0.14%); by 75u it has collapsed (p99 1100ms, 5xx 37.6%). **Capacity ceiling
≈ 50 users / ~37 RPS.** Note the failure signature at 75u/100u is almost entirely
**502/503/500 on `/events`** (not 409) — the read path itself falls over, which points
at a backend bottleneck, not inventory contention.

## 3. DORA Metrics

| Metric | Value | Source data |
|--------|-------|-------------|
| **Deployment Frequency** | ~9 changes over ~12 weeks (≈ weekly) | 9 PRs (`gh pr list`), 6 gateway Rollout revisions, 5 CI runs |
| **Lead Time for Changes** | < ~5 min commit→prod | CI build (~1 min) + ArgoCD poll interval (≤3 min); measured CI run = 55s in Lab 5 |
| **Change Failure Rate** | 1 of 2 canary AnalysisRuns failed (50%) — but **caught pre-prod** and auto-aborted | `kubectl get analysisrun` → 1 Successful, 1 Failed |
| **Time to Restore** | canary abort **~seconds** (Lab 7); `git revert`→ArgoCD **~3 min** (Lab 5); DB `pg_restore` **11s**, PVC pod-restart **~3s** (Lab 9) | rollout + git history |

> Solo-student cadence, so absolute numbers are modest. The 50% CFR is one *deliberately*
> broken canary (Lab 7 B.5) out of two analysis runs — and progressive delivery meant it
> was aborted automatically before a single user was affected. That is the DORA story that
> matters: failures are cheap because they are caught at 20% canary weight, not in prod.

## 4. Top 3 Reliability Risks

1. **`events` is a single replica AND the capacity bottleneck.** Under load it pins at
   its **200m CPU limit** (measured — see §Capacity) and starts returning 502/500, while
   the 5 gateway replicas sit idle. One slow/dead `events` pod = whole product down.
   *Fix:* scale `events` to ≥3 replicas, raise its CPU limit, add an HPA on CPU.
2. **Dependency-aware readiness cascade (found in Lab 8).** Both `gateway` and `events`
   used `/health` as their readiness probe, so a Redis blip flips every pod NotReady →
   the Service loses all endpoints → **total** outage from a *partial* failure.
   *Fix:* shallow (TCP) readiness — done for `gateway` in Lab 8; still to apply to `events`.
3. **No latency / saturation alerting.** Lab 6 alerts fire on 5xx only. Lab 8 proved a
   2s payments slowdown stays "200 OK" and pages no one; and at the breaking point
   `events` was CPU-throttled with no alert. *Fix:* a p99-latency SLO alert and a
   CPU-saturation alert on `events`.

## 5. Toil Identification

| Toil | How often | How to automate | What it saves |
|------|-----------|-----------------|---------------|
| Re-seed Postgres (`psql < seed.sql`) after every pod restart | every restart, ≥5× across Labs 4–9 | **PVC** (done Lab 9) so data persists + a one-shot init Job | manual `kubectl exec` + remembering the file each time |
| Re-create `kubectl port-forward` after pod/session restarts | many times (Labs 5, 6, 9) | run load/clients **in-cluster** (Jobs) or a persistent forward script | ~1 min + context-switch per drop |
| Babysitting canary rollout with `kubectl argo rollouts get --watch` | every rollout, ~6× in Lab 7 | **AnalysisTemplate** auto-promote/auto-abort (done Lab 7 bonus) | minutes of staring per deploy + human error |

## 6. Monitoring Gaps

- **Latency was invisible.** During Lab 8 I only had error-rate; the 2000ms payments
  injection was 0% errors and would never have paged. I wish I'd had a **p99 latency
  dashboard + SLO burn alert** per path.
- **Symptom, not cause.** I alerted on gateway 5xx, not on `up{job="payments"}==0` or
  `events` health — so alerts told me *something* was wrong, not *what*.
- **No saturation signal.** At the breaking point `events` was pinned at its CPU limit
  with no alert; a `container_cpu ... / limit > 0.8` alert would have caught the ceiling
  *before* users saw 502s.
- **The alert that would have caught the real break:** `events` CPU-at-limit (saturation)
  and/or gateway p99 > 500ms for 2m — either fires minutes before the 5xx cascade at 75u.

## 7. Capacity Plan

- **Current ceiling:** ~**50 concurrent users / ~37 RPS** at p99 390ms & 5xx 0.14%.
  Collapses by 75u (p99 1100ms, 5xx 37.6%).
- **Bottleneck:** `events` (single replica, `limits.cpu: 200m`), CPU-throttled at load.

**Per-pod CPU at the breaking point (75u load, `kubectl top pods`):**
```
events    200m   ← PINNED at its 200m CPU limit (throttled → 502/500)  ★ bottleneck
gateway   35–54m each × 5 pods  (~215m total; each has 200m limit → idle)
postgres  60–67m
payments  9m     (idle)
redis     4m     (idle)
```

**For 2× traffic (~75 RPS / ~100 users):**
| Service | Now | 2× plan | Why |
|---------|-----|---------|-----|
| events | 1 replica, 200m limit | **3–4 replicas, 500m–1 core limit**, HPA on 70% CPU | the actual bottleneck |
| gateway | 5 replicas, 200m | keep **5** (idle headroom) | not constrained |
| payments | 1 replica | **2** (HA, still idle on CPU) | remove the SPOF, not for load |
| postgres | 1 + PVC | keep **1**, but add PgBouncer + raise `DB_MAX_CONNS` | watch pool, not CPU |
| redis | 1 | keep **1** (4m CPU); add a replica only for HA | idle |

**Rough cost** (~$5/pod/mo): 4 events + 5 gateway + 2 payments + 1 postgres + 1 redis +
~2 platform (prometheus, argo) ≈ **15 pods ≈ $75/mo**. The cheap, high-leverage move is
the **+3 events replicas (~$15/mo)** — it directly lifts the ceiling.
