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

They use `imagePullPolicy: Always` and the `ghcr-secret` pull secret. The first successful run on `main` will replace `latest` with its exact 40-character commit SHA.

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

### External evidence pending after merge

These acceptance items require the Lab 5 commit to exist on GitHub `main` and are intentionally not fabricated:

- successful GitHub Actions run URL
- three packages returned by `gh api user/packages?package_type=container`
- ArgoCD `Synced` and `Healthy` output for the GitHub repository
- live gateway `version: v2` label synced from Git

Commands to capture them after the PR is merged:

```bash
gh run list --workflow CI --limit 3
gh api 'user/packages?package_type=container' --jq '.[].name'
kubectl apply -f argocd/quickticket.yaml
argocd app get quickticket
kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}{"\n"}'
```

## Task 2 — Rollback via GitOps

The rollback experiment must be performed on `main` after GHCR contains a valid image. The safe experiment is:

1. Commit a non-existent gateway image tag and push it.
2. Capture ArgoCD in `Progressing` or `Degraded` and the gateway pod in `ImagePullBackOff`.
3. Run `git revert HEAD --no-edit` and push the revert.
4. Measure from the revert push until the replacement gateway pod is Ready.

The exact recovery duration and Git log are pending this external run; no value is claimed from a local simulation.

## Bonus — automated image updates

The bonus is implemented in the `update-manifests` CI job. It starts only after all three image builds succeed, writes the originating SHA into the raw manifests, commits only the three intended files, and avoids an infinite workflow loop by skipping `ci:` commits.

Expected Git history after the first successful merge run:

```text
ci: update image tags to <40-character SHA>
feat(lab5): add CI/CD pipeline and ArgoCD GitOps
```

The resulting manifest commit is the Git source ArgoCD will automatically detect and deploy.

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
