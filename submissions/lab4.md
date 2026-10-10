# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

## Task 1 — Write Manifests & Deploy to k3d

### 4.1 Repository Baseline and Cluster

I fetched the current course repository and created `feature/lab4` directly from `upstream/main` at `cf2ad63`. The previous labs remain on separate branches; Lab 4 did not require copying the Lab 3 monitoring files.

```bash
git switch --no-track -c feature/lab4 upstream/main
git status
git log -1 --oneline
git rev-list --left-right --count upstream/main...HEAD
```

```text
Switched to a new branch 'feature/lab4'
On branch feature/lab4
nothing to commit, working tree clean
cf2ad63 (HEAD -> feature/lab4, upstream/main, upstream/HEAD, origin/main, origin/HEAD, main) fix(gitignore): unignore student deliverable paths
0       0
```

I used an ARM64 Kubernetes 1.33 client and created a one-node k3d cluster with k3s 1.33.13:

```bash
k3d cluster create quickticket \
  --image rancher/k3s:v1.33.13-k3s2 \
  --servers 1 \
  --agents 0 \
  --wait \
  --timeout 300s

kubectl --context k3d-quickticket wait \
  --for=condition=Ready nodes --all --timeout=120s

kubectl --context k3d-quickticket get nodes
```

```text
node/k3d-quickticket-server-0 condition met
NAME                       STATUS   ROLES                  AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane,master   8s    v1.33.13+k3s2
```

### 4.2 Build and Import Images

I built the three application images for `linux/arm64`:

```bash
docker build --platform linux/arm64 --progress=plain \
  -t quickticket-gateway:v1 ./app/gateway
docker build --platform linux/arm64 --progress=plain \
  -t quickticket-events:v1 ./app/events
docker build --platform linux/arm64 --progress=plain \
  -t quickticket-payments:v1 ./app/payments

docker image inspect \
  --format '{{.RepoTags}} {{.Os}}/{{.Architecture}} {{.Id}}' \
  quickticket-gateway:v1 \
  quickticket-events:v1 \
  quickticket-payments:v1
```

```text
[quickticket-gateway:v1] linux/arm64 sha256:10d8e7e72602d957f291fed95ade265971e57c8b4612801bc55c3acaa35d2c52
[quickticket-events:v1] linux/arm64 sha256:f522e78c90e36d6a92da7040d38d3cf95fb528173392444d38471a6640b5fee0
[quickticket-payments:v1] linux/arm64 sha256:d36c45d50f6f4fd828d7922bb75b626c90287e7a2043928ea25afd977d6fc1c6
```

I imported the images into the k3d container runtime:

```bash
k3d image import \
  quickticket-gateway:v1 \
  quickticket-events:v1 \
  quickticket-payments:v1 \
  -c quickticket
```

```text
Successfully imported 3 image(s) into 1 cluster(s)
```

### 4.3 Deploy PostgreSQL and Redis

I created raw Deployment and ClusterIP Service manifests for PostgreSQL and Redis. PostgreSQL also has a 1 GiB `local-path` PersistentVolumeClaim.

### 4.4 Deploy QuickTicket Services

I created the remaining application manifests, resulting in these five raw files:

```text
k8s/postgres.yaml
k8s/redis.yaml
k8s/events.yaml
k8s/payments.yaml
k8s/gateway.yaml
```

Every component has a Deployment and ClusterIP Service. The application Deployments use the imported images with `imagePullPolicy: Never`, and their environment variables use the `postgres`, `redis`, `events`, and `payments` Service DNS names.

After applying the manifests and waiting for all rollouts, the clean raw-manifest state was:

```bash
kubectl --context k3d-quickticket --namespace default get pods,svc
```

```text
NAME                            READY   STATUS    RESTARTS   AGE
pod/events-777dddf5f-d7lhc      1/1     Running   0          78s
pod/gateway-7489f5f778-7qmhf    1/1     Running   0          78s
pod/payments-6c68fcd57f-t9mrw   1/1     Running   0          78s
pod/postgres-7855d4cd7f-gz65x   1/1     Running   0          86s
pod/redis-5fb75b6499-6jl7b      1/1     Running   0          87s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.68.103    <none>        8081/TCP   51m
service/gateway      ClusterIP   10.43.212.31    <none>        8080/TCP   51m
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    67m
service/payments     ClusterIP   10.43.246.240   <none>        8082/TCP   51m
service/postgres     ClusterIP   10.43.110.59    <none>        5432/TCP   55m
service/redis        ClusterIP   10.43.49.28     <none>        6379/TCP   55m
```

