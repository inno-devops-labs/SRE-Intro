
# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

## Task 1 — Kubernetes deployment

### Cluster

Created a local k3d cluster:

```text
$ kubectl get nodes
NAME                       STATUS   ROLES           AGE    VERSION
k3d-quickticket-server-0   Ready    control-plane   110s   v1.35.5+k3s1
```

Built and imported local images:

```text
quickticket-gateway:v1
quickticket-events:v1
quickticket-payments:v1
```

### Pods and services

Created Deployment and Service manifests for:

* gateway
* events
* payments
* postgres
* redis

After deployment:

```text
$ kubectl get pods
events-859d5c5c98-l94fs    1/1   Running
gateway-6fc44f68c5-jljnk   1/1   Running
payments-58fb468db-g99q5   1/1   Running
postgres-7c7ffc4b-r9bxt    1/1   Running
redis-c46d5dffc-whqlw      1/1   Running
```

PostgreSQL and Redis were exposed internally using ClusterIP Services.

The database was initialized successfully:

```text
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

### Full-stack verification

Gateway was exposed through port-forward:

```text
$ kubectl port-forward svc/gateway 3080:8080
Forwarding from 127.0.0.1:3080 -> 8080
```

`/events` returned event data successfully.

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

This confirmed that the full application stack was working.

### Self-healing

I deleted the gateway pod:

```text
$ kubectl delete pod -l app=gateway
pod "gateway-6fc44f68c5-jljnk" deleted
```

Kubernetes automatically created a replacement:

```text
gateway-6fc44f68c5-sstb8   1/1   Running   0   9s
```

The replacement pod was running in approximately 9 seconds.

Unlike Docker Compose, Kubernetes automatically restores deleted pods because the Deployment continuously maintains the desired number of replicas.

---

## Task 2 — Probes and resource limits

### Readiness and liveness probes

Added HTTP probes to gateway, events and payments.

Example for gateway:

```text
Liveness:  http-get http://:8080/health delay=10s period=10s #failure=3
Readiness: http-get http://:8080/health delay=0s period=5s #failure=2
```

### Redis failure test

Redis was temporarily unavailable. As a result:

```text
events-7c68cd54d8-cnmd6    0/1   Running
gateway-854488bf7c-b2ljw   0/1   Running
```

Events reported:

```text
Readiness probe failed: HTTP probe failed with statuscode: 503
```

This demonstrated that readiness failure removes an unhealthy pod from traffic.

Liveness also failed because `/health` checked Redis, which caused unnecessary container restarts.

For external dependencies such as databases, readiness is preferable to liveness because restarting the application does not fix an unavailable dependency.

### Resource limits

Added to each Deployment:

```yaml
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi
```

All pods remained healthy:

```text
events      1/1   Running
gateway     1/1   Running
payments    1/1   Running
postgres    1/1   Running
redis       1/1   Running
```

Node allocation:

```text
Allocated resources:
Resource           Requests    Limits
cpu                450m (0%)   1 (2%)
memory             460Mi (0%)  1450Mi (2%)
```

---

## Bonus Task — Helm

Created a Helm chart:

```text
k8s/chart/
├── Chart.yaml
├── values.yaml
└── templates/
```

The chart passed validation:

```text
$ helm lint k8s/chart/
1 chart(s) linted, 0 chart(s) failed
```

Installed QuickTicket using Helm:

```text
$ helm install quickticket k8s/chart/

NAME: quickticket
STATUS: deployed
REVISION: 1
```

Helm release:

```text
$ helm list
NAME        NAMESPACE   REVISION   STATUS
quickticket default     1          deployed
```

Pods after Helm installation:

```text
events      1/1   Running
gateway     1/1   Running
payments    1/1   Running
postgres    1/1   Running
redis       1/1   Running
```

The optional kube-prometheus-stack installation was not performed.

---

## Conclusion

QuickTicket was successfully deployed to Kubernetes using k3d.

The lab demonstrated:

* Kubernetes Deployments and Services
* internal service discovery
* automatic pod recovery
* readiness and liveness probes
* resource requests and limits
* Helm packaging and deployment
