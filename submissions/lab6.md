# Lab 6 — Alerting & Incident Response

Grafana: `http://localhost:3000` (Prometheus datasource from Lab 3). Stack started with:

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
./loadgen/run.sh 3 300 &
```

Loadgen mix is ~70% reads / ~20% reserve / ~10% pay, so `PAYMENT_FAILURE_RATE=0.5` only moves gateway 5xx to ~2%. The High Error Rate rule is therefore **IS ABOVE 2** (not 5) so a real payments-fault injection can fire it; a full payments outage still exceeds 5% and is what we used for the incident.

---

## Task 1 — Alerts, contact point, incident

### Alert rule PromQL

**Alert 1 — QuickTicket High Error Rate** (`severity=critical`)

- Type: Grafana-managed
- Evaluate every **1m**, pending **for 2m**
- Condition: **IS ABOVE 2**

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

Annotations:
- Summary: `Gateway error rate is {{ $value }}%`
- Description: `Error rate exceeded threshold for 2 minutes. Check payments service health.`

**Alert 2 — QuickTicket SLO Burn Rate** (`severity=warning`)

- Evaluate every **1m**, pending **for 5m**
- Condition: **IS ABOVE 6**
- SLO: 99.5% availability (Lab 3). Burn `6` on a 30m window ≈ exhausting a 30-day 0.5% budget in ~5 days.

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

### Contact point

- **Name:** `quickticket-alerts`
- **Type:** Webhook
- **URL:** `https://webhook.site/7c3a9e21-4b8f-4d12-a6e0-91c4f8b2d703`

Notification policy (default):
- Default contact point: `quickticket-alerts`
- Group by: `alertname`
- Group wait: 30s
- Repeat interval: 5m

**Test notification** (Grafana Contact points → Test), webhook.site request:

```
POST /7c3a9e21-4b8f-4d12-a6e0-91c4f8b2d703  200
Host: webhook.site
Content-Type: application/json
Time: 2026-09-27 20:41:18 +0300

{
  "receiver": "quickticket-alerts",
  "status": "firing",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "TestAlert",
        "grafana_folder": "QuickTicket"
      },
      "annotations": {
        "summary": "Notification test from Grafana"
      }
    }
  ],
  "title": "[FIRING:1] TestAlert",
  "state": "alerting"
}
```

**Incident notification** (same URL, High Error Rate):

```
Time: 2026-09-27 21:06:22 +0300
status: firing
labels.alertname: QuickTicket High Error Rate
labels.severity: critical
annotations.summary: Gateway error rate is 8.73%
```

### Grafana alert status (Firing)

Alerting → Alert rules, 21:06:

```
Name                              State     Health   Last evaluation
QuickTicket High Error Rate       Firing    ok       21:06:00  value=8.73
QuickTicket SLO Burn Rate         Pending   ok       21:06:00  value=17.4
```

After fix, 21:12:

```
Name                              State     Health   Last evaluation
QuickTicket High Error Rate       Normal    ok       21:12:00  value=0.12
QuickTicket SLO Burn Rate         Normal    ok       21:12:00  value=0.24
```

---

### Runbook: QuickTicket High Error Rate

## Alert
- **Fires when:** Gateway 5xx error rate > 2% for 2 minutes (Grafana-managed rule; 5% is too high for pay-only failures under the default loadgen mix)
- **Dashboard:** QuickTicket — Golden Signals (`http://localhost:3000`)
- **Severity:** critical

## Diagnosis
1. Check which service is failing:
   - `curl -s http://localhost:3080/health | python3 -m json.tool`
2. Check payments service directly:
   - `curl -s http://localhost:8082/health`
3. Check events service:
   - `curl -s http://localhost:8081/health`
4. Check logs for errors:
   - From `app/`: `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs gateway --tail=20 --since=5m`
   - From `app/`: `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs payments --tail=20 --since=5m`
5. Confirm injected chaos env:
   - `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml exec payments env | findstr PAYMENT`

## Common Causes
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Payments service down | `/health` shows payments down; `curl :8082` fails | `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start payments` |
| Payments high failure rate | health OK but 5xx on `/charge` and gateway pay path | Restart with `PAYMENT_FAILURE_RATE=0.0` |
| Events service down | health shows events: down | Restart events |
| Database connection exhausted | events logs show pool errors | Restart events, check `DB_MAX_CONNS` |

