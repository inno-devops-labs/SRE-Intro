# Lab 5. CI/CD and GitOps: from a push to a running pod

**Student:** Kirill Fadeev
**Email:** ki.fadeev@innopolis.university
**Environment:** WSL2 (kernel 6.18.33.2-microsoft-standard-WSL2, x86_64), Docker Engine 29.7.2 (29.8.0 on the final run, after a desktop auto update), k3d v5.9.0, k3s v1.35.5+k3s1 with containerd 2.2.3-k3s1, kubectl v1.37.0, ArgoCD v3.5.3 (CLI v3.5.3+c9c369e), GitHub Actions on ubuntu-latest

Every number below comes from a capture file taken during the run. Timings on the GitOps loop are client side stamps taken every few seconds against the Application custom resource, read with kubectl rather than through the ArgoCD CLI, so a broken CLI session cannot silently change what a measurement means.

---

## Task 1. CI pipeline and ArgoCD

### 5.1 The workflow

`.github/workflows/ci.yml` triggers on a push to `main`, logs in to ghcr.io with the workflow token, and builds and pushes all three services under the commit SHA.

```yaml
permissions:
  contents: write
  packages: write

      - name: Build and push all three service images
        run: |
          set -euo pipefail
          owner=$(printf '%s' "${{ github.repository_owner }}" | tr '[:upper:]' '[:lower:]')
          for svc in gateway events payments; do
            img="ghcr.io/${owner}/quickticket-${svc}:${{ github.sha }}"
            docker build -t "${img}" "./app/${svc}"
            docker push "${img}"
          done
```

Two choices differ from the snippet in the lab text. The owner is lowercased by the workflow, not pasted in by hand, because ghcr.io rejects a path containing an uppercase letter and the account is `KORTEZZ12`. And `permissions:` is set explicitly at the top of the file instead of inheriting the default, which is wider than this job needs.

No `:latest` tag is pushed. Only the commit SHA is, so there is exactly one image per commit and a rollback has a tag to roll back to.

```plaintext
$ git log --oneline -1 8782cb7
8782cb7 ci: add CI pipeline for QuickTicket
 .github/workflows/ci.yml | 52 ++++++++++++++++++++++++++++++++++++++++++++++++

name        CI
event       push
status      completed
conclusion  success
started     2026-09-20T16:20:52Z
updated     2026-09-20T16:22:51Z
url         https://github.com/KORTEZZ12/SRE-Intro/actions/runs/35522424041
duration    119 s

job build: success
   1. Set up job                                             success
   2. Run actions/checkout@v4                                success
   3. Log in to GitHub Container Registry                    success
   4. Build and push all three service images                success
   5. Show what was published                                success
```

### 5.2 The images

The `gh` CLI was not authenticated on this machine, so the same endpoint `gh api user/packages?package_type=container` reads was called directly with the classic PAT:

```plaintext
$ curl -H "Authorization: Bearer $GHCR_PAT" https://api.github.com/user/packages?package_type=container
container packages: 3
quickticket-gateway      visibility=public    created=2026-09-20T16:21:28Z
    https://github.com/users/KORTEZZ12/packages/container/package/quickticket-gateway
quickticket-events       visibility=public    created=2026-09-20T16:22:35Z
quickticket-payments     visibility=public    created=2026-09-20T16:22:47Z
```

GitHub's own index says the packages exist. The registry is a separate service and was asked separately, by tag, for the digest it would serve:

```plaintext
  quickticket-gateway:8782cb7761b0   HTTP/2 200   sha256:530fa97af721df899d24a81f264df5b659e9c3fe4ee3c535e1d3e9ac41a8d75a
  quickticket-events:8782cb7761b0    HTTP/2 200   sha256:1dc167544d5b5be47b06f7ba0b277b37cc8aaf99ebdbb74dbd654f551def66b3
  quickticket-payments:8782cb7761b0  HTTP/2 200   sha256:a15f3c54fcddec5b41e43be9f47b97ec237a719ebbd0b5cf2b0dff807ea4e265
```

### 5.3 The manifests, and whether the pull secret does anything

The five manifests from Lab 4 moved from locally imported images to registry ones:

```diff
-          image: quickticket-gateway:v1
-          imagePullPolicy: Never
+          image: ghcr.io/kortezz12/quickticket-gateway:8782cb7761b00a750b49ea25256af85df2d2d9f3
+          imagePullPolicy: Always
+      imagePullSecrets:
+        - name: ghcr-secret
```

The lab's Common Pitfalls section states that ghcr.io packages are private by default and that a pull secret is therefore required. The listing above already says `visibility=public`, so the claim was worth testing rather than repeating. What the run measured is the visibility field and the registry's answer to an anonymous request; the reason the packages came out public, that the fork they were published from is public, is a reading of GitHub's inheritance rule and not something this lab measured.

The registry was asked for each image with no credential at all, using an anonymous pull token:

```plaintext
  anonymous GET quickticket-gateway:a2dfee5f0663  -> HTTP 200
  anonymous GET quickticket-events:a2dfee5f0663   -> HTTP 200
  anonymous GET quickticket-payments:a2dfee5f0663 -> HTTP 200
```

That settles whether a secret is needed. It leaves open what a secret does when it is present, so the cluster was put through three states with nothing else changed: no secret, a secret holding a credential the registry rejects, and no secret again.

```plaintext
--- no pull secret at all ---
$ kubectl get secret ghcr-secret
Error from server (NotFound): secrets "ghcr-secret" not found
NAME                       READY   IMAGE
gateway-59f9c58f7c-jssxm   true    ghcr.io/kortezz12/quickticket-gateway:a2dfee5f0663...

Warning  FailedToRetrieveImagePullSecret  2m29s (x28 over 62m)  kubelet
    Unable to retrieve some image pull secrets (ghcr-secret); attempting to pull the image may not succeed.
```

```plaintext
--- a secret whose password is not a token ---
$ kubectl create secret docker-registry ghcr-secret --docker-server=ghcr.io \
    --docker-username=kortezz12 --docker-password=<a string that is not a token>
$ kubectl rollout restart deploy/gateway
  t+0.13s   gateway-59f9c58f7c-jssxm 1/1 Running   gateway-6b475c767d-slxqf 0/1 ContainerCreating
  t+5.30s   gateway-59f9c58f7c-jssxm 1/1 Running   gateway-6b475c767d-slxqf 0/1 ErrImagePull
  t+20.82s  gateway-59f9c58f7c-jssxm 1/1 Running   gateway-6b475c767d-slxqf 0/1 ImagePullBackOff
```

The kubelet's own reason for the new pod, next to the old one it left alone:

```plaintext
NAME                       READY   REASON
gateway-59f9c58f7c-jssxm   true    <none>
gateway-6b475c767d-slxqf   false   ImagePullBackOff
```

```plaintext
--- secret deleted, nothing else changed ---
  t+5.34s   gateway-59f9c58f7c-jssxm 1/1 Running   gateway-5bdbf65bcd-rfc2q 0/1 Running
  t+10.50s  gateway-5bdbf65bcd-rfc2q 1/1 Running
in-cluster GET /health -> 200 {"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

> A pull secret naming a credential the registry rejects is worse than no pull secret at all. With no secret the kubelet logs a warning and falls back to an anonymous pull, which succeeds because the package is public. With a bad secret it presents those credentials instead, the pull fails, and it never falls back to the anonymous path, so a public image becomes unpullable. The failure looks like a permissions problem with the registry and is actually a problem with the thing that was added to fix permissions.

The Service stayed up throughout: the old pod stayed ready while the new one sat in `ImagePullBackOff`, and the in-cluster health check answered 200 during the failure. The step that was supposed to print the endpoints object by name failed on its own JSON parsing, so that listing is not reported here; the readiness column above and the 200 are what the capture supports.

### 5.4 Installing ArgoCD

The command from the lab was run first, unchanged:

```bash
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```

```plaintext
manifest: 34050 lines, 59 objects
configmap/argocd-gpg-keys-cm configured
... 29 more objects ...
networkpolicy.networking.k8s.io/argocd-server-network-policy configured
The CustomResourceDefinition "applicationsets.argoproj.io" is invalid:
    metadata.annotations: Too long: may not be more than 262144 bytes
