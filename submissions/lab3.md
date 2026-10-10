# Lab 3 — Monitoring, Observability & SLOs

## Task 1 — Configure Monitoring & Build Dashboard

### 3.1 Repository Baseline and Prometheus Configuration

I created `feature/lab3` from the fetched `upstream/main` at `cf2ad63`. Lab 1 and Lab 2 remained on separate branches.

`git log --oneline upstream/main..HEAD` and `git diff --stat upstream/main...HEAD` produced no output. The branch initially contained no Lab 1 or Lab 2 changes.

I read the lab instructions, Compose files, Grafana configuration, service source code, and load generator. I created `monitoring/prometheus/prometheus.yml`:

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

Prometheus uses internal Docker service names and ports. Host port `3080` forwards to `gateway:8080`. Scraping and global rule evaluation run every 15 seconds; Task 2 sets a 30-second interval for its SLO group.

### 3.2 Start and Validate the Combined Stack

From `app/`, I started and inspected the combined stack:

```bash
PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0 \
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build

sleep 30

docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
```

Compose output:

```text
NAME               IMAGE                     COMMAND                  SERVICE      CREATED             STATUS                       PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       34 seconds ago      Up 30 seconds                0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      33 seconds ago      Up 30 seconds                0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      34 seconds ago      Up 30 seconds                0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     34 seconds ago      Up 31 seconds                0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     About an hour ago   Up About an hour (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   34 seconds ago      Up 31 seconds                0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        About an hour ago   Up About an hour (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

All seven services were running. PostgreSQL and Redis passed their Compose healthchecks; the following commands checked Prometheus configuration and health endpoints.

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml \
  exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
```

```text
Checking /etc/prometheus/prometheus.yml
 SUCCESS: /etc/prometheus/prometheus.yml is valid prometheus config file syntax
```

```bash
curl -fsS http://localhost:3080/health | python3 -m json.tool
curl -fsS http://localhost:8082/health | python3 -m json.tool
curl -fsS http://localhost:3000/api/health | python3 -m json.tool
```

The three health responses, in command order:

```json
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
{
    "status": "healthy",
    "failure_rate": 0.0,
    "latency_ms": 0
}
{
    "database": "ok",
    "version": "13.0.1",
    "commit": "a100054f"
}
```

### 3.3 Verify Prometheus Targets

The `/api/v1/targets` check returned:

```text
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

### 3.4 Explore Metrics and Generate Traffic

```bash
curl -fsS http://localhost:3080/metrics | python3 -c '
import sys
lines = [line.rstrip() for line in sys.stdin if line.startswith("gateway_")]
print("\n".join(lines[:25]))
'
```

Relevant output:

```text
gateway_requests_total{method="GET",path="/health",status="200"} 1.0
gateway_request_duration_seconds_bucket{le="0.025",method="GET",path="/health"} 0.0
gateway_request_duration_seconds_bucket{le="0.05",method="GET",path="/health"} 1.0
gateway_request_duration_seconds_bucket{le="0.5",method="GET",path="/health"} 1.0
gateway_request_duration_seconds_bucket{le="+Inf",method="GET",path="/health"} 1.0
gateway_request_duration_seconds_count{method="GET",path="/health"} 1.0
gateway_request_duration_seconds_sum{method="GET",path="/health"} 0.03435492515563965
```

Gateway counters have `method`, `path`, and `status` labels. Its histogram has `method` and `path`, with `le` added to bucket series. I verified the installed default buckets:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml \
  exec -T gateway python -c \
  'from prometheus_client import Histogram; print("Default histogram buckets:", Histogram.DEFAULT_BUCKETS)'
```

```text
Default histogram buckets: (0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0, inf)
```

Initial traffic:

```bash
./loadgen/run.sh 5 20
sleep 20
```

```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 20s
Done. total=73 success=73 fail=0 error_rate=0%
```

The `/api/v1/label/__name__/values` check returned the complete custom metrics list:

```text
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

The executed request-rate query was:

```promql
sum(rate(gateway_requests_total[5m]))
```

The API returned `0.31113518679023044`. The analysis printed:

```text
Request rate: 0.3111 req/s
```

Counters accumulate events and can reset on restart; gauges report current values; histograms record cumulative buckets, a count, and a sum. `rate()` estimates counter growth per second while accounting for resets.

The short burst was evaluated over a five-minute window. The load generator runs scenarios sequentially with pauses, and purchase scenarios make multiple HTTP requests, so its configured rate and the observed HTTP rate differ.

### 3.5 Complete the Golden Signals Dashboard

I edited `QuickTicket — Golden Signals`. In the dashboard provider, I replaced `editable: true` with `allowUiUpdates: true`; UI changes were later exported to the repository.

**Latency:** Time series, seconds, minimum 0, automatic maximum, and three range queries with literal legends `p50`, `p95`, and `p99`:

```promql
histogram_quantile(0.50, sum by (le) (rate(gateway_request_duration_seconds_bucket[1m])))

histogram_quantile(0.95, sum by (le) (rate(gateway_request_duration_seconds_bucket[1m])))

histogram_quantile(0.99, sum by (le) (rate(gateway_request_duration_seconds_bucket[1m])))
```

These estimate the durations within which 50%, 95%, and 99% of requests completed. Aggregation preserves `le` while combining paths and methods.

**Saturation:** An instant Gauge query:

```promql
events_db_pool_size
```

Settings: minimum 0, maximum 10, zero decimals, absolute thresholds, green Base, yellow at 7, and red at 9. The implementation samples `len(db_pool._used)` at `/metrics`; zero does not prove that no connections were used between scrapes.

**Error Rate:** I added a fallback for an absent 5xx series when total traffic exists:

```promql
(
  sum(rate(gateway_requests_total{status=~"5.."}[1m]))
  or
  (0 * sum(rate(gateway_requests_total[1m])))
)
/
sum(rate(gateway_requests_total[1m]))
* 100
```

With no traffic, the ratio can remain undefined. I did not replace undefined ratios with a success value. I set UTC and five-second refresh, and increased Service Health height to show all three rows.

Repeated loads produced HTTP 409 reservation conflicts before fault injection. PostgreSQL and Redis showed:

| Event | Total tickets | Confirmed | Held | Reservable |
|---|---:|---:|---:|---:|
| 2 | 30 | 15 | 15 | 0 |
| 4 | 25 | 11 | 14 | 0 |

The source increments held counters without decrementing them on confirmation or setting their TTL. I preserved the application and data. Load-generator failures include 409 scenarios, whereas the dashboard Error Rate measures 5xx HTTP responses.

### 3.6 Stop Payments and Observe the Golden Signals

I ran 240 seconds of load: approximately 60 seconds of baseline, two minutes with payments stopped, and recovery. The Python automation used Compose stop, not SIGKILL, recorded UTC timestamps and Docker `FinishedAt`, and restored payments in a `finally` block.

Recorded output:

```text
Evidence directory: /Users/glebshvetsov/sre-lab3-task1-t_45sb0d
2026-09-13T15:56:26.054807+00:00 BASELINE_START
2026-09-13T15:57:26.060397+00:00 STOP_REQUESTED
2026-09-13T15:57:27.087300+00:00 STOP_COMPLETED
Container FinishedAt: 2026-09-13T15:57:26.788715803Z
2026-09-13T15:58:27.166518+00:00 PAYMENTS_DOWN_MINUTE_1
2026-09-13T15:59:27.173343+00:00 PAYMENTS_DOWN_MINUTE_2
2026-09-13T15:59:27.175166+00:00 RECOVERY_REQUESTED
2026-09-13T15:59:27.634172+00:00 START_COMPLETED
2026-09-13T16:00:26.170428+00:00 EXPERIMENT_END
Done. total=938 success=773 fail=165 error_rate=17.5%
```

First relevant gateway log entries:

```text
gateway-1  | 2026-09-13T15:57:28.802496595Z {"time":"2026-09-13 15:57:28,802","level":"ERROR","service":"gateway","msg":"payment error: [Errno -2] Name or service not known"}
gateway-1  | 2026-09-13T15:57:28.803481845Z INFO:     192.168.65.1:22641 - "POST /reserve/5cfecc64-b037-41d4-97a8-058f1f4302db/pay HTTP/1.1" 502 Bad Gateway
```

### 3.7 Compare Signals and Detection Times

I queried `/api/v1/query_range` over the recorded interval with a one-second evaluation step and saved the raw responses. Queries covered Error Rate, latency, `sum(rate(gateway_requests_total[1m]))`, `events_db_pool_size`, and `up{job="payments"}`.

Successful analysis output:

```text
error_pct
baseline min/max: (0.0, 0.0)
failure min/max: (0.0, 7.6546973360990425)
First failure indication: 2026-09-13T15:57:57+00:00 value: 4.875228552449476 seconds after container exit: 30.211

