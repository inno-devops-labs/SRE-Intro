# Lab 1 Submission
## 1.1 
```
$ docker compose ps 
NAME IMAGE COMMAND SERVICE CREATED STATUS PORTS 
app-events-1 app-events "uvicorn main:app --…" events 30 minutes ago Up 5 minutes 0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp 
app-gateway-1 app-gateway "uvicorn main:app --…" gateway 30 minutes ago Up 30 minutes 0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp 
app-payments-1 app-payments "uvicorn main:app --…" payments 30 minutes ago Up 5 minutes 0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp 
app-postgres-1 postgres:17-alpine "docker-entrypoint.s…" postgres 30 minutes ago Up 5 minutes (healthy) 0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp 
app-redis-1 redis:7-alpine "docker-entrypoint.s…" redis 30 minutes ago Up 5 minutes (healthy) 0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```
## 1.2
```bash
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
```

```bash
$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity": 1}' | python3 -m json.tool
{
    "reservation_id": "daadfa05-8837-4f5c-91aa-2d14015b286f",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}
```
   
```shell
$ curl -s -X POST http://localhost:3080/reserve/RESERVATION_ID_HERE/pay | python3 -m json.tool
{
    "detail": "Payment service unavailable"
}
```
  
```shell
$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "degraded",
    "checks": {
        "events": "ok",
        "payments": "down",
        "circuit_payments": "CLOSED"
    }
}
```

## 1.3 Dependency Map
### Service Call Graph
```
gateway → events

gateway → payments

gateway → notifications (optional, fire-and-forget)

events → postgres

events → redis
```


## 1.4: Systematic Failure Exploration

For each scenario, one component was stopped with `docker compose stop <service>`, the endpoints were tested through the gateway (`localhost:3080`), then the service was brought back with `docker compose start <service>`.

---

### Payments stopped

```bash
$ docker compose stop payments
 Container app-payments-1 Stopping
 Container app-payments-1 Stopped
```

```bash
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
```

```bash
$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" -d '{"quantity": 1}'
{"reservation_id":"ff8a3017-1743-4a58-b5e1-33c56e5b5b57","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
```

```bash
$ curl -s -X POST http://localhost:3080/reserve/ff8a3017-1743-4a58-b5e1-33c56e5b5b57/pay
{"detail":"Payment service unavailable"}
```

```bash
$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "degraded",
    "checks": {
        "events": "ok",
        "payments": "down",
        "circuit_payments": "CLOSED"
    }
}
```
(HTTP status: 503)

```bash
$ docker compose start payments
 Container app-payments-1 Starting
 Container app-payments-1 Started
```

1. `GET /events` and `POST /events/{id}/reserve` — both go through the events service only.
2. `POST /reserve/{id}/pay` fails because the gateway cannot reach payments.
3. HTTP 502 with `{"detail":"Payment service unavailable"}`.
4. Yes. Returns HTTP 503, `"status": "degraded"`, and `"payments": "down"` while events stays `"ok"`.

---

### Events stopped

```bash
$ docker compose stop events
 Container app-events-1 Stopping
 Container app-events-1 Stopped
```

```bash
$ curl -s http://localhost:3080/events | python3 -m json.tool
{
    "detail": "Events service unavailable"
}
```

```bash
$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" -d '{"quantity": 1}'
{"detail":"Events service unavailable"}
```

```bash
$ curl -s -X POST http://localhost:3080/reserve/test-id/pay
{"detail":"Payment succeeded but confirmation failed — contact support"}
```

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
(HTTP status: 503)

```bash
$ docker compose start events
 Container app-postgres-1 Waiting
 Container app-redis-1 Waiting
 Container app-redis-1 Healthy
 Container app-postgres-1 Healthy
 Container app-events-1 Starting
 Container app-events-1 Started
```

1. None of the user-facing ticket flows work reliably. Payments is up, but listing and reserving both fail. Pay may charge the user (payments does not validate the reservation) and then fail at the confirmation step.
2. `GET /events`, `POST /events/{id}/reserve`, and the confirmation half of `POST /reserve/{id}/pay`.
3. HTTP 502 `{"detail":"Events service unavailable"}` for list/reserve; HTTP 500 `{"detail":"Payment succeeded but confirmation failed — contact support"}` if pay is attempted (dangerous partial failure).
4. Yes. HTTP 503, `"status": "degraded"`, `"events": "down"`.

---

### Redis stopped

```bash
$ docker compose stop redis
 Container app-redis-1 Stopping
 Container app-redis-1 Stopped
```

```bash
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
    ...
]
```

```bash
$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" -d '{"quantity": 1}'
{"detail":"Events service timeout"}
```

```bash
$ curl -s -X POST http://localhost:3080/reserve/test-id/pay
{"detail":"Payment succeeded but confirmation failed — contact support"}
```

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
(HTTP status: 503)

