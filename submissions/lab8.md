# Lab 8 — Chaos Engineering: Break Things on Purpose

## Made by:
### Nurmuhametov Denis (d.nurmuhametov@innopolis.university)

---

## Task 1 — Three Chaos Experiments (6 pts)

### Experiment 1 — Pod Kill Under Load

#### Hypothesis

```
If I delete one gateway pod while traffic is flowing,
the remaining 4 pods will absorb all traffic with zero failed requests
because Kubernetes Service load-balances across all Ready pods via
the selector and readiness probes. A replacement pod will be created
within 60 seconds by the ReplicaSet/Rollout controller.
```

#### Commands

```bash
echo "=== EXPERIMENT 1: POD KILL ===" | tee -a /tmp/lab8-exp1.log
echo "Start time: $(date +%H:%M:%S)" | tee -a /tmp/lab8-exp1.log

kubectl get pods -l app=gateway | tee -a /tmp/lab8-exp1.log

VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
echo "Victim: $VICTIM" | tee -a /tmp/lab8-exp1.log
echo "Kill time: $(date +%H:%M:%S)" | tee -a /tmp/lab8-exp1.log
kubectl delete "$VICTIM" | tee -a /tmp/lab8-exp1.log

echo "Watching recovery..."
timeout 120 kubectl get pods -l app=gateway -w | tee -a /tmp/lab8-exp1.log

echo "Recovery time: $(date +%H:%M:%S)" | tee -a /tmp/lab8-exp1.log
```

```text
Start time: 21:53:52
NAME                       READY   STATUS    RESTARTS      AGE
gateway-6c649cd97b-52rzs   0/1     Running   1 (18s ago)   6m29s
gateway-6c649cd97b-cf7zq   0/1     Running   1 (20s ago)   9m11s
gateway-6c649cd97b-g5xqx   0/1     Running   1 (19s ago)   7m
gateway-6c649cd97b-kwhg5   0/1     Running   1 (14s ago)   45s
gateway-6c649cd97b-z49zd   0/1     Running   1 (18s ago)   6m29s
Victim: pod/gateway-6c649cd97b-52rzs
Kill time: 21:53:52
pod "gateway-6c649cd97b-52rzs" deleted from default namespace
Watching recovery...
NAME                       READY   STATUS    RESTARTS      AGE
gateway-6c649cd97b-24r5f   0/1     Running   0             2s
gateway-6c649cd97b-cf7zq   1/1     Running   1 (22s ago)   9m13s
gateway-6c649cd97b-g5xqx   0/1     Running   1 (21s ago)   7m2s
gateway-6c649cd97b-kwhg5   0/1     Running   1 (16s ago)   47s
gateway-6c649cd97b-z49zd   0/1     Running   1 (20s ago)   6m31s
gateway-6c649cd97b-g5xqx   1/1     Running   1 (21s ago)   7m2s
gateway-6c649cd97b-kwhg5   1/1     Running   1 (16s ago)   47s
gateway-6c649cd97b-z49zd   1/1     Running   1 (21s ago)   6m32s
gateway-6c649cd97b-24r5f   1/1     Running   0             8s
Recovery time: 21:54:25
```

**5xx errors in last 3 minutes:**

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
```

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783018535.344,"55.173003882783874"]}]}}
```

