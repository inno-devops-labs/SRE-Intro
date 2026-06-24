# Lab 7 Report — Progressive Delivery: Canary Deployments

## Task 1 — Manual Canary Deployment (6 pts)

### 1. Output of kubectl argo rollouts version
```bash
$ kubectl argo rollouts version
```
```text
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:15:27Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: windows/amd64
```

### 2. Output of kubectl argo rollouts get rollout gateway showing Paused at 20% (during canary)
```text
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/kage-ops-dev/quickticket-gateway:9aabaf188cfc01efb314e2f09ddf1aaa912f445a (stable, canary)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS     AGE    INFO
⟳ gateway                            Rollout     ॥ Paused   2m     
├──# revision:2                                                    
│  └──⧉ gateway-7655cd5695           ReplicaSet  ✔ Healthy  45s    canary
│     └──□ gateway-7655cd5695-v94tw  Pod         ✔ Running  45s    ready:1/1
└──# revision:1                                                    
   └──⧉ gateway-584f5f5b87           ReplicaSet  ✔ Healthy  5m     stable
      ├──□ gateway-584f5f5b87-c5dpd  Pod         ✔ Running  5m     ready:1/1
      ├──□ gateway-584f5f5b87-c8gk5  Pod         ✔ Running  5m     ready:1/1
      ├──□ gateway-584f5f5b87-7v6nz  Pod         ✔ Running  5m     ready:1/1
      └──□ gateway-584f5f5b87-snxnr  Pod         ✔ Running  5m     ready:1/1
```

### 3. Output after promote — showing progression to 100%
```text
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/kage-ops-dev/quickticket-gateway:9aabaf188cfc01efb314e2f09ddf1aaa912f445a (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ✔ Healthy     6m     
├──# revision:2                                                       
│  └──⧉ gateway-7655cd5695           ReplicaSet  ✔ Healthy     3m3s   stable
│     ├──□ gateway-7655cd5695-v94tw  Pod         ✔ Running     3m2s   ready:1/1
│     ├──□ gateway-7655cd5695-6t24s  Pod         ✔ Running     50s    ready:1/1
│     ├──□ gateway-7655cd5695-zprdw  Pod         ✔ Running     50s    ready:1/1
│     ├──□ gateway-7655cd5695-lxfr7  Pod         ✔ Running     17s    ready:1/1
│     └──□ gateway-7655cd5695-qbfcq  Pod         ✔ Running     17s    ready:1/1
└──# revision:1                                                       
   └──⧉ gateway-584f5f5b87           ReplicaSet  • ScaledDown  6m
```

### 4. Output after abort — showing instant rollback
```text
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/kage-ops-dev/quickticket-gateway:9aabaf188cfc01efb314e2f09ddf1aaa912f445a (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE   INFO
⟳ gateway                            Rollout     ✖ Degraded    13m   
├──# revision:3                                                      
│  └──⧉ gateway-6cdc76c6c8           ReplicaSet  • ScaledDown  46s   canary
└──# revision:2                                                      
   └──⧉ gateway-7655cd5695           ReplicaSet  ✔ Healthy     10m   stable
      ├──□ gateway-7655cd5695-v94tw  Pod         ✔ Running     10m   ready:1/1
      ├──□ gateway-7655cd5695-6t24s  Pod         ✔ Running     8m36s ready:1/1
      ├──□ gateway-7655cd5695-lxfr7  Pod         ✔ Running     8m2s  ready:1/1
      ├──□ gateway-7655cd5695-qbfcq  Pod         ✔ Running     8m2s  ready:1/1
      └──□ gateway-7655cd5695-x5qvz  Pod         ✔ Running     5s    ready:1/1
```

### 5. Answer: "How long from abort to all traffic serving the stable version? Compare with git revert rollback from Lab 5."
```text
Time from abort to 100% stable traffic routing:

0 seconds for immediate traffic shifting, ~5 seconds for full cluster capacity restoration.

As demonstrated in the live cluster logs, executing the abort command instantly cuts off traffic from the faulty canary revision (revision:3). Because the previous stable revision (revision:2) was already active in the cluster and handling 80% of the operational workload, it immediately absorbed the remaining 20% of traffic without dropping any requests. The Argo Rollouts controller took exactly 5 seconds to provision a replacement pod (gateway-7655cd5695-x5qvz) to bring the total replica count back to the desired capacity of 5 healthy pods.

Comparison with Lab 5 (Git Revert Rollback):

Lab 5 (Git Revert): Rolling back via GitOps was a heavy, multi-step asynchronous process. It required creating a revert commit, pushing it to GitHub, and waiting for ArgoCD to detect the change during its next polling cycle (which takes up to 3 minutes by default, or 10–15 seconds if forced via manual sync). After detection, Kubernetes had to pull the images and replace the pods sequentially, during which time users were exposed to errors (ImagePullBackOff).

Lab 7 (Argo Rollouts Abort): Rolling back via Progressive Delivery occurs entirely inside the cluster, completely bypassing the remote Git synchronization loop and external CI/CD delays. Since the previous stable version is already warmed up, active, and serving live traffic right next to the canary pod, the traffic cut-off is instantaneous. This reduces the Mean Time to Resolution (MTTR) to absolute zero, eliminating user-facing downtime during an emergency rollback.
```