```bash
$ docker compose start redis
 Container app-redis-1 Starting
 Container app-redis-1 Started
```

1. `GET /events` still works — event data is read from Postgres. Payments is also reachable.
2. `POST /events/{id}/reserve` times out (Redis is needed to hold reservations). Pay fails at confirmation because no reservation exists in Redis.
3. HTTP 504 `{"detail":"Events service timeout"}` on reserve; HTTP 500 on pay if attempted after a failed/missing reservation.
4. Yes, indirectly. Gateway reports `"events": "down"` (events `/health` returns 503 because Redis is down) even though read-only listing still works. Overall status is `"degraded"` with HTTP 503.

---

### Postgres stopped

```bash
$ docker compose stop postgres
 Container app-postgres-1 Stopping
 Container app-postgres-1 Stopped
```

```bash
$ curl -s http://localhost:3080/events | python3 -m json.tool
{
    "detail": "Events service unavailable"
}
```

```bash
$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" -d '{"quantity": 1}'
Internal Server Error
```

```bash
$ curl -s -X POST http://localhost:3080/reserve/test-id/pay
{"detail":"Payment succeeded but confirmation failed — contact support"}
```

```bash
$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "degraded",
    "checks": {
        "events": "degraded",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```
(HTTP status: 503)

```bash
$ docker compose start postgres
 Container app-postgres-1 Starting
 Container app-postgres-1 Started
```

1. Only payments health/charge path is reachable. All event data operations break because Postgres stores events and orders.
2. `GET /events`, `POST /events/{id}/reserve`, and pay confirmation all fail.
3. HTTP 502 `{"detail":"Events service unavailable"}` for listing; HTTP 500 plain `Internal Server Error` on reserve (DB pool error); HTTP 500 on pay confirmation.
4. Yes. HTTP 503, `"status": "degraded"`, `"events": "degraded"` (events `/health` returns non-200 because Postgres check fails).
## Failure Table

| Component Killed | Events List                                | Reserve                                    | Pay                                                       | Health Check                                                         | User Impact                                                           |
| ---------------- | ------------------------------------------ | ------------------------------------------ | --------------------------------------------------------- | -------------------------------------------------------------------- | --------------------------------------------------------------------- |
| payments         | Works                                      | Works                                      | Fails (502 — `Payment service unavailable`)               | Degraded (503) — `payments: down`, `events: ok`                      | Can browse events and reserve tickets; cannot complete payment        |
| events           | Fails (502 — `Events service unavailable`) | Fails (502 — `Events service unavailable`) | Fails (500 — payment may charge, confirmation fails)      | Degraded (503) — `events: down`, `payments: ok`                      | Entire ticket flow broken; risky partial failure if user attempts pay |
| redis            | Works                                      | Fails (504 — `Events service timeout`)     | Fails (500 — `Payment succeeded but confirmation failed`) | Degraded (503) — `events: down` (Redis check fails in events health) | Can browse events; cannot hold or complete a reservation              |
| postgres         | Fails (502 — `Events service unavailable`) | Fails (500 — `Internal Server Error`)      | Fails (500 — confirmation fails after charge)             | Degraded (503) — `events: degraded` (Postgres check fails)           | Complete data-layer outage; no reliable event or order operations     |

## 1.5 Payload
```bash
[nix-shell:~/tmp/SRE-Intro/app]$ ./loadgen/run.sh 5 30 
QuickTicket Load Generator Target: http://localhost:3080 | RPS: 5 | Duration: 30s --- [10s] 
requests=41 success=41 fail=0 error_rate=0% [10s] 
requests=42 success=42 fail=0 error_rate=0% [10s] 
requests=43 success=43 fail=0 error_rate=0% [10s] 
requests=44 success=44 fail=0 error_rate=0% [20s] 
requests=83 success=83 fail=0 error_rate=0% [20s] 
requests=84 success=84 fail=0 error_rate=0% [20s] 
requests=85 success=85 fail=0 error_rate=0% [20s] 
requests=86 success=86 fail=0 error_rate=0% 
--- 
Done. total=124 success=124 fail=0 error_rate=0%
```

- Stopping payments after ~25s
```bash
[nix-shell:~/tmp/SRE-Intro/app]$ ./loadgen/run.sh 5 30
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=42 success=42 fail=0 error_rate=0%
[10s] requests=43 success=43 fail=0 error_rate=0%
[10s] requests=44 success=44 fail=0 error_rate=0%
[10s] requests=45 success=45 fail=0 error_rate=0%
[10s] requests=46 success=46 fail=0 error_rate=0%
[20s] requests=84 success=84 fail=0 error_rate=0%
[20s] requests=85 success=85 fail=0 error_rate=0%
[20s] requests=86 success=86 fail=0 error_rate=0%
[20s] requests=87 success=87 fail=0 error_rate=0%
---
Done. total=125 success=124 fail=1 error_rate=.8%
```

