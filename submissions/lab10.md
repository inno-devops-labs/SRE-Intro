# Lab 10 — SRE Portfolio & Reliability Review

## Task 1 — Load Testing & Reliability Review

# QuickTicket Reliability Review

## 1. SLO Compliance

| SLO | Target | Observed | Status |
|-----|--------|----------|--------|
| Availability (5xx errors) | < 0.5% | 0% (up to 50 users), 0.64% (at 60 users) | Fails at 60+ users |
| Latency (p99) | < 500ms | 45ms (50 users), 74ms (60 users), 1700ms (100 users) | Fails at 100+ users |

## 2. Load Test Results

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) |
|-------|------|-----|-----|-----|-----|----------------|-----------------|
| 10 | 2/s | 7.71 | 7ms | 11ms | 20ms | 0% | 0 |
| 50 | 5/s | 36.63 | 7ms | 15ms | 45ms | 0% | 15 (0.69%) |
| 60 | 6/s | 44.37 | 7ms | 29ms | 74ms | 0.64% | 37 |
| 100 | 10/s | 54.97 | 360ms | 1200ms | 1700ms | 40.45% | 12 |

**Breaking point:** ~55-60 users (5xx errors exceed 0.5% at 60 users)

## 3. DORA Metrics

```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab10)
$ kubectl get rs -l app=gateway -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | wc -l
6

axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab10)
$ kubectl get analysisrun -o jsonpath='{.items[*].status.phase}' | tr ' ' '\n' | sort | uniq -c
      1 Failed
      1 Successful

axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab10)
$ kubectl argo rollouts get rollout gateway | grep -i degraded

axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab10)
$ git log --oneline main | wc -l
73
```

| Metric | Value | Source |
|--------|-------|--------|
| **Deployment Frequency** | 6 | `kubectl get rs -l app=gateway` |
| **Lead Time for Changes** | ~3 minutes | CI build + ArgoCD poll interval |
| **Change Failure Rate** | 50% | 1 Failed / 2 AnalysisRuns |
| **Recovery Time** | ~2-3 minutes | git revert → ArgoCD sync |



## 4. Top 3 Reliability Risks

1. **Database connection pool exhaustion** — Under load, `DB_MAX_CONNS=3` becomes a bottleneck, causing cascading failures across all services. **Fix:** Increase pool size to 10-20 and add connection pooling monitoring.

2. **No persistent storage for PostgreSQL** — Without PVC, all data is lost on pod restart. **Fix:** Add PVC (Lab 9 Bonus) and implement automated backups.

3. **Gateway timeout handling** — Slow payments cause premature timeouts and cascading failures. **Fix:** Increase timeout to 10000ms and add circuit breaker.

## 5. Toil Identification

| Toil Task | How often | How to automate | Time saved |
|-----------|-----------|-----------------|------------|
| Re-seeding Postgres after pod restart | Every Postgres restart | Add PVC (Lab 9 Bonus) | 2-3 min per incident |
| Manually watching canary rollouts | Every rollout | Use AnalysisTemplate (Lab 7) | 5-10 min per rollout |
| Copying files into pods (`kubectl cp`) | Every backup/restore | Use persistent volumes | 2-3 min per operation |

## 6. Monitoring Gaps

- **Latency monitoring for payment endpoint** — During Lab 8 chaos experiments, I wished I had a separate latency alert for `/pay` to detect slow-but-successful payments.
- **Database connection pool monitoring** — An alert on `events_db_pool_size` approaching its limit would have caught the DB exhaustion earlier.
- **Alert on Redis failure** — The health check didn't show Redis as down, so I would add a specific alert for Redis unavailability.

## 7. Capacity Plan

**Current ceiling:** ~44 RPS (at 60 users, before 5xx errors spike)

**For 2× traffic (~90 RPS):**

| Service | Current replicas | Required replicas | CPU requests | Memory requests |
|---------|------------------|-------------------|--------------|-----------------|
| gateway | 5 | 10 | 50m → 100m | 64Mi → 128Mi |
| events | 1 | 2 | 50m → 100m | 64Mi → 128Mi |
| payments | 1 | 2 | 50m → 100m | 64Mi → 128Mi |
| postgres | 1 | 1 (scale vertically) | 100m → 200m | 256Mi → 512Mi |
| redis | 1 | 1 | unchanged | unchanged |

**Rough cost estimate:** $5/pod/mo → 10+2+2 = 14 pods × $5 = **$70/month** (plus $20 for postgres upgrade = **~$90/month**)


## Task 2 — Capacity Plan with Numbers

### Per-pod CPU at breaking point (60 users)

| Service | Pod | CPU (cores) | Memory (MiB) |
|---------|-----|-------------|--------------|
| gateway | gateway-7c5778d94f-97j6h | 4m | 48Mi |
| gateway | gateway-7c5778d94f-bf2g5 | 4m | 43Mi |
| gateway | gateway-7c5778d94f-rwcxq | 4m | 40Mi |
| gateway | gateway-7c5778d94f-xjz5z | 4m | 42Mi |
| events | events-687fb9fb66-5qssc | 5m | 54Mi |
| payments | payments-7db8bc64fd-nqvrg | 4m | 35Mi |

**CPU-constrained service:** `events` (5m) — slightly higher than others

**Idle service:** `payments` (4m) — lowest CPU usage

### Detailed capacity plan for 2× traffic

| Service | Current replicas | New replicas | CPU request | Memory request | Reason |
|---------|------------------|--------------|-------------|----------------|--------|
| gateway | 5 | 10 | 50m → 100m | 64Mi → 128Mi | Doubling traffic requires doubling gateway capacity |
| events | 1 | 2 | 50m → 100m | 64Mi → 128Mi | Events is CPU-constrained under load |
| payments | 1 | 2 | 50m → 100m | 64Mi → 128Mi | Need redundancy for payment processing |
| postgres | 1 | 1 (scale vertically) | 100m → 200m | 256Mi → 512Mi | Single DB, scale up not out |
| redis | 1 | 1 | unchanged | unchanged | Redis is not a bottleneck |

### Cost estimate

| Service | Pods | Cost per pod | Monthly cost |
|---------|------|--------------|--------------|
| gateway | 10 | $5 | $50 |
| events | 2 | $5 | $10 |
| payments | 2 | $5 | $10 |
| postgres | 1 | $20 (vertical) | $20 |
| redis | 1 | $5 | $5 |
| **Total** | | | **$95/month** |
