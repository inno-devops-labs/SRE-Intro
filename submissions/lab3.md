# Lab 3 — Monitoring, Observability & SLOs

Liubov Utenysheva, CBS-03

---

## Task 1 — Configure Monitoring & Build Dashboard (6 pts)

### 3.1 Prometheus configuration

Created `monitoring/prometheus/prometheus.yml` — 15s scrape/evaluation intervals and one static job per QuickTicket service, using the Docker Compose service names as hostnames and the **internal** ports (8080/8081/8082), not the published ones:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "rules.yml"

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

(`rule_files` was added for Task 2.)

### 3.2 Monitoring stack — all 7 services

```console
$ docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
NAME             IMAGE                    COMMAND                  SERVICE    CREATED          STATUS
app-events-1     app-events               "uvicorn main:app --…"   events     36 seconds ago   Up 28 seconds
app-gateway-1    app-gateway              "uvicorn main:app --…"   gateway    35 seconds ago   Up 28 seconds
app-grafana-1    grafana/grafana:13.0.1   "/run.sh"                grafana    36 seconds ago   Up 34 seconds
app-payments-1   app-payments             "uvicorn main:app --…"   payments   36 seconds ago   Up 34 seconds
app-postgres-1   postgres:17-alpine       "docker-entrypoint.s…"   postgres   6 days ago       Up 34 seconds (healthy)
app-redis-1      redis:7-alpine           "docker-entrypoint.s…"   redis      6 days ago       Up 34 seconds (healthy)
app-prometheus-1 prom/prometheus:v3.11.2  "/bin/prometheus --con…" prometheus 36 seconds ago   Up 34 seconds
```

**7/7 services Up** (503 app/infra + prometheus + grafana, postgres & redis healthy).

### 3.3 Prometheus scrape targets

```console
$ curl -s http://localhost:9090/api/v1/targets | …
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

All 3 targets `up` after the first 15s scrape.

### 3.4 Metrics exploration

Raw metrics exposed by the gateway (`curl -s http://localhost:3080/metrics | grep -E "^gateway_" | head -10`):

```console
gateway_requests_total{method="POST",path="/events/{id}/reserve",status="200"} 27.0
gateway_requests_total{method="GET",path="/events",status="200"} 61.0
gateway_requests_total{method="POST",path="/reserve/{id}/pay",status="200"} 7.0
gateway_requests_created{method="POST",path="/events/{id}/reserve",status="200"} 1.789895367760747e+09
gateway_requests_created{method="GET",path="/events",status="200"} 1.7898953679819894e+09
gateway_requests_created{method="POST",path="/reserve/{id}/pay",status="200"} 1.7898953710724294e+09
gateway_request_duration_seconds_bucket{le="0.005",method="POST",path="/events/{id}/reserve"} 1.0
gateway_request_duration_seconds_bucket{le="0.01",method="POST",path="/events/{id}/reserve"} 26.0
gateway_request_duration_seconds_bucket{le="0.025",method="POST",path="/events/{id}/reserve"} 26.0
gateway_request_duration_seconds_bucket{le="0.05",method="POST",path="/events/{id}/reserve"} 27.0
```

All custom (app-level) metrics Prometheus knows about:

```console
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

Note: the gateway's counters/histogram are label-exploded on first use, so `gateway_*` appears in Prometheus only after traffic hits it.

After `./loadgen/run.sh 5 20` (88 requests, 0 failures):

```console
$ curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))' \
    http://localhost:9090/api/v1/query | …
Request rate: 0.34 req/s
```

(0.34 = the 20 s burst spread over the 5 m rate window, i.e. ~4 req/s of live traffic at burst time.)

### 3.5 Golden signals dashboard

The provisioned "QuickTicket — Golden Signals" dashboard ships with 3 working panels (Request Rate, Error Rate, Service Health table) + 2 text placeholders. I replaced the placeholders:

**Latency panel** (Golden Signal #1) — Time series, unit **seconds**, 3 queries:

```promql
p50: histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
p95: histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
p99: histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

