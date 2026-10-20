# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

### 5.1 — CI workflow

Created `.github/workflows/ci.yml`: triggers on push to `main`, logs into `ghcr.io` with `secrets.GITHUB_TOKEN`, then builds and pushes all 3 service images tagged with `${{ github.sha }}`. Image owner is lower-cased at runtime (`${GITHUB_REPOSITORY_OWNER,,}`) since ghcr.io rejects mixed-case paths and the GitHub username (`G-0-rG`) isn't already lowercase.

Sanity-checked the same build steps locally (mirrors what CI's `docker build` will do) before trusting the workflow:
```bash
docker build -q -t quickticket-gateway:ci-test ./app/gateway
docker build -q -t quickticket-events:ci-test ./app/events
docker build -q -t quickticket-payments:ci-test ./app/payments
```
All three built cleanly.

**Not yet done — needs an actual push to `main` on GitHub:**
- Link to the GitHub Actions run
- `gh api user/packages?package_type=container` output (5.2)

### 5.3 — K8s manifests updated for registry images

`k8s/gateway.yaml`, `k8s/events.yaml`, `k8s/payments.yaml` — each switched from the local-image pattern to:
```yaml
spec:
  imagePullSecrets:
    - name: ghcr-secret
  containers:
    - name: <service>
      image: ghcr.io/g-0-rg/quickticket-<service>:v1   # placeholder — see note below
      imagePullPolicy: Always
```

> **Note:** the tag is still the placeholder `v1`, not a real commit SHA — per 5.3 the tag should come from an actual CI run, which hasn't happened yet (no push to `main`). Update this once the first Actions run completes.

**Not yet done:**
- `kubectl create secret docker-registry ghcr-secret ...` — needs a classic PAT (`read:packages` scope) that only the account owner can generate.

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

### 5.5 — ArgoCD Application — blocked

```bash
argocd app create quickticket \
  --repo https://github.com/G-0-rG/SRE-Intro.git \
  --path k8s \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace default \
  --sync-policy automated
```
```
{"level":"fatal","msg":"...InvalidSpecError: Unable to generate manifests in k8s: ...k8s: app path does not exist","time":"..."}
```

**Root cause:** ArgoCD reads from the Git remote, not the local working tree. Checked the fork on GitHub directly:
```bash
gh api repos/G-0-rG/SRE-Intro/contents/k8s?ref=main
# → 404 Not Found
```
`k8s/` doesn't exist on `main` at all yet — `main` never got `feature/lab1` or `feature/lab4` merged into it (confirmed earlier: those branches sit ahead of `main`, not merged). So there's nothing on the remote `main` for ArgoCD (or the CI workflow, which also only triggers on push to `main`) to act on.

**Blocked on (all require GitHub actions only the repo owner can do):**
1. Merge `feature/lab1` → `main` and `feature/lab4` → `main` (brings `k8s/` and prior lab work onto `main`)
2. Push/merge `feature/lab5` (this branch, with `ci.yml` + updated manifests) → `main`
3. Once CI runs on `main` and pushes real images, re-run `argocd app create` and record `argocd app get quickticket` output
4. 5.6 GitOps-loop verification (edit → push → ArgoCD sync) — depends on the Application existing first

### 5.7 — Written answer

**What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?**
The edit applies immediately (kubectl talks straight to the API server, ArgoCD doesn't gate writes), but it only lasts until ArgoCD's next reconciliation pass. Because the Application is created with `--sync-policy automated`, ArgoCD continuously diffs live cluster state against the Git-declared manifests; on the next sync (or immediately, if `selfHeal` is enabled — the default for automated sync since ArgoCD v1.5) it detects the drift and overwrites the manual change to match Git. Git stays the single source of truth: a `kubectl edit` is treated as an unintended drift to be corrected, not a valid change, unless it's echoed back into the repo.

---

## Task 2 — Rollback via GitOps

**Blocked the same way as 5.5:** the actual task (`git revert` → push → ArgoCD auto-syncs the fix) needs a live ArgoCD Application, which needs `k8s/` to exist on `main`, which needs `feature/lab1`/`feature/lab4` merged first (see Task 1). Nothing new to unblock here beyond what's already listed there.

**What I did instead — a local rehearsal of the underlying failure/recovery mechanic**, directly against the cluster via `kubectl` (bypassing Git and ArgoCD entirely, so this does *not* count as 5.8/5.9's actual deliverable — just a sanity check that the failure mode behaves as expected before the real GitOps version is recorded):

```bash
kubectl set image deployment/gateway gateway=quickticket-gateway:does-not-exist
kubectl get pods -l app=gateway
```
```
NAME                       READY   STATUS              RESTARTS   AGE
gateway-689d8d9d88-zctmj   0/1     ErrImageNeverPull    0          8s
gateway-7cd55d8774-s9cns   1/1     Running              0          11m
```
```bash
kubectl get events --field-selector involvedObject.name=gateway-689d8d9d88-zctmj
```
```
LAST SEEN   TYPE      REASON              OBJECT                         MESSAGE
25s         Normal    Scheduled           pod/gateway-689d8d9d88-zctmj   Successfully assigned default/gateway-689d8d9d88-zctmj to k3d-quickticket-server-0
12s         Warning   ErrImageNeverPull   pod/gateway-689d8d9d88-zctmj   Container image "quickticket-gateway:does-not-exist" is not present with pull policy of Never
12s         Warning   Failed              pod/gateway-689d8d9d88-zctmj   Error: ErrImageNeverPull
```

Note the Deployment's default `RollingUpdate` strategy kept the **old, healthy pod running** the whole time (`maxUnavailable: 25%` means it won't tear down the last good replica until a new one is confirmed ready) — so `gateway` itself never actually went down; only the *new* ReplicaSet's pod sat in a failed state. This is a real difference from the lab's expected "Degraded" scenario: `argocd app get` reports application-level health (would show `Degraded` because a subset of desired pods is unhealthy), whereas from the Service's point of view traffic kept flowing to the surviving old pod the entire time.

```bash
kubectl set image deployment/gateway gateway=quickticket-gateway:v1
kubectl rollout status deployment/gateway --timeout=60s
```
```
deployment "gateway" successfully rolled out
```
Bad ReplicaSet's pod terminated, back to a single healthy `1/1 Running` pod. Immediate — no propagation delay since this bypassed Git/ArgoCD polling entirely (direct API call).

**Still needed for the real submission** (once Task 1's Application exists):
- `argocd app get` showing `Degraded` after a bad deploy pushed via Git
- `git log --oneline -3` showing deploy + revert commits
- `argocd app get` showing `Healthy` after `git revert` + push
- Real recovery-time measurement (this will be materially slower than the kubectl rehearsal above — bounded by ArgoCD's ~3 min poll interval unless `argocd app sync` is triggered manually)

### Answer (preliminary — based on rehearsal, not the real GitOps flow)
**How long from `git revert` + push to pods being healthy again?**
Can't give the real number yet — depends on ArgoCD's poll interval (~3 min by default) unless synced manually (`argocd app sync`, near-instant once triggered). The kubectl-only rehearsal above shows the *lower bound*: the underlying Kubernetes reconciliation itself (new pod scheduled → pulled/started → rollout confirmed) is fast, well under 30s here. In the real Git-mediated flow, that Kubernetes-level time is the same; the dominant factor is entirely how long it takes ArgoCD to *notice* the revert commit — so the honest answer is "K8s rollout time + ArgoCD's detection latency," and only the second half is currently unmeasured.

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

**Infinite-loop guard:** added a job-level `if` (not just the trigger-level one the lab's warning shows, since this workflow only has a single job):
```yaml
jobs:
  build:
    if: "!startsWith(github.event.head_commit.message, 'ci:')"
```
Also added `contents: write` to `permissions:` — the default `GITHUB_TOKEN` permissions don't include repo write access, and without it `git push` from the workflow would fail with a 403.

**Verified locally** (since I can't trigger the real workflow without a push to `main`): copied `k8s/gateway.yaml` to a scratch dir and ran the exact `sed` pattern against it —
```bash
sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/g-0-rg/quickticket-gateway:abc1234|" gateway.yaml
grep "image:" gateway.yaml
# → image: ghcr.io/g-0-rg/quickticket-gateway:abc1234
```
Confirms the regex correctly matches our actual manifest format and replaces only the tag.

**Still needed for the real submission** (same root blocker as Tasks 1 and 2 — `main` needs `feature/lab1`/`feature/lab4`/`feature/lab5` merged before any of this can run for real):
- Git log showing: code commit → separate CI tag-update commit (proves the loop guard works and doesn't fire twice)
- Confirmation the CI-authored commit does *not* re-trigger the workflow
- `argocd app get` / `kubectl get pods` showing the auto-updated tag synced without manual intervention
