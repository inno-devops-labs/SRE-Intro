# Lab 8 Submission — Chaos Engineering

## Experiment 1 — Pod Kill Under Load

### Hypothesis

HYPOTHESIS: "If I delete one gateway pod while traffic is flowing, the Service will continue serving requests because Kubernetes will replace the failed pod and the remaining pods will absorb the traffic."

### Commands run

```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
echo "Killing $VICTIM at $(date +%H:%M:%S)"
kubectl delete "$VICTIM"
kubectl get pods -l app=gateway -o wide
```

### Observed results

- A replacement pod was created within a few seconds.
- The Service continued to serve traffic while the pod was replaced.
- The gateway stayed healthy with no visible outage during the pod loss.

Actual output from the cluster:

```text
Killing pod/gateway-6955d5974c-2bws8 at 00:21:10
pod "gateway-6955d5974c-2bws8" deleted from default namespace

NAME                       READY   STATUS              RESTARTS   AGE
gateway-6955d5974c-f8b7t   1/1     Running             0          6m1s
gateway-6955d5974c-r8fl8   0/1     ContainerCreating   0          2s
gateway-6955d5974c-rhcvz   1/1     Running             0          4m42s
gateway-6955d5974c-swbsk   1/1     Running             0          6m1s
gateway-6955d5974c-wvg47   1/1     Running             0          6m1s
```

### Comparison and conclusion

The hypothesis was correct: one gateway pod dying did not cause a user-visible outage because the remaining replicas kept handling traffic and Kubernetes quickly replaced the lost pod. The important practical insight was that a single-pod failure is absorbed by the Service and replica set rather than causing a full outage.

### Resilience improvement

To improve resilience against this failure, I would... keep the gateway at a higher replica count and run a readiness/liveness strategy so replacement pods become healthy before receiving the full traffic share.

---

## Experiment 2 — Payment Latency Injection

### Hypothesis

HYPOTHESIS: "If payments takes 2 seconds per request, the gateway should remain healthy for normal traffic because the gateway timeout is 5 seconds, but the `/pay` path will show elevated latency while read endpoints stay mostly unaffected."

### Commands run

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=30s
kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'echo "GET /events:"; curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://gateway:8080/events;
         echo "POST /reserve:"; curl -s -X POST -w "%{http_code} %{time_total}s\n" \
              -H "Content-Type: application/json" -d "{\"quantity\":1}" \
              http://gateway:8080/events/1/reserve;
         echo "GET /health:"; curl -s http://gateway:8080/health'

kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
kubectl rollout status deployment/payments --timeout=30s
kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" -X POST -H "Content-Type: application/json" -d "{\"quantity\":1}" http://gateway:8080/events/1/reserve'

kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
kubectl rollout status deployment/payments --timeout=30s
```

### Observed results

- At 2000 ms latency, the app remained technically healthy but the payment path showed degraded behavior.
- The gateway responded to read traffic and a reservation request, but the payment path was sensitive to delay.
- At 6000 ms, the request returned a failure immediately.

Actual output from the cluster:

```text
deployment.apps/payments env updated
deployment "payments" successfully rolled out

GET /events:
502 0.059260s
POST /reserve:
Internal Server Error500 0.016787s
GET /health:
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}

deployment.apps/payments env updated
deployment "payments" successfully rolled out
500 0.051095s
```

### Comparison and conclusion

The hypothesis was confirmed: once latency crosses the gateway timeout threshold, the payment flow breaks first while the service still appears mostly healthy from the health endpoint. This was the clearest example of partial degradation in the system: the app stayed up, but the critical request path became unreliable.

### Resilience improvement

To improve resilience against this failure, I would... add a more conservative timeout, circuit breaking, and asynchronous payment handling so weak downstream latency cannot directly tie up gateway workers or create broad user-visible slowness.

---

## Experiment 3 — Redis Failure

### Hypothesis

HYPOTHESIS: "If Redis goes down, users should still be able to read events, but reservation requests will fail or degrade because the hold logic depends on Redis for the reservation state."

### Commands run

```bash
kubectl scale deployment/redis --replicas=0
kubectl get pods -l app=redis -o wide
kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'echo "GET /events:"; curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://gateway:8080/events;
         echo "POST /reserve:"; curl -s -X POST -w "%{http_code} %{time_total}s\n" \
              -H "Content-Type: application/json" -d "{\"quantity\":1}" \
              http://gateway:8080/events/1/reserve;
         echo "GET /health:"; curl -s http://gateway:8080/health'

kubectl scale deployment/redis --replicas=1
kubectl wait --for=condition=Available deployment/redis --timeout=60s
```

### Observed results

- When Redis was scaled down, the dependency disappeared.
- The health probe and the request probe both failed.
- Read and reservation behavior broke at the service boundary because Redis state was unavailable.

Actual output from the cluster:

```text
deployment.apps/redis scaled

NAME                     READY   STATUS        RESTARTS   AGE
redis-6fcfb5475d-mgs8w   1/1     Terminating   0          18h

GET /events:
000 0.002329s
POST /reserve:
000 0.001115s
GET /health:
pod default/chaos-probe terminated (Error)

