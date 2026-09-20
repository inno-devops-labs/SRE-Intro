# Lab 3 — Monitoring, SLOs and Failure Correlation

## Task 1 — Monitoring and Golden Signals

Monitoring was added to the QuickTicket application using Prometheus and Grafana.

The monitoring stack includes:

* Prometheus for metrics collection and recording rules.
* Grafana for visualization and dashboards.
* Metrics endpoints exposed by:

  * `gateway:8080/metrics`
  * `events:8081/metrics`
  * `payments:8082/metrics`

### Golden Signals Dashboard

A Grafana dashboard named **QuickTicket — Golden Signals** was created.

The dashboard contains:

* **Request Rate** — number of requests over time.
* **Error Rate** — proportion of HTTP 5xx responses.
* **Service Health** — service availability/status.
* **Latency** — p50, p95 and p99 request duration.
* **Saturation** — database connection pool size.

Latency percentiles are calculated using the gateway request duration histogram:

```promql
histogram_quantile(
  0.50,
  sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le)
)

histogram_quantile(
  0.95,
  sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le)
)

histogram_quantile(
  0.99,
  sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le)
)
```

The saturation panel uses:

```promql
events_db_pool_size
```

The configured gauge range is 0–10, with thresholds at 7 and 9.

### Failure Scenario

A failure scenario was tested by stopping the payments service while traffic was being generated.

The resulting failures were visible in the monitoring dashboard through an increase in the error rate and degradation of the availability SLI.

This confirmed that the monitoring setup detects service failures and exposes their impact through the Golden Signals dashboard.

---

# Task 2 — SLOs and Error Budgets

Two Service Level Objectives were defined for the gateway.

## Availability SLO

The target availability is:

**99.5% over a 7-day period.**

With approximately 1000 requests per day:

```text
1000 × 7 = 7000 requests/week
```

The allowed error budget is:

```text
7000 × (1 - 0.995) = 35 requests
```

Therefore, approximately **35 failed requests per week** are allowed while remaining within the 99.5% availability target.

## Latency SLO

The latency objective is:

> At least 95% of gateway requests should complete in less than 500 ms.

The corresponding Prometheus recording rule is:

```promql
sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m]))
/
sum(rate(gateway_request_duration_seconds_count[5m]))
```

## Prometheus Recording Rules

The following recording rules were added to `monitoring/prometheus/rules.yml`:

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
          (1 - gateway:sli_availability:ratio_rate5m) / (1 - 0.995)
```

Prometheus was configured to load the rules through:

```yaml
rule_files:
  - "rules.yml"
```

The rules file was mounted into the Prometheus container as:

```yaml
../monitoring/prometheus/rules.yml:/etc/prometheus/rules.yml:ro
```

The configuration was validated using:

```bash
promtool check rules /etc/prometheus/rules.yml
```

Result:

```text
SUCCESS: 3 rules found
```

## Grafana SLO Visualization

The availability SLO was visualized as a Grafana gauge using:

```promql
gateway:sli_availability:ratio_rate5m * 100
```

The gauge uses a 99–100% range with the 99.5% SLO threshold.

During the injected failure scenario, the availability value dropped below the 99.5% target, demonstrating that the SLO visualization reflects service degradation.

---

# Bonus Task — Failure Correlation

## Failure Injection

A controlled failure was introduced into the payments service using:

```bash
PAYMENT_FAILURE_RATE=0.5
PAYMENT_LATENCY_MS=1000
```

The payments service was recreated with:

```bash
PAYMENT_FAILURE_RATE=0.5 PAYMENT_LATENCY_MS=1000 \
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml \
up -d --force-recreate payments
```

This configuration introduced:

* a 50% probability of payment failures;
* an additional 1000 ms latency for payment requests.

## Timeline

All Docker timestamps below are in UTC. Grafana displayed the dashboard timestamp in local time, which was three hours ahead of UTC during the experiment.

| Event                                             | Time                              |
| ------------------------------------------------- | --------------------------------- |
| Payments service restarted with failure injection | ~12:00:03 UTC                     |
| First injected 1000 ms latency                    | 12:00:09.243 UTC                  |
| First observed injected payment failure           | **12:01:20.974 UTC**              |
| Payments returned HTTP 500                        | **12:01:20.976 UTC**              |
| Gateway received HTTP 500 from payments           | **12:01:20.976 UTC**              |
| Gateway returned HTTP 500 to client               | **12:01:20.978 UTC**              |
| Grafana dashboard spike                           | **12:01:45 UTC** |
| Payments service restored                         | **12:06:14 UTC**                  |
| Payments `/metrics` returned 200 after recovery   | **12:06:25 UTC**                  |

The approximately 24-second difference between the first payment failure and the visible Grafana spike is consistent with the Prometheus/Grafana collection and dashboard evaluation intervals.

## Payments Log

The first observed injected failure was:

```text
2026-09-20T12:01:20.974872379Z
{"time":"2026-09-20 12:01:20,974",
 "level":"WARNING",
 "service":"payments",
 "msg":"Payment failed (injected) for edd1e6f9-08c7-43d4-a312-405d771d739d"}
