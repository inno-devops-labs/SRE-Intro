# Lab 8 — Chaos Engineering

## Hypotheses (committed before any experiment ran)

**Experiment 1 — Pod kill under load.**
If one of the 5 gateway pods is deleted while mixedload runs, the error count stays at ~0 (at most 1–2 in-flight requests fail) and the other 4 pods absorb its traffic. The Service removes a terminating pod from endpoints right away, and the ReplicaSet starts a replacement within ~5–10 s. Total RPS should not dip noticeably.

**Experiment 2 — Payment latency 2000 ms.**
If payments takes 2 s per request, `/pay` p99 rises to ~2 s and no 5xx appear, because 2 s < `GATEWAY_TIMEOUT_MS=5000`. `/events` and `/reserve` p99 stay unchanged since they don't touch payments. Total RPS drops: each mixedload loop is sequential and now waits 2 s on `/pay`. At 6000 ms, `/pay` returns 504 after ~5 s. Gateway retries and circuit breaker are no-op stubs until Lab 11, so nothing fails fast — every request waits the full timeout.

**Experiment 3 — Redis down.**
If Redis is scaled to 0, `/reserve` fails with 5xx (504 seen in Lab 6, when events blocked on the Redis connection). `/health` reports `degraded`. Expected surprise: events' liveness probe hits `/health`, which returns 503 without Redis (same issue as Lab 4), so kubelet keeps restarting events. `/events`, which needs only Postgres, will likely start failing too after ~30 s. A Redis outage would then turn into a full outage of the read path.

## Setup

- `mixedload` (2 replicas) applied; in-cluster Prometheus from Lab 7 used for all queries.
- Event 1 had only 100 tickets, so mixedload would sell out in seconds and stop reaching `/pay`. Raised it before testing: `UPDATE events SET total_tickets=10000000 WHERE id=1`.

**Baseline (11:12:55 UTC):**
```
total RPS: 16.84   5xx: 0
rps: /events 5.65, /reserve 5.60, /pay 5.58   (all 200)
p99: /events 0.016s, /reserve 0.025s, /pay 0.069s
```

## Task 1 — Three Chaos Experiments

### Experiment 1 — Pod kill under load

**Command:**
```bash
kubectl delete $(kubectl get pods -l app=gateway -o name | head -1)
```

**Observed:**
```
Killing pod/gateway-6dcd8798dc-2bgl8 at 11:13:06
5/5 Ready again after 5.9s at 11:13:12

watch: 2bgl8 Terminating → Completed;  z957k Pending → ContainerCreating → Running 0/1 → 1/1

sum(increase(gateway_requests_total{status=~"5.."}[3m])) = 0

per-pod rps [20s] at 11:13:16 (kill+10s)        per-pod rps [1m] at 11:14:03
  xbpss 3.27  c2nj7 3.93  mqljx 4.27              xbpss 3.46  c2nj7 3.60  mqljx 3.38
  vgxwr 3.80  2bgl8 1.46 (dying)                  vgxwr 3.40  z957k 2.80 (new)
total rps 16.65 — unchanged
```
Prometheus only counts requests that reached a gateway, so a dropped connection would be invisible to it. A second kill was run with a separate client counting every response: 5 parallel curl loops on `/events` for 30 s, pod killed at 11:14:21:
```
2260 200
```
Zero client-side failures.

**Hypothesis vs reality:** matched, and was slightly better than expected: no in-flight requests failed. Endpoint removal plus uvicorn's graceful shutdown on SIGTERM drained the pod cleanly, and the 4 remaining pods picked up its share (~3.3 → ~4 RPS each). Replacement was Ready in 5.9 s.

**Improvement:** add a PodDisruptionBudget (`minAvailable: 4`) so voluntary evictions such as node drains can never take out several gateway pods at once.

### Experiment 2 — Payment latency

**Commands:**
```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000   # 11:14:52
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000   # 11:16:41
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0      # 11:18:37 (restore)
```

