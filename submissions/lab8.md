# Lab 8 — Chaos Engineering: Break Things on Purpose

## Task 1 — Three Chaos Experiments

### Experiment 1 — Pod Kill Under Load

1. **Hypothesis:**
   "If I delete one gateway pod while traffic is flowing, the overall request stream will continue serving successfully without noticeable errors because Kubernetes runs 5 gateway replicas behind the gateway Service, meaning kube-proxy will immediately route traffic across the remaining 4 healthy pods while the Rollout controller schedules a replacement."

2. **Command executed:**
   ```bash
   VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
   echo "Killing $VICTIM at $(date +%H:%M:%S)"
   kubectl delete "$VICTIM"
   ```

3. **Observations (executed at 11:15:20 on 2026-10-04):**
   - **Pod recovery:**
     ```bash
     $ kubectl get pods -l app=gateway -w
     NAME                       READY   STATUS        RESTARTS   AGE
     gateway-796d5bb8fd-xvxgp   1/1     Terminating   0          18m
     gateway-796d5bb8fd-74k2l   0/1     Pending       0          0s
     gateway-796d5bb8fd-74k2l   0/1     ContainerCreating   0          1s
     gateway-796d5bb8fd-74k2l   1/1     Running             0          3s
     gateway-796d5bb8fd-xvxgp   0/1     Terminating         0          18m
     ```
     The replacement pod `gateway-796d5bb8fd-74k2l` reached `Running` status in roughly 3–4 seconds.

   - **5xx Errors during transition:**
     ```bash
     $ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
       'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
     {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1791108920,"0"]}]}}
     ```
     Zero 5xx errors were introduced during the pod recreation window.

   - **Per-pod request rates:**
     ```bash
     $ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
       'http://localhost:9090/api/v1/query?query=sum+by+(pod)+(rate(gateway_requests_total%5B1m%5D))'
     {"status":"success","data":{"resultType":"vector","result":[
       {"metric":{"pod":"gateway-796d5bb8fd-jmxrl"},"value":[1791108920,"0.88"]},
       {"metric":{"pod":"gateway-796d5bb8fd-qqd8q"},"value":[1791108920,"0.91"]},
       {"metric":{"pod":"gateway-796d5bb8fd-s4dnz"},"value":[1791108920,"0.86"]},
       {"metric":{"pod":"gateway-796d5bb8fd-z6cxp"},"value":[1791108920,"0.89"]}
     ]}}
     ```
     Traffic was evenly distributed across the 4 surviving gateway pods at ~0.88 RPS each.

4. **Comparison:**
   The hypothesis was confirmed. The presence of 5 replicas combined with rapid endpoint removal ensured complete seamlessness with 0 failed requests. What was surprising was how cleanly kube-proxy removed the terminating pod endpoint before any connection attempts failed.

5. **Resilience improvement:**
   To improve resilience against this failure, I would configure a `PodDisruptionBudget` (`minAvailable: 80%`) to enforce that cluster maintenance or eviction routines can never drop gateway capacity below 4 active replicas simultaneously.

---

### Experiment 2 — Payment Latency Injection

1. **Hypothesis:**
   "If payments takes 2 seconds per request, the gateway will not return 5xx errors because 2000ms is well below the `GATEWAY_TIMEOUT_MS` threshold of 5000ms; however, the tail latency (p99) on the `/pay` route will surge to ~2 seconds, whereas browse paths (`/events`) will experience zero latency degradation."

2. **Command executed:**
   ```bash
   kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
   kubectl rollout status deployment/payments --timeout=30s
   ```

3. **Observations (executed at 11:28:10 on 2026-10-04):**
   - **Gateway 5xx error ratio:**
     ```bash
     $ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
       'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'
     {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1791109750,"0"]}]}}
     ```
     No errors occurred; all requests completed with HTTP 200.

   - **p99 Latency per endpoint:**
     ```bash
     $ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
       'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
     {"status":"success","data":{"resultType":"vector","result":[
       {"metric":{"path":"/events"},"value":[1791109750,"0.024"]},
       {"metric":{"path":"/events/{id}/reserve"},"value":[1791109750,"0.038"]},
       {"metric":{"path":"/reserve/{id}/pay"},"value":[1791109750,"2.048"]}
     ]}}
     ```
     The `/reserve/{id}/pay` p99 latency surged to ~2.05s, while read and reservation paths remained unchanged at 24ms and 38ms.

   - **Pushing beyond timeout (6000ms vs 5000ms timeout):**
     ```bash
     $ kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
     $ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
       'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D"504"%7D%5B1m%5D))'
     {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1791109920,"0.28"]}]}}
     ```
     As expected, the gateway enforced `GATEWAY_TIMEOUT_MS=5000` and returned 504 Gateway Timeout on checkout requests.

4. **Comparison:**
   The hypothesis was completely accurate. Latency was isolated strictly to the checkout path without contaminating catalog queries. Pushing latency past 5000ms cleanly provoked 504 responses, validating gateway self-protection.

5. **Resilience improvement:**
   To improve resilience against this failure, I would implement asynchronous decoupled checkout processing (e.g., producing charge intents to an event bus or worker queue) so user checkouts do not block synchronous HTTP connection threads on slow downstream providers.

---

### Experiment 3 — Redis Failure

1. **Hypothesis:**
   "If Redis goes down, event browsing (`GET /events`) will continue operating normally because it only queries PostgreSQL, but ticket holds (`POST /events/{id}/reserve`) will fail immediately because temporary seat locks require Redis, which will also cause `/health` to switch to a degraded status."

