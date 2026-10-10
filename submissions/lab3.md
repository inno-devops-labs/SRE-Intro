# Lab 3 Submission - Monitoring, Observability & SLOs

## Task 1 - Monitoring and Golden Signals Dashboard

### 1. Compose ps output

```text
NAME               IMAGE                     COMMAND                  SERVICE      CREATED              STATUS                    PORTS
app-events-1       app-events                "uvicorn main:app --..." events       32 minutes ago       Up 32 minutes             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --..." gateway      32 minutes ago       Up 32 minutes             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      32 minutes ago       Up 32 minutes             0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --..." payments     About a minute ago   Up About a minute         0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s..." postgres     32 minutes ago       Up 32 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c..." prometheus   32 minutes ago       Up 32 minutes             0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s..." redis        32 minutes ago       Up 32 minutes (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

### 2. Prometheus targets

```text
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

### 3. Custom metrics list

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

### 4. PromQL request rate output

Traffic generated with:

```bash
./app/loadgen/run.sh 5 60
```

Loadgen output:

```text
Done. total=217 success=215 fail=2 error_rate=.9%
```

Prometheus query:

```promql
sum(rate(gateway_requests_total[5m]))
```

Output:

```text
Request rate: 0.81 req/s
```

### 5. Dashboard panel PromQL

Latency panel:

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

Saturation panel:

```promql
events_db_pool_size
```

SLO gauge panel:

```promql
gateway:sli_availability:ratio_rate5m * 100
```

Grafana API confirmed these panels are provisioned:

```text
Request Rate (Traffic)
Error Rate
Service Health (up/down)
Latency (p50 / p95 / p99)
DB Pool Saturation
Availability SLO
```

### 6. Dashboard observations

Normal traffic:

```text
Request rate: 0.81 req/s
Availability SLI: 100.00%
Latency <500ms SLI: 99.57%
Burn rate: 0.00
```

Payments failure injection:

```text
Stopped payments at: 2026-06-12T17:44:50+03:00
Immediate/next-scrape observation:
payments up: 0
gateway 5xx error rate: 1.03%
availability SLO gauge: 100.00%

~30s after stop:
payments up: 0
gateway 5xx error rate: 3.78%
availability SLO gauge: 98.61%
gateway latency p95: 0.023s

~60s after stop:
gateway 5xx error rate: 0.00%
availability SLO gauge: 97.76%

After recovery:
payments up: 1
availability SLO gauge: 97.80%
```

Answer: Service Health (`up{job="payments"}`) showed the failure first. It flipped to `0` within one Prometheus scrape interval, approximately 0-15 seconds after stopping `payments`. Error Rate followed once gateway `/pay` requests failed and were scraped.

## Task 2 - SLOs and Recording Rules

### SLI/SLO definitions

Availability SLI:

```promql
sum(rate(gateway_requests_total{status!~"5.."}[5m]))
/
sum(rate(gateway_requests_total[5m]))
```

SLO: 99.5% availability over 7 days.

Latency SLI:

```promql
sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m]))
/
sum(rate(gateway_request_duration_seconds_count[5m]))
```

SLO: 95% of gateway requests under 500ms.

Error budget math with ~1000 requests/day:

```text
7 days * 1000 requests/day = 7000 requests/week
Availability error budget: 0.5% of 7000 = 35 failed 5xx requests/week
Latency budget: 5% of 7000 = 350 requests slower than 500ms/week
```

### Recording rules loaded output