traffic_rps
baseline min/max: (0.06666666666666665, 4.044534322984956)
failure min/max: (3.955291869430927, 4.25928099687287)

db_active
baseline min/max: (0.0, 0.0)
failure min/max: (0.0, 0.0)

payments_up
baseline min/max: (1.0, 1.0)
failure min/max: (0.0, 1.0)
First failure indication: 2026-09-13T15:57:35+00:00 value: 0.0 seconds after container exit: 8.211

p50
baseline min/max: (0.0075, 0.007884615384615384)
failure min/max: (0.007344961240310078, 0.007954545454545454)

p95
baseline min/max: (0.022449999999999984, 0.04625)
failure min/max: (0.018464285714285704, 0.022671052631578946)

p99
baseline min/max: (0.04925, 0.06712499999999995)
failure min/max: (0.02369285714285714, 0.04024999999999992)
```

**Errors were the first unambiguous failure indicator among the four golden signals:** Error Rate became positive at **15:57:57 UTC**, approximately **30.2 seconds after container exit**, or **30.9 seconds after the stop request**. Scrape health changed earlier, after 8.2 seconds, but `up` is a separate indicator.

Traffic stayed near 4 req/s. Latency did not increase because the gateway returned fast DNS-related 502 errors. Sampled DB usage remained zero. The dashboard showed an error spike around 5.5%; finer query evaluation found 7.655%.

A one-second query step does not provide one-second scrape resolution: scraping remained every 15 seconds and dashboard refresh every five seconds. Browser-render timing was not independently measured. After recovery, all seven services were running and payment injection parameters were `0.0` / `0`.

## Task 2 — Define SLOs & Recording Rules

### 3.8 SLI Definitions, SLOs, and Error Budget

| SLI | Definition | SLO |
|---|---|---|
| Availability | Fraction of gateway requests returning non-5xx responses | 99.5% over seven days |
| Latency | Fraction in the histogram bucket with `le="0.5"` | 95% |

The latency bucket includes **500 ms or less**. Availability treats 4xx as acceptable, so reservation conflicts can coexist with 100% availability under this SLI; this does not mean every user action succeeded.

```text
Requests per week = 1000 × 7 = 7000
Allowed failure fraction = 1 − 0.995 = 0.005 = 0.5%
Allowed failed requests = 7000 × 0.005 = 35 per week
```

An SLI is a measurement; an SLO is its target over an assessment window. The error budget is the allowed failure amount. Burn rate compares the observed failure fraction with the allowed 0.5%: 1 is the planned rate, and 2 is twice that rate.

The following five-minute rules are operational indicators and do not establish compliance with the seven-day SLO.

### 3.9 Create and Load Recording Rules

I created `monitoring/prometheus/rules.yml`:

```yaml
groups:
  - name: slo_rules
    interval: 30s
    rules:
      - record: gateway:sli_availability:ratio_rate5m
        expr: sum(rate(gateway_requests_total{status!~"5.."}[5m])) / sum(rate(gateway_requests_total[5m]))
      - record: gateway:sli_latency_500ms:ratio_rate5m
        expr: sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m])) / sum(rate(gateway_request_duration_seconds_count[5m]))
      - record: gateway:error_budget_burn_rate:ratio_rate5m
        expr: (1 - gateway:sli_availability:ratio_rate5m) / (1 - 0.995)
