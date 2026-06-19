# Lab 3 — Monitoring, Observability & SLOs

## Task 1 — Configure Monitoring & Build Dashboard

### 3.1 — Prometheus configuration

`monitoring/prometheus/prometheus.yml` scrapes the three QuickTicket services on
their **internal** ports (the published `3080` etc. are not used inside the Docker
network — Compose service names resolve as hostnames):

```yaml
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

### 3.2 — Stack deployed (all 7 services)

```
NAME               IMAGE                     SERVICE      STATUS
app-gateway-1      app-gateway               gateway      Up 6 minutes
app-events-1       app-events                events       Up 6 minutes
app-payments-1     app-payments              payments     Up 2 seconds
app-postgres-1     postgres:17-alpine        postgres     Up 6 minutes (healthy)
app-redis-1        redis:7-alpine            redis        Up 6 minutes (healthy)
app-prometheus-1   prom/prometheus:v3.11.2   prometheus   Up 6 minutes
app-grafana-1      grafana/grafana:13.0.1    grafana      Up 6 minutes
```

### 3.3 — Prometheus targets (all `up`)

```
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

### 3.4 — Custom metrics exposed

```
gateway_requests_total
gateway_request_duration_seconds_bucket / _count / _sum
events_requests_total
events_request_duration_seconds_bucket / _count / _sum
events_db_pool_size
events_reservations_active
events_orders_total / events_orders_created
payments_requests_total
payments_request_duration_seconds_bucket / _count / _sum
payments_charges_total
```

### PromQL query output — request rate (Traffic golden signal)

```
sum(rate(gateway_requests_total[5m]))  ->  Request rate: 0.45 req/s
```

### 3.5 — Dashboard panels (PromQL used)

The dashboard `monitoring/grafana/dashboards/golden-signals.json` is provisioned
into Grafana automatically. Verified via the Grafana API that all 7 panels load:

```
timeseries  Request Rate (Traffic)
timeseries  Error Rate
table       Service Health (up/down)
timeseries  Latency (Golden Signal #1)         <- added
gauge       Saturation (Golden Signal #4) — DB Pool   <- added
gauge       SLO — Availability (%)              <- added (Task 2)
timeseries  SLO — Error Budget Burn Rate        <- added (Task 2)
```

**Latency panel (Golden Signal #1)** — Time series, unit `seconds`, 3 queries:

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))   # p50
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))   # p95
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))   # p99
```

Baseline values under normal traffic: p50 ≈ 2.9 ms, p95 ≈ 8.2 ms, p99 ≈ 9.6 ms.

**Saturation panel (Golden Signal #4)** — Gauge, min 0 / max 10, thresholds
green (default) / yellow @ 7 / red @ 9:

```promql
events_db_pool_size
```

Baseline value: 0 (connections are returned to the pool immediately after each
query, so the in-use count idles near 0 under light load).

### 3.6 / 3.7 — Failure injection & observations

Generated steady traffic (`./loadgen/run.sh 5 110`), killed payments at **T+15s**,
watched for ~80s, then restarted. Polled Prometheus every 10s:

```
T0   00:04:10  traffic start
+15s 00:04:25  docker compose stop payments
+25s 00:04:36  err_rate=0.0%  availability=100.00%  burn_rate=0.0
+35s 00:04:46  err_rate=5.1%  availability=100.00%  burn_rate=0.0   <- first detection
+45s 00:04:56  err_rate=7.3%  availability=100.00%  burn_rate=0.0
+55s 00:05:06  err_rate=6.2%  availability=100.00%  burn_rate=0.0
+65s 00:05:16  err_rate=7.1%  availability=100.00%  burn_rate=0.0
+75s 00:05:26  err_rate=7.8%  availability=100.00%  burn_rate=0.0
+85s 00:05:36  err_rate=6.0%  availability=94.92%   burn_rate=10.2  <- SLI gauge drops
+95s 00:05:46  err_rate=5.6%  availability=94.92%   burn_rate=10.2
     00:05:46  docker compose start payments
