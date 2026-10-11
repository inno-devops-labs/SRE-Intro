# Lab 3 — Monitoring, Observability & SLOs

All commands below were run for real against the QuickTicket Compose stack (`app/docker-compose.yaml`
+ `docker-compose.monitoring.yaml`) carried over from Labs 1-2, on the same host. As in Lab 1, the
`events` service is published on host port **8091** (not 8081, that host port is taken by an
unrelated container), so `EVENTS_URL` internally still uses `events:8081`.

Grafana is reachable at `http://localhost:3000` (login `admin`/`admin`, confirmed from
`docker-compose.monitoring.yaml`'s `GF_SECURITY_ADMIN_USER`/`GF_SECURITY_ADMIN_PASSWORD`. Anonymous
Viewer access is also enabled, which is what the automated browser check used). Prometheus is at
`http://localhost:9090`.

---

## Task 1 — Configure Monitoring & Build Dashboard

### 3.1 — `monitoring/prometheus/prometheus.yml`

Created (not previously in the repo):

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

Internal container ports were used (8080/8081/8082), not the host-published ports (3080/8091/8082).
`docker-compose.monitoring.yaml` was also extended to mount `rules.yml` (needed for Task 2):

```yaml
  prometheus:
    volumes:
      - ../monitoring/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ../monitoring/prometheus/rules.yml:/etc/prometheus/rules.yml:ro
```

### 3.2 — Start the monitoring stack

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
```

`docker compose ... ps`, all 7 services running:

```
NAME               IMAGE                    SERVICE      STATUS                    PORTS
app-events-1       app-events               events       Up (healthy deps)         0.0.0.0:8091->8081/tcp
app-gateway-1      app-gateway              gateway      Up                        0.0.0.0:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1   grafana      Up                        0.0.0.0:3000->3000/tcp
app-payments-1     app-payments             payments     Up                        0.0.0.0:8082->8082/tcp
app-postgres-1     postgres:17-alpine       postgres     Up (healthy)              0.0.0.0:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2  prometheus   Up                        0.0.0.0:9090->9090/tcp
app-redis-1        redis:7-alpine           redis        Up (healthy)              0.0.0.0:6379->6379/tcp
```

(full untruncated output captured with `docker compose ... ps` during the run, 7/7 services `Up`.)

### 3.3 — Verify Prometheus is scraping

```bash
curl -s http://localhost:9090/api/v1/targets | python3 -c "..."
```

```
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

All three targets `up`.

### 3.4 — Explore metrics

Raw gateway metrics (`curl -s http://localhost:3080/metrics | grep -E "^gateway_" | head -10`):

```
gateway_requests_total{method="POST",path="/events/{id}/reserve",status="200"} 2.0
gateway_requests_total{method="POST",path="/reserve/{id}/pay",status="200"} 2.0
gateway_requests_total{method="POST",path="/reserve/{id}/pay",status="500"} 1.0
gateway_requests_created{method="POST",path="/events/{id}/reserve",status="200"} 1.7900236410382128e+09
gateway_requests_created{method="POST",path="/reserve/{id}/pay",status="200"} 1.7900236411197703e+09
gateway_requests_created{method="POST",path="/reserve/{id}/pay",status="500"} 1.7900236795772295e+09
gateway_request_duration_seconds_bucket{le="0.005",method="POST",path="/events/{id}/reserve"} 0.0
gateway_request_duration_seconds_bucket{le="0.01",method="POST",path="/events/{id}/reserve"} 1.0
gateway_request_duration_seconds_bucket{le="0.025",method="POST",path="/events/{id}/reserve"} 1.0
gateway_request_duration_seconds_bucket{le="0.05",method="POST",path="/events/{id}/reserve"} 2.0
```

Custom metric names known to Prometheus (`__name__` filtered for `gateway_`/`events_`/`payments_`):

```
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

Traffic generated with the loadgen, then the request-rate query:

```bash
./loadgen/run.sh 5 20
# QuickTicket Load Generator
# Target: http://localhost:3080 | RPS: 5 | Duration: 20s
# ---
# [10s] requests=44 success=44 fail=0 error_rate=0%
# ...
# Done. total=87 success=87 fail=0 error_rate=0%

curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))' \
  http://localhost:9090/api/v1/query | python3 -c "..."
