# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

## Task 1 — Deploy QuickTicket to Kubernetes

### 1.1 — Kubernetes cluster

A local k3d Kubernetes cluster named `quickticket` was created and used to deploy the QuickTicket application.

The application consists of the following services:

* `gateway`
* `events`
* `payments`
* `postgres`
* `redis`

The application images were built locally and imported into the k3d cluster. Local application images use `imagePullPolicy: Never`.

### 1.2 — Kubernetes resources

Kubernetes manifests were created for all five components:

* `k8s/gateway.yaml`
* `k8s/events.yaml`
* `k8s/payments.yaml`
* `k8s/postgres.yaml`
* `k8s/redis.yaml`

The application services use `ClusterIP`, so they are accessible from other pods inside the Kubernetes cluster through their Kubernetes Service names.

For example, the gateway communicates with the other services using:

```text
EVENTS_URL=http://events:8081
PAYMENTS_URL=http://payments:8082
```

The events service uses:

```text
DB_HOST=postgres
REDIS_HOST=redis
```

### 1.3 — Pods and Services

After deployment, all five components were running successfully:

```text
NAME                           READY   STATUS    RESTARTS   AGE
pod/events-6c4df7d6-z7v6w      1/1     Running   0          2m41s
pod/gateway-6fc44f68c5-cns7m   1/1     Running   0          95s
pod/payments-58fb468db-bzljj   1/1     Running   0          2m
pod/postgres-7c7ffc4b-nlq87    1/1     Running   0          5m42s
pod/redis-c46d5dffc-qwvdl      1/1     Running   0          4m47s
```

The following Kubernetes Services were created:

```text
NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)
service/events       ClusterIP   10.43.109.7     <none>        8081/TCP
service/gateway      ClusterIP   10.43.203.73    <none>        8080/TCP
service/payments     ClusterIP   10.43.149.163   <none>        8082/TCP
service/postgres     ClusterIP   10.43.71.154    <none>        5432/TCP
service/redis        ClusterIP   10.43.248.139   <none>        6379/TCP
```

### 1.4 — Database seeding

The PostgreSQL database was seeded using the provided `app/seed.sql` file:

```bash
kubectl exec -it $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql
```

The seeded data was successfully returned through the application API.

### 1.5 — Full-stack verification

The gateway was exposed locally using port forwarding:

```bash
kubectl port-forward svc/gateway 3080:8080
```

The `/events` endpoint was tested with:

```bash
curl -s http://localhost:3080/events | python3 -m json.tool
```

The request successfully returned the seeded events:

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

This verified the request path through the gateway, events service, and PostgreSQL database.

### 1.6 — Kubernetes self-healing

The Kubernetes Deployment was tested by manually deleting the gateway pod:

```bash
kubectl delete pod -l app=gateway
```

The original pod was deleted and Kubernetes automatically created a replacement:

```text
gateway-6fc44f68c5-hrp7w   1/1   Running   0   4s
```

The test was repeated:

```text
Mon Sep 21 08:10:14 MSK 2026
pod "gateway-6fc44f68c5-hrp7w" deleted from default namespace

gateway-6fc44f68c5-xp9sg   1/1   Running   0   9s
```

The replacement pod became `Running` within a few seconds.

### 1.7 — Kubernetes vs Docker Compose recovery

With Kubernetes, deleting a gateway pod does not permanently stop the service. The Deployment controller continuously maintains the desired number of replicas and automatically creates a replacement pod.

With Docker Compose, there is no equivalent Deployment controller maintaining the desired replica count. In the previous Docker Compose setup, recovery required manually restarting the service.

Therefore, Kubernetes provides automatic reconciliation and self-healing at the Deployment level.

---

# Task 2 — Probes and Resource Management

## 2.1 — Liveness and readiness probes

Liveness and readiness probes were added to `gateway`, `events`, and `payments`.

The gateway configuration was verified with:

```text
Limits:
  cpu:     200m
  memory:  256Mi
Requests:
  cpu:      50m
  memory:   64Mi
Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

The corresponding probes were configured for the other services using their respective ports:

* `events`: port `8081`
* `payments`: port `8082`

The configuration is:

### Liveness

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: <service-port>
  initialDelaySeconds: 10
  periodSeconds: 10
  failureThreshold: 3
```

### Readiness

```yaml
readinessProbe:
  httpGet:
    path: /health
    port: <service-port>
  periodSeconds: 5
  failureThreshold: 2
```

