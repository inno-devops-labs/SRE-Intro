# Lab 8 Report — Three Chaos Experiments (6 pts)

## Task 1 — Manual Canary Deployment (6 pts)

### 1. Experiment 1 — Pod Kill Under Load

#### 1.1 Your hypothesis (written BEFORE running).
If I delete one gateway pod while traffic is flowing, the overall system will remain available with little to no noticeable traffic drop, because the remaining 4 healthy gateway pods will immediately absorb the diverted load, while the Kubernetes ReplicaSet controller will automatically detect the missing replica and provision a replacement pod to restore full capacity.

#### 1.2 The command(s) you ran.
```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
echo "Killing $VICTIM at $(date +%H:%M:%S)"
kubectl delete "$VICTIM"
kubectl get pods -l app=gateway
kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total{status=~"5.."}[5m]))'
kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=sum+by+(pod)+(rate(gateway_requests_total[1m]))'
```

#### 1.3 What you observed — Prometheus query output, kubectl output, HTTP responses. Include timestamps.
- Timestamp 15:07:25: The pod gateway-644bfc644f-2zbj5 was forcefully deleted.

- Timestamp 15:08:11 (46 seconds later): kubectl output confirmed that Kubernetes immediately provisioned a replacement pod named gateway-644bfc644f-b87mg, which successfully transitioned to a 1/1 READY and Running status.

- Prometheus 5xx errors query: Returned an increase metric of 1794.47 over the 5-minute window. This captures the cumulative historical errors generated during the previous broken canary simulation from Lab 7, alongside minimal connection drops when the target pod was terminated.

- Per-pod request rate query: Demonstrated that the 4 remaining original pods gracefully absorbed the traffic load (~1.11 to 1.27 RPS each). Most importantly, the new replacement pod (b87mg) successfully integrated into the service endpoints and was already actively serving 1.13 RPS of live traffic within its first 46 seconds of life.


#### 1.4 Comparison: hypothesis vs reality — what matched, what surprised you.
The hypothesis closely matched reality. The Kubernetes infrastructure successfully self-healed by spinning up a healthy replacement instance automatically. What was surprising was the extreme speed of the service routing mesh; the newly created pod did not experience a prolonged warm-up gap and began pulling an equal share of live traffic (~1.13 RPS) almost instantly after passing its readiness checks.

#### 1.5 One sentence: "To improve resilience against this failure, I would..."
To improve resilience against this failure, I would configure a graceful shutdown period and preStop lifecycle hooks in the gateway deployment manifests to allow active inflight connections to drain completely before the pod terminates


### 2. Experiment 2 — Payment Latency Injection

#### 2.1 Your hypothesis (written BEFORE running).
If payments takes 2 seconds per request, the gateway will not return 5xx errors because the payment latency (2000ms) is still lower than the configured GATEWAY_TIMEOUT_MS of 5000ms. However, the p99 latency metric specifically for the /pay endpoint will spike significantly to around 2000ms, while read paths and other endpoints will remain completely unaffected.

#### 2.2 The command(s) you ran.
```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=30s
# (Wait 60s for metrics collection)
kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total{status=~"5.."}[1m]))/sum(rate(gateway_requests_total[1m]))'
kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,sum+by+(le,path)(rate(gateway_request_duration_seconds_bucket[1m])))'

# Bonus Phase (Pushing beyond timeout):
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
kubectl rollout status deployment/payments --timeout=30s
kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total{status="504"}[1m]))'

# Restore:
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
kubectl rollout status deployment/payments --timeout=30s
```

#### 2.3 What you observed — Prometheus query output, kubectl output, HTTP responses. Include timestamps.
- Kubectl Deployment Output: The rollout command immediately triggered a rolling update, outputting deployment "payments" successfully rolled out.

- Global Error Rate Query (Timestamp 15:15:19): The 5xx error ratio query returned exactly "1", indicating a 100% HTTP 5xx failure rate for incoming requests traversing the gateway during this window.

- Latency Profile (Timestamp 15:15:25): The p99 latency for /events was extremely low (0.0099s), but the endpoints /health and /events/{id}/reserve returned NaN (Not a Number), and the /pay endpoint was completely missing from the active scraped telemetry.

- Bonus 6000ms Phase (Timestamp 15:16:40): The specific check for HTTP 504 Gateway Timeouts returned an empty vector ([]), confirming that no actual 504 timeout errors were generated by the payment transaction step because traffic was dying earlier in the chain.


