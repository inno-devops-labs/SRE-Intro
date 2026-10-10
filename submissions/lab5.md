# Lab 5 — CI/CD and GitOps

## Task 1 — CI pipeline and ArgoCD

### CI workflow

I added `.github/workflows/ci.yml`. On every non-`ci:` push to `main`, it:

1. Builds gateway, events and payments in parallel.
2. Pushes each image to GHCR with the immutable commit SHA and `latest` tags.
3. Updates the three raw Kubernetes manifests to the new SHA.
4. Commits the manifest update as `ci: update image tags to <SHA>`.

The generated `ci:` commit triggers the workflow but both jobs are skipped, preventing an infinite loop. The workflow has only the permissions each job needs: `packages: write` for image publishing and `contents: write` for the manifest commit.

Local workflow validation:

```text
$ actionlint .github/workflows/ci.yml
# no errors
```

### GHCR manifests

The application Deployments now use:

```text
ghcr.io/tdzdslippen/quickticket-gateway:latest
ghcr.io/tdzdslippen/quickticket-events:latest
ghcr.io/tdzdslippen/quickticket-payments:latest
```

They use `imagePullPolicy: Always` and the `ghcr-secret` pull secret. The successful workflow replaced `latest` with the exact 40-character commit SHA.

The initial CI run completed successfully:

```text
https://github.com/tdzdslippen/SRE-Intro/actions/runs/36244249730

build-and-push (gateway)   success
build-and-push (events)    success
build-and-push (payments)  success
update-manifests           success
```

Published packages:

```text
$ gh api 'user/packages?package_type=container'
quickticket-events    public
quickticket-payments  public
quickticket-gateway   public
```

### ArgoCD installation

The original client-side installation hit Kubernetes' 262144-byte annotation limit on the ApplicationSet CRD. Reapplying the same official manifest with server-side apply completed successfully:

```text
customresourcedefinition.apiextensions.k8s.io/applicationsets.argoproj.io serverside-applied
deployment.apps/argocd-server condition met

NAME                                                READY   STATUS
argocd-application-controller-0                     1/1     Running
argocd-applicationset-controller-7f95b9cd7c-7dgh6   1/1     Running
argocd-dex-server-8666767789-l585k                  1/1     Running
argocd-notifications-controller-797f48b4-t2mgz      1/1     Running
argocd-redis-6fd5864464-85wxs                       1/1     Running
argocd-repo-server-c4977564f-5ltxz                  1/1     Running
argocd-server-59bd8b5c4-bhn5f                       1/1     Running
```

`argocd/quickticket.yaml` defines the application with automated sync, pruning and self-healing from the `k8s/` directory on `main`.

### GitOps drift behavior

If somebody manually changes an ArgoCD-managed resource with `kubectl edit`, it becomes `OutOfSync` because live state differs from Git. This application has automated self-healing enabled, so ArgoCD restores the Git version. Without self-healing, it remains drifted until the next manual or automated sync. The correct permanent change must be committed to Git.

### GitOps sync evidence

```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
Source:
- Repo:             https://github.com/tdzdslippen/SRE-Intro.git
  Target:           main
  Path:             k8s
Sync Policy:        Automated (Prune)
Sync Status:        Synced to main (78fbd40)
Health Status:      Healthy
```

All five Deployments and Services were `Synced` and `Healthy`. The visible label was also present in the cluster:

```text
$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2
```

## Task 2 — Rollback via GitOps

I pushed a deliberately invalid gateway tag in commit `1e870be` and synchronized it through ArgoCD. The failed pod appeared after two seconds:

```text
Sync Status:        Synced to main (1e870be)
Health Status:      Progressing

NAME                        READY   STATUS
gateway-84fc4f6695-ccz7p    0/1     ErrImagePull
gateway-8594949bff-hsdmj    1/1     Running
```

Kubernetes kept the previous healthy replica available while the replacement could not pull its image.

I then reverted the bad Git commit and pushed the revert. ArgoCD synchronized revision `a8020f8`, removed the failed rollout and returned to Healthy in **17.01 seconds**, measured from the completed revert push:

```text
Sync Status:        Synced to main (a8020f8)
Health Status:      Healthy

NAME                        READY   STATUS
events-5db4c4cfd6-brd8z     1/1     Running
gateway-8594949bff-hsdmj    1/1     Running
payments-587cd547b-kpdgw    1/1     Running
postgres-745cf6f696-vjfvd   1/1     Running
redis-d8d9865df-s5kbk       1/1     Running
```

Git history for the experiment:

```text
a8020f8 Revert "feat: deploy new gateway version [skip ci]"
1e870be feat: deploy new gateway version [skip ci]
78fbd40 ci: update image tags to 71bc18c558601a75bf88a047c6a85bb61af256f0
```

## Bonus — automated image updates

The bonus is implemented in the `update-manifests` CI job. It starts only after all three image builds succeed, writes the originating SHA into the raw manifests, commits only the three intended files, and avoids an infinite workflow loop by skipping `ci:` commits.

Git history after the first successful run:

```text
78fbd40 ci: update image tags to 71bc18c558601a75bf88a047c6a85bb61af256f0
71bc18c feat(lab5): add CI/CD pipeline and ArgoCD GitOps
```

After rollback, I manually dispatched the same workflow for revision `a8020f8`. Run `36245255630` completed successfully, generated manifest commit `2e54784`, and ArgoCD deployed the final SHA-tagged images:

```text
Sync Status:   Synced to main (2e54784)
Health Status: Healthy
Gateway image: ghcr.io/tdzdslippen/quickticket-gateway:a8020f855dddb9a5260a8a9ba233185ed8713861
```

## Local validation

```text
actionlint .github/workflows/ci.yml
passed

kubectl apply --dry-run=server -f argocd/quickticket.yaml
application.argoproj.io/quickticket created (server dry run)

helm lint k8s/chart
1 chart(s) linted, 0 chart(s) failed

helm template quickticket k8s/chart | kubectl apply --dry-run=server -f -
passed

kubectl apply --dry-run=server -f k8s/
passed

git diff --check
passed
```
