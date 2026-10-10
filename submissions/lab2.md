# Lab 2 — Containerization: Inspect, Understand, Optimize

## Task 1 — Docker Inspection & Operations

### 2.1: Image Inspection

**Image sizes:**

```
app-events:latest       157MB
app-gateway:latest      143MB
app-payments:latest     141MB
```

**Layer history for `app-gateway`:**

```
CREATED BY                                                          SIZE
CMD ["uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8080"]      0B
EXPOSE [8080/tcp]                                                   0B
COPY main.py .                                                      13.3kB
RUN pip install --no-cache-dir -r requirements.txt                  24.9MB    ← largest app layer (pip install)
COPY requirements.txt .                                             73B
WORKDIR /app                                                        0B
CMD ["python3"]                                                     0B
RUN set -eux; ... python3 --version; pip3 --version                 35.5MB    ← Python build (base image)
RUN set -eux; apt-get update; apt-get install ... ca-certificates   3.81MB
# debian.sh --arch 'amd64' out/ 'trixie'                           78.6MB    ← Debian base (largest overall)
```

The gateway image has **10 layers**. The largest layer is the Debian base at 78.6MB, followed by the Python build at 35.5MB. Among application-specific layers, `pip install` is the biggest at 24.9MB — this is where all Python dependencies (FastAPI, httpx, uvicorn, prometheus_client) are installed.

### 2.2: Container Inspection

**Service IP addresses:**

```
/app-events-1   172.18.0.5
/app-gateway-1  172.18.0.6
/app-payments-1 172.18.0.2
```

**Payments environment variables:**

```
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.14
PYTHON_SHA256=639e43243c620a308f968213df9e00f2f8f62332f7adbaa7a7eeb9783057c690
```

### 2.3: Live Debugging with exec

**whoami / id:**

```
root
uid=0(root) gid=0(root) groups=0(root)
```

(This was before the non-root user optimization in Task 2.)

**DNS configuration (`/etc/resolv.conf` inside gateway):**

```
nameserver 127.0.0.11
search .
options edns0 trust-ad ndots:0
```

The Docker embedded DNS server runs at `127.0.0.11`.

**Service discovery — gateway → events:**

```json
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
```

**Service discovery — gateway → payments:**

```json
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

### 2.4: Logs Analysis

**Gateway logs after reserve request:**

```
gateway-1  | {"time":"2026-06-13 16:04:18,122","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events \"HTTP/1.1 200 OK\""}
gateway-1  | INFO:     172.18.0.1:42390 - "GET /events HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-06-13 16:04:18,139","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve \"HTTP/1.1 200 OK\""}
gateway-1  | INFO:     172.18.0.1:42398 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

**Events logs for the same request:**

```
events-1  | INFO:     172.18.0.6:39098 - "GET /events HTTP/1.1" 200 OK
events-1  | {"time":"2026-06-13 16:04:18,138","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 81329f06-596f-49a7-9357-44b1283f1a69"}
events-1  | INFO:     172.18.0.6:39098 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

The request can be traced across services by matching timestamps: gateway sends POST to `events:8081` at `16:04:18,139`, events logs the reservation at `16:04:18,138` — sub-millisecond propagation. The source IP in events logs (`172.18.0.6`) matches gateway's container IP, confirming the call chain.

### 2.5: Network Inspection

```
1bdf54c08954   app_default   bridge   local
```

**Containers on the network:**

```
app-postgres-1: 172.18.0.4/16
app-redis-1:    172.18.0.3/16
app-gateway-1:  172.18.0.6/16
app-payments-1: 172.18.0.2/16
app-events-1:   172.18.0.5/16
```

### 2.6: How does the gateway find the events service?

The gateway uses Docker's embedded DNS server (`127.0.0.11`, as seen in `/etc/resolv.conf`). When the gateway code makes an HTTP request to `http://events:8081`, Docker DNS resolves the service name `events` to the container's IP address on the `app_default` bridge network — in this case `172.18.0.5`. This is automatic service discovery provided by Docker Compose: all services in the same `docker-compose.yaml` share a bridge network and can reach each other by service name.

---

## Task 2 — Dockerfile Optimization

### Image Sizes Before & After `.dockerignore`

| Image | Before | After |
|-------|--------|-------|
| app-events | 157MB | 157MB |
| app-gateway | 143MB | 143MB |
| app-payments | 141MB | 141MB |

Sizes are unchanged — the build context only contains `main.py` and `requirements.txt`, so `.dockerignore` has no practical effect here. It would matter in a real project with `.git/`, `node_modules/`, or large data files.

### `.dockerignore` Content

```
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

### Non-root User

**`whoami` after adding non-root user:**

```
app
```

### Dockerfile Diff

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

### Full Purchase Flow

Reservation ID: `2c76ffcd-894b-452c-9858-96089d076c3c`

### Annotated Request Trace

| # | Timestamp | Service | Action | Delta |
|---|-----------|---------|--------|-------|
| 1 | `16:09:56.493` | **events** | Reserved 1 ticket for event 1, created reservation `2c76ffcd` | — |
| 2 | `16:09:56.495` | **events** | HTTP response: POST `/events/1/reserve` → 200 | +2ms |
| 3 | `16:09:56.496` | **gateway** | Received 200 from `events:8081/events/1/reserve` | +1ms |
| 4 | `16:09:56.498` | **gateway** | HTTP response: POST `/events/1/reserve` → 200 to client | +2ms |
| 5 | `16:09:56.543` | **payments** | Payment success: `PAY-AEDFE9C2` for reservation `2c76ffcd` | +45ms |
| 6 | `16:09:56.544` | **payments** | HTTP response: POST `/charge` → 200 | +1ms |
| 7 | `16:09:56.544` | **gateway** | Received 200 from `payments:8082/charge` | +0ms |
| 8 | `16:09:56.553` | **events** | Order confirmed: `2c76ffcd` | +9ms |
| 9 | `16:09:56.554` | **events** | HTTP response: POST `/reservations/.../confirm` → 200 | +1ms |
| 10 | `16:09:56.554` | **gateway** | Received 200 from `events:8081/reservations/.../confirm` | +0ms |
| 11 | `16:09:56.556` | **gateway** | HTTP response: POST `/reserve/.../pay` → 200 to client | +2ms |

### Request Flow

```
Client → gateway (reserve) → events (reserve in DB + Redis) → gateway (200)
Client → gateway (pay) → payments (charge) → events (confirm) → gateway (200)
```

### End-to-End Timing

- **Reserve:** ~5ms (gateway received at `16:09:56.493`, returned at `16:09:56.498`)
- **Pay:** ~58ms total (`16:09:56.498` → `16:09:56.556`), broken down as:
  - gateway → payments (charge): ~45ms
  - gateway → events (confirm): ~9ms
  - gateway overhead: ~4ms
- **Total end-to-end (reserve + pay):** ~63ms

The payments service dominates latency in the pay flow (~45ms out of ~58ms). The inter-service hops via Docker bridge network add negligible overhead (<1ms each).
