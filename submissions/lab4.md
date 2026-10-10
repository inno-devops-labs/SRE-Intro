# Lab 4. Kubernetes: deploy QuickTicket to a cluster

**Student:** Kirill Fadeev
**Email:** ki.fadeev@innopolis.university
**Environment:** WSL2 (kernel 6.18.33.2-microsoft-standard-WSL2, x86_64), Docker Engine 29.7.2, Docker Compose v5.5.1, k3d v5.9.0, k3s v1.35.5+k3s1 with containerd 2.2.3-k3s1, kubectl v1.37.0, Helm v3.22.0, kube-prometheus-stack 91.4.1

Every number below comes from a live run on that cluster. Task 1 was captured in one pass on a freshly created cluster, Task 2 in a second pass on the same cluster, and the bonus in a third. Timings to a hundredth of a second come from client side stamps on `kubectl get pods -w` and from a polling client that runs inside the cluster, so that Service routing is part of what is measured.

---

## Task 1. Write manifests and deploy to k3d

### 4.0 What docker compose does when a container dies

The point of this lab is self-healing, so I measured the behaviour it replaces first, on the same machine and the same images.

```bash
docker compose -f docker-compose.yaml kill gateway
```

```plaintext
restart policy in compose file: 0 lines
restart policy of app-gateway-1: no
T_KILL 20:07:29.193
 Container app-gateway-1 Killed
t+1  s  state=exited      http=000
t+5  s  state=exited      http=000
t+10 s  state=exited      http=000
t+20 s  state=exited      http=000
t+30 s  state=exited      http=000
nothing restarted it. Manual recovery:
T_START 20:07:59.827
compose gateway 200 again 1.44 s after 'compose start' (and 32.10 s after the kill, most of it waiting for a human)
```

The container itself comes back in 1.44 seconds. The other 30.66 seconds are the interval between the failure and a human typing `docker compose start`, and in this capture that interval is short only because the script was the human.

### 4.1 The cluster

```bash
k3d cluster create quickticket --wait --timeout 180s --kubeconfig-update-default=false
kubectl get nodes -o wide
```

```plaintext
cluster created in 17.88 s

NAME                       STATUS   ROLES           AGE   VERSION        INTERNAL-IP   CONTAINER-RUNTIME
k3d-quickticket-server-0   Ready    control-plane   10s   v1.35.5+k3s1   172.18.0.3    containerd://2.2.3-k3s1
```

One node, `Ready`, ten seconds after the cluster was created.

### 4.2 Building and importing the images

```bash
docker build -t quickticket-gateway:v1 ./app/gateway     # and events, payments
k3d image import quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 \
    postgres:17-alpine redis:7-alpine -c quickticket
```

```plaintext
k3d image import rc=0:
INFO[0023] Successfully imported image(s)
INFO[0023] Successfully imported 5 image(s) into 1 cluster(s)
k3d reported success, but k3d-quickticket-server-0 has none of: quickticket-gateway:v1
quickticket-events:v1 quickticket-payments:v1 postgres:17-alpine redis:7-alpine
```

The import reports success with exit code 0 and imports nothing. I found this because the capture script checks the result on the node instead of trusting the tool, and the earlier run of this lab spent six minutes in `rollout status` before showing `ErrImageNeverPull` on all three application pods. The workaround skips the k3d tools container and writes into the node's containerd directly:

```bash
docker save --platform linux/amd64 quickticket-gateway:v1 \
  | docker exec -i k3d-quickticket-server-0 ctr -n k8s.io images import --platform linux/amd64 -
```

```plaintext
  -> quickticket-gateway:v1 now present
  -> quickticket-events:v1 now present
  -> quickticket-payments:v1 now present
  -> postgres:17-alpine now present
  -> redis:7-alpine now present
all 5 images verified on k3d-quickticket-server-0
import took 53.78 s

--- images as the node's containerd sees them ---
docker.io/library/postgres:17-alpine
docker.io/library/quickticket-events:v1
docker.io/library/quickticket-gateway:v1
docker.io/library/quickticket-payments:v1
docker.io/library/redis:7-alpine
```

> With Docker 29 and the containerd image store, `docker save` produces an OCI index that contains another index: one manifest for linux/amd64 and an attestation manifest whose platform is `unknown/unknown`. `ctr images import` handles that archive, the transfer through the k3d tools container does not, and the failure is silent because k3d checks that its own copy step returned rather than that the node gained an image. The two policies in the manifests then diverge: `imagePullPolicy: Never` on the application turns the empty node into `ErrImageNeverPull`, while `IfNotPresent` on postgres and redis quietly pulls them from Docker Hub instead, so half the cluster comes up and the deployment looks like an application problem.

### 4.3 PostgreSQL and Redis

`k8s/postgres.yaml` and `k8s/redis.yaml` carry a Deployment and a ClusterIP Service each, with the credentials from `docker-compose.yaml` passed as `env`.

