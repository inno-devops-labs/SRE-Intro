# Lab 2 - Containerization: Inspect, Understand, Optimize

## Task 1: Docker Inspection and Operations

### 2.1 Image inspection

Before optimization: `docker images | grep app`

```text
REPOSITORY    TAG       SIZE
app-gateway   latest    151MB
app-events    latest    165MB
app-payments  latest    150MB
```

The largest image was `app-events` at 165 MB. The gateway layer history showed 25.1 MB for the `pip install --no-cache-dir -r requirements.txt` layer. This is the largest application-specific layer because it contains the Python dependencies. The base `python:3.13-slim` layers account for most of the remaining image size.

Relevant gateway history after rebuilding:

```bash
docker history app-gateway --no-trunc --format "table {{.CreatedBy}}\t{{.Size}}"
```

```text
CMD ["uvicorn" "main:app" "--host" "0.0.0.0"... | 0B
EXPOSE [8080/tcp] | 0B
RUN /bin/sh -c addgroup --system app && adduser --system --ingroup app app | 4.3kB
COPY main.py . | 13.3kB
RUN /bin/sh -c pip install --no-cache-dir -r... | 25.1MB <--[LARGEST LAYER]
WORKDIR /app | 0B
CMD ["python3"] | 0B
```

### 2.2 Container inspection

Final service IP addresses after the optimized stack was recreated:
```bash
docker inspect app-events-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
docker inspect app-gateway-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
docker inspect app-payments-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
```

```text
/app-events-1 172.22.0.5
/app-gateway-1 172.22.0.6
/app-payments-1 172.22.0.3
```

Payments environment variables:
```bash
docker inspect app-payments-1 --format '{{range .Config.Env}}{{println .}}{{end}}'
```

```text
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PYTHON_VERSION=3.13.15
```

### 2.3 Live debugging with exec

Before optimization, the gateway ran as root:
```bash
docker exec app-gateway-1 whoami
docker exec app-gateway-1 id
```
```text
root
uid=0(root) gid=0(root) groups=0(root)
```

The container used Docker's embedded DNS resolver:

```bash
docker exec app-gateway-1 cat /etc/resolv.conf
```

```text
nameserver 127.0.0.11
options ndots:0
```

After optimization, all three application containers ran as the non-root `app` user:

```bash
docker exec app-gateway-1 whoami
docker exec app-events-1 whoami
docker exec app-payments-1 whoami
```

```text
app
app
app
```

The gateway reached both dependencies by Compose service name:
```bash
docker exec app-gateway-1 python3 -c "import urllib.request; print(urllib.request.urlopen('http://events:8081/health').read().decode())"
docker exec app-gateway-1 python3 -c "import urllib.request; print(urllib.request.urlopen('http://payments:8082/health').read().decode())"
```
```text
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

### 2.4 Logs and request flow

The gateway and events logs showed the same reservation request crossing the service boundary:
```bash
docker compose logs gateway --tail=20
docker compose logs events --tail=20
curl -s http://localhost:3080/events > /dev/null
curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity":1}'
docker compose logs gateway --tail=5
docker compose logs events --tail=5
```

```text
gateway-1 | {"time":"2026-09-13 17:43:19,935","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events HTTP/1.1 200 OK"}
gateway-1 | {"time":"2026-09-13 17:43:19,975","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve HTTP/1.1 200 OK"}
events-1  | {"time":"2026-09-13 17:43:19,972","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 49913772-da97-44e1-81f1-ce2cc5d6b21b"}
events-1  | INFO:     172.18.0.4:50344 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

The timestamped gateway and events entries confirm that the gateway forwards the request to `events` using the service name. The application logs use separate clocks/formatting from the Docker log timestamp, so the sub-millisecond ordering should be treated as approximate.

### 2.5 Network inspection

```bash
docker network ls | grep app
docker network inspect app_default --format '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}'
```
```text
f5aa0d923b49   app_default                      bridge    local
app-postgres-1: 172.22.0.4/16
app-redis-1: 172.22.0.2/16
app-gateway-1: 172.22.0.6/16
app-payments-1: 172.22.0.3/16
app-events-1: 172.22.0.5/16
```

The gateway finds `events` through Docker Compose's embedded DNS. `EVENTS_URL` is configured as `http://events:8081`, so Docker resolves the name `events` to the current container IP, `172.22.0.5` in the final inspection. The IP is dynamic and can change when the container or network is recreated; the service name remains the stable dependency address.

## Task 2: Dockerfile Optimization

### 2.7 `.dockerignore`

The same file was added to each application build context:

```bash
cat app/gateway/.dockerignore
cat app/events/.dockerignore
cat app/payments/.dockerignore
```

