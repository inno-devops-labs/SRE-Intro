# Lab 7 — Progressive Delivery: Canary Deployments

## Task 1 — Manual Canary Deployment

### 7.1: Install Argo Rollouts

**kubectl argo rollouts version:**
```
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:11:48Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: darwin/amd64
```

### 7.2-7.4: Canary at 20% with traffic split verification

**Rollout paused at 20%:**
```
Name:            gateway
Namespace:       default
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

NAME                                KIND        STATUS        AGE    INFO
⟳ gateway                           Rollout     ॥ Paused      2m8s  
├──# revision:2                                                       
│  └──⧉ gateway-6567ff84c           ReplicaSet  ✔ Healthy     13s   canary
│     └──□ gateway-6567ff84c-qvlmt  Pod         ✔ Running     12s   ready:1/1
└──# revision:1                                                       
   └──⧉ gateway-bb5476b6            ReplicaSet  ✔ Healthy     2m8s  stable
      ├──□ gateway-bb5476b6-6678g   Pod         ✔ Running     2m8s  ready:1/1,restarts:2
      ├──□ gateway-bb5476b6-g6hwh   Pod         ✔ Running     2m8s  ready:1/1,restarts:2
      ├──□ gateway-bb5476b6-hwkjq   Pod         ✔ Running     2m8s  ready:1/1,restarts:2
      └──□ gateway-bb5476b6-r974j   Pod         ✔ Running     2m8s  ready:1/1,restarts:2
```

**Traffic split verification (30s loadgen):**
```
pod/gateway-6567ff84c-qvlmt image=quickticket-gateway:v1 events_requests=37
pod/gateway-bb5476b6-6678g image=quickticket-gateway:v1 events_requests=30
pod/gateway-bb5476b6-g6hwh image=quickticket-gateway:v1 events_requests=35
pod/gateway-bb5476b6-hwkjq image=quickticket-gateway:v1 events_requests=32
pod/gateway-bb5476b6-r974j image=quickticket-gateway:v1 events_requests=25
```

The canary pod received 37 requests out of 159 total (23.3%), which is close to the expected 20% weight.

### 7.5: Promote to 100%

**After promote command:**
```
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          2/5
  SetWeight:     60
  ActualWeight:  25
Images:          quickticket-gateway:v1 (canary, stable)
Replicas:
  Desired:       5
  Current:       6
  Updated:       3
  Ready:         4
  Available:     4
```

**After 30s pause and auto-promotion to 100%:**
```
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          quickticket-gateway:v1 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                KIND        STATUS        AGE    INFO
⟳ gateway                           Rollout     ✔ Healthy     4m25s  
├──# revision:2                                                      
│  └──⧉ gateway-6567ff84c           ReplicaSet  ✔ Healthy     2m30s  stable
│     ├──□ gateway-6567ff84c-qvlmt  Pod         ✔ Running     2m29s  ready:1/1
│     ├──□ gateway-6567ff84c-qgrsc  Pod         ✔ Running     69s    ready:1/1
│     ├──□ gateway-6567ff84c-ss4pb  Pod         ✔ Running     69s    ready:1/1
│     ├──□ gateway-6567ff84c-46hjn  Pod         ✔ Running     28s    ready:1/1
│     └──□ gateway-6567ff84c-89kb2  Pod         ✔ Running     28s    ready:1/1
└──# revision:1                                                      
   └──⧉ gateway-bb5476b6            ReplicaSet  • ScaledDown   4m25s  
```

### 7.6: Deploy bad version and abort

**Canary paused at 20% with bad version:**
```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          quickticket-gateway:v1 (canary, stable)
```

**After abort command:**
```
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          quickticket-gateway:v1 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         4
  Available:     4

NAME                                KIND        STATUS         AGE    INFO
⟳ gateway                           Rollout     ✖ Degraded     4m53s  
├──# revision:3                                                       
│  └──⧉ gateway-7c4c865f8b          ReplicaSet  • ScaledDown   21s    canary
├──# revision:2                                                       
│  └──⧉ gateway-6567ff84c           ReplicaSet  ◌ Progressing  2m58s  stable
│     ├──□ gateway-6567ff84c-qvlmt  Pod         ✔ Running      2m57s  ready:1/1
│     ├──□ gateway-6567ff84c-qgrsc  Pod         ✔ Running      97s    ready:1/1
│     ├──□ gateway-6567ff84c-ss4pb  Pod         ✔ Running      97s    ready:1/1
│     ├──□ gateway-6567ff84c-46hjn  Pod         ✔ Running      56s    ready:1/1
│     └──□ gateway-6567ff84c-lx5fs  Pod         ✔ Running      4s     ready:0/1
└──# revision:1                                                       
   └──⧉ gateway-bb5476b6            ReplicaSet  • ScaledDown   4m53s  
```

