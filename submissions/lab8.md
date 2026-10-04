# Lab 8. Chaos engineering: breaking QuickTicket on purpose

**Student:** Kirill Fadeev
**Email:** ki.fadeev@innopolis.university
**Environment:** WSL2 (kernel 6.18.33.2-microsoft-standard-WSL2, x86_64), k3d v5.9.0, k3s v1.35.5+k3s1, kubectl v1.37.0, Argo Rollouts v1.10.0 (gateway is the 5 replica Rollout from Lab 7), in-cluster Prometheus v3.11.2 scraping every 5 s

All times are UTC. Every number below comes from a capture file taken during the run on 2026-10-03.

## Method

Traffic came from two sources running at the same time:

- `labs/lab8/mixedload.yaml` (2 replicas, `/events` then reserve then pay, `sleep 0.3` per loop), exactly as the lab provides it.
- A client-side probe of my own (`chaosprobe`): a Deployment that reuses the gateway image (it is already on the node and has Python) and runs two threads against `http://gateway:8080` through the Service. One thread calls `GET /events` every 0.2 s, the other runs reserve then pay every 0.5 s. It prints one line per second with counts per status and the slowest request in that second.

The probe exists because gateway metrics only count requests that reach a gateway process. A request refused at the Service, or one that crashes the handler, never shows up in `gateway_requests_total`. Experiment 3 and Task 2 show that this gap is real, not theoretical.

Prometheus was queried with the lab's queries through `kubectl exec -n monitoring deploy/prometheus -- wget`, using a small helper that URL-encodes the expression. Health endpoints were excluded from the per path tables (`path!="/health"`) because kubelet probes would otherwise dominate them.

The hypotheses were written into a local file at 16:10:55, before the loadgen was even applied, and are quoted verbatim below without edits.

## Pre-experiment finding: the loadgen sells out in 20 seconds

Right after applying mixedload, every reserve started returning 409:

```plaintext
16:11:21 {"/events": {"200": 5}, "reserve": {"409": 2}}
16:11:22 {"/events": {"200": 4}, "reserve": {"409": 2}}
```

```plaintext
 id | total_tickets | confirmed | orders
----+---------------+-----------+--------
  1 |           100 |        50 |     50
redis event:1:held = 50
redis live reservation keys = 0
redis event:1:held TTL = -1
```

Event 1 has 100 tickets, 50 were sold, and 50 more are still "held", although no reservation exists any more. In `app/events/main.py` the reserve handler increments `event:{id}:held`, but neither confirm nor expiry ever decrements it, and the key has no TTL. Every paid ticket is therefore subtracted twice: once as an order and once as a hold. With a 409 on reserve, the loadgen never calls `/pay`, so experiments 2 and 3 would have shown nothing.

To keep the experiments meaningful I set `total_tickets = 100000000` for event 1 and reset `held` to 0 (16:11:42); the full checkout flow returned to 200 within two seconds. After the lab both values were put back.

> A load generator that silently stops exercising the code under test is a chaos experiment of its own. Without the probe printing per-path status codes, the payment experiments would have "passed" because no payment was ever attempted.

---

## Task 1. Three chaos experiments

### Experiment 1. Gateway pod kill under load

**Hypothesis (written before the run):**

```plaintext
If I delete one gateway pod while mixedload is running, the ReplicaSet will have a
replacement Running and Ready in under 15 s (image already on the node, readiness period 5 s),
and the remaining 4 pods will absorb the traffic. Gateway-side 5xx stays at 0, because a
request that fails while the Service still points at the dying pod never reaches any gateway
process and is never counted in gateway_requests_total. Any client-visible failures will be
at most a handful of connection errors during endpoint removal, visible only to a client-side probe.
```

**Commands:**

```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
kubectl delete "$VICTIM" --wait=false
# readiness polled every 0.5 s, plus a background
kubectl get pods -l app=gateway -w --output-watch-events
```

**Observations:**

```plaintext
[16:13:54.302] Killing pod/gateway-65b977dbb6-4kpmk
16:13:54.434 MODIFIED   gateway-65b977dbb6-4kpmk   1/1   Terminating
16:13:55.060 MODIFIED   gateway-65b977dbb6-4kpmk   0/1   Completed
16:13:55.077 ADDED      gateway-65b977dbb6-vhqjz   0/1   Pending
16:13:56.031 MODIFIED   gateway-65b977dbb6-vhqjz   0/1   Running
16:14:02.041 MODIFIED   gateway-65b977dbb6-vhqjz   1/1   Running
16:14:02.842 ready=5 total=5        (readiness poll, every 0.5 s)
```

