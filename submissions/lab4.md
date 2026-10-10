# Lab 4 Report — Kubernetes: Deploy QuickTicket to a Cluster

## Task 1 — Write Manifests & Deploy to k3d (6 pts)

### 1. Output of kubectl get nodes
```bash
$ kubectl get nodes
```
```text
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   14s   v1.35.5+k3s1
```

### 2. Output of kubectl get pods,svc showing all running
```bash
$ kubectl get pods
```
```text
NAME                      READY   STATUS    RESTARTS   AGE
postgres-7c7ffc4b-x5cm7   1/1     Running   0          25s
redis-c46d5dffc-dzm6j     1/1     Running   0          15s
```

```bash
$ kubectl get svc
```
```text
NAME         TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    7m30s
postgres     ClusterIP   10.43.237.225   <none>        5432/TCP   36s
redis        ClusterIP   10.43.178.148   <none>        6379/TCP   25s
```

### 3. Output of curl localhost:3080/events via port-forward (proving the full stack works)
```bash
$ curl -s http://localhost:3080/events | python -m json.tool
```
```text
Handling connection for 3080
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

### 4. Output of kubectl get pods -w during pod deletion — showing auto-recovery
```bash
$ kubectl delete pod -l app=gateway
```
```text
pod "gateway-6fc44f68c5-xh74z" deleted from default namespace
```

```bash
$ kubectl get pods -w
```
```text
NAME                       READY   STATUS    RESTARTS   AGE
events-859d5c5c98-vhxg4    1/1     Running   0          9m35s
gateway-6fc44f68c5-j9gnc   1/1     Running   0          3s
payments-58fb468db-k7pt2   1/1     Running   0          9m35s
postgres-7c7ffc4b-x5cm7    1/1     Running   0          14m
redis-c46d5dffc-dzm6j      1/1     Running   0          14m
```

### 5. Answer: "How long did K8s take to recreate the deleted pod? How does this compare to docker-compose restart?"
It took Kubernetes approximately **1–2 seconds** to completely recreate and spin up the new gateway pod. This speed is achieved because the container image (quickticket-gateway:v1) was already cached locally inside the k3d cluster nodes, combined with the imagePullPolicy: Never setting.

**Comparison with docker-compose:**

**Automation:** In Lab 1, if a container was deleted or stopped, it required manual intervention (docker compose start) to bring it back online. In Kubernetes, the Deployment controller continuously monitors the cluster state and automatically triggers a recreation to match the declared state (1 replica).

**Resilience to Object Deletion:** If a container is completely removed via docker rm, docker-compose loses track of it until the stack is manually recreated. Kubernetes, on the other hand, monitors the application as a high-level state; if the physical Pod object disappears, K8s immediately provisions a brand new Pod infrastructure from scratch to maintain system availability.


## Task 2 — Probes & Resource Limits (4 pts)

### 6. kubectl describe pod output showing probes configured
```bash
$ kubectl describe pod -l app=gateway | grep -A 7 -B 2 "Liveness"
```
```text
      cpu:      50m
      memory:   64Mi
    Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      EVENTS_URL:          http://events:8081
      PAYMENTS_URL:        http://payments:8082
      GATEWAY_TIMEOUT_MS:  5000
    Mounts:
      /var/run/secrets/kubernetes.io/serviceaccount from kube-api-access-tpmpd (ro)
--
  Warning  Unhealthy  2m11s (x2 over 2m12s)  kubelet            Readiness probe failed: Get "http://10.42.0.21:8080/health": dial tcp 10.42.0.21:8080: connect: connection refused
  Warning  Unhealthy  2m6s                   kubelet            Readiness probe failed: HTTP probe failed with statuscode: 503
  Warning  Unhealthy  2m3s                   kubelet            Liveness probe failed: HTTP probe failed with statuscode: 503
