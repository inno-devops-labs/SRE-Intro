# Lab 1 — SRE Philosophy: Deploy, Break, Understand
## Proof of Work by Victoria Yurina B24-CBS-01

### Task 1 — Deploy & Break QuickTicket
#### Output of docker compose ps showing all 5 services running
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ sudo docker-compose ps
NAME             IMAGE                COMMAND                  SERVICE    CREATED          STATUS                    PORTS
app-events-1     app-events           "uvicorn main:app --…"   events     23 seconds ago   Up 16 seconds             0.0.0.0:8081->8081/tcp, :::8081->8081/tcp
app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    23 seconds ago   Up 15 seconds             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-payments-1   app-payments         "uvicorn main:app --…"   payments   23 seconds ago   Up 22 seconds             0.0.0.0:8082->8082/tcp, :::8082->8082/tcp
app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   23 seconds ago   Up 22 seconds (healthy)   0.0.0.0:5432->5432/tcp, :::5432->5432/tcp
app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      23 seconds ago   Up 22 seconds (healthy)   0.0.0.0:6379->6379/tcp, :::6379->6379/tcp
```

#### Output of the full critical path (list → reserve → pay) with real data
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ curl -s http://localhost:3080/events | python3 -m json.tool 
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
                                                                                                                                              
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity": 1}' | python3 -m json.tool
{
    "reservation_id": "a15fa6b7-efb6-4e60-9d87-c50d3e8adaa6",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}
                                                                                                                
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ curl -s -X POST http://localhost:3080/reserve/a15fa6b7-efb6-4e60-9d87-c50d3e8adaa6/pay | python3 -m json.tool
{
    "order_id": "a15fa6b7-efb6-4e60-9d87-c50d3e8adaa6",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "status": "confirmed"
}
```

#### Output of curl -s http://localhost:3080/health when everything is healthy
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

#### A dependency map
```
                 +------------------+
                 |     Gateway      |
                 |   (API Router)   |
                 +--------+---------+
                          |
          +---------------+---------------+
          |                               |
          v                               v
+------------------+          +------------------+
|      Events      |          |    Payments      |
| Ticket Service   |          | Payment Service  |
+--------+---------+          +------------------+
         |
         |
         +----------------+
         |                |
         v                v
+----------------+  +----------------+
|   PostgreSQL   |  |     Redis      |
| Event & Orders |  | Reservations   |
+----------------+  +----------------+
```

#### A failure table:
```
| Component Killed | Events List | Reserve | Pay |                                         Health Check                                                     |                           User Impact                          |
|------------------|-------------|---------|-----|----------------------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| payments         |      ✔      |    ✔    |  ×  | {"status": "degraded", "checks": {"events": "ok", "payments": "down", "circuit_payments": "CLOSED"}}     | "detail": "Payment service unavailable"                        |
| events           |      ×      |    ×    |  ×  | {"status": "degraded", "checks": {"events": "down", "payments": "ok", "circuit_payments": "CLOSED"}}     | {"detail":"Events service unavailable"}                        |
| redis            |      ✔      |    ×    |  ×  | {"status": "degraded", "checks": {"events": "down", "payments": "ok", "circuit_payments": "CLOSED"}}     | {"detail":"Events service timeout"}                            |
| postgres         |      ×      |    ×    |  ×  | {"status": "degraded", "checks": {"events": "degraded", "payments": "ok", "circuit_payments": "CLOSED"}} | "detail": "Events service unavailable"}, Internal Server Error |
```

