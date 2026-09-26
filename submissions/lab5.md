# Lab 5 — CI/CD & GitOps

Liubov Utenysheva, CBS-03

---

## Task 1 — CI Pipeline + ArgoCD Setup (6 pts)

### 5.1 — CI workflow

Wrote `.github/workflows/ci.yml` from the lab skeleton: trigger on push to `main`, log in to `ghcr.io` with `GITHUB_TOKEN` (`packages: write`), then for each of the 3 services `docker build -t ghcr.io/kvakz/quickticket-<service>:${{ github.sha }} ./app/<service>` + `docker push`. The SHA tag makes every image immutable and traceable back to its commit.

First run (commit `629d0ad`) — green:

```
https://github.com/kvakz/SRE-Intro/actions/runs/36249010614   # completed, success
```

All later runs are on the same branch: <https://github.com/kvakz/SRE-Intro/actions> (7 runs total, all `success` except one intentional mid-bonus failure, see Bonus section).

### 5.2 — Images pushed to ghcr.io

```bash
$ curl -s -H "Authorization: Bearer $PAT" \
    "https://api.github.com/user/packages?package_type=container" | jq -r '.[] | "\(.name)  \(.visibility)  \(.updated_at)"'
quickticket-gateway   public  2026-09-26T14:39:26Z
quickticket-events    public  2026-09-26T14:39:37Z
quickticket-payments  public  2026-09-26T14:39:47Z
```

All 3 container packages exist (also visible in fork → Packages tab), each tied to the `kvakz/SRE-Intro` repo.

### 5.3 — Manifests switched to registry images

Changed `k8s/gateway.yaml`, `k8s/events.yaml`, `k8s/payments.yaml` (commit `0a3de1b`):

```yaml
      imagePullSecrets:
        - name: ghcr-secret
      containers:
        - name: gateway
          image: ghcr.io/kvakz/quickticket-gateway:629d0ade591cd52b08727e968ec1e2381eb87cd3
          imagePullPolicy: Always
```

The tag is the SHA from the CI run in 5.1. Pull secret created in the cluster with a classic PAT (`read:packages`; fine-grained tokens are rejected by ghcr.io):

```bash
$ kubectl create secret docker-registry ghcr-secret \
    --docker-server=ghcr.io --docker-username=kvakz --docker-password=$PAT
secret/ghcr-secret created
```

The Docker daemon was down when I started, so I recreated the k3d cluster (`k3d cluster create quickticket`) and applied the manifests. Pods pulled from ghcr.io and came up (fresh pull of the 50-53 MB images took ~9-40 s per service):

```console
$ kubectl describe pod -l app=payments | grep -E "Image:|Pulled"
  Image:          ghcr.io/kvakz/quickticket-payments:629d0ade591cd52b08727e968ec1e2381eb87cd3
  Normal   Pulled   ...   Successfully pulled image "ghcr.io/kvakz/quickticket-payments:629d0ade..." in 9.413s

$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
events-5b8745c8f9-j5xtg     1/1     Running   0          43s
gateway-6897555cd6-89cnr    1/1     Running   0          43s
payments-74dc5f744c-kqnqp   1/1     Running   0          43s
postgres-85d6f95ff6-w4p48   1/1     Running   0          42s
redis-7b68444dd5-fmqpj      1/1     Running   0          42s
```

### 5.4 — ArgoCD installed

```bash
$ kubectl create namespace argocd
namespace/argocd created

$ kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
# ... 91 resources created ...

$ kubectl wait --for=condition=Available deployment/argocd-server -n argocd --timeout=120s
deployment.apps/argocd-server condition met

$ kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
iNDfI0npJ4iiiBKQ

$ kubectl -n argocd get pods
NAME                                            READY   STATUS    AGE
argocd-application-controller-0                 1/1     Running   54s
argocd-applicationset-controller-7f95b9cd7c-... 1/1     Running   54s
argocd-dex-server-8666767789-lddm6              1/1     Running   54s
argocd-notifications-controller-797f48b4-npj5p  1/1     Running   54s
argocd-redis-6fd5864464-88mmg                   1/1     Running   54s
argocd-repo-server-c4977564f-62brr              1/1     Running   54s
argocd-server-59bd8b5c4-t46cp                   1/1     Running   54s
```

All 7 ArgoCD pods Running (redis needed one extra pull attempt — backoff then success). One non-critical wrinkle: the `applicationsets` CRD was rejected by k3s (`metadata.annotations: Too long` — > 262144 bytes); it's not needed for this lab.

### 5.5 — ArgoCD Application

