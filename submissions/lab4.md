# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

Everything below was actually run against a local k3d cluster called `quickticket`. If something
could not be run, I say so directly.

**Manifests are in [`k8s/`](../k8s/)**: `postgres.yaml`, `redis.yaml`, `gateway.yaml`,
`events.yaml`, `payments.yaml`. The bonus Helm chart is in [`k8s/chart/`](../k8s/chart/), but
the raw manifests are what's actually deployed in the cluster and count as the source of truth.

---

## Task 1 — Write Manifests & Deploy to k3d (6 pts)

### 4.1 Create the cluster

k3d was already installed (`v5.9.0`), so the install script was skipped.

```
$ k3d cluster create quickticket
INFO[0000] Prep: Network
INFO[0000] Created network 'k3d-quickticket'
INFO[0000] Created image volume k3d-quickticket-images
INFO[0000] Starting new tools node...
INFO[0001] Creating node 'k3d-quickticket-server-0'
INFO[0003] Pulling image 'docker.io/rancher/k3s:v1.35.5-k3s1'
INFO[0009] Creating LoadBalancer 'k3d-quickticket-serverlb'
INFO[0014] Starting cluster 'quickticket'
INFO[0014] Starting servers...
INFO[0020] Starting helpers...
INFO[0027] Injecting records for hostAliases (incl. host.k3d.internal) and for 2 network members into CoreDNS configmap...
INFO[0029] Cluster 'quickticket' created successfully!
```

**Proof of work #1: `kubectl get nodes`**

```
$ kubectl get nodes
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   13m   v1.35.5+k3s1
```

```
$ kubectl version
Client Version: v1.35.3
Kustomize Version: v5.7.1
Server Version: v1.35.5+k3s1
```

> Note: the course README mentions k3s v1.33, but k3d v5.9.0 defaults to **k3s v1.35.5+k3s1**.
> That's what actually ran here. Nothing in the lab depends on the exact version.

### 4.2 Build and import images

```
$ docker build -t quickticket-gateway:v1  ./gateway
$ docker build -t quickticket-events:v1   ./events
$ docker build -t quickticket-payments:v1 ./payments

$ k3d image import quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 -c quickticket
INFO[0000] Importing image(s) into cluster 'quickticket'
INFO[0000] Saving 3 image(s) from runtime...
INFO[0002] Importing images into nodes...
INFO[0002] Importing images from tarball '/k3d/images/k3d-quickticket-images-20260922000517.tar' into node 'k3d-quickticket-server-0'...
INFO[0009] Successfully imported 3 image(s) into 1 cluster(s)

$ docker images | grep quickticket
quickticket-events:v1      1e5e73bec0f4   234MB   57.3MB
quickticket-gateway:v1     07762a3b0e8d   215MB   52.3MB
quickticket-payments:v1    31915a2f5ff6   213MB   51.7MB
```

These are the same optimised images from Lab 2 (`.dockerignore` plus the non-root `app` user).
Since the app listens on 8080/8081/8082, all above 1024, running as non-root doesn't need any
extra capabilities, so the `containerPort` values just carry over from compose unchanged.

### 4.3 Postgres and Redis

`k8s/postgres.yaml` contains four objects rather than two:

| Object | Why |
|---|---|
| `ConfigMap/postgres-seed` | `app/seed.sql` mounted at `/docker-entrypoint-initdb.d/01-seed.sql`, the same wiring the compose file did with a bind mount. The DB seeds itself on first start. |
| `PersistentVolumeClaim/postgres-data` | replaces the compose named volume `postgres_data`. k3d's default `local-path` StorageClass provisions it. |
| `Deployment/postgres` | `strategy: Recreate`: the PVC is `ReadWriteOnce`, so a rolling update would deadlock with two pods wanting the same volume. |
| `Service/postgres` | ClusterIP :5432: the DNS name `postgres` is what `DB_HOST` resolves to. |

```
$ kubectl apply -f k8s/postgres.yaml
configmap/postgres-seed created
persistentvolumeclaim/postgres-data created
deployment.apps/postgres created
service/postgres created

$ kubectl apply -f k8s/redis.yaml
deployment.apps/redis created
service/redis created

$ kubectl get pods,svc
NAME                            READY   STATUS    RESTARTS   AGE
pod/postgres-67977f4df6-jqgl9   1/1     Running   0          26s
pod/redis-87cf6bc6b-f5ng8       1/1     Running   0          26s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    81s
service/postgres     ClusterIP   10.43.100.101   <none>        5432/TCP   26s
service/redis        ClusterIP   10.43.150.16    <none>        6379/TCP   26s
```

Redis has no PVC, on purpose. It only holds reservation holds with `RESERVATION_TTL=300`, so
losing them on a restart is fine. Giving it a volume would just be fake durability.

### 4.4 The three QuickTicket services

```
$ kubectl apply -f k8s/
deployment.apps/events created
service/events created
deployment.apps/gateway created
service/gateway created
deployment.apps/payments created
service/payments created
configmap/postgres-seed unchanged
persistentvolumeclaim/postgres-data unchanged
deployment.apps/postgres unchanged
service/postgres unchanged
deployment.apps/redis unchanged
service/redis unchanged
```

**Proof of work #2: `kubectl get pods,svc` with everything running**

```
$ kubectl get pods,svc
NAME                            READY   STATUS    RESTARTS   AGE
pod/events-6f9bcfd995-sq6m9     1/1     Running   0          20s
pod/gateway-587477c67-m8j9k     1/1     Running   0          20s
pod/payments-5b5c664bc4-2q2sh   1/1     Running   0          20s
pod/postgres-67977f4df6-jqgl9   1/1     Running   0          50s
pod/redis-87cf6bc6b-f5ng8       1/1     Running   0          50s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.131.78    <none>        8081/TCP   20s
service/gateway      ClusterIP   10.43.252.53    <none>        8080/TCP   20s
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    105s
service/payments     ClusterIP   10.43.250.157   <none>        8082/TCP   20s
service/postgres     ClusterIP   10.43.100.101   <none>        5432/TCP   50s
service/redis        ClusterIP   10.43.150.16    <none>        6379/TCP   50s
```

