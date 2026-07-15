*Emil Nabiullin, e.nabiullin@innopolis.university*
---
# Lab 8 - Chaos Engineering

## Repository state

By the start of Lab 8 the repository already had the required Lab 7 base:

- `gateway` was running as an Argo Rollouts `Rollout` with 5 replicas
- in-cluster Prometheus from `labs/lab7/prometheus.yaml` was already deployed
- `labs/lab8/mixedload.yaml` was already present for full checkout traffic

For this lab I only kept one config change in the repo for the bonus improvement:

- `k8s/gateway.yaml`: `GATEWAY_TIMEOUT_MS` was reduced from `5000` to `2000`
- `submissions/lab8.md` was added

The same local runtime limitation from Lab 7 still existed: k3d could not pull the private GHCR image from inside the node runtime (`403 Forbidden`), so for the local verification of the bonus rollout I temporarily patched the live rollout to use `imagePullPolicy: IfNotPresent`. I did not keep that workaround in the final repo diff.

## Setup

I started the Lab 8 load generator and checked that Prometheus was receiving traffic:

```bash
kubectl apply -f labs/lab8/mixedload.yaml
kubectl rollout status deployment/mixedload --timeout=60s
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%5B1m%5D))'
```

```text
deployment.apps/mixedload created
Waiting for deployment "mixedload" rollout to finish: 0 of 2 updated replicas are available...
Waiting for deployment "mixedload" rollout to finish: 1 of 2 updated replicas are available...
deployment "mixedload" successfully rolled out
```

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783092691.214,"12.036320665135975"]}]}}
```

The provided `mixedload` permanently buys tickets, so the seed dataset gets exhausted after a few minutes. For a stable write-path experiment I switched the live load to event `3` and increased its ticket pool:

```bash
kubectl exec deploy/postgres -- psql -U quickticket -d quickticket -c \
  "update events set total_tickets = 100000 where id = 3; select id,name,total_tickets from events where id = 3;"
```

```text
UPDATE 1
 id |        name         | total_tickets
----+---------------------+---------------
  3 | Cloud Native Summit |        100000
(1 row)
```

## Task 1 - Three chaos experiments

### Experiment 1 - Pod kill under load

Hypothesis:

`If I delete one gateway pod while traffic is flowing, users should mostly stay unaffected because the Service can immediately send traffic to the remaining four pods while the ReplicaSet creates a replacement.`

Commands:

```bash
VICTIM=$(kubectl get pods -l app=gateway -o jsonpath='{.items[0].metadata.name}')
echo "start=$(date --iso-8601=seconds)"
echo "victim=$VICTIM"
kubectl delete pod "$VICTIM" --wait=false
kubectl get pods -l app=gateway --no-headers
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22,path!%3D%22/health%22%7D%5B1m%5D))'
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(pod)(rate(gateway_requests_total%5B1m%5D))'
```

Observed:

```text
start=2026-07-03T18:34:05+03:00
victim=gateway-779469f7fd-6j8jt
pod "gateway-779469f7fd-6j8jt" deleted
t=2026-07-03T18:34:05+03:00 ready=4/5
gateway-779469f7fd-6j8jt   1/1   Terminating         3 (18m ago)   109m
gateway-779469f7fd-ks9pg   0/1   ContainerCreating   0             1s
...
t=2026-07-03T18:34:17+03:00 ready=5/5
gateway-779469f7fd-ks9pg   1/1   Running   0             12s
end=2026-07-03T18:34:17+03:00
```

```json
{"status":"success","data":{"resultType":"vector","result":[]}}
```

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"pod":"gateway-779469f7fd-sdmmk"},"value":[1783092868.653,"3.381818181818182"]},{"metric":{"pod":"gateway-779469f7fd-6j8jt"},"value":[1783092868.653,"2.3025333333333333"]},{"metric":{"pod":"gateway-779469f7fd-7zs7c"},"value":[1783092868.653,"3.290968926707759"]},{"metric":{"pod":"gateway-779469f7fd-jsvqb"},"value":[1783092868.653,"3.309090909090909"]},{"metric":{"pod":"gateway-779469f7fd-nk4jn"},"value":[1783092868.653,"3.2727867779414175"]},{"metric":{"pod":"gateway-779469f7fd-ks9pg"},"value":[1783092868.653,"0.45121666666666665"]}]}}
```

Comparison:

The hypothesis was mostly correct. Recovery to `5/5 Ready` took about 12 seconds, and I did not observe user-path 5xx in the 1-minute window after the kill. The per-pod rates show that traffic stayed on the surviving pods while the new pod warmed up and only gradually started receiving requests.

To improve resilience against this failure, I would add a second node and spread gateway replicas across nodes instead of proving only single-node pod self-healing.

### Experiment 2 - Payment latency injection

Hypothesis:

`If payments takes 2 seconds per request, /pay latency should increase but the gateway should still return mostly 2xx because 2000ms is below the current gateway timeout.`