```

I added the rule reference to `prometheus.yml` and a Prometheus volume to `docker-compose.monitoring.yaml`, respectively:

```yaml
rule_files:
  - "rules.yml"
```

```yaml
- ../monitoring/prometheus/rules.yml:/etc/prometheus/rules.yml:ro
```

Validation from the repository root:

```bash
docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml \
  run --rm --no-deps --entrypoint promtool prometheus \
  check config /etc/prometheus/prometheus.yml
```

```text
Checking /etc/prometheus/prometheus.yml
  SUCCESS: 1 rule files found
 SUCCESS: /etc/prometheus/prometheus.yml is valid prometheus config file syntax

Checking /etc/prometheus/rules.yml
  SUCCESS: 3 rules found
```

A restart would not apply the new mount, so I applied the changed service configuration with `up`. Initial commands from `app/`:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml \
  up -d --no-deps prometheus

sleep 15
./loadgen/run.sh 5 60
sleep 30
```

The subsequent `/api/v1/rules` check returned:

```text
API status: success
Group: slo_rules | Interval: 30 seconds
gateway:sli_availability:ratio_rate5m = ok
gateway:sli_latency_500ms:ratio_rate5m = ok
gateway:error_budget_burn_rate:ratio_rate5m = ok
```

Querying `{__name__=~"gateway:.*:ratio_rate5m"}` through `/api/v1/query` returned:

```text
API status: success
gateway:sli_availability:ratio_rate5m = 1
gateway:sli_latency_500ms:ratio_rate5m = 1
gateway:error_budget_burn_rate:ratio_rate5m = 0
```

### 3.10 Availability SLO Gauge and Failure Observation

I added `Availability SLO (99.5%)` as a Gauge with this instant query:

```promql
gateway:sli_availability:ratio_rate5m * 100
```

Settings: Percent (0–100), minimum 99, maximum 100, two decimals, absolute thresholds, red Base, green at 99.5. I disabled the Gradient effect and verified that the historical **99.32%** value appeared red.

During idle time the ratio returned `NaN`; fresh traffic produced 100%. I retained the undefined idle value.

The second experiment ran 210 seconds of traffic: approximately 60 seconds of baseline, 60 seconds with payments stopped, then recovery. The automation restored payments in `finally` and saved recording-rule samples every 20 seconds.

```text
Evidence directory: /Users/glebshvetsov/sre-lab3-task2-zu2rtvh8
2026-09-13T18:01:58.058875+00:00 BASELINE_START
2026-09-13T18:02:58.143273+00:00 STOP_REQUESTED
2026-09-13T18:02:59.007046+00:00 STOP_COMPLETED
2026-09-13T18:03:59.084735+00:00 RECOVERY_REQUESTED
2026-09-13T18:03:59.616949+00:00 START_COMPLETED
2026-09-13T18:05:28.233133+00:00 EXPERIMENT_END
Done. total=822 success=633 fail=189 error_rate=22.9%
```

Selected actual recording-rule samples:

```text
BASELINE {'gateway:sli_availability:ratio_rate5m': '1', 'gateway:sli_latency_500ms:ratio_rate5m': '1', 'gateway:error_budget_burn_rate:ratio_rate5m': '0'}
FAILURE {'gateway:sli_availability:ratio_rate5m': '0.9971098265895953', 'gateway:sli_latency_500ms:ratio_rate5m': '1', 'gateway:error_budget_burn_rate:ratio_rate5m': '0.5780346820809296'}
RECOVERY {'gateway:sli_availability:ratio_rate5m': '0.9935622317596566', 'gateway:sli_latency_500ms:ratio_rate5m': '1', 'gateway:error_budget_burn_rate:ratio_rate5m': '1.2875536480686722'}
RECOVERY {'gateway:sli_availability:ratio_rate5m': '0.9931856899488928', 'gateway:sli_latency_500ms:ratio_rate5m': '1', 'gateway:error_budget_burn_rate:ratio_rate5m': '1.3628620102214428'}
```

The gauge showed **100.00% → 99.71% → 99.32% → 99.52%**. The lowest sampled availability fell below 99.5%, with burn rate about 1.363. This is a short-window breach of the target, not proof of weekly SLO violation.

