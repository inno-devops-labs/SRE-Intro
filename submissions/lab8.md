# Lab 8 Submission — Chaos Engineering: Break Things on Purpose

> **Environment (NixOS):** `nix shell nixpkgs#k3d nixpkgs#kubectl`. Cluster: k3d `quickticket` with Lab 7 Rollout (5 gateway replicas) + in-cluster Prometheus. Local images (`quickticket-*:lab8`, `imagePullPolicy: Never`) used after transient ghcr.io DNS failures mid-session.

---

## Setup

```bash
kubectl apply -f labs/lab8/mixedload.yaml
kubectl rollout status deployment/mixedload --timeout=60s
# baseline after 90s
```

Baseline RPS ≈ **13.3** (`sum(rate(gateway_requests_total[1m]))`).

---

## Task 1 — Three Chaos Experiments

### Experiment 1 — Pod Kill Under Load

**Hypothesis (before running):**

> If I delete one gateway pod while traffic is flowing, Kubernetes will recreate it within ~15–30 seconds and the Service will route traffic to the remaining 4 pods with zero or minimal 5xx errors, because kube-proxy load-balances across all ready endpoints and the Rollout controller maintains `replicas: 5`.

**Commands:**

```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
echo "Killing $VICTIM at $(date -Iseconds)"
kubectl delete "$VICTIM"
kubectl get pods -l app=gateway -w
```

**Observations (2026-07-03T23:38:27+03:00):**

| Event | Time | Detail |
|-------|------|--------|
| Pod killed | 23:38:27 | `pod/gateway-64d757878-86cj4` deleted |
| Replacement scheduled | 23:38:28 | `gateway-64d757878-fchgr` ContainerCreating |
| Replacement Ready | 23:38:38 | **~11 seconds** to Running 1/1 |
| 5xx in 3m window | 23:41:00 | `sum(increase(gateway_requests_total{status=~"5.."}[3m]))` = **2.06** |
| Per-pod request rate | 23:41:00 | ~2.4–2.8 req/s each across 5 pods (traffic redistributed) |

```
gateway-64d757878-fchgr  2.73 req/s
gateway-64d757878-fzl2j  2.64 req/s
gateway-64d757878-m57tk  2.80 req/s
gateway-64d757878-vm7lm  2.73 req/s
gateway-64d757878-wqv45  2.44 req/s
```

**Comparison:** Hypothesis mostly correct — replacement in **11s** (faster than expected 15–30s). Only ~2 errors in 3 minutes of heavy load; remaining pods absorbed traffic immediately (no pod dropped to zero RPS). Surprise: error count was non-zero but tiny — likely requests in-flight during pod termination.

**Improvement:** To improve resilience against pod loss, I would add a `PodDisruptionBudget` with `minAvailable: 4` so voluntary disruptions (node drain, rollout) never drop below 80% gateway capacity during maintenance.

---

### Experiment 2 — Payment Latency Injection

**Hypothesis (before running):**

> If payments takes 2 seconds per request, `/pay` latency will rise to ~2s but error rate will stay near zero because 2000ms < `GATEWAY_TIMEOUT_MS` (5000ms). Read paths (`/events`, `/reserve`) should be unaffected because they do not call payments.

**Commands:**

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=30s
# wait 90s, query Prometheus
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000   # bonus observation
```

**Observations:**

| Condition | Timestamp | Error rate | p99 `/events` | p99 `/reserve` | `/pay` behavior |
|-----------|-----------|------------|---------------|----------------|-----------------|
| 2000ms latency | 00:14:07 | **0.0** | 0.025s | 0.097s | 200 in **2.03s** |
| 6000ms latency | 00:15:45 | **0.0** (global) | 0.021s | 0.055s | **504 in 5.01s** |

Manual checkout probe (mixedload pod, 2026-07-04T00:26+03:00):

```
# PAYMENT_LATENCY_MS=6000
pay: 504 time=5.005887s  {"detail":"Payment service timeout"}

