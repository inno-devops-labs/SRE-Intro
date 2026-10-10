## Task 1 — Configure Monitoring & Build Dashboard

### Prometheus Configuration

Created `monitoring/prometheus/prometheus.yml` with scrape targets for all three QuickTicket services:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "rules.yml"

scrape_configs:
  - job_name: gateway
    static_configs:
      - targets: ["gateway:8080"]

  - job_name: events
    static_configs:
      - targets: ["events:8081"]

  - job_name: payments
    static_configs:
      - targets: ["payments:8082"]
```

The targets use Docker Compose service names and internal container ports.

### Monitoring Stack

Command:

```bash
docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
```

Output of `docker-compose ps`:

```text
NAME               IMAGE                     COMMAND                  SERVICE      STATUS                   PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       Up 4 minutes             0.0.0.0:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      Up 4 minutes             0.0.0.0:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      Up 4 minutes             0.0.0.0:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     Up 41 seconds            0.0.0.0:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     Up 4 minutes (healthy)   0.0.0.0:15432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   Up 4 minutes             0.0.0.0:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        Up 4 minutes (healthy)   0.0.0.0:16379->6379/tcp
```

Note: Postgres and Redis were published on `15432` and `16379` on the host to avoid local port conflicts. Inside Docker Compose, services still use `postgres:5432` and `redis:6379`.

### Prometheus Targets

Command:

```bash
curl -s http://localhost:9090/api/v1/targets
```

Formatted output:

```text
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

### Raw Gateway Metrics

After generating traffic, gateway exposed request counters and histograms:

```text
gateway_requests_total{method="GET",path="/health",status="200"} 1.0
gateway_requests_total{method="GET",path="/events",status="200"} 60.0
gateway_requests_total{method="POST",path="/events/{id}/reserve",status="200"} 18.0
gateway_requests_total{method="POST",path="/reserve/{id}/pay",status="200"} 10.0
gateway_request_duration_seconds_bucket{le="0.005",method="GET",path="/health"} 0.0
gateway_request_duration_seconds_bucket{le="0.01",method="GET",path="/health"} 0.0
```

### Custom Metrics List

Custom metrics discovered in Prometheus:

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
gateway:error_budget_burn_rate:ratio_rate5m
gateway:sli_availability:ratio_rate5m
gateway:sli_latency_500ms:ratio_rate5m
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

### Load Generation

Command:

```bash
./loadgen/run.sh 5 20
```

Output:

```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 20s
---
[10s] requests=39 success=39 fail=0 error_rate=0%
---
Done. total=78 success=78 fail=0 error_rate=0%
```

PromQL request rate:

```promql
sum(rate(gateway_requests_total[5m]))
```

Output:

```text
Request rate: 1.22 req/s
```

### Dashboard Panels

The Grafana dashboard `QuickTicket — Golden Signals` was updated by editing `monitoring/grafana/dashboards/golden-signals.json`.

Latency panel queries:

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

Observed values after traffic:

```text
p50 latency: 0.0081s
p95 latency: 0.0212s
p99 latency: 0.0242s
```

Saturation panel query:

```promql
events_db_pool_size
```

Observed value:

```text
events_db_pool_size: 0
```

The saturation gauge uses min `0`, max `10`, yellow threshold at `7`, and red threshold at `9`.

### Failure Injection

Steady traffic was started, then `payments` was stopped.

Commands:

```bash
./loadgen/run.sh 5 60
docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
```

Failure injection time:

```text
2026-06-18T21:28:07+03:00
```

Prometheus target state during the incident:

```text
events       up
gateway      up
payments     down     Get "http://payments:8082/metrics": dial tcp: lookup payments on 127.0.0.11:53: server misbehaving
```

Gateway error rate during the incident:

```text
Gateway 5xx error rate: 7.16%
```

Load generator result:

```text
[30s] requests=122 success=121 fail=1 error_rate=.8%
[40s] requests=163 success=159 fail=4 error_rate=2.4%
[50s] requests=208 success=198 fail=10 error_rate=4.8%
---
Done. total=244 success=230 fail=14 error_rate=5.7%
```

