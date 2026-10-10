# Lab 3 — Monitoring, Observability & SLOs

## Student
- GitHub: `MiniMaxC`
- Branch: `feature/lab3`

---

# Task 1 — Configure Monitoring & Build Dashboard

## 3.1 Prometheus configuration

`monitoring/prometheus/prometheus.yml`:

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
          - "gateway:8080"

  - job_name: events
    static_configs:
      - targets:
          - "events:8081"

  - job_name: payments
    static_configs:
      - targets:
          - "payments:8082"
```

Prometheus uses Docker Compose service names and the services' internal ports.

---

## 3.2 Monitoring stack

Final `docker compose ps` output showed all seven services running:

```text
NAME               IMAGE                     SERVICE      STATUS
app-events-1       app-events                events       Up
app-gateway-1      app-gateway               gateway      Up
app-grafana-1      grafana/grafana:13.0.1    grafana      Up
app-payments-1     app-payments              payments     Up
app-postgres-1     postgres:17-alpine         postgres     Up (healthy)
app-prometheus-1   prom/prometheus:v3.11.2   prometheus   Up
app-redis-1        redis:7-alpine            redis        Up (healthy)
```

Published ports included:
- gateway: `3080`
- events: `8081`
- payments: `8082`
- Prometheus: `9090`
- Grafana: `3000`

---

## 3.3 Prometheus scrape targets

After recovery, all three application targets were healthy:

```text
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

---

## 3.4 Custom metrics

Example raw gateway metrics:

```text
gateway_requests_total{method="GET",path="/events",status="200"} 62.0
gateway_requests_total{method="POST",path="/events/{id}/reserve",status="200"} 28.0
gateway_requests_total{method="POST",path="/reserve/{id}/pay",status="200"} 12.0
gateway_requests_created{method="GET",path="/events",status="200"} 1.7899317992641966e+09
gateway_requests_created{method="POST",path="/events/{id}/reserve",status="200"} 1.789931799703147e+09
gateway_requests_created{method="POST",path="/reserve/{id}/pay",status="200"} 1.7899318019009354e+09
gateway_request_duration_seconds_bucket{le="0.005",method="GET",path="/events"} 61.0
gateway_request_duration_seconds_bucket{le="0.01",method="GET",path="/events"} 61.0
gateway_request_duration_seconds_bucket{le="0.025",method="GET",path="/events"} 61.0
gateway_request_duration_seconds_bucket{le="0.05",method="GET",path="/events"} 62.0
```

Custom metric names discovered through Prometheus:

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

---

## 3.5 Request-rate query

Traffic was generated with:

```bash
./loadgen/run.sh 5 20
```

The run completed with:

```text
Done. total=87 success=87 fail=0 error_rate=0%
```

PromQL:

```promql
sum(rate(gateway_requests_total[5m]))
```

Observed output:

```text
Request rate: 0.33 req/s
```

The value is lower than the generator's nominal 5 RPS because the PromQL expression averages over a 5-minute window while the traffic burst lasted only 20 seconds.

---

## 3.6 Golden Signals dashboard

The provisioned `QuickTicket — Golden Signals` dashboard was completed by replacing the two placeholder panels.

### Request Latency

Visualization: Time series

p50:

```promql
histogram_quantile(
  0.50,
  sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le)
)
```

p95:

```promql
histogram_quantile(
  0.95,
  sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le)
)
```

p99:

```promql
histogram_quantile(
  0.99,
  sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le)
)
```

Legends:
- `p50`
- `p95`
- `p99`

Unit: seconds.

### DB Pool Saturation

Visualization: Gauge

```promql
events_db_pool_size
```

Configuration:

```text
Min: 0
Max: 10
green: default
yellow: 7
red: 9
```

Because the dashboard is provisioned from a read-only JSON file, the durable changes were made in:

```text
monitoring/grafana/dashboards/golden-signals.json
```

---

## 3.7 Payments outage observation

Steady traffic was generated and the payments service was stopped.

Observed behavior:

- **Service Health** was the clearest first signal and changed `payments` from `1` to `0` within roughly one Prometheus scrape interval, about 15 seconds.
- **Error Rate** increased afterward as failed payment requests accumulated.
- During the observed incident, Error Rate rose from roughly `1.77%` to `3.85%`, and later historical data showed a larger spike.
- **Request Latency** increased sharply during the failure.
- **Request Rate** continued because event browsing and some reservation traffic did not depend directly on successful payments.
- **DB Pool Saturation** did not materially change, which is expected because the incident was in the payments service rather than the events database pool.

### Which golden signal showed the failure first?

**Service Health** showed the outage first, within approximately one 15-second Prometheus scrape interval after stopping payments.

After restarting payments, all targets returned to:

```text
events       up
gateway      up
payments     up
```

---

# Task 2 — Define SLOs & Recording Rules

## 3.8 SLI and SLO definitions

### Availability SLI

Definition:

> Percentage of gateway requests that do not return a 5xx response.

Target:

```text
99.5% over 7 days
```

### Latency SLI

Definition:

> Percentage of gateway requests completing in under 500 ms.

Target:

```text
95%
```

### Error budget

At approximately 1000 requests/day:

```text
1000 requests/day × 7 days = 7000 requests/week

Allowed failure fraction:
100% - 99.5% = 0.5% = 0.005

7000 × 0.005 = 35
```

Therefore the availability SLO allows approximately:

```text
35 failed requests per week
```

before the weekly error budget is exhausted.

---

## 3.9 Prometheus recording rules

`monitoring/prometheus/rules.yml`:

```yaml
groups:
  - name: slo_rules
    interval: 30s
    rules:
      - record: gateway:sli_availability:ratio_rate5m
        expr: |
          1 -
          (
            sum(rate(gateway_requests_total{status=~"5.."}[5m]))
            /
            sum(rate(gateway_requests_total[5m]))
          )

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

Prometheus was configured with:

```yaml
rule_files:
  - "rules.yml"
```

and `docker-compose.monitoring.yaml` mounts:

```yaml
- ../monitoring/prometheus/rules.yml:/etc/prometheus/rules.yml:ro
```

### Rules-loaded verification

```text
gateway:sli_availability:ratio_rate5m              ok
gateway:sli_latency_500ms:ratio_rate5m             ok
gateway:error_budget_burn_rate:ratio_rate5m        ok
```

### Healthy SLO values

With normal service behavior:

```text
gateway:sli_availability:ratio_rate5m        = 1
gateway:sli_latency_500ms:ratio_rate5m       = 1
gateway:error_budget_burn_rate:ratio_rate5m  = 0
```

This corresponds to:
- availability = 100%
- under-500ms latency = 100%
- burn rate = 0×

---

## 3.10 Availability SLO gauge

A Grafana Gauge panel was added using:

```promql
gateway:sli_availability:ratio_rate5m * 100
```

Configuration:

```text
Min: 99
Max: 100
SLO threshold: 99.5
```

During a deliberately induced payments outage:

```text
gateway:sli_availability:ratio_rate5m       = 0.9681818181818181
gateway:error_budget_burn_rate:ratio_rate5m = 6.363636363636366
```

Therefore:

```text
Availability ≈ 96.82%
Burn rate    ≈ 6.36×
```

The availability gauge fell below the 99.5% SLO target, while the burn-rate metric showed the error budget was being consumed more than six times faster than the allowed rate.

---

# Bonus Task — Correlate Failure Across Metrics & Logs

## Controlled incident

A 120-second traffic run was started at:

```text
2026-09-20T23:09:19.651830646+03:00
```

Exactly 30 seconds later, the payments service was recreated with:

```text
PAYMENT_FAILURE_RATE=0.5
PAYMENT_LATENCY_MS=1000
```

Failure injection time:

```text
2026-09-20T23:09:49.676530424+03:00
```

This represents a degraded-but-running dependency rather than a completely unavailable service.

---

## Forced payment results

To guarantee payment traffic reached the degraded service, 12 explicit payment attempts were made.

```text
23:10:47.474 attempt=1  payment_status=200
23:10:48.514 attempt=2  payment_status=500
23:10:49.556 attempt=3  payment_status=200
23:10:50.601 attempt=4  payment_status=200
23:10:51.648 attempt=5  payment_status=200
23:10:52.697 attempt=6  payment_status=200
23:10:53.740 attempt=7  payment_status=200
23:10:54.784 attempt=8  payment_status=200
23:10:55.825 attempt=9  payment_status=500
23:10:56.870 attempt=10 payment_status=500
23:10:57.911 attempt=11 payment_status=500
23:10:58.955 attempt=12 payment_status=500
```

Result:

```text
7 successful payments
5 failed payments
```

The observed mix is consistent with the configured 50% injected failure rate.

---

## SLOs during the degraded period

```text
gateway:sli_availability:ratio_rate5m
= 0.982455427483671