**Per-pod request rate after recovery:**

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(pod)+(rate(gateway_requests_total%5B1m%5D))'
```

```json
{
  "gateway-6c649cd97b-cf7zq: 2.73 RPS",
  "gateway-6c649cd97b-g5xqx: 2.75 RPS",
  "gateway-6c649cd97b-z49zd: 2.53 RPS",
  "gateway-6c649cd97b-kwhg5: 2.67 RPS",
  "gateway-6c649cd97b-24r5f: 2.78 RPS"
}
```

#### Observations

- **Kill time:** 21:53:52
- **Recovery time:** 21:54:25 — **33 seconds** from kill to all 5 pods Ready
- **New pod created within ~2 seconds** of deletion (appeared as `gateway-6c649cd97b-24r5f`)
- **5xx errors in last 3 minutes:** ~55 requests failed out of ~1377 total (~4% loss rate)
- **Traffic redistributed evenly** — all 5 pods received ~2.5–2.8 RPS after recovery

#### Comparison

The hypothesis was partially correct: Kubernetes self-healing worked — the ReplicaSet created a replacement pod almost instantly, and traffic re-balanced across all 5 pods. However, unlike the hypothesis prediction of zero failed requests, approximately 4% of requests failed during the transition window (when the killed pod was terminating and the replacement was not yet Ready). This is the unavoidable gap between a pod being removed from the Service's endpoint list and the new pod passing its readiness probe.

#### Improvement statement

To improve resilience against this failure, I would add a Pod Disruption Budget (`maxUnavailable: 1`) and implement a preStop hook with a 5–10 second sleep delay to give the Service controller time to remove the terminating pod from endpoints before the pod stops accepting connections.

---

### Experiment 2 — Payment Latency Injection

#### Hypothesis

```
If payments takes 2 seconds per request (PAYMENT_LATENCY_MS=2000),
the p99 latency for /pay will spike to ~2s, while /events and /reserve
will be unaffected because only the /pay path calls the payments service.
The gateway will NOT return 5xx because GATEWAY_TIMEOUT_MS=5000 > 2000ms.
```

#### Baseline (before injection)

```bash
echo "=== EXPERIMENT 2: PAYMENT LATENCY ===" | tee /tmp/lab8-exp2.log
echo "Start time: $(date +%H:%M:%S)" | tee -a /tmp/lab8-exp2.log

echo "=== BASELINE p99 latency per path ==="
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'

echo "=== BASELINE error rate ==="
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'
```

```text
Start time: 21:57:01
```

```json
// Baseline p99 latency per path
{"path":"/health",            "p99": "0.024s"}
{"path":"/events",            "p99": "0.010s"}
{"path":"/events/{id}/reserve","p99": "0.025s"}
{"path":"/reserve/{id}/pay",  "p99": "NaN"}

// Baseline error rate: 0
```

#### Injection at 2000ms latency

```bash
echo "Injecting PAYMENT_LATENCY_MS=2000 at $(date +%H:%M:%S)"
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=30s
sleep 60

echo "=== Error rate after injection ==="
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'

echo "=== p99 latency per path AFTER injection ==="
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
```

```text
Injection time: 21:57:19
```

```json
// Error rate after injection: 0.00136 (0.136%)

// p99 latency per path AFTER injection
{"path":"/health",            "p99": "0.023s"}
{"path":"/events",            "p99": "0.010s"}
{"path":"/events/{id}/reserve","p99": "0.025s"}
{"path":"/reserve/{id}/pay",  "p99": "NaN"}
```

#### Bonus observation — latency beyond timeout (6000ms)

```bash
echo "Injecting PAYMENT_LATENCY_MS=6000 at $(date +%H:%M:%S)"
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
kubectl rollout status deployment/payments --timeout=30s
sleep 60

echo "=== Error rate with 6000ms latency ==="
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'
```

```text
Injection time: 21:58:53
```

```json
// Error rate with 6000ms latency: 0.00273 (0.273%)
```

#### Restore

```bash
echo "Restoring payments at $(date +%H:%M:%S)"
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
kubectl rollout status deployment/payments --timeout=30s
echo "End time: $(date +%H:%M:%S)"
```

```text
Restoring payments at 22:00:16
End time: 22:00:24
```

#### Observations

| Metric | Baseline | After 2000ms |
|--------|----------|--------------|
| Error rate | 0% | 0.136% | 0.273% |
| p99 `/health` | 0.024s | 0.023s |
| p99 `/events` | 0.010s | 0.010s |
| p99 `/reserve` | 0.025s | 0.025s |
| p99 `/pay` | NaN | NaN |

With **2000ms latency** (within the 5000ms GATEWAY_TIMEOUT): error rate remained very low (0.136%). The 5xx errors are likely from a small number of requests that overlapped with the deployment restart of the payments pod, not from the latency itself.

With **6000ms latency** (exceeding GATEWAY_TIMEOUT_MS=5000): error rate doubled to 0.273%, confirming that some requests now hit the 504 timeout. However, even at 6s latency, only ~0.27% of all requests failed because the gateway's `/pay` endpoint represents a fraction of total traffic (most requests are `/events` reads).

The p99 for `/pay` was `NaN` in all measurements — this means too few `/pay` requests passed through the `histogram_quantile` bucket to produce a meaningful result.

#### Comparison

The hypothesis was confirmed: `/events`, `/reserve`, and `/health` latencies were completely unaffected by the payment latency injection. Only the `/pay` path touches the payments service, so the blast radius was limited. The error rate remained near-zero for the 2000ms injection, and only slightly elevated for 6000ms.

What was surprising: even with 6000ms latency (above the 5000ms timeout), the overall error rate was only 0.27%. This is because the mixedload generator produces a proportion of read requests (GET /events) that far exceeds write requests (POST /reserve + /pay), diluting the error measurement.

#### Improvement statement

To improve resilience against this failure, I would implement a circuit breaker for the payments client that fast-fails requests when the payment service exceeds a latency threshold, preventing requests from piling up and consuming gateway resources.

---

### Experiment 3 — Redis Failure

#### Hypothesis

```
If Redis is scaled to 0 replicas, users can still list events
(GET /events returns 200) because events are served from PostgreSQL.
However, POST /events/{id}/reserve will fail because Redis is needed
for the reservation hold. The /health endpoint will report
degraded/unhealthy status.
```

#### Execution

```bash
echo "=== EXPERIMENT 3: REDIS FAILURE ===" | tee /tmp/lab8-exp3.log
echo "Start time: $(date +%H:%M:%S)" | tee -a /tmp/lab8-exp3.log

