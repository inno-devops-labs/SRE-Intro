# Lab 3 - Monitoring, Observability & SLOs

## Task 1 - Configure Monitoring and Build Dashboard

### Prometheus configuration

I configured Prometheus to scrape all three QuickTicket application services using Docker Compose service names and internal ports.

```yaml
scrape_configs:
  - job_name: gateway
    static_configs:
      - targets:
          - "gateway:8080"

  - job_name: events
    static_configs:
      - targets:
          - "events:8081"

  - job_name: payments
    static_configs:
      - targets:
          - "payments:8082"
```

### Monitoring stack

After starting the application and monitoring compose files together, all seven services were running:

```text
NAME               SERVICE      STATUS
app-events-1       events       Up
app-gateway-1      gateway      Up
app-grafana-1      grafana      Up
app-payments-1     payments     Up
app-postgres-1     postgres     Up (healthy)
app-prometheus-1   prometheus   Up
app-redis-1        redis        Up (healthy)
```

Prometheus reported all three application targets as healthy:

```text
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

### Custom metrics

The QuickTicket metrics visible in Prometheus included:

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

I generated traffic with the provided load generator. The first run completed with 78 successful requests and no failures. The Prometheus query returned:

```text
Request rate: 0.52 req/s
```

### Golden signals dashboard

I replaced the two placeholder panels in the provided dashboard.

For latency I used a Time series panel with:

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```

During normal traffic I measured:

```text
p50: 0.010000 s
p95: 0.023733 s
p99: 0.024953 s
```

For saturation I used:

```promql
events_db_pool_size
```

The gauge range is 0 to 10, with yellow at 7 and red at 9. During the normal measurement the pool size was 0/10.

### Payments failure observation

I repeated the outage experiment with forced reserve -> pay requests so every test request exercised the failing dependency.

I stopped payments at:

```text
2026-09-21T03:09:05-04:00
```

Prometheus showed:

```text
T+1s   payments_up=0
T+17s  status=503 total=5
T+33s  gateway 5xx rate=39.64%
T+59s  gateway 5xx rate=50.50%
T+64s  gateway 5xx rate=51.35%
```

The first client-side 503 was:

```text
2026-09-21T03:09:08-04:00 pay_status=503
```

The raw gateway metric later showed:

```text
gateway_requests_total{method="POST",path="/reserve/{id}/pay",status="503"} 27.0
```

After starting payments again, the gateway health endpoint returned healthy.

### Which golden signal showed the failure first?

The Service Health signal showed the failure first. `up{job="payments"}` became `0` on the first observation, about one second after I stopped the payments container.

The first user-visible 503 followed about three seconds after the injection. The Error Rate reacted later because the 5xx counter had to be scraped and there had to be enough samples for the `rate()` calculation. In my run the calculated 5xx percentage became visible at about T+33 seconds.

---

## Task 2 - Define SLOs and Recording Rules

### SLI and SLO definitions

Availability SLI:

```text
Percentage of gateway requests returning non-5xx
SLO target: 99.5% over 7 days
```

Latency SLI:

```text
Percentage of gateway requests completing in under 500 ms
SLO target: 95%
```

With approximately 1000 requests per day:

```text
1000 * 7 = 7000 requests/week
0.5% * 7000 = 35 failed requests/week
```

So the availability error budget is 35 failed requests per week.

For the latency objective, 5% of 7000 requests may exceed 500 ms:

```text
350 requests/week
```

### Recording rules

I created these rules:

```text
gateway:sli_availability:ratio_rate5m
gateway:sli_latency_500ms:ratio_rate5m
gateway:error_budget_burn_rate:ratio_rate5m
```

Prometheus returned the group:

```text
GROUP: slo_rules
gateway:sli_availability:ratio_rate5m       health=unknown
gateway:sli_latency_500ms:ratio_rate5m      health=unknown
gateway:error_budget_burn_rate:ratio_rate5m health=unknown
```

The rules were loaded and evaluated. Before the controlled outage, direct queries returned:

```text
availability = 1
latency under 500 ms = 0.9944444444444444
burn rate = 0
```

### SLO gauge

I added an `Availability SLO` Gauge panel using:

```promql
gateway:sli_availability:ratio_rate5m * 100
```

The panel is configured with min 99, max 100, and the SLO threshold at 99.5. I verified through the Grafana API that the panel exists as a gauge with the correct query.

During the payments outage:

```text
T+1s
Availability: 100.000%
Latency under 500ms: 99.444%
Burn rate: 0.00x

T+12s
Availability: 99.666%
Latency under 500ms: 99.331%
Burn rate: 0.67x

T+43s
Availability: 98.608%
Latency under 500ms: 98.376%
Burn rate: 2.78x

T+73s
Availability: 98.454%
Latency under 500ms: 98.282%
Burn rate: 3.09x
```

The SLO dropped below the 99.5% target and the burn rate exceeded 1.

At the end of that experiment:

```text
availability = 0.9846153846153846
latency under 500 ms = 0.9832167832167833
burn rate = 3.0769230769230855
```

A later 20-minute historical query after all experiments returned a minimum availability of 54.167%. That value includes all aggressive failure tests from the same session, so I used the controlled experiment timeline above for the SLO observation.

---

## Bonus Task - Correlate Failure Across Metrics and Logs

I cleared previous test orders and reservations, returned payments to a clean configuration, and started steady traffic.

Baseline:

```text
Gateway 5xx %: 0
Gateway p95: 0.024676794050162196 s
```

I injected:

```text
PAYMENT_FAILURE_RATE=0.5
PAYMENT_LATENCY_MS=1000
```

at:

```text
2026-09-21T03:23:51-04:00
```

The payments health endpoint confirmed:

```json
{
  "status": "healthy",
  "failure_rate": 0.5,
  "latency_ms": 1000
}
```

### Metric timeline

```text
T+7s
Gateway 5xx: 0.000%
Gateway p95: 0.0246s
Payments p95: 0.0047s

T+18s
Gateway 5xx: 0.000%
Gateway p95: 0.0248s
Payments p95: 2.3312s

T+28s
Gateway 5xx: 1.448%
Gateway p95: 1.0094s
Payments p95: 2.3312s

T+38s
Gateway 5xx: 2.091%
Gateway p95: 1.0094s
Payments p95: 2.3781s
Injected payment failures: 0.0935/s

T+59s
Gateway 5xx: 5.167%
Gateway p95: 1.7154s
Payments p95: 2.4250s

T+70s
Gateway 5xx: 5.882%
Gateway p95: 1.7154s
Payments p95: 2.4194s
```

The payments target stayed `up=1` because the service process remained reachable. The failure was in request behavior rather than service reachability.

### Log correlation

The first injected latency line was:

```text
2026-09-21T07:23:55.367490752Z payments:
Injecting 1000ms latency for b14c6edd-1cf3-4ce8-9aae-7c9fe7317443
```

The first injected payment failure was:

```text
2026-09-21T07:24:02.738402751Z payments:
Payment failed (injected) for e288b6fc-4929-49be-a44e-8e0833708604
```

The matching gateway line was:

```text
2026-09-21T07:24:02.742833651Z gateway:
POST /reserve/e288b6fc-4929-49be-a44e-8e0833708604/pay HTTP/1.1 500 Internal Server Error
```

### Incident timeline

```text
03:23:51 - fault configuration applied
03:23:55 - first 1000 ms latency injection logged
03:24:02 - first injected payment failure logged
03:24:02 - matching gateway request returned HTTP 500
~T+18s   - payments p95 increased to about 2.33 s
~T+28s   - gateway p95 exceeded 1 s and the 5xx metric became visible
03:26:04 - normal payments configuration restored
```

After recovery the payments service returned to `failure_rate=0.0` and `latency_ms=0`, and gateway health again showed events and payments as OK.

The load generator completed with:

```text
total=472 success=452 fail=20 error_rate=4.2%
```

The final rolling-window queries still included incident samples:

```text
Gateway 5xx %: 1.5873
Burn rate: 5.6296
```

### Root cause

The metrics and logs point to the payments service as the root cause. The process did not crash, so Service Health stayed up. Each charge request was intentionally delayed by 1000 ms and approximately half of the charge attempts were configured to fail.

The latency increase appeared before the gateway error-rate spike. Payment request p95 increased first, then gateway latency rose while it waited for payments, and injected payment failures propagated through the gateway as HTTP 500 responses.

This shows why service reachability by itself is not sufficient. A dependency may still answer health checks while producing slow or failed business requests.

---

## Summary

I configured Prometheus scraping for all QuickTicket services and deployed Prometheus and Grafana with the application stack. I completed the golden-signals dashboard with latency and DB saturation panels and added an availability SLO gauge.

I defined availability and latency SLIs, implemented recording rules, calculated the error budget, and observed error-budget burn during an outage.

Finally, I correlated a simulated payments incident across Prometheus metrics, gateway logs and payments logs.
