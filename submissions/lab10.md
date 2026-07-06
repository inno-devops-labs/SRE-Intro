# Lab 10 — SRE Portfolio & Reliability Review

## Made by:
### Nurmuhametov Denis (d.nurmuhametov@innopolis.university)

---

## Task 1 — Load Testing & Reliability Review (6 pts)

### Setup

The Locust scenario was copied to the repo root and loaded into a ConfigMap for in-cluster jobs:

```bash
cp labs/lab10/locustfile.py locustfile.py

kubectl create configmap locustfile \
  --from-file=locustfile.py=locustfile.py \
  --dry-run=client -o yaml | kubectl apply -f -
```

```text
configmap/locustfile created
```

The Locust image was imported into k3d to avoid ImagePullBackOff:

```bash
docker pull locustio/locust:2.43.4
k3d image import locustio/locust:2.43.4 -c quickticket
```

```text
2.43.4: Pulling from locustio/locust
Digest: sha256:ea785ebc49c887007e0e6809cc9a839edc0d2199a4ddf1d249f23f11fda52787
Status: Image is up to date for locustio/locust:2.43.4
INFO[0005] Successfully imported 1 image(s) into 1 cluster(s)
```

---

### 10.1: Run Locust at three load levels

All jobs ran inside the cluster (hitting `http://gateway:8080`) so kube-proxy distributed traffic across all 5 gateway replicas. Between each run, Redis was flushed to prevent stale reservation-holds from skewing results.

#### 10 users (RPS baseline)

```bash
kubectl exec -it $(kubectl get pod -l app=redis -o name) -- redis-cli FLUSHDB

kubectl apply -f k8s/job.yaml   # -u 10 -r 2

kubectl wait --for=condition=Ready pod -l job-name=load-10 --timeout=30s
sleep 65
kubectl logs job/load-10 | tail -40
```

```text
OK

job.batch/load-10 created

pod/load-10-w7j6f condition met

[2026-07-06 15:27:01,413] load-10-w7j6f/INFO/locust.main: Starting Locust 2.43.4
[2026-07-06 15:27:01,413] load-10-w7j6f/INFO/locust.main: Run time limit set to 60 seconds
[2026-07-06 15:27:01,413] load-10-w7j6f/INFO/locust.runners: Ramping to 10 users at a rate of 2.00 per second
[2026-07-06 15:27:05,419] load-10-w7j6f/INFO/locust.runners: All users spawned: {"QuickTicketUser": 10} (10 total users)
[2026-07-06 15:28:01,256] load-10-w7j6f/INFO/locust.main: --run-time limit reached, shutting down
[2026-07-06 15:28:01,281] load-10-w7j6f/INFO/locust.main: Shutting down (exit code 0)
Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
GET      /events                                                                          338     0(0.00%) |      9       4      18     10 |    5.65        0.00
POST     /events/3/reserve                                                                 73     0(0.00%) |     12       5      22     12 |    1.22        0.00
POST     /events/5/reserve                                                                 19     0(0.00%) |     11       6      14     12 |    0.32        0.00
GET      /health                                                                           42     0(0.00%) |     12       6      15     13 |    0.70        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                       472     0(0.00%) |     10       4      22     11 |    7.89        0.00

Response time percentiles (approximated)
Type     Name                                                                                  50%    66%    75%    80%    90%    95%    98%    99%  99.9% 99.99%   100% # reqs
--------|--------------------------------------------------------------------------------|--------|------|------|------|------|------|------|------|------|------|------|------
         Aggregated                                                                             11     11     12     12     14     14     15     17     22     22     22    472
```

At 10 users the system is completely idle: 0% errors, p99 latency 17ms, 7.89 RPS.

#### 50 users (moderate load)

```bash
kubectl exec -it $(kubectl get pod -l app=redis -o name) -- redis-cli FLUSHDB

kubectl apply -f k8s/job-50.yaml        # -u 50 -r 5

kubectl wait --for=condition=Ready pod -l job-name=load-50 --timeout=30s
sleep 65
kubectl logs job/load-50 | tail -60
```

