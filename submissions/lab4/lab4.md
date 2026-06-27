# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

## Task 1 — Write Manifests & Deploy to k3d

### Exercise 4.1 — Create a k3d Cluster

`k3d` was not installed on the machine, so I installed it locally into `/tmp/k3d-bin` and created the `quickticket` cluster.

```bash
/tmp/k3d-bin/k3d version
/tmp/k3d-bin/k3d cluster create quickticket
kubectl get nodes
```

Output:

```text
k3d version v5.9.0
k3s version v1.35.5-k3s1 (default)

NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   17s   v1.35.5+k3s1
```

Current Kubernetes context:

```text
k3d-quickticket
```

### Exercise 4.2 — Build and Import Images

Images were built locally:

```bash
cd app/
docker build -t quickticket-gateway:v1 ./gateway
docker build -t quickticket-events:v1 ./events
docker build -t quickticket-payments:v1 ./payments
```

Then imported into the k3d cluster:

```bash
/tmp/k3d-bin/k3d image import quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 -c quickticket
```

Output:

```text
INFO[0000] Importing image(s) into cluster 'quickticket'
INFO[0012] Successfully imported image(s)
INFO[0012] Successfully imported 3 image(s) into 1 cluster(s)
```

Docker image verification:

```text
quickticket-events:v1     73387bda3376   157MB
quickticket-gateway:v1    6c9906f7a8e4   143MB
quickticket-payments:v1   99c9dd5fe53b   141MB
```

### Exercise 4.3 — Kubernetes Manifests

Created the following manifests in `k8s/`:

```text
k8s/postgres.yaml
k8s/redis.yaml
k8s/events.yaml
k8s/payments.yaml
k8s/gateway.yaml
```

Each manifest contains a Deployment and a ClusterIP Service. The application deployments use local images with:

```yaml
imagePullPolicy: Never
```

The app services use the same internal ports as Docker Compose:

```text
gateway   -> 8080
events    -> 8081
payments  -> 8082
postgres  -> 5432
redis     -> 6379
```

The manifests were validated client-side:

```bash
kubectl apply --dry-run=client -f k8s/
```

Output:

```text
deployment.apps/events created (dry run)
service/events created (dry run)
deployment.apps/gateway created (dry run)
service/gateway created (dry run)
deployment.apps/payments created (dry run)
service/payments created (dry run)
deployment.apps/postgres created (dry run)
service/postgres created (dry run)
deployment.apps/redis created (dry run)
service/redis created (dry run)
```

### Exercise 4.4 — Deploy to Kubernetes

Command:

```bash
kubectl apply -f k8s/
```

Output:

```text
deployment.apps/events created
service/events created
deployment.apps/gateway created
service/gateway created
deployment.apps/payments created
service/payments created
deployment.apps/postgres created
service/postgres created
deployment.apps/redis created
service/redis created
```

Initial startup issue:

```text
events started before postgres was accepting TCP connections.
The events service exhausted its 10 DB connection attempts and stayed unready.
```

Events logs:

```text
DB connection attempt 1/10 failed: connection to server at "postgres" (10.43.138.46), port 5432 failed: Connection refused
...
DB connection attempt 10/10 failed: connection to server at "postgres" (10.43.138.46), port 5432 failed: Connection refused
Could not connect to database after 10 attempts
```

Recovery:

```bash
kubectl rollout restart deployment/events deployment/gateway
```

Output:

```text
deployment.apps/events restarted
deployment.apps/gateway restarted
deployment "events" successfully rolled out
deployment "gateway" successfully rolled out
```

### Exercise 4.5 — Initialize the Database

The first attempt without `-i` did not pass stdin into the pod, so the database stayed empty. The working command was:

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket < app/seed.sql
```

Output:

```text
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

Verification:

```bash
kubectl exec $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -c 'SELECT count(*) AS events_count FROM events;'
```

Output:

```text
 events_count
--------------
            5
(1 row)
```

### Exercise 4.6 — Verify Pods and Services

Command:

```bash
kubectl get pods,svc -o wide
```

Output:

```text
NAME                            READY   STATUS    RESTARTS      IP           NODE
pod/events-5d9f887494-phdpf     1/1     Running   0             10.42.0.15   k3d-quickticket-server-0
pod/gateway-6957f8fcfb-ct5zq    1/1     Running   1             10.42.0.16   k3d-quickticket-server-0
pod/payments-d7dc94485-c2xl8    1/1     Running   0             10.42.0.11   k3d-quickticket-server-0
pod/postgres-78489d7f5f-xhnwf   1/1     Running   0             10.42.0.12   k3d-quickticket-server-0
pod/redis-6fcfb5475d-k8tkg      1/1     Running   0             10.42.0.18   k3d-quickticket-server-0

NAME                 TYPE        CLUSTER-IP      PORT(S)    SELECTOR
service/events       ClusterIP   10.43.238.44    8081/TCP   app=events
service/gateway      ClusterIP   10.43.82.116    8080/TCP   app=gateway
service/kubernetes   ClusterIP   10.43.0.1       443/TCP    <none>
service/payments     ClusterIP   10.43.155.219   8082/TCP   app=payments
service/postgres     ClusterIP   10.43.138.46    5432/TCP   app=postgres
service/redis        ClusterIP   10.43.82.129    6379/TCP   app=redis
```

### Exercise 4.7 — Verify the Critical Path

Port-forward:

```bash
kubectl port-forward svc/gateway 3080:8080
```

Output:

```text
Forwarding from 127.0.0.1:3080 -> 8080
Forwarding from [::1]:3080 -> 8080
```

Events endpoint:

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

Health endpoint:

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

### Exercise 4.8 — Kubernetes Self-Healing

