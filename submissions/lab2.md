# Lab 2 — Containerization: Inspect, Understand, Optimize

## Made by: 
### Nurmuhametov Denis (d.nurmuhametov@innopolis.university)

---

## Task 1 — Docker Inspection & Operations

### 2.1: Image inspection

```text
$ docker images | grep app
app-events:latest      7c0c2ffecf43   156MB
app-gateway:latest     4572dc0b584f   142MB
app-payments:latest    f5de08ed4e39   141MB
```

The events image is the largest (156 MB) because it includes additional dependencies for PostgreSQL and Redis clients on top of the same base.

**Layer analysis of t*he largest image (gateway):**

```text
$ docker history app-gateway --no-trunc --format "table {{.CreatedBy}}\t{{.Size}}"
CREATED BY                                                                   SIZE
CMD ["uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8080"]                0B
EXPOSE [8080/tcp]                                                            0B
COPY main.py . # buildkit                                                    13.3kB
RUN /bin/sh -c pip install --no-cache-dir -r requirements.txt # buildkit     24.6MB  ← pip install layer
COPY requirements.txt . # buildkit                                           73B
WORKDIR /app                                                                 0B
... (Python base image layers below) ...
RUN /bin/sh -c ... Python build from source ...                              35.3MB
...                                                                          3.81MB
# debian.sh --arch 'amd64' out/ 'trixie' ...                                 78.6MB  ← largest layer
```

**Answer:** The gateway image has **15 layers** total. The largest layer is `# debian.sh` at **78.6 MB** — it is the base Debian operating system containing all system files, libraries, and package manager data needed to run Python and the application.

| Layer | Size | What |
|-------|------|------|
| `# debian.sh` (base OS) | 78.6 MB | Debian root filesystem |
| Python build from source | 35.3 MB | Compiling CPython |
| `pip install -r requirements.txt` | 24.6 MB | Python dependencies (FastAPI, uvicorn, psycopg2, redis, httpx, prometheus-client) |
| `COPY main.py .` | 13.3 kB | Application code |

---

### 2.2: Container inspection

**IP addresses of all services:**

```text
$ docker inspect app-events-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-events-1 172.23.0.5

$ docker inspect app-gateway-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-gateway-1 172.23.0.6

$ docker inspect app-payments-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-payments-1 172.23.0.2
```

**Environment variables of the payments service:**

```text
$ docker inspect app-payments-1 --format '{{range .Config.Env}}{{println .}}{{end}}'
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.13
PYTHON_SHA256=2ab91ff401783ccca64f75d10c882e957bdfd60e2bf5a72f8421793729b78a71
```

The `PAYMENT_FAILURE_RATE` and `PAYMENT_LATENCY_MS` are set to 0 — no fault injection is active. The remaining variables come from the base `python:3.13-slim` image.

---

### 2.3: Live debugging with exec

```text
$ docker exec app-gateway-1 whoami
root

$ docker exec app-gateway-1 id
uid=0(root) gid=0(root) groups=0(root)

$ docker exec app-gateway-1 cat /etc/resolv.conf
nameserver 127.0.0.11
search .
options edns0 trust-ad ndots:0

$ docker exec app-gateway-1 python3 -c "
import urllib.request
print(urllib.request.urlopen('http://events:8081/health').read().decode())
"
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}

$ docker exec app-gateway-1 python3 -c "
import urllib.request
print(urllib.request.urlopen('http://payments:8082/health').read().decode())
"
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

The container runs as `root`. Docker's embedded DNS server runs at `127.0.0.11`, which resolves Compose service names to container IPs — both `events:8081` and `payments:8082` are reachable from inside the gateway.

---

### 2.4: Logs analysis

**Initial logs (after startup, no traffic):**

```text
$ docker compose logs gateway --tail=20
gateway-1  | INFO:     Started server process [1]
gateway-1  | INFO:     Waiting for application startup.
gateway-1  | INFO:     Application startup complete.
gateway-1  | INFO:     Uvicorn running on http://0.0.0.0:8080

