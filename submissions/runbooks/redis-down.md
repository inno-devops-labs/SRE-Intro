# Runbook: QuickTicket Redis Unavailable

## Purpose

Diagnose and recover QuickTicket when Redis becomes unavailable and reservations fail or become unreliable. Run commands from the repository root (`SRE-Intro-lab6`).

## Alert and symptoms

Investigate this runbook when one or more of the following occur:

- The Events health endpoint reports `status: degraded`.
- The Events health check reports `redis: down`.
- Reservation requests through Gateway time out or return HTTP `5xx` responses.
- Grafana shows an increase in Gateway errors or an availability alert.

Dashboard: **QuickTicket — Golden Signals**  
Services involved: `redis`, `events`, and `gateway`.

## 1. Diagnose the incident

### 1.1 Check Events health

```bash
curl -s http://localhost:8081/health
```

Expected healthy response includes `status: healthy`, `postgres: ok`, and `redis: ok`. If the response reports `redis: down`, continue below.

### 1.2 Check Redis container state

```bash
docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml ps redis
```

If Redis is stopped, start it using the mitigation steps. If it is running but health remains degraded, inspect logs.

### 1.3 Inspect Redis logs

```bash
docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml logs redis --tail=30
```

Look for startup failures, repeated restarts, connection errors, or other Redis errors.

### 1.4 Inspect Events logs

```bash
docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml logs events --tail=30 --since=5m
```

Look for Redis connection errors, timeout messages, or reservation-related exceptions.

### 1.5 Check Gateway health

```bash
curl -s http://localhost:3080/health
```

Review the response for degraded dependencies or other service failures. Gateway health may not directly expose Redis because Events owns the Redis connection.

### 1.6 Test reservation behavior only if needed

Use one controlled request and record its HTTP status:

```bash
curl -s -w '\nHTTP %{http_code}\n' \
  -X POST http://localhost:3080/events/3/reserve \
  -H 'Content-Type: application/json' \
  -d '{"quantity":1}'
```

During the lab test with Redis stopped, this request returned HTTP `504` (`Events service timeout`). Do not repeatedly create reservations during diagnosis: a successful request may consume event inventory.

## 2. Mitigation and recovery

### 2.1 Start Redis if it is stopped

```bash
docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml start redis
```

Do not delete Redis data or volumes as an initial troubleshooting step.

### 2.2 Wait briefly and recheck Events health

```bash
curl -s http://localhost:8081/health
```

Expected recovery response includes `status: healthy`, `postgres: ok`, and `redis: ok`. The Events service caches its Redis health result briefly, so if the first check still says `redis: down`, wait a few seconds and check again.

### 2.3 Confirm reservation recovery

Once Events health is healthy, perform one controlled reservation request:

```bash
curl -s -w '\nHTTP %{http_code}\n' \
  -X POST http://localhost:3080/events/3/reserve \
  -H 'Content-Type: application/json' \
  -d '{"quantity":1}'
```

Expected result: a reservation response and HTTP `200`. Record the response and timestamp. Avoid repeating the request unnecessarily.

### 2.4 Check monitoring

Review Grafana Alerting → Alert rules. Confirm whether any relevant alerts remain active and verify that they return to `Normal` after the underlying problem is resolved and the evaluation window has passed.

## 3. Decision table

| Symptom | How to identify | Action |
|---|---|---|
| Redis container is stopped | `docker compose ... ps redis` shows it is not running | Run the `start redis` command in section 2.1 |
| Redis container runs, but Events reports `redis: down` | Events health endpoint reports `degraded`; inspect Redis and Events logs | Check logs and recheck health after a short wait; escalate if it persists |
| Events reports healthy, but reservation requests fail | Check Gateway response and Events logs | Investigate the specific response/logs; do not assume Redis is the cause |
| Redis is healthy but Gateway returns 504 | Compare Gateway and Events logs and health endpoints | Investigate Events responsiveness and network/timeouts; escalate if unresolved |
| Service does not recover within 10 minutes | Redis/Events health remains unhealthy or reservations still fail | Escalate to the instructor/TA with timestamps, health outputs, logs, and alert state |

## 4. Verification checklist

- [ ] Redis container is running.
- [ ] Events health reports `status: healthy`.
- [ ] Events health reports `redis: ok`.
- [ ] One controlled reservation succeeds with HTTP `200`.
- [ ] Relevant Grafana alerts have returned to `Normal`, where applicable.
- [ ] Incident timestamps and observed outputs are recorded.

## 5. Escalation

If Redis remains unavailable or reservations do not recover within **10 minutes**, escalate to the instructor/TA. Include incident start and recovery timestamps, Events/Gateway health outputs, Redis and Events logs, reservation HTTP status, and Grafana alert state.

## 6. Safety notes

- Do not delete Redis data or volumes as a first response.
- Do not restart unrelated services without evidence that they are involved.
- Use only one controlled reservation request during verification.
- Clearly distinguish observed facts from assumptions in incident notes.

## Lab validation notes

The following behavior was observed during the Lab 6 Redis failure exercise:

| Stage | Observed result |
|---|---|
| Before failure | Events health was `healthy`; PostgreSQL and Redis checks were `ok` |
| Redis stopped | Events health changed to `degraded`; Redis check reported `down` |
| Reservation during failure | Gateway returned HTTP `504` with `Events service timeout` |
| Redis restarted | Events health returned to `healthy`; PostgreSQL and Redis checks were `ok` |
| Reservation after recovery | Reservation request succeeded with HTTP `200` |

These are the results of the author's test. They are **not** evidence of an independent classmate/peer test. Complete the peer-test section in `submissions/lab6.md` only after a classmate has followed this runbook without being told which failure was injected, and record the actual result, duration, and feedback.
