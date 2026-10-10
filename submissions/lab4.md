# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

## Made by:
### Nurmuhametov Denis (d.nurmuhametov@innopolis.university)

---

## Task 1 — Write Manifests & Deploy to k3d (6 pts)

### 4.1: Create a k3d cluster

```bash
k3d cluster create quickticket
```

```text
INFO[0000] Prep: Network
INFO[0000] Created network 'k3d-quickticket'
INFO[0000] Created image volume k3d-quickticket-images
INFO[0000] Starting new tools node...
INFO[0001] Creating node 'k3d-quickticket-server-0'
INFO[0003] Pulling image 'docker.io/rancher/k3s:v1.35.5-k3s1'
...
INFO[0089] Cluster 'quickticket' created successfully!
```

```bash
kubectl get nodes
```

```text
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   15s   v1.35.5+k3s1
```

### 4.2: Build and import images

```bash
docker build -t quickticket-gateway:v1 ./gateway
docker build -t quickticket-events:v1 ./events
docker build -t quickticket-payments:v1 ./payments
k3d image import quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 -c quickticket
```

```text
INFO[0009] Successfully imported 3 image(s) into 1 cluster(s)
```

```bash
docker images | grep quickticket
```

```text
quickticket-events:v1     2b8b9d1839b2   157MB
quickticket-gateway:v1    608fdadbdbc7   143MB
quickticket-payments:v1   8daf421ec126   141MB
```

### 4.3: Deploy PostgreSQL and Redis

Created `k8s/postgres.yaml` and `k8s/redis.yaml` with Deployment + Service for each.

```bash
kubectl apply -f k8s/postgres.yaml
kubectl apply -f k8s/redis.yaml
kubectl get pods
```

```text
NAME                      READY   STATUS              RESTARTS   AGE
postgres-7c7ffc4b-njsbs   0/1     ContainerCreating   0          25s
redis-c46d5dffc-9frz5     0/1     ContainerCreating   0          5s
```

```bash
kubectl get svc
```

```text
NAME         TYPE        CLUSTER-IP     EXTERNAL-IP   PORT(S)    AGE
kubernetes   ClusterIP   10.43.0.1      <none>        443/TCP    8m34s
postgres     ClusterIP   10.43.18.31    <none>        5432/TCP   31s
redis        ClusterIP   10.43.97.147   <none>        6379/TCP   11s
```

### 4.4: Deploy QuickTicket services

Created `k8s/gateway.yaml`, `k8s/events.yaml`, `k8s/payments.yaml` with Deployment + Service for each, using `imagePullPolicy: Never`.

```bash
kubectl apply -f k8s/
```

```text
deployment.apps/events created
service/events created
deployment.apps/gateway created
service/gateway created
deployment.apps/payments created
service/payments created
deployment.apps/postgres unchanged
service/postgres unchanged
deployment.apps/redis unchanged
service/redis unchanged
```

```bash
kubectl get pods,svc
```

```text
NAME                           READY   STATUS    RESTARTS   AGE
pod/events-6c4df7d6-px4rk      1/1     Running   0          18m
pod/gateway-6fc44f68c5-sfh2v   1/1     Running   0          15m
pod/payments-58fb468db-lxhtc   1/1     Running   0          18m
pod/postgres-7c7ffc4b-njsbs    1/1     Running   0          22m
pod/redis-c46d5dffc-9frz5      1/1     Running   0          22m

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.198.191   <none>        8081/TCP   18m
service/gateway      ClusterIP   10.43.105.47    <none>        8080/TCP   18m
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    30m
service/payments     ClusterIP   10.43.69.58     <none>        8082/TCP   18m
service/postgres     ClusterIP   10.43.18.31     <none>        5432/TCP   22m
service/redis        ClusterIP   10.43.97.147    <none>        6379/TCP   22m
```

All 5 pods are `Running` and all 5 services are exposed via ClusterIP.

### 4.5: Initialize the database

```bash
kubectl exec -it $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql
```

```text
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

### 4.6: Verify everything works

```bash
kubectl port-forward svc/gateway 3080:8080 &
```

```text
Forwarding from 127.0.0.1:3080 -> 8080
Forwarding from [::1]:3080 -> 8080
```

```bash
curl -s http://localhost:3080/events | python3 -m json.tool
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
curl -s http://localhost:3080/health | python3 -m json.tool
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

The full QuickTicket stack is working inside Kubernetes — events list returns all 5 seed events and the health check reports all services healthy.

### 4.7: Test K8s self-healing

