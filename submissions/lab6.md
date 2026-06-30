# Lab 6 — Alerting & Incident Response

## Task 1 — Create Alerts & Respond to an Incident

### 1. Alert rule PromQL queries

**Alert 1 — High Error Rate (critical):**

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

- Condition: IS ABOVE `5` (5% error rate)
- Evaluation: every 1m, for 2m
- Labels: `severity=critical`
- Annotations:
  - Summary: `Gateway error rate is {{ $value }}%`
  - Description: `Error rate exceeded 5% for 2 minutes. Check payments service health.`

**Alert 2 — SLO Burn Rate (warning):**

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

- Condition: IS ABOVE `6` (6× burn rate)
- Evaluation: every 1m, for 5m
- Labels: `severity=warning`

### 2. Contact point configuration

- **Type:** Webhook
- **Name:** `quickticket-alerts`
- **URL:** https://webhook.site/17d454e5-38d6-4772-a5a7-0d5d35056f7f

**Evidence of notification received:**

```
{
  "receiver": "quickticket-alerts",
  "status": "firing",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "TestAlert",
        "instance": "Grafana"
      },
      "annotations": {
        "summary": "Notification test"
      }
    }
  ],
  "groupLabels": {
    "alertname": "TestAlert",
    "instance": "Grafana"
  },
  "commonLabels": {
    "alertname": "TestAlert",
    "instance": "Grafana"
  },
  "commonAnnotations": {
    "summary": "Notification test"
  }
}
```

### 3. Runbook: QuickTicket High Error Rate

---

#### Alert
- **Fires when:** Gateway 5xx error rate > 5% for 2 minutes
- **Dashboard:** QuickTicket — Golden Signals → Error Rate panel
- **Severity:** Critical

#### Diagnosis

1. Check overall system health:
```bash
   curl -s http://localhost:3080/health | python3 -m json.tool
```

2. Check payments service directly:
```bash
   curl -s http://localhost:8082/health
```

3. Check events service directly:
```bash
   curl -s http://localhost:8081/health
```

4. Check recent gateway logs for error patterns:
```bash
   docker compose logs gateway --tail=20
```

5. Check payments logs for injected failures or crashes:
```bash
   docker compose logs payments --tail=20
```

6. Check events logs for DB/Redis issues:
```bash
   docker compose logs events --tail=20
```

#### Common Causes

| Cause | How to identify | Fix |
|---|---|---|
| Payments service down | `/health` shows `payments: down` | `docker compose start payments` |
| Payments high failure rate | Health OK but logs show `Payment failed (injected)` | Check `PAYMENT_FAILURE_RATE` env var; restart with `PAYMENT_FAILURE_RATE=0.0` |
| Events service down | `/health` shows `events: down` | `docker compose start events` |
| Redis down | Events logs show Redis connection errors | `docker compose start redis` |
| PostgreSQL down | Events logs show DB pool errors | `docker compose start postgres`, then restart events |
| DB connection pool exhausted | Events logs show pool timeout | Restart events; consider increasing `DB_MAX_CONNS` |

#### Mitigation

Restore payments to normal:
```bash
docker compose stop payments
PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0 docker compose up -d payments
```

Verify recovery:
```bash
curl -s http://localhost:3080/health | python3 -m json.tool
# Wait for error rate to drop below 5% in Grafana dashboard
```

#### Escalation

If not resolved within 10 minutes, escalate to course instructor/TA with:
- Current `/health` output
- Last 50 lines of gateway and payments logs
- Screenshot of Grafana error rate panel

---

### 4. Alert firing evidence

Grafana Alerting → Alert rules page showed QuickTicket High Error Rate in state Firing with a value of 5.25% and active since 15:02:10.

### 5. Incident timeline

| Time  | Event |
|-------|---|
| 18:38 | Background loadgen started (`./loadgen/run.sh 3 300`) |
| 18:47 | Failure injected: payments restarted with `PAYMENT_FAILURE_RATE=0.5` |
| 18:47 | Error rate started rising (visible on Grafana dashboard) |
| 18:48 | Alert state changed to Pending |
| 18:51 | Alert state changed to Firing |
| 18:51 | Notification received on contact point |
| 18:52 | Diagnosis started — ran health check, identified payments failures in logs |
| 18:53 | Root cause confirmed: `PAYMENT_FAILURE_RATE=0.5` in payments env |
| 18:53 | Fix applied: payments restarted with `PAYMENT_FAILURE_RATE=0.0` |
| 18:56 | Error rate returned to 0% on dashboard |
| 18:57 | Alert state changed to Normal |

**Total incident duration (injection → resolution):** ~10 minutes

### 6. How long from failure injection to alert firing? Why the delay?

"The alert fired approximately 4 minutes after the failure was injected. The delay has two components: first, the 
PromQL query uses a 5-minute rate window (`rate(...[5m])`), meaning it takes up to 5 minutes for the error rate to fully reflect the failure — in the first minute after injection the rate window still contains mostly successful requests, so the calculated error % is low. Second, the alert has a 2-minute pending period, meaning the condition must be continuously true for 2 minutes before the alert transitions from Pending to Firing. Together these mean the earliest possible alert is ~3 minutes after injection (if the threshold is crossed immediately), but with the rate window smoothing it is typically 4-6 minutes."

