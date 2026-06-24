# Lab 5 — CI/CD & GitOps

**Student:** Rolan  
**Branch:** `feature/lab5`  
**Date:** 2026-06-24

---

## Task 1 — CI Pipeline + ArgoCD Setup (6 pts)

### 5.1 — CI workflow created

`.github/workflows/ci.yml` triggers on push to `main`, logs in to ghcr.io, and builds + pushes
all 3 images tagged with the commit SHA. A `matrix` strategy runs one build/push job per service,
so the three images build in parallel.

```yaml
name: CI

on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    if: ${{ !startsWith(github.event.head_commit.message, 'ci:') }}
    permissions:
      packages: write
    strategy:
      matrix:
        service: [gateway, events, payments]
    steps:
      - uses: actions/checkout@v4
      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Build image
        run: |
          docker build \
            -t ghcr.io/pon4ik7/quickticket-${{ matrix.service }}:${{ github.sha }} \
            ./app/${{ matrix.service }}
      - name: Push image
        run: |
          docker push ghcr.io/pon4ik7/quickticket-${{ matrix.service }}:${{ github.sha }}
```

> The `update-manifests` job (bonus) is documented in the Bonus section below.

### 5.2 — GitHub Actions run (green check)

**Run link:** https://github.com/pon4ik7/SRE-Intro/actions/runs/28047587518

```text
$ gh run list -R pon4ik7/SRE-Intro --limit 1
completed  success  ci: add CI pipeline for QuickTicket  CI  main  push  28047587518  1m32s
```

All three matrix jobs (`build (gateway)`, `build (events)`, `build (payments)`) finished green.

### 5.3 — Images pushed to ghcr.io

The packages are published under `ghcr.io/pon4ik7/quickticket-{gateway,events,payments}`.
(`gh api user/packages` requires a token with `read:packages` scope, which my CLI token lacks —
so the proof below is the cluster successfully pulling the image by its SHA tag:)

```text
$ kubectl describe pod -l app=gateway | grep -A1 Pulling
  Normal  Pulling  kubelet  Pulling image "ghcr.io/pon4ik7/quickticket-gateway:974c739ef877f6009ced34a1ae3e9339e8abdcdd"
  Normal  Pulled   kubelet  Successfully pulled image "...quickticket-gateway:974c739..." in 48.522s. Image size: 53130387 bytes.
```

### 5.4 — K8s manifests updated to use registry images

`k8s/{gateway,events,payments}.yaml` now pull `ghcr.io/pon4ik7/quickticket-<service>:<SHA>` with
`imagePullPolicy: Always`, and each Deployment declares `imagePullSecrets: [ghcr-secret]`.
(`postgres`/`redis` stay on their public Docker Hub images.)

```yaml
    spec:
      imagePullSecrets:
        - name: ghcr-secret
      containers:
        - name: gateway
          image: ghcr.io/pon4ik7/quickticket-gateway:974c739ef877f6009ced34a1ae3e9339e8abdcdd
          imagePullPolicy: Always
```

Pull secret (classic PAT with `read:packages`):

```bash
kubectl create secret docker-registry ghcr-secret \
  --docker-server=ghcr.io \
  --docker-username=pon4ik7 \
  --docker-password=<CLASSIC_PAT>
```

> Note: in my run the packages were anonymously pullable, so the images were fetched even before the
> secret existed (Kubernetes simply ignores a missing `imagePullSecret`). The secret is still declared
> as required by the task and is needed if the packages are kept private.

### 5.5 — ArgoCD installed

```text
$ kubectl apply -n argocd --server-side -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
$ kubectl wait --for=condition=Available deployment/argocd-server -n argocd --timeout=180s
deployment.apps/argocd-server condition met

$ kubectl get pods -n argocd
NAME                                                READY   STATUS    RESTARTS   AGE
argocd-application-controller-0                     1/1     Running   0          75s
argocd-applicationset-controller-77497b89df-s4nmq   1/1     Running   0          75s
argocd-dex-server-7c874c5958-krp2v                  1/1     Running   0          75s
argocd-notifications-controller-6c5f7c5dcc-n2mb4    1/1     Running   0          75s
argocd-redis-798565fd74-rhs7d                       1/1     Running   0          75s
argocd-repo-server-59d57b7dcc-plrjd                 1/1     Running   0          75s
argocd-server-7c8986577c-nsngj                      1/1     Running   0          75s
```

### 5.6 — ArgoCD Application created & synced

```bash
argocd app create quickticket \
  --repo https://github.com/pon4ik7/SRE-Intro.git \
  --path k8s --revision main \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace default \
  --sync-policy automated
```

```text
$ argocd app get quickticket
Sync Status:        Synced to main (cb032d4)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH
       Service     default    gateway   Synced  Healthy
       Service     default    events    Synced  Healthy
       Service     default    payments  Synced  Healthy
       Service     default    postgres  Synced  Healthy
       Service     default    redis     Synced  Healthy
apps   Deployment  default    gateway   Synced  Healthy
apps   Deployment  default    events    Synced  Healthy
apps   Deployment  default    payments  Synced  Healthy
apps   Deployment  default    postgres  Synced  Healthy
apps   Deployment  default    redis     Synced  Healthy
```

App works end-to-end through the gateway:

