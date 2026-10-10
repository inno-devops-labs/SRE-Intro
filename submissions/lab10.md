# QuickTicket Reliability Review

## 1. SLO Compliance

| SLO | Target | Observed | Status |
|-----|--------|----------|--------|
| Availability | 99.5% (Lab 3) | 100% at 10u/25u, 81.6% at 50u | ⚠️ Breached under load |
| Latency (p99 < 500ms) | 95% under 500ms | p99=63ms @10u, ~480ms @25u, 910ms @50u | ⚠️ Breached at 50u |

## 2. Load Test Results

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | Notes |
|------:|-----:|----:|----:|----:|----:|---------------:|-------|
| 10 | 2/s | 7.73 | 9ms | 13ms | 63ms | 0% | Healthy |
| 25 | 3/s | ~19 | 9ms | ~50ms | ~480ms | 0% | Near threshold, still healthy |
| 50 | 5/s | 30.33 | 290ms | 690ms | 910ms | 18.4% | **Breaking point exceeded** |

**Breaking point:** Between 25 and 50 concurrent users. At 50u, error rate (18.4%) far exceeds the 0.5% SLO threshold and p99 latency (910ms) nearly doubles the 500ms target. The system degrades sharply and non-linearly between these two load levels — not a gradual slope but a cliff, consistent with resource saturation (connection pool exhaustion) rather than simple queueing delay.

## 3. DORA Metrics

| Metric | Value | Source |
|--------|-------|--------|
| Deployment Frequency | 6 ReplicaSets for gateway (revisions from Lab 7 canary experiments) | `kubectl get rs -l app=gateway` |
| Total commits (main) | 53 | `git log --oneline main \| wc -l` |
| Change Failure Rate | 1 AnalysisRun, Successful (0 failed) in tracked history; 1 rollout aborted manually (Lab 7 bad-version test) | `kubectl get analysisrun` |
| Lead Time (commit → live) | ~45s CI build + up to 3min ArgoCD poll ≈ **~4 min** (or instant with `argocd app sync`) | CI workflow duration (Lab 5) |
| Recovery Time (rollout abort) | <1 second (Lab 7) | `kubectl argo rollouts abort` |
| Recovery Time (git revert) | ~4-5 minutes (CI build + ArgoCD sync) | Lab 5 |

**Assessment:** Solo-student project, not a full platform team, so elite DORA targets aren't the right bar. Deployment frequency is on-demand (every push to main triggers CI). Lead time (~4 min) is well within "elite" territory when using manual sync; automatic ArgoCD polling adds up to 3 min. Change failure rate is low (1 tracked failure out of several canary tests, and it was caught by design). Recovery time via Rollout abort (<1s) massively outperforms the git-revert path (~4-5 min) — this is the single biggest reliability lever available in this stack.

## 4. Top 3 Reliability Risks

1. **Events service has zero horizontal redundancy.** Only 1 replica exists for `events`, and it showed the highest CPU usage (56m) of any service under load — it's both a single point of failure and the primary bottleneck. **Fix:** scale events to 3+ replicas and add a Deployment/HPA.

2. **No circuit breaker on gateway→payments calls (Lab 11 TODOs still no-ops).** Under load, the gateway retries or waits on failing/slow payment calls, amplifying load on an already struggling dependency instead of failing fast. **Fix:** implement the circuit breaker pattern from Lab 11 scaffolding.

3. **Sharp non-linear breaking point between 25 and 50 concurrent users.** The system has almost no graceful degradation — going from 0% to 18% error rate in one step suggests connection pool exhaustion (likely Postgres `DB_MAX_CONNS`) rather than smooth backpressure. **Fix:** add connection pool metrics/alerts and tune pool size + add PgBouncer-style connection pooling in front of Postgres.

## 5. Toil Identification

