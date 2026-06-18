# Lab 3 — Monitoring, Observability & SLOs

**Author:** Anton Bugaev  
**Date:** 2026-06-16

> Note: Host port `5432` is already in use on my machine. For local runs I temporarily removed the `postgres` host port mapping so the stack can start. Internal Docker networking is unchanged.

---

## Task 1 — Monitoring + Dashboard

### 3.2 `docker compose ... ps` (all 7 services)

```
NAME               IMAGE                     COMMAND                  SERVICE      STATUS                    PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       Up                       0.0.0.0:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      Up                       0.0.0.0:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      Up                       0.0.0.0:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     Up                       0.0.0.0:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     Up (healthy)             5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   Up                       0.0.0.0:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        Up (healthy)             0.0.0.0:6379->6379/tcp
```

### 3.3 Prometheus targets (all 3 `up`)

```
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

### 3.4 Custom metrics list

From Prometheus label values filter (`gateway_`, `events_`, `payments_`):

```
events_db_pool_size
events_orders_created
events_orders_total
events_reservations_active
```

### 3.4 PromQL request rate query output

Query:

```
sum(rate(gateway_requests_total[5m]))
```

Result (req/s):

```
0.16763201155170498
```

### 3.5 Grafana panel queries used

**Latency (p50 / p95 / p99):**

- p50:

```
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

- p95:

```
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

- p99:

```
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

**Saturation (DB pool gauge):**

```
events_db_pool_size
```

### 3.6 Failure injection observation (payments stop/start)

I ran steady load and stopped payments:

```
stopped=2026-06-16T09:12:23Z started=2026-06-16T09:13:54Z
```

**Observations:**
- During normal traffic: request rate steady, error rate near 0%, `up{job="payments"}` = 1.
- After stopping payments: `up{job="payments"}` dropped to 0 quickly (next scrape), error rate panel spiked (5xx), latency (p95/p99) increased due to failed/slow downstream calls.
- After restarting: `up` returned to 1 and error rate recovered.

**Which golden signal showed failure first?**
- **Service Health (`up`)** showed it first (first scrape after stopping payments, within ~15s).

---

## Task 2 — SLOs + Recording Rules (optional)

### 3.8 SLI/SLO definitions + error budget math

- **SLI 1 (Availability):** % of gateway requests returning non-5xx  
  **SLO:** 99.5% over 7 days  
  Error budget = \(1 - 0.995 = 0.005\) = **0.5%** of requests.

- **SLI 2 (Latency):** % of gateway requests completing under 500ms  
  **SLO:** 95%  

With ~1000 requests/day ⇒ ~7000 requests/week:
- Allowed failures for availability SLO per week: \(7000 \times 0.005 = 35\) requests.

### 3.9 Rules loaded output

```
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

Example query outputs:

```
gateway:sli_availability:ratio_rate5m 0.937076249716289
gateway:sli_latency_500ms:ratio_rate5m 1
gateway:error_budget_burn_rate:ratio_rate5m 12.58475005674219
```

### 3.10 SLO gauge observation during failure

Gauge query:

```
gateway:sli_availability:ratio_rate5m * 100
```

During the payments outage the gauge dropped below the 99.5 threshold; burn rate rose above 1 (burning budget too fast).

---

## Bonus Task — Correlate Failure Across Metrics & Logs

### Setup

```bash
./loadgen/run.sh 5 60 &
sleep 30
PAYMENT_FAILURE_RATE=0.5 PAYMENT_LATENCY_MS=1000 \
  docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --no-deps payments
```

### Timeline

| Time (UTC) | Event |
|------------|-------|
| `20:07:37` | Load generator started (`./loadgen/run.sh 5 60`) |
| `20:08:07` | Fault injected — payments restarted with `PAYMENT_FAILURE_RATE=0.5`, `PAYMENT_LATENCY_MS=1000` |
| `20:08:14.570` | **First error in payments logs** — injected payment failure |
| `20:08:14.572` | **Gateway propagates failure** — `/charge` returns 500 (~2 ms later) |
| `~20:08:30` | **Dashboard spike** — Error Rate panel rises (Prometheus scrape interval 15s; `gateway_requests_total{status=~"5.."}` rate increases) |
| `20:08:32` | PromQL error rate query: `1.15%` (`sum(rate(...5..)[1m]) / sum(rate(...)[1m]) * 100`) |
| `20:08:37` | Load generator at 50s: `error_rate=15.5%` (cumulative, includes pre-injection failures) |
| `20:08:37` | Recovery — payments restored to `PAYMENT_FAILURE_RATE=0.0`, `PAYMENT_LATENCY_MS=0` |

### Log excerpts at failure moment

**payments:**
```
payments-1  | 2026-06-18T20:08:14.570818920Z {"time":"2026-06-18 20:08:14,570","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 44e3a117-8e37-4035-8270-489f678930f4"}
payments-1  | 2026-06-18T20:08:14.571558837Z INFO:     172.18.0.8:42096 - "POST /charge HTTP/1.1" 500 Internal Server Error
```

**gateway:**
```
gateway-1   | 2026-06-18T20:08:14.572859087Z {"time":"2026-06-18 20:08:14,572","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge "HTTP/1.1 500 Internal Server Error""}
gateway-1   | 2026-06-18T20:08:14.574368004Z INFO:     192.168.65.1:48336 - "POST /reserve/44e3a117-8e37-4035-8270-489f678930f4/pay HTTP/1.1" 500 Internal Server Error
```

### Root cause

The failure was **injected inside the payments service** (`PAYMENT_FAILURE_RATE=0.5` causes ~50% of `/charge` calls to fail; `PAYMENT_LATENCY_MS=1000` adds 1s delay). Payments logs the warning first, returns HTTP 500, gateway forwards that as a 500 to the client, and Prometheus records it via `gateway_requests_total{status="500"}` — which drives the Error Rate panel and SLO burn rate. Logs showed the root cause **before** the dashboard spike because metrics are aggregated over a scrape/rate window (~15–60s), while logs are immediate.

