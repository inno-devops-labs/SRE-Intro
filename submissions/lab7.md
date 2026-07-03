# Lab 7 — Progressive Delivery: Canary Deployments — Submission

**Student:** jakefish18
**Repo:** https://github.com/jakefish18/SRE-Intro
**Branch:** `feature/lab7`

PR checklist:
```text
- [x] Task 1 done — Argo Rollouts installed, canary deployed, promoted + aborted
- [x] Task 2 done — multi-step canary with observation
- [x] Bonus Task done — automated canary analysis with Prometheus
```

> **Environment notes:**
> - Work is on the k3d `quickticket` cluster from Labs 4–5. `k8s/gateway.yaml` is
>   converted from a Deployment to an Argo Rollouts **Rollout** (canary strategy).
> - ArgoCD's auto-sync on the `quickticket` app was **paused** for this lab
>   (`spec.syncPolicy.automated=null`) so manual `promote`/`abort` operations
>   aren't reverted by GitOps. This is the correct operational choice — you don't
>   want a GitOps controller fighting a live manual canary.
> - New "versions" are triggered by bumping an `APP_VERSION` env var (the lab's
>   suggested approach in 7.3) rather than rebuilding images — Argo Rollouts starts
>   a canary on **any** pod-template change, so this is sufficient and avoids
>   image-pull churn. The image stays the public ghcr.io gateway image from Lab 5.

---

## Task 1 — Manual Canary Deployment

### 1. `kubectl argo rollouts version`
```
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:11:48Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GoVersion: go1.24.13
  Platform: darwin/arm64
```
Controller: `deployment/argo-rollouts` in ns `argo-rollouts` → `1/1 Available`;
CRD `rollouts.argoproj.io` installed.

### 2. `kubectl argo rollouts get rollout gateway` — Paused at 20% (canary)

Strategy used for Task 1:
```yaml
strategy:
  canary:
    steps:
      - setWeight: 20
      - pause: {}               # infinite pause — waits for manual `promote`
      - setWeight: 60
      - pause: {duration: 30s}
      - setWeight: 100
```
```
Name:            gateway
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Replicas:  Desired: 5  Current: 5  Updated: 1  Ready: 5  Available: 5

⟳ gateway                            Rollout     ॥ Paused   2m11s
├──# revision:2
│  └──⧉ gateway-7bf8666797           ReplicaSet  ✔ Healthy  62s    canary
│     └──□ gateway-7bf8666797-stcnc  Pod         ✔ Running  60s    ready:1/1
└──# revision:1
   └──⧉ gateway-59ccf78f84           ReplicaSet  ✔ Healthy  2m11s  stable
      ├──□ gateway-59ccf78f84-6wzd2  Pod         ✔ Running  ready:1/1
      ├──□ gateway-59ccf78f84-hr8w6  Pod         ✔ Running  ready:1/1
      ├──□ gateway-59ccf78f84-qlzwm  Pod         ✔ Running  ready:1/1
      └──□ gateway-59ccf78f84-qwm6w  Pod         ✔ Running  ready:1/1
```
1 canary pod (rev 2) + 4 stable pods (rev 1) = the `setWeight: 20` split.

**Traffic split verification** (in-cluster loadgen through `kube-proxy`, per-pod
`GET /events` counts over an equal 30s window — *not* `port-forward`, which pins one endpoint):
```
canary hash=7bf8666797  stable hash=59ccf78f84
gateway-59ccf78f84-6wzd2 role=stable  events=39
gateway-59ccf78f84-hr8w6 role=stable  events=17
gateway-59ccf78f84-qlzwm role=stable  events=19
gateway-59ccf78f84-qwm6w role=stable  events=13
gateway-7bf8666797-stcnc role=CANARY  events=27
canary share = 27/115 = 23.5%   (target 20%)
```

### 3. After `promote` — progression to 100%

`kubectl argo rollouts promote gateway` → advances past the infinite pause to
`setWeight: 60`, then auto-proceeds through the 30s pause to `setWeight: 100`:
```
# mid-progression (step 2/5, SetWeight 60 — 3 canary pods):
Status: ◌ Progressing   Step: 2/5   SetWeight: 60   ActualWeight: 75

# final:
Status:          ✔ Healthy
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Replicas:  Desired: 5  Current: 5  Updated: 5  Ready: 5  Available: 5
# revision:2 (gateway-7bf8666797) is now stable, all 5 pods on the new version.
```

### 4. After `abort` — instant rollback

Deployed a "bad" version (`APP_VERSION=v3-bad`, revision 3) → canary paused at 20%
(1 bad pod). Then `kubectl argo rollouts abort gateway`:
```
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
  Step:          0/5   SetWeight: 0   ActualWeight: 0
Replicas:  Desired: 5  Current: 5  Updated: 0  Ready: 5  Available: 5

├──# revision:3
│  └──⧉ gateway-568d9b85cb   ReplicaSet  • ScaledDown  canary   ← bad version killed
├──# revision:2
│  └──⧉ gateway-7bf8666797   ReplicaSet  ✔ Healthy     stable   ← still serving, 5/5 ready
```
The canary ReplicaSet is scaled to 0; the stable pods were **never touched**
(`Ready: 5`, `Available: 5` throughout) → zero-downtime instant rollback.

