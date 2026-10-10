# Lab 2. Containerization: inspect, understand, optimize

**Student:** Kirill Fadeev
**Email:** ki.fadeev@innopolis.university
**Environment:** WSL2 (kernel 6.18.33.2-microsoft-standard-WSL2, x86_64), Docker Engine 29.7.2, Docker Compose v5.5.1, QuickTicket stack from `app/docker-compose.yaml` (gateway, events, payments, postgres:17-alpine, redis:7-alpine)

All output below was captured from live runs. Task 1 was captured against the images as shipped, before any Dockerfile change, so that the `whoami` baseline is honest; Task 2 was captured after re-applying the change on the same stack.

---

## Task 1. Docker inspection and operations

### 1.1 Images and what they actually cost

```bash
docker images | grep -E 'app-|postgres|redis'
```

```plaintext
IMAGE                 ID             DISK USAGE   CONTENT SIZE   EXTRA
app-events:latest     1df01e19f46f        245MB         60.2MB   U
app-gateway:latest    949629ff967d        226MB         55.2MB   U
app-payments:latest   a10bc092e9d8        223MB         54.7MB   U
postgres:17-alpine    18cfe3ef5e68        424MB          118MB   U
redis:7-alpine        ff02b58f971e       57.8MB         16.8MB   U
```

Docker 29 with the containerd image store no longer prints a single `SIZE` column. It reports `DISK USAGE` and `CONTENT SIZE` separately, and the two are not alternative spellings of the same quantity. Summing the layer diffs from `docker history` and adding the reported content size reproduces the disk usage exactly for all three images:

| Image | Sum of layer diffs | Content size | Sum | Reported disk usage |
|---|---:|---:|---:|---:|
| app-gateway | 170.8 MB | 55.2 MB | 226.0 MB | 226 MB |
| app-events | 185.1 MB | 60.2 MB | 245.3 MB | 245 MB |
| app-payments | 168.8 MB | 54.7 MB | 223.5 MB | 223 MB |

> The three app images look like 694 MB of storage and are nothing of the sort. Each carries the same 141 MB `python:3.13-slim` base, which the content store keeps once, and each is counted twice in its own row because the unpacked snapshot and the compressed blobs both live on disk. A capacity estimate built by adding up the numbers this command prints would be wrong in both directions at once.

### 1.2 Layer history and the largest layer

```bash
docker history app-gateway --no-trunc --format "table {{.CreatedBy}}\t{{.Size}}"
```

```plaintext
CREATED BY                                                                  SIZE
CMD ["uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8080"]               0B
EXPOSE [8080/tcp]                                                           0B
COPY main.py . # buildkit                                                   24.6kB
RUN /bin/sh -c pip install --no-cache-dir -r requirements.txt # buildkit    29.6MB   <-- pip install
COPY requirements.txt . # buildkit                                          12.3kB
WORKDIR /app                                                                8.19kB
CMD ["python3"]                                                             0B
RUN /bin/sh -c set -eux;  for src in idle3 pip3 pydoc3 python3 ...          16.4kB
RUN /bin/sh -c set -eux;  savedAptMark="$(apt-mark showmanual)"; ...        40.4MB
ENV PYTHON_SHA256=1e66a7945a48390ee4c2a4268a0e4185884059a13c4aab6d1...      0B
ENV PYTHON_VERSION=3.13.15                                                  0B
ENV GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305                        0B
RUN /bin/sh -c set -eux;  apt-get update;  apt-get install -y ...           13.2MB
ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:...        0B
# debian.sh --arch 'amd64' out/ 'trixie' '@1787529600'                      87.5MB
```

**How many layers does the gateway image have?** Fifteen, of which ten come from `python:3.13-slim` and five from the project Dockerfile.

**Which layer is the largest and why?** The single largest layer is the Debian rootfs at 87.5 MB, followed by the CPython build at 40.4 MB. Both belong to the base image and are shared with the other two services. The largest layer the project itself is responsible for is `RUN pip install --no-cache-dir -r requirements.txt` at 29.6 MB, because every wheel for FastAPI, uvicorn, httpx and prometheus-client lands in one filesystem diff.

