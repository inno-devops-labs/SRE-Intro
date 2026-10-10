## Task 1 Configure Monitoring & Build Dashboard (6 pts)

1. Output of compose ps showing all 7 services
```bash
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro/app$ docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
NAME               IMAGE                     COMMAND                  SERVICE      CREATED       STATUS                 PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       5 hours ago   Up 5 hours             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      5 hours ago   Up 5 hours             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      5 hours ago   Up 5 hours             0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     5 hours ago   Up 5 hours             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     5 hours ago   Up 5 hours (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   5 hours ago   Up 5 hours             0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        5 hours ago   Up 5 hours (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```
2. Prometheus targets output (all 3 `up`)

```bash
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro/app$ curl -s http://localhost:9090/api/v1/targets | python3 -c "
import sys, json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(f\"{t['labels']['job']:12} {t['health']:8} {t['scrapeUrl']}\")
"
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

3. Custom metrics list

```bash
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro/app$ curl -s http://localhost:9090/api/v1/label/__name__/values | python3 -c "
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
4. PromQL query output (request rate)

```bash
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro/app$ curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))' \
  http://localhost:9090/api/v1/query | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(f\"Request rate: {float(r['data']['result'][0]['value'][1]):.2f} req/s\")"
Request rate: 0.30 req/s
```
5. PromQL queries you used for Latency and Saturation panels

Latency panel:

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le)) 
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```
Saturation panel:


```promql
events_db_pool_size
```

6. Dashboard observations: normal traffic vs payments failure

* Normal state: error rate remained at 0%, p99 latency was around 23 ms,
availability stayed at 100%, and burn rate was 0.
* During payments outage: the gateway was unable to reach
the payments service, so all requests in the payment 
flow (~10% of total traffic) failed, causing 
the error rate to rise to about 10%. Importantly, 
p99 latency did not increase because failures occurred 
immediately (e.g., connection or DNS errors) rather than causing timeouts or slow responses. The DB pool metric remained unchanged at 0, since the events service was not affected. The 5-minute SLI-based metrics (availability and burn rate) responded with a delay, starting to reflect the degradation roughly 90 seconds after the failure began.




7. Answer: "Which golden signal showed the failure first? How long after killing payments?"

Errors (error rate) were the first signal to change. The 
payments service was stopped at 20:51:13, and the first 
non-zero errors appeared in the rate[1m] metric around 
20:52:15. This delay is 
explained by the 1-minute rate window, the 15-second scrape 
interval, and the fact that only about 10% of traffic goes
through the payment path.

Latency and saturation did not show a meaningful 
reaction because they are either decoupled 
from this failure mode or handled by other services
(fail-fast behavior and separate components).

The SLO/availability metric reacted the slowest,
since it is calculated over a rolling 5-minute window, 
which smooths short-term changes and introduces additional
lag in detecting the impact.
## Task 2 — Define SLOs & Recording Rules 

SLI 1 — Availability: % of gateway requests returning non-5xx
SLO target: 99.5% over a 7-day window

SLI 2 — Latency: % of gateway requests completing under 500ms
SLO target: 95%

Error budget math (assuming ~1000 requests/day):


Weekly volume: 1000 × 7 = 7,000 requests/week
Availability budget: (1 − 0.995) × 7,000 = 0.005 × 7,000 = 35 failed requests/week allowed
Latency budget: (1 − 0.95) × 7,000 = 0.05 × 7,000 = 350 "slow" (≥500ms) requests/week allowed

`rules.yml`:
```
groups:
  - name: slo_rules
    interval: 30s
    rules:
      # SLI 1 — Availability: share of gateway requests that are NOT 5xx.
      - record: gateway:sli_availability:ratio_rate5m
        expr: |
          sum(rate(gateway_requests_total{status!~"5.."}[5m]))
          /
          sum(rate(gateway_requests_total[5m]))

      # SLI 2 — Latency: share of gateway requests completing under 500ms.
      # le="0.5" bucket counts requests faster than 0.5s; _count is the total.
      - record: gateway:sli_latency_500ms:ratio_rate5m
        expr: |
          sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m]))
          /
          sum(rate(gateway_request_duration_seconds_count[5m]))

      # Error budget burn rate against the 99.5% availability SLO.
      # burn_rate = (1 - availability) / (1 - 0.995); >1 means burning budget
      # faster than the SLO allows.
      - record: gateway:error_budget_burn_rate:ratio_rate5m
        expr: |
          (1 - gateway:sli_availability:ratio_rate5m) / (1 - 0.995)
