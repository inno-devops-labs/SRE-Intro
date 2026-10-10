# Lab 4 - Kubernetes: Deploy QuickTicket to a Cluster

## Task 1 - Write Manifests & Deploy to k3d

### k3d cluster

```text
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   9s    v1.35.5+k3s1
```

### Local images

```text
quickticket-events:v1                                  ff9e7f3c8364        272MB           61MB
quickticket-gateway:v1                                 f4f12624eaa9        251MB         55.7MB
quickticket-payments:v1                                516251f428e9        249MB         55.2MB
```

### Raw manifests deployed

```text
NAME                            READY   STATUS    RESTARTS   AGE
pod/events-54697f46fd-dd8sn     1/1     Running   0          8s
pod/gateway-6fb7cf8bbc-mv9jn    1/1     Running   0          7s
pod/payments-d7dc94485-fbjb4    1/1     Running   0          7s
pod/postgres-599c58465c-2gd4v   1/1     Running   0          29s
pod/redis-7fbfd89858-zdqcd      1/1     Running   0          29s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.86.222    <none>        8081/TCP   8s
service/gateway      ClusterIP   10.43.93.51     <none>        8080/TCP   7s
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    117s
service/payments     ClusterIP   10.43.102.48    <none>        8082/TCP   7s
service/postgres     ClusterIP   10.43.249.106   <none>        5432/TCP   29s
service/redis        ClusterIP   10.43.120.195   <none>        6379/TCP   29s
```

Database initialization:

```text
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

### Port-forward verification

`curl http://localhost:3080/events`:

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

`curl http://localhost:3080/health`:

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

### Self-healing

Gateway pod deletion and recovery:

```text
delete_start=2026-09-16T21:27:57Z
NAME                       READY   STATUS    RESTARTS   AGE
gateway-7d57cf67bd-2l96x   1/1     Running   0          2m40s
pod "gateway-7d57cf67bd-2l96x" deleted from default namespace
tick=2026-09-16T21:27:59Z
NAME                       READY   STATUS    RESTARTS   AGE
gateway-7d57cf67bd-hxr2t   0/1     Running   0          2s
tick=2026-09-16T21:28:02Z
NAME                       READY   STATUS    RESTARTS   AGE
gateway-7d57cf67bd-hxr2t   0/1     Running   0          5s
tick=2026-09-16T21:28:05Z
NAME                       READY   STATUS    RESTARTS   AGE
gateway-7d57cf67bd-hxr2t   1/1     Running   0          8s
ready_time=2026-09-16T21:28:05Z
NAME                       READY   STATUS    RESTARTS   AGE
gateway-7d57cf67bd-hxr2t   1/1     Running   0          8s
```

Kubernetes recreated the deleted gateway pod and made it ready in about 8 seconds. In Lab 1 with docker-compose, stopping a container required a manual `docker compose start`; Kubernetes reconciled the desired state automatically through the Deployment controller.

## Task 2 - Probes & Resource Limits

### Probes configured

Gateway:

```text
Liveness:   http-get http://:8080/metrics delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

Events:

```text
Liveness:   http-get http://:8081/metrics delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:  http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

Payments:

```text
Liveness:   http-get http://:8082/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:  http-get http://:8082/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

### Readiness failure during Redis outage

Redis scaled to zero:

```text
redis_scale0_long_start=2026-09-16T21:35:21Z
deployment.apps/redis scaled
tick=2026-09-16T21:35:42Z
NAME                      READY   STATUS    RESTARTS   AGE
events-6f566894f7-6pvcv   0/1     Running   0          3m48s
tick=2026-09-16T21:36:09Z
NAME                      READY   STATUS    RESTARTS   AGE
events-6f566894f7-6pvcv   0/1     Running   0          4m15s
```

Events pod conditions while Redis was unavailable:

```text
Conditions:
  Type                        Status
  PodReadyToStartContainers   True
  Initialized                 True
  Ready                       False
  ContainersReady             False
  PodScheduled                True

Events:
  Warning  Unhealthy  7s (x5 over 37s)   kubelet  spec.containers{events}: Readiness probe failed: HTTP probe failed with statuscode: 503
  Warning  Unhealthy  1s (x6 over 116s)  kubelet  spec.containers{events}: Readiness probe failed: Get "http://10.42.0.24:8081/health": context deadline exceeded (Client.Timeout exceeded while awaiting headers)
