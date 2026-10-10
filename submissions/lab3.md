# Lab 3

## Task 1

I created the Prometheus config, started the app with the monitoring stack, and checked that Prometheus was scraping all three app services.

```bash
cd app
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
```

```text
NAME               IMAGE                     COMMAND                  SERVICE      CREATED          STATUS                    PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       17 seconds ago   Up 11 seconds             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      17 seconds ago   Up 10 seconds             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      48 minutes ago   Up 48 minutes             0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     18 seconds ago   Up 17 seconds             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     18 seconds ago   Up 17 seconds (healthy)   0.0.0.0:55432->5432/tcp, [::]:55432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   48 minutes ago   Up 22 minutes             0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        48 minutes ago   Up 48 minutes (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

Then I checked the Prometheus targets.

```bash
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import sys, json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(f\"{t['labels']['job']:12} {t['health']:8} {t['scrapeUrl']}\")
"
```

```text
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

I also checked the custom metrics in Prometheus.

```bash
curl -s http://localhost:9090/api/v1/label/__name__/values | python3 -c "
import sys, json
for n in json.load(sys.stdin)['data']:
    if any(x in n for x in ['gateway_', 'events_', 'payments_']):
        print(n)
"
```

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

After generating traffic, the gateway metrics appeared and the request rate query worked.

```bash
./loadgen/run.sh 5 25
curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))' \
  http://localhost:9090/api/v1/query
```

```text
Request rate: 0.18 req/s
```

I used these PromQL queries for the latency panel:

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

I used this PromQL query for the saturation panel:

```promql
events_db_pool_size
```

Then I generated traffic and stopped `payments`.

```bash
./loadgen/run.sh 5 60 &
sleep 15
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
```

The first signal I saw was service health. Prometheus scraped `payments` as down on the next scrape after I stopped it.

```text
stop payments: 20:43:36

20:43:21 1
20:43:36 1
20:43:51 0
20:44:06 0
20:44:21 0
```

So the first golden signal was service health, and it showed the failure about 15 seconds after killing `payments`.

## Task 2

I used these SLI and SLO definitions:

```text
Availability SLI = non-5xx gateway requests / all gateway requests
Availability SLO = 99.5% over 7 days

Latency SLI = gateway requests under 500 ms / all gateway requests
Latency SLO = 95%
```

With about 1000 requests per day, one week has about 7000 requests. A 99.5% availability SLO allows 0.5% errors, so the weekly error budget is:

```text
7000 * 0.005 = 35 failed requests per week
```

I added the recording rules and checked that Prometheus loaded them.

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml restart prometheus
curl -s http://localhost:9090/api/v1/rules | python3 -c "
import sys, json
for g in json.load(sys.stdin)['data']['groups']:
    for r in g['rules']:
        print(f\"{r['name']:45} = {r.get('health', 'N/A')}\")
"
```

```text
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

I also added the SLO gauge panel with this query:

```promql
gateway:sli_availability:ratio_rate5m * 100
```

During the `payments` failure, the SLO gauge dropped below the 99.5% target and the burn rate became higher than 1.

```text
20:43:55 100.00%
20:44:55 100.00%
20:45:55 97.20%
20:46:55 97.34%
20:47:55 96.68%
20:48:55 96.75%
20:49:55 96.80%
20:50:25 95.77%
```

```text
20:43:55 0.00
20:44:55 0.00
20:45:55 5.61
20:46:55 5.32
20:47:55 6.65
20:48:55 6.49
20:49:55 6.41
20:50:25 8.45
```

## Bonus Task

I started traffic, then restarted `payments` with fault injection.

```bash
./loadgen/run.sh 5 75 &
PAYMENT_FAILURE_RATE=0.5 PAYMENT_LATENCY_MS=1000 \
  docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --force-recreate payments
```

I checked that the injected settings were active.

```bash
curl -s http://localhost:8082/health
```

```json
{"status":"healthy","failure_rate":0.5,"latency_ms":1000}
```

The load generator showed more failed requests after the injection.

```text
[20s] requests=42 success=41 fail=1 error_rate=2.3%
[30s] requests=63 success=58 fail=5 error_rate=7.9%
[40s] requests=84 success=74 fail=10 error_rate=11.9%
[50s] requests=104 success=86 fail=18 error_rate=17.3%
[60s] requests=125 success=102 fail=23 error_rate=18.4%
[70s] requests=145 success=114 fail=31 error_rate=21.3%
Done. total=153 success=120 fail=33 error_rate=21.5%
```

For exact log correlation, I also made direct purchase requests while the same fault injection was active.

```text
inject: 20:52:56
first latency log: 20:53:03
first injected payment failure: 20:53:06
first gateway 500 from payments: 20:53:06
recovery: 20:53:22
```

Here are the relevant log lines.

```text
payments-1  | {"time":"2026-06-19 20:53:05,505","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for f45c5ca7-4423-43f5-927d-11ad42b7ec02"}
payments-1  | {"time":"2026-06-19 20:53:06,505","level":"WARNING","service":"payments","msg":"Payment failed (injected) for f45c5ca7-4423-43f5-927d-11ad42b7ec02"}
gateway-1   | {"time":"2026-06-19 20:53:06,509","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 500 Internal Server Error\""}
gateway-1   | INFO:     172.28.0.1:50762 - "POST /reserve/f45c5ca7-4423-43f5-927d-11ad42b7ec02/pay HTTP/1.1" 500 Internal Server Error
```

More failures followed the same pattern.

```text
payments-1  | {"time":"2026-06-19 20:53:15,897","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 673a2554-bbc1-4707-a16b-419146ceb3c5"}
gateway-1   | {"time":"2026-06-19 20:53:15,900","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 500 Internal Server Error\""}
gateway-1   | INFO:     172.28.0.1:47624 - "POST /reserve/673a2554-bbc1-4707-a16b-419146ceb3c5/pay HTTP/1.1" 500 Internal Server Error
```

The root cause was the injected payment behavior. The `payments` service added 1000 ms of latency before each charge and randomly failed charges with `PAYMENT_FAILURE_RATE=0.5`. The gateway logs show the same reservation ids returning `500` from `payments`, so the dashboard spike comes from the payment fault injection.
