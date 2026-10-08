# Lab 2 — Containerization: Inspect, Understand, Optimize

**Student:** Rolan  
**Branch:** `feature/lab2`  
**Date:** 2026-06-07

---

## Task 1 — Docker Inspection & Operations

### 2.1 — Image sizes

```
MacBook-Pro-pon4ik:app rolanmulukin$ docker images | grep app
app-gateway                 latest      969692cf8f6e   5 hours ago   238MB
app-events                  latest      5189be6a4c03   5 hours ago   259MB
app-payments                latest      531a134e6694   5 hours ago   236MB
```

`events` is the largest at 259MB. Inspecting the gateway image layers:

```
MacBook-Pro-pon4ik:app rolanmulukin$ docker history app-gateway --no-trunc --format "table {{.CreatedBy}}\t{{.Size}}"
CREATED BY                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      SIZE
CMD ["uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8080"]                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   0B
EXPOSE map[8080/tcp:{}]                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         0B
COPY main.py . # buildkit                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       24.6kB
RUN /bin/sh -c pip install --no-cache-dir -r requirements.txt # buildkit                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        28.4MB
COPY requirements.txt . # buildkit                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              12.3kB
WORKDIR /app                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    8.19kB
CMD ["python3"]                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 0B
RUN /bin/sh -c set -eux;  for src in idle3 pip3 pydoc3 python3 python3-config; do   dst="$(echo "$src" | tr -d 3)";   [ -s "/usr/local/bin/$src" ];   [ ! -e "/usr/local/bin/$dst" ];   ln -svT "$src" "/usr/local/bin/$dst";  done # buildkit                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  16.4kB
RUN /bin/sh -c set -eux;   savedAptMark="$(apt-mark showmanual)";  apt-get update;  apt-get install -y --no-install-recommends   dpkg-dev   gcc   gnupg   libbluetooth-dev   libbz2-dev   libc6-dev   libdb-dev   libffi-dev   libgdbm-dev   liblzma-dev   libncursesw5-dev   libreadline-dev   libsqlite3-dev   libssl-dev   make   tk-dev   uuid-dev   wget   xz-utils   zlib1g-dev  ;   wget -O python.tar.xz "https://www.python.org/ftp/python/${PYTHON_VERSION%%[a-z]*}/Python-$PYTHON_VERSION.tar.xz";  echo "$PYTHON_SHA256 *python.tar.xz" | sha256sum -c -;  wget -O python.tar.xz.asc "https://www.python.org/ftp/python/${PYTHON_VERSION%%[a-z]*}/Python-$PYTHON_VERSION.tar.xz.asc";  GNUPGHOME="$(mktemp -d)"; export GNUPGHOME;  gpg --batch --keyserver hkps://keys.openpgp.org --recv-keys "$GPG_KEY";  gpg --batch --verify python.tar.xz.asc python.tar.xz;  gpgconf --kill all;  rm -rf "$GNUPGHOME" python.tar.xz.asc;  mkdir -p /usr/src/python;  tar --extract --directory /usr/src/python --strip-components=1 --file python.tar.xz;  rm python.tar.xz;   cd /usr/src/python;  gnuArch="$(dpkg-architecture --query DEB_BUILD_GNU_TYPE)";  ./configure   --build="$gnuArch"   --enable-loadable-sqlite-extensions   --enable-optimizations   --enable-option-checking=fatal   --enable-shared   $(test "${gnuArch%%-*}" != 'riscv64' && echo '--with-lto')   --with-ensurepip  ;  nproc="$(nproc)";  EXTRA_CFLAGS="$(dpkg-buildflags --get CFLAGS)";  LDFLAGS="$(dpkg-buildflags --get LDFLAGS)";  LDFLAGS="${LDFLAGS:-} -Wl,--strip-all";  arch="$(dpkg --print-architecture)"; arch="${arch##*-}";  case "$arch" in   amd64|arm64)    EXTRA_CFLAGS="${EXTRA_CFLAGS:-} -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer";    ;;   i386)    ;;   *)    EXTRA_CFLAGS="${EXTRA_CFLAGS:-} -fno-omit-frame-pointer";    ;;  esac;  make -j "$nproc"   "EXTRA_CFLAGS=${EXTRA_CFLAGS:-}"   "LDFLAGS=${LDFLAGS:-}"  ;  rm python;  make -j "$nproc"   "EXTRA_CFLAGS=${EXTRA_CFLAGS:-}"   "LDFLAGS=${LDFLAGS:-} -Wl,-rpath='\$\$ORIGIN/../lib'"   python  ;  make install;   cd /;  rm -rf /usr/src/python;   find /usr/local -depth   \(    \( -type d -a \( -name test -o -name tests -o -name idle_test \) \)    -o \( -type f -a \( -name '*.pyc' -o -name '*.pyo' -o -name 'libpython*.a' \) \)   \) -exec rm -rf '{}' +  ;   ldconfig;   apt-mark auto '.*' > /dev/null;  apt-mark manual $savedAptMark;  find /usr/local -type f -executable -not \( -name '*tkinter*' \) -exec ldd '{}' ';'   | awk '/=>/ { so = $(NF-1); if (index(so, "/usr/local/") == 1) { next }; gsub("^/(usr/)?", "", so); printf "*%s\n", so }'   | sort -u   | xargs -rt dpkg-query --search   | awk 'sub(":$", "", $1) { print $1 }'   | sort -u   | xargs -r apt-mark manual  ;  apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false;  apt-get dist-clean;   export PYTHONDONTWRITEBYTECODE=1;  python3 --version;  pip3 --version # buildkit   43.4MB
ENV PYTHON_SHA256=2ab91ff401783ccca64f75d10c882e957bdfd60e2bf5a72f8421793729b78a71                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              0B
ENV PYTHON_VERSION=3.13.13                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      0B
ENV GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            0B
RUN /bin/sh -c set -eux;  apt-get update;  apt-get install -y --no-install-recommends   ca-certificates   netbase   tzdata  ;  apt-get dist-clean # buildkit                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    4.99MB
ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            0B
# debian.sh --arch 'arm64' out/ 'trixie' '@1779062400'                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          109MB
MacBook-Pro-pon4ik:app rolanmulukin$                                            69.2MB
```

