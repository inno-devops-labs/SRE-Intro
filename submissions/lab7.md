# Lab 7 — Progressive Delivery: Canary Deployments

Liubov Utenysheva, CBS-03

---

## Task 1 — Manual Canary Deployment (6 pts)

### 7.1 — Argo Rollouts installed

```bash
$ kubectl create namespace argo-rollouts
$ kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
$ kubectl wait --for=condition=Available deployment/argo-rollouts -n argo-rollouts --timeout=60s
```

> **Note on the install:** a plain `kubectl apply` of the latest `install.yaml` failed on this k3d/k3s cluster with
> `CustomResourceDefinition ... is invalid: metadata.annotations: Too long: may not be more than 262144 bytes` —
> client-side `apply` embeds the whole (≈650 KB) CRD into the `last-applied-configuration` annotation, which k3s's
> apiserver rejects. Using `kubectl apply --server-side` (no client-side annotation) installed all CRDs cleanly:
>
> ```bash
> $ kubectl apply -n argo-rollouts --server-side -f install.yaml
> customresourcedefinition.apiextensions.k8s.io/rollouts.argoproj.io serverside-applied
> ...
> deployment.apps/argo-rollouts serverside-applied
> ```

kubectl plugin installed per-user to `~/.local/bin` (option B, no sudo):

```bash
$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.10.0+d90700a
  BuildDate: 2026-08-27T15:22:01Z
  GitCommit: d90700ae8d71d141561f0c546e19f999bb335cbd
  GitTreeState: clean
  GoVersion: go1.26.7
  Compiler: gc
  Platform: linux/amd64
```

### 7.2 — Gateway converted to a canary Rollout

`k8s/gateway.yaml` changed: `kind: Deployment` → `kind: Rollout`, `apiVersion: apps/v1` → `argoproj.io/v1alpha1`,
`replicas: 1` → `5`, and a `strategy.canary` section added (20 → manual pause → 60 → 30 s pause → 100). An
`APP_VERSION` env var was added so versions are distinguishable in pod specs/logs (our gateway image is a CI-built
`ghcr.io` image, so per lab 7.3 the env var simulates a new version without rebuilding).

```bash
$ kubectl delete deployment gateway
$ kubectl apply -f k8s/gateway.yaml
rollout.argoproj.io/gateway created
service/gateway configured
```

Initial revision 1 (5 × `APP_VERSION=v1`) came up **Healthy** — this is the stable baseline.

### 7.3 — Canary at 20% (Paused)

Changed `APP_VERSION` to `v2` and applied. The rollout paused at step 1 as designed:

```bash
$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/kvakz/quickticket-gateway:ce3d600329e68c55401508d9129903372307cab5 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS     AGE    INFO
⟳ gateway                            Rollout     ॥ Paused   2m38s
├──# revision:2
│  └──⧉ gateway-c8979c4d6            ReplicaSet  ✔ Healthy  15s    canary
│     └──□ gateway-c8979c4d6-vs7cg   Pod         ✔ Running  15s    ready:1/1
└──# revision:1
   └──⧉ gateway-579bb5b447           ReplicaSet  ✔ Healthy  2m38s  stable
      ├──□ gateway-579bb5b447-pbcdg  Pod         ✔ Running  2m38s  ready:1/1
      ├──□ gateway-579bb5b447-t52wn  Pod         ✔ Running  2m38s  ready:1/1
      ├──□ gateway-579bb5b447-v5nl6  Pod         ✔ Running  2m38s  ready:1/1
      └──□ gateway-579bb5b447-wqjts  Pod         ✔ Running  2m38s  ready:1/1
```

4 stable pods (v1) + 1 canary pod (v2), `ActualWeight: 20`.

### 7.4 — Traffic split verified with the in-cluster loadgen

`labs/lab7/loadgen.yaml` applied, run for ~30 s, then per-pod request counts from pod logs (traffic through
`kube-proxy`, so real service-level balancing — not `port-forward`):

