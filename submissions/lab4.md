# Lab 4 Kubernetes: Deploy QuickTicket to a Cluster

# Task 1

## Output of kubectl get nodes

```bash
$ kubectl get nodes

NAME                       STATUS   ROLES           AGE   VERSION

k3d-quickticket-server-0   Ready    control-plane   14m   v1.35.5+k3s1
```

## Output of kubectl get pods,svc showing all running

```bash
$ kubectl get pods

NAME                      READY   STATUS    RESTARTS   AGE

postgres-7c7ffc4b-59gt2   1/1     Running   0          74s

redis-c46d5dffc-6x6nr     1/1     Running   0          67s

$ kubectl get svc

NAME         TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE

kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    22m

postgres     ClusterIP   10.43.157.248   <none>        5432/TCP   119s

redis        ClusterIP   10.43.104.130   <none>        6379/TCP   111s
```

## Output of curl localhost:3080/events via port-forward (proving the full stack works)

```bash
$ curl -s http://localhost:3080/events

[{"id":1,"name":"Go Conference 2026","venue":"Main Hall A","date":"2026-09-15T09:00:00+00:00","total_tickets":100,"price_cents":5000,"available":100},{"id":4,"name":"Python Workshop","venue":"Lab 301","date":"2026-09-22T14:00:00+00:00","total_tickets":25,"price_cents":2000,"available":25},{"id":2,"name":"SRE Meetup","venue":"Room 204","date":"2026-10-01T18:00:00+00:00","total_tickets":30,"price_cents":0,"available":30},{"id":5,"name":"Kubernetes Deep Dive","venue":"Auditorium B","date":"2026-10-10T10:00:00+00:00","total_tickets":80,"price_cents":8000,"available":80},{"id":3,"name":"Cloud Native Summit","venue":"Expo Center","date":"2026-11-20T10:00:00+00:00","total_tickets":500,"price_cents":15000,"available":500}]

$ curl -s http://localhost:3080/health

{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

## Output of kubectl get pods -w during pod deletion — showing auto-recovery

```bash
$ kubectl delete pod -l app=gateway

kubectl get pods -w

pod "gateway-6fc44f68c5-qx5sg" deleted from default namespace

NAME                       READY   STATUS    RESTARTS   AGE

events-859d5c5c98-mxbff    1/1     Running   0          12m

gateway-6fc44f68c5-m7mbd   1/1     Running   0          1s

payments-58fb468db-prtsp   1/1     Running   0          12m

postgres-7c7ffc4b-59gt2    1/1     Running   0          18m

redis-c46d5dffc-6x6nr      1/1     Running   0          17m
```

## Answer: "How long did K8s take to recreate the deleted pod? How does this compare to docker-compose restart?"

Approximately 3–5 seconds.

In Lab 1, when a service failed or was stopped, I had to manually run:

```bash
docker compose start <service>
```

to bring it back. This took about 1–2 minutes because I had to notice the failure, switch to the terminal, and execute the command manually.


# Task 2

## kubectl describe pod output showing probes configured

```bash
$ kubectl describe pod -l app=gateway | grep -A 5 "Liveness\|Readiness"

    Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      EVENTS_URL:          http://events:8081
      PAYMENTS_URL:        http://payments:8082
      GATEWAY_TIMEOUT_MS:  5000
    Mounts:
--
  Warning  Unhealthy  13s (x2 over 14s)  kubelet  Readiness probe failed: Get "http://10.42.0.18:8080/health": dial tcp 10.42.0.18:8080: connect: connection refused
```

## Output during Redis deletion showing readiness probe failure (0/1 Ready)

```bash
$ kubectl delete pod -l app=redis

pod "redis-c46d5dffc-6x6nr" deleted from default namespace


axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab4)

$ kubectl get pods -w

NAME                       READY   STATUS    RESTARTS   AGE

events-78696fcf65-74qwl    1/1     Running   0          2m37s

gateway-7cd55d8774-hpzxc   1/1     Running   0          2m37s

payments-d7dc94485-nl4s9   1/1     Running   0          2m37s

postgres-7c7ffc4b-59gt2    1/1     Running   0          38m

redis-c46d5dffc-ph9jh      1/1     Running   0          6s


axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab4)

$ kubectl describe pod -l app=events | grep -A 3 "Readiness"

    Readiness:  http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      DB_HOST:     postgres
      DB_PORT:     5432
--
  Warning  Unhealthy  3m8s (x2 over 3m9s)  kubelet  Readiness probe failed: Get "http://10.42.0.19:8081/health": dial tcp 10.42.0.19:8081: connect: connection refused
```

## kubectl describe node output showing allocated resources

```bash
$ kubectl describe node $NODE | grep -A 10 "Allocated resources"

Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)

  Resource           Requests    Limits
  --------           --------    ------
  cpu                350m (2%)   600m (5%)
  memory             332Mi (4%)  938Mi (12%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)

Events:              <none>
```

## Answer: "What's the difference between liveness and readiness probe failure? Which one should you use for checking database connectivity, and why?"

### Difference between liveness and readiness probe failure

* **Liveness probe failure:** The pod is killed and restarted. This is used to detect when the application itself is dead, stuck, or in a broken state that can only be fixed by restarting.

* **Readiness probe failure:** The pod is removed from the Service (no traffic is routed to it), but the pod is **not** restarted. This is used to detect when the application is alive but not yet ready to handle requests (e.g., still loading, or a dependency is unavailable).

### Which one should you use for checking database connectivity, and why?

**Use readiness probe for database connectivity.**

If the database is down, restarting the pod won't fix the problem — the pod will just restart and still fail to connect. Instead, we want to stop sending traffic to the pod while the dependency is unavailable, and automatically restore traffic once the database recovers. This prevents user requests from failing and allows the system to degrade gracefully.

# Bonus Task

## Your Chart.yaml and values.yaml

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
```

```yaml
gateway:
  replicas: 1
  image: quickticket-gateway:v1
  timeoutMs: "5000"

events:
  replicas: 1
  image: quickticket-events:v1
  db:
    host: postgres
    port: "5432"
    name: quickticket
    user: quickticket
    password: quickticket

  redis:
    host: redis
    port: "6379"

payments:
  replicas: 1
  image: quickticket-payments:v1
  failureRate: "0.0"
  latencyMs: "0"
```

## Output of helm list showing the installed release

```bash
$ helm list

NAME            NAMESPACE       REVISION        UPDATED                                 STATUS      CHART               APP VERSION

quickticket     default         1               2026-06-17 14:59:33.9139099 +0800 CST   deployed    quickticket-0.1.0
```

## Output of kubectl get pods after Helm install

```bash
$ kubectl get pods

NAME                       READY   STATUS    RESTARTS      AGE

events-78696fcf65-r94cs    0/1     Running   0             7s

gateway-7cd55d8774-4rf5b   1/1     Running   4 (54s ago)   3m34s

payments-d7dc94485-k75dp   1/1     Running   0             3m34s

postgres-7c7ffc4b-49lgc    1/1     Running   0             44s

redis-c46d5dffc-7jwwd      1/1     Running   0             36s
```
