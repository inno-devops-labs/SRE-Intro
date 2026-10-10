# Lab 6. Alerting and incident response: two SLO alerts, one incident, one postmortem

**Student:** Kirill Fadeev
**Email:** ki.fadeev@innopolis.university
**Environment:** WSL2 (kernel 6.18.33.2-microsoft-standard-WSL2, x86_64), Docker Engine 29.7.2, Docker Compose v5.5.1, Prometheus v3.11.2, Grafana 13.0.1, QuickTicket on Docker Compose, webhook.site as the notification receiver. Times are local (UTC+3) unless marked UTC; webhook.site stamps its receipts in UTC.

Every number below comes from a capture file taken during the run. The Grafana objects (contact point, notification policy, both rules) were created through Grafana's alerting HTTP API, the same API the UI calls, with `X-Disable-Provenance: true` so they stay editable in the UI like hand-made rules. Doing it by script made the configuration reproducible and let me read every object back and fail loudly if something was missing. The incident response in 6.6 was also scripted: the script waited for the alert, ran the runbook commands in order, applied the fix and stamped each step. The timeline therefore measures the system (detection, notification, recovery), not my reaction speed, and I say so where it matters.

---

## Task 1. Create alerts and respond to an incident

### 6.1 The stack, and two things it needed before alerting could mean anything

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
./loadgen/run.sh 3 10800 &
```

```plaintext
[20:38:50.768] Prometheus targets
events up http://events:8081/metrics
gateway up http://gateway:8080/metrics
payments up http://payments:8082/metrics
```

**Missing Prometheus config.** The course `main` has no `monitoring/prometheus/prometheus.yml`; the monitoring compose file mounts it, and Docker would create an empty directory in its place. This branch carries the scrape config and recording rules I wrote in Lab 3, plus the rules mount in `docker-compose.monitoring.yaml` and the completed Golden Signals dashboard, so the lab runs from a clean checkout.

**The load generator starves its own payment traffic.** Alert 1 depends on `/pay` requests reaching payments, so I checked that they keep coming. They do not. After 4 minutes of healthy traffic, a quarter of all reservation attempts were already rejected:

```plaintext
gateway requests by path/status, last 5m
  /events                200    597.9   66.6%
  /events/{id}/reserve   200    182.1   20.3%
  /events/{id}/reserve   409     62.1    6.9%
  /reserve/{id}/pay      200     53.7    6.0%

  1 Go Conference 2026     total=100     available=92
  2 SRE Meetup             total=30      available=24
  4 Python Workshop        total=25      available=18
```

The events list says SRE Meetup has 24 free seats while the reserve endpoint answers `409 Not enough tickets` for it. Two definitions of "available" disagree:

- `GET /events/{id}` computes `total - confirmed orders` from PostgreSQL only.
- `POST /reserve` computes `total - confirmed - held`, where `held` is the Redis counter `event:{id}:held`. That counter is written with `redis_client.decrby(key, -quantity)`, which *increments* it, and nothing ever decrements it: not the confirmation, not the 300 s reservation TTL. Every reservation ever made is held forever.

With 0.9 reservations per second the small events are "sold out" within two minutes and the 735 seeded tickets within about fifteen, after which almost no `/pay` requests exist and a payments outage cannot move the error rate at all. For the incident I cleared the orders and raised `total_tickets` to 100000 on all five events (a test-harness change in the database, not a code change), then waited 5 minutes so the rate window held only the new traffic mix:

```plaintext
  /events                200    613.7   67.7%
  /events/{id}/reserve   200    225.3   24.9%
  /events/{id}/reserve   409      0.0    0.0%
  /reserve/{id}/pay      200     67.4    7.4%
```

> An error-rate alert watches a ratio, and the denominator is the traffic that happens to exist. A bug that quietly removes one kind of request from the traffic makes every failure of that kind invisible to the alert, while the dashboard stays green.

### 6.2 Contact point

- **Name:** `quickticket-alerts`
- **Type:** Webhook, `POST` to `https://webhook.site/cf3ffbff-…-c1db239c1caa`

The test button's old API is gone in Grafana 13:

```plaintext
receivers/test: HTTP 410
{"message":"This endpoint has been removed. Please use `/apis/notifications.alerting.grafana.app/v1beta1/namespaces/{namespace}/receivers/{uid}/test` instead."}
```

Through the new endpoint the test went out and arrived:

```plaintext
POST .../receivers/cXVpY2t0aWNrZXQtYWxlcnRz/test: HTTP 200
{"status":"success","duration":"287ms"}
- received_at=2026-09-26 17:32:27 (UTC)  method=POST  status=firing  title='[FIRING:1] TestAlert Grafana '
    alert=TestAlert status=firing severity=None startsAt=2026-09-26T17:32:28.527556067Z
      summary: Notification test
```

### 6.3 Alert rules

Both rules live in folder `QuickTicket`, group `quickticket-slo`, evaluated every 1m. Read back from Grafana:

```plaintext
rule: 'QuickTicket High Error Rate' for=2m labels={'severity': 'critical'} cond=gt [5]
      expr: (sum(rate(gateway_requests_total{status=~"5.."}[5m])) or vector(0)) / sum(rate(gateway_requests_total[5m])) * 100
rule: 'QuickTicket SLO Burn Rate' for=5m labels={'severity': 'warning'} cond=gt [6]
      expr: (1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
group interval: 60s
```

Alert 1 differs from the lab by `or vector(0)`, and that change was forced by the first page this setup ever sent. With the lab's query, a healthy system has no series with a 5xx status yet, the numerator is an empty vector, the division is empty, and Grafana treats the rule as NoData. Its default NoData handling is to notify:

```plaintext
HighErrorRate=inactive/nodata NoData@17:34:10
raw numerator from Prometheus:
{"status":"success","data":{"resultType":"vector","result":[]}}

- received_at=2026-09-26 17:34:44 (UTC)  status=firing  title='[FIRING:1] DatasourceNoData (PBFA97CFB590B2093 QuickTicket A QuickTicket High Error Rate critical)'
    alert=DatasourceNoData status=firing severity=critical startsAt=2026-09-26T17:34:10Z
      summary: Gateway error rate is %
```

A `critical` page with an empty value, two minutes after setup, on a system with zero errors. `or vector(0)` gives the numerator a value when there are no errors. The denominator stays bare on purpose, so "no traffic at all" still ends up as NoData, which is a real signal. After the change the rule went to Normal on its next evaluation and the false page resolved:

```plaintext
[20:43:22.480] warmup  err5m=0.00% ... HighErrorRate=inactive/ok Normal@17:43:10
- received_at=2026-09-26 17:44:44 (UTC)  status=resolved  title='[RESOLVED] DatasourceNoData (...)'
```

The lab's summary annotation `{{ $value }}` also renders poorly in Grafana-managed rules: on the NoData page it printed an empty string, and on real alerts it prints the raw float (`5.340909090909092%`). I kept it as specified and added `error_rate_A: {{ printf "%.2f" $values.A.Value }}`, which gives `5.34`.

Full exported configuration (`/api/v1/provisioning/*/export`), trimmed to the parts that differ between the rules:

```yaml
groups:
    - orgId: 1
      name: quickticket-slo
      folder: QuickTicket
      interval: 1m
      rules:
        - title: QuickTicket High Error Rate
          condition: C
          data:
            - refId: A
              model:
                expr: (sum(rate(gateway_requests_total{status=~"5.."}[5m])) or vector(0)) / sum(rate(gateway_requests_total[5m])) * 100
                instant: true
            - refId: C
              datasourceUid: __expr__
              model: {type: threshold, expression: A, conditions: [{evaluator: {type: gt, params: [5]}}]}
          noDataState: NoData
          execErrState: Error
          for: 2m
          annotations:
            summary: Gateway error rate is {{ $value }}%
            description: Error rate exceeded 5% for 2 minutes. Check payments service health.
            error_rate_A: '{{ printf "%.2f" $values.A.Value }}'
          labels:
            severity: critical
        - title: QuickTicket SLO Burn Rate
          data:
            - refId: A
              model:
                expr: (1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
            - refId: C
              model: {type: threshold, expression: A, conditions: [{evaluator: {type: gt, params: [6]}}]}
          for: 5m
          labels:
            severity: warning
```

