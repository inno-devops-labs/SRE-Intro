# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

Test date: 21 September 2026. A separate k3d cluster named `quickticket` was used. Port-forward used localhost:3180 because the Lab 3 Compose stack already occupied port 3080.

## Task 1 — Manifests and deployment

Five manifests in `k8s/` define a Deployment and ClusterIP Service for PostgreSQL, Redis, gateway, events and payments. The three application images were built locally and imported into k3d with `imagePullPolicy: Never`. The database was initialized using `app/seed.sql`.

### Nodes

```text
NAME                       STATUS   ROLES           AGE     VERSION
k3d-quickticket-server-0   Ready    control-plane   4m26s   v1.35.5+k3s1
```

### Pods and Services

```text
NAME                            READY   STATUS    RESTARTS   AGE
pod/events-675d86c77-vsqc7      1/1     Running   0          2m24s
pod/gateway-7cd55d8774-8hwcj    1/1     Running   0          9s
pod/payments-d7dc94485-8gks8    1/1     Running   0          2m24s
pod/postgres-78489d7f5f-j775x   1/1     Running   0          2m24s
pod/redis-6fcfb5475d-4txmb      1/1     Running   0          35s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.111.110   <none>        8081/TCP   3m38s
service/gateway      ClusterIP   10.43.246.30    <none>        8080/TCP   3m38s
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    4m31s
service/payments     ClusterIP   10.43.208.122   <none>        8082/TCP   3m38s
service/postgres     ClusterIP   10.43.21.188    <none>        5432/TCP   3m41s
service/redis        ClusterIP   10.43.36.177    <none>        6379/TCP   3m41s
```

### Events through port-forward

Response from `http://localhost:3180/events` (formatted):

```json
[
  {"id": 1, "name": "Go Conference 2026", "venue": "Main Hall A", "date": "2026-09-15T09:00:00+00:00", "total_tickets": 100, "price_cents": 5000, "available": 100},
  {"id": 4, "name": "Python Workshop", "venue": "Lab 301", "date": "2026-09-22T14:00:00+00:00", "total_tickets": 25, "price_cents": 2000, "available": 25},
  {"id": 2, "name": "SRE Meetup", "venue": "Room 204", "date": "2026-10-01T18:00:00+00:00", "total_tickets": 30, "price_cents": 0, "available": 30},
  {"id": 5, "name": "Kubernetes Deep Dive", "venue": "Auditorium B", "date": "2026-10-10T10:00:00+00:00", "total_tickets": 80, "price_cents": 8000, "available": 80},
  {"id": 3, "name": "Cloud Native Summit", "venue": "Expo Center", "date": "2026-11-20T10:00:00+00:00", "total_tickets": 500, "price_cents": 15000, "available": 500}
]
```

The gateway health endpoint also returned `healthy`, with events and payments both `ok`.

### Self-healing

Output from `kubectl get pods -w` during gateway deletion (gateway rows):

```text
NAME                        READY   STATUS    RESTARTS   AGE
gateway-7cd55d8774-42ldm    1/1     Running   0          2m14s
gateway-7cd55d8774-42ldm    1/1     Terminating   0          2m15s
gateway-7cd55d8774-8hwcj    0/1     Pending       0          0s
gateway-7cd55d8774-42ldm    1/1     Terminating   0          2m15s
gateway-7cd55d8774-8hwcj    0/1     Pending       0          0s
gateway-7cd55d8774-8hwcj    0/1     ContainerCreating   0          0s
gateway-7cd55d8774-42ldm    0/1     Completed           0          2m15s
gateway-7cd55d8774-42ldm    0/1     Completed           0          2m16s
gateway-7cd55d8774-42ldm    0/1     Completed           0          2m16s
gateway-7cd55d8774-8hwcj    0/1     Running             0          1s
gateway-7cd55d8774-8hwcj    1/1     Running             0          7s
```

The replacement pod reached `Running` after about 1 second and `1/1 Ready` after **7.78 seconds**, measured from the deletion request. This test used the readiness probe added in Task 2.

Kubernetes automatically created a replacement to maintain the Deployment's desired replica count. In the Compose lab, the stopped service needed a manual start. A Compose restart policy can restart a failed container, but it does not recreate a deleted container as a Deployment does.

