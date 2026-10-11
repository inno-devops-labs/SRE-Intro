# Lab 7 — Progressive Delivery: Canary Deployments

**Branch:** `feature/lab7` · **Cluster:** k3d `quickticket` (k3s v1.35.5, single node) · **Date:** 2026-09-22

| Task | Status |
|---|---|
| Task 1: manual canary (6 pts) | ✅ done, for real |
| Task 2: multi-step canary with observation (4 pts) | ✅ done, for real |
| Bonus: automated canary analysis (2 pts) | ✅ done, for real (auto-promote **and** auto-abort) |

Every command block below is output that was actually produced on this cluster. Three places
where I deliberately deviated from the lab text are boxed and explained (`labs/lab7/prometheus.yaml`,
`labs/lab7/loadgen.yaml`, and the §B.5 "bad version" recipe). Nothing is reconstructed from memory.

`kubectl argo rollouts get rollout --watch` redraws the whole screen on every update, so the
captures below were taken with `--no-color` into a file and then split into frames, collapsing
consecutive frames whose only difference was the `AGE` column. The frame text itself is untouched.

---

## Decisions I had to make before starting (inherited collisions)

### 1. `labs/lab7/prometheus.yaml` was **not** applied — the deployed Prometheus is a superset

Lab 6 already deployed an in-cluster Prometheus to namespace `monitoring` from
`monitoring/k8s/prometheus.yaml`, using the **same object names** the lab's file uses
(`ConfigMap/prometheus-config`, `Deployment/prometheus`, `Service/prometheus`). Applying the lab's
file would have overwritten it. Diffing the two:

```text
$ diff labs/lab7/prometheus.yaml monitoring/k8s/prometheus.yaml
```

- the **`gateway` scrape job is byte-identical**, including the one rule Lab 7 actually depends on:
  `__meta_kubernetes_pod_label_rollouts_pod_template_hash → rs_hash`;
- the deployed one **additionally** scrapes `events` and `payments`, adds
  `rule_files: /etc/prometheus/rules/rules.yml` (the Lab 3 SLO recording rules), a 2-day TSDB,
  `--web.enable-lifecycle` and resource requests/limits.

So applying the lab's file would have silently **deleted** the events/payments jobs and the
recording rules, which the Lab 3 golden-signals dashboard and the Lab 6 runbooks read. I reused
what was running instead, and checked that the label the AnalysisTemplate needs is really there
(§B.1 below) rather than just assuming it.

### 2. `labs/lab7/loadgen.yaml` collides by name with Lab 6's load generator

Both create `Deployment/loadgen` in `default`. I used **Lab 7's** for the whole lab because it's
the right tool for the job: ~5 rps of `/events` + `/health` against `svc/gateway`, which gives a
statistically usable sample for a 20% traffic split in 60 seconds. Lab 6's generator runs at
~1.1 rps split three ways, which would have given ~8 canary requests per minute, way too few.

Applying Lab 7's file replaced Lab 6's Deployment. At the end of the lab (§B.6) I deleted it and
re-applied `monitoring/k8s/loadgen.yaml`, so Lab 6's generator (and the traffic its Grafana alert
rules depend on) is back. Consequence during the lab: no purchase traffic for ~30 minutes, so the
payments-path panels were flat. Nothing was left broken.

### 3. ArgoCD *does* fight a Deployment→Rollout conversion — see §7.2

---

## Task 1 — Manual Canary Deployment (6 pts)

### 7.1 Install Argo Rollouts

The controller install needed `--server-side`. The plain `kubectl apply` succeeded for 3 of the 5
CRDs and then failed on the two big ones, the same limit that bit the ArgoCD install in Lab 5:

```text
$ kubectl create namespace argo-rollouts
namespace/argo-rollouts created

$ kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
customresourcedefinition.apiextensions.k8s.io/analysistemplates.argoproj.io created
...
deployment.apps/argo-rollouts created
Error from server (Invalid): error when creating "...install.yaml": CustomResourceDefinition.apiextensions.k8s.io "analysisruns.argoproj.io" is invalid: metadata.annotations: Too long: may not be more than 262144 bytes
Error from server (Invalid): error when creating "...install.yaml": CustomResourceDefinition.apiextensions.k8s.io "rollouts.argoproj.io" is invalid: metadata.annotations: Too long: may not be more than 262144 bytes
```

Client-side apply stores the whole manifest in the `kubectl.kubernetes.io/last-applied-configuration`
annotation, and these two CRDs are larger than the 256 KiB annotation limit. Server-side apply
tracks field ownership in `metadata.managedFields` instead of an annotation, so it has no such
problem:

```text
$ kubectl apply --server-side --force-conflicts -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
customresourcedefinition.apiextensions.k8s.io/analysisruns.argoproj.io serverside-applied
customresourcedefinition.apiextensions.k8s.io/analysistemplates.argoproj.io serverside-applied
customresourcedefinition.apiextensions.k8s.io/clusteranalysistemplates.argoproj.io serverside-applied
customresourcedefinition.apiextensions.k8s.io/experiments.argoproj.io serverside-applied
customresourcedefinition.apiextensions.k8s.io/rollouts.argoproj.io serverside-applied
serviceaccount/argo-rollouts serverside-applied
clusterrole.rbac.authorization.k8s.io/argo-rollouts serverside-applied
clusterrole.rbac.authorization.k8s.io/argo-rollouts-aggregate-to-admin serverside-applied
clusterrole.rbac.authorization.k8s.io/argo-rollouts-aggregate-to-edit serverside-applied
clusterrole.rbac.authorization.k8s.io/argo-rollouts-aggregate-to-view serverside-applied
clusterrolebinding.rbac.authorization.k8s.io/argo-rollouts serverside-applied
configmap/argo-rollouts-config serverside-applied
secret/argo-rollouts-notification-secret serverside-applied
service/argo-rollouts-metrics serverside-applied
deployment.apps/argo-rollouts serverside-applied

$ kubectl wait --for=condition=Available deployment/argo-rollouts -n argo-rollouts --timeout=180s
deployment.apps/argo-rollouts condition met

$ kubectl get pods -n argo-rollouts
NAME                             READY   STATUS    RESTARTS   AGE
argo-rollouts-69645d4879-5g6dz   1/1     Running   0          12s
```

Plugin installed per-user (Option B, no sudo):

```text
$ mkdir -p ~/.local/bin
$ curl -fsSL -o ~/.local/bin/kubectl-argo-rollouts \
    https://github.com/argoproj/argo-rollouts/releases/latest/download/kubectl-argo-rollouts-linux-amd64
$ chmod +x ~/.local/bin/kubectl-argo-rollouts
$ export PATH=~/.local/bin:$PATH
```

**Proof-of-work item 1 (`kubectl argo rollouts version`):**

```text
$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.10.0+d90700a
  BuildDate: 2026-08-27T15:22:01Z
  GitCommit: d90700ae8d71d141561f0c546e19f999bb335cbd
  GitTreeState: clean
  GoVersion: go1.26.7
  Compiler: gc
  Platform: linux/amd64
```

(v1.10.0 ≥ the v1.9.0 the lab asks for.)

### 7.2 Convert gateway Deployment → Rollout

`k8s/gateway.yaml` changed in exactly four places. Everything under `template:` (probes,
`imagePullSecrets`, the ghcr.io image reference, resources, the Prometheus scrape annotations) is
unchanged from Lab 5:

| # | Change |
|---|---|
| 1 | `apiVersion: apps/v1` → `apiVersion: argoproj.io/v1alpha1` |
| 2 | `kind: Deployment` → `kind: Rollout` |
| 3 | `replicas: 1` → `replicas: 5` |
| 4 | new `spec.strategy.canary.steps` |

The Service was left with selector `app: gateway` (no pod-template-hash) on purpose, so stable and
canary pods are both endpoints and `kube-proxy` does the splitting.

```text
$ kubectl delete deployment gateway
deployment.apps "gateway" deleted from default namespace

$ kubectl apply -f k8s/gateway.yaml
rollout.argoproj.io/gateway created
service/gateway configured

$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/vanady39/quickticket-gateway:ae1a4579f016fd3e7b68ad97a4b998660af38d3c (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS     AGE  INFO
⟳ gateway                            Rollout     ✔ Healthy  20s
└──# revision:1
   └──⧉ gateway-594974b75c           ReplicaSet  ✔ Healthy  20s  stable
      ├──□ gateway-594974b75c-5p9g7  Pod         ✔ Running  20s  ready:1/1
      ├──□ gateway-594974b75c-bvnv6  Pod         ✔ Running  20s  ready:1/1
      ├──□ gateway-594974b75c-kkpc8  Pod         ✔ Running  20s  ready:1/1
      ├──□ gateway-594974b75c-ld2c9  Pod         ✔ Running  20s  ready:1/1
      └──□ gateway-594974b75c-xwn7l  Pod         ✔ Running  20s  ready:1/1
```