Command:

```bash
kubectl get pods -w
kubectl delete pod -l app=gateway
```

Deletion time:

```text
2026-06-18T21:50:00+03:00
pod "gateway-6957f8fcfb-hntjt" deleted from default namespace
2026-06-18T21:50:02+03:00
```

Watch output:

```text
gateway-6957f8fcfb-hntjt    1/1     Terminating         0   55s
gateway-6957f8fcfb-ct5zq    0/1     Pending             0   0s
gateway-6957f8fcfb-ct5zq    0/1     ContainerCreating   0   0s
gateway-6957f8fcfb-hntjt    0/1     Completed           0   56s
gateway-6957f8fcfb-ct5zq    0/1     Running             0   2s
gateway-6957f8fcfb-ct5zq    1/1     Running             0   8s
```

### Self-Healing Analysis

Kubernetes recreated the deleted gateway pod automatically. The replacement pod became `1/1 Running` in about **8 seconds**.

Compared with Docker Compose from Lab 1, Kubernetes recovery is automatic because the Deployment controller continuously reconciles the desired replica count. With Docker Compose, stopping or killing a service generally requires a manual `docker compose start` or `docker compose up` action.

---

## Task 2 — Probes & Resource Limits

### Exercise 4.9 — Probes

Gateway probes:

```text
Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

Events probes:

```text
Liveness:   http-get http://:8081/metrics delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:  http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

Payments probes were also configured:

```text
Liveness:   http-get http://:8082/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:  http-get http://:8082/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

Implementation note:

```text
events liveness uses /metrics, while readiness uses /health.
```

This keeps process liveness separate from dependency health. If Redis or Postgres is down, the pod should become unready, not necessarily restart.

### Exercise 4.10 — Readiness Probe Failure

First test: deleting Redis directly.

```bash
kubectl delete pod -l app=redis
```

Output:

```text
2026-06-18T21:50:49+03:00
pod "redis-6fcfb5475d-jj2pv" deleted from default namespace
2026-06-18T21:50:50+03:00
```

Watch output:

```text
redis-6fcfb5475d-jj2pv      1/1     Terminating         0   5m20s
redis-6fcfb5475d-w6vbr      0/1     Pending             0   0s
redis-6fcfb5475d-w6vbr      0/1     ContainerCreating   0   0s
redis-6fcfb5475d-w6vbr      1/1     Running             0   2s
```

Redis recovered so quickly that `events` did not fail two consecutive readiness checks.

To make the readiness failure visible, I temporarily scaled Redis to zero replicas:

```bash
kubectl scale deployment/redis --replicas=0
```

Watch output:

```text
redis-6fcfb5475d-w6vbr      1/1     Terminating         0   40s
redis-6fcfb5475d-w6vbr      0/1     Completed           0   41s
gateway-6957f8fcfb-ct5zq    0/1     Running             0   99s
events-5d9f887494-phdpf     0/1     Running             0   2m42s
```

`events` became `0/1 Ready` because its readiness probe checks `/health`, and `/health` reports Redis as a dependency.

Describe output:

```text
Readiness:  http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
Warning  Unhealthy  14s (x3 over 19s)  kubelet  Readiness probe failed: HTTP probe failed with statuscode: 503
Warning  Unhealthy  3s (x2 over 8s)    kubelet  Readiness probe failed: context deadline exceeded
```

Redis was restored:

```bash
kubectl scale deployment/redis --replicas=1
kubectl wait --for=condition=available deployment/redis deployment/events deployment/gateway --timeout=120s
```

Output:

```text
deployment.apps/redis condition met
deployment.apps/events condition met
deployment.apps/gateway condition met
```

Recovery watch output:

```text
redis-6fcfb5475d-k8tkg      0/1     Pending             0          0s
redis-6fcfb5475d-k8tkg      0/1     ContainerCreating   0          0s
redis-6fcfb5475d-k8tkg      1/1     Running             0          2s
events-5d9f887494-phdpf     1/1     Running             0          3m2s
gateway-6957f8fcfb-ct5zq    1/1     Running             1          2m8s
```

### Exercise 4.11 — Resource Requests and Limits

Each container has resource requests and limits:

```yaml
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi
```

Node allocation:

```bash
kubectl describe $(kubectl get nodes -o name | head -1) | grep -A 14 "Allocated resources"
```

Output:

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

The total includes QuickTicket pods plus Kubernetes system pods running in the k3d cluster.

---

## Final Answers

### How long did K8s take to recreate the deleted pod?

The gateway replacement pod became `1/1 Running` in about **8 seconds** after deletion.

### How does this compare to docker-compose restart?

Kubernetes recreated the pod automatically because the Deployment desired state still required one gateway replica. Docker Compose does not continuously reconcile replicas in the same way; after a manual stop or failure, recovery usually requires an explicit `docker compose start` or `docker compose up`.

### What is the difference between liveness and readiness probe failure?

Readiness failure means the pod stays running but is removed from Service endpoints, so it stops receiving traffic.

Liveness failure means Kubernetes treats the container as unhealthy and restarts it.

### Which probe should check database connectivity, and why?

Database and Redis connectivity should be checked with **readiness**, not liveness. If a dependency is down, restarting the application pod usually does not fix the dependency. The safer behavior is to stop routing traffic to that pod until dependencies recover.

The Redis test demonstrated this: when Redis was unavailable, `events` became `0/1 Ready`. Gateway also became unready because it depends on events. This is the behavior we want for dependency outages.

### Notes

The Lab 3 Docker Compose stack was stopped before running port-forward because it was using local port `3080`. The Kubernetes cluster remains running in k3d, and all QuickTicket pods are healthy at the end of the lab.
