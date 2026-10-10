# Task 1

## 1. Docker Compose Status

### Command

```bash
docker compose ps
```

### Output

```shell
app-events-1     app-events           "uvicorn main:app --…"   events     15 seconds ago   Up 8 seconds              0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    15 seconds ago   Up 8 seconds              0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-payments-1   app-payments         "uvicorn main:app --…"   payments   15 seconds ago   Up 14 seconds             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   15 seconds ago   Up 14 seconds (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      15 seconds ago   Up 14 seconds (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

---

## 2. Critical Path Verification

### 2.1 List Events

#### Command

```bash
curl -s http://localhost:3080/events | python3 -m json.tool
```

#### Output

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

### 2.2 Reserve Ticket

#### Command

```bash
curl -s -X POST http://localhost:3080/events/1/reserve \
-H "Content-Type: application/json" \
-d '{"quantity": 1}' | python3 -m json.tool
```

#### Output

```json
{
    "reservation_id": "e90ebff7-a8f6-429d-b2d5-c65399c0ddc2",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}
```

### 2.3 Pay Reservation

#### Command

```bash
curl -s -X POST http://localhost:3080/reserve/RESERVATION_ID/pay | python3 -m json.tool
```

#### Output

```json
{
    "order_id": "e90ebff7-a8f6-429d-b2d5-c65399c0ddc2",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "status": "confirmed"
}
```

---

## 3. Health Check (Healthy State)

### Command

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
```

### Output

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

---

## 4. Dependency Map

```text
gateway → events → postgres
gateway → events → redis
gateway → payments
```

---

## 5. Failure Exploration

For each component, I stopped one container and tested the main user-facing endpoints:

* `GET /events`
* `POST /events/1/reserve`
* `POST /reserve/{reservation_id}/pay`
* `GET /health`

---

### 5.1 Payments Stopped

#### Stop command

```bash
docker compose stop payments
```


Output:

```bash
[+] Stopping 1/1
 ✔ Container app-payments-1  Stopped     
 ```
#### Events List

Command:

```bash
curl -s http://localhost:3080/events | python3 -m json.tool
```

Output:

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

Result:

```text
Works / Fails
```

#### Reserve Ticket

Command:

```bash
curl -s -X POST http://localhost:3080/events/1/reserve \
-H "Content-Type: application/json" \
-d '{"quantity": 1}' | python3 -m json.tool
```

Output:

```json
{
    "reservation_id": "e2b07aaa-5670-4b1f-b1f0-75e828aaa1a7",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}
```

Result:

```text
Works
```

#### Pay Reservation

Command:

```bash
curl -s -X POST http://localhost:3080/reserve/RESERVATION_ID/pay | python3 -m json.tool
```

Output:

```json
{
    "detail": "Payment service timeout"
}
```

Result:

```text
Fails
```

#### Health Check

Command:

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
```

Output:

```json
{
    "status": "degraded",
    "checks": {
        "events": "ok",
        "payments": "down",
        "circuit_payments": "CLOSED"
    }
}
```

Result:

```text
Degraded
```

#### User Impact

```text
When payments is stopped, users can still list events and create reservations, but they cannot complete payment
```

#### Restart command

```bash
docker compose start payments
```

Output:
```bash
[+] Running 1/1
 ✔ Container app-payments-1  Started 
```

---

### 5.2 Events Stopped

#### Stop command

```bash
docker compose stop events
```

Output:

```bash
[+] Stopping 1/1
 ✔ Container app-events-1  Stopped  
```

#### Events List

Command:

```bash
curl -s http://localhost:3080/events | python3 -m json.tool
```

Output:

```json
{
    "detail": "Events service unavailable"
}
```

Result:

```text
Fails
```

#### Reserve Ticket

Command:

```bash
curl -s -X POST http://localhost:3080/events/1/reserve \
-H "Content-Type: application/json" \
-d '{"quantity": 1}' | python3 -m json.tool
```

Output:

```json
{
    "detail": "Events service timeout"
}
```

Result:

```text
Fails
```

#### Pay Reservation

Command:

```bash
curl -s -X POST http://localhost:3080/reserve/RESERVATION_ID/pay | python3 -m json.tool
```

Output:

```json
{
    "detail": "Payment succeeded but confirmation failed \u2014 contact support"
}
```

Result:

```text
Fails
```

#### Health Check

Command:

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
```

Output:

```json
{
    "status": "degraded",
    "checks": {
        "events": "down",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

Result:

```text
Degraded
```

#### User Impact

```text
When events is stopped, users cannot list events or create reservations. Payment also cannot complete because the gateway needs events to confirm the reservation after charging.
```

#### Restart command

```bash
docker compose start events
```

Output:
```bash
[+] Running 3/3
 ✔ Container app-postgres-1  Healthy                                                                                                                                                                 0.5s 
 ✔ Container app-redis-1     Healthy                                                                                                                                                                 0.5s 
 ✔ Container app-events-1    Started  