The gateway image has 10 layers total. The largest **our** layer is `RUN pip install` at 28.4MB — FastAPI, uvicorn, httpx, prometheus-client all land here. But the actually biggest layer in the whole image is the Debian base at 109MB, followed by Python compilation from source at 43.4MB — both come from the `python:3.13-slim` base image and are outside our control.

### 2.2 — Container IP addresses

```
MacBook-Pro-pon4ik:app rolanmulukin$ docker inspect app-events-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-events-1 172.19.0.5
MacBook-Pro-pon4ik:app rolanmulukin$ docker inspect app-gateway-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-gateway-1 172.19.0.6
MacBook-Pro-pon4ik:app rolanmulukin$ docker inspect app-payments-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-payments-1 172.19.0.3
```

Environment variables of the payments service:

```
MacBook-Pro-pon4ik:app rolanmulukin$ docker inspect app-payments-1 --format '{{range .Config.Env}}{{println .}}{{end}}'
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.13
PYTHON_SHA256=2ab91ff401783ccca64f75d10c882e957bdfd60e2bf5a72f8421793729b78a71
```

### 2.3 — Live debugging with exec

```
MacBook-Pro-pon4ik:app rolanmulukin$ docker exec app-gateway-1 whoami
root

MacBook-Pro-pon4ik:app rolanmulukin$ docker exec app-gateway-1 id
uid=0(root) gid=0(root) groups=0(root)

MacBook-Pro-pon4ik:app rolanmulukin$ docker exec app-gateway-1 cat /etc/resolv.conf
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

Reaching events by service name from inside gateway:

```
MacBook-Pro-pon4ik:app rolanmulukin$ docker exec app-gateway-1 python3 -c "
> import urllib.request
> print(urllib.request.urlopen('http://events:8081/health').read().decode())
> "
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
```

Reaching payments:

```
MacBook-Pro-pon4ik:app rolanmulukin$ docker exec app-gateway-1 python3 -c "
> import urllib.request
> print(urllib.request.urlopen('http://payments:8082/health').read().decode())
> "
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

### 2.4 — Logs analysis

