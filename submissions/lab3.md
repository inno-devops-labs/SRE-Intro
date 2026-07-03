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

Didn't save this output, need to rerun

```bash
curl -s http://localhost:9090/api/v1/label/__name__/values | grep -E 'gateway_|events_|payments_'
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

Lost this comparison after a restart, loadgen output I have:

```
[10s] requests=38 success=32 fail=6 error_rate=15.7%
[20s] requests=79 success=64 fail=15 error_rate=18.9%
[30s] requests=120 success=91 fail=29 error_rate=24.1%
[40s] requests=160 success=117 fail=43 error_rate=26.8%
[50s] requests=201 success=146 fail=55 error_rate=27.3%
Done. total=241 success=171 fail=70 error_rate=29.0%
```

Errors are already at 15.7% in the first 10s, before payments got stopped, so this run doesn't really show a clean before/after, need to redo with longer warmup

**Which signal showed failure first**

Can't say for sure from this run since there's no clean baseline. Need a rerun with proper timing to answer this one properly

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

Turned yellow/red and dropped below 99.5 during the failure, rules and panel work fine, didn't capture timestamps though