# Lab 6 — Alerting & Incident Response

# Task 1 — Create Alerts & Respond to an Incident

## Full Stack Startup

I started the QuickTicket application together with the monitoring stack using Docker Compose.

Command:

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
```

All required services were running:

```text
gateway
events
payments
postgres
redis
prometheus
grafana
```

I also generated background traffic so that Prometheus had gateway request metrics:

```bash
./loadgen/run.sh 5 300 &
```

I checked that Prometheus received gateway traffic:

```bash
curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))' \
  http://localhost:9090/api/v1/query | jq
```

Relevant output:

```json
{
  "status": "success",
  "data": {
    "resultType": "vector",
    "result": [
      {
        "metric": {},
        "value": [
          1782053174.464,
          "0.2971293333333333"
        ]
      }
    ]
  }
}
```

This confirmed that the gateway metrics were available and that the alert rules could evaluate real traffic.

---

## Contact Point

I created a Grafana contact point for alert notifications.

Configuration:

```text
Name: quickticket-alerts
Type: Webhook
Receiver: webhook.site
```

I tested the contact point from Grafana. The webhook receiver successfully received the test notification:

```json
{
  "receiver": "webhook",
  "status": "firing",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "TestAlert",
        "instance": "Grafana"
      },
      "annotations": {
        "summary": "Notification test"
      },
      "startsAt": "2026-06-21T14:48:15.267849757Z"
    }
  ],
  "title": "[FIRING:1] TestAlert Grafana",
  "state": "alerting"
}
```

This confirmed that Grafana could send alert notifications to the configured webhook receiver.

---

## Alert Rules

I created two Grafana-managed alert rules in the `QuickTicket` folder.

### Alert 1 — QuickTicket High Error Rate

This alert detects a high percentage of 5xx responses returned by the gateway.

Alert name:

```text
QuickTicket High Error Rate
```

Type:

```text
Grafana-managed
```

PromQL query:

```promql
100 * ((sum(rate(gateway_requests_total{status=~"5.."}[5m])) or vector(0)) / sum(rate(gateway_requests_total[5m])))
```

The query is equivalent to the required gateway 5xx error rate calculation, but it includes `or vector(0)` so that the alert stays in `Normal` instead of `No data` when there are no 5xx time series yet.

Condition:

```text
IS ABOVE 5
```

Evaluation behavior:

```text
Evaluate every: 1m
Pending period: 2m
```

Labels:

```text
severity = critical
service = gateway
```

Annotations:

```text
summary = Gateway error rate is above 5%
description = Gateway 5xx error rate exceeded 5% for 2 minutes. Check payments service health and gateway logs.
```

### Alert 2 — QuickTicket SLO Burn Rate

This alert detects fast burn of the availability error budget for the 99.5% availability SLO.

Alert name:

```text
QuickTicket SLO Burn Rate
```

Type:

```text
Grafana-managed
```

PromQL query:

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

Condition:

```text
IS ABOVE 6
```

Evaluation behavior:

```text
Evaluate every: 1m
Pending period: 5m
```

Labels:

```text
severity = warning
service = gateway
slo = availability
```

---

## Notification Policy

I configured the default Grafana notification policy to send alert notifications to the webhook contact point.

Configuration:

```text
Default contact point: quickticket-alerts
Group by: alertname
Group wait: 30s
Repeat interval: 5m
```

---

## Runbook: QuickTicket High Error Rate

### Alert

* **Alert name:** QuickTicket High Error Rate
* **Fires when:** the gateway 5xx error rate stays above 5% for 2 minutes
* **Severity:** critical
* **Dashboard:** QuickTicket — Golden Signals
* **Notification contact point:** quickticket-alerts

### What this alert means

This alert means that users are receiving too many server-side errors from the gateway. Since the gateway depends on the `events` and `payments` services, the first step is to check whether one of these downstream services is unavailable or returning errors.

### Initial checks

1. Check the overall gateway health:

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
```

2. Check the payments service directly:

```bash
curl -s http://localhost:8082/health
```

3. Check the events service directly:

```bash
curl -s http://localhost:8081/health
```

