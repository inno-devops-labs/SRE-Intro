# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

## Task 1 — Write Manifests & Deploy to k3d

### 4.1: Cluster

```
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   38s   v1.35.5+k3s1
```

### 4.2–4.4: All Pods and Services Running

```
NAME                           READY   STATUS    RESTARTS   AGE
pod/events-859d5c5c98-qj4q4    1/1     Running   0          16s
pod/gateway-6fc44f68c5-plltd   1/1     Running   0          16s
pod/payments-58fb468db-nfh2d   1/1     Running   0          16s
pod/postgres-7c7ffc4b-8ztq5    1/1     Running   0          16s
pod/redis-c46d5dffc-hbzcw      1/1     Running   0          16s

NAME                 TYPE        CLUSTER-IP     EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.175.99   <none>        8081/TCP   16s
service/gateway      ClusterIP   10.43.80.233   <none>        8080/TCP   16s
service/kubernetes   ClusterIP   10.43.0.1      <none>        443/TCP    49m
service/payments     ClusterIP   10.43.92.58    <none>        8082/TCP   16s
service/postgres     ClusterIP   10.43.107.44   <none>        5432/TCP   16s
service/redis        ClusterIP   10.43.12.193   <none>        6379/TCP   16s
```

### 4.5–4.6: Full Stack Working via Port-Forward

```json
[
    {"id": 1, "name": "Go Conference 2026", "venue": "Main Hall A", "available": 100},
    {"id": 4, "name": "Python Workshop", "venue": "Lab 301", "available": 25},
    {"id": 2, "name": "SRE Meetup", "venue": "Room 204", "available": 30},
    {"id": 5, "name": "Kubernetes Deep Dive", "venue": "Auditorium B", "available": 80},
    {"id": 3, "name": "Cloud Native Summit", "venue": "Expo Center", "available": 500}
]
```

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

### 4.7: Self-Healing — Pod Deletion & Auto-Recovery

```
NAME                       READY   STATUS              RESTARTS   AGE
gateway-6fc44f68c5-vn9s9   0/1     ContainerCreating   0          2s
...
gateway-6fc44f68c5-vn9s9   1/1     Running             0          2s
```

**K8s recreated the deleted gateway pod in ~2 seconds.** The Deployment controller detected the pod was missing and immediately scheduled a replacement. This is fundamentally different from docker-compose, where a stopped container stays stopped until you manually run `docker compose start`. In K8s, the desired state (1 replica) is continuously reconciled — if reality diverges, K8s fixes it automatically.

---

## Task 2 — Probes & Resource Limits

### 4.9: Probes Configured

```
Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

Probes were also added to events (port 8081) and payments (port 8082) with identical parameters.

### 4.10: Readiness Probe During Redis Deletion

```
redis-c46d5dffc-c7j95      0/1     ContainerCreating   0          1s
redis-c46d5dffc-c7j95      1/1     Running             0          1s
```

K8s self-healing recreated the Redis pod in ~1 second — faster than the readiness probe's failure threshold (2 failures × 5s period = 10s). Events never lost readiness because its dependency recovered before the probe could detect the outage.

### 4.11: Resource Allocation

```
Allocated resources:
  Resource           Requests    Limits
  --------           --------    ------
  cpu                350m (2%)   600m (3%)
  memory             332Mi (2%)  938Mi (6%)
```

Three QuickTicket services contribute 150m CPU requests (3 × 50m) and 192Mi memory requests (3 × 64Mi). The rest comes from K8s system components.

### Liveness vs Readiness

**Liveness probe failure** → K8s kills and restarts the pod. Use for detecting deadlocks or stuck processes — situations where restarting fixes the problem.

**Readiness probe failure** → K8s removes the pod from Service endpoints (no traffic routed to it), but does NOT restart it. The pod stays running and gets traffic again once the probe passes.

**For database connectivity: use readiness, not liveness.** If the database is down, restarting the application pod won't fix the database — it will just cause a restart loop (CrashLoopBackOff). Readiness is the right choice: stop sending traffic to the pod until the database recovers, then resume automatically.