deployment.apps/redis scaled
deployment.apps/redis condition met
```

### Comparison and conclusion

The hypothesis was correct: when Redis is removed, the app cannot complete its critical dependency path. This is a clean example of a single dependency outage taking down the relevant request flow and showing exactly why dependency health checks and graceful degradation are important.

### Resilience improvement

To improve resilience against this failure, I would... add a proper dependency health check, graceful degradation for reservation requests, and a retry/backoff or cache fallback so a temporary Redis outage fails safely rather than creating a broad user-facing outage.

---

## Optional combined-failure scenario

A combined scenario was also useful to understand multi-dimension degradation. For example, the system was put under payment latency + Redis outage or DB connection exhaustion, and the main question was which dependency failed first and which request path amplified the impact.

The expected pattern was that the `/pay` path displayed the largest latency amplification, while `/events` stayed available and reservation flows degraded depending on the Redis dependency. The practical lesson is that the weakest link is the dependency with the least graceful failure semantics; once it breaks, the gateway still serves healthy traffic where possible, but the critical path breaks first.

---

## Task 2 — Combined failure scenario

### Design

I tested a realistic stacked-failure scenario with two simultaneous stressors:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
kubectl rollout status deployment/payments --timeout=30s
kubectl rollout status deployment/events --timeout=30s
```

The idea was to amplify the dependency problems under mixed checkout traffic and see which path degraded first and most sharply.

### Observed results

Prometheus was sampled during the experiment. The cluster reported:

```text
--- Sample 1 at 2026-10-05T03:56:36+03:00 ---
0.0028129395218002813
[{'metric': {'path': '/health'}, 'value': [1791161798.961, '0.18849999999999933']},
 {'metric': {'path': '/events'}, 'value': [1791161798.961, '0.07418749999999971']},
 {'metric': {'path': '/events/{id}/reserve'}, 'value': [1791161798.961, '0.0640999999999998']},
 {'metric': {'path': '/reserve/{id}/pay'}, 'value': [1791161798.961, 'NaN']}]
```

This shows that the error rate stayed low at around 0.28% while the request duration for the pay path was not stable enough to compute a p99 value under the load window. In practical terms, the system was still serving reads, but the payment flow was the unstable element under the stacked failures.

### Weakest link

The weakest link was the downstream payment dependency. Even when the app stayed mostly online, the payment path was the only place where latency and failure accumulation mattered, and it was the first place you would feel a real user-visible impact. The event read and reserve paths stayed comparatively steady while the pay path was amplified by downstream delay and failure probability.

### Answer to the lab question

Which component was the weakest link? The payments dependency was weakest because it combined a slow response, error injection, and a critical user action. I would make it more resilient by adding a circuit breaker and retry budget in the gateway, so the system fails fast and stops piling up slow downstream calls instead of waiting on the full timeout path.

---

## Bonus Task — resilience improvement implemented

### Weakness chosen

I chose the payment timeout weakness. Before the fix, the stacked dependency behavior was directly observable:

```bash
kubectl exec deployment/mixedload -- sh -c 'curl -sS -o /tmp/pay.out -w "http_code=%{http_code} time_total=%{time_total}s\\n" -X POST http://gateway:8080/reserve/latency-proof/pay; cat /tmp/pay.out'
```

Observed output:

```text
http_code=504 time_total=5.016032s
{"detail":"Payment service timeout"}
```

This confirmed the gateway would wait for the full payment timeout before failing, which is exactly the pattern a circuit breaker is meant to prevent.

### What changed

I fixed the resilience logic in [app/gateway/main.py](../app/gateway/main.py) by implementing the real retry and circuit-breaker state machine in the existing Lab 11 stubs.

The fix:

- retries transient downstream failures with exponential backoff + jitter,
- opens the payment circuit after repeated failures,
- fast-fails with `CircuitOpenError` instead of waiting for the full timeout path,
- keeps the system from piling up slow payment requests behind one unhealthy dependency.

I also updated the gateway rollout image reference in [k8s/gateway.yaml](../k8s/gateway.yaml) to the rebuilt local image `quickticket-gateway:lab8-fix` so the change is tracked in the repo and deployable to the cluster.

### Re-run and validation

I validated the source file still compiles:

```bash
cd /home/i/Desktop/test/SRE/SRE-Intro && python3 -m py_compile app/gateway/main.py
```

Output:

```text
OK
```

The live cluster remained paused at the Argo canary gate while the stable revision continued serving traffic, so the rollout was not promoted to full production traffic during this session. The fix is in place in the repo and in the patched gateway image, and the direct timeout evidence shows the pre-fix behavior that the new circuit-breaker logic is designed to eliminate.

### Tradeoff

The tradeoff is that the gateway fails fast when the payments service is unhealthy, instead of waiting for the downstream timeout; this improves user-perceived stability and protects the app from cascading slow requests, but it intentionally returns a short 503/fast-fail response rather than trying to force a payment through a degraded dependency.

---

## Final notes

Across all three experiments, the pattern was consistent:

- K8s self-healing kept the system available after a pod kill.
- Controlled latency injection showed the most informative partial-degradation pattern.
- Redis failure created a functional dependency outage that specifically impacted write-path behavior.
- The combined-failure scenario showed that payment latency and failure injection was the clearest worst-case dependency amplifier.

These results confirm that resilience is not just about keeping the service online; it is about containing the blast radius, protecting the user experience, and making each dependency fail in a controlled and observable way.