Comparing the same layer across the three services shows where the size difference lives:

| Image | pip install layer | `COPY main.py` | Base subtotal | Reported disk usage |
|---|---:|---:|---:|---:|
| app-payments | 27.7 MB | 12.3 kB | 141.1 MB | 223 MB |
| app-gateway | 29.6 MB | 24.6 kB | 141.1 MB | 226 MB |
| app-events | 43.9 MB | 20.5 kB | 141.1 MB | 245 MB |

> The lab text suggests inspecting the gateway as the largest image, but the largest is `app-events`, and the gap is entirely one layer. Payments is bare FastAPI, gateway adds httpx, events adds `psycopg2-binary` and `redis`, which cost 16.2 MB more than httpx. Nothing about the application source explains the ranking. Dependency choice does.

One deviation from the lab text is worth recording. `python:3.13-slim` does not appear in `docker images` on this machine at all, because BuildKit resolved it into the build cache without ever creating a tagged entry. Its weight still has to come from somewhere, so I took it from `docker history` instead: 87.5 + 13.2 + 40.4 + 0.016, which is 141.1 MB.

### 1.3 Container addresses and environment

```bash
docker inspect app-events-1 --format '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
```

```plaintext
/app-events-1 172.18.0.5
/app-gateway-1 172.18.0.6
/app-payments-1 172.18.0.3
/app-postgres-1 172.18.0.4
/app-redis-1 172.18.0.2
```

```bash
docker inspect app-payments-1 --format '{{range .Config.Env}}{{println .}}{{end}}'
```

```plaintext
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.15
PYTHON_SHA256=1e66a7945a48390ee4c2a4268a0e4185884059a13c4aab6d148aa208deea4a76
```

Only the first two variables come from Compose. The remaining four are inherited from the base image, and they matter for a reason unrelated to Python versions.

> Everything an image sets with `ENV` at build time stays readable at runtime by anyone who can call `docker inspect`, whether or not the value was ever meant to leave the build. That is the argument against putting credentials in `ENV`. The fault injection knobs for this service are configuration and belong in that list; a database password would be sitting in it too, printed by the same command.

### 1.4 Who the process runs as

```bash
docker exec app-gateway-1 whoami
docker exec app-gateway-1 id
```

```plaintext
root
uid=0(root) gid=0(root) groups=0(root)
```

All three application containers report `root`. This is the baseline that Task 2 changes.

### 1.5 Service discovery

```bash
docker exec app-gateway-1 cat /etc/resolv.conf
```

```plaintext
# Generated by Docker Engine.
nameserver 127.0.0.11
options ndots:0
# Based on host file: '/etc/resolv.conf' (internal resolver)
# ExtServers: [host(192.168.65.7)]
```

```bash
docker exec app-gateway-1 python3 -c "
import socket
for name in ('events', 'payments', 'postgres', 'redis'):
    print(name, '->', socket.gethostbyname(name))
"
```

```plaintext
events -> 172.18.0.5
payments -> 172.18.0.3
postgres -> 172.18.0.4
redis -> 172.18.0.2
```

```bash
docker exec app-gateway-1 python3 -c "
import urllib.request
print(urllib.request.urlopen('http://events:8081/health').read().decode())
"
```

```plaintext
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
```

```bash
docker exec app-gateway-1 python3 -c "
import urllib.request
print(urllib.request.urlopen('http://payments:8082/health').read().decode())
"
```

```plaintext
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

**How does the gateway find the events service? What IP does `events` resolve to?**

The gateway resolves the name through Docker's embedded DNS server, which every container on a user-defined network sees at `127.0.0.11` in its own `/etc/resolv.conf`. Nothing actually listens on that address inside the container namespace: an iptables rule redirects the query into the daemon's own resolver. For a name that matches a Compose service on the same network, the resolver answers from its own table with the container's address on that network, and only forwards to the external resolver (`192.168.65.7` here) for everything else. `events` resolves to **172.18.0.5**, which matches `docker inspect` exactly, so the two commands agree that the name maps to the container and not to a proxy.

`options ndots:0` matters more than it looks. It tells the resolver to try a single-label name like `events` as an absolute name first, rather than appending search domains, which is why a bare service name resolves in one query.

> The gateway is configured with `EVENTS_URL=http://events:8081`, a name, not an address, and that is the only reason the stack survives its own restarts. Addresses here are leases from the `172.18.0.0/16` pool handed out at container creation; the bonus section below recreated every container, and nothing in the application had to be told. Service discovery is what makes the address disposable.

