# Lab 8 — Chaos Engineering: Break Things on Purpose

**Branch:** `feature/lab8`
**Cluster:** k3d `quickticket`, single node, arm64 (macOS host)
**Gateway:** Argo Rollout `gateway`, 5 replicas, stable RS `6458cb677d`, image `ghcr.io/r3v1k/quickticket-gateway:2fed1f97…`
**Observation:** in-cluster Prometheus (`monitoring/prometheus`, scrape interval 5 s), queried with `kubectl exec -n monitoring deployment/prometheus -- wget -qO- …`
**Raw terminal output:** `raw.log`, section `=== LAB 8 ===` (every command below is timestamped there)
**Timeline:** pre-flight, setup, Exp 1 and Exp 2 (2 s) on 2026-10-07; the host suspended during Exp 2 (6 s), which was measured and restored on 2026-10-11 together with Exp 3, Task 2 and the Bonus (see Exp 2). All times are UTC.

- [x] Task 1 — 3 chaos experiments with hypotheses
- [x] Task 2 — combined failure scenario
- [x] Bonus Task — resilience improvement with before/after proof (events health/reconnect fix, `app/events/main.py`)

---

## 8.0 Starting state and things the lab text does not mention

### Pre-flight (read-only, 2026-10-07 17:11Z)

| Check | Result |
|---|---|
| gateway | Argo Rollout, `Healthy`, step 6/6, 5/5 Ready, all on stable RS `6458cb677d` |
| gateway env | `EVENTS_URL=http://events:8081`, `PAYMENTS_URL=http://payments:8082`, `GATEWAY_TIMEOUT_MS=5000`, `APP_VERSION=v2`. No Lab 7 leftovers |
| gateway probes | readiness `GET /health` period 5 s, failureThreshold 2, timeout 1 s (default); liveness `GET /health` initialDelay 10 s, period 10 s, failureThreshold 3 |
| events / payments / redis / postgres | all `Deployment`, 1 replica each; live env equals `k8s/*.yaml` (`PAYMENT_FAILURE_RATE=0.0`, `PAYMENT_LATENCY_MS=0`, `DB_MAX_CONNS=10`, `REDIS_TIMEOUT_MS=1000`); redis has **no probes** |
| Prometheus | running, 5/5 `gateway` targets `up` |
| ArgoCD `quickticket` | `syncPolicy: {"automated":{}}` (no selfHeal, no prune), `OutOfSync` on `Deployment gateway` + `Service gateway` (git still has the Deployment), last sync `2026-09-28T07:21:55Z` at `d343061` = current fork `main`. Left unchanged. No commits to fork `main` during the lab, so no automated sync could fire (confirmed at cleanup). |

### Surprises

| # | What happened | What I did |
|---|---|---|
| 1 | **Gateway `/health` already returns intermittent 503s with zero user traffic.** All ~1.5 RPS of idle traffic is kubelet probes (5 pods × (1/5 + 1/10) per s). Of that, `/health 503` ran at ≈0.073/s; increase over 30 min per pod: wpkwk 35, 9v985 21, mwrz7 16, p8wwz 8, pbd54 8 (≈154 in 24 h). `kubectl get events` shows `Readiness probe failed: HTTP probe failed with statuscode: 503` on all five pods. events and payments logged only 200s on `/health` during the same 15 min, so the `degraded` verdict comes from the gateway's own call failing (`checks[...] = "down"`). Root cause not established. | Recorded as a known baseline condition. Every error ratio in this report is shown twice: including `/health` and excluding it. |
| 2 | **`mixedload` stops exercising `/pay` within 7 s.** Event 1 has `total_tickets=100`. Measured at 17:20: 50 confirmed orders in Postgres and `event:1:held = 50` in Redis. `reserve_tickets` increments `event:{id}:held` (`app/events/main.py:224`), but neither `confirm_reservation` nor TTL expiry ever decrements it. So available = 100 − 50 − 50 = 0, and from 17:17:52 (7 s after mixedload started) every `/reserve` returned 409. `/pay` traffic dropped to 0 and its p99 became `NaN`. | `UPDATE events SET total_tickets=100000000 WHERE id=1` at 17:22:25Z (restored to 100 in cleanup). No code, env or Redis change. The held-counter leak is a real bug, noted under weaknesses. |
| 3 | The `mixedload` `curl` calls have no `--connect-timeout`/`--max-time` (same as the Lab 7 loadgen, which froze for ~130 s after a pod kill there). | Kept the course file **unchanged** so Exp 1 shows what the stock client experiences. |
| 4 | One spontaneous `GET /events` **502** on gateway-9v985 at 17:26:02Z (`ERROR events service error: `), with nobody touching the cluster. Single request out of ~5.5 RPS. | Noted as baseline noise. |

### Baseline under full-flow mixedload (17:41:37Z, window 1m)

| Metric | Value |
|---|---|
| RPS total / excl. `/health` | 18.09 / 16.58 |
| RPS per path | `/events` 5.545, `/events/{id}/reserve` 5.527 (all 200), `/reserve/{id}/pay` 5.491 (all 200), `/health` 1.509 |
| per-pod RPS | p8wwz 3.80, pbd54 3.80, wpkwk 3.62, 9v985 3.62, mwrz7 3.29 |
| error ratio 5xx/all incl. / excl. `/health` | 0 / 0 (5m window: 0.000186 / 0) |
| p99 (s) | `/events` 0.0249, `/events/{id}/reserve` 0.0873, `/reserve/{id}/pay` 0.0688, `/health` 0.0538 |
| p50 (s) | `/events` 0.0115, `/events/{id}/reserve` 0.0166, `/reserve/{id}/pay` 0.0079 |

Histogram buckets: 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10 s, so p99 values are interpolated inside a bucket.

---

## Task 1 — Three Chaos Experiments

### Experiment 1 — Pod Kill Under Load

**Facts before the hypothesis:** gateway Rollout, 5 replicas, no PodDisruptionBudget, `imagePullPolicy: Always`, default terminationGracePeriodSeconds (30 s), no preStop hook. Readiness `/health` every 5 s, failureThreshold 2. Baseline: 16.58 user RPS spread ~3.3–3.8 RPS per pod, 0 user 5xx, p99 `/events` 25 ms, `/reserve` 87 ms, `/pay` 69 ms. mixedload: 2 replicas, sequential `curl -s` loop with no timeouts.

