# Lab 6 — Alerting & Incident Response

**Branch:** `feature/lab6`
**Stack:** docker compose — Grafana 13.0.1, Prometheus v3.11.2, QuickTicket gateway/events/payments
**Alerting configured via:** Grafana Alerting Provisioning API (`/api/v1/provisioning/...`), not the UI
**Contact point:** webhook → `https://webhook.site/81976ab3-901f-4b4b-960a-63cab94395ad`
**Raw terminal output:** `raw.log`, section `=== LAB 6 ===`

- [x] Task 1 — alerts created, incident simulated, runbook followed
- [x] Task 2 — blameless postmortem written
- [ ] Bonus Task — cross-tested runbook with classmate

---

## Task 1 — Create Alerts & Respond to an Incident

### 6.0 Two prerequisites the lab text does not mention

**`monitoring/prometheus/` is not on `main`.** Same situation as Lab 5's `k8s/`:
labs are submitted as PRs that stay open, so `main` is still a clean mirror of
upstream and the Prometheus config written in Lab 3 lives only on `feature/lab3`.
But `docker-compose.monitoring.yaml` mounts it:

```yaml
    volumes:
      - ../monitoring/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
```

Without the file Docker creates a *directory* at that path and Prometheus refuses to
start. Both files are promoted onto this branch as the first commit of the lab:

```
$ git ls-tree -r --name-only feature/lab3 -- monitoring/prometheus
monitoring/prometheus/prometheus.yml
monitoring/prometheus/rules.yml
```

**The load generator exhausts the ticket inventory, which silently removes the
failure signal.** This is the Lab 3 bug resurfacing, not a new finding — `events/main.py`
holds a reservation in Redis with a TTL but increments the availability counter
without one:

```python
218:            redis_client.setex(f"reservation:{reservation_id}", RESERVATION_TTL, ...)   # expires
224:            redis_client.decrby(f"event:{event_id}:held", -quantity)                    # never expires
```

`held` therefore ratchets upward forever. The seed file grants 100/30/500/25/80
tickets; at 3 rps the generator burns through all of them, after which every reserve
returns 409 and the purchase flow in `loadgen/run.sh` skips the payment call entirely:

```bash
        RES_ID=$(echo "$RESERVE_RESP" | grep -o '"reservation_id":"[^"]*"' | ...)
        if [ -n "$RES_ID" ]; then ... "$GATEWAY/reserve/$RES_ID/pay" ; else STATUS="409" ; fi
```

Measured before the fix — ten minutes of traffic, **zero** payment requests:

```
$ curl -s :9090/api/v1/query --data-urlencode 'query=sum by (path,status) (increase(gateway_requests_total[10m]))'
/events                      200    73.1
/events/{id}/reserve         409    22.0
/events/{id}/reserve         200     6.6
/reserve/{id}/pay            200     0.0     <-- nothing to break
```

With no traffic reaching payments, killing payments produces no 5xx and the alert can
never fire. Fixed in the test fixture only, no application code touched:

```
$ docker exec app-postgres-1 psql -U quickticket -d quickticket -c "UPDATE events SET total_tickets = 1000000;"
UPDATE 5
$ docker exec app-redis-1 sh -c "redis-cli --scan --pattern 'event:*:held' | xargs -r redis-cli del"
5
```

After the fix 409s drop to zero and the payment path carries ~6% of requests.

### 6.1 Stack up — and why none of this was done in the UI

```
$ cd app/
$ docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
$ ./loadgen/run.sh 3 5400 &
```

The lab describes Alerting as a sequence of UI clicks. Grafana exposes the whole of it
over HTTP, so everything below — contact point, both alert rules, evaluation interval
and notification policy — was created with `curl` against the Alerting Provisioning API
using Basic Auth. Verified against this instance before committing to the approach:

```
$ curl -s -o /dev/null -w "%{http_code}\n" -u admin:admin :3000/api/v1/provisioning/contact-points
200
$ curl -s -o /dev/null -w "%{http_code}\n" -u admin:admin :3000/api/v1/provisioning/alert-rules
200
$ curl -s -u admin:admin :3000/api/v1/provisioning/policies
{"receiver":"empty","group_by":["grafana_folder","alertname"]}
```

Every request carries `X-Disable-Provenance: true`. Without it Grafana marks the objects
as externally provisioned and they become read-only in the UI — which would make the
result *less* faithful to the lab, not more.

> **One thing the API would not do.** The "Test" button on a contact point has no working
> API in Grafana 13. The documented route `/api/alertmanager/grafana/config/api/v1/receivers/test`
> answers **410 Gone**; its replacement `/apis/notifications.alerting.grafana.app/v1beta1/
> namespaces/default/receivers/{name}/test` exists in the resource list and in the OpenAPI
> document, but that document declares no request body, and all six body shapes tried
> returned `400 unknown integration type: ''`. Rather than fall back to a click, §6.2 proves
> delivery end to end instead, which is stronger evidence than the Test button anyway.

### 6.2 Contact point + proof it actually delivers

```
$ curl -s -u admin:admin -H 'Content-Type: application/json' -H 'X-Disable-Provenance: true' \
    -X POST :3000/api/v1/provisioning/contact-points -d '{
      "name":"quickticket-alerts",
      "type":"webhook",
      "settings":{"url":"https://webhook.site/81976ab3-901f-4b4b-960a-63cab94395ad","httpMethod":"POST"},
      "disableResolveMessage":false }'
{"uid":"efzljmgqftjpce","name":"quickticket-alerts","type":"webhook", ...}
```

webhook.site also turns out to be scriptable, which the lab assumes it is not — `POST
https://webhook.site/token` returns a fresh token (`201`), and `GET
https://webhook.site/token/<uuid>/requests` returns everything delivered to it. So the
receiving end is verifiable from the terminal too.

With the Test button unavailable, delivery was proven with a real alert: a temporary rule
whose condition is `vector(1) > 0` and whose pending period is `0s`, evaluated every 10s.

```
$ # rule created 07:53:00Z, deleted immediately after the check
$ curl -s "https://webhook.site/token/$TOKEN/requests?sorting=newest"
```

```json
{
  "receiver": "quickticket-alerts",
  "status": "firing",
  "alerts": [{
    "status": "firing",
    "labels": {"alertname": "ZZ Contact Point Delivery Check", "severity": "info", ...},
    "startsAt": "2026-09-28T07:53:10Z",
    "values": {"A": 1, "C": 1}
  }]
}
```

