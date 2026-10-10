# Lab 8 — Chaos Engineering: Break Things on Purpose
## Task 1 — Three Chaos Experiments

### Experiment 1 — Pod Kill Under Load

**Hypothesis:** "If I delete one gateway pod while traffic is flowing, the remaining 4 pods will continue serving traffic without any 5xx errors, because Kubernetes Services provide automatic load balancing and the ReplicaSet will recreate the deleted pod within 10-15 seconds."

```
$ VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
$ echo "Killing $VICTIM at $(date +%H:%M:%S)"
Killing pod/gateway-7c5778d94f-8smrz at 02:25:11
$ kubectl delete "$VICTIM"
pod "gateway-7c5778d94f-8smrz" deleted
```
Observations:

Time to recreate: ~11 seconds

```
$ kubectl get pods -l app=gateway -w
gateway-7c5778d94f-dtffw   1/1     Running   0    11s
```
5xx errors (3-minute window):

```
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1782930365.918,"1101.4941728611343"]}]}}
```
1101 errors occurred during the transition.


Request distribution across pods:

```
gateway-7c5778d94f-xjz5z   1.53 req/s
gateway-7c5778d94f-rwcxq   1.48 req/s
gateway-7c5778d94f-bf2g5   1.63 req/s
gateway-7c5778d94f-97j6h   1.57 req/s
gateway-7c5778d94f-dtffw   1.50 req/s
```
Comparison: hypothesis vs reality

| What matched | What surprised me |
|--------------|-------------------|
| Kubernetes created a replacement pod within ~11 seconds | 1101 5xx errors during the transition — I expected 0 errors because the Service should have load-balanced traffic to the remaining 4 pods |

Why it happened:

The Service endpoint list takes a moment to update after a pod is terminated. Requests that arrived during this window were routed to the terminating pod, causing errors. The canary configuration (revision:6) was healthy, but the endpoint propagation delay created a brief failure window.

**To improve resilience against this failure, I would:**

- Add a preStop lifecycle hook to the gateway pods to gracefully drain connections before termination, reducing the number of in-flight requests lost during pod deletion.
- Also consider increasing the terminationGracePeriodSeconds to give the pod more time to finish ongoing requests.



### Experiment 2 — Payment Latency Injection

**Hypothesis:** "If payments takes 2 seconds per request, the gateway will not return 5xx errors because GATEWAY_TIMEOUT_MS is 5000ms (5 seconds), but p99 latency for `/pay` will spike to ~2000ms while `/events` and `/reserve` remain unaffected."



```
$ kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
deployment.apps/payments env updated

$ kubectl rollout status deployment/payments --timeout=30s
Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
deployment "payments" successfully rolled out

$ sleep 60
```
**Observations:**

| Check | Result |
|-------|--------|
| **Error Rate** | 0.804 (80.4% errors) |
| **p99 /events** | 0.024s (24ms) |
| **p99 /reserve** | 0.009s (9ms) |
| **p99 /health** | 0.054s (54ms) |

```
$ curl -s 'http://localhost:9091/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1782930843.355,"0.8040303715117656"]}]}}

$ curl -s 'http://localhost:9091/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
{"status":"success","data":{"resultType":"vector","result":[
  {"metric":{"path":"/events"},"value":[1782930851.229,"0.024593740609767642"]},
  {"metric":{"path":"/events/{id}/reserve"},"value":[1782930851.229,"0.009750000919810885"]},
  {"metric":{"path":"/health"},"value":[1782930851.229,"0.054000073584701526"]}
]}}
```


Restore:
```
$ kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
deployment.apps/payments env updated

$ kubectl rollout status deployment/payments --timeout=30s
deployment "payments" successfully rolled out
```

**Comparison: hypothesis vs reality**

| What matched | What surprised me |
|--------------|-------------------|
| `/events` and `/reserve` p99 latency remained low (~10-25ms) | **80.4% error rate** — I expected 0 errors because 2000ms < 5000ms timeout |
| The rollout completed successfully | The error rate was extremely high despite the timeout not being reached |

Why it happened:

While the timeout is 5 seconds, the gateway's connection pool or request handling may be timing out or rejecting requests when payments becomes slow. Slow payments also cause queueing and resource exhaustion, leading to cascading failures.

