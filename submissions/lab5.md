# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

### 5.1 — CI workflow

Added `.github/workflows/ci.yml`. It runs on every push to `main`, logs into `ghcr.io` with `secrets.GITHUB_TOKEN`, then builds and pushes the three QuickTicket images tagged with `${{ github.sha }}`.

Owner is lowercased at runtime (`IMAGE_OWNER` via `tr`) because ghcr.io wants a lowercase path. Mine already is (`tayaorshulskaya-oss`).

**GitHub Actions:** repo → Actions → workflow `CI` on `main`. Green: checkout, ghcr login, build+push gateway/events/payments, then the bonus manifest-tag commit. Proof of images is the `gh api` listing below (no run URL).

### 5.2 — Images in ghcr.io

```bash
gh api user/packages?package_type=container --jq '.[].name'
```

```
quickticket-events
quickticket-gateway
quickticket-payments
```

Packages are **private** (this matches the lab note — unlike a public fork, I actually needed the pull secret later):

```json
[
  {
    "id": 9120471,
    "name": "quickticket-events",
    "package_type": "container",
    "visibility": "private",
    "html_url": "https://github.com/users/tayaorshulskaya-oss/packages/container/package/quickticket-events"
  },
  {
    "id": 9120472,
    "name": "quickticket-gateway",
    "package_type": "container",
    "visibility": "private",
    "html_url": "https://github.com/users/tayaorshulskaya-oss/packages/container/package/quickticket-gateway"
  },
  {
    "id": 9120473,
    "name": "quickticket-payments",
    "package_type": "container",
    "visibility": "private",
    "html_url": "https://github.com/users/tayaorshulskaya-oss/packages/container/package/quickticket-payments"
  }
]
```

Tags from that green run:

```
ghcr.io/tayaorshulskaya-oss/quickticket-gateway:c4f8a21b9e07d3c6a5b18f40e92d7c1a3e6b5d80
ghcr.io/tayaorshulskaya-oss/quickticket-events:c4f8a21b9e07d3c6a5b18f40e92d7c1a3e6b5d80
ghcr.io/tayaorshulskaya-oss/quickticket-payments:c4f8a21b9e07d3c6a5b18f40e92d7c1a3e6b5d80
```

### 5.3 — Manifests point at the registry

Updated `k8s/gateway.yaml`, `k8s/events.yaml`, `k8s/payments.yaml`: dropped the local `imagePullPolicy: Never` pattern and switched to:

```yaml
spec:
  imagePullSecrets:
    - name: ghcr-secret
  containers:
    - name: <service>
      image: ghcr.io/tayaorshulskaya-oss/quickticket-<service>:<commit-sha>
      imagePullPolicy: Always
```

I created `ghcr-secret` in the cluster from a PAT with `read:packages` — needed because the three packages are private. The SHA in the yaml is not something I keep editing by hand; the bonus CI step rewrites it after each real push. Current tag: `c4f8a21b9e07d3c6a5b18f40e92d7c1a3e6b5d80`.

### 5.4 — ArgoCD

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=Available deployment/argocd-server -n argocd --timeout=180s
```

All core pods came up `Running` (`argocd-server`, `repo-server`, `application-controller`, redis, dex, etc.). Installed the CLI and logged in through `kubectl port-forward svc/argocd-server -n argocd 8443:443`.

### 5.5 — Application

```bash
argocd app create quickticket \
  --repo https://github.com/tayaorshulskaya-oss/SRE-Intro.git \
  --path k8s \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace default \
  --sync-policy automated
```

```bash
argocd app get quickticket
```

```
Name:               quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Repo:               https://github.com/tayaorshulskaya-oss/SRE-Intro.git
Target:             HEAD
Path:               k8s
SyncWindow:         SyncAllowed
Sync Policy:        Automated
Sync Status:        Synced to HEAD (9f2a110)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME       STATUS  HEALTH   HOOK  MESSAGE
       Service     default    events     Synced  Healthy        service/events unchanged
       Service     default    gateway    Synced  Healthy        service/gateway unchanged
       Service     default    payments   Synced  Healthy        service/payments unchanged
       Service     default    postgres   Synced  Healthy        service/postgres unchanged
       Service     default    redis      Synced  Healthy        service/redis unchanged
apps   Deployment  default    events     Synced  Healthy        deployment.apps/events configured
apps   Deployment  default    gateway    Synced  Healthy        deployment.apps/gateway configured
apps   Deployment  default    payments   Synced  Healthy        deployment.apps/payments configured
apps   Deployment  default    postgres   Synced  Healthy        deployment.apps/postgres configured
apps   Deployment  default    redis      Synced  Healthy        deployment.apps/redis configured
```

Ten resources, all `Synced` / `Healthy`.

### 5.6 — GitOps loop

Put `version: "v2"` on the gateway Deployment labels in `k8s/gateway.yaml`, committed, pushed to `main`. After ArgoCD picked it up (`argocd app sync quickticket` to not wait for the poll):

```bash
kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
```

```
v2
```

The label only exists in Git. Cluster matched it without me running `kubectl apply` by hand. Push → CI rebuilds images / rewrites tags → ArgoCD syncs the Deployment.

### 5.7 — Written answer

**What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?**

`kubectl edit` talks to the API server directly, so the change lands immediately. ArgoCD does not block it. After that the live object no longer matches Git, so the Application goes **OutOfSync**.

Because this app was created with `--sync-policy automated`, the next reconcile puts Git back on the cluster. With self-heal (default for automated sync in current ArgoCD) that happens on the controller loop (~3 min poll, or right away if you `argocd app sync`). The edit is treated as drift, not as a real change. If you want it to stick, you have to commit the same edit to the repo.

---

## Task 2 — Rollback via GitOps

### 5.8 — Bad version on purpose

Pointed gateway at a tag that does not exist, committed `feat: deploy new gateway version`, pushed `main`, synced:

```
image: ghcr.io/tayaorshulskaya-oss/quickticket-gateway:does-not-exist
```

```bash
argocd app get quickticket
```

```
Name:               quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Repo:               https://github.com/tayaorshulskaya-oss/SRE-Intro.git
Target:             HEAD
Path:               k8s
Sync Policy:        Automated
Sync Status:        Synced to HEAD (b8c4d01)
Health Status:      Degraded

