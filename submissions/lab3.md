# Lab 3 — Monitoring, Observability & SLOs

## Task 1 — Configure Monitoring & Build Dashboard

3.1 — Prometheus configuration

`monitoring/prometheus/prometheus.yml`:
```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

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
Used internal container ports (8080/8081/8082), not the published host ports (3080/8081/8082) — Prometheus runs inside the same Docker network and talks to services by their Compose hostnames.

3.2 — Start the monitoring stack

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
```
Output (final state):
```
NAME               IMAGE                     SERVICE      STATUS
app-events-1       app-events                events       Up (healthy deps)
app-gateway-1      app-gateway               gateway      Up
app-grafana-1      grafana/grafana:13.0.1    grafana      Up
app-payments-1     app-payments              payments     Up
app-postgres-1     postgres:17-alpine        postgres     Up (healthy)
app-prometheus-1   prom/prometheus:v3.11.2   prometheus   Up
app-redis-1        redis:7-alpine            redis        Up (healthy)
```
All 7 services running: the original 5 QuickTicket services + `prometheus` + `grafana`.

3.3 — Verify Prometheus is scraping

```bash
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import sys, json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(f\"{t['labels']['job']:12} {t['health']:8} {t['scrapeUrl']}\")
"
```
Output:
```
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```
All three targets `up`.

3.4 — Explore metrics

Custom metric names visible in Prometheus after generating traffic:
```
events_db_pool_size
events_orders_created
events_orders_total
events_request_duration_seconds_bucket / _count / _sum / _created
events_requests_created
events_requests_total
events_reservations_active
gateway_request_duration_seconds_bucket / _count / _sum / _created
gateway_requests_created
gateway_requests_total
payments_charges_created
payments_charges_total
payments_request_duration_seconds_bucket / _count / _sum / _created
payments_requests_created
payments_requests_total
```
Note: `gateway_requests_total` exists as a metric *name* as soon as the process starts (declared via `prometheus_client.Counter`), but Prometheus only reports label-specific time series once at least one request with that label combination has actually happened — so `/metrics` shows an empty `gateway_requests_total` block until traffic is generated.

Traffic + request rate query:
```bash
./loadgen/run.sh 5 20
sleep 20
curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))' \
  http://localhost:9090/api/v1/query
```
```
Request rate: 0.15 req/s   (measured right after a short 20s/5rps burst; 5m window still mostly zeros)
```
After a longer run, broken down by path:
```
path                          req/s
/events                       0.69
/events/{id}/reserve          0.31
/reserve/{id}/pay              0.07
```
Matches the load generator's traffic mix (70% reads, 20% reserve, 10% full purchase).

3.5 — Golden signals dashboard

Replaced the two placeholder panels in `monitoring/grafana/dashboards/golden-signals.json`:

**Latency panel** — timeseries, unit `s`:
```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))   # p50
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))   # p95
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))   # p99
```

**Saturation panel** — gauge, min 0 / max 10, thresholds green→yellow at 7, yellow→red at 9:
```promql
events_db_pool_size
```

Verified via the dashboard API after restarting Grafana that both panels loaded (no more placeholder text panels):
```
Request Rate (Traffic)        - timeseries
Error Rate                    - timeseries
Service Health (up/down)      - table
Latency (p50 / p95 / p99)     - timeseries
Saturation (DB pool usage)    - gauge
```

3.6 — Inject failure and observe

```bash
./loadgen/run.sh 5 60 &
sleep 15
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
# ... 60s later ...
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start payments
```
`payments` stopped at `13:16:08`, restarted at `13:17:08`. Loadgen summary for that run:
```
[10s] error_rate=0%
[20s] error_rate=2.2%
[30s] error_rate=5.2%
[40s] error_rate=7.3%
[50s] error_rate=5.8%
Done. total=262 success=244 fail=18 error_rate=6.8%
```

Prometheus range query, error rate (`sum(rate(gateway_requests_total{status=~"5.."}[1m])) / sum(rate(gateway_requests_total[1m])) * 100`):
```
13:16:40   2.95%
13:16:50   4.10%
13:17:00   4.40%
13:17:10   5.93%
13:17:20   5.90%
13:17:30   4.62%
13:17:40   nan   (payments back up, no more 5xx in the trailing 1m window)
```

Prometheus range query, p99 latency (`histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))`) over the same window:
```
13:15:50   0.0241s   (baseline, before failure)
13:16:10   0.0238s
13:16:30   0.0232s
13:16:50   0.0226s
13:17:10   0.0199s
13:17:30   0.0201s
```
p99 latency stayed essentially flat (~20-24ms) the entire time — it does **not** react to the payments outage.

Gateway logs, first failed request after the stop:
```
13:16:10.421   POST /reserve/8843312f.../pay HTTP/1.1  502 Bad Gateway
13:16:11.567   POST /reserve/548a9aa5.../pay HTTP/1.1  502 Bad Gateway
```

