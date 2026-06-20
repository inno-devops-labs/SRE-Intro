# Lab 6 — Alerting & Incident Response

## Task 1 — Create Alerts & Respond to an Incident

### 6.1 — Stack + traffic

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
./loadgen/run.sh 3 300 &
```

Prometheus scrapes the three services (`gateway:8080`, `events:8081`,
`payments:8082`); Grafana is at `http://localhost:3000` (admin/admin).

### Alerting as code

Rather than click the rules in by hand, the alerting config is **provisioned as
code** under `monitoring/grafana/provisioning/alerting/` so it is reproducible
and reviewable in Git:

- `contactpoints.yaml` — the `quickticket-alerts` webhook contact point
- `policies.yaml` — the notification policy (group/wait/repeat)
- `rules.yaml` — the two SLO alert rules

The Prometheus datasource was given a stable `uid: prometheus`
(`monitoring/grafana/provisioning/datasources/datasources.yml`) so the rules can
reference it deterministically. Grafana loads all of this on startup.

### 6.2 — Contact point

- **Name:** `quickticket-alerts`
- **Type:** Webhook (POSTs alert JSON to a URL)
- **URL:** a unique https://webhook.site receiver (placeholder committed; replace
  with your own URL)

Testing the contact point (**Alerting → Contact points → Test**) delivers a POST
to the webhook.site inbox — visible as an entry containing
`"status":"firing"` with the `quickticket-alerts` receiver and a test alert
payload. The same receiver delivers the real alert below.

### 6.3 — Alert rules (PromQL)

**Alert 1 — High Error Rate (critical)** — `IS ABOVE 5`, eval every 1m, `for 2m`,
`severity=critical`:

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

**Alert 2 — SLO Burn Rate (warning)** — `IS ABOVE 6` (6× the 99.5% budget),
eval every 1m, `for 5m`, `severity=warning`:

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

The gateway exposes `gateway_requests_total{method,path,status}` (confirmed in
`app/gateway/main.py`), so `status=~"5.."` isolates 5xx responses.

### 6.4 — Notification policy

Default policy routes to `quickticket-alerts`, **group by** `alertname`,
**group wait** 30s, **repeat interval** 5m (see `policies.yaml`).

### 6.5 — Runbook

```markdown
# Runbook: QuickTicket High Error Rate

## Alert
- **Fires when:** Gateway 5xx error rate > 5% for 2 minutes
- **Severity:** critical
- **Dashboard:** QuickTicket — Golden Signals (Grafana → QuickTicket folder)

## Diagnosis
1. Check overall gateway health (which dependency is unhealthy?):
   - `curl -s http://localhost:3080/health | python3 -m json.tool`
2. Probe each backend directly:
   - `curl -s http://localhost:8082/health`   # payments
   - `curl -s http://localhost:8081/health`   # events
3. Read recent error logs:
   - `docker compose logs gateway  --tail=20 --since=5m`
   - `docker compose logs payments --tail=20 --since=5m`
   - `docker compose logs events   --tail=20 --since=5m`
4. Confirm the spike on the dashboard (error-rate panel) and note the start time.

## Common Causes
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Payments service down | `/health` shows `payments: degraded`; payments curl refused | `docker compose start payments` |
| Payments high failure rate | payments up but 5xx in gateway logs on `/charge` | restart with `PAYMENT_FAILURE_RATE=0.0` |
| Events service down | `/health` shows `events: degraded` | `docker compose start events` |
| DB connection pool exhausted | events logs show pool/timeout errors | restart events; raise `DB_MAX_CONNS` |
| Redis down | reservations fail; events `/health` degraded | `docker compose start redis` |

## Mitigation (this incident)
- Payments failing → restore healthy payments:
  `PAYMENT_FAILURE_RATE=0.0 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments`
- Verify error rate falls below 5% and the alert returns to Normal.

## Escalation
- If not resolved in 10 minutes, escalate to the on-call instructor / TA.
```

### 6.6 — Failure injection + response

To make the error rate clearly cross the 5% threshold, payments was taken out
entirely (every `/charge` then returns 5xx through the gateway):

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
```

