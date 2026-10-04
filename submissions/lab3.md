# Lab 3 — Monitoring, Observability and SLOs

## Environment

The experiment used Docker Desktop on Windows. Docker was `29.2.1` and Docker Compose was `v5.0.2`.

## Monitoring Configuration

Prometheus is configured with `scrape_interval: 15s` and `evaluation_interval: 15s`. It scrapes the internal Compose addresses `gateway:8080`, `events:8081`, and `payments:8082`. The committed Prometheus configuration mounts `rules.yml` read-only.

## Running Services

```text
NAME               IMAGE                     COMMAND                  SERVICE      CREATED          STATUS                    PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       39 minutes ago   Up 39 minutes             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      39 minutes ago   Up 39 minutes             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      39 minutes ago   Up 12 minutes             0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     2 minutes ago    Up 2 minutes              0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     4 days ago       Up 39 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   39 minutes ago   Up 39 minutes             0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        4 days ago       Up 39 minutes (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

## Prometheus Targets

The Prometheus Targets API reported all three application targets as `up`.

```json
{"labels":{"instance":"events:8081","job":"events"},"scrapeUrl":"http://events:8081/metrics","health":"up"}
{"labels":{"instance":"gateway:8080","job":"gateway"},"scrapeUrl":"http://gateway:8080/metrics","health":"up"}
{"labels":{"instance":"payments:8082","job":"payments"},"scrapeUrl":"http://payments:8082/metrics","health":"up"}
```

## Custom Metrics

The following is the relevant part of the actual Prometheus metric-name response after application traffic was generated.

```text
events_db_pool_size
events_orders_created
events_orders_total
events_request_duration_seconds_bucket
events_requests_total
events_reservations_active
gateway_request_duration_seconds_bucket
gateway_request_duration_seconds_count
gateway_requests_total
gateway:sli_availability:ratio_rate5m
gateway:sli_latency_500ms:ratio_rate5m
gateway:error_budget_burn_rate:ratio_rate5m
payments_charges_total
payments_request_duration_seconds_bucket
payments_requests_total
up
```

## Request Rate

The following query was executed against the Prometheus HTTP API.

```promql
sum(rate(gateway_requests_total[5m]))
```

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1789914532.361,"0.09242839411293392"]}]}}
```

## Golden Signals Dashboard

The provisioned dashboard has UID `quickticket-golden-signals`, tags `quickticket` and `sre`, refresh `5s`, and default range `now-15m`. It keeps the original Request Rate, Error Rate, and Service Health panels, and adds the three requested panels. Each panel has `gridPos`, `description`, and `fieldConfig`.

The Grafana API returned these provisioned panels:

```text
Request Rate (Traffic)       timeseries  gridPos 0,0
Error Rate                   timeseries  gridPos 12,0
Service Health (up/down)     table       gridPos 0,8
Latency                      timeseries  gridPos 0,12
Database Pool Saturation     gauge       gridPos 12,12
SLO Availability             gauge       gridPos 0,20
```

### Dashboard Queries

```promql
# Request Rate
sum(rate(gateway_requests_total[1m])) by (path)

# Error Rate
sum(rate(gateway_requests_total{status=~"5.."}[1m])) / sum(rate(gateway_requests_total[1m])) * 100

# Service Health
up

# Latency p50, p95 and p99
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))

# Database Pool Saturation
events_db_pool_size

# SLO Availability
gateway:sli_availability:ratio_rate5m * 100
```

The saturation gauge range is 0–10, with yellow at 7 and red at 9. The SLO availability gauge range is 99–100%, with the healthy threshold at 99.5%.

## Failure Experiment

Payments was first verified in its normal configuration. I then created ten real reservations, stopped the `payments` Compose service, waited about five seconds, and sent pay requests for those reservations. This was a controlled fallback after the transient Alpine load-generator container could not install its required packages; no synthetic values were added to the report.

```text
2026-09-20T17:22:57.9797566+03:00  payments stop requested
2026-09-20T17:23:04.2116454+03:00  payment requests sent
HTTP statuses: 502 502 502 502 502 502 502 502 502 502
```

After the next scrape, Prometheus observed that the payment target was down:

```json
{"metric":{"job":"payments"},"value":[1789914204.242,"0"]}
```

The gateway counter subsequently contained ten failed payment requests:

```text
gateway_requests_total{method="POST",path="/reserve/{id}/pay",status="502"} 10.0
```

## First Detected Golden Signal

The first directly observed golden signal was Service Health: `up{job="payments"}` was `0` at the scrape whose value timestamp was `1789914204.242`. This was about 6 seconds after the recorded stop request. The following pay requests were already returning 502, but the Prometheus error-rate panel requires a scrape before it can show that change.

## SLI, SLO and Error Budget

Availability SLI is the proportion of gateway requests that do not return 5xx. The availability SLO is 99.5% over seven days. Latency SLI is the proportion of gateway requests faster than 500 ms, and the latency SLO is 95%.

```text
1000 requests/day × 7 days = 7000 requests/week
7000 × 0.5% = 35 allowed failed requests/week
```

The service may therefore return at most 35 failed requests in that seven-day example before exhausting the availability error budget.

## Recording Rules

The Prometheus rules API reported the following loaded recording rules, all with health `ok` and a 15-second group interval.

```text
gateway:sli_availability:ratio_rate5m
  sum(rate(gateway_requests_total{status!~"5.."}[5m])) / sum(rate(gateway_requests_total[5m]))
gateway:sli_latency_500ms:ratio_rate5m
  sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m])) / sum(rate(gateway_request_duration_seconds_count[5m]))
gateway:error_budget_burn_rate:ratio_rate5m
  (1 - gateway:sli_availability:ratio_rate5m) / (1 - 0.995)
```

