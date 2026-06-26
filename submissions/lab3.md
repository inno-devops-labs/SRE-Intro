# Task 1
#### 1. Output of compose ps showing all 7 services:
NAME               IMAGE                     COMMAND                  SERVICE      CREATED              STATUS                        PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       About a minute ago   **Up About a minute**             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      About a minute ago   **Up About a minute**             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      About a minute ago   **Up About a minute**             0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     About a minute ago   **Up About a minute**             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     About a minute ago   **Up About a minute** (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   About a minute ago   **Up About a minute**             0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        About a minute ago   **Up About a minute** (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp

#### 2. Prometheus targets output (all 3 `up`)
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics

#### 3. Custom metrics list
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

####  4. PromQL query output (request rate)
Request rate: 0.24 req/s
####  5. PromQL queries you used for Latency and Saturation panels

- Latency panel:
	**p50**: histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
	**p95**: histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
	**p99**: histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
- Saturation panel:
	**events_db_pool_size**
#### 6. Dashboard observations: normal traffic vs payments failure
| **Golden Signal / Metric** | **Normal Traffic State**                                                                 | **Payments Failure State**                                                                                        |
| -------------------------- | ---------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| **Service Health**         | All services  are healthy and report a status of **1 (Up)**.                             | The status of the `payments` service immediately drops to **0 (Down)**.                                           |
| **Request Rate (Traffic)** | Steady and consistent flow of incoming requests distributed across all three endpoints.  | Requests  continue to arrive from the load generator, but processing fails at the gateway level.                  |
| **Error Rate**             | Remains at **0%**. All client requests are processed successfully.                       | A sharp, vertical spike in HTTP 5xx errors occurs, reaching **9.52%** of total traffic.                           |
| **Latency**                | Latency is minimal and stable; the charts are flat and within normal operational limits. | A sudden, temporary spike in tail latency occurs as the gateway hits timeouts trying to reach the dead container. |
| **Saturation**             | Database connection pool utilization (`events_db_pool_size`) is normal and stable.       | Remains at **0** because the stopped service cannot generate new sessions or put load on the database pool.       |

#### 7. Answer: "Which golden signal showed the failure first? How long after killing payments?"

- The failure was first detected by the **Errors (Error Rate)** signal on the gateway level, along with the **Service Health** dashboard component showing `0` for the `payments` job.
- **Timing:** The reaction was near-instantaneous, appearing on the dashboard at the very next Prometheus scrape interval (`scrape_interval: 15s`) after the load generator script was executed. 
  
  
# Task 2

#### 3.8 SLI/SLO Definitions & Error Budget Math 

1,000 requests/day × 7 days = **7,000 requests/week**

**1. Availability Error Budget (Non-5xx responses)**
- **SLO Target:** 99.5%
- **Error Budget:** 0.5% (100% - 99.5%)
- **Allowed Failures:** 7,000 × 0.005 = **35 failed requests per week**

**2. Latency Error Budget (< 500ms)**
- **SLO Target:** 95%
- **Error Budget:** 5% (100% - 95%)
- **Allowed Failures:** 7,000 × 0.05 = **350 slow requests per wee**

#### Rules loaded output

`curl -s http://localhost:9090/api/v1/rules | python3 -c "import sys, json for g in json.load(sys.stdin)['data']['groups']: for r in g['rules']: print(f\"{r['name']:45} = {r.get('health', 'N/A')}\")":

**gateway:sli_availability:ratio_rate5m         = ok**
**gateway:sli_latency_500ms:ratio_rate5m        = ok**
**gateway:error_budget_burn_rate:ratio_rate5m   = ok**

#### 3.10: Build SLO panel

**Failure Simulation:** * The `payments` service was stopped for 60 seconds while a load generator simulated traffic.

- **Observed Behavior:** As the error rate increased, the `sli_availability` metric decreased, reflecting the impact on the error budget.
- The Gauge panel displayed a drop to **97.8%**, successfully breaching the 99.5% SLO threshold. The visual indicator turned red, confirming that the system correctly identifies an SLO violation in real-time.

# Bonus Task


#### **Timeline:**
- 14:25:31 - Traffic generation started
-  14:26:02 - injection
- 14:26:04 - first error in logs
- 14:27:30 - spike on dashboard
- 14:28:31 - end of traffic generation & recovery

#### **Log Excerpts at failure moment:**

**Gateway:** 
	gateway-1  | INFO:     172.18.0.1:39734 - "POST /reserve/505cc361-1d88-4e5c-b91f-572508f1867e/pay HTTP/1.1" 500 Internal Server Error
	gateway-1  | {"time":"2026-06-17 14:26:06,404","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge "HTTP/1.1 500 Internal Server Error""}

**Payments:**
	payments-1  | {"time":"2026-06-17 14:26:04,402","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 505cc361-1d88-4e5c-b91f-572508f1867e"}


#### **Root Cause Explanation:** 
The root cause of the service failure is the artificial injection of a 50% failure rate and latency into the `payments` service. The 500-series errors appearing in the `gateway` logs directly correlate with the "injected" warnings in the `payments` service logs. This failure propagates to the `gateway` as HTTP 500 status codes, which triggers the corresponding spike in the `gateway_requests_total` error rate metric observed on the Grafana dashboard, wich get its spike on 14:27:30.