No `CrashLoopBackOff`, and I didn't need a `rollout restart`. The lab's hint warns that `events`
might start before Postgres is ready, since K8s has no `depends_on`. That didn't actually happen
here because `events` opens its connection pool lazily and retries. But the general point still
holds: **ordering in K8s is handled with probes, not declared dependencies.** Task 2 makes that
explicit instead of just relying on luck.

### 4.5 Database initialisation

The ConfigMap route means the DB seeds itself, no manual step:

```
$ kubectl logs -l app=postgres --tail=200 | grep -i "initdb\|ready to accept"
2026-09-21 21:06:04.186 UTC [42] LOG:  database system is ready to accept connections
/usr/local/bin/docker-entrypoint.sh: running /docker-entrypoint-initdb.d/01-seed.sql
2026-09-21 21:06:05.035 UTC [1] LOG:  database system is ready to accept connections
```

I also ran the lab's explicit seed command anyway. **It turns out it's not idempotent**, which is worth knowing:

```
$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
    psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql
CREATE TABLE
psql:/dev/stdin:10: NOTICE:  relation "events" already exists, skipping
psql:/dev/stdin:20: NOTICE:  relation "orders" already exists, skipping
CREATE TABLE
INSERT 0 5

$ psql -c "SELECT id,name,total_tickets FROM events ORDER BY id;"
 id |         name         | total_tickets
----+----------------------+---------------
  1 | Go Conference 2026   |           100
  ...
  6 | Go Conference 2026   |           100
  7 | SRE Meetup           |            30
  8 | Cloud Native Summit  |           500
  9 | Python Workshop      |            25
 10 | Kubernetes Deep Dive |            80
(10 rows)
```

`INSERT 0 5` means every event got duplicated. `ON CONFLICT DO NOTHING` never fires because
`events.id` is a `SERIAL` primary key and there's **no unique constraint on the business key**
(`name`, `venue`, `event_date`). So every insert is a brand-new row that conflicts with nothing.
I cleaned it up:

```
$ psql -c "DELETE FROM events WHERE id > 5;" \
       -c "SELECT setval('events_id_seq', (SELECT max(id) FROM events));" \
       -c "SELECT id,name,venue,total_tickets,price_cents FROM events ORDER BY id;"
DELETE 5
 setval
--------
      5
(1 row)

 id |         name         |    venue     | total_tickets | price_cents
----+----------------------+--------------+---------------+-------------
  1 | Go Conference 2026   | Main Hall A  |           100 |        5000
  2 | SRE Meetup           | Room 204     |            30 |           0
  3 | Cloud Native Summit  | Expo Center  |           500 |       15000
  4 | Python Workshop      | Lab 301      |            25 |        2000
  5 | Kubernetes Deep Dive | Auditorium B |            80 |        8000
(5 rows)
```

Lesson learned: a seed script is only safe to re-run if the conflict target actually exists.
The ConfigMap/`initdb` route avoids this problem completely, since it runs exactly once, on an
empty data directory.

### 4.6 Verify the full stack through the cluster

**Proof of work #3: `curl` via port-forward.** I didn't just check `/health` here, I ran the
whole list → reserve → pay path, end to end, through `Service/gateway`.

```
$ kubectl port-forward svc/gateway 3080:8080 &

$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}

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

Reserve and pay, which exercises gateway → events → Postgres/Redis and gateway → payments:

```
$ curl -s -X POST http://localhost:3080/events/1/reserve \
       -H 'Content-Type: application/json' -d '{"quantity": 2}'
{
    "reservation_id": "0a86fa8c-50a8-46c8-9ce5-981bbeaa23f9",
    "event_id": 1,
    "quantity": 2,
    "total_cents": 10000,
    "expires_in_seconds": 300
}

$ curl -s -X POST http://localhost:3080/reserve/0a86fa8c-50a8-46c8-9ce5-981bbeaa23f9/pay
{
    "order_id": "0a86fa8c-50a8-46c8-9ce5-981bbeaa23f9",
    "event_id": 1,
    "quantity": 2,
    "total_cents": 10000,
    "status": "confirmed"
}

$ curl -s http://localhost:3080/events/1        # availability decremented
{
    "id": 1,
    ...
    "total_tickets": 100,
    "available": 98
}
```

And the order actually landed in Postgres, so cross-service DNS, the connection pool and the
PVC all work fine:

```
$ psql -c "SELECT id,event_id,quantity,total_cents,status FROM orders;"
                  id                  | event_id | quantity | total_cents |  status
--------------------------------------+----------+----------+-------------+-----------
 0a86fa8c-50a8-46c8-9ce5-981bbeaa23f9 |        1 |        2 |       10000 | confirmed
(1 row)
```

### 4.7 Self-healing

**Proof of work #4: pod deletion and auto-recovery.** `kubectl get pods -w`, with timestamps:

```
$ kubectl delete pod -l app=gateway
pod "gateway-587477c67-m8j9k" deleted from default namespace

$ kubectl get pods -w
00:06:50  NAME                        READY   STATUS      RESTARTS   AGE
00:06:50  events-6f9bcfd995-sq6m9     1/1     Running     0          38s
00:06:50  gateway-587477c67-m8j9k     1/1     Running     0          38s
00:06:50  payments-5b5c664bc4-2q2sh   1/1     Running     0          38s
00:06:50  postgres-67977f4df6-jqgl9   1/1     Running     0          68s
00:06:50  redis-87cf6bc6b-f5ng8       1/1     Running     0          68s
00:06:52  gateway-587477c67-m8j9k     1/1     Terminating         0   41s
00:06:52  gateway-587477c67-mn79x     0/1     Pending             0   0s
00:06:52  gateway-587477c67-mn79x     0/1     ContainerCreating   0   0s
00:06:52  gateway-587477c67-m8j9k     0/1     Completed           0   41s
00:06:53  gateway-587477c67-mn79x     1/1     Running             0   1s
00:06:53  gateway-587477c67-m8j9k     0/1     Completed           0   42s

