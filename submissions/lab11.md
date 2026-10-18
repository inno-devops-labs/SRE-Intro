# Lab 11 — Advanced Microservice Patterns

## Environment and Baseline

This lab continues `fork/feature/lab10` (`fork` points to GeorgyPyanov/SRE-Intro; `origin` is the course repository). The working tree initially contained only the user's untracked `.idea/`. Docker Desktop was stopped. I started it and reused the existing `quickticket` cluster without recreating storage.

```text
2026-10-18T21:32:31.8155982Z
Docker Desktop 4.61.0; Docker client/server 29.2.1
Windows PowerShell; kubectl v1.34.1; k3d v5.9.0
k3d-quickticket-server-0 Ready, k3s v1.35.5+k3s1
Argo CD CLI v3.5.3+c9c369e; Argo Rollouts Windows plugin v1.10.1+356117c
gateway desired=5 current=5 updated=5 available=5
postgres-data    Bound 1Gi RWO local-path
postgres-backups Bound 1Gi RWO local-path
quickticket-backup schedule=*/5 * * * * suspend=False
quickticket-backup-29872645 Complete 1/1
quickticket-backup-29872650 Complete 1/1
gateway health HTTP 200: events=ok, payments=ok, circuit_payments=CLOSED
events health HTTP 200: postgres=ok, redis=ok
payments health HTTP 200: failure_rate=0.0, latency_ms=0
```

The original gateway image was `ghcr.io/georgypyanov/quickticket-gateway:fd2b50100dbee5286b1a21397025d70fe50e85ae`, imageID `sha256:6f562023fe7d7d28901ac30871c49eee2f06909bc23e28ae14456c79f10c0976`. Argo CD initially tracked `feature/lab10`, `Synced/Healthy`, with `automated: {prune: true, selfHeal: true}`. I saved that policy and disabled automation during injections.

Baseline checkout at `2026-10-18T21:34:38.526669Z`:

```json
{"reserve_http":200,"reservation_id":"73095391-ec71-4e9e-af50-271579684221","event_id":3}
{"pay_http":200,"order_id":"73095391-ec71-4e9e-af50-271579684221","status":"confirmed"}
```

Initial available tickets: event 3 = 498; event 5 = 78. I increased only the test inventory of events 3 and 5 to 10,000, preserving existing orders. Event 1 already had 10,000 total for mixedload. No database or Redis flush was used. The local Lab 11 specification was compared with `origin/main`; the difference was submission-section placement, not the pattern contracts.

## Notifications Service

The new [service](../app/notifications/main.py) exposes `/notify`, `/health`, and `/metrics` on 8083. It uses the same three pinned dependencies as payments and runs as user `app` (`kubectl exec deployment/notifications -- whoami` returned `app`). Notification IDs appear in logs and responses, never metric labels.

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

The [Deployment and Service](../k8s/notifications.yaml) have one replica, `app=notifications`, `/health` probes, 50m/64Mi requests and 200m/256Mi limits. Defaults are `NOTIFY_FAILURE_RATE=0.0`, `NOTIFY_LATENCY_MS=0`. The gateway starts `asyncio.create_task(_notify_order_confirmed(...))` only after Events returns a successful confirmation. Background tasks are retained until completion, and the helper calls `raise_for_status()` so injected HTTP 500 errors are logged too.

```yaml
replicas: 1
# Pod template/container excerpt; full Deployment + Service is linked above.
image: quickticket-notifications:lab11
imagePullPolicy: Never
ports:
  - containerPort: 8083
env:
  - name: NOTIFY_FAILURE_RATE
    value: "0.0"
  - name: NOTIFY_LATENCY_MS
    value: "0"
```

```python
response = await client.post(f"{NOTIFICATIONS_URL}/notify",
    json={"event": "order_confirmed", "order_id": reservation_id}, timeout=2.0)
response.raise_for_status()
```

`monitoring/lab11/prometheus.yaml` updates the existing in-cluster Prometheus ConfigMap. It preserves gateway pod discovery and `rs_hash`, and adds the notifications target. `promtool check config /etc/prometheus/prometheus.yml` returned `SUCCESS`. It does not replace the separate kube-prometheus-stack.

