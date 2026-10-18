# Lab 6 — Alerting & Incident Response

Aleksey Chegaev, CBS-03

---

## Task 1 — Alerts, Runbook and Incident Response (6 pts)

### 6.1 — Application stack and traffic generation

The complete QuickTicket environment was launched from the `app/` directory:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
./loadgen/run.sh 3 3600 &
```

The traffic generator was configured for **3 requests per second** with approximately 70% read requests, 20% reservation requests and 10% purchase-related requests.

The load test started at **09:34:45 GMT+3 on 28 September 2026**. Because the SLO rule uses a 30-minute data window, the required observation period was available from approximately **10:04:45 GMT+3** onward.

### 6.2 — Grafana notification channel

A Grafana contact point named `quickticket-alerts` was configured as a webhook receiver.

* **Contact point:** `quickticket-alerts`
* **Receiver type:** Webhook
* **Endpoint:** `https://webhook.site/7d3b4856-173c-4e3d-aabf-571400e539cd`

The contact point was created through Grafana's provisioning API:

```bash
$ curl -s -b /tmp/gcookies -X POST http://localhost:3000/api/v1/provisioning/contact-points \
    -H 'Content-Type: application/json' -d @contactpoint.json
{"uid":"dfzflvgnd7ocgf","name":"quickticket-alerts","type":"webhook","settings":{"httpMethod":"POST","url":"https://webhook.site/7d3b4856-173c-4e3d-aabf-571400e539cd"},"disableResolveMessage":false}
```

The notification endpoint was also verified during the incident. After the critical alert entered the firing state, the webhook request appeared on webhook.site approximately 30 seconds later, in accordance with the configured notification grouping delay.

### 6.3 — Alert configuration

Two alert rules were created as Grafana-managed rules. Both belong to the `QuickTicket Alerts` folder and the `quickticket-alerts` rule group. The evaluation interval is **one minute**.

#### Alert 1 — QuickTicket High Error Rate

This alert tracks the percentage of gateway requests returning HTTP 5xx:

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m]) or vector(0)) / sum(rate(gateway_requests_total[5m])) * 100
```

The threshold is:

```text
IS ABOVE 5
```

The alert remains pending for **2 minutes** before firing and carries:

```text
severity=critical
```

Annotations:

```text
Summary: Gateway error rate is {{ $value }}%
Description: Error rate exceeded 5% for 2 minutes. Check payments service health.
```

An important issue with the initial expression was discovered during configuration. When no 5xx metrics exist, the numerator can be empty. That makes the complete PromQL expression return no result instead of an explicit zero. Since the rule uses a `NoData` state, the alert could remain stuck in a non-normal state even though the application was healthy.

The following construction avoids that problem:

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m]) or vector(0))
```

It explicitly treats the absence of 5xx series as zero errors.

#### Alert 2 — QuickTicket SLO Burn Rate

The second rule measures how quickly the availability error budget is being consumed:

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

It fires when the calculated burn rate exceeds:

```text
6x
```

The rule has a **5-minute pending period** and uses:

```text
severity=warning
```

Annotations:

```text
Summary: SLO burn rate is {{ $value }}x
Description: Error budget burning at 6x sustainable rate - 30-day budget exhausted in ~5 days.
```

The Grafana API returned the following rule metadata:

```json
Rule 1: {"id":1,"uid":"dfzfnhncqd79cc","folderUID":"bfzflyo8s1ybkb","ruleGroup":"quickticket-alerts","title":"QuickTicket High Error Rate","condition":"B","for":"2m","isPaused":false,"labels":{"severity":"critical"},"provenance":"api"}
Rule 2: {"id":2,"uid":"dfzfnhne4b668d","folderUID":"bfzflyo8s1ybkb","ruleGroup":"quickticket-alerts","title":"QuickTicket SLO Burn Rate","condition":"B","for":"5m","isPaused":false,"labels":{"severity":"warning"},"provenance":"api"}
```

#### Alert tuning during the test

The initial dashboard configuration used a **10-minute query window**. This introduced additional smoothing because the alert evaluation effectively averaged the incident over a longer period.

At **10:45:45 GMT+3**, the measured value was around **4.87%**, while the current 5-minute rate was already approximately **9.7%**.

To make the alert respond to the intended short-term error rate, the query windows were changed to **1 minute** at **10:48:05 GMT+3**, matching the one-minute evaluation cadence. The critical alert subsequently entered Pending during a single evaluation and reached Firing exactly two minutes later.

### 6.4 — Notification routing

