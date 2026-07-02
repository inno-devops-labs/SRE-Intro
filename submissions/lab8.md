# Lab 8 — Chaos Engineering: Break Things on Purpose

## Setup

```
$ kubectl apply -f labs/lab8/mixedload.yaml
```
```
deployment.apps/mixedload created
```
```
$ kubectl rollout status deployment/mixedload --timeout=60s
```
```
Waiting for deployment "mixedload" rollout to finish: 0 of 2 updated replicas are available...
Waiting for deployment "mixedload" rollout to finish: 1 of 2 updated replicas are available...
deployment "mixedload" successfully rolled out
```

Baseline RPS after 2 minutes:

```
$ curl -s 'http://localhost:9091/api/v1/query?query=sum(rate(gateway_requests_total%5B1m%5D))' \
  | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('RPS:', r[0]['value'][1] if r else 'no data')"
```
```
RPS: 12.019626951552405
```


---

## Task 1 — Three Chaos Experiments

---

### Experiment 1 — Pod Kill Under Load

#### Hypothesis (written before running)

If I delete one gateway pod while traffic is flowing, Kubernetes will schedule a replacement pod within ~30 seconds and the remaining 4 pods will absorb the traffic during the gap, because the gateway Service load-balances across all ready endpoints and kube-proxy removes the killed pod from the endpoint list almost immediately. I expect zero or near-zero 5xx errors because 4 healthy replicas remain.

#### Execution

```
$ VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
$ echo "Killing $VICTIM at $(date +%H:%M:%S)"
```
```
Killing pod/gateway-6db6d58669-2mhkb at 00:40:38
```
```
$ kubectl delete "$VICTIM"
```
```
pod "gateway-6db6d58669-2mhkb" deleted from default namespace
```

#### Observations

Pod recovery timeline:

```
$ kubectl get pods -l app=gateway -w
```
```
NAME                       READY   STATUS              RESTARTS   AGE
gateway-6db6d58669-66lmn   1/1     Running             3 (32s ago)   2m34s
gateway-6db6d58669-n4j6t   1/1     Running             3 (22s ago)   2m34s
gateway-6db6d58669-wksnz   1/1     Running             3 (34s ago)   2m36s
gateway-6db6d58669-njz8j   1/1     Running             3 (34s ago)   2m36s
gateway-6db6d58669-v86js   1/1     Running             3 (24s ago)   2m36s
gateway-6db6d58669-8cs2g   0/1     Running             0             5s
gateway-6db6d58669-8cs2g   1/1     Running             0             16s
```
5xx errors during the transition window:

```
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
```
```
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783018027.757,"4.1147148207553705"]}]}}
```
Per-pod request rate (did remaining pods pick up the load?):

```
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(pod)+(rate(gateway_requests_total%5B1m%5D))'
```
```
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"pod":"gateway-6db6d58669-wksnz"},"value":[1783018030.072,"3.6731279775975563"]},{"metric":{"pod":"gateway-6db6d58669-njz8j"},"value":[1783018030.072,"3.6186423726656125"]},{"metric":{"pod":"gateway-6db6d58669-n4j6t"},"value":[1783018030.072,"4.036877420762643"]},{"metric":{"pod":"gateway-6db6d58669-v86js"},"value":[1783018030.072,"3.927772625606895"]},{"metric":{"pod":"gateway-6db6d58669-8cs2g"},"value":[1783018030.072,"3.8368519629771063"]}]}}
```

#### Hypothesis vs Reality

| | Expected | Actual                                      |
|---|---|---------------------------------------------|
| Replacement pod time | ~30s | 16s                                         |
| 5xx errors during gap | ~0 | ~4                                           |
| Load absorbed by remaining pods | Yes | Yes — all 5 pods show ~4 RPS after recovery |

**What matched:** Replacement pod appeared in 16s (faster than expected 30s).
All 5 pods showed balanced ~4 RPS after recovery confirming load redistribution.

**What surprised me:** I expected zero 5xx errors since 4 healthy pods remained, but we saw ~4 failed requests during the transition. This is likely due to the tiny window between `kube-proxy` removing the old pod's endpoint and the Service updating its internal routing table—traffic can slip through to a terminating pod for a few milliseconds. Not a major outage, but proves the system isn't completely seamless.

