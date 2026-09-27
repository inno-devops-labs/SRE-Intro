## Task 1 Configure Monitoring & Build Dashboard (6 pts)

1. Output of compose ps showing all 7 services
```bash
$ docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
NAME               IMAGE                     COMMAND                  SERVICE      CREATED          STATUS                    PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       35 minutes ago   Up 35 minutes             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      35 minutes ago   Up 35 minutes             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      35 minutes ago   Up 35 minutes             0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     35 minutes ago   Up 35 minutes             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     35 minutes ago   Up 35 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   35 minutes ago   Up 35 minutes             0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        35 minutes ago   Up 35 minutes (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

2. Prometheus targets output (all 3 `up`)

```bash
$ curl -s http://localhost:9090/api/v1/targets | python3 -c "
import sys, json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(f\"{t['labels']['job']:12} {t['health']:8} {t['scrapeUrl']}\")
"
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

3. Custom metrics list

```bash
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

4. PromQL query output (request rate)

```bash
$ curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))' \
  http://localhost:9090/api/v1/query | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(f\"Request rate: {float(r['data']['result'][0]['value'][1]):.2f} req/s\")"
Request rate: 0.32 req/s
```

5. PromQL queries you used for Latency and Saturation panels

Latency panel:
```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

Saturation panel:
```promql
events_db_pool_size
```

6. Dashboard observations: normal traffic vs payments failure

* Normal state: error rate remained at 0%, p99 latency hovered around 21 ms, availability stayed at 100%, and the error budget burn rate was 0.
* During payments outage: the gateway could no longer communicate with the payments downstream, causing payment checkout calls (~10% of total traffic) to fail and lifting the error rate to approximately 10%. Latency did not experience a noticeable surge because failing connection requests terminated immediately instead of blocking until timeout. The database connection pool (`events_db_pool_size`) stayed flat at 0, confirming that the events persistence tier was unaffected. The 5-minute rolling SLI metrics (availability and burn rate) reacted smoothly, beginning to reflect degradation after roughly 75–90 seconds.

7. Answer: "Which golden signal showed the failure first? How long after killing payments?"

The Errors golden signal was the first to detect the failure. After stopping the payments service, non-zero errors manifested in the 1-minute rate metric within roughly 55–65 seconds. This reaction window matches the scrape interval (15s), the sliding rate window calculation, and the frequency of checkout requests. Latency and saturation showed no major changes due to immediate fast-failing connections and isolated service architecture. The 5-minute rolling availability SLI showed the slowest response due to its wider smoothing window.

## Task 2 — Define SLOs & Recording Rules

SLI 1 — Availability: % of gateway requests returning non-5xx
SLO target: 99.5% over a 7-day window

SLI 2 — Latency: % of gateway requests completing under 500ms
SLO target: 95%

Error budget math (assuming ~1000 requests/day):
Weekly volume: 1000 × 7 = 7,000 requests/week
Availability budget: (1 − 0.995) × 7,000 = 0.005 × 7,000 = 35 failed requests/week allowed
Latency budget: (1 − 0.95) × 7,000 = 0.05 × 7,000 = 350 "slow" (≥500ms) requests/week allowed

`rules.yml`:
```yaml
groups:
  - name: slo_rules
    interval: 30s
    rules:
      # SLI 1 — Availability: share of gateway requests that are NOT 5xx.
      - record: gateway:sli_availability:ratio_rate5m
        expr: |
          sum(rate(gateway_requests_total{status!~"5.."}[5m]))
          /
          sum(rate(gateway_requests_total[5m]))

      # SLI 2 — Latency: share of gateway requests completing under 500ms.
      - record: gateway:sli_latency_500ms:ratio_rate5m
        expr: |
          sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m]))
          /
          sum(rate(gateway_request_duration_seconds_count[5m]))

      # Error budget burn rate against the 99.5% availability SLO.
      - record: gateway:error_budget_burn_rate:ratio_rate5m
        expr: |
          (1 - gateway:sli_availability:ratio_rate5m) / (1 - 0.995)
```

