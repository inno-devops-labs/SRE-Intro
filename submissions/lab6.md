# Lab 6 — Alerting & Incident Response

## Task 1 — Create Alerts & Respond to an Incident

1. Your alert rule PromQL queries (both rules)

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

2. Contact point type and evidence of notification received (webhook URL output or screenshot)

- **Type:** Webhook (configured via Webhook.site)
- **Evidence:** Payload delivered to the Webhook receiver upon clicking **Test** in Grafana:

```json
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
      },
      "startsAt": "2026-09-28T16:15:35.529073954Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "",
      "fingerprint": "a4b1e847c1920dfa",
      "silenceURL": "http://localhost:3000/alerting/silence/new?alertmanager=grafana&matcher=alertname%3DTestAlert&matcher=instance%3DGrafana",
      "dashboardURL": "",
      "panelURL": "",
      "values": null,
      "valueString": "[ metric='foo' labels={instance=bar} value=10 ]"
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
  },
  "externalURL": "http://localhost:3000/",
  "appVersion": "13.0.1",
  "version": "1",
  "groupKey": "webhook-a4b1e847c1920dfa-1782499535",
  "truncatedAlerts": 0,
  "orgId": 1,
  "title": "[FIRING:1] TestAlert Grafana ",
  "state": "alerting",
  "message": "**Firing**\n\nValue: [no value]\nLabels:\n - alertname = TestAlert\n - instance = Grafana\nAnnotations:\n - summary = Notification test\nSilence: http://localhost:3000/alerting/silence/new?alertmanager=grafana&matcher=alertname%3DTestAlert&matcher=instance%3DGrafana\n"
}
```

3. Your runbook (full text)

```markdown
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
4. Inspect recent container logs:
   - `docker compose logs gateway --tail=25 --since=5m`
   - `docker compose logs payments --tail=25 --since=5m`

## Common Causes
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Payments container stopped | health endpoint reports payments down | Restart: `docker compose start payments` |
| Elevated payments failure rate | health returns 200 but charges fail in logs | Verify and reset `PAYMENT_FAILURE_RATE` |
| Events container stopped | health endpoint reports events down | Restart: `docker compose start events` |
| Database pool saturation | events logs indicate connection pool exhaustion | Restart events service, inspect `DB_MAX_CONNS` |

## Escalation
- If not resolved within 10 minutes, escalate to on-call lead / course TA.
```

4. Alert firing evidence: Grafana alert rule status showing "Firing"

Log of rule evaluation from the Grafana Alerting API (`GET /api/prometheus/grafana/api/v1/rules`) showing the transition across the incident lifecycle:

```text
2026-09-28 17:39:46 | err=0.48% | state=inactive   (nominal state)
2026-09-28 17:45:52 | err=5.32% | state=pending    (threshold exceeded, pending evaluation started)
2026-09-28 17:47:52 | err=5.49% | state=firing     <-- ALERT FIRING
2026-09-28 17:49:54 | err=4.15% | state=inactive   (incident resolved, metrics returned to threshold)
```

5. Timeline: when you injected → when alert fired → when you diagnosed → when you fixed → when alert resolved

| Time (2026-09-28) | Event |
|-------------------|-------|
| 17:39:46 | Fault injected — payments restarted with `PAYMENT_FAILURE_RATE=1.0` (baseline error rate 0.48%) |
| 17:45:41 | Gateway 5xx rate climbs above the 5% threshold (5.19%) |
| 17:45:52 | `QuickTicket High Error Rate` transitions to **Pending** (5.32%) |
| 17:47:52 | Alert transitions to **Firing** (5.49%) |
| 17:48:24 | Webhook alert notification dispatched after 30s group wait |
| 17:48:24 | Investigation: `/health` showed OK, but logs revealed 500s on all `/charge` calls. Mitigation applied: payments restored with `PAYMENT_FAILURE_RATE=0.0` |
| 17:49:54 | Error rate drops below 5% (4.15%), alert transitions to **Normal** |

6. Answer: "How long from failure injection to alert firing? Why the delay?"

**Time to fire: 8 min 6 s** (from injection at 17:39:46 to firing at 17:47:52). The latency is composed of three sequential mechanisms:

1. **Sliding rate window ramp (~6 min, dominant factor):** The query aggregates error rates across a rolling 5-minute window (`[5m]`). Under our traffic distribution, payment charges represent ~10% of overall throughput, meaning a 100% failure on payments produced a net gateway error rate of ~5.4%, hovering right above the 5% alert threshold. The sliding rate average required ~6 minutes for earlier healthy traffic to age out and push the rolling ratio past 5.0%.
2. **Pending period (2 min):** After crossing the threshold, Grafana enforces a 2-minute `for: 2m` delay before moving from Pending to Firing to prevent flapping on brief traffic spikes.
3. **Evaluation interval (1 min):** The rule is evaluated once every 60 seconds, introducing up to 1 minute of quantization delay.

## Task 2 — Blameless Postmortem

# Postmortem: QuickTicket — Downstream Payment Failure Induces 5xx Spike

**Date:** 2026-09-28  
**Duration:** 17:39:46 → 17:49:54 (10 min 8 s)  
**Severity:** SEV-3 (degraded customer experience: purchase checkouts failing, browsing and reservations functional)  
**Author:** ya-rav  

## Summary
The payments backend experienced complete rejection of all charge operations, causing the gateway to surface HTTP 5xx errors for 100% of checkout attempts. Read requests and reservations remained operational. The `QuickTicket High Error Rate` alert fired and the incident was resolved by resetting the payment service configuration. Zero persistent data loss occurred.

## Timeline
| Time (2026-09-28) | Event |
|-------------------|-------|
| 17:39:46 | Fault injection: payments service reconfigured to fail all charges (`PAYMENT_FAILURE_RATE=1.0`). Baseline errors: 0.48%. |
| 17:45:41 | Gateway 5xx aggregate error rate crosses the 5% SLO threshold (5.19%). |
| 17:45:52 | Rule `QuickTicket High Error Rate` enters **Pending** (5.32%). |
| 17:47:52 | Rule fires (**Firing**, 5.49%); detection duration was 8 min 6 s from initial injection. |
| 17:48:24 | Webhook notification received by on-call. |
| 17:48:24 | Diagnosis and mitigation: per-path metrics isolated failures strictly to `/reserve/{id}/pay`. Payments service restored with `PAYMENT_FAILURE_RATE=0.0`. |
| 17:49:54 | 5-minute rolling error rate decayed below 5% (4.15%); alert resolved to **Normal**. |

## Root Cause
The downstream payments service was returning HTTP 500 on all `/charge` endpoints. The gateway synchronously relayed these downstream failures directly to users. Two architectural factors prolonged customer impact:
1. **Absence of fallback / graceful degradation:** The gateway lacked active circuit-breaking and fallback mechanisms for payments, resulting in synchronous 5xx propagation for all payment calls.
2. **Alert threshold calibration on aggregate traffic:** Payments account for a minor share of total platform traffic (~10%). An outage in payments produces a modest ~5.5% aggregate error rate, taking nearly 6 minutes to clear the 5% window average plus the 2-minute pending verification.

## What Went Well
- Alert evaluation and webhook notification delivered reliably end-to-end.
- Per-route Prometheus metrics allowed immediate isolation of errors to the `/reserve/{id}/pay` endpoint.
- Clear runbook steps made root-cause confirmation and remediation swift (<2 minutes).
- Non-payment functionalities (event discovery, seat holding) remained functional throughout.

## What Went Wrong
- **Delayed detection (~8 min):** The 5-minute averaging window combined with a 2-minute pending threshold significantly delayed alerting for failures affecting lower-volume critical paths.
- **Manual intervention required:** Without an automated circuit breaker, the system could not fast-fail or queue requests without human action.
- **Health check ambiguity:** The payments `/health` endpoint continued returning `ok` while the charge API was failing, masking the issue from basic status probes.

## Action Items
| Action | Owner | Priority |
|--------|-------|----------|
| Implement multi-window burn-rate alerting (fast burn: 2% over 1m; slow burn: 5% over 5m) to catch checkout failures in <2 minutes | ya-rav | High |
| Implement gateway circuit breaker to handle payment service outages with graceful degradation (503 with hold preservation) | ya-rav | High |
| Add a dedicated SLI and alert tracking payments charge success ratio directly, decoupled from gateway read volume | ya-rav | Medium |
| Expand the runbook with dedicated triage for "health check ok but downstream charge throwing 500" scenarios | ya-rav | Medium |

## Most important action item — and why
**Multi-window burn-rate alerting.** A single downstream failure is inevitable in distributed systems, but taking 8 minutes to detect a 100% failure on purchases is excessive. Multi-window alerting triggers immediately on steep error surges regardless of overall traffic dilution, slashing time-to-detect from 8 minutes down to ~1–2 minutes across any critical failure scenario.
