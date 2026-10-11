# Lab 5 — CI/CD & GitOps

**Branch:** `feature/lab5`
**Cluster:** k3d `quickticket` (context `k3d-quickticket`), k3s v1.35.5, single node, namespace `default`
**ArgoCD:** v3.5.3, namespace `argocd`

---

## ⚠️ Read this first — what is real and what is not

Everything in this submission is real output from the cluster, with **one clearly marked
exception**.

| Part of the lab | Status |
|---|---|
| `.github/workflows/ci.yml` written | ✅ done: complete, runnable workflow |
| GitHub Actions run (green check) | ❌ **not run**, see §5.1 callout |
| ghcr.io package listing | ❌ **not run**, see §5.2 callout |
| K8s manifests switched to registry image refs | ✅ done (with a documented caveat, §5.3) |
| ArgoCD installed | ✅ done, for real |
| ArgoCD Application created + Synced/Healthy | ✅ done, for real |
| GitOps loop (Git change → cluster) | ✅ done, for real, timed |
| Task 2 rollback via `git revert` | ✅ done, for real, timed |
| Bonus auto-tag-update | ⚠️ workflow written; the *steps* were re-enacted locally and the resulting commit really was auto-synced by ArgoCD (§Bonus) |

**Why:** this environment has no GitHub credentials, and the fork hasn't been pushed yet, so
anything that needs `git push origin`, a GitHub Actions runner, or a `ghcr.io` login just can't
run. Instead of faking those, I set up the GitOps source of truth for ArgoCD as a **bare mirror
of this repo served over Git smart-HTTP from inside the cluster**. Every ArgoCD behaviour that's
being graded here (polling a real git remote, detecting a new commit, syncing, health assessment,
drift detection, rollback) is genuinely exercised against a real git server. The only thing that
differs is the hostname in `repoURL`. See [Appendix A](#appendix-a--the-in-cluster-git-remote)
for how it's built and how to switch back to GitHub.

---

## Task 1 — CI Pipeline + ArgoCD Setup (6 pts)

### 5.1 The CI workflow

`.github/workflows/ci.yml` is committed in this branch. A few notes on how I designed it:

* **`build` job**: a 3-way matrix (`gateway`, `events`, `payments`) so all three images build in
  parallel. Each one uses `docker/build-push-action@v6` with GitHub Actions layer caching scoped
  per service, and pushes two tags: `${{ github.sha }}` (immutable, this is what `k8s/` pins to)
  and `latest` (just for convenience).
* **Lowercase owner**: ghcr.io rejects uppercase path segments, and `Vanady39` has a capital V,
  so I lowercase the owner in shell (`${OWNER,,}`) instead of hardcoding it.
* **`permissions`**: `packages: write` on the build job, `contents: write` only on the job that
  needs to push the manifest commit. I wanted least privilege per job rather than one blanket
  block.
* **`concurrency`**: two overlapping runs would both try to push a tag-update commit to `main`
  and one of them would lose the race, so runs on the same ref get serialised.
* **Loop guard**: both jobs carry
  `if: ${{ !startsWith(github.event.head_commit.message, 'ci:') }}`, so the `ci: update image
  tags…` commit that the workflow itself pushes doesn't re-trigger the workflow. (For
  `workflow_dispatch`, `head_commit` is null, `startsWith` just sees an empty string, and the
  guard passes, so manual runs still work.)

```yaml
name: CI

on:
  push:
    branches: [main]
  workflow_dispatch:

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: false

jobs:
  build:
    name: build ${{ matrix.service }}
    runs-on: ubuntu-latest
    if: ${{ !startsWith(github.event.head_commit.message, 'ci:') }}

    permissions:
      contents: read
      packages: write      # needed to push to ghcr.io

    strategy:
      fail-fast: false
      matrix:
        service: [gateway, events, payments]

    steps:
      - uses: actions/checkout@v4

      - name: Compute lowercase image name
        id: img
        run: |
          OWNER="${{ github.repository_owner }}"
          echo "image=ghcr.io/${OWNER,,}/quickticket-${{ matrix.service }}" >> "$GITHUB_OUTPUT"

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push ${{ matrix.service }}
        uses: docker/build-push-action@v6
        with:
          context: ./app/${{ matrix.service }}
          file: ./app/${{ matrix.service }}/Dockerfile
          push: true
          tags: |
            ${{ steps.img.outputs.image }}:${{ github.sha }}
            ${{ steps.img.outputs.image }}:latest
          labels: |
            org.opencontainers.image.source=${{ github.server_url }}/${{ github.repository }}
            org.opencontainers.image.revision=${{ github.sha }}
          cache-from: type=gha,scope=${{ matrix.service }}
          cache-to: type=gha,mode=max,scope=${{ matrix.service }}
```

(The second job, `update-manifests`, is the Bonus Task and is shown in full there. The file on
disk is the authoritative copy.)

I checked the YAML actually parses:

```console
$ python -c "import yaml;d=yaml.safe_load(open('.github/workflows/ci.yml'));print('OK jobs:',list(d['jobs'].keys()))"
OK jobs: ['build', 'update-manifests']
```

> ⚠️ **Not yet run:** this workflow is committed but has not executed. The repo hasn't been
> pushed yet, and there are no GitHub credentials in this environment. Paste the Actions run URL
> and the green-check screenshot here after the first push.
>
> ```text
> ### PLACEHOLDER: GitHub Actions run
> Actions run URL: __________________________________________________
> Status:          __________________________________________________
> Duration:        __________________________________________________
> ```

### 5.2 Verify images are pushed

> ⚠️ **Not yet run:** no push means no packages. `gh` isn't installed either, and there are no
> GitHub credentials in this environment. Paste the ghcr.io package listing here after the first
> Actions run completes.
>
> ```text
> ### PLACEHOLDER: ghcr.io packages
> $ gh api user/packages?package_type=container --jq '.[].name'
> (expected:)
> quickticket-gateway
> quickticket-events
> quickticket-payments
> ```

Here are the image names the workflow will produce (GitHub owner `Vanady39`, lowercased for
ghcr.io):

```text
ghcr.io/vanady39/quickticket-gateway:<40-char commit sha>
ghcr.io/vanady39/quickticket-events:<40-char commit sha>
ghcr.io/vanady39/quickticket-payments:<40-char commit sha>
```

### 5.3 K8s manifests now use registry image references

I changed `k8s/gateway.yaml`, `k8s/events.yaml` and `k8s/payments.yaml` from
`quickticket-<svc>:v1` to the full ghcr.io reference pinned to a commit SHA, and each Deployment
now has an `imagePullSecrets` entry:

```yaml
    spec:
      # ghcr.io packages are private by default; this secret holds a classic PAT
      # with read:packages. Inert while imagePullPolicy is Never (no pull is
      # attempted) — required as soon as the policy flips to Always.
      imagePullSecrets:
        - name: ghcr-secret
      containers:
        - name: gateway
          image: ghcr.io/vanady39/quickticket-gateway:ae1a4579f016fd3e7b68ad97a4b998660af38d3c
          imagePullPolicy: Never
```

**One honest caveat on `imagePullPolicy`.** The lab asks for `Always`, but that can't actually
work here: nothing has ever been pushed to `ghcr.io/vanady39/*`, so `Always` would put every pod
into `ImagePullBackOff` and leave the cluster broken for labs 6-12. So instead I re-tagged the
locally built images with the exact ghcr.io names and loaded them into the cluster, and the
policy stays `Never`:

```console
$ for svc in gateway events payments; do
    docker tag quickticket-${svc}:v1 ghcr.io/vanady39/quickticket-${svc}:${SHA}
  done
$ k3d image import ghcr.io/vanady39/quickticket-gateway:${SHA} \
                   ghcr.io/vanady39/quickticket-events:${SHA} \
                   ghcr.io/vanady39/quickticket-payments:${SHA} -c quickticket
INFO[0005] Successfully imported image(s)
INFO[0005] Successfully imported 3 image(s) into 1 cluster(s)
```

So the manifest text is exactly what CI will produce (this matters, since the Bonus `sed` has to
match those lines), while the actual bytes running in the cluster are the locally built ones.
After the first real push, the only change needed is `Never` → `Always` plus creating the pull
secret.

I could not create `ghcr-secret` itself. It needs a classic PAT with `read:packages`, and that's
a credential this environment doesn't have and shouldn't fabricate:

```console
# NOT RUN — requires a classic PAT
kubectl create secret docker-registry ghcr-secret \
  --docker-server=ghcr.io \
  --docker-username=Vanady39 \
  --docker-password=<CLASSIC_PAT_WITH_read:packages>
```

The reference to the missing secret is harmless while the policy is `Never`, the kubelet just
logs a warning and carries on, since no pull is actually attempted. I verified this:

```console
$ kubectl get events --sort-by=.lastTimestamp | tail -12     # excerpt
43s  Warning  FailedToRetrieveImagePullSecret  pod/events-7f49b577c4-zmj7v    Unable to retrieve some image pull secrets (ghcr-secret); attempting to pull the image may not succeed.
43s  Warning  FailedToRetrieveImagePullSecret  pod/payments-7c99c48fb7-svc2t  Unable to retrieve some image pull secrets (ghcr-secret); attempting to pull the image may not succeed.
43s  Warning  FailedToRetrieveImagePullSecret  pod/gateway-7b5f6b4f98-r8868   Unable to retrieve some image pull secrets (ghcr-secret); attempting to pull the image may not succeed.

$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
events-7f49b577c4-zmj7v     1/1     Running   0          49s
gateway-7b5f6b4f98-r8868    1/1     Running   0          49s
payments-7c99c48fb7-svc2t   1/1     Running   0          49s
postgres-67977f4df6-rrh44   1/1     Running   0          9h
redis-87cf6bc6b-kn267       1/1     Running   0          9h
```

Lab 4's probe conventions (`tcpSocket` liveness on gateway and events, `httpGet /health` on
payments) were deliberately left alone here:

```console
$ git diff 2adb1c3 HEAD -- k8s/ | grep -E '^[-+].*(Probe|tcpSocket|httpGet)'
(no output — no probe changed)
```

### 5.4 Install ArgoCD

```console
$ kubectl create namespace argocd
namespace/argocd created

$ kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
customresourcedefinition.apiextensions.k8s.io/applications.argoproj.io created
customresourcedefinition.apiextensions.k8s.io/appprojects.argoproj.io created
serviceaccount/argocd-application-controller created
...
statefulset.apps/argocd-application-controller created
networkpolicy.networking.k8s.io/argocd-server-network-policy created
The CustomResourceDefinition "applicationsets.argoproj.io" is invalid: metadata.annotations: Too long: may not be more than 262144 bytes
```

**The documented install command actually doesn't fully succeed on a stock cluster.** The
`applicationsets.argoproj.io` CRD is bigger than 256 KiB, and client-side `kubectl apply` stores
the whole object in the `kubectl.kubernetes.io/last-applied-configuration` annotation, which is
capped at 262144 bytes. Everything else installs fine, just that one CRD doesn't. The fix is
server-side apply, which tracks field ownership in `metadata.managedFields` instead of an
annotation:

```console
$ curl -sSL -o argocd-install.yaml https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
$ kubectl apply -n argocd --server-side=true --force-conflicts -f argocd-install.yaml
...
networkpolicy.networking.k8s.io/argocd-server-network-policy serverside-applied

$ kubectl get crd | grep argoproj
applications.argoproj.io                       2026-09-22T07:21:14Z
applicationsets.argoproj.io                    2026-09-22T07:21:21Z
appprojects.argoproj.io                        2026-09-22T07:21:15Z
```

(`--force-conflicts` is only needed because the first, client-side apply already claimed
ownership of some fields.)

```console
$ kubectl wait --for=condition=Available deployment/argocd-server -n argocd --timeout=120s
deployment.apps/argocd-server condition met

$ kubectl get pods -n argocd
NAME                                                READY   STATUS    RESTARTS   AGE
argocd-application-controller-0                     1/1     Running   0          24m
argocd-applicationset-controller-7f95b9cd7c-v2vrk   1/1     Running   0          24m
argocd-dex-server-8666767789-lq6x5                  1/1     Running   0          24m
argocd-notifications-controller-797f48b4-snxfs      1/1     Running   0          24m
argocd-redis-6fd5864464-nxnvv                       1/1     Running   0          24m
argocd-repo-server-c4977564f-sw2n7                  1/1     Running   0          24m
argocd-server-59bd8b5c4-z8wk5                       1/1     Running   0          24m
```

Cost on this node was cheaper than the ~1 GB people often quote, because the default install
sets no resource requests and these components are idle:

```console
$ kubectl top pods -n argocd
NAME                                                CPU(cores)   MEMORY(bytes)
argocd-application-controller-0                     5m           170Mi
argocd-applicationset-controller-7f95b9cd7c-v2vrk   1m           20Mi
argocd-dex-server-8666767789-lq6x5                  1m           31Mi
argocd-notifications-controller-797f48b4-snxfs      1m           18Mi
argocd-redis-6fd5864464-nxnvv                       6m           4Mi
argocd-repo-server-c4977564f-sw2n7                  1m           39Mi
argocd-server-59bd8b5c4-z8wk5                       1m           27Mi
```

About 310 MiB resident for the whole control plane, and nothing went `Pending`.

Admin password and UI access:

```console
$ kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d; echo
<15-char generated password — not reproduced here>

$ kubectl port-forward svc/argocd-server -n argocd 8443:443 &
Forwarding from 127.0.0.1:8443 -> 8080
Forwarding from [::1]:8443 -> 8080
# UI: https://localhost:8443  (self-signed cert), login admin / <password>
```

### 5.5 Create the ArgoCD Application

**I skipped the CLI download.** The lab downloads the `argocd` binary from GitHub releases, but
that's unnecessary since the binary is already inside the `argocd-server` image the cluster just
pulled. So I just copied it out of the running pod instead:

```console
$ POD=$(kubectl -n argocd get pod -l app.kubernetes.io/name=argocd-server -o jsonpath='{.items[0].metadata.name}')
$ kubectl cp -n argocd "$POD:/usr/local/bin/argocd" ./argocd && chmod +x ./argocd
$ ./argocd version --client
argocd: v3.5.3
  BuildDate: 2026-09-14T07:18:56Z
  GitCommit: c9c369efcc5b2a0bd720803f8d14a1c3eaddf579
  GitTag: v3.5.3
  GoVersion: go1.26.4
  Platform: linux/amd64
```

```console
$ ./argocd login localhost:8443 --insecure --username admin --password "$PW"
'admin:login' logged in successfully
Context 'localhost:8443' updated

$ ./argocd account get-user-info
Logged In: true
Username: admin
Issuer: argocd
Groups:
```

