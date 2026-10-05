# Lab 2 - Containerization: Inspect, Understand, Optimize

## Task 1 - Docker Inspection and Operations

### Image inspection

I started by listing the QuickTicket images before making any changes.

```text
app-gateway    latest    6f250b658e01    151MB
app-events     latest    17ac9899daa0    165MB
app-payments   latest    62bf3c3f5dbd    150MB
```

The largest image was `app-events` at 165 MB. I inspected the gateway image history as the lab asks and counted 15 history entries.

The relevant application-specific layers were:

```text
COPY main.py .                                                13.3kB
RUN pip install --no-cache-dir -r requirements.txt            25.1MB
COPY requirements.txt .                                       73B
WORKDIR /app                                                   0B
```

The `pip install` step was the largest application-specific layer at 25.1 MB because it installs FastAPI, Uvicorn, HTTPX and the other Python dependencies into the image.

Looking at the complete image history, the largest inherited layer was the Debian base filesystem at 78.6 MB. There was also a 35.6 MB Python runtime build layer. These are part of the `python:3.13-slim` base image rather than my application code.

---

### Container addresses and payments environment

I inspected the three application containers and got these addresses:

```text
/app-events-1   172.18.0.5
/app-gateway-1  172.18.0.6
/app-payments-1 172.18.0.4
```

The payments container included the following relevant variables:

```text
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
PYTHON_VERSION=3.13.15
```

The default payment fault injection values were therefore disabled during this test.

---

### Live debugging from inside the gateway

Before the optimization, I checked which user the gateway container was running as.

```text
root
uid=0(root) gid=0(root) groups=0(root)
```

So the original image was running the application as root.

I also checked `/etc/resolv.conf` inside the gateway:

```text
nameserver 127.0.0.11
search localdomain
options ndots:0
```

Docker provides the embedded DNS resolver at `127.0.0.11`.

I then made HTTP requests from inside the gateway using Python.

Events health check:

```json
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
```

Payments health check:

```json
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

I also resolved the service names directly:

```text
events resolves to: 172.18.0.5
payments resolves to: 172.18.0.4
```

This confirmed that the gateway does not need hardcoded container IP addresses. Docker Compose places the services on the same network and Docker DNS resolves the service name `events` to the current container address, which in this run was `172.18.0.5`.

---

### Logs and request correlation

I generated a new reservation request:

```json
{"reservation_id":"9546a55c-74f7-49e5-a96d-853ff677630d","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
```

The same request can be followed in both gateway and events logs by timestamp and operation.

Gateway:

```text
2026-09-14T14:47:11.240715679Z INFO: 172.18.0.1:47724 - "POST /events/1/reserve HTTP/1.1" 200 OK
2026-09-14T14:47:11.240813058Z {"service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve \"HTTP/1.1 200 OK\""}
```

Events:

```text
2026-09-14T14:47:11.233835108Z {"service":"events","msg":"Reserved 1 tickets for event 1: 9546a55c-74f7-49e5-a96d-853ff677630d"}
2026-09-14T14:47:11.240837636Z INFO: 172.18.0.6:52304 - "POST /events/1/reserve HTTP/1.1" 200 OK
```

The events container saw the request from `172.18.0.6`, which was the gateway address. Matching timestamps and the operation made it possible to correlate the request across both services.

---

### Docker network

The Compose network was:

```text
app_default   bridge   local
```

The network contained:

```text
app-redis-1:    172.18.0.2/16
app-postgres-1: 172.18.0.3/16
app-payments-1: 172.18.0.4/16
app-events-1:   172.18.0.5/16
app-gateway-1:  172.18.0.6/16
```

All QuickTicket components were connected to the same bridge network.

The gateway finds the events service through Docker's embedded DNS. The application uses the hostname `events`, Docker resolves it through `127.0.0.11`, and in my run it resolved to `172.18.0.5`.

---

## Task 2 - Dockerfile Optimization

### `.dockerignore`

I created the same `.dockerignore` file for `gateway`, `events`, and `payments`:

```text
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

Before the rebuild, the images were:

| Image | Size before |
|---|---:|
| app-gateway | 151 MB |
| app-events | 165 MB |
| app-payments | 150 MB |

After rebuilding without cache, the image sizes were:

| Image | Size after |
|---|---:|
| app-gateway | 151 MB |
| app-events | 165 MB |
| app-payments | 150 MB |

There was no visible size reduction in this repository. The build contexts were already very small, so the ignored files did not contribute meaningful data to the final images. The `.dockerignore` files are still useful because they prevent accidental build-context growth and exclude files such as `.env`, Markdown files, editor metadata, bytecode and Git metadata.

---

### Non-root containers

The original gateway ran as root. I changed all three Dockerfiles by adding:

```dockerfile
RUN addgroup --system app && adduser --system --ingroup app app
RUN chown -R app:app /app
USER app
```

I placed these instructions before `CMD`.

The Dockerfile diff for the three services followed the same pattern:

```diff
 EXPOSE 8080
+
+RUN addgroup --system app && adduser --system --ingroup app app
+RUN chown -R app:app /app
+USER app
+
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

```diff
 EXPOSE 8081
+
+RUN addgroup --system app && adduser --system --ingroup app app
+RUN chown -R app:app /app
+USER app
+
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]
```

```diff
 EXPOSE 8082
