# Lab 2 — Containerization: Inspect, Understand, Optimize

## Task 1 — Docker Inspection & Operations

### 2.1 Image Inspection

#### Images

Command:

```bash
docker images | grep app
```

Output:

```text
app-gateway                                 latest          9109456d03d0   56 minutes ago   214MB
app-events                                  latest          828e5ce4a517   2 hours ago      233MB
app-payments                                latest          f45b4f3add24   2 hours ago      212MB
```

#### Image History

Command:

```bash
docker history app-gateway --no-trunc --format "table {{.CreatedBy}}\t{{.Size}}"
```

Output:

```text
CREATED BY  SIZE
CMD ["uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8080"] 0B
EXPOSE map[8080/tcp:{}] 0B
COPY main.py . # buildkit    24.6kB
RUN /bin/sh -c pip install --no-cache-dir -r requirements.txt # buildkit 29.2MB
COPY requirements.txt . # buildkit   12.3kB
WORKDIR /app   8.19kB
CMD ["python3"]  0B
RUN /bin/sh -c set -eux;  for src in idle3 pip3 pydoc3 python3 python3-config; do   dst="$(echo "$src" | tr -d 3)";   [ -s "/usr/local/bin/$src" ];   [ ! -e "/usr/local/bin/$dst" ];   ln -svT "$src" "/usr/local/bin/$dst";  done # buildkit  16.4kB
RUN /bin/sh -c set -eux;   savedAptMark="$(apt-mark showmanual)";  apt-get update;  apt-get install -y --no-install-recommends   dpkg-dev   gcc   gnupg   libbluetooth-dev   libbz2-dev   libc6-dev   libdb-dev   libffi-dev   libgdbm-dev   liblzma-dev   libncursesw5-dev   libreadline-dev   libsqlite3-dev   libssl-dev   make   tk-dev   uuid-dev   wget   xz-utils   zlib1g-dev  ;   wget -O python.tar.xz "https://www.python.org/ftp/python/${PYTHON_VERSION%%[a-z]*}/Python-$PYTHON_VERSION.tar.xz";  echo "$PYTHON_SHA256 *python.tar.xz" | sha256sum -c -;  wget -O python.tar.xz.asc "https://www.python.org/ftp/python/${PYTHON_VERSION%%[a-z]*}/Python-$PYTHON_VERSION.tar.xz.asc";  GNUPGHOME="$(mktemp -d)"; export GNUPGHOME;  gpg --batch --keyserver hkps://keys.openpgp.org --recv-keys "$GPG_KEY";  gpg --batch --verify python.tar.xz.asc python.tar.xz;  gpgconf --kill all;  rm -rf "$GNUPGHOME" python.tar.xz.asc;  mkdir -p /usr/src/python;  tar --extract --directory /usr/src/python --strip-components=1 --file python.tar.xz;  rm python.tar.xz;   cd /usr/src/python;  gnuArch="$(dpkg-architecture --query DEB_BUILD_GNU_TYPE)";  ./configure   --build="$gnuArch"   --enable-loadable-sqlite-extensions   --enable-optimizations   --enable-option-checking=fatal   --enable-shared   $(test "${gnuArch%%-*}" != 'riscv64' && echo '--with-lto')   --with-ensurepip  ;  nproc="$(nproc)";  EXTRA_CFLAGS="$(dpkg-buildflags --get CFLAGS)";  LDFLAGS="$(dpkg-buildflags --get LDFLAGS)";  LDFLAGS="${LDFLAGS:-} -Wl,--strip-all";  arch="$(dpkg --print-architecture)"; arch="${arch##*-}";  case "$arch" in   amd64|arm64)    EXTRA_CFLAGS="${EXTRA_CFLAGS:-} -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer";    ;;   i386)    ;;   *)    EXTRA_CFLAGS="${EXTRA_CFLAGS:-} -fno-omit-frame-pointer";    ;;  esac;  make -j "$nproc"   "EXTRA_CFLAGS=${EXTRA_CFLAGS:-}"   "LDFLAGS=${LDFLAGS:-}"  ;  rm python;  make -j "$nproc"   "EXTRA_CFLAGS=${EXTRA_CFLAGS:-}"   "LDFLAGS=${LDFLAGS:-} -Wl,-rpath='\$\$ORIGIN/../lib'"   python  ;  make install;   cd /;  rm -rf /usr/src/python;   find /usr/local -depth   \(    \( -type d -a \( -name test -o -name tests -o -name idle_test \) \)    -o \( -type f -a \( -name '*.pyc' -o -name '*.pyo' -o -name 'libpython*.a' \) \)   \) -exec rm -rf '{}' +  ;   ldconfig;   apt-mark auto '.*' > /dev/null;  apt-mark manual $savedAptMark;  find /usr/local -type f -executable -not \( -name '*tkinter*' \) -exec ldd '{}' ';'   | awk '/=>/ { so = $(NF-1); if (index(so, "/usr/local/") == 1) { next }; gsub("^/(usr/)?", "", so); printf "*%s\n", so }'   | sort -u   | xargs -rt dpkg-query --search   | awk 'sub(":$", "", $1) { print $1 }'   | sort -u   | xargs -r apt-mark manual  ;  apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false;  apt-get dist-clean;   export PYTHONDONTWRITEBYTECODE=1;  python3 --version;  pip3 --version # buildkit   40.2MB
ENV PYTHON_SHA256=639e43243c620a308f968213df9e00f2f8f62332f7adbaa7a7eeb9783057c690  0B
ENV PYTHON_VERSION=3.13.14 0B
ENV GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305 0B
RUN /bin/sh -c set -eux;  apt-get update;  apt-get install -y --no-install-recommends   ca-certificates   netbase   tzdata  ;  apt-get dist-clean # buildkit 4.94MB
ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin  0B
# debian.sh --arch 'amd64' out/ 'trixie' '@1781049600'  87.4MB
```

