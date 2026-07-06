# Lab 2 — Containerization: Inspect, Understand, Optimize

![difficulty](https://img.shields.io/badge/difficulty-beginner-success)
![topic](https://img.shields.io/badge/topic-Containers-blue)
![points](https://img.shields.io/badge/points-10%2B2-orange)
![tech](https://img.shields.io/badge/tech-Docker-informational)

> **Goal:** Understand how QuickTicket containers work under the hood — images, layers, networking, operational commands — and optimize them.

---

## Task 1 — Docker Inspection & Operations

### 2.1: Image inspection

#### Docker images

```text
docker images | grep app
app-events:latest                             157MB
app-gateway:latest                            143MB
app-payments:latest                           141MB
```

#### Docker history for `app-gateway`

```text
RUN /bin/sh -c pip install --no-cache-dir -r requirements.txt # buildkit	25MB
COPY main.py . # buildkit	14.3kB
RUN /bin/sh -c addgroup --system app && adduser --system --ingroup app app # buildkit	4.3kB
EXPOSE [8080/tcp]	0B
USER app	0B
CMD ["uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8080"]	0B
```

- The gateway image has **17 layers**.
- The largest layer is the `RUN pip install --no-cache-dir -r requirements.txt` layer.
- This is the largest because it installs the Python dependencies for the service.

### 2.2: Container inspection

#### Service IP addresses

```text
/app-events-1 172.21.0.5
/app-gateway-1 172.21.0.6
/app-payments-1 172.21.0.3
```

#### Payments service environment variables

```text
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.14
PYTHON_SHA256=639e43243c620a308f968213df9e00f2f8f62332f7adbaa7a7eeb9783057c690
```

### 2.3: Live debugging with `docker exec`

#### User inside gateway container

```text
app
```

#### DNS configuration inside gateway

```text
nameserver 127.0.0.11
search .
options edns0 trust-ad ndots:0
```

#### Gateway connectivity checks

```json
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
```

```json
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

### 2.4: Logs analysis

#### Request flow snippet

Gateway:

```text
gateway-1  | {"time":"2026-07-06 11:21:10,304","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve \"HTTP/1.1 200 OK\""}
```

Events:

```text
events-1  | {"time":"2026-07-06 11:21:10,301","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 0f3f1c16-d5ce-4161-a610-8aea567e1102"}
```

This shows the gateway receiving the request and forwarding it to the events service.

### 2.5: Network inspection

#### Docker network

```text
app_default
```

#### Connected containers and IPs

```text
app-postgres-1: 172.21.0.4/16
app-payments-1: 172.21.0.3/16
app-events-1: 172.21.0.5/16
app-redis-1: 172.21.0.2/16
app-gateway-1: 172.21.0.6/16
```

### Final answers

- **How does the gateway find the events service?**
  Docker Compose provides internal DNS on the application network. The gateway resolves `events` through the embedded DNS server and connects to the events container.
- **What IP does `events` resolve to?**
  `events` resolves to `172.21.0.5`.

---

## Task 2 — Dockerfile Optimization

### 2.7: `.dockerignore` content

Each service directory contains the same `.dockerignore`:

```text
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

### 2.8: Non-root user configuration

Each service Dockerfile already includes:

```dockerfile
RUN addgroup --system app && adduser --system --ingroup app app
USER app
```

### Image size comparison

#### Without `.dockerignore`
```text
app-events:latest   157MB
app-gateway:latest  143MB
app-payments:latest 141MB
```

#### With `.dockerignore`
```text
app-events:latest   157MB
app-gateway:latest  143MB
app-payments:latest 141MB
```

- No size difference was observed because the build context is already small.

### Non-root verification

```text
app
```

- `whoami` inside `app-gateway-1` prints `app`, confirming the container runs as a non-root user.

### Git diff of Dockerfile changes

- `git diff` is empty for `app/gateway/Dockerfile`, `app/events/Dockerfile`, and `app/payments/Dockerfile`.
- The non-root lines are already present in the repository.

---

## Bonus Task — Trace a Request Across Services

### Timestamped request flow

```text
gateway-1   | 2026-07-06T11:21:10.305741785Z INFO:     172.21.0.1:49966 - "POST /events/1/reserve HTTP/1.1" 200 OK
events-1    | 2026-07-06T11:21:10.303128676Z INFO:     172.21.0.6:60724 - "POST /events/1/reserve HTTP/1.1" 200 OK
payments-1  | 2026-07-06T11:21:10.372430097Z INFO:     172.21.0.6:51684 - "POST /charge HTTP/1.1" 200 OK
events-1    | 2026-07-06T11:21:10.382570694Z INFO:     172.21.0.6:60724 - "POST /reservations/0f3f1c16-d5ce-4161-a610-8aea567e1102/confirm HTTP/1.1" 200 OK
gateway-1   | 2026-07-06T11:21:10.384039315Z INFO:     172.21.0.1:49970 - "POST /reserve/0f3f1c16-d5ce-4161-a610-8aea567e1102/pay HTTP/1.1" 200 OK
```

### Annotations

- `gateway-1 11:21:10.305` — gateway receives the reservation request and forwards it to events.
- `events-1 11:21:10.303` — events service processes `/events/1/reserve` and creates the reservation.
- `payments-1 11:21:10.372` — gateway sends payment request to payments service.
- `events-1 11:21:10.382` — events service handles the confirmation callback.
- `gateway-1 11:21:10.384` — gateway returns the final `/reserve/.../pay` response.

### End-to-end time

- Total end-to-end time is approximately **78 milliseconds** from gateway receive to response.
