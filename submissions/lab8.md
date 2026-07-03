# Lab 8 — Chaos Engineering: Break Things on Purpose

## Overview

In this lab, I designed and executed three chaos engineering experiments to evaluate the resilience of the QuickTicket system under different failure scenarios. Before each experiment, I formulated a hypothesis, injected a controlled failure, observed the system using Kubernetes and Prometheus, compared the observed behavior with the hypothesis, and identified possible resilience improvements.

The experiments covered pod failure recovery, downstream service latency, and Redis unavailability.

---

## Setup

First, I deployed the mixed workload generator provided for Lab 8.

Command:

```bash
kubectl apply -f labs/lab8/mixedload.yaml
kubectl rollout status deployment/mixedload --timeout=60s
```

Output:

```bash
➜  SRE-Intro git:(feature/lab7) ✗ kubectl apply -f labs/lab8/mixedload.yaml
deployment.apps/mixedload created
➜  SRE-Intro git:(feature/lab7) ✗ kubectl rollout status deployment/mixedload --timeout=60s
Waiting for deployment "mixedload" rollout to finish: 0 of 2 updated replicas are available...
Waiting for deployment "mixedload" rollout to finish: 1 of 2 updated replicas are available...
deployment "mixedload" successfully rolled out
```

After waiting approximately two minutes for Prometheus to collect baseline metrics, I verified that requests were flowing through the gateway.

Command:

```bash
kubectl port-forward -n monitoring svc/prometheus 9091:9090 &

curl -s 'http://localhost:9091/api/v1/query?query=sum(rate(gateway_requests_total%5B1m%5D))' \
  | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('RPS:', r[0]['value'][1] if r else 'no data')"
```

Output:

```bash
RPS: 7.893931920858235
```

The query confirmed that the workload generator was continuously sending requests and the system was ready for chaos experiments.

---

# Task 1 — Three Chaos Experiments

## Experiment 1 — Pod Kill Under Load

### Hypothesis

Before running the experiment, I expected that deleting one gateway pod while traffic was flowing would not cause a significant service interruption. Kubernetes should immediately create a replacement pod, while the remaining gateway replicas continue serving requests. I expected little or no increase in HTTP 5xx responses because the Service would automatically stop routing traffic to the deleted pod.

---

### Execute the experiment

Command:

```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)

echo "Killing $VICTIM at $(date +%H:%M:%S)"

kubectl delete "$VICTIM"
```

Output:

```bash
➜  SRE-Intro git:(feature/lab7) ✗ VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
➜  SRE-Intro git:(feature/lab7) ✗ echo "Killing $VICTIM at $(date +%H:%M:%S)"
Killing pod/gateway-5bdd8bb7fd-5w2pj at 15:02:30
➜  SRE-Intro git:(feature/lab7) ✗ kubectl delete "$VICTIM"
pod "gateway-5bdd8bb7fd-5w2pj" deleted
```

---

### Observe pod recovery

How long until Kubernetes creates a replacement pod?

Command:

```bash
kubectl get pods -l app=gateway -w 
```

Output:

```bash
NAME                       READY   STATUS    RESTARTS   AGE
gateway-5bdd8bb7fd-7xhj7   1/1     Running   0          36m
gateway-5bdd8bb7fd-sjvb9   1/1     Running   0          36m
gateway-5bdd8bb7fd-td5k8   1/1     Running   0          12s
gateway-5bdd8bb7fd-wsl9t   1/1     Running   0          36m
gateway-5bdd8bb7fd-zbjll   1/1     Running   0          36m
```

---

### Observe failed requests

Did any request fail during the transition?

Command:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
```

Output:

```bash
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783080252.798,"1107.8670481660022"]}]}}
```

---

### Observe traffic redistribution

Did the per-pod request rate drop to zero during the gap, or was traffic picked up by the remaining 4 pods?

Command:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(pod)+(rate(gateway_requests_total%5B1m%5D))'
```

Output:

```bash
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"pod":"gateway-5bdd8bb7fd-7xhj7"},"value":[1783080303.863,"1.4881492504809262"]},{"metric":{"pod":"gateway-5bdd8bb7fd-zbjll"},"value":[1783080303.863,"1.4336787470736616"]},{"metric":{"pod":"gateway-5bdd8bb7fd-wsl9t"},"value":[1783080303.863,"1.9418532902616963"]},{"metric":{"pod":"gateway-5bdd8bb7fd-sjvb9"},"value":[1783080303.863,"1.7241066405328398"]},{"metric":{"pod":"gateway-5bdd8bb7fd-td5k8"},"value":[1783080303.863,"1.4700010888896953"]}]}}
```

---

### Comparison with the hypothesis

The gateway pod was deleted at 15:02:30. Kubernetes created a replacement pod quickly, and when I checked the pod list, the new pod was already `Running` with age `12s`. This means that the replacement happened quickly and the desired replica count returned to 5.

The per-pod request rate showed traffic on all five gateway pods after recovery:

- gateway-5bdd8bb7fd-7xhj7: ~1.49 RPS
- gateway-5bdd8bb7fd-zbjll: ~1.43 RPS
- gateway-5bdd8bb7fd-wsl9t: ~1.94 RPS
- gateway-5bdd8bb7fd-sjvb9: ~1.72 RPS
- gateway-5bdd8bb7fd-td5k8: ~1.47 RPS

However, the Prometheus query for 5xx responses returned `1108` failed requests over the 3-minute window. This means that some requests failed during the observation window. Since I did not capture a baseline 5xx count immediately before deleting the pod, I cannot attribute all of these failures solely to the pod deletion.

Overall, the hypothesis was partially confirmed. Kubernetes self-healing worked and traffic was successfully redistributed across the remaining gateway pods after the replacement pod became available.

---

### Resilience improvement

To improve resilience against this failure, I would add a PodDisruptionBudget for the gateway and also compare pre-failure and post-failure error rates more carefully. This would make it easier to distinguish failures caused by the pod kill from failures already present in the system

---

## Experiment 2 — Payment Latency Injection

### Hypothesis

Before starting the experiment, I expected that increasing payment latency to 2000 ms would noticeably increase request latency for `/pay`, while requests should still succeed because the gateway timeout is configured to 5000 ms. Read-only endpoints such as `/events` should remain unaffected.

---

### Inject latency

Command:

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000

kubectl rollout status deployment/payments --timeout=30s
```

Output:

```bash
➜  SRE-Intro git:(feature/lab7) ✗ kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
deployment.apps/payments env updated
➜  SRE-Intro git:(feature/lab7) ✗ kubectl rollout status deployment/payments --timeout=30s
Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
deployment "payments" successfully rolled out
```

---

### Observe error rate

Is the gateway returning 5xx? (2000ms < GATEWAY_TIMEOUT_MS of 5000ms — it should not)

Command:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'
```

Output:

```bash
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783088534.048,"0.0026316186852645713"]}]}}%     
```

---

### Observe p99 latency

How does p99 latency change per endpoint?

Command:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
```

Output:

```bash
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/health"},"value":[1783088729.394,"0.11650148458596384"]},{"metric":{"path":"/events"},"value":[1783088729.394,"0.024924983015828617"]},{"metric":{"path":"/events/{id}/reserve"},"value":[1783088729.394,"0.04940276725738692"]},{"metric":{"path":"/reserve/{id}/pay"},"value":[1783088729.394,"NaN"]}]}
```

---

### (Optional) Timeout experiment

Command:

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(path,status)+(rate(gateway_requests_total%5B1m%5D))'
```

Output:

```bash
deployment.apps/payments env updated
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/health","status":"200"},"value":[1783088630.321,"1.6360602701279456"]},{"metric":{"path":"/events","status":"502"},"value":[1783088630.321,"0"]},{"metric":{"path":"/health","status":"503"},"value":[1783088630.321,"0.01833348611238427"]},{"metric":{"path":"/events/{id}/reserve","status":"502"},"value":[1783088630.321,"0"]},{"metric":{"path":"/events","status":"504"},"value":[1783088630.321,"0"]},{"metric":{"path":"/events/{id}/reserve","status":"200"},"value":[1783088630.321,"0"]},{"metric":{"path":"/events","status":"200"},"value":[1783088630.321,"6.317655283324587"]},{"metric":{"path":"/reserve/{id}/pay","status":"200"},"value":[1783088630.321,"0"]},{"metric":{"path":"/events/{id}/reserve","status":"409"},"value":[1783088630.321,"6.2604432573322"]}]}}%    
```

---

### Restore configuration

Command:

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0 && kubectl rollout status deployment/payments --timeout=30s
```

Output:

```bash
deployment.apps/payments env updated
Waiting for deployment "payments" rollout to finish: 0 out of 1 new replicas have been updated...
Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
deployment "payments" successfully rolled out
```

---

### Comparison with the hypothesis

The observed behavior mostly matched the hypothesis. With PAYMENT_LATENCY_MS=2000, the gateway 5xx ratio remained very low (approximately 0.26%), confirming that the increased payment latency did not significantly affect request success. Read-only endpoints also remained fast (/events ≈ 0.025 s, /events/{id}/reserve ≈ 0.049 s). No significant increase in the gateway error rate was observed.

The /reserve/{id}/pay p99 latency was reported as NaN, indicating that there were not enough payment requests during the observation window to calculate a meaningful value. Because of this, the expected latency increase for /pay could not be directly verified

Overall, the hypothesis was partially confirmed. The system remained stable under the injected latency, but there was insufficient /pay traffic to fully observe its impact


### Resilience improvement

To improve resilience against this failure, I would add dedicated monitoring and alerting for /reserve/{id}/pay latency in addition to overall error-rate monitoring. This would make it easier to detect slow payment processing even when requests are still succeeding. I would also consider reducing the gateway timeout or introducing a circuit breaker so that excessively slow payment requests fail faster instead of tying up resources

---

## Experiment 3 — Redis Failure

### Hypothesis

Before running the experiment, I expected that disabling Redis would prevent ticket reservations because reservation state depends on Redis. Listing events should continue working because it does not require Redis. The health endpoint should report a degraded system state.

---

### Stop Redis

Command:

```bash
kubectl scale deployment/redis --replicas=0
```

Output:

```bash
deployment.apps/redis scaled
```

---

### Observe application behavior

Command:

```bash
kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'echo "GET /events:"; curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://gateway:8080/events;
         echo "POST /reserve:"; curl -s -X POST -w "%{http_code} %{time_total}s\n" \
              -H "Content-Type: application/json" -d "{\"quantity\":1}" \
              http://gateway:8080/events/1/reserve;
         echo "GET /health:"; curl -s http://gateway:8080/health'
```

Output:

```bash
GET /events:
000 0.011891s
POST /reserve:
000 0.002656s
GET /health:
pod default/chaos-probe terminated (Error)  
```

---

### Restore Redis

Command:

```bash
kubectl scale deployment/redis --replicas=1 && kubectl wait --for=condition=Available deployment/redis --timeout=60s
```

Output:

```bash

