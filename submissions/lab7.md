# Lab 7
## Task 1
**Updated `k8s/gateway.yaml`** (Rollout) goes in your fork.

**Paste into `submissions/lab7.md`:**
1. Output of `kubectl argo rollouts version`
```
user@MacBook-Air SRE-Intro % kubectl argo rollouts version
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:11:48Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: darwin/amd64
user@MacBook-Air SRE-Intro % 

```
2. Output of `kubectl argo rollouts get rollout gateway` showing Paused at 20% (during canary)
```
user@MacBook-Air sre % kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/viadimirsoiovev/quickticket-gateway:28a91268965beceb94e2f78aba5f30c50bb6ba6c (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS     AGE    INFO
⟳ gateway                            Rollout     ॥ Paused   3m24s  
├──# revision:2                                                    
│  └──⧉ gateway-5c5d65d9f6           ReplicaSet  ✔ Healthy  31s    canary
│     └──□ gateway-5c5d65d9f6-2bjts  Pod         ✔ Running  31s    ready:1/1
└──# revision:1                                                    
   └──⧉ gateway-6dc79b6df5           ReplicaSet  ✔ Healthy  3m24s  stable
      ├──□ gateway-6dc79b6df5-mhvc6  Pod         ✔ Running  3m23s  ready:1/1
      ├──□ gateway-6dc79b6df5-nxz9r  Pod         ✔ Running  3m23s  ready:1/1
      ├──□ gateway-6dc79b6df5-p8dbb  Pod         ✔ Running  3m23s  ready:1/1
      └──□ gateway-6dc79b6df5-wfhrr  Pod         ✔ Running  3m23s  ready:1/1
user@MacBook-Air sre % 
```
3. Output after `promote` — showing progression to 100%
```
Images:          ghcr.io/viadimirsoiovev/quickticket-gateway:28a91268965beceb94e2f78aba5f30c50bb6ba6c (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE  INFO
⟳ gateway                            Rollout     ✔ Healthy     19m  
├──# revision:3                                                     
│  └──⧉ gateway-775c87484b           ReplicaSet  ✔ Healthy     93s  stable
│     ├──□ gateway-775c87484b-mjqfr  Pod         ✔ Running     91s  ready:1/1
│     ├──□ gateway-775c87484b-msdzc  Pod         ✔ Running     68s  ready:1/1
│     ├──□ gateway-775c87484b-z2wwl  Pod         ✔ Running     68s  ready:1/1
│     ├──□ gateway-775c87484b-fvnb9  Pod         ✔ Running     26s  ready:1/1
│     └──□ gateway-775c87484b-gj2m4  Pod         ✔ Running     26s  ready:1/1
├──# revision:2                                                     
│  └──⧉ gateway-5c5d65d9f6           ReplicaSet  • ScaledDown  16m  
└──# revision:1                                                     
   └──⧉ gateway-6dc79b6df5           ReplicaSet  • ScaledDown  19m
```
4. Output after `abort` — showing instant rollback
```
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/viadimirsoiovev/quickticket-gateway:28a91268965beceb94e2f78aba5f30c50bb6ba6c (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                KIND        STATUS        AGE    INFO
⟳ gateway                           Rollout     ✖ Degraded    11m    
├──# revision:3                                                      
│  └──⧉ gateway-6dc79b6df5          ReplicaSet  • ScaledDown  2m19s  canary
├──# revision:2                                                      
│  └──⧉ gateway-9565d7484           ReplicaSet  ✔ Healthy     9m42s  stable
│     ├──□ gateway-9565d7484-9d8kn  Pod         ✔ Running     9m40s  ready:1/1
│     ├──□ gateway-9565d7484-dxq9t  Pod         ✔ Running     4m6s   ready:1/1
│     ├──□ gateway-9565d7484-xnt94  Pod         ✔ Running     4m6s   ready:1/1
│     ├──□ gateway-9565d7484-9xq8r  Pod         ✔ Running     3m25s  ready:1/1
│     └──□ gateway-9565d7484-2p74f  Pod         ✔ Running     31s    ready:1/1
└──# revision:1                                                      
   └──⧉ gateway-65c685dd8d          ReplicaSet  • ScaledDown  11m    
```
5. Answer: "How long from `abort` to all traffic serving the stable version? Compare with `git revert` rollback from Lab 5."
The abort command takes less than 2 seconds

Comparison with git revert rollback 
reverting a bad deployment using git revert required:

Creating a new Git commit that reverts the changes
Pushing the commit to the repository
Triggering CI/CD pipeline (GitHub Actions, ArgoCD sync, etc.)
Building a new Docker image with the reverted code
Pushing the image to container registry
Kubernetes pulling the new image and rolling out new pods (slow, gradual rollout)
All 5 pods being terminated and recreated with the fixed version

1–5 minutes (depending on build speed, registry upload, and pod startup time)
## Task 2

