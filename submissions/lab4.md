# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

## Student
- GitHub: `MiniMaxC`
- Branch: `feature/lab4`

# Task 1 — Write Manifests & Deploy to k3d

## Cluster

```text
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   11s   v1.35.5+k3s1
```

```text
NAME          SERVERS   AGENTS   LOADBALANCER
quickticket   1/1       0/0      true
```

## Manifests

Created:

```text
k8s/postgres.yaml
k8s/redis.yaml
k8s/events.yaml
k8s/payments.yaml
k8s/gateway.yaml
```

The locally built QuickTicket images use `imagePullPolicy: Never`.

## Pods and services

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-6cfdc74f4d-ljn4j     1/1     Running   0          14m
gateway-6fc44f68c5-th8gd    1/1     Running   0          27s
payments-58fb468db-75dm6    1/1     Running   0          17m
postgres-7c7ffc4b-tqr9v     1/1     Running   0          17m
redis-c46d5dffc-8cgtn       1/1     Running   0          17m
```

```text
NAME         TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)
events       ClusterIP   10.43.25.114    <none>        8081/TCP
gateway      ClusterIP   10.43.148.172   <none>        8080/TCP
kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP
payments     ClusterIP   10.43.218.137   <none>        8082/TCP
postgres     ClusterIP   10.43.156.105   <none>        5432/TCP
redis        ClusterIP   10.43.16.142    <none>        6379/TCP
```

## Database initialization

```text
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

## Full-stack verification

`GET /events` via the gateway returned the seeded event list, including:

```json
{
  "id": 1,
  "name": "Go Conference 2026",
  "venue": "Main Hall A",
  "total_tickets": 100,
  "available": 100
}
```

Gateway health:

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

The `events` Deployment initially needed one rollout restart after PostgreSQL and Redis were ready, demonstrating the startup-order issue because Kubernetes does not use Docker Compose `depends_on`.

## Self-healing

Original gateway pod:

```text
gateway-6fc44f68c5-hk7d8
```

Recovery watch:

```text
gateway-6fc44f68c5-hk7d8   1/1   Running             0   14m
gateway-6fc44f68c5-hk7d8   1/1   Terminating         0   16m
gateway-6fc44f68c5-th8gd   0/1   Pending             0   0s
gateway-6fc44f68c5-th8gd   0/1   ContainerCreating   0   0s
gateway-6fc44f68c5-th8gd   1/1   Running             0   1s
```

```text
New gateway pod: gateway-6fc44f68c5-th8gd
Recovery time: 16 seconds
```

### Comparison with Docker Compose

Kubernetes recreated the deleted gateway pod automatically in approximately 16 seconds. With Docker Compose in Lab 1, the failed service had to be restarted manually. Kubernetes continuously reconciles the Deployment's desired replica count, so deleting the pod automatically caused a replacement pod to be created.

# Task 2 — Probes & Resource Limits

## Probes

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

## Redis outage and readiness failure

During the Redis failure experiment:

```text
events-88fd587b7-tqgg6   1/1   Running
events-88fd587b7-tqgg6   0/1   Running
events-88fd587b7-tqgg6   1/1   Running
```

`kubectl describe` showed:

```text
Readiness:  http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2

Warning  Unhealthy  kubelet  Readiness probe failed:
Get "http://10.42.0.16:8081/health": context deadline exceeded

Warning  Unhealthy  kubelet  Readiness probe failed:
HTTP probe failed with statuscode: 503
```

A liveness failure was also observed while Redis was unavailable:

```text
Warning  Unhealthy  kubelet  Liveness probe failed:
Get "http://10.42.0.16:8081/health": context deadline exceeded
```

After Redis was restored, events returned to `1/1 Ready`.

## Resource requests and limits

Each container was configured with:

```yaml
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi
```

Node allocation:

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

Final pod state:

```text
NAME                        READY   STATUS    RESTARTS
events-88fd587b7-tqgg6      1/1     Running   0
gateway-7cd55d8774-qtnqc    1/1     Running   0
payments-d7dc94485-cm9jj    1/1     Running   0
postgres-78489d7f5f-84q2n   1/1     Running   0
redis-6fcfb5475d-mp6wl      1/1     Running   0
```

## Liveness vs readiness

A **readiness probe** determines whether a pod should receive traffic. If readiness fails, the pod remains running but is removed from Service endpoints until it becomes ready again.

A **liveness probe** determines whether Kubernetes considers the container unhealthy enough to restart. If liveness repeatedly fails, Kubernetes kills and restarts the container.

Database or Redis connectivity should therefore be checked with **readiness**, not liveness. If an external dependency is unavailable, restarting the application container will not repair that dependency. The correct behavior is to stop routing traffic to the affected pod until the dependency recovers.

# Submission Checklist

- [x] Task 1 done — K8s manifests written, QuickTicket deployed to k3d
- [x] Task 2 done — probes and resource limits added
- [ ] Bonus Task done — Helm chart created