recovery_seconds=1.266453950
```

I measured it again **after** Task 2 added a readiness probe, because the two numbers actually
mean different things:

| | Time from `delete` to `1/1 Ready` |
|---|---|
| No readiness probe (Task 1 manifests) | **1.27 s** |
| With readiness probe (Task 2 manifests) | **7.49 s** |

```
00:16:03  gateway-74c5b5d9fd-kb2t4   1/1     Terminating         0   2m47s
00:16:03  gateway-74c5b5d9fd-6wb7j   0/1     Pending             0   0s
00:16:03  gateway-74c5b5d9fd-6wb7j   0/1     ContainerCreating   0   0s
00:16:04  gateway-74c5b5d9fd-6wb7j   0/1     Running             0   1s
00:16:10  gateway-74c5b5d9fd-6wb7j   1/1     Running             0   7s

recovery_to_READY_seconds=7.493712856
```

The 1.27 s number is the flattering one, and it's **wrong** as a recovery measure. Without a
probe, the kubelet marks a container "Ready" the instant the process starts, so the Service
started routing traffic to a uvicorn that hadn't even finished importing FastAPI yet. The 7.49 s
figure is the honest one: it's the time until the pod could actually serve a request. Adding the
probe didn't make recovery slower, it just made the number truthful, and it got rid of a ~6 s
window of 502s on every restart.

### 4.8 Answer: how does this compare to docker-compose?

**How long did K8s take?** About 1.3 s to a running container, about 7.5 s to a pod genuinely
serving traffic (1 s for scheduling and container start, ~6 s for the Python app to boot and
pass readiness).

But I think the real comparison isn't the duration, it's who actually starts the clock.

In Lab 1, `docker compose stop payments` left payments down and it *stayed* down. It only came
back because I typed `docker compose start payments` myself. `app/docker-compose.yaml` declares
no `restart:` policy at all, so a crashed or stopped container is just a dead container until a
human notices and does something. So mean time to recovery was bounded by my own reaction time:
minutes if I was away from the keyboard, and basically unbounded at 3 a.m.

In Kubernetes, nothing "recovered" the pod in that sense. The ReplicaSet controller just saw
that `spec.replicas: 1` no longer matched reality and reconciled it. That's a continuous control
loop, not an event handler, so it doesn't care *why* the pod vanished (deleted, OOM-killed, node
drained, node died), and there's no way to "miss" it. The 7.5 s happens unattended, and it would
be the same at 3 a.m.

There are two more differences I noticed in the watch output:

- `docker compose restart` restarts the same container, but K8s replaces the pod outright. The
  new pod gets a new name (`...-m8j9k` → `...-mn79x`), a new IP, and a filesystem rebuilt from
  the image. Nothing from the old container survives, restarts are genuinely clean, which is
  part of why config has to live in env vars/ConfigMaps and state in a PVC.
- The Service tracked the change on its own. `Service/gateway` selects `app: gateway`, and its
  EndpointSlice got rewritten to the new pod IP without me touching a config file anywhere.
  With compose I'd have been relying on the container keeping its network alias.

Even if I added `restart: unless-stopped` to compose, Docker only restarts a container on the
host it's already on. K8s can reschedule onto any node with capacity, which is really the
difference between surviving a process crash and surviving a whole machine going down.

---

## Task 2 — Probes & Resource Limits (4 pts)

### 4.9 Probes

**Proof of work: `kubectl describe pod` showing probes configured**

```
$ kubectl describe pod -l app=gateway | grep -E 'Liveness:|Readiness:|Limits:|Requests:'
    Limits:
      cpu:     200m
      memory:  256Mi
    Requests:
      cpu:      50m
      memory:   64Mi
    Liveness:   tcp-socket :8080 delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2

$ kubectl describe pod -l app=events | grep -E 'Liveness:|Readiness:|Limits:|Requests:'
    Limits:
      cpu:     200m
      memory:  256Mi
    Requests:
      cpu:      50m
      memory:   64Mi
    Liveness:   tcp-socket :8081 delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2

$ kubectl describe pod -l app=payments | grep -E 'Liveness:|Readiness:|Limits:|Requests:'
    Limits:
      cpu:     200m
      memory:  256Mi
    Requests:
      cpu:      50m
      memory:   64Mi
    Liveness:   http-get http://:8082/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8082/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

> **I deviated from the lab text here, and the evidence for why is below.** The lab says to use
> `livenessProbe: httpGet /health` for all three services. I set it up that way first, saw what
> it did in §4.10, and then changed `gateway` and `events` over to a **TCP liveness probe**
> instead. `payments` keeps `httpGet /health` because it has no downstream dependencies, so its
> `/health` is just a self-check and is actually safe to use for liveness.
> Readiness stays `httpGet /health` everywhere, since that's exactly what readiness is for.

### 4.10 Readiness probe failure

**First attempt.** The lab's `kubectl delete pod -l app=redis` turned out to be too fast to
observe anything:

```
00:07:43  redis-87cf6bc6b-f5ng8    1/1     Terminating         0   2m2s
00:07:44  redis-87cf6bc6b-mhk6b    0/1     Pending             0   0s
00:07:44  redis-87cf6bc6b-mhk6b    0/1     ContainerCreating   0   0s
00:07:45  redis-87cf6bc6b-mhk6b    0/1     Running             0   1s
00:07:46  redis-87cf6bc6b-mhk6b    1/1     Running             0   2s
```

