# Lab 5 — CI/CD and GitOps

**Student:** Damir Bayazitov  
**Repository:** https://github.com/DamirBayazitov/SRE-Intro  
**Branch:** `feature/lab5`

## Task 1 — Build, publish, and deploy container images

### CI workflow

A GitHub Actions workflow was added at `.github/workflows/ci.yml`.

The workflow:

1. Checks out the repository.
2. Authenticates to GitHub Container Registry.
3. Builds the `gateway`, `events`, and `payments` images.
4. Tags every image with the immutable Git commit SHA.
5. Pushes the images to GHCR.
6. Updates the image tags in the Kubernetes manifests.
7. Commits and pushes the manifest changes back to the branch.

The workflow has the following permissions:

```yaml
permissions:
  contents: write
  packages: write
```

Published image naming scheme:

```text
ghcr.io/damirbayazitov/quickticket-gateway:<git-sha>
ghcr.io/damirbayazitov/quickticket-events:<git-sha>
ghcr.io/damirbayazitov/quickticket-payments:<git-sha>
```

### Successful CI runs

Initial image build and push:

- Run: https://github.com/DamirBayazitov/SRE-Intro/actions/runs/36271251544
- Commit: `cac0f8de827a481572525b403c3a0005f2e3f321`
- Result: `success`

Automatic image-tag update workflow:

- Run: https://github.com/DamirBayazitov/SRE-Intro/actions/runs/36274248802
- Commit built by CI: `c1f384868d900fdcc7e39f83bb86884ed1b991f7`
- Result: `success`

Evidence:

```json
{
  "conclusion": "success",
  "headSha": "c1f384868d900fdcc7e39f83bb86884ed1b991f7",
  "status": "completed",
  "url": "https://github.com/DamirBayazitov/SRE-Intro/actions/runs/36274248802"
}
```

### Container packages

All three GHCR packages were made public:

```text
quickticket-gateway: public
quickticket-events: public
quickticket-payments: public
```

The images were also successfully pulled locally from GHCR.

### Kubernetes manifests

The application manifests use immutable Git SHA tags and force Kubernetes to check the registry:

```yaml
image: ghcr.io/damirbayazitov/quickticket-gateway:c1f384868d900fdcc7e39f83bb86884ed1b991f7
imagePullPolicy: Always
```

The same configuration is used for `events` and `payments`.

The image values were updated in:

```text
k8s/gateway.yaml
k8s/events.yaml
k8s/payments.yaml
k8s/chart/values.yaml
```

## Argo CD GitOps deployment

Argo CD was installed in the `argocd` namespace.

The `quickticket` Application monitors:

```text
Repository: https://github.com/DamirBayazitov/SRE-Intro.git
Revision: feature/lab5
Path: k8s
Destination namespace: default
```

Automated synchronization was enabled with pruning and self-healing:

```yaml
syncPolicy:
  automated:
    prune: true
    selfHeal: true
```

Final Argo CD state:

```text
NAME          SYNC STATUS   HEALTH STATUS   REVISION                                   PROJECT
quickticket   Synced        Healthy         9647ca50769578600a26951f5e8c11eb27b4b705   default
```

The automatic manifest-update commit was:

```text
9647ca5 ci: update image tags to c1f384868d900fdcc7e39f83bb86884ed1b991f7
```

### Deployed images

```text
DEPLOYMENT   IMAGE                                                                                  READY
gateway      ghcr.io/damirbayazitov/quickticket-gateway:c1f384868d900fdcc7e39f83bb86884ed1b991f7    1
events       ghcr.io/damirbayazitov/quickticket-events:c1f384868d900fdcc7e39f83bb86884ed1b991f7     1
payments     ghcr.io/damirbayazitov/quickticket-payments:c1f384868d900fdcc7e39f83bb86884ed1b991f7   1
```

All application pods became Ready:

```text
events-d6dbc9ff4-rb5qm      1/1   Running   0
gateway-5f65fb5cd7-ng8gc   1/1   Running   0
payments-768fcfdc6b-nrj4n  1/1   Running   0
postgres-78489d7f5f-22s6k  1/1   Running
redis-6fcfb5475d-gnmfg     1/1   Running
```

### Application verification

The Gateway health endpoint returned:

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

`GET /events` returned all five seeded events successfully.

