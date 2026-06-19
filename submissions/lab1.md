## Task 1 — Deploy & Break QuickTicket 

1. Output of `docker compose ps` showing all 5 services running
```bash
$ docker compose ps
NAME             IMAGE                COMMAND                  SERVICE    CREATED              STATUS                        PORTS
app-events-1     app-events           "uvicorn main:app --…"   events     About a minute ago   Up About a minute             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    About a minute ago   Up About a minute             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-payments-1   app-payments         "uvicorn main:app --…"   payments   About a minute ago   Up About a minute             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   About a minute ago   Up About a minute (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      About a minute ago   Up About a minute (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```
2. Output of the full critical path (list → reserve → pay) with real data
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

$  curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity": 1}' | python3 -m json.tool
{
    "reservation_id": "47c56f64-2560-4261-9101-ce327a9a6a93",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}

$ curl -s -X POST http://localhost:3080/reserve/47c56f64-2560-4261-9101-ce327a9a6a93/pay | python3 -m json.tool
{
    "order_id": "47c56f64-2560-4261-9101-ce327a9a6a93",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "status": "confirmed"
}

```
3. Output of `curl -s http://localhost:3080/health` when everything is healthy
```bash
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
4. A dependency map:
   ```mermaid
   flowchart LR
       gateway["gateway"]
       events["events"]
       payments["payments"]
       postgres["postgres"]
       redis["redis"]

       gateway -- "GET /events\nGET /events/{id}\nPOST /events/{id}/reserve" --> events
       gateway -- "POST /reserve/{id}/pay" --> payments
       gateway -. "GET /health" .-> events
       gateway -. "GET /health" .-> payments

       events -- "SQL: events, orders" --> postgres
       events -- "Redis: reservations, holds" --> redis
       events -. "GET /health" .-> postgres
       events -. "GET /health" .-> redis
   ```
5. A failure table:

| Component Killed | Events List | Reserve | Pay  | Health Check | User Impact                                      |
|-----------------|-------------|---------|------|----------------------|--------------------------------------------------|
| payments        | OK          | OK      | FAIL |       degraded       | Impossible to pay                                |
| events          | FAIL        | FAIL    | FAIL |       degraded       | Whole service is unavailable                     |
| redis           | OK          | FAIL    | FAIL |       degraded       | List of events is the only available service     |
| postgres        | FAIL        | FAIL    | FAIL |       degraded       | Whole service is unavailable (no available data) |

*Note:* 
Reservation continues to work after killing payments because it is created during the /reserve step, which happens before the payment process. 
Payment is only required for order confirmation and does not affect the creation of a reservation.

6. Load generator output showing the error rate spike when payments is killed

```
$ ./app/loadgen/run.sh 5 30
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=23 success=19 fail=4 error_rate=17.3%
[10s] requests=24 success=20 fail=4 error_rate=16.6%
[20s] requests=33 success=26 fail=7 error_rate=21.2%
---
Done. total=44 success=36 fail=8 error_rate=18.1%

```

## Task 2 — Graceful Degradation

Diff of the gateway change
```diff
diff --git a/app/gateway/main.py b/app/gateway/main.py
index c86db33..6b7fb12 100644
--- a/app/gateway/main.py
+++ b/app/gateway/main.py
@@ -172,6 +172,9 @@ class RateLimiter:

 payments_cb = CircuitBreaker(CB_FAILURE_THRESHOLD, CB_COOLDOWN_S, name="payments")
 rate_limiter = RateLimiter(RATE_LIMIT_RPS)
+PAYMENTS_UNAVAILABLE_MESSAGE = (
+    "Payment service is temporarily down. Your reservation is held — try again in a few minutes."
+)


 # --- Middleware ---
@@ -329,11 +332,16 @@ async def pay_reservation(reservation_id: str):
     try:
         pay_resp = await payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
         payment_ref = pay_resp.json().get("payment_ref", "unknown")