## Escalation
- If not resolved in 10 minutes, escalate to course instructor / TA (`@Naghme98`).

---

### Incident timeline (27 Sep 2026, UTC+3)

| Time | Event |
|------|-------|
| 20:38 | Stack up; loadgen `./loadgen/run.sh 3 300` running; error rate ~0% |
| 20:41 | Contact point test received on webhook.site |
| 21:02:10 | Injected failure: stopped payments, brought it back with `PAYMENT_FAILURE_RATE=0.5` |
| 21:03:40 | PromQL ~2.1% 5xx — **below original 5% threshold**, rule stayed Normal. Stopped payments entirely (`docker compose ... stop payments`) so pay path 100% failed |
| 21:04:05 | Gateway `/health` showed payments down; `curl :8082/health` connection refused |
| 21:06:00 | **QuickTicket High Error Rate → Firing** (value 8.73%). Webhook POST at 21:06:22 |
| 21:06:40 | Followed runbook: logs showed gateway 502/503 on `/reserve/{id}/pay`; payments container `Exited` |
| 21:07:15 | Root cause: payments process not running, not a DB/events issue |
| 21:07:40 | Fix: `PAYMENT_FAILURE_RATE=0.0` `up -d payments` |
| 21:08:05 | `curl :8082/health` → 200; gateway health payments: ok |
| 21:12:00 | Alert **Normal**. SLO burn-rate rule never stayed Firing (5m pending; we recovered first) |

Diagnosis snippets:

```bash
curl -s http://localhost:3080/health
# during incident:
# {"status":"degraded","payments":"down","events":"ok"}

curl -s http://localhost:8082/health
# curl: (7) Failed to connect to localhost port 8082

docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs gateway --tail=5 --since=5m
# POST /reserve/3/pay → 502 Bad Gateway  (payments unreachable)
```

### How long from failure injection to alert firing? Why the delay?

From **full payments stop** at 21:03:40 to **Firing** at 21:06:00 is **2 minutes 20 seconds**.

Grafana evaluates every **1 minute** and requires the condition to hold **for 2 minutes** (pending). The 5m `rate()` window also ramps up as 5xx samples enter the series, so the first evaluation after the stop can still be under threshold. Together that is ~3 minutes from a hard failure to a page — by design, so a single scrape blip does not page.

The earlier `PAYMENT_FAILURE_RATE=0.5` inject at 21:02:10 **never fired** the 5% rule: only ~10% of loadgen traffic hits payments, so 50% charge failures ≈ **2% gateway 5xx**. That is why the rule was tuned to 2% for day-to-day chaos, and why the recorded incident used a payments outage to clear 5% as well.

---

## Task 2 — Blameless postmortem

# Postmortem: QuickTicket payments outage — High Error Rate

**Date:** 2026-09-27  
**Duration:** 21:03:40 → 21:12:00 (alert window); user-visible pay failures 21:03:40 → 21:08:05  
**Severity:** SEV-3 (pay path down; listing/reserve still worked)  
**Author:** Taya Orshulskaya  

## Summary

Payments was stopped in the compose stack while loadgen continued. Gateway turned pay requests into 5xx, Grafana **QuickTicket High Error Rate** fired, and restoring payments with `PAYMENT_FAILURE_RATE=0.0` returned error rate to ~0%. About 8 minutes of pay failures; list/reserve stayed up.

## Timeline

| Time | Event |
|------|-------|
| 21:02:10 | `PAYMENT_FAILURE_RATE=0.5` inject — gateway 5xx ~2%, no page |
| 21:03:40 | Payments container stopped (incident start) |
| 21:06:00 | High Error Rate **Firing** (8.73%); webhook received 21:06:22 |
| 21:06:40 | Runbook: health + logs; payments unreachable |
| 21:07:15 | Cause confirmed: payments not running |
| 21:07:40 | Fix applied: payments up, failure rate 0 |
| 21:12:00 | Alert **Normal**; burn-rate rule never fully fired |

## Root Cause

The gateway treats payments as a hard dependency on the charge path with no graceful degrade (queue, feature flag, or cached “payments unavailable” 503 budget). Stopping that container converts ~10% of mixed traffic into 5xx and burns the **99.5%** availability SLO immediately (observed ~8.7% errors vs 0.5% weekly budget). Alerting on a **5%** gateway-wide threshold also cannot see a 50% payment-fault injection, so the system can lose a large fraction of the **payment** SLO without paging.

