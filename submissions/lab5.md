# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

### 5.1 — CI workflow (`.github/workflows/ci.yml`)

```yaml
name: CI

on:
  push:
    branches: [main]
    paths-ignore:
      - 'submissions/**'
      - '*.md'
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push gateway
        run: |
          docker build -t ghcr.io/bytepharaoh/quickticket-gateway:${{ github.sha }} ./app/gateway
          docker build -t ghcr.io/bytepharaoh/quickticket-gateway:latest ./app/gateway
          docker push ghcr.io/bytepharaoh/quickticket-gateway:${{ github.sha }}
          docker push ghcr.io/bytepharaoh/quickticket-gateway:latest

      - name: Build and push events
        run: |
          docker build -t ghcr.io/bytepharaoh/quickticket-events:${{ github.sha }} ./app/events
          docker build -t ghcr.io/bytepharaoh/quickticket-events:latest ./app/events
          docker push ghcr.io/bytepharaoh/quickticket-events:${{ github.sha }}
          docker push ghcr.io/bytepharaoh/quickticket-events:latest

      - name: Build and push payments
        run: |
          docker build -t ghcr.io/bytepharaoh/quickticket-payments:${{ github.sha }} ./app/payments
          docker build -t ghcr.io/bytepharaoh/quickticket-payments:latest ./app/payments
          docker push ghcr.io/bytepharaoh/quickticket-payments:${{ github.sha }}
          docker push ghcr.io/bytepharaoh/quickticket-payments:latest
```

### 5.2 — GitHub Actions run (green) + images pushed to ghcr.io

The workflow triggered on push to main and completed successfully. Images confirmed in ghcr.io:

```json
{
  "name": "bytepharaoh/quickticket-gateway",
  "tags": ["0e38008ca97277a03c8d069fd5771e64e44bb06a", "latest"]
}
```

All three images (`quickticket-gateway`, `quickticket-events`, `quickticket-payments`) were built and pushed with both the commit SHA tag and `latest`.

### 5.3 — K8s manifests updated to use ghcr.io images

All three app service manifests (`k8s/gateway.yaml`, `k8s/events.yaml`, `k8s/payments.yaml`) were updated from `imagePullPolicy: Never` with local images to:

```yaml
imagePullSecrets:
  - name: ghcr-secret
containers:
  - image: ghcr.io/bytepharaoh/quickticket-gateway:latest
    imagePullPolicy: Always
```

A `ghcr-secret` docker-registry secret was created in the cluster:

```bash
kubectl create secret docker-registry ghcr-secret \
  --docker-server=ghcr.io \
  --docker-username=bytepharaoh \
  --docker-password=<PAT with read:packages>
```

### 5.4 — ArgoCD installed

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=Available deployment/argocd-server -n argocd --timeout=180s
# deployment.apps/argocd-server condition met
```

### 5.5 — ArgoCD Application created and synced

```
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/bytepharaoh/SRE-Intro.git
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (cfcb65a)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   MESSAGE
       Service     default    events    Synced  Healthy  service/events unchanged
       Service     default    payments  Synced  Healthy  service/payments unchanged
       Service     default    postgres  Synced  Healthy  service/postgres unchanged
       Service     default    redis     Synced  Healthy  service/redis unchanged
       Service     default    gateway   Synced  Healthy  service/gateway unchanged