```

**Normal traffic:** error rate 0%, availability 100%, latency single-digit ms,
DB pool gauge ~0. **During payments failure:** the gateway returns 5xx for the
~10% of requests that hit the full reserve→pay flow, so the Error Rate panel rose
to ~5–8%. The Saturation (DB pool) and Latency panels barely moved — payments
being down does not load the events DB and the failed calls return quickly.

### Which golden signal showed the failure first? How long after killing payments?

**Errors** (Golden Signal #3) detected it first. Payments was stopped at T+15s and
the Error Rate panel rose from 0% to 5.1% by T+35s — about **~20 seconds** after
the kill. That delay is the Prometheus scrape interval (15s) plus the `[1m]` rate
window beginning to fill. The recording-rule based availability SLI uses a `[5m]`
window evaluated every 30s, so it lagged further (visible drop ~70s after the kill).
Traffic, Latency and Saturation did not signal the outage at all in this scenario,
which matches the failure mode: a downstream dependency returning errors, not a
capacity or latency problem.

---

## Task 2 — Define SLOs & Recording Rules

### 3.8 — SLIs / SLOs and error budget math

**SLI 1 — Availability:** fraction of gateway requests returning non-5xx.
- **SLO target: 99.5%** over a rolling 7-day window.
- Error budget = 100% − 99.5% = **0.5%** of requests may fail.
- At ~1000 req/day → 7000 req/week. Budget = 0.5% × 7000 = **35 failed requests/week**.

**SLI 2 — Latency:** fraction of gateway requests completing under 500 ms.
- **SLO target: 95%.**
- Budget = 5% of requests may exceed 500 ms = 0.05 × 7000 = **350 slow requests/week**.

### 3.9 — Recording rules

`monitoring/prometheus/rules.yml` defines three rules (group `slo_rules`,
interval 30s), and `rules.yml` is referenced via `rule_files:` in
`prometheus.yml` and mounted into the Prometheus container through
`docker-compose.monitoring.yaml`:

```promql
gateway:sli_availability:ratio_rate5m
  = sum(rate(gateway_requests_total{status!~"5.."}[5m]))
  / sum(rate(gateway_requests_total[5m]))

gateway:sli_latency_500ms:ratio_rate5m
  = sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m]))
  / sum(rate(gateway_request_duration_seconds_count[5m]))

gateway:error_budget_burn_rate:ratio_rate5m
  = (1 - gateway:sli_availability:ratio_rate5m) / (1 - 0.995)
```

Rules loaded (`/api/v1/rules`):

```
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

Baseline values under healthy traffic: availability = 1 (100%), latency SLI = 1
(100%), burn rate = 0.

### 3.10 — SLO gauge observation during failure

The **"SLO — Availability (%)"** gauge (`gateway:sli_availability:ratio_rate5m * 100`,
min 99 / max 100, green threshold @ 99.5) dropped to **94.92%** during the payments
outage above (well below the 99.5% line, turning the gauge red), while the
**burn-rate** panel spiked to **10.2** (>1 = consuming error budget far faster than
the SLO allows). Both recovered to 100% / 0 once payments was restarted.

---

## Bonus Task — Correlate Failure Across Metrics & Logs

Started traffic, and at **T+30s** recreated payments with
`PAYMENT_FAILURE_RATE=0.5 PAYMENT_LATENCY_MS=1000`.

### Timeline

```
00:06:28  T0     traffic start
00:06:58  T+30s  INJECT: payments restarted with failure_rate=0.5, latency=1000ms
00:07:32         first "Injecting 1000ms latency" log line in payments
00:07:43         first "Payment failed (injected)" -> POST /charge 500 in payments logs
00:07:43         gateway logs corresponding POST http://payments:8082/charge -> 500
00:07:59  +90s   gateway 5xx error rate rises on the dashboard (0% -> 0.6%)
                 payments p99 latency ~2480 ms throughout (injected 1000ms delay)
00:08:11         payments reset to clean state -> errors stop
```

### Log excerpts at the failure moment

payments:
```
{"level":"INFO","service":"payments","msg":"Injecting 1000ms latency for b6586880-..."}
{"level":"WARNING","service":"payments","msg":"Payment failed (injected) for b6586880-..."}
INFO:  172.18.0.8 - "POST /charge HTTP/1.1" 500 Internal Server Error
```

gateway (same reservation id, ~same second):
```
{"level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 500 Internal Server Error\""}
INFO:  172.18.0.1 - "POST /reserve/dfe0e7b0-.../pay HTTP/1.1" 500 Internal Server Error
```

### Root cause

The injected `PAYMENT_FAILURE_RATE=0.5` makes the payments service deterministically
return HTTP 500 for ~half of `/charge` calls (logged as `Payment failed (injected)`),
and `PAYMENT_LATENCY_MS=1000` adds a 1s sleep to every charge (payments p99 ~2.4s).
The gateway calls payments synchronously during the `/reserve/{id}/pay` step; a 500
from payments propagates straight back to the client as a 500 on `…/pay`. Because
only ~10% of generated traffic exercises the full pay flow, the **gateway-level**
5xx rate stayed low (≤0.6%) even though **payments' own** error rate and latency
spiked immediately — the metrics→logs trail (gateway 500 ↔ payments
"Payment failed (injected)", matched by reservation id and timestamp) pinpoints the
payments dependency as the root cause, not the gateway itself.
