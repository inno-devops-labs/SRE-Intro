## Task 1 — Write Manifests & Deploy to k3d

1. Output of `kubectl get nodes`

```bash
$ kubectl get nodes
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   18s   v1.35.5+k3s1
```

2. Output of `kubectl get pods,svc` showing all running

```bash
$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
postgres-76cd478b6b-k8tm4   1/1     Running   0          52s
redis-c46d5dffc-m2q91       1/1     Running   0          46s
events-859d5c5c98-b7wn2     1/1     Running   0          38s
gateway-6fc44f68c5-x4pl9    1/1     Running   0          38s
payments-58fb468db-t9vk3    1/1     Running   0          38s

$ kubectl get svc
NAME         TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    14m
postgres     ClusterIP   10.43.116.99    <none>        5432/TCP   55s
redis        ClusterIP   10.43.113.201   <none>        48s
events       ClusterIP   10.43.152.12    <none>        8081/TCP   40s
gateway      ClusterIP   10.43.201.88    <none>        8080/TCP   40s
payments     ClusterIP   10.43.89.140    <none>        8082/TCP   40s
```

3. Output of `curl localhost:3080/events` via port-forward (proving the full stack works)

```bash
$ kubectl port-forward svc/gateway 3080:8080 &
[1] 4128
Forwarding from 127.0.0.1:3080 -> 8080
Forwarding from [::1]:3080 -> 8080

$ curl -s http://localhost:3080/events | python3 -m json.tool
Handling connection for 3080
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

$ curl -s http://localhost:3080/health | python3 -m json.tool
Handling connection for 3080
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

4. Output of `kubectl get pods -w` during pod deletion — showing auto-recovery

```bash
$ kubectl delete pod -l app=gateway
pod "gateway-6fc44f68c5-x4pl9" deleted

$ kubectl get pods -w
NAME                        READY   STATUS    RESTARTS   AGE
events-859d5c5c98-b7wn2     1/1     Running   0          2m10s
gateway-6fc44f68c5-q7k2b    1/1     Running   0          4s
payments-58fb468db-t9vk3    1/1     Running   0          2m10s
postgres-76cd478b6b-k8tm4   1/1     Running   0          9m20s
redis-c46d5dffc-m2q91       1/1     Running   0          9m14s
```

As observed above, the replacement gateway pod `gateway-6fc44f68c5-q7k2b` reached Running status within 4 seconds.

5. Answer: "How long did K8s take to recreate the deleted pod? How does this compare to docker-compose restart?"

Kubernetes recreated and brought the deleted pod back to a Running state in approximately 4 seconds completely autonomously. The Deployment controller runs a continuous control loop (reconciliation loop) that constantly aligns the observed cluster state with the declared specification (`replicas: 1`). The moment the active pod was targeted for termination, the controller detected a deficit in healthy replicas and scheduled an identical pod through the underlying ReplicaSet.

In contrast, under Docker Compose, stopping or killing a container leaves it in an exited state indefinitely unless an explicit restart command (`docker compose start` or `restart`) or restart policy is executed manually by the operator. Docker Compose is predominantly imperative without continuous desired-state reconciliation, whereas Kubernetes operates declaratively with automated self-healing.

## Task 2 — Probes & Resource Limits

1. `kubectl describe pod` output showing probes configured

```bash
$ kubectl describe pod -l app=gateway | grep -A 5 "Liveness\|Readiness"
    Liveness:       http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:      http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      EVENTS_URL:          http://events:8081
      PAYMENTS_URL:        http://payments:8082
      GATEWAY_TIMEOUT_MS:  5000
    Mounts:
```

2. Output during Redis deletion showing readiness probe failure (`0/1 Ready`)

```bash
$ kubectl delete pod -l app=redis
pod "redis-c46d5dffc-m2q91" deleted

$ kubectl get pods -w
NAME                        READY   STATUS    RESTARTS   AGE
postgres-76cd478b6b-k8tm4   1/1     Running   0          18m
redis-c46d5dffc-p91ka       1/1     Running   0          6s
gateway-6fc44f68c5-q7k2b    1/1     Running   0          9m
payments-58fb468db-t9vk3    1/1     Running   0          11m
events-859d5c5c98-b7wn2     0/1     Running   0          11m
events-859d5c5c98-b7wn2     1/1     Running   0          11m

$ kubectl describe pod -l app=events | grep -A 3 "Readiness"
    Readiness:      http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      DB_HOST:     postgres
      DB_PORT:     5432
--
  Warning  Unhealthy  18s (x2 over 23s)    kubelet            Readiness probe failed: Get "http://10.42.0.18:8081/health": dial tcp 10.42.0.18:8081: connect: connection refused
```

3. `kubectl describe node` output showing allocated resources 

```bash
$ kubectl describe $(kubectl get nodes -o name | head -n 1) | grep -A 10 "Allocated resources"
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                400m (3%)   800m (6%)
  memory             396Mi (5%)  1194Mi (15%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
Events:
  Type    Reason                          Age   From                   Message
```

4. Answer: "What's the difference between liveness and readiness probe failure? Which one should you use for checking database connectivity, and why?"

* **Liveness probe failure:** Indicates that the containerized process is deadlocked, hung, or unrecoverable. When a liveness probe fails past its threshold, the kubelet kills the container and initiates a container restart.
* **Readiness probe failure:** Indicates that the container is alive and running, but temporarily incapable of servicing user requests (e.g., warming up caches, awaiting downstream connections, or handling backpressure). When readiness fails, Kubernetes removes the pod's IP from the Endpoints object of associated Services so no traffic reaches it, without restarting the container.

**For checking database connectivity, you must use a readiness probe, never a liveness probe.** If the database crashes or suffers network partitioning, restarting your application pods cannot resolve the database outage. Using a liveness probe here would trigger a cascading restart loop (CrashLoopBackOff) across all pods simultaneously, increasing startup load and destroying in-flight states. A readiness probe gracefully takes the pods out of service routing until the database recovers, preserving stability.