-    except CircuitOpenError:
-        log.error("circuit open, skipping payments call")
-        raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
-    except httpx.TimeoutException:
-        raise HTTPException(504, "Payment service timeout")
+    except (CircuitOpenError, httpx.ConnectError, httpx.TimeoutException):
+        log.warning("payments unavailable for reservation %s", reservation_id)
+        return JSONResponse(
+            status_code=503,
+            content={
+                "error": "payments_unavailable",
+                "message": PAYMENTS_UNAVAILABLE_MESSAGE,
+                "reservation_id": reservation_id,
+            },
+        )
     except httpx.HTTPStatusError as e:
         raise HTTPException(e.response.status_code, "Payment failed")
     except Exception as e:
```

Verification with `payments` stopped
```bash
$ docker compose stop payments
[+] Stopping 1/1
 ✔ Container app-payments-1  Stopped   
$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" -d '{"quantity": 1}'
{"reservation_id":"f1123c5a-1891-47d8-b593-58f36b704ae3","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
timab@LAPTOP-CVMUCS3O MINGW64 ~/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro (main)
$ curl -s -X POST http://localhost:3080/reserve/f1123c5a-1891-47d8-b593-58f36b704ae3/pay
{"error":"payments_unavailable","message":"Payment service is temporarily down. Your reservation is held — try again in a few minutes.","reservation_id":"RESERVATION_ID"}

```
## Task 3 — GitHub Community Engagement 

### GitHub Community

Starring repositories helps surface useful open-source projects, 
makes them easier to revisit later, and gives maintainers a signal 
that their work is valuable. Following developers helps us learn from 
their activity, stay aware of what teammates are building, and grow a 
professional network that is useful in group projects and beyond.

## Bonus Task — Resource Usage Under Load

### Stats tables

The following tables were got via running the given commands:
#### Idle
```bash
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-payments-1   0.25%     34.14MiB / 7.622GiB   872B / 126B       1
app-gateway-1    0.20%     38.57MiB / 7.622GiB   447kB / 451kB     2
app-events-1     0.20%     41.46MiB / 7.622GiB   399kB / 521kB     2
app-redis-1      0.73%     3.648MiB / 7.622GiB   61.3kB / 24.2kB   6
app-postgres-1   0.00%     24.39MiB / 7.622GiB   217kB / 248kB     8
```
#### Under load
```bash
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-gateway-1    1.52%     38.96MiB / 7.622GiB   199kB / 200kB     2
app-events-1     0.98%     41.4MiB / 7.622GiB    183kB / 238kB     2
app-redis-1      0.82%     3.605MiB / 7.622GiB   31.1kB / 12.1kB   6
app-postgres-1   0.22%     24.3MiB / 7.622GiB    100kB / 112kB     8
app-payments-1   0.62%     34.73MiB / 7.622GiB   10.5kB / 6.06kB   2
```
#### Under stress with fault injection
```bash
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-payments-1   0.22%     34.7MiB / 7.622GiB    6.14kB / 4.4kB    2
app-gateway-1    2.08%     38.79MiB / 7.622GiB   377kB / 380kB     2
app-events-1     0.90%     41.45MiB / 7.622GiB   338kB / 442kB     2
app-redis-1      0.92%     3.379MiB / 7.622GiB   53.7kB / 21.1kB   6
app-postgres-1   0.26%     24.64MiB / 7.622GiB   184kB / 209kB     8
```

The measurements show that `redis` is the least expensive service in memory terms, while `events` is the most expensive one.
The memory usage of these services insignificantly changes under load (<0.3 MiB for `redis` and ≈0.06 MiB for `events`)

In idle state `redis` showed the most CPU usage. However, probably,
it is caused by short-term background activity and health checks. `gateway` 
shows the biggest CPU increase (and usage) under load and stress with fault injection,
because it fans out to other services and waits on responses. Hence, fault injection in
`payments` affects the `gateway` the 
most by increasing request latency and reducing throughput.