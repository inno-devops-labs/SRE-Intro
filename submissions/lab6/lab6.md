# Lab 6 — Alerting & Incident Response

## Task 1 — Create Alerts & Respond to an Incident

### 1. Full stack status

The QuickTicket application and monitoring stack were started with Docker Compose.

```bash
$ docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
```

All required containers were running:

```bash
$ docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
NAME               IMAGE                     SERVICE      STATUS                    PORTS
app-events-1       app-events                events       Up                         0.0.0.0:8081->8081/tcp
app-gateway-1      app-gateway               gateway      Up                         0.0.0.0:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    grafana      Up                         0.0.0.0:3000->3000/tcp
app-payments-1     app-payments              payments     Up                         0.0.0.0:8082->8082/tcp
app-postgres-1     postgres:17-alpine        postgres     Up (healthy)               0.0.0.0:15432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   prometheus   Up                         0.0.0.0:9090->9090/tcp
app-redis-1        redis:7-alpine            redis        Up (healthy)               0.0.0.0:16379->6379/tcp
```

Gateway health:

```bash
$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

Prometheus targets were up for `gateway`, `events`, and `payments`.

```bash
$ curl -s http://localhost:9090/api/v1/targets | python3 -m json.tool
...
"scrapePool": "gateway",
"health": "up",
...
"scrapePool": "events",
"health": "up",
...
"scrapePool": "payments",
"health": "up",
```

Grafana was healthy:

```bash
$ curl -s -u admin:admin http://localhost:3000/api/health | python3 -m json.tool
{
    "database": "ok",
    "version": "13.0.1",
    "commit": "a100054f"
}
```

Background traffic was generated:

```bash
$ ./loadgen/run.sh 10 600

QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 10 | Duration: 600s
---
```

---

### 2. Contact point

Contact point name: `quickticket-alerts`

Type: `webhook`

Webhook receiver: local HTTP receiver on the Docker host.

```text
http://172.21.0.1:9001/grafana
```

Contact point was created through the Grafana API:

```bash
$ curl -s -u admin:admin -H 'Content-Type: application/json' \
  -X POST http://localhost:3000/api/v1/provisioning/contact-points \
  -d '{
    "name": "quickticket-alerts",
    "type": "webhook",
    "settings": {
      "url": "http://172.21.0.1:9001/grafana",
      "httpMethod": "POST"
    },
    "disableResolveMessage": false
  }'
```

Created contact point:

```json
{
    "uid": "ffqeakiggh4aoc",
    "name": "quickticket-alerts",
    "type": "webhook",
    "settings": {
        "httpMethod": "POST",
        "url": "http://172.21.0.1:9001/grafana"
    },
    "disableResolveMessage": false
}
```

Notification policy:

```bash
$ curl -s -u admin:admin -H 'Content-Type: application/json' \
  -X PUT http://localhost:3000/api/v1/provisioning/policies \
  -d '{
    "receiver": "quickticket-alerts",
    "group_by": ["alertname"],
    "group_wait": "30s",
    "group_interval": "1m",
    "repeat_interval": "5m"
  }'

{"message":"policies updated"}
```

Notification evidence:

```text
2026-06-27T11:57:50.009936+00:00 /grafana {
  "receiver": "quickticket-alerts",
  "status": "firing",
  "alerts": [{
    "status": "firing",
    "labels": {
      "alertname": "QuickTicket High Error Rate",
      "grafana_folder": "QuickTicket",
      "severity": "critical"
    },
    "annotations": {
      "description": "Error rate exceeded 5% for the pending period. Check payments service health.",
      "summary": "Gateway error rate is 7.985044837147657%"
    }
  }]
}
```

Resolve notification evidence:

```text
2026-06-27T11:58:50.010431+00:00 /grafana {
  "receiver": "quickticket-alerts",
  "status": "resolved",
  "alerts": [{
    "status": "resolved",
    "labels": {
      "alertname": "QuickTicket High Error Rate",
      "severity": "critical"
    },
    "annotations": {
      "summary": "Gateway error rate is 4.912697474390587%"
    }
  }]
}
```

---

### 3. Alert rules

The final Grafana alert group uses `interval=60s`.

#### Alert 1 — QuickTicket High Error Rate

PromQL:

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

Condition: above `5`

Evaluation: every `1m`, for `2m`

Labels:

```text
severity=critical
```

Annotations:

```text
Summary: Gateway error rate is {{ $value }}%
Description: Error rate exceeded 5% for the pending period. Check payments service health.
```

#### Alert 2 — QuickTicket SLO Burn Rate

PromQL:

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

Condition: above `6`

Evaluation: every `1m`, for `5m`

Labels:

```text
severity=warning
```

During the lab incident test, the High Error Rate pending period was temporarily shortened to `30s` so the full inject-diagnose-resolve loop could be verified quickly. After the test, the rule was restored to the required `2m` pending period.

---

### 4. Incident injection

The payment failure rate was increased to `1.0`.

I used `1.0` instead of `0.5` because normal load generator traffic performs payment only for about 10% of requests. At `0.5`, the total gateway error rate may stay below the `5%` alert threshold.

```bash
$ date -u +%Y-%m-%dT%H:%M:%SZ
2026-06-27T11:53:51Z

$ docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
Container app-payments-1 Stopped

$ PAYMENT_FAILURE_RATE=1.0 docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
Container app-payments-1 Started

$ date -u +%Y-%m-%dT%H:%M:%SZ
2026-06-27T11:53:53Z
```

Additional payment-focused traffic was generated to make the failure visible above the 5% threshold.

Alert firing evidence:

```bash
$ curl -s -u admin:admin http://localhost:3000/api/prometheus/grafana/api/v1/rules
...
QuickTicket High Error Rate state= firing health= ok alerts= [('Alerting', '2026-06-27T11:57:20Z', '1e+00')]
...
```

Error rate during the incident:

```bash
$ curl -s --get http://localhost:9090/api/v1/query \
  --data-urlencode 'query=sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100'

error_rate_5m=7.985044837147657
```

---

## Runbook: QuickTicket High Error Rate

### Alert

- **Fires when:** Gateway 5xx error rate > 5% for 2 minutes
- **Dashboard:** QuickTicket — Golden Signals
- **Severity:** critical
- **Primary suspected dependency:** payments service

### Diagnosis

1. Check gateway health:

   ```bash
   curl -s http://localhost:3080/health | python3 -m json.tool
   ```

2. Check payments service directly:

   ```bash
   curl -s http://localhost:8082/health | python3 -m json.tool
   ```

3. Check events service:

   ```bash
   curl -s http://localhost:8081/health | python3 -m json.tool
   ```

4. Check recent gateway logs:

   ```bash
   docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs gateway --tail=20 --since=5m
   ```

5. Check recent payments logs:

   ```bash
   docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs payments --tail=20 --since=5m
   ```

6. Check current error rate:

   ```bash
   curl -s --get http://localhost:9090/api/v1/query \
     --data-urlencode 'query=sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100'
   ```

### Common Causes

| Cause | How to identify | Fix |
| ----- | --------------- | --- |
| Payments service down | Gateway health shows `payments: down`; payments port does not answer | `docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start payments` |
| Payments injected failure rate | Payments health shows `failure_rate > 0`; payments logs show injected 500s | Restart payments with `PAYMENT_FAILURE_RATE=0.0` |
| Events service down | Gateway health shows `events: degraded`; events health fails | Restart events and verify postgres/redis |
| Redis or Postgres dependency issue | Events health shows `redis` or `postgres` degraded | Restart dependency, then restart events |
| Gateway timeout too low | Logs show timeout errors while dependencies are otherwise healthy | Increase `GATEWAY_TIMEOUT_MS` and redeploy |

### Mitigation

Restore payments to normal:

```bash
docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_FAILURE_RATE=0.0 docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

Verify:

```bash
curl -s http://localhost:8082/health | python3 -m json.tool
curl -s http://localhost:3080/health | python3 -m json.tool
```

Expected:

```json
{
    "status": "healthy",
    "failure_rate": 0.0,
    "latency_ms": 0
}
```

### Escalation

If the alert does not return to Normal within 10 minutes after mitigation:

- escalate to instructor/TA;
- attach gateway logs, payments logs, Grafana alert state, and Prometheus query output;
- include exact incident start and mitigation timestamps.

---

### 5. Runbook execution evidence

Diagnosis started:

```bash
$ date -u +%Y-%m-%dT%H:%M:%SZ
2026-06-27T11:57:45Z
```

Gateway health:

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

Payments health showed the root cause:

```json
{
    "status": "healthy",
    "failure_rate": 1.0,
    "latency_ms": 0
}
```

Events health was normal:

```json
{
    "status": "healthy",
    "checks": {
        "postgres": "ok",
        "redis": "ok"
    }
}
```

Payments logs confirmed injected payment failures:

```text
payments-1 | "POST /charge HTTP/1.1" 500 Internal Server Error
payments-1 | Payment failed (injected) for 0d2e9b21-77f1-4ab3-ace1-a02172604152
payments-1 | "POST /charge HTTP/1.1" 500 Internal Server Error
payments-1 | Payment failed (injected) for c8fa6c06-ca02-4f79-bdd1-0cdae2b550fc
```

Fix applied:

```bash
$ date -u +%Y-%m-%dT%H:%M:%SZ
2026-06-27T11:58:03Z

$ docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
Container app-payments-1 Stopped

$ PAYMENT_FAILURE_RATE=0.0 docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
Container app-payments-1 Started

$ date -u +%Y-%m-%dT%H:%M:%SZ
2026-06-27T11:58:05Z
```

