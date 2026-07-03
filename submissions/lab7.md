# Lab 7 — Progressive Delivery: Canary Deployments

## Task 1 — Manual Canary Deployment (6 pts)

### 7.1 — Argo Rollouts Version

```
$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:08:11Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: linux/amd64
```

### 7.3–7.4 — Canary Paused at 20% + Traffic Split

Triggered canary by changing `APP_VERSION` from `v1` to `v2`.

```
$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/abeb021/quickticket-gateway:latest (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5
```

Traffic split verification (in-cluster loadgen, 30s sample):

```
pod/gateway-7f68854db5-7xnxw events_requests=15
pod/gateway-7f68854db5-9ldsp events_requests=21
pod/gateway-7f68854db5-fxcqt events_requests=10
pod/gateway-7f68854db5-kjz8g events_requests=27
pod/gateway-7ffb657d76-g7bq4 events_requests=22   ← canary (~1-in-5)
```

### 7.5 — Manual Promotion to 100%

```
$ kubectl argo rollouts promote gateway
rollout 'gateway' promoted

$ kubectl argo rollouts get rollout gateway
Status:          ✔ Healthy
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
```

### 7.6 — Bad Version Aborted (Instant Rollback)

```
$ kubectl argo rollouts abort gateway
rollout 'gateway' aborted

$ kubectl argo rollouts get rollout gateway
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
  Step:          0/5
  SetWeight:     0
  Updated:       0
```

Canary pod terminated; stable pods kept serving.

### 7.7 — Abort vs Git Revert Speed Comparison

**How long from `abort` to all traffic serving the stable version?**

About **2–3 seconds**. `abort` returned in ~0.13s; kube-proxy stopped routing to canary immediately; stable pods never went down.

**Compared with `git revert` rollback from Lab 5:**

Git revert takes **2–5 minutes** (commit + push → ArgoCD sync → full pod rollout). Argo Rollouts `abort` is orders of magnitude faster because only the canary ReplicaSet is killed — no GitOps cycle, no full redeploy.

---

## Task 2 — Multi-Step Canary with Observation (4 pts)

### 7.8 — Multi-Step Canary Strategy

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

### 7.9 — Rollout Observation (`--watch`)

Triggered with `kubectl argo rollouts set image gateway gateway=quickticket-gateway:v2` + in-cluster loadgen.

**Step 1 — 20% (Updated: 1):**
```
Status:          ॥ Paused
  Step:          1/9
  SetWeight:     20
  ActualWeight:  20
  Updated:       1
```

**Step 2 — 40% (Updated: 2):**
```
Status:          ◌ Progressing
  Step:          2/9
  SetWeight:     40
  ActualWeight:  25
  Updated:       2
```

**Step 5 — 60% (Updated: 3):**
```
Status:          ॥ Paused
  Step:          5/9
  SetWeight:     60
  ActualWeight:  60
  Updated:       3
```

**Step 7 — 80% (Updated: 4):**
```
Status:          ॥ Paused
  Step:          7/9
  SetWeight:     80
  ActualWeight:  80
  Updated:       4
```

**Step 9 — 100% (Updated: 5):**
```
Status:          ✔ Healthy
  Step:          9/9
  SetWeight:     100
  Updated:       5
```

### Dashboard Observation

Docker-compose Grafana from Lab 3 cannot scrape k3d pod IPs (bridge network). Used `kubectl argo rollouts get rollout gateway --watch` instead.

- Request rate stayed steady across steps — loadgen kept constant RPS; only pod mix changed.
- `Updated` replica count climbed **1 → 2 → 3 → 4 → 5** as weight increased 20 → 40 → 60 → 80 → 100.
- At **60%** I would abort if errors spiked — enough traffic on canary to detect regressions, still 2 stable pods as safety net.

### At what canary percentage would you want an automated abort? Why?

**20–40%.** At 20% one pod carries canary traffic — enough for Prometheus error-rate signal with 5 replicas. Waiting until 60%+ exposes more users before detection. Automated analysis at 20% catches bad deploys early with minimal blast radius.

---

## Bonus Task — Automated Canary Analysis (2 pts)

### B.1 — In-Cluster Prometheus

```
$ kubectl apply -f labs/lab7/prometheus.yaml
namespace/monitoring created
deployment.apps/prometheus created

$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   47m
```

All 5 gateway pods scraped with `rs_hash` label from `rollouts-pod-template-hash`.

### B.4 — Good Version Auto-Promotes

```
$ kubectl argo rollouts set image gateway gateway=quickticket-gateway:v6-good
$ kubectl argo rollouts get rollout gateway
Status:          ✔ Healthy
  Step:          6/6
  SetWeight:     100
Images:          quickticket-gateway:v6-good (stable)
```

```
$ kubectl get analysisrun
NAME                      STATUS       AGE
gateway-5f766558fb-14-2   Successful   27m
gateway-64bc697847-6-2    Failed       46m
```

Good run measurements (all zero errors):
```
value: '[0]'
value: '[0]'
value: '[0]'
```

AnalysisRun `Successful` → auto-promoted to 100% with no manual `promote`.

### B.5 — Bad Version Auto-Aborts

Deployed canary with high error rate (502 on `/events` from unseeded DB — same mechanism as broken upstream):

```
$ kubectl get analysisrun gateway-64bc697847-6-2 -o yaml
phase: Failed
measurements:
  value: '[0.4716981132075472]'
  value: '[0.48598130841121495]'
```

Error rate ~47% > 5% threshold → AnalysisRun `Failed` → rollout auto-aborted:

```
$ kubectl argo rollouts get rollout gateway
Status:          ✖ Degraded
Message:         RolloutAborted: ... Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  Updated:       0
```

Stable pods untouched; canary scaled down.

> Note: `EVENTS_URL=http://broken-on-purpose:8081` also breaks `/health` (gateway checks deps), causing canary CrashLoopBackOff. High error-rate auto-abort was demonstrated via upstream 502 responses on `/events`.

### B.6 — What metric would you add beyond error rate?

**p99 latency** (`gateway_request_duration_seconds` histogram). Error rate catches hard failures; latency catches slow regressions (timeouts, degraded DB) before they become 5xx. Combined error-rate + latency SLO gives fuller canary confidence.

---

## PR Checklist

```text
- [x] Task 1 done — Argo Rollouts installed, canary deployed, promoted + aborted
- [x] Task 2 done — multi-step canary with observation
- [x] Bonus Task done — automated canary analysis with Prometheus
```
