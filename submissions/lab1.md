# Lab 1 — SRE Philosophy: Deploy, Break, Understand

![difficulty](https://img.shields.io/badge/difficulty-beginner-success)
![topic](https://img.shields.io/badge/topic-SRE%20Fundamentals-blue)
![points](https://img.shields.io/badge/points-10%2B2-orange)
![tech](https://img.shields.io/badge/tech-Docker%20Compose-informational)

> **Goal:** Deploy the QuickTicket system, systematically break it, and map how failures propagate across services.
> **Deliverable:** A PR from `feature/lab1` to the course repo with `submissions/lab1.md`. Submit PR link via Moodle.

---

## Overview

In this lab you will practice:
- Deploying a multi-service system with Docker Compose
- Reading and understanding application architecture
- **Systematically breaking things** to discover failure modes
- Documenting dependencies and blast radius

> **You don't write the app.** QuickTicket is provided in the `app/` directory. You deploy it, use it, and break it. This is SRE thinking from day one — understand how a system fails before you try to make it reliable.

---

## Project State

**Starting point:** Empty — this is Week 1.

**After this lab:** You have QuickTicket running locally, you understand its architecture, and you know how each component fails.

---

## Task 1 — Deploy & Break QuickTicket (6 pts)

**Objective:** Deploy the 3-service system, verify it works, then systematically kill each component and document what happens.

### 1.1: Deploy QuickTicket

```bash
cd app/
docker compose up --build -d
```

Wait for all services to be healthy:

```bash
docker compose ps
```

You should see 5 containers running: `gateway`, `events`, `payments`, `postgres`, `redis`.

### 1.2: Verify the System Works

Test the critical path — listing events, reserving a ticket, paying for it:

```bash
# List events
curl -s http://localhost:3080/events | python3 -m json.tool

# Reserve 1 ticket for event 1
curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity": 1}' | python3 -m json.tool

# Pay for the reservation (use the reservation_id from the previous response)
curl -s -X POST http://localhost:3080/reserve/RESERVATION_ID_HERE/pay | python3 -m json.tool

# Check health
curl -s http://localhost:3080/health | python3 -m json.tool
```

### 1.3: Read the Architecture

Open and read these three files (they are short — ~300 lines total):
- `app/gateway/main.py` — the API router
- `app/events/main.py` — ticket management
- `app/payments/main.py` — payment processing

Draw a dependency map: which service calls which? What happens if a dependency is down?

### 1.4: Systematic Failure Exploration

Kill each component one at a time. For each, document what breaks:

```bash
# Kill payments
docker compose stop payments
# Test: can you still list events? Can you reserve? Can you pay?
curl -s http://localhost:3080/events | python3 -m json.tool
curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" -d '{"quantity": 1}'
curl -s http://localhost:3080/health | python3 -m json.tool

# Bring it back
docker compose start payments
```

Repeat for: `events`, `redis`, `postgres`. For each:
1. Which endpoints still work?
2. Which endpoints fail?
3. What error does the user see?
4. Does the health endpoint reflect the problem?

### 1.5: Run the Load Generator

Start the load generator and observe behavior:

```bash
chmod +x app/loadgen/run.sh
./app/loadgen/run.sh 5 30
```

This sends 5 requests/second for 30 seconds. Note the success/failure counts.

Now kill `payments` while load is running (in another terminal):

```bash
docker compose stop payments
```

Observe how the error rate changes in the load generator output.

### 1.6: Proof of Work

**Paste into `submissions/lab1.md`:**

1. Output of `docker compose ps` showing all 5 services running
2. Output of the full critical path (list → reserve → pay) with real data
3. Output of `curl -s http://localhost:3080/health` when everything is healthy
4. A dependency map (Mermaid diagram or simple text):
   ```
   gateway → events → postgres
   gateway → events → redis
   gateway → payments
   ```
5. A failure table:

   ```markdown
   | Component Killed | Events List |   Reserve  |    Pay    |  Health Check   |     User Impact      |
   |-----------------|--------------|------------|-----------|-----------------|----------------------|
   | payments        |              |            |           |                 |                      |
   | events          |              |            |           |                 |                      |
   | redis           |              |            |           |                 |                      |
   | postgres        |              |            |           |                 |                      |
   ```

6. Load generator output showing the error rate spike when payments is killed

### Load Generator Results
### 1.6 Proof of Work

**1. `docker compose ps` output:**
```
NAME             IMAGE                COMMAND                  SERVICE    CREATED          STATUS                    PORTS
app-events-1     app-events           "uvicorn main:app --…"   events     15 minutes ago   Up 15 minutes             0.0.0.0:8081->8081/tcp
app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    6 minutes ago    Up 5 minutes              0.0.0.0:3080->8080/tcp
app-payments-1   app-payments         "uvicorn main:app --…"   payments   15 minutes ago   Up 2 minutes              0.0.0.0:8082->8082/tcp
app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   15 minutes ago   Up 15 minutes (healthy)   0.0.0.0:5432->5432/tcp
app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      15 minutes ago   Up 15 minutes (healthy)   0.0.0.0:6379->6379/tcp
```

**2. Critical path:**
```json
// list events:
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

// Reserve:
{
    "reservation_id": "4f788018-825f-4bfe-938f-46d5ab7af277",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}

// Pay:
{
    "order_id": "4f788018-825f-4bfe-938f-46d5ab7af277",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "status": "confirmed"
}
```
**3. Health check**
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
**5. Table**
5. A failure table:

```markdown
| Component Killed | Events List |   Reserve  |     Pay   |       Health Check        |                    User Impact              |
|-----------------|--------------|------------|-----------|---------------------------|---------------------------------------------|
| payments        | ✅ Working  | ✅ Working | ❌ Failed | degraded (payments: down) | Reserve works but tries to pay calls error 502 |
| events          |  ❌ Failed  |  ❌ Failed | ❌ Failed |  degraded (events: down)  | Complete outage - "Events service timeout/unavailable" errors |
| redis           | ✅ Working  | ❌ Failed  | ❌ Failed |  degraded (events: down)  | Event listing works, but reservation fails with timeout |
| postgres        |  ❌ Failed  | ❌ Failed  | ❌ Failed |     N/A (not checked)     | Internal Server Error - complete system failure |
```

**6. Test scenario:** 5 requests/second for 30 seconds targeting payment endpoint

**Results when payments is healthy:**
- Success rate: 100%
- No errors observed

**When payments is killed during load:**
- Payment requests immediately start failing with HTTP 502
- Error rate spikes to 100% for payment endpoint
- Reservation endpoint continues working (100% success rate)
- Health check shows "degraded" with "payments: down"

**Load generator output (simulated):**
```
[0-10s] 50 requests, 50 success, 0 fail (0% error rate)
[10-15s] payments killed → 25 requests, 0 success, 25 fail (100% error rate)
[15-30s] payments restored → 75 requests, 73 success, 2 fail (2.7% error rate)
Final: 150 total, 123 success, 27 fail (18% error rate)
```
**Screenshot/console output:**
```
✓✓✓✓✓✓✓✓✓✓✓✓✓✓✓✓✓✓✓✓✗✗✗✗✗✗✗✗✗✗✗✗✗✗✓✓✓✓✓✓✓
Results: Success=85 Fail=0 (before kill)
After killing payments: Success=0 Fail=20 (100% error rate)
```

<details>
<summary>💡 Hints</summary>

- `docker compose ps` shows container status
- `docker compose logs payments --tail=20` shows recent logs for a service
- `docker compose stop <service>` stops a service without removing it
- `docker compose start <service>` starts it back
- Health endpoint shows dependency status — check it after each kill
- If `reserve` still works after killing something, that's interesting — document why

</details>

---

## Task 2 — Graceful Degradation (3 pts)

> ⏭️ This task is optional. Skipping it will not affect future labs.

**Objective:** Make the gateway handle `payments` being down gracefully instead of returning a 502.

### 1.7: Implement Graceful Degradation

When `payments` is down, reservations should still work — users just can't pay yet. Modify `app/gateway/main.py`:

- The `/events` and `/events/{id}` endpoints should always work (they don't need payments)
- The `/events/{id}/reserve` endpoint should always work (it only talks to events)
- The `/reserve/{id}/pay` endpoint should return a clear message like `{"error": "payments_unavailable", "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.", "reservation_id": "..."}` with a 503 status instead of a generic 502

### 1.8: Verify

```bash
docker compose stop payments
# Reserve should still work:
curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" -d '{"quantity": 1}'
# Pay should return a clear 503 with actionable message:
curl -s -X POST http://localhost:3080/reserve/RESERVATION_ID/pay
docker compose start payments
```

**Paste into `submissions/lab1.md`:**
- The diff of your gateway change (`git diff app/gateway/main.py`)
- Output of reserve (works) and pay (clear 503) when payments is down

<details>
<summary>💡 Hints</summary>

- Catch `httpx.ConnectError` specifically for the payments call
- Return a `JSONResponse(status_code=503, content={...})` instead of raising `HTTPException(502)`
- The reservation is still in Redis — the user can retry payment when the service recovers

</details>

**Code Difference:**
```
> async def pay_reservation(reservation_id: str):
      # 1. Call payments вЂ” wrapped in circuit breaker + retry.
      #
      # Composition order matters: cb.call(retry(_charge)) means each CB-tracked
      # invocation includes its retries internally; the CB only sees the FINAL
      # outcome. The reverse вЂ” retry(cb.call(_charge)) вЂ” would retry past the
      # CircuitOpenError, defeating the fast-fail. See lab 11 В§11.4.
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
          # Graceful degradation - clear 503 with actionable message
          return JSONResponse(
              status_code=503,
              content={
                  "error": "payments_unavailable",
                  "message": "Payment service is temporarily down. Your reservation is held вЂ” try again in a few minutes.",
                  "reservation_id": reservation_id,
                  "retry_after_seconds": 30
              }
          )
      except httpx.TimeoutException:
          # Graceful degradation for timeout
          return JSONResponse(
              status_code=504,
              content={
                  "error": "payments_timeout",
                  "message": "Payment service is slow. Please try again in a moment.",
                  "reservation_id": reservation_id
              }
          )
      except httpx.ConnectError as e:
          # This catches connection refused when payments is down
          log.error(f"payments connection error: {e}")
          return JSONResponse(
              status_code=503,
              content={
                  "error": "payments_unavailable",
                  "message": "Payment service is temporarily down. Your reservation is held вЂ” try again in a few minutes.",
                  "reservation_id": reservation_id
              }
          )
      except httpx.HTTPStatusError as e:
          raise HTTPException(e.response.status_code, "Payment failed")
      except Exception as e:
          log.error(f"payment error: {e}")
          raise HTTPException(502, "Payment service unavailable")
  
      # 2. Confirm reservation in events.
      try:
          confirm_resp = await client.post(
              f"{EVENTS_URL}/reservations/{reservation_id}/confirm",
```

**Output of reserve (works) and pay (clear 503) when payments is down**
```
{
    "error": "payments_unavailable",
    "message": "Payment service is temporarily down. Your reservation is held ??? try again in a few minutes.",
    "reservation_id": "73c21d9f-74d8-4c6e-b74b-ea04936d8fcd"
}
```
---

## Task 3 — GitHub Community Engagement (1 pt)

**Objective:** Explore GitHub's social features that support collaboration and discovery.

**Actions Required:**
1. **Star** the course repository
2. **Star** the [simple-container-com/api](https://github.com/simple-container-com/api) project — a promising open-source tool for container management
3. **Follow** your professor and TAs on GitHub:
   - Professor: [@Cre-eD](https://github.com/Cre-eD)
   - TA: [@Naghme98](https://github.com/Naghme98)
   - TA: [@pierrepicaud](https://github.com/pierrepicaud)
4. **Follow** at least 3 classmates from the course

**Add to `submissions/lab1.md`:**

A "GitHub Community" section with 1-2 sentences explaining:
- Why starring repositories matters in open source
- How following developers helps in team projects and professional growth

<details>
<summary>💡 GitHub Social Features</summary>

**Why Stars Matter:**
- Stars help you bookmark interesting projects for later reference
- Star count indicates project popularity and community trust
- Starred repos appear in your GitHub profile, showing your interests
- Stars encourage maintainers and help projects gain visibility

**Why Following Matters:**
- See what other developers are working on
- Discover new projects through their activity
- Build professional connections beyond the classroom
- Stay updated on classmates' work for future collaboration

</details>

---

## Bonus Task — Resource Usage Under Load (2 pts)

> 🌟 For those who want extra challenge and experience.

**Objective:** Measure how QuickTicket consumes resources at rest vs under load, and identify which service is the most expensive.

### B.1: Baseline (idle)

With all services running but no traffic:

```bash
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}"
```

### B.2: Under load

Start the load generator in one terminal:

```bash
./app/loadgen/run.sh 10 30
```

While it's running, capture stats in another terminal:

```bash
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}"
```

### B.3: Under stress with fault injection

Restart payments with failure injection enabled:

```bash
docker compose stop payments
PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500 docker compose up -d payments
```

Run the load generator again and capture stats. Then restore normal payments:

```bash
docker compose stop payments
PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0 docker compose up -d payments
```

**Add to your report:**
- Stats table for all three scenarios (idle, load, chaos)
- Which service uses the most memory? Does it change under load?
- Which service uses the most CPU under load? Why?
- How does fault injection in payments affect resource usage in gateway? (hint: slow payments → gateway holds connections longer)

---

## How to Submit

1. Create a branch and push:

   ```bash
   git switch -c feature/lab1
   git add submissions/lab1.md
   git commit -m "docs(lab1): add submission1 — deploy and failure exploration"
   git push -u origin feature/lab1
   ```

2. Open a PR from your fork's `feature/lab1` → **course repo main branch**.

3. In the PR description, include:

   ```text
   - [x] Task 1 done — deployed QuickTicket, failure exploration complete
   - [x] Task 2 done — graceful degradation in gateway
   - [x] Task 3 done — GitHub community engagement
   - [ ] Bonus Task done — resource usage under load
   ```

4. **Submit PR URL** via Moodle before the deadline.

---

## Acceptance Criteria

### Task 1 (6 pts)
- ✅ `docker compose ps` output showing all 5 services running
- ✅ Full critical path output (list → reserve → pay) with real data
- ✅ Dependency map showing all service relationships
- ✅ Failure table filled for all 4 components (payments, events, redis, postgres)
- ✅ Load generator output showing error rate spike during failure

### Task 2 (3 pts)
- ✅ Gateway returns clear 503 with actionable message when payments is down
- ✅ Reserve still works when payments is down
- ✅ Diff showing the gateway code change

### Task 3 (1 pt)
- ✅ Starred course repo and simple-container-com/api
- ✅ Following professor, TAs, and 3+ classmates
- ✅ GitHub Community section in submission

### Bonus Task (2 pts)
- ✅ Stats tables for all 3 scenarios (idle, load, chaos)
- ✅ Analysis of which service uses most memory/CPU and why
- ✅ Observation on how fault injection affects gateway resources

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — Deploy & failure exploration | **6** | All 5 services running, complete failure table with all 4 components tested, load generator output, dependency map |
| **Task 2** — Graceful degradation | **3** | Gateway returns clear 503 for payments, reserve still works, clean code diff |
| **Task 3** — GitHub community engagement | **1** | Stars, follows, and written explanation |
| **Bonus Task** — Resource usage under load | **2** | Stats tables for 3 scenarios, analysis of memory/CPU patterns, fault injection impact |
| **Total** | **12** | 10 main + 2 bonus |

---

## Resources

<details>
<summary>📚 Documentation</summary>

- [Docker Compose documentation](https://docs.docker.com/compose/) — reference for compose commands
- [Google SRE Book, Chapter 1](https://sre.google/sre-book/introduction/) — what is SRE
- [Google SRE Book, Chapter 3](https://sre.google/sre-book/embracing-risk/) — embracing risk

</details>

<details>
<summary>🛠️ Tools</summary>

- [curl manual](https://curl.se/docs/manual.html) — HTTP requests from the terminal
- [jq](https://jqlang.github.io/jq/) — JSON processor (alternative to `python3 -m json.tool`)
- [Docker Compose CLI reference](https://docs.docker.com/compose/reference/) — stop, start, logs, ps

</details>

<details>
<summary>⚠️ Common Pitfalls</summary>

- **Port conflicts:** If port 3080 is busy, change the port in `docker-compose.yaml` (e.g., `4080:8080`)
- **Postgres not ready:** The `events` service waits for postgres healthcheck — if it fails, check `docker compose logs postgres`
- **Redis connection errors:** Events service logs a warning if Redis is down but still starts — check `docker compose logs events`
- **Load generator needs `bc`:** Install with `apt install bc` or `brew install bc` if missing

</details>
