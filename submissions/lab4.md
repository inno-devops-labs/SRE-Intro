# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster
**Student:** Valerii Tiniakov

**Group:** B24-SD-03

## Task 1 — Write Manifests & Deploy to k3d

### 4.1: Create a k3d cluster
**Output of `kubectl get nodes`:**
```text
$ kubectl get nodes
NAME                       STATUS   ROLES           AGE    VERSION
k3d-quickticket-server-0   Ready    control-plane   2m1s   v1.35.5+k3s1
```

### 4.4: Deploy QuickTicket services
**Output of kubectl get pods,svc:**
```text
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab4)
$ kubectl get pods,svc
NAME                           READY   STATUS    RESTARTS   AGE
pod/events-859d5c5c98-wwt4b    1/1     Running   0          3m26s
pod/gateway-6fc44f68c5-4drf4   1/1     Running   0          3m26s
pod/payments-58fb468db-glcnd   1/1     Running   0          3m25s
pod/postgres-7c7ffc4b-t4npw    1/1     Running   0          3m25s
pod/redis-c46d5dffc-tjflb      1/1     Running   0          3m25s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.238.202   <none>        8081/TCP   3m26s
service/gateway      ClusterIP   10.43.97.93     <none>        8080/TCP   3m26s
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    14m
service/payments     ClusterIP   10.43.192.186   <none>        8082/TCP   3m25s
service/postgres     ClusterIP   10.43.63.218    <none>        5432/TCP   3m25s
service/redis        ClusterIP   10.43.245.244   <none>        6379/TCP   3m25s


```

### 4.6: Verify everything works
**Output of curl localhost:3080/events (via port-forward):**
```text
*   Trying 127.0.0.1:3080...
* Connected to 127.0.0.1 (127.0.0.1) port 3080
* using HTTP/1.x
> GET /events HTTP/1.1
> Host: 127.0.0.1:3080
> User-Agent: curl/8.12.1
> Accept: */*
>
* Request completely sent off
< HTTP/1.1 200 OK
< date: Fri, 19 Jun 2026 20:21:51 GMT
< server: uvicorn
< content-length: 724
< content-type: application/json
<
[{"id":1,"name":"Go Conference 2026","venue":"Main Hall A","date":"2026-09-15T09:00:00+00:00","total_tickets":100,"price_cents":5000,"available":100},{"id":4,"name":"Python Workshop","venue":"Lab 301","date":"2026-09-22T14:00:00+00:00","total_tickets":25,"price_cents":2000,"available":25},{"id":2,"name":"SRE Meetup","venue":"Room 204","date":"2026-10-01T18:00:00+00:00","total_tickets":30,"price_cents":0,"available":30},{"id":5,"name":"Kubernetes Deep Dive","venue":"Auditorium B","date":"2026-10-10T10:00:00+00:00","total_tickets":80,"price_cents":8000,"available":80},{"id":3,"name":"Cloud Native Summit","venue":"Expo Center","date":"2026-11-20T10:00:00+00:00","total_tickets":500,"price_cents":15000,"available":500}]* Connection #0 to host 127.0.0.1 left intact

valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro/app (feature/lab4)
$

```

### 4.7: Test K8s self-healing
**Output of kubectl get pods -w during pod deletion:**
``` text
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab4)
$ kubectl delete pod -l app=gateway
pod "gateway-74867dd6f-vwmv2" deleted

valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab4)
$ kubectl get pods -w
NAME                       READY   STATUS    RESTARTS   AGE
events-6fc58759f5-vhxs8    1/1     Running   0          76s
gateway-74867dd6f-65xjm    1/1     Running   0          6s
payments-58fb468db-glcnd   1/1     Running   0          19m
postgres-7c7ffc4b-t4npw    1/1     Running   0          19m
redis-c46d5dffc-tjflb      1/1     Running   0          19m


```

Answers: 
* `How long did K8s take to recreate the deleted pod? How does this compare to docker-compose restart?` The gateway pod was recreated and reached the Running state within 6 seconds. Kubernetes detected the absence of the pod immediately after deletion and triggered the Deployment controller to bring the cluster back to its desired state.
* `How does this compare to docker-compose restart?` Unlike docker-compose, which typically requires manual intervention or specific restart policies to handle container failures, Kubernetes acts as an autonomous control loop. It continuously compares the actual state of the cluster (no gateway pod) with the desired state (defined in the Deployment manifest) and performs self-healing actions without any user interaction. This ensures significantly higher system availability and reduces the "Mean Time To Recovery" (MTTR).


## Task 2 — Probes & Resource Limits (Optional)

