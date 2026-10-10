# Lab 2 — Containerization: Inspect, Understand, Optimize

**Author:** jakefish18
**System:** QuickTicket (gateway + events + payments + postgres + redis), still running from Lab 1.
**Base image:** `python:3.13-slim` for all three app services (the lab text says 3.12; the
repo Dockerfiles actually pin `3.13`).

> Same port note as Lab 1: host `5432` was taken, so Postgres is published on `5434:5432`
> via a local compose override. Internal networking (`events`/`payments`/`postgres`/`redis`
> by name) is unaffected.
---

## Task 1 — Docker Inspection & Operations

### 1) `docker images` — QuickTicket image sizes

```
REPOSITORY       TAG          SIZE
app-events       latest       260MB     <-- largest app image
app-gateway      latest       239MB
app-payments     latest       236MB
postgres         17-alpine    393MB
redis            7-alpine     58.7MB
```

The three app images are ~236–260 MB. `events` is the heaviest because its
`requirements.txt` pulls in `psycopg2-binary` (Postgres driver) and `redis` on top of the
shared FastAPI/uvicorn stack. `redis:7-alpine` (58.7 MB) shows how much smaller an
Alpine-based image is than a Debian-slim Python one.

### 2) `docker history` — layers (annotated)

I inspected the **largest** image, `app-events` (260 MB). It has **15 layers**:

```
SIZE      CREATED BY
0B        CMD ["uvicorn" "main:app" "--host" "0.0.0.0" ...]   <- metadata, 0B
0B        EXPOSE 8081/tcp                                      <- metadata, 0B
20.5kB    COPY main.py .                                       <- our app code
44.1MB    RUN pip install --no-cache-dir -r requirements.txt  <- *** PIP INSTALL (deps) ***
12.3kB    COPY requirements.txt .
8.19kB    WORKDIR /app
0B        CMD ["python3"]                                      <- from base image
16.4kB    RUN ... (python symlinks)                            <- from base image
43.5MB    RUN ... savedAptMark ... (build CPython + deps)      <- from base image
0B        ENV PYTHON_SHA256=...
0B        ENV PYTHON_VERSION=3.13.14
0B        ENV GPG_KEY=...
4.98MB    RUN apt-get update; apt-get install ca-certificates  <- from base image
0B        ENV PATH=...
109MB     # debian.sh ... trixie ... (rootfs)                  <- *** BASE OS LAYER ***
```

**Which layer is the largest and why?** The single largest layer is the **109 MB Debian
("trixie") root filesystem** that ships inside `python:3.13-slim` — i.e. the base OS, not
anything we added. The next is the 43.5 MB layer where the base image compiles/installs
CPython. **The largest layer *we* add is `RUN pip install` at 44.1 MB** — all of QuickTicket's
Python dependencies land there. (For `app-gateway` the pip layer is smaller, 28.7 MB, because
it has fewer deps — no `psycopg2`/`redis`.) Takeaway: ~150 MB of every image is the base OS +
runtime; our code+deps are only ~44 MB on top. To shrink the image you attack the base
(e.g. an `-alpine` or distroless base), not the app layer.

### 3) Service IP addresses (`docker inspect`)

```
/app-gateway-1   172.22.0.6
/app-events-1    172.22.0.5
/app-payments-1  172.22.0.4
```

(All on the `app_default` bridge network, `172.22.0.0/16`.)

### 4) Environment variables of the payments service

```
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
PYTHON_VERSION=3.13.14
PYTHON_SHA256=639e43243c620a308f968213df9e00f2f8f62332f7adbaa7a7eeb9783057c690
```

