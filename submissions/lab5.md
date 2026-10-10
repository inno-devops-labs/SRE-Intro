# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

### 5.1: CI Workflow

See `.github/workflows/ci.yml` — builds and pushes all 3 images to ghcr.io on push to main, then automatically updates image tags in Kubernetes manifests.

---

### 5.2: CI Run — Green

GitHub Actions run "feat: trigger CI pipeline" completed successfully.

The pipeline:

* builds Docker images
* pushes them to ghcr.io
* updates manifests in `k8s/`

---

### 5.3: Images Pushed to ghcr.io

CI auto-updated manifests with commit SHA:

```
image: ghcr.io/aaammi/quickticket-gateway:43b18db75eb1de6fd61526aacaad76bcfc54ce3a
image: ghcr.io/aaammi/quickticket-events:43b18db75eb1de6fd61526aacaad76bcfc54ce3a
image: ghcr.io/aaammi/quickticket-payments:43b18db75eb1de6fd61526aacaad76bcfc54ce3a
```

Tags are based on commit hash, ensuring reproducibility.

---

### 5.4–5.6: ArgoCD

ArgoCD was installed in the k3d cluster.

```
argocd-server-xxxxx   1/1   Running   0
```

However, it could not connect to the GitHub repository.

Error:

```
Unable to generate manifests: failed to list refs: EOF
```

Cause:

* k3d containers cannot access external network
* likely due to VPN or proxy restrictions

This is an environment issue, not a configuration problem.

---

### 5.7: Answer

If someone manually runs `kubectl edit` on a resource managed by ArgoCD:

* ArgoCD detects a difference between:

  * desired state (Git)
  * actual state (cluster)
* the application becomes `OutOfSync`
* if auto-sync is enabled, ArgoCD reverts the change

Git remains the single source of truth.

---

## Bonus Task — Automated Image Tag Update

Implemented in `.github/workflows/ci.yml`.

After building and pushing images, the workflow:

1. Updates image tags in `k8s/*.yaml`
2. Commits the change
3. Pushes it to the repository

To prevent infinite loops:

```
if: "!startsWith(github.event.head_commit.message, 'ci:')"
```

---

### Git log

```
3e68fad feat: use Always pull policy for registry images
39be3c2 ci: update image tags to 43b18db75eb1de6fd61526aacaad76bcfc54ce3a
43b18db feat: trigger CI pipeline
2bef945 ci: add CI pipeline for QuickTicket
```

Commit `39be3c2` was created automatically by the CI pipeline.

---

## Result

* CI pipeline builds and pushes images
* manifests are updated automatically
* GitOps flow is implemented
* ArgoCD is deployed but cannot sync due to network restrictions

