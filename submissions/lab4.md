# Lab 4 - Kubernetes: Deploy QuickTicket to a Cluster

Branch: `feature/lab4`

## Task 1 - Write Manifests and Deploy to k3d

### 1. `kubectl get nodes`

```text
NAME                       STATUS   ROLES           AGE     VERSION
k3d-quickticket-server-0   Ready    control-plane   3m35s   v1.35.5+k3s1
```

### 2. `kubectl get pods,svc` after raw manifest deployment

```text
NAME                            READY   STATUS    RESTARTS   AGE
pod/events-6cc7d85f9b-smnxs     1/1     Running   0          52s
pod/gateway-7cd55d8774-6cptz    1/1     Running   0          52s
pod/payments-d7dc94485-kkmtq    1/1     Running   0          52s
pod/postgres-5754568d8d-c64zt   1/1     Running   0          98s
pod/redis-79b8884cbf-kdrvr      1/1     Running   0          98s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.68.189    <none>        8081/TCP   52s
service/gateway      ClusterIP   10.43.194.145   <none>        8080/TCP   52s
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    3m38s
service/payments     ClusterIP   10.43.16.44     <none>        8082/TCP   52s
service/postgres     ClusterIP   10.43.141.129   <none>        5432/TCP   98s
service/redis        ClusterIP   10.43.247.137   <none>        6379/TCP   98s
```

### 3. `curl localhost:3080/events` via port-forward

Command used:

```bash
kubectl port-forward svc/gateway 3080:8080
curl -sS http://localhost:3080/events | python3 -m json.tool
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

Health check:

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

### 4. Self-healing proof

Command used:

```bash
kubectl get pods -w
kubectl delete pod -l app=gateway
kubectl wait --for=condition=ready --timeout=120s pod -l app=gateway
```

Watch output:

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-6cc7d85f9b-smnxs     1/1     Running   0          3m5s
gateway-7cd55d8774-6cptz    1/1     Running   0          3m5s
payments-d7dc94485-kkmtq    1/1     Running   0          3m5s
postgres-5754568d8d-c64zt   1/1     Running   0          3m51s
redis-79b8884cbf-kdrvr      1/1     Running   0          3m51s
gateway-7cd55d8774-6cptz    1/1     Terminating         0          3m29s
gateway-7cd55d8774-z77cz    0/1     Pending             0          0s
gateway-7cd55d8774-z77cz    0/1     ContainerCreating   0          0s
gateway-7cd55d8774-z77cz    0/1     Running             0          1s
gateway-7cd55d8774-6cptz    0/1     Completed           0          3m32s
gateway-7cd55d8774-z77cz    1/1     Running             0          7s
```

Measured recovery:

```text
pod "gateway-7cd55d8774-6cptz" deleted from default namespace
pod/gateway-7cd55d8774-z77cz condition met
gateway recovery seconds: 8
```

### 5. K8s recovery vs docker-compose

Kubernetes recreated the deleted `gateway` pod automatically in about 8 seconds because the Deployment/ReplicaSet controller continuously reconciles actual state back to the desired state. With docker-compose in Lab 1, a stopped or deleted container needed an explicit operator action such as `docker compose start` or `docker compose up` unless an extra restart policy was configured. Kubernetes self-healing is controller-driven and built into the Deployment abstraction.

## Task 2 - Probes and Resource Limits

### 1. Probe configuration from `kubectl describe pod`

Gateway:

```text
Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

Events:

```text
Liveness:   http-get http://:8081/health delay=30s timeout=1s period=10s #success=1 #failure=6
Readiness:  http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

Payments:

```text
Liveness:   http-get http://:8082/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:  http-get http://:8082/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

### 2. Readiness failure during Redis deletion

Redis pod was deleted and Redis was held at `replicas=0` long enough for readiness probes to fail, then restored to `replicas=1`.

Command output:

```text
pod "redis-79b8884cbf-kdrvr" deleted from default namespace
deployment.apps/redis scaled
NAME                        READY   STATUS    RESTARTS   AGE
events-6cc7d85f9b-smnxs     0/1     Running   0          5m57s
gateway-7cd55d8774-z77cz    0/1     Running   0          2m27s
payments-d7dc94485-kkmtq    1/1     Running   0          5m57s
postgres-5754568d8d-c64zt   1/1     Running   0          6m43s
    Readiness:  http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
--
  Warning  Unhealthy  8s                     kubelet            Readiness probe failed: HTTP probe failed with statuscode: 503
  Warning  Unhealthy  6s                     kubelet            Liveness probe failed: HTTP probe failed with statuscode: 503
  Warning  Unhealthy  2s (x3 over 13s)       kubelet            Readiness probe failed: Get "http://10.42.0.13:8081/health": context deadline exceeded (Client.Timeout exceeded while awaiting headers)
