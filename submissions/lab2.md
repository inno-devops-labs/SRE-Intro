# Lab 2 — Containerization: Inspect, Understand, Optimize

## Task 1 — Docker Inspection & Operations

### 1. Image Inspection

#### Docker Images

```bash
$ docker images | grep app
app-events:latest     4d5a050a241a   233MB
app-gateway:latest    852b5e2dcb62   213MB
app-payments:latest   0c54347112a3   211MB
```

The largest image is **app-events (233 MB)**.

#### Layer History

```bash
$ docker history app-events:latest --no-trunc --format "table {{.CreatedBy}}\t{{.Size}}"

CREATED BY                                                     SIZE
CMD ["uvicorn" ...]                                            0B
EXPOSE [8081/tcp]                                              0B
COPY main.py .                                                 20.5kB
RUN pip install --no-cache-dir -r requirements.txt             43.3MB
COPY requirements.txt .                                        12.3kB
WORKDIR /app                                                   8.19kB
...
```

#### Analysis

* The image contains multiple layers created from Dockerfile instructions.
* The largest application-specific layer is:

```text
RUN pip install --no-cache-dir -r requirements.txt
```

Size:

```text
43.3 MB
```

This layer is the largest because all Python dependencies are downloaded and installed during this step.

---

### 2. Container Inspection

#### Service IP Addresses

```bash
$ docker inspect app-events-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-events-1 172.18.0.5

$ docker inspect app-gateway-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-gateway-1 172.18.0.6

$ docker inspect app-payments-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-payments-1 172.18.0.3
```

#### Payments Service Environment Variables

```bash
$ docker inspect app-payments-1 --format '{{range .Config.Env}}{{println .}}{{end}}'

PAYMENT_LATENCY_MS=0
PAYMENT_FAILURE_RATE=0.0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.13
PYTHON_SHA256=2ab91ff401783ccca64f75d10c882e957bdfd60e2bf5a72f8421793729b78a71
```

---

### 3. Live Debugging with `docker exec`

#### Current User

```bash
$ docker exec app-gateway-1 whoami
root
```

```bash
$ docker exec app-gateway-1 id
uid=0(root) gid=0(root) groups=0(root)
```

#### DNS Configuration

```bash
$ docker exec app-gateway-1 cat /etc/resolv.conf

nameserver 127.0.0.11
search .
options edns0 trust-ad ndots:0
```

Docker uses the embedded DNS resolver at:

```text
127.0.0.11
```

#### Connectivity Test: Events Service

```bash
$ docker exec app-gateway-1 python3 -c "
import urllib.request
print(urllib.request.urlopen('http://events:8081/health').read().decode())
"
```

Output:

```json
{
  "status":"healthy",
  "checks":{
    "postgres":"ok",
    "redis":"ok"
  }
}
```

#### Connectivity Test: Payments Service

```bash
$ docker exec app-gateway-1 python3 -c "
import urllib.request
print(urllib.request.urlopen('http://payments:8082/health').read().decode())
"
```

Output:

```json
{
  "status":"healthy",
  "failure_rate":0.0,
  "latency_ms":0
}
```

These checks confirm that service discovery inside the Docker network works correctly.

---

### 4. Logs Analysis

Traffic generated:

```bash
curl -s http://localhost:3080/events > /dev/null

curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity":1}'
```

Reservation created:

```json
{
  "reservation_id":"b3bf7d48-1b86-498d-aead-1d59be271c20",
  "event_id":1,
  "quantity":1,
  "total_cents":5000,
  "expires_in_seconds":300
}
```

#### Gateway Logs

```text
{"time":"2026-06-08 09:56:21,087","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve HTTP/1.1 200 OK"}
INFO: 172.18.0.1:50728 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

#### Events Logs

```text
{"time":"2026-06-08 09:56:21,086","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: b3bf7d48-1b86-498d-aead-1d59be271c20"}
INFO: 172.18.0.6:47574 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