The replacement pod existed 0.8 s after the delete, its container ran 1 s later, and readiness passed 6 s after that (one probe period plus the first probe). From the delete command to the poll that saw 5/5 Ready: 8.5 s. (The capture script also printed "8.99 s", but its start stamp was taken before the `kubectl get pods` call that picks the victim, so it overstates the gap by about 0.4 s.)

Errors, from Prometheus and from the client:

```plaintext
sum(increase(gateway_requests_total{status=~"5.."}[3m]))   => 0.0000
client probe non-200 lines in whole run:                    none
```

Total throughput did not move, while per-pod rates show the handover:

```plaintext
total rps (rate[30s], 5 s step)
16:13:48 all 25.280
16:13:53 all 25.440   <- kill at 16:13:54
16:13:58 all 24.598
16:14:03 all 25.031
16:14:08 all 24.384   <- minimum of the window
16:14:13 all 25.428
16:14:18 all 24.435
16:14:23 all 24.740
16:14:28 all 24.805
16:14:33 all 25.218
16:14:38 all 25.760   <- maximum of the window
16:14:43 all 25.160
(full table 16:13:23 to 16:15:13 in the capture; every value is between 24.384 and 25.760)

per-pod rps (rate[30s], 10 s step)
16:14:03 pod=...-4kpmk 3.551
16:14:13 pod=...-4kpmk 1.707   pod=...-vhqjz 1.601
16:14:23                        pod=...-vhqjz 3.420
16:14:33                        pod=...-vhqjz 5.778
```

**Hypothesis vs reality.** Recovery time and the zero 5xx matched. The surprise was on the client side: I expected a few connection errors during endpoint removal and got none out of about 550 probe requests in the 80 s after the kill. The endpoint was withdrawn while uvicorn was still shutting down gracefully, so no request reached a closed socket. The per-pod graph is also misleading at first glance: the killed pod seems to "drain" over 20 s and the new one to "ramp up", but that is the 30 s rate window, not traffic. The total line stays between 24.38 and 25.76 rps the whole time.

> With 5 replicas on one node, losing a pod is a non-event for users. The only place it is visible at all is a per-pod rate panel, and even there the shape is produced by the `rate()` window rather than by the outage.

**To improve resilience against this failure, I would** add a PodDisruptionBudget (`minAvailable: 4`) and a short `preStop` sleep so that voluntary evictions on a multi-node cluster stay as clean as this single delete, and keep a client-side probe as the SLI, because server-side metrics cannot see requests that were refused before reaching a pod.

### Experiment 2. Payment latency injection

**Hypothesis (written before the run):**

```plaintext
If payments takes 2 s per request, /pay p99 will rise to about 2 s (histogram bucket
2.5) with 0 % 5xx, because 2000 ms < GATEWAY_TIMEOUT_MS 5000 ms. /events and /reserve p99 stay
unchanged. Total throughput of mixedload drops sharply (each loop iteration is sequential, so it
goes from roughly 0.3 s+ per loop to 2.3 s+ per loop), so /events RPS also falls even though
/events itself is healthy. Payments readiness stays green since its /health does not sleep.
Bonus 6000 ms: /pay returns 504 after ~5 s, but payments still finishes the sleep and records a
successful charge, so customers are charged for orders that are never confirmed.
```

**Commands:**

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=60s     # 9.2 s
# wait 90 s, query, then
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
# wait 90 s, query, then restore to 0
```

**Observations at 2000 ms (16:19:46 to 16:21:19):**

```plaintext
                         baseline (16:19:36)     latency 2000 ms (+90 s)
