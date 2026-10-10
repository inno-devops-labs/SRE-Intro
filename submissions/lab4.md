# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

## Environment

- Cluster: k3d / k3s
- Kubernetes: `v1.35.5+k3s1`
- Helm: `v3.19.0`
- Branch: `feature/lab4`

## Task 1 — Write Manifests and Deploy to k3d

### 4.1 Cluster

```text
NAME                       STATUS   ROLES           AGE   VERSION        INTERNAL-IP   EXTERNAL-IP   OS-IMAGE           KERNEL-VERSION     CONTAINER-RUNTIME
k3d-quickticket-server-0   Ready    control-plane   38m   v1.35.5+k3s1   172.21.0.3    <none>        K3s v1.35.5+k3s1   6.12.68-linuxkit   containerd://2.2.3-k3s1
```

### 4.2–4.5 Manifests, images, and database initialization

I wrote Deployment and ClusterIP Service manifests for:

- PostgreSQL
- Redis
- events
- payments
- gateway

The three application images were built locally and imported into k3d with `imagePullPolicy: Never`:

```text
docker.io/library/quickticket-events        v1   05ba8b2ca14f3   57.3MB
docker.io/library/quickticket-gateway       v1   25805cceccc24   52.3MB
docker.io/library/quickticket-payments      v1   c9ba683923575   51.7MB
```

The database was initialized from `app/seed.sql`:

```text
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

### 4.6 Running workloads and Services

```text
NAME                           READY   STATUS    RESTARTS   AGE     IP           NODE
pod/events-7766cff7b9-wc7bk    1/1     Running   0          3m43s   10.42.0.21   k3d-quickticket-server-0
pod/gateway-75ff468994-qm4lf   1/1     Running   0          83s     10.42.0.23   k3d-quickticket-server-0
pod/payments-58fb468db-nhphf   1/1     Running   0          29m     10.42.0.16   k3d-quickticket-server-0
pod/postgres-7c7ffc4b-vg8tz    1/1     Running   0          33m     10.42.0.12   k3d-quickticket-server-0
pod/redis-c46d5dffc-z5tq6      1/1     Running   0          33m     10.42.0.13   k3d-quickticket-server-0

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    SELECTOR
service/events       ClusterIP   10.43.125.63    <none>        8081/TCP   app=events
service/gateway      ClusterIP   10.43.196.213   <none>        8080/TCP   app=gateway
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    <none>
service/payments     ClusterIP   10.43.11.167    <none>        8082/TCP   app=payments
service/postgres     ClusterIP   10.43.100.31    <none>        5432/TCP   app=postgres
service/redis        ClusterIP   10.43.241.135   <none>        6379/TCP   app=redis
```

### Critical-path verification through port-forward

`GET /events` returned the seeded events:

```json
[
  {
    "id": 1,
    "name": "Go Conference 2026",
    "venue": "Main Hall A",
    "date": "2026-09-15T09:00:00+00:00",
    "total_tickets": 100,
    "price_cents": 5000,
    "available": 100
  },
  {
    "id": 4,
    "name": "Python Workshop",
    "venue": "Lab 301",
    "date": "2026-09-22T14:00:00+00:00",
    "total_tickets": 25,
    "price_cents": 2000,
    "available": 25
  },
  {
    "id": 2,
    "name": "SRE Meetup",
    "venue": "Room 204",
    "date": "2026-10-01T18:00:00+00:00",
    "total_tickets": 30,
    "price_cents": 0,
    "available": 30
  },
  {
    "id": 5,
    "name": "Kubernetes Deep Dive",
    "venue": "Auditorium B",
    "date": "2026-10-10T10:00:00+00:00",
    "total_tickets": 80,
    "price_cents": 8000,
    "available": 80
  },
  {
    "id": 3,
    "name": "Cloud Native Summit",
    "venue": "Expo Center",
    "date": "2026-11-20T10:00:00+00:00",
    "total_tickets": 500,
    "price_cents": 15000,
    "available": 500
  }
]
```

`GET /health` confirmed the critical dependencies:

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

### 4.7 Kubernetes self-healing

I deleted the running gateway pod and watched the Deployment create a replacement:

```text
Old gateway pod: gateway-75ff468994-dg6gg
Deletion started: 2026-09-21T05:45:08.732005764+03:00
gateway-75ff468994-dg6gg   1/1   Terminating        0   2m20s
gateway-75ff468994-dg6gg   0/1   Completed          0   2m20s
gateway-75ff468994-qm4lf   0/1   Pending            0   0s
gateway-75ff468994-qm4lf   0/1   ContainerCreating  0   0s
gateway-75ff468994-qm4lf   1/1   Running            0   2s
pod/gateway-75ff468994-qm4lf condition met
Recovery completed: 2026-09-21T05:45:11.366452397+03:00
Gateway recovery time: 2625 ms
```

Kubernetes restored the deleted gateway pod automatically in approximately **2.6 seconds**. The Deployment controller detected that the actual replica count was below the desired replica count and created a replacement. In Lab 1, Docker Compose required a manual `docker compose start`; Kubernetes continuously reconciled the desired state without manual intervention.

### Troubleshooting note: cluster DNS

Initially, `/events` failed because CoreDNS was in `ImagePullBackOff`; consequently, names such as `events`, `payments`, `postgres`, and `redis` could not be resolved. The registry download also timed out. I created a single-platform local CoreDNS image, imported it into k3d, and configured the CoreDNS Deployment to use it with `imagePullPolicy: Never`.

After recovery, in-cluster DNS resolved every Service:

```text
events -> 10.43.125.63
payments -> 10.43.11.167
postgres -> 10.43.100.31
redis -> 10.43.241.135
```

## Task 2 — Probes and Resource Limits

### 4.9 Probe configuration

Gateway:

```text
Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

