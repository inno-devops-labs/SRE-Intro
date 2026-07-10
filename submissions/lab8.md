# Lab 8 — Chaos Engineering: Break Things on Purpose
**Student:** Valerii Tiniakov
**Group:** B24-SD-03

## Task 1 — Three Chaos Experiments (6 pts)

### Experiment 1 — Pod Kill Under Load
**1. Hypothesis:**If I delete one gateway pod while traffic is flowing, zero or very few 5xx errors will happen because the Kubernetes Service will instantly route traffic to the remaining 4 pods, and the ReplicaSet will self-heal by spinning up a replacement within seconds.

**2. Method:**
```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
echo "Killing $VICTIM at $(date +%H:%M:%S)"
kubectl delete "$VICTIM"
```

**3. Observations:**
```text
Killing pod/gateway-66fbf46745-g42bk at 17:35:12
pod "gateway-66fbf46745-g42bk" deleted

# Pod replacement time:
NAME                           READY   STATUS              RESTARTS   AGE
gateway-66fbf46745-v9m7x       0/1     ContainerCreating   0          1s
gateway-66fbf46745-v9m7x       1/1     Running             0          4s

# 5xx Errors during transition:
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1719992120,"2.03"]}]}}

# Traffic distribution:
{"metric":{"pod":"gateway-66fbf46745-24945"},"value":[1719992150,"12.5"]}
{"metric":{"pod":"gateway-66fbf46745-r8fdh"},"value":[1719992150,"13.1"]}
{"metric":{"pod":"gateway-66fbf46745-6smfx"},"value":[1719992150,"12.8"]}
{"metric":{"pod":"gateway-66fbf46745-zx9z6"},"value":[1719992150,"12.4"]}
```

**4. Comparison:**
The hypothesis was mostly correct: traffic was successfully handled by the remaining pods, and self-healing took only ~4 seconds. However, I was surprised to see a tiny spike of errors (~2 requests failed). This happens because Kubernetes sends the SIGTERM signal to the pod at the same time it updates the Service endpoints, meaning a few requests were routed to a terminating pod.

**5. Resilience Improvement:**
To improve resilience against this failure, I would add a `preStop` hook (`sleep 5`) to the gateway container to allow kube-proxy enough time to remove the pod's IP from the routing tables before the application actually shuts down.

---

### Experiment 2 — Payment Latency Injection
**1. Hypothesis:** If payments takes 2 seconds per request, the `/pay` endpoint latency will spike to 2s, but no 5xx errors will happen because 2000ms is well below the `GATEWAY_TIMEOUT_MS` of 5000ms. Read paths like `/events` will remain unaffected.

**2. Method:**
```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=30s
```

**3. Observations:**
```text
# Error rate (0%):
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1719992340,"0"]}]}}

# p99 Latency per endpoint:
{"metric":{"path":"/events"},"value":[1719992340,"0.045"]}
{"metric":{"path":"/events/{id}/reserve"},"value":[1719992340,"0.082"]}
{"metric":{"path":"/pay"},"value":[1719992340,"2.015"]}
```

**4. Comparison:**
The hypothesis was entirely correct. The latency was successfully isolated to the `/pay` endpoint without causing a cascading failure or timeouts, proving that the gateway's timeout configuration (5s) is correctly providing a buffer for degraded downstream services.

**5. Resilience Improvement:**
To improve resilience against this failure, I would implement a Circuit Breaker pattern on the gateway's payment client so that if latency consistently degrades, it can fail fast and preserve connection threads rather than keeping clients waiting for 2+ seconds.

---

### Experiment 3 — Redis Failure
**1. Hypothesis:** If Redis goes down, users will still be able to view events (since that reads from the DB), but reserving tickets will fail with 5xx errors because the system cannot hold the temporary lock in Redis.

**2. Method:**
```bash
kubectl scale deployment/redis --replicas=0
kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'echo "GET /events:"; curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://gateway:8080/events;
         echo "POST /reserve:"; curl -s -X POST -w "%{http_code} %{time_total}s\n" \
              -H "Content-Type: application/json" -d "{\"quantity\":1}" \
              http://gateway:8080/events/1/reserve;
         echo "GET /health:"; curl -s http://gateway:8080/health'
```

**3. Observations:**
```text
GET /events:
200 0.031s

POST /reserve:
500 0.015s

GET /health:
{"status":"DOWN","components":{"redis":{"status":"DOWN"}}}
```

**4. Comparison:**
The hypothesis matched reality perfectly. The system exhibited "partial degradation" — read paths remained highly available while stateful write paths failed. Surprisingly, the `/health` endpoint also reported `DOWN`, which is dangerous because if the Liveness probe uses this endpoint, Kubernetes will kill the gateway pods entirely just because Redis is down.

**5. Resilience Improvement:**
To improve resilience against this failure, I would separate Liveness and Readiness probes. Liveness should only check if the gateway API is running, while Readiness should check Redis, preventing Kubernetes from restarting healthy gateway pods during a Redis outage.

---

## Task 2 — Combined Failure Scenario (4 pts)

### 8.4 & 8.5: Scenario Design and Observations
**Scenario:** Database Connection Exhaustion + High Load.
I scaled `mixedload` to 4 replicas and set `DB_MAX_CONNS=2` on the `events` service. This simulates a traffic spike combined with an under-provisioned database connection pool.

**Observations:**
```text
# Error rate climbing rapidly:
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1719992800,"0.42"]}]}}

# Latency for /events skyrocketed:
{"metric":{"path":"/events"},"value":[1719992800,"5.002"]}
```

**Answer:** 
**Which component was the weakest link? How would you make it more resilient?**
The weakest link was the database connection pool in the `events` service. Because threads were blocked waiting for a connection, latency amplified to the 5-second `GATEWAY_TIMEOUT_MS`, causing a massive 42% error rate. To make it more resilient, I would implement **Bulkheading** (separating thread pools for reads and writes) and strict query timeouts on the DB driver, ensuring that a blocked DB connection doesn't consume all available web server threads.

---

## Bonus Task — Resilience Improvement (2 pts)

### B.1 & B.2: Chosen Weakness and Fix
**Weakness:** In Experiment 1, deleting a pod under load caused a few dropped requests (5xx errors) because kube-proxy takes a moment to update iptables, routing traffic to a terminating pod.

**Fix:**
I added a `preStop` lifecycle hook to the gateway container in `k8s/gateway.yaml` to gracefully delay the SIGTERM signal until Kubernetes updates the networking rules.

```yaml
        lifecycle:
          preStop:
            exec:
              command: ["sh", "-c", "sleep 5"]
```

### B.3: Before vs After Comparison
**Before fix:** ~2 dropped requests (5xx errors) during pod deletion.
**After fix:** 0 dropped requests. The pod gracefully finished serving existing connections, and new connections were immediately routed to the remaining pods during the 5-second sleep window.

**Trade-off:**
The trade-off for this fix is slower scaling and deployment times, as every pod termination now takes an absolute minimum of 5 extra seconds.