exit code of that apply: 1
```

Every object applied except one, and the exit code is easy to miss in a terminal that has just scrolled thirty lines of output. That error reproduced on every run of this step. The documented cause is the client side apply path: it stores a full copy of the object in the `kubectl.kubernetes.io/last-applied-configuration` annotation, annotations are capped at 256 KiB, and this CRD's schema is larger than that.

Because a non-zero exit code is not something the lab's steps check, a step was added that asks the cluster which CRDs exist instead of trusting the apply:

```plaintext
  applications.argoproj.io present
  appprojects.argoproj.io present
  applicationsets.argoproj.io present
applications.argoproj.io                       2026-09-23T20:58:10Z
applicationsets.argoproj.io                    2026-09-23T20:58:13Z
appprojects.argoproj.io                        2026-09-23T20:58:11Z
```

All three exist, with creation timestamps inside that same run, so the non-zero exit did not always mean the CRD was absent. On the first install it did. That run's capture shows what an unchecked exit code costs:

```plaintext
argocd-applicationset-controller-7f95b9cd7c-crdxg   0/1   CrashLoopBackOff   4 (84s ago)   5h23m
argocd-applicationset-controller-7f95b9cd7c-crdxg: CrashLoopBackOff: back-off 2m40s restarting
    failed container=argocd-applicationset-controller
```

> Five hours and twenty three minutes of restarts on one pod, while `kubectl get pods -n argocd` showed the other six Running and the install looked finished. The missing piece was one CRD out of three, and the command that failed to create it returned a code that no step in the lab reads. A partial install that reports success is harder to notice than one that stops, because everything a person would think to look at is green.

After the fix the whole namespace came up, and the secret every other component mounts appeared once the redis pod's init container had its image:

```plaintext
argocd-redis secret appeared after 0.20s of waiting
  t+0.15s  7/7 ready  notready: -
ArgoCD ready 5.70s after apply
image: quay.io/argoproj/argocd:v3.5.3
```

### 5.5 The Application

```bash
argocd app create quickticket \
  --repo https://github.com/KORTEZZ12/SRE-Intro.git \
  --path k8s --dest-server https://kubernetes.default.svc \
  --dest-namespace default --sync-policy automated
```

```plaintext
$ argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Source:
- Repo:             https://github.com/KORTEZZ12/SRE-Intro.git
  Path:             k8s
Sync Policy:        Automated
Sync Status:        Synced to  (6b947f5)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME           STATUS  HEALTH
       Service     default    payments       Synced  Healthy
       ConfigMap   default    postgres-seed  Synced
       Service     default    events         Synced  Healthy
       Service     default    gateway        Synced  Healthy
       Service     default    postgres       Synced  Healthy
       Service     default    redis          Synced  Healthy
apps   Deployment  default    events         Synced  Healthy
apps   Deployment  default    gateway        Synced  Healthy
apps   Deployment  default    payments       Synced  Healthy
apps   Deployment  default    postgres       Synced  Healthy
apps   Deployment  default    redis          Synced  Healthy
```

One object here has no counterpart in the Lab 4 manifests. `postgres-seed` is a ConfigMap carrying `app/seed.sql` into `/docker-entrypoint-initdb.d`, added because Lab 4 filled the database with a `kubectl exec` that no manifest records. Anything ArgoCD does not know about is not part of the desired state, and the first sync that replaces the postgres pod throws it away. With the seed in Git the catalogue comes back on its own:

```plaintext
/usr/local/bin/docker-entrypoint.sh: running /docker-entrypoint-initdb.d/01-seed.sql
events returned by the service: 5
names: ['Go Conference 2026', 'Python Workshop', 'SRE Meetup', 'Kubernetes Deep Dive', 'Cloud Native Summit']
```

### 5.6 The loop, with nothing applied by hand

A label was added to the gateway Deployment in Git and pushed. No `kubectl apply` was run from that point on.

```diff
 metadata:
   name: gateway
   labels:
     app: gateway
+    version: "v2"
```

```plaintext
T_GIT_PUSH 01:37:38.875   commit 8c911c6156edae5e778dc6b0a60b8bd98cfbe207
  t+0.12s    OutOfSync Healthy 6ee34c82aa64
  ... 41 samples, unchanged ...
  t+206.51s  OutOfSync Healthy 6ee34c82aa64
  t+211.66s  OutOfSync Healthy 8c911c6156ed
