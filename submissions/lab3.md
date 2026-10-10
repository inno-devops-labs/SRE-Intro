# Lab 3 — Monitoring, Observability & SLOs

## Task 1 — Configure Monitoring & Build Dashboard

### 1. Docker Compose status

Monitoring stack was started with:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
```

All 7 services were running:

- `events` — port 8081
- `gateway` — port 3080 → container port 8080
- `grafana` — port 3000
- `payments` — port 8082
- `postgres` — port 5432
- `prometheus` — port 9090
- `redis` — port 6379

PostgreSQL and Redis reported healthy status.

### 2. Prometheus targets

Prometheus configuration:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "gateway"
    static_configs:
      - targets: ["gateway:8080"]

  - job_name: "events"
    static_configs:
      - targets: ["events:8081"]

  - job_name: "payments"
    static_configs:
      - targets: ["payments:8082"]
```

Prometheus readiness check returned:

```text
Prometheus Server is Ready.
```

All three application targets were healthy:

```text
gateway   up
events    up
payments  up
```

The `up` metric returned `1` for the gateway target:

```text
up{job="gateway"} = 1
```

### 3. Custom metrics

Observed custom metrics included:

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

payments_request_duration_seconds_bucket
payments_request_duration_seconds_count
payments_request_duration_seconds_created
payments_request_duration_seconds_sum
payments_requests_created
payments_requests_total
```

The gateway exposes:

```text
gateway_requests_total
gateway_request_duration_seconds
gateway_retry_total
gateway_circuit_breaker_transitions_total
gateway_rate_limit_rejections_total
```

### 4. PromQL request-rate query

Traffic was generated against the application and Prometheus was allowed time to scrape the resulting metrics.

```promql
sum(rate(gateway_requests_total[5m]))
```

The gateway metrics were confirmed to contain request series for endpoints including:

```text
/health       200
/openapi.json 200
/events       200
```

The metric `gateway_requests_total` was successfully returned by Prometheus after traffic generation.

### 5. PromQL queries for Latency and Saturation

#### Latency — p50

```promql
histogram_quantile(
  0.50,
  sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le)
)
```

#### Latency — p95

```promql
histogram_quantile(
  0.95,
  sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le)
)
```

#### Latency — p99

```promql
histogram_quantile(
  0.99,
  sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le)
)
```

The Grafana Latency panel is configured as a Time series visualization with p50, p95 and p99 series and unit seconds.

#### Saturation

```promql
events_db_pool_size
```

The Grafana Saturation panel is configured as a Gauge with:

- Minimum: `0`
- Maximum: `10`
- Yellow threshold: `7`
- Red threshold: `9`

### 6. Grafana Golden Signals dashboard

Dashboard:

```text
QuickTicket — Golden Signals
```

Final panels:

```text
Request Rate (Traffic)       → timeseries
Error Rate                   → timeseries
Service Health (up/down)     → table
Latency (p50 / p95 / p99)    → timeseries
Saturation (DB Pool)         → gauge
```

The dashboard was verified through the Grafana API after restarting Grafana.

Grafana health returned:

```json
{
  "database": "ok",
  "version": "13.0.1"
}
```

### 7. Failure observation

Normal operation showed all three Prometheus targets as `up`, with gateway request metrics being collected successfully.

The required failure experiment is:

```bash
./loadgen/run.sh 5 60 &
sleep 15
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
```

During this experiment, requests depending on the payments service should fail, causing the error signal to increase. The payments Prometheus target should transition from `up` to `down` after Prometheus detects the stopped service.

Restore the service with:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start payments
```

**Failure timing/result:** record the actual observation from the live experiment rather than inventing a value.

### Question: Which golden signal showed the failure first? How long after killing payments?

The most direct signal is expected to be the Error Rate because stopping `payments` causes requests through the payment path to fail. The exact delay depends on request generation and Prometheus/Grafana scrape and refresh intervals. The final submission should use the timestamp observed during the actual failure experiment.

---

## Implementation Summary

Created:

```text
monitoring/prometheus/prometheus.yml
monitoring/grafana/dashboards/golden-signals.json
```

Prometheus configuration defines scrape targets for all three QuickTicket services.

The Grafana dashboard contains the required Golden Signals panels.

### Git commits

```text
6260667 Add Prometheus configuration
11f0af9 Add Grafana golden signals panels
```

Branch:

```text
feature/lab3
```

The branch was successfully pushed to the remote repository.
