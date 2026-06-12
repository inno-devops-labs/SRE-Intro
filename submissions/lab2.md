# Lab 2 — Containerization: Inspect, Understand, Optimize

**Author:** Anton Bugaev  
**Date:** 2026-06-12

> Note: Host port `5432` is already in use on my machine. For local runs I temporarily remove the `postgres` host port mapping (same as Lab 1). Internal Docker networking is unchanged.

---

## Task 1 — Docker Inspection & Operations

### 2.1 Image sizes

```
$ docker images | grep app
app-events:latest     1e8cc80c6ab3        260MB         57.6MB
app-gateway:latest    da9de777c512        239MB         52.3MB
app-payments:latest   30b79806b032        237MB         51.8MB
```

**Largest image:** `app-events` (260 MB virtual / 57.6 MB compressed) — it has extra dependencies (`psycopg2-binary`, `redis`) compared to gateway and payments.

### 2.1 Layer history (gateway)

```
$ docker history app-gateway --no-trunc --format "table {{.CreatedBy}}\t{{.Size}}"
CREATED BY                                                                                              SIZE
CMD ["uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8080"]                                           0B
EXPOSE [8080/tcp]                                                                                       0B
USER app                                                                                                0B
RUN /bin/sh -c addgroup --system app && adduser --system --ingroup app app                              45.1kB
COPY main.py .                                                                                          24.6kB
RUN /bin/sh -c pip install --no-cache-dir -r requirements.txt   ← pip install layer                    28.7MB
COPY requirements.txt .                                                                                 12.3kB
WORKDIR /app                                                                                            8.19kB
... (python:3.13-slim base layers) ...
# debian.sh --arch 'arm64' ...                                                                          109MB   ← largest layer (OS base)
RUN /bin/sh -c set -eux; apt-get update; ... python build ...                                           43.5MB  ← second largest (Python runtime)
```

**Layer count:** 16 layers visible in `docker history`.  
**Largest layer:** the Debian base image layer (`109 MB`) — it contains the entire OS filesystem. Among application-specific layers, `RUN pip install` (`28.7 MB`) is the largest because it downloads and installs all Python dependencies (FastAPI, uvicorn, httpx, prometheus-client, etc.).

### 2.2 Container IPs and payments environment

```
$ docker inspect app-events-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-events-1 172.18.0.5

$ docker inspect app-gateway-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-gateway-1 172.18.0.6

$ docker inspect app-payments-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-payments-1 172.18.0.3
```

**Payments environment variables:**

```
PAYMENT_LATENCY_MS=0
PAYMENT_FAILURE_RATE=0.0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.14
PYTHON_SHA256=639e43243c620a308f968213df9e00f2f8f62332f7adbaa7a7eeb9783057c690
```

### 2.3 Live debugging (gateway container)

```
$ docker exec app-gateway-1 whoami
app

$ docker exec app-gateway-1 id
uid=100(app) gid=101(app) groups=101(app)

$ docker exec app-gateway-1 cat /etc/resolv.conf
nameserver 127.0.0.11
options ndots:0

$ docker exec app-gateway-1 python3 -c "import urllib.request; print(urllib.request.urlopen('http://events:8081/health').read().decode())"
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}

$ docker exec app-gateway-1 python3 -c "import urllib.request; print(urllib.request.urlopen('http://payments:8082/health').read().decode())"
{"status":"healthy","failure_rate":0.0,"latency_ms":0}

$ docker exec app-gateway-1 python3 -c "import socket; print(socket.gethostbyname('events'))"
172.18.0.5
```

### 2.4 Logs — request flowing gateway → events

After `GET /events` and `POST /events/1/reserve`:

```
gateway-1  | {"time":"2026-06-12 14:10:20,077","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | INFO:     192.168.65.1:60455 - "GET /events HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-06-12 14:10:20,094","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve "HTTP/1.1 200 OK""}
gateway-1  | INFO:     192.168.65.1:58302 - "POST /events/1/reserve HTTP/1.1" 200 OK

events-1   | INFO:     172.18.0.6:59996 - "GET /events HTTP/1.1" 200 OK
events-1   | {"time":"2026-06-12 14:10:20,093","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 94d5d851-3ef1-493d-9873-0892e155ff30"}
events-1   | INFO:     172.18.0.6:59996 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

**Observation:** Timestamps match within ~17 ms. Gateway logs show outbound calls to `events:8081`; events logs show requests from gateway IP `172.18.0.6`. Same request can be traced by timestamp and HTTP method/path.

### 2.5 Network inspect

```
$ docker network ls | grep app
4f4b3d4ebd09   app_default   bridge    local