4. Check the current gateway error rate in Prometheus:

```bash
curl -s --data-urlencode 'query=100 * ((sum(rate(gateway_requests_total{status=~"5.."}[5m])) or vector(0)) / sum(rate(gateway_requests_total[5m])))' \
  http://localhost:9090/api/v1/query | jq
```

### Logs to inspect

Check recent gateway logs:

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs gateway --tail=50 --since=5m
```

Check recent payments logs:

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs payments --tail=50 --since=5m
```

If the gateway health check points to the events service, also check events logs:

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs events --tail=50 --since=5m
```

### Common causes and fixes

| Cause                                  | How to identify it                                                                     | Suggested fix                                            |
| -------------------------------------- | -------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| Payments service is stopped            | Gateway health check shows payments as unavailable, or payment-related requests fail   | Start payments again                                     |
| Payments service has injected failures | Payments is running, but gateway still returns many 5xx responses during payment calls | Restart payments with `PAYMENT_FAILURE_RATE=0.0`         |
| Events service is unavailable          | Gateway or direct health check shows events as unavailable                             | Restart the events service                               |
| Database-related issue                 | Events logs contain database connection or pool errors                                 | Check PostgreSQL health and restart the affected service |

### Mitigation steps

If the payments service is stopped, start it again:

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start payments
```

If the payments service was started with an injected failure rate, restart it with normal settings:

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_FAILURE_RATE=0.0 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

If the events service is the failing dependency, restart it:

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml restart events
```

### Validation

After applying the fix, verify that the affected service is running:

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
```

Then check the gateway health again:

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
```

Finally, confirm that the gateway error rate is below the alert threshold:

```bash
curl -s --data-urlencode 'query=100 * ((sum(rate(gateway_requests_total{status=~"5.."}[5m])) or vector(0)) / sum(rate(gateway_requests_total[5m])))' \
  http://localhost:9090/api/v1/query | jq
```

The incident can be considered mitigated when:

* the failed dependency is running again;
* gateway health is healthy;
* the error rate drops below 5%;
* the Grafana alert returns to `Normal`.

### Escalation

If the alert is still firing after 10 minutes, escalate to the instructor or TA and include:

* current alert state;
* gateway health output;
* payments and events health output;
* recent gateway logs;
* recent downstream service logs;
* commands already used during mitigation.

---

## Incident Simulation

I started background traffic so that Prometheus had gateway request metrics:

```bash
./loadgen/run.sh 5 300 &
```

Then I injected a payments failure.

Failure injection command:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
```

After stopping payments, the gateway health check showed that the application was degraded and that the payments dependency was down:

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

At first, the overall gateway error rate was still below the 5% threshold:

```text
Gateway 5xx error rate: 1.065891472868217%
```

This happened because the default load generator sends mixed traffic, and payment-related requests are only a part of the total traffic. To make the incident deterministic, I generated payment-specific failure traffic against the payment endpoint while the payments service was stopped:

```bash
while true; do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST http://localhost:3080/reserve/test-reservation/pay
  sleep 0.2
done
```

This increased the gateway 5xx error rate above the alert threshold. Grafana moved the `QuickTicket High Error Rate` alert from `Normal` to `Pending`, and then to `Firing`.

---

## Proof of Work

### Alert firing evidence

Grafana showed the `QuickTicket High Error Rate` alert in `Firing` state.

The webhook receiver received the real alert notification:

```json
{
  "receiver": "quickticket-alerts",
  "status": "firing",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "QuickTicket High Error Rate",
        "grafana_folder": "QuickTicket"
      },
      "annotations": {
        "description": "Gateway 5xx error rate exceeded 5% for 2 minutes. Check payments service health and gateway logs.",
        "summary": "Gateway error rate is above 5%"
      },
      "startsAt": "2026-06-21T15:21:20Z",
      "values": {
        "A": 39.3606950161942,
        "C": 1
      }
    }
  ],
  "title": "[FIRING:1] QuickTicket High Error Rate QuickTicket (gateway)",
  "state": "alerting"
}
```

