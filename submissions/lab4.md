## Task 1 — Write Manifests & Deploy to k3d

1. Output of `kubectl get nodes`

```bash
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro/app$ kubectl get nodes
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   13s   v1.35.5+k3s1
```

2. Output of `kubectl get pods,svc` showing all running

```bash
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
postgres-76cd478b6b-rqt25   1/1     Running   0          50s
redis-c46d5dffc-j7r25       1/1     Running   0          44s


timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ kubectl get svc
NAME         TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    12m
postgres     ClusterIP   10.43.116.99    <none>        5432/TCP   53s
redis        ClusterIP   10.43.113.201   <none>        6379/TCP   46s
```
3. Output of `curl localhost:3080/events` via port-forward (proving the full stack works)

```bash
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ Forwarding from 127.0.0.1:3080 -> 8080
Forwarding from [::1]:3080 -> 8080
curl -s http://localhost:3080/events | python3 -m json.tool
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
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ curl -s http://localhost:3080/health | python3 -m json.tool
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
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ kubectl delete pod -l app=gateway
pod "gateway-6fc44f68c5-cjdk6" deleted
[1]+  Terminated              kubectl port-forward svc/gateway 3080:8080


timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ kubectl get pods -w
NAME                        READY   STATUS    RESTARTS   AGE
events-859d5c5c98-vx58f     1/1     Running   0          99s
gateway-6fc44f68c5-j6zzk    1/1     Running   0          5s
payments-58fb468db-vplxz    1/1     Running   0          99s
postgres-76cd478b6b-rqt25   1/1     Running   0          8m54s
redis-c46d5dffc-j7r25       1/1     Running   0          8m48s
```

As shown above, container gateway-6fc44f68c5-j6zzk recovered 5s 

5. Answer: "How long did K8s take to recreate the deleted pod? How does this compare to docker-compose restart?"

Kubernetes recreated the deleted pod automatically in about 3 seconds 
without any manual intervention. This happens because the Deployment controller 
constantly reconciles the desired state (replicas: 1) with the actual state. When the pod 
is deleted, the replica count drops to zero, so the ReplicaSet immediately schedules a new one.

In contrast, in Lab 1 with docker-compose, if a container is stopped, it remains down until 
I manually start or restart it using docker compose start or restart. Compose does not have a 
reconciliation loop — it only executes explicit commands.

The key difference is that Kubernetes is declarative and self-healing, continuously ensuring 
the desired state is maintained, while docker-compose is imperative and requires manual management.

## Task 2 — Probes & Resource Limits

1. `kubectl describe pod` output showing probes configured

```bash
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ kubectl describe pod -l app=gateway | grep -A 5 "Liveness\|Readiness"
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
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ kubectl delete pod -l app=redis
pod "redis-c46d5dffc-j7r25" deleted

events-6bbdc6d896-r4rqk     0/1     Running   0          28s
events-859d5c5c98-vx58f     1/1     Running   0          15m
gateway-854488bf7c-h56zw    1/1     Running   0          28s
payments-58fb468db-vplxz    1/1     Running   0          15m
payments-7945f6dc6d-47pm6   0/1     Running   0          28s
postgres-76cd478b6b-rqt25   1/1     Running   0          22m
redis-c46d5dffc-xsqgq       1/1     Running   0          7s
payments-7945f6dc6d-47pm6   0/1     Running   1 (0s ago)   41s
events-6bbdc6d896-r4rqk     0/1     Running   1 (1s ago)   41s


timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ kubectl describe pod -l app=events | grep -A 3 "Readiness"
    Readiness:      http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      DB_HOST:     postgres
      DB_PORT:     5432
--
  Warning  Unhealthy  27s (x19 over 103s)  kubelet            Readiness probe failed: Get "http://10.42.0.15:8080/health": dial tcp 10.42.0.15:8080: connect: connection refused    
  Normal   Pulled     24s (x3 over 104s)   kubelet            Container image "quickticket-events:v1" already present on machine and can be accessed by the pod
  Normal   Created    24s (x3 over 104s)   kubelet            Container created
  Warning  Unhealthy  24s (x6 over 84s)    kubelet            Liveness probe failed: Get "http://10.42.0.15:8080/health": dial tcp 10.42.0.15:8080: connect: connection refused    
```

3. `kubectl describe node` output showing allocated resources 

Command `$ kubectl describe node $(kubectl get nodes -o name | head -1) | grep -A 10 "Allocated resources"`
didn't work (see its output below), so I ran `$ kubectl describe $(kubectl get nodes -o name | head -n 1) | grep -A 10 "Allocated resources"`:
```bash
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ kubectl describe node $(kubectl get nodes -o name | head -1) | grep -A 10 "Allocated resources"
error: there is no need to specify a resource type as a separate argument when passing arguments in resource/name form (e.g. 'kubectl get resource/<resource_name>' instead of 'kubectl get resource resource/<resource_name>'
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ kubectl describe $(kubectl get nodes -o name | head -n 1) | grep -A 10 "Allocated resources"
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

Liveness probe failure means Kubernetes considers the container unhealthy and restarts it. It’s used to detect situations where the application is stuck, deadlocked, or cannot recover without a restart.

Readiness probe failure means the container is still running, but it is temporarily unable to serve traffic. Kubernetes removes the pod from the Service endpoints, so it receives no requests, but it is not restarted.