```plaintext
deployment "postgres" successfully rolled out
deployment "redis" successfully rolled out
postgres accepts TCP 1.52 s after rollout reported success

NAME                      READY   STATUS    RESTARTS   AGE
postgres-7c7ffc4b-pnfg9   1/1     Running   0          2s
redis-c46d5dffc-hzv7b     1/1     Running   0          2s

NAME         TYPE        CLUSTER-IP      PORT(S)    AGE
postgres     ClusterIP   10.43.145.234   5432/TCP   3s
redis        ClusterIP   10.43.192.33    6379/TCP   3s
```

`Running` and rolled out is not the same as accepting connections here. The postgres entrypoint starts a temporary server that listens on a unix socket only while it runs the init scripts, so the capture waits for `pg_isready -h 127.0.0.1` rather than for the rollout, and that took another 1.52 seconds.

### 4.5 Seeding the database

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql
```

```plaintext
CREATE TABLE
CREATE TABLE
INSERT 0 5
 id |         name         | total_tickets
----+----------------------+---------------
  1 | Go Conference 2026   |           100
  2 | SRE Meetup           |            30
  3 | Cloud Native Summit  |           500
  4 | Python Workshop      |            25
  5 | Kubernetes Deep Dive |            80
```

### 4.4 The three services

```bash
kubectl apply -f k8s/
kubectl get pods,svc -o wide
```

```plaintext
NAME                           READY   STATUS    RESTARTS   AGE   IP           NODE
pod/events-6c4df7d6-rf7tt      1/1     Running   0          7s    10.42.0.11   k3d-quickticket-server-0
pod/gateway-6fc44f68c5-5fn4m   1/1     Running   0          7s    10.42.0.12   k3d-quickticket-server-0
pod/payments-58fb468db-bq5cc   1/1     Running   0          7s    10.42.0.13   k3d-quickticket-server-0
pod/postgres-7c7ffc4b-pnfg9    1/1     Running   0          10s   10.42.0.9    k3d-quickticket-server-0
pod/redis-c46d5dffc-hzv7b      1/1     Running   0          10s   10.42.0.10   k3d-quickticket-server-0

NAME                 TYPE        CLUSTER-IP      PORT(S)    SELECTOR
service/events       ClusterIP   10.43.182.230   8081/TCP   app=events
service/gateway      ClusterIP   10.43.148.168   8080/TCP   app=gateway
service/payments     ClusterIP   10.43.248.38    8082/TCP   app=payments
service/postgres     ClusterIP   10.43.145.234   5432/TCP   app=postgres
service/redis        ClusterIP   10.43.192.33    6379/TCP   app=redis
```

Events connected to both dependencies on the first try, because the databases were applied first:

```plaintext
{"time":"2026-09-17 17:09:33,218","level":"INFO","service":"events","msg":"DB pool created (max=10)"}
{"time":"2026-09-17 17:09:33,225","level":"INFO","service":"events","msg":"Redis connected"}
```

### 4.6 The full path through a port-forward

```bash
kubectl port-forward svc/gateway 3080:8080 &
curl -s http://localhost:3080/events | python3 -m json.tool
```

```plaintext
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
    ... (four more events, 25 to 500 tickets each, all fully available)
]
```

```plaintext
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

A read proves the gateway to events to postgres path. A purchase proves the rest of it, including redis and payments:

```plaintext
reserve: {"reservation_id":"56649ca8-3438-4ac3-8134-ac879d18078c","event_id":1,"quantity":2,
          "total_cents":10000,"expires_in_seconds":300}
pay:     {"order_id":"56649ca8-3438-4ac3-8134-ac879d18078c","event_id":1,"quantity":2,
          "total_cents":10000,"status":"confirmed"}

 event_id | quantity | payment_ref  |  status
----------+----------+--------------+-----------
        1 |        2 | PAY-5C3EBCF1 | confirmed
```

### 4.7 Self-healing

The pod is deleted while two observers run: `kubectl get pods -w` with client side timestamps, and a python client inside the payments pod that requests `http://gateway:8080/health` five times a second, so the measurement goes through the Service the way real traffic does.

```bash
kubectl delete pod -l app=gateway
```

```plaintext
T_DELETE      20:09:42.993
T_DELETE_DONE 20:09:44.287   (kubectl delete returned after 1.28 s)
T_READY       20:09:44.634   (new pod Ready 1.64 s after the delete)
```

```plaintext
20:09:40.066 gateway-6fc44f68c5-5fn4m   1/1     Running             0     9s
20:09:43.094 gateway-6fc44f68c5-5fn4m   1/1     Terminating         0     12s
20:09:43.108 gateway-6fc44f68c5-76hgn   0/1     Pending             0     0s
20:09:43.133 gateway-6fc44f68c5-76hgn   0/1     ContainerCreating   0     0s
20:09:43.869 gateway-6fc44f68c5-5fn4m   0/1     Completed           0     12s
20:09:44.254 gateway-6fc44f68c5-76hgn   1/1     Running             0     1s
```

