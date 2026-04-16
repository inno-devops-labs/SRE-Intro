# Lab 2 — Containerization: Inspect, Understand, Optimize

![difficulty](https://img.shields.io/badge/difficulty-beginner-success)
![topic](https://img.shields.io/badge/topic-Containers-blue)
![points](https://img.shields.io/badge/points-10%2B2.5-orange)
![tech](https://img.shields.io/badge/tech-Docker-informational)

> **Goal:** Understand how QuickTicket containers work under the hood — images, layers, networking, operational commands — and optimize them.
> **Deliverable:** A PR from `feature/lab2` to the course repo with `submissions/lab2.md`. Submit PR link via Moodle.

---

## Overview

In this lab you will practice:
- Inspecting Docker images, layers, and sizes
- Using operational commands: `docker stats`, `docker logs`, `docker exec`, `docker inspect`
- Understanding Docker Compose networking and service discovery
- Optimizing Dockerfiles (`.dockerignore`, non-root user)

> **You already have QuickTicket running from Lab 1.** This lab digs into *how* it works, not *what* it does.

---

## Project State

**You should have from Lab 1:**
- QuickTicket deployed via `docker compose up --build` (5 containers running)
- Understanding of the 3-service architecture and failure modes

**This lab adds:**
- Deep understanding of how Docker containers work
- Optimized Dockerfiles (smaller, more secure)
- Operational debugging skills

---

## Task 1 — Docker Inspection & Operations (6 pts)

**Objective:** Use Docker operational commands to understand what's running, how much it costs, and how services find each other.

### 2.1: Image inspection

List all QuickTicket images and their sizes:

```bash
docker images | grep app
```

Pick the largest image and inspect its layers:

```bash
docker history app-gateway --no-trunc --format "table {{.CreatedBy}}\t{{.Size}}"
```

Answer: How many layers does the gateway image have? Which layer is the largest and why?

### 2.2: Container inspection

Find the IP address of each service on the Docker network:

```bash
docker inspect app-events-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
docker inspect app-gateway-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
docker inspect app-payments-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
```

Check what environment variables the payments service has:

```bash
docker inspect app-payments-1 --format '{{range .Config.Env}}{{println .}}{{end}}'
```

### 2.3: Live debugging with exec

Get a shell inside the gateway container and verify it can reach the other services:

```bash
# Check who you're running as
docker exec app-gateway-1 whoami
docker exec app-gateway-1 id

# Check DNS configuration — where does name resolution happen?
docker exec app-gateway-1 cat /etc/resolv.conf

# Verify gateway can reach events by service name (curl not available in slim image, use python):
docker exec app-gateway-1 python3 -c "
import urllib.request
print(urllib.request.urlopen('http://events:8081/health').read().decode())
"

# Verify gateway can reach payments:
docker exec app-gateway-1 python3 -c "
import urllib.request
print(urllib.request.urlopen('http://payments:8082/health').read().decode())
"
```

### 2.4: Logs analysis

View the last 20 log lines from each service:

```bash
docker compose logs gateway --tail=20
docker compose logs events --tail=20
docker compose logs payments --tail=20
```

Generate some traffic, then check logs again:

```bash
curl -s http://localhost:3080/events > /dev/null
curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity":1}'
docker compose logs gateway --tail=5
docker compose logs events --tail=5
```

Can you follow a single request across multiple services by matching the timestamps?

### 2.5: Network inspection

Inspect the Docker network that connects all services:

```bash
docker network ls | grep app
docker network inspect app_default --format '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}'
```

### 2.6: Proof of work

**Paste into `submissions/lab2.md`:**
1. Output of `docker images | grep app` with image sizes
2. Output of `docker history` for one image — annotate which layer is pip install
3. IP addresses of all 3 services from `docker inspect`
4. Environment variables of payments service
5. Output of `whoami` and `python3 urllib call to events:8081/health` from inside the gateway container
6. Log snippet showing the same request flowing through gateway → events
7. Network inspect output showing all containers and their IPs
8. Answer: "How does the gateway find the events service? What IP does `events` resolve to?"

<details>
<summary>💡 Hints</summary>

- `docker history` shows each Dockerfile instruction as a layer
- The biggest layer is usually `RUN pip install` — that's where all dependencies land
- `docker inspect` outputs JSON — use `--format` to extract specific fields
- Inside `docker exec`, you're in a minimal `python:3.12-slim` container — `curl`, `ping`, `vim` are NOT available
- Use `python3 -c "import urllib.request; ..."` to make HTTP requests from inside a container
- The Docker embedded DNS server runs at `127.0.0.11` — check `/etc/resolv.conf` inside a container

</details>

---

## Task 2 — Dockerfile Optimization (3 pts)

> ⏭️ This task is optional. Skipping it will not affect future labs.

**Objective:** Make the QuickTicket Dockerfiles smaller and more secure.

### 2.7: Add .dockerignore

Create `app/gateway/.dockerignore`, `app/events/.dockerignore`, and `app/payments/.dockerignore`:

```
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

Rebuild and compare sizes:

```bash
# Before (record from Task 1)
docker images | grep app

# Rebuild
docker compose build --no-cache

# After
docker images | grep app
```

### 2.8: Add non-root user

The containers currently run as `root` (you saw this with `whoami` in Task 1). Fix it by adding to each Dockerfile, before the `CMD` line:

```dockerfile
RUN addgroup --system app && adduser --system --ingroup app app
USER app
```

Rebuild, then verify:

```bash
docker compose up -d --build
docker exec app-gateway-1 whoami
# Should print: app (not root)
```

**Paste into `submissions/lab2.md`:**
- Image sizes before and after `.dockerignore` (any difference?)
- The `.dockerignore` content
- Output of `whoami` inside the container after adding non-root user
- The `git diff` of your Dockerfile changes

<details>
<summary>💡 Hints</summary>

- `.dockerignore` impact depends on what's in the build context — if there's no `.git/` directory, the saving is minimal
- The non-root user change might cause permission errors — if the app writes to any directory, that directory needs to be writable by the `app` user
- If you get permission errors after `USER app`, try: `RUN chown -R app:app /app` before the `USER` line
- The Dockerfiles are in `app/gateway/`, `app/events/`, `app/payments/` — each needs its own changes

</details>

---

## Bonus Task — Trace a Request Across Services (2.5 pts)

> 🌟 For those who want extra challenge and experience.

**Objective:** Follow a complete ticket purchase (reserve → pay → confirm) through all 3 services using only `docker compose logs` and timestamps.

1. Clear existing logs: `docker compose down && docker compose up -d`
2. Wait 5 seconds for services to stabilize
3. Execute a full purchase flow:
   ```bash
   RES=$(curl -s -X POST http://localhost:3080/events/1/reserve \
     -H "Content-Type: application/json" -d '{"quantity":1}')
   RES_ID=$(echo "$RES" | python3 -c "import sys,json; print(json.load(sys.stdin)['reservation_id'])")
   curl -s -X POST "http://localhost:3080/reserve/$RES_ID/pay"
   ```
4. Immediately capture all logs: `docker compose logs --timestamps`
5. Trace the request: find the matching log lines in gateway → events → payments → events (confirm)

**Paste into `submissions/lab2.md`:**
- The full timestamped logs showing one request flowing through all 3 services
- Annotate each line: which service, what it did, how long between hops
- Answer: "What is the total end-to-end time from gateway receiving the request to returning the response?"

---

## How to Submit

1. Create a branch and push:

   ```bash
   git switch -c feature/lab2
   git add submissions/lab2.md
   git commit -m "docs(lab2): add lab2 — Docker inspection and optimization"
   git push -u origin feature/lab2
   ```

2. Open a PR from your fork's `feature/lab2` → **course repo main branch**.

3. In the PR description, include:

   ```text
   - [x] Task 1 done — Docker inspection and operations
   - [ ] Task 2 done — Dockerfile optimization
   - [ ] Bonus Task done — request tracing across services
   ```

4. **Submit PR URL** via Moodle before the deadline.

---

## Acceptance Criteria

### Task 1 (6 pts)
- ✅ Image sizes listed for all QuickTicket images
- ✅ Layer history for one image with annotation of largest layer
- ✅ IP addresses of all 3 services from `docker inspect`
- ✅ `whoami` output from inside a container
- ✅ `python3 urllib call to events:8081/health` from inside gateway proving service discovery works
- ✅ Log snippet showing a request flowing through gateway → events
- ✅ Network inspect output with container IPs
- ✅ Written answer explaining Docker DNS service discovery

### Task 2 (3 pts)
- ✅ `.dockerignore` files created
- ✅ Image size comparison before/after
- ✅ Non-root user added, `whoami` proves it
- ✅ `git diff` of Dockerfile changes

### Bonus Task (2.5 pts)
- ✅ Full timestamped logs showing one purchase request across all 3 services
- ✅ Annotated with service name, action, and timing between hops
- ✅ End-to-end time calculated

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — Docker inspection & operations | **6** | All operational commands run, outputs documented, DNS discovery explained |
| **Task 2** — Dockerfile optimization | **3** | .dockerignore added, non-root user working, size comparison documented |
| **Bonus Task** — Request tracing | **2.5** | Full request traced across 3 services with timestamps and analysis |
| **Total** | **11.5** | 9 main + 2.5 bonus |

---

## Resources

<details>
<summary>📚 Documentation</summary>

- [Docker overview](https://docs.docker.com/get-started/docker-overview/) — how Docker works
- [Dockerfile reference](https://docs.docker.com/reference/dockerfile/) — all instructions
- [Docker networking](https://docs.docker.com/engine/network/) — bridge, DNS, compose networks
- [docker inspect reference](https://docs.docker.com/reference/cli/docker/inspect/) — format templates

</details>

<details>
<summary>🛠️ Tools</summary>

- [dive](https://github.com/wagoodman/dive) — interactive tool to explore image layers (optional, not required)
- [docker stats reference](https://docs.docker.com/reference/cli/docker/container/stats/) — resource monitoring

</details>

<details>
<summary>⚠️ Common Pitfalls</summary>

- **`docker exec` fails on stopped containers** — the container must be running
- **`ping` may not exist** in slim Python images — use `curl` instead
- **`.dockerignore` is per build context** — each service directory needs its own file
- **Non-root user + write permissions** — if the app writes temp files, ensure the directory is writable

</details>
