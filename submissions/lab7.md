# Lab 7 Submission — Progressive Delivery: Canary Deployments

> **Environment note (NixOS):** Used `nix develop` for `kubectl`/`k3d`. The old `quickticket` cluster (k3s v1.21.7) was stopped and incompatible with current Argo Rollouts CRDs — recreated with `k3d cluster create quickticket --image rancher/k3s:v1.32.5-k3s1 --wait`, redeployed QuickTicket, seeded DB, then completed the lab.

---

## Task 1 — Manual Canary Deployment

### 7.1: Argo Rollouts installed

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

Plugin installed to `~/.local/bin/kubectl-argo-rollouts` (no sudo needed on NixOS).

---

### 7.2: Gateway converted to Rollout (`k8s/gateway.yaml`)

- `kind: Deployment` → `kind: Rollout`
- `apiVersion: argoproj.io/v1alpha1`
- `replicas: 5` with canary strategy (20% → pause → 60% → 30s pause → 100%)

---

### 7.3: Canary paused at 20% (v2 deploy)

```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/stefan-genie/quickticket-gateway:d12a5c5b144edd79e5c5e14b4c70019bc7561c53 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5
```

1 canary pod (`APP_VERSION=v2`) + 4 stable pods (`APP_VERSION=v1`).

---

### 7.4: Traffic split verification (in-cluster loadgen)

```
pod/gateway-755777b4b4-kdf8t version=v2 events_requests=21
pod/gateway-b7c7b9d59-dl86q version=v1 events_requests=18
pod/gateway-b7c7b9d59-smrxx version=v1 events_requests=21
pod/gateway-b7c7b9d59-zgfnq version=v1 events_requests=20
pod/gateway-b7c7b9d59-zggl7 version=v1 events_requests=14
```

Canary pod received ~21 requests vs ~17–21 on each stable pod over 30s — roughly 1-in-5, matching `setWeight: 20`.

---

### 7.5: After manual `promote` — progression to 100%

```
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5
```

Promoted through 60% (step 2), auto-proceeded after 30s pause, reached 100% Healthy.

---

### 7.6: Bad version deployed and aborted

Bad canary (`APP_VERSION=v3-bad`) paused at 20%, then:

```
$ kubectl argo rollouts abort gateway
rollout 'gateway' aborted
```

```
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/stefan-genie/quickticket-gateway:... (stable)
Replicas:
  Updated:       0
```

Canary pod terminated immediately; stable v2 pods kept serving.

---

### 7.7: Abort vs git revert speed

**How long from `abort` to all traffic serving the stable version?**

Approximately **0.2 seconds** — `kubectl argo rollouts abort gateway` returned instantly and the canary ReplicaSet scaled down within the same second. Stable pods never stopped receiving traffic.

**Compare with `git revert` rollback from Lab 5:**

Lab 5 git revert took **~65 seconds** (push → ArgoCD detect drift → sync → new pod pull → readiness). Argo Rollouts abort is **~300× faster** because it kills the canary ReplicaSet in-cluster immediately — no Git push, no image pull, no CI pipeline. Trade-off: abort only rolls back the in-flight canary; git revert restores the entire declared state from Git history.

---

## Task 2 — Multi-Step Canary with Observation

### 7.8: Multi-step canary strategy

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

### 7.9: Rollout observation (`kubectl argo rollouts get rollout gateway --watch` snapshots)

**Step 1/9 — 20% (Updated: 1):**

```
Status:          ॥ Paused
  Step:          1/9
  SetWeight:     20
  ActualWeight:  20
Replicas:
  Updated:       1
```

**Step 3/9 — 40% (Updated: 2):**

```
Status:          ॥ Paused
  Step:          3/9
  SetWeight:     40
  ActualWeight:  40
Replicas:
  Updated:       2
```

**Step 4/9 — 60% (Updated: 3):**

```
Status:          ◌ Progressing
  Step:          4/9
  SetWeight:     60
  ActualWeight:  50
Replicas:
  Updated:       3
```

**Step 9/9 — 100% Healthy (Updated: 5):**

```
Status:          ✔ Healthy
  Step:          9/9
  SetWeight:     100
  ActualWeight:  100
Replicas:
  Updated:       5
```

| Time | Step | SetWeight | Updated replicas | Status |
|------|------|-----------|------------------|--------|
| 22:13:37 | 1/9 | 20 | 1 | Paused |
| 22:14:18 | 3/9 | 40 | 2 | Paused |
| 22:15:20 | 4/9 | 60 | 3 | Progressing |
| ~22:18 | 9/9 | 100 | 5 | Healthy |

