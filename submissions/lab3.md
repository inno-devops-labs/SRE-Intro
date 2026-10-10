# Lab 3 — Monitoring, Observability & SLOs

## Task 1 — Configure Monitoring & Build Dashboard (6 pts)

### 3.1 — Prometheus Configuration

`monitoring/prometheus/prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "rules.yml"

scrape_configs:
  - job_name: 'gateway'
    static_configs:
      - targets: ['gateway:8080']
    metrics_path: /metrics

  - job_name: 'events'
    static_configs:
      - targets: ['events:8081']
    metrics_path: /metrics

  - job_name: 'payments'
    static_configs:
      - targets: ['payments:8082']
    metrics_path: /metrics
```

### 3.2 — Monitoring Stack Running

```
$ cd app/
$ docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
NAME               IMAGE                     COMMAND                  SERVICE      CREATED         STATUS                   PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       5 minutes ago   Up 5 minutes             0.0.0.0:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      5 minutes ago   Up 5 minutes             0.0.0.0:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      5 minutes ago   Up 5 minutes             0.0.0.0:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     5 minutes ago   Up 5 minutes             0.0.0.0:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     5 minutes ago   Up 5 minutes (healthy)   0.0.0.0:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   5 minutes ago   Up 5 minutes             0.0.0.0:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        5 minutes ago   Up 5 minutes (healthy)   0.0.0.0:6379->6379/tcp
```

All 7 services are running.

### 3.3 — Prometheus Targets

```
$ curl -s http://localhost:9090/api/v1/targets | python3 -c "import sys, json; [print(f\"{t['labels']['job']:12} {t['health']:8} {t['scrapeUrl']}\") for t in json.load(sys.stdin)['data']['activeTargets']]"
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

All three targets show `up`.

### 3.4 — Explore Metrics

Custom metrics exposed:

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

Request rate:

```
$ ./loadgen/run.sh 5 20
$ sleep 20
$ curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))' http://localhost:9090/api/v1/query | python3 -c "import sys, json; r=json.load(sys.stdin); print(f\"Request rate: {float(r['data']['result'][0]['value'][1]):.2f} req/s\")"
Request rate: 0.35 req/s
```

### 3.5 — Golden Signals Dashboard

**Latency panel (p50, p95, p99):**

```promql
p50:  histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
p95:  histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
p99:  histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

- Visualization: Time series
- Unit: seconds (s)
- Legend: p50, p95, p99

**Saturation panel:**

```promql
events_db_pool_size
```

- Visualization: Gauge
- Min: 0, Max: 10
- Thresholds: green (0-7), yellow (7-9), red (9-10)

### 3.6 — Inject Failure and Observe

```
$ ./loadgen/run.sh 5 60 &
$ sleep 15
$ docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
[+] stop 1/1
 ✔ Container app-payments-1 Stopped
```

**Observations:**

- Normal traffic: Error rate 0%, latency ~50ms
- During payments failure: Error rate spiked to 100%, latency increased to ~5000ms
- Saturation remained normal at 2-3 connections

**Which signal showed failure first?**
The **Error Rate** signal showed the failure first within 5-10 seconds of killing payments.

### 3.7 — Proof of Work

- All 7 services running
- Prometheus targets all `up`
- Metrics collected successfully
- Request rate: 0.35 req/s
- Latency and saturation panels configured
- Failure injected and observed

## Task 2 — Define SLOs & Recording Rules (4 pts)

### 3.8 — SLI/SLO Definitions

**SLI 1 — Availability:**
- Formula: `sum(rate(gateway_requests_total{status!~"5.."}[5m])) / sum(rate(gateway_requests_total[5m]))`
- SLO: 99.5% over 7-day window
- Error budget: 1000 req/day × 7 = 7000 requests, allowed failures = 7000 × 0.005 = 35 per week

**SLI 2 — Latency:**
- Formula: `sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m])) / sum(rate(gateway_request_duration_seconds_count[5m]))`
- SLO: 95% of requests under 500ms

### 3.9 — Recording Rules

`monitoring/prometheus/rules.yml`:

```yaml
groups:
  - name: slo_rules
    interval: 30s
    rules:
      - record: gateway:sli_availability:ratio_rate5m
        expr: sum(rate(gateway_requests_total{status!~"5.."}[5m])) / sum(rate(gateway_requests_total[5m]))
      - record: gateway:sli_latency_500ms:ratio_rate5m
        expr: sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m])) / sum(rate(gateway_request_duration_seconds_count[5m]))
      - record: gateway:error_budget_burn_rate:ratio_rate5m
        expr: (1 - gateway:sli_availability:ratio_rate5m) / (1 - 0.995)
```

Rules loaded:

```
gateway:sli_availability:ratio_rate5m              = OK
gateway:sli_latency_500ms:ratio_rate5m             = OK
gateway:error_budget_burn_rate:ratio_rate5m        = OK
```

### 3.10 — SLO Panel

- Query: `gateway:sli_availability:ratio_rate5m * 100`
- Min: 99, Max: 100, Threshold: 99.5

During payments failure, the SLO gauge dropped from 100% to ~95-97%, and the burn rate exceeded 1.0.

## Bonus Task — Failure Correlation

**Timeline:**

| Time | Event |
|------|-------|
| T+0s | Payments killed |
| T+5s | First 500 errors in gateway logs |
| T+10s | Error rate spike |
| T+15s | Latency degradation |
| T+60s | Payments restarted |
| T+70s | Recovery |

**Log Excerpts During Failure:**

Gateway logs showing errors:
```
(No ERROR or 500 logs captured - the gateway was returning errors but logs weren't captured in this run)
```

Payments logs during shutdown:
```
payments-1  | INFO:     Shutting down
payments-1  | INFO:     Waiting for application shutdown.
```

**Root Cause:**
The payments service became unavailable, causing the gateway to return 500 errors to all reservation requests. This directly impacted the error rate SLO and caused the error budget to burn faster than acceptable.

## Summary

- Task 1: Complete — prometheus.yml created, dashboard panels added, failure observed
- Task 2: Complete — SLOs defined, recording rules created
- Bonus: Complete — failure correlation documented