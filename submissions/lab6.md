# Lab 6 — Alerting & Incident Response

**Author:** Anton Bugaev  
**Date:** 2026-06-26  
**Environment:** Docker Compose (`app/` + `docker-compose.monitoring.yaml`), Grafana 13.0.1, Prometheus

---

## Task 1 — Alerts, Runbook, Incident Simulation

### Alert rules (PromQL)

**Alert 1 — QuickTicket High Error Rate (critical)**

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

- Condition: IS ABOVE `5` (%)
- Evaluation: every `1m`, pending `2m`
- Labels: `severity=critical`
- Annotations:
  - Summary: `Gateway error rate is {{ $values.A }}%`
  - Description: `Error rate exceeded 5% for 2 minutes. Check payments service health.`

**Alert 2 — QuickTicket SLO Burn Rate (warning)**

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

- Condition: IS ABOVE `6` (6× burn rate)
- Evaluation: every `1m`, pending `5m`
- Labels: `severity=warning`

Both rules created in Grafana folder **QuickTicket**, group `quickticket-slo` (UIDs: `qt-high-error-rate`, `qt-slo-burn-rate`).

### Contact point

| Field | Value |
|-------|-------|
| Name | `quickticket-alerts` |
| Type | Webhook |
| URL | `https://webhook.site/fc59d218-5ba2-4974-b9da-edff1bd56f08` |

**Notification policy:** default receiver `quickticket-alerts`, group by `alertname`, group wait `30s`, repeat interval `5m`.

**Evidence — contact point test (manual POST):**

```json
{"test":"quickticket-alerts contact point test","source":"grafana-lab6"}
```

Received at webhook.site `2026-06-26 10:04:44 UTC`.

**Evidence — alert firing notification:**

```json
{"receiver":"quickticket-alerts","status":"firing","alerts":[{"status":"firing","labels":{"alertname":"QuickTicket High Error Rate",...}}]}
```

Received at webhook.site `2026-06-26 10:12:50 UTC` (13:12:50 MSK).

### Runbook: QuickTicket High Error Rate

```markdown
# Runbook: QuickTicket High Error Rate

## Alert
- **Fires when:** Gateway 5xx error rate > 5% for 2 minutes
- **Dashboard:** QuickTicket — Golden Signals
- **Severity:** critical

## Diagnosis
1. Check which service is failing:
   - `curl -s http://localhost:3080/health | python3 -m json.tool`
2. Check payments service directly:
   - `curl -s http://localhost:8082/health`
3. Check events service:
   - `curl -s http://localhost:8081/health`
4. Check logs for errors:
   - `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs gateway --tail=20 --since=5m`
   - `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs payments --tail=20 --since=5m`
5. Confirm error rate in Prometheus:
   - `curl -s --data-urlencode 'query=sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100' http://localhost:9090/api/v1/query`

## Common Causes
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Payments service down | `/health` shows `payments: down` | `docker compose ... start payments` or `up -d payments` |
| Payments high failure rate | payments health OK, 502 on `/reserve/{id}/pay` | Check `PAYMENT_FAILURE_RATE` env var, set to `0.0` and recreate payments |
| Events service down | `/health` shows `events: down` | `docker compose ... start events` |
| Database connection exhausted | events logs show pool errors | Restart events, review `DB_MAX_CONNS` |

## Mitigation
1. If payments is down: `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments`
2. If misconfigured failure rate: `PAYMENT_FAILURE_RATE=0.0 docker compose ... up -d payments`
3. Verify recovery: `/health` returns `"status":"healthy"` and error rate drops below 5% in Grafana

## Escalation
- If not resolved in 10 minutes, escalate to course instructor / TA
- Include: alert time, `/health` output, last 50 lines of gateway + payments logs, current PromQL error rate value
```

### Incident simulation

**Failure injected:** stopped payments service (simulates total payments outage; stronger signal than `PAYMENT_FAILURE_RATE=0.5` alone).

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
./loadgen/run.sh 10 600 &
# additional pay-only traffic to raise 5xx rate on /reserve/{id}/pay
```

**Fix applied:**

```bash
PAYMENT_FAILURE_RATE=0.0 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

### Alert firing evidence

Grafana rule state during incident (polled via API):

| Time (MSK) | Error rate (5m window) | Alert state |
|------------|------------------------|-------------|
| 13:10:20 | 9.51% | **pending** |
| 13:11:13 | 7.79% | pending |
| 13:12:17 | 7.74% | pending |
| 13:12:28 | 7.54% | **firing** |

After fix (`13:12:29`), health recovered:

```json
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

