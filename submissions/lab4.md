# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

## Task 1 — Write Manifests & Deploy to k3d

### 4.1 — Create a k3d cluster

```bash
k3d cluster create quickticket
kubectl get nodes
```
```
NAME                       STATUS   ROLES           AGE     VERSION
k3d-quickticket-server-0   Ready    control-plane   4m31s   v1.35.5+k3s1
```

### 4.2 — Build and import images

```bash
cd app/
docker build -t quickticket-gateway:v1 ./gateway
docker build -t quickticket-events:v1 ./events
docker build -t quickticket-payments:v1 ./payments
k3d image import quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 -c quickticket
```
All three images built and imported successfully (`k3d image import` reported "Successfully imported 3 image(s) into 1 cluster(s)").

### 4.3 — Deploy PostgreSQL and Redis

`k8s/postgres.yaml` — Deployment (1 replica, `postgres:17-alpine`, env `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD=quickticket`, port 5432) + ClusterIP Service on port 5432.

`k8s/redis.yaml` — Deployment (1 replica, `redis:7-alpine`, port 6379) + ClusterIP Service on port 6379.

```bash
kubectl apply -f k8s/postgres.yaml
kubectl apply -f k8s/redis.yaml
kubectl get pods
kubectl get svc
```
Both came up `1/1 Running` (postgres took ~2.5 min on first run — pulling `postgres:17-alpine` from Docker Hub since it isn't a locally-built image; redis was already cached).

### 4.4 — Deploy QuickTicket services

`k8s/gateway.yaml`, `k8s/events.yaml`, `k8s/payments.yaml` — each a Deployment (1 replica, locally-built image, `imagePullPolicy: Never`) + ClusterIP Service, with the env vars specified in the lab (gateway → `EVENTS_URL`/`PAYMENTS_URL`/`GATEWAY_TIMEOUT_MS`; events → `DB_*`/`REDIS_*`/`RESERVATION_TTL`; payments → `PAYMENT_FAILURE_RATE`/`PAYMENT_LATENCY_MS`).

```bash
kubectl apply -f k8s/
kubectl get pods -w
```
All three came up `1/1 Running` within ~6 seconds of apply — no pull wait since the images were already imported into the cluster's containerd, and postgres/redis were already `Ready` so there was no dependency-ordering failure (`events` connected to postgres/redis on the first try).

### 4.5 — Initialize the database

```bash
kubectl exec -it $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql
```
```
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

### 4.6 — Verify everything works

```bash
kubectl port-forward svc/gateway 3081:8080 &
curl -s http://localhost:3081/events | python3 -m json.tool
curl -s http://localhost:3081/health | python3 -m json.tool
```

`/events` — 5 seeded events returned (Go Conference 2026, Python Workshop, SRE Meetup, Kubernetes Deep Dive, Cloud Native Summit).

`/health`:
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

**`kubectl get pods,svc` (full stack running):**
```
NAME                           READY   STATUS    RESTARTS   AGE
pod/events-6c4df7d6-r2w95      1/1     Running   0          41s
pod/gateway-6fc44f68c5-8pw6t   1/1     Running   0          7s
pod/payments-58fb468db-xc4rv   1/1     Running   0          41s
pod/postgres-7c7ffc4b-6jffj    1/1     Running   0          3m27s
pod/redis-c46d5dffc-zphn8      1/1     Running   0          3m27s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.220.8     <none>        8081/TCP   41s
service/gateway      ClusterIP   10.43.17.143    <none>        8080/TCP   41s
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    4m32s
service/payments     ClusterIP   10.43.252.167   <none>        8082/TCP   41s
service/postgres     ClusterIP   10.43.155.207   <none>        5432/TCP   3m27s
service/redis        ClusterIP   10.43.55.171    <none>        6379/TCP   3m27s
```

### 4.7 — Test K8s self-healing

```bash
kubectl delete pod -l app=gateway
kubectl get pods -l app=gateway -w
```
```
13:49:57.332  pod "gateway-6fc44f68c5-w67p6" deleted
13:49:58.492  gateway-6fc44f68c5-8pw6t   1/1   Running   0   1s
```

**How fast did it recover? Compare with Lab 1 (manual `docker compose start`).**
The Deployment controller noticed the missing replica and had a brand-new pod `1/1 Ready` in **about 1 second** — no pull was needed since the image was already imported locally, so the whole cycle was just schedule → create container → pass the container's own startup. In Lab 1, docker-compose does **not** do this at all: a stopped/killed container stays down until a human runs `docker compose start <service>` — there is no controller reconciling desired vs. actual state, so recovery time there is "however long it takes the operator to notice and type the command," not a bounded, automatic number of seconds. That's the core difference Kubernetes' Deployment/ReplicaSet controller provides over plain Compose: continuous reconciliation instead of a one-shot `up`.

---

## Task 2 — Probes & Resource Limits

### 4.9 — Add readiness and liveness probes

Added to `gateway` (port 8080), `events` (port 8081) and `payments` (port 8082) in their respective `k8s/*.yaml`:
```yaml
livenessProbe:
  httpGet:
    path: /health
    port: <8080|8081|8082>
  initialDelaySeconds: 10
  periodSeconds: 10
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /health
    port: <8080|8081|8082>
  periodSeconds: 5
  failureThreshold: 2
```

```bash
kubectl apply -f k8s/
kubectl describe pod -l app=gateway | grep -A 5 "Liveness\|Readiness"
```
```
Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s successThreshold=1 failureThreshold=3
Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s successThreshold=1 failureThreshold=2
```
(`events` and `payments` show the same block on their own ports — confirmed via the same `describe` command.)

### 4.10 — Observe readiness probe failure

```bash
kubectl scale deployment redis --replicas=0
kubectl get pods -l app=events -w
```
Deleting just the redis *pod* (`kubectl delete pod -l app=redis`) wasn't enough to observe a failure — a fresh redis pod (already-cached image, local k3d) was back and `Ready` in ~1-2s, faster than `events`' own 5s Redis-check cache, so `/health` never saw it down. Scaling the Deployment to 0 replicas removed redis for longer and produced a real, sustained failure:

```
13:53:50  scaled redis to 0
13:54:06  events-675d86c77-vkzpc   0/1   Running   0             (readiness now failing — 16s after redis went away)
13:54:32  events-675d86c77-vkzpc   0/1   Running   1 (3s ago)     (liveness threshold also crossed — container restarted)
```

`kubectl describe pod -l app=events` events log:
```
Warning  Unhealthy  Readiness probe failed: Get ".../health": dial tcp ...: connect: connection refused
Warning  Unhealthy  Readiness probe failed: HTTP probe failed with statuscode: 503
Warning  Unhealthy  Liveness probe failed: HTTP probe failed with statuscode: 503
Normal   Killing    Container events failed liveness probe, will be restarted
```

**What happened:** `events`' own `/health` handler checks both Postgres *and* Redis and returns `503` if either is down (see `app/events/main.py`), so it's a dependency-aware endpoint — not a "is my own process alive" endpoint. With redis gone, `/health` returned `503`, so:
- **Readiness** (period 5s, threshold 2) tripped first, at ~16s — the pod went `0/1 Ready` and the `events` Service stopped routing traffic to it, exactly as intended.
- **Liveness** (period 10s, threshold 3) tripped shortly after — kubelet **killed and restarted the container**, which did nothing to fix the actual problem (Redis was still down) and just added an unnecessary restart + brief cold-start gap.
- This cascaded: `gateway`'s own `/health` aggregates `events` + `payments` health, so once `events` started returning `503`, `gateway`'s `/health` also went unhealthy, and `gateway` was **restarted by its own liveness probe too** (`kubectl describe pod -l app=gateway` shows the same `Killing … failed liveness probe` event). One downstream Redis outage caused two unrelated pods to be needlessly restarted — a direct, reproduced example of why liveness probes should never depend on downstream services (see 4.9/4.10 hint and the answer below).

After `kubectl scale deployment redis --replicas=1`, `events` (and `gateway`) recovered to `1/1 Ready` within a few seconds once the new redis pod was `Ready` and the next probe cycle passed.

### 4.11 — Add resource limits

Added to every container (`postgres`, `redis`, `gateway`, `events`, `payments`):
```yaml
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi
```

```bash
kubectl apply -f k8s/
kubectl describe nodes | grep -A 8 "Allocated resources"
```
```
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (5%)   1 (12%)
  memory             460Mi (3%)  1450Mi (9%)
```
(5 containers × 50m/64Mi requests = 250m/320Mi from app manifests; the higher totals include k3d's own system pods on the node — `450m`/`460Mi` requested, `1` CPU / `1450Mi` limit across everything scheduled.)

**What's the difference between liveness and readiness probe failure? Which one should you use for checking database connectivity, and why?**
A **readiness** failure removes the pod from the Service's list of endpoints — no traffic is routed to it — but the pod and its process keep running untouched; as soon as the probe passes again, it's added back. A **liveness** failure causes kubelet to **kill and restart the container**, on the assumption that a restart is the fix (e.g., a deadlocked process). For checking database/cache connectivity, use **readiness, not liveness** — this lab reproduced exactly why: `events`' `/health` checks Redis, and when Redis went down, the liveness probe restarted `events` (and, via the health-aggregation cascade, `gateway` too) even though restarting either process does nothing to bring Redis back. Liveness should only ever check "is *this* process's own event loop/handler still responding," never a downstream dependency — otherwise a single unrelated outage turns into a thundering herd of pointless restarts across every service that transitively depends on it.