```plaintext
--- in-cluster client, GET http://gateway:8080/health every 0.2 s, transitions only ---
17:09:40.171 probe#1  -> 200
17:09:43.324 probe#16 -> URLError:[Errno 111] Connection refused
17:09:45.929 probe#19 -> URLError:timed out
17:09:46.161 probe#20 -> 200
```

**How long did Kubernetes take to recreate the deleted pod, and how does this compare with a docker-compose restart?**

The replacement pod appeared 0.12 seconds after the delete, reached `Running` at 1.26 seconds and `Ready` at 1.64 seconds, with no human involved. Clients saw 2.84 seconds without service. Compose needed 32.10 seconds for the same failure, of which 1.44 seconds was the container start and the rest was waiting for somebody to notice.

The ratio between those two numbers says less than where each one comes from. Compose recovery is bounded by how fast somebody notices, so it measures the team rather than the system, and at four in the morning it is measured in minutes. The deployment controller sees `replicas: 1` against zero live pods and creates one, which takes the same 1.6 seconds at any hour. The failure here is also the friendly kind: a deliberate delete, a scheduler with a free node, and the image already on it.

> Without a readiness probe, the replacement pod was marked `Ready` 0.38 seconds after its container started, and it was added to the Service at that moment, while uvicorn was still binding its port. The in-cluster client got `Connection refused` and then a timeout for 2.84 seconds, which is 1.7 times longer than the 1.64 seconds the API reported as the recovery. Without a probe, `Ready` only means that the kubelet started a process.

---

## Task 2. Probes and resource limits

### 4.9 The probes

Applying the updated manifests replaced every pod, and `kubectl describe` confirms what the kubelet is running:

```plaintext
$ kubectl describe pod -l app=gateway | grep -A 5 "Liveness\|Readiness"
    Liveness:   http-get http://:8080/metrics delay=10s timeout=1s period=10s successThreshold=1 failureThreshold=3
    Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s successThreshold=1 failureThreshold=2

$ kubectl describe pod -l app=events | grep -A 5 "Liveness\|Readiness"
    Liveness:   http-get http://:8081/metrics delay=10s timeout=1s period=10s successThreshold=1 failureThreshold=3
    Readiness:  http-get http://:8081/health delay=0s timeout=1s period=5s successThreshold=1 failureThreshold=2

$ kubectl describe pod -l app=payments | grep -A 5 "Liveness\|Readiness"
    Liveness:   http-get http://:8082/health delay=10s timeout=1s period=10s successThreshold=1 failureThreshold=3
    Readiness:  http-get http://:8082/health delay=0s timeout=1s period=5s successThreshold=1 failureThreshold=2
```

This differs from the lab text, which puts liveness on `/health` for all three services. Section 4.10c below runs the lab's version as a control experiment and measures what it costs; payments keeps `/health` on both probes because it has no dependencies, so for that service the two questions are the same question.

Two more departures, both visible in the capture:

**Start order.** Kubernetes has no `depends_on`, and `events/main.py` connects once at startup: if Redis is unreachable then, `redis_client` stays `None` for the life of the process and no later recovery brings it back. The manifest solves this with init containers that use images the cluster already has, so nothing new is pulled:

```plaintext
  wait-for-postgres:
    State:          Terminated
      Reason:       Completed
      Exit Code:    0
  wait-for-redis:
    State:          Terminated
      Reason:       Completed
      Exit Code:    0
postgres:5432 - accepting connections
```

**The seed does not survive.** The postgres Deployment has no volume, so changing the pod template throws the data away:

```plaintext
seed survived the rollout? (postgres pod was replaced, no volume)
ERROR:  relation "events" does not exist
LINE 1: select count(*) from events
re-seeding the new postgres pod
5
```

### 4.7 again: the same delete, now with a readiness probe

```plaintext
T_DELETE      20:10:44.347
T_DELETE_DONE 20:10:45.488   (kubectl delete returned after 1.15 s)
T_READY       20:10:51.588   (new pod Ready 7.25 s after the delete)

20:10:44.544 gateway-6fb7cf8bbc-k8x9v   0/1     ContainerCreating   0   0s
20:10:45.448 gateway-6fb7cf8bbc-k8x9v   0/1     Running             0   1s
20:10:51.556 gateway-6fb7cf8bbc-k8x9v   1/1     Running             0   7s
```

```plaintext
17:10:44.698 probe#15 -> URLError:[Errno 111] Connection refused
17:10:52.177 probe#27 -> 200
```