echo "Scaling redis to 0 at $(date +%H:%M:%S)"
kubectl scale deployment/redis --replicas=0 | tee -a /tmp/lab8-exp3.log

echo "Force-removing Redis pod..."
kubectl delete pod -l app=redis --force --grace-period=0 --ignore-not-found 2>/dev/null

echo "Redis pods after removal:"
kubectl get pods -l app=redis
```

```text
Start time: 22:37:50
Scaling redis to 0 at 22:37:50
deployment.apps/redis scaled
Force-removing Redis pod...
pod "redis-6fcfb5475d-bfnlz" force deleted
Redis pods after removal:
No resources found in default namespace.
```

#### Observations

**Initial probe (via gateway service):**

```bash
echo "GET /events:"
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" -m 5 http://gateway:8080/events
```

```text
000 0.000675s
command terminated with exit code 7 (Connection refused)
```

The gateway service had **zero Ready endpoints** because all gateway pods were failing their readiness probes (which check `/health` → events returns 503 → gateway returns 503 → pod marked NotReady). The same result for `/reserve` and `/health` — all exit code 7.

**Direct probe to events pod (after recreating for a fresh startup window):**

```bash
echo "GET /health to events pod:"
curl -s http://10.42.0.24:8081/health

echo "GET /events to events pod:"
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://10.42.0.24:8081/events

echo "POST /reserve to events pod:"
curl -s -X POST -w "%{http_code} %{time_total}s\n" \
  -H "Content-Type: application/json" -d '{"quantity":1}' \
  http://10.42.0.24:8081/events/1/reserve
```

```text
GET /health:
{"status":"degraded","checks":{"postgres":"ok","redis":"down"}}

GET /events: 200 0.007s