### 1.6 Logs and following one request across services

```bash
curl -s http://localhost:3080/events > /dev/null
curl -s -X POST http://localhost:3080/events/1/reserve -H "Content-Type: application/json" -d '{"quantity":1}'
docker compose logs gateway --tail=6
docker compose logs events --tail=6
```

```plaintext
gateway-1  | {"time":"2026-09-12 11:53:31,847","level":"INFO","service":"gateway","msg":"HTTP Request: GET http://events:8081/events \"HTTP/1.1 200 OK\""}
gateway-1  | INFO:     172.18.0.1:60184 - "GET /events HTTP/1.1" 200 OK
gateway-1  | {"time":"2026-09-12 11:53:31,864","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve \"HTTP/1.1 200 OK\""}
gateway-1  | INFO:     172.18.0.1:60188 - "POST /events/1/reserve HTTP/1.1" 200 OK

events-1  | INFO:     172.18.0.6:47084 - "GET /events HTTP/1.1" 200 OK
events-1  | {"time":"2026-09-12 11:53:31,863","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 1a143a2a-e8da-4b08-b119-ae718db49d13"}
events-1  | INFO:     172.18.0.6:47084 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

**Can you follow a single request across services by matching timestamps?** Yes, once the streams are merged, because `docker compose logs` prints one service block after another rather than a single timeline:

```bash
docker compose logs --timestamps --no-color --tail=60 \
  | sed 's/^\([a-z0-9_.-]*\) *| *\([0-9TZ:.-]*\) /\2  \1  /' | sort
```

```plaintext
2026-09-12T11:53:31.847541746Z  events-1   INFO: 172.18.0.6:47084 - "GET /events HTTP/1.1" 200 OK
2026-09-12T11:53:31.848103611Z  gateway-1  {"msg":"HTTP Request: GET http://events:8081/events \"HTTP/1.1 200 OK\""}
2026-09-12T11:53:31.849116128Z  gateway-1  INFO: 172.18.0.1:60184 - "GET /events HTTP/1.1" 200 OK
2026-09-12T11:53:31.864541107Z  events-1   INFO: 172.18.0.6:47084 - "POST /events/1/reserve HTTP/1.1" 200 OK
2026-09-12T11:53:31.865066868Z  gateway-1  {"msg":"HTTP Request: POST http://events:8081/events/1/reserve \"HTTP/1.1 200 OK\""}
2026-09-12T11:53:31.866050382Z  gateway-1  INFO: 172.18.0.1:60188 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

The chain reads cleanly: events finishes serving at `.847541`, the gateway's HTTP client records the completed call 0.6 ms later, the gateway returns to the outside caller 1.0 ms after that. The only structural link between the two services is the source address in the events access line, `172.18.0.6`, which is the gateway container.

> The gateway's application code logs nothing at all on a successful request. Every per-request line in its stream comes from somewhere else: the JSON lines are httpx reporting its own outbound calls, the plain lines are uvicorn's access log. So one stream carries two formats, and a shipping pipeline that parses this service as JSON would silently drop exactly the lines that hold the status code and the client address. The service is described everywhere as emitting structured logs. Half of what it emits is not structured.

### 1.7 Network

```bash
docker network ls | grep app
docker network inspect app_default --format '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}'
```

```plaintext
NETWORK ID     NAME          DRIVER    SCOPE
4e8b7b65174b   app_default   bridge    local

app-postgres-1: 172.18.0.4/16
app-gateway-1: 172.18.0.6/16
app-events-1: 172.18.0.5/16
app-redis-1: 172.18.0.2/16
app-payments-1: 172.18.0.3/16

subnet=172.18.0.0/16 gateway=172.18.0.1
```