The default Grafana notification policy was configured to send the alerts to the new webhook receiver:

```json
{"receiver":"quickticket-alerts","group_by":["alertname"],"group_wait":"30s","group_interval":"5m","repeat_interval":"5m"}
```

Thus, notifications were grouped by alert name, with an initial 30-second wait before sending the grouped notification.

---

## 6.5 — Operational runbook

# Runbook: QuickTicket High Error Rate

> **Working directory:** execute all `docker compose` commands from the `app/` directory.

### Alert conditions

The runbook applies when:

* Gateway 5xx rate is above 5% for at least 2 minutes.
* Grafana dashboard: **QuickTicket — Golden Signals**
* Alert severity: `critical`

### Investigation procedure

**1. Start with the gateway health endpoint**

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
```

The health response identifies the dependencies that the gateway currently considers unavailable.

**2. Inspect the payments service**

```bash
curl -s http://localhost:8082/health
```

**3. Check the events service**

```bash
curl -s http://localhost:8081/health
```

**4. Inspect recent application logs**

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs gateway --tail=20 --since=5m
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs payments --tail=20 --since=5m
```

**5. Quantify the current gateway error rate**

```bash
curl -s 'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total{status=~"5.."}[5m]))/sum(rate(gateway_requests_total[5m]))*100'
```

### Likely failure scenarios

| Failure                               | Diagnostic indication                      | Recovery                                 |
| ------------------------------------- | ------------------------------------------ | ---------------------------------------- |
| Payments container is unavailable     | Gateway health contains `payments: down`   | Start the payments container             |
| Payments is running but charges fail  | Payments logs contain charge errors        | Reset `PAYMENT_FAILURE_RATE` and restart |
| Events service is unavailable         | Health reports `events: down`              | Restart events                           |
| Database connection pool is exhausted | Events logs contain connection/pool errors | Restart events and review `DB_MAX_CONNS` |

### Recovery actions

After confirming the affected dependency, apply the corresponding recovery.

For the payments service:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_FAILURE_RATE=0.0 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

Other failed containers can be restarted with:

```bash
docker compose start <service>
```

The incident is considered mitigated only after all health checks return successfully and the observed 5xx rate begins to fall.

### Post-recovery checks

```bash
curl -s http://localhost:3080/health
```

The expected state is that every dependency is healthy.

Additional confirmation:

* Grafana changes the critical alert back to **Normal**.
* The webhook receiver gets a resolve notification.
* A test purchase completes successfully.

### Escalation

If the problem remains unresolved for 10 minutes, escalate the incident to the instructor or TA.

---

## 6.6 — Failure injection and incident timeline

The failure was introduced in two stages to demonstrate the difference between a partial dependency failure and a complete outage.

### Stage 1 — Partial payment failure

At **10:30:54 GMT+3**, the payments service was recreated with a 50% payment failure rate:

```bash
cd app
PAYMENT_FAILURE_RATE=0.5 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

This produced roughly **4.6% gateway-wide 5xx**, which remained below the configured 5% alert threshold.

During the first hour of traffic, the available ticket inventory was exhausted. At **10:35:17 GMT+3**, the load generator completed its first run and ticket inventory was restored to `100000`. Traffic was restarted at **10:38:28 GMT+3**.

### Stage 2 — Complete payment outage

At **10:40:40 GMT+3**, the payments container was stopped:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
```

This caused the proportion of gateway 5xx responses to rise to approximately 10%, which was sufficient to cross the critical threshold.

### Incident timeline

| Time (GMT+3) | Event                                                                 |
| ------------ | --------------------------------------------------------------------- |
| 10:30:54     | `PAYMENT_FAILURE_RATE=0.5` introduced                                 |
| 10:35:17     | Loadgen finished; ticket inventory was replenished                    |
| 10:38:28     | Loadgen restarted at 3 RPS                                            |
| 10:40:40     | Payments service stopped completely                                   |
| 10:48:55     | `QuickTicket High Error Rate` entered **Pending** at 7.53%            |
| 10:50:55     | `QuickTicket High Error Rate` changed to **Firing** at 5.87%          |
| 10:51:25     | Firing notification received by webhook.site                          |
| 11:00:55     | `QuickTicket SLO Burn Rate` entered **Firing** at approximately 11.3x |
| 11:03:30     | Runbook investigation started                                         |
| 11:03:40     | Payments outage identified                                            |
| 11:03:43     | Payments restart initiated                                            |
| 11:04:11     | Payments recreated with `PAYMENT_FAILURE_RATE=0.0`                    |
| 11:05:55     | High Error Rate alert returned to **Normal**                          |

