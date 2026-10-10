# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster


## Task 1 — manifests and k3d deployment

### 1. `kubectl get nodes`

```text
NAME                       STATUS   ROLES           VERSION
k3d-quickticket-server-0   Ready    control-plane   v1.35.5+k3s1
```

### 2. `kubectl get pods,svc`

```text
NAME                        READY   STATUS    RESTARTS
events-675d86c77-pmg5l      1/1     Running   0
gateway-7cd55d8774-qlrb2    1/1     Running   0
payments-d7dc94485-mndzp    1/1     Running   0
postgres-78489d7f5f-jm224   1/1     Running   0
redis-6fcfb5475d-hmjqz      1/1     Running   0

NAME       TYPE        PORT
events     ClusterIP   8081/TCP
gateway    ClusterIP   8080/TCP
payments   ClusterIP   8082/TCP
postgres   ClusterIP   5432/TCP
redis      ClusterIP   6379/TCP
```

PostgreSQL was seeded successfully: `CREATE TABLE`, `CREATE TABLE`, `INSERT 0 5`.

### 3. Gateway port-forward test

```text
curl http://localhost:3081/events
[{"id":1,"name":"Go Conference 2026",...},{"id":5,"name":"Kubernetes Deep Dive",...}]
```

This proves that gateway, events, PostgreSQL, and Redis worked together.

### 4. Pod deletion and auto-recovery

```text
pod "gateway-69c4c7c4c6-l6z72" deleted
gateway-69c4c7c4c6-8wb7t   0/1   Running   0   6s
```

### 5. Recovery answer

Kubernetes created a new gateway pod in about 6 seconds. The pod became Ready after its readiness probe. Docker Compose does not keep a desired number of containers. After a stopped or deleted container, I need to start it myself with `docker compose start` or `docker compose up` unless a restart policy is used.

## Task 2 — probes and resource limits

### 1. Probe output

```text
Liveness:  http-get http://:8080/health delay=10s period=10s #failure=3
Readiness: http-get http://:8080/health delay=0s period=5s #failure=2
```

The same kind of probes were added to events on port 8081 and payments on port 8082.

### 2. Redis failure and readiness output

I scaled Redis to zero for 15 seconds. This kept it down long enough for the events readiness probe to fail. Then I scaled Redis back to one.

```text
NAME                        READY   STATUS
events-675d86c77-pmg5l      0/1     Running

NAME     ENDPOINTS
events   <none>
```

This shows that events stayed running, but Kubernetes removed it from Service endpoints. After Redis came back, both Redis and events were ready again.

### 3. Node allocated resources

```text
Allocated resources:
Resource  Requests    Limits
cpu       450m (3%)   1 (8%)
memory    460Mi (6%)  1450Mi (19%)
```

Every application container has these settings:

```yaml
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi
```

### 4. Liveness vs readiness answer

A liveness failure means Kubernetes restarts the container. A readiness failure means the container is not ready for Service traffic, but it is not restarted. For database or Redis connectivity, we must use readiness. Restarting the application does not repair a broken database connection. It is better to stop traffic until the dependency is ready.

## Bonus task — Helm chart

### `chart.yaml`:

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
```

### `values.yaml`:

```yaml
gateway:
  replicas: 1
  image: quickticket-gateway:v1
events:
  replicas: 1
  image: quickticket-events:v1
  db: { host: postgres, port: 5432, name: quickticket, user: quickticket, password: quickticket }
  redis: { host: redis, port: 6379 }
  maxConnections: 10
  redisTimeoutMs: 1000
  reservationTtl: 300
payments:
  replicas: 1
  image: quickticket-payments:v1
  failureRate: "0.0"
  latencyMs: "0"
postgres: { image: postgres:17-alpine, database: quickticket, user: quickticket, password: quickticket }
redis: { image: redis:7-alpine }
resources:
  requests: { cpu: 50m, memory: 64Mi }
  limits: { cpu: 200m, memory: 256Mi }
```

`helm lint k8s/chart` passed with 0 failed charts. `helm template` also passed Kubernetes dry-run validation.

### `helm list`:

```text
NAME        NAMESPACE  REVISION  STATUS    CHART
quickticket default    1         deployed  quickticket-0.1.0
```

### Pods after Helm install

```text
events      1/1 Running
gateway     1/1 Running
payments    1/1 Running
postgres    1/1 Running
redis       1/1 Running
```
