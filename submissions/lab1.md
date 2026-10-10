# Lab 1 Report — SRE Philosophy

**Student:** Valerii Tiniakov (B24-SD-03)

---

## Task 1 — Deploy & Break QuickTicket (6 pts)

### 1.1 & 1.2: System Launch and Health Check

**Command:** 
```
cd app/
docker compose up --build -d
docker compose ps`
```
```text
NAME             IMAGE                COMMAND                  SERVICE    CREATED        STATUS                    PORTS
app-events-1     app-events           "uvicorn main:app --…"   events     18 seconds ago   Up 11 seconds             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    18 seconds ago   Up 11 seconds             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-payments-1   app-payments         "uvicorn main:app --…"   payments   18 seconds ago   Up 17 seconds             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   18 seconds ago   Up 17 seconds (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      18 seconds ago   Up 17 seconds (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```
**Explanation:** Containers started successfully. Gateway, events, payments, postgres, and redis are operating normally.

**Command:** Full critical path (list → reserve → pay)
```text
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro/app (feature/lab1)
$ curl -s http://localhost:3080/events | python3 -m json.tool
[{"id":1,"name":"Go Conference 2026","venue":"Main Hall A","date":"2026-09-15T09:00:00+00:00","total_tickets":100,"price_cents":5000,"available":100},{"id":4,"name":"Python Workshop","venue":"Lab 301","date":"2026-09-22T14:00:00+00:00","total_tickets":25,"price_cents":2000,"available":25},{"id":2,"name":"SRE Meetup","venue":"Room 204","date":"2026-10-01T18:00:00+00:00","total_tickets":30,"price_cents":0,"available":30},{"id":5,"name":"Kubernetes Deep Dive","venue":"Auditorium B","date":"2026-10-10T10:00:00+00:00","total_tickets":80,"price_cents":8000,"available":80},{"id":3,"name":"Cloud Native Summit","venue":"Expo Center","date":"2026-11-20T10:00:00+00:00","total_tickets":500,"price_cents":15000,"available":500}]
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro/app (feature/lab1)
$ccurl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity": 1}'
{"reservation_id":"7627ff65-4247-44f3-9aa5-a5d38c106e37","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro/app (feature/lab1)
cucurl -s -X POST http://localhost:3080/reserve/7627ff65-4247-44f3-9aa5-a5d38c106e37/pay
{"order_id":"7627ff65-4247-44f3-9aa5-a5d38c106e37","event_id":1,"quantity":1,"total_cents":5000,"status":"confirmed"}

```
**Explanation:** The critical path runs successfully. The event list is returned, ticket reservation creates an ID, and payment for this ID processes without errors.

**Command:** System health check `curl -s http://localhost:3080/health | python3 -m json.tool`
```text
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro/app (feature/lab1)
$ curl -s http://localhost:3080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```
**Explanation:** The health endpoint confirms that all internal system dependencies are available.

### 1.3: Dependency Map

```text
gateway → events → postgres
gateway → events → redis
gateway → payments
```
**Explanation:** `gateway` routes requests, calling `events` for reservations and `payments` for checkout. The `events` service directly depends on `postgres` (data storage) and `redis` (queues/cache).

### 1.4: Systematic Failure Exploration (Failure Table)

| Component Killed | Events List | Reserve | Pay | Health Check | User Impact |
|-----------------|-------------|---------|-----|--------------|-------------|
| payments        |   Works     |  Works  | Fails (timeout)    |      Degraded (payments down)        |      Can browse and reserve, cannot complete checkout       |
| events          | Fails | Fails | Fails (Payment OK, Confirm fails) | Degraded (events down) | Complete outage. Cannot view, reserve, or safely pay. |
| redis           | Works | Fails (Timeout) | Fails (Payment OK, Confirm fails) | Degraded (events down) | Can list events, but reservations hang and payments fail to confirm. |
| postgres        | Fails (Unavailable) | Fails (500 Error) | Fails (Payment OK, Confirm fails) | Degraded (events down) | Complete outage. Cannot view events, reserve tickets, or confirm payments. |

**Explanation:** Disconnecting various components leads to cascading failures. (Add 1-2 sentences here about which component takes the system down the hardest).

### 1.5: Load Generator and Payments Failure

**Command:** `./app/loadgen/run.sh 5 30` while `payments` is down
```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=45 success=45 fail=0 error_rate=0%
[10s] requests=46 success=46 fail=0 error_rate=0%
[10s] requests=47 success=47 fail=0 error_rate=0%
[10s] requests=48 success=48 fail=0 error_rate=0%
[20s] requests=67 success=66 fail=1 error_rate=1.4%
[20s] requests=68 success=67 fail=1 error_rate=1.4%
[20s] requests=69 success=68 fail=1 error_rate=1.4%
[20s] requests=70 success=69 fail=1 error_rate=1.4%
[20s] requests=71 success=70 fail=1 error_rate=1.4%
---
Done. total=105 success=102 fail=3 error_rate=2.8%


```
**Explanation:** When the payments service goes down under load, we see a sharp spike in the number of errors in the gateway responses.

---

## Task 2 — Graceful Degradation (3 pts)

### 1.7 & 1.8: Implementation and Verification of Degradation

**Command:** Code changes `git diff app/gateway/main.py`
```diff
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab1)
$ git diff app/gateway/main.py
diff --git a/app/gateway/main.py b/app/gateway/main.py
index c86db33..a3e7fc6 100644
--- a/app/gateway/main.py
+++ b/app/gateway/main.py
@@ -313,11 +313,6 @@ async def _notify_order_confirmed(reservation_id: str):
 @app.post("/reserve/{reservation_id}/pay")
 async def pay_reservation(reservation_id: str):
     # 1. Call payments — wrapped in circuit breaker + retry.
-    #
-    # Composition order matters: cb.call(retry(_charge)) means each CB-tracked
-    # invocation includes its retries internally; the CB only sees the FINAL
-    # outcome. The reverse — retry(cb.call(_charge)) — would retry past the
-    # CircuitOpenError, defeating the fast-fail. See lab 11 §11.4.
     async def _charge():
         resp = await client.post(
             f"{PAYMENTS_URL}/charge",
@@ -332,8 +327,36 @@ async def pay_reservation(reservation_id: str):
     except CircuitOpenError:
         log.error("circuit open, skipping payments call")
         raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
-    except httpx.TimeoutException:
-        raise HTTPException(504, "Payment service timeout")
+    except httpx.RequestError:  # <--- СТАВИМ УНИВЕРСАЛЬНЫЙ ПЕРЕХВАТЧИК
+        log.error("payments network error, graceful degradation triggered")
+        return JSONResponse(
+            status_code=503,
+            content={
+                "error": "payments_unavailable",
+                "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.",
+                "reservation_id": reservation_id
+            }
+        )
+    except httpx.HTTPStatusError as e:
+        raise HTTPException(e.response.status_code, "Payment failed")
+    try:
+        pay_resp = await payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
+        payment_ref = pay_resp.json().get("payment_ref", "unknown")
+    except CircuitOpenError:
+        log.error("circuit open, skipping payments call")
+        raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
+    except (httpx.ConnectError, httpx.TimeoutException):
+        log.error("payments down or timed out, graceful degradation triggered")
+        return JSONResponse(
+            status_code=503,
+            content={
+                "error": "payments_unavailable",
+                "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.",
+                "reservation_id": reservation_id
+            }
+        )
+    except httpx.HTTPStatusError as e:
+        raise HTTPException(e.response.status_code, "Payment failed")
     except httpx.HTTPStatusError as e:
         raise HTTPException(e.response.status_code, "Payment failed")
     except Exception as e:


```
**Explanation:** We catch the connection error to the payments service and return a clear 503 status instead of the standard 502, so the frontend/user understands that the reservation is saved and payment can be retried.

**Command:** Testing reserve and pay while `payments` is stopped
```text
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro/app (feature/lab1)
$ docker compose stop payments
[+] Stopping 1/1
 ✔ Container app-payments-1  Stopped                                       0.5s

valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro/app (feature/lab1)
$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" -d '{"quantity": 1}'
{"reservation_id":"7a38731c-7106-4fca-86d7-664034fff05b","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro/app (feature/lab1)
$ ^C

valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro/app (feature/lab1)
$ curl -s -X POST http://localhost:3080/reserve/7a38731c-7106-4fca-86d7-664034fff05b/pay
{"error":"payments_unavailable","message":"Payment service is temporarily down. Your reservation is held — try again in a few minutes.","reservation_id":"7a38731c-7106-4fca-86d7-664034fff05b"}
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro/app (feature/lab1)
$


```
**Explanation:** Now, if `payments` goes down, users can still book tickets (reserve works), and when attempting to pay, they receive a readable message about temporary unavailability.

---

## Task 3 — GitHub Community Engagement

Starring repositories is essential in open source because it helps developers bookmark useful tools, boosts project visibility, and signals community trust to potential contributors. Following other developers fosters professional growth and team collaboration by keeping you updated on your peers' work, helping you discover new technologies through their activity, and building a strong network beyond the classroom.

