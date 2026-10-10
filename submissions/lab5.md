# Lab 5 — CI/CD & GitOps

**Branch:** `feature/lab5`
**Cluster:** k3d v5.9.0 / k3s v1.35.5+k3s1 (single node, **arm64**), kubectl v1.36.0
**ArgoCD:** CLI v3.5.3, server `quay.io/argoproj/argocd:v3.5.3`
**Registry:** `ghcr.io/r3v1k` — [packages](https://github.com/R3v1k?tab=packages)
**CI:** [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) — written from scratch
**Raw terminal output:** `raw.log`, section `=== LAB 5 ===`

- [x] Task 1 — CI pipeline + ArgoCD deployed + GitOps loop verified
- [x] Task 2 — rollback via `git revert`
- [x] Bonus Task — automated image tag update

---

## Task 1 — CI Pipeline + ArgoCD Setup

### 5.0 Prerequisite the lab does not mention — `k8s/` was not on `main`

Labs 1–4 are submitted as PRs that stay open, so `main` in my fork is still a clean
mirror of upstream and the Lab 4 manifests lived only on `feature/lab4`:

```
$ git ls-tree main --name-only
.gitignore
README.md
app
docker-compose.monitoring.yaml
labs
lectures
monitoring
```

ArgoCD deploys from a Git *branch*, so the manifests had to exist on the branch it
tracks. First commit of this lab promotes them:

```
$ git checkout feature/lab4 -- k8s/
$ git commit -m "chore(lab5): promote Lab 4 K8s manifests to main as ArgoCD source"
```

### 5.1 CI workflow

The one decision that is not in the lab text: **the runners and the cluster do not share
an architecture.** GitHub's `ubuntu-latest` is amd64; this k3d cluster runs on Apple
Silicon:

```
$ uname -m
arm64
$ kubectl get node -o jsonpath='{.items[0].status.nodeInfo.architecture}'
arm64
```

An amd64-only image pulls fine and then dies at exec time inside the cluster, which
looks like a crash loop rather than a registry problem. So the workflow builds a
multi-arch manifest list with Buildx + QEMU instead of a plain `docker build`:

```yaml
      - name: Build and push ${{ matrix.service }}
        uses: docker/build-push-action@v6
        with:
          context: ./app/${{ matrix.service }}
          # Runners are amd64, the k3d cluster runs on Apple Silicon (arm64):
          # an amd64-only image would fail at exec in the cluster.
          platforms: linux/amd64,linux/arm64
          push: true
          tags: ${{ env.REGISTRY }}/${{ env.OWNER }}/quickticket-${{ matrix.service }}:${{ github.sha }}
          cache-from: type=gha,scope=${{ matrix.service }}
          cache-to: type=gha,mode=max,scope=${{ matrix.service }}
```

Three services are built by a `strategy.matrix` so they run as parallel jobs, and
`cache-from/to: type=gha` keeps rebuilds cheap. `permissions:` lists `contents: read`
explicitly — naming only `packages: write` sets every other scope to `none`, which
takes `actions/checkout` down with it.

`OWNER` is hardcoded lowercase (`r3v1k`): `${{ github.actor }}` is `R3v1k`, and ghcr.io
rejects uppercase in an image path.

**All four runs green** (`gh run list`):

| Run | Commit | Duration | Trigger |
|---|---|---:|---|
| [36339313992](https://github.com/R3v1k/SRE-Intro/actions/runs/36339313992) | `2da177c` | 1m38s | `ci: add CI pipeline for QuickTicket` |
| [36340007714](https://github.com/R3v1k/SRE-Intro/actions/runs/36340007714) | `dd50f70` | 36s | `feat: add version label to gateway` |
| [36387518692](https://github.com/R3v1k/SRE-Intro/actions/runs/36387518692) | `a6ccc79` | — | `feat: use ghcr.io images in K8s manifests` |
| [36387640239](https://github.com/R3v1k/SRE-Intro/actions/runs/36387640239) | `7cfb905` | — | `feat: deploy new gateway version` |

Run 1 took 1m38s cold; run 2 took **36s** — the `type=gha` cache working.

### 5.2 Images pushed to ghcr.io

```
$ gh api user/packages?package_type=container --jq '.[].name'
quickticket-gateway
quickticket-payments
quickticket-events

$ gh api user/packages?package_type=container --jq '.[] | {name, visibility, html_url}'
{"html_url":"https://github.com/users/R3v1k/packages/container/package/quickticket-gateway","name":"quickticket-gateway","visibility":"public"}
{"html_url":"https://github.com/users/R3v1k/packages/container/package/quickticket-payments","name":"quickticket-payments","visibility":"public"}
{"html_url":"https://github.com/users/R3v1k/packages/container/package/quickticket-events","name":"quickticket-events","visibility":"public"}
```

Verifying the multi-arch claim rather than trusting it — each tag is an OCI **index**,
not a single image:

```
$ docker buildx imagetools inspect ghcr.io/r3v1k/quickticket-gateway:dd50f70a6a33cb14d8ec0294cd52ba2b35ea5301
Name:      ghcr.io/r3v1k/quickticket-gateway:dd50f70a6a33cb14d8ec0294cd52ba2b35ea5301
MediaType: application/vnd.oci.image.index.v1+json
  Platform:    linux/amd64
  Platform:    linux/arm64
  Platform:    unknown/unknown
  Platform:    unknown/unknown
```

The two `unknown/unknown` entries are the SLSA attestation manifests Buildx attaches by
default, not broken platforms.

### 5.3 Manifests moved onto registry images

Per service (`gateway`, `events`, `payments`):

```diff
     spec:
+      imagePullSecrets:
+        - name: ghcr-secret
       containers:
         - name: gateway
-          image: quickticket-gateway:v1
-          imagePullPolicy: Never
+          image: ghcr.io/r3v1k/quickticket-gateway:dd50f70a6a33cb14d8ec0294cd52ba2b35ea5301
+          imagePullPolicy: Always
```

```
$ kubectl create secret docker-registry ghcr-secret \
    --docker-server=ghcr.io --docker-username=R3v1k --docker-password=<token>
secret/ghcr-secret created
```

> Note: the packages ended up **public**, so the pull would have worked anonymously.
> `imagePullSecrets` is kept because 5.3 asks for it and because the manifests should
> not silently depend on the packages staying public.

After the sync, every application pod runs a registry image — no `k3d image import`
anywhere in this lab:

```
$ kubectl get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].image}{"\n"}{end}'
events-7d84c7d4bc-mwb5k	    ghcr.io/r3v1k/quickticket-events:dd50f70a6a33cb14d8ec0294cd52ba2b35ea5301
gateway-9d8974b44-ll6cz	    ghcr.io/r3v1k/quickticket-gateway:dd50f70a6a33cb14d8ec0294cd52ba2b35ea5301
payments-55c7895fc4-prm95   ghcr.io/r3v1k/quickticket-payments:dd50f70a6a33cb14d8ec0294cd52ba2b35ea5301
postgres-78489d7f5f-6zqxz   postgres:17-alpine
redis-6fcfb5475d-wft97	    redis:7-alpine

$ kubectl describe pod -l app=gateway | grep -E "Pulling|Pulled"
  Normal  Pulling  32s  kubelet  Pulling image "ghcr.io/r3v1k/quickticket-gateway:dd50f70a..."
  Normal  Pulled   26s  kubelet  Successfully pulled image "ghcr.io/r3v1k/quickticket-gateway:dd50f70a..." in 5.987s. Image size: 52437799 bytes.
```

Critical path still healthy on registry images:

```
$ curl -s localhost:3080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

### 5.4 ArgoCD installed

The documented one-liner fails on this k3s version:

```
$ kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
...
The CustomResourceDefinition "applicationsets.argoproj.io" is invalid:
metadata.annotations: Too long: may not be more than 262144 bytes
```

Client-side `apply` stores the whole manifest in the
`kubectl.kubernetes.io/last-applied-configuration` annotation, and that CRD is larger
than the 256 KiB annotation limit. Server-side apply keeps the diff on the API server
instead of in an annotation:

```
$ kubectl apply -n argocd --server-side --force-conflicts -f .../install.yaml
customresourcedefinition.apiextensions.k8s.io/applicationsets.argoproj.io serverside-applied
...

$ kubectl get pods -n argocd
NAME                                                READY   STATUS    RESTARTS      AGE
argocd-application-controller-0                     1/1     Running   0             5m5s
argocd-applicationset-controller-7f95b9cd7c-9xbls   1/1     Running   1 (81s ago)   5m5s
argocd-dex-server-8666767789-hlhj8                  1/1     Running   0             5m5s
argocd-notifications-controller-797f48b4-pbgfl      1/1     Running   0             5m5s
argocd-redis-6fd5864464-zwdgg                       1/1     Running   0             5m5s
argocd-repo-server-c4977564f-2dbvb                  1/1     Running   0             5m5s
argocd-server-59bd8b5c4-gmsdr                       1/1     Running   0             5m5s
```

### 5.5 Application created

```
$ argocd app create quickticket \
    --repo https://github.com/R3v1k/SRE-Intro.git \
    --path k8s \
    --revision main \
    --dest-server https://kubernetes.default.svc \
    --dest-namespace default \
    --sync-policy automated
application 'quickticket' created

$ argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
Source:
- Repo:             https://github.com/R3v1k/SRE-Intro.git
  Target:           main
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to main (2da177c)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    redis     Synced  Healthy        service/redis configured
       Service     default    events    Synced  Healthy        service/events configured
       Service     default    gateway   Synced  Healthy        service/gateway configured
       Service     default    payments  Synced  Healthy        service/payments configured
       Service     default    postgres  Synced  Healthy        service/postgres configured
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments configured
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres configured
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis configured
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events configured
```

ArgoCD **adopted** the five Deployments and five Services that `kubectl apply` had
created in Lab 4 — `configured`, not `created`, and no pod restarts. It also ignored
`k8s/chart/` (the Lab 4 Helm chart): a `directory` source is non-recursive by default,
so the chart templates were never applied as a duplicate copy of the app.

### 5.6 GitOps loop verified

Adding a label to the gateway Deployment in Git only — nothing was applied by hand:

```diff
 metadata:
   name: gateway
   labels:
     app: gateway
+    version: "v2"
```

```
$ kubectl get deployment gateway -o jsonpath='{.metadata.labels}'    # before
{"app":"gateway"}

$ git commit -m "feat: add version label to gateway" && git push origin main
# pushed at 18:15:59Z

# polling the cluster, no `argocd app sync` issued:
t=0s   : label not yet present
...
t=131s : label not yet present
SYNCED after 141s — version=v2 at 18:18:35Z

$ kubectl get deployment gateway -o jsonpath='{.metadata.labels}'    # after
{"app":"gateway","version":"v2"}

$ argocd app get quickticket | grep -E "Sync Status|Health Status"
Sync Status:        Synced to main (dd50f70)
Health Status:      Healthy
```

**141 s from `git push` to the change being live**, with no manual sync — consistent
with ArgoCD's default 3-minute reconciliation (`timeout.reconciliation` is unset in
`argocd-cm`, so the 180 s default applies and a push lands at a random point in that
window).

### Written answer: what happens on `kubectl edit` of an ArgoCD-managed resource

The common answer — "ArgoCD instantly reverts it" — is **wrong for this Application**,
and the distinction is the whole point of the question.

`--sync-policy automated` produces exactly this spec:

```
$ kubectl -n argocd get application quickticket -o jsonpath='{.spec.syncPolicy}'
{"automated":{}}
```

`automated` means *sync when **Git** changes*. Reverting *cluster* drift is a separate
switch, `automated.selfHeal: true`, which is **off** here. So a manual `kubectl edit`:

1. is picked up by the application controller within one reconciliation (~3 min, or
   sooner via the Kubernetes watch), and the app flips to **OutOfSync**;
2. **stays that way.** The manual change keeps running. ArgoCD reports drift, it does
   not undo it;
3. is silently destroyed by the *next* sync — whenever someone pushes any commit to
   `k8s/`, or runs `argocd app sync`. The edit disappears with no warning, because Git
   is the only source the sync reads.

With `selfHeal: true` step 2 changes: the controller re-applies the Git state on its own
and the edit is reverted within a reconciliation cycle.

Either way the manual edit is **temporary and invisible to the next reader of the repo**
— which is the real hazard. `prune` is also off (`{"automated":{}}`), so a resource
deleted from Git is *not* deleted from the cluster: it lingers as an orphan.

Verified empirically in this cluster — see [Drift check](#drift-check-kubectl-edit-in-practice) below.

### Drift check: `kubectl edit` in practice

Git says `version: "v2"`. Editing the live object by hand:

```
$ kubectl patch deployment gateway --type=merge \
    -p '{"metadata":{"labels":{"version":"hand-edited"}}}'
deployment.apps/gateway patched

$ kubectl get deployment gateway -o jsonpath='{.metadata.labels}'
{"app":"gateway","version":"hand-edited"}
```

ArgoCD notices within seconds and reports the drift precisely:

```
$ argocd app get quickticket | grep -E "Sync Status|Health Status"
Sync Status:        OutOfSync from main (b2d7978)
Health Status:      Healthy

$ argocd app diff quickticket
===== apps/Deployment default/gateway ======
12c12
<     version: hand-edited
---
>     version: v2
```

Then it does **nothing about it** — polled for 244 s, well past the 180 s
reconciliation interval:

```
t=0s   live_label=hand-edited sync=OutOfSync
t=41s  live_label=hand-edited sync=OutOfSync
...
t=244s live_label=hand-edited sync=OutOfSync
```

The edit survives indefinitely. It dies on the next sync, without a warning or a
confirmation prompt:

```
$ argocd app sync quickticket
Phase:              Succeeded
Message:            successfully synced (all tasks run)

$ kubectl get deployment gateway -o jsonpath='{.metadata.labels}'
{"app":"gateway","version":"v2"}
```

So `Health: Healthy` while `Sync: OutOfSync` is the dangerous state: the cluster is
running something nobody can reconstruct from the repo, and it will vanish at an
unpredictable moment — whenever anyone pushes an unrelated commit to `k8s/`.

---

## Task 2 — Rollback via GitOps

### 5.8 Deploying a bad version

```diff
-          image: ghcr.io/r3v1k/quickticket-gateway:dd50f70a6a33cb14d8ec0294cd52ba2b35ea5301
+          image: ghcr.io/r3v1k/quickticket-gateway:does-not-exist
```

Pushed as `7cfb905` at **06:40:24Z**. No manual sync was issued; ArgoCD picked it up on
its own poll **324 s later**, at 06:46:07Z:

```
t=0s   rev=a6ccc79
...
t=308s rev=a6ccc79
t=324s rev=7cfb905 | gateway-6957d744b-c7tzf  0/1  ErrImagePull  0  5s
```

> Detection latency was 141 s for the 5.6 label change and 324 s here. Both are one
> reconciliation window: a push lands at a random point in the 180 s cycle, and the
> repo-server caches the last resolved revision.

```
$ kubectl get pods
NAME                        READY   STATUS             RESTARTS   AGE
events-7d84c7d4bc-mwb5k     1/1     Running            0          30m
gateway-6957d744b-c7tzf     0/1     ImagePullBackOff   0          23m
gateway-9d8974b44-ll6cz     1/1     Running            0          30m
payments-55c7895fc4-prm95   1/1     Running            0          30m
postgres-78489d7f5f-6zqxz   1/1     Running            0          13h
redis-6fcfb5475d-wft97      1/1     Running            0          13h

$ kubectl describe pod gateway-6957d744b-c7tzf | grep Failed
  Warning  Failed  20m (x5 over 23m)  kubelet  Failed to pull image
  "ghcr.io/r3v1k/quickticket-gateway:does-not-exist": rpc error: code = NotFound
  desc = ... failed to resolve reference: not found
  Warning  Failed  20m (x5 over 23m)     kubelet  Error: ErrImagePull
  Warning  Failed  4m54s (x19 over 23m)  kubelet  Error: ImagePullBackOff
```

**Two things worth naming here.**

First, `Sync Status` stayed **`Synced`** while the app was broken. ArgoCD faithfully
delivered what Git asked for; "Synced" means "the cluster matches the repo", not "the
cluster works". The failure only ever shows up in `Health`.

Second, the service never went down. `maxUnavailable` for a 1-replica Deployment rounds
to 0, so the old ReplicaSet kept serving while the new one was stuck:

```
$ curl -s localhost:3080/health     # during the bad deploy
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

### Failing faster than the default

With the stock `progressDeadlineSeconds: 600` the Deployment reports `Progressing` for a
full ten minutes before Kubernetes gives up, and ArgoCD mirrors that — a broken release
looks merely slow for ten minutes. One line in Git fixes it:

```diff
 spec:
   replicas: 1
+  # Default is 600s, so a failed rollout stays invisible for 10 minutes.
+  progressDeadlineSeconds: 60
```

```
$ kubectl get deployment gateway \
    -o jsonpath='{range .status.conditions[*]}{.type}={.status} reason={.reason}{"\n"}{end}'
Available=True    reason=MinimumReplicasAvailable
Progressing=False reason=ProgressDeadlineExceeded

$ argocd app get quickticket
Sync Status:        Synced to main (f67c67c)
Health Status:      Degraded

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH    HOOK  MESSAGE
apps   Deployment  default    gateway   Synced  Degraded        deployment.apps/gateway configured
apps   Deployment  default    events    Synced  Healthy         deployment.apps/events unchanged
apps   Deployment  default    payments  Synced  Healthy         deployment.apps/payments unchanged
```

`Available=True` next to `Progressing=False` is the precise machine-readable form of
"the new version is broken, the old one is still carrying traffic".

### 5.9 Rollback via `git revert`

Reverting the bad-image commit specifically, so the faster deadline stays:

```
$ git revert 7cfb905 --no-edit
[main b2d7978] Revert "feat: deploy new gateway version"
 1 file changed, 1 insertion(+), 1 deletion(-)

$ git push origin main       # 07:10:00Z
```

```
$ git log --oneline -3
b2d7978 Revert "feat: deploy new gateway version"
f67c67c feat(gateway): fail a stuck rollout in 60s instead of 600s
7cfb905 feat: deploy new gateway version
```

Again with no manual sync — polling until ArgoCD reacted on its own:

```
t=0s   health=Degraded f67c67c badpods=1  at 07:10:14Z
t=93s  health=Degraded f67c67c badpods=1  at 07:11:47Z
t=186s health=Degraded f67c67c badpods=1  at 07:13:20Z
t=197s health=Healthy  b2d7978 badpods=0  at 07:13:31Z
```

```
$ argocd app get quickticket
Sync Status:        Synced to main (b2d7978)
Health Status:      Healthy

$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
events-7d84c7d4bc-mwb5k     1/1     Running   0          34m
gateway-9d8974b44-ll6cz     1/1     Running   0          34m
payments-55c7895fc4-prm95   1/1     Running   0          34m
postgres-78489d7f5f-6zqxz   1/1     Running   0          13h
redis-6fcfb5475d-wft97      1/1     Running   0          13h

$ curl -s localhost:3080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

### Written answer: how long from `git revert` + push to healthy pods

**211 s** — push at 07:10:00Z, `Healthy` at 07:13:31Z. The breakdown matters more than
the number:

| Stage | Time | What dominates it |
|---|---:|---|
| push → ArgoCD notices | ~197 s | the 180 s reconciliation poll |
| sync → pods healthy | ~1 s | deleting a ReplicaSet that never started |
| **total** | **211 s** | **almost entirely polling** |

Practically all of the recovery time was ArgoCD waiting to look at Git. `argocd app sync`
would have cut it to a few seconds, and a repo webhook removes the delay permanently —
polling is the part to fix if this were a real incident.

The rollback also cost **zero downtime**, and not by luck: the gateway pod serving
traffic at the end (`gateway-9d8974b44-ll6cz`, 34 m old) is the *same pod* that was
serving before the bad deploy. The broken ReplicaSet never replaced it, so "recovery"
meant deleting a pod that had never served a request. A real outage would need the bad
image to start and then fail its probes — that is a considerably worse failure mode than
an image that cannot be pulled at all.

---

## Bonus Task — Automated Image Tag Update

### The workflow

```yaml
  update-manifests:
    needs: build
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - uses: actions/checkout@v4

      - name: Point manifests at the images this run just built
        run: |
          for svc in gateway events payments; do
            sed -i "s|image: ghcr.io/.*/quickticket-$svc:.*|image: ghcr.io/$OWNER/quickticket-$svc:$GITHUB_SHA|" "k8s/$svc.yaml"
          done

      - name: Commit and push
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add k8s/
          git diff --cached --quiet && echo "manifests already at $GITHUB_SHA" && exit 0
          git commit -m "ci: update image tags to $GITHUB_SHA [skip ci]"
          git push
```

It is a separate job rather than extra steps on `build`, because `build` is a 3-way
matrix — the tag bump has to happen once, after all three images exist, which is exactly
what `needs: build` expresses.

### Breaking the loop — and the guard that broke itself first

The lab suggests guarding on the commit subject:

```yaml
if: "!startsWith(github.event.head_commit.message, 'ci:')"
```

I used a narrowed version of it, `startsWith(..., 'ci: update image tags')`, and it
immediately misfired on a human commit — my own, named
`ci: update image tags in manifests automatically after build`. The workflow filtered out
the very commit that introduced it:

```
36391047614 completed/skipped  ci: update image tags in manifests automatically after build
```

A commit *subject* is not a reliable signal for "did this pipeline write this?". The
author is:

```yaml
    # Skip the commit this workflow pushes itself, or it would trigger itself forever.
    # Keyed on the author, not the message: a human commit whose subject happens to
    # start with "ci: update image tags" would otherwise silently skip its own build.
    if: github.event.head_commit.author.name != 'github-actions[bot]'
```

Three independent layers end up preventing the loop, which is the right number for
something that would otherwise burn Actions minutes forever:

1. GitHub does not trigger workflows from pushes made with `GITHUB_TOKEN` — the loop is
   structurally impossible by default;
2. `[skip ci]` in the bot's commit subject, honoured natively by Actions;
3. the `if:` guard above, which also documents the intent in the file.

### The full loop, observed

Push `2fed1f9` (a human commit) → all four jobs green:

```
$ gh run view 36391122289 --json conclusion,jobs
{"conclusion":"success","jobs":[
  {"conclusion":"success","name":"build (gateway)"},
  {"conclusion":"success","name":"build (events)"},
  {"conclusion":"success","name":"build (payments)"},
  {"conclusion":"success","name":"update-manifests"}]}
```

CI then wrote its own commit, and **no run was triggered by it**:

```
$ git log origin/main --oneline -3 --format='%h %an: %s'
d343061 github-actions[bot]: ci: update image tags to 2fed1f97e33a71a3e82bf86af17117b5fe257691 [skip ci]
2fed1f9 Masis Davoian: ci: key the self-trigger guard on commit author, not message
11e5026 Masis Davoian: ci: update image tags in manifests automatically after build

$ gh run list --limit 3
36391122289 completed/success  ci: key the self-trigger guard on commit author, not message
36391047614 completed/skipped  ci: update image tags in manifests automatically after build
36390170639 completed/success  Revert "feat: deploy new gateway version"
```

ArgoCD picked up the bot's commit with no human involved, and all three services are now
running the image built from that same commit:

```
$ kubectl get pods -o custom-columns='NAME:.metadata.name,READY:...,IMAGE:...'
NAME                        READY   STATUS    IMAGE
events-65b956f965-9xq4f     true    Running   ghcr.io/r3v1k/quickticket-events:2fed1f97e33a71a3e82bf86af17117b5fe257691
gateway-54d7b967bb-hsj6s    true    Running   ghcr.io/r3v1k/quickticket-gateway:2fed1f97e33a71a3e82bf86af17117b5fe257691
payments-75659bdfd4-hccvj   true    Running   ghcr.io/r3v1k/quickticket-payments:2fed1f97e33a71a3e82bf86af17117b5fe257691
postgres-78489d7f5f-6zqxz   true    Running   postgres:17-alpine
redis-6fcfb5475d-wft97      true    Running   redis:7-alpine

$ argocd app get quickticket
Sync Status:        Synced to main (d343061)
Health Status:      Healthy

$ curl -s localhost:3080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}

$ curl -s localhost:3080/events
[{"id":1,"name":"Go Conference 2026","venue":"Main Hall A",...,"available":100},...]
```

The complete chain, with one human action at the front: **push → 3 images built for two
architectures → tags rewritten in Git by CI → ArgoCD syncs → pods replaced**.

---

## Summary

| What | Result |
|---|---|
| CI runs | 8 triggered — 7 green, 1 correctly skipped, 0 failures; the bot's own commit triggered none |
| Images | 3 services × `linux/amd64` + `linux/arm64`, tagged by commit SHA |
| Detection latency (push → ArgoCD acts) | 141 s / 197 s / 324 s — all one 180 s poll window |
| Rollback via `git revert` | 211 s, zero downtime |
| Drift (`kubectl edit`) | detected in seconds, never reverted, silently lost on next sync |
| Manual `kubectl apply` in this lab | none after 5.3 — every change reached the cluster through Git |
