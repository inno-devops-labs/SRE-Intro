# Lab 4 - Kubernetes: Deploy QuickTicket to a Cluster

## Task 1 - Write Manifests and Deploy to k3d

### k3d cluster

I created a local k3d cluster named `quickticket`. The Kubernetes node was ready:

```text
NAME                       STATUS   ROLES           VERSION
k3d-quickticket-server-0   Ready    control-plane   v1.35.5+k3s1
```

I built the three application images locally and imported them into the cluster:

```text
quickticket-events     v1
quickticket-gateway    v1
quickticket-payments   v1
```

For the application images I used `imagePullPolicy: Never` so Kubernetes used the images imported into k3d instead of trying to pull them from a registry.

### Manifests

I wrote Deployment and Service manifests for:

```text
postgres
redis
events
payments
gateway
```

PostgreSQL and Redis use their public container images. The three QuickTicket services use the locally imported `quickticket-*:v1` images.

The Services use `ClusterIP`. Service names are also used as internal DNS hostnames, for example:

```text
postgres
redis
events
payments
```

### Running pods and services

After deployment, all five pods were running:

```text
events      1/1 Running
gateway     1/1 Running
payments    1/1 Running
postgres    1/1 Running
redis       1/1 Running
```

The corresponding services were:

```text
events      ClusterIP  8081/TCP
gateway     ClusterIP  8080/TCP
payments    ClusterIP  8082/TCP
postgres    ClusterIP  5432/TCP
redis       ClusterIP  6379/TCP
```

### Full-stack verification

I port-forwarded the gateway service to local port 3080.

The gateway health endpoint returned:

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

The `/events` endpoint also returned the seeded event list through the Kubernetes deployment. For example:

```json
{
    "id": 1,
    "name": "Go Conference 2026",
    "venue": "Main Hall A",
    "total_tickets": 100,
    "price_cents": 5000
}
```

This verified the path gateway -> events -> PostgreSQL/Redis inside the cluster.

### Kubernetes self-healing

I deleted the gateway pod:

```text
Deleting: gateway-6d775544c-mb24c
```

Kubernetes immediately created a replacement:

```text
gateway-6d775544c-888rq   0/1   Running
gateway-6d775544c-888rq   0/1   Running
gateway-6d775544c-888rq   1/1   Running
```

Measured recovery time:

```text
15 seconds
```

The replacement pod became `1/1 Ready` without any manual start command.

Compared with Lab 1, this is the main behavioral difference I observed. With docker-compose, after deliberately stopping a service I had to manually start it again. With the Kubernetes Deployment, deleting a pod caused the controller to create a replacement automatically and restore the desired replica count.

---

## Task 2 - Probes and Resource Limits

### Liveness and readiness probes

I configured HTTP probes for gateway, events, and payments.

Gateway:

```text
Liveness:  http-get http://:8080/health delay=10s period=10s failure=3
Readiness: http-get http://:8080/health delay=5s period=5s failure=2
```

Events:

```text
Liveness:  http-get http://:8081/health delay=10s period=10s failure=3
Readiness: http-get http://:8081/health delay=5s period=5s failure=2
```

Payments:

```text
Liveness:  http-get http://:8082/health delay=10s period=10s failure=3
Readiness: http-get http://:8082/health delay=5s period=5s failure=2
```

### Readiness failure experiment

Events reports Redis state through `/health`, so I removed Redis long enough for the events readiness probe to fail.

The events pod initially stayed ready:

```text
events-56cb4c49bd-gzmn8   1/1   Running
```

Then Kubernetes changed it to:

```text
events-56cb4c49bd-gzmn8   0/1   Running
```

The pod events included:

```text
Readiness probe failed: HTTP probe failed with statuscode: 503
```

After restoring Redis, Kubernetes reported both Redis and events ready again:

```text
redis-6fcfb5475d-p5q2x    1/1   Running
events-56cb4c49bd-gzmn8   1/1   Running
```

### Liveness vs readiness

A readiness probe answers whether a pod is currently able to receive traffic. If readiness fails, Kubernetes removes the pod from Service endpoints, but does not restart the container.

A liveness probe answers whether the application process is still healthy enough to keep running. Repeated liveness failures cause Kubernetes to restart the container.

For database or other dependency connectivity I would use readiness, not liveness. If PostgreSQL or Redis is unavailable, restarting the application pod does not repair that external dependency. Marking the pod unready stops traffic from being routed to an instance that cannot serve requests, while allowing it to become ready again when the dependency recovers.

### Resource requests and limits

I added the following to each container:

```yaml
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi
```

The node allocation output during the experiment included:

```text
Resource           Requests    Limits
cpu                450m (11%)  1 (25%)
memory             460Mi (5%)  1450Mi (18%)
```

The total values also include Kubernetes/k3s system workloads in addition to my QuickTicket pods.

---

## Bonus Task - Helm Chart

I converted the manifests into a Helm chart under:

```text
k8s/chart/
```

The chart passed `helm lint`:

```text
1 chart(s) linted, 0 chart(s) failed
```

The rendered chart contained five Services and five Deployments.

### Chart.yaml

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
type: application
```

### values.yaml

```yaml
gateway:
  replicas: 1
  image: quickticket-gateway:v1
  port: 8080
  eventsUrl: http://events:8081
  paymentsUrl: http://payments:8082
  timeoutMs: "5000"

events:
  replicas: 1
  image: quickticket-events:v1
  port: 8081
  db:
    host: postgres
    port: "5432"
    name: quickticket
    user: quickticket
    password: quickticket
  redis:
    host: redis
    port: "6379"

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
  database: quickticket
  user: quickticket
  password: quickticket

redis:
  replicas: 1
  image: redis:7-alpine
  port: 6379
```

### Helm deployment

I removed the raw manifest deployment and installed the chart as a Helm release:

```text
NAME: quickticket
NAMESPACE: default
STATUS: deployed
REVISION: 1
```

`helm list` showed:

```text
NAME         NAMESPACE   REVISION   STATUS     CHART
quickticket  default     1          deployed   quickticket-0.1.0
```

After the Helm install the application pods were running. During the captured `kubectl get pods` output there were temporarily six pods because a gateway rollout restart was in progress: one old gateway pod was still `1/1` while its replacement was starting `0/1`. The steady-state chart defines five Deployments with one replica each.

I tested the Helm-deployed stack through gateway port-forwarding and received a healthy response:

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

The `/events` endpoint also returned the seeded event data after the Helm deployment.

I did not install the optional `kube-prometheus-stack` bonus extension. The required Helm chart itself was created, validated, installed, and verified.

---

## Summary

I deployed QuickTicket to a local k3d Kubernetes cluster using manifests I created for all five components. The complete application worked through a Kubernetes Service and port-forward.

Kubernetes recreated a deleted gateway pod automatically in about 15 seconds. I also configured readiness and liveness probes, observed events being removed from readiness when Redis was unavailable, and added CPU and memory requests and limits.

For the bonus task I converted the deployment to a configurable Helm chart, validated it with `helm lint`, installed it successfully, and verified that the application remained healthy.
