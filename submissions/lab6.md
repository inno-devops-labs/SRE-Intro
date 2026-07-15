# Lab 6 Submission - Alerting & Incident Response

Repository: https://github.com/Ars5njo/SRE-Intro
Branch: `feature/lab6`
Date: 2026-06-22
Timezone: Europe/Moscow (UTC+03:00)

## Task 1 - Alerts, Contact Point, Runbook, and Incident Response

### 6.1 Stack startup

Command used from `app/`:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
```

Runtime proof:

```text
app-gateway-1      Up, 0.0.0.0:3080->8080/tcp
app-events-1       Up, 0.0.0.0:8081->8081/tcp
app-payments-1     Up, 0.0.0.0:8082->8082/tcp
app-prometheus-1   Up, 0.0.0.0:9090->9090/tcp
app-grafana-1      Up, 0.0.0.0:3000->3000/tcp
```

Prometheus targets:

```text
events    up    http://events:8081/metrics
gateway   up    http://gateway:8080/metrics
payments  up    http://payments:8082/metrics
```

### 6.2 Contact point and notification policy

Contact point provisioning:

```yaml
name: quickticket-alerts
type: webhook
url: http://host.docker.internal:18080/quickticket-alerts
```

Notification policy:

```yaml
receiver: quickticket-alerts
group_by:
  - alertname
group_wait: 30s
group_interval: 1m
repeat_interval: 5m
```

Grafana API proof:

```text
GET /api/v1/provisioning/contact-points
quickticket-alerts, type=webhook, provenance=file

GET /api/v1/provisioning/policies
receiver=quickticket-alerts, group_by=["alertname"], group_wait=30s, repeat_interval=5m
```

Notification evidence from the local webhook receiver:

```text
2026-06-22T08:13:40Z POST /quickticket-alerts
title="[FIRING:1] QuickTicket High Error Rate (QuickTicket critical)"
value B0=11.958309877861462

2026-06-22T08:19:40Z POST /quickticket-alerts
title="[RESOLVED] QuickTicket High Error Rate (QuickTicket critical)"
```

Note: an initial `DatasourceNoData` notification appeared before real traffic was generated. It was not counted as incident evidence. The final provisioning sets `noDataState: OK` to avoid pre-traffic no-data alerts.

### 6.3 Alert rules

The alert rules are provisioned under `monitoring/grafana/provisioning/alerting/rules.yml`.

Alert 1 - High Error Rate:

```yaml
uid: quickticket-high-error-rate
title: QuickTicket High Error Rate
condition: IS ABOVE 5
evaluation: every 1m, for 2m
label: severity=critical
```

PromQL:

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

Annotations:

```text
Summary: Gateway error rate is {{ $value }}%
Description: Error rate exceeded 5% for 2 minutes. Check payments service health.
```

Alert 2 - SLO Burn Rate:

```yaml
uid: quickticket-slo-burn-rate
title: QuickTicket SLO Burn Rate
condition: IS ABOVE 6
evaluation: every 1m, for 5m
label: severity=warning
```

PromQL:

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

Grafana API proof:

```text
GET /api/v1/provisioning/alert-rules
quickticket-high-error-rate, title="QuickTicket High Error Rate", provenance=file
quickticket-slo-burn-rate, title="QuickTicket SLO Burn Rate", provenance=file
```

### 6.5 Runbook: QuickTicket High Error Rate

# Runbook: QuickTicket High Error Rate

## Alert

- **Fires when:** Gateway 5xx error rate > 5% for 2 minutes
- **Dashboard:** QuickTicket - Golden Signals
- **Severity:** critical
- **Primary symptom:** customers see failed payment confirmations or gateway 5xx responses

## Diagnosis

1. Check which service is failing:
   - `curl -s http://localhost:3080/health | python3 -m json.tool`
2. Check payments service directly:
   - `curl -s http://localhost:8082/health`
3. Check events service:
   - `curl -s http://localhost:8081/health`
4. Check gateway logs for failing upstream calls:
   - `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs gateway --tail=120 --since=5m`
5. Check payments logs for injected failures or runtime errors:
   - `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs payments --tail=120 --since=5m`
6. Confirm current error rate:
   - `curl -s 'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B5m%5D))%20%2F%20sum(rate(gateway_requests_total%5B5m%5D))%20%2A%20100'`

## Common Causes

