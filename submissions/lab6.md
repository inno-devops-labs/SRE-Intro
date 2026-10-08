# Lab 6 — Alerting & Incident Response

**Student:** Rolan
**Branch:** `feature/lab6`
**Date:** 2026-06-24

---

## Task 1 — Create Alerts & Respond to an Incident (6 pts)

### 6.1–6.4 — Stack, contact point, alert rules, notification policy

The full stack + monitoring was started with:

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
./loadgen/run.sh 5 1800 &
```

Rather than clicking through the Grafana UI, the alerting is provisioned **as code** under
`monitoring/grafana/provisioning/alerting/` (contact point, notification policy, two rules). This is
reproducible and version-controlled. The Prometheus datasource was pinned to `uid: prometheus` so the
rules can reference it.

**Contact point** (`contact-points.yaml`) — type **webhook**, posting to a local receiver
(`host.docker.internal:9099`) that logs every notification:

```yaml
contactPoints:
  - orgId: 1
    name: quickticket-alerts
    receivers:
      - uid: quickticket-webhook
        type: webhook
        settings:
          url: http://host.docker.internal:9099/
          httpMethod: POST
```

**Notification policy** (`policies.yaml`): default receiver `quickticket-alerts`, `group_by: [alertname]`,
`group_wait: 30s`, `repeat_interval: 5m`.

Provisioning verified live:

```text
$ curl -s -u admin:admin localhost:3000/api/v1/provisioning/alert-rules
quickticket-high-error-rate | QuickTicket High Error Rate | for 2m | {'severity': 'critical'}
quickticket-slo-burn-rate   | QuickTicket SLO Burn Rate   | for 5m | {'severity': 'warning'}

$ curl -s -u admin:admin localhost:3000/api/v1/provisioning/contact-points
quickticket-alerts webhook http://host.docker.internal:9099/
```

### 6.7.1 — Alert rule PromQL (both rules)

**Alert 1 — QuickTicket High Error Rate (critical)** — condition: `IS ABOVE 5`, eval every 1m for 2m:

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

**Alert 2 — QuickTicket SLO Burn Rate (warning)** — condition: `IS ABOVE 6`, eval every 1m for 5m:

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

### 6.7.2 — Contact point notification evidence

The webhook receiver captured the firing notification **30 s after the alert fired** (matching
`group_wait: 30s`):

```text
=== 15:02:40 POST / ===
{
  "receiver": "quickticket-alerts",
  "status": "firing",
  "title": "[FIRING:1] QuickTicket High Error Rate (QuickTicket critical)",
  "alerts": [
    {
      "status": "firing",
      "labels": { "alertname": "QuickTicket High Error Rate", "severity": "critical" },
      "annotations": { "summary": "Gateway error rate is 5.248117198246171%" }
    }
  ]
}
```

### 6.7.3 — Runbook

```markdown
# Runbook: QuickTicket High Error Rate

## Alert
- **Fires when:** Gateway 5xx error rate > 5% for 2 minutes
- **Severity:** critical
- **Dashboard:** QuickTicket — Golden Signals (Grafana)

## Diagnosis
1. Check which dependency is failing:
   - `curl -s http://localhost:3080/health | python3 -m json.tool`
   - Look at `checks.payments` / `checks.events` (`ok` / `degraded` / `down`).
2. Hit dependencies directly to localise:
   - `curl -s http://localhost:8082/health`   # payments
   - `curl -s http://localhost:8081/health`   # events
3. Confirm the error class in Prometheus:
   - `sum by (status) (rate(gateway_requests_total{status=~"5.."}[5m]))`
4. Check logs for the failing service:
   - `docker compose logs gateway  --tail=20 --since=5m`
   - `docker compose logs payments --tail=20 --since=5m`

## Common Causes
| Cause | How to identify | Fix |
|-------|-----------------|-----|
| Payments service down | `/health` shows `payments: down`, `/pay` returns 502 | `docker compose start payments` |
| Payments high failure rate | `/health` ok but 5xx in logs | Restart with `PAYMENT_FAILURE_RATE=0.0` |
| Events service down | `/health` shows `events: down` | `docker compose start events` |
| DB connection exhausted | events logs show pool errors | Restart events, check `DB_MAX_CONNS` |

