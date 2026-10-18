# Lab 8 — Resilience, Canary Deployments and Failure Experiments

## Student

Silvia Fedorovskaya

## Branch

`feature/lab8`

---

# Task 1 — Failure experiments

## Experiment 1 — Gateway pod deletion

### Hypothesis

If I delete one gateway pod while traffic is flowing, the remaining gateway pods will continue serving requests with little or no user-visible impact because the Kubernetes Service load-balances traffic across the remaining healthy replicas and Kubernetes replaces the deleted pod.

### Setup

A mixed-load generator was deployed:

```bash
kubectl apply -f labs/lab8/mixedload.yaml
kubectl rollout status deployment/mixedload --timeout=60s
```

The load was allowed to run before the failure injection.

### Failure injection

One Gateway pod was deleted while traffic was running:

```bash
kubectl delete pod gateway-6bf4b75d88-4vlxm
```

Kubernetes created a replacement Gateway pod.

### Observations

The replacement pod became `1/1 Running`.

Gateway metrics observed during the experiment included:

```text
gateway_requests_total{method="GET",path="/health",status="200"} 3711.0
gateway_requests_total{method="GET",path="/health",status="503"} 175.0
gateway_requests_total{method="POST",path="/reserve/test/pay",status="500"} 31.0
gateway_requests_total{method="GET",path="/events",status="502"} 310.0
```

After the replacement, the Gateway replicas were running and ready.

### Conclusion

The hypothesis is supported. Kubernetes replaced the deleted Gateway pod and the service continued operating while traffic was running.

---

# Experiment 2 — Payment latency

## Hypothesis

If payments takes 2 seconds per request, the gateway will continue returning successful responses without a significant increase in 5xx errors because the 2-second payment latency is below the gateway timeout of 5 seconds.

### Failure injection

Payment failures were disabled and a 2-second payment latency was introduced:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0 PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=60s
sleep 30
```

### Observations

Gateway metrics during the experiment included:

```text
gateway_requests_total{method="GET",path="/health",status="200"} 3823.0
gateway_requests_total{method="GET",path="/health",status="503"} 154.0
gateway_requests_total{method="GET",path="/events",status="502"} 578.0
gateway_requests_total{method="GET",path="/events/{id}/reserve",status="502"} 4.0
```

The Prometheus 5xx ratio observed during the experiment was approximately:

```text
0.7335
```

A p99 request-duration query showed approximately 25 ms for `/events` and `/health`. The reservation path did not always have enough successful observations for a meaningful p99 value.

### Analysis

The observed 5xx rate cannot be attributed exclusively to the injected 2-second latency because Prometheus counters already contained errors from previous experiments. Therefore, this experiment does not provide a clean isolated measurement of the effect of payment latency.

The payment latency was reset afterwards:

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
```

### Conclusion

The hypothesis cannot be conclusively confirmed from the collected metrics because previous experiment errors were included in the cumulative Prometheus counters. The configured 2-second latency is nevertheless below the Gateway timeout, so it is not expected by itself to cause Gateway request timeouts.

---

# Task 2 — Combined failure experiment

## Experiment 3 — Redis failure

### Hypothesis

If Redis goes down, listing events will continue to work because it does not depend on Redis, but ticket reservation will fail because Redis is required for the ticket hold.

### Failure injection

Redis was scaled to zero:

```bash
kubectl scale deployment/redis --replicas=0
sleep 15
```

### Observations

During the failure, the Gateway became unavailable with the original implementation.

The Gateway metrics endpoint could not be reached from the mixed-load generator:

```text
wget: can't connect to remote host (10.43.77.204): Connection refused
```

Gateway logs showed an exception caused by attempting to parse an upstream error response as JSON:

```text
json.decoder.JSONDecodeError: Expecting value
```

The problematic handler contained:

```python
raise HTTPException(e.response.status_code, e.response.json())
```

Redis was restored afterwards:

```bash
kubectl scale deployment/redis --replicas=1
kubectl rollout status deployment/redis --timeout=60s
```

The Events service had also entered `CrashLoopBackOff` because it had started while Redis was unavailable and its health check returned HTTP 503. It was restarted after Redis was restored:

```bash
kubectl rollout restart deployment/events
kubectl rollout status deployment/events --timeout=60s
```

### Conclusion

The experiment demonstrated that Redis is a critical dependency for the current ticketing path and that dependency failures can propagate into the Gateway and Events service. It also revealed an error-handling weakness in the Gateway, which was addressed in the bonus section.

---

# Canary deployment and automated rollback

The Gateway is managed by Argo Rollouts using a Canary strategy.

The configured Canary steps are:

```text
20%
Analysis
60s pause
40%
60s pause
60%
60s pause
80%
30s pause
100%
```

The analysis metric is `error-rate`.

The Prometheus query calculates:

```text
5xx request rate / total request rate
```

