# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

**Author:** jakefish18
**Cluster:** k3d `5.9.0` running k3s `v1.35.5+k3s1` (1 node), `kubectl`, `helm 4.2.0`.

> Manifests live in [`k8s/`](../k8s) (postgres, redis, events, payments, gateway) and the
> Helm chart in [`k8s/chart/`](../k8s/chart). Two local adaptations: the gateway
> port-forward uses **local port 3090** (host `3080` is still taken by the Lab 1–3
> docker-compose gateway), and Postgres is **ephemeral** (no PVC, per the lab) so its seed
> is re-loaded whenever the postgres pod is recreated.
---

## Task 1 — Write Manifests & Deploy to k3d

### 1) `kubectl get nodes`

```
NAME                       STATUS   ROLES           AGE     VERSION
k3d-quickticket-server-0   Ready    control-plane   2m16s   v1.35.5+k3s1
```

Images were built locally and imported into the cluster
(`k3d image import quickticket-{gateway,events,payments}:v1`), so every app Deployment
uses `imagePullPolicy: Never`. Postgres/Redis use the public `postgres:17-alpine` /
`redis:7-alpine` images (`imagePullPolicy: IfNotPresent`).

### 2) `kubectl get pods,svc` — all running

```
NAME                           READY   STATUS    RESTARTS   AGE     IP
pod/events-859d5c5c98-cmzw4    1/1     Running   0          24s     10.42.0.14
pod/gateway-6fc44f68c5-4j5gb   1/1     Running   0          3m16s   10.42.0.13
pod/payments-58fb468db-26hgw   1/1     Running   0          3m16s   10.42.0.12
pod/postgres-7c7ffc4b-98l4c    1/1     Running   0          3m28s   10.42.0.10
pod/redis-c46d5dffc-pmchx      1/1     Running   0          3m28s   10.42.0.9
NAME                 TYPE        CLUSTER-IP      PORT(S)    SELECTOR
service/events       ClusterIP   10.43.37.61     8081/TCP   app=events
service/gateway      ClusterIP   10.43.146.15    8080/TCP   app=gateway
service/payments     ClusterIP   10.43.3.222     8082/TCP   app=payments
service/postgres     ClusterIP   10.43.166.235   5432/TCP   app=postgres
service/redis        ClusterIP   10.43.12.9      6379/TCP   app=redis
service/kubernetes   ClusterIP   10.43.0.1       443/TCP
```

> Startup ordering: K8s has no `depends_on`. I avoided the race by applying
> `postgres.yaml`+`redis.yaml` first and `kubectl wait`-ing for them before applying the
> app services, so `events` connected on its first try (it also has a 10× retry loop).
> The fallback `kubectl rollout restart deployment/events` is the documented fix if events
> starts before Postgres is ready.
### 3) Full stack via port-forward (`kubectl port-forward svc/gateway 3090:8080`)