## Escalation
- If not resolved in 10 minutes, escalate to the on-call instructor/TA.
```

### 6.7.4 — Alert firing evidence

```text
$ curl -s -u admin:admin localhost:3000/api/prometheus/grafana/api/v1/rules
QuickTicket High Error Rate -> firing
  alert state: Alerting | value: 1e+00
  summary: Gateway error rate is 5.248117198246171%
  activeAt: 2026-06-24T12:02:10Z
```

Poller trace from injection through firing:

```text
[14:59:54] error_rate=4.77%  high_error_rate_alert=inactive
[15:00:09] error_rate=5.27%  high_error_rate_alert=inactive   <-- crossed 5%
[15:00:24] error_rate=5.34%  high_error_rate_alert=pending    <-- pending starts
[15:01:25] error_rate=5.15%  high_error_rate_alert=pending
[15:02:10] error_rate=5.25%  high_error_rate_alert=firing     <-- FIRED
```

The **SLO Burn Rate** alert also fired afterwards, since the 30-minute window accumulated the
incident's errors:

```text
$ # burn rate = (1 - availability_30m) / (1 - 0.995)
burn rate: 7.57x   (threshold 6x)  -> QuickTicket SLO Burn Rate -> firing
```

### 6.7.5 — Incident timeline

| Time (2026-06-24) | Event |
|-------------------|-------|
| 14:58:28 | Failure injected — `docker compose stop payments` |
| 15:00:09 | Error rate crosses 5% threshold (5.27%) |
| 15:00:24 | `QuickTicket High Error Rate` → **Pending** |
| 15:02:10 | Alert → **Firing** |
| 15:02:40 | Webhook notification received (group_wait 30 s) |
| 15:03:16 | Fix applied — `docker compose start payments` (failure rate 0.0), `/health` healthy |
| 15:05:22 | Alert → **Normal** (error rate decayed below 5%) |

### 6.7.6 — Answer: how long from injection to firing? Why the delay?

> **~3 min 42 s** (injected 14:58:28 → firing 15:02:10). The delay is the sum of three things:
> 1. **Metric window lag (~1.5 min):** the query uses `rate(...[5m])`, a 5-minute window. When
>    payments dies, the failing `/pay` calls are only ~10% of traffic and they enter a window still
>    full of pre-incident successful requests, so the computed error rate ramps up gradually and only
>    crosses 5% around 15:00.
> 2. **Evaluation interval (1 min):** the rule is only evaluated once a minute, so there's up to a
>    minute of quantisation before a breach is even seen.
> 3. **Pending period (2 min):** the `for: 2m` requires the condition to hold continuously for two
>    minutes before firing — this is what suppresses flapping on a transient blip.
>
> This delay is intentional: it trades a few minutes of detection latency for far fewer false alarms.
> A shorter window / pending period would page faster but flap on noise.

---

## Task 2 — Blameless Postmortem (4 pts)

```markdown
# Postmortem: Payments outage caused gateway error-budget burn

**Date:** 2026-06-24
**Duration:** 14:58:28 → 15:05:22 (~6m54s)
**Severity:** SEV-3 (degraded checkout, reads unaffected)
**Author:** Rolan

## Summary
The payments service became unavailable, causing the gateway to return HTTP 502 for every
purchase (`/reserve/{id}/pay`) request. Read traffic (event listing, reservations) was unaffected.
Overall gateway error rate rose to ~5–6% and the 30-minute SLO burn rate peaked at 7.57x, firing
both the critical error-rate alert and the warning burn-rate alert.

## Timeline
| Time | Event |
|------|-------|
| 14:58:28 | Payments service stopped — purchases start returning 502 |
| 15:00:09 | Gateway error rate crosses the 5% SLO alert threshold |
| 15:02:10 | "High Error Rate" alert fires (critical) |
| 15:02:40 | On-call notified via webhook contact point |
| 15:03:16 | Runbook step 1 (`/health`) showed `payments: down`; payments restarted |
| 15:05:22 | Error rate back under threshold; alert resolves to Normal |

