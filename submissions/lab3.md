# Lab 3 — Monitoring, Observability & SLOs

## Made by:
### Nurmuhametov Denis (d.nurmuhametov@innopolis.university)

---

## Task 1 — Configure Monitoring & Build Dashboard (6 pts)

### 3.1: Prometheus Configuration

Created `monitoring/prometheus/prometheus.yml` with scrape targets for all three QuickTicket services:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "gateway"
    static_configs:
      - targets: ["gateway:8080"]

  - job_name: "events"
    static_configs:
      - targets: ["events:8081"]

  - job_name: "payments"
    static_configs:
      - targets: ["payments:8082"]
```

### 3.2: All 7 Services Running

```text
$ docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
NAME               IMAGE                     COMMAND                  SERVICE      STATUS                    PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       Up About an hour          0.0.0.0:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      Up About an hour          0.0.0.0:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      Up 23 minutes             0.0.0.0:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     Up About a minute         0.0.0.0:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     Up (healthy)              0.0.0.0:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   Up About an hour          0.0.0.0:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        Up (healthy)              0.0.0.0:6379->6379/tcp
```

All 7 services are running across both compose files: 5 QuickTicket services + Prometheus + Grafana.

### 3.3: Prometheus Scraping All Targets

```text
$ curl -s http://localhost:9090/api/v1/targets | python3 -c "
import sys, json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(f\"{t['labels']['job']:12} {t['health']:8} {t['scrapeUrl']}\")
"
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

All three targets are `up`. Prometheus is successfully scraping metrics from every service.
### 3.4: Custom Metrics

```text
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

All three services expose custom metrics. Gateway has request counters and duration histograms; events add DB pool gauge and active reservations; payments has charge counters.

### 3.5: Request Rate

```text
$ ./loadgen/run.sh 5 20
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 20s
---
[10s] requests=42 success=41 fail=1 error_rate=2.3%
[10s] requests=43 success=42 fail=1 error_rate=2.3%
...
Done. total=83 success=83 fail=0 error_rate=0.0%

$ curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))' \
  http://localhost:9090/api/v1/query | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(f\"Request rate: {float(r['data']['result'][0]['value'][1]):.2f} req/s\")
"
Request rate: 0.83 req/s
```

With 5 RPS loadgen running, Prometheus reports ~0.83 req/s averaged over 5 minutes (lower due to the rate window including idle time).

### 3.6: Dashboard Panels

The **QuickTicket — Golden Signals** dashboard was completed by replacing two placeholder panels:

**Latency panel** (Time series, unit: seconds):
```promql
# p50
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
# p95
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
# p99
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

**Saturation panel** (Gauge, min: 0, max: 10, thresholds at 7/9):
```promql
events_db_pool_size
```

### 3.7: Observations & Analysis


**Normal traffic observation:** With 5 RPS load running, the dashboard shows Request Rate stable at ~4-5 req/s, Error Rate at 0% (all requests are 200s), Latency well under 50ms for all percentiles, and Saturation at 0-1 active DB connections (far below the pool limit of 10).

**Failure experiment (payments killed):** After running `docker compose stop payments` during active load:
- **Error Rate** jumped from 0% to ~12% within the first scrape interval (~15s). The 10% of requests hitting `/reserve/{id}/pay` immediately returned HTTP 503.
- **Request Rate** dropped slightly as failed requests consumed fewer resources.
- **Service Health** showed `payments = 0` (down) on the next scrape.
- **Latency** for the p50/p99 remained largely unchanged (successful requests unaffected).
- **Saturation** showed no change (DB pool independent of payments).

**Which golden signal showed the failure first?** Error Rate detected the failure first — it jumped from 0% to approximately 12% within the first 15-second scrape interval after stopping payments, as every `/reserve/{id}/pay` request immediately returned a 503 error. Service Health confirmed the failure on the same scrape by showing the `up` metric for payments as 0.

---

## Task 2 — Define SLOs & Recording Rules (4 pts)

### 3.8: SLI and SLO Definitions

**SLI 1 — Availability:** Percentage of gateway requests returning non-5xx status codes.
- **SLO target:** 99.5% over a 7-day rolling window.
- **Error budget:** With ~1000 requests/day, over 7 days that's ~7000 requests. The error budget allows `(1 - 0.995) × 7000 = 35` failed requests per week.

**SLI 2 — Latency:** Percentage of gateway requests completing in under 500ms.
- **SLO target:** 95% over a 7-day rolling window.
- **Error budget:** `(1 - 0.95) × 7000 = 350` slow requests per week.

### 3.9: Recording Rules

Created `monitoring/prometheus/rules.yml`:

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

Added `rule_files` to `prometheus.yml` and mounted the rules file in `docker-compose.monitoring.yaml`.

**Rules loaded verification:**

```text
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

All three recording rules are loaded and healthy.

### 3.10: SLO Dashboard Panel

Added a Gauge panel to the dashboard with query `gateway:sli_availability:ratio_rate5m * 100`, min 99, max 100, threshold at 99.5

**Failure experiment (stop payments for several minutes with 5 RPS load):**

| Metric | Before | After (payments stopped)            |
|--------|--------|-------------------------------------|
| SLO Availability | ~99.8% | **98.3%** ❌ (below 99.5% threshold) |
| Error Budget Burn Rate | 0.0 | **3.28** (> 1.0 = exceeding budget) |


**Observation:** With payments stopped, the SLO gauge dropped well below the 99.5% threshold to **98.46%**, turning red. The burn rate of **3.28** indicates the error budget was being consumed 3.28× faster than the SLO allows. The dashboard correctly identified the SLO violation in real-time, confirming that the recording rules and Grafana panel are properly configured.

---

## Bonus Task — Correlate Failure Across Metrics & Logs (2 pts)

### Experiment Setup

1. Start load generator at 5 RPS for 120 seconds
2. After 30 seconds, restart `payments` with fault injection:
   - `PAYMENT_FAILURE_RATE=0.5` (50% of charges fail)
   - `PAYMENT_LATENCY_MS=1000` (1 second artificial delay per charge)
3. Let the fault injection run, then restore payments
4. Observe dashboard and collect logs

### Timeline

```text
T+0s       (19:58:07)  loadgen started: ./loadgen/run.sh 5 120
T+30s      (19:58:37)  first injected latency log in payments:
                        → "Injecting 1000ms latency for 27e3410c"
