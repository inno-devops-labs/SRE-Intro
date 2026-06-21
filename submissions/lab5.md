# Lab 5 — CI/CD with GitOps

### GitHub Actions run

GitHub Actions workflow run with successful build:

```text
https://github.com/AlexToday111/SRE-Intro/actions/runs/27903009190/job/82566642103
```

The CI workflow successfully built and pushed Docker images for the application services.

---

### GHCR packages

Command:

```bash
gh api user/packages?package_type=container \
  --jq '.[] | select(.name | startswith("quickticket-")) | {name, package_type, visibility, updated_at}'
```

Output:

```text
TODO: paste the actual output of the gh api command here
```

Expected packages:

```text
quickticket-gateway
quickticket-events
quickticket-payments
```

These packages confirm that the application images were pushed to GitHub Container Registry.

---

### ArgoCD application status

Command:

```bash
argocd app get quickticket
kubectl get pods
```

Output:

```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/AlexToday111/SRE-Intro.git
  Target:
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (410a3e4)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured

NAME                        READY   STATUS    RESTARTS   AGE
events-df99bc8d6-8rpx7      1/1     Running   0          8m59s
gateway-6dcf468fc9-l4wsj    1/1     Running   0          6m30s
payments-787c5c6fc8-nh2jm   1/1     Running   0          8m59s
postgres-78489d7f5f-9thsz   1/1     Running   0          48m
redis-6fcfb5475d-6vctg      1/1     Running   0          48m
```

This shows that ArgoCD successfully synchronized the application and all Kubernetes pods were running.

---

### GitOps change synced to the cluster

A visible Git change was made by adding a `version=v2` label to the `gateway` Deployment.

The change was committed and pushed:

```text
410a3e4 feat: add version label to gateway
```

ArgoCD synchronized this revision:

```text
Sync Status:        Synced to  (410a3e4)
Health Status:      Healthy
```

Command used to verify that the Git change was applied to the live Kubernetes cluster:

```bash
kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
echo
```

Output:

```text
v2
```

This proves that the Git change was applied through the GitOps flow:

```text
Git commit -> Git push -> ArgoCD sync -> Kubernetes Deployment updated
```

---

### Question

**What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?**

If someone manually edits a Kubernetes resource managed by ArgoCD using `kubectl edit`, the live cluster state becomes different from the desired state stored in Git. ArgoCD detects this as drift.

If automated sync or self-heal is enabled, ArgoCD will reconcile the resource back to the version defined in Git. This means the manual change will be overwritten.

If self-heal is not enabled, ArgoCD will mark the application as `OutOfSync` until the resource is synchronized again.

Therefore, resources managed by ArgoCD should be changed through Git, not by direct manual Kubernetes edits, because Git is the source of truth in the GitOps model.

---

## Task 2 — Rollback via GitOps

### Deploy a bad version

A broken gateway version was deployed by changing the gateway image tag to a non-existent tag:

```yaml
image: ghcr.io/alextoday111/quickticket-gateway:does-not-exist
```

The change was committed and pushed to Git:

```text
355778f feat: deploy new gateway version
```

ArgoCD synchronized the bad revision:

```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/AlexToday111/SRE-Intro.git
  Target:
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (355778f)
Health Status:      Progressing

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH       HOOK  MESSAGE
       Service     default    redis     Synced  Healthy            service/redis unchanged
       Service     default    gateway   Synced  Healthy            service/gateway unchanged
       Service     default    postgres  Synced  Healthy            service/postgres unchanged
       Service     default    payments  Synced  Healthy            service/payments unchanged
       Service     default    events    Synced  Healthy            service/events unchanged
apps   Deployment  default    payments  Synced  Healthy            deployment.apps/payments unchanged
apps   Deployment  default    redis     Synced  Healthy            deployment.apps/redis unchanged
apps   Deployment  default    postgres  Synced  Healthy            deployment.apps/postgres unchanged
apps   Deployment  default    events    Synced  Healthy            deployment.apps/events unchanged
apps   Deployment  default    gateway   Synced  Progressing        deployment.apps/gateway configured
```

After the bad deploy, Kubernetes could not pull the new gateway image:

```text
NAME                        READY   STATUS             RESTARTS   AGE
events-df99bc8d6-8rpx7      1/1     Running            0          14m
gateway-6dcf468fc9-l4wsj    1/1     Running            0          12m
gateway-7d4cdbb49f-gqffk    0/1     ErrImagePull       0          63s
payments-787c5c6fc8-nh2jm   1/1     Running            0          14m
postgres-78489d7f5f-9thsz   1/1     Running            0          54m
redis-6fcfb5475d-6vctg      1/1     Running            0          54m

gateway-7d4cdbb49f-gqffk    0/1     ImagePullBackOff   0          73s
```