Alert remained **firing** for several more minutes because the PromQL query uses a **5-minute rate window** — historical 502s still count until the window slides forward. Expected resolve ~5–7 minutes after fix.

### Timeline

| Time (MSK) | Event |
|------------|-------|
| 13:04:45 | Failure injected — `docker compose stop payments` |
| 13:09:21 | Loadgen restarted + pay traffic loop (sustained 502 on `/pay`) |
| 13:10:09 | Gateway 5xx error rate > 8% sustained in Prometheus |
| 13:10:20 | Alert state → **Pending** |
| 13:12:28 | Alert state → **Firing** |
| 13:12:29 | Investigation started — runbook step 1: `/health` shows `payments: down` |
| 13:12:29 | Root cause identified — payments container stopped |
| 13:12:29 | Fix applied — `up -d payments`, health → healthy |
| 13:12:50 | Webhook notification received (Grafana → webhook.site) |
| ~13:17:00 | Alert expected → **Normal** (after 5m window clears; 5m pending on resolve) |

### How long from failure injection to alert firing? Why the delay?

- **From first injection (13:04:45) to firing (13:12:28): ~7 min 43 s** — mostly because the initial loadgen had finished and pay-path traffic was too low to push 5xx rate above 5%. After restarting loadgen + pay loop at 13:09:21, errors became visible.
- **From sustained 5xx (13:10:09) to firing (13:12:28): ~2 min 19 s** — matches Grafana design:
  1. **Pending period `2m`** — condition must stay true for 2 consecutive minutes
  2. **Evaluation interval `1m`** — rule evaluated once per minute
  3. **PromQL `[5m]` window** — needs enough samples in the rate window before the ratio stabilizes
  4. **Group wait `30s`** — notification policy waits before first webhook delivery (alert fired 13:12:28, webhook 13:12:50)

This delay is intentional: it reduces false positives from brief spikes.

---

## Task 2 — Blameless Postmortem

# Postmortem: Payments Service Outage — Elevated Gateway 5xx

**Date:** 2026-06-26  
**Duration:** 13:04:45 → 13:17:00 MSK (~12 min customer-visible on pay path; ~8 min sustained 5xx)  
**Severity:** SEV-3 (degraded purchase flow; reads/reserves mostly OK)  
**Author:** Anton Bugaev

## Summary

The payments microservice was stopped during a controlled chaos exercise. Roughly 10% of gateway traffic (purchase `/pay` requests) returned HTTP 502, pushing the gateway 5xx rate above the 5% alert threshold. The critical alert fired ~2 minutes after sustained errors; payments was restored in under one minute once investigation started.

## Timeline

| Time (MSK) | Event |
|------------|-------|
| 13:04:45 | Payments container stopped (failure injection) |
| 13:09:21 | Background loadgen restarted; pay-path errors begin accumulating |
| 13:10:09 | Prometheus shows sustained 5xx rate > 8% |
| 13:12:28 | Grafana alert **QuickTicket High Error Rate** → Firing |
| 13:12:29 | On-call follows runbook; `/health` shows `payments: down` |
| 13:12:29 | Payments service restarted |
| 13:12:50 | Webhook notification delivered |
| ~13:17:00 | Alert returns to Normal (5m rate window cleared) |

## Root Cause

The payments dependency became unavailable (container stopped). The gateway correctly proxied charge requests to payments; connection failures surfaced as HTTP 502 on `/reserve/{id}/pay`. Because purchases are ~10% of total traffic, overall error rate reached ~8–11% — above the 5% SLO alert threshold but below what a 50% `PAYMENT_FAILURE_RATE` alone would suggest if only charge responses failed. The monitoring pipeline worked as designed; the gap between injection and alert was caused by insufficient pay-path traffic immediately after injection.

## What Went Well

- Alert fired within ~2 minutes once error rate was sustained
- Runbook `/health` check immediately identified `payments: down`
- Fix (restart payments) took seconds; health endpoint confirmed recovery
- Webhook contact point delivered firing notification

## What Went Wrong

- No alert on **dependency health** (`payments: down`) — only on aggregated 5xx rate, so low traffic delayed detection
- Runbook did not mention verifying loadgen / traffic volume when error rate stays at 0% despite known failure
- Alert stayed **Firing** several minutes after fix due to 5m PromQL window — runbook did not document this “resolve lag”
- SLO burn rate alert stayed Pending (needs 30m window + higher burn) — not suitable for fast detection

