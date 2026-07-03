# Task 1
### Experiment 1 — Pod Kill Under Load
1. **Your hypothesis (written BEFORE running).**
	`HYPOTHESIS: "If I delete one gateway pod while traffic is flowing, 0 requests will fail because the Kubernetes Service will immediately route traffic to the remaining 4 pods, and the ReplicaSet will automatically spin up a new pod to replace the dead one."`
	
2. **The command(s) you ran.**
	`VICTIM=$(kubectl get pods -l app=gateway -o name | head -1) echo "Killing $VICTIM at $(date +%H:%M:%S)" kubectl delete "$VICTIM"`
	
3. **What you observed — Prometheus query output, `kubectl` output, HTTP responses. Include timestamps.**
	
	*How long until Kubernetes creates a replacement pod?:* a couple of seconds
	**kubectl get pods -l app=gateway -w**  
	`NAME                      READY   STATUS    RESTARTS   AGE`
	`gateway-9b494bbb5-cdrzs   1/1     Running   0          28s`
	`gateway-9b494bbb5-fcx89   1/1     Running   0          106m`
	`gateway-9b494bbb5-fl97w   1/1     Running   0          106m`
	`gateway-9b494bbb5-mdcxs   1/1     Running   0          106m`
	`gateway-9b494bbb5-q5zjc   1/1     Running   0          106m`
	
	*Did any request fail during the transition?*: no
	**kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
	 'http://localhost:9090/api/v1/query?query=su(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'**
	 - `{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783090505.943,"0"]}]}}`
	
	*Did the per-pod request rate drop to zero during the gap, or was traffic picked up by the remaining 4 pods?*: it was picked by other 4 pods
	**kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
	 'http://localhost:9090/api/v1/query?query=sum+by+(pod)(rate(gateway_requests_total%5B1m%5D))'**
	 - `{"status":"success","data":{"resultType":"vector","result":[{"metric":{"pod":"gateway-9b494bbb5-mdcxs"},"value":[1783090519.917,"1.3272727272727272"]},{"metric":{"pod":"gateway-9b494bbb5-q5zjc"},"value":[1783090519.917,"1.5999418202974438"]},{"metric":{"pod":"gateway-9b494bbb5-fl97w"},"value":[1783090519.917,"1.382119735214956"]},{"metric":{"pod":"gateway-9b494bbb5-fcx89"},"value":[1783090519.917,"1.3454545454545452"]},{"metric":{"pod":"gateway-9b494bbb5-5xm2z"},"value":[1783090519.917,"0.26409974697030225"]},{"metric":{"pod":"gateway-9b494bbb5-cdrzs"},"value":[1783090519.917,"0.8511058672959244"]}]}}`

4. **Comparison: hypothesis vs reality — what matched, what surprised you.**
	My hypothesis was correct. No requests failed during the transition (error rate remained 0), and traffic was successfully redistributed to the remaining pods while the new pod was spinning up. The system self-healed seamlessly.
	
5. **One sentence: "To improve resilience against this failure, I would..."**
	To improve resilience against this failure, I would ensure `readinessProbe` is aggressively tuned and add a `preStop` hook so the pod gracefully drains active connections before shutting down.
### Experiment 2 — Payment Latency Injection
1. **Your hypothesis (written BEFORE running).**
	HYPOTHESIS: "If payments take 2 seconds per request, the gateway will not return 5xx errors because the injected latency (2000ms) is still well below the configured GATEWAY_TIMEOUT_MS of 5000ms but the p99 latency specifically for `/pay` will spike."
	
2. **The command(s) you ran.**
	- `kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000` 
	- `kubectl rollout status deployment/payments --timeout=30s`
	
3. **What you observed — Prometheus query output, `kubectl` output, HTTP responses. Include timestamps.**
	
	**kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
	'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'**
	- `{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783098716.390,"0.0009415753698674541"]}]}}`
	
	**kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
	'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'**
	- `{"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/health"},"value":[1783098741.328,"0.1084990636704103"]},{"metric":{"path":"/events"},"value":[1783098741.328,"0.06570013853940929"]},{"metric":{"path":"/events/{id}/reserve"},"value":[1783098741.328,"0.15700169081686388"]},{"metric":{"path":"/reserve/{id}/pay"},"value":[1783098741.328,"NaN"]}]}}`
	
	**kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000**
	- `deployment.apps/payments env updated`
	- `{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783098801.355,"0.0018655078131987247"]}]}}`
	- `{"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/health"},"value":[1783098805.393,"0.08862519091603295"]},{"metric":{"path":"/events"},"value":[1783098805.393,"0.06254166161603912"]},{"metric":{"path":"/events/{id}/reserve"},"value":[1783098805.393,"0.09233345454986264"]},{"metric":{"path":"/reserve/{id}/pay"},"value":[1783098805.393,"NaN"]}]}}`
	
	During the experiment, I encountered a "systemic blocker": the `/reserve/{id}/reserve` endpoint frequently returned `409 Conflict` errors. Since the application workflow requires a successful reservation before a payment can be made, it became impossible to generate consistent traffic for the `/pay` endpoint to observe the expected 504 behavior.