**1. Hypothesis** (written 2026-10-07T17:42:25Z, before the pod was deleted):

> HYPOTHESIS: "If I delete one gateway pod while mixedload is running, the Rollout's ReplicaSet will create a replacement within 2 s and the Rollout will be back to 5/5 Running+Ready within 20 s; the gateway will record 0 user-facing 5xx (`increase` of non-`/health` 5xx over the window = 0), and the remaining 4 pods will absorb the victim's ~3.6 RPS so total user RPS stays within 10 % of the 16.6 RPS baseline, because the terminating pod is removed from the Service endpoints, uvicorn drains in-flight requests on SIGTERM, and each curl opens a new connection that kube-proxy sends to a Ready pod. The one risk I expect: if a curl connects to the victim's IP after its network is gone, that client hangs (no `--connect-timeout`), which would show up as a drop in RPS, not as 5xx."

**2. Commands**

```bash
kubectl get deploy mixedload                       # 2/2 before injection
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)   # pod/gateway-6458cb677d-9v985
timeout 60 kubectl logs -f $VICTIM --timestamps --since=30s > victim.log &   # victim's last lines
# 1 s poller, 75 iterations, under timeout: date, ready/notReady endpoint count, pods
for i in $(seq 1 75); do date -u +%T; kubectl get pods -l app=gateway --no-headers; kubectl get endpoints gateway ...; sleep 1; done &
timeout 60 kubectl delete "$VICTIM"
# afterwards: Prometheus increase/rate/query_range, pod conditions, kubectl events,
# access logs of all gateway pods → per-client request gaps
```

**3. Observations**

Timeline (1 s poller + pod conditions + `kubectl get events`):

| Time (UTC) | Event |
|---|---|
| 17:44:27 | `kubectl delete pod/gateway-6458cb677d-9v985`; event `Killing — Stopping container gateway` |
| 17:44:27.495 | victim serves its last request (`GET /events 200` from mixedload 10.42.0.82) |
| 17:44:27.563 → .670 | victim log: `Shutting down` → `Finished server process [1]` (≈0.17 s after its last request) |
| 17:44:27 (poll) | victim `0/1 Completed`; Service endpoints 4 ready, 0 notReady |
| 17:44:28 | `SuccessfulCreate gateway-6458cb677d-xt6rm`, PodScheduled, `Pulling image` (pull policy `Always`) |
| 17:44:30 | `Successfully pulled image … in 1.45s`, container started; poll: `xt6rm 0/1 Running`, endpoints 4 ready + 1 notReady |
| 17:44:31 | `Readiness probe failed: … connect: connection refused` (app not listening yet) |
| 17:44:36 | xt6rm `Ready`; endpoints 5 ready, 0 notReady → **5/5** |

- Delete → replacement created: **1 s**. Delete → replacement Running: **3 s**. Delete → Running+Ready = 5/5: **9 s**.
- 5xx: `sum by (path) (increase(gateway_requests_total{status=~"5.."}[3m]))` at 17:46:30 → `/health 0`, `/events 0`; non-`/health` total **0**.
- User RPS (`rate[15s]`, step 5 s, `path!="/health"`), 17:43:57–17:45:57: 16.8 17.4 17.4 16.2 15.9 17.0 **16.9 (T0) 15.8 15.7 15.8 15.1** 17.2 17.3 16.6 17.3 15.7 16.2 18.1 16.5 15.8 17.8 17.3 16.4 16.5 16.5. Min 15.1 at 17:44:47.
- Per pod: the victim series runs 3.5 → 2.3 (17:44:32) and then disappears; `xt6rm` appears at 17:44:47 with 2.8 and then holds 2.6–4.3. Surviving pods during the gap: mwrz7 3.7/3.9, p8wwz 3.4/3.6, pbd54 2.9/4.1, wpkwk 3.5/4.1 (17:44:32/17:44:37), i.e. traffic was redistributed, not dropped.
- Client side (gateway access logs of all 6 pods for 10.42.0.81 and 10.42.0.82, window T0−30 s … T0+90 s, ~999 requests each): max gap between consecutive requests **0.398 s** (17:44:30.995 → 31.393, client .82), max gap within T0−2 s … T0+3 s **0.331 s / 0.328 s**. The loop has `sleep 0.3`, so **no client stalled**. mixedload pods printed 0 log lines.
- p99 at T0+30 s / T0+60 s: `/events` 0.0249 / 0.0249, `/reserve` 0.0808 / 0.0697, `/pay` 0.0748 / 0.0730 s (baseline 0.0249 / 0.0873 / 0.0688).
- Restore check 17:47:31: all four specs byte-identical to the saved reference, Rollout `Healthy 5/5`, gateway `/health` `healthy`, error ratio 0 / 0, 18.33 RPS.

**4. Comparison**

| Hypothesis | Measured |
|---|---|
| replacement within 2 s | created 1 s after delete (17:44:28) |
| 5/5 Running+Ready within 20 s | 9 s (17:44:36) |
| 0 user-facing 5xx | 0 (also 0 on `/health` in that window) |
| remaining 4 pods absorb the load, total user RPS within 10 % of 16.6 | survivors went up to 3.6–4.1 RPS each; total user RPS stayed between 15.1 and 17.4 (−9 % … +5 %) |
| risk: a client may hang on the dead IP | did not happen: max client gap 0.398 s |

Verdict: the hypothesis held on every measurable point; the risk I named did not materialise. What surprised me: (a) the pod went from SIGTERM to process exit in ~0.17 s and was out of the endpoints in the same second, so there was no window in which kube-proxy could send a new connection to a dead pod — the opposite of the 135 s freeze of the Lab 7 loadgen; (b) more than half of the 9 s recovery was not Kubernetes but the app itself: 1.45 s of image pull (because of `imagePullPolicy: Always` the node goes to ghcr.io even with a cached image) and a first readiness probe that hit `connection refused`, costing a full 5 s probe period; (c) the dip was small (−9 %) only because 4 of 5 pods are plenty for 16 RPS — capacity, not speed, hid the loss.

**5.** To improve resilience against this failure, I would add a PodDisruptionBudget (`minAvailable: 4`) plus a short `preStop` sleep so endpoint removal always propagates before uvicorn stops listening, and switch `imagePullPolicy` to `IfNotPresent` for immutable SHA tags so a replacement pod does not depend on ghcr.io being reachable.

