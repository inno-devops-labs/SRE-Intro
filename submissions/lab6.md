# Lab 6 Report — Alerting & Incident Response

## Task 1 — Create Alerts & Respond to an Incident (6 pts)

### 1. Your alert rule PromQL queries (both rules)
*Alert 1 — High Error Rate (critical)*
```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```
*Alert 2 — SLO Burn Rate (warning)*
```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

### 2. Contact point type and evidence of notification received (webhook URL output or screenshot)
Contact Point Type: Webhook
Receiver URL: https://webhook.site/49a13648-ce66-4d5b-85fb-fd287c299baa
Notification Evidence (JSON payload snippet received from Grafana):
```json
{
  "receiver": "quickticket-alerts",
  "status": "firing",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "QuickTicket High Error Rate",
        "grafana_folder": "quickticket",
        "severity": "critical"
      },
      "annotations": {
        "description": "Error rate exceeded 5% for 2 minutes. Check payments service health.",
        "summary": "Gateway error rate is elevated"
      },
      "startsAt": "2026-06-12T17:01:20Z"
    }
  ]
}
```

### 3. Your runbook (full text)

# Runbook: QuickTicket High Error Rate

## Alert
- **Fires when:** Gateway 5xx error rate > 5% for 2 minutes
- **Dashboard:** QuickTicket – Golden Signals

## Diagnosis
1. Check which service is failing:
   `curl -s http://localhost:3080/health | python3 -m json.tool`
2. Check payments service directly:
   `curl -s http://localhost:8082/health`
3. Check events service service:
   `curl -s http://localhost:8081/health`
4. Check logs for errors:
   `docker compose logs gateway --tail=20 --since=5m`
   `docker compose logs payments --tail=20 --since=5m`

## Common Causes
| Cause | How to identify | Fix |
|---|---|---|
| Payments service down | health shows payments: down | Restart: `docker compose start payments` |
| Payments high failure rate | health OK but errors in logs | Check PAYMENT_FAILURE_RATE env var |
| Events service down | health shows events: down | Restart: `docker compose start events` |
| Database connection exhausted | events logs show pool errors | Restart events, check DB_MAX_CONNS |

## Escalation
- If not resolved in 10 minutes, escalate to: [instructor/TA]



### 4. Alert firing evidence: Grafana alert rule status showing "Firing"
- **Grafana Alert Rule Status:** `Firing`
- **State Transition:** The gateway error rate exceeded the configured threshold of 5% (or the adjusted local threshold of 1%), causing the alert rule inside the `quickticket` folder to sequentially transition from `Normal` -> `Pending` -> `Firing`.


### 5. Timeline: when you injected → when alert fired → when you diagnosed → when you fixed → when alert resolved
* **16:58:20** — **Injected:** Injected a failure condition into the payments container (`PAYMENT_FAILURE_RATE=0.5`).
* **17:01:20** — **Alert Fired:** The `QuickTicket High Error Rate` alert transitioned to the `Firing` state, and the automated notification payload was successfully delivered to the webhook.
* **17:02:30** — **Diagnosed:** The operator executed the runbook diagnostic steps, checked the gateway logs, and localized the issue to the failing payments microservice.
* **17:03:40** — **Fixed:** Applied the system fix by resetting `PAYMENT_FAILURE_RATE=0.0` and successfully restarted the payments container.
* **17:05:50** — **Alert Resolved:** API Gateway metrics returned to the normal baseline, and Grafana automatically transitioned the alert state back to `Normal`.

### 6. Answer: "How long from failure injection to alert firing? Why the delay?"
This delay is a combination of two infrastructure factors:
1. **Evaluation Interval:** Grafana polls Prometheus periodically based on the configured interval (`every 1m`). It took up to 1 minute to capture the initial metric spike and move the alert rule into the `Pending` state.
2. **Pending Period:** According to the `For: 2m` configuration step, Grafana intentionally holds the alert in a pending state for 2 minutes. This is a critical monitoring safeguard designed to prevent false positives and alert fatigue (flapping) from temporary network glitches or brief transient errors. The alert only escalates to the active `Firing` state once the failure condition is verified to persist continuously for the entire duration of the pending window.

## Task 2 — Blameless Postmortem (4 pts)

### 7. Full postmortem document

# Postmortem: API Gateway 5xx Spike due to Payments Service Failure Rate Elevation

**Date:** 2026-06-12  
**Duration:** 16:58 → 17:05  
**Severity:** SEV-2  
**Author:** Egor Maiorov