Compose created one implicit bridge network named after the project directory, and put all five containers on it. The `172.18.0.1` gateway address is the one that shows up as the client in the gateway's own access log, because traffic from the host arrives through the bridge.

### 1.8 What the stack costs to run

```bash
docker stats --no-stream
```

```plaintext
NAME             CPU %     MEM USAGE / LIMIT     MEM %     NET I/O
app-gateway-1    0.31%     39.52MiB / 15.51GiB   0.25%     6.74kB / 5.62kB
app-payments-1   0.32%     36.19MiB / 15.51GiB   0.23%     2.48kB / 1.22kB
app-events-1     0.40%     41.5MiB / 15.51GiB    0.26%     7.99kB / 7.65kB
app-postgres-1   0.00%     25.3MiB / 15.51GiB    0.16%     11.5kB / 8.55kB
app-redis-1      1.21%     4.324MiB / 15.51GiB   0.03%     7.8kB / 2.38kB
```

> An idle Python service costs about 38 MB of resident memory here, and the smallest of the three costs more than PostgreSQL with a live database in it. The `MEM %` column reads 0.25% only because no limit is set: the denominator is the whole machine, so the column says nothing about headroom. Lab 4 will set real limits, and that is when this number starts meaning something.

---

## Task 2. Dockerfile optimization

### 2.1 The `.dockerignore` files

Identical content in `app/gateway/`, `app/events/` and `app/payments/`:

```plaintext
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

### 2.2 Image sizes before and after

```plaintext
BEFORE                      AFTER
app-events    245MB         app-events    245MB
app-gateway   226MB         app-gateway   226MB
app-payments  223MB         app-payments  223MB
```

Not a single byte moved. That is the right answer here rather than a broken measurement, because each build context holds four files:

```plaintext
# app/gateway
-rw-r--r-- 1 fadeev fadeev    41 .dockerignore
-rw-r--r-- 1 fadeev fadeev   206 Dockerfile
-rw-r--r-- 1 fadeev fadeev 14032 main.py
-rw-r--r-- 1 fadeev fadeev    73 requirements.txt
context size: 32K
```

There is no `__pycache__`, no `.git`, no `.vscode` to exclude, and more importantly the Dockerfile never asks for them. It copies two named files, `COPY requirements.txt .` and `COPY main.py .`, so the patterns have nothing to act on.

### 2.3 A controlled experiment, because a null result proves nothing on its own

To separate "the ignore file does not work" from "there is nothing here for it to do", I built the same context twice with a deliberately planted 900 kB `__pycache__` and a `COPY . .` instruction:

```bash
docker build --no-cache --progress=plain -t lab2exp:a ctxA   # no .dockerignore
docker build --no-cache --progress=plain -t lab2exp:b ctxB   # with .dockerignore
```

```plaintext
context on disk: 916K

# COPY . .  WITHOUT .dockerignore
#4 transferring context: 914.62kB done
# COPY . .  WITH .dockerignore
#4 transferring context: 14.35kB done

