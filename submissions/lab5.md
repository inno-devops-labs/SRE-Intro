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

<!-- ACTIONS_RUN -->

### 2. Images pushed to ghcr.io

```
<!-- PACKAGES -->
```

### 3. `argocd app get quickticket` — Synced + Healthy

```
<!-- ARGOCD_GET -->
```

### 4. Proof a Git change was synced to the cluster

A `version: "v2"` label was added to the gateway Deployment in Git, pushed, and
synced by ArgoCD:

```
<!-- GITOPS_PROOF -->
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

### Bad deploy — `argocd app get` showing Degraded

```
<!-- BAD_ARGOCD -->
```

### `kubectl get pods` showing ImagePullBackOff

```
<!-- BAD_PODS -->
```

### `git log --oneline -3` showing deploy + revert

```
<!-- ROLLBACK_LOG -->
```

### After `git revert` — `argocd app get` showing Healthy

```
<!-- GOOD_ARGOCD -->
```

### How long from `git revert` + push to pods healthy again?

<!-- ROLLBACK_TIME -->

---

## Bonus Task — Automated Image Tag Update

The CI workflow (`.github/workflows/ci.yml`) builds each image tagged with the
commit SHA, then rewrites the `image:` tag in `k8s/*.yaml` to that SHA and pushes
a `ci: update image tags to <sha>` commit back to the branch. A guard
(`if: "!startsWith(github.event.head_commit.message, 'ci:')"`) prevents the
CI-authored commit from re-triggering the workflow (an infinite loop). ArgoCD then
syncs the auto-updated tag with no human intervention.

### Git log showing: code commit → CI tag-update commit

```
<!-- BONUS_LOG -->
```

### ArgoCD syncing the auto-updated tag

```
<!-- BONUS_SYNC -->
```