| Cause | How to identify | Fix |
|-------|-----------------|-----|
| Payments service down | gateway health shows payments degraded/down | `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments` |
| Payments high failure rate | payments health returns `failure_rate > 0`, logs show `Payment failed (injected)` | `PAYMENT_FAILURE_RATE=0.0 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments` |
| Events service down | gateway health shows events degraded/down | `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d events` |
| Redis unavailable | events health shows redis degraded/down, reservations fail | `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d redis events` |
| Database connection exhausted | events logs show pool errors or postgres check degraded | Restart events, then review `DB_MAX_CONNS` and request volume |

## Mitigation

1. If payments is down, start it.
2. If payments is healthy but `failure_rate` is non-zero, restart payments with normal fault-injection settings:
   ```bash
   docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
   PAYMENT_FAILURE_RATE=0.0 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
   ```
3. Keep normal traffic running so the 5-minute error-rate window can clear.
4. Watch Grafana Alerting until `QuickTicket High Error Rate` returns to `Normal`.

## Escalation

- If not resolved in 10 minutes, escalate to the instructor/TA with:
  - alert value and start time
  - gateway and payments health output
  - last 5 minutes of gateway and payments logs
  - exact mitigation already attempted

### 6.6 Incident simulation and response

Failure injection:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_FAILURE_RATE=0.5 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

Background traffic:

```bash
./loadgen/run.sh 3 300
```

Targeted payment traffic was added because 50% failures on only the 10% payment path can sit close to the 5% gateway threshold. This kept the configured `PAYMENT_FAILURE_RATE=0.5` while making the alert deterministic.

Targeted traffic excerpt:

```text
11:11:11+0300 attempt=1 reservation=2a594975-9264-4528-b50e-18ef53cc4781 pay_status=500
11:11:12+0300 attempt=2 reservation=1978139a-e291-43b8-95a6-ebe27b780952 pay_status=200
11:11:14+0300 attempt=4 reservation=385b390f-3e62-45a6-bb18-07d4865fc168 pay_status=500
11:11:25+0300 attempt=12 reservation=2a48b2ce-e7b2-48c0-926f-85e347be285f pay_status=500
```

Alert firing evidence:

```text
2026-06-22T11:11:28+0300
Prometheus high error rate query: 9.648969299372753%
Grafana state: Pending
activeAt: 2026-06-22T08:11:10Z

2026-06-22T11:13:25+0300
Prometheus high error rate query: 12.194840728345524%
Grafana state: Alerting
activeAt: 2026-06-22T08:13:10Z
Grafana alert value: 11.958309877861462%

2026-06-22T11:13:40+0300
Webhook notification received: [FIRING:1] QuickTicket High Error Rate
```

Runbook diagnosis evidence:

```text
2026-06-22T11:14:48+0300

gateway /health:
{
  "status": "healthy",
  "checks": {
    "events": "ok",
    "payments": "ok",
    "circuit_payments": "CLOSED"
  }
}

payments /health:
{"status":"healthy","failure_rate":0.5,"latency_ms":0}

events /health:
{
  "status": "healthy",
  "checks": {
    "postgres": "ok",
    "redis": "ok"
  }
}
```

Payments log evidence:

```text
payments-1 | {"time":"2026-06-22 08:14:34,992","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 00625b38-f9d7-4f2c-995d-d1fc2d073fdf"}
payments-1 | INFO: "POST /charge HTTP/1.1" 500 Internal Server Error
payments-1 | {"time":"2026-06-22 08:14:37,160","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 8ee28df2-1081-43c9-b1bd-4228f6171bc4"}
```

Gateway log evidence:

```text
gateway-1 | {"time":"2026-06-22 08:14:37,161","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 500 Internal Server Error\""}
gateway-1 | INFO: "POST /reserve/8ee28df2-1081-43c9-b1bd-4228f6171bc4/pay HTTP/1.1" 500 Internal Server Error
gateway-1 | {"time":"2026-06-22 08:14:39,306","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 500 Internal Server Error\""}
```

Fix:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_FAILURE_RATE=0.0 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

Fix proof:

```text
2026-06-22T11:15:36+0300
payments /health: {"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

Resolution evidence:

```text
2026-06-22T11:20:36+0300
Prometheus high error rate query: 0%
Grafana High Error Rate state: Normal

