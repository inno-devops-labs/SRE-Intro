# Lab 2 Submission - Containerization: Inspect, Understand, Optimize

## Task 1 - Docker Inspection & Operations

### Image inspection

The rebuilt `app-gateway` image is 55.2 MB. In `docker history`, metadata instructions have size 0 B; `COPY main.py` is 24.6 kB and the non-root-user layer is 45.1 kB. The largest application-specific layer is `RUN pip install --no-cache-dir -r requirements.txt`, because it contains FastAPI, Uvicorn, HTTPX, Prometheus client, and their dependencies. The larger Debian/Python layers belong to the base image.

### Container inspection

```text
/app-events-1   172.22.0.5
/app-gateway-1  172.22.0.6
/app-payments-1 172.22.0.4

PAYMENT_LATENCY_MS=0
PAYMENT_FAILURE_RATE=0.0
```

### Live debugging

```text
$ docker compose exec -T gateway whoami
app

$ docker compose exec -T gateway id
uid=100(app) gid=101(app) groups=101(app)

$ docker compose exec -T gateway cat /etc/resolv.conf
nameserver 127.0.0.11
options ndots:0
```

The gateway reached both services by their Compose names:

```json
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

### Network and DNS

All five containers are on the `app_default` bridge network. Docker embedded DNS runs at `127.0.0.11`; gateway calls `http://events:8081`, which resolved to `172.22.0.5` in this run. Container IPs are dynamic, so Compose DNS names rather than IP addresses must be used.

### Logs

Before submitting, add the raw output from:

After `docker compose logs gateway --tail=20`:
```
gateway-1  | INFO:     Started server process [1]
gateway-1  | INFO:     Waiting for application startup.
gateway-1  | INFO:     Application startup complete.
gateway-1  | INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
gateway-1  | {"time":"2026-09-14 03:53:31,833","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve "HTTP/1.1 200 OK""}
gateway-1  | INFO:     172.22.0.1:48520 - "POST /events/1/reserve HTTP/1.1" 200 OK
gateway-1  | INFO:     172.22.0.1:48524 - "POST /reserve/bb28d2b6-ed60-47fa-897a-5dbbc14d56d4/pay HTTP/1.1" 503 Service Unavailable
```
After `docker compose logs events --tail=20`:
```
events-1  | INFO:     Started server process [1]
events-1  | INFO:     Waiting for application startup.
events-1  | {"time":"2026-09-14 03:52:29,591","level":"INFO","service":"events","msg":"DB pool created (max=10)"}
events-1  | {"time":"2026-09-14 03:52:29,596","level":"INFO","service":"events","msg":"Redis connected"}
events-1  | INFO:     Application startup complete.
events-1  | INFO:     Uvicorn running on http://0.0.0.0:8081 (Press CTRL+C to quit)
events-1  | {"time":"2026-09-14 03:52:45,401","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: d3f180d8-0d43-4753-9222-b7c224c685ca"}
events-1  | INFO:     172.22.0.6:57996 - "POST /events/1/reserve HTTP/1.1" 200 OK
events-1  | INFO:     172.22.0.6:58008 - "GET /health HTTP/1.1" 200 OK
events-1  | {"time":"2026-09-14 03:53:31,832","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: bb28d2b6-ed60-47fa-897a-5dbbc14d56d4"}
events-1  | INFO:     172.22.0.6:44744 - "POST /events/1/reserve HTTP/1.1" 200 OK
events-1  | INFO:     172.22.0.6:49716 - "GET /health HTTP/1.1" 200 OK
```

The matching endpoint and timestamps demonstrate gateway -> events request flow.

## Task 2 - Dockerfile optimization

Created `.dockerignore` in `gateway`, `events`, and `payments`:

```text
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

Added to all 3 Dockerfiles before `CMD`:

```dockerfile
RUN addgroup --system app && adduser --system --ingroup app app
USER app
```

`whoami` proves gateway now runs as `app`, not root. The ignore files do not materially shrink this tiny build context, but prevent future caches, secrets, editor files, and documentation from entering images.