**Dashboard observation:** Host docker-compose Grafana cannot scrape k3d pod IPs (Lab 3 limitation). Used `kubectl argo rollouts get rollout gateway --watch` instead — request rate stayed steady via in-cluster loadgen; updated-replica count climbed 1 → 2 → 3 → 5 as weight increased.

**At what canary percentage would you want an automated abort? Why?**

**20%** — with 5 replicas that is exactly 1 canary pod, the minimum blast radius that still receives real production traffic. Aborting at 20% after a short analysis window (60–90s) catches regressions before 40–60% of connections hit the bad version, while still collecting enough samples for error-rate measurement. Waiting until 80% means most users already see the failure.

---

## Bonus Task — Automated Canary Analysis

### B.1: In-cluster Prometheus — gateway targets with `rs_hash`

```
$ kubectl port-forward -n monitoring svc/prometheus 9091:9090 &
$ curl -s 'http://localhost:9091/api/v1/targets?state=active' | python3 -c "..."
gateway-64d757878-vm7lm rs= 64d757878 up
gateway-64d757878-fzl2j rs= 64d757878 up
gateway-64d757878-wqv45 rs= 64d757878 up
gateway-64d757878-m57tk rs= 64d757878 up
gateway-64d757878-86cj4 rs= 64d757878 up
```

All 5 gateway pods discovered with `health=up` and `rs_hash` from `rollouts-pod-template-hash`.

### B.2: AnalysisTemplate installed

```
$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   39m
```

### B.3: Analysis wired into Rollout strategy (`k8s/gateway.yaml`)

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

### B.4: Good version — auto-promote

```
$ kubectl get analysisrun
NAME                      STATUS       AGE
gateway-564c8d7d88-7-2    Successful   6m16s
gateway-64d757878-6-2     Successful   7m21s
```

AnalysisRun `gateway-564c8d7d88-7-2`: 3 measurements, all `value=[0]` → Successful → auto-promoted to 100% Healthy without manual `promote`.

### B.5: Bad version — auto-abort

Patched canary with `EVENTS_URL=http://broken-on-purpose:8081` and `GATEWAY_TIMEOUT_MS=2000`. Because `/health` checks upstream connectivity, readiness probes were temporarily pointed at `/metrics` so the canary pod stays up and `/events` returns 504 (matching lab intent).

```
$ kubectl get analysisrun
NAME                      STATUS       AGE
gateway-66879cdc59-10-2   Failed       80s
gateway-564c8d7d88-7-2    Successful   9m59s
```

Failed run measurements:

```yaml
metricResults:
  - name: error-rate
    phase: Failed
    measurements:
      - phase: Failed
        value: '[1]'
      - phase: Failed
        value: '[1]'
status:
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  phase: Failed
```

Rollout after auto-abort:

```
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 10: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/stefan-genie/quickticket-gateway:... (stable)
Replicas:
  Desired:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS        INFO
⟳ gateway                            Rollout      ✖ Degraded
├──# revision:10
│  ├──⧉ gateway-66879cdc59           ReplicaSet   • ScaledDown  canary
│  └──α gateway-66879cdc59-10-2      AnalysisRun  ✖ Failed      ✖ 2
├──# revision:7
│  ├──⧉ gateway-564c8d7d88           ReplicaSet   stable
│  │  ├──□ gateway-564c8d7d88-fkp6s  Pod          ✔ Running     ready:1/1
│  │  ├──□ gateway-564c8d7d88-jfl2d  Pod          ✔ Running     ready:1/1
│  │  ├──□ gateway-564c8d7d88-vnsgz  Pod          ✔ Running     ready:1/1
│  │  └──□ gateway-564c8d7d88-n8dtv  Pod          ✔ Running     ready:1/1
│  └──α gateway-564c8d7d88-7-2       AnalysisRun  ✔ Successful  ✔ 3
```

Stable pods untouched; canary scaled down automatically. Reverted `EVENTS_URL` and ran `kubectl argo rollouts retry rollout gateway` to restore Healthy.

### B.6: What metric beyond error rate?

**p99 latency** (`histogram_quantile(0.99, rate(gateway_request_duration_seconds_bucket{rs_hash="..."}[60s]))`) — a version can pass error-rate checks while silently degrading performance (slow upstream, connection pool exhaustion). Pairing error rate with latency catches "soft failures" before they become hard 5xx errors.

---

## PR Checklist

```text
- [x] Task 1 done — Argo Rollouts installed, canary deployed, promoted + aborted
- [x] Task 2 done — multi-step canary with observation
- [x] Bonus Task done — automated canary analysis with Prometheus
```
