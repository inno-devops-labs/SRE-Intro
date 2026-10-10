# Lab 2 Submission — Containerization: Inspect, Understand, Optimize

## Task 1 — Docker Inspection & Operations

### 2.1 — Image Inspection

```
$ docker images | grep app
app-gateway             latest      fba47c5e2d3f   21 hours ago   151MB
app-events              latest      ef7faaecd484   22 hours ago   165MB
app-payments            latest      ecf28c7ab28d   22 hours ago   150MB
```

`docker history app-gateway --no-trunc` — 15 layers total. The Dockerfile has 6 instructions (`FROM`, `WORKDIR`, `COPY requirements.txt`, `RUN pip install`, `COPY main.py`, `EXPOSE`, `CMD`); the rest of the 15 layers come from the base `python:3.13-slim` image itself.

Largest layers:
- Base image build step (`debian.sh --arch amd64 ...`): **78.6MB** — this is the Debian rootfs baked into `python:3.13-slim`, not something the app Dockerfile controls.
- `RUN pip install --no-cache-dir -r requirements.txt`: **25.1MB** — this is the largest layer we actually control; it holds FastAPI, uvicorn, httpx, prometheus_client and their dependencies.
- Python interpreter build (`apt-get install ... build python from source`): **35.6MB** — also part of the base image.

### 2.2 — Container Inspection

IP addresses on `app_default`:
```
app-events-1:   172.18.0.5
app-gateway-1:  172.18.0.6
app-payments-1: 172.18.0.4
app-postgres-1: 172.18.0.2
app-redis-1:    172.18.0.3
```

Environment variables of `payments`:
```
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.15
PYTHON_SHA256=1e66a7945a48390ee4c2a4268a0e4185884059a13c4aab6d148aa208deea4a76
```

### 2.3 — Live Debugging with exec

```
$ docker exec app-gateway-1 whoami
root

$ docker exec app-gateway-1 id
uid=0(root) gid=0(root) groups=0(root)

$ docker exec app-gateway-1 cat /etc/resolv.conf
nameserver 127.0.0.11
search localdomain
options ndots:0

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

(At this point in the lab, before Task 2's non-root change, the container still ran as root — confirming what needed to be fixed.)

Gateway successfully reached both `events` and `payments` by service name, proving Docker's embedded DNS (`127.0.0.11`) resolves compose service names to container IPs.

### 2.4 — Logs Analysis

Generated fresh traffic (`GET /events`, `POST /events/1/reserve`), matched logs across services by `reservation_id`:

```
gateway-1 | {"time":"2026-09-10 18:48:20,625",...,"msg":"HTTP Request: GET http://events:8081/events \"HTTP/1.1 200 OK\""}
events-1  | INFO: 172.18.0.6:50360 - "GET /events HTTP/1.1" 200 OK
gateway-1 | INFO: 172.18.0.1:36064 - "POST /events/1/reserve HTTP/1.1" 200 OK
gateway-1 | {"time":"2026-09-10 18:48:20,642",...,"msg":"HTTP Request: POST http://events:8081/events/1/reserve \"HTTP/1.1 200 OK\""}
events-1  | {"time":"2026-09-10 18:48:20,641",...,"msg":"Reserved 1 tickets for event 1: 895645ed-1d97-4433-ad5d-5f8e58776a66"}
events-1  | INFO: 172.18.0.6:50360 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

The `reservation_id` `895645ed-1d97-4433-ad5d-5f8e58776a66` returned by curl matched the one logged by `events`. The gateway's outbound-request log (`HTTP Request: POST http://events:8081/events/1/reserve`) lined up in time (same millisecond window) with events' inbound log line — confirmed a single request can be traced across both services by timestamp + reservation ID correlation.

### 2.5 — Network Inspection

```
$ docker network ls | grep app
bc1024654483   app_default   bridge    local

$ docker network inspect app_default --format '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}'
app-postgres-1: 172.18.0.2/16
app-events-1: 172.18.0.5/16
app-payments-1: 172.18.0.4/16
app-redis-1: 172.18.0.3/16
app-gateway-1: 172.18.0.6/16
```

### 2.6 — How Does the Gateway Find the Events Service?

The gateway calls `events` by its Compose service name (e.g. `http://events:8081`), never by IP. Docker Compose attaches all services to the same user-defined bridge network (`app_default`) and runs an embedded DNS server at `127.0.0.11` inside every container (confirmed via `/etc/resolv.conf`). That DNS server resolves the service name `events` to the container's current IP on that network — `172.18.0.5` in this run. Because resolution happens at request time rather than being baked in, service discovery survives container restarts even if the IP changes.

## Task 2 — Dockerfile Optimization

### .dockerignore

