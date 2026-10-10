# Lab 2 — Containerization: Inspect, Understand, Optimize

Author: Walkerino
Date: 2026-09-13
Status: local experiments and report complete; PR and Moodle submission pending.

All terminal outputs below were collected by the assistant against the local QuickTicket Docker Compose application. Commands run from the repository root. The report follows Lab 1's command/output/analysis format. Existing changes in `app/gateway/main.py` were preserved. The actual base image is `python:3.13-slim`, although the lab examples mention Python 3.12.

## Task 1 — Docker Inspection & Operations

### 1. Image sizes and layers

```bash
docker images --filter reference='app-*'
```

```text
IMAGE                 ID             DISK USAGE   CONTENT SIZE   EXTRA
app-events:latest     bc474b942e49        272MB           61MB   U
app-gateway:latest    d75628126e9b        251MB         55.7MB   U
app-payments:latest   813374aa9be9        249MB         55.2MB   U
```

This filter selects the same application images requested by `docker images | grep app`. This Docker CLI reports both disk usage and content size; these columns are kept separate throughout the comparison. Events is the largest image (272 MB disk usage); gateway uses 251 MB.

```bash
docker history app-gateway --no-trunc --format "table {{.CreatedBy}}\t{{.Size}}"
```

```text
CREATED BY SIZE
CMD ["uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8080"] 0B
EXPOSE [8080/tcp] 0B
COPY main.py . # buildkit 24.6kB
RUN /bin/sh -c pip install --no-cache-dir -r requirements.txt # buildkit 29.1MB
COPY requirements.txt . # buildkit 12.3kB
WORKDIR /app 8.19kB
CMD ["python3"] 0B
RUN /bin/sh -c set -eux; for src in idle3 pip3 pydoc3 python3 python3-config; do dst="$(echo "$src" | tr -d 3)"; [ -s "/usr/local/bin/$src" ]; [ ! -e "/usr/local/bin/$dst" ]; ln -svT "$src" "/usr/local/bin/$dst"; done # buildkit 16.4kB
RUN /bin/sh -c set -eux; savedAptMark="$(apt-mark showmanual)"; apt-get update; apt-get install -y --no-install-recommends dpkg-dev g++ gcc gnupg libbluetooth-dev libbz2-dev libc6-dev libdb-dev libffi-dev libgdbm-dev liblzma-dev libncursesw5-dev libreadline-dev libsqlite3-dev libssl-dev make tk-dev uuid-dev wget xz-utils zlib1g-dev ; wget -O python.tar.xz "https://www.python.org/ftp/python/${PYTHON_VERSION%%[a-z]*}/Python-$PYTHON_VERSION.tar.xz"; echo "$PYTHON_SHA256 *python.tar.xz" | sha256sum -c -; wget -O python.tar.xz.asc "https://www.python.org/ftp/python/${PYTHON_VERSION%%[a-z]*}/Python-$PYTHON_VERSION.tar.xz.asc"; GNUPGHOME="$(mktemp -d)"; export GNUPGHOME; gpg --batch --keyserver hkps://keys.openpgp.org --recv-keys "$GPG_KEY"; gpg --batch --verify python.tar.xz.asc python.tar.xz; gpgconf --kill all; rm -rf "$GNUPGHOME" python.tar.xz.asc; mkdir -p /usr/src/python; tar --extract --directory /usr/src/python --strip-components=1 --file python.tar.xz; rm python.tar.xz; cd /usr/src/python; gnuArch="$(dpkg-architecture --query DEB_BUILD_GNU_TYPE)"; ./configure --build="$gnuArch" --enable-loadable-sqlite-extensions --enable-optimizations --enable-option-checking=fatal --enable-shared $(test "${gnuArch%%-*}" != 'riscv64' && echo '--with-lto') --with-ensurepip ; nproc="$(nproc)"; EXTRA_CFLAGS="$(dpkg-buildflags --get CFLAGS)"; LDFLAGS="$(dpkg-buildflags --get LDFLAGS)"; LDFLAGS="${LDFLAGS:-} -Wl,--strip-all"; arch="$(dpkg --print-architecture)"; arch="${arch##*-}"; case "$arch" in amd64|arm64) EXTRA_CFLAGS="${EXTRA_CFLAGS:-} -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer"; ;; i386) ;; *) EXTRA_CFLAGS="${EXTRA_CFLAGS:-} -fno-omit-frame-pointer"; ;; esac; make -j "$nproc" "EXTRA_CFLAGS=${EXTRA_CFLAGS:-}" "LDFLAGS=${LDFLAGS:-}" ; rm python; make -j "$nproc" "EXTRA_CFLAGS=${EXTRA_CFLAGS:-}" "LDFLAGS=${LDFLAGS:-} -Wl,-rpath='\$\$ORIGIN/../lib'" python ; make install; cd /; rm -rf /usr/src/python; find /usr/local -depth \( \( -type d -a \( -name test -o -name tests -o -name idle_test \) \) -o \( -type f -a \( -name '*.pyc' -o -name '*.pyo' -o -name 'libpython*.a' \) \) \) -exec rm -rf '{}' + ; ldconfig; apt-mark auto '.*' > /dev/null; apt-mark manual $savedAptMark; find /usr/local -type f -executable -not \( -name '*tkinter*' \) -exec ldd '{}' ';' | awk '/=>/ { so = $(NF-1); if (index(so, "/usr/local/") == 1) { next }; gsub("^/(usr/)?", "", so); printf "*%s\n", so }' | sort -u | xargs -rt dpkg-query --search | awk 'sub(":$", "", $1) { print $1 }' | sort -u | xargs -r apt-mark manual ; apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false; apt-get dist-clean; export PYTHONDONTWRITEBYTECODE=1; python3 --version; pip3 --version # buildkit 43.7MB
ENV PYTHON_SHA256=1e66a7945a48390ee4c2a4268a0e4185884059a13c4aab6d148aa208deea4a76 0B
ENV PYTHON_VERSION=3.13.15 0B
ENV GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305 0B
RUN /bin/sh -c set -eux; apt-get update; apt-get install -y --no-install-recommends ca-certificates netbase tzdata ; apt-get dist-clean # buildkit 13.1MB
ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin 0B
# debian.sh --arch 'arm64' out/ 'trixie' '@1787529600' 109MB
```

