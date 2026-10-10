*Emil Nabiullin, e.nabiullin@innopolis.university*
---
# Lab 4 - Kubernetes

## Repository state

The repository already contained completed Kubernetes manifests and a Helm chart.

- Raw manifests: `k8s/quickticket/` and `k8s/gateway/`
- Monitoring manifests: `k8s/monitoring/`
- Helm chart: `k8s/chart/`

At verification time the live application was already running in namespace `quickticket` from the Helm release `quickticket`, so I did not overwrite the existing resources with raw manifests again. I first checked the cluster state and then verified the running deployment.

## Cluster state

I used the `kubectl` binary from the running `k3d` server container because the host shell did not have `kubectl` in `PATH`.

```bash
docker exec k3d-quickticket-server-0 kubectl get nodes -o wide
```

```text
NAME                       STATUS   ROLES           AGE   VERSION        INTERNAL-IP   EXTERNAL-IP   OS-IMAGE           KERNEL-VERSION          CONTAINER-RUNTIME
k3d-quickticket-server-0   Ready    control-plane   41m   v1.35.5+k3s1   172.30.0.2    <none>        K3s v1.35.5+k3s1   6.12.90+deb13.1-amd64   containerd://2.2.3-k3s1
```

```bash
docker exec k3d-quickticket-server-0 kubectl get pods -n quickticket -o wide
docker exec k3d-quickticket-server-0 kubectl get svc -n quickticket
```

```text
NAME                        READY   STATUS    RESTARTS   AGE   IP           NODE
events-64d7b45f69-crmcb     1/1     Running   3          16m   10.42.0.39   k3d-quickticket-server-0
gateway-7b87464dc6-5dwxh    1/1     Running   0          46s   10.42.0.40   k3d-quickticket-server-0
payments-78747d8f5b-nqq6j   1/1     Running   0          16m   10.42.0.15   k3d-quickticket-server-0
postgres-76c7b94bf8-p8l7p   1/1     Running   0          16m   10.42.0.13   k3d-quickticket-server-0
redis-7f4495ff57-5m8wb      1/1     Running   0          50s   10.42.0.38   k3d-quickticket-server-0
```

```text
NAME         TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
events       ClusterIP   10.43.19.80     <none>        8081/TCP   16m
gateway      ClusterIP   10.43.57.146    <none>        8080/TCP   16m
payments     ClusterIP   10.43.182.142   <none>        8082/TCP   16m
postgres     ClusterIP   10.43.144.78    <none>        5432/TCP   16m
redis        ClusterIP   10.43.134.245   <none>        6379/TCP   16m
```

The application is exposed through Traefik and the configured `HTTPRoute`, so the gateway can be reached directly on host port `8080`.

```bash
curl -s http://localhost:8080/health
curl -s http://localhost:8080/events
```

```json
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

```json
[{"id":1,"name":"Jazz Night","venue":"Blue Hall","date":"2025-12-20","total_tickets":100,"price_cents":2500,"available":100},{"id":2,"name":"Rock Arena","venue":"Stadium A","date":"2026-01-15","total_tickets":500,"price_cents":4500,"available":500},{"id":3,"name":"Classical Evening","venue":"City Theater","date":"2026-02-10","total_tickets":200,"price_cents":3000,"available":200}]
```

## Manifests

The raw manifests are separated by concern:

- `k8s/quickticket/postgres.yml`
- `k8s/quickticket/redis.yml`
- `k8s/quickticket/events.yml`
- `k8s/quickticket/payments.yml`
- `k8s/gateway/gateway.yml`
- `k8s/gateway/http-route.yml`

The important implementation details are:

- `events`, `payments`, `postgres`, and `redis` run as `Deployment` + `Service`
- `gateway` is exposed through Traefik Gateway API resources
- probes are defined for `events`, `payments`, and `gateway`
- CPU and memory requests/limits are set for every application container

For example, the live deployment for `gateway` shows both readiness and liveness probes:

```bash
docker exec k3d-quickticket-server-0 kubectl describe deploy gateway -n quickticket
```

```text
Containers:
 gateway:
  Image:      quickticket-gateway:v1
  Port:       8080/TCP
  Limits:
    cpu:     500m
    memory:  256Mi
  Requests:
    cpu:      100m
    memory:   128Mi
  Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
  Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
```

Node allocation also reflects these requests and limits:

```bash
docker exec k3d-quickticket-server-0 kubectl describe node k3d-quickticket-server-0
```

```text
Allocated resources:
  Resource           Requests      Limits
  --------           --------      ------
  cpu                1735m (43%)   1800m (45%)
  memory             2476Mi (15%)  2816Mi (17%)
```

## Self-healing

I deleted the only running `gateway` pod. Kubernetes recreated it automatically from the Deployment ReplicaSet.

```bash
docker exec k3d-quickticket-server-0 kubectl get pod -l app=gateway -n quickticket
docker exec k3d-quickticket-server-0 kubectl delete pod gateway-7b87464dc6-wzwdf -n quickticket
docker exec k3d-quickticket-server-0 kubectl rollout status deployment/gateway -n quickticket --timeout=60s
docker exec k3d-quickticket-server-0 kubectl get pod -l app=gateway -n quickticket -o wide
```

```text
NAME                       READY   STATUS    RESTARTS   AGE
gateway-7b87464dc6-wzwdf   1/1     Running   0          14m
```

```text
pod "gateway-7b87464dc6-wzwdf" deleted
deployment "gateway" successfully rolled out
```

```text
NAME                      READY   STATUS    RESTARTS   AGE   IP           NODE
gateway-7b87464dc6-5dwxh  1/1     Running   0          27s   10.42.0.40   k3d-quickticket-server-0
```

After the new pod became ready, the endpoint was healthy again:

```bash
curl -s http://localhost:8080/health
```

```json
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

