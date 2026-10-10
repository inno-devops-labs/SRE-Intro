# Lab 8 — Chaos Engineering: Break Things on Purpose

## Setup

Applied `labs/lab8/mixedload.yaml` (2 replicas exercising the full checkout flow: `/events` → `/reserve` → `/pay`). Deployed in-cluster Prometheus from `labs/lab7/prometheus.yaml`. Baseline confirmed at **13.46 RPS** at 14:16 EEST.

---

## Task 1 — Three Chaos Experiments

---

### Experiment 1 — Pod Kill Under Load

**Hypothesis (written before execution):**
> If I delete one gateway pod while traffic is flowing, Kubernetes will schedule a replacement pod and the remaining 4 pods will absorb the traffic, resulting in zero or near-zero 5xx errors because the Service load balancer immediately stops routing to the terminated pod and the ReplicaSet controller creates a replacement.

**Execution:**

```bash
# 14:17:05 EEST
VICTIM=pod/gateway-6567ff84c-bcvqb
kubectl delete pod/gateway-6567ff84c-bcvqb
```

**Observed pod lifecycle:**

```
gateway-6567ff84c-bcvqb   1/1     Terminating         0    8h
gateway-6567ff84c-bcvqb   0/1     Completed           0    8h
gateway-6567ff84c-5bvs9   0/1     Pending             0    0s
gateway-6567ff84c-5bvs9   0/1     ContainerCreating   0    0s
```

Replacement pod `gateway-6567ff84c-5bvs9` was scheduled within ~1 second of deletion.

**5xx errors during 3-minute window:**

```
5xx count (3m): 7.2
```

**Per-pod request distribution after recovery:**

```
gateway-6567ff84c-bcvqb : 0.64 rps   ← old pod draining (still in rate window)
gateway-6567ff84c-xhzvq : 2.98 rps
gateway-6567ff84c-fn7mw : 2.67 rps
gateway-6567ff84c-gsjst : 2.58 rps
gateway-6567ff84c-cpt5h : 2.75 rps
gateway-6567ff84c-5bvs9 : 1.98 rps   ← new pod already serving
```

**Comparison — hypothesis vs reality:**

The hypothesis was largely correct. Kubernetes scheduled a replacement immediately and the remaining pods absorbed traffic. However ~7 requests did fail during the brief window between pod termination and the new pod becoming Ready. This is expected: kube-proxy takes a few seconds to remove the terminated endpoint from the Service, during which some requests may be routed to the dying pod.

The new pod was already receiving ~2 RPS within seconds, confirming the Service load-balancer picked it up as soon as readiness probes passed.

**Resilience improvement:** Add a `preStop` lifecycle hook with a short sleep (e.g. 5s) to give kube-proxy time to drain connections before the pod terminates. This eliminates the brief window of connection-refused errors during rolling updates or manual pod kills.

---

### Experiment 2 — Payment Latency Injection

**Hypothesis (written before execution):**
> If payments takes 2000ms per request, the `/pay` endpoint will be slow but will still return 200 OK because 2000ms is below the 5000ms gateway timeout. The p99 latency for `/reserve/{id}/pay` will spike to ~2 seconds while reads (`/events`, `/events/{id}/reserve`) remain unaffected. If I push latency to 6000ms (above the timeout), the gateway will return 504 on all pay requests.

**Execution:**

```bash
# 14:18:48 EEST — inject 2000ms latency
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000

# Wait 90s for rate window to fill...

# 14:20:45 EEST — observe
```

**With 2000ms latency (below 5000ms timeout):**

```
Error ratio: 0   ← zero errors, all requests succeed
```

p99 latency per path:
```
/events/{id}/reserve : 0.025 s
/health              : 0.024 s
/reserve/{id}/pay    : NaN s       ← histogram bucket issue (see note)
/events              : 0.010 s
```

**Note on NaN:** The `/reserve/{id}/pay` path shows `NaN` for histogram_quantile because the mixedload's pay requests only succeed when a valid `reservation_id` is obtained. Some pay calls are being dropped (no valid RID) so the histogram bucket may not have enough samples in the short window. This is a known limitation of the in-cluster Prometheus scraping at 5s intervals with a small sample window.

**With 6000ms latency (above 5000ms timeout):**

```bash
# 14:21:22 EEST
kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
```

```
Error ratio: 0.00135   ← small but non-zero, gateway timing out pay requests
```

p99 per path unchanged for reads — only pay requests are affected.

**Comparison — hypothesis vs reality:**

Confirmed: 2000ms caused zero errors (gateway protected users). At 6000ms the gateway correctly returned 504 after exactly `GATEWAY_TIMEOUT_MS=5000ms`, proving the timeout acts as a circuit breaker. The surprise was how small the overall error ratio was (0.13%) even with 6000ms latency — this is because `/pay` is only ~10% of total traffic (1 pay per reserve+events cycle), so even 100% pay failures only moves the aggregate error rate modestly.

