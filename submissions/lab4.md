# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

## Student Submission

---

# Task 4.1 — Create a k3d Cluster

## Objective

Create a local Kubernetes cluster using k3d.

## Commands Executed

```bash
k3d cluster create quickticket
kubectl get nodes
```

## Output

```text
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   30s   v1.35.5+k3s1
```

## Solution / Result

The Kubernetes cluster was created successfully. The node reached the `Ready` state, which means the cluster was ready to run workloads.

---

# Task 4.2 — Build and Import Images

## Objective

Build the QuickTicket Docker images locally and import them into the k3d cluster.

## Commands Executed

```bash
cd app

docker build -t quickticket-gateway:v1 ./gateway
docker build -t quickticket-events:v1 ./events
docker build -t quickticket-payments:v1 ./payments

k3d image import quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 -c quickticket
```

## Output

```text
INFO[0000] Importing image(s) into cluster 'quickticket'
INFO[0000] Saving 3 image(s) from runtime...
INFO[0001] Importing images into nodes...
INFO[0001] Importing images from tarball '/k3d/images/k3d-quickticket-images-20260619192147.tar' into node 'k3d-quickticket-server-0'...
INFO[0003] Removing the tarball(s) from image volume...
INFO[0004] Removing k3d-tools node...
INFO[0004] Successfully imported image(s)
INFO[0004] Successfully imported 3 image(s) into 1 cluster(s)
```

## Solution / Result

All three application images were built and imported into the k3d cluster successfully.

Because these images are local, the Kubernetes manifests use:

```yaml
imagePullPolicy: Never
```

This prevents Kubernetes from trying to pull the images from Docker Hub.

---

# Task 4.3 — Deploy PostgreSQL and Redis

## Objective

Create Kubernetes Deployments and Services for PostgreSQL and Redis.

## Commands Executed

```bash
kubectl apply -f k8s/postgres.yaml
kubectl apply -f k8s/redis.yaml
```

## Output

```text
deployment.apps/postgres created
service/postgres created

deployment.apps/redis created
service/redis created
```

## Verification

```bash
kubectl get pods -w
```

Output:

```text
NAME                        READY   STATUS              RESTARTS   AGE
postgres-78489d7f5f-4wfj4   0/1     ContainerCreating   0          6s
redis-6fcfb5475d-jqspl      0/1     ContainerCreating   0          4s
redis-6fcfb5475d-jqspl      1/1     Running             0          43s
postgres-78489d7f5f-4wfj4   1/1     Running             0          89s
```

## Solution / Result

PostgreSQL and Redis were deployed successfully. Both pods reached the `Running` state.

---

# Task 4.4 — Deploy QuickTicket Services

## Objective

Deploy the main QuickTicket services:

- Events
- Payments
- Gateway

Each service has its own Kubernetes Deployment and Service.

## Commands Executed

```bash
kubectl apply -f k8s/events.yaml
kubectl apply -f k8s/payments.yaml
kubectl apply -f k8s/gateway.yaml
```

## Output

```text
deployment.apps/events created
service/events created

deployment.apps/payments created
service/payments created

deployment.apps/gateway created
service/gateway created
```

## Verification

```bash
kubectl get pods -w
```

Output:

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-675d86c77-9btwc      1/1     Running   0          7s
gateway-7cd55d8774-xplb8    0/1     Running   0          3s
payments-d7dc94485-4q9zb    0/1     Running   0          6s
payments-d7dc94485-4q9zb    1/1     Running   0          7s
gateway-7cd55d8774-xplb8    1/1     Running   0          7s
```

## Solution / Result

All QuickTicket application services were deployed successfully and became ready.

---

# Task 4.5 — Initialize the Database

## Objective

Load the database schema and seed data into the PostgreSQL pod.

## Command Executed

```bash
kubectl exec -it $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql
```

## Output

```text
Unable to use a TTY - input is not a terminal or the right kind of file
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

## Solution / Result

The warning about TTY did not stop the command from working.

The important part is:

```text
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

This confirms that the database tables were created and five records were inserted.

---

# Task 4.6 — Verify Everything Works

## Objective

Verify that the full application stack works through the Gateway service.

## Step 1 — Port-forward Gateway

I forwarded the Kubernetes Gateway service to my local machine:

```bash
kubectl port-forward svc/gateway 3080:8080
```

Output:

```text
Forwarding from 127.0.0.1:3080 -> 8080
Forwarding from [::1]:3080 -> 8080
Handling connection for 3080
Handling connection for 3080
```

## Step 2 — Test Events Endpoint

Command:

```bash
curl -s http://localhost:3080/events | python3 -m json.tool
```

Output:

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

## Step 3 — Test Health Endpoint

Command:

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
```

