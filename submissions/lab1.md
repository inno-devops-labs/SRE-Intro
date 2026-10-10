# Lab 1 — Deploy, Break, Understand

Author: Walkerino
Date: 2026-09-13
Status: ready for course review; Moodle submission pending.

The initial checks and payments experiment below preserve the student's supplied
terminal output. Additional experiments, the repeated successful purchase, load
runs, and Task 2/bonus checks were performed by the assistant against the local
Docker Compose application. All reported HTTP status codes are observed results.
Task 3 distinguishes student confirmation from public API verification.
Markdown escaping and terminal prompts were removed.

## Task 1 — Deploy & Break QuickTicket

### 1. System deployment

The application was launched and its images rebuilt using:

```bash
docker compose -f app/docker-compose.yaml up --build -d
```

The command completed successfully. Relevant completion output (build details omitted):

```text
Image app-events Built
 Image app-payments Built
 Image app-gateway Built
 Container app-postgres-1 Running
 Container app-redis-1 Running
 Container app-gateway-1 Running
 Container app-events-1 Running
 Container app-payments-1 Running
 Container app-redis-1 Waiting
 Container app-postgres-1 Waiting
 Container app-redis-1 Healthy
 Container app-postgres-1 Healthy
```

All five containers were running:

```bash
docker compose -f app/docker-compose.yaml ps
```

```text
NAME             IMAGE                                                                     COMMAND                  SERVICE    CREATED          STATUS                   PORTS
app-events-1     sha256:c8b561af776ec8c6ffcfd61f6d23d70822fee42db7f0186dc6f518882e9cf8dd   "uvicorn main:app --…"   events     34 minutes ago   Up 7 minutes             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1    sha256:79e09e0975d9f7d2c1f9f2cd57bda5e6e3a80327d24ffca06f942153b4121f60   "uvicorn main:app --…"   gateway    34 minutes ago   Up 33 minutes            0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-payments-1   sha256:0d52739aeb83200edd3fb20bba2c23fc56f551195ab8acfbab20b7212005a281   "uvicorn main:app --…"   payments   34 minutes ago   Up 25 minutes            0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1   postgres:17-alpine                                                        "docker-entrypoint.s…"   postgres   34 minutes ago   Up 6 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-redis-1      redis:7-alpine                                                            "docker-entrypoint.s…"   redis      34 minutes ago   Up 5 minutes (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

PostgreSQL and Redis expose Docker health checks and were healthy. The application
services were running; their readiness was checked through HTTP endpoints below.

### 2. Successful user flow

#### Initial health check

```bash
curl -s http://localhost:3080/health
```

```json
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

The gateway reported healthy, with events and payments available. The HTTP status
was not captured because this initial command did not use -i.

#### List events

```bash
curl -s http://localhost:3080/events
```

```json
[{"id":1,"name":"Go Conference 2026","venue":"Main Hall A","date":"2026-09-15T09:00:00+00:00","total_tickets":100,"price_cents":5000,"available":100},{"id":4,"name":"Python Workshop","venue":"Lab 301","date":"2026-09-22T14:00:00+00:00","total_tickets":25,"price_cents":2000,"available":25},{"id":2,"name":"SRE Meetup","venue":"Room 204","date":"2026-10-01T18:00:00+00:00","total_tickets":30,"price_cents":0,"available":30},{"id":5,"name":"Kubernetes Deep Dive","venue":"Auditorium B","date":"2026-10-10T10:00:00+00:00","total_tickets":80,"price_cents":8000,"available":80},{"id":3,"name":"Cloud Native Summit","venue":"Expo Center","date":"2026-11-20T10:00:00+00:00","total_tickets":500,"price_cents":15000,"available":500}]
```

Five events were returned. Event 1 initially had 100 available tickets.

#### Reserve and pay

The student initially reported status confirmed without a complete transcript.
To capture complete evidence, the assistant repeated the critical path using
event 3, which had sufficient capacity. All requests used the host's published
gateway port 3080.

**List events**

```bash
curl -sS -i --max-time 10 http://localhost:3080/events
```

```http
HTTP/1.1 200 OK
date: Sun, 13 Sep 2026 12:16:30 GMT
server: uvicorn
content-length: 723
content-type: application/json

[{"id":1,"name":"Go Conference 2026","venue":"Main Hall A","date":"2026-09-15T09:00:00+00:00","total_tickets":100,"price_cents":5000,"available":99},{"id":4,"name":"Python Workshop","venue":"Lab 301","date":"2026-09-22T14:00:00+00:00","total_tickets":25,"price_cents":2000,"available":25},{"id":2,"name":"SRE Meetup","venue":"Room 204","date":"2026-10-01T18:00:00+00:00","total_tickets":30,"price_cents":0,"available":30},{"id":5,"name":"Kubernetes Deep Dive","venue":"Auditorium B","date":"2026-10-10T10:00:00+00:00","total_tickets":80,"price_cents":8000,"available":80},{"id":3,"name":"Cloud Native Summit","venue":"Expo Center","date":"2026-11-20T10:00:00+00:00","total_tickets":500,"price_cents":15000,"available":499}]
```

**Reserve one ticket**

```bash
curl -sS -i --max-time 10 -X POST http://localhost:3080/events/3/reserve -H 'Content-Type: application/json' -d '{"quantity":1}'
```

```http
HTTP/1.1 200 OK
date: Sun, 13 Sep 2026 12:16:30 GMT
server: uvicorn
content-length: 128
content-type: application/json

{"reservation_id":"46b0ba47-9a52-4ee3-83b9-cdbabcc70ba5","event_id":3,"quantity":1,"total_cents":15000,"expires_in_seconds":300}
```

**Pay for the same reservation**

```bash
curl -sS -i --max-time 10 -X POST http://localhost:3080/reserve/46b0ba47-9a52-4ee3-83b9-cdbabcc70ba5/pay
```

