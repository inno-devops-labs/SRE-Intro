# Lab 7 — Progressive Delivery: Canary Deployments

**Branch:** `feature/lab7`
**Cluster:** k3d `quickticket`, single node, k3s v1.35.5+k3s1, **arm64** (macOS / Apple Silicon host)
**Argo Rollouts:** controller + kubectl plugin **v1.10.0** (pinned, not `latest`)
**Gateway image:** `ghcr.io/r3v1k/quickticket-gateway:<sha>` (multi-arch, `imagePullPolicy: Always`)
**Raw terminal output:** `raw.log`, section `=== LAB 7 ===` (every command below is timestamped there)

- [x] Task 1 — Argo Rollouts installed, canary deployed, promoted + aborted
- [x] Task 2 — multi-step canary, observed step by step (snapshots + per-pod log counts instead of Grafana, see 7.9)
- [x] Bonus Task — automated canary analysis with in-cluster Prometheus (auto-promote + auto-abort)

---

## 7.0 Things the lab text does not mention

Each of these was hit for real; the evidence is in `raw.log`.

| # | What happened | What I did |
|---|---|---|
| 1 | The spec installs the `linux-amd64` plugin; the host is `darwin/arm64`. | Downloaded `kubectl-argo-rollouts-darwin-arm64` **v1.10.0** to `~/.local/bin` (already on `PATH`). The controller was installed from the same tag rather than `latest`, so client and server match. |
| 2 | `kubectl apply -f install.yaml` **failed** for 2 of 5 CRDs: `CustomResourceDefinition "rollouts.argoproj.io" is invalid: metadata.annotations: Too long: may not be more than 262144 bytes` (same for `analysisruns`). Client-side apply stores the whole object in the `last-applied-configuration` annotation. These two CRDs are bigger than 256 KiB. | Re-applied with `kubectl apply --server-side --force-conflicts`, which fixed the same problem for ArgoCD in Lab 5. The controller had already started without the `rollouts` CRD. For ~90 s it logged `Failed to watch ... Resource=rollouts`, then synced its informers on its own at 12:07:40 (`Started rollout workers`), so no restart was needed. |
| 3 | The Postgres DB was **empty**. `\dt` → `Did not find any relations.` and events crashed with `relation "events" does not exist`. **100% of `/events` returned 502** on every gateway pod, stable and canary. Postgres has no PVC, so the Lab 4 seed was lost when the pod was replaced. | Re-seeded with `kubectl exec -i deploy/postgres -- psql ... < app/seed.sql`, exactly as in Lab 4. The first traffic-split sample (taken against 502s) is kept in `raw.log`. 7.4 below uses only post-seed logs. |
| 4 | **The provided loadgen freezes for ~130 s whenever a gateway pod is deleted.** The loop is sequential and `curl` has no timeout. If a request is routed to a pod that has just been killed, `connect()` waits out `tcp_syn_retries=6` (~127 s). Measured: no loadgen request between **12:19:33.871 and 12:21:48.752 (134.9 s)**. The gap starts in the same second as `Killing gateway-6dd76b8f54-phdfl` (12:19:33). Consequence: during my first 7.6 run the bad canary received **zero** user requests. | Used a local copy of `labs/lab7/loadgen.yaml`. The only change is `curl -s --connect-timeout 1 --max-time 3` (diff in `raw.log`). The course file itself is untouched. The copy was used for 7.6 run 2, Task 2 and Bonus. With it, the largest gap between consecutive requests was 0.23–0.36 s, even across pod deletions. |
| 5 | In 7.3 only an env var changes, so canary and stable have **the same image**. The spec's per-pod loop prints `image=` and cannot tell them apart. | Added the `rollouts-pod-template-hash` label and `APP_VERSION` to the loop output. |
| 6 | `APP_VERSION` is not read anywhere in `app/gateway/main.py`. | Stated honestly in 7.6: `v3-bad` is a mechanics demo, not a real failure. A version that is actually bad is in B.5. |
| 0 | `k8s/gateway.yaml` is not on upstream `main`. Lab PRs stay open, so the file lives on my fork's `main` (promoted there in Lab 5 as the ArgoCD source). Cutting this branch from fork `main` would have dragged Lab 5's 10 commits (CI, ArgoCD, all of `k8s/`) into this PR. | Same approach as Lab 6: `feature/lab7` starts from upstream `main`. Its first commit promotes the Lab 5 `k8s/gateway.yaml` unchanged (Deployment, ghcr image, probes, `progressDeadlineSeconds: 60`), so the second commit shows the actual Deployment → Rollout diff. |
| 7 | The network was down for part of the session: GitHub, ghcr.io and Docker Hub timed out, and ArgoCD showed `ComparisonError ... TLS handshake timeout`. It recovered before any cluster change. Once more, during Task 2, a canary pod got `ErrImagePull ... TLS handshake timeout` from ghcr.io and recovered after backoff. | Waited for the outage to end before changing anything in the cluster. The Task 2 blip is described in 7.9. |

### ArgoCD vs. the Rollout (decision)

The `quickticket` Application syncs `k8s/` from **`main` of my fork** with `syncPolicy.automated: {}` (no `selfHeal`, no `prune`). On `main`, gateway is still a **Deployment**.