5xx ratio [1m]           0.0000                  0.0000
p99 /events              0.0880 s                0.0250 s
p99 /reserve             0.1528 s                0.0414 s
p99 /pay                 0.0972 s                2.4850 s
rps /events 200          9.80                    5.49
rps /reserve 200         7.07                    1.25
rps /pay 200             7.11                    1.25
```

```plaintext
reserve rps over time (rate[30s])
16:19:32 all 7.120
16:19:47 all 7.480   <- new payments pod ready at 16:19:46
16:20:02 all 4.000
16:20:17 all 1.280
16:20:32 all 1.200
```

```plaintext
client probe: 16:21:13 {"/events": {"200": 5}, "pay": {"200": 1}, "reserve": {"200": 1}} max_s {"pay": 2.02}
gateway /health: {"status":"healthy","checks":{"events":"ok","payments":"ok",...}}
```

**Observations at 6000 ms (16:21:29 to 16:23:02):**

```plaintext
5xx ratio [1m]                       0.0758
rps /pay 504                         0.5455
rps /pay 200                         0.0000
p99 /pay                             7.4750 s
/pay outcomes, increase[90s]:        200 => 0    504 => 42.9
client probe:  16:22:53 {"pay": {"504": 1}} max_s {"pay": 5.01}

payments_charges_total{result="success"}  0 at 16:21:29 (fresh pod, series not yet created)
                                          48 at 16:23:01
orders total                              3646 at 16:21:29
                                          3646 at 16:23:01
payments log 16:23:02  "Payment success: PAY-6201ADA6 for 105cef65-..."
gateway /health:       {"status":"healthy", ... "payments":"ok"}
```

**Hypothesis vs reality.** Everything in the hypothesis held, and the bonus case was worse than it reads in the lab text:

- At 2000 ms the system is "green" by every error metric, health is `healthy`, and checkout throughput fell by 82 % (7.1 to 1.25 rps). The loadgen is sequential, so one slow dependency throttles the whole client, including its reads. Read p99 actually improved (less concurrent work), which a dashboard would show as a good sign.
- At 6000 ms the gateway did protect itself: every `/pay` returned 504 at 5.01 s. But payments kept sleeping and then charged anyway. In 90 s it recorded 48 successful charges while the orders table did not gain a single row. Every one of those customers paid and got no ticket.
- The reported p99 of 7.475 s is not a real latency. No request took longer than 5.01 s; `histogram_quantile` interpolates inside the 5 to 10 s bucket. The 5xx ratio of 7.6 % looks mild, while the success rate of checkout was exactly 0 %, because `/events` traffic dilutes the ratio.

> Partial degradation hides in averages twice: the error ratio is diluted by healthy read traffic, and the latency quantile is invented by bucket interpolation. The only honest signal in this experiment was the business counter (charges vs orders), which nobody alerts on.

**To improve resilience against this failure, I would** give the payments call an idempotency key and a reconciliation step (refund or confirm charges whose order never arrived), make payments honour a deadline shorter than the gateway timeout so it stops working on requests nobody waits for, and alert on `/pay` p99 against a latency SLO instead of on 5xx alone.

### Experiment 3. Redis failure

**Hypothesis (written before the run):**

```plaintext
If Redis goes down, the system will NOT degrade gracefully as the lab text suggests.
events /health returns 503 (redis check), events readiness fails after 2 x 5 s, events is removed
from its Service; gateway /health then reports events "down/degraded" and returns 503, so all 5
gateway pods fail readiness and the gateway Service ends up with zero endpoints. Expected result:
total outage, including GET /events which does not need Redis. Before that cascade completes,
POST /reserve fails with 500 because redis_client is not None (it connected at startup) and
setex raises an unhandled ConnectionError; the gateway then calls e.response.json() on a plain
text 500 body inside its except block, which raises again and becomes an unhandled 500 in gateway.
```

The reasoning came from the live specs: gateway readiness is `/health` on 8080, which calls `events/health`; events readiness is `/health` on 8081, which pings Redis.

**Commands:**

```bash
kubectl scale deployment/redis --replicas=0
# every 2 s: ready endpoints of the events and gateway Services (EndpointSlices)
# at +15 s and +70 s: the lab's chaos-probe curl pod
kubectl scale deployment/redis --replicas=1
kubectl wait --for=condition=Available deployment/redis --timeout=90s
```

**Observations:**

```plaintext
16:24:25.546 scaling redis to 0
16:24:27.295 redis_pods=0 events_ready_eps=1 gateway_ready_eps=5
16:24:29.850 events: "Redis unavailable for availability check: Error 111 ... Connection refused."
16:24:31     probe:  "reserve": {"504": 1}  max_s 5.01
16:24:37.264 events: psycopg2.pool.PoolError: connection pool exhausted
16:24:37     probe:  "/events": {"200": 4, "502": 1}
16:24:45.294 .. 16:24:47.426  all 5 gateway pods  1/1 -> 0/1
16:24:46.616 events pod 1/1 -> 0/1
16:24:48     probe:  "/events": {"ERR:URLError": 5}
16:24:49.320 redis_pods=0 events_ready_eps=0 gateway_ready_eps=0
...           (unchanged for 62 s)
16:25:52.537 restore redis
16:25:53.657 redis Available after 1.1 s
16:25:58.684 events pod 0/1 -> 1/1
16:26:00.321 .. 16:26:02.074  gateway pods 0/1 -> 1/1
16:26:01     probe:  first "/events": {"200": 3} after the gap
```

The lab's own curl check, at +15 s and at +70 s:

```plaintext
GET /events:
000 0.001280s
POST /reserve:
 000 0.000884s
