# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

### CI pipeline

Created `.github/workflows/ci.yml` with a GitHub Actions workflow that runs on pushes to `main`, builds Docker images for all three QuickTicket services, and pushes them to GitHub Container Registry.

GitHub Actions run:

https://github.com/Troshkins/SRE-Intro/actions/runs/35992389015

The run completed successfully with all image build and push steps passing.

### Container images in GHCR

The following container packages were successfully pushed and verified in the GitHub Packages tab:

```text
quickticket-payments
quickticket-events
quickticket-gateway
```

The local `gh` CLI token did not contain the `read:packages` scope, so package availability was verified through the GitHub Packages UI instead.

The Kubernetes manifests were updated from local images to GHCR images and configured with `imagePullPolicy: Always`. An `imagePullSecret` named `ghcr-secret` was created so the k3d cluster could pull the images from GHCR.

### ArgoCD setup

ArgoCD was installed into the `argocd` namespace. The current ArgoCD manifest required server-side apply because the `applicationsets.argoproj.io` CRD exceeded the annotation size limit with regular client-side apply.

After installation, all ArgoCD components were running and the server became available:

```text
argocd-application-controller-0                     1/1   Running
argocd-applicationset-controller-7f95b9cd7c-hjw4g   1/1   Running
argocd-dex-server-8666767789-zxjfb                  1/1   Running
argocd-notifications-controller-797f48b4-h46r7      1/1   Running
argocd-redis-6fd5864464-4ckdd                       1/1   Running
argocd-repo-server-c4977564f-ll7th                  1/1   Running
argocd-server-59bd8b5c4-ph6fk                       1/1   Running
```

Created the `quickticket` ArgoCD Application pointing to the `k8s/` directory in the repository.

Healthy application state:

```text
$ argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
Source:
- Repo:             https://github.com/Troshkins/SRE-Intro.git
  Path:             k8s
Sync Policy:        Automated
Sync Status:        Synced to  (3df15f1)
Health Status:      Healthy
```

All QuickTicket resources were `Synced` and `Healthy`.

### GitOps loop verification

Added a visible version label to the gateway Deployment and pushed the change to Git:

```yaml
metadata:
  name: gateway
  labels:
    version: "v2"
```

ArgoCD synchronized revision `2dd84ff101a431f8103656dd403cbc2811e2e1c3` successfully:

```text
Sync Status:        Synced to  (2dd84ff)
Health Status:      Healthy

Operation:          Sync
Sync Revision:      2dd84ff101a431f8103656dd403cbc2811e2e1c3
Phase:              Succeeded
Duration:           1s
Message:            successfully synced (all tasks run)
```

The change was visible in the live Kubernetes Deployment:

```text
$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2
```

This confirmed the GitOps flow from Git to ArgoCD and then to Kubernetes.

### What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?

A manual `kubectl edit` changes the live Kubernetes state but not the desired state stored in Git. ArgoCD detects this drift and marks the Application or resource as `OutOfSync`.

In this lab automated sync was enabled, but self-heal was not explicitly enabled. Therefore, a manual live-cluster change may remain until another sync is triggered, for example by a Git change or a manual `argocd app sync`. If ArgoCD self-heal is enabled, ArgoCD automatically restores the resource to the state defined in Git.

Git remains the source of truth.

---

## Task 2 — Rollback via GitOps

### Bad deployment

To simulate a failed release, the gateway image tag was changed to a non-existent tag:

```text
image: ghcr.io/troshkins/quickticket-gateway:random
```

The change was committed as:

```text
dd8001f feat: deploy new gateway version
```

ArgoCD successfully synchronized the Git state, but the application could not become healthy:

```text
$ argocd app get quickticket
Sync Status:        Synced to  (dd8001f)
Health Status:      Progressing

apps   Deployment  default  gateway  Synced  Progressing
```

Kubernetes showed the new gateway pod failing to pull the invalid image:

```text
$ kubectl get pods
NAME                        READY   STATUS             RESTARTS   AGE
events-7f56d7d47f-8tdcc     1/1     Running            0          91m
gateway-5f8c9b6c9d-bnjrs    1/1     Running            0          91m
gateway-746d7d7d8c-xhsvx    0/1     ImagePullBackOff   0          28s
payments-6b4fdd979-qzb4z    1/1     Running            0          91m
postgres-78489d7f5f-z854d   1/1     Running            0          47h
redis-6fcfb5475d-7t5f4      1/1     Running            0          47h
```

This also demonstrates the difference between synchronization and health: Git and Kubernetes contained the same desired image tag, so the application was `Synced`, but the workload was not healthy because the image did not exist.

### Rollback with `git revert`

The bad deployment was rolled back using Git:

```text
$ git revert HEAD --no-edit
[feature/lab5 90277bd] Revert "feat: deploy new gateway version"
```

Relevant Git history:

```text
90277bd Revert "feat: deploy new gateway version"
dd8001f feat: deploy new gateway version
2dd84ff feat: add version label to gateway
```

ArgoCD synchronized the revert successfully:

```text
Sync Status:        Synced to  (90277bd)
Health Status:      Healthy

Operation:          Sync
Sync Revision:      90277bd75d262b25ad0e1d729d83fd64ee78e309
Phase:              Succeeded
Start:              2026-09-24 15:11:08 +0300 MSK
Finished:           2026-09-24 15:11:09 +0300 MSK
Duration:           1s
Message:            successfully synced (all tasks run)
```

After the rollback all pods were healthy again:

```text
$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
events-7f56d7d47f-8tdcc     1/1     Running   0          94m
gateway-5f8c9b6c9d-bnjrs    1/1     Running   0          94m
payments-6b4fdd979-qzb4z    1/1     Running   0          94m
postgres-78489d7f5f-z854d   1/1     Running   0          2d
redis-6fcfb5475d-7t5f4      1/1     Running   0          2d
```

### Recovery time

The revert commit was created at `15:10:21`, and ArgoCD reported the application healthy after the successful sync completed at `15:11:09`.

Observed end-to-end recovery time was approximately **48 seconds** from the revert commit timestamp to a healthy application. The ArgoCD synchronization itself took **1 second**.

---

## Bonus Task — Automated Image Tag Update

The CI pipeline was extended so that after building and pushing the three images it automatically updates the image tags in the Kubernetes manifests and commits the changes back to Git.

Relevant workflow configuration:

```yaml
jobs:
  build:
    if: "!startsWith(github.event.head_commit.message, 'ci:')"

    permissions:
      contents: write
      packages: write

    steps:
      # build and push steps omitted

      - name: Update image tags in manifests
        run: |
          SHA=${{ github.sha }}
          sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/troshkins/quickticket-gateway:${SHA}|" k8s/gateway.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-events:.*|image: ghcr.io/troshkins/quickticket-events:${SHA}|" k8s/events.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-payments:.*|image: ghcr.io/troshkins/quickticket-payments:${SHA}|" k8s/payments.yaml

      - name: Commit and push manifest update
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@github.com"
          git add k8s/
          git diff --cached --quiet || git commit -m "ci: update image tags to ${{ github.sha }}"
          git push
```

`contents: write` allows the workflow to push the manifest update back to the repository, while `packages: write` allows it to push images to GHCR. The `if` condition prevents CI-generated commits beginning with `ci:` from creating an infinite build loop.

Bonus CI run:

https://github.com/Troshkins/SRE-Intro/actions/runs/36000221863

The run completed successfully, including:

```text
Build and push gateway image        success
Build and push events image         success
Build and push payments image       success
Update image tags in manifests      success
Commit and push manifest update     success
```

The resulting Git history shows the original code/workflow commit followed by the CI-generated manifest commit:

```text
e4d3138 ci: update image tags to 462082928e8bd983a3ae2f414d7c8f00540df39a
4620829 feat: automate image tag updates
90277bd Revert "feat: deploy new gateway version"
dd8001f feat: deploy new gateway version
2dd84ff feat: add version label to gateway
```

The CI-generated commit automatically updated all three manifest image tags to the SHA of the triggering commit:

```text
ghcr.io/troshkins/quickticket-gateway:462082928e8bd983a3ae2f414d7c8f00540df39a
ghcr.io/troshkins/quickticket-events:462082928e8bd983a3ae2f414d7c8f00540df39a
ghcr.io/troshkins/quickticket-payments:462082928e8bd983a3ae2f414d7c8f00540df39a
```

No manual ArgoCD sync was required for this bonus verification. ArgoCD detected the CI-generated Git change and synchronized it automatically:

```text
$ argocd app get quickticket
Sync Policy:        Automated
Sync Status:        Synced to  (e4d3138)
Health Status:      Healthy
```

The new workloads were running successfully:

```text
$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
events-f5dd5774d-tks5t      1/1     Running   0          3m17s
gateway-7b4c4c7ccf-x6p2s    1/1     Running   0          3m17s
payments-684d5666bd-z7ks8   1/1     Running   0          3m17s
postgres-78489d7f5f-z854d   1/1     Running   0          2d
redis-6fcfb5475d-7t5f4      1/1     Running   0          2d
```

This completed the fully automated GitOps loop:

```text
push code
→ CI builds images
→ CI pushes images to GHCR
→ CI updates Kubernetes manifests
→ CI commits the new image tags
→ ArgoCD detects the Git change
→ ArgoCD synchronizes Kubernetes
→ application returns Healthy
```

---

## Conclusion

Lab 5 successfully implemented CI/CD and GitOps for QuickTicket.

The lab demonstrated:

* GitHub Actions CI triggered by pushes to `main`
* building and publishing immutable SHA-tagged images to GHCR
* Kubernetes image pulls using `imagePullSecrets`
* ArgoCD installation and automated synchronization
* Git as the source of truth for Kubernetes state
* detection of unhealthy deployments despite a `Synced` state
* rollback through `git revert`
* automated image-tag updates from CI
* a complete automated GitOps deployment loop

All Task 1, Task 2, and Bonus Task requirements were completed.
