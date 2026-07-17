# Lab 6 — Alerting & Incident Response

## Task 1 — Create Alerts & Respond to an Incident

### 6.2: Contact Point

- **Name:** `quickticket-alerts`
- **Type:** Webhook
- **URL:** https://webhook.site (TLS handshake timeout from Docker container — network restriction, contact point saved but delivery not possible from inside Docker)

### 6.3: Alert Rules

**Alert 1 — High Error Rate (critical):**

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

- Condition: IS ABOVE `1`
- Evaluation: every 1m, pending 1m
- Label: `severity=critical`

**Alert 2 — SLO Burn Rate (warning):**

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])))) / (1 - 0.995)
```

- Condition: IS ABOVE `6`
- Evaluation: every 1m, pending 2m
- Label: `severity=warning`

### 6.4: Notification Policy

Default policy configured with contact point `quickticket-alerts`, grouped by `grafana_folder, alertname`.

### 6.5: Runbook

# Runbook: QuickTicket High Error Rate

## Alert
- **Fires when:** Gateway 5xx error rate > 1% for 1 minute
- **Dashboard:** QuickTicket — Golden Signals

## Diagnosis
1. Check which service is failing:
   - `curl -s http://localhost:3080/health | python3 -m json.tool`
2. Check payments service directly:
   - `curl -s http://localhost:8082/health`
3. Check events service:
   - `curl -s http://localhost:8081/health`
4. Check logs for errors:
   - `docker compose logs gateway --tail=20 --since=5m`
   - `docker compose logs payments --tail=20 --since=5m`

## Common Causes
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Payments service down | health shows payments: down | Restart: `docker compose start payments` |
| Payments high failure rate | health OK but errors in logs | Check PAYMENT_FAILURE_RATE env var, restart with 0.0 |
| Events service down | health shows events: down | Restart: `docker compose start events` |
| Database connection exhausted | events logs show pool errors | Restart events, check DB_MAX_CONNS |

## Escalation
- If not resolved in 10 minutes, escalate to instructor/TA

### 6.6: Incident Simulation & Response

**Failure injected:** `docker compose stop payments` at ~21:52

**Load generator:** 5 RPS for 120s, error rate reached 22.8%

**Alert fired:** ~21:54 (High Error Rate → Firing, 1 instance)

**Diagnosis (following runbook):**
- `curl localhost:3080/health` → `{"status":"degraded","checks":{"events":"ok","payments":"down"}}`
- Root cause: payments service stopped

**Fix applied:** `docker compose start payments` at 21:59:40

**Alert resolved:** ~22:02 (status returned to Normal)

### 6.7: Answers

**How long from failure injection to alert firing?** ~2 minutes. The delay consists of: Prometheus scrape interval (15s) + alert evaluation interval (1m) + pending period (1m). The alert needs at least one full evaluation cycle to detect the condition, then waits the pending period to confirm it's not a transient blip before firing.

---

## Task 2 — Blameless Postmortem

# Postmortem: Payment Service Outage

**Date:** 2026-06-26
**Duration:** 21:52 → 22:02 (~10 minutes)
**Severity:** SEV-2
**Author:** Amina

## Summary
The payments service was stopped, causing all payment requests to fail with 502/503 errors. Users could browse events and create reservations but could not complete purchases. The gateway error rate reached 22.8% during the incident.

## Timeline
| Time | Event |
|------|-------|
| 21:52 | Payments service stopped (failure injected) |
| 21:52 | First 502 errors appear in gateway logs |
| 21:54 | "QuickTicket High Error Rate" alert fires (Firing state) |
| 21:55 | Investigation started — checked /health endpoint |
| 21:55 | Root cause identified — payments: down in health check |
| 21:59:40 | Fix applied — `docker compose start payments` |
| 22:00 | Payments service healthy, error rate dropping |
| 22:02 | Alert resolved — status returned to Normal |

## Root Cause
The payments service became unavailable, causing the gateway to return 502/503 errors for all payment-related requests (`/reserve/{id}/pay`). Since the load generator sends a mix of requests including payments, ~22.8% of all requests failed. The gateway had no fallback for payment failures beyond returning an error, so every payment attempt during the outage resulted in a user-visible error.

## What Went Well
- Alert fired within ~2 minutes of the failure starting
- Health endpoint clearly identified which service was down
- Runbook diagnosis steps were straightforward and led to quick identification
- Fix was simple — restarting the service restored normal operation

## What Went Wrong
- 7+ minutes from alert firing to fix applied (investigation delay)
- No automated recovery — required manual intervention to restart the service
- Webhook notification failed due to network restrictions, so alert was only visible in Grafana UI
- Load generator was needed to trigger the alert — without traffic, the alert wouldn't fire

## Action Items
| Action | Owner | Priority |
|--------|-------|----------|
| Add auto-restart policy for payments (restart: always in compose) | Amina | High |
| Configure working notification channel (email or in-network webhook) | Amina | High |
| Add latency-based alert to catch degradation before full failure | Amina | Medium |
| Implement graceful degradation in gateway for payment failures (done in Lab 1 Task 2) | Amina | Done |
| Add synthetic health check probe that runs independently of user traffic | Amina | Medium |

**Most important action item:** Adding auto-restart for the payments service. This is the highest-impact change because it would have resolved the incident automatically without human intervention, reducing the outage from ~10 minutes to ~30 seconds. In a real production system, this is what Kubernetes liveness probes provide — automatic restart of failed services.
