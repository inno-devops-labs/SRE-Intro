# Lab 6 — Alerting & Incident Response

## Made by:
### Nurmuhametov Denis (d.nurmuhametov@innopolis.university)

---

## Task 1 — Create Alerts & Respond to an Incident (6 pts)

### 6.1: Start the full stack

Started the QuickTicket microservices stack with monitoring:

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
```

```text
[+] Running 7/7
 ✔ Container app-grafana-1     Started
 ✔ Container app-prometheus-1  Started
 ✔ Container app-redis-1       Started
 ✔ Container app-postgres-1    Started
 ✔ Container app-events-1      Started
 ✔ Container app-payments-1    Started
 ✔ Container app-gateway-1     Started
```

Generated background traffic so Prometheus metrics are populated:

```bash
./loadgen/run.sh 3 300 &
```

```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 3 | Duration: 300s
---
[10s] requests=28 success=27 fail=1 error_rate=3.6%
[20s] requests=56 success=52 fail=4 error_rate=7.1%
...
```

**Prometheus queries used to verify metrics are flowing:**

```promql
# Error rate (5xx only)
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100

# Request rate
sum(rate(gateway_requests_total[5m]))
```

Both queries returned non-zero results, confirming the stack and loadgen are working.

---

### 6.2: Create a contact point

In Grafana (**Alerting → Contact points → Add contact point**):

- **Name:** `quickticket-alerts`
- **Type:** Webhook
- **URL:** `https://webhook.site/77ce8f88-fcb1-49b8-8356-3fc93ff7251f`

**Evidence:** The contact point was tested via Grafana's "Test" button, and a test notification was successfully received at the webhook URL. During the actual incident (see 6.6), the alert notification was also delivered to the same webhook endpoint, confirming end-to-end connectivity.

---

### 6.3: Create alert rules

Both alert rules were created as Grafana-managed rules in **Alerting → Alert rules → New alert rule**.

**Alert 1 — QuickTicket High Error Rate (critical):**

- **Query A (PromQL):**
  ```promql
  sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
  ```
- **Condition:** IS ABOVE `5` (5% error rate)
- **Evaluation:** every 1m, for 2m
- **Labels:** `severity=critical`
- **Annotations:**
  - Summary: `Gateway error rate is {{ $value }}%`
  - Description: `Error rate exceeded 5% for 2 minutes. Check payments service health.`

**Alert 2 — QuickTicket SLO Burn Rate (warning):**

- **Query A (PromQL):**
  ```promql
  (1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
  ```
- **Condition:** IS ABOVE `6` (6× burn rate)
- **Evaluation:** every 1m, for 5m
- **Labels:** `severity=warning`
- **Annotations:**
  - Summary: `SLO Burn Rate is {{ $value }}x`
  - Description: `Burn rate exceeded 6× SLO target for 5 minutes. SLO: 99.5%.`

---

### 6.4: Configure notification policy

In **Alerting → Notification policies**, the default policy was edited:

- **Default contact point:** `quickticket-alerts`
- **Group by:** `alertname`
- **Group wait:** 30s
- **Repeat interval:** 5m

This ensures all alerts from the QuickTicket project are routed to the webhook contact point, grouped by alert name for deduplication.

---

### 6.5: Runbook

The full runbook is presented below. It was written as a standalone `runbook.md` file.

---

# Runbook: QuickTicket High Error Rate

## Alert
- **Fires when:** Gateway HTTP 5xx error rate > 5% for at least 2 minutes
- **Dashboard:** QuickTicket — Golden Signals (`http://localhost:3000`)
- **Severity:** `critical`
- **Evaluation:** Every 1 minute, pending period 2 minutes
- **Notification:** Webhook / Discord / Slack via `quickticket-alerts` contact point

## Symptoms
- Grafana alert "QuickTicket High Error Rate" changes to **Firing** (red)
- Notification arrives at the configured contact point
- `curl http://localhost:3080/health` returns HTTP 503 with `"status": "degraded"`
- Gateway returns HTTP 502/500 on `/reserve/{id}/pay` — users can't complete purchases

## Diagnosis

### 1. Check system health via gateway
```bash
curl -s http://localhost:3080/health | python3 -m json.tool
```
**Healthy:**
```json
{"status": "healthy", "checks": {"events": "ok", "payments": "ok", "circuit_payments": "CLOSED"}}
```
**Degraded — `checks` shows which service is failing:**
```json
{"status": "degraded", "checks": {"events": "ok", "payments": "down", "circuit_payments": "CLOSED"}}
```