> The lab's query is correct arithmetic and still a broken alert. PromQL returns nothing for a counter that has never been incremented, so an error-rate rule has to decide explicitly what "no errors yet" means, or the alerting system decides for it by paging.

### 6.4 Notification policy

```plaintext
policy: receiver=quickticket-alerts group_by=['alertname'] group_wait=30s group_interval=5m repeat_interval=5m
```

`group_interval` was left at its 5m default. It turned out to matter: it is the reason the resolved notification in 6.6 arrived almost five minutes after the rule went back to Normal.

### 6.5 Runbook

<a id="runbook-quickticket-high-error-rate"></a>

#### Runbook: QuickTicket High Error Rate

##### Alert
- **Fires when:** Gateway 5xx error rate > 5% for 2 minutes
- **Dashboard:** QuickTicket, Golden Signals (Error Rate panel)

All commands run from `app/`.

##### Diagnosis
1. Check which service is failing:
   - `curl -s http://localhost:3080/health | python3 -m json.tool`
2. Check payments service directly (note the `failure_rate` and `latency_ms` fields, they are the injected fault knobs):
   - `curl -s http://localhost:8082/health`
3. Check events service:
   - `curl -s http://localhost:8081/health`
4. Check logs for errors:
   - `docker compose logs gateway --tail=20 --since=5m`
   - `docker compose logs payments --tail=20 --since=5m`
5. Find the failing endpoint (added after the incident, see 6.6):
   - `curl -s --get http://localhost:9090/api/v1/query --data-urlencode 'query=sum by (path,status)(rate(gateway_requests_total{status=~"5.."}[5m]))'`
   - 5xx on `/reserve/{id}/pay` means payments; 5xx on `/events/{id}/reserve` means events or its data stores, use the "QuickTicket Reservations Failing" runbook.

##### Common Causes
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Payments service down | health shows payments: down | Restart: `docker compose start payments` |
| Payments high failure rate | health OK, but step 2 shows `failure_rate` > 0 and step 4 shows `Payment failed (injected)` | Redeploy payments with `PAYMENT_FAILURE_RATE=0.0` (stop, then `up -d payments`) |
| Events service down | health shows events: down | Restart: `docker compose start events` |
| Database connection exhausted | events logs show pool errors | Restart events, check DB_MAX_CONNS |

##### Verify recovery
- step 5 shows no 5xx on the endpoint that was failing
- the alert returns to Normal; the resolved notification can lag by up to 5 minutes (policy group interval)

##### Escalation
- If not resolved in 10 minutes, escalate to: course TA

### 6.6 The incident

**Prediction from the code.** Only `/pay` can fail when payments misbehaves. The load generator issues 70 list requests, 20 reservations and 10 reserve-plus-pay flows per 100 iterations, so `/pay` is 10 of 110 requests, about 9.1%. Half of them failing gives about 4.5%, just under the 5% threshold. The measured mix before injection was worse still (7.4% `/pay`, so 3.7%). The lab's own hint says the same. My plan was to watch the lab's injection for 8 minutes as a control, then escalate by killing payments.

**Injection, exactly as the lab says:**

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_FAILURE_RATE=0.5 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

```plaintext
[20:48:09.557] TIMELINE: INJECT payments failure_rate=0.5
[20:48:11.933] payments /health failure_rate=0.5
[20:48:57.098] inject50   err5m=0.34%  burn30m=0.15x  pay5xx_rps=0.051 | HighErrorRate=inactive
[20:50:57.328] inject50   err5m=1.94%  burn30m=0.81x  pay5xx_rps=0.089 | HighErrorRate=inactive
[20:52:57.518] inject50   err5m=5.01%  burn30m=1.96x  pay5xx_rps=0.156 | HighErrorRate=inactive
[20:53:27.564] inject50   err5m=5.32%  burn30m=2.05x  pay5xx_rps=0.156 | HighErrorRate=pending/ok Pending@17:53:10
[20:54:42.684] inject50   err5m=5.14%  burn30m=2.27x  pay5xx_rps=0.089 | HighErrorRate=pending/ok Pending@17:53:10
[20:55:27.748] inject50   err5m=5.23%  burn30m=2.44x  pay5xx_rps=0.133 | HighErrorRate=firing/ok Alerting@17:55:10
[20:55:27.749] reached error=firing after 436s
```