Redis was back in **2 s**. The events readiness probe needs 2 failures × 5 s to trip, and the
events app also caches its Redis check for 5 s (`_REDIS_CHECK_INTERVAL`), so the whole outage
was over before anything could even notice. Events stayed `1/1 Ready` the entire time. That's
actually the lesson here: self-healing can be faster than detection, and a probe tuned to
`period=5s failureThreshold=2` just can't see a 2-second blip.

So instead I held Redis down on purpose, to get an outage long enough to actually observe:

```
$ kubectl scale deployment/redis --replicas=0
deployment.apps/redis scaled

TIME       EVENTS_READY   EV_RESTART  EP_READY  GATEWAY_READY/RESTARTS
00:09:07   true           0           true      true/r=0
00:09:21   true           0           true      true/r=0
00:09:29   true           0           true      false/r=0     <- gateway readiness trips
00:09:32   false          0           false     false/r=0     <- events 0/1, removed from endpoints
00:09:45   false          0           false     false/r=0
00:09:49   false          0           false     false/r=1     <- gateway LIVENESS kills it
00:10:20   false          0           false     false/r=2
00:10:49   false          0           false     false/r=3
00:11:19   false          0           false     false/r=4
00:11:38   false          0           false     false/r=4
```

```
$ kubectl get pods
NAME                        READY   STATUS    RESTARTS      AGE
events-8886bb57d-s56lq      0/1     Running   0             4m29s
gateway-669dc5867d-24chc    0/1     Running   4 (28s ago)   4m29s
payments-8648588477-7l4lh   1/1     Running   0             4m29s
postgres-67977f4df6-jqgl9   1/1     Running   0             6m6s
```

`events` shows exactly the behaviour the lab describes: **`0/1 Ready`, `RESTARTS 0`**.

```
$ kubectl describe pod -l app=events | grep -A 3 "Readiness"
    Readiness:  http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
--
  Warning  Unhealthy  4m26s (x2 over 4m27s)  kubelet  Readiness probe failed: Get "http://10.42.0.16:8081/health": dial tcp 10.42.0.16:8081: connect: connection refused
  Warning  Unhealthy  45s (x14 over 2m5s)    kubelet  Readiness probe failed: Get "http://10.42.0.16:8081/health": context deadline exceeded (Client.Timeout exceeded while awaiting headers)
  Warning  Unhealthy  41s (x9 over 2m21s)    kubelet  Readiness probe failed: HTTP probe failed with statuscode: 503
```

The EndpointSlice for `Service/events` flipped to `ready=false`, so K8s just stopped routing to
it. No restart, no crash loop, just traffic removal. This is exactly what should happen.

**The gateway is where the lab's suggested config actually bites you.** At this point `gateway`
still had `livenessProbe: httpGet /health`, exactly as the lab specifies:

```
$ kubectl describe pod -l app=gateway | tail -14
  Warning  Unhealthy  106s (x5 over 2m26s)   kubelet  Liveness probe failed: HTTP probe failed with statuscode: 503
  Warning  Unhealthy  104s (x10 over 4m29s)  kubelet  Readiness probe failed: HTTP probe failed with statuscode: 503
  Normal   Pulled     36s (x5 over 4m36s)    kubelet  Container image "quickticket-gateway:v1" already present on machine
  Normal   Created    36s (x5 over 4m36s)    kubelet  Container created
  Normal   Started    36s (x5 over 4m36s)    kubelet  Container started
  Normal   Killing    6s (x5 over 2m6s)      kubelet  Container gateway failed liveness probe, will be restarted
```

**And a Redis outage crash-looped the gateway.** Redis goes down → events `/health` returns 503
→ gateway `/health` returns 503 → gateway liveness fails → kubelet kills the gateway. Five
restarts in two minutes, and each one wiped out the gateway's in-memory circuit-breaker and
rate-limiter state from Lab 1, without ever actually bringing Redis back. This is basically the
textbook cascading failure you get from a dependency-checking liveness probe.

Recovery is automatic once Redis comes back, at least:

```
$ kubectl scale deployment/redis --replicas=1
00:12:00   events_ready=false  ep_ready=false  gateway_ready=false
00:12:10   events_ready=true   ep_ready=true   gateway_ready=false
00:12:41   events_ready=true   ep_ready=true   gateway_ready=true
```

Then I changed the gateway's liveness probe to `tcpSocket` and re-ran the exact same outage:

```
$ kubectl describe pod -l app=gateway | grep -E "Liveness:|Readiness:"
    Liveness:   tcp-socket :8080 delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2

$ kubectl scale deployment/redis --replicas=0
TIME       EVENTS_READY   EV_RESTART  GATEWAY_READY  GW_RESTART
00:13:24   true           0           true           0
00:13:35   true           0           false          0
00:13:42   false          0           false          0
   ... same 2-minute outage ...
00:15:32   false          0           false          0
00:15:38   false          0           false          0
```

**Zero restarts on both pods, over the same two-minute outage.** Readiness still correctly
pulled both pods out of their Services, nothing got killed. Same failure detection, just without
the self-inflicted damage.

### 4.11 Resource limits

`requests` is what the scheduler reserves, `limits` is what the kernel actually enforces (CPU
throttling, OOM-kill on memory). The three app services use the lab's values, and I gave the
datastores more, sized from what they actually used.

**Proof of work: `kubectl describe node` allocated resources**

```
# The lab's command is `kubectl describe node $(kubectl get nodes -o name | head -1)`, which
# kubectl v1.35 rejects ("no need to specify a resource type ... in resource/name form").
# Drop the word `node`:

$ kubectl describe $(kubectl get nodes -o name | head -1) | grep -A 10 "Allocated resources"
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                500m (4%)   1300m (10%)
  memory             492Mi (3%)  1578Mi (10%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
```

