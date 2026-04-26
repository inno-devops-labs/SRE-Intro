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
   | Component Killed | Events List | Reserve | Pay | Health Check | User Impact |
   |-----------------|-------------|---------|-----|--------------|-------------|
   | payments        |             |         |     |              |             |
   | events          |             |         |     |              |             |
   | redis           |             |         |     |              |             |
   | postgres        |             |         |     |              |             |
   ```

6. Load generator output showing the error rate spike when payments is killed

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

---

## Task 3 — GitHub Community Engagement (1 pt)

**Objective:** Explore GitHub's social features that support collaboration and discovery.

**Actions Required:**
1. **Star** the course repository
2. **Star** the [simple-container-com/api](https://github.com/simple-container-com/api) project — a promising open-source tool for container management
3. **Follow** your professor and TAs on GitHub:
   - Professor: [@Cre-eD](https://github.com/Cre-eD)
   - TA: [@marat-biriushev](https://github.com/marat-biriushev)
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
   - [ ] Task 2 done — graceful degradation in gateway
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
