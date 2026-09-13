# Lab 1 — SRE Philosophy: Deploy, Break, Understand

## Task 1 — Deploy & Break QuickTicket

### 1. Docker Compose

```text
NAME             IMAGE                COMMAND                  SERVICE    CREATED         STATUS                        PORTS
app-events-1     app-events           "uvicorn main:app --…"   events     5 minutes ago   Up About a minute             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    5 minutes ago   Up About a minute             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-payments-1   app-payments         "uvicorn main:app --…"   payments   5 minutes ago   Up 5 minutes                  0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   5 minutes ago   Up About a minute (healthy)
app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      5 minutes ago   Up 5 minutes (healthy)        0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

### 2. Critical Path

#### List events

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

#### Reserve

```json
{
    "reservation_id": "b9a651d1-a27a-48e1-95f7-c1459b3a24ca",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}
```

#### Pay

```json
{
    "order_id": "b9a651d1-a27a-48e1-95f7-c1459b3a24ca",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "status": "confirmed"
}
```

### 3. Healthy System

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
gateway -> events -> postgres
gateway -> events -> redis
gateway -> payments
```

### 5. Failure Table

| Component Killed | Events List | Reserve | Pay | Health Check | User Impact |
|---|---|---|---|---|---|
| `payments` | Works (`200`) | Works | Fails: `502 Bad Gateway` — `Payment service unavailable` | `degraded`, payments=`down` | Users can browse and reserve, but cannot pay |
| `events` | Fails: `502 Bad Gateway` | Fails: `502 Bad Gateway` | Fails: `500 Internal Server Error` — payment succeeds but confirmation fails | `degraded`, events=`down` | Event operations are unavailable; payment may succeed without reservation confirmation |
| `redis` | Works (`200`) | Fails: `504 Gateway Timeout` | Fails: `500 Internal Server Error` — payment succeeds but confirmation fails | `degraded`, events=`down` | Events can be viewed, but reservations and confirmations fail |
| `postgres` | Fails: `502 Bad Gateway` | Fails: `500 Internal Server Error` | Fails: `500 Internal Server Error` — payment succeeds but confirmation fails | `degraded`, events=`degraded` | Event data and the booking flow become unavailable |

### 6. Load Generator

Payments was stopped after the initial healthy period while the load generator was running.

```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=42 success=42 fail=0 error_rate=0%
[10s] requests=43 success=43 fail=0 error_rate=0%
[10s] requests=44 success=44 fail=0 error_rate=0%
[10s] requests=45 success=45 fail=0 error_rate=0%
[20s] requests=86 success=83 fail=3 error_rate=3.4%
[20s] requests=87 success=84 fail=3 error_rate=3.4%
[20s] requests=88 success=85 fail=3 error_rate=3.4%
[20s] requests=89 success=85 fail=4 error_rate=4.4%
---
Done. total=129 success=121 fail=8 error_rate=6.2%
```

The error rate increased from 0% before the payments failure to 6.2% by the end of the run.

---

## Task 2 — Graceful Degradation

### Gateway Change

```diff
     except CircuitOpenError:
         log.error("circuit open, skipping payments call")
         raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
+    except httpx.ConnectError:
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
```

### Verification

With `payments` stopped, reservation creation still worked:

```json
{
    "reservation_id": "2c4e060f-204d-4402-af71-953d90bb7be1",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}
```

Payment returned a clear `503 Service Unavailable` response:

```text
HTTP/1.1 503 Service Unavailable
content-type: application/json

{"error":"payments_unavailable","message":"Payment service is temporarily down. Your reservation is held — try again in a few minutes.","reservation_id":"2c4e060f-204d-4402-af71-953d90bb7be1"}
```

---

## Task 3 — GitHub Community

Stars help developers bookmark useful open-source projects and increase their visibility in the community. Following developers makes it easier to discover their work, stay aware of teammates' activity, and maintain professional connections for future collaboration.

---

## Bonus Task — Resource Usage Under Load

### Idle

```text
NAME             CPU %     MEM USAGE / LIMIT      NET I/O           PIDS
app-gateway-1    0.26%     38.04MiB / 15.33GiB   3.58kB / 2.28kB   2
app-events-1     0.22%     43.24MiB / 15.33GiB   415kB / 560kB     2
app-postgres-1   4.25%     25.02MiB / 15.33GiB   224kB / 261kB     8
app-payments-1   0.22%     32.46MiB / 15.33GiB   1.22kB / 126B     1
app-redis-1      5.02%     4.559MiB / 15.33GiB   65.5kB / 26.9kB   6
```

### Under Load

Load generator: 10 requests/second for 30 seconds.

```text
NAME             CPU %     MEM USAGE / LIMIT      NET I/O           PIDS
app-gateway-1    5.07%     38.41MiB / 15.33GiB   145kB / 140kB     2
app-events-1     2.65%     43.27MiB / 15.33GiB   537kB / 727kB     2
app-postgres-1   0.78%     24.94MiB / 15.33GiB   292kB / 340kB     8
app-payments-1   0.23%     33.6MiB / 15.33GiB    5.27kB / 2.91kB   2
app-redis-1      1.12%     4.109MiB / 15.33GiB   83.8kB / 34.6kB   6
```

### Under Stress With Payment Fault Injection

Payments was restarted with:

```text
PAYMENT_FAILURE_RATE=0.3
PAYMENT_LATENCY_MS=500
```

The same 10 RPS load was then applied.

```text
NAME             CPU %     MEM USAGE / LIMIT      NET I/O           PIDS
app-payments-1   0.25%     35.13MiB / 15.33GiB   3.03kB / 2.06kB   2
app-gateway-1    3.62%     38.54MiB / 15.33GiB   458kB / 441kB     2
app-events-1     1.76%     43.34MiB / 15.33GiB   798kB / 1.08MB    2
app-postgres-1   0.45%     24.84MiB / 15.33GiB   440kB / 517kB     8
app-redis-1      0.99%     4.359MiB / 15.33GiB   110kB / 46.4kB    6
```

### Analysis

The `events` service used the most memory in all three scenarios, staying at about 43 MiB. Its memory usage remained almost unchanged under load, so the test did not show significant memory growth caused by traffic.

Under normal load, `gateway` had the highest observed CPU usage at 5.07%, increasing from 0.26% at idle. This is expected because all external requests pass through the gateway and it performs routing and calls to downstream services.

With payment fault injection enabled, gateway memory increased slightly from 38.41 MiB to 38.54 MiB, while its observed CPU usage was 3.62%. The injected 500 ms payment latency makes payment requests remain active in the gateway for longer. In this single snapshot this did not produce a large memory increase, but it demonstrates how slow downstream dependencies can cause the gateway to hold requests and connections for longer.

The `payments` service also increased from 32.46 MiB at idle to 35.13 MiB during the fault-injection scenario. Overall, the measurements show that the gateway is the most CPU-sensitive component under request load, while the events service consistently consumes the most memory.