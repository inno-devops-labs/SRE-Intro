# Lab 8

I first corrected `labs/lab8/mixedload.yaml` for the current cluster state:

- event `1` was already sold out, so the original load generator did not reach `/pay`
- I switched it to `event 3` and replaced the `reservation_id` parsing with a more reliable `grep | cut` pipeline

Then I applied the load generator:

```bash
cat labs/lab8/mixedload.yaml | docker exec -i k3d-quickticket-server-0 kubectl apply -f -
docker exec k3d-quickticket-server-0 kubectl rollout status deployment/mixedload --timeout=60s
```

```text
deployment.apps/mixedload created
deployment "mixedload" successfully rolled out
```

The fixed load generator produced traffic on all expected paths:

```bash
docker exec k3d-quickticket-server-0 sh -lc '
  POD=$(kubectl get pods -n monitoring -l app=prometheus -o jsonpath="{.items[0].metadata.name}")
  kubectl exec -n monitoring "$POD" -- wget -qO- \
    "http://localhost:9090/api/v1/query?query=sum%20by%20(path)(rate(gateway_requests_total%5B1m%5D))"
' | python3 -c 'import json,sys; rows=json.load(sys.stdin)["data"]["result"]; rows=sorted(rows,key=lambda r:r["metric"].get("path","")); [print(r["metric"].get("path","")+" rate="+r["value"][1]) for r in rows]'
```

```text
/events rate=8.545454545454545
/events/{id}/reserve rate=8.557132424242424
/health rate=1.509090909090909
/reserve/{id}/pay rate=2.8363636363636364
```

## Task 1

### Experiment 1 — Pod Kill Under Load

Hypothesis:

> If I delete one gateway pod while traffic is flowing, the service should stay available because the remaining four gateway pods will continue serving traffic until Kubernetes recreates the fifth one.

Commands:

```bash
VICTIM=$(docker exec k3d-quickticket-server-0 kubectl get pods -l app=gateway -o jsonpath='{.items[0].metadata.name}')
docker exec k3d-quickticket-server-0 kubectl delete pod "$VICTIM" --wait=false
docker exec k3d-quickticket-server-0 kubectl get pods -l app=gateway -o wide
```

Observations:

```text
victim=gateway-b74ddbdcd-hqvdr start=21:44:04
21:44:04 ready=4 total=6
21:44:07 ready=4 total=5
21:44:09 ready=4 total=5
21:44:11 ready=5 total=5
recovered=21:44:11
```

```text
NAME                      READY   STATUS    RESTARTS   AGE
gateway-b74ddbdcd-wdr97   1/1     Running   0          7s
```

```bash
docker exec k3d-quickticket-server-0 sh -lc '
  POD=$(kubectl get pods -n monitoring -l app=prometheus -o jsonpath="{.items[0].metadata.name}")
  kubectl exec -n monitoring "$POD" -- wget -qO- \
    "http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bpath%3D%22%2Fevents%22%2Cstatus%3D~%225..%22%7D%5B3m%5D))"
' | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["result"])'
```

```text
[]
```

```bash
docker exec k3d-quickticket-server-0 sh -lc '
  POD=$(kubectl get pods -n monitoring -l app=prometheus -o jsonpath="{.items[0].metadata.name}")
  kubectl exec -n monitoring "$POD" -- wget -qO- \
    "http://localhost:9090/api/v1/query?query=sum%20by%20(pod)(rate(gateway_requests_total%5B1m%5D))"
' | python3 -c 'import json,sys; rows=json.load(sys.stdin)["data"]["result"]; rows=sorted(rows,key=lambda r:r["metric"].get("pod","")); [print(r["metric"].get("pod","")+" rate="+r["value"][1]) for r in rows]'
```

```text
gateway-b74ddbdcd-hxk5f rate=2.8
gateway-b74ddbdcd-k485j rate=2.6545454545454543
gateway-b74ddbdcd-sxzng rate=2.618181818181818
gateway-b74ddbdcd-wdr97 rate=1.8219324999999997
gateway-b74ddbdcd-wgppc rate=2.618181818181818
```

Comparison:

The hypothesis was correct. Kubernetes restored `5/5` ready gateway pods in about `7` seconds, `GET /events` produced no `5xx`, and the new pod immediately started receiving traffic.

To improve resilience against this failure, I would add an HPA on `gateway` so the service keeps more spare capacity during sudden pod loss.

### Experiment 2 — Payment Latency Injection

