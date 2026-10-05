# Lab 6 Submission — Alerting & Incident Response

## Task 1 — Grafana alerts and incident response

### 1. Alert rules

Grafana Prometheus data source: `Prometheus` (`PBFA97CFB590B2093`). Both Grafana-managed rules are in folder `QuickTicket`, evaluation group `quickticket-lab6`, evaluated every 1 minute.

**QuickTicket High Error Rate** — critical, `IS ABOVE 5`, pending for 2m:

```promql
(sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100) or vector(0)
```

**QuickTicket SLO Burn Rate** — warning, `IS ABOVE 6`, pending for 5m:

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

The `or vector(0)` on the error query gives the alert a zero value when no 5xx series exists yet.

### 2. Contact point and delivered notification

Configured contact point: `quickticket-alerts`, type **Webhook**, target `webhook.site`. Grafana’s Alertmanager POSTed notifications to the receiver:

- High-error alert notification received at 2026-09-28 07:47:30 UTC: [Webhook request](https://webhook.site/#!/view/007171ca-bdec-4227-8e32-10d83b79ce5b/0276cbaa-80e4-4400-be66-e5c986cf8166/1)
- SLO burn-rate notification received at 2026-09-28 07:50:35 UTC.

The first webhook body showed `status: firing`, alert name `QuickTicket High Error Rate`, severity `critical`, and a measured value of 100% at notification time. The default notification policy routes to `quickticket-alerts`, groups by `alertname`, with 30s group wait and 5m group/repeat intervals.

### 3. Runbook: QuickTicket High Error Rate

**Alert:** Gateway 5xx error rate above 5% for 2 minutes. Dashboard: QuickTicket — Golden Signals.

**Diagnosis**
1. Check application dependencies: `curl -sS http://localhost:3080/health | python3 -m json.tool`.
2. Check payments directly: `curl -sS http://localhost:8082/health`.
3. Check events: `curl -sS http://localhost:8081/health`.
4. Inspect recent logs: `docker compose logs gateway --tail=20 --since=5m` and `docker compose logs payments --tail=20 --since=5m`.
5. Check `PAYMENT_FAILURE_RATE` if payments health is reachable but charges are failing.

| Cause | Identification | Mitigation |
|---|---|---|
| Payments unavailable | Gateway health reports payments down or connection errors in logs | `docker compose start payments` |
| Payment failures injected | Payments `/health` reports nonzero `failure_rate`; logs show injected failures | Restore `PAYMENT_FAILURE_RATE=0.0` and recreate payments |
| Events unavailable | Gateway health reports events down | Start events and inspect its logs |
| DB pool exhausted | Events logs show pool/connection errors | Restore events, inspect `DB_MAX_CONNS` and Postgres health |

Escalate to the instructor/TA if unresolved after 10 minutes.

### 4. Incident and alert evidence

Payments was deliberately configured for 100% failure. The load-generator launch initially used the wrong working directory; once traffic was running, payment failures were confirmed in the gateway counters and payments logs. A stable-label burst was then used so Prometheus could calculate a rate reliably.

```text
$ curl -sS http://localhost:8082/health
{"status":"healthy","failure_rate":1.0,"latency_ms":0}

$ curl -sS -G --data-urlencode 'query=sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100' http://localhost:9090/api/v1/query
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790581503.649,"52.03389830508475"]}]}}
```

Grafana rule-evaluation evidence:

```text
QuickTicket High Error Rate: state=firing, health=ok, value=100
activeAt=2026-09-28T07:47:00Z, evaluation interval=60s, pending duration=120s
QuickTicket SLO Burn Rate: state=firing, health=ok, value≈44.7x
activeAt=2026-09-28T07:50:00Z, pending duration=300s
```

Webhook.site received the firing notifications (links above). Diagnosis showed the downstream payment processor was still healthy at the HTTP level but had `failure_rate: 1.0`; its logs contained `Payment failed (injected)` and HTTP 500 responses. Events/Postgres/Redis checks remained healthy. The runbook mitigation restored payments to `PAYMENT_FAILURE_RATE=0.0`; final service check:

```text
$ curl -sS http://localhost:8082/health
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

Grafana subsequently returned the high-error rule to `inactive` after the 5-minute rate window recovered. The burn-rate alert was also observed firing during the incident; its notification was delivered to the same webhook.

### 5. Incident timeline and alert-delay answer

| UTC time | Event |
|---|---|
| 07:35:58 | Initial 100% payment-failure injection started while preparing the test. |
| 07:44:37 | Stable gateway payment-failure burst started to create a measurable Prometheus rate. |
| 07:45:00 | Critical error-rate alert entered Pending; measured error rate exceeded 5%. |
| 07:47:00 | `QuickTicket High Error Rate` entered Firing (2m pending plus minute evaluation cadence). |
| 07:47:30 | Webhook.site received the critical alert notification. |
| 07:48 onward | Followed runbook: checked gateway/payments/events health and payment logs; identified injected 100% payment failures. |
| 07:50:00 | SLO burn-rate rule entered Firing; webhook received it at 07:50:35. |
| After 07:50 | Restored payments to 0% injected failure; verified its health response returned `failure_rate: 0.0`. |
| After recovery | Gateway error-rate alert returned to `inactive` after the 5-minute rate window cleared. |

**How long from failure injection to alert firing, and why?** From the sustained error condition at 07:45 to the critical alert’s firing at 07:47 was about 2 minutes, plus up to one 1-minute evaluation interval depending on when the condition first crosses the threshold. Grafana also needs the 5-minute Prometheus rate window to reflect enough failing samples. The earlier fault-injection setup preceded sustained measurable 5xx traffic, so it is not the useful start point for measuring alert latency.

## Task 2 — Blameless postmortem

# Postmortem: QuickTicket payment failure spike

**Date:** 2026-09-28  
**Duration:** Controlled test; payments restored after the alert and diagnosis  
**Severity:** SEV-2 (simulated)  
**Author:** Lab submission

### Summary
A fault-injection setting caused the payment service to return HTTP 500 for every charge. The gateway propagated failures to payment requests while event listing and core dependencies remained available; Grafana detected the elevated error rate and sent a webhook notification.

### Timeline
| Time (UTC) | Event |
|---|---|
| 07:35:58 | Payment failure injection enabled during test setup. |
| 07:44:37 | Sustained stable-label payment-failure requests started. |
| 07:45:00 | Critical error-rate rule became Pending. |
| 07:47:00 | Critical rule fired; measured value was 100% at firing. |
| 07:47:30 | Webhook notification received. |
| 07:48 onward | Health endpoints and logs identified `PAYMENT_FAILURE_RATE=1.0`; events/DB/Redis were healthy. |
| After 07:50 | Payment failures reset to 0%; health check confirmed recovery; alert later returned to inactive. |

### Root cause
The payment processor was intentionally configured to fail all charges. The system propagated those downstream failures to the gateway, and diagnosis depended on the runbook explicitly checking the payment failure-injection environment variable. This was a controlled test, not an unintended production outage.

### What went well
- Prometheus captured the gateway 5xx increase and Grafana alert rules evaluated without query errors.
- The critical webhook notification was received and included the alert name and measured value.
- Health checks and logs isolated the payment dependency while events and data services remained available.

### What went wrong
- A wrong working directory delayed the first load-generator start.
- The payment-failure percentage alone does not equal gateway-wide error rate; enough payment-path traffic and a stable metric label were needed to test the threshold reliably.
- The alert delay includes the rolling query window, evaluation interval, and pending duration.

### Action items
| Action | Owner | Priority |
|---|---|---|
| Add a runbook preflight showing the expected working directory and load-generator command | SRE student | High |
| Keep a payment-specific error-rate panel/alert alongside the gateway-wide 5xx alert | SRE student | Medium |
| Add an explicit post-experiment reset step for fault-injection variables | SRE student | High |

**Most important action item:** add an explicit fault-injection reset and verification step. It prevents test configuration from persisting after an experiment and gives the responder a deterministic recovery check.

## Bonus Task — Cross-tested runbook

A Redis-outage runbook draft is included below.
### Runbook: Redis unavailable

**Alert:** Reservations fail or event health reports Redis down.

1. Check `curl -sS http://localhost:3080/health | python3 -m json.tool`.
2. Check `docker compose exec redis redis-cli ping`.
3. Inspect `docker compose logs events --tail=50 --since=10m`.
4. If Redis is stopped, run `docker compose start redis`; then confirm `PONG` and retest a reservation.
5. If Redis is running but unreachable, verify Compose DNS/network and inspect Redis logs; escalate to the platform owner if unresolved.

**Peer test result:** Not performed; no classmate test or feedback is claimed.