**Observed at 2000 ms (11:16:28):**
```
5xx ratio: 0.000
p99: /events 0.022s   /reserve 0.010s   /pay 2.485s
rps: /events 0.82  /reserve 0.84  /pay 0.84   → total 2.49 (baseline 16.84)
```

**Observed at 6000 ms (11:18:17):**
```
rps: /events 0.38  /reserve 0.36  /pay 504: 0.35   → /pay 200: 0
p99: /events 0.022s   /reserve 0.010s   /pay 7.475s (bucket interpolation, real ≈ 5.0s)
5xx ratio: 0.318
single request: {"detail":"Payment service timeout"}  504  5.006769s
```
Recovery at 11:19:57: 16.89 RPS, 0 errors.

**Hypothesis vs reality:**
- Matched: no 5xx at 2 s, only `/pay` p99 moved, reads stayed at 10–22 ms, 504 after exactly `GATEWAY_TIMEOUT_MS` (5.0 s) at 6 s.
- Surprise 1: throughput fell **85%** (16.8 → 2.5 RPS) at 2 s with zero errors. A latency-only fault looks "green" on error-based alerts while users wait 2+ s per checkout.
- Surprise 2: one `/pay` 503 `payments unreachable` at 11:14:58 was caused by the `kubectl set env` rollout itself. Payments runs a single replica, so the restart briefly broke connections.

**Improvement:** add a latency SLO alert on `/pay` p99 and give payments 2+ replicas. A real circuit breaker (Lab 11) would fail fast instead of making every request wait 5 s at 6000 ms.

### Experiment 3 — Redis failure

**Commands:**
```bash
kubectl scale deployment/redis --replicas=0                     # 11:20:06
kubectl scale deployment/redis --replicas=1                     # 11:21:23 (restore)
```

**Observed at t+10 s (11:20:12):**
```
POST /reserve: {"detail":"Events service timeout"} 504 5.011s
GET /health:   {"status":"degraded","checks":{"events":"degraded","payments":"ok",...}}
events-76df68c54f-pbb4g   1/1   Running   0
```
**Observed at t+60 s (11:21:06):**
```
GET /events:   502 0.003s
POST /reserve: {"detail":"Events service unavailable"} 502 0.004s
GET /health:   {"status":"degraded","checks":{"events":"down",...}}
events-76df68c54f-pbb4g   0/1   Running   1 (27s ago)

Warning  Unhealthy  Liveness probe failed: HTTP probe failed with statuscode: 503
Warning  Unhealthy  Readiness probe failed: HTTP probe failed with statuscode: 503
Normal   Killing    Container events failed liveness probe, will be restarted
events log: "Redis connection failed: Error 111 connecting to redis:6379. Connection refused."

rps (11:21:22): /events 502: 1.83   /reserve 502: 1.83   /reserve 504: 0.09   → 5xx ratio 1.000
```
Redis back at 11:21:23, events Ready at 11:21:27, `/events` 200.

**Hypothesis vs reality:** fully matched, including the predicted surprise. `/reserve` failed first (504 after the 5 s gateway timeout). Then events' own liveness and readiness probes, which point at `/health` and so depend on Redis, restarted it and pulled it from the Service. `/events`, which needs only Postgres, went down too, and the error rate hit **100%**. A cache/hold dependency took down the whole system.

**Improvement:** point events' probes at an endpoint that doesn't depend on Redis, so a Redis outage only breaks reservations (done in the Bonus).

## Task 2 — Combined Failure: Degraded Dependencies

