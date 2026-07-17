# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

## Task 1 — Write Manifests & Deploy to k3d

Command:

```bash
kubectl get nodes
```

Output:

```bash
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   34s   v1.35.5+k3s1
```

### Import images to cluster

Command:
```
k3d image import quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 -c quickticket
```

Output:
```
INFO[0000] Importing image(s) into cluster 'quickticket' 
INFO[0000] Saving 3 image(s) from runtime...            
INFO[0002] Importing images into nodes...               
INFO[0002] Importing images from tarball '/k3d/images/k3d-quickticket-images-20260618203536.tar' into node 'k3d-quickticket-server-0'... 
INFO[0005] Removing the tarball(s) from image volume... 
INFO[0006] Removing k3d-tools node...                   
INFO[0007] Successfully imported image(s)               
INFO[0007] Successfully imported 3 image(s) into 1 cluster(s) 
```

#### Verify

Command:
```bash
docker images | grep quickticket
```

Output:
```bash
quickticket-events                          v1              f62566aafaef   7 days ago      233MB
quickticket-gateway                         v1              126b17ad6a68   7 days ago      214MB
quickticket-payments                        v1              4523772c0a2f   7 days ago      212MB
```

### Deploy to Kubernetes

Command:

```bash
kubectl get pods
```


Output:

```bash
NAME                       READY   STATUS    RESTARTS   AGE
events-7f6b68c586-rdf9l    1/1     Running   0          20m
gateway-6fc44f68c5-dplhr   1/1     Running   0          20m
payments-58fb468db-vslnm   1/1     Running   0          20m
postgres-7c7ffc4b-bdrwl    1/1     Running   0          20m
redis-c46d5dffc-fszl9      1/1     Running   0          20m
```

Command:
```bash
kubectl get svc
```

Output:
```bash
NAME         TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
events       ClusterIP   10.43.89.202    <none>        8081/TCP   22m
gateway      ClusterIP   10.43.99.0      <none>        8080/TCP   22m
kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    44m
payments     ClusterIP   10.43.152.180   <none>        8082/TCP   22m
postgres     ClusterIP   10.43.34.162    <none>        5432/TCP   22m
redis        ClusterIP   10.43.234.165   <none>        6379/TCP   22m
```


### Verify system works

Command:
```
kubectl port-forward svc/gateway 3080:8080
```

Command:
```bash
curl http://localhost:3080/events
```

Output:
```bash
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

Command:
```bash
curl http://localhost:3080/health
```

Output:
```
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

### Self-healing test

Command:
```
kubectl delete pod -l app=gateway
kubectl get pods -w
```

Output:
```
NAME                       READY   STATUS    RESTARTS   AGE
events-7f6b68c586-rdf9l    1/1     Running   0          37m
gateway-6fc44f68c5-tzpcm   1/1     Running   0          6s
payments-58fb468db-vslnm   1/1     Running   0          37m
postgres-7c7ffc4b-bdrwl    1/1     Running   0          37m
redis-c46d5dffc-fszl9      1/1     Running   0          37m
```

### How long did K8s take to recreate the deleted pod? How does this compare to docker-compose restart?

Kubernetes recreated the pod in approximately ~1 second.

This is expected in a local k3d cluster because:
- no image pulling is required
- no VM provisioning overhead exists

Compared to docker-compose, where manual restart is required,
Kubernetes provides automatic self-healing and near-instant recovery.

## Task 2 — Probes & Resource Limits

### kubectl describe pod output showing probes configured\
Command:
```
kubectl describe pod -l app=gateway | grep -A 5 "Liveness\|Readiness"
```

Output:
```
 Liveness:       http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:      http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      EVENTS_URL:          http://events:8081
      PAYMENTS_URL:        http://payments:8082
      GATEWAY_TIMEOUT_MS:  5000
    Mounts:
      /var/run/secrets/kubernetes.io/serviceaccount from kube-api-access-qkc44 (ro)
Conditions:
  Type                        Status
  PodReadyToStartContainers   True 
  Initialized                 True 
--
  Warning  Unhealthy  7s    kubelet            Readiness probe failed: Get "http://10.42.0.24:8080/health": dial tcp 10.42.0.24:8080: connect: connection refused
```

### Redis deletion

Command:
```
kubectl delete pod -l app=redis
```
Output:
```
pod "redis-c46d5dffc-mr2x7" deleted
```

Command:
```
kubectl describe pod -l app=events | grep -A 10 Readiness
```
Output:
```
Readiness:      http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      DB_HOST:      postgres
      DB_PORT:      5432
      DB_NAME:      quickticket
      DB_USER:      quickticket
      DB_PASSWORD:  quickticket
      REDIS_HOST:   redis
      REDIS_PORT:   6379
    Mounts:
      /var/run/secrets/kubernetes.io/serviceaccount from kube-api-access-8v5hz (ro)
--
  Warning  Unhealthy  92s    kubelet            Readiness probe failed: Get "http://10.42.0.25:8081/health": context deadline exceeded (Client.Timeout exceeded while awaiting headers)
```

### After adding resourse limit
```
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                350m (2%)   600m (5%)
  memory             332Mi (4%)  938Mi (12%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
Events:              <none>
```
### What's the difference between liveness and readiness probe failure? Which one should you use for checking database connectivity, and why?

Liveness probe checks if container is alive and restarts it if it fails. Readiness probe checks if container can serve traffic and removes it from service endpoints if it fails. For database connectivity, use readiness probe because DB issues mean the app should not receive traffic, but should not be restarted.

## Bonus Task — Helm Chart 

### Chart.yaml
```
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
```

### values.yaml
```
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
  redis:
    host: redis
    port: 6379

payments:
  replicas: 1
  image: quickticket-payments:v1
  failureRate: "0.0"
  latencyMs: "0"
```

### Output of helm list showing the installed release

```
NAME            NAMESPACE       REVISION        UPDATED                                 STATUS          CHART                   APP VERSION
quickticket     default         3               2026-06-18 23:24:29.170569668 +0300 MSK deployed        quickticket-0.1.0                  
```

### Output of kubectl get pods after Helm install

```
NAME                        READY   STATUS    RESTARTS   AGE
events-5848d9b878-79kw6     1/1     Running   0          23s
gateway-787c8c8c5-9rtmx     1/1     Running   0          16m
payments-55bbb85b4c-2ndmx   1/1     Running   0          16m
postgres-7c7ffc4b-prl4s     1/1     Running   0          16m
redis-c46d5dffc-s5p2c       1/1     Running   0          16m
```

### If monitoring installed: how many pods did kube-prometheus-stack create?

The kube-prometheus-stack Helm chart created 6 main pods:
- Prometheus
- Grafana
- Alertmanager
- kube-state-metrics
- node-exporter
- Prometheus Operator