+
+RUN addgroup --system app && adduser --system --ingroup app app
+RUN chown -R app:app /app
+USER app
+
 CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8082"]
```

After rebuilding, I verified the effective users:

```text
gateway:
app
uid=100(app) gid=101(app) groups=101(app)

events:
app

payments:
app
```

The system still passed its health check after the change:

```json
{
  "status": "healthy",
  "checks": {
    "events": "ok",
    "payments": "ok",
    "circuit_payments": "CLOSED"
  }
}
```

So I was able to remove root privileges without breaking the application.

---

## Bonus Task - Trace a Request Across Services

I restarted the stack to make the trace easier to read and then performed one complete reserve and pay sequence.

Reservation:

```json
{"reservation_id":"980f17fc-3a73-48d9-9b7a-f3430795c511","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
```

Payment:

```json
{"order_id":"980f17fc-3a73-48d9-9b7a-f3430795c511","event_id":1,"quantity":1,"total_cents":5000,"status":"confirmed"}
```

The client-side measurement for the complete reserve plus pay sequence was:

```text
357.590 ms
```

### Timestamped trace

The relevant log lines were:

```text
14:48:15.281639 events   Reserved 1 tickets for event 1: 980f17fc-3a73-48d9-9b7a-f3430795c511
14:48:15.283308 events   POST /events/1/reserve -> 200

14:48:15.285709 gateway  POST http://events:8081/events/1/reserve -> 200
14:48:15.288259 gateway  POST /events/1/reserve -> 200

14:48:15.447751 payments Payment success: PAY-4CDE0C7D for 980f17fc-3a73-48d9-9b7a-f3430795c511
14:48:15.448439 payments POST /charge -> 200

14:48:15.450071 gateway  POST http://payments:8082/charge -> 200

14:48:15.475910 events   Order confirmed: 980f17fc-3a73-48d9-9b7a-f3430795c511
14:48:15.478341 events   POST /reservations/980f17fc-3a73-48d9-9b7a-f3430795c511/confirm -> 200

14:48:15.480413 gateway  POST http://events:8081/reservations/980f17fc-3a73-48d9-9b7a-f3430795c511/confirm -> 200
14:48:15.482974 gateway  POST /reserve/980f17fc-3a73-48d9-9b7a-f3430795c511/pay -> 200
```

The sequence was therefore:

```text
client
  -> gateway
  -> events (reserve)
  -> gateway
  -> payments (charge)
  -> gateway
  -> events (confirm)
  -> gateway
  -> client
```

From the visible service timestamps:

- The events reservation log to the completed gateway reserve response was about 6.6 ms.
- Payment success to the gateway receiving the successful payment response was about 2.3 ms.
- The payment response at the gateway to the events confirmation log was about 25.8 ms.
- The events confirmation log to the final gateway response was about 7.1 ms.
- The visible interval from the events reservation log to the final payment response was about 201.3 ms.

The exact server-side time from the gateway receiving the pay request to returning the final response cannot be calculated from these logs because there is no incoming-request start timestamp for the pay endpoint. The best complete end-to-end measurement available from this run is the client-side reserve plus pay time of 357.590 ms.

---

## Conclusion

I used Docker inspection commands to understand the image layers, runtime users, container addresses, environment variables, logs, DNS and network topology of QuickTicket.

The most important networking observation was that service discovery is name-based rather than IP-based. Docker's DNS resolver allowed the gateway to call `events:8081` and `payments:8082` even though their container addresses are assigned dynamically.

For the optimization task, `.dockerignore` did not reduce the measured image sizes because the build contexts were already small. The security improvement was more significant: all three application containers now run as the unprivileged `app` user and the application remained healthy after the rebuild.

Finally, I traced one successful ticket purchase across gateway, events and payments using timestamps and a shared reservation ID.