```bash
$ argocd login localhost:8443 --insecure --username admin --password <PASSWORD>
'admin:login' logged in successfully

$ argocd app create quickticket \
    --repo https://github.com/kvakz/SRE-Intro.git \
    --path k8s \
    --dest-server https://kubernetes.default.svc \
    --dest-namespace default \
    --sync-policy automated
application 'quickticket' created

$ argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/kvakz/SRE-Intro.git
  Target:
  Path:             k8s
Sync Policy:        Automated
Sync Status:        Synced to  (0a3de1b)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    postgres  Synced  Healthy        service/postgres configured
       Service     default    gateway   Synced  Healthy        service/gateway configured
       Service     default    redis     Synced  Healthy        service/redis configured
       Service     default    events    Synced  Healthy        service/events configured
       Service     default    payments  Synced  Healthy        service/payments configured
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments configured
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres configured
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events configured
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis configured
```

**Synced + Healthy**, all 10 resources tracked. The fork is public, so `argocd repo add` with a PAT was not needed.

### 5.6 — GitOps loop verified

Added `version: "v2"` under `metadata.labels` of the gateway Deployment, pushed, and did **not** run a manual sync — waited for the automated poll:

```bash
$ git commit -m "feat: add version label to gateway" && git push origin main   # 14:58:48Z

# polling argocd app get every 20s:
14:58:55Z  Sync Status:  Synced to  (0a3de1b)
15:00:36Z  Sync Status:  Synced to  (0a3de1b)
15:01:36Z  Sync Status:  Synced to  (b77e093)      <-- auto-synced

$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2
```

The change went live **~2 min 48 s** after the push with zero manual intervention — the GitOps loop (push → ArgoCD poll → sync) works end to end.

### 5.7 — Question
https://github.com/kvakz/SRE-Intro/actions/runs/36251422686

**What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?**

The edit applies to the cluster immediately, but Git remains the source of truth: on the next reconciliation (3-minute poll by default) ArgoCD detects the drift, marks the app `OutOfSync`, and — because the sync policy is automated — re-applies the manifest from the repo, effectively rolling the manual edit back. Manual changes only survive if they are committed to Git first; the 5.6 experiment is the same mechanism in the "good" direction (Git → cluster). Self-healing is exactly the property GitOps is meant to enforce.

---

## Task 2 — Rollback via GitOps (4 pts)

### 5.8 — Bad deploy

Changed the gateway image to a non-existent tag, `git commit` + `git push origin main` at 15:04:23Z:

```console
# after ArgoCD auto-sync (15:05:43Z):
$ argocd app get quickticket | grep -E "Sync Status|Health Status|gateway"
Sync Status:        Synced to  (810367d)
Health Status:      Progressing
apps   Deployment  default    gateway   Synced  Progressing        deployment.apps/gateway configured

$ kubectl get pods
NAME                        READY   STATUS             RESTARTS   AGE
events-5b8745c8f9-j5xtg     1/1     Running            0          13m
gateway-6897555cd6-89cnr    1/1     Running            0          13m
gateway-6d6bd64c69-6z6xk    0/1     ImagePullBackOff   0          5m26s
payments-74dc5f744c-kqnqp   1/1     Running            0          13m
postgres-85d6f95ff6-w4p48   1/1     Running            0          13m
redis-7b68444dd5-fmqpj      1/1     Running            0          13m

$ kubectl describe pod -l app=gateway | grep "Failed to pull"
  Warning  Failed  ...  Failed to pull image "ghcr.io/kvakz/quickticket-gateway:does-not-exist":
    ... ghcr.io/kvakz/quickticket-gateway:does-not-exist: not found
```

The new replica set got `ImagePullBackOff`/`ErrImagePull` and the Deployment health flipped to `Progressing` (the old pod kept serving — rollout blocked, no traffic loss, which is the `maxUnavailable: 0` default behavior at work). ArgoCD stayed `Synced` (cluster matches Git) while health degraded — the distinction between *sync* and *health* is the point: GitOps detects the bad deploy via liveness/health, not via a sync diff.

### 5.9 — Rollback via `git revert`

```bash
$ git revert HEAD --no-edit
[main db45b1a] Revert "feat: deploy new gateway version"
$ git push origin main    # 15:11:14Z

$ git log --oneline -3
db45b1a Revert "feat: deploy new gateway version"
810367d feat: deploy new gateway version
b77e093 feat: add version label to gateway
```

Watched the automated sync (no manual `argocd app sync`):

```
15:11:22Z  health=Progressing
15:13:24Z  health=Healthy        <-- recovered
```

```console
$ argocd app get quickticket | grep -E "Sync Status|Health Status"
Sync Status:        Synced to  (db45b1a)
Health Status:      Healthy

$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
events-5b8745c8f9-j5xtg     1/1     Running   0          16m
gateway-6897555cd6-89cnr    1/1     Running   0          16m
payments-74dc5f744c-kqnqp   1/1     Running   0          16m
postgres-85d6f95ff6-w4p48   1/1     Running   0          16m
redis-7b68444dd5-fmqpj      1/1     Running   0          16m
```

The bad pod was terminated and the healthy pre-incident pod (`gateway-6897555cd6-89cnr`, running image `629d0ade...`) was restored as the sole replica.

**How long from `git revert` + push to pods being healthy again?**