#### Analysis

Largest layer:

```text
debian.sh --arch 'amd64' out/ 'trixie' '@1781049600' — 87.4MB
```

```text
16 layers
```

Reason:

```text
The largest layer is the base Debian layer from the Python base image (87.4MB). It contains the operating system files and shared libraries required by the container.

The largest application-specific layer is the pip install layer (29.2MB), which installs Python dependencies from requirements.txt, including FastAPI, Uvicorn, httpx, and Prometheus libraries.

Most of the image size comes from the base image and installed dependencies rather than the application code itself.
```

---

### 2.2 Container Inspection

#### Service IP Addresses

Events:

```text
➜  app git:(feature/lab1) ✗ docker inspect app-events-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-events-1 172.18.0.5
```

Gateway:

```text
➜  app git:(feature/lab1) ✗ docker inspect app-gateway-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-gateway-1 172.18.0.6
```

Payments:

```text
➜  app git:(feature/lab1) ✗ docker inspect app-payments-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-payments-1 172.18.0.2
```

#### Payments Environment Variables

Command:

```bash
docker inspect app-payments-1 --format '{{range .Config.Env}}{{println .}}{{end}}'
```

Output:

```text
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.14
PYTHON_SHA256=639e43243c620a308f968213df9e00f2f8f62332f7adbaa7a7eeb9783057c690
```

---

### 2.3 Live Debugging with Exec

#### Current User

Command:

```bash
docker exec app-gateway-1 whoami
```

Output:

```text
root
```

#### User ID

Command:

```bash
docker exec app-gateway-1 id
```

Output:

```text
uid=0(root) gid=0(root) groups=0(root)
```

#### DNS Configuration

Command:

```bash
docker exec app-gateway-1 cat /etc/resolv.conf
```

Output:

```text
# Generated by Docker Engine.
# This file can be edited; Docker Engine will not make further changes once it
# has been modified.

nameserver 127.0.0.11
options ndots:0

# Based on host file: '/etc/resolv.conf' (internal resolver)
# ExtServers: [8.8.8.8 1.1.1.1]
# Overrides: [nameservers]
# Option ndots from: internal
```

#### Service Discovery: Events

Command:

```bash
docker exec app-gateway-1 python3 -c "
import urllib.request
print(urllib.request.urlopen('http://events:8081/health').read().decode())
"
```

Output:

```text
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
```

#### Service Discovery: Payments

Command:

```bash
docker exec app-gateway-1 python3 -c "
import urllib.request
print(urllib.request.urlopen('http://payments:8082/health').read().decode())
"
```

Output:

```text
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

---

### 2.4 Logs Analysis

#### Initial Logs

Gateway:

```text
➜  app git:(feature/lab1) ✗ docker compose logs gateway --tail=20
gateway-1  | {"time":"2026-06-11 13:04:20,755","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/5/reserve "HTTP/1.1 200 OK""}
gateway-1  | INFO:     172.18.0.1:46770 - "POST /events/5/reserve HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-06-11 13:04:20,901","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | INFO:     172.18.0.1:46772 - "GET /events HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-06-11 13:04:21,037","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/3/reserve "HTTP/1.1 200 OK""}
gateway-1  | INFO:     172.18.0.1:46778 - "POST /events/3/reserve HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-06-11 13:04:21,188","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | INFO:     172.18.0.1:46786 - "GET /events HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-06-11 13:04:21,339","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | INFO:     172.18.0.1:46802 - "GET /events HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-06-11 13:04:21,489","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | INFO:     172.18.0.1:46816 - "GET /events HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-06-11 13:04:21,627","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | INFO:     172.18.0.1:46832 - "GET /events HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-06-11 13:04:21,757","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | INFO:     172.18.0.1:46844 - "GET /events HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-06-11 13:04:21,904","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | INFO:     172.18.0.1:46856 - "GET /events HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-06-11 14:07:33,523","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | INFO:     172.18.0.1:40820 - "GET /events HTTP/1.1" 200 OK
```

Events:

```text
➜  app git:(feature/lab1) ✗ docker compose logs events --tail=20
events-1  | INFO:     172.18.0.6:33198 - "POST /reservations/b1583ca1-4b67-491d-bf60-8af14397027f/confirm HTTP/1.1" 200 OK
events-1  | INFO:     172.18.0.6:33198 - "GET /events HTTP/1.1" 200 OK
events-1  | INFO:     172.18.0.6:33198 - "POST /events/2/reserve HTTP/1.1" 409 Conflict
events-1  | {"time":"2026-06-11 13:04:19,780","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 87316299-d1df-4b71-94f7-3648c3d34eb5"}
events-1  | INFO:     172.18.0.6:33198 - "POST /events/1/reserve HTTP/1.1" 200 OK
events-1  | INFO:     172.18.0.6:33198 - "GET /events HTTP/1.1" 200 OK
events-1  | INFO:     172.18.0.6:33198 - "GET /events HTTP/1.1" 200 OK
events-1  | {"time":"2026-06-11 13:04:20,752","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 5: 0e361dd8-bba0-47ae-9e76-1d7649a51217"}
events-1  | INFO:     172.18.0.6:33198 - "POST /events/5/reserve HTTP/1.1" 200 OK
events-1  | INFO:     172.18.0.6:33198 - "GET /events HTTP/1.1" 200 OK
events-1  | {"time":"2026-06-11 13:04:21,036","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 3: 3810eb68-f00a-493b-8479-a3b1e00a0194"}
events-1  | INFO:     172.18.0.6:33198 - "POST /events/3/reserve HTTP/1.1" 200 OK
events-1  | INFO:     172.18.0.6:33198 - "GET /events HTTP/1.1" 200 OK
events-1  | INFO:     172.18.0.6:33198 - "GET /events HTTP/1.1" 200 OK
events-1  | INFO:     172.18.0.6:33198 - "GET /events HTTP/1.1" 200 OK
events-1  | INFO:     172.18.0.6:33198 - "GET /events HTTP/1.1" 200 OK
events-1  | INFO:     172.18.0.6:33198 - "GET /events HTTP/1.1" 200 OK
events-1  | INFO:     172.18.0.6:33198 - "GET /events HTTP/1.1" 200 OK
events-1  | INFO:     172.18.0.6:58194 - "GET /health HTTP/1.1" 200 OK
events-1  | INFO:     172.18.0.6:44714 - "GET /events HTTP/1.1" 200 OK
```

Payments:

```text
➜  app git:(feature/lab1) ✗ docker compose logs payments --tail=20
payments-1  | INFO:     Started server process [1]
payments-1  | INFO:     Waiting for application startup.
payments-1  | INFO:     Application startup complete.
payments-1  | INFO:     Uvicorn running on http://0.0.0.0:8082 (Press CTRL+C to quit)
payments-1  | INFO:     172.18.0.6:51396 - "GET /health HTTP/1.1" 200 OK
```

#### Request Trace

Gateway logs:

```text
➜  app git:(feature/lab1) ✗ docker compose logs gateway --tail=5
gateway-1  | INFO:     172.18.0.1:40820 - "GET /events HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-06-11 14:11:53,043","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | INFO:     172.18.0.1:59936 - "GET /events HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-06-11 14:11:57,607","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve "HTTP/1.1 200 OK""}
gateway-1  | INFO:     172.18.0.1:52798 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