```

```bash
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro/app$ curl -s http://localhost:9090/api/v1/rules | python3 -c "
import sys, json
for g in json.load(sys.stdin)['data']['groups']:
    for r in g['rules']:
        print(f\"{r['name']:45} = {r.get('health', 'N/A')}\")
"
gateway:sli_availability:ratio_rate5m         = ok
gateway:sli_latency_500ms:ratio_rate5m        = ok
gateway:error_budget_burn_rate:ratio_rate5m   = ok
```

During the experiment, the payments service was stopped for 60 seconds while 
traffic was generated. After approximately one to two scrape intervals, the availability 
gauge dropped below the 99.5% SLO threshold. Once the service was restarted, the gauge gradually 
recovered toward 100%.

The recovery was not immediate because the availability SLI 
is calculated over a rolling 5-minute window (rate(...[5m])), 
so failed requests continued to affect the metric for several minutes
after the service became healthy again.




## Bonus Task — Correlate Failure Across Metrics & Logs

### Setup

```bash
./loadgen/run.sh 5 120 &
````

After ~30s, fault injection was applied by restarting `payments` with:

```bash
PAYMENT_FAILURE_RATE=0.5 PAYMENT_LATENCY_MS=1000
```

The system was observed in Grafana for ~2 minutes while collecting logs via `docker compose logs`.

---

## Timeline (metrics → logs correlation)

```text
T+0s        loadgen started (5 RPS)

T+30s       fault injection applied:
            payments restarted with:
            PAYMENT_FAILURE_RATE=0.5
            PAYMENT_LATENCY_MS=1000

T+~30–40s   first payments logs show injected behaviour:
            → "Injecting 1000ms latency for <id>"
            → "Payment failed (injected) for <id>"

T+~40–60s   first gateway errors appear:
            → HTTP 500 / failed /pay requests
            → error rate starts rising in Prometheus

T+~60–90s   Grafana spike visible:
            → p99 latency jumps to ~2s (1s injected + overhead)
            → availability drops below SLO (99.5%)

T+~90–120s  system stabilizes under steady fault load:
            → sustained elevated error + latency
```

---

## Log excerpts

### Payments service (root cause source)

```text
payments-1 | 20:12:09 {"level":"INFO","msg":"Injecting 1000ms latency for a9039354"}
payments-1 | 20:12:10 {"level":"WARNING","msg":"Payment failed (injected) for a9039354"}
payments-1 | 20:12:11 {"level":"INFO","msg":"Injecting 1000ms latency for b12a88d1"}
payments-1 | 20:12:12 {"level":"WARNING","msg":"Payment failed (injected) for b12a88d1"}
payments-1 | 20:12:13 {"level":"INFO","msg":"Payment success: PAY-XXXX for c91d02aa"}
```

---

### Gateway service (propagation point)

```text
gateway-1 | POST /reserve/8a12.../pay HTTP/1.1" 500 Internal Server Error
gateway-1 | POST http://payments:8082/charge "HTTP/1.1 500"
gateway-1 | POST /reserve/91bc.../pay HTTP/1.1" 500 Internal Server Error
```

---

## Metrics during failure

```text
SLO Availability (5m):     ~98.7%   below 99.5% threshold
Error Rate (5xx):          ~1–5%    spike after injection
P99 Latency:               ~2.0s    from ~20–30ms baseline
Error Budget Burn Rate:     >1.0    consuming budget too fast
```

---

## Correlation table

| Stage            | Signal                                     | Source             |
| ---------------- | ------------------------------------------ | ------------------ |
| Injection starts | payments logs: latency + failure injection | payments container |
| First impact     | HTTP 500 errors appear                     | gateway logs       |
| Metric spike     | error rate + p99 latency rise              | Prometheus/Grafana |
| SLO breach       | availability < 99.5%                       | 5m rolling SLI     |
| Recovery         | metrics stabilize after restart/window     | Grafana            |

---

## Root cause

The failure was introduced in the `payments` service via:

* `PAYMENT_FAILURE_RATE=0.5` → 50% of payment requests fail
* `PAYMENT_LATENCY_MS=1000` → each request is artificially delayed by 1 second

This created a combined degradation pattern:

* The gateway received HTTP 500 responses from `/payments/charge`
* The added latency caused p99 latency to spike to ~2 seconds
* The SLO metric is based on a 5-minute rolling window, so it reacted with delay
* The system remained partially functional, so degradation was gradual rather than immediate

---

## Key insight

* Logs captured the exact injection moment first
* Gateway logs showed propagation of failures
* Metrics lagged due to 1m and 5m rate windows
* Latency increase came from intentional synchronous delay injection, not infrastructure saturation

