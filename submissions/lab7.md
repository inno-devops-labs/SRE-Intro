# Lab 7 — Progressive Delivery: Canary Deployments

## Task 1 — Manual Canary Deployment

### Pre-flight: ArgoCD auto-sync paused

The `quickticket` ArgoCD Application tracks `main` / `k8s/` with `syncPolicy: {automated: {}}`. While this lab's Rollout only exists on `feature/lab7`, any push to `main` (e.g. CI's own image-tag bump commit) would make ArgoCD re-sync the old `kind: Deployment` back in next to the Rollout — two controllers fighting over `app: gateway` pods. Auto-sync was switched off for the duration of the lab:

```bash
kubectl patch application quickticket -n argocd --type merge -p '{"spec":{"syncPolicy":null}}'
```

To be re-enabled after this branch is merged (`{"spec":{"syncPolicy":{"automated":{}}}}`), at which point `main` itself declares the Rollout.

### 7.1 — Argo Rollouts installed

```bash
kubectl create namespace argo-rollouts
kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
```
Same failure as ArgoCD in Lab 5:
```
CustomResourceDefinition.apiextensions.k8s.io "rollouts.argoproj.io" is invalid: metadata.annotations: Too long: may not be more than 262144 bytes
CustomResourceDefinition.apiextensions.k8s.io "analysisruns.argoproj.io" is invalid: metadata.annotations: Too long: may not be more than 262144 bytes
```
The `rollouts` and `analysisruns` CRDs are too large for client-side apply's `last-applied-configuration` annotation. Fixed the same way:
```bash
kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml --server-side --force-conflicts
kubectl wait --for=condition=Available deployment/argo-rollouts -n argo-rollouts --timeout=180s
```
```
NAME                             READY   STATUS    RESTARTS   AGE
argo-rollouts-69645d4879-77ll9   1/1     Running   0          9m49s
```
(The controller took ~9 min to become Ready — all of it was pulling `quay.io/argoproj/argo-rollouts:v1.10.0`, not a config problem.)

kubectl plugin installed per-user into `~/bin` (no sudo):
```
$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.10.0+d90700a
  BuildDate: 2026-08-27T15:22:01Z
  GitCommit: d90700ae8d71d141561f0c546e19f999bb335cbd
  GitTreeState: clean
  GoVersion: go1.26.7
  Compiler: gc
  Platform: linux/amd64
```

### 7.2 — Gateway converted to a Rollout

`k8s/gateway.yaml` — the only changes vs. the Lab 5 Deployment:

```yaml
apiVersion: argoproj.io/v1alpha1     # was apps/v1
kind: Rollout                         # was Deployment
metadata:
  name: gateway
spec:
  replicas: 5                         # was 1 — 20% of 5 = exactly 1 canary pod
  strategy:
    canary:
      steps:
        - setWeight: 20
        - pause: {}                   # infinite — waits for `promote`
        - setWeight: 60
        - pause: {duration: 30s}
        - setWeight: 100
  selector:
    matchLabels:
      app: gateway
  template:                           # unchanged (+ APP_VERSION env, see 7.3)
```
(This is the Task 1 strategy. The committed `k8s/gateway.yaml` now holds the Bonus strategy with the analysis step and `APP_VERSION=v4` — see B.3. The Task 2 strategy is shown in 7.8.)

The Service is untouched: its selector is just `app: gateway`, so it matches both stable and canary pods — which is exactly what makes replica-count-based traffic splitting work without a mesh/ingress.

```bash
kubectl delete deployment gateway
kubectl apply -f k8s/gateway.yaml
```
```
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/g-0-rg/quickticket-gateway:340a6ba4557ab7fd19d4fef12961e0ccbe8fc327 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                KIND        STATUS     AGE  INFO
⟳ gateway                           Rollout     ✔ Healthy  16s
└──# revision:1
   └──⧉ gateway-984d854f7           ReplicaSet  ✔ Healthy  16s  stable
      ├──□ gateway-984d854f7-575kw  Pod         ✔ Running  16s  ready:1/1
      ├──□ gateway-984d854f7-8f2qg  Pod         ✔ Running  15s  ready:1/1
      ├──□ gateway-984d854f7-b557k  Pod         ✔ Running  15s  ready:1/1
      ├──□ gateway-984d854f7-pmfwj  Pod         ✔ Running  15s  ready:1/1
      └──□ gateway-984d854f7-r5r6s  Pod         ✔ Running  15s  ready:1/1
```
The very first Rollout goes straight to 100% — there is no "previous stable" to canary against.

### 7.3 — New version deployed as canary — Paused at 20%

New version simulated by adding an env var (changes the pod template hash → new ReplicaSet):
```yaml
            - name: APP_VERSION
              value: "v2"
```
```bash
kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts get rollout gateway
```
```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/g-0-rg/quickticket-gateway:340a6ba4557ab7fd19d4fef12961e0ccbe8fc327 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                KIND        STATUS     AGE  INFO
⟳ gateway                           Rollout     ॥ Paused   47s
├──# revision:2
│  └──⧉ gateway-dbdc86bb5           ReplicaSet  ✔ Healthy  20s  canary
│     └──□ gateway-dbdc86bb5-477hq  Pod         ✔ Running  20s  ready:1/1
└──# revision:1
   └──⧉ gateway-984d854f7           ReplicaSet  ✔ Healthy  47s  stable
      ├──□ gateway-984d854f7-575kw  Pod         ✔ Running  47s  ready:1/1
      ├──□ gateway-984d854f7-b557k  Pod         ✔ Running  46s  ready:1/1
      ├──□ gateway-984d854f7-pmfwj  Pod         ✔ Running  46s  ready:1/1
      └──□ gateway-984d854f7-r5r6s  Pod         ✔ Running  46s  ready:1/1
```
4 stable + 1 canary = 5 total. Note the controller *replaced* a stable pod rather than adding a 6th — total capacity stays at `replicas`, so canary weight = canary pods / total pods.

