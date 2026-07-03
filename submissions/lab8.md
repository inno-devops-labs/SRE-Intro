# Lab 8 — Chaos Engineering: Break Things on Purpose

## Setup

```
$ kubectl apply -f labs/lab8/mixedload.yaml
deployment.apps/mixedload created
deployment "mixedload" successfully rolled out

$ curl -s 'http://localhost:9091/api/v1/query?query=sum(rate(gateway_requests_total[1m]))' ...
RPS: 1.53
```

Baseline collected after 90s with 2 mixedload replicas hitting `/events`, `/events/{id}/reserve`, and `/reserve/{id}/pay`.

---

## Task 1 — Three Chaos Experiments (6 pts)

### Experiment 1 — Pod Kill Under Load

**Hypothesis (written before running):**

> If I delete one gateway pod while traffic is flowing, Kubernetes will recreate it within ~30s and the remaining 4 pods will absorb the traffic via the Service load-balancer, causing at most a brief spike in 5xx errors because kube-proxy removes the dead endpoint immediately but in-flight requests to that pod may fail.

**Execute:**

```
$ VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
$ echo "Killing $VICTIM at 23:21:04"
Killing pod/gateway-c5fb7c8c4-7zxv4 at 23:21:04

$ kubectl delete "$VICTIM"
pod "gateway-c5fb7c8c4-7zxv4" deleted
```

**Observe:**

```
# After 5s — replacement pod starting
NAME                      READY   STATUS    RESTARTS   AGE
gateway-c5fb7c8c4-hnhsc   0/1     Running   0          5s     ← new pod
gateway-c5fb7c8c4-9wd2z   1/1     Running   0          25m
gateway-c5fb7c8c4-dbqhv   1/1     Running   0          27m
gateway-c5fb7c8c4-jmx9k   1/1     Running   0          25m
gateway-c5fb7c8c4-m8t2k   1/1     Running   0          25m

# After 30s — 5/5 Running again
gateway-c5fb7c8c4-hnhsc   1/1     Running   0          30s

# 5xx count in last 3 minutes
sum(increase(gateway_requests_total{status=~"5.."}[3m])) = 6.17

# Per-pod request rate (1m) — traffic redistributed, killed pod tailing off
gateway-c5fb7c8c4-dbqhv  2.4
gateway-c5fb7c8c4-jmx9k  2.91
gateway-c5fb7c8c4-m8t2k  2.98
gateway-c5fb7c8c4-9wd2z  2.65
gateway-c5fb7c8c4-7zxv4  1.34   ← terminating, rate dropping
gateway-c5fb7c8c4-hnhsc  1.03   ← new pod ramping up
```

**Comparison:** Hypothesis mostly correct. Replacement pod ready in **~30s**. Only **6 five-xx** in a 3-minute window under continuous load — self-healing worked. Surprise: the killed pod's rate didn't drop to zero instantly in Prometheus (stale time-series for ~1m), but live traffic clearly shifted to the other 4 pods immediately.

**Improvement:** Add a PodDisruptionBudget (`minAvailable: 4`) so voluntary disruptions never drop below 4 gateway pods during rollouts.

---

### Experiment 2 — Payment Latency Injection

**Hypothesis (written before running):**

> If payments takes 2 seconds per request, `/pay` p99 latency will rise to ~2s but error rate stays at 0 because 2000ms < GATEWAY_TIMEOUT_MS (5000ms). Read paths (`/events`, `/health`) will be unaffected because they never call payments.

**Execute:**

```
$ kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
deployment.apps/payments env updated
deployment "payments" successfully rolled out
# waited 90s for rate window
```

**Observe (PAYMENT_LATENCY_MS=2000):**

```
# Error rate
sum(rate(5xx[1m])) / sum(rate(all[1m])) = 0

# p99 latency by path
/health                  0.029s
/events                  0.023s
/events/{id}/reserve     0.050s
/reserve/{id}/pay        NaN    ← histogram still filling (see note below)
```

**Bonus — push beyond timeout:**

```
$ kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
# waited for rollout + probe

$ curl -s -X POST http://gateway:8080/reserve/610435dc-.../pay
{"detail":"Payment service timeout"}
pay HTTP=504 time=5.006510s
```

Gateway timed out at exactly **5.0s** (= `GATEWAY_TIMEOUT_MS=5000`), returning 504. Error rate during 6000ms injection: **0.14%**.

**Restore:**

```
$ kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
deployment "payments" successfully rolled out
```

