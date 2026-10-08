# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

**Student:** Rolan  
**Branch:** `feature/lab4`  
**Date:** 2026-06-18

---

## Task 1 — Write Manifests & Deploy to k3d (6 pts)

### 4.1 — k3d cluster created (`kubectl get nodes`)

```text
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ k3d cluster create quickticket
INFO[0089] Cluster 'quickticket' created successfully!
INFO[0089] You can now use it like this:
kubectl cluster-info

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl get nodes
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   13s   v1.35.5+k3s1
```

One node, status `Ready`.

### 4.2 — Images built and imported into k3d

```text
MacBook-Pro-pon4ik:app rolanmulukin$ docker build -t quickticket-gateway:v1 ./gateway
MacBook-Pro-pon4ik:app rolanmulukin$ docker build -t quickticket-events:v1 ./events
MacBook-Pro-pon4ik:app rolanmulukin$ docker build -t quickticket-payments:v1 ./payments

MacBook-Pro-pon4ik:app rolanmulukin$ k3d image import quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 -c quickticket
INFO[0000] Importing image(s) into cluster 'quickticket'
INFO[0001] Importing images into nodes...
INFO[0004] Successfully imported 3 image(s) into 1 cluster(s)

MacBook-Pro-pon4ik:app rolanmulukin$ docker images | grep quickticket
quickticket-gateway    v1   6774b9799c45   16 hours ago   239MB
quickticket-events     v1   7e6d8c31b973   16 hours ago   260MB
quickticket-payments   v1   22b82b64039f   16 hours ago   237MB
```

### 4.3–4.4 — Manifests written and applied

I wrote one Deployment + Service per component from scratch
(`postgres`, `redis`, `events`, `payments`, `gateway`) — the full manifests are in the
**Appendix** at the end of this report.
All app Deployments use `imagePullPolicy: Never` so K8s uses the locally imported images.
Service names (`postgres`, `redis`, `events`, `payments`, `gateway`) double as in-cluster DNS hostnames,
which is how the env vars wire the services together (e.g. `DB_HOST=postgres`, `EVENTS_URL=http://events:8081`).

```text
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl apply -f k8s/postgres.yaml
deployment.apps/postgres created
service/postgres created
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl apply -f k8s/redis.yaml
deployment.apps/redis created
service/redis created
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl apply -f k8s/gateway.yaml -f k8s/events.yaml -f k8s/payments.yaml
deployment.apps/gateway created
service/gateway created
deployment.apps/events created
service/events created
deployment.apps/payments created
service/payments created
```

### 4.5 — Database seeded

```text
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
>   psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

### 4.6 — All pods & services running (`kubectl get pods,svc`)

```text
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl get pods,svc
NAME                            READY   STATUS    RESTARTS   AGE
pod/events-78696fcf65-6jxv7     1/1     Running   0          2m49s
pod/gateway-7cd55d8774-fm5dl    1/1     Running   0          2m10s
pod/payments-d7dc94485-zq6xn    1/1     Running   0          2m49s
pod/postgres-76cd478b6b-582cz   1/1     Running   0          3m55s
pod/redis-65bb44458c-5n2bx      1/1     Running   0          88s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.104.103   <none>        8081/TCP   2m49s
service/gateway      ClusterIP   10.43.125.134   <none>        8080/TCP   2m49s
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    4m42s
service/payments     ClusterIP   10.43.232.197   <none>        8082/TCP   2m49s
service/postgres     ClusterIP   10.43.223.60    <none>        5432/TCP   3m55s
service/redis        ClusterIP   10.43.162.192   <none>        6379/TCP   3m55s
```

### Full stack working via port-forward (`curl localhost:3080/events`)

```text
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl port-forward svc/gateway 3080:8080 &
[1] 41872
Forwarding from 127.0.0.1:3080 -> 8080

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ curl -s http://localhost:3080/events | python3 -m json.tool
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

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

The full critical path resolves end-to-end: `curl → gateway → events → postgres/redis`, with the seeded
5 events returned and `/health` reporting all downstream dependencies `ok`.

### 4.7 — Self-healing (`kubectl get pods -w` during pod deletion)

