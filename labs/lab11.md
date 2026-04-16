# Lab 11 — Bonus: Advanced Microservice Patterns

![difficulty](https://img.shields.io/badge/difficulty-advanced-red)
![topic](https://img.shields.io/badge/topic-Microservice%20Patterns-blue)
![points](https://img.shields.io/badge/points-10%2B2.5-orange)
![tech](https://img.shields.io/badge/tech-Python%20%2B%20httpx-informational)

> **Goal:** Add a 4th service to QuickTicket, implement inter-service resilience patterns (retries, timeouts, circuit breaker), and test under failure conditions.
> **Deliverable:** A PR from `feature/lab11` with new service code, updated manifests, and `submissions/lab11.md`. Submit PR link via Moodle.

> 📖 **Read first:** `lectures/reading11.md` — covers the patterns you'll implement.

---

## Overview

In this lab you will:
- Add a **notifications** service to QuickTicket (4th microservice)
- Implement retry with exponential backoff in the gateway
- Add a timeout budget across the call chain
- Implement a basic circuit breaker
- Test all patterns under failure injection

---

## Task 1 — Add Notifications Service + Retries (6 pts)

**Objective:** Create a new service, wire it into the gateway, and add retry logic for inter-service calls.

### 11.1: Create the notifications service

Create `app/notifications/main.py` — a simple service that "sends" notifications (just logs them):

- `POST /notify` — accepts `{"event": "order_confirmed", "order_id": "..."}`, logs it, returns 200
- `GET /health` — returns health status
- Add fault injection: `NOTIFY_FAILURE_RATE` and `NOTIFY_LATENCY_MS` env vars (same pattern as payments)
- Expose Prometheus metrics (same pattern as other services)

Create `app/notifications/Dockerfile` and `app/notifications/requirements.txt` (same as payments).

### 11.2: Wire into gateway

Update `app/gateway/main.py` — after a successful payment confirmation, call the notifications service:

```python
# After confirm_reservation succeeds, notify (non-blocking, best-effort)
try:
    await client.post(f"{NOTIFICATIONS_URL}/notify", json={
        "event": "order_confirmed",
        "order_id": reservation_id,
    }, timeout=2.0)
except Exception as e:
    log.warning(f"Notification failed (non-critical): {e}")
```

Add `NOTIFICATIONS_URL` env var to docker-compose and K8s manifests.

### 11.3: Implement retry with backoff

Add retry logic to the gateway for the payments call (the critical path):

```python
import random, asyncio

async def call_with_retry(func, max_retries=3, base_delay=0.1):
    for attempt in range(max_retries):
        try:
            return await func()
        except (httpx.HTTPStatusError, httpx.ConnectError) as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            log.warning(f"Retry {attempt+1}/{max_retries} after {delay:.2f}s: {e}")
            await asyncio.sleep(delay)
```

### 11.4: Test under failure

```bash
# Start everything
docker compose up -d --build

# Inject 30% notification failures
NOTIFY_FAILURE_RATE=0.3 docker compose up -d notifications

# Run load generator — do orders still succeed even with notification failures?
./loadgen/run.sh 5 60
```

**Paste into `submissions/lab11.md`:**
1. Your notifications service code (or link to file in fork)
2. Your retry implementation
3. Loadgen results: with notifications failing, do orders still succeed?
4. Answer: "Why should notifications be non-blocking (fire-and-forget)? What would happen if the gateway waited for a notification response before returning to the user?"

---

## Task 2 — Circuit Breaker + Rate Limiting (4 pts)

> ⏭️ This task is optional.

**Objective:** Implement a circuit breaker in the gateway and basic rate limiting.

### 11.5: Circuit breaker for payments

Implement a simple circuit breaker for the gateway → payments call:
- After 5 consecutive failures, open the circuit
- While open, return 503 immediately (don't wait for timeout)
- After 30 seconds, try one request (half-open)
- If it succeeds, close the circuit

Test: inject 100% payment failures, observe circuit opening. Restore payments, observe circuit closing.

### 11.6: Rate limiting

Add a simple in-memory rate limiter to the gateway:
- Max 10 requests per second per endpoint
- Return 429 Too Many Requests when exceeded

Test with high-load burst from the load generator.

**Paste into `submissions/lab11.md`:**
- Circuit breaker implementation
- Evidence of circuit opening/closing under failure injection
- Rate limiting implementation with 429 responses under burst load

---

## Bonus Task — Service Mesh Exploration (2.5 pts)

> 🌟 For those who want extra challenge.

**Objective:** Explore what a service mesh provides and why some teams use them instead of in-code patterns.

Research and answer:
1. What is a service mesh (Istio, Linkerd)? How does it differ from implementing retries/circuit breakers in code?
2. What are the tradeoffs? (complexity vs functionality)
3. For QuickTicket's scale (4 services), would a service mesh be overkill? At what scale does it make sense?

Write your analysis in `submissions/lab11.md` (no implementation needed — this is a research bonus).

---

## How to Submit

```bash
git switch -c feature/lab11
git add app/notifications/ k8s/notifications.yaml submissions/lab11.md
git commit -m "feat(lab11): add notifications service and resilience patterns"
git push -u origin feature/lab11
```

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — Notifications + retries | **6** | Service created, wired, retries implemented, tested under failure |
| **Task 2** — Circuit breaker + rate limiting | **4** | Both patterns implemented and tested |
| **Bonus Task** — Service mesh analysis | **2.5** | Thoughtful comparison with concrete tradeoffs |
| **Total** | **12.5** | 10 main + 2.5 bonus |
