# QuickTicket Reliability Review

## 1. SLO Compliance

Availability/latency SLOs come from `monitoring/prometheus/rules.yml` (Lab 6);
the migration-safety and DB-recovery rows are from the Lab 9 data/durability work.

| SLO | Target | Observed | Status |
|-----|--------|----------|--------|
| Availability (non-5xx) | ≥ 99.5% (≤ 0.5% 5xx) | 100% at ≤10 users (~7 RPS); **0.82% 5xx at 20 users** | ✅ within capacity / ❌ above ~14 RPS |
| Latency (share < 500ms) | ≥ 95% of requests < 500ms | p95 23ms at 10u; **p95 470ms / p99 1100ms at 20u** | ✅ at ≤10u / ❌ above ~14 RPS |
| Migration safety | 0 downtime | `email` migration = additive **nullable** column on `events` (metadata-only, no rewrite/lock) — zero-downtime by construction | ✅ |
| DB recovery (RTO) | < 5 min | **~20s** measured: deleted the Postgres pod, new pod Ready and serving with **5/5 rows intact** via the `postgres-data` PVC | ✅ |

## 2. Load Test Results

Each level: 60s run, `--only-summary`, Redis flushed before healthy-range runs.
409 = inventory exhausted (product behavior); 5xx = system failure (the SLO metric).

| Users | Ramp | RPS  | p50   | p95    | p99     |     5xx error rate | 409 (inventory) |
|------:|-----:|-----:|------:|-------:|--------:|-------------------:|----------------:|
| 10    | 2/s  | 7.72  | 15ms   | 23ms   | 78ms    |  0.00% | 0 |
| 20    | 3/s  | 14.34 | 20ms   | 470ms  | 1100ms  |  0.82% | 0 |
| 50    | 5/s  | 21.97 | 630ms  | 2000ms | 7200ms  | 48.48% | 0 |
| 100   | 10/s | 26.25 | 2000ms | 5800ms | 15000ms | 70.09% | 0 |

**Breaking point:** first SLO breach at **20 users / ~14 RPS** (5xx 0.82% > 0.5%
**and** p99 1100ms > 500ms). Throughput tops out around **~26 RPS** (at 100u,
fully saturated) — past the ~14 RPS breach point every extra user mostly adds
queueing and 5xx, not useful work.

## 3. DORA Metrics

| Metric | Value                                                                                                                                   | Source data | DORA tier |
|--------|-----------------------------------------------------------------------------------------------------------------------------------------|-------------|-----------|
| Deployment Frequency | 10 gateway rollout revisions over the course (~1/week, bursting during Labs 7–10); 5 GitOps image-tag auto-deploys                      | `kubectl get rs -l app=gateway` = 10; 5 `"ci: update image tags"` commits; 54 commits on `main` | Medium |
| Lead Time for Changes | ~10–15 min (CI builds+pushes 3 images ≈ 5 min + 3-min ArgoCD poll + ~5-min canary AnalysisRun)                                          | `ci.txt` (3 sequential image builds); ArgoCD 3-min poll | Elite (< 1 day) |
| Change Failure Rate | 2/5 canary analyses **Failed** = 40% at the canary gate, but **0% reached stable** — the AnalysisTemplate auto-aborted every bad canary | `kubectl get analysisrun`: 2 Failed / 3 Successful | Elite user-facing (the gate is why) |
| MTTR | seconds (Argo Rollouts auto-abort of a failed canary → last stable stays serving) to ~3–6 min (`git revert` → ArgoCD sync)              | Argo Rollouts abort behavior; 3-min poll | Elite (< 1 hr) |

## 4. Top 3 Reliability Risks

1. **Dependency-checking liveness probe with a default 1s timeout** — under load
   `/health` exceeds 1s, the liveness probe fails, and kubelet restarts *all*
   gateway pods, converting load into an outage. *Fix:* a lightweight `/livez`
   (process-alive only, no downstream calls) for liveness, keep `/health` for
   readiness, and set `timeoutSeconds: 3–5`. This is the single highest-leverage fix.
2. **CPU limits too tight + no autoscaling** — gateway/events limits are 200m and
   there is no HPA, so hot pods throttle under load (events peaked **181m against
   its 200m limit, ~90%**), which slows `/health` and feeds the probe collapse
   above. *Fix:* raise limits (gateway 500m, events 1000m) and add an HPA on CPU
   so capacity grows with load instead of throttling.