```
MacBook-Pro-pon4ik:app rolanmulukin$ docker compose logs gateway --tail=20
docker compose logs events --tail=20
docker compose logs payments --tail=20
gateway-1  | INFO:     Started server process [1]
gateway-1  | INFO:     Waiting for application startup.
gateway-1  | INFO:     Application startup complete.
gateway-1  | INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
MacBook-Pro-pon4ik:app rolanmulukin$ docker compose logs events --tail=20
events-1  | INFO:     Started server process [1]
events-1  | INFO:     Waiting for application startup.
events-1  | {"time":"2026-06-07 13:41:34,271","level":"INFO","service":"events","msg":"DB pool created (max=10)"}
events-1  | {"time":"2026-06-07 13:41:34,272","level":"INFO","service":"events","msg":"Redis connected"}
events-1  | INFO:     Application startup complete.
events-1  | INFO:     Uvicorn running on http://0.0.0.0:8081 (Press CTRL+C to quit)
events-1  | INFO:     172.19.0.6:33154 - "GET /health HTTP/1.1" 200 OK
MacBook-Pro-pon4ik:app rolanmulukin$ docker compose logs payments --tail=20
payments-1  | INFO:     Started server process [1]
payments-1  | INFO:     Waiting for application startup.
payments-1  | INFO:     Application startup complete.
payments-1  | INFO:     Uvicorn running on http://0.0.0.0:8082 (Press CTRL+C to quit)
payments-1  | INFO:     172.19.0.6:41230 - "GET /health HTTP/1.1" 200 OK
```

After generating traffic:

```
MacBook-Pro-pon4ik:app rolanmulukin$ curl -s http://localhost:3080/events > /dev/null
MacBook-Pro-pon4ik:app rolanmulukin$ curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity":1}'
{"reservation_id":"3dd3796d-1a19-4618-bb17-35bbcd84d419","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}MacBook-Pro-pon4ik:app rolanmulukin$ docker compose logs gateway --tail=5
gateway-1  | INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
gateway-1  | {"time":"2026-06-07 14:34:00,095","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | INFO:     192.168.65.1:30934 - "GET /events HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-06-07 14:34:00,128","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve "HTTP/1.1 200 OK""}
gateway-1  | INFO:     192.168.65.1:48209 - "POST /events/1/reserve HTTP/1.1" 200 OK
MacBook-Pro-pon4ik:app rolanmulukin$ docker compose logs events --tail=5
events-1  | INFO:     Uvicorn running on http://0.0.0.0:8081 (Press CTRL+C to quit)
events-1  | INFO:     172.19.0.6:33154 - "GET /health HTTP/1.1" 200 OK
events-1  | INFO:     172.19.0.6:55602 - "GET /events HTTP/1.1" 200 OK
events-1  | {"time":"2026-06-07 14:34:00,127","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 3dd3796d-1a19-4618-bb17-35bbcd84d419"}
events-1  | INFO:     172.19.0.6:55602 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

The gap between gateway (`14:34:00,095` → forwarded to events) and events (`14:34:00,127` → reserved) is ~32ms — time for the HTTP hop plus postgres availability check inside events.

### 2.5 — Network inspection

```
MacBook-Pro-pon4ik:app rolanmulukin$ docker network ls | grep app
8e93cfb753de   app_default           bridge    local
MacBook-Pro-pon4ik:app rolanmulukin$ docker network inspect app_default --format '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}'
app-gateway-1: 172.19.0.6/16
app-payments-1: 172.19.0.3/16
app-events-1: 172.19.0.5/16
app-redis-1: 172.19.0.4/16
app-postgres-1: 172.19.0.2/16
```

**How does gateway find the events service?**

Gateway uses the service name `events` directly in the URL (`http://events:8081`). Docker Compose automatically creates a shared bridge network (`app_default`) and runs an embedded DNS server at `127.0.0.11` inside each container. When gateway resolves `events`, the DNS server returns the container's current IP (172.19.0.5). This means services don't need to know each other's IPs — they just use the service name defined in `docker-compose.yaml`.

---

## Task 2 — Dockerfile Optimization

### 2.7 — .dockerignore

Added `.dockerignore` to `app/gateway/` (shown in diff below). Same file content used for `app/events/` and `app/payments/`:

```
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

Image sizes before and after rebuild:

```
# Before
MacBook-Pro-pon4ik:app rolanmulukin$ docker images | grep app
app-gateway                 latest      15e7d7dc0253   6 hours ago   238MB
app-events                  latest      09dfaa3d7aae   6 hours ago   259MB
app-payments                latest      01953fbe8664   6 hours ago   236MB

# After (docker compose build --no-cache)
MacBook-Pro-pon4ik:app rolanmulukin$ docker images | grep app
app-gateway                 latest      51c4322f8bd0   35 seconds ago   238MB
app-events                  latest      aa025136682f   47 seconds ago   259MB
app-payments                latest      86f0ad01f2f6   49 seconds ago   236MB
```

No size difference — the build context for these services contains only Python source files and `requirements.txt`, so there was nothing significant to exclude. The `.dockerignore` is still good practice for when `.git/` or large files appear in the directory.

### 2.8 — Non-root user

Added to each Dockerfile before `CMD`:

```dockerfile
RUN addgroup --system app && adduser --system --ingroup app app
USER app
```

```
MacBook-Pro-pon4ik:app rolanmulukin$ docker compose up -d --build
Compose can now delegate builds to bake for better performance.
 To do so, set COMPOSE_BAKE=true.
[+] Building 1.7s (30/30) FINISHED                                                                                                                              docker:desktop-linux
 => [events internal] load build definition from Dockerfile                                                                                                                     0.0s
 => => transferring dockerfile: 355B                                                                                                                                            0.0s 
 => [payments internal] load build definition from Dockerfile                                                                                                                   0.0s 
 => => transferring dockerfile: 355B                                                                                                                                            0.0s 
 => [gateway internal] load metadata for docker.io/library/python:3.13-slim                                                                                                     1.1s 
 => [payments internal] load .dockerignore                                                                                                                                      0.0s
 => => transferring context: 117B                                                                                                                                               0.0s 
 => [events internal] load .dockerignore                                                                                                                                        0.0s 
 => => transferring context: 117B                                                                                                                                               0.0s 
 => [payments internal] load build context                                                                                                                                      0.0s 
 => => transferring context: 138B                                                                                                                                               0.0s 
 => [events internal] load build context                                                                                                                                        0.0s 
 => => transferring context: 138B                                                                                                                                               0.0s 
 => [gateway 1/6] FROM docker.io/library/python:3.13-slim@sha256:b04b5d7233d2ad9c379e22ea8927cd1378cd15c60d4ef876c065b25ea8fb3bf3                                               0.0s 
 => => resolve docker.io/library/python:3.13-slim@sha256:b04b5d7233d2ad9c379e22ea8927cd1378cd15c60d4ef876c065b25ea8fb3bf3                                                       0.0s 
 => CACHED [gateway 2/6] WORKDIR /app                                                                                                                                           0.0s 
 => CACHED [events 3/6] COPY requirements.txt .                                                                                                                                 0.0s 
 => CACHED [events 4/6] RUN pip install --no-cache-dir -r requirements.txt                                                                                                      0.0s
 => CACHED [events 5/6] COPY main.py .                                                                                                                                          0.0s 
 => [events 6/6] RUN addgroup --system app && adduser --system --ingroup app app                                                                                                0.2s 
 => CACHED [payments 3/6] COPY requirements.txt .                                                                                                                               0.0s 
 => CACHED [payments 4/6] RUN pip install --no-cache-dir -r requirements.txt                                                                                                    0.0s
 => CACHED [payments 5/6] COPY main.py .                                                                                                                                        0.0s 
 => [payments 6/6] RUN addgroup --system app && adduser --system --ingroup app app                                                                                              0.2s 
 => [events] exporting to image                                                                                                                                                 0.1s 
 => => exporting layers                                                                                                                                                         0.0s 
 => => exporting manifest sha256:b1ef810d28f35dd3b15114256571261462e490ba3f36671a1f7f27998361b458                                                                               0.0s 
 => => exporting config sha256:6d134e9509d33f37f9c3cd0eaf63de9b062a3e098097e44921a3780ea31155fa                                                                                 0.0s 
 => => exporting attestation manifest sha256:c6d79658e1daed4def3ee98f6409980f12ec8a8c49842eae8670d4fa544d2bc4                                                                   0.0s 
 => => exporting manifest list sha256:c19748081f8076919e00ab4478eaa8fe3a2fd98552c32b94c2929be2660662d5                                                                          0.0s 
 => => naming to docker.io/library/app-events:latest                                                                                                                            0.0s 
 => => unpacking to docker.io/library/app-events:latest                                                                                                                         0.0s 
 => [payments] exporting to image                                                                                                                                               0.1s 
 => => exporting layers                                                                                                                                                         0.0s 
 => => exporting manifest sha256:2d05e3b2f4a2dfc0be399c5321394c00b0b98b6396e4b382900e898d688ed20b                                                                               0.0s 
 => => exporting config sha256:7acb116095444cb7ee4a63757a70b76238aed9fc29796939ec34a98c225031a6                                                                                 0.0s 
 => => exporting attestation manifest sha256:ca7df7872e4925ca7235e3555996e98f010ce0470b28e405a12a0bdda76c7ec7                                                                   0.0s 
 => => exporting manifest list sha256:fb38393a1d6d451ba1bfd7f26736988136480321c4eb6d15a1da8e993a674918                                                                          0.0s 
 => => naming to docker.io/library/app-payments:latest                                                                                                                          0.0s 
 => => unpacking to docker.io/library/app-payments:latest                                                                                                                       0.0s 
 => [payments] resolving provenance for metadata file                                                                                                                           0.0s 
 => [events] resolving provenance for metadata file                                                                                                                             0.0s 
 => [gateway internal] load build definition from Dockerfile                                                                                                                    0.0s 
 => => transferring dockerfile: 355B                                                                                                                                            0.0s 
 => [gateway internal] load .dockerignore                                                                                                                                       0.0s 
 => => transferring context: 117B                                                                                                                                               0.0s 
 => [gateway internal] load build context                                                                                                                                       0.0s 
 => => transferring context: 138B                                                                                                                                               0.0s 
 => CACHED [gateway 3/6] COPY requirements.txt .                                                                                                                                0.0s 
 => CACHED [gateway 4/6] RUN pip install --no-cache-dir -r requirements.txt                                                                                                     0.0s 
 => CACHED [gateway 5/6] COPY main.py .                                                                                                                                         0.0s 
 => [gateway 6/6] RUN addgroup --system app && adduser --system --ingroup app app                                                                                               0.1s 
 => [gateway] exporting to image                                                                                                                                                0.0s 
 => => exporting layers                                                                                                                                                         0.0s 
 => => exporting manifest sha256:05757c0a223adbf6a9fb2d1b8222fba4ecd693ad2bde162afcac09c3dd46f878                                                                               0.0s 
 => => exporting config sha256:1953c473ce38b4bc74515db3b8f1085c11c0cb72fa1e50d7c2eed556323fd5ab                                                                                 0.0s 
 => => exporting attestation manifest sha256:926f601b53526e98edd8631f06d0b5cfe3e02e9e7546037080662f61983bd11c                                                                   0.0s 
 => => exporting manifest list sha256:247496ac129a80d6797edae03019eb418ae08a36f736b6c7e7780e90574ab0b9                                                                          0.0s 
 => => naming to docker.io/library/app-gateway:latest                                                                                                                           0.0s 
 => => unpacking to docker.io/library/app-gateway:latest                                                                                                                        0.0s 
 => [gateway] resolving provenance for metadata file                                                                                                                            0.0s 
