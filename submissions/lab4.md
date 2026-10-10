# Lab 4 Report
## Task 1
**Paste into `submissions/lab4.md`** (report only, not manifests):
1. Output of `kubectl get nodes`
```
user@MacBook-Air sre-intro % kubectl get nodes

NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   17s   v1.35.5+k3s1
```
2. Output of `kubectl get pods,svc` showing all running
```
user@MacBook-Air app % kubectl get pods,svc
NAME                            READY   STATUS    RESTARTS   AGE
pod/events-5b75c4b498-bmlg8     1/1     Running   0          9m33s
pod/gateway-54b9b8b8c7-qzrr6    1/1     Running   0          9m33s
pod/payments-c67d96686-x2zpc    1/1     Running   0          9m33s
pod/postgres-657d459d58-422pn   1/1     Running   0          9m33s
pod/redis-64d9775897-wdtnz      1/1     Running   0          9m32s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.83.170    <none>        8081/TCP   68m
service/gateway      ClusterIP   10.43.118.250   <none>        8080/TCP   68m
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    107m
service/payments     ClusterIP   10.43.130.225   <none>        8082/TCP   68m
service/postgres     ClusterIP   10.43.30.179    <none>        5432/TCP   89m
service/redis        ClusterIP   10.43.50.241    <none>        6379/TCP   89m
user@MacBook-Air app % 
```
3. Output of `curl localhost:3080/events` via port-forward (proving the full stack works)
```
user@MacBook-Air app % curl -s http://localhost:3080/events | python3 -m json.tool

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
```
4. Output of `kubectl get pods -w` during pod deletion — showing auto-recovery
```
user@MacBook-Air app % kubectl get pods -w

NAME                        READY   STATUS    RESTARTS   AGE
events-5b75c4b498-bmlg8     1/1     Running   0          19m
gateway-54b9b8b8c7-qzrr6    1/1     Running   0          19m
payments-c67d96686-x2zpc    1/1     Running   0          19m
postgres-657d459d58-422pn   1/1     Running   0          19m
redis-64d9775897-wdtnz      1/1     Running   0          19m
gateway-54b9b8b8c7-qzrr6    1/1     Terminating   0          19m
gateway-54b9b8b8c7-qzrr6    1/1     Terminating   0          19m
gateway-54b9b8b8c7-tb6ml    0/1     Pending       0          0s
gateway-54b9b8b8c7-tb6ml    0/1     Pending       0          0s
gateway-54b9b8b8c7-tb6ml    0/1     ContainerCreating   0          0s
gateway-54b9b8b8c7-qzrr6    0/1     Completed           0          19m
gateway-54b9b8b8c7-qzrr6    0/1     Completed           0          19m
gateway-54b9b8b8c7-qzrr6    0/1     Completed           0          19m
gateway-54b9b8b8c7-tb6ml    0/1     Running             0          2s
gateway-54b9b8b8c7-tb6ml    1/1     Running             0          8s
```
5. Answer: "How long did K8s take to recreate the deleted pod? How does this compare to docker-compose restart?"
It took about 8 secs, in case of docker, the total amout of time is calculated as human reaction(couple of mins)+command execution

## Task 2
**Paste into `submissions/lab4.md`:**
- `kubectl describe pod` output showing probes configured
```
user@MacBook-Air app % kubectl describe pod -l app=gateway | grep -A 5 "Liveness\|Readiness"

    Liveness:       http-get http://:8080/health delay=10s timeout=5s period=10s #success=1 #failure=3
    Readiness:      http-get http://:8080/health delay=5s timeout=3s period=5s #success=1 #failure=2
    Environment:
      EVENTS_URL:          http://events:8081
      PAYMENTS_URL:        http://payments:8082
      GATEWAY_TIMEOUT_MS:  5000
    Mounts:

```

- Output during Redis deletion showing readiness probe failure (`0/1 Ready`)
```
user@MacBook-Air app % kubectl delete pod -l app=redis            

pod "redis-64d9775897-wdtnz" deleted from default namespace
user@MacBook-Air app % kubectl describe pod -l app=events | grep -A 3 "Readiness"

    Readiness:  http-get http://:8081/health delay=5s timeout=3s period=5s #success=1 #failure=2
    Environment:
      DB_HOST:           postgres
      DB_PORT:           5432
--
  Warning  Unhealthy  26m   kubelet            spec.containers{events}: Readiness probe failed: Get "http://10.42.0.20:8081/health": dial tcp 10.42.0.20:8081: connect: connection refused
user@MacBook-Air app % 
user@MacBook-Air app % kubectl get pods -w

NAME                        READY   STATUS    RESTARTS   AGE
events-5b75c4b498-bmlg8     1/1     Running   0          26m
gateway-54b9b8b8c7-tb6ml    1/1     Running   0          7m24s
payments-c67d96686-x2zpc    1/1     Running   0          26m
postgres-657d459d58-422pn   1/1     Running   0          26m
redis-64d9775897-wdtnz      1/1     Running   0          26m
redis-64d9775897-wdtnz      1/1     Terminating   0          26m
redis-64d9775897-wdtnz      1/1     Terminating   0          26m
redis-64d9775897-wdtnz      0/1     Completed     0          26m
redis-64d9775897-sjtrf      0/1     Pending       0          0s
redis-64d9775897-sjtrf      0/1     Pending       0          0s
redis-64d9775897-sjtrf      0/1     ContainerCreating   0          0s
redis-64d9775897-sjtrf      1/1     Running             0          1s
redis-64d9775897-wdtnz      0/1     Completed           0          26m
redis-64d9775897-wdtnz      0/1     Completed           0          26m
```
- `kubectl describe node` output showing allocated resources
```
user@MacBook-Air app % kubectl describe node $(kubectl get nodes -o name | head -1 | cut -d'/' -f2) | grep -A 10 "Allocated resources" 

Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests     Limits
  --------           --------     ------
  cpu                450m (5%)    1 (12%)
  memory             844Mi (21%)  1962Mi (50%)
  ephemeral-storage  0 (0%)       0 (0%)
  hugepages-1Gi      0 (0%)       0 (0%)
  hugepages-2Mi      0 (0%)       0 (0%)
  hugepages-32Mi     0 (0%)       0 (0%)
  hugepages-64Ki     0 (0%)       0 (0%)

```
- Answer: "What's the difference between liveness and readiness probe failure? Which one should you use for checking database connectivity, and why?"
Liveness probe failure restarts the pod, while readiness probe failure removes the pod from the Service without restarting it