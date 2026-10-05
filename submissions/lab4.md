# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

Liubov Utenysheva, CBS-03

---

## Task 1 — Write Manifests & Deploy to k3d (6 pts)

### 4.1 — k3d cluster

```bash
$ k3d cluster create quickticket
...
INFO[0054] Cluster 'quickticket' created successfully!

$ kubectl get nodes
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   16s   v1.35.5+k3s1
```

One node, status `Ready`.

### 4.2 — Local images, imported into k3d

```bash
$ docker build -t quickticket-gateway:v1 ./gateway
$ docker build -t quickticket-events:v1 ./events
$ docker build -t quickticket-payments:v1 ./payments
$ k3d image import quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 -c quickticket
INFO[0014] Successfully imported 3 image(s) into 1 cluster(s)

$ docker images | grep quickticket
quickticket-events:v1      96e63f1f678d   234MB   57.3MB
quickticket-gateway:v1     ced48c6ec950   215MB   52.3MB
quickticket-payments:v1    d4f6b23b8194   212MB   51.7MB
```

### 4.3–4.4 — Manifests written from scratch and deployed

Manifests live in `k8s/` in this repo: `postgres.yaml`, `redis.yaml`, `gateway.yaml`, `events.yaml`, `payments.yaml` — each a Deployment + ClusterIP Service, with `selector.matchLabels` matching the pod template labels (`app: <name>`), env vars copied over from `docker-compose.yaml` (service names as hostnames, e.g. `DB_HOST=postgres`), and `imagePullPolicy: Never` on the three app containers so k3s uses the locally imported images.

Postgres and redis were applied first and waited for `Running` before the app services (no `depends_on` in K8s), then `kubectl apply -f k8s/`:

```console
$ kubectl apply -f k8s/
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

$ kubectl get pods,svc
NAME                        READY   STATUS    RESTARTS   AGE
pod/events-6c4df7d6-6lwj2   1/1     Running   0          6s
pod/gateway-6fc44f68c5-sqw7c 1/1    Running   0          6s
pod/payments-58fb468db-v4dkx 1/1    Running   0          6s
pod/postgres-69f4d4cc85-kl7xq 1/1   Running   0          30s
pod/redis-6658df796-lsm6t   1/1     Running   0          30s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.50.148    <none>        8081/TCP   6s
service/gateway      ClusterIP   10.43.12.8      <none>        8080/TCP   6s
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    106s
service/payments     ClusterIP   10.43.136.235   <none>        8082/TCP   6s
service/postgres     ClusterIP   10.43.171.239   <none>        5432/TCP   30s
service/redis        ClusterIP   10.43.34.244    <none>        6379/TCP   30s
```

All 5 pods `1/1 Running`, all 5 app Services created.

### 4.5 + 4.6 — Seed database, verify full stack via port-forward

```bash
$ kubectl exec -it $(kubectl get pod -l app=postgres -o name) -- \
    psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql
CREATE TABLE
CREATE TABLE
INSERT 0 5

$ kubectl port-forward svc/gateway 3080:8080 &
$ curl -s http://localhost:3080/events | python3 -m json.tool
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
        "date": "2026-11-10T10:00:00+00:00",
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

$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

Full critical path works end-to-end: gateway → events → postgres (seeded rows returned) + gateway → payments.

### 4.7 — Self-healing

```bash
$ kubectl delete pod -l app=gateway
pod "gateway-6fc44f68c5-cvzzs" deleted

$ kubectl get pods -w        # (watch output, trimmed to the gateway pod)
gateway-6fc44f68c5-cvzzs    1/1   Terminating       0    80s    10.42.0.14   k3d-quickticket-server-0  <none>
gateway-6fc44f68c5-sn7fr    0/1   Pending           0    0s     <none>       <none>                    <none>
gateway-6fc44f68c5-sn7fr    0/1   Pending           0    0s     <none>       k3d-quickticket-server-0  <none>
gateway-6fc44f68c5-sn7fr    0/1   ContainerCreating 0    0s     <none>       k3d-quickticket-server-0  <none>
gateway-6fc44f68c5-sn7fr    1/1   Running           0    0s     10.42.0.15   k3d-quickticket-server-0  <none>
```

```bash
$ kubectl get pod -l app=gateway -o jsonpath='{.items[0].status.startTime}'
2026-09-20T10:59:51Z
```

**How long did K8s take to recreate the deleted pod? How does this compare to docker-compose restart?**

The new pod went `Pending → ContainerCreating → Running` in **~5 seconds** after `kubectl delete` (deletion at `13:59:50` local, new pod `startTime 13:59:51`), with **zero human intervention**. With docker-compose (Lab 1) a crashed container only restarts if `restart:` policy allows it — a hard failure or a stopped container stays stopped until a human runs `docker compose start`/`up`, i.e. recovery time = *however long it takes a person to notice*, and there is no controller reconciling "desired state = 1 running container". K8s treats the Deployment's `replicas: 1` as the desired state and the Deployment controller actively drives the cluster back to it: schedule → create pod → start container, all automatically, in single-digit seconds here (fast because the images were pre-imported, so no pull).

---

## Task 2 — Probes & Resource Limits (4 pts)

### 4.9 — Probes configured

Added `livenessProbe` (delay 10s, period 10s, 3 failures) and `readinessProbe` (period 5s, 2 failures) on `/health` to `gateway` (8080), `events` (8081), `payments` (8082). Verified on the live pod:

```console
$ kubectl describe pod -l app=gateway | grep -A 2 "Liveness\|Readiness"
    Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