**Paste into `submissions/lab7.md`:**
- Your multi-step canary strategy YAML
```
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
- Output of `kubectl argo rollouts get rollout gateway --watch` showing at least 3 steps
```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/9
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/viadimirsoiovev/quickticket-gateway:28a91268965beceb94e2f78aba5f30c50bb6ba6c (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ॥ Paused      27m    
├──# revision:4                                                       
│  └──⧉ gateway-869df7b46c           ReplicaSet  ✔ Healthy     66s    canary
│     └──□ gateway-869df7b46c-8fc7v  Pod         ✔ Running     63s    ready:1/1
├──# revision:3                                                       
│  └──⧉ gateway-775c87484b           ReplicaSet  ✔ Healthy     9m26s  stable
│     ├──□ gateway-775c87484b-msdzc  Pod         ✔ Running     9m1s   ready:1/1
│     ├──□ gateway-775c87484b-z2wwl  Pod         ✔ Running     9m1s   ready:1/1
│     ├──□ gateway-775c87484b-fvnb9  Pod         ✔ Running     8m19s  ready:1/1
│     └──□ gateway-775c87484b-gj2m4  Pod         ✔ Running     8m19s  ready:1/1
├──# revision:2                                                       
│  └──⧉ gateway-5c5d65d9f6           ReplicaSet  • ScaledDown  24m    
└──# revision:1                                                       
   └──⧉ gateway-6dc79b6df5           ReplicaSet  • ScaledDown  27m

Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          3/9
  SetWeight:     40
  ActualWeight:  40
Images:          ghcr.io/viadimirsoiovev/quickticket-gateway:28a91268965beceb94e2f78aba5f30c50bb6ba6c (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       2
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ॥ Paused      27m    
├──# revision:4                                                       
│  └──⧉ gateway-869df7b46c           ReplicaSet  ✔ Healthy     87s    canary
│     ├──□ gateway-869df7b46c-8fc7v  Pod         ✔ Running     84s    ready:1/1
│     └──□ gateway-869df7b46c-82d6l  Pod         ✔ Running     13s    ready:1/1
├──# revision:3                                                       
│  └──⧉ gateway-775c87484b           ReplicaSet  ✔ Healthy     9m47s  stable
│     ├──□ gateway-775c87484b-msdzc  Pod         ✔ Running     9m22s  ready:1/1
│     ├──□ gateway-775c87484b-z2wwl  Pod         ✔ Running     9m22s  ready:1/1
│     └──□ gateway-775c87484b-fvnb9  Pod         ✔ Running     8m40s  ready:1/1
├──# revision:2                                                       
│  └──⧉ gateway-5c5d65d9f6           ReplicaSet  • ScaledDown  24m    
└──# revision:1                                                       
   └──⧉ gateway-6dc79b6df5           ReplicaSet  • ScaledDown  27m

Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          5/9
  SetWeight:     60
  ActualWeight:  60
Images:          ghcr.io/viadimirsoiovev/quickticket-gateway:28a91268965beceb94e2f78aba5f30c50bb6ba6c (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ॥ Paused      28m    
├──# revision:4                                                       
│  └──⧉ gateway-869df7b46c           ReplicaSet  ✔ Healthy     2m43s  canary
│     ├──□ gateway-869df7b46c-8fc7v  Pod         ✔ Running     2m40s  ready:1/1
│     ├──□ gateway-869df7b46c-82d6l  Pod         ✔ Running     89s    ready:1/1
│     └──□ gateway-869df7b46c-9mpjp  Pod         ✔ Running     19s    ready:1/1
├──# revision:3                                                       
│  └──⧉ gateway-775c87484b           ReplicaSet  ✔ Healthy     11m    stable
│     ├──□ gateway-775c87484b-msdzc  Pod         ✔ Running     10m    ready:1/1
│     └──□ gateway-775c87484b-fvnb9  Pod         ✔ Running     9m56s  ready:1/1
├──# revision:2                                                       
│  └──⧉ gateway-5c5d65d9f6           ReplicaSet  • ScaledDown  25m    
└──# revision:1                                                       
   └──⧉ gateway-6dc79b6df5           ReplicaSet  • ScaledDown  28m
```
- Dashboard observation during the rollout
I observed the rollout via kubectl argo rollouts get rollout gateway --watch. The canary progressed automatically:

20% → 1 pod updated (pause 60s)
40% → 2 pods updated (pause 60s)
60% → 3 pods updated (pause 60s)
80% → 4 pods updated (pause 30s)
100% → all 5 pods updated → Healthy
Traffic weights matched the set percentages, and the service remained available throughout. No errors occurred, so the rollout completed successfully

- Answer: "At what canary percentage would you want an automated abort? Why?"
I would set automated abort at 20%. At this weight, only 1 pod (out of 5) receives traffic, so any error affects a minimal number of users. Early detection allows instant rollback before the issue spreads to more pods and users

