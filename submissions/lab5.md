# Lab 5 — CI/CD & GitOps

## Objective

The goal of this lab is to implement a CI/CD and GitOps workflow for the QuickTicket application.

The implemented pipeline:

```text
Git push
   ↓
GitHub Actions
   ↓
Docker build
   ↓
GitHub Container Registry (GHCR)
   ↓
ArgoCD
   ↓
Kubernetes
```

The lab also demonstrates Git-based rollback and automatic Docker image version tagging.

---

# Task 1 — CI/CD & GitOps

## 1. GitHub Actions CI

A GitHub Actions workflow was created in:

```text
.github/workflows/ci.yml
```

The workflow is triggered on pushes to `main` and on version tags matching `v*`.

The workflow:

1. Checks out the repository.
2. Logs in to GitHub Container Registry.
3. Builds Docker images for:

   * `gateway`
   * `events`
   * `payments`
4. Pushes the images to GHCR.

The images are tagged using the Git commit SHA.

Example:

```text
ghcr.io/wyroxx/quickticket-gateway:1aaff1a590851432a683b368cef4197c7bda2024
ghcr.io/wyroxx/quickticket-events:1aaff1a590851432a683b368cef4197c7bda2024
ghcr.io/wyroxx/quickticket-payments:1aaff1a590851432a683b368cef4197c7bda2024
```

The CI workflow completed successfully.

---

## 2. Kubernetes configuration

The Kubernetes manifests are stored in:

```text
k8s/
```

The deployments use the images published to GHCR.

The manifests also define CPU and memory requests/limits and health probes.

The private GHCR images are accessed using a Kubernetes secret:

```text
ghcr-secret
```

The secret is referenced through:

```yaml
imagePullSecrets:
  - name: ghcr-secret
```

The secret was successfully created in the `default` namespace.

Verification:

```text
NAME          TYPE                             DATA
ghcr-secret   kubernetes.io/dockerconfigjson   1
```

---

## 3. ArgoCD installation

ArgoCD was installed into the `argocd` namespace.

An ArgoCD Application named `quickticket` was created with:

```text
Repository:
https://github.com/wyroxx/SRE-Intro.git

Path:
k8s

Destination:
https://kubernetes.default.svc

Namespace:
default
```

The application uses automated synchronization:

```text
Sync Policy: Automated
```

ArgoCD therefore treats the Git repository as the source of truth for the Kubernetes manifests.

---

## 4. GitOps synchronization

A GitOps test was performed by modifying the Gateway deployment.

The following label was added:

```yaml
metadata:
  labels:
    version: "v2"
```

The change was committed and merged into `main`.

The resulting Git revision was:

```text
624ea8dc78c9ebb5b3e717b76870498f84ed07de
```

ArgoCD detected the new revision and automatically synchronized the Kubernetes resources.

Final ArgoCD status:

```text
Sync Policy:        Automated
Sync Status:        Synced to (624ea8d)
Health Status:      Healthy
```

The Gateway deployment was configured by ArgoCD:

```text
Deployment gateway    Synced    Healthy
```

The change was verified directly in Kubernetes:

```bash
kubectl get deployment gateway \
  -o jsonpath='{.metadata.labels.version}'
```

Result:

```text
v2
```

This confirms the GitOps loop:

```text
Git
 ↓
ArgoCD
 ↓
Kubernetes
```

---

## 5. Kubernetes health verification

All application deployments were healthy:

```text
NAME       READY   UP-TO-DATE   AVAILABLE
events     1/1     1            1
gateway    1/1     1            1
payments   1/1     1            1
postgres   1/1     1            1
redis      1/1     1            1
```

All application pods were running:

```text
events       1/1   Running
gateway      1/1   Running
payments     1/1   Running
postgres     1/1   Running
redis        1/1   Running
```

The deployed application images were:

```text
ghcr.io/wyroxx/quickticket-gateway:1aaff1a590851432a683b368cef4197c7bda2024
ghcr.io/wyroxx/quickticket-events:1aaff1a590851432a683b368cef4197c7bda2024
ghcr.io/wyroxx/quickticket-payments:1aaff1a590851432a683b368cef4197c7bda2024
```

---

# Task 2 — Git-based Rollback

## 1. Introduce a broken deployment configuration

