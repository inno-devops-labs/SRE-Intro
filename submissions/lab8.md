# Lab 8 - Chaos Engineering Report

## Task 1 - Chaos Experiments

### Experiment 1 - Pod Kill Under Load

Hypothesis written before running:

> If I delete one gateway pod while traffic is flowing, user traffic will mostly continue and Kubernetes will create a replacement within about 30 seconds because the Service can route to the remaining gateway pods and the Rollout/ReplicaSet will self-heal.

Commands run:

```bash
kubectl get pods -l app=gateway -o name
kubectl delete pod/gateway-58ffc77799-4kvk9
kubectl get pods -l app=gateway
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(pod)+(rate(gateway_requests_total%5B1m%5D))'
```

Observations:

- Deleted `pod/gateway-58ffc77799-4kvk9` at `2026-06-28T14:51:08+03:00`.
- At `2026-06-28T14:51:19+03:00`, replacement `gateway-58ffc77799-6wc2c` was already `1/1 Running` with age `8s`.
- At `2026-06-28T14:51:26+03:00`, six gateway pods were `1/1 Running`; one older extra Deployment pod was terminating.
- 5xx increase over 3 minutes at `2026-06-28T14:51:35+03:00`: `3.0821170506912443`.
- Per-pod request rates showed traffic continuing on the surviving pods, for example `gateway-58ffc77799-6ccn6 = 2.545 rps`, `gateway-58ffc77799-c22ng = 2.491 rps`, and the new `gateway-58ffc77799-6wc2c = 0.313 rps`.

Comparison:

- The hypothesis mostly matched reality. Kubernetes replaced the pod quickly and traffic continued on the remaining pods.
- The surprise was the extra gateway Deployment pod, which made the selected gateway pod count 6 instead of the expected 5.
- To improve resilience against this failure, I would remove the duplicate gateway Deployment so the Service only targets the Rollout-managed replicas and experiment results are less ambiguous.

### Experiment 2 - Payment Latency Injection

Hypothesis written before running:

> If payments takes 2 seconds per request, `/reserve/{id}/pay` latency will rise to about 2 seconds but gateway 5xx should not increase because 2000ms is below `GATEWAY_TIMEOUT_MS=5000`.

Commands run:

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=60s
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
kubectl run timeout-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" -X POST http://gateway:8080/reserve/timeout-test/pay'
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
```

Observations:

- Started `PAYMENT_LATENCY_MS=2000` at `2026-06-28T14:51:43+03:00`.
- After the rate window filled, at `2026-06-28T14:55:18+03:00`, 5xx ratio was `0`.
- p99 at the same timestamp:
  - `/reserve/{id}/pay = 2.485s`
  - `/events = 0.0098s`
  - `/events/{id}/reserve = 0.0248s`
  - `/health = 0.0252s`
- While preparing the measurement, event 1 reservation holds had saturated in Redis (`event:1:held = 50`), so I reset that transient counter to `0` to make mixedload produce valid `/pay` traffic again.
- Timeout observation: with `PAYMENT_LATENCY_MS=6000`, probe at `2026-06-28T14:55:46+03:00` returned `504 5.004744s`.

Comparison:

- The 2-second latency hypothesis matched: `/pay` p99 rose while 5xx stayed at 0 and read paths remained fast.
- The timeout observation also matched: gateway protected itself at about 5 seconds.
- The surprise was the reservation hold leak/saturation, which can hide payment-path degradation by stopping checkout traffic before it reaches payments.
- To improve resilience against this failure, I would add an alert on high p99 for `/reserve/{id}/pay` and fix reservation hold cleanup so failed or confirmed checkouts do not permanently reduce availability.

### Experiment 3 - Redis Failure

Hypothesis written before running:

> If Redis goes down, `/events` should still work because it reads from Postgres, `/reserve` should fail or degrade because holds require Redis, and `/health` should report degraded.

Commands run:

```bash
kubectl scale deployment/redis --replicas=0
kubectl get deployment redis
kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'curl -v --max-time 3 http://gateway:8080/events'
kubectl get endpoints gateway -o yaml
kubectl get pods -l app=gateway
kubectl describe pod gateway-58ffc77799-6ccn6
kubectl scale deployment/redis --replicas=1
kubectl wait --for=condition=Available deployment/redis --timeout=60s
```

Observations:

- Redis scaled down at `2026-06-28T14:56:27+03:00`; `deployment/redis` showed `0/0`.
- Probe through `http://gateway:8080/events` failed before reaching the app: `curl: (7) Failed to connect to gateway:8080 ... Connection refused`.
- Because the gateway Service had no ready endpoints, `/reserve` and `/health` through the Service were also unreachable during the outage. The failure happened before requests could reach the application handlers.
- `kubectl get endpoints gateway -o yaml` showed all gateway IPs under `notReadyAddresses` and no ready endpoint subset.
- `kubectl get pods -l app=gateway` showed all gateway pods `0/1 Running` with restarts beginning.
- `kubectl describe pod gateway-58ffc77799-6ccn6` showed both probes used `/health`; readiness failed with `HTTP probe failed with statuscode: 503`, and liveness killed the container after `/health` returned 503.
- Redis was restored at `2026-06-28T14:57:20+03:00`; gateway recovered after liveness restarts.