| Event | Time (UTC) |
|---|---|
| rule entered `firing` (`startsAt`) | 07:53:10 |
| `POST` landed on webhook.site | 07:53:40 |
| **delta** | **30s — exactly `group_wait`** |

That 30s gap is the notification policy doing what §6.4 configures it to do, so this one
check validates the contact point and the policy together.

### 6.3 Alert rules — and a defect in the queries the lab supplies

Both rules are Grafana-managed, live in folder `QuickTicket Alerts`, rule group
`quickticket-slo`, evaluated every 60s:

```
$ curl -s -u admin:admin -X PUT :3000/api/v1/provisioning/folder/$FUID/rule-groups/quickticket-slo \
    -d '{"title":"quickticket-slo","folderUid":"'"$FUID"'","interval":60}'
```

**The PromQL in the lab breaks on a healthy system.** Written as given:

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

When there are no 5xx at all the selector matches nothing, `sum(rate(...))` returns an
*empty vector*, and an empty vector divided by anything is still empty — not zero:

```
$ curl -s :9090/api/v1/query --data-urlencode 'query=sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100'
no data
```

Combined with the default `noDataState: NoData` this inverts the alert: a first draft of
this rule, on a completely healthy stack, went to **pending within 10 seconds**. The rule
as written pages you precisely when nothing is wrong. Two changes fix it:

```diff
-sum(rate(gateway_requests_total{status=~"5.."}[5m]))
-  / sum(rate(gateway_requests_total[5m])) * 100
+(sum(rate(gateway_requests_total{status=~"5.."}[5m])) or vector(0))
+  / sum(rate(gateway_requests_total[5m])) * 100
```

`or vector(0)` supplies an explicit zero when the numerator is empty, and `noDataState` is
set to `OK` so that a genuine gap in traffic is silence rather than a page. Verified:

```
$ # same instant, same data, with the fix
0.0
```

The burn-rate query has the same hole mirrored — if *every* request were a 5xx, its
`status!~"5.."` numerator would be the empty one — so it gets the same treatment.

**Alert 1 — `QuickTicket High Error Rate`** (`severity=critical`, `for: 2m`, `noDataState: OK`)

```promql
(sum(rate(gateway_requests_total{status=~"5.."}[5m])) or vector(0))
  / sum(rate(gateway_requests_total[5m])) * 100
```

condition: `IS ABOVE 2` — the threshold is derived in §6.6, and started at the lab's `5`.

**Alert 2 — `QuickTicket SLO Burn Rate`** (`severity=warning`, `for: 5m`, `noDataState: OK`)

```promql
(1 - ((sum(rate(gateway_requests_total{status!~"5.."}[30m])) or vector(0))
  / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

condition: `IS ABOVE 6`.

Rules are built as a two-node graph — `A` is the instant PromQL query, `C` is a threshold
expression over `A`, and `C` is the rule condition:

```json
"data": [
  {"refId":"A","datasourceUid":"PBFA97CFB590B2093","relativeTimeRange":{"from":600,"to":0},
   "model":{"refId":"A","instant":true,"expr":"(sum(rate(gateway_requests_total{status=~\"5..\"}[5m])) or vector(0)) / sum(rate(gateway_requests_total[5m])) * 100"}},
  {"refId":"C","datasourceUid":"__expr__",
   "model":{"refId":"C","type":"threshold","expression":"A",
    "conditions":[{"evaluator":{"type":"gt","params":[2]}, ...}]}}
],
"condition":"C", "for":"2m", "noDataState":"OK", "execErrState":"Error",
"labels":{"severity":"critical"},
"annotations":{"summary":"Gateway error rate is {{ $value }}%", ...}
```

### 6.4 Notification policy

```
$ curl -s -u admin:admin -X PUT :3000/api/v1/provisioning/policies -d '{
    "receiver":"quickticket-alerts",
    "group_by":["alertname"],
    "group_wait":"30s",
    "group_interval":"5m",
    "repeat_interval":"5m" }'
{"message":"policies updated"}

$ curl -s -u admin:admin :3000/api/v1/provisioning/policies
{"receiver":"quickticket-alerts","group_by":["alertname"],"group_wait":"30s","group_interval":"5m","repeat_interval":"5m"}
```

The 30s `group_wait` is confirmed empirically by the delivery check in §6.2.

### 6.5 Runbook

> Written **before** the incident in §6.7 and then followed during it. Every command in
> the Diagnosis section was executed against a live failure and its real output recorded —
> the "Observed" lines are transcripts, not expectations.

```markdown
# Runbook: QuickTicket High Error Rate

## Alert
- **Fires when:** gateway 5xx rate > 2% of all requests, sustained 2 minutes
- **Severity:** critical
- **Dashboard:** QuickTicket — Golden Signals (Grafana → QuickTicket folder)
- **Contact point:** quickticket-alerts (webhook)
- **Notification lags the alert by 30s** (`group_wait`) — do not treat a quiet webhook in
  the first half-minute as a false alarm.

## Diagnosis

Run these from `app/`. They are ordered to narrow the blast radius before touching logs.

1. **Which dependency does the gateway think is broken?**
   `curl -s http://localhost:3080/health | python3 -m json.tool`
   - Observed when payments is **stopped**: `"payments": "down"`, HTTP 503
   - Observed when payments is **up but failing half its charges**: `"payments": "ok"`,
     HTTP 200 — *this endpoint cannot see a partial failure, see step 4*

2. **Is payments reachable at all, and is fault injection switched on?**
   `curl -s http://localhost:8082/health`
   - Observed healthy: `{"status":"healthy","failure_rate":0.0,"latency_ms":0}`
   - Observed during injected failures: `{"status":"healthy","failure_rate":0.5,...}`
   - A non-zero `failure_rate` here is the single most direct answer this system gives.

3. **Is events implicated too?** (rules out a shared dependency)
   `curl -s http://localhost:8081/health`
   - Observed throughout the payments incident: `{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}`

4. **Count the failures — do not read the tail.**
   `docker compose logs payments --since=5m | grep -c 'Payment failed'`
   `docker compose logs payments --since=5m | grep -c 'Payment success'`
   `docker compose logs gateway  --since=5m | grep -c '500 Internal Server Error'`
   - `--tail=20` as a first move is a trap: during a 50%-failure incident the last 20 lines
     came back **all successes**, because successes outnumber failures in wall-clock order.
     Observed counts at that moment: 4 failed / 11 succeeded, 8 gateway 500s.

