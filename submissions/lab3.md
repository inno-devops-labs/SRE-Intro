# Lab 3 — Monitoring, Observability & SLOs

## Task 1 — Monitoring & Golden Signals

### Prometheus Configuration

Prometheus scrapes the three application services by their Docker Compose DNS
names and internal ports every 15 seconds. Rule evaluation also runs every 15
seconds, with `rules.yml` loaded from a read-only mount.

`promtool` validation:

```text
Checking /etc/prometheus/prometheus.yml
  SUCCESS: 1 rule files found
 SUCCESS: /etc/prometheus/prometheus.yml is valid prometheus config file syntax

Checking /etc/prometheus/rules.yml
  SUCCESS: 3 rules found
```

### Monitoring Stack

The full stack was started with both Compose files. All seven services were
running; PostgreSQL and Redis also passed their health checks:

```text
NAME               IMAGE                     COMMAND                  SERVICE      CREATED          STATUS                    PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       46 seconds ago   Up 38 seconds             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      45 seconds ago   Up 38 seconds             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      46 seconds ago   Up 44 seconds             0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     46 seconds ago   Up 44 seconds             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     12 days ago      Up 44 seconds (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   46 seconds ago   Up 44 seconds             0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        12 days ago      Up 44 seconds (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

### Prometheus Targets

After more than two scrape intervals, the targets API reported:

```text
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

### Metric Labels and Custom Metrics

The live gateway exposition confirmed that `status` is the HTTP status label
and `le` is the histogram bucket boundary label:

```text
gateway_requests_total{method="GET",path="/events",status="200"} 56.0
gateway_requests_total{method="POST",path="/events/{id}/reserve",status="200"} 24.0
gateway_requests_total{method="POST",path="/reserve/{id}/pay",status="200"} 13.0
gateway_request_duration_seconds_bucket{le="0.005",method="GET",path="/events"} 28.0
gateway_request_duration_seconds_bucket{le="0.01",method="GET",path="/events"} 54.0
gateway_request_duration_seconds_bucket{le="0.025",method="GET",path="/events"} 55.0
```

Custom application metrics discovered through Prometheus after generating
traffic:

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

### Baseline Traffic and Request Rate

`./loadgen/run.sh 5 20` completed with 80 successful requests and no failures.
After the next scrape, this query returned real data:

```promql
sum(rate(gateway_requests_total[5m]))
```

```text
Request rate: 0.31 req/s
```

The five-minute rate is lower than the generator's instantaneous 5 req/s
because traffic existed for only 20 seconds of the five-minute lookback.

### Golden Signals Dashboard

The version-controlled provisioned dashboard was completed rather than editing
only Grafana's runtime database. Grafana's dashboard API returned these panels:

```text
QuickTicket — Golden Signals
timeseries | Request Rate (Traffic) | 1 queries
timeseries | Error Rate | 1 queries
table | Service Health (up/down) | 1 queries
timeseries | Gateway Latency (p50 / p95 / p99) | 3 queries
gauge | Events DB Pool Saturation | 1 queries
gauge | Availability SLO | 1 queries
```

#### Latency

The time-series panel uses seconds and these three queries:

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

Baseline live values were p50 `0.00627 s`, p95 `0.00978 s`, and p99
`0.07679 s`.

#### Saturation

```promql
events_db_pool_size
```

The gauge has min 0, max 10, green as the default, yellow at 7, and red at 9.
The observed baseline value was `0` active checked-out connections between
requests.

### Failure Injection — Payments Stopped

The load generator ran at 5 req/s for 120 seconds. Payments was stopped at
`2026-09-20T15:00:39+03:00` and restarted at
`2026-09-20T15:01:43+03:00`. Prometheus was polled every two seconds during
the incident.

#### Normal traffic

- All three scrape targets were up.
- The baseline load generated no 5xx responses.
- Availability was `1` (100%), the under-500-ms latency ratio was `1`, and
  burn rate was `0`.
