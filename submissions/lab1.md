# Lab 1 Submission — SRE Philosophy: Deploy, Break, Understand

## Task 1 — Deploy & Break QuickTicket

### 1.1 — Deployment

Ran `docker compose up --build -d`. All 5 containers started and reached healthy/running state.

```
NAME             IMAGE                COMMAND                  SERVICE    CREATED         STATUS                   PORTS
app-events-1     app-events           "uvicorn main:app --…"   events     7 seconds ago   Up Less than a second    0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    7 seconds ago   Up Less than a second    0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-payments-1   app-payments         "uvicorn main:app --…"   payments   7 seconds ago   Up 6 seconds             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   7 seconds ago   Up 6 seconds (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      7 seconds ago   Up 6 seconds (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

### 1.2 — Critical Path Verification

Listed events, reserved a ticket for event 1, paid for the reservation, checked health.

```
$ curl -s http://localhost:3080/events | python3 -m json.tool
[
    {"id": 1, "name": "Go Conference 2026", "venue": "Main Hall A", "date": "2026-09-15T09:00:00+00:00", "total_tickets": 100, "price_cents": 5000, "available": 100},
    {"id": 4, "name": "Python Workshop", "venue": "Lab 301", "date": "2026-09-22T14:00:00+00:00", "total_tickets": 25, "price_cents": 2000, "available": 25},
    {"id": 2, "name": "SRE Meetup", "venue": "Room 204", "date": "2026-10-01T18:00:00+00:00", "total_tickets": 30, "price_cents": 0, "available": 30},
    {"id": 5, "name": "Kubernetes Deep Dive", "venue": "Auditorium B", "date": "2026-10-10T10:00:00+00:00", "total_tickets": 80, "price_cents": 8000, "available": 80},
    {"id": 3, "name": "Cloud Native Summit", "venue": "Expo Center", "date": "2026-11-20T10:00:00+00:00", "total_tickets": 500, "price_cents": 15000, "available": 500}
]

$ curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity": 1}' | python3 -m json.tool
{
    "reservation_id": "fa20aeba-9192-4f3f-94d6-0821243d5d7a",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}

$ curl -s -X POST http://localhost:3080/reserve/fa20aeba-9192-4f3f-94d6-0821243d5d7a/pay | python3 -m json.tool
{
    "order_id": "fa20aeba-9192-4f3f-94d6-0821243d5d7a",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "status": "confirmed"
}

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

Critical path works end to end. Health check reflects all dependencies as OK.

### 1.3 — Dependency Map

```
gateway → events → postgres
gateway → events → redis
gateway → payments
```

- `gateway` is the only entry point; routes to `events` and `payments`, never talks to postgres/redis directly.
- `events` owns state: reads/writes tickets and reservations in postgres, uses redis for reservation locking/expiry.
- `payments` is stateless and only reachable via gateway.

### 1.4 — Failure Table

