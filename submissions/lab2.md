## Task 1 — Docker Inspection & Operations

1. Output of `docker images | grep app` with image sizes
```bash
$ docker images | grep app
app-gateway              latest        3235924fb7a0   5 hours ago     213MB
app-events               latest        4f1f0016e1f8   30 hours ago    233MB
app-payments             latest        02d2c3dd89a6   30 hours ago    211MB
```
2. Output of `docker history` for one image — annotate which layer is pip install

The largest image in this setup is `app-events` (233MB), so I inspected its history.
The command `$ docker history app-events  --no-trunc --format "table {{.CreatedBy}}\t{{.Size}}"` made the result
difficult to read and added extra spaces, so I used the command `$ docker history app-events --no-trunc --format "{{.CreatedBy}} | {{.Size}}"` 
for more readable format:
```bash
$ docker history app-events --no-trunc --format "{{.CreatedBy}} | {{.Size}}"
CMD ["uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8081"] | 0B
EXPOSE map[8081/tcp:{}] | 0B
COPY main.py . # buildkit | 20.5kB
RUN /bin/sh -c pip install --no-cache-dir -r requirements.txt # buildkit | 43.6MB
COPY requirements.txt . # buildkit | 12.3kB
WORKDIR /app | 8.19kB
CMD ["python3"] | 0B
RUN /bin/sh -c set -eux;  for src in idle3 pip3 pydoc3 python3 python3-config; do   dst="$(echo "$src" | tr -d 3)";   [ -s "/usr/local/bin/$src" ];   [ ! -e "/usr/local/bin/$dst" ];   ln -svT "$src" "/usr/local/bin/$dst";  done # buildkit | 16.4kB
RUN /bin/sh -c set -eux;   savedAptMark="$(apt-mark showmanual)";  apt-get update;  apt-get install -y --no-install-recommends   dpkg-dev   gcc   gnupg   libbluetooth-dev   libbz2-
dev   libc6-dev   libdb-dev   libffi-dev   libgdbm-dev   liblzma-dev   libncursesw5-dev   libreadline-dev   libsqlite3-dev   libssl-dev   make   tk-dev   uuid-dev   wget   xz-utils
   zlib1g-dev  ;   wget -O python.tar.xz "https://www.python.org/ftp/python/${PYTHON_VERSION%%[a-z]*}/Python-$PYTHON_VERSION.tar.xz";  echo "$PYTHON_SHA256 *python.tar.xz" | sha256
sum -c -;  wget -O python.tar.xz.asc "https://www.python.org/ftp/python/${PYTHON_VERSION%%[a-z]*}/Python-$PYTHON_VERSION.tar.xz.asc";  GNUPGHOME="$(mktemp -d)"; export GNUPGHOME;  
gpg --batch --keyserver hkps://keys.openpgp.org --recv-keys "$GPG_KEY";  gpg --batch --verify python.tar.xz.asc python.tar.xz;  gpgconf --kill all;  rm -rf "$GNUPGHOME" python.tar.
xz.asc;  mkdir -p /usr/src/python;  tar --extract --directory /usr/src/python --strip-components=1 --file python.tar.xz;  rm python.tar.xz;   cd /usr/src/python;  gnuArch="$(dpkg-a
rchitecture --query DEB_BUILD_GNU_TYPE)";  ./configure   --build="$gnuArch"   --enable-loadable-sqlite-extensions   --enable-optimizations   --enable-option-checking=fatal   --enab
le-shared   $(test "${gnuArch%%-*}" != 'riscv64' && echo '--with-lto')   --with-ensurepip  ;  nproc="$(nproc)";  EXTRA_CFLAGS="$(dpkg-buildflags --get CFLAGS)";  LDFLAGS="$(dpkg-bu
ildflags --get LDFLAGS)";  LDFLAGS="${LDFLAGS:-} -Wl,--strip-all";  arch="$(dpkg --print-architecture)"; arch="${arch##*-}";  case "$arch" in   amd64|arm64)    EXTRA_CFLAGS="${EXTR
A_CFLAGS:-} -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer";    ;;   i386)    ;;   *)    EXTRA_CFLAGS="${EXTRA_CFLAGS:-} -fno-omit-frame-pointer";    ;;  esac;  make -j "$npr
oc"   "EXTRA_CFLAGS=${EXTRA_CFLAGS:-}"   "LDFLAGS=${LDFLAGS:-}"  ;  rm python;  make -j "$nproc"   "EXTRA_CFLAGS=${EXTRA_CFLAGS:-}"   "LDFLAGS=${LDFLAGS:-} -Wl,-rpath='\$\$ORIGIN/.
./lib'"   python  ;  make install;   cd /;  rm -rf /usr/src/python;   find /usr/local -depth   \(    \( -type d -a \( -name test -o -name tests -o -name idle_test \) \)    -o \( -t
ype f -a \( -name '*.pyc' -o -name '*.pyo' -o -name 'libpython*.a' \) \)   \) -exec rm -rf '{}' +  ;   ldconfig;   apt-mark auto '.*' > /dev/null;  apt-mark manual $savedAptMark;  
find /usr/local -type f -executable -not \( -name '*tkinter*' \) -exec ldd '{}' ';'   | awk '/=>/ { so = $(NF-1); if (index(so, "/usr/local/") == 1) { next }; gsub("^/(usr/)?", "",
 so); printf "*%s\n", so }'   | sort -u   | xargs -rt dpkg-query --search   | awk 'sub(":$", "", $1) { print $1 }'   | sort -u   | xargs -r apt-mark manual  ;  apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false;  apt-get dist-clean;   export PYTHONDONTWRITEBYTECODE=1;  python3 --version;  pip3 --version # buildkit | 40.2MB
ENV PYTHON_SHA256=639e43243c620a308f968213df9e00f2f8f62332f7adbaa7a7eeb9783057c690 | 0B
ENV PYTHON_VERSION=3.13.14 | 0B
ENV GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305 | 0B
RUN /bin/sh -c set -eux;  apt-get update;  apt-get install -y --no-install-recommends   ca-certificates   netbase   tzdata  ;  apt-get dist-clean # buildkit | 4.94MB
ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin | 0B
# debian.sh --arch 'amd64' out/ 'trixie' '@1781049600' | 87.4MB
```
The pip install layer is:
```bash
RUN /bin/sh -c pip install --no-cache-dir -r requirements.txt # buildkit | 43.6MB
```

