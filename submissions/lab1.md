# Lab 1

## Task 1

I started the app, checked the normal flow, and then broke one part at a time to see what changed. I kept the commands simple and saved the real outputs here.

First I made sure all 5 services were up.

```bash
cd app
docker compose ps
```

```text
NAME             IMAGE                COMMAND                  SERVICE    CREATED              STATUS                   PORTS
app-events-1     app-events           "uvicorn main:app --…"   events     About a minute ago   Up About a minute        0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    About a minute ago   Up About a minute        0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-payments-1   app-payments         "uvicorn main:app --…"   payments   About a minute ago   Up 26 seconds            0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   4 minutes ago        Up 2 minutes (healthy)   0.0.0.0:55432->5432/tcp, [::]:55432->5432/tcp
app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      4 minutes ago        Up 2 minutes (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

Then I checked the normal user flow. Listing events worked, reservation worked, payment worked, and health was healthy.

```bash
curl -s http://localhost:3080/events | python3 -m json.tool
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
        "available": 98
    },
    {
        "id": 4,
        "name": "Python Workshop",
        "venue": "Lab 301",
        "date": "2026-09-22T14:00:00+00:00",
        "total_tickets": 25,
        "price_cents": 2000,
        "available": 24
    },
    {
        "id": 2,
        "name": "SRE Meetup",
        "venue": "Room 204",
        "date": "2026-10-01T18:00:00+00:00",
        "total_tickets": 30,
        "price_cents": 0,
        "available": 29
    },
    {
        "id": 5,
        "name": "Kubernetes Deep Dive",
        "venue": "Auditorium B",
        "date": "2026-10-10T10:00:00+00:00",
        "total_tickets": 80,
        "price_cents": 8000,
        "available": 79
    },
    {
        "id": 3,
        "name": "Cloud Native Summit",
        "venue": "Expo Center",
        "date": "2026-11-20T10:00:00+00:00",
        "total_tickets": 500,
        "price_cents": 15000,
        "available": 499
    }
]
```

```bash
curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  --data-binary '{"quantity":1}'
```

```json
{"reservation_id":"23a11055-abf0-4fd1-81e9-c0c078753323","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
```

```bash
curl -s -X POST http://localhost:3080/reserve/23a11055-abf0-4fd1-81e9-c0c078753323/pay
```

```json
{"order_id":"23a11055-abf0-4fd1-81e9-c0c078753323","event_id":1,"quantity":1,"total_cents":5000,"status":"confirmed"}
```

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
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

After that I read the three app files and wrote down the dependencies in a simple way.

```text
gateway -> events
gateway -> payments
events -> postgres
events -> redis
```

The main thing I saw is that `gateway` is just the front door. The `events` service does the real work for listing events and holding reservations. The `payments` service only does the charge call, but the full pay flow still needs `events` to confirm the order after the charge.

Then I stopped one component at a time and tested again. The health endpoint noticed a problem every time, but the user impact was different depending on what I killed.

| Component Killed | Events List | Reserve | Pay | Health Check | User Impact |
|-----------------|-------------|---------|-----|--------------|-------------|
| payments | worked, 200 | worked, 200 | failed, 502 "Payment service unavailable" | 503, payments down | browsing and reserving still work, payment is down |
| events | failed, 502 | failed, 502 | failed, 500 "Payment succeeded but confirmation failed - contact support" | 503, events down | almost everything breaks, and pay is risky because charge can happen before confirm |
| redis | worked, 200 | failed, 504 "Events service timeout" | failed, 500 "Payment succeeded but confirmation failed - contact support" | 503, events down | reads still work, but reservations and confirm flow break |
| postgres | failed, 502 | failed, 500 | failed, 500 "Payment succeeded but confirmation failed - contact support" | 503, events degraded | reads and writes break, and payment can finish before the order is confirmed |

Here are the simple outputs I used for those checks.

```text
payments stopped
GET /events -> HTTP 200
POST /events/1/reserve -> HTTP 200
POST /reserve/<id>/pay -> HTTP 502
GET /health -> HTTP 503 {"status":"degraded","checks":{"events":"ok","payments":"down","circuit_payments":"CLOSED"}}

events stopped
GET /events -> HTTP 502 {"detail":"Events service unavailable"}
POST /events/1/reserve -> HTTP 502 {"detail":"Events service unavailable"}
POST /reserve/<id>/pay -> HTTP 500 {"detail":"Payment succeeded but confirmation failed - contact support"}
GET /health -> HTTP 503 {"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}

redis stopped
GET /events -> HTTP 200
POST /events/1/reserve -> HTTP 504 {"detail":"Events service timeout"}
POST /reserve/<id>/pay -> HTTP 500 {"detail":"Payment succeeded but confirmation failed - contact support"}
GET /health -> HTTP 503 {"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}

postgres stopped
GET /events -> HTTP 502 {"detail":"Events service unavailable"}
POST /events/1/reserve -> HTTP 500
POST /reserve/<id>/pay -> HTTP 500 {"detail":"Payment succeeded but confirmation failed - contact support"}
GET /health -> HTTP 503 {"status":"degraded","checks":{"events":"degraded","payments":"ok","circuit_payments":"CLOSED"}}
```

I also ran the small load test from the lab. I used the exact command from the task and then stopped `payments` while the script was still running. The error rate got higher after that, which is what I expected.

```bash
chmod +x app/loadgen/run.sh
./app/loadgen/run.sh 5 30
```

```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=23 success=22 fail=1 error_rate=4.3%
[10s] requests=24 success=23 fail=1 error_rate=4.1%
[20s] requests=44 success=40 fail=4 error_rate=9.0%
[20s] requests=45 success=41 fail=4 error_rate=8.8%
---
Done. total=65 success=61 fail=4 error_rate=6.1%
```

## Task 2

For the optional part I changed the gateway so it gives a clear message when `payments` is down. Reserve still works, and pay now returns a clean 503 with the reservation id.

File: `app/gateway/main.py`  
Line: around `335`

```python
except httpx.ConnectError:
    return JSONResponse(
        status_code=503,
        content={
            "error": "payments_unavailable",
            "message": "Payment service is temporarily down. Your reservation is held. Try again in a few minutes.",
            "reservation_id": reservation_id,
        },
    )
```

This catches the connection error when `payments` is down. Instead of a generic 502, it returns a clear 503 response and keeps the reservation id in the reply.

```bash
curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  --data-binary '{"quantity":1}' \
  -w '\nHTTP_STATUS:%{http_code}\n'
```

```text
{"reservation_id":"1d08bbca-a54f-413a-a4c6-f01d210138c0","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
HTTP_STATUS:200
```

```bash
curl -s -X POST http://localhost:3080/reserve/1d08bbca-a54f-413a-a4c6-f01d210138c0/pay \
  -w '\nHTTP_STATUS:%{http_code}\n'
```

```text
{"error":"payments_unavailable","message":"Payment service is temporarily down. Your reservation is held. Try again in a few minutes.","reservation_id":"1d08bbca-a54f-413a-a4c6-f01d210138c0"}
HTTP_STATUS:503
```

## Task 3

## GitHub Community

Starring repos matters because it is a small signal that people find the project useful. It also helps other people find good tools faster, and for maintainers it is a simple sign that someone is actually using the work.

## Bonus Task

I also checked resource usage in three cases: idle, normal load, and load with fault injection in `payments`.

Idle:

```text
app-gateway-1    0.16%   41.17MiB / 15.35GiB   21.4kB / 21kB     3
app-events-1     0.14%   40.53MiB / 15.35GiB   17.7kB / 18.4kB   2
app-payments-1   0.16%   32.71MiB / 15.35GiB   3.5kB / 2.61kB    2
app-postgres-1   0.42%   28.41MiB / 15.35GiB   59.5kB / 62.9kB   8
app-redis-1      0.34%   4.062MiB / 15.35GiB   23.3kB / 9.28kB   6
```

Load:

```text
app-gateway-1    1.18%   42.3MiB / 15.35GiB    65.5kB / 65.1kB   3
app-events-1     0.76%   42.08MiB / 15.35GiB   55.6kB / 68.1kB   2
app-payments-1   0.14%   33.7MiB / 15.35GiB    4.23kB / 3.19kB   2
app-postgres-1   0.17%   29.75MiB / 15.35GiB   79.4kB / 88.1kB   8
app-redis-1      0.40%   4.879MiB / 15.35GiB   26.6kB / 10.6kB   6
```

Load with fault injection in `payments`:

```text
app-payments-1   0.17%   40.52MiB / 15.35GiB   2.91kB / 2.49kB   2
app-gateway-1    0.91%   41.62MiB / 15.35GiB   197kB / 198kB     3
app-events-1     0.59%   42.06MiB / 15.35GiB   171kB / 220kB     2
app-postgres-1   0.17%   30.18MiB / 15.35GiB   141kB / 157kB     8
app-redis-1      0.33%   5.309MiB / 15.35GiB   45.4kB / 18.5kB   6
```

The biggest memory users were `gateway` and `events` in normal runs. With fault injection, `payments` used more memory than before, but `events` was still the highest by a small margin.

The highest CPU under load was `gateway`. That makes sense because every request goes through it first, and it has to wait for the other services too.

With fault injection, the system got slower and the load generator started to show failures. In this short run I did not see a big memory jump in `gateway`, but slow `payments` still made the whole request path less stable.