The initial `connection refused` readiness warnings observed during pod startup were expected because the readiness probe starts immediately while the application may still be starting. After startup, all application pods became `1/1 Ready`.

## 2.2 — Readiness failure when Redis is unavailable

To test readiness behavior, the Redis Deployment was temporarily scaled to zero:

```bash
kubectl scale deployment redis --replicas=0
```

Before the failure, all application pods were ready:

```text
events-675d86c77-twcnd     1/1     Running
gateway-7cd55d8774-5rxx6   1/1     Running
payments-d7dc94485-lcpkv   1/1     Running
postgres-7c7ffc4b-nlq87    1/1     Running
redis-c46d5dffc-qwvdl      1/1     Running
```

After Redis was scaled to zero, the events pod became:

```text
events-675d86c77-twcnd     0/1     Running
```

The container itself continued running, but the pod was no longer considered Ready.

This demonstrates the purpose of a readiness probe: a service can remain alive while being temporarily removed from normal traffic because one of its dependencies is unavailable.

## 2.3 — Restoring Redis

Redis was restored with:

```bash
kubectl scale deployment redis --replicas=1
```

After recovery, all services returned to the Ready state:

```text
NAME                       READY   STATUS    RESTARTS
events-675d86c77-twcnd     1/1     Running   2
gateway-7cd55d8774-5rxx6   1/1     Running   2
payments-d7dc94485-lcpkv   1/1     Running   0
postgres-7c7ffc4b-nlq87    1/1     Running   0
redis-c46d5dffc-xrk5m      1/1     Running   0
```

The application returned to a healthy state after the dependency was restored.

## 2.4 — Readiness vs liveness

The two probes serve different purposes.

**Readiness** determines whether a pod should receive traffic. If the readiness probe fails, Kubernetes keeps the container running but removes the pod from the set of ready endpoints for its Service.

**Liveness** determines whether the application process is still functioning. If the liveness probe repeatedly fails, kubelet can restart the container.

For the `events` service, dependency failures such as Redis becoming unavailable should primarily affect **readiness**, rather than liveness. The events process itself may still be functioning correctly even though it cannot currently serve requests that require Redis.

Using liveness for such a dependency failure could cause unnecessary container restarts instead of simply removing the instance from traffic.

## 2.5 — Resource requests and limits

Resource requests and limits were added to all three application containers.

The configured values are:

```yaml
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi
```

The gateway pod confirmed the configuration:

```text
Limits:
  cpu:     200m
  memory:  256Mi
Requests:
  cpu:      50m
  memory:   64Mi
```

The node's allocated resources after deployment were:

```text
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                350m (3%)   600m (6%)
  memory             332Mi (4%)  938Mi (11%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
```

This demonstrates that Kubernetes is aware of the resource requirements and limits of the deployed workloads.

## 2.6 — Final cluster state

After completing the readiness experiment and restoring Redis, the final cluster state was:

```text
NAME                           READY   STATUS    RESTARTS
pod/events-675d86c77-twcnd     1/1     Running   2
pod/gateway-7cd55d8774-5rxx6   1/1     Running   2
pod/payments-d7dc94485-lcpkv   1/1     Running   0
pod/postgres-7c7ffc4b-nlq87    1/1     Running   0
pod/redis-c46d5dffc-xrk5m      1/1     Running   0
```

All required services were running and ready.

The Kubernetes Services remained available as `ClusterIP` services:

```text
NAME         TYPE        PORT(S)
events       ClusterIP  8081/TCP
gateway      ClusterIP  8080/TCP
payments     ClusterIP  8082/TCP
postgres     ClusterIP  5432/TCP
redis        ClusterIP  6379/TCP
```

## Conclusion

In this lab, QuickTicket was migrated from Docker Compose to Kubernetes manifests and successfully deployed to a local k3d cluster.

The lab demonstrated:

* Kubernetes Deployments and Services;
* internal service discovery through Kubernetes Service names;
* local image deployment with `imagePullPolicy: Never`;
* PostgreSQL database initialization;
* exposing the gateway using `kubectl port-forward`;
* Kubernetes Deployment self-healing;
* liveness and readiness probes;
* readiness behavior when Redis becomes unavailable;
* CPU and memory requests and limits;
* the difference between readiness and liveness;
* the difference between Kubernetes automatic reconciliation and Docker Compose manual recovery.

The final application state was healthy, with all five required components running and all application pods reporting `1/1 Ready`.