gateway:sli_latency_500ms:ratio_rate5m
= 0.9616204690831556

gateway:error_budget_burn_rate:ratio_rate5m
= 3.5089145032658
```

Equivalent values:

```text
Availability                    ≈ 98.25%
Requests completing < 500 ms    ≈ 96.16%
Error-budget burn rate          ≈ 3.51×
```

This shows both reliability and latency degradation while the payments service remained reachable.

---

## Timeline

| Event | Time |
|---|---|
| Load started | 23:09:19.652 |
| Payments failure/latency injection | 23:09:49.677 |
| First gateway payment 500 visible in filtered logs | 23:10:11.877 |
| First non-zero dashboard Error Rate sample | 23:10:50.000 |
| Recovery started | 23:11:51.691 |

The first gateway payment failure appeared approximately:

```text
22.2 seconds after injection
```

The first non-zero Error Rate sample returned by Prometheus was:

```text
2026-09-20T20:10:50+00:00
0.53%
```

which is local time:

```text
2026-09-20T23:10:50+03:00
```

or approximately:

```text
60.3 seconds after injection
```

This delay is expected because dashboard metrics are based on Prometheus scrape/rate windows rather than instantaneous log events.

---

## Correlated log excerpts

Payments service:

```text
20:10:53.542 payments WARNING
Payment failed (injected)

20:10:53.542 payments
POST /charge HTTP/1.1 500 Internal Server Error

20:10:55.818 payments WARNING
Payment failed (injected)

20:10:55.818 payments
POST /charge HTTP/1.1 500 Internal Server Error

20:10:56.864 payments WARNING
Payment failed (injected)

20:10:56.865 payments
POST /charge HTTP/1.1 500 Internal Server Error
```

Gateway:

```text
20:10:48.508 gateway
POST http://payments:8082/charge -> 500 Internal Server Error

20:10:48.509 gateway
POST /reserve/.../pay -> 500 Internal Server Error

20:10:53.543 gateway
POST http://payments:8082/charge -> 500 Internal Server Error

20:10:53.544 gateway
POST /reserve/.../pay -> 500 Internal Server Error

20:10:55.819 gateway
POST http://payments:8082/charge -> 500 Internal Server Error
```

The payments logs show the injected root cause directly. The gateway then propagates those payment-service failures as failed `/pay` requests, which increases the 5xx metric used by the availability SLI and Error Rate panel.

---

## Root cause

The incident was intentionally caused by:

```text
PAYMENT_FAILURE_RATE=0.5
PAYMENT_LATENCY_MS=1000
```

The service itself stayed alive, so a simple `up` health metric alone would not fully describe the incident. Instead:

1. payments began returning injected 500 responses,
2. gateway `/pay` requests failed,
3. the gateway 5xx rate rose,
4. availability dropped below the 99.5% SLO,
5. the burn rate exceeded 1,
6. increased payment latency also reduced the under-500ms latency SLI.

This demonstrates why service health, errors, latency, and SLO metrics need to be observed together.

---

## Recovery

Recovery began at:

```text
2026-09-20T23:11:51.691121800+03:00
```

Payments was recreated with:

```text
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
```

Health after recovery:

```json
{
  "status": "healthy",
  "failure_rate": 0.0,
  "latency_ms": 0
}
```

Final Prometheus target state:

```text
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

All seven Docker Compose services were running at the end of the experiment.

---

# Submission Checklist

- [x] Task 1 done — monitoring deployed, dashboard completed
- [x] Task 2 done — SLOs defined, recording rules created
- [x] Bonus Task done — failure correlation completed