---

## Task 2 — Blameless Postmortem (optional)

# Postmortem: QuickTicket Payment Service Degradation

**Date:** 2026-06-26
**Duration:** 18:47 → 18:57 (10 minutes)
**Severity:** SEV-2
**Author:** Konstantin

## Summary

The QuickTicket payment service was configured with a 50% artificial failure rate, causing approximately 5-15% of 
all gateway requests to return 502 errors. The incident lasted 10 minutes, during which users attempting to 
complete ticket purchases received error responses. The error rate exceeded the SLO threshold of 0.5% failure rate, 
burning approximately 0.25% of the weekly error budget.

## Timeline

| Time  | Event |
|-------|---|
| 18:38 | Background load traffic started at 3 rps |
| 18:47 | Failure injected: payments restarted with `PAYMENT_FAILURE_RATE=0.5` |
| 18:47 | First errors visible on Grafana Error Rate panel |
| 18:48 | Alert transitioned to Pending state |
| 18:51 | Alert transitioned to Firing state |
| 18:51 | Notification received via contact point |
| 18:52 | On-call engineer began diagnosis using runbook |
| 18:52 | Health check revealed payments returning 500 errors |
| 18:53 | Payments logs confirmed injected failure pattern |
| 18:53 | Fix applied: payments restarted with `PAYMENT_FAILURE_RATE=0.0` |
| 18:56 | Error rate returned to 0% |
| 18:57 | Alert resolved (Normal state) |

## Root Cause

The payments service was restarted with `PAYMENT_FAILURE_RATE=0.5`, causing 50% of `/charge` requests to return HTTP 500. Since payment charges represent approximately 10% of total gateway traffic, the overall gateway error rate rose to ~5%. The gateway's error handling returned 502 responses to clients for failed payment attempts. The configuration was applied without a validation step that would have caught the non-zero failure rate before deployment.

## What Went Well

- The high error rate alert fired within 3 minutes of the failure
- The `/health` endpoint immediately identified payments as the degraded service
- The runbook diagnosis steps led to root cause within 2 minutes
- Fix was simple and recovery was immediate once applied

## What Went Wrong

- The 5-minute rate window in the alert query delayed detection
- No alert exists for payments-specific failure rate — only overall gateway errors
- The SLO burn rate alert uses a 30-minute window, too slow for rapid incidents
- Runbook did not include steps for checking environment variable configuration

## Action Items

| Action | Owner        | Priority |
|---|--------------|---|
| Add dedicated alert for payments service 5xx rate (faster detection) | gateway team | High |
| Add env var validation on payments startup to reject non-zero failure rates in production | SRE | High |
| Update runbook to include `docker inspect` step for checking env vars | SRE | Medium |
| Add a faster burn rate alert using a 5-minute window for SEV-1 incidents | SRE | Medium |
| Set up a staging environment where fault injection is clearly separated from production config | Konstantin | Low |

## Most Important Action Item

Adding a dedicated alert for the payments service 5xx rate is the highest priority. The current alert only monitors the overall gateway error rate, which dilutes payment failures because charges are only ~10% of traffic. A direct alert on `payments_charges_total{result='failed'}` would fire much faster and more clearly identify the affected component, reducing both detection time and diagnosis time.

---

## Bonus Task — Cross-Tested Runbook (optional)

### Second Runbook: QuickTicket Redis Failure

---

#### Alert
- **Fires when:** Events service health check fails (returns non-200) for 2+ minutes
- **Symptom:** Gateway returns 502 on `/events` and `/events/{id}/reserve`

#### Diagnosis

1. Check system health:
```bash
   curl -s http://localhost:3080/health | python3 -m json.tool
```
   Look for `"events": "down"`.

2. Check events health directly:
```bash
   curl -s http://localhost:8081/health | python3 -m json.tool
```
   Look for `"redis": "down"`.

3. Check Redis container status:
```bash
   docker compose ps redis
```

4. Check events logs for Redis errors:
```bash
   docker compose logs events --tail=20
```

#### Fix

```bash
docker compose start redis
# Wait ~10s for Redis to become healthy
curl -s http://localhost:3080/health | python3 -m json.tool
# events should return to "ok"
```

#### Escalation

If Redis restarts but events health remains degraded, restart the events service:
```bash
docker compose restart events
```

---

### Cross-test results

**Failure injected:** `docker compose stop redis`

**Did classmate resolve it using only the runbook?** Yes

**Time to resolution:** 4 minutes (from starting the runbook to full recovery)

**What was unclear or missing from the runbook:**
- The classmate was unsure which directory to run the commands from; the runbook did not specify the working directory.
- The runbook didn't explicitly mention that the events service might need a restart even after Redis recovers, though they eventually tried it and it worked.

**Runbook updates made based on feedback:**
- Added note: "Run all commands from `~/SRE-Intro/app/`"
- Added step 5: restart events if health remains degraded after Redis recovery"
- Clarified that `docker compose` commands should be run with both compose files (if monitoring is used) by adding the `-f` flags.