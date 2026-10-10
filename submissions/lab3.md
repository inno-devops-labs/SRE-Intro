# Lab 3 - Monitoring, Observability & SLOs

## Task 1 - Monitoring & Dashboard

### Prometheus configuration

Prometheus scrapes all three QuickTicket services by Docker Compose service name and internal port:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "rules.yml"

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

### Compose status

```text
NAME               IMAGE                     COMMAND                  SERVICE      CREATED          STATUS                    PORTS
app-events-1       app-events                "uvicorn main:app --..."   events       31 seconds ago   Up 30 seconds             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --..."   gateway      31 seconds ago   Up 30 seconds             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      31 seconds ago   Up 30 seconds             0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --..."   payments     31 seconds ago   Up 31 seconds             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s..."   postgres     13 minutes ago   Up 13 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c..."   prometheus   31 seconds ago   Up 30 seconds             0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s..."   redis        13 minutes ago   Up 13 minutes (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
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

Gateway metrics sample:

```text
gateway_requests_total{method="POST",path="/events/{id}/reserve",status="200"} 159.0
gateway_requests_total{method="GET",path="/events",status="200"} 450.0
gateway_requests_total{method="POST",path="/reserve/{id}/pay",status="200"} 33.0
gateway_requests_total{method="POST",path="/reserve/{id}/pay",status="502"} 18.0
gateway_requests_total{method="POST",path="/events/{id}/reserve",status="409"} 50.0
gateway_requests_total{method="POST",path="/reserve/{id}/pay",status="500"} 11.0
```

### Request rate query

Load generator:

```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 20s
---
[10s] requests=37 success=37 fail=0 error_rate=0%
[10s] requests=38 success=38 fail=0 error_rate=0%
[10s] requests=39 success=39 fail=0 error_rate=0%
---
Done. total=72 success=72 fail=0 error_rate=0%
```

PromQL output:

```text
Request rate: 0.16 req/s
```

### Dashboard panels

The Grafana dashboard file `monitoring/grafana/dashboards/golden-signals.json` was updated so the dashboard is provisioned with the required panels instead of manual-only placeholders.

Latency panel queries:

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

Saturation panel query:

```promql
events_db_pool_size
```

SLO gauge query:

```promql
gateway:sli_availability:ratio_rate5m * 100
```

### Failure observation

Payments stopped at:

```text
2026-09-16T21:02:49Z
```

Load generator during payments outage:

```text
[20s] requests=75 success=74 fail=1 error_rate=1.3%
[30s] requests=111 success=106 fail=5 error_rate=4.5%
[40s] requests=147 success=137 fail=10 error_rate=6.8%
[50s] requests=185 success=171 fail=14 error_rate=7.5%
---
Done. total=221 success=202 fail=19 error_rate=8.5%
```

Prometheus error-rate query during failure:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1789592615.170,"6.911071566168942"]}]}}
```

Recovery check:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"__name__":"up","instance":"gateway:8080","job":"gateway"},"value":[1789592635.563,"1"]},{"metric":{"__name__":"up","instance":"payments:8082","job":"payments"},"value":[1789592635.563,"1"]},{"metric":{"__name__":"up","instance":"events:8081","job":"events"},"value":[1789592635.563,"1"]}]}}
```

The service health signal (`up{job="payments"}`) is the first golden signal to show the failure, at the next Prometheus scrape after `payments` is stopped, so within roughly the 15 second scrape interval. Error rate followed immediately as payment requests started returning 5xx; the load generator showed the first failures in the next reporting window after the stop.

## Task 2 - SLOs & Recording Rules

### SLI/SLO definitions

Availability SLI:

```promql
sum(rate(gateway_requests_total{status!~"5.."}[5m])) / sum(rate(gateway_requests_total[5m]))
```

Availability SLO: 99.5% of gateway requests should return non-5xx over 7 days.

Latency SLI:

```promql
sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m])) / sum(rate(gateway_request_duration_seconds_count[5m]))
```

Latency SLO: 95% of gateway requests should complete under 500ms.

Error budget math:

```text
1000 requests/day * 7 days = 7000 requests/week
Availability error budget = 0.5% of 7000 = 35 failed requests/week
Latency budget = 5% of 7000 = 350 requests/week slower than 500ms
```

### Recording rules

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

Rules loaded:

```text
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