```json
$ curl -s http://localhost:3090/events
[
  {"id": 1, "name": "Go Conference 2026", "venue": "Main Hall A", "total_tickets": 100, "price_cents": 5000, "available": 100},
  {"id": 4, "name": "Python Workshop", "venue": "Lab 301", "total_tickets": 25, "price_cents": 2000, "available": 25},
  ... 5 events total ...
]

$ curl -s http://localhost:3090/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

(The DB was seeded with `kubectl exec -i $(kubectl get pod -l app=postgres -o name) --
psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql` → `CREATE TABLE ×2,
INSERT 0 5`.)

### 4) Self-healing — `kubectl get pods -w` during pod deletion

```
old gateway pod: gateway-6fc44f68c5-4j5gb
$ kubectl delete pod gateway-6fc44f68c5-4j5gb        # t=0
t=+0s  gateway-6fc44f68c5-4j5gb 1/1 Terminating | gateway-6fc44f68c5-7xsrg 0/1 ContainerCreating
t=+1s  gateway-6fc44f68c5-7xsrg 1/1 Running
>>> new pod gateway-6fc44f68c5-7xsrg Ready (1/1 Running) at t=+1s <<<
$ kubectl get deploy gateway
NAME      READY   UP-TO-DATE   AVAILABLE   AGE
gateway   1/1     1            1           3m18s
```

### 5) Recovery time vs docker-compose

**K8s recreated the pod and had it `Ready` in ~1 second, with zero human action.** The
Deployment's ReplicaSet controller runs a continuous reconcile loop: desired `replicas: 1`
≠ actual `0` → it immediately schedules a replacement (fast here because the image was
already on the node). In **Lab 1**, a killed docker-compose service stayed **down until I
manually ran `docker compose start <service>`** — Compose has no controller watching
desired state. That self-healing reconcile loop is the core operational difference: K8s
treats "1 replica running" as a goal it keeps re-asserting, not a one-time action.

---

## Task 2 — Probes & Resource Limits

### Probe design (a deliberate, important choice)

`gateway`'s and `events`' `/health` are **dependency-aware** (they return `503` when a
downstream service / Postgres / Redis is down). A **liveness** probe on such an endpoint
would make the kubelet **restart** the pod every time a *dependency* blips — which never
fixes the dependency and can turn a brief outage into a CrashLoop. So:

- **gateway, events:** `livenessProbe: tcpSocket` (just "is the process alive?") +
  `readinessProbe: httpGet /health` (dependency-aware → pulls the pod from Service
  endpoints when degraded, without killing it).
- **payments:** `/health` is self-contained (no downstream deps), so `httpGet /health` is
  safe for **both** liveness and readiness here.

### `kubectl describe pod` — probes configured

```
gateway   Liveness:   tcp-socket :8080 delay=10s period=10s #failure=3
          Readiness:  http-get http://:8080/health period=5s #failure=2
events    Liveness:   tcp-socket :8081 delay=10s period=10s #failure=3
          Readiness:  http-get http://:8081/health period=5s #failure=2
payments  Liveness:   http-get http://:8082/health delay=10s period=10s #failure=3
          Readiness:  http-get http://:8082/health period=5s #failure=2
```

### Readiness failure during a Redis outage

I scaled Redis to 0 (a plain `kubectl delete pod` self-heals in ~1 s — too short to cross
the 2×5 s readiness threshold), so `events`' `/health` starts returning `503`:

```
baseline   events 1/1 Ready   endpoints=[10.42.0.16]
$ kubectl scale deploy/redis --replicas=0
t=+ 9s  READY=1/1 STATUS=Running RESTARTS=0  endpoints=[10.42.0.16]
t=+12s  READY=0/1 STATUS=Running RESTARTS=0  endpoints=[]      <-- removed from Service
t=+27s  READY=0/1 STATUS=Running RESTARTS=0  endpoints=[]
describe: Warning Unhealthy ... Readiness probe failed: HTTP probe failed with statuscode: 503
$ kubectl scale deploy/redis --replicas=1
t=+ 6s  READY=1/1 STATUS=Running RESTARTS=0   <-- readiness recovers, RESTARTS still 0
```

`events` went **`0/1 Ready`** and was **removed from the Service endpoints** (no traffic
routed to it), but **`RESTARTS` stayed `0`** — the tcpSocket liveness kept it alive. When
Redis returned, readiness passed and the pod was re-added. (The same cascade is visible
upstream: with events out of its endpoints, the gateway's readiness `/health` also reports
events down — graceful degradation, no restarts anywhere.)

### Resource limits — node allocation

Each container has `requests: 50m/64Mi`, `limits: 200m/256Mi`.

```
Allocated resources:
  Resource   Requests    Limits
  cpu        450m (3%)   1 (8%)
  memory     460Mi (5%)  1450Mi (18%)
