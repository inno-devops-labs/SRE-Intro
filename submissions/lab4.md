# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

**Author:** Anton Bugaev  
**Date:** 2026-06-18

> Note: `kubectl apply -f k8s/` would also pick up Helm templates with `{{ }}` syntax. I apply raw manifests explicitly: `kubectl apply -f k8s/postgres.yaml ...` and use `helm install` for the chart (Bonus).

---

## Task 1 — Write Manifests & Deploy to k3d

### 4.1 `kubectl get nodes`

```
NAME                       STATUS   ROLES                  AGE     VERSION
k3d-quickticket-server-0   Ready    control-plane,master   7m39s   v1.31.5+k3s1
```

### 4.2 `kubectl get pods,svc` (all running)

```
NAME                            READY   STATUS    RESTARTS   AGE
pod/events-5747d7c57b-lqm6g     1/1     Running   0          ...
pod/gateway-6988f5d9c5-8s7wf    1/1     Running   0          ...
pod/payments-bf4c9687-r99rh     1/1     Running   0          ...
pod/postgres-85ffd4fb9f-9rbx2   1/1     Running   0          ...
pod/redis-6d65768944-pm58v      1/1     Running   0          ...

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.214.221   <none>        8081/TCP   ...
service/gateway      ClusterIP   10.43.132.7     <none>        8080/TCP   ...
service/payments     ClusterIP   10.43.31.92     <none>        8082/TCP   ...
service/postgres     ClusterIP   10.43.173.118   <none>        5432/TCP   ...
service/redis        ClusterIP   10.43.92.25     <none>        6379/TCP   ...
```

### 4.6 `curl` via port-forward (full stack works)

```
$ kubectl port-forward svc/gateway 3080:8080 &
$ curl -s http://localhost:3080/events | python3 -m json.tool | head -12
[
    {
        "id": 1,
        "name": "Go Conference 2026",
        "venue": "Main Hall A",
        ...
        "available": 100
    },
    ...
]

$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

### 4.7 Self-healing after `kubectl delete pod -l app=gateway`

```
$ kubectl delete pod gateway-6988f5d9c5-267cn
pod "gateway-6988f5d9c5-267cn" deleted

$ kubectl get pods -l app=gateway -w
gateway-6988f5d9c5-267cn   1/1   Terminating
gateway-6988f5d9c5-8s7wf   0/1   ContainerCreating
gateway-6988f5d9c5-8s7wf   1/1   Running
```

**Recovery time:** ~**6 seconds** from delete to new pod `Ready`.

**Comparison with docker-compose (Lab 1):** In docker-compose, killing a container required manual `docker compose start <service>`. Kubernetes Deployment controller automatically creates a replacement pod — no human intervention. Recovery is fast (~seconds) and built into the control plane.

---

## Task 2 — Probes & Resource Limits

### 4.9 Probes configured (`kubectl describe pod -l app=gateway`)

```
Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #failure=3
Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #failure=2
```

Similar probes on `events` (8081) and `payments` (8082).

### 4.10 Readiness failure when Redis unavailable

```
$ kubectl scale deployment redis --replicas=0
$ kubectl get pods -l app=events
NAME                     READY   STATUS    RESTARTS   AGE
events-8bb65ffd4-5kgpm   0/1     Running   0          68s

$ kubectl describe pod -l app=events | grep Readiness
  Warning  Unhealthy  Readiness probe failed: HTTP probe failed with statuscode: 503
```

Pod stayed **Running** but `0/1 Ready` — removed from Service endpoints, no traffic routed.

### 4.11 Resource limits — node allocation

```
Allocated resources:
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (5%)   1 (12%)
  memory             460Mi (5%)  1450Mi (18%)
```

Each app container: `requests: cpu 50m, memory 64Mi` / `limits: cpu 200m, memory 256Mi`.

### Liveness vs readiness for DB connectivity

Use **readiness**, not liveness, for database connectivity:

- **Readiness failure** → pod removed from Service endpoints (no traffic), pod is **not** restarted.
- **Liveness failure** → kubelet **kills and restarts** the pod.

If Postgres/Redis is down, restarting the app pod won't fix the dependency. Readiness stops routing traffic until dependencies recover. Liveness on DB checks would cause restart loops during outages.

---

## Bonus Task — Helm Chart

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

### `helm list`

```
NAME        NAMESPACE REVISION STATUS   CHART
quickticket default   1        deployed quickticket-0.1.0
```

### `kubectl get pods` after Helm install

```
NAME                        READY   STATUS    RESTARTS   AGE
events-5747d7c57b-lqm6g     1/1     Running   0          ...
gateway-6988f5d9c5-8s7wf    1/1     Running   0          ...
payments-bf4c9687-r99rh     1/1     Running   0          ...
postgres-85ffd4fb9f-9rbx2   1/1     Running   0          ...
redis-6d65768944-pm58v      1/1     Running   0          ...
```

### B.4 Monitoring (kube-prometheus-stack)

Not installed — optional per lab instructions. Would add ~10+ pods (Prometheus, Grafana, operators, exporters).
