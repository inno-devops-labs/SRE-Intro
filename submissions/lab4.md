# Lab 4 — Kubernetes, Probes, Resource Limits and Helm

## Task 1 — Deploy QuickTicket to Kubernetes

### Kubernetes cluster

I used a local Kubernetes cluster created with `k3d`.

Command:

```bash
kubectl get nodes
```

Output:

```text
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   16h   v1.35.5+k3s1
```

The node is in the `Ready` state, which means the Kubernetes cluster is running and can schedule workloads.

---

### Application pods

After applying the Kubernetes manifests, I checked the pod status:

```bash
kubectl get pods
```

Output:

```text
NAME                       READY   STATUS    RESTARTS   AGE
events-859d5c5c98-pnfl4    1/1     Running   0          2m26s
gateway-6fc44f68c5-gzrhz   1/1     Running   0          2m26s
payments-58fb468db-29r7g   1/1     Running   0          2m26s
postgres-7c7ffc4b-stsfd    1/1     Running   0          35m
redis-c46d5dffc-bmvtb      1/1     Running   0          35m
```

The QuickTicket stack was successfully deployed. The following components are running in Kubernetes:

```text
gateway
events
payments
postgres
redis
```

---

### Services

I checked the Kubernetes services after deploying the application and monitoring stack:

```bash
kubectl get svc
```

Output:

```text
NAME                                      TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)                      AGE
alertmanager-operated                     ClusterIP   None            <none>        9093/TCP,9094/TCP,9094/UDP   10m
events                                    ClusterIP   10.43.81.59     <none>        8081/TCP                     14m
gateway                                   ClusterIP   10.43.125.143   <none>        8080/TCP                     14m
kubernetes                                ClusterIP   10.43.0.1       <none>        443/TCP                      18h
monitoring-grafana                        ClusterIP   10.43.237.59    <none>        80/TCP                       11m
monitoring-kube-prometheus-alertmanager   ClusterIP   10.43.95.180    <none>        9093/TCP,8080/TCP            11m
monitoring-kube-prometheus-operator       ClusterIP   10.43.125.164   <none>        443/TCP                      11m
monitoring-kube-prometheus-prometheus     ClusterIP   10.43.124.10    <none>        9090/TCP,8080/TCP            11m
monitoring-kube-state-metrics             ClusterIP   10.43.186.230   <none>        8080/TCP                     11m
monitoring-prometheus-node-exporter       ClusterIP   10.43.250.59    <none>        9100/TCP                     11m
payments                                  ClusterIP   10.43.162.254   <none>        8082/TCP                     14m
postgres                                  ClusterIP   10.43.116.7     <none>        5432/TCP                     14m
prometheus-operated                       ClusterIP   None            <none>        9090/TCP                     10m
redis                                     ClusterIP   10.43.181.146   <none>        6379/TCP                     14m
```

The main QuickTicket services expose the application components inside the cluster:

```text
gateway   -> 8080
events    -> 8081
payments  -> 8082
postgres  -> 5432
redis     -> 6379
```

The monitoring stack also created services for Grafana, Prometheus, Alertmanager, kube-state-metrics, and node-exporter.

---

### Database initialization

I initialized PostgreSQL using the provided seed file:

```bash
kubectl exec -it $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql
```

Output:

```text
Unable to use a TTY - input is not a terminal or the right kind of file
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

The TTY warning appeared because the command used input redirection together with `-it`. It did not affect the execution. The important part is that the tables were created and 5 records were inserted.

---

### Gateway health check

I used port-forwarding to access the gateway service from the local machine:

```bash
kubectl port-forward svc/gateway 3080:8080
```

Then I checked the health endpoint:

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
```

Output:

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

This confirms that the gateway service is healthy and can communicate with the downstream `events` and `payments` services.

---

### Events endpoint

I also tested the main API flow through the gateway:

```bash
curl -s http://localhost:3080/events | python3 -m json.tool
```

Output:

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

This confirms that the request flow works end-to-end:

```text
client -> gateway -> events -> PostgreSQL
```

The `events` service successfully returned data from the PostgreSQL database.

---

### Self-healing test