apps   Deployment  default    postgres  Synced  Healthy  deployment.apps/postgres unchanged
apps   Deployment  default    payments  Synced  Healthy  deployment.apps/payments unchanged
apps   Deployment  default    redis     Synced  Healthy  deployment.apps/redis unchanged
apps   Deployment  default    events    Synced  Healthy  deployment.apps/events unchanged
apps   Deployment  default    gateway   Synced  Healthy  deployment.apps/gateway configured
```

Full stack verified via port-forward:

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

### 5.6 — GitOps loop verified

Added `version: v2` label to the gateway Deployment pod template, committed and pushed to main. ArgoCD detected the change and applied it within seconds:

```
apps  Deployment  default  gateway  Synced  Healthy  deployment.apps/gateway configured
```

Label confirmed live in the cluster:

```json
{
    "app": "gateway",
    "version": "v2"
}
```

The full GitOps loop: `git push` → ArgoCD polls Git → detects diff → applies to cluster — no manual `kubectl apply` needed.

### 5.7 — Answer: what happens if someone runs `kubectl edit` on an ArgoCD-managed resource?

Any manual change made via `kubectl edit` or `kubectl patch` will be **overwritten by ArgoCD on the next sync cycle** (within 3 minutes by default, or immediately on manual sync). ArgoCD continuously compares the desired state in Git against the actual state in the cluster — if they diverge, it reconciles back to Git. This is the core GitOps guarantee: Git is the single source of truth. A manual edit is not reflected in Git, so ArgoCD treats it as drift and reverts it. The correct way to make changes is always through a Git commit.

---

## Task 2 — Rollback via GitOps

### 5.8 — Bad deploy (non-existent image tag)

Pushed `quickticket-gateway:does-not-exist` image tag to main. ArgoCD synced the change immediately. The old gateway pod kept running (K8s rolling update keeps old pods until new ones are Ready), but the new pod failed to pull the image:

```
NAME                        READY   STATUS             RESTARTS   AGE
events-897bf84d6-dffsm      1/1     Running            0          5m23s
gateway-5bb8bdfc-2stjp      1/1     Running            0          109s   ← old pod still serving
gateway-654784bc65-xwg4n    0/1     ImagePullBackOff   0          45s    ← new pod stuck
payments-6c9c9695b8-hfngc   1/1     Running            0          5m23s
postgres-78489d7f5f-g8jgg   1/1     Running            0          6m49s
redis-6fcfb5475d-b5xcj      1/1     Running            0          6m47s
```

ArgoCD status during the bad deploy:

```
Sync Status:    Synced to  (734ffcf)
Health Status:  Progressing
apps  Deployment  default  gateway  Synced  Progressing  deployment.apps/gateway configured
```

Health stayed `Progressing` (not `Healthy`) because the new pod never became Ready. The readiness probe never passed since the image couldn't be pulled.

### 5.9 — Rollback via `git revert`

```bash
git revert HEAD --no-edit
# [feature/lab5 c577500] Revert "feat: deploy new gateway version"
git push origin feature/lab5:main --force
argocd app sync quickticket
```

ArgoCD synced the revert in **1 second**. All pods healthy:

```
NAME                        READY   STATUS    RESTARTS   AGE
events-897bf84d6-dffsm      1/1     Running   0          6m19s
gateway-5bb8bdfc-2stjp      1/1     Running   0          2m45s
payments-6c9c9695b8-hfngc   1/1     Running   0          6m19s
postgres-78489d7f5f-g8jgg   1/1     Running   0          7m45s
redis-6fcfb5475d-b5xcj      1/1     Running   0          7m43s
```

ArgoCD after revert:

```
Sync Status:    Synced to  (c577500)
Health Status:  Healthy
apps  Deployment  default  gateway  Synced  Healthy  deployment.apps/gateway configured
```

**Git log showing the full sequence:**

```
c577500  Revert "feat: deploy new gateway version"
734ffcf  feat: deploy new gateway version
cfcb65a  feat: add version label to gateway deployment
0e38008  feat(lab5): add CI pipeline and update k8s manifests to use ghcr.io images
```

**Recovery time:** From `git revert` + push to all pods Running — approximately **2 minutes 45 seconds**. This includes the time for ArgoCD to sync (~1s), Kubernetes to schedule the new pod, and the container runtime to pull the correct image and pass the readiness probe.

In docker-compose, rollback required manually editing the compose file and running `docker compose up -d` — a human action. With GitOps, the rollback is a git operation with a full audit trail, and the cluster state is guaranteed to match the commit history.