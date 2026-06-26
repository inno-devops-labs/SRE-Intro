# Lab 3 — Monitoring, Metrics, and SLOs

## Task 1 — Configure Monitoring & Build Dashboard

### 3.1 Prometheus Configuration

#### prometheus.yml

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

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

---

### 3.2 Monitoring Stack

Command:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
```

Output:

```text
NAME               IMAGE                     COMMAND                  SERVICE      CREATED          STATUS                   PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       11 seconds ago   Up 4 seconds             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      11 seconds ago   Up 3 seconds             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      11 seconds ago   Up 9 seconds             0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     11 seconds ago   Up 9 seconds             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     11 seconds ago   Up 9 seconds (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   11 seconds ago   Up 9 seconds             0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        11 seconds ago   Up 9 seconds (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

---

### 3.3 Prometheus Targets

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

Observation:

```text
All three QuickTicket services were successfully scraped by Prometheus and reported as healthy targets.
```

---

### 3.4 Metrics Exploration

#### Gateway Metrics

```text
# HELP gateway_requests_total Total requests
# TYPE gateway_requests_total counter
gateway_requests_total{method="GET",path="/events",status="200"} 116.0
gateway_requests_total{method="POST",path="/events/{id}/reserve",status="200"} 54.0
gateway_requests_total{method="POST",path="/reserve/{id}/pay",status="200"} 15.0
gateway_requests_total{method="GET",path="/health",status="200"} 3.0
# HELP gateway_requests_created Total requests
# TYPE gateway_requests_created gauge
gateway_requests_created{method="GET",path="/events",status="200"} 1.7817903773261633e+09
gateway_requests_created{method="POST",path="/events/{id}/reserve",status="200"} 1.7817903780510519e+09
```

#### Custom Metrics

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

#### Request Rate Query

```text
Request rate: 0.42 req/s
```

---

### 3.5 Golden Signals Dashboard

#### Latency Panel Queries

```promql
histogram_quantile(
  0.50,
  sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le)
)
```

```promql
histogram_quantile(
  0.95,
  sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le)
)
```

```promql
histogram_quantile(
  0.99,
  sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le)
)
```

#### Saturation Panel Query

```promql
events_db_pool_size
```

---

### 3.6 Failure Injection

Command:

```bash
docker compose stop payments
```

Observation:

```text
Before the failure, request rate and error rate remained stable. After stopping the payments service, the error rate increased, service health became degraded, and latency increased for payment-related requests. After restarting payments, the dashboard gradually returned to normal
```

---

### Dashboard Observations

Normal traffic:

```text
During normal operation, request rate remained stable across all endpoints (/events, /events/{id}/reserve, /health, /reserve/{id}/pay). Error rate was 0%, and all services showed healthy status in the Service Health panel. Latency stayed low and stable (p50 close to 0.3–0.5s, p95 around ~3–4s due to baseline processing, no spikes observed). Saturation (DB pool usage) remained at 0, indicating no resource pressure
```

Payments failure:

```text
After stopping the payments service, the system remained partially functional. The Service Health panel initially still showed events and gateway as healthy, but the payments dependency became unavailable shortly after. The Error Rate panel showed a clear spike up to ~10–12%, indicating failed payment-related requests. Latency increased significantly for /reserve and /pay endpoints, while /events remained stable. equest Rate stayed relatively consistent, meaning traffic did not drop, but reliability degraded
```

#### Which golden signal showed the failure first?

```text
The Error Rate panel showed the failure first, as it immediately reacted to failed payment requests. Service Health changed shortly after when Prometheus detected the payments service as unavailable
```

#### How long after killing payments?

```text
~17 seconds after stopping payments
```

---

## Task 2 — Define SLOs & Recording Rules

### SLI/SLO Definitions

Availability SLO:

```text
99.5% over 7 days
```

Latency SLO:

```text
95% of requests under 500 ms
```

Error Budget:

```text
1000 requests/day × 7 days = 7000 requests/week

Allowed failures:
7000 × 0.005 = 35 failures/week
```

---

### Recording Rules

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

---

### Rules Loaded Output

```text
 "rules": [
                    {
                        "name": "gateway:sli_availability:ratio_rate5m",
                        "query": "sum(rate(gateway_requests_total{status!~\"5..\"}[5m])) / sum(rate(gateway_requests_total[5m]))",
                        "labels": {},
                        "health": "ok",
                        "evaluationTime": 0.000819777,
                        "lastEvaluation": "2026-06-18T15:29:22.2872205Z",
                        "type": "recording"
                    },
                    {
                        "name": "gateway:sli_latency_500ms:ratio_rate5m",
                        "query": "sum(rate(gateway_request_duration_seconds_bucket{le=\"0.5\"}[5m])) / sum(rate(gateway_request_duration_seconds_count[5m]))",
                        "labels": {},
                        "health": "ok",
                        "evaluationTime": 0.000262493,
                        "lastEvaluation": "2026-06-18T15:29:22.288057131Z",
                        "type": "recording"
                    },
                    {
                        "name": "gateway:error_budget_burn_rate:ratio_rate5m",
                        "query": "(1 - gateway:sli_availability:ratio_rate5m) / (1 - 0.995)",
                        "labels": {},
                        "health": "ok",
                        "evaluationTime": 0.000151172,
                        "lastEvaluation": "2026-06-18T15:29:22.288329558Z",
                        "type": "recording"
                    }
                ],
```

---

### SLO Gauge Observation

```text
When payments was stopped, availability decreased and the SLO gauge dropped below its normal value. After payments recovered, the gauge gradually returned toward the target
```

---

## Bonus Task — Correlate Failure Across Metrics & Logs

### Timeline

T-30s → T0:
System is stable. Gateway successfully processes requests to events service.
Payments service responds normally, no errors observed in logs or metrics.

T0:
Payment failure injection started (PAYMENT_FAILURE_RATE=0.5, PAYMENT_LATENCY_MS=1000).

T0+~10–15s:
First signs of degradation appear.
Payments requests become slower and occasionally fail.
Gateway /reserve → /pay flow starts showing increased latency.

T0+~15–30s:
Error rate increases in gateway metrics.
Grafana dashboard shows spike in latency and partial failures.
Prometheus detects changes after scrape interval (~15s).

T0+~30–60s:
System is in degraded state.
Some payment requests fail or timeout, but events service remains stable.

Recovery:
After restarting payments, system returns to normal within ~10–20 seconds.
Payment success logs reappear and error rate drops back to zero.


### LOG EXCERPTS

#### Gateway

```bash
gateway-1  | 2026-06-18T15:38:34.140891136Z INFO:     172.18.0.1:48426 - "POST /events/2/reserve HTTP/1.1" 409 Conflict
gateway-1  | 2026-06-18T15:38:34.387430552Z {"time":"2026-06-18 15:38:34,386","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:34.389423131Z INFO:     172.18.0.1:48442 - "GET /events HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:34.628089432Z {"time":"2026-06-18 15:38:34,627","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:34.631776558Z INFO:     172.18.0.1:48450 - "GET /events HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:34.878396307Z {"time":"2026-06-18 15:38:34,877","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:34.881073486Z INFO:     172.18.0.1:48452 - "GET /events HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:35.138180808Z {"time":"2026-06-18 15:38:35,137","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/5/reserve "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:35.140617369Z INFO:     172.18.0.1:48456 - "POST /events/5/reserve HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:35.395293829Z {"time":"2026-06-18 15:38:35,394","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:35.397597195Z INFO:     172.18.0.1:48460 - "GET /events HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:35.670159776Z {"time":"2026-06-18 15:38:35,669","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/4/reserve "HTTP/1.1 409 Conflict""}
gateway-1  | 2026-06-18T15:38:35.673188295Z INFO:     172.18.0.1:48468 - "POST /events/4/reserve HTTP/1.1" 409 Conflict
gateway-1  | 2026-06-18T15:38:35.958331429Z {"time":"2026-06-18 15:38:35,957","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/2/reserve "HTTP/1.1 409 Conflict""}
gateway-1  | 2026-06-18T15:38:35.960301911Z INFO:     172.18.0.1:48476 - "POST /events/2/reserve HTTP/1.1" 409 Conflict
gateway-1  | 2026-06-18T15:38:36.240557266Z {"time":"2026-06-18 15:38:36,239","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:36.243278633Z INFO:     172.18.0.1:48488 - "GET /events HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:36.501375340Z {"time":"2026-06-18 15:38:36,500","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:36.503909598Z INFO:     172.18.0.1:48494 - "GET /events HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:36.752363790Z {"time":"2026-06-18 15:38:36,751","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:36.754460438Z INFO:     172.18.0.1:48506 - "GET /events HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:37.008625870Z {"time":"2026-06-18 15:38:37,007","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/3/reserve "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:37.010742087Z INFO:     172.18.0.1:48514 - "POST /events/3/reserve HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:37.269882853Z {"time":"2026-06-18 15:38:37,268","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/2/reserve "HTTP/1.1 409 Conflict""}
gateway-1  | 2026-06-18T15:38:37.272122859Z INFO:     172.18.0.1:48526 - "POST /events/2/reserve HTTP/1.1" 409 Conflict
gateway-1  | 2026-06-18T15:38:37.531405287Z {"time":"2026-06-18 15:38:37,530","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:37.533795985Z INFO:     172.18.0.1:48528 - "GET /events HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:37.767938575Z {"time":"2026-06-18 15:38:37,767","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:37.769976325Z INFO:     172.18.0.1:48530 - "GET /events HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:38.011946981Z {"time":"2026-06-18 15:38:38,011","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:38.013848670Z INFO:     172.18.0.1:48540 - "GET /events HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:38.266490978Z {"time":"2026-06-18 15:38:38,265","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:38.268831695Z INFO:     172.18.0.1:48544 - "GET /events HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:38.528994969Z {"time":"2026-06-18 15:38:38,528","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:38.531611856Z INFO:     172.18.0.1:48550 - "GET /events HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:38.789732227Z {"time":"2026-06-18 15:38:38,788","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:38.792595362Z INFO:     172.18.0.1:48566 - "GET /events HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:39.048641661Z {"time":"2026-06-18 15:38:39,047","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/3/reserve "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:39.050442854Z INFO:     172.18.0.1:48568 - "POST /events/3/reserve HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:39.302382488Z {"time":"2026-06-18 15:38:39,301","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:39.305439471Z INFO:     172.18.0.1:48578 - "GET /events HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:39.570457965Z {"time":"2026-06-18 15:38:39,569","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:39.573350225Z INFO:     172.18.0.1:48586 - "GET /events HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:39.826604178Z {"time":"2026-06-18 15:38:39,825","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:39.829656633Z INFO:     172.18.0.1:48600 - "POST /events/1/reserve HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:39.878745952Z {"time":"2026-06-18 15:38:39,877","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:39.896452618Z {"time":"2026-06-18 15:38:39,895","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/reservations/f76bc1d8-779b-49f1-a895-59f6951f607c/confirm "HTTP/1.1 200 OK""}
gateway-1  | 2026-06-18T15:38:39.898986480Z INFO:     172.18.0.1:48616 - "POST /reserve/f76bc1d8-779b-49f1-a895-59f6951f607c/pay HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:38:48.957477708Z INFO:     172.18.0.6:55578 - "GET /metrics HTTP/1.1" 200 OK
gateway-1  | 2026-06-18T15:39:03.956353028Z INFO:     172.18.0.6:35196 - "GET /metrics HTTP/1.1" 200 OK
```

#### Payments

```bash
payments-1  | 2026-06-18T15:25:14.898895251Z INFO:     172.18.0.8:33790 - "POST /charge HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:25:19.917003706Z {"time":"2026-06-18 15:25:19,916","level":"INFO","service":"payments","msg":"Payment success: PAY-71231A4E for ac27e5f8-f3d2-4676-8ab5-02ea93e0b566"}
payments-1  | 2026-06-18T15:25:19.918293202Z INFO:     172.18.0.8:33792 - "POST /charge HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:25:22.337741108Z INFO:     172.18.0.6:58748 - "GET /metrics HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:25:26.161654467Z {"time":"2026-06-18 15:25:26,161","level":"INFO","service":"payments","msg":"Payment success: PAY-B0E77527 for 077feaaf-de71-467c-8ac0-eb25f415ce6d"}
payments-1  | 2026-06-18T15:25:26.162648542Z INFO:     172.18.0.8:52336 - "POST /charge HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:25:28.847692106Z {"time":"2026-06-18 15:25:28,847","level":"INFO","service":"payments","msg":"Payment success: PAY-006E6203 for 35927719-928c-4d3f-830f-771ea2803fef"}
payments-1  | 2026-06-18T15:25:28.848492444Z INFO:     172.18.0.8:52336 - "POST /charge HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:25:29.464050300Z INFO:     Shutting down
payments-1  | 2026-06-18T15:25:29.565963989Z INFO:     Waiting for application shutdown.
payments-1  | 2026-06-18T15:25:29.566135894Z INFO:     Application shutdown complete.
payments-1  | 2026-06-18T15:25:29.566520715Z INFO:     Finished server process [1]
payments-1  | 2026-06-18T15:35:20.327941229Z INFO:     Started server process [1]
payments-1  | 2026-06-18T15:35:20.328025700Z INFO:     Waiting for application startup.
payments-1  | 2026-06-18T15:35:20.328099371Z INFO:     Application startup complete.
payments-1  | 2026-06-18T15:35:20.328924472Z INFO:     Uvicorn running on http://0.0.0.0:8082 (Press CTRL+C to quit)
payments-1  | 2026-06-18T15:35:22.260838404Z INFO:     172.18.0.6:35574 - "GET /metrics HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:35:37.243785616Z INFO:     172.18.0.6:52560 - "GET /metrics HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:35:52.233830403Z INFO:     172.18.0.6:40740 - "GET /metrics HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:36:07.235417581Z INFO:     172.18.0.6:34810 - "GET /metrics HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:36:22.233813059Z INFO:     172.18.0.6:52244 - "GET /metrics HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:36:37.238049719Z INFO:     172.18.0.6:53034 - "GET /metrics HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:36:51.349784096Z {"time":"2026-06-18 15:36:51,348","level":"INFO","service":"payments","msg":"Payment success: PAY-BB5E0E4E for a57bc2d5-eed9-4609-8675-dd13a5e61e2a"}
payments-1  | 2026-06-18T15:36:51.351342905Z INFO:     172.18.0.8:46828 - "POST /charge HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:36:52.229552940Z INFO:     172.18.0.6:58746 - "GET /metrics HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:36:54.539448227Z {"time":"2026-06-18 15:36:54,538","level":"INFO","service":"payments","msg":"Payment success: PAY-18F92291 for 2b2f9ecd-3c92-4633-86e0-ea352c19787c"}
payments-1  | 2026-06-18T15:36:54.540909024Z INFO:     172.18.0.8:46828 - "POST /charge HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:37:01.973930268Z {"time":"2026-06-18 15:37:01,973","level":"INFO","service":"payments","msg":"Payment success: PAY-106D432B for ce7677de-7494-4285-bf7f-c66cba80746a"}
payments-1  | 2026-06-18T15:37:01.975342932Z INFO:     172.18.0.8:53578 - "POST /charge HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:37:03.059786727Z {"time":"2026-06-18 15:37:03,058","level":"INFO","service":"payments","msg":"Payment success: PAY-EB0C1B49 for 3cc5ce7f-f91f-4d9f-86ce-6758fa77d0d3"}
payments-1  | 2026-06-18T15:37:03.060767023Z INFO:     172.18.0.8:53578 - "POST /charge HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:37:03.946853001Z {"time":"2026-06-18 15:37:03,945","level":"INFO","service":"payments","msg":"Payment success: PAY-E9F4901F for fd9dd478-a4ba-425a-9794-baec3a7fd33d"}
payments-1  | 2026-06-18T15:37:03.949241958Z INFO:     172.18.0.8:53578 - "POST /charge HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:37:06.371840154Z {"time":"2026-06-18 15:37:06,370","level":"INFO","service":"payments","msg":"Payment success: PAY-F1FEB700 for 5f2d9837-331a-494e-bd24-979d304fe830"}
payments-1  | 2026-06-18T15:37:06.373791403Z INFO:     172.18.0.8:53578 - "POST /charge HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:37:07.229001591Z INFO:     172.18.0.6:50828 - "GET /metrics HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:37:13.555606671Z INFO:     Shutting down
payments-1  | 2026-06-18T15:37:13.656385845Z INFO:     Waiting for application shutdown.
payments-1  | 2026-06-18T15:37:13.656639755Z INFO:     Application shutdown complete.
payments-1  | 2026-06-18T15:37:13.656668212Z INFO:     Finished server process [1]
payments-1  | 2026-06-18T15:38:36.214248184Z INFO:     Started server process [1]
payments-1  | 2026-06-18T15:38:36.214547164Z INFO:     Waiting for application startup.
payments-1  | 2026-06-18T15:38:36.215227634Z INFO:     Application startup complete.
payments-1  | 2026-06-18T15:38:36.216095443Z INFO:     Uvicorn running on http://0.0.0.0:8082 (Press CTRL+C to quit)
payments-1  | 2026-06-18T15:38:37.226918156Z INFO:     172.18.0.6:49982 - "GET /metrics HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:38:39.875285790Z {"time":"2026-06-18 15:38:39,874","level":"INFO","service":"payments","msg":"Payment success: PAY-F022FCA5 for f76bc1d8-779b-49f1-a895-59f6951f607c"}
payments-1  | 2026-06-18T15:38:39.876955341Z INFO:     172.18.0.8:55708 - "POST /charge HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:38:52.210636285Z INFO:     172.18.0.6:49942 - "GET /metrics HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:39:07.216848705Z INFO:     172.18.0.6:36990 - "GET /metrics HTTP/1.1" 200 OK
payments-1  | 2026-06-18T15:39:22.206015551Z INFO:     172.18.0.6:45394 - "GET /metrics HTTP/1.1" 200 OK
```

### Root cause
The root cause of the issue was the unavailability of the payments service. When payments was stopped, the gateway could still accept reservation requests, but payment processing failed or was delayed. This caused an increase in error rate and latency in the gateway, since the /pay endpoint depends on the payments service. After restarting payments, the system recovered automatically and payment processing returned to normal.