Final health:

```json
{
    "status": "healthy",
    "failure_rate": 0.0,
    "latency_ms": 0
}
```

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

Alert resolved:

```bash
$ curl -s -u admin:admin http://localhost:3000/api/prometheus/grafana/api/v1/rules
...
QuickTicket High Error Rate state= inactive health= ok alerts= [('Normal', '2026-06-27T11:58:10Z', '')]
...
```

Resolved notification:

```text
2026-06-27T11:58:50.010431+00:00 /grafana
"status": "resolved"
"alertname": "QuickTicket High Error Rate"
"summary": "Gateway error rate is 4.912697474390587%"
```

---

### 6. Timeline

| Time (UTC) | Event |
| ---------- | ----- |
| 11:53:51 | Failure injection started: payments restarted with `PAYMENT_FAILURE_RATE=1.0` |
| 11:53:53 | Faulty payments container started |
| 11:56:50 | High Error Rate condition entered Pending |
| 11:57:20 | High Error Rate became Alerting/Firing |
| 11:57:45 | Investigation started using the runbook |
| 11:57:45 | Root cause identified: payments health showed `failure_rate: 1.0` |
| 11:58:03 | Fix started: payments restarted with `PAYMENT_FAILURE_RATE=0.0` |
| 11:58:05 | Fixed payments container started |
| 11:58:10 | Grafana alert state returned to Normal |
| 11:58:50 | Resolve notification received by webhook |

### Alert delay

Failure injection to alert firing took about **3 minutes 29 seconds**: from `11:53:51` to `11:57:20`.

The delay happened because:

- Grafana does not alert instantly; it evaluates on a schedule.
- The rule uses a `rate(...[5m])` window, so the measured error rate changes gradually.
- The normal load generator sends payment requests for only about 10% of traffic, so the overall gateway error rate needed additional payment-focused traffic to stay above 5%.
- The alert must remain above the threshold for the pending period before becoming Firing.

---

## Task 2 — Blameless Postmortem

# Postmortem: QuickTicket Payment Failure Injection Caused High Gateway Error Rate

**Date:** 2026-06-27  
**Duration:** 11:53:51 → 11:58:10 UTC  
**Severity:** SEV-3  
**Author:** Gabdullin

## Summary

Payments were restarted with an injected failure rate of `1.0`, causing all payment charge attempts to return 500 errors. Gateway 5xx rate exceeded the 5% alert threshold and triggered the `QuickTicket High Error Rate` alert.

## Timeline

| Time | Event |
| ---- | ----- |
| 11:53:51 | Failure injected by restarting payments with `PAYMENT_FAILURE_RATE=1.0` |
| 11:53:53 | Faulty payments container started |
| 11:56:50 | Grafana alert entered Pending |
| 11:57:20 | Alert fired |
| 11:57:45 | Investigation started |
| 11:57:45 | Root cause identified from payments health: `failure_rate: 1.0` |
| 11:58:03 | Fix applied by restarting payments with `PAYMENT_FAILURE_RATE=0.0` |
| 11:58:10 | Alert returned to Normal |
| 11:58:50 | Resolve notification received |

## Root Cause

The payments service was running with `PAYMENT_FAILURE_RATE=1.0`, which made every `/charge` request return a 500 response. The gateway converted failed payment requests into 503 responses, increasing the gateway 5xx rate above the alert threshold.

This was possible because the runtime configuration allowed the payment failure rate to be changed without an automated guardrail or deploy-time validation.

## What Went Well

- Prometheus was scraping all required services.
- Grafana detected the high gateway error rate.
- The webhook contact point received both firing and resolved notifications.
- The runbook quickly narrowed the issue to payments.
- Payments health exposed `failure_rate`, which made the root cause easy to confirm.

## What Went Wrong

- The default mixed load generator produced too few payment requests to cross the 5% error threshold quickly.
- The initial alert evaluation saw `NoData` while the 5-minute rate window was still warming up.
- The runbook needed an explicit step to check `PAYMENT_FAILURE_RATE` in the payments health response.
- A local webhook receiver is useful for lab testing, but production alerting should use a durable channel such as Slack, Discord, PagerDuty, or email.

## Action Items

| Action | Owner | Priority |
| ------ | ----- | -------- |
| Add `PAYMENT_FAILURE_RATE` check to the runbook | Gabdullin | High |
| Add a payment-specific alert for `payments_requests_total{status=~"5.."}` | Gabdullin | High |
| Add a synthetic payment-check job so payment failures are visible even under read-heavy traffic | Gabdullin | Medium |
| Add a startup warning when `PAYMENT_FAILURE_RATE` is not `0.0` outside test mode | Gabdullin | Medium |
| Document alert warm-up behavior for `rate(...[5m])` windows | Gabdullin | Low |