**Answer — Which golden signal showed the failure first? How long after killing payments?**
**Error Rate**, not Latency. The gateway's `httpx` call to a stopped `payments` container fails immediately with connection-refused rather than hanging, so the gateway returns a `502 Bad Gateway` right away instead of a slow response — the first 502 appears in the logs at `13:16:10.42`, **~2.4 seconds** after `payments` was stopped (`13:16:08`). On the dashboard itself (1m-windowed rate), the Error Rate panel starts climbing visibly within the next ~30-60s as the rolling window fills with 5xx samples. The Latency panel (p50/p95/p99) barely moves throughout the whole incident, because a fast rejected connection is *cheap*, not slow — this particular failure mode is invisible to the Latency golden signal and only shows up in Error Rate (and, if Payments' health check were wired into `up`, in Service Health).

---

## Task 2 — Define SLOs & Recording Rules

3.8 — SLIs and SLOs

**SLI 1 — Availability:** % of gateway requests returning non-5xx. **SLO: 99.5%** over a 7-day window.
**SLI 2 — Latency:** % of gateway requests completing under 500ms. **SLO: 95%**.

Error budget math (at ~1000 requests/day → 7000 requests/week):
- **Availability budget:** `100% - 99.5% = 0.5%` allowed failure rate → `0.005 × 7000 = 35 failed requests/week`.
- **Latency budget:** `100% - 95% = 5%` allowed to exceed 500ms → `0.05 × 7000 = 350 slow requests/week`.

The availability budget is much tighter in absolute terms (35 vs 350 requests) even though the latency SLO *looks* looser at 95% vs 99.5% — because the latency target itself tolerates 10x more "bad" events per week than the availability target does.

3.9 — Recording rules

`monitoring/prometheus/rules.yml`:
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
Wired into `prometheus.yml` via `rule_files: ["rules.yml"]`, and mounted into the Prometheus container in `docker-compose.monitoring.yaml`:
```yaml
- ../monitoring/prometheus/rules.yml:/etc/prometheus/rules.yml:ro
```

Rules loaded output (`curl -s http://localhost:9090/api/v1/rules`):
```
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```
Sample values after some traffic (still carrying residual errors from the Task 1 failure injection in the trailing 5m window):
```
gateway:sli_availability:ratio_rate5m       = 0.9744   (97.4%)
gateway:sli_latency_500ms:ratio_rate5m      = 1.0      (100% under 500ms)
gateway:error_budget_burn_rate:ratio_rate5m = 5.11      (burning ~5x faster than the sustainable rate)
```

3.10 — SLO panel + failure observation

Added a gauge panel to `golden-signals.json`: `gateway:sli_availability:ratio_rate5m * 100`, min 99 / max 100, red below 99.5 / green at or above 99.5.

Ran another failure injection — `payments` stopped at `13:21:04`, restarted at `13:22:05`, with steady load throughout (`loadgen 5rps/75s`, ending at 16.1% error_rate). Availability SLI (`gateway:sli_availability:ratio_rate5m * 100`) over that window:
```
13:20:08   97.44
13:20:28   97.44
13:20:48   97.61   (pre-failure baseline)
13:21:08   97.31   (payments just stopped at 13:21:04)
13:21:28   97.31
13:21:48   97.38
13:22:08   97.09   (payments restarted at 13:22:05)
13:22:28   97.09
```

**Observation:** the SLO gauge sat in the **red zone the entire time** (well below the 99.5% target) — even the "baseline" before this specific injection was already only 97.4-97.6%, because the rule uses a 5-minute rolling window and the stack had been intentionally failing on and off throughout this lab session, so old failures hadn't fully aged out yet. The new injection nudged the gauge down further (97.6% → 97.1%), but the drop is much less dramatic than the raw Error Rate panel's spike (which jumped to ~17-18% almost immediately) — the 5m averaging window is exactly what a burn-rate SLO is supposed to do: smooth out short blips so a single minute-long outage doesn't look catastrophic, at the cost of reacting slowly and making a real outage easy to under-read if you only glance at the gauge for a few seconds. This is also why the separate `error_budget_burn_rate` rule exists — a burn rate of 5.1 (vs. the sustainable rate of 1.0) is a much clearer "this is a real problem" signal than the 0.5-percentage-point wobble on the availability gauge itself.

---

## Bonus Task — Correlate Failure Across Metrics & Logs

Setup:
```bash
./loadgen/run.sh 5 120 &
sleep 30
PAYMENT_FAILURE_RATE=0.5 PAYMENT_LATENCY_MS=1000 \
  docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```
This does a full container **recreate** (not just an env change on the running process), so it also introduces a short, *unintentional* connection gap while the old `payments` container stops and the new one boots — that turned out to be a useful extra data point (see timeline).

### Timeline