### Experiment 2 — Payment Latency Injection

**Facts before the hypothesis:** `payments` `/charge` is a sync `def` that does `time.sleep(PAYMENT_LATENCY_MS/1000)` before charging (`app/payments/main.py:55-75`). Gateway calls it through `httpx.AsyncClient(timeout=GATEWAY_TIMEOUT_MS/1000)` = 5 s; `call_with_retry` and `CircuitBreaker.call` are still Lab 11 no-op stubs (one attempt, circuit always `CLOSED`). `504` is returned on `httpx.TimeoutException`. payments: 1 replica, live `PAYMENT_LATENCY_MS=0`, `PAYMENT_FAILURE_RATE=0.0` (same as `k8s/payments.yaml`). Baseline at 17:47:31: 18.33 RPS (≈5.6 RPS on each of `/events`, `/reserve`, `/pay`), error ratio 0 / 0, p99 `/events` 0.0248 s, `/reserve` 0.0847 s, `/pay` 0.0664 s. mixedload is a closed loop: 2 clients, each doing `/events` → `/reserve` → `/pay` → `sleep 0.3` sequentially.

**1. Hypothesis** (written 2026-10-07T17:48:45Z, before any env change):

> HYPOTHESIS: "If payments takes 2 s per request, the gateway will return no 5xx (non-`/health` error ratio stays 0, gateway `/health` stays `healthy`) and only `/pay` p99 will rise, to roughly 2.0–2.5 s (it falls into the 1–2.5 s histogram bucket), while `/events` and `/reserve` p99 stay within ±20 % of baseline, because 2000 ms < GATEWAY_TIMEOUT_MS = 5000 ms and the read/reserve paths never call payments. Because mixedload is a closed loop of 2 sequential clients, throughput on every path will drop from ~5.6 to ~0.8 RPS (one loop iteration goes from ~0.36 s to ~2.4 s), so the damage shows up as lost traffic rather than errors. If I push it to 6 s, every `/pay` will return 504 after ≈5.0 s (httpx timeout), the non-`/health` error ratio will be ≈ 1/3 (`/pay` fails, `/events` and `/reserve` succeed), and payments will still complete each charge after 6 s, so money is taken for reservations that are never confirmed."

**2. Commands**

```bash
kubectl get deploy mixedload                                   # 2/2
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000    # 17:48:59Z
timeout 40 kubectl rollout status deployment/payments --timeout=30s
# 90 s, sample every 15 s: RPS by path/status + p99 by path (in-cluster Prometheus)
# then: error ratio (incl./excl. /health), increase of 5xx, gateway /health, and
kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
  # x3: POST /events/1/reserve → POST /reserve/$RID/pay, print %{http_code} %{time_total}'
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000    # 17:52:04Z, bonus observation
# … same measurements + payments log / orders table for the probe's reservation
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0       # restore (12:31:38Z, 2026-10-11 — see below)
```

**3. Observations**

*2000 ms* — payments pod `6b867c9f8d-qpz7s` Ready at 17:49:04 (rollout 5 s, old pod kept serving until then).

| Time (UTC) | `/events` RPS | `/reserve` RPS | `/pay` RPS | p99 `/events` | p99 `/reserve` | p99 `/pay` |
|---|---|---|---|---|---|---|
| baseline 17:47:31 | 5.618 | 5.582 | 5.636 | 0.0248 | 0.0847 | 0.0664 |
| 17:49:44 | 2.364 | 2.345 | 2.273 | 0.0247 | 0.0249 | 2.441 |
| 17:50:00 | 1.073 | 1.000 | 1.000 | 0.0245 | 0.0613 | 2.481 |
| 17:50:16 | 0.855 | 0.891 | 0.891 | 0.0247 | 0.0628 | 2.485 |
| 17:50:31 | 0.818 | 0.818 | 0.818 | 0.0247 | 0.0183 | 2.485 |
| 17:50:47 | 0.873 | 0.800 | 0.855 | 0.0247 | 0.0099 | 2.485 |
| 17:51:03 | 0.855 | 0.800 | 0.855 | 0.0247 | 0.0217 | 2.485 |
| 17:51:18 | 0.836 | 0.855 | 0.855 | 0.0247 | 0.0215 | 2.485 |

- 17:51:36: all statuses 200, error ratio **0 / 0**, `increase(5xx[2m])` = 0, total RPS 4.055 (18.33 at baseline); gateway `/health` `healthy`, `circuit_payments: CLOSED`.
- chaos-probe, 17:51:45–17:51:49: `/pay` → **200** in **2.028 s, 2.019 s, 2.017 s**, body `"status":"confirmed"`.

*6000 ms* — set at 17:52:04, new payments pod Running at 17:52:09. **The session then stalled:** the 15 s poller's next sample came at 17:57:51 instead of 17:52:25 and returned empty results. Prometheus' own `count_over_time(up[1h])` explains it: from 2026-10-07 17:31 most hours have **6 of 720** expected samples (551 for 17:31, 253 for 18:31), i.e. the Docker VM running k3d was suspended with the host. I did not restore before the stall, so **payments stayed at 6000 ms until 2026-10-11 12:31:38Z (~3.7 days)**. The 6000 ms measurements below are from 2026-10-11, a steady state in which mixedload had been running against the injected latency.

- 12:30:39 (1 m window): `/events` 0.332 RPS 200, `/reserve` 0.332 RPS 200, `/pay` **0.330 RPS, all 504**; error ratio **0.1474** incl. `/health` / **0.3283** excl. `/health`; `increase(5xx[5m])`: `/pay 504` 29.1, `/health 503` 1.04.
- `/pay` latency buckets over 5 m: 0 requests ≤ 5 s, 31.2 in (5, 7.5], 0 in (7.5, 10], **2.07 above 10 s**; p50 6.33 s (interpolated), p99 reported as `10` (the highest finite bucket — `histogram_quantile` caps there).
- chaos-probe 12:30:54–12:31:04: `/pay` → **504** `{"detail":"Payment service timeout"}` in **5.011 s, 5.009 s, 5.009 s**.
- payments log for probe reservation `b89b7b86…`: `Injecting 6000ms latency` at 12:30:54.806, **`Payment success: PAY-57833110` at 12:31:00.808**, about one second after the gateway had already answered 504. `SELECT count(*) FROM orders WHERE id='b89b7b86…'` → **0**.
- Since the 6000 ms pod started: payments `payments_requests_total{path="/charge",status="200"}` = **3081**, but only **3** orders were created after 17:52:09 (the last at 17:52:11, i.e. requests that were already in flight). So ~3078 charges succeeded at payments with no order behind them.
- gateway `/health` stayed `healthy` throughout (its own call to payments `/health` is not delayed).
- Restore: `PAYMENT_LATENCY_MS=0` at 12:31:38, rollout done 12:31:47 (one `/health` 503 read during the pod swap), user RPS back to 16.58 by 12:32:45. 12:33:32: all four specs identical to reference, error ratio 0 / 0, p99 `/events` 0.0248, `/reserve` 0.0746, `/pay` 0.0749 s.

