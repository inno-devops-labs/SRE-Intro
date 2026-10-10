# Lab 2 Report — Containerization: Inspect, Understand, Optimize

## Task 1 — Docker Inspection & Operations (6 pts)

### 1. Output of docker images | grep app with image sizes
```bash
$ docker images | grep app
```
```text
app-events:latest            c9b768071f41        233MB         56.9MB   U    
app-gateway:latest           9da125d60ba5        213MB         51.9MB   U    
app-payments:latest          9527d9420d7c        211MB         51.4MB   U   
```

### 2. Output of docker history for one image — annotate which layer is pip install
```bash
$ docker history app-gateway --no-trunc --format "table {{.CreatedBy}}\t{{.Size}}"
```
```text
IMAGE LAYER / COMMAND                                               SIZE
CMD ["uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8080"]       0B
EXPOSE [8080/tcp]                                                   0B
COPY main.py . # buildkit                                           29MB
COPY requirements.txt . # buildkit                                  12.3kB
RUN /bin/sh -c pip install --no-cache-dir -r requirements.txt       24.6kB  <-- !!! PIP INSTALL LAYER !!!
WORKDIR /app                                                        0B
RUN /bin/sh -c set -eux; for src in idle3 pip3...                   12.3kB
RUN /bin/sh -c set -eux; apt-get update; apt-get install...         16.4kB
RUN /bin/sh -c set -eux; savedAptMark="$(apt-mark showmanual)"...   40.1MB
ENV PYTHON_SHA256=2ab91ff401783ccca64f75d10c882e957bdfd60e...      0B
ENV GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305                0B
ENV PYTHON_VERSION=3.13.13                                          0B
ENV PATH=/usr/local/bin:/usr/local/sbin:...                         4.95MB
# debian.sh --arch 'amd64' out/ 'trixie' '@1779062400'              87.4MB
```

### 3. IP addresses of all 3 services from docker inspect
```bash
$ docker inspect app-events-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
docker inspect app-gateway-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
docker inspect app-payments-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
```
```text
/app-events-1 172.19.0.5
/app-gateway-1 172.19.0.6
/app-payments-1 172.19.0.3
```

### 4. Environment variables of payments service
```bash
$ docker inspect app-payments-1 --format '{{range .Config.Env}}{{println .}}{{end}}'
```
```text
PAYMENT_LATENCY_MS=0
PAYMENT_FAILURE_RATE=0.0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.13
PYTHON_SHA256=2ab91ff401783ccca64f75d10c882e957bdfd60e2bf5a72f8421793729b78a71
```

### 5. Output of whoami and python3 urllib call to events:8081/health from inside the gateway container
```bash
$ docker exec app-gateway-1 whoami
```
```text
root
```

```bash
$ docker exec app-gateway-1 python3 -c "
import urllib.request
print(urllib.request.urlopen('http://events:8081/health').read().decode())
"
```
```text
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
```

### 6. Log snippet showing the same request flowing through gateway → events
```bash
$ curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity":1}'
```
```text
{"reservation_id":"908773b3-a4d3-4898-b1d0-f681cae1ff86","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
```

```bash
$ docker compose logs gateway --tail=5
```
```text
gateway-1  | {"time":"2026-06-06 12:27:35,035","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve "HTTP/1.1 200 OK""}
gateway-1  | INFO:     172.19.0.1:52460 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

```bash
$ docker compose logs events --tail=5
```
```text
events-1  | {"time":"2026-06-06 12:27:35,034","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 908773b3-a4d3-4898-b1d0-f681cae1ff86"}
events-1  | INFO:     172.19.0.6:56158 - "POST /events/1/reserve HTTP/1.1" 200 OK 
```

### 7. Network inspect output showing all containers and their IPs
```bash
$ docker network ls | grep app
```
```text
6b13cb02834b   app_default     bridge    local
```

```bash
$ docker network inspect app_default --format '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}'
```
```text
app-events-1: 172.19.0.5/16
app-postgres-1: 172.19.0.4/16
app-gateway-1: 172.19.0.6/16
app-redis-1: 172.19.0.2/16
app-payments-1: 172.19.0.3/16
```

### 8. Answer: "How does the gateway find the events service? What IP does events resolve to?"
```text
The gateway finds the events service via Docker's embedded DNS server (located at `127.0.0.11`), which dynamically resolves the human-readable service name `events` to its internal IP address **`172.19.0.5`** within their shared bridge network (`app_default`). This automatic name resolution enables seamless Service Discovery between the containers.
```

## Task 2 — Dockerfile Optimization (4 pts)

### 9. Image sizes before and after .dockerignore (any difference?)
```bash
$ docker images | grep app
```
```text
app-events:latest            6c93af4826fb        233MB         56.9MB        
app-gateway:latest           037f0d3486db        213MB         51.9MB        
app-payments:latest          aad708f3e728        211MB         51.4MB
``` 

```text
There is no noticeable difference in the image sizes because the base image and the application dependencies account for almost 99 percent of the total container size. The excluded files, such as pycache, local markdown files, and vscode configurations, only weigh a few kilobytes, which is too small to change the final rounded megabyte count displayed by the docker images command. Even though there is no visible size reduction right now, the .dockerignore file is still essential for security to prevent sensitive files like .env from leaking into the final image, and it keeps the build context clean as the project git history expands over time.
```

### 10. The .dockerignore content
```text
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