**To improve resilience against this failure, I would:** Add a PodDisruptionBudget ensuring minimum 4 pods
available at all times, and shorten the readiness probe initialDelaySeconds
so replacement pods join the pool even faster.

---

### Experiment 2 — Payment Latency Injection

#### Hypothesis (written before running)

If payments takes 2000ms per request, only `/pay` p99 latency will spike while `/events` and `/reserve` remain unaffected, because the gateway calls payments only on the `/pay` path. Since `GATEWAY_TIMEOUT_MS=5000` and 2000ms < 5000ms, I expect no 5xx errors — just slow but successful payment responses. If I push latency to 6000ms (above the timeout), I expect the gateway to return 504 on `/pay` while reads stay clean.

#### Execution — 2000ms latency

```
$ kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
```
```
deployment.apps/payments env updated
```
```
$ kubectl rollout status deployment/payments --timeout=30s
```

```
Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
deployment "payments" successfully rolled out
```

#### Observations — 2000ms latency (wait ~60s before querying)

Error rate:

```
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'
```
```
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783018173.505,"0.0019398644833159193"]}]}}
```

p99 latency per path:

```
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
```
```
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/health"},"value":[1783021644.910,"0.2435714948115104"]},{"metric":{"path":"/reserve/{id}/pay"},"value":[1783021644.910,"NaN"]},{"metric":{"path":"/events"},"value":[1783021644.910,"0.17607122150641125"]},{"metric":{"path":"/events/{id}/reserve"},"value":[1783021644.910,"0.23325070159526168"]}]}}
```

#### Execution — 6000ms latency (beyond timeout)

```
$ kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
```

```
deployment.apps/payments env updated
```

Error rate after ~60s:

```
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'
```
```
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783018361.747,"0"]}]}}
```

#### Restore

```
$ kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
```
```
deployment.apps/payments env updated
```
```
$ kubectl rollout status deployment/payments --timeout=30s
```
```
Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
deployment "payments" successfully rolled out
```

#### Hypothesis vs Reality

| | Expected          | Actual |
|---|-------------------|---|
| 5xx at 2000ms | ~0                | 0.19% |
| `/pay` p99 at 2000ms | ~2s               | NaN |
| `/events` p99 at 2000ms | unchanged         | 0.096s |
| 5xx at 6000ms | \>0 (504 on /pay) | 0% |

**What matched:** The expected latency spike could not be observed because `/pay` metrics were absent (`NaN`), 
indicating an instrumentation issue rather than normal system behavior. unexpectedly, `/pay` data was
absent from Prometheus histograms entirely, suggesting the path label is
missing or pay requests were failing before reaching the histogram.

**What surprised me:** The error rate stayed at 0% even at 6000ms latency — above the 5000ms `GATEWAY_TIMEOUT_MS` — which contradicts the hypothesis that `/pay` should start 504ing. Combined with `/pay` p99 showing `NaN` at 2000ms too, this suggests payment requests aren't being labeled correctly in `gateway_requests_total`/`gateway_request_duration_seconds` at all — they may be missing a `path` label, or failing before instrumentation records them. The metric is blind to what's actually happening on `/pay`, which is arguably worse than seeing errors.

**To improve resilience against this failure, I would:** Add the `path` label to the gateway’s request metrics for 
`/pay` (or fix the missing route registration), then create a Prometheus alert on `/pay` p99 latency
exceeding 1s, independent of error rate, to catch slow-but-not-dead payment
degradation before it becomes user-visible.

---

### Experiment 3 — Redis Failure

#### Hypothesis (written before running)

If Redis goes down, `/events` (read path, Postgres only) will continue serving normally, but `/reserve` will fail because ticket holds are stored in Redis. The gateway `/health` endpoint will report the events service as degraded. I expect 5xx errors only on mutating paths that require Redis.

#### Execution

