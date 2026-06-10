# Lab 2 Submission — Containerization: Inspect, Understand, Optimize

> **Note:** All commands use **Podman** instead of Docker. Equivalent commands:
> - `podman images` instead of `docker images`
> - `podman inspect` instead of `docker inspect`
> - `podman exec` instead of `docker exec`
> - `podman compose` instead of `docker compose`
> - `podman network` instead of `docker network`
>
> Podman container names use underscores: `app_gateway_1` (not `app-gateway-1`).

---

## Task 1 — Container Inspection & Operations

### 2.1 Image Inspection

**Image sizes (`podman images | grep app`):**

```text
REPOSITORY                  TAG         IMAGE ID      CREATED       SIZE
localhost/app_gateway       latest      6a52d106ece2  ...           172 MB
localhost/app_events        latest      9ef5c7872786  ...           188 MB
localhost/app_payments      latest      b46b70fd8ec1  ...           171 MB
```

Largest image: **events (188 MB)** — it has the most dependencies (psycopg2, redis, prometheus-client, fastapi, uvicorn).

**Gateway layer history (`podman history localhost/app_gateway:latest`):**

```text
CREATED BY                                     SIZE
/bin/sh -c #(nop) CMD ["uvicorn", ...]         0B
/bin/sh -c #(nop) USER app                     0B
/bin/sh -c addgroup --system app && adduser... 10.8kB
/bin/sh -c #(nop) EXPOSE 8080                  0B
/bin/sh -c #(nop) COPY main.py                 15.4kB
/bin/sh -c pip install --no-cache-dir -r ...   25.3MB   ← pip install layer
/bin/sh -c #(nop) COPY requirements.txt        2.56kB
/bin/sh -c #(nop) WORKDIR /app                 0B
... (python:3.13-slim base layers) ...
debian.sh --arch 'arm64' ...                   103MB    ← largest overall layer
RUN python compile ...                         40MB
```

**How many layers?** The gateway image has **~14 layers** total (8 app-specific + 6 base image layers).

**Largest layer:** The **debian base layer (103 MB)** is the largest overall — it's the full OS root filesystem. Among app-specific layers, **`RUN pip install` (25.3 MB)** is the largest because it downloads and installs all Python dependencies (fastapi, httpx, uvicorn, prometheus-client, etc.).

### 2.2 Container Inspection

**IP addresses of the 3 services:**

```text
app_events_1   10.89.1.9
app_gateway_1  10.89.1.6
app_payments_1 10.89.1.8
```

**Payments environment variables:**

```text
container=podman
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.13
PYTHON_SHA256=2ab91ff401783ccca64f75d10c882e957bdfd60e2bf5a72f8421793729b78a71
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
HOME=/root
HOSTNAME=...
```

The fault-injection vars `PAYMENT_FAILURE_RATE` and `PAYMENT_LATENCY_MS` come from `docker-compose.yaml` environment section.

### 2.3 Live Debugging with exec

**Before optimization (ran as root):**

```text
$ podman exec app_gateway_1 whoami
root

$ podman exec app_gateway_1 id
uid=0(root) gid=0(root) groups=0(root)
```

**/etc/resolv.conf inside gateway:**

```text
search dns.podman yandex.net yandex-team.ru yandex.ru
nameserver 10.89.1.1
```

**Service discovery via Python urllib:**

```text
$ podman exec app_gateway_1 python3 -c "
import urllib.request
print(urllib.request.urlopen('http://events:8081/health').read().decode())
"
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}

$ podman exec app_gateway_1 python3 -c "
import urllib.request
print(urllib.request.urlopen('http://payments:8082/health').read().decode())
"
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

**DNS resolution:**

```text
events   -> 10.89.1.9
payments -> 10.89.1.8
```

### 2.4 Logs Analysis

**Log snippet showing the same request flowing through gateway → events:**

After running `curl http://localhost:3080/events` and `curl -X POST .../events/1/reserve`:

```text
# gateway
INFO: 192.168.127.1:46705 - "GET /events HTTP/1.1" 200 OK
INFO: 192.168.127.1:57181 - "POST /events/1/reserve HTTP/1.1" 200 OK

# events (source IP 10.89.1.6 = gateway)
INFO: 10.89.1.6:39024 - "GET /events HTTP/1.1" 200 OK
INFO: 10.89.1.6:39024 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

The gateway IP `10.89.1.6` appears as the client in events logs — you can follow a request by matching the HTTP method/path and timestamp across services.

### 2.5 Network Inspection

```text
$ podman network ls | grep app
f3569eee50ce  app_default  bridge