lab2exp:a  189MB     COPY layer: 942kB
lab2exp:b  187MB     COPY layer: 36.9kB
```

Same file, same patterns, and now they do something: the transferred context drops by a factor of 64 and the `COPY` layer by a factor of 25.

> `.dockerignore` filters what the client ships to the builder, and therefore what a wildcard `COPY` can pick up. On its own it optimizes nothing. In QuickTicket, the optimization the lab asks for was already performed by whoever wrote `COPY main.py .` instead of `COPY . .`. Adding the file still earns its place, because it turns the current correctness into a guarantee that survives the next person who edits the Dockerfile.

### 2.4 Non-root user

Added to all three Dockerfiles before `CMD`:

```dockerfile
RUN addgroup --system app && adduser --system --ingroup app app
USER app
```

```bash
docker compose build && docker compose up -d
docker exec app-gateway-1 whoami && docker exec app-gateway-1 id
```

```plaintext
app-gateway-1  -> whoami=app  id=uid=100(app) gid=101(app) groups=101(app)
app-events-1   -> whoami=app  id=uid=100(app) gid=101(app) groups=101(app)
app-payments-1 -> whoami=app  id=uid=100(app) gid=101(app) groups=101(app)
```

The application is unharmed. Health check, listing and a full purchase all succeed as the unprivileged user:

```plaintext
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
reserve: {"reservation_id":"05fecc74-c648-4780-b7b0-c18b92fbe452", ... }
{"order_id":"05fecc74-c648-4780-b7b0-c18b92fbe452","status":"confirmed"}
```

No `chown` was needed. The lab hints suggest adding one if permission errors appear, and none appeared, because the services only ever read `/app`.

### 2.5 The consequence that produces no error

```bash
docker exec app-gateway-1 ls -la /app
docker exec app-gateway-1 sh -c 'touch /app/probe || echo "write denied"'
docker exec app-gateway-1 sh -c 'ls -la /app/__pycache__ || echo "no __pycache__"'
```

```plaintext
drwxr-xr-x 1 root root  4096 /app
-rw-r--r-- 1 root root 14032 main.py
-rw-r--r-- 1 root root    73 requirements.txt

touch: cannot touch '/app/probe': Permission denied
write denied

no __pycache__ — interpreter could not cache bytecode
```

`/app` stays owned by `root`, so the `app` user can read it and nothing else. The same directory under the previous root image contained `__pycache__/main.cpython-313.pyc` after the first import.

> CPython treats a failed bytecode write as a non-event: no warning, no log line, no exit code. The container now recompiles `main.py` from source on every start, and the only visible trace is a directory that is not there. Problems like this outlive the obvious ones. The fix is known and cheap (`chown -R app:app /app` before the `USER` line), the cost is real and permanent, and nothing in the system will ever bring it up.

### 2.6 The diff

```diff
diff --git a/app/gateway/Dockerfile b/app/gateway/Dockerfile
@@ -6,4 +6,6 @@ RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .

 EXPOSE 8080
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

diff --git a/app/events/Dockerfile b/app/events/Dockerfile
@@ -6,4 +6,6 @@ RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .

 EXPOSE 8081
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]

diff --git a/app/payments/Dockerfile b/app/payments/Dockerfile
@@ -6,4 +6,6 @@ RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .

 EXPOSE 8082
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8082"]
```

```plaintext
 M app/events/Dockerfile
 M app/gateway/Dockerfile
 M app/payments/Dockerfile
