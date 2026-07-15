# QuickTicket Reliability Review

## 1. SLO Compliance

| SLO | Target | Observed | Status |
|-----|--------|----------|--------|
|Availability (5xx error rate)	|< 0.5%	|0% at 10-50 users; 37.36% at 100 users	|OK Up to 50 users / NOT OK Beyond 100 users
|Latency (p99, read path)	|< 500 ms	|17-130 ms at 50 users; 770 ms at 100 users	|OK Up to 50 users / NOT OK Beyond 100 users
|Error budget burn rate	|< 6× over 30m	|Not breached under normal load; breached at breaking point	|OK Under normal load

The system meets its SLOs at low-to-moderate load (10-50 users, ~7-39 RPS). At 100 users (~62 RPS), both availability and latency SLOs are violated, with the system entering a cascading failure state.

## 2. Load Test Results

| Users | Ramp |   RPS |    p50 |    p95 |    p99 | 5xx error rate | 409 (inventory) |
|------:|-----:|------:|-------:|-------:|-------:|---------------:|----------------:|
| 10 | 2/s |  8.12 |  12 ms |  22 ms |  30 ms |          0.00% |               0 |
| 50 | 10/s | 37.07 |   7 ms |  31 ms | 215 ms |          2.63% |              58 |
| 75 | 15/s | 55.34 |   6 ms | 546 ms |  1.5 s |         10.53% |              54 |
| 100 | 20/s | 69.24 | 135 ms | 532 ms | 976 ms |         25.96% |              48 |

Breaking point: Between 50 and 75 users (~37-55 RPS). At 75 users, the p99 latency exceeds 500 ms (1.5s) and the 5xx error rate reaches 10.53%, violating both SLOs simultaneously. The error chain is: 100 users → events CPU-saturates at 194m → Postgres connections exhaust → events returns 500 → gateway returns 502 → payments circuit breaker opens → gateway returns 503 even on health checks.

## 3. DORA Metrics

| Metric | Value | Source / How calculated                                                                    |
|--------|-------|--------------------------------------------------------------------------------------------|
| Deployment Frequency |~1 deployment/day| 8 ReplicaSets over 7.8 days of cluster lifetime (kubectl get rs -l app=gateway)            |
| Lead Time |~8-13| minutes	CI build (~5-10 min) + ArgoCD poll interval (~3 min) from git push to 100% traffic |
| Change Failure Rate |33%| 2 failed AnalysisRuns out of 6 total rollouts (kubectl get analysisrun)                    |
| Recovery Time |~13 seconds| Lab 6 payment failure recovery (~13s), Lab 9 DR without PVC (~19s), DR with PVC (~8s)      |

Against DORA 2023 elite benchmarks (deploy on-demand, lead time < 1 day, CFR 0-15%, restore < 1 hour), the project lands in the elite range for lead time and recovery time, while deployment frequency and change failure rate are impacted by the solo-student cadence.

## 4. Top 3 Reliability Risks

1. **Single-replica events CPU bottleneck** — *Why it matters:* At 75+ users, events saturates at 194m CPU (39% of 500m limit), causing cascading 502/503 failures across all endpoints and the gateway circuit breaker to open. *Fix:* Scale events to 3 replicas + HPA based on CPU utilization; increase `DB_MAX_CONNS` to accommodate additional replicas.

2. **Redis is a single point of failure for the read path** — *Why it matters:* When Redis is down, `/events` (a "read-only" endpoint) times out with 504 because availability checks call Redis synchronously. The system degrades poorly instead of falling back gracefully. *Fix:* Implement fail-fast Redis timeouts (10-50ms) and treat unavailable Redis as `held = 0`; deploy Redis Sentinel for HA.

3. **No rate limiting or graceful degradation** — *Why it matters:* A traffic spike overwhelms all services, including health checks, making it impossible to distinguish capacity issues from dependency failures during an incident. *Fix:* Add token-bucket rate limiter on gateway; implement graduated circuit breaker timeouts; add request queueing with bounded size.

## 5. Toil Identification

| Toil                                                                                                                                                                                         | How often                                                                                                                                           | Automation proposal                                                                                              | Time saved                    |
|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------|-------------------------------|
| Manual DB seeding after Postgres restart| Every Pod recreation (~8× before PVC)| PVC (done in Lab 9) + InitContainer checking table existence and seeding from ConfigMap| ~2-3 min per incident         
| Multi-step DR procedure (4 commands)| Every DB incident| disaster-recover.sh script that chains operations with health checks between steps| ~15s + eliminates human error 
| Re-running in-cluster load tests with same Job manifest| 3+ times per lab run| Reusable Job template that parameterizes users/ramp/time| ~10-15 min per run            
| Manual fault injection via environment variables| ~15 times per course| Makefile targets (make inject-payment-failure, make restore-all) or Chaos Mesh| ~2-3 min per experiment       |