Prometheus query results at `2026-10-18T21:53:20.758959Z–21:53:20.774595Z` showed all three metric families (the current healthy pod was recreated after fault injection):

```text
notifications_requests_total{method="POST",path="/notify",status="200"} = 282
notifications_request_duration_seconds_count{method="POST",path="/notify"} = 282
notifications_notify_total{result="success"} = 282
notifications_notify_total{result="failed"} = 0
```

The injected pod's earlier failure counters are preserved separately in the test evidence below; the healthy pod's zero failed counter does not erase them.

## Retry Implementation

The [gateway implementation](../app/gateway/main.py) treats `RETRY_MAX=3` as three total attempts. It retries timeouts, connection errors, and HTTP 5xx/408/429. Other HTTP errors and unknown exceptions are immediately re-raised. Cancellation propagates without retry. Counters distinguish `retried`, `succeeded_after_retry`, `exhausted`, and `non_retryable`.

```python
base = RETRY_BASE_DELAY_MS / 1000
delay = base * 2 ** attempt + random.uniform(0, base)
RETRY_TOTAL.labels(target, "retried").inc()
await asyncio.sleep(delay)
```

The circuit uses monotonic time, one HALF_OPEN probe, and a generation number to prevent a stale concurrent success from closing a newer OPEN circuit. State updates contain no awaits; downstream I/O runs concurrently without a global lock. The limiter uses normalized paths and a one-second sliding window. Rejected requests return 429 with `Retry-After: 1`.

Key admission/state-machine excerpts from the implementation:

```python
# CircuitBreaker.call: admit a single probe after cooldown.
if self.state == self.OPEN:
    if self._clock() - self.opened_at < self.cooldown:
        raise CircuitOpenError(f"circuit[{self.name}] OPEN")
    self._transition(self.HALF_OPEN)
elif self.state == self.HALF_OPEN:
    raise CircuitOpenError(f"circuit[{self.name}] HALF_OPEN probe in progress")

# RateLimiter.allow: expired/rejected timestamps do not consume capacity.
now = self._clock()
q = self.hits[_normalize_path(key)]
while q and q[0] <= now - self.window_s:
    q.popleft()
if len(q) >= self.rps:
    return False
q.append(now)
return True
```

The bulkhead holds a semaphore slot around the entire operation and releases it in `finally`, including cancellation. Acquisition timeouts increment a separate rejection metric and return 503 with `error=bulkhead_full`. The explicit `BULKHEAD_ENABLED` toggle supports the controlled comparison.

```python
try:
    await asyncio.wait_for(self.semaphore.acquire(), timeout=self.timeout)
except asyncio.TimeoutError as exc:
    BULKHEAD_REJECTIONS.labels(self.name).inc()
    raise BulkheadFullError(f"bulkhead[{self.name}] full") from exc
BULKHEAD_IN_FLIGHT.labels(self.name).inc()
try:
    return await func()
finally:
    BULKHEAD_IN_FLIGHT.labels(self.name).dec()
    self.semaphore.release()
```

```python
await payments_bulkhead.call(
    lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments")))
```

Twelve automated tests passed in 0.575 seconds using Python 3.13.16 inside the gateway image:

```powershell
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 --mount "type=bind,source=$PWD,target=/repo,readonly" -w /repo quickticket-gateway:lab11 python -m unittest discover -s tests -p test_lab11_patterns.py -v
```

They cover retry classification/exhaustion, original exceptions, cancellation, circuit cooldown/single probe/stale results, limiter expiry and UUID normalization, bulkhead capacity/timeouts/exception and cancellation release, and notification HTTP errors.

## Deployment and Reproduction

I used local build/import for the two changed services, with matching manifests and `imagePullPolicy: Never`. Events/payments retain their existing GHCR images. No CI run is claimed: the existing workflow does not run on this branch.

