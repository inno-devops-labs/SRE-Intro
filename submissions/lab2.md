# Lab 2 Containerization: Inspect, Understand, Optimize

## Task 1 Docker Inspection фтв Operations

### 1.1 Image inspection
```bash
docker images | grep app
```

I check app images here.

- app-events:latest — about 233MB
- app-gateway:latest — about 213MB
- app-payments:latest — about 211MB

Biggest part is Python install and pip packages.

### 1.2 Container inspection

I check IP address of services:

- gateway: 172.21.0.6
- events: 172.21.0.5
- payments: 172.21.0.4

Payments env variables:

- PAYMENT_FAILURE_RATE=0.0
- PAYMENT_LATENCY_MS=0

### 1.3 Live debugging with exec

```bash
docker exec app-gateway-1 whoami
# root (before Task 2)
```

DNS resolver is:

- nameserver 127.0.0.11

Check connection:

- http://events:8081/health -> works
- http://payments:8082/health -> works

So services talk by names like events and payments. Docker DNS help here.

### 1.4 Logs analysis

Logs show request flow:

- Gateway -> Events (reserve)
- Gateway -> Payments (charge)
- Events -> confirm

### 1.5 Network inspection

All containers are in network `app_default`.

IP range is like `172.21.0.0/16`.

---

## Task 2 — Dockerfile Optimization

I do some small optimization:

- make `.dockerignore` in `gateway/`, `events/`, `payments/`
- update `gateway/Dockerfile`
- add non-root user `app`

Check:

```bash
docker exec app-gateway-1 whoami
# app
```

So gateway now run not as root.

---

## Bonus Task — Trace a Request Across Services

I trace one ticket buy request.

Reservation ID: `cbb0db56-1b8b-4b10-a0f2-25b5e3378f3e`

Log flow:

- Gateway get `POST /events/1/reserve` -> `200 OK`
- Events reserve ticket
- Gateway -> Payments `/charge` -> `200 OK`
- Gateway -> Events `/confirm` -> `200 OK`
- User get confirmation

End to end time is about 100-200 ms. It is fast.

---

## Conclusions

In Lab 2 I learn:

- Docker image layers
- service discovery by name
- how to debug with `docker exec` and `logs`
- basic optimization and security with non-root user

I am ready for next labs