ArgoCD reached revision 8c911c6156ed 211.66s after the push, unattended

$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2
lab5-check label in the cluster: s1789943858   (pushed: s1789943858)
NAME      READY   UP-TO-DATE   AVAILABLE   AGE   LABELS
gateway   1/1     1            1           20m   app=gateway,lab5-check=s1789943858,version=v2
```

211.66 seconds against ArgoCD's documented three minute poll interval. Where the extra half minute went was not measured, and the most likely reading is that the push landed partway into an interval that was already running. What the samples do show is that the delay was in noticing: the revision changed between one five second sample and the next.

### 5.7 What happens when somebody runs kubectl edit

The Application was created with `--sync-policy automated`, which turns on automatic sync but leaves self-heal off. That is the default the lab's command produces, so it is the first case measured. `GATEWAY_TIMEOUT_MS` is `5000` in Git; it was changed in the cluster and left alone for four minutes.

```plaintext
$ kubectl set env deploy/gateway GATEWAY_TIMEOUT_MS=9999
  t+0.12s    Synced    Healthy      GATEWAY_TIMEOUT_MS=9999
  t+10.34s   OutOfSync Progressing  GATEWAY_TIMEOUT_MS=9999
  t+20.57s   OutOfSync Healthy      GATEWAY_TIMEOUT_MS=9999
  ...
  t+235.20s  OutOfSync Healthy      GATEWAY_TIMEOUT_MS=9999
after 240s the manual value is still 9999

sync status: OutOfSync
  Deployment/gateway -> OutOfSync
```

ArgoCD noticed within about ten seconds and then did nothing for the remaining 230. Turning self-heal on and repeating the same edit:

```plaintext
$ argocd app set quickticket --self-heal
syncPolicy: {"automated": {"selfHeal": true}}

  t+0.12s  Synced    Healthy      GATEWAY_TIMEOUT_MS=9999
  t+6.58s  OutOfSync Progressing  GATEWAY_TIMEOUT_MS=9999
  t+9.80s  OutOfSync Progressing  GATEWAY_TIMEOUT_MS=5000
self-heal put 5000 back 9.80s after the manual edit
```

Deleting a managed object behaves the same way:

```plaintext
$ kubectl delete svc payments
service "payments" deleted from default namespace
svc/payments was recreated 13.04s after the delete
NAME       TYPE        CLUSTER-IP     PORT(S)    AGE
payments   ClusterIP   10.43.248.97   8082/TCP   3s
```

A second run of the same two experiments on a rebuilt cluster gave 3.44 seconds and 3.37 seconds, so the figures above are an upper bound, not a constant: what is being measured is how soon the next reconciliation happens to land.

**What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?**

The edit takes effect immediately, because ArgoCD is not in the request path and the API server has no reason to refuse it. What happens next depends on one field in the Application. With automated sync alone, ArgoCD marks the Application `OutOfSync` within about ten seconds, names the specific resource that drifted, and leaves the cluster exactly as the person left it; the change survives until the next commit to Git, which overwrites it without mentioning that it did. With `selfHeal: true` the same drift is reverted in under ten seconds, and a deleted object is recreated just as fast.

Neither setting is correct on its own. Self-heal off keeps an operator's emergency edit alive through an incident and turns the dashboard into the alarm, at the cost of a cluster that quietly disagrees with Git. Self-heal on makes Git the only way to change anything, which is the point of GitOps, and also means an urgent manual fix is undone about nine seconds after it is made unless the person also pushes it. The honest description is that ArgoCD detects drift either way, and `selfHeal` chooses who wins, so the setting should be decided by whether the team's incident procedure expects to type kubectl at all.

---

## Task 2. Rollback through Git

### 5.8 A tag that does not exist

`k8s/gateway.yaml` was pointed at `quickticket-gateway:does-not-exist` and pushed. Before the push, two fields were recorded because they turned out to explain the result:

```plaintext
progressDeadlineSeconds on the gateway Deployment: 600
rollingUpdate maxUnavailable/maxSurge: 25%/25% (defaults, at replicas=1)
```

```plaintext
T_BAD 01:51:07.718  commit d3c2dc964e87cabc84902b93b0e85a308dbd95b5
  t+0.13s    Synced Healthy 8c911c6156ed
  ...
  t+150.17s  Synced Healthy 8c911c6156ed
  t+155.35s  OutOfSync Healthy d3c2dc964e87