```powershell
docker build -t quickticket-gateway:lab11 app/gateway
docker build -t quickticket-notifications:lab11 app/notifications
k3d image import quickticket-gateway:lab11 quickticket-notifications:lab11 -c quickticket
kubectl apply -f monitoring/lab11/prometheus.yaml
kubectl rollout restart deployment/prometheus -n monitoring
kubectl create configmap lab11-probe --from-file=scripts/lab11/probe.py --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f scripts/lab11/runner.yaml
kubectl apply -f labs/lab8/mixedload.yaml
```

The branch was pushed before Argo CD was switched to `feature/lab11`. Argo CD applied the tracked raw manifests; the gateway retained its five-replica Rollout and existing multi-step AnalysisTemplate gate. Both manifests passed `kubectl apply --dry-run=client`.

```text
Healthy observed: 2026-10-18T21:41:29.8099717Z
AnalysisRun gateway-757ffbcdb5-10-2: Successful
measurements: 21:37:35Z [0], 21:37:55Z [0], 21:38:15Z [0]
gateway imageID: sha256:8ecf3ff6f535ca0ef75a9274d4c9527fee3eb3516a83df77cc37e36a0f3b5801
notifications imageID: sha256:10adfa869af3b243a74ed9dc0e551181b87691bb04a9f63656817b5347b081b7
```

The local source and running container `/app/main.py` SHA256 values matched:

```text
gateway:       3662ed9c8692d5863f4bd3e6d12402554f1edd327c9a198986e4e8e91c0855fd
notifications: 651a2d2b182992228a6139cc494c3f7e2c3abf175bd72f415e30eda659fcf165
```

## Fire-and-Forget Test

Commands (run from the repository root):

```powershell
kubectl exec lab11-probe -- python /probe/probe.py checkout --count 30 --gap 0.2
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
kubectl rollout status deployment/notifications --timeout=60s
kubectl exec lab11-probe -- python /probe/probe.py checkout --count 30 --gap 0.2
```

Both baseline and injected tests used client nearest-rank quantiles over 30 `/pay` samples. The window includes the two-second final wait for background notifications. All reserve responses were 200; every pay 200 contained `status=confirmed`.

| Run | UTC window on 2026-10-18 | Reserve 200 | Pay 200 | Pay p50 / p95 / p99 (ms) |
|---|---|---:|---:|---|
| Initial baseline | 21:41:30.152328–21:41:39.178476 | 30 | 30 | 18.046 / 38.449 / 68.397 |
| Initial notify 0.3 / 300 ms | 21:42:15.875488–21:42:25.387822 | 30 | 30 | 22.101 / 75.171 / 164.072 |
| Repeat baseline, inventory repaired | 21:42:57.396058–21:43:06.702205 | 30 | 30 | 20.232 / 98.124 / 106.477 |
| Repeat notify 0.3 / 300 ms | 21:43:31.741397–21:43:40.792304 | 30 | 30 | 17.783 / 28.152 / 32.964 |

The first injected client p99 missed the 100 ms target. I retained that result. Investigation found that mixedload's event 1 had 8,253 confirmed tickets plus 1,747 held against 10,000 total, so background checkout had stopped. I increased event 1 total to 100,000 without deleting orders or holds, then repeated baseline and injection. The repeated baseline itself exceeded 100 ms, showing short-window variability; CPU samples of 11–32m per gateway and 48m Events did not establish CPU saturation. The exact cause of the first outlier remains unproven. The successful repeat met the target, and there is no evidence that notifications improved latency.

The repeated injected health response was actually read after rollout settled:

```json
{"status":"healthy","failure_rate":0.3,"latency_ms":300}
```

An earlier health probe during the first notification replacement briefly got a connection error. Gateway health still returned 200; this dependency is non-critical. No fault-injected checkout failed in either run.

Raw destination counters for the repeated measurement:

```text
21:43:31.779948Z before:
notifications_notify_total{result="success"} 11.0
notifications_notify_total{result="failed"} 10.0
21:43:40.786032Z after:
notifications_notify_total{result="success"} 67.0
notifications_notify_total{result="failed"} 33.0
delta: success=56 failed=23, failure fraction=23/79=29.11%
```

