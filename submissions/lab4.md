# Lab 4 Submission — Kubernetes: Deploy QuickTicket to a Cluster

## Task 1 — Write Manifests & Deploy to k3d

Manifests written from scratch in `k8s/`:
- `k8s/postgres.yaml` — Deployment + ClusterIP Service (postgres:17-alpine)
- `k8s/redis.yaml` — Deployment + ClusterIP Service (redis:7-alpine)
- `k8s/events.yaml` — Deployment + ClusterIP Service with DB/Redis env vars
- `k8s/payments.yaml` — Deployment + ClusterIP Service
- `k8s/gateway.yaml` — Deployment + ClusterIP Service with upstream URLs

Cluster created with `k3d cluster create quickticket`. Images built locally and imported with `imagePullPolicy: Never`.

---

### 4.1: `kubectl get nodes`

```
$ kubectl get nodes
NAME                       STATUS   ROLES                  AGE     VERSION
k3d-quickticket-server-0   Ready    control-plane,master   6d18h   v1.21.7+k3s1
```

---

### 4.5–4.6: `kubectl get pods,svc` (all running)

```
$ kubectl get pods,svc
NAME                            READY   STATUS    RESTARTS   AGE
pod/redis-b78bf6f8-cbdt2        1/1     Running   0          110m
pod/payments-65b7f98fdf-fzllc   1/1     Running   0          110m
pod/postgres-6c74cfd94f-242qt   1/1     Running   0          110m
pod/gateway-869fb599b-ksmgq     1/1     Running   0          1m
pod/events-864d68bb8c-gkt98     1/1     Running   0          110m

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    6d18h
service/postgres     ClusterIP   10.43.172.3     <none>        5432/TCP   6d18h
service/redis        ClusterIP   10.43.159.106   <none>        6379/TCP   6d18h
service/events       ClusterIP   10.43.205.251   <none>        8081/TCP   6d18h
service/payments     ClusterIP   10.43.123.177   <none>        8082/TCP   6d18h
service/gateway      ClusterIP   10.43.101.3     <none>        8080/TCP   6d18h
```

Database seeded with:

```bash
kubectl cp app/seed.sql default/$(kubectl get pod -l app=postgres -o jsonpath='{.items[0].metadata.name}'):/tmp/seed.sql
kubectl exec $(kubectl get pod -l app=postgres -o jsonpath='{.items[0].metadata.name}') -- \
  psql -U quickticket -d quickticket -f /tmp/seed.sql
```

---

### 4.6: Full stack via port-forward

```bash
$ kubectl port-forward svc/gateway 3080:8080 &
$ curl -s http://localhost:3080/events | python3 -m json.tool
```

```json
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

```bash
$ curl -s http://localhost:3080/health | python3 -m json.tool
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

---

### 4.7: K8s self-healing — pod deletion and auto-recovery

```bash
$ kubectl delete pod -l app=gateway
$ kubectl get pods -l app=gateway -w
```

```
NAME                      READY   STATUS              RESTARTS   AGE
gateway-869fb599b-2wg74   1/1     Terminating         0          82s
gateway-869fb599b-ksmgq   0/1     ContainerCreating   0          0s
gateway-869fb599b-2wg74   0/1     Terminating         0          83s
gateway-869fb599b-ksmgq   1/1     Running             0          1s
gateway-869fb599b-2wg74   0/1     Terminating         0          84s
gateway-869fb599b-2wg74   0/1     Terminating         0          84s
```

New pod reached `Running` and `1/1 Ready` in approximately **2.5 seconds** (measured from delete to new pod ready).

---

### 4.8: K8s recovery vs docker-compose

	How long did K8s take to recreate the deleted pod?

About 2–3 seconds from `kubectl delete pod` to the replacement gateway pod showing `1/1 Running`.

	How does this compare to docker-compose restart?

In Lab 1, when a service was stopped with `docker compose stop <service>`, it stayed down until we manually ran `docker compose start <service>`. There was no automatic recovery — downtime lasted as long as we took to notice and fix it.

With Kubernetes, the Deployment controller detected that the desired replica count (1) was not met after the pod was deleted, and immediately scheduled a replacement. No manual intervention was needed. The new pod was created, the container started, and traffic was routable again within a few seconds.

This is the core difference: docker-compose does not self-heal deleted/stopped containers by default, while Kubernetes continuously reconciles desired state and recreates failed or missing pods automatically.
