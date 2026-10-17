# Lab 3 — Monitoring, Observability & SLOs

## 3.7 Proof of Work

### 1. Docker Compose services

All seven required services were running:

```text
NAME               SERVICE      STATUS
app-events-1       events       Up
app-gateway-1      gateway      Up
app-grafana-1      grafana      Up
app-payments-1     payments     Up
app-postgres-1     postgres     Up (healthy)
app-prometheus-1   prometheus   Up
app-redis-1        redis        Up (healthy)
```

### 2. Prometheus targets

All three application targets were successfully scraped by Prometheus:

```text
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

### 3. Custom metrics

The following application-specific metrics were available in Prometheus:

```text
events_db_pool_size
events_orders_created
events_orders_total
events_reservations_active
gateway_request_duration_seconds_bucket
gateway_request_duration_seconds_count
gateway_request_duration_seconds_created
gateway_request_duration_seconds_sum
gateway_requests_created
gateway_requests_total
```

### 4. Request-rate PromQL query

Query:

```promql
sum(rate(gateway_requests_total[1m]))
```

Observed result:

```text
1.7339972222222224 req/s
```

### 5. Grafana PromQL queries

Latency panel:

```promql
histogram_quantile(
  0.50,
  sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le)
)
```

```promql
histogram_quantile(
  0.95,
  sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le)
)
```

```promql
histogram_quantile(
  0.99,
  sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le)
)
```

Saturation panel:

```promql
events_db_pool_size
```

### 6. Dashboard observations

#### Normal traffic

During normal traffic:

- request traffic was visible for `/events`, `/events/{id}/reserve`, and `/reserve/{id}/pay`;
- 5xx error rate stayed at or near 0%;
- all Prometheus service targets were up;
- request latency stayed low;
- DB pool saturation remained well below its maximum;
- the Availability SLI was 100% before the failure test.

#### Payments failure

The Payments service was stopped while traffic continued.

Observed effects:

- Prometheus marked the Payments target as down;
- payment requests began failing;
- the gateway 5xx error rate increased;
- latency increased;
- requests to unaffected endpoints such as `/events` continued to succeed;
- the Availability SLO gauge fell below the 99.5% target.

The Availability SLI changed as follows:

```text
Before failure: 100%
During failure: 95.82438422560881%
Later:          95.40275167639518%
Minimum seen:   93.97849637825162%
```

### 7. Which Golden Signal showed the failure first?

The **Service Health / availability of the Payments scrape target** showed the failure first.

Prometheus scrapes targets every 15 seconds. In the captured test, `up{job="payments"}` was already `0` within 20 seconds after the Payments service was stopped. Therefore, the failure was detected within approximately one scrape interval (the captured upper bound was 20 seconds).

The Error Rate and SLO gauge changed afterwards as failed payment requests accumulated and the corresponding rate windows were reevaluated.

---

## 3.8 SLIs and SLOs

### Availability SLI

The Availability SLI is the percentage of gateway requests that return a non-5xx response:

```text
Availability = non-5xx requests / all requests
```

Availability SLO:

```text
99.5% over a 7-day window
```

With approximately 1000 requests per day:

```text
1000 × 7 = 7000 requests/week
```

The allowed error budget is:

```text
100% - 99.5% = 0.5%
```

Therefore:

```text
7000 × 0.005 = 35
```

The availability error budget allows **35 failed 5xx requests per week** before the 99.5% SLO is violated.

### Latency SLI

The Latency SLI is the percentage of gateway requests completed within 500 ms.

Latency SLO:

```text
95% of requests < 500 ms
```

This means up to 5% of requests may take 500 ms or longer.

At 7000 requests per week:

```text
7000 × 0.05 = 350
```

Therefore, up to **350 slow requests per week** can occur while still satisfying the latency SLO.

---

## 3.9 Recording Rules

The following recording rules were configured:

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
          (
            1 - gateway:sli_availability:ratio_rate5m
          )
          /
          (1 - 0.995)
```

Prometheus successfully loaded all three rules:

```text
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

The burn-rate rule compares the current error rate with the allowed 0.5% error budget. A burn rate greater than 1 means that the error budget is being consumed faster than the SLO permits.

---

## 3.10 SLO Panel

A Grafana Gauge panel was created with:

```promql
gateway:sli_availability:ratio_rate5m * 100
```

Configuration:

```text
Min:       99
Max:       100
Threshold: 99.5
```

Before the failure, the gauge showed:

```text
100%
```

While Payments was unavailable and load continued, the gauge dropped below the 99.5% SLO threshold:

```text
95.82%
95.40%
93.98%
```

The dashboard showed the availability SLO violation together with increased 5xx errors and higher latency.

The gauge did not change immediately because the SLI uses a rolling 5-minute rate and the recording rule is evaluated every 30 seconds. After the Payments service recovered, the SLI recovered gradually because earlier failures remained inside the rolling window for some time.

---

# Bonus Task — Correlate Failure Across Metrics & Logs

## Experiment

Normal load was started at:

```text
2026-09-20 02:31:31 +03:00
```

After approximately 30 seconds, Payments was recreated with:

```text
PAYMENT_FAILURE_RATE=0.5
PAYMENT_LATENCY_MS=1000
```

Failure injection started at:

```text
2026-09-20 02:32:01 +03:00
```

The service intentionally added 1000 ms latency to payment calls and randomly failed approximately 50% of charge attempts.

## Timeline

| Time (+03:00) | Event |
|---|---|
| 02:31:31 | Traffic started at 5 RPS |
| 02:32:01 | Fault injection started |
| 02:32:03.780 | Gateway returned a transient 503 while the Payments container was being recreated |
| 02:32:04.806 | Payments began applying the configured 1000 ms latency |
| 02:32:05.806 | First injected payment failure was logged by Payments |
| 02:32:05.811 | Gateway observed the corresponding `500 Internal Server Error` from Payments |
| shortly afterwards | Grafana Error Rate increased and p95/p99 latency rose significantly |
| during degradation | Availability SLO dropped below the 99.5% objective |
| 02:33:50 | Recovery started by restoring failure rate and latency to zero |
| 02:33:51 | Payments container was recreated with normal settings |

The first persistent application-level injected failure appeared approximately **4.8 seconds after fault injection began**. A brief `503` occurred earlier because the Payments container was being recreated.

## Payments log excerpts

```text
2026-09-19T23:32:04.806492737Z
Injecting 1000ms latency for a4ac137d-75bd-4ea5-a995-e091dcf2689a

2026-09-19T23:32:05.807297699Z
Payment failed (injected) for a4ac137d-75bd-4ea5-a995-e091dcf2689a

2026-09-19T23:32:06.333439179Z
Injecting 1000ms latency for c3711c44-c6f0-4a87-ad89-748d1b4ad7d1

2026-09-19T23:32:07.335813346Z
Payment success: PAY-99599933 for c3711c44-c6f0-4a87-ad89-748d1b4ad7d1

2026-09-19T23:32:09.831425266Z
Injecting 1000ms latency for d7dd2f0d-6645-464b-908e-0bb98ed7b21a

2026-09-19T23:32:10.831833353Z
Payment failed (injected) for d7dd2f0d-6645-464b-908e-0bb98ed7b21a
```

## Gateway log excerpts

The same reservation ID can be followed from Payments to the Gateway:

```text
2026-09-19T23:32:05.811386296Z
HTTP Request: POST http://payments:8082/charge "HTTP/1.1 500 Internal Server Error"

2026-09-19T23:32:05.815502830Z
POST /reserve/a4ac137d-75bd-4ea5-a995-e091dcf2689a/pay HTTP/1.1
500 Internal Server Error
```

Another example:

```text
2026-09-19T23:32:10.833900525Z
HTTP Request: POST http://payments:8082/charge "HTTP/1.1 500 Internal Server Error"

2026-09-19T23:32:10.835746493Z
POST /reserve/d7dd2f0d-6645-464b-908e-0bb98ed7b21a/pay HTTP/1.1
500 Internal Server Error
```

Unrelated application traffic continued to succeed at the same time:

```text
GET /events HTTP/1.1 200 OK
POST /events/2/reserve HTTP/1.1 200 OK
```

## Metric correlation

The Grafana dashboard showed multiple symptoms of the same Payments degradation:

- **Traffic:** requests continued to reach the gateway, so this was not a complete system outage.
- **Errors:** the 5xx error rate increased after injected payment failures started.
- **Latency:** p95 and p99 increased strongly because every Payments charge was delayed by 1000 ms.
- **Service Health:** the service was generally reachable after recreation, so the main issue was degraded behavior rather than a permanent outage.
- **Availability SLO:** successful availability fell below the 99.5% target as gateway payment requests returned 5xx responses.

## Root cause

The root cause was the deliberate fault injection in the Payments service:

```text
PAYMENT_FAILURE_RATE=0.5
PAYMENT_LATENCY_MS=1000
```

The injected 1000 ms latency increased end-to-end request latency for payment flows. The 50% payment failure probability caused `/charge` to return HTTP 500 for a subset of requests. The Gateway propagated these failures to `/reserve/{id}/pay`, producing gateway 5xx responses.

The logs and metrics therefore describe the same failure chain:

```text
Payments fault injection
        ↓
1000 ms payment latency
        ↓
Injected Payments HTTP 500
        ↓
Gateway payment request HTTP 500
        ↓
Grafana Error Rate increases
        ↓
p95/p99 latency increases
        ↓
Availability SLI falls below the SLO
```

The initial `503 Service Unavailable` immediately after injection was a short-lived side effect of recreating the Payments container. The continuing 500 responses after the container started were caused by the configured failure injection itself.

After restoring:

```text
PAYMENT_FAILURE_RATE=0
PAYMENT_LATENCY_MS=0
```

the Payments service returned to normal operation and the affected metrics began recovering.