GET /health:
 000
```

`000` is curl's code for "no HTTP response at all": with zero ready endpoints, kube-proxy rejects the connection to the gateway ClusterIP.

What Prometheus showed at +75 s:

```plaintext
5xx ratio [1m]                               1.0000
rps /events 200                              0.0000
rps /events/{id}/reserve 504                 0.0364
p99 /events                                  nan
up{job="gateway"}                            1 for all 5 pods
total gateway rps (all paths incl. /health)  1.0545
```

**Hypothesis vs reality.** The cascade happened exactly as predicted: Redis, which only the reserve path needs, took down the whole product, including listing events. Users had no successful `GET /events` from 16:24:48 to 16:26:01, 73 s in total for an 87 s Redis outage. After the scale-up at 16:25:52.5, the first successful `GET /events` and the first full checkout came at 16:26:01 (8.5 s), and the last gateway pod turned Ready at 16:26:02.07 (9.5 s). Three details differed from the hypothesis:

- Reserve failed with 504 after 5.0 s, not with an instant 500. Only the first attempt got `Connection refused`; after that the events log shows `Timeout connecting to server`, and with two Redis calls per reserve the handler did not answer within the gateway's 5 s budget.
- The read path broke before the cascade. While reserves hung waiting for Redis, each one held a Postgres connection from the shared pool of 10. At 16:24:37 the pool ran dry and `GET /events`, which never touches Redis, failed: events returned a 500 (`PoolError`), and the gateway turned it into a 502, which is the `"502": 1` in the probe line at 16:24:37. Redis latency leaked into reads through the DB pool.
- The 5xx ratio of 1.0 is produced entirely by kubelet probes hitting `/health` on each pod IP. Real user traffic was refused at the Service and recorded nowhere: user path rates are 0 and `/events` p99 is `nan`. A dashboard without `/health` in it would show an idle system with no errors during a total outage.

> The health check was the failure amplifier. A dependency that only one endpoint needs was wired into readiness two levels deep, so Kubernetes faithfully removed every replica of every service in front of it. Server-side metrics went quiet at the exact moment the outage started.

**To improve resilience against this failure, I would** make readiness shallow so that a pod leaves the Service only when it itself cannot serve (implemented and measured in the bonus task), and make events release its DB connection before calling Redis and fail fast on Redis errors with a JSON 503 instead of holding the connection through timeouts.

---

## Task 2. Combined failure scenario: degraded dependencies

**Design.** Payments at 30 % failures plus 500 ms latency, events with `DB_MAX_CONNS=3`, and mixedload scaled to 3 replicas, held for 3.5 minutes (16:28:24 to 16:32:04). This is the lab's "degraded dependencies" scenario. I chose it because it stacks a loud failure (payments errors that the gateway passes through as 500) on top of a quiet one (a small DB pool), and the interesting question is which of them the monitoring actually notices.

**Commands:**

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
# sampled every 20 s: 5xx ratio, rps by path/status, p99 by path, probe
# restore: 0.0 / 0 / 10 / replicas=2
```

**Observations over the window:**

```plaintext
5xx ratio (rate[30s], 15 s step)
16:28:14 all 0.000   <- injection applied at 16:28:24
16:28:29 all 0.003
16:28:44 all 0.033
16:28:59 all 0.060
16:29:14 all 0.096
16:30:14 all 0.058
16:31:44 all 0.098
16:31:59 all 0.085
```

```plaintext
p99 by path (rate[30s])      before     16:29:14    16:31:44
/events                      0.087      0.025       0.062
/events/{id}/reserve         0.097      0.037       0.086
/reserve/{id}/pay            0.091      0.747       0.747
p50 /pay at end: 0.625 s
```