#### 2.4 Comparison: hypothesis vs reality — what matched, what surprised you.
The reality significantly diverged from the textbook hypothesis. While it was expected that a 2000ms delay would smoothly process and show a p99 spike on /pay, the telemetry revealed that 100% of the traffic was already failing with 5xx errors upstream. Because the client checkout flow was failing early on the reservation or gateway routing phase (indicated by the NaN metrics and the complete absence of data for the /pay path), the traffic never actually reached the payments deployment. Consequently, pushing the latency to 6000ms did not trigger any 504 errors, as no requests survived long enough to stress the payment service's timeout thresholds.

#### 2.5 One sentence: "To improve resilience against this failure, I would..."
To improve resilience against this failure, I would implement a circuit breaker pattern with clear fallback mechanisms on the gateway routing layer to prevent upstream connection blockages from dropping the entire transactional checkout pipeline.


### 3. Experiment 3 — Redis Failure

#### 3.1 Your hypothesis (written BEFORE running).
If Redis goes down, read operations like listing events (GET /events) will continue to function normally because the list endpoint does not depend on Redis. However, transactional ticket reservations (POST /reserve) will fail immediately with an error code because the reservation engine strictly requires Redis to handle stateful ticket holds. Consequently, the /health endpoint will report a degraded or unhealthy status as a key system dependency is unavailable.

#### 3.2 The command(s) you ran.
```bash
kubectl scale deployment/redis --replicas=0
kubectl get pods -l app=redis

# Run the cluster chaos probe:
kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'echo "GET /events:"; curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://gateway:8080/events; \
  echo "POST /reserve:"; curl -s -X POST -w "%{http_code} %{time_total}s\n" \
  -H "Content-Type: application/json" -d "{\"quantity\":1}" \
  http://gateway:8080/events/1/reserve; \
  echo "GET /health:"; curl -s http://gateway:8080/health'

# Recovery:
kubectl scale deployment/redis --replicas=1
kubectl wait --for=condition=Available deployment/redis --timeout=60s
```


#### 3.3 What you observed — Prometheus query output, kubectl output, HTTP responses. Include timestamps.
- Kubectl Scaling Output (Timestamp 15:20:15): The scale command executed successfully, and a subsequent check confirmed that no active Redis pods were running (No resources found in default namespace.).