To test Kubernetes self-healing, I deleted the gateway pod:

```bash
kubectl delete pod -l app=gateway
```

Output:

```text
pod "gateway-6fc44f68c5-gzrhz" deleted from default namespace
```

Then I watched the pod status:

```bash
kubectl get pods -w
```

Output:

```text
NAME                       READY   STATUS              RESTARTS   AGE
events-859d5c5c98-pnfl4    1/1     Running             0          9m43s
gateway-6fc44f68c5-7jrgw   0/1     ContainerCreating   0          6s
payments-58fb468db-29r7g   1/1     Running             0          9m43s
postgres-7c7ffc4b-stsfd    1/1     Running             0          43m
redis-c46d5dffc-bmvtb      1/1     Running             0          42m
gateway-6fc44f68c5-7jrgw   1/1     Running             0          21s
```

Kubernetes automatically created a replacement gateway pod. The new pod became ready in about 15–21 seconds.

After restarting port-forwarding, I checked the health endpoint again:

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
```

Output:

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

The application recovered successfully after the pod deletion.

---

### Kubernetes self-healing compared to Docker Compose

Kubernetes recreated the deleted pod automatically because the Deployment controller continuously compares the desired state with the actual state. The Deployment requires one gateway replica, so when the pod was deleted, Kubernetes created a replacement.

This is different from a typical `docker-compose restart` workflow. With Docker Compose, restarts are usually triggered manually by the user. Kubernetes provides a stronger reconciliation model: it keeps the requested number of replicas running without manual intervention.

---

## Task 2 — Probes and Resource Limits

### Liveness and readiness probes

I added `livenessProbe` and `readinessProbe` to the application Deployments.

The probes were configured for the following health endpoints:

```text
gateway  -> /health on port 8080
events   -> /health on port 8081
payments -> /health on port 8082
```

I verified the probes with:

```bash
kubectl describe pod -l app=gateway | grep -A 5 "Liveness\|Readiness"
kubectl describe pod -l app=events | grep -A 5 "Liveness\|Readiness"
kubectl describe pod -l app=payments | grep -A 5 "Liveness\|Readiness"
```

Output for `gateway`:

```text
Liveness:       http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:      http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
Environment:
  EVENTS_URL:          http://events:8081
  PAYMENTS_URL:        http://payments:8082
  GATEWAY_TIMEOUT_MS:  5000
Mounts:
```

Output for `events`:

```text
Liveness:       http-get http://:8081/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:      http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
Environment:
  DB_HOST:     postgres
  DB_PORT:     5432
  DB_NAME:     quickticket
  DB_USER:     quickticket
```

Output for `payments`:

```text
Liveness:       http-get http://:8082/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:      http-get http://:8082/health delay=0s timeout=1s period=5s #success=1 #failure=2
Environment:
  PAYMENT_FAILURE_RATE:  0.0
  PAYMENT_LATENCY_MS:    0
