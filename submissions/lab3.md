# Lab 3 — Monitoring, Observability and SLOs

## Task 1 — Monitoring and golden signals

### Running stack

The host port `5432` was already occupied by another local project, so I used a temporary Compose override that published QuickTicket PostgreSQL on `15432`. Internal traffic still used `postgres:5432`.

```text
$ docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml -f /tmp/sre-intro-lab3.override.yaml ps
NAME               IMAGE                     SERVICE      STATUS
app-events-1       app-events                events       Up
app-gateway-1      app-gateway               gateway      Up
app-grafana-1      grafana/grafana:13.0.1    grafana      Up
app-payments-1     app-payments              payments     Up
app-postgres-1     postgres:17-alpine        postgres     Up (healthy)
app-prometheus-1   prom/prometheus:v3.11.2   prometheus   Up
app-redis-1        redis:7-alpine            redis        Up (healthy)
```

### Prometheus targets

After waiting for one scrape interval:

```text
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

Prometheus uses Compose service names and internal ports. Published host ports are not involved in scraping.

### Custom metrics

Python counters and histograms with labels appear after the first matching request. After generating traffic, Prometheus contained:

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

### Normal traffic

```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 20s
[10s] requests=40 success=40 fail=0 error_rate=0%
Done. total=81 success=81 fail=0 error_rate=0%
```

The five-minute Prometheus query returned:

```text
Request rate: 0.26 req/s
```

It is lower than the configured generator rate because `rate(...[5m])` averages a short 20-second burst over a five-minute range.

Normal golden-signal values after the run:

```text
p50 latency       0.003910 s
p95 latency       0.020281 s
p99 latency       0.034750 s
DB pool size      0
availability SLI  1
latency SLI       1
burn rate         0
```

### Dashboard panels

The dashboard is provisioned from `monitoring/grafana/dashboards/golden-signals.json`. Grafana API confirmed six panels and no remaining `YOUR TURN` placeholders:

```text
timeseries Request Rate (Traffic)
timeseries Error Rate
table      Service Health (up/down)
timeseries Request Latency
gauge      Database Pool Saturation
gauge      Availability SLO
```

Latency queries:

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

Saturation query:

```promql
events_db_pool_size
```

The saturation gauge uses min 0, max 10, yellow at 7, and red at 9.

### Payments hard-down observation

I started 5 RPS and stopped payments at `2026-09-20T12:19:49Z`.

```text
12:19:50Z  after=0s   Errors=no new series  Availability=100%  Burn=0
12:20:05Z  after=15s  Errors=no new series  Availability=100%  Burn=0
12:20:21Z  after=30s  Errors=3.38%          Availability=100%  Burn=0
12:20:26Z  after=35s  Errors=4.01%          Availability=100%  Burn=0
12:20:26Z  payments restarted
```

The recording-rule group evaluates every 30 seconds, so its value changed on the next evaluation after Prometheus had scraped the 502 series:

```text
gateway:sli_availability:ratio_rate5m      0.9644475854
gateway:error_budget_burn_rate:ratio_rate5m 7.1104829151
```

The **Errors** golden signal showed the failure first. The load generator saw failed purchases immediately, while the dashboard query first had a non-empty 5xx result about 30 seconds after stopping payments. The delay came from the 15-second scrape interval plus the need for two counter samples for `rate()`; the recording-rule gauge had up to another 30 seconds of evaluation delay.

After payments was restarted, gateway health returned `healthy`. Once the one-minute error window aged out, the direct 5xx query returned 0.

## Task 2 — SLIs, SLOs and recording rules

### SLI and SLO definitions

Availability SLI:

```promql
sum(rate(gateway_requests_total{status!~"5.."}[5m]))
/
sum(rate(gateway_requests_total[5m]))
```

- SLO: 99.5% over 7 days.
- At 1,000 requests/day there are about 7,000 requests/week.
- Error budget: `7,000 × (1 - 0.995) = 35` failed requests per week.

Latency SLI:

```promql
sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m]))
/
sum(rate(gateway_request_duration_seconds_count[5m]))
```

- SLO: 95% of gateway requests complete within 500 ms.
- At 7,000 requests/week, the latency budget allows `7,000 × 5% = 350` requests slower than 500 ms.

The availability SLI treats 4xx responses as successful from the reliability perspective because the task defines failure as 5xx. The load-generator `fail` counter is broader and also includes responses such as 409, so those two percentages should not be compared as if they were the same metric.

### Recording rules

Prometheus loaded all rules successfully:

```text
gateway:sli_availability:ratio_rate5m                    = ok
gateway:sli_latency_500ms:ratio_rate5m                   = ok
gateway:error_budget_burn_rate:ratio_rate5m              = ok
```

The burn-rate expression is:

```promql
(1 - gateway:sli_availability:ratio_rate5m) / (1 - 0.995)
```

A value above 1 means the 0.5% weekly error budget is being consumed faster than the sustainable rate. During the hard payments outage it reached 7.11, so the service was burning budget approximately seven times too quickly.

### Availability SLO gauge

The Grafana gauge uses:

```promql
gateway:sli_availability:ratio_rate5m * 100
```

Its range is 99–100%, with the target threshold at 99.5%. Normal traffic produced 100%; the payments outage eventually reduced the recorded five-minute availability to about 96.4%, clearly below the target.

## Bonus — Correlate metrics and logs

### Long observation

The 120-second run injected `PAYMENT_FAILURE_RATE=0.5` and `PAYMENT_LATENCY_MS=1000` at `12:22:14Z`, then restored normal payments at `12:23:15Z`.

```text
Time       Since injection  5xx rate  p99 latency
12:22:25Z  10s              0.70%      0.032s
12:22:35Z  20s              0.57%      1.829s
12:22:45Z  30s              1.75%      1.927s
12:22:55Z  40s              2.64%      2.095s
12:23:05Z  50s              3.73%      2.282s
12:23:15Z  60s              4.03%      2.209s
```

Latency reacted strongly because every charge slept for one second before succeeding or failing. The histogram interpolation placed p99 above two seconds even though the configured delay was one second; Prometheus histograms estimate quantiles from bucket boundaries rather than storing exact request durations.

After restoration:

```json
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

