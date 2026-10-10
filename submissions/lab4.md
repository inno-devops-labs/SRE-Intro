# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

**Branch:** `feature/lab4`
**Cluster:** k3d v5.9.0 / k3s v1.35.5+k3s1 (single node), kubectl v1.36.0, Helm v4.3.0
**Manifests:** [`k8s/`](../k8s/) — written from scratch, no templates provided
**Raw terminal output:** `raw.log`, section `=== LAB 4 ===`

- [x] Task 1 — K8s manifests written, QuickTicket deployed to k3d
- [x] Task 2 — probes and resource limits added
- [x] Bonus Task — Helm chart created (+ kube-prometheus-stack installed)

---

## Task 1 — Write Manifests & Deploy to k3d

### 4.1 Cluster created

```
$ k3d cluster create quickticket
...
INFO[0032] Cluster 'quickticket' created successfully!

$ kubectl get nodes
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   13s   v1.35.5+k3s1
```

### 4.2 Images built and imported

```
$ docker images | grep quickticket
quickticket-gateway           v1             1416fecce176   240MB
quickticket-events            v1             2f61ab704964   260MB
quickticket-payments          v1             4fbe06ed10d9   237MB

$ k3d image import quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 -c quickticket
INFO[0004] Successfully imported 3 image(s) into 1 cluster(s)
```

### 4.3–4.4 Manifests

Five files in `k8s/`, each a `Deployment` + `ClusterIP Service`:

| File | Image | Port | Notes |
|---|---|---|---|
| `postgres.yaml` | `postgres:17-alpine` | 5432 | `POSTGRES_DB/USER/PASSWORD` = `quickticket` |
| `redis.yaml` | `redis:7-alpine` | 6379 | no env |
| `events.yaml` | `quickticket-events:v1` | 8081 | `DB_*`, `REDIS_*`, `RESERVATION_TTL` |
| `payments.yaml` | `quickticket-payments:v1` | 8082 | `PAYMENT_FAILURE_RATE`, `PAYMENT_LATENCY_MS` |
| `gateway.yaml` | `quickticket-gateway:v1` | 8080 | `EVENTS_URL`, `PAYMENTS_URL`, `GATEWAY_TIMEOUT_MS` |

All three app Deployments use `imagePullPolicy: Never` (locally imported images).

Because the hints call it out as the #1 reason K8s rejects a Deployment, I checked
`selector.matchLabels` against the pod template labels *before* applying:

```
$ python3 (compare spec.selector.matchLabels vs spec.template.metadata.labels)
k8s/events.yaml      events    selector={'app': 'events'}   templateLabels={'app': 'events'}   -> MATCH
k8s/gateway.yaml     gateway   selector={'app': 'gateway'}  templateLabels={'app': 'gateway'}  -> MATCH
k8s/payments.yaml    payments  selector={'app': 'payments'} templateLabels={'app': 'payments'} -> MATCH
k8s/postgres.yaml    postgres  selector={'app': 'postgres'} templateLabels={'app': 'postgres'} -> MATCH
k8s/redis.yaml       redis     selector={'app': 'redis'}    templateLabels={'app': 'redis'}    -> MATCH
ALL MATCH

$ kubectl apply -f k8s/ --dry-run=server
deployment.apps/events created (server dry run)
... (10/10 objects accepted)
```

### Startup order — the `depends_on` problem, observed for real

`kubectl apply -f k8s/` applied everything at once. The three app pods used cached
local images and were `Running` in 5 s; `postgres` had to unpack `postgres:17-alpine`
and took 25 s:

```
--- 23:00:09 ---
events-6c4df7d6-lwp2d      1/1   Running             0     5s
gateway-6fc44f68c5-krvbl   1/1   Running             0     5s
payments-58fb468db-2gt5n   1/1   Running             0     5s
postgres-7c7ffc4b-ljn48    0/1   ContainerCreating   0     5s
redis-c46d5dffc-pbwc9      0/1   ContainerCreating   0     5s
--- 23:00:29 ---
... all 5 Running
```

