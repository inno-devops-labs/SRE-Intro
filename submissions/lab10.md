# QuickTicket Reliability Review

## 1. SLO Compliance

| SLO | Target | Observed | Status |
|-----|--------|----------|--------|
| Availability (99.5% over 7d) | 99.5% | 100% (at 10u, 20u) | ✅ Pass |
| Latency (95% under 500ms) | 95% | 100% (at 10u, 20u) | ✅ Pass |

**Note:** SLOs are met at normal load levels (10-20 users). At 25 users, 5xx error rate reaches 0.67% (exceeding 0.5% threshold), indicating the breaking point.

## 2. Load Test Results

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) |
|------:|-----:|----:|----:|----:|----:|---------------:|----------------:|
| 10    | 2/s  | 7.56 | 13ms | 130ms | 260ms | 0.00% | 0.00% |
| 20    | 4/s  | 15.12 | 17ms | 300ms | 920ms | 0.00% | 0.00% |
| 25    | 5/s  | 17.57 | 42ms | 500ms | 1500ms | 0.67% | 0.00% |
| 30    | 5/s  | 21.30 | 31ms | 550ms | 980ms | 2.43% | 0.00% |
| 50    | 5/s  | 21.89 | 730ms | 2100ms | 3100ms | 25.02% | 0.00% |
| 100   | 10/s | 20.20 | 1200ms | 20000ms | 23000ms | 53.68% | 0.00% |

**Breaking Point:** 25 users (17.57 RPS) - 5xx error rate exceeds 0.5% threshold. At 30 users, both 5xx error rate (2.43%) and p99 latency (980ms) exceed thresholds.

## 3. DORA Metrics

| Metric | Value | Source Data |
|--------|-------|-------------|
| Deployment Frequency | 5 deployments | Git log: 5 deploy-related commits |
| Lead Time | ~3 minutes | CI build time + ArgoCD 3-min poll interval |
| Change Failure Rate | 20% (1 rollback / 5 deployments) | Git log: 1 revert commit out of 5 deploys |
| Recovery Time | ~40 seconds | Git revert → ArgoCD sync time (from Lab 5) |

**DORA Performance:** Medium performer (not elite, but reasonable for solo student project)

## 4. Top 3 Reliability Risks

1. **Single-point gateway bottleneck** - The gateway service is the single entry point for all traffic. With only 1 replica in current deployment, any gateway pod failure or high load directly impacts all users. **Fix:** Implement horizontal pod autoscaler (HPA) for gateway with minimum 3 replicas.

2. **Events service hard dependency on Redis** - Chaos experiments (Lab 8) revealed that events service fails completely when Redis is unavailable, even for read operations (/events returns 502). This creates a single point of failure. **Fix:** Implement cache-aside pattern with fallback to direct database queries for read operations when Redis is unavailable.

3. **Database connection pool exhaustion under load** - The events service has a fixed DB pool size (10 connections). Under high load (25+ users), this becomes a bottleneck as seen in load tests. **Fix:** Implement connection pooling with PgBouncer and configure appropriate pool sizes based on load testing.

## 5. Toil Identification

| Toil Item | How Often | How to Automate | What You'd Save |
|-----------|-----------|-----------------|-----------------|
| Manual Redis FLUSHDB between load tests | Every load test run (6+ times in Lab 10) | Add pre-hook to Locust Job that executes FLUSHDB before starting | ~2 minutes per run × 6+ runs = 12+ minutes saved |
| Manual port-forward recreation after pod restarts | Every pod restart in Labs 1-4 (10+ times) | Use kubectl port-forward with --address 0.0.0.0 in background with auto-restart script | ~30 seconds per restart × 10+ = 5+ minutes saved |
| Manual image tag updates in k8s manifests | Every deployment before Lab 5 bonus (5+ times) | Already automated in Lab 5 bonus with GitHub Actions | ~5 minutes per deploy × 5 = 25 minutes saved |

## 6. Monitoring Gaps

