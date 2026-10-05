# Lab 5 - CI/CD & GitOps

## Task 1 - CI Pipeline and ArgoCD Setup

### GitHub Actions CI

I created `.github/workflows/ci.yml` to build all three QuickTicket services and push the images to GitHub Container Registry.

The services are:

```text
gateway
events
payments
```

Each image is tagged with the Git commit SHA:

```text
ghcr.io/m1d0rfeed/quickticket-gateway:<commit-sha>
ghcr.io/m1d0rfeed/quickticket-events:<commit-sha>
ghcr.io/m1d0rfeed/quickticket-payments:<commit-sha>
```

The initial successful GitHub Actions run was:

```text
https://github.com/m1d0rfeed/SRE-Intro/actions/runs/36135562236
```

A later successful run used for the bonus automated update was:

```text
https://github.com/m1d0rfeed/SRE-Intro/actions/runs/36388364820
```

The three container package names used by the workflow are:

```text
quickticket-gateway
quickticket-events
quickticket-payments
```

I also verified that the cluster could pull from GHCR. A test pod using the `ghcr-secret` image pull secret became Ready:

```text
GHCR IMAGE PULL SUCCESS
```

### Kubernetes manifests

I changed the application manifests from local Lab 4 images to GHCR images.

The application Deployments use:

```yaml
imagePullPolicy: Always
imagePullSecrets:
  - name: ghcr-secret
```

The first working GitOps deployment used:

```text
ghcr.io/m1d0rfeed/quickticket-events:aa58c9a90c7ca3ac9153e0da0d7f39735914811f
ghcr.io/m1d0rfeed/quickticket-gateway:aa58c9a90c7ca3ac9153e0da0d7f39735914811f
ghcr.io/m1d0rfeed/quickticket-payments:aa58c9a90c7ca3ac9153e0da0d7f39735914811f
```

### ArgoCD

I installed ArgoCD in the `argocd` namespace.

The normal installation initially failed because the `applicationsets.argoproj.io` CRD exceeded the annotation size limit for client-side apply. I completed the installation with server-side apply.

After installation, the ArgoCD components were running and the `quickticket` Application became:

```text
NAME          SYNC STATUS   HEALTH STATUS   PROJECT
quickticket   Synced        Healthy         default
```

I configured the Application to read `k8s/` from my repository and deploy to the `default` namespace.

### GitOps synchronization test

I added a visible Git change to the gateway Deployment:

```yaml
metadata:
  labels:
    version: "v2"
```

After I pushed the commit, ArgoCD detected it and applied it automatically.

The cluster showed:

```text
version=v2
```

The Application status was:

```text
quickticket   Synced   Healthy
```

This confirmed that a Git change was synchronized to the cluster through ArgoCD.

### What happens if someone manually runs kubectl edit?

If someone manually changes a resource managed by this ArgoCD Application, the live state becomes different from the desired state stored in Git.

Because automated synchronization and self-healing are enabled, ArgoCD detects the drift and restores the Git version. The manual change is overwritten unless the field is explicitly configured to be ignored.

---

## Task 2 - Rollback via GitOps

### Broken deployment

I intentionally changed the gateway image to a non-existent tag:

```text
ghcr.io/m1d0rfeed/quickticket-gateway:does-not-exist
```

ArgoCD detected the Git commit and the new gateway replica failed:

```text
gateway-866f489945-lc4bp   0/1   ErrImagePull
gateway-bffc7974-pg8qg     1/1   Running
```

ArgoCD showed the new revision with health `Progressing`.

The bad deploy commit was:

```text
912dc4d feat(lab5): deploy intentionally broken gateway
```

### Rollback through Git

I reverted the bad Git commit instead of manually changing Kubernetes:

```text
a2fdc17 Revert "feat(lab5): deploy intentionally broken gateway"
```

Relevant Git history:

```text
a2fdc17 Revert "feat(lab5): deploy intentionally broken gateway"
912dc4d feat(lab5): deploy intentionally broken gateway
1fbc62b feat(lab5): add v2 GitOps deployment label
```

ArgoCD synchronized the revert and restored the working gateway image:

```text
ghcr.io/m1d0rfeed/quickticket-gateway:aa58c9a90c7ca3ac9153e0da0d7f39735914811f
```

The Application returned to:

```text
quickticket   Synced   Healthy
```

I then verified the gateway remained ready for three consecutive checks:

```text
ready=true sync=Synced health=Healthy
ready=true sync=Synced health=Healthy
ready=true sync=Synced health=Healthy
STABLE RECOVERY CONFIRMED
```

The health endpoint also returned:

```json
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

### Recovery time

The rollback verification script reached the healthy condition 17 seconds after that monitoring run started.

The verification was started after the revert had already been pushed, so this is the recorded verification time, not an exact measurement from the original `git push` timestamp. The later stability check confirmed that the gateway remained Ready and ArgoCD remained Synced and Healthy.

---

## Bonus Task - Automated Image Tag Update

I extended the workflow so CI automatically updates the image tags after a successful build.

The new flow is:

```text
push
-> build 3 images
-> push images to GHCR
-> update k8s image tags
-> commit manifest changes
-> push automated commit
-> ArgoCD sync
-> deploy new images
```

The workflow skips CI-generated commits to avoid an infinite loop:

```yaml
if: "!startsWith(github.event.head_commit.message, 'ci:')"
```

I pushed:

```text
cf2bb08 ci(lab5): automate image tag updates
```

GitHub Actions built the images and automatically created:

```text
b61540c ci: update image tags to cf2bb08b687eac1e98b63427ed589226d1fcac33
```

ArgoCD then synchronized the automated commit:

```text
revision=b61540cc6246c7be337d776d517042c55aa4d24a
sync=Synced
health=Healthy
```

The cluster was running the automatically updated images:

```text
events     ghcr.io/m1d0rfeed/quickticket-events:cf2bb08b687eac1e98b63427ed589226d1fcac33
gateway    ghcr.io/m1d0rfeed/quickticket-gateway:cf2bb08b687eac1e98b63427ed589226d1fcac33
payments   ghcr.io/m1d0rfeed/quickticket-payments:cf2bb08b687eac1e98b63427ed589226d1fcac33
```

All five application pods were Ready:

```text
events      1/1 Running
gateway     1/1 Running
payments    1/1 Running
postgres    1/1 Running
redis       1/1 Running
```

The final gateway health check returned:

```json
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}
```

This confirmed the full automated CI/CD and GitOps loop.

---

## Summary

I created a GitHub Actions pipeline that builds all three QuickTicket services and publishes SHA-tagged images to GHCR.

I installed ArgoCD and configured automated GitOps deployment from the repository. A Git label change was synchronized automatically.

I tested a broken image deployment and restored the system through `git revert`, without manually editing Kubernetes resources.

For the bonus task, CI automatically updated the Kubernetes image tags, committed the changes back to Git, and ArgoCD synchronized and deployed the new images successfully.
