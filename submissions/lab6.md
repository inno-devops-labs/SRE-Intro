# Lab 6 — Alerting and Incident Response

## Task 1 — Alerts and incident response

The alerting resources are stored in `monitoring/grafana/provisioning/alerting/quickticket.yml`. Grafana loaded two rules, a webhook contact point and the notification policy from this file.

### Alert rules

#### QuickTicket High Error Rate

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m]))
/
sum(rate(gateway_requests_total[5m])) * 100
```

- Threshold: above 5%
- Evaluation interval: 1 minute
- Pending period: 2 minutes
- Severity: critical

#### QuickTicket SLO Burn Rate

```promql
(1 - (
  sum(rate(gateway_requests_total{status!~"5.."}[30m]))
  /
  sum(rate(gateway_requests_total[30m]))
)) / (1 - 0.995)
```

- Threshold: above 6x
- Evaluation interval: 1 minute
- Pending period: 5 minutes
- Severity: warning

Grafana provisioning API evidence:

```text
QuickTicket High Error Rate  for=2m  severity=critical
QuickTicket SLO Burn Rate    for=5m  severity=warning
```

### Contact point and notification policy

The `quickticket-alerts` contact point uses a webhook to the local receiver at `http://webhook:8080/alerts`. This keeps incident data local and provides timestamped JSON evidence in container logs.

```text
type: webhook
group_by: [alertname]
group_wait: 30s
group_interval: 1m
repeat_interval: 5m
```

Firing notification received:

```text
received_at=2026-09-26T14:04:49.508767Z
status=firing
title=[FIRING:1] QuickTicket High Error Rate (QuickTicket critical)
summary=Gateway error rate is 6.0327012222812595%
```

Resolved notification received:

```text
received_at=2026-09-26T14:06:47.520066Z
status=resolved
title=[RESOLVED] QuickTicket High Error Rate (QuickTicket critical)
summary=Gateway error rate is 4.275153509103003%
```

The second rule also fired and delivered a webhook notification:

```text
received_at=2026-09-26T14:06:54.345539Z
status=firing
title=[FIRING:1] QuickTicket SLO Burn Rate (QuickTicket warning)
burn_rate=8.523097194728605
```

## Runbook: QuickTicket High Error Rate

### Alert

- **Fires when:** Gateway 5xx error rate is above 5% for 2 minutes.
- **Severity:** Critical.
- **Dashboard:** QuickTicket — Golden Signals.
- **Immediate impact:** Some client operations fail. Read-only traffic may still work if only payments is affected.

### Diagnosis

1. Record the alert start time and current error-rate value.
2. Check the aggregate health endpoint:

   ```bash
   curl -s http://localhost:3080/health | python3 -m json.tool
   ```

3. Check payments directly:

   ```bash
   curl -s --max-time 2 http://localhost:8082/health
   ```

4. Check events and its dependencies:

   ```bash
   curl -s http://localhost:8081/health | python3 -m json.tool
   ```

5. Inspect recent gateway and payments logs:

   ```bash
   docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml logs gateway payments --tail=50 --since=5m
   ```

6. Confirm which request paths and status codes contribute to the alert in the Golden Signals dashboard or Prometheus.

### Common causes

| Cause | How to identify | Mitigation |
|---|---|---|
| Payments service down | Gateway health reports `payments: down`; port 8082 is unavailable | Start payments and verify `/health` |
| Payments failure injection | Payments health reports a non-zero `failure_rate` | Restore `PAYMENT_FAILURE_RATE=0.0` and recreate payments |
| Events service down | Gateway reports `events: down`; events health is unavailable | Start events and verify PostgreSQL and Redis |
| PostgreSQL unavailable | Events health reports `postgres: down` | Restore PostgreSQL; avoid restarting healthy dependencies repeatedly |
| Redis unavailable | Events health reports `redis: down`; reservations time out | Restore Redis and verify reservation flow |
| DB pool exhaustion | Events logs show pool acquisition errors | Reduce load, inspect `DB_MAX_CONNS`, database capacity and leaked connections |

### Mitigation verification

```bash
curl -s http://localhost:8082/health | python3 -m json.tool
curl -s http://localhost:3080/health | python3 -m json.tool
```

Confirm that the alert returns to Normal and that the resolved notification is delivered. Do not treat process recovery alone as incident resolution while the SLI remains above threshold.

### Escalation

- Escalate to the instructor/TA if the cause is not identified within 10 minutes.
- Escalate immediately if all purchase traffic is failing or the error budget burn rate continues increasing after mitigation.
- Include timestamps, affected paths, health output, recent logs and changes made.

### Incident timeline

| Time (UTC) | Event |
|---|---|
| 14:00:24 | Payments container stopped; incident begins |
| 14:00:55 | First non-empty 5xx error-rate signal appears |
| 14:02:20 | High Error Rate enters Pending after the signal remains above 5% |
| 14:04:20 | High Error Rate changes to Firing |
| 14:04:49 | Webhook receives the firing notification |
| 14:04:57 | Investigation starts using the runbook |
| 14:05:04 | Root cause confirmed: payments container is stopped |
| 14:05:04 | Payments restored with failure rate 0; health immediately recovers |
| 14:06:20 | High Error Rate returns to Normal |
| 14:06:47 | Webhook receives the resolved notification |

The alert fired **3 minutes 56 seconds** after injection. The delay had three parts: failed payment calls first had to raise the aggregate error rate above 5%, the rule then had to remain above threshold for the 2-minute pending period, and evaluation occurs only once per minute. Notification delivery added another 29.5 seconds because the policy has a 30-second group wait.

## Task 2 — Blameless postmortem