### 4.5 Initialize the Database

Because `seed.sql` is not fully idempotent, I first guarded against loading it into an initialized database:

```bash
LAB4_TABLES="$(kubectl --context k3d-quickticket --namespace default \
  exec deployment/postgres -- \
  psql -U quickticket -d quickticket -v ON_ERROR_STOP=1 -tAc \
  "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename IN ('events', 'orders');")"

printf 'Existing application tables: %s\n' "$LAB4_TABLES"
```

```text
Existing application tables: 0
```

I then loaded the seed through standard input in one transaction:

```bash
kubectl --context k3d-quickticket --namespace default \
  exec -i deployment/postgres -- \
  psql -U quickticket -d quickticket \
  -v ON_ERROR_STOP=1 --single-transaction -f /dev/stdin \
  < app/seed.sql
```

```text
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

I verified the resulting rows:

```bash
kubectl --context k3d-quickticket --namespace default \
  exec deployment/postgres -- \
  psql -U quickticket -d quickticket -v ON_ERROR_STOP=1 \
  -c "SELECT id, name, total_tickets, price_cents FROM events ORDER BY id;" \
  -c "SELECT count(*) AS orders_count FROM orders;"
```

```text
 id |         name         | total_tickets | price_cents
----+----------------------+---------------+-------------
  1 | Go Conference 2026   |           100 |        5000
  2 | SRE Meetup           |            30 |           0
  3 | Cloud Native Summit  |           500 |       15000
  4 | Python Workshop      |            25 |        2000
  5 | Kubernetes Deep Dive |            80 |        8000
(5 rows)

 orders_count
--------------
            0
(1 row)
```

### 4.6 Verify the Full Stack Through Port-Forward

I forwarded the gateway Service to localhost:

```bash
kubectl --context k3d-quickticket --namespace default \
  port-forward --address 127.0.0.1 svc/gateway 3080:8080
```

From another terminal, I requested the events endpoint at `2026-09-14T10:32:14Z`:

```bash
curl -fsS --max-time 10 http://localhost:3080/events \
  | python3 -m json.tool
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

The response proves that gateway reached events and that events queried the seeded PostgreSQL database. Gateway health also returned HTTP 200 with both dependencies reported as `ok`.

### 4.7 Test Kubernetes Self-Healing

I watched gateway pods while a Python measurement script invoked the displayed delete command:

```text
Command: /Users/glebshvetsov/.local/share/sre-lab4/bin/kubectl --context k3d-quickticket --namespace default --request-timeout=10s delete pod gateway-5dffbbbb6f-r9ssj --wait=false
pod "gateway-5dffbbbb6f-r9ssj" deleted
```

```bash
kubectl --context k3d-quickticket --namespace default \
  get pods -l app=gateway -w
```

```text
NAME                       READY   STATUS              RESTARTS   AGE
gateway-5dffbbbb6f-r9ssj   1/1     Running             0          9m9s
gateway-5dffbbbb6f-r9ssj   1/1     Terminating         0          12m
gateway-5dffbbbb6f-4wdhp   0/1     Pending             0          0s
gateway-5dffbbbb6f-4wdhp   0/1     Pending             0          1s
gateway-5dffbbbb6f-4wdhp   0/1     ContainerCreating   0          1s
gateway-5dffbbbb6f-r9ssj   0/1     Completed           0          12m
gateway-5dffbbbb6f-4wdhp   1/1     Running             0          2s
gateway-5dffbbbb6f-r9ssj   0/1     Completed           0          12m
gateway-5dffbbbb6f-r9ssj   0/1     Completed           0          12m
```

Relevant fields from the measurement result:

```json
{
  "old_pod": "gateway-5dffbbbb6f-r9ssj",
  "old_uid": "a8125a4d-4750-42b9-873a-24157d7d80b9",
  "delete_requested_utc": "2026-09-14T10:39:50.677870+00:00",
  "new_pod": "gateway-5dffbbbb6f-4wdhp",
  "new_uid": "1c76f942-a0f6-4e42-80f4-4f6295bddbc1",
  "new_pod_first_observed_seconds": 0.602,
  "delete_to_ready_observed_seconds": 2.283,
  "restart_count": 0
}
```

