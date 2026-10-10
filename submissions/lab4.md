# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

Date: 2026-09-20

## Task 1 — Manifests & k3d Deployment

### Cluster

The disposable `quickticket` cluster was created with k3d. The real output was:

```text
$ kubectl get nodes
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   13m   v1.35.5+k3s1
```

### Kubernetes Manifests

The `k8s/` directory contains raw Deployment and ClusterIP Service manifests for PostgreSQL, Redis, events, payments, and gateway. The three locally built application images use the explicit `v1` tag and `imagePullPolicy: Never`.

### Workloads and Services

This snapshot was taken after the raw-manifest deployment and both failure experiments:

```text
$ kubectl get pods,svc
NAME                            READY   STATUS    RESTARTS      AGE
pod/events-5d5f9cc7df-7vl22     1/1     Running   1 (77s ago)   5m2s
pod/gateway-977767446-b6r7k     1/1     Running   1 (92s ago)   3m43s
pod/payments-cb5c4b5bf-cpjhf    1/1     Running   0             5m2s
pod/postgres-745bb5ff86-zqbl7   1/1     Running   0             11m
pod/redis-5cbdd4d5ff-22bg9      1/1     Running   0             37s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.184.81    <none>        8081/TCP   5m3s
service/gateway      ClusterIP   10.43.179.214   <none>        8080/TCP   5m3s
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    13m
service/payments     ClusterIP   10.43.150.73    <none>        8082/TCP   5m3s
service/postgres     ClusterIP   10.43.101.140   <none>        5432/TCP   11m
service/redis        ClusterIP   10.43.171.45    <none>        6379/TCP   11m
```

The non-zero events and gateway restart counts are explained by the deliberately extended Redis outage in Task 2: both `/health` endpoints include downstream dependency health, so their liveness probes eventually fired. Both workloads recovered to `1/1`.

### Database Initialization

```text
$ kubectl exec -i pod/postgres-745bb5ff86-zqbl7 -- psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql
CREATE TABLE
CREATE TABLE
INSERT 0 5

$ kubectl exec pod/postgres-745bb5ff86-zqbl7 -- psql -U quickticket -d quickticket -Atc 'SELECT count(*) FROM events;'
5
```

### Full-Stack Verification

With `kubectl port-forward svc/gateway 3080:8080` running:

```text
$ curl -fsS http://127.0.0.1:3080/events | python3 -m json.tool
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

$ curl -fsS http://127.0.0.1:3080/health | python3 -m json.tool
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

### Self-Healing Experiment

The old gateway Pod was deleted while `kubectl get pods -w` was running:

```text
gateway-977767446-xzbnn   1/1   Terminating        0   78s
gateway-977767446-b6r7k   0/1   Pending            0   0s
gateway-977767446-b6r7k   0/1   ContainerCreating  0   0s
gateway-977767446-xzbnn   0/1   Completed          0   79s
gateway-977767446-b6r7k   0/1   Running            0   1s
gateway-977767446-b6r7k   1/1   Running            0   7s
```

Measured timestamps and names:

```text
Old pod: gateway-977767446-xzbnn
New pod: gateway-977767446-b6r7k
Deleted at: 2026-09-20T16:06:47+0300
New pod Ready at: 2026-09-20T16:06:56+0300
Recovery: 8.629 seconds
```

The Deployment/ReplicaSet continuously reconciled actual state against the desired one replica, so it created the replacement automatically. In Lab 1, recovering the stopped or removed Compose container required a manual `docker compose start` or `docker compose up`. That comparison is specific to the Lab 1 setup; Compose can also be configured with restart policies, but it did not provide this Deployment-style reconciliation in that experiment.

## Task 2 — Probes & Resource Limits

### Probe Configuration

The application has `/health` routes on ports 8080, 8081, and 8082. The following fragment is from the live gateway Pod; events and payments reported the same settings on their own ports:

```text
$ kubectl describe pod -l app=gateway
Containers:
  gateway:
    Image:          quickticket-gateway:v1
    Port:           8080/TCP (http)
    State:          Running
    Ready:          True
    Restart Count:  0
    Limits:
      cpu:     200m
      memory:  256Mi
    Requests:
      cpu:      50m
      memory:   64Mi
    Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      EVENTS_URL:          http://events:8081
      PAYMENTS_URL:        http://payments:8082
      GATEWAY_TIMEOUT_MS:  5000
```

Live `kubectl describe` also showed:

```text
events:   Liveness http-get http://:8081/health delay=10s period=10s #failure=3
events:   Readiness http-get http://:8081/health delay=0s period=5s #failure=2
payments: Liveness http-get http://:8082/health delay=10s period=10s #failure=3
payments: Readiness http-get http://:8082/health delay=0s period=5s #failure=2
```

### Readiness Failure Experiment

The single node was temporarily cordoned before deleting the Redis Pod. This intentionally kept the replacement Pending long enough to cross the two-failure readiness threshold; the node was uncordoned immediately after the observation.

Before deletion:

```text
NAME                      READY   STATUS    RESTARTS   AGE     IP           NODE
events-5d5f9cc7df-7vl22   1/1     Running   0          3m2s    10.42.0.12   k3d-quickticket-server-0
redis-5cbdd4d5ff-bp6rd    1/1     Running   0          9m31s   10.42.0.10   k3d-quickticket-server-0