### 5. How long from `abort` to all-stable? Compare with Lab 5 `git revert`.

**Abort → all traffic on stable: a few seconds** (effectively instant). During a
canary the stable ReplicaSet is *already running at full capacity*; `abort` just
scales the canary ReplicaSet to 0. There is **nothing to bring back** — no image
pull, no pod scheduling, no waiting for readiness. On the next controller reconcile
(~1–2s) the canary pod is terminated and 100% of traffic is on the stable pods that
never stopped serving. Observed `Available: 5` the entire time (no dip).

**Lab 5 `git revert` rollback: minutes.** That path is a full GitOps redeploy
cycle: edit manifest → `git commit` → `git push` → ArgoCD detects (poll interval up
to ~3 min, or a manual `argocd app sync`) → applies the reverted manifest → pulls
the image → schedules new pods → waits readiness → terminates old pods.

| | Argo Rollouts `abort` | Lab 5 `git revert` |
|---|---|---|
| Mechanism | local controller scales canary→0; stable already up | commit → sync → pull → schedule → reschedule |
| Time to recover | ~seconds | minutes (dominated by sync + image pull + scheduling) |
| Blast radius during rollback | canary only (≤20% of users) | whole deployment churns |

**Takeaway:** canary abort is a *runtime traffic decision* on already-healthy pods;
git revert is a *redeploy*. Abort is dramatically faster because the good version
never left.

---

## Task 2 — Multi-Step Canary with Observation

### Multi-step strategy (applied to `k8s/gateway.yaml`)
```yaml
strategy:
  canary:
    steps:
      - setWeight: 20
      - pause: {duration: 60s}   # observe 1 min at 20%
      - setWeight: 40
      - pause: {duration: 60s}   # observe 1 min at 40%
      - setWeight: 60
      - pause: {duration: 60s}   # observe 1 min at 60%
      - setWeight: 80
      - pause: {duration: 30s}
      - setWeight: 100
```

### `--watch`-style observation (all 5 weight steps)

Captured by polling `kubectl argo rollouts get rollout gateway` (weight is reflected
by `updatedPods/5` — this basic canary splits by replica count, not a traffic mesh):
```
11:18:25Z Paused       step=1/9  updatedPods=1/5   (setWeight 20 = 20%)
11:19:25Z Progressing  step=2/9  updatedPods=2/5   → scaling to 40%
11:19:38Z Paused       step=3/9  updatedPods=2/5   (setWeight 40 = 40%)
11:20:38Z Progressing  step=4/9  updatedPods=3/5   → scaling to 60%
11:20:50Z Paused       step=5/9  updatedPods=3/5   (setWeight 60 = 60%)
11:21:51Z Progressing  step=6/9  updatedPods=4/5   → scaling to 80%
11:22:03Z Paused       step=7/9  updatedPods=4/5   (setWeight 80 = 80%)
11:22:28Z Progressing  step=8/9  updatedPods=5/5   → scaling to 100%
11:22:40Z Healthy      step=9/9  updatedPods=5/5   (setWeight 100 = full)
```
The updated-replica count climbs **1 → 2 → 3 → 4 → 5** exactly as the weight steps
20 → 40 → 60 → 80 → 100.

### Dashboard observation
> The docker-compose Prometheus/Grafana from Lab 3 **cannot scrape pods inside
> k3d** (pod IPs live in a bridge network the host can't reach — the lab notes
> this in 7.9). Real-time step/weight/replica observation was therefore done via
> `kubectl argo rollouts get rollout gateway --watch` (above) and, for the Bonus,
> an **in-cluster** Prometheus (`labs/lab7/prometheus.yaml`). Observed: the
> updated-replica count climbs 1 → 2 → 3 → 4 → 5 as the weight steps 20→40→60→80→100,
> and total request rate stays steady across steps (stable+canary always sum to 5
> serving pods, so there is no capacity dip during the rollout).

### At what canary % would you want an automated abort? Why?

**As early as the first step (20%), gated on a short-but-sufficient analysis
window.** The entire value of a canary is *bounding blast radius*: catching a bad
version while only ~20% (1 of 5 pods) of users are affected is far better than
discovering it at 60%. So I want the automated analysis to run at the **first**
weight step, with a window long enough to collect statistically meaningful data
(enough requests to trust the error rate — ~1–2 min), and abort immediately if the
canary's error rate exceeds threshold. Too early/too short = noisy false aborts;
too late (e.g. only at 80%) = you've already exposed most users. 20% with a ~1 min
analysis window is the sweet spot — which is exactly what the Bonus AnalysisTemplate
implements.

---

## Bonus Task — Automated Canary Analysis