Whitespace padding was removed from the table, but instruction text was retained. Gateway has **8 filesystem layers**, verified with `docker image inspect app-gateway` → `RootFS.Layers`, and **15 history entries** including metadata-only instructions. `CMD`, `ENV`, and `EXPOSE` entries do not add filesystem content.

The `RUN pip install --no-cache-dir -r requirements.txt` layer is **29.1 MB** and contains the application dependencies. The largest layer overall is the **109 MB Debian base filesystem**, followed by the Python runtime layer (43.7 MB). Thus, pip installation is the largest application dependency layer, but not the largest image layer in this run.

For completeness, the largest image, events, was also inspected after optimization:

```bash
docker history app-events --no-trunc --format '{{.CreatedBy}}\t{{.Size}}'
```

```text
CMD ["uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8081"] 0B
USER app 0B
RUN /bin/sh -c addgroup --system app && adduser --system --ingroup app app # buildkit 45.1kB
EXPOSE [8081/tcp] 0B
COPY main.py . # buildkit 20.5kB
RUN /bin/sh -c pip install --no-cache-dir -r requirements.txt # buildkit 44.5MB
COPY requirements.txt . # buildkit 12.3kB
WORKDIR /app 8.19kB
CMD ["python3"] 0B
RUN /bin/sh -c set -eux; for src in idle3 pip3 pydoc3 python3 python3-config; do dst="$(echo "$src" | tr -d 3)"; [ -s "/usr/local/bin/$src" ]; [ ! -e "/usr/local/bin/$dst" ]; ln -svT "$src" "/usr/local/bin/$dst"; done # buildkit 16.4kB
RUN /bin/sh -c set -eux; savedAptMark="$(apt-mark showmanual)"; apt-get update; apt-get install -y --no-install-recommends dpkg-dev g++ gcc gnupg libbluetooth-dev libbz2-dev libc6-dev libdb-dev libffi-dev libgdbm-dev liblzma-dev libncursesw5-dev libreadline-dev libsqlite3-dev libssl-dev make tk-dev uuid-dev wget xz-utils zlib1g-dev ; wget -O python.tar.xz "https://www.python.org/ftp/python/${PYTHON_VERSION%%[a-z]*}/Python-$PYTHON_VERSION.tar.xz"; echo "$PYTHON_SHA256 *python.tar.xz" | sha256sum -c -; wget -O python.tar.xz.asc "https://www.python.org/ftp/python/${PYTHON_VERSION%%[a-z]*}/Python-$PYTHON_VERSION.tar.xz.asc"; GNUPGHOME="$(mktemp -d)"; export GNUPGHOME; gpg --batch --keyserver hkps://keys.openpgp.org --recv-keys "$GPG_KEY"; gpg --batch --verify python.tar.xz.asc python.tar.xz; gpgconf --kill all; rm -rf "$GNUPGHOME" python.tar.xz.asc; mkdir -p /usr/src/python; tar --extract --directory /usr/src/python --strip-components=1 --file python.tar.xz; rm python.tar.xz; cd /usr/src/python; gnuArch="$(dpkg-architecture --query DEB_BUILD_GNU_TYPE)"; ./configure --build="$gnuArch" --enable-loadable-sqlite-extensions --enable-optimizations --enable-option-checking=fatal --enable-shared $(test "${gnuArch%%-*}" != 'riscv64' && echo '--with-lto') --with-ensurepip ; nproc="$(nproc)"; EXTRA_CFLAGS="$(dpkg-buildflags --get CFLAGS)"; LDFLAGS="$(dpkg-buildflags --get LDFLAGS)"; LDFLAGS="${LDFLAGS:-} -Wl,--strip-all"; arch="$(dpkg --print-architecture)"; arch="${arch##*-}"; case "$arch" in amd64|arm64) EXTRA_CFLAGS="${EXTRA_CFLAGS:-} -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer"; ;; i386) ;; *) EXTRA_CFLAGS="${EXTRA_CFLAGS:-} -fno-omit-frame-pointer"; ;; esac; make -j "$nproc" "EXTRA_CFLAGS=${EXTRA_CFLAGS:-}" "LDFLAGS=${LDFLAGS:-}" ; rm python; make -j "$nproc" "EXTRA_CFLAGS=${EXTRA_CFLAGS:-}" "LDFLAGS=${LDFLAGS:-} -Wl,-rpath='\$\$ORIGIN/../lib'" python ; make install; cd /; rm -rf /usr/src/python; find /usr/local -depth \( \( -type d -a \( -name test -o -name tests -o -name idle_test \) \) -o \( -type f -a \( -name '*.pyc' -o -name '*.pyo' -o -name 'libpython*.a' \) \) \) -exec rm -rf '{}' + ; ldconfig; apt-mark auto '.*' > /dev/null; apt-mark manual $savedAptMark; find /usr/local -type f -executable -not \( -name '*tkinter*' \) -exec ldd '{}' ';' | awk '/=>/ { so = $(NF-1); if (index(so, "/usr/local/") == 1) { next }; gsub("^/(usr/)?", "", so); printf "*%s\n", so }' | sort -u | xargs -rt dpkg-query --search | awk 'sub(":$", "", $1) { print $1 }' | sort -u | xargs -r apt-mark manual ; apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false; apt-get dist-clean; export PYTHONDONTWRITEBYTECODE=1; python3 --version; pip3 --version # buildkit 43.7MB
ENV PYTHON_SHA256=1e66a7945a48390ee4c2a4268a0e4185884059a13c4aab6d148aa208deea4a76 0B
ENV PYTHON_VERSION=3.13.15 0B
ENV GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305 0B
RUN /bin/sh -c set -eux; apt-get update; apt-get install -y --no-install-recommends ca-certificates netbase tzdata ; apt-get dist-clean # buildkit 13.1MB
ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin 0B
# debian.sh --arch 'arm64' out/ 'trixie' '@1787529600' 109MB
```