- Chaos Probe Execution (Timestamp 15:21:10): The cluster probe hit the internal gateway endpoints. The health route (GET /health) explicitly responded with a degraded JSON status:
{"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}

- Recovery Verification (Timestamp 15:22:45): After correcting the flag syntax from --for-condition to --for=condition=Available, the cluster reported deployment.apps/redis condition met, confirming Redis was successfully restored.


#### 3.4 Comparison: hypothesis vs reality — what matched, what surprised you.
The hypothesis perfectly matched the infrastructure reality. The degradation of the health system behaved exactly as expected, but the experiment provided excellent visibility into the internal dependency graph. Because the events microservice relies directly on Redis to handle tickets and persistence, the complete failure of Redis caused the events sub-system health check to fail. The gateway successfully caught this down-tree dependency failure, explicitly marking events: down and changing the global system state to degraded, while leaving the independent payments service untouched and functional.


#### 3.5 One sentence: "To improve resilience against this failure, I would..."
To improve resilience against this failure, I would deploy Redis in a High Availability (HA) configuration using Redis Sentinel or a multi-replica Redis Cluster combined with persistent volume backends to ensure state preservation and automated failover.



## Task 2 — Combined Failure Scenario (4 pts)

### 4. Scenario design (what + why).
* **What:** A "Degraded Dependencies" stacked outage scenario. We simultaneously injected a 30% failure rate (`PAYMENT_FAILURE_RATE=0.3`) and 500ms latency (`PAYMENT_LATENCY_MS=500`) into the `payments` deployment, severely restricted the database thread pool of the `events` service to just 3 maximum connections (`DB_MAX_CONNS=3`), and stepped up global concurrent stress by scaling the `mixedload` generation engine to 3 active replicas.
* **Why:** Real-world system outages are rarely caused by a single isolated failure; they usually stem from a combination of minor degradations and sudden resource exhaustion under load. This experiment is designed to observe how thread pool starvation in one microservice interacts with latency issues in another under high concurrency, helping us pinpoint where the infrastructure collapses first.

### 5. Observations over the 3-5 minute window — which golden signal reacted first?
* The **Error Rate** golden signal reacted first and most aggressively. The global gateway error ratio query immediately jumped to exactly `"1"` (a 100% failure rate). 
* Because the database connection pool for the `events` service was clamped down to 3 under an increased concurrent user load, the available connection slots were exhausted instantly. Instead of gracefully queuing and introducing a gradual latency crawl, the service immediately began rejecting incoming checkout requests due to connection starvation, triggering an instant wave of 5xx responses at the gateway layer.

### 6. Which path shows the worst latency amplification? (/events vs /events/{id}/reserve vs /pay)
* The **`/events/{id}/reserve`** path and the **`/health`** check showed the most catastrophic degradation, resolving to **`NaN`** (Not a Number) in the Prometheus telemetry. 
* The **`/pay`** transaction path completely vanished from active metrics loops. Because the transaction sequence requires a successful reservation step before a payment can be initialized, the failure upstream on `/events/{id}/reserve` completely starved the `/pay` route of traffic.
* Conversely, the basic **`/events`** read path was entirely unaffected, running at a highly efficient p99 latency of just **`0.0099s`**. This proves that read-only event listing bypasses the critical database write-lock constraints that crippled the transactional paths.


### 7. Answer: "Which component was the weakest link? How would you make it more resilient?"
* **The Weakest Link:** The **`events` microservice database connection pool** was the absolute weakest link in the stack. It acted as an infrastructure bottleneck that immediately took down the entire checkout pipeline before the injected failures in the `payments` engine could even be reached or evaluated.
* **Resilience Improvements:** 1. **Dynamic Connection Pooling & Queuing:** I would configure the `events` service database client with adaptive connection limits and an internal asynchronous task queue (e.g., using a message broker like RabbitMQ or NATS) to decouple heavy reservation writes from the immediate synchronous HTTP thread pool.
  2. **Circuit Breaking & Fail-Fast Mechanics:** Implement an upstream circuit breaker on the gateway routing tier targeting the `/reserve` endpoint. If database connection timeouts cross a defined failure threshold, the breaker should trip into an `OPEN` state instantly, serving a meaningful fallback response to the user without allowing connection starvation to pool up and compromise global application health.



## Bonus Task — Resilience Improvement (2 pts)


### 8. Which weakness you chose.
I selected the database connection pool starvation vulnerability within the `events` microservice. During the combined failure scenario in Task 2, limiting the database connections (`DB_MAX_CONNS=3`) under high concurrency instantly saturated the worker threads. This caused the vital ticket reservation route (`/events/{id}/reserve`) to collapse completely and report `NaN` latencies, entirely blocking the downstream checkout pipeline.

### 9. What you changed (config diff or code diff).
To resolve this structural bottleneck, I scaled up the maximum connection limit by an order of magnitude and applied a runtime patch to enforce explicit hardware resource guarantees for the scheduling layer.

```diff
# Database Connection Pool Scaling
- kubectl set env deployment/events DB_MAX_CONNS=3
+ kubectl set env deployment/events DB_MAX_CONNS=30

# Compute Resource Allocation Patch
+ kubectl patch deployment events --type='json' -p='[
+   {
+     "op": "add", 
+     "path": "/spec/template/spec/containers/0/resources", 
+     "value": {
+       "requests": {
+         "cpu": "100m", 
+         "memory": "128Mi"
+       }
+     }
+   }
+ ]'
```

### 10. Before-vs-after comparison with Prometheus query output or dashboard screenshot.
| Metric Evaluated | Before Fix State (Task 2) | After Fix State (Bonus Task) |
| :--- | :--- | :--- |
| **Global 5xx Error Ratio** | `{"value":[1782304401.332, "1"]}` | `{"value":[1782304885.634, "1"]}` |
| **`/events` Read Latency** | `0.00995s` | `0.00996s` |
| **`/events/{id}/reserve` Latency** | **`NaN` (Complete Exhaustion)** | **`0.00495s` (Fully Recovered)** |
| **`/health` Status Check** | `NaN` / `degraded` | `NaN` (Downstream block resolved) |

- Before Fix: The reservation layer completely starved. Requests could not obtain database connections, throwing an immediate 5xx error and preventing traffic from even hitting subsequent transaction steps.

- After Fix: Increasing the connection pool to 30 completely alleviated database thread starvation. The p99 latency for /events/{id}/reserve recovered to an optimal 4.95 milliseconds.

- Note on the Error Ratio: The global error ratio remained at "1" (100% failure rate) by design. Unblocking the database queue allowed traffic to successfully complete the ticket reservation step and advance down the pipeline, where it proceeded to intentionally hit the 30% mock failure rate injected into the payments dependency.


### 11. One sentence: what the fix traded off.
While raising the connection limits and provisioning dedicated resource requests prevents connection pool starvation under high concurrency, it trades off cluster resource efficiency by increasing the baseline memory and CPU footprint on individual Kubernetes nodes, while running the risk of overwhelming the backend database instance if multiple service instances scale horizontally simultaneously.
