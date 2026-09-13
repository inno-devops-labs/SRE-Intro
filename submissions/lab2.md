# Lab 2 — Containerization: Inspect, Understand, Optimize

## Task 1 — Docker Inspection & Operations

### 1. Image inspection

The application images were inspected using:

```bash
docker images | grep app
```

Output:

```text
app-events:latest    47041604bdfb   272MB   61MB
app-gateway:latest   cf5fa311dd4c    251MB   55.7MB
app-payments:latest  83479c6b1b71     249MB   55.2MB
```

`app-events` is the largest image at 272 MB.

To inspect the image layers:

```bash
docker history app-gateway --no-trunc --format "table {{.CreatedBy}}\t{{.Size}}"
```

The gateway image has **14 layers**.

The largest layer is the Debian base layer:

```text
# debian.sh --arch 'arm64' out/ 'trixie' '@1787529600'    109MB
```

The next significant layers are the Python runtime layer at 43.7 MB and the `pip install` layer at 29.1 MB. The `pip install` layer contains the installed Python dependencies.

The number of layers was verified with:

```bash
docker history app-gateway --no-trunc --format '{{.CreatedBy}}' | wc -l
```

### 2. Container inspection

The IP addresses of the application containers were inspected using:

```bash
docker inspect app-events-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'

docker inspect app-gateway-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'

docker inspect app-payments-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
```

Output:

```text
/app-events-1 172.25.0.5
/app-gateway-1 172.25.0.6
/app-payments-1 172.25.0.3
```

The environment variables of the payments container were inspected using:

```bash
docker inspect app-payments-1 --format '{{range .Config.Env}}{{println .}}{{end}}'
```

### 3. Container execution and connectivity

The user and working directory inside the gateway container were checked using:

```bash
docker compose exec gateway whoami
```

Output:

```text
root
```

The working directory was checked using:

```bash
docker compose exec gateway pwd
```

Output:

```text
/app
```

The contents of the application directory were inspected with:

```bash
docker compose exec gateway ls -la /app
```

The application directory contains `main.py`, `requirements.txt` and `__pycache__`.

The container user information was also inspected using:

```bash
docker exec app-gateway-1 id
```

Output:

```text
uid=0(root) gid=0(root) groups=0(root)
```

The container DNS configuration was inspected using:

```bash
docker exec app-gateway-1 cat /etc/resolv.conf
```

Docker Compose service discovery was verified with:

```bash
docker compose exec gateway getent hosts payments events redis postgres
```

Output:

```text
172.25.0.3 payments
172.25.0.5 events
172.25.0.2 redis
172.25.0.4 postgres
```

The gateway finds the `events` service using Docker Compose's internal DNS. The hostname `events` resolves to `172.25.0.5` on the `app_default` network.

The gateway can communicate with the payments service:

```bash
docker compose exec gateway python -c "import urllib.request; print(urllib.request.urlopen('http://payments:8082/health').read().decode())"
```

Output:

```text
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

The gateway can also communicate with the events service:

```bash
docker compose exec gateway python -c "import urllib.request; print(urllib.request.urlopen('http://events:8081/health').read().decode())"
```

Output:

```text
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
```

Therefore, the gateway can reach both application services through Docker's internal network and service-name-based DNS resolution.

### 4. Logs and traffic

The recent logs of the three application services were inspected with:

```bash
docker compose logs gateway --tail=20

docker compose logs events --tail=20

docker compose logs payments --tail=20
```

The gateway successfully communicated with the other services. For example:

```text
payments-1 | INFO: 172.25.0.6:39088 - "GET /health HTTP/1.1" 200 OK

events-1   | INFO: 172.25.0.6:37698 - "GET /health HTTP/1.1" 200 OK
```

Traffic was generated with:

```bash
curl -s http://localhost:3080/events > /dev/null

curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity":1}'
```

The generated traffic was visible in the gateway and service logs.

### 5. Docker network

The Docker network was inspected using:

```bash
docker network ls | grep app
```

Output:

```text
8b6ac8f2a32e app_default bridge local
```

The connected containers were inspected using:

```bash
docker network inspect app_default --format '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}'
```

Output:

```text
app-redis-1: 172.25.0.2/16
app-payments-1: 172.25.0.3/16
app-postgres-1: 172.25.0.4/16
app-events-1: 172.25.0.5/16
app-gateway-1: 172.25.0.6/16
```

Docker Compose provides internal DNS, allowing containers to communicate using service names instead of fixed IP addresses.

For example:

```text
events → 172.25.0.5
payments → 172.25.0.3
redis → 172.25.0.2
postgres → 172.25.0.4
```

The IP addresses are dynamically assigned to containers on the Docker bridge network, while service names remain stable for inter-service communication.

### 6. Request flow

A request was generated with:

```bash
curl -s http://localhost:3080/events > /dev/null
```

Gateway log:

```text
gateway-1 | {"time":"2026-09-13 16:33:15,327","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events \"HTTP/1.1 200 OK\""}
gateway-1 | INFO: 172.25.0.1:65318 - "GET /events HTTP/1.1" 200 OK
```

Events log:

```text
events-1 | INFO: 172.25.0.6:48618 - "GET /events HTTP/1.1" 200 OK
```

The request flow is:

```text
Client
  ↓
gateway:8080
  ↓
events:8081
  ↓
PostgreSQL / Redis
```

The gateway forwards the request to the events service using the Docker service name `events`. The events service handles the request and uses PostgreSQL and Redis as backend dependencies.

The matching gateway and events logs confirm that the request crossed the Docker network from the gateway container to the events container.

---

## Task 2 — Dockerfile Optimization

### 1. `.dockerignore`

A `.dockerignore` file was added to the `gateway`, `events` and `payments` build contexts:

```text
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

These files are not required to run the application and therefore do not need to be included in the Docker build context.

### 2. Image rebuild and size comparison

The original image sizes were:

```text
app-events:latest    272MB
app-gateway:latest   251MB
app-payments:latest  249MB
```

After adding `.dockerignore`, the images were rebuilt from scratch:

```bash
docker compose build --no-cache
```

The resulting images were inspected with:

```bash
docker images | grep app
```

Output:

```text
app-events:latest    3abcfdf8b97a    272MB    61MB   U
app-gateway:latest   2576b4c2ddde    251MB   55.7MB   U
app-payments:latest  441342304552    249MB   55.2MB   U
```

Image size comparison:

| Image        | Before |  After |
| ------------ | -----: | -----: |
| app-events   | 272 MB | 272 MB |
| app-gateway  | 251 MB | 251 MB |
| app-payments | 249 MB | 249 MB |

The final image sizes did not change. This is expected because `.dockerignore` affects the Docker build context, while the final image size is dominated by the Debian/Python base image and installed dependencies. The Dockerfiles only copy `requirements.txt` and `main.py`, so the excluded files do not contribute significantly to the resulting image layers.

### 3. Non-root user

A dedicated non-root user was added to all three Dockerfiles:

```dockerfile
RUN addgroup --system app && adduser --system --ingroup app app

USER app
```

The containers were rebuilt and restarted:

```bash
docker compose build --no-cache
docker compose up -d
```

The gateway container user was verified with:

```bash
docker exec app-gateway-1 whoami
```

Output:

```text
app
```

The containers now run the application as a non-root user instead of `root`, which reduces the privileges available to the application process inside the container.

The Dockerfile changes were reviewed using:

```bash
git diff -- gateway/Dockerfile events/Dockerfile payments/Dockerfile
```

```
diff --git a/app/events/Dockerfile b/app/events/Dockerfile
index c45a68c..5da5370 100644
--- a/app/events/Dockerfile
+++ b/app/events/Dockerfile
@@ -5,5 +5,8 @@ COPY requirements.txt .
 RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .
 
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
+
 EXPOSE 8081
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]
diff --git a/app/gateway/Dockerfile b/app/gateway/Dockerfile
index 68ef075..bff1a79 100644
--- a/app/gateway/Dockerfile
+++ b/app/gateway/Dockerfile
@@ -5,5 +5,8 @@ COPY requirements.txt .
 RUN pip install --no-cache-dir -r requirements.txt
```

The relevant changes include the addition of the non-root `app` user and the corresponding `USER app` instruction.

---

## Bonus — Trace a Request Across Services

A complete purchase flow was executed with:

```bash
RES=$(curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity":1}')

RES_ID=$(echo "$RES" | python3 -c "import sys,json; print(json.load(sys.stdin)['reservation_id'])")

echo "Reservation ID: $RES_ID"

curl -s -X POST "http://localhost:3080/reserve/$RES_ID/pay"
```

The reservation ID was:

```text
556d3f26-cdae-45cc-b08c-c54f8a0a0fdf
```

The final client response was:

```json
{
  "order_id":"556d3f26-cdae-45cc-b08c-c54f8a0a0fdf",
  "event_id":1,
  "quantity":1,
  "total_cents":5000,
  "status":"confirmed"
}
```

The request was traced through the following services:

```text
gateway
  ↓
events
  ↓
gateway
  ↓
payments
  ↓
gateway
  ↓
events
```

### Timestamped trace

Reservation creation:

```text
events-1 | 2026-09-13T16:43:19.785192428Z {"time":"2026-09-13 16:43:19,785","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 556d3f26-cdae-45cc-b08c-c54f8a0a0fdf"}
```

Gateway received the successful reservation response:

```text
gateway-1 | 2026-09-13T16:43:19.786122845Z {"time":"2026-09-13 16:43:19,785","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve \"HTTP/1.1 200 OK\""}
```

Payment processing:

```text
payments-1 | 2026-09-13T16:43:43.295506675Z {"time":"2026-09-13 16:43:43,295","level":"INFO","service":"payments","msg":"Payment success: PAY-D33A4F79 for 556d3f26-cdae-45cc-b08c-c54f8a0a0fdf"}

payments-1 | 2026-09-13T16:43:43.296132800Z INFO: 172.25.0.6:54406 - "POST /charge HTTP/1.1" 200 OK

gateway-1 | 2026-09-13T16:43:43.296957050Z {"time":"2026-09-13 16:43:43,296","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 200 OK\""}
```

Confirmation:

```text
events-1 | 2026-09-13T16:43:43.305296758Z {"time":"2026-09-13 16:43:43,305","level":"INFO","service":"events","msg":"Order confirmed: 556d3f26-cdae-45cc-b08c-c54f8a0a0fdf"}

gateway-1 | 2026-09-13T16:43:43.305912758Z {"time":"2026-09-13 16:43:43,305","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/reservations/556d3f26-cdae-45cc-b08c-c54f8a0a0fdf/confirm \"HTTP/1.1 200 OK\""}

gateway-1 | 2026-09-13T16:43:43.306547966Z INFO: 192.168.65.1:23223 - "POST /reserve/556d3f26-cdae-45cc-b08c-c54f8a0a0fdf/pay HTTP/1.1" 200 OK
```

### Trace analysis

1. `events` created the reservation for event `1` at `16:43:19.785192428`.
2. `gateway` received the successful reservation response at `16:43:19.786122845`.
3. `payments` successfully processed the payment at `16:43:43.295506675`.
4. `gateway` received the successful payment response at `16:43:43.296957050`.
5. `events` confirmed the order at `16:43:43.305296758`.
6. `gateway` received the successful confirmation response at `16:43:43.305912758`.
7. `gateway` returned `200 OK` for the complete payment request at `16:43:43.306547966`.

### Timing

Observed timings between the relevant service log entries are:

* `events reservation → gateway response`: approximately `0.930 ms`
* `payments success → payments HTTP 200`: approximately `0.626 ms`
* `payments HTTP 200 → gateway response`: approximately `0.824 ms`
* `events confirmation → gateway response`: approximately `0.616 ms`
* gateway's final observable payment-flow segment: approximately `9.591 ms`

There is approximately `23.51 seconds` between the reservation and payment operations. This interval is not service latency: the reservation and payment were issued as two separate client requests, so it includes the time between those requests.

The provided access logs do not contain a timestamp for the exact moment when the gateway initially accepted the `POST /reserve/{reservation_id}/pay` request. Therefore, the exact end-to-end time from request arrival at the gateway to the final response cannot be measured directly from the available log lines.

The observable completion time of the payment flow in the gateway logs is approximately `9.6 ms`.

### Conclusion

The complete purchase request was successfully traced across all required services:

```text
gateway → events → gateway → payments → gateway → events
```

The same reservation ID was preserved across the services, and the final result was:

```text
status = confirmed
```

This demonstrates successful service-to-service communication through Docker's internal network and DNS-based service discovery.