This confirms that the alert fired and that the notification was delivered through the configured contact point.

### Diagnosis evidence

I followed the runbook and checked the gateway health endpoint:

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
```

Output:

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

The health check showed that the `events` service was healthy, while the `payments` service was down. This matched the injected failure and explained the 5xx responses from the gateway payment flow.

### Mitigation and recovery evidence

I stopped the payment-specific failure traffic and started the payments service again:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start payments
```

After the fix, the gateway health check returned to healthy:

```json
{
  "status": "healthy",
  "checks": {
    "events": "ok",
    "payments": "ok",
    "circuit_payments": "CLOSED"
  }
}
```

I also checked the 1-minute 5xx rate by path and status. New payment failures stopped after the mitigation:

```text
path="/reserve/test-reservation/pay", status="502" -> 0
path="/reserve/{id}/pay", status="502" -> 0
path="/health", status="503" -> 0
```

At 18:27:37, Grafana showed that `QuickTicket High Error Rate` returned to `Normal`.

### Incident timeline

| Time        | Event                                                                                  |
| ----------- | -------------------------------------------------------------------------------------- |
| 18:16:47    | Payments service was stopped                                                           |
| 18:16:49    | Gateway health became degraded: `events=ok`, `payments=down`                           |
| 18:17:02    | Gateway error rate was about `1.06%`, below the 5% threshold                           |
| 18:18–18:21 | Payment-specific failure traffic was generated against `/reserve/test-reservation/pay` |
| 18:21:20    | `QuickTicket High Error Rate` became `Firing`                                          |
| 18:21:20+   | Webhook notification was received by `quickticket-alerts`                              |
| 18:26:07    | New 1-minute 502 rate dropped to `0` after stopping failure traffic                    |
| 18:27:02    | Payments was restored and gateway health returned to `healthy`                         |
| 18:27:37    | `QuickTicket High Error Rate` returned to `Normal`                                     |

### Alert delay explanation

Time from failure injection to alert firing:

```text
18:16:47 → 18:21:20 = approximately 4 minutes 33 seconds
```

The delay had two reasons.

First, the initial mixed background traffic did not immediately push the global gateway error rate above the 5% threshold. The gateway health check showed `payments=down`, but the gateway 5xx error rate was initially only about `1.06%`, because payment requests were only a part of the total traffic.

Second, after the payment-specific failure traffic pushed the error rate above 5%, Grafana still had to wait for the configured alert evaluation behavior:

```text
Evaluate every: 1m
Pending period: 2m
```

Therefore, the alert fired only after the condition stayed true for multiple evaluations. This is expected behavior and prevents short temporary spikes from immediately paging the responder.

---

# Task 2 — Blameless Postmortem

# Postmortem: Payments Service Failure Caused Gateway 5xx Spike

**Date:** 2026-06-21
**Duration:** 18:16:47 → 18:27:37
**Severity:** SEV-3
**Author:** Ernest Kudakaev

## Summary

During this lab, I simulated an incident by stopping the `payments` service. As a result, payment-related requests through the gateway started returning 502 errors, and the `QuickTicket High Error Rate` alert eventually moved to `Firing`.

The incident affected the payment flow only. Other parts of the system, such as the `events` service, continued to work normally.

## Timeline

| Time        | Event                                                                                |
| ----------- | ------------------------------------------------------------------------------------ |
| 18:16:47    | Payments service was stopped                                                         |
| 18:16:49    | Gateway health became degraded: `events=ok`, `payments=down`                         |
| 18:17:02    | Gateway error rate was about `1.06%`, still below the 5% alert threshold             |
| 18:18–18:21 | I generated payment-specific failure traffic against `/reserve/test-reservation/pay` |
| 18:21:20    | `QuickTicket High Error Rate` became `Firing`                                        |
| 18:21:20+   | Webhook notification was received by `quickticket-alerts`                            |
| 18:26:07    | New 1-minute 502 rate dropped to `0` after I stopped the failure traffic             |
| 18:27:02    | Payments service was started again and gateway health returned to `healthy`          |
| 18:27:37    | `QuickTicket High Error Rate` returned to `Normal`                                   |

