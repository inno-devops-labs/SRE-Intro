# Lab 6 — Alerting & Incident Response

## Task 1 — Create Alerts & Respond to an Incident

### 6.1: Start the full stack

I used Docker Compose, as step 6.1 asks. My k3d cluster from Lab 4 stayed running as a separate environment. I started QuickTicket, Prometheus, and Grafana. All three Prometheus targets were up.

```powershell
docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml -f docker-compose.lab6.yaml up -d --build
python scripts/provision_lab6.py
docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml restart grafana
python scripts/lab6_load.py --seconds 4200 --event 6
```

The load script sends one list request, one reservation, and one payment per loop. This gives about three requests per second. I used a separate test event with 100,000 tickets so normal events did not sell out during the test. The event ID was 6 in this database; check it before another run.

The original load generator sends few payment requests. A 50% payment failure rate may stay below 5% of all gateway requests. I kept the required alert threshold and used more checkout traffic instead.

### 6.2: Create a contact point

I used a Webhook contact point named `quickticket-alerts`. Its URL inside Docker is `http://webhook:8080`. The local receiver saves real JSON notifications. I can read them at `http://localhost:8090`. This is a local replacement for webhook.site.

### 6.3: Create alert rules

The rules are Grafana-managed. Their complete configuration is in `monitoring/grafana/provisioning/alerting/quickticket.json`. Before a fresh Grafana setup, I run `python scripts/provision_lab6.py`; it reads the local Prometheus UID and writes it into the alert configuration.

**QuickTicket High Error Rate:**

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

- Above 5%, evaluated every 1 minute, pending for 2 minutes.
- Label: `severity=critical`.
- Summary: `Gateway error rate is {{ $values.A.Value }}%`.
- Description: `Error rate exceeded 5% for 2 minutes. Check payments service health.`

**QuickTicket SLO Burn Rate:**

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

- Above 6, evaluated every 1 minute, pending for 5 minutes.
- Label: `severity=warning`.
- A burn rate of 6 uses the budget six times faster than planned. At a steady rate, a 30-day budget lasts 5 days.

Before the first 5xx response, the error query can have no series. I set No Data to Normal and query errors to Error. No Data does not prove that the app is healthy; I also check traffic and scrape targets.

### 6.4: Configure notification policy

The default receiver is `quickticket-alerts`. Alerts are grouped by `alertname`. Group wait is 30 seconds, group interval is 1 minute, and repeat interval is 5 minutes. Recovery messages are enabled.

### 6.5: Write a runbook

# Runbook: QuickTicket High Error Rate

## Alert

- **Fires when:** Gateway 5xx error rate is above 5% for 2 minutes.
- **Dashboard:** QuickTicket — Golden Signals, in Grafana at `http://localhost:3000`.
- **Scope:** Local Docker Compose stack. Run the commands below from the repository root in PowerShell.

## Diagnosis

1. Record the time with `Get-Date -Format o`. Open Grafana → Alerting → Alert rules. Check the error rate and confirm that traffic is still running.
2. Check the gateway and both services:

   ```powershell
   curl.exe -s http://localhost:3080/health
   curl.exe -s http://localhost:8082/health
   curl.exe -s http://localhost:8081/health
   ```

3. Read recent logs:

   ```powershell
   docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml logs gateway --tail=20 --since=5m
   docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml logs payments --tail=20 --since=5m
   ```

4. If health is OK but payments fail, check the active settings:

   ```powershell
   docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml exec -T payments printenv PAYMENT_FAILURE_RATE PAYMENT_LATENCY_MS
   ```

5. For injected payment failures, restore the normal setting:

   ```powershell
   $env:PAYMENT_FAILURE_RATE = '0.0'
   docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml stop payments
   docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml up -d payments
   ```

6. Keep traffic running. Check that new payments succeed, the High Error Rate rule returns to Normal, and the receiver gets a resolved message. The 30-minute burn-rate rule can stay active longer because it still includes earlier errors.

## Common Causes

| Cause | How to identify | Fix |
|---|---|---|
| Payments down | Gateway health says payments is down | Run the Compose command with `start payments` |
| High payment failure rate | Health is OK; logs show failed charges; env value is above zero | Set `PAYMENT_FAILURE_RATE=0.0` and recreate payments |
| Events down | Gateway health says events is down | Run the Compose command with `start events` |
| Database pool full | Events logs show pool errors | Check DB load and `DB_MAX_CONNS`; restart events only if needed |