```
$ kubectl describe $(kubectl get nodes -o name | head -1) | grep -A 14 "Non-terminated Pods"
Non-terminated Pods:          (10 in total)
  Namespace     Name                                      CPU Requests  CPU Limits  Memory Requests  Memory Limits  Age
  default       events-8886bb57d-s56lq                    50m (0%)      200m (1%)   64Mi (0%)        256Mi (1%)     8m37s
  default       gateway-74c5b5d9fd-kb2t4                  50m (0%)      200m (1%)   64Mi (0%)        256Mi (1%)     2m39s
  default       payments-8648588477-7l4lh                 50m (0%)      200m (1%)   64Mi (0%)        256Mi (1%)     8m37s
  default       postgres-67977f4df6-jqgl9                 100m (0%)     500m (4%)   128Mi (0%)       512Mi (3%)     10m
  default       redis-87cf6bc6b-bb4wv                     50m (0%)      200m (1%)   32Mi (0%)        128Mi (0%)     13s
  kube-system   coredns-8db54c48d-fb7vt                   100m (0%)     0 (0%)      70Mi (0%)        170Mi (1%)     11m
  kube-system   local-path-provisioner-5d9d9885bc-lsj9w   0 (0%)        0 (0%)      0 (0%)           0 (0%)         11m
  kube-system   metrics-server-786d997795-99vmc           100m (0%)     0 (0%)      70Mi (0%)        0 (0%)         11m
  kube-system   svclb-traefik-ccbefc57-42dz4              0 (0%)        0 (0%)      0 (0%)           0 (0%)         10m
  kube-system   traefik-9bcdbbd9-zh7wd                    0 (0%)        0 (0%)      0 (0%)           0 (0%)         10m
```

And actual consumption, to compare against the requests:

```
$ kubectl top pods
NAME                        CPU(cores)   MEMORY(bytes)
events-8886bb57d-s56lq      3m           41Mi
gateway-74c5b5d9fd-kb2t4    3m           37Mi
payments-8648588477-7l4lh   3m           35Mi
postgres-67977f4df6-jqgl9   5m           28Mi

$ kubectl top nodes
NAME                       CPU(cores)   CPU(%)   MEMORY(bytes)   MEMORY(%)
k3d-quickticket-server-0   155m         1%       984Mi           6%
```

The 64Mi request against 35-41Mi actual gives a reasonable ~1.6x headroom while idle. Limits
add up to **1578Mi against 492Mi of requests**, so the node is overcommitted 3:1 on purpose.
That's fine while everything is idle, but it means `limits` here work as a blast-radius cap, not
a capacity guarantee. If all five pods hit their ceiling at the same time, it's the node, not
the scheduler, that decides who dies.

### Answer: liveness vs readiness, and which to use for database connectivity

**What each failure does:**

| | Readiness failure | Liveness failure |
|---|---|---|
| Effect | Pod's `Ready` condition → `false`; its IP is removed from every Service EndpointSlice | Container is `SIGKILL`ed and restarted in place (`RESTARTS` increments) |
| Pod restarted? | **No** | **Yes**, with exponential backoff → `CrashLoopBackOff` |
| Reversible? | Yes, instantly: pod rejoins endpoints as soon as the probe passes | Only by restarting; in-memory state is lost each time |
| Question it answers | "Should traffic go here **right now**?" | "Is this process unrecoverable without a restart?" |

I saw both of these above: `events` went `0/1 Ready` with `RESTARTS 0` (readiness), while the
gateway with an HTTP `/health` liveness probe hit `RESTARTS 4` (liveness).

For database connectivity, I'd say readiness, pretty clearly.

1. A restart can't fix the dependency. If Postgres is down, killing the events pod just gives
   you a fresh pod that also can't reach Postgres. The restart is pure cost here: lost
   connection pool, lost warm caches, plus a `CrashLoopBackOff` backoff that delays recovery
   even *after* the DB is back up.
2. The correct response to "my DB is down" is to stop taking traffic, and that's exactly what
   readiness does. Requests get routed to healthy replicas, or fail fast at the load balancer,
   instead of hanging on a pod that's going to time out anyway.
3. A shared dependency turns liveness into a correlated, cluster-wide outage. All replicas check
   the same database, so they all fail liveness within one period and all restart at once, right
   into a thundering herd of reconnection attempts against a database that's already struggling.
   Readiness just degrades things; liveness amplifies them.
4. It inverts the blast radius. I measured this above: a *Redis* outage restarted the *gateway*,
   a service two hops away that doesn't even use Redis. Health checks that reach through to
   dependencies end up propagating failure upward through the call graph.

So the rule I applied to the manifests was: liveness should only ever test the process itself
(is the event loop accepting connections?), which is why gateway and events use `tcpSocket`.
Readiness is allowed to test dependencies, because the only thing it can do is withhold traffic,
which is the right response to a broken dependency anyway. `payments` is the exception that
proves this: its `/health` doesn't touch anything external, so it's safe on both.

And the right tool for "slow to start" is actually neither of these, it's a `startupProbe`,
which suspends liveness until the app has booted once.

---

## Bonus Task — Helm Chart (2 pts)

The chart lives in **[`k8s/chart/`](../k8s/chart/)**, as B.1 specifies.

> **The raw manifests in `k8s/*.yaml` stay the source of truth for the course.** Labs 5-12 edit
> those directly. The chart is just a parallel packaging of the same stack.

A chart nested inside `k8s/` looks like it should collide with `kubectl apply -f k8s/`, but it
doesn't, because `kubectl apply -f <dir>` is not recursive (you'd need `-R` for that). I checked
this instead of just assuming it: the apply picks up exactly the 12 objects from the five
top-level YAML files and never goes into `chart/`:

```
$ kubectl apply -f k8s/ --dry-run=server
deployment.apps/events unchanged (server dry run)
service/events unchanged (server dry run)
deployment.apps/gateway unchanged (server dry run)
service/gateway unchanged (server dry run)
deployment.apps/payments unchanged (server dry run)
service/payments unchanged (server dry run)
configmap/postgres-seed unchanged (server dry run)
persistentvolumeclaim/postgres-data unchanged (server dry run)
deployment.apps/postgres unchanged (server dry run)
service/postgres unchanged (server dry run)
deployment.apps/redis unchanged (server dry run)
service/redis unchanged (server dry run)
```

```
k8s/
├── postgres.yaml                 # the raw manifests — source of truth
├── redis.yaml
├── gateway.yaml
├── events.yaml
├── payments.yaml
└── chart/                        # the Helm chart (ignored by `kubectl apply -f k8s/`)
    ├── Chart.yaml
    ├── README.md
    ├── values.yaml
    ├── files/
    │   └── seed.sql              # copy of app/seed.sql (Helm cannot read outside the chart dir)
    └── templates/
        ├── _helpers.tpl          # shared labels, metrics annotations, probe block
        ├── events.yaml
        ├── gateway.yaml
        ├── payments.yaml
        ├── postgres.yaml
        └── redis.yaml
```

### Chart.yaml

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project — gateway, events, payments, Postgres, Redis
type: application
version: 0.1.0
appVersion: "v1"
```

### values.yaml

```yaml
# Applied to every container. `Never` matches the raw manifests, which rely on
# images pushed into the cluster with `k3d image import`.
imagePullPolicy: Never

gateway:
  replicas: 1
  image: quickticket-gateway:v1
  port: 8080
  timeoutMs: "5000"
  resources:
    requests: { cpu: 50m, memory: 64Mi }
    limits: { cpu: 200m, memory: 256Mi }

events:
  replicas: 1
  image: quickticket-events:v1
  port: 8081
  db:
    host: postgres
    port: 5432
    name: quickticket
    user: quickticket
    password: quickticket
    maxConns: "10"
  redis:
    host: redis
    port: 6379
    timeoutMs: "1000"
  reservationTtl: "300"
  resources:
    requests: { cpu: 50m, memory: 64Mi }
    limits: { cpu: 200m, memory: 256Mi }

payments:
  replicas: 1
  image: quickticket-payments:v1
  port: 8082
  # Fault-injection knobs used from Lab 1 onwards.
  failureRate: "0.0"
  latencyMs: "0"
  resources:
    requests: { cpu: 50m, memory: 64Mi }
    limits: { cpu: 200m, memory: 256Mi }

postgres:
  enabled: true
  image: postgres:17-alpine
  port: 5432
  db: quickticket
  user: quickticket
  password: quickticket
  seed: true
  persistence:
    enabled: true
    size: 1Gi
  resources:
    requests: { cpu: 100m, memory: 128Mi }
    limits: { cpu: 500m, memory: 512Mi }

redis:
  enabled: true
  image: redis:7-alpine
  port: 6379
  resources:
    requests: { cpu: 50m, memory: 32Mi }
    limits: { cpu: 200m, memory: 128Mi }

# Probe timings shared by the three application services. Liveness is a TCP
# accept, never an HTTP dependency check — see Task 2.
probes:
  enabled: true
  readiness:
    periodSeconds: 5
    failureThreshold: 2
  liveness:
    initialDelaySeconds: 10
    periodSeconds: 10
    failureThreshold: 3

# Pod annotations for a scrape-by-annotation Prometheus (Lab 7).
metrics:
  annotations: true
```

Setting `postgres.enabled: false` and `events.db.host: <rds-endpoint>` swaps the bundled
database for a managed one, which is really the main thing templating buys over raw YAML.

### B.3 Install and verify

```
$ helm lint k8s/chart/
==> Linting k8s/chart/
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
```

The chart creates the same object names as the raw manifests, so as B.3 says, the raw
deployment has to come down first:

```
$ kubectl delete -f k8s/
deployment.apps "events" deleted from default namespace
service "events" deleted from default namespace
deployment.apps "gateway" deleted from default namespace
service "gateway" deleted from default namespace
deployment.apps "payments" deleted from default namespace
service "payments" deleted from default namespace
configmap "postgres-seed" deleted from default namespace
persistentvolumeclaim "postgres-data" deleted from default namespace
deployment.apps "postgres" deleted from default namespace
service "postgres" deleted from default namespace
deployment.apps "redis" deleted from default namespace
service "redis" deleted from default namespace

$ helm install quickticket k8s/chart/
NAME: quickticket
LAST DEPLOYED: Tue Sep 22 00:25:54 2026
NAMESPACE: default
STATUS: deployed
REVISION: 1
TEST SUITE: None
```

**`helm list` showing the installed release:**

```
$ helm list
NAME         NAMESPACE  REVISION  UPDATED                                 STATUS    CHART              APP VERSION
quickticket  default    1         2026-09-22 00:25:54.175703828 +0300 MSK deployed  quickticket-0.1.0  v1
```

**`kubectl get pods` after the Helm install:**

```
$ kubectl get pods,svc
NAME                            READY   STATUS    RESTARTS   AGE
pod/events-5fcdcf7fb-46pdc      1/1     Running   0          18s
pod/gateway-766794dd97-bd78w    1/1     Running   0          18s
pod/payments-689849b7fc-n5kwg   1/1     Running   0          18s
pod/postgres-68d7c55444-z92ss   1/1     Running   0          18s
pod/redis-68587b4c6-h24hz       1/1     Running   0          18s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.240.102   <none>        8081/TCP   18s
service/gateway      ClusterIP   10.43.56.252    <none>        8080/TCP   18s
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    21m
service/payments     ClusterIP   10.43.98.252    <none>        8082/TCP   18s
service/postgres     ClusterIP   10.43.26.178    <none>        5432/TCP   18s
service/redis        ClusterIP   10.43.87.133    <none>        6379/TCP   18s
```

The pod hashes differ from the raw-manifest ones (`events-5fcdcf7fb-…` vs `events-8886bb57d-…`)
because the chart adds `app.kubernetes.io/managed-by` and `helm.sh/chart` labels to the pod
template, so it's a genuinely different PodSpec, not just a relabelled one.

It's not just Running either, the Helm release actually serves the real critical path,
including a self-seeded DB:

```
$ kubectl port-forward svc/gateway 3080:8080 &

