# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster
## Proof of work by Viktoriya Yurina B24-CBS-01

### Task 1 — Write Manifests & Deploy to k3d
#### Output of kubectl get nodes
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ sudo kubectl get nodes
NAME                       STATUS   ROLES           AGE     VERSION
k3d-quickticket-server-0   Ready    control-plane   5m51s   v1.35.5+k3s1
```
#### Output of kubectl get pods,svc showing all running
```
┌──(shersh㉿kali)-[~/SRE-Intro/k8s]
└─$ sudo kubectl get pods
NAME                      READY   STATUS    RESTARTS   AGE
postgres-7c7ffc4b-wvmfp   1/1     Running   0          35s
redis-c46d5dffc-9c54b     1/1     Running   0          27s
                                                                                                                             
┌──(shersh㉿kali)-[~/SRE-Intro/k8s]
└─$ sudo kubectl get svc 
NAME         TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    18m
postgres     ClusterIP   10.43.182.129   <none>        5432/TCP   43s
redis        ClusterIP   10.43.254.192   <none>        6379/TCP   35s

```
#### Output of curl localhost:3080/events via port-forward (proving the full stack works)
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ Forwarding from 127.0.0.1:3080 -> 8080
Forwarding from [::1]:3080 -> 8080
Handling connection for 3080
```
In another tab:
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ curl -s http://localhost:3080/events | python3 -m json.tool
[
    {
        "id": 1,
        "name": "Go Conference 2026",
        "venue": "Main Hall A",
        "date": "2026-09-15T09:00:00+00:00",
        "total_tickets": 100,
        "price_cents": 5000,
        "available": 84
    },
    {
        "id": 4,
        "name": "Python Workshop",
        "venue": "Lab 301",
        "date": "2026-09-22T14:00:00+00:00",
        "total_tickets": 25,
        "price_cents": 2000,
        "available": 19
    },
    {
        "id": 2,
        "name": "SRE Meetup",
        "venue": "Room 204",
        "date": "2026-10-01T18:00:00+00:00",
        "total_tickets": 30,
        "price_cents": 0,
        "available": 18
    },
    {
        "id": 5,
        "name": "Kubernetes Deep Dive",
        "venue": "Auditorium B",
        "date": "2026-10-10T10:00:00+00:00",
        "total_tickets": 80,
        "price_cents": 8000,
        "available": 60
    },
    {
        "id": 3,
        "name": "Cloud Native Summit",
        "venue": "Expo Center",
        "date": "2026-11-20T10:00:00+00:00",
        "total_tickets": 500,
        "price_cents": 15000,
        "available": 487
    }
]
```
#### Output of kubectl get pods -w during pod deletion — showing auto-recovery
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ kubectl delete pod -l app=gateway
pod "gateway-6fc44f68c5-l5w54" deleted
                                                                                                                             
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ kubectl get pods -w 
NAME                       READY   STATUS    RESTARTS   AGE
events-6c4df7d6-98s2k      1/1     Running   0          13m
gateway-6fc44f68c5-578sv   1/1     Running   0          5s
payments-58fb468db-8cklk   1/1     Running   0          13m
postgres-7c7ffc4b-wvmfp    1/1     Running   0          16m
redis-c46d5dffc-9c54b      1/1     Running   0          15m
```
#### Answer: "How long did K8s take to recreate the deleted pod? How does this compare to docker-compose restart?"
Kubernetes recreated the deleted pod in a few seconds (~5s) thanks to the ReplicaSet controller continuously reconciling desired state, whereas docker-compose restart is usually faster but only restarts a container manually and does not provide self-healing or automatic replacement of failed replicas. Comparing to the first lab, it tooks up to 1-2 minutes to restart service via docker-compose.

### Task 2 — Probes & Resource Limits
#### kubectl describe pod output showing probes configured
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ kubectl describe pod -l app=gateway | grep -A 5 "Liveness\|Readiness"
    Liveness:       http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:      http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      EVENTS_URL:          http://events:8081
      PAYMENTS_URL:        http://payments:8082
      GATEWAY_TIMEOUT_MS:  5000

┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ kubectl describe pod -l app=events | grep -A 5 "Liveness\|Readiness"
    Liveness:       http-get http://:8081/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:      http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      DB_HOST:           postgres
      DB_PORT:           5432
      DB_NAME:           quickticket
      DB_USER:           quickticket

┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ kubectl describe pod -l app=payments | grep -A 5 "Liveness\|Readiness"
    Liveness:       http-get http://:8082/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:      http-get http://:8082/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      PAYMENT_FAILURE_RATE:  0.0
      PAYMENT_LATENCY_MS:    0
```
#### Output during Redis deletion showing readiness probe failure (0/1 Ready)
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ kubectl delete pod -l app=redis
pod "redis-c46d5dffc-n4gjc" deleted

┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ kubectl get pods -w
NAME                        READY   STATUS    RESTARTS   AGE
events-6cf4b4bd44-n79gw     0/1     Running   0          51m
gateway-854488bf7c-dxqhx    0/1     Running   0          51m
payments-68dcdf7696-dmjrk   1/1     Running   0          51m
postgres-7c7ffc4b-wvmfp     1/1     Running   0          84m

┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ kubectl describe pod -l app=events | grep -A 3 "Readiness"
    Readiness:      http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      DB_HOST:           postgres
      DB_PORT:           5432
--
  Warning  Unhealthy  52m   kubelet            Readiness probe failed: Get "http://10.42.0.15:8081/health": dial tcp 10.42.0.15:8081: connect: connection refused
  Warning  Unhealthy  18m   kubelet            Readiness probe failed: Get "http://10.42.0.15:8081/health": context deadline exceeded (Client.Timeout exceeded while awaiting headers)
```
#### kubectl describe node output showing allocated resources
Command from the lab did not work for me, so I modified it a bit.
```
┌──(shersh㉿kali)-[~/SRE-Intro/k8s]
└─$ NODE=$(kubectl get nodes -o jsonpath="{.items[0].metadata.name}")
kubectl describe node $NODE | grep -A 10 "Allocated resources"
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                350m (2%)   600m (5%)
  memory             332Mi (2%)  938Mi (6%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
Events:              <none>
    
```
#### Answer: "What's the difference between liveness and readiness probe failure? Which one should you use for checking database connectivity, and why?"
A liveness probe failure means the container is unhealthy and Kubernetes will restart it. A readiness probe failure means the container is running but should not receive traffic, so it is removed from Service endpoints without restarting.\
Database connectivity should be checked in a readiness probe, because if the database is unavailable the application should stop receiving traffic, not restart continuously.