| | Task 1, no probes | Task 2, readiness probe |
|---|---:|---:|
| pod reported `Ready` | 1.64 s | 7.25 s |
| clients without service | 2.84 s | 7.48 s |
| gap between the two | 1.20 s | 0.23 s |

The probe made the recovery look four times slower and made the reported number honest. The extra six seconds are the probe's own granularity: the container was running at 1.1 seconds, the first probe found nothing listening, and the next one came a full `periodSeconds: 5` later. With a single replica this is pure added downtime. With two or more replicas it is the opposite, because the Service keeps sending traffic to the healthy pod instead of a socket that refuses connections.

### 4.10a Deleting the Redis pod, exactly as the lab describes it

```bash
kubectl delete pod -l app=redis
```

```plaintext
20:11:24.733 redis-6cf6c6f989-ncj22   1/1   Terminating         0   62s
20:11:24.963 redis-6cf6c6f989-6kdrg   0/1   Pending             0   0s
20:11:26.583 redis-6cf6c6f989-6kdrg   1/1   Running             0   2s

time         events_ready/restarts  gateway_ready/restarts  events_endpoints  gateway_endpoints
20:11:27.606 true/0                 true/0                  ready=true        ready=true
20:11:30.013 true/0                 true/0                  ready=true        ready=true
...                                                         (12 samples over 30 s, all identical)
20:11:55.175 true/0                 true/0                  ready=true        ready=true
```

The expected `0/1 Ready` never appeared. Redis was back 1.96 seconds after the delete, and readiness needs two consecutive failures at `periodSeconds: 5`, so ten seconds of failure, which is five times longer than the outage this experiment produces. The experiment is sound, the dependency just never stays away long enough to be noticed.

### 4.10b The same dependency, held down for 80 seconds

```bash
kubectl scale deploy/redis --replicas=0    # 80.76 s later: --replicas=1
```

```plaintext
T_REDIS_DOWN 20:12:00.981
time         events_ready/restarts  gateway_ready/restarts  events_endpoints  gateway_endpoints
20:12:02.558 true/0                 true/0                  ready=true        ready=true
20:12:07.677 true/0                 false/0                 ready=true        ready=false   <- gateway out
20:12:17.730 false/0                false/0                 ready=false       ready=false   <- events out
...
20:13:15.904 false/0                false/0                 ready=false       ready=false
T_REDIS_UP   20:13:21.746

--- pod watch (client-side timestamps), events/gateway/redis rows ---
20:12:07.474 gateway-6fb7cf8bbc-k8x9v   0/1     Running             0   83s
20:12:16.377 events-777d4f49ff-h64v2    0/1     Running             0   114s
20:13:23.855 redis-6cf6c6f989-tq6xf     0/1     Running             0   1s
20:13:23.880 redis-6cf6c6f989-tq6xf     1/1     Running             0   1s
20:13:26.376 events-777d4f49ff-h64v2    1/1     Running             0   3m4s
20:13:27.461 gateway-6fb7cf8bbc-k8x9v   1/1     Running             0   2m43s
```

```plaintext
--- in-cluster client, GET http://gateway:8080/events every 0.5 s, transitions only ---
17:12:07.518 probe#19  -> URLError:[Errno 111] Connection refused
17:13:27.648 probe#104 -> 200

--- restart counts after the experiment ---
NAME                       READY   RESTARTS   LAST_EXIT
events-777d4f49ff-h64v2    true    0          <none>
gateway-6fb7cf8bbc-k8x9v   true    0          <none>
```

The Redis outage lasted 80.77 seconds, clients were refused for 80.13 seconds of it, and service returned 5.90 seconds after Redis was scaled back. Nothing restarted.

The kubelet recorded two different failure modes on the events probe during this outage, and the difference explains the ordering above:

```plaintext
Warning  Unhealthy  6s (x8 over 56s)     kubelet  spec.containers{events}: Readiness probe failed:
    Get "http://10.42.0.15:8081/health": context deadline exceeded (Client.Timeout exceeded while awaiting headers)
Warning  Unhealthy  2s (x8 over 2m42s)   kubelet  spec.containers{events}: Readiness probe failed:
    HTTP probe failed with statuscode: 503
```

The same call made from inside the events pod did not return at all within its three second timeout, which is why the capture shows a traceback there instead of a status code. In 4.10c below, after the pod had been restarted, the same call answered `503` immediately.

> Gateway was removed from its Service ten seconds before events was removed from its own, although Redis is the dependency of events and gateway never touches it. The reason is in the two timeouts. Gateway's `/health` calls events with a two second client timeout, and events, whose Redis connection was established before the outage and now waits on a dead socket, answers slower than that, so gateway fails its probe on the first attempt after the check goes slow. Events, meanwhile, needs two consecutive failures of its own one second probe. A health endpoint that fans out to dependencies makes the caller fail earlier than the service that actually owns the problem, and an operator reading the dashboard sees the gateway named as the broken component.