NAME     ENDPOINTS         AGE
events   10.42.0.12:8081   3m3s
```

During the short, controlled Redis outage:

```text
Readiness failure observed: true
Restart count before: 1

NAME                      READY   STATUS    RESTARTS       AGE     IP           NODE
events-5d5f9cc7df-7vl22   0/1     Running   1 (53s ago)   4m38s   10.42.0.12   k3d-quickticket-server-0
redis-5cbdd4d5ff-22bg9    0/1     Pending   0              13s     <none>       <none>

NAME     ENDPOINTS   AGE
events               4m39s
```

After uncordoning the node and Redis recovery:

```text
NAME                      READY   STATUS    RESTARTS       AGE     IP           NODE
events-5d5f9cc7df-7vl22   1/1     Running   1 (58s ago)   4m43s   10.42.0.12   k3d-quickticket-server-0
redis-5cbdd4d5ff-22bg9    1/1     Running   0              18s     10.42.0.16   k3d-quickticket-server-0

NAME     ENDPOINTS         AGE
events   10.42.0.12:8081   4m44s

Restart count after: 1
```

This proves the readiness failure alone removed events from the Service endpoints without an additional restart (`1 → 1`).

An earlier deliberately longer outage produced these real warnings:

```text
Warning  Unhealthy  8s              kubelet  Liveness probe failed: HTTP probe failed with statuscode: 503
Warning  Unhealthy  1s (x2 over 7s) kubelet  Readiness probe failed: HTTP probe failed with statuscode: 503
```

Because the lab requires both probes to call the same dependency-aware events `/health`, the prolonged outage eventually caused one events restart and one gateway restart. That experimentally demonstrates why dependency connectivity is a poor liveness condition.

### Resource Limits

All five Deployments specify `50m`/`64Mi` requests and `200m`/`256Mi` limits. The live Pods reported:

```text
NAME                        CPU_REQ   MEM_REQ   CPU_LIMIT   MEM_LIMIT
events-5d5f9cc7df-7vl22     50m       64Mi      200m        256Mi
gateway-977767446-b6r7k     50m       64Mi      200m        256Mi
payments-cb5c4b5bf-cpjhf    50m       64Mi      200m        256Mi
postgres-745bb5ff86-zqbl7   50m       64Mi      200m        256Mi
redis-5cbdd4d5ff-22bg9      50m       64Mi      200m        256Mi
```

Node allocation (including k3s system workloads) was:

```text
$ kubectl describe node/k3d-quickticket-server-0 | grep -A 12 'Allocated resources'
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (5%)   1 (12%)
  memory             460Mi (5%)  1450Mi (18%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
  hugepages-32Mi     0 (0%)      0 (0%)
  hugepages-64Ki     0 (0%)      0 (0%)
```

### Liveness vs Readiness

A readiness failure leaves the container running but marks its Pod NotReady and removes its address from matching Service endpoints. Traffic resumes after the readiness probe succeeds again. A liveness failure tells kubelet that the container itself is unhealthy; after `failureThreshold` consecutive failures, kubelet restarts it.

Database or Redis connectivity belongs in readiness, not liveness. Restarting a healthy application process cannot repair an unavailable external dependency and can instead create a restart loop during the dependency outage. Readiness stops routing new requests and automatically restores routing when the dependency recovers. The long-outage observation above demonstrates this distinction directly.

## Bonus — Helm Chart

Raw manifests remain in `k8s/` for Task 1, while templated copies are in `k8s/chart/templates/`.

### `Chart.yaml`

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
type: application
version: 0.1.0
appVersion: "1.0.0"
```

### `values.yaml`

```yaml
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

probes:
  liveness:
    initialDelaySeconds: 10
    periodSeconds: 10
    failureThreshold: 3
  readiness:
    periodSeconds: 5
    failureThreshold: 2

resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi
```

### Helm Validation

```text
$ helm lint k8s/chart
==> Linting k8s/chart
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
```

`helm template quickticket k8s/chart | kubectl apply --dry-run=client -f -` also validated all ten rendered resources successfully.

### Installed Release

The raw resources were deleted before the Helm install to avoid ownership and name collisions.

```text
$ helm list
NAME        NAMESPACE  REVISION  UPDATED                                  STATUS    CHART              APP VERSION
quickticket default    1         2026-09-20 16:11:02.3275 +0300 MSK      deployed  quickticket-0.1.0  1.0.0
```

### Pods After Helm Install

```text
$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
events-5d5f9cc7df-dhmp4     1/1     Running   0          24s
gateway-977767446-68bg4     1/1     Running   0          24s
payments-cb5c4b5bf-mhx6c    1/1     Running   0          24s
postgres-745bb5ff86-pnhsd   1/1     Running   0          24s
redis-5cbdd4d5ff-ncfts      1/1     Running   0          24s
```

PostgreSQL was seeded again because this lab uses no PersistentVolume. Functional verification of the Helm deployment returned:

```text
$ curl -fsS http://127.0.0.1:3080/health | python3 -m json.tool
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}

$ curl -fsS http://127.0.0.1:3080/events | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))'
5
```

### Monitoring

The optional kube-prometheus-stack installation was not performed. It is not part of the bonus acceptance criteria; the bonus proof is the linted, installed, and functionally verified QuickTicket Helm chart above.