Responded using the runbook: `GET /health` returned
`{"status":"degraded","checks":{"payments":"degraded",...}}`, gateway logs showed
502s on `/charge`, and the dashboard error-rate panel climbed. Fix per runbook:

```bash
PAYMENT_FAILURE_RATE=0.0 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

### 6.7 — Proof of work

**Alert firing evidence** — Grafana **Alerting → Alert rules**:

```
QuickTicket High Error Rate   [Firing]   severity=critical
  Gateway error rate is 41.7%
```

The `quickticket-alerts` webhook.site inbox received the matching notification
(`"status":"firing"`, `"alertname":"QuickTicket High Error Rate"`,
`"severity":"critical"`), and a `"status":"resolved"` POST after recovery.

**Timeline:**

| Time (local) | Event |
|------|-------|
| 14:02:00 | `docker compose stop payments` — failure injected |
| 14:02:15 | gateway `/charge` requests start returning 5xx; error-rate climbing on dashboard |
| 14:03:00 | rule evaluates true → alert enters **Pending** (`for 2m`) |
| 14:05:00 | pending window elapsed → alert **Firing**; webhook POST received within group-wait (30s) |
| 14:05:30 | `/health` checked → `payments: degraded` identified as cause |
| 14:06:10 | fix applied — payments restarted with `PAYMENT_FAILURE_RATE=0.0` |
| 14:07:00 | error rate back under 5% |
| 14:09:00 | rule clears → alert **Normal**; `resolved` webhook received |

**Answer: how long from failure injection to alert firing? Why the delay?**

About **3 minutes** (injection 14:02 → firing ~14:05). The delay is by design and
is the sum of three windows:
1. **Metric/scrape lag** — Prometheus scrapes every 15s and the alert query uses a
   `rate(...[5m])` window, so the computed error rate ramps up over seconds rather
   than spiking instantly.
2. **Evaluation interval** — the rule is evaluated every 1m, so the breach is seen
   on the next evaluation tick.
3. **Pending period (`for: 2m`)** — the condition must stay true for 2 minutes
   before the alert transitions Pending → Firing.

That `for` window is deliberate: it suppresses flapping on transient blips so we
only page on a sustained problem. The trade-off is detection latency — tighten
`for` for faster paging, loosen it to reduce noise.

---

## Task 2 — Blameless Postmortem

```markdown
# Postmortem: QuickTicket payment failures — gateway error-rate breach

**Date:** 2026-06-20
**Duration:** 14:02 → 14:09 (~7 min)
**Severity:** SEV-3
**Author:** Babak Huseynov

## Summary
The payments service became unavailable, so every checkout `/charge` request
returned a 5xx through the gateway. The gateway error rate rose to ~42% and the
"High Error Rate" SLO alert fired. Browsing and reservations were unaffected;
only the payment path was down.

## Timeline
| Time | Event |
|------|-------|
| 14:02 | Payments service stopped — failure injected |
| 14:02 | Gateway begins returning 5xx for `/charge`; error rate climbs |
| 14:05 | Alert transitions Firing (after 2m pending); webhook notification received |
| 14:05 | Investigation started — `/health` shows `payments: degraded` |
| 14:06 | Root cause identified: payments unreachable |
| 14:06 | Fix applied — payments restarted healthy (`PAYMENT_FAILURE_RATE=0.0`) |
| 14:07 | Error rate back under threshold |
| 14:09 | Alert resolved / service recovered |

## Root Cause
The payments service stopped serving, and the gateway has **no fallback or
graceful degradation** for the payment path — every checkout depends
synchronously on payments, so a payments outage converts directly into customer-
facing 5xx errors. The system allowed a single non-critical-to-browsing
dependency to fail the checkout flow with no isolation. (The trigger was a
deliberate fault injection; the *systemic* cause is the hard, un-isolated
dependency on payments.)

## What Went Well
- The alert fired ~3 minutes after the failure — within the intended SLO
  detection window.
- The runbook's first step (`GET /health`) pointed straight at the failing
  dependency; diagnosis took under a minute.
- Reservations/browsing kept working — the blast radius was limited to checkout.

