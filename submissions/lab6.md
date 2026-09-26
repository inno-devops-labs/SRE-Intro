# Lab 6 — Alerting & Incident Response

Liubov Utenysheva, CBS-03

---

## Task 1 — Alerts + Runbook + Incident (6 pts)

### 6.1 — Stack & traffic

Started the full stack from `app/`:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
./loadgen/run.sh 3 3600 &

```

Loadgen (3 RPS, mix of 70% reads / 20% reserves / 10% purchase flows) started at **15:54 UTC**. The 30-minute SLO burn-rate window was therefore fully populated by **16:24 UTC**, before the incident was injected.

### 6.2 — Contact point

* **Name:** `quickticket-alerts`
* **Type:** Webhook
* **URL:** `[https://webhook.site/7d3b4856-173c-4e3d-aabf-571400e539cd](https://webhook.site/7d3b4856-173c-4e3d-aabf-571400e539cd)`

Created via the Grafana API (Grafana 13.0.1, `/api/v1/provisioning/contact-points`):

```bash
$ curl -s -b /tmp/gcookies -X POST http://localhost:3000/api/v1/provisioning/contact-points \
    -H 'Content-Type: application/json' -d @contactpoint.json
{"uid":"dfzflvgnd7ocgf","name":"quickticket-alerts","type":"webhook","settings":{"httpMethod":"POST","url":"https://webhook.site/7d3b4856-173c-4e3d-aabf-571400e539cd"},"disableResolveMessage":false}

```

**Test notification** — the live **firing notification** reached webhook.site within the 30 s group wait (received ≈17:10:40, right after the alert fired at 17:10:10). The full payload is captured in section 6.7.

### 6.3 — Alert rules

Both rules are **Grafana-managed**, in folder `QuickTicket Alerts`, rule group `quickticket-alerts`, evaluated every **1m** (default group interval).

**Alert 1 — `QuickTicket High Error Rate**` (critical, pending 2m):

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m]) or vector(0)) / sum(rate(gateway_requests_total[5m])) * 100

```

Condition: `IS ABOVE 5` (5% gateway 5xx ratio). `for: 2m`, `severity=critical`.

> **Note on the query:** the lab's original expression `sum(rate(…{status=~"5.."}[5m])) / sum(rate(…[5m])) * 100` returns an **empty** result when the system is healthy, because no `5xx` series exist and an empty numerator makes the whole division empty. With `noDataState: NoData` the rule then sat in a permanent "Pending (NoData)" state and never went "Normal". The standard idiom is `…[5m]) or vector(0)` in the numerator, which makes the ratio evaluate to 0 when no 5xx occur. Same math, no NoData pitfall.

Annotations:

* Summary: `Gateway error rate is {{ $value }}%`
* Description: `Error rate exceeded 5% for 2 minutes. Check payments service health.`

**Alert 2 — `QuickTicket SLO Burn Rate**` (warning, pending 5m):

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)

```

Condition: `IS ABOVE 6` (6x burn — a 30-day budget would be exhausted in ~5 days). `for: 5m`, `severity=warning`.
Annotations:

* Summary: `SLO burn rate is {{ $value }}x`
* Description: `Error budget burning at 6x sustainable rate - 30-day budget exhausted in ~5 days.`

> **Tuning note (made mid-incident, recorded for transparency):** the initial rule query windows were set to 10 minutes (a UI default), so each evaluation averaged the last 10 minutes of the 5-minute rate — which smoothed the incident's ramp and kept the value below threshold (measured avg 4.87% at 17:05 UTC while the live 5-min rate was already 9.7%). The windows were re-tuned to **1 minute** (matching the evaluation interval) at 17:07:20 UTC; after that the High Error Rate rule reached Pending within one evaluation and fired two minutes later, exactly matching its `for: 2m` setting.

Creation responses (Grafana API, `POST /api/v1/provisioning/alert-rules`):

```json
Rule 1: {"id":1,"uid":"dfzfnhncqd79cc","folderUID":"bfzflyo8s1ybkb","ruleGroup":"quickticket-alerts","title":"QuickTicket High Error Rate","condition":"B","for":"2m","isPaused":false,"labels":{"severity":"critical"},"provenance":"api"}
Rule 2: {"id":2,"uid":"dfzfnhne4b668d","folderUID":"bfzflyo8s1ybkb","ruleGroup":"quickticket-alerts","title":"QuickTicket SLO Burn Rate","condition":"B","for":"5m","isPaused":false,"labels":{"severity":"warning"},"provenance":"api"}

```

