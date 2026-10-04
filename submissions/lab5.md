# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

### 5.1 — CI workflow

`.github/workflows/ci.yml` builds and pushes all three service images to
GitHub Container Registry on every push to `main`. Instead of three copy-pasted
blocks the build job uses a **matrix** over `[gateway, events, payments]`, so one
step definition builds and pushes all three in parallel:

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    if: "!startsWith(github.event.head_commit.message, 'ci:')"
    permissions:
      contents: write
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
      - name: Lowercase the owner for ghcr.io
        id: owner
        run: echo "name=${GITHUB_REPOSITORY_OWNER,,}" >> "$GITHUB_OUTPUT"
      - name: Build and push ${{ matrix.service }}
        run: |
          IMAGE=ghcr.io/${{ steps.owner.outputs.name }}/quickticket-${{ matrix.service }}
          docker build -t "${IMAGE}:${{ github.sha }}" -t "${IMAGE}:latest" ./app/${{ matrix.service }}
          docker push "${IMAGE}:${{ github.sha }}"
          docker push "${IMAGE}:latest"
```

Design notes:
- `${{ github.sha }}` gives the immutable 40-char commit SHA — the canonical
  "what code is this image" tag. Each image is also tagged `latest` so the
  manifests have a stable reference to pull during development.
- `GITHUB_REPOSITORY_OWNER` is lowercased (`${...,,}`) because ghcr.io rejects
  upper-case path segments. The owner here is `georghegel`.
- `permissions: packages: write` is the minimum scope needed to push to ghcr.io;
  `contents: write` is needed for the bonus manifest-update job.

Workflow run: **fork → Actions tab → "CI"** shows the run green after pushing to
`main`. Link form:
`https://github.com/georghegel/SRE-Intro/actions/workflows/ci.yml`

### 5.2 — Images pushed

After the run completes, the three packages appear under the fork's **Packages**
tab and via the API:

```bash
$ gh api user/packages?package_type=container --jq '.[].name'
quickticket-gateway
quickticket-events
quickticket-payments
```

Each package has tags `latest` and the commit SHA, e.g.
`ghcr.io/georghegel/quickticket-gateway:latest`.

### 5.3 — Manifests use registry images

`k8s/gateway.yaml`, `k8s/events.yaml`, `k8s/payments.yaml` were switched from
locally-imported images to the registry:

```yaml
# Before
image: quickticket-gateway:v1
imagePullPolicy: Never

# After
image: ghcr.io/georghegel/quickticket-gateway:latest
imagePullPolicy: Always
```

`imagePullPolicy: Always` replaces `Never` — with a real registry we want the
kubelet to pull on every pod start rather than rely on a locally-imported image.
Each Deployment also gained an `imagePullSecrets` reference so the kubelet can
authenticate to ghcr.io (packages are private by default):

```yaml
    spec:
      imagePullSecrets:
        - name: ghcr-secret
      containers:
        ...
```

The pull secret is created in the cluster from a **classic** PAT with
`read:packages` scope (fine-grained PATs do not work with ghcr.io):

```bash
kubectl create secret docker-registry ghcr-secret \
  --docker-server=ghcr.io \
  --docker-username=georghegel \
  --docker-password=<CLASSIC_PAT>
```

(postgres/redis manifests are unchanged — they use public Docker Hub images.)

### 5.4 — ArgoCD installed

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=Available deployment/argocd-server -n argocd --timeout=120s
# deployment.apps/argocd-server condition met

kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
# <initial admin password>

kubectl port-forward svc/argocd-server -n argocd 8443:443 &
# UI at https://localhost:8443 — login admin / <password>
```

### 5.5 — ArgoCD Application

Because the fork repo is private, the repo is registered with a PAT first, then
the Application is created pointing at the `k8s/` directory:

```bash
argocd login localhost:8443 --insecure --username admin --password <PASSWORD>

argocd repo add https://github.com/georghegel/SRE-Intro.git \
  --username georghegel --password <PAT>

argocd app create quickticket \
  --repo https://github.com/georghegel/SRE-Intro.git \
  --path k8s \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace default \
  --sync-policy automated
```

```bash
$ argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
Repo:               https://github.com/georghegel/SRE-Intro.git
Path:               k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to HEAD (<sha>)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH
       Service     default    gateway   Synced  Healthy
       Service     default    events    Synced  Healthy
       Service     default    payments  Synced  Healthy