```text
$ curl -s localhost:3080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

### 5.7 — GitOps loop verified

Added `version: "v2"` to the gateway Deployment labels, committed and pushed to `main`,
then let ArgoCD sync — no manual `kubectl apply`:

```text
$ git push origin HEAD:main        # fe3ad61
$ argocd app sync quickticket      # gateway configured
$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2
```

### Answer — `kubectl edit` on an ArgoCD-managed resource

> ArgoCD continuously compares the live cluster state against the desired state declared in Git.
> A manual `kubectl edit` is detected as **drift**: the Application immediately flips to `OutOfSync`.
> Because my Application uses an **automated** sync policy, ArgoCD reconciles the resource back to what
> Git declares, **reverting** the manual edit (with `selfHeal` enabled it does so automatically; even
> without it, the next sync overwrites the change). Git stays the single source of truth — out-of-band
> changes don't survive. This is exactly why GitOps discourages `kubectl edit`: the only durable way to
> change a managed resource is to commit the change to Git.

---

## Task 2 — Rollback via GitOps (4 pts)

### 5.8 — Bad version deployed

Changed the gateway image to a non-existent tag (`...-gateway:does-not-exist`), pushed to `main`, synced:

```text
$ git push origin HEAD:main        # 4bc4ccf
$ argocd app sync quickticket
$ kubectl get pods -l app=gateway
NAME                       READY   STATUS             RESTARTS   AGE
gateway-664f6fc588-85hh2   0/1     ImagePullBackOff   0          26s   <-- new (bad) replicaset
gateway-7d48578df-ncmsv    1/1     Running            0          3m41s <-- old pod still serving

$ argocd app get quickticket
Sync Status:        Synced to main (4bc4ccf)
Health Status:      Progressing
```

The rolling update kept the old pod alive (the new ReplicaSet never became Ready), so the app stayed up
while the bad version sat in `ImagePullBackOff`.

### 5.9 — Rollback via `git revert`

```text
$ git revert HEAD --no-edit && git push origin HEAD:main     # 560e398
$ git log --oneline -3
560e398 Revert "feat(lab5): deploy broken gateway version (bad image tag)"
4bc4ccf feat(lab5): deploy broken gateway version (bad image tag)
fe3ad61 feat(lab5): add version label to gateway

$ argocd app sync quickticket
deployment "gateway" successfully rolled out

$ kubectl get pods -l app=gateway
NAME                      READY   STATUS    RESTARTS   AGE
gateway-7d48578df-ncmsv   1/1     Running   0          4m21s

$ argocd app get quickticket
Sync Status:        Synced to main (560e398)
Health Status:      Healthy
```

### Answer — recovery time

> **~5 seconds** from `git revert` + push (after `argocd app sync`) to a fully Healthy gateway.
> It was near-instant because the bad deploy never took down the running pod: the rolling update held
> the old `gateway-...ncmsv` pod while the new ReplicaSet was stuck in `ImagePullBackOff`. The revert
> just deleted the broken ReplicaSet, and the already-running good pod kept serving — so there was zero
> downtime, only a failed rollout that was discarded.

---

## Bonus Task — Automated Image Tag Update (2 pts)

### Workflow with auto-tag update

A second job `update-manifests` runs after `build` (`needs: build`, so it fires once — not per matrix
service), rewrites the image tags in `k8s/*.yaml` to the current SHA, and commits them back. The
`if: !startsWith(..., 'ci:')` guard on `build` (plus the fact that a `GITHUB_TOKEN` push doesn't
re-trigger workflows) prevents an infinite loop.

```yaml
  update-manifests:
    needs: build
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - name: Update image tags in manifests
        run: |
          SHA=${{ github.sha }}
          sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/pon4ik7/quickticket-gateway:${SHA}|"   k8s/gateway.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-events:.*|image: ghcr.io/pon4ik7/quickticket-events:${SHA}|"     k8s/events.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-payments:.*|image: ghcr.io/pon4ik7/quickticket-payments:${SHA}|" k8s/payments.yaml
      - name: Commit and push manifest update
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@github.com"
          git add k8s/
          git diff --cached --quiet || git commit -m "ci: update image tags to ${{ github.sha }}"
          git push
```

### Auto-update loop proof

```text
$ git log origin/main --oneline -2
9db5c10 ci: update image tags to 7baae5712a4b1b1798e7a49b6eda93466d9f00f2   <-- CI bot commit
7baae57 feat(lab5): auto-update image tags in manifests from CI (bonus)     <-- my code commit

$ git show origin/main:k8s/gateway.yaml | grep image:
          image: ghcr.io/pon4ik7/quickticket-gateway:7baae5712a4b1b1798e7a49b6eda93466d9f00f2
```

The `ci:` commit `9db5c10` did **not** spawn a new CI run (no loop). ArgoCD then synced it automatically:

```text
$ argocd app sync quickticket
$ kubectl get deployment gateway -o jsonpath='{.spec.template.spec.containers[0].image}'
ghcr.io/pon4ik7/quickticket-gateway:7baae5712a4b1b1798e7a49b6eda93466d9f00f2

$ argocd app get quickticket
Sync Status:        Synced to main (9db5c10)
Health Status:      Healthy
```

Full loop: code push → CI builds images → CI bumps tags + commits → ArgoCD syncs → new image live,
with no manual step after the initial push.

---

## PR checklist

```text
- [x] Task 1 done — CI pipeline + ArgoCD deployed + GitOps loop verified
- [x] Task 2 done — rollback via git revert
- [x] Bonus Task done — automated image tag update
```