### What happens after a manual `kubectl edit`?

A manual change modifies only the live Kubernetes resource and creates drift between the cluster and Git.

Because automated synchronization and `selfHeal` are enabled, Argo CD detects the drift and restores the resource to the state declared in Git. Therefore, Git remains the source of truth and a manual cluster edit is temporary.

## Task 2 — Failed deployment and GitOps rollback

### Failure injection

The Gateway image was deliberately changed to a nonexistent tag:

```text
ghcr.io/damirbayazitov/quickticket-gateway:lab5-broken
```

Failure commit:

```text
1fda65892d4abc2e8d6b6dcc86ba76649cc9507b
test(lab5): deploy invalid gateway image
```

Argo CD detected and synchronized the Git change. The new Gateway pod could not start:

```text
gateway-5f655b4b7b-qs29j   0/1   ErrImagePull
```

The pod events showed:

```text
Pulling image "ghcr.io/damirbayazitov/quickticket-gateway:lab5-broken"
Failed to pull image
Error: ErrImagePull
Error: ImagePullBackOff
```

Argo CD reported:

```text
SYNC STATUS: Synced
HEALTH STATUS: Progressing
REVISION: 1fda65892d4abc2e8d6b6dcc86ba76649cc9507b
```

The previous healthy Gateway pod remained Running during the failed rolling update. Therefore, the service could still answer health requests while the new ReplicaSet was failing.

### Git rollback

The bad commit was reverted through Git:

```text
ad20f30271226e37fdffe77a09cfbb4be5f9a775
Revert "test(lab5): deploy invalid gateway image"
```

Argo CD subsequently restored the valid image:

```text
ghcr.io/damirbayazitov/quickticket-gateway:cac0f8de827a481572525b403c3a0005f2e3f321
```

Final rollback state:

```text
SYNC STATUS: Synced
HEALTH STATUS: Healthy
```

Measured end-to-end time from the revert operation until the observed healthy GitOps state:

```text
330057 ms
approximately 5 minutes 30 seconds
```

This measurement includes Git push propagation, Argo CD repository polling, a temporary DNS delay, synchronization, and rollout observation.

## Bonus — Automatic image-tag updates

The CI workflow was extended to update the Kubernetes manifests automatically after publishing new images.

The workflow:

1. Builds images using `${GITHUB_SHA}`.
2. Pushes all three images to GHCR.
3. Replaces the previous tags in the static manifests and Helm values.
4. Creates a commit beginning with `ci:`.
5. Pushes the commit to the current branch.

To avoid an infinite CI loop, the build job skips ordinary push events whose commit message begins with `ci:`:

```yaml
if: "${{ github.event_name == 'workflow_dispatch' || !startsWith(github.event.head_commit.message, 'ci:') }}"
```

In addition, commits pushed using the repository `GITHUB_TOKEN` do not normally start a second push workflow.

Successful automatic update:

```text
9647ca5 ci: update image tags to c1f384868d900fdcc7e39f83bb86884ed1b991f7
```

Argo CD detected that commit and deployed the new images automatically.

## Issues encountered

### Workflow YAML parsing

The first bonus workflow attempt failed before creating a job because the job-level condition contained an unquoted colon in `ci: update image tags`.

The condition was quoted in commit:

```text
c1f3848 fix(lab5): quote workflow condition
```

The next workflow run completed successfully.

### Temporary cluster DNS timeout

During one Argo CD refresh, `argocd-repo-server` temporarily failed to resolve GitHub:

```text
lookup github.com on 10.43.0.10:53: i/o timeout
```

The following retry succeeded, GitHub DNS resolution recovered, and Argo CD synchronized commit `9647ca5` without manual modification of the application resources.

### PostgreSQL initialization

The PostgreSQL Deployment does not currently use persistent storage. After the pod was recreated, the schema had to be restored using:

```bash
kubectl exec -i deployment/postgres -- \
  psql -U quickticket -d quickticket \
  < app/seed.sql
```

After initialization, both `/health` and `/events` returned successful responses.

## Result

The complete delivery flow was demonstrated:

```text
Git push
→ GitHub Actions build
→ GHCR image publication
→ automatic manifest update
→ Git commit
→ Argo CD synchronization
→ Kubernetes rolling deployment
```

A failed deployment and Git-based rollback were also tested successfully.
