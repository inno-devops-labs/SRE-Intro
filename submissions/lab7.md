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

## Bonus Task — Automated Canary Analysis (2 pts)

### B.1 — In-cluster Prometheus

```bash
$ kubectl apply -f labs/lab7/prometheus.yaml
$ kubectl -n monitoring rollout status deployment/prometheus --timeout=60s
deployment "prometheus" successfully rolled out
```

Target verification — all 5 gateway pods discovered, each carrying the `rs_hash` label copied from
`rollouts-pod-template-hash` (the mechanism that lets the AnalysisTemplate scope queries to canary replicas only):

```bash
$ kubectl port-forward -n monitoring svc/prometheus 9091:9090 &
$ curl -s 'http://localhost:9091/api/v1/targets?state=active' | python3 -c "
    import sys,json
    for t in json.load(sys.stdin)['data']['activeTargets']:
        print(t['labels'].get('pod'), 'rs=', t['labels'].get('rs_hash'), t['health'])"
gateway-79bf656f44-4mhw7 rs= 79bf656f44 up
gateway-79bf656f44-c5r4m rs= 79bf656f44 up
gateway-79bf656f44-wg9dg rs= 79bf656f44 up
gateway-79bf656f44-fd5k9 rs= 79bf656f44 up
gateway-79bf656f44-kl9xs rs= 79bf656f44 up
```

### B.2 — AnalysisTemplate

Applied `labs/lab7/analysis-template.yaml` (also committed as `k8s/analysis-template.yaml`):

```bash
$ kubectl apply -f labs/lab7/analysis-template.yaml
$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   0s
```

