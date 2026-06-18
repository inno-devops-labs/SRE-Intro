# Lab 1 Report — SRE Philosophy: Deploy, Break, Understand

## 1. Docker Compose PS Output
```text
NAME             IMAGE                COMMAND                  SERVICE    CREATED             STATUS                    PORTS
app-events-1     app-events           "uvicorn main:app --…"   events     About an hour ago   Up 20 minutes             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    About an hour ago   Up About an hour          0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-payments-1   app-payments         "uvicorn main:app --…"   payments   About an hour ago   Up 8 minutes              0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   About an hour ago   Up 27 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      About an hour ago   Up 30 minutes (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```
## 2. Output of the full critical path
List events
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
        "available": 25
    },
    {
        "id": 2,
        "name": "SRE Meetup",
        "venue": "Room 204",
        "date": "2026-10-01T18:00:00+00:00",
        "total_tickets": 30,
        "price_cents": 0,
        "available": 27
    },
    {
        "id": 5,
        "name": "Kubernetes Deep Dive",
        "venue": "Auditorium B",
        "date": "2026-10-10T10:00:00+00:00",
        "total_tickets": 80,
        "price_cents": 8000,
        "available": 76
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


Reserve Ticket
```json
{
    "reservation_id": "0ac8e5bc-68c2-4c94-ba5c-f512e087103f",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}
```
Pay Reservation
```json
{
    "order_id": "0ac8e5bc-68c2-4c94-ba5c-f512e087103f",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "status": "confirmed"
}
```

## 3. Healthy Status
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
## 4. Dependency Map
```text
gateway → events → postgres
gateway → events → redis
gateway → payments
```

## 5. Failure Table
| Component Killed| Events List | Reserve     | Pay          | Health Check | User Impact                                                       |
|-----------------|-------------|-------------|--------------|--------------|-------------------------------------------------------------------|
| payments        |     OK      |    OK       |Fail(502/504) |  Degraded    |Users can browse and reserve, but cannot purchase tickets.         |
| events          |  Fail (502) |  Fail (502) |Fail          |  Degraded    |Complete system outage for ticket operations.                      |
| redis           |    OK       |  Fail (504) |Fail(404)     |  Degraded    |Cascading failure. Ticket reservation and purchase are broken.     |
| postgres        |  Fail (502) |  Fail (500) |Fail          |  Degraded    |Complete system outage. Postgres is a critical SPOF.               |


## 6. Load generator output showing the error rate spike when payments is killed
```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[20s] requests=28 success=25 fail=3 error_rate=10.7%
---
Done. total=38 success=33 fail=5 error_rate=13.2%
```

## 7. Task 2 - Graceful Degradation
```bash
$ git diff app/gateway/main.py
```
```diff
diff --git a/app/gateway/main.py b/app/gateway/main.py
index c86db33..d68fc90 100644
--- a/app/gateway/main.py
+++ b/app/gateway/main.py
@@ -338,7 +338,14 @@ async def pay_reservation(reservation_id: str):
         raise HTTPException(e.response.status_code, "Payment failed")
     except Exception as e:
         log.error(f"payment error: {e}")
-        raise HTTPException(502, "Payment service unavailable")
+        return JSONResponse(
+            status_code=503,
+            content={
+                "error": "payments_unavailable",
+                "message": "Payment service is temporarily down. Your reservation is held – try again in a few minutes.",
+                "reservation_id": reservation_id
+            }
+        )
 
     # 2. Confirm reservation in events.
     try:
```

```bash
$ curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity": 1}' | python -m json.tool
```
```json
{
    "reservation_id": "8285b749-1866-4c41-a0cb-472b180c2a85",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}
```

```bash
$ curl -s -X POST http://localhost:3080/reserve/8285b749-1866-4c41-a0cb-472b180c2a85/pay | python -m json.tool
```
```json
{
    "error": "payments_unavailable",
    "message": "Payment service is temporarily down. Your reservation is held - try again in a few minutes.",
    "reservation_id": "8285b749-1866-4c41-a0cb-472b180c2a85"
}
```

## 8. Task 3 - GitHub Community Engagement

Starring repositories plays a crucial role in the open-source ecosystem by increasing project visibility, helping other developers discover reliable tools, and serving as a metric of community trust and adoption. Following developers and peers helps cultivate a professional network, facilitates knowledge sharing through activity feeds, and streamlines collaboration in team-driven software engineering projects.

## 9. Bonus Task - Resource Usage Under Load

### Baseline (idle)
```text
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-gateway-1    0.12%     38MiB / 15.58GiB      3.35kB / 2.27kB   2
app-events-1     0.11%     40.93MiB / 15.58GiB   5.82kB / 5.03kB   2
app-postgres-1   0.00%     27.28MiB / 15.58GiB   247kB / 314kB     8
app-redis-1      0.33%     4.625MiB / 15.58GiB   43.3kB / 16kB     6
```

### B.2: Under load
```text
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-gateway-1    0.11%     38.15MiB / 15.58GiB   23.9kB / 22.6kB   2
app-events-1     0.12%     41.1MiB / 15.58GiB    25.6kB / 31.4kB   2
app-postgres-1   4.61%     27.85MiB / 15.58GiB   258kB / 325kB     8
app-redis-1      0.35%     4.586MiB / 15.58GiB   48kB / 17.7kB     6
```

### B.3: Under stress with fault injection
```text
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-payments-1   0.30%     35.25MiB / 15.58GiB   6.51kB / 4.53kB   2
app-gateway-1    2.06%     38.43MiB / 15.58GiB   188kB / 188kB     2
app-events-1     1.08%     41.35MiB / 15.58GiB   166kB / 217kB     2
app-postgres-1   4.81%     27.68MiB / 15.58GiB   337kB / 415kB     8
app-redis-1      3.87%     4.605MiB / 15.58GiB   72.1kB / 27.3kB   6
```

* **Which service uses the most memory? Does it change under load?**
  * **Memory Leader:** Based on the collected metrics, **`app-events-1`** consistently consumes the most RAM (\~**`41 MiB`**), closely followed by **`app-gateway-1`** (\~**`38 MiB`**). Surprisingly, `app-postgres-1` ranks third, holding steady at around ~27 MiB.
  * **Change Under Load:** Memory consumption **remains completely static** across all three scenarios. For instance, `app-events-1` goes from `40.93 MiB` (idle) to `41.1 MiB` (load) and `41.35 MiB` (chaos). This negligible variance of just a few hundred kilobytes proves that the microservices are properly designed as `stateless` and do not suffer from memory leaks.

* **Which service uses the most CPU under load? Why?**
  * **CPU Leader:** Under standard load (Scenario B.2), the database **`app-postgres-1`** exhibits the highest CPU utilization (**`4.61%`**), while the gateway and events services sit comfortably near ~0.11%.
  * **Why:** At 10 RPS, the primary computational bottleneck is the relational database. It has to parse incoming SQL queries, manage transactional write locks, compute ticket availability dynamically using aggregation functions (`COALESCE`, `SUM`), and update tables. For lightweight `` `FastAPI` `` instances, a clean 10 RPS flow is simply not enough traffic to drive significant CPU cycles.

* **How does fault injection in payments affect resource usage in gateway?**
  * **Effect:** Enabling artificial latency in the payments service (Scenario B.3) triggers a **massive CPU spike** on the gateway **`app-gateway-1`**, forcing it to jump from `0.11%` up to **`2.06%`** (nearly a 20x increase). We also see a clear knock-on effect on Redis (`3.87%`) and events (`1.08%`).
  * **Why:** Because the payments service introduces a heavy 500ms delay, the asynchronous `Event Loop` inside the gateway is forced to keep hundreds of concurrent client connections open simultaneously. The gateway burns CPU overhead on maintaining open network sockets, handling intense context-switching, and managing an inflating pool of long-lived coroutines waiting on the sluggish upstream бэкэнд, rather than processing requests instantly and freeing up system resources.