# Lab 2 — Containerization: Inspect, Understand, Optimize
**Student:** Valerii Tiniakov
**Group:** B24-SD-03

## Task 1 — Docker Inspection & Operations

### 2.1: Image inspection

**1. Output of `docker images | grep app` with image sizes:**
```text
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab2)
$ docker images | grep app
app-gateway                   latest      29d5bc419774   13 minutes ago      214MB
app-events                    latest      7814a2a89ded   About an hour ago   233MB
app-payments                  latest      2cc8a18f11fc   About an hour ago   212MB

```

**2. Output of `docker history` for the largest image:**
```text
CREATED BY                                                                     SIZE
CMD ["uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8080"]                  0B
EXPOSE map[8080/tcp:{}]                                                        0B
COPY main.py . # buildkit                                                      24.6kB
RUN /bin/sh -c pip install --no-cache-dir -r requirements.txt # buildkit       29.2MB  <--- pip install layer
COPY requirements.txt . # buildkit                                             12.3kB
WORKDIR /app                                                                   8.19kB
CMD ["python3"]                                                                0B
RUN /bin/sh -c set -eux;  for src in idle3...                                  16.4kB
RUN /bin/sh -c set -eux;  savedAptMark... (Python installation)                40.2MB
ENV PYTHON_SHA256=...                                                          0B
ENV PYTHON_VERSION=3.13.14                                                     0B
ENV GPG_KEY=...                                                                0B
RUN /bin/sh -c set -eux;  apt-get update...                                    4.94MB
ENV PATH=...                                                                   0B
# debian.sh --arch 'amd64' out/ 'trixie' '@1781049600'                         87.4MB
```

**Questions:**
* **How many layers does the gateway image have?**
  15
* **Which layer is the largest and why?**
  Excluding the base OS layer (Debian, 87.4MB) and Python installation (40.2MB), the largest application layer is RUN pip install (29.2MB). This is because it downloads and installs all external Python dependencies (like FastAPI, Uvicorn, httpx) into the image.

### 2.2: Container inspection

**3. IP addresses of all 3 services:**
```text
/app-events-1 172.18.0.5
/app-gateway-1 172.18.0.6
/app-payments-1 172.18.0.2
```

**4. Environment variables of payments service:**
```text
PAYMENT_LATENCY_MS=0
PAYMENT_FAILURE_RATE=0.0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.14
PYTHON_SHA256=639e43243c620a308f968213df9e00f2f8f62332f7adbaa7a7eeb9783057c690
```

### 2.3: Live debugging with exec

**5. Output of `whoami` and `python3 urllib` from inside the gateway container:**
* `whoami` output:
```text
root
```

* `python3 urllib` output for `events:8081/health`:
```text
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
```

* `python3 urllib` output for `payments:8082/health`:
```text
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

### 2.4: Logs analysis

**6. Log snippet showing the same request flowing through gateway → events:**
```text
events-1   | {"time":"2026-06-12 20:45:36,113","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: a4ae6cdb-cd69-40a8-a037-c383bc99208f"}
gateway-1  | {"time":"2026-06-12 20:45:36,114","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve "HTTP/1.1 200 OK""}
```

### 2.5 & 2.6: Network inspection & Service Discovery

**7. Network inspect output showing all containers and their IPs:**
```text
app-postgres-1: 172.18.0.4/16
app-redis-1: 172.18.0.3/16
app-payments-1: 172.18.0.2/16
app-events-1: 172.18.0.5/16
app-gateway-1: 172.18.0.6/16
```

**8. Questions about Service Discovery:**
* **How does the gateway find the events service?**
  The gateway uses Docker's embedded DNS server (located at `127.0.0.11`), which automatically resolves service names (like `events` or `payments`) to their internal IP addresses within the `app_default` bridge network.
* **What IP does `events` resolve to?**
  `172.18.0.5`

---

## Task 2 — Dockerfile Optimization

### 2.7 & 2.8: .dockerignore & Non-root User

**1. Image sizes before and after `.dockerignore`:**
* **Before:**
```text
app-gateway           latest      29d5bc419774   13 minutes ago      214MB
app-events            latest      7814a2a89ded   About an hour ago   233MB
app-payments          latest      2cc8a18f11fc   About an hour ago   212MB
```
* **After:**
```text
app-events            latest      94e701259486   7 seconds ago       233MB
app-gateway           latest      d829d2078e67   15 seconds ago      214MB
app-payments          latest      4a33cd4e46a4   18 seconds ago      212MB
```
* **Any difference?** There is no significant difference in size because the individual build contexts for each service (`app/gateway`, `app/events`, `app/payments`) did not contain large excluded files like `.git` to begin with. However, `.dockerignore` protects against future context bloat.

**2. The `.dockerignore` content:**
```text
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

**3. Output of `whoami` inside the container after adding non-root user:**
```text
app
```

**4. The `git diff` of your Dockerfile changes:**
```diff
diff --git a/app/events/Dockerfile b/app/events/Dockerfile
index ...
--- a/app/events/Dockerfile
+++ b/app/events/Dockerfile
@@ -6,4 +6,6 @@ RUN pip install --no-cache-dir -r requirements.txt
 
 COPY main.py .
 
 EXPOSE 8081
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]

diff --git a/app/gateway/Dockerfile b/app/gateway/Dockerfile
index ...
--- a/app/gateway/Dockerfile
+++ b/app/gateway/Dockerfile
@@ -6,4 +6,6 @@ RUN pip install --no-cache-dir -r requirements.txt
 
 COPY main.py .
 
 EXPOSE 8080
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

diff --git a/app/payments/Dockerfile b/app/payments/Dockerfile
index ...
--- a/app/payments/Dockerfile
+++ b/app/payments/Dockerfile
@@ -6,4 +6,6 @@ RUN pip install --no-cache-dir -r requirements.txt
 
 COPY main.py .
 
 EXPOSE 8082
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8082"]
```