```http
HTTP/1.1 200 OK
date: Sun, 13 Sep 2026 12:16:31 GMT
server: uvicorn
content-length: 118
content-type: application/json

{"order_id":"46b0ba47-9a52-4ee3-83b9-cdbabcc70ba5","event_id":3,"quantity":1,"total_cents":15000,"status":"confirmed"}
```

**Healthy baseline captured before the flow**

```bash
curl -sS -i --max-time 10 http://localhost:3080/health
```

```http
HTTP/1.1 200 OK
date: Sun, 13 Sep 2026 12:16:30 GMT
server: uvicorn
content-length: 89
content-type: application/json

{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

The reservation ID matches the confirmed order ID. Listing, reservation creation,
and payment returned HTTP 200, demonstrating the full user flow.

### 3. Architecture

Reviewed source: app/gateway/main.py, app/events/main.py, app/payments/main.py,
and app/docker-compose.yaml.

```text
User --HTTP :3080--> gateway
gateway --HTTP--> events
gateway --HTTP--> payments
events --SQL--> PostgreSQL
events --Redis protocol--> Redis
```

| Component | Responsibility | Dependencies |
|---|---|---|
| gateway | Public API, routing, timeout/error handling, aggregate health | events and payments |
| events | Event listing, reservations, confirmed orders | PostgreSQL and Redis |
| payments | Mock charging with configurable delay and random failures | No database or Redis dependency |
| PostgreSQL | Persistent events and confirmed orders | Named Docker volume postgres_data |
| Redis | Temporary reservation data (300-second TTL) and held-ticket counters | Used by events |

Listing reads PostgreSQL. Reservation creation checks event data and availability,
then stores a temporary reservation in Redis. Payment first calls the mock charge
endpoint, then calls events to confirm the reservation; confirmation reads Redis,
writes the order to PostgreSQL, and deletes the temporary reservation.

Therefore, payment success alone does not guarantee order confirmation: events,
Redis, or PostgreSQL can fail after the mock charge has succeeded. Gateway health
checks events and payments; events health checks PostgreSQL and Redis.

The gateway also contains optional notification wiring and resilience-pattern
scaffolding for Lab 11. Notifications are disabled in this Compose setup, and
the circuit-breaker state stays CLOSED in the unimplemented scaffold.

### 4. Failure exploration

#### 4.1. Stopping payments

**Stop payments**

```bash
docker compose -f app/docker-compose.yaml stop payments
```

```text
[+] stop 1/1
✔ Container app-payments-1 Stopped  0.3s
```

**List events**

```bash
curl -i http://localhost:3080/events
```

```http
HTTP/1.1 200 OK
date: Sun, 13 Sep 2026 11:49:45 GMT
server: uvicorn
content-length: 723
content-type: application/json

[{"id":1,"name":"Go Conference 2026","venue":"Main Hall A","date":"2026-09-15T09:00:00+00:00","total_tickets":100,"price_cents":5000,"available":99},{"id":4,"name":"Python Workshop","venue":"Lab 301","date":"2026-09-22T14:00:00+00:00","total_tickets":25,"price_cents":2000,"available":25},{"id":2,"name":"SRE Meetup","venue":"Room 204","date":"2026-10-01T18:00:00+00:00","total_tickets":30,"price_cents":0,"available":30},{"id":5,"name":"Kubernetes Deep Dive","venue":"Auditorium B","date":"2026-10-10T10:00:00+00:00","total_tickets":80,"price_cents":8000,"available":80},{"id":3,"name":"Cloud Native Summit","venue":"Expo Center","date":"2026-11-20T10:00:00+00:00","total_tickets":500,"price_cents":15000,"available":500}]
```

**Create a new reservation**

```bash
curl -i -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity": 1}'
```

```http
HTTP/1.1 200 OK
date: Sun, 13 Sep 2026 11:49:59 GMT
server: uvicorn
content-length: 127
content-type: application/json