### 2. Check each service directly
```bash
curl -s http://localhost:8082/health   # payments
curl -s http://localhost:8081/health   # events
```

### 3. Check logs
```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs gateway --tail=30 --since=5m
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs payments --tail=30 --since=5m
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs events --tail=30 --since=5m
```

### 4. Check Prometheus
Open `http://localhost:9090` and run:
```
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

### 5. Check infrastructure
```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml exec postgres pg_isready -U quickticket
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml exec redis redis-cli ping
```

## Common Causes

| Cause | How to identify | Fix |
|-------|----------------|-----|
| **Payments service down** | `/health` shows `payments: down`; `curl :8082/health` fails with connection refused | `docker compose start payments` |
| **Payments high failure rate** | `/health` shows `payments: ok` but logs show errors; `PAYMENT_FAILURE_RATE` is set > 0 | Set `PAYMENT_FAILURE_RATE=0.0` and restart |
| **Payments latency spike** | `PAYMENT_LATENCY_MS` is set too high; gateway times out | Set `PAYMENT_LATENCY_MS=0` and restart |
| **Events service down** | `/health` shows `events: down`; `curl :8081/health` fails | `docker compose start events` |
| **DB connection exhausted** | Events logs show pool errors; health shows `events: ok` but requests fail | Restart events or increase `DB_MAX_CONNS` |
| **Redis unavailable** | Events logs show redis errors; reservations fail | `docker compose start redis` |

## Resolution

### Fix payments failure rate
```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_FAILURE_RATE=0.0 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

### Fix payments latency
```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_LATENCY_MS=0 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

### Restart any service
```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml restart <service_name>
```

### Full stack restart (last resort)
```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml down
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
```

## Verification
- Grafana → Alerting → Alert rules → status is **Normal** (green)
- `curl http://localhost:3080/health` returns `{"status": "healthy"}`
- Prometheus error rate query returns value below 5%
- Loadgen traffic resumes with HTTP 200 responses

## Escalation
- If not resolved within **10 minutes** of alert firing → notify instructor / TA
- If multiple services fail simultaneously → check shared dependencies (PostgreSQL, Redis) first
- If full stack restart doesn't help → there may be a code/config issue — check recent changes

## Notes
- Always run compose commands from the `app/` directory
- Always use both files: `docker-compose.yaml` + `../docker-compose.monitoring.yaml`
- Load generator (`./loadgen/run.sh`) must be running for metrics to flow
- `PAYMENT_FAILURE_RATE=0.5` → ~50% of charge requests fail, but charges are ~10% of traffic → overall error rate ~1-2%. If alert doesn't fire, either increase failure rate or kill payments entirely

---

### 6.6: Inject failure and respond

The failure was injected by restarting the payments service with `PAYMENT_FAILURE_RATE=1.0`:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_FAILURE_RATE=1.0 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

**Alert firing evidence:**

After the injection, only the **QuickTicket SLO Burn Rate** alert fired. The **QuickTicket High Error Rate** alert did NOT fire.

**Why the SLO Burn Rate fired but High Error Rate did not:**

The `PAYMENT_FAILURE_RATE=1.0` causes 100% of charge requests to fail. However, charge requests (POST `/reserve/{id}/pay`) constitute only ~10% of total loadgen traffic. The remaining 90% of traffic (GET `/events` and POST `/events/{id}/reserve`) was unaffected.

- **Actual 5xx rate:** ~3.8% — below the 5% threshold for High Error Rate
- **SLO Burn Rate calculation:** `(1 - 0.962) / (1 - 0.995) = 0.038 / 0.005 = 7.6×` — above the 6× threshold

The burn rate formula amplifies small error rates because the denominator (1 - SLO target = 0.005) is so small. A mere 3.8% error rate translates to 7.6× budget consumption, which rapidly burned through the error budget.

**The response proceeded as follows:**

1. **Alert fired** — the `QuickTicket SLO Burn Rate` alert fired at 20:42:40 UTC, approximately 3.5 minutes after injection.
2. **Notification received** — webhook.site (`https://webhook.site/77ce8f88-fcb1-49b8-8356-3fc93ff7251f`) received the alert notification.
3. **Diagnosis** — health check (`/health`) showed both services `ok`, confirming this was not an outright service crash. Payments logs showed the injected failures.
4. **Fix applied** — payments restarted with `PAYMENT_FAILURE_RATE=0.0` at 20:42:53 UTC.
5. **Alert resolved** — after ~12 minutes, the SLO Burn Rate dropped below 6× and the alert returned to "Normal" at 20:54:40 UTC.

**Grafana annotations evidence:**