[+] Running 8/8                                                                                                                                                                      
 ✔ events                    Built                                                                                                                                              0.0s 
 ✔ gateway                   Built                                                                                                                                              0.0s 
 ✔ payments                  Built                                                                                                                                              0.0s 
 ✔ Container app-redis-1     Healthy                                                                                                                                            1.5s 
 ✔ Container app-postgres-1  Healthy                                                                                                                                            1.5s 
 ✔ Container app-gateway-1   Started                                                                                                                                            1.6s 
 ✔ Container app-events-1    Started                                                                                                                                            1.2s 
 ✔ Container app-payments-1  Started                                                                                                                                            0.7s 
MacBook-Pro-pon4ik:app rolanmulukin$ docker exec app-gateway-1 whoami
app
```

Dockerfile diff:

```diff
MacBook-Pro-pon4ik:app rolanmulukin$ git diff
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
diff --git a/app/gateway/.dockerignore b/app/gateway/.dockerignore
index e69de29..99ff1a6 100644
--- a/app/gateway/.dockerignore
+++ b/app/gateway/.dockerignore
@@ -0,0 +1,6 @@
+__pycache__
+*.pyc
+.git
+.env
+*.md
+.vscode
\ No newline at end of file
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

## Bonus Task — Request Tracing Across Services