2026-06-22T11:19:40+0300
Webhook notification received: [RESOLVED] QuickTicket High Error Rate
```

The SLO Burn Rate warning also fired:

```text
2026-06-22T11:16:45+0300
Webhook notification received: [FIRING:1] QuickTicket SLO Burn Rate
value B0=21.853733848713077
```

This is expected because the burn-rate query uses a longer 30-minute window, so it remains elevated after the short incident even after the fast 5-minute High Error Rate alert resolves.

### Timeline

| Time (UTC+03) | Event |
|---------------|-------|
| 11:06:55 | Fault injection exercise started: payments was stopped and recreated with `PAYMENT_FAILURE_RATE=0.5`. |
| 11:11:11 | First confirmed user-impacting targeted payment failure after local traffic generator was run outside the sandbox. |
| 11:11:28 | Prometheus high error rate was 9.65%; Grafana High Error Rate state was `Pending`. |
| 11:13:10 | Grafana High Error Rate became `Alerting`. |
| 11:13:40 | Webhook notification received for `QuickTicket High Error Rate`. |
| 11:14:48 | Runbook diagnosis completed: gateway/events healthy, payments healthy but `failure_rate=0.5`, logs showed injected payment 500s. |
| 11:15:36 | Fix verified: payments recreated with `PAYMENT_FAILURE_RATE=0.0`. |
| 11:16:45 | SLO Burn Rate warning notification received due 30-minute burn-rate window. |
| 11:19:10 | Grafana High Error Rate ended. |
| 11:19:40 | Webhook resolved notification received for High Error Rate. |
| 11:20:36 | Prometheus high error rate query returned 0%; Grafana state was `Normal`. |

### Alert delay answer

From the first confirmed failing payment request at 11:11:11 to Grafana `Alerting` at 11:13:10, the delay was about 1 minute 59 seconds. To the webhook notification at 11:13:40, the delay was about 2 minutes 29 seconds.

The delay is expected because the alert has a `for: 2m` pending period and is evaluated every 1 minute. Grafana must observe the condition continuously above 5% before moving from `Pending` to `Alerting`, then the notification policy waits 30 seconds before sending the grouped webhook.

Measured from the earlier env-var injection command at 11:06:55, the delay was longer because the first local traffic generator run was sandboxed and produced `000` client-side failures without reaching the gateway. I counted the alert SLO delay from the first confirmed gateway 500 because that is when the user-visible incident began in metrics.

## Task 2 - Blameless Postmortem

# Postmortem: QuickTicket Payment Failure Injection Triggered Gateway 5xx Alert

**Date:** 2026-06-22
**Duration:** 11:11:11 -> 11:19:40 UTC+03 (8m 29s from first confirmed failed request to resolved notification)
**Severity:** SEV-3
**Author:** Arsen

## Summary

Payments was intentionally run with `PAYMENT_FAILURE_RATE=0.5`, causing intermittent 500 responses from `/charge`. Gateway surfaced those failures as 500 responses on `/reserve/{reservation_id}/pay`, driving the gateway 5xx rate above 5% and triggering the High Error Rate alert.

## Timeline

| Time | Event |
|------|-------|
| 11:06 | Fault injection exercise started by recreating payments with `PAYMENT_FAILURE_RATE=0.5`. |
| 11:11 | First confirmed targeted payment request failed with HTTP 500. |
| 11:11 | Grafana High Error Rate entered `Pending`; Prometheus showed 9.65% gateway 5xx rate. |
| 11:13 | High Error Rate entered `Alerting`; Grafana value was 11.96%. |
| 11:13 | Webhook notification arrived for High Error Rate. |
| 11:14 | Runbook diagnosis found payments health `failure_rate=0.5` and injected failure logs. |
| 11:15 | Payments was restored with `PAYMENT_FAILURE_RATE=0.0`; health confirmed normal config. |
| 11:19 | High Error Rate resolved in Grafana and webhook resolved notification arrived. |
| 11:20 | Prometheus 5xx rate query returned 0%. |

## Root Cause

The payments service was intentionally configured with a 50% injected failure rate. The gateway payment endpoint depends synchronously on payments, so injected payment 500s propagated to users as gateway 500 responses for the payment-confirmation path. Because the background traffic mix only sends payments for a subset of requests, targeted payment traffic was needed to exercise the failure path strongly enough to exceed the 5% gateway error-rate threshold.

## What Went Well

- The High Error Rate alert fired after the expected pending period.
- The webhook contact point received both firing and resolved notifications.
- The runbook quickly separated service availability from fault configuration: payments was healthy but intentionally returning failures.
- Logs in payments and gateway correlated the same failure mode from upstream and downstream perspectives.

## What Went Wrong

- The first local load generator run was sandboxed and produced client-side `000` failures instead of real gateway traffic. This delayed the observable incident start.
- Grafana emitted a `DatasourceNoData` notification before real traffic existed, which was noisy during setup.
- The generic load generator can show high "fail" percentages after recovery due 409 reservation conflicts, so PromQL 5xx queries are a better recovery signal than loadgen totals.

## Action Items

| Action | Owner | Priority |
|--------|-------|----------|
| Keep alert provisioning with `noDataState: OK` to avoid pre-traffic no-data notifications in lab environments. | Arsen | High |
| Add a small targeted payment load script for incident drills so payment-path alerts can be tested deterministically. | Arsen | High |
| Update the runbook to call out `PAYMENT_FAILURE_RATE` in payments health as a known injected-failure indicator. | Arsen | Medium |
| Add a separate warning for payments injected failures using `payments_charges_total{result="failed"}` so responders see the upstream symptom directly. | Arsen | Medium |

## Most Important Action Item

The most important action item is adding a targeted payment load script for incident drills. Without traffic that reliably exercises `/reserve/{reservation_id}/pay`, a 50% payments failure can stay close to the gateway 5xx threshold because payments are only a small part of the generic traffic mix. Deterministic incident traffic makes alert tests faster, less flaky, and easier to explain.

## Bonus Task - Second Runbook

# Runbook: QuickTicket Redis Down

## Alert

- **Fires when:** Events service cannot use Redis and reservation requests start failing or events health reports Redis degraded.
- **Dashboard:** QuickTicket - Golden Signals
- **Severity:** warning or critical depending on reservation impact
- **Primary symptom:** users can browse events but reservations return 5xx/409-like failures or reservation counts stop changing.

## Diagnosis

1. Check gateway health:
   - `curl -s http://localhost:3080/health | python3 -m json.tool`
