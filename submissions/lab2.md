## Task 1 — Docker Inspection & Operations

#### Image sizes
The following command shows locally built application images and their sizes:

```bash
docker images | grep app
```

The QuickTicket-related images are:

```text
app-events:latest      233MB
app-gateway:latest     213MB
app-payments:latest    211MB
```

The `atlas-atlas-app` image is unrelated to this lab and belongs to another local project.

---

#### Docker history

For the `app-gateway` image, `docker history` shows the image build history and layer sizes.

The image has 15 history entries. If only non-empty filesystem layers are counted, it has 8 layers.

The largest layer overall is the base Debian layer:

```text
# debian.sh --arch 'amd64' out/ 'trixie' '@1779062400'     87.4MB
```

The largest layer related to the application itself is:

```text
RUN /bin/sh -c pip install --no-cache-dir -r requirements.txt # buildkit     29MB
```

This is the `pip install` layer. It is relatively large because Python dependencies from `requirements.txt` are installed into the image filesystem.

---

#### Container IP addresses
The inspected services received the following internal Docker network IP addresses:

```text
/app-events-1 172.19.0.5
/app-gateway-1 172.19.0.6
/app-payments-1 172.19.0.4
```

These IP addresses are internal to the Docker Compose network. They are also ephemeral, meaning they may change after containers are recreated. Because of that, services should not depend on fixed IP addresses and should use Docker Compose service names instead.

---

#### Payments environment variables

The `payments` service was configured with the following application-specific environment variables:

```text
PAYMENT_FAILURE_RATE=0.0
PAYMENT_LATENCY_MS=0
```

This means that payments were configured to always succeed and to have no artificial latency during this run.

---

#### Live debugging from inside the gateway container

Before the non-root user change, the gateway container was running as `root`:

```text
root
uid=0(root) gid=0(root) groups=0(root)
```

The gateway container uses Docker’s embedded DNS resolver:

```text
nameserver 127.0.0.11
```

This resolver allows containers in the same Docker Compose network to reach each other by service name.

From inside the gateway container, I checked the `events` service health endpoint:

```json
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
```

This confirms that `gateway` can resolve and reach `events:8081` through the internal Docker network.

---

#### Request flow through gateway and events

I generated traffic from the host through the gateway:

```bash
curl -s http://localhost:3080/events > /dev/null

curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity":1}'
```

The logs show that the request entered through the gateway and was forwarded to the `events` service:

```text
gateway -> http://events:8081/events
gateway -> http://events:8081/events/1/reserve
events  -> Reserved 1 tickets for event 1
```

This confirms the flow:

```text
host curl -> localhost:3080 -> gateway:8080 -> events:8081
```

---

#### Network inspection

Docker Compose created the default bridge network:

```text
app_default
```

All services were attached to this network:

```text
app-payments-1: 172.19.0.4/16
app-gateway-1: 172.19.0.6/16
app-redis-1: 172.19.0.3/16
app-events-1: 172.19.0.5/16
app-postgres-1: 172.19.0.2/16
```

This confirms that all containers are connected to the same Docker Compose network and can communicate internally.

---

#### Answer

The gateway finds the `events` service using Docker Compose service discovery.

All containers are connected to the `app_default` network, and Docker provides an internal DNS resolver at:

```text
127.0.0.11
```

When the gateway sends a request to:

```text
http://events:8081
```

Docker resolves the service name `events` to the internal IP address of the `events` container.

In this run, `events` resolved to:

```text
172.19.0.5
```

So the request path was:

```text
gateway -> Docker DNS -> events:8081 -> app-events-1 at 172.19.0.5
```

---

## Task 2 — Dockerfile Optimization

#### Image sizes before and after `.dockerignore`

Before adding `.dockerignore`:

```text
app-events:latest      233MB
app-gateway:latest     213MB
app-payments:latest    211MB
```

After adding `.dockerignore` and rebuilding:

```text
app-events:latest      233MB
app-gateway:latest     213MB
app-payments:latest    211MB
```

There was no visible change in the final image sizes.

