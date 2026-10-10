# Lab 6 — Alerting & Incident Response — Submission

**Student:** jakefish18
**Repo:** https://github.com/jakefish18/SRE-Intro
**Branch:** `feature/lab6`

PR checklist:
```text
- [x] Task 1 done — alerts created, incident simulated, runbook followed
- [x] Task 2 done — blameless postmortem written
- [x] Bonus Task done — second runbook + cross-test (self-administered dry-run)
```

> **Environment notes (reproducible setup):**
> - The whole stack runs via `docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml`.
> - `monitoring/prometheus/prometheus.yml` (a Lab 3 artifact) was missing from the
>   repo, so it is added here — Prometheus scrapes `gateway`/`events`/`payments`.
> - Grafana runs on **http://localhost:3001** (host port 3000 was occupied by an
>   unrelated process; `3000:3000` in compose maps to 3001 here).
> - Alerting is **provisioned as code** in
>   `monitoring/grafana/provisioning/alerting/quickticket-alerts.yaml` and loaded
>   with `POST /api/admin/provisioning/alerting/reload` — so the contact point,
>   notification policy, and both alert rules are reproducible, not click-ops.
> - The webhook contact point points at a tiny local receiver
>   (`host.docker.internal:9099`) that logs every payload — this is the "webhook
>   receiver" the lab suggests (webhook.site equivalent), captured locally as proof.

---

## Task 1 — Create Alerts & Respond to an Incident

### 1. Alert rule PromQL queries (both rules)

**Alert 1 — QuickTicket High Error Rate (critical)** — condition `IS ABOVE 5`, `for: 2m`, eval every `1m`:
```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

**Alert 2 — QuickTicket SLO Burn Rate (warning)** — condition `IS ABOVE 6`, `for: 5m`, eval every `1m`:
```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

Each rule is a 3-node Grafana-managed alert: **A** (Prometheus query) → **B** (Reduce
`last`) → **C** (Threshold), with `C` as the alert condition. Full definitions live in
[`monitoring/grafana/provisioning/alerting/quickticket-alerts.yaml`](../monitoring/grafana/provisioning/alerting/quickticket-alerts.yaml).

Provisioned and verified:
```
$ curl -su admin:admin localhost:3001/api/v1/provisioning/alert-rules
- QuickTicket High Error Rate | for=2m | cond=C | labels={'severity': 'critical'}
- QuickTicket SLO Burn Rate   | for=5m | cond=C | labels={'severity': 'warning'}
```

### 2. Contact point type + evidence of notification received

- **Type:** Webhook (JSON POST), name `quickticket-alerts`, URL
  `http://host.docker.internal:9099/grafana-webhook` (local receiver).
- **Notification policy:** default route → `quickticket-alerts`, `group_by=[alertname]`,
  `group_wait=30s`, `repeat_interval=5m`.

```
$ curl -su admin:admin localhost:3001/api/v1/provisioning/contact-points
- quickticket-alerts  webhook  http://host.docker.internal:9099/grafana-webhook
$ curl -su admin:admin localhost:3001/api/v1/provisioning/policies
receiver=quickticket-alerts group_by=['alertname'] group_wait=30s repeat=5m
```

> Grafana 13 removed the old "Test contact point" REST endpoint (it now lives behind
> the new `notifications.alerting.grafana.app` k8s-style API). Rather than a synthetic
> test, the contact point is verified by the **real firing notification** below — an
> actual webhook delivered when the incident alert fired.

**Webhook payload received when the alert fired** (captured by the local receiver
at `2026-06-28T21:23:30Z`, ~30s after firing — the `group_wait`):
```json
{
  "receiver": "quickticket-alerts",
  "status": "firing",
  "groupLabels": { "alertname": "QuickTicket High Error Rate" },
  "commonLabels": {
    "alertname": "QuickTicket High Error Rate",
    "grafana_folder": "QuickTicket Alerts",
    "severity": "critical"
  },
  "commonAnnotations": {
    "description": "Error rate exceeded 5% for 2 minutes. Check payments service health.",
    "summary": "Gateway error rate is [ var='B' type='reduce' value=36.38 ], [ var='C' type='threshold' value=1 ]%"
  },
  "title": "[FIRING:1] QuickTicket High Error Rate (QuickTicket Alerts critical)",
  "alerts": [
    {
      "status": "firing",
      "labels": { "alertname": "QuickTicket High Error Rate", "severity": "critical" },
      "startsAt": "2026-06-28T21:23:00Z"
    }
  ]
}
```
A matching `[RESOLVED]` webhook (`"status":"resolved"`, `value=0`) was delivered after
recovery. (Grafana renders `{{ $value }}` for a multi-node rule as each expression
node's value — `var='B'`=36.38 is the actual error-rate %, `var='C'`=1 is the
threshold-breach boolean.)

