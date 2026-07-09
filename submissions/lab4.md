# Task 1

#### 1. Output of `kubectl get nodes`
`NAME                       STATUS   ROLES           AGE     VERSION
`k3d-quickticket-server-0   Ready    control-plane   3m54s   v1.35.5+k3s1

#### 2. Output of `kubectl get pods,svc` showing all running

**kubectl get pods**
`NAME                       READY   STATUS    RESTARTS   AGE`
`events-859d5c5c98-zr4bq    1/1     Running   0          58s`
`gateway-6fc44f68c5-xdfl5   1/1     Running   0          58s`
`payments-58fb468db-4nx47   1/1     Running   0          58s`
`postgres-7c7ffc4b-zl4hk    1/1     Running   0          25m`
`redis-c46d5dffc-jhnvj      1/1     Running   0          25m`

**kubectl get svc**
`NAME         TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE`
`events       ClusterIP   10.43.82.173    <none>        8081/TCP   54s`
`gateway      ClusterIP   10.43.191.138   <none>        8080/TCP   54s`
`kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    44m`
`payments     ClusterIP   10.43.32.126    <none>        8082/TCP   54s`
`postgres     ClusterIP   10.43.195.81    <none>        5432/TCP   25m`
`redis        ClusterIP   10.43.227.39    <none>        6379/TCP   24m`

#### 3. Output of `curl localhost:3080/events` via port-forward (proving the full stack works)

`[`
    `￼{`
        `"id": 1,`
        `"name": "Go Conference 2026",`
        `"venue": "Main Hall A",`
        `"date": "2026-09-15T09:00:00+00:00",`
        `"total_tickets": 100,`
        `"price_cents": 5000,`
        `"available": 100`
    `},`
    `￼{`
        `"id": 4,`
        `"name": "Python Workshop",`
        `"venue": "Lab 301",`
        `"date": "2026-09-22T14:00:00+00:00",`
        `"total_tickets": 25,`
        `"price_cents": 2000,`
        `"available": 25`
    `},`
    `￼{`
        `"id": 2,`
        `"name": "SRE Meetup",`
        `"venue": "Room 204",`
        `"date": "2026-10-01T18:00:00+00:00",`
        `"total_tickets": 30,`
        `"price_cents": 0,`
        `"available": 30`
    `},`
    `￼{`
        `"id": 5,`
        `"name": "Kubernetes Deep Dive",`
        `"venue": "Auditorium B",`
        `"date": "2026-10-10T10:00:00+00:00",`
        `"total_tickets": 80,`
        `"price_cents": 8000,`
        `"available": 80`
    `},`
    `￼{`
        `"id": 3,`
        `"name": "Cloud Native Summit",`
        `"venue": "Expo Center",`
        `"date": "2026-11-20T10:00:00+00:00",`
        `"total_tickets": 500,`
        `"price_cents": 15000,`
        `"available": 500`
    `}`
`]`


#### 4. Output of `kubectl get pods -w` during pod deletion — showing auto-recovery

`NAME                       READY   STATUS    RESTARTS   AGE`
`events-859d5c5c98-zr4bq    1/1     Running   0          20m`
`gateway-6fc44f68c5-qzwxs   1/1     Running   0          2s`
`payments-58fb468db-4nx47   1/1     Running   0          20m`
`postgres-7c7ffc4b-zl4hk    1/1     Running   0          45m`
`redis-c46d5dffc-jhnvj      1/1     Running   0          44m`

#### 5. Answer: "How long did K8s take to recreate the deleted pod? How does this compare to docker-compose restart?"

Kubernetes recreated the deleted pod in just two seconds, since the container image had already been downloaded to the machine. This is the main difference from docker-compose: instead of waiting for a user to manually restart a failed service with docker compose start, Kubernetes continuously monitors the system. As soon as it notices that the number of running pods has dropped below the configured limit, it instantly and automatically starts a new one to replace the deleted one.

# Task 2

#### `kubectl describe pod` output showing probes configured
    Liveness:       http-get http://:8080/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:      http-get http://:8080/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      EVENTS_URL:          http://events:8081
      PAYMENTS_URL:        http://payments:8082
      GATEWAY_TIMEOUT_MS:  5000
    Mounts:


#### Output during Redis deletion showing readiness probe failure (`0/1 Ready`)
**kubectl get pods -w**
`NAME                        READY   STATUS    RESTARTS      AGE`
`events-7bb796d9b4-gfn5v     1/1     Running   0             5s`
`gateway-854488bf7c-7wnwf    1/1     Running   6 (25m ago)   6h32m`
`payments-68dcdf7696-vqxxn   1/1     Running   0             6h32m`
`postgres-7c7ffc4b-zl4hk     1/1     Running   0             7h32m`
`redis-c46d5dffc-7hct2       1/1     Running   0             2m25s`
`redis-c46d5dffc-7hct2       1/1     Terminating   0             2m29s`
`redis-c46d5dffc-dpm7l       0/1     Pending       0             0s`
`redis-c46d5dffc-7hct2       1/1     Terminating   0             2m29s`
`redis-c46d5dffc-dpm7l       0/1     Pending       0             0s`
`redis-c46d5dffc-dpm7l       0/1     ContainerCreating   0             0s`
`redis-c46d5dffc-7hct2       0/1     Completed           0             2m29s`
`redis-c46d5dffc-7hct2       0/1     Completed           0             2m30s`
`redis-c46d5dffc-7hct2       0/1     Completed           0             2m30s`
`redis-c46d5dffc-dpm7l       1/1     Running             0             1s`