### 7.4 — Traffic split verified (in-cluster loadgen)

Before this step `/events` was returning `502 {"detail":"Events service unavailable"}` on **every** pod — unrelated to the Rollout: the events service logged `psycopg2.errors.UndefinedTable: relation "events" does not exist`. Postgres runs without a PVC, so its data was lost when the pod restarted; re-seeded the same way as in Lab 4:
```bash
kubectl exec -i deploy/postgres -- psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql
# CREATE TABLE / CREATE TABLE / INSERT 0 5
```

Then ran `labs/lab7/loadgen.yaml` for 60s and counted `GET /events` per pod. Both versions run the same image, so pods are identified by their `rollouts-pod-template-hash` and `APP_VERSION`:
```
pod/gateway-984d854f7-575kw rs=984d854f7 APP_VERSION=<unset> events_requests=42
pod/gateway-984d854f7-b557k rs=984d854f7 APP_VERSION=<unset> events_requests=48
pod/gateway-984d854f7-pmfwj rs=984d854f7 APP_VERSION=<unset> events_requests=55
pod/gateway-984d854f7-r5r6s rs=984d854f7 APP_VERSION=<unset> events_requests=54
pod/gateway-dbdc86bb5-477hq rs=dbdc86bb5 APP_VERSION=v2     events_requests=55
```
Canary: **55 / 254 = 21.7%** of requests vs. `setWeight: 20` — within normal variance for a ~250-request sample (stable pods individually range 42–55, i.e. each ~17–22%). The split is kube-proxy picking uniformly among 5 ready endpoints, not a weighted router.

### 7.5 — Promoted to 100%

```bash
kubectl argo rollouts promote gateway
```
~12s later — at step 3 (60%), auto-paused for 30s:
```
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

NAME                                KIND        STATUS     AGE    INFO
⟳ gateway                           Rollout     ॥ Paused   2m52s
├──# revision:2
│  └──⧉ gateway-dbdc86bb5           ReplicaSet  ✔ Healthy  2m25s  canary
│     ├──□ gateway-dbdc86bb5-477hq  Pod         ✔ Running  2m25s  ready:1/1
│     ├──□ gateway-dbdc86bb5-2dfm4  Pod         ✔ Running  12s    ready:1/1
│     └──□ gateway-dbdc86bb5-sg2j4  Pod         ✔ Running  12s    ready:1/1
└──# revision:1
   └──⧉ gateway-984d854f7           ReplicaSet  ✔ Healthy  2m52s  stable
      ├──□ gateway-984d854f7-pmfwj  Pod         ✔ Running  2m51s  ready:1/1
      └──□ gateway-984d854f7-r5r6s  Pod         ✔ Running  2m51s  ready:1/1
```
Then, with no further input, through to 100%:
```
Paused - CanaryPauseStep
Progressing - more replicas need to be updated
Progressing - updated replicas are still becoming available
Progressing - old replicas are pending termination
Progressing - updated replicas are still becoming available
Progressing - waiting for all steps to complete
Healthy
promote -> Healthy: 53s
```
```
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/g-0-rg/quickticket-gateway:340a6ba4557ab7fd19d4fef12961e0ccbe8fc327 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                KIND        STATUS        AGE    INFO
⟳ gateway                           Rollout     ✔ Healthy     3m32s
├──# revision:2
│  └──⧉ gateway-dbdc86bb5           ReplicaSet  ✔ Healthy     3m5s   stable
│     ├──□ gateway-dbdc86bb5-477hq  Pod         ✔ Running     3m5s   ready:1/1
│     ├──□ gateway-dbdc86bb5-2dfm4  Pod         ✔ Running     52s    ready:1/1
│     ├──□ gateway-dbdc86bb5-sg2j4  Pod         ✔ Running     52s    ready:1/1
│     ├──□ gateway-dbdc86bb5-8s2nb  Pod         ✔ Running     11s    ready:1/1
│     └──□ gateway-dbdc86bb5-m66tb  Pod         ✔ Running     11s    ready:1/1
└──# revision:1
   └──⧉ gateway-984d854f7           ReplicaSet  • ScaledDown  3m32s
```
`promote` → `Healthy` took 53s, of which 30s is the configured pause; the rest is pod startup. revision:2 is now labelled `stable`; revision:1 is scaled to 0 but kept for history.

### 7.6 — Bad version deployed and aborted

A bad version that only differs by an env var *name* (`APP_VERSION=v3-bad`) isn't actually bad — it would be indistinguishable from 7.3. To make the abort meaningful the canary was given a real misconfiguration, applied from a temporary copy of the manifest (the committed `k8s/gateway.yaml` stays at the good `v2`). Ran it two ways, because the first attempt surfaced something worth recording.

**Measuring rollback:** a probe pod inside the cluster (`curlimages/curl`, through the Service → kube-proxy, same as loadgen) hit `GET /events` every 0.1s and logged `<unix-ts> <http_code>`, so the error rate seen by clients could be lined up against the moment of `abort`.

#### Attempt A — `EVENTS_URL=http://broken-on-purpose:8081`: canary never got traffic