### Diagnostic evidence

The first health check immediately showed which downstream component was unavailable:

```bash
$ curl -s http://localhost:3080/health
{"status":"degraded","checks":{"events":"ok","payments":"down","circuit_payments":"CLOSED"}}
```

The payments health endpoint confirmed that the container could not be reached:

```bash
$ curl -s --max-time 5 http://localhost:8082/health
<connection refused — payments unreachable>
```

The gateway metrics also showed failures specifically on the payment endpoint:

```bash
$ curl -s 'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total{path="/reserve/{id}/pay"}[5m])) by (status)'
502  20.0
504  14.7
200  0
```

The two observed failure codes are consistent with the gateway handling different upstream connection failure paths against an unavailable payments container.

The combination of:

```text
payments: down
```

in `/health` and the 502/504 responses from `/pay` was enough to identify the root cause without investigating unrelated services.

### Recovery verification

After restarting the payments service:

```bash
$ curl -s http://localhost:8082/health
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

Three purchase requests were then tested:

```bash
$ for i in 1 2 3; do curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:3080/reserve/<id>/pay; done
200
200
200
```

All three returned HTTP 200.

---

## 6.7 — Incident evidence and alert latency

### Recorded sequence

| Time (GMT+3) | Observation                                    |
| ------------ | ---------------------------------------------- |
| 10:30:54     | Partial payment failure introduced             |
| 10:40:40     | Payments outage escalated to complete downtime |
| 10:50:55     | Critical gateway error-rate alert fired        |
| 10:51:25     | Webhook notification arrived                   |
| 11:00:55     | SLO burn-rate alert fired                      |
| 11:03:30     | Investigation started                          |
| 11:03:40     | Root cause confirmed                           |
| 11:04:11     | Clean payments instance running                |
| 11:05:55     | Critical alert resolved                        |

### Failure-to-alert interval

The meaningful start point for threshold crossing is the complete payments outage at **10:40:40 GMT+3**. The critical alert fired at **10:50:55 GMT+3**, giving an interval of approximately **10 minutes 15 seconds**.

The initial partial-failure stage began at 10:30:54, but its measured gateway-wide error rate remained below 5%, so the critical alert was not expected to fire during that phase.

The detection delay comes from several parts of the alert design:

1. The metric uses a **5-minute rate range**, so the effect of a new outage is not immediately represented by the calculated value.
2. The alert has a **2-minute `for` period**, meaning the threshold must persist across evaluations before the state changes from Pending to Firing.
3. Grafana evaluates the rule every **1 minute**, which can add up to one evaluation interval before the state is updated.

Consequently, the alert intentionally favors stability over immediate reaction. Short spikes are less likely to page the operator, but a sustained outage takes longer to become visible.

---

# Task 2 — Blameless Postmortem (4 pts)

## Incident summary

On **28 September 2026**, the QuickTicket payments component became unavailable at **10:40:40 GMT+3** and was restored at **11:04:11 GMT+3**.

During this interval, purchase requests reaching `/pay` could not communicate with the payments service and returned 502 or 504 responses. The resulting gateway-wide 5xx ratio reached approximately 10%.

The critical error-rate alert became active at **10:50:55**, while the SLO burn-rate alert followed at **11:00:55**. Once the incident was investigated, the failing dependency was identified using the gateway health endpoint and the recovery was completed by restarting payments with a zero failure rate.

Read operations and reservation functionality continued to work. The impact was limited to the payment part of the purchase flow.

## User impact

The incident affected approximately 10% of gateway traffic because `/pay` accounts for about one tenth of requests.

The service's availability target was 99.5%, so the sustained error rate consumed the error budget much faster than the normal sustainable rate. The 30-minute burn-rate measurement reached approximately **11.9x**, corresponding to roughly **2.5 days** to consume a 30-day budget at that rate.

The API returned controlled HTTP errors rather than corrupting stored data.

## Root cause

The direct cause of the incident was the complete unavailability of the payments container.

The gateway depends on that service for the final payment operation. Once the payments service was stopped, `/pay` requests returned upstream connection-related 502/504 responses. Because payment traffic represents roughly 10% of the total gateway traffic, a complete outage of that dependency increased the aggregate gateway 5xx rate enough to exceed the 5% alert threshold.

## What worked well

The gateway health endpoint provided dependency-level information instead of only returning a generic degraded status. The first check clearly showed:

```text
payments: down
```

This substantially shortened the investigation.

The runbook was also effective because its first diagnostic branch led directly to the failing component. Restarting payments with:

```bash
PAYMENT_FAILURE_RATE=0.0
```

restored successful payment requests.

The two alerts provided complementary information. The critical error-rate rule reacted first to the active outage, while the burn-rate rule confirmed that the sustained incident was consuming the SLO budget rapidly.

## Problems observed

The first failure-injection stage did not immediately trigger the gateway alert. A 50% payment failure rate resulted in only about 4.6% gateway-wide 5xx, which is below the configured 5% threshold.

The test environment also contained an inventory-related complication. Once all tickets had been consumed, requests failed during reservation with HTTP 409 and therefore never reached the payment endpoint. This temporarily hid the injected payments failures from the gateway 5xx metric.

Another issue came from the alerting query configuration. A 10-minute window initially smoothed the increasing error rate and delayed the threshold crossing. Reducing the window to one minute made the alert output much more representative of the current incident.

## Action items

### 1. Monitor payments independently

Add a dedicated alert for the payments service, based on its own charge success/failure metrics. For example:

```promql
payments_charges_total{result="failed"}
```

The purpose is to detect a payments-specific problem without waiting for the aggregate gateway ratio to become large enough.

**Owner:** on-call rotation
**Effort:** small

### 2. Keep query ranges aligned with evaluation frequency

Avoid using a long query range when the operational goal is to react quickly to a short-lived increase in errors. With a one-minute evaluation interval, a one-minute alert window gives a much more direct representation of the current state.

**Owner:** alert owner
**Effort:** trivial

### 3. Simplify alert notification templates

Use a normal numeric template for the error-rate annotation so the webhook message displays a readable percentage such as:

```text
6.53%
```

instead of exposing Grafana's internal representation.

**Owner:** alert owner
**Effort:** trivial

### 4. Prepare the test environment before failure drills

The load-testing environment should start with enough ticket inventory, or the test procedure should explicitly verify that reservations are still reaching the payment stage.

**Owner:** lab environment owner
**Effort:** small

## Main improvement identified

The most significant improvement is the **service-specific payments alert**.

The incident demonstrated that a dependency may be seriously broken while an aggregate gateway metric remains below its alert threshold. In this scenario, approximately half of payment attempts failed during the first injection stage, yet the gateway-wide value was still only around 4.6%.

An alert based directly on the payments service would remove this dependency on traffic share and reduce the amount of time between the start of the outage and its detection.

---

# Bonus Task — Cross-Tested Runbook (2 pts)

## B.1 — Redis failure runbook

# Runbook: Redis Failure Affecting Reservations and Purchases

> **Working directory:** use the `app/` directory for all `docker compose` commands.

## Symptoms

A Redis outage can produce two different behaviors depending on when the Redis failure happens.

### Scenario A — Redis stops after events is already running

The events service may block while trying to communicate with Redis. A reservation request can then remain open until the gateway returns:

```json
{"detail":"Events service timeout"}
```

### Scenario B — events starts while Redis is unavailable

The events service can start without an active Redis client. In this case a reservation may still return HTTP 200, but no Redis hold is created. A later payment request can then return:

```text
404 Reservation not found or expired
```

This makes the failure mode different from a normal gateway 5xx outage.

Normal event reads such as:

```text
GET /events
GET /events/{id}
```

can continue working because they do not depend on Redis in the same way.

## Diagnosis

### 1. Inspect the gateway health status

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
```