> #### ⚠️ ArgoCD resurrected the Deployment 90 seconds later
>
> This is the interesting failure of the lab, and the lab text doesn't warn about it. Lab 5's
> Application has `syncPolicy: {automated: {selfHeal: false}}`, and Lab 6's handoff records the
> rule of thumb "an imperative change survives until the next sync of *any* revision". Turns out
> a **deleted** resource is not the same case as a **drifted** one. With no new commit on the
> GitOps remote (`main` was still at Lab 6's `52151d9`, as the push at the bottom of this
> submission confirms, `52151d9..38de42d`), ArgoCD ran an *automated* sync anyway and put the
> missing Deployment back.
>
> ```text
> $ kubectl get deploy gateway
> NAME      READY   UP-TO-DATE   AVAILABLE   AGE
> gateway   1/1     1            1           27s          # ← I deleted this 90 s earlier
>
> $ kubectl -n argocd get app quickticket -o jsonpath='{range .status.history[*]}{.id}{"\t"}{.revision}{"\t"}{.deployedAt}{"\n"}{end}' | tail -3
> 7	fe611dc6bc3e803d704259e5f9be5e0f9faa44e9	2026-09-22T08:36:21Z
> 8	ad4627bfc9b7f9ddc3dac95d678aeebd828d443f	2026-09-22T08:57:29Z
> 9	52151d92bc28e67650b9eeaaff050ef3547f648d	2026-09-22T09:28:30Z    # ← the sync that put the Deployment back
>
> $ kubectl -n argocd get app quickticket -o jsonpath='{.status.operationState.phase}{" initiatedBy.automated="}{.status.operationState.operation.initiatedBy.automated}{"\n"}'
> Succeeded initiatedBy.automated=true
> ```
>
> The resurrected Deployment's ReplicaSet was happily creating a 6th pod with `app: gateway`,
> which `svc/gateway` was routing to. That would have polluted every traffic-split measurement
> in this lab with a pod Argo Rollouts knew nothing about.
>
> **What I did about it**, deliberately: switched the Application to manual sync for the duration of
> the lab, deleted the Deployment again, did all the canary work with `kubectl apply`, and at the
> end committed the Rollout, pushed it to the in-cluster GitOps remote, let ArgoCD reconcile, and
> **re-enabled `automated: {selfHeal: false}`**. Final state is `Synced` / `Healthy` on the new
> revision (see "GitOps reconciliation" at the bottom).
>
> ```text
> $ kubectl -n argocd patch app quickticket --type merge -p '{"spec":{"syncPolicy":{"automated":null}}}'
> application.argoproj.io/quickticket patched
> $ kubectl delete deployment gateway
> deployment.apps "gateway" deleted from default namespace
> ```
>
> The alternative, push the Rollout to Git *first* so ArgoCD's desired state never contains a
> Deployment, also works, and it's what I'd do in production. I chose manual sync because I
> wanted the intermediate manifests (`§7.2` steps, `§7.8` steps, the deliberately broken
> `EVENTS_URL`) to stay out of Git history entirely.

### 7.3 Deploy a new version (canary)

I added `APP_VERSION=v2` to the container env, the smallest change that alters the pod
template hash, which is what actually triggers a canary.

**Proof-of-work item 2 (paused at 20%):**

```text
$ kubectl apply -f k8s/gateway.yaml
rollout.argoproj.io/gateway configured
service/gateway unchanged

$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/vanady39/quickticket-gateway:ae1a4579f016fd3e7b68ad97a4b998660af38d3c (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS     AGE  INFO
⟳ gateway                            Rollout     ॥ Paused   96s
├──# revision:2
│  └──⧉ gateway-75968bb7c7           ReplicaSet  ✔ Healthy  36s  canary
│     └──□ gateway-75968bb7c7-7mxzj  Pod         ✔ Running  36s  ready:1/1
└──# revision:1
   └──⧉ gateway-594974b75c           ReplicaSet  ✔ Healthy  96s  stable
      ├──□ gateway-594974b75c-5p9g7  Pod         ✔ Running  96s  ready:1/1
      ├──□ gateway-594974b75c-bvnv6  Pod         ✔ Running  96s  ready:1/1
      ├──□ gateway-594974b75c-ld2c9  Pod         ✔ Running  96s  ready:1/1
      └──□ gateway-594974b75c-xwn7l  Pod         ✔ Running  96s  ready:1/1
```

`Status: Paused`, `Message: CanaryPauseStep`, step 1/5, `ActualWeight: 20`, 1 updated pod out
of 5. Because both `Images` lines are the same digest, the canary and stable tags show up on
one line, the env var is the only difference.

### 7.4 Verify traffic split

The lab's loop counts *whole-log* matches, which isn't fair here: the four stable pods were
created 60 s before the canary pod, so they carry 60 s of extra history. Both versions are
below. The whole-log one is the lab's command verbatim, the second one restricts to a fixed
window.

```text
$ kubectl apply -f labs/lab7/loadgen.yaml
deployment.apps/loadgen configured

$ for pod in $(kubectl get pods -l app=gateway -o name); do
    count=$(kubectl logs $pod 2>/dev/null | grep -c 'GET /events')
    img=$(kubectl get $pod -o jsonpath='{.spec.containers[0].image}')
    echo "$pod image=$img events_requests=$count"
  done
pod/gateway-594974b75c-5p9g7 image=ghcr.io/vanady39/quickticket-gateway:ae1a457... events_requests=100
pod/gateway-594974b75c-bvnv6 image=ghcr.io/vanady39/quickticket-gateway:ae1a457... events_requests=92
pod/gateway-594974b75c-ld2c9 image=ghcr.io/vanady39/quickticket-gateway:ae1a457... events_requests=98
pod/gateway-594974b75c-xwn7l image=ghcr.io/vanady39/quickticket-gateway:ae1a457... events_requests=112
pod/gateway-75968bb7c7-7mxzj image=ghcr.io/vanady39/quickticket-gateway:ae1a457... events_requests=44
```