This is expected because the Dockerfiles already copy only `requirements.txt` and `main.py` into each image. The `.dockerignore` file still improves the build process by keeping unnecessary files out of the Docker build context.

The build output also confirmed that `.dockerignore` was loaded and that the build context was very small:

```text
transferring context: 64B
```

---

#### `.dockerignore` content

The same `.dockerignore` file was added to all three service directories:

```text
app/gateway/.dockerignore
app/events/.dockerignore
app/payments/.dockerignore
```

Content:

```dockerignore
__pycache__
*.pyc
.git
.env
*.md
.vscode
```

This excludes Python cache files, Git metadata, environment files, Markdown files, and editor-specific files from the build context.

---

#### Non-root user

Initially, the containers were running as `root`.

To improve security, I added a dedicated non-root user to each application Dockerfile:

```dockerfile
RUN addgroup --system app && adduser --system --ingroup app app
USER app
```

After rebuilding and restarting the containers, the gateway container runs as:

```text
app
```

This confirms that the application process no longer runs as `root`.

---

#### Dockerfile diff

The Dockerfile changes add a system user named `app` and switch the runtime user from `root` to `app`:

```diff
+RUN addgroup --system app && adduser --system --ingroup app app
+USER app
```

This change was applied to:

```text
app/events/Dockerfile
app/gateway/Dockerfile
app/payments/Dockerfile
```

The main benefit is reduced privilege inside the container. If the application process is compromised, it will run with fewer permissions than a root process.

---

## Task 3 — Bonus: Trace a Request Across Services

#### Full purchase flow

I restarted the Docker Compose environment to clear previous logs:

```bash
docker compose down
docker compose up -d
sleep 5
docker compose ps
```

Then I executed a full purchase flow:

```bash
RES=$(curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity":1}')

RES_ID=$(echo "$RES" | python3 -c "import sys,json; print(json.load(sys.stdin)['reservation_id'])")

curl -s -X POST "http://localhost:3080/reserve/$RES_ID/pay"
```

The reservation was created successfully:

```json
{"reservation_id":"1f9837d5-24f1-46fe-91dc-c0e58059e28a","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
```

The payment request returned a confirmed order:

```json
{"order_id":"1f9837d5-24f1-46fe-91dc-c0e58059e28a","event_id":1,"quantity":1,"total_cents":5000,"status":"confirmed"}
```

This confirms that the full purchase flow completed successfully.

---

#### Timestamped trace

The logs were captured with timestamps:

```bash
docker compose logs --timestamps --no-color > /tmp/lab2_purchase_flow.log
grep -nEi "$RES_ID|reserve|pay|payment|confirm" /tmp/lab2_purchase_flow.log
```

Relevant timestamped logs:

```text
25:gateway-1   | 2026-06-09T12:22:13.423813508Z {"time":"2026-06-09 12:22:13,423","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/events/1/reserve \"HTTP/1.1 200 OK\""}
26:gateway-1   | 2026-06-09T12:22:13.424987296Z INFO:     172.19.0.1:53212 - "POST /events/1/reserve HTTP/1.1" 200 OK
40:events-1    | 2026-06-09T12:22:13.422400120Z {"time":"2026-06-09 12:22:13,422","level":"INFO","service":"events","msg":"Reserved 1 tickets for event 1: 1f9837d5-24f1-46fe-91dc-c0e58059e28a"}
41:events-1    | 2026-06-09T12:22:13.423029084Z INFO:     172.19.0.6:56810 - "POST /events/1/reserve HTTP/1.1" 200 OK

27:gateway-1   | 2026-06-09T12:22:44.070178314Z {"time":"2026-06-09 12:22:44,069","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 200 OK\""}
14:payments-1  | 2026-06-09T12:22:44.069179982Z {"time":"2026-06-09 12:22:44,068","level":"INFO","service":"payments","msg":"Payment success: PAY-34F4EB2C for 1f9837d5-24f1-46fe-91dc-c0e58059e28a"}
15:payments-1  | 2026-06-09T12:22:44.069502644Z INFO:     172.19.0.6:49112 - "POST /charge HTTP/1.1" 200 OK
28:gateway-1   | 2026-06-09T12:22:44.084544425Z {"time":"2026-06-09 12:22:44,084","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://events:8081/reservations/1f9837d5-24f1-46fe-91dc-c0e58059e28a/confirm \"HTTP/1.1 200 OK\""}
29:gateway-1   | 2026-06-09T12:22:44.085263161Z INFO:     172.19.0.1:40360 - "POST /reserve/1f9837d5-24f1-46fe-91dc-c0e58059e28a/pay HTTP/1.1" 200 OK
42:events-1    | 2026-06-09T12:22:44.083659465Z {"time":"2026-06-09 12:22:44,083","level":"INFO","service":"events","msg":"Order confirmed: 1f9837d5-24f1-46fe-91dc-c0e58059e28a"}
43:events-1    | 2026-06-09T12:22:44.084054713Z INFO:     172.19.0.6:50218 - "POST /reservations/1f9837d5-24f1-46fe-91dc-c0e58059e28a/confirm HTTP/1.1" 200 OK
```

