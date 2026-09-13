# Lab 2 — Containerization

## Task 1 — Docker inspection and operations

The local PostgreSQL port `5432` was already used by another development project. I used a temporary Compose override to publish QuickTicket PostgreSQL on `15432`; service-to-service traffic still used `postgres:5432` inside `app_default`.

### Images and layers

Before optimization:

```text
REPOSITORY     TAG      IMAGE ID       SIZE
app-events    latest   513ca428e614   191MB
app-gateway   latest   5a611a6ca284   176MB
app-payments  latest   1f9ae3db96d4   174MB
```

`app-events` is the largest image at 191 MB because it includes the PostgreSQL and Redis clients in addition to FastAPI. I inspected `app-gateway` as required by the task:

```text
CREATED BY                                                   SIZE
CMD ["uvicorn" "main:app" "--host" "0.0.0.0" ...]          0B
EXPOSE [8080/tcp]                                            0B
COPY main.py .                                               13kB
RUN pip install --no-cache-dir -r requirements.txt           24.7MB
COPY requirements.txt .                                      73B
WORKDIR /app                                                 0B
... Python base-image instructions ...
```

The gateway image had 8 filesystem layers before optimization. Its largest application-owned layer was `pip install` at 24.7 MB because it contained FastAPI, Uvicorn, HTTPX, Prometheus client, and their dependencies. The 101 MB Debian root filesystem layer was the largest layer overall, but it came from the `python:3.13-slim` base image rather than our Dockerfile.

### Container addresses

```text
/app-events-1 172.21.0.5
/app-gateway-1 172.21.0.6
/app-payments-1 172.21.0.4
```

Payments environment:

```text
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
PYTHON_SHA256=1e66a7945a48390ee4c2a4268a0e4185884059a13c4aab6d148aa208deea4a76
PYTHON_VERSION=3.13.15
```

### Debugging from inside gateway

Before optimization the process ran as root:

```text
$ docker exec app-gateway-1 whoami
root
$ docker exec app-gateway-1 id
uid=0(root) gid=0(root) groups=0(root)
```

Docker supplied its embedded DNS resolver:

```text
$ docker exec app-gateway-1 cat /etc/resolv.conf
nameserver 127.0.0.11
search msk.avito.ru
options edns0 trust-ad ndots:0
```

Service-name resolution and internal health requests:

```text
$ docker exec app-gateway-1 python3 -c "import socket; ..."
events: 172.21.0.5
payments: 172.21.0.4

$ docker exec app-gateway-1 python3 -c "import urllib.request; ... events:8081/health ..."
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}

$ docker exec app-gateway-1 python3 -c "import urllib.request; ... payments:8082/health ..."
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

The gateway reads `EVENTS_URL=http://events:8081` from its environment. Docker Compose registers the service name `events` with embedded DNS at `127.0.0.11`, which resolved it to `172.21.0.5` during this run. The IP is dynamic and may change after containers are recreated, so configuration correctly uses the stable service name rather than the IP.

### Logs for one gateway → events request

```text
events-1  | 2026-09-13T13:40:12.137417266Z ... "Reserved 1 tickets for event 3: a62069fe-fd5b-4488-b5b1-1c156d8519e4"
gateway-1 | 2026-09-13T13:40:12.138615186Z ... "HTTP Request: POST http://events:8081/events/3/reserve ... 200 OK"
```

The events log records the reservation first. About 1.20 ms later, the gateway logs the successful downstream response.

### Compose network

```text
$ docker network ls | grep app
app_default bridge

$ docker network inspect app_default --format '...'
app-events-1: 172.21.0.5/16
app-gateway-1: 172.21.0.6/16
app-payments-1: 172.21.0.4/16
app-postgres-1: 172.21.0.2/16
app-redis-1: 172.21.0.3/16
```

All five containers share the Compose bridge network. Host port publishing is not needed for calls between them; they communicate using service names and container ports.

## Task 2 — Dockerfile optimization

I added this `.dockerignore` to all three build contexts:

```text
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

I also added a system user to every Dockerfile:

```diff
 COPY main.py .
 
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
+
 EXPOSE 8080
```

The events and payments Dockerfiles contain the same change before their respective `EXPOSE` and `CMD` instructions.

After a no-cache rebuild:

```text
REPOSITORY     TAG      IMAGE ID       SIZE
app-gateway   latest   6dccb7d37890   176MB
app-events    latest   2e3bec4167d4   191MB
app-payments  latest   1a4939eb05dd   174MB
```

The displayed sizes did not change. Each build context already contained only `main.py`, `requirements.txt`, and the Dockerfile, so `.dockerignore` had almost nothing to remove. Creating the system user added one small filesystem layer, taking gateway from 8 to 9 layers, but it was below Docker's displayed MB precision.

All application containers now run as non-root:

```text
$ docker exec app-gateway-1 whoami
app
$ docker exec app-gateway-1 id
uid=100(app) gid=101(app) groups=101(app)

$ docker exec app-events-1 whoami
app
$ docker exec app-payments-1 whoami
app
```

The final gateway health check returned:

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

## Bonus — Trace a purchase across services

Reservation ID: `a62069fe-fd5b-4488-b5b1-1c156d8519e4`.

```text
13:40:12.137417 events   Reserved 1 ticket for event 3
13:40:12.138615 gateway  Received 200 from events reserve       (+1.20 ms)
13:40:12.210902 payments Charged reservation successfully       (+72.29 ms)
13:40:12.211734 gateway  Received 200 from payments             (+0.83 ms)
13:40:12.216023 events   Confirmed the order                    (+4.29 ms)
13:40:12.216386 events   Returned 200 for confirmation          (+0.36 ms)
13:40:12.216894 gateway  Received confirmation response         (+0.51 ms)
13:40:12.217519 gateway  Returned 200 to the client              (+0.62 ms)
```

Full timestamped log lines:

```text
events-1   | 2026-09-13T13:40:12.137417266Z ... "Reserved 1 tickets for event 3: a62069fe-fd5b-4488-b5b1-1c156d8519e4"
gateway-1  | 2026-09-13T13:40:12.138615186Z ... "HTTP Request: POST http://events:8081/events/3/reserve ... 200 OK"
payments-1 | 2026-09-13T13:40:12.210902184Z ... "Payment success: PAY-3EF69046 for a62069fe-fd5b-4488-b5b1-1c156d8519e4"
gateway-1  | 2026-09-13T13:40:12.211734939Z ... "HTTP Request: POST http://payments:8082/charge ... 200 OK"
events-1   | 2026-09-13T13:40:12.216023690Z ... "Order confirmed: a62069fe-fd5b-4488-b5b1-1c156d8519e4"
events-1   | 2026-09-13T13:40:12.216386443Z ... "POST /reservations/a62069fe-fd5b-4488-b5b1-1c156d8519e4/confirm HTTP/1.1" 200 OK
gateway-1  | 2026-09-13T13:40:12.216894239Z ... "HTTP Request: POST http://events:8081/reservations/a62069fe-fd5b-4488-b5b1-1c156d8519e4/confirm ... 200 OK"
gateway-1  | 2026-09-13T13:40:12.217519058Z ... "POST /reserve/a62069fe-fd5b-4488-b5b1-1c156d8519e4/pay HTTP/1.1" 200 OK
```

The client-observed end-to-end time for the pay request was `0.019624` seconds, or 19.624 ms. The visible server-side interval from payment success to the final gateway response was about 6.62 ms. The initial reservation was a separate request immediately before payment, which is why there is a gap between its log line and the payment trace.
