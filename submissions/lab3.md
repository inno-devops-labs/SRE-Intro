# Lab 3 — Monitoring, Observability & SLOs

**Author:** jakefish18
**Stack:** QuickTicket (5 services) + Prometheus `v3.11.2` + Grafana `13.0.1`.

> Local port notes: host `5432` and `3000` were already in use on my machine, so via a
> local compose override Postgres is published on `5434:5432` and **Grafana on `3001:3000`**
> (Prometheus is on the standard `9090`). Internal scraping by service name is unaffected.
> All compose commands below use the two lab files plus that override:
> `-f app/docker-compose.yaml -f docker-compose.monitoring.yaml -f <override>`.
---

## Task 1 — Configure Monitoring & Build Dashboard

### `monitoring/prometheus/prometheus.yml`

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "rules.yml"          # added in Task 2

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
Each service is scraped by its **Compose service name** on its **internal** port
(8080/8081/8082, not the published 3080), because Prometheus shares the `app_default`
network and resolves those names via Docker DNS.

### 1) `compose ps` — all 7 services

```
NAME               SERVICE      STATUS                 PORTS
app-events-1       events       Up                     0.0.0.0:8081->8081/tcp
app-gateway-1      gateway      Up                     0.0.0.0:3080->8080/tcp
app-payments-1     payments     Up                     0.0.0.0:8082->8082/tcp
app-postgres-1     postgres     Up (healthy)           0.0.0.0:5434->5432/tcp
app-redis-1        redis        Up (healthy)           0.0.0.0:6379->6379/tcp
app-prometheus-1   prometheus   Up                     0.0.0.0:9090->9090/tcp
app-grafana-1      grafana      Up                     0.0.0.0:3001->3000/tcp
```
### 2) Prometheus targets — all 3 `up`
```
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```
### 3) Custom metrics exposed
```
gateway_requests_total, gateway_request_duration_seconds_{bucket,count,sum}
events_requests_total,  events_request_duration_seconds_{bucket,count,sum}
events_db_pool_size, events_reservations_active, events_orders_total
payments_requests_total, payments_request_duration_seconds_{bucket,count,sum}
payments_charges_total{result="success|failed"}        # appears once a charge happens
```
### 4) PromQL — request rate (Traffic)
```
$ query: sum(rate(gateway_requests_total[5m]))
Request rate: 0.33 req/s
```
(0.33 req/s is the 5-minute average just after a 30 s, ~120-request burst.)
Other golden signals at the same moment (all healthy):
```
Latency   p50 = 6.9 ms   p95 = 9.8 ms   p99 = 25.1 ms
Errors    0% (no 5xx)
Saturation events_db_pool_size = 0
```
### 5) PromQL used for the two new panels
**Latency panel** (Time series, unit = seconds) — 3 queries:
```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

**Saturation panel** (Gauge, min 0 / max 10, thresholds green→yellow@7→red@9):
```promql
events_db_pool_size
```

Both replaced the placeholder text panels in
`monitoring/grafana/dashboards/golden-signals.json` (provisioned automatically). The
dashboard now has 6 panels; I verified each query renders through Grafana's datasource
proxy, e.g. p99 via Grafana → Prometheus returned `28.7 ms`.

### 6) Dashboard observations — normal vs `payments` killed

Driving steady traffic (6 rps) and then `docker compose stop payments` at t=0, polling
the panels' queries every ~5 s:

```
baseline   error_rate=0.00%  up{payments}=1  p99=0.02s
t=+ 0s     error_rate=0.00%  up{payments}=1  p99=0.02s
t=+ 5s     error_rate=0.00%  up{payments}=1  p99=0.02s
t=+10s     error_rate=0.00%  up{payments}=0  p99=0.02s    <-- Service Health flips first
t=+20s     error_rate=2.94%  up{payments}=0  p99=0.02s    <-- Error Rate becomes visible
t=+30s     error_rate=4.56%  up{payments}=0  p99=0.02s
t=+61s     error_rate=5.48%  up{payments}=0  p99=0.02s
```

- **Traffic** unchanged (clients keep hitting the gateway).
- **Errors** climb from 0% → ~5% (only the ~10% purchase-flow traffic touches payments;
  the gateway returns `502` for those).
- **Latency** does **not** move — a stopped container gives a *fast* connection-refused,
  not a slow response.
- **Saturation** unaffected (payments isn't a DB consumer).
- **Service Health** (`up{payments}`) drops to 0.

### 7) Which golden signal showed the failure first?

**The `up{job="payments"}` Service-Health panel detected it first — at ~10 s** (one
15 s scrape interval: the first scrape Prometheus couldn't complete). Among the four
*classic* golden signals, **Errors** was first to move, at **~20 s**, as gateway `502`s
accumulated in the 1-minute rate window. **Latency never reacted at all**, and Traffic/
Saturation were flat.

> SRE takeaway: for a hard-down dependency, black-box availability (`up`) beats the
> symptom-based error SLI by ~10 s, and a *latency*-only alert would have completely
> missed this outage. (Contrast with the Bonus, where a *slow/flaky* dependency lights
> up Latency **and** Errors.)
---

## Task 2 — Define SLOs & Recording Rules

### SLIs / SLOs and error-budget math

| SLI | Definition | SLO target | Window |
|-----|-----------|-----------:|--------|
| **Availability** | % of gateway requests **not** returning 5xx | **99.5%** | 7 days |
| **Latency** | % of gateway requests completing **< 500 ms** | **95%** | (rolling) |

**Error budget (availability), at ~1000 req/day:**
- Requests/week = 1000 × 7 = **7000**
- Budget = (1 − 0.995) × 7000 = 0.005 × 7000 = **35 failed requests per week** allowed
  (≈ 5 failures/day). Expressed as time, 0.5% of a 7-day week ≈ **50.4 minutes** of
  full downtime per week.
- **Latency budget:** up to 5% of 7000 = **350 requests/week** may exceed 500 ms.

> Reality check from the observations: a single ~1-minute `payments` outage at 6 rps
> produced ~30–40 failed purchases — i.e. **one short incident can burn ~the entire
> weekly availability budget**, which is exactly why the burn-rate metric matters.
### `monitoring/prometheus/rules.yml`

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
Mounted into Prometheus by adding to `docker-compose.monitoring.yaml`:
```yaml
- ../monitoring/prometheus/rules.yml:/etc/prometheus/rules.yml:ro
```

### Rules loaded

```
group: slo_rules
  gateway:sli_availability:ratio_rate5m         health=ok
  gateway:sli_latency_500ms:ratio_rate5m        health=ok
  gateway:error_budget_burn_rate:ratio_rate5m   health=ok
