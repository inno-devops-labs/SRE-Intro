# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

## Task 1 — Write Manifests & Deploy to k3d

```sh
[ustkost@prime SRE-Intro]$ kubectl get nodes
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   15m   v1.35.5+k3s1
```

```sh
[ustkost@prime SRE-Intro]$ kubectl get pods,svc
NAME                           READY   STATUS    RESTARTS   AGE
pod/events-6c4df7d6-2gvtx      1/1     Running   0          3m36s
pod/gateway-6fc44f68c5-rtd2c   1/1     Running   0          3m36s
pod/payments-58fb468db-2hsjv   1/1     Running   0          3m36s
pod/postgres-7c7ffc4b-rpcvx    1/1     Running   0          6m17s

NAME                 TYPE        CLUSTER-IP     EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.17.29    <none>        8081/TCP   3m37s
service/gateway      ClusterIP   10.43.14.202   <none>        8080/TCP   3m37s
service/kubernetes   ClusterIP   10.43.0.1      <none>        443/TCP    15m
service/payments     ClusterIP   10.43.48.129   <none>        8082/TCP   3m37s
service/postgres     ClusterIP   10.43.26.22    <none>        5432/TCP   6m17s
service/redis        ClusterIP   10.43.128.42   <none>        6379/TCP   6m12s
```

```sh
[ustkost@prime SRE-Intro]$ curl localhost:3080/events
Handling connection for 3080
[{"id":1,"name":"Go Conference 2026","venue":"Main Hall A","date":"2026-09-15T09:00:00+00:00","total_tickets":100,"price_cents":5000,"available":100},{"id":4,"name":"Python Workshop","venue":"Lab 301","date":"2026-09-22T14:00:00+00:00","total_tickets":25,"price_cents":2000,"available":25},{"id":2,"name":"SRE Meetup","venue":"Room 204","date":"2026-10-01T18:00:00+00:00","total_tickets":30,"price_cents":0,"available":30},{"id":5,"name":"Kubernetes Deep Dive","venue":"Auditorium B","date":"2026-10-10T10:00:00+00:00","total_tickets":80,"price_cents":8000,"available":80},{"id":3,"name":"Cloud Native Summit","venue":"Expo Center","date":"2026-11-20T10:00:00+00:00","total_tickets":500,"price_cents":15000,"available":500}]
```

```sh
[ustkost@prime SRE-Intro]$ kubectl get pods -w
NAME                       READY   STATUS    RESTARTS   AGE
events-6c4df7d6-2gvtx      1/1     Running   0          5m14s
gateway-6fc44f68c5-rtd2c   1/1     Running   0          5m14s
payments-58fb468db-2hsjv   1/1     Running   0          5m14s
postgres-7c7ffc4b-rpcvx    1/1     Running   0          7m55s
gateway-6fc44f68c5-rtd2c   1/1     Terminating   0          5m24s
gateway-6fc44f68c5-rtd2c   1/1     Terminating   0          5m24s
gateway-6fc44f68c5-lk5gd   0/1     Pending       0          0s
gateway-6fc44f68c5-lk5gd   0/1     Pending       0          0s
gateway-6fc44f68c5-lk5gd   0/1     ContainerCreating   0          0s
gateway-6fc44f68c5-rtd2c   0/1     Completed           0          5m24s
gateway-6fc44f68c5-lk5gd   1/1     Running             0          0s
gateway-6fc44f68c5-rtd2c   0/1     Completed           0          5m25s
gateway-6fc44f68c5-rtd2c   0/1     Completed           0          5m25s
```

### How long did K8s take to recreate the deleted pod? How does this compare to docker-compose restart?
Kubernetes recreated the deleted pod almost instantly, because the ReplicaSet controller immediately detected the missing pod and launched a new one. This is significantly faster than docker-compose restart, because it requires stopping and restarting the container sequentially.

## Task 2 — Probes & Resource Limits

```sh
[ustkost@prime app]$ kubectl describe pod -l app=gateway | grep -A 4 "Liveness\|Readiness"
    Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      EVENTS_URL:          http://events:8081
      PAYMENTS_URL:        http://payments:8082
      GATEWAY_TIMEOUT_MS:  5000
```

```sh
[ustkost@prime app]$ kubectl get pods -w
NAME                        READY   STATUS    RESTARTS   AGE
events-675d86c77-dxxkf      1/1     Running   0          3m54s
gateway-7cd55d8774-mcgp7    1/1     Running   0          3m53s
payments-d7dc94485-rwz7k    1/1     Running   0          3m53s
postgres-78489d7f5f-qlv84   1/1     Running   0          3m53s
redis-6fcfb5475d-5rx2v      1/1     Running   0          9s
redis-6fcfb5475d-5rx2v      1/1     Terminating   0          23s
redis-6fcfb5475d-phrwp      0/1     Pending       0          0s
redis-6fcfb5475d-phrwp      0/1     Pending       0          0s
redis-6fcfb5475d-5rx2v      1/1     Terminating   0          23s
redis-6fcfb5475d-phrwp      0/1     ContainerCreating   0          0s
redis-6fcfb5475d-5rx2v      0/1     Completed           0          23s
redis-6fcfb5475d-phrwp      1/1     Running             0          0s
```

```sh
[ustkost@prime app]$ kubectl describe node
...
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (3%)   1 (8%)
  memory             460Mi (2%)  1450Mi (9%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
Events:
  Type    Reason                          Age   From                   Message
...
```

### What's the difference between liveness and readiness probe failure? Which one should you use for checking database connectivity, and why?

Liveness probe failures restart the container when the application is deadlocked or stuck, while readiness probe failures temporarily remove the pod from the Services load balancer without restarting it

For checking database connectivity, you should use readiness probes because database connection issues are usually temporary and you dont want Kubernetes to repeatedly restart your pod for transient failures. This ensures the pod remains running and can automatically recover once the database becomes available again without unnecessary restarts that could make the situation worse