The two app-relevant vars are `PAYMENT_FAILURE_RATE` and `PAYMENT_LATENCY_MS` (the fault-injection
knobs from Lab 1's bonus); the rest are inherited from the `python:3.13-slim` base image.
Note payments has **no** `EVENTS_URL`/`DB_HOST` etc. — it is a leaf service that talks to nobody.

### 5) Live debugging with `exec`

```
$ docker exec app-gateway-1 whoami
root
$ docker exec app-gateway-1 id
uid=0(root) gid=0(root) groups=0(root)
```

The container runs as **root** (Task 2 fixes this).

```
$ docker exec app-gateway-1 cat /etc/resolv.conf
nameserver 127.0.0.11
options ndots:0
```

Name resolution is handled by **Docker's embedded DNS server at `127.0.0.11`**.

Service discovery by name works (no IPs hardcoded anywhere in the app):

```
$ docker exec app-gateway-1 python3 -c "import urllib.request; print(urllib.request.urlopen('http://events:8081/health').read().decode())"
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
$ docker exec app-gateway-1 python3 -c "import urllib.request; print(urllib.request.urlopen('http://payments:8082/health').read().decode())"
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
$ docker exec app-gateway-1 python3 -c "import socket; print(socket.gethostbyname('events')); print(socket.gethostbyname('payments'))"
172.22.0.5      # events
172.22.0.4      # payments
```

The resolved IPs match `docker inspect` exactly.

### 6) Log snippet — one request flowing gateway → events

A `GET /events` then `POST /events/1/reserve`, seen from both sides (`--timestamps`):

```
# gateway received the client call and immediately called events by name:
gateway-1 | 2026-06-15T22:32:44.857420Z msg:"HTTP Request: GET http://events:8081/events HTTP/1.1 200 OK"
gateway-1 | 2026-06-15T22:32:44.858651Z INFO: 149.154.167.51:20817 - "GET /events HTTP/1.1" 200 OK
gateway-1 | 2026-06-15T22:32:44.875104Z msg:"HTTP Request: POST http://events:8081/events/1/reserve HTTP/1.1 200 OK"
gateway-1 | 2026-06-15T22:32:44.875563Z INFO: 149.154.167.51:49626 - "POST /events/1/reserve HTTP/1.1" 200 OK
# events handled those same calls — note the source IP 172.22.0.6 == the gateway container:
events-1  | 2026-06-15T22:32:44.856281Z INFO: 172.22.0.6:55368 - "GET /events HTTP/1.1" 200 OK
events-1  | 2026-06-15T22:32:44.874358Z msg:"Reserved 1 tickets for event 1: 0fc95a7f-4b74-4459-830f-16da4f91d269"
events-1  | 2026-06-15T22:32:44.874765Z INFO: 172.22.0.6:55368 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

**Yes — you can follow one request across services by timestamp.** The `events` access logs
list the source as `172.22.0.6` (the gateway's IP), and the events-side line (`.856`) precedes
the gateway's "got 200 back" line (`.857`) by ~1 ms — exactly the gateway→events round trip.

### 7) Network inspect

```
$ docker network ls | grep app
72942945427e   app_default   bridge   local
$ docker network inspect app_default --format '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}'
app-payments-1: 172.22.0.4/16
app-events-1:   172.22.0.5/16
app-gateway-1:  172.22.0.6/16
app-redis-1:    172.22.0.2/16
app-postgres-1: 172.22.0.3/16
```

All five containers share one user-defined bridge network (`app_default`, `172.22.0.0/16`),
created automatically by Compose.

### 8) How does the gateway find the events service?

The gateway never knows or hardcodes an IP. Its code calls `http://events:8081`. Because every
container's `/etc/resolv.conf` points at **Docker's embedded DNS resolver `127.0.0.11`**, the
hostname `events` is resolved there. On a Compose-created user-defined bridge network, Docker
registers each **service/container name** as a DNS A-record pointing at that container's current
bridge IP. So `events` resolves to **`172.22.0.5`** (confirmed by both `socket.gethostbyname`
and `docker inspect`). This is dynamic service discovery: if a container is recreated and gets a
new IP, the name still resolves to the new address — which is why the app references services by
name, not by IP. (Indeed, after the Bonus `down`/`up` the IPs were reassigned, but the names kept
working unchanged.)

---

## Task 2 — Dockerfile Optimization

### `.dockerignore`

Created identical `app/gateway/.dockerignore`, `app/events/.dockerignore`,
`app/payments/.dockerignore`:

```
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

### Image sizes before / after `.dockerignore` (`docker compose build --no-cache`)

| Image | Before (Task 1) | After `.dockerignore` |
|-------|----------------:|----------------------:|
| app-gateway  | 239 MB | 239 MB |
| app-events   | 260 MB | 260 MB |
| app-payments | 236 MB | 237 MB |

**Difference: effectively none (±1 MB rounding).** This is expected and is the *point* of the
exercise: a `.dockerignore` only helps when the build context actually contains junk
(`.git/`, `__pycache__/`, virtualenvs, large docs). Here each build context is a single service
folder containing only `main.py`, `requirements.txt`, `Dockerfile` — there is no `.git/` in the
context (it lives at the repo root, outside each service dir) and no caches, so there is nothing
to exclude. It is still worth committing: it keeps the saving at zero *and prevents* a future
`__pycache__/` or `.env` from silently bloating the image or leaking secrets into a layer.

### Non-root user

Added to each Dockerfile before `CMD`:

```dockerfile
RUN addgroup --system app && adduser --system --ingroup app app
USER app
```

Rebuilt (`docker compose up -d --build`) and verified — all three now run as **`app`**, not root:

```
app-gateway-1    whoami=app  id=100
app-events-1     whoami=app  id=100
app-payments-1   whoami=app  id=100
```

The app needed **no** `chown`: dependencies live in `/usr/local` (world-readable) and the
services never write to `/app` (state lives in Postgres/Redis), so an unprivileged user can run
them as-is. Functional smoke test after the change confirms nothing broke:

```
$ curl -s http://localhost:3080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
$ curl -s -X POST http://localhost:3080/events/3/reserve -H 'Content-Type: application/json' -d '{"quantity":2}'
{"reservation_id":"cb1802f9-e2c5-4a2d-a5e9-3028997e4257","event_id":3,"quantity":2,"total_cents":30000,"expires_in_seconds":300}
```

Size after non-root: unchanged (239/260/237 MB) — the user-creation layer is a few KB.

### `git diff` of the Dockerfile changes

```diff
diff --git a/app/gateway/Dockerfile b/app/gateway/Dockerfile
@@ -5,5 +5,9 @@ COPY requirements.txt .
 RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .

+# Run as an unprivileged user instead of root (defense in depth)
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
+
 EXPOSE 8080
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
diff --git a/app/events/Dockerfile b/app/events/Dockerfile
@@ -5,5 +5,9 @@ COPY requirements.txt .
 RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .

+# Run as an unprivileged user instead of root (defense in depth)
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
+
 EXPOSE 8081
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]
diff --git a/app/payments/Dockerfile b/app/payments/Dockerfile
@@ -5,5 +5,9 @@ COPY requirements.txt .
 RUN pip install --no-cache-dir -r requirements.txt
 COPY main.py .