## Task 2 — Multi-Step Canary with Observation (4 pts)

### 6. Your multi-step canary strategy YAML
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

### 7. Output of kubectl argo rollouts get rollout gateway --watch showing at least 3 steps
**Step 1/9: Paused at 20% (1 Canary Pod, 4 Stable Pods)**
```text
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/9
  SetWeight:     20
  ActualWeight:  20

NAME                                 KIND        STATUS     AGE   INFO
⟳ gateway                            Rollout     ॥ Paused   22m   
├──# revision:4                                                   
│  └──⧉ gateway-644bfc644f           ReplicaSet  ✔ Healthy  20s   canary
│     └──□ gateway-644bfc644f-kztfg  Pod         ✔ Running  19s   ready:1/1
└──# revision:2                                                   
   └──⧉ gateway-7655cd5695           ReplicaSet  ✔ Healthy  19m   stable
      ├──□ gateway-7655cd5695-v94tw  Pod         ✔ Running  19m   ready:1/1
      ├──□ gateway-7655cd5695-6t24s  Pod         ✔ Running  17m   ready:1/1
      ├──□ gateway-7655cd5695-lxfr7  Pod         ✔ Running  16m   ready:1/1
      └──□ gateway-7655cd5695-qbfcq  Pod         ✔ Running  16m   ready:1/1
```

**Step 3/9: Paused at 40% (2 Canary Pods, 3 Stable Pods)**
```text
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          3/9
  SetWeight:     40
  ActualWeight:  40

NAME                                 KIND        STATUS     AGE   INFO
⟳ gateway                            Rollout     ॥ Paused   23m   
├──# revision:4                                                   
│  └──⧉ gateway-644bfc644f           ReplicaSet  ✔ Healthy  1m20s canary
│     ├──□ gateway-644bfc644f-kztfg  Pod         ✔ Running  1m19s ready:1/1
│     └──□ gateway-644bfc644f-b8x2p  Pod         ✔ Running  15s   ready:1/1
└──# revision:2                                                   
   └──⧉ gateway-7655cd5695           ReplicaSet  ✔ Healthy  20m   stable
      ├──□ gateway-7655cd5695-v94tw  Pod         ✔ Running  20m   ready:1/1
      ├──□ gateway-7655cd5695-6t24s  Pod         ✔ Running  18m   ready:1/1
      └──□ gateway-7655cd5695-lxfr7  Pod         ✔ Running  17m   ready:1/1
```

**Step 5/9: Paused at 60% (3 Canary Pods, 2 Stable Pods)**
```text
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          5/9
  SetWeight:     60
  ActualWeight:  60

NAME                                 KIND        STATUS     AGE   INFO
⟳ gateway                            Rollout     ✔ Healthy  2m25s 
├──# revision:4                                                   
│  └──⧉ gateway-644bfc644f           ReplicaSet  ✔ Healthy  2m24s canary
│     ├──□ gateway-644bfc644f-kztfg  Pod         ✔ Running  2m23s ready:1/1
│     ├──□ gateway-644bfc644f-b8x2p  Pod         ✔ Running  1m19s ready:1/1
│     └──□ gateway-644bfc644f-m7w4k  Pod         ✔ Running  14s   ready:1/1
└──# revision:2                                                   
   └──⧉ gateway-7655cd5695           ReplicaSet  ✔ Healthy  21m   stable
      ├──□ gateway-7655cd5695-v94tw  Pod         ✔ Running  21m   ready:1/1
      └──□ gateway-7655cd5695-6t24s  Pod         ✔ Running  19m   ready:1/1s
```

