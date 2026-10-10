# Task 1 — Critical Path & Chaos

## Baseline

The QuickTicket stack was deployed using Docker Compose.

All required services were running:

* `gateway`
* `events`
* `payments`
* `postgres`
* `redis`

PostgreSQL and Redis reported `healthy` status.

### Critical path

The main user flow was successfully tested:

```text
GET /events
    ↓
POST /events/1/reserve
    ↓
POST /reserve/{reservation_id}/pay
```

The event used for testing was **Go Conference 2026** (`event_id=1`).

### List events

```bash
curl -s http://localhost:3080/events
```

The request returned the list of available events. For Go Conference 2026:

```text
id: 1
name: Go Conference 2026
total_tickets: 100
available: 100
price_cents: 5000
```

### Reserve

```bash
curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity":1}'
```

Result:

```json
{
    "reservation_id": "4cff7893-060d-4a06-a68b-44e83a6ec174",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}
```

### Pay

```bash
curl -s -X POST \
  "http://localhost:3080/reserve/4cff7893-060d-4a06-a68b-44e83a6ec174/pay" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Result:

```json
{
    "order_id": "4cff7893-060d-4a06-a68b-44e83a6ec174",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "status": "confirmed"
}
```

The complete critical path successfully completed.

---

## Chaos experiments

Each dependency was stopped individually and the effect on the application was observed.

| Dependency failure | Operation                | Result                        | HTTP status |
| ------------------ | ------------------------ | ----------------------------- | ----------: |
| `payments` stopped | `POST /events/1/reserve` | Reservation succeeded         |         200 |
| `payments` stopped | `POST /reserve/{id}/pay` | `Payment service unavailable` |         502 |
| `events` stopped   | `GET /events`            | `Events service unavailable`  |         502 |
| `events` stopped   | `POST /events/1/reserve` | `Events service unavailable`  |         502 |
| `redis` stopped    | `GET /events`            | Events returned successfully  |         200 |
| `redis` stopped    | `POST /events/1/reserve` | `Events service timeout`      |         504 |
| `postgres` stopped | `GET /events`            | `Events service unavailable`  |         502 |
| `postgres` stopped | `POST /events/1/reserve` | Internal Server Error         |         500 |

### Observations

The experiments showed different dependency failure behaviors.

`payments` is not required for creating a reservation, but it is required for completing payment. When `payments` was unavailable, the reservation could still be created, while the payment request returned HTTP 502.

`events` is a critical dependency for both listing events and creating reservations. When it was stopped, both operations returned HTTP 502.

`redis` is not required for listing events: `GET /events` continued to return HTTP 200 while Redis was unavailable. However, reservation creation timed out, showing that Redis is required for the reservation path.

`postgres` is a critical dependency of the Events Service. When PostgreSQL was stopped, listing events returned HTTP 502 and reservation creation resulted in HTTP 500.

---

## Load test

### Baseline load

The load generator was run at **5 RPS for 30 seconds**.

Result:

```text
Done. total=118 success=118 fail=0 error_rate=0%
```

The application handled the baseline load without errors.

### Failure spike

The same load test was performed while the `payments` service was stopped.

Result:

```text
Done. total=123 success=119 fail=4 error_rate=3.2%
```

During the failure window the error rate increased, reaching:

```text
error_rate=4.7%
```

The observed failures demonstrate that an unavailable payment dependency causes user-visible errors under load.

### Conclusion

The critical path works correctly when all dependencies are available. Chaos testing identified the following dependency relationships:

* `events` and `postgres` are critical for the event workflow.
* `redis` is critical for reservations but not for event listing.
* `payments` is only required when completing payment.
* Under normal load the system achieved **0% error rate**.
* When `payments` was intentionally stopped during load, the error rate increased to **3.2% overall**, demonstrating measurable degradation.

## Task 2 — Graceful degradation

When the Payments service is unavailable, the Gateway now returns a clear `503 Service Unavailable` response instead of a generic `502 Bad Gateway`.

The payment request handles the unavailable Payments service and returns a structured response:

```json
{
  "error": "payments_unavailable",
  "message": "Payment service is temporarily unavailable. Your reservation is held — try again in a few minutes.",
  "reservation_id": "5bc001f0-8a03-48db-bf6b-83391fa2d611"
}
```

The observed HTTP response was:

```text
HTTP/1.1 503 Service Unavailable
```

The reservation remains held in Redis, so the user can retry the payment after the Payments service recovers.

The `/events` and `/events/{id}` endpoints continue to work independently of the Payments service, and reservation creation also remains available. Only the payment operation is degraded.

The implementation catches payment-service connection failures and returns a structured `503` response with an actionable message and the reservation ID.

## GitHub Community

I starred the course repository and `simple-container-com/api`, followed the course professor and TAs, and followed at least three classmates. I also explored other students' GitHub profiles to learn more about the course community and their projects.

## Bonus — Resource Usage

### Idle

| Service    |   CPU |    Memory |
| ---------- | ----: | --------: |
| Gateway    | 0.25% | 38.45 MiB |
| Events     | 0.29% | 41.20 MiB |
| PostgreSQL | 0.04% | 23.73 MiB |
| Redis      | 0.78% |  9.57 MiB |
| Payments   | 0.25% | 34.99 MiB |

### Under 10 RPS load

| Service    |   CPU |    Memory |
| ---------- | ----: | --------: |
| Gateway    | 4.94% | 38.54 MiB |
| Events     | 2.14% | 41.25 MiB |
| PostgreSQL | 0.67% | 23.74 MiB |
| Redis      | 0.67% |  9.57 MiB |
| Payments   | 0.26% | 35.11 MiB |

The load generator was run with:

```bash
./loadgen/run.sh 10 30
```

### Under Payments fault injection

Payments was configured with a 30% failure rate and 500 ms artificial latency:

```bash
PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500 docker compose up -d payments
```

| Service    |   CPU |    Memory |
| ---------- | ----: | --------: |
| Gateway    | 4.55% | 38.64 MiB |
| Events     | 2.35% | 41.34 MiB |
| PostgreSQL | 0.66% | 23.74 MiB |
| Redis      | 0.66% |  9.32 MiB |
| Payments   | 0.30% | 34.96 MiB |

### Analysis

The Gateway consumed the most CPU under normal load, reaching 4.94%, because it is the main entry point and handles requests to downstream services. Events was the second-largest CPU consumer at 2.14%.

Memory usage remained very stable across all three scenarios. For example, Gateway memory increased only from 38.45 MiB at idle to 38.64 MiB during fault injection.

The Payments service remained lightweight even with fault injection: CPU usage was only 0.30%. The artificial failures and latency did not cause a significant resource spike.

Overall, the system did not show a cascade in CPU or memory consumption when the Payments service was degraded. The main resource consumer was the Gateway, while memory usage remained stable across the tested scenarios.
