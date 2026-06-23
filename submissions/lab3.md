# Lab 3 — Monitoring, Observability & SLOs

**Student:** Rolan  
**Branch:** `feature/lab3`  
**Date:** 2026-06-17

---

## Task 1 — Configure Monitoring & Build Dashboard (6 pts)

### 3.1 — Prometheus configuration

```yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "rules.yml"

scrape_configs:
  # Service names resolve over the compose network; use internal ports.
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

### 3.2 — All 7 services running (`compose ps`)

```text
NAME               SERVICE      STATUS
app-events-1       events       Up 18 minutes
app-gateway-1      gateway      Up 18 minutes
app-grafana-1      grafana      Up 19 minutes
app-payments-1     payments     Up 20 seconds
app-postgres-1     postgres     Up 18 minutes (healthy)
app-prometheus-1   prometheus   Up 19 minutes
app-redis-1        redis        Up 19 minutes (healthy)
```

### 3.3 — Prometheus targets (all 3 `up`)

```text
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

### 3.4 — Custom metrics list + request rate query

```text
# Custom metrics exposed by the services
events_db_pool_size
events_orders_created
events_orders_total
events_reservations_active
gateway_requests_total
gateway_request_duration_seconds_bucket
```

```text
# Request rate (Traffic golden signal) after ./loadgen/run.sh 5 20
Request rate: 0.20 req/s
```

### 3.5 — Dashboard panels (PromQL used)

**Latency panel (p50 / p95 / p99)** — `timeseries`, unit `seconds`:

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))   # p50
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))   # p95
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))   # p99
```

**Saturation panel (DB pool)** — `gauge`, min 0 / max 10, thresholds yellow @7, red @9:

```promql
events_db_pool_size
```

### 3.6 / 3.7 — Failure observations & analysis

Steady traffic at ~3.6 req/s (`./loadgen/run.sh 5 180`). `payments` stopped at **20:08:47**,
restarted at **20:10:27**. Metrics sampled every 15s straight from the Prometheus query API:

```text
TIME     | PHASE       | rate     | err    | p99      | pool | avail    | burn
20:08:32 | BASELINE    | 3.64 r/s |  0.00% |  23.7ms  |  0   | 100.000% | 0.00
20:08:47 | <<< payments stopped >>>
20:08:57 | FAILURE+15s | 3.62 r/s |  0.00% |  23.8ms  |  0   | 100.000% | 0.00
20:09:12 | FAILURE+30s | 3.55 r/s |  0.00% |  23.5ms  |  0   | 100.000% | 0.00
20:09:27 | FAILURE+45s | 3.58 r/s |  4.67% |  22.3ms  |  0   | 100.000% | 0.00
20:09:42 | FAILURE+60s | 3.45 r/s |  4.51% |  21.1ms  |  0   | 100.000% | 0.00
20:09:57 | FAILURE+75s | 3.53 r/s |  5.03% |  19.0ms  |  0   | 100.000% | 0.00
20:10:12 | FAILURE+90s | 3.51 r/s |  3.80% |  19.1ms  |  0   |  98.409% | 3.18
20:10:27 | <<< payments restarted >>>
20:10:40 | RECOVERY    | 2.51 r/s |  7.08% |  16.5ms  |  0   |  97.913% | 4.17
```

**Observations — normal vs failure:**
- **Normal:** error rate 0%, p99 ≈ 23 ms, availability 100%, burn rate 0.
- **Payments down:** the gateway cannot reach the `payments` host, so every purchase-flow
  request (~10% of traffic) returns an error. Error rate climbed to ~5%. Notably p99 latency
  did **not** rise — the gateway fails *fast* (DNS/connection error), it doesn't hang. The
  DB-pool gauge stayed at 0 (events service untouched). The 5-minute SLI windows
  (availability, burn rate) reacted slowly and only started moving ~90s in.

**Gateway log at the failure moment** confirms the mechanism (502 Bad Gateway):

```text
gateway | ERROR payment error: [Errno -2] Name or service not known
gateway | INFO  "POST /reserve/.../pay HTTP/1.1" 502 Bad Gateway
```

**Which golden signal showed the failure first? How long after killing payments?**

> **Errors** (error rate) showed it first. payments was stopped at 20:08:47 and the first
> non-zero error appeared on the `rate[1m]` window at ~20:09:27 — about **~40 seconds** later
> (delay = 1-minute rate window + 15s scrape interval + the fact that only ~10% of requests
> hit the payment path). **Latency** and **Saturation** never reacted (fail-fast, different
> service). The SLO/availability signal lagged the most because it uses a 5-minute window.

---

## Task 2 — Define SLOs & Recording Rules (4 pts)

### 3.8 — SLI/SLO definitions & error budget math

- **SLI 1 — Availability:** share of gateway requests returning non-5xx.
  **SLO: 99.5% over a 7-day window.**
- **SLI 2 — Latency:** share of gateway requests completing under 500 ms.
  **SLO: 95%.**

**Error budget (availability):**
- Traffic ≈ 1000 req/day → 1000 × 7 = **7000 requests / week**.
- Budget = 100% − 99.5% = **0.5%**.
- Allowed failures = 7000 × 0.005 = **35 failed requests per week**.

**Error budget (latency):**
- Budget = 100% − 95% = 5% → 7000 × 0.05 = **350 slow (>500 ms) requests per week**.

### 3.9 — Recording rules loaded

```text
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