### 4.10 — Readiness failure during Redis outage

Deleted the redis pod; because k3s brings it back in ~1s, I held the outage window open with `kubectl scale deployment/redis --replicas=0` to observe the steady state:

```console
$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
events-675d86c77-scvqr      0/1     Running   0          105s
gateway-7cd55d8774-7th58    0/1     Running   0          105s
payments-d7dc94485-hlhfv    1/1     Running   0          104s
postgres-85d6f95ff6-xtkgk   1/1     Running   0          104s

$ kubectl describe pod -l app=events | grep -A 2 "Ready"
    Ready:          False
...
  Type                        Status
  Ready                       False
  ContainersReady             False
...
  Warning  Unhealthy  1s (x3 over 12s)   kubelet   Readiness probe failed: Get "http://10.42.0.16:8081/health": context deadline exceeded (...)

$ kubectl get endpoints events
NAME     ENDPOINTS   AGE     # no endpoints listed — events removed from the Service
```

Redis down → events' `/health` fails (its health check covers the redis connection) → readiness probe fails → pod flips to `0/1 Ready` and is **removed from the `events` Service endpoints**, so no traffic is routed to it. The failure also cascaded: gateway's health check calls events, so it went `0/1` as well. After restoring redis (`scale --replicas=1`), the probe passed and the endpoint reappeared:

```console
$ kubectl get pods
NAME                        READY   STATUS    RESTARTS      AGE
events-675d86c77-scvqr      1/1     Running   1 (16s ago)   2m11s
gateway-7cd55d8774-7th58    1/1     Running   1 (20s ago)   2m11s
redis-7b68444dd5-rfdxf      1/1     Running   0             20s

$ kubectl get endpoints events
NAME     ENDPOINTS         AGE
events   10.42.0.16:8081   6m48s
```

Note the `RESTARTS: 1` on events and gateway: while redis was down, their `/health` returned 503, so the **liveness** probe also failed three times and the kubelet restarted the containers — a readiness failure alone would have *not* restarted anything. That distinction is the point of the answer below.

### 4.11 — Resource limits

Added the lab's `resources` block (requests 50m/64Mi, limits 200m/256Mi) to all five containers and re-applied. Node allocation:

```console
$ kubectl describe node k3d-quickticket-server-0 | grep -A 9 "Allocated resources"
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (2%)   1 (5%)
  memory             460Mi (2%)  1450Mi (9%)
  ephemeral-storage  0 (0%)      0 (0%)
```

5 workloads × 50m = 250m app CPU plus system components ≈ 450m of the node's requests.

**What's the difference between liveness and readiness probe failure? Which one should you use for checking database connectivity, and why?**

- **Readiness failure** = the pod is **removed from its Service's endpoints** (no new traffic is sent to it), but it is *not* restarted. It is "I can't serve requests *right now*".
- **Liveness failure** = the kubelet **kills and restarts the container** (after `failureThreshold` consecutive failures). It is "you're deadlocked/stuck, a fresh start will help".

For **database connectivity, use readiness, not liveness** — exactly what happened in 4.10. If the DB is down, restarting the pod cannot possibly fix the DB; you'd just get a crash-loop of restarts (wasted work, log noise, and brief total outages of the pod), while the pod that is *not* restarted simply waits, is drained from traffic, and resumes the moment the DB returns. A liveness check that depends on an external dependency is an antipattern: liveness should only test the process's own health (e.g. "am I hung?"), while readiness is the right place for "can I serve right now" checks that legitimately fail while a dependency is unavailable.

---

## Bonus Task — Helm Chart (2 pts)