3. IP addresses of all 3 services from `docker inspect`

```bash
$ docker inspect app-events-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-events-1 172.18.0.5

$ docker inspect app-gateway-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-gateway-1 172.18.0.6

$ docker inspect app-payments-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
/app-payments-1 172.18.0.2
```
4. Environment variables of payments service

```bash
$ docker inspect app-payments-1 --format '{{range .Config.Env}}{{println .}}{{end}}'
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.14
PYTHON_SHA256=639e43243c620a308f968213df9e00f2f8f62332f7adbaa7a7eeb9783057c690
```
5. Output of `whoami` and `python3 urllib call to events:8081/health` from inside the gateway container
```bash
$ docker exec app-gateway-1 whoami
root

$ docker exec app-gateway-1 python3 -c "import urllib.request; print(urllib.request.urlopen('http://events:8081/health').read().decode())"
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
```
6. Log snippet showing the same request flowing through gateway → events

```bash
$ curl -s http://localhost:3080/events > /dev/null

$ curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity":1}'
{"reservation_id":"532e2847-526d-428a-b170-8064ff5dd1d5","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}

$ docker compose logs gateway --tail=5
gateway-1  | INFO:     172.18.0.1:35742 - "GET /events HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-06-11 16:21:30,620","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events "HTTP/1.1 200 OK""}
gateway-1  | INFO:     172.18.0.1:52030 - "GET /events HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-06-11 16:21:34,341","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve "HTTP/1.1 200 OK""}
gateway-1  | INFO:     172.18.0.1:52044 - "POST /events/1/reserve HTTP/1.1" 200 OK

$ docker compose logs events --tail=5
events-1  | INFO:     172.18.0.6:57934 - "POST /events/1/reserve HTTP/1.1" 200 OK
events-1  | INFO:     172.18.0.6:40438 - "GET /events HTTP/1.1" 200 OK
events-1  | INFO:     172.18.0.6:55496 - "GET /events HTTP/1.1" 200 OK
events-1  | {"time":"2026-06-11 16:21:34,339","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 532e2847-526d-428a-b170-8064ff5dd1d5"}
events-1  | INFO:     172.18.0.6:55496 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

The relevant request is `"POST /events/1/reserve HTTP/1.1" 200 OK`

7. Network inspect output showing all containers and their IPs
```bash
$ docker network inspect app_default --format '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}'
app-gateway-1: 172.18.0.6/16
app-payments-1: 172.18.0.2/16
app-redis-1: 172.18.0.3/16
app-events-1: 172.18.0.5/16
app-postgres-1: 172.18.0.4/16
```
8. Answer: "How does the gateway find the events service? What IP does `events` resolve to?"

The gateway finds the events service using Docker Compose service discovery. 
Docker's embedded DNS resolves the hostname `events` to the container IP on the `app_default` network.
During testing, `events` resolved to `172.18.0.5`, but the IP can change after container recreation.

## Task 2 — Dockerfile Optimization
1. Image sizes before and after `.dockerignore` 

```bash
$ docker images | grep app
app-gateway              latest        1b802c9f431a   11 hours ago    214MB
app-events               latest        cca60960483a   11 hours ago    233MB
app-payments             latest        281bc75556ab   11 hours ago    212MB
```
After 3 `.dockerignore` files were created and the containers were rebuilt:
```
$ docker compose build --no-cache
Compose can now delegate builds to bake for better performance.
 To do so, set COMPOSE_BAKE=true.