### 4.8 Proof of Work and Recovery Comparison

The replacement pod first appeared after 0.602 seconds and was observed Ready after 2.283 seconds. These client-side measurements include Kubernetes API and polling delay and are not an exact measurement of HTTP downtime.

Kubernetes recreated the deleted gateway pod automatically through its Deployment controller. In Lab 1, a stopped Compose service required an explicit `docker compose start`; Compose did not continuously reconcile the desired replica count.

## Task 2 — Probes & Resource Limits

### 4.9 Configure Liveness and Readiness Probes

I added the required HTTP probes to gateway, events, and payments and applied the updated manifests. I inspected the resulting pod definitions with the exact filtering command used during the lab:

```bash
kubectl --context k3d-quickticket --namespace default \
  describe pod -l 'app in (gateway,events,payments)' \
  | tee "$LAB4_EVIDENCE/probes-describe.txt" \
  | python3 -c 'import sys; sys.stdout.writelines(line for line in sys.stdin if line.startswith("Name:") or line.lstrip().startswith(("Liveness:", "Readiness:")))'
```

```text
Name:             events-9d5d75c6d-td49q
    Liveness:       http-get http://:8081/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:      http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
Name:             gateway-84749f5fdc-d2bqb
    Liveness:       http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:      http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
Name:             payments-78ccf78f4d-wlpgx
    Liveness:       http-get http://:8082/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:      http-get http://:8082/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

### 4.10 Observe Readiness Failure

A normal Redis replacement finished in approximately three seconds, before the events service's five-second Redis health cache and readiness threshold could expose `0/1`. For a controlled observation on the single-node test cluster, an executed Python script cordoned the node, deleted Redis, polled the unchanged events pod, and guaranteed `uncordon` in a `finally` block.

This is an excerpt from that executed script, not a standalone script:

```python
# Excerpt only
run(["cordon", node_name])
try:
    run(["delete", "pod", redis_name, "--wait=false"])
    # Poll the existing events pod until its Ready condition is False.
finally:
    run(["uncordon", node_name], check=False)
```

Relevant output from `kubectl get pods -l 'app in (gateway,events,redis)' -w`:

```text
events-9d5d75c6d-td49q     1/1   Running             0   22m
gateway-84749f5fdc-d2bqb   1/1   Running             0   22m
redis-55c88ffcb8-2s28r     1/1   Terminating         0   15m
redis-55c88ffcb8-ljsr8     0/1   Pending             0   0s
events-9d5d75c6d-td49q     0/1   Running             0   24m
gateway-84749f5fdc-d2bqb   0/1   Running             0   24m
redis-55c88ffcb8-ljsr8     0/1   ContainerCreating   0   21s
redis-55c88ffcb8-ljsr8     1/1   Running             0   22s
events-9d5d75c6d-td49q     1/1   Running             0   24m
gateway-84749f5fdc-d2bqb   1/1   Running             0   24m
```

At the failure point, Kubernetes reported:

```text
NAME                       READY   STATUS    RESTARTS   AGE
events-9d5d75c6d-td49q     0/1     Running   0          24m
gateway-84749f5fdc-d2bqb   0/1     Running   0          24m
redis-55c88ffcb8-ljsr8     0/1     Pending   0          21s

