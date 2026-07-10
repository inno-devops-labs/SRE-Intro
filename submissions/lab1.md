# Lab 1 — SRE Philosophy: Deploy, Break, Understand

## Task 1 — Deploy & Break QuickTicket

### 1.1 / 1.2 — Deploy & verify

```
$ docker compose up --build -d
$ docker compose ps
NAME             IMAGE                COMMAND                  SERVICE    STATUS                    PORTS
app-events-1     app-events           "uvicorn main:app --…"   events     Up (healthy)              0.0.0.0:8081->8081/tcp
app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    Up                        0.0.0.0:3080->8080/tcp
app-payments-1   app-payments         "uvicorn main:app --…"   payments   Up                        0.0.0.0:8082->8082/tcp
app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   Up (healthy)              0.0.0.0:5432->5432/tcp
app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      Up (healthy)              0.0.0.0:6379->6379/tcp
```

All 5 containers up and healthy.

### Critical path (list → reserve → pay)

```
$ curl -s http://localhost:3080/events | python3 -m json.tool
[
    {"id": 1, "name": "Go Conference 2026", "venue": "Main Hall A", "date": "2026-09-15T09:00:00+00:00", "total_tickets": 100, "price_cents": 5000, "available": 100},
    {"id": 4, "name": "Python Workshop", "venue": "Lab 301", "date": "2026-09-22T14:00:00+00:00", "total_tickets": 25, "price_cents": 2000, "available": 25},
    {"id": 2, "name": "SRE Meetup", "venue": "Room 204", "date": "2026-10-01T18:00:00+00:00", "total_tickets": 30, "price_cents": 0, "available": 30},
    {"id": 5, "name": "Kubernetes Deep Dive", "venue": "Auditorium B", "date": "2026-10-10T10:00:00+00:00", "total_tickets": 80, "price_cents": 8000, "available": 80},
    {"id": 3, "name": "Cloud Native Summit", "venue": "Expo Center", "date": "2026-11-20T10:00:00+00:00", "total_tickets": 500, "price_cents": 15000, "available": 500}
]

$ curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity": 1}' | python3 -m json.tool
{
    "reservation_id": "baae64aa-8b7b-4048-930f-5f7c3918fd46",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}

$ curl -s -X POST http://localhost:3080/reserve/baae64aa-8b7b-4048-930f-5f7c3918fd46/pay | python3 -m json.tool
{
    "order_id": "baae64aa-8b7b-4048-930f-5f7c3918fd46",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "status": "confirmed"
}
```

### Health check (everything healthy)