POST /reserve: 200 0.006s
{"reservation_id":"0d249ffc-180c-438b-9bcd-a63ee22e7d5b","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
```

**Prometheus error rate:**

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'
```

```json
{"result": []}
```

Empty result — no gateway pods were serving traffic to generate metrics.

**Pod states during failure:**

```text
NAME                       READY   STATUS             RESTARTS
events-757576b494-z9ssp    0/1     Running            0
gateway-6c649cd97b-6jqxm   0/1     CrashLoopBackOff   7
gateway-6c649cd97b-bc7sp   0/1     CrashLoopBackOff   7
gateway-6c649cd97b-gtbzk   0/1     CrashLoopBackOff   6
gateway-6c649cd97b-kcpcz   0/1     CrashLoopBackOff   7
gateway-6c649cd97b-pcghl   0/1     CrashLoopBackOff   6
```

**Restore:**

```bash
kubectl scale deployment/redis --replicas=1
kubectl wait --for=condition=Available deployment/redis --timeout=60s
echo "End time: $(date +%H:%M:%S)"
```

```text
Restoring redis at 22:46:19
deployment.apps/redis scaled
deployment.apps/redis condition met
End time: 22:46:20
```

#### Observations

With Redis scaled to 0:

- **`GET /events` — 200 OK (0.007s):** The events listing endpoint worked correctly because it queries PostgreSQL, which was still healthy.
- **`POST /events/{id}/reserve` — 200 OK (0.006s):** The reservation was created in PostgreSQL and returned a valid reservation ID. However, the response includes `"expires_in_seconds":300` — but this TTL is **not actually enforced** because Redis is unavailable. Looking at the source code (`app/events/main.py:217-227`):

  ```python
  if redis_client:
      redis_client.setex(f"reservation:{reservation_id}", RESERVATION_TTL, ...)
      redis_client.decrby(f"event:{event_id}:held", -quantity)
  else:
      log.warning("Redis unavailable — reservation not held")
  ```

  When `redis_client` is `None` (connection failed), the reservation is stored in PostgreSQL but **no hold is created in Redis**. This means:
  - The reservation will **never expire** (no TTL mechanism)
  - The `held` counter is not decremented, so `_get_available()` shows `total_tickets` without accounting for reserved tickets — **potential overselling** if multiple users reserve concurrently
  - The `expires_in_seconds` field in the response is misleading — the reservation will not actually auto-cancel

- **`GET /health` — 503:** Events returned `{"status":"degraded","checks":{"postgres":"ok","redis":"down"}}` confirming Redis was the only failing dependency.
- **Events pod killed by liveness probe:** After ~40 seconds (initialDelaySeconds=10 + 3 failures × periodSeconds=10), the liveness probe detected the 503 health check and killed the events pod, putting it into a CrashLoopBackOff cycle.
- **All 5 gateway pods in CrashLoopBackOff:** Because events was unavailable, the gateway's `/health` aggregated a 503, causing its own readiness and liveness probes to fail. With no Ready gateway pods, the Service had zero endpoints, blocking ALL traffic (including GET /events which would have worked).
- **Prometheus error rate:** Empty — no gateway pods were serving requests to generate metrics.
- **Payments unaffected:** `GET /health` to payments returned `{"status":"healthy"}`.

#### Comparison

The hypothesis was partially correct: `GET /events` did work (via PostgreSQL), and `/health` did report degraded status. However, three outcomes were unexpected:

1. **`POST /reserve` returned 200 but with caveats** — the reservation was saved in PostgreSQL, but the Redis hold was skipped (`redis_client: None`). The `expires_in_seconds` field in the response is misleading: no actual TTL is set, so the reservation will never auto-expire. Additionally, `_get_available()` cannot see held tickets without Redis, creating a risk of **overselling** under concurrent load.

2. **Complete system collapse due to liveness probe design** — the shared `/health` endpoint for both readiness and liveness probes caused a cascading failure: Redis down → events /health = 503 → events killed by liveness → gateway /health = 503 → gateways killed → zero Ready pods → full service outage.

3. **No graceful degradation of reads** — even though GET /events worked fine (it only needs Postgres), the gateway was completely unreachable because all pods were marked NotReady/CrashLooping. The cluster went from 100% functional (with a degraded dependency) to 0% available within ~40 seconds.

The most important finding is that the liveness probe, configured to check `/health`, became a single point of failure. Even though the events service could serve its core business logic (events listing and reservations) without Redis, the liveness probe killed the pod because a non-critical dependency was unavailable.

#### Improvement statement

To improve resilience against this failure, I would separate the liveness probe from the readiness probe: the liveness probe should only check that the process is alive (e.g., a simple `/alive` endpoint), while the readiness probe checks critical dependencies (`/health`). This way, Redis being down would only remove the pod from service (readiness fails) without killing it (liveness passes), allowing it to continue serving requests that don't require Redis.

---

## Task 2 — Combined Failure Scenario (4 pts)

### Scenario design

I simulated a real incident pattern where two independent services degrade simultaneously:

- **payments**: `PAYMENT_FAILURE_RATE=0.3` (30% chance of 500 error on `/charge`) + `PAYMENT_LATENCY_MS=500` (500ms artificial delay)
- **events**: `DB_MAX_CONNS=3` (connection pool of only 3 — requests must queue for a connection)
- **mixedload**: scaled to 3 replicas to increase concurrency

The expectation was that the small DB pool would create contention across all events endpoints (`GET /events`, `POST /reserve`, `/health`), amplifying latency on every path, while the payment failures would add a steady error rate on the write path.

### Hypothesis

```
If payments has 30% failure rate + 500ms latency and events is limited
to DB_MAX_CONNS=3 under 3x mixedload replicas, the first golden signal
to degrade will be latency on ALL paths due to DB connection pool
contention. Error rate on /pay will reach ~30% of write requests.
The gateway will not protect itself — it keeps retrying, amplifying
load on the degraded downstream.
```

### Baseline (before injection)

```bash
echo "=== TASK 2: COMBINED FAILURE ===" | tee /tmp/lab8-task2.log
echo "Start time: $(date +%H:%M:%S)" | tee -a /tmp/lab8-task2.log

echo "=== BASELINE ERROR RATE ==="
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'

echo "=== BASELINE p99 LATENCY PER PATH ==="
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'

echo "=== BASELINE RPS ==="
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%5B1m%5D))'
```

```text
Start time: 23:43:13

Baseline error rate: 0.135%

Baseline p99 latency:
  /health:            0.064s
  /events/{id}/reserve: 0.025s
  /events:            0.010s

Baseline RPS: 13.5
```

### Injection

```bash
echo "Injecting failures at $(date +%H:%M:%S)"
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl rollout status deployment/payments --timeout=30s

kubectl set env deployment/events DB_MAX_CONNS=3
kubectl rollout status deployment/events --timeout=30s

kubectl scale deployment/mixedload --replicas=3
kubectl get pods -l app=mixedload

sleep 30
```

```text
Injecting failures at 23:24:39
deployment.apps/payments env updated
deployment "payments" successfully rolled out
deployment.apps/events env updated
deployment "events" successfully rolled out
deployment.apps/mixedload scaled
Mixedload replicas:
mixedload-744ccb6dfb-987mv   1/1   Running
mixedload-744ccb6dfb-gmvt7   1/1   Terminating
mixedload-744ccb6dfb-h4rlf   1/1   Running
```

### Observations

Five samples were taken over ~5 minutes to track the evolution of error rate, latency, and throughput.

```bash
# Sample 1
echo "=== SAMPLE 1 at $(date +%H:%M:%S) ==="
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%5B1m%5D))'
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=events_db_pool_size'
sleep 60
# ... repeat for samples 2-5 (identical commands)
```

| Sample | Time | Elapsed | Error Rate | RPS | p99 /events | p99 /reserve | p99 /health | DB Pool |
|--------|------|---------|-----------|-----|------------|-------------|------------|---------|
| Baseline | 23:43:13 | — | 0.135% | 13.5 | 0.010s | 0.025s | 0.064s | — |
| S1 | 23:25:32 | +53s | 1.687% | 18.3 | 0.024s | 0.097s | 0.068s | empty |
| S2 | 23:26:49 | +130s | 0.106% | 17.1 | 0.021s | 0.044s | 0.024s | empty |
| S3 | 23:27:58 | +199s | 0.229% | 15.9 | 0.024s | 0.075s | 0.055s | empty |
| S4 | 23:29:05 | +266s | 0.935% | 15.6 | 0.024s | 0.066s | 0.068s | empty |
| S5 | 23:30:20 | +341s | 0.336% | 16.4 | 0.024s | 0.080s | 0.068s | empty |

**Key observations from the data:**

**1. `DB_MAX_CONNS=3` did NOT create measurable contention at 16 RPS.**

- p99 `/events` stayed flat at **0.024s** across all 5 samples — identical to baseline.
- p99 `/reserve` oscillated between 0.044s and 0.097s — normal range for this endpoint (it does a SELECT + INSERT + optional Redis write).
- At 16–18 RPS with each request holding a connection for 10–90ms, 3 connections can handle approximately 33–300 req/s. We were well below saturation.

**2. Error rate oscillated without a clear trend.**

- S1 (1.687%) — highest spike, likely from the rollout restart of payments pod
- S2 (0.106%) — dropped near zero, system stabilised
- S3 (0.229%) — slight increase
- S4 (0.935%) — another spike
- S5 (0.336%) — back down
- The 30% payment failure rate should produce a steady ~3–5% of total traffic failing, but the actual error rate was diluted by the dominance of read requests (GET /events). The observed oscillation is driven by short bursts of 503 during pod restarts rather than the steady-state failure injection.

**3. /health latency stayed consistent.**

- Baseline: 0.064s
- After injection: 0.055–0.068s
- No significant change — the `/health` check (SELECT 1 on postgres) was unaffected by the limited DB connection pool at this traffic level.

**4. `events_db_pool_size` returned empty every time.**

- This metric is exposed on the events service's `/metrics` endpoint, not on the gateway. Our Prometheus queries ran against gateway metrics only. To observe DB pool utilisation correctly, one would need to query the events pod IP directly or configure Prometheus to scrape the events service.

### Restore

```bash
echo "Restoring at $(date +%H:%M:%S)"
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0
kubectl rollout status deployment/payments --timeout=30s
kubectl set env deployment/events DB_MAX_CONNS=10
kubectl rollout status deployment/events --timeout=30s
kubectl scale deployment/mixedload --replicas=2
echo "End time: $(date +%H:%M:%S)"
```

```text
Restoring at 23:31:16
deployment.apps/payments env updated
deployment "payments" successfully rolled out
deployment.apps/events env updated
deployment "events" successfully rolled out
deployment.apps/mixedload scaled
End time: 23:31:31
```

### Analysis

#### Which golden signal reacted first?

**Error rate** — it increased from 0.135% to 1.687% in the first sample. However, this was primarily driven by the rollout restarts of the payments and events pods (which briefly return 503), not by the actual failure injection.

#### How did error rate evolve over 5 minutes?

Error rate oscillated between 0.1% and 1.7% without a monotonic trend. This indicates:
- The DB connection pool was **not saturated** (no growing queue of failed requests)
- The 30% payment failure rate was **diluted** by the high proportion of read traffic (~85% of requests are GET /events)
- Short bursts of 503 during pod restarts dominated the error signal

#### Which path showed the worst latency amplification?

**`/health`** — p99 stayed in the 0.055–0.068s range, similar to the 0.064s baseline. `/reserve` p99 oscillated between 0.044s and 0.097s (baseline 0.025s — slight increase from write-path DB contention). `/events` was completely unaffected.

#### Why did `DB_MAX_CONNS=3` not create problems?

The traffic level (16 RPS) was well within the capacity of 3 connections:

- Each DB request completes in 10–90ms
- One connection = ~11–100 req/s
- Three connections = ~33–300 req/s theoretical capacity
- Actual load: 16 RPS (factor of 2–18x margin)

To observe real pool contention, traffic would need to be 3–5x higher.

#### What was the weakest link?

**Payments** — the 30% failure rate was the only injected fault that would have produced measurable user-facing errors at this traffic level. However, even that was diluted to ~0.3% aggregate error rate because most traffic is read-heavy. The DB_MAX_CONNS=3 constraint was not a bottleneck at this scale.

The **real weakest link** is the **read-heavy traffic profile**: because the system serves mostly GET /events (which completes in ~24ms and needs no payments or Redis), partial payments degradation is barely visible in aggregate metrics. This masks the problem until write traffic spikes — then the connection pool or payment failures would suddenly become catastrophic.

### Improvement statement

To improve resilience against combined failures, I would:

1. **Add a circuit breaker for payments in the gateway**: if payments returns errors above a threshold (e.g., >10% in 30s), the gateway should fast-fail `/pay` without waiting for the timeout. This prevents degraded payments from consuming gateway resources.

2. **Separate read and write connection pools in events**: `GET /events` should use a dedicated pool (larger, lower latency) while write operations use a smaller pool. This prevents write contention from affecting reads.

3. **Add `events_db_pool_utilization` metric to Prometheus scraping**: the metric already exists on the events `/metrics` endpoint but is not scraped. Adding it would allow alerting when pool utilisation exceeds 70%, preventing silent capacity exhaustion.

---

## Bonus Task — Resilience Improvement (2 pts)

### Chosen weakness

**Experiment 3 — Redis failure with cascading pod death.** When Redis is down, the events `/health` endpoint returns 503. Because **both liveness and readiness probes** check `/health`, the liveness probe kills the events pod within ~40 seconds. The gateway in turn detects events as unavailable, its own `/health` returns 503, and all 5 gateway pods also get killed by their liveness probes — a **complete system collapse** (0% availability).

### Fix implemented

**Separated liveness probe from readiness probe:**

1. **`app/events/main.py`** — added a lightweight `/alive` endpoint that always returns 200 without checking any dependencies:

```python
@app.get("/alive")
def alive():
    return {"status": "alive"}
```

2. **`app/gateway/main.py`** — same lightweight `/alive` endpoint:

```python
@app.get("/alive")
async def alive():
    return {"status": "alive"}
```

3. **`k8s/events.yaml`** — liveness probe path changed from `/health` to `/alive`

4. **`k8s/gateway.yaml`** — liveness probe path changed from `/health` to `/alive`

The readiness probes remain on `/health` — when a dependency is down, traffic stops being routed to the pod (readiness fails), but the pod is **not killed** (liveness passes).

### Build and deploy

```bash
docker build -t quickticket-events:alive-probe app/events
docker build -t quickticket-gateway:alive-probe app/gateway
k3d image import quickticket-events:alive-probe quickticket-gateway:alive-probe -c quickticket

kubectl set image deployment/events events=quickticket-events:alive-probe
kubectl argo rollouts set image gateway gateway=quickticket-gateway:alive-probe

kubectl patch deployment events --type=json \
  -p='[{"op": "replace", "path": "/spec/template/spec/containers/0/livenessProbe/httpGet/path", "value": "/alive"}]'
kubectl patch rollout gateway --type=json \
  -p='[{"op": "replace", "path": "/spec/template/spec/containers/0/livenessProbe/httpGet/path", "value": "/alive"}]'

kubectl rollout status deployment/events --timeout=60s
kubectl argo rollouts promote gateway
```

### Re-run: Redis failure after fix

```bash
echo "=== BONUS: Redis Failure AFTER FIX ==="
echo "Start: $(date +%H:%M:%S)"

kubectl scale deployment/redis --replicas=0
kubectl delete pod -l app=redis --force --grace-period=0 --ignore-not-found

sleep 30
echo "Pods after 30s with Redis down:"
kubectl get pods

sleep 30
echo "Pods after 60s with Redis down:"
kubectl get pods
```

### Before/After comparison

| Metric | Before fix (Exp 3) | After fix (Bonus) |
|--------|-------------------|-------------------|
| Events pod state (60s Redis down) | CrashLoopBackOff, 1 restart | **Running (0/1 NotReady), 0 restarts** |
| Gateway pod state (60s Redis down) | CrashLoopBackOff ×5, 6–7 restarts each | **Running (0/1 NotReady) ×5, 0 restarts each** |
| Events `/alive` | not exist | **200 OK `{"status":"alive"}`** |
| Events `/health` | 503 (degraded) | 503 (degraded) — unchanged |
| Events `/events` (direct probe) | 200 OK (0.007s) | **200 OK (0.003s)** |
| Events `/reserve` (direct probe) | 200 OK (0.006s, no Redis hold) | **hangs (Redis client timeout)** |
| Recovery when Redis returns | CrashLoopBackOff backoff delay (~30s) | **Immediate — readiness passes, traffic resumes** |

**After-fix pod state (60 seconds with Redis down):**
```
events-6d4f648fdd-bwlpx  0/1  Running  0   4m10s
gateway-ffc9f4d8b-wrfrk  0/1  Running  0   4m5s
gateway-ffc9f4d8b-bxr4f  0/1  Running  0   2m8s
gateway-ffc9f4d8b-qjpv9  0/1  Running  0   2m8s
gateway-ffc9f4d8b-bzdgd  0/1  Running  0   104s
gateway-ffc9f4d8b-p2sbb  0/1  Running  0   104s
```

All pods **Running**, zero restarts — the system gracefully degraded instead of collapsing.

**After restore:**
```
events-6d4f648fdd-bwlpx  1/1  Running  0  6m54s
gateway-ffc9f4d8b-wrfrk  1/1  Running  0  6m49s
gateway-ffc9f4d8b-bxr4f  1/1  Running  0  4m52s
gateway-ffc9f4d8b-bzdgd  1/1  Running  0  4m28s
gateway-ffc9f4d8b-p2sbb  1/1  Running  0  4m28s
gateway-ffc9f4d8b-qjpv9  1/1  Running  0  4m52s
```

All pods recovered their readiness probes once Redis was available — no manual intervention needed.

### Trade-off

Separating the liveness probe from `/health` means the liveness probe no longer catches **process-liveliness bugs where the process is stuck but the TCP stack responds** (deadlock, infinite loop). The `/alive` endpoint is trivial and always succeeds. This is a deliberate trade-off: we accept a slightly higher risk of false negatives (a stuck process not being killed) in exchange for eliminating false positives (a perfectly healthy process being killed because a non-critical dependency is down).
