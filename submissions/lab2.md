## Lab2 Report
### Task 1
1. 
```
user@MacBook-Air SRE-Intro % docker images | grep app
WARNING: This output is designed for human readability. For machine-readable output, please use --format.
app-events:latest                                                                                     1a2c40214eaa        260MB         57.6MB   U    
app-gateway:latest                                                                                    f3b92a071eb8        239MB         52.3MB   U    
app-payments:latest                                                                                   cda4501637ef        236MB         51.8MB   U    
```
2.
```
ser@MacBook-Air SRE-Intro % docker history app-gateway --no-trunc --format "table {{.CreatedBy}}\t{{.Size}}"

CREATED BY                                                                                                                                                                                                        SIZE
CMD ["uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8080"]                                                                                                                                                                                                  0B
EXPOSE [8080/tcp]                                                                                                                                                                                                                                                                                                                                               0B
COPY main.py . # buildkit                                                                                                                                                                                                24.6kB
RUN /bin/sh -c pip install --no-cache-dir -r requirements.txt # buildkit                                                                                                                                                                                                 28.7MB
COPY requirements.txt . # buildkit                                                                                                                                                                                                 12.3kB
WORKDIR /app                                                                                                                                                                                                                                                                                                                                                 8.19kB
CMD ["python3"]                                                                                                                                                                                                                                                                                                                                          0B
RUN /bin/sh -c set -eux;  for src in idle3 pip3 pydoc3 python3 python3-config; do   dst="$(echo "$src" | tr -d 3)";   [ -s "/usr/local/bin/$src" ];   [ ! -e "/usr/local/bin/$dst" ];   ln -svT "$src" "/usr/local/bin/$dst";  done #                                                               16.4kB
RUN /bin/sh -c set -eux;   savedAptMark="$(apt-mark showmanual)";  apt-get update;  apt-get install -y --no-install-recommends   dpkg-dev   gcc   gnupg   libbluetooth-dev   libbz2-dev   libc6-dev   libdb-dev   libffi-dev   libgdbm-dev   liblzma-dev   libncursesw5-dev   libreadline-dev   libsqlite3-dev   libssl-dev   make   tk-dev   uuid-dev   wget   xz-utils   zlib1g-dev  ;   wget -O python.tar.xz "https://www.python.org/ftp/python/${PYTHON_VERSION%%[a-z]*}/Python-$PYTHON_VERSION.tar.xz";  echo "$PYTHON_SHA256 *python.tar.xz" | sha256sum -c -;  wget -O python.tar.xz.asc "https://www.python.org/ftp/python/${PYTHON_VERSION%%[a-z]*}/Python-$PYTHON_VERSION.tar.xz.asc";  GNUPGHOME="$(mktemp -d)"; export GNUPGHOME;  gpg --batch --keyserver hkps://keys.openpgp.org --recv-keys "$GPG_KEY";  gpg --batch --verify python.tar.xz.asc python.tar.xz;  gpgconf --kill all;  rm -rf "$GNUPGHOME" python.tar.xz.asc;  mkdir -p /usr/src/python;  tar --extract --directory /usr/src/python --strip-components=1 --file python.tar.xz;  rm python.tar.xz;   cd /usr/src/python;  gnuArch="$(dpkg-architecture --query DEB_BUILD_GNU_TYPE)";  ./configure   --build="$gnuArch"   --enable-loadable-sqlite-extensions   --enable-optimizations   --enable-option-checking=fatal   --enable-shared   $(test "${gnuArch%%-*}" != 'riscv64' && echo '--with-lto')   --with-ensurepip  ;  nproc="$(nproc)";  EXTRA_CFLAGS="$(dpkg-buildflags --get CFLAGS)";  LDFLAGS="$(dpkg-buildflags --get LDFLAGS)";  LDFLAGS="${LDFLAGS:-} -Wl,--strip-all";  arch="$(dpkg --print-architecture)"; arch="${arch##*-}";  case "$arch" in   amd64|arm64)    EXTRA_CFLAGS="${EXTRA_CFLAGS:-} -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer";    ;;   i386)    ;;   *)    EXTRA_CFLAGS="${EXTRA_CFLAGS:-} -fno-omit-frame-pointer";    ;;  esac;  make -j "$nproc"   "EXTRA_CFLAGS=${EXTRA_CFLAGS:-}"   "LDFLAGS=${LDFLAGS:-}"  ;  rm python;  make -j "$nproc"   "EXTRA_CFLAGS=${EXTRA_CFLAGS:-}"   "LDFLAGS=${LDFLAGS:-} -Wl,-rpath='\$\$ORIGIN/../lib'"   python  ;  make install;   cd /;  rm -rf /usr/src/python;   find /usr/local -depth   \(    \( -type d -a \( -name test -o -name tests -o -name idle_test \) \)    -o \( -type f -a \( -name '*.pyc' -o -name '*.pyo' -o -name 'libpython*.a' \) \)   \) -exec rm -rf '{}' +  ;   ldconfig;   apt-mark auto '.*' > /dev/null;  apt-mark manual $savedAptMark;  find /usr/local -type f -executable -not \( -name '*tkinter*' \) -exec ldd '{}' ';'   | awk '/=>/ { so = $(NF-1); if (index(so, "/usr/local/") == 1) { next }; gsub("^/(usr/)?", "", so); printf "*%s\n", so }'   | sort -u   | xargs -rt dpkg-query --search   | awk 'sub(":$", "", $1) { print $1 }'   | sort -u   | xargs -r apt-mark manual  ;  apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false;  apt-get dist-clean;   export PYTHONDONTWRITEBYTECODE=1;  python3 --version;  pip3 --version # buildkit   43.5MB
ENV PYTHON_SHA256=639e43243c620a308f968213df9e00f2f8f62332f7adbaa7a7eeb9783057c690                                                                                                              0B
ENV PYTHON_VERSION=3.13.14                                                                                                                                                                                                     0B
ENV GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305                                                                                                                                                                                                                                                                                                0B
RUN /bin/sh -c set -eux;  apt-get update;  apt-get install -y --no-install-recommends   ca-certificates   netbase   tzdata  ;  apt-get dist-clean # buildkit                                                                                                                                                                                                                                                                                                                                    4.99MB
ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin                                                                                                                                                                                                                                                                                                                                        0B
# debian.sh --arch 'arm64' out/ 'trixie' '@1781049600'                                                                                                                                                                                 109MB
```
So, the biggest layer is the Debian OS base because every container requires a lightweight operating system environment