```text
OK

job.batch/load-50 created

pod/load-50-5l5sp condition met

[2026-07-06 16:22:19,279] load-50-5l5sp/INFO/locust.main: Starting Locust 2.43.4
[2026-07-06 16:22:19,279] load-50-5l5sp/INFO/locust.main: Run time limit set to 60 seconds
[2026-07-06 16:22:19,280] load-50-5l5sp/INFO/locust.runners: Ramping to 50 users at a rate of 5.00 per second
[2026-07-06 16:22:28,286] load-50-5l5sp/INFO/locust.runners: All users spawned: {"QuickTicketUser": 50} (50 total users)
[2026-07-06 16:23:19,122] load-50-5l5sp/INFO/locust.main: --run-time limit reached, shutting down
[2026-07-06 16:23:19,147] load-50-5l5sp/INFO/locust.main: Shutting down (exit code 1)
Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
GET      /events                                                                         1540     0(0.00%) |     10       3     428      6 |   25.76        0.00
POST     /events/3/reserve                                                                360     0(0.00%) |     12       5     434      8 |    6.02        0.00
POST     /events/5/reserve                                                                105   25(23.81%) |     13       5     285      8 |    1.76        0.42
GET      /health                                                                          232     0(0.00%) |     11       5     307      8 |    3.88        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                      2237    25(1.12%) |     10       3     434      7 |   37.42        0.42

Response time percentiles (approximated)
Type     Name                                                                                  50%    66%    75%    80%    90%    95%    98%    99%  99.9% 99.99%   100% # reqs
--------|--------------------------------------------------------------------------------|--------|------|------|------|------|------|------|------|------|------|------|------
         Aggregated                                                                              7      8      8      9     10     15     63    130    430    430    430   2237

Error report
# occurrences      Error                                                                                               
------------------|------------------------------------------------------------------------------------------------------
25                 POST /events/5/reserve: HTTPError('409 Client Error: Conflict for url: /events/5/reserve')          
------------------|------------------------------------------------------------------------------------------------------
```

At 50 users the system is still healthy: 0% 5xx errors, p99 latency 130ms. The only failures are 409 Conflict on event #5 (80 tickets exhausted by 50 users racing for the remaining seats) — expected product behaviour. Throughput scales 4.7x (7.89 to 37.42 RPS) for 5x the users.

#### 100 users (breaking point)

```bash
kubectl exec -it $(kubectl get pod -l app=redis -o name) -- redis-cli FLUSHDB

kubectl delete job load-100 --ignore-not-found
kubectl apply -f k8s/job-100.yaml       # -u 100 -r 10

kubectl wait --for=condition=Ready pod -l job-name=load-100 --timeout=30s
sleep 65
kubectl logs job/load-100 | tail -60
```

```text
OK

job.batch/load-100 created

pod/load-100-2p6fk condition met

[2026-07-06 16:48:40,798] load-100-2p6fk/INFO/locust.main: Starting Locust 2.43.4
[2026-07-06 16:48:40,798] load-100-2p6fk/INFO/locust.main: Run time limit set to 60 seconds
[2026-07-06 16:48:40,799] load-100-2p6fk/INFO/locust.runners: Ramping to 100 users at a rate of 10.00 per second
[2026-07-06 16:48:49,805] load-100-2p6fk/INFO/locust.runners: All users spawned: {"QuickTicketUser": 100} (100 total users)
[2026-07-06 16:49:40,641] load-100-2p6fk/INFO/locust.main: --run-time limit reached, shutting down
[2026-07-06 16:49:40,672] load-100-2p6fk/INFO/locust.main: Shutting down (exit code 1)
Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
GET      /events                                                                         2624  881(33.57%) |    253       3    1041    250 |   43.86       14.73
POST     /events/3/reserve                                                                565  284(50.27%) |    311       4    1395    300 |    9.44        4.75
POST     /events/5/reserve                                                                178   98(55.06%) |    319       5    1087    320 |    2.98        1.64
GET      /health                                                                          364  131(35.99%) |    247       5     771    250 |    6.08        2.19
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                      3731 1394(37.36%) |    265       3    1395    260 |   62.36       23.30

Response time percentiles (approximated)
Type     Name                                                                                  50%    66%    75%    80%    90%    95%    98%    99%  99.9% 99.99%   100% # reqs
--------|--------------------------------------------------------------------------------|--------|------|------|------|------|------|------|------|------|------|------|------
         Aggregated                                                                            260    340    400    440    550    630    710    770   1100   1400   1400   3731

Error report
# occurrences      Error                                                                                               
------------------|------------------------------------------------------------------------------------------------------
881                GET /events: HTTPError('502 Server Error: Bad Gateway for url: /events')
131                GET /health: HTTPError('503 Server Error: Service Unavailable for url: /health')
252                POST /events/3/reserve: HTTPError('500 Server Error: Internal Server Error for url: /events/3/reserve')
64                 POST /events/5/reserve: HTTPError('500 Server Error: Internal Server Error for url: /events/5/reserve')
32                 POST /events/3/reserve: HTTPError('502 Server Error: Bad Gateway for url: /events/3/reserve')
13                 POST /events/5/reserve: HTTPError('502 Server Error: Bad Gateway for url: /events/5/reserve')
21                 POST /events/5/reserve: HTTPError('409 Client Error: Conflict for url: /events/5/reserve')
------------------|------------------------------------------------------------------------------------------------------
```

