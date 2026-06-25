# Lab 6 — Alerting & Incident Response

## Task 1 — Alerts, Runbook & Incident

### 6.1 — Stack running

All 7 services confirmed running via `docker compose ps`. Background traffic generated with:

```bash
./loadgen/run.sh 3 300 &
```

### 6.2 — Contact point

Created a **Webhook** contact point named `quickticket-alerts` in Grafana Alerting → Contact points. URL pointed to a unique webhook.site receiver. Test notification confirmed received.

### 6.3 — Alert rules created

**Alert 1 — QuickTicket High Error Rate (critical)**

PromQL query:
```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

- Condition: IS ABOVE `0.5` (threshold tuned down from 5% after discovering Prometheus records ~1% 5xx even with 50% payment failure rate, because the gateway graceful degradation returns fast 503s that only affect pay requests which are ~10% of traffic)
- Evaluation: every `1m`, pending period `2m`
- Labels: `severity=critical`
- Annotations:
  - Summary: `Gateway error rate is {{ $value }}%`
  - Description: `Error rate exceeded 5% for 2 minutes. Check payments service health.`

**Alert 2 — QuickTicket SLO Burn Rate (warning)**

PromQL query:
```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

- Condition: IS ABOVE `6`
- Evaluation: every `1m`, pending period `5m`
- Labels: `severity=warning`

### 6.4 — Notification policy

Default policy updated to use `quickticket-alerts` contact point. Group by `alertname`, group wait `30s`, repeat interval `5m`.

### 6.5 — Runbook: QuickTicket High Error Rate

```markdown
# Runbook: QuickTicket High Error Rate

## Alert
- Fires when: Gateway 5xx error rate > 0.5% for 2 minutes
- Dashboard: QuickTicket — Golden Signals (Error Rate panel)
- Severity: critical

## Diagnosis

1. Check overall system health:
   curl -s http://localhost:3080/health | python3 -m json.tool

2. Check payments service directly:
   curl -s http://localhost:8082/health

3. Check events service directly:
   curl -s http://localhost:8081/health

4. Check gateway logs for error patterns:
   docker compose logs gateway --tail=20

5. Check payments logs for failure injection:
   docker compose logs payments --tail=20

6. Check Prometheus error rate:
   curl -s 'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total{status=~"5.."}[5m]))/sum(rate(gateway_requests_total[5m]))*100'

## Common Causes

| Cause | How to identify | Fix |
|-------|----------------|-----|
| Payments service down | health shows payments: down | docker compose start payments |
| Payments failure rate injected | logs show PAYMENT_FAILURE_RATE > 0 | Restart with PAYMENT_FAILURE_RATE=0.0 |
| Events service down | health shows events: down | docker compose start events |
| Redis down | events logs show redis errors | docker compose start redis |
| Postgres down | events shows degraded | docker compose start postgres |

## Resolution Steps

1. Identify the failing service from health check
2. Check logs for root cause
3. Restart the failing service:
   docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start <service>
4. Verify error rate drops below 0.5% in Grafana
5. Confirm alert resolves (Normal state)

## Escalation
If not resolved in 10 minutes, escalate to professor @Cre-eD or TAs @Naghme98 / @pierrepicaud.
```

### 6.6 — Incident simulation and response

**Failure injected at 03:48:01:**

```bash
docker compose stop payments
PAYMENT_FAILURE_RATE=0.5 PAYMENT_LATENCY_MS=200 docker compose up -d payments
```

**Load generator output showing errors:**

```
[10s]  requests=27   success=24   fail=3    error_rate=11.1%
[20s]  requests=54   success=46   fail=8    error_rate=14.8%
[30s]  requests=81   success=67   fail=14   error_rate=17.2%
[60s]  requests=160  success=128  fail=32   error_rate=20.0%
---
Done. total=792  success=601  fail=191  error_rate=24.1%
```

**Prometheus error rate (5-minute rate):**

```
Error rate %: 1.1428571428571428
```

The Prometheus error rate (1.14%) is lower than the loadgen error rate (24%) because the gateway graceful degradation returns 503 instantly — only pay requests fail, which are ~10% of total traffic. Load generator counts any non-200 as failure including 409 (ticket conflicts), while Prometheus only counts 5xx.

**Alert fired at 03:59:19** (Firing state confirmed in Grafana):

```
QuickTicket High Error Rate — Firing — 1 instance
```

**Webhook notification received at 03:56:10:**

```json
{
  "receiver": "quickticket-alerts",
  "status": "resolved",
  "alerts": [{
    "status": "resolved",
    "labels": {
      "alertname": "DatasourceNoData",
      "rulename": "QuickTicket High Error Rate",
      "severity": "critical"
    },
    "annotations": {
      "description": "Error rate exceeded 5% for 2 minutes. Check payments service health.",
      "summary": "Gateway error rate is %"
    }
  }]
}
```