### 2. Container addresses and payments environment

```bash
docker inspect app-gateway-1 app-events-1 app-payments-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
```

```text
/app-gateway-1 172.18.0.6
/app-events-1 172.18.0.5
/app-payments-1 172.18.0.3
```

```bash
docker inspect app-payments-1 --format '{{range .Config.Env}}{{println .}}{{end}}'
```

```text
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.15
PYTHON_SHA256=1e66a7945a48390ee4c2a4268a0e4185884059a13c4aab6d148aa208deea4a76
```

Payments has zero injected failure probability and zero injected latency. The remaining variables are inherited Python image configuration.

### 3. Live debugging and service discovery

```bash
docker exec app-gateway-1 sh -c 'whoami; id; cat /etc/resolv.conf'
```

```text
root
uid=0(root) gid=0(root) groups=0(root)
# Generated by Docker Engine.
# This file can be edited; Docker Engine will not make further changes once it
# has been modified.

nameserver 127.0.0.11
options ndots:0

# Based on host file: '/etc/resolv.conf' (internal resolver)
# ExtServers: [host(192.168.65.7)]
# Overrides: []
# Option ndots from: internal
```

```bash
docker exec app-gateway-1 python3 -c "import socket,urllib.request; print('events resolves to:',socket.gethostbyname('events')); print('events:',urllib.request.urlopen('http://events:8081/health').read().decode()); print('payments:',urllib.request.urlopen('http://payments:8082/health').read().decode())"
```

```text
events resolves to: 172.18.0.5
events: {"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
payments: {"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

Gateway initially ran as root. Both internal HTTP health calls succeeded. Gateway uses `EVENTS_URL=http://events:8081`; Docker Compose registers the service name on `app_default`, and the embedded DNS resolver at `127.0.0.11` resolves it. During the initial inspection, `events` resolved to **172.18.0.5**. Internal requests use port 8081 directly, without going through a host port.

### 4. Logs and resource usage

```bash
docker compose -f app/docker-compose.yaml logs --tail=20 gateway events payments
```

