# Lab 3 — Observability, Monitoring, and SLOs

## 1. Monitoring stack status

I started the main QuickTicket application together with the monitoring stack using Docker Compose. The stack includes the application services, infrastructure dependencies, Prometheus, and Grafana.

Command:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
```

Output:

```text
NAME               IMAGE                     COMMAND                  SERVICE      CREATED          STATUS                    PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       27 seconds ago   Up 21 seconds             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      27 seconds ago   Up 20 seconds             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      2 minutes ago    Up 2 minutes              0.0.0.0:3000->3000/tcp, [::]:3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     27 seconds ago   Up 26 seconds             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     2 minutes ago    Up 2 minutes (healthy)    0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   2 minutes ago    Up 2 minutes              0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        2 minutes ago    Up 26 seconds (healthy)
```

All 7 required services are running:

```text
gateway
events
payments
postgres
redis
prometheus
grafana
```

This confirms that the application and the monitoring infrastructure were started successfully.

---

## 2. Prometheus targets

After starting the stack, I checked that Prometheus was able to scrape metrics from the application services.

Command:

```bash
curl http://localhost:9090/api/v1/targets | jq
```

Relevant output:

```text
events:
  scrapeUrl: http://events:8081/metrics
  health: up

gateway:
  scrapeUrl: http://gateway:8080/metrics
  health: up

payments:
  scrapeUrl: http://payments:8082/metrics
  health: up
```

Prometheus successfully discovered and scraped all three application targets: `gateway`, `events`, and `payments`.

I also checked the built-in `up` metric:

```bash
curl "http://localhost:9090/api/v1/query?query=up" | jq
```

Output:

```text
gateway:  1
events:   1
payments: 1
```

The value `1` means that Prometheus can reach the service and scrape its `/metrics` endpoint successfully.

---

## 3. Custom metrics list

I then checked which custom application metrics were available in Prometheus.

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
events_reservations_active
```

These metrics show that Prometheus collected application-specific metrics from the QuickTicket services. The `events_db_pool_size` metric was later used for the Saturation panel in Grafana.

The gateway request metric was also available and was used successfully in the request rate query below.

---

## 4. Request rate query

To make the metrics more visible, I generated traffic against the application using the provided load generator.

Command:

```bash
./loadgen/run.sh 5 20
```

Load generator output:

```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 20s
---
Done. total=92 success=78 fail=14 error_rate=15.2%
```

Then I queried the traffic golden signal in Prometheus.

PromQL query:

```promql
sum(rate(gateway_requests_total[5m]))
```

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
Request rate: 0.37 req/s
```

This confirms that Prometheus collected gateway request traffic and that the request rate can be queried with PromQL.

---

## 5. Grafana dashboard panels

I updated the `QuickTicket — Golden Signals` dashboard by adding the missing Latency and Saturation panels.

### Latency panel

Panel type:

```text
Time series
```

PromQL queries:

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

The panel shows three latency percentiles for gateway requests:

```text
p50
p95
p99
```

The unit was set to:

```text
seconds
```

This panel helps observe how response time changes under normal traffic and during failures. Instead of looking only at average latency, percentiles make it easier to notice slow requests and tail latency.

### Saturation panel

Panel type:

```text
Gauge
```

PromQL query:

```promql
events_db_pool_size
```

Gauge settings:

```text
Min: 0
Max: 10
Thresholds:
- green: default
- yellow: 7
- red: 9
```

This panel shows the current database pool usage for the `events` service. It represents the Saturation golden signal because it shows how close a resource is to its limit.

---

## 6. Dashboard observations: normal traffic vs payments failure

During normal traffic, the dashboard showed that all services were healthy:

```text
gateway:  1
events:   1
payments: 1
```

The Request Rate panel showed traffic generated by the load generator. The Error Rate panel stayed relatively low, and the Latency panel showed normal gateway latency percentiles during the traffic window.

For the failure scenario, I started load generation and stopped the `payments` service while traffic was still running.

Commands:

```bash
./loadgen/run.sh 5 60 &
sleep 15
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
```

Load generator output:

```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 60s
---
Done. total=270 success=251 fail=19 error_rate=7.0%
```

Prometheus `up` query during the failure:

```text
gateway:  1
events:   1
payments: 0
```

This shows that the `payments` service became unavailable, while `gateway` and `events` were still running.

Gateway 5xx error rate query:

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m]))
```