## Task 2 — Probes and resource limits

Gateway, events and payments have HTTP probes on `/health`, using their respective ports (8080, 8081 and 8082).

Gateway `kubectl describe pod` excerpt:

```text
Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

### Redis failure

Deleting Redis recreated it too quickly to trigger readiness failure. To make the outage observable, Redis was then temporarily scaled to zero until events became unready, and restored to one replica.

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-675d86c77-vsqc7      0/1     Running   0          108s
gateway-7cd55d8774-42ldm    1/1     Running   0          108s
payments-d7dc94485-8gks8    1/1     Running   0          108s
postgres-78489d7f5f-j775x   1/1     Running   0          108s
```

Probe failure messages from `kubectl describe pod`:

```text
Warning  Unhealthy  8s                   kubelet            spec.containers{events}: Liveness probe failed: HTTP probe failed with statuscode: 503
Warning  Unhealthy  1s (x3 over 6s)      kubelet            spec.containers{events}: Readiness probe failed: HTTP probe failed with statuscode: 503
```

The events endpoint had `ready: false`, so the Service stopped routing traffic to it. After Redis recovered, the same events pod returned to `1/1 Ready` with zero restarts:

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-675d86c77-vsqc7      1/1     Running   0          114s
redis-6fcfb5475d-4txmb      1/1     Running   0          5s
```

### Resource allocation

All five containers request 50m CPU and 64Mi memory, with limits of 200m CPU and 256Mi memory.

Node allocation from `kubectl describe node` (includes Kubernetes system pods):

```text
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (2%)   1 (6%)
  memory             460Mi (2%)  1450Mi (9%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
```

**Readiness vs liveness:** readiness failure stops Service traffic to the pod; liveness failure restarts its container after the failure threshold. Database connectivity belongs in readiness: restarting an application cannot repair an unavailable database. The lab's `/health` liveness probes also check dependencies; a production liveness check should only check the application itself.

## Bonus — Helm chart

The chart in `k8s/chart/` templates all five components. Images, replica counts, environment variables and resource settings are configurable.

`Chart.yaml`:

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
```

`values.yaml`:

```yaml
gateway:
  replicas: 1
  image: quickticket-gateway:v1
  env:
    EVENTS_URL: http://events:8081
    PAYMENTS_URL: http://payments:8082
    GATEWAY_TIMEOUT_MS: '5000'
events:
  replicas: 1
  image: quickticket-events:v1
  env:
    DB_HOST: postgres
    DB_PORT: '5432'
    DB_NAME: quickticket
    DB_USER: quickticket
    DB_PASS: quickticket
    DB_MAX_CONNS: '10'
    REDIS_HOST: redis
    REDIS_PORT: '6379'
    REDIS_TIMEOUT_MS: '1000'
    RESERVATION_TTL: '300'
payments:
  replicas: 1
  image: quickticket-payments:v1
  env:
    PAYMENT_FAILURE_RATE: '0.0'
    PAYMENT_LATENCY_MS: '0'
postgres:
  replicas: 1
  image: postgres:17-alpine
  env:
    POSTGRES_DB: quickticket
    POSTGRES_USER: quickticket
    POSTGRES_PASSWORD: quickticket
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

The raw resources were removed before installing the chart. Seed data was loaded again into the new PostgreSQL instance.

`helm list`:

```text
NAME       	NAMESPACE	REVISION	UPDATED                                	STATUS  	CHART            	APP VERSION
quickticket	default  	1       	2026-09-21 10:56:29.179980755 +0300 MSK	deployed	quickticket-0.1.0
```

Pods after Helm installation:

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-675d86c77-dfs5b      1/1     Running   0          36s
gateway-7cd55d8774-6rqm4    1/1     Running   0          36s
payments-d7dc94485-7mkf5    1/1     Running   0          36s
postgres-78489d7f5f-xs94d   1/1     Running   0          36s
redis-6fcfb5475d-fthlr      1/1     Running   0          36s
```

Helm lint and server-side manifest validation passed. After Helm installation, port-forward returned all five events and a healthy gateway. The optional kube-prometheus-stack was not installed.
