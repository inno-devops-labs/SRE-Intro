*Emil Nabiullin, e.nabiullin@innopolis.university*
---
# Lab 5 - CI/CD and GitOps

## Repository state

This branch contains the Lab 5 preparation on top of the merged Lab 4 base:

- `.github/workflows/ci.yml` adds a GitHub Actions workflow for image build and push to `ghcr.io`
- raw manifests in `k8s/` were updated from local images to registry images
- the Helm chart in `k8s/chart/` was updated to match the same image strategy
- ArgoCD was installed into the existing `k3d` cluster and connected to this repository

The repository remote for this work is:

```text
origin  git@github-emil:Deldore/SRE-Intro.git (fetch)
origin  git@github-emil:Deldore/SRE-Intro.git (push)
```

## CI workflow

The workflow file prepared for this lab is `.github/workflows/ci.yml`.

It does the following:

1. runs on push to `main`
2. logs in to `ghcr.io`
3. builds all three application images
4. pushes both `${GITHUB_SHA}` and `main` tags

The important image names are:

- `ghcr.io/deldore/quickticket-gateway:${GITHUB_SHA}`
- `ghcr.io/deldore/quickticket-events:${GITHUB_SHA}`
- `ghcr.io/deldore/quickticket-payments:${GITHUB_SHA}`
- `ghcr.io/deldore/quickticket-gateway:main`
- `ghcr.io/deldore/quickticket-events:main`
- `ghcr.io/deldore/quickticket-payments:main`

## Kubernetes manifest update

The application manifests were updated from local images to registry images and now use `imagePullSecrets`.

Example from `k8s/gateway.yaml`:

```yaml
spec:
  imagePullSecrets:
    - name: ghcr-secret
  containers:
    - name: gateway
      image: ghcr.io/deldore/quickticket-gateway:main
      imagePullPolicy: Always
```

The same was done for:

- `k8s/events.yaml`
- `k8s/payments.yaml`
- `k8s/chart/values.yaml`
- `k8s/chart/templates/{gateway,events,payments}.yaml`

I also added a visible marker for the GitOps sync check:

```yaml
metadata:
  labels:
    app: gateway
    version: "v2"
```

## ArgoCD installation

ArgoCD was installed into namespace `argocd`.

```bash
docker exec k3d-quickticket-server-0 kubectl get pods -n argocd
```

```text
NAME                                                READY   STATUS    RESTARTS       AGE
argocd-application-controller-0                     1/1     Running   0              11m
argocd-applicationset-controller-77497b89df-79kdr   1/1     Running   4 (2m7s ago)   11m
argocd-dex-server-7c874c5958-4njwl                  1/1     Running   0              11m
argocd-notifications-controller-6c5f7c5dcc-lpmw6    1/1     Running   0              11m
argocd-redis-798565fd74-44qjq                       1/1     Running   0              11m
argocd-repo-server-59d57b7dcc-2m8l7                 1/1     Running   0              11m
argocd-server-7c8986577c-9g94z                      1/1     Running   0              11m
```

The initial admin secret exists:

```bash
docker exec k3d-quickticket-server-0 kubectl get secret -n argocd argocd-initial-admin-secret -o jsonpath='{.metadata.name}'
```

```text
argocd-initial-admin-secret
```

## ArgoCD application

I created an ArgoCD application named `quickticket` that points to this repository:

```bash
argocd app create quickticket \
  --repo https://github.com/Deldore/SRE-Intro.git \
  --path k8s \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace default \
  --sync-policy automated
```

Current status:

```bash
argocd app get quickticket
```

```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/Deldore/SRE-Intro.git
  Target:           main
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to main (6b6f1db)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
```

## GitOps loop note

The visible change for the GitOps test is already prepared in this branch as `version: "v2"` on the `gateway` deployment.

At the moment of writing this report, the cluster is still synced to `main` revision `6b6f1db`, so the new label is not yet live in the cluster. It will become the proof of sync after this branch is merged and ArgoCD reconciles the new revision.

## GitHub Actions / GHCR note

The workflow is configured correctly, but the evidence items below depend on the first `push` to `main`, because the trigger is:

```yaml
on:
  push:
    branches:
      - main
```

Because of that, at report-writing time there was no valid Lab 5 run on `main` yet, and no honest `gh api user/packages?package_type=container` output could be included.

The missing post-merge checks are:

1. GitHub Actions run URL with green status
2. `gh api user/packages?package_type=container` output
3. live cluster proof that `gateway.metadata.labels.version == v2`

## Answer

If someone runs `kubectl edit` on a resource managed by ArgoCD, the change creates drift from the Git state. ArgoCD detects that drift and reconciles the resource back to what is stored in Git. In practice, the manual edit is temporary unless the same change is committed to the repository.