- **Option proposed:** remove `automated` for the duration of the lab (`kubectl -n argocd patch application quickticket --type json -p '[{"op":"remove","path":"/spec/syncPolicy/automated"}]'`).
- **Risk if left on:** without `selfHeal`, automated sync fires only when the Git revision changes. Lab 5 measured this: 244 s of drift and no revert. But **any** sync for any reason recreates `Deployment/gateway` with the `app=gateway` selector. That can be a commit to fork `main` (including the CI image-tag bot) or a manual Sync. The Service would then route to Deployment pods as well as Rollout pods, which breaks the weight split. `prune` is off, so the Rollout would not be removed. Two controllers would own `app=gateway` traffic.
- **Risk of the patch:** the cluster would diverge from Git silently and pushes to `main` would stop deploying. Re-enabling `automated` before the Rollout reaches `main` triggers exactly the double-ownership problem above.
- **Decision (mine as the operator): keep `automated` as is.** No commits went to `main` during the lab.
- **What was observed:** after `kubectl delete deployment gateway` the Application went `OutOfSync` (`Deployment OutOfSync`, `Service OutOfSync`) and stayed that way to the end. `operationState.finishedAt` remained `2026-09-28T07:21:55Z` the whole time. **No sync happened, and the Deployment was never recreated.** Checked at 12:08, 12:20 and 13:28.

---

## Task 1 — Manual Canary Deployment

### 7.1 Install Argo Rollouts

```
$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.10.0+d90700a
  BuildDate: 2026-08-27T15:26:09Z
  GitCommit: d90700ae8d71d141561f0c546e19f999bb335cbd
  GitTreeState: clean
  GoVersion: go1.26.7
  Compiler: gc
  Platform: darwin/arm64
```

```
$ kubectl get crd | grep argoproj.io        # after the server-side re-apply
analysisruns.argoproj.io                       2026-10-05T12:07:05Z
analysistemplates.argoproj.io                  2026-10-05T12:04:55Z
clusteranalysistemplates.argoproj.io           2026-10-05T12:04:55Z
experiments.argoproj.io                        2026-10-05T12:04:56Z
rollouts.argoproj.io                           2026-10-05T12:07:07Z
```

### 7.2 Deployment → Rollout

**Resource check before `replicas: 5`.** Node allocatable is 11 CPU / 8025284Ki (≈7837Mi). Requests already in use: 450m / 460Mi.
- 5 gateway replicas add 4 × (50m, 64Mi).
- Worst case during a canary is 7 gateway pods (default 25% surge), which gives **750m (6.8%) / 844Mi (10.8%)**.
- That leaves plenty of room; no pod went `Pending`.

The diff keeps the probes, resources, `imagePullSecrets`, `imagePullPolicy` and `progressDeadlineSeconds: 60` from Labs 4–5 untouched:

```diff
-apiVersion: apps/v1
-kind: Deployment
+apiVersion: argoproj.io/v1alpha1
+kind: Rollout
 ...
-  replicas: 1
+  # 5 replicas so canary weights map to whole pods (20% = 1 pod).
+  replicas: 5
   # Default is 600s, so a failed rollout stays invisible for 10 minutes.
   progressDeadlineSeconds: 60
+  strategy:
+    canary:
+      steps:
+        - setWeight: 20
+        - pause: {}
+        - setWeight: 60
+        - pause: {duration: 30s}
+        - setWeight: 100
```

**Order deviation:** the spec does `delete deployment` and then `apply`. That leaves a window in which `svc/gateway` has no endpoints. I applied the Rollout first, waited for `Healthy 5/5` (12 s), and only then deleted the Deployment. The Rollout's ReplicaSets select on `rollouts-pod-template-hash`, so the two never adopt each other's pods.

```
$ kubectl apply -f k8s/gateway.yaml          # 12:08:16
rollout.argoproj.io/gateway created
$ kubectl argo rollouts get rollout gateway  # 12:08:28 → Healthy, 5/5, revision:1 gateway-6dd76b8f54
$ kubectl delete deployment gateway          # 12:08:29
deployment.apps "gateway" deleted from default namespace
```

The final `k8s/gateway.yaml` in this PR has the Bonus strategy (B.3). The Task 1 and Task 2 strategies are quoted in their sections.

### 7.3 Canary paused at 20%

Trigger: `APP_VERSION: "v2"` was added to the container env, then `kubectl apply` at 12:09:07. The rollout was `Paused` by 12:09:13.

```
$ kubectl argo rollouts get rollout gateway       # 12:09:36
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/r3v1k/quickticket-gateway:2fed1f97e33a71a3e82bf86af17117b5fe257691 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS     AGE  INFO
⟳ gateway                            Rollout     ॥ Paused   80s
├──# revision:2
│  └──⧉ gateway-6458cb677d           ReplicaSet  ✔ Healthy  29s  canary
│     └──□ gateway-6458cb677d-nggq2  Pod         ✔ Running  29s  ready:1/1
└──# revision:1
   └──⧉ gateway-6dd76b8f54           ReplicaSet  ✔ Healthy  80s  stable
      ├──□ gateway-6dd76b8f54-8bmvr  Pod         ✔ Running  80s  ready:1/1
      ├──□ gateway-6dd76b8f54-9jfrs  Pod         ✔ Running  80s  ready:1/1
      ├──□ gateway-6dd76b8f54-mhd88  Pod         ✔ Running  80s  ready:1/1
      └──□ gateway-6dd76b8f54-phdfl  Pod         ✔ Running  80s  ready:1/1
```

1 canary + 4 stable, `ActualWeight: 20`. `Images` shows one tag as "(canary, stable)" because only the env changed.

### 7.4 Traffic split (in-cluster loadgen, not port-forward)

Per-pod `GET /events` counts from the gateway access logs, using only the window after the DB re-seed (`--since-time`). The loadgen is the only `/events` client; kubelet probes hit `/health`.

