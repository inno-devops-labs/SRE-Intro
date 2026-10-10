# Lab 3 Submission

## Task 1

**7 services up**

```
NAME               IMAGE                     SERVICE      STATUS
app-events-1       app-events                events       Up (healthy)
app-gateway-1      app-gateway               gateway      Up
app-grafana-1      grafana/grafana:13.0.1    grafana      Up
app-payments-1     app-payments              payments     Up
app-postgres-1     postgres:17-alpine        postgres     Up (healthy)
app-prometheus-1   prom/prometheus:v3.11.2   prometheus   Up
app-redis-1        redis:7-alpine            redis        Up (healthy)
```

**Prometheus targets, all 3 up**

```
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

**Custom metrics list**
```
user@MacBook-Air app % curl -s http://localhost:9090/api/v1/label/__name__/values | python3 -c "
import sys, json
for n in json.load(sys.stdin)['data']:
    if any(x in n for x in ['gateway_', 'events_', 'payments_']):
        print(n)
"
events_db_pool_size
events_orders_created
events_orders_total
events_reservations_active
user@MacBook-Air app % 
```

**Request rate**

```
Request rate: 0.10 req/s
```

**Latency panel queries**

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le))
```

**Saturation panel query**

```promql
events_db_pool_size
```

Gauge, min 0, max 10, yellow at 7, red at 9

**Normal vs payments failure**

user@MacBook-Air app % ./loadgen/run.sh 5 60
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 60s
---
[10s] requests=38 success=38 fail=0 error_rate=0%
[10s] requests=39 success=39 fail=0 error_rate=0%
[10s] requests=40 success=40 fail=0 error_rate=0%
[20s] requests=78 success=78 fail=0 error_rate=0%
[20s] requests=79 success=79 fail=0 error_rate=0%
[20s] requests=80 success=80 fail=0 error_rate=0%
[20s] requests=81 success=81 fail=0 error_rate=0%
[30s] requests=118 success=116 fail=2 error_rate=1.6%
[30s] requests=119 success=117 fail=2 error_rate=1.6%
[30s] requests=120 success=118 fail=2 error_rate=1.6%
[30s] requests=121 success=119 fail=2 error_rate=1.6%
[40s] requests=158 success=153 fail=5 error_rate=3.1%
[40s] requests=159 success=153 fail=6 error_rate=3.7%
[40s] requests=160 success=154 fail=6 error_rate=3.7%
[40s] requests=161 success=155 fail=6 error_rate=3.7%
[50s] requests=198 success=184 fail=14 error_rate=7.0%
[50s] requests=199 success=185 fail=14 error_rate=7.0%
[50s] requests=200 success=186 fail=14 error_rate=7.0%
[50s] requests=201 success=187 fail=14 error_rate=6.9%
---
Done. total=237 success=218 fail=19 error_rate=8.0%
user@MacBook-Air app % 

**Which signal showed failure first**

In the load test output, errors started appearing at the 30‑second mark (error_rate=1.6%), while the 20‑second interval had zero errors. This makes error rate the most sensitive golden signal for detecting a service outage, it rises immediately when payments stops responding, before latency increases due to timeouts
## Task 2

**SLO**: 99.5% success over 5min window

**SLI**

```promql
sum(rate(gateway_requests_total{status!~"5.."}[5m])) / sum(rate(gateway_requests_total[5m]))
```

Error budget = 0.5% (0.005)

**Burn rate**

```promql
(1 - SLI) / (1 - 0.995)
```

Above 1 means burning budget too fast

**Recording rules, all health ok**

```
gateway:sli_availability:ratio_rate5m
gateway:sli_latency_500ms:ratio_rate5m
gateway:error_budget_burn_rate:ratio_rate5m
```

**SLO gauge**

Turned yellow/red and dropped below 99.5 during the failure, rules and panel work fine
