# Lab 5 — CI/CD & GitOps

## Task 1 — CI pipeline and ArgoCD

### 5.1–5.2 — Build and publish images

The workflow `.github/workflows/ci.yml` runs on pushes to `main` and `feature/lab5`. The additional feature-branch trigger allowed verification before merging the PR. It builds and publishes gateway, events, and payments to GHCR with immutable commit SHA tags for both `linux/amd64` and `linux/arm64`. Successful multi-platform run: [GitHub Actions #36331544158](https://github.com/Esqavator/SRE-Intro/actions/runs/36331544158).

Package visibility and names:

```text
quickticket-gateway	public
quickticket-events	public
quickticket-payments	public
```

All three packages are public; Kubernetes pulls them without a registry credential. The updated manifests use `imagePullPolicy: Always`.

### 5.3–5.5 — Kubernetes and ArgoCD

ArgoCD was installed in namespace `argocd`. The Application reads the public fork at revision `feature/lab5`, path `k8s`, and deploys into `default`. The previous Helm release was removed before ArgoCD synchronized the five Deployments and five Services. This branch is used because the Lab 4 manifests are not yet present in the fork's `main`.

After the deployment, the database was initialized from `app/seed.sql`. The PostgreSQL backup taken before the switch contained no tables (the Lab 4 database used ephemeral storage). The gateway responded successfully through `kubectl port-forward svc/gateway 3080:8080`:

```json
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

The `/events` response contained five seeded events.

Automatic synchronization, pruning, and self-healing are configured:

```json
{
  "automated": {
    "prune": true,
    "selfHeal": true
  }
}
```

### 5.6 — GitOps change observed in the cluster

Commit `23583e2` added the Deployment label `version: v2`. CI built new images and committed the manifest SHA update as `bc62a6c`, without triggering another build. Successful CI run: [GitHub Actions #36332260202](https://github.com/Esqavator/SRE-Intro/actions/runs/36332260202).

```text
bc62a6c ci: update image tags to 23583e28ce9fe66b969adcbc3c09e0f573278a21
23583e2 feat(lab5): label gateway v2 for GitOps sync
00298d6 ci: update image tags to 948c0e6c9cd45260885b104f1fcc1f12629ff0bc
```

ArgoCD automatically synchronized the CI commit; no manual `argocd app sync` was used for this change:

```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                http://localhost:64535/applications/quickticket
Source:
- Repo:             https://github.com/Esqavator/SRE-Intro.git
  Target:           feature/lab5
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated (Prune)
Sync Status:        Synced to feature/lab5 (bc62a6c)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments configured
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events configured
```

The live gateway Deployment had the new label and image:

```text
version=v2
image=ghcr.io/esqavator/quickticket-gateway:23583e28ce9fe66b969adcbc3c09e0f573278a21
```

**What happens after a manual `kubectl edit`?** It changes the live resource but not Git. ArgoCD notices drift and reports `OutOfSync`. With `selfHeal: true` in this Application, it applies the Git version again; the manual edit does not persist.

## Task 2 — Rollback through Git

### 5.8 — Deploy an unavailable image

Commit `cb9444d` temporarily selected `ghcr.io/esqavator/quickticket-gateway:does-not-exist` and set a 60-second Deployment progress deadline. Its `[skip ci]` annotation prevented the bonus workflow from immediately replacing the deliberately bad image tag. ArgoCD still synchronized the Git commit.

```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                http://localhost:65054/applications/quickticket
Source:
- Repo:             https://github.com/Esqavator/SRE-Intro.git
  Target:           feature/lab5
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated (Prune)
Sync Status:        Synced to feature/lab5 (cb9444d)
Health Status:      Degraded

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH    HOOK  MESSAGE
       Service     default    redis     Synced  Healthy         service/redis unchanged
       Service     default    events    Synced  Healthy         service/events unchanged
       Service     default    postgres  Synced  Healthy         service/postgres unchanged
       Service     default    payments  Synced  Healthy         service/payments unchanged
       Service     default    gateway   Synced  Healthy         service/gateway unchanged
apps   Deployment  default    payments  Synced  Healthy         deployment.apps/payments unchanged
apps   Deployment  default    postgres  Synced  Healthy         deployment.apps/postgres unchanged
apps   Deployment  default    events    Synced  Healthy         deployment.apps/events unchanged
apps   Deployment  default    redis     Synced  Healthy         deployment.apps/redis unchanged
apps   Deployment  default    gateway   Synced  Degraded        deployment.apps/gateway configured
```

The new pod could not pull the image:

```text
NAME                       READY   STATUS         RESTARTS   AGE     IP           NODE                       NOMINATED NODE   READINESS GATES
gateway-66c67cf766-6w98r   1/1     Running        0          3m27s   10.42.0.45   k3d-quickticket-server-0   <none>           <none>
gateway-6f84fd4944-lvxvl   0/1     ErrImagePull   0          66s     10.42.0.46   k3d-quickticket-server-0   <none>           <none>
```

After the progress deadline, Kubernetes reported `ProgressDeadlineExceeded`:

```json
[
  {
    "lastTransitionTime": "2026-09-27T16:06:26Z",
    "lastUpdateTime": "2026-09-27T16:06:26Z",
    "message": "Deployment has minimum availability.",
    "reason": "MinimumReplicasAvailable",
    "status": "True",
    "type": "Available"
  },
  {
    "lastTransitionTime": "2026-09-27T16:17:44Z",
    "lastUpdateTime": "2026-09-27T16:17:44Z",
    "message": "ReplicaSet \"gateway-6f84fd4944\" has timed out progressing.",
    "reason": "ProgressDeadlineExceeded",
    "status": "False",
    "type": "Progressing"
  }
]
```

The old gateway pod remained ready during this rolling update, so the observed failure was a failed deployment rather than a complete gateway outage.

### 5.9 — Revert and recovery

The bad commit was reverted with `git revert HEAD --no-edit` and pushed. ArgoCD synchronized the revert automatically, returning to `Synced` and `Healthy`:

```text
fabe9e8 Revert "feat(lab5): test unavailable gateway image [skip ci]"
cb9444d feat(lab5): test unavailable gateway image [skip ci]
bc62a6c ci: update image tags to 23583e28ce9fe66b969adcbc3c09e0f573278a21
23583e2 feat(lab5): label gateway v2 for GitOps sync
```

```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                http://localhost:65150/applications/quickticket
Source:
- Repo:             https://github.com/Esqavator/SRE-Intro.git
  Target:           feature/lab5
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated (Prune)
Sync Status:        Synced to feature/lab5 (fabe9e8)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
```

Gateway pod after the revert:

```text
NAME                       READY   STATUS    RESTARTS   AGE     IP           NODE                       NOMINATED NODE   READINESS GATES
gateway-66c67cf766-6w98r   1/1     Running   0          4m38s   10.42.0.45   k3d-quickticket-server-0   <none>           <none>
```

**Recovery time:** 5.87 seconds from the start of `git revert` and push to the observed `Synced / Healthy` state. This measures deployment recovery; the previous gateway pod continued serving throughout.

```text
BAD_COMMIT=cb9444dfba8628489bd8819c1c4a2a559c03e074
REVERT_COMMIT=fabe9e8f0abe0687ab1375a23c8181c3074b2cef
REVERT_START_TIME=2026-09-27T16:18:53Z
RECOVERY_TIME=2026-09-27T16:18:59Z
RECOVERY_SECONDS=5.87
```

## Bonus — Automated image tag update

The same CI workflow builds the three architecture-compatible images, updates `k8s/gateway.yaml`, `k8s/events.yaml`, and `k8s/payments.yaml` to `${GITHUB_SHA}`, then commits and pushes those manifests. Commits beginning with `ci:` skip the build job, preventing a build loop. The code commit → CI manifest commit → ArgoCD sync sequence is shown above in the Task 1 evidence.

Workflow as submitted:

```yaml
name: QuickTicket CI

on:
  push:
    branches:
      - main
      - feature/lab5
  workflow_dispatch:

jobs:
  build-and-push:
    if: ${{ !startsWith(github.event.head_commit.message, 'ci:') }}
    runs-on: ubuntu-latest
    permissions:
      contents: write
      packages: write

    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Set up QEMU
        uses: docker/setup-qemu-action@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v4

      - name: Build and push all application images
        shell: bash
        run: |
          set -euo pipefail
          owner="${GITHUB_REPOSITORY_OWNER,,}"

          for service in gateway events payments; do
            image="ghcr.io/${owner}/quickticket-${service}:${GITHUB_SHA}"
            docker buildx build \
              --platform linux/amd64,linux/arm64 \
              --push \
              -t "$image" \
              "./app/${service}"
          done

      - name: Update Kubernetes image tags
        shell: bash
        run: |
          set -euo pipefail
          owner="${GITHUB_REPOSITORY_OWNER,,}"

          for service in gateway events payments; do
            manifest="k8s/${service}.yaml"
            image="ghcr.io/${owner}/quickticket-${service}:${GITHUB_SHA}"

            sed -i -E \
              "s|(^[[:space:]]*image: )[[:graph:]]*quickticket-${service}:[[:graph:]]*|\1${image}|" \
              "$manifest"

            grep -Fq "image: ${image}" "$manifest"
          done

      - name: Commit and push manifest updates
        shell: bash
        run: |
          set -euo pipefail
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

          git add -- k8s/gateway.yaml k8s/events.yaml k8s/payments.yaml

          if git diff --cached --quiet; then
            echo "Image tags are already current"
            exit 0
          fi

          git commit -m "ci: update image tags to ${GITHUB_SHA}"
          git push origin "HEAD:${GITHUB_REF_NAME}"
```

## Final state

```text
NAME                           READY   STATUS    RESTARTS   AGE
pod/events-cd8795f5f-n8glk     1/1     Running   0          6m33s
pod/gateway-66c67cf766-6w98r   1/1     Running   0          6m33s
pod/payments-77bc9ddc9-mnpgs   1/1     Running   0          6m33s
pod/postgres-f5754f487-mhjww   1/1     Running   0          14m
pod/redis-68f999b745-h7j5k     1/1     Running   0          14m

NAME                 TYPE        CLUSTER-IP     EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.96.148   <none>        8081/TCP   14m
service/gateway      ClusterIP   10.43.187.45   <none>        8080/TCP   14m
service/kubernetes   ClusterIP   10.43.0.1      <none>        443/TCP    13d
service/payments     ClusterIP   10.43.24.179   <none>        8082/TCP   14m
service/postgres     ClusterIP   10.43.166.21   <none>        5432/TCP   14m
service/redis        ClusterIP   10.43.240.26   <none>        6379/TCP   14m
```
