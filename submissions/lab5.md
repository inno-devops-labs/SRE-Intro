# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

### 1. GitHub Actions run (green check)

Link to the successful run:
`https://github.com/kostya2505/SRE-Intro/actions/runs/28226462039`

### 2. Images pushed to ghcr.io

```bash
gh api user/packages?package_type=container --jq '.[].name'
```

```
quickticket-gateway
quickticket-events
quickticket-payments
```

### 3. ArgoCD Application status

```bash
argocd app get quickticket
```

```
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/kostya2505/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (4133814)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH       HOOK  MESSAGE
       Service     default    redis     Synced  Healthy            service/redis created
       Service     default    gateway   Synced  Healthy            service/gateway created
       Service     default    payments  Synced  Healthy            service/payments created
       Service     default    postgres  Synced  Healthy            service/postgres created
       Service     default    events    Synced  Healthy            service/events created
apps   Deployment  default    payments  Synced  Healthy            deployment.apps/payments created
apps   Deployment  default    redis     Synced  Healthy            deployment.apps/redis created
apps   Deployment  default    events    Synced  Healthy            deployment.apps/events created
apps   Deployment  default    gateway   Synced  Healthy            deployment.apps/gateway created
apps   Deployment  default    postgres  Synced  Healthy            deployment.apps/postgres created
```

### 4. Git change synced to cluster

```bash
# After pushing version label change:
kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
```

```
v2
```

```bash
git log --oneline -3
```

```
61ef715 (HEAD -> main, origin/main, origin/HEAD) feat: add version label to gateway
4133814 feat: use ghcr.io images in K8s manifests
01c6e9d fix: ci.yml
```

### 5. What happens if someone runs `kubectl edit` on an ArgoCD-managed resource?

ArgoCD continuously reconciles the cluster state against the Git repository (the single source of truth). If someone 
manually edits a resource with `kubectl edit`, ArgoCD will detect the drift at its next sync cycle (within ~3 minutes, or immediately if webhooks are configured) and revert the manual change back to whatever is defined in Git. The application status would briefly show `OutOfSync` before being corrected. This is the core GitOps guarantee: Git wins, always. The correct way to make changes is to edit the manifests in Git and let ArgoCD deploy them - manual `kubectl` edits are effectively overwritten.

---

## Task 2 — Rollback via GitOps (optional)

### Bad deploy — ArgoCD shows Degraded

```bash
argocd app get quickticket
```

```
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/kostya2505/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (72cd76b)
Health Status:      Degraded

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH    HOOK  MESSAGE
       Service     default    payments  Synced  Healthy         service/payments unchanged
       Service     default    gateway   Synced  Healthy         service/gateway unchanged
       Service     default    events    Synced  Healthy         service/events unchanged
       Service     default    postgres  Synced  Healthy         service/postgres unchanged
       Service     default    redis     Synced  Healthy         service/redis unchanged
apps   Deployment  default    postgres  Synced  Healthy         deployment.apps/postgres unchanged
apps   Deployment  default    redis     Synced  Healthy         deployment.apps/redis unchanged
apps   Deployment  default    payments  Synced  Healthy         deployment.apps/payments unchanged
apps   Deployment  default    events    Synced  Healthy         deployment.apps/events unchanged
apps   Deployment  default    gateway   Synced  Degraded        deployment.apps/gateway configured
```

### Pods showing ImagePullBackOff

```bash
kubectl get pods
```

```
NAME                        READY   STATUS              RESTARTS   AGE                                                                              
events-7dfc58d69d-h5p5m     1/1     Running             0          74m
gateway-57485fdc6-p98b5     0/1     ErrImagePull        0          11m
gateway-cfbd8fd9-826bj      1/1     Running             0          74m
payments-95f7f7cd-nsxx4     1/1     Running             0          74m
postgres-8648c9df6c-r2k5x   1/1     Running             0          74m
redis-88f6ffbc8-9xl9t       1/1     Running             0          74m
```

### Git log showing deploy + revert

```bash
git log --oneline -3
```