### Most important action item

The most important action item is adding a payment-specific alert for `payments_requests_total{status=~"5.."}`.

The gateway high-error-rate alert is useful for user impact, but it can be diluted by read traffic. A payment-specific alert would detect payment failures faster and would point responders directly to the failing dependency.

---

## Bonus Task — Cross-Test Runbooks

### Second Runbook: Redis Down

#### Alert

- **Failure mode:** Redis unavailable
- **User impact:** reservations may fail or become unreliable
- **Expected symptom:** events service health becomes degraded, gateway reservation flow returns errors

#### Diagnosis

1. Check gateway health:

   ```bash
   curl -s http://localhost:3080/health | python3 -m json.tool
   ```

2. Check events health:

   ```bash
   curl -s http://localhost:8081/health | python3 -m json.tool
   ```

3. Check Redis container:

   ```bash
   docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps redis
   ```

4. Check Redis directly:

   ```bash
   docker exec app-redis-1 redis-cli ping
   ```

5. Check events logs:

   ```bash
   docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs events --tail=40 --since=5m
   ```

#### Common Causes

| Cause | How to identify | Fix |
| ----- | --------------- | --- |
| Redis container stopped | `ps redis` shows stopped/exited | `docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start redis` |
| Redis not responding | `redis-cli ping` fails | Restart Redis |
| Events service has stale Redis connection | Redis is healthy but events still degraded | Restart events |
| Redis timeout too low | Logs show intermittent Redis timeout | Increase `REDIS_TIMEOUT_MS` |

#### Fix

```bash
docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start redis
docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml restart events
```

#### Verification

```bash
curl -s http://localhost:8081/health | python3 -m json.tool
curl -s http://localhost:3080/health | python3 -m json.tool
```

Expected:

```json
{
    "status": "healthy",
    "checks": {
        "postgres": "ok",
        "redis": "ok"
    }
}
```

#### Escalation

If Redis cannot be restored in 10 minutes, escalate with:

- Redis container status;
- Redis logs;
- events logs;
- gateway health output.

### Cross-test result

Redis failure was injected and the Redis runbook was tested against the live stack.

Failure injection:

```bash
$ docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop redis
[+] stop 1/1
 ✔ Container app-redis-1 Stopped
```

Diagnosis start:

```bash
$ date -u +%Y-%m-%dT%H:%M:%SZ
2026-06-27T12:06:50Z
```

Events service showed Redis as down:

```bash
$ curl -s http://localhost:8081/health | python3 -m json.tool
{
    "status": "degraded",
    "checks": {
        "postgres": "ok",
        "redis": "down"
    }
}
```

Gateway showed the upstream events dependency as down:

```bash
$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "degraded",
    "checks": {
        "events": "down",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

Direct Redis check confirmed the container was not running:

```bash
$ docker exec app-redis-1 redis-cli ping
Error response from daemon: container ... is not running
```

Fix:

```bash
$ date -u +%Y-%m-%dT%H:%M:%SZ
2026-06-27T12:07:25Z

$ docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start redis
Container app-redis-1 Started

$ docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml restart events
Container app-events-1 Started

$ date -u +%Y-%m-%dT%H:%M:%SZ
2026-06-27T12:07:27Z
```

Verification:

```bash
$ docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps redis
NAME          IMAGE            SERVICE   STATUS
app-redis-1   redis:7-alpine   redis     Up 25 seconds (healthy)

$ docker exec app-redis-1 redis-cli ping
PONG
```

Events recovered:

```bash
$ curl -s http://localhost:8081/health | python3 -m json.tool
{
    "status": "healthy",
    "checks": {
        "postgres": "ok",
        "redis": "ok"
    }
}
```

Gateway recovered:

```bash
$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

Result:

```text
Resolved: yes
Diagnosis time: about 35 seconds
Recovery command time: about 2 seconds
End-to-end time from diagnosis start to healthy verification: about 40 seconds
```

What was unclear or missing:

- The runbook should say that gateway may report `events: down` even though the real root cause is Redis.
- The runbook should include `docker-compose ... restart events` after Redis starts, because restarting events clears stale dependency state faster.
- The direct `redis-cli ping` check fails with a Docker error if the container is stopped; that is still useful evidence.

Runbook update applied:

- Added gateway symptom interpretation.
- Kept both `start redis` and `restart events` in the fix section.
- Added direct Redis verification with `redis-cli ping`.

Strict classmate cross-test note:

```text
This was a live runbook test, but not yet an independent classmate test.
For the full bonus requirement, give the Redis runbook to a classmate,
repeat the same Redis-down injection, and replace this note with their
result, timing, and feedback.
```