Commands:

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=60s
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22,path!%3D%22/health%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%7Bpath!%3D%22/health%22%7D%5B1m%5D))'
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)(rate(gateway_request_duration_seconds_bucket%7Bpath!%3D%22/health%22%7D%5B1m%5D)))'
kubectl run pay-probe-2000b --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'RES=$(curl -s -X POST -H "Content-Type: application/json" -d "{\"quantity\":1}" http://gateway:8080/events/3/reserve); RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\([^\"]*\).*/\1/p"); echo "$RES"; if [ -n "$RID" ]; then curl -s -X POST -o /dev/null -w "pay_status=%{http_code} pay_time=%{time_total}s\n" http://gateway:8080/reserve/$RID/pay; fi'
```

Observed:

```text
start=2026-07-03T18:35:09+03:00
deployment.apps/payments env updated
deployment "payments" successfully rolled out
ready=2026-07-03T18:35:20+03:00
```

```json
{"status":"success","data":{"resultType":"vector","result":[]}}
```

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/reserve/{id}/pay"},"value":[1783093170.270,"2.485"]},{"metric":{"path":"/events/{id}/reserve"},"value":[1783093170.270,"0.1809989909011565"]},{"metric":{"path":"/events"},"value":[1783093170.270,"0.024843181798458777"]}]}}
```

```text
pay_status=200 pay_time=2.030836s
```

Comparison:

The hypothesis was correct. Business-path error rate stayed at zero (`result: []` from the Prometheus ratio query), while p99 for `/reserve/{id}/pay` rose to about `2.5s`. Read paths stayed fast, so the latency stayed localized to the payment path.

To improve resilience against this failure, I would make the gateway fail faster on obviously doomed payment calls instead of spending the full timeout budget.

### Experiment 3 - Redis failure

Hypothesis:

`If Redis goes down, listing events should degrade less than ticket reservation because reserve depends directly on Redis-backed holds, while health and probes may eventually mark the events service unhealthy.`

Commands:

```bash
kubectl scale deployment/redis --replicas=0
kubectl wait --for=delete pod -l app=redis --timeout=60s
kubectl run redis-events-clean --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'echo GET /events; curl -sS -w "\nstatus=%{http_code} time=%{time_total}s\n" http://gateway:8080/events'
kubectl run redis-reserve-clean --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'echo POST /events/3/reserve; curl -sS -X POST -w "\nstatus=%{http_code} time=%{time_total}s\n" -H "Content-Type: application/json" -d "{\"quantity\":1}" http://gateway:8080/events/3/reserve'
kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://gateway.default.svc.cluster.local:8080/health'
kubectl describe pod $(kubectl get pods -l app=events -o jsonpath='{.items[0].metadata.name}')
```

Observed:

```text
start=2026-07-03T19:04:49+03:00
deployment.apps/redis scaled
pod/redis-6d65768944-75qn9 condition met
down=2026-07-03T19:04:50+03:00
```

```text
GET /events
[{"id":1,"name":"Go Conference 2026","venue":"Main Hall A","date":"2026-09-15T09:00:00+00:00","total_tickets":100,"price_cents":5000,"available":84},{"id":4,"name":"Python Workshop","venue":"Lab 301","date":"2026-09-22T14:00:00+00:00","total_tickets":25,"price_cents":2000,"available":25},{"id":2,"name":"SRE Meetup","venue":"Room 204","date":"2026-10-01T18:00:00+00:00","total_tickets":30,"price_cents":0,"available":30},{"id":5,"name":"Kubernetes Deep Dive","venue":"Auditorium B","date":"2026-10-10T10:00:00+00:00","total_tickets":80,"price_cents":8000,"available":80},{"id":3,"name":"Cloud Native Summit","venue":"Expo Center","date":"2026-11-20T10:00:00+00:00","total_tickets":100000,"price_cents":15000,"available":94872}]
status=200 time=0.093047s
```

```text
{"detail":"Events service timeout"}
status=504 time=2.013263s
```

```json
{"status":"degraded","checks":{"events":"degraded","payments":"ok","circuit_payments":"CLOSED"}}
```

```text
Events:
  Warning  Unhealthy  6s   kubelet  Readiness probe failed: HTTP probe failed with statuscode: 503
  Warning  Unhealthy  1s   kubelet  Liveness probe failed: HTTP probe failed with statuscode: 503
```

Comparison:

The hypothesis was only partially correct. Listing events did keep working in the clean rerun, but reservation calls failed through the gateway timeout path, and then the events pod also accumulated readiness and liveness failures because `/health` depends on Redis. So this was worse than a simple “reserve only is broken” model.

To improve resilience against this failure, I would remove Redis dependency checks from the `events` liveness path and keep them only in readiness or in the reservation code path itself.

## Task 2 - Combined failure scenario

Scenario design:

I combined three stressors at the same time:

- `payments`: `PAYMENT_FAILURE_RATE=0.3`
- `payments`: `PAYMENT_LATENCY_MS=500`
- `events`: `DB_MAX_CONNS=3`
- `mixedload`: scaled from `2` to `3`

