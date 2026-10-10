# Lab 3 — Monitoring, Observability & SLOs

Test date: 21 September 2026. All timestamps below are UTC.

Docker Hub was unavailable, so the test used cached Prometheus 2.54.0 and Grafana 12.2.0 images through [compose.local.yaml](../lab3-results/compose.local.yaml). The main Compose file keeps the versions provided in the lab.

## Task 1 — Monitoring and dashboard

Prometheus scrapes gateway, events and payments every 15 seconds.

### Compose services

```text
NAME               IMAGE                     COMMAND                  SERVICE      CREATED          STATUS                    PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       7 days ago       Up 14 minutes             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      7 days ago       Up 14 minutes             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:12.2.0    "/run.sh"                grafana      14 minutes ago   Up 14 minutes             0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     5 minutes ago    Up 5 minutes              0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     7 days ago       Up 14 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v2.54.0   "/bin/prometheus --c…"   prometheus   14 minutes ago   Up 14 minutes             0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        7 days ago       Up 14 minutes (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

### Prometheus targets

```text
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

### Custom metrics

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

### Request rate

The standard load generator was run with a target of 5 requests/s. Measured rate after the five-minute window filled:

```promql
sum(rate(gateway_requests_total[5m]))
```

```text
Request rate: 4.51 req/s
```

### Dashboard panels

Latency: Time series, unit: seconds.

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

Saturation: Gauge, min 0, max 10; green by default, yellow at 7, red at 9.

```promql
events_db_pool_size
```

### Payments failure

Traffic ran for 200 seconds. Payments was stopped after 60 seconds and restarted about two minutes later.

| State | Error rate | p50 | p95 | p99 | DB pool |
|---|---:|---:|---:|---:|---:|
| Before failure | 0% | 6.97 ms | 19.13 ms | 24.18 ms | 0 |
| End of outage | 5.21% | 6.68 ms | 9.73 ms | 10.00 ms | 0 |

The error rate peaked at 7.44%. Event reads continued working. Latency did not increase because gateway quickly returned 502 when it could not connect to payments. The DB pool showed no saturation.

**The first golden signal was Error Rate.** Payments stopped at 07:27:33; the error-rate query showed 2.12% at 07:27:51, about **18 seconds later**. Service Health showed `up=0` after 2 seconds, but it is not one of the four golden signals. These timings come from polling the panel queries every 2 seconds; Grafana refreshes every 5 seconds.

Payments restarted at 07:29:34.

## Task 2 — SLOs and recording rules

- **Availability SLI:** percentage of gateway responses that are not 5xx. **SLO:** at least 99.5% over 7 days.
- **Latency SLI:** percentage of gateway requests within 500 ms, using the `le="0.5"` bucket. **SLO:** at least 95%.

At 1,000 requests/day, the weekly availability error budget is:

```text
1,000 × 7 = 7,000 requests/week
7,000 × (1 - 0.995) = 35 allowed failures/week
```

The three rules in [rules.yml](../monitoring/prometheus/rules.yml) evaluate every 30 seconds. They track five-minute SLIs and burn rate, rather than the full seven-day SLO.

Rules loaded successfully:

```text
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

The availability Gauge uses `gateway:sli_availability:ratio_rate5m * 100`, min 99, max 100, threshold 99.5.

During the outage, availability fell from 100% to **95.97%**, and burn rate reached **8.06**. The Gauge first dropped below the target at 07:28:07, about 34 seconds after payments stopped. After recovery, old errors remained in the five-minute window before availability returned to 100%.

## Bonus — Correlating metrics and logs

Traffic ran for 180 seconds. After 30 seconds, payments was recreated with `PAYMENT_FAILURE_RATE=0.5` and `PAYMENT_LATENCY_MS=1000`. The failure was observed for two minutes.

| Time (UTC) | Event |
|---|---|
| 07:32:23 | Fault settings applied to payments |
| 07:32:52 | p99 rose to 1.045 s |
| 07:33:02.685 | First injected payment error |
| 07:33:02.689 | Gateway returned 500 for the same reservation |
| 07:33:22 | Error Rate showed 1.34% |
| 07:34:25 | Normal payment settings restored |
| 07:34:29.345 | First successful payment after recovery |
| 07:35:07 | Error Rate returned to 0% |

Log excerpts (formatting shortened):

```text
payments 2026-09-21T07:33:01.685004258Z Injecting 1000ms latency for 1d3578f9-ed0b-4380-a084-5e28546b1e7f
payments 2026-09-21T07:33:02.685446647Z Payment failed (injected) for 1d3578f9-ed0b-4380-a084-5e28546b1e7f
payments 2026-09-21T07:33:02.686361322Z POST /charge HTTP/1.1 500 Internal Server Error
gateway  2026-09-21T07:33:02.687519041Z HTTP Request: POST http://payments:8082/charge HTTP/1.1 500 Internal Server Error
gateway  2026-09-21T07:33:02.689340366Z POST /reserve/1d3578f9-ed0b-4380-a084-5e28546b1e7f/pay HTTP/1.1 500 Internal Server Error
```

**Root cause:** payments delayed requests by one second and injected 500 responses. Gateway passed the error to the client; the matching reservation ID connects both logs. Slow successful payments occurred before the first failure, so p99 rose before Error Rate. All targets remained `up` because their metrics endpoints were still reachable.

After restoring both fault settings to zero and allowing the five-minute window to clear, Error Rate was 0%, availability and latency SLIs were 100%, and burn rate was 0. All seven services were running.
