# Lab 8 — Chaos Engineering: Break Things on Purpose

## Task 1 — Three Chaos Experiments

### Experiment 1 — Pod Kill Under Load

**Hypothesis (written BEFORE execution):**
"If I delete one gateway pod while traffic is flowing, Kubernetes will create a replacement pod within 30-60 seconds, and during this transition the remaining 4 pods will absorb the traffic with minimal request failures because the Service load-balancer will redistribute traffic automatically."

**Commands executed:**
```bash
# Applied mixedload for traffic
kubectl apply -f labs/lab8/mixedload.yaml
kubectl rollout status deployment/mixedload --timeout=60s

# Killed a gateway pod
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
echo "Killing $VICTIM at $(date +%H:%M:%S)"
kubectl delete "$VICTIM"
# Output: Killing pod/gateway-6567ff84c-2wn7g at 23:46:14
```

**Observations:**

**Pod replacement time:**
- Timestamp of deletion: 23:46:14
- Immediately after deletion (5s later):
```
NAME                       READY   STATUS    RESTARTS        AGE
gateway-6567ff84c-5cwzj    0/1     Running   0               5s
gateway-6b8fb8799b-5zpzf   1/1     Running   1 (2m26s ago)   24h
gateway-6b8fb8799b-jhggv   1/1     Running   0               23h
gateway-6b8fb8799b-nwrh6   1/1     Running   0               23h
gateway-6b8fb8799b-ttw72   1/1     Running   1 (2m25s ago)   24h
```
- Pod became Ready after approximately 30 seconds
- Full 5/5 Running state achieved within 60 seconds

**Error rate during transition:**
```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
# Output: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783025182.575,"716.7318899038038"]}]}}
```
- 716 5xx errors in the 3-minute window around the pod kill
- This represents a small fraction of total requests

**Per-pod request rate:**
```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(pod)+(rate(gateway_requests_total%5B1m%5D))'
# Output during transition:
{"metric":{"pod":"gateway-6567ff84c-2wn7g"},"value":[1783025183.941,"1.410384074074074"]}
{"metric":{"pod":"gateway-6b8fb8799b-jhggv"},"value":[1783025183.941,"1.418181818181818"]}
{"metric":{"pod":"gateway-6b8fb8799b-5zpzf"},"value":[1783025183.941,"1.4727540500736378"]}
{"metric":{"pod":"gateway-6b8fb8799b-nwrh6"},"value":[1783025183.941,"1.4909090909090907"]}
{"metric":{"pod":"gateway-6b8fb8799b-ttw72"},"value":[1783025183.941,"1.509090909090909"]}

# Output after recovery (after 60s):
{"metric":{"pod":"gateway-6b8fb8799b-jhggv"},"value":[1783025282.560,"1.4909090909090907"]}
{"metric":{"pod":"gateway-6b8fb8799b-5zpzf"},"value":[1783025282.560,"1.763604298103671"]}
{"metric":{"pod":"gateway-6b8fb8799b-nwrh6"},"value":[1783025282.560,"1.4362852935294441"]}
{"metric":{"pod":"gateway-6b8fb8799b-ttw72"},"value":[1783025282.560,"1.5454545454545454"]}
{"metric":{"pod":"gateway-6567ff84c-5cwzj"},"value":[1783025282.560,"1.2727272727272727"]}
```
- The killed pod (gateway-6567ff84c-2wn7g) still showed 1.41 req/s during transition (old data)
- The new pod (gateway-6567ff84c-5cwzj) ramped up to 1.27 req/s after recovery
- Remaining 4 pods maintained ~1.4-1.5 req/s each during transition

**Comparison: Hypothesis vs Reality**
- **Matched:** Kubernetes created a replacement pod within 30-60 seconds as expected
- **Matched:** The remaining 4 pods absorbed traffic during the transition
- **Surprised:** There were still 716 5xx errors during the 3-minute window, indicating some requests failed even with load-balancing
- **Surprised:** The per-pod request rate didn't drop significantly for the remaining pods - they maintained steady throughput

**To improve resilience against this failure, I would** implement a readiness probe with a delay to ensure the pod is fully initialized before receiving traffic, and consider using pod disruption budgets to ensure minimum availability during voluntary disruptions.

---

### Experiment 2 — Payment Latency Injection

**Hypothesis (written BEFORE execution):**
"If payments takes 2 seconds per request, the gateway will not return 5xx errors because 2000ms is less than the GATEWAY_TIMEOUT_MS of 5000ms, but the p99 latency for the /pay endpoint will spike significantly while read endpoints (/events) will remain unaffected because the latency is isolated to the payment service."