`events` therefore started ~20 s before Postgres could accept connections, burned all
ten of its retries, and gave up:

```
$ kubectl logs -l app=events
{"level":"WARNING","service":"events","msg":"DB connection attempt 1/10 failed: connection to server at
  \"postgres\" (10.43.97.49), port 5432 failed: Connection refused"}
... attempts 2..10 ...
{"level":"ERROR","service":"events","msg":"Could not connect to database after 10 attempts"}
{"level":"INFO","service":"events","msg":"Redis connected"}
INFO:     Application startup complete.
```

**This is the trap the lab warns about, and it is nastier than `CrashLoopBackOff`:** the
pod reported `1/1 Running` the whole time. Nothing in `kubectl get pods` was red. The
process had started fine — it simply had a dead DB pool inside it, and `/health`
returned 503:

```
$ (from inside the cluster) GET http://events:8081/health
urllib.error.HTTPError: HTTP Error 503: Service Unavailable
```

Fix, exactly as the spec prescribes — K8s has no `depends_on`, so the dependency order
is re-established by restarting the dependent *after* the dependency is up:

```
$ kubectl rollout restart deployment/events
deployment.apps/events restarted
deployment "events" successfully rolled out

$ kubectl logs -l app=events --tail=4
{"level":"INFO","service":"events","msg":"DB pool created (max=10)"}
{"level":"INFO","service":"events","msg":"Redis connected"}
INFO:     Application startup complete.
```

(In Task 2 the readiness probe turns this silent failure into a visible `0/1 Ready`,
which is the whole point of adding probes.)

### 4.5 Database seeded

```
$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
    psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql
CREATE TABLE
CREATE TABLE
INSERT 0 5

$ kubectl exec ... -- psql -U quickticket -d quickticket -c "SELECT id,name,total_tickets FROM events;"
 id |         name         | total_tickets
----+----------------------+---------------
  1 | Go Conference 2026   |           100
  2 | SRE Meetup           |            30
  3 | Cloud Native Summit  |           500
  4 | Python Workshop      |            25
  5 | Kubernetes Deep Dive |            80
(5 rows)
```

> Note: Postgres runs without a PVC, so its data lives in the pod. Every time the
> Postgres pod was replaced (e.g. by the `resources:` rollout in 4.11) the seed had to
> be re-loaded. In docker-compose the named volume `postgres_data` survived restarts —
> the K8s equivalent would be a PersistentVolumeClaim, which this lab does not cover.

### 4.8.2 — `kubectl get pods,svc`

```
$ kubectl get pods,svc
NAME                           READY   STATUS    RESTARTS   AGE
pod/events-86c76f9997-f9ngc    1/1     Running   0          5s
pod/gateway-6fc44f68c5-krvbl   1/1     Running   0          49s
pod/payments-58fb468db-2gt5n   1/1     Running   0          49s
pod/postgres-7c7ffc4b-ljn48    1/1     Running   0          49s
pod/redis-c46d5dffc-pbwc9      1/1     Running   0          49s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.160.66    <none>        8081/TCP   50s
service/gateway      ClusterIP   10.43.209.24    <none>        8080/TCP   50s
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    117s
service/payments     ClusterIP   10.43.181.6     <none>        8082/TCP   50s
service/postgres     ClusterIP   10.43.97.49     <none>        5432/TCP   50s
service/redis        ClusterIP   10.43.115.155   <none>        6379/TCP   50s
```

### 4.8.3 — Critical path through the port-forward