Output:

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

## Solution / Result

The application worked through the Gateway service.

The `/events` endpoint returned seeded events from the database, and the `/health` endpoint showed that Gateway could reach Events and Payments successfully.

---

# Task 4.7 — Test Kubernetes Self-Healing

## Objective

Delete one pod and verify that Kubernetes recreates it automatically.

## Current Pods Before Deletion

Command:

```bash
kubectl get pods
```

Output:

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-675d86c77-9btwc      1/1     Running   0          11m
gateway-7cd55d8774-xplb8    1/1     Running   0          11m
payments-d7dc94485-4q9zb    1/1     Running   0          11m
postgres-78489d7f5f-4wfj4   1/1     Running   0          13m
redis-6fcfb5475d-jqspl      1/1     Running   0          13m
```

## Delete Gateway Pod

Command:

```bash
kubectl delete pod -l app=gateway
```

Output:

```text
pod "gateway-7cd55d8774-xplb8" deleted from default namespace
```

## Watch Recovery

Command:

```bash
kubectl get pods -w
```

Output:

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-675d86c77-9btwc      1/1     Running   0          11m
gateway-7cd55d8774-m47g8    0/1     Running   0          5s
payments-d7dc94485-4q9zb    1/1     Running   0          11m
postgres-78489d7f5f-4wfj4   1/1     Running   0          13m
redis-6fcfb5475d-jqspl      1/1     Running   0          13m
gateway-7cd55d8774-m47g8    1/1     Running   0          7s
```

## Solution / Result

Kubernetes automatically created a new Gateway pod after the old one was deleted.

The deleted pod was:

```text
gateway-7cd55d8774-xplb8
```

The new pod was:

```text
gateway-7cd55d8774-m47g8
```

The replacement pod became fully ready in about **7 seconds**.

## Comparison with Docker Compose

In Docker Compose, if a container is manually deleted or stopped, I usually need to restart it manually with a command such as:

```bash
docker compose up -d
```

In Kubernetes, the Deployment controller keeps the desired state. Since the desired state was one Gateway replica, Kubernetes automatically created a new pod when the old one was deleted.

This shows Kubernetes self-healing behavior.

---

# Task 4.8 — Proof of Work Summary

## Nodes

```text
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   30s   v1.35.5+k3s1
```

## Pods and Services

```text
NAME                            READY   STATUS    RESTARTS   AGE
pod/events-675d86c77-9btwc      1/1     Running   0          2m10s
pod/gateway-7cd55d8774-xplb8    1/1     Running   0          2m6s
pod/payments-d7dc94485-4q9zb    1/1     Running   0          2m9s
pod/postgres-78489d7f5f-4wfj4   1/1     Running   0          4m7s
pod/redis-6fcfb5475d-jqspl      1/1     Running   0          4m5s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.167.2     <none>        8081/TCP   2m11s
service/gateway      ClusterIP   10.43.104.113   <none>        8080/TCP   2m6s
service/kubernetes   ClusterIP   10.43.0.1       <none>        443/TCP    5m11s
service/payments     ClusterIP   10.43.1.237     <none>        8082/TCP   2m9s
service/postgres     ClusterIP   10.43.241.192   <none>        5432/TCP   4m8s
service/redis        ClusterIP   10.43.105.206   <none>        6379/TCP   4m5s
```

## Application Check

The Gateway service was tested locally using port-forwarding.

The `/events` endpoint returned event data, and `/health` returned:

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

## Self-Healing Check

Gateway pod deletion was tested. Kubernetes created a replacement pod automatically and it became ready in about **7 seconds**.

---

# Final Conclusion

In this lab I deployed the QuickTicket application to a local Kubernetes cluster using k3d.

I completed the main Kubernetes deployment task by:

- creating the k3d cluster
- writing Kubernetes manifests for all services
- building and importing local Docker images
- deploying PostgreSQL and Redis
- initializing the database
- deploying Events, Payments, and Gateway
- verifying the application through port-forwarding
- testing Kubernetes self-healing

The final state of the system was healthy, and all required services were running successfully inside Kubernetes.
