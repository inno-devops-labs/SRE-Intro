# Lab 6 — Alerting & Incident Response

## Task 1 — Create Alerts & Respond to an Incident

### Start the full stack

```
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
./loadgen/run.sh 3 300 &
```

### Alert Rules (PromQL)

### Alert 1 — QuickTicket High Error Rate (critical)

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

- Condition: **IS ABOVE 5**
- Evaluation interval: **1 minute**
- Pending period: **2 minutes**
- Label: `severity=critical`

Annotations:

- Summary: `Gateway error rate exceeded 5%.`
- Description: `Error rate exceeded 5% for 2 minutes. Check payments service health.`


### Alert 2 — QuickTicket SLO Burn Rate (warning)

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

- Condition: **IS ABOVE 6**
- Evaluation interval: **1 minute**
- Pending period: **5 minutes**
- Label: `severity=warning`

Both alert rules were created in Grafana and configured to send notifications to the default notification policy.


### Contact Point

| Field    | Value                                                       |
| -------- | ----------------------------------------------------------- |
| Name     | `quickticket-alerts`                                        |
| Type     | Webhook                                                     |
| Receiver | `https://webhook.site/17d454e5-38d6-4772-a5a7-0d5d35056f7f` |

The contact point was successfully tested using Grafana's **Test** button.

Notification received:

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
      "startsAt": "2026-06-26T12:53:00.578858007Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "",
      "fingerprint": "57c6d9296de2ad39",
      "silenceURL": "http://localhost:3000/alerting/silence/new?alertmanager=grafana&matcher=alertname%3DTestAlert&matcher=instance%3DGrafana",
      "dashboardURL": "",
      "panelURL": "",
      "values": null,
      "valueString": "[ metric='foo' labels={instance=bar} value=10 ]"
    }
  ],
  "groupLabels": {
    "alertname": "TestAlert",
    "instance": "Grafana"
  },
  "commonLabels": {
    "alertname": "TestAlert",
    "instance": "Grafana"
  },
  "commonAnnotations": {
    "summary": "Notification test"
  },
  "externalURL": "http://localhost:3000/",
  "appVersion": "13.0.1",
  "version": "1",
  "groupKey": "webhook-57c6d9296de2ad39-1782478380",
  "truncatedAlerts": 0,
  "orgId": 1,
  "title": "[FIRING:1] TestAlert Grafana ",
  "state": "alerting",
  "message": "**Firing**\n\nValue: [no value]\nLabels:\n - alertname = TestAlert\n - instance = Grafana\nAnnotations:\n - summary = Notification test\nSilence: http://localhost:3000/alerting/silence/new?alertmanager=grafana&matcher=alertname%3DTestAlert&matcher=instance%3DGrafana\n"
}
```

This confirms that Grafana successfully delivers webhook notifications.

---

### Runbook — QuickTicket High Error Rate

### Alert

- **Alert name:** QuickTicket High Error Rate
- **Severity:** Critical
- **Fires when:** Gateway 5xx error rate exceeds 5% for at least 2 minutes.
- **Dashboard:** QuickTicket — Golden Signals

### Diagnosis

1. Check gateway health.

   curl -s http://localhost:3080/health | python3 -m json.tool

2. Check the payments service.

   curl -s http://localhost:8082/health

3. Check the events service.

   curl -s http://localhost:8081/health

4. Review gateway logs.

   docker compose logs gateway --tail=20 --since=5m

5. Review payments logs.

   docker compose logs payments --tail=20 --since=5m

6. Verify that all containers are running.

   docker compose ps

### Common Causes

| Cause                        | How to identify                   | Resolution                                       |
| ---------------------------- | --------------------------------- | ------------------------------------------------ |
| Payments service stopped     | Health endpoint unavailable       | `docker compose start payments`                  |
| High payment failure rate    | Large number of 5xx responses     | Restart payments with `PAYMENT_FAILURE_RATE=0.0` |
| Events service unavailable   | Health endpoint unavailable       | `docker compose start events`                    |
| Database connectivity issues | Database errors in logs           | Restart events service and verify PostgreSQL     |
| Gateway configuration issue  | Gateway startup or routing errors | Verify configuration and restart gateway         |

### Resolution Steps

1. Identify the unhealthy service.
2. Review logs to determine the root cause.
3. Restore the affected service.
4. Verify that all health endpoints return healthy responses.
5. Confirm that the Grafana alert returns to **Normal**.
6. Continue monitoring the system for several minutes.

### Escalation

If the issue cannot be resolved within 10 minutes, escalate it to the course instructor or teaching assistant together with logs, Grafana screenshots and the investigation timeline.

---

### Incident Simulation

Failure was injected by restarting the payments service with an increased failure rate.

Command:

```
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_FAILURE_RATE=0.5 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