[+] Building 32.3s (27/27) FINISHED                                                                                                                            docker:desktop-linux
 => [events internal] load build definition from Dockerfile                                                                                                                    0.0s
 => => transferring dockerfile: 254B                                                                                                                                           0.0s 
 => [payments internal] load build definition from Dockerfile                                                                                                                  0.0s 
 => => transferring dockerfile: 254B                                                                                                                                           0.0s 
 => [gateway internal] load metadata for docker.io/library/python:3.13-slim                                                                                                    2.0s 
 => [payments internal] load .dockerignore                                                                                                                                     0.0s
 => => transferring context: 85B                                                                                                                                               0.0s 
 => [events internal] load .dockerignore                                                                                                                                       0.0s 
 => => transferring context: 85B                                                                                                                                               0.0s
 => [gateway 1/5] FROM docker.io/library/python:3.13-slim@sha256:f82c96458eedc847b233e582eb31336f4954b39cae020b6dcf5b3ed0e5cbcd74                                              0.1s 
 => => resolve docker.io/library/python:3.13-slim@sha256:f82c96458eedc847b233e582eb31336f4954b39cae020b6dcf5b3ed0e5cbcd74                                                      0.0s 
 => [events internal] load build context                                                                                                                                       0.0s 
 => => transferring context: 64B                                                                                                                                               0.0s 
 => [payments internal] load build context                                                                                                                                     0.0s 
 => => transferring context: 64B                                                                                                                                               0.0s 
 => CACHED [gateway 2/5] WORKDIR /app                                                                                                                                          0.0s 
 => [payments 3/5] COPY requirements.txt .                                                                                                                                     0.1s 
 => [events 3/5] COPY requirements.txt .                                                                                                                                       0.1s 
 => [events 4/5] RUN pip install --no-cache-dir -r requirements.txt                                                                                                           13.8s 
 => [payments 4/5] RUN pip install --no-cache-dir -r requirements.txt                                                                                                          9.5s 
 => [payments 5/5] COPY main.py .                                                                                                                                              0.1s 
 => [payments] exporting to image                                                                                                                                              1.5s 
 => => exporting layers                                                                                                                                                        0.9s 
 => => exporting manifest sha256:12e170974df461e4fde4626939dab7a300a5c561256c198bb2d10fea21edc739                                                                              0.0s 
 => => exporting config sha256:def53fdbfa0558eebc97e4a91d75d1e4389ab10cff722ce0656225200f46d2d2                                                                                0.0s 
 => => exporting attestation manifest sha256:f370a8a70b7c8504eb9c45f49369bc6de08c52fc60839bce604615c363fd5b6d                                                                  0.0s 
 => => exporting manifest list sha256:d97bbef15498f0d013f4a11224c88e7ced673a193f4c74e38c1e80b077813827                                                                         0.0s 
 => => naming to docker.io/library/app-payments:latest                                                                                                                         0.0s 
 => => unpacking to docker.io/library/app-payments:latest                                                                                                                      0.3s 
 => [payments] resolving provenance for metadata file                                                                                                                          0.0s 
 => [events 5/5] COPY main.py .                                                                                                                                                0.1s 
 => [events] exporting to image                                                                                                                                                1.7s 
 => => exporting layers                                                                                                                                                        1.3s 
 => => exporting manifest sha256:6427714e27d195911467bbd149ec7a6a3bd0e13161edf00677851a85a813e5a8                                                                              0.0s 
 => => exporting config sha256:1eea4a62cb21bef2d221fbeb9a1d925cb2e5e5812ebc3c3f4db7e6ec4cda147b                                                                                0.0s 
 => => exporting attestation manifest sha256:88624adf4f4e925b2bb88624afaf30e2f543164bc455f85c2afc90371022cd63                                                                  0.0s 
 => => exporting manifest list sha256:64cbc47d5a9752c08a983cf1ce27aecbc5890bce3960394edc4eae1f2d20645e                                                                         0.0s 
 => => naming to docker.io/library/app-events:latest                                                                                                                           0.0s 
 => => unpacking to docker.io/library/app-events:latest                                                                                                                        0.3s 
 => [events] resolving provenance for metadata file                                                                                                                            0.0s 
 => [gateway internal] load build definition from Dockerfile                                                                                                                   0.0s 
 => => transferring dockerfile: 254B                                                                                                                                           0.0s 
 => [gateway internal] load .dockerignore                                                                                                                                      0.0s 
 => => transferring context: 85B                                                                                                                                               0.0s 
 => [gateway internal] load build context                                                                                                                                      0.0s 
 => => transferring context: 64B                                                                                                                                               0.0s 
 => [gateway 3/5] COPY requirements.txt .                                                                                                                                      0.1s 
 => [gateway 4/5] RUN pip install --no-cache-dir -r requirements.txt                                                                                                          12.5s 
 => [gateway 5/5] COPY main.py .                                                                                                                                               0.0s 
 => [gateway] exporting to image                                                                                                                                               1.4s 
 => => exporting layers                                                                                                                                                        1.0s 
 => => exporting manifest sha256:0a41b6671f838cc3d7b3e53fe6a810e13cdbc78c4d5956b0d60902a36acd215d                                                                              0.0s 
 => => exporting config sha256:be72134f53b9d55097dc5c7e740215822e0eb1b39ce2381b3676cb9b787f7a17                                                                                0.0s 
 => => exporting attestation manifest sha256:603280c05aad5f4abd6623ea60842e11e14940fb317f12dcb7cc97134bbe55a3                                                                  0.0s 
 => => exporting manifest list sha256:b423438b7e479cfdebea2900632b35fa5adf4c78aa05778ef722f4a499e43260                                                                         0.0s 
 => => naming to docker.io/library/app-gateway:latest                                                                                                                          0.0s 
 => => unpacking to docker.io/library/app-gateway:latest                                                                                                                       0.3s 
 => [gateway] resolving provenance for metadata file                                                                                                                           0.0s 
