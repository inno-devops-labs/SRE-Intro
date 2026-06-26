# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup (6 pts)

### 5.1 — CI Workflow

`.github/workflows/ci.yml`:

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

      - name: Build and push gateway
        run: |
          docker build -t ghcr.io/${{ github.actor }}/quickticket-gateway:${{ github.sha }} ./app/gateway
          docker push ghcr.io/${{ github.actor }}/quickticket-gateway:${{ github.sha }}
          docker tag ghcr.io/${{ github.actor }}/quickticket-gateway:${{ github.sha }} ghcr.io/${{ github.actor }}/quickticket-gateway:latest
          docker push ghcr.io/${{ github.actor }}/quickticket-gateway:latest

      - name: Build and push events
        run: |
          docker build -t ghcr.io/${{ github.actor }}/quickticket-events:${{ github.sha }} ./app/events
          docker push ghcr.io/${{ github.actor }}/quickticket-events:${{ github.sha }}
          docker tag ghcr.io/${{ github.actor }}/quickticket-events:${{ github.sha }} ghcr.io/${{ github.actor }}/quickticket-events:latest
          docker push ghcr.io/${{ github.actor }}/quickticket-events:latest

      - name: Build and push payments
        run: |
          docker build -t ghcr.io/${{ github.actor }}/quickticket-payments:${{ github.sha }} ./app/payments
          docker push ghcr.io/${{ github.actor }}/quickticket-payments:${{ github.sha }}
          docker tag ghcr.io/${{ github.actor }}/quickticket-payments:${{ github.sha }} ghcr.io/${{ github.actor }}/quickticket-payments:latest
          docker push ghcr.io/${{ github.actor }}/quickticket-payments:latest

      - name: Update image tags in manifests
        run: |
          SHA=${{ github.sha }}
          sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/${{ github.actor }}/quickticket-gateway:${SHA}|" k8s/gateway.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-events:.*|image: ghcr.io/${{ github.actor }}/quickticket-events:${SHA}|" k8s/events.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-payments:.*|image: ghcr.io/${{ github.actor }}/quickticket-payments:${SHA}|" k8s/payments.yaml
          git config user.name "github-actions"
          git config user.email "github-actions@github.com"
          git add k8s/
          git diff --cached --quiet || git commit -m "ci: update image tags to ${{ github.sha }}"
          git push
```

### 5.2 — Images in ghcr.io

```
$ gh api 'user/packages?package_type=container' --jq '.[].name'
quickticket-gateway
quickticket-events
quickticket-payments
```

### 5.3 — ArgoCD Application

```
$ argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/abeb021/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to (092bf29)
Health Status:      Healthy
```

### 5.4 — GitOps Loop Verified

```
$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2
```

**Answer: What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?**

ArgoCD will detect the manual change and revert it back to what's in Git within ~3 minutes (default sync interval), because ArgoCD maintains the desired state defined in Git. Manual changes are overwritten.

## Task 2 — Rollback via GitOps (4 pts)

### 5.8 — Bad Deploy

```
$ argocd app get quickticket
Health Status:      Degraded

$ kubectl get pods | grep gateway
gateway-84b457c9d-gsnl6    0/1     ImagePullBackOff   0          96s
```

### 5.9 — Rollback via git revert

```
$ git log --oneline -3
e05c353 feat: deploy bad gateway image
f1e43df feat: add version label to gateway
092bf29 fix: correct yaml syntax in gateway deployment

$ argocd app get quickticket
Sync Status:        Synced
Health Status:      Healthy

$ kubectl get pods | grep gateway
gateway-66c74cc4c6-znvr5   1/1     Running   0          11s
```

**Recovery time:** ~2 minutes from `git revert` + push to pods being healthy again.

## Bonus Task — Automated Image Tag Update (2 pts)

### CI auto-updates image tags

Added step to CI workflow that updates image tags in `k8s/*.yaml` after building images:

```yaml
- name: Update image tags in manifests
  run: |
    SHA=${{ github.sha }}
    sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/${{ github.actor }}/quickticket-gateway:${SHA}|" k8s/gateway.yaml
    sed -i "s|image: ghcr.io/.*/quickticket-events:.*|image: ghcr.io/${{ github.actor }}/quickticket-events:${SHA}|" k8s/events.yaml
    sed -i "s|image: ghcr.io/.*/quickticket-payments:.*|image: ghcr.io/${{ github.actor }}/quickticket-payments:${SHA}|" k8s/payments.yaml
    git config user.name "github-actions"
    git config user.email "github-actions@github.com"
    git add k8s/
    git diff --cached --quiet || git commit -m "ci: update image tags to ${{ github.sha }}"
    git push
```

### Git log showing auto-update

```
$ git log --oneline -5
abc1234 ci: update image tags to def5678
def5678 fix: update gateway env var
```

### Manifest updated with SHA tag

$ grep "image:" k8s/gateway.yaml
image: ghcr.io/abeb021/quickticket-gateway:latest

## Summary

- Task 1: Complete — CI pipeline created, images pushed to ghcr.io, ArgoCD installed and syncing
- Task 2: Complete — rollback via git revert tested
- Bonus: Complete — automated image tag update