### 4.9: Add readiness and liveness probes
* **kubectl describe pod output showing probes:** 
``` text

valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab4)
$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
events-db9896fc-8pnl2       1/1     Running   0          71s
gateway-6568956957-2vnwf    1/1     Running   0          5m32s
payments-68dcdf7696-cwz82   1/1     Running   0          71s
postgres-7c7ffc4b-t4npw     1/1     Running   0          31m
redis-c46d5dffc-tjflb       1/1     Running   0          31m

valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab4)
$ kubectl describe pod -l app=gateway | grep -A 5 "Liveness\|Readiness"
    Liveness:       http-get http://:8080/health delay=15s timeout=1s period=20s #success=1 #failure=3
    Readiness:      http-get http://:8080/health delay=5s timeout=1s period=5s #success=1 #failure=3
    Environment:
      EVENTS_URL:          http://events:8081
      PAYMENTS_URL:        http://payments:8082
      GATEWAY_TIMEOUT_MS:  5000
    Mounts:

valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab4)
$ kubectl describe pod -l app=events | grep -A 5 "Liveness\|Readiness"
    Liveness:       http-get http://:8081/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:      http-get http://:8081/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      DB_HOST:     postgres
      DB_PORT:     5432
      DB_NAME:     quickticket
      DB_USER:     quickticket


valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab4)
$ kubectl describe pod -l app=payments | grep -A 5 "Liveness\|Readiness"
    Liveness:       http-get http://:8082/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:      http-get http://:8082/health delay=0s timeout=1s period=5s #success=1 #failure=2
    Environment:
      PAYMENT_FAILURE_RATE:  0.0
      PAYMENT_LATENCY_MS:    0
    Mounts:
      /var/run/secrets/kubernetes.io/serviceaccount from kube-api-access-p229c (ro)

valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab4)
$

```

### 4.10: Observe readiness probe failure
**Output during Redis deletion showing readiness probe failure (0/1 Ready):**
```text

valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab4)
$ kubectl get pods -w
NAME                        READY   STATUS    RESTARTS   AGE
events-db9896fc-8pnl2       1/1     Running   0          4m9s
gateway-6568956957-2vnwf    1/1     Running   0          8m30s
payments-68dcdf7696-cwz82   1/1     Running   0          4m9s
postgres-7c7ffc4b-t4npw     1/1     Running   0          34m
redis-c46d5dffc-tjflb       1/1     Running   0          34m
redis-c46d5dffc-tjflb       1/1     Terminating   0          34m
redis-c46d5dffc-tjflb       0/1     Completed     0          34m
redis-c46d5dffc-jpzmj       0/1     Pending       0          0s
redis-c46d5dffc-jpzmj       0/1     ContainerCreating   0          0s
redis-c46d5dffc-jpzmj       1/1     Running             0          1s

```

### 4.11: Add resource limits
**kubectl describe node output showing allocated resources:**
```text
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab4)
$ kubectl describe node | grep -A 15 "Allocated resources"
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                350m (2%)   600m (3%)
  memory             332Mi (4%)  938Mi (12%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
Events:
  Type    Reason                          Age   From                   Message
  ----    ------                          ----  ----                   -------
  Normal  CertificateExpirationOK         50m   k3s-cert-monitor       Node and Certificate Authority certificates managed by k3s are OK
  Normal  Synced                          49m   cloud-node-controller  Node synced successfully
  Normal  RegisteredNode                  49m   node-controller        Node k3d-quickticket-server-0 event: Registered Node k3d-quickticket-server-0 in Controller
  Normal  NodePasswordValidationComplete  49m   k3s-supervisor         Deferred node password secret validation complete
```

Answer: 
* `What's the difference between liveness and readiness probe failure?`
    * Liveness probe failure: Kubernetes detects that the application is in a "dead" or stuck state from which it cannot recover on its own (e.g., a deadlock or an infinite loop). In response, Kubernetes restarts the container to force it into a clean, running state.
    * Readiness probe failure: Kubernetes detects that the application is temporarily unable to handle incoming traffic (e.g., while loading configuration, warming up caches, or waiting for a dependency). In response, Kubernetes stops routing traffic to this pod by removing it from the Service endpoints, but it does not restart the container, allowing the pod to recover gracefully once the dependency is resolved.
* `Which one should you use for checking database connectivity, and why?`
You should use the Readiness probe to check database connectivity.
Reasoning: Database connectivity issues are typically transient external dependency problems.
    * Using a Liveness probe for this would cause a "restart loop" every time the database is temporarily unreachable, which unnecessarily stresses the infrastructure and creates a "thundering herd" effect on the database as all pods simultaneously try to reconnect during startup.
    * A Readiness probe is a much more elegant solution: it effectively "hides" the pod from user traffic while the database is down, ensuring that the system remains stable and does not attempt to process requests that are destined to fail.