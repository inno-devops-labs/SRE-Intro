# Lab 3 Submission

## 1) docker compose ps output

```text
$ cd app && docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
NAME               IMAGE                     COMMAND                  SERVICE      CREATED          STATUS                   PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       13 minutes ago   Up 2 minutes             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      13 minutes ago   Up 2 minutes             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      2 seconds ago    Up Less than a second    0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     13 minutes ago   Up 13 minutes            0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     2 minutes ago    Up 2 minutes (healthy)   0.0.0.0:15432->5432/tcp, [::]:15432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   2 seconds ago    Up Less than a second    0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        2 minutes ago    Up 2 minutes (healthy)   0.0.0.0:16379->6379/tcp, [::]:16379->6379/tcp
```

## 2) Prometheus targets

```text
$ curl -s http://localhost:9090/api/v1/targets | python3 -c "
import sys, json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(f\"{t['labels']['job']:12} {t['health']:8} {t['scrapeUrl']}\")
"
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

## 3) Custom metrics list

```text
$ curl -s http://localhost:9090/api/v1/label/__name__/values | python3 -c "
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

## 4) Request-rate PromQL output

```text
$ ./loadgen/run.sh 5 20
$ curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))' http://localhost:9090/api/v1/query | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(r['data']['result'][0]['value'][1]) if r['data']['result'] else print('NO_DATA')
"
0.23286577777777778
```

## 5) Latency and saturation queries used in Grafana

Latency panel (p50, p95, p99):

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

Saturation panel:

```promql
events_db_pool_size
```

## 6) Dashboard observations: normal traffic vs payments failure

- Under normal traffic, the request rate and dependency health stayed stable and the app responded successfully.
- When the payments dependency is stopped, the gateway begins returning errors and the latency/error panels reflect the dependency failure.
- Restarting payments restores the error rate and latency back toward normal.

## 7) Which golden signal showed the failure first?

The earliest observable signal is the error-rate / dependency-health trend, followed very closely by the latency panel as requests begin timing out. In the live run, the failure becomes visible within the first 15–30 seconds after the payments dependency is stopped.

---

## Bonus / SLO notes

Availability SLI:

- availability = rate(non-5xx) / rate(total)
- SLO target = 99.5% over a 7-day window
- With ~1000 requests/day, weekly total ≈ 7000 requests
- Error budget = 0.5% × 7000 = 35 failed requests per week

Latency SLI:

- target = 95% of requests under 500 ms
- this is tracked separately from availability and uses the histogram bucket ratio for requests under 0.5s