```
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          0/5
  SetWeight:     20
  ActualWeight:  0
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         4
  Available:     4

├──# revision:3
│  └──⧉ gateway-868465f5d6           ReplicaSet  ◌ Progressing   2m25s  canary
│     └──□ gateway-868465f5d6-6mccq  Pod         ⚠ ErrImagePull  2m25s  ready:0/1,restarts:2
```
```
Warning   Unhealthy   Readiness probe failed: HTTP probe failed with statuscode: 503
Warning   Unhealthy   Liveness probe failed: HTTP probe failed with statuscode: 503
Normal    Killing     Container gateway failed liveness probe, will be restarted
```
The gateway's `/health` (`app/gateway/main.py:215`) checks `events` and `payments` and returns 503 if either is down. With `EVENTS_URL` broken, the canary is **never Ready**, so it never joins the Service endpoints: the probe logged **327/327 × 200** during this window — users saw nothing. Instead the liveness probe kept killing it, and (with `imagePullPolicy: Always`) every restart re-pulled from ghcr.io, one of which failed transiently → `ErrImagePull`. The Rollout never reaches `Paused`, it just sits at step 0 `Progressing`. Real cost: **capacity**, not errors — a stable pod was removed to make room, so only 4/5 were serving.

```bash
kubectl argo rollouts abort gateway
# abort -> canary gone & 5/5 stable available: 15.7s
```
The 15.7s is almost entirely starting a fresh 5th stable pod; the canary was never serving, so there was nothing to roll back from the user's point of view. The readiness probe was the safety net here, not the canary.

#### Attempt B — `GATEWAY_TIMEOUT_MS=1`: canary serves real errors → abort

`/health` passes its own `timeout=2`, so it stays green, but every real `/events` call uses the shared httpx client with the 1 ms timeout → `504 Events service timeout`. This is the realistic dangerous case: passes health checks, fails users.

```diff
-              value: "5000"     # GATEWAY_TIMEOUT_MS
+              value: "1"
-              value: "v2"       # APP_VERSION
+              value: "v3-bad"
```
```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/g-0-rg/quickticket-gateway:340a6ba4557ab7fd19d4fef12961e0ccbe8fc327 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ॥ Paused      12m
├──# revision:5
│  └──⧉ gateway-67c6cf5668           ReplicaSet  ✔ Healthy     2m45s  canary
│     └──□ gateway-67c6cf5668-7rh7p  Pod         ✔ Running     2m45s  ready:1/1
├──# revision:4
│  └──⧉ gateway-dbdc86bb5            ReplicaSet  ✔ Healthy     12m    stable
│     ├──□ gateway-dbdc86bb5-477hq   Pod         ✔ Running     12m    ready:1/1
│     ├──□ gateway-dbdc86bb5-2dfm4   Pod         ✔ Running     10m    ready:1/1
│     ├──□ gateway-dbdc86bb5-sg2j4   Pod         ✔ Running     10m    ready:1/1
│     └──□ gateway-dbdc86bb5-m66tb   Pod         ✔ Running     9m19s  ready:1/1
...
```
Probe, 40s window while paused at 20%:
```
    263 200
     66 504
```
**66 / 329 = 20.1% errors** — the blast radius is exactly the canary weight.

```bash
kubectl argo rollouts abort gateway
```
```
B: abort -> canary removed from Service endpoints: 0.35s
B: abort -> canary pod gone & 5/5 stable available: 18.4s
```
Per-second probe codes around the abort (`abort_ts=1791127884`):
```
1791127881 200 4    1791127881 504 4
1791127882 200 6    1791127882 504 3
1791127883 200 6    1791127883 504 2
1791127884 200 7    1791127884 504 1     ← abort issued in this second
1791127885 200 8
1791127886 200 8
1791127887 200 9
1791127888 200 8
1791127889 200 8
```
Last 504 was in the same second as `abort`; the next 20s were 163/163 × 200.

After abort:
```
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 5
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/g-0-rg/quickticket-gateway:340a6ba4557ab7fd19d4fef12961e0ccbe8fc327 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                KIND        STATUS        AGE    INFO
⟳ gateway                           Rollout     ✖ Degraded    13m
├──# revision:5
│  └──⧉ gateway-67c6cf5668          ReplicaSet  • ScaledDown  3m39s  canary
├──# revision:4
│  └──⧉ gateway-dbdc86bb5           ReplicaSet  ✔ Healthy     13m    stable
│     ├──□ gateway-dbdc86bb5-477hq  Pod         ✔ Running     13m    ready:1/1
│     ├──□ gateway-dbdc86bb5-2dfm4  Pod         ✔ Running     10m    ready:1/1
│     ├──□ gateway-dbdc86bb5-sg2j4  Pod         ✔ Running     10m    ready:1/1
│     ├──□ gateway-dbdc86bb5-m66tb  Pod         ✔ Running     10m    ready:1/1
│     └──□ gateway-dbdc86bb5-phvxs  Pod         ✔ Running     39s    ready:1/1
├──# revision:3
│  └──⧉ gateway-868465f5d6          ReplicaSet  • ScaledDown  8m5s
└──# revision:1
   └──⧉ gateway-984d854f7           ReplicaSet  • ScaledDown  13m
```
`Degraded` here means "the desired spec was rejected", not "the service is unhealthy" — all 5 pods are the known-good `v2`. Recovered by re-applying the good manifest (`kubectl apply -f k8s/gateway.yaml`): the spec now matches the stable ReplicaSet's template, so the Rollout returned to `✔ Healthy` immediately without creating any new pods.

### 7.7 — Answer

**How long from `abort` to all traffic serving the stable version? Compare with `git revert` rollback from Lab 5.**

**Under 1 second.** The canary pod left the Service endpoints 0.35s after `abort`, and the client-side probe logged its last 504 in the same second the abort was issued. Full capacity restoration (canary pod deleted + 5th stable pod started and Ready) took 18.4s, but that tail doesn't affect users — the 4 stable pods were already serving 100% of traffic.

Abort is this fast because nothing has to be built, pulled or started: the stable ReplicaSet was never touched, its pods are already Running and Ready, and abort just removes the canary pod from the endpoint list.

`git revert` in Lab 5 is a full new deploy of the old version: commit → push → ArgoCD notices (up to ~3 min poll, or near-instant with manual `argocd app sync`) → new ReplicaSet → image pull → readiness → old pods replaced. The Kubernetes part alone was 10–40s in Lab 5 (`1/1 Running` 42s after the fix synced), plus ArgoCD's detection latency on top — so **roughly 40s–3.5 min vs. <1s**, and during all of that the bad version is serving **100%** of traffic, not 20%.

