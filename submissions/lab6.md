# Lab 6 — Alerting & Incident Response

## Task 1 — Create Alerts & Respond to an Incident

### 1. Alert PromQL rules
```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100 # Error rate

(1 - (sum(rate(gateway_requests_total{status!~\"5..\"}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995) # Burn rate 
```

### 2. Notification received evidence
https://images.iimg.live/images/incredible-portfolio-5026.webp

### 3. Runbook
```md
# Runbook: QuickTicket High Error Rate

## Alert

- **Fires when:** Gateway 5xx error rate > 5% for 2 minutes
- **Dashboard:** QuickTicket — Golden Signals

## Diagnosis

1. Check overall application health:

   curl -s http://localhost:3080/health | python3 -m json.tool

2. Check gateway logs:

   docker compose logs gateway --tail=50 --since=5m

3. Check payments service:

   curl -s http://localhost:8082/health

4. Check events service:

   curl -s http://localhost:8081/health

5. Check database logs:

   docker compose logs db --tail=50 --since=5m

6. Check other services logs:

   docker compose logs events --tail=50 --since=5m
   docker compose logs payments --tail=50 --since=5m

## Common Causes

| Cause | How to identify | Fix |
|-------|----------------|-----|
| Payments service down | `/health` reports payments as down | `docker compose restart payments` |
| Events service down | `/health` reports events as down | `docker compose restart events` |
| Database unavailable | Database logs contain connection errors | Restart the database and verify configuration |
| High payment failure rate | Payments health is OK, but logs contain many failed transactions | Check `PAYMENT_FAILURE_RATE` configuration |
| Application bug | Services are healthy, but logs contain exceptions | Investigate recent code changes and stack traces |

## Recovery

Restart a service:

docker compose restart <service>

Example:

docker compose restart payments

After restarting:

- Verify `/health` endpoints return HTTP 200.
- Check Grafana to confirm the error rate is decreasing.
- Ensure the alert is resolved.

## Escalation

If the issue is not resolved within 10 minutes:

- Notify the instructor/TA.
- Attach logs from the affected services.
- Include a screenshot of the Grafana dashboard and alert.
```

### 4. Alert firing evidence
https://images.iimg.live/images/perfect-snapshot-6043.webp

### 5. Timeline
23:05 - Killed `events` service
23:09 - High Error Rate alert fired
23:09 - Started investigating -> gateway health endpoint says `events` service is down
23:09 - Restarted `events` service - fixed applied
23:11 - Alert resolved

### 6. How long from failure injection to alert firing? Why the delay?
It took 4-5 minutes, because of the 5 minute rate window and 2 minute pending period of the alert. Grafana alerts are configured to accumulate errors first.

## Task 2 — Blameless Postmortem

```md
# Postmortem: QuickTicket events service sudden death

**Date:** 2026-06-26
**Duration:** 23:05 - 23:11
**Severity:** SEV-3
**Author:** Konstantin Ustiuzhanin

## Summary
Due to unknown reasons, events service was killed. The error rate was high and the application became unusable
[1-2 sentences: what happened and impact]

## Timeline
| Time | Event |
|------|-------|
| 23:05 | [The `events` service was killed] |
| 23:09 | [Grafana fired the High Error Rate alert] |
| 23:09 | [Engineer on duty responded to the alert and started the investigation] |
| 23:09 | [Following the playbook, the root cause was quickly found: `events` service was down] |
| 23:09 | [`events` service was restarted] |
| 23:11 | [Alert was already resolved] |

## Root Cause
The `events` service was unavailable, making the entire application unusable. Error rate rose up to 100%.

## What Went Well
- Alert fired within 4 minutes of failure
- Runbook was clear and easy to follow
- The issue was very quickly fixed

## What Went Wrong
- Nothing

## Action Items
| Action | Owner | Priority |
|--------|-------|----------|
| Add an alert for services health | Konstantin | High |
```
