# Lab 1 — SRE Philosophy: Deploy, Break, Understand

## Task 1 — Deploy & Break QuickTicket

### 1. docker compose ps (all 5 services running)
```
[ustkost@prime app]$ docker compose ps
NAME             IMAGE                COMMAND                  SERVICE    CREATED          STATUS                    PORTS
app-events-1     app-events           "uvicorn main:app --…"   events     41 minutes ago   Up 22 minutes             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    41 minutes ago   Up 41 minutes             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   41 minutes ago   Up 21 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      41 minutes ago   Up 22 minutes (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```
### 2. Critical path: list → reserve → pay
```
[ustkost@prime app]$ curl -s http://localhost:3080/events | python3 -m json.tool
[
    {
        "id": 1,
        "name": "Go Conference 2026",
        "venue": "Main Hall A",
        "date": "2026-09-15T09:00:00+00:00",
        "total_tickets": 100,
        "price_cents": 5000,
        "available": 97
    },
    ...
]
[ustkost@prime app]$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity": 1}' | python3 -m json.tool
{
    "reservation_id": "67088120-6878-4cc6-8cdb-b86a397e7d4f",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}
[ustkost@prime app]$ curl -s -X POST http://localhost:3080/reserve/67088120-6878-4cc6-8cdb-b86a397e7d4f/pay | python3 -m json.tool
{
    "order_id": "67088120-6878-4cc6-8cdb-b86a397e7d4f",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "status": "confirmed"
}
```
### 3. Health check (everything healthy)
```
[ustkost@prime app]$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```
### 4. Dependency map
```
client -> gateway
gateway -> events -> postgres
gateway -> events -> redis
gateway -> payments
gateway -> events
```
### 5. Failure table


| Component Killed | Events List | Reserve | Pay | Health Check | User Impact |
|-----------------|-------------|---------|-----|--------------|-------------|
| payments        | Avail | Avail | Unavail | degraded, payments down | user can browse and reserve but cant pay |
| events          | Unavail | Unavail | Unavail | degraded, events down | system unavailable for users |
| redis           | Unavail | Unavail | Unavail | degraded, events down | system unavailable for users |
| postgres        | Unavail | Unavail | Unavail | degraded, events degraded | system  unavailable for users |


### 6. Load generator output (payments killed mid-run)
```
[ustkost@prime SRE-Intro]$ ./app/loadgen/run.sh 5 30
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=42 success=42 fail=0 error_rate=0%
[10s] requests=43 success=43 fail=0 error_rate=0%
[10s] requests=44 success=44 fail=0 error_rate=0%
[10s] requests=45 success=45 fail=0 error_rate=0%
[20s] requests=84 success=82 fail=2 error_rate=2.3%
[20s] requests=85 success=82 fail=3 error_rate=3.5%
[20s] requests=86 success=83 fail=3 error_rate=3.4%
---
Done. total=122 success=115 fail=7 error_rate=5.7%
[ustkost@prime SRE-Intro]$ ./app/loadgen/run.sh 5 30
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=43 success=42 fail=1 error_rate=2.3%
[10s] requests=44 success=43 fail=1 error_rate=2.2%
[10s] requests=45 success=44 fail=1 error_rate=2.2%
[10s] requests=46 success=45 fail=1 error_rate=2.1%
[10s] requests=47 success=46 fail=1 error_rate=2.1%
[20s] requests=82 success=76 fail=6 error_rate=7.3%
[20s] requests=83 success=76 fail=7 error_rate=8.4%
---
Done. total=115 success=101 fail=14 error_rate=12.1%
```
`error_rate` only reached 12.1% since the load generation script sends only a small part of requests to the stopped payments service, while other services still process requests successfully.

## Task 2 — Graceful Degradation

### Diff of gateway change
```
diff --git a/app/gateway/main.py b/app/gateway/main.py
index c86db33..56a1541 100644
--- a/app/gateway/main.py
+++ b/app/gateway/main.py
@@ -331,14 +331,38 @@ async def pay_reservation(reservation_id: str):
         payment_ref = pay_resp.json().get("payment_ref", "unknown")
     except CircuitOpenError:
         log.error("circuit open, skipping payments call")
-        raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
+        return JSONResponse(
+            status_code=503,
+            content={
+                "error": "payments_unavailable",
+                "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.",
+                "reservation_id": reservation_id,
+            },
+        )
+    except httpx.ConnectError:
+        log.error(f"payments unreachable for reservation {reservation_id}")
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
         raise HTTPException(e.response.status_code, "Payment failed")
     except Exception as e:
         log.error(f"payment error: {e}")
-        raise HTTPException(502, "Payment service unavailable")
+        return JSONResponse(
+            status_code=503,
+            content={
+                "error": "payments_unavailable",
+                "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.",
+                "reservation_id": reservation_id,
+            },
+        )
 
     # 2. Confirm reservation in events.
```

### Verification
```
[ustkost@prime app]$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" -d '{"quantity": 1}'

{"reservation_id":"4d9d345a-82c7-49f7-97e1-46e877424813","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}

[ustkost@prime app]$ curl -s -X POST http://localhost:3080/reserve/4d9d345a-82c7-49f7-97e1-46e877424813/pay

{"error":"payments_unavailable","message":"Payment service is temporarily down. Your reservation is held — try again in a few minutes.","reservation_id":"4d9d345a-82c7-49f7-97e1-46e877424813"}
```
## Task 3 — GitHub Community Engagement
```
1. Starred the course repository
2. Starred simple-container-com/api
3. Followed @Cre-eD, @Naghme98, @pierrepicaud
4. Followed 3+ classmates
```
### **Why stars matter:** Firstly, it supports the project developers and shows them that people actually value their project. Also it helps the project gain popularity. And people can see the projects you star so they can show your interests.

### **Why following matters:** Following is a convenient way to track activity of other people, see the new projects they create and build connections with them.