Hypothesis:

> If payments takes 2 seconds per request, `/pay` should become slower but still succeed because `2s < GATEWAY_TIMEOUT_MS=5000`; if payments takes 6 seconds, the gateway should start returning `504` after about 5 seconds.

Commands:

```bash
docker exec k3d-quickticket-server-0 kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
docker exec k3d-quickticket-server-0 kubectl rollout status deployment/payments --timeout=60s
docker exec k3d-quickticket-server-0 kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
docker exec k3d-quickticket-server-0 kubectl rollout status deployment/payments --timeout=60s
docker exec k3d-quickticket-server-0 kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
```

Observations:

The `2000ms` run started at `21:45:20`. With the fixed load generator, the gateway did not emit `5xx`, while `/reserve/{id}/pay` p99 moved into the multi-second range:

```text
--- error ratio 2000ms fixed loadgen ---
[{'metric': {}, 'value': [1783105932.036, '0']}]
```

```text
--- p99 by path 2000ms fixed loadgen ---
/events p99=0.03850000000000002
/events/{id}/reserve p99=0.09859375000000001
/health p99=0.0976388888888889
/reserve/{id}/pay p99=2.485
```

A direct end-to-end probe also confirmed that the payment completed successfully in about 2 seconds:

```text
pay_http=200 pay_total=2.028891s
{"order_id":"4a021aa4-bfb9-44c5-b51d-6355ee9064a0","event_id":2,"quantity":1,"total_cents":0,"status":"confirmed"}
```

The `6000ms` run started at `21:46:41`. The direct probe then timed out almost exactly at the gateway timeout:

```text
pay_http=504 pay_total=5.012017s
{"detail":"Payment service timeout"}
```

Comparison:

The hypothesis was correct. At `2000ms`, the system degraded only on the `/pay` path; read paths stayed fast and the gateway returned `200`. At `6000ms`, the gateway protected itself and cut the request off at about `5s`.

To improve resilience against this failure, I would separate read and write SLOs and alert on `/pay` latency before the timeout is hit.

### Experiment 3 — Redis Failure

Hypothesis:

> If Redis goes down, listing events should still work because it reads from PostgreSQL, but reservations should fail because the reservation hold is stored in Redis.

Commands:

```bash
docker exec k3d-quickticket-server-0 kubectl scale deployment/redis --replicas=0
docker exec k3d-quickticket-server-0 kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'echo "GET /events:"; curl -s -o /tmp/events.out -w "%{http_code} %{time_total}s\n" http://gateway:8080/events; cat /tmp/events.out;
         echo "POST /reserve:"; curl -s -o /tmp/reserve.out -w "%{http_code} %{time_total}s\n" -X POST -H "Content-Type: application/json" -d "{\"quantity\":1}" http://gateway:8080/events/1/reserve; cat /tmp/reserve.out;
         echo "GET /health:"; curl -s http://gateway:8080/health'
docker exec k3d-quickticket-server-0 kubectl scale deployment/redis --replicas=1
docker exec k3d-quickticket-server-0 kubectl wait --for=condition=Available deployment/redis --timeout=60s
```

Observations:

```text
scale redis to 0 at 21:52:12
21:52:12 redis_pods=1
21:52:14 redis_pods=0
```

Immediate user-facing checks:

```text
GET /events:
200 0.024457s
...
POST /reserve:
500 0.002779s
Internal Server Error
GET /health:
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

After the Redis health cache expired, the system started reporting degradation:

```text
{"status":"degraded","checks":{"events":"degraded","payments":"ok","circuit_payments":"CLOSED"}}
```

```text
NAME                      READY   STATUS    RESTARTS   AGE
events-5b65b6bcfb-42vmn   0/1     Running   0          157m
```

Comparison:

The hypothesis was mostly correct. Reads stayed available and reservations failed immediately, but the first `/health` response was still `healthy` because the events service caches the Redis reachability check for a few seconds.

To improve resilience against this failure, I would return an explicit `503` for reservation attempts when Redis is unavailable instead of a generic `500`.

## Task 2

Scenario:

I combined two payment degradations under higher load:

- `PAYMENT_FAILURE_RATE=0.3`
- `PAYMENT_LATENCY_MS=500`
- `mixedload` scaled from `2` to `5` replicas

Commands:

```bash
docker exec k3d-quickticket-server-0 kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
docker exec k3d-quickticket-server-0 kubectl scale deployment/mixedload --replicas=5
docker exec k3d-quickticket-server-0 kubectl rollout status deployment/payments --timeout=60s
docker exec k3d-quickticket-server-0 kubectl rollout status deployment/mixedload --timeout=60s
```

Observations over the `22:01:33` to `22:05:16` window:

```text
--- error ratio after 2m ---
[{'metric': {}, 'value': [1783105419.502, '0.08838133068520358']}]
```

```text
--- p99 by path after 2m ---
/events p99=0.1439999999999975
/events/{id}/reserve p99=0.2492500000000002
/health p99=0.3974999999999989
/reserve/{id}/pay p99=0.7475
```

```text
--- error ratio after 3m30s ---
[{'metric': {}, 'value': [1783105510.205, '0.09832841691248771']}]
```

```text
--- p99 by path after 3m30s ---
/events p99=0.06793749999999985
/events/{id}/reserve p99=0.09949999999999973
/health p99=0.12699999999999867
/reserve/{id}/pay p99=0.7474999999999999
```

The first golden signal to react was error rate: it climbed to about `8.8%` after two minutes and then to about `9.8%`. The worst path was `/reserve/{id}/pay`; its p99 latency was about `0.75s`, while reads remained much lower.

Weakest link:

`payments` was the weakest link. Once it became both slow and unreliable, the write path degraded first while reads stayed comparatively healthy.

To make this part more resilient, I would add a circuit breaker or queue-based decoupling around payment processing.

## Bonus Task

Chosen weakness:

Experiment 2 showed that the gateway timeout was the limiting factor for slow-but-eventually-successful payments. With `PAYMENT_LATENCY_MS=6000` and `GATEWAY_TIMEOUT_MS=5000`, `/pay` returned `504` after about five seconds.

I changed the gateway timeout in `k8s/gateway.yaml`:

```diff
-            - name: GATEWAY_TIMEOUT_MS
-              value: "5000"
+            - name: GATEWAY_TIMEOUT_MS
+              value: "7000"
```

Before:

```text
pay_http=504 pay_total=5.012017s
{"detail":"Payment service timeout"}
```

Prometheus also showed that the old `5000ms` timeout made the canary fail all `/pay` requests under `PAYMENT_LATENCY_MS=6000`:

```bash
docker exec k3d-quickticket-server-0 sh -lc '
  POD=$(kubectl get pods -n monitoring -l app=prometheus -o jsonpath="{.items[0].metadata.name}")
  kubectl exec -n monitoring "$POD" -- wget -qO- \
    "http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Brs_hash%3D%2264d65474f4%22%2Cpath%3D%22%2Freserve%2F%7Bid%7D%2Fpay%22%2Cstatus%3D~%225..%22%7D%5B1m%5D))%2Fsum(rate(gateway_requests_total%7Brs_hash%3D%2264d65474f4%22%2Cpath%3D%22%2Freserve%2F%7Bid%7D%2Fpay%22%7D%5B1m%5D))"
'
```

```text
[{'metric': {}, 'value': [1783106319.699, '1']}]
```

After applying the same timeout change to the live gateway rollout and re-running the same `PAYMENT_LATENCY_MS=6000` experiment:

```text
pay_http=200 pay_total=6.017019s
{"order_id":"1243c89b-260c-4c7c-b80e-445519b57362","event_id":2,"quantity":1,"total_cents":0,"status":"confirmed"}
```

The Prometheus query for the stable `7000ms` ReplicaSet dropped to zero failed `/pay` requests:

```bash
docker exec k3d-quickticket-server-0 sh -lc '
  POD=$(kubectl get pods -n monitoring -l app=prometheus -o jsonpath="{.items[0].metadata.name}")
  kubectl exec -n monitoring "$POD" -- wget -qO- \
    "http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Brs_hash%3D%2274b4f47bd%22%2Cpath%3D%22%2Freserve%2F%7Bid%7D%2Fpay%22%2Cstatus%3D~%225..%22%7D%5B1m%5D))%2Fsum(rate(gateway_requests_total%7Brs_hash%3D%2274b4f47bd%22%2Cpath%3D%22%2Freserve%2F%7Bid%7D%2Fpay%22%7D%5B1m%5D))"
'
```

```text
[{'metric': {}, 'value': [1783106590.217, '0']}]
```

So the fix removed the timeout failure for this scenario. The tradeoff is that users now wait longer for a slow payment backend, and slow upstreams keep gateway resources busy for longer.
