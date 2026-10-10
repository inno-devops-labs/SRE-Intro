# Lab 5 — CI/CD & GitOps — Submission

**Student:** jakefish18
**Repo:** https://github.com/jakefish18/SRE-Intro
**Branch:** `feature/lab5`

PR checklist:
```text
- [x] Task 1 done — CI pipeline + ArgoCD deployed + GitOps loop verified
- [x] Task 2 done — rollback via git revert
- [x] Bonus Task done — automated image tag update
```

> **Note on environment:** to keep `main` clean, the CI workflow triggers on
> `feature/lab5` (in addition to `main`) and the ArgoCD Application tracks the
> `feature/lab5` revision. The GitOps mechanics are identical to tracking `main`.
> ghcr.io packages were made **public**, so no `imagePullSecret` is required
> (`imagePullPolicy: Always` pulls anonymously).

---

## Task 1 — CI Pipeline + ArgoCD Setup

### 1. GitHub Actions run (green check)

- ✅ https://github.com/jakefish18/SRE-Intro/actions/runs/28267406118 — *feat(lab5): add CI/CD pipeline and ArgoCD GitOps* (initial pipeline, builds + pushes all 3 images)
- ✅ https://github.com/jakefish18/SRE-Intro/actions/runs/28334004365 — *feat: add version label to gateway* (re-build + auto-tag)

All workflow steps (build + push images, update image tags, commit/push) completed
green in ~55s.

### 2. Images pushed to ghcr.io

The lab's `gh api user/packages?package_type=container` requires a token with the
`read:packages` scope; the `gh` CLI's OAuth token does not have it, so that exact
call returns 403:

```
$ gh api "user/packages?package_type=container" --jq '.[].name'
{"message":"You need at least read:packages scope to list packages.", ... "status":"403"}
```

Equivalent proof — the three images are published and **public** on ghcr.io
(anonymous registry manifest request returns HTTP 200 for the built SHA):

```
$ for svc in gateway events payments; do ... ghcr.io/v2/jakefish18/quickticket-$svc/manifests/<sha> ; done
ghcr.io/jakefish18/quickticket-gateway   ->  HTTP 200
ghcr.io/jakefish18/quickticket-events    ->  HTTP 200
ghcr.io/jakefish18/quickticket-payments  ->  HTTP 200
```

They are also visible at https://github.com/jakefish18?tab=packages.


### 3. `argocd app get quickticket` — Synced + Healthy

```
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
Source:
- Repo:             https://github.com/jakefish18/SRE-Intro.git
  Target:           feature/lab5
  Path:             k8s
Sync Policy:        Automated
Sync Status:        Synced to feature/lab5 (15feeb5)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   MESSAGE
       Service     default    gateway   Synced  Healthy  service/gateway unchanged
       Service     default    events    Synced  Healthy  service/events unchanged
       Service     default    payments  Synced  Healthy  service/payments unchanged
       Service     default    postgres  Synced  Healthy  service/postgres unchanged
       Service     default    redis     Synced  Healthy  service/redis unchanged
apps   Deployment  default    gateway   Synced  Healthy  deployment.apps/gateway unchanged
apps   Deployment  default    events    Synced  Healthy  deployment.apps/events unchanged
apps   Deployment  default    payments  Synced  Healthy  deployment.apps/payments unchanged
apps   Deployment  default    postgres  Synced  Healthy  deployment.apps/postgres unchanged
apps   Deployment  default    redis     Synced  Healthy  deployment.apps/redis unchanged
```

### 4. Proof a Git change was synced to the cluster

A `version: "v2"` label was added to the gateway Deployment in Git (commit
`b4dbad3`), pushed, and synced by ArgoCD. The label is live in the cluster, and the
running image is the SHA the CI auto-tagged:

```
$ kubectl get deployment gateway -n default -o jsonpath='{.metadata.labels.version}'
v2

$ kubectl get deployment gateway -n default -o jsonpath='{.spec.template.spec.containers[0].image}'
ghcr.io/jakefish18/quickticket-gateway:b4dbad3edacdf1c055234d40b3d7b263eb3d0e9c
```

### 5. What happens if someone runs `kubectl edit` on an ArgoCD-managed resource?

ArgoCD continuously compares the **live** cluster state against the **desired**
state in Git (the source of truth). A manual `kubectl edit` makes the live state
diverge, so ArgoCD immediately reports the Application as **OutOfSync** and shows
the manual change as a diff (`argocd app diff`).

What happens next depends on the sync policy:

- **`automated` + `selfHeal`** → ArgoCD automatically reverts the manual edit back
  to the Git-defined state within the reconciliation interval. The manual change is
  effectively impossible to keep — Git wins.
- **`automated` without selfHeal** (what the lab's `--sync-policy automated`
  configures) → the app is flagged OutOfSync but the drift is *not* auto-reverted
  until the next sync is triggered (a new Git commit, a manual `argocd app sync`,
  or enabling self-heal). Running `argocd app sync` (or `--self-heal`) restores Git
  state.
- **Manual sync policy** → the edit persists and the app simply stays OutOfSync
  until someone syncs.