deployment.apps/redis scaled
deployment.apps/redis condition met
deployment.apps/events condition met
deployment.apps/gateway condition met
NAME                        READY   STATUS    RESTARTS   AGE
events-6cc7d85f9b-smnxs     1/1     Running   0          6m13s
gateway-7cd55d8774-z77cz    1/1     Running   0          2m43s
payments-d7dc94485-kkmtq    1/1     Running   0          6m13s
postgres-5754568d8d-c64zt   1/1     Running   0          6m59s
redis-79b8884cbf-6b4b9      1/1     Running   0          16s
```

Watch output:

```text
redis-79b8884cbf-kdrvr      1/1     Terminating         0          6m26s
redis-79b8884cbf-97bvc      0/1     Pending             0          0s
redis-79b8884cbf-97bvc      0/1     ContainerCreating   0          0s
redis-79b8884cbf-97bvc      0/1     Running             0          1s
redis-79b8884cbf-97bvc      0/1     Terminating         0          1s
events-6cc7d85f9b-smnxs     0/1     Running             0          5m49s
gateway-7cd55d8774-z77cz    0/1     Running             0          2m22s
redis-79b8884cbf-6b4b9      0/1     Pending             0          0s
redis-79b8884cbf-6b4b9      0/1     ContainerCreating   0          0s
redis-79b8884cbf-6b4b9      0/1     Running             0          1s
redis-79b8884cbf-6b4b9      1/1     Running             0          7s
events-6cc7d85f9b-smnxs     1/1     Running             0          6m9s
gateway-7cd55d8774-z77cz    1/1     Running             0          2m42s
```

### 3. Node allocation with resource requests and limits

```text
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests     Limits
  --------           --------     ------
  cpu                450m (5%)    1 (12%)
  memory             460Mi (11%)  1450Mi (37%)
  ephemeral-storage  0 (0%)       0 (0%)
  hugepages-1Gi      0 (0%)       0 (0%)
  hugepages-2Mi      0 (0%)       0 (0%)
  hugepages-32Mi     0 (0%)       0 (0%)
  hugepages-64Ki     0 (0%)       0 (0%)
```

### 4. Liveness vs readiness answer

A readiness probe answers "should this pod receive traffic right now?" If readiness fails, Kubernetes keeps the container running but removes the pod from Service endpoints until it becomes ready again.

A liveness probe answers "is this container stuck and should it be restarted?" If liveness fails, Kubernetes kills and restarts the container.

Database or Redis connectivity should be checked with readiness, not liveness. If the database is down, restarting the application pod usually does not fix the database; it only creates extra churn. Readiness is the correct signal because it stops routing traffic to a pod that cannot serve requests and lets it recover automatically when the dependency comes back. In a production setup, liveness should usually use a shallow local health endpoint, while readiness can include dependencies.

## Bonus Task - Helm Chart

### `k8s/chart/Chart.yaml`

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
appVersion: "1.0.0"
```

### `k8s/chart/values.yaml`

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
  db:
    name: quickticket
    user: quickticket
    password: quickticket
  port: 5432

redis:
  replicas: 1
  image: redis:7-alpine
  port: 6379

gateway:
  replicas: 1
  image: quickticket-gateway:v1
  imagePullPolicy: Never
  port: 8080
  timeoutMs: "5000"
  eventsUrl: http://events:8081
  paymentsUrl: http://payments:8082

events:
  replicas: 1
  image: quickticket-events:v1
  imagePullPolicy: Never
  port: 8081
  db:
    host: postgres
    port: "5432"
    name: quickticket
    user: quickticket
    password: quickticket
    maxConns: "10"
  redis:
    host: redis
    port: "6379"
    timeoutMs: "1000"
  reservationTtl: "300"

payments:
  replicas: 1
  image: quickticket-payments:v1
  imagePullPolicy: Never
  port: 8082
  failureRate: "0.0"
  latencyMs: "0"
```

### `helm list`

```text
NAME       	NAMESPACE	REVISION	UPDATED                             	STATUS  	CHART            	APP VERSION
quickticket	default  	1       	2026-06-12 18:27:00.520368 +0300 MSK	deployed	quickticket-0.1.0	1.0.0
```

### `kubectl get pods` after Helm install

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-6cc7d85f9b-qlq8p     1/1     Running   0          39s
gateway-7cd55d8774-cdht8    1/1     Running   0          39s
payments-d7dc94485-rftf7    1/1     Running   0          39s
postgres-5754568d8d-mklj8   1/1     Running   0          39s
redis-79b8884cbf-mgslk      1/1     Running   0          39s
```

Monitoring via `kube-prometheus-stack` was not installed; it is optional in the bonus evidence and is not part of the Lab 4 Acceptance Criteria.

## Filled Checklist

- [x] Task 1 done - K8s manifests written, QuickTicket deployed to k3d
- [x] Task 2 done - probes and resource limits added
- [x] Bonus Task done - Helm chart created and installed

## Acceptance Criteria Checklist

### Task 1

- [x] k3d cluster running (`kubectl get nodes` output)
- [x] All manifest files added in `k8s/` (postgres, redis, gateway, events, payments)
- [x] All pods running (`kubectl get pods,svc` output)
- [x] Full stack working via port-forward (`curl` output)
- [x] Self-healing demonstrated (pod delete and auto-recovery output)
- [x] Written comparison of K8s recovery vs docker-compose

### Task 2

- [x] Probes configured in manifests (`kubectl describe` output)
- [x] Readiness failure observed during Redis deletion
- [x] Resource limits set, node allocation shown
- [x] Written answer on liveness vs readiness for DB checks

### Bonus Task

- [x] Helm chart with `Chart.yaml`, `values.yaml`, and templates
- [x] `helm list` showing installed release
- [x] Pods running after Helm install
