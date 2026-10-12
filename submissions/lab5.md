# Lab 5 Submission — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

### 5.1–5.2: GitHub Actions CI run (green check)

**Workflow:** `.github/workflows/ci.yml`

**Successful CI run:**

https://github.com/arsenez2006/SRE-Intro/actions/runs/36360868981


All three images built and pushed to `ghcr.io`:

```
ghcr.io/arsenez2006/quickticket-gateway:43121f9f5c88e4a79a9322a3c10cc408e79849d0
ghcr.io/arsenez2006/quickticket-events:43121f9f5c88e4a79a9322a3c10cc408e79849d0
ghcr.io/arsenez2006/quickticket-payments:43121f9f5c88e4a79a9322a3c10cc408e79849d0
```

---

### 5.3: K8s manifests updated for registry images

Deployments use `ghcr.io/arsenez2006/quickticket-*` images with `imagePullPolicy: Always`.

---

### 5.4–5.5: ArgoCD installed and Application created

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=Available deployment/argocd-server -n argocd --timeout=120s

argocd app create quickticket \
  --repo https://github.com/arsenez2006/SRE-Intro.git \
  --path k8s \
  --directory-recurse=false \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace default \
  --sync-policy automated
```

---

### 5.5: `argocd app get quickticket` — Synced + Healthy

```
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/arsenez2006/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (a186456)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
```

---

### 5.6: GitOps loop — Git change synced to cluster

Added `version: v2` label to `k8s/gateway.yaml` Deployment metadata. After ArgoCD sync:

```bash
$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2
```

Git change → ArgoCD detected → cluster updated. No `kubectl apply` needed.

---

### 5.7: What happens if someone manually runs `kubectl edit` on an ArgoCD-managed resource?

ArgoCD continuously compares the live cluster state against the desired state in Git. If someone runs `kubectl edit` on a managed Deployment, the change is detected as **drift** (OutOfSync). With automated sync enabled, ArgoCD will **revert the manual edit** on the next reconciliation cycle and restore the resource to match Git. The manual change is temporary — Git remains the source of truth, not the cluster.

---

## Task 2 — Rollback via GitOps

### 5.8: Bad deploy — Degraded / ImagePullBackOff

Changed gateway image to a non-existent tag and pushed:

```yaml
image: ghcr.io/arsenez2006/quickticket-gateway:does-not-exist
```

```
$ argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/arsenez2006/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (a13ebe6)
Health Status:      Progressing

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH       HOOK  MESSAGE
       Service     default    events    Synced  Healthy            service/events unchanged
       Service     default    gateway   Synced  Healthy            service/gateway unchanged
       Service     default    redis     Synced  Healthy            service/redis unchanged
       Service     default    payments  Synced  Healthy            service/payments unchanged
       Service     default    postgres  Synced  Healthy            service/postgres unchanged
apps   Deployment  default    redis     Synced  Healthy            deployment.apps/redis unchanged
apps   Deployment  default    events    Synced  Healthy            deployment.apps/events unchanged
apps   Deployment  default    payments  Synced  Healthy            deployment.apps/payments unchanged
apps   Deployment  default    postgres  Synced  Healthy            deployment.apps/postgres unchanged
apps   Deployment  default    gateway   Synced  Progressing        deployment.apps/gateway configured
```

```
$ kubectl get pods 
NAME                        READY   STATUS             RESTARTS   AGE
events-678bbbbf5f-rr9xj     1/1     Running            0          31m
gateway-69c45854b-v9c5f     1/1     Running            0          16m
gateway-988d46f48-j4z2v     0/1     ImagePullBackOff   0          2m38s
payments-fbccb4c9c-d6hxf    1/1     Running            0          31m
postgres-66df5bd7f6-dn4xl   1/1     Running            0          31m
redis-7957b48d69-2rc9h      1/1     Running            0          31m
```

---

### 5.9: Rollback via `git revert`

```bash
git revert HEAD --no-edit
git push origin main
argocd app sync quickticket
```

```
$ git log --oneline -3
ce21bb3 (HEAD -> main, origin/main, origin/HEAD) Revert "k8s(lab5): deploy new gateway version"
a13ebe6 k8s(lab5): deploy new gateway version
a186456 (origin/feature/lab5, feature/lab5) k8s(lab5): add version label to gateway
```

```
$ argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/arsenez2006/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (ce21bb3)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
```

**How long from `git revert` + push to pods being healthy again?**

Approximately **65 seconds** — from `git push` of the revert commit until ArgoCD showed `Healthy` and the gateway pod was `1/1 Running` with the correct image. No `kubectl rollout undo` was needed; Git history was the rollback mechanism.

---

## Bonus Task — Automated Image Tag Update

The CI workflow includes auto-tag update with infinite-loop prevention:

```yaml
jobs:
  build:
    if: ${{ !startsWith(github.event.head_commit.message, 'ci:') }}
    ...
      - name: Update image tags in manifests
        run: |
          SHA=${{ github.sha }}
          sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/${{ github.actor }}/quickticket-gateway:${SHA}|" k8s/gateway.yaml
          ...

      - name: Commit and push manifest update
        run: |
          git commit -m "ci: update image tags to ${{ github.sha }}"
          git push
```

**Git log showing code commit → CI tag-update commit:**

```
3b7697e (HEAD -> main, origin/main, origin/HEAD) ci: update image tags to 1d72368a957f486411aacb8a4d783686492b9e74
1d72368 ci(lab5): bonus
```

Each application commit is immediately followed by a `ci: update image tags` commit from GitHub Actions. The `ci:` commits do **not** re-trigger the workflow (filtered by `if:` condition). ArgoCD synced the auto-updated manifests without manual intervention — gateway pod runs `ghcr.io/arsenez2006/quickticket-gateway:7c9d39a...` after the first CI cycle.