### 4.10c The lab's probe configuration on the same outage

```bash
kubectl patch deploy gateway --type=json -p='[{"op":"replace",
  "path":"/spec/template/spec/containers/0/livenessProbe/httpGet/path","value":"/health"}]'
kubectl patch deploy events  ...same...
```

```plaintext
NAME      LIVENESS   READINESS
gateway   /health    /health
events    /health    /health

T_REDIS_DOWN 20:15:01.295
time         events_ready/restarts  gateway_ready/restarts
20:15:23.194 true/0     false/0    <- gateway not ready, t+21.9 s
20:15:25.791 false/0    false/0    <- events not ready,  t+24.5 s
20:15:48.007 false/0    false/1    <- gateway restarted, t+46.7 s
20:15:58.339 false/1    false/1    <- events restarted,  t+57.0 s
T_REDIS_UP   20:16:18.661
20:16:25.849 false/1    false/2    <- gateway restarted again, 7 s AFTER redis is back
20:16:36.119 false/2    false/2    <- events restarted again, 17 s after redis is back
20:16:43.757 true/2     false/2
20:16:48.786 true/2     true/2
```

```plaintext
  Normal   Killing    34s               kubelet   spec.containers{events}: Container events failed liveness probe, will be restarted
  Warning  Unhealthy  4s (x4 over 54s)  kubelet   spec.containers{events}: Liveness probe failed: HTTP probe failed with statuscode: 503

$ kubectl exec deploy/events -- python -c "...urlopen('http://127.0.0.1:8081/health')..."
503 {"status":"degraded","checks":{"postgres":"ok","redis":"down"}}
```

| | 4.10b, liveness on `/metrics` | 4.10c, liveness on `/health` |
|---|---:|---:|
| Redis kept down | 80.77 s | 77.37 s |
| clients without service | 80.13 s | 85.68 s |
| service back after Redis returned | 5.90 s | 28.64 s |
| restarts, gateway and events | 0 and 0 | 2 and 2 |

> Pointing liveness at `/health` turns a dependency outage into a restart loop of two services that were working correctly, and it charges for it after the incident rather than during it. Both restarted a second time seven and seventeen seconds after Redis came back, because a restart that begins while the dependency is still missing produces a fresh container that immediately fails the same check. The 22.7 extra seconds of downtime are the price of two kills plus one cold start of each service, paid at exactly the moment the system was about to recover on its own.

**What is the difference between a liveness failure and a readiness failure, and which one should check database connectivity?**

A readiness failure removes the pod from the endpoints of its Service and leaves the process alone, so traffic stops arriving and the pod rejoins as soon as the check passes again. A liveness failure kills the container and the kubelet starts a new one, with a backoff that grows if it keeps happening.

Database connectivity belongs to readiness. Restarting an application does not start a database, so a liveness probe that checks a dependency converts one outage into two, as the 22.7 extra seconds above show. The asymmetry is in what each probe can fix: a restart repairs state inside the process such as a deadlock or an exhausted event loop, and nothing else, so liveness should only ask questions whose answer a restart can change. That is why liveness here points at `/metrics`, which touches no dependency and confirms that the process still serves HTTP.

There is one detail in this application that argues the other way, and it is worth naming because it is a code smell rather than a counterexample. `events` creates its Redis client once, at startup, and never rebuilds it, so a pod that starts while Redis is down stays broken forever by design. In that specific case a restart is the only repair available, which is exactly why the manifest adds init containers: they make the start order deterministic so that the broken state cannot appear, and then liveness no longer has to work around the application's own lifecycle.

### 4.11 Resource requests and limits

Every container asks for 50m CPU and 64Mi of memory and is capped at 200m and 256Mi.

```plaintext
NAME                       CPU_REQ   CPU_LIM   MEM_REQ   MEM_LIM   QOS
events-7697479fbb-njrtf    50m       200m      64Mi      256Mi     Burstable
gateway-6fb7cf8bbc-t4tpd   50m       200m      64Mi      256Mi     Burstable
payments-d7dc94485-qj268   50m       200m      64Mi      256Mi     Burstable
postgres-fdf49567-dl2hl    50m       200m      64Mi      256Mi     Burstable
redis-6cf6c6f989-fm7qz     50m       200m      64Mi      256Mi     Burstable
```

The limits reach the kernel, and the kernel has already enforced them. `cpu.max` reads `20000 100000`, which is 20 ms of CPU per 100 ms period and matches the 200m limit, and `memory.max` is 268435456 bytes, which is 256Mi:

```bash
kubectl exec deploy/gateway -- sh -c 'cat /sys/fs/cgroup/cpu.max /sys/fs/cgroup/memory.max; \
  grep -E "nr_throttled|throttled_usec" /sys/fs/cgroup/cpu.stat'
```