$ docker compose logs events --tail=20
events-1  | INFO:     Started server process [1]
events-1  | {"time":"2026-06-07 20:04:20,989","level":"INFO","msg":"DB pool created (max=10)"}
events-1  | {"time":"2026-06-07 20:04:20,992","level":"INFO","msg":"Redis connected"}
events-1  | INFO:     Application startup complete.
events-1  | INFO:     Uvicorn running on http://0.0.0.0:8081

$ docker compose logs payments --tail=20
payments-1  | INFO:     Started server process [1]
payments-1  | INFO:     Application startup complete.
payments-1  | INFO:     Uvicorn running on http://0.0.0.0:8082
```

**After generating traffic:**

```text
$ curl -s http://localhost:3080/events > /dev/null
$ curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" -d '{"quantity":1}'

$ docker compose logs gateway --tail=5
gateway-1  | {"time":"2026-06-07 20:05:24,495","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events \"HTTP/1.1 200 OK\""}
gateway-1  | INFO:     172.23.0.1:35652 - "GET /events HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-06-07 20:05:30,963","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve \"HTTP/1.1 200 OK\""}
gateway-1  | INFO:     172.23.0.1:57566 - "POST /events/1/reserve HTTP/1.1" 200 OK

$ docker compose logs events --tail=5
events-1  | INFO:     172.23.0.6:46936 - "GET /events HTTP/1.1" 200 OK
events-1  | {"time":"2026-06-07 20:05:30,961","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 6996aaef-496d-4bc8-b0cd-08d7671c779c"}
events-1  | INFO:     172.23.0.6:60976 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

The `POST /events/1/reserve` request flows through both services:
- **gateway** (20:05:30,963) → forwards to events, logs the response.
- **events** (20:05:30,961) → processes the reservation, assigns reservation ID `6996aaef-496d-4bc8-b0cd-08d7671c779c`, returns 200.

Timestamps match within ~2 ms — the request path is: `curl → gateway:3080 → events:8081`.

---

### 2.5: Network inspection

```text
$ docker network ls | grep app
30c5940b6df6   app_default   bridge   local

$ docker network inspect app_default --format \
  '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}'
app-redis-1: 172.23.0.3/16
app-payments-1: 172.23.0.2/16
app-events-1: 172.23.0.5/16
app-gateway-1: 172.23.0.6/16
app-postgres-1: 172.23.0.4/16
```

All five containers share the `app_default` bridge network with sequential IPs in the `172.23.0.0/16` subnet.

**How does the gateway find the events service? What IP does `events` resolve to?**

Docker Compose creates a custom bridge network (`app_default`) and registers each container under its service name. The gateway's `/etc/resolv.conf` points to Docker's embedded DNS server at `127.0.0.11`, which resolves the hostname `events` to the container's IP (`172.23.0.5`). This is standard Docker Compose service discovery — no manual IP configuration is needed, and the DNS always returns the current IP even if containers are recreated.

---

## Task 2 — Dockerfile Optimization

### 2.7: Add `.dockerignore`

**Content of each `.dockerignore` (identical for all 3 services):**

```text
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

**Image sizes before rebuild:**

```text
$ docker images | grep app
app-events:latest      7c0c2ffecf43   156MB
app-gateway:latest     4572dc0b584f   142MB
app-payments:latest    f5de08ed4e39   141MB
```

**Image sizes after rebuild (`docker compose build --no-cache`):**

```text
$ docker images | grep app
app-events:latest      86d8e749ac6e   156MB
app-gateway:latest     ed3028fb5117   142MB
app-payments:latest    1d64b0a3602a   141MB
```

**Analysis:** Image sizes remained identical (156 / 142 / 141 MB). The `.dockerignore` had no measurable effect because the build context for each service is its own subdirectory (`./gateway/`, `./events/`, `./payments/`), which contains only `Dockerfile`, `main.py`, and `requirements.txt` — none of the patterns in `.dockerignore` (`.git`, `__pycache__`, `.env`, `*.md`, `.vscode`) are present in these directories.

---

### 2.8: Add non-root user

**Dockerfile changes (all 3 services):**

```diff
 EXPOSE <port>
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 CMD ["uvicorn", ...
```

**Verify the non-root user is active:**

```text
$ docker exec app-gateway-1 whoami
app
```

**Full `git diff` of Dockerfile changes:**

```diff
diff --git a/app/events/Dockerfile b/app/events/Dockerfile
index c45a68c..b6cb18d 100644
--- a/app/events/Dockerfile
+++ b/app/events/Dockerfile
@@ -6,4 +6,6 @@ RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .
 
 EXPOSE 8081
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]

