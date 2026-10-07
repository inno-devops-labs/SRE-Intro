# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

### GitHub Actions CI

The CI workflow builds and pushes all three QuickTicket service images to GitHub Container Registry.

GitHub Actions run:

https://github.com/MiniMaxC/SRE-Intro/actions/runs/36353180786

The successful CI run used commit SHA:

```text
117336566e7a6d1f9bb7613eee1695e3b345fe9a
```

The images were tagged with the full immutable commit SHA.

### GHCR Packages

The following container packages were successfully published:

```text
quickticket-gateway
quickticket-events
quickticket-payments
```

The Kubernetes manifests were updated to use the GHCR images, for example:

```text
ghcr.io/minimaxc/quickticket-gateway:117336566e7a6d1f9bb7613eee1695e3b345fe9a
ghcr.io/minimaxc/quickticket-events:117336566e7a6d1f9bb7613eee1695e3b345fe9a
ghcr.io/minimaxc/quickticket-payments:117336566e7a6d1f9bb7613eee1695e3b345fe9a
```

The manifests use `imagePullPolicy: Always` and the cluster was configured with the `ghcr-secret` image pull secret.

### ArgoCD Application

ArgoCD was installed in the cluster and the `quickticket` Application was created.

Observed application state:

```text
Application: quickticket
Sync Status: Synced
Health Status: Healthy
```

ArgoCD was configured to deploy the manifests from the repository `k8s/` directory.

### GitOps Sync Proof

A visible Git change was made to the gateway manifest by adding the version label:

```yaml
metadata:
  labels:
    version: "v2"
```

After ArgoCD synchronized the repository, the live gateway resource showed:

```text
v2
```

This demonstrated the GitOps loop:

```text
Git change
→ push
→ ArgoCD detects desired-state change
→ Kubernetes resource updated
```

### What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?

A manual `kubectl edit` changes the live cluster without changing the desired state stored in Git. ArgoCD detects this as configuration drift and the Application becomes `OutOfSync`.

If ArgoCD self-healing is enabled, it automatically restores the resource to the state stored in Git. If self-healing is not enabled, the drift remains until a manual sync or another synchronization event reapplies the Git state.

For this reason, changes to GitOps-managed resources should normally be made in Git rather than directly in the cluster.

---

## Task 2 — Rollback via GitOps

### Bad Deployment

To simulate a failed release, the gateway manifest was changed to reference a non-existent container image tag.

ArgoCD synchronized the bad desired state into the cluster.

Observed result:

```text
Gateway pod:
ErrImagePull / ImagePullBackOff
```

The ArgoCD Application no longer had a healthy deployment because the new gateway image could not be pulled.

The bad deployment commit was:

```text
6ac3146
```

### Git Revert

The bad deployment was rolled back using Git rather than modifying Kubernetes directly:

```text
git revert
git push
```

The revert commit was:

```text
a5921b9
```

ArgoCD detected the reverted desired state and restored the previous working gateway image.

Observed recovered state:

```text
Application: quickticket
Sync Status: Synced
Health Status: Healthy
```

The gateway pod returned to `Running`.

### Recovery Time

Measured recovery time from the Git revert/push until ArgoCD restored the healthy deployment:

```text
25 seconds
```

### Rollback Analysis

The rollback succeeded because Git remained the source of truth. Instead of manually changing the live resource, the faulty desired-state commit was reverted.

ArgoCD then reconciled the cluster with the corrected Git state. This preserves a clear audit trail and avoids configuration drift between Git and Kubernetes.

---

## Summary

Task 1 demonstrated:

- GitHub Actions CI for all three QuickTicket services.
- Immutable SHA-tagged images pushed to GHCR.
- Kubernetes manifests using registry images.
- ArgoCD installed and managing QuickTicket.
- A Git change automatically synchronized into the cluster.

Task 2 demonstrated:

- A deliberately broken gateway deployment.
- `ErrImagePull` / `ImagePullBackOff` detection.
- Rollback through `git revert`.
- ArgoCD restoring `Synced` and `Healthy`.
- A measured recovery time of approximately 25 seconds.

The experiment demonstrated the central GitOps principle that Git stores the desired state and ArgoCD continuously reconciles the cluster toward that state.
