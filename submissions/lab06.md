# Lab 6 Submission

## Task 1. Alerts, notification, runbook, incident response

### Alert rules

**QuickTicket High Error Rate**

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

- Condition: `> 5`
- Evaluation: every `1m`
- For: `2m`
- Labels: `severity=critical`

**QuickTicket SLO Burn Rate**

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

- Condition: `> 6`
- Evaluation: every `1m`
- For: `5m`
- Labels: `severity=warning`

### Contact point

- Name: `quickticket-alerts`
- Type: `webhook`
- URL: `http://host.docker.internal:18080/alerts`

Evidence that notification was received:

```text
2026-06-26T20:59:25Z  receiver=quickticket-alerts  status=firing
title=[FIRING:1] QuickTicket High Error Rate QuickTicket (critical)
ruleUID=ffqc2ejjb81dsf
```

Resolved notification was also received:

```text
2026-06-26T21:04:25Z  receiver=quickticket-alerts  status=resolved
title=[RESOLVED] QuickTicket High Error Rate QuickTicket (critical)
value B=2.0559, C=0
```

### Runbook

# Runbook: QuickTicket High Error Rate

## Alert

- Fires when: gateway 5xx error rate is above `5%` for `2 minutes`
- Dashboard folder: `QuickTicket`
- Rule: `QuickTicket High Error Rate`

## Diagnosis

1. Check gateway health:
   ```bash
   curl -s http://localhost:3080/health | python3 -m json.tool
   ```
2. If gateway says `payments: down`, check the service state:
   ```bash
   cd app
   docker compose -f docker-compose.lab6.full.yaml -f ../docker-compose.monitoring.yaml ps
   ```
3. Check recent gateway errors:
   ```bash
   docker compose -f docker-compose.lab6.full.yaml -f ../docker-compose.monitoring.yaml logs gateway --tail=20 --since=5m
   ```
4. Check payments logs:
   ```bash
   docker compose -f docker-compose.lab6.full.yaml -f ../docker-compose.monitoring.yaml logs payments --tail=20 --since=5m
   ```

## Common causes

| Cause | How to identify | Fix |
|---|---|---|
| Payments service stopped | `gateway /health` shows `payments: down` | `docker compose ... start payments` |
| Payments connection error from gateway | gateway logs show `payments connect error` | start or restart `payments` |
| Too many failed payment requests | alert fires and webhook shows high reduce value `B` | restore payments and keep healthy traffic running until alert resolves |

## Recovery

```bash
cd app
docker compose -f docker-compose.lab6.full.yaml -f ../docker-compose.monitoring.yaml start payments
```

Then continue healthy traffic and wait until the rule returns to `Normal`.

## Escalation

- If the alert is still firing after `10 minutes`, escalate to the instructor or TA.

### What I used as failure injection

I used `payments` service stop, because the lab hint is correct: `PAYMENT_FAILURE_RATE=0.5` does not guarantee total gateway 5xx above `5%`. Stopping `payments` makes the payment flow fail immediately and reliably triggers the critical alert.

Injection command:

```bash
cd app
docker compose -f docker-compose.lab6.full.yaml -f ../docker-compose.monitoring.yaml stop payments
```

Recovery command:

```bash
cd app
docker compose -f docker-compose.lab6.full.yaml -f ../docker-compose.monitoring.yaml start payments
```

### Firing evidence

Grafana rules API showed:

```text
2026-06-26T20:58:50Z  QuickTicket High Error Rate  state=firing
2026-06-26T20:58:50Z  activeAt=2026-06-26T20:58:50Z
notificationSettings.receiver=quickticket-alerts
```

At the same moment the live 1-minute gateway 5xx rate was:

```text
11.194029850746269%
```

### Diagnosis evidence

When I investigated, gateway health showed:

```json
{
  "status": "degraded",
  "checks": {
    "events": "ok",
    "payments": "down",
    "circuit_payments": "CLOSED"
  }
}
```

Gateway logs also showed:

```text
payments connect error
POST /reserve/.../pay HTTP/1.1" 503 Service Unavailable
```

### Timeline

| Time (MSK) | Event |
|---|---|
| 2026-06-26 23:56:00 | I stopped `payments` |
| 2026-06-26 23:56:50 | Alert condition became active (`pending`) |
| 2026-06-26 23:58:50 | `QuickTicket High Error Rate` became `firing` |
| 2026-06-26 23:59:25 | Webhook notification for firing alert arrived |
| 2026-06-26 23:59:56 | I checked `gateway /health` and confirmed `payments: down` |
| 2026-06-26 23:59:56 | I started `payments` again |
| 2026-06-27 00:00:43 | `gateway /health` returned healthy again |
| 2026-06-27 00:03:50 | High Error Rate returned to `Normal` in Grafana |
| 2026-06-27 00:04:25 | Webhook notification for resolved alert arrived |

### How long from failure injection to alert firing? Why the delay?

It took about **2 minutes 50 seconds** from failure injection (`23:56:00`) to firing (`23:58:50`).

The delay came from three things:

1. Prometheus and Grafana evaluate on intervals, not continuously.
2. The rule had `for: 2m`, so the condition had to stay true for two full minutes.
3. The query uses a `5m` rate window, so the 5xx ratio needed some time to move clearly above the threshold.

## Task 2. Blameless postmortem

# Postmortem: Payments outage triggered High Error Rate alert

**Date:** 2026-06-26  
**Duration:** 2026-06-26 23:56:00 MSK -> 2026-06-27 00:03:50 MSK  
**Severity:** SEV-3  
**Author:** Pavel

## Summary

The `payments` service was stopped, which caused payment requests through `gateway` to fail with 5xx responses. The `QuickTicket High Error Rate` alert fired, notification was delivered, and service was restored by starting `payments` again.

## Timeline

| Time | Event |
|---|---|
| 23:56:00 | `payments` stopped |
| 23:56:50 | High Error Rate entered pending |
| 23:58:50 | High Error Rate fired |
| 23:59:25 | Firing webhook received |
| 23:59:56 | Investigation confirmed `payments: down` |
| 23:59:56 | `payments` started |
| 00:00:43 | gateway health became healthy |
| 00:03:50 | High Error Rate resolved |
| 00:04:25 | Resolved webhook received |

## Root Cause

The payment dependency became unavailable, so gateway could not complete payment requests and returned 5xx responses for that path. Because alerting was based on gateway error rate, the outage appeared as an availability problem at the entry point instead of as a direct payments-specific alert.

## What Went Well

- The critical alert fired automatically and the webhook notification arrived.
- `gateway /health` immediately pointed to the failing dependency.
- Restarting `payments` restored service health quickly.

## What Went Wrong

- The alert did not identify the dependency directly; I still had to confirm it in health and logs.
- The `5m` window plus `for: 2m` means the alert is not instant.
- The `SLO Burn Rate` warning stayed noisy longer because of its longer time window.

## Action Items

| Action | Owner | Priority |
|---|---|---|
| Add a direct alert for `payments` dependency health or connect errors | Pavel | High |
| Keep the gateway high-error alert, but add a dependency-specific runbook link in the annotations | Pavel | Medium |
| Review burn-rate thresholds/window so the warning is less noisy after recovery | Pavel | Medium |

### Most important action item

The most important action item is to **add a direct payments-dependency alert**.

Why: the system already told me that the user-facing symptom was high gateway 5xx, but the real fix was about a single downstream service. A direct alert on `payments` availability or `payments connect error` would reduce diagnosis time and make the response more precise.
