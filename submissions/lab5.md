# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD

**Workflow:** [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) — on push to `main` builds `gateway`, `events`, `payments` and pushes them to ghcr.io tagged with `${{ github.sha }}`.

**1. First green run:** https://github.com/Meliman1000-7/SRE-Intro/actions/runs/36210197464

**2. Pushed images** (`gh api user/packages?package_type=container`):
```
quickticket-gateway   public  https://github.com/users/Meliman1000-7/packages/container/package/quickticket-gateway
quickticket-events    public  https://github.com/users/Meliman1000-7/packages/container/package/quickticket-events
quickticket-payments  public  https://github.com/users/Meliman1000-7/packages/container/package/quickticket-payments
```

Manifests in `k8s/` switched to `ghcr.io/meliman1000-7/quickticket-<svc>:<sha>`, `imagePullPolicy: Always`, `imagePullSecrets: [ghcr-secret]`. ArgoCD installed from the stable manifests; the CLI was used in `--core` mode (talks to the cluster via kubeconfig, no admin password needed).

**3. `argocd app get quickticket`:**
```
Source:           https://github.com/Meliman1000-7/SRE-Intro.git, Path: k8s
Sync Policy:      Automated
Sync Status:      Synced to  (6d49423)
Health Status:    Healthy

KIND        NAME      STATUS  HEALTH
Deployment  events    Synced  Healthy
Deployment  payments  Synced  Healthy
Deployment  redis     Synced  Healthy
Deployment  gateway   Synced  Healthy
Deployment  postgres  Synced  Healthy
(+ 5 Services, all Synced/Healthy)
```

**4. Git change synced** — commit `08b91b4 feat: add version label to gateway` added `version: "v2"` to the gateway Deployment:
```
PUSH 02:44:27 UTC
LIVE 02:44:32 UTC
$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2
Sync Status: Synced to (08b91b4)   Health Status: Healthy
```

**5. What happens if someone runs `kubectl edit` on a resource managed by ArgoCD?**
The change applies, but ArgoCD sees live state ≠ Git and marks the app `OutOfSync`. Tested: `kubectl label deployment gateway version=manual` → app became `OutOfSync`, and the label stayed `manual` because `selfHeal` is off. It was reverted to `v2` on the next sync (manual `argocd app sync` or the next Git change). With `selfHeal: true` ArgoCD would revert the drift automatically within seconds. Either way the manual edit is temporary: Git is the source of truth.

## Task 2 — Rollback via GitOps

Commit `8b39f11` set the gateway image to `:does-not-exist`.

**`argocd app get` after the bad deploy:**
```
Sync Status:     Synced to  (8b39f11)
Health Status:   Degraded
Deployment  gateway   Synced  Degraded   deployment.apps/gateway configured
```
(`Progressing` for the first 10 min, then `Degraded` once `progressDeadlineSeconds: 600` expired.)

**`kubectl get pods`:**
```
gateway-5c47677bb6-knqxf   1/1     Running            0   6h13m
gateway-7668c85d46-c254j   0/1     ImagePullBackOff   0   6h9m
```
The old pod kept serving: the rolling update never removes the last healthy replica while the new one is not Ready.

**`git log --oneline -3`:**
```
5dda702 Revert "feat: deploy new gateway version"
8b39f11 feat: deploy new gateway version
725d3b2 ci: update image tags to 69b34c2d07fab6590f1f5240db56e8f640548355
```

**After revert:**
```
Sync Status:     Synced to  (5dda702)
Health Status:   Healthy
gateway-5c47677bb6-knqxf   1/1   Running   0   6h18m
```

**How long from `git revert` + push to pods healthy again?**
**304 s.** Push at 18:27:28 UTC, ArgoCD synced at 18:32:28, Healthy at 18:32:30. Almost all of it was waiting for ArgoCD's Git poll; the rollout itself took 2 s, since the old ReplicaSet was still running and only the broken one had to be scaled down. A GitHub webhook to ArgoCD would cut this to seconds.