Failure injected at **17:50**.

To ensure that the configured `PAYMENT_FAILURE_RATE=0.5` produced a measurable increase in the overall gateway error rate, additional payment-specific traffic was generated throughout the experiment. Since payment requests represent only a subset of all gateway requests, this approach ensured that payment failures had a sufficient impact on the overall gateway error rate while preserving the required failure rate configuration.

---

### Alert Firing Evidence

During testing, the **QuickTicket High Error Rate** alert transitioned from **Normal** to **Pending** after the gateway 5xx error rate exceeded the configured threshold. Following the configured pending period, the alert entered the **Firing** state and Grafana successfully delivered a webhook notification to the configured contact point.

---

### Timeline

| Time   | Event                                                                                            |
| ------ | ------------------------------------------------------------------------------------------------ |
| 17:50  | Failure injected by restarting the payments service with `PAYMENT_FAILURE_RATE=0.5`              |
| 17:50  | Additional payment-specific traffic generation started                                           |
| ~17:52 | Gateway error rate exceeded the configured threshold and the alert entered the **Pending** state |
| ~17:54 | Alert transitioned to **Firing** after the configured pending period                             |
| ~17:54 | Investigation started using the runbook (`/health`, service health checks, container logs)       |
| ~17:55 | Root cause identified as intentionally increased payment failures                                |
| ~17:56 | Payments service restored with `PAYMENT_FAILURE_RATE=0.0`                                        |
| ~18:01 | Alert returned to **Normal** after the gateway error rate dropped below the configured threshold |

---

### How long from failure injection to alert firing? Why the delay?

The alert fired approximately **4 minutes** after the failure was injected.

The delay is expected because the alert rule evaluates a **5-minute Prometheus rate window**, Grafana evaluates the rule every **1 minute**, and the alert requires the condition to remain true for a **2-minute pending period** before entering the **Firing** state. Additional payment-specific traffic was generated to ensure that the gateway error rate exceeded the configured threshold, while the evaluation window and pending period intentionally prevented false positives caused by short-lived traffic spikes.

## Task 2 — Blameless Postmortem

# Postmortem: QuickTicket High Error Rate During Payments Failure

**Date:** 2026-06-26

**Duration:** 17:50 → 18:01

**Severity:** SEV-3

**Author:** Irina Poltorakova

## Summary

During the incident simulation, the payments service was restarted with an elevated failure rate (`PAYMENT_FAILURE_RATE=0.5`). Additional payment-specific traffic was generated to ensure that payment failures were reflected in the overall gateway error rate. Grafana detected the issue through the **QuickTicket High Error Rate** alert, and the service recovered after the payments service was restored to the normal configuration.

## Timeline

| Time   | Event                                                                                            |
| ------ | ------------------------------------------------------------------------------------------------ |
| 17:50  | Failure injected by restarting the payments service with `PAYMENT_FAILURE_RATE=0.5`              |
| 17:50  | Additional payment-specific traffic generation started                                           |
| ~17:52 | Gateway error rate exceeded the configured threshold and the alert entered the **Pending** state |
| ~17:54 | Alert transitioned to **Firing**                                                                 |
| ~17:54 | Investigation started using the runbook                                                          |
| ~17:55 | Root cause identified as intentionally increased payment failures                                |
| ~17:56 | Payments service restored with `PAYMENT_FAILURE_RATE=0.0`                                        |
| ~18:01 | Alert returned to **Normal** and the service fully recovered                                     |

## Root Cause

The payments service was intentionally restarted with an elevated failure rate as part of the incident simulation. Since payment requests represent only a subset of the overall gateway traffic, additional payment-specific traffic was generated to make the increase in gateway 5xx responses observable. This caused the gateway error rate to exceed the configured threshold, allowing the monitoring system to detect the incident and trigger the alert after the configured evaluation and pending periods.

The incident demonstrated that gateway-level monitoring successfully detects downstream dependency failures. It also highlighted that dedicated service-level alerts for the payments service would allow engineers to identify the root cause more quickly.

## What Went Well

- Grafana automatically detected the increased gateway error rate.
- The webhook contact point successfully received the alert notification.
- The runbook provided clear diagnosis steps for checking gateway, payments, events, and container logs.
- Additional payment-specific traffic produced a deterministic alert while preserving the required `PAYMENT_FAILURE_RATE=0.5`.
- The issue was resolved quickly by restoring the payments service to the normal configuration.
- The alert automatically returned to **Normal** after the gateway error rate fell below the configured threshold.

## What Went Wrong

- The alert did not fire immediately because it relied on a 5-minute Prometheus rate window and a 2-minute pending period.
- Gateway-level monitoring required additional investigation before identifying the payments service as the root cause.
- The runbook did not explicitly recommend generating payment-specific traffic when validating gateway-level alerts.
- There was no dedicated payments-specific alert to identify the failing dependency directly.

