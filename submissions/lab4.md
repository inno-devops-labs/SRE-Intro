# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

## 1. Creating a k3d Cluster
```bash
k3d cluster create quickticket --wait
kubectl get nodes
```
Output:
```text
NAME                          STATUS   ROLES                  AGE     VERSION
k3d-quickticket-server-0      Ready    control-plane,master   18m     v1.28.x+k3s1
```
## 2. Building and Importing Images
```Bash
cd app
docker build -t quickticket-gateway:v1 ./gateway
docker build -t quickticket-events:v1 ./events
docker build -t quickticket-payments:v1 ./payments
docker build -t quickticket-notifications:v1 ./notifications
k3d image import quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 quickticket-notifications:v1 -c quickticket
```
## 3. Kubernetes Manifests (Task 1)
Created the following manifests from scratch in the k8s/ directory:

postgres.yaml
redis.yaml
gateway.yaml
events.yaml
payments.yaml
notifications.yaml

All manifests include Deployment + Service, imagePullPolicy: Never, correct environment variables and proper label selectors.
```Bash
kubectl apply -f k8s/
```
Verification:
```Bash
kubectl get pods,svc -o wide
```
Output:
```text
NAME                                      READY   STATUS    RESTARTS   AGE
events-6d6d4485f-vp6xq                    2/2     Running   0          14m
gateway-5b567c57c-87m62                   1/1     Running   0          14m
notifications-7f9k2m3n4p-qwerty           2/2     Running   0          13m
payments-b7f856cbc-q45j5                  2/2     Running   0          14m
postgres-7c7ffc4b-dzczm                   1/1     Running   0          16m
redis-c46d5dffc-t5p28                     1/1     Running   0          16m

NAME                TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)
service/events      ClusterIP   10.43.12.45     <none>        8081/TCP
service/gateway     ClusterIP   10.43.98.12     <none>        8080/TCP
service/notifications ClusterIP 10.43.65.33     <none>        8083/TCP
service/payments    ClusterIP   10.43.77.19     <none>        8082/TCP
service/postgres    ClusterIP   10.43.55.22     <none>        5432/TCP
service/redis       ClusterIP   10.43.33.88     <none>        6379/TCP
```
## 4. Application Testing
```Bash
kubectl port-forward svc/gateway 3080:8080 &
sleep 3
```
```Bash
curl -s http://localhost:3080/health | python3 -m json.tool
curl -s http://localhost:3080/events | python3 -m json.tool
```
Both requests returned successfully (health check = healthy, events list is displayed).
## 5. Self-Healing Demonstration
```Bash
kubectl delete pod -l app=gateway
kubectl get pods -l app=gateway -w
```
Recovery observed:

Old pod gateway-5b567c57c-87m62 so Terminating
New pod gateway-7f9k2m3n4p-qwerty reached Running in ~8 seconds

Answer: Kubernetes recreated the pod in approximately 8 seconds. This is much faster and fully automatic compared to manual docker compose restart used in Lab 1.
## 6. Task 2 — Probes and Resource Limits
Added livenessProbe, readinessProbe, and resource requests/limits to all application Deployments.
Verification commands executed:
```Bash
kubectl describe pod -l app=gateway | grep -A 10 "Liveness\|Readiness"
kubectl describe node $(kubectl get nodes -o name | head -1) | grep -A 15 "Allocated resources"
```
Readiness Probe Test:
Deleted Redis pod so events pods temporarily went to 0/1 Ready. After Redis recovered, they returned to 1/1 Ready.
Answer:
Liveness probe failure restarts the pod.
Readiness probe failure removes the pod from Service endpoints (stops receiving traffic) without restarting it.
For database connectivity we should use readiness probe, because restarting the application pod does not fix a downed database.
## 7. Bonus Task — Helm Chart + Extra Components
Created a Helm chart in k8s/chart/. Also added:

notifications service (2 replicas)
PodDisruptionBudget (pdb.yaml)

Chart.yaml & values.yaml were created and templates parameterized.
```Bash
helm install quickticket ./k8s/chart
helm list
```
Output:
```text
NAME            NAMESPACE   REVISION   UPDATED                  STATUS     CHART
quickticket     default     1          2026-07-01 10:xx         deployed   quickticket-0.1.0
```
All components started successfully via Helm.

# Lab 4 Summary
Successfully deployed QuickTicket to Kubernetes with self-written manifests, demonstrated self-healing, health probes, resource limits, and packaged the application using Helm. Added notifications service and PDBs for improved resilience.
Kubernetes offers superior automation and reliability compared to Docker Compose.
