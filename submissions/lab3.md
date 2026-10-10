# Lab 3 Submission — Monitoring, Observability & SLOs

## Task 1 — Configure Monitoring & Build Dashboard

### 3.2: All 7 services running

```
$ cd app/
$ docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
NAME               IMAGE                     COMMAND                  SERVICE      CREATED          STATUS                    PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       23 minutes ago   Up 23 minutes             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      23 minutes ago   Up 23 minutes             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      23 minutes ago   Up 7 minutes              0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     23 minutes ago   Up 54 seconds             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     23 minutes ago   Up 23 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   23 minutes ago   Up 23 minutes             0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        23 minutes ago   Up 23 minutes (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

---

### 3.3: Prometheus targets (all 3 up)

```
$ curl -s http://localhost:9090/api/v1/targets | python3 -c "
import sys, json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(f\"{t['labels']['job']:12} {t['health']:8} {t['scrapeUrl']}\")
"
events-metrics up       http://events:8081/metrics
gateway-metrics up       http://gateway:8080/metrics
payments-metrics up       http://payments:8082/metrics
```

---

### 3.4: Custom metrics list

```
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

Request rate after generating traffic:

```
$ curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))' \
  http://localhost:9090/api/v1/query | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(f\"Request rate: {float(r['data']['result'][0]['value'][1]):.2f} req/s\")"
Request rate: 3.49 req/s
```

---

### 3.5: Golden signals dashboard panels

Replaced the two placeholder panels in **QuickTicket — Golden Signals** (`http://localhost:3000`).

**Latency panel (Time series, unit: seconds):**

```promql
# p50
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))

# p95
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))

# p99
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

**Saturation panel (Gauge, min 0 / max 10, thresholds green / yellow@7 / red@9):**

```promql
events_db_pool_size
```

---

### 3.6–3.7: Failure injection & observations

Generated steady traffic, then stopped payments:

```bash
./loadgen/run.sh 5 60 &   # used Python load generator (bc not available locally)
sleep 15
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
# watched dashboard ~2 min, then:
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start payments
```

**Normal traffic (baseline):**

| Signal | Value |
|--------|-------|
| Request rate | ~2.1 req/s |
| Error rate | 0% |
| p99 latency | ~0.023 s |
| payments `up` | 1 |
| DB pool (`events_db_pool_size`) | 0 |

**After `docker compose stop payments` (~2 min observation):**

| Time after kill | Error rate | p99 latency | payments `up` | DB pool |
| --------------: | ---------: | ----------: | ------------: | ------: |
|             +1s |         0% |     0.023 s |             1 |       0 |
|            +13s |         0% |     0.021 s |             1 |       0 |
|            +19s |      13.7% |     0.074 s |             0 |       0 |
|            +37s |      23.4% |     0.074 s |             0 |       0 |
|            +79s |      25.0% |     0.092 s |             0 |       0 |

**After restart:**

Error rate dropped from ~25% toward ~17% within one scrape cycle as `payments up` returned to 1. Latency followed the same trend (p99 back toward baseline).

**Which golden signal showed the failure first? How long after killing payments?**

**Service Health** (payments target `up=0`) and **Error Rate** both showed the failure at the **same time — ~19 seconds** after stopping payments. This lines up with Prometheus's 15 s scrape interval: the first post-kill scrape that included failed payment requests also marked the payments target as down.

Latency jumped in that same scrape window (p99 went from ~0.023 s to ~0.074 s). **Saturation** (`events_db_pool_size`) did not reflect the failure — it stayed at 0 throughout, since the outage was in the payments service, not DB connection pressure on events.

---

## Task 2 — Define SLOs & Recording Rules

### 3.8: SLI/SLO definitions & error budget

| SLI | Definition | SLO target |
|-----|------------|------------|
| **Availability** | % of gateway requests returning non-5xx | **99.5%** over 7 days |
| **Latency** | % of gateway requests completing under 500 ms | **95%** |

**Error budget math (availability):**

- Allowed error rate = 100% − 99.5% = **0.5%**
- ~1000 requests/day × 7 days = **7000 requests/week**
- Allowed failures = 7000 × 0.005 = **35 failed requests per week**

---

### 3.9: Recording rules loaded

Created `monitoring/prometheus/rules.yml` and mounted it in `docker-compose.monitoring.yaml`.

```
$ docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml restart prometheus
$ curl -s http://localhost:9090/api/v1/rules | python3 -c "
import sys, json
for g in json.load(sys.stdin)['data']['groups']:
    for r in g['rules']:
        print(f\"{r['name']:45} = {r.get('health', 'N/A')}\")
"
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

**Recording rules:**

```yaml
# Availability SLI
gateway:sli_availability:ratio_rate5m =
  sum(rate(gateway_requests_total{status!~"5.."}[5m]))
  / sum(rate(gateway_requests_total[5m]))

# Latency SLI (% under 500ms)
gateway:sli_latency_500ms:ratio_rate5m =
  sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m]))
  / sum(rate(gateway_request_duration_seconds_count[5m]))

# Error budget burn rate (>1 = burning too fast)
gateway:error_budget_burn_rate:ratio_rate5m =
  (1 - gateway:sli_availability:ratio_rate5m) / (1 - 0.995)
```

---

### 3.10: SLO gauge during failure

Added gauge panel: `gateway:sli_availability:ratio_rate5m * 100` (min 99, max 100, threshold at 99.5%).

Stopped payments for 60 s under steady traffic:

| Phase | SLO availability | Burn rate |
|-------|-----------------:|----------:|
| Baseline | 100.00% | 0.00 |
| Outage +34s | **91.70%** | **16.61** |
| Outage +56s | 91.70% | 16.61 |
| Recovery | 91.61% | 16.78 |

The gauge dropped well below the 99.5% SLO threshold once failed `/pay` requests entered the 5-minute rate window (~34 s after kill). Burn rate jumped to **16.6×** — meaning we were consuming the weekly error budget 16× faster than sustainable.

---

## Bonus Task — Correlate Failure Across Metrics & Logs

### Procedure

```bash
# steady traffic ~5 rps for 120s
./loadgen/run.sh 5 120 &    # used Python load generator
sleep 30
PAYMENT_FAILURE_RATE=0.5 PAYMENT_LATENCY_MS=1000 \
  docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
# watched Grafana ~2 min, then restored PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0
```

### Timeline

| Time (UTC) | Event |
|------------|-------|
| 20:47:29 | Traffic started |
| **20:47:59** | **Failure injection** — payments restarted with `PAYMENT_FAILURE_RATE=0.5`, `PAYMENT_LATENCY_MS=1000` |
| **20:48:03** | **First error in gateway logs** — `POST http://payments:8082/charge` returns 500 |
| **20:48:20** (+25s) | **Latency spike on dashboard** — p99 jumps from 0.034 s → **1.892 s** (1 s injected delay) |
| 20:48:38 | Continued 500s from payments (~50% failure rate on `/charge`) |
| **20:49:05** (+70s) | **Error rate spike on dashboard** — crosses 2% (only ~10% of traffic hits `/pay`, so overall error % rises slowly) |
| 20:50:00 | Payments restored to healthy env vars |

### Log excerpts at failure moment

**Payments** (`20:48:03` window — latency injected, then random failure):

```
{"time":"2026-06-19 20:48:02,...","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for <reservation_id>"}
{"time":"2026-06-19 20:48:03,...","level":"WARNING","service":"payments","msg":"Payment failed (injected) for <reservation_id>"}
INFO: ... "POST /charge HTTP/1.1" 500 Internal Server Error
```

**Gateway** (propagates payments 500 to client):

```
{"time":"2026-06-19 20:48:03,627","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 500 Internal Server Error\""}
INFO: ... "POST /reserve/.../pay HTTP/1.1" 500 Internal Server Error
```

### Root cause

The failure originated in **payments**: env vars caused each `/charge` call to sleep 1 s (`PAYMENT_LATENCY_MS=1000`) and return 500 on ~50% of attempts (`PAYMENT_FAILURE_RATE=0.5`). Gateway forwarded these as 500 responses on `/reserve/{id}/pay`, which incremented `gateway_requests_total{status="500"}` and inflated `gateway_request_duration_seconds` histogram buckets.

**Metrics → logs correlation:** Latency appeared on the dashboard first (+25 s) because the 1 s sleep affects every charge call immediately. Error rate lagged (+70 s) because only ~10% of loadgen traffic reaches `/pay` — the 50% charge failure rate translates to ~1–3% overall gateway error rate, and the 1-minute `rate()` window smooths the signal further. Logs confirmed the root cause at **20:48:03** — before either dashboard spike — showing injected latency and failure in payments before gateway recorded the 500.
