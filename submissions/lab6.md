# Lab 6 — Alerting & Incident Response

## Environment and scope

QuickTicket ran through Docker Compose with Prometheus and Grafana.
The Lab 3 Prometheus configuration was reused locally. All incident
timestamps below are in UTC. The payment outage was an intentional
local exercise; there was no external customer traffic.

## Task 1 — Alerts and incident response

### Grafana alert rules

Both Grafana-managed rules belong to the `quickticket-slo` rule group
in `Lab 6 QuickTicket`. They evaluate every 60 seconds. Their
no-data state is `OK`, because the gateway initially had no 5xx metric
series. Query execution errors retain the `Error` state.

**Critical: QuickTicket High Error Rate.** Threshold: above `5`; pending period: `2m`; label: `severity=critical`; no-data state: `OK`.

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

**Warning: QuickTicket SLO Burn Rate.** Threshold: above `6`; pending period: `5m`; label: `severity=warning`; no-data state: `OK`.

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

### Contact point and notification policy

The contact point is a Webhook to a local receiver on the Mac.
Grafana reaches it through `host.docker.internal`. The receiver
recorded test, firing, and resolved notifications.

```json
[
  {
    "uid": "lab6-webhook",
    "name": "quickticket-alerts",
    "type": "webhook",
    "settings": {
      "httpMethod": "POST",
      "url": "http://host.docker.internal:19098/lab6"
    }
  }
]
```

```json
{
  "receiver": "quickticket-alerts",
  "group_by": [
    "alertname"
  ],
  "group_wait": "30s",
  "group_interval": "5m",
  "repeat_interval": "5m"
}
```

Grafana's **Test** action delivered `TestAlert` at `2026-09-27T16:54:24.857127+00:00`. An earlier `DatasourceNoData` notification occurred before a 5xx series existed; the rule's no-data state was corrected before fault injection.

### High Error Rate runbook

# Runbook: QuickTicket High Error Rate

## Alert

- Fires when gateway 5xx responses exceed 5% of gateway requests for 2 minutes.
- Evaluation interval: 1 minute; query window: 5 minutes.
- Severity: critical.
- Dashboard: QuickTicket — Golden Signals in Grafana at http://localhost:3000.
- Contact point: quickticket-alerts.

## Diagnosis

Run commands from the repository root. Record the UTC time of each observation.

1. Confirm the alert and current error ratio in Grafana. Check whether the rule is Pending or Firing.
2. Check gateway dependency health:
   `curl -sS http://localhost:3080/health | python3 -m json.tool`
3. Check payments and events directly:
   `curl -sS http://localhost:8082/health | python3 -m json.tool`
   `curl -sS http://localhost:8081/health | python3 -m json.tool`
4. Check recent service logs:
   `docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml logs --since=5m --tail=50 gateway payments events`
5. Check deployment state:
   `docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml ps`
6. Compare the Grafana error-rate panel with the gateway's 5xx logs. Identify which request path and dependency failed.

## Common causes and mitigation

| Cause | Evidence | Mitigation |
|------|----------|------------|
| Payments container stopped | Gateway health says payments down; Compose shows payments stopped | `PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0 docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml up -d --force-recreate payments` |
| Injected payment failures | Payments health reports nonzero `failure_rate`; payments logs contain injected failures | Recreate payments with `PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0` using the command above |
| Events container stopped | Gateway health says events down; `/events` returns 502 or 504 | `docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml start events` |
| Database unavailable or pool exhausted | Events health reports postgres down or events logs show pool errors | Check PostgreSQL health and `DB_MAX_CONNS`; restore database connectivity before restarting events |

## Verification

1. Confirm payments and events health return `healthy`.
2. Confirm gateway `/health` reports both dependencies `ok`.
3. Confirm the Prometheus 5-minute gateway 5xx ratio drops below 5%.
4. Wait for Grafana rule to return to Normal and verify a resolved webhook notification.
5. Record recovery and alert-resolution times separately. A rolling window can delay resolution after the service is healthy.

## Escalation

If the error rate remains above threshold for 10 minutes after mitigation, or the cause is unclear, notify the instructor or TA with alert time, health outputs, failing request paths, recent logs, and actions already taken.

### Fault injection and alert evidence

Before injection, Prometheus observed HTTP 200 and 409 gateway
responses. The load generator counted reservation conflicts (409)
as failed operations, so its failed-operation percentage was not
used as the gateway 5xx SLI. A valid reservation was created before
payments stopped. Focused payment requests then returned HTTP 502.

```text
200 2.056118707522377 req/s
409 0.5403451893488841 req/s
```

```json
{
  "reservation_id": "279ff32d-401c-46bb-b2ab-cfec9b08045f",
  "event_id": 3,
  "quantity": 1,
  "total_cents": 15000,
  "expires_in_seconds": 300
}
```