3.
```
user@MacBook-Air SRE-Intro % docker inspect app-events-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 

/app-events-1 172.19.0.5
user@MacBook-Air SRE-Intro % docker inspect app-gateway-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'

/app-gateway-1 172.19.0.6
user@MacBook-Air SRE-Intro % docker inspect app-payments-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'

/app-payments-1 172.19.0.2
```
4.
```
user@MacBook-Air SRE-Intro % docker inspect app-payments-1 --format '{{range .Config.Env}}{{println .}}{{end}}'

PAYMENT_LATENCY_MS=0
PAYMENT_FAILURE_RATE=0.0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.14
PYTHON_SHA256=639e43243c620a308f968213df9e00f2f8f62332f7adbaa7a7eeb9783057c690

```
5.
```
user@MacBook-Air SRE-Intro % docker exec app-gateway-1 whoami
root
user@MacBook-Air SRE-Intro % docker exec app-gateway-1 python3 -c "
import urllib.request
print(urllib.request.urlopen('http://payments:8082/health').read().decode())
"
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```
6.
```
gateway-1  | {"time":"2026-06-12 14:28:40,530","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve \"HTTP/1.1 200 OK\""}
gateway-1  | INFO:     192.168.65.1:45465 - "POST /events/1/reserve HTTP/1.1" 200 OK

events-1  | {"time":"2026-06-12 14:28:40,527","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 2d93c04e-a066-464a-9898-c63d7f82da62"}
events-1  | INFO:     172.19.0.6:42312 - "POST /events/1/reserve HTTP/1.1" 200 OK
```
7.
```
user@MacBook-Air app % docker network ls | grep app

5f3e521bb5c0   app_default   bridge    local
user@MacBook-Air app % docker network inspect app_default --format '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}'

app-redis-1: 172.19.0.4/16
app-payments-1: 172.19.0.2/16
app-events-1: 172.19.0.5/16
app-postgres-1: 172.19.0.3/16
app-gateway-1: 172.19.0.6/16

user@MacBook-Air app % 
```
8.

### Task 2