```
window: since=2026-10-05T12:18:03Z until=2026-10-05T12:19:15Z
pod/gateway-6458cb677d-nggq2 role=canary hash=6458cb677d image=…:2fed1f97 APP_VERSION=v2 events_requests=70 codes=[200×70 ]
pod/gateway-6dd76b8f54-8bmvr role=stable hash=6dd76b8f54 image=…:2fed1f97 APP_VERSION=- events_requests=58 codes=[200×58 ]
pod/gateway-6dd76b8f54-9jfrs role=stable hash=6dd76b8f54 image=…:2fed1f97 APP_VERSION=- events_requests=58 codes=[200×58 ]
pod/gateway-6dd76b8f54-mhd88 role=stable hash=6dd76b8f54 image=…:2fed1f97 APP_VERSION=- events_requests=67 codes=[200×67 ]
pod/gateway-6dd76b8f54-phdfl role=stable hash=6dd76b8f54 image=…:2fed1f97 APP_VERSION=- events_requests=61 codes=[200×61 ]
TOTAL=314 canary=70 share=22.3%
```

**Canary share 22.3%, expected 20%.** The four stable pods got 58–67 each (18.5–21.3%), so the canary sits inside the per-pod spread. Without a traffic router, `setWeight: 20` does not split traffic by weight at all. It only sets the pod ratio (1 of 5), and kube-proxy spreads connections randomly across ready endpoints. "20%" is therefore really "1/5 of pods", and that is why only 0/20/40/60/80/100 are reachable with 5 replicas.

The sample taken before the re-seed is in `raw.log` (22/148 = 14.9% on canary, all 502). It shows the same routing, but no user got a 200.

### 7.5 Promote → 100%

```
$ kubectl argo rollouts promote gateway            # 12:19:28
rollout 'gateway' promoted

12:19:28  ◌ Progressing  Step: 2/5  ActualWeight: 25  Updated: 3
12:19:35  ◌ Progressing  Step: 2/5  ActualWeight: 50  Updated: 3
12:19:39  ॥ Paused       Step: 3/5  ActualWeight: 60  Updated: 3     ← pause {duration: 30s}
12:20:10  ◌ Progressing  Step: 4/5  ActualWeight: 75  Updated: 5
12:20:19  ✔ Healthy      Step: 5/5  ActualWeight: 100 Updated: 5
```

(Lines condensed from 2-second snapshots; the full output is in `raw.log`.) The intermediate weights 25 and 50 are `ready canary / ready total` while new pods were still becoming Ready. **Promote → Healthy took 51 s**, of which 31 s is the configured pause.

```
$ kubectl argo rollouts get rollout gateway        # 12:20:28
Status:          ✔ Healthy
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Replicas:  Desired 5 / Current 5 / Updated 5 / Ready 5 / Available 5
├──# revision:2
│  └──⧉ gateway-6458cb677d           ReplicaSet  ✔ Healthy     11m  stable   (5 pods Running)
└──# revision:1
   └──⧉ gateway-6dd76b8f54           ReplicaSet  • ScaledDown  12m
```

Caveat: the loadgen freeze from 7.0 #4 began at 12:19:33, so this promotion ran with no user traffic. Weights and steps are unaffected, since they are controller state.

### 7.6 "Bad" version + abort

`APP_VERSION` was changed to `"v3-bad"` and applied. **Honesty note:** the gateway never reads `APP_VERSION`, so `v3-bad` serves exactly like `v2`: 47/47 of its `/events` responses were `200`. This demonstrates the **abort mechanics only**, not recovery from a real failure. A version that is really broken is tested in B.5. For Task 1 the same could be done by pointing `EVENTS_URL` at a host that passes `/health` but has no `/events` (see B.5 run 2).

**Run 1** (12:20:43, abort at 12:21:23) is in `raw.log` but **not used for timing**. The canary received **0** loadgen requests because of the loadgen freeze, and my poller's "canary IP in endpoints" column was broken by a zsh quirk (`"$CIP:true"` is parsed as the `:t` modifier). Both are noted in `raw.log`.

**Run 2** used the loadgen with timeouts: `kubectl argo rollouts retry rollout gateway` at 12:25:25, then `Paused` at 20% with canary `gateway-68ddbccc8f-wnswv` (10.42.0.65). The canary served `/events` from 12:25:35 onward, 47 requests in total. Abort followed at 12:26:21. Timing came from a per-second poll (`date -u` on every line), a `kubectl logs -f --timestamps` stream on the canary pod started before the abort, and cluster events.

```
12:26:20 rollout=Paused   abort=false canary_in_endpoints(ready)=true   endpoints=5 canary_pod=Running canary_events_served_total=46
12:26:21 rollout=Paused   abort=false canary_in_endpoints(ready)=true   endpoints=5 canary_pod=Running canary_events_served_total=47
[2026-10-05T12:26:21Z] $ kubectl argo rollouts abort gateway
rollout 'gateway' aborted
12:26:22 rollout=Degraded abort=true  canary_in_endpoints(ready)=false  endpoints=5 canary_pod=Running del=2026-10-05T12:26:51Z canary_events_served_total=47
12:26:23 rollout=Degraded abort=true  canary_in_endpoints(ready)=absent endpoints=4 canary_pod=GONE    canary_events_served_total=47
```