```text
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl get pods -l app=gateway -w &
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl delete pod -l app=gateway
pod "gateway-7cd55d8774-29hzl" deleted

NAME                       READY   STATUS              RESTARTS   AGE
gateway-7cd55d8774-29hzl   1/1     Running             0          36s
gateway-7cd55d8774-29hzl   1/1     Terminating         0          38s
gateway-7cd55d8774-29hzl   0/1     Completed           0          39s
gateway-7cd55d8774-fm5dl   0/1     Pending             0          0s
gateway-7cd55d8774-fm5dl   0/1     ContainerCreating   0          0s
gateway-7cd55d8774-fm5dl   0/1     Running             0          0s
gateway-7cd55d8774-fm5dl   1/1     Running             0          6s
```

The instant the old pod (`...29hzl`) terminated, the Deployment's ReplicaSet created a
replacement (`...fm5dl`) — `Pending → ContainerCreating → Running` — and it became `1/1 Ready`
about **6 s** after creation (total end-to-end recovery measured at **~8 s**).

### Answer — K8s recovery vs docker-compose

> K8s recreated the deleted pod **automatically in ~8 seconds**, with **no manual intervention**.
> The Deployment controller continuously reconciles desired state (`replicas: 1`) against actual
> state; deleting the pod drops the count to 0, so the ReplicaSet immediately schedules a new one.
> In Lab 1 with docker-compose, a stopped container **stays down** until I run `docker compose start`
> (or `restart`) by hand — Compose has no reconciliation loop, it only acts on explicit commands.
> This is the core difference: K8s gives **declarative self-healing**, Compose is **imperative**.

---

## Task 2 — Probes & Resource Limits (4 pts)

### 4.9 — Probes configured (`kubectl describe pod`)

```text
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl describe pod -l app=gateway | grep -E "Liveness|Readiness"
    Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl describe pod -l app=events | grep -E "Liveness|Readiness"
    Liveness:   http-get http://:8081/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

Liveness and readiness HTTP probes against `/health` are configured on `gateway` (8080),
`events` (8081) and `payments` (8082).

### 4.10 — Readiness failure during Redis deletion

I deleted the Redis pod and sampled the `events` pod's READY column once per second.
`events`/`health` returns 503 when Redis is unreachable, so its readiness probe starts failing:

```text
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl delete pod -l app=redis
pod "redis-65bb44458c-r56qv" deleted

# events pod sampled every 1s (time | name | READY | STATUS | RESTARTS):
12:04:47  events-78696fcf65-6jxv7   1/1   Running   0
12:04:48  events-78696fcf65-6jxv7   1/1   Running   0
12:04:49  events-78696fcf65-6jxv7   0/1   Running   0   <-- readiness fails, removed from Service endpoints
12:04:50  events-78696fcf65-6jxv7   0/1   Running   0
12:04:52  events-78696fcf65-6jxv7   0/1   Running   0
12:04:53  events-78696fcf65-6jxv7   0/1   Running   0
12:04:54  events-78696fcf65-6jxv7   1/1   Running   0   <-- Redis recreated, probe passes again
```

```text
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl describe pod -l app=events | grep -A1 "Readiness probe failed"
  Warning  Unhealthy  62s (x2 over 67s)  kubelet  Readiness probe failed: context deadline exceeded (Client.Timeout exceeded while awaiting headers)
  Warning  Unhealthy  62s                kubelet  Readiness probe failed: HTTP probe failed with statuscode: 503
  Warning  Unhealthy  60s                kubelet  Liveness probe failed: HTTP probe failed with statuscode: 503