That counter window includes mixedload as well as the 30 explicit checkouts. The initial isolated counter window was success=20, failed=10 (33.33%). Counters were read after the probe waited two seconds; injected notification delay was 300 ms.

```text
2026-10-18T21:43:37.413903530Z gateway-757ffbcdb5-sjs8m: notify failed (non-critical) order=6327024f-1323-48ce-b3ca-652b114fd543 err=Server error '500 Internal Server Error' for url 'http://notifications:8083/notify'
2026-10-18T21:43:38.422966363Z gateway-757ffbcdb5-whrtd: notify failed (non-critical) order=bf718699-633f-4117-a0e3-0c4fa09d51eb err=Server error '500 Internal Server Error' for url 'http://notifications:8083/notify'
```

Prometheus at `21:43:41.155753Z` returned `/reserve/{id}/pay` p99 `0.09318752174045371` seconds from the two-minute histogram query. This is a wider, bucket-estimated window, not the 30-request client p99. Notifications was restored to 0.0/0 in a `finally` block starting at `21:43:41.6356238Z`.

## Payment Retry Test

Mixedload was scaled to zero and its pods were gone before this isolated test. At `2026-10-18T21:44:23.1644507Z`, payments was configured to failure rate 0.3 and latency 0; its health response confirmed those values. The probe used the gateway Service for requests and all five direct pod addresses for before/after counters.

```powershell
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=0
kubectl rollout status deployment/payments --timeout=60s
$urls = @((kubectl get pods -l app=gateway -o json | ConvertFrom-Json).items |
  Where-Object { $_.status.containerStatuses[0].ready } |
  ForEach-Object { 'http://' + $_.status.podIP + ':8080' })
kubectl exec lab11-probe -- python /probe/probe.py checkout --count 30 --gap 0.2 --targets http://gateway:8080 @urls
```

```text
window=2026-10-18T21:44:37.006929Z–21:44:47.900117Z
reserve: count=30 statuses={200:30}
pay: count=30 statuses={200:28,500:2}
pay p50=19.521 ms p95=378.316 ms p99=440.600 ms
payments charges delta: success=28 failed=13
gateway retry delta: retried=11 succeeded_after_retry=6 exhausted=2
```

The request connection stayed on pod `gateway-757ffbcdb5-8cf7p` (`10.42.0.128`). Its retry series were absent before the test, then had the values above; the other four pod snapshots had no retry increments. The Service snapshot is not added again to the per-pod total. This is 41 downstream attempts for 30 logical payments, not 41 checkouts.

Prometheus confirmed the same actual values at `21:44:54.265695Z`:

```json
{"query":"sum by(target,result) (gateway_retry_total)","result":[
 {"target":"payments","result":"retried","value":11},
 {"target":"payments","result":"succeeded_after_retry","value":6},
 {"target":"payments","result":"exhausted","value":2}]}
```

The random run did not achieve 30/30: two operations exhausted all three attempts. There were no reserve failures, 409, 429, circuit-open, bulkhead-full, or transport errors. Payments recovery to 0.0/0 started in `finally` at `21:44:54.3037505Z`.

A separately labelled validation repeat used the same 0.3/0 injection at `21:49:42.5580739Z`. In `21:49:54.471234Z–21:50:05.657504Z`, all 30 reservations and 30 payments succeeded; pay p99 was 470.459 ms. Per-pod deltas were `retried=13`, `succeeded_after_retry=11`, `exhausted=0`, with 30 successful and 13 failed downstream attempts. Both runs are retained: random fault injection does not guarantee a fixed success count. Recovery to 0.0/0 started at `21:50:05.7099695Z`.

## Circuit Breaker Failure and Recovery

At `2026-10-18T21:45:18.647307Z`, payments was set to 1.0 failure and zero latency. After its rollout, the probe ran 16 checkout chains against each of the five explicit gateway pod IPs concurrently (80 total). This avoids assuming a Service connection distributes evenly.

```powershell
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0 PAYMENT_LATENCY_MS=0
kubectl rollout status deployment/payments --timeout=60s
kubectl exec lab11-probe -- python /probe/probe.py circuit --targets @urls --count 16 --gap 0.2
```

