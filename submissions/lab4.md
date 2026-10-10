# Lab 4 Submission — Kubernetes: Deploy QuickTicket to a Cluster

## Task 1 — Write Manifests and Deploy to k3d

### 4.1 — Create a k3d Cluster

I created a local k3d cluster named `quickticket` with one k3s control-plane node.

Commands:

```bash
k3d cluster create quickticket
kubectl get nodes -o wide
```

Observed node status:

```text
NAME                       STATUS   ROLES           AGE   VERSION        INTERNAL-IP   EXTERNAL-IP   OS-IMAGE           KERNEL-VERSION     CONTAINER-RUNTIME
k3d-quickticket-server-0   Ready    control-plane   9s    v1.35.5+k3s1   172.22.0.3    <none>        K3s v1.35.5+k3s1   6.12.68-linuxkit   containerd://2.2.3-k3s1

```

The node reached the `Ready` state.

### 4.2 — Build and Import Images

I built the three application images locally:

```bash
docker build -t quickticket-gateway:v1 ./app/gateway
docker build -t quickticket-events:v1 ./app/events
docker build -t quickticket-payments:v1 ./app/payments
k3d image import quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 -c quickticket
```

Local images:

```text
REPOSITORY                  TAG                IMAGE ID       SIZE
quickticket-events          v1                 d119601ed308   272MB
quickticket-payments        v1                 554b1b2051a4   249MB
quickticket-gateway         v1                 b96dd53772da   251MB

```

Images verified inside the k3d node:

```text
IMAGE                                        TAG                     IMAGE ID            SIZE
docker.io/library/quickticket-events         v1                      9974092b62be9       61MB
docker.io/library/quickticket-gateway        v1                      4e0936917cfe2       55.7MB
docker.io/library/quickticket-payments       v1                      d527c8a8c2987       55.2MB

```

The application Deployments use `imagePullPolicy: Never` to use the imported
images rather than pulling them from Docker Hub.

### 4.3–4.4 — Kubernetes Manifests

I wrote five raw manifest files, each containing one Deployment and one
ClusterIP Service:

- [PostgreSQL](../k8s/postgres.yaml)
- [Redis](../k8s/redis.yaml)
- [Events](../k8s/events.yaml)
- [Payments](../k8s/payments.yaml)
- [Gateway](../k8s/gateway.yaml)

Deployment selectors match their pod template labels. The application uses
Service DNS names: `postgres:5432`, `redis:6379`, `events:8081`, and
`payments:8082`. The gateway Service exposes port `8080`.

I deployed PostgreSQL and Redis first and waited for their readiness before
deploying the application services.

### 4.5 — Initialize PostgreSQL

I loaded `app/seed.sql` through standard input using `kubectl exec -i` and
`psql -v ON_ERROR_STOP=1 -U quickticket -d quickticket -f /dev/stdin`.

Seed output:

```text
CREATE TABLE
CREATE TABLE
INSERT 0 5

```

The script created the `events` and `orders` tables and inserted five events.

### 4.6 — Verify the Full Stack

Output of `kubectl get pods,svc -o wide`:

```text
NAME                            READY   STATUS    RESTARTS   AGE    IP           NODE                       NOMINATED NODE   READINESS GATES
pod/events-6db7d49668-bp2pp     1/1     Running   0          13s    10.42.0.11   k3d-quickticket-server-0   <none>           <none>
pod/gateway-548fb4fc9-2xpnj     1/1     Running   0          13s    10.42.0.13   k3d-quickticket-server-0   <none>           <none>
pod/payments-6f594669c6-r2d6m   1/1     Running   0          13s    10.42.0.12   k3d-quickticket-server-0   <none>           <none>
pod/postgres-f5754f487-dbpgj    1/1     Running   0          102s   10.42.0.10   k3d-quickticket-server-0   <none>           <none>
pod/redis-68f999b745-nj7wq      1/1     Running   0          102s   10.42.0.9    k3d-quickticket-server-0   <none>           <none>

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE    SELECTOR
service/events       ClusterIP   10.43.25.37     <none>        8081/TCP   13s    app=events
service/gateway      ClusterIP   10.43.100.115   <none>        8080/TCP   13s    app=gateway
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    12m    <none>
service/payments     ClusterIP   10.43.37.82     <none>        8082/TCP   13s    app=payments
service/postgres     ClusterIP   10.43.248.3     <none>        5432/TCP   102s   app=postgres
service/redis        ClusterIP   10.43.119.99    <none>        6379/TCP   102s   app=redis

```