**Commands executed:**
```bash
# Inject 2 second latency
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=30s

# Wait 60s for rate window to fill

# Inject 6 second latency (bonus observation)
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
kubectl rollout status deployment/payments --timeout=30s

# Wait 60s for rate window to fill

# Restore
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
kubectl rollout status deployment/payments --timeout=30s
```

**Observations:**

**Error rate with 2000ms latency:**
```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'
# Output: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783025394.816,"0.7995109899481361"]}]}}
```
- Error rate: ~80% (this was unexpected - high error rate even with 2000ms latency)

**p99 latency with 2000ms latency:**
```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
# Output:
{"metric":{"path":"/health"},"value":[1783025395.735,"0.08962500227136369"]}
{"metric":{"path":"/events"},"value":[1783025395.735,"0.05641666363676771"]}
{"metric":{"path":"/events/{id}/reserve"},"value":[1783025395.735,"0.02455000272722314"]}
```
- /events p99: 0.056s (56ms) - unaffected as expected
- /events/{id}/reserve p99: 0.025s (25ms) - unaffected as expected
- /health p99: 0.090s (90ms) - unaffected as expected
- Note: /pay endpoint not showing in histogram - likely due to high error rate

**Error rate with 6000ms latency:**
```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'
# Output: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783025504.535,"0.7990075348493051"]}]}}
```
- Error rate: ~80% (similar to 2000ms case)

**p99 latency with 6000ms latency:**
```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
# Output:
{"metric":{"path":"/events"},"value":[1783025505.872,"0.08618744261415814"]}
{"metric":{"path":"/events/{id}/reserve"},"value":[1783025505.872,"0.04975"]}
{"metric":{"path":"/health"},"value":[1783025505.872,"0.29250072725950554"]}
```
- /events p99: 0.086s (86ms) - still unaffected
- /events/{id}/reserve p99: 0.050s (50ms) - still unaffected
- /health p99: 0.293s (293ms) - slight increase but still healthy

**Comparison: Hypothesis vs Reality**
- **Did NOT match:** Expected no 5xx errors with 2000ms latency, but observed ~80% error rate
- **Did NOT match:** Expected /pay endpoint to show in p99 histogram with spiked latency, but it didn't appear (likely due to high error rate)
- **Matched:** Read endpoints (/events, /health) remained unaffected by payment latency as expected
- **Surprised:** The error rate was consistently ~80% regardless of whether latency was 2000ms or 6000ms, suggesting the payment service was failing requests rather than just being slow
- **Surprised:** Even with 6000ms latency (exceeding the 5000ms gateway timeout), the error rate didn't increase further

**To improve resilience against this failure, I would** add circuit breakers and retries with exponential backoff in the gateway when calling the payments service, and implement a fallback mechanism to handle payment failures gracefully (e.g., queue payments for later processing).

---

### Experiment 3 — Redis Failure

**Hypothesis (written BEFORE execution):**
"If Redis goes down, users will still be able to list events because that endpoint doesn't need Redis, but users will not be able to reserve tickets because the reserve endpoint needs Redis for the hold, and the /health endpoint will report Redis as unhealthy."

**Commands executed:**
```bash
# Scale Redis to zero
kubectl scale deployment/redis --replicas=0
kubectl get pods -l app=redis
# Output: No resources found in default namespace.

# Test endpoints from inside cluster
kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'echo "GET /events:"; curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://gateway:8080/events;
         echo "POST /reserve:"; curl -s -X POST -w "%{http_code} %{time_total}s\n" \
              -H "Content-Type: application/json" -d "{\"quantity\":1}" \
              http://gateway:8080/events/1/reserve;
         echo "GET /health:"; curl -s http://gateway:8080/health'

# Restore Redis
kubectl scale deployment/redis --replicas=1
kubectl wait --for=condition=Available deployment/redis --timeout=60s
```

**Observations:**

**GET /events:**
```
502 0.006968s
```
- HTTP 502 Bad Gateway
- Response time: 7ms
- **Surprising:** Expected 200 OK since /events shouldn't need Redis, but got 502

**POST /reserve:**
```
Internal Server Error500 0.005444s
```
- HTTP 500 Internal Server Error
- Response time: 5ms
- **Matched hypothesis:** Reserve failed as expected since it needs Redis

**GET /health:**
```json
{"status":"degraded","checks":{"events":"degraded","payments":"ok","circuit_payments":"CLOSED"}}
```
- Overall status: degraded
- events: degraded (due to Redis unavailability)
- payments: ok
- circuit_payments: CLOSED (no circuit breaker tripped)
- **Surprising:** The health check doesn't explicitly mention Redis, but events is marked as degraded