```
$ kubectl port-forward svc/gateway 3080:8080 &
Forwarding from 127.0.0.1:3080 -> 8080

$ curl -s http://localhost:3080/events | python3 -m json.tool
[
    {"id": 1, "name": "Go Conference 2026",   "venue": "Main Hall A",  "date": "2026-09-15T09:00:00+00:00", "total_tickets": 100, "price_cents": 5000,  "available": 100},
    {"id": 4, "name": "Python Workshop",      "venue": "Lab 301",      "date": "2026-09-22T14:00:00+00:00", "total_tickets": 25,  "price_cents": 2000,  "available": 25},
    {"id": 2, "name": "SRE Meetup",           "venue": "Room 204",     "date": "2026-10-01T18:00:00+00:00", "total_tickets": 30,  "price_cents": 0,     "available": 30},
    {"id": 5, "name": "Kubernetes Deep Dive", "venue": "Auditorium B", "date": "2026-10-10T10:00:00+00:00", "total_tickets": 80,  "price_cents": 8000,  "available": 80},
    {"id": 3, "name": "Cloud Native Summit",  "venue": "Expo Center",  "date": "2026-11-20T10:00:00+00:00", "total_tickets": 500, "price_cents": 15000, "available": 500}
]

$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "healthy",
    "checks": {"events": "ok", "payments": "ok", "circuit_payments": "CLOSED"}
}
```

Full write path (gateway → events → postgres/redis → payments), not just the read:

```
$ curl -s -X POST localhost:3080/events/1/reserve -d '{"quantity":2}'
{"reservation_id": "e94b0166-20c1-444e-bc0c-a1cc1c7791ca", "event_id": 1, "quantity": 2,
 "total_cents": 10000, "expires_in_seconds": 300}

$ curl -s -X POST localhost:3080/reserve/e94b0166-.../pay -d '{}'
{"order_id": "e94b0166-20c1-444e-bc0c-a1cc1c7791ca", "event_id": 1, "quantity": 2,
 "total_cents": 10000, "status": "confirmed"}

$ curl -s localhost:3080/events/1
{"id": 1, "name": "Go Conference 2026", ..., "total_tickets": 100, "available": 98}
```

`available` went 100 → 98, so the reservation really landed in Postgres.

### 4.7 / 4.8.4 — Self-healing

Method: `date -u` immediately before the delete, a timestamped `kubectl get pods -w`
stream running in parallel, plus a 1 s poll loop, so every transition carries a real
wall-clock second.

```
T0_DELETE=23:01:32
$ kubectl delete pod -l app=gateway
pod "gateway-6fc44f68c5-krvbl" deleted from default namespace

--- timestamped `kubectl get pods -l app=gateway -w` stream ---
23:01:31 gateway-6fc44f68c5-krvbl   1/1   Running             0   86s
23:01:32 gateway-6fc44f68c5-krvbl   1/1   Terminating         0   87s
23:01:32 gateway-6fc44f68c5-krvbl   0/1   Completed           0   87s
23:01:32 gateway-6fc44f68c5-lr2wz   0/1   Pending             0   0s
23:01:32 gateway-6fc44f68c5-lr2wz   0/1   ContainerCreating   0   0s
23:01:33 gateway-6fc44f68c5-lr2wz   1/1   Running             0   1s      <-- back up
23:01:33 gateway-6fc44f68c5-krvbl   0/1   Completed           0   88s

--- 1s poll ---
23:01:34 | gateway-6fc44f68c5-lr2wz   1/1   Running   0   2s
READY_AT=23:01:34
```

Measured recovery: **delete 23:01:32 → new pod `Running` 23:01:33 ≈ 1 second**
(the 1 s poll confirms it independently at 23:01:34, i.e. ≤ 2 s). The replacement pod
was `Pending` in the same second the old one went `Terminating` — the ReplicaSet
controller does not wait for the old pod to finish terminating before scheduling its
replacement.

### 4.8.5 — Written answer: recovery time vs docker-compose

K8s recreated the deleted pod in about **1 second** — the delete was at `23:01:32`, the
replacement was already `Pending`/`ContainerCreating` in that same second, and it was
`1/1 Running` at `23:01:33`. A 1-second poll independently confirmed it was still up at
`23:01:34`. Notably, the ReplicaSet didn't even wait for the old pod to finish
`Terminating` before scheduling the new one — the two overlapped.