$ podman network inspect app_default
app_gateway_1:  10.89.1.6/24
app_payments_1: 10.89.1.8/24
app_redis_1:    10.89.1.10/24
app_postgres_1: 10.89.1.11/24
app_events_1:   10.89.1.9/24
```

All 5 containers share the `app_default` bridge network.

### 2.6 DNS Service Discovery Answer

**How does the gateway find the events service?**

The gateway uses the hostname `events` (configured via `EVENTS_URL=http://events:8081` in compose). Podman's embedded DNS server at `10.89.1.1` (shown in `/etc/resolv.conf`) resolves service names to container IPs on the `app_default` network. When the gateway calls `http://events:8081`, DNS resolves `events` → **10.89.1.9** (the events container IP). This is automatic — Compose creates the network and registers each service name as a DNS alias.

---

## Task 2 — Dockerfile Optimization

### 2.7 .dockerignore

Created identical `.dockerignore` in `app/gateway/`, `app/events/`, `app/payments/`:

```
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

**Image sizes before vs after:**

| Image | Before | After |
|-------|-------:|------:|
| gateway | 172 MB | 172 MB |
| events | 188 MB | 188 MB |
| payments | 171 MB | 171 MB |

**No size difference.** The build context for each service contains only `main.py` and `requirements.txt` — there is no `.git/`, `__pycache__/`, or `.vscode/` in the context, so `.dockerignore` has nothing to exclude. The hint in the lab was correct: savings depend on what's actually in the build context.

### 2.8 Non-root User

**`whoami` after adding `USER app` and rebuilding:**

```text
$ podman exec app_gateway_1 whoami
app

$ podman exec app_events_1 whoami
app

$ podman exec app_payments_1 whoami
app
```

System still healthy after rebuild — no permission errors because the app only reads files and listens on ports (no file writes needed).

**Dockerfile diff (`git diff app/*/Dockerfile`):**

```diff
diff --git a/app/events/Dockerfile b/app/events/Dockerfile
--- a/app/events/Dockerfile
+++ b/app/events/Dockerfile
@@ -6,4 +6,6 @@ RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .
 
 EXPOSE 8081
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]

diff --git a/app/gateway/Dockerfile b/app/gateway/Dockerfile
--- a/app/gateway/Dockerfile
+++ b/app/gateway/Dockerfile
@@ -6,4 +6,6 @@ RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .
 
 EXPOSE 8080
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

diff --git a/app/payments/Dockerfile b/app/payments/Dockerfile
--- a/app/payments/Dockerfile
+++ b/app/payments/Dockerfile
@@ -6,4 +6,6 @@ RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .
 
 EXPOSE 8082
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8082"]
```

---

## Bonus Task — Trace a Request Across Services

Reservation ID: `e5b18b86-9e6c-4453-a97d-84451d598e5d`

**Timestamped logs (full purchase: reserve → pay → confirm):**

```text
# Step 1: RESERVE — gateway → events
2026-06-10T23:32:19+03:00  gateway  POST /events/1/reserve → 200 OK
{"time":"2026-06-10 20:32:19,546","service":"events","msg":"Reserved 1 tickets for event 1: e5b18b86-..."}
{"time":"2026-06-10 20:32:19,548","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve HTTP/1.1 200 OK"}
2026-06-10T23:32:19+03:00  events   POST /events/1/reserve → 200 OK

# Step 2: PAY — gateway → payments → events (confirm)
{"time":"2026-06-10 20:32:19,631","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge HTTP/1.1 200 OK"}
{"time":"2026-06-10 20:32:19,631","service":"payments","msg":"Payment success: PAY-6532E234 for e5b18b86-..."}
2026-06-10T23:32:19+03:00  payments POST /charge → 200 OK

{"time":"2026-06-10 20:32:19,636","service":"gateway","msg":"HTTP Request: POST http://events:8081/reservations/e5b18b86-.../confirm HTTP/1.1 200 OK"}
{"time":"2026-06-10 20:32:19,636","service":"events","msg":"Order confirmed: e5b18b86-..."}
2026-06-10T23:32:19+03:00  events   POST /reservations/.../confirm → 200 OK

2026-06-10T23:32:19+03:00  gateway  POST /reserve/e5b18b86-.../pay → 200 OK
```

**Annotated trace:**

| Time (ms) | Service | Action | Hop latency |
|-----------|---------|--------|-------------|
| 546 | events | Created reservation in Redis | — |
| 548 | gateway | Received reserve response from events | 2 ms |
| 631 | payments | Processed charge, returned PAY-6532E234 | 83 ms after reserve |
| 631 | gateway | Received payment response | ~0 ms |
| 636 | events | Confirmed order in Postgres, cleaned Redis | 5 ms |
| 636 | gateway | Received confirm response, returned to client | ~0 ms |

**End-to-end time for `/pay` request:** From gateway's first downstream call (payments at 631 ms) to final response (confirm at 636 ms) = **~5 ms** of internal orchestration. The full user-facing `/pay` endpoint (including reserve earlier) completes within the same second.

The 83 ms gap between reserve (548 ms) and pay (631 ms) is client-side — the time between the two `curl` commands, not service latency.