```
---

### 5.3 Redis Stopped

#### Stop command

```bash
docker compose stop redis
```

Output:
```
[+] Stopping 1/1
Container app-redis-1  Stopped   
```



#### Events List

Command:

```bash
curl -s http://localhost:3080/events | python3 -m json.tool
```

Output:

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

Result:

```text
Works
```

#### Reserve Ticket

Command:

```bash
curl -s -X POST http://localhost:3080/events/1/reserve \
-H "Content-Type: application/json" \
-d '{"quantity": 1}' | python3 -m json.tool
```

Output:

```json
{
    "detail": "Events service timeout"
}
```

Result:

```text
Fails
```

#### Pay Reservation

Command:

```bash
curl -s -X POST http://localhost:3080/reserve/RESERVATION_ID/pay | python3 -m json.tool
```

Output:

```json
{
    "detail": "Payment succeeded but confirmation failed \u2014 contact support"
}
```

Result:

```text
 Fails
```

#### Health Check

Command:

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
```

Output:

```json
{
    "status": "degraded",
    "checks": {
        "events": "down",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

Result:

```text
Degraded
```

#### User Impact

```text
When Redis is stopped, event listing still works because it depends on Postgres. Other parts fails
```

#### Restart command

```bash
docker compose start redis
```

Output:
```bash
[+] Running 1/1
 ✔ Container app-redis-1  Started  
 ```

---

### 5.4 Postgres Stopped

#### Stop command

```bash
docker compose stop postgres
```

Output:
```bash
[+] Stopping 1/1
 ✔ Container app-postgres-1  Stopped    
```
#### Events List

Command:

```bash
curl -s http://localhost:3080/events | python3 -m json.tool
```

Output:

```json
{
    "detail": "Events service unavailable"
}
```

Result:

```text
Fails
```

#### Reserve Ticket

Command:

```bash
curl -s -X POST http://localhost:3080/events/1/reserve \
-H "Content-Type: application/json" \
-d '{"quantity": 1}' | python3 -m json.tool
```

Output:

```json
{
    "detail": "Events service timeout"
}
```

Result:

```text
Fails
```

#### Pay Reservation

Command:

```bash
curl -s -X POST http://localhost:3080/reserve/RESERVATION_ID/pay | python3 -m json.tool
```

Output:

```json
{
    "detail": "Payment succeeded but confirmation failed \u2014 contact support"
}
```

Result:

```text
Fails
```

#### Health Check

Command:

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
```

Output:

```json
{
    "status": "degraded",
    "checks": {
        "events": "degraded",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

Result:

```text
Degraded
```

#### User Impact

```text
When Postgres is stopped, the events service cannot read event data or write confirmed orders. Listing events, reservations, and payment confirmation fail.
```

#### Restart command

```bash
docker compose start postgres
```

---

## 6. Failure Summary Table

| Component Killed | Events List | Reserve         | Pay   | Health Check | User Impact                                                                                          |
| ---------------- | ----------- | --------------- | ----- | ------------ | ---------------------------------------------------------------------------------------------------- |
| payments         | Works       | Works           | Fails | Degraded     | Users can browse and reserve tickets, but cannot pay                                                |
| events           | Fails       | Fails           | Fails | Degraded     | Users cannot browse events, reserve tickets, or complete orders                                     |
| redis            | Works       | Fails | Fails | Degraded     | Users can browse events, but other services fails |
| postgres         | Fails       | Fails           | Fails | Degraded     | Event and order data are unavailable                                                                |

---

## 7. Load Generator Test

### Start load generator

Command:

```bash
./app/loadgen/run.sh 5 30
```

Output before stopping payments:

```text
[10s] requests=39 success=39 fail=0 error_rate=0%
[10s] requests=40 success=40 fail=0 error_rate=0%
[10s] requests=41 success=41 fail=0 error_rate=0%
[10s] requests=42 success=42 fail=0 error_rate=0%
```

### Stop payments during load

Command:

```bash
docker compose stop payments
```

Output after stopping payments:

```text
[20s] requests=59 success=56 fail=3 error_rate=5.0%
[20s] requests=60 success=57 fail=3 error_rate=5.0%
[20s] requests=61 success=57 fail=4 error_rate=6.5%
[20s] requests=62 success=58 fail=4 error_rate=6.4%
```

### Observation

```text
Before the payments service was stopped, all requests completed successfully with a 0% error rate. After stopping the payments service, payment-related requests started failing and the error rate increased from 0% to approximately 6%
```

## Task 2 — Graceful Degradation

### Gateway Change

I updated the gateway payment endpoint so that when the payments service is unavailable, the gateway returns a clear `503` response instead of a generic `502`.

### Diff

Command:

```bash
git diff app/gateway/main.py
```

Output:

```diff
index c86db33..432bcd7 100644
--- a/app/gateway/main.py
+++ b/app/gateway/main.py
@@ -331,14 +331,16 @@ async def pay_reservation(reservation_id: str):
         payment_ref = pay_resp.json().get("payment_ref", "unknown")
     except CircuitOpenError:
         log.error("circuit open, skipping payments call")