Grafana recorded the High Error Rate rule in the Firing state:

```json
{
  "name": "QuickTicket High Error Rate",
  "state": "firing",
  "health": "ok",
  "alerts": [
    {
      "state": "Alerting",
      "labels": {
        "alertname": "QuickTicket High Error Rate",
        "grafana_folder": "Lab 6 QuickTicket",
        "severity": "critical"
      }
    }
  ]
}
```

The incident and resolved webhooks reached the receiver:

```json
[
  {
    "received_at": "2026-09-27T17:03:25.054690+00:00",
    "status": "firing",
    "title": "[FIRING:1] QuickTicket High Error Rate (Lab 6 QuickTicket critical)"
  },
  {
    "received_at": "2026-09-27T17:13:25.068213+00:00",
    "status": "resolved",
    "title": "[RESOLVED] QuickTicket High Error Rate (Lab 6 QuickTicket critical)"
  }
]
```

Following the runbook showed a degraded gateway, healthy Events, and a stopped payments container:

```json
{
  "status": "degraded",
  "checks": {
    "events": "ok",
    "payments": "down",
    "circuit_payments": "CLOSED"
  }
}
```

```json
{
  "status": "healthy",
  "checks": {
    "postgres": "ok",
    "redis": "ok"
  }
}
```

```text
Direct payments health: connection failed (HTTP 000).
app-payments-1     app-payments              "uvicorn main:app --…"   payments     19 minutes ago   Exited (0) 5 minutes ago
```

```text
gateway-1  | 2026-09-27T17:05:02.979073085Z {"time":"2026-09-27 17:05:02,978","level":"ERROR","service":"gateway","msg":"payment error: [Errno -2] Name or service not known"}
gateway-1  | 2026-09-27T17:05:02.979805793Z INFO:     151.101.128.223:60699 - "POST /reserve/279ff32d-401c-46bb-b2ab-cfec9b08045f/pay HTTP/1.1" 502 Bad Gateway
gateway-1  | 2026-09-27T17:05:04.046525918Z {"time":"2026-09-27 17:05:04,046","level":"ERROR","service":"gateway","msg":"payment error: [Errno -2] Name or service not known"}
gateway-1  | 2026-09-27T17:05:04.047193668Z INFO:     151.101.128.223:43319 - "POST /reserve/279ff32d-401c-46bb-b2ab-cfec9b08045f/pay HTTP/1.1" 502 Bad Gateway
gateway-1  | 2026-09-27T17:05:05.110197336Z {"time":"2026-09-27 17:05:05,110","level":"ERROR","service":"gateway","msg":"payment error: [Errno -2] Name or service not known"}
gateway-1  | 2026-09-27T17:05:05.110682086Z INFO:     151.101.128.223:16893 - "POST /reserve/279ff32d-401c-46bb-b2ab-cfec9b08045f/pay HTTP/1.1" 502 Bad Gateway
```

Focused failed-payment traffic was stopped. Payments was recreated
with `PAYMENT_FAILURE_RATE=0.0` and `PAYMENT_LATENCY_MS=0`.
Payment and gateway health then returned `healthy`.

```json
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

```json
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

```json
{
  "name": "QuickTicket High Error Rate",
  "state": "inactive",
  "health": "ok"
}
```

### Incident timeline (UTC)

| Time | Event |
|------|-------|
| 2026-09-27T16:59:49Z | Payments stopped; failed-payment traffic started. |
| 2026-09-27T17:00:56Z | High Error Rate first observed Pending. |
| 2026-09-27T17:02:58Z | High Error Rate first observed Firing. |
| 2026-09-27T17:03:25.054690+00:00 | Incident webhook received. |
| 2026-09-27T17:05:10Z | Runbook diagnosis began. |
| 2026-09-27T17:05:10Z | Payments outage identified. |
| 2026-09-27T17:05:10Z | Failed-payment traffic stopped; payments restored. |
| 2026-09-27T17:05:13Z | Payments and gateway healthy. |
| 2026-09-27T17:09:58Z | High Error Rate returned to Normal. |
| 2026-09-27T17:13:25.068213+00:00 | Resolved webhook received. |

**Injection to Firing:** 189 seconds (3.15 minutes). The 5-minute rate first had to exceed 5%; Grafana evaluated once per minute and required the condition for 2 minutes. The firing webhook arrived 27.1 seconds later, consistent with the 30-second group wait.

**Service impact:** 324 seconds from injection to healthy checks. **Alert recovery:** 285 seconds after service recovery because the 5-minute query still included failures. Peak observed 5xx ratio: 27.72%.

## Task 2 — Blameless postmortem

### Postmortem: Payment dependency outage

**Date:** 2026-09-27 (UTC)

**Duration:** 2026-09-27T16:59:49Z to
2026-09-27T17:05:13Z
(324 seconds
of service degradation)

**Severity:** SEV-3 (local exercise, partial payment-path outage)