```

The `events` pod went **`0/1 Ready`** for ~5 s — K8s removed it from the Service endpoints so no
traffic was routed to it — but **`RESTARTS` stayed `0`**: the pod was *not* killed. K8s self-healing
recreated the deleted Redis pod within a few seconds, the readiness probe passed again, and `events`
returned to `1/1 Ready`. Note the liveness probe logged a single 503 too, but it needs 3 consecutive
failures (30 s) to restart — Redis recovered long before that, so no restart happened.

### 4.11 — Resource limits + node allocation

Every container declares `requests` (cpu 50m / mem 64Mi) and `limits` (cpu 200m / mem 256Mi).

```text
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl describe node $(kubectl get nodes -o name | head -1) | grep -A 9 "Allocated resources"
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests     Limits
  --------           --------     ------
  cpu                450m (22%)   1 (50%)
  memory             460Mi (23%)  1450Mi (73%)
  ephemeral-storage  0 (0%)       0 (0%)
  hugepages-1Gi      0 (0%)       0 (0%)
  hugepages-2Mi      0 (0%)       0 (0%)

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl top pods
NAME                        CPU(cores)   MEMORY(bytes)
events-78696fcf65-6jxv7     5m           42Mi
gateway-7cd55d8774-fm5dl    6m           38Mi
payments-d7dc94485-zq6xn    5m           41Mi
postgres-76cd478b6b-582cz   4m           30Mi
redis-65bb44458c-5n2bx      9m           8Mi
```

Five app pods × 50m requested = 250m, plus k3d system pods (CoreDNS, traefik, metrics-server, etc.)
bring the node to 450m CPU / 460Mi memory requested. Actual usage (`kubectl top`) is far below the
requests — the requests are reservations for the scheduler, not live consumption.

### Answer — liveness vs readiness for DB connectivity

> **Readiness probe failure** = the pod is removed from the Service endpoints (no traffic routed to it),
> but the pod is **not** restarted. **Liveness probe failure** = the pod is considered dead and is
> **killed and restarted**.
>
> For **database connectivity** you should use a **readiness** probe, not liveness. If the DB is down,
> restarting the app pod does nothing to fix the DB — it just churns pods (and can cause a
> `CrashLoopBackOff` storm across every replica at once). With readiness, the pod stays alive but is
> pulled out of rotation until the DB comes back, then automatically rejoins. Use **liveness** only for
> *self*-faults the process can fix by restarting (deadlock, wedged event loop), never for a shared
> external dependency.

---

## Bonus Task — Helm Chart (2 pts)

I converted the raw manifests into a Helm chart under `k8s/chart/` (templates parameterised via
`{{ .Values.* }}`, including a shared `resources` block injected with `toYaml`).

### `Chart.yaml`

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
```

### `values.yaml`

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
    port: 5432
    name: quickticket
    user: quickticket
    password: quickticket
  redis:
    host: redis
    port: 6379

payments:
  replicas: 1
  image: quickticket-payments:v1
  failureRate: "0.0"
  latencyMs: "0"

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
```

### Install & verify

```text
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl delete -f k8s/
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

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ helm lint k8s/chart/
==> Linting k8s/chart/
[INFO] Chart.yaml: icon is recommended
1 chart(s) linted, 0 chart(s) failed

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ helm install quickticket k8s/chart/
NAME: quickticket
LAST DEPLOYED: Thu Jun 18 12:07:10 2026
NAMESPACE: default
STATUS: deployed
REVISION: 1

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ helm list
NAME         NAMESPACE  REVISION  UPDATED                              STATUS    CHART              APP VERSION
quickticket  default    1         2026-06-18 12:07:10.461871 +0300 MSK deployed  quickticket-0.1.0

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
events-78696fcf65-8cw2s     1/1     Running   0          18s
gateway-7cd55d8774-qm7bz    1/1     Running   0          18s
payments-d7dc94485-xkknh    1/1     Running   0          18s
postgres-76cd478b6b-b8lqh   1/1     Running   0          18s
redis-65bb44458c-mpjsk      1/1     Running   0          18s
```

After re-seeding the fresh Helm-managed postgres pod, the full stack works through the Helm release
exactly as with the raw manifests (`/health` → all `ok`).

**Monitoring (`kube-prometheus-stack`):** optional sub-step, not installed in this run — I focused
the bonus on converting QuickTicket itself into a working, lint-clean Helm chart.

---

## Appendix — Manifests (written from scratch)

These are the manifests used above. The deliverable for this lab is this single report
(`submissions/lab4.md`), so they are embedded here in full.

### `k8s/postgres.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
  labels:
    app: postgres
