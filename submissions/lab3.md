# Lab 3 — Monitoring, Observability & SLOs

## Task 1 — Configure Monitoring & Build Dashboard

### Output of compose ps showing all 7 services

```sh
[ustkost@prime app]$ dc ps
NAME               IMAGE                     COMMAND                  SERVICE      CREATED          STATUS                    PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       18 minutes ago   Up 18 minutes             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      18 minutes ago   Up 18 minutes             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      18 minutes ago   Up 18 minutes             0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     18 minutes ago   Up 18 minutes             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     18 minutes ago   Up 18 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   18 minutes ago   Up 18 minutes             0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        18 minutes ago   Up 18 minutes (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
[ustkost@prime app]$
```

### Prometheus targets output

```sh
[ustkost@prime app]$ curl -s http://localhost:9090/api/v1/targets | python3 -c "
import sys, json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(f\"{t['labels']['job']:12} {t['health']:8} {t['scrapeUrl']}\")
"
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     down     http://payments:8082/metrics
```

### Custom metrics list

```sh
[ustkost@prime app]$ curl -s http://localhost:9090/api/v1/label/__name__/values | python3 -c "
import sys, json
for n in json.load(sys.stdin)['data']:
    if any(x in n for x in ['gateway_', 'events_', 'payments_']):
        print(n)
"
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

### PromQL query output (request rate)

```sh
[ustkost@prime app]$ curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))'   http://localhost:9090/api/v1/query | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(f\"Request rate: {float(r['data']['result'][0]['value'][1]):.2f} req/s\")"
Request rate: 0.91 req/s
[ustkost@prime app]$ 
```

### PromQL queries for Latency and Saturation

```sh
"histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
events_db_pool_size
```

### Dashboard observations
Different metrics change with different speeds, there is a delay between the actual event happening and the metrics displaying it. The latency panel also shows that when requests fail, they get the responses almost immediately.

### Which golden signal showed the failure first? How long after killing payments?
Service Health (up/down) metric almost immediately (in seconds) showed the failure (payments 0)

## Task 2 — SLOs + Recording Rules

### SLI/SLO definitions with error budget math

- SLI 1 - availability - percent of gateway requests that return not 5xx
  SLO - 99.5% in a week
  Error budget = \(1 - 0.995 = 0.005\) = 0.5% of requests.

- SLI 2 - latency - percent of gateway requests that complete under 500ms
  SLO: 95%  

If we get approximately 1000 requests a day (7000 a week), we allow 0.5% failed requests (35) and 5% of the requests (350) can take over 500ms to complete.

### Rules loaded output

```sh
[ustkost@prime app]$ curl -s http://localhost:9090/api/v1/rules | python3 -c "
import sys, json
for g in json.load(sys.stdin)['data']['groups']:
    for r in g['rules']:
        print(f\"{r['name']:45} = {r.get('health', 'N/A')}\")
"
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

### SLO gauge observation during failure
When payments were off the gauge dropped below the 99.5 threshold and since the budget burt too quickly the burn rate rose above 1
