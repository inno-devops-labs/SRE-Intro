# Lab 3: Monitoring, Observability & SLOs

## Task 1 — Configure Monitoring & Build Dashboard (6 pts)

### 3.3 Prometheus Targets Output

| Service  | Status | Metrics Endpoint               |
| -------- | ------ | ------------------------------ |
| gateway  | up     | `http://gateway:8080/metrics`  |
| events   | up     | `http://events:8081/metrics`   |
| payments | up     | `http://payments:8082/metrics` |

---

### 3.4 Custom Metrics List

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
payments_request_duration_seconds_bucket
payments_request_duration_seconds_count
payments_request_duration_seconds_created
payments_request_duration_seconds_sum
payments_requests_created
payments_requests_total
```

---

### PromQL Query Output (Request Rate)

```text
Request rate: 2.00 req/s
```

---

### PromQL Queries for Latency Panel

#### p99

```promql
histogram_quantile(
  0.99,
  sum(
    rate(gateway_request_duration_seconds_bucket{path!="/health"}[1m])
  ) by (le)
)
```

#### p95

```promql
histogram_quantile(
  0.95,
  sum(
    rate(gateway_request_duration_seconds_bucket{path!="/health"}[1m])
  ) by (le)
)
```

#### p50

```promql
histogram_quantile(
  0.50,
  sum(
    rate(gateway_request_duration_seconds_bucket{path!="/health"}[1m])
  ) by (le)
)
```

---

### PromQL Query for Saturation Panel

```promql
events_db_pool_size
```

---

### Dashboard Observations (Normal Traffic vs Payments Failure)

#### Normal Traffic

* Request rate: ~2 req/s
* Error rate: 0%
* Latency p99: ~25–50 ms
* DB pool size: 2–3 connections

#### After Killing Payments

* Error rate increased to ~33% (payment requests started failing)
* Request rate remained unchanged (~2 req/s)
* Latency showed spikes on p99 due to timeout errors
* DB pool size remained unchanged

---

### Which Golden Signal Showed the Failure First?

The **Error Rate** signal detected the failure first, approximately **15–30 seconds** after the `payments` service was stopped.

This corresponds to the Prometheus configuration:

```yaml
scrape_interval: 15s
```

The first scrape after the container stopped captured the increase in failed requests and reflected it in the error-rate metric.

---

# Task 2 — Define SLOs & Recording Rules (4 pts)

## 3.8 SLIs and SLOs

### SLI 1 — Availability

**Definition:** Percentage of gateway requests returning non-5xx responses.

**Formula:**

```text
(total requests - 5xx errors) / total requests × 100
```

**SLO Target:**

* 99.5% availability
* Measured over a 7-day rolling window

---

### SLI 2 — Latency

**Definition:** Percentage of gateway requests completed within 500 ms.

**Formula:**

```text
requests with duration ≤ 500ms / total requests × 100
```

**SLO Target:**

* 95% of requests under 500 ms

---

### Error Budget Calculation

For a **99.5% Availability SLO**:

```text
Error Budget = 100% − 99.5% = 0.5%
```

Assuming approximately:

```text
1000 requests/day
```

Over 7 days:

```text
1000 × 7 = 7000 requests/week
```

Maximum allowed failures:

```text
7000 × 0.005 = 35 failures/week
```

**Result:** The service can experience up to **35 failed requests per week** while still meeting the SLO.

---

## 3.9 Recording Rules

### Rules Loaded Output

```text
gateway:sli_availability:ratio_rate5m
gateway:sli_latency_500ms:ratio_rate5m
gateway:error_budget_burn_rate:ratio_rate5m
```

**Rules Health Status:** `ok`

---

### rules.yml

```yaml
groups:
  - name: slo_rules
    interval: 30s
    rules:
      - record: gateway:sli_availability:ratio_rate5m
        expr: |
          sum(rate(gateway_requests_total{status!~"5.*"}[5m]))
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

---

## 3.10 SLO Gauge Observation

During normal traffic:

* Availability remained at **100%**
* Error budget burn rate stayed near **0**

After stopping the `payments` service:

* Availability dropped below the **99.5% SLO target**
* Error budget burn rate increased significantly
* The SLO gauge clearly reflected the violation

This demonstrates how SLO monitoring can quickly reveal reliability issues and quantify their impact through error-budget consumption.