```

Recovery after Redis returned:

```text
deployment.apps/redis scaled
deployment.apps/redis condition met
deployment.apps/events condition met
NAME                        READY   STATUS    RESTARTS   AGE
events-6f566894f7-6pvcv     1/1     Running   0          4m28s
gateway-646cf59778-p68p2    0/1     Running   0          4m28s
payments-d7dc94485-x9fbm    1/1     Running   0          4m46s
postgres-599c58465c-wkphj   1/1     Running   0          4m46s
redis-7fbfd89858-kb8xt      1/1     Running   0          7s
```

Final clean state:

```text
deployment.apps/gateway condition met
NAME                        READY   STATUS    RESTARTS   AGE
events-6f566894f7-6pvcv     1/1     Running   0          4m46s
gateway-646cf59778-p68p2    1/1     Running   0          4m46s
payments-d7dc94485-x9fbm    1/1     Running   0          5m4s
postgres-599c58465c-wkphj   1/1     Running   0          5m4s
redis-7fbfd89858-kb8xt      1/1     Running   0          25s
```

### Resource allocation

```text
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests     Limits
  --------           --------     ------
  cpu                450m (22%)   1 (50%)
  memory             460Mi (23%)  1450Mi (74%)
  ephemeral-storage  0 (0%)       0 (0%)
```

### Liveness vs readiness

Readiness failure means the pod stays running but is removed from Service endpoints, so traffic is not routed to it until the check passes again. Liveness failure means Kubernetes kills and restarts the container.

For checking database or Redis connectivity, readiness is the safer probe. If a dependency is down, restarting the application pod does not fix the dependency; it can create a restart loop and make debugging harder. Readiness communicates "this pod cannot serve traffic right now" without destroying the process.

## Bonus Task - Helm Chart

### Chart.yaml

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
```

### values.yaml

```yaml
gateway:
  replicas: 1
  image: quickticket-gateway:v1
  eventsUrl: http://events:8081
  paymentsUrl: http://payments:8082
  timeoutMs: "5000"

events:
  replicas: 1
  image: quickticket-events:v1
  db:
    host: postgres
    port: "5432"
    name: quickticket
    user: quickticket
    password: quickticket
    maxConns: "10"
  redis:
    host: redis
    port: "6379"
    timeoutMs: "1000"
  reservationTtl: "300"

payments:
  replicas: 1
  image: quickticket-payments:v1
  failureRate: "0.0"
  latencyMs: "0"

postgres:
  replicas: 1
  image: postgres:17-alpine
  database: quickticket
  user: quickticket
  password: quickticket

redis:
  replicas: 1
  image: redis:7-alpine

resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi
```

Helm lint:

```text
==> Linting k8s/chart
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
```

Helm install:

```text
NAME: quickticket
LAST DEPLOYED: Thu Sep 17 00:31:36 2026
NAMESPACE: default
STATUS: deployed
REVISION: 1
DESCRIPTION: Install complete
```

Helm list:

```text
NAME        NAMESPACE  REVISION  UPDATED                              STATUS    CHART              APP VERSION
quickticket default    1         2026-09-17 00:31:36.241578 +0300 MSK deployed  quickticket-0.1.0
```

Pods and services after Helm install:

```text
NAME                            READY   STATUS    RESTARTS   AGE
pod/events-6f566894f7-6pvcv     1/1     Running   0          110s
pod/gateway-646cf59778-p68p2    1/1     Running   0          110s
pod/payments-d7dc94485-x9fbm    1/1     Running   0          2m8s
pod/postgres-599c58465c-wkphj   1/1     Running   0          2m8s
pod/redis-7fbfd89858-mzk9t      1/1     Running   0          2m8s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.236.208   <none>        8081/TCP   2m8s
service/gateway      ClusterIP   10.43.32.78     <none>        8080/TCP   2m8s
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    10m
service/payments     ClusterIP   10.43.70.10     <none>        8082/TCP   2m8s
service/postgres     ClusterIP   10.43.117.87    <none>        5432/TCP   2m8s
service/redis        ClusterIP   10.43.62.2      <none>        6379/TCP   2m8s
```

Application still worked after Helm install:

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

I did not install kube-prometheus-stack for the bonus because the Helm chart requirement was already satisfied and monitoring deployment was optional in the lab text.