ArgoCD was on the bad revision after 155.35s
```

```plaintext
NAME                        READY   STATUS             RESTARTS   AGE
events-c5b558f5c-rt9nn      1/1     Running            0          32m
gateway-5fd64847b5-5gk98    1/1     Running            0          8m17s
gateway-87bf8b4df-fjzvm     0/1     ImagePullBackOff   0          18s
payments-5f4fbffdbc-g7x95   1/1     Running            0          32m

NAME                 DESIRED   READY    IMAGE
gateway-5fd64847b5   1         1        ghcr.io/kortezz12/quickticket-gateway:8782cb7761b0...
gateway-769844dcfd   0         <none>   ghcr.io/kortezz12/quickticket-gateway:8782cb7761b0...
gateway-87bf8b4df    1         <none>   ghcr.io/kortezz12/quickticket-gateway:does-not-exist
```

An in-cluster client polled `http://gateway:8080/events` twice a second for the whole experiment and printed only transitions. It printed one line:

```plaintext
22:51:02.554 probe#1 -> 200
```

```plaintext
ready addresses  : ['10.42.0.25']
notReady addresses: ['10.42.0.27']
```

> Nothing was ever down. At `replicas: 1` the default `maxUnavailable: 25%` rounds down to zero, so Kubernetes refuses to remove the working pod until the replacement is ready, and the replacement is never going to be ready. What broke was the ability to ship anything else, and only that. The failure mode is a kind one and a quiet one: with no check on rollout status, every dashboard stays green while the deployment pipeline sits stuck behind a tag that will never resolve.

ArgoCD reported `Degraded` once the Deployment gave up on the rollout:

```plaintext
$ argocd app get quickticket
Sync Status:        Synced to  (d3c2dc9)
Health Status:      Degraded

apps   Deployment  default    gateway   Synced  Degraded   deployment.apps/gateway configured

Available    True   MinimumReplicasAvailable  Deployment has minimum availability.
Progressing  False  ProgressDeadlineExceeded  ReplicaSet "gateway-87bf8b4df" has timed out progressing.
```

ArgoCD does not decide this for itself. It reads the Deployment's own `Progressing` condition, and that condition only turns false once `progressDeadlineSeconds` elapses without the new ReplicaSet making progress. On the gateway Deployment that field is 600:

```plaintext
progressDeadlineSeconds on the gateway Deployment: 600
```

At ten minutes of default deadline, a rollout that was visibly failing eighteen seconds in still counts as in progress for the rest of that window. The pod was in `ImagePullBackOff` and the tag was never going to resolve, and every health field above the Deployment stayed green until the deadline ran out. Anything that watches `Application.status.health` for a verdict inherits that delay; watching the pod's container state does not.

### 5.9 Reverting

```bash
git revert HEAD --no-edit
git push origin main
```

```plaintext
T_REVERT 13:46:38.909  commit a4173073c578915ad1370dcdcb338ae124fda2cb

$ git log --oneline -3
a417307 Revert "feat: deploy new gateway version"
d3c2dc9 feat: deploy new gateway version
8c911c6 feat: stamp the gateway deployment for a GitOps round trip
```

```plaintext
  t+0.11s    Synced    Degraded d3c2dc964e87
  ...
  t+170.56s  Synced    Degraded d3c2dc964e87
  t+175.74s  OutOfSync Degraded a4173073c578
ArgoCD was on the revert revision 175.74s after the push
  t+179.54s  OutOfSync Degraded a4173073c578
  t+184.69s  OutOfSync Degraded a4173073c578
  t+189.84s  Synced    Healthy  a4173073c578
Synced and Healthy 189.84s after git revert + push
```

```plaintext
NAME                        READY   STATUS    RESTARTS   AGE
gateway-5fd64847b5-5gk98    1/1     Running   0          12h
$ kubectl get deploy gateway -o jsonpath='{...image}'
ghcr.io/kortezz12/quickticket-gateway:8782cb7761b00a750b49ea25256af85df2d2d9f3

$ argocd app get quickticket
Sync Status:        Synced to  (a417307)
Health Status:      Healthy
```