- Baseline p50/p95/p99 were `0.00627 / 0.00978 / 0.07679 s`.
- DB pool saturation was `0/10` at scrape time.

#### Payments failure

Observed signal sequence:

| Time | Delay from stop | Observation |
| --- | ---: | --- |
| 15:00:39 +03:00 | 0 s | `payments` stopped |
| 15:00:41 +03:00 | 2 s | `up{job="payments"}` first became 0 |
| 15:00:58 +03:00 | 19 s | gateway 5xx rate first became non-zero (`0.23494 req/s`) |
| 15:01:07 +03:00 | 28 s | availability first fell below 99.5% (`0.92436`), burn rate `15.13` |
| 15:01:43 +03:00 | 64 s | peak observation taken and payments restarted |

At the peak observation:

```text
request rate                         = 4.7733 req/s
5xx error percentage                 = 12.5701%
p50 / p95 / p99                      = 0.00560 / 0.00967 / 0.01415 s
events DB pool size                  = 0
availability (rolling 5m)            = 0.90016
error-budget burn rate (rolling 5m)  = 19.9677
```

Stopping payments caused connection failures to return quickly, so error rate
and availability changed while latency percentiles did not rise. Saturation
also remained unchanged because the incident was isolated to payments rather
than the events database.

#### Recovery

At `15:02:03 +03:00`, 20 seconds after restart, the payments target was back
at `up = 1`. The one-minute 5xx percentage had begun falling but was still
`7.70%`. Availability and burn rate initially remained at `0.90016` and
`19.97`, as expected: their recording rules use a rolling five-minute window,
so pre-recovery failures remain in the calculation until they age out.

#### Which golden signal detected the failure first?

**Service Health was first**, two seconds after payments was stopped. Traffic
continued, but the first non-zero gateway error rate followed 19 seconds after
the stop and the availability SLO breach followed at 28 seconds.

---

## Task 2 — SLOs & Recording Rules

### SLI 1 — Availability

The SLI is the percentage of gateway requests that do not return HTTP 5xx.
The SLO is **99.5% availability over a seven-day window**.

### SLI 2 — Latency

The SLI is the percentage of gateway requests completing in under 500 ms. The
SLO target is **95%**. The recording rule computes a rolling five-minute view;
no additional long-term objective window is assumed.

### Error Budget

At approximately 1,000 requests per day:

```text
1,000 requests/day × 7 days = 7,000 requests/week
100% - 99.5% = 0.5% error budget
7,000 × 0.005 = 35 failures/week
```

The weekly availability error budget therefore permits approximately **35
failed requests**.

### Recording Rules

Availability treats an absent 5xx series as zero failures. This avoids `No
Data` during a healthy period before the first-ever 5xx response:

```promql
gateway:sli_availability:ratio_rate5m =
  1 - (
    (sum(rate(gateway_requests_total{status=~"5.."}[5m])) or vector(0))
    /
    sum(rate(gateway_requests_total[5m]))
  )

gateway:sli_latency_500ms:ratio_rate5m =
  sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m]))
  /
  sum(rate(gateway_request_duration_seconds_count[5m]))

gateway:error_budget_burn_rate:ratio_rate5m =
  (1 - gateway:sli_availability:ratio_rate5m) / (1 - 0.995)
```

### Rules Loaded