5. **Which endpoint is actually erroring?** (confirms blast radius)
   `curl -s :9090/api/v1/query --data-urlencode 'query=sum by (path,status) (increase(gateway_requests_total[5m]))'`
   - Only `/reserve/{id}/pay` should carry 5xx. If `/events` does too, this is not a
     payments incident.

6. **Alert state, without the UI:**
   `curl -s -u admin:admin :3000/api/prometheus/grafana/api/v1/rules`

## Common Causes

Ordered by how often they are the answer, not by severity. `$C` below is shorthand for
`docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml`.

| Cause | How to identify | Fix |
|-------|----------------|-----|
| Payments container not running | Step 1 `"payments":"down"` + HTTP 503; step 2 refuses the connection (curl exit 7); step 6 shows `payments  Exited`; gateway log says `Name or service not known` | `$C up -d payments` |
| Payments up, failing a share of charges | Step 1 still says `"payments":"ok"` + HTTP 200 — it cannot see this; step 2 returns `"failure_rate"` above `0.0`; step 4 shows `Payment failed` **and** `Payment success` interleaved | `$C stop payments && PAYMENT_FAILURE_RATE=0.0 $C up -d payments` |
| Payments up but slow | Step 2 returns non-zero `"latency_ms"`; gateway 5xx are **504**, not 502, and the log says `Payment service timeout` | `$C stop payments && PAYMENT_LATENCY_MS=0 $C up -d payments`. Only raise `GATEWAY_TIMEOUT_MS` if the latency is real and expected to persist |
| Events is the failing dependency, not payments | Step 1 shows `"events":"degraded"`/`"down"`; step 5 shows 5xx on `/events` or reserve, not only on `/pay` | **Stop — wrong runbook.** Go to *QuickTicket reservations failing / checkout timing out* |
| Shared dependency down (Postgres or Redis) | Step 3 returns 503 with `postgres` or `redis` not `"ok"` | `$C start postgres` / `$C start redis`. Do not restart `events` — its clients reconnect on their own |
| Everything checks out clean | Steps 1–3 all healthy, step 5 shows no 5xx in the last 5 minutes, step 6 still says `firing` | The fault already ended and the 5-minute averaging window is draining; it takes ~4 minutes to clear. Confirm step 5 twice, two minutes apart, then **wait**. Restarting things here only adds a second incident |