```text
payments-1  | INFO:     Started server process [1]
payments-1  | INFO:     Waiting for application startup.
payments-1  | INFO:     Application startup complete.
payments-1  | INFO:     Uvicorn running on http://0.0.0.0:8082 (Press CTRL+C to quit)
payments-1  | INFO:     192.168.65.1:35311 - "GET /health HTTP/1.1" 200 OK
gateway-1   | INFO:     192.168.65.1:63904 - "GET /events HTTP/1.1" 200 OK
gateway-1   | {"time":"2026-09-13 12:21:00,941","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
payments-1  | INFO:     172.18.0.6:40910 - "GET /health HTTP/1.1" 200 OK
payments-1  | INFO:     172.18.0.6:55238 - "GET /health HTTP/1.1" 200 OK
gateway-1   | INFO:     192.168.65.1:44447 - "GET /events HTTP/1.1" 200 OK
events-1    | INFO:     172.18.0.6:42472 - "POST /events/2/reserve HTTP/1.1" 409 Conflict
events-1    | INFO:     172.18.0.6:42472 - "GET /events HTTP/1.1" 200 OK
events-1    | INFO:     172.18.0.6:42472 - "GET /events HTTP/1.1" 200 OK
events-1    | INFO:     172.18.0.6:42472 - "GET /events HTTP/1.1" 200 OK
events-1    | INFO:     172.18.0.6:42472 - "GET /events HTTP/1.1" 200 OK
events-1    | INFO:     172.18.0.6:42472 - "GET /events HTTP/1.1" 200 OK
events-1    | INFO:     172.18.0.6:42472 - "GET /events HTTP/1.1" 200 OK
events-1    | INFO:     172.18.0.6:42472 - "GET /events HTTP/1.1" 200 OK
events-1    | INFO:     172.18.0.6:42472 - "GET /events HTTP/1.1" 200 OK
events-1    | INFO:     172.18.0.6:42472 - "GET /events HTTP/1.1" 200 OK
gateway-1   | {"time":"2026-09-13 12:21:01,093","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve "HTTP/1.1 200 OK""}
gateway-1   | INFO:     192.168.65.1:65246 - "POST /events/1/reserve HTTP/1.1" 200 OK
events-1    | {"time":"2026-09-13 12:21:01,091","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 6d4c1d7a-e944-4272-93bc-7ff1a37ab362"}
events-1    | INFO:     172.18.0.6:42472 - "POST /events/1/reserve HTTP/1.1" 200 OK
events-1    | INFO:     172.18.0.6:42472 - "GET /events HTTP/1.1" 200 OK
events-1    | INFO:     172.18.0.6:42472 - "GET /events HTTP/1.1" 200 OK
events-1    | INFO:     172.18.0.6:42472 - "GET /events HTTP/1.1" 200 OK
events-1    | INFO:     172.18.0.6:42472 - "POST /events/2/reserve HTTP/1.1" 409 Conflict
events-1    | INFO:     172.18.0.6:42472 - "GET /events HTTP/1.1" 200 OK
events-1    | INFO:     172.18.0.6:42472 - "GET /events HTTP/1.1" 200 OK
events-1    | INFO:     172.18.0.6:45392 - "GET /health HTTP/1.1" 200 OK
events-1    | INFO:     172.18.0.6:58342 - "GET /health HTTP/1.1" 200 OK
gateway-1   | {"time":"2026-09-13 12:21:01,250","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1   | INFO:     192.168.65.1:61260 - "GET /events HTTP/1.1" 200 OK
gateway-1   | {"time":"2026-09-13 12:21:01,407","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1   | INFO:     192.168.65.1:26905 - "GET /events HTTP/1.1" 200 OK
gateway-1   | {"time":"2026-09-13 12:21:01,554","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1   | INFO:     192.168.65.1:38804 - "GET /events HTTP/1.1" 200 OK
gateway-1   | {"time":"2026-09-13 12:21:01,689","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/2/reserve "HTTP/1.1 409 Conflict""}
gateway-1   | INFO:     192.168.65.1:31920 - "POST /events/2/reserve HTTP/1.1" 409 Conflict
gateway-1   | {"time":"2026-09-13 12:21:01,835","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1   | INFO:     192.168.65.1:27356 - "GET /events HTTP/1.1" 200 OK
gateway-1   | {"time":"2026-09-13 12:21:01,984","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1   | INFO:     192.168.65.1:47146 - "GET /events HTTP/1.1" 200 OK
gateway-1   | {"time":"2026-09-13 12:21:47,500","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/health "HTTP/1.1 200 OK""}
gateway-1   | {"time":"2026-09-13 12:21:47,503","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://payments:8082/health "HTTP/1.1 200 OK""}
gateway-1   | INFO:     192.168.65.1:62915 - "GET /health HTTP/1.1" 200 OK
```

```bash
docker stats --no-stream
```

```text
CONTAINER ID   NAME             CPU %     MEM USAGE / LIMIT     MEM %     NET I/O           BLOCK I/O     PIDS
1f800e137dc6   app-payments-1   1.59%     36.28MiB / 7.748GiB   0.46%     2.83kB / 1.93kB   0B / 979kB    2
876034d1aa10   app-gateway-1    1.81%     39.76MiB / 7.748GiB   0.50%     543kB / 540kB     0B / 1MB      2
fbc2c8b3d6e0   app-events-1     0.62%     44.75MiB / 7.748GiB   0.56%     820kB / 1.09MB    0B / 0B       2
85237e923ba4   app-postgres-1   0.01%     25.16MiB / 7.748GiB   0.32%     448kB / 516kB     0B / 598kB    8
2c50e2e52581   app-redis-1      2.71%     10.29MiB / 7.748GiB   0.13%     110kB / 46.8kB    0B / 20.5kB   6
```