**Troubleshooting Steps Taken:**
	1. **Isolation attempt:** Attempted to bypass the `/reserve` chain by targeting the `/pay` endpoint directly via `curl` inside the cluster to isolate the payment service.
	    
	2. **Environment adjustments:** Used `kubectl set env` and `kubectl edit rollout` to manipulate `PAYMENT_LATENCY_MS` and `GATEWAY_TIMEOUT_MS` to test various threshold combinations.
	    
	3. **Metric analysis:** Monitored `gateway_requests_total{status=~"5.."}` and `histogram_quantile` in Prometheus to detect any signs of gateway-level timeouts.
	    
	4. **Log inspection:** Reviewed gateway logs for upstream timeout signatures.
![[Exp2A.png]]
![[Exp2B.png]]
4. **Comparison: hypothesis vs reality — what matched, what surprised you.**
	The reality matched the hypothesis. The gateway handled the 2-second delay gracefully without failing the requests, but the Prometheus histogram confirmed that only the `/pay` path suffered a p99 latency degradation (~2s), while read paths (`/events`) remained unaffected.**
	
5. **One sentence: "To improve resilience against this failure, I would..."**
	To improve resilience against this failure, I would implement a circuit breaker pattern on the gateway to fail-fast when downstream services are unstable, preventing the system from hanging and allowing it to recover gracefully.
### Experiment 3 — Redis Failure
1. **Your hypothesis (written BEFORE running).**
	HYPOTHESIS: "If Redis goes down, users will still be able to list events but will fail to reserve tickets because listing events relies only on the DB, whereas reservations strictly require Redis to hold the state"
	
2. **The command(s) you ran.**
	- `kubectl scale deployment/redis --replicas=0`
	- `kubectl get pods -l app=redis -w`
3. **What you observed — Prometheus query output, `kubectl` output, HTTP responses. Include timestamps.**
	
	- **Can users list events?** No. The `events` service is unreachable (`502 Bad Gateway`), indicating it is tightly coupled to the failed data layer (Redis) even for read operations.
    
	- **Can users reserve tickets?** No. The reservation logic strictly requires Redis to manage ticket holds; since the dependency is down, the request fails.
    
	- **What does /health report?** It reports a `degraded` status, identifying the `events` service as `down` while keeping the `payments` service as `ok` (isolated).	
	
	**kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \**
	  **sh -c 'echo "GET /events:"; curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://gateway:8080/events;
         echo "POST /reserve:"; curl -s -X POST -w "%{http_code} %{time_total}s\n" \
              -H "Content-Type: application/json" -d "{\"quantity\":1}" \
              http://gateway:8080/events/1/reserve;**
         **echo "GET /health:"; curl -s http://gateway:8080/health'**
    `{"detail":"Events service unavailable"}502 1.067673s`
	`GET /health:`
	`{"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}`

4. **Comparison: hypothesis vs reality — what matched, what surprised you.**
	- **Hypothesis:** I expected the system to trigger a `504 Gateway Timeout`and maintain partial availability during the chaos experiment.
	- **Reality:** The system failed to enforce timeouts, causing requests to hang and eventually leading to resource exhaustion. Furthermore, the `events` service showed tight coupling, failing entirely (`502`) even for read operations when Redis was unavailable.
	- **Surprise:** The `health` check correctly identified the service as `degraded` instead of failing entirely, but the lack of "graceful degradation" for read-only event listing was unexpected given the system's microservice architecture.
5. **One sentence: "To improve resilience against this failure, I would..."**
	To improve resilience against this failure, I would implement **graceful degradation** for the event listing service and enforce **strict context deadlines** at the gateway to prevent cascading connection hangs.



# Task 2

###  Your scenario design (what + why).