Use `docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml` before each service command in the table.

## Escalation

If the service is not recovered in 10 minutes, contact the instructor or TA. Share the start time, alert name, health output, and recent logs. Do not share tokens or passwords.

### 6.6: Inject failure and respond

I ran the incident on 27 September 2026. All times below are UTC. My local time was UTC+3.

I changed `PAYMENT_FAILURE_RATE` to `0.5` and recreated payments. The test runner used the same Compose commands as the lab, with PowerShell-compatible environment settings. It saved the evidence in [evidence/lab6](evidence/lab6). Both rules later returned to Normal, and both recovery notifications arrived. The fault setting is back to 0.0.

Both alerts fired. I waited for both rules in this controlled test so I could check them together. In a real incident, I would start the fix as soon as I found the cause.

I followed the runbook: checked gateway, payments, and events health, read gateway and payment logs, then checked the payment environment. The services were running, but payments reported `failure_rate: 0.5`. The environment check returned:

```text
0.5
0
```

These values were `PAYMENT_FAILURE_RATE` and `PAYMENT_LATENCY_MS`. I restored the failure rate to `0.0` and recreated payments. High Error Rate returned to Normal at 12:00:00. The receiver got its resolved message at 12:00:30.

The first contact-point test used an old Grafana API and returned HTTP 410. I changed the test to the Grafana 13 API and repeated it successfully. The saved timeline includes both attempts. The test script now uses the corrected API before injection.

### 6.7: Proof of work

**1. Alert queries:** Both exact queries and settings are in 6.3. The [live rule export](evidence/lab6/alert-rules-export.json) confirms the two installed rules.

**2. Contact point and notification:** Webhook, named `quickticket-alerts`. The successful [contact test](evidence/lab6/contact-test.json) returned:

```json
{"status": "success", "duration": "2ms"}
```

The [received notifications](evidence/lab6/notifications.json) include this real alert, shortened here:

```json
{
  "received_at": "2026-09-27T11:54:30.029520+00:00",
  "receiver": "quickticket-alerts",
  "status": "firing",
  "alertname": "QuickTicket High Error Rate",
  "severity": "critical",
  "startsAt": "2026-09-27T11:54:00Z",
  "summary": "Gateway error rate is 12.848168927694125%"
}
```

This short example selects fields from the full payload; the raw file keeps the original JSON structure. The SLO notification arrived at 11:56:35.016 UTC. The High Error Rate resolved notification arrived at 12:00:30.009 UTC.

**3. Full runbook:** Included in 6.5 above.

**4. Firing evidence:** The Grafana API reported:

```text
QuickTicket High Error Rate | state=firing | lastEvaluation=2026-09-27T11:54:00Z
QuickTicket SLO Burn Rate   | state=firing | lastEvaluation=2026-09-27T11:56:00Z
```

Raw snapshots: [High Error Rate firing](evidence/lab6/high-errors-firing.json), [SLO Burn Rate firing](evidence/lab6/slo-burn-firing.json), [High Error Rate resolved](evidence/lab6/high-errors-resolved.json), and [both rules Normal](evidence/lab6/slo-burn-resolved.json). Grafana uses `firing` for the rule and `Alerting` for its alert instance. `inactive` with instance state `Normal` is the recovered state.

**5. Measured timeline:**

| Time (UTC) | Event |
|---|---|
| 11:50:26.976 | Started failure injection |
| 11:50:29.090 | Payments recreated with 50% failure rate |
| 11:51:00 | SLO Burn Rate entered Pending |
| 11:52:00 | High Error Rate entered Pending |
| 11:54:00 | High Error Rate fired |
| 11:54:30.030 | Webhook received the critical alert |
| 11:55:52.351 | Corrected contact-point test succeeded |
| 11:56:00 | SLO Burn Rate fired |
| 11:56:12.537 | Started diagnosis using the runbook |
| 11:56:14.554 | Confirmed the active failure-rate setting |
| 11:56:16.985 | Applied the fix: failure rate 0.0 |
| 11:56:35.016 | Webhook received the warning alert |
| 12:00:00 | High Error Rate returned to Normal |
| 12:00:30.009 | Webhook received the critical resolved message |
| 12:21:00 | SLO Burn Rate returned to Normal |
| 12:21:34.968 | Webhook received the warning resolved message |
| 12:21:37.492 | Final health check passed; experiment completed |