```text
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

### SLO gauge observation during failure

During the `payments` stop incident, the SLO gauge dropped from `100.00%` to `98.61%` after about 30 seconds, then to `97.76%` after about a minute. This crossed below the 99.5% threshold and demonstrated error budget burn.

## Bonus Task - Correlate Failure Across Metrics and Logs

### Timeline

Container logs are in UTC. Local timestamps are Europe/Moscow, UTC+03:00.

```text
2026-06-12T17:48:52+03:00 - Restarted payments with PAYMENT_FAILURE_RATE=0.5 and PAYMENT_LATENCY_MS=1000.
2026-06-12T17:49:56+03:00 - First transient gateway 502 while payments was being recreated.
2026-06-12T17:55:54+03:00 - Started targeted reserve/pay attempts against event 3.
2026-06-12T17:55:56+03:00 - First injected payments failure for reservation 1e34ad45-3105-4421-9687-c5e90038fcc1.
2026-06-12T17:55:56+03:00 - Gateway returned 500 for the same reservation.
2026-06-12T17:56:37+03:00 - Prometheus/Grafana spike observed after scrape: gateway 5xx 18.01%, gateway p95 2.344s, payments p95 2.425s.
2026-06-12T18:01:18+03:00 - payments verified recovered with failure_rate=0.0 and latency_ms=0.
```

Targeted traffic summary:

```text
2026-06-12T17:55:54+03:00 attempt=1 reservation=59e2f3bd-a983-4764-81ad-16811bd06546 pay_status=200
2026-06-12T17:55:56+03:00 attempt=2 reservation=1e34ad45-3105-4421-9687-c5e90038fcc1 pay_status=500
2026-06-12T17:55:58+03:00 attempt=3 reservation=b316fafb-7dd8-40d3-9734-2fa5bc7d7bd9 pay_status=200
2026-06-12T17:56:07+03:00 attempt=7 reservation=b00e8f9d-2db0-4876-b22e-769462befd31 pay_status=500
2026-06-12T17:56:13+03:00 attempt=10 reservation=c63b0c64-089c-4e3b-8aa7-301fdd240a1e pay_status=500
2026-06-12T17:56:15+03:00 attempt=11 reservation=460f4a28-d68b-45d7-a29b-b5c286826ca7 pay_status=500
2026-06-12T17:56:17+03:00 attempt=12 reservation=fc704c92-4889-45e9-8992-073315d19bf1 pay_status=500
```

Metric spike:

```text
gateway 5xx error rate 5m: 18.01%
gateway latency p95 5m: 2.344s
payments injected failure rate 5m: 0.01/s
payments latency p95 5m: 2.425s
```

### Log excerpts

Payments logs:

```text
payments-1 | {"time":"2026-06-12 14:55:55,316","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for 1e34ad45-3105-4421-9687-c5e90038fcc1"}
payments-1 | {"time":"2026-06-12 14:55:56,320","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 1e34ad45-3105-4421-9687-c5e90038fcc1"}
payments-1 | {"time":"2026-06-12 14:56:12,638","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for c63b0c64-089c-4e3b-8aa7-301fdd240a1e"}
payments-1 | {"time":"2026-06-12 14:56:13,644","level":"WARNING","service":"payments","msg":"Payment failed (injected) for c63b0c64-089c-4e3b-8aa7-301fdd240a1e"}
payments-1 | {"time":"2026-06-12 14:56:16,888","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for fc704c92-4889-45e9-8992-073315d19bf1"}
payments-1 | {"time":"2026-06-12 14:56:17,891","level":"WARNING","service":"payments","msg":"Payment failed (injected) for fc704c92-4889-45e9-8992-073315d19bf1"}
```

Gateway logs:

```text
gateway-1 | {"time":"2026-06-12 14:55:56,322","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 500 Internal Server Error\""}
gateway-1 | INFO:     151.101.0.223:17198 - "POST /reserve/1e34ad45-3105-4421-9687-c5e90038fcc1/pay HTTP/1.1" 500 Internal Server Error
gateway-1 | {"time":"2026-06-12 14:56:13,649","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 500 Internal Server Error\""}
gateway-1 | INFO:     151.101.0.223:54502 - "POST /reserve/c63b0c64-089c-4e3b-8aa7-301fdd240a1e/pay HTTP/1.1" 500 Internal Server Error
gateway-1 | {"time":"2026-06-12 14:56:17,903","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 500 Internal Server Error\""}
gateway-1 | INFO:     151.101.0.223:60745 - "POST /reserve/fc704c92-4889-45e9-8992-073315d19bf1/pay HTTP/1.1" 500 Internal Server Error
```

### Root cause

The root cause was the intentional fault configuration on `payments`: `PAYMENT_FAILURE_RATE=0.5` and `PAYMENT_LATENCY_MS=1000`. Payments added 1s latency to each charge attempt and randomly returned injected 500s. Gateway surfaced those payment failures as 500 responses on `/reserve/{reservation_id}/pay`, which produced the Grafana spike in gateway 5xx rate and higher p95 latency.

## PR Checklist

```text
- [x] Task 1 done - monitoring deployed, dashboard completed
- [x] Task 2 done - SLOs defined, recording rules created
- [x] Bonus Task done - failure correlation
```

## Acceptance Criteria Checklist

Task 1:

```text
- [x] prometheus.yml committed with 3 scrape targets
- [x] All 7 services running
- [x] Prometheus scraping all 3 targets
- [x] Latency panel added (p50/p95/p99)
- [x] Saturation panel added (DB pool gauge)
- [x] Failure observed + answered which signal detected first
```

Task 2:

```text
- [x] SLI/SLO definitions with error budget math
- [x] Recording rules loaded in Prometheus
- [x] SLO gauge showing drop during failure
```

Bonus Task:

```text
- [x] Timestamped failure timeline
- [x] Log excerpts correlating with metrics
- [x] Root cause explanation
```
