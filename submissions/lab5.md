# Lab 5 — CI/CD & GitOps

## Work status

I prepared the workflow, registry manifests, and ArgoCD Application file. I installed ArgoCD in my existing k3d cluster and checked that its components were ready. I have not pushed these changes to GitHub yet. The Actions run, registry upload, GitOps sync, and rollback still need a live test. I do not have results for those steps yet.

The full Windows command guide is in [lab5-lab6-run-guide.md](lab5-lab6-run-guide.md). It includes the commands to collect the missing evidence.

## Task 1 — CI Pipeline + ArgoCD Setup

### 5.1: Create the CI workflow

I added [ci.yml](../.github/workflows/ci.yml). It builds the gateway, events, and payments images and pushes them to GHCR. Each tag uses the full commit SHA. The registry owner is changed to lowercase, so the image names start with `ghcr.io/nurkhab-ib/`.

The workflow runs on pushes to `main` that change application code or the workflow. I also added a manual trigger. Manifest-only commits do not rebuild images. This matters during the rollback exercise: CI must not replace the bad tag before I can observe it.

The workflow has `packages: write` to publish images and `contents: write` to update manifests. It skips commits whose message starts with `ci:`. GitHub's default token also prevents a pushed CI commit from starting another workflow run.

**Live result:** Not run. No green Actions run is available yet.

**Local validation:** `actionlint` v1.7.7 checked the workflow with exit code 0 and no errors. This validates the workflow file; it does not replace a real Actions run.

### 5.2: Verify images are pushed

The expected package names are:

```text
quickticket-gateway
quickticket-events
quickticket-payments
```

These are expected names, not command output. After a successful run, collect the real output:

```powershell
gh api 'user/packages?package_type=container' --jq '.[].name'
```

**Live result:** Not run. Images have not been published by this workflow yet.

### 5.3: Update K8s manifests to use registry images

All three application Deployment files now use GHCR image names, `imagePullPolicy: Always`, and `imagePullSecrets: [{name: ghcr-secret}]`.

The current SHA is only a starting value. It is not a tested image tag. The first successful CI run will replace it with the SHA that CI actually built. Wait for that update before creating the ArgoCD Application.

The pull secret must be created in the `default` namespace with a classic PAT that has `read:packages`. The token must stay out of Git and the report.

**Local check:** Kubernetes accepted all three manifest files in a client dry run. This checks the files, but does not prove that their images can be pulled.

### 5.4: Install ArgoCD

I installed ArgoCD v3.3.2 in the `argocd` namespace with server-side apply. The version is fixed so the setup can be repeated.

```powershell
kubectl create namespace argocd
kubectl apply --server-side -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/v3.3.2/manifests/install.yaml
kubectl get deployments -n argocd
```

Actual output, 27 September 2026:

```text
NAME                               READY   UP-TO-DATE   AVAILABLE   AGE
argocd-applicationset-controller   1/1     1            1           9m16s
argocd-dex-server                  1/1     1            1           9m16s
argocd-notifications-controller    1/1     1            1           9m16s
argocd-redis                       1/1     1            1           9m16s
argocd-repo-server                 1/1     1            1           9m16s
argocd-server                      1/1     1            1           9m16s
```

```text
NAME                            READY   AGE
argocd-application-controller   1/1     9m16s
```

Raw output: [deployments](evidence/lab5/argocd-deployments.txt), [controller](evidence/lab5/argocd-controller.txt).

### 5.5: Create an ArgoCD Application

The prepared file is [argocd/quickticket.yaml](../argocd/quickticket.yaml). It watches the `main` branch and the `k8s` directory in my fork. It deploys to the `default` namespace. It reads only the top-level manifests, so it does not include the old Helm chart inside `k8s/chart`.

Automatic sync and self-healing are enabled. Automatic pruning is off to avoid deleting older lab resources during the first sync.

**Local check:** The Application passed `kubectl apply --dry-run=client`. I did not create it yet because the remote branch does not contain the new manifests and built images.

### 5.6: Verify the GitOps loop

The live check is to add `version: "v2"` under the gateway Deployment's `metadata.labels`, commit and push it, then wait for ArgoCD. Check:

```powershell
argocd app get quickticket
kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
```

**Expected result:** `Synced`, `Healthy`, and `v2`. This result is not measured yet.

### 5.7: Proof of work

| Required evidence | Current status |
|---|---|
| Green GitHub Actions run link | Pending user push and CI run |
| Package API output with three images | Pending successful CI run |
| `argocd app get quickticket`: Synced + Healthy | Pending registry setup and Application creation |
| Git label change visible in cluster | Pending GitOps test |
| Answer about manual edits | Answer below |

**What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?**

ArgoCD compares the live resource with Git. A change to a managed field can make the app OutOfSync. My Application has self-healing enabled, so ArgoCD restores the Git value. With automatic sync alone and self-healing off, a manual edit does not normally trigger an automatic repair by itself. A later sync can still replace it. The lasting fix should be committed to Git.

## Task 2 — Rollback via GitOps

### 5.8: Deploy a bad version

The planned test changes only the gateway tag to `does-not-exist`. A manifest-only push does not trigger a build, so CI will not hide this failure by updating the tag again.

Wait for ArgoCD to sync and for Kubernetes to fail the image pull. The app can first show Progressing. It can become Degraded after the Deployment progress deadline, which is normally 600 seconds. Keep the old healthy pod running; do not delete it to create an outage.

**Live evidence:** Not run. No Degraded or ImagePullBackOff output is claimed.

### 5.9: Rollback via git revert

Revert the specific bad commit, then push. Record the time before `git revert`, and stop the timer only after ArgoCD is Synced and Healthy and the gateway rollout is complete. This avoids calling the rollback complete while only an old pod is still serving requests.

Required output to add after the test:

```powershell
git log --oneline -3
argocd app get quickticket
kubectl get pods
```

**How long from `git revert` + push to pods being healthy again?**

I have not measured this yet. The command guide includes a timer. I will report the measured time, not an estimate.

## Bonus Task — Automated Image Tag Update

The full workflow is in [ci.yml](../.github/workflows/ci.yml). After all images are pushed, it fetches `main`, updates the three image tags, and creates a `ci: update image tags to <SHA>` commit. The next ArgoCD sync uses those tags.

The relevant workflow steps are:

```yaml
- name: Update image tags in manifests
  run: |
    git fetch origin main
    git checkout -B main origin/main
    for service in gateway events payments; do
      sed -i -E "s|image: .*quickticket-$service:.*|image: ghcr.io/$OWNER/quickticket-$service:$GITHUB_SHA|" "k8s/$service.yaml"
    done
- name: Commit and push manifest update
  run: |
    git config user.name "github-actions[bot]"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
    git add k8s/gateway.yaml k8s/events.yaml k8s/payments.yaml
    if ! git diff --cached --quiet; then
      git commit -m "ci: update image tags to $GITHUB_SHA"
      git push origin HEAD:main
    fi
```

The workflow does not force-push. If another push wins the race before the final push, CI fails safely and can be rerun. Avoid other pushes during this exercise.

**Prepared:** Automatic update, commit step, and loop prevention.

**Still to verify:** A code commit followed by a CI tag-update commit, and ArgoCD deploying that tag without a manual sync. Do not use `argocd app sync` during this bonus check.