### 3. Runbook (full text)

```markdown
# Runbook: QuickTicket High Error Rate

## Alert
- **Fires when:** Gateway 5xx error rate > 5% for 2 minutes
- **Severity:** critical
- **Dashboard:** QuickTicket — Golden Signals (Grafana → QuickTicket folder)
- **Query:** sum(rate(gateway_requests_total{status=~"5.."}[5m]))
            / sum(rate(gateway_requests_total[5m])) * 100

## Diagnosis
1. Check the aggregate gateway health (which dependency is bad?):
   - `curl -s http://localhost:3080/health | python3 -m json.tool`
     - look at `.checks.payments` and `.checks.events` (`ok` / `degraded` / `down`)
2. Check payments service directly:
   - `curl -s --max-time 3 http://localhost:8082/health`  (connection refused = down)
3. Check events service directly:
   - `curl -s --max-time 3 http://localhost:8081/health`
4. Confirm the error mix in Prometheus (which status codes?):
   - `sum by (status) (rate(gateway_requests_total[2m]))`  (502/504 = downstream; 500 = gateway)
5. Check logs for the failing service:
   - `docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml logs gateway --tail=20`
   - `docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml logs payments --tail=20`

## Common Causes
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Payments service down | /health shows payments: down; :8082 refused; 502s on /reserve/{id}/pay | `docker compose ... start payments` |
| Payments high failure rate | /health payments: ok, but charge errors in logs; 500/502 on pay | Restart with `PAYMENT_FAILURE_RATE=0.0` |
| Events service down | /health shows events: down; 502s on /events and /reserve | `docker compose ... start events` |
| DB connection exhausted | events logs show pool/timeout errors | Restart events; raise `DB_MAX_CONNS` |

## Mitigation / Recovery
- Restore the failing dependency (see Fix column).
- Verify recovery: error rate returns below 5% and alert state → Normal:
  - `curl -s http://localhost:3080/health` → status `healthy`
- Watch the alert flip back to Normal in Grafana (Alerting → Alert rules).

## Escalation
- If not resolved in 10 minutes, escalate to the on-call SRE / course TA (@Naghme98).
- If customer-facing payment loss is suspected, page the payments service owner.
```

### 4. Alert firing evidence (Grafana status "Firing")

Rule state progression observed via `GET /api/prometheus/grafana/api/v1/rules` and
the Alertmanager API (`GET /api/alertmanager/grafana/api/v2/alerts`):

```
21:20:21  inactive   err5m=0.28%      (injection)
21:20:53  pending    err5m≈7%         (condition first breached >5%)
21:21:08  pending    err5m=10.76%
   ...    pending    (held for the 2m `for` period)
21:23:08  FIRING     err5m=38.59%     <-- alert fired
21:23:23  firing     err5m=40.67%
```

```json
// GET /api/alertmanager/grafana/api/v2/alerts  (at firing)
[{ "labels": {"alertname":"QuickTicket High Error Rate","severity":"critical"},
   "status": {"state":"active"},
   "annotations": {"summary":"Gateway error rate is ... value=36.38 ..."},
   "startsAt": "2026-06-28T21:23:00Z" }]
```
Webhook title delivered: `[FIRING:1] QuickTicket High Error Rate (QuickTicket Alerts critical)`.

### 5. Timeline (inject → fire → diagnose → fix → resolve)

All times UTC, 2026-06-28:

| Time | Event |
|------|-------|
| 21:20:21 | **Failure injected** — `payments` stopped; sustained customer checkout attempts (502s) begin |
| 21:20:22 | Runbook diagnosis run: gateway `/health` = `degraded` (`payments: down`); `:8082` connection refused; events healthy → **payments identified as cause** |
| ~21:20:53 | Gateway 5xx error rate crosses 5% → alert state **Pending** |
| 21:23:08 | Alert state **Firing** (after the 2-minute pending period) |
| 21:23:30 | **Webhook notification delivered** (`[FIRING:1]`, severity critical) |
| 21:23:38 | **Fix applied** — `payments` restarted (`PAYMENT_FAILURE_RATE=0.0`) |
| ~21:28:25 | Error rate (5m avg) falls back below 5% |
| 21:29:10 | Alert state back to **Normal** (resolved) |
| 21:33:30 | `[RESOLVED]` webhook delivered |

### 6. How long from failure injection to alert firing? Why the delay?

**~2 minutes 47 seconds** (injected 21:20:21 → fired 21:23:08), i.e. ≈3 minutes.

The delay is *by design*, and is dominated by the alert's **pending period**
(`for: 2m`): the condition must stay true for 2 full minutes before the rule fires,
which suppresses flapping on transient blips. On top of that:
- **Evaluation interval = 1m** — the rule is only checked once a minute, so there is
  up to ~1 min of granularity before the breach is first noticed (state → Pending).
- **`rate(...[5m])` smoothing** — the 5-minute rate window needs a couple of 15s
  scrapes of elevated 5xx before the computed error rate climbs past 5%.

So: `~ (rate ramp, seconds) + (≤1m eval granularity) + (2m pending) ≈ 3 min`. This is
the classic detection-vs-noise trade-off: a shorter `for`/window fires faster but
flaps; a longer one is calmer but slower to page.

---

## Task 2 — Blameless Postmortem

```markdown
# Postmortem: QuickTicket payments outage → elevated gateway error rate