3. **SPOFs in the data/read tier** — events runs a **single replica** (the busiest
   pod, ~180m under load, and the DB workhorse); Postgres and Redis are single
   pods. *Fix:* events ≥ 2 replicas + a `PodDisruptionBudget`, and a Postgres
   replica for the DB tier.

## 5. Toil Identification

| Toil (Labs 1–9) | Frequency | How to automate | What it saves |
|-----------------|-----------|-----------------|---------------|
| Querying Prometheus via `kubectl exec -n monitoring deploy/prometheus -- wget -qO- '…'` for every golden-signal sample (docker-compose Grafana can't scrape k3d pod IPs) | dozens of times across Lab 8 | Wire the in-cluster Prometheus as a Grafana datasource (or run Grafana in-cluster) so dashboard panels replace ad-hoc queries | ~1–2 min per query × dozens |
| Re-creating `kubectl port-forward svc/gateway 3080:8080` after every pod restart / new shell | many times, Labs 3–8 | Stable Ingress or NodePort + a `make forward` / Tiltfile target | ~30s each, several times/day |
| Manually watching `kubectl argo rollouts get rollout gateway --watch` through each canary step | every deploy, Lab 7+ | Trust the AnalysisTemplate gate + Argo Rollouts notifications (Slack/email on Promote/Abort) | ~5–8 min babysitting per deploy |

## 6. Monitoring Gaps

- **Latency SLO alert missing.** Lab 6/8 alerted on 5xx error rate only. The Lab 8
  payment-latency experiment (all 200s, p99 ~2.5s) would have paged no one — a
  "slow but successful" degradation. A p99 > 500ms burn alert closes this gap.
- **No pod-restart / liveness-failure alert.** The exact thing that broke under
  load here (all 5 gateway pods restarting) produces no page today. An alert on
  `rate(kube_pod_container_status_restarts_total[5m])` would have caught it.
- **No saturation alert.** DB-pool-near-max and per-pod-CPU-vs-request are
  unmonitored (the golden-signals dashboard left Saturation as a TODO panel).
- **Which alert would have caught what actually broke:** in Lab 8 the Redis
  outage (reserve → 5xx) took ~8 min to page under the single 5%/5m slow alert. A
  **multi-window fast-burn** availability alert (~2% over 1m) would have paged in
  1–2 min.

## 7. Capacity Plan

- **Current ceiling:** ~14 RPS (5xx SLO first breached at 20 users / ~14 RPS);
  throughput tops out around ~26 RPS at 100u (fully broken).
- **Per-pod CPU at breaking point (50-user run, sampled live):**

  | Service | CPU under load | Limit | Replicas | Note |
  |---------|---------------:|------:|---------:|------|
  | gateway | 120–148m each  | 200m  | 5        | ~74% of limit — nearing throttle |
  | events  | 150–**181m**   | 200m  | 1        | **~90% of limit** — throttle-bound, single replica, DB workhorse |
  | payments| 13–19m         | 200m  | 1        | idle (no `/pay` in the mix) |
  | redis   | 11–12m         | none  | 1        | idle |
  | postgres| 74–122m        | 200m  | 1        | moderate |

  The **node** has ample CPU, but the per-pod **200m limits** throttle the hot
  pods (events at ~90% of its limit). So this is not a raw-CPU/node ceiling — it's
  per-pod throttling: throttle-slowed `/health` trips the 1s liveness probe and
  collapses the gateway. The fix is **higher limits + an HPA**, not more nodes.

- **For 2× traffic (~30 RPS):**
  - **Fix the liveness probe first** — it unlocks most of the headroom; adding
    replicas without it just gives more pods to crash.
  - events: 1 → **3 replicas** (busiest single pod, DB workhorse).
  - gateway: keep **5** (large per-pod headroom once probe is fixed); optionally
    an HPA 3–8 on CPU.
  - payments: **1** (idle); redis: **single pod OK** (12m idle — no replication
    needed for 2×).
  - postgres: single pod ~120m at 1× → ~240m at 2× (< ½ core); the
    single-pooler → single-Postgres path is **not** the 2× bottleneck, but raise
    the pooler max-conns to match events×3. Add a replica only for HA / > 2×.
  - Requests/limits: raise from today's 50m/200m to gateway 100m/500m, events
    250m/1000m, payments 50m/200m, redis 50m/200m, postgres 250m/1000m.
  - **Cost:** 9 pods today → ~11 pods (events +2) at $5/pod/mo ≈ **$55/mo** (from
    ~$45/mo). The probe fix is $0 and buys more than the extra $10.
