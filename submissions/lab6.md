# Lab 6 — Alerting & Incident Response

## Task 1 — Alerts & Incident Response

**Contact point:** `quickticket-alerts`, type **Webhook**. Instead of webhook.site, a tiny local receiver (`python:3.13-alpine` container on the `app_default` network, `http://webhook-receiver:9000/`) was used, so no data left the machine. Test from Grafana arrived:
```
02:01:02Z status=firing alert=TestAlert severity=None summary=contact point test
```

**Notification policy:** default → `quickticket-alerts`, group by `alertname`, group wait 30s, repeat 5m.

**Alert rules** (Grafana-managed, folder `QuickTicket`, group `quickticket-slo`, eval every 1m):

| Rule | PromQL | Condition | For | Severity |
|------|--------|-----------|-----|----------|
| QuickTicket High Error Rate | `sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100` | > **1** | 2m | critical |
| QuickTicket SLO Burn Rate | `(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)` | > 6 | 5m | warning |

Threshold tuning: under loadgen, `/reserve/{id}/pay` is only ~2.5% of traffic (0.019 of 0.74 req/s), so `PAYMENT_FAILURE_RATE=0.5` gives ~1.3% overall errors. With the suggested 5% the alert would never fire, so the critical threshold was lowered to 1%.

### Runbook: QuickTicket High Error Rate

**Alert**
- Fires when: gateway 5xx rate > 1% for 2 minutes
- Dashboard: QuickTicket — Golden Signals (Error Rate panel)

**Diagnosis**
1. Which dependency is failing: `curl -s localhost:3080/health | python3 -m json.tool`
2. Payments directly (shows injected config): `curl -s localhost:8082/health`
3. Events directly: `curl -s localhost:8081/health`
4. Which endpoint returns 5xx: Prometheus → `sum(rate(gateway_requests_total{status=~"5.."}[5m])) by (path, status)`
5. Logs:
   - `docker compose logs gateway --tail=20 --since=5m`
   - `docker compose logs payments --tail=20 --since=5m`
6. Payments env: `docker compose exec payments env | grep PAYMENT`

**Common causes**
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Payments down | gateway health: `payments: down` | `docker compose start payments` |
| Payments high failure rate | health OK, `failure_rate` > 0, `Payment failed` in logs | Restart payments with `PAYMENT_FAILURE_RATE=0.0` |
| Events down | gateway health: `events: down` | `docker compose start events` |
| DB pool exhausted | events logs show pool errors | Restart events, check `DB_MAX_CONNS` |

All commands run from `app/` with `-f docker-compose.yaml -f ../docker-compose.monitoring.yaml` for restarts.

**Escalation:** not resolved in 10 min → course TA.

### Incident

Failure injected with `PAYMENT_FAILURE_RATE=0.5` while loadgen ran at 3 RPS.

**Alert firing** (Grafana rule state via `/api/prometheus/grafana/api/v1/rules`):
```
QuickTicket High Error Rate | firing | ok | ['1e+00']
QuickTicket SLO Burn Rate   | inactive | ok | ['']
```

**Notifications received by the webhook:**
```
02:13:45Z status=firing   alert=QuickTicket High Error Rate severity=critical summary=Gateway error rate is 1.380175658720201%
02:16:45Z status=resolved alert=QuickTicket High Error Rate severity=critical summary=Gateway error rate is 0.5076142131979696%
```

**Diagnosis (runbook steps 1–6):**
```
gateway  {"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
payments {"status":"healthy","failure_rate":0.5,"latency_ms":0}
events   {"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
payments-1 | "POST /charge HTTP/1.1" 500 Internal Server Error
payments-1 | {"level":"WARNING","service":"payments","msg":"Payment failed (injected) for aa81e45e-..."}
PAYMENT_FAILURE_RATE=0.5
```
→ matches "Payments high failure rate" row.

**Timeline (UTC, 2026-09-26):**
| Time | Event |
|------|-------|
| 02:01:45 | `PAYMENT_FAILURE_RATE=0.5` injected |
| 02:11:18 | Alert → Pending |
| 02:13:15 | Alert → Firing |
| 02:13:45 | Webhook notification received |
| 02:16:45 | Alert auto-resolved (rate dipped below 1%) — failure still active |
| 02:17:49 | Diagnosis started, root cause found (`failure_rate: 0.5`) |
| 02:17:55 | Fix: payments restarted with `PAYMENT_FAILURE_RATE=0.0` |
| ~02:18 | Payments health `failure_rate: 0.0`, alert stays Normal |

**How long from failure injection to alert firing? Why the delay?**
~11.5 minutes. Built-in delay is ~3 min (5m `rate()` window has to fill + 1m evaluation + 2m pending). The rest came from low signal: only ~0.01 failed req/s, so the 5m error ratio hovered around the 1% threshold and needed several minutes of unlucky samples to stay above it for 2 full minutes. The same noise made the alert resolve at 02:16:45 while the failure was still active. The burn-rate alert never fired: ~1.3% errors = ~2.6x burn, below the 6x threshold.