```bash
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

During testing, stopping the payments service for 60 seconds under load caused the availability metric to drop below the 99.5% SLO threshold. Once restored, the gauge recovered back toward 100% progressively over 5 minutes due to the rolling rate window.

## Bonus Task — Correlate Failure Across Metrics & Logs

### Setup

```bash
./loadgen/run.sh 5 120 &
```

After ~30s, fault injection was applied by restarting `payments` with:
```bash
PAYMENT_FAILURE_RATE=0.5 PAYMENT_LATENCY_MS=1000
```

The system was monitored in Grafana for ~2 minutes while streaming container logs.

---

## Timeline (metrics → logs correlation)

```text
T+0s        loadgen started (5 RPS)

T+30s       fault injection applied:
            payments restarted with:
            PAYMENT_FAILURE_RATE=0.5
            PAYMENT_LATENCY_MS=1000

T+~30–40s   payments logs capture artificial delay and errors:
            → "Injecting 1000ms latency for 8e12b041"
            → "Payment failed (injected) for 8e12b041"

T+~40–60s   gateway registers failed downstream charges:
            → HTTP 500 on /pay endpoints
            → error rate starts climbing in Prometheus

T+~60–90s   dashboard shows clear degradation:
            → p99 latency jumps to ~2.05s
            → availability drops below 99.5% SLO target

T+~90–120s  steady degraded equilibrium under sustained chaos:
            → stable elevated latency and 5xx rate
```

---

## Log excerpts

### Payments service (root cause source)

```text
payments-1 | 2026-09-28 15:12:09 {"level":"INFO","msg":"Injecting 1000ms latency for 7c10b429"}
payments-1 | 2026-09-28 15:12:10 {"level":"WARNING","msg":"Payment failed (injected) for 7c10b429"}
payments-1 | 2026-09-28 15:12:11 {"level":"INFO","msg":"Injecting 1000ms latency for d491a812"}
payments-1 | 2026-09-28 15:12:12 {"level":"WARNING","msg":"Payment failed (injected) for d491a812"}
payments-1 | 2026-09-28 15:12:13 {"level":"INFO","msg":"Payment success: PAY-D912E401 for e817cb21"}
```

---

### Gateway service (propagation point)

```text
gateway-1 | POST /reserve/3f12b84a-91dc-4a11-8201-9bc124ad014a/pay HTTP/1.1" 500 Internal Server Error
gateway-1 | POST http://payments:8082/charge "HTTP/1.1 500"
gateway-1 | POST /reserve/4c81a29d-114a-4ecb-9912-88da12bc7190/pay HTTP/1.1" 500 Internal Server Error
```

---

## Metrics during failure

```text
SLO Availability (5m):     ~98.6%   (below 99.5% target)
Error Rate (5xx):          ~1–5%    (visible spike following injection)
P99 Latency:               ~2.05s   (increased from ~20ms baseline)
Error Budget Burn Rate:     >1.0    (consuming budget faster than allowed)
```

---

## Correlation table

| Stage            | Signal                                     | Source             |
| ---------------- | ------------------------------------------ | ------------------ |
| Injection starts | payments logs: latency + failure injection | payments container |
| First impact     | HTTP 500 errors appear                     | gateway logs       |
| Metric spike     | error rate + p99 latency rise              | Prometheus/Grafana |
| SLO breach       | availability < 99.5%                       | 5m rolling SLI     |
| Recovery         | metrics stabilize after restart/window     | Grafana            |

---

## Root cause

The degradation was triggered by restarting `payments` with chaos parameters:
* `PAYMENT_FAILURE_RATE=0.5` (50% failure probability on charges)
* `PAYMENT_LATENCY_MS=1000` (1000 ms synthetic delay)

This caused the downstream `/charge` endpoint to return HTTP 500 and hold open connections, which propagated to the gateway as elevated p99 latency (~2s) and 500 errors. Because availability is calculated on a 5-minute rolling rate window, the dashboard SLI showed degradation with a minor smoothing delay.

---

## Key insight

* Application logs immediately captured the precise moment of fault injection.
* Gateway logs reflected downstream error propagation right away.
* Metrics smoothed the impact due to 1-minute and 5-minute rate evaluation windows.
* Latency degradation originated from synchronous downstream delay rather than CPU/memory resource exhaustion.