## What Went Well
- High Error Rate fired in **2m 20s** after the hard outage, consistent with 1m eval + 2m pending
- Webhook contact point delivered the firing payload (same channel as the Test button)
- Runbook health checks (`:3080/health`, `:8082/health`) isolated payments in under a minute
- Golden Signals dashboard already showed error rate as the first signal (same as Lab 3)

## What Went Wrong
- First inject (`FAILURE_RATE=0.5`) did not page; threshold assumed payment errors dominate gateway `requests_total`
- Burn-rate rule uses a **30m** window and **5m** pending, so it did not reach Firing before recovery — no SLO-budget page for a short incident
- Runbook first draft used `docker compose logs` without the two-file `-f` flags; that fails if you are not in the Lab 3 compose context
- No alert on `up{job="payments"} == 0`, so a dead payments replica is only inferred from gateway 5xx

## Action Items

| Action | Owner | Priority |
|--------|-------|----------|
| Add Grafana alert `payments container down` (`up{job="payments"} == 0` for 1m) | Taya Orshulskaya | High |
| Keep High Error Rate threshold at 2% **or** add a payments-only 5xx ratio alert | Taya Orshulskaya | High |
| Document dual compose files in every runbook command | Taya Orshulskaya | Medium |
| Add a 1h/5m fast-burn SLO alert so short incidents still page on budget | Taya Orshulskaya | Medium |
| Gateway: map payments-down to a dedicated 503 + metric so pay SLO is visible separately from list traffic | Taya Orshulskaya | Low |

### What is the most important action item from your postmortem? Why?

**Page on payments being down (and/or on payment-path error ratio), not only on blended gateway 5xx.** The outage was a dead dependency; the 5% gateway-wide rule is blind to the default loadgen mix. A binary `up` alert plus a pay-route SLI would have fired on the first inject and on a stopped container, without waiting for enough 5xx to dilute across GET `/events`.

---

## Bonus — Cross-tested runbook (Redis down)

Classmate: **Ivan K.** (did not know which failure would be injected). Failure: `docker compose ... stop redis`.

### Runbook: QuickTicket reservations failing (Redis down)

## Alert
- **Fires when:** Users cannot reserve (gateway/events 5xx or 503 on `POST /events/{id}/reserve`); dashboard saturation/errors rise while `/events` GET still works
- **Dashboard:** QuickTicket — Golden Signals

## Diagnosis
1. `curl -s http://localhost:3080/health | python3 -m json.tool`
2. `curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d "{\"quantity\":1}"`
3. `curl -s http://localhost:8081/health`
4. From `app/`: `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps redis`
5. Logs: `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs events --tail=30 --since=5m` — look for Redis connection errors
6. `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml exec redis redis-cli ping` — expect `PONG` if healthy

## Common Causes
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Redis container stopped | `ps` shows redis Exit / unhealthy; `redis-cli ping` fails | `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start redis` |
| Redis unreachable but running | events logs: connection refused / timeout | Check `REDIS_HOST`/`REDIS_PORT`; restart events after redis |
| Postgres down (everything fails) | `/events` GET also fails; postgres unhealthy | Restart postgres, then events — **not this runbook** |

## Escalation
- If redis is up and events still fail after 10 minutes, escalate to TA; collect events logs + `redis-cli ping`.

### Cross-test results

- **Did they resolve it using only the runbook?** Yes. Ivan restarted redis and confirmed `PONG` + reserve `201`/`200`.
- **Time:** **7 minutes 40 seconds** (21:24:00 inject → 21:31:40 reserve OK).
- **Unclear / missing:** (1) Must run compose commands from `app/` with **both** yaml files — first attempt used `docker compose ps redis` in repo root and saw no project. (2) Successful reserve status can be `200` or `201` depending on handler; runbook now says check not-5xx. (3) They wanted a one-liner that is safe if redis is already up (`start` is idempotent; documented).

Runbook above includes that feedback (explicit `app/` + two `-f` files, `ps redis`, status not-5xx).

---

## PR checklist

- [x] Task 1 done — alerts created, incident simulated, runbook followed
- [x] Task 2 done — blameless postmortem written
- [x] Bonus Task done — cross-tested runbook with classmate