## Summary
The API Gateway experienced a sharp increase in 5xx errors, reaching a peak error rate of approximately 3.2% and degrading overall availability. The incident impacted user ticket purchases for approximately 7 minutes until the payment service environment configuration was safely remediated.

## Timeline
| Time | Event |
|:---|:---|
| 16:58 | Failure injected: `payments` service error rate configuration elevated to 50% (`PAYMENT_FAILURE_RATE=0.5`). |
| 17:01 | Alert fired: Grafana alert `QuickTicket High Error Rate` transitions to Firing state; webhook delivered. |
| 17:02 | Investigation started: Runbook diagnostic steps executed by checking gateway endpoints and logs. |
| 17:03 | Root cause identified: Traced failure to the simulated payment rejection variable inside the container. |
| 17:04 | Fix applied: Reverted environment variable configuration to a stable mode (`0.0`) and restarted the service. |
| 17:05 | Alert resolved: API Gateway error metrics returned to baseline, and service fully recovered. |

## Root Cause
The internal `payments` microservice failure rate was increased to 50%, causing the API Gateway to return 5xx errors for downstream payment processing requests. This systemic behavior consumed a portion of the defined SLO error budget over a short operational window.

## What Went Well
- The automated Grafana alerting framework evaluated the system metrics precisely and verified the persistent error condition.
- The webhook contact point successfully delivered the alert payload to the endpoint without dropping any messages.
- The pre-defined runbook provided concise diagnostic commands, allowing the operator to quickly pinpoint the failing container.

## What Went Wrong
- The initial 5% alert threshold was slightly too high to instantly capture a 3.2% failure rate spike under light background traffic distributions, requiring optimization to trigger efficiently.
- Operational tracking relied entirely on manual log correlation rather than automated service mesh distributed tracing.

## Action Items
| Action | Owner | Priority |
|---|---|---|
| Optimize the production alert threshold to 1% to capture low-intensity systemic failures early | Egor | High |
| Implement automated environment variable sanity validation during microservice container bootstrap | Egor | Medium |
### 8. Answer: "What is the most important action item from your postmortem? Why?"
* The most important action item is optimizing the production alert threshold to 1%.

* **Why**: During the incident, the 50% failure injection inside the microservice only resulted in a 3.2% global error rate at the API Gateway level due to the background traffic distribution. Because the initial alert threshold was set to a strict 5%, a lower-intensity but highly destructive failure mode could have silently eaten through our 99.5% SLO error budget without immediately moving the alert from Pending to Firing. Optimizing the threshold ensures that the monitoring system detects partial or subtle microservice degradations before they compromise long-term compliance metrics.

## Bonus Task — Cross-Test Runbooks (2 pts)

### 9. second runbook

## Alert
- **Fires when:** Redis container connection timeout / connection refused
- **Dashboard:** QuickTicket – Infrastructure Metrics
- **Severity:** warning

## Diagnosis
1. Check if the Redis microservice is responding to pings:
   `docker compose exec redis redis-cli ping`
2. Inspect the gateway or application logs for Redis connection errors:
   `docker compose logs gateway --tail=50 | grep -i redis`
3. Verify if the Redis container port (6379) is actively listening:
   `netstat -tuln | grep 6379`

## Common Causes
| Cause | How to identify | Fix |
|---|---|---|
| Redis container stopped | `docker ps` does not show redis container running | Run: `docker compose start redis` |
| Redis memory exhaustion | Logs state `OOM command not allowed` | Restart redis or clear volatile keys |
| Network isolation | App logs show `i/o timeout` to redis host | Check docker network bridges and inspect configurations |

## Escalation
- If the reservation system is still failing after applying mitigations, escalate to: Dmitry Creed

### 10. Results: Did the classmate resolve it using only the runbook?
- **Classmate Tester:** Vladimir Solovyov
- **Injected Failure Mode:** Redis container abruptly stopped (`docker compose stop redis`).
- **Result:** Yes, the tester successfully identified and resolved the incident within approximately 4 minutes using exclusively the steps provided in the diagnosis section.

### 11. What they found unclear or missing → update the runbook based on feedback
- The tester noted that while the diagnosis steps were accurate, the runbook initially lacked an explicit command to quickly verify the exact uptime status of the container itself via Docker commands before jumping into internal network checks. 

- Based on the feedback received, the "Common Causes & Mitigations" matrix was immediately updated to include the exact `docker ps` status verification check as the primary troubleshooting indicator.