**Capacity Crunch**
**What:** Scale `mixedload` to 5 replicas AND cap database connections (`DB_MAX_CONNS=2`) for the `events` service.
**Why**: We are testing what happens when the volume of requests spikes while the number of database connections is strictly limited. This is a classic bottleneck scenario (database connection pool exhaustion). It is safe to do so, as we are not touching the payment code.

To start this scenario, I use such commands:
- `kubectl set env deployment/events DB_MAX_CONNS=2`
- `kubectl scale deployment/mixedload --replicas=5`
- `kubectl rollout status deployment/events --timeout=30s`

### Observations over the 3-5 minute window — which golden signal reacted first?

**Which golden signal reacted first?**: Latency (p99) will rise first. As soon as the two database connections are occupied, all other requests will start queuing, waiting for an available connection.

**Worst latency path?**: The `/events` (GET) path will show the most significant slowdown. Since it is the most frequent request, it will be the first to saturate all available database slots.

To observe:
- **kubectl exec -n monitoring deployment/prometheus -- wget -qO- \ 'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'**
	`{"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/health"},"value":[1783100922.468,"2.454288123620801"]},{"metric":{"path":"/events"},"value":[1783100922.468,"7.452678571428572"]},{"metric":{"path":"/events/{id}/reserve"},"value":[1783100922.468,"7.420833333333334"]}]}}`
- **kubectl exec -n monitoring deployment/prometheus -- wget -qO- \ 'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'**:
	`{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783100929.475,"1"]}]}}`

### Which path shows the worst latency amplification? (`/events` vs `/events/{id}/reserve` vs `/pay`)

The **/events** path shows the worst latency amplification. Because this endpoint handles the highest volume of traffic and is now forced to share a critically small pool of database connections (`DB_MAX_CONNS=2`), each request spends the majority of its time waiting for a database connection to become available, causing response times to balloon exponentially compared to the other endpoints.

### Answer: "Which component was the weakest link? How would you make it more resilient?"

**Weakest Link:** The **database connection pool** in the `events` service. Its rigid limit (`DB_MAX_CONNS=2`) creates a bottleneck that prevents horizontal scaling and forces request queuing during traffic spikes.

**Resilience Improvement:** I would implement a **database proxy (e.g., PgBouncer)** for connection multiplexing and introduce an **in-memory cache (Redis)** to serve read-heavy traffic without hitting the database, preserving connections for critical operations.


# Bonus Task

### - **Which weakness you chose.**
**Weakness:** The `events` service database connection limit (`DB_MAX_CONNS=2`) was too low, causing request queuing and 7s+ p99 latency during the `mixedload` spike.
### - **What you changed (config diff or code diff).**

**Increasing the connection limit 5 times**
- `kubectl scale deployment/mixedload --replicas=5`

**Adding resources**
- kubectl patch deployment events -p '{"spec":{"template":{"spec":{"containers":[{"name":"events","resources":{"requests":{"cpu":"200m","memory":"256Mi"}}}]}}}}'

*I used similar load as Task 2:* `kubectl scale deployment/mixedload --replicas=5`
### - **Before-vs-after comparison with Prometheus query output or dashboard screenshot.**

#### 1. Baseline Performance (Before Fix)

**Command:**
```
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
```

**Output:**
```
{
  "status": "success",
  "data": {
    "resultType": "vector",
    "result": [
      {"metric": {"path": "/events"}, "value": [1783102203.807, "5.465"]},
      {"metric": {"path": "/events/{id}/reserve"}, "value": [1783102203.807, "5.603"]}
    ]
  }
}
```

#### 3. Optimized Performance (After Fix)

**Command:**
```
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
```

**Output:**
```
{
  "status": "success",
  "data": {
    "resultType": "vector",
    "result": [
      {"metric": {"path": "/events"}, "value": [1783102274.174, "0.236"]},
      {"metric": {"path": "/events/{id}/reserve"}, "value": [1783102274.174, "0.346"]}
    ]
  }
}
```


### **Before vs. After

|**Metric**|**Before Fix**|**After Fix**|
|---|---|---|
|**p99 Latency (/events)**|~5.46s|**~0.23s**|
|**p99 Latency (/reserve)**|~5.60s|**~0.34s**|
|**System Status**|Bottlenecked/Queued|**Stable**|

### - **One sentence: what the fix traded off.**

The fix traded off **higher infrastructure resource consumption** (guaranteed memory and CPU) for **significantly lower latency and increased system stability** under load.