**How long from `git revert` and push to pods being healthy again?**

189.84 seconds, of which 175.74 was ArgoCD waiting for its own poll interval to come round and 14.10 was the cluster doing the work. Nobody typed a kubectl command and nobody needed cluster credentials; the rollback was a commit.

The number is dominated by a polling interval, which means it is a configuration choice rather than a property of the system. A webhook from GitHub would cut most of the 175 seconds, and `argocd app sync` cuts all of it. The 189 seconds also cover the whole of the decision: the previous state was already a commit that had run, so reverting it needed no reconstruction of what to go back to.

---

## Bonus task. CI writes the image tag back to Git

Two steps were added to the workflow after the push, and a guard was added to the job:

```yaml
    if: ${{ !startsWith(github.event.head_commit.message, 'ci:') }}

      - name: Update image tags in manifests
        run: |
          for svc in gateway events payments; do
            sed -i -E \
              "s#^([[:space:]]*image:[[:space:]]*)ghcr\.io/[^[:space:]]*/quickticket-${svc}:.*\$#\1ghcr.io/${owner}/quickticket-${svc}:${{ github.sha }}#" \
              "k8s/${svc}.yaml"
          done

      - name: Commit and push manifest update
        run: |
          git add k8s/
          if git diff --cached --quiet; then exit 0; fi
          git commit -m "ci: update image tags to ${{ github.sha }}"
          git push origin HEAD:main
```

The substitution is anchored on the service name so the three manifests cannot be cross written, and on `image:` so that `imagePullPolicy` on the next line is untouched.

The loop closed twice, and the history shows both turns:

```plaintext
$ git log --oneline -6 origin/main
6b947f5 ci: update image tags to a2dfee5f0663bd645857f685c294c0646bdb5f4b
a2dfee5 feat: stamp the gateway deployment for a GitOps round trip
200b58e ci: update image tags to d300862309bb539f5f735671cbf51d3a896e557c
d300862 feat(ci): update k8s image tags from the build job
f86f617 feat: stamp the gateway deployment for a GitOps round trip
a417307 Revert "feat: deploy new gateway version"

6b947f5  github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>
a2dfee5  Kirill Fadeev <mesh04ck+ss@gmail.com>
200b58e  github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>
d300862  Kirill Fadeev <mesh04ck+ss@gmail.com>
```

```plaintext
$ git show 6b947f5
 k8s/events.yaml   | 2 +-
 k8s/gateway.yaml  | 2 +-
 k8s/payments.yaml | 2 +-
-          image: ghcr.io/kortezz12/quickticket-events:d300862309bb539f5f735671cbf51d3a896e557c
+          image: ghcr.io/kortezz12/quickticket-events:a2dfee5f0663bd645857f685c294c0646bdb5f4b
-          image: ghcr.io/kortezz12/quickticket-gateway:d300862309bb539f5f735671cbf51d3a896e557c
+          image: ghcr.io/kortezz12/quickticket-gateway:a2dfee5f0663bd645857f685c294c0646bdb5f4b
-          image: ghcr.io/kortezz12/quickticket-payments:d300862309bb539f5f735671cbf51d3a896e557c
+          image: ghcr.io/kortezz12/quickticket-payments:a2dfee5f0663bd645857f685c294c0646bdb5f4b
```

The build that produced it, on the commit that did trigger one:

```plaintext
conclusion success  duration 53 s
url        https://github.com/KORTEZZ12/SRE-Intro/actions/runs/36057046075
```

Neither CI authored commit started a build of its own:

```plaintext
ci: commit 6b947f5bd81265f54fa -> workflow runs: 0
ci: commit 200b58ec38a8235e0c7 -> workflow runs: 0
```

> Zero runs, and no skipped run either: GitHub created nothing at all, because it does not trigger workflows on a push made with `GITHUB_TOKEN`. The `if:` guard never had to fire. So the loop has two independent brakes and only one of them appears in the YAML, which means anyone reading the workflow will credit the guard for the protection. That misreading costs nothing until the day the push switches to a personal access token, at which point the guard becomes the only brake left and had better be correct.

