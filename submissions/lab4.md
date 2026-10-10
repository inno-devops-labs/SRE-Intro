# Lab 4

## Task 1

I created a local k3d cluster and checked that the node was ready.

```bash
/tmp/bin/k3d cluster create quickticket
/tmp/bin/kubectl get nodes
```

```text
NAME                       STATUS   ROLES           AGE     VERSION
k3d-quickticket-server-0   Ready    control-plane   47s     v1.35.5+k3s1
```

Then I built the three app images, imported them into k3d, and applied the manifests from `k8s/`.

```bash
cd app
docker build -t quickticket-gateway:v1 ./gateway
docker build -t quickticket-events:v1 ./events
docker build -t quickticket-payments:v1 ./payments
/tmp/bin/k3d image import quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 -c quickticket

cd ..
/tmp/bin/kubectl apply -f k8s/
/tmp/bin/kubectl get pods,svc
```

```text
NAME                            READY   STATUS    RESTARTS   AGE
pod/events-75c9b6f574-ncdrz     1/1     Running   0          18s
pod/gateway-7cd55d8774-bmlds    1/1     Running   0          28s
pod/payments-d7dc94485-bvrk8    1/1     Running   0          17s
pod/postgres-78489d7f5f-wx4xw   1/1     Running   0          17s
pod/redis-6fcfb5475d-bd7lt      1/1     Running   0          17s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.106.137   <none>        8081/TCP   18s
service/gateway      ClusterIP   10.43.145.3     <none>        8080/TCP   18s
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    66s
service/payments     ClusterIP   10.43.71.130    <none>        8082/TCP   17s
service/postgres     ClusterIP   10.43.6.70      <none>        5432/TCP   17s
service/redis        ClusterIP   10.43.225.39    <none>        6379/TCP   17s
```

After that I loaded the seed data and tested the stack through port-forward.

```bash
/tmp/bin/kubectl exec -i $(/tmp/bin/kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql

/tmp/bin/kubectl port-forward svc/gateway 3080:8080 &
curl -s http://localhost:3080/events
curl -s http://localhost:3080/health
```

```text
[{"id":1,"name":"Go Conference 2026","venue":"Main Hall A","date":"2026-09-15T09:00:00+00:00","total_tickets":100,"price_cents":5000,"available":76},{"id":4,"name":"Python Workshop","venue":"Lab 301","date":"2026-09-22T14:00:00+00:00","total_tickets":25,"price_cents":2000,"available":16},{"id":2,"name":"SRE Meetup","venue":"Room 204","date":"2026-10-01T18:00:00+00:00","total_tickets":30,"price_cents":0,"available":23},{"id":5,"name":"Kubernetes Deep Dive","venue":"Auditorium B","date":"2026-10-10T10:00:00+00:00","total_tickets":80,"price_cents":8000,"available":65},{"id":3,"name":"Cloud Native Summit","venue":"Expo Center","date":"2026-11-20T10:00:00+00:00","total_tickets":500,"price_cents":15000,"available":480}]
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

Then I deleted the gateway pod and watched Kubernetes recreate it.

```bash
/tmp/bin/kubectl delete pod -l app=gateway
/tmp/bin/kubectl get pods -w
```

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-75c9b6f574-ncdrz     1/1     Running   0          81s
gateway-7cd55d8774-bmlds    1/1     Running   0          81s
payments-d7dc94485-bvrk8    1/1     Running   0          80s
postgres-78489d7f5f-wx4xw   1/1     Running   0          80s
redis-6fcfb5475d-bd7lt      1/1     Running   0          80s
gateway-7cd55d8774-bmlds    1/1     Terminating   0          83s
gateway-7cd55d8774-bmlds    0/1     Completed     0          84s
gateway-7cd55d8774-rgv8r    0/1     Pending       0          0s
gateway-7cd55d8774-rgv8r    0/1     ContainerCreating   0          0s
gateway-7cd55d8774-rgv8r    0/1     Running             0          1s
gateway-7cd55d8774-rgv8r    1/1     Running             0          7s
```

Kubernetes took about `11 seconds` to recreate the deleted gateway pod and make the new one ready. In lab 1 with docker compose I had to start the service myself, but here Kubernetes replaced it automatically.

## Task 2

I added probes and resource limits to the manifests. Here is the `kubectl describe` output for the gateway pod.

```bash
/tmp/bin/kubectl describe pod -l app=gateway | grep -A 5 "Liveness\|Readiness"
```

```text
Liveness:   http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
Readiness:  http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
Environment:
  EVENTS_URL:          http://events:8081
  PAYMENTS_URL:        http://payments:8082
  GATEWAY_TIMEOUT_MS:  5000
```

For the readiness check on `events`, I deleted `redis` and kept it down for a few more seconds so the readiness change had time to show up.

