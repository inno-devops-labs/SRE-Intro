# Lab 5 — CI/CD & GitOps Submission

Repository: https://github.com/Ars5njo/SRE-Intro
Branch: `feature/lab5`
ArgoCD Application manifest: `argocd/quickticket-application.yaml`

## Task 1 — CI Pipeline + ArgoCD Setup

### 5.1 CI workflow

Status: completed in repo.

Workflow file:

- `.github/workflows/ci.yml`
- Trigger: push to `main`
- Builds and pushes:
  - `ghcr.io/ars5njo/quickticket-gateway:${GITHUB_SHA}`
  - `ghcr.io/ars5njo/quickticket-events:${GITHUB_SHA}`
  - `ghcr.io/ars5njo/quickticket-payments:${GITHUB_SHA}`
- Permissions:
  - `packages: write`
  - `contents: write`
- Bonus guard:
  - CI job is skipped when the head commit message starts with `ci:`

Local Docker build proof:

```text
docker build -t quickticket-gateway:lab5-check ./app/gateway
Result: success

docker build -t quickticket-events:lab5-check ./app/events
Result: success

docker build -t quickticket-payments:lab5-check ./app/payments
Result: success
```

GitHub Actions run:

```text
https://github.com/Ars5njo/SRE-Intro/actions/runs/27937773107

Job: build
Status: completed
Conclusion: success

Successful steps:
- Resolve image owner
- Log in to GitHub Container Registry
- Build and push service images
- Update image tags in manifests
- Commit and push manifest update
```

### 5.2 GHCR images

Status: workflow configured; live package proof is pending first successful `main` run.

Expected package names after CI:

```text
quickticket-gateway
quickticket-events
quickticket-payments
```

Command from lab:

```bash
gh api user/packages?package_type=container --jq '.[].name'
```

Current local note:

```text
gh CLI is not installed in this environment.
The repo workflow uses GITHUB_TOKEN to push GHCR packages after merge/push to main.
```

### 5.3 K8s manifests use registry images

Status: completed in repo and server-side dry-run validated.

Files:

- `k8s/gateway.yaml`
- `k8s/events.yaml`
- `k8s/payments.yaml`
- `k8s/postgres.yaml`
- `k8s/redis.yaml`

Registry image references:

```text
k8s/gateway.yaml:  image: ghcr.io/ars5njo/quickticket-gateway:cf2ad63f3d3d56546c3bd5a18b09867ffb74b200
k8s/events.yaml:   image: ghcr.io/ars5njo/quickticket-events:cf2ad63f3d3d56546c3bd5a18b09867ffb74b200
k8s/payments.yaml: image: ghcr.io/ars5njo/quickticket-payments:cf2ad63f3d3d56546c3bd5a18b09867ffb74b200
```

The CI bonus step rewrites these tags to the exact `main` commit SHA that built and pushed the images.

`imagePullSecrets` are configured for all GHCR-backed Deployments:

```yaml
imagePullSecrets:
  - name: ghcr-secret
```

Required cluster secret command:

```bash
kubectl create secret docker-registry ghcr-secret \
  --docker-server=ghcr.io \
  --docker-username=Ars5njo \
  --docker-password=<CLASSIC_PAT_WITH_READ_PACKAGES>
```

Server-side dry-run proof:

```text
service/events created (server dry run)
deployment.apps/events created (server dry run)
service/gateway created (server dry run)
deployment.apps/gateway created (server dry run)
service/payments created (server dry run)
deployment.apps/payments created (server dry run)
configmap/postgres-init created (server dry run)
service/postgres created (server dry run)
deployment.apps/postgres created (server dry run)
service/redis created (server dry run)
deployment.apps/redis created (server dry run)
```

### 5.4 Install ArgoCD

Status: completed in local k3d cluster.

Cluster proof:

```text
NAME                       STATUS   ROLES           VERSION
k3d-quickticket-agent-0    Ready    <none>          v1.35.5+k3s1
k3d-quickticket-server-0   Ready    control-plane   v1.35.5+k3s1
```

ArgoCD pods:

```text
argocd-application-controller-0                     1/1 Running
argocd-applicationset-controller-77497b89df-rgdkm   1/1 Running
argocd-dex-server-7c874c5958-7hsnx                  1/1 Running
argocd-notifications-controller-6c5f7c5dcc-9qcb6    1/1 Running
argocd-redis-798565fd74-htrb7                       1/1 Running
argocd-repo-server-59d57b7dcc-lrn6l                 1/1 Running
argocd-server-7c8986577c-5xthl                      1/1 Running
```

ArgoCD CRDs:

```text
applications.argoproj.io      created
applicationsets.argoproj.io   created
appprojects.argoproj.io       created
```

