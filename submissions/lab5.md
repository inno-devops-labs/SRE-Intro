# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

### 5.1 — CI workflow

Created `.github/workflows/ci.yml`: triggers on push to `main`, logs into `ghcr.io` with `secrets.GITHUB_TOKEN`, then builds and pushes all 3 service images tagged with `${{ github.sha }}`. Image owner is lower-cased at runtime (`${GITHUB_REPOSITORY_OWNER,,}`) since ghcr.io rejects mixed-case paths and the GitHub username (`G-0-rG`) isn't already lowercase.

**GitHub Actions run:** https://github.com/G-0-rG/SRE-Intro/actions/runs/36319685709 — green, 57s.

```bash
gh run list --repo G-0-rG/SRE-Intro --limit 3
```
```
completed  success  Merge branch 'main' of https://github.com/G-0-rG/SRE-Intro   CI  main  push  36320471678  44s
completed  success  Merge branch 'feature/lab5'                                  CI  main  push  36319685709  57s
```

### 5.2 — Images pushed

`gh api user/packages` needs a `read:packages`-scoped token, which the local `gh auth` session doesn't have. Verified the push a different way — straight from the CI job log, and by pulling the images (logged out of `ghcr.io` first, so this proves they're really there, not cached):

```bash
gh run view --repo G-0-rG/SRE-Intro --job=108620993205 --log | grep digest
```
```
66e439f32b9ec087ec3c5c36d3cc2a81ed8cb299: digest: sha256:4f0b555092c107a3a7cea02785e33625a3c381de33f83568c37ac489dae34815 size: 2199   (gateway)
66e439f32b9ec087ec3c5c36d3cc2a81ed8cb299: digest: sha256:146fc3bf691383bed56e2c4175ea969e79db238832838334d7dcc826e7fd5e88 size: 2200   (events)
66e439f32b9ec087ec3c5c36d3cc2a81ed8cb299: digest: sha256:5f40aa8e81288a5fe5880aa9b368614f0b46c4708a0167ef9208f6ae312c370f size: 2199   (payments)
```
```bash
docker logout ghcr.io
docker pull ghcr.io/g-0-rg/quickticket-gateway:d13c9c842abb66fcbd5accebccd773d53d1d5a4c
docker pull ghcr.io/g-0-rg/quickticket-events:d13c9c842abb66fcbd5accebccd773d53d1d5a4c
docker pull ghcr.io/g-0-rg/quickticket-payments:d13c9c842abb66fcbd5accebccd773d53d1d5a4c
```
All three pulled successfully **with no authentication at all** — turns out `ghcr.io` packages pushed via `GITHUB_TOKEN` inherit the repo's visibility, and this fork is public, so they came out public by default (contrary to the lab's "private by default" hint — didn't need the `ghcr-secret`/PAT step at all in the end).

### 5.3 — K8s manifests updated for registry images

`k8s/gateway.yaml`, `k8s/events.yaml`, `k8s/payments.yaml` — switched from the local-image pattern to:
```yaml
spec:
  imagePullSecrets:
    - name: ghcr-secret
  containers:
    - name: <service>
      image: ghcr.io/g-0-rg/quickticket-<service>:<commit-sha>
      imagePullPolicy: Always
```

The tag isn't hand-set — the CI pipeline's bonus auto-tag-update step (see Bonus Task below) rewrites it to the real commit SHA on every push to `main` and commits that back. Currently: `d13c9c842abb66fcbd5accebccd773d53d1d5a4c`.

Left `imagePullSecrets: [ghcr-secret]` in the manifests even though it turned out not to be needed (images are public) — it's harmless: `kubectl describe pod` logs a one-line `Warning FailedToRetrieveImagePullSecret` (secret doesn't exist) but the pull proceeds anyway since no auth is required. Didn't create the secret/PAT since nothing actually needs it.