```plaintext
20000 100000
268435456
nr_throttled 30
throttled_usec 2271582
```

```bash
kubectl describe $(kubectl get nodes -o name | head -1) | grep -A 10 "Allocated resources"
```

```plaintext
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (3%)   1 (8%)
  memory             660Mi (4%)  1450Mi (9%)

Capacity:    cpu: 12   memory: 16263812Ki   pods: 110
```

The node carries 16 pods at that moment: the five QuickTicket pods at 50m and 64Mi each, five k3s system pods, and the six pods of the bonus monitoring stack. Their contributions are not comparable:

```plaintext
Non-terminated Pods:          (16 in total)
  default      events-55dff6998f-b9h6b      50m (0%)   200m (1%)   64Mi (0%)   256Mi (1%)
  default      gateway-6fb7cf8bbc-jjg7j     50m (0%)   200m (1%)   64Mi (0%)   256Mi (1%)
  default      payments-d7dc94485-2wc58     50m (0%)   200m (1%)   64Mi (0%)   256Mi (1%)
  default      postgres-7667ddb566-sfkkv    50m (0%)   200m (1%)   64Mi (0%)   256Mi (1%)
  default      redis-6cf6c6f989-gz5bm       50m (0%)   200m (1%)   64Mi (0%)   256Mi (1%)
  kube-system  coredns-8db54c48d-k2jvf     100m (0%)     0 (0%)    70Mi (0%)   170Mi (1%)
  kube-system  metrics-server-786d997795-zp7fb                         100m (0%)   0 (0%)   70Mi (0%)    0 (0%)
  ...          (three more kube-system pods, all with no requests)
  monitoring   monitoring-grafana-668cb8b6b7-5hq5j                        0 (0%)   0 (0%)    0 (0%)      0 (0%)
  monitoring   prometheus-monitoring-kube-prometheus-prometheus-0         0 (0%)   0 (0%)    0 (0%)      0 (0%)
  monitoring   alertmanager-monitoring-kube-prometheus-alertmanager-0     0 (0%)   0 (0%)  200Mi (1%)    0 (0%)
```

```plaintext
$ kubectl top node
NAME                       CPU(cores)   CPU(%)   MEMORY(bytes)   MEMORY(%)
k3d-quickticket-server-0   249m         2%       2553Mi          16%
```


> The scheduler believes this node is 4% full while it is using 16% of its memory, and the difference is entirely the monitoring stack. Five of its six pods declare no requests at all, so 850Mi of real usage is invisible to scheduling decisions, while the five QuickTicket pods that declare 64Mi each are fully accounted for. A request is a promise to the scheduler rather than a measurement of anything, so a workload that promises nothing gets scheduled as if it used nothing, and the next pod lands on a node that looks emptier than it is. Limits show the other half of the same gap, since the 1450Mi of limits could not all be honoured at once, which is what the `may be over 100 percent` line says out loud.

The limits also bite quietly. The gateway pod from Task 2 recorded 30 throttled periods and 2.27 seconds of lost CPU, and the pod installed later by Helm recorded 25 periods and 0.026 seconds after 25 idle minutes, so nearly all of it happens while the process starts. Nothing was OOM killed and nothing appeared in the logs: a container that hits its CPU limit does not fail, it just takes longer, and that time surfaces as latency measured somewhere else.

---

## Bonus task. Helm chart

### B.1 and B.2 The chart

```plaintext
k8s/chart/Chart.yaml
k8s/chart/values.yaml
k8s/chart/files/seed.sql
k8s/chart/templates/_helpers.tpl
k8s/chart/templates/events.yaml
k8s/chart/templates/gateway.yaml
k8s/chart/templates/payments.yaml
k8s/chart/templates/postgres.yaml
k8s/chart/templates/redis.yaml
```

`Chart.yaml`:

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
type: application
version: 0.1.0
appVersion: "v1"
```

`values.yaml` holds everything that differs between environments, including the two fault injection knobs, the image pull policy for locally imported images, and one shared resources block:

```yaml
gateway:
  replicas: 1
  image: quickticket-gateway:v1
  timeoutMs: "5000"
events:
  replicas: 1
  image: quickticket-events:v1
  maxConns: "10"
  reservationTtl: "300"
  db: {host: postgres, port: 5432, name: quickticket, user: quickticket, password: quickticket}
  redis: {host: redis, port: 6379, timeoutMs: "1000"}
payments:
  replicas: 1
  image: quickticket-payments:v1
  failureRate: "0.0"
  latencyMs: "0"
postgres:
  image: postgres:17-alpine
  seed: true          # load app/seed.sql through docker-entrypoint-initdb.d
redis:
  image: redis:7-alpine
localImagePullPolicy: Never
resources:
  requests: {cpu: 50m, memory: 64Mi}
  limits:   {cpu: 200m, memory: 256Mi}