T+36s      (19:58:43)  first 500 error logged in gateway:
                        → "POST /reserve/88132310-.../pay HTTP/1.1" 500
T+36-99s   (19:58:43–19:59:46) sustained 500s from injected failures
T+99s      (19:59:46)  last 500 error recorded
```

### Log Excerpts

**Gateway — 500 errors during fault injection:**

```text
gateway-1  | 2026-06-18T19:58:43.286332976Z POST http://payments:8082/charge "HTTP/1.1 500"
gateway-1  | 2026-06-18T19:58:43.288073319Z POST /reserve/88132310-.../pay HTTP/1.1" 500
gateway-1  | 2026-06-18T19:58:44.983442402Z POST http://payments:8082/charge "HTTP/1.1 500"
gateway-1  | 2026-06-18T19:58:44.984623699Z POST /reserve/6b40ad55-.../pay HTTP/1.1" 500
gateway-1  | 2026-06-18T19:59:14.208855717Z POST http://payments:8082/charge "HTTP/1.1 500"
gateway-1  | ... total 8x 500 errors from /reserve/{id}/pay
```

**Payments — injected latency and failures:**

```text
payments-1 | 19:58:37 {"level":"INFO",    "msg":"Injecting 1000ms latency for 27e3410c"}
payments-1 | 19:58:38 {"level":"WARNING", "msg":"Payment failed (injected) for 27e3410c"}
payments-1 | 19:58:42 {"level":"INFO",    "msg":"Injecting 1000ms latency for 88132310"}
payments-1 | 19:58:43 {"level":"WARNING", "msg":"Payment failed (injected) for 88132310"}
payments-1 | 19:58:45 {"level":"INFO",    "msg":"Injecting 1000ms latency for 826d6ba0"}
payments-1 | 19:58:46 {"level":"INFO",    "msg":"Payment success: PAY-20E89E7E for 826d6ba0"}
payments-1 | 19:59:13 {"level":"INFO",    "msg":"Injecting 1000ms latency for a6fa91dc"}
payments-1 | 19:59:14 {"level":"WARNING", "msg":"Payment failed (injected) for a6fa91dc"}
payments-1 | 19:59:45 {"level":"INFO",    "msg":"Injecting 1000ms latency for c8a2395b"}
payments-1 | 19:59:46 {"level":"WARNING", "msg":"Payment failed (injected) for c8a2395b"}
```

Total: 15 charges attempted, 9 failed (injected), 6 succeeded — consistent with 50% failure rate. Every charge had 1s latency injected.

### Prometheus Metrics During Failure

```text
$ curl -s 'http://localhost:9090/api/v1/query?query=gateway:sli_availability:ratio_rate5m'
SLO Availability:     98.78%  ↓ (below 99.5% threshold — RED)

$ curl -s '...query=gateway:error_budget_burn_rate:ratio_rate5m'
Error Budget Burn:    2.44    ↑ (> 1.0 — exceeding budget 2.44×)

$ curl -s '...query=histogram_quantile(0.99, ...)'
P99 Latency:          2.07s   ↑ (from ~30ms baseline — 69× increase)

$ curl -s '...query=sum(rate(gateway_requests_total{status=~"5.."}[5m])) / ...'
5xx Error Rate:       1.24%   ↑ (from 0% baseline)
```

| Metric | Baseline | During Injection | Impact |
|--------|----------|-----------------|--------|
| SLO Availability | ~99.8% | **98.78%** | ❌ Below 99.5% — breached |
| Burn Rate | 0.0 | **2.44** | Consuming budget 2.44× faster than allowed |
| P99 Latency | ~30ms | **2.07s** | 69× increase due to 1s artificial delay |
| 5xx Error Rate | 0% | **1.24%** | 8 gateway 500s from 50% failure injection |

### Root Cause Analysis

The failure propagation path:

```
User → GET /events (200)  ← 90% of traffic, unaffected
User → POST /reserve/{id} (200)
         ↓
       POST /payments/charge
         ↓
       PAYMENT_LATENCY_MS=1000 → 1s sleep
         ↓
       random.random() < 0.5?
         ├── Yes → HTTP 500 → gateway logs 500 → Prometheus: status="500"++
         └── No  → HTTP 200 (after 1s delay) → gateway p99 latency spikes
```

**Key observations**

The **Error Rate** showed the failure first — within ~6 seconds of the first injection (`19:58:37` → first 500 at `19:58:43`). Since only errors from `/pay` requests reveal the fault (90% of traffic is GET /events, unaffected), the error rate jumped from 0% to a noticeable level on the first Prometheus scrape.

**P99 Latency** was the second indicator — it rose from ~30ms to **2.07s** (69× increase), visible as soon as the first injected charges completed after the 1-second sleep.

**SLO Availability** responded slowest because the 5-minute rate window includes healthy traffic before and after the injection window. The burn rate of **2.44** confirms the dashboard correctly detected the SLO breach.

**Key insight:** Every failed payment in the gateway logs (`HTTP/1.1 500`) maps 1:1 to a `"Payment failed (injected)"` warning in the payments logs. The timestamps align within milliseconds — full traceability from dashboard → gateway logs → payments logs.