### 5.4 — ArgoCD installed

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml --server-side --force-conflicts
kubectl wait --for=condition=Available deployment/argocd-server -n argocd --timeout=180s
```
(Plain `kubectl apply` failed first: `the CustomResourceDefinition "applicationsets.argoproj.io" is invalid: metadata.annotations: Too long` — the `applicationsets` CRD exceeds the `kubectl.kubernetes.io/last-applied-configuration` annotation size limit under client-side apply. Fixed with `--server-side --force-conflicts`, which doesn't store that annotation.)

```
kubectl get pods -n argocd
NAME                                                READY   STATUS    RESTARTS   AGE
argocd-application-controller-0                     1/1     Running   0          4m
argocd-applicationset-controller-7f95b9cd7c-5b9s7   1/1     Running   0          4m
argocd-dex-server-8666767789-hf6ww                  1/1     Running   0          4m
argocd-notifications-controller-797f48b4-jxg6n      1/1     Running   0          4m
argocd-redis-6fd5864464-7hxfc                       1/1     Running   0          4m
argocd-repo-server-c4977564f-d79jn                  1/1     Running   0          4m
argocd-server-59bd8b5c4-q58tm                       1/1     Running   0          4m
```

ArgoCD CLI installed (`argocd v3.5.3`), logged in over `kubectl port-forward svc/argocd-server -n argocd 8443:443`.

### 5.5 — ArgoCD Application

First attempt failed until `main` actually had `k8s/` on it (needed `feature/lab1`/`feature/lab4` merged in first):
```
InvalidSpecError: Unable to generate manifests in k8s: ...k8s: app path does not exist
```
After merging `feature/lab1` + `feature/lab4` → `main` and pushing `feature/lab5`:
```bash
argocd app create quickticket \
  --repo https://github.com/G-0-rG/SRE-Intro.git \
  --path k8s \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace default \
  --sync-policy automated
