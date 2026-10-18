# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

Workflow: `.github/workflows/ci.yml`

- Triggers on push to `main`
- Skips runs whose commit message starts with `ci:` (avoids an infinite loop from the auto tag-update commit)
- Builds and pushes `gateway`, `events`, and `payments` to `ghcr.io/tayaorshulskaya-oss/quickticket-<service>:<sha>`
- Updates image tags in `k8s/*.yaml` and pushes a `ci: update image tags` commit

K8s manifests use registry images + `imagePullPolicy: Always` + `imagePullSecrets: ghcr-secret`. Gateway Deployment has `version: "v2"` so GitOps sync is visible on the cluster.

### 1. GitHub Actions run (green check)

Paste the Actions URL after the first successful run on `main`:

```
https://github.com/tayaorshulskaya-oss/SRE-Intro/actions/runs/<RUN_ID>
```

### 2. Pushed container images

Command:

```bash
gh api user/packages?package_type=container --jq '.[].name'
```

Expected names:

```
quickticket-events
quickticket-gateway
quickticket-payments
```

Paste the real `gh api` JSON/output here after CI finishes:

```
<PASTE>
```

### 3. ArgoCD Application Synced + Healthy

Command:

```bash
argocd app get quickticket
```

Paste output showing `Sync Status: Synced` and `Health Status: Healthy`:

```
<PASTE>
```

### 4. Git change synced to the cluster

Change: `metadata.labels.version: "v2"` on the gateway Deployment.

Command:

```bash
kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
```

Expected:

```
v2
```

Paste live output:

```
<PASTE>
```

### 5. What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?

Git is the source of truth. `kubectl edit` only changes live cluster state. ArgoCD will mark the Application **OutOfSync** because the cluster no longer matches Git.

With automated sync **and** self-heal enabled, ArgoCD reverts the edit on the next reconcile (typically within the 3-minute poll interval, or immediately if you run `argocd app sync`). The cluster is healed back to what is in Git; the kubectl edit is lost unless the same change is committed.

With automated sync **without** self-heal (`argocd app create ... --sync-policy automated` only), Git-driven deploys still apply when the repo changes, but a one-off kubectl edit can sit as OutOfSync until someone syncs or self-heal is turned on. Durable changes still belong in Git, not in the cluster.

---

## Task 2 — Rollback via GitOps

### Bad deploy (non-existent image tag)

After pushing `image: ghcr.io/tayaorshulskaya-oss/quickticket-gateway:does-not-exist`:

`argocd app get quickticket` — paste Degraded / Progressing:

```
<PASTE>
```

`kubectl get pods` — paste ImagePullBackOff / ErrImagePull on gateway:

```
<PASTE>
```

### git revert

```bash
git revert HEAD --no-edit
git push origin main
```

`git log --oneline -3`:

```
<PASTE>
```

`argocd app get quickticket` after revert — Synced + Healthy:

```
<PASTE>
```

`kubectl get pods` — gateway Running:

```
<PASTE>
```

### How long from `git revert` + push to pods being healthy again?

Measure it: note the time of `git push`, then poll `argocd app get` and `kubectl get pods` until gateway is Running and the app is Healthy.

Typical range:

- ArgoCD poll interval is **3 minutes** unless you run `argocd app sync quickticket` (then detect is seconds).
- After sync, Kubernetes starts a new pod and pulls the good image — usually **30–90 seconds** if the image is already in ghcr.io and the pull secret works.

**Recorded recovery time:** `<MM:SS from push to Healthy>` (fill after you time it). With a manual `argocd app sync` it is usually under ~2 minutes; waiting for poll only is often ~3–5 minutes.

---

## Bonus — Automated image tag update

See `.github/workflows/ci.yml`:

- `if: ${{ !startsWith(github.event.head_commit.message, 'ci:') }}` skips the follow-up commit
- After push, sed rewrites the three `k8s` image lines to the build SHA
- `contents: write` lets the workflow commit `ci: update image tags to <sha>`

`git log --oneline` showing code commit then CI tag-update commit:

```
<PASTE>
```

ArgoCD picking up the new SHA without a manual kubectl set-image (paste `argocd app get` or `kubectl get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].image}'`):

```
<PASTE>
```

---

## PR checklist

- [x] Task 1 — CI workflow + ghcr images + ArgoCD GitOps files
- [x] Task 2 — rollback procedure via `git revert` (paste cluster proof after you run it)
- [x] Bonus — auto tag update in CI (no infinite loop)
