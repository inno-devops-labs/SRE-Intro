# Lab 5 — CI/CD and GitOps

**Student:** Gleb Shvetsov

**GitHub:** `L10nff`

**Repository:** `https://github.com/L10nff/SRE-Intro`

**Working branch:** `feature/lab5`

## Task 1 — CI/CD Pipeline and GitOps Deployment

### 1. Repository baseline and Lab 4 dependency

Lab 5 was based on the current course `upstream/main`, not on the Lab 4 branch history. Only the five raw Kubernetes manifests required as the Argo CD source were restored from `feature/lab4`:

```text
k8s/events.yaml
k8s/gateway.yaml
k8s/payments.yaml
k8s/postgres.yaml
k8s/redis.yaml
```

The restored files matched their Lab 4 Git objects exactly, and no Helm chart or previous submission report was copied.

Repository state after creating the branch:

```text
feature/lab5
cf2ad63 fix(gitignore): unignore student deliverable paths
0  0
?? k8s/
```

### 2. Local environment and Kubernetes baseline

The active target was verified explicitly because Docker Desktop had temporarily selected another Kubernetes context.

```bash
kubectl config use-context k3d-quickticket
kubectl get nodes -o wide
```

```text
Switched to context "k3d-quickticket".
NAME                       STATUS   ROLES                  VERSION         INTERNAL-IP
k3d-quickticket-server-0   Ready    control-plane,master   v1.33.13+k3s2   172.22.0.2
```

Relevant tool versions:

```text
Docker Engine client: 29.6.1, server: 29.7.2
Docker Buildx: v0.36.1-desktop.1
k3d: v5.9.0
kubectl client: v1.33.13
Helm: v3.22.0
Host architecture: arm64
```

All three application Dockerfiles were built locally before enabling CI. Excerpt only:

```text
Validating service: gateway
#9 [5/5] COPY main.py .
#9 DONE 0.0s
Validating service: events
#9 [5/5] COPY main.py .
#9 DONE 0.1s
Validating service: payments
#9 [5/5] COPY main.py .
#9 DONE 0.0s
```

### 3. GitHub Actions workflow

The final `.github/workflows/ci.yml`:

- triggers on pushes to the fork's `main` branch;
- authenticates to `ghcr.io` with the repository-scoped `GITHUB_TOKEN`;
- grants `packages: write` and `contents: write`;
- configures QEMU and Buildx;
- builds `gateway`, `events`, and `payments` for `linux/amd64` and `linux/arm64`;
- tags every image with the immutable source commit SHA;
- contains the Bonus manifest-update step described later in this report.

Relevant final workflow fields:

```yaml
on:
  push:
    branches:
      - main

jobs:
  build:
    if: ${{ !startsWith(github.event.head_commit.message, 'ci:') }}
    permissions:
      contents: write
      packages: write
```

The workflow YAML and both embedded Bash scripts were validated before commit:

```text
YAML syntax: OK
Bash syntax OK: .../run-01.sh
Bash syntax OK: .../run-02.sh
Credential-pattern scan: clean
```

### 4. Successful CI build and GHCR publication

The first workflow run built and published all three application images.

```text
run_id: 36331065446
name: CI
event: push
head_branch: main
head_sha: 341ad1125433baf905650c9a61c39ad4f6920cda
status: completed
conclusion: success
created_at: 2026-09-27T15:51:08Z
run_started_at: 2026-09-27T15:51:08Z
updated_at: 2026-09-27T15:54:40Z
html_url: https://github.com/L10nff/SRE-Intro/actions/runs/36331065446
```

Job result:

```text
job_status: completed
job_conclusion: success
job_started_at: 2026-09-27T15:51:10Z
job_completed_at: 2026-09-27T15:54:39Z
Build and push multi-platform images | completed | success
```

Anonymous registry inspection succeeded, proving that the packages are pullable and contain both required architectures. Excerpt only; the platform and digest fields are shown:

```text
ghcr.io/l10nff/quickticket-gateway:341ad1125433baf905650c9a61c39ad4f6920cda
Digest: sha256:f2ba18a629be41cfcc13d8a0052406df896c45cbf63ce212bfd3e23612e033f3
Platform: linux/amd64
Platform: linux/arm64

ghcr.io/l10nff/quickticket-events:341ad1125433baf905650c9a61c39ad4f6920cda
Digest: sha256:98340009518050562287e64d011566ab3e7eef37a3b4b4882b2719cb1f097ca1
Platform: linux/amd64
Platform: linux/arm64

ghcr.io/l10nff/quickticket-payments:341ad1125433baf905650c9a61c39ad4f6920cda
Digest: sha256:5624696c0844789db34f95b6620079da9c0d341555d32191c4943603b7dd2ceb
Platform: linux/amd64
Platform: linux/arm64
```

The public package listing returned all three packages:

```text
quickticket-events | container | public | 2026-09-27T15:53:35Z
quickticket-gateway | container | public | 2026-09-27T15:52:30Z
quickticket-payments | container | public | 2026-09-27T15:54:34Z
```

### 5. Kubernetes registry configuration

The three application Deployments were changed from local images with `imagePullPolicy: Never` to SHA-tagged GHCR images with `imagePullPolicy: Always`. Every Deployment references `ghcr-secret` through `imagePullSecrets`.

The pull secret was created using a hidden-input classic PAT with only `read:packages`. The token value was never printed or stored in the repository.

```bash
kubectl get secret ghcr-secret -n default \
  -o custom-columns='NAME:.metadata.name,TYPE:.type,CREATED:.metadata.creationTimestamp'
```

```text
NAME          TYPE                             CREATED
ghcr-secret   kubernetes.io/dockerconfigjson   2026-09-27T16:04:14Z
```

Manifest validation:

```bash
kubectl apply --dry-run=client -f k8s
```

```text
deployment.apps/events configured (dry run)
service/events configured (dry run)
deployment.apps/gateway configured (dry run)
service/gateway configured (dry run)
deployment.apps/payments configured (dry run)
service/payments configured (dry run)
persistentvolumeclaim/postgres-data configured (dry run)
deployment.apps/postgres configured (dry run)
service/postgres configured (dry run)
deployment.apps/redis configured (dry run)
service/redis configured (dry run)
```

### 6. Argo CD installation

The current upstream `stable` manifest resolved to Argo CD `v3.5.3`. The inspected manifest was then downloaded through the immutable `v3.5.3` URL and its SHA-256 was verified before installation.

```text
Argo CD image: quay.io/argoproj/argocd:v3.5.3
Manifest SHA-256: 7efe2d6bbc03f63623640f1e4198f16c84009d510fb810ef71e56df1b7614ba9
Expected SHA-256: 7efe2d6bbc03f63623640f1e4198f16c84009d510fb810ef71e56df1b7614ba9
Actual SHA-256:   7efe2d6bbc03f63623640f1e4198f16c84009d510fb810ef71e56df1b7614ba9
```

The first client-side apply installed all normal resources but rejected the large ApplicationSet CRD because its last-applied annotation exceeded the Kubernetes annotation limit:

```text
The CustomResourceDefinition "applicationsets.argoproj.io" is invalid:
metadata.annotations: Too long: may not be more than 262144 bytes
```

The pinned standalone CRD was safely applied with server-side apply:

```bash
kubectl apply --server-side \
  --field-manager=argocd-lab5 \
  -f "$lab5_applicationset_crd"
```

```text
customresourcedefinition.apiextensions.k8s.io/applicationsets.argoproj.io serverside-applied
```

The final installation contained six ready Deployments and one ready StatefulSet. Excerpt only:

```text
deployment.apps/argocd-applicationset-controller   1/1
deployment.apps/argocd-dex-server                  1/1
deployment.apps/argocd-notifications-controller    1/1
deployment.apps/argocd-redis                       1/1
deployment.apps/argocd-repo-server                 1/1
deployment.apps/argocd-server                      1/1
statefulset.apps/argocd-application-controller     1/1
```

The native Apple Silicon CLI matched the server version:

```text
argocd: v3.5.3+c9c369e
Platform: darwin/arm64
argocd-server: v3.5.3
Platform: linux/arm64
```

### 7. Argo CD Application and initial synchronization

The Application was configured as follows:

```text
Name: quickticket
Project: default
Repository: https://github.com/L10nff/SRE-Intro.git
Target revision: main
Path: k8s
Destination server: https://kubernetes.default.svc
Destination namespace: default
Sync policy: Automated
```

Initial synchronization result:

```text
Sync Status: Synced to main (14b1652)
Health Status: Healthy
```

The initial sync operation started at `2026-09-27T16:30:08Z` and finished at `2026-09-27T16:30:09Z`. All five Deployments, five Services, and the PostgreSQL PVC were synchronized.

### 8. Visible GitOps change

A visible `version: "v2"` label was added to the Gateway Deployment in Git commit:

```text
80ebf3eaac97fa874ab7de46d5bebd3139427008
feat: add version label to gateway
```

The push completed at `2026-09-27T16:37:24Z`. No manual `argocd app sync`, `kubectl apply`, or `kubectl set image` was used.

Argo CD automatically reconciled the commit:

```text
sync: Synced
health: Healthy
revision: 80ebf3eaac97fa874ab7de46d5bebd3139427008
operation started: 2026-09-27T16:38:34Z
operation finished: 2026-09-27T16:38:40Z
live gateway version: v2
```

The operation began 70 seconds after the observed push completion and finished 76 seconds after it.

The CI run for this commit also succeeded:

```text
id: 36333921379
head_sha: 80ebf3eaac97fa874ab7de46d5bebd3139427008
status: completed
conclusion: success
job_started_at: 2026-09-27T16:37:28Z
job_completed_at: 2026-09-27T16:40:59Z
```

### 9. Manual-edit behavior

The Application uses automated sync but was not configured with `selfHeal`. A manual `kubectl edit` would therefore make the Application `OutOfSync`, but Argo CD would not necessarily revert that live-only drift immediately. The change would be overwritten by an explicit sync or by a later Git change that causes synchronization. With automated self-healing enabled, Argo CD would automatically restore the Git-declared state.

## Task 2 (Optional) — Failed Deployment and Git Revert

### 1. Healthy baseline

The experiment started from this confirmed state at `2026-09-27T16:43:13Z`:

```text
Git HEAD/origin/main: 80ebf3eaac97fa874ab7de46d5bebd3139427008
Argo CD: Synced / Healthy
Gateway ready replicas: 1
Gateway available replicas: 1
Gateway image: ghcr.io/l10nff/quickticket-gateway:341ad1125433baf905650c9a61c39ad4f6920cda
Gateway image ID: sha256:f2ba18a629be41cfcc13d8a0052406df896c45cbf63ce212bfd3e23612e033f3
Gateway restarts: 0
```

### 2. Intentional failure

Only the Gateway image tag was changed:

```diff
- image: ghcr.io/l10nff/quickticket-gateway:341ad1125433baf905650c9a61c39ad4f6920cda
+ image: ghcr.io/l10nff/quickticket-gateway:lab5-nonexistent
```

Failure commit and observed push times:

```text
commit: 7b6961ae6687704dee26abe02fa78a4836497eb5
message: test: deploy invalid gateway image
failure_push_start: 2026-09-27T16:49:40Z
failure_push_end: 2026-09-27T16:49:43Z
```

Argo CD selected the new revision, and the new Pod moved through the expected states:

```text
2026-09-27T16:52:11Z revision=7b6961a... health=Progressing pod=ContainerCreating
2026-09-27T16:52:16Z revision=7b6961a... health=Progressing pod=ErrImagePull
2026-09-27T16:52:27Z revision=7b6961a... health=Progressing pod=ImagePullBackOff
```

The image failure was first confirmed 164 seconds after the observed push completion.

Pod events identified the exact cause:

```text
Back-off pulling image "ghcr.io/l10nff/quickticket-gateway:lab5-nonexistent"
Error: ImagePullBackOff
Failed to pull image ... failed to resolve reference ... lab5-nonexistent: not found
Error: ErrImagePull
```

Container logs could not exist because the container never started:

```text
Error from server (BadRequest): container "gateway" in pod
"gateway-7dc55f845d-snv46" is waiting to start: trying and failing to pull image
```

At `2026-09-27T17:02:38Z`, Kubernetes reached its progress deadline and Argo CD reported the required degraded state:

```text
NAME          SYNC     HEALTH     REVISION
quickticket   Synced   Degraded   7b6961ae6687704dee26abe02fa78a4836497eb5

Available   | True  | MinimumReplicasAvailable
Progressing | False | ProgressDeadlineExceeded
ReplicaSet "gateway-7dc55f845d" has timed out progressing.
```

The previous Gateway Pod remained Ready during the failed RollingUpdate, so the Service retained an available replica while the replacement could not start.

### 3. Git-based rollback and recovery

The failure was reverted through Git, not through an imperative Kubernetes command:

```bash
git revert --no-edit 7b6961ae6687704dee26abe02fa78a4836497eb5
```

```text
commit: 3a3a95a6513a452151ee0f7d780244e300ef96f6
message: Revert "test: deploy invalid gateway image"
recovery_push_start: 2026-09-27T17:03:53Z
recovery_push_end: 2026-09-27T17:03:57Z
```

Argo CD recovery:

```text
operation_revision=3a3a95a6513a452151ee0f7d780244e300ef96f6
started_at=2026-09-27T17:06:21Z
finished_at=2026-09-27T17:06:22Z
phase=Succeeded
recovery_observed_utc=2026-09-27T17:06:23Z
```

The application returned to `Synced / Healthy`, and the endpoint was verified immediately afterwards:

```text
endpoint_recovery_observed_utc=2026-09-27T17:06:24Z
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
http_code=200
```

Calculated from the observed push completion at `17:03:57Z`:

- Argo CD was observed `Healthy` after 146 seconds.
- The Gateway `/health` endpoint returned HTTP 200 after 147 seconds.
- These are client-observed recovery times, not internal Kubernetes event timestamps.

## Bonus Task — Automatic Manifest Updates

### 1. Pipeline extension

The workflow was extended to update all three application image references after a successful multi-platform build, commit only the three manifests, and push the generated commit to the fork's `main` branch.

Relevant final commands in the workflow:

```bash
for service in gateway events payments; do
  image="${REGISTRY}/${IMAGE_OWNER}/quickticket-${service}:${GITHUB_SHA}"
  # Validate one image reference and replace its tag.
done

git add -- k8s/gateway.yaml k8s/events.yaml k8s/payments.yaml
git commit -m "ci: update image tags to ${GITHUB_SHA}"
git push origin HEAD:main
```

The job-level guard is:

```yaml
if: ${{ !startsWith(github.event.head_commit.message, 'ci:') }}
```

### 2. Source commit, CI run, and generated commit

The source commit enabling the automation was:

```text
8149b864f12fd96abeecb9c922d786ae08c898b4
feat: automate image tag updates in CI
```

Its workflow run succeeded, including the new update step:

```text
run_id: 36335986319
run_url: https://github.com/L10nff/SRE-Intro/actions/runs/36335986319
created_at: 2026-09-27T17:11:08Z
run_started_at: 2026-09-27T17:11:08Z
updated_at: 2026-09-27T17:14:04Z
job: build | completed | success
Build and push multi-platform images | completed | success
Update Kubernetes image tags | completed | success
```