I chose this scenario because it stacks an unstable downstream dependency on top of local capacity pressure and makes it easier to see which golden signal reacts first.

Commands:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
kubectl rollout status deployment/payments --timeout=60s
kubectl rollout status deployment/events --timeout=60s
```

Observed over the window:

```text
start=2026-07-03T19:11:12+03:00
ready=2026-07-03T19:11:23+03:00
```

```text
sample=1 time=2026-07-03T19:11:23+03:00
error_ratio= 0
/events 0.15388116250251194
/events/{id}/reserve 0.14652729131711517
/reserve/{id}/pay 0.1851287916319499
```

```text
sample=2 time=2026-07-03T19:13:04+03:00
error_ratio= 0.1111111111111111
/events 0.09062500000000004
/events/{id}/reserve 0.12025000000000015
/reserve/{id}/pay 0.7475
```

```text
sample=3 time=2026-07-03T19:14:45+03:00
error_ratio= 0.10038610038610038
/events 0.18399999999999994
/events/{id}/reserve 0.21292857142857127
/reserve/{id}/pay 0.7475
```

Analysis:

The first golden signal that reacted was the error ratio: it moved from `0` in the first sample to about `11.1%` and then stayed around `10%` across the rest of the 3+ minute window. The worst latency amplification was consistently on `/reserve/{id}/pay`, which settled at about `0.75s` p99, while `/events` and `/events/{id}/reserve` remained much lower.

The weakest link was `payments`. The reduced DB connection cap on `events` made reserve noisier, but it did not dominate the user-visible behavior. The path that produced the sustained failures and the highest steady latency was the payment path.

To make this scenario more resilient, I would isolate payment latency/failure from the user flow earlier with a lower gateway timeout and then add payment-specific circuit breaker logic in code.

## Bonus task - Resilience improvement

Chosen weakness:

In Experiment 2, a doomed payment call could still occupy the full `5s` gateway timeout budget. That is too long for an obviously unhealthy downstream.

Repo change:

```yaml
- name: GATEWAY_TIMEOUT_MS
  value: "2000"
```

This is the only Lab 8 code/config change I kept in git.

### Before the fix

Commands:

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
kubectl rollout status deployment/payments --timeout=60s
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22,path%3D%22/reserve/%7Bid%7D/pay%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%7Bpath%3D%22/reserve/%7Bid%7D/pay%22%7D%5B1m%5D))'
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)(rate(gateway_request_duration_seconds_bucket%7Bpath%3D%22/reserve/%7Bid%7D/pay%22%7D%5B1m%5D)))'
kubectl run pay-probe-6000-before --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'RES=$(curl -s -X POST -H "Content-Type: application/json" -d "{\"quantity\":1}" http://gateway:8080/events/3/reserve); RID=$(echo "$RES" | sed -n "s/.*reservation_id\":\"\([^\"]*\).*/\1/p"); echo "$RES"; if [ -n "$RID" ]; then curl -s -X POST -o /tmp/pay.out -w "pay_status=%{http_code} pay_time=%{time_total}s\n" http://gateway:8080/reserve/$RID/pay; cat /tmp/pay.out; fi'
```

Observed:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783093289.195,"1"]}]}}
```

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/reserve/{id}/pay"},"value":[1783093289.196,"7.475"]}]}}
```

```text
pay_status=504 pay_time=5.014284s
{"detail":"Payment service timeout"}
```

### After the fix

I applied the new `k8s/gateway.yaml`, waited for the canary rollout to become healthy, and then ran the same `PAYMENT_LATENCY_MS=6000` scenario again. Because of the same GHCR limitation already described in Lab 7, I used a temporary live-only `imagePullPolicy: IfNotPresent` patch during this local verification step; that workaround is not part of the final repo diff.

Observed:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783094571.234,"1"]}]}}
```

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/reserve/{id}/pay"},"value":[1783094571.233,"2.485"]}]}}
```

```text
pay_status=504 pay_time=2.015836s
{"detail":"Payment service timeout"}
```

Comparison:

The fix did not change the availability result for a `6000ms` downstream call: `/pay` still failed with `504`, and the Prometheus error ratio for that path stayed at `1`. But it cut the user wait from about `5.01s` to `2.02s`, and p99 for `/reserve/{id}/pay` dropped from `7.475` to `2.485`. This is a real resilience improvement because the gateway fails faster and spends less time waiting on a downstream that is already beyond budget.

Trade-off:

The shorter timeout reduces user wait time, but it also means the gateway will give up earlier on slow payments that might still have succeeded between `2s` and `5s`.

## Cleanup and final state

I restored the injected faults, removed `mixedload`, and checked the final service health:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0
kubectl set env deployment/events DB_MAX_CONNS=10
kubectl delete -f labs/lab8/mixedload.yaml
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://gateway.default.svc.cluster.local:8080/health'
```

```json
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```
