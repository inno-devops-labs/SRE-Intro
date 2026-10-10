# Lab 4 Submission

## Task 1. Kubernetes deployment

### Cluster

Command:

```bash
kubectl get nodes
```

Output:

```text
NAME                       STATUS   ROLES                  AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane,master   12m   v1.31.5+k3s1
```

### Pods and services

Command: 

```bash
kubectl get pods,svc
```

Output:

```text
NAME                            READY   STATUS    RESTARTS   AGE
pod/events-7d4558487-pv8p5      1/1     Running   0          3m12s
pod/gateway-55bf98fd46-2dshz    1/1     Running   0          3m12s
pod/payments-bf4c9687-sxf5m     1/1     Running   0          11m
pod/postgres-85ffd4fb9f-j5268   1/1     Running   0          12m
pod/redis-6d65768944-h78kt      1/1     Running   0          66s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.159.136   <none>        8081/TCP   12m
service/gateway      ClusterIP   10.43.141.33    <none>        8080/TCP   12m
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    14m
service/payments     ClusterIP   10.43.130.151   <none>        8082/TCP   12m
service/postgres     ClusterIP   10.43.36.46     <none>        5432/TCP   12m
service/redis        ClusterIP   10.43.47.84     <none>        6379/TCP   12m
```

### Full stack check

I initialized PostgreSQL with `app/seed.sql`, then used port-forward:

```bash
kubectl port-forward svc/gateway 3080:8080
curl -s http://localhost:3080/events
```

Output:

```json
[{"id":1,"name":"Go Conference 2026","venue":"Main Hall A","date":"2026-09-15T09:00:00+00:00","total_tickets":100,"price_cents":5000,"available":100},{"id":4,"name":"Python Workshop","venue":"Lab 301","date":"2026-09-22T14:00:00+00:00","total_tickets":25,"price_cents":2000,"available":25},{"id":2,"name":"SRE Meetup","venue":"Room 204","date":"2026-10-01T18:00:00+00:00","total_tickets":30,"price_cents":0,"available":30},{"id":5,"name":"Kubernetes Deep Dive","venue":"Auditorium B","date":"2026-10-10T10:00:00+00:00","total_tickets":80,"price_cents":8000,"available":80},{"id":3,"name":"Cloud Native Summit","venue":"Expo Center","date":"2026-11-20T10:00:00+00:00","total_tickets":500,"price_cents":15000,"available":500}]
```

### Self-healing

Command:

```bash
kubectl delete pod -l app=gateway
kubectl get pods -w
```

Output:

```text
gateway-7766c54df8-wrfw8    1/1     Terminating         0          2m23s
gateway-7766c54df8-2g4ks    0/1     Pending             0          0s
gateway-7766c54df8-2g4ks    0/1     ContainerCreating   0          0s
gateway-7766c54df8-2g4ks    0/1     Running             0          0s
gateway-7766c54df8-wrfw8    0/1     Completed           0          2m24s
gateway-7766c54df8-2g4ks    1/1     Running             0          5s
```

Kubernetes recreated the gateway pod in about 5 seconds. In docker-compose I had to restart a stopped container manually, but here the Deployment controller noticed that the actual state did not match the desired state and created a replacement pod automatically.

## Task 2. Probes and resource limits

### Probes

Command:

```bash
kubectl describe pod -l app=events
```

Relevant output:

```text
Containers:
  events:
    Image:          quickticket-events:v1
    Port:           8081/TCP
    Ready:          True
    Restart Count:  0
    Limits:
      cpu:     200m
      memory:  256Mi
    Requests:
      cpu:      50m
      memory:   64Mi
    Liveness:   tcp-socket :8081 delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

I used readiness for `/health`, because this endpoint checks dependencies. Liveness checks only that the process port is open.

### Readiness failure

I made Redis unavailable long enough to observe the readiness probe:

```bash
kubectl scale deployment/redis --replicas=0
kubectl get pods -w
```

Output:

```text
redis-6d65768944-zksdk      1/1     Terminating   0          2m51s
redis-6d65768944-zksdk      0/1     Completed     0          2m52s
gateway-55bf98fd46-2dshz    0/1     Running       0          101s
events-7d4558487-pv8p5      0/1     Running       0          105s
```

During the failure, `events` stayed Running but became not Ready:

```text
Ready:          False
Restart Count:  0
Liveness:       tcp-socket :8081 delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:      http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2

Warning  Unhealthy  2s (x4 over 17s)  kubelet  Readiness probe failed: HTTP probe failed with statuscode: 503
```

After returning Redis to one replica, the pods became Ready again:

```text
redis-6d65768944-h78kt      1/1     Running   0          1s
events-7d4558487-pv8p5      1/1     Running   0          2m10s
gateway-55bf98fd46-2dshz    1/1     Running   0          2m15s
```

### Resource allocation

Command:

```bash
kubectl describe node k3d-quickticket-server-0
```

Relevant output:

```text
Non-terminated Pods:          (10 in total)
  Namespace                   Name                                       CPU Requests  CPU Limits  Memory Requests  Memory Limits  Age
  ---------                   ----                                       ------------  ----------  ---------------  -------------  ---
  default                     events-7d4558487-pv8p5                     50m (0%)      200m (1%)   64Mi (0%)        256Mi (3%)     33s
  default                     gateway-55bf98fd46-2dshz                   50m (0%)      200m (1%)   64Mi (0%)        256Mi (3%)     33s
  default                     payments-bf4c9687-sxf5m                    50m (0%)      200m (1%)   64Mi (0%)        256Mi (3%)     8m43s
  default                     postgres-85ffd4fb9f-j5268                  50m (0%)      200m (1%)   64Mi (0%)        256Mi (3%)     10m
  default                     redis-6d65768944-zksdk                     50m (0%)      200m (1%)   64Mi (0%)        256Mi (3%)     114s

Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (4%)   1 (9%)
  memory             460Mi (5%)  1450Mi (18%)
  ephemeral-storage  0 (0%)      0 (0%)
```

### Liveness vs readiness

Liveness failure means Kubernetes treats the container as broken and restarts it. Readiness failure means the pod is still running, but Kubernetes removes it from Service endpoints until it can serve traffic again.

For database or Redis connectivity I should use readiness, not liveness. Restarting the app does not fix a database outage; it only adds more churn. Readiness is enough to stop routing traffic to a pod that cannot serve requests because a dependency is down.