The workflow produced this direct child commit:

```text
commit: ac32267466c3374dc80afb93da865f5edb786133
parent: 8149b864f12fd96abeecb9c922d786ae08c898b4
author: github-actions[bot]
message: ci: update image tags to 8149b864f12fd96abeecb9c922d786ae08c898b4
files: k8s/events.yaml, k8s/gateway.yaml, k8s/payments.yaml
```

All three manifests were updated to the source SHA tag with `imagePullPolicy: Always`.

A second workflow run was not created for the bot commit. This is expected for a push authenticated with the workflow's `GITHUB_TOKEN`, which prevents recursive workflow execution. The explicit `ci:` guard provides an additional loop-prevention rule if the push mechanism changes later.

### 3. Automatic Argo CD deployment

Argo CD automatically detected the bot commit and synchronized it:

```text
started_at=2026-09-27T17:17:01Z
finished_at=2026-09-27T17:17:03Z
phase=Succeeded
bonus_rollout_observed_utc=2026-09-27T17:17:21Z
revision=ac32267466c3374dc80afb93da865f5edb786133
sync=Synced
health=Healthy
```

The actual running Pods used the generated SHA tag and registry digests:

```text
events   ghcr.io/l10nff/quickticket-events:8149b864f12fd96abeecb9c922d786ae08c898b4
         sha256:f485e59e5064a462a06c81888b28ba0a436cd23f73e674dc4d5ac4058fd15bbc
gateway  ghcr.io/l10nff/quickticket-gateway:8149b864f12fd96abeecb9c922d786ae08c898b4
         sha256:13a2ec0851d3f85ef7c7908956ac3743a73fa14de60ab37b3c8d788806676e15
payments ghcr.io/l10nff/quickticket-payments:8149b864f12fd96abeecb9c922d786ae08c898b4
         sha256:e35b9bc32f6b1cf0c8ce83ffaf2bbc9f4ecb02e13fab7d9f744a91d744d068c6
```

All three Pods were `Running`, Ready, and had zero restarts.

## Final Verification

Final validation was performed at `2026-09-27T17:19:10Z`.

```text
Current context: k3d-quickticket
Node: Ready, v1.33.13+k3s2
Argo CD revision: ac32267466c3374dc80afb93da865f5edb786133
Argo CD sync: Synced
Argo CD health: Healthy
Gateway rollout: successfully rolled out
Events rollout: successfully rolled out
Payments rollout: successfully rolled out
PostgreSQL rollout: successfully rolled out
Redis rollout: successfully rolled out
PostgreSQL PVC: Bound, 1Gi, local-path
Workflow YAML syntax: OK
Kubernetes client-side dry run: successful for all resources
Credential-pattern scan: clean
```

All five final Pods were `Running`, Ready, and had zero restarts. The three application Pods used the `8149b86...` GHCR images, while PostgreSQL and Redis retained their required upstream images.

Final endpoint verification at `2026-09-27T17:19:15Z`:

```text
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
http_code=200
```

## Conclusion

Lab 5 established a working CI/CD and GitOps path for QuickTicket. GitHub Actions builds immutable multi-architecture images and publishes them to GHCR. Kubernetes pulls the registry images through the configured secret, and Argo CD continuously deploys the Git-declared manifests.

The controlled failure demonstrated that an invalid image produces `ErrImagePull`, `ImagePullBackOff`, and eventually an Argo CD `Degraded` state. A Git revert restored the desired image and returned the system to `Synced / Healthy` without an imperative deployment rollback.

The Bonus automation completed the delivery loop: a source commit built new images, GitHub Actions generated a manifest commit, and Argo CD automatically deployed that commit. The final runtime and endpoint checks were healthy, and no credentials were committed.