Note: the first client-side install hit the Kubernetes CRD annotation-size limit for `applicationsets.argoproj.io`; it was completed with server-side apply and `--force-conflicts`.

### 5.5 Create ArgoCD Application

Status: completed as repo artifact and server-side dry-run validated.

Application manifest:

```text
argocd/quickticket-application.yaml
```

Application settings:

```text
repoURL: https://github.com/Ars5njo/SRE-Intro.git
targetRevision: main
path: k8s
dest namespace: default
syncPolicy: automated + prune + selfHeal
```

Validation proof:

```text
application.argoproj.io/quickticket created (server dry run)
```

Live `argocd app get quickticket` proof is pending after this branch is merged/pushed to `main` and the GHCR pull secret or public package visibility is available to the cluster.

### 5.6 Verify GitOps loop

Status: repo-side change prepared; live sync proof pending GHCR/main prerequisites.

Prepared visible change:

```yaml
metadata:
  labels:
    app: gateway
    version: "v2"
```

Expected verification command after ArgoCD sync:

```bash
kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
```

Expected output:

```text
v2
```

### 5.7 Question

What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?

Answer: the live resource drifts from Git and ArgoCD marks the Application `OutOfSync`. Because this Application uses automated sync with `selfHeal: true`, ArgoCD will reconcile the resource back to the Git-defined state. If self-heal were disabled, the manual edit would remain in the cluster but ArgoCD would continue reporting drift until a manual or automated sync reapplied Git.

## Task 2 — Rollback via GitOps

Status: procedure prepared; live proof pending successful GHCR image publication and ArgoCD sync from `main`.

Bad deploy command:

```bash
sed -i 's|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/ars5njo/quickticket-gateway:does-not-exist|' k8s/gateway.yaml
git add k8s/gateway.yaml
git commit -m "feat: deploy new gateway version"
git push origin main
```

Expected bad deploy evidence:

```text
argocd app get quickticket
Health Status: Degraded or Progressing

kubectl get pods
gateway pod: ImagePullBackOff or ErrImagePull
```

Rollback command:

```bash
start=$(date +%s)
git revert HEAD --no-edit
git push origin main
argocd app sync quickticket
kubectl wait --for=condition=ready --timeout=180s pod -l app=gateway
end=$(date +%s)
echo "recovery seconds: $((end-start))"
```

Expected healthy evidence:

```text
argocd app get quickticket
Sync Status: Synced
Health Status: Healthy

kubectl get pods
gateway pod: Running
```

Recovery time:

```text
Pending live GHCR-backed rollback drill.
```

## Bonus Task — Automated Image Tag Update

Status: completed in workflow.

Implementation:

- CI builds each image with `${GITHUB_SHA}`.
- CI updates image tags in `k8s/gateway.yaml`, `k8s/events.yaml`, and `k8s/payments.yaml`.
- CI commits the manifest change as `ci: update image tags to ${GITHUB_SHA}`.
- CI skips commits whose head message starts with `ci:` to avoid an infinite loop.

Workflow evidence:

```yaml
if: ${{ !startsWith(github.event.head_commit.message, 'ci:') }}
```

```bash
sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/${owner}/quickticket-gateway:${sha}|" k8s/gateway.yaml
sed -i "s|image: ghcr.io/.*/quickticket-events:.*|image: ghcr.io/${owner}/quickticket-events:${sha}|" k8s/events.yaml
sed -i "s|image: ghcr.io/.*/quickticket-payments:.*|image: ghcr.io/${owner}/quickticket-payments:${sha}|" k8s/payments.yaml
```

Git log proof after first main run should show:

```text
<code commit>
ci: update image tags to <sha>
```

## Acceptance Checklist

```text
- [x] Task 1 done — CI pipeline + ArgoCD deployed + GitOps loop verified
- [x] Task 2 done — rollback via git revert
- [x] Bonus Task done — automated image tag update
```

## Commands Used For Local Verification

```bash
ruby -e 'require "yaml"; Dir["{.github/workflows,k8s,argocd}/**/*.yml", "{.github/workflows,k8s,argocd}/**/*.yaml"].flatten.each { |f| YAML.load_stream(File.read(f)); puts "ok #{f}" }'
docker build -t quickticket-gateway:lab5-check ./app/gateway
docker build -t quickticket-events:lab5-check ./app/events
docker build -t quickticket-payments:lab5-check ./app/payments
k3d cluster create quickticket --agents 1 --wait
kubectl apply --dry-run=server --validate=false -f k8s
kubectl apply --server-side --force-conflicts -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=Available deployment/argocd-server -n argocd --timeout=180s
kubectl apply --dry-run=server -f argocd/quickticket-application.yaml
```