```
$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

### Dependency map

```
gateway → events → postgres
gateway → events → redis
gateway → payments
```

* `gateway` is a pure router/aggregator — it holds no state of its own.
* `events` is the only service that talks to Postgres (event catalog + orders) and Redis (pending reservations, held-ticket counters).
* `payments` is stateless — mock charge endpoint, tunable failure/latency via env vars.
* `gateway /health` only checks `events` and `payments` directly; Postgres/Redis health is reflected indirectly through `events`' own `/health`.

### 1.4 — Systematic failure exploration

For each component, all other services were left running; only the named component was stopped with `docker compose stop <svc>` and restarted with `docker compose start <svc>` afterwards.

#### Kill `payments`

```
$ docker compose stop payments
$ curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:3080/events
HTTP 200
$ curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity": 1}'
{"reservation_id":"f2223a9d-...","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
$ curl -s -X POST http://localhost:3080/reserve/<id>/pay
{"detail":"Payment service unavailable"}   # HTTP 502
$ curl -s http://localhost:3080/health
{"status":"degraded","checks":{"events":"ok","payments":"down","circuit_payments":"CLOSED"}}   # HTTP 503
```

#### Kill `events`

```
$ docker compose stop events
$ curl -s http://localhost:3080/events
{"detail":"Events service unavailable"}   # HTTP 502
$ curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity": 1}'
{"detail":"Events service unavailable"}   # HTTP 502
$ curl -s http://localhost:3080/health
{"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}   # HTTP 503
```

#### Kill `redis`

```
$ docker compose stop redis
$ curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:3080/events
HTTP 200
$ curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity": 1}'
{"detail":"Events service timeout"}   # HTTP 504 — events hangs trying to reach redis (DNS lookup of stopped container)
$ curl -s -X POST http://localhost:3080/reserve/<no-id>/pay
{"detail":"Not Found"}   # HTTP 404 — reservation was never written to Redis, so no reservation_id exists
$ curl -s http://localhost:3080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

`/health` stayed "healthy" right after the kill because `events`' Redis check is cached for 5s (`_REDIS_CHECK_INTERVAL`). Within that window the listing endpoint (read-only, Postgres-only) keeps working, but `reserve` — which writes to Redis — times out the whole request chain.

#### Kill `postgres`

```
$ docker compose stop postgres
$ curl -s http://localhost:3080/events
{"detail":"Events service unavailable"}   # HTTP 502
$ curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity": 1}'
Internal Server Error   # HTTP 500
$ curl -s http://localhost:3080/health
{"status":"degraded","checks":{"events":"degraded","payments":"ok","circuit_payments":"CLOSED"}}   # HTTP 503
```

### Failure table

| Component Killed | Events List | Reserve | Pay | Health Check | User Impact |
|-------------------|-------------|---------|-----|--------------|-------------|
| payments | 200 OK | 200 OK | 502 "Payment service unavailable" | 503 degraded (`payments: down`) | Browse + reserve still work; checkout blocked. Reservation stays held in Redis (5 min TTL) so the user can retry payment once `payments` is back. |
| events | 502 "Events service unavailable" | 502 "Events service unavailable" | not tested (no reservation possible without events) | 503 degraded (`events: down`) | Whole app effectively down — `events` is the single point of failure for both reads and writes. |
| redis | 200 OK | 504 "Events service timeout" | 404 (no reservation exists) | 200 healthy (stale, cached redis check) | Browsing still works (Postgres-only). Reservations time out — core write path broken, but health check lags ~5s behind reality. |
| postgres | 502 "Events service unavailable" | 500 Internal Server Error | not tested | 503 degraded (`events: degraded`) | App effectively down for both reads and writes; `events` itself is up but every DB-backed query fails. |

### 1.5 — Load generator + payments failure

```
$ ./app/loadgen/run.sh 5 30
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=46 success=46 fail=0 error_rate=0%
[20s] requests=90 success=88 fail=2 error_rate=2.2%
---
Done. total=130 success=126 fail=4 error_rate=3.0%
```

`docker compose stop payments` was run ~8s into the run (mid first window) and `docker compose start payments` ~30s in (near the end). Error rate jumped from **0% → 2.2%** as soon as payments went down, settling at **3.0%** overall by the end of the run. The failures are concentrated in the "full purchase flow" (10% of traffic) — `pay` calls return 502 while `payments` is down; `events` (70% reads) and `reserve` (20%) keep succeeding throughout, which matches the failure-table finding that listing/reserving are unaffected by a `payments` outage.

## Task 2 — Graceful Degradation

Added a dedicated `httpx.ConnectError` branch in `pay_reservation` so a down `payments` service returns a clear 503 instead of a generic 502, while `reserve` is unaffected (it never calls `payments`).

### Diff (`app/gateway/main.py`)

```diff
diff --git a/app/gateway/main.py b/app/gateway/main.py
index c86db33..8d898f9 100644
--- a/app/gateway/main.py
+++ b/app/gateway/main.py
@@ -332,6 +332,16 @@ async def pay_reservation(reservation_id: str):
     except CircuitOpenError:
         log.error("circuit open, skipping payments call")
         raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
+    except httpx.ConnectError:
+        log.warning(f"payments unreachable, reservation {reservation_id} stays held")
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

### Verification

```
$ docker compose stop payments
$ curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity": 1}'
{"reservation_id":"cfc10020-3dcb-4f2a-8d57-c1e8e69ff599","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}

$ curl -s -X POST http://localhost:3080/reserve/cfc10020-3dcb-4f2a-8d57-c1e8e69ff599/pay
HTTP 503
{"error":"payments_unavailable","message":"Payment service is temporarily down. Your reservation is held — try again in a few minutes.","reservation_id":"cfc10020-3dcb-4f2a-8d57-c1e8e69ff599"}
$ docker compose start payments
```

Reserve still succeeds (200) while `payments` is down, and `pay` now returns a clear 503 with `payments_unavailable` + an actionable message instead of the generic `502 "Payment service unavailable"`.

## Bonus Task — Resource Usage Under Load

### B.1 — Idle (all 5 services running, no traffic)

```
$ docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}"
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-gateway-1    0.29%     40.46MiB / 15.34GiB   4.18kB / 3.16kB   2
app-events-1     0.27%     43.4MiB / 15.34GiB    189kB / 246kB     2
app-payments-1   0.27%     33.06MiB / 15.34GiB   796B / 126B       1
app-postgres-1   0.01%     27.37MiB / 15.34GiB   95kB / 113kB      8
app-redis-1      1.36%     5.977MiB / 15.34GiB   25kB / 9.26kB     6
```

### B.2 — Under load (`./loadgen/run.sh 10 30`, normal payments)

```
$ docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}"
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-gateway-1    4.27%     41.55MiB / 15.34GiB   158kB / 153kB     2
app-events-1     2.22%     44.5MiB / 15.34GiB    316kB / 417kB     2
app-payments-1   0.29%     34.28MiB / 15.34GiB   5.23kB / 3.28kB   2
app-postgres-1   0.62%     27.97MiB / 15.34GiB   164kB / 199kB     8
app-redis-1      3.30%     6.289MiB / 15.34GiB   37kB / 14.7kB     6
```

```
Done. total=230 success=230 fail=0 error_rate=0%
```

### B.3 — Chaos (`PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500`, `./loadgen/run.sh 10 30`)

```
$ docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}"
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-payments-1   0.25%     35.18MiB / 15.34GiB   10.2kB / 7.84kB   2
app-gateway-1    0.23%     40.98MiB / 15.34GiB   622kB / 610kB     2
app-events-1     0.28%     43.55MiB / 15.34GiB   713kB / 950kB     2
app-postgres-1   0.00%     27.86MiB / 15.34GiB   382kB / 455kB     8
app-redis-1      0.76%     5.695MiB / 15.34GiB   92.2kB / 38.1kB   6
```

```
Done. total=167 success=158 fail=9 error_rate=5.3%
```

### Analysis

- **Memory:** `events` (~43-44 MiB) and `gateway` (~40-41 MiB) are the heaviest by RSS — both are FastAPI/uvicorn processes with httpx/psycopg2/redis clients loaded. `redis` is smallest (~6 MiB, in-memory but tiny dataset). Memory barely moves between idle/load/chaos (±1 MiB) — no memory pressure at these volumes.
- **CPU under load:** `gateway` is highest (0.29% → 4.27%), then `events` (0.27% → 2.22%), then `redis` (1.36% → 3.30%). Makes sense — `gateway` does the most per-request work (routing, metrics middleware, JSON (de)serialization for every hop), `events` does DB+Redis I/O, `redis` handles the held-ticket counters on every reserve.
- **Fault injection impact on gateway:** in the B.3 run, `gateway`/`events`/`payments` CPU all *dropped back toward idle* (gateway 4.27%→0.23%) despite the 1-second snapshot landing mid-run. This is the opposite of "gateway holds connections longer → higher CPU" — instead, `PAYMENT_LATENCY_MS=500` makes each `/pay` call block for 500ms+ inside `client.post()` (an I/O wait, not CPU work), so the *event loop is idle* during that time — CPU% drops while wall-clock latency rises. The real cost of the chaos run shows up in **throughput and error rate**: total requests dropped from 230 → 167 (-27%) for the same 30s/10rps target, and error_rate rose from 0% → 5.3% (the 30% injected charge failures). So fault injection in `payments` doesn't spike gateway *CPU* — it spikes gateway *latency* (slower event loop turnaround) and the visible error rate.

## GitHub Community

*(Task 3 — to be completed manually: star the course repo + `simple-container-com/api`, follow `@Cre-eD`, `@Naghme98`, `@pierrepicaud`, and 3+ classmates.)*

- **Why stars matter:** starring bookmarks useful projects, signals community trust/popularity, and surfaces your interests on your profile — helpful for discovering tools and for maintainers to gauge adoption.
- **Why following matters:** following classmates/maintainers surfaces their activity (PRs, issues, projects), which helps spot collaboration opportunities and keeps you visible professionally as your network grows.
