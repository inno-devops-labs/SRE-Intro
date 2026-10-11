# QuickTicket Reliability Review

Locust 2.43.4 ran as an in-cluster Job against `http://gateway:8080` on 2026-10-11; Redis was flushed before each run.

## 1. SLO Compliance

| SLO | Target | Observed | Status |
|---|---|---|---|
| Availability, normal load (2590 req, incl. a live migration) | 99.5% | 100% | ✅ |
| Latency < 500 ms, normal load | 95% | 100% | ✅ |
| Availability, whole test day (36.6k req, incl. overload and DB kills) | 99.5% | 91.7% | ❌ |
| Latency < 500 ms, whole test day | 95% | 93.9% | ❌ |

The test day burned 3036 errors against a budget of 183, almost all from load above the ceiling.

## 2. Load Test Results

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) |
|------:|-----:|----:|----:|----:|----:|---------------:|----------------:|
| 10    | 2/s  | 7.6  | 12 ms | 19 ms | 26 ms | 0% | 0 |
| 50    | 5/s  | 37.4 | 7 ms | 24 ms | 67 ms | 0% | 25 (1.1%) |
| 75    | 8/s  | 55.5 | 6 ms | 150 ms | 260 ms | 0.42% | 98 (3.0%) |
| **100** | 10/s | 60.9 | 270 ms | 610 ms | 800 ms | **37.0%** | 20 (0.5%) |
| 200   | 20/s | 78.5 | 85 ms | 5000 ms | 5000 ms | 43.8% | 33 (0.7%) |

**Breaking point: between 75 and 100 users, ceiling ≈ 55 RPS.**

Cause: the single `events` pod. Its log had `psycopg2.pool.PoolError: connection pool exhausted` 873 times, its CPU sat at the 200m limit, and at 200 users it was restarted by its liveness probe.

Same tests with `events` scaled to 3 replicas:

| Users | RPS | p50 | p95 | p99 | 5xx | 409 |
|------:|----:|----:|----:|----:|----:|----:|
| 100 | 75.1 | 6 ms | 11 ms | 24 ms | 0% | 285 (6.3%) |
| 200 | 149.2 | 5 ms | 15 ms | 61 ms | 0% | 1187 (13.3%) |

## 3. DORA Metrics

| Metric | Value | Source |
|---|---|---|
| Deployment frequency | 21 deploys in 16 days | 9 ArgoCD syncs + 12 gateway Rollout revisions |
| Lead time | ~4.7 min | Lab 5: push 12:09:47 → Healthy 12:14:28 |
| Change failure rate | 29% (6 of 21) incl. deliberate failures; ~5% (1 of 21) unplanned | 2 bad tags (Lab 5), 4 aborted canaries (Lab 7); AnalysisRuns: 2 Successful, 1 Failed |
| Time to restore | 0.3 s abort, 107 s analysis abort, 304 s `git revert` | Labs 5 and 7 |

## 4. Top 3 Reliability Risks

1. **`events` is a single replica that fails hard** — caps the system at ~55 RPS, and its restart is a full outage. Fix: 3 replicas, higher CPU limit, pool wait with a timeout.
2. **Postgres is a single instance and backups sit on the same node** — losing the node loses both. Fix: off-cluster dumps, WAL archiving, scheduled restore drills.
3. **Checkout has no protection from a degraded payments service** — one replica, retries and circuit breaker are stubs; a 2 s delay cut throughput by 85% with no alert (Lab 8). Fix: 2+ replicas, retry with backoff, circuit breaker, `/pay` latency alert.

## 5. Toil Identification

| Toil | How often | How to automate | Saved |
|---|---|---|---|
| Re-seeding Postgres after pod or cluster restarts | 4+ times | PVC (done in Lab 9); schema and seed as a migration Job | ~5 min each |
| Watching deploys and waiting on ArgoCD polls | every deploy, 20+ times | GitHub webhook → ArgoCD; rely on AnalysisTemplate and notify on abort | 3–5 min per deploy |
| Rebuilding port-forwards, switching kube namespace, restarting Docker/k3d after sleep | 10+ times | Makefile for port-forwards and context | ~2–3 min each |

## 6. Monitoring Gaps

Wished for during Lab 8:
- Latency alerts; only error-rate alerts exist.
- Metrics from events, payments, Postgres and Redis; Prometheus scrapes only gateway.
- A client-side probe; gateway counters miss dropped connections.

Also missing: alert rules inside the cluster (Lab 6 rules live in compose Grafana), persistent Prometheus storage, a backup-freshness check.

Alerts that would have caught what actually broke:
- events DB pool usage > 80%
- container restarts > 0 in 5 min
- `/pay` p99 > 1 s
- last successful backup older than 15 min

## 7. Capacity Plan

- Current ceiling: ~55 RPS.
- For 2× traffic (110 RPS): `events` ×3, measured at 149 RPS with 0% 5xx.
- Cost: 9 → 12 pods, $45 → $60 per month.

## 8. Task 2 — Capacity Plan with Numbers

**`kubectl top pods` at the breaking point (100 users):**
```
events-7cd9fdc86c-4nv6z    200m    52Mi
gateway-6dcd8798dc-mqljx   59m     41Mi
gateway-6dcd8798dc-tvdqd   29m     40Mi
gateway-6dcd8798dc-vgxwr   42m     41Mi
gateway-6dcd8798dc-xbpss   32m     41Mi
gateway-6dcd8798dc-z957k   49m     42Mi
payments-6bb5cdc9c-4xwpt   9m      36Mi
postgres-dbb54497f-x5nvz   149m    29Mi
redis-c46d5dffc-kvdz2      6m      11Mi
```
`events` is CPU-constrained (at its limit); gateway, payments and Redis are idle.

**Plan for 2× (110 RPS):**

| Service | Now | Plan | Requests / limits | Reason |
|---|---|---|---|---|
| events | 1 | 3 | 100m / 500m | bottleneck; ×3 measured at 149 RPS |
| gateway | 5 | 5 | 50m / 200m | ≤ 76m per pod at 149 RPS; 5 kept for 20% canary steps |
| payments | 1 | 2 | 50m / 200m | idle; second replica for availability |
| postgres | 1 | 1 | 250m / 1 CPU | 215m at 149 RPS, no requests set today |
| redis | 1 | 1 | unchanged | 6m CPU |

- **Redis:** single pod is fine; it is a single point of failure for `/reserve` only.
- **DB connections:** not a bottleneck: 3 × 10 = 30 of 100 `max_connections`. A pooler is needed at ~9 events pods.
- **Cost:** 12 pods × $5 = **$60/month** (+33% for 2.7× capacity).

## Bonus — SRE Handbook

[`submissions/runbooks/quickticket-handbook.md`](runbooks/quickticket-handbook.md)