The docker-compose comparison from Lab 1 isn't really a speed comparison, it's a
comparison of *whether recovery happens at all without a human*. `docker compose stop
payments` left the container `Exited` indefinitely — nothing about compose runs
continuously to notice that a container it started is now gone or broken. Recovery
time there wasn't some number of seconds, it was "however long until I typed `docker
compose start payments` myself." Even `restart: unless-stopped`, which I didn't have
configured but which the compose docs offer, only restarts the *same* container after
a crash — it can't do anything about a container that's still running but returning
503s, and it can't replace one that got deleted outright.

The reason for that gap isn't that Kubernetes is faster at restarting things, it's that
Kubernetes and Compose are solving different problems. Compose executes a fixed set of
commands once — `up`, `stop`, `start` — and then it's done; there's no process
afterward that keeps checking anything. A Deployment in Kubernetes declares a *desired
state* (`replicas: 1`) and the controller keeps comparing that against the actual
state, in a loop, forever. Deleting the gateway pod didn't "break" anything from the
controller's point of view — it just made observed state (0 pods) diverge from desired
state (1 pod), and the loop closed that gap on its own, the same way it would close it
if the pod had crashed, been OOM-killed, or evicted for any other reason. That's the
real difference: compose recovers from nothing by itself, Kubernetes recovers from
almost anything by design, because recovery isn't a special case it handles — it's the
same reconciliation loop running all the time regardless of why the state drifted.

One caveat worth being honest about: `Running` and actually serving traffic aren't the
same milestone. This 1-second measurement was taken *before* Task 2 added a readiness
probe, so `Running` was a good enough proxy for "back in service" at the time. Once
probes existed, the Helm install in the Bonus task took a fuller 15 seconds to get all
5 pods to `1/1 Ready` — that's the more honest number for "time until this pod is
actually taking traffic," and it's a reminder that self-healing without a readiness
probe can silently put a not-yet-ready pod back in the traffic path, which is exactly
the "1/1 Running but /health is 503" trap from §4.4.

---

## Task 2 — Probes & Resource Limits

### 4.9 Probes configured

`livenessProbe` + `readinessProbe` on `/health` added to gateway (8080), events (8081),
payments (8082) with the periods from the spec.

```
$ kubectl describe pod -l app=gateway | grep -A 5 "Liveness\|Readiness"
    Liveness:       http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:      http-get http://:8080/health delay=0s  timeout=1s period=5s  #success=1 #failure=2

$ kubectl describe pod -l app=events ...
    Liveness:       http-get http://:8081/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:      http-get http://:8081/health delay=0s  timeout=1s period=5s  #success=1 #failure=2

$ kubectl describe pod -l app=payments ...
    Liveness:       http-get http://:8082/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:      http-get http://:8082/health delay=0s  timeout=1s period=5s  #success=1 #failure=2
```

### 4.10 Observing readiness failure

**First attempt failed to demonstrate anything — and that is itself the result.**
`kubectl delete pod -l app=redis` did *not* produce a readiness failure: the ReplicaSet
recreated Redis in under 3 s (same ~1 s self-healing as 4.7), while detection needs
≥ 10 s (readiness `period=5s × failure=2`) plus the events app's own 5 s Redis-check
cache. The outage was over before the probe could notice it.

To hold Redis down long enough, I scaled the Deployment to zero instead:

```
T0_SCALE_DOWN=23:05:34   (events pod AGE at this moment: 2m58s)
$ kubectl scale deployment/redis --replicas=0
deployment.apps/redis scaled

--- 23:05:50 ---   (AGE 3m14s)
events-6d67955775-ttp66    1/1   Running   0   3m14s
    ep/events  ready=[10.42.0.16] notReady=[]
    ep/gateway ready=[10.42.0.17] notReady=[]

--- 23:05:54 ---   (AGE 3m18s)  <-- events drops out, ~20s after Redis went away
events-6d67955775-ttp66    0/1   Running   0   3m18s
    ep/events  ready=[]           notReady=[10.42.0.16]
    ep/gateway ready=[10.42.0.17] notReady=[]

