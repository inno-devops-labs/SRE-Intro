# Task 1 — Docker Inspection & Operations

## Exercise 2.1 — Image Inspection

### Docker Images

```bash
docker images | grep app
```

Output:

```text
app-events:latest    233MB
app-gateway:latest   214MB
app-payments:latest  212MB
```

### Docker History (`app-events`)

```bash
docker history app-events --no-trunc --format "table {{.CreatedBy}}\t{{.Size}}"
```

<full output>

### Analysis

The largest image is **app-events** with a size of **233 MB**.

The image consists of multiple layers inherited from the base Python image and layers created by Dockerfile instructions.

The largest application-specific layer is:

```text
RUN pip install --no-cache-dir -r requirements.txt
```

Size:

```text
43.6 MB
```

This layer is the largest because it installs all Python dependencies required by the service.

The image also contains large layers inherited from the `python:3.13-slim` base image, including the Python runtime and operating system packages.

---

## Exercise 2.2 — Container Inspection

### Service IP Addresses

```text
app-events-1   -> 172.26.0.3
app-gateway-1  -> 172.26.0.5
app-payments-1 -> 172.26.0.2
```

### Payments Service Environment Variables

```text
PAYMENT_LATENCY_MS=0
PAYMENT_FAILURE_RATE=0.0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.14
PYTHON_SHA256=639e43243c620a308f968213df9e00f2f8f62332f7adbaa7a7eeb9783057c690
```

### Analysis

All services are connected to the same Docker network and receive private IP addresses from the `172.26.0.0/16` subnet.

The payments service configuration is provided through environment variables, including payment latency and failure rate settings.

---

## Exercise 2.3 — Live Debugging with `docker exec`

### User Information

```text
whoami -> root

uid=0(root) gid=0(root) groups=0(root)
```

### DNS Configuration

```text
nameserver 127.0.0.11
search .
options edns0 trust-ad ndots:0
```

Docker uses its embedded DNS server (`127.0.0.11`) for service discovery.

### Service Connectivity Tests

#### Events Service

```text
HTTP Error 503: Service Unavailable
```

#### Payments Service

```json
{
  "status": "healthy",
  "failure_rate": 0.0,
  "latency_ms": 0
}
```

### Analysis

The gateway container successfully resolves service names using Docker DNS.

The payments service responded successfully and reported a healthy status.

The events service returned HTTP 503 because one of its dependencies was unavailable during startup, which is also visible in the service logs.

---

## Exercise 2.4 — Logs Analysis

### Initial Logs

The services started successfully and began listening on their configured ports.

The events service reported a Redis connection issue:

```text
Redis connection failed: Error -3 connecting to redis:6379. Temporary failure in name resolution.
```

### Logs After Generating Traffic

Gateway:

```text
HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK"
HTTP Request: POST http://events:8081/events/1/reserve "HTTP/1.1 200 OK"
```

Events:

```text
Reserved 1 tickets for event 1: 58abe1e8-de0d-4725-9e0b-9f51ecafaaf9
POST /events/1/reserve HTTP/1.1 200 OK
```

### Analysis

The same request can be traced across multiple services using timestamps.

The gateway receives the client request and forwards it to the events service.

The events service processes the reservation request and returns a successful response.

---

## Exercise 2.5 — Network Inspection

### Docker Network

```text
app_default
```

### Connected Containers

```text
app-events-1: 172.26.0.3/16
app-gateway-1: 172.26.0.5/16
app-payments-1: 172.26.0.2/16
app-postgres-1: 172.26.0.4/16
```

### Analysis

All application services are connected to the same bridge network (`app_default`) and can communicate with each other using service names.

---

## Final Answers

### How does the gateway find the events service?

Docker Compose automatically creates an internal DNS service for the network. When the gateway sends a request to `http://events:8081`, the hostname `events` is resolved by Docker's embedded DNS server (`127.0.0.11`) to the IP address of the events container.

### What IP does `events` resolve to?

```text
events -> 172.26.0.3
```

Therefore, communication between services happens through Docker's internal network rather than localhost or public IP addresses.