Recovery time was below one minute in this run. Compared to `docker compose`, this is automatic: no manual restart command was needed.

## Redis outage experiment

To check the probe behavior, I temporarily scaled `redis` to zero replicas and then restored it.

```bash
docker exec k3d-quickticket-server-0 kubectl scale deployment/redis -n quickticket --replicas=0
docker exec k3d-quickticket-server-0 kubectl get pods -n quickticket
docker exec k3d-quickticket-server-0 kubectl describe pod events-64d7b45f69-crmcb -n quickticket
```

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-64d7b45f69-crmcb     0/1     Running   3          15m
gateway-7b87464dc6-5dwxh    1/1     Running   0          35s
payments-78747d8f5b-nqq6j   1/1     Running   0          15m
postgres-76c7b94bf8-p8l7p   1/1     Running   0          15m
```

```text
State:          Running
Ready:          False
Restart Count:  3
Liveness:       http-get http://:8081/health delay=15s timeout=1s period=10s #success=1 #failure=3
Readiness:      http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
Events:
  Warning  Unhealthy  27s                kubelet  Readiness probe failed: HTTP probe failed with statuscode: 503
  Warning  Unhealthy  17s                kubelet  Liveness probe failed: HTTP probe failed with statuscode: 503
  Normal   Killing    17s                kubelet  Container events failed liveness probe, will be restarted
```

This confirms the expected behavior:

- readiness failed first, so the pod stopped being ready for traffic
- liveness also failed, so Kubernetes restarted the container
- this is a good example of why dependency checks are usually safer in readiness probes than in liveness probes

After restoring Redis, the application recovered:

```bash
docker exec k3d-quickticket-server-0 kubectl scale deployment/redis -n quickticket --replicas=1
docker exec k3d-quickticket-server-0 kubectl rollout status deployment/redis -n quickticket --timeout=60s
docker exec k3d-quickticket-server-0 kubectl rollout status deployment/events -n quickticket --timeout=60s
curl -s http://localhost:8080/events
```

```text
deployment.apps/redis scaled
deployment "redis" successfully rolled out
deployment "events" successfully rolled out
```

```json
[{"id":1,"name":"Jazz Night","venue":"Blue Hall","date":"2025-12-20","total_tickets":100,"price_cents":2500,"available":100},{"id":2,"name":"Rock Arena","venue":"Stadium A","date":"2026-01-15","total_tickets":500,"price_cents":4500,"available":500},{"id":3,"name":"Classical Evening","venue":"City Theater","date":"2026-02-10","total_tickets":200,"price_cents":3000,"available":200}]
```

## Helm chart

The Helm chart in this repository is stored in `k8s/chart`.

`helm lint` passed:

```bash
docker run --rm -v /home/andrey-debian/Projects/IU/SRE/Emil/SRE-Intro:/work -w /work alpine/helm lint k8s/chart
```

```text
==> Linting k8s/chart
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
```

The cluster contains the expected Helm releases:

```bash
docker cp k3d-quickticket-server-0:/output/kubeconfig.yaml /tmp/quickticket-kubeconfig.yaml
docker run --rm --network container:k3d-quickticket-server-0 \
  -v /tmp/quickticket-kubeconfig.yaml:/kubeconfig \
  alpine/helm list -A --kubeconfig /kubeconfig
```

```text
NAME         NAMESPACE     REVISION  UPDATED                                   STATUS    CHART                         APP VERSION
monitoring   monitoring    1         2026-06-26 22:45:01.587103624 +0300 +0300 deployed  kube-prometheus-stack-87.2.1  v0.92.0
quickticket  quickticket   1         2026-06-26 22:43:48.555246008 +0300 +0300 deployed  quickticket-0.1.0
traefik      kube-system   1         2026-06-26 19:16:14.666399912 +0000 UTC   deployed  traefik-39.0.701+up39.0.7     v3.6.12
traefik-crd  kube-system   1         2026-06-26 19:16:11.755253489 +0000 UTC   deployed  traefik-crd-39.0.701+up39.0.7 v3.6.12
```

## Monitoring

The monitoring stack is also deployed:

```bash
docker exec k3d-quickticket-server-0 kubectl get pods -n monitoring
docker exec k3d-quickticket-server-0 kubectl get servicemonitor -n quickticket
```

```text
NAME                                                         READY   STATUS    RESTARTS   AGE
alertmanager-monitoring-kube-prometheus-alertmanager-0       2/2     Running   0          16m
monitoring-grafana-557f4c758d-8wwkt                          3/3     Running   0          16m
monitoring-kube-prometheus-operator-66887b4db8-zl9bf         1/1     Running   0          16m
monitoring-kube-state-metrics-56dcb84df5-s7j82              1/1     Running   0          16m
monitoring-prometheus-node-exporter-b2fjv                   1/1     Running   0          16m
prometheus-monitoring-kube-prometheus-prometheus-0          2/2     Running   0          16m
```

```text
NAME          AGE
quickticket   16m
```

So the cluster now has:

- the QuickTicket application in namespace `quickticket`
- the Helm release `quickticket`
- the Helm release `monitoring`
- a `ServiceMonitor` for Prometheus discovery

## Result

The repository and cluster were already close to complete. The final check showed that:

- the manifests in `k8s/` are present and match the deployed application
- the Helm chart in `k8s/chart` is valid and deployed
- the monitoring stack is installed and running
- the application serves requests through the gateway
- Kubernetes self-healing works
- removing Redis causes `events` readiness failure and also liveness-triggered restarts in the current probe configuration
