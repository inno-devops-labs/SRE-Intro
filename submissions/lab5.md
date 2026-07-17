# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

### 5.1: CI Workflow

See `.github/workflows/ci.yml` — builds and pushes all 3 images to ghcr.io on push to main, then auto-updates image tags in k8s manifests.

### 5.2: CI Run — Green

GitHub Actions run #2 "feat: trigger CI pipeline" completed successfully (45s).
Link: https://github.com/aaammi/SRE-Intro/actions/runs/2

### 5.3: Images Pushed to ghcr.io

CI auto-updated manifests with commit `39be3c2`:

```
image: ghcr.io/aaammi/quickticket-gateway:43b18db75eb1de6fd61526aacaad76bcfc54ce3a
image: ghcr.io/aaammi/quickticket-events:43b18db75eb1de6fd61526aacaad76bcfc54ce3a
image: ghcr.io/aaammi/quickticket-payments:43b18db75eb1de6fd61526aacaad76bcfc54ce3a
```

### 5.4–5.6: ArgoCD

ArgoCD was installed on the k3d cluster but could not connect to GitHub due to network restrictions (k3d containers unable to reach external hosts — likely VPN/proxy interference). The ArgoCD server pod was running but unable to clone the repository.

```
argocd-server-7c8986577c-f4w44   1/1     Running   0
```

Error: `Unable to generate manifests: failed to list refs: EOF`

**Note:** This is an environment-specific network issue, not a configuration error. The CI pipeline and ghcr.io integration work correctly.

### 5.7: Answer — What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?

ArgoCD continuously compares the desired state (from Git) with the actual state (in the cluster). If someone manually edits a resource with `kubectl edit`, ArgoCD detects the drift — the resource shows as "OutOfSync" in the ArgoCD dashboard. With `sync-policy: automated`, ArgoCD will automatically revert the manual change to match what's in Git. This is the core GitOps principle: Git is the single source of truth, and manual cluster changes are overwritten.

---

## Bonus Task — Automated Image Tag Update

Implemented in `.github/workflows/ci.yml`. After building and pushing images, the workflow:

1. Updates image tags in `k8s/*.yaml` with the new commit SHA
2. Commits and pushes the manifest change
3. Skips CI on its own commits (`if: "!startsWith(github.event.head_commit.message, 'ci:')"`) to prevent infinite loops

Git log showing the auto-update:

```
3e68fad feat: use Always pull policy for registry images
39be3c2 ci: update image tags to 43b18db75eb1de6fd61526aacaad76bcfc54ce3a
43b18db feat: trigger CI pipeline
2bef945 ci: add CI pipeline for QuickTicket
```

Commit `39be3c2` was created automatically by GitHub Actions after building images from `43b18db`.