The two aren't alternatives, though. Abort only puts the cluster back; Git still declares the bad version. The right flow is abort first (stop the bleeding in seconds), then `git revert` so Git, ArgoCD and the cluster agree again — otherwise the next sync re-deploys the bad version.

---

## Task 2 — Multi-Step Canary with Observation

### 7.8 — Strategy

`k8s/gateway.yaml` during Task 2 (replaced by the Bonus strategy afterwards):
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
No manual gates — the whole thing runs unattended in ~4.5 min unless someone runs `abort`. With `replicas: 5` each step is exactly one more pod (20% = 1 pod), so the weights map onto pods cleanly.

The rollout was triggered by bumping `APP_VERSION` `v2` → `v3` in the same `kubectl apply` as the strategy change. The strategy alone wouldn't trigger anything — it isn't part of the pod template, so it doesn't change the template hash. (`kubectl argo rollouts set image` from the lab wasn't used: CI only publishes one image tag for the current commit, and a manifest change keeps `k8s/gateway.yaml` the source of truth anyway.)

### 7.9 — Observation setup

As the lab notes, the docker-compose Prometheus/Grafana from Lab 3 can't scrape pods on k3d's private network, so instead of a Grafana panel the "dashboard" during the rollout was built from three in-cluster sources running at the same time:

| Signal | Source |
|---|---|
| Step / weight / replica counts | `kubectl argo rollouts get rollout gateway --watch` captured to a file, plus a poller logging `currentStepIndex`, `phase`, `updatedReplicas`, `availableReplicas`, Set/Actual weight every 2s |
| Background traffic | `labs/lab7/loadgen.yaml` (`/events` + `/health` loop) |
| Client-side request rate + status codes | probe pod (same as Task 1) logging `<unix-ts> <http_code>` for every `GET /events` through the Service, every 0.1s |

The probe log was then bucketed by the step boundaries from the poller → request rate and error rate per step. 30s baseline before `apply`, 20s tail after `Healthy`.

### `--watch` output

Raw `--watch` redraws the whole screen ~1×/s (286 frames over the rollout). Condensed to only the frames where status/step/weight/replicas changed:

```
frame | status        | step | SetWeight | ActualWeight | Updated | Available
------+---------------+------+-----------+--------------+---------+----------
    1 | ◌ Progressing | 0/9  |  20       |   0          | 1       | 4
   13 | ॥ Paused      | 1/9  |  20       |  20          | 1       | 5
   72 | ◌ Progressing | 2/9  |  40       |  20          | 1       | 5
   73 | ◌ Progressing | 2/9  |  40       |  25          | 1       | 4
   74 | ◌ Progressing | 2/9  |  40       |  25          | 2       | 4
   85 | ॥ Paused      | 3/9  |  40       |  40          | 2       | 5
  144 | ◌ Progressing | 4/9  |  60       |  40          | 2       | 5
  145 | ◌ Progressing | 4/9  |  60       |  50          | 2       | 4
  146 | ◌ Progressing | 4/9  |  60       |  50          | 3       | 4
  159 | ॥ Paused      | 5/9  |  60       |  60          | 3       | 5
  219 | ◌ Progressing | 6/9  |  80       |  60          | 3       | 5
  220 | ◌ Progressing | 6/9  |  80       |  75          | 4       | 4
  239 | ॥ Paused      | 7/9  |  80       |  80          | 4       | 5
  268 | ◌ Progressing | 8/9  | 100       |  80          | 4       | 5
  269 | ◌ Progressing | 8/9  | 100       | 100          | 4       | 4
  270 | ◌ Progressing | 8/9  | 100       | 100          | 5       | 4
  286 | ✔ Healthy     | 9/9  | 100       | 100          | 5       | 5
```

Full frames at four of the pause steps (older scaled-down revisions trimmed):

**Step 1/9 — 20%**
```
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/9
  SetWeight:     20
  ActualWeight:  20
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

⟳ gateway                            Rollout     ॥ Paused      27m
├──# revision:7
│  └──⧉ gateway-7bbc59467b           ReplicaSet  ✔ Healthy     19s  canary
│     └──□ gateway-7bbc59467b-47n87  Pod         ✔ Running     19s  ready:1/1
├──# revision:6
│  └──⧉ gateway-dbdc86bb5            ReplicaSet  ✔ Healthy     26m  stable
│     ├──□ gateway-dbdc86bb5-477hq   Pod         ✔ Running     26m  ready:1/1
│     ├──□ gateway-dbdc86bb5-2dfm4   Pod         ✔ Running     24m  ready:1/1
│     ├──□ gateway-dbdc86bb5-sg2j4   Pod         ✔ Running     24m  ready:1/1
│     └──□ gateway-dbdc86bb5-m66tb   Pod         ✔ Running     24m  ready:1/1
```

**Step 3/9 — 40%**
```
Status:          ॥ Paused
  Step:          3/9
  SetWeight:     40
  ActualWeight:  40
  Updated:       2
  Available:     5

├──# revision:7
│  └──⧉ gateway-7bbc59467b           ReplicaSet  ✔ Healthy     89s  canary
│     ├──□ gateway-7bbc59467b-47n87  Pod         ✔ Running     89s  ready:1/1
│     └──□ gateway-7bbc59467b-nlkr7  Pod         ✔ Running     19s  ready:1/1
├──# revision:6
│  └──⧉ gateway-dbdc86bb5            ReplicaSet  ✔ Healthy     28m  stable
│     ├──□ gateway-dbdc86bb5-2dfm4   Pod         ✔ Running     25m  ready:1/1
│     ├──□ gateway-dbdc86bb5-sg2j4   Pod         ✔ Running     25m  ready:1/1
│     └──□ gateway-dbdc86bb5-m66tb   Pod         ✔ Running     25m  ready:1/1
```