Availability before failure:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1789592535.695,"100"]}]}}
```

Forced failure SLO gauge observation:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1789593041.260,"96.07843137254902"]}]}}
```

Burn rate during forced failure:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"__name__":"gateway:error_budget_burn_rate:ratio_rate5m"},"value":[1789593041.300,"7.843137254901948"]}]}}
```

The SLO gauge dropped from 100% to about 96.08% during the controlled payments outage. The burn-rate rule reported about 7.84, meaning the service was consuming the 99.5% availability error budget too quickly.

## Bonus Task - Correlate Failure Across Metrics & Logs

Failure injection time:

```text
2026-09-16T21:04:50Z
```

Bonus load generator result:

```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 120s
---
[20s] requests=69 success=64 fail=5 error_rate=7.2%
[40s] requests=134 success=117 fail=17 error_rate=12.6%
[60s] requests=196 success=168 fail=28 error_rate=14.2%
[80s] requests=250 success=211 fail=39 error_rate=15.6%
[100s] requests=305 success=257 fail=48 error_rate=15.7%
---
Done. total=366 success=306 fail=60 error_rate=16.3%
```

Prometheus error-rate query:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1789592781.788,"4.25531914893617"]}]}}
```

Gateway log excerpt:

```text
gateway-1  | 2026-09-16T21:06:10.932485345Z {"time":"2026-09-16 21:06:10,932","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 500 Internal Server Error\""}
gateway-1  | 2026-09-16T21:06:10.933066802Z INFO:     172.18.0.1:58390 - "POST /reserve/6c892073-0619-4abb-bcb4-009499c415c0/pay HTTP/1.1" 500 Internal Server Error
```

Payments log excerpt:

```text
payments-1  | 2026-09-16T21:04:56.201305369Z {"time":"2026-09-16 21:04:56,201","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for 91c4e0ce-acd1-4194-b4cc-bea712d159a9"}
payments-1  | 2026-09-16T21:04:57.204299205Z {"time":"2026-09-16 21:04:57,203","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 91c4e0ce-acd1-4194-b4cc-bea712d159a9"}
payments-1  | 2026-09-16T21:04:57.205983619Z INFO:     172.18.0.8:44544 - "POST /charge HTTP/1.1" 500 Internal Server Error
payments-1  | 2026-09-16T21:06:09.927876221Z {"time":"2026-09-16 21:06:09,927","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for 6c892073-0619-4abb-bcb4-009499c415c0"}
payments-1  | 2026-09-16T21:06:10.931735180Z {"time":"2026-09-16 21:06:10,931","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 6c892073-0619-4abb-bcb4-009499c415c0"}
payments-1  | 2026-09-16T21:06:10.932029596Z INFO:     172.18.0.8:51248 - "POST /charge HTTP/1.1" 500 Internal Server Error
```

Timeline:

| Time UTC | Observation |
|----------|-------------|
| 21:04:50 | Payments restarted with `PAYMENT_FAILURE_RATE=0.5` and `PAYMENT_LATENCY_MS=1000`. |
| 21:04:56 | Payments began injecting 1000ms latency. |
| 21:04:57 | First injected payment failure appeared in payments logs. |
| 21:06:10 | Gateway logged upstream `payments:8082/charge` returning HTTP 500 and returned HTTP 500 to the client. |
| During run | Load generator error rate climbed to 16.3%; Prometheus error-rate query showed 4.26% at the sampled instant. |

Root cause: the payments service was intentionally configured to add 1000ms latency and fail roughly 50% of charge attempts. The failure surfaced first in payments logs as injected failures, then in gateway logs as upstream `POST /charge` 500 responses, and finally in metrics/dashboard as increased error rate and degraded SLO availability.