**Date:** 2026-06-28
**Duration:** 21:20:21 → 21:29:10 UTC (~8m49s alert-active; user impact ended at the
21:23:38 fix, ~3m after onset)
**Severity:** SEV-3 (degraded payment flow; reads/reserves unaffected; no data loss)
**Author:** jakefish18

## Summary
The payments service became unavailable. Because the gateway calls payments
synchronously on the purchase path with no working fallback, every checkout
returned HTTP 502. Overall gateway error rate rose to ~9%, breaching the 5% SLO
alert. Browsing and reserving tickets were unaffected.

## Timeline
| Time (UTC) | Event |
|------|-------|
| 21:20:21 | payments service stopped (failure injected); checkout requests start returning 502 |
| 21:20:53 | gateway 5xx error rate crosses the 5% SLO threshold (alert Pending) |
| 21:23:08 | "QuickTicket High Error Rate" alert fires (critical) |
| 21:23:30 | on-call notified via webhook contact point |
| 21:20:22–21:23:38 | investigation: gateway `/health` → `payments: down`; confirmed via `:8082` refused |
| 21:23:38 | root cause confirmed (payments down) and fix applied (payments restarted) |
| 21:29:10 | error rate back to 0; alert resolved (Normal) |

## Root Cause
The payments service was stopped (simulated outage). The systemic cause is that
the gateway's purchase path (`POST /reserve/{id}/pay`) depends on payments
**synchronously with no graceful degradation**: the resilience patterns (retry,
circuit breaker) are no-op stubs in this build (they are implemented in Lab 11),
so a payments outage translates 1:1 into user-facing 5xx. There is a single point
of failure on the critical revenue path and no queue/fallback to absorb a
short payments outage.

## What Went Well
- The SLO-based alert fired automatically within ~3 minutes of the failure.
- The runbook's first diagnosis step (`/health`) pinpointed the culprit
  immediately (`payments: "down"`), no guesswork.
- Reads and reservations kept working — blast radius was limited to checkout.
- Recovery was a single, reversible action (restart payments).

## What Went Wrong
- A single dependency outage directly produced customer-facing failures — no
  circuit breaker / fallback to fast-fail or queue payments.
- 5xx is a coarse signal: the 30m burn-rate alert is too slow to catch a short,
  sharp outage; only the 5m error-rate alert caught it.
- No alerting on the payments service's own `up`/health metric — we detected the
  symptom (gateway 5xx) rather than the cause (payments down).

## Action Items
| Action | Owner | Priority |
|--------|-------|----------|
| Implement the gateway circuit breaker + retry (Lab 11) so payments outages fast-fail / degrade gracefully instead of returning 5xx | gateway owner | High |
| Add a cause-level alert on `up{job="payments"} == 0` (and per-service health) for faster, more precise detection | SRE | High |
| Add a multi-window, multi-burn-rate SLO alert (fast + slow) so short sharp outages page quickly | SRE | Medium |
| Add a payment-latency alert (p95) to catch slow-but-not-down degradations | SRE | Medium |
| Extend the runbook with the "payments high failure rate" (up but erroring) signature | on-call | Low |
```

### Most important action item — and why

**Implement the circuit breaker + graceful degradation on the gateway's payments
path (Lab 11).** Faster/finer alerting (the other items) only shortens *detection*;
it does not reduce *user impact*. The breaker attacks the root cause: today a
single payments outage maps 1:1 to customer-facing 5xx on the revenue path. With a
breaker + fallback, the same outage fast-fails or queues, so checkout degrades
gracefully instead of erroring — turning a SEV-3 into a near-non-event and
shrinking the error budget burn for the *entire class* of payments incidents, not
just this one.

---

## Bonus Task — Cross-Test Runbooks

> A real classmate was not available, so B.2 is performed as a **self-administered
> "cold" dry-run**: a *different* failure (Redis down) is injected and resolved by
> following **only** the second runbook, timing it and noting gaps, then the runbook
> is updated from those findings. (Honest disclosure: this is a proxy for peer
> testing, not a true blind peer test.)

### B.1 — Second runbook (different failure mode: Redis down)

```markdown
# Runbook: QuickTicket Reservation Failures (Redis down)

