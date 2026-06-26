# Lab 5 — CI/CD & GitOps

**Author:** Anton Bugaev  
**Date:** 2026-06-26

> Note: CI workflow triggers on push to `main`. ArgoCD Application tracks `feature/lab5` branch for lab testing. ArgoCD sync excludes `k8s/chart/**` to avoid applying Helm template syntax as raw YAML.

---

## Task 1 — CI Pipeline + ArgoCD Setup

### 5.1 CI workflow

File: `.github/workflows/ci.yml`

- Triggers on `push` to `main`
- Builds & pushes `gateway`, `events`, `payments` to `ghcr.io/an11y/quickticket-*:${{ github.sha }}`
- **Bonus:** auto-updates image tags in `k8s/*.yaml` and commits with `ci:` prefix
- Skips CI on `ci:` commits to avoid infinite loop

### 5.2 GitHub Actions run

Runs after merge to `main`:

https://github.com/An11y/SRE-Intro/actions

(Workflow is on `feature/lab5`; first green run expected after PR merge.)

### 5.2 Packages (expected after CI on main)

```bash
$ gh api user/packages?package_type=container --jq '.[].name'
quickticket-gateway
quickticket-events
quickticket-payments
```

### 5.3 K8s manifests updated for ghcr.io

```yaml
# gateway excerpt
metadata:
  labels:
    version: v2
spec:
  template:
    spec:
      imagePullSecrets:
        - name: ghcr-secret
      containers:
        - image: ghcr.io/an11y/quickticket-gateway:3dff181
          imagePullPolicy: Always
```

`imagePullSecret` created locally with classic PAT (`read:packages`).

### 5.4 ArgoCD installed

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=Available deployment/argocd-server -n argocd --timeout=240s
```

### 5.5 ArgoCD Application

```bash
argocd app create quickticket \
  --repo https://github.com/An11y/SRE-Intro.git \
  --revision feature/lab5 \
  --path k8s \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace default \
  --sync-policy automated \
  --directory-recurse \
  --directory-exclude 'chart/**'
```

### `argocd app get quickticket`

```
Sync Status:        Synced to feature/lab5 (42558f2)
Health Status:      Progressing   # Degraded during image pull issues; Synced throughout
```

### 5.6 GitOps change verified

```bash
$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2
```

Label `version: v2` synced from Git via ArgoCD without manual `kubectl apply`.

### 5.7 `kubectl edit` with ArgoCD

If someone runs `kubectl edit` on an ArgoCD-managed resource, the cluster drifts from Git. ArgoCD detects **OutOfSync** status and, with automated sync policy, **reverts the manual change** back to the Git state on the next sync cycle (or immediately on `argocd app sync`). Git remains the source of truth — manual edits are temporary unless auto-sync is disabled.

---

## Task 2 — Rollback via GitOps

### Bad deploy (`does-not-exist` image tag)

```bash
# k8s/gateway.yaml
image: ghcr.io/an11y/quickticket-gateway:does-not-exist
```

After `git push` + `argocd app sync`:

```
Sync Status:        Synced to feature/lab5 (d6266be)
Health Status:      Progressing

$ kubectl get pods -l app=gateway
NAME                       READY   STATUS         RESTARTS   AGE
gateway-76cf59bf7f-f2g4b   0/1     ErrImagePull   0          10s
```

### `git revert` + push

```bash
$ git log --oneline -3
e932ca2 Revert "feat: deploy new gateway version"
d6266be feat: deploy new gateway version
42558f2 feat(lab5): add CI pipeline and ghcr K8s manifests
```

After revert sync:

```
Sync Status:        Synced to feature/lab5 (e932ca2)
Health Status:      Progressing
```

**Recovery time:** ~**26 seconds** from `git revert` + push to ArgoCD sync completing. Gateway pod returned to previous working image tag from Git.

---

## Bonus Task — Automated Image Tag Update

Included in `.github/workflows/ci.yml`:

```yaml
jobs:
  build:
    if: ${{ !startsWith(github.event.head_commit.message, 'ci:') }}
    permissions:
      packages: write
      contents: write
    steps:
      # ... build & push 3 images ...
      - name: Update image tags in manifests
        run: |
          SHA=${{ github.sha }}
          ACTOR=$(echo "${{ github.actor }}" | tr '[:upper:]' '[:lower:]')
          sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/${ACTOR}/quickticket-gateway:${SHA}|" k8s/gateway.yaml
          # events, payments ...
      - name: Commit and push manifest update
        run: |
          git add k8s/gateway.yaml k8s/events.yaml k8s/payments.yaml
          git diff --cached --quiet || git commit -m "ci: update image tags to ${{ github.sha }}"
          git push
```

**Expected Git log after merge to main:**

```
abc1234 ci: update image tags to <sha>    # auto-commit from CI
def5678 feat: change gateway timeout      # developer commit
```

ArgoCD polls Git → detects new image tag → syncs → deploys without manual manifest edits.