## Action Items

| Action                                                                                 | Owner         | Priority |
| -------------------------------------------------------------------------------------- | ------------- | -------- |
| Add a payments-specific error rate alert                                               | Irina Poltorakova | High     |
| Update the runbook with an explicit `PAYMENT_FAILURE_RATE` verification step           | Irina Poltorakova | Medium   |
| Add a dashboard panel showing payment-specific error metrics                           | Irina Poltorakova | Medium   |
| Document the procedure for generating targeted payment traffic during alert validation | Irina Poltorakova | Low      |

## Most Important Action Item

The most important action item is to add a payments-specific error rate alert. During this incident, the initial alert was triggered by the gateway error rate, while the underlying problem originated in the payments service. A dedicated payments alert would identify the failing dependency immediately, reduce diagnosis time, and improve incident response.

## Bonus Task — Cross-Test Runbooks

# Runbook: Redis Unavailable

## Alert

- **Alert name:** Redis Unavailable
- **Severity:** Critical
- **Fires when:** The Redis service becomes unavailable, causing reservation operations to fail.
- **Dashboard:** QuickTicket — Infrastructure

## Diagnosis

1. Check gateway health:

   ```
   curl -s http://localhost:3080/health | python3 -m json.tool
   ```

2. Check the events service:

   ```
   curl -s http://localhost:8081/health
   ```

3. Check the payments service:

   ```
   curl -s http://localhost:8082/health
   ```

4. Verify that the Redis container is running:

   ```
   docker compose ps
   ```

5. Inspect Redis logs:

   ```
   docker compose logs redis --tail=20 --since=5m
   ```

6. Review recent events service logs for Redis connection errors:

   ```
   docker compose logs events --tail=20 --since=5m
   ```

## Common Causes

| Cause                           | How to identify                                            | Resolution                                                |
| ------------------------------- | ---------------------------------------------------------- | --------------------------------------------------------- |
| Redis container stopped         | Redis container is not running in `docker compose ps`      | `docker compose start redis`                              |
| Redis startup failure           | Redis logs contain startup or configuration errors         | Restart Redis and verify configuration                    |
| Network connectivity issue      | Events service reports Redis connection refused or timeout | Restart Redis and verify Docker networking                |
| Redis unavailable after restart | Health checks continue to fail                             | Recreate the container using `docker compose up -d redis` |

## Resolution Steps

1. Confirm that Redis is the unavailable component by checking health endpoints and service logs.

2. Verify the Redis container status.

3. Restart the Redis service if it is stopped:

   ```
   docker compose start redis
   ```

4. If Redis still fails to start, recreate the container:

   ```
   docker compose up -d redis
   ```

5. Confirm that Redis is running successfully:

   ```
   docker compose ps
   ```

6. Verify that reservation requests succeed again and all health endpoints return healthy status.

7. Continue monitoring the application for several minutes to ensure the issue does not reoccur.

## Escalation

If the incident cannot be resolved within **10 minutes**:

- Escalate to the course instructor or teaching assistant.
- Include:

  - recent Redis logs;
  - events service logs;
  - output of `docker compose ps`;
  - health endpoint responses;
  - description of the recovery actions already attempted.


## Results of testing by classmate

I gave this runbook to a classmate. They spent a short amount of time studying it, after which I broke Redis.

The classmate first verified the health endpoints using the provided curl commands, then inspected the service logs to identify the failing component. After determining that Redis was unavailable, they followed the recovery instructions from the runbook and successfully restored the service. In the end, it took about 5 minutes to fix the issue.

### Feedback

- "The diagnosis steps were clear and easy to follow"

- "It was not immediately obvious that Redis was responsible for reservation failures"

- "A brief explanation of typical Redis-related symptoms would improve the runbook"

### Suggestion for improvment

- Added a short description of common Redis failure symptoms

- Reordered the diagnosis steps so that Redis verification is performed before detailed log inspection.

### Suggested Improvements

Based on the classmate's feedback, the runbook was updated with the following improvements:

#### Redis Failure Symptoms

A short description of common Redis-related symptoms was added to help identify the issue more quickly before starting detailed troubleshooting.

Typical symptoms include:

- Reservation requests fail or return server errors.
- The events service reports Redis connection refused or timeout errors.
- Gateway health checks indicate degraded service while Redis is unavailable.

#### Updated Diagnosis Order

The diagnosis section was reorganized to make the troubleshooting process more intuitive:

1. Check service health endpoints.
2. Verify that the Redis container is running (`docker compose ps`).
3. Inspect Redis logs.
4. Review the events service logs for Redis connection errors.

This ordering helps identify the failing component faster and avoids unnecessary log inspection when the Redis container is simply stopped.