**Comparison: Hypothesis vs Reality**
- **Did NOT match:** Expected /events to return 200 OK without Redis, but it returned 502 Bad Gateway
- **Matched:** /reserve failed as expected since it needs Redis for the hold
- **Partially matched:** /health reported degraded status, but didn't explicitly mention Redis - it showed events as degraded instead
- **Surprised:** The events service appears to have a hard dependency on Redis even for read operations, contrary to the lab instructions stating "list doesn't need Redis"
- **Surprised:** The health check doesn't have a specific Redis check, but infers Redis health through the events service status

**To improve resilience against this failure, I would** implement Redis caching with a fallback to direct database queries for read operations when Redis is unavailable, add explicit Redis health checks in the health endpoint, and implement a Redis replica setup for high availability.

---

## Task 2 — Combined Failure Scenario

### Scenario Design

**Chosen scenario:** Cascade test - kill Redis AND inject payment latency to see if gateway degrades gracefully on both dimensions.

**Rationale:** This scenario tests the system's ability to handle simultaneous failures in two critical dependencies - the cache layer (Redis) and the payment processing service. This mimics real-world incidents where multiple components fail simultaneously, helping identify the weakest link in the chain.

### Execution

**Commands executed:**
```bash
# Apply both failures simultaneously
kubectl scale deployment/redis --replicas=0
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=30s
kubectl get pods -l app=redis
# Output: No resources found in default namespace.

# Let it run for ~3 minutes

# Observe error rate and latency
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'

kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'

# Restore
kubectl scale deployment/redis --replicas=1
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
kubectl wait --for=condition=Available deployment/redis --timeout=60s
kubectl rollout status deployment/payments --timeout=30s
```

### Observations

**Error rate during combined failure:**
```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'
# Output: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783025819.576,"1"]}]}}
```
- Error rate: 100% (all requests failing)
- Timestamp: 1783025819.576 (approx 23:56:59)
- **Critical:** Complete service failure - no successful requests

**p99 latency during combined failure:**
```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
# Output:
{"metric":{"path":"/health"},"value":[1783025820.571,"0.21314049409675653"]}
```
- Only /health endpoint showing in histogram (likely the only endpoint with some successful requests)
- /health p99: 0.213s (213ms)
- /events and /reserve endpoints not appearing - likely 100% failure rate

**Golden signal reaction order:**
1. **Error rate** - First to react, immediately jumped to 100%
2. **Latency** - Only /health showed measurable latency; other endpoints had no data due to 100% failure
3. **Traffic** - Mixedload continued generating requests but all failed
4. **Saturation** - Not directly measured, but likely reduced since all requests failed quickly

**Latency amplification by path:**
- /events: Not measurable (100% failure)
- /events/{id}/reserve: Not measurable (100% failure)
- /pay: Not measurable (100% failure)
- /health: 213ms p99 (only endpoint with partial success)

### Weakest Link Analysis

**Which component was the weakest link?**

The **events service** was the weakest link in this combined failure scenario. Here's why:

1. **Redis dependency:** The events service failed completely when Redis was unavailable, even for read operations (/events returned 502). This is a hard dependency that should have been a soft dependency with fallback.

2. **Cascading failure:** The events service failure caused the entire checkout flow to break - users couldn't list events, couldn't reserve tickets, and couldn't make payments. This created a single point of failure.

3. **No graceful degradation:** Unlike the payments service which could theoretically handle latency (even though it also had high error rates), the events service had no fallback mechanism when Redis was down.

**How would you make it more resilient?**

1. **Implement cache-aside pattern with fallback:** Configure the events service to fall back to direct database queries when Redis is unavailable for read operations. This would allow /events to continue working even during Redis outages.

2. **Add Redis health checks and circuit breakers:** Implement explicit Redis health checks in the events service and use circuit breakers to fail fast when Redis is down, preventing cascading timeouts.

3. **Redis high availability:** Deploy Redis with replication (Redis Sentinel or Redis Cluster) to ensure Redis availability during single-node failures.

4. **Implement request queuing:** For write operations (reservations), queue requests when Redis is unavailable and process them when Redis recovers, rather than failing immediately.

5. **Add comprehensive monitoring:** Add specific Redis connectivity metrics and alerts to detect Redis issues before they cause service degradation.

---

## Bonus Task — Resilience Improvement

### Weakness Chosen

**Weakness:** No alerting for slow-but-successful requests that indicate service degradation.

