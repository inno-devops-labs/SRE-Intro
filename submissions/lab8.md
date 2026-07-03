# Lab 8 — Chaos Engineering: Break Things on Purpose

## Task 1 — Three Chaos Experiments

### Experiment 1 — Pod Kill Under Load

**Hypothesis:** If I delete one gateway pod while traffic is flowing, 5xx errors will start happening during the readiness gap, but because the Rollout uses 5 replicas, Kubernetes will replace the pod automatically. Will take about 30 seconds for the pod to be fully replaced.

**Commands:**
```bash
[ustkost@prime SRE-Intro]$ VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
[ustkost@prime SRE-Intro]$ echo "Killing $VICTIM at $(date +%H:%M:%S)"
Killing pod/gateway-7598dd5fc4-cddwc at 23:30:43
[ustkost@prime SRE-Intro]$ kubectl delete "$VICTIM"
pod "gateway-7598dd5fc4-cddwc" deleted from default namespace
```

**Observation:** killed the pod at 23:31:37, Kubernetes detected it and at 23:31:48 a new pod was already running (approximately 11s)

**Comparison:** The hypothesis was correct, but the pod recovery was faster than expected (11 vs 30 seconds)

**To improve resilience against this failure, I would:** configure proper readiness and liveness probes with appropriate thresholds to minimize downtime during pod startup

### Experiment 2 — Payment Latency Injection

**Hypothesis:** If payments takes 2 seconds per request, p99 latency will quickly rise, but error rate will remain the same  because the timeout is 5 seconds. Other services will be unaffected.

**Commands:**
```bash
[ustkost@prime SRE-Intro]$ kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
deployment.apps/payments env updated
[ustkost@prime SRE-Intro]$ kubectl rollout status deployment/payments --timeout=30s
deployment "payments" successfully rolled out
```

**Observation:** 
```
Error rate: 0%
latency p99 /health: 0.27 seconds
latency p99 /events: 0.19 seconds
latency p99 /reserve/{id}/pay: 2.391 seconds
```

**Comparison:** The hypothesis was confirmed: other services are unaffected, error rate is zero, p99 payments latency rose to ~2.3 seconds


**To improve resilience against this failure, I would:** add more metrics and alerts specifically for the payment service endpoints latency as its the most expected to be the bottleneck of the entire application

### Experiment 3 — Redis Failure

**Hypothesis:** If Redis goes down, `events` service will partially work: some endpoints are Postgres only, some will fail because they need Redis. `/health` status will show degraded.


**Commands:**
```bash
[ustkost@prime SRE-Intro]$ kubectl scale deployment/redis --replicas=0
deployment.apps/redis scaled
[ustkost@prime SRE-Intro]$ kubectl get pods -l app=redis -w    # wait until gone
```

**Observation:** 
`GET /events` endpoint works fine, as it does not need Redis to work; `POST /reserve` times out returning 504: it doesnt work without redis.
Most interesting: `GET /health` shows `"status": "healthy"`

**Comparison:** Hypothesis partially was correct: partial failure of `events` service happened. But I was wrong about the `/health` status: it shows healthy instead of degraded.

**To improve resilience against this failure, I would:** Improve events `/health` endpoint to also verify Redis health
