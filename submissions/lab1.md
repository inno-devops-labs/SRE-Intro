# Lab 1 — SRE Philosophy: Deploy, Break, Understand

## Task 1 — Deploy & Break QuickTicket

### 1.1 — `docker compose ps` output (all 5 services running)

```
NAME             IMAGE                COMMAND                  SERVICE    CREATED          STATUS                    PORTS
app-events-1     app-events           "uvicorn main:app --…"   events     5 seconds ago    Up 4 seconds              0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    5 seconds ago    Up 4 seconds              0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-payments-1   app-payments         "uvicorn main:app --…"   payments   5 seconds ago    Up 4 seconds              0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   42 seconds ago   Up 42 seconds (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      42 seconds ago   Up 42 seconds (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

### 1.2 — Full critical path (list → reserve → pay)

**List events:**
```json
[
    {
        "id": 1,
        "name": "Go Conference 2026",
        "venue": "Main Hall A",
        "date": "2026-09-15T09:00:00+00:00",
        "total_tickets": 100,
        "price_cents": 5000,
        "available": 100
    },
    {
        "id": 4,
        "name": "Python Workshop",
        "venue": "Lab 301",
        "date": "2026-09-22T14:00:00+00:00",
        "total_tickets": 25,
        "price_cents": 2000,
        "available": 25
    },
    {
        "id": 2,
        "name": "SRE Meetup",
        "venue": "Room 204",
        "date": "2026-10-01T18:00:00+00:00",
        "total_tickets": 30,
        "price_cents": 0,
        "available": 30
    },
    {
        "id": 5,
        "name": "Kubernetes Deep Dive",
        "venue": "Auditorium B",
        "date": "2026-10-10T10:00:00+00:00",
        "total_tickets": 80,
        "price_cents": 8000,
        "available": 80
    },
    {
        "id": 3,
        "name": "Cloud Native Summit",
        "venue": "Expo Center",
        "date": "2026-11-20T10:00:00+00:00",
        "total_tickets": 500,
        "price_cents": 15000,
        "available": 500
    }
]
```

**Reserve 1 ticket for event 1:**
```json
{
    "reservation_id": "5ac728c2-741e-44a7-8f5c-94cbf0c9878a",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}
```

**Pay for reservation `5ac728c2-741e-44a7-8f5c-94cbf0c9878a`:**
```json
{
    "order_id": "5ac728c2-741e-44a7-8f5c-94cbf0c9878a",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "status": "confirmed"
}
```

### 1.3 — Health check (all services healthy)

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

### 1.4 — Dependency map

```
gateway → events → postgres
gateway → events → redis
gateway → payments
```

- `gateway` is the single entry point. It routes all external HTTP traffic.
- `events` handles ticket inventory using postgres (persistent storage) and redis (reservation locks/TTL).
- `payments` is a separate service called only during the `/pay` step.
- If `events` is down, **nothing works** — gateway cannot list events or create reservations.
- If `payments` is down, listing and reserving still work; only payment fails.
- If `redis` is down, reservation creation fails because the TTL-based hold cannot be written.
- If `postgres` is down, `events` crashes on any database query, taking down listing and reserving.

### 1.5 — Failure table

| Component Killed | Events List | Reserve | Pay | Health Check | User Impact |
|-----------------|-------------|---------|-----|--------------|-------------|
| payments | ✅ works | ✅ works | ❌ 503 payments_unavailable | ⚠️ degraded — payments: down | Users can browse and reserve but cannot complete payment |
| events | ❌ 502 Events service unavailable | ❌ 502 Events service unavailable | ❌ 502 | ⚠️ degraded — events: down | Complete outage for all ticket operations |
| redis | ✅ works (reads postgres) | ❌ 504 Events service timeout | ❌ not reachable | ⚠️ degraded — events: down | Listing works (data in postgres); reservations fail because redis hold cannot be created |
| postgres | ❌ 502 Events service unavailable | ❌ Internal Server Error | ❌ not reachable | ⚠️ degraded — events: degraded | Complete outage; events service crashes on any DB query |

**Key observations:**
- Killing `redis` produces a 504 timeout on reserve (not an immediate 502), because the events service waits for redis before giving up — this reveals the 5-second gateway timeout in action.
- Killing `postgres` makes the health check report `events: degraded` rather than `events: down`, because the events process is still running; only its database queries fail.
- `payments` being down does not affect the health check's ability to detect the problem — it correctly flips to `degraded` immediately.
- The circuit breaker reports `CLOSED` throughout because the Lab 11 implementation is not yet active (no-op by design).

### 1.6 — Load generator output with error rate spike

**Normal run (payments up), 5 RPS for 30 seconds:**
```
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=40 success=40 fail=0 error_rate=0%
[10s] requests=41 success=41 fail=0 error_rate=0%
[10s] requests=42 success=42 fail=0 error_rate=0%
[10s] requests=43 success=43 fail=0 error_rate=0%
[20s] requests=81 success=81 fail=0 error_rate=0%
[20s] requests=82 success=82 fail=0 error_rate=0%
[20s] requests=83 success=83 fail=0 error_rate=0%
[20s] requests=84 success=84 fail=0 error_rate=0%
---
Done. total=122 success=115 fail=7 error_rate=5.7%
```

The 5.7% error rate at the end reflects payments being stopped mid-run. All 115 successes occurred while payments was up. The 7 failures are requests that hit the pay endpoint after `docker compose stop payments` was executed in a second terminal. This directly demonstrates the blast radius of a single-service failure: the load generator catches the exact moment the error rate changes from 0% to non-zero.

---

## Task 2 — Graceful Degradation

### Code change — `git diff app/gateway/main.py`

```diff
@@ -312,12 +312,6 @@ async def _notify_order_confirmed(reservation_id: str):
 
 @app.post("/reserve/{reservation_id}/pay")
 async def pay_reservation(reservation_id: str):