**4. Comparison**

| Hypothesis | Measured |
|---|---|
| 2 s: no 5xx, gateway `/health` healthy | 0 5xx, error ratio 0 / 0, `/health` healthy |
| 2 s: only `/pay` p99 rises, to ~2.0–2.5 s | `/pay` p99 2.441 → 2.485 s; real `/pay` 2.017–2.028 s by curl |
| 2 s: `/events`, `/reserve` p99 within ±20 % of baseline | `/events` 0.0245–0.0247 (−1 %); `/reserve` dropped from 0.0847 to 0.0099–0.0628 s, i.e. *better* than baseline, outside ±20 % in the good direction |
| 2 s: throughput on every path drops from ~5.6 to ~0.8 RPS | 0.800–0.873 RPS per path from 17:50:16 on; total 18.33 → 4.06 |
| 6 s: every `/pay` → 504 after ≈5.0 s | probe: 504 in 5.009–5.011 s; but in mixedload 6 % of `/pay` took **> 10 s** |
| 6 s: non-`/health` error ratio ≈ 1/3 | 0.3283 |
| 6 s: payments still charges after the 504, so orders are lost | `PAY-57833110` charged 1 s after the 504, no order; 3081 charges vs 3 orders |

Verdict: the hypothesis held — including the closed-loop throughput collapse, which is the real user-facing damage here and is invisible to an error-rate alert (error ratio stayed exactly 0 while completed purchases fell by 85 %: `/pay` 200s 5.636 → 0.855 RPS). What surprised me: (a) `/reserve` got *faster* under the fault, because the slow `/pay` throttled the whole loop and events/Postgres saw 6× less load; (b) the gateway timeout is not a hard ceiling: `httpx.AsyncClient(timeout=5)` applies 5 s to each phase (pool/connect/write/read) separately, and 2 of ~33 `/pay` requests in a 5 m window took more than 10 s — I could not pin the exact phase from the access log; (c) the worst outcome is not the 504 but the money: payments has no idea the caller gave up, so every timed-out `/pay` is a successful charge with no ticket.

**5.** To improve resilience against this failure, I would make `/pay` idempotent end-to-end — pass the reservation id as an idempotency key to payments and have payments cancel/refund a charge whose caller has disconnected (or have the gateway reconcile 504s against payments before telling the user it failed) — and add a latency SLO alert on `/pay` p99 plus a throughput-drop alert, because this fault never moves the error rate at 2 s.

### Experiment 3 — Redis Failure

**Facts before the hypothesis:** redis is a 1-replica Deployment with no probes and no persistence. events: `/health` = Postgres `SELECT 1` **and** Redis `PING` (result cached 5 s, `socket_connect_timeout=0.5`), 503 if either fails; **both readiness (5 s × 2) and liveness (10 s × 3) probe that `/health`**. events creates its Redis client once in `startup()` and sets `redis_client = None` if the first `PING` fails, with no reconnect (`app/events/main.py:52-60`). `GET /events` uses only Postgres; `/reserve` writes the hold to Redis. gateway `/health` returns 503 unless events **and** payments `/health` are 200, and the gateway's readiness (5 s × 2) and liveness (10 s × 3) both use it. Baseline at 12:33:32 (2026-10-11): 18.22 RPS, error ratio 0 / 0, p99 `/events` 0.0248, `/reserve` 0.0746, `/pay` 0.0749 s.

**1. Hypothesis** (written 2026-10-11T12:34:45Z, before Redis was scaled down):

> HYPOTHESIS: "If Redis goes down, the outage will not stay limited to reservations: within ~15 s events' `/health` (which pings Redis) returns 503, events fails readiness and drops out of its Service, then every gateway pod's `/health` (which requires events to be ok) returns 503 and within roughly another 10 s all 5 gateway pods leave the gateway Service — so `GET /events`, `POST /reserve` and `/health` all become unavailable to clients even though listing events needs only Postgres. Because liveness uses the same `/health`, I expect events and all gateway containers to be restarted repeatedly (first restart ~30 s after their probes start failing, then with back-off). When Redis comes back, I expect recovery to depend on an events restart, because an events container that started while Redis was down holds `redis_client = None` forever; liveness will do that restart on its own, so the system returns to 5/5 Ready and baseline error ratio within 30–90 s after Redis is Ready, without a manual restart."

**2. Commands**

```bash
kubectl get deploy mixedload                                  # 2/2
kubectl scale deployment/redis --replicas=0                   # 12:35:32Z
# poller (bounded: 180 s down + ≤240 s recovery): date, redis pods, events ready/restarts,
# gateway ready/restarts, ready endpoints in the gateway and events EndpointSlices,
# gateway /health from the Prometheus pod (wget -T 2). One iteration = 4 kubectl calls,
# so the real cadence was ~2–3 s, not 1 s.
# spec probe at T+70 s and T+150 s (curl with --max-time 10 added):
kubectl run chaos-probe-a --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '
  curl … /events ; curl -X POST … /events/1/reserve ; curl … /health'
kubectl scale deployment/redis --replicas=1                   # 12:38:34Z
# poll until gw 5/5 + ep_gw=5 + ep_ev=1 + /health healthy five times in a row
```

**3. Observations**

