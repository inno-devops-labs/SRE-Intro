```bash
docker compose ps
```
Output:
```
NAME             IMAGE                COMMAND                  SERVICE    CREATED          STATUS                    PORTS
app-events-1     app-events           "uvicorn main:app --…"   events     12 seconds ago   Up 6 seconds              0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    12 seconds ago   Up 6 seconds              0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-payments-1   app-payments         "uvicorn main:app --…"   payments   13 seconds ago   Up 12 seconds             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   12 seconds ago   Up 12 seconds (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      12 seconds ago   Up 12 seconds (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```
1.2
```
georgiy@georgiy-Vivobook-Go-E1504FA-E1504FA:~/SRE-Intro/app$ curl -s http://localhost:3080/events | python3 -m json.tool
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
georgiy@georgiy-Vivobook-Go-E1504FA-E1504FA:~/SRE-Intro/app$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity": 1}' | python3 -m json.tool
{
    "reservation_id": "3f513fb2-c266-4843-af4f-149f61cb8434",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}
georgiy@georgiy-Vivobook-Go-E1504FA-E1504FA:~/SRE-Intro/app$ curl -s -X POST http://localhost:3080/reserve/3f513fb2-c266-4843-af4f-149f61cb8434/pay | python3 -m json.tool
{
    "order_id": "3f513fb2-c266-4843-af4f-149f61cb8434",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "status": "confirmed"
}
georgiy@georgiy-Vivobook-Go-E1504FA-E1504FA:~/SRE-Intro/app$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}

```
1.3
```
gateway → events → postgres
gateway → events → redis
gateway → payments
```

1.4 — Systematic Failure Exploration

```bash
docker compose stop payments
curl -s http://localhost:3080/events
curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity": 1}'
curl -s -X POST http://localhost:3080/reserve/<reservation_id>/pay
curl -s http://localhost:3080/health
docker compose start payments
```
Output:
```
########## KILL PAYMENTS ##########
--- events list ---
HTTP 200
[{"id":1,"name":"Go Conference 2026", ... "available":99}, ...]
--- reserve ---
{"reservation_id":"dfeac7ea-b854-43a2-8f06-ffc90766167c","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
HTTP 200
--- pay ---
{"detail":"Payment service unavailable"}
HTTP 502
--- health ---
{
    "status": "degraded",
    "checks": {
        "events": "ok",
        "payments": "down",
        "circuit_payments": "CLOSED"
    }
}
```

```bash
docker compose stop events
curl -s http://localhost:3080/events
curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity": 1}'
curl -s -X POST http://localhost:3080/reserve/dfeac7ea-b854-43a2-8f06-ffc90766167c/pay
curl -s http://localhost:3080/health
docker compose start events
```
Output:
```
########## KILL EVENTS ##########
--- events list ---
{"detail":"Events service unavailable"}
HTTP 502
--- reserve ---
{"detail":"Events service unavailable"}
HTTP 502
--- pay (reservation held earlier, before events died) ---
{"detail":"Payment succeeded but confirmation failed — contact support"}
HTTP 500
--- health ---
{
    "status": "degraded",
    "checks": {
        "events": "down",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```
Interesting: pay actually charges the card via `payments` (step 1 succeeds) and only fails on the second call to `events` to confirm the order. Result: money is taken but the reservation is never confirmed — a real "double failure" the user has no way to recover from except contacting support.

```bash
docker compose stop redis
curl -s http://localhost:3080/events
curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity": 1}'
curl -s -X POST http://localhost:3080/reserve/00000000-0000-0000-0000-000000000000/pay
curl -s http://localhost:3080/health
curl -s http://localhost:8081/health
docker compose start redis
```
Output:
```
########## KILL REDIS ##########
--- events list ---
HTTP 200
[{"id":1, ... }, ...]
--- reserve ---
{"detail":"Events service timeout"}
HTTP 504
--- pay ---
{"detail":"Payment succeeded but confirmation failed — contact support"}
HTTP 500
--- health (gateway) ---
{
    "status": "degraded",
    "checks": {
        "events": "down",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
--- health (events, direct) ---
{
    "status": "healthy",
    "checks": {
        "postgres": "ok",
        "redis": "ok"
    }
}
```
Interesting: `events` caches its Redis check for 5s (`_check_redis`), so a direct `/health` call right after killing redis can still report `redis: ok` from the stale cache, while the gateway's own 2s timeout against `/health` times out and reports `events: down` at the same moment. Two health checks of the same dependency disagreeing at the same instant — a good example of why health checks need consistent, short cache windows.