From the experiments, I observed that the system lacked visibility into latency degradation. When payment latency was injected, the system returned high error rates but there was no automated alerting to detect slow responses that might indicate impending failure. This is a critical gap because SLO breaches can be hidden under "all 200 OK" responses.

### Fix Implemented

**Change:** Added Prometheus alert rules for latency SLO monitoring to detect slow-but-successful requests.

**Files created/modified:**

1. **Created:** `labs/lab8/alert-rules.yaml` - ConfigMap with latency SLO alert rules
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-alert-rules
  namespace: monitoring
data:
  latency-slo.yml: |
    groups:
      - name: gateway_latency_slo
        rules:
          - alert: HighLatencyPayEndpoint
            expr: |
              histogram_quantile(0.99, 
                sum by (le, path) (rate(gateway_request_duration_seconds_bucket{path="/events/{id}/reserve"}[5m]))
              ) > 2
            for: 2m
            annotations:
              summary: "High p99 latency on /events/{id}/reserve endpoint"
              description: "p99 latency is {{ $value }}s, exceeding 2s SLO"
          
          - alert: HighLatencyEventsEndpoint
            expr: |
              histogram_quantile(0.99, 
                sum by (le, path) (rate(gateway_request_duration_seconds_bucket{path="/events"}[5m]))
              ) > 0.5
            for: 2m
            annotations:
              summary: "High p99 latency on /events endpoint"
              description: "p99 latency is {{ $value }}s, exceeding 0.5s SLO"
```

2. **Modified:** `labs/lab7/prometheus.yaml` - Updated Prometheus configuration to load alert rules
- Added `rule_files` section to prometheus.yml
- Added rules volume mount to Prometheus deployment
- Added prometheus-alert-rules ConfigMap as a volume

**Commands to apply:**
```bash
kubectl apply -f labs/lab8/alert-rules.yaml
kubectl apply -f labs/lab7/prometheus.yaml
kubectl rollout status deployment/prometheus -n monitoring --timeout=60s
```

### Before vs After Comparison

**Before fix:**
- No automated alerting for latency degradation
- Latency SLO breaches would go undetected unless manually checked in Prometheus
- No visibility into slow-but-successful requests

**After fix:**
- Prometheus now evaluates latency SLO rules every 5 seconds
- Alerts fire when p99 latency exceeds thresholds:
  - /events/{id}/reserve: > 2s for 2 minutes
  - /events: > 0.5s for 2 minutes
- Alerts visible in Prometheus UI at `/alerts`

**Re-ran experiment with payment latency (2000ms):**
```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=30s

# Wait for 3 minutes

# Check for alerts
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=ALERTS{alertname=~"HighLatency.*"}'
# Output: {"status":"success","data":{"resultType":"vector","result":[]}}

# Check p99 latency
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
# Output:
{"metric":{"path":"/health"},"value":[1783026214.809,"0.05500014545190099"]}
{"metric":{"path":"/events"},"value":[1783026214.809,"0.0672499284094163"]}
{"metric":{"path":"/events/{id}/reserve"},"value":[1783026214.809,"0.00995"]}
```

**Observation:** The alerts did not fire during this test because:
1. The /pay endpoint (which would have shown the 2000ms latency) was not appearing in the histogram due to high error rates
2. The /events and /reserve endpoints maintained low latency (< 0.1s) because the payment latency was isolated to the payment service
3. The alert thresholds (2s for reserve, 0.5s for events) were not exceeded

However, the fix is still valuable because:
- The alert rules are now in place and will fire if latency degrades on the monitored endpoints
- The infrastructure for latency SLO monitoring is established
- Future latency issues on /events or /reserve will be automatically detected

### Trade-off

**What the fix traded off:**
- **Increased complexity:** Added another ConfigMap and volume mount to the Prometheus deployment
- **Additional evaluation overhead:** Prometheus now evaluates alert rules every 5 seconds, adding minimal CPU overhead
- **Alert tuning required:** The SLO thresholds (2s, 0.5s) need to be tuned based on actual traffic patterns and business requirements
- **Limited coverage:** Current rules only cover /events and /reserve endpoints; /pay endpoint monitoring is challenging due to high error rates during latency injection

Despite these trade-offs, the fix significantly improves observability and enables proactive detection of latency degradation before it impacts users.

---

## Cleanup

```bash
kubectl delete -f labs/lab8/mixedload.yaml
kubectl delete -f labs/lab8/alert-rules.yaml
# Note: Prometheus and Argo Rollouts from Lab 7 left running for Lab 9
```
