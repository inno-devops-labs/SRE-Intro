# Lab 6 — Alerting & Incident Response

# Task 1
## Your alert rule PromQL queries

#### Alert 1 - High Error Rate (critical)
```
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```
- **Condition:** IS ABOVE 5
- **Evaluation:** Every 1m, for 2m
- **Labels:** severity=critical
- **Annotations:**
  - Summary: Gateway error rate is {{ $value }}%
  - Description: Error rate exceeded 5% for 2 minutes. Check payments service health.
  
#### Alert 2 - SLO Burn Rate (warning)
```
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```
- **Condition:** IS ABOVE 6
- **Evaluation:** Every 1m, for 5m
- **Labels:** severity=warning

## Contact point type and evidence of notification received
Webhook
<img width="1855" height="1096" alt="evidence" src="https://github.com/user-attachments/assets/40516f51-e7c5-427f-ab79-713f904d77e7" />



# Runbook: QuickTicket High Error Rate

## Alert
- **Fires when:** Gateway 5xx error rate > 5% for 2 minutes
- **Dashboard:** QuickTicket — Golden Signals
- **Severity:** Critical

## Diagnosis

1. Check which service is failing:
   ```bash
   curl -s http://localhost:3080/health
   ```

2. Check payments service directly:
   ```bash
   curl -s http://localhost:8082/health
   ```

3. Check events service:
   ```bash
   curl -s http://localhost:8081/health
   ```

4. Check logs for errors:
   ```bash
   docker compose logs gateway --tail=20
   docker compose logs payments --tail=20
   ```

5. Check metrics for 5xx errors:
   ```bash
   curl -s http://localhost:3080/metrics | grep -E 'gateway_requests_total.*status="5.."'
   ```

## Common Causes

| Cause | How to identify | Fix |
|-------|----------------|-----|
| Payments service down | health shows payments: down | Restart: `docker compose start payments` |
| Payments high failure rate | health OK but errors in logs | Check PAYMENT_FAILURE_RATE env var |
| Events service down | health shows events: down | Restart: `docker compose start events` |
| Database connection exhausted | events logs show pool errors | Restart events, check DB_MAX_CONNS |

## Escalation
- If not resolved in 10 minutes, escalate to instructor/TA

### Failure injection and response

**Failure injection:**

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_FAILURE_RATE=0.5 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

**Generating error requests:**

```bash
EVENT_ID=3
for i in {1..30}; do
  RES=$(curl -s -X POST "http://localhost:3080/events/$EVENT_ID/reserve" -H "Content-Type: application/json" -d '{"quantity":1}')
  RES_ID=$(echo "$RES" | grep -o '"reservation_id":"[^"]*"' | cut -d'"' -f4)
  if [ -n "$RES_ID" ]; then
    PAY=$(curl -s -X POST "http://localhost:3080/reserve/$RES_ID/pay")
    echo "$i: $PAY"
  else
    echo "$i: Reservation failed"
  fi
done
```

**Errors confirmed:**

```bash
$ curl -s http://localhost:3080/metrics | grep -E 'gateway_requests_total.*status="5.."'
gateway_requests_total{method="POST",path="/reserve/{id}/pay",status="500"} 15.0
```

**Diagnosis:**