[+] Building 3/3
 ✔ events    Built                                                                                                                                                             0.0s 
 ✔ gateway   Built                                                                                                                                                             0.0s 
 ✔ payments  Built                                                                                                                                                             0.0s 

$ docker images | grep app
app-gateway              latest        b423438b7e47   21 seconds ago   214MB
app-events               latest        64cbc47d5a97   36 seconds ago   233MB
app-payments             latest        d97bbef15498   40 seconds ago   212MB
```

`.dockerignore` did not noticeably change the final image sizes, because each 
Dockerfile already uses a very narrow build context and only copies `requirements.txt` and `main.py`. 
In this setup, `.dockerignore` mainly reduces the files sent to the Docker daemon during build, 
rather than removing anything that would have ended up in the image anyway.
2. The `.dockerignore` content
```bash
__pycache__
*.pyc
.git
.env
*.md
.vscode
```
3. Output of `whoami` inside the container after adding non-root user
```bash
$ docker exec app-gateway-1 whoami
app
```
4. The `git diff` of my Dockerfile changes
```diff
$ git diff -- app/gateway/Dockerfile app/events/Dockerfile app/payments/Dockerfile
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

## Bonus Task — Trace a Request Across Services

1. The full timestamped logs showing one request flowing through all 3 services
```bash
$ docker compose logs --timestamps
payments-1  | 2026-06-12T13:00:10.667683826Z INFO:     Started server process [1] 
redis-1     | 2026-06-12T13:00:10.281808555Z 1:C 12 Jun 2026 13:00:10.281 * oO0OoO0OoO0Oo Redis is starting oO0OoO0OoO0Oo
postgres-1  | 2026-06-12T13:00:10.328901624Z
postgres-1  | 2026-06-12T13:00:10.328946350Z PostgreSQL Database directory appears to contain a database; Skipping initialization
postgres-1  | 2026-06-12T13:00:10.328949793Z
payments-1  | 2026-06-12T13:00:10.667837445Z INFO:     Waiting for application startup.
payments-1  | 2026-06-12T13:00:10.667842288Z INFO:     Application startup complete.
payments-1  | 2026-06-12T13:00:10.668155995Z INFO:     Uvicorn running on http://0.0.0.0:8082 (Press CTRL+C to quit)
payments-1  | 2026-06-12T13:00:19.846876093Z {"time":"2026-06-12 13:00:19,846","level":"INFO","service":"payments","msg":"Payment success: PAY-F839D295 for 5f32bc4e-a8c6-4cdd-9490-d9d49103ca46"}
payments-1  | 2026-06-12T13:00:19.847264159Z INFO:     172.18.0.6:46916 - "POST /charge HTTP/1.1" 200 OK
redis-1     | 2026-06-12T13:00:10.281854920Z 1:C 12 Jun 2026 13:00:10.281 * Redis version=7.4.9, bits=64, commit=00000000, modified=0, pid=1, just started
redis-1     | 2026-06-12T13:00:10.281857608Z 1:C 12 Jun 2026 13:00:10.281 # Warning: no config file specified, using the default config. In order to specify a config file use redis-server /path/to/redis.conf
redis-1     | 2026-06-12T13:00:10.282415348Z 1:M 12 Jun 2026 13:00:10.282 * monotonic clock: POSIX clock_gettime
redis-1     | 2026-06-12T13:00:10.284146976Z 1:M 12 Jun 2026 13:00:10.283 * Running mode=standalone, port=6379.
redis-1     | 2026-06-12T13:00:10.284929429Z 1:M 12 Jun 2026 13:00:10.284 * Server initialized
redis-1     | 2026-06-12T13:00:10.284950984Z 1:M 12 Jun 2026 13:00:10.284 * Ready to accept connections tcp
postgres-1  | 2026-06-12T13:00:10.353598597Z 2026-06-12 13:00:10.353 UTC [1] LOG:  starting PostgreSQL 17.10 on x86_64-pc-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit
postgres-1  | 2026-06-12T13:00:10.353631917Z 2026-06-12 13:00:10.353 UTC [1] LOG:  listening on IPv4 address "0.0.0.0", port 5432
postgres-1  | 2026-06-12T13:00:10.353636681Z 2026-06-12 13:00:10.353 UTC [1] LOG:  listening on IPv6 address "::", port 5432
postgres-1  | 2026-06-12T13:00:10.360064757Z 2026-06-12 13:00:10.359 UTC [1] LOG:  listening on Unix socket "/var/run/postgresql/.s.PGSQL.5432"
postgres-1  | 2026-06-12T13:00:10.366109772Z 2026-06-12 13:00:10.365 UTC [30] LOG:  database system was shut down at 2026-06-12 13:00:08 UTC
postgres-1  | 2026-06-12T13:00:10.373356398Z 2026-06-12 13:00:10.373 UTC [1] LOG:  database system is ready to accept connections
events-1    | 2026-06-12T13:00:16.626045646Z INFO:     Started server process [1]
gateway-1   | 2026-06-12T13:00:16.805375982Z INFO:     Started server process [1]
gateway-1   | 2026-06-12T13:00:16.805416493Z INFO:     Waiting for application startup.
gateway-1   | 2026-06-12T13:00:16.805418228Z INFO:     Application startup complete.
gateway-1   | 2026-06-12T13:00:16.805760154Z INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
gateway-1   | 2026-06-12T13:00:19.354767541Z {"time":"2026-06-12 13:00:19,354","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve "HTTP/1.1 200 OK""}
gateway-1   | 2026-06-12T13:00:19.355630351Z INFO:     172.18.0.1:33654 - "POST /events/1/reserve HTTP/1.1" 200 OK
gateway-1   | 2026-06-12T13:00:19.847971532Z {"time":"2026-06-12 13:00:19,847","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge "HTTP/1.1 200 OK""}
gateway-1   | 2026-06-12T13:00:19.855291819Z {"time":"2026-06-12 13:00:19,855","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/reservations/5f32bc4e-a8c6-4cdd-9490-d9d49103ca46/confirm "HTTP/1.1 200 OK""}
gateway-1   | 2026-06-12T13:00:19.856095403Z INFO:     172.18.0.1:33662 - "POST /reserve/5f32bc4e-a8c6-4cdd-9490-d9d49103ca46/pay HTTP/1.1" 200 OK
events-1    | 2026-06-12T13:00:16.626082540Z INFO:     Waiting for application startup.
events-1    | 2026-06-12T13:00:16.681669590Z {"time":"2026-06-12 13:00:16,681","level":"INFO","service":"events","msg":"DB pool created (max=10)"}
events-1    | 2026-06-12T13:00:16.684951519Z {"time":"2026-06-12 13:00:16,684","level":"INFO","service":"events","msg":"Redis connected"}
events-1    | 2026-06-12T13:00:16.685017319Z INFO:     Application startup complete.
events-1    | 2026-06-12T13:00:16.685204847Z INFO:     Uvicorn running on http://0.0.0.0:8081 (Press CTRL+C to quit)
events-1    | 2026-06-12T13:00:19.353096719Z {"time":"2026-06-12 13:00:19,352","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 5f32bc4e-a8c6-4cdd-9490-d9d49103ca46"}
events-1    | 2026-06-12T13:00:19.353895257Z INFO:     172.18.0.6:57218 - "POST /events/1/reserve HTTP/1.1" 200 OK
events-1    | 2026-06-12T13:00:19.854520741Z {"time":"2026-06-12 13:00:19,854","level":"INFO","service":"events","msg":"Order confirmed: 5f32bc4e-a8c6-4cdd-9490-d9d49103ca46"}     
events-1    | 2026-06-12T13:00:19.854904190Z INFO:     172.18.0.6:57218 - "POST /reservations/5f32bc4e-a8c6-4cdd-9490-d9d49103ca46/confirm HTTP/1.1" 200 OK
```
2. Annotate each line: which service, what it did, how long between hops

