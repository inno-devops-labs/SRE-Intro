# Lab 3 — Monitoring, Observability & SLOs

## Task 1 — Configure Monitoring & Build Dashboard

### 3.1: Prometheus Configuration

See `monitoring/prometheus/prometheus.yml` — 3 scrape targets for gateway, events, payments.

### 3.2: All 7 Services Running

```
NAME               IMAGE                     SERVICE      STATUS                    PORTS
app-events-1       app-events                events       Up 42 seconds             0.0.0.0:8081->8081/tcp
app-gateway-1      app-gateway               gateway      Up 42 seconds             0.0.0.0:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    grafana      Up 48 seconds             
app-payments-1     app-payments              payments     Up 48 seconds             0.0.0.0:8082->8082/tcp
app-postgres-1     postgres:17-alpine        postgres     Up 48 seconds (healthy)   0.0.0.0:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   prometheus   Up 48 seconds             0.0.0.0:9090->9090/tcp
app-redis-1        redis:7-alpine            redis        Up 48 seconds (healthy)   0.0.0.0:6379->6379/tcp
```

### 3.3: Prometheus Targets

```
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

### 3.4: Custom Metrics

**Gateway metrics sample:**

```
gateway_requests_total{method="GET",path="/events",status="200"} 67.0
gateway_requests_total{method="POST",path="/events/{id}/reserve",status="200"} 23.0
gateway_requests_total{method="POST",path="/reserve/{id}/pay",status="200"} 7.0
```

**All custom metrics in Prometheus:**

```
events_db_pool_size, events_orders_created, events_orders_total
events_request_duration_seconds_bucket/count/created/sum
events_requests_created, events_requests_total, events_reservations_active
gateway_request_duration_seconds_bucket/count/created/sum
gateway_requests_created, gateway_requests_total
payments_charges_created, payments_charges_total
payments_request_duration_seconds_bucket/count/created/sum
payments_requests_created, payments_requests_total
```

**Request rate query:**

```
sum(rate(gateway_requests_total[5m])) = 0.29 req/s
```

### 3.5: Dashboard Panels — PromQL Queries

**Latency panel (p50 / p95 / p99):**

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

Visualization: Time series, Unit: seconds.

**Saturation panel (DB pool):**

```promql
events_db_pool_size
```

Visualization: Gauge, Min: 0, Max: 10, Thresholds: green default, yellow at 7, red at 9.

### 3.6–3.7: Failure Observation

**Normal traffic:** Request Rate ~3 req/s, Error Rate 0%, Latency p50 ~5ms / p95 ~17ms / p99 ~23ms.

**After killing payments:** Error Rate spiked from 0% to ~5% within ~15 seconds. The `/reserve/{id}/pay` dropped to 0. Latency stayed stable since failing requests return quickly (connection refused). Service Health showed payments as down.

**Which golden signal showed the failure first?** Error Rate detected the failure first, approximately 15 seconds after `docker compose stop payments` (next Prometheus scrape interval). The error rate climbed from 0% to ~5% as pay requests began failing with 503.

---

## Task 2 — Define SLOs & Recording Rules

### 3.8: SLI/SLO Definitions

**SLI 1 — Availability:** % of gateway requests returning non-5xx responses.
- **SLO target:** 99.5% over a 7-day window
- **Error budget:** 0.5% of requests can fail
- With ~1000 requests/day × 7 days = 7000 requests/week → **35 failures allowed** per week

**SLI 2 — Latency:** % of gateway requests completing under 500ms.
- **SLO target:** 95%
- With 7000 requests/week → **350 requests** can exceed 500ms

### 3.9: Recording Rules

See `monitoring/prometheus/rules.yml` — 3 recording rules:

```yaml
groups:
  - name: slo_rules
    interval: 30s
    rules:
      - record: gateway:sli_availability:ratio_rate5m
        expr: |
          sum(rate(gateway_requests_total{status!~"5.."}[5m]))
          /
          sum(rate(gateway_requests_total[5m]))

      - record: gateway:sli_latency_500ms:ratio_rate5m
        expr: |
          sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m]))
          /
          sum(rate(gateway_request_duration_seconds_count[5m]))

      - record: gateway:error_budget_burn_rate:ratio_rate5m
        expr: |
          (1 - gateway:sli_availability:ratio_rate5m)
          /
          (1 - 0.995)
```

**Rules loaded:**

```
gateway:sli_availability:ratio_rate5m              = ok
gateway:sli_latency_500ms:ratio_rate5m             = ok
gateway:error_budget_burn_rate:ratio_rate5m         = ok
```

### 3.10: SLO Gauge Observation

**SLO Availability panel:** `gateway:sli_availability:ratio_rate5m * 100`, Gauge, Min 99, Max 100, threshold red below 99.5.

During payments failure, the SLO gauge dropped from 100% to **95.1%** — well below the 99.5% threshold, indicating rapid error budget burn. After fault injection with `PAYMENT_FAILURE_RATE=0.5`, SLO stabilized around **99.4%** — still in the red zone.

---

## Bonus Task — Correlate Failure Across Metrics & Logs

### Failure Timeline

| Time (UTC) | Event |
|------------|-------|
| `18:56:00` | Payments stopped, fault injection configured (`FAILURE_RATE=0.5`, `LATENCY_MS=1000`) |
| `18:56:30` | Payments restarted with injected faults |
| `18:56:34` | First injected latency logged: `Injecting 1000ms latency for 34b7b29c` |
| `18:56:35` | First injected failure: `Payment failed (injected) for 34b7b29c` → 500 |
| `18:56:35–18:57:02` | ~50% of charges fail with 500, all have +1000ms latency |
| `18:56:44` | First successful payment post-injection: `PAY-6B87F175` → 200 (still with 1000ms latency) |
| ~`18:57:00` | Dashboard shows: Error Rate rising to ~2%, Latency p99 jumping to ~2.5s |
| ~`18:57:30` | SLO Availability gauge drops to 99.4% (below 99.5% threshold) |

### Log Excerpts

**Payments — injected failure:**

```
18:56:34 INFO  Injecting 1000ms latency for 34b7b29c-4fc1-4076-9475-cf496f0494a3
18:56:35 WARN  Payment failed (injected) for 34b7b29c-4fc1-4076-9475-cf496f0494a3
18:56:35 INFO  172.18.0.7 - "POST /charge HTTP/1.1" 500 Internal Server Error
```

**Payments — injected success (still slow):**

```
18:56:43 INFO  Injecting 1000ms latency for b32fdaf6-87c1-48d0-a73b-b3b54193746d
18:56:44 INFO  Payment success: PAY-6B87F175 for b32fdaf6-87c1-48d0-a73b-b3b54193746d
18:56:44 INFO  172.18.0.7 - "POST /charge HTTP/1.1" 200 OK
```

### Root Cause

The fault injection set `PAYMENT_FAILURE_RATE=0.5` and `PAYMENT_LATENCY_MS=1000`, causing the payments service to randomly fail 50% of charges with a 500 error and add 1000ms latency to every request. This propagated through the gateway as:

1. **Error Rate spike** — gateway received 500s from payments and returned them to clients, increasing the 5xx error percentage
2. **Latency spike** — the injected 1000ms delay in payments added directly to end-to-end latency; p99 jumped from ~25ms to ~2.5s because gateway holds the connection open while waiting for the slow payments response
3. **SLO breach** — the combination of errors consumed the error budget faster than 1x burn rate, dropping availability below the 99.5% target

The **Latency panel** was the most dramatic indicator: jumping 100x from milliseconds to seconds. The **Error Rate** was a more reliable failure signal since it directly measured the SLO violation.