## 6. Monitoring Gaps

- **What I wished I had been monitoring during Lab 8 (chaos experiments):**
  - Per-service p99 latency panel with separate counters for 500/502/503 errors
  - Direct tracking of internal application resource saturation (database connection pool utilization, thread pools)
  - Per-dependency saturation metrics to see which dependency is the bottleneck
  - Latency SLO alerting (not just error rate) — Lab 8 payment latency injection produced 0% errors but 2485ms p99 latency

- **What alert would have caught the thing that actually broke:**
  - **Database pool saturation alert:** `(database_open_connections / database_max_connections) > 0.85` would have warned about connection starvation before cascading failure
  - **Container CPU throttling alert:** Events CPU approaching its limit (`container_cpu_usage_seconds_total / container_cpu_limit`) would catch saturation before users see 502s
  - **Per-service latency alert:** `histogram_quantile(0.99, gateway_request_duration_seconds) > 500ms` on the read path
  - **Metric absence (dead-man switch):** `sum(rate(gateway_requests_total{path="/events"}[2m])) == 0` to detect total route failure

## 7. Capacity Plan

### Per-pod CPU at breaking point (75-100 users, ~55-62 RPS)

```bash
kubectl top pods -l app=gateway
kubectl top pods -l app=events
kubectl top pods -l app=payments
```

```text
gateway (5 pods):   44-67m each   (limit 200m → ~25-33% utilized)   → comfortable headroom
events (1 pod):     194m          (limit 500m → ~39% utilized)      → THE bottleneck
payments (1 pod):   11m           (limit 200m → ~6% utilized)       → idle — barely reached
postgres (1 pod):   163m          (limit 500m → ~33% utilized)      → supporting events queries
redis (1 pod):      6m            (limit 200m → ~3% utilized)       → idle — not a factor
```

**Current capacity ceiling:** ~37-40 RPS (sustainable throughput without 5xx, achieved at 50 users). Beyond ~40 RPS, the events service CPU-saturates and the cascading failure begins.

### For 2× traffic (~75-80 RPS sustainable)

| Service | Current replicas | Proposed replicas | Requests | Limits | Rationale |
|---------|:----------------:|:-----------------:|:--------:|:------:|-----------|
| **gateway** | 5 | 8 | 50m | 200m | At 62 RPS, 5 pods use 56m avg = 11 RPS/pod. For 80 RPS: 80/11 ≈ 8 with headroom |
| **events** | 1 | **3** | 200m | 500m | CPU-constrained bottleneck; 3 pods share load ~27 RPS/pod, estimated ~95m CPU each |
| **payments** | 1 | 2 | 50m | 200m | Idle now (11m), but with events scaled, more traffic reaches payments → add redundancy |
| **postgres** | 1 (PVC) | 1 (PVC) | 200m | 500m | At 62 RPS, postgres is at 163m CPU — can handle 80 RPS; if >70%, add PgBouncer |
| **redis** | 1 | 1 (+Sentinel) | 50m | 200m | Single-pod OK for capacity (6m at 62 RPS), but SPOF for reliability |

**Redis:** Single-pod OK at 80 RPS for *capacity* (6m CPU out of 200m → 33x headroom). However, it's a reliability SPOF — a replicated Sentinel setup is a reliability upgrade, not a throughput one.

**DB connections:** With 3 events pods and `DB_MAX_CONNS=30` each, Postgres needs to support 90+ connections. Either increase `max_connections` or add PgBouncer for connection pooling.

### Cost estimate

| Component | Pods | Monthly ($5/pod) | Notes |
|-----------|:----:|:---------------:|-------|
| gateway | 8 | $40 | +3 pods from current 5 |
| events | 3 | $15 | +2 pods (critical bottleneck fix) |
| payments | 2 | $10 | +1 pod |
| postgres | 1 | $5 | Unchanged; PVC storage ~$0.10/GB |
| redis | 1 | $5 | Unchanged (+Sentinel is operational, not per-pod) |
| PgBouncer sidecar | 1 | $5 | Or dedicated pod for connection pooling |
| Load balancer | 1 | $15 | Ingress controller (fixed cost) |
| Storage (PVCs) | — | ~$1 | 2 PVCs × 1Gi at $0.10/GB |
| Network egress | — | ~$10 | Estimated for 80 RPS sustained |
| **Total** | **16-17 pods** | **~$106/mo** | |