**Saturation panel** (Golden Signal #4) — **Gauge**, min 0 / max 10, thresholds green (default) → yellow at 7 → red at 9:

```promql
events_db_pool_size
```

**Task 2 SLO panel** (added in 3.10) — Gauge, `gateway:sli_availability:ratio_rate5m * 100`, min 99 / max 100, threshold at 99.5.

Saved in Grafana (`admin/admin`). One environment note: Grafana 12+ makes file-provisioned dashboards read-only in the UI, so the completed dashboard was saved as a dashboard copy under the same title — "QuickTicket — Golden Signals" (uid `quickticket-golden-signals-completed`); the provisioned scaffold file in the repo stays untouched.

### 3.6 Failure injection — observations

Steady 5 rps traffic, then `… stop payments` at **12:14:39**, watched for 2 minutes, `… start payments` at **12:17:48**:

| Time (local) | Event / metric | Value |
|---|---|---|
| 12:14:39 | payments container stopped | — |
| 12:14:45 | first 504 in gateway log (5 s gateway timeout hit) | +6 s |
| 12:14:49 | first 502 (retry exhausted) | +10 s |
| 12:15:39 | `up{job="payments"}` | **0** (Service Health panel red) |
| 12:15:39 | 5xx error rate (1 m) | **8.2 %** |
| 12:15:39 | p99 latency (1 m) | **5.47 s** (pinned at the 5 s timeout) |
| 12:15:39 | request rate (1 m) | 1.73 req/s (down from ~5) |
| 12:16:03 | SLO availability gauge (5 m) | **100 % → 93.5 %** |
| 12:17:48 | error-budget burn rate | **13.3** (> 1 — burning) |
| 12:17:48 | payments restarted | — |
| 12:18:04 | `up{job="payments"}` | 1 (recovered) |

Normal traffic: all golden signals flat — error rate 0 %, p99 well under 0.5 s, ~5 req/s, DB pool low. During the failure: **Errors** and **Latency** spiked first and together (the same timed-out purchase requests appear as a 5xx *and* as a ~5.5 s histogram sample pinned at the gateway timeout), **Traffic** sagged (some requests dropped while clients/retries gave up), the **Service Health** table flipped payments to 0 within one 15 s scrape interval, and **Saturation** (events DB pool) was unaffected — the outage is downstream of events.

### 3.7 Which golden signal showed the failure first?

**Error Rate**, at **+6 s** after killing payments (first 504 at 12:14:45 vs kill at 12:14:39) — with **Latency (p99)** spiking in lockstep at the same moment, because every failed purchase burns the full 5 s gateway timeout and is counted in both signals. On the dashboard itself the **Service Health** table (payments `up = 0`) confirms the outage within one 15 s scrape window — the fastest signal of all — while Saturation never reacted.

---

## Task 2 — Define SLOs & Recording Rules (4 pts)

### 3.8 SLI / SLO definitions and error budget math

| SLI | Definition (5 m window) | SLO target |
|---|---|---|
| **Availability** | `% of gateway requests returning non-5xx` | **99.5 %** over a 7-day window |
| **Latency** | `% of gateway requests completing under 500 ms` | **95 %** |

Error budget (1000 requests/day × 7 = **7 000 requests/week**):

- Availability: `7000 × (1 − 0.995) = 7000 × 0.005 =` **35 failing requests per week allowed**
- Latency: `7000 × (1 − 0.95) = 7000 × 0.05 =` **350 slow (>500 ms) requests per week allowed**

So under steady load the SLOs tolerate ~5 availability failures per day and ~50 slow ones.

### 3.9 Recording rules

Created `monitoring/prometheus/rules.yml`:

```yaml
groups:
  - name: slo_rules
    interval: 30s
    rules:
      # SLI 1 — Availability: % of gateway requests returning non-5xx
      - record: gateway:sli_availability:ratio_rate5m
        expr: sum(rate(gateway_requests_total{status!~"5.."}[5m])) / sum(rate(gateway_requests_total[5m]))

      # SLI 2 — Latency: % of gateway requests completing under 500ms
      - record: gateway:sli_latency_500ms:ratio_rate5m
        expr: sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m])) / sum(rate(gateway_request_duration_seconds_count[5m]))

      # Error budget burn rate vs 99.5% SLO (>1 = burning budget too fast)
      - record: gateway:error_budget_burn_rate:ratio_rate5m
        expr: (1 - (sum(rate(gateway_requests_total{status!~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])))) / 0.005
```

Declared in `prometheus.yml` via `rule_files: [rules.yml]` and mounted into the container (`docker-compose.monitoring.yaml`):

```yaml
- ../monitoring/prometheus/rules.yml:/etc/prometheus/rules.yml:ro
```

After `… restart prometheus`, rules loaded:

```console
$ curl -s http://localhost:9090/api/v1/rules | …
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

### 3.10 SLO panel

Added a **Gauge** panel: `gateway:sli_availability:ratio_rate5m * 100`, min 99 / max 100, green above the 99.5 threshold. During the payments-down incident (3.6) it **dropped from 100 % to 93.4 %**, and the burn rate computed **13.3** — meaning the 5 m window was burning the weekly error budget ~13× faster than allowed. After payments came back, the gauge climbed back toward 100 % as the 5 m window refilled with healthy traffic.

---

## Bonus Task — Correlate Failure Across Metrics & Logs (2 pts)

Setup: `./loadgen/run.sh 5 150` steady traffic; after 30 s payments recreated with `PAYMENT_FAILURE_RATE=0.5 PAYMENT_LATENCY_MS=1000` (compose interpolates both into the container env).

### Timeline (local time)

| Time | Side | Event |
|---|---|---|
| 13:28:03 | loadgen | steady 5 rps traffic starts |
| 13:28:33 | ops | payments recreated with 50 % failures + 1000 ms latency (**injection**) |
| 13:28:47 | payments log | **first "Injecting 1000ms latency"** for `d49eea05-…` |
| 13:29:03 | payments log | "Injecting 1000ms latency for `43074b66-…`" |
| 13:29:04 | payments log | **first "Payment failed (injected)"** for `43074b66-…` (WARN) |
| 13:29:04 | gateway log | first **500** on `/reserve/43074b66-…/pay` — *same reservation id, 4 ms later* |
| 13:29:40–48 | gateway log | further 500s (`f01da344-…`, `fb88e9d8-…`) |
| ~13:29:30 | dashboard | 5xx error % climbs (0.5 % → 3 % over the next minute), p99 rises to ~1.8–2.3 s (1 s injected latency × retry) |
| 13:30:59 | ops | payments recreated clean (**recovery**) |
| 13:31:17 | metrics | 5xx rate back to 0 |

### Log excerpts at the failure moment

payments (first injected failure):

```console
{"time":"2026-09-20 10:29:03,800","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for 43074b66-7fc2-4c66-ac02-6c477364b092"}
{"time":"2026-09-20 10:29:04,800","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 43074b66-7fc2-4c66-ac02-6c477364b092"}
```

gateway (the same request, 4 ms later):

```console
2026-09-20T10:29:04.804Z INFO: 172.22.0.1:58284 - "POST /reserve/43074b66-7fc2-4c66-ac02-6c477364b092/pay HTTP/1.1" 500 Internal Server Error
```

### Root cause

The injected fault lives entirely in the payments service: `/charge` sleeps 1000 ms on **every** call and then fails 50 % of them with HTTP 500. The metrics and logs match one-to-one:

1. payments logs the WARN, gateway answers the client's `/reserve/{id}/pay` with 500 → the **5xx error rate** rises and `payments_charges_total{result="failed"}` grows (≈ 0.12 failed charges/s at 5 rps ⇒ ~30 % of traffic touches payments × 50 % failure).
2. Every charge that succeeds still takes the injected second, and failed ones cost two attempts (retry) → **p99** climbs to ~2 s even though p50 stays ~4 ms: only the payment path degraded, reads (`GET /events`) were untouched.
3. The gateway is a transparent pass-through here (no circuit breaker open at this failure rate), so its 500 timestamps are 3–4 ms after each payments WARN — the log correlation by `reservation_id` pins the causal chain payment-injection → gateway-5xx → dashboard spike.

Removing the env vars (clean recreate) at 13:30:59 ends the failure; 5xx returns to 0 within one scrape/evaluation cycle — confirming payments, not the gateway or events, was the root cause.

---

## Deliverables

- `monitoring/prometheus/prometheus.yml` — 3 scrape jobs + `rule_files`
- `monitoring/prometheus/rules.yml` — 3 SLO recording rules
- `docker-compose.monitoring.yaml` — rules mount added to the prometheus service
- `submissions/lab3.md` — this file