#### Analysis

The timestamps match:

```text
events:  09:56:21.086
gateway: 09:56:21.087
```

This demonstrates a single request flowing through:

```text
gateway → events
```

---

### 5. Network Inspection

```bash
$ docker network ls | grep app

29fd56a854fb   app_default   bridge   local
```

```bash
$ docker network inspect app_default --format '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}'

app-payments-1: 172.18.0.3/16
app-postgres-1: 172.18.0.2/16
app-gateway-1: 172.18.0.6/16
app-redis-1: 172.18.0.4/16
app-events-1: 172.18.0.5/16
```

---

### 6. Service Discovery Explanation

The gateway finds the events service using Docker's internal DNS server.

Inside the container:

```text
nameserver 127.0.0.11
```

When the gateway sends a request to:

```text
http://events:8081
```

Docker resolves the hostname:

```text
events
```

to:

```text
172.18.0.5
```

Therefore:

```text
gateway → Docker DNS → events (172.18.0.5)
```

---

## Task 2 — Dockerfile Optimization

### 1. Added `.dockerignore`

Created in:

```text
app/events/.dockerignore
app/gateway/.dockerignore
app/payments/.dockerignore
```

Content:

```text
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

---

### 2. Image Size Comparison

#### Before

```bash
app-events:latest     233MB
app-gateway:latest    213MB
app-payments:latest   211MB
```

#### After

```bash
app-events:latest     233MB
app-gateway:latest    213MB
app-payments:latest   211MB
```

#### Analysis

No measurable image size reduction occurred.

Reason:

* Build context is already very small.
* No large `.git` directory or unnecessary files were included.

---

### 3. Running Containers as Non-Root

Verification:

```bash
$ docker exec app-gateway-1 whoami

app
```

The container now runs as a dedicated non-root user.

---

### 4. Dockerfile Changes

```diff
diff --git a/app/events/Dockerfile b/app/events/Dockerfile

 EXPOSE 8081
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]

diff --git a/app/gateway/Dockerfile b/app/gateway/Dockerfile

 EXPOSE 8080
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

diff --git a/app/payments/Dockerfile b/app/payments/Dockerfile

 EXPOSE 8082
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8082"]
```

#### Security Benefit

Running as a non-root user reduces the impact of a container compromise because processes no longer have root privileges inside the container.

---

## Bonus Task — Trace a Request Across Services

### Request Flow

Reservation:

```text
01c5497f-9065-4a25-bdb2-2fab97cb59c0
```

#### Step 1 — Gateway → Events (Reserve)

```text
2026-06-08 10:52:09.992
gateway:
POST http://events:8081/events/1/reserve
```

```text
2026-06-08 10:52:09.990
events:
Reserved 1 tickets for event 1
```

---

#### Step 2 — Gateway → Payments

```text
2026-06-08 10:52:10.024
gateway:
POST http://payments:8082/charge
```

```text
2026-06-08 10:52:10.023
payments:
Payment success: PAY-DF70D968
```

---

#### Step 3 — Gateway → Events (Confirm)

```text
2026-06-08 10:52:10.031
gateway:
POST /reservations/.../confirm
```

```text
2026-06-08 10:52:10.030
events:
Order confirmed
```

---

### Timing Analysis

| Step                | Timestamp    |
| ------------------- | ------------ |
| Reservation created | 10:52:09.990 |
| Payment processed   | 10:52:10.023 |
| Order confirmed     | 10:52:10.030 |
| Response returned   | 10:52:10.032 |

Approximate end-to-end time:

```text
10:52:10.032 - 10:52:09.990
≈ 42 ms
```

### Conclusion

The complete purchase flow successfully traversed:

```text
gateway
   ↓
events (reserve)
   ↓
payments (charge)
   ↓
events (confirm)
   ↓
gateway response
```

Total processing time was approximately **42 milliseconds**.