$ curl -s http://localhost:3080/health
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}

$ # reserve + pay through the Helm-installed gateway
{
    "order_id": "fe8d29c3-2067-4732-9f6b-f60777f18116",
    "event_id": 4,
    "quantity": 2,
    "total_cents": 4000,
    "status": "confirmed"
}
```

And the values are actually wired up, not just decorative:

```
$ helm upgrade quickticket k8s/chart/ --set gateway.replicas=2 --set payments.failureRate=0.3
NAME: quickticket
STATUS: deployed
REVISION: 2

$ kubectl get deploy
NAME       READY   UP-TO-DATE   AVAILABLE   AGE
events     1/1     1            1           33s
gateway    2/2     2            2           33s
payments   1/1     1            1           33s
postgres   1/1     1            1           33s
redis      1/1     1            1           33s

$ kubectl get deploy/payments -o jsonpath='{.spec.template.spec.containers[0].env}'
[{"name":"PAYMENT_FAILURE_RATE","value":"0.3"},{"name":"PAYMENT_LATENCY_MS","value":"0"}]

$ helm history quickticket
REVISION  UPDATED                   STATUS      CHART              APP VERSION  DESCRIPTION
1         Tue Sep 22 00:25:54 2026  superseded  quickticket-0.1.0  v1           Install complete
2         Tue Sep 22 00:26:19 2026  deployed    quickticket-0.1.0  v1           Upgrade complete

$ helm rollback quickticket 1
Rollback was a success! Happy Helming!
```

Release revisions with a one-command rollback are the other thing raw `kubectl apply` just
cannot do.

I then uninstalled the release and re-applied the raw manifests, so labs 5-12 inherit the
plain-manifest deployment instead of a Helm release:

```
$ helm uninstall quickticket
release "quickticket" uninstalled

$ kubectl apply -f k8s/
deployment.apps/events created
service/events created
deployment.apps/gateway created
service/gateway created
deployment.apps/payments created
service/payments created
configmap/postgres-seed created
persistentvolumeclaim/postgres-data created
deployment.apps/postgres created
service/postgres created
deployment.apps/redis created
service/redis created

$ helm list        # empty — nothing Helm-managed left
NAME	NAMESPACE	REVISION	UPDATED	STATUS	CHART	APP VERSION
```

One thing worth recording: both `kubectl delete -f k8s/` and `helm uninstall` remove
`PersistentVolumeClaim/postgres-data`, so **the database got wiped and re-seeded from the
ConfigMap** on every reinstall. That's correct behaviour for this chart, since the PVC is part
of the release, but on a real system you'd want a `helm.sh/resource-policy: keep` annotation on
the PVC so an uninstall can't destroy customer data.

### B.4 Monitoring via Helm

```
$ helm install monitoring prometheus-community/kube-prometheus-stack \
    --set grafana.adminPassword=admin \
    --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false
NAME: monitoring
LAST DEPLOYED: Mon Sep 22 10:16:28 2026
NAMESPACE: default
STATUS: deployed
REVISION: 1

$ helm list
NAME        NAMESPACE  REVISION  UPDATED                                 STATUS    CHART                         APP VERSION
monitoring  default    1         2026-09-22 10:16:28.907249435 +0300 MSK deployed  kube-prometheus-stack-91.4.1  v0.94.0

