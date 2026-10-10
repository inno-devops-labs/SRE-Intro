# Lab 5 — CI/CD & GitOps

Date: 2026-09-27. Repository: Walkerino/SRE-Intro. Environment: local k3d
(v1.33.6-k3s1, arm64), ArgoCD v3.3.9. All output below was collected from
actual executions. Task 1, Task 2 and the image-update bonus are completed.

## Task 1 — CI and automated deployment

The [workflow](../.github/workflows/ci.yml) builds gateway, events and payments,
publishes full-SHA tags to GHCR, and advances the three manifests only after
all builds succeed. Each image supports linux/amd64 and linux/arm64, so the
same pipeline serves GitHub runners and the local Apple Silicon cluster.
PR builds have read-only repository permissions and do not publish images.

The workflow supports main as required and feature/lab5 for demonstrating the
exercise before merge. [The Application](../gitops/quickticket.yaml) tracks
feature/lab5; change it to main after merge. No direct push or merge to main
was needed. The Kubernetes manifests from Lab 4 are prerequisites.

### Successful GitHub Actions run

[QuickTicket CI #36314033914](https://github.com/Walkerino/SRE-Intro/actions/runs/36314033914)

```text
build (gateway)    completed success
build (events)     completed success
build (payments)  completed success
update-manifests  completed success
```

Machine-readable run result: [ci-run.json](evidence/lab5/ci-run.json).

### Registry images

The packages are publicly readable, so no credentials or imagePullSecrets
are required in this fork. Instead of `gh api user/packages` (the local gh CLI
has no login), the actual OCI indexes were fetched anonymously:

```bash
for service in gateway events payments; do
  docker buildx imagetools inspect \
    ghcr.io/walkerino/quickticket-${service}:17b2fdb163812e011ade153c83cf598de034a616
done
```

```text
Name:      ghcr.io/walkerino/quickticket-gateway:17b2fdb163812e011ade153c83cf598de034a616
MediaType: application/vnd.oci.image.index.v1+json
Digest:    sha256:9a86835ff766a564f5e595fb405d5627289ed465617c947322e95f53ed65afea
Platforms: linux/amd64, linux/arm64
```

```text
Name:      ghcr.io/walkerino/quickticket-events:17b2fdb163812e011ade153c83cf598de034a616
MediaType: application/vnd.oci.image.index.v1+json
Digest:    sha256:d1b5584b66428198f117090b8a99cfb51cf0f09870f7884f6abd8e7f531159df
Platforms: linux/amd64, linux/arm64
```

```text
Name:      ghcr.io/walkerino/quickticket-payments:17b2fdb163812e011ade153c83cf598de034a616
MediaType: application/vnd.oci.image.index.v1+json
Digest:    sha256:c2d3bf53028646ef73b7da2c466e734702ff7a1c5d64e174d67d71d606af2312
Platforms: linux/amd64, linux/arm64
```

Complete results: [gateway](evidence/lab5/image-gateway.txt),
[events](evidence/lab5/image-events.txt), [payments](evidence/lab5/image-payments.txt).
The Kubernetes pods subsequently pulled these same images successfully.

### ArgoCD installed and healthy

```bash
scripts/bootstrap-lab5.sh
argocd app get quickticket
```

```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/Walkerino/SRE-Intro.git
  Target:           feature/lab5
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated (Prune)
Sync Status:        Synced to feature/lab5 (96323bf)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    events    Synced  Healthy        service/events unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events configured
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments configured
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
```

### Git change observed in the cluster

Commit `96323bf` adds `metadata.labels.version: v2` to the gateway and uses
public registry pulls. After push, `argocd app get quickticket --hard-refresh`
requested a fresh Git comparison; automated sync applied it without
`kubectl apply` of application manifests or `argocd app sync`.

```bash
kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}{"\n"}'
```

```text
v2
```

### What happens after kubectl edit?

A manual edit creates drift between the live resource and Git. This Application
has automated sync and `selfHeal: true`, so ArgoCD restores the Git version;
make persistent changes in Git instead. Automated sync alone, without
self-healing, does not guarantee correction of live-only drift until another
sync/revision is processed. [ArgoCD documentation](https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/).

## Task 2 — Bad deployment and Git revert

Committed and pushed the nonexistent gateway tag `does-not-exist` in `205a41c`.
The 60s training progress deadline allows the Deployment to become Degraded
promptly. This is a failed rollout, not a full service outage: RollingUpdate
kept the previous ready pod available.

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
- Repo:             https://github.com/Walkerino/SRE-Intro.git
  Target:           feature/lab5
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated (Prune)
Sync Status:        Synced to feature/lab5 (205a41c)
Health Status:      Degraded

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH    HOOK  MESSAGE
       Service     default    postgres  Synced  Healthy         service/postgres unchanged
       Service     default    events    Synced  Healthy         service/events unchanged
       Service     default    payments  Synced  Healthy         service/payments unchanged
       Service     default    redis     Synced  Healthy         service/redis unchanged
       Service     default    gateway   Synced  Healthy         service/gateway unchanged
apps   Deployment  default    redis     Synced  Healthy         deployment.apps/redis unchanged
apps   Deployment  default    payments  Synced  Healthy         deployment.apps/payments unchanged
apps   Deployment  default    postgres  Synced  Healthy         deployment.apps/postgres unchanged
apps   Deployment  default    events    Synced  Healthy         deployment.apps/events unchanged
apps   Deployment  default    gateway   Synced  Degraded        deployment.apps/gateway configured
```

```bash
kubectl get pods
```

```text
NAME                              READY   STATUS             RESTARTS   AGE
alert-receiver-7b5bc4cf88-lnw2t   1/1     Running            0          3m37s
events-7cbfc9d5fb-d96lq           1/1     Running            0          2m25s
gateway-7b755c5bc-2bcr2           0/1     ImagePullBackOff   0          94s
gateway-7fcbf786f8-5qb8j          1/1     Running            0          2m25s
grafana-56b4dfc74f-5d6mb          1/1     Running            0          3m37s
lab6-loadgen-559594c575-p4qwj     1/1     Running            0          2m25s
payments-7d8847677f-txcqq         1/1     Running            0          2m25s
postgres-56db6d697c-bcpkl         1/1     Running            0          4m15s
prometheus-6df9866b87-zx8f5       1/1     Running            0          3m36s
redis-6d695466ff-b5dd5            1/1     Running            0          4m15s
```

### Rollback

```bash
git revert 205a41c --no-edit
git push origin feature/lab5
argocd app get quickticket --hard-refresh
argocd app wait quickticket --sync --health --timeout 60
git log --oneline -3
```

```text
8648fea Revert "test(lab5): deploy nonexistent gateway tag for rollback exercise"
205a41c test(lab5): deploy nonexistent gateway tag for rollback exercise
96323bf feat(lab5): verify GitOps label sync with public GHCR images
```

```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/Walkerino/SRE-Intro.git
  Target:           feature/lab5
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated (Prune)
Sync Status:        Synced to feature/lab5 (8648fea)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway unchanged
       Service     default    events    Synced  Healthy
       Service     default    gateway   Synced  Healthy
       Service     default    payments  Synced  Healthy
       Service     default    postgres  Synced  Healthy
       Service     default    redis     Synced  Healthy
apps   Deployment  default    events    Synced  Healthy
apps   Deployment  default    payments  Synced  Healthy
apps   Deployment  default    postgres  Synced  Healthy
apps   Deployment  default    redis     Synced  Healthy
```

### How long did recovery take?


```text
Revert started: 2026-09-27T11:00:18.708756+00:00
Synced and Healthy observed: 2026-09-27T11:00:24.690762+00:00
Elapsed: 5.982 seconds
```

The measured interval includes local revert, push, a hard refresh and waiting
for automated reconciliation. It is **5.982 seconds**, not the default
polling latency. Recovery was fast because the old ready pod and its image
were still available; a cold-image rollout or waiting for periodic Git polling
would take longer. Timestamps are UTC.

## Bonus — Automatic image tags

The source commit triggered CI; after all three builds the bot committed the
new tags. ArgoCD observed that commit and replaced the initial image references
without manually editing their SHA tags.

```bash
git log --oneline -3
```

```text
96323bf feat(lab5): verify GitOps label sync with public GHCR images
3800c40 ci: update image tags to 17b2fdb163812e011ade153c83cf598de034a616
17b2fdb feat(lab5): build multi-platform images and automate GitOps image updates
```

Only app/workflow/tag-updater paths trigger builds on push. A manifest-only bot
commit therefore cannot form a build loop; GITHUB_TOKEN pushes additionally
do not trigger new workflows. The updater validates exactly one image per
service before writing anything, checks that newer source has not superseded
the build, and never force-pushes concurrent human changes.

## Reproduction and validation

See [GitOps setup](../gitops/README.md). The final manifests were rendered with
`kubectl kustomize k8s` and accepted by `kubectl apply --dry-run=server`.
The actual CI builds, public registry inspection, healthy deployment, label
sync, failed rollout and Git revert were all verified. Evidence files contain
actual outputs, not expected/example outputs.

- [x] Task 1: CI green, three images published, ArgoCD and GitOps verified.
- [x] Task 2: Degraded/ImagePullBackOff, Git revert, timed recovery.
- [x] Bonus: CI-generated manifest commit deployed without a build loop.