```


### 7. Output during Redis deletion showing readiness probe failure (0/1 Ready)
```bash
$ kubectl delete pod -l app=redis && kubectl get pods -w
```
```text
pod "redis-6fcfb5475d-76fv4" deleted from default namespace
NAME                        READY   STATUS    RESTARTS   AGE
events-78696fcf65-fdfnb     1/1     Running   0          3m41s
gateway-7cd55d8774-jwt6p    1/1     Running   0          3m41s
payments-d7dc94485-ktjq2    1/1     Running   0          3m41s
postgres-78489d7f5f-cm9hw   1/1     Running   0          3m41s
redis-6fcfb5475d-9qm7k      1/1     Running   0          1s
```

### 8. kubectl describe node output showing allocated resources
```bash
$ kubectl describe node k3d-quickticket-server-0 | grep -A 7 "Allocated resources"
```
```text
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (3%)   1 (8%)
  memory             460Mi (2%)  1450Mi (9%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
```

### 9. Answer: "What's the difference between liveness and readiness probe failure? Which one should you use for checking database connectivity, and why?"
**The Difference Between Liveness and Readiness Probes:**

**Liveness Probe answers the question:** "Is the container completely frozen or deadlocked?" If this probe fails, Kubernetes assumes the application is dead and takes drastic action: it restarts the container (kills the current pod and spins up a fresh one) to recover from a broken state.

**Readiness Probe answers the question:** "Is the container ready to accept incoming network traffic right now?" If this probe fails, Kubernetes does NOT restart the container. It simply removes the pod from the Service endpoints, meaning it stops routing client traffic to it until the probe starts passing again.

**Which one to use for Database Connectivity?**
For checking database (or Redis) connectivity, you must strictly use a Readiness probe.

**Why?**
If your database goes down for quick maintenance, gets overloaded, or experiences a network blip, all your application pods will simultaneously lose connection to it.

If you use a **Liveness probe** for DB checks: Kubernetes will assume your application code itself is broken. It will start aggressively and infinitely restarting every single pod across the cluster, throwing the entire system into a brutal CrashLoopBackOff cycle. This wastes huge amounts of CPU on container restarts and does absolutely nothing to help the external database recover.

If you use a **Readiness probe** for DB checks: Kubernetes handles it much smarter. It will simply cut off the client traffic to these pods. The application containers safely stay alive without wasting resources on restarts, continuously retrying to connect to the database in the background. As soon as the database comes back online, the readiness probe automatically passes, and Kubernetes gracefully routes traffic back to the services—with zero destructive restarts.

## Bonus Task — Helm Chart (2 pts)

### 10. Your Chart.yaml and values.yaml
**Chart.yaml:**
```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
```
**values.yaml:**
```yaml
gateway:
  replicas: 1
  image: quickticket-gateway:v1
events:
  replicas: 1
  image: quickticket-events:v1
  db:
    host: postgres
    port: 5432
    name: quickticket
    user: quickticket
    password: quickticket
payments:
  replicas: 1
  image: quickticket-payments:v1
  failureRate: "0.0"
  latencyMs: "0"
```

### 11. Output of helm list showing the installed release
```bash
$ helm list
```
```text
NAME            NAMESPACE       REVISION        UPDATED                                 STATUS          CHART                   APP VERSION
quickticket     default         1               2026-06-12 14:14:09.1272159 +0300 MSK   deployed        quickticket-0.1.0                  
```

### 12. Output of kubectl get pods after Helm install
```bash
$ kubectl get pods
```
```text
NAME                        READY   STATUS    RESTARTS   AGE
events-d6c4bf4cf-q6f4z      1/1     Running   0          4s
gateway-774b57ddc6-gfscg    1/1     Running   0          4s
payments-54dbf5cd95-p5kkb   1/1     Running   0          4s
postgres-78489d7f5f-crrn7   1/1     Running   0          4s
redis-6fcfb5475d-z94mb      1/1     Running   0          4s
```


### 13. If monitoring installed: how many pods did kube-prometheus-stack create?
```bash
$ kubectl get pods
```
```text
NAME                                                     READY   STATUS    RESTARTS   AGE
alertmanager-monitoring-kube-prometheus-alertmanager-0   2/2     Running   0          5m23s
events-d6c4bf4cf-q6f4z                                   1/1     Running   0          6m52s
gateway-774b57ddc6-gfscg                                 1/1     Running   0          6m52s
monitoring-grafana-c4d66cf58-dnczs                       3/3     Running   0          5m32s
monitoring-kube-prometheus-operator-69c79c5d78-76blq     1/1     Running   0          5m32s
monitoring-kube-state-metrics-868694bc4b-6p5sx           1/1     Running   0          5m32s
monitoring-prometheus-node-exporter-r9mfr                1/1     Running   0          5m32s
payments-54dbf5cd95-p5kkb                                1/1     Running   0          6m52s
postgres-78489d7f5f-crrn7                                1/1     Running   0          6m52s
prometheus-monitoring-kube-prometheus-prometheus-0       2/2     Running   0          5m23s
redis-6fcfb5475d-z94mb                                   1/1     Running   0          6m52s
```
The *kube-prometheus-stack* created **exactly 6 pods** to run the complete monitoring infrastructure:

* **alertmanager-monitoring-kube-prometheus-alertmanager-0** - Manages alerts triggered by Prometheus.

* **monitoring-grafana-c4d66cf58-dnczs** - Provides dashboard visualizations for metrics.

* **monitoring-kube-prometheus-operator-69c79c5d78-76blq** - The core operator that manages the lifecycle of Prometheus and abstract K8s CRDs.

* **monitoring-kube-state-metrics-868694bc4b-6p5sx** - Listens to the Kubernetes API server and generates metrics about the state of objects (deployments, pods, etc.).

* **monitoring-prometheus-node-exporter-r9mfr** - Collects host/node-level compute infrastructure metrics (CPU, memory, disk).

* **prometheus-monitoring-kube-prometheus-prometheus-0** - The core time-series database scraping and storing all the metrics.