Events Service endpoints:
addresses= ['10.42.0.16'] conditions= {'ready': False, 'serving': False, 'terminating': False}
```

Relevant fields from the recorded timing JSON:

```json
{
  "delete_requested_utc": "2026-09-14T11:14:07.250374+00:00",
  "events_not_ready_observed_utc": "2026-09-14T11:14:28.021435+00:00",
  "delete_to_events_not_ready_seconds": 20.771,
  "uncordon_requested_utc": "2026-09-14T11:14:28.201703+00:00",
  "redis_ready_utc": "2026-09-14T11:14:29.574053+00:00",
  "events_ready_again_utc": "2026-09-14T11:14:32.787664+00:00",
  "gateway_ready_again_utc": "2026-09-14T11:14:35.854099+00:00"
}
```

The 20.771-second measurement includes application caching, probe intervals, the failure threshold, API calls, and polling delay. The events pod remained Running with zero restarts, while its EndpointSlice became `ready: false`. After Redis returned, Redis, events, and gateway recovered to `1/1`, and gateway health returned HTTP 200.

A readiness failure removes a pod from Service traffic without restarting it. A persistent liveness failure causes kubelet to restart the container. Database connectivity should use readiness because restarting a healthy application cannot repair an unavailable external database. The lab-required liveness probe also calls dependency-aware `/health`, so a longer dependency outage could cause unnecessary restarts in a production design.

### 4.11 Configure Resource Requests and Limits

I added the following resources to every container in all five Deployments:

```yaml
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi
```

I applied the manifests, waited for all five rollouts, and inspected node allocation using the actual commands:

```bash
LAB4_NODE="$(
  kubectl --context k3d-quickticket get nodes -o name \
    | head -1
)"

kubectl --context k3d-quickticket describe "$LAB4_NODE" \
  | tee "$LAB4_EVIDENCE/node-describe-after-resources.txt" \
  | grep -A 10 "Allocated resources"