| Time (UTC) | Event | Source |
|---|---|---|
| 12:26:20.634 | last `GET /events` served by the canary | canary log |
| 12:26:21 | `abort` issued; `RolloutAborted`, `Killing gateway-68ddbccc8f-wnswv` | shell `date -u`, k8s events (1 s resolution) |
| 12:26:21.788 | last request of any kind served by the canary (`/health` from loadgen) | canary log |
| 12:26:21.867 | canary `Shutting down` | canary log |
| 12:26:21.970 | canary `Finished server process` | canary log |
| 12:26:22 | canary endpoint `ready=false` | poll |
| 12:26:23 | canary gone from the EndpointSlice; 4 endpoints | poll |
| 12:26:22 → 12:26:33 | replacement stable pod `ctrjn` created → Ready (7.8 s of that is the image pull, because of `imagePullPolicy: Always`) | events, pod status |

During the abort the loadgen saw **no failed request**: every response was `200` and the largest gap between consecutive requests was 0.23 s.

```
$ kubectl argo rollouts get rollout gateway       # 12:26:43
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/r3v1k/quickticket-gateway:2fed1f97e33a71a3e82bf86af17117b5fe257691 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ✖ Degraded    18m
├──# revision:3
│  └──⧉ gateway-68ddbccc8f           ReplicaSet  • ScaledDown  5m59s  canary
├──# revision:2
│  └──⧉ gateway-6458cb677d           ReplicaSet  ✔ Healthy     17m    stable
│     ├──□ gateway-6458cb677d-nggq2  Pod         ✔ Running     17m    ready:1/1
│     ├──□ gateway-6458cb677d-2wxdd  Pod         ✔ Running     7m15s  ready:1/1
│     ├──□ gateway-6458cb677d-l5crh  Pod         ✔ Running     7m15s  ready:1/1
│     ├──□ gateway-6458cb677d-gf2hs  Pod         ✔ Running     6m34s  ready:1/1
│     └──□ gateway-6458cb677d-ctrjn  Pod         ✔ Running     21s    ready:1/1
└──# revision:1
   └──⧉ gateway-6dd76b8f54           ReplicaSet  • ScaledDown  18m
```

Afterwards the manifest was set back to `APP_VERSION: "v2"` and applied. The template then equals the stable ReplicaSet, so the Rollout went straight back to `Healthy` without starting a new canary (12:27:17).

### Written answer (7.7.5): how long from `abort` to all traffic on stable, compared with `git revert` in Lab 5?

**Under 1 second.** The abort was issued at 12:26:21. The canary process was gone at **12:26:21.970**. The last request it served was at 12:26:21.788, and its last `/events` was at 12:26:20.634. From then on every request went to the four stable pods, all of them `200`, with no gap longer than 0.23 s. Full stable capacity (5/5) came back **12 s** after the abort (12:26:33). 7.8 s of that was `imagePullPolicy: Always` pulling an image that was already cached on the node. Users were never short of serving pods.

**Lab 5 `git revert` rollback: 211 s.** Push at 07:10:00Z, `Healthy` at 07:13:31Z. Lab 5's own breakdown: "push → ArgoCD notices ~197 s" (the 180 s reconciliation poll) and "sync → pods healthy ~1 s". Those two rows add up to ~198 s, so ~13 s of the 211 s is not attributed in that report. I quote it as written.

So abort was **more than 200× faster** here. The difference is not Kubernetes speed. In Lab 5, 93% of the time was ArgoCD waiting to look at Git. Abort skips Git entirely: the controller simply scales the canary ReplicaSet to 0 while the stable ReplicaSet is already running. The cases are also not equivalent:
- **Blast radius differs.** In Lab 5 the bad image never started, so nothing bad served anyone. Here the "bad" canary did take real traffic (47 `/events` between 12:25:35 and 12:26:20) until the abort. A canary limits *how many* users a bad version reaches; abort limits *for how long*.
- **Abort does not fix Git.** After the abort, the desired state still said `v3-bad`, and the Rollout stayed `Degraded` until I changed the manifest. With ArgoCD managing the Rollout, the next sync would re-apply `v3-bad` and start the bad canary again. Abort is the emergency brake measured in seconds. `git revert` is still needed to make the source of truth match, and it still costs whatever the GitOps loop costs (211 s with polling).

---

## Task 2 — Multi-Step Canary with Observation

### 7.8 Strategy

```yaml
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
```

Applying the new strategy by itself did not start a rollout: the status stayed `✔ Healthy, Step 9/9`. Only template changes create a revision.

### 7.9 Observation

Trigger at 12:28:06: `kubectl argo rollouts set image gateway gateway=ghcr.io/r3v1k/quickticket-gateway:dd50f70a6a33cb14d8ec0294cd52ba2b35ea5301`. This tag points to the **same image** (image ID `68d230ed1cc18`) as the running `2fed1f97…`, because both are CI builds of an unchanged gateway. A different tag gives a different pod-template hash, so this is a real canary of an identical binary. I chose it over the spec's `docker tag … :v2` + `k3d image import` because the manifest pulls from ghcr with `imagePullPolicy: Always`, so a local `quickticket-gateway:v2` would end in `ErrImagePull`. Traffic came from the in-cluster loadgen with timeouts.

**Replacement for the Grafana dashboard.** The Lab 3 docker-compose Grafana cannot see k3d pods, and the in-cluster Prometheus was installed only later (Bonus). So the observation here uses:
- (a) a compact poll of the Rollout status every 2 s;
- (b) a full `get rollout` snapshot every ~12 s;
- (c) request rate and canary share computed from the access logs of every gateway pod, with timestamps (stable pods streamed with `kubectl logs -f` while they lived, canary pods read afterwards).

No dashboard screenshots or values exist, and none are claimed.

Compact poll (state transitions only):

