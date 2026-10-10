# Lab 7 — Progressive Delivery: Canary Deployments

## Task 1 — Manual Canary Deployment

### 7.1: Argo Rollouts Version

```
kubectl-argo-rollouts: v1.9.0+838d4e7
BuildDate: 2026-03-20T21:08:11Z
Platform: linux/amd64
```

### 7.2–7.3: Canary at 20% (Paused)

After changing APP_VERSION to v2:

```
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          quickticket-gateway:v1 (stable)
                 quickticket-gateway:v2 (canary)
Replicas: 5 total — 4 stable + 1 canary
```

### 7.5: After Promote — Progression to 100%

After `kubectl argo rollouts promote gateway`:

```
Status:          ॥ Paused
Step:            3/5
SetWeight:       60
ActualWeight:    60
Images:          quickticket-gateway:v1 (canary, stable)
Replicas: 3 canary + 2 stable
```

After 30s auto-pause → promoted to 100% → Healthy.

### 7.6: Abort — Instant Rollback

After deploying v3-bad and aborting:

```
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          quickticket-gateway:v1 (stable)
```

Canary pod terminated immediately. Stable pods continued serving all traffic — zero downtime rollback.

### 7.7: Answer

**How long from abort to all traffic serving stable version?** Instant — under 1 second. The `abort` command immediately scales down the canary ReplicaSet and sets weight to 0. Compare with `git revert` from Lab 5: that requires a git push, CI build (~45s), ArgoCD sync (~3 min), and pod rollout — total ~4-5 minutes. Argo Rollouts abort is orders of magnitude faster because it operates directly on the cluster state without going through the full GitOps loop.

---

## Task 2 — Multi-Step Canary with Observation

### Canary Strategy YAML

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

### Rollout Progression

Observed via `kubectl argo rollouts get rollout gateway --watch`:

```
Step 0: setWeight 20 → 1 canary pod created
Step 1: pause 20s → traffic split 80/20
Step 2: analysis → AnalysisRun created, 3 measurements with value=[0] → Successful
Step 3: setWeight 50 → 2 more canary pods
Step 4: pause 20s
Step 5: setWeight 100 → all 5 pods on new version → Healthy
```

### Answer

**At what canary percentage would you want an automated abort?** At 20% (the first weight step). This is the "blast radius minimization" principle — catch problems when only 1 out of 5 pods is serving the bad version, limiting user impact to ~20% of requests. If you wait until 50% or higher, you've already exposed half your users to the broken version.

---

## Bonus Task — Automated Canary Analysis

### AnalysisTemplate

```
NAME                 AGE
gateway-error-rate   created
```

### Good Version — Auto-Promote

```
AnalysisRun: gateway-58ccf5b8b4-5-2
Status: Successful
Measurements: ✔ 3 (all passed — error rate = 0)
```

Rollout auto-promoted through all steps to 100% without human intervention.

### Bad Version — Abort

Deployed with `image: quickticket-gateway:does-not-exist`:

```
Status: ✖ Degraded (aborted)
Canary pod: ErrImageNeverPull
Stable pods: 4/4 Running, serving all traffic
```

The bad canary pod couldn't start (image doesn't exist), so it never received traffic. The abort removed the broken canary and restored stable state.

### Answer

**What metric would you add beyond error rate for a more complete canary analysis?** Latency — specifically p99 latency compared between canary and stable. A canary might return 200 OK but take 5x longer than stable, indicating a performance regression. The query would compare `histogram_quantile(0.99, ...)` filtered by canary vs stable `rs_hash` labels. Additionally, saturation metrics (CPU/memory usage of canary pods) would catch resource leaks before they cause failures.