**Step 5/9 — 60%**
```
Status:          ॥ Paused
  Step:          5/9
  SetWeight:     60
  ActualWeight:  60
  Updated:       3
  Available:     5

├──# revision:7
│  └──⧉ gateway-7bbc59467b           ReplicaSet  ✔ Healthy     2m39s  canary
│     ├──□ gateway-7bbc59467b-47n87  Pod         ✔ Running     2m39s  ready:1/1
│     ├──□ gateway-7bbc59467b-nlkr7  Pod         ✔ Running     89s    ready:1/1
│     └──□ gateway-7bbc59467b-tx9pn  Pod         ✔ Running     20s    ready:1/1
├──# revision:6
│  └──⧉ gateway-dbdc86bb5            ReplicaSet  ✔ Healthy     29m    stable
│     ├──□ gateway-dbdc86bb5-2dfm4   Pod         ✔ Running     27m    ready:1/1
│     └──□ gateway-dbdc86bb5-m66tb   Pod         ✔ Running     26m    ready:1/1
```

**Step 7/9 — 80%**
```
Status:          ॥ Paused
  Step:          7/9
  SetWeight:     80
  ActualWeight:  80
  Updated:       4
  Available:     5

├──# revision:7
│  └──⧉ gateway-7bbc59467b           ReplicaSet  ✔ Healthy     3m54s  canary
│     ├──□ gateway-7bbc59467b-47n87  Pod         ✔ Running     3m54s  ready:1/1
│     ├──□ gateway-7bbc59467b-nlkr7  Pod         ✔ Running     2m44s  ready:1/1
│     ├──□ gateway-7bbc59467b-tx9pn  Pod         ✔ Running     95s    ready:1/1
│     └──□ gateway-7bbc59467b-gbvkm  Pod         ✔ Running     25s    ready:1/1
├──# revision:6
│  └──⧉ gateway-dbdc86bb5            ReplicaSet  ✔ Healthy     30m    stable
│     └──□ gateway-dbdc86bb5-2dfm4   Pod         ✔ Running     28m    ready:1/1
```

**Final**
```
Status:          ✔ Healthy
Strategy:        Canary
  Step:          9/9
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/g-0-rg/quickticket-gateway:340a6ba4557ab7fd19d4fef12961e0ccbe8fc327 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5
```
Total: `apply` → `Healthy` in **280s** (configured pauses = 210s; the other 70s is 5 pod startups at ~11–16s each).

### Dashboard observation

Per-step client-side metrics from the probe (bucketed by step boundaries from the poller):

| Phase | Weight (set/actual) | Updated / Available | Duration | Requests | Rate | Non-200 |
|---|---|---|---:|---:|---:|---:|
| baseline (before apply) | — | 0 / 5 | 31s | 252 | 8.1 rps | 0 |
| step 0 — scaling to 20% | 20 / 0 | 1 / **4** | 11s | 90 | 8.2 rps | 0 |
| step 1 — paused | 20 / 20 | 1 / 5 | 59s | 479 | 8.1 rps | 0 |
| step 2 — scaling to 40% | 40 / 25 | 1→2 / **4** | 11s | 90 | 8.2 rps | 0 |
| step 3 — paused | 40 / 40 | 2 / 5 | 59s | 482 | 8.2 rps | 0 |
| step 4 — scaling to 60% | 60 / 50 | 2→3 / **4** | 11s | 89 | 8.1 rps | 0 |
| step 5 — paused | 60 / 60 | 3 / 5 | 59s | 482 | 8.2 rps | 0 |
| step 6 — scaling to 80% | 80 / 75 | 4 / **4** | 15s | 122 | 8.1 rps | 0 |
| step 7 — paused | 80 / 80 | 4 / 5 | 30s | 243 | 8.1 rps | 0 |
| step 8 — scaling to 100% | 100 / 100 | 4→5 / **4** | 16s | 131 | 8.2 rps | 0 |
| step 9 — Healthy | 100 / 100 | 5 / 5 | 8s | 65 | 8.1 rps | 0 |
| after (20s tail) | — | 5 / 5 | 20s | 164 | 8.2 rps | 0 |
| **total** | | | | **2715** | | **0** |

**Answers to the lab's observation questions:**

- **Does request rate stay steady across canary steps?** Yes — 8.1–8.2 rps in every phase, including the transitions, and 0 non-200 out of 2715 requests. `v3` is a good version, so this is what a healthy rollout looks like: it's invisible to clients.
- **Does the updated-replica count climb 1 → 2 → 3 → 4 → 5 as weight climbs?** Yes, exactly one pod per step, and at every pause `ActualWeight == SetWeight` (20/40/60/80/100).
- **At which step would I abort if I saw elevated errors?** See answer below — as early as possible, i.e. at the 20% step.

**Something the table shows that's easy to miss — capacity dips during every transition.** At each step the `Available` count drops to **4**, and `ActualWeight` passes through intermediate values that aren't in the strategy (25 = 1/4, 50 = 2/4, 75 = 3/4). Frame 73 shows why:
```
│  └──⧉ gateway-7bbc59467b           ReplicaSet  ◌ Progressing  70s  canary
│     ├──□ gateway-7bbc59467b-47n87  Pod         ✔ Running      70s  ready:1/1
│     └──□ gateway-7bbc59467b-nlkr7  Pod         ◌ Pending      0s   ready:0/1
├──# revision:6
│  └──⧉ gateway-dbdc86bb5            ReplicaSet  ✔ Healthy      27m  stable
│     ├──□ gateway-dbdc86bb5-477hq   Pod         ◌ Terminating  27m  ready:1/1
```
A stable pod is already `Terminating` while the new canary pod is still `Pending`. With no traffic router, the basic canary uses the Rollout's `maxUnavailable` (default 25% → 1 of 5) and `maxSurge` (default 25% → 1), and here the controller used the "unavailable" budget: kill one stable, then start one canary. So for ~11–16s per step (~64s in total) the service runs at 80% capacity. At 8 rps this is invisible (0 errors). Near saturation, it would be a self-inflicted 20% capacity loss on every deploy. Fix: `maxUnavailable: 0` + `maxSurge: 1` under `strategy.canary` (surge first, then scale down stable). Not applied here, to keep the manifest matching the lab's spec, but it's the setting I'd use for a real service.

