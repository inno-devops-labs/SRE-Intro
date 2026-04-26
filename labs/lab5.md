# Lab 5 — CI/CD & GitOps

![difficulty](https://img.shields.io/badge/difficulty-intermediate-yellow)
![topic](https://img.shields.io/badge/topic-CI%2FCD%20%26%20GitOps-blue)
![points](https://img.shields.io/badge/points-10%2B2-orange)
![tech](https://img.shields.io/badge/tech-GitHub%20Actions%20%2B%20ArgoCD-informational)

> **Goal:** Write a CI pipeline that builds and pushes container images, deploy ArgoCD, and set up GitOps deployment from Git to Kubernetes.
> **Deliverable:** A PR from `feature/lab5` with `.github/workflows/ci.yml` and `submissions/lab5.md`. Submit PR link via Moodle.

---

## Overview

In this lab you will practice:
- Writing a GitHub Actions CI workflow from scratch
- Building and pushing container images to GitHub Container Registry (ghcr.io)
- Installing ArgoCD on your k3d cluster
- Creating an ArgoCD Application that deploys from your Git repo
- Experiencing the full GitOps loop: push → build → sync → deploy

> **Nothing is provided.** You write the workflow and configure ArgoCD yourself.

---

## Project State

**You should have from previous labs:**
- QuickTicket K8s manifests in `k8s/` (from Lab 4)
- k3d cluster with QuickTicket deployed

**This lab adds:**
- GitHub Actions CI pipeline (`.github/workflows/ci.yml`)
- ArgoCD managing deployments from your Git repo
- GitOps workflow: code change → image build → ArgoCD sync

---

## Task 1 — CI Pipeline + ArgoCD Setup (6 pts)

**Objective:** Write a CI workflow, install ArgoCD, and verify the GitOps loop.

### 5.1: Create the CI workflow

Create `.github/workflows/ci.yml` in your fork. The workflow should:

1. Trigger on push to `main`
2. Build Docker images for all 3 services
3. Push them to GitHub Container Registry (ghcr.io)

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      packages: write      # Needed to push to ghcr.io

    steps:
      - uses: actions/checkout@v4

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      # YOUR TASK: Add steps to build and push all 3 images
      # For each service (gateway, events, payments):
      #   1. docker build -t ghcr.io/<your-username>/quickticket-<service>:${{ github.sha }} ./app/<service>
      #   2. docker push ghcr.io/<your-username>/quickticket-<service>:${{ github.sha }}
      #
      # Hint: ${{ github.sha }} gives the commit SHA — unique, immutable tag
      # Hint: Replace <your-username> with your GitHub username (lowercase)
```

Push to main and verify the workflow runs:

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add CI pipeline for QuickTicket"
git push origin main
```

Check: Go to your fork → Actions tab → the workflow should be running.

### 5.2: Verify images are pushed

After the workflow completes (green check):

```bash
# List your packages (replace YOUR_USERNAME)
gh api user/packages?package_type=container --jq '.[].name'
```

Or check: Your fork → Packages tab — you should see 3 container images.

### 5.3: Update K8s manifests to use registry images

Update your `k8s/*.yaml` manifests to use the ghcr.io images instead of local ones:

```yaml
# Before (local image)
image: quickticket-gateway:v1
imagePullPolicy: Never

# After (registry image — use YOUR commit SHA from the CI run)
image: ghcr.io/YOUR_USERNAME/quickticket-gateway:COMMIT_SHA
imagePullPolicy: Always
```

Also add `imagePullSecrets` to each Deployment so k3d can pull private images:

```yaml
    spec:
      imagePullSecrets:
        - name: ghcr-secret
      containers:
        ...
```

Create the pull secret in your cluster (you need a **classic PAT** with `read:packages` scope — [create one here](https://github.com/settings/tokens/new?scopes=read:packages)):

```bash
kubectl create secret docker-registry ghcr-secret \
  --docker-server=ghcr.io \
  --docker-username=YOUR_GITHUB_USERNAME \
  --docker-password=YOUR_CLASSIC_PAT
```

Commit and push:

```bash
git add k8s/
git commit -m "feat: use ghcr.io images in K8s manifests"
git push origin main
```

### 5.4: Install ArgoCD

```bash
# Create namespace
kubectl create namespace argocd

# Install ArgoCD
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# Wait for ArgoCD to be ready
kubectl wait --for=condition=Available deployment/argocd-server -n argocd --timeout=120s

# Get the admin password
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
echo  # newline
```

Access the ArgoCD UI:

```bash
kubectl port-forward svc/argocd-server -n argocd 8443:443 &
# Open https://localhost:8443 (accept the self-signed cert warning)
# Login: admin / <password from above>
```

### 5.5: Create an ArgoCD Application

Install the ArgoCD CLI:

```bash
# Linux
curl -sSL -o argocd https://github.com/argoproj/argo-cd/releases/latest/download/argocd-linux-amd64
chmod +x argocd && sudo mv argocd /usr/local/bin/
```

Login and create the Application:

```bash
# Login (use the password from 5.4)
argocd login localhost:8443 --insecure --username admin --password <PASSWORD>

# Create Application pointing to your k8s/ directory
argocd app create quickticket \
  --repo https://github.com/YOUR_USERNAME/SRE-Intro.git \
  --path k8s \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace default \
  --sync-policy automated
```

Verify:

```bash
argocd app get quickticket
# Should show: Sync Status: Synced, Health: Healthy
```

### 5.6: Verify the GitOps loop

Make a visible change — edit a gateway env var or add a label:

```bash
# Add an annotation to gateway deployment
# Edit k8s/gateway.yaml — add under metadata.labels:
#   version: "v2"

git add k8s/gateway.yaml
git commit -m "feat: add version label to gateway"
git push origin main
```

Wait ~3 minutes (ArgoCD default poll interval) or trigger manual sync:

```bash
argocd app sync quickticket
```

Verify the change is live:

```bash
kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
# Should show: v2
```

### 5.7: Proof of work

**`.github/workflows/ci.yml`** and updated `k8s/` manifests go in your fork.

**Paste into `submissions/lab5.md`:**
1. Link to your GitHub Actions run (green check)
2. Output of `gh api user/packages?package_type=container` showing pushed images
3. Output of `argocd app get quickticket` showing Synced + Healthy
4. Output proving a Git change was synced (label, annotation, or image tag change visible in cluster)
5. Answer: "What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?"

<details>
<summary>💡 Hints</summary>

- ghcr.io images may be private by default — go to Packages settings and make them public, or create an `imagePullSecret` in K8s
- If ArgoCD can't pull from a private repo, you need to add the repo: `argocd repo add https://github.com/... --username git --password <PAT>`
- `${{ github.sha }}` in GitHub Actions gives the full 40-char commit SHA
- ArgoCD polls Git every 3 minutes by default. For instant sync, use webhooks or `argocd app sync`
- If `argocd app get` shows `OutOfSync`, run `argocd app sync quickticket`
- Your GitHub username in ghcr.io URLs must be **lowercase**

</details>

---

## Task 2 — Rollback via GitOps (4 pts)

> ⏭️ This task is optional. Skipping it will not affect future labs.

**Objective:** Deploy a broken version and rollback using Git, not kubectl.

### 5.8: Deploy a bad version

Update a manifest with a non-existent image tag:

```bash
# Edit k8s/gateway.yaml — change image to a tag that doesn't exist:
# image: ghcr.io/YOUR_USERNAME/quickticket-gateway:does-not-exist
git add k8s/gateway.yaml
git commit -m "feat: deploy new gateway version"
git push origin main
```

Wait for ArgoCD to sync, then observe:

```bash
argocd app get quickticket
# Health should show: Degraded or Progressing
kubectl get pods
# Gateway pod should show: ImagePullBackOff or ErrImagePull
```

### 5.9: Rollback via git revert

```bash
git revert HEAD --no-edit
git push origin main
```

Watch ArgoCD sync the revert:

```bash
argocd app get quickticket
# Should return to: Synced + Healthy
kubectl get pods
# Gateway pod should be Running again
```

**Paste into `submissions/lab5.md`:**
- `argocd app get` showing Degraded after bad deploy
- `kubectl get pods` showing ImagePullBackOff
- `git log --oneline -3` showing the deploy + revert commits
- `argocd app get` showing Healthy after revert
- Answer: "How long from `git revert` + push to pods being healthy again?"

---

## Bonus Task — Automated Image Tag Update (2 pts)

> 🌟 For those who want extra challenge and experience.

**Objective:** Make the CI pipeline automatically update the image tag in K8s manifests after building.

The full GitOps loop should be:
1. Push code change
2. CI builds new image with SHA tag
3. CI updates image tag in `k8s/*.yaml`
4. CI commits and pushes the manifest change
5. ArgoCD detects → syncs → deploys

Add a step to your CI workflow after pushing the images:

```yaml
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

> **Warning:** This creates a commit from CI that triggers another CI run. Add a check to skip if the commit message starts with "ci:":
> ```yaml
> on:
>   push:
>     branches: [main]
> jobs:
>   build:
>     if: "!startsWith(github.event.head_commit.message, 'ci:')"
> ```

**Paste into `submissions/lab5.md`:**
- Updated workflow file showing auto-tag update
- Git log showing: code commit → CI tag-update commit
- ArgoCD syncing the auto-updated tag without manual intervention

---

## How to Submit

```bash
git switch -c feature/lab5
git add .github/workflows/ci.yml k8s/ submissions/lab5.md
git commit -m "feat(lab5): add CI/CD pipeline and ArgoCD GitOps"
git push -u origin feature/lab5
```

PR checklist:
```text
- [x] Task 1 done — CI pipeline + ArgoCD deployed + GitOps loop verified
- [ ] Task 2 done — rollback via git revert
- [ ] Bonus Task done — automated image tag update
```

---

## Acceptance Criteria

### Task 1 (6 pts)
- ✅ CI workflow committed (`.github/workflows/ci.yml`)
- ✅ GitHub Actions run shows green (images built + pushed)
- ✅ Images visible in ghcr.io
- ✅ ArgoCD installed and Application created
- ✅ Git change synced to cluster via ArgoCD
- ✅ Written answer about `kubectl edit` with ArgoCD

### Task 2 (4 pts)
- ✅ Bad deploy detected (Degraded status + ImagePullBackOff)
- ✅ `git revert` restored healthy state
- ✅ Git log showing deploy + revert
- ✅ Recovery time measured

### Bonus Task (2 pts)
- ✅ CI auto-updates image tags in manifests
- ✅ No infinite loop (skip CI-triggered commits)
- ✅ ArgoCD syncs auto-updated tag

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — CI + ArgoCD + GitOps | **6** | Workflow written, images pushed, ArgoCD deployed, GitOps loop verified |
| **Task 2** — Rollback via Git | **4** | Bad deploy → git revert → recovery, timing measured |
| **Bonus Task** — Auto tag update | **2** | Full automated loop: push → build → update tag → ArgoCD sync |
| **Total** | **12** | 10 main + 2 bonus |

---

## Resources

<details>
<summary>📚 Documentation</summary>

- [GitHub Actions quickstart](https://docs.github.com/en/actions/quickstart)
- [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [ArgoCD Getting Started](https://argo-cd.readthedocs.io/en/stable/getting_started/)
- [ArgoCD CLI reference](https://argo-cd.readthedocs.io/en/stable/user-guide/commands/argocd/)

</details>

<details>
<summary>⚠️ Common Pitfalls</summary>

- **ghcr.io images are private by default** — create an `imagePullSecret` with a classic PAT (fine-grained PATs don't work with ghcr.io)
- **GitHub username must be lowercase** in ghcr.io URLs
- **ArgoCD can't access private repos** — add repo with `argocd repo add` + GitHub PAT
- **ArgoCD polls every 3 min** — use `argocd app sync` for instant sync during testing
- **CI commit triggers infinite loop** — filter CI commits in workflow trigger
- **`imagePullPolicy: Always`** — needed when using registry images (not `Never` like local k3d images)

</details>
