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
    "reservation_id": "9c3a81b4-7e12-42da-91a5-e3902f814b10",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}

$ curl -s -X POST http://localhost:3080/reserve/9c3a81b4-7e12-42da-91a5-e3902f814b10/pay | python3 -m json.tool
{
    "order_id": "9c3a81b4-7e12-42da-91a5-e3902f814b10",
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
[10s] requests=22 success=18 fail=4 error_rate=18.1%
[20s] requests=25 success=20 fail=5 error_rate=20.0%
[30s] requests=31 success=24 fail=7 error_rate=22.5%
---
Done. total=43 success=34 fail=9 error_rate=20.9%

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
{"reservation_id":"e72b904d-16a8-42df-b3f4-50821c97a2e5","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}

$ curl -s -X POST http://localhost:3080/reserve/e72b904d-16a8-42df-b3f4-50821c97a2e5/pay
{"error":"payments_unavailable","message":"Payment service is temporarily down. Your reservation is held — try again in a few minutes.","reservation_id":"e72b904d-16a8-42df-b3f4-50821c97a2e5"}

```
## Task 3 — GitHub Community Engagement 

### GitHub Community

Starring repositories helps bookmark critical open-source dependencies and signals appreciation to project maintainers, increasing the visibility of reliable tools. Following peers and engineers enables tracking industry practices, keeping up with active development across collaborative projects, and building professional connections within the engineering community.

## Bonus Task — Resource Usage Under Load

### Stats tables

The following tables were gathered by monitoring service resources across different load profiles:

#### Idle
```bash
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-payments-1   0.21%     34.20MiB / 11.85GiB   910B / 140B       1
app-gateway-1    0.18%     38.45MiB / 11.85GiB   452kB / 456kB     2
app-events-1     0.19%     41.30MiB / 11.85GiB   402kB / 525kB     2
app-redis-1      0.68%     3.612MiB / 11.85GiB   62.1kB / 24.8kB   6
app-postgres-1   0.00%     24.42MiB / 11.85GiB   220kB / 251kB     8
```
#### Under load
```bash
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-gateway-1    1.48%     39.10MiB / 11.85GiB   204kB / 205kB     2
app-events-1     0.94%     41.52MiB / 11.85GiB   186kB / 241kB     2
app-redis-1      0.79%     3.620MiB / 11.85GiB   32.0kB / 12.5kB   6
app-postgres-1   0.21%     24.35MiB / 11.85GiB   102kB / 115kB     8
app-payments-1   0.59%     34.80MiB / 11.85GiB   11.0kB / 6.20kB   2
```
#### Under stress with fault injection
```bash
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-payments-1   0.20%     34.75MiB / 11.85GiB   6.30kB / 4.50kB   2
app-gateway-1    2.12%     38.85MiB / 11.85GiB   382kB / 385kB     2
app-events-1     0.88%     41.48MiB / 11.85GiB   341kB / 446kB     2
app-redis-1      0.89%     3.390MiB / 11.85GiB   54.2kB / 21.4kB   6
app-postgres-1   0.25%     24.68MiB / 11.85GiB   187kB / 212kB     8
```

The measurements show that redis is the least expensive service in terms of memory consumption, while events is the most demanding. The memory footprint of both services remains relatively stable under load variations.

In the idle state, redis shows slightly elevated baseline CPU usage due to background polling and periodic health checks. The gateway experiences the most substantial CPU surge during load and fault injection because it acts as the ingress orchestrator, multiplexing requests and holding client connections while downstream dependencies respond slowly.