# Postmortem: Payment dependency outage increased gateway 5xx rate

**Date:** 2026-09-26
**Duration:** 14:00:24–14:06:20 UTC (5 minutes 56 seconds)
**Severity:** SEV-3
**Author:** avlaptev

## Summary

The payments service became unavailable during generated traffic. Payment operations returned 5xx responses through the gateway, while event reads and the events service remained healthy. Grafana detected the sustained error rate, notified the local webhook, and the service was restored by starting payments with failure injection disabled.

## Impact

- Payment attempts failed while payments was unavailable.
- Event listing and reservation paths remained available where inventory allowed.
- Gateway health correctly reported a degraded payments dependency.
- The alert observed 6.03% gateway 5xx at firing time.
- The SLO burn-rate warning later reached 8.52x because its 30-minute window retained incident errors.

## Timeline

| Time (UTC) | Event |
|---|---|
| 14:00:24 | Payments was stopped and background traffic continued |
| 14:00:55 | Prometheus showed the first 5xx ratio sample |
| 14:02:20 | High Error Rate entered Pending |
| 14:04:20 | High Error Rate fired |
| 14:04:49 | Critical webhook notification arrived |
| 14:04:57 | Runbook-driven investigation began |
| 14:05:04 | Health checks isolated payments as unavailable |
| 14:05:04 | Payments was restored with normal configuration |
| 14:06:20 | High Error Rate resolved |
| 14:06:47 | Resolved webhook notification arrived |

## Root cause

The payments process was unavailable while the gateway continued accepting payment requests. The gateway converted connection failures into controlled 503 responses, so the failure was contained but still consumed the availability error budget. The alert did not fire immediately because the service-level SLI aggregates all gateway traffic and payments represents only a fraction of requests.

## Contributing factors

- The standard mixed load sends relatively few payment requests, so the aggregate error ratio initially remained near the 5% threshold.
- Alert evaluation occurs once per minute and intentionally requires two minutes of sustained failure.
- The 30-minute burn-rate window stays elevated after service recovery, which is correct but can look like an ongoing incident without a short-window companion alert.

## What went well

- The gateway degraded gracefully and returned explicit 503 responses instead of crashing.
- Reads and events dependency health remained available.
- The alert fired at the configured threshold and pending duration.
- The webhook delivered both firing and resolved notifications.
- The runbook isolated the failing dependency in seven seconds.
- Recovery was verified using service health, alert state and a resolved notification.

## What could be improved

- The initial traffic profile was not strong enough to keep aggregate errors reliably above 5%; targeted payment traffic was needed.
- The runbook originally did not explicitly distinguish load-generator failures such as 409 from availability-impacting 5xx responses.
- A dependency-specific payments availability alert would identify the failure sooner than the aggregate gateway SLI.
- The burn-rate alert needs a paired short/long-window policy to reduce ambiguity after recovery.

## Action items

| Action | Owner | Priority |
|---|---|---|
| Add a payments dependency availability alert and dashboard panel | avlaptev | High |
| Add a deterministic payment-heavy incident load profile | avlaptev | High |
| Update the runbook to separate HTTP 5xx from expected 409 responses | avlaptev | Medium |
| Add multi-window burn-rate alerts for fast and slow budget consumption | avlaptev | Medium |
| Add an automated alert provisioning and webhook delivery smoke test | avlaptev | Medium |

The most important action is a **payments dependency availability alert**. The aggregate SLI needed almost four minutes to fire because payment calls were a small share of total traffic. A dependency-specific signal would shorten detection while the SLO alert continues to measure user impact.

## Bonus — Runbook: Redis unavailable

### Alert and impact

- **Symptom:** Events health reports Redis down; reservation requests time out or fail.
- **User impact:** Event reads may continue, but new reservations are unavailable.

### Diagnosis

1. Check gateway and events health:

   ```bash
   curl -s http://localhost:3080/health | python3 -m json.tool
   curl -s http://localhost:8081/health | python3 -m json.tool
   ```

2. Verify the Redis container state:

   ```bash
   docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml ps redis
   ```

3. Check events logs for Redis timeout or connection errors:

   ```bash
   docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml logs events --tail=50 --since=5m
   ```

4. Confirm impact with one reservation request. Do not repeatedly create reservations during diagnosis.

### Mitigation

```bash
docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml start redis
curl -s http://localhost:8081/health | python3 -m json.tool
```

Verify that both `postgres` and `redis` report `ok`, then run one reservation request and confirm a successful response.

### Escalation

- Escalate after 10 minutes if Redis cannot start or repeatedly exits.
- Include Redis state, events health, recent events logs and the last known successful reservation time.

### Local validation

The runbook was validated locally on 2026-09-26:

```text
14:07:34Z Redis stopped
events health: degraded, postgres=ok, redis=down
reservation result: HTTP 504, Events service timeout
14:07:45Z Redis started
events health: healthy, postgres=ok, redis=ok
```

The validation showed that checking events health should come before scanning logs: it identified Redis immediately, while the short log tail only showed the resulting 503 health check. The runbook was updated to preserve that order and to include one controlled reservation check.

### Classmate cross-test

- **Tester:** Danil Khasanshin (d.khasanshin@innopolis.university)
- **Result:** Successful
- **Resolution time:** Approximately 5–10 minutes
- **Diagnosis:** The tester followed every runbook step and correctly identified that Redis was unavailable.
- **Recovery:** Redis was restored and the procedure completed successfully.
- **Feedback:** No blocking ambiguities or missing steps were reported. The health-first diagnostic order was sufficient to isolate the failed dependency, so no additional runbook changes were required after the peer test.
