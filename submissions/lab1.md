# Lab 1: SRE Philosophy — Deploy & Break QuickTicket

## Task 1 — Deploy & Break QuickTicket

### 1.1 docker compose ps output

```text
NAME             IMAGE                COMMAND                  SERVICE    CREATED              STATUS                        PORTS
app-events-1     app-events           "uvicorn main:app --…"   events     About a minute ago   Up 54 seconds                 0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    About a minute ago   Up 54 seconds                 0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-payments-1   app-payments         "uvicorn main:app --…"   payments   About a minute ago   Up About a minute             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   About a minute ago   Up About a minute (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      About a minute ago   Up About a minute (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

### 1.2 Critical Path Output

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
    "available": 99
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

#### Reserve ticket

```json
{
  "reservation_id": "cb810559-370a-4d5a-a00c-ba88b52402f4",
  "event_id": 1,
  "quantity": 1,
  "total_cents": 5000,
  "expires_in_seconds": 300
}
```

#### Pay for reservation

```json
{
  "order_id": "cb810559-370a-4d5a-a00c-ba88b52402f4",
  "event_id": 1,
  "quantity": 1,
  "total_cents": 5000,
  "status": "confirmed"
}
```

### 1.3 Output of `curl -s http://localhost:3080/health` when everything is healthy

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

### 1.4 Dependency map

```text
Gateway (port 3080)
   ├── Events Service (port 8081)
   │       ├── PostgreSQL (port 5432)
   │       └── Redis (port 6379)
   └── Payments Service (port 8082)
```

### 1.5 Failure table

| Component Killed | Events List         | Reserve         | Pay                 | Health Check | User Impact     |
| ---------------- | ------------------- | --------------- | ------------------- | ------------ | --------------- |
| payments         | Works               | Works           | Fails (504 Timeout) | degraded     | Cannot pay      |
| events           | Fails (Timeout)     | Fails (Timeout) | Fails               | degraded     | Complete outage |
| redis            | Works               | Fails (Timeout) | Fails               | degraded     | Cannot reserve  |
| postgres         | Fails (Unavailable) | Fails           | Fails               | degraded     | Complete outage |

### 1.6 Generator output

```text
QuickTicket Load Generator

Target: http://localhost:3080 | RPS: 5 | Duration: 30s

---

✓ Request 1: HTTP 200
✓ Request 2: HTTP 200
✓ Request 3: HTTP 200
✓ Request 4: HTTP 200
✓ Request 5: HTTP 200
✓ Request 6: HTTP 200
✓ Request 7: HTTP 200
✓ Request 8: HTTP 200
✓ Request 9: HTTP 200
✓ Request 10: HTTP 200
✓ Request 11: HTTP 200
✓ Request 12: HTTP 200
✓ Request 13: HTTP 200
✓ Request 14: HTTP 200
✓ Request 15: HTTP 200
✓ Request 16: HTTP 200
✓ Request 17: HTTP 200
✓ Request 18: HTTP 200
✓ Request 19: HTTP 200
✓ Request 20: HTTP 200
✓ Request 21: HTTP 200
✓ Request 22: HTTP 200
✓ Request 23: HTTP 200
✓ Request 24: HTTP 200
✓ Request 25: HTTP 200
✓ Request 26: HTTP 200
✓ Request 27: HTTP 200
✓ Request 28: HTTP 200
✓ Request 29: HTTP 200
✓ Request 30: HTTP 200
✓ Request 31: HTTP 200
✓ Request 32: HTTP 200
✓ Request 33: HTTP 200
✓ Request 34: HTTP 200
✓ Request 35: HTTP 200
✓ Request 36: HTTP 200
✓ Request 37: HTTP 200
✓ Request 38: HTTP 200
✓ Request 39: HTTP 200
✓ Request 40: HTTP 200
✓ Request 41: HTTP 200
✓ Request 42: HTTP 200
✓ Request 43: HTTP 200
✓ Request 44: HTTP 200
✓ Request 45: HTTP 200
✓ Request 46: HTTP 200
✗ Request 47: HTTP 504
✓ Request 48: HTTP 200
✗ Request 49: HTTP 504
✓ Request 50: HTTP 200
✗ Request 51: HTTP 504

---

Done. total=51 success=48 fail=3 error_rate=5.9%
```

## Task 2 — Graceful Degradation

### Diff of gateway change

```diff
diff --git a/app/gateway/main.py b/app/gateway/main.py
index abc123def..456ghi789 100644

--- a/app/gateway/main.py
+++ b/app/gateway/main.py

@@ -250,7 +250,15 @@ async def pay_reservation(reservation_id: str):

     except CircuitOpenError:
         log.error("circuit open, skipping payments call")
         raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
-    except httpx.TimeoutException:
-        raise HTTPException(504, "Payment service timeout")
+    except httpx.TimeoutException as e:
+        log.error(f"payment timeout: {e}")
+        raise HTTPException(
+            status_code=503,
+            detail={
+                "error": "payments_unavailable",
+                "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.",
+                "reservation_id": reservation_id
+            }
+        )

     except httpx.HTTPStatusError as e:
         raise HTTPException(e.response.status_code, "Payment failed")
     except Exception as e:
```

### Output when payments is down

#### Reserve (works)

```json
{
  "reservation_id": "425c8744-f2ee-4a52-9bd5-2d43064d80c8",
  "event_id": 1,
  "quantity": 1,
  "total_cents": 5000,
  "expires_in_seconds": 300
}
```

#### Pay (clear 503 error)

```json
{
  "detail": {
    "error": "payments_unavailable",
    "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.",
    "reservation_id": "425c8744-f2ee-4a52-9bd5-2d43064d80c8"
  }
}
```

## GitHub Community

Starring helps discover popular projects, shows support to maintainers, and makes it easy to find repositories later.

Following developers helps build professional network, learn from their work, and stay updated on interesting projects.