**kubectl describe pod -l app=events | grep -A 3 "Readiness"**
    `Readiness:      http-get http://:8081/health delay=0s timeout=1s period=1s #success=1 #failure=2`
    `Environment:`
      `DB_HOST:     postgres`
      `DB_PORT:     5432`
--
  `Warning  Unhealthy  21s   kubelet            spec.containers{events}: Readiness probe failed: Get "http://10.42.0.32:8081/health": dial tcp 10.42.0.32:8081: connect: connection refused`



#### `kubectl describe node` output showing allocated resources

`Allocated resources:`
  `(Total limits may be over 100 percent, i.e., overcommitted.)`
  `Resource           Requests    Limits`
  `--------           --------    ------`
  `cpu                450m (11%)  1 (25%)`
  `memory             460Mi (5%)  1450Mi (18%)`
  `ephemeral-storage  0 (0%)      0 (0%)`
  `hugepages-2Mi      0 (0%)      0 (0%)`
`Events:              <none>`


#### Answer: "What's the difference between liveness and readiness probe failure? Which one should you use for checking database connectivity, and why?"

- A liveness probe checks if a container is alive and restarts it upon failure, whereas a readiness probe checks if a container can accept traffic and removes it from the Service endpoints upon failure without restarting.
- Use a **readiness probe**. If the database drops, a liveness probe will trigger a destructive cycle of endless container restarts, while a readiness probe merely stops traffic routing to the affected pods until the connection gracefully recovers.


# Bonus Task

#### Your `Chart.yaml` and `values.yaml`

**Chart.yaml**
`apiVersion: v2`
`name: quickticket`
`description: QuickTicket SRE learning project`
`version: 0.1.0`

**values.yaml**
`gateway:`
  `replicas: 1`
  `image: quickticket-gateway:v1`
`events:`
  `replicas: 1`
  `image: quickticket-events:v1`
  `db:`
    `host: postgres`
    `port: 5432`
    `name: quickticket`
    `user: quickticket`
    `password: quickticket`
`payments:`
  `replicas: 1`
  `image: quickticket-payments:v1`
  `failureRate: "0.0"`
  `latencyMs: "0"`

#### Output of `helm list` showing the installed release

**helm list**
`NAME       	NAMESPACE	REVISION	UPDATED                                	STATUS  	CHART            	APP VERSION`
`quickticket	default  	1       	2026-06-18 00:40:56.581955825 +0000 UTC	deployed	quickticket-0.1.0`
#### Output of `kubectl get pods` after Helm install

**kubectl get pods**
`NAME                                                     READY   STATUS    RESTARTS   AGE`
`alertmanager-monitoring-kube-prometheus-alertmanager-0   2/2     Running   0          98s`
`events-78696fcf65-5kzpm                                  1/1     Running   0          10m`
`gateway-7cd55d8774-w8v4c                                 1/1     Running   0          10m`
`monitoring-grafana-65749cfb9-k5p7w                       3/3     Running   0          110s`
`monitoring-kube-prometheus-operator-7f87f6796-r8vxb      1/1     Running   0          110s`
`monitoring-kube-state-metrics-b55bfdbfc-rkrq7            1/1     Running   0          110s`
`monitoring-prometheus-node-exporter-ts4jn                1/1     Running   0          110s`
`payments-d7dc94485-pzl4s                                 1/1     Running   0          10m`
`postgres-78489d7f5f-f4j7f                                1/1     Running   0          10m`
`prometheus-monitoring-kube-prometheus-prometheus-0       2/2     Running   0          97s`
`redis-6fcfb5475d-4lrxm                                   1/1     Running   0          10m`



#### If monitoring installed: how many pods did kube-prometheus-stack create?

**`kube-prometheus-stack` created 6 pods in total to deploy the monitoring infrastructure**:

1. `alertmanager` (1 pod) — handles alerting logic.
2. `grafana` (1 pod) — provides dashboards and visualization.
3. `kube-prometheus-operator` (1 pod) — manages the lifecycle of Prometheus components.
4. `kube-state-metrics` (1 pod) — listens to the Kubernetes API server and generates metrics about the state of objects.
5. `prometheus-node-exporter` (1 pod) — collects host-level hardware and OS metrics.
6. `prometheus-server` (1 pod) — the core time-series database scraping and storing metrics.
