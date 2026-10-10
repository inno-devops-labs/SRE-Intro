# Lab 8 — Chaos Engineering: Break Things on Purpose

Date: 2026-07-01
Branch: `feature/lab8`

## Setup notes

- Applied Lab 8 load generator: `kubectl apply -f labs/lab8/mixedload.yaml`.
- The live cluster initially had the Lab 7 `events` stub image, so `/events/1/reserve` returned 404 and did not exercise checkout. I rebuilt and imported the full local images:
  - `docker build -t quickticket-events:lab8 app/events`
  - `docker build -t quickticket-gateway:lab8 app/gateway`
  - `docker build -t quickticket-payments:lab8 app/payments`
  - `k3d image import quickticket-events:lab8 quickticket-gateway:lab8 quickticket-payments:lab8 -c quickticket`
- Switched live workloads to local `:lab8` images with `imagePullPolicy: Never`.
- Reset checkout inventory for sustained mixed load:
  - `DELETE FROM orders; UPDATE events SET total_tickets = 100000 WHERE id = 1;`
  - `redis-cli FLUSHALL`
- Baseline at `2026-07-01T17:29:46Z`:
  - Total RPS: `26.69166779557741`
  - `path/status`: `/events 200 = 9.8003 rps`, `/events/{id}/reserve 200 = 5.4911 rps`, `/reserve/{id}/pay 200 = 5.5820 rps`
  - Baseline p99: `/events = 0.058s`, `/events/{id}/reserve = 0.173s`, `/reserve/{id}/pay = 0.091s`

## Experiment 1 — Pod Kill Under Load

### Hypothesis

If I delete one gateway pod while traffic is flowing, requests will continue with no visible 5xx spike because the Service will route to the remaining 4 ready gateway pods while Argo Rollouts/Kubernetes creates a replacement.

### Method

```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
kubectl delete "$VICTIM"
kubectl get pods -l app=gateway -w
```

### Observations

Timestamp: `2026-07-01T17:30:02Z`

```text
START 2026-07-01T17:30:02Z killing pod/gateway-785bb78c9f-2ckbw
t+2s ready=4 total=5
t+5s ready=4 total=5
t+7s ready=4 total=5
t+10s ready=4 total=5
t+12s ready=4 total=5
t+14s ready=5 total=5
```

5xx query:

```text
sum(increase(gateway_requests_total{status=~"5.."}[3m]))
result: []  # no 5xx series during the window
```

Per-pod request rate at `2026-07-01T17:30:25Z`:

```text
gateway-785bb78c9f-649k5  5.7999 rps
gateway-785bb78c9f-dgq26  5.6913 rps
gateway-785bb78c9f-sgm5m  5.8907 rps
gateway-785bb78c9f-nsj6z  5.5816 rps
gateway-785bb78c9f-4jdqf  0.9289 rps  # new pod entering the rate window
gateway-785bb78c9f-2ckbw  3.2087 rps  # deleted pod still visible in the 1m Prometheus window
```

### Comparison

The hypothesis matched reality. The replacement became ready in about 14 seconds, and Prometheus did not show any 5xx increase. The only surprise was that the deleted pod remained visible in per-pod Prometheus output until the rate window aged out.

To improve resilience against this failure, I would keep at least 5 gateway replicas and add a PodDisruptionBudget so planned disruptions preserve capacity.

## Experiment 2 — Payment Latency Injection

### Hypothesis

If payments takes 2 seconds per request, `/reserve/{id}/pay` p99 latency will spike, but gateway should not return 5xx because 2000ms is below `GATEWAY_TIMEOUT_MS=5000`.

### Method

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=60s
```

### Observations

Applied at `2026-07-01T17:30:55Z`; measured after the 1m Prometheus window filled.

Error ratio query:

```text
sum(rate(gateway_requests_total{status=~"5.."}[1m])) / sum(rate(gateway_requests_total[1m]))
result: []  # no 5xx series
```

p99 latency by path at `2026-07-01T17:32:27Z`:

```text
/events/{id}/reserve  0.097s
/reserve/{id}/pay     2.485s
/health               0.025s
/events               0.030s
```

Direct checkout probe:

```text
pay_http=200 pay_time=2.013438s
```

Bonus timeout boundary:

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
```

Result:

```text
pay_http=504 pay_time=5.011295s
```

Restored:

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
```

### Comparison

The hypothesis matched. Slow payments did not create 5xx while latency stayed below the gateway timeout, but `/reserve/{id}/pay` p99 rose to about 2.5 seconds. When latency exceeded the timeout, gateway returned 504 after about 5 seconds.

To improve resilience against this failure, I would alert on latency SLO burn for `/reserve/{id}/pay`, not only on 5xx rate.

## Experiment 3 — Redis Failure

### Hypothesis

If Redis goes down, users should still list events because reads use Postgres, but reservation/payment should fail or degrade because reservations need Redis holds; `/health` should report degraded.

### Method

```bash
kubectl scale deployment/redis --replicas=0
kubectl get pods -l app=redis -w
```

### Observations

Timestamp: `2026-07-01T17:37:06Z`

```text
START 2026-07-01T17:37:06Z scaled redis to 0
t+0s redis_pods=1
t+2s redis_pods=0
```

Prometheus status-by-path during Redis down:

```text
/health status=503                 0.3239 rps
/events/{id}/reserve status=504    0.0207 rps
/events status=200                 7.3274 rps
/reserve/{id}/pay status=200       3.4182 rps  # old successful traffic still in 1m window
```

Unexpected live cluster behavior before the bonus fix:

```text
gateway pods: 0/1 Ready, restarts=2 each
gateway Endpoints: empty
gateway logs: GET /health HTTP/1.1 503 Service Unavailable
```

Because gateway readiness and liveness used dependency-heavy `/health`, Redis down caused `events` health to degrade, then gateway health returned 503, all gateway pods became NotReady, and the gateway Service temporarily had no ready endpoints.

Restored after the experiment:

```bash
kubectl scale deployment/redis --replicas=1
kubectl wait --for=condition=Available deployment/redis --timeout=60s
```

### Comparison

The hypothesis was only partly correct. Redis did degrade reservation behavior and health, but the larger surprise was the readiness/liveness cascade: even read-only traffic could be impacted because Services lost endpoints.

To improve resilience against this failure, I would separate dependency health from Kubernetes readiness/liveness probes so dependency outages do not remove otherwise running API pods from service discovery.

## Task 2 — Combined Failure Scenario

### Scenario design

I used the lab's degraded dependencies scenario because it combines a partial downstream failure with constrained database capacity while checkout load is active:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
```

