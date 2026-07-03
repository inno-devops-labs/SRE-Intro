# Lab 7 — Progressive Delivery: Canary Deployments
**Student:** Valerii Tiniakov
**Group:** B24-SD-03

## Task 1 — Manual Canary Deployment (6 pts)

### 7.1: Argo Rollouts Version
```text
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab7)
$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:15:27Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: windows/amd64
```

### 7.3: Canary Paused at 20%
```text
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab7)
$ kubectl argo rollouts get rollout gateway --watch
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/wal1ys/quickticket-gateway:23d8036839eb050a0b5372c4abbbf68a58f71726 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS     AGE    INFO
⟳ gateway                            Rollout     ॥ Paused   2m37s
├──# revision:2
│  └──⧉ gateway-66fbf46745           ReplicaSet  ✔ Healthy  33s    canary
│     └──□ gateway-66fbf46745-g42bk  Pod         ✔ Running  32s    ready:1/1
└──# revision:1
   └──⧉ gateway-5b8b4bdc4f           ReplicaSet  ✔ Healthy  2m37s  stable
      ├──□ gateway-5b8b4bdc4f-52nmd  Pod         ✔ Running  2m36s  ready:1/1
      ├──□ gateway-5b8b4bdc4f-cmmr7  Pod         ✔ Running  2m36s  ready:1/1
      ├──□ gateway-5b8b4bdc4f-jdf95  Pod         ✔ Running  2m36s  ready:1/1
      └──□ gateway-5b8b4bdc4f-tvzwx  Pod         ✔ Running  2m36s  ready:1/1

```

### 7.5: Promoted to 100%
```text
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab7)
$ kubectl argo rollouts get rollout gateway --watch
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/wal1ys/quickticket-gateway:23d8036839eb050a0b5372c4abbbf68a58f71726 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ✔ Healthy     11m
├──# revision:2
│  └──⧉ gateway-66fbf46745           ReplicaSet  ✔ Healthy     9m41s  stable
│     ├──□ gateway-66fbf46745-g42bk  Pod         ✔ Running     9m40s  ready:1/1
│     ├──□ gateway-66fbf46745-24945  Pod         ✔ Running     63s    ready:1/1
│     ├──□ gateway-66fbf46745-r8fdh  Pod         ✔ Running     63s    ready:1/1
│     ├──□ gateway-66fbf46745-6smfx  Pod         ✔ Running     21s    ready:1/1
│     └──□ gateway-66fbf46745-vbpd2  Pod         ✔ Running     21s    ready:1/1
└──# revision:1
   └──⧉ gateway-5b8b4bdc4f           ReplicaSet  • ScaledDown  11m

```

### 7.6: Aborted Rollout
```text
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab7)
$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/wal1ys/quickticket-gateway:23d8036839eb050a0b5372c4abbbf68a58f71726 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         4
  Available:     4

NAME                                 KIND        STATUS         AGE    INFO
⟳ gateway                            Rollout     ✖ Degraded     16m
├──# revision:3
│  └──⧉ gateway-56888fdbf4           ReplicaSet  • ScaledDown   43s    canary
├──# revision:2
│  └──⧉ gateway-66fbf46745           ReplicaSet  ◌ Progressing  14m    stable
│     ├──□ gateway-66fbf46745-g42bk  Pod         ✔ Running      14m    ready:1/1
│     ├──□ gateway-66fbf46745-24945  Pod         ✔ Running      5m29s  ready:1/1
│     ├──□ gateway-66fbf46745-r8fdh  Pod         ✔ Running      5m29s  ready:1/1
│     ├──□ gateway-66fbf46745-6smfx  Pod         ✔ Running      4m47s  ready:1/1
│     └──□ gateway-66fbf46745-zx9z6  Pod         ✔ Running      5s     ready:0/1
└──# revision:1
   └──⧉ gateway-5b8b4bdc4f           ReplicaSet  • ScaledDown   16m


```