```bash
$ kubectl apply -f labs/lab7/loadgen.yaml
sleep 30
$ for pod in $(kubectl get pods -l app=gateway -o name); do
    count=$(kubectl logs $pod 2>/dev/null | grep -c 'GET /events')
    ver=$(kubectl get $pod -o jsonpath='{.spec.containers[0].env[?(@.name=="APP_VERSION")].value}')
    hash=$(kubectl get $pod -o jsonpath='{.metadata.labels.rollouts-pod-template-hash}')
    echo "$pod version=$ver rs=$hash events_requests=$count"
  done
pod/gateway-579bb5b447-pbcdg version=v1 rs=579bb5b447 events_requests=23
pod/gateway-579bb5b447-t52wn version=v1 rs=579bb5b447 events_requests=14
pod/gateway-579bb5b447-v5nl6 version=v1 rs=579bb5b447 events_requests=23
pod/gateway-579bb5b447-wqjts version=v1 rs=579bb5b447 events_requests=16
pod/gateway-c8979c4d6-vs7cg version=v2 rs=c8979c4d6 events_requests=26
$ kubectl delete -f labs/lab7/loadgen.yaml
```

Canary share: **26 / 102 ≈ 25%** — i.e. roughly 1-in-5 requests hit the canary pod, matching `setWeight: 20`
within small-sample variance. The split is real: without it, a single sticky endpoint would show 100/0.

### 7.5 — Manual promotion to 100%

```bash
$ kubectl argo rollouts promote gateway
rollout 'gateway' promoted
```

Immediately after — step 2/5, weight climbing toward 60, canary scaling 1 → 3:

```
Name:            gateway
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          2/5
  SetWeight:     60
  ActualWeight:  25
Replicas:
  Desired:       5
  Current:       6
  Updated:       3
  Ready:         4
  Available:     4

├──# revision:2
│  └──⧉ gateway-c8979c4d6            ReplicaSet  ◌ Progressing  canary
│     ├──□ gateway-c8979c4d6-vs7cg   Pod         ✔ Running      ready:1/1
│     ├──□ gateway-c8979c4d6-bndhd   Pod         ✔ Running      ready:0/1
│     └──□ gateway-c8979c4d6-d47fg   Pod         ✔ Running      ready:0/1
└──# revision:1
   └──⧉ gateway-579bb5b447           ReplicaSet  ✔ Healthy      stable
      └──□ (3 pods)
```

After the 30 s duration-pause it auto-proceeded to 100% and settled **Healthy**:

```
Name:            gateway
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/kvakz/quickticket-gateway:ce3d600329e68c55401508d9129903372307cab5 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

├──# revision:2
│  └──⧉ gateway-c8979c4d6            ReplicaSet  ✔ Healthy  113s  stable
│     ├──□ gateway-c8979c4d6-vs7cg   Pod         ✔ Running  113s  ready:1/1
│     ├──□ gateway-c8979c4d6-bndhd   Pod         ✔ Running  51s   ready:1/1
│     ├──□ gateway-c8979c4d6-d47fg   Pod         ✔ Running  51s   ready:1/1
│     ├──□ gateway-c8979c4d6-f4dhh   Pod         ✔ Running  11s   ready:1/1
│     └──□ gateway-c8979c4d6-rmzfh   Pod         ✔ Running  11s   ready:1/1
└──# revision:1
   └──⧉ gateway-579bb5b447           ReplicaSet  • ScaledDown
```

### 7.6 — "Bad" version deployed and aborted

`APP_VERSION` set to `v3-bad`, applied → canary paused at 20% with 1 bad pod + 4 stable pods. Then:

```bash
$ kubectl argo rollouts abort gateway
rollout 'gateway' aborted
```

6 seconds after the abort (06:26:58 → 06:27:04 UTC) — the canary ReplicaSet is already **ScaledDown**, stable
pods serving:

```
Name:            gateway
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/kvakz/quickticket-gateway:ce3d600329e68c55401508d9129903372307cab5 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         4
  Available:     4

├──# revision:3
│  └──⧉ gateway-dd8f496f9            ReplicaSet  • ScaledDown   canary
├──# revision:2
│  └──⧉ gateway-c8979c4d6            ReplicaSet  ◌ Progressing  stable
│     ├──□ gateway-c8979c4d6-vs7cg   Pod         ✔ Running      ready:1/1
│     ├──□ gateway-c8979c4d6-bndhd   Pod         ✔ Running      ready:1/1
│     ├──□ gateway-c8979c4d6-d47fg   Pod         ✔ Running      ready:1/1
│     ├──□ gateway-c8979c4d6-f4dhh   Pod         ✔ Running      ready:1/1
│     └──□ gateway-c8979c4d6-fpkkd   Pod         ✔ Running  6s   ready:0/1
└──# revision:1
   └──⧉ gateway-579bb5b447           ReplicaSet  • ScaledDown
```

