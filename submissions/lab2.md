# Lab 2 — Containerization: Inspect, Understand, Optimize

## Task 1 — Docker Inspection & Operations

### 1. Image Inspection

```text
app-events:latest     a97981790657    165MB
app-gateway:latest    6dd063c6e497    151MB
app-payments:latest   ed6c03cee69a    150MB
```

Gateway image history:

```text
CREATED BY                                                       SIZE
CMD ["uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8080"]   0B
EXPOSE [8080/tcp]                                                0B
COPY main.py .                                                   13kB
RUN pip install --no-cache-dir -r requirements.txt               25.1MB
COPY requirements.txt .                                          73B
WORKDIR /app                                                     0B
CMD ["python3"]                                                   0B
RUN ...                                                           36B
RUN ... Python installation ...                                  35.6MB
ENV PYTHON_SHA256=...                                             0B
ENV PYTHON_VERSION=3.13.15                                       0B
ENV GPG_KEY=...                                                   0B
RUN ... apt packages ...                                         12MB
ENV PATH=...                                                      0B
# debian.sh --arch 'amd64' ...                                   78.6MB
```

The gateway image has 15 history entries, 7 of which add filesystem data. The largest layer is the 78.6 MB Debian base filesystem layer. The `pip install --no-cache-dir -r requirements.txt` layer adds 25.1 MB and contains the Python dependencies.

### 2. Container Inspection

Service IP addresses:

```text
/app-events-1 172.20.0.5
/app-gateway-1 172.20.0.6
/app-payments-1 172.20.0.2
```

Payments environment:

```text
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.15
PYTHON_SHA256=1e66a7945a48390ee4c2a4268a0e4185884059a13c4aab6d148aa208deea4a76
```

### 3. Live Debugging and Service Discovery

Before optimization, the gateway container was running as root:

```text
root
uid=0(root) gid=0(root) groups=0(root)
```

Docker DNS configuration inside the gateway:

```text
nameserver 127.0.0.11
search .
options edns0 trust-ad ndots:0
```

Calling the events service by its Compose service name from inside the gateway:

```text
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
```

Calling payments from inside the gateway:

```text
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

Docker Compose provides service discovery through Docker's embedded DNS server at `127.0.0.11`. The gateway does not need to know the events container IP directly: it requests the hostname `events`, which Docker DNS resolves to the events container on the Compose network. During this inspection, `events` resolved to `172.20.0.5`.

### 4. Logs Analysis

A reservation request could be followed from the gateway to the events service using timestamps:

```text
events-1  | 2026-09-13T14:32:44.781787012Z {"level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 200cf967-4f4a-410f-b4f2-e17802584c51"}
events-1  | 2026-09-13T14:32:44.783273414Z INFO: 172.20.0.6:40876 - "POST /events/1/reserve HTTP/1.1" 200 OK
gateway-1 | 2026-09-13T14:32:44.784576122Z {"level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve \"HTTP/1.1 200 OK\""}
gateway-1 | 2026-09-13T14:32:44.787099877Z INFO: 172.20.0.1:42008 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

The timestamps show that the gateway forwarded the request to `events`, the events service completed the reservation, and the gateway then returned `200 OK` to the client.

### 5. Network Inspection

```text
app-redis-1: 172.20.0.3/16
app-events-1: 172.20.0.5/16
app-payments-1: 172.20.0.2/16
app-gateway-1: 172.20.0.6/16
app-postgres-1: 172.20.0.4/16
```

All five containers are connected to the `app_default` bridge network.

---

## Task 2 — Dockerfile Optimization

### 1. `.dockerignore`

The following `.dockerignore` was added to `gateway`, `events`, and `payments`:

```text
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

### 2. Image Size Comparison

Before:

```text
app-events:latest     165MB
app-gateway:latest    151MB
app-payments:latest   150MB
```

After rebuilding without cache:

```text
app-events:latest     165MB
app-gateway:latest    151MB
app-payments:latest   150MB
```

The displayed image sizes did not change. This is expected because the service build contexts are already very small and the Dockerfiles explicitly copy only `requirements.txt` and `main.py`. The `.dockerignore` still prevents unnecessary files from being sent in the build context.

### 3. Non-root User

The following lines were added to each service Dockerfile before `CMD`:

```dockerfile
RUN addgroup --system app && adduser --system --ingroup app app
USER app
```

After rebuilding:

```text
app
uid=100(app) gid=101(app) groups=101(app)
```

The application now runs as the non-root `app` user.

The system remained healthy after the change:

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

### 4. Dockerfile Diff

```diff
diff --git a/app/events/Dockerfile b/app/events/Dockerfile
@@
 COPY main.py .

 EXPOSE 8081
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
+
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]

diff --git a/app/gateway/Dockerfile b/app/gateway/Dockerfile
@@
 COPY main.py .

 EXPOSE 8080
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
+
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

diff --git a/app/payments/Dockerfile b/app/payments/Dockerfile
@@
 COPY main.py .

 EXPOSE 8082
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
+
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8082"]
```

---

## Bonus Task — Trace a Request Across Services

Reservation:

```json
{
    "reservation_id": "200cf967-4f4a-410f-b4f2-e17802584c51",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "expires_in_seconds": 300
}
```

Payment:

```json
{
    "order_id": "200cf967-4f4a-410f-b4f2-e17802584c51",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "status": "confirmed"
}
```

### Timestamped Trace

```text
14:32:44.781787 — events:
Reserved 1 ticket for event 1.

14:32:44.783273 — events:
POST /events/1/reserve completed with 200 OK.

14:32:44.784576 — gateway:
Received the successful response from http://events:8081/events/1/reserve.

14:32:44.787100 — gateway:
Returned the reserve response to the client.

14:32:44.856312 — payments:
Payment succeeded and payment reference PAY-0128C817 was created.

14:32:44.857191 — payments:
POST /charge completed with 200 OK.

14:32:44.858193 — gateway:
Received the successful response from http://payments:8082/charge.

14:32:44.869379 — events:
Reservation was confirmed after successful payment.

14:32:44.870174 — events:
POST /reservations/200cf967-4f4a-410f-b4f2-e17802584c51/confirm completed with 200 OK.

14:32:44.871091 — gateway:
Received the successful confirmation response from events.

14:32:44.872727 — gateway:
Returned the final POST /reserve/200cf967-4f4a-410f-b4f2-e17802584c51/pay response with 200 OK.
```

The observed flow was:

```text
client
  -> gateway
  -> events (reserve)
  -> gateway
  -> payments (charge)
  -> gateway
  -> events (confirm)
  -> gateway
  -> client
```

Timing between the main observable hops:

```text
events reserve -> gateway observes reserve response: ~2.8 ms
gateway reserve response -> payment success: ~69.2 ms
payment success -> gateway observes payment response: ~1.9 ms
gateway payment response -> events confirmation: ~11.2 ms
events confirmation -> gateway observes confirmation: ~1.7 ms
gateway confirmation response -> final client response: ~1.6 ms
```

From the first observable reservation log at `14:32:44.781787` to the final gateway response at `14:32:44.872727`, the complete purchase trace took approximately **90.9 ms**.

The logs do not contain a separate timestamp for the exact instant when the gateway initially received the first request, so 90.9 ms is the closest end-to-end duration measurable from the required timestamped service logs.