Events logs:

```text
➜  app git:(feature/lab1) ✗ docker compose logs events --tail=5
events-1  | INFO:     172.18.0.6:58194 - "GET /health HTTP/1.1" 200 OK
events-1  | INFO:     172.18.0.6:44714 - "GET /events HTTP/1.1" 200 OK
events-1  | INFO:     172.18.0.6:53758 - "GET /events HTTP/1.1" 200 OK
events-1  | {"time":"2026-06-11 14:11:57,606","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: f81e918b-be32-41ae-b370-79c3597067c0"}
events-1  | INFO:     172.18.0.6:53758 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

Observation:

```text
A single request can be followed across multiple services using timestamps. The gateway logged a POST request to /events/1/reserve, and the events service logged the corresponding reservation at the same time. This confirms that the gateway successfully forwarded the request to the events service through Docker service discovery
```

---

### 2.5 Network Inspection

#### Docker Networks

Command:

```bash
docker network ls | grep app
```

Output:

```text
b75dbe54b859   app_default   bridge    local
```

#### Network Members

Command:

```bash
docker network inspect app_default --format '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}'
```

Output:

```text
app-postgres-1: 172.18.0.4/16
app-gateway-1: 172.18.0.6/16
app-events-1: 172.18.0.5/16
app-redis-1: 172.18.0.3/16
app-payments-1: 172.18.0.2/16
```

---

### 2.6 Proof of Work Answers

#### How does the gateway find the events service?

```text
The gateway uses Docker's internal DNS service discovery. Inside the Docker network, the hostname "events" automatically resolves to the IP address of the events container.
```

#### What IP does events resolve to?

```text
172.18.0.5
```

---

## Task 2 — Dockerfile Optimization

### 2.7 .dockerignore

#### Content

```text
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

#### Image Sizes Before

```text
app-gateway                                 latest          9109456d03d0   2 hours ago     214MB
app-events                                  latest          828e5ce4a517   3 hours ago     233MB
app-payments                                latest          f45b4f3add24   3 hours ago     212MB
```

#### Image Sizes After

```text
app-events                                  latest          6fb15b89ed1a   7 seconds ago   233MB
app-gateway                                 latest          f505d1a5fffb   7 seconds ago   214MB
app-payments                                latest          ad3c4f0ba55a   8 seconds ago   212MB
```

#### Observation

```text
Adding the .dockerignore files did not noticeably change the image sizes. The ignored files (__pycache__, .git, .env, .md, and .vscode) were either not present in the build context or were too small to significantly affect the final image size. Most of the image size comes from the base Python image and installed dependencies rather than project files
```

---

### 2.8 Non-Root User

#### whoami Output

```text
➜  app git:(feature/lab1) ✗ docker exec app-gateway-1 whoami
app
```

#### Dockerfile Diff

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
index c86db33..432bcd7 100644
--- a/app/gateway/main.py
+++ b/app/gateway/main.py
@@ -331,14 +331,16 @@ async def pay_reservation(reservation_id: str):
         payment_ref = pay_resp.json().get("payment_ref", "unknown")
     except CircuitOpenError:
         log.error("circuit open, skipping payments call")
-        raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
+        return payments_unavailable_response(reservation_id)
     except httpx.TimeoutException:
-        raise HTTPException(504, "Payment service timeout")
+        log.error("payment service timeout")
+        return payments_unavailable_response(reservation_id)
     except httpx.HTTPStatusError as e:
-        raise HTTPException(e.response.status_code, "Payment failed")
+        log.error(f"payment failed: {e}")
+        return payments_unavailable_response(reservation_id)
     except Exception as e:
         log.error(f"payment error: {e}")
-        raise HTTPException(502, "Payment service unavailable")
+        return payments_unavailable_response(reservation_id)
 
     # 2. Confirm reservation in events.
     try:
@@ -356,3 +358,13 @@ async def pay_reservation(reservation_id: str):
     asyncio.create_task(_notify_order_confirmed(reservation_id))
 
     return result