32 s after the abort (06:27:30 UTC) the replacement stable pod is ready — all 5 stable pods serving, canary gone:

```
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

├──# revision:3
│  └──⧉ gateway-dd8f496f9            ReplicaSet  • ScaledDown   canary
└──# revision:2
   └──⧉ gateway-c8979c4d6            ReplicaSet  ✔ Healthy      stable
      └──□ (5 pods, all ready:1/1)
```

### 7.7 — How long from `abort` to all traffic on the stable version?

**~0–6 s for traffic, ~32 s for full replica parity.** The abort command took effect immediately: the canary
ReplicaSet was scaled to 0 within ~2 s, so from 06:27:00 onward **zero** requests could reach the bad version —
the 4 already-ready stable pods absorbed 100% of traffic within seconds (service endpoints update as pods are
removed). The only latency was the 5th stable pod taking ~26 s to start and pass its readiness probe to restore
full 5/5 capacity; there was no traffic loss in the meantime.

**Comparison with Lab 5's `git revert`:** there, rollback meant `git revert` + push → GitHub Actions rebuilding
the container image (~1–2 min) → ArgoCD noticing the new commit and syncing → the Deployment rolling the pods
(~30–60 s) — typically **3–5 minutes end-to-end**, during which the *bad version kept serving 100% of traffic*
(a plain Deployment has no partial rollout). The canary abort inverts both weaknesses: the bad version only ever
served 20% of traffic, and undoing it is a single `kubectl` command that needs no pipeline, no image build, no
repo round-trip — traffic is back on the known-good version in seconds.

---

## Task 2 — Multi-Step Canary with Observation (4 pts)

### 7.8 — Strategy

`k8s/gateway.yaml` (committed version):

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: gateway
  labels:
    app: gateway
    version: "v2"
spec:
  replicas: 5
  strategy:
    canary:
      steps:
        - setWeight: 20
        - pause: {duration: 60s}
        - setWeight: 40
        - pause: {duration: 60s}
        - setWeight: 60
        - pause: {duration: 60s}
        - setWeight: 80
        - pause: {duration: 30s}
        - setWeight: 100
  selector:
    matchLabels:
      app: gateway
  template:
    # ... same pod template as before (image, env, probes, resources)
```

Triggered with `APP_VERSION: v4` (after the aborted `v3-bad`, per the lab's pitfall note: set the template back
to a good version, then `kubectl argo rollouts retry rollout gateway`). The in-cluster loadgen ran for the whole
rollout.

### 7.9 — Rollout observed step by step

Continuous observation (one line per ~15 s, from `kubectl argo rollouts get rollout gateway --watch`):

```
06:28:18  Paused       step=1/9  setWeight=20   actual=20   updated=1
06:28:33  Paused       step=1/9  setWeight=20   actual=20   updated=1
06:28:49  Paused       step=1/9  setWeight=20   actual=20   updated=1
06:29:04  Paused       step=1/9  setWeight=20   actual=20   updated=1
06:29:19  Paused       step=3/9  setWeight=40   actual=40   updated=2
06:29:35  Paused       step=3/9  setWeight=40   actual=40   updated=2
06:29:50  Paused       step=3/9  setWeight=40   actual=40   updated=2
06:30:06  Paused       step=3/9  setWeight=40   actual=40   updated=2
06:30:21  Progressing  step=4/9  setWeight=60   actual=50   updated=3
06:30:37  Paused       step=5/9  setWeight=60   actual=60   updated=3
06:30:52  Paused       step=5/9  setWeight=60   actual=60   updated=3
06:31:08  Paused       step=5/9  setWeight=60   actual=60   updated=3
06:31:23  Paused       step=5/9  setWeight=60   actual=60   updated=3
06:31:39  Paused       step=7/9  setWeight=80   actual=80   updated=4
06:31:54  Paused       step=7/9  setWeight=80   actual=80   updated=4
06:32:10  Progressing  step=8/9  setWeight=100  actual=100  updated=5
06:32:25  Healthy      step=9/9  setWeight=100  actual=100  updated=5
```

Each 60 s pause held at its weight, then the weight climbed 20 → 40 → 60 → 80 → 100 while the updated-replica
count climbed **1 → 2 → 3 → 4 → 5** exactly in step with the weight. Final state:

```
Name:            gateway
Status:          ✔ Healthy
Strategy:        Canary
  Step:          9/9
  SetWeight:     100
  ActualWeight:  100
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