```bash
1. payments-1  | 2026-06-12T13:00:10.667683826Z INFO:     Started server process [1] — payments starts. Timing: startup.
2. redis-1     | 2026-06-12T13:00:10.281808555Z 1:C 12 Jun 2026 13:00:10.281 * oO0OoO0OoO0Oo Redis is starting oO0OoO0OoO0Oo — redis starts. Timing: startup.
3. postgres-1  | 2026-06-12T13:00:10.328901624Z — postgres prints an empty startup entry. Timing: startup.
4. postgres-1  | 2026-06-12T13:00:10.328946350Z PostgreSQL Database directory appears to contain a database; Skipping initialization — postgres reuses the existing data directory. Timing: +0.000045 s.
5. postgres-1  | 2026-06-12T13:00:10.328949793Z — postgres prints another empty startup entry. Timing: startup.
6. payments-1  | 2026-06-12T13:00:10.667837445Z INFO:     Waiting for application startup. — payments is still initializing. Timing: +0.000154 s.
7. payments-1  | 2026-06-12T13:00:10.667842288Z INFO:     Application startup complete. — payments finishes startup. Timing: +0.000005 s.
8. payments-1  | 2026-06-12T13:00:10.668155995Z INFO:     Uvicorn running on http://0.0.0.0:8082 (Press CTRL+C to quit) — payments begins serving HTTP. Timing: +0.000314 s.
9. payments-1  | 2026-06-12T13:00:19.846876093Z {"time":"2026-06-12 13:00:19,846","level":"INFO","service":"payments","msg":"Payment success: PAY-F839D295 for 5f32bc4e-a8c6-4cdd-9490-d9d49103ca46"} — payments successfully charges the reservation. Timing: ~9.179 s after startup; this is the payment hop result.
10. payments-1 | 2026-06-12T13:00:19.847264159Z INFO:     172.18.0.6:46916 - "POST /charge HTTP/1.1" 200 OK — payments returns 200 to gateway. Timing: +0.000388 s after line 9.
11. redis-1     | 2026-06-12T13:00:10.281854920Z 1:C 12 Jun 2026 13:00:10.281 * Redis version=7.4.9, bits=64, commit=00000000, modified=0, pid=1, just started — redis reports version. Timing: startup.
12. redis-1     | 2026-06-12T13:00:10.281857608Z 1:C 12 Jun 2026 13:00:10.281 # Warning: no config file specified, using the default config. In order to specify a config file use redis-server /path/to/redis.conf — redis uses default config. Timing: +0.000003 s.
13. redis-1     | 2026-06-12T13:00:10.282415348Z 1:M 12 Jun 2026 13:00:10.282 * monotonic clock: POSIX clock_gettime — redis notes clock source. Timing: +0.000558 s.
14. redis-1     | 2026-06-12T13:00:10.284146976Z 1:M 12 Jun 2026 13:00:10.283 * Running mode=standalone, port=6379. — redis starts in standalone mode. Timing: +0.001732 s.
15. redis-1     | 2026-06-12T13:00:10.284929429Z 1:M 12 Jun 2026 13:00:10.284 * Server initialized — redis finishes init. Timing: +0.000783 s.
16. redis-1     | 2026-06-12T13:00:10.284950984Z 1:M 12 Jun 2026 13:00:10.284 * Ready to accept connections tcp — redis is ready. Timing: +0.000022 s.
17. postgres-1  | 2026-06-12T13:00:10.353598597Z 2026-06-12 13:00:10.353 UTC [1] LOG:  starting PostgreSQL 17.10 on x86_64-pc-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit — postgres starts. Timing: startup.
18. postgres-1  | 2026-06-12T13:00:10.353631917Z 2026-06-12 13:00:10.353 UTC [1] LOG:  listening on IPv4 address "0.0.0.0", port 5432 — postgres listens on IPv4. Timing: +0.000033 s.
19. postgres-1  | 2026-06-12T13:00:10.353636681Z 2026-06-12 13:00:10.353 UTC [1] LOG:  listening on IPv6 address "::", port 5432 — postgres listens on IPv6. Timing: +0.000005 s.
20. postgres-1  | 2026-06-12T13:00:10.360064757Z 2026-06-12 13:00:10.359 UTC [1] LOG:  listening on Unix socket "/var/run/postgresql/.s.PGSQL.5432" — postgres opens its local socket. Timing: +0.006428 s.
21. postgres-1  | 2026-06-12T13:00:10.366109772Z 2026-06-12 13:00:10.365 UTC [30] LOG:  database system was shut down at 2026-06-12 13:00:08 UTC — postgres reports prior shutdown. Timing: +0.006045 s.
22. postgres-1  | 2026-06-12T13:00:10.373356398Z 2026-06-12 13:00:10.373 UTC [1] LOG:  database system is ready to accept connections — postgres is ready. Timing: +0.007247 s.
23. events-1    | 2026-06-12T13:00:16.626045646Z INFO:     Started server process [1] — events starts. Timing: startup.
24. gateway-1   | 2026-06-12T13:00:16.805375982Z INFO:     Started server process [1] — gateway starts. Timing: startup.
25. gateway-1   | 2026-06-12T13:00:16.805416493Z INFO:     Waiting for application startup. — gateway is still initializing. Timing: +0.000040 s.
26. gateway-1   | 2026-06-12T13:00:16.805418228Z INFO:     Application startup complete. — gateway finishes startup. Timing: +0.000002 s.
27. gateway-1   | 2026-06-12T13:00:16.805760154Z INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit) — gateway begins serving HTTP. Timing: +0.000342 s.
28. gateway-1   | 2026-06-12T13:00:19.354767541Z {"time":"2026-06-12 13:00:19,354","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve "HTTP/1.1 200 OK""} — gateway forwards reserve to events. Timing: this hop reaches events and comes back in about 1.7 ms.
29. gateway-1   | 2026-06-12T13:00:19.355630351Z INFO:     172.18.0.1:33654 - "POST /events/1/reserve HTTP/1.1" 200 OK — gateway returns reserve success to the client. Timing: +0.000863 s.
30. gateway-1   | 2026-06-12T13:00:19.847971532Z {"time":"2026-06-12 13:00:19,847","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge "HTTP/1.1 200 OK""} — gateway forwards payment to payments. Timing: about 0.492 s after reserve, which is the slow hop.
31. gateway-1   | 2026-06-12T13:00:19.855291819Z {"time":"2026-06-12 13:00:19,855","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/reservations/5f32bc4e-a8c6-4cdd-9490-d9d49103ca46/confirm "HTTP/1.1 200 OK""} — gateway calls events to confirm after payment. Timing: +0.007320 s.
32. gateway-1   | 2026-06-12T13:00:19.856095403Z INFO:     172.18.0.1:33662 - "POST /reserve/5f32bc4e-a8c6-4cdd-9490-d9d49103ca46/pay HTTP/1.1" 200 OK — gateway returns final success to the client. Timing: +0.000804 s.
33. events-1    | 2026-06-12T13:00:16.626082540Z INFO:     Waiting for application startup. — events is still initializing. Timing: +0.000037 s from line 23.
34. events-1    | 2026-06-12T13:00:16.681669590Z {"time":"2026-06-12 13:00:16,681","level":"INFO","service":"events","msg":"DB pool created (max=10)"} — events connects to PostgreSQL. Timing: +0.055587 s.
35. events-1    | 2026-06-12T13:00:16.684951519Z {"time":"2026-06-12 13:00:16,684","level":"INFO","service":"events","msg":"Redis connected"} — events connects to Redis. Timing: +0.003282 s.
36. events-1    | 2026-06-12T13:00:16.685017319Z INFO:     Application startup complete. — events finishes startup. Timing: +0.000066 s.
37. events-1    | 2026-06-12T13:00:16.685204847Z INFO:     Uvicorn running on http://0.0.0.0:8081 (Press CTRL+C to quit) — events begins serving HTTP. Timing: +0.000188 s.
38. events-1    | 2026-06-12T13:00:19.353096719Z {"time":"2026-06-12 13:00:19,352","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 5f32bc4e-a8c6-4cdd-9490-d9d49103ca46"} — events creates the reservation. Timing: about 2.668 s after startup, matching the reserve request hop.
39. events-1    | 2026-06-12T13:00:19.353895257Z INFO:     172.18.0.6:57218 - "POST /events/1/reserve HTTP/1.1" 200 OK — events returns reserve success. Timing: +0.000799 s.
40. events-1    | 2026-06-12T13:00:19.854520741Z {"time":"2026-06-12 13:00:19,854","level":"INFO","service":"events","msg":"Order confirmed: 5f32bc4e-a8c6-4cdd-9490-d9d49103ca46"} — events confirms the order after payment succeeds. Timing: about 0.5006 s after reserve success, which is the payment hop.
41. events-1    | 2026-06-12T13:00:19.854904190Z INFO:     172.18.0.6:57218 - "POST /reservations/5f32bc4e-a8c6-4cdd-9490-d9d49103ca46/confirm HTTP/1.1" 200 OK — events returns confirm success. Timing: +0.000384 s.
```
3. Answer: "What is the total end-to-end time from gateway receiving the request to returning the response?"

The total end-to-end time for the full purchase flow in your log is about 0.501 s:
* start: 13:00:19.354767541Z on the first gateway hop to events
* end: 13:00:19.856095403Z on the final gateway response

The difference is: 0.501327862 seconds (≈ 501.3 ms)