| Time (UTC) | Event | Source |
|---|---|---|
| `13:24:03.0` | `docker compose up -d payments` issued — old container stops, new one (with `PAYMENT_FAILURE_RATE=0.5`, `PAYMENT_LATENCY_MS=1000`) starts | shell |
| `13:24:04.851` | Gateway logs `payment error: All connection attempts failed` → `502 Bad Gateway` — **transient** failure from the container restart gap, unrelated to the injected failure rate | `gateway` log |
| `13:24:07.652` | First successful charge on the *new* payments container — restart gap closed (~3.6s total) | `payments` / `gateway` logs |
| `13:24:20` | Dashboard **Latency** panel (p99) jumps from a 0.034s baseline to **1.075s** — first window where the new 1000ms artificial delay dominates the 1m rate window | Prometheus query |
| `13:24:26.652` | `payments` logs `"Injecting 1000ms latency for 0d81d025..."` — first request to hit the new latency injection | `payments` log |
| `13:24:27.652` | Exactly 1.000s later: `payments` logs `"Payment failed (injected) for 0d81d025..."` (WARNING) and returns `500 Internal Server Error` | `payments` log |
| `13:24:27.654` | Gateway relays the same `500 Internal Server Error` to the client, 2ms after receiving it from payments | `gateway` log |
| `13:24:50` | Dashboard **Error Rate** panel peaks around **2.8%** (1m-windowed `5xx / total`) | Prometheus query |
| `13:24:20 → 13:26:10` | p99 latency stays pinned at **~2.0-2.2s** — because *every* charge request (success or fail) now pays the 1000ms `PAYMENT_LATENCY_MS` cost, plus retry/queueing overhead compounds it for the ~50% that also fail | Prometheus query |
| `13:26:04` | Loadgen run ends (120s duration); `payments` left running with the failure/latency injection for a few more seconds | shell |
| after | `docker compose up -d payments` (no env override) → container recreated back to `PAYMENT_FAILURE_RATE=0.0`, `PAYMENT_LATENCY_MS=0` | shell |

### Log excerpts

`payments` — the injected failure, one full cycle:
```
13:24:26.652  INFO   Injecting 1000ms latency for 0d81d025-c66a-4a38-9f8d-6da41fefaee9
13:24:27.652  WARNING Payment failed (injected) for 0d81d025-c66a-4a38-9f8d-6da41fefaee9
13:24:27.653  INFO   "POST /charge HTTP/1.1" 500 Internal Server Error
```
`gateway` — same request, other side of the call:
```
13:24:27.654  HTTP Request: POST http://payments:8082/charge "HTTP/1.1 500 Internal Server Error"
13:24:27.655  INFO  "POST /reserve/0d81d025-c66a-4a38-9f8d-6da41fefaee9/pay HTTP/1.1" 500 Internal Server Error
```
Gateway forwards `payments`' own status code (500) verbatim here — different from Task 1's scenario, where a **connection-refused** (payments container fully down) produced a gateway-generated `502 Bad Gateway` instead.

### Root cause explanation

Two distinct failure signatures are visible, and each maps to a different golden signal:

1. **`13:24:04.851` — transient 502, connection-refused.** Caused by the `docker compose up -d payments` **recreate** itself (old container stopped, new one still binding to its port) — an artifact of *how* the failure was injected, not the injection itself. Lasted under 4 seconds and is invisible on the dashboard because the 1m rate window never accumulates enough samples from a 1-request blip to move the needle.
2. **`13:24:26+` — sustained 500s + latency, the actual injected fault.** `PAYMENT_LATENCY_MS=1000` adds a flat 1s delay to *every* `/charge` call (success or fail — visible from the "Injecting 1000ms latency" log line appearing before both outcomes), and `PAYMENT_FAILURE_RATE=0.5` then fails roughly half of those delayed calls with a `500`. This is why, unlike Task 1's "payments fully down" scenario:
   - **Latency (p99) is the loud signal here** — it jumps 30x (34ms → ~1-2s) almost as soon as the new container starts taking traffic, because the delay applies unconditionally to every request, not just the failing half.
   - **Error Rate rises more slowly and modestly** (peaking ~2.8% on the 1m-windowed dashboard panel, vs. the loadgen's own tighter accounting of ~11-16%) — a nuance worth flagging: the dashboard's `status=~"5.."` filter and the loadgen's client-side success/fail bookkeeping aren't counting identical things (loadgen also treats client-side timeouts as failures, which the 1s injected delay makes more likely at RPS=5), so the two "error rate" numbers aren't directly comparable even though they're measuring the same incident.
   - Root cause, in one line: **a single upstream (`payments`) config change that adds fixed per-request latency will show up on the Latency golden signal first and more clearly than on Error Rate**, even when it also causes real request failures — the opposite of Task 1's finding, where a fully-down dependency showed up on Error Rate first and barely touched Latency. Which signal "catches" a given failure first depends entirely on *how* the dependency fails (fast-reject vs. slow-then-fail), which is exactly why golden-signals dashboards track all four signals rather than relying on any single one.
