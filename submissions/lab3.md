# Lab 3 Submission — Monitoring, Observability & SLOs

## Task 1 — Configure Monitoring & Build Dashboard

### 3.1 — Prometheus Configuration

Prometheus was configured to scrape metrics from all three QuickTicket services using their Docker Compose service names and internal ports.

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "rules.yml"

scrape_configs:
  - job_name: gateway
    static_configs:
      - targets:
          - gateway:8080

  - job_name: events
    static_configs:
      - targets:
          - events:8081

  - job_name: payments
    static_configs:
      - targets:
          - payments:8082
```

Docker Compose service names are resolvable through Docker's embedded DNS. Prometheus therefore reaches the services as `gateway:8080`, `events:8081`, and `payments:8082`. The published gateway port `3080` is intended for access from the host and is not needed for container-to-container communication.

### 3.2 — Monitoring Stack Deployment

The application and monitoring Compose files were started together. All seven services reached running state:

```text
NAME               IMAGE                     COMMAND                  SERVICE      CREATED          STATUS                 PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       28 seconds ago   Up 25 seconds          0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      27 seconds ago   Up 25 seconds          0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      28 seconds ago   Up 25 seconds          0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     28 seconds ago   Up 25 seconds          0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     4 hours ago      Up 4 hours (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   28 seconds ago   Up 25 seconds          0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        4 hours ago      Up 4 hours (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

The deployed stack consists of:

- Gateway
- Events
- Payments
- PostgreSQL
- Redis
- Prometheus
- Grafana

The gateway health endpoint also returned a healthy response:

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

### 3.3 — Prometheus Targets

All three QuickTicket targets were successfully scraped:

```text
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

### 3.4 — Custom Metrics

The following application-specific metrics were discovered through the Prometheus API:

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
payments_request_duration_seconds_bucket
payments_request_duration_seconds_count
payments_request_duration_seconds_created
payments_request_duration_seconds_sum
payments_requests_created
payments_requests_total
```

A baseline load test was run at 5 RPS for 20 seconds:

```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 20s
---
[10s] requests=39 success=39 fail=0 error_rate=0%
[10s] requests=40 success=40 fail=0 error_rate=0%
[10s] requests=41 success=41 fail=0 error_rate=0%
[10s] requests=42 success=42 fail=0 error_rate=0%
---
Done. total=78 success=78 fail=0 error_rate=0%
```

All 78 requests completed successfully with a 0% error rate.

The five-minute gateway request-rate query was:

```promql
sum(rate(gateway_requests_total[5m]))
```

Result:

```text
Request rate: 0.24 req/s
```

The measured value is lower than the configured load-generator rate because the query was executed after the short load test had finished and was averaged over a five-minute window.

### 3.5 — Golden Signals Dashboard

The provided Grafana dashboard was completed by replacing both placeholder panels in `monitoring/grafana/dashboards/golden-signals.json`.

Because the dashboard is provisioned from a file, Grafana does not allow it to be persisted directly from the UI. The provisioning source JSON was updated instead, making the panels reproducible and preserving them in Git.

#### Latency Panel

Visualization: Time series

p50 query:

```promql
histogram_quantile(
  0.50,
  sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le)
)
```

p95 query:

```promql
histogram_quantile(
  0.95,
  sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le)
)
```

p99 query:

```promql
histogram_quantile(
  0.99,
  sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le)
)
```

The panel unit is seconds. Grafana automatically scales small values and displays them as milliseconds. Under normal traffic, the observed percentiles were approximately:

- p50: 8–10 ms
- p95: 20–25 ms
- p99: 25–50 ms

#### Saturation Panel

Visualization: Gauge

Query:

```promql
events_db_pool_size
```

Panel configuration:

```text
Min: 0
Max: 10
Base threshold: green
Yellow threshold: 7
Red threshold: 9
```

The saturation gauge shows when the PostgreSQL pool is close to its maximum of ten concurrent connections. During the observed workload, the gauge normally showed zero connections in active use at the instant of each Prometheus scrape, so no database-pool saturation was detected.

### 3.6 — Payments Failure Observation

A steady 5 RPS load was generated and the payments container was stopped at:

```text
2026-09-13T17:24:00Z
```

The first observation samples were:

```text
2026-09-13T17:24:01Z elapsed=  1s payments_up=1    error_rate_pct=NaN          request_rate=1.1999466690369318
2026-09-13T17:24:06Z elapsed=  6s payments_up=1    error_rate_pct=NaN          request_rate=1.1999466690369318
2026-09-13T17:24:11Z elapsed= 11s payments_up=0    error_rate_pct=NaN          request_rate=1.1999466690369318
2026-09-13T17:24:16Z elapsed= 16s payments_up=0    error_rate_pct=NaN          request_rate=2.466611853069932
2026-09-13T17:24:22Z elapsed= 22s payments_up=0    error_rate_pct=NaN          request_rate=2.466611853069932
2026-09-13T17:24:27Z elapsed= 27s payments_up=0    error_rate_pct=NaN          request_rate=2.4666118530699315
2026-09-13T17:24:32Z elapsed= 32s payments_up=0    error_rate_pct=1.5206837727250704 request_rate=3.813404739354913
2026-09-13T17:24:37Z elapsed= 37s payments_up=0    error_rate_pct=1.8101765507842045 request_rate=3.8246521558493463
2026-09-13T17:24:42Z elapsed= 42s payments_up=0    error_rate_pct=2.099220163076809 request_rate=3.8359395750106233
```

The payments target changed from `up=1` to `up=0` after approximately 11 seconds. The gateway 5xx Error Rate first became non-zero after approximately 32 seconds and later reached approximately 3.87%.

The load-generator result for the complete incident scenario was:

```text
Done. total=693 success=564 fail=129 error_rate=18.6%
```

Payments was restarted at:

```text
2026-09-13T17:26:03Z
```

Recovery observations:

```text
2026-09-13T17:26:03Z payments_up=0    error_rate_pct=2.2346368715083798
2026-09-13T17:26:08Z payments_up=1    error_rate_pct=2.2346368715083798
2026-09-13T17:26:14Z payments_up=1    error_rate_pct=2.209944751381215
2026-09-13T17:26:19Z payments_up=1    error_rate_pct=2.209944751381215
2026-09-13T17:26:24Z payments_up=1    error_rate_pct=2.209944751381215
2026-09-13T17:26:29Z payments_up=1    error_rate_pct=0.5586592178770949
```

Prometheus detected the recovered payments target approximately five seconds after it was restarted. The Error Rate decreased gradually because the panel uses a one-minute rolling rate window.

### 3.7 — Which Signal Detected the Failure First?

The first dashboard indicator was the **Service Health** panel: `up{job="payments"}` changed to zero approximately 11 seconds after payments was stopped. This delay is consistent with the configured 15-second Prometheus scrape interval.

Among the four canonical golden signals, **Errors** showed the failure first. Gateway 5xx Error Rate became non-zero approximately 32 seconds after the stop. Traffic continued because event-listing and reservation operations do not require the payments service. Latency and database saturation did not identify this complete payments outage before the Error Rate.

Normal traffic produced no 5xx errors, stable latency, and no database-pool saturation. During the payments failure, Service Health changed to down and gateway Error Rate increased, while non-payment traffic continued to work.

## Task 2 — Define SLOs & Recording Rules

### 3.8 — SLI and SLO Definitions

#### SLI 1 — Availability

Availability is the proportion of gateway requests that do not return a 5xx status:

```promql
sum(rate(gateway_requests_total{status!~"5.."}[5m]))
/
sum(rate(gateway_requests_total[5m]))
```

SLO:

```text
At least 99.5% availability over a seven-day window.
```

With approximately 1000 requests per day:

```text
1000 requests/day × 7 days = 7000 requests/week
Allowed error ratio = 1 - 0.995 = 0.005
7000 × 0.005 = 35
```

The availability error budget therefore allows **35 failed requests per week**.

#### SLI 2 — Latency

Latency compliance is the proportion of gateway requests completed within 500 ms:

```promql
sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m]))
/
sum(rate(gateway_request_duration_seconds_count[5m]))
```

SLO:

```text
At least 95% of gateway requests complete within 500 ms.
```

At 7000 requests per week, the 5% latency budget allows:

```text
7000 × 0.05 = 350
```

Therefore, up to 350 weekly requests may exceed 500 ms while still meeting the latency SLO.

#### Error Budget Burn Rate

The burn rate compares the observed error ratio with the allowed 0.5% error ratio:

```promql
(
  1 - gateway:sli_availability:ratio_rate5m
)
/
(1 - 0.995)
```

Interpretation:

- Burn rate below `1`: the service is consuming budget slowly enough.
- Burn rate equal to `1`: budget is being consumed at exactly the allowed rate.
- Burn rate above `1`: the service is consuming its budget too quickly.

### 3.9 — Recording Rules

The following rules were added to `monitoring/prometheus/rules.yml`:

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
            1 -
            (
              sum(rate(gateway_requests_total{status!~"5.."}[5m]))
              /
              sum(rate(gateway_requests_total[5m]))
            )
          )
          /
          (1 - 0.995)
```

Prometheus configuration validation succeeded:

```text
Prometheus configuration is valid.
1 rule file was found.
3 recording rules were found.
```

All three rules loaded successfully:

```text
gateway:sli_availability:ratio_rate5m              = ok
gateway:sli_latency_500ms:ratio_rate5m             = ok
gateway:error_budget_burn_rate:ratio_rate5m        = ok
```

Example values observed after the first incident:

```text
gateway:sli_availability:ratio_rate5m              = 0.9815195071868584
gateway:sli_latency_500ms:ratio_rate5m             = 1
gateway:error_budget_burn_rate:ratio_rate5m        = 3.6960985626283107
```

The latency SLI was `1`, meaning 100% of observed requests completed within 500 ms. The burn rate of approximately `3.70` showed that the availability error budget was being consumed about 3.7 times faster than allowed during the five-minute observation window.

### 3.10 — SLO Gauge

A Grafana Gauge panel named `Gateway Availability SLO` was added with this query:

```promql
gateway:sli_availability:ratio_rate5m * 100
```

Configuration:

```text
Unit: percent
Min: 99
Max: 100
SLO threshold: 99.5
Below 99.5%: red
At or above 99.5%: green
```

Before the test, the gauge showed:

```text
Availability SLI: 100.000%
```

Payments was then stopped for approximately one minute. The observed SLO values included:

```text
2026-09-13T17:35:10Z elapsed= 31s payments_up=0    availability_pct=99.15254237288134 burn_rate=1.6949152542373043
2026-09-13T17:35:15Z elapsed= 36s payments_up=0    availability_pct=99.15254237288134 burn_rate=1.6949152542373043
2026-09-13T17:35:20Z elapsed= 41s payments_up=0    availability_pct=99.15254237288134 burn_rate=1.6949152542373043
2026-09-13T17:35:25Z elapsed= 46s payments_up=0    availability_pct=99.15254237288134 burn_rate=1.6949152542373043
2026-09-13T17:35:30Z elapsed= 51s payments_up=0    availability_pct=99.15254237288134 burn_rate=1.6949152542373043
2026-09-13T17:35:35Z elapsed= 56s payments_up=0    availability_pct=99.15254237288134 burn_rate=1.6949152542373043
```

The first recording-rule update showing the incident appeared approximately 31 seconds after payments was stopped:

```text
Availability: 99.153%
Burn rate: 1.695
```

The SLO Gauge fell below the 99.5% objective and became red. Because availability uses a rolling five-minute window and the recording rules are evaluated every 30 seconds, the value continued to decrease briefly after payments was restarted. A later sample showed approximately `98.729%`, with burn rate above `2.54`.

This is expected: recovery of the dependency stops new failures, but previous failures remain inside the SLI window until they age out.

## Bonus Task — Correlate Failure Across Metrics & Logs

### Fault Injection

Load was started at 5 RPS. After 30 seconds, payments was recreated with:

```text
PAYMENT_FAILURE_RATE=0.5
PAYMENT_LATENCY_MS=1000
```

The payments health endpoint confirmed the configuration:

```json
{"status":"healthy","failure_rate":0.5,"latency_ms":1000}```

Timeline actions:

```text
BONUS_START=2026-09-13T17:39:43Z
Starting load at 5 RPS for 150 seconds

INJECTION_TIME=2026-09-13T17:40:13Z
PAYMENT_FAILURE_RATE=0.5
PAYMENT_LATENCY_MS=1000

RECOVERY_TIME=2026-09-13T17:41:51Z
Restoring PAYMENT_FAILURE_RATE=0.0 and PAYMENT_LATENCY_MS=0
```

### Correlated Failure Logs

The first failed payment was correlated using reservation ID:

```text
7a5726d5-f553-4986-b40d-220605797eec
```

Matching log lines from payments and gateway:

```text
INJECTION_TIME=2026-09-13T17:40:13Z
FIRST_FAILED_RESERVATION_ID=7a5726d5-f553-4986-b40d-220605797eec

===== CORRELATED FAILURE LOGS =====
payments-1  | 2026-09-13T17:40:18.978391593Z {"time":"2026-09-13 17:40:18,978","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for 7a5726d5-f553-4986-b40d-220605797eec"}
payments-1  | 2026-09-13T17:40:19.982193802Z {"time":"2026-09-13 17:40:19,980","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 7a5726d5-f553-4986-b40d-220605797eec"}
gateway-1   | 2026-09-13T17:40:19.988705219Z INFO:     151.101.128.223:39323 - "POST /reserve/7a5726d5-f553-4986-b40d-220605797eec/pay HTTP/1.1" 500 Internal Server Error
```

Timeline:

1. `17:40:13Z` — payments fault configuration was injected.
2. `17:40:18.978Z` — payments began the artificial 1000 ms delay for the request.
3. `17:40:19.982Z` — payments logged the injected payment failure.
4. `17:40:19.989Z` — gateway returned HTTP 500 for the same reservation.
5. `17:40:28Z` — the p99 dashboard metric showed the latency spike.
6. `17:41:28Z` — the one-minute gateway Error Rate query showed a non-zero value.
7. `17:41:51Z` — payments was restored with zero artificial latency and failure rate.

The first injected payment failure appeared in payments logs approximately:

```text
17:40:19.982 - 17:40:13.000 = 6.982 seconds after injection
```

Gateway returned HTTP 500 approximately 6.989 seconds after injection and approximately 6.5 ms after the corresponding payments failure log.

### Metrics Correlation

Prometheus range-query analysis produced:

```text
INJECTION_TIME=2026-09-13T17:40:13Z
FIRST_ERROR_METRIC=2026-09-13T17:41:28Z value=0.602% delay=75.0s
PEAK_ERROR_METRIC=2026-09-13T17:41:58Z value=0.610%
FIRST_P99_SPIKE=2026-09-13T17:40:28Z value=1.755s delay=15.0s
PEAK_P99_LATENCY=2026-09-13T17:40:58Z value=1.896s
```

The p99 latency signal reacted before the Error Rate query:

- First p99 value above 500 ms: 15 seconds after injection
- Peak p99 latency: approximately 1.896 seconds
- First non-zero Error Rate sample from the range query: 75 seconds after injection
- Peak observed Error Rate in that range: approximately 0.610%

The payments target stayed `up=1` because the process itself remained healthy and scrapeable. The incident was an application-level degradation rather than a container outage.

The Error Rate appeared later than the latency signal because:

- the fault only affects payment requests;
- the load generator also sends many event-listing and reservation requests;
- payment requests are less frequent;
- Prometheus scrapes every 15 seconds;
- Error Rate uses a one-minute `rate()` window;
- the first slow payment requests could still succeed before an injected failure was sampled.

### Root Cause

The root cause was the intentional payments configuration:

```text
PAYMENT_FAILURE_RATE=0.5
PAYMENT_LATENCY_MS=1000
```

The 1000 ms artificial delay caused the gateway p99 latency to rise from tens of milliseconds to approximately 1.9 seconds. The 50% payment failure probability produced HTTP 500 responses from `/charge`, which gateway propagated to `/reserve/{id}/pay`. Prometheus then observed the gateway 5xx series and the availability SLI decreased.

The gateway and payments logs were correlated using the same reservation ID, proving that the metrics spike was caused by injected payment failures rather than by PostgreSQL, Redis, or the events service.

The final bonus load-generator result was:

```text
Done. total=550 success=409 fail=141 error_rate=25.6%
```

This total includes both injected 5xx payment failures and 409 reservation conflicts caused by tickets already held or exhausted during repeated laboratory tests. The dashboard Error Rate query counts only 5xx responses, so it is the appropriate signal for the injected service failure.

### Recovery

Payments was restored with:

```text
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
```

Final payments health:

```json
{
  "status": "healthy",
  "failure_rate": 0.0,
  "latency_ms": 0
}
```

Final gateway health:

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

The fault was therefore removed successfully, and all QuickTicket dependencies returned to healthy state.
