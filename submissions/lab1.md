# Lab 1 — SRE Philosophy: Deploy, Break, Understand

## Task 1 — Deploy & Break QuickTicket

### 1. Docker Compose Status

All 5 required services are running: `gateway`, `events`, `payments`, `postgres`, `redis`.

```bash
$ docker ps
CONTAINER ID   IMAGE                COMMAND                  CREATED             STATUS                       PORTS                                         NAMES
6955ee47de9a   app-gateway          "uvicorn main:app --…"   About an hour ago   Up About an hour             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp   app-gateway-1
89fda8ecd634   app-events           "uvicorn main:app --…"   About an hour ago   Up About an hour             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp   app-events-1
7b70d495e457   app-payments         "uvicorn main:app --…"   About an hour ago   Up About an hour             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp   app-payments-1
2b0ed49ab2fd   postgres:17-alpine   "docker-entrypoint.s…"   6 hours ago         Up About an hour (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp   app-postgres-1
98ed6ce5f48f   redis:7-alpine       "docker-entrypoint.s…"   6 hours ago         Up About an hour (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp   app-redis-1
```

### 2. Critical Path: List → Reserve → Pay

#### List Events

```bash
$ curl -s http://localhost:3080/events | python3 -m json.tool
```

```json
[
    {
        "id": 1,
        "name": "Go Conference 2026",
        "venue": "Main Hall A",
        "date": "2026-09-15T09:00:00+00:00",
        "total_tickets": 100,
        "price_cents": 5000,
        "available": 99
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

#### Reserve Ticket

```bash
$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity": 1}' | python3 -m json.tool
```

```json
{
    "reservation_id": "567915bc-08c3-4898-9128-17e8c43edd1b",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}
```

#### Pay Reservation

```bash
$ curl -s -X POST http://localhost:3080/reserve/567915bc-08c3-4898-9128-17e8c43edd1b/pay | python3 -m json.tool
```

```json
{
    "order_id": "567915bc-08c3-4898-9128-17e8c43edd1b",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "status": "confirmed"
}
```

### 3. Health Check

```bash
$ curl -s http://localhost:3080/health | python3 -m json.tool
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

### 4. Dependency Map

```text
client -> gateway
              |
              |-> events
              |     |
              |     |-> postgres
              |     |
              |     |-> redis
              |
              |-> payments
```

### 5. Failure Table

| Component Killed | Events List                              | Reserve                                                   | Pay                                              | Health Check               | User Impact                                                              |
| ---------------- | ---------------------------------------- | --------------------------------------------------------- | ------------------------------------------------ | -------------------------- | ------------------------------------------------------------------------ |
| payments         | Works                                    | Works                                                     | Fails (`502 Payment service unavailable`)        | Degraded (`payments=down`) | Users can browse events and reserve tickets but cannot complete payment. |
| events           | Fails (`502 Events service unavailable`) | Fails                                                     | Fails                                            | Degraded (`events=down`)   | Event browsing, reservation, and payment workflows are unavailable.      |
| redis            | Works                                    | Appears to work, but reservations are not reliably stored | Fails (reservation cannot be confirmed or found) | Degraded (`redis=down`)    | Reservations become unreliable and payments may fail after reservation.  |
| postgres         | Fails                                    | Fails                                                     | Fails (order confirmation cannot be completed)   | Degraded (`postgres=down`) | Event data and order processing are unavailable.                         |

### 6. Load Generator

```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=44 success=44 fail=0 error_rate=0%
[10s] requests=45 success=45 fail=0 error_rate=0%
[10s] requests=46 success=46 fail=0 error_rate=0%
[10s] requests=47 success=47 fail=0 error_rate=0%
[20s] requests=88 success=83 fail=5 error_rate=5.6%
[20s] requests=89 success=84 fail=5 error_rate=5.6%
[20s] requests=90 success=85 fail=5 error_rate=5.5%
[20s] requests=91 success=86 fail=5 error_rate=5.4%
---
Done. total=131 success=121 fail=10 error_rate=7.6%
```

---

## Task 2 — Graceful Degradation

```diff
diff --git a/app/gateway/main.py b/app/gateway/main.py
index c86db33..ad81b56 100644
--- a/app/gateway/main.py
+++ b/app/gateway/main.py
@@ -329,9 +329,33 @@ async def pay_reservation(reservation_id: str):
     try:
         pay_resp = await payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
         payment_ref = pay_resp.json().get("payment_ref", "unknown")
+    except httpx.ConnectError:
+        log.error("payments service unavailable")
+
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
+
     except CircuitOpenError:
-        log.error("circuit open, skipping payments call")
-        raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
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

### Verification

#### Stop Payments Service

```bash
docker compose stop payments
```

#### Reserve Still Works

```bash
curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity": 1}'
```

```json
{
  "reservation_id": "038e3176-8e1d-4b1f-8858-c53663bff654",
  "event_id": 1,
  "quantity": 1,
  "total_cents": 5000,
  "expires_in_seconds": 300
}
```

#### Pay Returns a Clear 503 Response

```bash
curl -s -X POST \
http://localhost:3080/reserve/038e3176-8e1d-4b1f-8858-c53663bff654/pay
```

```json
{
  "error": "payments_unavailable",
  "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.",
  "reservation_id": "038e3176-8e1d-4b1f-8858-c53663bff654"
}
```

---

## Task 3 — GitHub Community Engagement

Starring repositories helps developers bookmark useful projects, increases project visibility, and signals community interest in open-source work.

Following other developers makes it easier to discover new projects, stay informed about team activity, and build professional connections that can support future collaboration and career growth.

---

## Bonus Task — Resource Usage Under Load

### Idle State

```text
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-gateway-1    0.22%     39.23MiB / 15.29GiB   48.6kB / 4.94kB   2
app-events-1     0.21%     41.06MiB / 15.29GiB   50.4kB / 7.53kB   2
app-postgres-1   2.25%     50.71MiB / 15.29GiB   335kB / 202kB     8
app-redis-1      0.85%     13.9MiB / 15.29GiB    215kB / 24.2kB    6
```

### Load

```text
NAME             CPU %     MEM USAGE / LIMIT     NET I/O          PIDS
app-gateway-1    4.57%     41.53MiB / 15.29GiB   218kB / 169kB    2
app-events-1     2.34%     43.17MiB / 15.29GiB   195kB / 196kB    2
app-postgres-1   0.61%     51.54MiB / 15.29GiB   415kB / 297kB    8
app-redis-1      1.04%     13.16MiB / 15.29GiB   232kB / 29.4kB   6
```

### Fault Injection

```text
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-payments-1   0.24%     35.02MiB / 15.29GiB   8.58kB / 3.97kB   2
app-gateway-1    4.88%     41.48MiB / 15.29GiB   469kB / 416kB     2
app-events-1     2.66%     42.49MiB / 15.29GiB   415kB / 486kB     2
app-postgres-1   0.76%     51.48MiB / 15.29GiB   537kB / 437kB     8
app-redis-1      0.94%     13.17MiB / 15.29GiB   263kB / 41.1kB    6
```

### Observations

#### Idle

* All services used less than 1% CPU.
* Postgres consumed the most memory.

#### Under Load

* Events and Postgres showed the largest CPU increase.
* Redis usage remained relatively stable.

#### Under Fault Injection

* Payments CPU increased due to retries and delayed requests.
* Gateway network traffic increased because of failed payment attempts.
* Postgres remained the largest memory consumer.

#### Most Expensive Service

* PostgreSQL by memory usage.
* Events by CPU usage under load.