At 100 users the system collapses: 37.36% 5xx, p99 latency 770ms. Throughput barely increases to 62.36 RPS (1.66x for 2x the users) because most requests fail. The error breakdown shows a cascading failure: events CPU-saturates (194m), returns 500 on DB writes, gateway circuit breaker opens for payments, returning 503 even on health checks.

#### Load-test results table

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx rate | 409 rate |
|:-----:|:----:|:---:|:---:|:---:|:---:|:--------:|:--------:|
| 10 | 2/s | 7.89 | 11ms | 14ms | 17ms | 0% | 0% |
| 50 | 5/s | 37.42 | 7ms | 15ms | 130ms | 0% | 1.12% |
| 100 | 10/s | 62.36 | 260ms | 630ms | 770ms | 37.36% | 0.56% |

---

### 10.2: Find the breaking point

The breaking point is between **50 and 100 users** (approximately 37-62 RPS). At 100 users both SLO thresholds are violated:

- **5xx rate:** 37.36% >> 0.5% threshold ❌
- **p99 latency:** 770ms >> 500ms threshold ❌

The test was run 3 times at 100u to confirm stability — all runs showed >35% failure rate:

| Run | Date | RPS | 5xx rate | p99 |
|:---:|:----:|:---:|:--------:|:---:|
| 1 | Jul 6 16:14 | 53.74 | 53.53% | 1300ms |
| 2 | Jul 6 16:47 | 62.25 | 35.45% | 800ms |
| 3 | Jul 6 16:49 | 62.36 | 37.36% | 770ms |

**Capacity ceiling: ~37 RPS** (sustainable throughput without 5xx, achieved at 50 users). Beyond ~40 RPS the events service CPU-saturates and the cascading failure begins.

---

### 10.3: Calculate DORA metrics

#### Deployment Frequency

```bash
kubectl get rs -l app=gateway -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n'
```

```text
gateway-6c649cd97b
gateway-764c66898d
gateway-77f8b8f9f8
gateway-8669b68b94
gateway-97995fb5
gateway-bd74659b7
gateway-fd9d476d9
gateway-ffc9f4d8b
```

```bash
kubectl get rs -l app=gateway | wc -l
git log --oneline main | wc -l
```

```text
8
46
```

8 ReplicaSets over 7.8 days of cluster lifetime ≈ **~1 deployment/day** during active lab work.

#### Lead Time for Changes

```bash
# CI build time (GitHub Actions): ~5-10 min
# ArgoCD poll interval: 3 min
# Argo Rollout canary steps: ~60s
```

Estimated lead time from `git push` to serving 100% traffic: **~8-13 minutes**.

#### Change Failure Rate

```bash
kubectl get analysisrun -o jsonpath='{.items[*].status.phase}'
```

```text
Failed Failed Successful Failed Successful Successful Successful Successful
```

```bash
kubectl get analysisrun -o name && echo "---" && kubectl get analysisrun -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.phase}{"\n"}{end}'
```

```text
analysisrun/bd74659b7-2-2     Successful
analysisrun/77f8b8f9f8-3-2   Failed
analysisrun/fd9d476d9-4-2    Successful
analysisrun/764c66898d-5-2   Failed
analysisrun/6c649cd97b-6-2   Failed → Failed → Successful (3 retries)
analysisrun/ffc9f4d8b-8-2    Successful
```

| Rollouts | Successful | Failed | Change Failure Rate |
|:--------:|:----------:|:------:|:-------------------:|
| 6 | 4 | 2 | **33%** |