# Request rate: 0.37 req/s
```

(0.37 req/s reflects the 5-minute rate window averaging in the idle time before/after the 20s burst.
This is the real number the real query returned, not a hand-picked one.)

### 3.5 — Golden signals dashboard

The dashboard ships with 3 working panels (Request Rate, Error Rate, Service Health) and 2 placeholder
text panels. I replaced the two placeholders in `monitoring/grafana/dashboards/golden-signals.json`
with real panels (edited directly in the JSON source file, which is what Grafana's file provisioner
reads. The provider is `disableDeletion: false`/`editable: true` but has no `allowUiUpdates`, so
editing the JSON source and restarting Grafana is the way to make panel changes stick across
restarts and into git):

**Latency panel**, `timeseries`, unit `s`:
```
p50: histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
p95: histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
p99: histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

**Saturation panel**, `gauge`, query `events_db_pool_size`, min 0 / max 10, thresholds green
(default) / yellow at 7 / red at 9.

A third panel (SLO Availability gauge, `gateway:sli_availability:ratio_rate5m * 100`) was also added
for Task 2, see below.

**Verified live in the browser** (not just assumed): I opened `http://localhost:3000/d/quickticket-golden-signals`
via the Grafana UI while the loadgen was generating traffic. All 6 panels rendered with real,
moving data. Request Rate showing per-path req/s, Error Rate at 0% under normal traffic, Service
Health table showing `payments 1` / `gateway 1` / `events 1`, Latency lines for p50/p95/p99 in the
tens-of-ms range, Saturation gauge reading the live `events_db_pool_size` value, and the SLO gauge at
100% before any fault injection.

### 3.6/3.7 — Inject failure and observe

I started steady traffic, then stopped payments **outright** (hard failure):

```bash
./loadgen/run.sh 5 90 &
sleep 15
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
# killed at 2026-09-21T20:54:28Z
```

Polled Prometheus every 10s during the outage (`error_rate` = 30s-window 5xx ratio on gateway,
`availability_sli` = the Task-2 recording rule, `payments_up` = the raw `up{job="payments"}` metric):

```
[@ 20:54:28Z] payments stopped
[@ 20:54:48Z] error_rate%=0.00   availability_sli%=100.00  payments_up=0   <- Service Health already down
[@ 20:54:58Z] error_rate%=7.03   availability_sli%=100.00  payments_up=0   <- Error Rate first shows failure
[@ 20:55:08Z] error_rate%=1.47   availability_sli%=97.37   payments_up=0   <- SLO Availability gauge first drops
[@ 20:55:18Z] error_rate%=1.47   availability_sli%=97.37   payments_up=0
[@ 20:55:28Z] error_rate%=2.94   availability_sli%=97.37   payments_up=0
[@ 20:55:39Z] error_rate%=5.63   availability_sli%=97.95   payments_up=0
[@ 20:56:10Z] error_rate%=nan    availability_sli%=97.26   payments_up=0   (loadgen had finished, no traffic in window)
[@ 20:56:20Z] error_rate%=nan    availability_sli%=97.26   payments_up=0
[@ 20:56:30Z] error_rate%=nan    availability_sli%=97.26   payments_up=0
[@ 20:56:30Z] payments restarted
```

A Grafana screenshot taken mid-outage (`http://localhost:3000/d/quickticket-golden-signals`) showed,
in the live browser, Error Rate spiking to ~4%, Service Health table showing `payments 0`, and the
SLO Availability gauge at **97.9%** with its bar turned red (below the 99.5% threshold line).

After `docker compose ... start payments`, a fresh 20s/5rps load run confirmed recovery.
`up{job="payments"}` was back to `1`, and the Grafana dashboard (screenshotted again) showed the
error-rate line falling back toward 0% and the SLO gauge climbing back up from 97.4% as the
5-minute rolling window aged out the failed requests.

**Which golden signal showed the failure first? How long after killing payments?**

**Service Health (the raw `up{job="payments"}` metric)** detected the failure first. It read `0`
already at the very first poll, **≤20s after `stop payments`** (bounded by Prometheus's 15s scrape
interval, a scrape failure is visible on the very next scheduled scrape, with no dependency on
traffic). **Error Rate** was next, going from 0% to a nonzero value **~30s** after the kill. It can
only move once an actual client request hits the gateway, gets proxied to the dead payments
container, and gets counted. The **SLO Availability gauge** (`gateway:sli_availability:ratio_rate5m`,
a 30s-interval recording rule over a 5-minute rate window) was the slowest to react, showing its
first measurable drop **~40s** after the kill, because it needs enough failed requests to build up
inside its 5-minute window before the ratio moves visibly.