Availability continued falling briefly after restart because the five-minute window retained failures and rules updated every 30 seconds. Gateway health and normal payment parameters were verified after recovery.

## Bonus Task — Correlate Failure Across Metrics & Logs

### 3.11 Inject Payment Errors and Latency

Before injection, only event 3 had reservable capacity: `500 − 35 − 151 = 314`. The unchanged load generator selected events 1–5, so some scenarios returned 409 without reaching payments. I did not reset data.

The automation ran 210 seconds of traffic: 30 seconds of baseline, approximately 120 seconds of injection, then recovery. It recreated payments with failure probability 0.5 and latency 1000 ms, checked `/health`, preserved fault logs before replacement, and restored `0.0` / `0` in `finally`.

Exact excerpt from the executed `configure()` helper, not a standalone script:

```python
def configure(rate, latency):
    env = dict(os.environ, PAYMENT_FAILURE_RATE=rate, PAYMENT_LATENCY_MS=latency)
    sp.run(dc + ["up", "-d", "--no-deps", "--force-recreate", "payments"],
           env=env, check=True)
```

Recorded output:

```text
Evidence directory: /Users/glebshvetsov/sre-lab3-bonus-bd7na9ei
2026-09-13T18:09:54.315252+00:00 BASELINE_START
2026-09-13T18:10:24.620936+00:00 INJECTION_REQUESTED
2026-09-13T18:10:26.441236+00:00 INJECTION_READY
2026-09-13T18:11:26.443004+00:00 INJECTION_MINUTE_1
2026-09-13T18:12:26.778640+00:00 RECOVERY_REQUESTED
2026-09-13T18:12:28.585957+00:00 RECOVERY_READY
2026-09-13T18:13:24.221479+00:00 EXPERIMENT_END
Done. total=771 success=595 fail=176 error_rate=22.8%
Restored payments: {'status': 'healthy', 'failure_rate': 0.0, 'latency_ms': 0}
```

### 3.12 Correlate the Same Reservation Across Logs

I extracted the first injected failure's reservation ID and searched for it in payments and gateway logs:

```bash
cd /Users/glebshvetsov/sre-lab3-bonus-bd7na9ei

grep -m 1 'Payment failed (injected)' payments-fault.log

LAB3_FAILED_ID=$(grep -m 1 'Payment failed (injected)' payments-fault.log | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')

if [ -n "$LAB3_FAILED_ID" ]; then
  grep -F "$LAB3_FAILED_ID" payments-fault.log
  grep -F "$LAB3_FAILED_ID" gateway.log
fi

grep '^payments_charges_total' payments-fault.metrics
```

```text
payments-1  | 2026-09-13T18:10:32.646092840Z {"time":"2026-09-13 18:10:32,644","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for d99669f2-0427-40c9-96ea-68510da3a745"}
payments-1  | 2026-09-13T18:10:33.649946174Z {"time":"2026-09-13 18:10:33,648","level":"WARNING","service":"payments","msg":"Payment failed (injected) for d99669f2-0427-40c9-96ea-68510da3a745"}
gateway-1  | 2026-09-13T18:10:33.666232757Z INFO:     192.168.65.1:19455 - "POST /reserve/d99669f2-0427-40c9-96ea-68510da3a745/pay HTTP/1.1" 500 Internal Server Error
```

The matching ID links the injected failure to the gateway response. The delay-to-failure log interval is approximately **1.004 seconds**, consistent with the configured 1000 ms delay.

Charge counters immediately before recovery:

```text
payments_charges_total{result="failed"} 6.0
payments_charges_total{result="success"} 5.0
```

Six of eleven charges failed, approximately **54.5%**. A 50% probability does not require exactly half of a small sample to fail.

I similarly extracted the first success ID from `payments-recovery.log` and matched it against `gateway.log`. Relevant output:

```text
payments-1  | 2026-09-13T18:12:30.935100506Z {"time":"2026-09-13 18:12:30,934","level":"INFO","service":"payments","msg":"Payment success: PAY-AC152C76 for a8bff634-7897-4beb-af12-fec7c800d4d9"}
gateway-1  | 2026-09-13T18:12:30.942006464Z INFO:     192.168.65.1:63142 - "POST /reserve/a8bff634-7897-4beb-af12-fec7c800d4d9/pay HTTP/1.1" 200 OK
```

### 3.13 Metric Timeline and Root Cause

I queried the recorded interval at a one-second evaluation step. Besides dashboard queries, this diagnostic query isolated payment-route latency:

```promql
histogram_quantile(0.95, sum by (le) (rate(gateway_request_duration_seconds_bucket{path="/reserve/{id}/pay"}[1m])))
```

The successful Python analysis read `timeline.log`, queried `/api/v1/query_range`, saved responses, excluded non-finite values, and calculated phase ranges and first threshold crossings. Output:

```text
METRIC: error_pct
baseline min/max: (0.0, 0.0)
injected min/max: (0.0, 2.537779137472618)
First above 0 : ('2026-09-13T18:10:56+00:00', 1.4176761254066623)
First zero after recovery: 2026-09-13T18:12:56+00:00

METRIC: p99_seconds
baseline min/max: (0.099, 0.154)
injected min/max: (0.21700000000000044, 2.0169999999999995)
First above 0.5 : ('2026-09-13T18:10:41+00:00', 1.2174999999999998)

METRIC: pay_p95_seconds
baseline min/max: (0.0725, 0.0725)
injected min/max: (0.07125, 2.425)
First above 0.5 : ('2026-09-13T18:10:41+00:00', 2.3125)

METRIC: payments_up
baseline min/max: (1.0, 1.0)
injected min/max: (1.0, 1.0)
```

Latency crossed 500 ms at **18:10:41 UTC**, before Error Rate became positive at **18:10:56 UTC**. These are reconstructed metric evaluations, not browser-render timestamps. Screenshots also showed the error spike and return to zero.

**Root cause:** charge requests received a 1000 ms delay and a configured 0.5 failure probability. Matching reservation IDs connect the payment failure to the gateway 500. Aggregate gateway errors stayed well below 50% because most requests were reads or reservations, and many reservations returned 409 before reaching payments.

Estimated gateway p99 reached 2.017 seconds and payment-route p95 reached 2.425 seconds. Requests slightly above one second enter the wide 1.0–2.5-second bucket; histogram interpolation can produce these estimates even though the correlated charge's log interval was about 1.004 seconds.

Payments `up` remained 1 at sampled times because `/metrics` stayed reachable. Scrape health did not reveal partial payment failures, and brief recreation gaps could fall between scrapes.

### 3.14 Verify Recovery and Export the Dashboard

After the Bonus experiment, I checked the stack and health endpoints:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
curl -fsS http://localhost:3080/health | python3 -m json.tool
curl -fsS http://localhost:8082/health | python3 -m json.tool
```

All seven containers were running. Gateway returned `healthy`, with events/payments `ok` and `circuit_payments: CLOSED`; payments returned `healthy`, `failure_rate: 0.0`, and `latency_ms: 0`.

I exported the saved dashboard through Grafana's API into the repository, preserving its UID and six panels, removing instance-specific ID/version, and setting a relative 15-minute range, UTC, and five-second refresh.

```text
Exported: monitoring/grafana/dashboards/golden-signals.json
UID: quickticket-golden-signals
Time: {'from': 'now-15m', 'to': 'now'} | Timezone: utc
timeseries | Request Rate (Traffic)
timeseries | Error Rate
table | Service Health (up/down)
timeseries | Latency (p50 / p95 / p99)
gauge | Saturation (DB connections)
gauge | Availability SLO (99.5%)
```

The local experiment directories contain the original logs, timestamps, and API responses; relevant evidence is reproduced above. They are not repository dependencies.

Deliverables: `monitoring/prometheus/prometheus.yml`, `monitoring/prometheus/rules.yml`, `docker-compose.monitoring.yaml`, `monitoring/grafana/provisioning/dashboards/dashboards.yml`, `monitoring/grafana/dashboards/golden-signals.json`, and `submissions/lab3.md`.