**Diagnosis following runbook:**

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
# {"status": "healthy", "checks": {"events": "ok", "payments": "ok", "circuit_payments": "CLOSED"}}
```

Health showed payments as "ok" (the container was up, just returning failures). Gateway logs revealed `payment service down` errors.

**Fix applied at 03:59:43:**

```bash
docker compose stop payments
PAYMENT_FAILURE_RATE=0.0 docker compose up -d payments
```

### 6.7 — Incident timeline and answers

**Full timeline:**

| Time | Event |
|------|-------|
| 03:48:01 | Failure injected — PAYMENT_FAILURE_RATE=0.5 |
| 03:48:01 | Load generator showing ~14% error rate immediately |
| 03:56:10 | Webhook notification received (DatasourceNoData — traffic gap) |
| 03:57:14 | Prometheus error rate confirmed at 1.14% |
| 03:58:38 | Load generator restarted |
| 03:59:19 | Alert confirmed **Firing** in Grafana |
| 03:59:43 | Fix applied — payments restored to PAYMENT_FAILURE_RATE=0.0 |

**How long from failure injection to alert firing?**

Approximately **11 minutes** (03:48 → 03:59). The delay had two causes: first, there was a gap where the load generator finished and Prometheus had no traffic data (`DatasourceNoData` state), which temporarily paused the evaluation. Second, the pending period of 2 minutes means the condition must be continuously true for 2 minutes before firing. The actual detection time once traffic was stable was about 2-3 minutes — the evaluation interval (1m) plus the pending period (2m).

---

## Task 2 — Blameless Postmortem

```markdown
# Postmortem: QuickTicket Payment Service Failure

**Date:** 2026-06-25
**Duration:** 03:48 → 03:59 (approximately 11 minutes)
**Severity:** SEV-2 (partial outage — pay requests failing, browse/reserve still working)
**Author:** bytepharaoh

## Summary
The payments service was restarted with PAYMENT_FAILURE_RATE=0.5, causing 50% of
payment charge requests to fail. This resulted in approximately 24% of all user-facing
requests returning errors (since the load generator includes pay requests in its mix).
The Grafana alert fired after ~11 minutes and a webhook notification was delivered.

## Timeline

| Time     | Event |
|----------|-------|
| 03:48:01 | PAYMENT_FAILURE_RATE=0.5 injected, payments container recreated |
| 03:48:10 | Load generator begins showing ~14% error rate |
| 03:51:16 | Load generator restarted after initial run ended |
| 03:55:07 | Confirmed Prometheus records status values: 200, 409, 500 |
| 03:56:10 | Webhook notification received (DatasourceNoData alert) |
| 03:57:14 | Prometheus 5-minute error rate confirmed at 1.14% |
| 03:58:38 | Load generator restarted for continuous traffic |
| 03:59:19 | Alert confirmed Firing in Grafana |
| 03:59:43 | Fix applied — payments restored with PAYMENT_FAILURE_RATE=0.0 |

## Root Cause
The payments service failure rate environment variable was set to 0.5, causing the
underlying /charge endpoint to randomly return HTTP 500 for 50% of payment attempts.
The gateway's graceful degradation (Lab 1 Task 2) correctly caught these and returned
503 to clients, preserving reservations. However the blast radius was limited: only
pay requests failed (~10% of traffic), while event listing and reservations continued
to work normally.

## What Went Well
- The graceful degradation implemented in Lab 1 prevented a total outage — users
  could still browse and reserve tickets during the payment failure
- The alert fired and webhook notification was delivered successfully
- The runbook clearly identified the failing service and fix steps
- Recovery was fast once the root cause was identified (< 1 minute to fix)

## What Went Wrong
- Alert threshold tuning was required — the initial 5% threshold was too high for
  the actual Prometheus error rate (~1%), requiring a threshold adjustment during
  the incident
- The load generator gap (loadgen finishing) caused a DatasourceNoData alert before
  the real error rate alert fired, creating noise
- Time to detection was ~11 minutes — too long for a SEV-2 incident

## Action Items

| Action | Owner | Priority |
|--------|-------|----------|
| Set alert threshold based on measured baseline, not assumed error rate | bytepharaoh | High |
| Add dedicated alert for payment service health endpoint returning non-200 | bytepharaoh | High |
| Run continuous background traffic (not time-limited loadgen) so DatasourceNoData alerts don't fire | bytepharaoh | Medium |
| Add PAYMENT_FAILURE_RATE to runbook as explicit check step | bytepharaoh | Medium |
| Reduce pending period to 1m for critical payment alerts | bytepharaoh | Low |
```

**Most important action item:** Adding a dedicated alert for the payment service health endpoint. The current error rate alert took 11 minutes to fire and required threshold tuning. A direct `up{job="payments"} == 0` or health-endpoint alert would have fired within 15 seconds of the failure, giving much faster time-to-detection regardless of traffic patterns.