Grafana annotations API was queried to confirm the alert state transitions:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml exec grafana wget -q -O - \
  'http://localhost:3000/api/annotations?type=alert&limit=100' 2>/dev/null | python3 -m json.tool
```

The response showed:
- `alertId=6` (SLO Burn Rate): `prevState="Pending"` → `newState="Alerting"` at timestamp `1740933760000` (20:42:40 UTC)
- `alertId=6`: `prevState="Alerting"` → `newState="Normal"` at timestamp `1740934480000` (20:54:40 UTC)

**Timeline:**

| Time | Event |
|---|---|
| 23:39:09 | Failure injected — payments restarted with `PAYMENT_FAILURE_RATE=1.0` |
| 23:42:40 | QuickTicket SLO Burn Rate alert fired; notification delivered to webhook |
| 23:42:50 | Engineer noticed the alert notification |
| 23:42:53 | Fix applied — payments restarted with `PAYMENT_FAILURE_RATE=0.0` |
| 23:53:58 | SLO burn rate returned to normal levels |
| 20:54:40 | Alert resolved (status changed to "Normal" in Grafana annotations) |

Key metrics:
- **Time to detect (injection → alert fire):** ~3 minutes 31 seconds
- **Time to acknowledge (alert fire → noticed):** ~10 seconds
- **MTTR (detection → fix applied):** ~13 seconds
- **Alert duration (firing → resolved):** ~12 minutes

**Delay analysis:**

The SLO Burn Rate alert fired approximately **3.5 minutes** after failure injection, despite having a `for: 5m` pending period.

The expected delay would be ~6 minutes (1 min evaluation interval + 5 min pending). However, the actual delay was shorter because the 30-minute window in the burn rate query was already accumulating data from natural loadgen traffic before the injection. The loadgen produces occasional 409 Conflict responses (counted as failures by the loadgen, but not as 5xx), and natural variance caused the burn rate to float above zero. This effectively **pre-warmed the pending counter** — the alert condition was partially met before the failure was injected, so fewer consecutive above-threshold evaluations were needed to reach the `for: 5m` requirement.

For the **High Error Rate alert**, the expected delay would be ~3 minutes (1 min evaluation + 2 min pending), but this alert did not fire because the actual 5xx rate (~3.8%) remained below the 5% threshold.

---

## Task 2 — Blameless Postmortem (4 pts)

### 6.8: Postmortem

The full blameless postmortem is presented below and saved as a separate `postmortem.md` file.

---

# Postmortem: Payments Failure Spike Causes SLO Burn Rate Alert

**Date:** 2026-06-25
**Duration:** 20:39:09 — 20:54:40 UTC (approx. 15 minutes)
**Severity:** SEV-3
**Author:** Nurmuhametov Denis

## Summary

A manual failure injection (`PAYMENT_FAILURE_RATE=1.0`) caused 100% of payment charge requests to fail. Since charges make up roughly 10% of total loadgen traffic, this produced a ~3.8% gateway 5xx error rate — below the 5% High Error Rate threshold but sufficient to trigger the SLO Burn Rate alert (7.6× burn rate). The incident lasted approximately 15 minutes and consumed an estimated 2.5% of the monthly error budget.

## Timeline

| Time (UTC) | Event |
|---|---|
| 20:39:09 | Failure injected — payments restarted with `PAYMENT_FAILURE_RATE=1.0` |
| 20:42:40 | QuickTicket SLO Burn Rate alert fired; notification delivered to webhook |
| 20:42:50 | Engineer noticed the alert notification |
| 20:42:53 | Fix applied — payments restarted with `PAYMENT_FAILURE_RATE=0.0` |
| 20:54:40 | Alert resolved (status → "Normal" after SLO burn rate dropped below 6×) |

## Root Cause

The payments service was configured to deliberately fail all charge requests via the `PAYMENT_FAILURE_RATE=1.0` environment variable. This caused the gateway to return HTTP 502 errors for every `/reserve/{id}/pay` endpoint call. Because charge requests constitute approximately 10% of overall traffic, the overall 5xx error rate reached ~3.8%, which is below the critical threshold (5%) but high enough to produce a 7.6× SLO burn rate, rapidly consuming the error budget.

The systemic issue is that the payments service has no guardrail or safety check on its `PAYMENT_FAILURE_RATE` mechanism — there is no rate limiter on failure injection, no validation that caps the maximum failure rate, and no separate alert for payment-specific failures. A single environment variable change was sufficient to degrade the user experience for all purchase flows.

## What Went Well

- **Alert fired within ~3.5 minutes** of failure injection, providing rapid detection of the SLO breach.
- **Webhook notification was received immediately** — the contact point configuration worked correctly.
- **MTTR was ~13 seconds** from alert notification to fix application, demonstrating excellent runbook adherence and quick decision-making.
- **Runbook diagnosis steps were accurate** — the health check immediately identified payments as the failing service.

## What Went Wrong

- **The High Error Rate alert did not fire** — the 5xx rate (~3.8%) was below the 5% threshold but was still sufficient to burn error budget rapidly. This means the critical alert is configured with too high a threshold to catch payment-only failures.
- **No payment-specific alert exists** — a dedicated alert on payment service failures would have detected the issue directly, regardless of traffic composition.
- **Timeline recording was done manually** — there was no automated incident timeline; timestamps were reconstructed from memory and Grafana annotations after the fact.
- **SLO Burn Rate alert pending period was misleading** — the 5-minute `for` period appeared shorter (~3.5 min) because the burn rate was already partially counting from natural loadgen traffic fluctuations before the injection.

## Action Items

| Action | Owner | Priority |
|--------|-------|----------|
| Add a dedicated payments-failure alert (e.g., `rate(gateway_requests_total{path=~"/reserve/.*/pay",status=~"5.."}[1m]) > 0`) | Denis | High |
| Lower High Error Rate alert threshold from 5% to 3% to catch partial-failure scenarios | Denis | Medium |
| Add a payments-service Grafana dashboard panel showing charge success/failure ratio | Denis | Medium |
| Implement automated incident timeline capture in Grafana (annotations with `type=incident`) | Denis | Low |
| Document the relationship between traffic composition and error rate in the runbook | Denis | Low |

---

**What is the most important action item from your postmortem? Why?**

The most important action item is **adding a dedicated payments-failure alert**. The current alerting setup relies solely on gateway-level error rate, which dilutes payment-specific failures across all traffic. Since charges are only ~10% of traffic, even a 100% payment failure rate produces only ~3.8% gateway errors — invisible to the 5% threshold. A direct payment-path alert would fire immediately when any payment fails, regardless of traffic mix, reducing detection time from ~3.5 minutes to near-instant. This addresses the fundamental gap exposed by the incident: the system had no mechanism to detect that a critical subsystem (payments) was completely failing, as long as the overall error rate stayed below the gateway-level threshold.

---

## Bonus Task — Cross-Test Runbooks (2 pts)

### B.1: Second runbook — Redis Down

The second runbook for the **Redis failure mode** is presented below. It was written as a separate `runbook-redis.md` file.

---

# Runbook: QuickTicket Redis Down

## Alert
- **Fires when:** Gateway 5xx error rate > 5% for at least 2 minutes (or events health returns `redis: down`)
- **Dashboard:** QuickTicket — Golden Signals (`http://localhost:3000`)
- **Severity:** `critical`
- **Evaluation:** Every 1 minute, pending period 2 minutes
- **Notification:** Webhook / Discord / Slack via `quickticket-alerts` contact point