```
12:28:06 phase=Progressing step=0 updated=   ready=5 avail=5
12:28:08 phase=Progressing step=0 updated=1  ready=4 avail=4
12:28:18 phase=Paused      step=1 updated=1  ready=5 avail=5
12:29:17 phase=Progressing step=2 updated=2  ready=4 avail=4
12:29:22 phase=Paused      step=3 updated=2  ready=5 avail=5
12:30:20 phase=Progressing step=4 updated=3  ready=4 avail=4
12:30:25 phase=Paused      step=5 updated=3  ready=5 avail=5
12:31:25 phase=Progressing step=6 updated=4  ready=4 avail=4
12:31:58 phase=Paused      step=7 updated=4  ready=5 avail=5
12:32:27 phase=Progressing step=8 updated=5  ready=4 avail=4
12:32:32 phase=Healthy     step=9 updated=5  ready=5 avail=5
```

**Updated replicas climbed 1 → 2 → 3 → 4 → 5** as the weight went 20 → 40 → 60 → 80 → 100. The whole rollout took 266 s (12:28:06 → 12:32:32).

Snapshots at three of the steps (the full set is in `raw.log`):

```
--- 12:29:30Z
Status:          ॥ Paused
Message:         CanaryPauseStep
  Step:          3/9
  SetWeight:     40
  ActualWeight:  40
Images:          ghcr.io/r3v1k/quickticket-gateway:2fed1f97e33a71a3e82bf86af17117b5fe257691 (stable)
                 ghcr.io/r3v1k/quickticket-gateway:dd50f70a6a33cb14d8ec0294cd52ba2b35ea5301 (canary)
Replicas:  Desired 5 / Current 5 / Updated 2 / Ready 5 / Available 5
├──# revision:5
│  └──⧉ gateway-69fb7db8f6           ReplicaSet  ✔ Healthy     85s    canary
│     ├──□ gateway-69fb7db8f6-fqkxv  Pod         ✔ Running     84s    ready:1/1
│     └──□ gateway-69fb7db8f6-pq9bh  Pod         ✔ Running     14s    ready:1/1
├──# revision:4
│  └──⧉ gateway-6458cb677d           ReplicaSet  ✔ Healthy     20m    stable
│     ├──□ gateway-6458cb677d-nggq2  Pod         ✔ Running     20m    ready:1/1
│     ├──□ gateway-6458cb677d-2wxdd  Pod         ✔ Running     10m    ready:1/1
│     └──□ gateway-6458cb677d-l5crh  Pod         ✔ Running     10m    ready:1/1

--- 12:30:42Z
Status:          ॥ Paused
  Step:          5/9
  SetWeight:     60
  ActualWeight:  60
Replicas:  Desired 5 / Current 5 / Updated 3 / Ready 5 / Available 5
│  └──⧉ gateway-69fb7db8f6           ReplicaSet  ✔ Healthy     2m37s  canary   (fqkxv, pq9bh, 8lvkd)
│  └──⧉ gateway-6458cb677d           ReplicaSet  ✔ Healthy     21m    stable   (nggq2, 2wxdd)

--- 12:31:42Z
Status:          ◌ Progressing
Message:         more replicas need to be updated
  Step:          6/9
  SetWeight:     80
  ActualWeight:  75
Replicas:  Desired 5 / Current 5 / Updated 4 / Ready 4 / Available 4
│     └──□ gateway-69fb7db8f6-pn766  Pod         ⚠ ErrImagePull  18s    ready:0/1

--- 12:32:06Z
Status:          ॥ Paused
  Step:          7/9
  SetWeight:     80
  ActualWeight:  80
Replicas:  Desired 5 / Current 5 / Updated 4 / Ready 5 / Available 5

--- 12:32:32Z (final)
Status:          ✔ Healthy
  Step:          9/9
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/r3v1k/quickticket-gateway:dd50f70a6a33cb14d8ec0294cd52ba2b35ea5301 (stable)
Replicas:  Desired 5 / Current 5 / Updated 5 / Ready 5 / Available 5
```

**Traffic per step** (`GET /events` from the loadgen, counted on every gateway pod; window boundaries come from the 2-second poll):

| Window | From → To | s | /events total | on canary | canary share | /events rps | non-200 |
|---|---|---:|---:|---:|---:|---:|---:|
| →20% (canary starting) | 12:28:06 → 12:28:18 | 12 | 52 | 0 | 0.0% | 4.33 | 0 |
| **Paused 20%** | 12:28:18 → 12:29:17 | 59 | 254 | **49** | 19.3% | 4.31 | 0 |
| →40% | 12:29:17 → 12:29:22 | 5 | 22 | 6 | 27.3% | 4.40 | 0 |
| **Paused 40%** | 12:29:22 → 12:30:20 | 58 | 249 | **113** | 45.4% | 4.29 | 0 |
| →60% | 12:30:20 → 12:30:25 | 5 | 23 | 8 | 34.8% | 4.60 | 0 |
| **Paused 60%** | 12:30:25 → 12:31:25 | 60 | 257 | **168** | 65.4% | 4.28 | 0 |
| →80% (ErrImagePull) | 12:31:25 → 12:31:58 | 33 | 143 | 107 | 74.8% | 4.33 | 0 |
| **Paused 80%** | 12:31:58 → 12:32:27 | 29 | 124 | **100** | 80.6% | 4.28 | 0 |
| →100% | 12:32:27 → 12:32:32 | 5 | 22 | 22 | 100.0% | 4.40 | 0 |
| **Total** | 12:28:06 → 12:32:32 | 266 | 1146 | 573 | | | 0 |