{"reservation_id":"482b22f0-6c64-4648-ac9b-f2de1298f483","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
```

**Attempt payment**

```bash
curl -i -X POST http://localhost:3080/reserve/482b22f0-6c64-4648-ac9b-f2de1298f483/pay
```

```http
HTTP/1.1 502 Bad Gateway
date: Sun, 13 Sep 2026 11:50:25 GMT
server: uvicorn
content-length: 40
content-type: application/json

{"detail":"Payment service unavailable"}
```

**Check health**

```bash
curl -i http://localhost:3080/health
```

```http
HTTP/1.1 503 Service Unavailable
date: Sun, 13 Sep 2026 11:50:30 GMT
server: uvicorn
content-length: 92
content-type: application/json

{"status":"degraded","checks":{"events":"ok","payments":"down","circuit_payments":"CLOSED"}}
```

**Restore payments**

```bash
docker compose -f app/docker-compose.yaml start payments
```

```text
[+] start 1/1
✔ Container app-payments-1 Started  0.1s
```

The supplied output confirms the container started. The healthy baseline in
section 4.2 confirms subsequent application recovery.

**Analysis:** listing and reservation creation continued to return 200.
Payment returned 502, preventing checkout. The health endpoint detected the
outage and returned 503 with payments: down.

#### Method for the additional experiments

The assistant sent HTTP requests from inside the gateway container to
http://127.0.0.1:8080 using Python's standard library. This exercises the gateway
API inside Docker rather than testing the host's published port 3080.
Each output line records a request path, HTTP status, and response body.

Before each outage, the system was healthy and a fresh reservation was created
for event 3. Payment during the outage used that reservation. Only one dependency
was stopped at a time, and it remained stopped until all four checks finished.
The assistant waited six seconds before outage checks and recovery checks.

The helper below was passed to
`docker compose -f app/docker-compose.yaml exec -T gateway python -u -c`,
followed by calls to request; each result was printed with print(json.dumps(...)).

```python
import urllib.request, urllib.error, json
def request(path, data=None):
    req=urllib.request.Request("http://127.0.0.1:8080"+path, data=None if data is None else json.dumps(data).encode(), headers={"Content-Type":"application/json"}, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return {"path":path,"status":r.status,"body":r.read().decode()}
    except urllib.error.HTTPError as e: return {"path":path,"status":e.code,"body":e.read().decode()}
    except Exception as e: return {"path":path,"error":str(e)}
```

Baseline calls: request("/health") and
request("/events/3/reserve", {"quantity": 1}).
Outage calls: request("/events"), request("/events/3/reserve", {"quantity": 1}),
request("/reserve/ACTUAL_ID/pay", {}), and request("/health").
Recovery calls: request("/health") and request("/events").

An initial Redis run was excluded from the outage table because Redis was
restored before all requests completed; a payment then succeeded during recovery.
Section 4.3 contains a repeated run with Redis stopped throughout the checks.
This accounts for event 3's available count changing between runs.

#### 4.2. Stopping events

**Healthy baseline and fresh reservation**

```text
{"path": "/health", "status": 200, "body": "{\"status\":\"healthy\",\"checks\":{\"events\":\"ok\",\"payments\":\"ok\",\"circuit_payments\":\"CLOSED\"}}"}
{"path": "/events/3/reserve", "status": 200, "body": "{\"reservation_id\":\"793ee0b9-7ec5-4950-ab89-d9e723178c66\",\"event_id\":3,\"quantity\":1,\"total_cents\":15000,\"expires_in_seconds\":300}"}
```

**Stop command and output**

```bash
docker compose -f app/docker-compose.yaml stop events
```

```text
Container app-events-1 Stopping
 Container app-events-1 Stopped
```

**Observed outage responses**

```text
{"path": "/events", "status": 502, "body": "{\"detail\":\"Events service unavailable\"}"}
{"path": "/events/3/reserve", "status": 502, "body": "{\"detail\":\"Events service unavailable\"}"}
{"path": "/reserve/793ee0b9-7ec5-4950-ab89-d9e723178c66/pay", "status": 500, "body": "{\"detail\":\"Payment succeeded but confirmation failed \u2014 contact support\"}"}
{"path": "/health", "status": 503, "body": "{\"status\":\"degraded\",\"checks\":{\"events\":\"down\",\"payments\":\"ok\",\"circuit_payments\":\"CLOSED\"}}"}
```

**Start command and output**

```bash
docker compose -f app/docker-compose.yaml start events
```

```text
Container app-postgres-1 Waiting
 Container app-redis-1 Waiting
 Container app-redis-1 Healthy
 Container app-postgres-1 Healthy
 Container app-events-1 Starting
 Container app-events-1 Started
```

**Observed recovery**

```text
{"path": "/health", "status": 200, "body": "{\"status\":\"healthy\",\"checks\":{\"events\":\"ok\",\"payments\":\"ok\",\"circuit_payments\":\"CLOSED\"}}"}
{"path": "/events", "status": 200, "body": "[{\"id\":1,\"name\":\"Go Conference 2026\",\"venue\":\"Main Hall A\",\"date\":\"2026-09-15T09:00:00+00:00\",\"total_tickets\":100,\"price_cents\":5000,\"available\":99},{\"id\":4,\"name\":\"Python Workshop\",\"venue\":\"Lab 301\",\"date\":\"2026-09-22T14:00:00+00:00\",\"total_tickets\":25,\"price_cents\":2000,\"available\":25},{\"id\":2,\"name\":\"SRE Meetup\",\"venue\":\"Room 204\",\"date\":\"2026-10-01T18:00:00+00:00\",\"total_tickets\":30,\"price_cents\":0,\"available\":30},{\"id\":5,\"name\":\"Kubernetes Deep Dive\",\"venue\":\"Auditorium B\",\"date\":\"2026-10-10T10:00:00+00:00\",\"total_tickets\":80,\"price_cents\":8000,\"available\":80},{\"id\":3,\"name\":\"Cloud Native Summit\",\"venue\":\"Expo Center\",\"date\":\"2026-11-20T10:00:00+00:00\",\"total_tickets\":500,\"price_cents\":15000,\"available\":500}]"}
```

**Analysis:** Listing and new reservations returned 502 because events was unreachable. The mock payment call succeeded, but confirming the reservation in events failed, so the overall payment endpoint returned 500. Successful payment processing alone therefore did not guarantee a confirmed order.

After restoration, health and event listing both returned 200, with status healthy.

#### 4.3. Stopping redis

**Healthy baseline and fresh reservation**

```text
{"path": "/health", "status": 200, "body": "{\"status\":\"healthy\",\"checks\":{\"events\":\"ok\",\"payments\":\"ok\",\"circuit_payments\":\"CLOSED\"}}"}
{"path": "/events/3/reserve", "status": 200, "body": "{\"reservation_id\":\"50c7aef6-5e38-440f-a974-f85178c30141\",\"event_id\":3,\"quantity\":1,\"total_cents\":15000,\"expires_in_seconds\":300}"}
```

**Stop command and output**

```bash
docker compose -f app/docker-compose.yaml stop redis
```

```text
Container app-redis-1 Stopping
 Container app-redis-1 Stopped
```

**Observed outage responses**

```text
{"path": "/events", "status": 200, "body": "[{\"id\":1,\"name\":\"Go Conference 2026\",\"venue\":\"Main Hall A\",\"date\":\"2026-09-15T09:00:00+00:00\",\"total_tickets\":100,\"price_cents\":5000,\"available\":99},{\"id\":4,\"name\":\"Python Workshop\",\"venue\":\"Lab 301\",\"date\":\"2026-09-22T14:00:00+00:00\",\"total_tickets\":25,\"price_cents\":2000,\"available\":25},{\"id\":2,\"name\":\"SRE Meetup\",\"venue\":\"Room 204\",\"date\":\"2026-10-01T18:00:00+00:00\",\"total_tickets\":30,\"price_cents\":0,\"available\":30},{\"id\":5,\"name\":\"Kubernetes Deep Dive\",\"venue\":\"Auditorium B\",\"date\":\"2026-10-10T10:00:00+00:00\",\"total_tickets\":80,\"price_cents\":8000,\"available\":80},{\"id\":3,\"name\":\"Cloud Native Summit\",\"venue\":\"Expo Center\",\"date\":\"2026-11-20T10:00:00+00:00\",\"total_tickets\":500,\"price_cents\":15000,\"available\":499}]"}
{"path": "/events/3/reserve", "status": 504, "body": "{\"detail\":\"Events service timeout\"}"}
{"path": "/reserve/50c7aef6-5e38-440f-a974-f85178c30141/pay", "status": 500, "body": "{\"detail\":\"Payment succeeded but confirmation failed \u2014 contact support\"}"}
{"path": "/health", "status": 503, "body": "{\"status\":\"degraded\",\"checks\":{\"events\":\"down\",\"payments\":\"ok\",\"circuit_payments\":\"CLOSED\"}}"}
```

**Start command and output**

```bash
docker compose -f app/docker-compose.yaml start redis
```

```text
Container app-redis-1 Starting
 Container app-redis-1 Started
```

**Observed recovery**

```text
{"path": "/health", "status": 200, "body": "{\"status\":\"healthy\",\"checks\":{\"events\":\"ok\",\"payments\":\"ok\",\"circuit_payments\":\"CLOSED\"}}"}
{"path": "/events", "status": 200, "body": "[{\"id\":1,\"name\":\"Go Conference 2026\",\"venue\":\"Main Hall A\",\"date\":\"2026-09-15T09:00:00+00:00\",\"total_tickets\":100,\"price_cents\":5000,\"available\":99},{\"id\":4,\"name\":\"Python Workshop\",\"venue\":\"Lab 301\",\"date\":\"2026-09-22T14:00:00+00:00\",\"total_tickets\":25,\"price_cents\":2000,\"available\":25},{\"id\":2,\"name\":\"SRE Meetup\",\"venue\":\"Room 204\",\"date\":\"2026-10-01T18:00:00+00:00\",\"total_tickets\":30,\"price_cents\":0,\"available\":30},{\"id\":5,\"name\":\"Kubernetes Deep Dive\",\"venue\":\"Auditorium B\",\"date\":\"2026-10-10T10:00:00+00:00\",\"total_tickets\":80,\"price_cents\":8000,\"available\":80},{\"id\":3,\"name\":\"Cloud Native Summit\",\"venue\":\"Expo Center\",\"date\":\"2026-11-20T10:00:00+00:00\",\"total_tickets\":500,\"price_cents\":15000,\"available\":499}]"}
```

**Analysis:** Listing remained available, but new reservations returned 504 (Events service timeout). Existing reservations could not be confirmed without Redis, so payment returned 500 after the mock charge. The gateway reported events: down even though the events container remained running: the label reflects a failed health request.

After restoration, health and event listing both returned 200, with status healthy.

#### 4.4. Stopping postgres

**Healthy baseline and fresh reservation**

```text
{"path": "/health", "status": 200, "body": "{\"status\":\"healthy\",\"checks\":{\"events\":\"ok\",\"payments\":\"ok\",\"circuit_payments\":\"CLOSED\"}}"}
{"path": "/events/3/reserve", "status": 200, "body": "{\"reservation_id\":\"f7b4c7c7-ae81-4324-8db8-a2d90428085a\",\"event_id\":3,\"quantity\":1,\"total_cents\":15000,\"expires_in_seconds\":300}"}
```

**Stop command and output**

```bash
docker compose -f app/docker-compose.yaml stop postgres
```

```text
Container app-postgres-1 Stopping
 Container app-postgres-1 Stopped
```

**Observed outage responses**

```text
{"path": "/events", "status": 502, "body": "{\"detail\":\"Events service unavailable\"}"}
{"path": "/events/3/reserve", "status": 502, "body": "{\"detail\":\"Events service unavailable\"}"}
{"path": "/reserve/f7b4c7c7-ae81-4324-8db8-a2d90428085a/pay", "status": 500, "body": "{\"detail\":\"Payment succeeded but confirmation failed \u2014 contact support\"}"}
{"path": "/health", "status": 503, "body": "{\"status\":\"degraded\",\"checks\":{\"events\":\"down\",\"payments\":\"ok\",\"circuit_payments\":\"CLOSED\"}}"}
```

**Start command and output**

```bash
docker compose -f app/docker-compose.yaml start postgres
```

```text
Container app-postgres-1 Starting
 Container app-postgres-1 Started
```

**Observed recovery**

```text
{"path": "/health", "status": 200, "body": "{\"status\":\"healthy\",\"checks\":{\"events\":\"ok\",\"payments\":\"ok\",\"circuit_payments\":\"CLOSED\"}}"}
{"path": "/events", "status": 200, "body": "[{\"id\":1,\"name\":\"Go Conference 2026\",\"venue\":\"Main Hall A\",\"date\":\"2026-09-15T09:00:00+00:00\",\"total_tickets\":100,\"price_cents\":5000,\"available\":99},{\"id\":4,\"name\":\"Python Workshop\",\"venue\":\"Lab 301\",\"date\":\"2026-09-22T14:00:00+00:00\",\"total_tickets\":25,\"price_cents\":2000,\"available\":25},{\"id\":2,\"name\":\"SRE Meetup\",\"venue\":\"Room 204\",\"date\":\"2026-10-01T18:00:00+00:00\",\"total_tickets\":30,\"price_cents\":0,\"available\":30},{\"id\":5,\"name\":\"Kubernetes Deep Dive\",\"venue\":\"Auditorium B\",\"date\":\"2026-10-10T10:00:00+00:00\",\"total_tickets\":80,\"price_cents\":8000,\"available\":80},{\"id\":3,\"name\":\"Cloud Native Summit\",\"venue\":\"Expo Center\",\"date\":\"2026-11-20T10:00:00+00:00\",\"total_tickets\":500,\"price_cents\":15000,\"available\":499}]"}
```

**Analysis:** Listing and new reservations returned 502 because their database dependency was unavailable. The mock payment call could succeed, but order confirmation failed, returning 500. The gateway reported events: down. Restarting PostgreSQL was sufficient for recovery in this run; no additional events restart was needed.

After restoration, health and event listing both returned 200, with status healthy.

#### Failure summary

The payments row comes from the student's terminal session; the remaining rows
come from the assistant's local experiments. Payment in the additional runs used
a fresh reservation created before stopping the dependency.

| Component Killed | Events List | Reserve | Pay | Health Check | User Impact |
|---|---|---|---|---|---|
| payments | 200 OK | 200 OK | 502, Payment service unavailable | 503, degraded; events: ok, payments: down | Browsing and reservations work; payment is unavailable |
| events | 502, Events service unavailable | 502, Events service unavailable | 500, payment succeeded but confirmation failed | 503, degraded; events: down, payments: ok | Browsing and reservations fail; mock charge can succeed without a confirmed order |
| redis | 200 OK | 504, Events service timeout | 500, payment succeeded but confirmation failed | 503, degraded; events: down, payments: ok | Browsing works; new reservations time out and checkout cannot confirm the order |
| postgres | 502, Events service unavailable | 502, Events service unavailable | 500, payment succeeded but confirmation failed | 503, degraded; events: down, payments: ok | Event data and reservations are unavailable; confirmed orders cannot be persisted |

The events: down label does not distinguish a stopped container from a failed
health request caused by another dependency. These are the captured results;
exact errors can vary with timeout and connection state.

### 5. Load testing

Both runs used the supplied, unchanged bash generator before the Task 2 gateway
image was deployed. The generator mixes approximately 70% listing, 20%
reservation creation, and 10% full purchases. It is sequential: the RPS argument
sets the sleep interval, and request latency adds to the actual interval.
Its total counter counts selected scenarios, not every individual HTTP call.

**Healthy baseline**

```bash
bash app/loadgen/run.sh 5 30
```

```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=40 success=40 fail=0 error_rate=0%
[10s] requests=41 success=41 fail=0 error_rate=0%
[10s] requests=42 success=42 fail=0 error_rate=0%
[10s] requests=43 success=43 fail=0 error_rate=0%
[20s] requests=79 success=79 fail=0 error_rate=0%
[20s] requests=80 success=80 fail=0 error_rate=0%
[20s] requests=81 success=81 fail=0 error_rate=0%
---
Done. total=115 success=115 fail=0 error_rate=0%
```

**Outage during load**

The same command was started again. Approximately ten seconds after launch,
payments was stopped from another terminal/tool session.

```bash
bash app/loadgen/run.sh 5 30
```

```bash
docker compose -f app/docker-compose.yaml stop payments
```

The stop command was issued at 2026-09-13 12:17:40 UTC.

```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 30s
---
[10s] requests=38 success=38 fail=0 error_rate=0%
[10s] requests=39 success=39 fail=0 error_rate=0%
[10s] requests=40 success=40 fail=0 error_rate=0%
[10s] requests=41 success=41 fail=0 error_rate=0%
[20s] requests=77 success=72 fail=5 error_rate=6.4%
[20s] requests=78 success=73 fail=5 error_rate=6.4%
[20s] requests=79 success=74 fail=5 error_rate=6.3%
[20s] requests=80 success=75 fail=5 error_rate=6.2%
---
Done. total=116 success=106 fail=10 error_rate=8.6%
```

| Scenario | Duration parameter | RPS parameter | Total scenarios | Success | Fail | Reported error rate |
|---|---:|---:|---:|---:|---:|---:|
| Healthy | 30 s | 5 | 115 | 115 | 0 | 0% |
| Payments stopped at approximately 10 s | 30 s | 5 | 116 | 106 | 10 | 8.6% |

The outage run had no failures in its first ten-second progress messages, then
five failures around twenty seconds and ten by completion. Listing and
reservation creation remained available, so the failure rate was far below
100%. Exact rates depend on the randomly chosen workload mix.

The progress rates are cumulative; repeated timestamps are emitted by the
provided script when several iterations occur within the same second.

**Recovery**

```bash
docker compose -f app/docker-compose.yaml start payments
```

```bash
curl -sS -i --max-time 10 http://localhost:3080/health
```

```http
HTTP/1.1 200 OK
date: Sun, 13 Sep 2026 12:18:09 GMT
server: uvicorn
content-length: 89
content-type: application/json

{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

## Task 2 — Graceful Degradation

**Completed and verified against the running application.**

The payment handler now catches httpx.ConnectError specifically and returns
HTTP 503 with an actionable message and the reservation ID. The message tells
the user to retry before expiry, rather than promising an unlimited hold.
Other error handlers remain in place.

**Code change**

```bash
git diff -- app/gateway/main.py
```

```diff
diff --git a/app/gateway/main.py b/app/gateway/main.py
index c86db33..ee64ea7 100644
--- a/app/gateway/main.py
+++ b/app/gateway/main.py
@@ -336,6 +336,18 @@ async def pay_reservation(reservation_id: str):
         raise HTTPException(504, "Payment service timeout")
     except httpx.HTTPStatusError as e:
         raise HTTPException(e.response.status_code, "Payment failed")
+    except httpx.ConnectError:
+        return JSONResponse(
+            status_code=503,
+            content={
+                "error": "payments_unavailable",
+                "message": (
+                    "Payment service is temporarily unavailable. "
+                    "Please retry before your reservation expires."
+                ),
+                "reservation_id": reservation_id,
+            },
+        )
     except Exception as e:
         log.error(f"payment error: {e}")
         raise HTTPException(502, "Payment service unavailable")
```

**Deploy the change**

```bash
docker compose -f app/docker-compose.yaml up -d --build --no-deps gateway
```

```text
Image app-gateway Built
 Container app-gateway-1 Recreate
 Container app-gateway-1 Recreated
 Container app-gateway-1 Starting
 Container app-gateway-1 Started
```

**Stop payments**

```bash
docker compose -f app/docker-compose.yaml stop payments
```

```text
Container app-payments-1 Stopping
 Container app-payments-1 Stopped
```

**Verify listing and individual event retrieval**

```bash
curl -sS -i --max-time 10 http://localhost:3080/events
```

```http
HTTP/1.1 200 OK
date: Sun, 13 Sep 2026 12:18:34 GMT
server: uvicorn
content-length: 723
content-type: application/json

[{"id":1,"name":"Go Conference 2026","venue":"Main Hall A","date":"2026-09-15T09:00:00+00:00","total_tickets":100,"price_cents":5000,"available":92},{"id":4,"name":"Python Workshop","venue":"Lab 301","date":"2026-09-22T14:00:00+00:00","total_tickets":25,"price_cents":2000,"available":21},{"id":2,"name":"SRE Meetup","venue":"Room 204","date":"2026-10-01T18:00:00+00:00","total_tickets":30,"price_cents":0,"available":25},{"id":5,"name":"Kubernetes Deep Dive","venue":"Auditorium B","date":"2026-10-10T10:00:00+00:00","total_tickets":80,"price_cents":8000,"available":76},{"id":3,"name":"Cloud Native Summit","venue":"Expo Center","date":"2026-11-20T10:00:00+00:00","total_tickets":500,"price_cents":15000,"available":496}]
```

```bash
curl -sS -i --max-time 10 http://localhost:3080/events/3
```

```http
HTTP/1.1 200 OK
date: Sun, 13 Sep 2026 12:18:34 GMT
server: uvicorn
content-length: 150
content-type: application/json

{"id":3,"name":"Cloud Native Summit","venue":"Expo Center","date":"2026-11-20T10:00:00+00:00","total_tickets":500,"price_cents":15000,"available":496}
```

**Create a reservation while payments is stopped**

```bash
curl -sS -i --max-time 10 -X POST http://localhost:3080/events/3/reserve -H 'Content-Type: application/json' -d '{"quantity":1}'
```

```http
HTTP/1.1 200 OK
date: Sun, 13 Sep 2026 12:18:34 GMT
server: uvicorn
content-length: 128
content-type: application/json

{"reservation_id":"64c3ad94-4ddc-4160-95e1-d9b6a31b8660","event_id":3,"quantity":1,"total_cents":15000,"expires_in_seconds":300}
```

**Attempt payment**

```bash
curl -sS -i --max-time 10 -X POST http://localhost:3080/reserve/64c3ad94-4ddc-4160-95e1-d9b6a31b8660/pay
```

```http
HTTP/1.1 503 Service Unavailable
date: Sun, 13 Sep 2026 12:18:34 GMT
server: uvicorn
content-length: 190
content-type: application/json

{"error":"payments_unavailable","message":"Payment service is temporarily unavailable. Please retry before your reservation expires.","reservation_id":"64c3ad94-4ddc-4160-95e1-d9b6a31b8660"}
```

**Confirm degraded health**

```bash
curl -sS -i --max-time 10 http://localhost:3080/health
```

```http
HTTP/1.1 503 Service Unavailable
date: Sun, 13 Sep 2026 12:18:34 GMT
server: uvicorn
content-length: 92
content-type: application/json

{"status":"degraded","checks":{"events":"ok","payments":"down","circuit_payments":"CLOSED"}}
```

**Restore payments and check health**

```bash
docker compose -f app/docker-compose.yaml start payments
```

```text
Container app-payments-1 Starting
 Container app-payments-1 Started
```

```bash
curl -sS -i --max-time 10 http://localhost:3080/health
```

```http
HTTP/1.1 200 OK
date: Sun, 13 Sep 2026 12:18:35 GMT
server: uvicorn
content-length: 89
content-type: application/json

{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

**Retry payment for the same, still-valid reservation**

```bash
curl -sS -i --max-time 10 -X POST http://localhost:3080/reserve/64c3ad94-4ddc-4160-95e1-d9b6a31b8660/pay
```

```http
HTTP/1.1 200 OK
date: Sun, 13 Sep 2026 12:18:36 GMT
server: uvicorn
content-length: 118
content-type: application/json

{"order_id":"64c3ad94-4ddc-4160-95e1-d9b6a31b8660","event_id":3,"quantity":1,"total_cents":15000,"status":"confirmed"}
```

Listing, event retrieval, and reservation creation returned 200 during the
outage. Payment returned the intended 503 instead of the original 502. After
recovery, the same reservation was successfully confirmed with HTTP 200.

## Task 3 — GitHub Community

**The student confirmed that all required actions were completed.**

- [x] Star [the course repository](https://github.com/inno-devops-labs/SRE-Intro).
- [x] Star [simple-container-com/api](https://github.com/simple-container-com/api).
- [x] Follow [Cre-eD](https://github.com/Cre-eD), [Naghme98](https://github.com/Naghme98), and [pierrepicaud](https://github.com/pierrepicaud).
- [x] Follow classmates [kujifined](https://github.com/kujifined), [Troshkins](https://github.com/Troshkins), and [L10nff](https://github.com/L10nff).


## Bonus — Resource Usage Under Load

**Completed with measurements in all three scenarios.**

The bonus runs used the Task 2 gateway. Both loaded scenarios used the supplied
generator with parameters 10 and 30 seconds. One docker stats snapshot was taken
approximately nine seconds after each load launch; these are point samples,
not averages or peak measurements. The same five application containers were
selected explicitly to exclude unrelated Docker workloads.

### B.1. Idle

All services were running with no load generator active.

```bash
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}" app-gateway-1 app-events-1 app-payments-1 app-postgres-1 app-redis-1
```

```text
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-gateway-1    0.45%     39.48MiB / 7.748GiB   10.8kB / 10.5kB   2
app-events-1     0.31%     44.45MiB / 7.748GiB   351kB / 469kB     2
app-payments-1   0.36%     34.31MiB / 7.748GiB   2.1kB / 1.11kB    2
app-postgres-1   0.02%     25.28MiB / 7.748GiB   185kB / 208kB     8
app-redis-1      2.24%     10.93MiB / 7.748GiB   55.2kB / 23.5kB   6
```

### B.2. Load with normal payment settings

```bash
bash app/loadgen/run.sh 10 30
```

```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 10 | Duration: 30s
---
[10s] requests=66 success=63 fail=3 error_rate=4.5%
[10s] requests=67 success=64 fail=3 error_rate=4.4%
[10s] requests=68 success=65 fail=3 error_rate=4.4%
[10s] requests=69 success=66 fail=3 error_rate=4.3%
[10s] requests=70 success=67 fail=3 error_rate=4.2%
[10s] requests=71 success=68 fail=3 error_rate=4.2%
[20s] requests=130 success=119 fail=11 error_rate=8.4%
[20s] requests=131 success=120 fail=11 error_rate=8.3%
[20s] requests=132 success=121 fail=11 error_rate=8.3%
[20s] requests=133 success=122 fail=11 error_rate=8.2%
[20s] requests=134 success=123 fail=11 error_rate=8.2%
[20s] requests=135 success=124 fail=11 error_rate=8.1%
---
Done. total=195 success=179 fail=16 error_rate=8.2%
```

While the generator was active:

```bash
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}" app-gateway-1 app-events-1 app-payments-1 app-postgres-1 app-redis-1
```

```text
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-gateway-1    4.38%     39.95MiB / 7.748GiB   114kB / 113kB     2
app-events-1     2.38%     45.41MiB / 7.748GiB   450kB / 603kB     2
app-payments-1   0.30%     34.56MiB / 7.748GiB   6.04kB / 3.92kB   2
app-postgres-1   0.64%     25.35MiB / 7.748GiB   241kB / 271kB     8
app-redis-1      1.49%     10.41MiB / 7.748GiB   70.9kB / 30.4kB   6
```

The 8.2% error rate was not a payment outage: some reservations hit HTTP 409
after earlier experiments consumed/held tickets. Representative gateway logs:

```bash
docker compose -f app/docker-compose.yaml logs --since 2m --tail=120 gateway
```

```text
gateway-1  | INFO:     192.168.65.1:25833 - "POST /events/2/reserve HTTP/1.1" 409 Conflict
gateway-1  | INFO:     192.168.65.1:23882 - "POST /events/2/reserve HTTP/1.1" 409 Conflict
gateway-1  | INFO:     192.168.65.1:27968 - "POST /events/4/reserve HTTP/1.1" 409 Conflict
gateway-1  | INFO:     192.168.65.1:61024 - "POST /events/4/reserve HTTP/1.1" 409 Conflict
```

### B.3. Load with injected failures and delay

```bash
PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500 docker compose -f app/docker-compose.yaml up -d payments
```

```text
Container app-payments-1 Recreate
 Container app-payments-1 Recreated
 Container app-payments-1 Starting
 Container app-payments-1 Started
```

The environment change recreated the payments container. Verify applied values:

```bash
curl -sS -i --max-time 10 http://localhost:8082/health
```

```http
HTTP/1.1 200 OK
date: Sun, 13 Sep 2026 12:20:31 GMT
server: uvicorn
content-length: 56
content-type: application/json

{"status":"healthy","failure_rate":0.3,"latency_ms":500}
```

Run the same load:

```bash
bash app/loadgen/run.sh 10 30
```

```text
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 10 | Duration: 30s
---
[10s] requests=51 success=44 fail=7 error_rate=13.7%
[10s] requests=52 success=45 fail=7 error_rate=13.4%
[10s] requests=53 success=46 fail=7 error_rate=13.2%
[10s] requests=54 success=47 fail=7 error_rate=12.9%
[10s] requests=55 success=48 fail=7 error_rate=12.7%
[10s] requests=56 success=48 fail=8 error_rate=14.2%
[20s] requests=106 success=91 fail=15 error_rate=14.1%
[20s] requests=107 success=92 fail=15 error_rate=14.0%
[20s] requests=108 success=93 fail=15 error_rate=13.8%
[20s] requests=109 success=94 fail=15 error_rate=13.7%
[20s] requests=110 success=95 fail=15 error_rate=13.6%
[20s] requests=111 success=96 fail=15 error_rate=13.5%
[20s] requests=112 success=97 fail=15 error_rate=13.3%
---
Done. total=170 success=150 fail=20 error_rate=11.7%
```

While the generator was active:

```bash
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}" app-gateway-1 app-events-1 app-payments-1 app-postgres-1 app-redis-1
```

```text
NAME             CPU %     MEM USAGE / LIMIT     NET I/O           PIDS
app-gateway-1    4.14%     40.25MiB / 7.748GiB   380kB / 376kB     2
app-events-1     1.89%     44.94MiB / 7.748GiB   677kB / 908kB     2
app-payments-1   0.32%     36.05MiB / 7.748GiB   3.86kB / 2.65kB   2
app-postgres-1   0.64%     25.53MiB / 7.748GiB   371kB / 422kB     8
app-redis-1      1.06%     10.74MiB / 7.748GiB   96.9kB / 41.1kB   6
```

Payment logs confirmed that latency and failures were actually injected:

```bash
docker compose -f app/docker-compose.yaml logs --tail=100 payments
```

```text
payments-1  | {"time":"2026-09-13 12:20:45,544","level":"INFO","service":"payments","msg":"Injecting 500ms latency for 18117efe-258d-4304-9e9a-f9cd7c0ba5d5"}
payments-1  | {"time":"2026-09-13 12:20:47,760","level":"INFO","service":"payments","msg":"Injecting 500ms latency for e13db435-e724-44fa-b2ee-d196201b4e50"}
payments-1  | {"time":"2026-09-13 12:20:53,990","level":"INFO","service":"payments","msg":"Injecting 500ms latency for b03ff274-0872-44a5-bd39-23b255c3f30f"}
payments-1  | {"time":"2026-09-13 12:20:54,491","level":"WARNING","service":"payments","msg":"Payment failed (injected) for b03ff274-0872-44a5-bd39-23b255c3f30f"}
payments-1  | INFO:     172.18.0.6:53576 - "POST /charge HTTP/1.1" 500 Internal Server Error
```

### Resource comparison

| Service | Idle CPU | Normal-load CPU | Chaos-load CPU | Idle memory (MiB) | Normal-load memory (MiB) | Chaos-load memory (MiB) |
|---|---:|---:|---:|---:|---:|---:|
| gateway | 0.45% | 4.38% | 4.14% | 39.48 | 39.95 | 40.25 |
| events | 0.31% | 2.38% | 1.89% | 44.45 | 45.41 | 44.94 |
| payments | 0.36% | 0.30% | 0.32% | 34.31 | 34.56 | 36.05 |
| postgres | 0.02% | 0.64% | 0.64% | 25.28 | 25.35 | 25.53 |
| redis | 2.24% | 1.49% | 1.06% | 10.93 | 10.41 | 10.74 |

| Loaded scenario | Total scenarios | Success | Fail | Reported error rate |
|---|---:|---:|---:|---:|
| Normal payments | 195 | 179 | 16 | 8.2% |
| 30% payment failures and 500 ms payment delay | 170 | 150 | 20 | 11.7% |

**Memory:** events used the most memory in every snapshot. Its memory rose from
44.45 MiB idle to 45.41 MiB under normal load, then measured 44.94 MiB in the
chaos run; it maintains the database pool and Redis client and handles event
and reservation operations. These snapshots alone do not establish a memory leak.

**CPU:** gateway used the most CPU in both loaded samples (4.38% and 4.14%).
Every generated request passes through it, including proxy calls and middleware.
The higher idle Redis sample illustrates why a single snapshot should not be
treated as a stable average.

**Effect on gateway:** gateway memory increased slightly from 39.95 to 40.25 MiB
between normal and chaos load, while CPU decreased from 4.38% to 4.14%.
Waiting for a slow payment keeps request state alive but does not necessarily
increase CPU. The sequential generator completed fewer scenarios (170 versus
195); this is consistent with added waiting time but is not an isolated causal
measurement.

**Limitations:** the database and Redis were not reset between runs. Reservation
conflicts and a random request mix confound comparisons, so the full 11.7% error
rate cannot be attributed to injected payment failures. Code inspection also
shows that held-ticket counters are incremented without a matching decrement
on confirmation or reservation expiry; accumulated counters can cause additional
409 responses. No unrelated application change was made for this bonus.
Payments was recreated when its environment changed, so cumulative network I/O
is not directly comparable across its snapshots.

### Restore normal operation

```bash
PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0 docker compose -f app/docker-compose.yaml up -d payments
```

```text
Container app-payments-1 Recreate
 Container app-payments-1 Recreated
 Container app-payments-1 Starting
 Container app-payments-1 Started
