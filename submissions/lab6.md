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

- **Type:** [Webhook](https://webhook.site/6e61b3d5-7035-461a-903a-08673ae56e7a) → https://webhook.site/#!/view/6e61b3d5-7035-461a-903a-08673ae56e7a/71047890-8beb-49f8-8a2a-6b759cbbe72e/1
- **Evidence:** payload received at webhook.site after clicking **Test** on the contact point
  in Grafana — confirms the contact point delivers notifications end-to-end:

```json
{
  "receiver": "webhook",
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
      "startsAt": "2026-06-26T18:45:35.529073954Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "",
      "fingerprint": "57c6d9296de2ad39",
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
  "groupKey": "webhook-57c6d9296de2ad39-1782499535",
  "truncatedAlerts": 0,
  "orgId": 1,
  "title": "[FIRING:1] TestAlert Grafana ",
  "state": "alerting",
  "message": "**Firing**\n\nValue: [no value]\nLabels:\n - alertname = TestAlert\n - instance = Grafana\nAnnotations:\n - summary = Notification test\nSilence: http://localhost:3000/alerting/silence/new?alertmanager=grafana&matcher=alertname%3DTestAlert&matcher=instance%3DGrafana\n"
}
```

3. Your runbook (full text)
```commandline
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
4. Check logs for errors:
   - `docker compose logs gateway --tail=20 --since=5m`
   - `docker compose logs payments --tail=20 --since=5m`
## Common Causes
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Payments service down | health shows payments: down | Restart: `docker compose start payments` |
| Payments high failure rate | health OK but errors in logs | Check PAYMENT_FAILURE_RATE env var |
| Events service down | health shows events: down | Restart: `docker compose start events` |
| Database connection exhausted | events logs show pool errors | Restart events, check DB_MAX_CONNS |
## Escalation
- If not resolved in 10 minutes, escalate to: [instructor/TA]
```
4. Alert firing evidence: Grafana alert rule status showing "Firing"

State of the `QuickTicket High Error Rate` rule, polled from the Grafana alerting API
(`GET /api/prometheus/grafana/api/v1/rules`) and the error-rate value from Prometheus
across the incident:

```text
2026-06-26 22:39:46 | err=0.54% | state=inactive   (baseline)
2026-06-26 22:45:52 | err=5.29% | state=pending    (condition true, pending period started)
2026-06-26 22:47:52 | err=5.44% | state=firing     <-- ALERT FIRING
2026-06-26 22:49:54 | err=4.23% | state=inactive   (resolved after fix)
```

5. Timeline: when you injected → when alert fired → when you diagnosed → when you fixed → when alert resolved

| Time (2026-06-26) | Event |
|-------------------|-------|
| 22:39:46 | Failure injected — payments restarted with `PAYMENT_FAILURE_RATE=1.0` (baseline error rate 0.54%) |
| 22:45:41 | Error rate crosses the 5% threshold (5.21%) |
| 22:45:52 | `QuickTicket High Error Rate` → **Pending** (5.29%) |
| 22:47:52 | Alert → **Firing** (5.44%) |
| 22:48:24 | Webhook notification dispatched (after 30 s group wait) |
| 22:48:24 | Root cause confirmed (payments reachable but every `/charge` returns 500 — seen in payments logs and the per-path 5xx breakdown isolating errors to `/reserve/{id}/pay`) and fix applied — payments restarted with `PAYMENT_FAILURE_RATE=0.0` |
| 22:49:54 | Alert → **Normal** (error rate decayed below 5%, 4.23%) |

6. Answer: "How long from failure injection to alert firing? Why the delay?"

**Time to fire: 8 min 6 s** (injected 22:39:46 → firing 22:47:52). The delay is the sum of
three components:

1. **Rate-window ramp (~6 min, the dominant factor).** The query averages errors over a
   5-minute window (`[5m]`). The steady-state error rate during the incident was only ~5.4%,
   *just* above the 5% threshold. The `[5m]` average starts at 0 and climbs gradually as 5xx
   responses accumulate and pre-incident healthy samples age out, so it only crossed 5% at
   22:45:41 — about 6 minutes after injection.
2. **Pending period (2 min).** Once the condition was true, Grafana held the rule in **Pending**
   for the configured `for: 2m` before firing (22:45:52 → 22:47:52), to avoid flapping on
   short transient spikes.
3. **Evaluation interval (1 min).** The rule is evaluated every 1 minute, adding up to ~1 min
   of quantization.

**Key insight:** the ~8-minute delay (vs the textbook "~3 min") is dominated by component 1 —
the thin margin above threshold. A more severe failure (error rate jumping to ~50%) would
cross 5% almost immediately, collapsing total time-to-fire to ~2–3 min (just the pending
period + evaluation interval). This is the SLO-alerting trade-off: the 5-minute averaging
window plus the pending period buy protection against false alarms at the cost of slower
detection for slow-burn, marginal failures.

## Task 2 — Blameless Postmortem

## Postmortem: QuickTicket — Payment Failures Cause Gateway 5xx Spike

**Date:** 2026-06-26
**Duration:** 22:39:46 → 22:49:54 (10 min 8 s)
**Severity:** SEV-3 (degraded — purchases failing, reads/reservations unaffected)
**Author:** Timur Bikmetov

## Summary
The payments service started rejecting 100% of charge requests, causing the gateway to
return 5xx for every purchase attempt while read and reservation traffic stayed healthy.
The `QuickTicket High Error Rate` alert fired and the service was recovered by restoring
payments to a healthy state. No data was lost.

## Timeline
| Time (2026-06-26) | Event |
|-------------------|-------|
| 22:39:46 | Fault introduced — payments started returning 500 on every charge (`PAYMENT_FAILURE_RATE=1.0`). Baseline error rate 0.54%. |
| 22:45:41 | Gateway 5xx error rate crosses the 5% SLO threshold (5.21%). |
| 22:45:52 | `QuickTicket High Error Rate` enters **Pending** (5.29%). |
| 22:47:52 | Alert **Firing** (5.44%); detection latency 8 min 6 s from fault. |
| 22:48:24 | Webhook notification delivered (after 30 s group wait). |
| 22:48:24 | Diagnosis: payments `/health` was still OK, but its logs showed every `/charge` returning 500 and the gateway per-path 5xx breakdown isolated the errors to `/reserve/{id}/pay`. Fix applied — payments restored (`PAYMENT_FAILURE_RATE=0.0`). |
| 22:49:54 | Error rate decayed below 5%; alert returns to **Normal** (4.23%). Incident resolved. |

## Root Cause
The payments service entered a state where every `/charge` call returned HTTP 500. The
gateway correctly translated these downstream failures into 5xx responses on the purchase
path, so 100% of payment attempts failed for the duration of the incident. Two systemic
factors turned a single-dependency failure into a user-visible, slowly-detected incident:

1. **No graceful degradation.** The gateway's circuit breaker around payments is a no-op
   (not yet implemented — Lab 11), so there was no fast-fail or fallback; every purchase
   request was driven straight into the failing dependency and surfaced as a 5xx.
2. **Detection tuned for severe, not marginal, failures.** A payments-only outage moves the
   *aggregate* gateway error rate only to ~5–6% (payments is a minority of total traffic),
   which sits right on the 5% threshold. Averaged over a 5-minute window, it took ~6 minutes
   just to cross the line, and a further 2-minute pending period to fire — 8 minutes of
   customer-facing failures before anyone was paged.

## What Went Well
- The alert fired correctly and the webhook notification was delivered end-to-end.
- The alert annotation ("Check payments service health") plus the per-path 5xx breakdown
  quickly pointed at payments — errors were cleanly isolated to the `/reserve/{id}/pay` path.
- Blast radius was contained: reads and reservations were never affected, so the incident
  was a partial degradation, not a full outage.
- Time-to-mitigate once firing was short (~2 min): the fix was obvious from the runbook.

## What Went Wrong
- **Detection was slow (8 min).** For a marginal-burn failure, the single 5%-over-5m alert is
  too sluggish — users experienced 100% purchase failures for ~6 minutes before the alert even
  entered Pending.
- **No automatic mitigation.** With the circuit breaker disabled, the system had no way to
  shed or fast-fail failing payment calls; recovery required a human to act.
- **The runbook did not distinguish "payments down" from "payments up but failing."** Here
  `/health` would show payments reachable but charges failing — a row the runbook only
  partially covered.

## Action Items
| Action | Owner | Priority |
|--------|-------|----------|
| Add multi-window burn-rate alerting (fast: ~2% over 1m + slow: 5% over 5m) so payments-only, marginal failures are detected in ~1–2 min instead of ~8 min | Timur Bikmetov | High |
| Implement the payments circuit breaker (Lab 11) so the gateway fast-fails/degrades gracefully instead of surfacing a 5xx for every purchase | Timur Bikmetov | High |
| Add a payments-specific alert on charge success ratio (not just aggregate gateway error rate) so payment incidents are not diluted by read traffic | Timur Bikmetov | Medium |
| Update the runbook with an explicit "payments reachable but charges returning 500" diagnosis branch (check payments logs / `PAYMENT_FAILURE_RATE`) | Timur Bikmetov | Medium |

## Most important action item — and why
**Multi-window burn-rate alerting.** The incident's defining failure was not the payments
fault itself (a single dependency failing is expected and survivable) but that it took
**8 minutes to detect** while 100% of purchases were failing. A fast-burn alert (high error
rate over a short window) paired with the existing slow-burn alert would have paged within
~1–2 minutes, cutting customer-facing impact by roughly 75%. It also generalizes: it improves
detection for *any* sharp failure, not just this one, directly addressing the SLO-alerting
trade-off this incident exposed. The circuit breaker is a close second — it reduces blast
radius — but without faster detection the system still depends on a human noticing late.