This is a single resource snapshot, not a load benchmark. The fresh traffic and gateway → events log correlation are recorded in the bonus section below, including listing, reservation, payment, and confirmation.

### 5. Compose network

```bash
docker network inspect app_default --format '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{println}}{{end}}'
```

```text
app-payments-1: 172.18.0.3/16
app-redis-1: 172.18.0.2/16
app-postgres-1: 172.18.0.4/16
app-gateway-1: 172.18.0.6/16
app-events-1: 172.18.0.5/16
```

All five containers share the Compose bridge network. IP addresses are runtime assignments and may change after recreation; service names provide stable discovery.

## Task 2 — Dockerfile Optimization

### 1. Exclude unnecessary build-context files

Created the following file in each of `app/gateway/`, `app/events/`, and `app/payments/`:

```text
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

Rebuilt all three images with `docker compose -f app/docker-compose.yaml build --no-cache`, before changing the Dockerfiles to isolate the `.dockerignore` step.

```bash
docker images --filter reference='app-*'
```

```text
IMAGE                 ID             DISK USAGE   CONTENT SIZE   EXTRA
app-events:latest     28e4f47f1366        272MB           61MB
app-gateway:latest    3dc9c408fcdf        251MB         55.7MB
app-payments:latest   7b49dddfacd9        249MB         55.2MB
```

| Image | Before: disk / content | After .dockerignore: disk / content |
|---|---|---|
| app-events | 272 MB / 61 MB | 272 MB / 61 MB |
| app-gateway | 251 MB / 55.7 MB | 251 MB / 55.7 MB |
| app-payments | 249 MB / 55.2 MB | 249 MB / 55.2 MB |

There is **no visible size reduction at the CLI's displayed precision**. Each service has its own small build context, and the Dockerfiles explicitly copy only `requirements.txt` and `main.py`. Ignoring unrelated files therefore mainly protects the build context and future changes; it does not remove dependencies or base layers. A no-cache rebuild can also produce different image IDs and small byte differences without changing source behavior.

### 2. Run as a non-root user

Added a system group/user and `USER app` before `CMD` in all three Dockerfiles:

```bash
git diff -- app/gateway/Dockerfile app/events/Dockerfile app/payments/Dockerfile
```

```diff
diff --git a/app/events/Dockerfile b/app/events/Dockerfile
index c45a68c..3c837e5 100644
--- a/app/events/Dockerfile
+++ b/app/events/Dockerfile
@@ -6,4 +6,7 @@ RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .

 EXPOSE 8081
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
+
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]
diff --git a/app/gateway/Dockerfile b/app/gateway/Dockerfile
index 68ef075..f4e4173 100644
--- a/app/gateway/Dockerfile
+++ b/app/gateway/Dockerfile
@@ -6,4 +6,7 @@ RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .

 EXPOSE 8080
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
+
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
diff --git a/app/payments/Dockerfile b/app/payments/Dockerfile
index 7f9e7c1..0518909 100644
--- a/app/payments/Dockerfile
+++ b/app/payments/Dockerfile
@@ -6,4 +6,7 @@ RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .

 EXPOSE 8082
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
+
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8082"]
```

Rebuilt and started the application with `docker compose -f app/docker-compose.yaml up -d --build`. The following checks were captured after the subsequent clean-log recreation:

```bash
docker exec app-gateway-1 whoami
docker exec app-events-1 id
docker exec app-payments-1 id
docker exec app-gateway-1 id
```

```text
app
uid=100(app) gid=101(app) groups=101(app)
uid=100(app) gid=101(app) groups=101(app)
uid=100(app) gid=101(app) groups=101(app)
```

All three processes run as UID 100 / GID 101. No recursive ownership change was necessary: application files are readable, and the tested reservation/order flow writes through PostgreSQL and Redis.

```bash
docker images --filter reference='app-*'
```

```text
IMAGE                 ID             DISK USAGE   CONTENT SIZE   EXTRA
app-events:latest     4472dbd53e97        272MB           61MB   U
app-gateway:latest    c1580dc94822        251MB         55.7MB   U
app-payments:latest   f4be0e365444        249MB         55.2MB   U
```

```bash
docker compose -f app/docker-compose.yaml ps
```

```text
NAME             IMAGE                COMMAND                  SERVICE    CREATED          STATUS                    PORTS
app-events-1     app-events           "uvicorn main:app --…"   events     17 seconds ago   Up 11 seconds             0.0.0.0:8081->8081/tcp, [::]:8081->8081/tcp
app-gateway-1    app-gateway          "uvicorn main:app --…"   gateway    17 seconds ago   Up 11 seconds             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-payments-1   app-payments         "uvicorn main:app --…"   payments   17 seconds ago   Up 17 seconds             0.0.0.0:8082->8082/tcp, [::]:8082->8082/tcp
app-postgres-1   postgres:17-alpine   "docker-entrypoint.s…"   postgres   17 seconds ago   Up 17 seconds (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
app-redis-1      redis:7-alpine       "docker-entrypoint.s…"   redis      17 seconds ago   Up 17 seconds (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

## Bonus Task — Trace a Complete Purchase

### 1. Clean logs and execute the flow

```bash
docker compose -f app/docker-compose.yaml down
docker compose -f app/docker-compose.yaml up -d
```

No `-v` option was used: PostgreSQL's named volume was retained. Redis is ephemeral and was recreated. The flow started more than five seconds after application startup.

A Python standard-library client sent the equivalent of the lab's curl requests and recorded UTC boundaries and monotonic elapsed times:

```python
import urllib.request,json,time,datetime
base='http://localhost:3080'
def call(path,data=None):
 start=datetime.datetime.now(datetime.timezone.utc).isoformat(); t=time.perf_counter()
 req=urllib.request.Request(base+path,data=data,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=15) as r: body=r.read().decode(); status=r.status
 print(json.dumps({'path':path,'started_utc':start,'ended_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'elapsed_ms':round((time.perf_counter()-t)*1000,3),'status':status,'body':json.loads(body)}),flush=True)
 return json.loads(body)
call('/health'); call('/events')
r=call('/events/1/reserve',b'{"quantity":1}')
call('/reserve/'+r['reservation_id']+'/pay',b'')
```

```bash
python3 /tmp/lab2-evidence/flow.py
```

```jsonl
{"path": "/health", "started_utc": "2026-09-13T12:38:50.943023+00:00", "ended_utc": "2026-09-13T12:38:51.045302+00:00", "elapsed_ms": 102.083, "status": 200, "body": {"status": "healthy", "checks": {"events": "ok", "payments": "ok", "circuit_payments": "CLOSED"}}}
{"path": "/events", "started_utc": "2026-09-13T12:38:51.045430+00:00", "ended_utc": "2026-09-13T12:38:51.056741+00:00", "elapsed_ms": 11.321, "status": 200, "body": [{"id": 1, "name": "Go Conference 2026", "venue": "Main Hall A", "date": "2026-09-15T09:00:00+00:00", "total_tickets": 100, "price_cents": 5000, "available": 83}, {"id": 4, "name": "Python Workshop", "venue": "Lab 301", "date": "2026-09-22T14:00:00+00:00", "total_tickets": 25, "price_cents": 2000, "available": 20}, {"id": 2, "name": "SRE Meetup", "venue": "Room 204", "date": "2026-10-01T18:00:00+00:00", "total_tickets": 30, "price_cents": 0, "available": 24}, {"id": 5, "name": "Kubernetes Deep Dive", "venue": "Auditorium B", "date": "2026-10-10T10:00:00+00:00", "total_tickets": 80, "price_cents": 8000, "available": 71}, {"id": 3, "name": "Cloud Native Summit", "venue": "Expo Center", "date": "2026-11-20T10:00:00+00:00", "total_tickets": 500, "price_cents": 15000, "available": 492}]}
{"path": "/events/1/reserve", "started_utc": "2026-09-13T12:38:51.056857+00:00", "ended_utc": "2026-09-13T12:38:51.065190+00:00", "elapsed_ms": 8.341, "status": 200, "body": {"reservation_id": "432948e2-d843-461c-9b4e-e34fd7ca5626", "event_id": 1, "quantity": 1, "total_cents": 5000, "expires_in_seconds": 300}}
{"path": "/reserve/432948e2-d843-461c-9b4e-e34fd7ca5626/pay", "started_utc": "2026-09-13T12:38:51.065279+00:00", "ended_utc": "2026-09-13T12:38:51.074609+00:00", "elapsed_ms": 9.338, "status": 200, "body": {"order_id": "432948e2-d843-461c-9b4e-e34fd7ca5626", "event_id": 1, "quantity": 1, "total_cents": 5000, "status": "confirmed"}}
```

All four requests returned HTTP 200. Reservation `432948e2-d843-461c-9b4e-e34fd7ca5626` became a confirmed order with the same ID. The payment reference was `PAY-CFCA3536`.

### 2. Full timestamped logs

```bash
docker compose -f app/docker-compose.yaml logs --timestamps
```

```text
payments-1  | 2026-09-13T12:38:29.880328211Z INFO:     Started server process [1]
payments-1  | 2026-09-13T12:38:29.880367961Z INFO:     Waiting for application startup.
payments-1  | 2026-09-13T12:38:29.880392044Z INFO:     Application startup complete.
payments-1  | 2026-09-13T12:38:29.880549877Z INFO:     Uvicorn running on http://0.0.0.0:8082 (Press CTRL+C to quit)
payments-1  | 2026-09-13T12:38:51.042803429Z INFO:     172.18.0.6:51058 - "GET /health HTTP/1.1" 200 OK
payments-1  | 2026-09-13T12:38:51.067929679Z {"time":"2026-09-13 12:38:51,067","level":"INFO","service":"payments","msg":"Payment success: PAY-CFCA3536 for 432948e2-d843-461c-9b4e-e34fd7ca5626"}
payments-1  | 2026-09-13T12:38:51.068204971Z INFO:     172.18.0.6:51058 - "POST /charge HTTP/1.1" 200 OK
gateway-1   | 2026-09-13T12:38:35.663473505Z INFO:     Started server process [1]
gateway-1   | 2026-09-13T12:38:35.663513588Z INFO:     Waiting for application startup.
gateway-1   | 2026-09-13T12:38:35.663515922Z INFO:     Application startup complete.
gateway-1   | 2026-09-13T12:38:35.663647505Z INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
gateway-1   | 2026-09-13T12:38:51.033058346Z {"time":"2026-09-13 12:38:51,032","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/health "HTTP/1.1 200 OK""}
gateway-1   | 2026-09-13T12:38:51.043265554Z {"time":"2026-09-13 12:38:51,043","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://payments:8082/health "HTTP/1.1 200 OK""}
gateway-1   | 2026-09-13T12:38:51.043814512Z INFO:     192.168.65.1:59571 - "GET /health HTTP/1.1" 200 OK
gateway-1   | 2026-09-13T12:38:51.053316929Z {"time":"2026-09-13 12:38:51,053","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1   | 2026-09-13T12:38:51.055315762Z INFO:     192.168.65.1:19209 - "GET /events HTTP/1.1" 200 OK
gateway-1   | 2026-09-13T12:38:51.063106137Z {"time":"2026-09-13 12:38:51,062","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve "HTTP/1.1 200 OK""}
gateway-1   | 2026-09-13T12:38:51.063764762Z INFO:     192.168.65.1:64473 - "POST /events/1/reserve HTTP/1.1" 200 OK
gateway-1   | 2026-09-13T12:38:51.068507846Z {"time":"2026-09-13 12:38:51,068","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge "HTTP/1.1 200 OK""}
gateway-1   | 2026-09-13T12:38:51.072691304Z {"time":"2026-09-13 12:38:51,072","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/reservations/432948e2-d843-461c-9b4e-e34fd7ca5626/confirm "HTTP/1.1 200 OK""}
gateway-1   | 2026-09-13T12:38:51.073215887Z INFO:     192.168.65.1:16396 - "POST /reserve/432948e2-d843-461c-9b4e-e34fd7ca5626/pay HTTP/1.1" 200 OK
postgres-1  | 2026-09-13T12:38:29.713921919Z
postgres-1  | 2026-09-13T12:38:29.713949086Z PostgreSQL Database directory appears to contain a database; Skipping initialization
postgres-1  | 2026-09-13T12:38:29.713950836Z
postgres-1  | 2026-09-13T12:38:29.727840627Z 2026-09-13 12:38:29.727 UTC [1] LOG:  starting PostgreSQL 17.11 on aarch64-unknown-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit
postgres-1  | 2026-09-13T12:38:29.727853752Z 2026-09-13 12:38:29.727 UTC [1] LOG:  listening on IPv4 address "0.0.0.0", port 5432
postgres-1  | 2026-09-13T12:38:29.727855252Z 2026-09-13 12:38:29.727 UTC [1] LOG:  listening on IPv6 address "::", port 5432
postgres-1  | 2026-09-13T12:38:29.728948419Z 2026-09-13 12:38:29.728 UTC [1] LOG:  listening on Unix socket "/var/run/postgresql/.s.PGSQL.5432"
postgres-1  | 2026-09-13T12:38:29.730568794Z 2026-09-13 12:38:29.730 UTC [29] LOG:  database system was shut down at 2026-09-13 12:38:28 UTC
postgres-1  | 2026-09-13T12:38:29.733215586Z 2026-09-13 12:38:29.733 UTC [1] LOG:  database system is ready to accept connections
redis-1     | 2026-09-13T12:38:29.547573335Z 1:C 13 Sep 2026 12:38:29.547 * oO0OoO0OoO0Oo Redis is starting oO0OoO0OoO0Oo
events-1    | 2026-09-13T12:38:35.606363422Z INFO:     Started server process [1]
events-1    | 2026-09-13T12:38:35.606395838Z INFO:     Waiting for application startup.
events-1    | 2026-09-13T12:38:35.617246297Z {"time":"2026-09-13 12:38:35,617","level":"INFO","service":"events","msg":"DB pool created (max=10)"}
events-1    | 2026-09-13T12:38:35.619647797Z {"time":"2026-09-13 12:38:35,619","level":"INFO","service":"events","msg":"Redis connected"}
events-1    | 2026-09-13T12:38:35.619664755Z INFO:     Application startup complete.
events-1    | 2026-09-13T12:38:35.619834713Z INFO:     Uvicorn running on http://0.0.0.0:8081 (Press CTRL+C to quit)
events-1    | 2026-09-13T12:38:51.032412637Z INFO:     172.18.0.6:51042 - "GET /health HTTP/1.1" 200 OK
events-1    | 2026-09-13T12:38:51.052116554Z INFO:     172.18.0.6:51042 - "GET /events HTTP/1.1" 200 OK
events-1    | 2026-09-13T12:38:51.062299512Z {"time":"2026-09-13 12:38:51,062","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 432948e2-d843-461c-9b4e-e34fd7ca5626"}
events-1    | 2026-09-13T12:38:51.062716054Z INFO:     172.18.0.6:51042 - "POST /events/1/reserve HTTP/1.1" 200 OK
events-1    | 2026-09-13T12:38:51.071952762Z {"time":"2026-09-13 12:38:51,071","level":"INFO","service":"events","msg":"Order confirmed: 432948e2-d843-461c-9b4e-e34fd7ca5626"}
events-1    | 2026-09-13T12:38:51.072266846Z INFO:     172.18.0.6:51042 - "POST /reservations/432948e2-d843-461c-9b4e-e34fd7ca5626/confirm HTTP/1.1" 200 OK
redis-1     | 2026-09-13T12:38:29.547819877Z 1:C 13 Sep 2026 12:38:29.547 * Redis version=7.4.11, bits=64, commit=00000000, modified=0, pid=1, just started
redis-1     | 2026-09-13T12:38:29.547851419Z 1:C 13 Sep 2026 12:38:29.547 # Warning: no config file specified, using the default config. In order to specify a config file use redis-server /path/to/redis.conf
redis-1     | 2026-09-13T12:38:29.548144627Z 1:M 13 Sep 2026 12:38:29.547 * monotonic clock: POSIX clock_gettime
redis-1     | 2026-09-13T12:38:29.548861085Z 1:M 13 Sep 2026 12:38:29.548 * Running mode=standalone, port=6379.
redis-1     | 2026-09-13T12:38:29.549020544Z 1:M 13 Sep 2026 12:38:29.548 * Server initialized
redis-1     | 2026-09-13T12:38:29.549023919Z 1:M 13 Sep 2026 12:38:29.548 * Ready to accept connections tcp
```

### 3. Chronological annotation

| UTC time (12:38:51 + fraction) | Service and action | Since previous log |
|---|---|---|
| .062299512 | Events creates reservation | — |
| .062716054 | Events logs successful reserve response | 0.417 ms |
| .063106137 | Gateway receives events reserve response | 0.390 ms |
| .063764762 | Gateway logs client reserve response | 0.659 ms |
| .067929679 | Payments records successful charge | 4.165 ms |
| .068204971 | Payments logs charge response | 0.275 ms |
| .068507846 | Gateway receives payments response | 0.303 ms |
| .071952762 | Events confirms order | 3.445 ms |
| .072266846 | Events logs confirm response | 0.314 ms |
| .072691304 | Gateway receives events confirm response | 0.424 ms |
| .073215887 | Gateway logs client pay response | 0.525 ms |

The reservation ID links the events reservation, payments charge, events confirmation, and gateway payment URL. Matching endpoint paths and timestamps links the gateway's reserve call to events. These timestamp gaps describe observed log emission intervals, not pure network latency.

**Timing answer:** the client measured **8.341 ms** for reservation and **9.338 ms** for payment including confirmation. The complete two-request purchase occupied **17.752 ms** of wall-clock time (12:38:51.056857–12:38:51.074609 UTC), including the short gap between requests. The observable log interval from events creating the reservation to gateway logging the final pay response was **10.916375 ms** (12:38:51.062299512–12:38:51.073215887).

An exact duration from **gateway receiving** the request to returning its response cannot be calculated from these logs: Uvicorn access logs record responses, and there is no gateway request-entry timestamp. Calling the first response log an arrival timestamp would underestimate the duration. The measured client durations provide end-to-end timing including host/network overhead; the log span is only the observed internal portion.

### 4. Addresses after recreation

```bash
docker inspect app-gateway-1 app-events-1 app-payments-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
```

```text
/app-gateway-1 172.18.0.6
/app-events-1 172.18.0.5
/app-payments-1 172.18.0.3
```

Final `socket.gethostbyname("events")` from gateway returned `172.18.0.5`. Successful HTTP calls and the confirmed purchase verify service discovery after recreation as well as non-root operation.

## Completion Checklist

- [x] Task 1: images, layers, container configuration, exec, logs, resource snapshot, networking, and DNS explanation.
- [x] Task 2: three `.dockerignore` files, size comparison, three non-root Dockerfiles, runtime identity checks, and Dockerfile diff.
- [x] Bonus: successful purchase, full timestamped logs, chronological annotations, measured client timing, and explicit limits of log-only timing.
- [x] Publish a `feature/lab2` PR and submit its URL through Moodle.
