# Lab 8 - Chaos Engineering

## Setup

Lab 8 load generator was applied and kept running during the experiments:

```bash
$ kubectl apply -f labs/lab8/mixedload.yaml
deployment.apps/mixedload created

$ kubectl rollout status deployment/mixedload --timeout=60s
deployment "mixedload" successfully rolled out
```

Before the experiments I found that Postgres did not have the seed schema yet, so `/events` was returning 502 through the gateway. I initialized the expected lab data and increased event 1 capacity so `mixedload` could keep exercising `/reserve` and `/pay` instead of quickly exhausting the default 100 tickets.

```bash
$ kubectl exec -i deployment/postgres -- psql -U quickticket -d quickticket -f - < app/seed.sql
CREATE TABLE
CREATE TABLE
INSERT 0 5

$ kubectl exec deployment/postgres -- psql -U quickticket -d quickticket -c 'UPDATE events SET total_tickets = 100000 WHERE id = 1'
UPDATE 1

$ kubectl exec deployment/postgres -- psql -U quickticket -d quickticket -c 'TRUNCATE orders'
TRUNCATE TABLE

$ kubectl exec deployment/redis -- redis-cli FLUSHALL
OK
```

Clean baseline at `2026-06-27T16:30:51+03:00`:

```text
error_ratio = 0

status_by_path:
/health 200: 1.49 rps
/events 200: 5.65 rps
/events/{id}/reserve 200: 5.58 rps
/reserve/{id}/pay 200: 5.60 rps

p99:
/events: 0.020s
/events/{id}/reserve: 0.039s
/reserve/{id}/pay: 0.064s
/health: 0.025s
```

## Task 1 - Three Chaos Experiments

### Experiment 1 - Pod Kill Under Load

Hypothesis written before running:

```text
HYPOTHESIS: If I delete one gateway pod while traffic is flowing, Kubernetes will create a replacement quickly and user traffic will mostly continue through the remaining four pods because the Service removes the terminating pod from endpoints and the Rollout keeps five replicas desired.
```

Commands:

```bash
$ VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
$ kubectl delete "$VICTIM"
```

Observed at `2026-06-27T16:31:02+03:00`:

```text
victim=pod/gateway-7dbd865f6b-8r6ts
pod "gateway-7dbd865f6b-8r6ts" deleted from default namespace
2026-06-27T16:31:03+03:00 ready=4 total=5
2026-06-27T16:31:06+03:00 ready=4 total=5
2026-06-27T16:31:08+03:00 ready=5 total=5
```

Replacement time: about 6 seconds from delete to 5/5 Running again.

Prometheus observation at `2026-06-27T16:31:18+03:00`:

```text
sum(increase(gateway_requests_total{status=~"5.."}[3m])) = 6.160960714285714

sum by (path,status):
/health 503: 6.195499047619047
/events 502: 0

sum by (pod)(rate(gateway_requests_total[1m])):
gateway-7dbd865f6b-zqk7r: 3.89 rps
gateway-7dbd865f6b-xdnb5: 4.07 rps
gateway-7dbd865f6b-wdz8q: 3.49 rps
gateway-7dbd865f6b-r7pjd: 3.95 rps
gateway-7dbd865f6b-8r6ts: 2.55 rps
gateway-7dbd865f6b-tqdms: 0.57 rps
```

Comparison: the hypothesis mostly matched reality. User traffic continued, and the new pod started receiving traffic while old time series still had samples from the killed pod. The surprise was that the 5xx increase came from `/health` 503, not from the user paths.

To improve resilience against this failure, I would separate liveness/readiness probes from dependency health so probe noise does not look like user-facing 5xx during pod replacement.

### Experiment 2 - Payment Latency Injection

Hypothesis written before running:

```text
HYPOTHESIS: If payments takes 2 seconds per request, /reserve/{id}/pay p99 will rise to about 2 seconds, but gateway 5xx should stay near zero because 2000 ms is below GATEWAY_TIMEOUT_MS=5000.
```

Commands:

```bash
$ kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
deployment.apps/payments env updated

$ kubectl rollout status deployment/payments --timeout=30s
deployment "payments" successfully rolled out
```

Observed after the rate window filled, at `2026-06-27T16:33:23+03:00`:

```text
error_ratio = 0.004524886877828055

p99:
/reserve/{id}/pay: 2.485s
/events: 0.010s
/events/{id}/reserve: 0.039s
/health: 0.025s

status_by_path:
/reserve/{id}/pay 200: 0.87 rps
/events 200: 0.85 rps
/events/{id}/reserve 200: 0.80 rps
/health 503: 0.018 rps
```