```

```text
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (5%)   1 (12%)
  memory             460Mi (7%)  1450Mi (24%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
  hugepages-32Mi     0 (0%)      0 (0%)
  hugepages-64Ki     0 (0%)      0 (0%)
```

The node totals include k3s system workloads. The five QuickTicket containers account for a calculated `250m` CPU and `320Mi` memory in requests, plus `1` CPU and `1280Mi` memory in limits.

## Bonus Task — Helm Chart

### B.1 Chart Scaffold and Values

I installed Helm `v3.22.0` for ARM64 and preserved the raw Task 1 manifests while creating templated copies under `k8s/chart/templates/`.

`k8s/chart/Chart.yaml`:

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
```

`k8s/chart/values.yaml`:

```yaml
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi

postgres:
  replicas: 1
  image: postgres:17-alpine
  imagePullPolicy: IfNotPresent
  port: 5432
  storageClass: local-path
  storageSize: 1Gi

redis:
  replicas: 1
  image: redis:7-alpine
  imagePullPolicy: IfNotPresent
  port: 6379

gateway:
  replicas: 1
  image: quickticket-gateway:v1
  imagePullPolicy: Never
  port: 8080
  eventsUrl: http://events:8081
  paymentsUrl: http://payments:8082
  timeoutMs: 5000

events:
  replicas: 1
  image: quickticket-events:v1
  imagePullPolicy: Never
  port: 8081
  db:
    host: postgres
    port: 5432
    name: quickticket
    user: quickticket
    password: quickticket
    maxConnections: 10
  redis:
    host: redis
    port: 6379
    timeoutMs: 1000
  reservationTtl: 300

payments:
  replicas: 1
  image: quickticket-payments:v1
  imagePullPolicy: Never
  port: 8082
  failureRate: "0.0"
  latencyMs: "0"
```

### B.2 Create and Validate Templates

The chart contains templates for PostgreSQL, Redis, events, payments, and gateway. I used an executed Python transformation script to copy the verified raw manifests and replace configurable fields with `.Values` references.

```bash
helm lint k8s/chart

helm template quickticket k8s/chart \
  > "$HOME/SRE-Intro-lab4-evidence/quickticket-helm-rendered.yaml"

kubectl --context k3d-quickticket --namespace default apply \
  --dry-run=server \
  -f "$HOME/SRE-Intro-lab4-evidence/quickticket-helm-rendered.yaml"
```

```text
==> Linting k8s/chart
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
persistentvolumeclaim/postgres-data configured (server dry run)
service/events unchanged (server dry run)
service/gateway unchanged (server dry run)
service/payments unchanged (server dry run)
service/postgres unchanged (server dry run)
service/redis unchanged (server dry run)
deployment.apps/events unchanged (server dry run)
deployment.apps/gateway unchanged (server dry run)
deployment.apps/payments unchanged (server dry run)
deployment.apps/postgres unchanged (server dry run)
deployment.apps/redis unchanged (server dry run)
```

### B.3 Install and Verify the QuickTicket Chart

The verified raw resources already existed, so I used `--take-ownership` instead of deleting them. The PostgreSQL PVC template also has `helm.sh/resource-policy: keep`.

```bash
helm install quickticket k8s/chart \
  --kube-context k3d-quickticket \
  --namespace default \
  --take-ownership \
  --wait \
  --timeout 5m

helm list --kube-context k3d-quickticket --namespace default
kubectl --context k3d-quickticket --namespace default get pods,pvc
```

```text
NAME          NAMESPACE  REVISION  UPDATED                                      STATUS    CHART              APP VERSION
quickticket   default    1         2026-09-14 14:32:01.452723 +0300 MSK         deployed  quickticket-0.1.0

NAME                            READY   STATUS    RESTARTS   AGE
pod/events-777dddf5f-d7lhc      1/1     Running   0          14m
pod/gateway-7489f5f778-7qmhf    1/1     Running   0          14m
pod/payments-6c68fcd57f-t9mrw   1/1     Running   0          14m
pod/postgres-7855d4cd7f-gz65x   1/1     Running   0          14m
pod/redis-5fb75b6499-6jl7b      1/1     Running   0          14m

NAME                                  STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS   VOLUMEATTRIBUTESCLASS   AGE
persistentvolumeclaim/postgres-data   Bound    pvc-067aef56-ca29-433c-8a2d-c171ac0b0c43   1Gi        RWO            local-path     <unset>                 68m
```

All five Deployments, five Services, and the PVC reported `release=quickticket`, `namespace=default`, and `managed-by=Helm`. PostgreSQL retained five events, and gateway health returned HTTP 200.

### B.4 Deploy Monitoring with Helm

I installed the chart version found during the lab into a separate namespace with the required values:

```bash
helm repo add prometheus-community \
  https://prometheus-community.github.io/helm-charts \
  --force-update
helm repo update prometheus-community

helm install monitoring \
  prometheus-community/kube-prometheus-stack \
  --version 91.2.3 \
  --kube-context k3d-quickticket \
  --namespace monitoring \
  --create-namespace \
  --set grafana.adminPassword=admin \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --wait \
  --timeout 10m
```

Relevant columns from `helm list --all-namespaces`:

```text
NAME          NAMESPACE    REVISION  STATUS    CHART                              APP VERSION
monitoring    monitoring   1         deployed  kube-prometheus-stack-91.2.3       v0.94.0
quickticket   default      1         deployed  quickticket-0.1.0
traefik       kube-system  1         deployed  traefik-40.1.4+up40.1.0            v3.7.1
traefik-crd   kube-system  1         deployed  traefik-crd-40.1.4+up40.1.0        v3.7.1
```

Relevant columns from the monitoring pod output:

```text
NAME                                                     READY   STATUS    RESTARTS   AGE
alertmanager-monitoring-kube-prometheus-alertmanager-0   2/2     Running   0          107s
monitoring-grafana-577fcc465f-8wb2j                      3/3     Running   0          118s
monitoring-kube-prometheus-operator-7446dc967c-56pjv     1/1     Running   0          118s
monitoring-kube-state-metrics-6d44444f57-8csfm           1/1     Running   0          118s
monitoring-prometheus-node-exporter-xmdlt                1/1     Running   0          118s
prometheus-monitoring-kube-prometheus-prometheus-0       2/2     Running   0          107s
```

The monitoring release created **6 pods**. All six were Running, and all containers were Ready. After the installation, all five QuickTicket pods remained `1/1 Running` with zero restarts, the PostgreSQL PVC remained Bound, and gateway health returned HTTP 200.

## Conclusion

QuickTicket was successfully moved from Docker Compose to a Kubernetes 1.33 cluster using five raw manifests written for the lab. Kubernetes Service discovery connected all components, the seeded PostgreSQL data survived pod replacement through a PVC, and the gateway served the complete events list through port-forwarding.

The experiments demonstrated two controller behaviors: a deleted gateway pod was automatically replaced and observed Ready in 2.283 seconds, while a Redis dependency outage made events unready and removed its endpoint without restarting the events container. Resource requests, limits, and probes now describe the expected operating boundaries of all five workloads.

Finally, the same deployment was converted into a configurable Helm chart and adopted without deleting the existing database volume. Both the QuickTicket chart and kube-prometheus-stack were installed as healthy Helm releases; the monitoring stack created six fully ready pods.