Started at `2026-07-01T17:40:46Z`.

### Observations

Sample 1 — `2026-07-01T17:45:24Z`:

```text
error_ratio = 0.052951406110889346
p99 /reserve/{id}/pay = 0.7475s
p99 /events = 0.0246s
p99 /events/{id}/reserve = 0.0229s
/reserve/{id}/pay status=500 = 1.1090 rps
```

Sample 2 — `2026-07-01T17:46:24Z`:

```text
error_ratio = 0.05161875043878982
p99 /reserve/{id}/pay = 0.7475s
/reserve/{id}/pay status=500 = 1.0727 rps
```

Sample 3 — `2026-07-01T17:47:26Z`:

```text
error_ratio = 0.05968862568316008
p99 /reserve/{id}/pay = 0.7475s
p99 /events/{id}/reserve = 0.0760s
/reserve/{id}/pay status=500 = 1.2546 rps
```

Restored:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0
kubectl set env deployment/events DB_MAX_CONNS=10
kubectl scale deployment/mixedload --replicas=2
```

### Weakest link

The weakest link was payments. The first and strongest golden signal was availability: 5xx error ratio rose to about 5-6%, and the 500s were concentrated on `/reserve/{id}/pay`. Latency amplification was also worst on `/reserve/{id}/pay` at about 0.7475s p99, while `/events` and `/events/{id}/reserve` stayed much lower.

I would make payments more resilient with a circuit breaker and a latency/error SLO alert specifically for the pay path.

## Bonus Task — Resilience Improvement

### Weakness chosen

Redis down caused a readiness/liveness cascade. Before the fix, gateway pods became NotReady/restarted and the gateway Service had no endpoints, even though the process itself was running and some read-only behavior should remain available.

### Fix implemented

Changed Kubernetes probes for `gateway` and `events` from dependency-heavy `/health` to dependency-light `/metrics`:

```diff
- path: /health
+ path: /metrics
```

Files changed:

- `k8s/gateway.yaml`
- `k8s/events.yaml`

The live cluster was patched the same way and rolled out while Redis was down.

### Before vs after

Before fix, Redis down:

```text
gateway pods: 0/1 Ready, restarts=2 each
gateway Endpoints: empty
gateway logs: GET /health HTTP/1.1 503 Service Unavailable
```

After readiness-only fix, gateway stayed in endpoints but events still disappeared from service discovery. That showed the same fix was needed for `events` too.

After full readiness+liveness fix at `2026-07-01T17:54:33Z`, with Redis still down:

```text
gateway pods: 5/5 Ready, restarts=0 on new pods
events pod: 1/1 Ready, restarts=0
gateway Endpoints: 10.42.0.74:8080,10.42.0.75:8080,10.42.0.76:8080 + 2 more
events Endpoints: 10.42.0.73:8081
```

HTTP proof at `2026-07-01T17:54:55Z`:

```text
GET /events through gateway: http=200 time=0.051377s
gateway /health: {"status":"degraded","checks":{"events":"degraded","payments":"ok","circuit_payments":"CLOSED"}}
events /health: {"status":"degraded","checks":{"postgres":"ok","redis":"down"}}
```

The tradeoff is that Kubernetes will keep routing to pods during dependency degradation, so application-level health and alerts must clearly expose the degraded state.

Restored final cluster state before cleanup:

```text
redis        1/1 Available
events       1/1 Available
payments     1/1 Available
mixedload    2/2 Available
gateway      Rollout 5 desired / 5 current / 5 updated / 5 available
```

Cleanup completed:

```bash
kubectl delete -f labs/lab8/mixedload.yaml
```

## PR checklist

```text
- [x] Task 1 done — 3 chaos experiments with hypotheses
- [x] Task 2 done — combined failure scenario
- [x] Bonus Task done — resilience improvement with before/after proof
```

## Acceptance Criteria checklist

```text
- [x] Task 1: 3 experiments, each with hypothesis, method, observations, comparison
- [x] Task 1: hypotheses written before executing
- [x] Task 1: Prometheus / kubectl / HTTP evidence included
- [x] Task 1: improvement sentence included for each experiment
- [x] Task 2: combined scenario with 2+ simultaneous failures
- [x] Task 2: observations with timestamps
- [x] Task 2: weakest link identified
- [x] Bonus: weakness chosen from experiments
- [x] Bonus: config fix implemented
- [x] Bonus: before-vs-after comparison included
```