Output:

```text
0.0538873217740773
```

This is approximately:

```text
5.39%
```

During the failure window, the dashboard showed a visible increase in the Error Rate panel. Request traffic continued, but some requests failed because `gateway` could not successfully complete payment-related operations. The Latency panel also changed during the same period, because some requests depended on an unavailable downstream service.

After restarting `payments`, the `up` query returned:

```text
gateway:  1
events:   1
payments: 1
```

This confirms that the service recovered and Prometheus was again able to scrape metrics from all three application services.

---

## 7. Which golden signal showed the failure first?

The failure was shown first by the Availability / Service Health signal.

After the `payments` container was stopped, Prometheus marked the `payments` target as down:

```text
payments: 0
```

This happened after the next Prometheus scrape. Since the scrape interval was configured as 15 seconds, the failure became visible approximately 15–20 seconds after killing the `payments` service.

The Error Rate signal increased after that, when traffic continued and gateway started returning failed responses for payment-related requests. So, in this experiment, Service Health showed the failure first, and Error Rate confirmed the user-visible impact.

# Task 2 — SLOs and Recording Rules

## SLI/SLO definitions with error budget math

For this task, I defined two SLIs for the `gateway` service: Availability and Latency. These SLIs describe the user-visible quality of the service and can be used to check whether the system meets its SLO targets.

### SLI 1 — Availability

Availability is defined as the percentage of gateway requests that do not return a 5xx status code.

```promql
sum(rate(gateway_requests_total{status!~"5.."}[5m]))
/
sum(rate(gateway_requests_total[5m]))
```

SLO target:

```text
99.5% availability over a 7-day window
```

This means that at least 99.5% of gateway requests should complete without a server-side failure. The remaining 0.5% is the error budget.

Given approximately 1000 requests per day:

```text
1000 requests/day * 7 days = 7000 requests/week
```

Allowed failure percentage:

```text
100% - 99.5% = 0.5%
```

Allowed failed requests per week:

```text
7000 * 0.005 = 35 failed requests/week
```

So, with around 1000 requests per day, the weekly error budget allows approximately **35 failed 5xx gateway requests**.

### SLI 2 — Latency

Latency is defined as the percentage of gateway requests that complete in under 500ms.

```promql
sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m]))
/
sum(rate(gateway_request_duration_seconds_count[5m]))
```

SLO target:

```text
95% of gateway requests should complete under 500ms
```

This SLO focuses on the user experience of successful traffic. Even if the service is available, it should also respond quickly enough for most requests.

---

## Rules loaded output

I created the file `monitoring/prometheus/rules.yml` with three recording rules:

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

I added the rules file to `monitoring/prometheus/prometheus.yml`:

```yaml
rule_files:
  - "rules.yml"
```

I also mounted the rules file into the Prometheus container in `docker-compose.monitoring.yaml`:

```yaml
- ./monitoring/prometheus/rules.yml:/etc/prometheus/rules.yml:ro
```

After recreating/restarting Prometheus, I verified that the rules were loaded.

