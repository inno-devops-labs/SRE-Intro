# Lab 3 Report — Monitoring, Observability & SLOs

## Task 1 — Configure Monitoring & Build Dashboard (6 pts)

### 1. Output of compose ps showing all 7 services
```bash
$ dc ps
```
```text
NAME               IMAGE                     COMMAND                  SERVICE      CREATED          STATUS                  PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       52 seconds ago   Up 48 seconds           0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      50 seconds ago   Up 48 seconds           0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      52 seconds ago   Up 49 seconds           0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     52 seconds ago   Up 49 seconds           0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     21 hours ago     Up 21 hours (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   52 seconds ago   Up 49 seconds           0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        21 hours ago     Up 21 hours (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

### 2. Prometheus targets output (all 3 up)
```bash
$ curl -s http://localhost:9090/api/v1/targets | python -c "
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


### 3. Custom metrics list
```bash
$ curl -s http://localhost:9090/api/v1/label/__name__/values | python -c "
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
events_reservations_active
```

### 4. PromQL query output (request rate)
```bash
$ curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))'   http://localhost:9090/api/v1/query | python -c "
import sys, json
r = json.load(sys.stdin)
print(f\"Request rate: {float(r['data']['result'][0]['value'][1]):.2f} req/s\")"
```
```text
Request rate: 0.38 req/s
```

### 5. PromQL queries you used for Latency and Saturation panels
**Latency Panel Queries:**
* p50: histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le))
* p95: histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le))
* p99: histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le))

**Saturation Panel Query:**
* events_db_pool_size

### 6. Dashboard observations: normal traffic vs payments failure
* **Normal traffic:** All latency percentiles (p50, p95, p99) were stable and close to 0 seconds (a few milliseconds), meaning all microservices responded instantly. The request rate showed a clean step up when the load generator started.

* **Payments failure state:** As soon as the payments service was stopped, the p99 Latency experienced a massive spike, exploding up to ~4.5 seconds. Interestingly, p50 and p95 metrics remained near zero. This indicates that endpoints not dependent on payments (like browsing events) still performed perfectly, but the payment processing endpoint started hitting a severe HTTP/network timeout bottleneck, forcing the unluckiest 1% of requests to hang before failing.

### 7. Answer: "Which golden signal showed the failure first? How long after killing payments?"
* **Which signal showed it first:** The Latency signal (specifically the p99 percentile) caught the degradation immediately. While the overall traffic throughput (Request Rate) looked normal, the p99 metric exposed that the system started failing under the hood due to internal network timeouts.

* **How long after:** The degradation appeared on the dashboard within 10–15 seconds after executing the stop payments command. This latency in visualization matches the Prometheus configuration, as the global scrape_interval is set to 15 seconds. The system captured the very first scrape loop right after the container went down.

## Task 2 — Define SLOs & Recording Rules (4 pts)

### 9. SLI/SLO definitions with error budget math
* **SLI 1 (Availability):** The percentage of HTTP requests processed by the API gateway returning a non-5xx status code.
  * **SLO Target:** 99.5% over a rolling 7-day window.
* **SLI 2 (Latency):** The percentage of HTTP requests handled by the API gateway completing in under 500ms ($le = 0.5$).
  * **SLO Target:** 95%.

**Error Budget Calculation:**
Given an average traffic volume of ~1,000 requests per day:
* **Total weekly traffic:** $$1000 \text{ requests/day} \times 7 \text{ days} = 7000 \text{ requests/week}$$
* **Allowed error percentage:** $$100\% - 99.5\% = 0.5\%$$
* **Total allowed failures per week (Error Budget):** $$7000 \times 0.005 = 35 \text{ failures}$$

Our error budget allows for exactly **35 failed requests per week** before the SLO is breached.


### 10. Rules loaded output
After adding the rules configuration to `rules.yml` and configuring Prometheus, the rules were verified using the API endpoint. All defined target metrics loaded successfully with a healthy status:

```text
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

### 11. SLO gauge observation during failure

* **Before the failure:** With normal background traffic running, the Availability SLO gauge was stable, indicating an ideal status of **100%** (displayed inside a healthy green arc).
* **During the failure:** Immediately after stopping the `payments` container and running the load generator, the gateway began failing on payment routes, returning 5xx HTTP statuses. As a result of the sudden error rate spike within the 5-minute rolling window, the **SLO gauge dropped significantly to 99.2%**.
* **Visual indicator shift:** Because the metric dropped below our defined threshold of 99.5%, the gauge exceeded the absolute margin, breached the SLO target, and **instantly turned red**, providing a clear visual alert of an active error budget burn.



## Bonus Task — Correlate Failure Across Metrics & Logs (2 pts)

### 13. Timeline with timestamps: injection → first error in logs → spike on dashboard → recovery

**12:42:38** — Failure Injection: The payments service container was recreated with failure parameters. It immediately logged the first artificial error (Payment failed (injected)) and started responding with 500 Internal Server Error.

**12:42:42** — Cascading Latency: The payment service began pruningly injecting an artificial 1000ms delay into successful transactions. Requests started piling up in the processing queue.

**12:43:02** — Dashboard Spike & Gateway Congestion: Due to severe delays from the upstream payment service, the gateway couldn't handle incoming traffic in time. The p99 latency chart in Grafana spiked dramatically. The gateway logged multiple 409 Conflict errors as retried requests clashed over database locks. The SLO gauge dropped below 99.5% and turned red.

**12:43:21** — Recovery: The load generator (loadgen) finished its 120-second execution loop. Active user traffic dropped to zero, leaving only background Prometheus scraping (GET /metrics), which stopped the error budget burn.


### 14. Log excerpts from gateway and payments at the failure moment
**Gateway logs (cascading lock conflicts due to timeouts):**
```text
gateway-1  | 2026-06-07T12:43:02.933987664Z {"time":"2026-06-07 12:43:02,933","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/2/reserve \"HTTP/1.1 409 Conflict\""}
gateway-1  | 2026-06-07T12:43:02.934616584Z INFO:     172.19.0.1:58590 - "POST /events/2/reserve HTTP/1.1" 409 Conflict
```

**Payments logs (injected 500 errors and 1-second latency):**
```text
payments-1 | 2026-06-07T12:42:38.604271814Z {"time":"2026-06-07 12:42:38,603","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 3ad0fd47-ec82-4c45-8b68-808dcb550ec3"}
payments-1 | 2026-06-07T12:42:38.605115047Z INFO:     172.19.0.8:51756 - "POST /charge HTTP/1.1" 500 Internal Server Error
payments-1 | 2026-06-07T12:42:42.475612190Z {"time":"2026-06-07 12:42:42,475","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for 2009b021-9727-41ef-a078-24e9b0804a95"}
```

### 15. Root cause explanation connecting metrics to logs

* The root cause of the system failure and the subsequent dashboard anomalies lies in the artificial degradation of the downstream payments microservice.

* Once the failure variables were applied, the payment component dropped 50% of incoming gateway requests with internal server errors and delayed the remaining successful ones by 1000ms. This response delay instantly caused a massive backup in connection queues, directly reflecting on Grafana's Latency (p99) graph as a massive spike.

* Concurrently, traffic congestion and client retries led to resource contention, evidenced by the gateway logging cascading 409 Conflict statuses. Within Prometheus's rolling 5-minute evaluation window, this combined wave of HTTP 5xx errors and business logic anomalies rapidly burned through the error budget, driving the overall availability SLI down to 99.2%. Since this fell below the 99.5% operational target, it triggered the visual red alert on the SLO gauge panel.