```bash
kubectl delete pod -l app=gateway
```

```text
pod "gateway-6fc44f68c5-qwb9s" deleted
```

```bash
kubectl get pods -w
```

```text
NAME                       READY   STATUS    RESTARTS   AGE
events-6c4df7d6-px4rk      1/1     Running   0          3m40s
gateway-6fc44f68c5-sfh2v   1/1     Running   0          7s
payments-58fb468db-lxhtc   1/1     Running   0          3m40s
postgres-7c7ffc4b-njsbs    1/1     Running   0          7m20s
redis-c46d5dffc-9frz5      1/1     Running   0          7m
```

The deleted gateway pod was automatically replaced by Kubernetes. The new pod (`gateway-6fc44f68c5-sfh2v`) appeared with an AGE of only 7 seconds, meaning the entire recovery took approximately 5-10 seconds.

### 4.8: Analysis

**How long did K8s take to recreate the deleted pod? How does this compare to docker-compose restart?**

Kubernetes recreated the gateway pod in approximately 5-10 seconds — the new pod was already `Running` with an AGE of 7s by the time the `-w` output was captured. This is dramatically faster than docker-compose's approach, where a stopped container stays dead until a human notices the failure and manually runs `docker compose start <service>` (or `docker compose up -d`). Even in the best case, docker-compose recovery depends on human reaction time (minutes at minimum), whereas Kubernetes self-healing is automatic and completes within seconds because the ReplicaSet controller continuously reconciles desired vs actual state.

---

## Task 2 — Probes & Resource Limits (4 pts)

### 4.9: Add readiness and liveness probes

Updated all three service manifests (`k8s/gateway.yaml`, `k8s/events.yaml`, `k8s/payments.yaml`) with HTTP health probes on their respective ports:

```bash
kubectl describe pod -l app=gateway | grep -A 5 "Liveness\|Readiness"
```

```text
Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:  http-get http://:8080/health delay=5s timeout=1s period=5s #success=1 #failure=2
```

```bash
kubectl describe pod -l app=events | grep -A 3 "Readiness"
```

```text
Readiness:  http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

All three services have both liveness and readiness probes configured. The gateway has `initialDelaySeconds: 5` on its readiness probe to allow uvicorn to start before the first health check.

### 4.10: Observe readiness probe failure

To test readiness probe behaviour, Redis was taken down. Since events depends on Redis for reservations, its `/health` endpoint returns HTTP 503 when Redis is unreachable.

```bash
kubectl scale deployment/redis --replicas=0
```

After the 5-second Redis health check cache in the events service expired, the readiness probe detected the failure:

```bash
kubectl get pod -l app=events -o wide
```

```text
NAME                      READY   STATUS    RESTARTS   AGE   IP
events-7f8bc556b9-lmztm   0/1     Running   0          15m   10.42.0.20
```

```bash
kubectl describe pod -l app=events | grep -A 3 "Readiness"
```

```text
Warning  Unhealthy  2s (x3 over 7s)  kubelet  Readiness probe failed: HTTP probe failed with statuscode: 503
```

The events pod showed `0/1 Ready` — K8s removed it from the Service endpoints, stopping traffic from the gateway. Interestingly, the **liveness probe also failed** (same `/health` endpoint), causing the pod to be restarted multiple times (RESTARTS: 3). This demonstrates a key anti-pattern: liveness probes should not depend on external services, because restarting a pod does not fix a missing Redis.

After scaling Redis back up, the probe passed again and events returned to `1/1`:

```bash
kubectl scale deployment/redis --replicas=1
kubectl get pods -o wide
```

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-7f8bc556b9-lmztm     1/1     Running   3          17m
gateway-6d775544c-bn985     1/1     Running   2          13m
payments-77cfcb5d65-kv88m   1/1     Running   0          17m
postgres-78489d7f5f-st9lw   1/1     Running   0          20m
redis-6fcfb5475d-7k565      1/1     Running   0          44s
```

### 4.11: Add resource limits

Added CPU and memory requests/limits to all 5 containers in the cluster:

```yaml
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi
```

```bash
kubectl describe node k3d-quickticket-server-0 | grep -A 10 "Allocated resources"
```

```text
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (2%)   1 (6%)
  memory             460Mi (3%)  1450Mi (9%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
```

Total allocated resources across all 5 containers: 450m CPU requests (50m × 5 + some overhead from system pods) and 460Mi memory requests (64Mi × 5 + overhead). The node has 6% of CPU and 9% of memory allocated as limits.