Note: the first attempt of this task (commits `8dd50e4` / `4375951`) did not recover after the revert. The CI built amd64-only images, the k3d node is arm64 (Apple Silicon), and under emulation the old gateway pod started too slowly and was killed by its liveness probe (exit 137, `CrashLoopBackOff`). Multi-arch builds fixed it (see Bonus), then the task was repeated cleanly.

## Bonus — Automated Image Tag Update

Final workflow ([`ci.yml`](../.github/workflows/ci.yml)):
```yaml
on:
  push:
    branches: [main]
    paths-ignore: ['k8s/**', 'submissions/**']   # manifest-only commits need no rebuild

jobs:
  build:
    # Skip commits made by this workflow to avoid an infinite loop
    if: "!startsWith(github.event.head_commit.message, 'ci:')"
    runs-on: ubuntu-latest
    permissions:
      contents: write      # Needed to push the manifest update
      packages: write      # Needed to push to ghcr.io
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3   # ghcr.io, GITHUB_TOKEN
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - name: Build and push images
        run: |
          for svc in gateway events payments; do
            docker buildx build --platform linux/amd64,linux/arm64 \
              -t ghcr.io/meliman1000-7/quickticket-$svc:${{ github.sha }} \
              --push ./app/$svc
          done
      - name: Update image tags in manifests
        run: |
          for svc in gateway events payments; do
            sed -i "s|image: ghcr.io/.*/quickticket-$svc:.*|image: ghcr.io/meliman1000-7/quickticket-$svc:${{ github.sha }}|" k8s/$svc.yaml
          done
      - name: Commit and push manifest update
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@github.com"
          git add k8s/
          git diff --cached --quiet && exit 0
          git commit -m "ci: update image tags to ${{ github.sha }}"
          git pull --rebase origin main
          git push
```
Loop protection works on two levels: `ci:` commits are skipped by the `if`, and `paths-ignore` means manifest-only commits (the CI's own commit, rollbacks) never trigger a rebuild. The second level also stops CI from overwriting a hand-made manifest change such as the Task 2 bad tag.

**Git log: code commit → CI tag-update commit:**
```
725d3b2 github-actions: ci: update image tags to 69b34c2d07fab6590f1f5240db56e8f640548355
69b34c2 Meliman1000-7:  feat(ci): skip image builds for manifest-only commits
adfe63c github-actions: ci: update image tags to 89ea6d9526b53d57215da9c20f806b6b614e5119
89ea6d9 Meliman1000-7:  feat(ci): build multi-arch images and auto-update manifest tags
```
Runs: [36213533187](https://github.com/Meliman1000-7/SRE-Intro/actions/runs/36213533187), [36241061034](https://github.com/Meliman1000-7/SRE-Intro/actions/runs/36241061034). Neither `ci:` commit started a new run.

**ArgoCD synced the auto-updated tag with no manual action** (no `sync`, no `--refresh`):
```
12:09:47  push 69b34c2
12:13:30  CI commit 725d3b2 (images built, tags updated)
12:13:57  ArgoCD synced 725d3b2      ← argocd app history: 6  2026-09-26 15:13:57 +0300 (725d3b2)
12:14:28  Health: Healthy

$ kubectl get deploy -o custom-columns=NAME:.metadata.name,IMAGE:...
events     ghcr.io/meliman1000-7/quickticket-events:69b34c2d07fab6590f1f5240db56e8f640548355
gateway    ghcr.io/meliman1000-7/quickticket-gateway:69b34c2d07fab6590f1f5240db56e8f640548355
payments   ghcr.io/meliman1000-7/quickticket-payments:69b34c2d07fab6590f1f5240db56e8f640548355
```
Push to running new code took **~4.7 min**, mostly the multi-arch build (~3.5 min).
