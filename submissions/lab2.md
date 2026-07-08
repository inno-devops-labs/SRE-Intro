# Task 1

#### **1. docker images | grep app**

app-events:latest     570e507ec599        233MB         56.9MB   U    
app-gateway:latest    bcb72f78586d        213MB         51.9MB   U    
app-payments:latest   f7734d21ffaf        211MB         51.4MB   U   

#### **2. docker history app-events --no-trunc --format "table {{.CreatedBy}}\t{{.Size}}"**

CREATED BY                                      SIZE
CMD ["uvicorn" "main:app" "--host" "0.0.0.0"…   0B
EXPOSE [8081/tcp]                               0B
COPY main.py . # buildkit                       20.5kB
RUN /bin/sh -c pip install --no-cache-dir -r…   43.3MB
COPY requirements.txt . # buildkit              12.3kB
WORKDIR /app                                    8.19kB
CMD ["python3"]                                 0B
RUN /bin/sh -c set -eux;  for src in idle3 p…   16.4kB
RUN /bin/sh -c set -eux;   savedAptMark="$(a…   40.1MB
ENV PYTHON_SHA256=2ab91ff401783ccca64f75d10c…   0B
ENV PYTHON_VERSION=3.13.13                      0B
ENV GPG_KEY=7169605F62C751356D054A26A821E680…   0B
RUN /bin/sh -c set -eux;  apt-get update;  a…   4.95MB
ENV PATH=/usr/local/bin:/usr/local/sbin:/usr…   0B
\# debian.sh --arch 'amd64' out/ 'trixie' '@1…   87.4MB

*This gateway image has 15 layers.
The largest layer is `\# debian.sh --arch 'amd64' out/ 'trixie' '@1…   87.4MB`, because it is the basic layer of OS Debian Linux on which whole image constructed. And `RUN /bin/sh -c pip install --no-cache-dir -r…   43.3MB` is the largest layer between **own** app instructions, because on this layer Python downloads, unpacks and compile requirements.*

**RUN /bin/sh -c pip install --no-cache-dir -r…   43.3MB** - is pip install layer.


#### **3. IP addresses of all 3 services from `docker inspect`** 

**docker inspect app-events-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'**
`/app-events-1 172.18.0.5

**docker inspect app-gateway-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'**
`/app-gateway-1 172.18.0.6

**docker inspect app-payments-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'**
`/app-payments-1 172.18.0.2


#### **4 Environment variables of payments service**

**docker inspect app-payments-1 --format '{{range .Config.Env}}{{println .}}{{end}}'**
`PAYMENT_FAILURE_RATE=0.0
`PAYMENT_LATENCY_MS=0
`PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
`GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
`PYTHON_VERSION=3.13.13
`PYTHON_SHA256=2ab91ff401783ccca64f75d10c882e957bdfd60e2bf5a72f8421793729b78a71

#### **5. 

**Output of whoami inside gateway:**
`root`

**Output of python3 urllib call to events:8081/health:**
`{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}`

#### **6. Log snippet showing the same request flowing through gateway → events**
`events-1 | {"time":"2026-06-10 13:59:14,755","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 3b64b417-f8c6-4ca7-8773-1fe1478ae8d0"} 
`events-1 | INFO: 172.18.0.6:56588 - "POST /events/1/reserve HTTP/1.1" 200 OK 

`gateway-1 | {"time":"2026-06-10 13:59:14,757","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve \"HTTP/1.1 200 OK\""} 
`gateway-1 | INFO: 172.18.0.1:52882 - "POST /events/1/reserve HTTP/1.1" 200 OK

***Can you follow a single request across multiple services by matching the timestamps?*:**
Yes. The POST /events/1/reserve request example shows that the internal events service recorded the ticket reservation at 13:59:14,755, and the gateway registered a successful response from it at 13:59:14,757. The time difference is only 2 milliseconds, allowing us to combine these logs into a single end-to-end request.

#### **7. Network inspect output showing all containers and their IPs**

`app-redis-1: 172.18.0.3/16
`app-events-1: 172.18.0.5/16
`app-payments-1: 172.18.0.2/16
`app-postgres-1: 172.18.0.4/16
`app-gateway-1: 172.18.0.6/16

#### **8. Answer: "How does the gateway find the events service? What IP does `events` resolve to?"**

The `gateway` service finds the `events` service using **Docker's embedded DNS server**, which automatically resolves container/service names within the same user-defined network (`app_default`).
- Inside the gateway container, the network routing is configured via `/etc/resolv.conf` to use the Docker internal nameserver at **`127.0.0.11`**.
- When the gateway makes a request to `http://events:8081`, this internal DNS server intercepts the hostname `events` and maps it to the active container `app-events-1`.
- Based on the `docker inspect` and `docker network inspect` outputs, the `events` service hostname currently resolves to the IP address **`172.18.0.5`**.

# Task 2

#### **1. Image sizes before and after `.dockerignore` (any difference?)**
**Before:**
	app-events:latest     570e507ec599        233MB         56.9MB   U    
	app-gateway:latest    bcb72f78586d        213MB         51.9MB   U    
	app-payments:latest   f7734d21ffaf        211MB         51.4MB   U  
**After:**
	app-events:latest     b70e0b5640c9        233MB         56.9MB   U    
	app-gateway:latest    2820975cddda        213MB         51.9MB   U    
	app-payments:latest   d6b485c51639        211MB         51.4MB   U 

*The final image sizes did not change because the Dockerfiles explicitly target specific files (`COPY main.py .` and `COPY requirements.txt .`) instead of using a blanket `COPY . .`. Consequently, heavy development files like `.git` or `.venv` never entered the image layers in the first place, meaning the `.dockerignore` file itself—and the paths listed inside it—never enter the final image. 
#### **2. The `.dockerignore` content**

`app/events/.dockerignore`, `app/gateway/.dockerignore`, and `app/payments/.dockerignore` have the same content:

\__pycache__
\*.pyc
\.git
\.env
\*.md
\.vscode

#### **3. Output of `whoami` inside the container after adding non-root user**
`app`

#### **4. The `git diff` of your Dockerfile changes**

##### **git diff events/Dockerfile:**
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

##### **git diff gateway/Dockerfile:**
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

##### **git diff payments/Dockerfile:**
diff --git a/app/payments/Dockerfile b/app/payments/Dockerfile
index 7f9e7c1..8cf997d 100644
--- a/app/payments/Dockerfile
+++ b/app/payments/Dockerfile
@@ -6,4 +6,6 @@ RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .
 
 EXPOSE 8082
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8082"]

# Bonus Task

#### **1. The full timestamped logs showing one request flowing through all 3 services**
- 2026-06-10T16:57:14.179961803Z events-1 | {"time":"2026-06-10 16:57:14,179","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: ef5d8c0a-a756-4e71-aee1-b44340331708"} 
- 2026-06-10T16:57:14.181780045Z events-1 | INFO: 172.18.0.6:44388 - "POST /events/1/reserve HTTP/1.1" 200 OK 
- 2026-06-10T16:57:14.183665603Z gateway-1 | {"time":"2026-06-10 16:57:14,183","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve \"HTTP/1.1 200 OK\""} 
- 2026-06-10T16:57:14.185244787Z gateway-1 | INFO: 172.18.0.1:60514 - "POST /events/1/reserve HTTP/1.1" 200 OK
- 2026-06-10T16:57:38.521698265Z payments-1 | {"time":"2026-06-10 16:57:38,521","level":"INFO","service":"payments","msg":"Payment success: PAY-6E4D5D10 for ef5d8c0a-a756-4e71-aee1-b44340331708"} 
- 2026-06-10T16:57:38.522862527Z payments-1 | INFO: 172.18.0.6:49914 - "POST /charge HTTP/1.1" 200 OK 
- 2026-06-10T16:57:38.524219127Z gateway-1 | {"time":"2026-06-10 16:57:38,523","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 200 OK\""} 
- 2026-06-10T16:57:38.542748100Z events-1 | {"time":"2026-06-10 16:57:38,541","level":"INFO","service":"events","msg":"Order confirmed: ef5d8c0a-a756-4e71-aee1-b44340331708"} 
- 2026-06-10T16:57:38.544562303Z events-1 | INFO: 172.18.0.6:56042 - "POST /reservations/ef5d8c0a-a756-4e71-aee1-b44340331708/confirm HTTP/1.1" 200 OK 
- 2026-06-10T16:57:38.545319893Z gateway-1 | {"time":"2026-06-10 16:57:38,544","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/reservations/ef5d8c0a-a756-4e71-aee1-b44340331708/confirm \"HTTP/1.1 200 OK\""} 
- 2026-06-10T16:57:38.548149341Z gateway-1 | INFO: 172.18.0.1:41212 - "POST /reserve/ef5d8c0a-a756-4e71-aee1-b44340331708/pay HTTP/1.1" 200 OK

#### **2. Annotate each line: which service, what it did, how long between hops**
- **Line 1 (`events-1`):** The application logic inside the `events` service completes the DB operations to reserve 1 ticket and generates the reservation ID. **Time: 16:57:14.179**.
- **Line 2 (`events-1`):** The Uvicorn web server of the `events` service logs an incoming HTTP `POST /events/1/reserve` request completion with status `200 OK`. **Delta: ~2ms** (Time since Line 1).
- **Line 3 (`gateway-1`):** The application layer of the `gateway` service logs that its internal HTTP client successfully received the `200 OK` response from the upstream `events` service. **Delta: ~2ms** (Time since Line 2).
- **Line 4 (`gateway-1`):** The Uvicorn server of the `gateway` service completes the cycle, returning `200 OK` back to the host machine client (`curl`). **Delta: ~1.5ms** (Time since Line 3).
- **Line 5 (`payments-1`):** The application layer of the `payments` service successfully processes the mock credit card charge task for the reservation. **Time: 16:57:38.521**. This opperation is triggered by user command(in terminal in my case), so - new time counter.
- **Line 6 (`payments-1`):** The Uvicorn server of the `payments` service registers the formal HTTP response completion and returns `200 OK` to the Gateway. **Delta: ~1.1ms** (Time since Line 5).
- **Line 7 (`gateway-1`):** The `gateway` service application layer records that the upstream payment transaction request has successfully finished processing. **Delta: ~1.4ms** (Time since Line 6).
- **Line 8 (`events-1`):** Upon receiving the order verification from Gateway, the `events` application layer updates the ticket record state to "confirmed" in PostgreSQL. **Delta: ~18.5ms** (Time since Line 7).
- **Line 9 (`events-1`):** The Uvicorn server of the `events` service logs the successful `200 OK` response transmission back to the Gateway. **Delta: ~1.8ms** (Time since Line 8).
- **Line 10 (`gateway-1`):** The `gateway` application client logs that the final ticket state confirmation step with the upstream `events` API was successful. **Delta: ~0.8ms** (Time since Line 9).
- **Line 11 (`gateway-1`):** The Uvicorn server of the `gateway` service delivers the final transaction confirmation JSON payload back to the external client terminal. **Delta: ~2.8ms** (Time since Line 10).

#### **3. Answer: "What is the total end-to-end time from gateway receiving the request to returning the response?**
The purchase transaction is split into two independent client-side synchronous HTTP roundtrips:
1. **Reservation Phase (Lines 1 to 4):**  = **~5.3 milliseconds**.
2. **Payment & Confirmation Phase (Lines 5 to 11):**  = **~26.5 milliseconds**.

**Total active infrastructure processing time:** **~31.8 milliseconds**.