```

```bash
curl -sS -i --max-time 10 http://localhost:8082/health
```

```http
HTTP/1.1 200 OK
date: Sun, 13 Sep 2026 12:21:47 GMT
server: uvicorn
content-length: 54
content-type: application/json

{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

```bash
curl -sS -i --max-time 10 http://localhost:3080/health
```

```http
HTTP/1.1 200 OK
date: Sun, 13 Sep 2026 12:21:46 GMT
server: uvicorn
content-length: 89
content-type: application/json

{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

Final container status:

```bash
docker compose -f app/docker-compose.yaml ps
```

```text
NAME             IMAGE                                                                     COMMAND                  SERVICE    CREATED          STATUS                    PORTS
app-events-1     sha256:c8b561af776ec8c6ffcfd61f6d23d70822fee42db7f0186dc6f518882e9cf8dd   "uvicorn main:app --…"   events     39 minutes ago   Up 12 minutes             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1    app-gateway                                                               "uvicorn main:app --…"   gateway    3 minutes ago    Up 3 minutes              0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-payments-1   app-payments                                                              "uvicorn main:app --…"   payments   24 seconds ago   Up 23 seconds             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1   postgres:17-alpine                                                        "docker-entrypoint.s…"   postgres   39 minutes ago   Up 11 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-redis-1      redis:7-alpine                                                            "docker-entrypoint.s…"   redis      39 minutes ago   Up 11 minutes (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

## Conclusions

The failure experiments showed different blast radii. Payments failure preserved
browsing and reservations, Redis failure preserved browsing but interrupted
reservations and confirmation, and events/PostgreSQL failures interrupted both
browsing and reservations. In several outages the mock charge succeeded before
order confirmation failed, exposing a consistency gap between those operations.

The original gateway detected unhealthy dependencies, but its events: down
label did not identify whether events itself or a downstream dependency failed.
Under mixed load, stopping payments increased the reported error rate from 0%
to 8.6%, while unaffected operations continued.

Task 2 replaced the generic unavailable-payment 502 with an actionable 503 and
preserved a successful retry for the same reservation after recovery. Resource
measurements showed events using the most memory and gateway using the most
CPU under load. The chaos comparison also exposed why business-level 409
responses and changing data state must be separated from infrastructure failures.

All containers remain running, payments has failure_rate 0.0 and latency_ms 0,
and gateway health is healthy. The gateway code change remains applied locally.

## Submission readiness

- [x] Task 1: deployment, full purchase, architecture, four failures, and load evidence.
- [x] Task 2: code diff, working reservations, clear 503, and successful retry.
- [x] Task 3: all actions confirmed by the student; independent verification is partial as documented.
- [x] Bonus: three resource snapshots, load output, analysis, and restoration.
- [x] Report reviewed against the Lab 1 acceptance criteria.
- [ ] Resolve the independently unverified GitHub actions noted in Task 3.
- [ ] Submit the pull request URL in Moodle before the deadline.