Fair version: same window for every pod, printing `rs_hash` + `APP_VERSION` instead of the
image (the image is identical for both versions in this lab, so it can't label the canary):

```text
$ for pod in $(kubectl get pods -l app=gateway -o name); do
    count=$(kubectl logs --since=60s $pod | grep -c 'GET /events')
    hash=$(kubectl get $pod -o jsonpath='{.metadata.labels.rollouts-pod-template-hash}')
    ver=$(kubectl get $pod -o jsonpath='{.spec.containers[0].env[?(@.name=="APP_VERSION")].value}')
    ...
  done
pod/gateway-594974b75c-5p9g7 rs_hash=594974b75c role=stable events_requests_60s=49
pod/gateway-594974b75c-bvnv6 rs_hash=594974b75c role=stable events_requests_60s=53
pod/gateway-594974b75c-ld2c9 rs_hash=594974b75c role=stable events_requests_60s=49
pod/gateway-594974b75c-xwn7l rs_hash=594974b75c role=stable events_requests_60s=56
pod/gateway-75968bb7c7-7mxzj rs_hash=75968bb7c7 role=canary events_requests_60s=53
total=260 canary=53 canary_share=20.3%
```

**20.3% of 260 requests hit the canary pod**, against a configured `setWeight: 20`. The four
stable pods got 49/53/49/56, an even spread, which is what I'd expect from `kube-proxy`'s
per-connection random endpoint selection.

Worth spelling out the mechanism here: there's no service mesh or ingress traffic-shaping
provider in this cluster, so Argo Rollouts can't shift a *percentage of requests*. It
approximates `setWeight: N` by **replica count** instead. It scales the canary ReplicaSet to
`round(N% × 5)` pods and lets the Service spread load over all endpoints. That's why 20% is
exactly 1 of 5 pods, and why intermediate values like `ActualWeight: 25` show up mid-step (3
canary pods out of a momentary 6 total, before a stable pod finishes terminating).

### 7.5 Promote the canary

**Proof-of-work item 3 (progression to 100%).** Full run took 92 s wall clock (12:30:04 → 12:31:36),
of which 30 s is the configured `pause: {duration: 30s}`:

```text
$ kubectl argo rollouts promote gateway
rollout 'gateway' promoted
```

State machine, one line per observed change (`--watch` capture, 100 frames → 23 state changes):

```text
status=॥ Paused       step=1/5  setWeight=20   actualWeight=20   updated=1 ready=5
status=◌ Progressing  step=1/5  setWeight=20   actualWeight=20   updated=1 ready=5
status=◌ Progressing  step=2/5  setWeight=60   actualWeight=25   updated=1 ready=4
status=◌ Progressing  step=2/5  setWeight=60   actualWeight=25   updated=3 ready=4
status=॥ Paused       step=3/5  setWeight=60   actualWeight=60   updated=3 ready=5
status=◌ Progressing  step=4/5  setWeight=100  actualWeight=60   updated=3 ready=5
status=◌ Progressing  step=4/5  setWeight=100  actualWeight=75   updated=3 ready=4
status=◌ Progressing  step=4/5  setWeight=100  actualWeight=75   updated=5 ready=4
status=✔ Healthy      step=5/5  setWeight=100  actualWeight=100  updated=5 ready=5
```

At the 60% pause (step 3/5, auto-proceeds after 30 s):

```text
Name:            gateway
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          3/5
  SetWeight:     60
  ActualWeight:  60
Replicas:
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS         AGE    INFO
⟳ gateway                            Rollout     ॥ Paused       5m10s
├──# revision:2
│  └──⧉ gateway-75968bb7c7           ReplicaSet  ✔ Healthy      4m10s  canary
│     ├──□ gateway-75968bb7c7-7mxzj  Pod         ✔ Running      4m10s  ready:1/1
│     ├──□ gateway-75968bb7c7-9d9hz  Pod         ✔ Running      9s     ready:1/1
│     └──□ gateway-75968bb7c7-wxpzq  Pod         ✔ Running      9s     ready:1/1
└──# revision:1
   └──⧉ gateway-594974b75c           ReplicaSet  ✔ Healthy      5m10s  stable
      ├──□ gateway-594974b75c-5p9g7  Pod         ◌ Terminating  5m10s  ready:0/1
      ├──□ gateway-594974b75c-bvnv6  Pod         ✔ Running      5m10s  ready:1/1
      └──□ gateway-594974b75c-ld2c9  Pod         ✔ Running      5m10s  ready:1/1
```

Final:

```text
Name:            gateway
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/vanady39/quickticket-gateway:ae1a4579f016fd3e7b68ad97a4b998660af38d3c (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS         AGE    INFO
⟳ gateway                            Rollout     ✔ Healthy      5m48s
├──# revision:2
│  └──⧉ gateway-75968bb7c7           ReplicaSet  ✔ Healthy      4m48s  stable
│     ├──□ gateway-75968bb7c7-7mxzj  Pod         ✔ Running      4m48s  ready:1/1
│     ├──□ gateway-75968bb7c7-9d9hz  Pod         ✔ Running      47s    ready:1/1
│     ├──□ gateway-75968bb7c7-wxpzq  Pod         ✔ Running      47s    ready:1/1
│     ├──□ gateway-75968bb7c7-gkn6v  Pod         ✔ Running      9s     ready:1/1
│     └──□ gateway-75968bb7c7-m2vsx  Pod         ✔ Running      9s     ready:1/1
└──# revision:1
   └──⧉ gateway-594974b75c           ReplicaSet  • ScaledDown   5m48s
      └──□ gateway-594974b75c-ld2c9  Pod         ◌ Terminating  5m48s  ready:1/1
```

### 7.6 Deploy a "bad" version and abort

`APP_VERSION: "v3-bad"`. (In Task 1 "bad" is only a label, the pod is perfectly healthy. The
Bonus Task deploys a version that genuinely returns 5xx and lets the analysis catch it by
itself.)

```text
$ kubectl apply -f k8s/gateway.yaml
rollout.argoproj.io/gateway configured

$ kubectl argo rollouts get rollout gateway
Status:          ॥ Paused
Message:         CanaryPauseStep
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
...
├──# revision:3
│  └──⧉ gateway-6bcd7d4d74           ReplicaSet  ✔ Healthy     31s    canary
│     └──□ gateway-6bcd7d4d74-68wl9  Pod         ✔ Running     30s    ready:1/1
├──# revision:2
│  └──⧉ gateway-75968bb7c7           ReplicaSet  ✔ Healthy     6m34s  stable
│     ├──□ gateway-75968bb7c7-7mxzj  Pod         ✔ Running     6m34s  ready:1/1
│     ├──□ gateway-75968bb7c7-9d9hz  Pod         ✔ Running     2m33s  ready:1/1
│     ├──□ gateway-75968bb7c7-gkn6v  Pod         ✔ Running     115s   ready:1/1
│     └──□ gateway-75968bb7c7-m2vsx  Pod         ✔ Running     115s   ready:1/1

$ kubectl get endpointslice -l kubernetes.io/service-name=gateway -o ...
10.42.0.111 gateway-75968bb7c7-7mxzj ready=true
10.42.0.114 gateway-75968bb7c7-9d9hz ready=true
10.42.0.115 gateway-75968bb7c7-gkn6v ready=true
10.42.0.116 gateway-75968bb7c7-m2vsx ready=true
10.42.0.117 gateway-6bcd7d4d74-68wl9 ready=true     # ← the bad canary, taking 1/5 of requests
```

I timed the abort with `date +%s.%N` before the command, then a tight poll of the Service's
EndpointSlice afterwards:

```text
canary pod=gateway-6bcd7d4d74-68wl9 ip=10.42.0.117
T0 (just before abort)      = 2026-09-22T12:32:45+03:00   epoch=1790069565.257584478
rollout 'gateway' aborted
T1 (abort command returned) = epoch=1790069565.376146489  delta=0.119 s
canary IP removed from ready endpoints  at epoch=1790069565.733703508  delta_from_abort=0.476 s
canary pod object gone                  at epoch=1790069566.850077583  delta_from_abort=1.592 s

--- ready endpoints now ---
10.42.0.111 gateway-75968bb7c7-7mxzj ready=true
10.42.0.114 gateway-75968bb7c7-9d9hz ready=true
10.42.0.115 gateway-75968bb7c7-gkn6v ready=true
10.42.0.116 gateway-75968bb7c7-m2vsx ready=true
10.42.0.118 gateway-75968bb7c7-cfg7w ready=false     # replacement stable pod, still starting
```

> Resolution caveat: the poll loop issues a `kubectl get endpointslice` per iteration, each
> costing roughly 0.1–0.3 s of API round-trip, so 0.476 s is an *upper bound* at that
> granularity. The true removal probably happened somewhere in the preceding ~0.3 s window.
> That's still more than precise enough for the comparison I'm making, which is against
> 134 seconds.

**Proof-of-work item 4 (after abort):**

```text
$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/vanady39/quickticket-gateway:ae1a4579f016fd3e7b68ad97a4b998660af38d3c (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ✖ Degraded    8m4s
├──# revision:3
│  └──⧉ gateway-6bcd7d4d74           ReplicaSet  • ScaledDown  61s    canary
├──# revision:2
│  └──⧉ gateway-75968bb7c7           ReplicaSet  ✔ Healthy     7m4s   stable
│     ├──□ gateway-75968bb7c7-7mxzj  Pod         ✔ Running     7m4s   ready:1/1
│     ├──□ gateway-75968bb7c7-9d9hz  Pod         ✔ Running     3m3s   ready:1/1
│     ├──□ gateway-75968bb7c7-gkn6v  Pod         ✔ Running     2m25s  ready:1/1
│     ├──□ gateway-75968bb7c7-m2vsx  Pod         ✔ Running     2m25s  ready:1/1
│     └──□ gateway-75968bb7c7-cfg7w  Pod         ✔ Running     22s    ready:1/1
└──# revision:1
   └──⧉ gateway-594974b75c           ReplicaSet  • ScaledDown  8m4s

$ kubectl run curl-check --rm -i --restart=Never --image=curlimages/curl:latest -- \
    -s -o /dev/null -w 'http://gateway:8080/health -> HTTP %{http_code}\n' http://gateway:8080/health
http://gateway:8080/health -> HTTP 200

$ for p in $(kubectl get pods -l app=gateway -o name); do echo "$p APP_VERSION=$(...)"; done
pod/gateway-75968bb7c7-7mxzj APP_VERSION=v2
pod/gateway-75968bb7c7-9d9hz APP_VERSION=v2
pod/gateway-75968bb7c7-cfg7w APP_VERSION=v2
pod/gateway-75968bb7c7-gkn6v APP_VERSION=v2
pod/gateway-75968bb7c7-m2vsx APP_VERSION=v2
```

Every serving pod is back on `v2`. `Degraded` here means "the *update* failed", not "the
service is down": the service never lost capacity, and `Ready: 5 / Available: 5` throughout.

### 7.7 Answer — how long from `abort` to all traffic on stable, vs. a `git revert` rollback?

| | Lab 7 `kubectl argo rollouts abort` | Lab 5 `git revert` + push |
|---|---|---|
| Command returns | 0.119 s | ~2 s (`git revert` + `git push`) |
| Bad pod out of the Service's ready endpoints | **0.476 s** | N/A |
| Bad pod object deleted | 1.592 s | N/A |
| Bad version fully out of rotation | **< 1 s** | **134 s** (measured in Lab 5 §5.9; three runs gave 132 s / 242 s / 235 s) |
| Blast radius while it was bad | 20% of requests, for as long as I chose to leave it | 100% of requests, for the whole detection window |

**Two orders of magnitude, and that is the smaller half of the story.**

*Why abort is fast:* nothing has to be fetched, rendered or diffed. `abort` is just a single
PATCH that sets `status.abortedAt` on one object the controller is already watching. The
controller scales the canary ReplicaSet to 0, the endpoints controller drops the pod from the
EndpointSlice, and `kube-proxy` reprograms its rules. No new image gets pulled and no stable
pod restarts, the stable ReplicaSet was never scaled below 4 in the first place.

*Why `git revert` is slow:* the 134 s in Lab 5 was almost entirely **poll latency**. ArgoCD's
repo-server polls the Git remote on a ~180 s timer, and the reconcile itself only took ~2 s
once it noticed. `argocd app sync` cuts it down to the reconcile time, and a webhook would too,
but the spread I measured in Lab 5 (132–242 s) is the honest number for the unattended path,
and you can't make it deterministic without a webhook.

*The part that doesn't show up in the table:* with a Deployment + `git revert`, the bad version
was serving **100%** of traffic for those 134 seconds, because a RollingUpdate replaces every
pod. With a canary, the bad version never went past the weight I let it reach. Lab 5 also found
that ArgoCD wouldn't even report `Degraded` until `progressDeadlineSeconds` (600 s) elapsed, so
a human had to notice. The Bonus Task below removes the human from that loop entirely.

They're not competitors. `abort` is the **fast lane**, it undoes an in-flight rollout and
leaves Git untouched, so the next sync of `main` would happily re-deploy the bad version. `git
revert` is the **durable** fix, it changes the desired state. The right incident sequence is
abort first (seconds, stops the bleeding), revert second (minutes, makes it stick).

---

## Task 2 — Multi-Step Canary with Observation (4 pts)

### 7.8 Strategy

```yaml
strategy:
  canary:
    steps:
      - setWeight: 20             # 1/5 pods
      - pause: {duration: 60s}    # observe for 1 min
      - setWeight: 40             # 2/5 pods
      - pause: {duration: 60s}
      - setWeight: 60             # 3/5 pods
      - pause: {duration: 60s}
      - setWeight: 80             # 4/5 pods
      - pause: {duration: 30s}
      - setWeight: 100
```

### 7.9 The rollout

I triggered it with a genuinely different image reference, as the lab asks. The lab's
`docker tag quickticket-gateway:v1 quickticket-gateway:v2` assumes the deployed image is
`quickticket-gateway:v1`, but since Lab 5 the manifests carry
`ghcr.io/vanady39/quickticket-gateway:ae1a457…`. So I re-tagged **that** image instead:

```text
$ docker tag ghcr.io/vanady39/quickticket-gateway:ae1a4579f016fd3e7b68ad97a4b998660af38d3c quickticket-gateway:v2
$ k3d image import -c quickticket quickticket-gateway:v2
INFO[0004] Successfully imported 1 image(s) into 1 cluster(s)

$ kubectl argo rollouts set image gateway gateway=quickticket-gateway:v2
rollout "gateway" image updated
```

Run took 5 min 47 s (12:34:07 → 12:39:54). State machine, 38 state changes condensed:

```text
status=◌ Progressing  step=0/9  setWeight=20   actualWeight=0    updated=1 ready=4
status=॥ Paused       step=1/9  setWeight=20   actualWeight=20   updated=1 ready=5
status=◌ Progressing  step=2/9  setWeight=40   actualWeight=25   updated=2 ready=4
status=॥ Paused       step=3/9  setWeight=40   actualWeight=40   updated=2 ready=5
status=◌ Progressing  step=4/9  setWeight=60   actualWeight=50   updated=3 ready=4
status=॥ Paused       step=5/9  setWeight=60   actualWeight=60   updated=3 ready=5
status=◌ Progressing  step=6/9  setWeight=80   actualWeight=75   updated=4 ready=4
status=॥ Paused       step=7/9  setWeight=80   actualWeight=80   updated=4 ready=5
status=◌ Progressing  step=8/9  setWeight=100  actualWeight=100  updated=5 ready=4
status=✔ Healthy      step=9/9  setWeight=100  actualWeight=100  updated=5 ready=5
```

Four of the five pause frames in full (all `Paused` frames, showing the canary ReplicaSet growing
1 → 2 → 3 → 4 pods while the stable one shrinks 4 → 3 → 2 → 1):

```text
  Step: 1/9   SetWeight: 20   ActualWeight: 20   Updated: 1
├──# revision:5
│  └──⧉ gateway-c766bd4cf            ReplicaSet  ✔ Healthy     9s     canary
│     └──□ gateway-c766bd4cf-tv6jd   Pod         ✔ Running     9s     ready:1/1
├──# revision:4
│  └──⧉ gateway-75968bb7c7           ReplicaSet  ✔ Healthy     8m13s  stable
│     ├──□ gateway-75968bb7c7-7mxzj  Pod         ✔ Running     8m13s  ready:1/1
│     ├──□ gateway-75968bb7c7-9d9hz  Pod         ✔ Running     4m12s  ready:1/1
│     ├──□ gateway-75968bb7c7-gkn6v  Pod         ✔ Running     3m34s  ready:1/1
│     └──□ gateway-75968bb7c7-m2vsx  Pod         ✔ Running     3m34s  ready:1/1

  Step: 3/9   SetWeight: 40   ActualWeight: 40   Updated: 2
├──# revision:5
│  └──⧉ gateway-c766bd4cf            ReplicaSet  ✔ Healthy     79s    canary
│     ├──□ gateway-c766bd4cf-tv6jd   Pod         ✔ Running     79s    ready:1/1
│     └──□ gateway-c766bd4cf-mzwts   Pod         ✔ Running     11s    ready:1/1
├──# revision:4
│  └──⧉ gateway-75968bb7c7           ReplicaSet  ✔ Healthy     9m23s  stable
│     ├──□ gateway-75968bb7c7-7mxzj  Pod         ✔ Running     9m23s  ready:1/1
│     ├──□ gateway-75968bb7c7-9d9hz  Pod         ✔ Running     5m22s  ready:1/1
│     └──□ gateway-75968bb7c7-gkn6v  Pod         ✔ Running     4m44s  ready:1/1

  Step: 5/9   SetWeight: 60   ActualWeight: 60   Updated: 3
│  └──⧉ gateway-c766bd4cf            ReplicaSet  ✔ Healthy     2m26s  canary
│     ├──□ gateway-c766bd4cf-tv6jd   Pod         ✔ Running     2m26s  ready:1/1
│     ├──□ gateway-c766bd4cf-mzwts   Pod         ✔ Running     78s    ready:1/1
│     └──□ gateway-c766bd4cf-pt82m   Pod         ✔ Running     8s     ready:1/1
│  └──⧉ gateway-75968bb7c7           ReplicaSet  ✔ Healthy     10m    stable
│     ├──□ gateway-75968bb7c7-7mxzj  Pod         ✔ Running     10m    ready:1/1
│     └──□ gateway-75968bb7c7-gkn6v  Pod         ✔ Running     5m51s  ready:1/1

  Step: 7/9   SetWeight: 80   ActualWeight: 80   Updated: 4
│  └──⧉ gateway-c766bd4cf            ReplicaSet  ✔ Healthy     3m33s  canary
│     ├──□ gateway-c766bd4cf-tv6jd   Pod         ✔ Running     3m33s  ready:1/1
│     ├──□ gateway-c766bd4cf-mzwts   Pod         ✔ Running     2m25s  ready:1/1
│     ├──□ gateway-c766bd4cf-pt82m   Pod         ✔ Running     75s    ready:1/1
│     └──□ gateway-c766bd4cf-xrfzg   Pod         ✔ Running     8s     ready:1/1
│  └──⧉ gateway-75968bb7c7           ReplicaSet  ✔ Healthy     11m    stable
│     └──□ gateway-75968bb7c7-7mxzj  Pod         ✔ Running     11m    ready:1/1
```

### Dashboard observation

> **Note on method.** The lab warns that the Lab 3 docker-compose Grafana can't scrape k3d
> pods. Lab 6 already moved Grafana **and** Prometheus into the cluster (namespace `monitoring`),
> so the "QuickTicket — Golden Signals" dashboard *is* live and *is* reading this data. Below is
> the exact PromQL those panels run, sampled every 10 s through the whole rollout, because a
> table of numbers is reviewable in a PR and a screenshot isn't. I verify the Grafana side
> independently just below.

```text
time     step  phase        rps_total 5xx_ratio  per-rs_hash rps  (75968bb7c7=stable, c766bd4cf=canary)
12:34:04    9  Healthy           9.56    0.0000  75968bb7c7=9.56
12:34:14    0  Progressing       9.67    0.0000  75968bb7c7=9.67
12:34:25    1  Paused            9.51    0.0000  75968bb7c7=8.86 c766bd4cf=0.66
12:34:35    1  Paused            9.53    0.0000  75968bb7c7=8.20 c766bd4cf=1.33
12:34:45    1  Paused            9.72    0.0000  75968bb7c7=7.96 c766bd4cf=1.76
12:34:55    1  Paused            9.32    0.0000  75968bb7c7=7.60 c766bd4cf=1.72
12:35:06    1  Paused            9.48    0.0000  75968bb7c7=7.76 c766bd4cf=1.72
12:35:16    2  Progressing       9.29    0.0000  75968bb7c7=7.41 c766bd4cf=1.88
12:35:26    3  Paused            9.31    0.0000  75968bb7c7=7.23 c766bd4cf=2.08
12:35:37    3  Paused            8.91    0.0000  75968bb7c7=5.84 c766bd4cf=3.07
12:35:47    3  Paused            9.51    0.0000  75968bb7c7=5.80 c766bd4cf=3.71
12:35:57    3  Paused            9.28    0.0000  75968bb7c7=5.44 c766bd4cf=3.84
12:36:08    3  Paused            9.32    0.0000  75968bb7c7=5.44 c766bd4cf=3.88
12:36:18    3  Paused            9.64    0.0000  75968bb7c7=5.40 c766bd4cf=4.24
12:36:28    4  Progressing       9.27    0.0000  75968bb7c7=4.76 c766bd4cf=4.52
12:36:38    5  Paused            9.20    0.0000  75968bb7c7=4.84 c766bd4cf=4.36
12:36:49    5  Paused            9.32    0.0000  75968bb7c7=4.00 c766bd4cf=5.32
12:36:59    5  Paused            9.30    0.0000  75968bb7c7=3.28 c766bd4cf=6.02
12:37:09    5  Paused            9.52    0.0000  75968bb7c7=3.20 c766bd4cf=6.32
12:37:20    5  Paused            9.72    0.0000  75968bb7c7=3.76 c766bd4cf=5.96
12:37:30    5  Paused            9.72    0.0000  75968bb7c7=3.60 c766bd4cf=6.12
12:37:40    7  Paused            9.55    0.0000  75968bb7c7=3.07 c766bd4cf=6.48
12:37:51    7  Paused            9.64    0.0000  75968bb7c7=3.21 c766bd4cf=6.43
12:38:01    7  Paused            9.23    0.0000  75968bb7c7=2.12 c766bd4cf=7.11
12:38:11    8  Progressing       9.68    0.0000  75968bb7c7=2.12 c766bd4cf=7.56
12:38:21    9  Healthy           9.42    0.0000  75968bb7c7=1.30 c766bd4cf=8.12
12:38:32    9  Healthy           9.54    0.0000  75968bb7c7=0.63 c766bd4cf=8.91
12:38:42    9  Healthy           9.32    0.0000  c766bd4cf=9.32
12:39:44    9  Healthy           9.48    0.0000  c766bd4cf=9.48
```

Same numbers, but fetched **through Grafana's own datasource proxy**, to show the dashboard is
genuinely wired to this data and isn't living in a parallel universe:

```text
$ curl -s -u admin:admin 'http://localhost:3000/api/search?type=dash-db'
    "uid": "quickticket-golden-signals"
    "title": "QuickTicket — Golden Signals"

$ curl -s -u admin:admin 'http://localhost:3000/api/datasources/uid/quickticket-prom'
Prometheus prometheus http://prometheus:9090

# queries executed through /api/datasources/proxy/uid/quickticket-prom/api/v1/query
--- sum(rate(gateway_requests_total[1m]))
    {} => 9.5455
--- sum by (rs_hash) (rate(gateway_requests_total[1m]))
    {'rs_hash': 'c766bd4cf'} => 9.5455
--- gateway:sli_availability:ratio_rate5m
    {} => 1.0
```

**What the observation actually shows.**

1. *Does request rate stay steady across canary steps?* Yes, total throughput sat between
   **8.91 and 9.72 rps** for the entire 5 min 47 s, a ±4% band around ~9.4 rps, with no dip at
   any step boundary. That's the number that matters: the *split* moved, the *total* didn't.
   Capacity is preserved because `maxSurge`/`maxUnavailable` default to 25%/25%, so at most one
   of five pods is missing at a time (visible as `ready=4` in the Progressing frames) and the
   remaining four absorb the load easily at this rate.
2. *Does the updated-replica count climb 1→2→3→4→5?* Yes, exactly, and the per-`rs_hash` rates
   track it: canary ≈ 1.7 / 3.9 / 6.1 / 6.5–7.1 / 9.4 rps at weights 20 / 40 / 60 / 80 / 100.
   Note the metric lags the weight by ~20–30 s because the query is `rate(...[30s])`, a new
   canary pod's share gets averaged over a window that partly predates it. Never read a
   canary's error rate the instant a weight step lands, it's still measuring the past.
3. *Error rate:* flat `0.0000` throughout. Makes sense, this "new version" is the same image
   under a different tag.

### Answer — at what canary percentage would you want an automated abort?

**The gate belongs at the *first* step, 20%, and it should be the only fully-automatic one.**

The argument comes down to how much signal you have versus how much damage you're doing.

- **Damage is linear in weight.** At 20% one pod in five is bad: 20% of requests affected, and
  for an idempotent read path most clients retry onto a healthy pod. At 80% you've already
  broken four users in five, and aborting is barely better than a full rollback, you've already
  spent the blast-radius budget that made canarying worth doing in the first place.
- **Signal is *not* linear in weight, it's linear in time × traffic.** This is the part people
  get wrong. At ~9.4 rps overall, a 20% canary sees ~1.9 rps. To tell "5% error rate" apart from
  "0% error rate" with any real confidence you need something like a hundred canary requests,
  roughly a minute. That's exactly why my template uses `initialDelay: 60s` plus three 20 s
  measurements: ~110 canary requests behind each verdict. Going to 40% only halves that wait
  while doubling the damage, a bad trade.
- **So: hold at 20% long enough to actually measure, abort automatically there, and make every
  later step cheap.** Concretely, what I'd run in production for this service:

  | Step | Gate |
  |---|---|
  | 20% | automated analysis, `failureLimit: 1` (abort without asking anyone) |
  | 50% | automated analysis, but on a *tighter* threshold (the 5% SLO breach becomes visible sooner at 4.7 rps) |
  | 100% | no gate; if it survived 50% the remaining risk is capacity, not correctness |

- **The threshold matters more than the percentage.** A 5% error rate is a blunt instrument, way
  above this service's error budget (Lab 3's SLO is 99.5% availability, i.e. 0.5%), so a canary
  can pass analysis while burning budget ten times faster than it's allowed to. The honest gate
  is a *comparison against the stable ReplicaSet over the same window*, not an absolute number.
  If stable is at 0.2% and canary is at 2%, that's a ten-fold regression that a `< 0.05` check
  just waves through.