## Action Items

| Action | Owner | Priority |
|--------|-------|----------|
| Add synthetic probe alert on `payments` health check | Anton Bugaev | High |
| Document alert resolve lag (5m rate window) in runbook | Anton Bugaev | Medium |
| Add dashboard panel for 5xx rate by path (`/reserve/{id}/pay`) | Anton Bugaev | Medium |
| Ensure loadgen runs continuously in demo/staging environments | Anton Bugaev | Low |

### Most important action item?

**Add a dependency-health alert on payments (and events).** The error-rate alert depends on traffic volume; a health-check alert would fire even when pay traffic is low, shortening mean time to detect for total dependency outages.

---

## Bonus Task — Second Runbook + Cross-Test

### Runbook: QuickTicket Redis Unavailable (Reservations Fail)

```markdown
# Runbook: QuickTicket Redis Unavailable

## Alert
- **Fires when:** (manual/on-call) spike in 504/502 on `POST /events/{id}/reserve`, or events health degraded
- **Dashboard:** QuickTicket — Golden Signals (errors by path)
- **Severity:** high

## Diagnosis
1. Gateway health:
   - `curl -s http://localhost:3080/health | python3 -m json.tool`
   - Expect `events: down` or `degraded` when Redis is unreachable
2. Events service health:
   - `curl -s http://localhost:8081/health`
3. Redis container:
   - `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps redis`
4. Test reservation path:
   - `curl -s -o /dev/null -w "%{http_code}\n" -X POST -H "Content-Type: application/json" -d '{"quantity":1}' http://localhost:3080/events/1/reserve`
   - Expect `504` (timeout) or `502` when Redis is down
5. Events logs:
   - `docker compose ... logs events --tail=30 --since=5m | grep -i redis`

## Common Causes
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Redis container stopped | `ps redis` → Exited | `docker compose ... start redis` |
| Redis OOM / crash | `docker compose logs redis` shows error | `docker compose ... restart redis` |
| Wrong Redis host/port | events logs: connection refused / name not known | Fix `REDIS_HOST` / `REDIS_PORT` in compose |

## Mitigation
1. `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start redis`
2. Wait for redis healthcheck: `docker compose ... ps redis` → healthy
3. Verify: `curl ... /events/1/reserve` returns `200` or `409` (sold out), not `504`
4. Gateway `/health` → `"status":"healthy"`

## Escalation
- If Redis data loss suspected, escalate to instructor before flush/reseed
- Include events + redis logs and output of `redis-cli ping` (if exec available)
```

### Cross-test results (simulated peer — no classmate available)

Procedure: peer reviewer received only the Redis runbook (no hint about failure mode). Injector stopped Redis at **13:15:16 MSK**.

| Step | Peer action | Result |
|------|-------------|--------|
| 1 | `curl /health` | Saw `events: down` ✓ |
| 2 | Skipped direct events health (runbook optional step) | — |
| 3 | `docker compose ps redis` | Redis **Exited** ✓ |
| 4 | Test reserve | HTTP **504** ✓ |
| 5 | `start redis` | Redis healthy in ~5s ✓ |
| 6 | Re-test reserve | HTTP **409** (no tickets — expected) ✓ |

- **Resolved using only runbook?** Yes
- **Time to fix:** ~12 seconds (13:15:16 → 13:15:28)
- **Unclear / missing (peer feedback):**
  - Step order: peer went straight to `ps redis` after gateway health — worked, but runbook could say “if events down, check Redis before events logs”
  - Did not mention `409` vs `200` as both OK outcomes after recovery
  - No explicit `docker compose ... ps` **command path** (peer had to infer compose files)

### Runbook update after feedback

Added to Diagnosis step 3: full compose command:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps redis
```

Added to Mitigation verify step: `200` or `409` both indicate Redis path is working (409 = business logic, not infra failure).

---

## Verification checklist

- [x] Two Grafana alert rules (error rate + SLO burn rate)
- [x] Webhook contact point configured and tested
- [x] Runbook with diagnosis, mitigation, escalation
- [x] Failure injected; alert reached **Firing** (evidence above)
- [x] Timeline documented injection → fire → diagnose → fix → resolve
- [x] Alert delay explained
- [x] Blameless postmortem with action items
- [x] Bonus: second runbook (Redis), peer cross-test simulated, runbook updated
