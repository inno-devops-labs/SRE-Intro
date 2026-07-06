# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

### 1. GitHub Actions CI workflow

Created `.github/workflows/ci.yml`.

The workflow is triggered by pushes to `main`, builds all three QuickTicket services, pushes immutable SHA-tagged images to GitHub Container Registry, and then updates Kubernetes manifests with the new image tags.

```yaml
name: CI

on:
  push:
    branches: [main]

jobs:
  build:
    if: "!startsWith(github.event.head_commit.message, 'ci:')"
    runs-on: ubuntu-latest
    permissions:
      contents: write
      packages: write

    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push images
        run: |
          for service in gateway events payments; do
            docker build \
              -t ghcr.io/gam1rka/quickticket-${service}:${{ github.sha }} \
              ./app/${service}
            docker push ghcr.io/gam1rka/quickticket-${service}:${{ github.sha }}
          done

      - name: Update image tags in manifests
        run: |
          SHA=${{ github.sha }}
          sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/gam1rka/quickticket-gateway:${SHA}|" k8s/gateway.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-events:.*|image: ghcr.io/gam1rka/quickticket-events:${SHA}|" k8s/events.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-payments:.*|image: ghcr.io/gam1rka/quickticket-payments:${SHA}|" k8s/payments.yaml

      - name: Commit and push manifest update
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@github.com"
          git add k8s/
          git diff --cached --quiet || git commit -m "ci: update image tags to ${{ github.sha }}"
          git push
```

GitHub Actions run with the bonus workflow:

```text
https://github.com/GaM1rka/SRE-Intro/actions/runs/28288073798
```

```bash
$ gh run view 28288073798 --repo GaM1rka/SRE-Intro \
  --json url,conclusion,headSha,createdAt,event,status,workflowName
{
  "conclusion": "success",
  "createdAt": "2026-06-27T11:38:31Z",
  "event": "push",
  "headSha": "60fe20ec477479c9cacbdacfc5d6c2e3ae1cd937",
  "status": "completed",
  "url": "https://github.com/GaM1rka/SRE-Intro/actions/runs/28288073798",
  "workflowName": "CI"
}
```

---

### 2. GHCR images

The Kubernetes manifests use GHCR images tagged by commit SHA:

```text
ghcr.io/gam1rka/quickticket-gateway:60fe20ec477479c9cacbdacfc5d6c2e3ae1cd937
ghcr.io/gam1rka/quickticket-events:60fe20ec477479c9cacbdacfc5d6c2e3ae1cd937
ghcr.io/gam1rka/quickticket-payments:60fe20ec477479c9cacbdacfc5d6c2e3ae1cd937
```

Package verification command from this machine:

```bash
$ gh api user/packages?package_type=container --jq '.[].name'
gh: You need at least read:packages scope to list packages. (HTTP 403)
{"message":"You need at least read:packages scope to list packages.","documentation_url":"https://docs.github.com/rest/packages/packages#list-packages-for-the-authenticated-users-namespace","status":"403"}
```

The CI run succeeded, so the images were built and pushed, but the local GitHub CLI token does not have the `read:packages` scope required to list packages through this API.

Direct registry manifest check for the gateway image:

```bash
$ docker manifest inspect ghcr.io/gam1rka/quickticket-gateway:60fe20ec477479c9cacbdacfc5d6c2e3ae1cd937 \
  | jq -r '.mediaType // .schemaVersion'
application/vnd.docker.distribution.manifest.v2+json
```

---

### 3. Kubernetes manifests updated for GitOps

All service Deployments now use registry images and `imagePullPolicy: Always`.

Each Deployment also has the GHCR pull secret configured:

```yaml
spec:
  imagePullSecrets:
    - name: ghcr-secret
  containers:
    ...
```

The secret creation command:

```bash
$ kubectl create secret docker-registry ghcr-secret \
  --docker-server=ghcr.io \
  --docker-username=GaM1rka \
  --docker-password=<classic PAT with read:packages>
```

The gateway Deployment also has the visible GitOps test label:

```yaml
metadata:
  name: gateway
  labels:
    version: "v2"
```

---

### 4. ArgoCD installation and Application

ArgoCD was installed into the `argocd` namespace.

```bash
$ kubectl create namespace argocd
Error from server (AlreadyExists): namespaces "argocd" already exists

$ kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
customresourcedefinition.apiextensions.k8s.io/applications.argoproj.io unchanged
...
deployment.apps/argocd-server unchanged
statefulset.apps/argocd-application-controller unchanged
...

$ kubectl wait --for=condition=Available deployment/argocd-server -n argocd --timeout=120s
deployment.apps/argocd-server condition met
```

ArgoCD pods:

```bash
$ kubectl get pods -n argocd
NAME                                                READY   STATUS    RESTARTS        AGE
argocd-application-controller-0                     1/1     Running   0               14m
argocd-applicationset-controller-5b887868c5-gmgrk   1/1     Running   4 (2m28s ago)   14m
argocd-dex-server-64d89d46c8-4kh8b                  1/1     Running   0               14m
argocd-notifications-controller-97b9678-b8pw5       1/1     Running   0               14m
argocd-redis-7c89f9f856-xxpp4                       1/1     Running   0               14m
argocd-repo-server-7b6d58c697-kthxx                 1/1     Running   0               14m
argocd-server-54569dc877-vfpmf                      1/1     Running   0               14m
```

The downloaded `argocd` CLI binary segfaulted, so the Application was created through the ArgoCD Kubernetes CRD instead:

```bash
$ kubectl apply -f -
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: quickticket
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/GaM1rka/SRE-Intro.git
    targetRevision: HEAD
    path: k8s
  destination:
    server: https://kubernetes.default.svc
    namespace: default
  syncPolicy:
    automated:
      prune: true
      selfHeal: true

application.argoproj.io/quickticket created
```

ArgoCD Application status:

```bash
$ kubectl get application quickticket -n argocd \
  -o jsonpath='Sync={.status.sync.status}{"\n"}Health={.status.health.status}{"\n"}Revision={.status.sync.revision}{"\n"}'
Sync=Synced
Health=Progressing
Revision=470f5e3d62f89edf981b5b3d426eda0172d91e6b
```

---

### 5. GitOps loop verification

The Git change used for verification is the `version: "v2"` label on the gateway Deployment.

```bash
$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}{"\n"}'
v2
```

This proves that the desired state from Git was synced into the live cluster.

During the first sync, the gateway pod could not pull the old image tag because that old SHA was not built in GHCR:

```bash
$ kubectl describe pod -l app=gateway
Events:
  Type     Reason   Message
  ----     ------   -------
  Warning  Failed   Failed to pull image "ghcr.io/gam1rka/quickticket-gateway:ae8704a2e7262ea61ec3ee85755d9343de9d4193": ... not found
  Warning  Failed   Error: ErrImagePull
  Normal   BackOff  Back-off pulling image "ghcr.io/gam1rka/quickticket-gateway:ae8704a2e7262ea61ec3ee85755d9343de9d4193"
  Warning  Failed   Error: ImagePullBackOff
```

The manifests were then updated by CI to the successful image SHA:

```text
60fe20ec477479c9cacbdacfc5d6c2e3ae1cd937
```

After the automated tag update, ArgoCD synced the generated manifest commit:

```bash
$ kubectl get application quickticket -n argocd \
  -o jsonpath='Sync={.status.sync.status}{"\n"}Health={.status.health.status}{"\n"}Revision={.status.sync.revision}{"\n"}'
Sync=Synced
Health=Progressing
Revision=470f5e3d62f89edf981b5b3d426eda0172d91e6b
```

---

### 6. Validation

YAML syntax check:

```bash
$ python3 - <<'PY'
import pathlib, yaml
for name in ['.github/workflows/ci.yml', *map(str, pathlib.Path('k8s').glob('*.yaml'))]:
    with open(name) as f:
        list(yaml.safe_load_all(f))
    print(f'ok {name}')
PY
ok .github/workflows/ci.yml
ok k8s/postgres.yaml
ok k8s/events.yaml
ok k8s/gateway.yaml
ok k8s/payments.yaml
ok k8s/redis.yaml
```

Whitespace check:

```bash
$ git diff --check
```

No whitespace errors were reported.

---

## Bonus Task — Automated Image Tag Update

The CI workflow now implements the full automated image tag update loop:

1. Push to `main`
2. CI builds and pushes images tagged with `${{ github.sha }}`
3. CI rewrites image tags in `k8s/gateway.yaml`, `k8s/events.yaml`, and `k8s/payments.yaml`
4. CI commits the manifest update as `ci: update image tags to <sha>`
5. The workflow skips commits whose message starts with `ci:` to avoid an infinite loop
6. ArgoCD detects the manifest commit and syncs the new desired state

Loop protection:

```yaml
if: "!startsWith(github.event.head_commit.message, 'ci:')"
```

Manifest update step:

```yaml
- name: Update image tags in manifests
  run: |
    SHA=${{ github.sha }}
    sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/gam1rka/quickticket-gateway:${SHA}|" k8s/gateway.yaml
    sed -i "s|image: ghcr.io/.*/quickticket-events:.*|image: ghcr.io/gam1rka/quickticket-events:${SHA}|" k8s/events.yaml
    sed -i "s|image: ghcr.io/.*/quickticket-payments:.*|image: ghcr.io/gam1rka/quickticket-payments:${SHA}|" k8s/payments.yaml
```

Commit step:

```yaml
- name: Commit and push manifest update
  run: |
    git config user.name "github-actions"
    git config user.email "github-actions@github.com"
    git add k8s/
    git diff --cached --quiet || git commit -m "ci: update image tags to ${{ github.sha }}"
    git push
```

Git log after the updated workflow ran:

```bash
$ git log --oneline -3
470f5e3 ci: update image tags to 60fe20ec477479c9cacbdacfc5d6c2e3ae1cd937
60fe20e feat(lab5): add bonus image tag automation
84682da Revert "feat(lab5): add CI/CD pipeline and ArgoCD GitOps"
```

ArgoCD synced the auto-updated manifest commit:

```bash
$ kubectl get application quickticket -n argocd \
  -o jsonpath='Sync={.status.sync.status}{"\n"}Health={.status.health.status}{"\n"}Revision={.status.sync.revision}{"\n"}'
Sync=Synced
Health=Progressing
Revision=470f5e3d62f89edf981b5b3d426eda0172d91e6b
```

---

## Question

### What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?

ArgoCD compares the live cluster state with the desired state stored in Git.

If someone manually edits a managed resource with `kubectl edit`, the cluster state drifts from Git and ArgoCD marks the Application as `OutOfSync`.

With automated sync and self-heal enabled, ArgoCD reverts the manual change and restores the Git version. Without self-heal, ArgoCD still detects the drift, but the change is usually corrected on the next manual sync or the next Git-driven sync.

In short: Git is the source of truth, and manual cluster changes should be treated as temporary drift.