```

```
$ kubectl top pods
events     5m   44Mi
gateway    5m   38Mi
payments   4m   36Mi
postgres   1m   38Mi
redis      7m    3Mi
```

(450m/460Mi requests = our 5 pods' requests + k3s system pods; actual usage is far below
the limits.)

### Liveness vs readiness — which for DB connectivity, and why?

- **Liveness failure → kubelet kills & restarts the container.** Use it only for "the
  process is wedged/deadlocked and a restart will fix it."
- **Readiness failure → pod removed from Service endpoints (no traffic), NOT restarted;**
  re-added when it passes again. Use it for "temporarily can't serve" — warming up, or a
  dependency is unavailable.

**For database connectivity, use readiness, not liveness.** If the DB is down, restarting
the app pod does nothing to fix the DB — it would just CrashLoop (and a fleet of
simultaneous restarts/reconnects can make the outage worse). Readiness correctly stops
routing traffic to a pod that can't serve and resumes the instant the DB recovers, with no
pointless restarts. My run proves it: `events` (readiness = DB/Redis-aware `/health`) went
`0/1 Ready` with `RESTARTS=0` during the Redis outage; had `/health` been a *liveness*
probe, events would have been killed every ~30 s while Redis was down.

---

## Bonus Task — Helm Chart

Converted the raw manifests into a chart at `k8s/chart/` (`Chart.yaml`, `values.yaml`,
`templates/{postgres,redis,events,payments,gateway}.yaml`). Hardcoded values (`replicas`,
`image`, env vars, and the shared `resources` block via `{{ toYaml .Values.resources }}`)
are now driven from `values.yaml`.

**`Chart.yaml`**
```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
type: application
version: 0.1.0
appVersion: "v1"
```
**`values.yaml`** (excerpt)
```yaml
resources:
  requests: { cpu: 50m, memory: 64Mi }
  limits:   { cpu: 200m, memory: 256Mi }
gateway:  { replicas: 1, image: quickticket-gateway:v1, timeoutMs: "5000" }
events:
  replicas: 1
  image: quickticket-events:v1
  db:    { host: postgres, port: 5432, name: quickticket, user: quickticket, password: quickticket }
  redis: { host: redis, port: 6379 }
payments: { replicas: 1, image: quickticket-payments:v1, failureRate: "0.0", latencyMs: "0" }
postgres: { image: postgres:17-alpine, db: quickticket, user: quickticket, password: quickticket }
redis:    { image: redis:7-alpine }
```

`helm lint` → `0 chart(s) failed`. After `kubectl delete -f k8s/{postgres,redis,events,
payments,gateway}.yaml` and `helm install quickticket k8s/chart/`:

```
$ helm list
NAME         NAMESPACE  REVISION  STATUS    CHART              APP VERSION
quickticket  default    1         deployed  quickticket-0.1.0  v1
$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
events-5c97fcbb69-wwpfk     1/1     Running   0          59s
gateway-97f8b986b-knclg     1/1     Running   0          59s
payments-d7dc94485-75fsr    1/1     Running   0          59s
postgres-78489d7f5f-tn8s8   1/1     Running   0          59s
redis-6fcfb5475d-g479q      1/1     Running   0          59s
```

End-to-end on the Helm-managed stack (after re-seeding the fresh Postgres pod):
```
/events  -> 5 events (first = "Go Conference 2026")
/health  -> {"status":"healthy", ...}
/pay     -> {"order_id":"a446aa4e-...","status":"confirmed"}
```

> The optional `kube-prometheus-stack` monitoring sub-step was not installed (it pulls a
> large multi-pod stack); the chart + release requirements above are all satisfied.

---

## Summary

- Wrote all five Deployment+Service manifests from scratch, built+imported local images,
  deployed to k3d, seeded Postgres, and verified the full critical path through a
  port-forward.
- **Self-healing:** a deleted pod was back `Ready` in ~1 s with no human action — vs
  docker-compose, which needed a manual `start`.
- Added **tcpSocket liveness + httpGet /health readiness** (so a dependency outage pulls a
  pod from Service endpoints instead of restarting it) and resource requests/limits;
  demonstrated `events` going `0/1 Ready` with `RESTARTS=0` during a Redis outage.
- Packaged everything as a working Helm chart (`helm install` → all pods Running,
  end-to-end purchase succeeds).