**After any fix, before you call it mitigated:** re-run step 1 and require `HTTP 200` with
`"payments":"ok"`. Do not accept "the command exited without complaining" as evidence — a
mitigation on this stack has already been issued once, looked successful, and done nothing
(see the postmortem's *What Went Wrong*).

## Escalation

**Timings measured on this stack**, to judge "too long" against: the alert fires 4m00s
after the fault begins; working the six diagnosis steps takes ~15s; restarting a container
restores service in under a second; the alert then needs a further 4m01s to clear on its own.

- **Escalate if 10 minutes pass from the notification with service still down.** That is
  roughly four times the entire observed diagnose-and-fix path, so it means the fault is not
  one of the rows in the table above.
- **Escalate immediately, without waiting out the 10 minutes, if:** the restart succeeds but
  errors continue; Postgres is the failing dependency (data is at risk, unlike a stateless
  payments restart); or two dependencies report unhealthy at the same time.
- **Escalate to:** course instructor / TA, through the course channel.
- **Include in the escalation:** the alert's `startsAt`, the raw output of steps 1–3, and
  `docker compose ps -a`. Those three are enough to re-run the whole diagnosis without you.

**Do not silence the alert to stop the repeat notifications.** `repeat_interval` is 5m by
design; a silence outlives the incident and the next occurrence arrives unannounced.
```

### 6.6 Threshold tuning — deriving the number instead of guessing it

The lab specifies `IS ABOVE 5` and then warns in a hint that `PAYMENT_FAILURE_RATE=0.5`
"means 50% of charge requests fail — but charges are only ~10% of traffic, so overall
error rate is ~1-2%". Both halves of that are worth checking rather than assuming, so the
rule was created with the lab's threshold of `5` and the lab's injection was run first.

**Measured traffic mix** (the denominator of the alert), after the inventory fix from §6.0:

```
$ curl -s :9090/api/v1/query --data-urlencode 'query=sum by (path,status) (increase(gateway_requests_total[1m]))'
/events                      200    114.7   65.6%
/events/{id}/reserve         200     49.3   28.2%
/reserve/{id}/pay            200     10.7    6.1%
```

The purchase branch is 10% of loadgen *iterations*, but each iteration issues two gateway
requests (reserve, then pay), so payments only accounts for ~6% of *requests*. That is the
ceiling on the damage this fault can do: **a total payments outage cannot push the error
rate much past 6%**, and a 50% failure rate cannot push it past ~3%.

**What the lab's injection actually does.** `PAYMENT_FAILURE_RATE=0.5` was applied at
07:54:17Z with the rule still on the lab's threshold of `5`. The hint predicts 1–2%.
Measured, once the 5-minute rate window filled:

```
[07:55:39] erate=0.469   HighErrorRate=inactive
[07:57:09] erate=2.476   HighErrorRate=inactive
[07:59:10] erate=4.257   HighErrorRate=inactive
[07:59:40] erate=5.322   HighErrorRate=inactive
[08:00:21] erate=6.102   HighErrorRate=pending     <- crossed 5%
[08:02:22] erate=5.597   HighErrorRate=firing      <- after the 2m pending period
[08:04:23] erate=4.046   HighErrorRate=firing
[08:05:23] erate=3.769   HighErrorRate=inactive    <- self-resolved, fault still injected
[08:07:44] erate=3.453   HighErrorRate=inactive
```

The steady-state error rate is **~3.4–6.1%**, not 1–2%. And the threshold of 5 does not
fail by staying silent — it fails by **flapping**. The alert fired at 08:02:22, then
cleared itself at 08:05:23 while payments was still dropping half of every charge.
Both transitions were delivered to the webhook:

```
2026-09-28 08:02:35  status=firing    values={'A': 5.61, 'C': 1}
2026-09-28 08:07:35  status=resolved  values={'A': 3.44, 'C': 0}
```

A `resolved` notification arrived for an incident that was still happening. That is worse
than no alert at all: an all-clear that is wrong teaches the on-call to distrust the
channel.

**Choosing the threshold.** The SLO from Lab 3 is 99.5% availability, so the error budget
is 0.5%. Expressing a candidate threshold as a burn rate against that budget:

| Threshold | Burn rate | 30-day budget exhausted in | Behaviour against the measured signal |
|---|---|---|---|
| 5% (lab default) | 10× | 3 days | sits inside the 3.4–6.1% signal band — flaps |
| **2% (chosen)** | **4×** | **7.5 days** | 1.7× below the weakest part of the signal — stable |
| 0.5% | 1× | 30 days | inside normal noise — would fire on single stray 5xx |

2% is the lowest number that is unambiguously an incident rather than noise, and it clears
the measured failure signal with enough margin that the rule does not oscillate. The
threshold was changed from `5` to `2` via the same API before the real incident:

```
$ # PUT /api/v1/provisioning/alert-rules/<uid> with evaluator.params = [2]
PUT -> 200
QuickTicket High Error Rate threshold: [2]
QuickTicket SLO Burn Rate threshold: [6]
```

The deeper point is that the ceiling is structural: payments handles ~6% of requests, so
**no payments fault can ever produce a 50% gateway error rate**. A threshold copied from a
tutorial without checking the traffic mix is a threshold that may be unreachable.

### 6.7 The incident

With the threshold at 2% and a clean baseline (`erate = 0.0`, both rules `inactive`),
payments was stopped outright — a total dependency outage rather than the partial one used
for tuning:

```
$ docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
```

**Diagnosis, following the runbook in §6.5 step by step.** Real transcript:

```
step 1  $ curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://localhost:3080/health
        HTTP 503
        {"status": "degraded",
         "checks": {"events": "ok", "payments": "down", "circuit_payments": "CLOSED"}}

step 2  $ curl -s -m 5 http://localhost:8082/health
        (curl exit 7 — connection refused, HTTP 000)

step 3  $ curl -s http://localhost:8081/health
        HTTP 200 {"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}

step 4  $ docker compose logs payments --since=3m | tail -5
        (no output — a stopped container writes nothing, so logs are silent, not noisy)
        $ docker compose logs gateway --since=3m | grep -c '502 Bad Gateway'
        40
        $ docker compose logs gateway --since=3m | grep 'payment error' | tail -3
        {"level":"ERROR","service":"gateway","msg":"payment error: [Errno -2] Name or service not known"}

step 5  $ sum by (path,status) (increase(gateway_requests_total[3m]))
          /events                      200    332.7
          /events/{id}/reserve         200    130.9
          /reserve/{id}/pay            502     38.2     <- blast radius is exactly one route

step 6  $ docker compose ps -a
        payments     Exited (0) 4 minutes ago
```

Three things about that transcript are worth keeping, because they are what a runbook is
*for*:

- **Step 4 produces silence, not errors.** The instinct on a dead service is to read its
  logs; a stopped container has none. The signal is in the *caller's* log —
  `Name or service not known` is a DNS failure, which means the container is gone rather
  than broken.
- **`docker compose ps` without `-a` hides the evidence.** It returns a tidy six-service
  list with payments simply absent. You have to notice an absence. With `-a` the line reads
  `Exited (0)`. The runbook specifies `-a`.
- **Exit code 0.** The container did not crash, it was stopped. Nothing in the logs or the
  exit status suggests a fault — only the operator action that caused it.

**Two runs, and why.** The incident was run twice. The first run (08:13:26Z) produced valid
detection numbers but an invalid recovery number, for a reason worth recording: the
mitigation command was written as

```bash
DC="docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml"
$DC stop payments
```

zsh does not word-split an unquoted parameter, so the entire string was taken as a command
name and the shell answered `no such file or directory: docker compose -f ...`. **The
mitigation appeared to be applied and did nothing.** The outage ran another 27 minutes. The
only reason it was caught is that the alert refused to clear — which is exactly the job the
alert exists to do, and is carried into the postmortem as a finding rather than hidden.

That accidental 27-minute outage did produce one thing the short run could not: it drove
the 30-minute burn-rate window past its threshold, so **Alert 2 fired for real**:

```
2026-09-28 08:23:30  firing    QuickTicket SLO Burn Rate  values={'A': 9.37,  'C': 1}
2026-09-28 08:45:46  firing    QuickTicket SLO Burn Rate  values={'A': 16.22, 'C': 1}
2026-09-28 08:58:21  resolved  QuickTicket SLO Burn Rate  values={'A': 1.72,  'C': 0}
```

A 16× burn rate means the 30-day error budget would have been gone in under two days.

The second run is the clean measurement, from a verified baseline (`erate = 0.0`, both
rules `inactive`), and is the one the postmortem uses.

### Timeline — clean run

| Time (UTC) | +T | Event | Evidence |
|---|---|---|---|
| 08:58:06 | 0s | payments container stopped | `### T0 INJECT 2026-09-28 08:58:06Z` |
| 08:59:29 | +83s | error rate crosses 2% | `erate=2.39` |
| 09:00:06 | +120s | rule → `pending` | `state=pending` |
| 09:02:06 | +240s | rule → **`firing`** | `state=firing`, `erate=6.533` |
| 09:02:06 | +240s | diagnosis: `gateway /health` 503, `payments Exited (0)` | runbook steps 1–6 |
| 09:02:06 | +240s | fix applied — payments restarted at `PAYMENT_FAILURE_RATE=0.0` | `payments /health → 200`, `gateway /health → 200` |
| 09:02:35 | +269s | webhook notification delivered | `values={'A': 6.53, 'C': 1}` |
| 09:06:07 | +481s | rule → `Normal` | `erate=1.866` |

Derived figures:

| Measurement | Value |
|---|---|
| injection → alert `firing` | **240s** (4m00s) |
| alert `firing` → notification delivered | **29s** |
| injection → notification in hand | **269s** (4m29s) |
| fix applied → user-visible recovery | **< 1s** (`gateway /health` 200 on the first probe) |
| fix applied → alert back to `Normal` | **241s** (4m01s) |
| peak error rate | **6.53%** |

The gap between "the service is fixed" (under a second) and "the alert says so" (four
minutes) is the same 5-minute averaging window that delayed detection, running in reverse.

### Written answer: how long from failure injection to alert firing? Why the delay?

**240 seconds** — payments stopped at 08:58:06Z, the rule entered `firing` at 09:02:06Z.

The delay is not one wait, it is three stacked on top of each other, and the measured
timestamps account for all 240 seconds exactly:

| Component | Cost | Why it exists |
|---|---:|---|
| `rate(...[5m])` window filling | 83s | The true error rate jumped to ~6.5% instantly, but a 5-minute average cannot. Measured climb: 0.18% → 1.02% → 2.39% → 3.39% → 4.91% → 6.53%. It only crossed the 2% threshold at 08:59:29. |
| Evaluation quantisation | 37s | The rule group is evaluated once every 60s. The threshold was crossed at 08:59:29; the next evaluation was at 09:00:06. Worst case here is 60s, and this run drew 37 of them. |
| `for: 2m` pending period | 120s | Deliberate. The condition must hold across two consecutive evaluations before the rule is allowed to fire. |
| **Total** | **240s** | |

Delivery added another 29s of `group_wait` on top, so the notification was in hand at
09:02:35Z — 4m29s after the fault began.

**Which part I would actually shorten.** Not the pending period, and not the evaluation
interval. The pending period is the only thing standing between this alert and the flapping
demonstrated in §6.6 — the whole point of `for: 2m` is that a single unlucky evaluation
cannot page anyone. Dropping the evaluation interval to 10s would buy at most 50s and would
multiply Grafana's query load against Prometheus by six for that gain.

The 83 seconds of window fill is the expensive part, and lowering `[5m]` to `[1m]` is the
wrong way to reclaim it: a short window is noisy, and noise plus a low threshold is exactly
how you get an alert nobody trusts.

The right fix is not to tune this rule at all — it is to **add a second, structurally faster
signal** and leave this one alone. Two candidates, both already visible in the evidence:

1. `gateway /health` reported `"payments": "down"` within seconds of the container stopping,
   because it is a direct probe rather than an average over a window. An alert on a
   dependency-health gauge would fire in well under a minute and is immune to traffic mix.
2. The multiwindow burn-rate approach from the SRE workbook: a fast window (e.g. 5m at 14.4×)
   for total outages alongside the slow 30m window already configured as Alert 2, so a
   complete outage is not detected on the same timescale as a slow degradation.

That way the fast path catches a hard failure in seconds, and the averaged rule keeps doing
what it is good at — deciding whether a partial degradation is sustained enough to be worth
waking someone for.

---

## Task 2 — Blameless Postmortem

> Every field below is filled from the recorded incident — the timestamps come from the
> polling log in `raw.log`, not from memory. The fault was injected deliberately as part of
> this lab, but the postmortem deliberately does not stop at "someone stopped a container":
> that is the trigger, not the cause. The question it answers is why a single container stop
> became four minutes of failed checkouts with no automatic recovery and no fast detection.

```markdown
# Postmortem: QuickTicket checkout unavailable — payments dependency stopped

**Date:** 2026-09-28
**Duration:** 08:58:06Z → 09:06:07Z (8m01s from injection to alert clear;
              user-visible impact ended 09:02:06Z, i.e. 4m00s)
**Severity:** SEV-3 — one revenue-carrying feature fully unavailable, the rest of the
              product unaffected, no data loss, recovered within the hour.
              *(This would be SEV-2 if it had lasted long enough to threaten the monthly
              error budget. It very nearly did: a longer instance of the identical fault
              earlier the same hour drove the 30-minute burn rate to **16.2×**, which
              exhausts a 30-day budget in under two days.)*
**Author:** Masis Davoian

## Summary

The payments service stopped at 08:58:06Z and nothing restarted it, so every checkout
attempt returned HTTP 502 for the next four minutes — 6.53% of all gateway traffic at peak,
with browsing and seat reservation entirely unaffected. Detection was automatic and took
four minutes; the repair itself took under a second once the cause was known.

## Timeline
| Time (UTC) | Event |
|------|-------|
| 08:58:06 | payments container stopped; `/reserve/{id}/pay` begins returning 502 |
| 08:59:29 | measured error rate crosses the 2% threshold |
| 09:00:06 | alert rule enters `pending` |
| 09:02:06 | alert rule enters `firing` |
| 09:02:06 | diagnosis: `gateway /health` 503 `"payments":"down"`; `docker compose ps -a` shows `payments Exited (0)` |
| 09:02:06 | payments restarted; `gateway /health` returns 200 on the first probe |
| 09:02:35 | webhook notification delivered (29s after firing, matching `group_wait`) |
| 09:06:07 | alert rule returns to `Normal` |

*Diagnosis and mitigation carry the same timestamp because the runbook had already been
walked end to end against an earlier instance of this fault (08:17:44Z–08:17:59Z), so the
cause was recognised on sight. The first-encounter diagnosis — the honest number for how
long these six steps take someone seeing the symptom for the first time — was 15 seconds.*

## Root Cause

The payments container exited cleanly (status `0`). That alone should have been a
non-event. It became a user-visible outage because three independent gaps lined up:

1. **Nothing owns restarting a stopped service.** No service in either compose file declares
   a `restart:` policy, and no supervisor sits above them. A clean exit is therefore
   permanent until a human notices. The system has no concept of "this should be running".

2. **The gateway has no degraded mode for a missing payments dependency.** Every failed
   charge is converted straight into a user-facing 502. The two mechanisms that exist to
   prevent exactly this are present in the code but inert — `call_with_retry()` and
   `CircuitBreaker.call()` in `app/gateway/main.py` are both Lab 11 stubs whose bodies are
   `# TODO (Lab 11)` followed by `return await func()`. So there is no retry of a transient
   failure, no fast-fail once the dependency is known dead, and no queue-and-confirm-later
   path. The blast radius of "payments is absent" is the full set of checkout attempts.

3. **The only detection path was a lagging one.** `gateway /health` correctly reported
   `"payments": "down"` within seconds, because it is a direct probe. Nothing consumes that
   signal. Detection instead depended on a 5-minute averaged error ratio crossing a
   threshold, which structurally cannot react in less than the time the window takes to
   fill — 83 seconds here, before the deliberate 2-minute pending period even started.

Stated as one chain: a service can stop without anything restoring it, the caller turns its
absence into customer-facing errors rather than absorbing it, and the fastest signal the
system already produces is not wired to anything — so the outage lasted as long as it took a
5-minute average to notice.

## What Went Well

- **Detection needed no human.** The alert fired 240s after the fault with nobody watching,
  and the notification reached the contact point 29s later — exactly the configured
  `group_wait`, so the delivery path behaved as designed rather than by luck.
- **The blast radius was genuinely contained.** `/events` and `/events/{id}/reserve` served
  200 throughout. The failure stayed on the one route that depends on payments, which is
  what made the diagnosis fast.
- **The runbook worked on first use.** Six steps, ~15 seconds, no dead ends, and the
  distinguishing evidence (`payments Exited (0)`, `Name or service not known` in the
  caller's log) was exactly where the runbook said it would be.
- **Recovery was instant once the cause was known.** `gateway /health` returned 200 on the
  first probe after the restart. The time cost of this incident was detection, not repair.
- **A monitoring gap produced silence, not noise.** Later in the session host contention
  stalled rule evaluation for ~8 minutes. Because the rules were configured with
  `noDataState: OK`, the result was no page at all rather than a false one — the design
  decision from §6.3 paid off under a condition it was not written for.

## What Went Wrong

- **A mitigation was issued that silently did nothing.** At 08:18:25Z, during an earlier
  instance of this same fault, the recovery command was stored in a shell variable and
  invoked unquoted; the shell treated the whole string as one command name and refused it.
  The step *looked* applied. The outage continued a further 27 minutes. The only thing that
  caught it was the alert declining to clear. There is no procedural step anywhere that
  requires proving a fix landed before declaring an incident mitigated.
- **The inherited threshold produced a false all-clear.** With the lab's default of 5%, the
  alert fired at 08:02:22Z and then sent `status=resolved` to the webhook at 08:07:35Z while
  the fault was still fully present. An incorrect all-clear is worse than no alert: it
  actively tells the responder to stand down.
- **The obvious diagnostic commands hide this class of failure.** `docker compose logs
  payments` returns nothing at all, because a stopped container writes no logs, and
  `docker compose ps` without `-a` omits the failed service from the table entirely — the
  output looks like a healthy six-service stack. Both invite the wrong conclusion.
- **Detection cost four minutes for a total outage.** 83s of averaging-window fill, 37s of
  evaluation quantisation, 120s of pending period. The system knew within seconds
  (`gateway /health`) and that knowledge went nowhere.
- **The monitoring stack is not isolated from host load.** Resource contention on the host
  stalled Grafana rule evaluation for roughly 8 minutes mid-session, leaving a NoData gap in
  the incident record. The alerting path degraded at exactly the moment it was needed.

## Action Items

| # | Action | Traces to | Owner | Priority | Status |
|---|--------|-----------|-------|----------|--------|
| 1 | Require a fix to prove itself: after any mitigation, re-run the step-1 health check and require an explicit `HTTP 200` before the incident is called mitigated. Never accept "the command exited" as evidence. | Silent mitigation failure | Masis Davoian | **High** | **Done** — added to the *Common Causes* section of both runbooks in this submission |
| 2 | Add `restart: unless-stopped` to every service in `app/docker-compose.yaml` and `docker-compose.monitoring.yaml`, so a clean exit self-heals instead of waiting on a human. | Root cause #1 | Masis Davoian | **High** | Open — needs a change to files owned by the course template; raise before editing |
| 3 | Add a dependency-health alert on `gateway /health` reporting any critical dependency as `down`/`degraded`, with a short pending period. Detects a hard outage in seconds instead of four minutes, and is independent of traffic mix. | Root cause #3, detection cost | Masis Davoian | **High** | Open |
| 4 | Add a fast burn-rate window (5m at 14.4×) alongside the existing 30m rule, per the SRE workbook's multiwindow method, so a total outage and a slow degradation are not detected on the same timescale. | Detection cost | Masis Davoian | Medium | Open |
| 5 | Make `grep`-based failure counting and `docker compose ps -a` the standing convention for every runbook in this repo, not just these two — `--tail` and bare `ps` are what hid this failure. | Diagnostics that mislead | Masis Davoian | Medium | **Partly done** — applied in both runbooks here; not yet written down as a convention for future ones |
| 6 | Record an explicit, dated decision that the Lab 11 resilience stubs (`call_with_retry`, `CircuitBreaker.call`) are knowingly inert, so their absence is a tracked risk rather than something discovered mid-incident. | Root cause #2 | Masis Davoian | Medium | Open |
| 7 | Re-validate every alert threshold against the measured traffic mix rather than copying defaults, and re-check after any change to the request mix. | False all-clear | Masis Davoian | Medium | **Done for the two rules in this lab** (§6.6); open as a standing practice |
| 8 | Give the monitoring stack a CPU reservation, or accept and document that alert evaluation degrades under host load. Either is fine; the current state — unknown and unstated — is not. | Monitoring stalled under load | Masis Davoian | Low | Open |

*Owner is the same name on every row because this project has one maintainer. That is a
statement of fact rather than a distribution of work; in a team, items 1–3 would be split so
that the detection change and the runbook change do not queue behind each other.*
```

### Written answer: what is the most important action item, and why?

**Item 1 — require a fix to prove itself before the incident is called mitigated.** It is
already applied in both runbooks above, which is itself part of the argument: it was the
cheapest item on the list and the only one that could be closed the same day.

The tempting answer is item 2, the restart policy, because it would have prevented this
particular incident outright. I do not think it is the right one. A restart policy fixes
exactly one failure mode: a process that exits cleanly and should not have. It does nothing
for a process that is running and wrong — the more common and more expensive case, and the
one this same lab produced in §6.6, where payments was up, reported itself `healthy`, and
was failing half of all charges.

Item 1 matters more because it addresses a failure of the *response*, not of the system, and
response failures compound on top of whatever else is already broken. The evidence is in the
record rather than in theory: a mitigation was applied at 08:18:25Z, appeared to succeed,
and did nothing. The outage ran 27 minutes longer than anyone believed it was running, and
every one of those minutes was spent by a responder who thought the incident was over.
Nothing in the system caught it — not the health checks, not the logs, not any of the six
diagnostic steps. The only thing that noticed was an alert that refused to clear, and that
was a lucky side effect of configuration rather than a designed safeguard: with the lab's
original flapping 5% threshold, the alert would have cleared itself and confirmed the false
belief instead of contradicting it.

That is a difference in kind. Items 2 through 8 each shorten one class of incident. Item 1
stops the response from silently becoming the longest part of any incident, and it costs one
line in a runbook. An unverified fix is indistinguishable from no fix right up to the moment
someone happens to notice — and "someone eventually notices" is precisely the condition this
whole lab exists to replace.

---

## Bonus Task — Cross-Test Runbook

### B.1 Choosing the failure mode

Four candidates were on the table; the pick is **Redis down**, chosen for whether the
cross-test would actually test anything:

| Candidate | Verdict |
|---|---|
| PostgreSQL down | Everything fails at once and `events /health` names `postgres` directly. Solvable in under a minute — it tests nothing. |
| Gateway timeout too short | Needs a config edit plus slow responses to reproduce. Intermittent and load-dependent, so a failed cross-test would be ambiguous: bad runbook, or load that never triggered it? |
| DB pool exhausted | Requires concurrency far above what `loadgen` produces at 3 rps. Not reliably reproducible on this stack. |
| **Redis down** | **Partial failure.** `/events` — the majority of traffic — keeps returning 200, so the system looks half-alive. The failing routes are the write paths, and the symptom is a *timeout*, not an immediate error. Diagnosis requires going one level deeper than `gateway /health`, which is the skill being tested. |

Redis-down is also genuinely distinct from the payments incident in §6.7: a different
status code (504 vs 502), a different health-check signature, and a lag before the system
admits anything is wrong.

### B.2 Self-test before handing it over

Every command in the runbook below was executed against a real Redis outage injected at
09:07:14Z, **before** the runbook is given to anyone. This is a check that the instructions
are executable and that the outputs they promise are the outputs that appear — not a
substitute for the classmate's test.

Two claims in the first draft did not survive contact and were corrected:

- The draft said `events /health` reports `redis: down`. **The first probe, 8 seconds after
  Redis was stopped, still returned `200 {"redis":"ok"}`** — `events/main.py` caches the
  Redis health result for 5 seconds (`_REDIS_CHECK_INTERVAL = 5.0`) and the first check
  after the stop had not happened yet. From ~50s onward it was consistently
  `503 {"postgres":"ok","redis":"down"}`. The runbook now tells the responder to probe
  twice, a few seconds apart, rather than trust one sample.
- The draft expected 502s, by analogy with the payments incident. The real status code is
  **504**: redis-py blocks on DNS resolution longer than `GATEWAY_TIMEOUT_MS=5000`, so the
  gateway times out rather than receiving an error. That single digit is the fastest way to
  tell the two incidents apart.

### B.3 Second runbook

```markdown
# Runbook: QuickTicket reservations failing / checkout timing out

## Alert
- **Fires when:** gateway 5xx rate > 2% for 2 minutes (same rule as the High Error Rate
  alert — this runbook is for when that alert fires and the payments runbook does NOT match)
- **First discriminator:** if `/events` is serving 200 but reservations are failing, and the
  failures are **504** rather than 502, you are in this runbook, not the payments one.

## Diagnosis

Run from `app/`.

1. **Gateway's view of its dependencies.**
   `curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://localhost:3080/health`
   `curl -s http://localhost:3080/health`
   - Observed: `HTTP 503`
     `{"status":"degraded","checks":{"events":"degraded","payments":"ok","circuit_payments":"CLOSED"}}`
   - Note `events` reads **`degraded`**, not `down` — the gateway reports any non-200 from a
     dependency as `degraded`, so this does not tell you whether events is dead or merely
     unhappy. Go to step 2; do not stop here.

2. **Ask events directly. Probe it twice, ~10s apart.**
   `curl -s -o /dev/null -w 'HTTP %{http_code}  ' http://localhost:8081/health; curl -s http://localhost:8081/health`
   - Observed 8s after the outage began: `HTTP 200 {"postgres":"ok","redis":"ok"}` — **stale**
   - Observed from ~50s onward: `HTTP 503 {"status":"degraded","checks":{"postgres":"ok","redis":"down"}}`
   - The Redis result is cached for 5s in `events/main.py` (`_REDIS_CHECK_INTERVAL = 5.0`),
     and the cached value survives the first moments of an outage. One sample is not evidence.

3. **Rule payments out.**
   `curl -s http://localhost:8082/health`
   - Observed: `HTTP 200 {"status":"healthy","failure_rate":0.0,"latency_ms":0}`
   - If this is healthy and step 2 shows `redis: down`, the payments runbook does not apply.

4. **Confirm Redis is actually gone.**
   `docker compose exec redis redis-cli ping`
   - Observed: `service "redis" is not running`
   - From the caller's side, which is what matters:
     `docker compose exec -T events python3 -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('redis',6379))"`
   - Observed: `gaierror [Errno -2] Name or service not known` — DNS, i.e. the container is
     gone, not merely refusing connections.

5. **Blast radius.**
   `curl -s :9090/api/v1/query --data-urlencode 'query=sum by (path,status) (increase(gateway_requests_total[2m]))'`
   - Observed:
     ```
     /events                      200    139.4     <- unaffected
     /events/{id}/reserve         200     40.0
     /reserve/{id}/pay            200     16.0
     /events/{id}/reserve         504      8.2     <- timeouts, not errors
     ```
   - **504, not 502.** redis-py retries the name lookup for longer than
     `GATEWAY_TIMEOUT_MS=5000`, so the gateway gives up before events answers. In the
     payments incident the same position shows 502.

6. **Events logs name it outright.**
   `docker compose logs events --since=2m | grep -iE "redis|error"`
   - Observed: `redis.exceptions.ConnectionError: Error -2 connecting to redis:6379. Name or service not known.`

7. **Container state — `-a` is required.**
   `docker compose ps -a`
   - Observed: `redis    Exited (0)`
   - Without `-a` the stopped service is omitted from the table entirely.

## Common Causes

`$C` is shorthand for `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml`.

| Cause | How to identify | Fix |
|-------|----------------|-----|
| Redis container not running | Step 2 `redis: "down"`; step 4 fails with `gaierror [Errno -2] Name or service not known` (the name does not resolve, so the container is gone); step 7 `redis  Exited` | `$C start redis`. **Do not restart `events`** — `redis-py` reconnects by itself once the host resolves again; this was verified |
| Redis running but not answering | Step 2 `redis: "down"`, but step 4's socket connect *succeeds* and only `redis-cli ping` hangs or errors. Container shows `Up` in step 7 | `$C restart redis`, then check `$C logs redis --tail=50` for OOM or a failed background save before closing the incident |
| Postgres is the failing dependency, not Redis | Step 2 returns 503 with `postgres: "down"` (and possibly `redis: "ok"`) | `$C start postgres`. Treat as higher severity than Redis: reservations are cached, orders are not |
| Stale health cache — no fault at all | Step 1 says `events: "degraded"` but step 2, probed again ~10s later, returns 200 with everything `"ok"`; step 5 shows no 5xx | Nothing to fix. `events` caches its Redis check for 5s (`_REDIS_CHECK_INTERVAL = 5.0`), so a single probe taken during the outage's first seconds — or just after recovery — can disagree with reality. Probe twice before acting |
| Wrong runbook — this is a payments incident | Step 5 shows **502** on `/reserve/{id}/pay` rather than **504** on reserve, and step 3 shows payments unreachable or with a non-zero `failure_rate` | Switch to *QuickTicket High Error Rate* |
| Network, not the container | Step 7 shows `redis  Up`, but step 4 still reports `gaierror` from inside `events` | The compose network is broken or the containers are on different networks. `$C up -d` to reconcile, and check `docker network inspect app_default` |

**After any fix, before you call it mitigated:** re-run step 2 and require `HTTP 200` with
`redis: "ok"`, then run one end-to-end write, because a health check only proves the client
reconnected, not that the write path works:

    $ RID=$(curl -s -X POST -H 'Content-Type: application/json' -d '{"quantity":1}' \
            http://localhost:3080/events/1/reserve \
            | python3 -c 'import sys,json;print(json.load(sys.stdin)["reservation_id"])')
    $ curl -s -o /dev/null -w '%{http_code}\n' -X POST "http://localhost:3080/reserve/$RID/pay"
    200

## Escalation

**Timing measured on this stack:** the injected outage was diagnosed from these seven steps
and fully recovered — including the end-to-end smoke test above — in **251s (4m11s)**.
Recovery itself is a single `start`; essentially all of that time is diagnosis and
verification.

- **Escalate if 10 minutes pass from the notification without `redis: "ok"` restored.** At
  roughly 2.5× the whole observed path, that means the fault is not one of the rows above.
- **Escalate immediately, without waiting, if:** `redis` shows `Up` but `events` still
  cannot reach it (a network fault, out of scope for this runbook); Postgres is the failing
  dependency rather than Redis; or Redis restarts and then dies again within a minute
  (likely OOM — capture `$C logs redis` *before* restarting it a second time).
- **Escalate to:** course instructor / TA, through the course channel.
- **Include in the escalation:** the raw output of steps 2, 4, 5 and 7. Those four show what
  is down, whether it is reachable, what it is breaking, and what the container thinks it is
  doing.

**One warning specific to this failure mode:** the symptom is a *timeout*, not an error, so
the instinct is to blame whichever service is slow to answer. `events` is not slow — it is
blocked waiting on a Redis name lookup that will never succeed. Restarting `events` does not
help and destroys the evidence.
```

### B.3a Verified recovery

The mitigation and the proof that it worked were executed as part of the self-test, so the
classmate's run has a known-good endpoint to reach:

```
$ docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start redis
 Container app-redis-1  Started

$ curl -s http://localhost:8081/health
HTTP 200 {"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
$ curl -s http://localhost:3080/health
HTTP 200 {"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}

$ # end-to-end smoke, not just health checks
smoke reserve -> 377ec1e6-c1c6-4ff3-bd01-8551adc28035
smoke pay     -> HTTP 200
```

Injected outage lasted 251s end to end. Recovery needed no restart of `events` — the
`redis-py` client reconnects on its own once the host resolves again, which is worth
knowing before anyone starts restarting unrelated services.

### B.4 Cross-test results

> **Status: not yet run.** B.1–B.3 are complete — the runbook is written and every command
> in it has been executed against a real injected Redis outage. B.2's *swap* step needs a
> second person, who must not know in advance which fault will be injected, so it cannot be
> self-reported without defeating the point of the exercise. The table below is the form the
> result will take; the bonus checkbox at the top of this document stays unticked until it
> is filled.

| Question | Result |
|---|---|
| Did they resolve it using only the runbook? | *pending* |
| Time to diagnose | *pending* |
| Time to fix | *pending* |
| Steps that were unclear or missing | *pending* |
| Changes made to the runbook as a result | *pending* |

**Procedure to follow when the tester is available:** restore the stack to healthy, confirm
both alert rules are `inactive`, hand over §B.3 alone (not this document), then inject with
`docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop redis` and
start the clock at the moment the webhook notification arrives rather than at injection —
that is what a real responder's clock starts on.

---

## Summary

| What | Result |
|---|---|
| Alert rules | 2, both Grafana-managed, created via Provisioning API |
| Contact point | `quickticket-alerts`, webhook, delivery verified end to end |
| Notification policy | `group_by=[alertname]`, `group_wait=30s`, `repeat_interval=5m` — the 30s confirmed by measurement |
| Alert 1 fired | yes — 09:02:06Z, 240s after injection, notification 29s later |
| Alert 2 fired | yes — 08:23:30Z, peak burn rate **16.2×** |
| Peak error rate (payments outage) | 6.53% |
| Peak error rate (Redis outage) | 12.6% (last sample before the fix) |
| Detection (injection → firing) | 240s |
| Recovery (fix → service healthy) | < 1s |
| Recovery (fix → alert Normal) | 241s |
| Runbooks | 2, every diagnosis command executed against a live failure |
| Incidents injected | 3 — partial payments (threshold tuning), full payments (the incident), Redis (bonus self-test) |
| Alert-rule settle order after the Redis test | Alert 1 `Normal` 09:15:23Z, Alert 2 09:20:25Z — the 5m vs 30m window gap |

One last observation, from watching both rules clear after the bonus injection: Alert 1
returned to `Normal` at 09:15:23Z and Alert 2 at 09:20:25Z — five minutes apart, from the
same fault ending at the same instant. That gap is the 5-minute versus 30-minute averaging
window, and it is the clearest demonstration that the two rules are not redundant: one
answers "is something broken right now", the other answers "are we spending the error
budget faster than we can afford".

### What this lab actually produced, beyond the checklist

Three things in the lab text do not survive contact with the running system, and all three
are the kind of defect that looks like a working configuration until an incident happens:

1. **The supplied PromQL alerts when nothing is wrong.** An empty numerator makes the whole
   expression empty rather than zero, and `noDataState: NoData` turns that into a page. The
   first version of this rule went to `pending` in 10 seconds on a healthy stack.
2. **The supplied threshold flaps.** 5% sits inside the 3.4–6.1% band that the lab's own
   injection produces, so the alert fired, then sent a `resolved` notification five minutes
   later while the fault was still fully present. A wrong all-clear is worse than no alert.
3. **The supplied hint's arithmetic is wrong.** "Charges are only ~10% of traffic, so overall
   error rate is ~1-2%" — measured, it is 3.4–6.1%, because the purchase branch issues two
   gateway requests and only one of them can fail.

And one finding that came from an accident rather than a plan: a mitigation was issued
that silently did not execute, and the only thing that noticed was the alert refusing to
clear. That is the clearest argument in this whole lab for why the alert exists.