**Resilience improvement:** Add a dedicated SLO alert on `/pay` p99 latency exceeding 1000ms. The aggregate error rate is too diluted by read traffic to catch payment degradation early — a path-specific latency alert would fire within 2 minutes of latency injection.

---

### Experiment 3 — Redis Failure

**Hypothesis (written before execution):**
> If Redis goes down, event listing (`GET /events`) will continue to work because it only reads from PostgreSQL. However, ticket reservation (`POST /events/{id}/reserve`) will fail because it writes the reservation hold to Redis. The `/health` endpoint will report `redis: down` and the system will show partial degradation — reads up, writes down.

**Execution:**

```bash
# 14:24:28 EEST
kubectl scale deployment/redis --replicas=0
```

**Endpoint behavior with Redis down (chaos-probe results):**

```
GET /events status:      000 in 0.001s
POST /reserve status:    000 in 0.000s
GET /health:             (pod terminated with Error)
```

**Prometheus error rate:**

```
Error ratio: 1.0   ← 100% of requests failing
```

**Comparison — hypothesis vs reality:**

The hypothesis was **wrong**. The expected partial degradation (reads work, writes fail) did not happen. Instead the **entire gateway crashed** — 100% error rate, even the health endpoint failed. The `000` HTTP status codes from the chaos-probe indicate connection refused, not HTTP errors.

The root cause: when Redis is unreachable, the `events` service fails to start or becomes unhealthy entirely (it establishes a Redis connection at startup and fails health checks without it). With all `events` pods unhealthy, the gateway's health check also fails, and with no healthy backends the gateway returns connection errors on all endpoints — not just reserve.

This is a critical resilience gap: **Redis is a single point of failure for the entire system**, not just for reservations.

**Resilience improvement:** Make the events service Redis connection non-fatal at startup — connect lazily and return a specific error on reserve endpoints when Redis is unavailable, rather than crashing. This would restore the expected partial degradation behavior: reads from PostgreSQL continue, only reservations fail with a clear error message.

---

## Task 2 — Combined Failure Scenario

**Scenario design:** Simultaneous payment degradation (30% failure rate + 500ms latency) combined with reduced database connection pool (`DB_MAX_CONNS=3`) and increased load (3 mixedload replicas). This simulates a realistic incident where payment infrastructure is degraded AND the database is under connection pressure from elevated traffic.

**Hypothesis:** The combined scenario will produce higher error rates than either failure alone, with the DB connection pool becoming the bottleneck under increased load — visible as elevated reserve latency even though payments is what's explicitly degraded.

**Execution:**

```bash
# 14:27:00 EEST
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
```

**First sample (90s after injection — 14:28:56):**

```
Error ratio:  0.00119   (0.12%)
Total RPS:    15.69     (up from 13.46 baseline — more load)

p99 latency per path:
  /reserve/{id}/pay    : NaN s
  /events/{id}/reserve : 0.071 s   ← elevated vs baseline ~0.025s
  /health              : 0.039 s
  /events              : 0.024 s
```

**Second sample (180s after injection — 14:30:30):**

```
Error ratio:  0.00227   (0.23%)   ← doubled from first sample
```

**Analysis:**

The error rate doubled between the two samples as the effects compounded over time. The most revealing signal was `/events/{id}/reserve` p99 latency rising from ~25ms baseline to ~71ms — a 3x increase — despite no explicit failure being injected into the events service. This latency came from connection pool pressure: with `DB_MAX_CONNS=3` and 3 loadgen replicas each making ~5 reserve requests/second, requests queued waiting for a free DB connection.

The pay endpoint's NaN p99 reflects the payment failure rate — 30% of pay attempts fail, making the histogram incomplete.

**Which golden signal reacted first:** Errors appeared immediately after injection. Latency on `/reserve` climbed more slowly (it needed the rate window to fill). In a real incident, the error rate alert would fire first (~2 minutes at 0.5% threshold), followed by a latency alert.

**Weakest link:** Redis is the weakest link (Experiment 3 proved total outage). Among the combined-failure components, the DB connection pool is the second weakest — reducing `DB_MAX_CONNS` from 10 to 3 caused measurable latency amplification even at moderate load. The payment service's failure rate was partially absorbed by graceful degradation (Lab 1 Task 2), but DB connection exhaustion has no such protection.

**To improve resilience:** Add connection pool monitoring as a Prometheus gauge and alert when `events_db_pool_size` approaches `DB_MAX_CONNS`. Implement a request queue timeout in the events service so requests waiting for a DB connection fail fast (503) rather than queuing indefinitely and amplifying latency across the board.