Added identical `.dockerignore` to `gateway/`, `events/`, and `payments/`:
```
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

### Image Size Comparison

| Image | Before | After `.dockerignore` |
|-------|-------:|------------------------:|
| app-gateway | 151MB | 151MB |
| app-events | 165MB | 165MB |
| app-payments | 150MB | 150MB |

No size difference observed. Each service's build context contained only `main.py`, `requirements.txt`, and the Dockerfile — no `.git` directory or bulky files present to exclude, so `.dockerignore` had nothing to strip here. Kept as a safety net against future context bloat.

### Non-root User

Added to all three Dockerfiles, before `CMD`:
```dockerfile
RUN addgroup --system app && adduser --system --ingroup app app
USER app
```

Verification after rebuild:
```
$ docker exec app-gateway-1 whoami
app
```

No permission errors observed on rebuild/restart — none of the services write to disk at runtime, so no extra `chown` was needed. `/health` returned `healthy` after switching to the non-root user, confirming the app runs correctly under the restricted account.

### Dockerfile Diff

```diff
diff --git a/app/events/Dockerfile b/app/events/Dockerfile
index c45a68c..3c837e5 100644
--- a/app/events/Dockerfile
+++ b/app/events/Dockerfile
@@ -6,4 +6,7 @@ RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .


 EXPOSE 8081
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
+
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]
diff --git a/app/gateway/Dockerfile b/app/gateway/Dockerfile
index 68ef075..f4e4173 100644
--- a/app/gateway/Dockerfile
+++ b/app/gateway/Dockerfile
@@ -6,4 +6,7 @@ RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .

 EXPOSE 8080
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
+
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
diff --git a/app/payments/Dockerfile b/app/payments/Dockerfile
index 7f9e7c1..0518909 100644
--- a/app/payments/Dockerfile
+++ b/app/payments/Dockerfile
@@ -6,4 +6,7 @@ RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .

 EXPOSE 8082
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
+
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8082"]
```

## Bonus Task — Trace a Request Across Services

Ran a clean `docker compose down && docker compose up -d`, waited for startup, ran a full reserve → pay flow for `reservation_id = 906f5f4b-d4fb-4baa-8686-c622d1656235`.

```
gateway-1 | 18:57:08.966622833Z INFO: 172.18.0.1:48194 - "POST /events/1/reserve HTTP/1.1" 200 OK
gateway-1 | 18:57:08.966637692Z HTTP Request: POST http://events:8081/events/1/reserve "HTTP/1.1 200 OK"
events-1  | 18:57:08.964104914Z Reserved 1 tickets for event 1: 906f5f4b-d4fb-4baa-8686-c622d1656235
events-1  | 18:57:08.964537119Z INFO: 172.18.0.6:38504 - "POST /events/1/reserve HTTP/1.1" 200 OK

gateway-1 | 18:57:09.004484792Z HTTP Request: POST http://payments:8082/charge "HTTP/1.1 200 OK"
payments-1| 18:57:09.003636595Z Payment success: PAY-80EC15C1 for 906f5f4b-d4fb-4baa-8686-c622d1656235
payments-1| 18:57:09.004027338Z INFO: 172.18.0.6:39270 - "POST /charge HTTP/1.1" 200 OK

gateway-1 | 18:57:09.012839005Z HTTP Request: POST http://events:8081/reservations/906f5f4b-d4fb-4baa-8686-c622d1656235/confirm "HTTP/1.1 200 OK"
events-1  | 18:57:09.012006900Z Order confirmed: 906f5f4b-d4fb-4baa-8686-c622d1656235
events-1  | 18:57:09.012454444Z INFO: 172.18.0.6:38504 - "POST /reservations/.../confirm HTTP/1.1" 200 OK
gateway-1 | 18:57:09.013547288Z INFO: 172.18.0.1:48196 - "POST /reserve/906f5f4b.../pay HTTP/1.1" 200 OK
```

Annotated hop by hop:
1. **18:57:08.964** — `events` receives and processes the reserve call (client already called `/events/1/reserve` directly, this reservation was created before the `/pay` call in this trace).
2. **18:57:09.0036–09.004** — gateway calls `payments:8082/charge`; payments processes and returns `PAY-80EC15C1` (~1ms round trip observed in logs).
3. **18:57:09.012** — gateway calls `events:8081/reservations/{id}/confirm`; events marks the order confirmed (~8ms after the payments call completed).
4. **18:57:09.0135** — gateway returns the final `200 OK` to the client.

**Total end-to-end time (gateway receiving `/pay` request to returning response):** from the payments call at `09.003636` to the final response at `09.013547` is approximately **10ms**. (The reserve step itself happened a fraction of a second earlier as a separate request in this trace, per the curl script's two sequential calls.)