?? app/events/.dockerignore
?? app/gateway/.dockerignore
?? app/payments/.dockerignore
```

---

## Bonus task. Tracing one purchase across three services

```bash
docker compose down && docker compose up -d
RES=$(curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" -d '{"quantity":1}')
RES_ID=$(echo "$RES" | python3 -c "import sys,json; print(json.load(sys.stdin)['reservation_id'])")
curl -s -X POST "http://localhost:3080/reserve/$RES_ID/pay"
docker compose logs --timestamps
```

```plaintext
reservation_id = 120097ab-472c-4040-860f-d0429c92daac
reserve:  200, client-measured total 14.803 ms
pay:      200, client-measured total 29.337 ms
payments /health before the run: {"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

The merged trace, sorted by the Docker-side timestamp:

```plaintext
11:43:10.915280  events-1    Reserved 1 tickets for event 1: 120097ab-...
11:43:10.916187  events-1    POST /events/1/reserve HTTP/1.1 200 OK
11:43:10.916827  gateway-1   HTTP Request: POST http://events:8081/events/1/reserve "200 OK"
11:43:10.917919  gateway-1   POST /events/1/reserve HTTP/1.1 200 OK      <- reserve answered
11:43:10.952874  payments-1  Payment success: PAY-0F14F395 for 120097ab-...
11:43:10.953339  payments-1  POST /charge HTTP/1.1 200 OK
11:43:10.954006  gateway-1   HTTP Request: POST http://payments:8082/charge "200 OK"
11:43:10.971871  events-1    Order confirmed: 120097ab-...
11:43:10.973160  events-1    POST /reservations/120097ab-.../confirm HTTP/1.1 200 OK
11:43:10.974497  gateway-1   HTTP Request: POST http://events:8081/reservations/120097ab-.../confirm "200 OK"
11:43:10.977008  gateway-1   POST /reserve/120097ab-.../pay HTTP/1.1 200 OK   <- pay answered
```

Line by line, for the `pay` request:

| Time | Service | What happened | Since previous |
|---|---|---|---|
| .952874 | payments | charge accepted, reference `PAY-0F14F395` issued | first visible evidence |
| .953339 | payments | `POST /charge` answered 200 | 0.47 ms |
| .954006 | gateway | httpx records the completed charge call | 0.67 ms |
| .971871 | events | order written to PostgreSQL, reservation removed from Redis | 17.87 ms |
| .973160 | events | `POST /reservations/{id}/confirm` answered 200 | 1.29 ms |
| .974497 | gateway | httpx records the completed confirm call | 1.34 ms |
| .977008 | gateway | `POST /reserve/{id}/pay` answered 200 to the client | 2.51 ms |

**What is the total end to end time?** 29.337 ms measured at the client for `POST /reserve/{id}/pay`, confirmed by a second purchase at 31.440 ms with `ttfb=31.057 ms` and `connect=0.188 ms`. Of that, only 24.134 ms is visible in the logs, from the first payments line to the gateway's response. The `reserve` call is worse in proportion: 14.803 ms at the client against 2.639 ms of log-visible span.

The gap has a cause. uvicorn writes its access line when the response is finished, and the application writes nothing on arrival, so no log anywhere records when a request came in. Everything before the first downstream call completes, that is connection acceptance, routing and body parsing, is outside the reconstructable window.

Two further measurements from the same capture. The dominant cost inside `pay` is the 17.87 ms between the charge returning and the order being confirmed, which is the only step that writes to PostgreSQL and deletes from Redis. And each application's own timestamp precedes the Docker one by about 1.3 ms consistently (events logs `11:43:10,914` against `.915280`), which is log collection lag, not service latency.

> The trace only worked because this application happens to echo the reservation UUID into its own messages, and because I was the only one making requests. Nothing propagates a correlation identifier: the `POST /charge` access line in payments carries no identifier at all, so under any concurrency it could not be attributed to a purchase rather than to some other purchase happening in the same millisecond. What I did here is closer to reading an ordered list and assuming that adjacency means causality. Under load that assumption is the first one to break, which is the gap trace context propagation exists to close.

---

## Results

| Check | Result |
|---|---|
| Image sizes for all QuickTicket images | 245 / 226 / 223 MB, with disk usage reconciled against layer diffs |
| Layer history with the largest layer annotated | 15 layers, base 87.5 MB, project `pip install` 29.6 MB |
| IP addresses of all three services | 172.18.0.5, 172.18.0.6, 172.18.0.3 on `app_default` |
| `whoami` inside a container | `root` before Task 2, `app` (uid 100) after |
| Service discovery proven from inside the gateway | `urllib` calls to `events:8081` and `payments:8082` both answered 200 |
| Request flowing gateway to events in the logs | merged trace, 1.6 ms across the hop |
| Network inspect with container IPs | `app_default`, subnet 172.18.0.0/16, gateway 172.18.0.1 |
| Written answer on Docker DNS | embedded resolver at 127.0.0.11, `events` to 172.18.0.5 |
| `.dockerignore` created, sizes compared | created in three contexts, no size change, cause established by experiment |
| Non-root user working | all three services as uid 100, full purchase succeeds |
| `git diff` of the Dockerfile changes | three files, two lines each |
| Full purchase traced across three services | eleven lines, annotated, end to end 29.337 ms |

The same problem showed up in each of the three tasks. `app-events` is 245 MB because of one dependency layer, not because of 14 kB of application code, and the columns the CLI prints measure storage rather than the image. `.dockerignore` changed nothing here, and the only way to say that honestly was to build the case where it does change something. The purchase trace reads cleanly because a UUID happened to land in the log text and nobody else was making requests. Every command in this lab answers instantly. What each one is actually counting takes longer to find out, and that is where the answers were.