Events:

```text
Liveness:   http-get http://:8081/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:  http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

Payments:

```text
Liveness:   http-get http://:8082/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:  http-get http://:8082/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

### 4.10 Readiness failure during Redis outage

I deleted the Redis pod and temporarily held the Redis Deployment at zero replicas so that the outage lasted long enough for the readiness failure threshold to be observed.

```text
Controlled Redis outage: 2026-09-21T06:04:58.373634908+03:00
Deleting: redis-c46d5dffc-zd6d2
06:04:59 events Ready=True
06:05:10 events Ready=True
06:05:18 events Ready=False
Readiness failure observed

NAME                        READY   STATUS    RESTARTS
events-68765c76b8-xskg8     0/1     Running   0
gateway-6558d88bd8-wkdxz    1/1     Running   0
payments-68dcdf7696-5gfps   1/1     Running   0
postgres-7c7ffc4b-vg8tz     1/1     Running   0
```

The events probe reported:

```text
Warning  Unhealthy  kubelet  Readiness probe failed: HTTP probe failed with statuscode: 503
```

Redis was restored immediately. Both applications became Ready again without a container restart:

```text
NAME                       READY   RESTARTS
events-68765c76b8-xskg8    true    0
gateway-6558d88bd8-wkdxz   true    0
```

### Liveness versus readiness

A **readiness** failure removes a pod from Service traffic but does not restart its container. A **liveness** failure tells kubelet that the application process cannot recover and causes a container restart.

Database and Redis connectivity should be checked by readiness, not liveness. Restarting an application cannot repair an unavailable external dependency and can create a restart loop. Ideally, liveness should use a separate endpoint that checks only whether the application process itself is alive, while dependency-aware `/health` should be used for readiness.

During an intentionally prolonged first experiment, using the dependency-aware `/health` endpoint for liveness caused repeated events and gateway restarts. This demonstrated why dependency checks should not normally gate liveness.

### 4.11 Resource requests and limits

Each of the five Deployments has the following resources:

```text
DEPLOYMENT   CPU_REQUEST   MEMORY_REQUEST   CPU_LIMIT   MEMORY_LIMIT
postgres     50m           64Mi             200m        256Mi
redis        50m           64Mi             200m        256Mi
events       50m           64Mi             200m        256Mi
payments     50m           64Mi             200m        256Mi
gateway      50m           64Mi             200m        256Mi
```

Node allocation after applying the limits:

```text
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests     Limits
  --------           --------     ------
  cpu                450m (3%)    1 (8%)
  memory             460Mi (12%)  1450Mi (38%)
  ephemeral-storage  0 (0%)       0 (0%)
  hugepages-1Gi      0 (0%)       0 (0%)
  hugepages-2Mi      0 (0%)       0 (0%)
```

The totals include both QuickTicket and Kubernetes system workloads on the node.

## Bonus Task — Helm Chart

### Chart.yaml

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
type: application
version: 0.1.0
appVersion: "1.0.0"
```

### values.yaml

```yaml
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi

gateway:
  replicas: 1
  image: quickticket-gateway:v1
  imagePullPolicy: Never
  port: 8080
  eventsUrl: http://events:8081
  paymentsUrl: http://payments:8082
  timeoutMs: "5000"

events:
  replicas: 1
  image: quickticket-events:v1
  imagePullPolicy: Never
  port: 8081
  db:
    host: postgres
    port: "5432"
    name: quickticket
    user: quickticket
    password: quickticket
    maxConnections: "10"
  redis:
    host: redis
    port: "6379"
    timeoutMs: "1000"
  reservationTtl: "300"

payments:
  replicas: 1
  image: quickticket-payments:v1
  imagePullPolicy: Never
  port: 8082
  failureRate: "0.0"
  latencyMs: "0"

postgres:
  replicas: 1
  image: postgres:17-alpine
  port: 5432
  database: quickticket
  user: quickticket
  password: quickticket

redis:
  replicas: 1
  image: redis:7-alpine
  port: 6379
```

The chart passed both rendering and client-side Kubernetes validation:

```text
1 chart(s) linted, 0 chart(s) failed
Rendered YAML: no unresolved expressions
```

### Helm release

```text
NAME          NAMESPACE  REVISION  STATUS    CHART               APP VERSION
quickticket   default    1         deployed  quickticket-0.1.0   1.0.0
```

Pods after Helm installation:

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-675d86c77-5fngd      1/1     Running   0          78s
gateway-7cd55d8774-skp67    1/1     Running   0          78s
payments-d7dc94485-h4r79    1/1     Running   0          78s
postgres-78489d7f5f-22s6k   1/1     Running   0          78s
redis-6fcfb5475d-gnmfg      1/1     Running   0          78s
```

After installing through Helm and reseeding PostgreSQL, `/events` returned all five records and `/health` returned `healthy`.

The optional `kube-prometheus-stack` installation was not required for the Helm-chart bonus and was not installed.