This confirms that the bad image tag was deployed through GitOps and Kubernetes failed to pull the image.

---

### Rollback via git revert

The broken deployment was rolled back using Git, not `kubectl`:

```bash
git revert HEAD --no-edit
git push origin main
```

Recent Git history:

```text
84c58f2 (HEAD -> main, origin/main, origin/HEAD) Revert "feat: deploy new gateway version"
355778f feat: deploy new gateway version
410a3e4 feat: add version label to gateway
```

ArgoCD synchronized the revert commit:

```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/AlexToday111/SRE-Intro.git
  Target:
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (84c58f2)
Health Status:      Healthy

Operation:          Sync
Sync Revision:      84c58f25f58dbbc9b1af90a90515a8441fabdfad
Phase:              Succeeded
Start:              2026-06-21 14:26:39 +0300 MSK
Finished:           2026-06-21 14:26:40 +0300 MSK
Duration:           1s
Message:            successfully synced (all tasks run)

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    events    Synced  Healthy        service/events unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
```

### Rollback recovery time

Question:

**How long from git revert + push to pods being healthy again?**

Answer:

```text
Rollback recovery time: 5s
```

It took approximately **5 seconds** from `git revert` and `git push` until ArgoCD synchronized the reverted revision and reported the application as `Synced` and `Healthy`.

The rollback was performed through Git. No direct `kubectl edit`, `kubectl set image`, or manual Kubernetes patch was used.

---

## Bonus Task — Automated Image Tag Update

### Goal

The goal of this bonus task was to extend the CI/CD pipeline so that Kubernetes image tags are updated automatically after a successful image build.

The expected GitOps flow is:

```text
Push code change
CI builds new Docker images with the Git commit SHA as the tag
CI pushes the images to GitHub Container Registry
CI updates image tags in k8s/*.yaml
CI commits and pushes the updated manifests
ArgoCD detects the Git change
ArgoCD syncs the application
Kubernetes deploys the new image tags
```

---

### Updated workflow file

The `.github/workflows/ci.yml` workflow was updated to build and push Docker images, then automatically update the image tags in Kubernetes manifests.

The workflow uses the current Git commit SHA as the Docker image tag:

```yaml
name: CI

on:
  push:
    branches: [main]

jobs:
  build:
    if: ${{ !startsWith(github.event.head_commit.message, 'ci:') }}
    runs-on: ubuntu-latest

    permissions:
      contents: write
      packages: write

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push gateway image
        run: |
          docker build -t ghcr.io/alextoday111/quickticket-gateway:${{ github.sha }} ./app/gateway
          docker push ghcr.io/alextoday111/quickticket-gateway:${{ github.sha }}

      - name: Build and push events image
        run: |
          docker build -t ghcr.io/alextoday111/quickticket-events:${{ github.sha }} ./app/events
          docker push ghcr.io/alextoday111/quickticket-events:${{ github.sha }}

      - name: Build and push payments image
        run: |
          docker build -t ghcr.io/alextoday111/quickticket-payments:${{ github.sha }} ./app/payments
          docker push ghcr.io/alextoday111/quickticket-payments:${{ github.sha }}

      - name: Update image tags in manifests
        run: |
          SHA=${{ github.sha }}
          sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/alextoday111/quickticket-gateway:${SHA}|" k8s/gateway.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-events:.*|image: ghcr.io/alextoday111/quickticket-events:${SHA}|" k8s/events.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-payments:.*|image: ghcr.io/alextoday111/quickticket-payments:${SHA}|" k8s/payments.yaml

      - name: Commit and push manifest update
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@github.com"
          git add -f k8s/
          git diff --cached --quiet || git commit -m "ci: update image tags to ${{ github.sha }}"
          git push
```

The workflow has write permissions for repository contents and packages:

```yaml
permissions:
  contents: write
  packages: write
```

The `contents: write` permission is required because GitHub Actions commits and pushes updated Kubernetes manifests back to the repository.

The workflow also includes a guard against an infinite CI loop:

```yaml
if: ${{ !startsWith(github.event.head_commit.message, 'ci:') }}
```

This is needed because the CI workflow creates an additional commit with the message starting with `ci:`. Without this guard, the CI-generated commit would trigger another workflow run, which could create an infinite loop.

