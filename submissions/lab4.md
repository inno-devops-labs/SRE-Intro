# Lab 4 — Kubernetes and Helm

## Environment and Cluster

The work was performed on Windows with Docker Desktop. The observed tool versions were Docker `29.2.1`, kubectl client `v1.34.1`, k3d `v5.9.0` (k3s `v1.35.5+k3s1`), and Helm `v4.3.0`.

```text
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   9h    v1.35.5+k3s1
```

The existing cluster's kubeconfig initially pointed at `host.docker.internal:55714`, which timed out on this Windows network. It was repaired to use the same local API port at `127.0.0.1:55714` with TLS server name `host.docker.internal`; the node then returned `Ready`.

## Images and Raw Manifests

The following locally built images were imported into k3d before applying the raw manifests.

```text
quickticket-gateway   v1   215MB
quickticket-events    v1   234MB
quickticket-payments  v1   212MB
```

All five raw files in `k8s/` contain one Deployment and one ClusterIP Service. They use matching `app` selectors, internal service DNS names, locally imported application images with `imagePullPolicy: Never`, probes, and 50m/64Mi requests with 200m/256Mi limits.

## Raw Deployment and Database Seed

The client-side validation created all ten objects in dry-run mode. The raw stack was then deployed and all five pods became Ready. The PowerShell-compatible seed command and result were:

```powershell
Get-Content app/seed.sql -Raw | kubectl exec -i deployment/postgres -- psql -U quickticket -d quickticket
kubectl exec deployment/postgres -- psql -U quickticket -d quickticket -c 'SELECT count(*) AS events_count FROM events;'
```

```text
CREATE TABLE
CREATE TABLE
INSERT 0 5
 events_count
--------------
            5
```

The events container initially logged DB connection refusals while PostgreSQL was still starting, then connected on its seventh retry. This was a real startup-order race; no manual application change was necessary.

## Critical Path

Via `kubectl port-forward svc/gateway 3080:8080`, the raw deployment was checked at `2026-09-20T18:38:03.4327835+03:00`.

```text
GET /health -> 200
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}

GET /events -> 200
[{"id":1,"name":"Go Conference 2026",...,"available":100}, ...]

POST /events/1/reserve -> 200
{"reservation_id":"52bf2717-1534-4b62-af7e-8ccbcea947ad","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}

POST /reserve/52bf2717-1534-4b62-af7e-8ccbcea947ad/pay -> 200
{"order_id":"52bf2717-1534-4b62-af7e-8ccbcea947ad","event_id":1,"quantity":1,"total_cents":5000,"status":"confirmed"}
```

## Self-Healing

The Helm-managed gateway pod was deleted after the release was installed.

| Timestamp | Observation |
|---|---|
| 2026-09-21T02:19:57.7933487+03:00 | Deleted ready pod `gateway-7cd55d8774-rk2td`, UID `95de103d-c470-4064-a397-139cf6fc2b1b`. |
| 2026-09-21T02:19:59.8198440+03:00 | Replacement `gateway-7cd55d8774-mccn5`, UID `df9baeb2-27be-4e45-961c-650c8758eb37`, appeared Pending. |
| 2026-09-21T02:20:01.5412386+03:00 | The replacement was Running but not Ready. |
| 2026-09-21T02:20:13.3281500+03:00 | The replacement became Ready. |

The observable recovery time from deletion command to Ready was `15.5362632` seconds. Kubernetes recreated the desired Deployment replica automatically. In Lab 1, Docker Compose required an explicit `docker compose start` after a stopped service; it did not replace a deleted container through a Deployment controller.

## Probes and Resource Allocation

`kubectl describe pod` showed the requested probes and limits for all three application pods. The gateway example was:

```text
Limits: cpu 200m, memory 256Mi
Requests: cpu 50m, memory 64Mi
Liveness:  http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness: http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

Events and payments reported the same settings on ports 8081 and 8082 respectively. Payments describe output showed `PAYMENT_FAILURE_RATE: 0.0` and `PAYMENT_LATENCY_MS: 0`.

```text
Allocated resources:
Resource           Requests    Limits
cpu                450m (3%)   1 (8%)
memory             460Mi (6%)  1450Mi (19%)
```

## Redis Readiness Experiment

Deleting Redis once did not expose a non-ready events pod because Redis recovered too quickly. A controlled scale-to-zero experiment was then used.

| Timestamp | State |
|---|---|
| 2026-09-21T02:20:36.7287523+03:00 | events was `1/1 Ready`; Redis pod was `redis-6fcfb5475d-twhs9`. |
| 2026-09-21T02:20:57.5600690+03:00 | After `kubectl scale deployment/redis --replicas=0`, events was `0/1 Ready`. |
| same observation | `kubectl get endpoints events` returned an empty ENDPOINTS column. |
| 2026-09-21T02:21:33.5237623+03:00 | Redis was restored to one replica and events was again `1/1 Ready`. |

The events describe output contained `Readiness probe failed: HTTP probe failed with status code: 503`. A readiness failure removes the pod from Service endpoints but does not itself restart it. A liveness failure restarts the container. Database or Redis connectivity belongs in readiness: restarting an application does not repair an unavailable dependency and can make the outage worse.

## Helm Chart Bonus

The raw manifests remain in `k8s/`. The chart templates all five Deployments and Services, with images, replicas, ports, application environment, probes, and resources configurable in `values.yaml`.

`Chart.yaml`:

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
type: application
version: 0.1.0
appVersion: "v1"
```

