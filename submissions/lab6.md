# Lab 6 Submission — Alerting & Incident Response

## Task 1 — Create Alerts & Respond to an Incident

### 6.1: Stack running + loadgen

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
./loadgen/run.sh 3 600 &
```

> **Note (NixOS):** host PostgreSQL already bound port 5432, so I used a one-off compose override (`ports: !override ["5433:5432"]`) locally. Internal service DNS is unchanged.

All 7 services up. Grafana at http://localhost:3000 (admin/admin).

---

### 6.2: Contact point

| Setting | Value |
|---------|-------|
| **Name** | `quickticket-alerts` |
| **Type** | Webhook |
| **URL** | `https://webhook.site/e4099138-35dc-411e-8581-7c3806d4e560` |

**Test evidence** — manual POST and Grafana firing notification both received:

```
2026-06-26 20:22:28  POST  {"test":"QuickTicket contact point manual test","source":"lab6-setup"}
2026-06-26 20:39:15  POST  {"receiver":"quickticket-alerts","status":"firing","alerts":[{"status":"firing","labels":{"alertname":"QuickTicket High Error Rate",...
```

Webhook inbox: https://webhook.site/#!/e4099138-35dc-411e-8581-7c3806d4e560

---

### 6.3: Alert rules (PromQL)

**Alert 1 — QuickTicket High Error Rate (critical)**

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

- Condition: IS ABOVE `5`
- Evaluation: every 1m, pending 2m
- Labels: `severity=critical`

**Alert 2 — QuickTicket SLO Burn Rate (warning)**

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

- Condition: IS ABOVE `6`
- Evaluation: every 1m, pending 5m
- Labels: `severity=warning`

---

### 6.4: Notification policy

| Setting | Value |
|---------|-------|
| Default contact point | `quickticket-alerts` |
| Group by | `alertname` |
| Group wait | 30s |
| Repeat interval | 5m |

---

### 6.5: Runbook — QuickTicket High Error Rate

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
- If not resolved in 10 minutes, escalate to: course instructor / TA
```

---

### 6.6: Incident simulation

**Injection attempt 1** — lab recipe with 50% payment failures:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_FAILURE_RATE=0.5 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

Error rate plateaued at ~0.6% — below the 5% threshold (charges are only ~10% of traffic). As the lab hint says, threshold tuning is part of the job.

**Injection attempt 2 (effective)** — stopped payments entirely:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
```

**Runbook execution output:**

```
$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "degraded",
    "checks": {
        "events": "ok",
        "payments": "down",
        "circuit_payments": "CLOSED"
    }
}

$ curl -s http://localhost:8082/health
(payments unreachable)

$ curl -s http://localhost:8081/health
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
```

**Fix applied:**

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_FAILURE_RATE=0.0 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

```
$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

---

### 6.7: Proof of work

#### Alert firing evidence

```
$ curl -s -u admin:admin 'http://localhost:3000/api/prometheus/grafana/api/v1/rules' | ...
Group: quickticket-slo interval: 60
 - QuickTicket High Error Rate | state: firing | health: ok
 - QuickTicket SLO Burn Rate   | state: inactive | health: ok
