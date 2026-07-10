# QuickTicket Reliability Review

Capstone document for Lab 10 — load testing, DORA metrics, toil, and capacity planning.

---

## 1. SLO Compliance

| SLO | Target | Observed (load test) | Status |
|-----|--------|----------------------|--------|
| Availability | 99.5% (7-day) | 100% at 10u; **95.3%** at 50u; 55.7% at 100u | **FAIL** above ~30 RPS |
| Latency (p95 < 500ms) | 95% under 500ms | p95 = 22ms at 10u; **1300ms** at 50u | **FAIL** above ~30 RPS |
| Error budget burn | < 6× (30m window) | No burn at 10u; rapid burn at 50u+ | **FAIL** under stress |

At **10 concurrent users** (~7.6 RPS) both SLOs pass comfortably. The system breaches availability and latency SLOs somewhere between 10 and 50 users.

---

## 2. Load Test Results

Locust ran **in-cluster** via Kubernetes Jobs (`http://gateway:8080` through kube-proxy). Redis flushed between runs.

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) |
|------:|-----:|----:|----:|----:|----:|---------------:|----------------:|
| 10 | 2/s | **7.6** | 13ms | 22ms | 37ms | **0.00%** | ~0 |
| 50 | 5/s | **27.1** | 380ms | 1300ms | 1800ms | **4.68%** | low |
| 100 | 10/s | **31.0** | 1200ms | 11000ms | 13000ms | **44.26%** | low |
| 200 | 20/s | **35.3** | 3300ms | 8000ms | 23000ms | **46.62%** | low |

**Breaking point:** **50 users / ~27 RPS** — first level where **5xx > 0.5%** (4.68%) AND **p99 > 500ms** (1800ms). Errors were predominantly 502/500 from the events service (connection pool / CPU saturation), not 409 inventory conflicts.

At 10u, Locust summary:

```
Aggregated   455 reqs   0(0.00%) fails   7.60 req/s   p50=13ms p95=22ms p99=37ms
```

At 50u (breaking point):

```
Aggregated   1624 reqs   76(4.68%) fails   27.13 req/s   p50=380ms p95=1300ms p99=1800ms
Errors: 502 Bad Gateway (/events), 500 Internal Server Error (/reserve), 503 (/health)
```

---

## 3. DORA Metrics

| Metric | Value | Source |
|--------|-------|--------|
| **Deployment Frequency** | ~57 commits to `main` over the course | `git log --oneline main \| wc -l` |
| **Lead Time for Changes** | ~5–8 min (CI build ~2 min + ArgoCD poll ~3 min) | Lab 5 GitOps flow; image tag CI workflow |
| **Change Failure Rate** | ~15–20% (3 abort/revert cycles / ~15 rollouts) | Lab 7 canary abort + git revert experiments |
| **Mean Time to Recovery** | **2–3s** (rollout abort) vs **2–5 min** (git revert) | Lab 7 §7.7 timing comparison |

**Recovery evidence (Lab 7):**

```
$ kubectl argo rollouts abort gateway
→ stable traffic restored in ~2–3 seconds

git revert → push → ArgoCD sync → full rollout: 2–5 minutes
```

---

## 4. Top 3 Reliability Risks

1. **Events service is the bottleneck under load** — single replica, 200m CPU limit. At 50u Locust, events returned 502/500 while payments stayed idle (6m CPU). **Fix:** scale events to 3 replicas, raise CPU limits to 500m, tune `DB_MAX_CONNS`.

2. **No persistent Postgres storage (pre-Lab 9)** — pod restart = total data loss, requiring manual `pg_restore`. **Fix:** PVC on postgres (done in Lab 9 Bonus) + automated CronJob backups.

3. **Latency-only failures invisible to error-rate alerts** — Lab 8 showed payments at 2000ms latency with 0% 5xx; SLO breach hidden. **Fix:** add p99 latency alert on `gateway_request_duration_seconds` per path.

---

## 5. Toil Identification

