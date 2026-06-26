# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

## Task 1 — Write Manifests & Deploy to k3d

### 1. kubectl get nodes

```bash
kubectl get nodes
```

```
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   18m   v1.35.5+k3s1
```

### 2. All pods and services running

```bash
kubectl get pods,svc
```

```
NAME                           READY   STATUS    RESTARTS   AGE
pod/events-6c4df7d6-24d7r      1/1     Running   0          4m8s
pod/gateway-6fc44f68c5-t4kh9   1/1     Running   0          4m8s
pod/payments-58fb468db-ksdqj   1/1     Running   0          4m8s
pod/postgres-7c7ffc4b-755kd    1/1     Running   0          4m7s
pod/redis-c46d5dffc-ssxr8      1/1     Running   0          4m7s

NAME                 TYPE        CLUSTER-IP     EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.173.8    <none>        8081/TCP   4m8s
service/gateway      ClusterIP   10.43.18.213   <none>        8080/TCP   4m8s
service/kubernetes   ClusterIP   10.43.0.1      <none>        443/TCP    29m
service/payments     ClusterIP   10.43.55.216   <none>        8082/TCP   4m8s
service/postgres     ClusterIP   10.43.73.59    <none>        5432/TCP   4m7s
service/redis        ClusterIP   10.43.18.14    <none>        6379/TCP   4m7s
```

### 3. Full stack working via port-forward

```bash
kubectl port-forward svc/gateway 3080:8080 &
curl -s http://localhost:3080/events | python3 -m json.tool
curl -s http://localhost:3080/health | python3 -m json.tool
```

```
[
    {
        "id": 1,
        "name": "Go Conference 2026",
        "venue": "Main Hall A",
        "date": "2026-09-15T09:00:00+00:00",
        "total_tickets": 100,
        "price_cents": 5000,
        "available": 98
    },
    {
        "id": 4,
        "name": "Python Workshop",
        "venue": "Lab 301",
        "date": "2026-09-22T14:00:00+00:00",
        "total_tickets": 25,
        "price_cents": 2000,
        "available": 24
    },
    {
        "id": 2,
        "name": "SRE Meetup",
        "venue": "Room 204",
        "date": "2026-10-01T18:00:00+00:00",
        "total_tickets": 30,
        "price_cents": 0,
        "available": 27
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
        "available": 499
    }
]
```

```
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

### 4. Self-healing — pod deletion and auto-recovery

```bash
kubectl get pods
kubectl delete pod -l app=gateway
kubectl get pods -w
```

```
NAME                       READY   STATUS    RESTARTS   AGE
events-76dddcc778-x49nm    1/1     Running   0          7m43s
gateway-6fc44f68c5-t4kh9   1/1     Running   0          12m
payments-58fb468db-ksdqj   1/1     Running   0          12m
postgres-7c7ffc4b-755kd    1/1     Running   0          12m
redis-c46d5dffc-ssxr8      1/1     Running   0          12m

pod "gateway-6fc44f68c5-t4kh9" deleted from default namespace

NAME                       READY   STATUS    RESTARTS   AGE
events-76dddcc778-x49nm    1/1     Running   0          8m1s
gateway-6fc44f68c5-w2tn6   1/1     Running   0          17s
payments-58fb468db-ksdqj   1/1     Running   0          13m
postgres-7c7ffc4b-755kd    1/1     Running   0          13m
redis-c46d5dffc-ssxr8      1/1     Running   0          13m
```

### 5. K8s recovery vs docker-compose

Kubernetes took approximately 33 seconds to recreate the deleted gateway pod. This is significantly faster than docker-compose restart, which typically requires manual intervention (`docker compose start` or `docker compose restart`) and doesn't have automatic self-healing capabilities. With Kubernetes, the Deployment controller automatically detects when a pod is deleted and creates a replacement to maintain the desired replica count, without any manual intervention required. This is a key advantage of Kubernetes over docker-compose - it provides built-in self-healing and maintains the desired state automatically.

---

## Task 2 — Probes & Resource Limits (optional)

### Probes configured (kubectl describe)

```bash
kubectl describe pod -l app=gateway | grep -A 5 "Liveness\|Readiness"
```

```
    Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      EVENTS_URL:          http://events:8081
      PAYMENTS_URL:        http://payments:8082
      GATEWAY_TIMEOUT_MS:  5000
    Mounts:
```

### Readiness probe failure during Redis deletion

```bash
kubectl delete pod -l app=redis
kubectl get pods -w
```

```
pod "redis-c46d5dffc-ssxr8" deleted from default namespace
NAME                       READY   STATUS    RESTARTS   AGE
events-76dddcc778-x49nm    1/1     Running   0          11m
events-95f5cc9b9-k4bpp     0/1     Running   0          48s
gateway-6fc44f68c5-w2tn6   1/1     Running   0          3m45s
gateway-7cd55d8774-r9hx8   0/1     Running   0          48s
payments-58fb468db-ksdqj   1/1     Running   0          16m
payments-d7dc94485-ltzz5   0/1     Running   0          47s
postgres-7c7ffc4b-755kd    1/1     Running   0          16m
redis-c46d5dffc-r7qc4      1/1     Running   0          43s
STATUS                     REASON          MESSAGE
Failure                    InternalError   an error on the server ("unable to decode an event from the watch stream: http2: client connection lost") has prevented the request from succeeding
```

```bash
kubectl describe pod -l app=events | grep -A 3 "Readiness"
```

```
    Readiness:  http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      DB_HOST:           postgres
      DB_PORT:           5432
```

### Node resource allocation

```bash
kubectl describe node $(kubectl get nodes -o name | head -1) | grep -A 10 "Allocated resources"
```

```
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                350m (4%)   600m (7%)
  memory             332Mi (8%)  938Mi (23%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
  hugepages-32Mi     0 (0%)      0 (0%)
  hugepages-64Ki     0 (0%)      0 (0%)
```

### Liveness vs readiness — which for DB connectivity?

A **readiness probe** failure removes the pod from the Service's endpoint list, stopping new traffic from being routed to it, but does NOT restart the pod. A **liveness probe** failure kills and restarts the pod. For database connectivity, readiness is the correct choice: if the database goes down, restarting the application pod does nothing to fix the database. The right behavior is to stop sending traffic to the pod (readiness failure) until the database recovers, then automatically resume. Using a liveness probe for DB checks would cause the pod to restart in a loop during any database outage — a `CrashLoopBackOff` that makes recovery slower.

---

## Bonus Task — Helm Chart (optional)

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
  events:
    replicas: 1
    image: quickticket-events:v1
  db:
    host: postgres
    port: 5432
    name: quickticket
    user: quickticket
    password: quickticket
payments:
  replicas: 1
  image: quickticket-payments:v1
  failureRate: "0.0"
  latencyMs: "0"
```

### helm list

```bash
helm list
```

```
NAME            NAMESPACE       REVISION        UPDATED                                 STATUS          CHART                   APP VERSION
quickticket     default         1               2026-06-20 04:19:50.864531587 +0800 CST deployed        quickticket-0.1.0
```

### kubectl get pods after Helm install

```bash
kubectl get pods
```

```
NAME                       READY   STATUS              RESTARTS      AGE
events-675d86c77-tnrkp     0/1     ContainerCreating   0             2s
events-95f5cc9b9-k4bpp     0/1     Terminating         1 (11m ago)   14m
gateway-7cd55d8774-r9hx8   0/1     Terminating         1 (11m ago)   14m
gateway-7cd55d8774-rmhft   0/1     ContainerCreating   0             2s
payments-d7dc94485-8zsc7   0/1     ContainerCreating   0             2s
payments-d7dc94485-ltzz5   0/1     Terminating         1 (11m ago)   14m
postgres-7c7ffc4b-9cbcx    0/1     ContainerCreating   0             2s
redis-c46d5dffc-4cwsl      0/1     ContainerCreating   0             2s
```