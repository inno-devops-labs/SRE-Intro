# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

## Task 1 — Write Manifests & Deploy to k3d

**Cluster created:**
```
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   9s    v1.35.5+k3s1
```

**Images built and imported into k3d:**
```
quickticket-events:v1            f6a99d256264        234MB
quickticket-gateway:v1           b5e7c81adcb7        215MB
quickticket-payments:v1          108f13cc87e1        213MB
Successfully imported 3 image(s) into 1 cluster(s)
```

**Manifests written from scratch** in `k8s/`: `postgres.yaml`, `redis.yaml`, `events.yaml`, `payments.yaml`, `gateway.yaml` — each a Deployment + Service, env vars mirrored from `docker-compose.yaml`, `imagePullPolicy: Never` on the three custom images.

**All pods and services running:**
```
NAME                       READY   STATUS    RESTARTS   AGE
events-6c4df7d6-mgwqh      1/1     Running   0          6m48s
gateway-6fc44f68c5-5p97h   1/1     Running   0          5m12s
payments-58fb468db-d96r8   1/1     Running   0          6m48s
postgres-7c7ffc4b-hwzgg    1/1     Running   0          7m46s
redis-c46d5dffc-tzlcg      1/1     Running   0          7m46s

NAME                 TYPE        CLUSTER-IP      PORT(S)
service/events       ClusterIP   10.43.217.227   8081/TCP
service/gateway      ClusterIP   10.43.121.85    8080/TCP
service/payments     ClusterIP   10.43.180.90    8082/TCP
service/postgres     ClusterIP   10.43.249.65    5432/TCP
service/redis        ClusterIP   10.43.83.116    6379/TCP
```

**Full stack verified via port-forward:**
```
$ curl -s http://localhost:3080/events   → returns event list (Go Conference, SRE Meetup, etc.)
$ curl -s http://localhost:3080/health
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```
(Note: seed.sql was applied twice by mistake, producing duplicate rows in `/events`; harmless for this lab, and later corrected with `TRUNCATE ... RESTART IDENTITY CASCADE` before re-seeding.)

**Self-healing test:**
```
$ kubectl delete pod -l app=gateway
pod "gateway-6fc44f68c5-8ptnv" deleted
NAME                       READY   STATUS    RESTARTS   AGE
gateway-6fc44f68c5-5p97h   1/1     Running   0          1s
```

**How long did K8s take to recreate the deleted pod? How does this compare to docker-compose restart?**
The new pod reached `Running` in about 1 second after deletion — the Deployment controller detected the missing replica and scheduled a replacement almost instantly. In docker-compose (Lab 1), a stopped container stayed down until `docker compose start` was run manually; there was no controller watching desired state. Kubernetes' Deployment/ReplicaSet model makes recovery automatic and near-immediate, without any manual intervention.

## Task 2 — Probes & Resource Limits

**Probes configured** (gateway shown, same pattern applied to events and payments):
```
Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s failureThreshold=3
Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s failureThreshold=2
```

**Readiness/liveness failure observed during Redis outage:**
Scaling Redis to 0 replicas caused `events` and `gateway` readiness to drop (`0/1 Ready`) as expected — but since both liveness and readiness were pointed at the same `/health` endpoint, and that endpoint returned 503 while Redis was down, the **liveness** probe also failed and Kubernetes killed and restarted the containers repeatedly (`RESTARTS` climbing to 5):
```
Warning  Unhealthy  ... Liveness probe failed: HTTP probe failed with statuscode: 503
Normal   Killing    ... Container events failed liveness probe, will be restarted
```
This was a useful accidental demonstration of the exact pitfall the lab warns about: a liveness probe should never depend on an external service, only on whether the process itself is alive. Restarting the container does nothing to fix an unavailable Redis — it just adds churn. The Helm chart templates (bonus task) were corrected to only use `readinessProbe`, not `livenessProbe`, for this reason.

**Resource limits applied and reflected in node allocation:**
```
Resource           Requests    Limits
cpu                350m (2%)   600m (3%)
memory             332Mi (2%)  938Mi (6%)
```

**What's the difference between liveness and readiness probe failure? Which one should you use for checking database connectivity, and why?**
A readiness failure removes the pod from the Service's endpoint list — no traffic is routed to it, but the container keeps running untouched. A liveness failure kills and restarts the container. For database/cache connectivity checks, readiness is the correct choice: if a dependency is down, the fix is to stop sending traffic to the pod, not to restart a healthy process that can't fix someone else's outage. Using liveness for this (as this setup initially did) causes needless restart loops, as observed above.

## Bonus Task — Helm Chart

**Chart scaffold** created at `k8s/chart/` with `Chart.yaml`, `values.yaml`, and templates for all five components (postgres, redis, events, payments, gateway), each parameterized via `{{ .Values.x }}`.

**Raw manifests removed, chart installed:**
```
$ helm install quickticket k8s/chart/
STATUS: deployed
REVISION: 1

$ helm list
NAME          NAMESPACE   REVISION   STATUS     CHART              APP VERSION
quickticket   default     1          deployed   quickticket-0.1.0
```

**Pods running after Helm install:**
```
NAME                       READY   STATUS    RESTARTS   AGE
events-57c98cc577-csm5x    1/1     Running   0          15s
gateway-774bb76c4f-d27kt   1/1     Running   0          15s
payments-7cccc4f684-4rkm6  1/1     Running   0          15s
postgres-7c7ffc4b-874d5    1/1     Running   0          15s
redis-c46d5dffc-fqqv8      1/1     Running   0          15s
```

All five Deployments and Services came up clean on the first Helm install, with no crashes or restarts.

Monitoring stack (kube-prometheus-stack) was not installed for this bonus — optional per the lab spec.