Comparison:

- The hypothesis was partly wrong. I expected only reservation and health degradation, but Kubernetes removed every gateway pod from the Service because gateway readiness was tied to dependency-sensitive `/health`.
- The surprise was a cascading control-plane failure: Redis down caused events health degradation, gateway health degradation, gateway NotReady, empty gateway Service endpoints, and gateway restarts.
- To improve resilience against this failure, I would separate local liveness/readiness from dependency health so Kubernetes does not restart/eject healthy gateway processes during a downstream outage.

## Task 2 - Combined Failure Scenario

Scenario design:

- Degraded dependencies: `payments` with `PAYMENT_FAILURE_RATE=0.3` and `PAYMENT_LATENCY_MS=500`, `events` with `DB_MAX_CONNS=3`, and checkout load increased to 3 replicas.
- I used event 3 for the later checkout load samples because event 1 became saturated by held reservations during the earlier experiments.

Commands run:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3

kubectl create deployment mixedload-event3 --image=curlimages/curl:latest -- sh -c '
  while true; do
    curl -s http://gateway:8080/events > /dev/null

    RES=$(curl -s -X POST http://gateway:8080/events/3/reserve \
      -H "Content-Type: application/json" \
      -d "{"quantity":1}")

    RID=$(echo "$RES" | sed -n "s/.*reservation_id":"\([^"]*\).*/\1/p")

    if [ -n "$RID" ]; then
      curl -s -X POST http://gateway:8080/reserve/$RID/pay > /dev/null
    fi

    sleep 0.3
  done
'

kubectl scale deployment/mixedload-event3 --replicas=3

kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(path,status)+(rate(gateway_requests_total%5B1m%5D))'
```

Observations:

- Scenario started at `2026-06-28T14:58:25+03:00`.
- First event-1 sample at `2026-06-28T15:00:03+03:00`: 5xx ratio `0.000894454382826476`, but `/reserve/{id}/pay` was `NaN` because event 1 checkout traffic was blocked by reservation conflicts.
- Event-3 checkout sample at `2026-06-28T15:02:29+03:00`:
  - Overall 5xx ratio: `0.05739795918367347`.
  - p99: `/reserve/{id}/pay = 0.7475s`, `/events = 0.0118s`, `/events/{id}/reserve = 0.0220s`, `/health = 0.0099s`.
  - Rates: `/reserve/{id}/pay status=200 = 2.018 rps`, `/reserve/{id}/pay status=500 = 0.818 rps`, `/events status=200 = 4.8 rps`, `/events/{id}/reserve status=200 = 2.836 rps`.
- Follow-up sample at `2026-06-28T15:03:45+03:00`:
  - Overall 5xx ratio returned to `0`.
  - `/reserve/{id}/pay` became `NaN`.
  - `/events/{id}/reserve status=409 = 8.855 rps`, showing reservation saturation had taken over and prevented payment traffic.
- Restored at `2026-06-28T15:03:54+03:00`:
  - `PAYMENT_FAILURE_RATE=0.0`
  - `PAYMENT_LATENCY_MS=0`
  - `DB_MAX_CONNS=10`
  - `mixedload=2`
  - deleted `mixedload-event3`

Weakest link:

- The weakest link was reservation state accounting. Payment failures and confirmations did not reliably release held inventory, so the system shifted from payment errors to reservation conflicts and stopped exercising the payment path.
- The golden signal that reacted first in the useful event-3 sample was error rate: `/reserve/{id}/pay status=500` appeared immediately from payment injection. Latency rose on `/reserve/{id}/pay`, but reservation saturation quickly became the dominant symptom.
- The path with the worst useful latency amplification was `/reserve/{id}/pay`: its p99 reached `0.7475s`, while `/events` stayed near `0.0118s` and `/events/{id}/reserve` stayed near `0.0220s`.
- I would make the reservation workflow more resilient by decrementing Redis held counters on confirmation, expiry, and payment failure, and by making reservation holds idempotent and observable with a hold-count alert.

## Bonus Task - Resilience Improvement

Weakness chosen:

- Redis failure caused gateway readiness and liveness failures because both probes used `/health`, and `/health` depends on downstream services.

Change made:

```diff
diff --git a/k8s/gateway.yaml b/k8s/gateway.yaml
@@
           livenessProbe:
             httpGet:
-              path: /health
+              path: /metrics
               port: 8080
@@
           readinessProbe:
             httpGet:
-              path: /health
+              path: /metrics
               port: 8080
```

Applied live for proof:

```bash
kubectl patch rollout gateway --type=json -p='[{"op":"replace","path":"/spec/template/spec/containers/0/livenessProbe/httpGet/path","value":"/metrics"},{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/metrics"}]'
kubectl patch deployment gateway --type=json -p='[{"op":"replace","path":"/spec/template/spec/containers/0/livenessProbe/httpGet/path","value":"/metrics"},{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/metrics"}]'
/home/ernest/.local/bin/kubectl-argo-rollouts promote gateway --full
```

Before fix:

- During Redis outage at `2026-06-28T14:56:51+03:00`, gateway Service had no ready endpoints; `curl http://gateway:8080/events` failed with connection refused.
- All gateway pods were `0/1 Running`; one described pod showed liveness `/health` failed with `503` and the container was restarted.

After fix:

- During Redis outage at `2026-06-28T15:06:44+03:00`, gateway pods stayed ready with zero restarts:
  - six gateway pods were `1/1 Running`, `RESTARTS=0`
  - `gateway` Endpoints stayed populated: `10.42.0.48:8080,10.42.0.49:8080,10.42.1.110:8080 + 3 more`
- HTTP behavior after fix:
  - `events 502 0.004019s`
  - `reserve 502 0.004474s`
  - `health 503 {"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}`

Trade-off:

- The fix prevents Kubernetes from cascading a downstream outage into gateway endpoint removal and restarts, but it also means readiness no longer blocks traffic when dependencies are down, so alerts and graceful application-level degradation become more important.

## Final Cleanup / Restore

Final restore verification:

```bash
kubectl get deployment mixedload redis events payments gateway
kubectl get rollout gateway -o wide
kubectl run final-health --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'curl -s http://gateway:8080/health'
kubectl delete -f labs/lab8/mixedload.yaml
kubectl exec deployment/redis -- redis-cli set event:1:held 0
```

Observed:

- Before cleanup: `mixedload 2/2`, `redis 1/1`, `events 1/1`, `payments 1/1`, extra `gateway` Deployment `1/1`.
- Rollout `gateway`: desired `5`, current `5`, up-to-date `5`, available `5`.
- Final health: `{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}`.
- Cleanup completed: `deployment.apps "mixedload" deleted`; reset transient Redis hold counter with `event:1:held = 0`.