```
deployment.apps/redis scaled
deployment.apps/redis condition met
---

### Comparison with the hypothesis

The observed behavior generally matched the hypothesis. As expected, Redis unavailability affected operations that depend on reservation state. The probe was unable to successfully complete the requests while Redis was unavailable, indicating that the reservation workflow could not function correctly

One unexpected observation was that the probe itself terminated with an error instead of returning normal HTTP responses. During troubleshooting, I observed that the gateway also became unavailable, which suggests that its health checks were affected by the Redis outage. After Redis was restored, the gateway recovered and the rollout returned to the `Healthy` state

Overall, the hypothesis was partially confirmed. Redis failure disrupted reservation-related functionality as expected, but it also had a broader impact on gateway availability than I initially anticipated

---

### Resilience improvement

To improve resilience against this failure, I would separate liveness and readiness probes. The liveness probe should verify only that the gateway process is running, while readiness or health checks can validate external dependencies such as Redis. This would reduce unnecessary gateway restarts during temporary Redis outages

---

# Task 2 — Combined Failure Scenario

## Scenario Design

For the combined failure scenario, I chose the **degraded dependencies** case. I injected failures into the `payments` service while simultaneously limiting the database connection pool of the `events` service and increasing the request load

The scenario included:

- `PAYMENT_FAILURE_RATE=0.3`
- `PAYMENT_LATENCY_MS=500`
- `DB_MAX_CONNS=3` for the events service
- scaling `mixedload` to 3 replicas

I chose this scenario because production incidents are often caused by multiple partial failures rather than a single complete outage. By degrading two critical dependencies at the same time, I expected to identify which component would become the weakest link first and which golden signal would react earliest.

---

## Execute the Scenario

Command:

    kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
    kubectl set env deployment/events DB_MAX_CONNS=3
    kubectl scale deployment/mixedload --replicas=3
    kubectl rollout status deployment/payments --timeout=30s
    kubectl rollout status deployment/events --timeout=30s

Output:
    deployment.apps/payments env updated
    deployment.apps/events env updated
    deployment.apps/mixedload scaled
    Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
    Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
    deployment "payments" successfully rolled out
    deployment "events" successfully rolled out

After applying the changes, I allowed the system to run for approximately 3–5 minutes so that Prometheus could collect representative metrics.

---

## Observe Error Rate

Command:

    kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
      'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'

Sample 1:
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783084479.208,"0.8609375303822997"]}]}}

Sample 2:

{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783084519.375,"0.8620690289330069"]}]}

Sample 3:

{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783084537.068,"0.8573151684725036"]}]}

---

## Observe p99 Latency

Command:

    kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
      'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'

Sample 1:
    {"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/events"},"value":[1783084557.245,"0.0223649862672302"]},{"metric":{"path":"/events/{id}/reserve"},"value":[1783084557.245,"0.07370420630992318"]},{"metric":{"path":"/health"},"value":[1783084557.245,"0.05060061666171014"]}]}}%         

Sample 2:

    {"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/health"},"value":[1783084566.060,"0.05250024749752512"]},{"metric":{"path":"/events"},"value":[1783084566.060,"0.023349989375017803"]},{"metric":{"path":"/events/{id}/reserve"},"value":[1783084566.060,"0.07375"]}]}

Sample 3:

    {"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/health"},"value":[1783084575.820,"0.05287787872043735"]},{"metric":{"path":"/events"},"value":[1783084575.820,"0.02361332578980731"]},{"metric":{"path":"/events/{id}/reserve"},"value":[1783084575.820,"0.0737500366642835"]}]}}

---

## Restore Configuration

Command:

    kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0
    kubectl set env deployment/events DB_MAX_CONNS=10
    kubectl scale deployment/mixedload --replicas=2
    kubectl rollout status deployment/payments --timeout=30s
    kubectl rollout status deployment/events --timeout=30s

Output:

deployment.apps/payments env updated
deployment.apps/events env updated
deployment.apps/mixedload scaled
Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
deployment "payments" successfully rolled out
Waiting for deployment "events" rollout to finish: 1 old replicas are pending termination...
Waiting for deployment "events" rollout to finish: 1 old replicas are pending termination...
deployment "events" successfully rolled out

---

## Analysis

### Which golden signal reacted first?

The first golden signal that reacted was the error rate. Shortly after injecting failures into the payments service and limiting database connections for the events service, the proportion of HTTP 5xx responses increased to approximately 86%, while latency increased only moderately. This indicates that service availability degraded before latency became the dominant issue

---

### Which endpoint showed the highest latency amplification?

The endpoint with the highest observed latency was `/events/{id}/reserve`. This endpoint depends on both the events service and downstream components involved in ticket reservation, making it more sensitive to degraded dependencies than simple read operations

---

### Weakest Link

The payments service was the primary contributor to the high error rate, while the events service became the latency bottleneck because of the limited database connection pool

---

## Resilience Improvement

To make the system more resilient against this combined failure, I would:

- introduce circuit breakers for the payments service
- configure retries with exponential backoff only for idempotent operations
- increase the database connection pool or optimize connection usage in the events service
- add alerting based on both latency and error rate so that partial degradations are detected earlier

---

# Bonus Task — Resilience Improvement

## Chosen Weakness

During the combined failure scenario, the `/events/{id}/reserve` endpoint showed the highest p99 latency among the observed gateway paths.

This endpoint depends on the `events` service and the database-backed reservation flow. In the previous experiment, the events service was constrained with `DB_MAX_CONNS=3`, which could cause database connection queueing under mixed load.

Therefore, I chose limited database connection capacity in the `events` service as the weakness to improve.

---

## Implement the Fix

To reduce database connection pressure, I increased the database connection limit for the `events` service.

Before the fix:

    DB_MAX_CONNS=3

After the fix:

    DB_MAX_CONNS=20

Command:

    kubectl set env deployment/events DB_MAX_CONNS=20
    kubectl rollout status deployment/events --timeout=30s

Output:

    deployment.apps/events env updated
    Waiting for deployment "events" rollout to finish: 1 old replicas are pending termination...
    Waiting for deployment "events" rollout to finish: 1 old replicas are pending termination...
    deployment "events" successfully rolled out
---

## Re-run the Experiment

I repeated the same degraded dependencies scenario as in Task 2, but kept the improved database connection limit for the `events` service.

Command:

    kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
    kubectl set env deployment/events DB_MAX_CONNS=20
    kubectl scale deployment/mixedload --replicas=3
    kubectl rollout status deployment/payments --timeout=30s
    kubectl rollout status deployment/events --timeout=30s

Output:

    deployment.apps/payments env updated
    deployment.apps/mixedload scaled
    Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
    Waiting for deployment "payments" rollout to finish: 1 old replicas are pending termination...
    deployment "payments" successfully rolled out
    deployment "events" successfully rolled out

---

## After-fix Error Rate

Command:

    kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
      'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'

Output:

    {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783085537.278,"0.8488204282875014"]}]}}

---

## After-fix p99 Latency

Command:

    kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
      'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'

Output:

    {"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/health"},"value":[1783085554.572,"0.0522875151335498"]},{"metric":{"path":"/events"},"value":[1783085554.572,"0.023531931458263673"]},{"metric":{"path":"/events/{id}/reserve"},"value":[1783085554.572,"0.00495"]}]}}

---

## Before vs After Comparison

Before the fix, with `DB_MAX_CONNS=3`, the `/events/{id}/reserve` endpoint had the highest observed p99 latency:

    /events/{id}/reserve p99 before fix: approximately 0.0737s

After increasing `DB_MAX_CONNS` to 20 and re-running the same scenario, the observed p99 latency was:

    /events/{id}/reserve p99 after fix: approximately 0.00495s

The result showed that increasing the database connection limit significantly reduced latency on the reservation path. This confirms that the limited database connection pool contributed to request queueing. The p99 latency for `/events/{id}/reserve` dropped from approximately 73.7 ms to approximately 4.95 ms.

The error rate was still high after the fix:

    error rate after fix: approximately 0.8488

This means that the payment failure injection was still causing many user-visible failures. However, the reservation latency improved, so the database connection limit in the `events` service was no longer the main latency bottleneck

---

## Trade-off

Increasing `DB_MAX_CONNS` reduced request queueing and improved reservation latency. The trade-off is higher resource usage on PostgreSQL. Setting the value too high can overload the database and negatively affect overall system performance, so the connection pool should be sized according to the available database capacity