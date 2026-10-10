# Runbook: QuickTicket – reservation and purchase failures

Use this runbook to diagnose and restore the local QuickTicket stack. Record the commands, observations, restoration time and any unclear steps.

## Alert

Use the QuickTicket Golden Signals dashboard and failed reservation requests.
The aggregate High Error Rate alert is not guaranteed to detect this failure:
this implementation can reserve without holding data when Redis is unavailable.
Symptoms may instead be failed payment confirmation and inconsistent reservations.

## Diagnosis

Open a terminal in `/Users/kuji/notes/uni/f2026/SRE/SRE-Intro/app/`. Run the commands in the same shell:

```bash
DC=(docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml)
curl -sS --max-time 5 http://localhost:3080/health
curl -sS --max-time 5 http://localhost:8081/health
"${DC[@]}" ps -a redis events
"${DC[@]}" logs events --tail=40 --since=5m
"${DC[@]}" exec -T redis redis-cli ping
```

A stopped Redis container, refused connections, or `Redis unavailable – reservation
not held` identify the fault. Gateway health is insufficient: inspect dependency
health and logs. Distinguish database errors from Redis errors before mitigation.

## Common Causes

| Cause | Evidence | Mitigation |
|---|---|---|
| Redis stopped | ps shows exited; ping cannot execute | Start Redis |
| Redis unreachable | running container, ping succeeds locally but events logs show connection errors | Inspect shared network and REDIS_HOST/REDIS_PORT |
| Events started without Redis client | Redis is restored but events still cannot store reservations | Restart events after Redis is healthy |

## Mitigation

```bash
"${DC[@]}" start redis
"${DC[@]}" exec -T redis redis-cli ping
# Expect PONG. If events did not reconnect:
"${DC[@]}" restart events
```

Never delete Redis or PostgreSQL volumes to repair connectivity.

## Verification

Repeat health checks. Use event 3 (if tickets remain) to reserve and pay:

```bash
curl -sS --max-time 5 -X POST -H 'Content-Type: application/json' \
  -d '{"quantity":1}' http://localhost:3080/events/3/reserve
# Copy the returned reservation_id into the URL:
curl -sS --max-time 5 -X POST http://localhost:3080/reserve/RESERVATION_ID/pay
```

Expect a confirmed order, not merely healthy endpoints. Check events logs for
new Redis errors and confirm the Golden Signals recover under traffic.

## Escalation

If not mitigated within 10 minutes, contact the instructor/TA with dependency
health, container status, timestamps and logs.