Rules defined in `monitoring/prometheus/rules.yml`:
- `gateway:sli_availability:ratio_rate5m` = rate(non-5xx) / rate(total)
- `gateway:sli_latency_500ms:ratio_rate5m` = rate(bucket le=0.5) / rate(count)
- `gateway:error_budget_burn_rate:ratio_rate5m` = (1 − availability) / (1 − 0.995)

### 3.10 — SLO gauge observation during failure

Grafana panel **"SLO — Availability (7d target 99.5%)"** = `gateway:sli_availability:ratio_rate5m * 100`
(min 99, max 100, red below the 99.5 threshold).

During the payments outage the gauge dropped from **100.000% → 98.409%**, crossing below the
99.5% SLO line (turning red), while the **burn rate** climbed from 0 to **3.18** and peaked at
**~5** during recovery — i.e. the error budget was being spent ~3–5× faster than the SLO allows.
The 5-minute window makes the gauge react slowly and recover slowly even after payments is back.

---

## Bonus Task — Correlate Failure Across Metrics & Logs (2 pts)

**Setup:** `./loadgen/run.sh 5 150` in the background, then at +30s recreated payments with
`PAYMENT_FAILURE_RATE=0.5 PAYMENT_LATENCY_MS=1000`. Health endpoint confirmed the injection:

```text
{"status":"healthy","failure_rate":0.5,"latency_ms":1000}
```

Metrics during injection — note the **latency** signal now reacts (it didn't in Task 1):

```text
TIME     | PHASE  | rate     | err   | p99       | avail    | burn
20:14:03 | INJECT | 3.07 r/s | 0.72% | 2086.0ms  | 96.987%  | 6.03
20:14:18 | INJECT | 2.44 r/s | 0.91% | 2087.5ms  | 97.541%  | 4.92
```

### Failure timeline (local time, UTC+3)

```text
20:12:09  injection applied (PAYMENT_FAILURE_RATE=0.5, PAYMENT_LATENCY_MS=1000)
20:12:19  payments log: first "Injecting 1000ms latency for ..."
20:12:48  payments log: first "Payment failed (injected) ..." + HTTP 500
20:14:03  dashboard: p99 spikes to ~2086ms, burn rate up to ~6
20:14:36  payments healed (FAILURE_RATE=0.0, LATENCY_MS=0)
```

> Container logs are emitted in UTC (17:xx); add 3h to line them up with the local sample
> timestamps above.

### Log excerpts

**payments** (shows both injected behaviours — latency + forced 500):

```text
{"level":"INFO","service":"payments","msg":"Injecting 1000ms latency for a9039354-..."}
{"level":"WARNING","service":"payments","msg":"Payment failed (injected) for b1e532f8-..."}
INFO:     172.19.0.8:56240 - "POST /charge HTTP/1.1" 500 Internal Server Error
```

**gateway** (Task 1 stop case — host unreachable → 502):

```text
{"level":"ERROR","service":"gateway","msg":"payment error: [Errno -2] Name or service not known"}
INFO:     "POST /reserve/.../pay HTTP/1.1" 502 Bad Gateway
```

### Root cause

Two distinct downstream-failure modes, both traceable from metrics → logs:

1. **Task 1 (payments stopped):** the container is gone, so the gateway's DNS lookup of the
   `payments` host fails (`Name or service not known`). The gateway converts this into a
   **502 Bad Gateway** and returns *fast* → the **Errors** signal moves, but **Latency does not**.

2. **Bonus (payments degraded):** the container is up but deliberately injects 1000 ms latency
   on every call and returns HTTP 500 on 50% of charges. Here the **Latency** signal is the
   loud one — p99 jumps from ~23 ms to **~2086 ms** (≈ the injected 1000 ms plus the synchronous
   purchase flow), while errors rise only modestly because the failed charges are a small slice
   of total traffic. The payments logs (`Injecting 1000ms latency` / `Payment failed (injected)`)
   pinpoint the exact cause, and their timestamps line up with the dashboard p99 spike.

**Lesson:** a *crash* and a *slow/flaky dependency* look completely different across the golden
signals — "errors" catches a hard outage, "latency" catches a degradation. You need both.
