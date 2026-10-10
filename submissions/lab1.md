# Lab 1 --- SRE Philosophy: Deploy, Break, Understand

## Task 1 --- Deploy & Break QuickTicket

### 1.1 Deploy QuickTicket

All five required containers were successfully deployed with Docker
Compose:

``` text
NAME             IMAGE                COMMAND                  SERVICE    STATUS
app-events-1     app-events           "uvicorn main:app --…"  events     Up
app-gateway-1    app-gateway           "uvicorn main:app --…"  gateway    Up
app-payments-1   app-payments          "uvicorn main:app --…"  payments   Up
app-postgres-1   postgres:17-alpine    "docker-entrypoint.s…"  postgres   Up (healthy)
app-redis-1      redis:7-alpine        "docker-entrypoint.s…"  redis      Up (healthy)
```

### 1.2 Critical Path

The system was tested through the complete user flow: list events →
reserve a ticket → pay → check health.

#### Events list

``` json
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

#### Reserve

``` json
{
  "reservation_id": "6232adc0-0311-4c21-b3e2-387e79d9bac7",
  "event_id": 1,
  "quantity": 1,
  "total_cents": 5000,
  "expires_in_seconds": 300
}
```

#### Pay

``` json
{
  "order_id": "6232adc0-0311-4c21-b3e2-387e79d9bac7",
  "event_id": 1,
  "quantity": 1,
  "total_cents": 5000,
  "status": "confirmed"
}
```

#### Health

``` json
{
  "status": "healthy",
  "checks": {
    "events": "ok",
    "payments": "ok",
    "circuit_payments": "CLOSED"
  }
}
```

### 1.3 Dependency Map

``` text
Gateway
  ├──> Events
  │      ├──> PostgreSQL
  │      └──> Redis
  │
  └──> Payments
```

Critical path:

``` text
Client
  → Gateway
  → Events
  → Redis / PostgreSQL
  → Payments
  → Events
  → Redis / PostgreSQL
  → confirmed