## Root Cause

The immediate cause of the incident was that the `payments` service was unavailable. The gateway payment endpoint depends on this service, so requests to `/reserve/{reservation_id}/pay` started failing with 502 responses.

One important observation from the lab is that the gateway-level alert did not fire immediately after the payments service was stopped. The default background load produced mixed traffic, so payment failures were only a part of all gateway requests. Because of that, the overall gateway 5xx error rate was initially only about `1.06%`, which was below the 5% alert threshold.

After I generated payment-specific traffic, the share of failing requests increased, the error rate crossed the threshold, and Grafana moved the alert from `Pending` to `Firing`.

## What Went Well

* The Grafana webhook contact point worked correctly and delivered notifications.
* The `QuickTicket High Error Rate` alert fired when the gateway 5xx error rate stayed above the threshold.
* The gateway health endpoint made the diagnosis straightforward: it clearly showed `events=ok` and `payments=down`.
* The runbook gave a clear order of checks: gateway health, downstream service health, Prometheus query, and logs.
* After the payments service was restored, the gateway health returned to `healthy`, and the alert eventually returned to `Normal`.

## What Went Wrong

* The first mixed load did not trigger the alert quickly, because payment failures were diluted by successful non-payment requests.
* The original high error rate query could return `No data` when there were no 5xx series yet, so I had to adjust it with `or vector(0)`.
* The alert did not resolve immediately after the fix because it used a 5-minute rate window. Old 502 responses stayed in the calculation for a few minutes.
* The runbook could be more explicit about checking endpoint-specific errors, not only the global gateway error rate.

## Action Items

| Action                                                                                      | Owner           | Priority |
| ------------------------------------------------------------------------------------------- | --------------- | -------- |
| Add a payment-specific 5xx alert for the payment flow                                       | Ernest Kudakaev | High     |
| Add a dashboard panel for payment-specific error rate and payment latency                   | Ernest Kudakaev | Medium   |
| Update the runbook with a note that mixed traffic can hide endpoint-specific failures       | Ernest Kudakaev | Medium   |
| Keep `or vector(0)` in the high error rate query to avoid `No data` during normal operation | Ernest Kudakaev | Medium   |
| Add a validation step that checks the 1-minute 5xx rate after mitigation                    | Ernest Kudakaev | Low      |

## Most Important Action Item

The most important action item is to add a payment-specific 5xx alert for the payment flow.

The global gateway error rate alert is useful, but this lab showed that it may react slowly when only one endpoint is failing. In my case, the payments service was already down, but the overall gateway error rate was still below the 5% threshold because the background traffic also included successful non-payment requests.

A payment-specific alert would detect this kind of failure earlier. It would also make the diagnosis faster, because the alert would immediately point to the payment flow instead of only showing that the gateway has too many 5xx responses overall.

---

# Bonus Task — Cross-Test Runbooks

## Second Runbook: Redis Down → Reservations Fail

### Alert / Failure Mode

* **Failure mode:** Redis is unavailable
* **Expected impact:** reservation-related requests may fail or become degraded
* **Main affected flow:** creating reservations through the gateway
* **Services involved:** `gateway`, `events`, `redis`
* **Dashboard:** QuickTicket — Golden Signals

### What this failure means

The `events` service depends on Redis for reservation-related operations. If Redis is down, the service may still start, but reservation requests can fail because the dependency required for reservation state is unavailable.

The goal of this runbook is to help the responder identify whether reservation failures are caused by Redis, confirm the impact through gateway requests and logs, and restore the system by starting Redis again.

## Diagnosis

### 1. Check gateway health

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
```

Expected signal:

* If gateway is healthy, the issue may still be hidden inside a specific endpoint.
* If gateway is degraded, check which dependency is shown as unhealthy.

### 2. Check events service health directly

```bash
curl -s http://localhost:8081/health
```

If the events service reports Redis-related problems, Redis is likely the affected dependency.

### 3. Check Redis container status

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps redis
```

If Redis is stopped or not healthy, this is the likely root cause.

### 4. Test the reservation flow

First, list events:

```bash
curl -s http://localhost:3080/events | python3 -m json.tool
```

Then try to create a reservation for an event:

```bash
curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test-user","quantity":1}' | python3 -m json.tool
```

If Redis is down, this request may return an error from the gateway or events service.

### 5. Check logs

Check gateway logs:

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs gateway --tail=50 --since=5m
```

Check events logs:

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs events --tail=50 --since=5m
```

Check Redis logs:

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs redis --tail=50 --since=5m
```

Look for Redis connection errors, timeout errors, or reservation-related failures in the events service logs.

## Common Causes

| Cause                                                  | How to identify it                                         | Fix                                                |
| ------------------------------------------------------ | ---------------------------------------------------------- | -------------------------------------------------- |
| Redis container is stopped                             | `docker compose ps redis` shows Redis as stopped or exited | Start Redis again                                  |
| Redis is unhealthy                                     | Redis container is running but health check is failing     | Restart Redis                                      |
| Events service cannot connect to Redis                 | Events logs show Redis connection errors                   | Start/restart Redis, then restart events if needed |
| Reservation endpoint fails while `/events` still works | Listing events works, but reservation requests fail        | Check Redis status and events logs                 |

## Mitigation Steps

### 1. Start Redis again

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start redis
```

### 2. If Redis does not recover, restart it

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml restart redis
```

### 3. If reservation requests still fail, restart the events service

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml restart events
```

The events service may need to reconnect to Redis after Redis becomes available again.

## Validation

### 1. Check Redis status

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps redis
```

Redis should be `Up` and `healthy`.

### 2. Check gateway health

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
```

The system should not show Redis-related dependency failures.

### 3. Test reservation flow again

```bash
curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test-user","quantity":1}' | python3 -m json.tool
```

The reservation request should no longer fail because of Redis connectivity.

### 4. Check recent 5xx errors

```bash
curl -s --data-urlencode 'query=sum by (path,status)(rate(gateway_requests_total{status=~"5.."}[1m]))' \
  http://localhost:9090/api/v1/query | jq
```

The 1-minute rate for reservation-related 5xx responses should drop to `0` or disappear from the result.

## Escalation

If Redis is running and healthy but reservation requests are still failing after restarting the events service, escalate to the instructor or TA with:

* Redis container status;
* gateway health output;
* events health output;
* recent gateway logs;
* recent events logs;
* exact reservation request used for testing;
* Prometheus query result for gateway 5xx responses.

---

## Cross-Test Results

### Test setup

I gave this Redis failure runbook to Islam Gainullin and asked him to diagnose and fix the issue using only the runbook.

He did not know in advance which dependency I was going to break.

Injected failure:

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop redis
```

### Result

Islam was able to identify that Redis was the affected dependency and restore the system using only the runbook.

Result summary:

```text
Resolved using only the runbook: Yes
Time to diagnose: 4 minutes
Time to fix: 2 minutes
Total time: 6 minutes
```

### What the classmate checked

Islam followed the runbook and checked:

1. gateway health;
2. events service health;
3. Redis container status;
4. reservation endpoint behavior;
5. events service logs.

The most useful step was checking the Redis container status:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps redis
```

This showed that the Redis container was stopped, which matched the reservation failure.

### What was unclear or missing

The main unclear part was that the gateway health check did not immediately explain the full impact of the Redis failure. It was possible to see that the system had a problem, but the more important signal came from testing the reservation endpoint directly.

The first version of the runbook also did not emphasize strongly enough that `/events` and `/events/{id}/reserve` can behave differently. Listing events may still work, while creating a reservation can fail because Redis is needed for the reservation flow.

### Runbook update based on feedback

Based on Islam's feedback, I updated the runbook to make the reservation-flow check more explicit. The updated version now says that the responder should not rely only on `/events`, and should test `/events/{id}/reserve` directly.

I also added a note that if Redis is restored but reservation requests are still failing, the `events` service should be restarted so it can reconnect to Redis.

Most important update:

```text
Do not rely only on /events. Test /events/{id}/reserve directly, because Redis issues are most visible in the reservation flow.
```