The numerator and denominator are each aggregated before division, so they have compatible label sets.

## SLO Gauge Observation

During the short outage observation, the recorded availability value was still `1` and the burn rate was `0`. This is a real limitation of the sampled five-minute rule result at that instant, not an assertion that the ten 502 responses were successful. The SLO gauge therefore did not visibly change in the captured API result even though the gateway counter and request responses showed failures.

### Controlled 60+ second outage evidence

I repeated the test with payments stopped at `2026-09-20T17:52:30.7063841+03:00` and restarted it at `2026-09-20T17:55:08.1513465+03:00`. The outage therefore lasted about 157 seconds. During it, four batches of 20 real reserve-and-pay attempts were made; all 80 pay requests returned HTTP 502. Each later sampling command began after a 15-second wait, generated its batch, and then queried Prometheus. The service remained stopped for more than two scrape/evaluation cycles after failures had been sent.

| Timestamp | Failed pay requests in batch | Availability rule | Burn-rate rule | Gateway error rate |
|---|---:|---:|---:|---:|
| 2026-09-20T17:52:54.4254627+03:00 | 20 × 502 | 0.7177307570555129 | 56.453848588897365 | 29.815432879307206% |
| 2026-09-20T17:53:33.1959966+03:00 | 20 × 502 | 0.7019025391955993 | 59.619492160880085 | 30.20545285851825% |
| 2026-09-20T17:54:12.6149562+03:00 | 20 × 502 | 0.6103629809085892 | 77.92740381828209 | 33.22928860603305% |
| 2026-09-20T17:54:50.9370129+03:00 | 20 × 502 | 0.6773494168982447 | 64.53011662035101 | 33.43248305042335% |

The first captured recording-rule result, at `17:52:54.4254627+03:00`, was already below 1. This made the SLO Availability gauge fall below 100%, and the burn rate was greater than zero at `56.453848588897365`. The lowest captured availability was `0.6103629809085892`, and the highest captured burn rate was `77.92740381828209`.

Payments was then started again in its normal configuration. The post-recovery health checks were:

```text
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0

GET http://localhost:8082/health
{"status":"healthy","failure_rate":0.0,"latency_ms":0}

GET http://localhost:3080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

## Bonus Failure Timeline

For the separate bonus experiment, `payments` was recreated with `PAYMENT_FAILURE_RATE=0.5` and `PAYMENT_LATENCY_MS=1000`. Local control timestamps use `+03:00`; application log timestamps below are UTC.

| Timestamp | Source | Observation |
|---|---|---|
| 2026-09-20T17:24:50.7330804+03:00 | Docker Compose | Fault injection was applied while payments was recreated. |
| 2026-09-20 14:25:47,482 UTC | payments log | A 1000 ms delay was injected for reservation `5cf394e3-4cb8-420f-9cdc-257e59de87e7`. |
| 2026-09-20 14:25:48,483 UTC | payments log | The first shown injected payment failure for that reservation returned HTTP 500. |
| 2026-09-20 14:25:48,484 UTC | gateway log | Gateway received the correlated 500 from `/charge` and returned 500 to the caller. |
| 2026-09-20T17:25:04.4849775+03:00 | client batch | Eight outcomes were `500 500 500 200 200 500 500 200`. |
| Prometheus query at 1789914372.740 | Prometheus | Gateway error rate was 10.186195465492842%; p95 was 2.1592753291780435 s and p99 was 2.43186037251903 s. |
| recovery command after experiment | Docker Compose | Payments was recreated with failure rate 0.0 and latency 0. |

## Gateway and Payments Logs

The following timestamped lines correlate the same reservation ID across the injected failure path.

```text
payments | {"time":"2026-09-20 14:25:47,482","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for 5cf394e3-4cb8-420f-9cdc-257e59de87e7"}
payments | {"time":"2026-09-20 14:25:48,483","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 5cf394e3-4cb8-420f-9cdc-257e59de87e7"}
payments | INFO:     172.18.0.4:47082 - "POST /charge HTTP/1.1" 500 Internal Server Error
gateway  | {"time":"2026-09-20 14:25:48,484","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 500 Internal Server Error\""}
gateway  | INFO:     172.18.0.1:51337 - "POST /reserve/5cf394e3-4cb8-420f-9cdc-257e59de87e7/pay HTTP/1.1" 500 Internal Server Error
```

The normal failure experiment also produced gateway dependency errors while payments was stopped:

```text
gateway | {"time":"2026-09-20 14:23:03,907","level":"ERROR","service":"gateway","msg":"payment error: [Errno -2] Name or service not known"}
gateway | INFO:     172.18.0.1:51280 - "POST /reserve/00bf…/pay HTTP/1.1" 502 Bad Gateway
```

## Root Cause

The injected 1000 ms payment delay held gateway payment calls open longer, while the 50% injected failure rate made part of the checkout flow fail. The observed result was a 10.186195465492842% gateway error rate with p95 and p99 above two seconds; the log correlation identifies the payment service as the source of both effects.

## Recovery Verification

After the experiments, payments was recreated with the normal values. The direct health response, environment, and gateway health were:

```text
GET http://localhost:8082/health
{"status":"healthy","failure_rate":0.0,"latency_ms":0}

PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0

GET http://localhost:3080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

## Conclusions

Prometheus successfully scraped all application services and evaluated all three recording rules. The Grafana dashboard is provisioned from the committed JSON, so its full golden-signals layout survives a Grafana restart. The experiments showed immediate user-visible payment failures during an outage and higher checkout errors and latency during the injected payment degradation; payments was restored to its normal configuration afterwards.