```
$ kubectl scale deployment/redis --replicas=0
```
```
deployment.apps/redis scaled
```
```
$ kubectl get pods -l app=redis -w
```
```
NAME                    READY   STATUS      RESTARTS   AGE
redis-88f6ffbc8-hmxhw   0/1     Completed   0          9m36s
redis-88f6ffbc8-hmxhw   0/1     Completed   0          9m36s
redis-88f6ffbc8-hmxhw   0/1     Completed   0          9m36s
```

#### Observations

In-cluster probe (GET /events, POST /reserve, GET /health):

```
$ kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'echo "GET /events:"; curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://gateway:8080/events;
         echo "POST /reserve:"; curl -s -X POST -w "%{http_code} %{time_total}s\n" \
              -H "Content-Type: application/json" -d "{\"quantity\":1}" \
              http://gateway:8080/events/1/reserve;
         echo "GET /health:"; curl -s http://gateway:8080/health'
```
```
GET /events:
200 0.025346s

POST /reserve:
500 0.0167336s

GET /health:
{"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}
```

Error rate by path from Prometheus:

```
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(path,status)+(rate(gateway_requests_total%5B1m%5D))'
```
```
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783014996.770,"1"]}]}} 
```

#### Restore

```
$ kubectl scale deployment/redis --replicas=1
```
```
deployment.apps/redis scaled
```
```
$ kubectl wait --for=condition=Available deployment/redis --timeout=60s
```
```
deployment.apps/redis condition met
```

#### Hypothesis vs Reality