### Answer 1
**Question:** How long from `abort` to all traffic serving the stable version? Compare with `git revert` rollback from Lab 5.

**Answer:** The rollback using the abort command happens almost instantly (in a couple of seconds). This is because the stable pods from the previous version were never deleted — they continued running and serving 80% of the traffic. The Argo Rollouts controller simply needs to shift the routing weight back to 100% for the stable version and terminate the single canary pod. In contrast, a rollback via git revert (as seen in Lab 5) takes several minutes: it requires creating a commit, pushing it to the repository, waiting for ArgoCD to sync, and then executing a full Rolling Update cycle (spinning up new pods with the old image, passing readiness probes, and gradually terminating the faulty pods).

---

## Task 2 — Multi-Step Canary with Observation (4 pts)

### 7.8: Multi-Step Canary Strategy
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

### 7.9: Rollout Progression
```text
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          3/9
  SetWeight:     40
  ActualWeight:  40
Images:          ghcr.io/wal1ys/quickticket-gateway:23d8036839eb050a0b5372c4abbbf68a58f71726 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       2
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE   INFO
⟳ gateway                            Rollout     ॥ Paused      32m
├──# revision:4
│  └──⧉ gateway-85b67cfc99           ReplicaSet  ✔ Healthy     117s  canary
│     ├──□ gateway-85b67cfc99-msjqq  Pod         ✔ Running     116s  ready:1/1
│     └──□ gateway-85b67cfc99-dg8nx  Pod         ✔ Running     45s   ready:1/1
├──# revision:3
│  └──⧉ gateway-56888fdbf4           ReplicaSet  • ScaledDown  16m
├──# revision:2
│  └──⧉ gateway-66fbf46745           ReplicaSet  ✔ Healthy     30m   stable
│     ├──□ gateway-66fbf46745-g42bk  Pod         ✔ Running     30m   ready:1/1
│     ├──□ gateway-66fbf46745-24945  Pod         ✔ Running     21m   ready:1/1
│     └──□ gateway-66fbf46745-r8fdh  Pod         ✔ Running     21m   ready:1/1
└──# revision:1
   └──⧉ gateway-5b8b4bdc4f           ReplicaSet  • ScaledDown  32m

```

### Dashboard Observation
During the rollout, I observed the kubectl argo rollouts get rollout gateway --watch output. The Updated replicas smoothly scaled up (1 → 2 → 3 → 4 → 5) parallel to the SetWeight increases (20% → 40% → 60% → 80% → 100%). The old ReplicaSet systematically scaled down. If connected to a fully integrated Grafana dashboard, we would see the traffic volume shifting incrementally to the new pod IPs without any spike in 5xx error rates.

### Answer 2
**Question:** At what canary percentage would you want an automated abort? Why?

**Answer:** I would want an automated abort at the very first canary step (e.g., 10% or 20%). The goal of a canary deployment is to expose the minimum required number of users to a new version to gather statistical confidence. If the error rate spikes at the first 20% tier, the version is clearly defective. Aborting early minimizes the "blast radius" and prevents the remaining 80% of users from experiencing those errors.

---

## Bonus Task — Automated Canary Analysis (2 pts)

### B.2: AnalysisTemplate
```text
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab7)
$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   7s
```

### B.4 & B.5: AnalysisRuns (Successful and Failed)
```text
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab7)
$ kubectl get analysisrun
NAME                     STATUS   AGE
gateway-57d445f7c4-5-2   Failed   4m6s

```