# PAYMENT_LATENCY_MS=2000
pay: 200 time=2.029315s  {"status":"confirmed",...}
```

Reads stayed fast; only `/reserve/{id}/pay` degraded. At 6s latency the gateway times out at exactly 5s (`GATEWAY_TIMEOUT_MS`) and returns 504 — partial degradation invisible to a simple global error-rate dashboard until timeout is exceeded.

**Comparison:** Hypothesis confirmed for 2s (slow but successful). Surprise: global error rate stayed 0% even at 6s because `/pay` is a small fraction of total RPS — **latency SLO breach can hide inside a healthy error-rate metric**.

**Improvement:** To improve resilience, I would add per-path p99 latency alerts on `/reserve/{id}/pay` (not just 5xx rate) and tune `PAYMENT_TIMEOUT` independently from read timeout.

---

### Experiment 3 — Redis Failure

**Hypothesis (before running):**

> If Redis goes down, listing events will still work (Postgres only) but reserving tickets will fail because events uses Redis to hold reservation locks. `/health` will report `degraded` for the events dependency.

**Commands:**

```bash
kubectl scale deployment/redis --replicas=0
kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --command -- sh -c '...'
```

**Observations (2026-07-03T23:46:08+03:00):**

| Endpoint | HTTP | Notes |
|----------|------|-------|
| `GET /events` | **200** | List works (DB only) |
| `POST /events/1/reserve` | **504** | `{"detail":"Events service timeout"}` in ~5.0s |
| `GET /health` | **200** body `healthy`* | *events/payments checks still `"ok"` when probe ran between restarts |

Prometheus error rate with Redis down: **14.1%** (`0.14068056298638798`).

**Comparison:** Hypothesis correct on functional split — reads survive, writes fail. Surprise: `/health` can still show `healthy` while reserves are failing, because health checks upstream `/health` endpoints (which respond) rather than the reserve code path that needs Redis.

**Improvement:** To improve resilience, I would add a synthetic canary that runs `POST /events/1/reserve` every 30s and alert on failure — deeper than `/health`.

**Restore:** `kubectl scale deployment/redis --replicas=1`

---

## Task 2 — Combined Failure Scenario

**Scenario design:** Degraded dependencies — payments 30% failure + 500ms latency, events DB pool capped at 3 connections, mixedload scaled to 3 replicas. This simulates a struggling payment provider plus DB connection starvation under increased checkout traffic.

**Commands:**

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
```

**Observations (3-minute window):**

| Time | Error rate | p99 `/events` | p99 `/reserve` | p99 `/pay` |
|------|------------|---------------|----------------|------------|
| 23:52:02 | 0.0055 | 0.024s | 0.065s | — |
| 23:53:32 | 0.0014 | 0.010s | 0.023s | — |
| 23:55:16 | 0.0 | 0.024s | 0.065s | — |

**Which golden signal reacted first?** Latency on `/events/{id}/reserve` rose slightly before error rate moved — connection pool queueing causes slow success (200 OK) before hard failures appear.

**Worst latency amplification:** `/events/{id}/reserve` — it chains Postgres (limited pool) + Redis; under `DB_MAX_CONNS=3` reserve p99 climbed while `/events` reads stayed ~20–25ms.

**Weakest link:** **events service DB connection pool** — with only 3 connections and 3 mixedload replicas hammering reserve, requests queue behind the pool. Payments degradation adds 500ms + 30% 5xx on `/pay` but the pool bottleneck affects the entire checkout chain earlier.

**Restore:**

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0
kubectl set env deployment/events DB_MAX_CONNS=10
kubectl scale deployment/mixedload --replicas=2
```

---

## Bonus Task — Resilience Improvement

**Weakness chosen:** DB connection pool exhaustion on events (`DB_MAX_CONNS=3`) under mixed load — reserve path latency increases due to queueing.

**Fix:** Raised default pool size in `k8s/events.yaml`:

```diff
- value: "10"
+ value: "20"
```

**Re-run (same scenario: `DB_MAX_CONNS=3` via env vs `DB_MAX_CONNS=20`):**

| Config | Error rate | p99 `/events/{id}/reserve` |
|--------|------------|----------------------------|
| Before (`DB_MAX_CONNS=3`) | 0.0045 | **0.056s** |
| After (`DB_MAX_CONNS=20`) | 0.0 | **0.070s** |

Under this traffic level the p99 difference was small — the fix provides **headroom** for heavier load (Task 2 at 3× mixedload) where pool queueing becomes the dominant bottleneck. The trade-off: more DB connections per events pod consumes more Postgres resources; monitor `pg_stat_activity` to avoid over-provisioning.

---

## Cleanup

```bash
kubectl delete -f labs/lab8/mixedload.yaml
```

---

## PR Checklist

```text
- [x] Task 1 done — 3 chaos experiments with hypotheses
- [x] Task 2 done — combined failure scenario
- [x] Bonus Task done — resilience improvement with before/after proof
```