### Answer

**At what canary percentage would you want an automated abort? Why?**

**At 20%, the very first step — and the check should keep running at every later step, not just once.**

- **Blast radius grows with every step.** Task 1 showed it directly: a bad canary at 20% produced exactly 20.1% errors (66/329). The same bug at 60% hits 60% of users, at 80% it's basically an outage. Abort is equally instant at any step (<1s, Task 1), so the only thing waiting buys is more failed requests. The earliest step that actually carries real traffic is the cheapest place to catch it.
- **20% is enough signal for gross failures, not for subtle ones.** At 20% of ~8 rps the canary sees ~1.6 rps → ~100 requests in a 60s pause. That's plenty to detect a broken version (in Task 1 every canary request failed — 100% vs. a stable baseline of 0%). But it can't reliably detect a 0.5% error-rate regression or a p99 latency creep: with ~100 samples that's 0–1 bad requests, which is noise. So the policy I'd use is:
  - **20%:** strict gate on hard failures — error rate clearly above stable (e.g. >5% absolute, or canary error rate ≥ 2× stable), readiness/restarts. Abort automatically.
  - **40–80%:** same analysis keeps running with more samples per step, which is where subtler regressions (small error-rate increase, p95/p99 latency vs. stable) become statistically visible. Abort automatically there too.
- **Compare canary against stable, not against a fixed threshold.** Both run side by side on the same traffic at the same time, so "canary 5xx rate vs. stable 5xx rate" cancels out noise from downstream dependencies (e.g. if `events` blips, both get errors and the canary shouldn't be blamed). That's exactly what the bonus task's `rs_hash` / `canary-hash` label enables.
- **Not 100%.** Once at 100% there's no stable left to compare against or fall back to, and rollback becomes a full redeploy again (Lab 5-style). The last automated check has to pass at 80%.

---

## Bonus Task — Automated Canary Analysis

### B.1 — In-cluster Prometheus

```bash
kubectl apply -f labs/lab7/prometheus.yaml
```
Getting it running took most of the time on this task, for reasons that had nothing to do with Prometheus config:

1. `ImagePullBackOff`: `docker.io/prom/prometheus:v3.11.2` → `net/http: TLS handshake timeout` against `registry-1.docker.io`, repeatedly. Docker Hub was unreachable from the k3d node at the time.
2. The image was already on the host (from Lab 3), so tried importing it. `k3d image import` reported `Successfully imported 1 image(s)` but the image never appeared in the node's containerd (`crictl images` — nothing). The host's Docker uses the containerd image store, and `docker save` exports a multi-arch index without the layers. `docker save --platform linux/amd64 | ctr -n k8s.io images import -` did land it (`crictl images` and `ctr images check` → `complete`).
3. Even then, the kubelet ignored it: with `IfNotPresent` it kept "Pulling" (queued behind the hung Docker Hub pull), and with `imagePullPolicy: Never` it failed with `ErrImageNeverPull: Container image "prom/prometheus:v3.11.2" is not present` — while `crictl` listed it. My best guess, not confirmed: k8s v1.35's kubelet image-pull tracking (`KubeletEnsureSecretPulledImages`) treats an image that was side-loaded after kubelet start, not pulled by the kubelet, as unverified.
4. **Fix:** pointed the live Deployment at the same release on Prometheus's official quay.io mirror. Running in ~25s:
   ```bash
   kubectl -n monitoring patch deployment prometheus --type json -p \
     '[{"op":"replace","path":"/spec/template/spec/containers/0/image","value":"quay.io/prometheus/prometheus:v3.11.2"},
       {"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"IfNotPresent"}]'
   ```
   `labs/lab7/prometheus.yaml` itself is unchanged — only the live object was patched.

Targets (queried from inside the cluster, so no port-forward was needed):
```
gateway-7bbc59467b-jkrgq rs= 7bbc59467b up
gateway-7bbc59467b-tx9pn rs= 7bbc59467b up
gateway-7bbc59467b-nlkr7 rs= 7bbc59467b up
gateway-7bbc59467b-47n87 rs= 7bbc59467b up
gateway-7bbc59467b-gbvkm rs= 7bbc59467b up
```
All 5 gateway pods `up`, each with `rs_hash` = its `rollouts-pod-template-hash`. This relabel is what lets one query select only the canary's series.

### B.2 — AnalysisTemplate

Copied to `k8s/analysis-template.yaml`, so it's in the GitOps path next to the Rollout that references it. Content is unchanged from `labs/lab7/analysis-template.yaml`.
```
$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   0s
```
The four design choices, in my own words:
1. **`initialDelay: 60s`.** The canary pod is brand new. Prometheus has to discover it through k8s SD, scrape it a few times at 5s intervals, and collect enough samples for `rate(...[60s])`. Querying earlier returns an empty vector → measurement error → false abort.
2. **`or on() vector(0)` on the numerator.** A healthy canary has *no* 5xx series at all (the counter label set is never created), so the numerator would be empty rather than 0. The fallback makes "no errors" mean 0.
3. **Strict denominator.** If the canary got no traffic, the result should be "can't measure" (error → abort), not 0/x = "perfect". Adding `or vector(0)` there would quietly promote a version nobody has actually exercised.
4. **`{{args.canary-hash}}`** comes from `podTemplateHashValue: Latest`, so the query only covers the canary ReplicaSet's pods. Otherwise the 4 healthy stable pods would dilute a broken canary's error rate by ~5×.

### B.3 — Analysis wired into the Rollout

`k8s/gateway.yaml` (this is the strategy now committed):
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
The analysis step is *blocking*: the Rollout stays at 20% until the AnalysisRun finishes. That's 60s delay + 3 × 20s, so ~100–120s.

### B.4 — Good version → auto-promoted

Trigger: `APP_VERSION` `v3` → `v4` (same reason as Task 2: CI publishes only one image tag, and the env change produces a new pod-template hash). `labs/lab7/loadgen.yaml` ran throughout. A poller logged phase, step and the latest AnalysisRun every 2s:
```
+1s   Progressing step=0 ar=[]
+19s  Paused      step=1 ar=[]
+38s  Progressing step=2 ar=[gateway-58bbf67b5f-8-2 Running ]
+98s  Progressing step=2 ar=[gateway-58bbf67b5f-8-2 Running [0]]
+118s Progressing step=2 ar=[gateway-58bbf67b5f-8-2 Running [0] [0]]
+139s Progressing step=3 ar=[gateway-58bbf67b5f-8-2 Successful [0] [0] [0]]
+156s Paused      step=4 ar=[gateway-58bbf67b5f-8-2 Successful [0] [0] [0]]
+175s Progressing step=5 ar=[gateway-58bbf67b5f-8-2 Successful [0] [0] [0]]
+187s Healthy     step=6 ar=[gateway-58bbf67b5f-8-2 Successful [0] [0] [0]]
```
The first measurement arrives at +98s, i.e. 60s after the analysis step began (`initialDelay`), then one every 20s. Three `[0]` → `Successful` → on to 50% → 100% → `Healthy` in 187s, with nobody touching it.
```
Name:            gateway
Status:          ✔ Healthy
Strategy:        Canary
  Step:          6/6
  SetWeight:     100
  ActualWeight:  100
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS         AGE    INFO
⟳ gateway                            Rollout      ✔ Healthy      69m
├──# revision:8
│  ├──⧉ gateway-58bbf67b5f           ReplicaSet   ✔ Healthy      3m6s   stable
│  │  ├──□ gateway-58bbf67b5f-9mx8l  Pod          ✔ Running      3m6s   ready:1/1
│  │  ├──□ gateway-58bbf67b5f-hhg8p  Pod          ✔ Running      49s    ready:1/1
│  │  ├──□ gateway-58bbf67b5f-q2rld  Pod          ✔ Running      49s    ready:1/1
│  │  ├──□ gateway-58bbf67b5f-4jcn8  Pod          ✔ Running      12s    ready:1/1
│  │  └──□ gateway-58bbf67b5f-lkzxx  Pod          ✔ Running      12s    ready:1/1
│  └──α gateway-58bbf67b5f-8-2       AnalysisRun  ✔ Successful   2m29s  ✔ 3
```

### B.5 — Bad version → auto-aborted

**Why not the lab's `EVENTS_URL=http://broken-on-purpose:8081`:** Task 1 Attempt A already showed what that does in this repo. Gateway `/health` checks `events`, so a canary with a broken `EVENTS_URL` is never Ready and gets restarted by its liveness probe. The Rollout then waits at step 0 for the canary to become available and never gets to the `analysis` step, so it would sit in `Progressing` instead of auto-aborting. (Prometheus *would* see 5xx — it scrapes pod IPs directly, and the kubelet's 503 `/health` probes get counted — but no AnalysisRun is ever created to look at them.)