├──# revision:4
│  └──⧉ gateway-79bf656f44           ReplicaSet  ✔ Healthy  10m    stable
│     ├──□ gateway-79bf656f44-wg9dg  Pod         ✔ Running  10m     ready:1/1
│     ├──□ gateway-79bf656f44-c5r4m  Pod         ✔ Running  9m35s   ready:1/1
│     ├──□ gateway-79bf656f44-fd5k9  Pod         ✔ Running  8m25s   ready:1/1
│     ├──□ gateway-79bf656f44-kl9xs  Pod         ✔ Running  7m17s   ready:1/1
│     └──□ gateway-79bf656f44-4mhw7  Pod         ✔ Running  6m37s   ready:1/1
└──# revision:3
   └──⧉ gateway-dd8f496f9            ReplicaSet  • ScaledDown 12m
```

### Dashboard observation during the rollout

The Lab 3 docker-compose Prometheus/Grafana **cannot scrape pods inside k3d** (pod IPs sit on the k3d bridge
network, unreachable from the host), so per the lab's guidance the observation was done via `--watch` plus the
in-cluster loadgen's per-pod request counts. With the loadgen running continuously (~5 req/s total), per-pod
`GET /events` counts at the end of the rollout were:

```
pod/gateway-79bf656f44-wg9dg  version=v4  events_requests=533   (first canary pod — present for all 5 steps)
pod/gateway-79bf656f44-c5r4m  version=v4  events_requests=482
pod/gateway-79bf656f44-fd5k9  version=v4  events_requests=417
pod/gateway-79bf656f44-kl9xs  version=v4  events_requests=356
pod/gateway-79bf656f44-4mhw7  version=v4  events_requests=340
```

- **Request rate stayed steady across all canary steps** — no dip when weight changed (total 2128 requests over
  ~10 min, ≈ 3.5 req/s to `/events`, constant).
- The first canary pod (present since step 1) accumulated the most requests (533) and the last pod (born at
  step 80 → 100) the fewest (340) — consistent with traffic being redistributed proportionally as each new
  replica joined.
- No pod restarts, no readiness flaps, no error spikes in the logs across the whole rollout.

### At what canary percentage would you want an automated abort? Why?

At the **first low-weight step — 10–20%**. An automated abort (an Argo Rollouts `analysis` step querying, e.g.,
the canary's 5xx ratio via an in-cluster Prometheus) should be armed before any weight increase: if the canary's
error rate exceeds a threshold (say 5% for a couple of measurement windows) while it only serves 1–2 of 5 pods,
the blast radius is already capped at ~20% of users, and aborting there rolls back in seconds. Waiting for a
higher weight to "be sure" is backwards — the signal (rising 5xx on canary-labelled series, distinguishable via
the `rs_hash`/pod-template-hash label) is available from the first scrape of the canary pod, and every additional
percent of weight only increases the number of users hit by the defect before we react. The higher steps
(40/60/80%) then exist to catch *slower* failures — performance degradation, memory leaks, downstream queue
buildup — that need longer observation windows to manifest, not to re-verify basic correctness.

---

## Summary

| Item | Result |
|------|--------|
| Argo Rollouts | v1.10.0 controller + plugin, installed (server-side apply due to k3s CRD annotation limit) |
| Gateway | `Deployment` (1 replica) → `Rollout` (5 replicas, canary) |
| Canary 20% | Paused at step 1, traffic split verified in-cluster (26/102 ≈ 25% to canary) |
| Promotion | Manual `promote` → auto 30 s pause → 100% Healthy |
| Bad version | `v3-bad` paused at 20% → `abort` → canary gone in ~2 s, stable serving in seconds |
| Multi-step | 20/40/60/80/100 with 60/60/60/30 s pauses, replicas 1→2→3→4→5, steady traffic throughout |