```
Clean-baseline values (5-minute window all-healthy):
```
availability = 100.000%   latency_under_500ms = 100.0%   burn_rate = 0.00
```
### SLO gauge during a `payments` outage
Gauge query `gateway:sli_availability:ratio_rate5m * 100` (red below 99.5):
```
                 SLO gauge (avail%)   burn_rate
healthy          100.000              0.00
t=+49s           100.000              0.00     (5m window still mostly healthy)
t=+55s            94.930             10.14     <-- gauge crosses 99.5 -> RED
t=+67s            94.930             10.14
```
Availability fell to **94.93%** (well under the 99.5% objective → gauge turns red) and
the **burn rate hit 10.1** — i.e. the error budget was being consumed **~10× faster**
than sustainable. (The 5 m-window SLI reacts more slowly/smoothly than the 1 m error-rate
panel — expected for an SLO signal.)
---
## Bonus Task — Correlate Failure Across Metrics & Logs
**Fault injected:** `payments` restarted with `PAYMENT_FAILURE_RATE=0.5
PAYMENT_LATENCY_MS=1000` (verified live: `{"failure_rate":0.5,"latency_ms":1000}`).
Unlike Task 1's hard-down failure, payments is **up but flaky+slow**.
### Metric response (dashboard)
```
inject t=0   error_rate=0.00%   p50=0.007s   p99=0.018s
t=+ 6s       error_rate=0.64%   p50=0.007s   p99=0.013s   <-- Errors first
t=+25s       error_rate=0.55%   p50=0.007s   p99=1.951s   <-- Latency p99 spikes (the 1s)
t=+49s       error_rate=4.11%   p50=0.007s   p99=2.285s
t=+80s       error_rate=5.67%   p50=0.007s   p99=2.359s
```
Both **Errors** (→5.7%) and **Latency p99** (0.018 s → **2.36 s**) light up; `p50` stays
flat because only ~half of charges fail/are slow and only ~10% of traffic is purchases.
### Log correlation — same `reservation_id` across services
Driving 30 purchases through the gateway with the fault active gave exactly
**15 success / 15 failed** (the configured 50%). Matching individual requests by id:
```
# FAILURE path — payments rejects, gateway returns 500, SAME id, 4 ms apart:
payments-1 | 08:31:16.231  WARNING  Payment failed (injected) for 7c6060b9-cdcf-4181-a48f-9de0fcafbc05
gateway-1  | 08:31:16.235  INFO     POST /reserve/7c6060b9-cdcf-4181-a48f-9de0fcafbc05/pay  500 Internal Server Error