**Author:** Esqavator

#### Summary

The payments container was deliberately stopped in a local exercise.
Gateway payment requests returned HTTP 502 because the payments
hostname was unavailable. Event listings and Events remained healthy.
The gateway 5xx ratio reached about 27.72%. There was no
external customer traffic.

#### Timeline

The complete timeline appears in Task 1. Key transitions were
injection at 2026-09-27T16:59:49Z, alert firing at
2026-09-27T17:02:58Z, incident notification at
2026-09-27T17:03:25.054690+00:00, service recovery at
2026-09-27T17:05:13Z, and alert recovery at
2026-09-27T17:09:58Z.

#### Root cause

The payment path depended on one payments container. With that
container stopped, gateway calls to payments failed and returned
HTTP 502. The 5-minute SLI window and 2-minute pending period
delayed detection relative to the first failed request. Old errors
in the rolling window kept the alert firing after service recovery.
Background HTTP 409 conflicts did not cause the 5xx alert.

#### What went well

- The critical alert fired
  189 seconds after
  injection, and the webhook received an incident notification.
- The runbook directed diagnosis to health endpoints, Compose
  state, and recent logs.
- Payments and gateway became healthy
  3 seconds
  after mitigation started.
- A resolved webhook confirmed notification routing after recovery.

#### What went wrong

- With no available payments instance, payment requests returned 502.
- An absent 5xx metric series initially produced a
  `DatasourceNoData` notification before the no-data behavior was set.
- The general load generator combined HTTP 409 conflicts with
  server failures in its failed-operation percentage.
- The alert remained Firing after application health recovered
  because its rolling window still contained errors.

#### Action items

| Action | Owner | Priority |
|--------|-------|----------|
| Alert directly on payments availability and payment-path 5xx failures. | QuickTicket maintainer (Esqavator) | High |
| Add an independent Prometheus target-down alert. | QuickTicket maintainer (Esqavator) | High |
| Separate HTTP 409 and HTTP 5xx in load-test summaries. | QuickTicket maintainer (Esqavator) | Medium |
| Record service recovery and alert resolution separately in the runbook. | QuickTicket maintainer (Esqavator) | Medium |

**Most important action item:** alert directly on payment-path failures.
A narrow payment failure may exceed the payment-specific threshold
before it raises gateway-wide 5xx errors above 5%.

## Bonus Task — Redis runbook and peer test

### Second runbook: Redis unavailable

**Impact:** New reservations may fail while Redis is unavailable.

**Working directory:** `~/Desktop/SRE-Intro/app`.

#### Diagnosis

1. Check gateway and Events health:

   ```bash
   curl -s -i http://localhost:3080/health
   curl -s -i http://localhost:8081/health
   ```

   Look for `events: down` at the gateway and `redis: down` at Events.
   HTTP 503 may still include a useful diagnostic response body.

2. Check Redis container state, including stopped containers:

   ```bash
   docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps --all redis
   ```

3. Inspect Events and Redis logs:

   ```bash
   docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs events redis --tail=50 --since=10m
   ```

4. If Redis is running, test it directly:

   ```bash
   docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml exec redis redis-cli ping
   ```

   A healthy Redis responds with `PONG`.

#### Common causes

| Cause | Evidence | Response |
|-------|----------|----------|
| Redis stopped | `ps --all redis` shows `Exited` | Start Redis |
| Redis unresponsive | `redis-cli ping` fails | Check Redis logs and health |
| Events cannot reach Redis | Redis replies `PONG`, Events logs show connection errors | Check `REDIS_HOST`, `REDIS_PORT`, and Compose networking |
| Redis times out | Events logs show timeouts | Inspect Redis logs and resource use |

#### Mitigation

If Redis is stopped, start it:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start redis
```

If it does not start, inspect its logs:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs redis --tail=100
```

#### Verification

1. Confirm Redis responds with `PONG`.
2. Confirm Events reports `postgres: ok` and `redis: ok`.
3. Confirm gateway reports `events: ok`.
4. Create a **new** reservation for an event with available tickets
   and record the response.
5. Check the relevant alert status in Grafana.

#### Escalation

If Redis is not healthy within 10 minutes, or new reservations still
fail after Redis replies `PONG`, contact the instructor or TA.
Provide health responses, Compose state, and recent logs.

### Peer test result

A classmate tested the Redis runbook and restored service using its
diagnosis and mitigation steps. Recovery took approximately **6 minutes**.

The unclear step was checking the stopped container: ordinary
`docker compose ps redis` did not show it, which initially made Redis
appear absent rather than stopped.

### Runbook improvement after peer feedback

The Redis diagnosis now uses:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps --all redis
```

This displays stopped containers, including the `Exited` state. The runbook
then instructs the responder to restore a stopped Redis container with:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start redis
```

These instructions are included in the second runbook above.