Command:

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
gateway:sli_availability:ratio_rate5m        = ok
gateway:sli_latency_500ms:ratio_rate5m       = ok
gateway:error_budget_burn_rate:ratio_rate5m  = ok
```

I also checked the recorded metrics directly after generating traffic:

```text
gateway:sli_availability:ratio_rate5m = 0.9191919191919192
gateway:sli_latency_500ms:ratio_rate5m = 1
gateway:error_budget_burn_rate:ratio_rate5m = 16.16161616161614
```

The availability SLI was approximately **91.92%**, which is below the 99.5% SLO. The latency SLI was **100%**, meaning that all measured gateway requests in the current window completed under 500ms. The burn rate was **16.16**, which means the error budget was being consumed much faster than allowed.

---

## SLO gauge observation during failure

I added an SLO Gauge panel in Grafana using the recorded availability SLI:

```promql
gateway:sli_availability:ratio_rate5m * 100
```

Panel configuration:

```text
Min: 99
Max: 100
Threshold: 99.5
```

During normal traffic, the gauge should stay close to 100% if the gateway does not return 5xx errors. During the failure experiment, I stopped the `payments` service while traffic was still running. After that, some payment-related requests started failing, and the availability SLI dropped.

Observed value during/after the failure window:

```text
gateway:sli_availability:ratio_rate5m = 0.9191919191919192
```

Converted to percentage:

```text
0.9191919191919192 * 100 ≈ 91.92%
```

This value is below the 99.5% SLO threshold, so the gauge clearly showed that the service was violating the availability SLO during the failure window. The burn rate also increased to approximately **16.16**, meaning that the system was spending its error budget much faster than acceptable.

# Bonus Task — Correlating Failure Across Metrics and Logs

## Timeline

For the bonus task, I ran a longer load test and then intentionally degraded the `payments` service to see how the failure appears in both metrics and logs.

Traffic was generated with:

```bash
./loadgen/run.sh 5 120
```

During the experiment, the `payments` service was restarted with the following configuration:

```text
PAYMENT_FAILURE_RATE=0.5
PAYMENT_LATENCY_MS=1000
```

This means that the service started adding around 1000ms of latency to payment requests and randomly failing part of them.

Observed timeline:

```text
2026-06-15T10:44:41Z — payments service was restarted
2026-06-15T10:44:48Z — payments started injecting 1000ms latency
2026-06-15T10:44:56Z — first clear injected payment failure appeared in payments logs
2026-06-15T10:44:23Z–10:44:57Z — gateway returned 500 responses for payment-related requests
~10:45:00Z onward — Grafana showed increased error rate and degraded availability during the traffic window
After payments was recreated without the chaos override — the service recovered
```

Load generator result:

```text
Done. total=387 success=352 fail=35 error_rate=9.0%
```

The load generator output confirms that some user-facing requests failed during the experiment.

---

## Log excerpts

The gateway logs show that payment-related requests started failing with 500 responses:

```text
gateway-1 | 2026-06-15T10:44:23.891712289Z {"time":"2026-06-15 10:44:23,891","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 500 Internal Server Error\""}
gateway-1 | 2026-06-15T10:44:23.892625523Z INFO: 172.19.0.1:50804 - "POST /reserve/384a907c-ef23-4dc7-a7cc-bafd0f2a96d0/pay HTTP/1.1" 500 Internal Server Error
```

The payments logs show that the service was restarted and then began injecting latency:

```text
payments-1 | 2026-06-15T10:44:41.905537343Z INFO: Started server process [1]
payments-1 | 2026-06-15T10:44:41.905650305Z INFO: Application startup complete.
payments-1 | 2026-06-15T10:44:48.702568616Z {"time":"2026-06-15 10:44:48,702","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for 944d0386-a451-4d02-8496-03e9e0c5b614"}
```

The payments logs also show injected failures:

```text
payments-1 | 2026-06-15T10:44:55.688438735Z {"time":"2026-06-15 10:44:55,688","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for 661e6eb0-6b7f-4c85-aa44-a1353d7c9704"}
payments-1 | 2026-06-15T10:44:56.688724112Z {"time":"2026-06-15 10:44:56,688","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 661e6eb0-6b7f-4c85-aa44-a1353d7c9704"}
payments-1 | 2026-06-15T10:44:56.689145458Z INFO: 172.19.0.7:57326 - "POST /charge HTTP/1.1" 500 Internal Server Error
```

---

## Root cause explanation

The root cause of the incident was the intentional degradation of the `payments` service.

The service was restarted with:

```text
PAYMENT_FAILURE_RATE=0.5
PAYMENT_LATENCY_MS=1000
```

Because of this configuration, `payments` started behaving as an unreliable downstream dependency. It added artificial latency to payment requests and returned injected 500 errors for some `/charge` calls.

This is visible in the payments logs through messages such as:

```text
Injecting 1000ms latency
Payment failed (injected)
POST /charge HTTP/1.1 500 Internal Server Error
```

The gateway logs show the user-facing impact. When `gateway` called `http://payments:8082/charge`, some requests received `500 Internal Server Error`, and the gateway then returned 500 responses from the `/reserve/.../pay` endpoint.

The metrics and load generator output match the same story. During the experiment, the load generator finished with:

```text
total=387
success=352
fail=35
error_rate=9.0%
```
So the dashboard spike was caused by a degraded downstream service. The `payments` container was still running, but it was slow and unreliable. As a result, gateway continued receiving traffic, while payment-related requests started failing. This led to a higher gateway error rate and lower availability during the failure window.