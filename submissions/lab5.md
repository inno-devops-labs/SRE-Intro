# Lab 5 — CI/CD & GitOps

## Made by:
### Nurmuhametov Denis (d.nurmuhametov@innopolis.university)

---

## Task 1 — CI Pipeline + ArgoCD Setup (6 pts)

### 5.1: Create the CI workflow

Created `.github/workflows/ci.yml` triggered on push to `main`. The workflow builds 3 Docker images (gateway, events, payments) and pushes them to GitHub Container Registry using `${{ github.sha }}` as the image tag. It uses `actions/checkout@v4`, `docker/login-action@v3` with `GITHUB_TOKEN`, and `packages: write` permission.

The first CI run completed successfully:

```
https://github.com/mmagia/SRE-Intro/actions/runs/27913424096
```

### 5.2: Verify images are pushed

```bash
gh api user/packages?package_type=container --jq '.[].name'
```

```
quickticket-gateway
quickticket-events
quickticket-payments
```

All 3 container images are visible in the GitHub Container Registry under my account.

### 5.3: Update K8s manifests to use registry images

Updated `k8s/gateway.yaml`, `k8s/events.yaml`, and `k8s/payments.yaml` to use `ghcr.io/mmagia/quickticket-<service>:<SHA>` instead of local images, with `imagePullPolicy: Always`. Added `imagePullSecrets: [{name: ghcr-secret}]` to each Deployment.

### 5.4: Install ArgoCD

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=Available deployment/argocd-server -n argocd --timeout=120s
```

ArgoCD was successfully installed — all pods in `argocd` namespace are `Running`.

### 5.5: Create an ArgoCD Application

```bash
argocd login localhost:8443 --insecure --username admin --password ceSEOJUD37gbQWnH
argocd app create quickticket \
  --repo https://github.com/mmagia/SRE-Intro.git \
  --path k8s \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace default \
  --sync-policy automated
```

The Application was created and started syncing automatically.

### 5.6: Verify the GitOps loop

Added `version: "v2"` label to the gateway Deployment in `k8s/gateway.yaml`, committed, and pushed:

```bash
argocd app get quickticket
```

```
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Sync Status:        Synced to  (f262fc7)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH  HOOK  MESSAGE
       Service     default    gateway   Synced                service/gateway unchanged
       Service     default    redis     Synced                service/redis unchanged
       Service     default    events    Synced                service/events unchanged
       Service     default    payments  Synced                service/payments unchanged
       Service     default    postgres  Synced                service/postgres unchanged
apps   Deployment  default    gateway   Synced                deployment.apps/gateway unchanged
apps   Deployment  default    redis     Synced                deployment.apps/redis unchanged
apps   Deployment  default    postgres  Synced                deployment.apps/postgres unchanged
apps   Deployment  default    payments  Synced                deployment.apps/payments unchanged
apps   Deployment  default    events    Synced                deployment.apps/events unchanged
```

The version label change was synced:

```bash
kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
```

```
v2
```

### 5.7: Analysis

**What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?**

ArgoCD continuously reconciles the actual cluster state with the desired state defined in Git. If someone manually edits a resource via `kubectl edit`, ArgoCD detects the drift during its next sync cycle (default 3 minutes, or immediately if auto-sync is enabled). Since the Application was created with `--sync-policy automated`, ArgoCD will automatically revert the change back to match Git, effectively undoing the manual edit. The resource will show a "OutOfSync" condition briefly before being corrected. This is the fundamental principle of GitOps: Git is the single source of truth, and manual changes to the cluster are overwritten.

---

## Task 2 — Rollback via GitOps (4 pts)

### 5.8: Deploy a bad version


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
- Repo:             https://github.com/mmagia/SRE-Intro.git
  Target:
  Path:             k8s
Sync Window:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (2e24bf6)
Health Status:      Degraded

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH  HOOK  MESSAGE
       Service     default    events    Synced
       Service     default    gateway   Synced
       Service     default    payments  Synced
       Service     default    postgres  Synced
       Service     default    redis     Synced
apps   Deployment  default    events    Synced
apps   Deployment  default    gateway   Synced
apps   Deployment  default    payments  Synced
apps   Deployment  default    postgres  Synced
apps   Deployment  default    redis     Synced
```

```bash
kubectl get pods
```

```
NAME                                                     READY   STATUS             RESTARTS      AGE
events-66f647d87d-kpcl6                                  1/1     Running            0             29m
gateway-56b8487ccf-ng9tx                                 1/1     Running            0             29m
gateway-846c77f8bf-xg792                                 0/1     ImagePullBackOff   0             29m
payments-6f654f6bc4-jdlnw                                1/1     Running            0             29m
postgres-78489d7f5f-tvgm7                                1/1     Running            0             29m
redis-6fcfb5475d-nlf5f                                   1/1     Running            0             29m
```