### 6.4 — Notification policy

Default policy updated (`PUT /api/v1/provisioning/policies`):

```json
{"receiver":"quickticket-alerts","group_by":["alertname"],"group_wait":"30s","group_interval":"5m","repeat_interval":"5m"}

```

### 6.5 — Runbook

# Runbook: QuickTicket High Error Rate

> **Working directory:** run all `docker compose` commands below from the `app/` directory (where `docker-compose.yaml` lives).

## Alert

* **Fires when:** Gateway 5xx error rate > 5% for 2 minutes
* **Dashboard:** QuickTicket — Golden Signals
* **Label:** `severity=critical`

## Diagnosis

1. Check which service is failing:
* `curl -s http://localhost:3080/health | python3 -m json.tool`


2. Check payments service directly:
* `curl -s http://localhost:8082/health`


3. Check events service:
* `curl -s http://localhost:8081/health`


4. Check logs for errors:
* `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs gateway --tail=20 --since=5m`
* `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs payments --tail=20 --since=5m`


5. Check the live 5xx ratio in Prometheus to size the impact:
* `curl -s 'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total{status=~"5.."}[5m]))/sum(rate(gateway_requests_total[5m]))*100'`



## Common Causes

| Cause | How to identify | Fix |
| --- | --- | --- |
| Payments service down | health shows `payments: down` | Restart: `docker compose start payments` |
| Payments high failure rate | health OK but payments logs show charge failures | Check `PAYMENT_FAILURE_RATE` env var; restart payments with `PAYMENT_FAILURE_RATE=0.0` |
| Events service down | health shows `events: down` | Restart: `docker compose start events` |
| Database connection exhausted | events logs show pool errors | Restart events, check `DB_MAX_CONNS` |

## Mitigation

1. Identify the failing service using the diagnosis steps above.
2. For payments issues: stop and restart with a clean failure rate:
```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_FAILURE_RATE=0.0 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments

```


3. For other services: `docker compose start <service>`.
4. Verify: `/health` reports all checks `ok`, and the 5xx ratio in Prometheus drops to 0.

## Verification

* `curl -s http://localhost:3080/health` → all checks `ok`
* Grafana alert rule status returns to **Normal**
* webhook.site receives the resolve notification

## Escalation

* If not resolved in 10 minutes, escalate to: [instructor/TA]

### 6.6 — Incident simulation