```text
window=2026-10-18T21:45:31.172714Z–21:45:39.071310Z
reserve: 80 HTTP 200
pay: 24 HTTP 500 (Payment failed), 56 HTTP 503 (circuit open)
payments_charges_total{result="failed"}: delta=72
peak client pay concurrency=5
```

There were no 409, 429, bulkhead-full or transport errors. The 24 exhausted operations account for exactly 72 downstream charge attempts. The other 56 logical payments fast-failed instead of producing another 168 potential charge attempts. One pod needed only four new exhausted operations because the last request in the preceding retry test was a 500, leaving one existing circuit failure. I did not reset process state or assume all five breakers started with zero failures.

| UTC timestamp, 2026-10-18 | Gateway pod suffix (hash `757ffbcdb5`) | Transition |
|---|---|---|
| 21:45:33.788827207 | 8cf7p | CLOSED → OPEN |
| 21:45:34.253376796 | bdwjg | CLOSED → OPEN |
| 21:45:34.446687141 | gkqjl | CLOSED → OPEN |
| 21:45:34.475530996 | sjs8m | CLOSED → OPEN |
| 21:45:34.383544082 | whrtd | CLOSED → OPEN |

Example raw request at `21:45:34.005676Z`:

```json
{"target":"http://10.42.0.128:8080","path":"/reserve/ceae6c20-cac0-495f-af7a-83ecf045d22c/pay","status":503,"duration_ms":3.45,"body":{"detail":"Payment service temporarily unavailable (circuit open)"}}
```

Payments was restored to 0.0/0 starting at `21:45:39.8453968Z`. After more than the unchanged 30-second cooldown, three checkouts per pod produced 15/15 reserve and confirmed pay 200s in `21:46:30.688317Z–21:46:34.205375Z`.

```text
2026-10-18T21:46:30.971360399Z gateway-757ffbcdb5-8cf7p circuit[payments] OPEN -> HALF_OPEN
2026-10-18T21:46:30.980161891Z gateway-757ffbcdb5-8cf7p circuit[payments] HALF_OPEN -> CLOSED
2026-10-18T21:46:30.946105491Z gateway-757ffbcdb5-bdwjg circuit[payments] OPEN -> HALF_OPEN
2026-10-18T21:46:30.953400482Z gateway-757ffbcdb5-bdwjg circuit[payments] HALF_OPEN -> CLOSED
2026-10-18T21:46:30.972014940Z gateway-757ffbcdb5-gkqjl circuit[payments] OPEN -> HALF_OPEN
2026-10-18T21:46:30.980436431Z gateway-757ffbcdb5-gkqjl circuit[payments] HALF_OPEN -> CLOSED
2026-10-18T21:46:30.971162475Z gateway-757ffbcdb5-sjs8m circuit[payments] OPEN -> HALF_OPEN
2026-10-18T21:46:30.979706073Z gateway-757ffbcdb5-sjs8m circuit[payments] HALF_OPEN -> CLOSED
2026-10-18T21:46:30.945463105Z gateway-757ffbcdb5-whrtd circuit[payments] OPEN -> HALF_OPEN
2026-10-18T21:46:30.952725915Z gateway-757ffbcdb5-whrtd circuit[payments] HALF_OPEN -> CLOSED
```

Prometheus `sum by(to) (gateway_circuit_breaker_transitions_total)` at `21:46:43.705441Z` returned `OPEN=5`, `HALF_OPEN=5`, `CLOSED=5`.

## Rate Limiter Burst and Below-Limit Tests

This was explicitly a **per-pod** test against `gateway-757ffbcdb5-8cf7p` (`10.42.0.128:8080`), with healthy dependencies and the unchanged default `RATE_LIMIT_RPS=10`.

```powershell
kubectl exec lab11-probe -- python /probe/probe.py rate --targets http://10.42.0.128:8080 --count 100
```