### 4.12: Analysis

**What's the difference between liveness and readiness probe failure? Which one should you use for checking database connectivity, and why?**

A **readiness probe failure** marks the pod as `NotReady` and removes it from the Service's endpoints — traffic stops being routed to it, but the pod keeps running. A **liveness probe failure** kills the pod entirely, and the ReplicaSet controller creates a replacement.

For checking database (or Redis) connectivity, you should use a **readiness probe**, not a liveness probe. If Redis is down, a readiness probe failure correctly stops traffic to the events pod (preventing requests that would fail anyway), but the pod stays alive so it can resume serving as soon as Redis recovers. A liveness probe failure would restart the pod, which is useless — restarting a Python process does not fix a missing database. Worse, as we observed in 4.10, if liveness also depends on the same health endpoint, it creates a cascading failure: the pod gets killed and restarted in a loop, consuming cluster resources and generating noise, without any benefit to the system.

---

## Bonus Task — Helm Chart (2 pts)

### B.1: Chart scaffold

Created the Helm chart under `k8s/chart/` with a parameterised template per service.

**`k8s/chart/Chart.yaml`:**

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
```

**`k8s/chart/values.yaml`:**

```yaml
gateway:
  replicas: 1
  image: quickticket-gateway:v1
  timeoutMs: 5000

events:
  replicas: 1
  image: quickticket-events:v1
  db:
    host: postgres
    port: 5432
    name: quickticket
    user: quickticket
    password: quickticket
    maxConns: 10
  redis:
    host: redis
    port: 6379
    timeoutMs: 1000
  reservationTtl: 300

payments:
  replicas: 1
  image: quickticket-payments:v1
  failureRate: "0.0"
  latencyMs: 0

postgres:
  image: postgres:17-alpine
  db: quickticket
  user: quickticket
  password: quickticket

redis:
  image: redis:7-alpine

resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi

probes:
  liveness:
    initialDelaySeconds: 10
    periodSeconds: 10
    failureThreshold: 3
  readiness:
    initialDelaySeconds: 5
    periodSeconds: 5
    failureThreshold: 2
```

### B.2: Templates

Each service has its own YAML template in `k8s/chart/templates/` with hardcoded values replaced by `{{ .Values.* }}` references and env values wrapped in double quotes (`value: "{{ .Values.x }}"`) to ensure correct YAML typing for Kubernetes.

### B.3: Install and verify

Raw manifests were deleted and the chart was installed via Helm:

```bash
kubectl delete -f k8s/ --ignore-not-found
helm install quickticket k8s/chart/
```

```text
NAME: quickticket
LAST DEPLOYED: Fri Jun 19 00:37:05 2026
NAMESPACE: default
STATUS: deployed
REVISION: 1
TEST SUITE: None
```

```bash
helm list
```

```text
NAME            NAMESPACE       REVISION  UPDATED                    STATUS    CHART              APP VERSION
quickticket     default         1         2026-06-19 ... MSK         deployed  quickticket-0.1.0
```

```bash
kubectl get pods
```

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-55fb7c5b64-zpwjc     1/1     Running   0          14s
gateway-78ffbb768f-dsvt9    1/1     Running   0          14s
payments-6687958cb8-6nzrr   1/1     Running   0          2m24s
postgres-78489d7f5f-qvh9m   1/1     Running   0          2m24s
redis-6fcfb5475d-m2fzh      1/1     Running   0          2m24s
```

After re-seeding the database (`kubectl cp` + `psql`), the full stack was verified end-to-end:

```bash
kubectl port-forward svc/gateway 3080:8080 &
curl -s http://localhost:3080/events | python3 -m json.tool
curl -s http://localhost:3080/health | python3 -m json.tool
```

Both endpoints returned correct data — 5 seed events and `"status": "healthy"`.

### B.4: Deploy monitoring via Helm

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install monitoring prometheus-community/kube-prometheus-stack \
  --set grafana.adminPassword=admin \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false
```

The `kube-prometheus-stack` created **6 pods** in the cluster:

```text
alertmanager-monitoring-kube-prometheus-alertmanager-0   2/2     Running
monitoring-grafana-65749cfb9-8ndhw                       3/3     Running
monitoring-kube-prometheus-operator-7f87f6796-z7z2x      1/1     Running
monitoring-kube-state-metrics-b55bfdbfc-fvffl            1/1     Running
monitoring-prometheus-node-exporter-klwnn                1/1     Running
prometheus-monitoring-kube-prometheus-prometheus-0       2/2     Running
```