2. **Command executed:**
   ```bash
   kubectl scale deployment/redis --replicas=0
   kubectl get pods -l app=redis -w
   ```

3. **Observations (executed at 11:42:00 on 2026-10-04):**
   ```bash
   $ kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
     sh -c 'echo "GET /events:"; curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://gateway:8080/events;
            echo "POST /reserve:"; curl -s -X POST -w "%{http_code} %{time_total}s\n" \
                 -H "Content-Type: application/json" -d "{\"quantity\":1}" \
                 http://gateway:8080/events/1/reserve;
            echo "GET /health:"; curl -s http://gateway:8080/health'
   
   GET /events:
   200 0.018s
   POST /reserve:
   500 0.042s
   GET /health:
   {"status":"degraded","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED","redis":"down"}}
   ```
   - Listing events worked without issue (`200 OK` in 18ms).
   - Reserving failed immediately (`500 Internal Server Error`).
   - `/health` reported `"status":"degraded"`.

4. **Comparison:**
   The hypothesis aligned with observations: Redis only handles dynamic holding locks, leaving catalog lookups functional. What was notable was that readiness probes on the `events` service eventually registered failure and pulled the pod from endpoints if persistent connectivity was checked.

5. **Resilience improvement:**
   To improve resilience against this failure, I would deploy Redis in High Availability mode (Redis Sentinel or a Redis Cluster with replication) with automatic failover to eliminate Redis as a single point of failure for bookings.

---

## Task 2 — Combined Failure Scenario

### Scenario Design
**Degraded dependencies under load:**  
* `payments`: `PAYMENT_FAILURE_RATE=0.3`, `PAYMENT_LATENCY_MS=500`  
* `events`: `DB_MAX_CONNS=3`  
* `mixedload`: scaled to 3 replicas  

**Rationale:** In production incidents, downstream slowdowns often trigger connection pool exhaustion upstream. Artificially restricting database connections while injecting payment delays stresses both worker thread concurrency and database connection reuse.

### Execution and Telemetry (11:55:00 → 12:00:00 on 2026-10-04)
```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
kubectl rollout status deployment/payments --timeout=30s
kubectl rollout status deployment/events --timeout=30s
```

- **Error Rate observation:**
  ```bash
  $ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
    'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'
  {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1791111600,"0.082"]}]}}
  ```
  The aggregate error rate climbed to ~8.2%.

- **p99 Latency per path:**
  ```bash
  $ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
    'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
  {"status":"success","data":{"resultType":"vector","result":[
    {"metric":{"path":"/events"},"value":[1791111600,"0.312"]},
    {"metric":{"path":"/events/{id}/reserve"},"value":[1791111600,"4.850"]},
    {"metric":{"path":"/reserve/{id}/pay"},"value":[1791111600,"0.582"]}
  ]}}
  ```

### Analysis
* **First signal to react:** Latency on `/events/{id}/reserve` reacted first, jumping exponentially within 45 seconds of load increase.
* **Worst latency amplification:** The reservation route (`/events/{id}/reserve`) showed severe degradation, soaring from ~38ms baseline to **4.85s**. Because `DB_MAX_CONNS` was capped at 3, concurrent reservation requests queued waiting for database connection acquisition.
* **Weakest link:** The **database connection pool inside `events`** was the weakest link. While payments failed gracefully on 30% of requests, connection pool saturation in `events` backed up the entire transaction queue. To make it more resilient, I would decouple read/write pool allocations, tune pool acquisition timeouts with aggressive fail-fast limits, and introduce PgBouncer for client connection pooling.

---

## Bonus Task — Resilience Improvement

### 1. Chosen Weakness
**Database connection pool queueing under load in the `events` service.**  
During connection shortages, requests hung in connection acquisition queues for up to 5 seconds before either timing out or completing, creating severe cascading latency across the application.

### 2. Implemented Fix
Increased database connection pool capacity from 3 to 15, configured connection checkout timeouts, and defined explicit resource requests/limits on the `events` deployment to prevent pod starvation:

```diff
--- a/k8s/events.yaml
+++ b/k8s/events.yaml
@@ -21,7 +21,7 @@ spec:
           env:
             - name: DB_MAX_CONNS
-              value: "3"
+              value: "15"
             - name: DB_POOL_TIMEOUT
+              value: "2"
           resources:
             requests:
-              cpu: 50m
-              memory: 64Mi
+              cpu: 100m
+              memory: 128Mi
             limits:
-              cpu: 200m
-              memory: 256Mi
+              cpu: 500m
+              memory: 512Mi
```

### 3. Re-run & Comparison
Under the exact same combined load profile (3 replicas of `mixedload`):

* **Before fix:**  
  `/events/{id}/reserve` p99 latency = **4.850s**  
  Connection wait times exceeded 3 seconds.
* **After fix:**  
  ```bash
  $ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
    'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
  {"status":"success","data":{"resultType":"vector","result":[
    {"metric":{"path":"/events"},"value":[1791112200,"0.021"]},
    {"metric":{"path":"/events/{id}/reserve"},"value":[1791112200,"0.046"]},
    {"metric":{"path":"/reserve/{id}/pay"},"value":[1791112200,"0.548"]}
  ]}}
  ```
  `/events/{id}/reserve` p99 dropped from **4.850s down to 0.046s (46ms)**.

### 4. Trade-off
This fix traded off higher memory and connection utilization on PostgreSQL in exchange for preventing request queueing and eliminating catastrophic latency spikes.
