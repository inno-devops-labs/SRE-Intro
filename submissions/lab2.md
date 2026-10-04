# Lab 2 — Containerization

## Image Inspection

Before changes: `app-gateway 226MB`, `app-events 245MB`, `app-payments 223MB`. Gateway had the pip-install layer at 29.6MB; a Python base-image layer was larger at 87.5MB, so pip was not the largest layer. The gateway image had 13 history entries in this environment.

## Container Inspection

```text
/app-gateway-1 172.27.0.6
/app-events-1 172.27.0.5
/app-payments-1 172.27.0.2
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
```

Before the change, gateway ran as:

```text
root
uid=0(root) gid=0(root) groups=0(root)
```

## Service Discovery

Inside gateway, `events` resolved to `172.27.0.5`; `/etc/resolv.conf` used `nameserver 127.0.0.11`. Compose created the `app_default` bridge network and its embedded DNS resolves the service hostname to that dynamic container IP.

```text
GET http://events:8081/health -> {"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
GET http://payments:8082/health -> {"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

## Live Debugging

The Python health calls above succeeded from the gateway container. `docker network inspect app_default` showed gateway `172.27.0.6/16`, events `172.27.0.5/16`, payments `172.27.0.2/16`, postgres `172.27.0.4/16`, and redis `172.27.0.3/16`.

## Logs Analysis

Gateway and Events logs contain endpoint requests at close timestamps, but ordinary list/reserve requests have no request ID. That is only temporal/endpoint correlation and is not reliable distributed tracing.

## Network Inspection

All five containers were attached to the Compose-created `app_default` network. Service names, not hard-coded IPs, are used by gateway (`http://events:8081` and `http://payments:8082`).

## Dockerfile Optimization

Added the required `.dockerignore` to each Python service and added a system `app` user before each `CMD`. No `chown` was needed: the applications ran successfully as the non-root user.

```text
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

## Before/After Comparison

After a no-cache rebuild: gateway `226MB`, events `245MB`, payments `223MB`; image sizes did not materially change. The contexts are already small and Dockerfiles copy only requirements and main.py, so `.dockerignore` mainly protects future build contexts and secrets.

```text
docker exec app-gateway-1 whoami -> app
uid=100(app) gid=101(app) groups=101(app)
GET /health -> {"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
POST full purchase -> {"order_id":"49780e41-7d1a-4f55-b423-86b9eb06c2a3",...,"status":"confirmed"}
```

## Request Tracing Bonus

For reservation `ef1d0627-6bfa-4516-97a7-bbe6547f5bc9`, timestamped logs showed Events reserve at `22:14:31.328`, Payments success (`PAY-6EE0D77F`) at `22:14:31.403`, Events confirmation at `22:14:31.410`, and gateway’s 200 response at `22:14:31.412`. The observable reserve-to-pay-response interval was about 84 ms; gateway does not log receipt of the reserve request, so an exact whole-flow duration cannot be calculated from logs alone.

## Conclusions

The services communicate through Compose DNS on a bridge network. Running as `app` reduces container privilege without changing application behavior.

## Raw Command Evidence

```text
docker image ls app-gateway
app-gateway:latest  226MB
docker image ls app-events
app-events:latest   245MB
docker image ls app-payments
app-payments:latest 223MB

docker history app-gateway --no-trunc --format "{{.CreatedBy}} | {{.Size}}"
CMD ["uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8080"] | 0B
USER app | 0B
RUN /bin/sh -c addgroup --system app && adduser --system --ingroup app app # buildkit | 45.1kB
EXPOSE [8080/tcp] | 0B
COPY main.py . # buildkit | 24.6kB
RUN /bin/sh -c pip install --no-cache-dir -r requirements.txt # buildkit | 29.6MB
COPY requirements.txt . # buildkit | 12.3kB
WORKDIR /app | 8.19kB
# debian.sh --arch 'amd64' out/ 'trixie' '@1787529600' | 87.5MB

docker compose -f app/docker-compose.yaml ps
app-events-1    app-events           Up
app-gateway-1   app-gateway          Up
app-payments-1  app-payments         Up
app-postgres-1  postgres:17-alpine   Up (healthy)
app-redis-1     redis:7-alpine       Up (healthy)

docker inspect
/app-gateway-1 172.27.0.6
/app-events-1 172.27.0.5
/app-payments-1 172.27.0.3
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
PYTHON_VERSION=3.13.15

docker exec gateway whoami; docker exec gateway id
app
uid=100(app) gid=101(app) groups=101(app)
docker exec gateway cat /etc/resolv.conf
nameserver 127.0.0.11
options ndots:0
docker exec gateway python3 -c "import socket,urllib.request;print(socket.gethostbyname('events'));print(urllib.request.urlopen('http://events:8081/health').read().decode())"
172.27.0.5
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}

