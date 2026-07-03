# Lab 5 Submission — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

### 5.1–5.2: GitHub Actions CI run (green check)

**Workflow:** `.github/workflows/ci.yml`

**Successful CI run:**

https://github.com/stefan-genie/SRE-Intro/actions/runs/28249868947

```
$ gh run view 28249868947 --json url,conclusion,displayTitle,headSha
{
  "conclusion": "success",
  "displayTitle": "feat(lab5): add CI pipeline and ghcr.io image manifests",
  "headSha": "bd9d4778d2819fd037c912070c3fad4e8929caec",
  "url": "https://github.com/stefan-genie/SRE-Intro/actions/runs/28249868947"
}
```

All three images built and pushed to `ghcr.io/stefan-genie/`:

```
ghcr.io/stefan-genie/quickticket-gateway:bd9d4778d2819fd037c912070c3fad4e8929caec
ghcr.io/stefan-genie/quickticket-events:bd9d4778d2819fd037c912070c3fad4e8929caec
ghcr.io/stefan-genie/quickticket-payments:bd9d4778d2819fd037c912070c3fad4e8929caec
```

> `gh api user/packages` requires `read:packages` scope on the local token. Images are confirmed by successful CI push and running pods pulling from ghcr.io (see below).

**Packages on GitHub:** https://github.com/stefan-genie?tab=packages — `quickticket-gateway`, `quickticket-events`, `quickticket-payments`

---

### 5.3: K8s manifests updated for registry images

Deployments use `ghcr.io/stefan-genie/quickticket-*` images with `imagePullPolicy: Always` and `imagePullSecrets: ghcr-secret`.

```bash
kubectl create secret docker-registry ghcr-secret \
  --docker-server=ghcr.io \
  --docker-username=stefan-genie \
  --docker-password=<GITHUB_TOKEN>
```

---

### 5.4–5.5: ArgoCD installed and Application created

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=Available deployment/argocd-server -n argocd --timeout=120s

argocd app create quickticket \
  --repo https://github.com/stefan-genie/SRE-Intro.git \
  --path k8s \
  --directory-recurse=false \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace default \
  --sync-policy automated
```

`--directory-recurse=false` excludes `k8s/chart/` Helm templates from being applied as raw YAML.

---

### 5.5: `argocd app get quickticket` — Synced + Healthy

```
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
Source:
- Repo:             https://github.com/stefan-genie/SRE-Intro.git
  Path:             k8s
Sync Policy:        Automated
Sync Status:        Synced to (2e0adcc)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH
       Service     default    events    Synced  Healthy
       Service     default    gateway   Synced  Healthy
       Service     default    redis     Synced  Healthy
       Service     default    payments  Synced  Healthy
       Service     default    postgres  Synced  Healthy
apps   Deployment  default    redis     Synced  Healthy
apps   Deployment  default    gateway   Synced  Healthy
apps   Deployment  default    postgres  Synced  Healthy
apps   Deployment  default    events    Synced  Healthy
apps   Deployment  default    payments  Synced  Healthy
```

---

### 5.6: GitOps loop — Git change synced to cluster

Added `version: v2` label to `k8s/gateway.yaml` Deployment metadata. After ArgoCD sync:

```bash
$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2

$ kubectl get deployment gateway -o jsonpath='{.spec.template.spec.containers[0].image}'
ghcr.io/stefan-genie/quickticket-gateway:bd9d4778d2819fd037c912070c3fad4e8929caec
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
image: ghcr.io/stefan-genie/quickticket-gateway:does-not-exist
```

```
$ argocd app get quickticket
Sync Status:        Synced to (eef146d)
Health Status:      Progressing

apps   Deployment  default    gateway   Synced  Progressing
```

```
$ kubectl get pods -l app=gateway
NAME                       READY   STATUS             RESTARTS   AGE
gateway-5d67fc55c7-rsrbr   1/1     Running            0          3m24s
gateway-5c9d8b6cbf-rnq9d   0/1     ImagePullBackOff   0          2m20s
```

```
  Warning  Failed  Failed to pull image "ghcr.io/stefan-genie/quickticket-gateway:does-not-exist": not found
  Warning  Failed  Error: ImagePullBackOff
```

---

### 5.9: Rollback via `git revert`

```bash
git revert HEAD --no-edit
git push origin main
argocd app sync quickticket
```

```
$ git log --oneline -4
b892cab ci: update image tags to a46908e9b8f21b2792647220d5ee1296f9d18b74
a46908e Revert "feat: deploy new gateway version"
eef146d feat: deploy new gateway version
f06dc80 ci: update image tags to 7c9d39a2595a0fa15a8604f586dfc723fd39ccc6
```

```
$ argocd app get quickticket
Sync Status:        Synced to (a46908e)
Health Status:      Healthy
```

```
$ kubectl get pods -l app=gateway
NAME                       READY   STATUS    RESTARTS   AGE
gateway-79f7b5c979-fpchk   1/1     Running   0          32s
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
b892cab ci: update image tags to a46908e9b8f21b2792647220d5ee1296f9d18b74
a46908e Revert "feat: deploy new gateway version"
eef146d feat: deploy new gateway version
f06dc80 ci: update image tags to 7c9d39a2595a0fa15a8604f586dfc723fd39ccc6
7c9d39a feat: deploy new gateway version
2e0adcc ci: update image tags to bd9d4778d2819fd037c912070c3fad4e8929caec
bd9d477 feat(lab5): add CI pipeline and ghcr.io image manifests
```

Each application commit (`bd9d477`, `7c9d39a`, …) is immediately followed by a `ci: update image tags` commit from GitHub Actions. The `ci:` commits do **not** re-trigger the workflow (filtered by `if:` condition). ArgoCD synced the auto-updated manifests without manual intervention — gateway pod runs `ghcr.io/stefan-genie/quickticket-gateway:7c9d39a...` after the first CI cycle.