## Task 2 — Blameless Postmortem

# Postmortem: Partial payment failures on QuickTicket

**Date:** 2026-09-26
**Duration:** 02:01:45 → 02:17:55 UTC (16 min)
**Severity:** SEV-3 (checkout degraded, browsing and reservations unaffected)
**Author:** Meliman1000-7

## Summary
The payments service started rejecting 50% of charges. About half of all purchase attempts returned 500 for 16 minutes; overall gateway error rate was ~1.3%. Browsing and reservations worked normally.

## Timeline
| Time (UTC) | Event |
|------|-------|
| 02:01:45 | Payments restarted with `PAYMENT_FAILURE_RATE=0.5`; first failed charges |
| 02:13:15 | High Error Rate alert fired |
| 02:13:45 | Notification delivered to webhook |
| 02:16:45 | Alert auto-resolved while failure was still ongoing |
| 02:17:49 | Investigation started; runbook step 2 showed `failure_rate: 0.5` |
| 02:17:55 | Payments restarted with `PAYMENT_FAILURE_RATE=0.0` |
| ~02:18 | Payments healthy, errors stopped |

## Root Cause
A configuration change raised the payments failure rate to 50%. Nothing validated the value at deploy time, and payments `/health` still reports `healthy` with a non-zero failure rate, so neither health checks nor the gateway circuit breaker reacted. Because charges are only ~2.5% of traffic, the failure was diluted in the global error-rate SLI: alerting on the aggregate ratio detected it late (11.5 min) and flapped back to Normal before the fix.

## What Went Well
- Contact point and routing worked; notification arrived 30s after firing.
- Runbook step 2 identified the cause immediately; fix applied 6s after diagnosis started.
- No data loss: failed charges returned clean 500s, reservations stayed consistent.

## What Went Wrong
- Detection took 11.5 min — longer than the whole fix would need.
- Alert resolved on its own while the incident was ongoing, which could make responders stand down.
- Burn-rate alert (6x) never fired: a sustained 2.6x burn eats the 30-day budget in ~11.5 days and is invisible to paging.
- Default 5% threshold would have missed the incident entirely.

## Action Items
| Action | Owner | Priority |
|--------|-------|----------|
| Add a per-endpoint alert on `/reserve/{id}/pay` 5xx ratio (checkout is the revenue path) | Meliman1000-7 | High |
| Add a slow-burn alert (e.g. 3x over 6h) alongside the 6x fast-burn | Meliman1000-7 | High |
| Add a `keep_firing_for` / longer resolve delay to stop flapping | Meliman1000-7 | Medium |
| Validate `PAYMENT_*` env vars in CI/deploy (reject non-zero failure rate outside chaos tests) | Meliman1000-7 | Medium |
| Runbook: add "check `failure_rate` in payments health" as the first payments step | Meliman1000-7 | Low |

**Most important action item and why:**
The per-endpoint checkout alert. The global error ratio dilutes a 50% checkout failure to ~1.3%, so any global threshold is either too noisy or too blind. Alerting on the path that carries revenue gives a strong, stable signal (~50%) and would have fired in ~3 minutes instead of 11.5.

## Bonus — Cross-Tested Runbook

### Runbook: QuickTicket Reservations Failing

**Alert / symptom**
- `POST /events/{id}/reserve` returns 504 `{"detail":"Events service timeout"}`; High Error Rate may fire
- Browsing (`GET /events`) can still return 200

**Diagnosis** (run from `app/`)
1. Gateway view of dependencies: `curl -s localhost:3080/health` — `events: down` points at events or its datastores
2. Events directly: `curl -s localhost:8081/health` — note: may still report `redis: ok`, don't trust it alone
3. Reproduce: `curl -s -X POST -H 'Content-Type: application/json' -d '{"quantity":1}' localhost:3080/events/1/reserve`
4. Events logs: `docker compose logs events --since=5m | grep -iE "redis|postgres|pool|error"`
5. Datastore containers: `docker compose ps redis postgres`

**Common causes**
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Redis down | logs: `Redis unavailable ... connecting to redis:6379`; `ps` shows redis exited | `docker compose start redis` |
| PostgreSQL down | logs show postgres connection errors; `ps` shows postgres exited | `docker compose start postgres`, then restart events |
| DB pool exhausted | logs show pool timeouts, postgres Up | `docker compose restart events`, check `DB_MAX_CONNS` |

**Verify:** step 3 returns 200/409, gateway health shows `events: ok`.

**Escalation:** not resolved in 10 min → course TA.

### Cross-test results

Not done: no classmate was available to run the runbook blind. The failure mode itself was verified by stopping Redis: reservations returned 504 `Events service timeout`, gateway health showed `events: down`, and events logs showed `Redis unavailable ... connecting to redis:6379`.
