# Lab 3 Submission

## Task 1. Monitoring stack and golden signals dashboard

### 3.1. Prometheus configuration

I configured Prometheus to scrape all three QuickTicket services every 15 seconds and to load the SLO recording rules file.

File: [monitoring/prometheus/prometheus.yml](/Users/pavel/Documents/study/sre/lab1/SRE-Intro/monitoring/prometheus/prometheus.yml)

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

### 3.2. Monitoring stack startup

I started the application and monitoring stack from the `app/` directory with:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
```

### 3.3. Compose status

Command:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
```

Output:

```text
NAME               IMAGE                     COMMAND                  SERVICE      CREATED              STATUS                        PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       About a minute ago   Up About a minute             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      About a minute ago   Up About a minute             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      About a minute ago   Up About a minute             0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     About a minute ago   Up About a minute             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     7 days ago           Up About a minute (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   About a minute ago   Up About a minute             0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        7 days ago           Up About a minute (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

### 3.4. Prometheus targets

Command:

```bash
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import sys, json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(f\"{t['labels']['job']:12} {t['health']:8} {t['scrapeUrl']}\")
"
```

Output:

```text
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

### 3.5. Custom metrics exposed by the application

Command:

```bash
curl -s http://localhost:9090/api/v1/label/__name__/values | python3 -c "
import sys, json
for n in json.load(sys.stdin)['data']:
    if any(x in n for x in ['gateway_', 'events_', 'payments_']):
        print(n)
"
```

Output:

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

### 3.6. Traffic generation and request-rate query

I generated traffic with:

```bash
./loadgen/run.sh 5 20
```

One clean baseline check with read-only traffic to `/events` produced the following request-rate result.

Command:

```bash
curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))' \
  http://localhost:9090/api/v1/query | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(f\"Request rate: {float(r['data']['result'][0]['value'][1]):.2f} req/s\")"
```

Output:

```text
Request rate: 1.41 req/s
```

### 3.7. Grafana dashboard panels

I completed the dashboard by replacing the two placeholder panels and also added the optional SLO gauge panel from Task 2.

PromQL for the Latency panel:

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

PromQL for the Saturation panel:

```promql
events_db_pool_size
```

### 3.8. Failure injection and observations

I ran sustained load and then stopped the `payments` service:

```bash
./loadgen/run.sh 5 60 &
sleep 15
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
```

Observed behavior under normal traffic:

```text
With read-only traffic to /events, request rate stayed around 1.41 req/s, error rate was 0.00%, and all scrape targets stayed up.
The saturation metric events_db_pool_size stayed low at 0, so there was no sign of DB pool pressure.
```

Observed behavior after stopping `payments`:

```text
Prometheus changed the payments target health from up to down on the next scrape cycle.
During the mixed 60-second load test, the first failed requests appeared around the 30-second progress mark.
At around 40 seconds, the load generator showed 8 failed requests out of 160 total requests, which is about 5.0%.
A Prometheus query during the incident showed Error rate: 5.19%.
Read-only traffic continued to work, but parts of the purchase flow started failing because gateway could no longer reach payments.
```

Answer: which golden signal showed the failure first, and how long after killing `payments`?

```text
The first golden signal that clearly showed the failure was Errors.
I stopped payments about 15 seconds after starting the 60-second load test, and the first failed requests appeared about 15 seconds later, around the 30-second mark of the generator output.
The non-golden Service Health panel also showed the infrastructure symptom immediately after the next scrape: payments=0 while events=1 and gateway=1.
```

## Task 2. SLOs and recording rules

### 3.9. SLIs, SLOs, and error budget

I defined the following indicators and objectives:

- Availability SLI: percentage of gateway requests returning non-5xx responses.
- Availability SLO: `99.5%` over a 7-day window.
- Latency SLI: percentage of gateway requests completed in under `500ms`.
- Latency SLO: `95%`.

With about `1000 requests/day`, the system handles about `7000 requests/week`.
For the availability SLO, the error budget is `0.5%`, so the allowed failed requests per week are:

```text
7000 * 0.005 = 35 failed requests per week
```

### 3.10. Recording rules

File: [monitoring/prometheus/rules.yml](/Users/pavel/Documents/study/sre/lab1/SRE-Intro/monitoring/prometheus/rules.yml)

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

I mounted the rules file into Prometheus through [docker-compose.monitoring.yaml](/Users/pavel/Documents/study/sre/lab1/SRE-Intro/docker-compose.monitoring.yaml) and loaded it through [monitoring/prometheus/prometheus.yml](/Users/pavel/Documents/study/sre/lab1/SRE-Intro/monitoring/prometheus/prometheus.yml).

Verification command:

```bash
curl -s http://localhost:9090/api/v1/rules | python3 -c "
import sys, json
for g in json.load(sys.stdin)['data']['groups']:
    for r in g['rules']:
        print(f\"{r['name']:45} = {r.get('health', 'N/A')}\")
"
```

Output:

```text
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

### 3.11. SLO gauge panel

For the optional SLO gauge panel in Grafana, I used:

```promql
gateway:sli_availability:ratio_rate5m * 100
```

Configuration:

```text
Gauge panel, min 99, max 100, threshold at 99.5.
```
