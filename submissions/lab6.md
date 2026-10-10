# Lab 6 — Alerting & Incident Response

**Date:** 2026-09-27  
**Branch:** `feature/lab6`  
**Environment:** local Docker Compose stack  
**Time zone for evidence:** UTC

The monitoring stack uses [`../monitoring/prometheus/prometheus.yml`](../monitoring/prometheus/prometheus.yml) to scrape gateway, events, and payments every 15 seconds. The Compose file references this configuration, so it is included with the submission for a fresh checkout.

## Task 1 — Alerts, Runbook, and Incident Response

### Runbook: QuickTicket High Error Rate

Run from `app/` and use both Compose files for all service operations:

```bash
DC=(docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml)
```

#### Alert

- **Fires when:** gateway 5xx error rate over 5 minutes is above 5% for 2 minutes.
- **Evaluation interval:** 1 minute.
- **Severity:** `critical`.
- **Dashboard:** [QuickTicket — Golden Signals](http://localhost:3000/d/quickticket-golden-signals).
- **Expected impact:** failures in downstream services can prevent users from completing purchases.

#### Diagnosis

1. Check gateway health:

   ```bash
   curl -sS --max-time 5 http://localhost:3080/health | python3 -m json.tool
   ```

2. Check payments directly:

   ```bash
   curl -sS --max-time 5 http://localhost:8082/health
   ```

3. Check events directly:

   ```bash
   curl -sS --max-time 5 http://localhost:8081/health
   ```

4. Check recent gateway and payments logs:

   ```bash
   "${DC[@]}" logs gateway --tail=20 --since=5m
   "${DC[@]}" logs payments --tail=20 --since=5m
   ```

5. Check the payments container state:

   ```bash
   "${DC[@]}" ps -a payments
   ```

6. If payments is running, inspect its configured failure rate:

   ```bash
   "${DC[@]}" exec -T payments printenv PAYMENT_FAILURE_RATE
   ```

7. Check the High Error Rate PromQL in Grafana Explore. A healthy `/health` response alone does not prove that purchase requests are succeeding.

#### Common Causes

| Cause | How to identify | Mitigation |
|---|---|---|
| Payments service unavailable | Gateway reports payments down, connection errors, stopped container | Start payments with the normal failure rate |
| Payments returns charge failures | Health is OK, charge requests fail, non-zero `PAYMENT_FAILURE_RATE` | Restore `PAYMENT_FAILURE_RATE=0.0` |
| Events service unavailable | Gateway reports events down, events-related errors | Restore events and check its dependencies |
| Database connection exhaustion | Events logs show pool/connection errors | Check PostgreSQL and `DB_MAX_CONNS`, then restart events after correcting the cause |

#### Mitigation

For a payments fault:

```bash
"${DC[@]}" stop payments
PAYMENT_FAILURE_RATE=0.0 "${DC[@]}" up -d payments
```

#### Verification

After mitigation:

1. Repeat gateway, payments, and events health checks.
2. Execute a real reserve/pay transaction and confirm HTTP 200 with a confirmed order.
3. Keep traffic running and observe the 5-minute gateway error rate falling.
4. Wait until the High Error Rate alert returns to `Normal`.
5. Confirm that the resolved notification is received.

#### Escalation

If the incident is not mitigated within 10 minutes, escalate to the instructor/TA with timestamps, health output, logs, container state, and the alert query.

---

## Alert Configuration

Both rules are Grafana-managed in the `QuickTicket Lab 6` folder and are evaluated every 60 seconds. The exported rules are available in [`assets/lab6/alert-rules.json`](assets/lab6/alert-rules.json).

### Alert 1 — QuickTicket High Error Rate

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

Configuration:

- **Condition:** above `5`.
- **Pending period:** `2m`.
- **Severity:** `critical`.
- **Summary:** `Gateway error rate is {{ $value }}%`.
- **Description:** `Error rate exceeded 5% for 2 minutes. Check payments service health.`

### Alert 2 — QuickTicket SLO Burn Rate

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

Configuration:

- **Condition:** above `6`.
- **Pending period:** `5m`.
- **Severity:** `warning`.
- **SLO:** 99.5% availability.

A sustained 6x burn rate would consume a 30-day error budget in roughly 5 days. During this experiment the 30-minute window initially contained less than 30 minutes of fresh samples, so early values represented only the history available at that time.

The SLO Burn Rate alert also fired during the experiment. Its notification was received at `11:10:45.031 UTC`. Because it uses a 30-minute window, it remained affected by earlier errors after the critical alert recovered.

### Contact Point and Notification Policy

Contact point:

- **Name:** `quickticket-alerts`.
- **Type:** Webhook.
- **Receiver:** `http://webhook:9000` inside the Compose network.
- **Resolved notifications:** enabled.

Notification policy:

- **Group by:** `alertname`.
- **Group wait:** `30s`.
- **Group interval:** `1m`.
- **Repeat interval:** `5m`.

Evidence:

- [Contact point export](assets/lab6/contact-point.json)
- [Notification policy export](assets/lab6/notification-policy.json)
- [Received webhook payloads](assets/lab6/notifications.jsonl)

A test notification was received at `11:00:00.185 UTC`. The real High Error Rate firing notification was received at `11:07:40.039 UTC`, and its resolved notification was received at `11:12:40.042 UTC`.

---

## Incident Simulation

The initial failure injection followed the assignment scenario:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_FAILURE_RATE=0.5 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

Background traffic remained active during the experiment.

After approximately two minutes, the measured aggregate gateway 5xx rate was only **0.1423%**, which was below the configured 5% threshold. This happened because payment requests are only part of total gateway traffic, so a 50% payment failure rate did not translate into a 50% aggregate gateway failure rate.

To create a failure strong enough to exercise the unchanged alert threshold, payments was stopped completely and valid reserve/pay traffic was generated. The alert configuration was not changed.

The measured peak gateway 5xx percentage reached **18.05%**.

### Alert Lifecycle Evidence

The alert moved through the expected states:

- [NoData snapshot](assets/lab6/state-nodata.json)
- [Pending snapshot](assets/lab6/state-pending.json)
- [Firing snapshot](assets/lab6/state-alerting.json)
- [Normal snapshot](assets/lab6/state-normal.json)

The initial `NoData` state occurred before a 5xx series existed for the exact query. It was not counted as incident detection. The actual High Error Rate webhook contains `status=firing`, `state=alerting`, and the `QuickTicket High Error Rate` alert name.

### Diagnosis and Recovery

After the alert entered `Firing`, the runbook checks showed:

- Gateway health returned HTTP 503 and reported payments down.
- Payments refused connections.
- Events remained healthy with PostgreSQL and Redis OK.
- `docker compose ps -a payments` showed the payments container stopped.
- Gateway logs contained payment-related 502 responses.

Evidence:

- [Health checks](assets/lab6/diagnosis-health.json)
- [Gateway logs](assets/lab6/diagnosis-gateway.log)
- [Payments logs](assets/lab6/diagnosis-payments.log)
- [Container status](assets/lab6/diagnosis-ps.log)

Payments was restored with `PAYMENT_FAILURE_RATE=0.0`. Gateway health returned HTTP 200, and a real reserve/pay transaction returned HTTP 200 with a confirmed order. The transaction and action timestamps are recorded in [`assets/lab6/timeline.jsonl`](assets/lab6/timeline.jsonl).

### Timeline

All timestamps are UTC on 2026-09-27.

| Time | Event |
|---|---|
| 11:01:43.725 | 50% payment failure rate applied |
| 11:03:44.972 | Payments stopped; valid purchase traffic added |
| 11:05:10.000 | Grafana evaluation: `Pending` |
| 11:07:10.000 | Grafana evaluation: `Firing` |
| 11:07:14.058 | Firing state observed |
| 11:07:14.059 | Runbook diagnosis started |
| 11:07:14.403 | Payments outage identified from health/logs/container state |
| 11:07:14.403 | Mitigation started |
| 11:07:15.258 | Payments restored with failure rate `0.0` |
| 11:07:16.319 | Gateway health returned healthy |
| 11:07:16.360 | Real purchase confirmed, HTTP 200 |
| 11:07:40.039 | Firing webhook received |
| 11:12:10.000 | Grafana evaluation: `Normal` |
| 11:12:17.651 | Normal state observed |
| 11:12:40.042 | Resolved webhook received |

### How long from failure injection to alert firing? Why the delay?

From the initial 50% failure injection to the Grafana `Firing` evaluation, the delay was **5m 26.275s**.

The initial partial failure did not immediately push the aggregate gateway 5xx rate above 5%. After payments was stopped completely, the delay from the stronger outage to `Firing` was **3m 25.027s**. The transition from the first `Pending` evaluation to `Firing` was exactly **2 minutes**.

The delay came from several factors:

- the alert uses a rolling 5-minute error-rate window;
- metrics are scraped every 15 seconds;
- the rule is evaluated every 1 minute;
- the condition must remain true for the 2-minute pending period;
- evaluation alignment adds additional delay depending on when the failure begins.

The webhook arrived approximately 30 seconds after the `Firing` evaluation because the notification policy has a 30-second group wait. This notification delay is separate from the alert firing delay.

Service recovery and alert resolution were also separate events: after payments recovered, historical errors remained in the 5-minute query window until the alert returned to `Normal`.

---

# Task 2 — Blameless Postmortem

## Postmortem: QuickTicket Payment Dependency Outage

**Date:** 2026-09-27  
**Duration:** 11:01:43.725 → 11:07:16.319 UTC; alert returned to Normal at 11:12:17.651 UTC  
**Severity:** SEV-2 for the simulated exercise  
**Author:** kujifined

### Summary

The payments dependency first returned injected charge failures and was then made unavailable to produce a sustained incident. Purchase requests through the gateway returned 5xx responses, which raised the aggregate gateway error rate above the configured threshold. The critical alert fired, the payments failure was identified through health checks and logs, payments was restored, and a successful purchase confirmed recovery.

This was a local exercise and did not affect production users.

### Timeline

| Time UTC | Event |
|---|---|
| 11:01:43.725 | 50% payment failure rate applied |
| 11:03:44.972 | Payments stopped |
| 11:05:10.000 | High Error Rate entered Pending |
| 11:07:10.000 | High Error Rate entered Firing |
| 11:07:14.059 | Investigation started |
| 11:07:14.403 | Payments outage identified |
| 11:07:15.258 | Payments restored |
| 11:07:16.360 | Successful purchase confirmed |
| 11:12:10.000 | High Error Rate evaluated Normal |
| 11:12:40.042 | Resolved webhook received |

### Root Cause

Purchase completion depends directly on the payments service. When payments became unavailable, the gateway propagated downstream connection failures as 502 responses, so a single dependency outage directly reduced purchase availability.

The aggregate High Error Rate alert also uses all gateway requests as its denominator. Reads, reservation requests, health checks, and other successful traffic diluted the initial payment-failure signal. As a result, a 50% failure probability inside the payments service produced only a small aggregate gateway 5xx rate and did not immediately cross the 5% alert threshold.

The failure injection was the trigger for the exercise; the postmortem focuses on the service behavior, monitoring characteristics, and response process rather than individual blame.

### What Went Well

- The runbook existed before the failure was injected.
- Health checks, logs, and container state identified payments as the failed dependency while events remained healthy.
- The configured 5% threshold and 2-minute pending period were kept unchanged during the incident.
- The webhook receiver recorded the real firing and resolved notifications.
- Recovery was verified with both health checks and a successful end-to-end reserve/pay transaction.
- The alert was observed returning to `Normal` after the 5-minute error window drained.

### What Went Wrong

- The original 50% payment failure injection was not enough to cross the aggregate 5% gateway error threshold.
- Before the first gateway 5xx series existed, the exact error query returned no data and Grafana produced a `DatasourceNoData` alert.
- The load generator reports all unsuccessful responses, including HTTP 409 business rejections, so its displayed failure percentage cannot be used directly as the SLO 5xx percentage.
- The SLO burn-rate rule uses a 30-minute window, so early evaluation during a short local experiment represents only the samples available at that time.
- The monitoring stack required a Prometheus configuration fix before the exercise could run correctly.

### Action Items

| Action | Owner | Priority |
|---|---|---|
| Add a purchase/payment-path availability alert with its own request denominator and test it with sparse payment traffic | kujifined | High |
| Make the High Error Rate query zero-safe when no 5xx series exists, while separately alerting on missing telemetry | kujifined | High |
| Add an end-to-end synthetic reserve/pay check | kujifined | High |
| Make failure scenarios and traffic profiles reproducible and separate 4xx business rejections from 5xx failures in test reporting | kujifined | Medium |
| Configure an external paging receiver before production use | kujifined | Medium |
| Keep runbooks peer-tested and update ambiguous recovery steps after each test | kujifined | Medium |

### Most Important Action Item

The most important action item is adding a dedicated purchase/payment-path availability alert. The experiment showed that a low-volume but business-critical path can be severely degraded while the aggregate gateway error rate remains below its threshold because other requests continue succeeding. A dedicated denominator would detect this failure mode earlier and more directly.

---

# Bonus Task — Cross-Tested Runbook

## Failure Mode

**Redis unavailable → reservation/purchase flow degraded**

The peer test used the original handout preserved at [`assets/lab6/peer-test-2/runbook-as-tested.md`](assets/lab6/peer-test-2/runbook-as-tested.md). Redis was stopped before the recorded peer session began.

The recorded session shows that the participant:

1. checked gateway and events health;
2. identified Redis as down while PostgreSQL remained healthy;
3. confirmed the Redis container was stopped;
4. inspected events logs and observed Redis connection errors;
5. started Redis and confirmed `PONG`;
6. confirmed gateway and events health returned healthy;
7. completed a reserve/pay transaction successfully.

### Peer-Test Result

| Time UTC | Event |
|---|---|
| 21:21:29 | Recorded peer session started |
| During session | Redis identified as the failed dependency |
| During session | Redis started and `PONG` confirmed |
| During session | Gateway/events health returned healthy |
| 21:22:06 | Order `b9e5d88e-53b1-4bb4-bb1a-12b9d8e570f8` confirmed |
| 21:22:09 | Recorded peer session ended |

**Result:** the core recovery succeeded using the runbook. Time from the recorded start to a confirmed purchase was **37 seconds**, and the total recorded session lasted **40 seconds**.

Evidence:

- [Terminal transcript](assets/lab6/peer-test-2/terminal.log)
- [Baseline](assets/lab6/peer-test-2/baseline.json)
- [Verification](assets/lab6/peer-test-2/verification.json)

The recorded session completed the dependency diagnosis, Redis recovery, health verification, and successful purchase. Dashboard verification was not completed during that session.

### Post-Test Runbook Improvements

The runbook was revised after the test to make recovery instructions clearer and more complete:

| Gap found during review | Revision |
|---|---|
| Dashboard verification was not completed and the original handout did not provide a direct dashboard URL or explicit metric recovery criteria | Added the dashboard URL, time range, refresh guidance, and concrete recovery checks |
| Verification used a fixed event ID and did not explain what to do if that event could not be reserved | Added `/events` discovery and instructions to select another event after HTTP 409 |
| The instruction to restart events did not define how long to wait for Redis reconnection or when a restart was actually necessary | Added a 15-second reconnection window, repeated dependency health checks, and explicit restart conditions |

The revised runbook is below.

## Runbook: QuickTicket Reservation Failures / Redis Unavailable

### Alert / Symptoms

Use the [QuickTicket — Golden Signals](http://localhost:3000/d/quickticket-golden-signals) dashboard together with failed reservation or purchase requests.

The aggregate High Error Rate alert is not guaranteed to detect this failure immediately because this implementation can accept a reservation request even when Redis cannot persist the hold. Symptoms can therefore include inconsistent reservations or later payment failures.

### Diagnosis

From `app/`:

```bash
DC=(docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml)

curl -sS --max-time 5 http://localhost:3080/health
curl -sS --max-time 5 http://localhost:8081/health
"${DC[@]}" ps -a redis events
"${DC[@]}" logs events --tail=40 --since=5m
"${DC[@]}" exec -T redis redis-cli ping
```

A stopped Redis container, failed Redis ping, or Redis connection errors in events logs identify the Redis failure mode. Check PostgreSQL separately so that a database failure is not mistaken for a Redis problem.

### Common Causes

| Cause | Evidence | Mitigation |
|---|---|---|
| Redis stopped | `ps` shows Redis exited; ping cannot execute | Start Redis |
| Redis unreachable | Redis container is running but events logs show connection failures | Check the Compose network and `REDIS_HOST` / `REDIS_PORT` |
| Events does not reconnect after Redis recovery | Redis answers `PONG`, but events continues to report Redis down | Inspect fresh events logs/configuration and restart events only if necessary |

### Mitigation

```bash
"${DC[@]}" start redis
"${DC[@]}" exec -T redis redis-cli ping
```

Expected result:

```text
PONG
```

After Redis returns `PONG`, allow up to 15 seconds for reconnection and cached health status to refresh. Check events health every 5 seconds for three attempts:

```bash
curl -sS --max-time 5 http://localhost:8081/health
```

Expected state:

- HTTP 200;
- `postgres: ok`;
- `redis: ok`.

If Redis returns `PONG` but events still reports Redis down after these checks, inspect fresh logs and Redis connection configuration:

```bash
"${DC[@]}" logs events --tail=40 --since=1m
```

After correcting any network/configuration issue, restart events only if it still has not reconnected:

```bash
"${DC[@]}" restart events
```

Do not restart events just because PostgreSQL is unhealthy; diagnose that dependency separately. Do not delete Redis or PostgreSQL volumes as a connectivity fix.

### Verification

Repeat gateway and events health checks.

List events:

```bash
curl -sS --max-time 5 http://localhost:3080/events | python3 -m json.tool
```

Choose an event with `available > 0`. If a reservation returns HTTP 409, choose another available event.

Reserve one ticket:

```bash
curl -sS --max-time 5 -X POST \
  -H 'Content-Type: application/json' \
  -d '{"quantity":1}' \
  http://localhost:3080/events/EVENT_ID/reserve
```

Use the returned reservation ID to pay:

```bash
curl -sS --max-time 5 -X POST \
  http://localhost:3080/reserve/RESERVATION_ID/pay
```

Recovery is confirmed when:

- Redis returns `PONG`;
- gateway and events health return HTTP 200;
- events reports both PostgreSQL and Redis healthy;
- a new reserve/pay transaction returns a confirmed order;
- fresh events logs show no new Redis connection errors;
- under background traffic, Golden Signals return to their normal state after metric windows drain.

If the High Error Rate alert fired during the incident, wait for it to return to `Normal`. A burn-rate alert may remain elevated longer because its query uses a 30-minute window.

### Escalation

If the problem is not mitigated within 10 minutes, escalate to the instructor/TA with dependency health, container status, timestamps, and recent logs.

---

## Evidence Index

- [`assets/lab6/alert-rules.json`](assets/lab6/alert-rules.json) — exported Grafana alert rules.
- [`assets/lab6/contact-point.json`](assets/lab6/contact-point.json) — contact point configuration.
- [`assets/lab6/notification-policy.json`](assets/lab6/notification-policy.json) — notification routing.
- [`assets/lab6/notifications.jsonl`](assets/lab6/notifications.jsonl) — received test, firing, burn-rate, and resolved notifications.
- [`assets/lab6/state-pending.json`](assets/lab6/state-pending.json) — High Error Rate pending state.
- [`assets/lab6/state-alerting.json`](assets/lab6/state-alerting.json) — High Error Rate firing state.
- [`assets/lab6/state-normal.json`](assets/lab6/state-normal.json) — recovered state.
- [`assets/lab6/timeline.jsonl`](assets/lab6/timeline.jsonl) — incident timestamps and recovery verification.
- [`assets/lab6/diagnosis-health.json`](assets/lab6/diagnosis-health.json) — dependency health during the incident.
- [`assets/lab6/diagnosis-gateway.log`](assets/lab6/diagnosis-gateway.log) — gateway logs.
- [`assets/lab6/diagnosis-payments.log`](assets/lab6/diagnosis-payments.log) — payments logs.
- [`assets/lab6/diagnosis-ps.log`](assets/lab6/diagnosis-ps.log) — payments container state.
- [`assets/lab6/peer-test-2/terminal.log`](assets/lab6/peer-test-2/terminal.log) — recorded Redis peer-test session.
- [`assets/lab6/peer-test-2/verification.json`](assets/lab6/peer-test-2/verification.json) — post-test verification.