```

Grafana UI: **Alerting → Alert rules → QuickTicket High Error Rate → Firing**

Webhook notification arrived at **20:39:15 UTC** with `alertname=QuickTicket High Error Rate`, `severity=critical`.

#### Timeline

| Time (local) | Event |
|--------------|-------|
| 23:22:39 | Failure injected: `PAYMENT_FAILURE_RATE=0.5` |
| 23:27:53 | Escalated injection: payments container stopped |
| 23:36:47 | Alert entered **Pending** state |
| 23:38:58 | Alert **Firing** |
| 23:39:15 | Webhook notification received |
| 23:39:32 | Investigation started (runbook step 1) |
| 23:39:45 | Root cause identified: payments down; fix applied |
| 23:40:02 | Alert **Normal** / resolved |

#### How long from failure injection to alert firing? Why the delay?

From the **effective** injection (payments stopped at **23:27:53**) to alert firing (**23:38:58**) = **~11 minutes**.

Reasons for the delay:

1. **5-minute rate window** — PromQL uses `rate(...[5m])`, so the error percentage ramps up gradually as failed payment requests replace healthy ones in the window. Payment traffic is only ~10% of total gateway traffic, so it took several minutes to cross 5%.
2. **2-minute pending period** — the rule must stay above threshold for 2 continuous minutes before firing.
3. **1-minute evaluation interval** — Grafana evaluates the rule group once per minute.
4. **Loadgen gap** — the first loadgen run (600s) ended around 23:30, briefly starving the rate query (NaN / NoData), which reset the pending timer once. Restarting loadgen at 23:38 kept metrics flowing.

The lab's `PAYMENT_FAILURE_RATE=0.5` alone would likely **never** fire this alert — a good lesson in aligning alert thresholds with actual traffic mix.

---

## Task 2 — Blameless Postmortem

# Postmortem: QuickTicket Gateway Elevated 5xx Error Rate

**Date:** 2026-06-26  
**Duration:** 23:27 → 23:40 (~13 min)  
**Severity:** SEV-3  
**Author:** Stefan

## Summary

The payments service became unavailable during a deliberate chaos exercise, causing the gateway to return 5xx errors on ~10% of requests (the purchase flow). Grafana's High Error Rate alert fired and a webhook notification was delivered. Service was restored by restarting payments with `PAYMENT_FAILURE_RATE=0.0`; no customer data was lost (lab environment).

## Timeline

| Time | Event |
|------|-------|
| 23:22 | Initial failure injected (`PAYMENT_FAILURE_RATE=0.5`) — error rate too low to page |
| 23:28 | Payments container stopped entirely — gateway `/health` shows `payments: down` |
| 23:37 | Gateway 5xx rate crosses 5% threshold in Prometheus |
| 23:39 | Grafana alert fires; webhook notification delivered |
| 23:39 | On-call follows runbook — health check pinpoints payments |
| 23:39 | Payments restarted with `PAYMENT_FAILURE_RATE=0.0` |
| 23:40 | Gateway healthy; alert resolves |

## Root Cause

The payments microservice was stopped as part of a failure injection. The gateway proxies all `/pay` requests to payments; when that dependency is unreachable, the gateway returns 502/503 responses. Because purchases represent ~10% of synthetic load, the overall gateway error rate climbed above the 5% alert threshold after the 5-minute PromQL window absorbed enough failures.

A contributing factor: the initial injection (`PAYMENT_FAILURE_RATE=0.5`) did not produce enough aggregate errors to trigger the alert, masking the dependency failure for several minutes and delaying the incident response exercise.

## What Went Well

- Alert eventually fired and webhook notification arrived with correct labels and annotations
- Runbook health-check steps quickly isolated payments as the failing dependency
- Fix (restart payments) was straightforward and recovery was under 1 minute once diagnosed

## What Went Wrong

- ~11 minutes from effective failure to alert — too slow for a payments outage
- Alert threshold (5% gateway errors) is misaligned with the traffic mix; payment-only failures are diluted by read traffic
- Loadgen stopping mid-incident caused a brief metrics gap (NoData), resetting alert pending state
- Runbook did not mention checking `docker compose ps` to see if the container itself was stopped vs. unhealthy

## Action Items

| Action | Owner | Priority |
|--------|-------|----------|
| Add a dedicated **payments availability** alert (`up{job="payments-metrics"} == 0`) | Stefan | High |
| Lower gateway error-rate threshold or add payment-path-specific error alert | Stefan | High |
| Update runbook: add `docker compose ps payments` and `PAYMENT_FAILURE_RATE` check | Stefan | Medium |
| Keep loadgen running during incidents or use longer-running traffic in chaos drills | Stefan | Low |

### Most important action item?

**Add a payments availability alert** — the gateway error-rate alert is a lagging, diluted signal. A direct `up` or health-check alert on the payments service would have paged within 1–2 minutes of the container stopping, regardless of traffic mix. Error-rate alerts are useful for gradual degradation; dependency-down needs a fast, targeted signal.

---

## Bonus Task — Cross-Tested Runbook (Redis Down)

### Second runbook

```markdown
# Runbook: QuickTicket Redis Unavailable

## Alert
- **Fires when:** Reservation failures spike or events service reports Redis errors
- **Dashboard:** QuickTicket — Golden Signals (events latency / 5xx)

## Diagnosis
1. Check gateway health (reservations go through events → Redis):
   - `curl -s http://localhost:3080/health | python3 -m json.tool`
2. Check events service health:
   - `curl -s http://localhost:8081/health | python3 -m json.tool`
3. Test a reservation end-to-end:
   - `curl -s -o /dev/null -w "status=%{http_code}\n" -X POST -H "Content-Type: application/json" -d '{"quantity":1}' http://localhost:3080/events/1/reserve`
4. Check Redis container:
   - `docker compose ps redis`
   - `docker compose logs events --tail=20 --since=5m | grep -i redis`
5. Ping Redis directly:
   - `docker compose exec redis redis-cli ping` (expect `PONG`)

## Common Causes
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Redis container stopped | `docker compose ps redis` shows Exited | `docker compose start redis` |
| Redis OOM / crash | redis logs show crash | `docker compose restart redis` |
| Network partition | events logs: "Error connecting to redis" | restart redis + events |
| TTL eviction under load | slow reserves, redis memory maxed | check `redis-cli info memory` |

## Escalation
- If not resolved in 10 minutes, escalate to: course instructor / TA
```

### Peer test results

**Tester:** Alex (classmate) — did **not** know the injected failure beforehand.

| Metric | Result |
|--------|--------|
| Resolved using only runbook? | **Yes** — in ~20 seconds |
| Time to diagnose | ~15s (steps 1–4) |
| Time to fix | ~5s (`docker compose start redis`) |

**What Alex found unclear:**

- Step 2 (`events /health`) still returned `"redis": "ok"` even with Redis stopped — the health endpoint does a shallow check and logged a warning but reported OK. Alex had to rely on step 3 (reserve returned **504**) and step 4 (redis container **Exited**).
- Missing explicit note that gateway `/health` shows `events: down` when Redis fails (not just events internal health).

### Runbook update (based on feedback)

Added to Diagnosis section:

> **⚠️ Known quirk:** `events /health` may report `redis: ok` while Redis is actually down. Always verify with `docker compose ps redis` and a live reserve test (step 3).

---

## PR Checklist

```text
- [x] Task 1 done — alerts created, incident simulated, runbook followed
- [x] Task 2 done — blameless postmortem written
- [x] Bonus Task done — cross-tested runbook with classmate
```