# SLOW-success path — latency injection adds EXACTLY 1.000 s:
payments-1 | 08:31:14.168  INFO     Injecting 1000ms latency for 75dfa8ce-0498-410c-a6f8-67a21c0ea4ef
payments-1 | 08:31:15.168  INFO     Payment success: PAY-A0393BF9 for 75dfa8ce-0498-410c-a6f8-67a21c0ea4ef
```
Payments' own metrics confirm the split independently:
```
payments_charges_total{result="failed"}  = 15
payments_charges_total{result="success"} = 15
mean charge latency = 1004 ms     # the injected 1000 ms
```
### Timeline & root cause
| Time (UTC) | Where | What |
|------------|-------|------|
| 08:30:40 | operator | inject fault (`FAILURE_RATE=0.5`, `LATENCY_MS=1000`) into `payments` |
| 08:31:09 | **gateway logs** | first `POST /reserve/…/pay → 500` |
| 08:31:14–16 | **payments logs** | `Injecting 1000ms latency …` + `Payment failed (injected) …` |
| t≈+6 s | **dashboard / Errors** | error rate first rises off 0% |
| t≈+25 s | **dashboard / Latency** | p99 jumps to ~2 s as slow samples fill the 1 m window |
| on restore | all | error rate → 0, p99 → ~0.02 s, charges all succeed |
**Root cause chain:** the injected config makes `payments.charge` (a) fail 50% of the
time with HTTP 500 and (b) sleep 1 s on the rest. The gateway calls `payments:8082/charge`
inside `/pay`; a failed charge surfaces as a client-facing **500** (Errors signal), while
a slow-but-successful charge inflates **gateway request latency by ~1 s** (Latency signal).
The metrics tell you *that* and *how much* the service is degraded; the logs — joined by
`reservation_id` — tell you *which* dependency (`payments`) and *why* (`injected` failure /
`Injecting … latency`). Metrics for detection, logs for the exact root cause.
---
## Summary
- Wrote `prometheus.yml` (3 scrape jobs) — all targets `up`; completed the Golden Signals
  dashboard with **Latency (p50/p95/p99)** and **Saturation (DB pool)** panels.
- Observed a hard `payments` outage: **Service Health detected at ~10 s, Errors at ~20 s,
  Latency never** — a key argument for multi-signal alerting.
- Defined Availability (99.5%/7d → **35 errors/week budget**) and Latency (95% < 500 ms)
  SLOs; 3 recording rules load `health=ok`; SLO gauge dropped to **94.9%** with burn rate
  **10×** during an outage.
- Correlated a flaky+slow `payments` fault across metrics and logs down to the
  **per-`reservation_id`** level (failed charge → gateway 500; +1.000 s injected latency).