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

**How long did K8s take to recreate the deleted pod?**

About 2–3 seconds from `kubectl delete pod` to the replacement gateway pod showing `1/1 Running`.

**How does this compare to docker-compose restart?**

In Lab 1, when a service was stopped with `docker compose stop <service>`, it stayed down until we manually ran `docker compose start <service>`. There was no automatic recovery — downtime lasted as long as we took to notice and fix it.

With Kubernetes, the Deployment controller detected that the desired replica count (1) was not met after the pod was deleted, and immediately scheduled a replacement. No manual intervention was needed. The new pod was created, the container started, and traffic was routable again within a few seconds.

This is the core difference: **docker-compose does not self-heal deleted/stopped containers by default**, while **Kubernetes continuously reconciles desired state** and recreates failed or missing pods automatically.

---

## Task 2 — Probes & Resource Limits

Probes added to `gateway`, `events`, and `payments`. Resource requests/limits (`50m/64Mi` requests, `200m/256Mi` limits) added to all five Deployments.

### 4.9: Probes configured (`kubectl describe pod -l app=gateway`)

```
$ kubectl describe pod -l app=gateway | grep -A 5 "Liveness\|Readiness"
    Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      EVENTS_URL:          http://events:8081
      PAYMENTS_URL:        http://payments:8082
      GATEWAY_TIMEOUT_MS:  5000
```

Similar probes are configured on `events` (port 8081) and `payments` (port 8082).

---

### 4.10: Readiness probe failure when Redis is unavailable

Redis was scaled to 0 replicas to keep it unavailable long enough to observe probe behaviour:

```bash
$ kubectl scale deployment redis --replicas=0
$ kubectl get pods -l app=events
```

```
NAME                      READY   STATUS    RESTARTS   AGE
events-587b965954-mb6ql   0/1     Running   0          108s
```

```
$ kubectl describe pod -l app=events | grep -A 3 "Readiness"
    Readiness:  http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      DB_HOST:           postgres
      DB_PORT:           5432
--
  Warning  Unhealthy  9s                   kubelet  spec.containers{events}: Liveness probe failed: HTTP probe failed with statuscode: 503
  Warning  Unhealthy  4s (x2 over 9s)      kubelet  spec.containers{events}: Readiness probe failed: HTTP probe failed with statuscode: 503
```

The events pod showed `0/1 Ready` — Kubernetes removed it from the Service endpoints so no traffic was routed to it. After scaling Redis back to 1 replica, the readiness probe passed and the pod returned to `1/1 Ready`.

---

### 4.11: Node allocated resources

```
$ kubectl describe node k3d-quickticket-server-0 | grep -A 10 "Allocated resources"
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                350m (2%)   1 (6%)
  memory             390Mi (1%)  1450Mi (5%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
```

Five QuickTicket containers each request `50m` CPU / `64Mi` memory → `250m` / `320Mi` from app workloads, plus cluster overhead.

---

### Liveness vs readiness for database connectivity

**What's the difference between liveness and readiness probe failure?**

- **Readiness failure:** the pod stays running but is removed from Service endpoints — no traffic is sent to it. Kubernetes does not restart the pod.
- **Liveness failure:** Kubernetes considers the container unhealthy and **kills and restarts** it.

**Which one should you use for checking database connectivity, and why?**

Use a **readiness** probe for database connectivity. If the database is temporarily unavailable, you want to stop routing traffic to the pod until the DB recovers — not restart the pod. Restarting the application container will not fix a down database and can make things worse (connection storms, partial state). Readiness lets the pod wait and rejoin the load balancer automatically once the dependency is healthy again.

---

## Bonus Task — Helm Chart

Manifests converted to a Helm chart at `k8s/chart/` with templates for all five components.

### Chart.yaml

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
```

### values.yaml

```yaml
postgres:
  replicas: 1
  image: postgres:17-alpine
  db: quickticket
  user: quickticket
  password: quickticket

redis:
  replicas: 1
  image: redis:7-alpine

gateway:
  replicas: 1
  image: quickticket-gateway:v1
  timeoutMs: "5000"

events:
  replicas: 1
  image: quickticket-events:v1
  db:
    host: postgres
    port: 5432
    name: quickticket
    user: quickticket
    password: quickticket
  redis:
    host: redis
    port: 6379
    timeoutMs: "1000"
  reservationTtl: "300"
  maxConns: "10"

payments:
  replicas: 1
  image: quickticket-payments:v1
  failureRate: "0.0"
  latencyMs: "0"

resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi
```

### Install and verify

Raw manifests were removed, then the chart was installed:

```bash
kubectl delete -f k8s/postgres.yaml -f k8s/redis.yaml -f k8s/events.yaml -f k8s/payments.yaml -f k8s/gateway.yaml
helm install quickticket k8s/chart/
```

```
$ helm list
NAME       	NAMESPACE	REVISION	UPDATED                                	STATUS  	CHART                       	APP VERSION
monitoring 	default  	1       	2026-06-26 18:52:27 +0300 MSK          	deployed	kube-prometheus-stack-45.0.0	v0.63.0
quickticket	default  	1       	2026-06-26 18:49:31 +0300 MSK          	deployed	quickticket-0.1.0
```

```
$ kubectl get pods
NAME                                                     READY   STATUS    RESTARTS   AGE
redis-dd88c599-gghmc                                     1/1     Running   0          4m
postgres-766c6d49cd-nm825                                1/1     Running   0          4m
payments-7ff7469446-z68xz                                1/1     Running   0          4m
gateway-656d76fd65-xk6sj                                 1/1     Running   1          4m
events-859c84455c-dbhb8                                  1/1     Running   0          114s
monitoring-kube-prometheus-operator-6488866fbb-45sc4     1/1     Running   0          53s
monitoring-prometheus-node-exporter-cqb7h                1/1     Running   0          53s
monitoring-kube-state-metrics-5f4b58495-62cbt            1/1     Running   0          53s
alertmanager-monitoring-kube-prometheus-alertmanager-0   2/2     Running   1          47s
monitoring-grafana-7897f888cf-r6rmc                      3/3     Running   0          53s
prometheus-monitoring-kube-prometheus-prometheus-0       2/2     Running   0          47s
```

### Monitoring via Helm (B.4)

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install monitoring prometheus-community/kube-prometheus-stack \
  --version 45.0.0 \
  --set grafana.adminPassword=admin \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false
```

> Note: the latest chart requires Kubernetes ≥1.25; this cluster runs v1.21.7, so chart version **45.0.0** was used instead.

**How many pods did kube-prometheus-stack create?** **6 pods:**

1. `monitoring-kube-prometheus-operator`
2. `monitoring-prometheus-node-exporter`
3. `monitoring-kube-state-metrics`
4. `monitoring-grafana`
5. `prometheus-monitoring-kube-prometheus-prometheus-0`
6. `alertmanager-monitoring-kube-prometheus-alertmanager-0`