+# Run as an unprivileged user instead of root (defense in depth)
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
+
 EXPOSE 8082
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8082"]
```

(The three `.dockerignore` files are new/untracked, so they don't appear in the diff above —
their content is shown earlier in this section.)

---

## Bonus Task — Trace a Request Across Services

After `docker compose down && docker compose up -d` (clean logs) and a 5 s settle, I ran one full
purchase. `/pay` was measured client-side with `curl -w '%{time_total}'`:

```
reservation_id = b8b302c0-7a71-416e-ba5d-7236964bb291
pay http_code=200  time_total=0.011906s
pay body: {"order_id":"b8b302c0-...","event_id":1,"quantity":1,"total_cents":5000,"status":"confirmed"}
```

Timestamped logs for that reservation, in true chronological order:

```
# --- step 0: the reserve (events only, 1 hop) ---
events-1   | 2026-06-15T22:33:42.740905Z  Reserved 1 tickets for event 1: b8b302c0-...
# --- the /pay request (gateway fans out to payments, then events) ---
payments-1 | 2026-06-15T22:33:42.815xxxZ  Payment success: PAY-BC1C3515 for b8b302c0-...
payments-1 | 2026-06-15T22:33:42.816439Z  INFO: 172.22.0.6:55976 - "POST /charge HTTP/1.1" 200 OK
gateway-1  | 2026-06-15T22:33:42.816931Z  HTTP Request: POST http://payments:8082/charge  200 OK
events-1   | 2026-06-15T22:33:42.821711Z  Order confirmed: b8b302c0-...
events-1   | 2026-06-15T22:33:42.821976Z  INFO: 172.22.0.6:45736 - "POST /reservations/b8b302c0-.../confirm HTTP/1.1" 200 OK
gateway-1  | 2026-06-15T22:33:42.822260Z  HTTP Request: POST http://events:8081/reservations/b8b302c0-.../confirm  200 OK
gateway-1  | 2026-06-15T22:33:42.822902Z  INFO: 149.154.167.51:40536 - "POST /reserve/b8b302c0-.../pay HTTP/1.1" 200 OK
```

### Annotated, with timing between hops

| # | T (relative) | Service | Action | Δ since prev |
|---|-------------:|---------|--------|-------------:|
| 1 | +0.000 ms | **gateway** | receives client `POST /reserve/<id>/pay`, calls `payments:8082/charge` | — |
| 2 | ~+0.8 ms | **payments** | mock charge succeeds → `PAY-BC1C3515` (no latency injected) | sub-ms |
| 3 | +1.8 ms | **gateway** | gets `charge 200`, calls `events:8081/reservations/<id>/confirm` | ~0.5 ms |
| 4 | +6.6 ms | **events** | INSERT order into Postgres + DELETE reservation from Redis → "Order confirmed" | **~4.8 ms** |
| 5 | +7.2 ms | **gateway** | gets `confirm 200` | ~0.5 ms |
| 6 | +7.7 ms | **gateway** | returns `200` (+ fires async notify, which is a no-op in labs 1–10) to client | ~0.6 ms |

(Relative times are anchored at the payments charge log `.815`; the gateway received the request
a fraction of a ms earlier.)

### End-to-end time

- **Client-measured (curl `time_total`): ~11.9 ms** — the headline number, gateway request-in to
  response-out as seen over localhost.
- **Server-side span in the logs: ~7.9 ms** (`.815` charge → `.822902` final 200). The
  ~4 ms gap vs. the client number is localhost TCP setup/teardown and gateway request parsing
  before the first downstream call.

**The dominant cost is the `confirm` hop (~4.8 ms)** — the only step doing real I/O (a Postgres
`INSERT` + a Redis `DELETE`). The mock `charge` is sub-millisecond. So in a healthy system the
purchase latency is essentially "one database write," and the two cross-service network hops add
only ~1 ms each on the local bridge.

---

## Summary

- Inspected images (3 app images 236–260 MB), layers (base OS = 109 MB dominates; pip layer
  44 MB is the largest we add), per-service IPs, payments env, and the `app_default` bridge.
- Proved Docker DNS service discovery: `127.0.0.11` resolves `events`→`172.22.0.5`,
  `payments`→`172.22.0.4`, matching `inspect`.
- Optimized all three Dockerfiles: added `.dockerignore` (no size change here — contexts were
  already clean) and a **non-root `app` user** (verified `whoami=app`, app still healthy).
- Traced a full purchase across all three services by timestamp: **~11.9 ms** end-to-end,
  dominated by the Postgres/Redis write in the confirm step.