```bash
docker compose stop postgres
curl -s http://localhost:3080/events
curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity": 1}'
curl -s -X POST http://localhost:3080/reserve/00000000-0000-0000-0000-000000000000/pay
curl -s http://localhost:3080/health
docker compose start postgres
```
Output:
```
########## KILL POSTGRES ##########
--- events list ---
{"detail":"Events service unavailable"}
HTTP 502
--- reserve ---
Internal Server Error
HTTP 500
--- pay ---
{"detail":"Payment succeeded but confirmation failed — contact support"}
HTTP 500
--- health ---
{
    "status": "degraded",
    "checks": {
        "events": "degraded",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

Failure table:

| Component Killed | Events List | Reserve | Pay | Health Check | User Impact |
|-----------------|-------------|---------|-----|--------------|-------------|
| payments        | 200 OK   | 200 OK | 502 "Payment service unavailable" | `payments: down`, status `degraded` | Can browse and reserve tickets, but cannot pay. Reservation is held in Redis and can be retried once payments is back. |
| events          | 502 "Events service unavailable" | 502 "Events service unavailable" | 500 "Payment succeeded but confirmation failed" (if reservation existed before events died) | `events: down`, status `degraded` | Cannot browse or reserve at all. Worse: an in-flight pay charges the card via payments but can't confirm the order in events — money taken, no ticket. |
| redis           | 200 OK (doesn't touch redis) | 504 "Events service timeout" | 500 "Payment succeeded but confirmation failed" | Gateway reports `events: down` (its 2s health timeout expires while events blocks on redis); a direct call to `events`'s own `/health` can still say `redis: ok` for up to 5s due to its health-check cache | Cannot reserve new tickets (blocks/times out). Listing still works since it only hits postgres. Health checks momentarily disagree with each other. |
| postgres        | 502 "Events service unavailable" | 500 Internal Server Error (unhandled exception, no clean error message) | 500 "Payment succeeded but confirmation failed" | `events: degraded`, status `degraded` | Nothing works — postgres is the source of truth for events/tickets/orders. Reserve returns a raw 500 instead of a clean error — worst UX of all the failures tested. |

1.5 — Load Generator

Baseline run (no faults):
```bash
chmod +x app/loadgen/run.sh
./app/loadgen/run.sh 5 30
```
Output:
```
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=43 success=43 fail=0 error_rate=0%
[20s] requests=86 success=86 fail=0 error_rate=0%
---
Done. total=127 success=127 fail=0 error_rate=0%
```

Run with `payments` killed mid-load (killed at ~10s, restarted at ~22s):
```bash
./app/loadgen/run.sh 5 30 &
sleep 10 && docker compose stop payments
sleep 12 && docker compose start payments
```
Output:
```
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=40 success=40 fail=0 error_rate=0%
[20s] requests=83 success=77 fail=6 error_rate=7.2%
---
Done. total=125 success=117 fail=8 error_rate=6.4%
```
Error rate jumps from 0% to ~7% right after `payments` is killed, and stops climbing once it's restarted. Only ~10% of load-generator traffic is a full reserve+pay flow, so a 100%-down payments service shows up as roughly a 6-8% overall error rate rather than a full outage — reads and plain reserves keep succeeding underneath it.

## Task 2 — Graceful Degradation

1.7 — Diff of `app/gateway/main.py`:
```diff
diff --git a/app/gateway/main.py b/app/gateway/main.py
index c86db33..095f1ab 100644
--- a/app/gateway/main.py
+++ b/app/gateway/main.py
@@ -332,6 +332,16 @@ async def pay_reservation(reservation_id: str):
     except CircuitOpenError:
         log.error("circuit open, skipping payments call")
         raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
