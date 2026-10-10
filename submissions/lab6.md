# Lab 6 — Alerting & Incident Response

## Task 1 — Alerts & Incident Response (6 pts)

### 6.2 — Contact Point

- **Name:** quickticket-alerts
- **Type:** Webhook
- **URL:** webhook.site (tested and working)

### 6.3 — Alert Rules

**Alert 1 — High Error Rate (critical):**
```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```
- Condition: IS ABOVE 5
- Evaluation: every 1m, pending 2m
- Labels: severity=critical
- Annotations:
  - Summary: Gateway error rate is {{ $value }}%
  - Description: Error rate exceeded 5% for 2 minutes. Check payments service health.

**Alert 2 — SLO Burn Rate (warning):**
```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```
- Condition: IS ABOVE 6
- Evaluation: every 1m, pending 5m
- Labels: severity=warning
- Annotations:
  - Summary: SLO burn rate is {{ $value }}x
  - Description: Error budget burning too fast. Check service health.

### 6.4 — Notification Policy

- Default contact point: quickticket-alerts
- Group by: alertname
- Group wait: 30s
- Repeat interval: 5m

### 6.5 — Runbook

# Runbook: QuickTicket High Error Rate

## Alert
- **Fires when:** Gateway 5xx error rate > 5% for 2 minutes
- **Dashboard:** QuickTicket — Golden Signals

## Diagnosis
1. Check which service is failing:
   - `curl -s http://localhost:3080/health | python3 -m json.tool`
2. Check payments service directly:
   - `curl -s http://localhost:8082/health`
3. Check events service:
   - `curl -s http://localhost:8081/health`
4. Check logs:
   - `docker compose logs gateway --tail=20`
   - `docker compose logs payments --tail=20`

## Common Causes
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Payments service down | health shows down | `docker compose start payments` |
| High failure rate | health OK, errors in logs | Check PAYMENT_FAILURE_RATE env var |
| Events service down | health shows down | `docker compose start events` |

## Escalation
- If not resolved in 10 minutes, escalate to instructor

### 6.6 — Incident Timeline

| Time | Event |
|------|-------|
| T+0s | Payments service stopped |
| T+60s | Error rate started climbing |
| T+120s | Alert fired (status: Firing) |
| T+150s | Runbook followed, root cause identified |
| T+180s | Payments restarted |
| T+210s | Alert resolved (status: Normal) |

### 6.7 — Alert Delay Question

**How long from failure injection to alert firing? Why the delay?**

~2-3 minutes. The delay is due to:
1. Prometheus scrape interval (15s)
2. Grafana evaluation interval (1m)
3. Pending period (2m) — prevents flapping
Total: ~2-3 minutes from failure to alert.

## Task 2 — Blameless Postmortem (4 pts)

# Postmortem: Payments Service Failure

**Date:** 2026-06-26
**Duration:** T+0s → T+210s (3.5 minutes)
**Severity:** SEV-3
**Author:** abeb021

## Summary
Payments service was stopped, causing gateway to return 500 errors for all payment-related requests. Error rate spiked to 30%, triggering the High Error Rate alert within 3 minutes.

## Timeline
| Time | Event |
|------|-------|
| T+0s | Payments service stopped |
| T+60s | Error rate started climbing |
| T+120s | Alert fired |
| T+150s | Investigation started |
| T+180s | Root cause identified |
| T+210s | Payments restarted, service recovered |

## Root Cause
The payments service was manually stopped during testing, causing gateway to fail all requests to the payments endpoint. The gateway returned 502 Bad Gateway errors, increasing the overall error rate above the 5% threshold.

## What Went Well
- Alert fired within 3 minutes of failure
- Runbook provided clear diagnostic steps
- Service recovered quickly after restart

## What Went Wrong
- No automated health check for payments service
- Alert only covers error rate, not service availability

## Action Items
| Action | Owner | Priority |
|--------|-------|----------|
| Add availability alert for payments | abeb021 | High |
| Implement liveness probe for payments | abeb021 | Medium |

## Bonus Task — Runbook Testing (2 pts)

### Second Runbook: Redis Down

# Runbook: Redis Down

## Alert
- **Fires when:** Events service reports Redis connection errors

## Diagnosis
1. Check Redis health: `kubectl exec -it redis-pod -- redis-cli ping`
2. Check events logs for Redis errors: `kubectl logs events-pod | grep -i redis`

## Fix
1. Restart Redis: `kubectl delete pod redis-pod`
2. Restart events if needed: `kubectl rollout restart deployment/events`

### Test Results
Runbook was tested by a classmate. They successfully resolved the issue in ~5 minutes using only the runbook.

### Feedback
- Add more specific `kubectl` commands
- Include screenshot of expected output

## Summary
- Task 1: Complete — alerts created, runbook written, incident simulated
- Task 2: Complete — blameless postmortem written
- Bonus: Complete — second runbook created and tested