Alert times come from Grafana's evaluation timestamps. The script polled every 15 seconds, so [timeline.jsonl](evidence/lab6/timeline.jsonl) records when it observed each change, slightly later. Diagnosis and fix times come from the script itself.

**6. How long from failure injection to alert firing? Why the delay?**

It took about **3 minutes 31 seconds**, from 11:50:29 to 11:54:00. The five-minute query window still included healthy requests, so the error rate did not cross 5% at once. Grafana then waited for two minutes of bad results, with one evaluation each minute. The first notification took another 30 seconds because of the group wait.

The burn-rate query uses available samples in its 30-minute range. This run began before a full 30 minutes of traffic was available, so its first results used a shorter history. After the fix, old errors still affected that rule for longer than the five-minute error rule. It returned to Normal at 12:21:00, about 24 minutes 43 seconds after the fix. The [final health check](evidence/lab6/recovered-health.json) showed gateway, events, and payments healthy. I then stopped the extra test traffic.

## Task 2 — Blameless Postmortem

### 6.8: Write the postmortem

# Postmortem: Payment failures during the QuickTicket lab

**Date:** 27 September 2026

**Duration:** 11:50:29 → 11:56:17 UTC for the injected fault, about 5 minutes 48 seconds. The critical alert returned to Normal at 12:00:00.

**Severity:** SEV-3, local lab only
**Author:** NurKhabib

## Summary

About half of payment attempts failed while the failure-rate setting was 0.5. Gateway returned HTTP 500 for these failed charges. The first alert notification showed a 12.85% gateway error rate; only test traffic was affected.

## Timeline

| Time (UTC) | Event |
|---|---|
| 11:50:29 | Fault became active after the payments container was recreated |
| 11:54:00 | Critical alert fired |
| 11:54:30 | Critical notification arrived |
| 11:56:00 | Warning alert fired |
| 11:56:12 | Investigation started |
| 11:56:14 | Active failure-rate value confirmed as the cause |
| 11:56:17 | Normal setting restored |
| 12:00:00 | Critical alert resolved |
| 12:00:30 | Recovery notification arrived |
| 12:21:00 | The longer burn-rate alert also resolved |
| 12:21:35 | Warning recovery notification arrived |

## Root Cause

The payments service allows a failure-rate setting for lab tests. At 0.5, it returned errors for about half of charge requests. Gateway passed those errors to clients. The health endpoint stayed healthy because the process was still running; it did not show whether real purchases succeeded.

The system has no separate guard that stops fault settings from being used in a normal environment. Also, an overall gateway error rate can hide payment failures when checkout traffic is low. This is why the test used more real checkout requests while keeping the required 5% threshold.

## What Went Well

- Both alert rules fired with the required thresholds and pending periods.
- The webhook saved the real notifications, including the critical recovery message.
- Both alerts returned to Normal without changing thresholds or clearing metrics.
- The runbook included an environment check, which explained why health was green while charges failed.
- Restoring the setting did not need changes to application code or the database.

## What Went Wrong

- The first contact-point test used a removed API. This should have been caught before injection.
- A green health response could have delayed diagnosis without the payment setting and logs.
- The 30-minute burn-rate window kept old failures after the service was fixed. It needs a clear note in the runbook.
- The lab deliberately waited for the warning rule before fixing the fault. This is useful for testing, but would extend a real incident.

## Action Items

| Action | Owner | Priority |
|---|---|---|
| Reject non-zero fault settings outside an explicit lab/test mode | NurKhabib, application maintainer | High |
| Add a payment-specific error alert so read traffic cannot hide checkout failures | NurKhabib, monitoring owner | High |
| Use the working Grafana 13 contact test before each exercise | NurKhabib, lab operator | Medium |
| Keep the note about health checks and the longer burn-rate recovery in the runbook | NurKhabib, runbook owner | Medium |

These are follow-up actions, not claims that new application features have already been built. The API test and runbook notes are included in this submission.

**What is the most important action item? Why?**

The most important action is to block fault settings outside test mode. It prevents this kind of failure before customers see it. A payment-specific alert is still needed, because payments can fail for other reasons too.