### B.1 — Chart scaffold

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
```

### B.2 — Templates

`k8s/chart/templates/` contains the chart-rendered versions of the manifests from Task 1+2 (postgres, redis and the three app Deployment+Service files) with `replicas: {{ .Values.<svc>.replicas }}`, `image: {{ .Values.<svc>.image }}`, and the events DB env vars driven by `{{ .Values.events.db.* }}`. Validated with `helm template` (10 objects rendered).

### B.3 — Install via Helm

```bash
$ kubectl delete -f k8s/
deployment.apps "events" deleted
service "events" deleted
deployment.apps "gateway" deleted
service "gateway" deleted
deployment.apps "payments" deleted
service "payments" deleted
deployment.apps "postgres" deleted
service "postgres" deleted
deployment.apps "redis" deleted
service "redis" deleted

$ helm install quickticket k8s/chart/
NAME: quickticket
NAMESPACE: default
STATUS: deployed
REVISION: 1
DESCRIPTION: Install complete

$ helm list
NAME       	NAMESPACE	REVISION	UPDATED                                	STATUS  	CHART            	APP VERSION
quickticket	default  	1       	2026-09-20 14:05:18.646201554 +0300 MSK	deployed	quickticket-0.1.0

$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
events-675d86c77-gmks6      1/1     Running   0          14s
gateway-7cd55d8774-4vqjb    1/1     Running   0          14s
payments-d7dc94485-m6fp5    1/1     Running   0          14s
postgres-85d6f95ff6-hr25r   1/1     Running   0          14s
redis-7b68444dd5-mskms      1/1     Running   0          14s
```

All 5 pods Running from the Helm release. One wrinkle: the fresh postgres pod started with an **empty data directory** (no volume in the lab manifests), so the seed had to be re-applied — a concrete reminder that stateless Deployments lose state, which is why production postgres would use a PVC / statefulset.

### B.4 — Monitoring via Helm

```bash
$ helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
"prometheus-community" has been added to your repositories

$ helm install monitoring prometheus-community/kube-prometheus-stack \
    --set grafana.adminPassword=admin \
    --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false
```

**How many pods did kube-prometheus-stack create?** It created **6 pods**: `prometheus`, `alertmanager`, `grafana`, `kube-prometheus-operator`, `kube-state-metrics`, `prometheus-node-exporter`.

Five of them came up `Running` (alertmanager 2/2, grafana 3/3, operator 1/1, node-exporter 1/1, prometheus 2/2), but `kube-state-metrics` stalled in `ImagePullBackOff`:

```console
Warning  Failed  30s (x2 over 58s)  kubelet  Failed to pull image "registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.20.0":
  ... Get "https://cdn.registry.k8s.io/containers/images/sha256:...": net/http: TLS handshake timeout
```

The registry's blob CDN (`cdn.registry.k8s.io`) is unreachable from this network (`curl` to it times out, while `registry.k8s.io/v2/` answers in 0.2s), and the image isn't mirrored elsewhere (gcr.io staging has no tags, no Docker Hub mirror). I therefore disabled just that component — `helm upgrade ... --set kubeStateMetrics.enabled=false` — so the **5 remaining monitoring pods are all Running**:

```console
$ kubectl get pods
NAME                                                     READY   STATUS    RESTARTS   AGE
alertmanager-monitoring-kube-prometheus-alertmanager-0   2/2     Running   0          3m22s
events-675d86c77-gmks6                                    1/1     Running   0          4m8s
gateway-7cd55d8774-4vqjb                                  1/1     Running   0          4m8s
monitoring-grafana-668cb8b6b7-wgjdd                       3/3     Running   0          3m31s
monitoring-kube-prometheus-operator-86db765b7f-fzh6l     1/1     Running   0          3m31s
monitoring-prometheus-node-exporter-jrbrk                1/1     Running   0          3m31s
payments-d7dc94485-m6fp5                                  1/1     Running   0          4m7s
postgres-85d6f95ff6-hr25r                                 1/1     Running   0          4m7s
prometheus-monitoring-kube-prometheus-prometheus-0       2/2     Running   0          3m21s
redis-7b68444dd5-mskms                                    1/1     Running   0          40s

$ helm list
NAME       	NAMESPACE	REVISION	UPDATED                                	STATUS  	CHART                       	APP VERSION
monitoring 	default  	2       	2026-09-20 14:09:10.902170849 +0300 MSK	deployed	kube-prometheus-stack-91.4.1	v0.94.0
quickticket	default  	1       	2026-09-20 14:05:18.646201554 +0300 MSK	deployed	quickticket-0.1.0
```

Post-stack node allocation (requests): CPU 450m (2%), memory 660Mi (4%) — the stack is light enough for a single-node k3d cluster.
