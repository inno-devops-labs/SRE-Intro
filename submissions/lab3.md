# Lab 3 — Monitoring, Observability & SLOs


## Task 1 — Monitoring and dashboard

### `prometheus.yml`:

```yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "rules.yml"

scrape_configs:
  - job_name: gateway
    static_configs:
      - targets: ["gateway:8080"]
  - job_name: events
    static_configs:
      - targets: ["events:8081"]
  - job_name: payments
    static_configs:
      - targets: ["payments:8082"]
```

### 1. Docker Compose output

```text
NAME              SERVICE      STATUS
app-gateway-1     gateway      Up
app-events-1      events       Up
app-payments-1    payments     Up
app-postgres-1    postgres     Up (healthy)
app-redis-1       redis        Up (healthy)
app-prometheus-1  prometheus   Up
app-grafana-1     grafana      Up
```

All 7 services were running

### 2. Prometheus targets output

```text
events     up  http://events:8081/metrics
gateway    up  http://gateway:8080/metrics
payments   up  http://payments:8082/metrics
```

### 3. Custom metrics list

```text
events_db_pool_size
events_orders_created
events_orders_total
events_request_duration_seconds_bucket
events_requests_total
events_reservations_active
gateway_request_duration_seconds_bucket
gateway_request_duration_seconds_count
gateway_request_duration_seconds_sum
gateway_requests_total
payments_charges_total
payments_request_duration_seconds_bucket
payments_requests_total
```

### 4. Request rate query output

I generated traffic at 5 requests per second for 20 seconds.

```text
Request rate: 0.16244841494716492 req/s
```

### 5. PromQL used in Grafana

The latency panel is a Time series panel. The unit is seconds.

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

The saturation panel is a Gauge panel `events_db_pool_size`.

Its min is 0, max is 10, yellow threshold is 7, and red threshold is 9. During normal traffic its value was 0.

### 6. Dashboard observations

With normal traffic, request rate was stable, error rate was 0%, and latency was low. The database pool was not full.

For the failure test, I started payments with `PAYMENT_FAILURE_RATE=0.5` and `PAYMENT_LATENCY_MS=1000`. Payment requests became about one second slower. The gateway error-rate query reached `0.5799932035284528%`. The latency and error-rate panels changed, but the database pool stayed normal.

### 7. Which golden signal showed the failure first?

For a full payments stop, **Service Health** is the first clear signal. Prometheus sees `up{job="payments"}` become 0 on the next scrape, so it takes up to about 15 seconds. Gateway errors appear after the next payment request. In the injected failure test, latency changed first because every payment had a one-second delay.

## Task 2 — SLOs and recording rules

### SLI, SLO, and error budget

Availability SLI is `non-5xx gateway requests / all gateway requests`. Its SLO is **99.5% in 7 days**.

At about 1,000 requests per day, there are 7,000 requests per week. The error budget is 0.5%:

```text
7000 × 0.005 = 35 failed requests per week
```

Latency SLI is `gateway requests completed in 500 ms or less / all gateway requests`. Its SLO is **95%**.

### Recording rules output

```text
gateway:sli_availability:ratio_rate5m       = ok
gateway:sli_latency_500ms:ratio_rate5m      = ok
gateway:error_budget_burn_rate:ratio_rate5m = ok
```

### SLO gauge observation

```promql
gateway:sli_availability:ratio_rate5m * 100
```

The Gauge has min 99, max 100, and a threshold at 99.5. During the payments failure, 5xx responses entered the five-minute window and availability dropped below the target.

## Bonus task — metrics and logs

| Time (+03) | What happened |
| --- | --- |
| 15:34:04 | Payments started with 1000 ms delay and 50% failure rate. |
| 15:34:26 | Payments wrote the first injected failure to its log. |
| 15:34:27 | Payments returned HTTP 500. |
| 15:34:54 | Gateway logged the related HTTP 500. |
| 15:35:01 | Payments was restored with normal settings. |

### Log excerpts

```text
payments | 15:34:26.226 Injecting 1000ms latency for 6a5d...
payments | 15:34:27.225 WARNING Payment failed (injected) for 6a5d...
payments | POST /charge HTTP/1.1 500 Internal Server Error
gateway  | 15:34:54.375 POST http://payments:8082/charge "HTTP/1.1 500 Internal Server Error"
gateway  | POST /reserve/e911.../pay HTTP/1.1 500 Internal Server Error
```

### Root cause

The root cause was the test configuration in payments. It added a one-second delay and made about half of payment requests fail. Payments returned 500, then gateway returned 500 to the client. This increased the gateway error metric and used the availability error budget.