$ kubectl get pods
NAME                                                     READY   STATUS    RESTARTS   AGE
alertmanager-monitoring-kube-prometheus-alertmanager-0   2/2     Running   0          105s
events-8886bb57d-jpngl                                   1/1     Running   0          9h
gateway-74c5b5d9fd-zlb2t                                 1/1     Running   0          9h
monitoring-grafana-668cb8b6b7-vjtzw                      3/3     Running   0          114s
monitoring-kube-prometheus-operator-86db765b7f-k67lc     1/1     Running   0          114s
monitoring-kube-state-metrics-6d8ffd8867-xk9rj           1/1     Running   0          114s
monitoring-prometheus-node-exporter-b5fb7                1/1     Running   0          114s
payments-8648588477-b9xh6                                1/1     Running   0          9h
postgres-67977f4df6-rrh44                                1/1     Running   0          9h
prometheus-monitoring-kube-prometheus-prometheus-0       2/2     Running   0          105s
redis-87cf6bc6b-kn267                                    1/1     Running   0          9h
```

**How many pods did kube-prometheus-stack create? 6.** All reached `Running`/Ready:

| Pod | Workload | Containers |
|---|---|---|
| `alertmanager-monitoring-kube-prometheus-alertmanager-0` | StatefulSet (created by the operator from an `Alertmanager` CR) | 2 |
| `prometheus-monitoring-kube-prometheus-prometheus-0` | StatefulSet (from a `Prometheus` CR) | 2 |
| `monitoring-grafana-668cb8b6b7-vjtzw` | Deployment | 3 |
| `monitoring-kube-prometheus-operator-86db765b7f-k67lc` | Deployment | 1 |
| `monitoring-kube-state-metrics-6d8ffd8867-xk9rj` | Deployment | 1 |
| `monitoring-prometheus-node-exporter-b5fb7` | DaemonSet | 1 |

Two things worth noting here. First, the two StatefulSet pods are **not** in the chart's
templates. The chart installs the Prometheus Operator plus `Prometheus`/`Alertmanager` custom
resources, and the operator is what actually creates those StatefulSets. That's why they're
~9 s younger than the other four. Second, node-exporter is a DaemonSet, so it's only 1 pod here
because this k3d cluster has a single node. On an N-node cluster it would create N+5 pods.

So that's 6 pods and about 2 GB of memory just to monitor a 5-pod application, the whole
`kube-system` plus `default` namespace count went from 10 pods to 16. Probably worth it on a
real cluster, and it's a good contrast with the single-container Prometheus from Lab 3's
compose stack.

> I uninstalled the release right afterwards (`helm uninstall monitoring`) to give the ~2 GB
> back to the later labs, which are memory-constrained on this machine. It's not installed now.
> Lab 7 brings its own, lighter in-cluster Prometheus, so nothing downstream depends on this
> release existing.

The QuickTicket pods (`events`, `gateway`, `payments`, `postgres`, `redis`, the `9h` ones above)
were untouched the whole time. The monitoring stack was installed alongside them in `default`,
not in place of them.

One leftover I checked after the uninstall: `helm uninstall` does not delete CRDs. Ten
`monitoring.coreos.com` CRDs are still registered on the cluster:

```
$ kubectl get crd -o name | grep monitoring.coreos.com
customresourcedefinition.apiextensions.k8s.io/alertmanagerconfigs.monitoring.coreos.com
customresourcedefinition.apiextensions.k8s.io/alertmanagers.monitoring.coreos.com
customresourcedefinition.apiextensions.k8s.io/podmonitors.monitoring.coreos.com
customresourcedefinition.apiextensions.k8s.io/probes.monitoring.coreos.com
customresourcedefinition.apiextensions.k8s.io/prometheusagents.monitoring.coreos.com
customresourcedefinition.apiextensions.k8s.io/prometheuses.monitoring.coreos.com
customresourcedefinition.apiextensions.k8s.io/prometheusrules.monitoring.coreos.com
customresourcedefinition.apiextensions.k8s.io/scrapeconfigs.monitoring.coreos.com
customresourcedefinition.apiextensions.k8s.io/servicemonitors.monitoring.coreos.com
customresourcedefinition.apiextensions.k8s.io/thanosrulers.monitoring.coreos.com
```

This is deliberate Helm behaviour. CRDs are cluster-scoped and shared, so deleting them on
uninstall would break any other release using them, and it would delete every CR of that kind
too. They cost nothing at rest, just API-server schema registration. It does mean a
`ServiceMonitor` can still be applied to this cluster even with no operator running to act on
it, though.

---

## Final state

The cluster is left running for labs 5-12, with the raw `k8s/` manifests deployed in `default`
and no Helm release installed:

```
$ kubectl get nodes
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   22m   v1.35.5+k3s1

$ kubectl get pods,svc
NAME                            READY   STATUS    RESTARTS   AGE
pod/events-8886bb57d-jpngl      1/1     Running   0          56s
pod/gateway-74c5b5d9fd-zlb2t    1/1     Running   0          56s
pod/payments-8648588477-b9xh6   1/1     Running   0          56s
pod/postgres-67977f4df6-rrh44   1/1     Running   0          56s
pod/redis-87cf6bc6b-kn267       1/1     Running   0          56s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.113.39    <none>        8081/TCP   56s
service/gateway      ClusterIP   10.43.146.68    <none>        8080/TCP   56s
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    22m
service/payments     ClusterIP   10.43.160.163   <none>        8082/TCP   56s
service/postgres     ClusterIP   10.43.252.63    <none>        5432/TCP   56s
service/redis        ClusterIP   10.43.244.237   <none>        6379/TCP   56s

$ psql -c "SELECT count(*) FROM events;" -c "SELECT count(*) FROM orders;"
 events
--------
      5
 orders
--------
      1
```

The pods are young because the bonus task cycled the deployment (`kubectl delete -f k8s/` →
`helm install` → `helm uninstall` → `kubectl apply -f k8s/`), which also wiped and re-seeded the
PVC. That's why there's 1 order instead of the earlier 2, it's the purchase I made to verify the
restored deployment. Five seeded events and a real confirmed order, so this is live state, not
just a fresh empty database.

I re-verified the critical path after the restore:

```
$ curl -s http://localhost:3080/health
{
    "status": "healthy",
    "checks": {"events": "ok", "payments": "ok", "circuit_payments": "CLOSED"}
}

$ # reserve + pay on the restored raw-manifest deployment
{
    "order_id": "a2a77d4d-6588-4556-a62d-e072460b9703",
    "event_id": 1,
    "quantity": 2,
    "total_cents": 10000,
    "status": "confirmed"
}

$ kubectl diff -f k8s/
(no diff — committed manifests == live cluster)
```

---

## What changed vs docker-compose, in one table

| Compose concept | Kubernetes equivalent used here |
|---|---|
| `build: ./gateway` | pre-built image + `k3d image import` + `imagePullPolicy: Never` |
| `ports: "3080:8080"` | `Service` ClusterIP + `kubectl port-forward` (no Ingress yet) |
| `environment:` | `env:` list on the container |
| service name as hostname | `Service` name as cluster DNS (`postgres`, `redis`, `events`, `payments`) |
| `volumes: postgres_data:` | `PersistentVolumeClaim` on the `local-path` StorageClass |
| `./seed.sql:/docker-entrypoint-initdb.d/...` | `ConfigMap` mounted at the same path |
| `healthcheck:` | `readinessProbe` (traffic gating) + `livenessProbe` (restart): two separate ideas compose conflates into one |
| `depends_on: condition: service_healthy` | **no equivalent**, replaced by readiness probes and retry-on-failure |
| manual `docker compose start` | ReplicaSet controller reconciliation |

---

## PR description

```text
- [x] Task 1 done — K8s manifests written, QuickTicket deployed to k3d
- [x] Task 2 done — probes and resource limits added
- [x] Bonus Task done — Helm chart created
```