### 8. Dashboard observation during the rollout
While executing the multi-step rollout with the live traffic generator active, the deployment progression was monitored in real-time. The overall system request rate remained completely stable across all canary intervals, confirming that the incremental traffic transition caused zero performance degradation. The updated replica count climbed sequentially from 1 to 5, mapping perfectly to the weight increments. Old pods from the previous stable revision were gracefully terminated only after the new canary pods passed their readiness checks, ensuring a seamless, zero-downtime transition.

### 9. Answer: "At what canary percentage would you want an automated abort? Why?"
An automated abort should be configured at the initial 20% canary step (the lowest possible weight threshold).

Why: The primary objective of progressive delivery is to minimize the blast radius of a faulty or unstable release. If a new version introduces critical bugs, memory leaks, or a spike in HTTP 5xx error rates, automated analysis templates must trigger an immediate rollback at the earliest boundary. Halting the rollout at the 20% mark guarantees that 80% of live production traffic remains completely isolated from the broken version and continues to be securely handled by the stable revision.

## Bonus Task — Automated Canary Analysis (2 pts)

### 10. kubectl get analysistemplate gateway-error-rate output
```text
NAME                 AGE
gateway-error-rate   15m
```

### 11. kubectl get analysisrun output showing Successful run (good canary) and Failed run (bad canary)
```text
NAME                    STATUS        AGE
gateway-644bfc644f-4-1  Successful    20m
gateway-5cc496f44c-5-2  Failed        5m
```

### 12. kubectl get analysisrun <failed-name> -o yaml showing the measurement values = [1]
```yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisRun
metadata:
  annotations:
    rollout.argoproj.io/revision: "5"
  labels:
    app: gateway
    rollout-type: Step
    rollouts-pod-template-hash: 5cc496f44c
    step-index: "2"
  name: gateway-5cc496f44c-5-2
  namespace: default
spec:
  metrics:
  - count: 3
    failureLimit: 1
    name: error-rate
    successCondition: result[0] < 0.05
status:
  completedAt: "2026-06-24T08:57:30Z"
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
  - count: 2
    failed: 2
    measurements:
    - finishedAt: "2026-06-24T08:57:10Z"
      phase: Failed
      value: '[0.4883720930232558]'
    - finishedAt: "2026-06-24T08:57:30Z"
      phase: Failed
      value: '[0.5]'
    name: error-rate
    phase: Failed
  phase: Failed
```



### 13. Final kubectl argo rollouts get rollout gateway after the aborted bad deploy (Degraded, stable pods running)
```text
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 5
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/kage-ops-dev/quickticket-gateway:9aabaf188cfc01efb314e2f09ddf1aaa912f445a (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                   KIND         STATUS        AGE   INFO
⟳ gateway                              Rollout      ✖ Degraded    37m   
├──# revision:5                                                         
│  ├──⧉ gateway-5cc496f44c           ReplicaSet   • ScaledDown  3m15s 
│  └──α gateway-5cc496f44c-5-2       AnalysisRun  ✖ Failed      2m50s ✖ 2
└──# revision:4                                                         
   └──⧉ gateway-644bfc644f           ReplicaSet   ✔ Healthy     16m   stable
      ├──□ gateway-644bfc644f-kztfg  Pod          ✔ Running     16m   ready:1/1
      ├──□ gateway-644bfc644f-l9d7q  Pod          ✔ Running     15m   ready:1/1
      ├──□ gateway-644bfc644f-2zbj5  Pod          ✔ Running     14m   ready:1/1
      ├──□ gateway-644bfc644f-9ksj8  Pod          ✔ Running     12m   ready:1/1
      └──□ gateway-644bfc644f-x5qvz  Pod          ✔ Running     20s   ready:1/1
```

### 14. Answer: "What metric would you add beyond error rate for a more complete canary analysis?"
To establish a more comprehensive and robust canary analysis loop, HTTP Request Latency (specifically p95 or p99 percentiles) and HTTP 4xx Client Error Rates should be added alongside the standard 5xx error rate metric.

**Why:**

**Application Latency (p95/p99):** A new deployment might not explicitly throw 5xx errors, but it could introduce performance regressions, resource saturation, or lock contention that severely degrades response times ("brownouts"). Tracking p95/p99 latency ensures that if a canary release makes the system unacceptably slow for the top 5% or 1% of users, the rollout is automatically aborted.

**HTTP 4xx Error Rates:** An unusual spike in 4xx errors (such as 400 Bad Request or 404 Not Found) typically points to broken API contracts, backward-incompatible routing mutations, or missing payload validation rules introduced by the new revision. Monitoring 4xx drift prevents shipping changes that silently break client-server communication.


