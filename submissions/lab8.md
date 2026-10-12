# Lab 8 — Chaos Engineering

## Experiment 1 — kill one gateway pod

**Hypothesis written before the test:** If I delete one gateway pod while
traffic is flowing, Kubernetes will create a replacement and the other four
pods will continue to serve requests because the Service balances traffic.

**Commands:**

```bash
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
kubectl delete "$VICTIM"
kubectl get pods -l app=gateway -w
```

**Observation.** Start: `2026-10-05T02:27:20+03:00`. Check:
`2026-10-05T02:27:30+03:00`. Replacement pod
`gateway-68f84bb847-zqcb5` was Running and 1/1 Ready after about ten seconds.
The 3-minute 5xx query returned `0`. Remaining pod rates were 1.45–1.85 RPS,
so traffic continued on the available pods.

**Comparison.** The hypothesis was correct: no request failure was recorded
and capacity returned when the replacement became ready.

To improve resilience against this failure, I would run at least five ready
gateway replicas and alert on a sudden drop in ready endpoints.

## Experiment 2 — payment latency

**Hypothesis written before the test:** If payments takes 2 seconds per
request, only payment requests will become slow, but gateway will not return
5xx because 2 seconds is below its 5-second timeout.

**Commands:**

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=60s
```

**Observation at `2026-10-04T23:32:51+03:00`:** Prometheus returned an error
ratio of `0`. p99 was `/events` `0.023s`, reserve `0.060s`, and `/health`
`0.040s`. The first payment series was `NaN` because the histogram window did
not yet contain enough completed payment requests.

**Comparison.** The zero 5xx result matched the hypothesis. The `NaN` result
was a useful warning: metrics need enough time and traffic before p99 is safe
to use.

To improve resilience against this failure, I would alert on payment p99
latency as well as on errors.

## Experiment 3 — Redis failure

**Hypothesis written before the test:** If Redis goes down, users can still
list events, but reserve requests will fail because Redis stores the temporary
ticket hold.

**Commands:**

```bash
kubectl scale deployment/redis --replicas=0
kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'echo "GET /events:"; curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://gateway:8080/events; \
  echo "POST /reserve:"; curl -s -X POST -o /dev/null -w "%{http_code} %{time_total}s\n" \
  -H "Content-Type: application/json" -d "{\"quantity\":1}" http://gateway:8080/events/1/reserve; \
  echo "GET /health:"; curl -s http://gateway:8080/health'
```

**Observation at `2026-10-04T23:33:01+03:00`:**

```text
GET /events:    200 0.007613s
POST /reserve:  504 5.006372s
GET /health:    {"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

Redis was restored with `kubectl scale deployment/redis --replicas=1` and
`kubectl wait --for=condition=Available deployment/redis --timeout=60s`.

**Comparison.** This exactly matched the hypothesis for events and reserve.
The surprise was that `/health` still said healthy, because it checks events
and payments but does not report Redis readiness.

To improve resilience against this failure, I would add Redis to the health
and readiness report and fail reserve quickly instead of waiting five seconds.

## Combined scenario

The combined scenario used payment failures, a small DB pool, and more load:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=0
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
```

At `2026-10-04T23:33:59+03:00`, error ratio was `0.02636`. p99 was `/events`
`0.020s`, reserve `6.262s`, and pay `2.445s`. Reserve reacted first and was
the worst path because it needs the events database/Redis chain before payment.
The weakest link was the small events DB connection pool: requests waited in
the pool queue and latency became much larger than the read path.

## Bonus — resilience improvement

`k8s/events.yaml`:

```diff
- DB_MAX_CONNS=10
- requests: { cpu: 50m, memory: 64Mi }
+ DB_MAX_CONNS=20
+ requests: { cpu: 100m, memory: 128Mi }
```

After applying the manifest and repeating the mixed-load test, error ratio was
`0.000649`; reserve p99 was
`0.0505s` instead of `6.262s` before the fix. The trade-off is more possible
database connections and more reserved CPU/memory, so the database must have
enough capacity for the larger pool.