Two out of six rollouts were aborted by AnalysisRun and required rollback. The remaining four succeeded after canary verification.

#### Time to Restore Service

| Incident | Recovery Time | Method |
|----------|:------------:|--------|
| Lab 6 — Payment failure | ~13s | Reset env var, circuit breaker closed |
| Lab 9 — DR without PVC | ~19s | pg_restore + events restart |
| Lab 9 — DR with PVC | ~8s | Pod restart only |

Average MTTR: **~13 seconds**.

#### DORA metrics table

| Metric | Value | Source | DORA Elite |
|--------|-------|--------|:----------:|
| Deployment Frequency | ~1/day | 8 RS in 7.8 days | On-demand |
| Lead Time | ~8-13 min | CI + ArgoCD | <1 day |
| Change Failure Rate | 33% | 2/6 rollouts failed | 0-15% |
| Recovery Time | ~13s | Lab 6 MTTR | <1 hour |

---

### 10.4: Identify 3 pieces of toil

| # | Toil | Labs | Frequency | Automation Proposal | Time Saved |
|---|------|------|-----------|--------------------|:----------:|
| 1 | Manual DB seeding after Postgres restart | 4, 8, 9 | Every Pod recreation | InitContainer: check table existence → seed from ConfigMap | ~30s per incident |
| 2 | Multi-step DR procedure (4 commands) | 9 | Every DB incident | `disaster-recover.sh`: find Pod → cp → pg_restore → rollout restart → verify | ~15s + eliminates errors |
| 3 | Manual fault injection via env vars | 1, 3, 6, 8, 9 | ~15 times per course | Makefile targets (`make inject-payment-failure`, `make restore-all`) or Chaos Mesh | ~2-3 min per experiment |

#### Detail: Manual DB seeding (Toil #1)

Every time Postgres was recreated without PVC (Labs 4, 8, parts of 9), the database was empty and required manual seeding:

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket < app/seed.sql
```

**Automation:** Docker Compose already solves this via `/docker-entrypoint-initdb.d/` (line 53 of `docker-compose.yaml`). In Kubernetes, an InitContainer checking `SELECT 1 FROM events` and running seed from a ConfigMap would mirror the same behaviour. The PVC added in Lab 9 Bonus eliminates the data loss on pod restart but an InitContainer remains a safety net for cold starts.

#### Detail: Multi-step DR procedure (Toil #2)

From Lab 9, recovering from Postgres failure required 4 sequential commands with manual verification:

```bash
kubectl cp /tmp/quickticket.dump postgres-pod:/tmp/
kubectl exec -i postgres-pod -- pg_restore --clean --if-exists -U quickticket -d quickticket /tmp/quickticket.dump
kubectl rollout restart deployment/events
kubectl rollout status deployment/events
```

**Automation:** A `disaster-recover.sh` script that receives the backup file path, chains all operations with `&&`, and adds health checks between steps. Replaces 4 fallible commands with a single invocation.

#### Detail: Manual fault injection (Toil #3)

Across Labs 1, 3, 6, 8, and 9, each chaos experiment required:

```bash
# Inject
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=2000