diff --git a/app/gateway/Dockerfile b/app/gateway/Dockerfile
index 68ef075..71c6891 100644
--- a/app/gateway/Dockerfile
+++ b/app/gateway/Dockerfile
@@ -6,4 +6,6 @@ RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .
 
 EXPOSE 8080
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

diff --git a/app/payments/Dockerfile b/app/payments/Dockerfile
index 7f9e7c1..8cf997d 100644
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

**Full purchase flow (reserve → pay → confirm):**

```text
$ RES=$(curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" -d '{"quantity":1}')
$ RES_ID=$(echo "$RES" | python3 -c "import sys,json; print(json.load(sys.stdin)['reservation_id'])")
$ curl -s -X POST "http://localhost:3080/reserve/$RES_ID/pay"
{"order_id":"c8852abb-f87d-4ff7-8663-1d9342dd1fe8","event_id":1,"quantity":1,"total_cents":5000,"status":"confirmed"}
```

**Full timestamped logs:**

```text
$ docker compose logs --timestamps

--- Reserve flow (gateway → events) ---
events-1  | 20:45:57.326720519Z | Reserved 1 tickets for event 1: c8852abb-...
gateway-1 | 20:45:57.328660283Z | HTTP Request: POST http://events:8081/events/1/reserve → 200
gateway-1 | 20:45:57.329824681Z | POST /events/1/reserve HTTP/1.1 200 OK

--- Pay flow (gateway → payments → events) ---
payments-1 | 20:45:57.381267963Z | Payment success: PAY-B93D1EF8 for c8852abb-...
gateway-1  | 20:45:57.382550954Z | HTTP Request: POST http://payments:8082/charge → 200
events-1   | 20:45:57.391719137Z | Order confirmed: c8852abb-...
gateway-1  | 20:45:57.393213815Z | HTTP Request: POST http://events:8081/.../confirm → 200
gateway-1  | 20:45:57.394127592Z | POST /reserve/.../pay HTTP/1.1 200 OK
```

### Annotated timeline

| Timestamp | Service | Action | Δ from previous |
|-----------|---------|--------|:---------------:|
| `20:45:57.326` | **events** | Reserve processed (`c8852abb-...`) | — |
| `20:45:57.328` | **gateway** | Reserve response forwarded to client | 2 ms |
| `20:45:57.329` | **gateway** | `POST /events/1/reserve` 200 OK (response sent) | 1 ms |
| *(client-side gap — bash extracts `reservation_id` and calls pay)* | | | |
| `20:45:57.381` | **payments** | Payment success (`PAY-B93D1EF8`) | — |
| `20:45:57.382` | **gateway** | Charge response from payments forwarded | 1 ms |
| `20:45:57.391` | **events** | Order confirmed (`c8852abb-...`) | 9 ms |
| `20:45:57.393` | **gateway** | Confirm response from events forwarded | 2 ms |
| `20:45:57.394` | **gateway** | `POST /reserve/.../pay` 200 OK (response sent) | 1 ms |

### Request flow diagram

```
curl → gateway:3080
  └──→ events:8081  ── reserve (20:45:57.326)
  (reservation_id returned)
  
curl → gateway:3080/reserve/{id}/pay
  ├──→ payments:8082 ── charge (20:45:57.381)
  └──→ events:8081  ── confirm (20:45:57.391)
  (order confirmed → 200)
```

**End-to-end time for the pay request** (from gateway receiving `/reserve/{id}/pay` to returning the response):

The first evidence of gateway processing the pay request is the call to payments at ~20:45:57.380. The final response is logged at `20:45:57.394`. This gives an estimated **end-to-end time of ~14 ms**.

The request path was: **gateway → payments (charge) → events (confirm) → 200 OK**, all completing in roughly 14 milliseconds with no fault injection active.
