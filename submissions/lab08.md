# Lab 8 Submission

## Experiment 1. Pod kill under load

### Hypothesis

If I delete one `gateway` pod while traffic is flowing, the service will stay available and Kubernetes will quickly create a replacement pod, because there are still 4 healthy replicas behind the Service.

### Commands

```bash
kubectl apply -f labs/lab8/mixedload.yaml
kubectl apply -f labs/lab7/prometheus.yaml

VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
kubectl delete "$VICTIM" --wait=false

kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'

kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(pod)+(rate(gateway_requests_total%5B1m%5D))'
```

### What I observed

Timestamp when I started:

```text
2026-07-03 23:04:19 MSK
```

Pod recovery:

```text
victim=pod/gateway-5758b8d586-dl8cr
23:04:19 ready=4 total=6
23:04:21 ready=4 total=5
23:04:23 ready=4 total=5
23:04:25 ready=5 total=5
recovery_seconds=6
```

Replacement pod:

```text
gateway-5758b8d586-7nckw   1/1 Running   AGE 6s
```

Prometheus at `2026-07-03 23:04:30 MSK`:

```text
sum(increase(gateway_requests_total{status=~"5.."}[3m])) = 2.0571487351304993
```

```text
gateway-5758b8d586-hnmkz = 3.2363636363636363 rps
gateway-5758b8d586-zhmz8 = 3.4183682746331616 rps
gateway-5758b8d586-z7qqv = 3.745454545454545 rps
gateway-5758b8d586-fhnqb = 3.3453328969855645 rps
gateway-5758b8d586-7nckw = 1.4916073697903904 rps
```

### Comparison

My hypothesis was mostly correct. The rollout returned to `5/5` in about 6 seconds, and the remaining pods kept serving traffic during the gap. I did see a small amount of 5xx in the 3-minute window, so the transition was not completely invisible, but the system recovered fast.

### Resilience improvement

To improve resilience against this failure, I would add an alert on temporary 5xx spikes during pod replacement and verify graceful connection draining before pod termination.

## Experiment 2. Payment latency injection

### Hypothesis

If `payments` takes 2 seconds per request, only the `/reserve/{id}/pay` path will become much slower, but the gateway should not return many 5xx errors because `2000ms` is still below `GATEWAY_TIMEOUT_MS=5000`.

### Commands

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=60s

kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'

kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'

kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(path,status)+(rate(gateway_requests_total%5B1m%5D))'

kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
kubectl rollout status deployment/payments --timeout=60s
```

### What I observed

Latency injection started at:

```text
2026-07-03 23:05:07 MSK
```

Prometheus at `2026-07-03 23:06:38 MSK`:

```text
error_ratio = 0.005208498060515257
```

```text
p99 /health = 0.09312522498409236
p99 /events = 0.024654998581786547
p99 /events/{id}/reserve = 0.07212508863057902
p99 /reserve/{id}/pay = 2.485
```

Request rate by path and status:

```text
/health 200 = 0.9636409940074044 rps
/reserve/{id}/pay 200 = 0.8363652912095157 rps
/events 200 = 0.8363685971148545 rps
/events/{id}/reserve 200 = 0.8363682663139639 rps
```

### Comparison

My hypothesis was correct. I did not get a big 5xx wave, and the error ratio stayed very low at about `0.0052`. The main effect was latency: `/reserve/{id}/pay` jumped to about `2.485s` p99, while `/events` stayed fast at about `0.025s`.

### Resilience improvement

To improve resilience against this failure, I would add a timeout or circuit-breaker metric specifically for the payment path and alert when `/reserve/{id}/pay` latency grows while read paths stay normal.

## Experiment 3. Redis failure

### Hypothesis

If Redis goes down, listing events should still work, but ticket reservation should fail because holds depend on Redis, and `/health` should become unhealthy.

### Commands

```bash
kubectl scale deployment/redis --replicas=0

kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'echo "GET /events:"; curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://gateway:8080/events;
         echo "POST /reserve event3:"; curl -s -X POST -o /dev/null -w "%{http_code} %{time_total}s\n" \
              -H "Content-Type: application/json" -d "{\"quantity\":1}" \
              http://gateway:8080/events/3/reserve;
         echo "GET /health:"; curl -s http://gateway:8080/health'

kubectl get pods -l app=gateway -o wide
kubectl logs -l app=gateway --tail=60

kubectl scale deployment/redis --replicas=1
kubectl wait --for=condition=Available deployment/redis --timeout=60s
```

### What I observed

Redis scale-down started at:

```text
2026-07-03 23:07:24 MSK
```

Redis pod was gone at:

```text
23:07:26 redis_pods_present=0
```

Probe at `2026-07-03 23:07:39 MSK`:

```text
GET /events: 000 0.000822s
POST /reserve event3: 000 0.000524s
GET /health: probe failed because the service had no ready endpoints
```

Gateway pod state:

```text
all 5 gateway pods were Running but 0/1 Ready
```

Important log lines:

```text
GET /health -> 503 Service Unavailable
POST /events/1/reserve -> 504 Gateway Timeout
reserve error: All connection attempts failed
GET /events -> 502 Bad Gateway
events service error: All connection attempts failed
```

After restore, at the end I got:

```text
events: 200 0.006782s
health: 200 0.011956s
```

### Comparison

My hypothesis was only partly correct. I expected `GET /events` to keep working, but in reality the failure was stronger: after Redis went down, `gateway` health became `503`, all gateway pods turned `0/1 Ready`, and the service stopped answering normally. So Redis was a more critical dependency than I expected.

### Resilience improvement

To improve resilience against this failure, I would separate readiness for read-only paths from reservation dependencies so that `/events` can stay available even when Redis is down.