It fired, which I had not expected, and the margin explains why. During the pending period the value never went above 5.35%. Grafana's state history records the transitions with the evaluated value:

```plaintext
Normal   -> Pending   A=5.071759
Pending  -> Alerting  A=5.340909
Alerting -> Normal    A=4.772727
```

The traffic mix for the five minutes before the fix shows where the extra 1.5 percentage points over my prediction came from:

```plaintext
window 5m ending at 20:56:00
  /events                200    595.8   64.5%
  /events/{id}/reserve   200    243.2   26.3%
  /reserve/{id}/pay      200     36.8    4.0%
  /reserve/{id}/pay      500     47.4    5.1%
  total 923.2
```

`/pay` was 9.1% of traffic in this window (7.4% in the five minutes before injection), and 56% of charges failed instead of the configured 50%. Both are ordinary randomness at 85 payment requests per five minutes. With the pre-injection mix and exactly 50% failures the value would have been 3.7% and the alert would have stayed silent. The escalation step was not needed, because the alert fired within the 8-minute window.

**Notification:**

```plaintext
- received_at=2026-09-26 17:55:44 (UTC)  status=firing  title='[FIRING:1] QuickTicket High Error Rate (QuickTicket critical)'
    alert=QuickTicket High Error Rate status=firing severity=critical startsAt=2026-09-26T17:55:10Z
      summary: Gateway error rate is 5.340909090909092%
      error_rate_A: 5.34
      valueString: [ var='A' labels={} type='query' value=5.340909090909092 ], [ var='C' labels={} type='threshold' value=1 ]
```

**Runbook, step by step:**

```plaintext
step 1: gateway health
{ "status": "healthy", "checks": { "events": "ok", "payments": "ok", "circuit_payments": "CLOSED" } }
step 2: payments directly
{"status":"healthy","failure_rate":0.5,"latency_ms":0} HTTP 200
step 3: events
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}} HTTP 200
step 4: logs
payments-1  | {"level":"WARNING","service":"payments","msg":"Payment failed (injected) for e017dc8b-..."}
payments-1  | INFO:     172.19.0.8:54530 - "POST /charge HTTP/1.1" 500 Internal Server Error
step 5 (runbook addition): which endpoint is failing
/reserve/{id}/pay 500 0.158 req/s
```

Every health check was green. Step 1 alone would have ruled payments out. The cause was visible only in the `failure_rate` field of step 2 and in the payments log. That matches the table row "Payments high failure rate: health OK but errors in logs". I made that row more precise and added step 5, because an endpoint breakdown answers "which service" faster than any health endpoint.