+
+def payments_unavailable_response(reservation_id: str):
+    return JSONResponse(
+        status_code=503,
+        content={
+            "error": "payments_unavailable",
+            "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.",
+            "reservation_id": reservation_id,
+        },
+    )
\ No newline at end of file
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
```

---

## Bonus Task — Trace a Request Across Services

### Timestamped Logs

```text
docker compose logs --timestamps
events-1  | 2026-06-11T15:15:42.838471904Z INFO:     Started server process [1]
events-1  | 2026-06-11T15:15:42.838522181Z INFO:     Waiting for application startup.
events-1  | 2026-06-11T15:15:42.869290979Z {"time":"2026-06-11 15:15:42,868","level":"INFO","service":"events","msg":"DB pool created (max=10)"}
events-1  | 2026-06-11T15:15:42.879240245Z {"time":"2026-06-11 15:15:42,878","level":"INFO","service":"events","msg":"Redis connected"}
events-1  | 2026-06-11T15:15:42.879429416Z INFO:     Application startup complete.
events-1  | 2026-06-11T15:15:42.880028023Z INFO:     Uvicorn running on http://0.0.0.0:8081 (Press CTRL+C to quit)
events-1  | 2026-06-11T15:18:26.776310355Z {"time":"2026-06-11 15:18:26,775","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 32d746e2-c0cb-4d3f-aa2c-56b5d573dfa5"}
events-1  | 2026-06-11T15:18:26.778388893Z INFO:     172.18.0.6:45508 - "POST /events/1/reserve HTTP/1.1" 200 OK
events-1  | 2026-06-11T15:18:26.959983057Z {"time":"2026-06-11 15:18:26,959","level":"INFO","service":"events","msg":"Order confirmed: 32d746e2-c0cb-4d3f-aa2c-56b5d573dfa5"}
events-1  | 2026-06-11T15:18:26.961407561Z INFO:     172.18.0.6:45508 - "POST /reservations/32d746e2-c0cb-4d3f-aa2c-56b5d573dfa5/confirm HTTP/1.1" 200 OK
payments-1  | 2026-06-11T15:15:36.829195331Z INFO:     Started server process [1]
payments-1  | 2026-06-11T15:15:36.829407507Z INFO:     Waiting for application startup.
payments-1  | 2026-06-11T15:15:36.829781629Z INFO:     Application startup complete.
payments-1  | 2026-06-11T15:15:36.830098437Z INFO:     Uvicorn running on http://0.0.0.0:8082 (Press CTRL+C to quit)
payments-1  | 2026-06-11T15:18:26.935127947Z {"time":"2026-06-11 15:18:26,934","level":"INFO","service":"payments","msg":"Payment success: PAY-A76F3E06 for 32d746e2-c0cb-4d3f-aa2c-56b5d573dfa5"}
payments-1  | 2026-06-11T15:18:26.936379525Z INFO:     172.18.0.6:51608 - "POST /charge HTTP/1.1" 200 OK
gateway-1   | 2026-06-11T15:15:43.088724463Z INFO:     Started server process [1]
gateway-1   | 2026-06-11T15:15:43.088789429Z INFO:     Waiting for application startup.
gateway-1   | 2026-06-11T15:15:43.089168802Z INFO:     Application startup complete.
gateway-1   | 2026-06-11T15:15:43.089403381Z INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
gateway-1   | 2026-06-11T15:18:26.780793698Z {"time":"2026-06-11 15:18:26,780","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve "HTTP/1.1 200 OK""}
gateway-1   | 2026-06-11T15:18:26.783576219Z INFO:     172.18.0.1:43602 - "POST /events/1/reserve HTTP/1.1" 200 OK
gateway-1   | 2026-06-11T15:18:26.937708564Z {"time":"2026-06-11 15:18:26,937","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge "HTTP/1.1 200 OK""}
gateway-1   | 2026-06-11T15:18:26.962881178Z {"time":"2026-06-11 15:18:26,962","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/reservations/32d746e2-c0cb-4d3f-aa2c-56b5d573dfa5/confirm "HTTP/1.1 200 OK""}
gateway-1   | 2026-06-11T15:18:26.965037788Z INFO:     172.18.0.1:43610 - "POST /reserve/32d746e2-c0cb-4d3f-aa2c-56b5d573dfa5/pay HTTP/1.1" 200 OK
postgres-1  | 2026-06-11T15:15:36.040298648Z 
postgres-1  | 2026-06-11T15:15:36.040371325Z PostgreSQL Database directory appears to contain a database; Skipping initialization
postgres-1  | 2026-06-11T15:15:36.040379300Z 
postgres-1  | 2026-06-11T15:15:36.095940841Z 2026-06-11 15:15:36.095 UTC [1] LOG:  starting PostgreSQL 17.10 on x86_64-pc-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit
postgres-1  | 2026-06-11T15:15:36.095999263Z 2026-06-11 15:15:36.095 UTC [1] LOG:  listening on IPv4 address "0.0.0.0", port 5432
postgres-1  | 2026-06-11T15:15:36.096006104Z 2026-06-11 15:15:36.095 UTC [1] LOG:  listening on IPv6 address "::", port 5432
postgres-1  | 2026-06-11T15:15:36.101531763Z 2026-06-11 15:15:36.101 UTC [1] LOG:  listening on Unix socket "/var/run/postgresql/.s.PGSQL.5432"
postgres-1  | 2026-06-11T15:15:36.112606781Z 2026-06-11 15:15:36.112 UTC [29] LOG:  database system was shut down at 2026-06-11 15:15:33 UTC
postgres-1  | 2026-06-11T15:15:36.124625085Z 2026-06-11 15:15:36.124 UTC [1] LOG:  database system is ready to accept connections
redis-1     | 2026-06-11T15:15:36.066807565Z 1:C 11 Jun 2026 15:15:36.066 * oO0OoO0OoO0Oo Redis is starting oO0OoO0OoO0Oo
redis-1     | 2026-06-11T15:15:36.066875370Z 1:C 11 Jun 2026 15:15:36.066 * Redis version=7.4.9, bits=64, commit=00000000, modified=0, pid=1, just started
redis-1     | 2026-06-11T15:15:36.066882874Z 1:C 11 Jun 2026 15:15:36.066 # Warning: no config file specified, using the default config. In order to specify a config file use redis-server /path/to/redis.conf
redis-1     | 2026-06-11T15:15:36.067669326Z 1:M 11 Jun 2026 15:15:36.067 * monotonic clock: POSIX clock_gettime
redis-1     | 2026-06-11T15:15:36.071526779Z 1:M 11 Jun 2026 15:15:36.071 * Running mode=standalone, port=6379.
redis-1     | 2026-06-11T15:15:36.072569709Z 1:M 11 Jun 2026 15:15:36.072 * Server initialized
redis-1     | 2026-06-11T15:15:36.072782995Z 1:M 11 Jun 2026 15:15:36.072 * Ready to accept connections tcp
```

### Request Flow

| Service  | Action                | Timestamp |
| -------- | --------------------- | --------- |
| Gateway  | Forwarded reservation request to events | 2026-06-11T15:18:26.780793698Z |
| Events   | Created reservation `32d746e2-c0cb-4d3f-aa2c-56b5d573dfa5` | 2026-06-11T15:18:26.776310355Z |
| Gateway  | Forwarded payment request to payments | 2026-06-11T15:18:26.937708564Z |
| Payments | Processed payment `PAY-A76F3E06` | 2026-06-11T15:18:26.935127947Z |
| Gateway  | Forwarded confirmation request to events | 2026-06-11T15:18:26.962881178Z |
| Events   | Confirmed reservation `32d746e2-c0cb-4d3f-aa2c-56b5d573dfa5` | 2026-06-11T15:18:26.959983057Z |
| Gateway  | Returned final response to client | 2026-06-11T15:18:26.965037788Z |

### End-to-End Time

```text
Start:  2026-06-11T15:18:26.780793698Z
End:    2026-06-11T15:18:26.965037788Z

Total end-to-end time: approximately 184 ms
```

### Analysis

```text
The request flow was gateway -> events -> payments -> events. First, the gateway forwarded the reservation request to the events service. The events service created the reservation with ID 32d746e2-c0cb-4d3f-aa2c-56b5d573dfa5. Then, during the payment step, the gateway called the payments service. The payments service successfully processed the charge and returned payment reference PAY-A76F3E06. Finally, the gateway called the events service again to confirm the reservation. The events service confirmed the order, and the gateway returned a 200 OK response to the client. The full flow took about 184 ms from the gateway's first logged action to the final gateway response

```