`docker compose logs` groups logs by service, so the printed order is not perfectly chronological. The timestamps should be used to reconstruct the real order.

---

#### Annotated trace

##### Reserve phase

| Timestamp          |     Delta | Service | Action                                                     |
| ------------------ | --------: | ------- | ---------------------------------------------------------- |
| 12:22:13.422400120 |  0.000 ms | events  | Created reservation `1f9837d5-24f1-46fe-91dc-c0e58059e28a` |
| 12:22:13.423029084 | +0.629 ms | events  | Returned `200 OK` for `POST /events/1/reserve`             |
| 12:22:13.423813508 | +0.784 ms | gateway | Received `200 OK` from `events:8081/events/1/reserve`      |
| 12:22:13.424987296 | +1.174 ms | gateway | Returned `200 OK` to the external client                   |

Observed reserve duration:

```text
12:22:13.424987296 - 12:22:13.422400120 = 2.587 ms
```

##### Pay and confirm phase

| Timestamp          |      Delta | Service  | Action                                                                     |
| ------------------ | ---------: | -------- | -------------------------------------------------------------------------- |
| 12:22:44.069179982 |   0.000 ms | payments | Payment succeeded for reservation `1f9837d5-24f1-46fe-91dc-c0e58059e28a`   |
| 12:22:44.069502644 |  +0.323 ms | payments | Returned `200 OK` for `POST /charge`                                       |
| 12:22:44.070178314 |  +0.676 ms | gateway  | Received `200 OK` from `payments:8082/charge`                              |
| 12:22:44.083659465 | +13.481 ms | events   | Confirmed the order                                                        |
| 12:22:44.084054713 |  +0.395 ms | events   | Returned `200 OK` for `POST /reservations/{reservation_id}/confirm`        |
| 12:22:44.084544425 |  +0.490 ms | gateway  | Received `200 OK` from `events:8081/reservations/{reservation_id}/confirm` |
| 12:22:44.085263161 |  +0.719 ms | gateway  | Returned `200 OK` to the external client                                   |

Observed pay + confirm duration from the first visible payment log to the final gateway response:

```text
12:22:44.085263161 - 12:22:44.069179982 = 16.083 ms
```

---

#### Answer

The exact timestamp when the gateway first received the external `/reserve/{reservation_id}/pay` request is not explicitly logged. The gateway access log appears when the request is completed.

Using the first visible gateway log in the payment flow:

```text
12:22:44.070178314 gateway -> payments:8082/charge completed
```

and the final gateway response log:

```text
12:22:44.085263161 gateway -> external client 200 OK
```

the observable gateway-side end-to-end time is:

```text
15.085 ms
```

If measured from the first visible service log in the payment flow to the final gateway response, the observed time is:

```text
16.083 ms
```

So the best supported answer from these logs is:

```text
Observable end-to-end time for the pay + confirm request: about 15–16 ms.
```

The full logical flow was:

```text
host curl
  -> gateway: POST /reserve/{reservation_id}/pay
  -> payments: POST /charge
  -> gateway: payment succeeded
  -> events: POST /reservations/{reservation_id}/confirm
  -> gateway: returned 200 OK to the client
```