I stopped the old Compose gateway to release local port `3080`, then tested
the Kubernetes gateway through port-forwarding:

```bash
kubectl port-forward svc/gateway 3080:8080
curl -fsS http://localhost:3080/events
curl -fsS http://localhost:3080/health
```

Events response:

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

Gateway health response:

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

Both requests returned HTTP `200`. The events response demonstrates the
gateway-to-events-to-database path. The health response also reports healthy
payments and a closed circuit breaker. I stopped port-forwarding afterward.

### 4.7–4.8 — Kubernetes Self-Healing and Recovery Comparison

I watched gateway pods while deleting the existing pod:

```bash
kubectl get pods -l app=gateway -w --output-watch-events
kubectl delete pod -l app=gateway --wait=false
```

Observed transitions:

```text
EVENT      NAME                      READY   STATUS    RESTARTS   AGE
ADDED      gateway-548fb4fc9-2xpnj   1/1     Running   0          3m5s
MODIFIED   gateway-548fb4fc9-2xpnj   1/1     Terminating   0          3m8s
MODIFIED   gateway-548fb4fc9-2xpnj   1/1     Terminating   0          3m8s
ADDED      gateway-548fb4fc9-dmhr2   0/1     Pending       0          0s
MODIFIED   gateway-548fb4fc9-dmhr2   0/1     Pending       0          0s
MODIFIED   gateway-548fb4fc9-dmhr2   0/1     ContainerCreating   0          0s
MODIFIED   gateway-548fb4fc9-2xpnj   0/1     Completed           0          3m8s
MODIFIED   gateway-548fb4fc9-dmhr2   0/1     Running             0          1s
MODIFIED   gateway-548fb4fc9-2xpnj   0/1     Completed           0          3m9s
DELETED    gateway-548fb4fc9-2xpnj   0/1     Completed           0          3m9s
MODIFIED   gateway-548fb4fc9-dmhr2   1/1     Running             0          7s

```

Recovery measurement:

```text
OLD_GATEWAY_POD=gateway-548fb4fc9-2xpnj
NEW_GATEWAY_POD=gateway-548fb4fc9-dmhr2
DELETION_TIME=2026-09-14T10:28:17Z
RECOVERY_TIME=2026-09-14T10:28:25Z
RECOVERY_SECONDS=8.89

```

The measured interval from immediately before the delete command until the
replacement pod was observed Ready was **8.89 seconds**, including command
and polling overhead. The new pod passed through `Pending`,
`ContainerCreating`, `Running`, and `1/1 Ready`.

The Deployment's ReplicaSet automatically created a replacement to restore
the desired replica count. No manual application start was required.

In Lab 1, after intentionally stopping a Compose service, I had to run
`docker compose start` to restore it. This compares automatic Kubernetes
replacement with the manual recovery procedure used in that experiment;
it does not imply that Docker cannot restart crashed containers when an
appropriate restart policy is configured.

## Task 2 — Probes and Resource Limits

### 4.9 — Liveness and Readiness Probes

Gateway probe configuration:

```text
    Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8080/health delay=5s timeout=1s period=5s #success=1 #failure=2
    Environment:
      EVENTS_URL:          http://events:8081
      PAYMENTS_URL:        http://payments:8082
      GATEWAY_TIMEOUT_MS:  5000
    Mounts:
      /var/run/secrets/kubernetes.io/serviceaccount from kube-api-access-rmb7j (ro)

```

Events probe configuration:

```text
    Liveness:   http-get http://:8081/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8081/health delay=5s timeout=1s period=5s #success=1 #failure=2
    Environment:
      DB_HOST:           postgres
      DB_PORT:           5432
      DB_NAME:           quickticket
      DB_USER:           quickticket
      DB_PASS:           quickticket

```

Equivalent HTTP probes are configured for payments on port `8082`.
PostgreSQL uses `pg_isready`, and Redis uses `redis-cli ping`.

### 4.10 — Readiness Failure During Redis Outage

I deleted Redis and temporarily scaled its Deployment to zero replicas to
keep the outage long enough for the readiness failure threshold to be
reached. I then restored one replica. This was a deliberately extended
dependency outage, not just an unmodified pod-deletion test.

Watch output:

```text
EVENT      NAME                      READY   STATUS    RESTARTS   AGE
ADDED      events-6db7d49668-bp2pp   1/1     Running   0          4m47s
ADDED      redis-68f999b745-nj7wq    1/1     Running   0          6m16s
MODIFIED   redis-68f999b745-nj7wq    1/1     Terminating   0          6m18s
MODIFIED   redis-68f999b745-nj7wq    1/1     Terminating   0          6m18s
ADDED      redis-68f999b745-czkkl    0/1     Pending       0          0s
MODIFIED   redis-68f999b745-czkkl    0/1     Pending       0          0s
MODIFIED   redis-68f999b745-czkkl    0/1     Terminating   0          0s
MODIFIED   redis-68f999b745-czkkl    0/1     Terminating   0          0s
MODIFIED   redis-68f999b745-nj7wq    0/1     Completed     0          6m19s
MODIFIED   redis-68f999b745-czkkl    0/1     Terminating   0          1s
MODIFIED   redis-68f999b745-nj7wq    0/1     Completed     0          6m19s
DELETED    redis-68f999b745-nj7wq    0/1     Completed     0          6m19s
MODIFIED   redis-68f999b745-czkkl    0/1     ContainerStatusUnknown   0          2s
MODIFIED   redis-68f999b745-czkkl    0/1     ContainerStatusUnknown   0          2s
DELETED    redis-68f999b745-czkkl    0/1     ContainerStatusUnknown   0          2s
MODIFIED   events-6db7d49668-bp2pp   0/1     Running                  0          4m59s
ADDED      redis-68f999b745-cgkbp    0/1     Pending                  0          0s
MODIFIED   redis-68f999b745-cgkbp    0/1     Pending                  0          0s
MODIFIED   redis-68f999b745-cgkbp    0/1     ContainerCreating        0          0s
MODIFIED   redis-68f999b745-cgkbp    0/1     Running                  0          1s
MODIFIED   redis-68f999b745-cgkbp    1/1     Running                  0          7s
MODIFIED   events-6db7d49668-bp2pp   1/1     Running                  0          5m9s

```

Events pod during the outage:

```text
NAME                      READY   STATUS    RESTARTS   AGE     IP           NODE                       NOMINATED NODE   READINESS GATES
events-6db7d49668-bp2pp   0/1     Running   0          4m59s   10.42.0.11   k3d-quickticket-server-0   <none>           <none>

```

Recorded probe failures:

```text
  Normal   Created    4m59s            kubelet            spec.containers{events}: Container created
  Normal   Started    4m59s            kubelet            spec.containers{events}: Container started
  Warning  Unhealthy  5s               kubelet            spec.containers{events}: Readiness probe failed: Get "http://10.42.0.11:8081/health": context deadline exceeded (Client.Timeout exceeded while awaiting headers)
  Warning  Unhealthy  1s (x2 over 1s)  kubelet            spec.containers{events}: Readiness probe failed: HTTP probe failed with statuscode: 503

```

Pods after recovery:

```text
NAME                      READY   STATUS    RESTARTS   AGE     IP           NODE                       NOMINATED NODE   READINESS GATES
events-6db7d49668-bp2pp   1/1     Running   0          5m12s   10.42.0.11   k3d-quickticket-server-0   <none>           <none>
redis-68f999b745-cgkbp    1/1     Running   0          12s     10.42.0.15   k3d-quickticket-server-0   <none>           <none>

```

Events became `0/1 Ready` while remaining `Running`, with zero restarts.
After Redis recovered, the same events pod returned to `1/1 Ready`.

A NotReady endpoint is excluded from normal Service routing. Its address
can still appear in an EndpointSlice: the `-o wide` output does not show
the endpoint's `conditions.ready` value. Therefore, the address remaining
visible is not evidence that the pod continued receiving Service traffic.

### Liveness vs Readiness

A readiness failure marks the pod unavailable for normal Service traffic
without restarting its container. A liveness failure restarts the container
after the configured failure threshold.

Database connectivity belongs in readiness: restarting an application
cannot fix a failed database and may cause unnecessary restart loops.
Liveness should normally check the application's own ability to function,
independently of external dependencies.

For this lab, both HTTP probes use `/health` as specified in the exercise.
Because events `/health` checks PostgreSQL and Redis, a sufficiently long
dependency outage could also trigger its liveness probe. The short outage
tested here did not restart events. A production setup should separate
dependency-aware readiness from dependency-independent liveness.

