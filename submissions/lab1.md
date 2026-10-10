# Lab 1 Submission — SRE Philosophy: Deploy, Break, Understand

## Task 1 — Deploy & Break QuickTicket

### 1.1 — Deployment

Ran `docker compose up --build -d`. All five containers started and reached healthy/running state.

```text
NAME             IMAGE                COMMAND                  SERVICE    CREATED              STATUS                        PORTS
app-events-1     app-events           "uvicorn main:app --…"   events     About a minute ago   Up About a minute             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    About a minute ago   Up About a minute             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-payments-1   app-payments         "uvicorn main:app --…"   payments   About a minute ago   Up About a minute             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   About a minute ago   Up About a minute (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      About a minute ago   Up About a minute (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

### 1.2 — Critical Path Verification

Listed events, reserved one ticket for event 1, paid for the reservation, and checked system health.

```text
$ curl -s http://localhost:3080/events | python3 -m json.tool
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

$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity": 1}' | python3 -m json.tool
{
    "reservation_id": "b27da169-c7b3-4156-a551-8bd60b5d935a",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}

$ curl -s -X POST http://localhost:3080/reserve/b27da169-c7b3-4156-a551-8bd60b5d935a/pay \
  | python3 -m json.tool
{
    "order_id": "b27da169-c7b3-4156-a551-8bd60b5d935a",
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

The critical path worked end to end. A real reservation ID was returned by the reservation endpoint, payment completed successfully, and the order received the `confirmed` status. The health endpoint reported all critical dependencies as available.

### 1.3 — Dependency Map

```text
gateway → events → postgres
gateway → events → redis
gateway → payments
```

- `gateway` is the only user-facing entry point and routes requests to `events` and `payments`.
- `events` reads event data and stores confirmed orders in PostgreSQL.
- `events` uses Redis to hold reservations and manage their expiration.
- `payments` is stateless and handles mock payment processing.
- During `/pay`, gateway first calls `payments` and then calls `events` to confirm the reservation.

### 1.4 — Failure Table

| Component Killed | Events List | Reserve | Pay | Health Check | User Impact |
|-------------------|-------------|---------|-----|--------------|-------------|
| **payments** | Works — HTTP 200 | Works — HTTP 200 | Fails — `{"detail":"Payment service unavailable"}` (502) | `degraded`, `payments: down` (503) | Users can browse and reserve tickets, but cannot complete payment. The reservation remains available for retry. |
| **events** | Fails — `{"detail":"Events service unavailable"}` (502) | Fails — `{"detail":"Events service unavailable"}` (502) | Fails — payment succeeds, but confirmation fails (500) | `degraded`, `events: down` (503) | The core ticket workflow is unavailable. A payment may be processed without successful order confirmation. |
| **redis** | Works — HTTP 200 | Fails — `{"detail":"Events service timeout"}` (504) | Fails — payment succeeds, but confirmation fails (500) | `degraded`, gateway reports `events: down` (503) | Users can browse events, but reservations cannot be reliably held or confirmed. |
| **postgres** | Fails — `{"detail":"Events service unavailable"}` (502) | Fails — `Internal Server Error` (500) | Fails — payment succeeds, but confirmation fails (500) | `degraded`, gateway reports `events: down` (503) | Event data, reservation creation, and order confirmation become unavailable. |

Notes:

- A `payments` outage has a limited blast radius because event listing and reservation only depend on `events`.
- Event listing survives a Redis outage because it reads event data directly from PostgreSQL.
- Reservation requires Redis, so stopping Redis caused the gateway to return a 504 timeout.
- Stopping `events`, Redis, or PostgreSQL caused a partial failure during `/pay`: the mock payment succeeded, but order confirmation failed afterward.
- The health endpoint correctly reported every tested dependency outage as `degraded`.

### 1.5 — Load Generator

Baseline run with all services available:

```text
$ ./loadgen/run.sh 5 30
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=41 success=41 fail=0 error_rate=0%
[10s] requests=42 success=42 fail=0 error_rate=0%
[10s] requests=43 success=43 fail=0 error_rate=0%
[10s] requests=44 success=44 fail=0 error_rate=0%
[20s] requests=81 success=81 fail=0 error_rate=0%
[20s] requests=82 success=82 fail=0 error_rate=0%
[20s] requests=83 success=83 fail=0 error_rate=0%
[20s] requests=84 success=84 fail=0 error_rate=0%
---
Done. total=121 success=121 fail=0 error_rate=0%
```

Payments was then stopped while the load generator was running and restarted before the end of the test:

```text
$ ./loadgen/run.sh 5 30
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=38 success=38 fail=0 error_rate=0%
[10s] requests=39 success=39 fail=0 error_rate=0%
[10s] requests=40 success=39 fail=1 error_rate=2.5%
[10s] requests=41 success=40 fail=1 error_rate=2.4%
[20s] requests=78 success=74 fail=4 error_rate=5.1%
[20s] requests=79 success=75 fail=4 error_rate=5.0%
[20s] requests=80 success=76 fail=4 error_rate=5.0%
[20s] requests=81 success=77 fail=4 error_rate=4.9%
---
Done. total=118 success=113 fail=5 error_rate=4.2%
```

The healthy run completed 121 requests with zero failures. When payments was stopped during the second run, the final error rate increased to 4.2%, while the intermediate cumulative error rate reached 5.1%. The failures appeared during the payments outage, while event listing and reservation requests continued working.

## Task 2 — Graceful Degradation

### Code Diff (`app/gateway/main.py`)

```diff
diff --git a/app/gateway/main.py b/app/gateway/main.py
index c86db33..348ae69 100644
--- a/app/gateway/main.py
+++ b/app/gateway/main.py
@@ -332,6 +332,19 @@ async def pay_reservation(reservation_id: str):
     except CircuitOpenError:
         log.error("circuit open, skipping payments call")
         raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
+    except httpx.ConnectError:
+        log.error("payments service connection failed")
+        return JSONResponse(
+            status_code=503,
+            content={
+                "error": "payments_unavailable",
+                "message": (
+                    "Payment service is temporarily down. "
+                    "Your reservation is held — try again in a few minutes."
+                ),
+                "reservation_id": reservation_id,
+            },
+        )
     except httpx.TimeoutException:
         raise HTTPException(504, "Payment service timeout")
     except httpx.HTTPStatusError as e:
```

The `/events` and `/events/{id}/reserve` endpoints already call only `events`, so no changes were required there. The payment handler was updated to catch `httpx.ConnectError` and return a clear HTTP 503 response instead of the original generic 502.

### Verification

Payments was stopped, and a new reservation was created:

```text
$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity": 1}'
{
    "reservation_id": "131b89ee-fdfa-4c7f-8bc0-38334b634a09",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}
HTTP_STATUS=200
```

Payment was then attempted using the returned reservation ID:

```text
$ curl -s -X POST \
  http://localhost:3080/reserve/131b89ee-fdfa-4c7f-8bc0-38334b634a09/pay
{
    "error": "payments_unavailable",
    "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.",
    "reservation_id": "131b89ee-fdfa-4c7f-8bc0-38334b634a09"
}
HTTP_STATUS=503
```

Reserve still works while payments is down. Pay returns a clean HTTP 503 response with an actionable message and the reservation ID instead of the original generic 502.

## Task 3 — GitHub Community Engagement

Completed: starred the course repository and `simple-container-com/api`; followed the professor, both TAs, and at least three classmates.

**Why it matters:** Starring bookmarks useful projects, helps projects gain visibility, and signals community interest. Following developers makes it easier to discover what teammates and peers are working on and supports future collaboration and professional growth.

## Bonus Task — Resource Usage Under Load

### B.1 — Idle

```text
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-gateway-1    0.20%     38.11MiB / 5.786GiB   9.46kB / 8.49kB   2
app-events-1     0.20%     41.23MiB / 5.786GiB   9.71kB / 8.17kB   2
app-payments-1   0.31%     33.91MiB / 5.786GiB   2.42kB / 1.39kB   2
app-postgres-1   1.72%     23.45MiB / 5.786GiB   179kB / 208kB     8
app-redis-1      2.15%     9.66MiB / 5.786GiB    52.2kB / 20.3kB   6
```

### B.2 — Under Load (10 RPS, 30 seconds)

Load generator result:

```text
[20s] requests=136 success=130 fail=6 error_rate=4.4%
[20s] requests=137 success=131 fail=6 error_rate=4.3%
[20s] requests=138 success=132 fail=6 error_rate=4.3%
---
Done. total=197 success=187 fail=10 error_rate=5.0%
```

Stats captured during the run:

```text
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-gateway-1    4.72%     38.64MiB / 5.786GiB   110kB / 107kB     2
app-events-1     2.23%     41.53MiB / 5.786GiB   87.7kB / 116kB    2
app-payments-1   0.58%     34MiB / 5.786GiB      5.82kB / 3.83kB   2
app-postgres-1   0.72%     23.73MiB / 5.786GiB   228kB / 265kB     8
app-redis-1      0.79%     9.898MiB / 5.786GiB   65.9kB / 26.2kB   6
```

### B.3 — Under Stress with Fault Injection

Payments was restarted with a 30% failure rate and 500 ms latency:

```bash
docker compose stop payments

PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500 \
  docker compose up -d --force-recreate payments
```

The applied configuration was verified through the payments health endpoint:

```json
{
    "status": "healthy",
    "failure_rate": 0.3,
    "latency_ms": 500
}
```

Load generator result:

```text
[20s] requests=124 success=108 fail=16 error_rate=12.9%
[20s] requests=125 success=109 fail=16 error_rate=12.8%
[20s] requests=126 success=110 fail=16 error_rate=12.6%
---
Done. total=180 success=155 fail=25 error_rate=13.8%
```

Stats captured during the run:

```text
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-payments-1   0.87%     34.88MiB / 5.786GiB   2.31kB / 1.39kB   2
app-gateway-1    4.53%     38.53MiB / 5.786GiB   391kB / 385kB     2
app-events-1     2.15%     41.68MiB / 5.786GiB   342kB / 458kB     2
app-postgres-1   0.47%     23.77MiB / 5.786GiB   362kB / 425kB     8
app-redis-1      0.87%     9.648MiB / 5.786GiB   93.5kB / 37.6kB   6
```

Payments was restored to normal afterward using `PAYMENT_FAILURE_RATE=0.0` and `PAYMENT_LATENCY_MS=0`. Both the payments health endpoint and the gateway health endpoint returned healthy responses.

### Analysis

- **Memory:** `events` used the most memory in all three scenarios: 41.23 MiB while idle, 41.53 MiB under normal load, and 41.68 MiB during fault injection. Memory usage barely changed, so the workload did not create significant memory pressure.
- **CPU under load:** `gateway` used the most CPU under normal load at 4.72%, followed by `events` at 2.23%. This is expected because every external request passes through gateway, where routing, middleware, and metrics processing occur.
- **Payments under fault injection:** Payments CPU increased from 0.58% to 0.87%, and its memory usage increased from 34.00 MiB to 34.88 MiB.
- **Gateway under fault injection:** Gateway CPU remained approximately stable, changing from 4.72% to 4.53%, while memory remained around 38.5 MiB. Slow payment calls kept requests open longer, but this sequential load generator did not create enough concurrency to cause a visible gateway memory increase.
- **Throughput and failures:** Throughput decreased from 197 to 180 requests per 30 seconds, which is approximately an 8.6% reduction. The final error rate increased from 5.0% under normal load to 13.8% during fault injection.
- **Normal-load errors:** The non-zero error rate in the bonus normal-load run was likely influenced by reservations left from earlier experiments. Reservations remain held in Redis for 300 seconds, and repeated tests can temporarily exhaust tickets for events with smaller capacity. The separate clean Task 1 baseline completed with a 0% error rate.