### B.5: Failed AnalysisRun Details
```text
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab7)
$ kubectl get analysisrun gateway-57d445f7c4-5-2 -o yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisRun
metadata:
  annotations:
    rollout.argoproj.io/revision: "5"
  creationTimestamp: "2026-07-03T14:16:53Z"
  generation: 4
  labels:
    app: gateway
    rollout-type: Step
    rollouts-pod-template-hash: 57d445f7c4
    step-index: "2"
  name: gateway-57d445f7c4-5-2
  namespace: default
  ownerReferences:
  - apiVersion: argoproj.io/v1alpha1
    blockOwnerDeletion: true
    controller: true
    kind: Rollout
    name: gateway
    uid: d1871464-40ae-4f45-8a9d-c6d07f315eca
  resourceVersion: "9707"
  uid: 2726e2b9-8be4-4b14-b683-06d767d65b41
spec:
  args:
  - name: canary-hash
    value: 57d445f7c4
  metrics:
  - count: 3
    failureLimit: 1
    initialDelay: 60s
    interval: 20s
    name: error-rate
    provider:
      prometheus:
        address: http://prometheus.monitoring.svc.cluster.local:9090
        authentication:
          oauth2: {}
          sigv4: {}
        query: |
          (
            sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}",status=~"5.."}[60s]))
            or on() vector(0)
          )
          /
          sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}"}[60s]))
    successCondition: result[0] < 0.05
status:
  completedAt: "2026-07-03T14:18:13Z"
  dryRunSummary: {}
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
  - count: 2
    failed: 2
    measurements:
    - finishedAt: "2026-07-03T14:17:53Z"
      phase: Failed
      startedAt: "2026-07-03T14:17:53Z"
      value: '[0.3369565217391304]'
    - finishedAt: "2026-07-03T14:18:13Z"
      phase: Failed
      startedAt: "2026-07-03T14:18:13Z"
      value: '[0.38095238095238093]'
    metadata:
      ResolvedPrometheusQuery: |
        (
          sum(rate(gateway_requests_total{rs_hash="57d445f7c4",status=~"5.."}[60s]))
          or on() vector(0)
        )
        /
        sum(rate(gateway_requests_total{rs_hash="57d445f7c4"}[60s]))
    name: error-rate
    phase: Failed
  phase: Failed
  runSummary:
    count: 1
    failed: 1
  startedAt: "2026-07-03T14:16:53Z"


```

### B.5: Final Rollout Status
```text
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 5: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/wal1ys/quickticket-gateway:23d8036839eb050a0b5372c4abbbf68a58f71726 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS        AGE    INFO
⟳ gateway                            Rollout      ✖ Degraded    47m
├──# revision:5
│  ├──⧉ gateway-57d445f7c4           ReplicaSet   • ScaledDown  3m47s  canary
│  └──α gateway-57d445f7c4-5-2       AnalysisRun  ✖ Failed      3m15s  ✖ 2
├──# revision:4
│  └──⧉ gateway-85b67cfc99           ReplicaSet   ✔ Healthy     17m    stable
│     ├──□ gateway-85b67cfc99-msjqq  Pod          ✔ Running     17m    ready:1/1
│     ├──□ gateway-85b67cfc99-dg8nx  Pod          ✔ Running     15m    ready:1/1
│     ├──□ gateway-85b67cfc99-45pjg  Pod          ✔ Running     14m    ready:1/1
│     ├──□ gateway-85b67cfc99-mqlt7  Pod          ✔ Running     13m    ready:1/1
│     └──□ gateway-85b67cfc99-kc6jh  Pod          ✔ Running     113s   ready:1/1
├──# revision:3
│  └──⧉ gateway-56888fdbf4           ReplicaSet   • ScaledDown  31m
├──# revision:2
│  └──⧉ gateway-66fbf46745           ReplicaSet   • ScaledDown  45m
└──# revision:1
   └──⧉ gateway-5b8b4bdc4f           ReplicaSet   • ScaledDown  47m

```

### Answer Bonus
**Question:** What metric would you add beyond error rate for a more complete canary analysis?


**Answer:** I would add the 95th percentile (p95) response latency and Memory/CPU utilization.
Relying only on the error rate is not enough because a new canary version might not return 5xx errors, but it could introduce severe performance regressions (e.g., making requests 10x slower). Monitoring latency ensures the user experience remains fast. Additionally, monitoring resource usage protects the cluster from memory leaks that might eventually crash the pods with Out-Of-Memory (OOM) errors after the rollout completes.