$ docker network inspect app_default --format '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}'
app-events-1: 172.18.0.5/16
app-gateway-1: 172.18.0.6/16
app-payments-1: 172.18.0.3/16
app-postgres-1: 172.18.0.4/16
app-redis-1: 172.18.0.2/16
```

### 2.6 DNS service discovery answer

**How does the gateway find the events service?**

Docker Compose creates a bridge network (`app_default`) and registers each service name as a DNS alias. The embedded DNS resolver at `127.0.0.11` (visible in `/etc/resolv.conf`) resolves `events` to the current container IP of the `events` service.

**What IP does `events` resolve to?** `172.18.0.5` (verified with `socket.gethostbyname('events')` from inside the gateway container).

The gateway uses the hostname `events` (configured via `EVENTS_URL=http://events:8081` in `docker-compose.yaml`), not a hardcoded IP — so if the events container is recreated with a new IP, DNS still works.

---

## Task 2 — Dockerfile Optimization

### Image sizes before vs after `.dockerignore`

| Image | Before (Task 1) | After rebuild |
|-------|-----------------|---------------|
| app-events | 260 MB / 57.6 MB | 260 MB / 57.6 MB |
| app-gateway | 239 MB / 52.3 MB | 239 MB / 52.3 MB |
| app-payments | 237 MB / 51.8 MB | 237 MB / 51.8 MB |

**No measurable difference.** The build context for each service only contains `requirements.txt` and `main.py` — there is no `__pycache__/`, `.git/`, or `.md` files in the context directory, so `.dockerignore` has nothing to exclude.

### `.dockerignore` content (identical in all three services)

```
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

### Non-root user verification

```
$ docker exec app-gateway-1 whoami
app
```

### Dockerfile `git diff`

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

Full purchase flow: `POST /events/1/reserve` → `POST /reserve/{id}/pay`

```
events-1    | 2026-06-12T14:11:50.549519792Z  [events]   Reserved 1 ticket: 244bbb52-...
events-1    | 2026-06-12T14:11:50.550145584Z  [events]   POST /events/1/reserve → 200 OK
gateway-1   | 2026-06-12T14:11:50.551161251Z  [gateway]  → events: POST /events/1/reserve → 200 OK
gateway-1   | 2026-06-12T14:11:50.552076001Z  [gateway]  ← client: POST /events/1/reserve → 200 OK          (+1 ms)
payments-1  | 2026-06-12T14:11:50.638820084Z  [payments] Payment success: PAY-C0726750
payments-1  | 2026-06-12T14:11:50.639104417Z  [payments] POST /charge → 200 OK                               (+87 ms from reserve)
gateway-1   | 2026-06-12T14:11:50.639611834Z  [gateway]  → payments: POST /charge → 200 OK
events-1    | 2026-06-12T14:11:50.643407792Z  [events]   Order confirmed: 244bbb52-...
events-1    | 2026-06-12T14:11:50.643660167Z  [events]   POST /reservations/.../confirm → 200 OK             (+4 ms after charge)
gateway-1   | 2026-06-12T14:11:50.643905376Z  [gateway]  → events: POST /confirm → 200 OK
gateway-1   | 2026-06-12T14:11:50.644343542Z  [gateway]  ← client: POST /reserve/.../pay → 200 OK            (+92 ms from pay start)
```

**Flow:**
1. **Gateway** receives `POST /reserve/{id}/pay` from client
2. **Gateway → Events:** reserve already done; for pay flow: gateway calls **Payments** `/charge`
3. **Payments** processes charge (~87 ms after reserve in this trace; pay request starts ~88 ms after reserve response)
4. **Gateway → Events:** confirm reservation
5. **Gateway** returns `200 OK` to client

**End-to-end time (pay request):** from gateway receiving `POST /reserve/.../pay` (first gateway log at `14:11:50.639`) to returning response (`14:11:50.644`) ≈ **5 ms** for the pay leg.  
**Full flow (reserve + pay):** from first reserve log (`14:11:50.549`) to final pay response (`14:11:50.644`) ≈ **95 ms**.