**Instead:** the same "passes health checks, fails users" bug as Task 1 Attempt B, applied from a temporary copy (the committed manifest stays at good `v4`):
```diff
-              value: "5000"     # GATEWAY_TIMEOUT_MS
+              value: "1"
-              value: "v4"       # APP_VERSION
+              value: "v5-bad"
```
```
+1s   Progressing step=0 ar=[gateway-58bbf67b5f-8-2 Successful [0] [0] [0]]   ← previous run
+15s  Paused      step=1 ar=[gateway-58bbf67b5f-8-2 Successful [0] [0] [0]]
+35s  Progressing step=2 ar=[gateway-d9c4f69bb-9-2 Running ]
+95s  Progressing step=2 ar=[gateway-d9c4f69bb-9-2 Running [0.4642857142857143]]
+114s Degraded    step=0 ar=[gateway-d9c4f69bb-9-2 Failed [0.4642857142857143] [0.48936170212765956]]
```
Two failed measurements → `failed (2) > failureLimit (1)` → aborted at +114s without waiting for the third. The canary never went past 20%.

```
$ kubectl get analysisrun
NAME                     STATUS       AGE
gateway-58bbf67b5f-8-2   Successful   4m49s
gateway-d9c4f69bb-9-2    Failed       81s
```

`kubectl get analysisrun gateway-d9c4f69bb-9-2 -o yaml` (spec + status):
```yaml
spec:
  args:
  - name: canary-hash
    value: d9c4f69bb
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
  completedAt: "2026-10-04T16:30:18Z"
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
  - count: 2
    failed: 2
    measurements:
    - finishedAt: "2026-10-04T16:29:58Z"
      phase: Failed
      startedAt: "2026-10-04T16:29:58Z"
      value: '[0.4642857142857143]'
    - finishedAt: "2026-10-04T16:30:18Z"
      phase: Failed
      startedAt: "2026-10-04T16:30:18Z"
      value: '[0.48936170212765956]'
    metadata:
      ResolvedPrometheusQuery: |
        (
          sum(rate(gateway_requests_total{rs_hash="d9c4f69bb",status=~"5.."}[60s]))
          or on() vector(0)
        )
        /
        sum(rate(gateway_requests_total{rs_hash="d9c4f69bb"}[60s]))
    name: error-rate
    phase: Failed
  phase: Failed
  startedAt: "2026-10-04T16:28:58Z"
```