`values.yaml`:

```yaml
postgres: {replicas: 1, image: postgres:17-alpine, port: 5432, database: quickticket, user: quickticket, password: quickticket}
redis: {replicas: 1, image: redis:7-alpine, port: 6379}
gateway:
  replicas: 1
  image: quickticket-gateway:v1
  imagePullPolicy: Never
  port: 8080
  eventsUrl: http://events:8081
  paymentsUrl: http://payments:8082
  timeoutMs: "5000"
events:
  replicas: 1
  image: quickticket-events:v1
  imagePullPolicy: Never
  port: 8081
  db: {host: postgres, port: "5432", name: quickticket, user: quickticket, password: quickticket, maxConns: "10"}
  redis: {host: redis, port: "6379", timeoutMs: "1000"}
  reservationTtl: "300"
payments:
  replicas: 1
  image: quickticket-payments:v1
  imagePullPolicy: Never
  port: 8082
  failureRate: "0.0"
  latencyMs: "0"
```

The full file also contains the common resources and all probe settings:

```yaml
postgres:
  resources: {requests: {cpu: 50m, memory: 64Mi}, limits: {cpu: 200m, memory: 256Mi}}
redis:
  resources: {requests: {cpu: 50m, memory: 64Mi}, limits: {cpu: 200m, memory: 256Mi}}
gateway:
  probes:
    liveness: {initialDelaySeconds: 10, periodSeconds: 10, failureThreshold: 3}
    readiness: {periodSeconds: 5, failureThreshold: 2}
  resources: {requests: {cpu: 50m, memory: 64Mi}, limits: {cpu: 200m, memory: 256Mi}}
events:
  probes:
    liveness: {initialDelaySeconds: 10, periodSeconds: 10, failureThreshold: 3}
    readiness: {periodSeconds: 5, failureThreshold: 2}
  resources: {requests: {cpu: 50m, memory: 64Mi}, limits: {cpu: 200m, memory: 256Mi}}
payments:
  probes:
    liveness: {initialDelaySeconds: 10, periodSeconds: 10, failureThreshold: 3}
    readiness: {periodSeconds: 5, failureThreshold: 2}
  resources: {requests: {cpu: 50m, memory: 64Mi}, limits: {cpu: 200m, memory: 256Mi}}
```

```text
helm lint k8s/chart
1 chart(s) linted, 0 chart(s) failed
```

`helm template quickticket k8s/chart` rendered the five ClusterIP Services and five Deployments. After explicitly deleting only the five raw YAML files from the cluster, the chart was installed:

```text
NAME        NAMESPACE REVISION STATUS   CHART             APP VERSION
quickticket default   1        deployed quickticket-0.1.0 v1
```

After Helm installation PostgreSQL was seeded again and, at `2026-09-21T02:19:28.0936783+03:00`, the full stack passed again:

```text
GET /health -> 200 {"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
GET /events -> 200 (five seeded events)
POST /events/1/reserve -> 200
{"reservation_id":"fae1d5d8-49dd-4de5-9eee-a580137cf4ec",...}
POST /reserve/fae1d5d8-49dd-4de5-9eee-a580137cf4ec/pay -> 200
{"order_id":"fae1d5d8-49dd-4de5-9eee-a580137cf4ec","status":"confirmed"}
```

## Monitoring Attempt

`kube-prometheus-stack` was installed with Helm into namespace `monitoring`. Helm listed release `monitoring` as deployed. At the observed point it created four pods: operator, kube-state-metrics, node-exporter, and Grafana. The operator, kube-state-metrics, and node-exporter were Running; the Grafana pod was `0/3 ErrImagePull`. The QuickTicket release remained deployed and healthy, so this optional monitoring image-pull problem did not affect the Lab 4 application release.

## Final State

After Redis restoration, all five QuickTicket pods were `1/1 Running`; events Service again had endpoint `10.42.0.21:8081`. The final Helm release was `quickticket` in `deployed` state. A final three-service port-forward health check at `2026-09-21T02:29:57.0644636+03:00` returned:

```text
gateway  200 {"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
events   200 {"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
payments 200 {"status":"healthy","failure_rate":0.0,"latency_ms":0}
```