The one-minute 5xx query returned 0 after the failure window aged out.

### Exact correlated failure

I performed a short replay so that payments and gateway logs could be captured before the payments container was replaced during recovery.

```text
12:24:41Z          failure injection enabled
12:24:47.903603Z   payments logs injected failure for reservation 8724ae16-...
12:24:47.904769Z   gateway logs downstream /charge HTTP 500       (+1.17 ms)
12:24:47.905660Z   gateway returns HTTP 500 to the client          (+0.89 ms)
12:25:17Z          normal payments configuration restored
```

Payments root-cause log:

```text
payments-1 | 2026-09-20T12:24:47.903602836Z {"level":"WARNING","service":"payments","msg":"Payment failed (injected) for 8724ae16-9b38-477f-9671-448b84eb825b"}
```

Gateway propagation logs:

```text
gateway-1 | 2026-09-20T12:24:47.904768829Z ... "POST http://payments:8082/charge ... 500 Internal Server Error"
gateway-1 | 2026-09-20T12:24:47.905659532Z ... "POST /reserve/8724ae16-9b38-477f-9671-448b84eb825b/pay HTTP/1.1" 500 Internal Server Error
```

The correlated Prometheus snapshot showed:

```text
5xx rate:    8.33%
p99 latency: 2.47 seconds
```

The shared reservation ID proves that the payments warning and gateway 500 are the same request. The root cause was deliberate payments fault injection, not PostgreSQL, Redis, or network failure: payments logged the injected rejection first, gateway propagated its HTTP 500, and the gateway metrics then reflected both increased errors and latency.