```bash
$ curl -s http://localhost:3080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

Health check showed all services responsive, but metrics confirmed 15 payment errors with status 500.

**Fix applied:**

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_FAILURE_RATE=0.0 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

### Incident timeline

| Time | Event |
|------|-------|
| 17:07 | Failure injected: `PAYMENT_FAILURE_RATE=0.5` set on payments service |
| 17:08 | Payment requests start failing with HTTP 500 errors |
| 17:09 | Notified via webhook  |
| 17:10 | Alert fired: `QuickTicket High Error Rate` (error rate > 5%) |
| 17:11 | Investigation started |
| 17:13 | Root cause identified: payments service returning 500 errors (15 errors observed) |
| 17:16 | Fix applied: `PAYMENT_FAILURE_RATE=0.0` and payments restarted |
| 17:17 | Alert resolved (status changed to Normal) |

### Answer: How long from failure injection to alert firing? Why the delay?

The alert fired approximately **3 minutes** after failure injection (17:07 → 17:10).

**Why the delay:**
- **Evaluation interval:** 1 minute (Prometheus scrapes metrics every 15s, but alert evaluation runs every 1m)
- **Pending period:** 2 minutes (the condition must be true for 2 full minutes before the alert fires)
- **Total delay:** ~1m (evaluation) + 2m (pending) = ~3 minutes



## Alert firing evidence: Grafana alert rule status showing "Firing"

<img width="1906" height="1147" alt="status-failed" src="https://github.com/user-attachments/assets/83492b6e-b672-4510-826a-106be0d7415a" />
<img width="1906" height="1147" alt="firing notification" src="https://github.com/user-attachments/assets/d23a5982-cb6a-40a2-a342-9ecf64c36de0" />




## After fixing the problem

<img width="1915" height="1137" alt="resolved web hook" src="https://github.com/user-attachments/assets/1d87a618-9d90-4e02-942c-b62249ec42c7" />
<img width="1896" height="1132" alt="resolvedGrafana" src="https://github.com/user-attachments/assets/7f7a4f76-0f27-49b1-830f-e1ee3f59a0cf" />

# Task 2 

# Postmortem: Payment Service High Error Rate Incident

**Date:** 2026-06-25
**Duration:** 17:07 — 17:17 (10 minutes)
**Severity:** SEV-3
**Author:** Fidan Akhmedova

## Summary

On June 25, 2026, the payments service was configured with a 50% failure rate, causing 15 payment requests to fail with HTTP 500 errors. The `QuickTicket High Error Rate` alert fired within 3 minutes, and the incident was resolved in 10 minutes by restoring the normal failure rate configuration.

## Timeline

| Time | Event |
|------|-------|
| 17:07 | Failure injected: `PAYMENT_FAILURE_RATE=0.5` set on payments service |
| 17:08 | Payment requests start failing with HTTP 500 errors |
| 17:09 | Notified via webhook |
| 17:10 | Alert fired: `QuickTicket High Error Rate` (error rate > 5%) |
| 17:11 | Investigation started |
| 17:13 | Root cause identified: payments service returning 500 errors (15 errors observed) |
| 17:16 | Fix applied: `PAYMENT_FAILURE_RATE=0.0` and payments restarted |
| 17:17 | Alert resolved (status changed to Normal) |

## Root Cause

The payments service failure rate was increased from 0% to 50%, causing the gateway to return HTTP 500 errors for payment requests. This resulted in an error rate spike that burned through the SLO error budget. The root cause was a configuration change to the `PAYMENT_FAILURE_RATE` environment variable.

## What Went Well

- Alert fired within 3 minutes of failure injection
- Runbook provided clear diagnosis steps
- Health check endpoint quickly showed all services were responsive
- Metrics clearly identified the failing endpoint and error code
- Fix was straightforward and applied quickly

## What Went Wrong

- Alert query initially showed "No data" due to insufficient metrics
- Required manual traffic generation to accumulate enough error data
- Runbook didn't specify how to check for injected failure rates

## Action Items

| Action | Owner | Priority |
|--------|-------|----------|
| Add alert for payment latency spike | Fidan | High |
| Update runbook with env var check for PAYMENT_FAILURE_RATE | Fidan | Medium |
| Add automated test to verify alert rules fire correctly | Fidan | Medium |
| Document error budget burn rate calculation for SLO alerts | Fidan | Low |

### Answer: What is the most important action item from your postmortem? Why?

The most important action item is **adding an alert for payment latency spike**.

**Why:** The current alert only tracks error rate. However, latency spikes can also impact user experience even if errors stay low. Adding a latency alert ensures we detect performance degradation before users are impacted.

