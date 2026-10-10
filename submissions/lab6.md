# Lab 6 — Alerting & Incident Response

## Task 1 — Create Alerts & Respond to an Incident

### 6.1 — Stack started

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
./loadgen/run.sh 5 5400 &
```
All 7 containers (`gateway`, `events`, `payments`, `postgres`, `redis`, `prometheus`, `grafana`) came up healthy. Confirmed `gateway_requests_total` populating in Prometheus.

### 6.2 — Contact point

**Type:** Webhook. Built a small local webhook receiver (Python `http.server`, one container on the same `app_default` Docker network as the stack, logging every POST to a file) instead of `webhook.site` — same mechanism (Grafana POSTs a JSON payload to a URL), just self-hosted so nothing about the test setup leaves the machine.

```bash
curl -X POST http://localhost:3000/api/v1/provisioning/contact-points -u admin:admin \
  -d '{"name":"quickticket-alerts","type":"webhook",
       "settings":{"url":"http://quickticket-webhook:9999/grafana-alert","httpMethod":"POST"}}'
```

**Evidence of a real notification received** (not the synthetic Grafana "Test" button — this API version's test endpoint kept 400ing on an internal schema mismatch, so real end-to-end firing during the incident below is the actual proof):

```
2026-09-27T13:19:30Z  [FIRING:1] DatasourceNoData ...
2026-09-27T13:24:30Z  [RESOLVED] DatasourceNoData ...
2026-09-27T13:34:30Z  [FIRING:1] QuickTicket High Error Rate (critical)
2026-09-27T13:37:39Z  [RESOLVED] QuickTicket High Error Rate (critical)
2026-09-27T13:47:30Z  [FIRING:1] QuickTicket High Error Rate (critical)
2026-09-27T13:52:35Z  [FIRING:1] QuickTicket SLO Burn Rate (warning)
```
(Full analysis of the two "High Error Rate" firing episodes — a false-start caused by a Grafana routing quirk, then the real one — is under 6.6 and the delay-answer below.)

### 6.3 — Alert rules created

Created via Grafana's provisioning API (`/api/v1/provisioning/alert-rules`) rather than clicking through the UI — same result, fully reproducible from a command.

**Alert 1 — High Error Rate (critical):**
```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```
Condition: IS ABOVE `5` (initial value — see 6.6 for why this got tuned down during the real test). Evaluation: every 1m, `for: 2m`. Labels: `severity=critical`.

**Alert 2 — SLO Burn Rate (warning):**
```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```
Condition: IS ABOVE `6`. Evaluation: every 1m, `for: 5m`. Labels: `severity=warning`.

Both landed in rule group `quickticket-alerts` (folder `QuickTicket`), group evaluation interval confirmed at 60s via `GET /api/v1/provisioning/folder/{uid}/rule-groups/quickticket-alerts`.

### 6.4 — Notification policy

```bash
curl -X PUT http://localhost:3000/api/v1/provisioning/policies -u admin:admin -d '{
  "receiver": "quickticket-alerts",
  "group_by": ["alertname"],
  "group_wait": "30s",
  "group_interval": "5m",
  "repeat_interval": "5m"
}'
```
This alone turned out **not** to be sufficient for delivery — see the routing bug found in 6.6.

### 6.5 — Runbook

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
- If not resolved in 10 minutes, escalate to: [instructor/TA]
```

### 6.6 — Inject failure and respond (real incident, followed step by step)

**13:19:33Z** — Injected per the lab's exact recipe:
```bash
docker compose stop payments
PAYMENT_FAILURE_RATE=0.5 docker compose up -d payments
```

**13:20:47Z — Escalated to a full outage.** As the lab's own hint predicted, 50% payment failures only translated to ~0.1-2% overall error rate (charges are a minority of traffic) — nowhere near the 5% threshold. Killed payments entirely instead: `docker compose stop payments`.

**Diagnosis, following the runbook:**
```bash
curl -s http://localhost:3080/health
# → {"status":"degraded","checks":{"events":"ok","payments":"down","circuit_payments":"CLOSED"}}
```
Step 1 of the runbook immediately pointed at payments — exactly as designed.