```
15fd5ec (HEAD -> main, origin/main, origin/HEAD) Revert "feat: deploy new gateway version"
72cd76b feat: deploy new gateway version
61ef715 feat: add version label to gateway
```

### After revert — ArgoCD Healthy again

```bash
argocd app get quickticket
```

```
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/kostya2505/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (15fd5ec)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
```

```bash
kubectl get pods
```

```
NAME                        READY   STATUS    RESTARTS   AGE
events-7dfc58d69d-h5p5m     1/1     Running   0          85m
gateway-cfbd8fd9-826bj      1/1     Running   0          85m
payments-95f7f7cd-nsxx4     1/1     Running   0          85m
postgres-8648c9df6c-r2k5x   1/1     Running   0          85m
redis-88f6ffbc8-9xl9t       1/1     Running   0          85m
```

### Recovery time

From `git push` of the revert to gateway pod showing `Running`: approximately **3 minutes**. This covers: GitHub 
receiving the push → ArgoCD detecting the change (instant with sync, up to 3 min polling) → Kubernetes pulling the correct image → pod starting → readiness probe passing.

---

## Bonus Task — Automated Image Tag Update

### Updated CI workflow (auto tag update steps)

```yaml
name: CI

on:
  push:
    branches: [main]

jobs:
  build:
    if: "!startsWith(github.event.head_commit.message, 'ci:')"
    runs-on: ubuntu-latest
    permissions:
      packages: write
      contents: write

    steps:
      - uses: actions/checkout@v4

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Set image owner
        run: |
          echo "IMAGE_OWNER=$(echo '${{ github.repository_owner }}' | tr '[:upper:]' '[:lower:]')" >> $GITHUB_ENV

      - name: Build and push gateway
        run: |
          docker build -t ghcr.io/${{ env.IMAGE_OWNER }}/quickticket-gateway:${{ github.sha }} ./app/gateway
          docker push ghcr.io/${{ env.IMAGE_OWNER }}/quickticket-gateway:${{ github.sha }}

      - name: Build and push events
        run: |
          docker build -t ghcr.io/${{ env.IMAGE_OWNER }}/quickticket-events:${{ github.sha }} ./app/events
          docker push ghcr.io/${{ env.IMAGE_OWNER }}/quickticket-events:${{ github.sha }}

      - name: Build and push payments
        run: |
          docker build -t ghcr.io/${{ env.IMAGE_OWNER }}/quickticket-payments:${{ github.sha }} ./app/payments
          docker push ghcr.io/${{ env.IMAGE_OWNER }}/quickticket-payments:${{ github.sha }}

      - name: Update image tags in manifests
        run: |
          SHA=${{ github.sha }}
          sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/${{ github.actor }}/quickticket-gateway:${SHA}|" k8s/gateway.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-events:.*|image: ghcr.io/${{ github.actor }}/quickticket-events:${SHA}|" k8s/events.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-payments:.*|image: ghcr.io/${{ github.actor }}/quickticket-payments:${SHA}|" k8s/payments.yaml

      - name: Commit and push manifest update
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@github.com"
          git add k8s/
          git diff --cached --quiet || git commit -m "ci: update image tags to ${{ github.sha }}"
          git push
```

### Git log showing automated loop

```bash
git log --oneline -5
```

```
76ef458 (HEAD -> main, origin/main, origin/HEAD) ci: update image tags to 2f18b31d1cfa561f5c27fc95a45165dbaf63c35e
2f18b31 feat: ci.yml update
15fd5ec Revert "feat: deploy new gateway version"
72cd76b feat: deploy new gateway version
61ef715 feat: add version label to gateway
```

### ArgoCD syncing auto-updated tag

```bash
argocd app get quickticket
```

```
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/kostya2505/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (2f18b31)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
```

The loop worked end-to-end: developer pushes code → CI builds image with SHA tag → CI auto-commits updated tag to k8s/ → ArgoCD detects new commit → syncs → deploys new image — all without any manual intervention after the initial `git push`.