What the observation answers:
- **Did the request rate stay steady?** Yes. `/events` held 4.28–4.60 rps on every step, with zero non-200 and a largest gap of 0.36 s between consecutive requests. The loadgen also sends one `/health` per `/events`, so total gateway traffic is about twice that.
- **Did updated replicas climb 1→5?** Yes (see the poll). The canary share tracked the weight on every paused step: 19.3 / 45.4 / 65.4 / 80.6% for 20 / 40 / 60 / 80 (largest deviation +5.4 pp; with 5 replicas and random kube-proxy balancing, the weight is a pod ratio, not an exact split).
- **Two things a dashboard would probably have hidden:**
  1. **Ready drops to 4/5 at every step.** At each step the controller kills a stable pod in the same second it creates the canary pod (events: `Killing gateway-6458cb677d-…` at 12:29:17, 12:30:20, 12:31:25, 12:32:27). For ~5 s per step the service runs on 4 pods, because the spec sets no `maxUnavailable`. Setting `maxUnavailable: 0` would probably keep capacity at 5/5; I did not test it.
  2. **The 80% step took 33 s instead of ~5 s.** The new canary pod got `Failed to pull image ... Head "https://ghcr.io/v2/...": net/http: TLS handshake timeout` at 12:31:35 → `ImagePullBackOff` → pulled at 12:31:50. That is `imagePullPolicy: Always` meeting a flaky registry. For 33 s the rollout ran 3 canary + 1 stable (`ActualWeight: 75`) at 80% capacity. Users were unaffected (0 errors), but the same flake on a 1-replica service would have been an outage.

### Written answer: at what canary percentage would you want an automated abort? Why?

**At 20%, the first step. Automated analysis should run from the first step and stay attached to every later one.** The reasoning comes from the measured exposure at ~4.3 `/events` rps:

| If a 100%-broken version is detected only at the end of… | canary `/events` already served (cumulative, measured above) |
|---|---:|
| 20% pause | **49** |
| 40% pause | 168 |
| 60% pause | 344 |
| 80% pause | 551 |
| never (100%) | 573 + every request after |

Every step you wait multiplies the damage: each manual "let's look at it at the next step" multiplied the failed requests by 1.6–3.4× in this run (49 → 168 → 344 → 551). 20% is the point where a bad version costs the least: 49 requests, ~0.83 rps, in a 59 s window. Its blast radius is bounded by the step duration, so the abort has to be **automated**. A human reading a dashboard needs longer than a 60 s step, and a missed pause lets the rollout walk on to 40% by itself.