```plaintext
rps (rate[30s])              16:27:44    16:31:44
/events 200                  10.16       8.16
/reserve 200                 7.28        4.44
/pay 200                     7.40        2.72
/pay 500                     0.00        1.72
```

Logs over the same window:

```plaintext
events:   19  psycopg2.pool.PoolError: connection pool exhausted
gateway:  18  json.decoder.JSONDecodeError
           1  confirm error after payment: Server error '500 Internal Server Error'
```

Client probe totals for the window against what the gateway recorded for reserve:

```plaintext
probe:       reserve 200=206  500=4  504=1      pay 200=137  500=69
Prometheus:  /events/{id}/reserve  status="200" ... (no status="500" series exists at all)
```

**Which golden signal reacted first.** Errors: the 5xx ratio left zero 5 s after the injection and passed 3 % within 20 s. Latency on `/pay` rose in the same 30 s window and then sat flat at 0.747 s, which is the 0.5 to 0.75 bucket boundary rather than a measurement. Traffic fell more slowly (reserve from 7.3 to 4.4 rps) because the slower `/pay` throttles the sequential loadgen. Saturation never showed up, because nothing in the setup exports DB pool usage to Prometheus (it only scrapes the gateway).

**Worst latency amplification.** `/pay`: p99 went from 0.09 s to 0.75 s (about 8 times), p50 to 0.63 s. `/events` and reserve stayed under 0.1 s. The lab's dry-run note predicts reserve p99 above 5 s from pool queueing with `DB_MAX_CONNS=3`; that did not happen here, and the logs show why: `psycopg2.pool.ThreadedConnectionPool.getconn()` does not queue, it raises `PoolError` immediately when the pool is empty. A small pool turns into fast errors, not slow requests.

**Weakest link.** The events DB pool combined with the gateway's error path for reserve. Payments failed loudly: 69 of 206 probe payments returned 500 (33 %, the injected 30 % plus confirmation failures), and every one of them was counted. The pool failures were silent. When events raised `PoolError`, it returned a plain text 500; the gateway's reserve handler then called `e.response.json()` inside its `except httpx.HTTPStatusError` block, which raised `JSONDecodeError`, and the exception escaped through `metrics_middleware` before the counter was incremented. The client got a 500, and Prometheus recorded nothing. The same pool also broke one confirm after a successful charge, so the user saw "Payment succeeded but confirmation failed".

> The component that failed most often was not the weakest link. Payments failed 69 times and every failure was visible; the DB pool failed 19 times and the monitoring saw none of them, including one paid order that was lost.

**How I would make it more resilient:** size the pool to the threadpool (or use a blocking pool with a short `getconn` timeout and a JSON 503), stop `reserve` from holding two connections at once (it calls `_get_available` while already holding one), make the gateway parse error bodies defensively, and record the metric in a `try/finally` so a crashed handler is still counted as a 500.

---

## Bonus task. Resilience improvement: decouple readiness from transitive dependencies

**Weakness chosen.** Experiment 3: a Redis outage caused a 73 s total outage of a product whose read path does not use Redis. This had the largest blast radius of everything tested (100 % of traffic, every service), and it is purely a configuration problem.

**Fix.** Readiness for gateway and events now uses `/metrics`, which answers as long as the process can serve HTTP. `/health` keeps its dependency checks and is still used by dashboards and alerts; it just no longer decides whether Kubernetes routes traffic to the pod.

```diff
--- a/k8s/events.yaml
+++ b/k8s/events.yaml
-          # Readiness asks "can it do its job": /health checks the dependencies.
+          # Readiness is shallow on purpose (lab 8 bonus). /health fails when Redis is down,
+          # which pulled the only events pod out of its Service and broke GET /events too,
+          # although listing needs only Postgres. /health stays for dashboards and alerts.
           readinessProbe:
             httpGet:
-              path: /health
+              path: /metrics
               port: 8081
--- a/k8s/gateway.yaml
+++ b/k8s/gateway.yaml   (the Lab 7 Rollout)
-          # Readiness asks "can it do its job": /health checks events and payments.
-          # It also keeps a canary with a broken dependency out of the Service.
+          # Readiness is shallow on purpose (lab 8 bonus). /health returns 503 when events
+          # reports any dependency down, so a Redis outage took all 5 replicas out of the
+          # Service at once although the gateway could still serve reads. A canary with a
+          # broken dependency is now caught by the error-rate analysis, not by readiness.
           readinessProbe:
             httpGet:
-              path: /health
+              path: /metrics
               port: 8080
```