```text
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

Baseline evaluated values:

```text
gateway:sli_availability:ratio_rate5m = 1
gateway:sli_latency_500ms:ratio_rate5m = 1
gateway:error_budget_burn_rate:ratio_rate5m = 0
```

A burn rate below 1 spends budget slower than allowed, 1 exactly matches the
allowed rate, and a value above 1 spends budget too quickly.

### SLO Gauge

The dashboard contains a gauge using:

```promql
gateway:sli_availability:ratio_rate5m * 100
```

It has min 99, max 100, and a threshold at 99.5. The gauge was 100% at
baseline, fell to 90.02% during the stopped-payments incident, and then began
recovering after restart as failure samples aged out of the five-minute
window.

---

## Bonus Task — Metrics & Logs Correlation

### Experiment

Traffic started at `2026-09-20T15:04:27+03:00`. Payments was force-recreated
at `15:04:57` and ready at `15:04:58` with the following environment verified
inside the running container:

```text
PAYMENT_FAILURE_RATE=0.5
PAYMENT_LATENCY_MS=1000
```

The default configuration was restored at `15:06:00` and ready at `15:06:01`.
The replacement container was verified as:

```text
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
```

### Timeline

| Time (+03:00 unless marked UTC) | Event |
| --- | --- |
| 15:04:27 | 5 req/s load generator started |
| 15:04:57 | payments recreation with failure rate 0.5 and 1000 ms latency started |
| 15:04:58 | injected configuration ready and verified inside container |
| 15:05:27 | first Prometheus-observed error spike, 29 s after ready (`0.6601%`) |
| 15:05:43 | p95 first exceeded 500 ms, 45 s after ready (`1.4568 s`) |
| 15:06:00 | peak metrics captured; normal payments recreation started |
| 15:06:01 | normal configuration ready and verified |
| 15:06:47 | one-minute 5xx rate recovered to 0%; p95 recovered to `0.00988 s` |
| 12:07:49.248 UTC | correlation replay began 1000 ms delay for reservation `4d167a49-…` |
| 12:07:50.257 UTC | payments logged the injected failure for the same reservation |
| 12:07:50.262 UTC | gateway logged HTTP 500 for the same reservation |
| 15:07:53 | correlation replay restored to failure rate 0 and latency 0 |

Peak metrics from the two-minute load experiment:

```text
request rate                         = 3.4887 req/s
5xx error percentage                 = 1.9108%
p50 / p95 / p99                      = 0.00497 / 1.42955 / 2.28591 s
availability (rolling 5m)            = 0.94438
error-budget burn rate (rolling 5m)  = 11.1248
```

At `15:06:47`, after restoration, `up{job="payments"}` was 1, the one-minute
5xx percentage was 0, and p95 was back to `0.00988 s`. The five-minute SLI was
still recovering (`99.3255%`, burn rate `1.349`) because injected failures had
not fully aged out.

The load generator's overall failure count also included expected 409
responses from exhausted/held ticket inventory. The Prometheus observations
above deliberately use gateway 5xx only, so those client/business failures do
not inflate the availability incident.

### Correlated Payments Logs

The follow-up correlation replay used the same verified fault profile and
captured logs before recreating the payments container:

```text
payments-1 | 2026-09-20T12:07:49.248904007Z {"time":"2026-09-20 12:07:49,248","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for 4d167a49-8e77-4ac4-8854-11aa42eb224e"}
payments-1 | 2026-09-20T12:07:50.258919882Z {"time":"2026-09-20 12:07:50,257","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 4d167a49-8e77-4ac4-8854-11aa42eb224e"}
```

### Correlated Gateway Log

```text
gateway-1 | 2026-09-20T12:07:50.262315757Z INFO: 172.19.0.1:62820 - "POST /reserve/4d167a49-8e77-4ac4-8854-11aa42eb224e/pay HTTP/1.1" 500 Internal Server Error
```

The gateway response was logged about 5 ms after the payments failure log, and
the shared reservation ID establishes that both entries describe the same
request.

### Root Cause

The injected payments configuration added approximately one second to every
charge and failed about half of charge attempts. Payments logs show the delay
and injected failure for an exact reservation ID; the gateway then propagated
that downstream HTTP 500 for the same ID. Prometheus scraped the resulting 5xx
counter increase and the observations in the high-latency histogram buckets.
Grafana therefore showed a rising Error Rate, p95/p99 latency above one second,
availability below its objective, and error-budget burn above 1.

After payments was recreated with its normal `0.0` failure rate and `0 ms`
latency, new payment requests stopped failing, the one-minute error and latency
panels recovered, and the five-minute availability/burn rules recovered more
gradually as incident samples aged out of their rolling windows.