## Alert
- **Symptom:** Spikes of 5xx on `POST /events/{id}/reserve`; checkout/reserve fails
  while plain event listing (`GET /events`) still works.
- **Dashboard:** QuickTicket — Golden Signals

## Diagnosis
1. Gateway health — narrow it to a service:
   - `curl -s http://localhost:3080/health | python3 -m json.tool`
     - `.checks.events == "down"` while `.checks.payments == "ok"` → the *events*
       dependency chain is the problem (Redis or Postgres).
2. Distinguish Redis vs Postgres at the source (authoritative checks):
   - `docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml exec redis redis-cli ping`
     → no `PONG` / "is not running" = **Redis is down**.
   - `docker compose ... ps` → confirm which container is not `Up`.
   > ⚠️ Do **not** rely on `GET :8081/health` alone — it can still report
   > `redis: "ok"` for a short window after Redis stops (shallow/cached check).
   > Trust `redis-cli ping` + container status instead.
3. Confirm the user-facing symptom:
   - `curl -s -o /dev/null -w '%{http_code}' -X POST -d '{"quantity":1}' \
        -H 'Content-Type: application/json' http://localhost:3080/events/2/reserve`
     → **504** (gateway times out because events hangs on the dead Redis).
   - In Prometheus: `sum by (path,status) (rate(events_requests_total[2m]))` → errors on reserve path.

## Common Causes
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Redis container down | events /health redis: down; `redis-cli ping` fails | `docker compose ... start redis` |
| Redis OOM / evictions | redis up but errors in events logs | Increase maxmemory; check eviction policy |
| Network/timeout to Redis | events logs show REDIS_TIMEOUT_MS errors | Restart events; raise REDIS_TIMEOUT_MS |

## Mitigation / Recovery
- `docker compose ... start redis`
- Verify: `curl -s http://localhost:8081/health` → redis: ok; reserve requests succeed.

## Escalation
- If Redis won't recover in 10 minutes, escalate to on-call; consider failing
  reservations open (read-only catalog) until Redis is restored.
```

### B.2 — Cross-test results (self-administered dry-run)

**Setup:** with the stack healthy, I injected a *different* failure (stopped Redis)
and resolved it following **only** the second runbook, timing the response.

**What happened (captured):**
```
21:40:06Z  INJECT: docker stop app-redis-1
  step 1  gateway /health -> {"status":"degraded","checks":{"events":"down","payments":"ok"}}
  step 2  events  /health -> {"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}   # <-- WRONG signal!
  step 3  redis-cli ping  -> "container ... is not running"                                  # <-- correct signal
  symptom reserve POST    -> HTTP 504 (gateway timeout)
21:40:14Z  FIX: docker start app-redis-1
  verify  events /health  -> redis: ok ; reserve POST -> 409 (normal conflict, service restored)
TIME_TO_RESOLVE ≈ 14s (detect + fix + verify)
```

**Did the runbook work?** Yes — it led to the correct fix (restart Redis) and a
clean recovery in ~14s.

**What was unclear / wrong (the feedback):** the original runbook told the responder
to confirm Redis via `GET :8081/health → checks.redis == "down"`. In the dry-run that
endpoint **still reported `redis: "ok"`** for the first ~seconds after Redis stopped
(shallow/cached check), which would mislead a responder. The reliable signals were
instead: gateway `/health` showing `events: "down"`, a **504** on reserve, and
`redis-cli ping` failing.

**Runbook updated based on feedback:** the Diagnosis section of the second runbook
above was revised to (1) treat `redis-cli ping` + container status as authoritative,
(2) add a ⚠️ warning *not* to trust `:8081/health` alone, and (3) cite the 504 on
`/events/{id}/reserve` as the user-facing symptom. This is exactly the
write → test → discover-gap → fix loop the bonus asks for.