Fresh start to get clean logs:

```
MacBook-Pro-pon4ik:app rolanmulukin$ docker compose down && docker compose up -d
 Container app-postgres-1  Healthy
 Container app-events-1    Started
 Container app-gateway-1   Started
```

Full purchase flow:

```
MacBook-Pro-pon4ik:app rolanmulukin$ RES=$(curl -s -X POST http://localhost:3080/events/1/reserve \
>   -H "Content-Type: application/json" -d '{"quantity":1}')
MacBook-Pro-pon4ik:app rolanmulukin$ RES_ID=$(echo "$RES" | python3 -c "import sys,json; print(json.load(sys.stdin)['reservation_id'])")
MacBook-Pro-pon4ik:app rolanmulukin$ curl -s -X POST "http://localhost:3080/reserve/$RES_ID/pay" | python3 -m json.tool
{
    "order_id": "a3366981-bf66-4ce4-aadf-f2048699ae07",
    "event_id": 1,
    "quantity": 1,
    "total_cents": 5000,
    "status": "confirmed"
}
```

Timestamped logs tracing the request across all 3 services:

```
MacBook-Pro-pon4ik:app rolanmulukin$ docker compose logs --timestamps 2>&1 | grep -E "reserve|Reserve|charge|confirm|POST"

# --- RESERVE ---
events-1  | 2026-06-07T14:51:02.404803Z  Reserved 1 tickets for event 1: a3366981-bf66-4ce4-aadf-f2048699ae07   # [+0ms]   events stores reservation in Redis
events-1  | 2026-06-07T14:51:02.405692Z  POST /events/1/reserve HTTP/1.1" 200 OK                                # [+1ms]   events returns 200 to gateway
gateway-1 | 2026-06-07T14:51:02.406502Z  HTTP Request: POST http://events:8081/events/1/reserve "200 OK"        # [+2ms]   gateway receives response from events
gateway-1 | 2026-06-07T14:51:02.407213Z  POST /events/1/reserve HTTP/1.1" 200 OK                                # [+2ms]   gateway returns reservation_id to client

# --- PAY ---
payments-1 | 2026-06-07T14:51:02.487128Z  POST /charge HTTP/1.1" 200 OK                                         # [+82ms]  payments processes charge
payments-1 | 2026-06-07T14:51:02.487160Z  Payment success: PAY-C6E2A7F0 for a3366981-bf66-4ce4-aadf-f2048699ae07
gateway-1  | 2026-06-07T14:51:02.487298Z  HTTP Request: POST http://payments:8082/charge "200 OK"               # [+82ms]  gateway gets payment_ref back

# --- CONFIRM ---
gateway-1  | 2026-06-07T14:51:02.492185Z  HTTP Request: POST http://events:8081/reservations/.../confirm "200 OK" # [+87ms] gateway calls events to confirm
events-1   | 2026-06-07T14:51:02.491149Z  Order confirmed: a3366981-bf66-4ce4-aadf-f2048699ae07                  # [+86ms]  events inserts into orders table in postgres
gateway-1  | 2026-06-07T14:51:02.493256Z  POST /reserve/.../pay HTTP/1.1" 200 OK                                 # [+88ms]  gateway returns final response to client
```

**Annotated flow:**

| Time | Service | Action |
|------|---------|--------|
| +0ms | events | Reservation stored in Redis, 200 returned |
| +2ms | gateway | reserve response received, returned to client |
| +82ms | payments | `/charge` processed, PAY-C6E2A7F0 issued |
| +82ms | gateway | payment_ref received, calls events to confirm |
| +86ms | events | INSERT into orders table (postgres) |
| +88ms | gateway | 200 returned to client — flow complete |

Total end-to-end time: **~88ms** for the full pay flow (from client sending POST /pay to receiving 200). Reserve alone takes ~2ms. The biggest chunk of latency is the payments call (~80ms) followed by the postgres INSERT on confirm (~4ms).