Mounts:
```

This confirms that both liveness and readiness probes were configured for the application services.

---

### Readiness probe failure during Redis outage

To test readiness behavior, I simulated a Redis dependency failure:

```bash
kubectl delete pod -l app=redis
```

Then I watched the pod states:

```bash
kubectl get pods -w
```

Output:

```text
NAME                        READY   STATUS              RESTARTS   AGE
events-7c68cd54d8-kzkdj     1/1     Running             0          4m11s
gateway-854488bf7c-brshs    1/1     Running             0          114s
payments-68dcdf7696-cp88k   1/1     Running             0          4m11s
postgres-7c7ffc4b-jmkqk     1/1     Running             0          13h
redis-c46d5dffc-zt7rs       1/1     Running             0          94s
redis-c46d5dffc-zt7rs       1/1     Terminating         0          101s
redis-c46d5dffc-zt7rs       0/1     Completed           0          101s
redis-c46d5dffc-g9fjj       0/1     Pending             0          0s
redis-c46d5dffc-g9fjj       0/1     ContainerCreating   0          0s
redis-c46d5dffc-g9fjj       1/1     Running             0          6s
redis-c46d5dffc-g9fjj       1/1     Terminating         0          58s
redis-c46d5dffc-g9fjj       0/1     Completed           0          59s
events-7c68cd54d8-kzkdj     0/1     Running             0          5m22s
gateway-854488bf7c-brshs    0/1     Running             0          3m11s
redis-c46d5dffc-d9m7g       0/1     Pending             0          0s
redis-c46d5dffc-d9m7g       0/1     ContainerCreating   0          0s
redis-c46d5dffc-d9m7g       1/1     Running             0          1s
gateway-854488bf7c-brshs    0/1     Running             1 (1s ago)   3m32s
events-7c68cd54d8-kzkdj     1/1     Running             0            5m52s
gateway-854488bf7c-brshs    1/1     Running             1 (7s ago)   3m38s
```

During the Redis outage, the `events` pod changed to:

```text
events-7c68cd54d8-kzkdj     0/1     Running
```

The container was still running, but the pod was no longer considered ready. Kubernetes therefore removed it from the Service endpoints until the readiness probe started passing again.

The `gateway` pod also became `0/1 Running`, because its health endpoint depends on downstream services. Once Redis recovered and the `events` service became ready again, the gateway returned to `1/1 Running`.

---

### Resource requests and limits

I added CPU and memory requests and limits to all Deployments:

```yaml
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi
```

The resources were added to all main components:

```text
gateway
events
payments
postgres
redis
```

After applying the manifests:

```bash
kubectl apply -f k8s/
```

Output:

```text
deployment.apps/events configured
service/events unchanged
deployment.apps/gateway configured
service/gateway unchanged
deployment.apps/payments configured
service/payments unchanged
deployment.apps/postgres configured
service/postgres unchanged
deployment.apps/redis configured
service/redis unchanged
```

I checked that all pods were running:

```bash
kubectl get pods
```

Output:

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-78696fcf65-j6qrp     1/1     Running   0          8s
gateway-7cd55d8774-4qkq9    1/1     Running   0          8s
payments-d7dc94485-dm7nt    1/1     Running   0          8s
postgres-78489d7f5f-2k6cz   1/1     Running   0          8s
redis-6fcfb5475d-gfgld      1/1     Running   0          8s
```

Then I checked allocated resources on the node:

```bash
kubectl describe $(kubectl get nodes -o name | head -1) | grep -A 10 "Allocated resources"
```

Output:

```text
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (3%)   1 (8%)
  memory             460Mi (1%)  1450Mi (4%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
```

This output shows that Kubernetes registered CPU and memory requests and limits for the workloads running on the node.

---

### Difference between liveness and readiness probe failure

A liveness probe checks whether a container is still alive. If the liveness probe fails repeatedly, Kubernetes treats the container as unhealthy and restarts it.

A readiness probe checks whether a pod is ready to receive traffic. If the readiness probe fails, Kubernetes does not immediately restart the container. Instead, it marks the pod as `NotReady` and removes it from the Service endpoints.

For database or Redis connectivity, readiness is usually the better probe. If PostgreSQL or Redis is temporarily unavailable, restarting the application will not fix the external dependency. A better behavior is to keep the container running but temporarily stop routing traffic to it until the dependency becomes available again.

In this lab, the Redis failure caused the `events` pod to become `0/1 Running`. This demonstrated a readiness failure: the application process was still alive, but it was not ready to serve requests.

---

## Bonus Task — Helm Chart

### Chart scaffold

I converted the raw Kubernetes manifests into a Helm chart located in:

```text
k8s/chart
```

The chart contains metadata in `Chart.yaml`, configurable values in `values.yaml`, and Kubernetes templates in the `templates` directory.

`k8s/chart/Chart.yaml`:

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
```

`k8s/chart/values.yaml`:

```yaml
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

postgres:
  replicas: 1
  image: postgres:17-alpine
  db:
    name: quickticket
    user: quickticket
    password: quickticket

redis:
  replicas: 1
  image: redis:7-alpine

resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi
```

The original Kubernetes manifests were moved into `k8s/chart/templates/`, and hardcoded values were replaced with Helm values.

For example:

```yaml
replicas: {{ .Values.gateway.replicas }}
image: {{ .Values.gateway.image }}
```

I validated the chart:

```bash
helm lint k8s/chart/
```

Output:

```text
==> Linting k8s/chart/
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
```

I also rendered the chart locally:

```bash
helm template quickticket k8s/chart/
```

The rendered output included Deployments and Services for `gateway`, `events`, `payments`, `postgres`, and `redis`.

---

### Installing QuickTicket using Helm

After removing the raw Kubernetes manifests from the cluster, I installed the application with Helm:

```bash
helm install quickticket k8s/chart/
```

Then I verified that all application pods were running:

```bash
kubectl get pods
```

Output:

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-78696fcf65-sdjn8     1/1     Running   0          29s
gateway-7cd55d8774-ptxvr    1/1     Running   0          29s
payments-d7dc94485-x9bq6    1/1     Running   0          29s
postgres-78489d7f5f-7tvw4   1/1     Running   0          29s
redis-6fcfb5475d-5hl8d      1/1     Running   0          29s
```

I checked the installed Helm releases:

```bash
helm list
```

Output:

```text
NAME          NAMESPACE   REVISION   UPDATED                                  STATUS     CHART                         APP VERSION
monitoring    default     1          2026-06-16 11:50:55.436858415 +0300 MSK   deployed   kube-prometheus-stack-86.2.3  v0.91.0
quickticket   default     1          2026-06-16 11:48:14.357929996 +0300 MSK   deployed   quickticket-0.1.0
```

This confirms that QuickTicket was installed as a Helm release named `quickticket`.

---

### Monitoring deployment with Helm

I installed the `kube-prometheus-stack` chart from the `prometheus-community` Helm repository:

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install monitoring prometheus-community/kube-prometheus-stack \
  --set grafana.adminPassword=admin \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false
```

The `helm list` output shows that the monitoring release was installed successfully:

```text
NAME          NAMESPACE   REVISION   UPDATED                                  STATUS     CHART                         APP VERSION
monitoring    default     1          2026-06-16 11:50:55.436858415 +0300 MSK   deployed   kube-prometheus-stack-86.2.3  v0.91.0
quickticket   default     1          2026-06-16 11:48:14.357929996 +0300 MSK   deployed   quickticket-0.1.0
```

I checked the pods created by the `monitoring` Helm release:

```bash
kubectl get pods -l app.kubernetes.io/instance=monitoring
```

Output:

```text
NAME                                                  READY   STATUS      RESTARTS   AGE
monitoring-grafana-65749cfb9-b4znx                    0/3     Evicted     0          8m50s
monitoring-grafana-65749cfb9-fpzpl                    0/3     Pending     0          8m49s
monitoring-grafana-65749cfb9-sbbm9                    0/3     Error       2          11m
monitoring-kube-prometheus-operator-7f87f6796-kt969   0/1     Completed   0          11m
monitoring-kube-prometheus-operator-7f87f6796-nk7jm   0/1     Pending     0          8m47s
monitoring-kube-state-metrics-b55bfdbfc-64mss         0/1     Error       0          11m
monitoring-kube-state-metrics-b55bfdbfc-88wpw         0/1     Pending     0          8m44s
monitoring-prometheus-node-exporter-djcxc             0/1     Evicted     0          9s
```

The command returned 8 pod objects for the `monitoring` Helm release.

Some monitoring pods were shown as `Evicted`, `Pending`, or `Error`. This happened after the chart had been installed and is likely caused by the fact that `kube-prometheus-stack` is relatively heavy for a small local `k3d` cluster.

However, the Helm release itself was installed successfully:

```text
monitoring   default   1   deployed   kube-prometheus-stack-86.2.3
```

The chart created Kubernetes resources for the monitoring stack, including components such as Grafana, Prometheus Operator, kube-state-metrics, and node-exporter. Earlier pod output also showed Prometheus and Alertmanager pods.

The `READY` column values such as `0/3`, `0/1`, or `2/2` show how many containers inside a pod are ready. They do not represent the number of pod replicas.

For example:

```text
prometheus-monitoring-kube-prometheus-prometheus-0   1/2   Running
```

means that one out of two containers inside the Prometheus pod was ready at that moment.