**The measured values are ~0.46–0.49, not the `[1]` the lab predicts.** Breaking down the canary's counters by path:
```
{'path': '/health', 'status': '200'} 97
{'path': '/events', 'status': '504'} 81
```
Every user-facing `/events` request on the canary failed (81/81). But the query divides by *all* requests, and about half the canary's requests are `/health`: loadgen's `/health` calls plus the kubelet's own readiness/liveness probes, which hit the pod directly. Those always return 200 here, so they roughly halve the measured ratio. It doesn't matter for this failure (0.46 ≫ 0.05), but it's a real weakness — see the answer below.

Final state after the automatic abort (settled ~15s later):
```
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 9: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/g-0-rg/quickticket-gateway:340a6ba4557ab7fd19d4fef12961e0ccbe8fc327 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS        AGE    INFO
⟳ gateway                            Rollout      ✖ Degraded    72m
├──# revision:9
│  ├──⧉ gateway-d9c4f69bb            ReplicaSet   • ScaledDown  2m19s  canary
│  └──α gateway-d9c4f69bb-9-2        AnalysisRun  ✖ Failed      105s   ✖ 2
├──# revision:8
│  ├──⧉ gateway-58bbf67b5f           ReplicaSet   ✔ Healthy     5m50s  stable
│  │  ├──□ gateway-58bbf67b5f-9mx8l  Pod          ✔ Running     5m50s  ready:1/1
│  │  ├──□ gateway-58bbf67b5f-hhg8p  Pod          ✔ Running     3m33s  ready:1/1
│  │  ├──□ gateway-58bbf67b5f-q2rld  Pod          ✔ Running     3m33s  ready:1/1
│  │  ├──□ gateway-58bbf67b5f-lkzxx  Pod          ✔ Running     2m56s  ready:1/1
│  │  └──□ gateway-58bbf67b5f-gvw6s  Pod          ✔ Running     25s    ready:1/1
│  └──α gateway-58bbf67b5f-8-2       AnalysisRun  ✔ Successful  5m13s  ✔ 3
```
Stable `v4` (revision 8) was never touched; the bad canary was scaled to 0. Restored by re-applying the good `k8s/gateway.yaml`, which brought it back to `✔ Healthy` with no new pods. Loadgen deleted afterwards.

**Cost of automating it:** with a manual abort (Task 1) the bad canary served errors for as long as a human took to notice. Here it served for ~80s (from reaching 20% at +15s to the abort at +114s). That time is set entirely by `initialDelay` + 2 × `interval` and could be cut by shortening them, at the price of a noisier `rate()` window. Nobody had to be watching.

### Answer

**What metric would you add beyond error rate for a more complete canary analysis?**

**Latency — p99 of the canary vs. p99 of stable**, from the `gateway_request_duration_seconds` histogram the gateway already exports:
```promql
histogram_quantile(0.99, sum by (le) (rate(gateway_request_duration_seconds_bucket{rs_hash="{{args.canary-hash}}", path!="/health"}[60s])))
/
histogram_quantile(0.99, sum by (le) (rate(gateway_request_duration_seconds_bucket{rs_hash="{{args.stable-hash}}", path!="/health"}[60s])))
```
with `successCondition: result[0] < 1.5` and `stable-hash` from `podTemplateHashValue: Stable`. Why:
- **Error rate is blind to "slow but successful".** A version with a missing index or an extra synchronous call returns 200 at 3× the latency and sails through the current template. Latency is the other half of QuickTicket's SLO from Lab 3, and in real incidents it usually degrades before errors appear: timeouts are a lagging symptom of slowness.
- **Comparing to stable, not a fixed number**, cancels shared noise. If `events` or Postgres gets slow, both ReplicaSets slow down together and the ratio stays ~1, so the canary isn't blamed for a dependency problem.

Two fixes to the existing error-rate metric, which this run showed are needed:
- **Exclude `/health` (and `/metrics`) with `path!="/health"`** in both numerator and denominator. The kubelet probes and loadgen health checks halved the measured error rate (0.46 instead of 1.0 for a canary failing 100% of real traffic). A bug that breaks 8% of real requests would be measured at ~4% and pass the 5% threshold.
- **Also run analysis at the 50% step.** At 20% with ~8 rps the canary sees ~100 real requests per minute — enough to catch gross failures like this one, not enough to tell 0.5% from 0%. A background analysis (`strategy.canary.analysis`) that runs for the whole rollout would keep checking as sample sizes grow.

Lower priority: **container restarts / readiness flaps** for the canary pods (`kube_pod_container_status_restarts_total`, needs kube-state-metrics). That catches crash-loops and memory leaks that kill the pod before it serves enough requests to move the error rate.

---

### Notes / follow-ups

- **ArgoCD auto-sync is off** (see Pre-flight). Re-enable after merging this PR: `kubectl patch application quickticket -n argocd --type merge -p '{"spec":{"syncPolicy":{"automated":{}}}}'`.
- **Postgres has no persistent volume** — the schema/seed disappears on every pod restart (hit in 7.4). Lab 9 territory, but worth knowing that any Postgres restart silently breaks `/events`.
- **In-cluster Prometheus runs `quay.io/prometheus/prometheus:v3.11.2`** (live patch, see B.1) because Docker Hub was unreachable. `labs/lab7/prometheus.yaml` still says `prom/prometheus`, so re-applying it would bring back the Docker Hub pull.
- **`imagePullSecrets: ghcr-secret` refers to a secret that doesn't exist** in `default` (`FailedToRetrieveImagePullSecret` warning in Attempt A). Pulls work because the package is public; either create the secret or drop the reference.