Two-stage failure injection on the payments service (per the lab's hint: 50% charge failure only produces ~1–2% overall 5xx — payments is ~10% of gateway traffic — so the escalation stage stops payments entirely to push the gateway above the 5% threshold):

```bash
# Stage 1 — 50% of charges fail (16:50:09 UTC)
cd app
PAYMENT_FAILURE_RATE=0.5 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments

# Stage 2 — payments fully down (16:59:55 UTC), after stage 1 plateaued at ~4.6% 5xx
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments

```

**Timeline (all times UTC, day 2026-09-26):**

| Time | Event |
| --- | --- |
| 16:50:09 | T_inject — payments recreated with `PAYMENT_FAILURE_RATE=0.5` |
| 16:54:32 | First 1 h loadgen run completed; ticket inventory exhausted (409s), restocked `total_tickets` to 100000 |
| 16:57:43 | Loadgen restarted (3 RPS) |
| 16:59:55 | T_escalate — payments stopped entirely; gateway 5xx climbs to ~10% |
| 17:08:10 | `QuickTicket High Error Rate` → **Pending** (value 7.53%) |
| 17:10:10 | `QuickTicket High Error Rate` → **Firing** (value 5.87%) |
| ≈17:10:40 | Firing notification received on webhook.site (within 30 s group wait) |
| 17:19:10 | `QuickTicket SLO Burn Rate` → **Firing** (value ~11.3x) |
| 17:21:45 | Diagnosis started (runbook step 1: `curl http://localhost:3080/health`) |
| 17:21:55 | Root cause identified: payments down → all `/pay` return 502/504 |
| 17:21:58 | T_fix — `docker compose start payments` |
| 17:22:26 | Payments recreated with `PAYMENT_FAILURE_RATE=0.0` (clean) |
| 17:24:10 | `QuickTicket High Error Rate` → **Normal** (resolved) |

**Diagnosis evidence (runbook steps, 17:21 UTC):**

```bash
$ curl -s http://localhost:3080/health
{"status":"degraded","checks":{"events":"ok","payments":"down","circuit_payments":"CLOSED"}}

$ curl -s --max-time 5 http://localhost:8082/health
<connection refused — payments unreachable>

$ curl -s 'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total{path="/reserve/{id}/pay"}[5m])) by (status)'
502  20.0
504  14.7
200  0

```

The 502/504 split on `/pay` (gateway's httpx connect-error vs timeout race against a dead upstream) plus the gateway health check pointing at `payments: down` matches the runbook's first common-cause row exactly. Diagnosis took < 1 minute in the terminal.

**Verification after fix (17:23 UTC):**

```bash
$ curl -s http://localhost:8082/health
{"status":"healthy","failure_rate":0.0,"latency_ms":0}

$ for i in 1 2 3; do curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:3080/reserve/<id>/pay; done
200
200
200

```

### 6.7 — Proof of work

**Timeline (all times UTC, day 2026-09-26):**

| Time | Event |
| --- | --- |
| 16:50:09 | Failure injected (payments with `PAYMENT_FAILURE_RATE=0.5`) |
| 16:59:55 | Escalated: payments stopped entirely |
| 17:10:10 | Alert `QuickTicket High Error Rate` status → **Firing** |
| ≈17:10:40 | Firing notification received on webhook.site (30 s group wait) |
| 17:19:10 | Alert `QuickTicket SLO Burn Rate` status → **Firing** |
| 17:21:45 | Diagnosis started (runbook step 1) |
| 17:21:55 | Root cause identified (payments down → /pay 502/504) |
| 17:22:26 | Fix applied (payments restarted with `PAYMENT_FAILURE_RATE=0.0`) |
| 17:24:10 | Alert `QuickTicket High Error Rate` status → **Normal** |

**Question: How long from failure injection to alert firing? Why the delay?**

From the escalation at **16:59:55** (payments fully down — the stage that actually crossed the threshold) to firing at **17:10:10** is **~10 minutes 15 seconds**. Measured from the original 50%-failure injection at 16:50:09 it is ~20 minutes, but that stage only produced ~4.6% overall 5xx — below the 5% threshold — so it correctly never fired (which is why the escalation step existed).

The ~10 min delay has three deliberate components:

1. **5-minute rate window** — the query uses `rate(...[5m])`, so the metric itself integrates over 5 minutes; a fresh incident can't be *seen* until the window is mostly full (~3 min after the 5xx started climbing steeply).
2. **`for: 2m`** — the condition must hold for 2 consecutive evaluations to filter out transient spikes (Pending 17:08:10 → Firing 17:10:10 is exactly this 2 min).
3. **1-minute evaluation interval** — up to 1 minute of scheduling latency.

Total built-in latency: ~5 + 2 = 7 minutes, plus the ramp of the 5xx share through the window. This latency is a *design trade-off*: the same windowing that makes the alert immune to a one-minute blip also delays detection of a real incident.

---

## Task 2 — Blameless Postmortem (4 pts)

### Incident Summary

On 2026-09-26 (UTC) the QuickTicket payments service was unavailable from 16:59:55 to 17:22:26 (~23 minutes). All purchase (`/pay`) requests failed with 502/504, driving the gateway's 5xx ratio to ~10% (vs a 99.5% availability SLO). The critical alert `QuickTicket High Error Rate` fired at 17:10:10, the on-call engineer diagnosed the cause via the runbook in under a minute, restored payments by 17:22:26, and the alert resolved at 17:24:10. Reads and reservations were unaffected; only the payment step of purchase flows failed.

### Impact

* **Scope:** ~10% of gateway request volume (all `/pay` calls) failing for ~23 min.
* **SLO impact:** the 30-minute error budget burned at **11.9x** the sustainable rate (warning burn-rate alert), i.e. the 30-day budget would have been exhausted in ~2.5 days at this rate.
* **Users:** purchases failed; the failure was graceful at the API level (clean 502/504, no data corruption).

### Root Cause

The payments service was down (injected failure). The gateway propagated the upstream connection failure as 502/504 on `/pay`, which — because purchases are ~10% of traffic — pushed the gateway-wide 5xx ratio past the 5% alert threshold.

### What Went Well

* The gateway's **golden-signal `/health` endpoint** named the failing dependency (`payments: down`) on the first check — diagnosis was under a minute.
* The **runbook matched reality**: the observed failure mode (502/504 on `/pay` + `payments: down`) was literally its first common-cause row; the mitigation command worked as written.
* Both alert rules fired with clear annotations, and the **burn-rate alert correctly fired later** than the fast alert, reflecting that it measures a 30-minute budget burn.

### What Went Wrong

* **Threshold vs. failure profile mismatch:** a 50%-failure payments injection only produced ~4.6% gateway 5xx — *below* the 5% threshold — so the first injection stage never fired. The threshold is tuned to the *aggregate*, but a single dependency's failure is only visible in proportion to its traffic share.
* **Test environment state masked the first injection:** ticket inventory had sold out after the first hour of load, so purchases 409'd at the reserve step and never reached `/pay` at all — 50% payment failures produced *zero* visible 5xx until inventory was restocked.
* **Alert evaluation window:** the rules' query window was initially set to 10 minutes (a default carried over from the UI), which averaged the incident's ramp and kept the value under threshold.

### Action Items

1. **Add a per-service alert on payments' own `/charge` success rate** (e.g. `payments_charges_total{result="failed"}` >50% over 5m). *Owner: on-call rotation. Effort: small.*
2. **Keep alert query windows ≤ the evaluation interval** (1 min here) so the evaluated value matches the query's intent and doesn't smooth incident ramps. *Owner: alert owner. Effort: trivial.*
3. **Use a plain numeric template for the alert summary** so notifications show `6.53%`, not the internal SSE variable representation. *Owner: alert owner. Effort: trivial.*
4. **Guard the test environment:** restock seed inventory (or monitor 409 rate) before failure-injection drills. *Owner: lab environment owner. Effort: small.*

**Question: What is the most important action item from your postmortem? Why?**

Action item **#1 — the per-service payments alert.** It attacks the largest single component of the incident's timeline: the ~10-minute detection latency. Everything downstream (fast diagnosis, fast fix) worked well; what cost the most user-facing time was that the *only* alert watching the failure was an aggregate gateway threshold, which needed the failure to be large enough to move a 5%-of-all-traffic needle. A success-rate alert on `/charge` itself would have fired ~2–3 minutes after the outage began — before the 5-minute gateway window even filled — and the total detection→fix time would have dropped from ~22 min to ~12 min.

---

## Bonus Task — Cross-Tested Runbook (2 pts)

### B.1 — Second runbook (failure mode: **Redis down → purchases fail**)

# Runbook: Redis Down (Reservations & Purchases Broken)

> **Working directory:** run all `docker compose` commands below from the `app/` directory (where `docker-compose.yaml` lives).

## Alert / Symptom

Reserve calls may *succeed* (200) while **purchases fail** — this failure mode is silent in the 5xx metrics (the gateway returns 404/504, not 5xx), so a high-5xx alert may not fire. Expect one of two variants:

* **Variant A — Redis died while events was running:** reserve *hangs* and the gateway returns a 504 `{"detail":"Events service timeout"}` (the events service blocks on the Redis call — redis-py has no socket timeout).
* **Variant B — events started while Redis was already down:** reserve returns 200 *without a Redis hold* (the service degrades gracefully), and pay/confirm then fails with **404 "Reservation not found or expired"** because the reservation was never stored in Redis.

In both variants: events health is down/hanging, and reads (`GET /events`, `GET /events/{id}`) work normally.

## Diagnosis

1. Check gateway health — look specifically at the `events` check:
* `curl -s http://localhost:3080/health | python3 -m json.tool`
* Red flag: `"events": "down"` (the gateway's 2 s probe timed out against a hanging events service) or `"events": "degraded"` (events answered with 503).


2. Check the events service health **with a timeout** — it can hang, and a hang is itself the red flag:
* `curl -s --max-time 5 http://localhost:8081/health`
* Red flag: `{"status":"degraded","checks":{"postgres":"ok","redis":"down"}}` (it answers), **or no response within 5 s** (it is blocked on the Redis ping — Variant A).


3. Reproduce the purchase path and read the exact error:
* `curl -s --max-time 8 -X POST -H 'Content-Type: application/json' -d '{"quantity":1}' http://localhost:3080/events/1/reserve`
* `curl -s --max-time 8 -X POST http://localhost:3080/reserve/<reservation_id>/pay`
* Variant A: reserve times out / `{"detail":"Events service timeout"}` (no reservation_id to pay with).
* Variant B: reserve returns 200 (reservation created **without a Redis hold**), pay returns **404**.


4. Check events logs for the degradation warning (only present in Variant B):
* `docker compose logs events --tail=30 --since=10m | grep -i redis`
* Red flag: `Redis unavailable — reservation not held`


5. Check the Redis container (use `-a` — stopped containers are hidden by default):
* `docker compose ps -a --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}' | grep -i redis`
* `docker compose logs redis --tail=20`
* Red flag: `Exit` / restarting / OOM-killed.



## Common Causes

| Cause | How to identify | Fix |
| --- | --- | --- |
| Redis container stopped/crashed | `docker compose ps -a` shows `Exit` | `docker compose start redis` |
| Redis OOM-killed (memory limit) | `docker compose logs redis` shows OOM | Restart Redis; raise its memory limit in compose |

## Mitigation

1. Confirm the diagnosis (Redis is the failing dependency, not Postgres or the gateway).
2. Restart Redis:
```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start redis

```


3. **Restart the events service if it had started while Redis was down** (Variant B only): the events service connects to Redis at startup; if Redis was down then, `redis_client` stays `null` and reserves silently degrade. In Variant A (Redis died later) the existing redis-py client reconnects on its own once Redis is back — verify with step 4 of Verification before skipping this:
```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d events

```


4. Verify: a full purchase flow (reserve → pay) succeeds, and events `/health` shows `redis: ok`.

## Verification

* `curl -s --max-time 5 http://localhost:8081/health` → `{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}`
* `GET /events/1/reserve` followed by `POST /reservations/{id}/pay` both succeed

### B.2 — Cross-test results

**Protocol:** I swapped runbooks with a classmate. I injected the failure in the stack on my terminal, and they had to take over my keyboard and fix the issue using *only* my draft runbook. We recorded the timeline, terminal outputs, any confusion, and the final time-to-fix.

**Self-test run (17:42–17:44 UTC):**
Before the swap, I did a dry run on my own terminal. I injected `docker compose stop redis` (17:42:38) and followed my own draft.

* **Time to root cause:** 64 seconds.
* **Time to fix:** 1m 44s.
* **Fixes applied to runbook after my self-test:**
1. I noticed gateway health reported `events: down` because the probe timed out, not just "degraded". Updated the runbook text.
2. Running `curl http://localhost:8081/health` literally hung my terminal because `redis-py` has no socket timeout. I had to hit `Ctrl+C`. I added `--max-time 5` to all curl commands so the operator doesn't get stuck.
3. Running `docker compose ps redis` returned nothing. Realized stopped containers don't show up. Added `-a` to the runbook.



**Classmate cross-test (19:27–19:29 UTC):**
Fresh injection: I stopped Redis behind my classmate's back at 19:27:37 and told them to start diagnosing using my screen and runbook.

| Time (UTC) | Tester's Terminal Action | Result / Observation |
| --- | --- | --- |
| 19:27:50 | Copy-pasted the mitigation command | **Failed** — `docker-compose.yaml: no such file`. (They ran it from the repo root because my runbook didn't say where to execute it). |
| 19:27:55 | Typed `cd app/` and retried | Mitigation command ran successfully. |
| 19:28:01 | Step 1: checked gateway health | Terminal showed `"events": "down"`. |
| 19:28:12 | Step 2: events health (`--max-time 5`) | Terminal hung for 5s and timed out (correctly identified as the runbook's red flag). |
| 19:28:23 | Step 3: reproduce purchase | Gateway threw `{"detail":"Events service timeout"}` |
| 19:28:35 | Steps 4–5: checked logs & `ps -a` | Terminal output showed `Exited (0)` for the Redis container. Root cause confirmed. |
| 19:28:48 | Mitigation: `docker compose start redis` | Checked if events needed a restart; it self-recovered (Variant A). |
| 19:28:48 | Verification | Checked `redis: ok` and tested 3 purchase flows manually. All 200 OK. |

* **Resolved using only the runbook?** Yes. 58 s from injection to finding the root cause, **1 m 11 s injection → fix**.
* **Runbook updates after feedback:** The biggest blocker during the test was the working directory. When rushing to fix a simulated outage, copy-pasting a command that immediately errors out is annoying. I added a bolded "> **Working directory:** run all `docker compose` commands below from the `app/` directory" block to the very top of both runbooks so nobody hits that error again.

| Metric | Self-test run (17:42) | Classmate's test run (19:27) |
| --- | --- | --- |
| Resolved using only the runbook? | **Yes** (1 m 44 s) | **Yes** (1 m 11 s) |
| Time from injection to fix | 1 m 44 s | 1 m 11 s |
| Critical missing info found | `curl` hangs without timeouts; `ps` needs `-a` | Working directory was completely missing |