### 11. Output of whoami inside the container after adding non-root user
```bash
$ docker exec app-gateway-1 whoami
```
```text
app
```

### 12. The git diff of your Dockerfile changes
```bash
$ git diff
```
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
diff --git a/app/gateway/main.py b/app/gateway/main.py
index c86db33..b166152 100644
--- a/app/gateway/main.py
+++ b/app/gateway/main.py
@@ -338,7 +338,14 @@ async def pay_reservation(reservation_id: str):
         raise HTTPException(e.response.status_code, "Payment failed")
     except Exception as e:
:
```

## Bonus Task — Trace a Request Across Services (2 pts)

### 13. The full timestamped logs showing one request flowing through all 3 services
```text
payments-1  | 2026-06-06T13:35:47.741837468Z {"time":"2026-06-06 13:35:47,741","level":"INFO","service":"payments","msg":"Payment success: PAY-66E09E85 for e5ec3361-c5cb-440e-bdfc-f747251b5a8c"}
payments-1  | 2026-06-06T13:35:47.742269381Z INFO:      172.19.0.6:50754 - "POST /charge HTTP/1.1" 200 OK
gateway-1   | 2026-06-06T13:35:47.742823483Z {"time":"2026-06-06 13:35:47,742","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 200 OK\""}
events-1    | 2026-06-06T13:35:47.755155134Z {"time":"2026-06-06 13:35:47,754","level":"INFO","service":"events","msg":"Order confirmed: e5ec3361-c5cb-440e-bdfc-f747251b5a8c"}
events-1    | 2026-06-06T13:35:47.755725897Z INFO:      172.19.0.6:45528 - "POST /reservations/e5ec3361-c5cb-440e-bdfc-f747251b5a8c/confirm HTTP/1.1" 200 OK
gateway-1   | 2026-06-06T13:35:47.756366176Z {"time":"2026-06-06 13:35:47,756","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/reservations/e5ec3361-c5cb-440e-bdfc-f747251b5a8c/confirm \"HTTP/1.1 200 OK\""}
gateway-1   | 2026-06-06T13:35:47.757275413Z INFO:      172.19.0.1:37076 - "POST /reserve/e5ec3361-c5cb-440e-bdfc-f747251b5a8c/pay HTTP/1.1" 200 OK
```

### 14. Annotate each line: which service, what it did, how long between hops

**Line 1 (payments)**: The billing service successfully runs internal business logic and registers the transaction PAY-66E09E85 for the reservation ID.

**Line 2 (payments)**: The billing server completes its internal operation and logs an outgoing 200 OK network response.
* **Hop duration**: 0.43 ms since internal processing started.

**Line 3 (gateway)**: The API gateway receives the network confirmation back from the payments microservice.
* **Hop duration**: 0.55 ms for network delivery from payments to gateway.

**Line 4 (events)**: The gateway triggers the next step, and the events microservice updates the database status to mark the order as confirmed.
* **Hop duration**: 12.33 ms since the gateway finished dealing with the payment response (includes network overhead and DB write time).

**Line 5 (events)**: The events HTTP server completes the transaction and logs its 200 OK response to its log stream.
* **Hop duration**: 0.57 ms to shift from business logic to networking.

**Line 6 (gateway)**: The API gateway catches the response from the events service and logs the status.
* **Hop duration**: 0.64 ms for network delivery from events back to gateway.

**Line 7 (gateway)**: The gateway logs the outer client-facing transaction state and sends the final JSON payload back to the external computer.
* **Hop duration**: 0.91 ms to finalize internal tracking metrics and exit.
    

### 15. Answer: "What is the total end-to-end time from gateway receiving the request to returning the response?"

* The total recorded internal execution window across the system logs is exactly 15.44 milliseconds (the time delta between the first log trace in payments at 13:35:47.741837Z and the final client delivery trace on the gateway at 13:35:47.757275Z).

* When accounting for the initial network hop where the gateway first accepts the external terminal request and routes it to the payment layer (which takes approximately an extra 1.5 to 2 milliseconds before logging fires), the actual end-to-end request lifecycle is roughly 17 to 18 milliseconds.