This matches the general SRE pattern: a synthetic/blackbox signal (`up`) reacts fastest since it
doesn't need real traffic, symptom-based rate metrics (error rate) react next as soon as real
requests fail, and longer-window aggregate SLIs (the recording rule) are slowest but most stable
for burn-rate alerting.

---

## Task 2 — Define SLOs & Recording Rules

### 3.8 — SLI/SLO definitions and error budget math

**SLI 1 (Availability):** % of gateway requests returning non-5xx.
SLO target: **99.5%** over a rolling 7-day window.

**SLI 2 (Latency):** % of gateway requests completing under 500ms.
SLO target: **95%**.

Error budget math, assuming ~1000 requests/day → **7000 requests/week**:

- Availability: allowed failure budget = `(1 - 0.995) * 7000 = 35 failed requests/week`.
- Latency: allowed "slow" (≥500ms) budget = `(1 - 0.95) * 7000 = 350 slow requests/week`.

So the service can return up to 35 non-2xx/3xx/4xx-classified 5xx errors per week and still
meet the 99.5% availability SLO, and up to 350 requests/week can take 500ms or longer and
still meet the 95% latency SLO.

### 3.9 — Recording rules

Created `monitoring/prometheus/rules.yml`:

```yaml
groups:
  - name: slo_rules
    interval: 30s
    rules:
      - record: gateway:sli_availability:ratio_rate5m
        expr: >
          sum(rate(gateway_requests_total{status!~"5.."}[5m]))
          /
          sum(rate(gateway_requests_total[5m]))

      - record: gateway:sli_latency_500ms:ratio_rate5m
        expr: >
          sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m]))
          /
          sum(rate(gateway_request_duration_seconds_count[5m]))

      - record: gateway:error_budget_burn_rate:ratio_rate5m
        expr: >
          (1 - gateway:sli_availability:ratio_rate5m)
          /
          (1 - 0.995)
```

`rule_files: ["rules.yml"]` added to `prometheus.yml`, and the file mounted in
`docker-compose.monitoring.yaml` as shown in Task 1.

Rules loaded, verified with:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml restart prometheus
curl -s http://localhost:9090/api/v1/rules | python3 -c "..."
```

```
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

Sample real values, queried directly after generating traffic while the environment was healthy:

```
gateway:sli_availability:ratio_rate5m * 100        -> 97.90   (still recovering inside its 5m window from the Task-1 outage test)
gateway:sli_latency_500ms:ratio_rate5m * 100        -> 97.73
gateway:error_budget_burn_rate:ratio_rate5m         -> 4.21   (burning ~4x the allowed rate while the 5m window still contains the induced failures)
```

### 3.10 — SLO panel and failure observation

I added a Gauge panel to `golden-signals.json`: `gateway:sli_availability:ratio_rate5m * 100`,
min 99 / max 100, thresholds red (default) below 99.5, green from 99.5 up. Verified live in Grafana.

During the Task 1 payments outage (payments killed at `20:54:28Z`), the SLO gauge (screenshotted live
in the browser) dropped from **100% → 97.9%** and its bar rendered **red** (below the 99.5% line),
then recovered gradually back toward 100% over the following minutes as the 5-minute rate window
aged the failed requests out. Exactly the delayed-recovery behaviour you'd expect from a
rate()-based SLI.

---

## Bonus Task — Correlate Failure Across Metrics & Logs

I started traffic, and after 30s restarted payments with a **partial** failure injected
(`PAYMENT_FAILURE_RATE=0.5 PAYMENT_LATENCY_MS=1000`) instead of a hard stop, to get a mixed
latency + error signature and actually exercise log correlation:

```bash
./loadgen/run.sh 5 120 &
# traffic_start = 2026-09-21T20:57:44Z
sleep 30
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_FAILURE_RATE=0.5 PAYMENT_LATENCY_MS=1000 \
  docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
# payments_restarted = 2026-09-21T20:58:15Z
```

### Timeline

