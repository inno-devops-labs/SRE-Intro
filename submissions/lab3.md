# Lab 3 — Monitoring, Observability & SLOs

## Task 1 — Configure Monitoring and Build the Dashboard

### 1. Running services

The application and monitoring stack were started with:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
```

`docker compose ps` showed all seven services running:

```text
NAME                 SERVICE      STATUS
app-events-1         events       Up
app-gateway-1        gateway      Up
app-grafana-1        grafana      Up
app-payments-1       payments     Up
app-postgres-1       postgres     Up (healthy)
app-prometheus-1     prometheus   Up
app-redis-1          redis        Up (healthy)
```

The gateway health endpoint returned:

```json
{
  "status": "healthy",
  "checks": {
    "events": "ok",
    "payments": "ok",
    "circuit_payments": "CLOSED"
  }
}
```

### 2. Prometheus targets

Prometheus successfully scraped all three QuickTicket services:

```text
events    up    http://events:8081/metrics
gateway   up    http://gateway:8080/metrics
payments  up    http://payments:8082/metrics
```

### 3. Custom metrics

```text
events_db_pool_size
events_orders_created
events_orders_total
events_request_duration_seconds_bucket
events_request_duration_seconds_count
events_request_duration_seconds_created
events_request_duration_seconds_sum
events_requests_created
events_requests_total
events_reservations_active
gateway_request_duration_seconds_bucket
gateway_request_duration_seconds_count
gateway_request_duration_seconds_created
gateway_request_duration_seconds_sum
gateway_requests_created
gateway_requests_total
payments_charges_created
payments_charges_total
payments_request_duration_seconds_bucket
payments_request_duration_seconds_count
payments_request_duration_seconds_created
payments_request_duration_seconds_sum
payments_requests_created
payments_requests_total
```

### 4. Request-rate query

PromQL:

```promql
sum(rate(gateway_requests_total[5m]))
```

Result:

```text
1.2385877993838639 requests/second
```

### 5. Dashboard queries

Latency p50:

```promql
histogram_quantile(0.50, sum by (le) (rate(gateway_request_duration_seconds_bucket[1m])))
```

Latency p95:

```promql
histogram_quantile(0.95, sum by (le) (rate(gateway_request_duration_seconds_bucket[1m])))
```

Latency p99:

```promql
histogram_quantile(0.99, sum by (le) (rate(gateway_request_duration_seconds_bucket[1m])))
```

Saturation:

```promql
events_db_pool_size
```

The latency panel uses seconds as its unit. The saturation gauge has a range of
0–10, with a yellow threshold at 7 and a red threshold at 9.

### 6. Normal traffic and payments failure

During normal traffic, the dashboard showed approximately:

- p50 latency: 7–8 ms;
- p95 latency: 14–17 ms;
- p99 latency: 23–24 ms;
- no 5xx errors;
- zero database connections in use at the exact scrape instants.

Payments was stopped at `2026-09-21T03:16:49.950753045+03:00`. During the
failure, the 5xx error rate increased to approximately 2%, while p99 latency
spiked to 332–370 ms. Read-only `/events` traffic continued to work, so the
failure affected only part of the workload.

The error signal was the first unambiguous golden signal showing the failure.
The first visible error-rate increase and p99 latency spike were both displayed
at `03:17:45`, approximately 55 seconds after payments was stopped. This delay
includes the request mix, the Prometheus 15-second scrape interval, and Grafana
sampling. The Prometheus `up` metric for payments also changed to zero, but
service health is not one of the four golden signals.

Payments was started again at `2026-09-21T03:21:02.927286155+03:00`. The gateway
health check returned `healthy`, payments returned `ok`, and the payment circuit
breaker returned to `CLOSED`.

The load generator later reported some `409 Conflict` responses because held
ticket counters accumulated in Redis. These client errors are counted as
failures by the load generator but are intentionally not included in the 5xx
availability SLI.

## Task 2 — Define SLOs and Recording Rules

### 7. SLI and SLO definitions

**Availability SLI:** the proportion of gateway requests that return a non-5xx
status code.

```promql
sum(rate(gateway_requests_total{status!~"5.."}[5m]))
/
sum(rate(gateway_requests_total[5m]))
```

**Availability SLO:** at least 99.5% over a seven-day window.

**Latency SLI:** the proportion of gateway requests completed in 500 ms or less.

```promql
sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m]))
/
sum(rate(gateway_request_duration_seconds_count[5m]))
```

**Latency SLO:** at least 95% of requests complete within 500 ms.

### 8. Error-budget calculation

With approximately 1,000 requests per day:

```text
Requests per week = 1,000 × 7 = 7,000
Allowed failure ratio = 1 − 0.995 = 0.005
Allowed failures = 7,000 × 0.005 = 35
```

The availability error budget therefore allows **35 responses with 5xx status
per week**.

### 9. Recording-rule verification

`promtool` verified the Prometheus configuration and rules:

```text
Checking /etc/prometheus/prometheus.yml
  SUCCESS: 1 rule files found
  SUCCESS: /etc/prometheus/prometheus.yml is valid prometheus config file syntax