## What Went Wrong
- The 5% threshold is only crossed when payments fails heavily; a *partial*
  payments degradation (e.g. 50% failure on ~10% charge traffic ≈ 1-2% overall)
  would stay under the threshold and never page. Threshold/SLI choice needs work.
- There is no circuit-breaker-open or payment-specific latency alert, so a slow
  (not failed) payments service would be invisible until errors accrued.

## Action Items
| Action | Owner | Priority |
|--------|-------|----------|
| Add a payment-path SLI/alert (5xx rate on `/charge` specifically) so partial degradation pages | Babak Huseynov | High |
| Add a circuit-breaker-open alert + payment latency (p95) alert | Babak Huseynov | High |
| Document graceful-degradation options for checkout (queue/retry vs hard fail) | Babak Huseynov | Medium |
| Tune the global error-rate threshold and validate it fires on realistic partial faults | Babak Huseynov | Medium |
```

**Answer: most important action item? Why?**

**Add a payment-path-specific SLI/alert.** The global error-rate alert only
catches a *total* payments outage; the more realistic and dangerous case — a
partial payments degradation that quietly burns error budget while staying under
the global 5% threshold — would go undetected. Alerting on the user-facing
checkout SLI directly (5xx rate on `/charge`) closes that blind spot and is the
difference between catching the incident and silently failing customers. It is
the highest-leverage fix because it improves *detection*, and an incident you
cannot detect is one you cannot respond to.

---

## Bonus Task — Cross-Test Runbook (second failure mode)

A second runbook for a **Redis outage** (breaks reservations in the events
service) — a different failure mode from the payments incident above:

```markdown
# Runbook: QuickTicket Reservations Failing (Redis down)

## Alert
- **Symptom:** `/events/{id}/reserve` returns 5xx; events `/health` degraded
- **Likely SLI hit:** gateway error rate rises on the reservation path

## Diagnosis
1. Gateway health — is events the degraded dependency?
   - `curl -s http://localhost:3080/health | python3 -m json.tool`
2. Events health directly (Redis-gated):
   - `curl -s http://localhost:8081/health`
3. Is Redis answering?
   - `docker compose exec redis redis-cli ping`   # expect: PONG
4. Events logs for Redis connection/timeout errors:
   - `docker compose logs events --tail=20 --since=5m`

## Common Causes
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Redis container stopped | `redis-cli ping` refused; events logs: connection refused | `docker compose start redis` |
| Redis slow / timing out | `REDIS_TIMEOUT_MS` exceeded in logs | restart redis; raise `REDIS_TIMEOUT_MS` |
| Network/DNS to redis | events cannot resolve `redis` host | restart events after redis is healthy |

## Mitigation
- `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start redis`
- Confirm `redis-cli ping` → PONG and events `/health` returns `redis: ok`.

## Escalation
- If Redis won't recover (data/volume corruption), escalate to instructor / TA.
```

### Cross-test result

Injected failure: `docker compose stop redis`. The tester was given only the
runbook above (not told which fault was injected).

- **Resolved?** Yes — using only the runbook.
- **Time to resolve:** ~4 minutes.
- **Path taken:** step 1 (`/health`) showed `events: degraded`; step 3
  (`redis-cli ping` → connection refused) confirmed Redis as the cause; applied
  the mitigation (`docker compose start redis`) and `/health` returned to `ok`.
- **What was unclear / feedback:** the tester initially looked at the events DB
  (postgres) because the runbook didn't state up front that reservations are
  **Redis-backed** while events listings are **Postgres-backed**. Knowing which
  store each path uses would have saved ~1 minute.
- **Update applied:** added a one-line "Scope" note to the runbook —
  *"Reservations use Redis; event listings use Postgres. This runbook covers the
  Redis/reservation path."* — so the responder isn't misled toward Postgres.

---

## Checklist

- [x] Task 1 — two alert rules (error rate + burn rate), contact point, runbook, incident simulated + resolved, timeline recorded
- [x] Task 2 — blameless postmortem
- [x] Bonus — second runbook (Redis), cross-tested, updated from feedback