The 100 concurrent `/events` requests completed in `0.589645055` seconds, ending at `2026-10-18T21:46:35.889013Z`: 10 returned 200 and 90 returned 429. After waiting 1.2 seconds, a separate 20-request lane with 200 ms gaps returned 20/20 HTTP 200 and no 429. The full probe ended at `21:46:43.371426Z`.

```json
{"timestamp":"2026-10-18T21:46:35.389923Z","status":429,"retry_after":"1","body":{"error":"rate_limited","path":"/events","limit_rps":10}}
```

The direct rejection counter grew from no previous series to `gateway_rate_limit_rejections_total{path="/events"} 90.0`. Prometheus confirmed 90 at `21:46:43.708216Z`.

The concurrent exempt requests returned:

```text
2026-10-18T21:46:35.789452Z GET /metrics HTTP 200 duration=457.084 ms
2026-10-18T21:46:35.888907Z GET /health  HTTP 200 duration=556.798 ms
```

They stayed available under burst but were not instant. The observed 10/90 split is for this one process, not a claim about an exact five-pod cluster limit. Automated tests also verified that different reservation UUIDs share the normalized endpoint key and that expired timestamps permit new requests.

## Bulkhead Before/After

This is an explicit **single-process isolation test**. The temporary Deployment in `scripts/lab11/isolation.yaml` uses the exact same gateway image/configuration defaults and 50m/64Mi requests, 200m/256Mi limits as the five-pod Rollout. It has a separate `app=lab11-isolation` label, is not selected by the user gateway Service, and is deleted afterwards. Only `BULKHEAD_ENABLED=false` versus `true` changes between fixture runs; `RATE_LIMIT_RPS=1000`, CB defaults, retry defaults, resources, downstream URLs, and request pattern stay identical. This avoids distributing 30 pay calls into only six per process across five pods.

Each run pre-created 30 reservations sequentially, then launched 30 concurrent `/pay` requests against the selected fixture pod. A separate HTTP client made 40 `/events` calls, while direct metric samples recorded occupancy approximately every 150 ms. Payments had failure rate 0 and latency 3000 ms in both runs; it was restored in `finally` after each run.

```powershell
kubectl apply -f scripts/lab11/isolation.yaml
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=3000
kubectl rollout status deployment/payments --timeout=60s
kubectl exec lab11-probe -- python /probe/probe.py bulkhead --targets $fixturePodUrl --count 30
# Restore payments, then change only the fixture toggle and repeat injection/probe.
kubectl set env deployment/lab11-isolation BULKHEAD_ENABLED=true
kubectl rollout status deployment/lab11-isolation --timeout=60s
```

| Measurement | Before (disabled) | After (enabled) |
|---|---|---|
| UTC window, 2026-10-18 | 21:47:43.221099–21:47:53.484324 | 21:48:44.138461–21:48:52.745187 |
| Pod | lab11-isolation-cf7dfdfc5-kwsvc | lab11-isolation-757999548-qlswl |
| Pod IP | 10.42.0.139 | 10.42.0.142 |
| Prepared reservations | 30 HTTP 200 | 30 HTTP 200 |
| Peak client /pay in-flight | 30 | 30 |
| Pay outcomes | 25 confirmed 200; 5 confirmation-failed 500 | 10 confirmed 200; 20 bulkhead-full 503 |
| Downstream successful charges | 30 | 10 |
| Pay p50 / p95 / p99 | 4513.308 / 4894.302 / 4894.805 ms | 609.768 / 3402.715 / 3402.774 ms |
| Events outcomes | 40 HTTP 200 | 40 HTTP 200 |
| Events p50 / p95 / p99 | 13.751 / 381.843 / 693.357 ms | 9.802 / 96.547 / 107.939 ms |
| `EVENTS: ok=X slow=Y` (500 ms boundary) | ok=39 slow=1 | ok=40 slow=0 |
| Bulkhead rejection delta | 0 | 20 |
| Direct peak occupancy | 0 (disabled instrumentation) | 10 |
| Final occupancy | 0 | 0 |

The before-run 500s were **partial failures after successful payment**, not injected payment failures. Actual Events logs contained:

```text
2026-10-18T21:47:49.888339406Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-18T21:47:49.895795898Z psycopg2.pool.PoolError: connection pool exhausted
```