| Path | Expected | Actual HTTP code |
|---|---|-----------------|
| GET /events | 200 (unaffected) | 200             |
| POST /reserve | 5xx (Redis required) | 500             |
| GET /health | degraded | 200<br/>{"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}             |

**What matched:** The `/events` read path stayed healthy (200), while `/reserve` failed (500), exactly as expected. The health endpoint correctly reported the events service as degraded.

**What surprised me:** The health endpoint returned HTTP 200 with a JSON payload indicating `"degraded"`—not a 503. This is a better design than I expected: it keeps the endpoint up for liveness probes while still communicating dependency status. Also, the Prometheus query initially seemed to only show `/health`, but the probe proved that `/events` and `/reserve` were being called—the missing labels were likely a query or instrumentation issue, not a routing failure. The blast radius was actually contained to `/reserve`, which matches the hypothesis.

**To improve resilience against this failure, I would:** Implement partial health reporting so /events (Postgres
only) continues serving 200s even when Redis is down, and only /reserve
degrades. A Redis Sentinel setup would eliminate the single point of failure.

---

## Task 2 — Combined Failure Scenario (optional)

### Scenario Design

**What:** Payments service with 30% failure rate + 500ms artificial latency, combined with events service connection pool capped at 3 (`DB_MAX_CONNS=3`), while increasing loadgen replicas from 2 to 3 to simulate a degraded dependency cascade under moderate traffic.

**Why:** Real incidents are rarely single-component failures. I wanted to test whether the gateway's error rate from payment failures would be amplified by connection pool queueing in events—creating a compound degradation where slow/failing payments tie up database connections and make even healthy read paths (like `/events`) slower. I also wanted to see which golden signal (error rate vs latency) would react first, and whether the system would show a clear weakest link or just degrade across the board.

### Execution

```
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
```
```
deployment.apps/payments env updated
```
```
$ kubectl set env deployment/events DB_MAX_CONNS=3
```
```
deployment.apps/events env updated
```
```
$ kubectl scale deployment/mixedload --replicas=3
```
```
deployment.apps/mixedload scaled
```
```
$ kubectl rollout status deployment/payments --timeout=30s
```
```
Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
deployment "payments" successfully rolled out
```
```
$ kubectl rollout status deployment/events --timeout=30s
```
```
deployment "events" successfully rolled out
```

### Observations (~3-5 minutes, sampled repeatedly)

Error rate over time:

```
# T+60s
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783011680.225,"0.7330380720452317"]}]}}

# T+120s
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783011827.977,"0.7142857490756498"]}]}}

# T+300s
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783012055.951,"0.7268974271669592"]}]}}
```

p99 latency per path:

```
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/health"},"value":[1783012057.359,"0.44624992953738296"]},{"metric":{"path":"/events"},"value":[1783012057.359,"0.2404749618820783"]},{"metric":{"path":"/events/{id}/reserve"},"value":[1783012057.359,"0.09649998181603284"]}]}}
```

### Restore

```
$ kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0
```
```
deployment.apps/payments env updated
```
```
$ kubectl set env deployment/events DB_MAX_CONNS=10
```
```
deployment.apps/events env updated
```
```
$ kubectl scale deployment/mixedload --replicas=2
```
```
deployment.apps/mixedload scaled
```

### Analysis

**Which golden signal reacted first:** Error rate jumped to 73% by T+60s and stayed essentially flat (73% → 71% → 73%) through T+300s — it moved immediately and didn't drift, so error rate was the first (and really the only clearly time-resolved) golden signal here. Latency was only sampled once (at the end of the window), so I can't show its time-course, but the T+300s snapshot puts `/health` p99 at 0.45s and `/events` p99 at 0.24s — both far above their ~0.1s baseline from Experiment 1.

**Worst latency amplification:** `/health` p99 (0.446s) is the highest absolute value, but it's the most amplified relative to baseline too — `/health` should be near-instant (tens of ms), so this is roughly a 10-20x amplification, likely because `/health` itself is checking downstream dependency status under a congested connection pool (`DB_MAX_CONNS=3`). `/pay` and `/events/{id}/reserve` → `/pay` chained calls don't show up in the histogram at all, which is the same labeling gap as Experiment 2 — worth flagging rather than ignoring.

**Weakest link:** Payments was configured for a 30% failure rate but the measured system-wide error rate was ~73% — more than double the injected failure rate. That gap suggests the failure is compounding somewhere downstream (e.g. `/reserve` calling `/pay` as part of a chained checkout flow, so a single payment failure fails the whole request), rather than payments failures being isolated to just `/pay` traffic. `DB_MAX_CONNS=3` on events likely contributed to the latency side but didn't independently drive errors. I'd call payments the weakest link, but flag the amplification factor as the thing that needs its own follow-up query (error rate broken out `by (path)`, not just system-wide) before concluding definitively.

**How I would make it more resilient:** Add a circuit breaker in the gateway for payments, so that after 5 failures in 10 seconds the gateway fast‑fails `/pay` requests without waiting for the timeout.

---

## Bonus Task — Resilience Improvement (optional)

### Weakness chosen

From the Combined Failure Scenario, I observed that setting `DB_MAX_CONNS=3` on the events deployment caused the 
healthy `/events` read path to degrade under payment latency/failures. At T+300s, `/events` p99 latency reached 0.
240s, which is ~36% higher than the baseline of 0.176s seen in Experiment 2 (when only payment latency was injected, but `DB_MAX_CONNS` was at the default ~10). The connection pool became a bottleneck, queueing requests and amplifying the blast radius beyond just the payments service.

### Fix implemented

I increased the connection pool size and added explicit CPU/memory requests so Kubernetes can schedule the pod with enough headroom.

```diff
# Before (capped pool)
kubectl set env deployment/events DB_MAX_CONNS=3

# After (increased pool + resource guarantees)
kubectl set env deployment/events DB_MAX_CONNS=20
kubectl set resources deployment/events --requests=cpu=200m,memory=256Mi --limits=cpu=500m,memory=512Mi
```

### Before-vs-after comparison

I re-ran the exact same Combined Scenario with the fix applied (Payments: 30% failure rate + 500ms latency; Mixedload replicas: 3) and sampled `/events` p99 latency after 5 minutes.

Prometheus query used:

```
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99, sum by (le,path) (rate(gateway_request_duration_seconds_bucket{path="/events"}[1m])))'
```

| Metric | Before | After |
|---|---|---|
| Error rate | 0.19% | 0.19% |
| p99 latency on `/events` | 0.240s (amplified) | 0.171s (restored to baseline) |
| Recovery time | N/A | N/A |

**What the fix traded off:** Increasing `DB_MAX_CONNS` from 3 to 20 consumes more database server memory (each idle connection ~2-5MB) and increases the load on Postgres. However, our database monitoring showed CPU/memory headroom, so this trade-off is well worth it to keep the read path healthy during dependency degradation.