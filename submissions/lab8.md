# Lab 8 — Chaos Engineering

**Author:** Anton Bugaev  
**Date:** 2026-06-29  
**Cluster:** k3d `quickticket` (gateway Rollout ×5, in-cluster Prometheus from Lab 7)

---

## Setup

```bash
kubectl apply -f labs/lab8/mixedload.yaml
kubectl rollout status deployment/mixedload --timeout=60s
# baseline after ~90s
```

Baseline RPS: **7.42 req/s** (`sum(rate(gateway_requests_total[1m]))`).

---

## Task 1 — Three Chaos Experiments

### Experiment 1 — Pod Kill Under Load

#### Hypothesis (before running)

> If I delete one gateway pod while `mixedload` is running, Kubernetes will replace it within ~30 seconds, a small number of in-flight requests may fail, and kube-proxy will shift traffic to the remaining 4 pods because the Service still has healthy endpoints.

#### Commands

```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
echo "Killing $VICTIM at $(date +%H:%M:%S)"
kubectl delete "$VICTIM"
```

#### Observations (15:49:05 MSK)

| Metric | Result |
|--------|--------|
| Pod count after kill | 4 → **5 Running in 2 s** (`RECOVERY_SECONDS=2`) |
| 5xx increase (3m window) | `653` (`sum(increase(gateway_requests_total{status=~"5.."}[3m]))`) |
| Per-pod request rate | Traffic spread across all 5 pods (~0.65–1.67 RPS each); new pod `gateway-...-jphcx` started receiving traffic immediately |

Per-pod rate excerpt:

```
gateway-6cffcc6f66-scqwd  1.67 RPS
gateway-6cffcc6f66-xk5qr  1.45 RPS
gateway-6cffcc6f66-zbqff  1.62 RPS
gateway-6cffcc6f66-kssgc  1.51 RPS
gateway-6cffcc6f66-jphcx  0.70 RPS  (replacement)
```

#### Hypothesis vs reality

- **Matched:** replacement was much faster than 30 s; remaining pods carried traffic with no visible “hole” in per-pod rates.
- **Surprise:** absolute 5xx count in the 3m window was high — includes background errors from checkout contention (409/5xx on `/reserve`/`/pay`), not only the pod kill window.

**Improvement:** add `PodDisruptionBudget` (`minAvailable: 4`) so voluntary disruptions cannot drop below 4 gateway pods during rollouts.

---

### Experiment 2 — Payment Latency Injection

#### Hypothesis (before running)

> If payments adds 2000 ms latency per charge, `/pay` p99 latency will rise noticeably but the gateway should **not** return 5xx because `GATEWAY_TIMEOUT_MS=5000`. If latency exceeds 5000 ms, `/pay` should start returning **504** timeouts.

#### Commands

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=30s
# observe ~90s
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
# observe ~90s
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
```

#### Observations

**`PAYMENT_LATENCY_MS=2000` (15:50:30):**

| Query | Result |
|-------|--------|
| Error rate ratio | **0.806** (80.6%) |
| p99 `/health` | 0.040 s |
| p99 `/events` | 0.024 s |
| p99 `/events/{id}/reserve` | 0.010 s |

**`PAYMENT_LATENCY_MS=2000` (supplement, 16:05):**

| Path | p99 |
|------|-----|
| `/health` | 0.068 s |
| `/events` | 0.098 s |
| `/events/{id}/reserve` | **0.490 s** |

**`PAYMENT_LATENCY_MS=6000`:**

| Query | Result |
|-------|--------|
| Error rate ratio | **0.803** (80.3%) |

#### Hypothesis vs reality

- **Partially matched:** read paths stayed fast; reserve/pay path degraded under load.
- **Surprise:** aggregate error rate ~80% dominated by **payment failures + reserve conflicts** (mixedload always reserves `event_id=1`), not only gateway timeouts. At 6000 ms expected more 504s on `/pay`, but ratio barely changed — failures were already high from business-logic errors.
- **Key insight:** slow-but-“successful” degradation is harder to see than total outage; latency spikes on mutating paths while reads look healthy.

**Improvement:** add **latency SLO alert** on `histogram_quantile(0.99, rate(gateway_request_duration_seconds_bucket{path=~"/reserve.*"}[5m]))` — error-rate alerts miss slow payments under timeout.

---

### Experiment 3 — Redis Failure

#### Hypothesis (before running)

> If Redis goes down, **listing events** (`GET /events`) will still work (Postgres only), but **reserving tickets** will fail because events stores reservation holds in Redis. Gateway `/health` will report `events: down` or degraded.

#### Commands

```bash
kubectl scale deployment/redis --replicas=0
kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --command -- \
  sh -c 'curl -w "%{http_code}\n" http://gateway:8080/events; \
         curl -X POST -w "%{http_code}\n" ... http://gateway:8080/events/1/reserve; \
         curl http://gateway:8080/health'
