### Lab1 Report


### Task 1

1. Output of `docker compose ps` showing all 5 services running
```
user@MacBook-Air app % docker compose ps
NAME             IMAGE                COMMAND                  SERVICE    CREATED         STATUS                 PORTS
app-events-1     app-events           "uvicorn main:app --…"   events     7 seconds ago   Up 5 seconds           0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    7 seconds ago   Up 5 seconds           0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-payments-1   app-payments         "uvicorn main:app --…"   payments   7 seconds ago   Up 6 seconds           0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   4 hours ago     Up 4 hours (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      4 hours ago     Up 4 hours (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```
2. Output of the full critical path (list → reserve → pay) with real data
```
user@MacBook-Air app % curl -s http://localhost:3080/events | python3 -m json.tool
[
    {
        "id": 1,
        "name": "Go Conference 2026",
        "venue": "Main Hall A",
        "date": "2026-09-15T09:00:00+00:00",
        "total_tickets": 100,
        "price_cents": 5000,
        "available": 91
    },
    {
        "id": 4,
        "name": "Python Workshop",
        "venue": "Lab 301",
        "date": "2026-09-22T14:00:00+00:00",
        "total_tickets": 25,
        "price_cents": 2000,
        "available": 22
    },
    {
        "id": 2,
        "name": "SRE Meetup",
        "venue": "Room 204",
        "date": "2026-10-01T18:00:00+00:00",
        "total_tickets": 30,
        "price_cents": 0,
        "available": 24
    },
    {
        "id": 5,
        "name": "Kubernetes Deep Dive",
        "venue": "Auditorium B",
        "date": "2026-10-10T10:00:00+00:00",
        "total_tickets": 80,
        "price_cents": 8000,
        "available": 75
    },
    {
        "id": 3,
        "name": "Cloud Native Summit",
        "venue": "Expo Center",
        "date": "2026-11-20T10:00:00+00:00",
        "total_tickets": 500,
        "price_cents": 15000,
        "available": 492
    }
]
user@MacBook-Air app % docker compose stop payments                                                          

[+] stop 1/1
 ✔ Container app-payments-1 Stopped                                                                                               0.4s
user@MacBook-Air app % curl -s -X POST http://localhost:3080/events/1/reserve \                              
  -H "Content-Type: application/json" -d '{"quantity": 1}'
{"reservation_id":"2d93c04e-a066-464a-9898-c63d7f82da62","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}%      
user@MacBook-Air app % curl -s -X POST http://localhost:3080/reserve/2d93c04e-a066-464a-9898-c63d7f82da62/pay
{"error":"payments_unavailable","message":"Payment service is temporarily down. Your reservation is held — try again in a few minutes.","reservation_id":"2d93c04e-a066-464a-9898-c63d7f82da62"}%                                                                           
user@MacBook-Air app %  

```
3. Output of `curl -s http://localhost:3080/health` when everything is healthy
```
user@MacBook-Air app % curl -s http://localhost:3080/health | python3 -m json.tool

{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```
4. A dependency map (Mermaid diagram or simple text):
   ```
   gateway → events → postgres
   gateway → events → redis
   gateway → payments
   ```
5. A failure table:

   ```markdown
   | Component Killed | Events List | Reserve | Pay | Health Check | User Impact |
   |-----------------|-------------|---------|-----|--------------|-------------|
   | payments        |   ok        |  ok     |503  |payments: down|cannot pay but reserve is working
                |
   | events          |    502      |   502   |  502| events:down  |system failure|
   | redis           |     ok     |not working|404 |redis down    |events are visible, but a secure reservation cant be created or secured               |
   | postgres        |       500   |  500    |500  |postgres down |system failure|
   ```

6. Load generator output showing the error rate spike when payments is killed
```
user@MacBook-Air SRE-Intro % ./app/loadgen/run.sh 5 30
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=38 success=32 fail=6 error_rate=15.7%
[10s] requests=39 success=33 fail=6 error_rate=15.3%
[10s] requests=40 success=34 fail=6 error_rate=15.0%
[10s] requests=41 success=35 fail=6 error_rate=14.6%
[20s] requests=79 success=66 fail=13 error_rate=16.4%
[20s] requests=80 success=67 fail=13 error_rate=16.2%
[20s] requests=81 success=68 fail=13 error_rate=16.0%
[20s] requests=82 success=69 fail=13 error_rate=15.8%
---
Done. total=118 success=102 fail=16 error_rate=13.5%
```

### Task 2

- The diff of your gateway change (`git diff app/gateway/main.py`)
```
@app.post("/reserve/{reservation_id}/pay")
async def pay_reservation(reservation_id: str):
    # 1. Call payments — wrapped in circuit breaker + retry.
    #
    # Composition order matters: cb.call(retry(_charge)) means each CB-tracked
    # invocation includes its retries internally; the CB only sees the FINAL
    # outcome. The reverse — retry(cb.call(_charge)) — would retry past the
    # CircuitOpenError, defeating the fast-fail. See lab 11 §11.4.
    async def _charge():
        resp = await client.post(
            f"{PAYMENTS_URL}/charge",
            json={"reservation_id": reservation_id, "amount": 0},
        )
        resp.raise_for_status()
        return resp

    try:
        pay_resp = await payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
        payment_ref = pay_resp.json().get("payment_ref", "unknown")
    except CircuitOpenError:
        log.error("circuit open, skipping payments call")
        raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
    except httpx.TimeoutException:
        raise HTTPException(504, "Payment service timeout")
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, "Payment failed")
    except Exception as e:
        log.error(f"payment error: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "error": "payments_unavailable",
                "message": "Payment service is temporarily down. Your reservation is held - try again in a few minutes.",
                "reservation_id": reservation_id
            }
        )
```
- Output of reserve (works) and pay (clear 503) when payments is down
```
user@MacBook-Air app % docker compose stop payments                                                          

[+] stop 1/1
 ✔ Container app-payments-1 Stopped                                                                                               0.4s
user@MacBook-Air app % curl -s -X POST http://localhost:3080/events/1/reserve \                              
  -H "Content-Type: application/json" -d '{"quantity": 1}'
{"reservation_id":"2d93c04e-a066-464a-9898-c63d7f82da62","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}%      
user@MacBook-Air app % curl -s -X POST http://localhost:3080/reserve/2d93c04e-a066-464a-9898-c63d7f82da62/pay
{"error":"payments_unavailable","message":"Payment service is temporarily down. Your reservation is held — try again in a few minutes.","reservation_id":"2d93c04e-a066-464a-9898-c63d7f82da62"}%  
```

### Task 3

**Why starring matters:** Stars act as bookmarks and signal community interest, helping maintainers gain visibility and contributors discover valuable projects.  
**Why following developers helps:** Following peers and mentors lets you see their activity, discover new tools, and build professional connections that support collaboration and career growth.