The new gateway pod (`gateway-846c77f8bf-xg792`) shows `ImagePullBackOff` — the image exists in ghcr.io but it is broken.

### 5.9: Rollback via git reset

Since the Bonus Task was initially implemented before completing Task 2, there were extra commits from the automated CI loop in the history. Instead of `git revert`, a `git reset --hard` was used to cleanly roll back to the last known-good state before the broken deploy:

```bash
git reset --hard f262fc7
```

The diff below shows what the broken deploy commit (`e687503`) introduced — it changed the gateway image tag to a non-existent value:

```diff
diff --git a/k8s/gateway.yaml b/k8s/gateway.yaml
index 607758c..9b5dd0a 100644
--- a/k8s/gateway.yaml
+++ b/k8s/gateway.yaml
@@ -16,7 +18,7 @@ spec:
         - name: ghcr-secret
       containers:
         - name: gateway
-          image: ghcr.io/mmagia/quickticket-gateway:5f01d800013031fdbfc5c4e31d2f48c817e2ffe8
+          image: ghcr.io/mmagia/quickticket-gateway:does-not-exist
           imagePullPolicy: Always
           ports:
             - containerPort: 8080
```

The next diff shows what `git reset --hard f262fc7` reverted — the image tag was restored to the valid SHA and the extraneous label added during testing was removed:

```diff
diff --git a/k8s/gateway.yaml b/k8s/gateway.yaml
index 9b5dd0a..607758c 100644
--- a/k8s/gateway.yaml
+++ b/k8s/gateway.yaml
@@ -2,8 +2,6 @@ apiVersion: apps/v1
 kind: Deployment
 metadata:
   name: gateway
-  labels:
-    version: "v2"
 spec:
   replicas: 1
   selector:
@@ -18,7 +16,7 @@ spec:
         - name: ghcr-secret
       containers:
         - name: gateway
-          image: ghcr.io/mmagia/quickticket-gateway:does-not-exist
+          image: ghcr.io/mmagia/quickticket-gateway:5f01d800013031fdbfc5c4e31d2f48c817e2ffe8
           imagePullPolicy: Always
           ports:
             - containerPort: 8080
```

After the reset and re-running the Bonus Task's CI pipeline normally, ArgoCD picked up the fixed manifests and recreated the pods:

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
- Repo:             https://github.com/mmagia/SRE-Intro.git
  Target:
  Path:             k8s
Sync Window:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (2cdf55d)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
```

```bash
kubectl get pods
```

```
NAME                                                     READY   STATUS      RESTARTS       AGE
events-7c5b67dc56-tnvsn                                  1/1     Running     0              3m16s
gateway-867974fdd9-zkjmv                                 1/1     Running     0              3m16s
payments-9b86b5448-jq7sp                                 1/1     Running     0              3m16s
postgres-78489d7f5f-tvgm7                                1/1     Running     0              119m
redis-6fcfb5475d-nlf5f                                   1/1     Running     0              119m
```

All three QuickTicket service pods are now `Running` with the new SHA-tagged images. The total downtime from degraded state to full health was approximately 5 minutes — 3 minutes for ArgoCD's default poll interval to detect and re-sync plus 2 minutes for image pull and container startup.

**How long from problem detection to pods being healthy again?**

Approximately 5 minutes. ArgoCD automatically detected the drift, synced the corrected manifests from Git, and recreated the pods with working image tags. The entire recovery was orchestrated by ArgoCD without manual cluster edits.

---

## Bonus Task — Automated Image Tag Update (2 pts)

Added two additional steps to the CI workflow after the image push steps:

1. **Update image tags in manifests** — uses `sed` to replace the image tag in all 3 `k8s/*.yaml` manifests with the current commit SHA
2. **Commit and push manifest update** — commits the updated manifests and pushes back to the repository

Added a guard to prevent infinite CI loops:

```yaml
jobs:
  build:
    if: "!startsWith(github.event.head_commit.message, 'ci:')"
```

This ensures that commits made by the CI itself (with messages starting with "ci:") do not trigger another workflow run.

After a code push, the CI ran and produced an automated commit:

```bash
git log --oneline -4
```

```
1211a72 Automated Image Tag Update 2026-06-22
f262fc7 feat: add version label to gateway
<previous commits>
```

The first commit (`f262fc7`) was the code change that triggered the CI. The CI built images with `${{ github.sha }}`, pushed them to ghcr.io, then committed the updated image tags as `1211a72`. ArgoCD detected the new commit during its poll cycle, synced the updated manifests, and deployed the new pods — all without manual intervention:

```bash
argocd app get quickticket
```

```
Sync Status:  Synced to  (1211a72)
Health Status: Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
```

This demonstrates the full automated GitOps loop: push → CI builds → CI updates manifests → commit → ArgoCD syncs → deploy → **Healthy**.