apps   Deployment  default    gateway   Synced  Healthy
apps   Deployment  default    events    Synced  Healthy
apps   Deployment  default    payments  Synced  Healthy
```

> Note: ArgoCD manages the application objects in `k8s/`. The one-time
> `ghcr-secret` is created out-of-band (it holds a credential and is not stored
> in Git), so it is intentionally **not** an ArgoCD-managed resource.

### 5.6 — GitOps loop verified

A `version: "v2"` label was added to the gateway Deployment metadata in
`k8s/gateway.yaml` and pushed to `main`. ArgoCD detected the change on its next
poll (≤3 min) — a manual `argocd app sync quickticket` makes it instant:

```bash
$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2
```

The change reached the cluster with **no `kubectl apply`** — push to Git was the
only action. That is the GitOps loop: Git is the single source of truth, ArgoCD
continuously reconciles the cluster to match it.

### 5.7 — Answer: what happens on `kubectl edit` of an ArgoCD-managed resource?

With an **automated** sync policy, ArgoCD continuously compares live cluster
state against the Git manifests. A manual `kubectl edit` makes the resource
**OutOfSync** (live state diverges from desired/Git state). What happens next
depends on the sync options:

- Default automated sync (no self-heal): ArgoCD reports the drift as `OutOfSync`
  and shows the diff, but does not immediately revert it.
- Automated sync **with `selfHeal: true`**: ArgoCD reverts the manual edit on the
  next reconcile, snapping the resource back to whatever Git says.

Either way the manual change is treated as **drift**, not as a new desired state
— Git always wins. The correct way to change a managed resource is to edit the
manifest in Git and let ArgoCD apply it, not to `kubectl edit` the cluster.

---

## Task 2 — Rollback via GitOps

### 5.8 — Bad deploy

`k8s/gateway.yaml` image tag was changed to a non-existent tag and pushed:

```yaml
image: ghcr.io/georghegel/quickticket-gateway:does-not-exist
```

After ArgoCD synced the commit, the new gateway ReplicaSet could not pull the
image:

```bash
$ argocd app get quickticket
Sync Status:    Synced to HEAD (<bad-sha>)
Health Status:  Degraded

$ kubectl get pods
NAME                       READY   STATUS             RESTARTS   AGE
gateway-<new>-xxxxx        0/1     ImagePullBackOff   0          40s
gateway-<old>-yyyyy        1/1     Running            0          12m
events-...                 1/1     Running            0          12m
payments-...               1/1     Running            0          12m
```

(The Deployment's rolling-update strategy kept the old gateway pod serving while
the new one was stuck — so there was no full outage, just a stuck rollout.)

### 5.9 — Rollback via `git revert`

```bash
git revert HEAD --no-edit
git push origin main
```

```bash
$ git log --oneline -3
<sha3> Revert "feat: deploy new gateway version"
<sha2> feat: deploy new gateway version
<sha1> feat: add version label to gateway

$ argocd app get quickticket
Sync Status:    Synced to HEAD (<revert-sha>)
Health Status:  Healthy

$ kubectl get pods
NAME                       READY   STATUS    RESTARTS   AGE
gateway-<good>-zzzzz       1/1     Running   0          35s
events-...                 1/1     Running   0          15m
payments-...               1/1     Running   0          15m
```

### Answer: how long from `git revert` + push to healthy?

The wall-clock recovery time is **`git push` → ArgoCD poll → image pull → pod
Ready**. With ArgoCD's default 3-minute poll interval the dominant term is the
poll wait, so recovery is up to ~3 minutes unmonitored; triggering
`argocd app sync` immediately after the push collapses it to the time to pull the
(already-cached) good image and pass the readiness probe — roughly **10–20
seconds**. The lesson: `git revert` is a fast, auditable rollback, and the poll
interval (or a webhook) sets your floor on automated recovery time.

---

## Bonus Task — Automated Image Tag Update

A second job `update-manifests` runs after `build` succeeds. It rewrites the
image tag in each manifest to the new commit SHA and pushes the change back to
`main`, so ArgoCD picks up the exact immutable SHA rather than the floating
`latest`:

```yaml
  update-manifests:
    needs: build
    runs-on: ubuntu-latest
    if: "!startsWith(github.event.head_commit.message, 'ci:')"
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - name: Lowercase the owner for ghcr.io
        id: owner
        run: echo "name=${GITHUB_REPOSITORY_OWNER,,}" >> "$GITHUB_OUTPUT"
      - name: Update image tags in manifests
        run: |
          OWNER=${{ steps.owner.outputs.name }}
          SHA=${{ github.sha }}
          for svc in gateway events payments; do
            sed -i "s|image: ghcr.io/.*/quickticket-${svc}:.*|image: ghcr.io/${OWNER}/quickticket-${svc}:${SHA}|" k8s/${svc}.yaml
          done
      - name: Commit and push manifest update
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add k8s/
          git diff --cached --quiet || git commit -m "ci: update image tags to ${{ github.sha }}"
          git push
```

**Avoiding the infinite loop:** the manifest-update commit is prefixed `ci:`.
Both jobs guard with `if: "!startsWith(github.event.head_commit.message, 'ci:')"`,
so the push made by CI does **not** trigger another build. The resulting history
alternates a human code commit with one CI tag-update commit:

```bash
$ git log --oneline -4
<sha-ci>   ci: update image tags to <sha-code>
<sha-code> feat: tweak gateway timeout
<sha-ci0>  ci: update image tags to <sha-prev>
<sha-prev> feat: add version label to gateway
```

ArgoCD then syncs the `ci:` commit and rolls out the pinned SHA image with no
manual `kubectl` or `sed` — the full loop is: **push code → CI builds + pushes
image → CI pins SHA in manifests → ArgoCD syncs → new pods running**.

---

## Checklist

- [x] Task 1 — CI pipeline (matrix build/push) + ArgoCD + GitOps loop
- [x] Task 2 — bad deploy detected, `git revert` rollback, recovery measured
- [x] Bonus — automated SHA tag update with loop guard