The same change was applied to the running cluster with `kubectl patch`. For gateway this went through the Lab 7 canary (20 %, pause, AnalysisTemplate on the error rate, 50 %, pause, 100 %); the analysis passed and the Rollout reached Healthy at 16:37:20. The course repository has no `k8s/` directory, so both manifests appear in this PR as whole files: `k8s/gateway.yaml` is the Lab 7 Rollout and `k8s/events.yaml` is the events Deployment from my GitOps setup, each with only the readiness change above, so the files match what runs in the cluster.

**Re-run.** The experiment 3 script was run again unchanged (same probe, same sampling, same lab curl checks), with Redis at 0 replicas from 16:38:28 to 16:40:13.

```plaintext
gateway_ready_eps during the outage:   35 samples, all = 5      (before: 23 samples = 0)
lab check at +70 s:
  GET /events:   200 0.046177s
  POST /reserve: {"detail":"Events service timeout"} 504 5.005277s
  GET /health:   {"status":"degraded","checks":{"events":"down",...}} 503
Prometheus at +75 s:  rps /events 200 = 5.58   rps /reserve 504 = 0.51   rps /events 502 = 0.02
```

**Before vs after (client-side probe, only while Redis was at 0 replicas):**

| Metric | Before (readiness `/health`) | After (readiness `/metrics`) |
|---|---|---|
| Longest run with no successful `GET /events` | 64 s inside the window (73 s until the first success) | 0 s |
| Seconds with at least one successful `GET /events` | 23 of 87 | 105 of 105 |
| `GET /events` success | 106 of 133 (the rest refused or 502) | 490 of 493 (3 x 502) |
| Gateway ready endpoints | 0 for 62 s | 5 the whole time |
| First successful checkout after Redis returns | 8.5 s (16:25:52.5 to 16:26:01) | 3 s (16:40:13.0 to 16:40:16) |
| Reserve | refused at the Service | 500 x 35, 504 x 15 |
| `/health` | unreachable | reachable, honestly reports `degraded` |

The outage is now limited to the feature that needs Redis. The remaining three `/events` errors are the DB pool leak from experiment 3 (reserves holding connections while Redis times out), which this fix deliberately does not address. The 35 reserve 500s are the gateway `JSONDecodeError` from Task 2, and Prometheus again did not count them; it only shows the 504s.

**Trade-off.** Kubernetes no longer takes a pod out of rotation when its dependencies are broken, so a replica that has truly lost its database keeps receiving traffic and answers with errors instead of being bypassed; that is acceptable here because every replica shares the same dependencies, but it moves the job of detecting dependency failure from the kubelet to alerting on `/health` and error rates.

---

## Results

| Experiment | Hypothesis | Impact observed | Visible in gateway metrics |
|---|---|---|---|
| E1 pod kill | held | 0 failed requests, replacement Ready in 8.5 s | yes (nothing to see) |
| E2 latency 2000 ms | held | 0 % 5xx, checkout throughput down 82 % | latency only |
| E2 latency 6000 ms | held | 100 % `/pay` 504 at 5.01 s, 48 charges with 0 orders | 7.6 % 5xx, fake p99 7.475 s |
| E3 Redis down | held, cascade confirmed | 73 s total outage incl. reads | no: ratio 1.0 came from kubelet probes |
| T2 combined | n/a | `/pay` p99 x8, 33 % payment errors, 19 pool errors | pool errors invisible |
| Bonus | n/a | read outage 73 s to 0 s, checkout back 8.5 s to 3 s after Redis | yes |

The common thread is that the failures with the largest consequences were the ones the gateway's own metrics could not see. A pod kill, which looks dramatic, was absorbed completely. Slow payments, a Redis outage and a small DB pool each did real damage (lost money, a full outage, lost orders), and in each case the server-side picture was diluted, interpolated or empty: an error ratio averaged over healthy reads, a quantile inside an empty bucket, a 1.0 made of probe traffic, and handler crashes that never reached the counter. The fix in the bonus task removes the worst amplifier, the readiness cascade. The client-side probe was the instrument that made all of these findings measurable, and it is the first thing I would keep from this lab as a permanent SLI.