Now the Application. **`--repo` points at the in-cluster git server, not github.com** (see the
callout at the top and [Appendix A](#appendix-a--the-in-cluster-git-remote)):

```console
$ ./argocd app create quickticket \
    --repo http://gitserver.gitops-src.svc.cluster.local:8080/quickticket.git \
    --path k8s \
    --revision main \
    --dest-server https://kubernetes.default.svc \
    --dest-namespace default \
    --sync-policy automated
application 'quickticket' created
```

The first attempt failed, and I kept the error because it actually proves ArgoCD really did
reach out and talk git to the remote:

```console
{"level":"fatal","msg":"rpc error: code = InvalidArgument desc = application spec for quickticket is invalid: InvalidSpecError: repository not accessible: repositories not accessible: &Repository{Repo: \"http://gitserver.gitops-src.svc.cluster.local:8080/quickticket.git\", Type: \"\", Name: \"\", Project: \"\"}: repo client error while testing repository: rpc error: code = Unknown desc = error testing repository connectivity: unable to ls-remote HEAD on repository: unable to resolve 'HEAD' to a commit SHA","time":"2026-09-22T10:24:42+03:00"}
```

Turns out the bare repo had been created by `git init --bare`, whose `HEAD` pointed at
`refs/heads/master`, while the branch I pushed was `main`. Fixed it with
`git symbolic-ref HEAD refs/heads/main` inside the server pod. ArgoCD validates a source by
running `git ls-remote`, exactly like the CLI would.

```console
$ ./argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             http://gitserver.gitops-src.svc.cluster.local:8080/quickticket.git
  Target:           main
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to main (c3abe94)
Health Status:      Healthy

GROUP  KIND                   NAMESPACE  NAME           STATUS  HEALTH   HOOK  MESSAGE
       ConfigMap              default    postgres-seed  Synced                 configmap/postgres-seed configured
       PersistentVolumeClaim  default    postgres-data  Synced  Healthy        persistentvolumeclaim/postgres-data configured
       Service                default    postgres       Synced  Healthy        service/postgres configured
       Service                default    redis          Synced  Healthy        service/redis configured
       Service                default    gateway        Synced  Healthy        service/gateway configured
       Service                default    events         Synced  Healthy        service/events configured
       Service                default    payments       Synced  Healthy        service/payments configured
apps   Deployment             default    payments       Synced  Healthy        deployment.apps/payments configured
apps   Deployment             default    postgres       Synced  Healthy        deployment.apps/postgres configured
apps   Deployment             default    events         Synced  Healthy        deployment.apps/events configured
apps   Deployment             default    gateway        Synced  Healthy        deployment.apps/gateway configured
apps   Deployment             default    redis          Synced  Healthy        deployment.apps/redis configured
```

**Synced + Healthy.** ArgoCD picked up exactly the 12 objects from the five top-level files in
`k8s/` and didn't descend into `k8s/chart/` (directory recursion is off by default), which
matches how `kubectl apply -f k8s/` behaved back in Lab 4.

The takeover wasn't a no-op though, ArgoCD re-applied the three Deployments with the new ghcr.io
image references and the pods actually rolled:

```console
$ kubectl get deploy -o custom-columns='NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image'
NAME       IMAGE
events     ghcr.io/vanady39/quickticket-events:28eb8392a575447044442fde4b4fa3e38b757430
gateway    ghcr.io/vanady39/quickticket-gateway:28eb8392a575447044442fde4b4fa3e38b757430
payments   ghcr.io/vanady39/quickticket-payments:28eb8392a575447044442fde4b4fa3e38b757430
postgres   postgres:17-alpine
redis      redis:7-alpine

$ curl -s http://127.0.0.1:3080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

### 5.6 Verify the GitOps loop

I added a `version: "v2"` label to `k8s/gateway.yaml` **in Git only**, no `kubectl` involved, then
committed and pushed it to the branch ArgoCD watches. I didn't run `argocd app sync`, I just let
the poller find it on its own:

```console
$ git commit -m "feat: add version label to gateway"
$ git push cluster HEAD:refs/heads/main
pushed 9c407c5 at 2026-09-22T10:26:09+03:00
t+0s    label=''    argocd=Synced/c3abe94d4b06e
t+10s   label=''    argocd=Synced/c3abe94d4b06e
t+20s   label=''    argocd=Synced/c3abe94d4b06e
t+31s   label=''    argocd=Synced/c3abe94d4b06e
t+41s   label=''    argocd=Synced/c3abe94d4b06e
t+51s   label=''    argocd=Synced/c3abe94d4b06e
t+61s   label=''    argocd=Synced/c3abe94d4b06e
t+71s   label=''    argocd=Synced/c3abe94d4b06e
t+81s   label=''    argocd=Synced/c3abe94d4b06e
t+91s   label=''    argocd=Synced/c3abe94d4b06e
t+102s  label=''    argocd=Synced/c3abe94d4b06e
t+112s  label=''    argocd=Synced/c3abe94d4b06e
t+122s  label=''    argocd=Synced/c3abe94d4b06e
t+132s  label='v2'  argocd=Synced/9c407c50c2049
AUTO-SYNCED after 132s
```

```console
$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2

$ ./argocd app get quickticket          # header lines
Sync Status:        Synced to main (9c407c5)
Health Status:      Healthy
apps   Deployment   default   gateway   Synced   Healthy   deployment.apps/gateway configured

$ ./argocd app history quickticket
SOURCE  http://gitserver.gitops-src.svc.cluster.local:8080/quickticket.git
ID      DATE                           REVISION
0       2026-09-22 10:24:54 +0300 MSK  main (c3abe94)
1       2026-09-22 10:28:12 +0300 MSK  main (9c407c5)
```

132 s from push to the label being live in the cluster, which lines up with ArgoCD's 180 s
default repo-poll interval (`timeout.reconciliation`).

### 5.7 Answer — "What happens if someone runs `kubectl edit` on a resource managed by ArgoCD?"

Short answer: it depends entirely on whether `selfHeal` is on, and by default it's off. So the
edit *sticks*, and all ArgoCD does is turn the Application red. This seems to be the most common
misconception about ArgoCD, so I actually tested both ways instead of just asserting it.

**With `syncPolicy.automated` but `selfHeal: false`** (what `--sync-policy automated` gives you):

```console
$ kubectl -n argocd get app quickticket -o jsonpath='{.spec.syncPolicy}'
{"automated":{}}

$ kubectl patch deployment gateway --type=merge -p '{"metadata":{"labels":{"version":"hand-edited"}}}'
deployment.apps/gateway patched

# ...60 seconds later...
$ kubectl -n argocd get app quickticket -o jsonpath='{.status.sync.status}'
OutOfSync
$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
hand-edited

$ ./argocd app diff quickticket
===== apps/Deployment default/gateway ======
13c13
<     version: hand-edited
---
>     version: v2
diff exit=1
```

The hand edit survived. Automated sync means "reconcile when the desired state in Git changes",
not "reconcile whenever the live state drifts". ArgoCD noticed within one reconcile cycle,
flipped the app to `OutOfSync`, and showed the drift in `argocd app diff`, but it didn't touch
the cluster.

**With `selfHeal: true`:**

```console
$ ./argocd app set quickticket --self-heal
$ kubectl -n argocd get app quickticket -o jsonpath='{.spec.syncPolicy}'
{"automated":{"selfHeal":true}}

$ kubectl patch deployment gateway --type=merge -p '{"metadata":{"labels":{"version":"hand-edited-again"}}}'
deployment.apps/gateway patched
t+0s label=hand-edited-again
t+3s label=v2
self-healed back to Git after 3s
```

Reverted in **3 seconds**. Self-heal is driven by the Kubernetes watch stream, not the 3-minute
git poll, so it's basically instant.

**What this actually means in practice:**

1. Your change is, at best, temporary, and at worst, invisible. Even without self-heal, the next
   commit that touches that resource silently overwrites the hand edit, because ArgoCD applies
   the whole manifest from Git, not a diff. An operator who "fixed" a replica count by hand during
   an incident will have it undone by an unrelated PR hours later, with no warning at all.
2. It breaks the audit trail. Git stops being the answer to "why does production look like this?",
   which is kind of the whole point of GitOps.
3. `OutOfSync` is a real signal and should be alerted on. ArgoCD exports
   `argocd_app_info{sync_status="OutOfSync"}`, and a long-lived OutOfSync app usually means
   someone is operating outside the pipeline.
4. The correct emergency procedure is still Git: commit the fix and let it sync (~3 min, or
   instant with `argocd app sync`). If the outage can't wait, the honest options are
   `argocd app set <app> --sync-policy none` first, so the change isn't fought or silently
   reverted, then fix by hand, then land the same change in Git and re-enable automation.
5. One exception worth knowing about: fields that a *controller* legitimately owns, HPA-managed
   `spec.replicas` being the classic case, need to be excluded with `ignoreDifferences`, or
   ArgoCD and the HPA will fight each other forever. That's not really "someone ran kubectl edit",
   but it produces the same `OutOfSync` symptom.

I set `selfHeal` back to `false` after the experiment, so labs 6-12 can still run the course's
`kubectl set env` / `kubectl scale` exercises without ArgoCD undoing them mid-experiment.

---

## Task 2 — Rollback via GitOps (4 pts)

### 5.8 Deploy a bad version

I edited `k8s/gateway.yaml` to point at a tag that doesn't exist, and flipped `imagePullPolicy`
to `Always` in the same commit. That's what a real registry deploy actually looks like, and it's
what makes the failure a genuine `ImagePullBackOff` instead of a local `ErrImageNeverPull`:

```diff
-          image: ghcr.io/vanady39/quickticket-gateway:28eb8392a575447044442fde4b4fa3e38b757430
-          imagePullPolicy: Never
+          image: ghcr.io/vanady39/quickticket-gateway:does-not-exist
+          imagePullPolicy: Always
```

```console
$ git commit -m "feat: deploy new gateway version"     # 69c55a5
$ git push cluster HEAD:refs/heads/main
bad deploy pushed 69c55a5 at 2026-09-22T10:30:04+03:00
t+0s   argocd-rev=9c407c5
...
t+232s argocd-rev=9c407c5
t+242s argocd-rev=69c55a5
ArgoCD picked up bad commit after 242s
```

```console
$ kubectl get pods
NAME                        READY   STATUS             RESTARTS   AGE
events-7f49b577c4-zmj7v     1/1     Running            0          10m
gateway-54454dc585-5mwsv    0/1     ImagePullBackOff   0          52s
gateway-7b5f6b4f98-r8868    1/1     Running            0          10m
payments-7c99c48fb7-svc2t   1/1     Running            0          10m
postgres-67977f4df6-rrh44   1/1     Running            0          10h
redis-87cf6bc6b-kn267       1/1     Running            0          10h
```

Here's the real reason, from `kubectl describe`:

```text
Warning  Failed   spec.containers{gateway}: Failed to pull image "ghcr.io/vanady39/quickticket-gateway:does-not-exist":
  failed to resolve reference "ghcr.io/vanady39/quickticket-gateway:does-not-exist": failed to authorize:
  failed to fetch anonymous token: unexpected status from GET request to
  https://ghcr.io/token?scope=repository%3Avanady39%2Fquickticket-gateway%3Apull&service=ghcr.io: 403 Forbidden
Warning  Failed   spec.containers{gateway}: Error: ErrImagePull
Warning  Failed   spec.containers{gateway}: Error: ImagePullBackOff
```

And ArgoCD health, once `progressDeadlineSeconds` (600 s, the Deployment default) expired:

```console
$ ./argocd app get quickticket
...
Sync Status:        Synced to main (69c55a5)
Health Status:      Degraded

GROUP  KIND                   NAMESPACE  NAME           STATUS  HEALTH    HOOK  MESSAGE
...
apps   Deployment             default    events         Synced  Healthy         deployment.apps/events unchanged
apps   Deployment             default    gateway        Synced  Degraded        deployment.apps/gateway configured

$ kubectl get deploy gateway -o json | python3 -c "import json,sys;d=json.load(sys.stdin);[print(f\"{c['type']}={c['status']} {c['reason']}: {c.get('message','')}\") for c in d['status']['conditions']]"
Available=True MinimumReplicasAvailable: Deployment has minimum availability.
Progressing=False ProgressDeadlineExceeded: ReplicaSet "gateway-54454dc585" has timed out progressing.
```

Two things worth calling out here.

First, `Sync Status` stayed `Synced` the whole time. ArgoCD was doing its job perfectly, the
cluster matched Git exactly, Git was just wrong. Sync status tells you about the pipeline, health
status tells you about the service. Alerting on `OutOfSync` alone would have missed this outage
completely.

Second, it sat `Progressing` for 10 minutes before `Degraded`. ArgoCD doesn't invent its own
health verdict for a Deployment, it reads the Kubernetes rollout conditions. Kubernetes won't
declare a rollout failed until `progressDeadlineSeconds` elapses, so there's a 10-minute blind
window by default. Worth shortening (e.g. `progressDeadlineSeconds: 120`) if you want ArgoCD's
Degraded signal to actually be usable as an alert source.

And the good news is the service never actually went down. `maxUnavailable` on a 1-replica
Deployment rounds to 0, so the old ReplicaSet kept serving while the new pod sat in
`ImagePullBackOff`:

```console
$ kubectl get rs -l app=gateway -o custom-columns='NAME:.metadata.name,DESIRED:.spec.replicas,READY:.status.readyReplicas,IMAGE:.spec.template.spec.containers[0].image'
NAME                 DESIRED   READY    IMAGE
gateway-54454dc585   1         <none>   ghcr.io/vanady39/quickticket-gateway:does-not-exist
gateway-7b5f6b4f98   1         1        ghcr.io/vanady39/quickticket-gateway:28eb8392a575447044442fde4b4fa3e38b757430

$ curl -s http://127.0.0.1:3080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

A bad image tag is the friendly failure mode here. A bad image that starts up and passes its
probes but is actually broken would have rolled all the way out.

### 5.9 Rollback via `git revert`

No `kubectl`, no `argocd app rollback`, no `argocd app sync`, only Git:

```console
$ git revert HEAD --no-edit
[feature/lab5 682141b] Revert "feat: deploy new gateway version"
 Date: Tue Sep 22 11:01:43 2026 +0300
 1 file changed, 7 insertions(+), 2 deletions(-)

$ git log --oneline -3
682141b Revert "feat: deploy new gateway version"
69c55a5 feat: deploy new gateway version
9c407c5 feat: add version label to gateway

$ git push cluster HEAD:refs/heads/main
revert 682141b pushed at 2026-09-22T11:01:48+03:00
t+0s    rev=69c55a5 health=Degraded | gateway-54454dc585-5mwsv=ImagePullBackOff(0/1) gateway-7b5f6b4f98-r8868=Running(1/1)
t+10s   rev=69c55a5 health=Degraded | gateway-54454dc585-5mwsv=ImagePullBackOff(0/1) gateway-7b5f6b4f98-r8868=Running(1/1)
t+21s   rev=69c55a5 health=Degraded | gateway-54454dc585-5mwsv=ImagePullBackOff(0/1) gateway-7b5f6b4f98-r8868=Running(1/1)
t+31s   rev=69c55a5 health=Degraded | gateway-54454dc585-5mwsv=ImagePullBackOff(0/1) gateway-7b5f6b4f98-r8868=Running(1/1)
t+41s   rev=69c55a5 health=Degraded | gateway-54454dc585-5mwsv=ImagePullBackOff(0/1) gateway-7b5f6b4f98-r8868=Running(1/1)
t+51s   rev=69c55a5 health=Degraded | gateway-54454dc585-5mwsv=ImagePullBackOff(0/1) gateway-7b5f6b4f98-r8868=Running(1/1)
t+62s   rev=69c55a5 health=Degraded | gateway-54454dc585-5mwsv=ImagePullBackOff(0/1) gateway-7b5f6b4f98-r8868=Running(1/1)
t+72s   rev=69c55a5 health=Degraded | gateway-54454dc585-5mwsv=ImagePullBackOff(0/1) gateway-7b5f6b4f98-r8868=Running(1/1)
t+82s   rev=69c55a5 health=Degraded | gateway-54454dc585-5mwsv=ImagePullBackOff(0/1) gateway-7b5f6b4f98-r8868=Running(1/1)
t+93s   rev=69c55a5 health=Degraded | gateway-54454dc585-5mwsv=ImagePullBackOff(0/1) gateway-7b5f6b4f98-r8868=Running(1/1)
t+103s  rev=69c55a5 health=Degraded | gateway-54454dc585-5mwsv=ImagePullBackOff(0/1) gateway-7b5f6b4f98-r8868=Running(1/1)
t+113s  rev=69c55a5 health=Degraded | gateway-54454dc585-5mwsv=ImagePullBackOff(0/1) gateway-7b5f6b4f98-r8868=Running(1/1)
t+123s  rev=69c55a5 health=Degraded | gateway-54454dc585-5mwsv=ImagePullBackOff(0/1) gateway-7b5f6b4f98-r8868=Running(1/1)
t+134s  rev=682141b health=Healthy | gateway-7b5f6b4f98-r8868=Running(1/1)
RECOVERED after 134s at 2026-09-22T11:04:02+03:00
```

```console
$ ./argocd app get quickticket
...
Sync Status:        Synced to main (682141b)
Health Status:      Healthy

apps   Deployment  default  gateway  Synced  Healthy  deployment.apps/gateway configured

$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
events-7f49b577c4-zmj7v     1/1     Running   0          39m
gateway-7b5f6b4f98-r8868    1/1     Running   0          39m
payments-7c99c48fb7-svc2t   1/1     Running   0          39m
postgres-67977f4df6-rrh44   1/1     Running   0          10h
redis-87cf6bc6b-kn267       1/1     Running   0          10h

$ curl -s http://127.0.0.1:3080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

Notice the gateway pod's age: **39m, zero restarts**. The rollback didn't restart anything at
all. ArgoCD applied the reverted spec, the Deployment controller recognised it as the
already-running ReplicaSet `gateway-7b5f6b4f98`, scaled the broken one down to zero, and the
surviving pod was never touched.

### 5.9 Answer — "How long from `git revert` + push to pods being healthy again?"

**134 seconds**, measured from `git push` returning to `argocd app get` reporting
`Synced to main (682141b)` + `Healthy`, with the failed pod gone.

Broken down:

| Phase | Time | What sets it |
|---|---:|---|
| `git revert` + `git push` | ~2 s | local |
| ArgoCD notices the new commit | ~130 s | repo poll interval, default 180 s, so 0–180 s uniform; this was one sample |
| Apply + Deployment converges | ~2 s | no pod start needed: rolling back to the old ReplicaSet is a scale operation |
| **Total** | **134 s** | |

Basically all of that is poll latency. The actual reconcile itself was ~2 seconds. Across three
independent pushes in this lab, detection took 132 s, 242 s and 235 s, a spread of about 110 s,
which is pretty much what an un-triggered 3-minute poll should look like. That variance is the
argument for a webhook: `POST /api/webhook` from GitHub drops detection to under a second and
makes recovery time deterministic. `argocd app sync` does the same thing manually, and
`argocd app rollback <app> 3` (using the history IDs above) would have been faster still, but it
writes to the cluster without writing to Git, so the next sync of `main` would have re-broken it.
`git revert` is the right tool here precisely because it fixes the source of truth.

One honest caveat on the MTTR number: 134 s is time-to-recovery-of-the-broken-thing. Time-to-
detection was actually much worse, 600 s of `progressDeadlineSeconds` before ArgoCD would even
say `Degraded`. In a real incident the clock starts at the bad push, so the total would have been
~12 minutes unless a human or an alert caught the pod earlier.

---

## Bonus Task — Automated Image Tag Update (2 pts)

### The workflow

The `update-manifests` job in `.github/workflows/ci.yml` closes the loop. It runs after all three
matrix builds succeed, rewrites the `image:` lines in `k8s/`, and commits them back to `main`,
where ArgoCD is watching.

```yaml
  update-manifests:
    name: update image tags in k8s/
    needs: build
    runs-on: ubuntu-latest
    if: ${{ !startsWith(github.event.head_commit.message, 'ci:') }}

    permissions:
      contents: write      # needed to push the manifest commit back to main

    steps:
      - uses: actions/checkout@v4
        with:
          # Keep the GITHUB_TOKEN in .git/config so the final `git push` works.
          persist-credentials: true
          ref: main

      - name: Update image tags in manifests
        run: |
          set -euo pipefail
          SHA="${{ github.sha }}"
          OWNER="${{ github.repository_owner }}"
          OWNER="${OWNER,,}"
          for svc in gateway events payments; do
            sed -i -E "s|image: ghcr\.io/[^/]+/quickticket-${svc}:.*|image: ghcr.io/${OWNER}/quickticket-${svc}:${SHA}|" "k8s/${svc}.yaml"
          done
          grep -n 'image: ghcr.io' k8s/*.yaml

      - name: Commit and push manifest update
        run: |
          set -euo pipefail
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add k8s/
          if git diff --cached --quiet; then
            echo "manifests already point at ${{ github.sha }} — nothing to commit"
            exit 0
          fi
          git commit -m "ci: update image tags to ${{ github.sha }}"
          git push origin HEAD:main

      - name: Summary
        run: |
          {
            echo "### Images pushed"
            OWNER="${{ github.repository_owner }}"; OWNER="${OWNER,,}"
            for svc in gateway events payments; do
              echo "- \`ghcr.io/${OWNER}/quickticket-${svc}:${{ github.sha }}\`"
            done
          } >> "$GITHUB_STEP_SUMMARY"
```

**No infinite loop, and here's why.** Three independent guards:

1. `if: ${{ !startsWith(github.event.head_commit.message, 'ci:') }}` on both jobs, the pushed
   commit is literally named `ci: update image tags to <sha>`, so the run it triggers gets
   skipped.
2. Idempotence: `git diff --cached --quiet` exits 0 without committing if the tags are already
   correct, so even if the guard was removed the loop would still terminate after one extra
   iteration instead of running forever.
3. `concurrency`, so two runs can't race to push to `main`.

> ⚠️ **Not yet run:** the workflow has never executed on a runner. Paste the two-run Actions
> history (the code push, and the skipped `ci:` run) here after the first push.
>
> ```text
> ### PLACEHOLDER: Actions runs proving the loop terminates
> Run 1 (code commit)     : ______________________  status: ______
> Run 2 (ci: tag commit)  : ______________________  status: should be "skipped"
> ```

### Local re-enactment — and a real ArgoCD sync of the result

Since the workflow steps couldn't run on a runner, I executed each one **verbatim** on this
machine with `github.sha` bound to a real commit. Everything downstream of that (the manifest
rewrite, the commit, and ArgoCD noticing and deploying it with no manual sync) is genuine.

**Step 1: a real code change** (`app/gateway/main.py`, FastAPI app version `1.0.0` → `1.1.0`,
observable at `/openapi.json`):

```console
$ git commit -m "feat(gateway): bump API version to 1.1.0"
code commit: ae1a4579f016fd3e7b68ad97a4b998660af38d3c
```

**Step 2: what the `build` matrix job does** (local `docker build` plus `k3d image import`
standing in for `docker push ghcr.io/...`):

```console
### local re-enactment of the CI 'build' job (matrix: gateway, events, payments)
gateway: sha256:adffd4105887f3fb1c936b962b2303f09a8dd57c986d56b667d56e1e03400073
events: sha256:d7f7fb4b9ea24a9c72d24bac9265b48e1b0bf15aabb15eb3451c00da5e1b8d2e
payments: sha256:a5bc77fbfd326e94422201488e3b394ba018e1c8eddd3c76076dd3b782d6c925

### k3d image import (stands in for 'docker push ghcr.io/...')
INFO[0005] Successfully imported image(s)
INFO[0005] Successfully imported 3 image(s) into 1 cluster(s)
```

**Step 3: the `Update image tags in manifests` step, copied out of the workflow unchanged:**

```console
$ SHA="$(git rev-parse HEAD)"; OWNER="Vanady39"; OWNER="${OWNER,,}"
$ for svc in gateway events payments; do
    sed -i -E "s|image: ghcr\.io/[^/]+/quickticket-${svc}:.*|image: ghcr.io/${OWNER}/quickticket-${svc}:${SHA}|" "k8s/${svc}.yaml"
  done
$ grep -n 'image: ghcr.io' k8s/*.yaml
k8s/gateway.yaml:39:          image: ghcr.io/vanady39/quickticket-gateway:ae1a4579f016fd3e7b68ad97a4b998660af38d3c
k8s/payments.yaml:36:          image: ghcr.io/vanady39/quickticket-payments:ae1a4579f016fd3e7b68ad97a4b998660af38d3c
k8s/events.yaml:38:          image: ghcr.io/vanady39/quickticket-events:ae1a4579f016fd3e7b68ad97a4b998660af38d3c
```

**Step 4: the `Commit and push manifest update` step:**

```console
$ git add k8s/ && git commit -m "ci: update image tags to ${SHA}"
[feature/lab5 8309022] ci: update image tags to ae1a4579f016fd3e7b68ad97a4b998660af38d3c
 3 files changed, 3 insertions(+), 3 deletions(-)
```

> ⚠️ This commit was authored by the repo owner, not `github-actions[bot]`. The workflow sets the
> bot identity itself, and forging that author locally would misrepresent who actually ran it, so
> I left it alone.

**Step 5: ArgoCD deploys it with no human in the loop.** I deliberately did *not* run
`argocd app sync`:

```console
$ git push cluster HEAD:refs/heads/main
ci: commit pushed at 2026-09-22T11:04:44+03:00 — NOT running 'argocd app sync', waiting for the poller
t+0s    argocd-rev=682141b health=Healthy gateway-tag=28eb8392a575
t+11s   argocd-rev=682141b health=Healthy gateway-tag=28eb8392a575
...
t+225s  argocd-rev=682141b health=Healthy gateway-tag=28eb8392a575
t+235s  argocd-rev=8309022 health=Healthy gateway-tag=ae1a4579f016
AUTO-DEPLOYED new tag after 235s, no manual sync
```

### Git log — code commit → CI tag-update commit → live

```console
$ git log --oneline -4
8309022 ci: update image tags to ae1a4579f016fd3e7b68ad97a4b998660af38d3c
ae1a457 feat(gateway): bump API version to 1.1.0
682141b Revert "feat: deploy new gateway version"
69c55a5 feat: deploy new gateway version
```

```console
$ kubectl get deploy -o custom-columns='NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image'
NAME       IMAGE
events     ghcr.io/vanady39/quickticket-events:ae1a4579f016fd3e7b68ad97a4b998660af38d3c
gateway    ghcr.io/vanady39/quickticket-gateway:ae1a4579f016fd3e7b68ad97a4b998660af38d3c
payments   ghcr.io/vanady39/quickticket-payments:ae1a4579f016fd3e7b68ad97a4b998660af38d3c
postgres   postgres:17-alpine
redis      redis:7-alpine

$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
events-7dc944bd56-t86dv     1/1     Running   0          29s
gateway-858884796-fhpz9     1/1     Running   0          29s
payments-5c797645b5-vtj29   1/1     Running   0          29s
postgres-67977f4df6-rrh44   1/1     Running   0          10h
redis-87cf6bc6b-kn267       1/1     Running   0          10h
```

**The code change is actually live.** This is the `1.1.0` from step 1, served by a pod whose
image tag was chosen by the pipeline, not by a human:

```console
$ curl -s http://127.0.0.1:3080/openapi.json | jq -c .info
{"title":"QuickTicket Gateway","version":"1.1.0"}

$ curl -s http://127.0.0.1:3080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

Here's the full deployment history ArgoCD recorded across this lab:

```console
$ ./argocd app history quickticket
SOURCE  http://gitserver.gitops-src.svc.cluster.local:8080/quickticket.git
ID      DATE                           REVISION
0       2026-09-22 10:24:54 +0300 MSK  main (c3abe94)
1       2026-09-22 10:28:12 +0300 MSK  main (9c407c5)
2       2026-09-22 10:34:05 +0300 MSK  main (69c55a5)
3       2026-09-22 11:03:59 +0300 MSK  main (682141b)
4       2026-09-22 11:08:31 +0300 MSK  main (8309022)
```

Which maps to: 0 is the initial adoption of the existing deployment, 1 is the §5.6 `version: v2`
label, 2 is the §5.8 bad image tag, 3 is the §5.9 `git revert`, 4 is the Bonus auto tag update.

### A design objection to the pattern the lab teaches

This works, but I think committing to the same branch CI is triggered by is the fragile version
of the pattern, and it's worth saying so:

* The loop guard is just a string match on a commit message. Anyone who writes
  `ci: fix typo in gateway` by hand silently skips the whole build.
* CI needs `contents: write` on the app repo, so a compromised action could rewrite application
  source, not just publish an image.
* Build and deploy history get entangled in one branch, and `git log` becomes half machine noise.

The production-grade alternatives would be a separate config repo that ArgoCD watches and CI
writes to (no trigger overlap, no write access to app source), or ArgoCD Image Updater, which
watches the registry directly and needs no CI write access at all. Kustomize `images:` overlays
or a Helm `values.yaml` would also beat `sed` on raw YAML, since a regex over manifests breaks
the moment someone adds a sidecar or an initContainer whose image happens to match the pattern.

---

## Acceptance criteria

### Task 1 (6 pts)
- ✅ CI workflow committed (`.github/workflows/ci.yml`): complete and runnable
- ⚠️ GitHub Actions run green: **not run**, no GitHub credentials / repo not pushed (placeholder above)
- ⚠️ Images visible in ghcr.io: **not run**, same reason (placeholder above)
- ✅ ArgoCD installed (v3.5.3, 7/7 pods Running) and Application `quickticket` created
- ✅ Git change synced to cluster via ArgoCD: `version: v2` label, 132 s, no manual sync
- ✅ Written answer about `kubectl edit` with ArgoCD: tested both with and without `selfHeal`

### Task 2 (4 pts)
- ✅ Bad deploy detected: `Health Status: Degraded` + `ImagePullBackOff`
- ✅ `git revert` restored healthy state: `Synced to main (682141b)` + `Healthy`
- ✅ Git log showing deploy + revert
- ✅ Recovery time measured: 134 s, decomposed

### Bonus Task (2 pts)
- ✅ CI auto-updates image tags in manifests: `update-manifests` job
- ✅ No infinite loop: `if: !startsWith(head_commit.message, 'ci:')` + idempotent commit + `concurrency`
- ✅ ArgoCD syncs auto-updated tag: 235 s, no manual sync, new code verified live at `/openapi.json`
- ⚠️ The workflow itself has never executed on a runner; its steps were re-enacted locally (disclosed above)

---

## Appendix A — the in-cluster git remote

### Why

ArgoCD's `repoURL` has to be a git remote it can actually reach.
`https://github.com/Vanady39/SRE-Intro-Ivan-Vavilov.git` doesn't yet contain this branch, since
the fork hasn't been pushed. Pointing ArgoCD at it would have just produced a permanently
`Unknown`/`ComparisonError` Application and no GitOps loop at all. So I stood up a real git
server inside the cluster instead and pushed the branch to it.

### What differs from the lab's intended setup

| | Lab intends | Actually used |
|---|---|---|
| `repoURL` | `https://github.com/Vanady39/SRE-Intro-Ivan-Vavilov.git` | `http://gitserver.gitops-src.svc.cluster.local:8080/quickticket.git` |
| `targetRevision` | `main` | `main` (same) |
| `path` | `k8s` | `k8s` (same) |
| How commits arrive | `git push origin main` | `git push cluster HEAD:refs/heads/main` |
| Transport | HTTPS + PAT | plain HTTP, anonymous, cluster-internal only |
| Everything else | n/a | identical |

**Nothing about ArgoCD's behaviour here is simulated.** It runs `git ls-remote` and `git fetch`
against a real git daemon over the real Git smart-HTTP protocol, on its real 3-minute poll, and
assesses real resource health.

### Switching to GitHub after the first push

```bash
argocd app set quickticket \
  --repo https://github.com/Vanady39/SRE-Intro-Ivan-Vavilov.git \
  --revision main
# private repo? add credentials first:
#   argocd repo add https://github.com/Vanady39/SRE-Intro-Ivan-Vavilov.git \
#     --username Vanady39 --password <PAT>
kubectl delete namespace gitops-src   # tear the stand-in down
```

### How it is built

The image is `git http-backend` (git's own CGI program) behind a ~90-line stdlib Python server:

```dockerfile
FROM python:3.13-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
COPY githttp.py /usr/local/bin/githttp.py
ENV GIT_PROJECT_ROOT=/srv/git
ENV PORT=8080
EXPOSE 8080
CMD ["python3", "/usr/local/bin/githttp.py"]
```

```python
# githttp.py — the load-bearing part
def _cgi(self, method):
    path, _, query = self.path.partition("?")
    body = b""
    length = self.headers.get("Content-Length")
    if length:
        body = self.rfile.read(int(length))
    env = dict(os.environ)
    env.update({
        "GIT_PROJECT_ROOT": GIT_PROJECT_ROOT,
        "GIT_HTTP_EXPORT_ALL": "1",
        "GATEWAY_INTERFACE": "CGI/1.1",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "REMOTE_ADDR": self.client_address[0],
        "REMOTE_USER": "anonymous",
        "CONTENT_TYPE": self.headers.get("Content-Type", ""),
        "CONTENT_LENGTH": length or "",
        "GIT_PROTOCOL": self.headers.get("Git-Protocol", ""),
    })
    proc = subprocess.run(["git", "http-backend"], input=body, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    head, sep, payload = proc.stdout.partition(b"\r\n\r\n")
    # ...parse "Status:" + headers out of `head`, then write `payload`
```

For the deployment: namespace `gitops-src`, a 1 Gi `local-path` PVC so the repo survives pod
restarts, and an initContainer that creates the bare repo and enables push:

```yaml
      initContainers:
        - name: init-repo
          image: local-git-http:v1
          imagePullPolicy: Never
          command: ["/bin/sh", "-c"]
          args:
            - |
              set -eu
              REPO=/srv/git/quickticket.git
              [ -d "$REPO" ] || git init --bare "$REPO"
              git -C "$REPO" config http.receivepack true
              git -C "$REPO" config http.uploadpack true
```

Then build, load, deploy, seed:

```bash
docker build -t local-git-http:v1 .
k3d image import local-git-http:v1 -c quickticket
kubectl apply -f gitserver.yaml
kubectl -n gitops-src exec deploy/gitserver -- \
  git -C /srv/git/quickticket.git symbolic-ref HEAD refs/heads/main   # default branch = main

kubectl port-forward -n gitops-src svc/gitserver 9418:8080 &
git remote add cluster http://127.0.0.1:9418/quickticket.git
git push cluster HEAD:refs/heads/main
```

And proof the server actually speaks real git:

```console
$ git ls-remote cluster
c3abe94d4b06ea778277e935b8a9d4f34128c87b        refs/heads/main
$ git ls-remote cluster HEAD
c3abe94d4b06ea778277e935b8a9d4f34128c87b        HEAD
```

The `cluster` remote only lives in `.git/config`, which isn't committed, so it won't follow the
branch to GitHub.

---

## PR description

```text
- [x] Task 1 done — CI pipeline + ArgoCD deployed + GitOps loop verified
- [x] Task 2 done — rollback via git revert
- [x] Bonus Task done — automated image tag update

Caveats (see the callouts in submissions/lab5.md):
- The GitHub Actions run and the ghcr.io package listing could not be produced: this branch
  had not been pushed and the environment has no GitHub credentials. .github/workflows/ci.yml
  is complete and runnable; placeholders are marked in the submission for the run URL and the
  package listing.
- ArgoCD's Application therefore points at a bare mirror of this repo served over Git
  smart-HTTP from inside the cluster instead of github.com. Appendix A documents the setup and
  the one-command switch back to the GitHub repoURL.
- k8s/ manifests carry the real ghcr.io image references but keep imagePullPolicy: Never,
  because nothing has been published to ghcr.io yet; the images are locally built and
  re-tagged with those names. Flip to Always + create the ghcr-secret after the first push.
```