#### Load generator output showing the error rate spike when payments is killed
Before kill:
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ ./loadgen/run.sh 5 30
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=42 success=42 fail=0 error_rate=0%
[10s] requests=43 success=43 fail=0 error_rate=0%
[10s] requests=44 success=44 fail=0 error_rate=0%
[10s] requests=45 success=45 fail=0 error_rate=0%
[20s] requests=86 success=86 fail=0 error_rate=0%
[20s] requests=87 success=87 fail=0 error_rate=0%
[20s] requests=88 success=88 fail=0 error_rate=0%
[20s] requests=89 success=89 fail=0 error_rate=0%
---
Done. total=128 success=128 fail=0 error_rate=0%
```

After kill:
```                                                                                                                                     
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ ./loadgen/run.sh 5 30
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=42 success=37 fail=5 error_rate=11.9%
[10s] requests=43 success=38 fail=5 error_rate=11.6%
[10s] requests=44 success=39 fail=5 error_rate=11.3%
[10s] requests=45 success=40 fail=5 error_rate=11.1%
[20s] requests=85 success=74 fail=11 error_rate=12.9%
[20s] requests=86 success=75 fail=11 error_rate=12.7%
[20s] requests=87 success=76 fail=11 error_rate=12.6%
[20s] requests=88 success=77 fail=11 error_rate=12.5%
---
Done. total=128 success=114 fail=14 error_rate=10.9%
```

### Task 2 — Graceful Degradation
#### The diff of your gateway change (git diff app/gateway/main.py)
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ git diff gateway/main.py 
diff --git a/app/gateway/main.py b/app/gateway/main.py
index c86db33..47792c1 100644
--- a/app/gateway/main.py
+++ b/app/gateway/main.py
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
@@ -327,20 +321,65 @@ async def pay_reservation(reservation_id: str):
         return resp
 
     try:
-        pay_resp = await payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
+        pay_resp = await payments_cb.call(
+            lambda: call_with_retry(_charge, target="payments")
+        )
         payment_ref = pay_resp.json().get("payment_ref", "unknown")
+
     except CircuitOpenError:
         log.error("circuit open, skipping payments call")
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
+
     except httpx.TimeoutException:
-        raise HTTPException(504, "Payment service timeout")
-    except httpx.HTTPStatusError as e:
-        raise HTTPException(e.response.status_code, "Payment failed")
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
+    except httpx.HTTPStatusError:
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
     except Exception as e:
         log.error(f"payment error: {e}")
-        raise HTTPException(502, "Payment service unavailable")
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
 
-    # 2. Confirm reservation in events.
     try:
         confirm_resp = await client.post(
             f"{EVENTS_URL}/reservations/{reservation_id}/confirm",
@@ -348,11 +387,14 @@ async def pay_reservation(reservation_id: str):
         )
         confirm_resp.raise_for_status()
         result = confirm_resp.json()
+
     except Exception as e:
         log.error(f"confirm error after payment: {e}")
-        raise HTTPException(500, "Payment succeeded but confirmation failed — contact support")
+        raise HTTPException(
+            500,
+            "Payment succeeded but confirmation failed — contact support",
+        )
 
-    # 3. Fire-and-forget notify (don't await → don't add latency, don't fail user).
     asyncio.create_task(_notify_order_confirmed(reservation_id))
 
     return result
```

#### Output of reserve (works) and pay (clear 503) when payments is down
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ sudo docker-compose stop payments 
[+] Stopping 1/1
 ✔ Container app-payments-1  Stopped                                                                                                    0.6s 
                                                                                                                                              
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" -d '{"quantity": 1}'
{"reservation_id":"8c4605c7-e45a-45c5-a2e5-86b793e73a81","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}                                                                                                                                              
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ curl -s -X POST http://localhost:3080/reserve/RESERVATION_ID/pay
{"error":"payments_unavailable","message":"Payment service is temporarily down. Your reservation is held — try again in a few minutes.","reservation_id":"RESERVATION_ID"}
                                                                                                                                              
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ sudo docker-compose start payments
[+] Running 1/1
 ✔ Container app-payments-1  Started
```     

### Task 3 — GitHub Community Engagement
#### GitHub Community
Starring repositories helps open-source projects by showing community interest and appreciation. A higher number of stars increases a project's visibility and can attract more contributors and users.\
Following developers helps you stay updated on their work, learn from their contributions, and discover useful projects. In team environments, it also makes collaboration easier by helping you track teammates' activity and professional growth.
