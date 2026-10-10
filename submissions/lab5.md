# Lab 5 - CI/CD & GitOps

## Task 1 - CI Pipeline + ArgoCD Setup

### GitHub Actions run

- Successful CI run: https://github.com/eraegar/SRE-Intro/actions/runs/35771437948
- Rollback commit CI run: https://github.com/eraegar/SRE-Intro/actions/runs/35788443807
- Result: images were built and pushed to GHCR; the rollback workflow completed successfully.

### GHCR packages

The workflow publishes these immutable images:

```text
ghcr.io/eraegar/quickticket-gateway
ghcr.io/eraegar/quickticket-events
ghcr.io/eraegar/quickticket-payments
```

The authenticated `gh api user/packages?package_type=container` command required the
`read:packages` scope, but the CI job itself successfully pushed the three images.

### ArgoCD application state

ArgoCD was configured with the local Git daemon because the k3d VM could not reach
GitHub directly through the corporate IKEv2 network path.

```text
repo=git://lab5-git/SRE-Intro
sync=Synced health=Healthy revision=c740e59f846b1cdec8e1b1e8d67f6e19d57d3f3d
```

### GitOps sync proof

The gateway label was changed from `version: "v1"` to `version: "v2"` in Git.
After ArgoCD refreshed the application, the live deployment reported:

```text
version=v2
sync=Synced health=Healthy
```

The deployed workloads were running:

```text
events    1/1 Running
gateway   1/1 Running
payments  1/1 Running
postgres  1/1 Running
redis     1/1 Running
```

### What happens if someone manually runs `kubectl edit` on an ArgoCD-managed resource?

The manual edit creates drift between the live Kubernetes object and the desired state
stored in Git. ArgoCD detects the resource as `OutOfSync`; with automated sync and
self-heal enabled, it restores the Git version. Without self-heal, the drift remains
visible until the next sync.

## Task 2 - Rollback via GitOps

### Bad deploy evidence

Commit `c881b37` changed the gateway image to the non-existent tag
`does-not-exist`. ArgoCD detected the new revision and reported:

```text
sync=Synced health=Progressing revision=c881b3772aafd69970ba74feefb2a2b906393db4
image=ghcr.io/eraegar/quickticket-gateway:does-not-exist version=v2
gateway  0/1 ContainerCreating
```

The deployment was progressing because Kubernetes was unable to obtain the intentionally
invalid image tag.

### Git revert evidence

```text
c740e59 Revert "ci: deploy broken gateway version"
c881b37 ci: deploy broken gateway version
f3aa36d ci: prove ArgoCD GitOps sync
5c9a45b ci: update image tags to f1f0f7056c7acf47fbcbf4375de85322b25d2637
```

### Recovery evidence

After `git revert HEAD --no-edit`, push, and ArgoCD refresh:

```text
sync=Synced health=Healthy revision=c740e59f846b1cdec8e1b1e8d67f6e19d57d3f3d
events    1/1 Running
gateway   1/1 Running
payments  1/1 Running
postgres  1/1 Running
redis     1/1 Running
```

Recovery from the revert push to a healthy workload took approximately 30 seconds.

## Bonus Task - Automated Image Tag Update

The CI workflow builds all three service images, pushes them to GHCR with the immutable
`${{ github.sha }}` tag, updates image tags in `k8s/*.yaml`, and commits the manifest
change with a `ci:` prefix. The build job skips `ci:` commits to prevent an infinite loop.

### Auto-tag evidence

```text
5c9a45b ci: update image tags to f1f0f7056c7acf47fbcbf4375de85322b25d2637
f1f0f70 fix(lab5): use cached images for local k3d GitOps
```

The workflow successfully published the SHA-tagged service images, and ArgoCD deployed
the resulting manifest revision. The later GitOps and rollback commits were intentionally
prefixed with `ci:` and their Actions runs were skipped, proving loop prevention:

```text
35787933643  ci: prove ArgoCD GitOps sync       skipped
35788040777  ci: deploy broken gateway version  skipped
35788443807  Revert broken gateway version     success
```