| Component Killed | Events List | Reserve | Pay | Health Check | User Impact |
|-------------------|-------------|---------|-----|---------------|-------------|
| **payments** | Works (200, reads from events/postgres) | Works (200, doesn't touch payments) | Fails — `{"detail":"Payment service unavailable"}` (502) | `degraded`, `payments: down` | Users can browse and reserve, but cannot complete payment. Reservation is preserved for retry. |
| **events** | Fails — `{"detail":"Events service unavailable"}` | Fails — `{"detail":"Events service unavailable"}` | Not tested (blocked by events being the source of reservation data) | `degraded`, `events: down` | Full outage of the core flow — browsing and reserving both broken. |
| **redis** | Works (200, list doesn't need redis) | Fails — `{"detail":"Events service timeout"}` | N/A | `degraded`, `events: down` (events reports itself unhealthy without redis) | Users can browse but cannot reserve — reservation locking depends on redis. |
| **postgres** | Fails — `{"detail":"Events service unavailable"}` | Fails — `Internal Server Error` (500) | N/A | `degraded`, `events: down` | Full outage of browsing and reserving — postgres is the source of truth for event data. |

Notes:
- Reserve surviving a `payments` outage confirms `events` and `payments` are decoupled at the gateway level — reserve only calls `events`.
- `events` treats redis as a hard dependency for the reserve path (used for locking), even though the list endpoint reads straight from postgres.
- Health check correctly reflects every dependency outage as `degraded`, never silently reports `healthy`.

### 1.5 — Load Generator

Baseline run (no chaos):
```
$ ./loadgen/run.sh 5 30
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
Done. total=43 success=36 fail=7 error_rate=16.2%
```

Payments was stopped mid-run (~5s in) and restarted ~15s later. Error rate rose from a clean run to 16.2% overall, driven entirely by `/pay` calls failing while payments was down — confirming the failure directly correlates with the outage window.

## Task 2 — Graceful Degradation

### Code Diff (`app/gateway/main.py`)

```diff
diff --git a/app/gateway/main.py b/app/gateway/main.py
index c86db33..d9de720 100644
--- a/app/gateway/main.py
+++ b/app/gateway/main.py
@@ -332,6 +332,16 @@ async def pay_reservation(reservation_id: str):
     except CircuitOpenError:
         log.error("circuit open, skipping payments call")
         raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
+    except httpx.ConnectError:
+        log.error(f"payments unreachable for reservation={reservation_id}")
+        return JSONResponse(
+            status_code=503,
+            content={
+                "error": "payments_unavailable",
+                "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.",
+                "reservation_id": reservation_id,
+            },
+        )
     except httpx.TimeoutException:
         raise HTTPException(504, "Payment service timeout")
     except httpx.HTTPStatusError as e:
```

`/events` and `/events/{id}/reserve` already only call `events`, so no changes were needed there — they were unaffected by payments outages before this change too.

### Verification

```
$ curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity": 1}'
{"reservation_id":"99f7322c-d6d8-4fc6-9201-58da81c10cb6","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}

$ curl -s -X POST http://localhost:3080/reserve/99f7322c-d6d8-4fc6-9201-58da81c10cb6/pay
{"error":"payments_unavailable","message":"Payment service is temporarily down. Your reservation is held — try again in a few minutes.","reservation_id":"99f7322c-d6d8-4fc6-9201-58da81c10cb6"}
```

Reserve still works with payments down. Pay returns a clean 503 with an actionable message and the reservation ID, instead of a generic 502.

## Task 3 — GitHub Community Engagement

Completed: starred the course repo and `simple-container-com/api`; followed the professor, both TAs, and 3+ classmates.

**Why it matters:** Starring bookmarks useful projects, signals trust/popularity to the community, and helps maintainers gauge interest. Following developers surfaces what teammates and peers are working on, which is useful for coordinating on team projects and for building a professional network beyond the classroom.

## Bonus Task — Resource Usage Under Load

### B.1 — Idle

```
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-gateway-1    0.10%     38.02MiB / 7.725GiB   3.08kB / 2.28kB   2
app-events-1     0.10%     42.57MiB / 7.725GiB   516kB / 700kB     2
app-redis-1      1.93%     8.613MiB / 7.725GiB   88.5kB / 37.5kB   6
app-payments-1   0.12%     32.43MiB / 7.725GiB   726B / 126B       1
app-postgres-1   0.00%     23.8MiB / 7.725GiB    286kB / 323kB     8
```

### B.2 — Under Load (10 rps, 30s)

Loadgen result:
```
[10s] requests=88 success=75 fail=13 error_rate=14.7%
```

Stats during the run:
```
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-gateway-1    2.66%     38.35MiB / 7.725GiB   110kB / 105kB     2
app-events-1     1.44%     42.57MiB / 7.725GiB   610kB / 827kB     2
app-redis-1      1.97%     8.617MiB / 7.725GiB   99.5kB / 41.8kB   6
app-payments-1   0.11%     33.43MiB / 7.725GiB   1.64kB / 812B     2
app-postgres-1   0.42%     23.8MiB / 7.725GiB    334kB / 380kB     8
```

### B.3 — Under Stress with Fault Injection (payments: 30% failure rate, 500ms latency)

Loadgen result:
```
[20s] requests=120 success=109 fail=11 error_rate=9.1%
```

Stats during the run:
```
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-payments-1   0.34%     34.83MiB / 7.725GiB   1.97kB / 1.17kB   2
app-gateway-1    1.70%     38.5MiB / 7.725GiB    439kB / 423kB     2
app-events-1     0.95%     42.71MiB / 7.725GiB   888kB / 1.2MB     2
app-redis-1      0.32%     8.633MiB / 7.725GiB   128kB / 54.3kB    6
app-postgres-1   0.48%     23.8MiB / 7.725GiB    494kB / 570kB     8
```

Payments restored to normal afterward (`PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0`).

### Analysis

- **Memory:** `events` uses the most memory across all three scenarios (~42-43MiB), consistently ahead of the others. Memory usage barely moves between idle, load, and chaos — the workload is CPU/IO-bound, not memory-bound at this scale.
- **CPU under load:** `gateway` uses the most CPU under plain load (2.66%) — expected, since it is the single entry point handling routing, middleware, and metrics for every request. `events` is second (1.44%), driven by DB/redis calls.
- **Fault injection impact on gateway:** With payments injecting 500ms latency and 30% failures, gateway's CPU dropped from 2.66% to 1.70% despite similar request volume. This matches the hint — slow payment calls mean the gateway spends more time waiting (held-open connections) rather than doing active work, so CPU usage per request goes down while requests are effectively queued/blocked on the slow dependency.