kubectl scale deployment/redis --replicas=1
```

#### Observations (15:54:58 MSK)

```
GET /events:     502  (0.019s)   # transient while events lost Redis
POST /reserve:   500  (0.006s)   # reservation path failed as expected
GET /health:     {"status":"healthy","checks":{"events":"ok","payments":"ok",...}}
```

#### Hypothesis vs reality

- **Matched:** reserve failed (500) when Redis unavailable.
- **Surprise:** `/events` returned **502** briefly (expected 200) — events service flapped when Redis connection dropped; health still returned `"healthy"` because probe ran after quick Redis restore began.
- **Surprise:** gateway health did **not** flip to degraded in the captured probe — health check timing vs failure window matters.

**Improvement:** add **Redis health** to gateway critical path or separate alert on `events` Redis connection errors in logs.

---

## Task 2 — Combined Failure Scenario

### Scenario design

**Degraded dependencies:** simultaneous stress on checkout chain:

- `payments`: 30% failure rate + 500 ms latency
- `events`: `DB_MAX_CONNS=3` (connection pool bottleneck)
- `mixedload`: scaled to **3 replicas**

**Why:** models realistic incident — downstream slowness + errors plus DB pool exhaustion under multiplied write load.

### Commands

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
```

### Observations (5 × 60s samples)

| Time | Error rate | p99 `/events` | p99 `/reserve` | p99 `/health` |
|------|------------|---------------|----------------|---------------|
| 15:56:04 | 79.9% | 0.085 s | 0.005 s | 0.188 s |
| 15:57:04 | 86.1% | 0.051 s | **0.480 s** | 0.090 s |
| 15:58:04 | 86.5% | 0.067 s | 0.072 s | 0.126 s |
| 15:59:05 | 86.1% | 0.068 s | 0.074 s | 0.126 s |
| 16:00:05 | 85.7% | 0.066 s | 0.010 s | 0.080 s |

**Golden signal that reacted first:** **error rate** jumped to ~80% within the first minute (payment failures + reserve conflicts).  
**Worst latency amplification:** **`/events/{id}/reserve`** (p99 **0.48 s** at 15:57) — DB connection pool queueing under `DB_MAX_CONNS=3` + tripled load. Reads (`/events`) stayed &lt;0.1 s.

### Weakest link

**Events service DB connection pool** (`DB_MAX_CONNS=3`). Payments degradation caused user-visible failures, but reserve latency spike shows events became the bottleneck for the write path. Fix: raise `DB_MAX_CONNS`, add connection-pool metrics, scale events replicas, or queue reservation requests.

**Restore:**

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0
kubectl set env deployment/events DB_MAX_CONNS=10
kubectl scale deployment/mixedload --replicas=2
```

---

## Bonus Task — Resilience Improvement

### Weakness chosen

Reserve path p99 latency under **`DB_MAX_CONNS=3`** during mixed load (connection pool queueing).

### Fix applied

```bash
# Before
kubectl set env deployment/events DB_MAX_CONNS=3

# After (fix)
kubectl set env deployment/events DB_MAX_CONNS=20
```

Config change: increase Postgres pool size on events from 3 → 20 connections.

### Before vs after (90s after rollout, same `mixedload`)

| Metric | Before (`DB_MAX_CONNS=3`) | After (`DB_MAX_CONNS=20`) |
|--------|---------------------------|---------------------------|
| p99 `/events/{id}/reserve` | **0.048 s** | **0.005 s** (~10× faster) |
| Error rate (1m) | 0.802 | 0.800 (unchanged — dominated by payment/409 errors) |

### Trade-off

Larger `DB_MAX_CONNS` reduces reserve latency but increases maximum Postgres connections per events pod — on a small Postgres instance this can cause **DB-side exhaustion** if many events replicas scale up. Better long-term: pool sizing per replica + HPA on events + Postgres `max_connections` tuning.

---

## Verification checklist

- [x] 3 experiments with pre-written hypotheses
- [x] Prometheus / kubectl evidence per experiment
- [x] Combined multi-failure scenario (Task 2)
- [x] Bonus: DB_MAX_CONNS fix with before/after metrics