```

Immediately afterwards, the payments service returned HTTP 500:

```text
2026-09-20T12:01:20.976113670Z
INFO: 172.25.0.8:44552 - "POST /charge HTTP/1.1" 500 Internal Server Error
```

This confirms that the HTTP 500 was caused by the intentionally injected payment failure.

## Gateway Log

At almost exactly the same timestamp, the gateway recorded the downstream failure:

```text
2026-09-20T12:01:20.976947420Z
{"time":"2026-09-20 12:01:20,976",
 "level":"INFO",
 "service":"gateway",
 "msg":"HTTP Request: POST http://payments:8082/charge HTTP/1.1 500 Internal Server Error"}
```

The gateway subsequently returned the error to the client:

```text
2026-09-20T12:01:20.978507670Z
INFO:
"POST /reserve/edd1e6f9-08c7-43d4-a312-405d771d739d/pay HTTP/1.1"
500 Internal Server Error
```

The timestamps demonstrate direct propagation of the downstream payment failure through the gateway.

Other requests to the events service around the same time continued to return `200 OK`. Some `409 Conflict` responses were also observed, but these were reservation conflicts and were unrelated to the injected payments failure.

## Grafana Correlation

The Grafana dashboard showed a noticeable error/availability spike at approximately:

```text
15:01:45 local time
12:01:45 UTC
```

This occurred shortly after the first injected payment failure at:

```text
12:01:20.974 UTC
```

The dashboard therefore reflected the same failure that was observed in the application logs.

## Recovery

The payments service was restored without failure injection:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml \
up -d --force-recreate payments
```

The new payments container started at:

```text
2026-09-20T12:06:14.908 UTC
```

The service successfully responded to Prometheus shortly afterwards:

```text
2026-09-20T12:06:25.878 UTC
GET /metrics HTTP/1.1 200 OK
```

This confirmed that the payments service had recovered and was again available for monitoring.

## Root Cause

The root cause of the observed errors was an intentionally injected failure in the payments service.

The service was configured with:

```text
PAYMENT_FAILURE_RATE=0.5
```

which caused approximately half of payment attempts to fail intentionally.

At the same time:

```text
PAYMENT_LATENCY_MS=1000
```

introduced an additional 1000 ms delay for payment processing.

The first observed injected failure occurred at `12:01:20.974 UTC`. Payments returned HTTP 500, which was immediately observed by the gateway. The gateway then returned HTTP 500 for the corresponding reservation payment request.

The failure was subsequently reflected in the monitoring system as an increase in errors and a decrease in the availability SLI.

The correlation between logs and metrics confirms the following chain:

1. Failure injection was enabled in the payments service.
2. Payments generated an intentional HTTP 500.
3. Gateway received the downstream HTTP 500.
4. Gateway returned HTTP 500 to the client.
5. Prometheus collected the resulting request metrics.
6. Grafana displayed the resulting degradation in the error/availability metrics.
7. After payments was recreated without failure injection, the service became healthy again.

This demonstrates that the monitoring setup can be used not only to detect an incident, but also to correlate a metric anomaly with the exact application-level failure and identify its root cause.