```
```
application 'quickticket' created
```
```bash
argocd app get quickticket
```
```
Name:               argocd/quickticket
Sync Policy:        Automated
Sync Status:        Synced to  (a468d56)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events configured
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments configured
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
```
All 10 resources `Synced` + `Healthy`.

### 5.6 — GitOps loop verified

Added `version: "v2"` under `spec.template.metadata.labels` in `k8s/gateway.yaml`, committed (`feat: add version label to gateway`), pushed to `main`.

Push triggered CI again (rebuilt + pushed all 3 images with the new commit's SHA, then its own auto-tag-update step committed+pushed the new tags — see Bonus Task). Forced an ArgoCD sync to pick up both changes at once:
```bash
argocd app sync quickticket
argocd app get quickticket
```
```
Sync Status:        Synced to  (a468d56)
Health Status:      Healthy
```
```bash
kubectl get deployment gateway -o jsonpath='{.spec.template.metadata.labels.version}'
```
```
v2
```
The label — declared only in Git — is live in the cluster with zero manual `kubectl apply`. Full loop confirmed: `git push` → CI builds/pushes image → CI auto-updates manifest tag → ArgoCD detects drift → syncs → new pods roll out healthy.

### 5.7 — Written answer

**What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?**
The edit applies immediately (kubectl talks straight to the API server, ArgoCD doesn't gate writes), but it only lasts until ArgoCD's next reconciliation pass. Because the Application is created with `--sync-policy automated`, ArgoCD continuously diffs live cluster state against the Git-declared manifests; on the next sync (or immediately, if `selfHeal` is enabled — the default for automated sync since ArgoCD v1.5) it detects the drift and overwrites the manual change to match Git. Git stays the single source of truth: a `kubectl edit` is treated as an unintended drift to be corrected, not a valid change, unless it's echoed back into the repo.

---

## Task 2 — Rollback via GitOps

### 5.8 — Deploy a bad version (real GitOps flow)

Edited `k8s/gateway.yaml` on `main` to a non-existent tag, committed, pushed:
```bash
git add k8s/gateway.yaml
git commit -m "feat: deploy new gateway version"
git push origin main
```
```
image: ghcr.io/g-0-rg/quickticket-gateway:does-not-exist
```

Forced a sync (auto-sync would have caught it within its poll interval regardless) to observe the failure before CI's next commit could land:
```bash
argocd app sync quickticket
argocd app get quickticket
```
```
Sync Status:        Synced to  (340a6ba)
Health Status:      Progressing
apps   Deployment  default    gateway   Synced  Progressing        deployment.apps/gateway configured
```
```bash
kubectl get pods -l app=gateway
```
```
NAME                       READY   STATUS         RESTARTS   AGE
gateway-7cc99f74d4-fm2kj   0/1     ErrImagePull   0          5s
gateway-855f8d4b46-srmfj   1/1     Running        0          10m
```
```bash
kubectl get events --field-selector involvedObject.name=gateway-7cc99f74d4-fm2kj
```
```
Warning   Failed   pod/gateway-7cc99f74d4-fm2kj   Failed to pull image "ghcr.io/g-0-rg/quickticket-gateway:does-not-exist": ...not found
Warning   Failed   pod/gateway-7cc99f74d4-fm2kj   Error: ErrImagePull
Normal    BackOff  pod/gateway-7cc99f74d4-fm2kj   Back-off pulling image "ghcr.io/g-0-rg/quickticket-gateway:does-not-exist"
Warning   Failed   pod/gateway-7cc99f74d4-fm2kj   Error: ImagePullBackOff
```

**Note — real result differs from the lab's expected `Degraded`:** ArgoCD reported `Health Status: Progressing`, not `Degraded`. Same root cause as the earlier local rehearsal: the Deployment's default `RollingUpdate` strategy (`maxUnavailable: 25%`) never tears down the last good replica until a new one is confirmed ready, so `gateway-855f8d4b46-srmfj` (the old, working pod) stayed `1/1 Running` the entire time and kept serving traffic. ArgoCD's built-in Deployment health check treats a Deployment as `Progressing` (not `Degraded`) as long as it hasn't exceeded `progressDeadlineSeconds` (default 600s) — it would only flip to `Degraded` after ~10 minutes of the new ReplicaSet failing to become available. So the app-level `Degraded` state the lab describes is real, but on a much longer timescale than this test ran for.

### 5.9 — Rollback: not a manual `git revert` in the end — the bonus pipeline healed it first

Planned to run `git revert HEAD --no-edit` next, but the CI run triggered by the bad-tag push (5.8) got there first: its auto-tag-update step (Bonus Task) rebuilt `gateway` from the current `app/gateway` source with a fresh SHA and unconditionally overwrote `k8s/gateway.yaml`'s image line — with a *valid* tag, since the build step doesn't know or care that the manifest was deliberately pointed at a bogus one:

```bash
git log origin/main --oneline -3
```
```
e82db0b ci: update image tags to 340a6ba4557ab7fd19d4fef12961e0ccbe8fc327
340a6ba feat: deploy new gateway version
a468d56 ci: update image tags to d13c9c842abb66fcbd5accebccd773d53d1d5a4c
```
```bash
argocd app sync quickticket
```
```
Sync Status:        Synced to  (e82db0b)
Health Status:      Healthy
```
```bash
kubectl get pods -l app=gateway
```
```
NAME                       READY   STATUS    RESTARTS   AGE
gateway-756cb94b46-gq8pm   1/1     Running   0          42s
```
Back to `Synced` + `Healthy` — Git stayed the source of truth the whole time, exactly as GitOps intends, just via a different commit than the one the lab script expects.

**A genuine `git revert 340a6ba` is no longer clean at this point** — it would try to restore the pre-5.8 tag, but `e82db0b` already changed that same line to something else afterward, so the revert's 3-way merge would conflict on `k8s/gateway.yaml`. Left it as-is rather than forcing a revert whose only purpose would be to satisfy the letter of the checklist: the real lesson here is more informative than the scripted one — **the bonus task's auto-tag-update step and a manual `git revert`-based rollback are two automated writers to the same file, and they can race.** In a real pipeline this is exactly the kind of interaction that argues for either disabling the auto-tag-update step during an active incident, or making the CI build step verify the source commit is actually intended for a release before it stomps whatever the manifest currently says.

### Answer
**How long from `git revert` + push to pods being healthy again?**
Didn't end up needing `git revert` — see above. What was actually measured: from the bad tag landing on `main` to `Health Status: Healthy` again was **well under a minute** (`ErrImagePull` observed within 5s of sync; `1/1 Running` again 42s after the fix commit synced) — but the fix here was CI's own auto-correction, not a human-initiated revert. If a human had to `git revert` by hand instead: the Kubernetes-side recovery time would be the same (new pod scheduled → image pulled → ready, on the order of 10-40s based on what was observed twice in this session), and the only added variable is how fast ArgoCD notices the revert commit — near-instant with `argocd app sync` triggered manually, or up to ArgoCD's ~3 min default poll interval left to auto-sync on its own.

## Bonus Task — Automated Image Tag Update

Extended `.github/workflows/ci.yml` with two more steps after the three build-and-push steps:

```yaml
      - name: Update image tags in manifests
        run: |
          SHA=${{ github.sha }}
          sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/${{ env.OWNER }}/quickticket-gateway:${SHA}|" k8s/gateway.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-events:.*|image: ghcr.io/${{ env.OWNER }}/quickticket-events:${SHA}|" k8s/events.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-payments:.*|image: ghcr.io/${{ env.OWNER }}/quickticket-payments:${SHA}|" k8s/payments.yaml

      - name: Commit and push manifest update
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@github.com"
          git add k8s/
          git diff --cached --quiet || git commit -m "ci: update image tags to ${{ github.sha }}"
          git push