The success condition is:

```text
result[0] < 0.05
```

Therefore, the Canary must keep the 5xx error rate below 5%.

## Canary failure

A new Gateway revision was introduced for the experiment. Argo Rollouts created revision 12 and started its AnalysisRun.

The AnalysisRun produced:

```text
0.8473024061323354
0.8476190476190477
```

This corresponds to approximately 84.7% 5xx errors, which is far above the 5% limit.

The AnalysisRun failed with:

```text
Metric "error-rate" assessed Failed
failed (2) > failureLimit (1)
```

Argo Rollouts automatically aborted the Canary:

```text
RolloutAborted
```

The Canary ReplicaSet was scaled down and the stable revision remained available.

The rollout was then restored:

```bash
kubectl argo rollouts undo gateway
```

The final Rollout state was:

```text
Status: Healthy
Step: 10/10
SetWeight: 100
ActualWeight: 100

Replicas:
  Desired: 5
  Current: 5
  Updated: 5
  Ready: 5
  Available: 5
```

### Conclusion

The Canary Analysis successfully detected the unhealthy revision and prevented it from becoming the stable version. This demonstrates automated deployment protection based on a Prometheus error-rate SLO.

---

# Bonus — Resilience improvement

## Problem

When Redis or another dependency became unavailable, the original Gateway implementation could become unstable.

Two related problems were identified:

1. The reservation handler attempted to parse every upstream error response as JSON.
2. The Gateway health endpoint returned HTTP 503 whenever a critical dependency was unavailable, which could cause Kubernetes to restart the Gateway even though the application process itself was still functioning.

## Fix 1 — Safe upstream error handling

The reservation handler was changed from:

```python
raise HTTPException(e.response.status_code, e.response.json())
```

to:

```python
raise HTTPException(e.response.status_code, e.response.text)
```

This prevents a non-JSON upstream error response from causing an unexpected `JSONDecodeError` in the Gateway.

## Fix 2 — Dependency degradation without process failure

The Gateway health endpoint was changed so that dependency degradation is reported in the response body while the endpoint itself continues returning HTTP 200.

During dependency failure, the endpoint returned:

```text
HTTP/1.1 200 OK
```

with:

```json
{
  "status": "degraded",
  "checks": {
    "events": "down",
    "payments": "ok",
    "circuit_payments": "CLOSED"
  }
}
```

## Bonus verification

Redis was scaled to zero:

```bash
kubectl scale deployment/redis --replicas=0
sleep 15
```

The fixed Gateway remained running.

The health endpoint returned:

```text
HTTP/1.1 200 OK
```

and:

```json
{
  "status": "degraded",
  "checks": {
    "events": "down",
    "payments": "ok",
    "circuit_payments": "CLOSED"
  }
}
```

Therefore, the Gateway did not enter CrashLoopBackOff because of the dependency failure.

Redis was restored:

```bash
kubectl scale deployment/redis --replicas=1
kubectl rollout status deployment/redis --timeout=60s
```

The Events service was restarted so it could reconnect to Redis:

```bash
kubectl rollout restart deployment/events
kubectl rollout status deployment/events --timeout=60s
```

The Gateway was restarted and returned to:

```text
Healthy
```

## Bonus conclusion

The changes improve resilience by separating dependency health from process health and by making upstream error handling safe.

Instead of repeatedly restarting when a dependency is temporarily unavailable, the Gateway can remain alive and report a degraded state. This improves availability and makes the failure mode observable without causing unnecessary pod restarts.

---

# Final cluster state

After restoring all dependencies and restarting affected services, the final cluster state was:

```text
events      1/1 Running
gateway     5/5 Running
payments    1/1 Running
redis       1/1 Running
mixedload   2/2 Running
```

The Gateway Argo Rollout was:

```text
Status: Healthy
Strategy: Canary
Step: 10/10
SetWeight: 100
ActualWeight: 100
Desired: 5
Current: 5
Updated: 5
Ready: 5
Available: 5
```

# Overall conclusion

The laboratory work demonstrated:

1. Kubernetes automatically replaces failed Gateway pods while traffic continues.
2. Injected payment latency can be investigated through Prometheus metrics, although cumulative counters must be interpreted carefully when experiments are not isolated.
3. Redis failure exposes dependency coupling in the ticketing path.
4. Argo Rollouts provides automated Canary deployment protection.
5. A Prometheus-based error-rate AnalysisRun detected an unhealthy Canary with approximately 84.7% 5xx errors.
6. The unhealthy Canary was automatically aborted before becoming stable.
7. The Gateway was improved to handle upstream non-JSON errors safely.
8. The Gateway health endpoint was made resilient to dependency degradation.
9. The fixed Gateway remained alive and returned HTTP 200 with a clear `degraded` status while a dependency was unavailable.
10. The cluster was restored to a healthy state after the experiments.