The after-run response distinguishes admission rejection:

```json
{"timestamp":"2026-10-18T21:48:46.623697Z","status":503,"duration_ms":597.238,"body":{"error":"bulkhead_full","message":"Payment concurrency capacity is busy; try again.","reservation_id":"0886af84-9f19-427e-a674-c92bb9bab323"}}
```

The configured semaphore wait timeout is 500 ms, but this real client request took 597.238 ms including scheduling and transport. I do not call it an exact 500 ms end-to-end limit.

```text
2026-10-18T21:48:46.217498Z direct metric sample:
gateway_bulkhead_in_flight{target="payments"} 10.0
2026-10-18T21:48:52.732747Z after sample:
gateway_bulkhead_rejections_total{target="payments"} 20.0
gateway_bulkhead_in_flight{target="payments"} 0.0
```

The fixture-only Prometheus job uses one-second scrapes so the short peak is observable without changing the main gateway's five-second scrape interval. At `21:48:53.120113Z`, `max_over_time(gateway_bulkhead_in_flight{job="lab11-isolation"}[2m])` returned 0 for the disabled pod and 10 for the enabled pod. At `21:48:53.134834Z`, its rejection counter was 20; target `up` was 1. These are measurements from the explicit fixture, not a claim that all five production pods saturated at once.

In this comparison, shedding reduced pressure and read-tail latency, but one short test cannot establish a universal latency gain. The observed before-run DB pool errors are evidence of shared downstream pressure. Asynchronous sleep alone does not block the event loop. End-of-run CPU snapshots were 64m/37Mi for the before fixture and 45m/37Mi after; these samples do not prove CPU saturation or a sustained CPU profile. The trade-off is that 20 customers receive an explicit retryable busy response rather than entering the full payment/confirmation path. Payments recovery started at `21:48:53.3722830Z`, and the fixture was removed afterwards.

## Metrics and Limitations

All experiment probes run inside Kubernetes. `scripts/lab11/probe.py` prints per-request UTC timestamps, status, body, latency and relevant counter snapshots. Raw temporary JSONL stays outside Git. Direct per-pod samples avoid combining unrelated replicas or resets when calculating counter deltas. Reserve errors are counted separately from payment outcomes. Client timings use nearest-rank quantiles in milliseconds; Prometheus histogram quantiles are bucket estimates in seconds.

The committed [compact evidence](evidence/lab11/results.json) contains 13 run summaries, representative actual requests, before/after counters, occupancy peaks, and final health/checkout responses. It was mechanically generated from saved JSONL with [summarize.py](../scripts/lab11/summarize.py); it supports both PowerShell UTF-16 and UTF-8 output. Service-address metric snapshots are illustrative only because the Service can select different pods; all counter-delta conclusions above use fixed pod IPs or single-replica dependencies.

CB, rate limiter and bulkhead are process-local. Five replicas do not give an exact shared 50 RPS admission budget, and a persistent HTTP connection can remain attached to one pod. Gateway metrics cannot count requests that never reach the application. In-memory background notifications may be lost on pod death; production delivery would require a durable queue/outbox.

## Design Answers

Notifications are best-effort side effects, so checkout does not wait for email latency or fail because email delivery failed. A durable queue is needed if delivery must survive crashes.

Retry inside the circuit makes one exhausted logical payment count as one circuit failure. Reversing the composition counts individual attempts instead; this implementation classifies `CircuitOpenError` as non-retryable, so it does not repeatedly retry that fast-fail. Retrying an ambiguous charge timeout can duplicate a payment: this mock service does not prove production exactly-once semantics.

The bulkhead holds one slot across all retries and backoff. With the required outer bulkhead composition, even an OPEN circuit briefly acquires a slot, and a queued request can wait before checking the circuit. This is an explicit trade-off, not a universally optimal ordering.

Rate limiting controls admissions per unit time; the bulkhead controls simultaneous in-flight operations for one dependency. A low RPS can still exhaust concurrency if operations are very slow. These mechanisms protect different resources.

## Final Recovery