**In-cluster Prometheus** (`labs/lab7/prometheus.yaml`) discovers all 5 gateway pods
with the `rs_hash` label (relabeled from `rollouts-pod-template-hash`), so the query
can separate canary from stable:
```
active targets: 5
  gateway-55f8d4f9dc-rhl5p  rs_hash=55f8d4f9dc  up    ← canary RS
  gateway-5b78b4d484-gqvkp  rs_hash=5b78b4d484  up    ← stable RS
  gateway-5b78b4d484-xggzf  rs_hash=5b78b4d484  up
  ... (5 total, all up)
```

### `kubectl get analysistemplate gateway-error-rate`
```
NAME                 AGE
gateway-error-rate   24m
# metric: error-rate  interval=20s  count=3  successCondition="result[0] < 0.05"  failureLimit=1
```

The AnalysisTemplate (`k8s/analysis-template.yaml`) queries the in-cluster
Prometheus, scoping to the canary via the `rs_hash` label
(`rollouts-pod-template-hash` relabeled in `labs/lab7/prometheus.yaml`):
```promql
( sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}",status=~"5.."}[60s])) or on() vector(0) )
/ sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}"}[60s]))
```
`successCondition: result[0] < 0.05`, `count: 3`, `interval: 20s`, `initialDelay: 60s`, `failureLimit: 1`.

Strategy with the analysis step wired in:
```yaml
strategy:
  canary:
    steps:
      - setWeight: 20
      - pause: {duration: 20s}
      - analysis:
          templates: [{ templateName: gateway-error-rate }]
          args:
            - name: canary-hash
              valueFrom: { podTemplateHashValue: Latest }
      - setWeight: 50
      - pause: {duration: 20s}
      - setWeight: 100
```

### Good version → auto-promote  &  Bad version → auto-abort

Analysis strategy used: `setWeight 20 → pause 20s → analysis → setWeight 50 → pause 20s → setWeight 100`.

```
$ kubectl get analysisrun -n default
NAME                     STATUS       AGE
gateway-6b6457b8d-4-2    Failed       12m     ← bad canary (v7)
gateway-784f5b8dcc-3-2   Successful   17m     ← good canary (v6)
```

**Good canary (v6) — auto-promoted.** The AnalysisRun measured the canary's 5xx
ratio 3× and all were `[0]`; it passed and the rollout promoted itself to 100% with
no human action:
```
11:25:01  Progressing step=2/6  analysisrun: gateway-784f5b8dcc-3-2  Running     6s
11:26:39  Progressing step=3/6  analysisrun: gateway-784f5b8dcc-3-2  Successful  104s
11:27:28  Healthy      step=6/6  analysisrun: gateway-784f5b8dcc-3-2  Successful
# measurements: value=[0]  value=[0]  value=[0]   (all Successful)
```

**Bad canary (v7) — auto-aborted.** I made the canary time out on `/events`
(`GATEWAY_TIMEOUT_MS=1`) while keeping `/health` green (it uses its own 2s timeout),
i.e. a canary that *passes health checks but errors on real traffic* — the exact
case a health check alone would miss. The AnalysisRun measured a ~40% 5xx ratio,
failed twice (> `failureLimit: 1`) and the rollout auto-aborted:
```
11:30:00  Progressing step=2/6  analysisrun: gateway-6b6457b8d-4-2  Running  8s
11:31:13  Degraded     step=0/6  analysisrun: gateway-6b6457b8d-4-2  Failed   81s

# failed-run measurements (5xx ratio of the canary; threshold is 0.05):
overall=Failed   metric=error-rate  failed=2
  value=[0.3813559322033898]  phase=Failed
  value=[0.41538461538461546]  phase=Failed
```
> Note: the lab's suggested `EVENTS_URL=broken` yields a *higher* ratio (~1.0) but
> also makes `/health` return 503, so the canary fails readiness and is pulled from
> the Service before it can be measured (the template's strict-denominator "no
> traffic = fail-safe abort" path). The `GATEWAY_TIMEOUT_MS=1` variant keeps the
> canary Ready and receiving traffic, so we get real measured values (~0.4 ≫ 0.05)
> — a cleaner, more instructive failure.

Final rollout after the bad deploy — **Degraded, stable untouched**:
```
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 4: Step-based
                 analysis phase error/failed: Metric "error-rate" assessed Failed
                 due to failed (2) > failureLimit (1)
  Step: 0/6  SetWeight: 0
Replicas:  Desired: 5  Current: 5  Updated: 0  Ready: 5  Available: 5
# canary ReplicaSet scaled to 0; stable's 5 pods never dropped below Ready: 5.
```

### What metric would you add beyond error rate for a more complete canary analysis?

**p95/p99 request latency.** A canary can serve `0%` errors yet be 3× slower — "slow
is the new down." The gateway already exposes `gateway_request_duration_seconds`
(a histogram), so a second analysis metric like
`histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket{rs_hash="<canary>"}[60s])) by (le))`
with a latency budget would catch performance regressions that error-rate misses.
Runner-ups worth adding: **saturation** (canary CPU/memory vs stable — catches leaks
under real traffic), **request rate** (a sudden drop = canary not actually receiving
or serving traffic), and a **business metric** (successful-checkout rate) so you
measure user outcomes, not just HTTP status.