**Design:** payments `PAYMENT_FAILURE_RATE=0.3` + `PAYMENT_LATENCY_MS=500`, events `DB_MAX_CONNS=3`, mixedload scaled 2 → 3. This simulates a flaky payment provider during a traffic bump, with the DB pool misconfigured at the same time — the kind of stacked partial failure real incidents are made of.

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
```

**Samples** (`rps`, 5xx ratio, p99 per path, 1m windows):
```
11:22:27 baseline  rps=16.82 err=0.000 | /events=0.024 /reserve=0.074 /pay=0.067
11:22:27 INJECT
11:23:02           rps=14.21 err=0.044 | /events=0.018 /reserve=0.025 /pay=0.743
11:23:32           rps=10.53 err=0.104 | /events=0.010 /reserve=0.022 /pay=0.748
11:24:34           rps=10.64 err=0.104 | /events=0.010 /reserve=0.022 /pay=0.748
11:25:36           rps=10.56 err=0.100 | /events=0.015 /reserve=0.022 /pay=0.748
11:26:38           rps=10.62 err=0.115 | /events=0.010 /reserve=0.023 /pay=0.748
11:27:09           rps=10.55 err=0.124 | /events=0.010 /reserve=0.023 /pay=0.748
11:27:30 RESTORE
```

**Which golden signal reacted first?** **Errors and latency together**, in the first 30 s sample: 4.4% errors, `/pay` p99 0.07 → 0.74 s. Traffic dropped next, 16.8 → 10.5 RPS despite 50% more clients, because each loop now waits ~0.5 s on `/pay`. Errors settled at ~10%, i.e. 30% of `/pay`, which is ⅓ of requests.

**Worst latency amplification:** `/pay`, 11× its baseline p99 (0.067 → 0.748 s). `/events` and `/reserve` didn't degrade and even got slightly faster, since there was less concurrent load.

**`DB_MAX_CONNS=3` had no visible effect.** mixedload clients are sequential, so 3 clients never hold more than ~3 DB connections at once, and the payment latency throttled them further. Pool saturation can't be confirmed directly: the in-cluster Prometheus scrapes only gateway, not events.

**Which component was the weakest link? How would you make it more resilient?**
**Payments**, and more precisely the gateway → payments call. Every payment failure passes straight to the user as a 5xx, and every ms of payment latency directly cuts checkout throughput. Retries and the circuit breaker are still no-op stubs. Payments also runs a single replica, which Experiment 2 showed blips on every config change. Fixes:
- 2+ payments replicas;
- an idempotent retry with backoff for 5xx (a payment reference makes retries safe);
- a circuit breaker so a dead provider fails fast;
- a `/pay` latency SLO alert.

## Bonus — Resilience Improvement

**Weakness chosen:** Experiment 3. A Redis outage became a 100% outage because events' liveness/readiness probes depend on Redis via `/health`. It had the worst impact of all experiments, and it's a one-line class of bug.

**Change** (`k8s/events.yaml`):
```diff
           livenessProbe:
             httpGet:
-              path: /health
+              path: /metrics
               port: 8081
 ...
           readinessProbe:
             httpGet:
-              path: /health
+              path: /metrics
               port: 8081
```
The same fix was applied to gateway in Lab 7. Its `/health` also depends on events and payments.

**Re-run** (same commands; Redis down 11:28:27 → 11:30:11, observed at t+90 s):
```
GET /events:   200 0.014s
POST /reserve: {"detail":"Events service timeout"} 504 5.009s
events-77f9c76cfc-hptzg   1/1   Running   0          ← no restarts, stays Ready

rps: /events 200: 0.44   /reserve 504: 0.38
5xx ratio: 0.467
reserve after Redis restore: 200
```

| Metric (Redis down) | Before fix | After fix |
|---|---|---|
| `GET /events` | 502 | **200** |
| `POST /reserve` | 502 / 504 | 504 (expected — needs Redis) |
| 5xx ratio | **1.000** | **0.467** |
| events restarts | 1 within 60 s | 0 in 90 s |
| events Ready | 0/1 | 1/1 |

Users can still browse during a Redis outage; only reservations fail.

**Trade-off:** probes no longer catch a dead Postgres connection, so a broken events pod stays in rotation and returns 500s instead of being pulled out. With a single replica that is the same user impact anyway. Reserve still takes the full 5 s to fail; making events fail fast on Redis (short connect timeout → immediate 503) would be the next fix.