## Root Cause
The payments dependency was unavailable. The gateway has **no fallback or degraded checkout path**
for a missing payments service — `/pay` maps any payments connection error straight to a 502. So a
single-dependency outage translates directly into user-visible 5xx for the entire purchase flow and
immediately starts burning the availability error budget.

## What Went Well
- Alert fired within ~3.5 min of the failure and the webhook notification arrived reliably.
- The runbook's first diagnostic step (`/health`) pinpointed the failing dependency immediately.
- Reads stayed healthy — blast radius was limited to the purchase path, not the whole product.

## What Went Wrong
- The 5-minute rate window + 2-minute pending period meant ~3.5 min of customer-visible errors
  before anyone was paged.
- There is no circuit breaker / graceful degradation, so payments being down = hard 502s with no
  "try again later" UX.
- The burn-rate alert reacted *after* the incident was already resolved (30-min window), so it adds
  little for short, sharp incidents.

## Action Items
| Action | Owner | Priority |
|--------|-------|----------|
| Add a graceful-degradation path: queue/retry payment or return a 503 "try later" instead of 502 | gateway team | High |
| Add a fast-burn multi-window burn-rate alert (e.g. 5m/1h) for quicker SLO paging | SRE | High |
| Add a dedicated `payments_up` availability alert independent of overall error rate | SRE | Medium |
| Extend runbook with the `PAYMENT_FAILURE_RATE` partial-failure case | Rolan | Medium |
```

### Answer: most important action item, and why

> **Add graceful degradation for a missing payments dependency.** Detection and runbooks only shorten
> the *response*; they don't reduce *user impact* during the outage. Today a payments outage is a
> 100%-of-checkout 502 storm. If the gateway instead fast-failed with a retryable 503 (or queued the
> charge), the same dependency outage would be a soft degradation rather than a budget-burning
> hard-error incident — turning a SEV-3 into a non-event. It attacks the root cause (no isolation
> between a dependency failure and the user) rather than the symptom (slow paging).

---

## Bonus Task — Second Runbook (Redis down) (2 pts)

A runbook for a **different** failure mode — Redis unavailable, which breaks ticket reservations
(the events service uses Redis to hold reservation state).

```markdown
# Runbook: QuickTicket Reservations Failing (Redis down)

## Alert
- **Fires when:** error rate on `POST /events/{id}/reserve` is elevated, or events `/health` is degraded
- **Severity:** critical

## Diagnosis
1. Check the events dependency view:
   - `curl -s http://localhost:3080/health | python3 -m json.tool`  (look at `checks.events`)
   - `curl -s http://localhost:8081/health`  (events' own view of Redis)
2. Reproduce a reservation:
   - `curl -s -X POST -H 'Content-Type: application/json' -d '{"quantity":1}' http://localhost:3080/events/1/reserve`
   - A 5xx / timeout here points at the reservation store.
3. Check Redis directly:
   - `docker compose exec redis redis-cli ping`   (expect `PONG`)
   - `docker compose ps redis`                    (is it Up / healthy?)
4. Logs:
   - `docker compose logs events --tail=20 --since=5m`  (look for redis connection errors)

## Common Causes
| Cause | How to identify | Fix |
|-------|-----------------|-----|
| Redis container down | `docker compose ps redis` not Up; `redis-cli ping` fails | `docker compose start redis` |
| Redis OOM / evictions | `redis-cli info memory` shows evictions | Increase memory / set eviction policy |
| Network partition | events logs show connection refused/timeout | Restart events after Redis is back |

## Escalation
- If Redis data is lost (reservations gone) after restart, escalate to the on-call instructor/TA —
  in-flight reservations may need manual reconciliation.
```

### Peer test results

> _To be completed after a classmate runs the runbook against an injected Redis outage:_
>
> - **Tester:** _[name]_
> - **Resolved using only the runbook?** _[yes/no]_
> - **Time to resolve:** _[mm:ss]_
> - **Unclear / missing:** _[feedback]_
> - **Runbook update made:** _[what was changed based on feedback]_

---

## PR checklist

```text
- [x] Task 1 done — alerts created (as code), incident simulated, runbook followed, timeline recorded
- [x] Task 2 done — blameless postmortem written
- [~] Bonus Task done — second runbook written; peer-test section pending a classmate
```