The limit is sample size, not courage. At 20% the canary got ~50 requests per minute. That is plenty to catch a gross failure: the B.5 canary failed **100% of its 90 `/events`**, and analysis aborted it 104 s after the canary pod was created (13:21:13 → 13:22:57); 60 s of that is the template's `initialDelay`. It is far too few for a subtle regression. A 1% error-rate increase means ~0.5 extra errors per minute at 20%, which is statistically invisible. So the plan is:
- abort automatically at **20%** on large deviations (error rate several times stable's, `/health` failing, crash loops);
- keep analysis running at **40–60%**, where the canary gets 113–168 requests/min, to catch smaller regressions before the majority of users is on the new version;
- never rely on the 80% step for detection: by then ~550 requests have already hit the canary in this run.

---

## Bonus Task — Automated Canary Analysis

### B.1 In-cluster Prometheus

Pre-check: there was no `monitoring` namespace, no `monitoring.coreos.com` CRDs, and no `prometheus` ClusterRole/Binding, so no kube-prometheus-stack leftovers.

```
$ kubectl apply -f labs/lab7/prometheus.yaml
namespace/monitoring created ... service/prometheus created
$ kubectl -n monitoring rollout status deployment/prometheus --timeout=180s
deployment "prometheus" successfully rolled out

$ curl -s 'http://localhost:9091/api/v1/targets?state=active' | python3 -c ...
gateway-69fb7db8f6-pn766 rs= 69fb7db8f6 up
gateway-69fb7db8f6-8lvkd rs= 69fb7db8f6 up
gateway-69fb7db8f6-q8c28 rs= 69fb7db8f6 up
gateway-69fb7db8f6-fqkxv rs= 69fb7db8f6 up
gateway-69fb7db8f6-pq9bh rs= 69fb7db8f6 up
```

All 5 pods are `up`, and `rs_hash` matches the pods' `rollouts-pod-template-hash`.

### B.2 AnalysisTemplate

```
$ kubectl apply -f labs/lab7/analysis-template.yaml
analysistemplate.argoproj.io/gateway-error-rate created
$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   0s
```

A copy is committed as `k8s/analysis-template.yaml`, as the spec's submit step asks. Since ArgoCD syncs `k8s/`, the Rollout and its template will travel together once this reaches `main`.

### B.3 Analysis step in the strategy

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

This is applied together with the live image, so it started no rollout (`Healthy`, `Step 6/6`).

### B.4 Good version auto-promotes

Trigger at 12:36:45: `kubectl argo rollouts set image gateway gateway=ghcr.io/r3v1k/quickticket-gateway:2fed1f97…`, with the loadgen running.

```
12:36:54 phase=Paused      step=1 updated=1 lastAR:
12:37:12 phase=Progressing step=2 updated=1 lastAR: gateway-6458cb677d-6-2=Running vals=
12:38:14 phase=Progressing step=2 updated=1 lastAR: gateway-6458cb677d-6-2=Running vals=[0],
12:38:31 phase=Progressing step=2 updated=1 lastAR: gateway-6458cb677d-6-2=Running vals=[0],[0],
12:38:53 phase=Progressing step=3 updated=3 lastAR: gateway-6458cb677d-6-2=Successful vals=[0],[0],[0],
12:39:16 phase=Paused      step=4 updated=3 lastAR: gateway-6458cb677d-6-2=Successful vals=[0],[0],[0],
12:39:38 phase=Healthy     step=6 updated=5 lastAR: gateway-6458cb677d-6-2=Successful vals=[0],[0],[0],
```

Paused → AnalysisRun `Running` → after the 60 s `initialDelay`, 3 measurements of `[0]` 20 s apart → `Successful` → 50% → 100% → **Healthy at 12:39:38, 173 s after `set image`, with no human action.**

### B.5 Bad version auto-aborts

**Run 1, exactly as in the spec (`EVENTS_URL=http://broken-on-purpose:8081`, `GATEWAY_TIMEOUT_MS=2000`): the analysis never ran.** In this repo the gateway's `/health` is a *deep* check. It calls `{EVENTS_URL}/health` and returns 503 when events is unreachable (`app/gateway/main.py:215-240`). The Lab 4 readiness and liveness probes use `/health`. So the canary never became Ready, never joined the Service, and served **0 requests**:

```
13:20:38  Readiness probe failed: HTTP probe failed with statuscode: 503
13:20:38  Liveness probe failed: HTTP probe failed with statuscode: 503
13:20:38  Container gateway failed liveness probe, will be restarted
Status:   ✖ Degraded
Message:  ProgressDeadlineExceeded: ReplicaSet "gateway-688fbb5d44" has timed out progressing.
```

The readiness gate worked as a zero-traffic canary check, which is a good outcome. But it ended as `Degraded` via `progressDeadlineSeconds: 60`, **not** as an abort: `status.abort` was empty. Stable stayed at **4/5 Ready**, with the crash-looping canary still holding the 5th slot, until the next change. `progressDeadlineAbort: true` would turn this into a real abort. No AnalysisRun was created.

**Run 2: a bad version that passes readiness.** I set `EVENTS_URL=http://payments:8082`. That host exists and its `/health` returns 200, so the pod becomes Ready and takes traffic. But payments has no `/events`: the 404 makes `raise_for_status()` fail, and the gateway returns **502** on every `/events`. This is still an `EVENTS_URL` misconfiguration, just one the probes cannot see.

```
13:21:18 phase=Paused      step=1 updated=1
13:21:40 phase=Progressing step=2 updated=1 lastAR: gateway-679bb4dbf4-8-2=Running vals=
13:22:38 phase=Progressing step=2 updated=1 lastAR: gateway-679bb4dbf4-8-2=Running vals=[0.4594594594594595],
13:22:59 phase=Degraded    step=0           lastAR: gateway-679bb4dbf4-8-2=Failed  vals=[0.4594594594594595],[0.45736434108527124],
```

Canary up at 13:21:13 → **auto-abort at ~13:22:57**, after two failed measurements, without waiting for the third.

```
$ kubectl get analysisrun
NAME                     STATUS       AGE
gateway-6458cb677d-6-2   Successful   50m
gateway-679bb4dbf4-8-2   Failed       5m41s
```

```
$ kubectl get analysisrun gateway-679bb4dbf4-8-2 -o yaml      # spec + status
spec:
  args:
  - name: canary-hash
    value: 679bb4dbf4
  metrics:
  - count: 3
    failureLimit: 1
    initialDelay: 60s
    interval: 20s
    name: error-rate
    provider:
      prometheus:
        address: http://prometheus.monitoring.svc.cluster.local:9090
        query: |
          (
            sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}",status=~"5.."}[60s]))
            or on() vector(0)
          )
          /
          sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}"}[60s]))
    successCondition: result[0] < 0.05
status:
  completedAt: "2026-10-05T13:22:57Z"
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
  - count: 2
    failed: 2
    measurements:
    - finishedAt: "2026-10-05T13:22:37Z"
      phase: Failed
      startedAt: "2026-10-05T13:22:37Z"
      value: '[0.4594594594594595]'
    - finishedAt: "2026-10-05T13:22:57Z"
      phase: Failed
      startedAt: "2026-10-05T13:22:57Z"
      value: '[0.45736434108527124]'
    metadata:
      ResolvedPrometheusQuery: |
        (
          sum(rate(gateway_requests_total{rs_hash="679bb4dbf4",status=~"5.."}[60s]))
          or on() vector(0)
        )
        /
        sum(rate(gateway_requests_total{rs_hash="679bb4dbf4"}[60s]))
    name: error-rate
    phase: Failed
  phase: Failed
  startedAt: "2026-10-05T13:21:37Z"
```

**The measurement values are `[0.46]`, not the `[1]` the spec expects.** Prometheus shows why. Everything this canary served:

```
{'path': '/health', 'status': '200'} 112
{'path': '/events', 'status': '502'} 90
```

The canary failed **100%** of its business traffic (90/90 `/events`), but the template's denominator also counts `/health`. That includes the loadgen's `/health` calls and the kubelet probes, which are 200 by definition on any pod that is Ready. 90 / (90 + 112) = 44.6%, which is in line with the measured 0.46. Here it does not matter, because 0.46 ≫ 0.05. But a version that broke 8% of `/events` would show up as ~4%, under the 5% threshold, and be **promoted**. The fix is `path!="/health"` (or `path="/events"`) in both the numerator and the denominator.

Final state after the auto-abort (Degraded, stable serving):

```
$ kubectl argo rollouts get rollout gateway      # 13:23:00
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 8: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/r3v1k/quickticket-gateway:2fed1f97e33a71a3e82bf86af17117b5fe257691 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         4
  Available:     4
NAME                                 KIND         STATUS         AGE    INFO
⟳ gateway                            Rollout      ✖ Degraded     74m
├──# revision:8
│  ├──⧉ gateway-679bb4dbf4           ReplicaSet   • ScaledDown   107s   canary
│  └──α gateway-679bb4dbf4-8-2       AnalysisRun  ✖ Failed       83s    ✖ 2
├──# revision:7
│  └──⧉ gateway-688fbb5d44           ReplicaSet   • ScaledDown   3m42s
├──# revision:6
│  ├──⧉ gateway-6458cb677d           ReplicaSet   ◌ Progressing  73m    stable
│  │  ├──□ gateway-6458cb677d-p8wwz  Pod          ✔ Running      46m    ready:1/1
│  │  ├──□ gateway-6458cb677d-pbd54  Pod          ✔ Running      44m    ready:1/1
│  │  ├──□ gateway-6458cb677d-9v985  Pod          ✔ Running      43m    ready:1/1
│  │  ├──□ gateway-6458cb677d-wpkwk  Pod          ✔ Running      43m    ready:1/1
│  │  └──□ gateway-6458cb677d-mwrz7  Pod          ✔ Running      3s     ready:0/1
│  └──α gateway-6458cb677d-6-2       AnalysisRun  ✔ Successful   45m    ✔ 3
...
```

(`Ready: 4` is the 5th stable pod `mwrz7` being recreated at that moment; all 5 were Ready in the 13:27:52 snapshot below.)

**Revert + retry, as in the spec:** `EVENTS_URL=http://events:8081` and `GATEWAY_TIMEOUT_MS=5000` were restored and applied at 13:27:37. The template now equals the stable ReplicaSet (`6458cb677d`), so the Rollout went `Healthy` immediately as revision 9 and created no new canary. The `retry` that followed (13:27:42) therefore had nothing to do; the status stayed `Healthy` (the spec presents retry as the step that redeploys, but here it was a no-op):

```
$ kubectl argo rollouts get rollout gateway      # 13:27:52
Status:          ✔ Healthy
  Step:          6/6
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/r3v1k/quickticket-gateway:2fed1f97e33a71a3e82bf86af17117b5fe257691 (stable)
Replicas:  Desired 5 / Current 5 / Updated 5 / Ready 5 / Available 5
├──# revision:9
│  └──⧉ gateway-6458cb677d           ReplicaSet   ✔ Healthy     78m    stable  (5 pods Running, ready:1/1)
├──# revision:8
│  ├──⧉ gateway-679bb4dbf4           ReplicaSet   • ScaledDown  6m40s
│  └──α gateway-679bb4dbf4-8-2       AnalysisRun  ✖ Failed      6m16s  ✖ 2
├──# revision:6
│  └──α gateway-6458cb677d-6-2       AnalysisRun  ✔ Successful  50m    ✔ 3
```

### B.6 Cleanup

`kubectl delete` of the loadgen (13:28:08). In-cluster Prometheus, the AnalysisTemplate and the Argo Rollouts controller stay installed.

### Written answer: what metric would you add beyond error rate?

**Canary-scoped latency, specifically p99 of `/events`, compared against stable rather than an absolute number.** Two facts from this run support it.

**1. It is the only other signal the gateway actually exports.** Prometheus lists exactly these `gateway_*` series: `gateway_requests_total{method,path,status}` and the histogram `gateway_request_duration_seconds{method,path}` (`_bucket/_sum/_count`). `/metrics` also declares `gateway_retry_total`, `gateway_circuit_breaker_transitions_total` and `gateway_rate_limit_rejections_total`. They have no series yet, because those labeled counters have never been incremented. They cannot be queried until they fire once, and an analysis on them would hit the "empty result" problem the template comments warn about. Baseline on stable at 12:35: p99 `/events` **0.064 s**, p99 `/health` **0.0099 s**.

**2. Error rate is blind to the failure modes this gateway is built to absorb.** The gateway retries upstream calls (`RETRY_MAX=3`, exponential backoff from `RETRY_BASE_DELAY_MS=100`) and has a per-request timeout (`GATEWAY_TIMEOUT_MS`). A canary whose upstream calls fail transiently, or whose dependency became slow, still returns **200**, just later: retries turn errors into latency. A canary with `GATEWAY_TIMEOUT_MS` set too high does the same. Error rate reads 0% and the analysis promotes it. Only the duration histogram shows it:

```promql
histogram_quantile(0.99,
  sum by (le) (rate(gateway_request_duration_seconds_bucket{rs_hash="{{args.canary-hash}}", path="/events"}[60s])))
```

with a `successCondition` relative to the same query on the stable hash (for example canary p99 < 1.5 × stable p99, or < 0.5 s absolute as a backstop). It should exclude `/health` for the same reason the error-rate query should: B.5 showed the probe traffic dilutes a 100% `/events` failure to 46%.

In the same spirit I would also change the existing template so it measures what users hit (`path!="/health"`), and add `progressDeadlineAbort: true`. Run 1 of B.5 showed that a canary failing readiness otherwise leaves the service at 4/5 capacity in `Degraded`, with no abort and no analysis.

---

## State at the end of the lab

- `rollout/gateway`: `Healthy`, revision 9, 5/5 `ghcr.io/r3v1k/quickticket-gateway:2fed1f97…`, Bonus strategy (analysis step).
- `analysistemplate/gateway-error-rate` installed; AnalysisRuns: 1 `Successful` and 1 `Failed`.
- `monitoring/prometheus` running; the loadgen has been deleted.
- ArgoCD `quickticket`: `syncPolicy.automated` **unchanged**, `OutOfSync` (Git `main` still has `Deployment/gateway`), no sync since 2026-09-28. **This still needs a decision:** keep automated sync off this app until the Rollout manifest is on `main`, or leave it as is.