+    except httpx.ConnectError:
+        log.error("payments unreachable")
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
```

1.8 — Verify:
```bash
docker compose stop payments
curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity": 1}'
curl -s -X POST http://localhost:3080/reserve/<reservation_id>/pay
docker compose start payments
```
Output:
```
=== stop payments ===
--- reserve (should work) ---
{"reservation_id":"249fbc21-5ce3-4512-b06c-2606a769d7ad","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
HTTP 200
--- pay (should be clear 503) ---
{"error":"payments_unavailable","message":"Payment service is temporarily down. Your reservation is held — try again in a few minutes.","reservation_id":"249fbc21-5ce3-4512-b06c-2606a769d7ad"}
HTTP 503
```
Reserve keeps working exactly as before (it never touches payments), and pay now returns a clean, actionable `503` instead of the generic `502 "Payment service unavailable"` seen in Task 1. The reservation stays held in Redis, so the user can retry payment once the service is back — confirmed by the health check going back to `healthy` after `docker compose start payments`.

## Task 3 — GitHub Community

Starring a repository bookmarks it for later and signals to maintainers and other developers that the project is useful, which helps good tools get discovered. Following other developers surfaces their activity in your feed, making it easier to find collaborators, learn from their work, and stay connected for future team projects.

## Bonus Task — Resource Usage Under Load

B.1 — Baseline (idle):
```bash
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}"
```
Output:
```
NAME             CPU %     MEM USAGE / LIMIT    NET I/O           PIDS
app-gateway-1    0.21%     38.01MiB / 14.9GiB   11.5kB / 7.36kB   2
app-events-1     0.21%     40.8MiB / 14.9GiB    11.9kB / 7.48kB   2
app-postgres-1   5.48%     24.41MiB / 14.9GiB   9.52kB / 3.82kB   8
app-redis-1      0.88%     3.895MiB / 14.9GiB   8.05kB / 1.16kB   6
app-payments-1   0.21%     33.41MiB / 14.9GiB   5.86kB / 1.35kB   2
```

B.2 — Under load (10 req/s for 30s, stats captured at ~15s):
```bash
./app/loadgen/run.sh 10 30
# in another terminal, mid-run:
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}"
```
Output:
```
NAME             CPU %     MEM USAGE / LIMIT    NET I/O           PIDS
app-gateway-1    5.62%     38.7MiB / 14.9GiB    203kB / 193kB     2
app-events-1     2.60%     41.95MiB / 14.9GiB   165kB / 215kB     2
app-postgres-1   6.50%     25.04MiB / 14.9GiB   101kB / 112kB     8
app-redis-1      0.86%     3.434MiB / 14.9GiB   28.8kB / 10.1kB   6
app-payments-1   0.21%     33.53MiB / 14.9GiB   11kB / 5.04kB     2
```
Loadgen output:
```
Done. total=222 success=208 fail=14 error_rate=6.3%
```

B.3 — Under stress with fault injection (`PAYMENT_FAILURE_RATE=0.3`, `PAYMENT_LATENCY_MS=500`, 10 req/s for 30s, stats captured at ~15s):
```bash
docker compose stop payments
PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500 docker compose up -d payments
./app/loadgen/run.sh 10 30
# in another terminal, mid-run:
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}"
docker compose stop payments
PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0 docker compose up -d payments
```
Output:
```
NAME             CPU %     MEM USAGE / LIMIT    NET I/O           PIDS
app-payments-1   0.19%     34.89MiB / 14.9GiB   7.27kB / 3.38kB   2
app-gateway-1    5.25%     39.44MiB / 14.9GiB   494kB / 479kB     2
app-events-1     2.96%     41.99MiB / 14.9GiB   427kB / 563kB     2
app-postgres-1   1.00%     25.14MiB / 14.9GiB   241kB / 278kB     8
app-redis-1      0.94%     4.426MiB / 14.9GiB   56.3kB / 21.4kB   6
```
Loadgen output:
```
Done. total=186 success=153 fail=33 error_rate=17.7%
```

**Analysis:**
- **Memory:** `events` uses the most memory at rest and under load (~41MiB), closely followed by `gateway` (~38-39MiB); both stay essentially flat across all three scenarios — this workload doesn't grow memory, it's CPU/IO bound, not memory bound.
- **CPU under load:** `gateway` uses the most CPU under load (~5-5.6%), because it terminates every inbound HTTP connection, runs the rate-limiter/metrics middleware on every request, and fans each request out to one or two backend calls. `postgres` is a close second at idle (~5.5%, mostly autovacuum/background housekeeping) but drops relatively under load since `events` reuses a pooled connection instead of opening new ones.
- **Fault injection impact on gateway:** with `PAYMENT_FAILURE_RATE=0.3` and `PAYMENT_LATENCY_MS=500`, `payments`' own CPU/memory barely change (it's just sleeping before responding), but `gateway`'s NetIO more than doubles (203kB→494kB) for the same 15s window and total completed requests drop (222→186 for the same 30s/10rps target) even though gateway's CPU% stays similar. This matches the hint: slow payments responses mean the gateway holds each `/pay` connection open ~500ms longer, so the single-threaded load generator gets fewer requests out the door in the same wall-clock time — the latency in one dependency throttles overall system throughput even though no service is actually overloaded.
