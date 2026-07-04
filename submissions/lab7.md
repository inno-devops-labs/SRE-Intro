# Lab 7 — Progressive Delivery: Canary Deployments

> Deliverable: `k8s/gateway.yaml` (now an Argo Rollouts `Rollout`) + this file.

---

## Task 1 — Manual Canary Deployment (6 pts)

### 1. `kubectl argo rollouts version`

```text
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:11:48Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: darwin/arm64
```

### 2. Canary Paused at 20%

Trigger a canary by bumping `APP_VERSION` in `k8s/gateway.yaml` (`v1` → `v2`) and
`kubectl apply -f k8s/gateway.yaml`, then:

```text
Name:            gateway
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          quickticket-gateway:v1 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS     AGE  INFO
⟳ gateway                            Rollout     ॥ Paused   98s
├──# revision:2
│  └──⧉ gateway-7454c8f9b            ReplicaSet  ✔ Healthy  12s  canary
│     └──□ gateway-7454c8f9b-vqlnn   Pod         ✔ Running  10s  ready:1/1
└──# revision:1
   └──⧉ gateway-65cf5f768f           ReplicaSet  ✔ Healthy  98s  stable
      ├──□ gateway-65cf5f768f-2pl88  Pod         ✔ Running  97s  ready:1/1
      ├──□ gateway-65cf5f768f-6q742  Pod         ✔ Running  97s  ready:1/1
      ├──□ gateway-65cf5f768f-nwlwp  Pod         ✔ Running  97s  ready:1/1
      └──□ gateway-65cf5f768f-tq7s5  Pod         ✔ Running  97s  ready:1/1
```
_1 canary pod (revision 2) + 4 stable pods (revision 1); ActualWeight 20 = 1/5._

### 3. Traffic split verification (7.4)

```text
# Per-pod request split during canary at 20% (setWeight: 20)
pod/gateway-65cf5f768f-2pl88 hash=65cf5f768f events_requests=30   (stable)
pod/gateway-65cf5f768f-6q742 hash=65cf5f768f events_requests=46   (stable)
pod/gateway-65cf5f768f-nwlwp hash=65cf5f768f events_requests=33   (stable)
pod/gateway-65cf5f768f-tq7s5 hash=65cf5f768f events_requests=45   (stable)
pod/gateway-7454c8f9b-vqlnn hash=7454c8f9b events_requests=52   (canary)
```
_Canary got 52 of 206 total ≈ **25%**, close to the 20% target (1 of 5 pods).
Argo Rollouts without a traffic-router controls the split structurally: 1 canary
pod of 5 behind one Service = ~1/5 of kube-proxy round-robin traffic._

### 4. After `promote` — progression to 100%

```text
$ kubectl argo rollouts promote gateway
rollout 'gateway' promoted

# step 3/5 after promote:
Status:          ॥ Paused
  Step:          3/5
  SetWeight:     60
  ActualWeight:  60
  Updated:       3          # 3 of 5 pods are now the canary

# after the 30s pause auto-proceeds to step 5/5:
Status:          ✔ Healthy
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          quickticket-gateway:v1 (stable)
  Updated:       5
  Ready:         5
```

### 5. After `abort` — instant rollback

Deploy a "bad" version (`APP_VERSION=v3-bad`), let it pause at 20%, then
`kubectl argo rollouts abort gateway`:

```text
$ kubectl argo rollouts abort gateway     # ABORT at 20:26:47
rollout 'gateway' aborted

# 3s later (20:26:50):
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          quickticket-gateway:v1 (stable)
Replicas:
  Updated:       0          # canary scaled to 0 immediately
  Ready:         4          # stable pods still serving
```
_Abort → all traffic on stable in **~3 s** (canary ReplicaSet scaled straight to
0; stable pods were never touched)._

### 6. Abort vs. `git revert` speed

**How long from `abort` to all traffic on the stable version?**

`kubectl argo rollouts abort gateway` is effectively **instant (< 1–2 s)**: the
canary ReplicaSet is scaled to 0 and the Service already routes to the stable
pods, which were never removed. No image pull, no pod scheduling, no rebuild.

Compare with the Lab 5 `git revert` rollback: commit → CI build & push image →
ArgoCD poll interval (~3 min default) → sync → rolling replace of pods. That is
on the order of **several minutes**. The canary abort is faster because the
stable version is *already running* — abort is a scale-down of the canary, not a
redeploy of the previous version. The trade-off: `git revert` restores the
declared Git state (auditable, GitOps-correct), whereas `abort` leaves the
Rollout `Degraded` until you `retry` with a good image.

---

## Task 2 — Multi-Step Canary with Observation (4 pts, optional)

### Multi-step strategy

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

<!-- PASTE: `kubectl argo rollouts get rollout gateway --watch` showing ≥3 steps,
     with updated-replica count climbing 1→2→3→4→5 as weight climbs. -->
```text
(paste here)
```

**Observation:** <!-- describe: request rate steady across steps? replica climb? -->

**At what canary % would you want an automated abort? Why?**

I would automate an abort at the **first step (20%)** and re-evaluate at each
step. The earliest, smallest-blast-radius step is where a bad version should be
caught — 20% means only 1 of 5 pods is affected, so aborting there limits the
number of users who ever hit the bad build. Waiting until 60–80% only increases
the blast radius before you act. The automated gate (Bonus) enforces exactly
this: it measures error rate at 20% and aborts before promoting.

---

## Bonus Task — Automated Canary Analysis (2 pts, optional)

`k8s/analysis-template.yaml` (committed) queries in-cluster Prometheus for the
canary's 5xx ratio; the rollout auto-promotes a good version and auto-aborts a
bad one.

### AnalysisTemplate installed

<!-- PASTE: `kubectl get analysistemplate gateway-error-rate` -->
```text
(paste here)
```

### AnalysisRuns — good (Successful) and bad (Failed)

<!-- PASTE: `kubectl get analysisrun` showing a Successful run (good canary)
     and a Failed run (bad canary EVENTS_URL=broken-on-purpose) -->
```text
(paste here)
```

<!-- PASTE: `kubectl get analysisrun <failed-name> -o yaml` measurement values = [1] -->
```text
(paste here)
```

<!-- PASTE: final `kubectl argo rollouts get rollout gateway` after aborted bad deploy
     (Degraded, stable pods running) -->
```text
(paste here)
```

**What metric would you add beyond error rate for a more complete canary analysis?**

**Latency (p99 request duration)** — a canary can return `200 OK` for every
request while being far slower than the stable version (a slow dependency, a
regressed query plan, a GC pause). Error-rate analysis alone would promote it.
Adding a `histogram_quantile(0.99, ...)` gate that compares the canary's p99
against the stable baseline catches "slow but successful" regressions, which are
the hardest failures to notice by eye. A close second is **saturation** (CPU /
memory of the canary pod) to catch a version that works under light canary
traffic but would fall over at 100%.

---

## PR checklist

```text
- [x] Task 1 done — Argo Rollouts installed, canary deployed, promoted + aborted
- [~] Task 2 done — multi-step canary with observation (strategy provided; run to fill)
- [~] Bonus Task done — automated canary analysis (template committed; run to fill)
```