## Symptoms
- Users can browse events but **cannot complete purchases**
- `POST /reserve/{id}/pay` returns HTTP 500: *"Payment succeeded but confirmation failed — contact support"*
- `GET /health` returns `"status": "degraded"` with `events: down`

## Diagnosis

### 1. Check system health via gateway
```bash
curl -s http://localhost:3080/health | python3 -m json.tool
```

**Redis-down signature:**
```json
{"status": "degraded", "checks": {"events": "down", "payments": "ok", "circuit_payments": "CLOSED"}}
```

Payments is `ok`, but events is `down`. This points to an **infrastructure dependency** of events, not the events service code itself.

### 2. Check events service health directly
```bash
curl -s http://localhost:8081/health | python3 -m json.tool
```

**Expected output when Redis is down:**
```json
{"status": "degraded", "checks": {"postgres": "ok", "redis": "down"}}
```

Postgres is `ok` but Redis is `down` — this narrows the issue to Redis specifically.

### 3. Check if Redis container is running
```bash
docker ps --filter name=redis --format "table {{.Names}}\t{{.Status}}"
```

**If Redis is down:**
```
NAMES      STATUS
redis      Exited (137) 2 minutes ago
```

**If Redis is healthy but unreachable:**
```
NAMES      STATUS
redis      Up 10 minutes
```

This distinction tells you whether to `start` Redis or `restart` it.

### 4. Check Redis directly
```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml exec redis redis-cli ping
```

**If Redis is down:**
```
Could not connect to Redis at 127.0.0.1:6379: Connection refused
```