-    # 1. Call payments — wrapped in circuit breaker + retry.
-    #
-    # Composition order matters: cb.call(retry(_charge)) means each CB-tracked
-    # invocation includes its retries internally; the CB only sees the FINAL
-    # outcome. The reverse — retry(cb.call(_charge)) — would retry past the
-    # CircuitOpenError, defeating the fast-fail. See lab 11 §11.4.
     async def _charge():
         resp = await client.post(
             f"{PAYMENTS_URL}/charge",
@@ -329,9 +323,16 @@ async def pay_reservation(reservation_id: str):
     try:
         pay_resp = await payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
         payment_ref = pay_resp.json().get("payment_ref", "unknown")
-    except CircuitOpenError:
-        log.error("circuit open, skipping payments call")
-        raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
+    except (httpx.ConnectError, CircuitOpenError) as e:
+        log.error(f"payment service down: {e}")
+        return JSONResponse(
+            status_code=503,
+            content={
+                "error": "payments_unavailable",
+                "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.",
+                "reservation_id": reservation_id,
+            }
+        )
     except httpx.TimeoutException:
         raise HTTPException(504, "Payment service timeout")
```

### Verification — payments down, reserve still works

```bash
$ docker compose stop payments
$ curl -s -X POST http://localhost:3080/events/1/reserve \
    -H "Content-Type: application/json" -d '{"quantity": 1}'
```
```json
{
    "reservation_id": "99d4f848-d39c-4af2-9081-eba5fa8fb57c",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}
```

Reserve returns 200 ✅ — the reservation is held in Redis even though payments is down.

### Verification — pay returns clear 503 with actionable message

```bash
$ curl -s -X POST http://localhost:3080/reserve/ce05bf55-eb56-4f3c-82c4-439f2dcdd07d/pay
```
```json
{
    "error": "payments_unavailable",
    "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.",
    "reservation_id": "ce05bf55-eb56-4f3c-82c4-439f2dcdd07d"
}
```

Returns HTTP 503 ✅ with a structured error body and an actionable message. The user knows their reservation is safe and can retry later — no data is lost.

---

## Task 3 — GitHub Community Engagement

**Actions completed:**
- ⭐ Starred the course repository `inno-devops-labs/SRE-Intro`
- ⭐ Starred `simple-container-com/api`
- ✅ Following professor [@Cre-eD](https://github.com/Cre-eD)
- ✅ Following TA [@Naghme98](https://github.com/Naghme98)
- ✅ Following TA [@pierrepicaud](https://github.com/pierrepicaud)
- ✅ Following 3+ classmates

**Why this matters:**

Starring repositories serves as a public bookmark and a signal of trust — a project's star count tells the community how widely it is used and valued, which helps maintainers know their work is useful and helps newcomers judge which tools are worth adopting. Following professors, TAs, and classmates surfaces their activity in your GitHub feed, making it easy to discover new projects they contribute to, see what problems the team is solving, and build the professional connections that often matter more than the coursework itself once you enter the industry.

---

## Bonus Task — Resource Usage Under Load

### Scenario 1 — Idle (no traffic)

```
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-gateway-1    4.37%     38.42MiB / 7.652GiB   80.4kB / 79.8kB   2
app-events-1     2.04%     41.6MiB / 7.652GiB    72.3kB / 94kB     2
app-postgres-1   0.66%     24.27MiB / 7.652GiB   135kB / 155kB     8
app-redis-1      0.78%     9.629MiB / 7.652GiB   41.3kB / 16.2kB   6
```

### Scenario 2 — Under load (10 RPS for 30 seconds)

```
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-payments-1   0.21%     34.91MiB / 7.652GiB   2.12kB / 1.13kB   2
app-gateway-1    3.91%     38.57MiB / 7.652GiB   384kB / 383kB     2
app-events-1     2.00%     41.81MiB / 7.652GiB   338kB / 449kB     2
app-postgres-1   0.61%     23.93MiB / 7.652GiB   281kB / 327kB     8
app-redis-1      0.67%     9.902MiB / 7.652GiB   78.5kB / 30.1kB   6
```

### Analysis

**Memory:** `events` uses the most memory (~41.8 MiB) in both scenarios and stays nearly flat under load. This is expected — Python loads all models and the ORM at startup; memory grows with connection pools, not per-request. `redis` is the lightest at ~9.9 MiB, consistent with its in-memory key-value model and small dataset. Memory does not scale noticeably with the load level tested here, because 10 RPS is well within the capacity of a single-worker uvicorn process.

**CPU:** `gateway` uses the most CPU under load (~4%) because it handles every inbound request, performs JSON parsing, makes outbound HTTP calls to two downstream services, and runs the Prometheus middleware on every request. `events` is second (~2%) as it executes SQL queries and Redis writes per reservation. `postgres` CPU stays low because the queries are simple indexed lookups, not analytical workloads.

**Network I/O:** The spike from idle to load is most visible on `gateway` (80 KB → 384 KB) and `events` (72 KB → 338 KB), confirming they are the hot path. `redis` network grows modestly (41 KB → 78 KB) — one write per reservation. `payments` remains near-zero because it is a mock with no persistent I/O.

**Fault injection impact (Scenario 3 — `PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500`):** When payments introduces 500ms artificial latency, gateway CPU and network I/O increase because it holds open HTTP connections for the full 500ms before the payments response returns. Each in-flight pay request occupies a connection slot for 10x longer than normal, increasing gateway's concurrent connection count and the time its event loop spends waiting — a classic case of slow downstream causing resource accumulation upstream, even when the downstream eventually succeeds.