# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

## Task 1 — Write Manifests & Deploy to k3d

Manifests written from scratch live in `k8s/`: `postgres.yaml`, `redis.yaml`,
`events.yaml`, `payments.yaml`, `gateway.yaml` (each a Deployment + ClusterIP
Service). The three app Deployments use `imagePullPolicy: Never` so k3d uses the
locally imported images.

### 4.1 — `kubectl get nodes`

```
NAME                       STATUS   ROLES           AGE     VERSION
k3d-quickticket-server-0   Ready    control-plane   3m55s   v1.35.5+k3s1
```

### 4.2 — Images built and imported

```
quickticket-events:v1     a56b1c2ad23a   260MB
quickticket-gateway:v1    4ee7c2372917   239MB
quickticket-payments:v1   be5b68dc59b3   237MB
# k3d image import ... -> Successfully imported 3 image(s) into 1 cluster(s)
```

### 4.2–4.5 — `kubectl get pods,svc` (all running)

Deploy order: `postgres` + `redis` first, seed the DB, then the three app
services. (Because K8s has no `depends_on`, postgres/redis are applied and made
Ready first; events then connects on first start.)

```
NAME                           READY   STATUS    RESTARTS   AGE
pod/events-675d86c77-97ggd     1/1     Running   0          46s
pod/gateway-7cd55d8774-kwzs5   1/1     Running   0          46s
pod/payments-d7dc94485-zklql   1/1     Running   0          46s
pod/postgres-d5bf8446f-4667z   1/1     Running   0          72s
pod/redis-b8f877f88-8x9jb      1/1     Running   0          72s

NAME                 TYPE        CLUSTER-IP      PORT(S)    AGE
service/events       ClusterIP   10.43.182.19    8081/TCP   46s
service/gateway      ClusterIP   10.43.135.224   8080/TCP   46s
service/payments     ClusterIP   10.43.199.223   8082/TCP   46s
service/postgres     ClusterIP   10.43.133.140   5432/TCP   72s
service/redis        ClusterIP   10.43.18.231    6379/TCP   72s
service/kubernetes   ClusterIP   10.43.0.1       443/TCP    3m56s
```

DB seeded with `kubectl exec ... psql ... < app/seed.sql` → `CREATE TABLE`,
`CREATE TABLE`, `INSERT 0 5`.

### 4.6 — Full stack via port-forward (`kubectl port-forward svc/gateway 3080:8080`)

`curl localhost:3080/events`:

```json
[
  { "id": 1, "name": "Go Conference 2026", "venue": "Main Hall A",
    "date": "2026-09-15T09:00:00+00:00", "total_tickets": 100,
    "price_cents": 5000, "available": 100 },
  { "id": 4, "name": "Python Workshop", ... "available": 25 },
  { "id": 2, "name": "SRE Meetup", ... }
  ...
]
```

`curl localhost:3080/health`:

```json
{ "status": "healthy",
  "checks": { "events": "ok", "payments": "ok", "circuit_payments": "CLOSED" } }
```

The full gateway → events → postgres/redis and gateway → payments paths work
end-to-end inside the cluster.

### 4.7 — Self-healing (`kubectl delete pod -l app=gateway`)

```
deleted at 00:14:53
00:14:54 new pod Ready after 2.83s
NAME                       READY   STATUS    RESTARTS   AGE
gateway-7cd55d8774-5sgk7   1/1     Running   0          3s
```

### How long did K8s take to recreate the deleted pod? vs docker-compose?

The Deployment's ReplicaSet noticed the missing pod and scheduled a replacement
**immediately** — a new gateway pod was `Running` and `Ready` in **~3 seconds**,
with **no human action**. In Lab 1 a stopped container stayed down until I ran
`docker compose start` manually; Compose `restart:` policies only react to a
process exiting, not to a deleted/missing container, and there is no controller
continuously reconciling desired vs actual state. Kubernetes' control loop
(Deployment → ReplicaSet → Pod) treats "1 replica" as a *declared desired state*
and self-heals automatically, which is the core operational difference.

---

## Task 2 — Probes & Resource Limits

Readiness + liveness probes and resource requests/limits are baked into all
manifests (`k8s/*.yaml`). App services probe `GET /health`; postgres/redis use
`pg_isready` / `redis-cli ping` exec readiness probes.

### 4.9 — `kubectl describe pod` showing probes configured

```
events    Liveness:   http-get http://:8081/health delay=10s timeout=1s period=10s #failure=3
          Readiness:  http-get http://:8081/health delay=0s  timeout=1s period=5s  #failure=2
gateway   Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #failure=3
          Readiness:  http-get http://:8080/health delay=0s  timeout=1s period=5s  #failure=2
```

### 4.10 — Readiness probe failure during Redis outage

`events /health` returns 503 when redis (or postgres) is unreachable. To force a
*sustained* outage I scaled redis to 0 (deleting the pod alone self-heals in ~2s,
too fast to observe). The events pod dropped to `0/1 Ready`:

```
00:16:45 scale redis --replicas=0
00:16:45 events: 1/1 Running
...
00:17:04 events: 0/1 Running      <- readiness probe failing
00:17:20 events: 0/1 Running
```

`kubectl describe pod -l app=events` events:

```
Warning  Unhealthy  Readiness probe failed: ... 8081/health: context deadline exceeded
Warning  Unhealthy  Readiness probe failed: HTTP probe failed with statuscode: 503
Warning  Unhealthy  Liveness probe failed:  HTTP probe failed with statuscode: 503
Normal   Killing    Container events failed liveness probe, will be restarted
```

`kubectl get endpoints events` → **empty** while `0/1 Ready`: K8s removed the pod
from the Service endpoints, so no traffic was routed to it. After
`kubectl scale deployment/redis --replicas=1`, the probe passed again and the pod
returned to `1/1 Ready` and back into the endpoints.

> Note this run also demonstrated the *anti-pattern*: because the **liveness**
> probe on events also hits the dependency-gated `/health`, the kubelet killed and
> restarted the events pod (`RESTARTS 2`) even though restarting cannot fix a down
> Redis. The same cascaded to the gateway (whose `/health` is gated on events).

### 4.11 — Resource limits / node allocation

Each container: `requests cpu=50m mem=64Mi`, `limits cpu=200m mem=256Mi`.

```
Allocated resources:
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (11%)  1 (25%)
  memory             460Mi (7%)  1450Mi (24%)
```

(5 app/db pods × 50m = 250m of the 450m requests; the remainder is k3s system
pods — coredns, traefik, metrics-server, local-path-provisioner.)

### Liveness vs readiness for DB connectivity?

- **Readiness probe failure** → the pod is removed from the Service's endpoints so
  it receives no traffic, but the pod is **not restarted**. It rejoins automatically
  once the probe passes again.
- **Liveness probe failure** → the kubelet **kills and restarts** the container.

For checking **database (or any dependency) connectivity you should use a
readiness probe, not liveness.** If the DB is down, you want to stop routing
traffic to the pod (readiness) until the DB recovers — restarting the pod
(liveness) won't bring the database back, it just adds churn, loses warm state,
and can cause a restart storm that cascades to upstream services (exactly what was
observed above when events/gateway flapped during the Redis outage). Liveness
should only check whether *this* process is itself wedged (e.g. a deadlock),
independent of external dependencies.

---

## Bonus Task — Helm Chart

Raw manifests were converted into a Helm chart under `k8s/chart/`
(`Chart.yaml`, `values.yaml`, `templates/` for all five components) with hardcoded
values replaced by `{{ .Values.* }}` references. `helm lint` passes and
`helm template` renders identically to the raw manifests
(`kubectl apply --dry-run` → all 10 objects `unchanged`).

### Chart.yaml

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
```

### values.yaml (excerpt)

```yaml
gateway:   { replicas: 1, image: quickticket-gateway:v1, timeoutMs: "5000" }
events:
  replicas: 1
  image: quickticket-events:v1
  db:    { host: postgres, port: 5432, name: quickticket, user: quickticket, password: quickticket, maxConns: "10" }
  redis: { host: redis, port: 6379, timeoutMs: "1000" }
  reservationTtl: "300"
payments:  { replicas: 1, image: quickticket-payments:v1, failureRate: "0.0", latencyMs: "0" }
postgres:  { image: postgres:17-alpine, db: { name: quickticket, user: quickticket, password: quickticket } }
redis:     { image: redis:7-alpine }
resources: { requests: { cpu: 50m, memory: 64Mi }, limits: { cpu: 200m, memory: 256Mi } }
```

### Install + verify (`kubectl delete -f k8s/*.yaml` then `helm install quickticket k8s/chart/`)

`helm list`:

```
NAME         NAMESPACE  REVISION  STATUS    CHART              APP VERSION
quickticket  default    1         deployed  quickticket-0.1.0
```

`kubectl get pods` after Helm install:

```
NAME                       READY   STATUS    RESTARTS   AGE
events-675d86c77-p4x8m     1/1     Running   0          36s
gateway-7cd55d8774-cftfx   1/1     Running   0          36s
payments-d7dc94485-6m97x   1/1     Running   0          36s
postgres-d5bf8446f-hdrwc   1/1     Running   0          36s
redis-b8f877f88-97xdk      1/1     Running   0          36s
```

Re-seeded the fresh DB and confirmed the Helm-deployed stack via port-forward:
`/events` returned all 5 seeded events and `/health` reported
`{"status":"healthy", ...}`.

### B.4 — monitoring via Helm

Optional. The `kube-prometheus-stack` install was not run in this submission;
typically it creates ~8–10 pods (Prometheus + Alertmanager StatefulSets, Grafana,
kube-state-metrics, node-exporter DaemonSet, and the prometheus-operator).