Pay particular attention to:

```text
"events": "down"
```

or:

```text
"events": "degraded"
```

A `down` result can occur when the gateway's health probe times out while events is blocked.

### 2. Query events health with a timeout

Do not execute the request without a timeout because the Redis call may block:

```bash
curl -s --max-time 5 http://localhost:8081/health
```

Possible warning signs are:

```json
{"status":"degraded","checks":{"postgres":"ok","redis":"down"}}
```

or simply no response before the five-second timeout.

### 3. Exercise the reservation/payment path

```bash
curl -s --max-time 8 -X POST \
  -H 'Content-Type: application/json' \
  -d '{"quantity":1}' \
  http://localhost:3080/events/1/reserve
```

Then, when a reservation ID exists:

```bash
curl -s --max-time 8 -X POST \
  http://localhost:3080/reserve/<reservation_id>/pay
```

Interpretation:

* Scenario A: the reservation request can time out with `Events service timeout`.
* Scenario B: reservation succeeds but payment returns 404 because no Redis hold was created.

### 4. Look for Redis-related events logs

```bash
docker compose logs events --tail=30 --since=10m | grep -i redis
```

In Scenario B, a useful indicator is:

```text
Redis unavailable — reservation not held
```

### 5. Check the Redis container state

Stopped containers are not necessarily visible with the standard `docker compose ps`, so use:

```bash
docker compose ps -a --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}' | grep -i redis
```

Recent Redis logs:

```bash
docker compose logs redis --tail=20
```

Relevant states include:

```text
Exit
restarting
OOM-killed
```

## Common causes

| Cause                                   | Evidence                           | Recovery                               |
| --------------------------------------- | ---------------------------------- | -------------------------------------- |
| Redis container stopped or crashed      | Redis appears as `Exit` in `ps -a` | Start Redis                            |
| Redis killed because of memory pressure | Redis logs indicate OOM            | Restart Redis and review memory limits |

## Recovery procedure

First bring Redis back:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start redis
```

When the events service had started while Redis was already unavailable, it may require a restart:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d events
```

This distinction matters because an already-running Redis client can reconnect after a later Redis outage, while a service initialized without a Redis client may remain degraded until it is restarted.

Finally, run a complete reserve → pay flow and confirm that Redis is healthy.

## Verification

```bash
curl -s --max-time 5 http://localhost:8081/health
```

Expected result:

```json
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
```

A full purchase sequence should also complete successfully.

---

## B.2 — Cross-test evaluation

The Redis runbook was tested in two stages: first by performing a self-test, then by exchanging the procedure with a classmate.

### Self-test

The self-test took place approximately from **11:22:45 to 11:24:45 GMT+3**.

At **11:23:23**, Redis was stopped:

```bash
docker compose stop redis
```

The runbook was then followed from start to finish.

Measured results:

* Root cause identified in **64 seconds**.
* Full recovery completed in **1 minute 44 seconds**.

Three weaknesses were discovered during this test.

First, the gateway health result was `events: down`, because the health probe timed out. The runbook was updated to explicitly document both `down` and `degraded`.

Second, an unrestricted request to:

```bash
curl http://localhost:8081/health
```

could block the terminal because the Redis client had no socket timeout. All relevant health requests were therefore changed to use `--max-time`.

Third, the command:

```bash
docker compose ps redis
```

did not display the stopped Redis container. The procedure was changed to use `docker compose ps -a`.

### Classmate cross-test

The second test began at **13:07:37 GMT+3**, with Redis stopped without informing the tester about the exact failure.

The classmate initially attempted to run the recovery command from the repository root. It failed because the compose file was located under `app/`.

The rest of the diagnosis proceeded as follows:

| Time (GMT+3) | Tester action                         | Observation                                    |
| ------------ | ------------------------------------- | ---------------------------------------------- |
| 13:07:50     | Copied recovery command               | Failed: `docker-compose.yaml: no such file`    |
| 13:07:55     | Changed directory to `app/`           | Command worked                                 |
| 13:08:01     | Checked gateway health                | `events: down`                                 |
| 13:08:12     | Ran events health with `--max-time 5` | Timed out after 5 seconds                      |
| 13:08:23     | Reproduced reservation/purchase path  | `Events service timeout`                       |
| 13:08:35     | Checked logs and Redis with `ps -a`   | Redis shown as `Exited (0)`                    |
| 13:08:48     | Started Redis                         | Redis recovered                                |
| 13:08:48     | Verification                          | `redis: ok`; three purchase tests returned 200 |

The classmate completed the incident response using only the runbook.

Measured results:

* **58 seconds** from injection to root-cause identification.
* **1 minute 11 seconds** from injection to recovery.

### Changes made after the cross-test

The most significant usability issue was the working-directory assumption. The tester naturally copied a command from the runbook while standing in the repository root, which caused an immediate compose-file error.

To prevent this, a prominent note was added at the top of both runbooks:

> **Working directory:** run all `docker compose` commands from the `app/` directory.

### Final runbook test results

| Metric                         | Self-test                                                  | Classmate test                                      |
| ------------------------------ | ---------------------------------------------------------- | --------------------------------------------------- |
| Solved using only the runbook  | Yes                                                        | Yes                                                 |
| Injection → fix                | 1m 44s                                                     | 1m 11s                                              |
| Main missing detail discovered | Health requests could hang; stopped containers needed `-a` | Working directory was unclear                       |
| Result after update            | Added timeouts and `ps -a`                                 | Added explicit `app/` working-directory instruction |

Overall, the cross-test confirmed that the runbook was sufficient to identify and recover from the Redis failure, while the test also exposed practical usability issues that were corrected before the final version.