--- 23:05:57 ---   (AGE 3m21s)  <-- gateway follows, ~23s (its /health calls events/health)
events-6d67955775-ttp66    0/1   Running   0   3m21s
gateway-854488bf7c-f9b2n   0/1   Running   0   3m21s
    ep/events  ready=[]           notReady=[10.42.0.16]
    ep/gateway ready=[]           notReady=[10.42.0.17]
```

`0/1 Ready` as required, and the Service endpoints prove traffic was actually cut:

```
$ kubectl get endpoints        # DURING the outage
NAME         ENDPOINTS         AGE
events                         33m      <-- empty
gateway                        33m      <-- empty
payments     10.42.0.18:8082   33m
postgres     10.42.0.11:5432   33m
redis        <none>            33m

$ (from the payments pod) GET http://events:8081/health
urllib.error.URLError: <urlopen error [Errno 111] Connection refused>
```

The pod IP moved from `addresses` to `notReadyAddresses`, the Service was left with
zero backends, and a request to `events:8081` was refused outright — nothing was
routed to the unhealthy pod.

Pod conditions and kubelet events:

```
$ kubectl describe pod -l app=events
Conditions:
  Type                        Status
  Initialized                 True
  Ready                       False    <-- 
  ContainersReady             False
  PodScheduled                True

Events:
  Warning  Unhealthy  ...  Readiness probe failed: HTTP probe failed with statuscode: 503
  Warning  Unhealthy  ...  Liveness probe failed: HTTP probe failed with statuscode: 503
  Normal   Killing    ...  Container events failed liveness probe, will be restarted
```

**The liveness probe fired too**, because I pointed both probes at the same `/health`
endpoint — and `/health` reports the state of Redis, a *dependency*. So K8s restarted a
perfectly healthy process because a different pod was down:

```
$ kubectl get pods
events-6d67955775-ttp66     0/1   Running   1 (22m ago)   30m
gateway-854488bf7c-f9b2n    0/1   Running   1 (22m ago)   30m     <-- restart count climbing
```

Recovery:

```
T0_SCALE_UP=23:33:38
$ kubectl scale deployment/redis --replicas=1
redis-c46d5dffc-b7vqv   1/1   Running   0   4s      <-- Redis back almost immediately

--- 23:34:02 ---
events-6d67955775-ttp66   0/1   Running   2 (38s ago)   31m       <-- still NOT ready
--- 23:34:06 ---
events-6d67955775-ttp66   1/1   Running   3 (2s ago)    31m       <-- ready only after ANOTHER restart
    ep/events  ready=[10.42.0.16] notReady=[]
ALL_READY_AT=23:34:06
```

The reason is in the logs — the old process never recovered on its own, it was the
liveness restart that fixed it by re-running startup:

```
$ kubectl logs -l app=events --previous     # the container that was killed
INFO:     "GET /health HTTP/1.1" 503 Service Unavailable   (x8, continuing after Redis was back)
INFO:     Shutting down

