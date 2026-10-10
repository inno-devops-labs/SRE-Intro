# Lab 3 — Monitoring, Observability & SLOs
**Student:** Valerii Tiniakov
**Group:** B24-SD-03

## Task 1 — Configure Monitoring & Build Dashboard

### 3.2: Start the monitoring stack
**1. Output of compose ps showing all 7 services:**
```text
NAME               IMAGE                     COMMAND                  SERVICE      CREATED          STATUS                    PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       19 seconds ago   Up 12 seconds             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      19 seconds ago   Up 11 seconds             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      20 seconds ago   Up 18 seconds             0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     20 seconds ago   Up 18 seconds             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     20 seconds ago   Up 18 seconds (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   20 seconds ago   Up 18 seconds             0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        20 seconds ago   Up 18 seconds (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

### 3.3: Verify Prometheus is scraping
**2. Prometheus targets output:**
```text
"job":"events"
"scrapeUrl":"http://events:8081/metrics"
"health":"up"
"job":"gateway"
"scrapeUrl":"http://gateway:8080/metrics"
"health":"up"
"job":"payments"
"scrapeUrl":"http://payments:8082/metrics"
"health":"up"
```

### 3.4: Explore metrics
**3. Custom metrics list:**
```text
"events_db_pool_size"
"events_orders_created"
"events_orders_total"
"events_reservations_active"
```

**4. PromQL query output (request rate):**
```text
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1781894837.212,"0.17765584199122172"]}]}}
```

### 3.5: Complete the golden signals dashboard
**5. PromQL queries used:**
* **Latency panel (Golden Signal #1):**
  * `histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))`
  * `histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))`
  * `histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))`
* **Saturation panel (Golden Signal #4):**
  * `events_db_pool_size`

### 3.6 & 3.7: Inject failure and observe
**6. Dashboard observations (normal traffic vs payments failure):**
During normal traffic, the error rate was 0, and latency was low. When the `payments` service was stopped, the `Error Rate` immediately spiked to ~6%, `Latency` (p95 and p99) shot up dramatically to ~6-7 seconds (likely due to timeouts waiting for the dead service), and `Service Health` for `payments` dropped from 1 to 0. `Saturation` remained stable at 0 since the events database was not affected by this specific failure.

**7. Question: Which golden signal showed the failure first? How long after killing payments?**
The `Error Rate` and `Latency` signals showed the failure first, almost immediately as active requests failed or timed out. The `Service Health` signal took longer (up to 15 seconds) to reflect the failure because it depends on the Prometheus scrape interval (`scrape_interval: 15s`) to fail before the `up` metric changes to 0.

---

## Task 2 — Define SLOs & Recording Rules

### 3.8: Define SLIs and SLOs
* **SLI 1 — Availability:** % of gateway requests returning non-5xx (Target: 99.5% over 7 days)
* **SLI 2 — Latency:** % of gateway requests completing under 500ms (Target: 95%)
* **Error Budget Math:** With ~1000 requests/day, that is 7000 requests per week. An SLO of 99.5% allows for a 0.5% error budget. Therefore, the error budget allows for `7000 * 0.005 = 35` failures per week.

### 3.9: Create recording rules
**Rules loaded output:**
```text
"name":"slo_rules"
"name":"gateway:sli_availability:ratio_rate5m"
"health":"ok"
"name":"gateway:sli_latency_500ms:ratio_rate5m"
"health":"ok"
"name":"gateway:error_budget_burn_rate:ratio_rate5m"
"health":"ok"
```

### 3.10: Build SLO panel
**SLO gauge observation during failure:**
When the `payments` service was stopped during active traffic, the `gateway:sli_availability:ratio_rate5m` metric immediately began to drop. The Gauge panel clearly showed the availability percentage falling from 100% down to 98.5%, significantly below our target SLO threshold of 99.5%. This drop also triggered a spike in the Error Budget Burn Rate, confirming that our monitoring correctly identifies and quantifies the impact of the service failure on our error budget.