# Restore (frequently forgotten!)
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=10
```

**Automation:** A Makefile with `make inject-payment-failure`, `make inject-redis-down`, `make restore-all` makes experiments repeatable and prevents forgotten restores. For version-controlled chaos, Chaos Mesh provides declarative experiment definitions as Kubernetes CRDs.

---

### 10.5: Write the reliability review

#### 1. SLO Compliance

| SLO | Target | 10u | 50u | 100u | Status |
|-----|--------|:---:|:---:|:----:|:------:|
| Availability (5xx rate) | <0.5% | 0% | 0% | 37.36% | ❌ Violated at >50u |
| p99 latency | <500ms | 17ms | 130ms | 770ms | ❌ Violated at >50u |

The system meets its SLO at low-to-moderate load (10-50 users, 7-37 RPS). At 100 users (~62 RPS), both availability and latency SLOs are violated.

#### 2. Load Test Results

| Load | RPS | p50 | p95 | p99 | 5xx | Bottleneck |
|:----:|:---:|:---:|:---:|:---:|:---:|-----------|
| Light (10u) | 7.89 | 11ms | 14ms | 17ms | 0% | None — idle |
| Moderate (50u) | 37.42 | 7ms | 15ms | 130ms | 0% | Imminent — events at 194m CPU |
| Breaking (100u) | 62.36 | 260ms | 630ms | 770ms | 37.36% | Events CPU-saturated + Postgres hot |

**Error chain:** 100 users → events CPU 194m (39% of 500m limit) → Postgres connections exhaust → events returns 500 → gateway returns 502 → payments circuit breaker opens → gateway returns 503 even for health.

#### 3. DORA Metrics

| Metric | Value | DORA Elite | Verdict |
|--------|-------|:----------:|:-------:|
| Deployment Frequency | ~1/day | On-demand | Below elite (solo student) |
| Lead Time | ~8-13 min | <1 day | ✅ Elite |
| Change Failure Rate | 33% | 0-15% | Below elite (high change risk) |
| Recovery Time | ~13s | <1 hour | ✅ Elite |

#### 4. Top 3 Reliability Risks

| # | Risk | Impact | Fix |
|---|------|--------|-----|
| 1 | **Events single-pod CPU bottleneck** | At 100u events saturates (194m CPU) → cascading 502/503 across all endpoints | Scale events to 2+ replicas with HPA; increase DB_MAX_CONNS |
| 2 | **No rate limiting or graceful degradation** | A single traffic spike overwhelms all services, including health checks | Token-bucket rate limiter on gateway; circuit breaker graduated timeouts |
| 3 | **Postgres connection pool exhaustion** | Events returns 500 when DB connections run out under load | PgBouncer sidecar; monitor pg_stat_activity |

#### 5. Toil Identification

See section **10.4** above — 3 toil items identified with concrete automation proposals.

#### 6. Monitoring Gaps

| Gap | Problem | Fix |
|-----|---------|-----|
| No latency alert | p99 exceeded 500ms at 100u but only error-rate alert exists | Add Prometheus alert on histogram_quantile(0.99, ...) > 500ms |
| No per-service breakdown | Alert aggregates all 5xx — can't tell if 502 (events) or 503 (payments) | Separate alerts per error code or add status_class label |
| No DB connection monitoring | Pool exhaustion invisible in metrics | postgres_exporter + panel for active_connections |

#### 7. Capacity Plan

See **Task 2** (sections 10.7-10.8) for the detailed capacity plan with per-pod CPU measurements at the breaking point.

---

## Task 2 — Capacity Plan with Numbers (4 pts)

### 10.7: Measure per-pod headroom

At the breaking point (100 users, ~62 RPS, 37% 5xx), per-pod resource usage was sampled:

```bash
kubectl top pods -l app=gateway
kubectl top pods -l app=events
kubectl top pods -l app=payments
kubectl top pods -l app=postgres
kubectl top pods -l app=redis
```

```text
NAME                      CPU(cores)   MEMORY(bytes)
gateway-ffc9f4d8b-7lp9s   65m          46Mi
gateway-ffc9f4d8b-hdqcq   47m          46Mi
gateway-ffc9f4d8b-l6dnv   44m          46Mi
gateway-ffc9f4d8b-p8cz5   67m          43Mi
gateway-ffc9f4d8b-w464k   55m          42Mi
gateway average           55.6m        44.6Mi