```

```plaintext
$ helm lint k8s/chart
==> Linting k8s/chart
[INFO] Chart.yaml: icon is recommended
1 chart(s) linted, 0 chart(s) failed

rendered objects
      1 kind: ConfigMap    name: postgres-seed
      1 kind: Deployment   name: events
      1 kind: Deployment   name: gateway
      1 kind: Deployment   name: payments
      1 kind: Deployment   name: postgres
      1 kind: Deployment   name: redis
      1 kind: Service      name: events
      1 kind: Service      name: gateway
      1 kind: Service      name: payments
      1 kind: Service      name: postgres
      1 kind: Service      name: redis
```

The ConfigMap is the one object that has no counterpart in `k8s/`. It carries `seed.sql` into `/docker-entrypoint-initdb.d`, the way the compose file mounts it, so a fresh install seeds itself and step 4.5 disappears.

### B.3 Installing the release

```bash
kubectl delete -f k8s/ --wait=true
helm install quickticket k8s/chart/ --wait --timeout 5m
```

```plaintext
NAME: quickticket
LAST DEPLOYED: Thu Sep 17 20:18:00 2026
STATUS: deployed
REVISION: 1
helm install --wait returned after 28.94 s

$ helm list
NAME       	NAMESPACE	REVISION	STATUS  	CHART            	APP VERSION
quickticket	default  	1       	deployed	quickticket-0.1.0	v1

$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
events-55dff6998f-b9h6b     1/1     Running   0          30s
gateway-6fb7cf8bbc-jjg7j    1/1     Running   0          30s
payments-d7dc94485-h62bv    1/1     Running   0          30s
postgres-7667ddb566-sfkkv   1/1     Running   0          30s
redis-6cf6c6f989-gz5bm      1/1     Running   0          30s
```

A Helm install starts everything at once, which is the case the init containers were written for, and this run is where they earned their place:

```plaintext
waiting for postgres
postgres:5432 - no response
waiting for postgres
postgres:5432 - accepting connections
{"time":"2026-09-17 17:18:14,994","level":"INFO","service":"events","msg":"DB pool created (max=10)"}

/usr/local/bin/docker-entrypoint.sh: running /docker-entrypoint-initdb.d/01-seed.sql
```

The catalogue answers through the port-forward with no manual seeding step:

```plaintext
$ curl -s localhost:3080/events
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
    ...
]
```

Values are worth having only if they change behaviour, so I moved one and put it back:

```plaintext
$ helm upgrade quickticket k8s/chart/ --set payments.failureRate=0.5 --wait
REVISION: 2
{"status":"healthy","failure_rate":0.5,"latency_ms":0}

$ helm rollback quickticket 1 --wait
Rollback was a success! Happy Helming!
{"status":"healthy","failure_rate":0.0,"latency_ms":0}

REVISION	UPDATED                 	STATUS    	DESCRIPTION
1       	Thu Sep 17 20:18:00 2026	superseded	Install complete
2       	Thu Sep 17 20:18:31 2026	superseded	Upgrade complete
3       	Thu Sep 17 20:18:43 2026	deployed  	Rollback to 1
```

> A rollback is a third revision rather than a return to the first one. Helm keeps the history append only, so revision 3 contains the same manifests as revision 1 and the release can be traced forward without guessing, which is the property that makes `helm rollback` safe to run during an incident. The chart-to-cluster path is also where `--wait` earns its keep: it returned after 28.94 seconds because it waits for the pods that the init containers deliberately hold back.

### B.4 The monitoring stack

The first attempt failed in a way that had nothing to do with Helm:

```plaintext
Error: INSTALLATION FAILED: Get "https://github.com/prometheus-community/helm-charts/releases/
download/kube-prometheus-stack-91.4.1/kube-prometheus-stack-91.4.1.tgz": dial tcp: lookup
github.com on 10.255.255.254:53: read udp 10.255.255.254:53604->10.255.255.254:53: i/o timeout
```

The repository index came from `prometheus-community.github.io` a second earlier, and the chart archive itself lives in GitHub releases, so a single DNS timeout in WSL was enough to stop it. The retry downloads the chart with `helm pull` first and installs from the local file:

```bash
helm pull prometheus-community/kube-prometheus-stack --destination /tmp
helm install monitoring /tmp/kube-prometheus-stack-91.4.1.tgz \
  --namespace monitoring --create-namespace \
  --set grafana.adminPassword=admin \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false --wait --timeout 15m
```

```plaintext
helm install monitoring rc=0 after 136.19 s

NAME        NAMESPACE   REVISION  STATUS    CHART                          APP VERSION
monitoring  monitoring  1         deployed  kube-prometheus-stack-91.4.1   v0.94.0
quickticket default     3         deployed  quickticket-0.1.0              v1