**2 min 10 s** (push 15:11:14Z → Healthy 15:13:24Z). The whole window is ArgoCD's 3-minute poll interval — the revert was picked up on the first poll after the push. No image-pull delay was added because the good image was already cached on the node from before the bad deploy; with a cold node the total would be `poll interval + image pull time`. With `argocd app sync` (or a GitHub webhook) the recovery would drop to seconds after the push.

---

## Bonus Task — Automated Image Tag Update (2 pts)

### Full loop

```
code/manifest push → CI builds+pushes images (SHA tag) → CI rewrites image tags in k8s/*.yaml
→ CI commits "ci: update image tags to <sha>" and pushes → ArgoCD polls → syncs → new pods
```

Final `.github/workflows/ci.yml` (the two added steps + the loop guard):

```yaml
jobs:
  build:
    if: "!startsWith(github.event.head_commit.message, 'ci:')"
    runs-on: ubuntu-latest
    permissions:
      packages: write      # Needed to push to ghcr.io
      contents: write      # Needed to commit updated image tags back to main

    steps:
      - uses: actions/checkout@v4

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push gateway
        run: |
          docker build -t ghcr.io/kvakz/quickticket-gateway:${{ github.sha }} ./app/gateway
          docker push ghcr.io/kvakz/quickticket-gateway:${{ github.sha }}

      - name: Build and push events
        run: |
          docker build -t ghcr.io/kvakz/quickticket-events:${{ github.sha }} ./app/events
          docker push ghcr.io/kvakz/quickticket-events:${{ github.sha }}

      - name: Build and push payments
        run: |
          docker build -t ghcr.io/kvakz/quickticket-payments:${{ github.sha }} ./app/payments
          docker push ghcr.io/kvakz/quickticket-payments:${{ github.sha }}

      - name: Update image tags in manifests
        run: |
          SHA=${{ github.sha }}
          sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/${{ github.actor }}/quickticket-gateway:${SHA}|" k8s/gateway.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-events:.*|image: ghcr.io/${{ github.actor }}/quickticket-events:${SHA}|" k8s/events.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-payments:.*|image: ghcr.io/${{ github.actor }}/quickticket-payments:${SHA}|" k8s/payments.yaml

      - name: Commit and push manifest update
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@github.com"
          git add k8s/
          git diff --cached --quiet || git commit -m "ci: update image tags to ${{ github.sha }}"
          git push
```

**One real bug found while testing:** the first run with the self-commit steps failed at `git push` — the explicit `permissions: packages: write` block *replaces* the token's default scopes, so the token had no `contents` scope at all. Adding `contents: write` fixed it. (Failed run: 36251237102, fixed run: 36251422686 — both linked under 5.1.)

### Git log: code commit → CI tag-update commit

```bash
$ git log --oneline -5
cef9563 ci: update image tags to ce3d600329e68c55401508d9129903372307cab5
ce3d600 fix(bonus): add contents write permission for manifest self-update
08f9ffa feat(bonus): auto-update image tags in CI manifests
db45b1a Revert "feat: deploy new gateway version"
810367d feat: deploy new gateway version
```

`ce3d600` is a human commit (CI change); `cef9563` was authored by `github-actions` — CI rewrote all three manifests to tag `ce3d600...` and pushed them.

### No infinite loop

The CI self-commit `cef9563` **did not trigger a new workflow run** — the Actions run list still ends at `ce3d600`:

```
https://github.com/kvakz/SRE-Intro/actions/runs/36251422686  | ce3d600 | success   <- last run
(cef9563 has no run)
```

Two layers stop the loop: (1) GitHub does not trigger workflow runs for pushes made with `GITHUB_TOKEN`; (2) the job-level `if: "!startsWith(github.event.head_commit.message, 'ci:')'"` guard skips any `ci:`-prefixed commit that a human or other bot might push.

### ArgoCD synced the auto-updated tag without manual intervention

```
15:14:25Z  bonus workflow pushed (ce3d600)
15:20:01Z  Sync Status:  Synced to  (ce3d600)  Health: Healthy     <- manifest not updated yet
15:21:22Z  Sync Status:  Synced to  (cef9563)  Health: Progressing <- CI self-commit detected
15:21:42Z  Sync Status:  Synced to  (cef9563)  Health: Healthy     <- new images up

$ kubectl get pods -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[0].ready,IMAGE:.spec.containers[0].image
NAME                      READY   IMAGE
events-5f9fd6b494-bwxqx   true    ghcr.io/kvakz/quickticket-events:ce3d600329e68c55401508d9129903372307cab5
gateway-6688849887-7qvcm  true    ghcr.io/kvakz/quickticket-gateway:ce3d600329e68c55401508d9129903372307cab5
payments-f87448ffb-58tgz  true    ghcr.io/kvakz/quickticket-payments:ce3d600329e68c55401508d9129903372307cab5
```

All three app pods are now running the CI-built `ce3d600...` images with zero human steps between the code push and the running cluster — the full automated loop works.