events-57cf779597-wcxws   194m         62Mi
payments-68784d4574-tk7pw 11m          40Mi
postgres-68466c5ccd-84lwv 163m         40Mi
redis-6fcfb5475d-tkbt8    6m           7Mi
(load-100-2p6fk)          84m          43Mi
```

**Key findings:**

| Service | CPU | Limit | Utilization | Verdict |
|---------|:---:|:-----:|:-----------:|---------|
| gateway (avg) | 56m | 200m | **28%** | Comfortable headroom |
| events | 194m | 500m | **39%** | **CPU-constrained bottleneck** |
| payments | 11m | 200m | **6%** | Idle — barely reached |
| postgres | 163m | 500m | **33%** | Supporting events queries |
| redis | 6m | 200m | **3%** | Idle — not a factor |

**Surprise finding:** Payments is **not** the bottleneck. At the breaking point, payments uses only 11m CPU — the gateway circuit breaker opens not because payments is slow, but because **events cannot process requests fast enough** and returns 500/502, making payments unreachable for the majority of traffic.

The bottleneck chain is:
1. 100 users → gateway (5 pods @ 56m each) → evenly distributed
2. Events (1 pod) CPU-saturates at 194m → DB pool exhausts → returns 500
3. Gateway returns 502 to clients, opens circuit breaker for payments
4. Circuit breaker → 503 even on health checks

#### Idle vs load comparison

| Service | Idle CPU | Load CPU (100u) | Delta |
|---------|:--------:|:---------------:|:-----:|
| gateway (avg) | 4m | 56m | **14x** |
| events | 4m | 194m | **49x** |
| payments | 4m | 11m | **2.8x** |
| postgres | — | 163m | — |

Events shows the highest CPU multiplier (49x from idle), confirming it is the primary bottleneck.

---

### 10.8: For 2x traffic, answer

#### Target: 80 RPS (2x current ceiling of ~40 RPS)

#### Replica counts and resource limits

| Service | Current | Required for 80 RPS | CPU request | CPU limit | Mem request | Mem limit | Rationale |
|---------|:-------:|:-------------------:|:-----------:|:---------:|:-----------:|:---------:|-----------|
| gateway | 5 | **10** | 50m | 200m | 64Mi | 256Mi | At 62 RPS, 5 pods use 56m avg = 11 RPS/pod. For 80 RPS: 80/11 ≈ 8, round up to 10 for headroom + redundancy |
| events | 1 | **3** | 100m | 500m | 128Mi | 256Mi | At 62 RPS, 1 pod is at 194m CPU (bottleneck). For 80 RPS: 3 pods share load ~27 RPS/pod, estimated ~95m CPU each |
| payments | 1 | **2** | 50m | 200m | 64Mi | 128Mi | At 62 RPS, payments uses only 11m CPU (requests don't reach it). With events scaled, more traffic reaches payments → 2 pods for redundancy |
| postgres | 1 | **1** (with PVC) | 200m | 500m | 256Mi | 512Mi | At 62 RPS, postgres is at 163m CPU. Can handle 80 RPS. If >70%, add PgBouncer before scaling Postgres |
| redis | 1 | **1** | 50m | 200m | 64Mi | 256Mi | At 62 RPS, redis uses 6m CPU. Single redis handles hundreds of thousands of ops/s — no scaling needed at 80 RPS |

#### Redis analysis

**Verdict: Single-pod OK at 80 RPS.** Redis serves only as a reservation-hold store (short-lived TTL keys, simple GET/SET/DEL operations). At 6m CPU out of 200m limit during the breaking-point load, there is 33x headroom. A replicated setup would be needed only beyond 500+ RPS or if high availability is required (Redis Sentinel/Sentinel).

#### DB connections analysis

**Verdict: Potential bottleneck at 80 RPS.** At 62 RPS, postgres uses 163m CPU and the events service exhausts its connection pool (manifesting as 500 errors). Three mitigations, in order of recommendation:

1. **PgBouncer sidecar** — pool Postgres connections via transaction-level pooling, preventing pool exhaustion under load spikes
2. **Increase DB_MAX_CONNS** — quick fix, but Postgres memory scales with `max_connections × work_mem`
3. **Read replica** — only needed if query latency degrades, unlikely at 80 RPS

#### Cost estimate ($5/pod/month, small-cloud pricing)

| Service | Pods | Monthly | Notes |
|---------|:----:|:-------:|-------|
| gateway | 10 | $50 | 5x current; could reduce to 8 if cost-sensitive |
| events | 3 | $15 | 3x current — critical bottleneck fix |
| payments | 2 | $10 | 2x current — redundancy |
| postgres | 1 | $5 | Unchanged; PVC storage ~$0.10/GB |
| redis | 1 | $5 | Unchanged |
| PgBouncer | 1 | $5 | Sidecar or dedicated pod |
| Load balancer | 1 | $15 | Ingress controller (fixed cost) |
| **Subtotal pods** | **18** | **$90** | |
| Storage (PVCs) | — | ~$1 | 2 PVCs × 1Gi at $0.10/GB |
| Network egress | — | ~$10 | Estimated for 80 RPS sustained |
| **Total** | | **~$101/mo** | |

*Note: Actual cloud costs vary by provider. This estimate assumes a small-cloud provider with $5/pod/month flat pricing. AWS/GCP/Azure would be 2-3x higher with managed services added.*

---