(The script's stamp for this step read "payments unavailable"; that wording was written into the script before the run and is wrong. Payments was available and failing half of its charges.)

**Fix and recovery:**

```plaintext
[20:56:02.830] payments /health failure_rate=0.0
[20:56:02.876] TIMELINE: FIX applied, payments failure_rate=0.0
[20:56:03.015] recovery   err5m=5.13% | HighErrorRate=firing/ok Alerting@17:55:10
[20:56:18.038] recovery   err5m=4.77% | HighErrorRate=inactive/ok Normal@17:56:10
[20:56:18.038] reached error=inactive after 15s
- received_at=2026-09-26 18:00:44 (UTC)  status=resolved  title='[RESOLVED] QuickTicket High Error Rate (QuickTicket critical)'
    alert=QuickTicket High Error Rate status=resolved  startsAt=2026-09-26T17:56:10Z endsAt=2026-09-26T17:56:10Z
```

The rule went back to Normal 7 seconds after the fix, although the 5-minute window was still almost entirely made of failing traffic. It had been hovering just above the line, and one evaluation's worth of clean requests pushed it below. An alert that clears one evaluation after the fix, while the window still holds almost five minutes of failures, is flapping, not recovering. With traffic slightly less favourable it would have cycled Pending, Firing, Normal several times during the incident. The resolved notification came 4 min 34 s after Normal: the next `group_interval` flush.

**Timeline**

| Time | Event | Source |
|------|-------|--------|
| 20:48:09 | `PAYMENT_FAILURE_RATE=0.5` injected, payments restarted | script stamp |
| 20:48:57 | first 5xx visible in Prometheus (0.34% on the 5m ratio) | snapshot |
| 20:52:57 | 5m error ratio crosses 5% (5.01%) | snapshot |
| 20:53:10 | rule evaluation: Normal to Pending (A=5.07) | Grafana `activeAt`, state history |
| 20:55:10 | rule evaluation: Pending to Firing (A=5.34) | Grafana `activeAt`, state history |
| 20:55:44 | firing notification received | webhook.site, 17:55:44 UTC |
| 20:55:59 | investigation started (runbook steps 1 to 5) | script stamp |
| 20:56:00 | cause identified: payments `failure_rate=0.5`, 5xx only on `/pay` | script stamp |
| 20:56:02 | fix applied: payments redeployed with `failure_rate=0.0` | script stamp |
| 20:56:10 | rule evaluation: Firing to Normal (A=4.77) | Grafana state |
| 21:00:44 | resolved notification received | webhook.site, 18:00:44 UTC |

The 15 seconds between notification and investigation, and the 3 seconds from investigation to fix, are script times. A person following the same runbook would add minutes there, and those minutes are exactly what the runbook is meant to shorten.

### 6.7 How long from failure injection to alert firing, and why the delay

**7 min 01 s to Firing (20:48:09 to 20:55:10), 7 min 35 s to the notification.** The lab's rule of thumb of about 3 minutes (1m evaluation + 2m pending) assumes the condition becomes true right away. Here it did not, and that part took longest:

| Stage | Duration | Why |
|-------|---------:|-----|
| Injection to 5m ratio above 5% | 4:48 | the first failures reached Prometheus after ~45 s (15 s scrape interval); after that, `rate(...[5m])` averages the new failures with up to five minutes of healthy history. The steady state (5.1% to 5.3%) was barely above the threshold, so the ratio needed almost the whole window before crossing it |
| Wait for next evaluation | 0:13 | the rule group evaluates on minute boundaries (xx:xx:10) |
| Pending period (`for: 2m`) | 2:00 | the condition has to hold for two consecutive minutes |
| Group wait and delivery | 0:34 | `group_wait: 30s`, then the POST to webhook.site |

The window ramp dominates, and its length depends on how far above the threshold the failure sits. A failure at 10x the threshold crosses it in the first seconds and the alert arrives in ~3 minutes. A failure at 1.05x takes nearly the whole window. The dry run in the bonus (Redis down, error ratio heading for 35%) crossed 5% after 2 min 56 s and fired after 5 min 23 s, the slower rise there coming from a traffic collapse described below.

> Detection time is not a property of the alert rule alone. The same rule detects a severe outage in about three minutes and a marginal one in seven, and it would never detect a slightly smaller marginal one. The pending period is the only part of that delay I actually chose.

---

## Task 2. Blameless postmortem

#### Postmortem: Half of ticket payments failed for 8 minutes

**Date:** 2026-09-26
**Duration:** 20:48:09 → 20:56:02 (7 min 53 s of customer impact); alert active 20:55:10 → 20:56:10
**Severity:** SEV-3 (one user journey, purchase, degraded for a subset of attempts; browsing and reservations unaffected)
**Author:** Kirill Fadeev

##### Summary
The payments service was redeployed with a configured failure rate of 50%, and for almost 8 minutes about half of all ticket purchases failed with HTTP 500 at the payment step. 67 purchases failed in total (4.7% of all gateway requests in the window). The critical error-rate alert fired 7 minutes after the change, and the incident was mitigated 53 seconds after the alert fired (19 seconds after the page arrived) by redeploying payments with the failure rate set back to zero.

##### Timeline
| Time | Event |
|------|-------|
| 20:48 | payments redeployed with `PAYMENT_FAILURE_RATE=0.5`; first failed charges at 20:48:57 |
| 20:53 | error-rate alert goes Pending (5.07% against a 5% threshold) |
| 20:55 | alert fires (5.34%); page delivered to the webhook at 20:55:44 |
| 20:55 | investigation started with the High Error Rate runbook |
| 20:56 | root cause identified: all health checks green, payments `/health` reports `failure_rate: 0.5`, 5xx only on `/reserve/{id}/pay` |
| 20:56 | fix applied: payments redeployed with `PAYMENT_FAILURE_RATE=0.0` |
| 20:56 | alert back to Normal; resolved notification delivered at 21:00:44 |

##### Root Cause
A configuration value that controls payment failures was changed at deploy time and nothing between the change and production checked it. The payments service takes `PAYMENT_FAILURE_RATE` from the environment at start-up, and the compose file passes through whatever the deploying shell has set (`${PAYMENT_FAILURE_RATE:-0.0}`). A non-zero value is a legitimate fault-injection setting, so the service started normally, reported itself `healthy`, and the gateway's dependency check stayed `ok`. The failures surfaced only as 500s on the payment endpoint, which carries about 9% of gateway traffic, so the overall error ratio rose to just 5.1 to 5.3%. At that level the 5% error-rate alert needed 7 minutes to fire and, as it turned out, fired only because that particular five-minute window happened to contain slightly more payments and slightly more failures than average. The incident consumed about 0.2% of the 30-day error budget of the 99.5% availability SLO; the 30-minute burn rate peaked at 2.6x.

##### What Went Well
- The alert fired and the page arrived 34 s later with the value in it (`error_rate_A: 5.34`), so the responder knew the size of the problem before opening anything.
- The runbook's "health OK but errors in logs" row matched the situation exactly, and following it identified the cause in under a minute.
- The fix was a redeploy with a known-good value, verified by reading the setting back from `/health` before declaring it done.

##### What Went Wrong
- Detection depended on luck. The failure mode's steady-state error ratio was within 0.35 percentage points of the threshold; with the average traffic mix it would have been 3.7% and no alert would have fired at all, while one purchase in two was failing.
- The aggregate ratio dilutes endpoint failures. A 56% failure rate on the payment endpoint showed up as 5.1% overall. The alert has no view of the endpoint that matters most for revenue.
- Health checks stayed green throughout. Gateway `/health` checks reachability, not correctness, so step 1 of the runbook points away from payments.
- The alert cleared one evaluation after the fix (7 s) while its 5-minute window still held almost five minutes of failures. The same fragility near the threshold that delayed firing also makes it flap.
- The resolved notification arrived 4.5 minutes after recovery because of the notification policy's 5-minute group interval, which leaves the channel showing an open incident that is already over.
- Before the incident, the same error-rate rule had paged `critical` on a healthy system (DatasourceNoData) because its query returned nothing when there were no errors. A page like that trains people to ignore the channel.

##### Action Items
| Action | Owner | Priority |
|--------|-------|----------|
| Add a per-endpoint SLO alert for purchases: `/reserve/{id}/pay` 5xx ratio > 10% for 2m, so payment failures are judged against payment traffic | Kirill Fadeev | High |
| Replace the single-window burn-rate rule with multi-window, multi-burn-rate rules (14.4x over 1h AND 5m; 6x over 6h AND 30m), so severe incidents page fast and pages clear when the short window recovers | Kirill Fadeev | High |
| Reject a non-zero `PAYMENT_FAILURE_RATE` outside a declared chaos window: fail the deploy script, and export the value as a `payments_config_failure_rate` gauge with its own alert | Kirill Fadeev | Medium |
| Keep `or vector(0)` on every ratio alert numerator, and set `noDataState` deliberately per rule | Kirill Fadeev | Medium |
| Lower the notification policy `group_interval` for `severity=critical` to 1m so resolution reaches the channel within a minute | Kirill Fadeev | Low |
| Add the endpoint-breakdown query (runbook step 5) as a panel on the Golden Signals dashboard | Kirill Fadeev | Low |

**The most important action item is the per-endpoint purchase alert.** Every other problem in this incident follows from judging a payment failure against all traffic. That dilution put the steady state a hair above the threshold, which made detection slow (7 min), lucky (it would have missed 3.7%) and unstable (cleared in 7 s). Measured against its own endpoint the same failure is 56% against a 10% threshold: far above the line, detected at the minimum delay, with no flapping. The multi-window burn rate is the long-term fix for the page rules, but it keeps the aggregate denominator and would still underweight the one endpoint that makes money.

---

## Bonus task. Cross-tested runbook

### B.1 Second runbook: Redis unavailable (validated by a dry run before handing it over)

Before giving a runbook to anyone, I ran the failure myself with the draft in hand. Three of my code-based predictions were wrong, and the runbook changed because of them:

| Draft said (from reading the code) | Dry run showed | Change |
|---|---|---|
| reservations fail fast with 500 (`setex` raises) | 504 after 5.0 s: the container's DNS name disappears and events hangs on the lookup until the gateway gives up | symptom rewritten: "504 after about 5 seconds", plus a timed `curl` as step 2 |
| events `/health` names the culprit (`"redis":"down"`) | events `/health` answered `{"postgres":"ok","redis":"ok"}` 5 minutes into the outage | health endpoints demoted to a cross-check with an explicit warning; container state became the decisive step |
| restarting events before Redis leaves it permanently broken | events start-up blocked 12 s on the lookup, Redis came back in that window, `Redis connected` | warning kept (the code path exists) but worded as a risk, not a certainty |

```plaintext
[21:02:47.526] TIMELINE: INJECT redis killed
  HTTP 000 5.002437s            <- client gave up at 5 s
  HTTP 500 0.007758s
[21:03:28.063] list endpoint still works: HTTP 200
[21:05:43.349] redis-down err5m=5.29%  rps=0.69
[21:08:28.649] redis-down err5m=25.58% rps=0.47 | HighErrorRate=firing/ok Alerting@18:08:10
runbook 2, step 2: gateway health
{"status":"degraded","checks":{"events":"down","payments":"ok",...}}  HTTP 503
runbook 2, step 3: events health
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}  HTTP 200
redis.exceptions.ConnectionError: Error -5 connecting to redis:6379. No address associated with hostname.
redis exited Exited (137) 5 minutes ago
```

The request rate fell from 3.1 to 0.5 per second: the load generator is synchronous and every reservation now cost it 5 seconds. Fewer requests per minute is also why this far larger failure took 5 min 23 s to fire. After the fix the error-rate alert cleared at 21:13:10, but at 21:14:10, five minutes after the system had fully recovered, the burn-rate alert fired for the first time in the lab:

```plaintext
- received_at=2026-09-26 18:14:39 (UTC)  status=firing  title='[FIRING:1] QuickTicket SLO Burn Rate (QuickTicket warning)'
      summary: Error budget burn rate is 6.303949461924069x
```

Its 30-minute window still held both outages. It stayed Firing until 21:20:10, eleven minutes after the system had recovered, and cleared only as clean traffic diluted the window below 6x (5.70x at that evaluation). That is the exact behaviour the lecture's multi-window design exists to prevent, and it went into the runbook's "verify recovery" section so that whoever is on call does not reopen a closed incident.

The runbook as handed to the classmate (v1):

#### Runbook: QuickTicket Reservations Failing

##### Alert
- **Fires when:** `QuickTicket High Error Rate` (gateway 5xx > 5% for 2 minutes) and the 5xx are on `POST /events/{id}/reserve`, not on `/reserve/{id}/pay`
- **User impact:** nobody can reserve a ticket, so nobody can buy one. Browsing (`GET /events`) keeps working, so the site looks up at a glance.
- **Typical signature:** reservations return **504 after about 5 seconds** (the gateway timeout), and total request rate on the dashboard drops, because every client waits 5 s per failed reservation.
- **Dashboard:** QuickTicket, Golden Signals (Error Rate and Request Rate panels)

All commands run from the `app/` directory of the repository.

##### Diagnosis
1. Confirm which endpoint is failing. If the 5xx are on `/reserve/{id}/pay`, use the "QuickTicket High Error Rate" runbook instead.
   ```bash
   curl -s --get http://localhost:9090/api/v1/query \
     --data-urlencode 'query=sum by (path, status) (rate(gateway_requests_total{status=~"5.."}[2m]))' | python3 -m json.tool
   ```
2. Reproduce it yourself, with timing:
   ```bash
   curl -s -o /dev/null -w 'HTTP %{http_code} in %{time_total}s\n' -X POST \
     -H 'Content-Type: application/json' -d '{"quantity":1}' http://localhost:3080/events/3/reserve
   ```
   `504` after ~5 s means events hangs on a dependency. An instant `500` means events fails fast (look at the logs in step 4).
3. State of the containers. **This is the decisive check.**
   ```bash
   docker compose ps -a events redis postgres
   ```
4. Events logs, without health and metrics noise:
   ```bash
   docker compose logs events --since=5m | grep -vE 'GET /metrics|GET /health|GET /events' | tail -20
   ```
5. Health endpoints, as a cross-check only: `curl -s http://localhost:3080/health`, `curl -s http://localhost:8081/health`. **Do not trust a green result here**: during testing events reported `"redis":"ok"` while Redis had been dead for 5 minutes.

##### Common Causes
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Redis down | step 3: redis `exited`; step 4: `ConnectionError ... redis:6379` | 1. `docker compose start redis` 2. `docker compose exec redis redis-cli ping` until `PONG` 3. re-run step 2; if it still fails after 15 s, `docker compose restart events` |
| PostgreSQL down | step 3: postgres `exited`; step 4: errors mentioning postgres | 1. `docker compose start postgres` 2. `docker compose exec postgres pg_isready -U quickticket` until `accepting connections` 3. re-run step 2; if it still fails after 30 s, `docker compose restart events` |
| Events process down | step 3: events `exited`; gateway `/health` shows `"events":"down"` | `docker compose start events` |

**Bring the data store back before touching events.** Events connects to Redis once, at start-up, and gives up on Redis for the life of the process if that first connection fails.

##### Verify recovery
- step 2 returns `HTTP 200` in well under a second; gateway `/health` is `healthy`
- the error-rate alert returns to Normal in about 4 to 5 minutes (the 5m window has to drain); the resolved notification can take up to 5 more minutes
- `QuickTicket SLO Burn Rate` may start firing after the fix, because its 30-minute window still holds the outage. It clears on its own; do not reopen the incident if step 2 is green.

##### Escalation
- If not resolved in 10 minutes, escalate to the course TA with the output of steps 1 to 4 and the time the alert fired.

### B.2 Swap and test

Not done. No classmate was available to run the test before the deadline, so the runbook has only been checked by its author (B.1). That is a weaker test than the one the task asks for: I knew the failure, so I could not find out whether the runbook alone leads someone to it. The bonus is therefore incomplete, and I am not claiming it.

---

## Results

| Item | Result |
|------|--------|
| Contact point | webhook to webhook.site, test delivered (Grafana 13 test API; the old one returns 410) |
| Alert 1, High Error Rate | fired at 50% payment failures: 7 min 01 s to Firing, 7 min 35 s to the page, with a margin of 0.07 to 0.35 points over the threshold |
| Alert 1 query | lab version pages `DatasourceNoData` on a healthy system; fixed with `or vector(0)` on the numerator |
| Alert 2, SLO Burn Rate | silent during the 8-minute payments incident (peak 2.6x); fired 5 minutes *after* recovery from the Redis dry run (6.30x) and stayed Firing for 6 minutes on a healthy system |
| Resolution | error-rate rule cleared 7 s after the fix while the window still held the incident; resolved page 4 min 34 s later |
| Runbook | cause found from step 2 and the logs, with every health check green; step 5 (endpoint breakdown) added |
| Test harness | load generator exhausts inventory within ~15 min through a `held` counter that only grows; `available` in the events list disagrees with reserve |
| Bonus | runbook 2 validated by a dry run (three predictions corrected); classmate cross-test not done, bonus not claimed |

What ties the lab together is that each alert was only as good as the traffic and the query underneath it. The error-rate rule paged on zero errors because an absent series is not a zero, fired on a real incident only because one five-minute window held a few more payments than average, and cleared while the incident was still in its window, all for the same reason: the failure was judged against all traffic instead of against the endpoint that failed. The burn-rate rule showed the opposite failure of a single long window, staying quiet during an incident and paging after it was over. The runbook worked, but not in the way it was written. The diagnosis came from the value of one field and one log line, while every health endpoint in the system reported green, and in the Redis dry run one of them reported green about a dead dependency.