- **Two things need to be automatic no matter the weight.** A canary that can't be measured (no
  traffic reaching it) has to fail, not pass, that's why the denominator in the template has no
  `or vector(0)` fallback. And a canary that never becomes `Ready` has to be caught by
  `progressDeadlineSeconds`, which defaults to 600 s and is way too long. I'd set it to 120 s for
  this service. §B.5 below is a live demonstration of exactly that hole.

---

## Bonus Task — Automated Canary Analysis (2 pts)

### B.1 In-cluster Prometheus

> ⚠️ **`kubectl apply -f labs/lab7/prometheus.yaml` was deliberately NOT run.** See
> "Decisions" at the top: the Prometheus already serving `monitoring` is a strict superset of it and
> uses the same object names, so applying the lab's file would have deleted the `events` and
> `payments` scrape jobs and the `rule_files:` entry that the Lab 3 dashboard and the Lab 6 runbooks
> depend on. The `gateway` job (the only part Lab 7 needs) is byte-identical in both files.

Rather than just trust that, I checked that the `rs_hash` label really lands on canary metrics
while a canary was live (this is exactly the lab's verification snippet, against port 9091):

```text
$ kubectl port-forward -n monitoring svc/prometheus 9091:9090 &
$ curl -s 'http://localhost:9091/api/v1/targets?state=active' | python3 -c "..."
gateway-75968bb7c7-7mxzj rs= 75968bb7c7 up      # ← canary ReplicaSet
gateway-594974b75c-ld2c9 rs= 594974b75c up      # ← stable ReplicaSet
gateway-594974b75c-bvnv6 rs= 594974b75c up
gateway-594974b75c-5p9g7 rs= 594974b75c up
gateway-594974b75c-xwn7l rs= 594974b75c up
gateway-858884796-t2r8x  rs= None  unknown      # ← the Deployment pod ArgoCD had just resurrected

# the extras the lab's file would have removed are still there:
events: 1 targets
gateway: 6 targets
payments: 1 targets
group slo_rules : gateway:sli_availability:ratio_rate5m, gateway:sli_latency_500ms:ratio_rate5m, gateway:error_budget_burn_rate:ratio_rate5m
```

All five Rollout-managed pods are `up` and carry a `rs_hash` that matches their ReplicaSet. The
sixth line is the ArgoCD-resurrected Deployment pod from §7.2, and it has **no** `rs_hash`, since
the `rollouts-pod-template-hash` label only exists on pods Argo Rollouts owns. A nice accidental
demonstration of why that relabel is the whole mechanism: without it there's no way to write a
PromQL selector that means "the canary".

### B.2 AnalysisTemplate

I copied it to `k8s/analysis-template.yaml` (the lab's "How to Submit" expects it there, and it
puts the template under ArgoCD's management alongside the Rollout). The spec is unchanged from
`labs/lab7/analysis-template.yaml`, I only added a header comment recording which Prometheus it
points at.

```text
$ kubectl apply -f k8s/analysis-template.yaml
analysistemplate.argoproj.io/gateway-error-rate created

$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   0s
```

The four design choices the lab asks about:

1. **`initialDelay: 60s`.** Three separate clocks have to catch up before a measurement means
   anything: Kubernetes SD refresh (~10 s) to discover the pod, the 5 s scrape interval to get
   samples, and the `rate(...[60s])` window itself needing a full minute of them. Measure before
   that and the query returns an empty vector, which counts as an *error*, not a failure, so
   `consecutiveErrorLimit` (default 4) trips and the rollout aborts a perfectly good release.
2. **`or on() vector(0)` on the numerator.** "No 5xx series exists for this canary" and "the
   canary is returning errors at a rate of zero" are the same real-world fact, but different
   PromQL results: the first is an empty vector. `or on() vector(0)` turns the empty case into
   the scalar 0 so `result[0]` is always indexable. `on()` is needed because the two sides have
   no labels in common.
3. **The denominator stays strict.** This is deliberately asymmetric. An empty denominator means
   the canary received *no traffic at all*, and a canary you can't measure is a canary you can't
   trust. The empty vector propagates, the metric errors, and the rollout fails safe. Adding
   `or vector(0)` there would produce `0/0` and silently promote a release nobody ever tested.
4. **`{{args.canary-hash}}`.** The Rollout passes `podTemplateHashValue: Latest` at runtime,
   which resolves to the canary ReplicaSet's `rollouts-pod-template-hash`. Prometheus has that
   same value as `rs_hash` (B.1). Without this filter the query would average canary and stable
   together, and a single bad pod in five would get diluted to a fifth of its true error rate,
   probably under the 5% threshold, so the gate would pass a release that's failing 100% of its
   own requests.

### B.3 Wiring it in

```yaml
strategy:
  canary:
    steps:
      - setWeight: 20
      - pause: {duration: 20s}
      - analysis:
          templates:
            - templateName: gateway-error-rate
          args:
            - name: canary-hash
              valueFrom:
                podTemplateHashValue: Latest
      - setWeight: 50
      - pause: {duration: 20s}
      - setWeight: 100
```

### B.4 Good version — auto-promotes

I applied the strategy above together with a return to the canonical ghcr.io image reference (a
real image change from `quickticket-gateway:v2`, so a real canary). Total 4 min 48 s, **no
human input after the apply**:

```text
$ kubectl apply -f k8s/gateway.yaml        # 12:40:53
rollout.argoproj.io/gateway configured
```

Analysis starting, then finishing:

```text
  Step: 2/6   SetWeight: 20   ActualWeight: 20   Updated: 1
├──# revision:6
│  ├──⧉ gateway-75968bb7c7           ReplicaSet   ✔ Healthy      15m    canary
│  │  └──□ gateway-75968bb7c7-62hg9  Pod          ✔ Running      28s    ready:1/1
│  └──α gateway-75968bb7c7-6-2       AnalysisRun  ◌ Running      0s

  Step: 2/6   SetWeight: 20   ActualWeight: 20   Updated: 1
│  ├──⧉ gateway-75968bb7c7           ReplicaSet   ✔ Healthy      16m    canary
│  │  └──□ gateway-75968bb7c7-62hg9  Pod          ✔ Running      2m8s   ready:1/1
│  └──α gateway-75968bb7c7-6-2       AnalysisRun  ✔ Successful   100s   ✔ 3
```

The AnalysisRun's own progress in the tree, measurement by measurement:

```text
α gateway-75968bb7c7-6-2   AnalysisRun  ◌ Running       0s
α gateway-75968bb7c7-6-2   AnalysisRun  ◌ Running      60s   ✔ 1
α gateway-75968bb7c7-6-2   AnalysisRun  ◌ Running      80s   ✔ 2
α gateway-75968bb7c7-6-2   AnalysisRun  ✔ Successful  100s   ✔ 3
```

60 s (the `initialDelay`), then 20 s, then 20 s (the `interval`), exactly as configured. Then
the rollout carried on by itself to 50% and 100%:

```text
status=॥ Paused       step=1/6  setWeight=20   actualWeight=20   updated=1 ready=5
status=◌ Progressing  step=2/6  setWeight=20   actualWeight=20   updated=1 ready=5      # analysis
status=◌ Progressing  step=3/6  setWeight=50   actualWeight=25   updated=3 ready=4
status=॥ Paused       step=4/6  setWeight=50   actualWeight=50   updated=3 ready=6
status=◌ Progressing  step=5/6  setWeight=100  actualWeight=75   updated=5 ready=4
status=✔ Healthy      step=6/6  setWeight=100  actualWeight=100  updated=5 ready=5
```

```text
$ kubectl get analysisrun
NAME                     STATUS       AGE
gateway-75968bb7c7-6-2   Successful   4m20s

$ kubectl get analysisrun gateway-75968bb7c7-6-2 -o yaml
status:
  completedAt: "2026-09-22T09:43:01Z"
  metricResults:
  - consecutiveSuccess: 3
    count: 3
    measurements:
    - finishedAt: "2026-09-22T09:42:21Z"
      phase: Successful
      startedAt: "2026-09-22T09:42:21Z"
      value: '[0]'
    - finishedAt: "2026-09-22T09:42:41Z"
      phase: Successful
      startedAt: "2026-09-22T09:42:41Z"
      value: '[0]'
    - finishedAt: "2026-09-22T09:43:01Z"
      phase: Successful
      startedAt: "2026-09-22T09:43:01Z"
      value: '[0]'
    metadata:
      ResolvedPrometheusQuery: |
        (
          sum(rate(gateway_requests_total{rs_hash="75968bb7c7",status=~"5.."}[60s]))
          or on() vector(0)
        )
        /
        sum(rate(gateway_requests_total{rs_hash="75968bb7c7"}[60s]))
    name: error-rate
    phase: Successful
    successful: 3
  phase: Successful
  runSummary:
    count: 1
    successful: 1
  startedAt: "2026-09-22T09:41:21Z"
```

`ResolvedPrometheusQuery` is the proof that `{{args.canary-hash}}` was substituted with the real
canary hash at runtime.

### B.5 Bad version — auto-aborts

> ⚠️ **The lab's literal recipe doesn't work on this repo, and figuring out why was the most
> useful part of the bonus.** I ran it first anyway, and it fails in an instructive way. Both
> attempts are below.

#### Attempt A — `EVENTS_URL: "http://broken-on-purpose:8081"` (the lab's suggestion)

```text
$ kubectl apply -f k8s/gateway.yaml     # EVENTS_URL=http://broken-on-purpose:8081, GATEWAY_TIMEOUT_MS=2000
rollout.argoproj.io/gateway configured

# 100 seconds later:
Status:          ◌ Progressing
Message:         more replicas need to be updated
  Step:          0/6
  SetWeight:     20
  ActualWeight:  0
├──# revision:7
│  └──⧉ gateway-56f854f8c            ReplicaSet   ◌ Progressing  100s   canary
│     └──□ gateway-56f854f8c-gz54x   Pod          ✔ Running      100s   ready:0/1

$ kubectl get endpointslice -l kubernetes.io/service-name=gateway -o ...
gateway-75968bb7c7-62hg9 ready=true
gateway-75968bb7c7-nqwmj ready=true
gateway-75968bb7c7-kbzzh ready=true
gateway-75968bb7c7-dqrhm ready=true
gateway-56f854f8c-gz54x  ready=false        # ← never joins the Service

$ kubectl get analysisrun
NAME                     STATUS       AGE
gateway-75968bb7c7-6-2   Successful   6m17s    # ← only the previous, good one. No new run at all.
```

**Why.** This repo's gateway has a readiness probe on `/health` (a Lab 4 decision I kept on
purpose), and `/health` reports the state of its downstreams:

```text
$ kubectl describe pod gateway-56f854f8c-gz54x | grep Unhealthy
  Warning  Unhealthy  8s (x19 over 96s)  kubelet  Readiness probe failed: HTTP probe failed with statuscode: 503

$ curl http://10.42.0.132:8080/health      # the canary pod, directly
{"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}
HTTP 503
```

An unresolvable `EVENTS_URL` makes `/health` return 503 → readiness fails → the endpoints
controller never adds the pod to `svc/gateway` → **the canary receives zero requests** → it
emits no `gateway_requests_total` samples → the strict denominator is empty → the metric
*errors*. But the rollout never even gets that far: `setWeight: 20` won't complete until the
canary has an available replica, so the rollout just sits at step 0/6 and the `analysis` step
at index 2 is never reached. It would eventually go `Degraded` at `progressDeadlineSeconds`
(600 s, the default), a 10-minute blind window, which is the exact hole I argued for closing in
Task 2.

This isn't really a bug in the lab. It's more an interaction with good probe design: a readiness
probe that checks downstreams is *also* a canary gate, and it fires before any analysis can. I
aborted rather than wait out the deadline:

```text
$ kubectl argo rollouts abort gateway
rollout 'gateway' aborted
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 7
```

#### Attempt B — `EVENTS_URL: "http://payments:8082"` (reachable, but the wrong service)

For the canary to genuinely serve 5xx it has to stay **Ready**, which means `/health` has to
pass while `/events` fails. Pointing `EVENTS_URL` at the payments service does exactly that:

```text
$ curl http://payments:8082/health  -> 200      # so gateway /health reports events:"ok" -> pod Ready
$ curl http://payments:8082/events  -> 404      # so gateway /events raises -> HTTPException(502)
```

(`app/gateway/main.py`'s `list_events()` does `r.raise_for_status()` inside a `try`, and the generic
`except` turns any failure into a 502.)

```text
$ kubectl apply -f k8s/gateway.yaml        # 12:48:11
rollout.argoproj.io/gateway configured
```

```text
  Step: 2/6   SetWeight: 20   ActualWeight: 20   Updated: 1   Ready: 5
├──# revision:8
│  ├──⧉ gateway-85cff5f8fc           ReplicaSet   ✔ Healthy      107s   canary
│  │  └──□ gateway-85cff5f8fc-85rzn  Pod          ✔ Running      107s   ready:1/1   ← Ready, serving 5xx
│  └──α gateway-85cff5f8fc-8-2       AnalysisRun  ✖ Failed       80s    ✖ 2
```

and 3 minutes 19 seconds after the apply, with nobody watching:

**Proof-of-work (final rollout state after the aborted bad deploy):**

```text
$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 8: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/vanady39/quickticket-gateway:ae1a4579f016fd3e7b68ad97a4b998660af38d3c (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS        AGE    INFO
⟳ gateway                            Rollout      ✖ Degraded    26m
├──# revision:8
│  ├──⧉ gateway-85cff5f8fc           ReplicaSet   • ScaledDown  3m18s  canary
│  └──α gateway-85cff5f8fc-8-2       AnalysisRun  ✖ Failed      2m51s  ✖ 2
├──# revision:7
│  └──⧉ gateway-56f854f8c            ReplicaSet   • ScaledDown  5m32s
├──# revision:6
│  ├──⧉ gateway-75968bb7c7           ReplicaSet   ✔ Healthy     25m    stable
│  │  ├──□ gateway-75968bb7c7-62hg9  Pod          ✔ Running     10m    ready:1/1
│  │  ├──□ gateway-75968bb7c7-nqwmj  Pod          ✔ Running     8m29s  ready:1/1
│  │  ├──□ gateway-75968bb7c7-dqrhm  Pod          ✔ Running     8m2s   ready:1/1
│  │  ├──□ gateway-75968bb7c7-kbzzh  Pod          ✔ Running     8m2s   ready:1/1
│  │  └──□ gateway-75968bb7c7-vrsst  Pod          ✔ Running     91s    ready:1/1
│  └──α gateway-75968bb7c7-6-2       AnalysisRun  ✔ Successful  10m    ✔ 3
├──# revision:5
│  └──⧉ gateway-c766bd4cf            ReplicaSet   • ScaledDown  17m
├──# revision:3
│  └──⧉ gateway-6bcd7d4d74           ReplicaSet   • ScaledDown  19m
└──# revision:1
   └──⧉ gateway-594974b75c           ReplicaSet   • ScaledDown  26m
```

Five stable pods still `Running`, `Ready: 5 / Available: 5`, never scaled below 4 at any point.

**Proof-of-work (both AnalysisRuns, one Successful and one Failed):**

```text
$ kubectl get analysisrun
NAME                     STATUS       AGE
gateway-75968bb7c7-6-2   Successful   10m
gateway-85cff5f8fc-8-2   Failed       2m51s
```

**Proof-of-work (the failed run's measurements):**

```text
$ kubectl get analysisrun gateway-85cff5f8fc-8-2 -o yaml
status:
  completedAt: "2026-09-22T09:49:59Z"
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
  - count: 2
    failed: 2
    measurements:
    - finishedAt: "2026-09-22T09:49:39Z"
      phase: Failed
      startedAt: "2026-09-22T09:49:39Z"
      value: '[0.5428571428571429]'
    - finishedAt: "2026-09-22T09:49:59Z"
      phase: Failed
      startedAt: "2026-09-22T09:49:59Z"
      value: '[0.5196078431372549]'
    metadata:
      ResolvedPrometheusQuery: |
        (
          sum(rate(gateway_requests_total{rs_hash="85cff5f8fc",status=~"5.."}[60s]))
          or on() vector(0)
        )
        /
        sum(rate(gateway_requests_total{rs_hash="85cff5f8fc"}[60s]))
    name: error-rate
    phase: Failed
  phase: Failed
  runSummary:
    count: 1
    failed: 1
  startedAt: "2026-09-22T09:48:39Z"
```

> The lab predicts measurements of `[1.0]`. I measured **0.543** and **0.520**, and the
> difference is real, not noise: the loadgen sends one `/events` and one `/health` per loop.
> Every `/events` call on the canary 502s, every `/health` call succeeds, so a little over half
> the canary's requests are 5xx, not all of them. Well above the 5% threshold either way.
> `failureLimit: 1` means the second consecutive failure ends it. Also note the counter
> `count: 2`, the run stopped after two of its three configured measurements because the
> verdict was already decided.

Restored afterwards, as the lab instructs:

```text
$ kubectl apply -f k8s/gateway.yaml     # EVENTS_URL back to http://events:8081, timeout back to 5000
rollout.argoproj.io/gateway configured
$ kubectl argo rollouts retry rollout gateway
rollout 'gateway' retried
$ kubectl argo rollouts get rollout gateway
Status:          ✔ Healthy
  Step:          6/6
  SetWeight:     100
  ActualWeight:  100
```

### B.6 Cleanup

```text
$ kubectl delete -f labs/lab7/loadgen.yaml
deployment.apps "loadgen" deleted from default namespace

$ kubectl apply -f monitoring/k8s/loadgen.yaml       # restore Lab 6's generator (same object name)
deployment.apps/loadgen created

$ kubectl get pods -l app=loadgen
NAME                       READY   STATUS        RESTARTS   AGE
loadgen-76f75d7468-tlhjc   2/2     Running       0          25s
loadgen-c89dd77b9-48ffv    1/1     Terminating   0          27m
```

### Answer — what metric would you add beyond error rate?

**Latency, expressed as a comparison against the stable ReplicaSet instead of an absolute
threshold.** Here's a second metric in the same template:

```yaml
- name: latency-p95-vs-stable
  initialDelay: 60s
  interval: 20s
  count: 3
  successCondition: result[0] < 1.2      # canary p95 no worse than 1.2x stable p95
  failureLimit: 1
  provider:
    prometheus:
      query: |
        histogram_quantile(0.95, sum by (le) (rate(gateway_request_duration_seconds_bucket{rs_hash="{{args.canary-hash}}"}[60s])))
        /
        histogram_quantile(0.95, sum by (le) (rate(gateway_request_duration_seconds_bucket{rs_hash!="{{args.canary-hash}}"}[60s])))
```

Three reasons this is the right *second* metric, in order of importance:

1. **It catches the most common real regression, the one error rate is blind to.** A leaked
   connection pool, a lost index, an N+1 query, a synchronous call added to a hot path, none of
   these produce a single 5xx. They produce a service that still returns 200 while getting
   slower, until something upstream times out and the failure surfaces somewhere else entirely.
   The gateway already exports `gateway_request_duration_seconds` (Lab 3 built the SLO recording
   rules on it), so this costs nothing to collect.
2. **The ratio form is what makes it trustworthy during a canary.** An absolute "p95 < 500 ms"
   gate doesn't work here: the canary is cold (empty caches, JIT-less first requests, a fresh
   connection pool) and is one pod out of five, so its p95 is noisy by construction. Dividing by
   the stable ReplicaSet's p95 over the *same window* cancels out load spikes, noisy neighbours
   and time-of-day effects. The only thing left is "is the new code slower than the old code".
   The `rs_hash!=` selector comes for free from the same relabel rule the error-rate query uses.
3. **Multiple metrics in one AnalysisTemplate are evaluated independently and any one failing
   aborts**, so adding it strictly tightens the gate without touching the error-rate logic.

Two more I'd add before calling the gate production-grade, briefly:

- **Saturation.** `container_memory_working_set_bytes` for the canary pods against their limit.
  A memory leak shows up as a clean upward slope long before the OOMKill that would otherwise
  get discovered at 100% weight, at 3am.
- **A business KPI.** For QuickTicket, successful-purchase rate
  (`payments_charges_total{result="ok"}` per unit of `/pay` traffic). This is the one that catches
  the class of bug where every layer reports success and the product is still broken: a checkout
  that returns 200 with a silently dropped reservation is invisible to error rate, latency and
  saturation alike. It's also the slowest signal, since purchases are ~10% of traffic, so it
  belongs on the 50% step rather than the 20% one.

Worth stating the limit here: none of these metrics can catch a regression the canary is never
exposed to. Argo Rollouts splits by replica count here, not by request attributes, so a bug that
only triggers for one customer segment has maybe a 20% chance of being sampled at all. Metric
choice complements traffic shaping, it doesn't replace it.

---

## GitOps reconciliation — putting the Rollout under ArgoCD

I closed the manual-sync window from §7.2 at the end of the lab. The Rollout and
AnalysisTemplate got committed, pushed to the in-cluster git remote, and I restored
`automated: {selfHeal: false}`:

```text
$ kubectl port-forward -n gitops-src svc/gitserver 9418:8080 &
$ git push cluster HEAD:refs/heads/main
To http://127.0.0.1:9418/quickticket.git
   52151d9..38de42d  HEAD -> main

$ kubectl -n argocd patch app quickticket --type merge -p '{"spec":{"syncPolicy":{"automated":{"selfHeal":false}}}}'
application.argoproj.io/quickticket patched
{"automated":{"selfHeal":false}}
```

ArgoCD picked it up on its next poll, 61 seconds later:

```text
2026-09-22T12:53:33+03:00 OutOfSync/Healthy/52151d92bc28e67650b9eeaaff050ef3547f648d
2026-09-22T12:53:48+03:00 OutOfSync/Healthy/52151d92bc28e67650b9eeaaff050ef3547f648d
2026-09-22T12:54:03+03:00 OutOfSync/Healthy/52151d92bc28e67650b9eeaaff050ef3547f648d
2026-09-22T12:54:18+03:00 OutOfSync/Healthy/52151d92bc28e67650b9eeaaff050ef3547f648d
2026-09-22T12:54:33+03:00 Synced/Healthy/38de42dfc5387a1e074d82ff771ce0bc3fb63324
```

ArgoCD understands the Rollout CRD natively, no plugin, no health-check customisation needed:

```text
$ kubectl -n argocd get app quickticket -o jsonpath='{range .status.resources[*]}{.kind}/{.name} {.status}{"\n"}{end}'
ConfigMap/postgres-seed Synced
PersistentVolumeClaim/postgres-data Synced
Service/events Synced
Service/gateway Synced
Service/payments Synced
Service/postgres Synced
Service/redis Synced
Deployment/events Synced
Deployment/payments Synced
Deployment/postgres Synced
Deployment/redis Synced
AnalysisTemplate/gateway-error-rate Synced
Rollout/gateway Synced
```

Final cluster state:

```text
$ kubectl argo rollouts get rollout gateway
Status:          ✔ Healthy
  Step:          6/6
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/vanady39/quickticket-gateway:ae1a4579f016fd3e7b68ad97a4b998660af38d3c (stable)
Replicas:        Desired 5 / Current 5 / Updated 5 / Ready 5 / Available 5

$ kubectl get pods -n default
events-7dc944bd56-t86dv      1/1 Running
gateway-75968bb7c7-62hg9     1/1 Running
gateway-75968bb7c7-dqrhm     1/1 Running
gateway-75968bb7c7-kbzzh     1/1 Running
gateway-75968bb7c7-nqwmj     1/1 Running
gateway-75968bb7c7-vrsst     1/1 Running
loadgen-76f75d7468-tlhjc     2/2 Running
payments-5c797645b5-cfx47    1/1 Running
postgres-67977f4df6-rrh44    1/1 Running
redis-87cf6bc6b-kn267        1/1 Running

$ curl http://gateway:8080/health        # from inside the cluster
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
HTTP 200
```

`argo-rollouts` costs one pod in its own namespace. Host memory at the end of the lab was
4.9 GB available, basically unchanged.

---

## ⚠️ Note for whoever runs `kubectl` against the gateway next

**`gateway` is no longer a Deployment.** `kubectl get deployment gateway` returns `NotFound`, and
`kubectl rollout status deployment/gateway` / `kubectl set image deployment/gateway ...` will fail.
The equivalents are:

| Was | Now |
|---|---|
| `kubectl get deployment gateway` | `kubectl get rollout gateway` |
| `kubectl rollout status deployment/gateway` | `kubectl argo rollouts status gateway` |
| `kubectl rollout restart deployment/gateway` | `kubectl argo rollouts restart gateway` |
| `kubectl set image deployment/gateway gateway=X` | `kubectl argo rollouts set image gateway gateway=X` |
| `kubectl scale deployment/gateway --replicas=N` | `kubectl scale rollout/gateway --replicas=N` (works: Rollout implements the `scale` subresource, so an HPA also works unmodified) |

`events`, `payments`, `postgres` and `redis` are still Deployments. Replica count went from 1 to 5,
which also satisfies the ≥2 replicas a PodDisruptionBudget would need.

---

## Acceptance Criteria

### Task 1 (6 pts)
- ✅ Argo Rollouts installed: `kubectl argo rollouts version` → `v1.10.0+d90700a`; controller
  `1/1 Running` in `argo-rollouts` (needed `--server-side` for the two oversized CRDs)
- ✅ Gateway converted to Rollout: `k8s/gateway.yaml`, `apiVersion: argoproj.io/v1alpha1`,
  `kind: Rollout`, `replicas: 5`, canary `strategy`
- ✅ Canary at 20% shown: `Status: ॥ Paused`, `Message: CanaryPauseStep`, `Step: 1/5`,
  `ActualWeight: 20`; traffic split measured at **20.3%** over 260 requests in a 60 s window
- ✅ Manual promotion to 100%: `promote` → `Healthy`, `Step: 5/5`, `ActualWeight: 100` in 92 s
- ✅ Bad version aborted: canary out of the Service's ready endpoints **0.476 s** after `abort`
- ✅ Written comparison of abort vs `git revert` speed: §7.7, with Lab 5's measured 134 s

### Task 2 (4 pts)
- ✅ Multi-step canary strategy designed and applied: 9 steps, 20/40/60/80/100
- ✅ Steps observed via `--watch`: all 5 pause frames captured, updated-replicas 1→2→3→4→5
- ✅ Dashboard observation during rollout: per-`rs_hash` request rate sampled every 10 s for the
  whole 5 min 47 s run, plus the same queries executed through Grafana's datasource proxy
- ✅ Analysis of abort threshold: §"at what canary percentage", with the signal-vs-damage argument

### Bonus Task (2 pts)
- ✅ AnalysisTemplate created with Prometheus query: `k8s/analysis-template.yaml`, and `rs_hash`
  verified present on live canary targets before relying on it
- ✅ Auto-promote on good version: AnalysisRun `gateway-75968bb7c7-6-2` `Successful`, 3 × `value: [0]`
- ✅ Auto-abort on bad version: AnalysisRun `gateway-85cff5f8fc-8-2` `Failed`,
  `[0.543]` and `[0.520]`, `failed (2) > failureLimit (1)`, stable pods untouched

---

## PR description

```text
- [x] Task 1 done — Argo Rollouts installed, canary deployed, promoted + aborted
- [x] Task 2 done — multi-step canary with Grafana observation
- [x] Bonus Task done — automated canary analysis with Prometheus
```

Notes for the reviewer:

- `k8s/gateway.yaml` is now an `argoproj.io/v1alpha1` **Rollout**, 5 replicas. Anything in later
  labs that runs `kubectl ... deployment/gateway` needs the equivalents listed above.
- `labs/lab7/prometheus.yaml` was **not** applied, the Prometheus deployed in Lab 6 is a superset
  of it under the same object names, and applying the lab's copy would have dropped the events and
  payments scrape jobs plus the SLO recording rules. The `gateway` job, including the
  `rollouts-pod-template-hash → rs_hash` relabel, is byte-identical in both; verified live in §B.1.
- `labs/lab7/loadgen.yaml` collides by name with Lab 6's `Deployment/loadgen`. Lab 7's was used for
  the lab and Lab 6's was restored at the end.
- §B.5's suggested `EVENTS_URL: http://broken-on-purpose:8081` doesn't work against this gateway,
  because its readiness probe checks downstreams: the canary never becomes Ready, never receives
  traffic, and the analysis step is never reached. Both the failed attempt and a working variant
  (`EVENTS_URL: http://payments:8082`: resolves, `/health` 200, `/events` 404 → gateway 502) are
  documented.
- ArgoCD's `selfHeal: false` does **not** protect a *deleted* resource: it re-created the gateway
  Deployment 90 s after `kubectl delete deployment gateway`. Evidence in §7.2. The Application was
  switched to manual sync for the lab and restored to `automated: {selfHeal: false}` afterwards;
  final state is `Synced` / `Healthy`.
```
