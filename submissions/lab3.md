# Lab 3 Monitoring, Observability & SLOs

## Task 1  Monitoring Setup

**Prometheus Configuration** (`monitoring/prometheus/prometheus.yml`)  
I set scrape targets for gateway, events and payments.

**Monitoring Stack** is running now, 7 services.

**Prometheus Targets** are all **up**.

**Golden Signals Dashboard** in Grafana:

- I add **Latency** panel (p50, p95, p99)
- I add **Saturation** panel (DB pool gauge)

When I stop payments, I can see big increase in Error Rate and Service Health go down.

## Task 2 SLOs and Recording Rules

I created `monitoring/prometheus/rules.yml` with three recording rules:

- `gateway:sli_availability:ratio_rate5m`
- `gateway:sli_latency_500ms:ratio_rate5m`
- `gateway:error_budget_burn_rate:ratio_rate5m`

Rules are loaded in Prometheus successfully.

**SLI/SLO:**

- Availability SLO: **99.5%**
- Latency SLO (< 500ms): **95%**

## Bonus Task Failure Correlation

I run load, inject failure in payments and watch dashboard + logs.

**Conclusion:** Failure first show in **Error Rate**, then in **Service Health**. Latency increase later.

## Final

In Lab 3 I setup monitoring for QuickTicket with Prometheus + Grafana, make Golden Signals dashboard and define basic SLOs.


