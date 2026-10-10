# Lab 1 Submission - SRE Philosophy: Deploy, Break, Understand

## Task 1 - Deploy & Break QuickTicket

### Deployment

Ran `docker compose up --build -d` from `app/`. All 5 containers were running: `gateway`, `events`, `payments`, `postgres` (healthy), `redis` (healthy). Gateway is available on port 3080.

### Critical path

`GET /events` returned the seeded events. One reservation and the corresponding payment completed:

```json
{"reservation_id":"9ecdd053-5976-402e-9e21-ad177e3952be","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
{"order_id":"9ecdd053-5976-402e-9e21-ad177e3952be","event_id":1,"quantity":1,"total_cents":5000,"status":"confirmed"}
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

### Dependency map

```text
client -> gateway -> events -> postgres
                    |-> redis
                 |-> payments
```

### Failure table

| Component stopped | Events list | Reserve | Pay | Health check | User impact |
|---|---|---|---|---|---|
| payments | 200 | 200 | 503 `payments_unavailable` | 503, payments down | Browsing and reservation continue; payment can be retried. |
| events | 502 | 502 | confirmation cannot complete | 503, events down | Ticket workflow is unavailable. |
| redis | 200 | fails/times out | reservation cannot be confirmed | events degrades | Ticket data remains readable, but reservation state is unavailable. |
| postgres | events queries fail | fails | confirmation fails | events degrades | Inventory and durable order storage are unavailable. |

After PostgreSQL was restarted, the existing events connection pool kept closed connections; restarting `events` restored it. This is a failure-mode observation of the supplied application.

### Load generator

Run this before opening the PR and paste the two final `Done.` lines below:

```bash
./app/loadgen/run.sh 5 30
# docker compose stop payments
# docker compose start payments
```

## Task 2 - Graceful degradation

The payment handler catches connection errors and timeouts from payments, then returns a clear 503. With payments stopped, reserve returned 200 and pay returned:

```json
{"error":"payments_unavailable","message":"Payment service is temporarily down. Your reservation is held - try again in a few minutes.","reservation_id":"bb28d2b6-ed60-47fa-897a-5dbbc14d56d4"}
```

## Task 3 - GitHub Community

Do not want to follow unfamiliar classmates =(