**What I wished I had during Lab 8 chaos experiments:**
- **Latency SLO alert** - I only had error rate alerts. A slow-but-successful dependency (like the 1000ms latency injection in Lab 6) wouldn't page anyone until it caused cascading failures. Added in Lab 8 bonus task.
- **Database connection pool saturation alert** - During load testing, the DB pool became a bottleneck, but there was no alert on `events_db_pool_size` approaching limits.
- **Redis connectivity alert** - Chaos experiments showed that Redis failure causes complete events service degradation, but there was no explicit Redis health check alert.
- **Redis memory usage alert** - No monitoring on Redis memory, which could lead to OOM under high reservation load.

**Alert that would have caught the actual break:**
- An alert on `events_db_pool_size > 8` (80% of max) would have provided early warning during the 25-user load test, allowing proactive scaling before the system degraded.

## 7. Capacity Plan

**Current ceiling:** 17.57 RPS (25 users) - 5xx error rate exceeds 0.5%

**For 2x traffic (35 RPS):**
- Gateway: Scale from 1 to 3 replicas (each handles ~12 RPS at current capacity)
- Events: Scale from 1 to 2 replicas (DB pool is the bottleneck)
- Payments: Keep at 1 replica (low traffic, not CPU-bound)
- Redis: Single pod still OK (reservation holds are short-lived)
- Postgres: Add PgBouncer connection pooler to handle increased connections

**Rough cost estimate:** $5/pod/month
- Gateway: 3 pods × $5 = $15/month
- Events: 2 pods × $5 = $10/month
- Payments: 1 pod × $5 = $5/month
- Redis: 1 pod × $5 = $5/month
- Postgres: 1 pod × $5 = $5/month
- PgBouncer: 1 pod × $5 = $5/month
- **Total:** ~$45/month for 2x capacity

---

## Task 2 — Capacity Plan with Numbers

### Per-Pod CPU at Breaking Point (25 users)

**kubectl top pods output at breaking point:**
```
NAME                                CPU(cores)   MEMORY(bytes)
gateway-7b45dc47d7-wd5wc           86m          63Mi
events-75d4f54dc8-km4kl            62m          74Mi
payments-6695cbb8fd-x2766         7m           49Mi
postgres-dbb54497f-zblzp           <measured separately>
redis-c46d5dffc-jhwct              <measured separately>
```

**Analysis:** Gateway is the most CPU-constrained (86m), followed by events (62m). Payments is nearly idle (7m), indicating it's not the bottleneck. The system is not CPU-bound at the breaking point - the failures are likely due to connection limits and request queuing rather than CPU exhaustion.

### Detailed 2× Capacity Plan

| Service | Current Replicas | 2× Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit | Cost/Month |
|---------|------------------|-------------|-------------|-----------|----------------|--------------|------------|
| Gateway | 1 | 3 | 100m | 500m | 64Mi | 128Mi | $15 |
| Events | 1 | 2 | 150m | 500m | 64Mi | 128Mi | $10 |
| Payments | 1 | 1 | 50m | 200m | 32Mi | 64Mi | $5 |
| Redis | 1 | 1 | 50m | 200m | 16Mi | 32Mi | $5 |
| Postgres | 1 | 1 | 100m | 300m | 32Mi | 64Mi | $5 |
| PgBouncer | 0 | 1 | 50m | 200m | 16Mi | 32Mi | $5 |
| **Total** | **5** | **9** | **500m** | **1900m** | **224Mi** | **448Mi** | **$45** |

**Redis:** Single pod still acceptable for 2x traffic. Reservations are short-lived (5-minute TTL), so memory pressure is manageable. For 10x+ traffic, would need Redis Cluster.

**DB connections:** The single-pooler-to-single-Postgres path becomes a bottleneck at 2x. Adding PgBouncer with transaction pooling mode allows ~100 concurrent connections vs. current 10, eliminating this bottleneck.

**Cost breakdown:** $5/pod/month × 9 pods = $45/month for production-capable setup handling 2x current traffic.