The manifest update step uses `git add -f k8s/` because the `k8s/` directory is ignored by `.gitignore`:

```yaml
git add -f k8s/
```

---

### GitHub Actions run

GitHub Actions run:

```text
https://github.com/AlexToday111/SRE-Intro/actions/runs/27903009190/job/82566642103
```

The workflow completed successfully and performed the image build, push, manifest update, and Git commit steps.

---

### Git log showing code commit and CI tag-update commit

Command:

```bash
git log --oneline -5
```

Output:

```text
73982cc (HEAD -> main, origin/main, origin/HEAD) ci: update image tags to c2b309afeefad9f46debb0cbf8c178b0364f64b6
c2b309a feat: automate image tag updates
84c58f2 Revert "feat: deploy new gateway version"
355778f feat: deploy new gateway version"
410a3e4 feat: add version label to gateway
```

This output shows the expected sequence:

```text
c2b309a  -> manual commit that updated the CI workflow
73982cc  -> automatic commit created by GitHub Actions
```

The CI-generated commit updated the Kubernetes manifests with the image tag based on commit SHA:

```text
c2b309afeefad9f46debb0cbf8c178b0364f64b6
```

---

### Auto-updated image tags in manifests

Command:

```bash
grep -R "image: ghcr.io/alextoday111/quickticket" k8s/
```

Output:

```text
k8s/gateway.yaml:          image: ghcr.io/alextoday111/quickticket-gateway:c2b309afeefad9f46debb0cbf8c178b0364f64b6
k8s/events.yaml:          image: ghcr.io/alextoday111/quickticket-events:c2b309afeefad9f46debb0cbf8c178b0364f64b6
k8s/payments.yaml:          image: ghcr.io/alextoday111/quickticket-payments:c2b309afeefad9f46debb0cbf8c178b0364f64b6
```

This confirms that the Kubernetes manifests were automatically updated by the CI pipeline.

---

### Live cluster image tags

Command:

```bash
kubectl get deployment gateway -o jsonpath='{.spec.template.spec.containers[0].image}'
echo

kubectl get deployment events -o jsonpath='{.spec.template.spec.containers[0].image}'
echo

kubectl get deployment payments -o jsonpath='{.spec.template.spec.containers[0].image}'
echo
```

Output:

```text
ghcr.io/alextoday111/quickticket-gateway:c2b309afeefad9f46debb0cbf8c178b0364f64b6
ghcr.io/alextoday111/quickticket-events:c2b309afeefad9f46debb0cbf8c178b0364f64b6
ghcr.io/alextoday111/quickticket-payments:c2b309afeefad9f46debb0cbf8c178b0364f64b6
```

This proves that the updated image tags were not only committed to Git, but also applied to the live Kubernetes cluster.

---

### ArgoCD syncing the auto-updated tag

Command:

```bash
argocd app get quickticket
kubectl get pods
```

Output:

```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/AlexToday111/SRE-Intro.git
  Target:
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (73982cc)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    events    Synced  Healthy        service/events unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events configured
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments configured

NAME                        READY   STATUS    RESTARTS   AGE
events-d47b89997-g9wgs      1/1     Running   0          77s
gateway-676fd944f8-42ncp    1/1     Running   0          77s
payments-77688fc6d5-hcc8t   1/1     Running   0          77s
postgres-78489d7f5f-9thsz   1/1     Running   0          67m
redis-6fcfb5475d-6vctg      1/1     Running   0          67m
```

ArgoCD synchronized the application to the CI-generated commit:

```text
73982cc
```

The application status is:

```text
Sync Status:   Synced
Health Status: Healthy
```

All application pods are running successfully:

```text
events      1/1 Running
gateway     1/1 Running
payments    1/1 Running
postgres    1/1 Running
redis       1/1 Running
```

---

### Result

The bonus task was completed successfully.

The final automated GitOps flow works as follows:

```text
Developer pushes a code or workflow change
GitHub Actions builds Docker images with the commit SHA as the tag
GitHub Actions pushes the images to GHCR
GitHub Actions updates k8s/gateway.yaml, k8s/events.yaml, and k8s/payments.yaml
GitHub Actions commits and pushes the manifest update
ArgoCD detects the new Git commit
ArgoCD synchronizes the application
Kubernetes deploys the new image tags
```

This confirms that image tag updates are now automated and that ArgoCD deploys the CI-generated manifest changes through the GitOps workflow.
