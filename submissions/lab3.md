# Lab 3 — Monitoring, Observability & SLOs

## Task 1 — Configure Monitoring & Build Dashboard

### 3.1 — `prometheus.yml`

Created at `monitoring/prometheus/prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "rules.yml"

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

Each service is scraped by its Docker Compose service name (hostname) and internal port — same DNS-based discovery as Lab 2. Prometheus pulls `/metrics` from each every 15 seconds.

### 3.2 — All 7 services running

```
NAME               IMAGE                     COMMAND                  SERVICE      CREATED         STATUS                   PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       8 minutes ago   Up 8 minutes             0.0.0.0:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      8 minutes ago   Up 8 minutes             0.0.0.0:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      8 minutes ago   Up 8 minutes             0.0.0.0:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     8 minutes ago   Up 8 minutes             0.0.0.0:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     8 minutes ago   Up 8 minutes (healthy)   0.0.0.0:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   8 minutes ago   Up 8 minutes             0.0.0.0:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        8 minutes ago   Up 8 minutes (healthy)   0.0.0.0:6379->6379/tcp
```

### 3.3 — Prometheus targets (all 3 up)

```
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

All three targets are healthy. Prometheus resolves the service hostnames through Docker's embedded DNS at `127.0.0.11`, same mechanism we observed in Lab 2.

### 3.4 — Custom metrics exposed by QuickTicket services

```
events_db_pool_size
events_orders_created
events_orders_total
events_reservations_active
```

The gateway also exposes `gateway_requests_total` and `gateway_request_duration_seconds_bucket` which are used for the golden signals dashboard. These appear after traffic is generated.

**PromQL request rate query (after 20s load at 5 RPS):**

```
Request rate: 0.2765 req/s
```

The rate is lower than 5 RPS because the 5-minute window includes the idle period before traffic started — `rate()` averages over the full window.

### 3.5 — Golden signals dashboard panels

The two placeholder panels were replaced in `monitoring/grafana/dashboards/golden-signals.json`.

**Latency panel (Golden Signal #1) — PromQL queries:**

```promql
# p50
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))

# p95
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))

# p99
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

Visualization: Time series, unit: seconds. Three series (p50/p95/p99) let you distinguish typical user experience from tail latency — p99 being much higher than p50 signals that a small fraction of users are getting a much worse experience.

**Saturation panel (Golden Signal #4) — PromQL query:**

```promql
events_db_pool_size
```

Visualization: Gauge, min 0, max 10. Thresholds: green below 7, yellow 7–9, red at 9+. This shows active DB connections out of the pool maximum — approaching 10 means new requests will queue waiting for a free connection.

### 3.6 — Failure injection observation

Load generator run (5 RPS for 60s) with payments killed at ~15s:

```
[10s]  requests=38   success=38   fail=0   error_rate=0%
[10s]  requests=41   success=41   fail=0   error_rate=0%
[20s]  requests=78   success=78   fail=0   error_rate=0%
[20s]  requests=81   success=81   fail=0   error_rate=0%
[30s]  requests=119  success=119  fail=0   error_rate=0%
[30s]  requests=121  success=120  fail=1   error_rate=.8%   ← payments stopped here
[40s]  requests=160  success=158  fail=2   error_rate=1.2%
[40s]  requests=163  success=161  fail=2   error_rate=1.2%
[50s]  requests=200  success=193  fail=7   error_rate=3.5%
[50s]  requests=204  success=197  fail=7   error_rate=3.4%
---
Done. total=241  success=229  fail=12  error_rate=4.9%
```

### 3.7 — Which golden signal detected the failure first?

**Errors** (Golden Signal #3) detected the failure first — within one scrape interval (15 seconds) of stopping the payments container, the `gateway_requests_total{status="503"}` counter started incrementing, causing the error rate panel to spike from 0% toward ~5%.

Latency did not spike because the gateway now returns 503 immediately via the `httpx.ConnectError` handler added in Lab 1 Task 2 — the failure is fast, not slow. Without that graceful degradation change, latency would also spike as requests timed out over 5 seconds.

The **Service Health** table also showed payments going `down` within the next health-check poll (~2s), but errors appeared in the time series first because the load generator was hitting the pay endpoint continuously.

---

## Task 2 — SLO Definitions & Recording Rules

### 3.8 — SLI and SLO definitions

**SLI 1 — Availability**
Measured as the ratio of non-5xx responses to total responses over a 5-minute window:
```
SLI = rate(gateway_requests_total{status!~"5.."}[5m]) / rate(gateway_requests_total[5m])
```
SLO target: **99.5%** over a 7-day rolling window.

**Error budget math:**
- Minutes in 7 days: 10,080
- Allowed downtime: 0.5% × 10,080 = **50.4 minutes per week**
- At ~1,000 requests/day (7,000/week): allowed failures = 0.5% × 7,000 = **35 failed requests per week**

**SLI 2 — Latency**
Ratio of requests completing under 500ms:
```
SLI = rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m]) / rate(gateway_request_duration_seconds_count[5m])
```
SLO target: **95%** of requests under 500ms.

### 3.9 — Recording rules

Created at `monitoring/prometheus/rules.yml`:

```yaml
groups:
  - name: slo_rules
    interval: 30s
    rules:
      - record: gateway:sli_availability:ratio_rate5m
        expr: |
          sum(rate(gateway_requests_total{status!~"5.."}[5m]))
          / sum(rate(gateway_requests_total[5m]))

      - record: gateway:sli_latency_500ms:ratio_rate5m
        expr: |
          sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m]))
          / sum(rate(gateway_request_duration_seconds_count[5m]))

      - record: gateway:error_budget_burn_rate:ratio_rate5m
        expr: |
          (1 - gateway:sli_availability:ratio_rate5m) / (1 - 0.995)
```

**Rules loaded in Prometheus:**

```
gateway:sli_availability:ratio_rate5m              ok
gateway:sli_latency_500ms:ratio_rate5m             ok
gateway:error_budget_burn_rate:ratio_rate5m        ok
```

All three rules are healthy. The burn rate rule divides the current error rate by the sustainable error rate (0.5%). A value above 1 means we are consuming the error budget faster than the SLO allows — during the payments outage this spiked to approximately 10x (error rate ~5% vs budget of 0.5%).

**SLO gauge panel query:**

```promql
gateway:sli_availability:ratio_rate5m * 100
```

Gauge configured with min 99, max 100, threshold at 99.5. During the payments failure the gauge dropped from 100% to ~95%, visually crossing the red threshold line — making the SLO breach immediately obvious without needing to read raw numbers.