```text
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

The image sizes were unchanged at the displayed precision because each build context contained only a small set of application files:

```bash
docker compose build --no-cache
docker images | grep app
```

| Image | Before | After |
|---|---:|---:|
| app-gateway | 151 MB | 151 MB |
| app-events | 165 MB | 165 MB |
| app-payments | 150 MB | 150 MB |

### 2.8 Non-root users

The following lines were added before each Dockerfile's `CMD`:

```dockerfile
RUN addgroup --system app && adduser --system --ingroup app app
USER app
```

The optimized images were rebuilt with `docker compose build --no-cache` and started successfully. `docker exec` reported `app` for gateway, events, and payments,

```bash
docker compose up -d --build
docker exec app-gateway-1 whoami
curl -s http://localhost:3080/health
```
and the health endpoint returned:

```json
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

Dockerfile diff:

```diff
git diff -- app/gateway/Dockerfile app/events/Dockerfile app/payments/Dockerfile

diff --git a/app/events/Dockerfile b/app/events/Dockerfile
@@
 COPY main.py .
 
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 
 EXPOSE 8081
diff --git a/app/gateway/Dockerfile b/app/gateway/Dockerfile
@@
 COPY main.py .
 
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 
 EXPOSE 8080
diff --git a/app/payments/Dockerfile b/app/payments/Dockerfile
@@
 COPY main.py .
 
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 
 EXPOSE 8082
```

## Bonus Task: Trace a Request Across Services

The complete purchase used reservation ID `982e122e-89fb-4f9e-bff7-23e0f9510c66`:

```bash
RES=$(curl -s -X POST http://localhost:3080/events/1/reserve \
	-H "Content-Type: application/json" -d '{"quantity":1}')
RES_ID=$(echo "$RES" | python3 -c "import sys,json; print(json.load(sys.stdin)['reservation_id'])")
curl -s -X POST "http://localhost:3080/reserve/$RES_ID/pay"
```

```text
RESERVE {"reservation_id":"982e122e-89fb-4f9e-bff7-23e0f9510c66","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
PAY {"order_id":"982e122e-89fb-4f9e-bff7-23e0f9510c66","event_id":1,"quantity":1,"total_cents":5000,"status":"confirmed"}
```

Timestamped trace:

```bash
docker compose logs --timestamps gateway events payments
```

```text
events-1  | 2026-09-13T17:47:53.924839878Z {"time":"2026-09-13 17:47:53,921","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 982e122e-89fb-4f9e-bff7-23e0f9510c66"}
events-1  | 2026-09-13T17:47:53.926569244Z INFO:     172.22.0.6:40482 - "POST /events/1/reserve HTTP/1.1" 200 OK
gateway-1 | 2026-09-13T17:47:53.927811832Z {"time":"2026-09-13 17:47:53,927","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve \"HTTP/1.1 200 OK\""}
gateway-1 | 2026-09-13T17:47:53.930304659Z INFO:     172.22.0.1:39622 - "POST /events/1/reserve HTTP/1.1" 200 OK
payments-1 | 2026-09-13T17:47:53.975202109Z {"time":"2026-09-13 17:47:53,974","level":"INFO","service":"payments","msg":"Payment success: PAY-5B635909 for 982e122e-89fb-4f9e-bff7-23e0f9510c66"}
payments-1 | 2026-09-13T17:47:53.976528639Z INFO:     172.22.0.6:56252 - "POST /charge HTTP/1.1" 200 OK
gateway-1 | 2026-09-13T17:47:53.978875857Z {"time":"2026-09-13 17:47:53,978","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 200 OK\""}
events-1  | 2026-09-13T17:47:53.993909444Z {"time":"2026-09-13 17:47:53,993","level":"INFO","service":"events","msg":"Order confirmed: 982e122e-89fb-4f9e-bff7-23e0f9510c66"}
events-1  | 2026-09-13T17:47:53.996037766Z INFO:     172.22.0.6:40482 - "POST /reservations/982e122e-89fb-4f9e-bff7-23e0f9510c66/confirm HTTP/1.1" 200 OK
gateway-1 | 2026-09-13T17:47:53.998892086Z {"time":"2026-09-13 17:47:53,997","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/reservations/982e122e-89fb-4f9e-bff7-23e0f9510c66/confirm \"HTTP/1.1 200 OK\""}
gateway-1 | 2026-09-13T17:47:54.000618077Z INFO:     172.22.0.1:39634 - "POST /reserve/982e122e-89fb-4f9e-bff7-23e0f9510c66/pay HTTP/1.1" 200 OK
```

Annotations:

1. The first two events lines create the temporary reservation in Redis.
2. Gateway receives the reservation response, then calls payments to charge it.
3. Payments returns a payment reference, `PAY-5B635909`.
4. Gateway calls events `/confirm`, which writes the order to PostgreSQL and removes the temporary reservation.
5. Gateway returns the confirmed order to the client.

Using the gateway access-log timestamps, the end-to-end payment request took approximately 70.3 ms: from `17:47:53.930304659` to `17:47:54.000618077`. The payment hop took approximately 23.3 ms from the payment service's request log to its response log, and the confirmation completed within the same overall request.