| Toil | Frequency | Automation | Time saved |
|------|-----------|------------|------------|
| Re-seed Postgres after every pod restart (pre-PVC) | ~8× across Labs 4–9 | PVC + init Job that runs `seed.sql` on first boot | ~5 min/incident |
| Re-create `kubectl port-forward` after pod restarts | ~10× (Alembic, Prometheus, Grafana) | `k9s` port-forward profiles or Telepresence | ~2 min/session |
| Manually watch canary rollouts (`kubectl argo rollouts get rollout --watch`) | ~6× in Lab 7 | AnalysisTemplate auto-promote/abort (already configured) | ~10 min/deploy |

---

## 6. Monitoring Gaps

**During Lab 8 chaos experiments, I wished I had:**

- **Per-dependency latency histogram** — error rate caught payment 500s but not 2000ms slow-success path
- **Postgres connection pool utilization** — `DB_MAX_CONNS=3` queueing was invisible until p99 climbed on `/reserve`
- **Redis health in gateway `/health`** — 5s cache lag delayed degraded status during Redis failure

**Alert that would have caught the actual breaker:**

```promql
histogram_quantile(0.99, sum by (le, path) (rate(gateway_request_duration_seconds_bucket{path="/events/{id}/reserve"}[5m]))) > 0.5
```

This fires on latency SLO breach before error rate spikes — exactly what happened in Lab 8 Task 2 combined scenario.

---

## 7. Capacity Plan

**Current ceiling:** ~**27 RPS** (50 Locust users) before 5xx > 0.5%.

**For 2× traffic (~54 RPS):**

| Component | Current | 2× Plan | Est. cost/mo |
|-----------|---------|---------|-------------|
| gateway | 5 replicas, 50m CPU req | 5 replicas (sufficient — not the bottleneck) | $25 |
| events | 1 replica, 50m CPU req | **3 replicas**, 200m CPU req, 512Mi mem | $15 |
| payments | 1 replica, 50m CPU req | 2 replicas, 100m CPU req | $10 |
| postgres | 1 pod, no pooler | 1 pod + PVC, `DB_MAX_CONNS=50` | $5 |
| redis | 1 pod | 1 pod (OK for hold TTL workload) | $5 |
| **Total** | | | **~$60/mo** |

---

## Task 2 — Capacity Plan with Numbers (4 pts)

### 10.7 — Per-pod CPU at breaking point (50 users)

Sampled during/after load-50 saturation:

```
NAME                       CPU(cores)   MEMORY(bytes)
events-74fdb75c8-4sg2m     62m          66Mi
payments-5cfb45886-d9pbq   6m           35Mi
gateway (5 pods)           saturated → CrashLoopBackOff after sustained 100u+
```

**CPU-constrained service:** **events** (62m of 200m limit, generating 502/500 under pool pressure). **Idle:** payments (6m). Gateway distributes load fine — the downstream events pool is the weak link.

### 10.8 — Detailed 2× capacity plan

| Service | Replicas | CPU request/limit | Memory request/limit | Notes |
|---------|----------|-------------------|---------------------|-------|
| gateway | 5 | 50m / 200m | 64Mi / 256Mi | Already load-balanced; no change needed |
| events | **3** | **100m / 500m** | **128Mi / 512Mi** | Primary scale target |
| payments | 2 | 50m / 200m | 64Mi / 256Mi | Headroom for 30% failure injection |
| postgres | 1 | default | 1Gi PVC | `DB_MAX_CONNS=50`; consider PgBouncer at 4× |
| redis | 1 | 50m / 200m | 64Mi / 128Mi | Single-pod OK; holds are short-TTL |

**Redis:** single-pod sufficient — holds expire in minutes; no persistence needed for holds.

**DB connections:** single Postgres + per-pod pool is fine at 2× with 3 events replicas × 20 conns = 60 total (within Postgres default max_connections=100). Add PgBouncer if scaling beyond 5 events replicas.

**Rough cost:** 5 gateway + 3 events + 2 payments + 1 postgres + 1 redis = **12 pod-months × $5 ≈ $60/mo** on a small cloud.