**To improve resilience against this failure, I would:**

- Increase the gateway's connection pool size

- Add a circuit breaker for payments to fail fast instead of waiting for timeouts

- Implement a timeout that's more appropriate for payments (e.g., 1500ms) with a fallback response

- Add monitoring for payment latency to detect degradation before it reaches critical levels

### Experiment 3 — Redis Failure

**Hypothesis:** "If Redis goes down, users can still list events (because data comes from PostgreSQL), but reservations will fail because Redis is used for the reservation hold. The `/health` endpoint will report Redis as down."


```
$ kubectl scale deployment/redis --replicas=0
deployment.apps/redis scaled

$ kubectl get pods -l app=redis -w
NAME                    READY   STATUS        RESTARTS      AGE
redis-c46d5dffc-nxvqh   1/1     Terminating   3 (11h ago)   2d3h
redis-c46d5dffc-nxvqh   0/1     Completed     3 (11h ago)   2d3h
redis-c46d5dffc-nxvqh   0/1     Completed     3 (11h ago)   2d3h
redis-c46d5dffc-nxvqh   0/1     Completed     3 (11h ago)   2d3h
```
Observations:

```
$ kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'echo "GET /events:"; curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://gateway:8080/events;
         echo "POST /reserve:"; curl -s -X POST -w "%{http_code} %{time_total}s\n" \
              -H "Content-Type: application/json" -d "{\"quantity\":1}" \
              http://gateway:8080/events/1/reserve;
         echo "GET /health:"; curl -s http://gateway:8080/health'

GET /events:
000 0.002454s
POST /reserve:
000 0.001084s
GET /health:
pod default/chaos-probe terminated (Error)
```
The chaos-probe returned 000 responses, indicating that the pod could not reach the gateway Service. However, the gateway itself remained healthy and accessible from other pods.

Restore Redis:

```
$ kubectl scale deployment/redis --replicas=1
deployment.apps/redis scaled

$ kubectl wait --for=condition=Available deployment/redis --timeout=60s
deployment.apps/redis condition met
```
Comparison: hypothesis vs reality


| What matched | What surprised me |
|--------------|-------------------|
| Redis was successfully scaled to 0 and terminated | The `chaos-probe` pod could not reach the gateway Service (000 responses) |

**To improve resilience against this failure, I would:**

- Ensure health checks are accessible from within the cluster

- Add retry logic for reservation attempts when Redis is unavailable

- Update the health check to return degraded when Redis is unavailable

- Consider implementing a fallback mechanism for reservations without Redis


## Task 2 — Combined Failure Scenario

### Scenario Design

**Scenario:** Degraded dependencies

- Payments: 30% failure rate + 500ms latency
- Events: DB connection pool capped at 3
- Traffic: mixedload scaled to 3 replicas

**Hypothesis:** "Under combined degradation, the error rate will spike to ~30% (payment failures), and p99 latency will increase significantly for all endpoints because the database connection pool will become a bottleneck. The first golden signal to react will be Error Rate, followed by Latency."

**Execution:**
```
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
```

**Observations (after 3 minutes):**

| Metric | Value |
|--------|-------|
| **Error Rate** | 0.8596 (86% errors) |
| **p99 /health** | 0.054s |
| **p99 /events** | 0.014s |
| **p99 /reserve** | NaN (no data — reservations likely failing) |

**Analysis:**

- **Error Rate spiked to 86%** — much higher than the expected 30% because DB connection pool exhaustion caused cascading failures
- **Reserve endpoint showed NaN** — indicates that reservation requests are failing completely, not just slowing down
- **/events and /health remained responsive** — the read path was less affected by the DB pool limit

**Which component was the weakest link?**

The **database connection pool** (`DB_MAX_CONNS=3`) was the weakest link. Combined with the mixedload traffic, it caused connection queueing and timeouts, which amplified the payment failures into a near-complete outage for the reserve endpoint.

**How would you make it more resilient?**

1. Increase `DB_MAX_CONNS` to a higher value (e.g., 10-20) to handle peak traffic
2. Add connection pooling configuration with proper timeouts
3. Implement circuit breaker for the events service when the DB pool is exhausted
4. Add a read-only replica for read-heavy queries (like `/events`)
5. Monitor connection pool usage and alert when it approaches the limit