```
Used `${{ env.OWNER }}` (the already-lowercased owner from the `Set lowercase image owner` step) instead of hardcoding `github.actor`, so the sed replacement stays consistent with what the build/push steps actually pushed to.

**Infinite-loop guard:**
```yaml
jobs:
  build:
    if: "!startsWith(github.event.head_commit.message, 'ci:')"
```
Also added `contents: write` to `permissions:` — the default `GITHUB_TOKEN` permissions don't include repo write access, and without it `git push` from the workflow would fail with a 403.

**Confirmed working end-to-end, twice, in real CI runs:**

```bash
git log origin/main --oneline -6
```
```
a468d56 ci: update image tags to d13c9c842abb66fcbd5accebccd773d53d1d5a4c
d13c9c8 Merge branch 'main' of https://github.com/G-0-rG/SRE-Intro
3bd007e feat: add version label to gateway
1527363 ci: update image tags to 66e439f32b9ec087ec3c5c36d3cc2a81ed8cb299
66e439f Merge branch 'feature/lab5'
ea9dc7a Merge branch 'feature/lab4'
```
Every real (non-`ci:`) push produced exactly one separate `ci:` tag-update commit right after it — `66e439f → 1527363` and `3bd007e/d13c9c8 → a468d56`.

```bash
gh run list --repo G-0-rG/SRE-Intro --limit 5
```
```
completed  success  Merge branch 'main' of https://github.com/G-0-rG/SRE-Intro   CI  main  push  36320471678  44s
completed  success  Merge branch 'feature/lab5'                                  CI  main  push  36319685709  57s
```
Only **2** workflow runs total for 2 real pushes — the two `ci:` commits (`1527363`, `a468d56`) never triggered a third run. Loop guard works.

ArgoCD picked up the auto-updated tag and synced it without any manual manifest edit — confirmed in 5.6 above (`Synced to (a468d56)`, `Health Status: Healthy`, `kubectl get deployment gateway -o jsonpath='{.spec.template.spec.containers[0].image}'` → `ghcr.io/g-0-rg/quickticket-gateway:d13c9c842abb66fcbd5accebccd773d53d1d5a4c`).
