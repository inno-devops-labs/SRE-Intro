# Lab 4 Submission

## 1) kubectl get nodes

```text
$ kubectl get nodes -o wide
NAME                       STATUS   ROLES           AGE   VERSION        INTERNAL-IP   EXTERNAL-IP   OS-IMAGE           KERNEL-VERSION      CONTAINER-RUNTIME
k3d-quickticket-server-0   Ready    control-plane   15h   v1.35.5+k3s1   172.20.0.2    <none>        K3s v1.35.5+k3s1   6.8.0-138-generic   containerd://2.2.3-k3s1
```

## 2) kubectl get pods,svc output

```text
$ kubectl get pods,svc -o wide
NAME                            READY   STATUS    RESTARTS      AGE    IP           NODE                       NOMINATED NODE   READINESS GATES
pod/events-675d86c77-vjrcs      1/1     Running   1 (15h ago)   15h    10.42.0.10   k3d-quickticket-server-0   <none>           <none>
pod/gateway-7cd55d8774-97rks    1/1     Running   0             4m3s   10.42.0.15   k3d-quickticket-server-0   <none>           <none>
pod/payments-d7dc94485-5m27m    1/1     Running   0             15h    10.42.0.9    k3d-quickticket-server-0   <none>           <none>
pod/postgres-78489d7f5f-7msjr   1/1     Running   0             15h    10.42.0.12   k3d-quickticket-server-0   <none>           <none>
pod/redis-6fcfb5475d-x7z7k      1/1     Running   0             15h    10.42.0.13   k3d-quickticket-server-0   <none>           <none>

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE   SELECTOR
service/events       ClusterIP   10.43.183.233   <none>        8081/TCP   15h   app=events
service/gateway      ClusterIP   10.43.61.96     <none>        8080/TCP   15h   app=gateway
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    15h   <none>
service/payments     ClusterIP   10.43.80.50     <none>        8082/TCP   15h   app=payments
service/postgres     ClusterIP   10.43.99.246    <none>        5432/TCP   15h   app=postgres
service/redis        ClusterIP   10.43.198.122   <none>        6379/TCP   15h   app=redis
```

## 3) curl localhost:3080/events via port-forward

```text
$ kubectl port-forward svc/gateway 3080:8080
$ curl -sS http://localhost:3080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}

$ curl -sS http://localhost:3080/events | python3 -m json.tool | sed -n '1,120p'
[
    {
        "id": 1,
        "name": "Go Conference 2026",
        "venue": "Main Hall A",
        "date": "2026-09-15T09:00:00+00:00",
        "total_tickets": 100,
        "price_cents": 5000,
        "available": 90
    },
    {
        "id": 4,
        "name": "Python Workshop",
        "venue": "Lab 301",
        "date": "2026-09-22T14:00:00+00:00",
        "total_tickets": 25,
        "price_cents": 2000,
        "available": 18
    },
    {
        "id": 2,
        "name": "SRE Meetup",
        "venue": "Room 204",
        "date": "2026-10-01T18:00:00+00:00",
        "total_tickets": 30,
        "price_cents": 0,
        "available": 25
    },
    {
        "id": 5,
        "name": "Kubernetes Deep Dive",
        "venue": "Auditorium B",
        "date": "2026-10-10T10:00:00+00:00",
        "total_tickets": 80,
        "price_cents": 8000,
        "available": 73
    },
    {
        "id": 3,
        "name": "Cloud Native Summit",
        "venue": "Expo Center",
        "date": "2026-11-20T10:00:00+00:00",
        "total_tickets": 500,
        "price_cents": 15000,
        "available": 489
    }
]
```

## 4) kubectl get pods -w during deletion

```text
$ kubectl delete pod -l app=gateway --wait=false
$ timeout 90s kubectl get pods -w -l app=gateway --no-headers
pod "gateway-7cd55d8774-97rks" deleted from default namespace
gateway-7cd55d8774-97rks   1/1   Terminating   0     4m5s
gateway-7cd55d8774-xsrs5   0/1   Pending       0     0s
gateway-7cd55d8774-xsrs5   0/1   Pending       0     0s
gateway-7cd55d8774-xsrs5   0/1   ContainerCreating   0     0s
gateway-7cd55d8774-97rks   0/1   Completed           0     4m5s
gateway-7cd55d8774-97rks   0/1   Completed           0     4m6s
gateway-7cd55d8774-97rks   0/1   Completed           0     4m6s
gateway-7cd55d8774-xsrs5   0/1   Running             0     2s
gateway-7cd55d8774-xsrs5   1/1   Running             0     13s
```

The recreated pod was available again in about 13 seconds after deletion.

## 5) Answer: recovery time and comparison to docker-compose

Kubernetes recreated the deleted gateway pod in about 13 seconds, from the first `Pending`/`ContainerCreating` state to a ready `Running` pod. This is much faster and more automatic than docker-compose recovery, where a failed container required a manual `docker compose start` or restart action. Kubernetes self-healing handles the replacement without manual intervention once the ReplicaSet notices the missing pod.

---

## Optional Task 2 notes

- Liveness probe: restarts the pod if the app fails its health check.
- Readiness probe: removes the pod from Service endpoints so it stops receiving traffic.
- For database connectivity, readiness is the right choice: if Postgres or Redis is unavailable, you want traffic stopped until the dependency is back, not a restart loop.

---

## Bonus (Helm) notes

```yaml
# Chart.yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
```

```yaml
# values.yaml
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

```text
$ helm list
# NAME      NAMESPACE   REVISION  UPDATED   STATUS   CHART          APP VERSION
# quickticket default     1         ...       deployed quickticket-0.1.0
```

```text
$ kubectl get pods
# includes gateway, events, payments, postgres, redis
```

If kube-prometheus-stack is installed, it creates several monitoring pods (Prometheus, Alertmanager, Grafana, and node-exporter/other operators depending on the chart defaults).