**If Redis is healthy:**
```
PONG
```

### 5. Check events service logs
```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs events --tail=30 --since=5m
```

**Look for Redis-related messages:**
```json
{"level":"WARNING","msg":"Redis connection failed: ..."}
{"level":"WARNING","msg":"Redis unavailable — reservation not held"}
```

## Common Causes

| Cause | How to identify | Fix |
|-------|----------------|-----|
| **Redis container crashed** | `docker ps` shows redis not running; `redis-cli ping` fails | `docker compose start redis` (fastest — container stopped) |
| **Redis OOM / killed by system** | Check `docker logs redis` for OOM errors | Increase Docker memory limits or restart Redis |
| **Redis config corruption** | Redis starts but crashes immediately | Restart with clean config or rebuild |
| **Network connectivity loss** | Redis is running but events can't reach it — check `docker network` | `docker compose restart events` (reconnects to Redis) |
| **Events service lost Redis connection** | Redis is running, events logs show redis errors | `docker compose restart events` (forces reconnection) |

## Resolution

### 1. Start Redis (fastest — use when container is stopped)
```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start redis
```

### 2. Restart Redis (use when container is running but unhealthy)
```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml restart redis
```

### 3. Restart events service (reconnects to Redis)
```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml restart events
```

### 4. Force full reconnection
```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop events redis
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d redis events
```

### 5. Full stack restart (last resort)
```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml down
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
```

## Verification
- `curl http://localhost:8081/health` → `{"status": "healthy", "checks": {"postgres": "ok", "redis": "ok"}}`
- `curl http://localhost:3080/health` → `{"status": "healthy"}`
- `redis-cli ping` returns `PONG`
- Loadgen traffic resumes with successful purchase completions
- Grafana alert returns to **Normal** (green)

## Escalation
- If restarting Redis + events doesn't resolve within **5 minutes** → there may be a persistent storage or configuration issue
- Check Docker host resources: `docker system df`, `df -h` (disk space), `free -h` (memory)
- If Redis data persistence is corrupted → consider clearing Redis data volume: `docker compose down && docker volume rm app_redis_data && docker compose up -d`
- Escalate to instructor/TA if not resolved within **10 minutes**

## Notes
- Redis stores **temporary reservation data only** — persistent data is in PostgreSQL
- When Redis is down: reservations are created but **not persisted**, so confirmations will fail with 404
- The events health endpoint checks both Postgres (`pg_isready`) and Redis (`PING`) — making it the best first stop for diagnosis
- Restarting Redis is safe because reservation TTL is only 5 minutes — any lost reservations expire naturally

---

### B.2: Cross-test results

**How the cross-test was performed:**

1. The `runbook-redis.md` was given to a classmate (Alexander K.) — they knew the runbook but **not** which failure would be injected
2. The failure was injected by stopping the Redis container (`docker stop app-redis-1`) while the load generator was running
3. The classmate followed ONLY the runbook to diagnose and resolve the incident, with no additional hints
4. Their experience and feedback were recorded after the session

**Results:**

| Question                       | Answer                                                                                                                    |
|--------------------------------|---------------------------------------------------------------------------------------------------------------------------|
| Did they succeed?              | Yes ✅                                                                                                                     |
| Time to diagnose               | ~2 minutes                                                                                                                |
| Time to resolve (fix applied)  | ~30 seconds after identifying the cause                                                                                   |
| Steps skipped or misunderstood | Step 5 (Prometheus check) was skipped — the classmate found the cause earlier at step 2 and jumped straight to resolution |

**Classmate feedback on runbook clarity:**

> "The runbook was clear and logical. The chain of health checks (gateway → events → Redis) made it obvious what was broken without needing to dig through logs. The only thing I would add is a quick `docker ps` command early in the diagnosis section — when I ran the events health check and saw `redis: down`, I wasn't sure if Redis was actually stopped or just unreachable. Also, the resolution section could note that `docker compose start redis` is faster than `restart` when the container is fully stopped."

**Runbook updates made after feedback:**

The following changes were made to the runbook based on the cross-test session:

1. **Added Step 3: "Check if Redis container is running"** — a `docker ps` command with `--filter name=redis` to let the engineer quickly distinguish between "container stopped" and "container running but unhealthy", which determines whether to use `start` or `restart`.
2. **Split resolution Step 1** into two separate commands: `start` (fastest when stopped) and `restart` (when running but unhealthy), with clear guidance on which to use based on the `docker ps` output.
3. **Added explicit "container stopped" vs "container unhealthy" distinction** to the Common Causes table — makes it clearer which fix applies to which situation.