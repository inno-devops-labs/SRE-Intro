# Lab 7 — Progressive Delivery: Canary Deployments

**Author:** Anton Bugaev  
**Date:** 2026-06-29  
**Cluster:** k3d `quickticket` (recreated for this lab)

---

## Task 1 — Manual Canary Deployment

### 7.1 Argo Rollouts version

```
$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:11:48Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: darwin/arm64
```

Controller installed in namespace `argo-rollouts`.

### 7.2 Gateway converted to Rollout

`k8s/gateway.yaml`: `kind: Rollout`, `apiVersion: argoproj.io/v1alpha1`, `replicas: 5`, canary strategy with analysis (see Bonus wiring in final manifest).

Local verification used `quickticket-gateway:v1` images imported via `k3d image import` (same app code as ghcr image in committed manifest).

### 7.3 Canary paused at 20% (APP_VERSION v1 → v2)

```
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
```

### 7.4 Traffic split verification (in-cluster loadgen)

```
pod/gateway-5d444cdd8c-894fs APP_VERSION=v1 events_requests=21
pod/gateway-5d444cdd8c-hkms7 APP_VERSION=v1 events_requests=30
pod/gateway-5d444cdd8c-jkfs2 APP_VERSION=v1 events_requests=31
pod/gateway-5d444cdd8c-pkff9 APP_VERSION=v1 events_requests=30
pod/gateway-cd54f9c66-g8bsz APP_VERSION=v2 events_requests=27
```

Canary pod received ~19% of `/events` traffic (27 / 139 ≈ 19%) — matches `setWeight: 20`.

### 7.5 After `kubectl argo rollouts promote gateway`

Rollout progressed through 60% → 30s pause → **100% Healthy** (all 5 pods on revision v2).

```
Status:          ✔ Healthy
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
  Updated:       5
```

### 7.6 Bad version + `abort`

Deployed `APP_VERSION=v3-bad`, paused at 20%:

```
Status:          ॥ Paused
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
  Updated:       1
```

```
$ kubectl argo rollouts abort gateway
```

```
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
  Step:          0/5
  SetWeight:     0
Images:          quickticket-gateway:v1 (stable)
```

Canary pod scaled down instantly; stable revision kept serving.

### Abort vs git revert (Lab 5)

| Method | Time to stable traffic |
|--------|------------------------|
| `kubectl argo rollouts abort` | **~5 seconds** (canary pod terminated, weight → 0) |
| `git revert` + ArgoCD sync (Lab 5) | **~26 seconds** (commit → push → sync → image pull) |

**Why:** `abort` is a control-plane action — kube-proxy stops routing to canary ReplicaSet immediately. GitOps rollback waits for Git → ArgoCD → Kubernetes reconciliation and possibly image pull.

---

## Task 2 — Multi-Step Canary with Observation

### Strategy YAML applied

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

### `--watch` observations (excerpt)

| Time | Step | SetWeight | ActualWeight | Updated replicas |
|------|------|-----------|--------------|------------------|
| t+0 | 1/9 | 20 | 20 | 1 |
| t+60s | 3/9 | 40 | 40 | 2 |
| t+120s | 5/9 | 60 | 60 | 3 |
| t+180s | 7/9 | 80 | 80 | 4 |
| t+210s | 9/9 | 100 | 100 | 5 |

Final status: **✔ Healthy** at step 9/9.

### Dashboard observation

Docker-compose Grafana cannot scrape k3d pod IPs (private bridge network). Observed via `kubectl argo rollouts get rollout gateway --watch` instead (per lab hint):

- Request rate stayed steady while loadgen ran (continuous `curl` loop to `http://gateway:8080/events`)
- `Updated` replica count climbed **1 → 2 → 3 → 4 → 5** as weight increased
- No error spike during good rollout (v2.1)

### At what canary % would you want automated abort?

**20–40%.** At 20% only ~1/5 pods are canary — enough traffic for error-rate statistics with loadgen, but blast radius stays small. Waiting until 60–80% lets a bad version touch most users before analysis completes. Automated abort should fire as early as metrics are statistically meaningful (after `initialDelay` + 2–3 measurement windows).

---

## Bonus Task — Automated Canary Analysis

### In-cluster Prometheus targets

```
gateway-6cffcc6f66-c5btn rs= 6cffcc6f66 up
gateway-6cffcc6f66-fzl2g rs= 6cffcc6f66 up
gateway-6cffcc6f66-kssgc rs= 6cffcc6f66 up
gateway-6cffcc6f66-scqwd rs= 6cffcc6f66 up
gateway-6cffcc6f66-xk5qr rs= 6cffcc6f66 up
gateway-6cffcc6f66-zbqff rs= 6cffcc6f66 up
```

All gateway pods discovered with `rs_hash` label for canary-scoped queries.

### AnalysisTemplate

```
$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   1s
```

File: `k8s/analysis-template.yaml` (same query as `labs/lab7/analysis-template.yaml`).

### Good version — auto-promote

```
$ kubectl argo rollouts set image gateway gateway=quickticket-gateway:v2
```

AnalysisRun `gateway-6cffcc6f66-6-2`: **Successful**, measurements `value=[0]` × 3 → auto-promoted to **100% Healthy** without manual `promote`.

```
$ kubectl get analysisrun
NAME                     STATUS       AGE
gateway-6cffcc6f66-6-2   Successful   2m8s
```

### Bad version — auto-abort

Set `EVENTS_URL=http://broken-on-purpose:8081` (every `/events` → 504).

> **Note:** Gateway `/health` returns 503 when events is unreachable, so readinessProbe was temporarily switched to `tcpSocket:8080` during this test so the canary pod becomes Ready and receives traffic (otherwise AnalysisRun never gets samples). Committed `k8s/gateway.yaml` keeps standard HTTP `/health` readiness.

AnalysisRun `gateway-67b874d5c9-8-2`: **Failed**

```yaml
measurements:
  - phase: Failed
    value: '[1]'
  - phase: Failed
    value: '[1]'
```

```
Status:          ✖ Degraded
Message:         RolloutAborted: ... Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Images:          quickticket-gateway:v1 (stable)
```

Stable pods untouched; canary scaled down automatically.

### Metric beyond error rate?

Add **p99 latency** (`histogram_quantile` on `gateway_request_duration_seconds`) and **CPU saturation** (`container_cpu_usage_seconds_total`). Error rate alone misses slow-burn regressions — a canary can return 200s with 3× latency and burn SLO before error rate moves.

---

## Verification checklist

- [x] Argo Rollouts v1.9.0 installed
- [x] Gateway Rollout with 5 replicas
- [x] Canary paused at 20%, traffic split verified
- [x] Manual promote to 100%
- [x] Bad deploy aborted (~5s)
- [x] Multi-step canary observed (steps 20→40→60→80→100)
- [x] AnalysisTemplate + auto-promote (Successful) + auto-abort (Failed, value=[1])
