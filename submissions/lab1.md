# Task 1

#### 1
**docker compose ps**
`NAME             IMAGE                COMMAND                  SERVICE    CREATED         STATUS                   PORTS
`app-events-1     app-events           "uvicorn main:app --…"   events     2 minutes ago   Up 2 minutes             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
`app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    2 minutes ago   Up 2 minutes             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
`app-payments-1   app-payments         "uvicorn main:app --…"   payments   2 minutes ago   Up 2 minutes             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
`app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   2 minutes ago   Up 2 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
`app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      2 minutes ago   Up 2 minutes (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp`

#### 2

**curl -s http://localhost:3080/events | python3 -m json.tool**
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

**curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity": 1}' | python3 -m json.tool**
{
    "reservation_id": "e00c0259-d45e-41c9-bd5d-4ec2bd6853e8",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}

**curl -s -X POST http://localhost:3080/reserve/e00c0259-d45e-41c9-bd5d-4ec2bd6853e8/pay | python3 -m json.tool**
{
    "order_id": "e00c0259-d45e-41c9-bd5d-4ec2bd6853e8",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "status": "confirmed"
}


#### 3
**curl -s http://localhost:3080/health | python3 -m json.tool**
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}

#### 4
**Dependency map**
gateway → (EVENTS_URL) → events → (psycopg2) → postgres
gateway → (EVENTS_URL) → events → (redis-py) → redis
gateway → (PAYMENTS_URL) → payments


#### 5 
| Component Killed | Events List                        | Reserve                           | Pay                                | Health Check               | User Impact                                                                                                                                                            |
| ---------------- | ---------------------------------- | --------------------------------- | ---------------------------------- | -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| payments         | Works                              | Works                             | Fails(Payment service unavailable) | Degraded(payments: down)   | Users can browse events and reserve tickets, but cannot complete any purchases. Critical path is broken at the final step.                                             |
| events           | Fails(Events service unavailable)  | Fails(Events service unavailable) | Fails(Events service unavailable)  | Degraded(events: down)     | Total failure of the ticket catalog and reservation engine. If a payment is somehow forced, the order confirmation fails, leaving the system in an inconsistent state. |
| redis            | Works                              | Fails(Gateway Timeout)            | Fails(Cannot progress)             | Degraded(redis: down)      | Users can view the event catalog, but the entire reservation flow is completely blocked.                                                                               |
| postgres         | Fails (Events service unavailable) | Fails(Internal Server Error)      | Fails (Cannot progress)            | Degraded(events: degraded) | Completely paralyzes core business logic. Users can neither see the event catalog nor book tickets.                                                                    |


#### 6

Target: http://localhost:3080 | RPS: 5 | Duration: 30s

10s requests=41 success=41 fail=0 error_rate=0%
10s requests=42 success=42 fail=0 error_rate=0%
10s requests=43 success=43 fail=0 error_rate=0%
10s requests=44 success=44 fail=0 error_rate=0%
20s requests=83 success=78 fail=5 error_rate=6.0%
20s requests=84 success=79 fail=5 error_rate=5.9%
20s requests=85 success=80 fail=5 error_rate=5.8%
20s requests=86 success=81 fail=5 error_rate=5.8%

Done. total=124 success=114 fail=10 error_rate=8.0%

# Task 2

**1. git diff app/gateway/main.py**
diff --git a/app/gateway/main.py b/app/gateway/main.py
index c86db33..c8559de 100644
--- a/app/gateway/main.py
+++ b/app/gateway/main.py
@@ -336,6 +336,16 @@ async def pay_reservation(reservation_id: str):
         raise HTTPException(504, "Payment service timeout")
     except httpx.HTTPStatusError as e:
         raise HTTPException(e.response.status_code, "Payment failed")
+    except httpx.ConnectError:
+        log.warning(f"ConnectError caught for payments, returning graceful 503 for reservation {reservation_id}")
+        return JSONResponse(
+            status_code=503,
+            content={
+                "error": "payments_unavailable",
+                "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.",
+                "reservation_id": reservation_id
+            }
+        )
     except Exception as e:
         log.error(f"payment error: {e}")
         raise HTTPException(502, "Payment service unavailable")


**2.**

**curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" -d '{"quantity": 1}'**
  {"reservation_id":"af91ade2-05f8-481c-863d-b2a1741b11c1","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}

**curl -s -X POST http://localhost:3080/reserve/af91ade2-05f8-481c-863d-b2a1741b11c1/pa**
{"error":"payments_unavailable","message":"Payment service is temporarily down. Your reservation is held — try again in a few minutes.","reservation_id":"af91ade2-05f8-481c-863d-b2a1741b11c1"}

# Task 3

**Why starring repositories matters in open source:**
 Stars help bookmark interesting projects for later reference while indicating their popularity and community trust. Additionally, they appear on your profile to show your interests and encourage maintainers by increasing project visibility.

 **How following developers helps in team projects and professional growth:**
 Following others helps you see what developers are working on and discover new projects through their activity. It allows you to stay updated on classmates' work for future collaboration and build professional connections beyond the classroom.

# Bonus Task

### Stats tables:
1. **B1**
	NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
	app-gateway-1    0.21%     38.11MiB / 7.754GiB   11.9kB / 7.63kB   2
	app-payments-1   0.20%     39.06MiB / 7.754GiB   2.8kB / 126B      1
	app-events-1     0.29%     41MiB / 7.754GiB      14.3kB / 11.1kB   2
	app-postgres-1   0.01%     23.57MiB / 7.754GiB   108kB / 115kB     8
	app-redis-1      0.84%     9.336MiB / 7.754GiB   38.9kB / 13.6kB   6
2. **B2**
	NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
	app-gateway-1    6.62%     38.79MiB / 7.754GiB   529kB / 507kB     2
	app-payments-1   0.32%     39.86MiB / 7.754GiB   18.6kB / 11.1kB   2
	app-events-1     3.22%     41.7MiB / 7.754GiB    463kB / 620kB     2
	app-postgres-1   0.96%     24.25MiB / 7.754GiB   364kB / 406kB     8
	app-redis-1      5.42%     9.355MiB / 7.754GiB   103kB / 40.7kB    6
3. **B3**
	NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
	app-payments-1   0.22%     33.73MiB / 7.754GiB   10.2kB / 5.68kB   2
	app-gateway-1    4.57%     39.16MiB / 7.754GiB   949kB / 914kB     2
	app-events-1     2.39%     41.77MiB / 7.754GiB   821kB / 1.1MB     2
	app-postgres-1   0.54%     24.32MiB / 7.754GiB   564kB / 645kB     8
	app-redis-1      0.55%     3.496MiB / 7.754GiB   140kB / 56kB      6

**Which service uses the most memory? Does it change under load?** 
	The `events` service uses the most memory (~41 MiB). Its consumption remains highly stable and barely changes under load (varying by less than 1 MiB). 
**Which service uses the most CPU under load? Why?** 
	The `gateway` service uses the most CPU (6.62%) because it is the central entry point. It has to process all incoming traffic, manage routing, and handle JSON serialization for every request. 
**How does fault injection in payments affect resource usage in gateway?** 
	Introducing latency in `payments` decreases the `gateway` CPU usage (from 6.62% to 4.57%). This happens because the gateway becomes I/O-bound, spending more time holding connections open and waiting for responses rather than executing active CPU computations.