-        raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
+        return payments_unavailable_response(reservation_id)
     except httpx.TimeoutException:
-        raise HTTPException(504, "Payment service timeout")
+        log.error("payment service timeout")
+        return payments_unavailable_response(reservation_id)
     except httpx.HTTPStatusError as e:
-        raise HTTPException(e.response.status_code, "Payment failed")
+        log.error(f"payment failed: {e}")
+        return payments_unavailable_response(reservation_id)
     except Exception as e:
         log.error(f"payment error: {e}")
-        raise HTTPException(502, "Payment service unavailable")
+        return payments_unavailable_response(reservation_id)
 
     # 2. Confirm reservation in events.
     try:
:
diff --git a/app/gateway/main.py b/app/gateway/main.py
index c86db33..432bcd7 100644
--- a/app/gateway/main.py
+++ b/app/gateway/main.py
@@ -331,14 +331,16 @@ async def pay_reservation(reservation_id: str):
         payment_ref = pay_resp.json().get("payment_ref", "unknown")
     except CircuitOpenError:
         log.error("circuit open, skipping payments call")
-        raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
+        return payments_unavailable_response(reservation_id)
     except httpx.TimeoutException:
-        raise HTTPException(504, "Payment service timeout")
+        log.error("payment service timeout")
+        return payments_unavailable_response(reservation_id)
     except httpx.HTTPStatusError as e:
-        raise HTTPException(e.response.status_code, "Payment failed")
+        log.error(f"payment failed: {e}")
+        return payments_unavailable_response(reservation_id)
     except Exception as e:
         log.error(f"payment error: {e}")
-        raise HTTPException(502, "Payment service unavailable")
+        return payments_unavailable_response(reservation_id)
 
     # 2. Confirm reservation in events.
     try:
@@ -356,3 +358,13 @@ async def pay_reservation(reservation_id: str):
     asyncio.create_task(_notify_order_confirmed(reservation_id))
 
     return result
+
+def payments_unavailable_response(reservation_id: str):
+    return JSONResponse(
+        status_code=503,
+        content={
+            "error": "payments_unavailable",
+            "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.",
+            "reservation_id": reservation_id,
+        },
+    )
```

---

### Verification: Payments Stopped

Command:

```bash
docker compose stop payments
```

Output:
```bash
[+] Stopping 1/1
 ✔ Container app-payments-1  Stopped     
 ```
---

### Reserve Still Works

Command:

```bash
curl -s -X POST http://localhost:3080/events/1/reserve \
-H "Content-Type: application/json" \
-d '{"quantity": 1}' | python3 -m json.tool
```

Output:

```json
{
    "reservation_id": "38723467-05c3-439b-81f7-c1f25eef0f81",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}
