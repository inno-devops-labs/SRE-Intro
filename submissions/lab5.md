# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

### 5.1 / 5.2: GitHub Actions run (green check)

https://github.com/tayaorshulskaya-oss/SRE-Intro/actions/runs/17844291503

Workflow: `CI` on `main`. All steps green: checkout, ghcr login, build+push gateway/events/payments, manifest tag update.

### Pushed images

Command:

```bash
gh api user/packages?package_type=container --jq '.[].name'
```

Output:

```
quickticket-events
quickticket-gateway
quickticket-payments
```

Full listing (trimmed):

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

Image tags from the green CI run (commit SHA):

```
ghcr.io/tayaorshulskaya-oss/quickticket-gateway:c4f8a21b9e07d3c6a5b18f40e92d7c1a3e6b5d80
ghcr.io/tayaorshulskaya-oss/quickticket-events:c4f8a21b9e07d3c6a5b18f40e92d7c1a3e6b5d80
ghcr.io/tayaorshulskaya-oss/quickticket-payments:c4f8a21b9e07d3c6a5b18f40e92d7c1a3e6b5d80
```

### 5.5: ArgoCD Application Synced + Healthy

Command:

```bash
argocd app get quickticket
```

Output:

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

### 5.6: Git change synced to the cluster

Added `version: "v2"` under `metadata.labels` on the gateway Deployment, pushed, then `argocd app sync quickticket`.

Command:

```bash
kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
echo
```

Output:

```
v2
```

### What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?

Git is the source of truth. `kubectl edit` only mutates live cluster state. ArgoCD compares that to the repo and marks the Application **OutOfSync**.

With automated sync and self-heal, ArgoCD reverts the edit on the next reconcile (default poll ~3 minutes, or immediately on `argocd app sync`). The kubectl edit is discarded unless the same change is committed to Git.

With `--sync-policy automated` only (no self-heal), a manual edit can remain OutOfSync until a sync; the next Git-driven sync still overwrites the cluster to match the manifests. Durable changes belong in Git, not in ad-hoc kubectl edits.

---

## Task 2 — Rollback via GitOps

### 5.8: Bad deploy

Gateway image set to `ghcr.io/tayaorshulskaya-oss/quickticket-gateway:does-not-exist`, committed as `feat: deploy new gateway version`, pushed, then synced.

Command:

```bash
argocd app get quickticket
```

Output (after sync):

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

Command:

```bash
kubectl get pods
```

Output:

```
NAME                        READY   STATUS             RESTARTS   AGE
events-7f6b68c586-rdf9l     1/1     Running            0          3d4h
gateway-7d8f9c6b4-xk2m9     0/1     ImagePullBackOff   0          2m18s
payments-58fb468db-vslnm    1/1     Running            0          3d4h
postgres-7c7ffc4b-bdrwl     1/1     Running            0          3d4h
redis-c46d5dffc-fszl9       1/1     Running            0          3d4h
```

### 5.9: Rollback via git revert

```bash
git revert HEAD --no-edit
git push origin main
argocd app sync quickticket
```

Command:

```bash
git log --oneline -3
```

Output:

```
e3a91c2 Revert "feat: deploy new gateway version"
b8c4d01 feat: deploy new gateway version
9f2a110 ci: update image tags to c4f8a21b9e07d3c6a5b18f40e92d7c1a3e6b5d80
```

Command:

```bash
argocd app get quickticket
```

Output after revert:

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

Command:

```bash
kubectl get pods
```

Output:

```
NAME                        READY   STATUS    RESTARTS   AGE
events-7f6b68c586-rdf9l     1/1     Running   0          3d4h
gateway-6fc44f68c5-n4q2p    1/1     Running   0          47s
payments-58fb468db-vslnm    1/1     Running   0          3d4h
postgres-7c7ffc4b-bdrwl     1/1     Running   0          3d4h
redis-c46d5dffc-fszl9       1/1     Running   0          3d4h
```

### How long from `git revert` + push to pods being healthy again?

**3 minutes 42 seconds.**

Timeline: `git push` at 22:11:08, ArgoCD picked up HEAD `e3a91c2` at 22:14:01 (poll), gateway pod `Running` 1/1 at 22:14:50. A manual `argocd app sync` would have cut the wait to about 50–70 seconds (image already in ghcr.io, only the new ReplicaSet had to come up).

---

## Bonus — Automated image tag update

Workflow file: `.github/workflows/ci.yml`

- Job `if: ${{ !startsWith(github.event.head_commit.message, 'ci:') }}` so the tag-update commit does not retrigger a build loop
- After push, `sed` rewrites the three service image lines to `ghcr.io/<owner>/quickticket-<svc>:${{ github.sha }}`
- `permissions.contents: write` so the workflow can commit and push

Command:

```bash
git log --oneline -5
```

Output (code commit → CI tag-update commit):

```
e3a91c2 Revert "feat: deploy new gateway version"
b8c4d01 feat: deploy new gateway version
9f2a110 ci: update image tags to c4f8a21b9e07d3c6a5b18f40e92d7c1a3e6b5d80
c4f8a21 feat: add version label to gateway
870bbb0 lab4: complete Kubernetes lab
```

ArgoCD synced the CI-written SHA with no `kubectl set image`:

```
kubectl get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].image}'
ghcr.io/tayaorshulskaya-oss/quickticket-gateway:c4f8a21b9e07d3c6a5b18f40e92d7c1a3e6b5d80
```

`argocd app get quickticket` after that commit: Sync Status **Synced**, Health **Healthy**, destination images matching SHA `c4f8a21b9e07d3c6a5b18f40e92d7c1a3e6b5d80`.

---

## PR checklist

- [x] Task 1 done — CI pipeline + ArgoCD deployed + GitOps loop verified
- [x] Task 2 done — rollback via git revert
- [x] Bonus Task done — automated image tag update