The canary pod was immediately killed and stable pods continued serving traffic.

### 7.7: Answer - Abort vs git revert speed

**Question:** How long from `abort` to all traffic serving the stable version? Compare with `git revert` rollback from Lab 5.

**Answer:** The abort took approximately 5-10 seconds from command execution to all stable pods being ready. This is significantly faster than `git revert` rollback from Lab 5, which required:
1. Reverting the commit
2. Rebuilding the Docker image
3. Pushing the image to registry
4. Redeploying via ArgoCD
5. Waiting for pods to roll out

The canary abort is instant because it simply kills the canary pods and keeps the stable pods running - no image rebuild or full redeployment is needed. This is a key advantage of canary deployments for quick rollback when issues are detected early.

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

### 7.9: Rollout observation

**Step 1 - 20% weight:**
```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/9
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

**Step 3 - 40% weight:**
```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          3/9
  SetWeight:     40
  ActualWeight:  40
Images:          quickticket-gateway:v1 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       2
  Ready:         5
  Available:     5
```

**Step 7 - 80% weight:**
```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          7/9
  SetWeight:     80
  ActualWeight:  80
Images:          quickticket-gateway:v1 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       4
  Ready:         5
  Available:     5
```

**Final - 100% weight (Healthy):**
```
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          9/9
  SetWeight:     100
  ActualWeight:  100
Images:          quickticket-gateway:v1 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5
```

**Observations:**
- Request rate stayed steady across canary steps
- Updated-replica count climbed 1 → 2 → 3 → 4 → 5 as weight climbed
- The gradual rollout allowed observation at each step before proceeding

### Answer: Automated abort threshold

**Question:** At what canary percentage would you want an automated abort? Why?

**Answer:** I would set an automated abort at 20-40% canary percentage. At this point:
1. Enough traffic is flowing to the canary to detect meaningful issues
2. The blast radius is still limited if something goes wrong
3. Early detection prevents wasting time on higher-traffic steps
4. It aligns with the "fail fast" principle - catching issues early minimizes impact

The exact threshold depends on the service's criticality and traffic volume. For a critical service, 20% provides a good balance between detection capability and risk mitigation.

---

## Bonus Task — Automated Canary Analysis

### B.1: In-cluster Prometheus verification

**Prometheus targets verification:**
```
gateway-6b8fb8799b-5zpzf rs= 6b8fb8799b up
gateway-6b8fb8799b-ttw72 rs= 6b8fb8799b up
gateway-6b8fb8799b-dn8fw rs= 6b8fb8799b up
gateway-6b8fb8799b-jcv4r rs= 6b8fb8799b up
gateway-6b8fb8799b-q4nl9 rs= 6b8fb8799b up
```

All 5 gateway pods are discovered with `rs_hash` label and health=up.

### B.2: AnalysisTemplate

**kubectl get analysistemplate gateway-error-rate:**
```
NAME                 AGE
gateway-error-rate   5m
```

### B.3-B.4: Analysis run with bad version (auto-abort)

**kubectl get analysisrun:**
```
NAME                     STATUS   AGE
gateway-7fb5b8f95c-6-2   Failed   101s
```

**kubectl get analysisrun gateway-7fb5b8f95c-6-2 -o yaml (measurement values):**
```yaml
status:
  metricResults:
  - count: 2
    failed: 2
    measurements:
    - finishedAt: "2026-07-01T20:32:26Z"
      phase: Failed
      startedAt: "2026-07-01T20:32:26Z"
      value: '[0.4260869565217391]'
    - finishedAt: "2026-07-01T20:32:46Z"
      phase: Failed
      startedAt: "2026-07-01T20:32:46Z"
      value: '[0.46956521739130436]'
    name: error-rate
    phase: Failed
  phase: Failed
```

The analysis detected error rates of 42.6% and 46.9%, both exceeding the 5% threshold, causing automatic abort.

**Final rollout status after auto-abort:**
```
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 6: Step-based analysis phase error/failed
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          quickticket-gateway:v1 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5
```

The stable pods (revision 5) continued serving traffic while the canary (revision 6) was automatically aborted.

### Answer: Additional metric for canary analysis

**Question:** What metric would you add beyond error rate for a more complete canary analysis?

**Answer:** I would add **latency (p95/p99 response time)** as an additional metric. Error rate alone doesn't capture performance degradation - a canary might have 0% errors but significantly slower response times, which is also a failure condition. Monitoring latency percentiles ensures the canary performs at least as well as the stable version. Other useful metrics could include:
- Request rate (to ensure canary is receiving expected traffic)
- CPU/memory usage (to detect resource leaks)
- Custom business metrics (e.g., conversion rate, checkout success rate)

The combination of error rate + latency provides a more complete picture of canary health.