To demonstrate rollback, the Gateway image reference was intentionally modified.

The resulting commit was:

```text
a2dad9d
test: break gateway image for rollback
```

The image line became:

```yaml
image: image: ghcr.io/wyroxx/quickticket-gateway:does-not-exist
```

This intentionally introduced an invalid Kubernetes manifest.

---

## 2. ArgoCD detected the failure

After pushing the broken commit to `main`, ArgoCD attempted to process the new repository state.

The application entered an error state:

```text
Sync Status: Unknown
Health Status: Healthy
```

ArgoCD reported:

```text
ComparisonError

Failed to load target state:
failed to generate manifest for source 1 of 1

failed to unmarshal "gateway.yaml":
yaml: line 21:
mapping values are not allowed in this context
```

This demonstrated that ArgoCD detected the invalid configuration before applying it to Kubernetes.

---

## 3. Git revert

Instead of manually modifying the Kubernetes cluster, the broken Git commit was reverted:

```bash
git revert HEAD --no-edit
```

This created the rollback commit:

```text
66cc6f5
Revert "test: break gateway image for rollback"
```

The rollback commit was pushed to `main`:

```bash
git push origin main
```

---

## 4. Automatic recovery through ArgoCD

After the rollback commit was pushed, ArgoCD automatically synchronized the repository again.

Final ArgoCD state:

```text
Sync Policy:        Automated
Sync Status:        Synced to (66cc6f5)
Health Status:      Healthy
```

The Gateway deployment returned to a healthy state.

Kubernetes verification:

```text
events       1/1   Running
gateway      1/1   Running
payments     1/1   Running
postgres     1/1   Running
redis        1/1   Running
```

The correct Gateway image was restored:

```text
ghcr.io/wyroxx/quickticket-gateway:1aaff1a590851432a683b368cef4197c7bda2024
```

The rollback therefore followed the GitOps model:

```text
Broken Git state
      ↓
    ArgoCD
      ↓
Configuration error
      ↓
   git revert
      ↓
Correct Git state
      ↓
    ArgoCD
      ↓
Healthy Kubernetes
```

No manual rollback operation was performed directly against the Kubernetes Deployment.

---

# Bonus — Automatic Image Version Tagging

The CI workflow was extended to support automatic Docker image version tags.

The workflow now listens for Git tags matching:

```yaml
tags:
  - 'v*'
```

When a normal commit is pushed to `main`, the image is tagged using the commit SHA.

When a version tag is pushed, for example:

```bash
git tag v1.0.0
git push origin v1.0.0
```

the workflow publishes both:

```text
<commit-sha>
```

and:

```text
1.0.0
```

image tags.

For example:

```text
ghcr.io/wyroxx/quickticket-gateway:1.0.0
ghcr.io/wyroxx/quickticket-events:1.0.0
ghcr.io/wyroxx/quickticket-payments:1.0.0
```

The CI workflow was updated in commit:

```text
10a1c2c
ci: add automatic version tags
```

A version tag was then created:

```text
v1.0.0
```

GitHub Actions successfully executed the workflow for both `main` and `v1.0.0`.

Successful CI run for the version tag:

```text
36327543546
```

The successful run demonstrates automatic version-based image publishing.

---

# Final Result

The QuickTicket project now has a complete CI/CD and GitOps workflow:

```text
                 Git
                  │
          ┌───────┴───────┐
          │               │
        main           v1.0.0
          │               │
          ▼               ▼
    GitHub Actions   GitHub Actions
          │               │
          ▼               ▼
        GHCR            GHCR
          │
          ▼
       ArgoCD
          │
          ▼
      Kubernetes
```

Implemented functionality:

* GitHub Actions CI
* Docker image builds
* GHCR image publishing
* SHA-based image tags
* Automatic version tags
* Kubernetes deployments
* GHCR authentication with `imagePullSecrets`
* ArgoCD Application
* Automated GitOps synchronization
* Git-to-Kubernetes synchronization verification
* Git-based rollback using `git revert`
* Automatic recovery through ArgoCD

Final ArgoCD state after rollback:

```text
Sync Status:   Synced
Health Status: Healthy
```

Final Kubernetes state:

```text
All deployments: 1/1
All pods:        Running
```