| Time (UTC) | t | What happened |
|---|---|---|
| 12:35:32 | 0 s | `scale redis --replicas=0` |
| 12:35:35 | +3 s | redis pod gone |
| 12:35:48 | +16 s | first events liveness `503` (12:35:48) and readiness `503` (12:35:50); gateway `/health` → **503** (events `degraded`) |
| 12:35:54 | +22 s | gateway 3/5 Ready, gateway endpoints 3 |
| 12:35:56 | +24 s | **events NotReady, events endpoints 0**; gateway 1/5 |
| 12:35:59 | +27 s | **gateway 0/5, gateway endpoints 0** → `wget: can't connect … Connection refused` on the gateway ClusterIP |
| 12:36:08–12:36:16 | +36 s | first liveness kills: events and all 5 gateway containers (`failed liveness probe, will be restarted`) |
| 12:36:47 | +75 s | probe a: `GET /events` **000** (0.001 s), `POST /reserve` **000**, `GET /health` **000**, curl rc=7 (connection refused) |
| 12:38:05 | +153 s | probe b: same, all **000**, rc=7 |
| 12:38:10 | | events container #4 starts while Redis is still down: `Redis connection failed: Error 111 … Connection refused` (12:38:22), so `redis_client = None` |
| 12:38:34 | | `scale redis --replicas=1` |
| 12:38:36 | **R** | redis pod Ready |
| 12:38:48–12:38:56 | R+12 s | 5th liveness kill of events (container #4 never recovered on its own) and of all gateway pods |
| 12:39:43 | R+67 s | events container #5 starts (CrashLoop back-off delay), logs `Redis connected` at 12:39:45 |
| 12:39:51 | R+75 s | events Ready, events endpoints 1 |
| 12:39:53 | R+77 s | gateway `/health` healthy again, gateway 1/5 Ready |
| 12:40:02 | R+86 s | gateway 3/5 |
| 12:41:00 | R+144 s | gateway 4/5 (pbd54 and p8wwz restarted at 12:40:57 and 12:41:02, after longer back-off) |
| 12:41:10 | **R+154 s** | **gateway 5/5**, endpoints 5; stable ×5 at 12:41:16 |

- Restarts caused by a Redis outage of 3 min: **events 5, gateway 5–6 per pod (27 in total)**. All restarts were from liveness; none were manual.
- Prometheus during the outage (`query_range`, step 30 s): `sum(up{job="gateway"})` stayed **5** most of the time (dips to 4 at 12:37:00 and to 1–3 between 12:39:00 and 12:41:00 while containers restarted) — Prometheus scrapes pods by IP, so the pods looked "up" while the Service had zero endpoints. User RPS (`path!="/health"`): 16.72 at 12:35:30 → 1.0 at 12:36:00 → **0.0** at 12:36:30, then no samples until 12:40:00 (2.45) and 17.08 at 12:40:30. 5xx rate 1.08–1.38/s during the outage — **all of it kubelet probes hitting `/health`**; the user requests never reached a gateway pod, so none were counted.
- Redis came back empty (`DBSIZE` 1 after recovery — only the new `event:1:held` counter); in-flight reservations from before the outage were lost.
- Restore check 12:42:08: all four specs identical to reference (redis back to 1 replica), Rollout `Healthy 5/5`, `/health` healthy, 18.31 RPS, error ratio 0 / 0, p99 `/events` 0.0249, `/reserve` 0.0743, `/pay` 0.0709 s. No pod had to be restarted by hand.

**4. Comparison**

| Hypothesis | Measured |
|---|---|
| events `/health` 503 within ~15 s | first events 503 at +16 s |
| events drops out of its Service | events endpoints 0 at +24 s |
| all 5 gateway pods leave the Service ~10 s later | gateway 0/5, 0 endpoints at +27 s |
| `/events`, `/reserve`, `/health` all unavailable although listing needs only Postgres | all three `000` / connection refused at +75 s and +153 s |
| events and gateway restarted repeatedly by liveness | events 5, gateway 27 restarts in total, with back-off |
| recovery needs an events restart because of `redis_client = None` | confirmed: container started at 12:38:10 logged `Redis connection failed` and stayed unhealthy after Redis was back, until liveness killed it at 12:38:49 |
| no manual restart needed | correct |
| 5/5 Ready within 30–90 s after Redis is Ready | events Ready after 75 s, but gateway 5/5 only after **154 s** |

Verdict: the mechanism of the hypothesis was right step by step, but I underestimated the recovery time by ~1 minute. The surprise is that liveness, which "healed" the system, is also what slowed recovery: after five kills every container was in CrashLoop back-off, so even after events and Redis were healthy, two gateway pods waited until 12:40:57/12:41:02 for kubelet's back-off timer. The second surprise is observability: during a total outage `up` stayed at 5 and the gateway recorded *zero* user errors — the only signal was probe traffic. A Redis failure, which should only block reservations, turned into a full site outage (including the read-only event list) because the dependency was wired into **liveness** and into a **transitive** health check.

**5.** To improve resilience against this failure, I would split health checks — liveness = "process responds" only, readiness of events = Postgres only (Redis reported as degraded, not as not-ready), gateway readiness without transitive dependency checks — and let events reconnect to Redis lazily, so a Redis outage only disables `/reserve` instead of taking down the whole site and then waiting out CrashLoop back-off.

---

## Task 2 — Combined Failure Scenario

### 8.4 Scenario design

What Task 1 taught me, and what that means for picking a scenario:

1. Exp 2: slow payments does not produce errors, it **throttles the closed-loop client**, and that load reduction made other paths look healthier. So a combined scenario should include something that does *not* get relieved when `/pay` slows down.
2. Exp 3: anything that makes events `/health` fail takes the **whole site** down through readiness → transitive gateway `/health` → liveness restarts. events `/health` takes a connection from the same DB pool as user requests (`app/events/main.py:113`).
3. The DB pool is `psycopg2.pool.ThreadedConnectionPool(minconn=2, maxconn=DB_MAX_CONNS)`. Its `getconn()` **raises `PoolError` immediately when the pool is exhausted, it does not wait**, and `reserve_tickets` holds one connection while `_get_available` takes a second one (`app/events/main.py:195` and `:290`).

Candidates:

| | Scenario | Why / what it can show | Verdict |
|---|---|---|---|
| A | **Degraded dependencies** (spec): payments `PAYMENT_FAILURE_RATE=0.3` + `PAYMENT_LATENCY_MS=500`, events `DB_MAX_CONNS=3`, mixedload 3 replicas | Three different failure modes at once: hard errors (`/pay` 30 %), latency (`/pay` +0.5 s) and a resource limit on the path that Exp 3 showed is load-bearing for availability (events pool → events `/health` → everything). | **Recommended** |
| B | Capacity crunch (spec): mixedload 5 replicas + `DB_MAX_CONNS=2` | Isolates pool exhaustion; but with only one failure dimension it is closer to a single experiment. | not chosen |
| C | Cascade (spec): Redis down + payment latency | Exp 3 already showed Redis down = full outage in 27 s; extra latency on a path that is unreachable adds nothing measurable. | not chosen |

Load setup (not a failure): mixedload is scaled to 3 replicas first and a 3-replica baseline is taken, so latency amplification is measured against the same load.

**Hypothesis** (written 2026-10-11T12:43:17Z, before mixedload was scaled and before any injection):

> HYPOTHESIS: "If payments fails 30 % of charges with +500 ms latency while events is capped at 3 DB connections under 3 mixedload clients, the first golden signal to move will be **errors**, on `/pay` (≈30 % of `/pay` → 5xx, i.e. ≈10 % of user requests), immediately after payments rolls out; latency of `/pay` will rise to ≈0.5–0.6 s p99. The weakest link will be the **events DB pool**, not payments: because `getconn()` fails instead of waiting and `/reserve` needs 2 connections, I expect intermittent 5xx on `/reserve` and `/events` as soon as 3 clients overlap, and events `/health` occasionally failing with `postgres: down`, which can flip events NotReady and briefly take gateway pods out of the Service exactly like Exp 3. The worst latency *amplification* relative to baseline will be on `/pay` (×7–8 from ~0.07 s), because errors on the DB pool fail fast rather than queue."

### 8.5 Execution and observations

```bash
kubectl scale deployment/mixedload --replicas=3                                # 12:43:26 (load setup)
# 3-replica baseline at 12:45:03
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500   # 12:45:25
kubectl set env deployment/events DB_MAX_CONNS=3                                       # 12:45:25
kubectl rollout status deployment/payments --timeout=30s; kubectl rollout status deployment/events --timeout=30s   # both done 12:45:29
# sampler: 16 samples, ~13 s apart, 12:45:29–12:48:42 (3 min 13 s), every query on the [1m] window
# restore (spec): FAILURE_RATE=0.0 LATENCY_MS=0, DB_MAX_CONNS=10, mixedload 2  → 12:49:48
```

3-replica baseline (12:45:03, 1 m): 25.11 RPS, 7.87 RPS on each of `/events`, `/reserve`, `/pay`, error ratio 0 / 0, p99 `/events` 0.1201, `/reserve` 0.0987, `/pay` 0.0985 s.

| Time | err ratio incl./excl. `/health` | `/events` 200 | `/reserve` 200 | `/pay` 200 | `/pay` 5xx | p99 `/events` | p99 `/reserve` | p99 `/pay` | events / gateway / payments pods |
|---|---|---|---|---|---|---|---|---|---|
| 12:45:29 | 0 / 0 | 8.05 | 8.11 | 7.96 | 0 | 0.0978 | 0.0973 | 0.0977 | all Ready, no new restarts |
| 12:45:42 | 0.0044 / 0.0047 | 7.22 | 7.11 | 6.93 | 0.10 | 0.0876 | 0.0973 | **0.7261** | same |
| 12:45:55 | 0.0173 / 0.0188 | 5.95 | 5.98 | 5.51 | 0.33 | 0.0919 | 0.0974 | 0.7402 | same |
| 12:46:08 | 0.0355 / 0.0391 | 5.00 | 5.07 | 4.34 | 0.59 | 0.0558 | 0.0912 | 0.7444 | same |
| 12:46:20 | 0.0564 / 0.0638 | 4.04 | 4.02 | 2.98 | 0.75 | 0.0246 | 0.0249 | 0.7469 | same |
| 12:46:33 | 0.0807 / 0.0924 | 3.55 | 3.42 | 2.56 | 0.97 | 0.0247 | 0.0156 | 0.7475 | same |
| 12:46:46 | 0.0770 / 0.0880 | 3.49 | 3.54 | 2.56 | 0.93 | 0.0247 | 0.0104 | 0.7475 | same |
| 12:46:59 | 0.0718 / 0.0821 | 3.58 | 3.58 | 2.60 | 0.87 | 0.0247 | 0.0177 | 0.7475 | same |
| 12:47:12 | 0.0823 / 0.0940 | 3.46 | 3.51 | 2.49 | 1.00 | 0.0248 | 0.0180 | 0.7475 | same |
| 12:47:25 | 0.0878 / 0.1002 | 3.56 | 3.47 | 2.44 | 1.05 | 0.0247 | 0.0205 | 0.7475 | same |
| 12:47:37 | 0.0872 / 0.0995 | 3.51 | 3.51 | 2.53 | 1.05 | 0.0247 | 0.0204 | 0.7475 | same |
| 12:47:50 | 0.0939 / 0.1071 | 3.56 | 3.56 | 2.42 | 1.15 | 0.0247 | 0.0152 | 0.7475 | same |
| 12:48:03 | 0.0923 / 0.1054 | 3.58 | 3.49 | 2.35 | 1.11 | 0.0247 | 0.0178 | 0.7475 | same |
| 12:48:16 | 0.0910 / 0.1037 | 3.56 | 3.56 | 2.46 | 1.11 | 0.0246 | 0.0190 | 0.7475 | same |
| 12:48:29 | 0.0962 / 0.1102 | 3.51 | 3.53 | 2.36 | 1.16 | 0.0247 | 0.0192 | 0.7475 | same |
| 12:48:42 | 0.0999 / 0.1140 | 3.47 | 3.54 | 2.31 | 1.20 | 0.0247 | 0.0192 | 0.7475 | same |

(`/pay` 5xx are all `500` — payments' injected failure passed through by `raise HTTPException(e.response.status_code, …)`. p99 0.7475 is the top of the 0.5–0.75 s bucket, so the true p99 is somewhere in that bucket.)

Onset at finer resolution (`rate[15s]`, step 5 s):

| | 12:45:25 | 12:45:30 | **12:45:35** | 12:45:40 | 12:45:45 | 12:45:50 |
|---|---|---|---|---|---|---|
| user RPS | 23.40 | 24.70 | **19.30** | 12.33 | 10.21 | 10.50 |
| user 5xx/s | — | — | — | **0.225** | 0.607 | 1.10 |
| p99 `/pay` (s) | 0.095 | 0.093 | **0.738** | 0.746 | 0.748 | 0.748 |
| p99 `/reserve` (s) | 0.098 | 0.096 | 0.071 | 0.024 | 0.023 | 0.020 |

Totals over the last 3 min (`increase[3m]` at 12:48:42): `/events` 200 = 634.6, `/reserve` 200 = 635.7, `/pay` 200 = 437.1, `/pay` 500 = **198.4** (31 % of `/pay`).

What happened that the metrics did *not* show:

- events log: **4 × `psycopg2.pool.PoolError: connection pool exhausted`**, all in `reserve_tickets` → `_get_available` (line 290, the *second* `getconn()` while the first connection is held), at 12:45:34, 12:45:49, 12:49:01, 12:49:06 (the last two after the sampler ended, before restore). Each reached a client as `POST /events/1/reserve` **500** (gateway access logs on wpkwk, mwrz7, xt6rm). **None of them is in Prometheus** — there is no `/events/{id}/reserve,status=500` series. The gateway's reserve handler does `raise HTTPException(e.response.status_code, e.response.json())`; events' 500 body is plain `Internal Server Error`, `.json()` raises inside the `except`, the exception escapes `metrics_middleware` before it records anything, and Starlette answers 500.
- 4 × `502` at 12:45:29 (`/events` ×2, `/reserve` ×2 on pbd54/p8wwz) during the events pod swap for `DB_MAX_CONNS=3`. Their counter series were *created* at value 1–2, so `rate()`/`increase()` never show them either (first sample of a new series has no predecessor).
- events never went NotReady, no pod restarted, gateway `/health` stayed healthy throughout.

Restore: all changes reverted at 12:49:48, rollouts done 12:49:53, user RPS 16.11 at 12:50:24 and 18.35 at 12:50:55, 5xx 0 from 12:50:55. Check at 12:51:28: all four specs identical to reference, 5/5, `/health` healthy, error ratio 0 / 0.

### Hypothesis vs. measured

| Hypothesis | Measured |
|---|---|
| errors move first, on `/pay` | **latency and traffic moved first** (12:45:35: `/pay` p99 0.093 → 0.738 s, user RPS 24.7 → 19.3); user 5xx appeared one sample later (12:45:40) |
| ≈30 % of `/pay` fail → ≈10 % of user requests | 31 % of `/pay` (198.4 / 635.5); error ratio excl. `/health` 0.114 at the end |
| `/pay` p99 ≈ 0.5–0.6 s | in the 0.5–0.75 s bucket (reported 0.7475) |
| events DB pool is the weakest link: intermittent 5xx on `/reserve` and `/events` | 4 `/reserve` 500s in ~4.5 min (vs 198 `/pay` 500s in 3 min); 0 `/events` errors from the pool |
| events `/health` fails, events flaps NotReady, cascade like Exp 3 | did not happen — no NotReady, no restarts |
| worst latency amplification on `/pay`, ×7–8 | `/pay` ×7.6 (0.0985 → 0.7475); `/events` ×0.21 (0.1201 → 0.0247) and `/reserve` ×0.19 (0.0987 → 0.0192) got *faster* |

### Which golden signal reacted first, which path amplified most

- **First:** latency (`/pay` p99) and traffic (user RPS) together at 12:45:35, ~6 s after the rollouts finished; errors followed at 12:45:40. Saturation never moved in a visible way (no NotReady, no restarts, pool exhaustion only in logs).
- **Worst amplification:** `/reserve/{id}/pay`, ×7.6 at p99. The other two paths got 4–5× faster, because the same closed-loop effect as in Exp 2 cut the load on events by more than half (7.9 → 3.5 RPS per path).

### Weakest link

**The gateway → payments call is the weakest link.** It turned 30 % downstream failures into 31 % user-visible `/pay` errors (198 in 3 min) one-for-one, with no retry, no circuit breaker (both are still no-op stubs in `app/gateway/main.py:94-160`) and no fallback, and its 500 ms of latency halved total throughput (23.4 → ~10.5 user RPS) without the error rate showing it. The DB pool, which I expected to be the weakest link, only produced 4 errors, because the closed loop never let more than ~3 requests overlap; but it is a **latent** weak link with an observability hole: `getconn()` fails instead of waiting, `/reserve` needs two connections at once, and those failures are invisible in gateway metrics because of the `e.response.json()` bug. To make it more resilient I would (1) implement the Lab 11 retry with backoff for payments behind an idempotency key (the reservation id) plus the circuit breaker, so transient payment failures are absorbed instead of returned; (2) make `/reserve` use a single connection and switch the pool to block with a short timeout instead of raising; (3) fix the gateway error handler so every 5xx is counted.

---

## Bonus Task — Resilience Improvement

### B.1 Weaknesses found, with measured impact

| # | Weakness | Evidence | Impact |
|---|---|---|---|
| W1 | Redis is wired into events readiness **and** liveness, and transitively into every gateway pod's `/health`; events never reconnects to Redis after a failed start | Exp 3 | Redis down → **whole site down 27 s later** (incl. read-only `/events`), 27 container restarts, gateway 5/5 only **154 s** after Redis was back |
| W2 | `/pay` has no idempotency: payments keeps charging after the gateway times out | Exp 2 (6 s) | 3081 charges vs 3 orders; `PAY-57833110` charged 1 s after the user got 504 |
| W3 | gateway → payments has no retry / circuit breaker (Lab 11 stubs) | Task 2 | 30 % payment failures → 31 % of `/pay` user-visible (198 in 3 min) |
| W4 | events DB pool raises instead of waiting, `/reserve` holds 2 connections; gateway doesn't count those 500s | Task 2 | 4 user-visible `/reserve` 500s, **0 in Prometheus** |
| W5 | `event:{id}:held` is never decremented | 8.0 #2 | event sold out after 100 tickets with 50 sold |

**Chosen: W1** — it is the only one that turned a partial dependency failure into a total outage, and it had the longest recovery.

### B.2 Fix options and choice

| Option | Change | Trade-off |
|---|---|---|
| A | Probes only: liveness → `/metrics` (no dependency checks) on events and gateway | Removes restart storms and CrashLoop back-off, but readiness still cascades (events NotReady → gateway `/health` 503 → gateway NotReady), so the site is still down during the outage. Needs a gateway manifest change. |
| B | **events code:** readiness (`/health` status code) gated on Postgres only, Redis reported as `degraded`; Redis client reconnects lazily from `_check_redis()` | Fixes the cascade at its root and the "stuck without Redis" recovery bug; touches only events. Events will report Ready while it cannot take reservations. |
| C | gateway `/health` without transitive checks + real circuit breaker | Most complete, but it is a gateway (Argo Rollout) change; out of scope for this lab run, and B alone already breaks the chain. |

I chose **B**. Code diff (`app/events/main.py`, in this PR):

```diff
diff --git a/app/events/main.py b/app/events/main.py
index 72c5d3c..d961823 100644
--- a/app/events/main.py
+++ b/app/events/main.py
@@ -65,18 +65,28 @@ def startup():
     else:
         log.error("Could not connect to database after 10 attempts")
 
+    _connect_redis()
+
+
+def _connect_redis() -> bool:
+    """(Re)create the Redis client. Called at startup and again from _check_redis
+    while the client is missing, so events recovers on its own once Redis is back."""
+    global redis_client
     try:
-        redis_client = redis.Redis(
+        client = redis.Redis(
             host=REDIS_HOST, port=REDIS_PORT,
             socket_timeout=REDIS_TIMEOUT_MS / 1000,
             socket_connect_timeout=0.5,
             decode_responses=True,
         )
-        redis_client.ping()
+        client.ping()
+        redis_client = client
         log.info("Redis connected")
+        return True
     except Exception as e:
         log.warning(f"Redis connection failed: {e}")
         redis_client = None
+        return False
 
 
 def get_db():
@@ -120,9 +130,12 @@ def health():
         checks["postgres"] = "down"
     checks["redis"] = "ok" if _check_redis() else "down"
 
+    # Only Postgres gates readiness: without Redis, listing events still works and
+    # only reservations fail, so a Redis outage must not take events (and, through
+    # the gateway's own /health, the whole site) out of service.
     all_ok = all(v == "ok" for v in checks.values())
     return JSONResponse(
-        status_code=200 if all_ok else 503,
+        status_code=200 if checks["postgres"] == "ok" else 503,
         content={"status": "healthy" if all_ok else "degraded", "checks": checks},
     )
 
@@ -319,8 +332,8 @@ def _check_redis():
         return _redis_ok
     _redis_checked_at = now
     if not redis_client:
-        _redis_ok = False
-        return False
+        _redis_ok = _connect_redis()
+        return _redis_ok
     try:
         _redis_ok = redis_client.ping()
     except Exception:
```

Deployed for the test without touching git-managed manifests: `docker build -t quickticket-events:lab8-bonus app/events` → `k3d image import` → `kubectl patch deployment events` (image + `imagePullPolicy: IfNotPresent`) at 12:58:15Z, Ready 12:58:22Z; `/health` → `{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}`.

### B.3 Before vs. after — exactly the same experiment

Same script for both (Exp 3's runner plus one passive field, events' own `/health`): mixedload 2 replicas, `scale redis --replicas=0`, 180 s down, spec probes at T+70 s and T+150 s, `scale redis --replicas=1`, poll until 5 stable healthy samples. "Before" was re-run right before the fix on the original image (the Exp 3 run was 17 min earlier; >10 min had passed since the last restart, so kubelet back-off had reset).

| Metric | Before (original image, 12:52:44–12:57:41) | After (fixed image, 13:00:17–13:03:29) |
|---|---|---|
| gateway Ready during outage | 4/5 at +10 s, **0/5 from +22 s** | **5/5 throughout** |
| events Ready during outage | NotReady from +22 s | **Ready throughout**; `/health` = `200 {"status":"degraded","checks":{"postgres":"ok","redis":"down"}}` |
| probe at T+70 s: `GET /events` / `POST /reserve` / `GET /health` | `000` / `000` / `000` (connection refused) | **`200` 0.007 s** / `504` 5.012 s / **`200` healthy** |
| probe at T+150 s | `000` / `000` / `000` | **`200` 0.009 s** / `504` 5.008 s / **`200`** |
| user requests served during the 181 s outage (`increase` by path) | `/events` 200: **8.0**, `/reserve` 504: 5.2 | `/events` 200: **72.0**, `/reserve` 504: 63.9, `/pay` 200: 1.0 |
| container restarts caused by the outage | events 5, gateway 23 | **0** |
| time from Redis scale-up to 5 stable healthy samples | **116 s** (events Ready +20 s, gateway 5/5 at 12:57:35) | **11 s** |
| manual action needed | none (liveness + back-off) | none |

Restore after the comparison: events back to the reference image (`ghcr.io/r3v1k/quickticket-events:2fed1f97…`, `Always`) at 13:04:44 so the cluster matches git; restore check at 13:05:20 — all four specs identical to the saved reference.

What the fix traded off: the site now stays up and browsable without Redis, but events reports Ready while it cannot hold reservations, so instead of being refused instantly every `/reserve` hangs until the gateway's 5 s timeout (64 × 504 in 3 min, p99 7.5 s) and Redis loss no longer shows up in any readiness signal — it now needs its own alert, and a follow-up should make `/reserve` fail fast with 503 when `_redis_ok` is false.

---

## Cleanup

| Step | Result (13:04:44–13:05:21Z) |
|---|---|
| events image back to reference | done, rollout OK |
| `kubectl delete -f labs/lab8/mixedload.yaml` | deleted, pods gone |
| `events.total_tickets` for id 1 | back to 100 |
| redis | 1 replica |
| env | payments `PAYMENT_FAILURE_RATE=0.0`, `PAYMENT_LATENCY_MS=0`; events `DB_MAX_CONNS=10` — all four specs byte-identical to the pre-lab reference |
| background processes | `pgrep -x kubectl` → none; no `chaos-probe` pods left |
| kept for Lab 9 | Argo Rollout `gateway` (Healthy 5/5) and `monitoring/prometheus` |
| ArgoCD | unchanged: `syncPolicy: {"automated":{}}`, `OutOfSync`, last sync still `2026-09-28T07:21:55Z` — no sync happened during the lab |