$ kubectl logs -l app=events                # the fresh container
{"level":"INFO","service":"events","msg":"DB pool created (max=10)"}
{"level":"INFO","service":"events","msg":"Redis connected"}
INFO:     "GET /health HTTP/1.1" 200 OK
```

> Measurement caveat, recorded honestly: my laptop suspended partway through this
> observation, so host wall-clock jumps (23:05:57 → 23:10:05 → 23:33) and the kubelet
> was frozen across that gap. The probe-failure *counts* in `describe` are therefore
> lower than the nominal 10 s period would imply. The transition timings quoted above
> are anchored to **pod AGE** (the in-cluster clock), which stayed consistent.

### 4.11 Resource requests and limits

`requests: cpu 50m / memory 64Mi`, `limits: cpu 200m / memory 256Mi` added to all five
containers.

```
$ kubectl describe node k3d-quickticket-server-0 | grep -A 10 "Allocated resources"
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (4%)   1 (9%)
  memory             460Mi (5%)  1450Mi (18%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
  hugepages-32Mi     0 (0%)      0 (0%)
  hugepages-64Ki     0 (0%)      0 (0%)

Non-terminated Pods:          (10 in total)
  Namespace     Name                          CPU Requests  CPU Limits  Memory Requests  Memory Limits
  default       events-765b7749fb-ppvsx       50m (0%)      200m (1%)   64Mi (0%)        256Mi (3%)
  default       gateway-7cd55d8774-nqwml      50m (0%)      200m (1%)   64Mi (0%)        256Mi (3%)
  default       payments-d7dc94485-fh889      50m (0%)      200m (1%)   64Mi (0%)        256Mi (3%)
  default       postgres-78489d7f5f-9gc6t     50m (0%)      200m (1%)   64Mi (0%)        256Mi (3%)
  default       redis-6fcfb5475d-jhnxn        50m (0%)      200m (1%)   64Mi (0%)        256Mi (3%)
  kube-system   coredns-8db54c48d-85f96       100m (0%)     0 (0%)      70Mi (0%)        170Mi (2%)
  kube-system   metrics-server-786d997795     100m (0%)     0 (0%)      70Mi (0%)        0 (0%)
  ...
```

Actual usage sits inside the 50m CPU request for every pod (Postgres closest, at
48m while serving the seed + queries), and well inside the 64Mi memory request:

```
$ kubectl top pods
NAME                        CPU(cores)   MEMORY(bytes)
events-765b7749fb-ppvsx     25m          39Mi
gateway-7cd55d8774-nqwml    6m           37Mi
payments-d7dc94485-fh889    5m           34Mi
postgres-78489d7f5f-9gc6t   48m          23Mi
redis-6fcfb5475d-jhnxn      11m          8Mi
```

### Written answer: liveness vs readiness, and which one for DB connectivity

The two failures in 4.10 look identical at the surface — same 503, same probe hitting
the same `/health` endpoint — but they did completely different things to the pod.
Readiness failure pulled `events` out of rotation without touching the running
process: the pod IP moved from `addresses` to `notReadyAddresses`, `kubectl get
endpoints events` went empty, and a request from another pod got a flat `Connection
refused`. Nothing inside the container was disturbed — it just stopped receiving
traffic. Liveness failure did the opposite: it killed the container outright
(`Container events failed liveness probe, will be restarted`) and `RESTARTS` climbed
0→1→2→3, even though the process itself was fine. It was killed because *Redis* was
down, not because anything was wrong with events.

That's really the core problem with pointing liveness at the same `/health` endpoint
that checks a dependency: restarting a pod can only fix things that are wrong *inside
that pod* — a stuck thread, a memory leak, corrupted internal state. It does nothing
about an external dependency, because the dependency is, by definition, external. All
it did here was throw away the connection pool events had already built up (the logs
show `DB pool created (max=10)` on every fresh start) and cause a visible cascade:
gateway's own `/health` calls events' `/health`, so gateway went `0/1` about 3 seconds
after events did, and gateway's liveness killed *it* too — one Redis outage restarting
two services that had nothing wrong with them individually. With more replicas or a
longer outage, that's exactly the shape of a self-inflicted cascading failure: every
dependent service restart-looping in sync with the thing that's actually down.

There's a complication in my own run that's worth being honest about instead of
glossing over: events did *not* recover on its own once Redis came back up. The old
process kept returning 503 — its Redis client was apparently still bound to the dead
connection — and it was the liveness-triggered restart that actually fixed it, by
forcing a fresh process that reconnected cleanly. So in this specific case, the "wrong"
probe accidentally did something useful. I don't think that's an argument for
configuring it this way, though — it worked here because the app doesn't reconnect to
Redis on its own after a drop. If it did, the readiness probe by itself would have been
enough: traffic stops during the outage, comes back once the dependency check passes
again, no restart needed. Relying on liveness to paper over a missing reconnect is
fragile in a way that specific bug happened to hide — a longer outage, or a service
that doesn't recover cleanly after a forced restart, turns the same setup into an
endless restart loop instead of a lucky fix.

For database connectivity specifically, the answer is readiness, not liveness. If
Postgres goes down, restarting `events` doesn't bring Postgres back — it just discards
a working connection pool for no benefit. What actually helps is taking that pod out of
the Service's endpoint list so no traffic gets routed to something that can't serve it,
which is exactly what the readiness probe did here without any collateral damage. Given
what this run showed, I'd split the two endpoints if I were doing this for real: a
`/livez` with no dependency calls at all for liveness (fails only if the process itself
is wedged), and `/health` with the dependency checks kept on readiness. That also
closes the loop back to §4.4 — a readiness probe would have caught that silent
`1/1 Running`-with-no-DB-pool state as `0/1 Ready` automatically, instead of needing me
to `curl /health` by hand to even notice something was wrong.

---

## Bonus Task — Helm Chart

### B.1 / B.2 Chart

```
k8s/chart/
├── Chart.yaml
├── values.yaml
└── templates/
    ├── events-deployment.yaml     ├── payments-deployment.yaml
    ├── events-service.yaml        ├── payments-service.yaml
    ├── gateway-deployment.yaml    ├── postgres-deployment.yaml
    ├── gateway-service.yaml       ├── postgres-service.yaml
    ├── redis-deployment.yaml      └── redis-service.yaml
```

`Chart.yaml`:

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
```

`values.yaml`:

```yaml
gateway:
  replicas: 1
  image: quickticket-gateway:v1
  port: 8080
  eventsUrl: "http://events:8081"
  paymentsUrl: "http://payments:8082"
  timeoutMs: "5000"

events:
  replicas: 1
  image: quickticket-events:v1
  port: 8081
  db: {host: postgres, port: 5432, name: quickticket, user: quickticket, password: quickticket, maxConns: "10"}
  redis: {host: redis, port: 6379, timeoutMs: "1000"}
  reservationTtl: "300"

payments:
  replicas: 1
  image: quickticket-payments:v1
  port: 8082
  failureRate: "0.0"
  latencyMs: "0"

postgres:
  replicas: 1
  image: postgres:17-alpine
  port: 5432
  db: quickticket
  user: quickticket
  password: quickticket

redis:
  replicas: 1
  image: redis:7-alpine
  port: 6379

imagePullPolicy: Never

probes:
  liveness:  {initialDelaySeconds: 10, periodSeconds: 10, failureThreshold: 3}
  readiness: {periodSeconds: 5, failureThreshold: 2}

resources:
  requests: {cpu: 50m, memory: 64Mi}
  limits:   {cpu: 200m, memory: 256Mi}
```

(The real `values.yaml` uses expanded block style; collapsed here for readability.)

Ports are parameterised too, so `containerPort`, the probe port and the Service port
cannot drift apart; `probes` and `resources` are shared across services and injected
with `toYaml`.

Before installing I checked the chart is a faithful translation rather than a rewrite —
rendering it and diffing every object against the raw manifests:

```
$ helm lint k8s/chart/
1 chart(s) linted, 0 chart(s) failed

$ helm template quickticket k8s/chart/  |  diff against k8s/*.yaml (parsed YAML, per object)
raw objects:      10
rendered objects: 10
same key set: True
  Deployment  events    -> IDENTICAL      Service  events    -> IDENTICAL
  Deployment  gateway   -> IDENTICAL      Service  gateway   -> IDENTICAL
  Deployment  payments  -> IDENTICAL      Service  payments  -> IDENTICAL
  Deployment  postgres  -> IDENTICAL      Service  postgres  -> IDENTICAL
  Deployment  redis     -> IDENTICAL      Service  redis     -> IDENTICAL
RESULT: chart renders equivalent objects
```

### B.3 Install

```
$ kubectl delete -f k8s/
deployment.apps "events" deleted ... service "redis" deleted     (10 objects)

$ helm install quickticket k8s/chart/
NAME: quickticket
STATUS: deployed
REVISION: 1

$ helm list
NAME         NAMESPACE  REVISION  UPDATED                  STATUS    CHART                        APP VERSION
monitoring   default    1         2026-09-21 10:37:29 MSK  deployed  kube-prometheus-stack-91.4.1  v0.94.0
quickticket  default    1         2026-09-21 10:36:28 MSK  deployed  quickticket-0.1.0

$ kubectl get pods        # 15s after install, all Ready
events-675d86c77-lvd9s      1/1   Running   0   15s
gateway-7cd55d8774-fgl9n    1/1   Running   0   15s
payments-d7dc94485-wwxgk    1/1   Running   0   15s
postgres-78489d7f5f-9njr7   1/1   Running   0   15s
redis-6fcfb5475d-29slt      1/1   Running   0   15s
```

Worth noting against 4.4: this time `events` connected to Postgres **on the first
try** — no `rollout restart` needed. Both images were already cached on the node, so
Postgres was accepting connections inside the events app's 10×2 s retry budget. The
startup-order bug didn't disappear, it just didn't trigger; that non-determinism is
exactly why probes/init containers (not luck) are the real fix.

Stack verified after the Helm install (seed re-loaded, Postgres pod is new):

```
$ curl -s localhost:3080/health
{"status": "healthy", "checks": {"events": "ok", "payments": "ok", "circuit_payments": "CLOSED"}}

$ curl -s localhost:3080/events
5 events: ['Go Conference 2026', 'Python Workshop', 'SRE Meetup', 'Kubernetes Deep Dive', 'Cloud Native Summit']
```

### B.4 Monitoring via Helm

`kube-prometheus-stack` installed successfully — it fit in the default k3d resources.

```
$ helm install monitoring prometheus-community/kube-prometheus-stack \
    --set grafana.adminPassword=admin \
    --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false
NAME: monitoring
STATUS: deployed
REVISION: 1
```

**How many pods did kube-prometheus-stack create? Six**, taking the cluster from 5 pods
to 11:

```
$ kubectl get pods
NAME                                                     READY   STATUS    RESTARTS   AGE
alertmanager-monitoring-kube-prometheus-alertmanager-0   2/2     Running   0          63s   <-- 1
monitoring-grafana-668cb8b6b7-4gfks                      2/3     Running   0          75s   <-- 2
monitoring-kube-prometheus-operator-86db765b7f-7cqgf     1/1     Running   0          75s   <-- 3
monitoring-kube-state-metrics-6d8ffd8867-jt8tl           1/1     Running   0          75s   <-- 4
monitoring-prometheus-node-exporter-klm6j                1/1     Running   0          75s   <-- 5
prometheus-monitoring-kube-prometheus-prometheus-0       2/2     Running   0          62s   <-- 6
events-675d86c77-lvd9s                                   1/1     Running   0          2m27s
gateway-7cd55d8774-fgl9n                                 1/1     Running   0          2m27s
payments-d7dc94485-wwxgk                                 1/1     Running   0          2m27s
postgres-78489d7f5f-9njr7                                1/1     Running   0          2m27s
redis-6fcfb5475d-29slt                                   1/1     Running   0          2m27s
```

Their workload kinds, confirmed with `kubectl get statefulsets,daemonsets,deployments`:
`alertmanager-...-0` and `prometheus-...-0` are **StatefulSet** pods, created by the
Prometheus Operator from the Alertmanager/Prometheus CRDs rather than by the chart
directly; `node-exporter` is a **DaemonSet** (1 desired on this 1-node cluster, so it
would scale with the cluster); grafana, the operator and kube-state-metrics are plain
**Deployments**.

Node allocation barely moved, because most of the stack ships without resource
requests:

```
$ kubectl describe node k3d-quickticket-server-0 | grep -A 6 "Allocated resources"
  Resource  Requests    Limits
  cpu       450m (4%)   1 (9%)
  memory    660Mi (8%)  1450Mi (18%)
```

QuickTicket kept serving throughout:

```
$ curl -s localhost:3080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```