Query: canary 5xx ratio over 60 s, `initialDelay: 60s` (Prometheus discovery + scrape warm-up), `interval: 20s`,
`count: 3`, `successCondition: result[0] < 0.05`, `failureLimit: 1`, numerator guarded with `or on() vector(0)`
(zero errors is a real answer), denominator strict (no traffic = can't measure = fail-safe abort),
`{{args.canary-hash}}` scopes the series to canary replicas.

### B.3 — Analysis wired into the strategy

`k8s/gateway.yaml` (committed version):

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

### B.4 — Good version auto-promotes

Loadgen running, `APP_VERSION: v5` applied. **First attempt aborted automatically** — and it was right: the
postgres `events` table had gone missing from the cluster (`psycopg2.errors.UndefinedTable: relation "events"
does not exist`), so every pod was returning 502s on `/events`. The analysis measured the canary's real error
rate and refused to promote:

```
gateway-65f669b67f-5-2  Failed  Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  2026-10-05T06:45:16Z Failed [0.42148760330578505]
  2026-10-05T06:45:36Z Failed [0.4173913043478261]
```

Re-seeded the database (`kubectl cp app/seed.sql … && psql -f /tmp/seed.sql` → `CREATE TABLE`, `INSERT 0 5`)
and retried. Second attempt — no human intervention, the analysis cleared and the rollout drove itself to 100%:

```
06:54:17  Paused       step=1/6  setWeight=20   actual=20   updated=1
06:54:38  Progressing  step=2/6  setWeight=20   actual=20   updated=1   analysisrun gateway-65f669b67f-5-2.1 Running
06:55:41  Progressing  step=2/6  setWeight=20   actual=20   updated=1   analysisrun Running (60 s initial delay)
06:56:12  Progressing  step=3/6  setWeight=50   actual=25   updated=3   analysisrun Successful   ← auto-promote 20→50
06:56:23  Paused       step=4/6  setWeight=50   actual=50   updated=3   (20 s pause)
06:56:44  Progressing  step=5/6  setWeight=100  actual=100  updated=5
06:56:54  Healthy      step=6/6  setWeight=100  actual=100  updated=5
```

The successful AnalysisRun — 3 measurements, all `[0]`:

```
$ kubectl get analysisrun gateway-65f669b67f-5-2.1 -o jsonpath='{.status.metricResults[0].measurements}'
phase: Successful
2026-10-05T06:55:30Z Successful [0]
2026-10-05T06:55:50Z Successful [0]
2026-10-05T06:56:10Z Successful [0]
```

### B.5 — Bad version auto-aborts

**Adaptation from the lab text:** the suggested `EVENTS_URL: http://broken-on-purpose:8081` (unresolvable name)
does not work with this gateway build, because `/health` gates on the events downstream (`app/gateway/main.py:237`
returns 503 when events is "down") — the liveness probe kills the canary pod in ~30 s (CrashLoopBackOff) before it
ever becomes ready, so no traffic, no metrics, no analysis. Instead I pointed the canary at a service whose
`/health` passes but whose data path fails: `EVENTS_URL: http://payments:8082`. `GET /health` → 200 (pod stays
ready and takes traffic), `GET /events` → 404 from payments → gateway maps it to **502**
(`app/gateway/main.py:259-261`), i.e. real 5xx on the canary's `/events`.

```
07:08:02  Progressing  step=0/6  setWeight=20  actual=0   updated=1   canary starting
07:08:12  Paused       step=1/6  setWeight=20  actual=20  updated=1
07:08:34  Progressing  step=2/6  setWeight=20  actual=20  updated=1   analysisrun gateway-5898845c97-7-2 Running
07:09:58  Degraded     step=0/6  setWeight=0   actual=0   updated=0   RolloutAborted   ← AUTO-ABORT, no human
```

~96 s from canary start to abort (20 s pause + 60 s initial delay + two 20 s measurements). The failed
AnalysisRun — 5xx ratio ≈ 45% on the canary (not 1.0 because the loadgen's `/health` calls also count as
denominator traffic and succeed; a `/events`-only client would show ~1.0), far above the 5% threshold:

```
$ kubectl get analysisrun gateway-5898845c97-7-2 -o jsonpath='{.status.metricResults[0].measurements}'
2026-10-05T07:09:30Z Failed [0.4491525423728813]
2026-10-05T07:09:50Z Failed [0.4552845528455285]
message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
```

All analysis runs across the bonus exercise:

```bash
$ kubectl get analysisrun
NAME                       STATUS       AGE
gateway-5898845c97-7-2     Failed       5m2s
gateway-65f669b67f-5-2     Failed       29m
gateway-65f669b67f-5-2.1   Successful   19m
```

### B.6 — Cleanup + recovery

Reverted `EVENTS_URL` to `http://events:8081` (and `APP_VERSION` back to the good `v5`), applied, then
`kubectl argo rollouts retry rollout gateway`. Deleted the loadgen. Final state — Degraded cleared, all 5 stable
pods serving:

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
```

### What metric would you add beyond error rate for a more complete canary analysis?

**Request latency (p95/p99), compared canary-vs-stable.** Error rate catches crashes and hard failures, but a
regression that makes the canary *slow* — a missing index, a leaked connection, extra serialization, a GIL
contention — serves 200s the whole time and sails through a 5% error threshold while user experience degrades.
The gateway already exposes a request-duration histogram, so an `analysis` metric like
`histogram_quantile(0.99, sum by (le) (rate(gateway_request_duration_seconds_bucket{rs_hash="{{args.canary-hash}}"}[5m])))`
aborted when the canary's p99 exceeds, say, 1.5× the stable's p99 (computed the same way with the stable hash)
would catch that class of defect. A relative canary-vs-stable comparison is important in absolute terms too: a
latency threshold that makes sense for the canary makes sense for the stable, so the analysis adapts to normal
system load instead of a hardcoded number that either cries wolf under peak load or sleeps through a real
regression at quiet hours.

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
| Bonus: in-cluster Prometheus | `monitoring` ns, 5 gateway targets with `rs_hash`, all `up` |
| Bonus: good canary | AnalysisRun `Successful` (3× `[0]`) → auto-promoted 20→50→100, no human intervention |
| Bonus: bad canary | 502s on `/events` → AnalysisRun `Failed` (`[0.449]`, `[0.455]`) → auto-abort in ~96 s, stable untouched |
| Bonus: real incident caught | Missing postgres `events` table detected by the analysis on the first v5 canary; re-seeded via `app/seed.sql` |