```bash
/tmp/bin/kubectl delete pod -l app=redis
/tmp/bin/kubectl get pods -w
```

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-f95fc7f64-zwhhr      1/1     Running   0          57s
gateway-7cd55d8774-rgv8r    1/1     Running   0          2m55s
payments-d7dc94485-bvrk8    1/1     Running   0          4m18s
postgres-78489d7f5f-wx4xw   1/1     Running   0          4m18s
redis-6fcfb5475d-6k2vt      1/1     Running   0          37s
redis-6fcfb5475d-6k2vt      1/1     Terminating   0          39s
redis-6fcfb5475d-d5lv2      0/1     Pending       0          0s
redis-6fcfb5475d-d5lv2      0/1     ContainerCreating   0          0s
redis-6fcfb5475d-d5lv2      0/1     Terminating         0          1s
events-f95fc7f64-zwhhr      0/1     Running             0          78s
redis-6fcfb5475d-z7zpx      0/1     Pending             0          0s
redis-6fcfb5475d-z7zpx      1/1     Running             0          1s
gateway-7cd55d8774-rgv8r    0/1     Running             0          3m22s
```

The readiness probe on `events` showed the dependency failure.

```bash
/tmp/bin/kubectl describe pod -l app=events | grep -A 4 "Readiness"
```

```text
Readiness:  http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=1
Environment:
  DB_HOST:           postgres
  DB_PORT:           5432
--
Warning  Unhealthy  32s (x2 over 33s)  kubelet  spec.containers{events}: Readiness probe failed
```

I also checked allocated resources on the node.

```bash
/tmp/bin/kubectl describe node $(/tmp/bin/kubectl get nodes -o name | head -1) | grep -A 10 "Allocated resources"
```

```text
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (2%)   1 (5%)
  memory             460Mi (2%)  1450Mi (9%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
```

Liveness failure means Kubernetes restarts the pod. Readiness failure means the pod stays alive, but Kubernetes stops sending traffic to it. For database connectivity I would use readiness, not liveness, because restarting the app does not fix a broken database and only makes recovery noisier.

## Bonus Task

I turned the raw manifests into a Helm chart under `k8s/chart`.

`Chart.yaml`

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
```

`values.yaml`

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

Then I removed the raw manifests and installed the chart with Helm.

```bash
/tmp/bin/kubectl delete -f k8s/gateway.yaml -f k8s/events.yaml -f k8s/payments.yaml -f k8s/postgres.yaml -f k8s/redis.yaml
/tmp/bin/helm install quickticket k8s/chart/
/tmp/bin/helm list
/tmp/bin/kubectl get pods
```

```text
NAME        NAMESPACE   REVISION   UPDATED                                 STATUS    CHART             APP VERSION
quickticket default     1          2026-06-20 00:30:58.727204064 +0300 MSK deployed  quickticket-0.1.0
```

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-784bfcb5b7-6h65p     1/1     Running   0          18s
gateway-7cd55d8774-qzzkd    1/1     Running   0          18s
payments-d7dc94485-4qhjd    1/1     Running   0          18s
postgres-78489d7f5f-qzhkv   1/1     Running   0          18s
redis-6fcfb5475d-lqclr      1/1     Running   0          18s
```

I also installed monitoring with Helm.

```bash
/tmp/bin/helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
/tmp/bin/helm install monitoring prometheus-community/kube-prometheus-stack \
  --set grafana.adminPassword=admin \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false
/tmp/bin/helm list
/tmp/bin/kubectl get pods
```

```text
NAME        NAMESPACE   REVISION   UPDATED                                 STATUS    CHART                        APP VERSION
monitoring  default     1          2026-06-20 00:31:30.251672601 +0300 MSK deployed  kube-prometheus-stack-86.3.2 v0.91.0
quickticket default     1          2026-06-20 00:30:58.727204064 +0300 MSK deployed  quickticket-0.1.0
```

```text
NAME                                                     READY   STATUS    RESTARTS   AGE
alertmanager-monitoring-kube-prometheus-alertmanager-0   2/2     Running   0          62s
events-784bfcb5b7-6h65p                                  1/1     Running   0          109s
gateway-7cd55d8774-qzzkd                                 1/1     Running   0          109s
monitoring-grafana-6f784bf566-ghxjm                      3/3     Running   0          68s
monitoring-kube-prometheus-operator-748cc88c88-fgd62     1/1     Running   0          68s
monitoring-kube-state-metrics-6b8f7fb688-tf8hl           1/1     Running   0          68s
monitoring-prometheus-node-exporter-fq78j                1/1     Running   0          68s
payments-d7dc94485-4qhjd                                 1/1     Running   0          109s
postgres-78489d7f5f-qzhkv                                1/1     Running   0          109s
prometheus-monitoring-kube-prometheus-prometheus-0       2/2     Running   0          62s
redis-6fcfb5475d-lqclr                                   1/1     Running   0          109s
```

In this run `kube-prometheus-stack` created `6` monitoring pods: Alertmanager, Grafana, Prometheus Operator, kube-state-metrics, node-exporter, and Prometheus.