| Toil Item | How Often | How to Automate | What You'd Save |
|-----------|-----------|------------------|------------------|
| Re-seeding Postgres after pod restart (`psql < seed.sql`) | 5+ times across Labs 4, 5, 7, 9 (before PVC was added) | Init container or Job that seeds only if `events` table is empty | ~2 min per occurrence, plus the confusion of debugging "why is /events returning empty" |
| Re-creating `kubectl port-forward` sessions after they silently drop | 8+ times across Labs 3-9 | Wrapper script with auto-restart loop (`while true; do kubectl port-forward ...; sleep 1; done`) | ~30s per occurrence + context-switching cost of noticing it died |
| Manually watching canary rollouts with `--watch` instead of trusting AnalysisTemplate | 4+ times in Lab 7 | Rely fully on automated AnalysisRun + Prometheus-based auto-promote/abort (built in Lab 7 Bonus) | ~2-5 min of active attention per deploy |

## 6. Monitoring Gaps

- During Lab 8's chaos experiments, the only real-time signal was manually-run Prometheus queries — there was no live dashboard showing golden signals for the k3d cluster (Grafana from Lab 3 only scrapes docker-compose, not k3d pods).
- Lab 6's alert rules only covered **error rate**, not **latency**. Experiment 2 in Lab 8 (payment latency injection) proved a slow-but-successful dependency produces near-zero error rate — that alert would never have fired even though p99 latency spiked. A latency-based alert (`p99 > 500ms for 2m`) would have caught the Lab 10 breaking point automatically instead of requiring manual Locust runs to discover it.
- No alert exists for connection pool saturation, which is the suspected root cause of the 25u→50u cliff. A `pg_stat_activity` count approaching `max_connections` would be a leading indicator, catching the problem before user-facing errors appear.

## 7. Capacity Plan

- **Current ceiling:** ~19-20 RPS sustained (25 users) before SLO breach; hard failure by 30 RPS (50 users).
- **For 2× traffic (~40 RPS target with headroom):**
  - `events`: scale from 1 → 3 replicas (it showed the highest per-pod CPU and is the likely bottleneck)
  - `gateway`: scale from 5 → 8 replicas (already well-distributed, cheap to add more)
  - `payments`: scale from 1 → 2 replicas (currently underutilized at 12m CPU, but redundancy matters)
  - `postgres`: increase `DB_MAX_CONNS` and add connection pooling (PgBouncer) rather than just adding more Postgres replicas — vertical/pooling fix, not horizontal
  - `redis`: single-pod is fine at this scale; revisit if reservation volume grows 10×+
- **Rough cost estimate** (at $5/pod/mo):
  - events: 3 pods × $5 = $15/mo (was $5/mo)
  - gateway: 8 pods × $5 = $40/mo (was $25/mo)
  - payments: 2 pods × $5 = $10/mo (was $5/mo)
  - postgres + redis: unchanged, ~$10/mo
  - **Total: ~$75/mo** (up from ~$45/mo) for roughly 2× headroom — driven mostly by fixing the events bottleneck and adding gateway replicas.

---

## Load Test Configuration (Task 1 Setup)

`locustfile.py` committed at repo root — reserve tasks split across event 3 (500 tickets) and event 5 (80 tickets) to avoid inventory exhaustion (409s) dominating the results before the system's real capacity limit is reached.

## Per-Pod CPU at Breaking Point (Task 2)

Sampled during the 50-user load test:

```
NAME                       CPU(cores)   MEMORY(bytes)
gateway-58ccf5b8b4-45m4m   25m          46Mi
gateway-58ccf5b8b4-6c7lf   39m          42Mi
gateway-58ccf5b8b4-gxpr9   32m          47Mi
gateway-58ccf5b8b4-kxdr7   26m          42Mi

events-cc76c9645-rjqmh     56m          59Mi

payments-7f5974d6c6-dwspt  12m          37Mi
```

**CPU-constrained service:** `events` — highest per-pod CPU (56m) despite having only 1 replica to absorb all traffic. Gateway pods sit around 25-39m each (well-distributed across 4-5 replicas). Payments is nearly idle at 12m — confirming it's not the bottleneck at this load level, contrary to what the Lab 8 chaos experiments might suggest in isolation.
