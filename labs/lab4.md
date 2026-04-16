# Lab 4 — Kubernetes: Deploy QuickTicket to a Cluster

![difficulty](https://img.shields.io/badge/difficulty-intermediate-yellow)
![topic](https://img.shields.io/badge/topic-Kubernetes-blue)
![points](https://img.shields.io/badge/points-10%2B2.5-orange)
![tech](https://img.shields.io/badge/tech-k3d%20%2B%20kubectl-informational)

> **Goal:** Write Kubernetes manifests from scratch, deploy QuickTicket to k3d, and debug using kubectl.
> **Deliverable:** A PR from `feature/lab4` to the course repo with `submissions/lab4.md`. Submit PR link via Moodle.

---

## Overview

In this lab you will practice:
- Creating a local Kubernetes cluster with k3d
- Writing Deployment and Service manifests **from scratch**
- Deploying a multi-service app to Kubernetes
- Using kubectl to observe, debug, and recover from failures
- Understanding how K8s self-healing works vs docker-compose

> **No manifests are provided.** You write them yourself, using the lecture slides and docker-compose.yaml as reference. This is where you translate Compose knowledge into Kubernetes.

---

## Project State

**You should have from previous labs:**
- QuickTicket running in docker-compose with monitoring (Prometheus + Grafana)
- Understanding of Docker networking, images, and service discovery

**This lab adds:**
- QuickTicket deployed to a Kubernetes cluster (k3d)
- K8s manifests you wrote yourself (Deployment + Service for each component)

---

## Task 1 — Write Manifests & Deploy to k3d (6 pts)

**Objective:** Start a local K8s cluster, write manifests for all QuickTicket components, deploy, and verify.

### 4.1: Create a k3d cluster

k3d runs k3s (lightweight Kubernetes) inside Docker — you already have Docker from Week 1.

```bash
# Install k3d (if not already)
curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash

# Create a cluster
k3d cluster create quickticket

# Verify
kubectl get nodes
```

You should see one node with status `Ready`.

### 4.2: Build and import images

Build the images locally, then import them into k3d:

```bash
cd app/
docker build -t quickticket-gateway:v1 ./gateway
docker build -t quickticket-events:v1 ./events
docker build -t quickticket-payments:v1 ./payments

# Import into k3d cluster
k3d image import quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 -c quickticket

# Verify
docker images | grep quickticket
```

### 4.3: Deploy PostgreSQL and Redis

For the databases, use existing Docker images directly. Create a file `k8s/postgres.yaml`:

```yaml
# YOUR TASK: Write a Deployment + Service for PostgreSQL
# Requirements:
# - Deployment: 1 replica, image: postgres:16-alpine
# - Environment variables: POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD (all "quickticket")
# - Container port: 5432
# - Service: ClusterIP, port 5432
#
# Hint: Look at your docker-compose.yaml for the env vars
# Hint: Lecture slide 7 shows the Deployment format
# Hint: Lecture slide 8 shows the Service format
```

Create `k8s/redis.yaml` similarly:

```yaml
# YOUR TASK: Write a Deployment + Service for Redis
# - Image: redis:7-alpine
# - Port: 6379
# - No environment variables needed
```

Apply and verify:

```bash
kubectl apply -f k8s/postgres.yaml
kubectl apply -f k8s/redis.yaml
kubectl get pods
# Wait until both show Running
kubectl get svc
```

### 4.4: Deploy QuickTicket services

Create `k8s/gateway.yaml`, `k8s/events.yaml`, and `k8s/payments.yaml`.

Each needs a **Deployment** and a **Service**. Use your docker-compose.yaml as reference:

**For `events` — you need to pass environment variables:**
```yaml
# Hint: environment variables in K8s Deployment
env:
  - name: DB_HOST
    value: "postgres"      # The Service name becomes the hostname
  - name: DB_PORT
    value: "5432"
  - name: DB_NAME
    value: "quickticket"
  # ... add the rest (DB_USER, DB_PASS, REDIS_HOST, REDIS_PORT)
```

**For `gateway` — it needs to know where events and payments are:**
```yaml
env:
  - name: EVENTS_URL
    value: "http://events:8081"
  - name: PAYMENTS_URL
    value: "http://payments:8082"
  - name: GATEWAY_TIMEOUT_MS
    value: "5000"
```

**For `payments`:**
```yaml
env:
  - name: PAYMENT_FAILURE_RATE
    value: "0.0"
  - name: PAYMENT_LATENCY_MS
    value: "0"
```

**Important:** Set `imagePullPolicy: Never` in each Deployment so K8s uses the locally-built images:

```yaml
containers:
  - name: gateway
    image: quickticket-gateway:v1
    imagePullPolicy: Never      # ← Use locally imported image, don't pull from Docker Hub
```

Apply all:

```bash
kubectl apply -f k8s/
kubectl get pods -w   # Watch pods starting (Ctrl+C to stop)
```

### 4.5: Initialize the database

The PostgreSQL doesn't have seed data yet. Load it:

```bash
kubectl exec -it $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql
```

### 4.6: Verify everything works

```bash
# Port-forward gateway to your local machine
kubectl port-forward svc/gateway 3080:8080 &

# Test the critical path
curl -s http://localhost:3080/events | python3 -m json.tool
curl -s http://localhost:3080/health | python3 -m json.tool

# Kill the port-forward when done
kill %1
```

### 4.7: Test K8s self-healing

This is the key difference from docker-compose:

```bash
# Delete a pod (K8s should recreate it automatically)
kubectl delete pod -l app=gateway
kubectl get pods -w   # Watch it come back
```

How fast did it recover? Compare with Lab 1 where you had to manually `docker compose start`.

### 4.8: Proof of work

**Your manifests go to `k8s/` directory** in your fork — like a real project.

**Paste into `submissions/lab4.md`** (report only, not manifests):
1. Output of `kubectl get nodes`
2. Output of `kubectl get pods,svc` showing all running
3. Output of `curl localhost:3080/events` via port-forward (proving the full stack works)
4. Output of `kubectl get pods -w` during pod deletion — showing auto-recovery
5. Answer: "How long did K8s take to recreate the deleted pod? How does this compare to docker-compose restart?"

<details>
<summary>💡 Hints</summary>

- Every Deployment needs `selector.matchLabels` matching the pod template `labels` — if they don't match, K8s rejects it
- `imagePullPolicy: Never` is required for locally imported images — without it, K8s tries to pull from Docker Hub and fails
- If a pod shows `CrashLoopBackOff`, check logs: `kubectl logs <pod-name>` — usually a missing env var or wrong port
- `kubectl describe pod <name>` shows events — look at the bottom for error messages
- Service names (`postgres`, `redis`, `events`, `payments`, `gateway`) become DNS hostnames inside the cluster — same concept as docker-compose
- **Startup order:** Unlike docker-compose, K8s has no `depends_on`. If events starts before postgres is ready, it will fail to connect. Fix: `kubectl rollout restart deployment/events` after postgres is Running. This is a real problem — in later weeks we solve it with probes and init containers.
- If `seed.sql` fails, the postgres pod might not be ready yet — wait for `kubectl get pod -l app=postgres` to show `Running`
- `kubectl get pods -w` streams updates — you'll see `Terminating` → new pod → `Pending` → `Running`

</details>

---

## Task 2 — Probes & Resource Limits (3 pts)

> ⏭️ This task is optional. Skipping it will not affect future labs.

**Objective:** Add health probes and resource limits to your manifests, and observe what happens when they trigger.

### 4.9: Add readiness and liveness probes

Update your `gateway` Deployment to include:

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8080
  initialDelaySeconds: 10
  periodSeconds: 10
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /health
    port: 8080
  periodSeconds: 5
  failureThreshold: 2
```

Add similar probes to `events` (port 8081) and `payments` (port 8082).

Apply and verify probes are running:

```bash
kubectl apply -f k8s/
kubectl describe pod -l app=gateway | grep -A 5 "Liveness\|Readiness"
```

### 4.10: Observe readiness probe failure

Kill Redis (events depends on it for health check):

```bash
kubectl delete pod -l app=redis
# Watch events pod — readiness probe should fail
kubectl get pods -w
kubectl describe pod -l app=events | grep -A 3 "Readiness"
```

What happened? The events pod should show `0/1 Ready` — K8s removed it from the Service endpoints (no traffic routed to it). Once Redis comes back, the probe passes and traffic resumes.

### 4.11: Add resource limits

Add to each container in your Deployments:

```yaml
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi
```

Apply, then check allocation:

```bash
kubectl apply -f k8s/
kubectl describe node $(kubectl get nodes -o name | head -1) | grep -A 10 "Allocated resources"
```

**Paste into `submissions/lab4.md`:**
- `kubectl describe pod` output showing probes configured
- Output during Redis deletion showing readiness probe failure (`0/1 Ready`)
- `kubectl describe node` output showing allocated resources
- Answer: "What's the difference between liveness and readiness probe failure? Which one should you use for checking database connectivity, and why?"

<details>
<summary>💡 Hints</summary>

- Readiness failure = pod removed from Service (no traffic), NOT restarted
- Liveness failure = pod killed and restarted — dangerous if it checks a dependency
- For database connectivity: use **readiness**, not liveness — if DB is down, you want to stop traffic to the pod, not restart the pod (restarting won't fix the DB)
- `kubectl top pods` shows actual CPU/memory usage (k3d includes metrics-server by default)

</details>

---

## Bonus Task — Helm Chart (2.5 pts)

> 🌟 For those who want extra challenge and experience.

**Objective:** Convert your raw manifests into a Helm chart with configurable values.

### B.1: Create the chart scaffold

```bash
mkdir -p k8s/chart
```

Create `k8s/chart/Chart.yaml`:
```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
version: 0.1.0
```

Create `k8s/chart/values.yaml` with all configurable values:
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

### B.2: Create templates

Move your manifests to `k8s/chart/templates/` and replace hardcoded values with `{{ .Values.x }}` references. For example, in `gateway-deployment.yaml`:

```yaml
replicas: {{ .Values.gateway.replicas }}
image: {{ .Values.gateway.image }}
```

### B.3: Install and verify

```bash
# Uninstall the raw manifests first
kubectl delete -f k8s/

# Install via Helm
helm install quickticket k8s/chart/

# Verify
kubectl get pods
helm list
```

### B.4: Deploy monitoring via Helm

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install monitoring prometheus-community/kube-prometheus-stack \
  --set grafana.adminPassword=admin \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false
```

**Paste into `submissions/lab4.md`:**
- Your `Chart.yaml` and `values.yaml`
- Output of `helm list` showing the installed release
- Output of `kubectl get pods` after Helm install
- If monitoring installed: how many pods did kube-prometheus-stack create?

---

## How to Submit

1. Create a branch and push:

   ```bash
   git switch -c feature/lab4
   git add k8s/ submissions/lab4.md monitoring/prometheus/prometheus.yml
   git commit -m "docs(lab4): add lab4 — K8s manifests and deployment"
   git push -u origin feature/lab4
   ```

2. Open a PR from your fork's `feature/lab4` → **course repo main branch**.

3. In the PR description, include:

   ```text
   - [x] Task 1 done — K8s manifests written, QuickTicket deployed to k3d
   - [ ] Task 2 done — probes and resource limits added
   - [ ] Bonus Task done — Helm chart created
   ```

4. **Submit PR URL** via Moodle before the deadline.

---

## Acceptance Criteria

### Task 1 (6 pts)
- ✅ k3d cluster running (`kubectl get nodes` output)
- ✅ All manifest files committed in `k8s/` (postgres, redis, gateway, events, payments)
- ✅ All pods running (`kubectl get pods,svc` output)
- ✅ Full stack working via port-forward (`curl` output)
- ✅ Self-healing demonstrated (pod delete + auto-recovery output)
- ✅ Written comparison of K8s recovery vs docker-compose

### Task 2 (3 pts)
- ✅ Probes configured in manifests (kubectl describe output)
- ✅ Readiness failure observed during Redis deletion
- ✅ Resource limits set, node allocation shown
- ✅ Written answer on liveness vs readiness for DB checks

### Bonus Task (2.5 pts)
- ✅ Helm chart with Chart.yaml, values.yaml, templates
- ✅ `helm list` showing installed release
- ✅ Pods running after Helm install

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — K8s manifests + deployment | **6** | All manifests written from scratch, deployed, verified, self-healing demonstrated |
| **Task 2** — Probes + resource limits | **3** | Probes added and tested, readiness failure observed, resource limits set |
| **Bonus Task** — Helm chart | **2.5** | Chart created, installed, working, monitoring optional |
| **Total** | **11.5** | 9 main + 2.5 bonus |

---

## Resources

<details>
<summary>📚 Documentation</summary>

- [Kubernetes Concepts](https://kubernetes.io/docs/concepts/) — Pods, Deployments, Services
- [kubectl cheat sheet](https://kubernetes.io/docs/reference/kubectl/cheatsheet/)
- [k3d quick start](https://k3d.io/) — k3s in Docker
- [Helm quickstart](https://helm.sh/docs/intro/quickstart/)

</details>

<details>
<summary>🛠️ Manifest Reference</summary>

```yaml
# Minimal Deployment
apiVersion: apps/v1
kind: Deployment
metadata:
  name: myapp
spec:
  replicas: 1
  selector:
    matchLabels:
      app: myapp
  template:
    metadata:
      labels:
        app: myapp
    spec:
      containers:
        - name: myapp
          image: myimage:v1
          ports:
            - containerPort: 8080

---
# Minimal Service
apiVersion: v1
kind: Service
metadata:
  name: myapp
spec:
  selector:
    app: myapp
  ports:
    - port: 8080
      targetPort: 8080
```

</details>

<details>
<summary>⚠️ Common Pitfalls</summary>

- **ImagePullBackOff** — forgot `imagePullPolicy: Never` for local images
- **CrashLoopBackOff** — check `kubectl logs <pod>` — usually a missing env var
- **selector doesn't match labels** — `matchLabels` in Deployment must match `labels` in pod template exactly
- **Can't connect to service** — use `kubectl port-forward svc/<name> local:remote` for testing
- **seed.sql fails** — postgres might not be ready yet. Wait for `Running` status, then retry
- **k3d cluster won't start** — check Docker is running: `docker ps`

</details>