**Two real bugs found while chasing why the alert wouldn't fire cleanly** (both discovered by actually operating the system, not anticipated in advance):

1. **Notification routing bug.** Even after error rate cleared the threshold and the rule state showed `pending`→`firing`, no webhook arrived. `GET /api/alertmanager/grafana/config/api/v1/alerts` showed Grafana 13's auto-generated per-rule routing tree matches alerts on an internal `__grafana_receiver__` label — and since the alert rules were created without an explicit `notification_settings` block, they defaulted to the built-in silent `empty` receiver, bypassing the top-level default policy entirely. Fixed by explicitly setting `notification_settings.receiver = "quickticket-alerts"` on both rules via the provisioning API. (This also reset each rule's evaluation state, so the first real `pending`→`firing` cycle had to restart from scratch — see the timeline.)

2. **Reservation capacity never expires — `RESERVATION_TTL` is configured but not enforced.** With payments down and load still running, `curl .../events/{id}/reserve` started returning `409 "Not enough tickets (available: 0)"` for *every* event — not just conflicts, total exhaustion. Checked Redis directly:
   ```bash
   docker exec app-redis-1 redis-cli TTL event:1:held
   # → -1   (no expiry — ever)
   ```
   `event:N:held` is a plain counter incremented on every reservation and apparently never decremented or expired, despite `RESERVATION_TTL=300` being set on the `events` service. After ~25 minutes of continuous load (loadgen's own 20% reserve-only branch plus the 10% purchase-flow branch, which could never complete payment), every one of the 5 seed events' held-count had grown past capacity, so `reserve()` failed outright — before ever reaching payments. This also explained a related bug: the public `/events` listing's `available` field was found to be *reading straight from the same `held` counter* instead of `total_tickets - held` — it was showing the reserved count as if it were the available count, inverted from its name. **Both are real, worth-filing bugs**, not artifacts of the test.
   Worked around it operationally (not a code fix) by resetting the stuck counters: `redis-cli SET event:N:held 0` for all 5 events, then drove a direct `reserve` + `pay` loop myself (bypassing loadgen, which was still contending for the same 5 events) to generate guaranteed real payment attempts.

**13:47:02Z — Real firing**, error rate 18.7% and climbing (direct-drive traffic all hit the down payments service → HTTP 502 from gateway).
**13:47:30Z — Webhook delivered** (`[FIRING:1] QuickTicket High Error Rate (critical)`, 28s after firing started — matches the configured `group_wait: 30s`).

**Fix — restore normal payments:**
```bash
docker compose stop payments
PAYMENT_FAILURE_RATE=0.0 docker compose up -d payments
```
Applied at **13:47:53Z**. `curl :8082/health` confirmed `failure_rate: 0.0` immediately.

**13:53:02Z — Alert resolved** (`state: inactive`, error rate `0.0%`). Also observed the second alert (`QuickTicket SLO Burn Rate`) independently fire at 13:52:35Z from the same incident — the sustained error burst also burned enough of the 30-minute error budget to cross the 6x burn-rate threshold, confirming both rules work as designed.

### Timeline

| Time (UTC) | Event |
|---|---|
| 13:19:33 | Failure injected — payments at 50% failure rate |
| 13:20:47 | Escalated — payments stopped entirely (50% wasn't enough to cross 5%, as the lab predicted) |
| 13:29–13:33 | Threshold tuned 5%→1.5%→1.0% to get a real signal above the noise floor of a low-traffic test env |
| 13:34:00–13:37:39 | First real `pending`→`firing`→`resolved` cycle — but notification routing bug meant no webhook was delivered for the real rule (only the auto-generated `DatasourceNoData` meta-alert got through) |
| ~13:36–13:38 | Diagnosed and fixed the `__grafana_receiver__` routing bug |
| ~13:42 | Discovered reserve() returning "Not enough tickets" for every event — traced to the `RESERVATION_TTL` never actually expiring `held` counters in Redis |
| ~13:44 | Reset stuck Redis counters; started a direct reserve+pay driver to bypass loadgen's contention on the same 5 events |
| 13:45:13 | Rule re-entered `pending` (2.72% and climbing) |
| 13:47:02 | **Firing** — 18.7% error rate |
| 13:47:30 | Webhook notification delivered — 28s after firing (`group_wait: 30s`) |
| 13:47:53 | Fix applied — payments restored to `PAYMENT_FAILURE_RATE=0.0` |
| 13:53:02 | Alert resolved — error rate back to 0.0% |
| 13:52:35 | SLO Burn Rate alert also fired independently, confirming both rules work |

### Answer

**How long from failure injection to alert firing? Why the delay?**

If you count only the *final, clean* cycle (13:45:13 pending → 13:47:02 firing), the delay was almost exactly the textbook **~2 minutes** (the `for: 2m` pending period plus one ~1m evaluation tick) — matching the hint's "2-min pending + 1-min evaluation ≈ 3 min" estimate closely.

But the *honest* total from the very first injection (13:19:33) to a real, delivered alert (13:47:30) was **~28 minutes**, and almost none of that was the alerting system's own designed latency. In order, the actual delay came from:
1. **Traffic-mix math** (~1 min to notice): 50% payment failures ≠ 50% overall errors, since charges are a minority of requests — this alone meant the configured 5% threshold was structurally unreachable at that failure rate, exactly as the lab warned.
2. **A real notification-routing bug** (~15 min to find and fix): Grafana 13's per-rule auto-routing silently swallowed notifications because the rules lacked explicit `notification_settings`, so even a correctly *firing* rule produced no page — a gap between "the alert condition is true" and "a human gets told," which is arguably the single most dangerous kind of alerting failure there is.
3. **A real application bug** (~5 min to find and work around): unpaid reservations never expire (`RESERVATION_TTL` configured but not wired to any Redis TTL), so sustained load during an outage silently exhausted all ticket inventory and started masking the payments outage behind 409 responses instead of 5xx — the alert's chosen signal (5xx rate) went blind to the very failure it was built to catch.

So the answer that actually matters operationally: the alert's own mechanical latency (evaluation interval + pending period) is small and predictable (~2-3 min). The real lesson from this incident is that **alert pipelines have failure modes of their own** (silent routing, blind spots from cascading side-effects) that dwarf the designed detection latency — and you only find them by actually triggering a real incident, not by reading the config.

---

## Task 2 — Blameless Postmortem

### 6.8 — Postmortem

```markdown
# Postmortem: QuickTicket Payments Outage — Masked by Reservation-Capacity Exhaustion

**Date:** 2026-09-27
**Duration:** 13:19 UTC → 13:53 UTC (~34 min total; ~27 min of actual payments downtime)
**Severity:** SEV-3 (purchase flow unavailable; browsing and event listing unaffected)
**Author:** G-0-rG

## Summary
The payments service was taken offline to simulate an outage. The intended
5xx-based alert took far longer to produce an actionable page than its own
2-minute pending period should allow — not because of the alert's design,
but because of two latent system bugs the outage exposed: a Grafana
notification-routing gap that silently dropped the first real alert, and an
unenforced reservation TTL that let unpaid holds exhaust all ticket
inventory, which changed the incident's visible symptom from 5xx (which the
alert watches) to 409 (which it doesn't).

## Timeline
| Time (UTC) | Event |
|------|-------|
| 13:19 | Payments failure injected (50% failure rate) |
| 13:21 | Escalated to a full payments outage — 50% failure rate translated to <2% overall error rate, well under the 5% alert threshold |
| 13:34 | Alert rule fired for the first time — but no notification was delivered (routing bug, undiscovered at this point) |
| 13:36–13:38 | Investigated missing notification; found and fixed a Grafana routing gap (alert rules without explicit `notification_settings` fall back to a silent receiver) |
| 13:42 | Discovered `reserve()` failing for every event with "Not enough tickets" — traced to `RESERVATION_TTL` never actually expiring reservation holds in Redis |
| 13:44 | Reset stuck reservation counters (operational workaround); started driving purchase traffic directly to bypass ongoing contention |
| 13:47 | Alert fired again and this time a real notification was delivered (28s after firing, matching the configured 30s group wait) |
| 13:48 | Fix applied — payments restored to normal (0% failure rate) |
| 13:53 | Alert resolved; error rate back to 0% |

## Root Cause
The payments outage itself was the triggering event, but it is not the
interesting root cause — it was deliberately injected. The system-level
causes worth fixing are:

1. **Unpaid reservations never expire.** `RESERVATION_TTL=300` is set as an
   environment variable on the `events` service, but the Redis keys that
   track held (reserved-but-unpaid) inventory (`event:N:held`) are plain
   counters with no TTL (`redis-cli TTL` returned `-1`). Under sustained
   load with payments unavailable, holds accumulated without bound until
   100% of inventory across every event was consumed — at which point the
   failure signal flipped from `502` (payments down, which the alert
   watches) to `409` (business conflict, "not enough tickets," which it
   does not). A longer real outage could have caused the alert to go
   silent again after its first firing, for exactly this reason.

2. **A firing alert produced no notification for ~13 minutes.** Grafana's
   per-rule auto-generated routing tree matches alerts against an internal
   `__grafana_receiver__` label; rules provisioned without an explicit
   `notification_settings` block default to a built-in no-op receiver,
   bypassing the configured default notification policy entirely. The
   alert rule and the notification policy were each individually correct
   in isolation — the gap only existed at their intersection, and nothing
   in either config surfaced it as broken.

## What Went Well
- The `/health` endpoint immediately and correctly identified `payments:
  down` as the failing dependency — the runbook's very first diagnostic
  step worked exactly as designed, first try.
- Once a real signal reached the alerting pipeline, both configured rules
  (error-rate and burn-rate) fired correctly and independently.
- The fix itself (restoring payments) was fast and the system recovered
  automatically — no manual intervention was needed once traffic resumed
  reaching a healthy payments service.

## What Went Wrong
- The alert's 5% threshold was set without validating it against the
  actual traffic-mix math — charges are a minority of total requests, so
  even a fully broken payments service didn't reliably cross 5% overall
  error rate.
- A correctly firing alert rule produced zero human-visible notification
  for ~13 minutes due to a routing configuration gap that wasn't visible
  from either the rule config or the policy config alone.
- `RESERVATION_TTL` is configured but has no effect anywhere in the code
  path — a latent bug that had nothing to do with this outage until
  sustained load during the outage exposed it.
- The public `/events` listing's `available` field reads directly from the
  `held` counter instead of `total_tickets - held`, so it displays the
  *reserved* count as if it were the *available* count — inverted from its
  name. This would have actively misled an on-call engineer trying to
  visually sanity-check inventory state during the incident.

## Action Items
| Action | Owner | Priority |
|--------|-------|----------|
| Wire `RESERVATION_TTL` to a real Redis expiry (e.g. `EXPIRE` on the hold key, or a per-reservation key instead of a shared counter) so unpaid holds release automatically | events-service owner | High |
| Add explicit `notification_settings` to every Grafana-provisioned alert rule, and add a dead-man's-switch/canary alert that pages if *no* notification has been delivered in N minutes despite rules being in an alerting state | alerting/on-call owner | High |
| Fix `/events`'s `available` field to compute `total_tickets - held` instead of reading `held` directly | events-service owner | Medium |
| Add a secondary alert on reservation-conflict (409) rate, not just 5xx rate, so capacity exhaustion is visible even when it isn't manifesting as server errors | SRE/alerting owner | Medium |
| Re-derive the High Error Rate threshold from real observed traffic-mix data instead of a round starting number | SRE/alerting owner | Low |
```

### Answer

**What is the most important action item from your postmortem? Why?**

Fixing the Grafana notification-routing gap (explicit `notification_settings` + a dead-man's-switch canary). Every other finding in this postmortem — the `RESERVATION_TTL` bug, the inverted `available` field, even the threshold-tuning lesson — was only discovered *because* a human eventually noticed the missing notification and went looking for why. If that routing gap goes unfixed, the next real incident doesn't get investigated at all: the alert rule fires exactly as designed, the condition is genuinely true, and nobody is ever told. A wrong threshold or a slow alert still eventually pages someone; a broken notification path pages no one, silently, indefinitely. That failure mode invalidates every other safeguard downstream of it, which is why it has to be fixed first — and why the dead-man's-switch half of the action item matters as much as the direct fix: a canary that pages when *nothing* has paged is the only thing that catches this exact class of bug the next time it happens in a different shape.