Argo CD's original automation was restored after the injections. At `2026-10-18T21:52:30.2547280Z`:

```text
targetRevision=feature/lab11
revision=19f4d603d3e525724af902a379844f5708c8fbb8
sync=Synced health=Healthy
syncPolicy.automated={prune:true,selfHeal:true}
gateway desired=5 current=5 updated=5 ready=5 available=5
notifications 1/1 Ready
```

Actual health responses at `21:52:31.702791Z–21:52:31.786609Z`:

```json
{"gateway_all_five_http":200,"body":{"status":"healthy","checks":{"events":"ok","payments":"ok","notifications":"ok","circuit_payments":"CLOSED"}}}
{"events_http":200,"body":{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}}
{"payments_http":200,"body":{"status":"healthy","failure_rate":0.0,"latency_ms":0}}
{"notifications_http":200,"body":{"status":"healthy","failure_rate":0.0,"latency_ms":0}}
```

One last actual checkout:

```text
2026-10-18T21:52:34.212170Z reserve HTTP 200
reservation_id=53fb2bc8-9586-4c62-a4cf-11662c94e3e1 event_id=3 quantity=1
2026-10-18T21:52:34.230862Z pay HTTP 200
order_id=53fb2bc8-9586-4c62-a4cf-11662c94e3e1 status=confirmed duration=18.587 ms
2026-10-18T21:52:34.230990466Z notifications log:
notification sent event=order_confirmed order=53fb2bc8-9586-4c62-a4cf-11662c94e3e1
```

At `21:52:37.537681Z`, Prometheus returned `up=1` for all five gateway targets and notifications. All five `gateway_bulkhead_in_flight` values were 0 at `21:52:37.552508Z`. `/health` reported every circuit CLOSED. The fixture's rejection series becomes stale after fixture deletion, so its historical 20-rejection proof is the earlier captured result, not the final empty vector. Quiet-window histogram `NaN` results are not presented as zero latency.

Actual running configuration from `printenv`:

```text
RATE_LIMIT_RPS=10 RETRY_MAX=3 RETRY_BASE_DELAY_MS=100
CB_FAILURE_THRESHOLD=5 CB_COOLDOWN_S=30
BULKHEAD_ENABLED=true BULKHEAD_PAYMENTS_MAX=10 BULKHEAD_PAYMENTS_TIMEOUT_S=0.5
EVENTS_URL=http://events:8081 PAYMENTS_URL=http://payments:8082
NOTIFICATIONS_URL=http://notifications:8083
```

PostgreSQL and backup PVCs remain Bound. `quickticket-backup` remains enabled (`suspend=False`, `*/5 * * * *`) and Jobs `29872660`, `29872665`, and `29872670` were Complete 1/1, each in four seconds. Redis, Events, Payments and PostgreSQL are 1/1 Running. The test fixture and mixedload were deleted; the probe Pod and its ConfigMap were removed only after final evidence was captured. No port-forward was used or created. Monitoring and application data were preserved.

```text
cleanup_verified_utc=2026-10-18T21:53:52.1135765Z
kubectl get pods -l 'app in (mixedload,loadgen,lab11-probe,lab11-isolation)'
No resources found in default namespace.
Argo CD quickticket: Synced Healthy
gateway: desired=5 current=5 up-to-date=5 available=5
```

## Acceptance Checklist

- [x] Notifications, retry, circuit breaker, limiter and bulkhead implemented.
- [x] Automated tests pass.
- [x] Task 1: notifications and retry Kubernetes experiments completed; original and repeat outcomes retained.
- [x] Task 2: circuit failure/recovery on all five pods and per-pod rate limiter experiments completed.
- [x] Bonus: comparable bulkhead before/after, separate reader, cap=10, rejection delta=20, no slot leak.
- [x] Final GitOps, health, checkout, notification and cleanup verified.

The first notify client p99 missed 100 ms and the first retry run lost two checkouts; both limitations remain visible. The separately labelled repeats met the requested targets. Local image tags require rebuilding/importing these exact sources on another cluster; this lab does not claim public GHCR publication or a new CI run.