```

### 1.4 Failure Exploration

  -----------------------------------------------------------------------------------------------------------------------------------
  Component    Events List                    Reserve                    Pay                             Health        User impact
  killed                                                                                                               
  ------------ ------------------------------ -------------------------- ------------------------------- ------------- --------------
  `payments`   Works                          Works                      Fails:                          `degraded`,   Users can
                                                                         `Payment service unavailable`   payments down browse and
                                                                                                                       reserve, but
                                                                                                                       cannot pay

  `events`     Fails:                         Fails                      Payment may succeed, but        `degraded`,   Browsing and
               `Events service unavailable`                              confirmation fails              events down   reservations
                                                                                                                       unavailable;
                                                                                                                       payment
                                                                                                                       confirmation
                                                                                                                       can fail

  `redis`      Works                          Fails:                     Confirmation fails              Events        Browsing
                                              `Events service timeout`                                   dependency    works, but
                                                                                                         degraded      reservation
                                                                                                                       flow is
                                                                                                                       unavailable

  `postgres`   Fails:                         Fails / HTTP 500           Confirmation fails              `degraded`,   Event data and
               `Events service unavailable`                                                              events        reservations
                                                                                                         degraded      are
                                                                                                                       unavailable
  -----------------------------------------------------------------------------------------------------------------------------------

#### Payments down

``` text
Events List: works
Reserve: works
Pay: {"detail":"Payment service unavailable"}
Health:
{"status":"degraded","checks":{"events":"ok","payments":"down","circuit_payments":"CLOSED"}}
```

#### Events down

``` text
Events List: {"detail":"Events service unavailable"}
Reserve: {"detail":"Events service unavailable"}
Pay: {"detail":"Payment succeeded but confirmation failed — contact support"}
Health:
{"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}
```

#### Redis down

``` text
Events List: works
Reserve: {"detail":"Events service timeout"}
Pay: {"detail":"Payment succeeded but confirmation failed — contact support"}
Health:
{"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}
```

#### PostgreSQL down

``` text
Events List: {"detail":"Events service unavailable"}
Reserve: HTTP 500 Internal Server Error
Pay: {"detail":"Payment succeeded but confirmation failed — contact support"}
Health:
{"status":"degraded","checks":{"events":"degraded","payments":"ok","circuit_payments":"CLOSED"}}
```

### 1.5 Load Generator

#### Baseline

With all services healthy:

``` text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=40 success=40 fail=0 error_rate=0%
[10s] requests=41 success=41 fail=0 error_rate=0%
[10s] requests=42 success=42 fail=0 error_rate=0%
[10s] requests=43 success=43 fail=0 error_rate=0%
[20s] requests=80 success=80 fail=0 error_rate=0%
[20s] requests=81 success=81 fail=0 error_rate=0%
[20s] requests=82 success=82 fail=0 error_rate=0%
---
Done. total=118 success=118 fail=0 error_rate=0%
```

#### Payments stopped during load

``` text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=41 success=37 fail=4 error_rate=9.7%
[10s] requests=42 success=37 fail=5 error_rate=11.9%
[10s] requests=43 success=38 fail=5 error_rate=11.6%
[10s] requests=44 success=38 fail=6 error_rate=13.6%
[20s] requests=80 success=67 fail=13 error_rate=16.2%
[20s] requests=81 success=68 fail=13 error_rate=16.0%
[20s] requests=82 success=69 fail=13 error_rate=15.8%
[20s] requests=83 success=70 fail=13 error_rate=15.6%
---
Done. total=118 success=98 fail=20 error_rate=16.9%
```

The healthy baseline had a 0% error rate. After Payments was stopped,
the error rate increased to 16.9% by the end of the test.

------------------------------------------------------------------------

## Task 2 --- Graceful Degradation

### Change

`app/gateway/main.py` was updated to catch `httpx.ConnectError`
specifically for the Payments call and return HTTP 503 with a structured
JSON response.

### Verification with Payments down

Payments was stopped:

``` bash
docker compose stop payments
```

#### Events still worked

``` text
GET /events → 200 OK
```

#### Reserve still worked

``` text
POST /events/1/reserve → 200 OK
```

Response:

``` json
{
  "reservation_id": "26ab0997-a843-4cdc-8dbc-e22029a2569c",
  "event_id": 1,
  "quantity": 1,
  "total_cents": 5000,
  "expires_in_seconds": 300
}
```

#### Pay returned a clear 503

``` text
HTTP/1.1 503 Service Unavailable
content-type: application/json
```

``` json
{
  "error": "payments_unavailable",
  "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.",
  "reservation_id": "26ab0997-a843-4cdc-8dbc-e22029a2569c"
}
```

#### Payments restored

After starting Payments again:

``` bash
docker compose start payments
```

The same reservation was successfully paid:

``` text
HTTP/1.1 200 OK
```

``` json
{
  "order_id": "26ab0997-a843-4cdc-8dbc-e22029a2569c",
  "event_id": 1,
  "quantity": 1,
  "total_cents": 5000,
  "status": "confirmed"
}
```

### Gateway diff

``` diff
@@ -336,6 +336,15 @@ async def pay_reservation(reservation_id: str):
         raise HTTPException(504, "Payment service timeout")
     except httpx.HTTPStatusError as e:
         raise HTTPException(e.response.status_code, "Payment failed")
+    except httpx.ConnectError:
+        return JSONResponse(
+            status_code=503,
+            content={
+                "error": "payments_unavailable",
+                "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.",
+                "reservation_id": reservation_id,
+            },
+        )
     except Exception as e:
         log.error(f"payment error: {e}")
         raise HTTPException(502, "Payment service unavailable")
```

### Result

When Payments is unavailable, users can still browse events and create
reservations. Payment requests fail explicitly with HTTP 503 and an
actionable message. Once Payments is restored, the reservation can be
paid successfully.

------------------------------------------------------------------------

## Task 3 --- GitHub Community Engagement

Task 3 has not yet been completed.

Required actions: - Star the course repository. - Star
`simple-container-com/api`. - Follow `@Cre-eD`. - Follow `@Naghme98`. -
Follow `@pierrepicaud`. - Follow at least 3 classmates.

After completing these actions, add a short GitHub Community section
explaining that stars help bookmark and give visibility to useful
open-source projects, while following developers helps discover their
work and maintain professional connections.

------------------------------------------------------------------------

## Bonus Task --- Resource Usage Under Load

Not completed yet.

The bonus requires collecting `docker stats` for: 1. idle services; 2.
services under load; 3. services under load with Payments fault
injection;

and then comparing CPU, memory, network I/O, and process counts.

------------------------------------------------------------------------

## Conclusion

The QuickTicket system was successfully deployed and its critical path
was verified. Systematic failure testing showed that the Gateway depends
on Events for event and reservation operations and on Payments for
completing purchases. Events in turn depends on PostgreSQL and Redis.

The load test demonstrated a clear increase in errors when Payments was
unavailable: from 0% with all services healthy to 16.9% during the
failure experiment.

Task 2 was implemented and verified successfully. With Payments down,
event browsing and reservations remained available, while payment
returned an explicit HTTP 503 response containing the reservation ID and
a message that the reservation was held. After Payments was restored,
the reservation was successfully confirmed.