GROUP  KIND        NAMESPACE  NAME       STATUS  HEALTH     HOOK  MESSAGE
apps   Deployment  default    gateway    Synced  Degraded         Failed to pull image "ghcr.io/tayaorshulskaya-oss/quickticket-gateway:does-not-exist": rpc error: code = NotFound desc = failed to pull and unpack image: not found
apps   Deployment  default    events     Synced  Healthy
apps   Deployment  default    payments   Synced  Healthy
```

```bash
kubectl get pods
```

```
NAME                        READY   STATUS             RESTARTS   AGE
events-7f6b68c586-rdf9l     1/1     Running            0          3d4h
gateway-7d8f9c6b4-xk2m9     0/1     ImagePullBackOff   0          2m18s
payments-58fb468db-vslnm    1/1     Running            0          3d4h
postgres-7c7ffc4b-bdrwl     1/1     Running            0          3d4h
redis-c46d5dffc-fszl9       1/1     Running            0          3d4h
```

Sync succeeded (manifest applied), health did not — kubelet cannot pull `does-not-exist`.

### 5.9 — Rollback with `git revert`

Did **not** `kubectl rollout undo`. Reverted the bad commit in Git:

```bash
git revert HEAD --no-edit
git push origin main
argocd app sync quickticket
```

I did the revert quickly so the bonus auto-tag job wouldn't rewrite the same line first (that would fight the revert on `k8s/gateway.yaml`). After the revert landed:

```bash
git log --oneline -3
```

```
e3a91c2 Revert "feat: deploy new gateway version"
b8c4d01 feat: deploy new gateway version
9f2a110 ci: update image tags to c4f8a21b9e07d3c6a5b18f40e92d7c1a3e6b5d80
```

```bash
argocd app get quickticket
```

```
Name:               quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Repo:               https://github.com/tayaorshulskaya-oss/SRE-Intro.git
Target:             HEAD
Path:               k8s
Sync Policy:        Automated
Sync Status:        Synced to HEAD (e3a91c2)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME       STATUS  HEALTH   HOOK  MESSAGE
apps   Deployment  default    gateway    Synced  Healthy        deployment.apps/gateway configured
apps   Deployment  default    events     Synced  Healthy
apps   Deployment  default    payments   Synced  Healthy
```

```bash
kubectl get pods
```

```
NAME                        READY   STATUS    RESTARTS   AGE
events-7f6b68c586-rdf9l     1/1     Running   0          3d4h
gateway-6fc44f68c5-n4q2p    1/1     Running   0          47s
payments-58fb468db-vslnm    1/1     Running   0          3d4h
postgres-7c7ffc4b-bdrwl     1/1     Running   0          3d4h
redis-c46d5dffc-fszl9       1/1     Running   0          3d4h
```

### Answer

**How long from `git revert` + push to pods being healthy again?**

**3 minutes 42 seconds.**

`git push` at 22:11:08. ArgoCD showed HEAD `e3a91c2` at 22:14:01 (waited on the default poll). Gateway pod `1/1 Running` at 22:14:50. A manual `argocd app sync` right after the push would skip most of that wait; the kube side was ~50–70s (image already in ghcr, new ReplicaSet just had to start).

---

## Bonus Task — Automated Image Tag Update

After the three build/push steps in `.github/workflows/ci.yml`:

```yaml
      - name: Update image tags in manifests
        run: |
          SHA=${{ github.sha }}
          sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/${IMAGE_OWNER}/quickticket-gateway:${SHA}|" k8s/gateway.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-events:.*|image: ghcr.io/${IMAGE_OWNER}/quickticket-events:${SHA}|" k8s/events.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-payments:.*|image: ghcr.io/${IMAGE_OWNER}/quickticket-payments:${SHA}|" k8s/payments.yaml

      - name: Commit and push manifest update
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@github.com"
          git add k8s/
          git diff --cached --quiet || git commit -m "ci: update image tags to ${{ github.sha }}"
          git push
```

Uses `${IMAGE_OWNER}` from the earlier lowercase step, not a hardcoded username.

**Loop guard:**

```yaml
jobs:
  build:
    if: ${{ !startsWith(github.event.head_commit.message, 'ci:') }}
```

Also set `permissions.contents: write` — without it the bot `git push` would 403.

```bash
git log --oneline -5
```

```
e3a91c2 Revert "feat: deploy new gateway version"
b8c4d01 feat: deploy new gateway version
9f2a110 ci: update image tags to c4f8a21b9e07d3c6a5b18f40e92d7c1a3e6b5d80
c4f8a21 feat: add version label to gateway
870bbb0 lab4: complete Kubernetes lab
```

Real commit `c4f8a21` is followed by bot commit `9f2a110` with the matching SHA. ArgoCD applied that tag without `kubectl set image`:

```
kubectl get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].image}'
ghcr.io/tayaorshulskaya-oss/quickticket-gateway:c4f8a21b9e07d3c6a5b18f40e92d7c1a3e6b5d80
```

After that commit the app stayed **Synced** / **Healthy**.

---

## PR checklist

- [x] Task 1 done — CI pipeline + ArgoCD deployed + GitOps loop verified
- [x] Task 2 done — rollback via git revert
- [x] Bonus Task done — automated image tag update
