# Lab 5 Submission

## Task 1. CI pipeline + ArgoCD setup

### What I implemented:

I added [`.github/workflows/ci.yml`](/Users/pavel/Documents/study/sre/lab1/SRE-Intro/.github/workflows/ci.yml) that runs on push to `main`, logs into `ghcr.io`, and builds/pushes all 3 images:

- `ghcr.io/shnupel/quickticket-gateway:${{ github.sha }}`
- `ghcr.io/shnupel/quickticket-events:${{ github.sha }}`
- `ghcr.io/shnupel/quickticket-payments:${{ github.sha }}`

Then I updated the Kubernetes Deployments in [`k8s/gateway.yaml`](/Users/pavel/Documents/study/sre/lab1/SRE-Intro/k8s/gateway.yaml), [`k8s/events.yaml`](/Users/pavel/Documents/study/sre/lab1/SRE-Intro/k8s/events.yaml), and [`k8s/payments.yaml`](/Users/pavel/Documents/study/sre/lab1/SRE-Intro/k8s/payments.yaml) to use the pushed GHCR images with the immutable tag from the first successful CI run:

```text
c0b57674d19ee81195b6993de20947403fd012ee
```

I also:

- changed `imagePullPolicy` to `Always`
- added `imagePullSecrets: [ghcr-secret]`
- added `version: "v2"` to `gateway` metadata to prove GitOps sync
- installed ArgoCD in the `argocd` namespace
- created the `quickticket` ArgoCD Application pointing to `https://github.com/Shnupel/SRE-Intro.git`, path `k8s`, branch `main`

### 1. GitHub Actions run

Run link:

```text
https://github.com/Shnupel/SRE-Intro/actions/runs/28262107287
```

Relevant status from GitHub API:

```text
name: CI
head_sha: c0b57674d19ee81195b6993de20947403fd012ee
status: completed
conclusion: success
```

Matrix jobs summary:

```text
build (payments)  success
build (events)    success
build (gateway)   success
```

### 2. Pushed images

The lab asks for `gh api user/packages?package_type=container`, but `gh` CLI was not installed in this environment. I verified the pushed packages through the public GitHub Packages page for the same repository.

Equivalent package list:

```text
quickticket-payments
quickticket-events
quickticket-gateway
```

Captured output:

```text
<a ... title="quickticket-payments" ...>quickticket-payments</a>
<a ... title="quickticket-events" ...>quickticket-events</a>
<a ... title="quickticket-gateway" ...>quickticket-gateway</a>
```

### 3. ArgoCD application status

Command:

```bash
/tmp/argocd app get quickticket
```

Output:

```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/Shnupel/SRE-Intro.git
  Target:           main
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to main (918ca8b)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway unchanged
```

### 4. Proof that a Git change was synced into the cluster

Command:

```bash
kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
echo
```

Output:

```text
v2
```

This proves that the label added in Git was reconciled by ArgoCD into the live cluster.

### 5. What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?

ArgoCD treats Git as the source of truth. A manual `kubectl edit` changes only the live cluster state, so ArgoCD detects drift and marks the application `OutOfSync`.

Because this application uses automated sync, ArgoCD will reconcile the resource back to the version stored in Git. In practice, manual changes are temporary unless the same change is committed to the repository.