Bonus observation for timeout behavior:

```bash
$ kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
deployment.apps/payments env updated
```

At `2026-06-27T16:35:21+03:00`:

```text
status_by_path:
/reserve/{id}/pay 503: 0.36 rps
/reserve/{id}/pay 200: 0 rps

p99:
/reserve/{id}/pay: 7.475s
/events: 0.010s
/events/{id}/reserve: 0.045s
```

Manual probe:

```text
503 5.005329s
```

Restore:

```bash
$ kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
deployment.apps/payments env updated

$ kubectl rollout status deployment/payments --timeout=30s
deployment "payments" successfully rolled out
```

Comparison: the 2000 ms hypothesis matched well. `/pay` became slow but mostly successful; read and reserve paths stayed fast. When latency exceeded the gateway timeout, the gateway protected itself by returning after about 5 seconds, but the status was 503 rather than the 504 mentioned in the lab hint.

To improve resilience against this failure, I would add a latency SLO alert on p99 for `/reserve/{id}/pay`, because a 2 second payment slowdown can hurt users while error-rate alerts remain quiet.

### Experiment 3 - Redis Failure

Hypothesis written before running:

```text
HYPOTHESIS: If Redis goes down, listing events will still work because it only needs Postgres, reserve will fail because ticket holds are stored in Redis, and /health will report degraded.
```

Commands:

```bash
$ kubectl scale deployment/redis --replicas=0
deployment.apps/redis scaled
```

Observed at `2026-06-27T16:37:18+03:00`:

```text
redis_pods=0
```

The first probe could not connect to gateway at all:

```text
GET /events:
000 0.001042s
POST /reserve:
000 0.000531s
```

Endpoint and pod state explained why:

```bash
$ kubectl get endpoints gateway redis
NAME      ENDPOINTS   AGE
gateway               124m
redis     <none>      124m

$ kubectl get pods -l app=gateway
NAME                       READY   STATUS    RESTARTS
gateway-7dbd865f6b-r7pjd   0/1     Running   1
gateway-7dbd865f6b-tqdms   0/1     Running   0
gateway-7dbd865f6b-wdz8q   0/1     Running   1
gateway-7dbd865f6b-xdnb5   0/1     Running   0
gateway-7dbd865f6b-zqk7r   0/1     Running   0
```

Verbose curl:

```text
* Host gateway:8080 was resolved.
* IPv4: 10.96.150.166
* connect to 10.96.150.166 port 8080 failed: Connection refused
curl: (7) Failed to connect to gateway:8080
```

Pod events showed the root cause:

```text
Readiness probe failed: HTTP probe failed with statuscode: 503
Liveness probe failed: HTTP probe failed with statuscode: 503
```

Restore:

```bash
$ kubectl scale deployment/redis --replicas=1
deployment.apps/redis scaled

$ kubectl wait --for=condition=Available deployment/redis --timeout=60s
deployment.apps/redis condition met

2026-06-27T16:38:23+03:00 gateway_ready=5 endpoints=5
```

Comparison: the hypothesis was wrong in an important way. Redis failure did not only break reservation holds; dependency-aware gateway probes removed every gateway pod from the Service, so even `/events` could not be reached through gateway. This is the biggest resilience weakness found in the lab.

To improve resilience against this failure, I would keep process-level liveness/readiness separate from dependency health and expose dependency degradation in `/health`/metrics instead of removing all gateway endpoints.

## Task 2 - Combined Failure Scenario

Scenario: degraded dependencies.

Why: this stacks partial payment failure with payment latency, tighter DB connection limits, and higher checkout load. It should show whether the weakest link is DB pool pressure or the payments dependency.

Commands:

```bash
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
deployment.apps/payments env updated

$ kubectl set env deployment/events DB_MAX_CONNS=3
deployment.apps/events env updated

$ kubectl scale deployment/mixedload --replicas=3
deployment.apps/mixedload scaled
```

Observed samples:

```text
sample=1 time=2026-06-27T16:40:09+03:00
error_ratio = 0.10185185185185185
/reserve/{id}/pay 503: 0.98 rps
/reserve/{id}/pay 200: 1.76 rps
p99 /reserve/{id}/pay: 0.7475s
p99 /events: 0.0099s
p99 /events/{id}/reserve: 0.0234s

sample=2 time=2026-06-27T16:41:09+03:00
error_ratio = 0.09009009009009009
/reserve/{id}/pay 503: 1.13 rps
/reserve/{id}/pay 200: 2.47 rps
p99 /reserve/{id}/pay: 0.7475s
p99 /events: 0.0110s
p99 /events/{id}/reserve: 0.0100s

sample=3 time=2026-06-27T16:42:10+03:00
error_ratio = 0.08774583963691376
/reserve/{id}/pay 503: 1.07 rps
/reserve/{id}/pay 200: 2.44 rps
p99 /reserve/{id}/pay: 0.7475s
p99 /events: 0.0100s
p99 /events/{id}/reserve: 0.0102s
```

The first golden signal to react was error rate. It jumped to about 9-10% and stayed there. The worst latency amplification was on `/reserve/{id}/pay`; `/events` and `/events/{id}/reserve` stayed near baseline.

Weakest link: `payments`. The injected 30% failure rate produced almost all visible user-facing 5xx on `/reserve/{id}/pay`, while the DB cap did not create meaningful latency on the read or reserve paths in this run. I would make it more resilient with payment-specific circuit breaking, clearer 503/504 handling, idempotency keys, and alerts on both error rate and p99 latency for the payment path.

Restore:

```bash
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0
deployment.apps/payments env updated

$ kubectl set env deployment/events DB_MAX_CONNS=10
deployment.apps/events env updated

$ kubectl scale deployment/mixedload --replicas=2
deployment.apps/mixedload scaled
```

## Bonus Task - Resilience Improvement

Chosen weakness: Redis outage removed gateway endpoints, and after the first attempted fix it also became clear that `events` readiness had the same dependency-coupling problem. A noncritical dependency outage should not make read-only `/events` unreachable.

Config change:

```diff
diff --git a/k8s/events.yaml b/k8s/events.yaml
@@
           readinessProbe:
             httpGet:
-              path: /health
+              path: /metrics
               port: 8081

diff --git a/k8s/gateway.yaml b/k8s/gateway.yaml
@@
           livenessProbe:
             httpGet:
-              path: /health
+              path: /metrics
               port: 8080
@@
           readinessProbe:
             httpGet:
-              path: /health
+              path: /metrics
               port: 8080
```

Applied with:

```bash
$ kubectl apply -f k8s/gateway.yaml
rollout.argoproj.io/gateway configured
service/gateway unchanged

$ kubectl apply -f k8s/events.yaml
deployment.apps/events configured
service/events configured
```

The gateway Rollout completed after the Lab 7 canary analysis:

```text
2026-06-27T16:46:15+03:00
phase=Healthy step=6 updated=5 ready=5
```

Before fix, with Redis scaled to zero:

```text
gateway endpoints: 0
events through gateway: curl failed, connection refused
reserve through gateway: curl failed, connection refused

kubectl get endpoints gateway redis
gateway: <none>
redis: <none>
```

After fix, with Redis scaled to zero again:

```text
2026-06-27T16:48:51+03:00
redis_pods=0 gateway_ready=5 gateway_endpoints=5 events_ready=1 events_endpoints=1
```

Direct proof:

```text
direct_events
200 0.007359s

gateway_events
200 0.008226s
```

Gateway logs during the same Redis outage showed that read traffic survived while reserve degraded:

```text
GET /events HTTP/1.1" 200 OK
POST /events/1/reserve HTTP/1.1" 504 Gateway Timeout
GET /health HTTP/1.1" 503 Service Unavailable
```

Before vs after:

```text
Before fix:
- Redis down caused gateway Service endpoints to drop to 0.
- Users could not reach even GET /events through gateway.

After fix:
- Redis down left gateway endpoints at 5/5 and events endpoint at 1/1.
- GET /events through gateway returned 200 in about 8 ms.
- POST /events/1/reserve still failed/timed out, which is expected because Redis is required for reservation holds.
```

Tradeoff: the fix keeps partially working services in rotation, so dependency degradation must be surfaced through `/health`, metrics, and alerts rather than by removing pods from Service endpoints.

## Final Cluster State

```bash
$ kubectl get deploy events payments redis mixedload -o wide
NAME        READY   UP-TO-DATE   AVAILABLE
events      1/1     1            1
payments    1/1     1            1
redis       1/1     1            1
mixedload   2/2     2            2

$ kubectl get rollout gateway -o jsonpath='phase={.status.phase} updated={.status.updatedReplicas} ready={.status.readyReplicas} stable={.status.stableRS}{"\n"}'
phase=Healthy updated=5 ready=5 stable=79bc69dcf6
```
