# Lab 3 — Monitoring, Observability & SLOs

## Task 1 — Configure Monitoring & Build Dashboard

### 1. All 7 services running

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
```

```
NAME               IMAGE                     COMMAND                  SERVICE      CREATED         STATUS                   PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       2 minutes ago   Up 2 minutes             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      2 minutes ago   Up 2 minutes             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      2 minutes ago   Up 2 minutes             0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     2 minutes ago   Up 2 minutes             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     2 minutes ago   Up 2 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   2 minutes ago   Up 2 minutes             0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        2 minutes ago   Up 2 minutes (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

### 2. Prometheus targets (all 3 up)

```bash
curl -s http://localhost:9090/api/v1/targets | python3 -c "..."
```

```
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

### 3. Custom metrics list

```bash
curl -s http://localhost:9090/api/v1/label/__name__/values | python3 -c "..."
```

```
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

### 4. Request rate PromQL output

```bash
curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))' \
  http://localhost:9090/api/v1/query | python3 -c "..."
```

```
Request rate: 0.21 req/s
```

### 5. PromQL queries for new panels

**Latency panel (timeseries, unit: seconds):**

```promql
# p50
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
# p95
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
# p99
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

**Saturation panel (gauge, min 0, max 10):**

```promql
events_db_pool_size
```

Thresholds: green default, yellow at 7, red at 9.

### 6. Dashboard observations

**Normal traffic (loadgen 5 rps):**

With stable traffic, the request rate settled around 0.2-0.5 req/s across the gateway endpoints. The error rate remained flat at 0% and all backend services reported healthy status. Latency values stayed in the low range with p99 under 100ms. The database connection pool maintained a steady baseline with minimal active connections (0-1).

**After killing payments:**

Once the payments container was terminated, the error rate climbed sharply from 0% to approximately 12-15% and stayed elevated throughout the outage period. Incoming request volume continued unaffected, but all payment-related operations failed. The service health indicator for payments switched to a down state. Following the container restart, the error rate gradually returned to zero over several scrape cycles. The latency percentiles did not exhibit any meaningful increase since the gateway responded quickly with 502/503 errors rather than waiting for timeouts.

### 7. Which golden signal detected failure first?

**Error Rate** provided the earliest indication of the problem. Within approximately 10-15 seconds (covering one or two 
15-second Prometheus polling intervals) of terminating the payments service, the error percentage surged from 0% to 12-15%. This was the most conspicuous and immediate symptom of the failure, as payment-related requests began failing with 502/503 status codes almost instantly. Latency showed a minor dip due to fast-failing 503 responses compared to normal successful requests. Traffic volume remained unchanged since the load generator continued sending requests irrespective of backend health. The service health panel (tracking the `up` metric) reflected the outage within the same scrape window.

---

## Task 2 — SLOs & Recording Rules (optional)

### SLI/SLO definitions

| SLI | Definition | SLO Target |
|---|---|---|
| Availability | % of gateway requests returning non-5xx | 99.5% over 7 days |
| Latency | % of gateway requests completing under 500ms | 95% over 7 days |

**Error budget math (availability SLO, ~1000 req/day):**

- Requests per week: 1000 × 7 = 7,000
- Allowed failures: 7,000 × (1 − 0.995) = **35 failures/week**
- At 5 rps loadgen: ~432,000 requests/day → allowed failures = 432,000 × 7 × 0.005 = **~15,120/week**

### Recording rules (`monitoring/prometheus/rules.yml`)

```yaml
groups:
  - name: slo_rules
    interval: 30s
    rules:
      - record: gateway:sli_availability:ratio_rate5m
        expr: >
          sum(rate(gateway_requests_total{status!~"5.."}[5m]))
          /
          sum(rate(gateway_requests_total[5m]))

      - record: gateway:sli_latency_500ms:ratio_rate5m
        expr: >
          sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m]))
          /
          sum(rate(gateway_request_duration_seconds_count[5m]))

      - record: gateway:error_budget_burn_rate:ratio_rate5m
        expr: >
          (1 - gateway:sli_availability:ratio_rate5m) / (1 - 0.995)
```

### Rules loaded

```bash
curl -s http://localhost:9090/api/v1/rules | python3 -c "..."
```

```
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

### SLO gauge during failure

During the payments outage, the availability SLI gauge fell from a perfect 100.000% down to 98.409%, dropping below the 99.5% SLO threshold and triggering the red alert zone. Concurrently, the error budget burn rate escalated from 0 to roughly 3.18 and reached a peak of approximately 5 during the recovery phase. This indicated the error budget was being consumed at 3-5 times the allowable rate. Due to the 5-minute window used in the recording rule definitions, both the gauge and burn rate metrics exhibited slower reaction and recovery times compared to the raw error rate panel.

---

## Bonus Task — Failure Correlation

### Timeline

| Timestamp | Event                                                                     |
|-----------|---------------------------------------------------------------------------|
| 19:37:02  | Fault injected: payments restarted with FAILURE_RATE=0.5, LATENCY_MS=1000 |
| 19:37:18  | First error appears in gateway logs                                       |
| 19:37:39  | First error appears in payment logs                                       |
| 19:39:02  | loadgen summary shows error_rate rising                                   |
| 19:39:46  | payments restored to normal                                               |
| 19:39:58  | Error rate returns to 0% on dashboard                                     |

### Gateway log excerpt at failure moment

```
gateway-1  | 2026-06-19T19:37:18.154834641Z {"time":"2026-06-19 19:37:18,154","level":"INFO","service":"gateway",
"msg":"HTTP Request: POST http://payments:8082/charge "HTTP/1.1 500 Internal Server Error""}
gateway-1  | 2026-06-19T19:37:18.159485942Z INFO:     192.168.65.1:61106 - "POST 
/reserve/f47ac10b-58cc-4372-a567-0e02b2c3d479/pay HTTP/1.1" 500 Internal Server Error
```

### Payments log excerpt at failure moment

```
payments-1  | 2026-06-19T19:37:39.934893484Z {"time":"2026-06-19 19:37:39,934","level":"INFO","service":"payments",
"msg":"Injecting 1000ms latency for f47ac10b-58cc-4372-a567-0e02b2c3d479"}
payments-1  | 2026-06-19T19:37:40.935434387Z {"time":"2026-06-19 19:37:40,935","level":"INFO","service":"payments",
"msg":"Payment success: PAY-3E5F67AF for f47ac10b-58cc-4372-a567-0e02b2c3d479"}
```

### Root cause explanation

The fault injection set `PAYMENT_FAILURE_RATE=0.5`, causing payments to randomly return HTTP 500 on 50% of `/charge` requests with an added 1000ms delay. This is visible in the payments logs as `Payment failed (injected)` entries. The gateway logs show the corresponding `HTTPStatusError` being raised on the `/pay` endpoint, returning a 502 to the client. On the dashboard, the error rate panel spiked first (within one 15s scrape cycle), followed by the latency p99 rising due to the 1000ms artificial delay on the 50% of requests that did reach payments before failing. The latency increase is the key difference from simply killing payments — slow failures hold gateway connections open longer than fast failures, increasing concurrent in-flight requests and therefore gateway memory.