```

Result:

```text
Reservation still works while payments is down
```

---

### Pay Returns Clear 503

Command:

```bash
curl -s -X POST http://localhost:3080/reserve/38723467-05c3-439b-81f7-c1f25eef0f81/pay | python3 -m json.tool
```

Output:

```json
HTTP/1.1 503 Service Unavailable
date: Thu, 11 Jun 2026 12:53:49 GMT
server: uvicorn
content-length: 194
content-type: application/json
{
    "error": "payments_unavailable",
    "message": "Payment service is temporarily down. Your reservation is held \u2014 try again in a few minutes.",
    "reservation_id": "38723467-05c3-439b-81f7-c1f25eef0f81"
}
```

Result:

```text
The payment endpoint returns a clear 503 response with an actionable message instead of a generic 502
```

---

### Restore Payments

Command:

```bash
docker compose start payments
```

---

## Task 3 — GitHub Community

I starred the course repository and the `simple-container-com/api` repository. I also followed the professor, TAs, and at least three classmates on GitHub

In open source projects, it's important to star repositories, so this helps increase the visibility of useful projects and makes them easier to find later. Following developers promotes collaboration, discovery of related work, and learning from the experience of colleagues and supporting projects

---

## Bonus Task — Resource Usage Under Load

### B.1 Baseline: Idle

Command:

```bash
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}"
```

Output:

```text
NAME             CPU %     MEM USAGE / LIMIT    NET I/O           PIDS
app-gateway-1    0.27%     38.12MiB / 7.62GiB   8.22kB / 7.57kB   2
app-events-1     0.27%     40.82MiB / 7.62GiB   9.04kB / 6.85kB   2
app-postgres-1   4.56%     38.34MiB / 7.62GiB   7.29kB / 3.26kB   8
app-redis-1      4.87%     3.605MiB / 7.62GiB   6.19kB / 1.32kB   6
app-payments-1   0.31%     32.92MiB / 7.62GiB   1.06kB / 264B     1
```

---

### B.2 Under Load

Load generator command:

```bash
./app/loadgen/run.sh 10 30
```

Docker stats command while load is running:

```bash
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}"
```

Load generator output:

```text
Target: http://localhost:3080 | RPS: 10 | Duration: 30s
---
[10s] requests=66 success=66 fail=0 error_rate=0%
[10s] requests=67 success=67 fail=0 error_rate=0%
[10s] requests=68 success=68 fail=0 error_rate=0%
[10s] requests=69 success=69 fail=0 error_rate=0%
[10s] requests=70 success=70 fail=0 error_rate=0%
[10s] requests=71 success=71 fail=0 error_rate=0%
[10s] requests=72 success=72 fail=0 error_rate=0%
[20s] requests=132 success=132 fail=0 error_rate=0%
[20s] requests=133 success=133 fail=0 error_rate=0%
[20s] requests=134 success=134 fail=0 error_rate=0%
[20s] requests=135 success=135 fail=0 error_rate=0%
[20s] requests=136 success=136 fail=0 error_rate=0%
[20s] requests=137 success=137 fail=0 error_rate=0%
---
Done. total=197 success=197 fail=0 error_rate=0%
```

Docker stats output:

```text
NAME             CPU %     MEM USAGE / LIMIT    NET I/O           PIDS
app-gateway-1    0.29%     38.57MiB / 7.62GiB   305kB / 297kB     2
app-events-1     0.30%     41.26MiB / 7.62GiB   268kB / 361kB     2
app-postgres-1   5.63%     38.89MiB / 7.62GiB   153kB / 170kB     8
app-redis-1      0.97%     3.629MiB / 7.62GiB   48.1kB / 18.3kB   6
app-payments-1   0.26%     33.7MiB / 7.62GiB    9.93kB / 6.42kB   2
```

---

### B.3 Under Stress with Fault Injection

Enable payment failures and latency:

```bash
docker compose stop payments
PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500 docker compose up -d payments
```

Run load generator:

```bash
./app/loadgen/run.sh 10 30
```

Capture stats:

```bash
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}"
```

Load generator output:

```text
Target: http://localhost:3080 | RPS: 10 | Duration: 30s
---
[10s] requests=56 success=56 fail=0 error_rate=0%
[10s] requests=57 success=57 fail=0 error_rate=0%
[10s] requests=58 success=57 fail=1 error_rate=1.7%
[10s] requests=59 success=58 fail=1 error_rate=1.6%
[10s] requests=60 success=59 fail=1 error_rate=1.6%
[10s] requests=61 success=60 fail=1 error_rate=1.6%
[20s] requests=117 success=115 fail=2 error_rate=1.7%
[20s] requests=118 success=115 fail=3 error_rate=2.5%
[20s] requests=119 success=116 fail=3 error_rate=2.5%
[20s] requests=120 success=117 fail=3 error_rate=2.5%
[20s] requests=121 success=118 fail=3 error_rate=2.4%
---
Done. total=160 success=151 fail=9 error_rate=5.6%
```

Docker stats output:

```text
NAME             CPU %     MEM USAGE / LIMIT    NET I/O           PIDS
app-payments-1   0.30%     34.95MiB / 7.62GiB   12.2kB / 9.43kB   2
app-gateway-1    5.92%     38.65MiB / 7.62GiB   651kB / 644kB     2
app-events-1     4.00%     41.34MiB / 7.62GiB   570kB / 762kB     2
app-postgres-1   1.16%     38.64MiB / 7.62GiB   316kB / 361kB     8
app-redis-1      1.19%     3.387MiB / 7.62GiB   90.5kB / 35.4kB   6
```

Restore normal payments:

```bash
docker compose stop payments
PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0 docker compose up -d payments
```

---

### Resource Usage Analysis

The service using the most memory at idle was:

```text
app-events-1
```

The service using the most memory under load was:

```text
app-events-1
```

The service using the most CPU under load was:

```text
app-postgres-1
```

Reason:

```text
The events service used the most memory because it maintains application state, database connections, and reservation-related data structures. Under load, PostgreSQL used the most CPU because every reservation and event lookup requires database queries and transaction processing.
```

Fault injection in payments affected the gateway because slow payments made the gateway hold requests open for longer. This increased the amount of in-flight work and changed the gateway resource usage during the test.