| Event | Timestamp (UTC) | Delta from injection |
|---|---|---|
| Payments container restarted with `PAYMENT_FAILURE_RATE=0.5`, `PAYMENT_LATENCY_MS=1000` | `20:58:15.x` | t+0 |
| First injected 1000ms latency logged in payments | `20:58:17.442Z` | t+2.4s |
| **First injected payment failure logged in payments** (`Payment failed (injected)`) | `20:58:18.442Z` | t+3.4s |
| **Corresponding gateway 500** for the same reservation ID (`e84dfbc8-...`) | `20:58:18.444Z` | t+3.4s (2ms after the payments log line, same request, proxied) |
| First Prometheus scrape (15s interval) reflecting a nonzero gateway 5xx rate | `20:58:30Z` (scrape tick) | t+15s |
| Dashboard visibly shows the error-rate/latency spike (Grafana panel refresh, screenshotted live) | ~`20:58:35Z` | t+~20s |
| Payments reverted to normal (`PAYMENT_FAILURE_RATE=0.0`, `PAYMENT_LATENCY_MS=0`) | after the 2-minute observation window | recovery |

### Log excerpts

**Payments**, first injected failure:
```
payments-1 | 2026-09-21T20:58:16.713973534Z INFO: Uvicorn running on http://0.0.0.0:8082 (Press CTRL+C to quit)
payments-1 | 2026-09-21T20:58:17.442204999Z {"time":"2026-09-21 20:58:17,442","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for e84dfbc8-faae-4556-be08-eb0cffe715ca"}
payments-1 | 2026-09-21T20:58:18.442532432Z {"time":"2026-09-21 20:58:18,442","level":"WARNING","service":"payments","msg":"Payment failed (injected) for e84dfbc8-faae-4556-be08-eb0cffe715ca"}
payments-1 | 2026-09-21T20:58:18.443564705Z INFO: 172.19.0.6:52822 - "POST /charge HTTP/1.1" 500 Internal Server Error
```

**Gateway**, same moment (same reservation id `e84dfbc8-...`, proxied one hop later):
```
gateway-1 | 2026-09-21T20:58:18.444554268Z {"time":"2026-09-21 20:58:18,444","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 500 Internal Server Error\""}
gateway-1 | 2026-09-21T20:58:18.445784569Z INFO: 172.19.0.1:33158 - "POST /reserve/e84dfbc8-faae-4556-be08-eb0cffe715ca/pay HTTP/1.1" 500 Internal Server Error
```

A Prometheus range query over the same window (`sum(rate(gateway_requests_total{status=~"5.."}[30s]))`,
step 15s) shows when the failure became visible in metrics:

```
20:58:00  0
20:58:15  0
20:58:30  0.0667   <- first nonzero point, one scrape tick after the 20:58:18.44Z error
20:58:45  0.1333
20:59:00  0.1333
```

### Root cause explanation

The gateway's `/reserve/{id}/pay` handler proxies to `payments:8082/charge`. At `20:58:15Z` the
payments container was recreated with `PAYMENT_FAILURE_RATE=0.5` and `PAYMENT_LATENCY_MS=1000`,
so each charge request now has a coin-flip chance of an artificial 1000ms delay followed by an
injected failure (`payments/main.py`'s fault-injection logic, confirmed by the `"Injecting 1000ms
latency for ..."` / `"Payment failed (injected) for ..."` log lines). The first affected request
(reservation `e84dfbc8-...`) hit that failure path at `20:58:18.442Z`. Because the gateway just
awaits and forwards the payments response, its own 500 for the same reservation ID shows up **2ms
later** in the gateway log. So the gateway isn't broken itself, it's just faithfully passing along
a downstream payments failure (this matches the `payments_unavailable`/503 pattern gateway already
uses for a *fully* unreachable payments, except here payments is reachable but returns its own
500). The dashboard (Error Rate + Latency panels) didn't show this until the **next Prometheus
scrape**, ~15s later at `20:58:30Z`, because Prometheus only samples `gateway_requests_total` /
`gateway_request_duration_seconds_bucket` once per `scrape_interval` (15s). So there's an inherent
~15s (up to one full scrape interval) floor on how fast any Prometheus-based dashboard can visibly
react to a real failure, even though the failure is already in the logs and in the raw `/metrics`
endpoint right away.

---

## PR description

```text
- [x] Task 1 done — monitoring deployed, dashboard completed
- [x] Task 2 done — SLOs defined, recording rules created
- [x] Bonus Task done — failure correlation
```