NAME                                                     READY   STATUS    RESTARTS   AGE
alertmanager-monitoring-kube-prometheus-alertmanager-0   2/2     Running   0          104s
monitoring-grafana-668cb8b6b7-5hq5j                      3/3     Running   0          2m3s
monitoring-kube-prometheus-operator-86db765b7f-4zkmw     1/1     Running   0          2m3s
monitoring-kube-state-metrics-6d8ffd8867-zhp9x           1/1     Running   0          2m3s
monitoring-prometheus-node-exporter-hhwsp                1/1     Running   0          2m3s
prometheus-monitoring-kube-prometheus-prometheus-0       2/2     Running   0          103s

pods created by kube-prometheus-stack: 6
CRDs installed: 10
```

**How many pods did kube-prometheus-stack create?** Six: three Deployments (Grafana, the operator, kube-state-metrics), two StatefulSets created by the operator from its own CRDs (Prometheus and Alertmanager), and one DaemonSet (node-exporter), which would grow with the cluster rather than staying at one.

What it scrapes, and what it does not:

```plaintext
apiserver                                 up   1
coredns                                   up   1
kube-state-metrics                        up   1
kubelet                                   up   3
monitoring-grafana                        up   1
monitoring-kube-prometheus-alertmanager   up   2
monitoring-kube-prometheus-operator       up   1
monitoring-kube-prometheus-prometheus     up   2
node-exporter                             up   1

quickticket series present: 0
```

> Thirteen targets, nine jobs, every one of them healthy, and not a single metric from the application the cluster exists to run. The `serviceMonitorSelectorNilUsesHelmValues=false` flag from the lab tells Prometheus to accept ServiceMonitors from any namespace, which is necessary and does nothing on its own, because no ServiceMonitor for QuickTicket exists. In Lab 3 the same three services were scraped by naming them in a static file; here the operator replaces that file with a ServiceMonitor object that nobody has written yet, and the gap is easy to miss precisely because the dashboard for the cluster is full and green.

The stack is not free on a single node laptop cluster:

```plaintext
NAME                                                     CPU(cores)   MEMORY(bytes)
monitoring-grafana-668cb8b6b7-5hq5j                      80m          408Mi
prometheus-monitoring-kube-prometheus-prometheus-0       51m          247Mi
monitoring-kube-prometheus-operator-86db765b7f-4zkmw     5m           22Mi
monitoring-kube-state-metrics-6d8ffd8867-zhp9x           4m           21Mi
alertmanager-monitoring-kube-prometheus-alertmanager-0   1m           23Mi
monitoring-prometheus-node-exporter-hhwsp                3m           7Mi
```

Grafana and Prometheus together take 655Mi, which is twice what the five QuickTicket pods request between them (320Mi) and half of what their limits would allow (1280Mi).

---

## Results

| Check | Result |
|---|---|
| k3d cluster running | one node `Ready` 10 s after create, k3s v1.35.5+k3s1 |
| Manifests in `k8s/` | postgres, redis, gateway, events, payments, each a Deployment plus a ClusterIP Service |
| Images available to the cluster | 5 verified on the node after `k3d image import` reported success and delivered nothing |
| All pods running | 5/5 `Running`, applied with one `kubectl apply -f k8s/` |
| Full stack through port-forward | `/events` returns 5 events, `/health` healthy, one purchase confirmed and stored as `PAY-5C3EBCF1` |
| Self-healing demonstrated | pod `Ready` again 1.64 s after delete, clients out for 2.84 s, no operator involved |
| Kubernetes against docker-compose | 1.64 s automatic against 32.10 s with a human, of which 1.44 s was the container |
| Probes configured | liveness on `/metrics`, readiness on `/health`, both confirmed by `kubectl describe` |
| Readiness failure observed | lab's pod delete never showed it (redis back in 1.96 s), 80 s outage showed `0/1` on both events and gateway |
| Liveness on `/health` measured | 4 restarts, recovery 28.64 s against 5.90 s, downtime 85.68 s against 80.13 s |
| Resource limits set | 50m/64Mi requested, 200m/256Mi capped, QoS Burstable, 30 throttled periods on the gateway |
| Helm chart | 11 objects, `helm lint` clean, installed in 28.94 s, upgrade and rollback across 3 revisions |
| Monitoring installed | 6 pods, 10 CRDs, 9 scrape jobs up, 0 QuickTicket series |

Four times in this lab a command reported one thing and the cluster held another. `k3d image import` returned success over an empty node. The API called a pod `Ready` while its port was still closed. The Redis deletion from the lab text finished faster than the readiness probe could see it, so the failure it promises never appeared. The liveness probe the lab recommends produced four restarts and 22.7 extra seconds of downtime that the lab does not mention. Each one surfaced the same way: by asking the receiving side instead of reading the exit code. The commands in `k8s/` are worth about as much as the last `kubectl exec` that checked them.
