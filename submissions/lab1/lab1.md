# Lab 1 — SRE Philosophy: Deploy, Break, Understand

## Task 1 — Deploy & Break QuickTicket

### 1. Docker Compose status

All 5 required services are running: `gateway`, `events`, `payments`, `postgres`, `redis`.

```bash
$ sudo docker ps
CONTAINER ID   IMAGE                STATUS                    PORTS                                         NAMES
20b0673728b0   app-gateway          Up 6 seconds              0.0.0.0:3080->8080/tcp                       app-gateway-1
719d0b957029   app-events           Up 7 seconds              0.0.0.0:8081->8081/tcp                       app-events-1
9b280c15218c   postgres:17-alpine   Up 12 seconds (healthy)                                                 app-postgres-1
f66787d1c6fc   app-payments         Up 8 minutes              0.0.0.0:8082->8082/tcp                       app-payments-1
4cf19048fb61   redis:7-alpine       Up 12 seconds (healthy)                                                 app-redis-1
```

---

### 2. Critical path: list → reserve → pay

#### List events

```bash
$ curl -s http://localhost:3080/events | python3 -m json.tool
{
    "detail": "Events service unavailable"
}
```

#### Reserve ticket

```bash
$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity": 1}' | python3 -m json.tool

Expecting value: line 1 column 1 (char 0)
```

#### Pay reservation

```bash
$ curl -s -X POST http://localhost:3080/reserve/RESERVATION_ID_HERE/pay | python3 -m json.tool
{
    "detail": "Payment succeeded but confirmation failed — contact support"
}
```

#### Health check

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

---

### 3. Dependency map

```text
client
  |
  v
gateway
  |------------------> events
  |                      |
  |                      |--> postgres
  |                      |
  |                      |--> redis
  |
  |------------------> payments
```

If a dependency is unavailable, the gateway returns an error response.
For example, when a required service is down, the user receives a `502` or a service-specific error message.

---

### 4. Failure exploration

#### Payments service stopped

```bash
$ sudo docker-compose stop payments
[+] stop 1/1
 ✔ Container app-payments-1 Stopped
```

#### List events

```bash
$ curl -s http://localhost:3080/events | python3 -m json.tool
{
    "detail": "Events service unavailable"
}
```

#### Reserve ticket

```bash
$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity": 1}'

Internal Server Error
```

#### Health check

```bash
$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "degraded",
    "checks": {
        "events": "degraded",
        "payments": "down",
        "circuit_payments": "CLOSED"
    }
}
```

#### Payments service restored

```bash
$ sudo docker-compose start payments
[+] start 1/1
 ✔ Container app-payments-1 Started
```

---

### 5. Failure table

| Component Killed | Events List       | Reserve                            | Pay                                              | Health Check       | User Impact                                   |
| ---------------- | ----------------- | ---------------------------------- | ------------------------------------------------ | ------------------ | --------------------------------------------- |
| payments         | Failed / degraded | Internal Server Error              | Payment unavailable                              | `payments: down`   | User cannot complete payment                  |
| events           | Failed            | Failed                             | Not useful without reservation                   | `events: degraded` | User cannot list events or reserve tickets    |
| redis            | Degraded          | May fail or lose reservation state | May fail because reservation data is unavailable | degraded           | Reservation flow becomes unreliable           |
| postgres         | Failed            | Failed                             | Not useful without event/reservation data        | degraded           | Events and reservations cannot work correctly |

---

### 6. Load generator output

```bash
$ ./app/loadgen/run.sh 5 30

QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=44 success=0 fail=44 error_rate=100.0%
[10s] requests=45 success=0 fail=45 error_rate=100.0%
[10s] requests=46 success=0 fail=46 error_rate=100.0%
[10s] requests=47 success=0 fail=47 error_rate=100.0%
[10s] requests=48 success=0 fail=48 error_rate=100.0%
[20s] requests=88 success=0 fail=88 error_rate=100.0%
[20s] requests=89 success=0 fail=89 error_rate=100.0%
[20s] requests=90 success=0 fail=90 error_rate=100.0%
[20s] requests=91 success=0 fail=91 error_rate=100.0%
[20s] requests=92 success=0 fail=92 error_rate=100.0%
---
Done. total=132 success=0 fail=132 error_rate=100.0%
```

The error rate reached `100%`, which means the system was fully unavailable for the tested critical path.

---

## Task 2 — Graceful Degradation

### Payments service stopped

```bash
$ sudo docker-compose stop payments
[+] stop 1/1
 ✔ Container app-payments-1 Stopped
```

### Pay request when payments is unavailable

```bash
$ curl -s -X POST http://localhost:3080/reserve/RESERVATION_ID/pay
{
    "detail": "Payment service unavailable"
}
```

This behavior is better than a generic internal server error because the user receives a clear explanation that the payment service is temporarily unavailable.

### Payments service restored

```bash
$ sudo docker-compose start payments
[+] start 1/1
 ✔ Container app-payments-1 Started
```

---

### Git diff

```diff
diff --git a/app/gateway/main.py b/app/gateway/main.py
index c86db33..6e98a4f 100644
--- a/app/gateway/main.py
+++ b/app/gateway/main.py
@@ -331,14 +331,46 @@ async def pay_reservation(reservation_id: str):
         payment_ref = pay_resp.json().get("payment_ref", "unknown")
     except CircuitOpenError:
         log.error("circuit open, skipping payments call")
-        raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
-    except httpx.TimeoutException:
+        raise HTTPException(
+            status_code=503,
+            detail="Payment service temporarily unavailable"
+        )
+    except httpx.ConnectError:
+        raise HTTPException(
+            status_code=503,
+            detail="Payment service unavailable"
+        )
+    except httpx.TimeoutException:
+        raise HTTPException(
+            status_code=503,
+            detail="Payment service timeout"
+        )
```

---

## Task 3 — GitHub Community

I starred the required repositories and followed the professor, TAs, and classmates.

Starring repositories is useful because it helps save interesting projects and also gives visibility to open-source tools. Following developers helps track what teammates and experienced engineers are working on, which can be useful for learning and future collaboration