spec:
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
        - name: postgres
          image: postgres:17-alpine
          ports:
            - containerPort: 5432
          env:
            - name: POSTGRES_DB
              value: "quickticket"
            - name: POSTGRES_USER
              value: "quickticket"
            - name: POSTGRES_PASSWORD
              value: "quickticket"
          readinessProbe:
            exec:
              command: ["pg_isready", "-U", "quickticket"]
            initialDelaySeconds: 5
            periodSeconds: 5
            failureThreshold: 3
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 256Mi
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
spec:
  type: ClusterIP
  selector:
    app: postgres
  ports:
    - port: 5432
      targetPort: 5432
```

### `k8s/redis.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
  labels:
    app: redis
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      containers:
        - name: redis
          image: redis:7-alpine
          ports:
            - containerPort: 6379
          readinessProbe:
            exec:
              command: ["redis-cli", "ping"]
            initialDelaySeconds: 5
            periodSeconds: 5
            failureThreshold: 3
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 256Mi
---
apiVersion: v1
kind: Service
metadata:
  name: redis
spec:
  type: ClusterIP
  selector:
    app: redis
  ports:
    - port: 6379
      targetPort: 6379
```

### `k8s/events.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: events
  labels:
    app: events
spec:
  replicas: 1
  selector:
    matchLabels:
      app: events
  template:
    metadata:
      labels:
        app: events
    spec:
      containers:
        - name: events
          image: quickticket-events:v1
          imagePullPolicy: Never
          ports:
            - containerPort: 8081
          env:
            - name: DB_HOST
              value: "postgres"
            - name: DB_PORT
              value: "5432"
            - name: DB_NAME
              value: "quickticket"
            - name: DB_USER
              value: "quickticket"
            - name: DB_PASS
              value: "quickticket"
            - name: REDIS_HOST
              value: "redis"
            - name: REDIS_PORT
              value: "6379"
          livenessProbe:
            httpGet:
              path: /health
              port: 8081
            initialDelaySeconds: 10
            periodSeconds: 10
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /health
              port: 8081
            periodSeconds: 5
            failureThreshold: 2
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 256Mi
---
apiVersion: v1
kind: Service
metadata:
  name: events
spec:
  type: ClusterIP
  selector:
    app: events
  ports:
    - port: 8081
      targetPort: 8081
```

### `k8s/payments.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: payments
  labels:
    app: payments
spec:
  replicas: 1
  selector:
    matchLabels:
      app: payments
  template:
    metadata:
      labels:
        app: payments
    spec:
      containers:
        - name: payments
          image: quickticket-payments:v1
          imagePullPolicy: Never
          ports:
            - containerPort: 8082
          env:
            - name: PAYMENT_FAILURE_RATE
              value: "0.0"
            - name: PAYMENT_LATENCY_MS
              value: "0"
          livenessProbe:
            httpGet:
              path: /health
              port: 8082
            initialDelaySeconds: 10
            periodSeconds: 10
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /health
              port: 8082
            periodSeconds: 5
            failureThreshold: 2
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 256Mi
---
apiVersion: v1
kind: Service
metadata:
  name: payments
spec:
  type: ClusterIP
  selector:
    app: payments
  ports:
    - port: 8082
      targetPort: 8082
```

### `k8s/gateway.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gateway
  labels:
    app: gateway
spec:
  replicas: 1
  selector:
    matchLabels:
      app: gateway
  template:
    metadata:
      labels:
        app: gateway
    spec:
      containers:
        - name: gateway
          image: quickticket-gateway:v1
          imagePullPolicy: Never
          ports:
            - containerPort: 8080
          env:
            - name: EVENTS_URL
              value: "http://events:8081"
            - name: PAYMENTS_URL
              value: "http://payments:8082"
            - name: GATEWAY_TIMEOUT_MS
              value: "5000"
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 10
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            periodSeconds: 5
            failureThreshold: 2
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 256Mi
---
apiVersion: v1
kind: Service
metadata:
  name: gateway
spec:
  type: ClusterIP
  selector:
    app: gateway
  ports:
    - port: 8080
      targetPort: 8080
```