Gateway log excerpt:

```text
INFO: 172.19.0.1:54998 - "POST /reserve/1d70d69d-671d-467b-ad0d-9b22b8301bee/pay HTTP/1.1" 503 Service Unavailable
INFO: 172.19.0.1:60782 - "POST /reserve/7d0f0c60-a58e-4780-969b-f7b494207061/pay HTTP/1.1" 503 Service Unavailable
INFO: 172.19.0.1:60802 - "POST /reserve/826e9202-5dc6-4327-8497-618e96f55aeb/pay HTTP/1.1" 503 Service Unavailable
```

### Failure Observation

During normal traffic:

```text
Gateway health: healthy
Loadgen: 78 total, 78 success, 0 failures
Request rate: 1.22 req/s
p95 latency: 0.0212s
events_db_pool_size: 0
```

During the payments failure:

```text
payments target: down
Gateway 5xx error rate: 7.16%
Loadgen final error rate: 5.7%
Availability SLI: 96.87%
Error budget burn rate: 6.27x
```

The first user-facing golden signal to show the failure was **Errors**. It appeared about **10-15 seconds after killing payments**, once the load generator hit the `/reserve/{id}/pay` path. The Prometheus `up` signal for `payments` also flipped to `down` within roughly one scrape interval.

---

## Task 2 — Define SLOs & Recording Rules

### SLI and SLO Definitions

Availability SLI:

```text
Percentage of gateway requests that do not return 5xx.
```

Availability SLO:

```text
99.5% over 7 days
```

Latency SLI:

```text
Percentage of gateway requests completed under 500ms.
```

Latency SLO:

```text
95% under 500ms
```

Error budget math for availability:

```text
1000 requests/day * 7 days = 7000 requests/week
Allowed failure ratio = 1 - 0.995 = 0.005
7000 * 0.005 = 35 failed requests/week
```

With about 1000 requests per day, the availability error budget allows **35 5xx responses per week**.

Latency budget math:

```text
Allowed slow ratio = 1 - 0.95 = 0.05
7000 * 0.05 = 350 slow requests/week
```

The latency SLO allows **350 requests per week** to take 500ms or longer.

### Recording Rules

Created `monitoring/prometheus/rules.yml`:

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

Added the rule file to `docker-compose.monitoring.yaml`:

```yaml
- ../monitoring/prometheus/rules.yml:/etc/prometheus/rules.yml:ro
```

Prometheus validation:

```text
Checking /etc/prometheus/prometheus.yml
  SUCCESS: 1 rule files found
 SUCCESS: /etc/prometheus/prometheus.yml is valid prometheus config file syntax

Checking /etc/prometheus/rules.yml
  SUCCESS: 3 rules found
```

Rules loaded output:

```text
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

### SLO Gauge

Added a Grafana gauge panel with this query:

```promql
gateway:sli_availability:ratio_rate5m * 100
```

Gauge configuration:

```text
Min: 99
Max: 100
Threshold: 99.5
```

During the payments failure, the SLO gauge dropped below the target:

```text
Availability SLI: 96.87%
Error budget burn rate: 6.27x
```

This means the service was burning the 99.5% availability error budget about 6.27 times faster than allowed during the incident window.

---

## Final Answers

### Which golden signal showed the failure first?

The **Errors** golden signal showed the first user-impacting failure. After `payments` was stopped, `/events` and reservation requests continued to work, but payment requests started returning `503 Service Unavailable`.

### How long after killing payments?

The first loadgen failure appeared roughly **10-15 seconds** after stopping `payments`, when traffic next exercised the payment path. Prometheus target health for `payments` showed `down` within about one scrape interval.

### Root Cause

The `payments` service was intentionally stopped. Gateway could still serve read and reservation paths through `events`, but full checkout requests that required `payments` failed with `503`, increasing the gateway 5xx error rate and dropping the availability SLI.