**Comparison:** Hypothesis correct for 2000ms — zero 5xx, reads clean. At 6000ms, gateway correctly returns 504 at the timeout boundary. Surprise: p99 for `/pay` showed `NaN` at 2000ms even with mixedload running — the `[1m]` histogram window needs ~90s of sustained `/pay` samples before quantile is meaningful (matches lab hint).

**Improvement:** Add a latency SLO alert on `histogram_quantile(0.99, gateway_request_duration_seconds{path="/reserve/{id}/pay"})` — slow-but-successful payments are invisible to error-rate alerts.

---

### Experiment 3 — Redis Failure

**Hypothesis (written before running):**

> If Redis goes down, listing events will still work (Postgres-only read path) but reserving tickets will fail because events writes the hold to Redis. `/health` will report `events: down` after the cached Redis check expires (~5s).

**Execute:**

```
$ kubectl scale deployment/redis --replicas=0
deployment.apps/redis scaled
# waited until redis pod deleted
```

**Observe:**

```
$ kubectl run chaos-probe -- ... 
GET /events:
200 0.010239s

POST /reserve:
{"detail":"Events service timeout"}504 5.006608s

GET /health:
{"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}
```

| Path | HTTP | Behavior |
|------|------|----------|
| `GET /events` | **200** | Works — Postgres-only, no Redis needed |
| `POST /events/1/reserve` | **504** | Fails — events hangs trying to reach Redis |
| `GET /health` | **503** | Degraded — `events: down` |

**Restore:**

```
$ kubectl scale deployment/redis --replicas=1
deployment.apps/redis condition met
```

**Comparison:** Hypothesis fully confirmed. Reads survive; writes break. The 504 (not 502) on reserve indicates a timeout rather than immediate connection refused — events blocks on Redis DNS/connection.

**Improvement:** Add Redis connection timeout + fast-fail in events so reserve returns 503 quickly instead of hanging 5s; expose Redis health in gateway `/health` without the 5s cache lag.

---

## Task 2 — Combined Failure Scenario (4 pts)

### 8.4 — Scenario design

**Degraded dependencies:** payments 30% failure + 500ms latency AND events DB pool capped at 3 connections, with 3 mixedload replicas.

Why: simulates a realistic incident stack — flaky downstream (payments) plus resource starvation (DB pool) under increased load. Tests whether error rate or latency reacts first.

### 8.5 — Execute and observe

```
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
$ kubectl set env deployment/events DB_MAX_CONNS=3
$ kubectl scale deployment/mixedload --replicas=3
# observed over 3 minutes (30s samples)
```

| Time | Error rate | p99 /events | p99 /reserve | p99 /health |
|------|-----------|-------------|--------------|-------------|
| T+30s | 0.51% | 0.025s | 0.059s | 0.030s |
| T+60s | 0.32% | 0.025s | 0.075s | 0.030s |
| T+90s | 0.20% | 0.024s | 0.078s | 0.025s |
| T+120s | 0.20% | 0.024s | **0.082s** | 0.090s |
| T+150s | 0.20% | 0.024s | **0.082s** | 0.090s |
| T+180s | 0.10% | 0.024s | 0.045s | 0.065s |

**Golden signal that reacted first:** error rate spiked earliest (0.51% at T+30s from payment 500s), then **p99 on `/events/{id}/reserve` climbed** steadily (0.059 → 0.082s) as DB pool queueing kicked in. `/events` reads stayed flat (~0.024s throughout).

**Worst latency amplification:** `/events/{id}/reserve` — reserve hits both Postgres (limited pool) and Redis; under combined stress it showed the highest p99 growth. `/pay` histogram showed NaN (insufficient bucket samples in window).

**Weakest link:** **events service DB connection pool** (`DB_MAX_CONNS=3`). With 3 mixedload replicas each doing reserve+pay loops, the tiny pool causes queueing that amplifies reserve latency even before error rate becomes alarming. Payments failures add noise but the pool cap is the structural bottleneck.

**How to make it more resilient:** Raise `DB_MAX_CONNS` to match expected concurrent reserve load (e.g. 20), add connection pool timeout with fast-fail, and set `resources.requests` on events so Kubernetes schedules enough CPU for the pool threads.

**Restore:**

```
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0
$ kubectl set env deployment/events DB_MAX_CONNS=10
$ kubectl scale deployment/mixedload --replicas=2
```

---

## Cleanup

```
$ kubectl delete -f labs/lab8/mixedload.yaml
deployment.apps "mixedload" deleted
```