**Bottom line:** out-of-band `kubectl edit` is an anti-pattern under GitOps — it's
either reverted automatically (self-heal) or shows up as drift that the next sync
wipes out. The correct way to change a managed resource is to commit the change to
Git and let ArgoCD apply it.

---

## Task 2 — Rollback via GitOps

The broken version (`image: ghcr.io/jakefish18/quickticket-gateway:does-not-exist`)
was committed as `b06d5e6`. The commit message starts with `ci:` *on purpose* so the
workflow's auto-tag step is skipped — otherwise the bonus pipeline would rebuild and
overwrite the bad tag with a valid SHA, "healing" the break before ArgoCD could show
it.

### Bad deploy — `argocd app get` showing Degraded/Progressing

```
Sync Status:        Synced to feature/lab5 (b06d5e6)
Health Status:      Progressing

GROUP  KIND        NAMESPACE  NAME     STATUS  HEALTH       MESSAGE
apps   Deployment  default    gateway  Synced  Progressing  deployment.apps/gateway configured
```

(ArgoCD holds at `Progressing`; it flips to `Degraded` once the Deployment's
`progressDeadlineSeconds` — default 600s — is exceeded. The new ReplicaSet can never
become ready because the image does not exist.)

### `kubectl get pods` showing ImagePullBackOff

```
$ kubectl get pods -n default | grep gateway
gateway-667b76d744-rpbkh    1/1     Running            0          23m   <- old good pod still serving
gateway-868895d66c-tltlw    0/1     ImagePullBackOff   0          43s   <- new bad pod
```

The rolling update keeps the old pod up (so the Service stays available) while the
new pod is stuck pulling the non-existent image.

### `git log --oneline -3` showing deploy + revert

```
$ git log --oneline -3
1c2c8f6 Revert "ci: task2 deploy broken gateway tag (intentional bad deploy)"
b06d5e6 ci: task2 deploy broken gateway tag (intentional bad deploy)
15feeb5 ci: update image tags to b4dbad3edacdf1c055234d40b3d7b263eb3d0e9c
```

### After `git revert` — `argocd app get` showing Healthy

```
Sync Status:   Synced to feature/lab5 (1c2c8f6)
Health Status: Healthy

$ kubectl get pods -n default | grep gateway
gateway-667b76d744-rpbkh    1/1     Running   0   26m
```

### How long from `git revert` + push to pods healthy again?

**~6 seconds** when recovery is triggered with `argocd app sync` (measured: revert
push → `argocd app wait --health` returning Healthy = 6s). Recovery was near-instant
because the revert restored the previous good image (`…:b4dbad3…`), which was already
cached on the node and whose old pod had never been torn down — ArgoCD simply scaled
the broken ReplicaSet to 0.

Without a manual sync, ArgoCD's default Git poll interval is **~3 minutes**, so an
unattended rollback would heal within that window. (A fresh image that still needed
pulling would add the pull time on top.)

---

## Bonus Task — Automated Image Tag Update

The CI workflow (`.github/workflows/ci.yml`) builds each image tagged with the
commit SHA, then rewrites the `image:` tag in `k8s/*.yaml` to that SHA and pushes
a `ci: update image tags to <sha>` commit back to the branch. A guard
(`if: "!startsWith(github.event.head_commit.message, 'ci:')"`) prevents the
CI-authored commit from re-triggering the workflow (an infinite loop). ArgoCD then
syncs the auto-updated tag with no human intervention.

### Git log showing: code commit → CI tag-update commit

Each human/code commit is immediately followed by an automated `ci: update image
tags to <sha>` commit authored by the workflow:

```
$ git log --oneline -5
15feeb5 ci: update image tags to b4dbad3edacdf1c055234d40b3d7b263eb3d0e9c   <- CI auto-commit
b4dbad3 feat: add version label to gateway                                  <- my commit
7a9e968 ci: update image tags to 52037579b761e85cd77ea99ac3b005a371cdc532   <- CI auto-commit
5203757 feat(lab5): add CI/CD pipeline and ArgoCD GitOps                    <- my commit
3634ca7 feat: solution
```

### ArgoCD syncing the auto-updated tag

ArgoCD synced to the CI-authored commit `15feeb5` with no manual edit to the
manifests — the image tag in `k8s/gateway.yaml` was written by CI, and ArgoCD
deployed it:

```
Sync Status:   Synced to feature/lab5 (15feeb5)
Health Status: Healthy
running image: ghcr.io/jakefish18/quickticket-gateway:b4dbad3edacdf1c055234d40b3d7b263eb3d0e9c
```

**Loop guard:** the `ci:` commit does **not** re-trigger the workflow because of
`if: "!startsWith(github.event.head_commit.message, 'ci:')"` (and GitHub also does
not run workflows for pushes made with the default `GITHUB_TOKEN`). No infinite loop.

Concrete evidence — the Task 2 bad-deploy commit `b06d5e6` (message prefixed `ci:`)
shows a **skipped** workflow run, while normal commits run green:

```
$ gh run list --branch feature/lab5
completed  success  1c2c8f6  Revert "ci: task2 deploy broken gateway tag ..."
completed  skipped  b06d5e6  ci: task2 deploy broken gateway tag (intentional bad deploy)
completed  success  b4dbad3  feat: add version label to gateway
```