2. Check events health:
   - `curl -s http://localhost:8081/health | python3 -m json.tool`
3. Check Redis container state:
   - `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps redis`
4. Check events logs:
   - `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs events --tail=80 --since=5m`
5. Confirm reservation path:
   - `curl -s -X POST -H 'Content-Type: application/json' -d '{"quantity":1}' http://localhost:3080/events/1/reserve`

## Common Causes

| Cause | How to identify | Fix |
|-------|-----------------|-----|
| Redis container stopped | `ps redis` shows exited/stopped, events health redis check fails | `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d redis` |
| Redis reachable but events has stale connection | Redis is up, events health still degraded | `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml restart events` |
| Redis timeout too low | events logs show timeout errors while Redis is up | Increase `REDIS_TIMEOUT_MS` after confirming Redis latency |
| Network/DNS issue | events logs show connection refused or name lookup failure | Inspect Compose network and recreate affected services |

## Mitigation

1. Restore Redis:
   ```bash
   docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d redis
   ```
2. If events remains degraded, restart events:
   ```bash
   docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml restart events
   ```
3. Verify health:
   ```bash
   curl -s http://localhost:8081/health | python3 -m json.tool
   curl -s http://localhost:3080/health | python3 -m json.tool
   ```
4. Verify a reservation succeeds.

## Escalation

- If Redis cannot stay healthy for 10 minutes, escalate to instructor/TA with Redis container state, events health, events logs, and exact restart attempts.

## Cross-Test Result

Second runbook written: yes.

Classmate cross-test: not completed in this local Codex session because it requires a real second person who follows only the runbook. The runbook is ready for peer testing. After a classmate tests it, record:

```text
Tester:
Injected failure:
Start time:
Resolved time:
Did they resolve using only the runbook:
Unclear/missing steps:
Runbook update made after feedback:
```

I am not marking the peer-test acceptance item as complete because that would require human feedback that was not available here.

## PR Checklist

```text
- [x] Task 1 done - alerts created, incident simulated, runbook followed
- [x] Task 2 done - blameless postmortem written
- [ ] Bonus Task done - second runbook written, classmate cross-test still pending
```

## Acceptance Criteria Checklist

### Task 1

- [x] Two alert rules created in Grafana: `QuickTicket High Error Rate` and `QuickTicket SLO Burn Rate`.
- [x] Contact point configured and tested: `quickticket-alerts` webhook received firing and resolved notifications.
- [x] Runbook written with diagnosis, mitigation, and escalation.
- [x] Alert fired during failure injection: High Error Rate entered `Alerting` and sent webhook.
- [x] Timeline recorded from injection to resolution.
- [x] Written answer about alert delay included.

### Task 2

- [x] Full blameless postmortem following the template.
- [x] Focus is on systems/processes, not individual blame.
- [x] Action items are specific and assigned.

### Bonus Task

- [x] Second runbook for a different failure mode written: Redis down.
- [ ] Classmate tested it.
- [ ] Runbook updated based on classmate feedback.
