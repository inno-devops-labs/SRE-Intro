# Lab 4 — Kubernetes

## Task 1 — Manifests and deployment

I created a local k3d cluster and wrote Deployment and ClusterIP Service manifests for all five components in `k8s/`.

### Cluster node

```text
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   48s   v1.35.5+k3s1
```

### Pods and services

```text
NAME                            READY   STATUS    RESTARTS
pod/events-675d86c77-2qpqq      1/1     Running   0
pod/gateway-7cd55d8774-5tqqg    1/1     Running   0
pod/payments-d7dc94485-bxvw6    1/1     Running   0
pod/postgres-745cf6f696-wgmg9   1/1     Running   0
pod/redis-d8d9865df-jft5g       1/1     Running   0

NAME                 TYPE        PORT(S)
service/events       ClusterIP   8081/TCP
service/gateway      ClusterIP   8080/TCP
service/payments     ClusterIP   8082/TCP
service/postgres     ClusterIP   5432/TCP
service/redis        ClusterIP   6379/TCP
```

I loaded `app/seed.sql` into PostgreSQL. It created both tables and inserted five events.

### Full-stack check through gateway

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

`GET /events` returned all five seeded events:

```text
Go Conference 2026
Python Workshop
SRE Meetup
Kubernetes Deep Dive
Cloud Native Summit
```

### Self-healing

I deleted the gateway pod and measured the time until its replacement became Ready:

```text
started_at=2026-09-20T12:42:35Z old_pod=gateway-7cd55d8774-5tqqg
pod "gateway-7cd55d8774-5tqqg" deleted
t=0.14s gateway-7cd55d8774-5tqqg   1/1   Terminating
t=0.90s gateway-7cd55d8774-87rvb   0/1   ContainerCreating
t=1.75s gateway-7cd55d8774-87rvb   0/1   Running
t=7.79s gateway-7cd55d8774-87rvb   1/1   Running
recovered_in=7.79s new_pod=gateway-7cd55d8774-87rvb
```

Kubernetes recreated the deleted pod automatically in **7.79 seconds**. In Lab 1, Docker Compose did not reconcile a manually stopped container, so I had to run `docker compose start` myself. The Deployment controller continuously keeps the requested replica count without manual recovery.

## Task 2 — Probes and resources

All application Deployments have HTTP liveness and readiness probes. PostgreSQL and Redis use command probes. Every container requests `50m` CPU and `64Mi` memory and is limited to `200m` CPU and `256Mi` memory.

### Gateway probes

```text
Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

### Redis dependency failure

A single deleted Redis pod was replaced in about two seconds, before events accumulated two failed readiness checks. To make the dependency outage observable, I temporarily scaled Redis to zero and restored it immediately after events became NotReady:

```text
started_at=2026-09-20T12:43:48Z
deployment.apps/redis scaled
t=1s  events_ready=true  phase=Running endpoints=10.42.0.13
t=10s events_ready=true  phase=Running endpoints=10.42.0.13
t=14s events_ready=false phase=Running endpoints=none

deployment.apps/redis scaled
deployment "redis" successfully rolled out
recovery_t=1s events_ready=false
recovery_t=4s events_ready=true
```

The events process stayed Running with zero restarts, but Kubernetes removed it from the Service endpoints while its readiness probe failed.

### Allocated node resources

```text
Allocated resources:
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (9%)   1 (20%)
  memory             460Mi (5%)  1450Mi (16%)
```

A readiness failure only removes a pod from Service traffic; it does not restart the container. A liveness failure tells Kubernetes that the process itself is broken, so kubelet restarts it. Database connectivity belongs in readiness: restarting a healthy application cannot repair an unavailable database and can amplify an outage. In a production service I would use a dependency-free liveness endpoint rather than the shared dependency-aware `/health` endpoint used by this lab.

## Bonus — Helm

I created `k8s/chart` with configurable values for all components and installed it after deleting the raw resources.

### Chart.yaml

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
type: application
version: 0.1.0
appVersion: "1.0"
```

### values.yaml

```yaml
gateway:
  replicas: 1
  image: quickticket-gateway:v1
events:
  replicas: 1
  image: quickticket-events:v1
  db:
    host: postgres
    port: "5432"
    name: quickticket
    user: quickticket
    password: quickticket
payments:
  replicas: 1
  image: quickticket-payments:v1
  failureRate: "0.0"
  latencyMs: "0"
```

The complete file additionally configures component ports, Redis, PostgreSQL, timeouts and shared resource limits.

### Helm releases

```text
NAME        NAMESPACE  REVISION  STATUS    CHART                         APP VERSION
monitoring  default    1         deployed  kube-prometheus-stack-91.4.1  v0.94.0
quickticket default    1         deployed  quickticket-0.1.0             1.0
```

### QuickTicket after Helm install

```text
NAME                         READY   STATUS    RESTARTS
events-675d86c77-glhb7       1/1     Running   0
gateway-7cd55d8774-kc9wx     1/1     Running   0
payments-d7dc94485-7mdc4     1/1     Running   0
postgres-745cf6f696-2rjqj    1/1     Running   0
redis-d8d9865df-f7kdb        1/1     Running   0
```

`kube-prometheus-stack` created **6 pods**: Grafana, Prometheus Operator, kube-state-metrics, node-exporter, Prometheus and Alertmanager. All were Running after installation.

## Validation

```text
helm lint k8s/chart
1 chart(s) linted, 0 chart(s) failed

helm template quickticket k8s/chart | kubectl apply --dry-run=server -f -
all 5 Services and all 5 Deployments accepted

git diff --check
passed
```