Checking /etc/prometheus/rules.yml
  SUCCESS: 3 rules found
```

Prometheus reported all three recording rules as healthy:

```text
gateway:sli_availability:ratio_rate5m          ok
gateway:sli_latency_500ms:ratio_rate5m         ok
gateway:error_budget_burn_rate:ratio_rate5m    ok
```

### 10. SLO gauge failure experiment

The Grafana gauge uses:

```promql
gateway:sli_availability:ratio_rate5m * 100
```

Its range is 99–100%, with the SLO threshold at 99.5%.

During the experiment, requests were sent to the gateway health endpoint at
approximately one request per second. Payments was stopped for one minute. The
health endpoint returned 503 while payments was unavailable.

Observed results:

```text
Availability during failure: 47.61904761904761%
Error-budget burn rate:       104.76190476190467
HTTP 200 responses:           135
HTTP 503 responses:           45
Payments started again:       2026-09-21T04:03:46.708928276+03:00
```

The availability gauge fell below 99.5% and displayed the failure state. A burn
rate greater than 1 means that the service is consuming its error budget too
quickly; the observed value of approximately 104.76 means the budget was being
consumed about 105 times faster than the allowed rate.

After payments restarted, the next request changed from 503 to 200 within about
one second. The final gateway health response was:

```json
{
  "status": "healthy",
  "checks": {
    "events": "ok",
    "payments": "ok",
    "circuit_payments": "CLOSED"
  }
}
```

The five-minute SLI did not return to 100% immediately because failed requests
remain in the rolling five-minute calculation until they age out of the window.

## Bonus Task — Correlate Failure Across Metrics and Logs

### Fault injection

Normal traffic was started with the load generator. After 30 seconds, payments
was recreated with the following environment variables:

```text
PAYMENT_FAILURE_RATE=0.5
PAYMENT_LATENCY_MS=1000
```

The main incident started at `2026-09-21T04:11:16+03:00` and payments was
restored at `2026-09-21T04:12:48+03:00`.

The load generator produced:

```text
Done. total=507 success=366 fail=141 error_rate=27.8%
```

This overall percentage also contains pre-existing `409 Conflict` reservation
responses, so it is not a direct measurement of the injected 50% payments
failure probability.

### Metrics observed in Grafana

During the incident:

- p99 gateway latency increased to approximately 1.5 seconds;
- the gateway 5xx error rate increased;
- the availability SLI fell to 99.3%, below the 99.5% objective;
- payments remained scrapeable (`up = 1`) because the process was running even
  while `/charge` returned injected errors;
- database connection usage stayed at zero, confirming that PostgreSQL was not
  the source of the failure.

The first gateway 500 in the main load experiment was logged at
`2026-09-21T04:11:28.402+03:00`, approximately 12.4 seconds after fault
injection. Grafana showed the error and latency increase on the following
scrape/display samples.

### Exact cross-service correlation

A controlled reproduction was performed to capture both services' logs before
the faulty payments container was replaced. The faulty configuration was
enabled at `2026-09-21T04:18:50+03:00`. Four of ten payment attempts returned
500 and six returned 200.

For reservation `8f9699c2-9624-486a-974a-f3da58b3447c`, the correlated timeline
was:

| Time (UTC+3) | Component | Event |
|---|---|---|
| 04:18:50.000 | Compose | Faulty payments configuration enabled |
| 04:18:53.625 | payments | Began injecting 1,000 ms latency |
| 04:18:54.625 | payments | Logged `Payment failed (injected)` |
| 04:18:54.627 | payments | `POST /charge` returned 500 |
| 04:18:54.629 | gateway | `POST /reserve/.../pay` returned 500 to the client |
| 04:19:15.000 | Compose | Normal payments configuration restored |

Relevant payments log excerpt:

```text
2026-09-21T01:18:53.624665908Z Injecting 1000ms latency for 8f9699c2-9624-486a-974a-f3da58b3447c
2026-09-21T01:18:54.626026444Z Payment failed (injected) for 8f9699c2-9624-486a-974a-f3da58b3447c
2026-09-21T01:18:54.626703165Z POST /charge HTTP/1.1 500 Internal Server Error
```

Relevant gateway log excerpt:

```text
2026-09-21T01:18:54.629367973Z POST /reserve/8f9699c2-9624-486a-974a-f3da58b3447c/pay HTTP/1.1 500 Internal Server Error
```

Docker timestamps above are in UTC; adding three hours gives the local UTC+3
times used in the timeline.

### Root cause and recovery

The root cause was deliberate payments fault injection. Every charge waited an
additional second, explaining the latency increase. Half of charge attempts
were configured to return HTTP 500, and gateway propagated those failures to
the client, explaining the 5xx and SLI changes. The database saturation signal
did not change because the fault was isolated to payments.

After payments was recreated with `PAYMENT_FAILURE_RATE=0.0` and
`PAYMENT_LATENCY_MS=0`, its health endpoint reported:

```json
{
  "status": "healthy",
  "failure_rate": 0.0,
  "latency_ms": 0
}
```