### 4.11 — Resource Requests and Limits

Configured resources:

```text
POD                         CONTAINER   CPU_REQUEST   MEMORY_REQUEST   CPU_LIMIT   MEMORY_LIMIT
events-6db7d49668-bp2pp     events      50m           64Mi             200m        256Mi
gateway-548fb4fc9-dmhr2     gateway     50m           64Mi             200m        256Mi
payments-6f594669c6-r2d6m   payments    50m           64Mi             200m        256Mi
postgres-f5754f487-dbpgj    postgres    50m           64Mi             200m        256Mi
redis-68f999b745-cgkbp      redis       50m           64Mi             200m        256Mi

```

Node allocation from kubectl describe node:

```text
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (5%)   1 (12%)
  memory             460Mi (7%)  1450Mi (24%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
  hugepages-32Mi     0 (0%)      0 (0%)
  hugepages-64Ki     0 (0%)      0 (0%)
Events:
  Type    Reason                          Age   From                   Message

```

Each of the five containers requests `50m` CPU and `64Mi` memory, with
limits of `200m` CPU and `256Mi` memory. Node allocation totals also include
Kubernetes system components.

## Bonus Task — Helm Chart

### B.1–B.2 — Chart and Templates

I retained the raw manifests for Task 1 and created a separate chart:

- `k8s/chart/Chart.yaml`
- `k8s/chart/values.yaml`
- `k8s/chart/templates/_helpers.tpl`
- `k8s/chart/templates/applications.yaml`
- `k8s/chart/templates/infrastructure.yaml`

The chart exposes images, replicas, application environment settings,
Service ports, application probe timing, and shared resources through values.
The rendered output contains five Deployments and five Services.

Chart.yaml:

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
type: application
version: 0.1.0
appVersion: "1.0.0"

```

values.yaml:

```yaml
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

probes:
  liveness:
    initialDelaySeconds: 10
    periodSeconds: 10
    failureThreshold: 3
  readiness:
    initialDelaySeconds: 5
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

Helm lint result:

```text
==> Linting k8s/chart
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed

```

### B.3 — Install and Verify

I deleted only the five raw Deployments and their Services from the cluster,
retaining the source files. Then I installed the chart:

```bash
helm install quickticket k8s/chart
```

PostgreSQL has no persistent volume in these lab manifests, so I loaded
`app/seed.sql` again into the new PostgreSQL pod.

Installed release:

```text
NAME       	NAMESPACE	REVISION	UPDATED                             	STATUS  	CHART            	APP VERSION
quickticket	default  	1       	2026-09-14 13:35:14.893829 +0300 MSK	deployed	quickticket-0.1.0	1.0.0

```

Pods after Helm installation:

```text
NAME                        READY   STATUS    RESTARTS   AGE   IP           NODE                       NOMINATED NODE   READINESS GATES
events-c655bb4cd-w98g6      1/1     Running   0          18s   10.42.0.18   k3d-quickticket-server-0   <none>           <none>
gateway-bcd46f546-l8zqw     1/1     Running   0          18s   10.42.0.19   k3d-quickticket-server-0   <none>           <none>
payments-849d699bfb-sm2nr   1/1     Running   0          18s   10.42.0.17   k3d-quickticket-server-0   <none>           <none>
postgres-967779f67-rxkzd    1/1     Running   0          18s   10.42.0.20   k3d-quickticket-server-0   <none>           <none>
redis-544858dfb9-lrkgd      1/1     Running   0          18s   10.42.0.16   k3d-quickticket-server-0   <none>           <none>

```

Events response through the Helm-managed gateway port-forward:

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

Health response through the same port-forward:

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

All five Helm-managed pods reached `1/1 Running` with zero restarts, and
both application requests succeeded.

### B.4 — Optional Monitoring

I did not install `kube-prometheus-stack`. No pod count is reported for it,
because the optional monitoring release was not deployed.

## Conclusion

QuickTicket was deployed to k3d using raw Deployments and Services and then
installed successfully through Helm. Evidence demonstrates working
port-forwarded requests, automatic gateway pod replacement in 8.89 seconds,
readiness failure and recovery during a Redis outage, and resource requests
and limits on all five workloads.

This is a local learning deployment: database storage is ephemeral and
the database credentials are the exercise's explicitly prescribed test
values, not production credentials.