Nothing was applied by hand, and the tag CI wrote is the tag the cluster runs:

```plaintext
in Git:
26:  image: ghcr.io/kortezz12/quickticket-gateway:a2dfee5f0663bd645857f685c294c0646bdb5f4b
49:  image: ghcr.io/kortezz12/quickticket-events:a2dfee5f0663bd645857f685c294c0646bdb5f4b
24:  image: ghcr.io/kortezz12/quickticket-payments:a2dfee5f0663bd645857f685c294c0646bdb5f4b

in the cluster:
NAME       IMAGE
events     ghcr.io/kortezz12/quickticket-events:a2dfee5f0663bd645857f685c294c0646bdb5f4b
gateway    ghcr.io/kortezz12/quickticket-gateway:a2dfee5f0663bd645857f685c294c0646bdb5f4b
payments   ghcr.io/kortezz12/quickticket-payments:a2dfee5f0663bd645857f685c294c0646bdb5f4b

sync    Synced 6b947f5bd812
health  Healthy
deploy history, newest last:
  id=0 2026-09-23T21:03:43Z  a4173073c578
  id=1 2026-09-23T21:55:34Z  f86f617dac27
  id=2 2026-09-23T22:13:03Z  200b58ec38a8
  id=3 2026-09-24T20:48:58Z  6b947f5bd812

200 {"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
events: 5
```

One consequence of this arrangement caught the measurement, not the cluster. After the bonus step was installed, a push of commit `a2dfee5` was followed within a minute by CI's `6b947f5`, and the deploy history above shows ArgoCD going from `200b58e` straight to `6b947f5`. It never deployed `a2dfee5` as a revision, although the content of that commit reached the cluster inside the CI written one, which the round trip label confirmed:

```plaintext
lab5-check label in the cluster: s1790282739   (pushed: s1790282739)
```

> Once CI is allowed to commit, a developer's commit stops being the revision the cluster reports. The deployed revision is the CI follow-up, which contains the developer's change plus the tag update, and a check that waits for the developer's SHA to appear as the synced revision waits forever while the change is already running. Verification after a deploy has to compare content, not identifiers.

---

## Results

| Check | Result |
|---|---|
| CI workflow committed | `.github/workflows/ci.yml`, commit 8782cb7, triggers on push to main |
| Actions run green | run 35522424041, conclusion success, 119 s, 5 of 5 steps success |
| Images in ghcr.io | 3 packages, `visibility=public`, digests verified by asking the registry by tag |
| Pull secret necessary? | no: anonymous manifest GET returns 200 for all three |
| Pull secret harmful? | yes if wrong: ErrImagePull at 5.30 s, ImagePullBackOff at 20.82 s, recovered 10.50 s after deleting it |
| ArgoCD installed | v3.5.3, 7 pods ready 5.70 s after apply, CRD presence checked because the install exits 1 |
| Application created | Synced and Healthy, 11 objects including the seed ConfigMap |
| Git change synced to cluster | `version: v2` live 211.66 s after the push, unattended |
| `kubectl edit` answer | OutOfSync in 10.34 s, uncorrected after 240 s; with self-heal reverted in 9.80 s |
| Deleted Service | recreated 13.04 s after the delete, self-heal on |
| Bad deploy detected | ArgoCD Degraded, `ProgressDeadlineExceeded`, pod in ImagePullBackOff, sync took 155.35 s |
| Clients affected by the bad deploy | none: in-cluster prober saw 200 throughout, old ReplicaSet kept serving |
| `git revert` recovery | Synced and Healthy 189.84 s after the push, 175.74 s of it poll interval |
| Bonus loop | 2 CI authored commits by github-actions[bot], 0 workflow runs on them, cluster runs the CI written tag |

The pattern running through this lab is that the thing which reports and the thing which holds state are two different systems, and they disagree. `kubectl apply` left one CRD out of three and said so in an exit code nobody reads. The pull secret added to make images pullable was the reason they stopped being pullable. A deploy that every status field called broken served every request it was given. A commit that was pushed, built and deployed never appeared as the revision ArgoCD reported. None of those would have been visible from the side that does the reporting, and all of them were one query to the receiving side away. That is the whole reason the numbers above mean anything: each was read off the system being changed, never off the tool doing the changing.
