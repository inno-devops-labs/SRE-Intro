# Lab 2 — Containerization: Inspect, Understand, Optimize

## Task 1 — Docker Inspection & Operations

### Image inspection

```text
app-events      latest    261MB    57.7MB
app-gateway     latest    240MB    52.4MB
app-payments    latest    237MB    51.9MB
```

Gateway image: 18 layers. The largest layer is the Debian base layer (~110 MB). The `pip install` layer is ~29.2 MB.

### Container inspection

```text
events     172.19.0.5
gateway    172.19.0.6
payments   172.19.0.2
```

Payments environment:

```text
PAYMENT_LATENCY_MS=0
PAYMENT_FAILURE_RATE=0.0
```

### Live debugging

```text
whoami: app
events health: 200
payments health: 200
DNS: 127.0.0.11
```

### Logs

Gateway:

```text
2026-09-28 17:31:41.751 POST http://events:8081/events/1/reserve HTTP/1.1 200 OK
```

Events:

```text
2026-09-28 17:31:41.749 Reserved 1 tickets for event 1
POST /events/1/reserve HTTP/1.1 200 OK
```

The request flowed from gateway to events successfully.

### Network

```text
events    172.19.0.5
gateway   172.19.0.6
payments  172.19.0.2
postgres  172.19.0.4
redis     172.19.0.3
```

### Docker DNS

The gateway finds the events service using Docker's embedded DNS at `127.0.0.11`. The service name `events` resolves to `172.19.0.5`.

## Task 2 — Dockerfile Optimization

### .dockerignore

```text
__pycache__/
*.pyc
.git/
.env
*.md
.vscode
```

Added to gateway, events and payments.

### Non-root user

```text
app
```

All three containers now run as the `app` user.

### Dockerfile changes

```dockerfile
RUN groupadd --system app &&     useradd --system --gid app --home-dir /app --no-create-home app

ENV PYTHONDONTWRITEBYTECODE=1

USER app
```