docker network inspect app_default
app-postgres-1: 172.27.0.4/16
app-payments-1: 172.27.0.3/16
app-gateway-1: 172.27.0.6/16
app-events-1: 172.27.0.5/16
app-redis-1: 172.27.0.2/16
```

The 29.6MB `pip install` layer is identified above. It is not the largest: the 87.5MB Debian base layer is larger. Docker embedded DNS at `127.0.0.11` resolves `events` to the current internal bridge IP; the address is dynamic and services use the hostname.

```diff
git diff main...feature/lab2 -- app/gateway/Dockerfile app/events/Dockerfile app/payments/Dockerfile
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
(these two added lines are present in all three Dockerfiles)
```

## Request Tracing Bonus — Full Timestamped Evidence

The stack was restarted with `docker compose -f app/docker-compose.yaml down` then `up -d`, without `-v`. `/health` returned HTTP 200 before this purchase.

```text
RESERVE={"reservation_id":"b39f6976-44e6-4344-8ce1-95d9bcc7195f","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
PAY={"order_id":"b39f6976-44e6-4344-8ce1-95d9bcc7195f","event_id":1,"quantity":1,"total_cents":5000,"status":"confirmed"}

events-1   | 2026-09-15T22:46:51.426682813Z {"time":"2026-09-15 22:46:51,426","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: b39f6976-44e6-4344-8ce1-95d9bcc7195f"}
payments-1 | 2026-09-15T22:46:51.479888471Z {"time":"2026-09-15 22:46:51,479","level":"INFO","service":"payments","msg":"Payment success: PAY-101ECFE3 for b39f6976-44e6-4344-8ce1-95d9bcc7195f"}
events-1   | 2026-09-15T22:46:51.490419070Z INFO: "POST /reservations/b39f6976-44e6-4344-8ce1-95d9bcc7195f/confirm HTTP/1.1" 200 OK
gateway-1  | 2026-09-15T22:46:51.491047803Z {"service":"gateway","msg":"HTTP Request: POST http://events:8081/reservations/b39f6976-44e6-4344-8ce1-95d9bcc7195f/confirm \"HTTP/1.1 200 OK\""}
gateway-1  | 2026-09-15T22:46:51.492051037Z INFO: "POST /reserve/b39f6976-44e6-4344-8ce1-95d9bcc7195f/pay HTTP/1.1" 200 OK
```

| Timestamp | Service | Action | Correlation data |
|---|---|---|---|
| 22:46:51.426 | events | Created reservation | `b39f6976-44e6-4344-8ce1-95d9bcc7195f` |
| 22:46:51.479 (+53 ms) | payments | Charged reservation | `PAY-101ECFE3`, reservation ID |
| 22:46:51.490 (+11 ms) | events/gateway | Confirmed order | reservation ID |
| 22:46:51.492 (+2 ms) | gateway | Returned HTTP 200 | reservation ID |

Observable execution time is `65.368 ms` (`22:46:51.492051037 - 22:46:51.426682813`). This is not guaranteed to be full end-to-end latency because gateway does not log the receipt timestamp of the initial reserve request; there is no shared request ID or distributed tracing.
