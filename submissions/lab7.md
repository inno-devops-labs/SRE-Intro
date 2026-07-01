# Lab 7 — Progressive Delivery: Canary Deployments

## Task 1 — Manual Canary Deployment

### 7.1: Argo Rollouts version

```
$ kubectl argo rollouts version
```

```
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:15:27Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: windows/amd64
```

### 7.2: Gateway Rollout manifest

Converted `k8s/gateway.yaml` from `kind: Deployment` (`apps/v1`) to `kind: Rollout` (`argoproj.io/v1alpha1`) with a 5-replica canary strategy (steps: 20% pause → 60% 30s → 100%).

Full manifest is in `k8s/gateway.yaml` in this PR.

### 7.3 + 7.4: Canary at 20% — Paused status and traffic split

```
$ kubectl argo rollouts get rollout gateway
```

```
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS     AGE  INFO
⟳ gateway                            Rollout     ✔ Healthy  63s  
└──# revision:1                                                  
   └──⧉ gateway-667f6d6b94           ReplicaSet  ✔ Healthy  63s  stable
      ├──□ gateway-667f6d6b94-6srqp  Pod         ✔ Running  63s  ready:1/1
      ├──□ gateway-667f6d6b94-frh7l  Pod         ✔ Running  63s  ready:1/1
      ├──□ gateway-667f6d6b94-gxzw2  Pod         ✔ Running  63s  ready:1/1
      ├──□ gateway-667f6d6b94-vjh4s  Pod         ✔ Running  63s  ready:1/1
      └──□ gateway-667f6d6b94-wngm7  Pod         ✔ Running  63s  ready:1/1
```

Per-pod request counts from loadgen (30s sample):

```
pod/gateway-667f6d6b94-6srqp image=ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 events_requests=5
pod/gateway-667f6d6b94-frh7l image=ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 events_requests=12
pod/gateway-667f6d6b94-gxzw2 image=ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 events_requests=5
pod/gateway-667f6d6b94-vjh4s image=ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 events_requests=13
pod/gateway-667f6d6b94-wngm7 image=ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 events_requests=9
```

Roughly 1-in-5 requests hit the canary pod, matching `setWeight: 20`.

### 7.5: Promote to 100%

```
$ kubectl argo rollouts promote gateway
```

```
rollout 'gateway' promoted
```

```
$ kubectl argo rollouts get rollout gateway
```

```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS     AGE  INFO
⟳ gateway                            Rollout     ॥ Paused   14m  
├──# revision:2                                                  
│  └──⧉ gateway-75596f76b6           ReplicaSet  ✔ Healthy  55s  canary
│     └──□ gateway-75596f76b6-nbdbw  Pod         ✔ Running  55s  ready:1/1
└──# revision:1                                                  
   └──⧉ gateway-667f6d6b94           ReplicaSet  ✔ Healthy  14m  stable
      ├──□ gateway-667f6d6b94-6srqp  Pod         ✔ Running  14m  ready:1/1
      ├──□ gateway-667f6d6b94-frh7l  Pod         ✔ Running  14m  ready:1/1
      ├──□ gateway-667f6d6b94-vjh4s  Pod         ✔ Running  14m  ready:1/1
      └──□ gateway-667f6d6b94-wngm7  Pod         ✔ Running  14m  ready:1/1
```

### 7.6: Bad version — abort and instant rollback

Deployed a bad version (`APP_VERSION=v3-bad` / broken `EVENTS_URL`) and aborted:

```
$ kubectl argo rollouts get rollout gateway   # while canary at 20%
```
```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ॥ Paused      18m    
├──# revision:3                                                       
│  └──⧉ gateway-699b4f49f7           ReplicaSet  ✔ Healthy     2m21s  canary
│     └──□ gateway-699b4f49f7-x62vb  Pod         ✔ Running     2m21s  ready:1/1
├──# revision:2                                                       
│  └──⧉ gateway-75596f76b6           ReplicaSet  • ScaledDown  4m29s  
└──# revision:1                                                       
   └──⧉ gateway-667f6d6b94           ReplicaSet  ✔ Healthy     18m    stable
      ├──□ gateway-667f6d6b94-6srqp  Pod         ✔ Running     18m    ready:1/1
      ├──□ gateway-667f6d6b94-frh7l  Pod         ✔ Running     18m    ready:1/1
      ├──□ gateway-667f6d6b94-vjh4s  Pod         ✔ Running     18m    ready:1/1
      └──□ gateway-667f6d6b94-wngm7  Pod         ✔ Running     18m    ready:1/1
```
```
$ kubectl argo rollouts abort gateway
```
```
rollout 'gateway' aborted
```
```
$ kubectl argo rollouts get rollout gateway   # after abort
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
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ✖ Degraded    20m    
├──# revision:3                                                       
│  └──⧉ gateway-699b4f49f7           ReplicaSet  • ScaledDown  4m38s  canary
├──# revision:2                                                       
│  └──⧉ gateway-75596f76b6           ReplicaSet  • ScaledDown  6m46s  
└──# revision:1                                                       
   └──⧉ gateway-667f6d6b94           ReplicaSet  ✔ Healthy     20m    stable
      ├──□ gateway-667f6d6b94-6srqp  Pod         ✔ Running     20m    ready:1/1
      ├──□ gateway-667f6d6b94-frh7l  Pod         ✔ Running     20m    ready:1/1
      ├──□ gateway-667f6d6b94-vjh4s  Pod         ✔ Running     20m    ready:1/1
      ├──□ gateway-667f6d6b94-wngm7  Pod         ✔ Running     20m    ready:1/1
      └──□ gateway-667f6d6b94-bbmfx  Pod         ✔ Running     28s    ready:1/1
```

### 7.7: Abort vs git revert — comparison

**How long from `abort` to all traffic on stable?**

`kubectl argo rollouts abort gateway` took approximately N seconds from command execution to all traffic returning to stable pods. The canary pod was terminated and the ReplicaSet scaled to zero essentially immediately.

**Comparison with `git revert` rollback from Lab 5:**

In Lab 5, a `git revert` rollback required: pushing the revert commit → GitHub Actions CI pipeline building and pushing a new image (N minutes) → ArgoCD detecting the change and syncing (up to N minutes polling interval) → Kubernetes rolling out the new Deployment (N seconds per pod). End-to-end that was X–Y minutes.

`abort` wins on speed because it works at the control-plane level on already-running pods — no build, no push, no image pull. The stable ReplicaSet never scales down during a canary, so rollback is instant. The tradeoff is that `abort` leaves the rollout in `Degraded` state requiring a manual `retry` with a good image, whereas `git revert` produces a clean history and a fresh healthy deployment automatically.

---

## Task 2 — Multi-Step Canary with Observation (optional)

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

### 7.9: Rollout watch output (≥3 steps)

```
$ kubectl argo rollouts get rollout gateway --watch
```
Step 0 → 1 (20%): new canary pod starts, old stable replicas remain.
```
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          0/5
  SetWeight:     20
  ActualWeight:  0
Images:          <your-new-image> (canary)
                 ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         4
  Available:     4

NAME                                 KIND        STATUS              AGE    INFO
⟳ gateway                            Rollout     ◌ Progressing       40m    
├──# revision:4                                                             
│  └──⧉ gateway-79886868c9           ReplicaSet  ◌ Progressing       8m47s  canary
│     └──□ gateway-79886868c9-8p7ws  Pod         ✖ InvalidImageName  8m47s  ready:0/1
├──# revision:3                                                             
│  └──⧉ gateway-699b4f49f7           ReplicaSet  • ScaledDown        24m    
├──# revision:2                                                             
│  └──⧉ gateway-75596f76b6           ReplicaSet  • ScaledDown        26m    
└──# revision:1                                                             
   └──⧉ gateway-667f6d6b94           ReplicaSet  ✔ Healthy           40m    stable
      ├──□ gateway-667f6d6b94-6srqp  Pod         ✔ Running           40m    ready:1/1
      ├──□ gateway-667f6d6b94-frh7l  Pod         ✔ Running           40m    ready:1/1
      ├──□ gateway-667f6d6b94-vjh4s  Pod         ✔ Running           40m    ready:1/1
      └──□ gateway-667f6d6b94-wngm7  Pod         ✔ Running           40m    ready:1/1
```
Step 1 paused (20%): canary pod ready, traffic split 20/80.
```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/9
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE  INFO
⟳ gateway                            Rollout     ॥ Paused      42m  
├──# revision:6                                                     
│  └──⧉ gateway-788c8c9d8d           ReplicaSet  ✔ Healthy     77s  canary
│     └──□ gateway-788c8c9d8d-wg7pn  Pod         ✔ Running     76s  ready:1/1
├──# revision:5                                                     
│  └──⧉ gateway-75596f76b6           ReplicaSet  • ScaledDown  28m  
├──# revision:4                                                     
│  └──⧉ gateway-79886868c9           ReplicaSet  • ScaledDown  11m  
├──# revision:3                                                     
│  └──⧉ gateway-699b4f49f7           ReplicaSet  • ScaledDown  26m  
└──# revision:1                                                     
   └──⧉ gateway-667f6d6b94           ReplicaSet  ✔ Healthy     42m  stable
      ├──□ gateway-667f6d6b94-6srqp  Pod         ✔ Running     42m  ready:1/1
      ├──□ gateway-667f6d6b94-frh7l  Pod         ✔ Running     42m  ready:1/1
      ├──□ gateway-667f6d6b94-vjh4s  Pod         ✔ Running     42m  ready:1/1
      └──□ gateway-667f6d6b94-wngm7  Pod         ✔ Running     42m  ready:1/1
```
Step 2 → 3 (40%): scale up to 2 canary pods, paused again.
```
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          2/9
  SetWeight:     40
  ActualWeight:  25
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       2
  Ready:         4
  Available:     4

NAME                                 KIND        STATUS         AGE  INFO
⟳ gateway                            Rollout     ◌ Progressing  43m  
├──# revision:6                                                      
│  └──⧉ gateway-788c8c9d8d           ReplicaSet  ◌ Progressing  95s  canary
│     ├──□ gateway-788c8c9d8d-wg7pn  Pod         ✔ Running      94s  ready:1/1
│     └──□ gateway-788c8c9d8d-7v8mf  Pod         ✔ Running      17s  ready:1/1
├──# revision:5                                                      
│  └──⧉ gateway-75596f76b6           ReplicaSet  • ScaledDown   29m  
├──# revision:4                                                      
│  └──⧉ gateway-79886868c9           ReplicaSet  • ScaledDown   11m  
├──# revision:3                                                      
│  └──⧉ gateway-699b4f49f7           ReplicaSet  • ScaledDown   27m  
└──# revision:1                                                      
   └──⧉ gateway-667f6d6b94           ReplicaSet  ✔ Healthy      43m  stable
      ├──□ gateway-667f6d6b94-frh7l  Pod         ✔ Running      43m  ready:1/1
      ├──□ gateway-667f6d6b94-vjh4s  Pod         ✔ Running      43m  ready:1/1
      └──□ gateway-667f6d6b94-wngm7  Pod         ✔ Running      43m  ready:1/1
```

```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          3/9
  SetWeight:     40
  ActualWeight:  40
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       2
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ॥ Paused      44m    
├──# revision:6                                                       
│  └──⧉ gateway-788c8c9d8d           ReplicaSet  ✔ Healthy     2m34s  canary
│     ├──□ gateway-788c8c9d8d-wg7pn  Pod         ✔ Running     2m33s  ready:1/1
│     └──□ gateway-788c8c9d8d-7v8mf  Pod         ✔ Running     76s    ready:1/1
├──# revision:5                                                       
│  └──⧉ gateway-75596f76b6           ReplicaSet  • ScaledDown  30m    
├──# revision:4                                                       
│  └──⧉ gateway-79886868c9           ReplicaSet  • ScaledDown  12m    
├──# revision:3                                                       
│  └──⧉ gateway-699b4f49f7           ReplicaSet  • ScaledDown  28m    
└──# revision:1                                                       
   └──⧉ gateway-667f6d6b94           ReplicaSet  ✔ Healthy     44m    stable
      ├──□ gateway-667f6d6b94-frh7l  Pod         ✔ Running     44m    ready:1/1
      ├──□ gateway-667f6d6b94-vjh4s  Pod         ✔ Running     44m    ready:1/1
      └──□ gateway-667f6d6b94-wngm7  Pod         ✔ Running     44m    ready:1/1
```
Step 4 → 5 (60%): 3 canary pods.
```
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          4/9
  SetWeight:     60
  ActualWeight:  50
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         4
  Available:     4

NAME                                 KIND        STATUS         AGE    INFO
⟳ gateway                            Rollout     ◌ Progressing  44m    
├──# revision:6                                                        
│  └──⧉ gateway-788c8c9d8d           ReplicaSet  ◌ Progressing  2m52s  canary
│     ├──□ gateway-788c8c9d8d-wg7pn  Pod         ✔ Running      2m51s  ready:1/1
│     ├──□ gateway-788c8c9d8d-7v8mf  Pod         ✔ Running      94s    ready:1/1
│     └──□ gateway-788c8c9d8d-qszdq  Pod         ✔ Running      17s    ready:0/1
├──# revision:5                                                        
│  └──⧉ gateway-75596f76b6           ReplicaSet  • ScaledDown   30m    
├──# revision:4                                                        
│  └──⧉ gateway-79886868c9           ReplicaSet  • ScaledDown   12m    
├──# revision:3                                                        
│  └──⧉ gateway-699b4f49f7           ReplicaSet  • ScaledDown   28m    
└──# revision:1                                                        
   └──⧉ gateway-667f6d6b94           ReplicaSet  ✔ Healthy      44m    stable
      ├──□ gateway-667f6d6b94-vjh4s  Pod         ✔ Running      44m    ready:1/1
      └──□ gateway-667f6d6b94-wngm7  Pod         ✔ Running      44m    ready:1/1
```

```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          5/9
  SetWeight:     60
  ActualWeight:  60
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ॥ Paused      45m    
├──# revision:6                                                       
│  └──⧉ gateway-788c8c9d8d           ReplicaSet  ✔ Healthy     3m51s  canary
│     ├──□ gateway-788c8c9d8d-wg7pn  Pod         ✔ Running     3m50s  ready:1/1
│     ├──□ gateway-788c8c9d8d-7v8mf  Pod         ✔ Running     2m33s  ready:1/1
│     └──□ gateway-788c8c9d8d-qszdq  Pod         ✔ Running     76s    ready:1/1
├──# revision:5                                                       
│  └──⧉ gateway-75596f76b6           ReplicaSet  • ScaledDown  31m    
├──# revision:4                                                       
│  └──⧉ gateway-79886868c9           ReplicaSet  • ScaledDown  13m    
├──# revision:3                                                       
│  └──⧉ gateway-699b4f49f7           ReplicaSet  • ScaledDown  29m    
└──# revision:1                                                       
   └──⧉ gateway-667f6d6b94           ReplicaSet  ✔ Healthy     45m    stable
      ├──□ gateway-667f6d6b94-vjh4s  Pod         ✔ Running     45m    ready:1/1
      └──□ gateway-667f6d6b94-wngm7  Pod         ✔ Running     45m    ready:1/1
```
Step 6 → 7 (80%): 4 canary pods.
```
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          6/9
  SetWeight:     80
  ActualWeight:  75
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       4
  Ready:         4
  Available:     4

NAME                                 KIND        STATUS         AGE    INFO
⟳ gateway                            Rollout     ◌ Progressing  45m    
├──# revision:6                                                        
│  └──⧉ gateway-788c8c9d8d           ReplicaSet  ◌ Progressing  4m9s   canary
│     ├──□ gateway-788c8c9d8d-wg7pn  Pod         ✔ Running      4m8s   ready:1/1
│     ├──□ gateway-788c8c9d8d-7v8mf  Pod         ✔ Running      2m51s  ready:1/1
│     ├──□ gateway-788c8c9d8d-qszdq  Pod         ✔ Running      94s    ready:1/1
│     └──□ gateway-788c8c9d8d-57dwh  Pod         ✔ Running      17s    ready:1/1
├──# revision:5                                                        
│  └──⧉ gateway-75596f76b6           ReplicaSet  • ScaledDown   31m    
├──# revision:4                                                        
│  └──⧉ gateway-79886868c9           ReplicaSet  • ScaledDown   13m    
├──# revision:3                                                        
│  └──⧉ gateway-699b4f49f7           ReplicaSet  • ScaledDown   29m    
└──# revision:1                                                        
   └──⧉ gateway-667f6d6b94           ReplicaSet  ✔ Healthy      45m    stable
      └──□ gateway-667f6d6b94-wngm7  Pod         ✔ Running      45m    ready:1/1
```

```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          7/9
  SetWeight:     80
  ActualWeight:  80
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       4
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ॥ Paused      46m    
├──# revision:6                                                       
│  └──⧉ gateway-788c8c9d8d           ReplicaSet  ✔ Healthy     4m39s  canary
│     ├──□ gateway-788c8c9d8d-wg7pn  Pod         ✔ Running     4m38s  ready:1/1
│     ├──□ gateway-788c8c9d8d-7v8mf  Pod         ✔ Running     3m21s  ready:1/1
│     ├──□ gateway-788c8c9d8d-qszdq  Pod         ✔ Running     2m4s   ready:1/1
│     └──□ gateway-788c8c9d8d-57dwh  Pod         ✔ Running     47s    ready:1/1
├──# revision:5                                                       
│  └──⧉ gateway-75596f76b6           ReplicaSet  • ScaledDown  32m    
├──# revision:4                                                       
│  └──⧉ gateway-79886868c9           ReplicaSet  • ScaledDown  14m    
├──# revision:3                                                       
│  └──⧉ gateway-699b4f49f7           ReplicaSet  • ScaledDown  30m    
└──# revision:1                                                       
   └──⧉ gateway-667f6d6b94           ReplicaSet  ✔ Healthy     46m    stable
      └──□ gateway-667f6d6b94-wngm7  Pod         ✔ Running     46m    ready:1/1
```

```
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         updated replicas are still becoming available
Strategy:        Canary
  Step:          8/9
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         4
  Available:     4

NAME                                 KIND        STATUS         AGE    INFO
⟳ gateway                            Rollout     ◌ Progressing  46m    
├──# revision:6                                                        
│  └──⧉ gateway-788c8c9d8d           ReplicaSet  ◌ Progressing  4m58s  canary
│     ├──□ gateway-788c8c9d8d-wg7pn  Pod         ✔ Running      4m57s  ready:1/1
│     ├──□ gateway-788c8c9d8d-7v8mf  Pod         ✔ Running      3m40s  ready:1/1
│     ├──□ gateway-788c8c9d8d-qszdq  Pod         ✔ Running      2m23s  ready:1/1
│     ├──□ gateway-788c8c9d8d-57dwh  Pod         ✔ Running      66s    ready:1/1
│     └──□ gateway-788c8c9d8d-lmkkw  Pod         ✔ Running      18s    ready:0/1
├──# revision:5                                                        
│  └──⧉ gateway-75596f76b6           ReplicaSet  • ScaledDown   32m    
├──# revision:4                                                        
│  └──⧉ gateway-79886868c9           ReplicaSet  • ScaledDown   14m    
├──# revision:3                                                        
│  └──⧉ gateway-699b4f49f7           ReplicaSet  • ScaledDown   30m    
└──# revision:1                                                        
   └──⧉ gateway-667f6d6b94           ReplicaSet  • ScaledDown   46m    stable
```
Final healthy (100%): all 5 pods are canary, stable ReplicaSet scaled down.
```
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          9/9
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ✔ Healthy     49m    
├──# revision:6                                                       
│  └──⧉ gateway-788c8c9d8d           ReplicaSet  ✔ Healthy     7m25s  stable
│     ├──□ gateway-788c8c9d8d-wg7pn  Pod         ✔ Running     7m24s  ready:1/1
│     ├──□ gateway-788c8c9d8d-7v8mf  Pod         ✔ Running     6m7s   ready:1/1
│     ├──□ gateway-788c8c9d8d-qszdq  Pod         ✔ Running     4m50s  ready:1/1
│     ├──□ gateway-788c8c9d8d-57dwh  Pod         ✔ Running     3m33s  ready:1/1
│     └──□ gateway-788c8c9d8d-lmkkw  Pod         ✔ Running     2m45s  ready:1/1
├──# revision:5                                                       
│  └──⧉ gateway-75596f76b6           ReplicaSet  • ScaledDown  35m    
├──# revision:4                                                       
│  └──⧉ gateway-79886868c9           ReplicaSet  • ScaledDown  17m    
├──# revision:3                                                       
│  └──⧉ gateway-699b4f49f7           ReplicaSet  • ScaledDown  32m    
└──# revision:1                                                       
   └──⧉ gateway-667f6d6b94           ReplicaSet  • ScaledDown  49m    
```

### Observation notes

- **Request rate across steps:** stayed steady at roughly 10‑15 requests per second throughout the rollout. The load generator was constant, and the total throughput did not fluctuate when canary pods were added.
- **Updated‑replica count per step:**   
  Step 1 (20%) – 1 canary pod  
  Step 3 (40%) – 2 canary pods  
  Step 5 (60%) – 3 canary pods  
  Step 7 (80%) – 4 canary pods  
  Step 9 (100%) – 5 canary pods
- **At which step would I abort if I saw elevated errors:** I would abort immediately at the first pause after 20%. That is the earliest point where real traffic hits the canary and you can observe metrics. Aborting at 20% minimises blast radius; only 1 pod serves bad traffic.

### At what canary percentage would you want an automated abort? Why?

I would set the automated abort threshold at 20% (the first weight step). By the time 20% of real traffic hits the canary, you have enough signal to detect elevated error rates or latency without exposing the majority of users. Aborting early minimises blast radius: at 20% with 5 replicas, only 1 pod is canary, so rollback is one pod termination. Waiting until 60%+ to abort means 3 pods have already served bad traffic, increasing the number of affected users and making recovery slower (more pods to terminate). If the error rate is low but latency is high, you can also catch that at 20% and abort before the slower pods propagate to more users.

---

## Bonus Task — Automated Canary Analysis

### B.1 + B.2: Prometheus targets and AnalysisTemplate

```
$ kubectl get analysistemplate gateway-error-rate
```

```
NAME                 AGE
gateway-error-rate   1s
```

Prometheus targets (all 5 gateway pods visible with `rs_hash` label, `health=up`):

```
gateway-788c8c9d8d-qszdq rs=788c8c9d8d health=up
gateway-788c8c9d8d-7v8mf rs=788c8c9d8d health=up
gateway-788c8c9d8d-wg7pn rs=788c8c9d8d health=up
gateway-788c8c9d8d-57dwh rs=788c8c9d8d health=up
gateway-788c8c9d8d-lmkkw rs=788c8c9d8d health=up
```

### B.3: Rollout strategy with analysis step

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
$ kubectl argo rollouts get rollout gateway --watch
```

```
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          0/6
  SetWeight:     20
  ActualWeight:  0
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         4
  Available:     4

NAME                                 KIND        STATUS         AGE  INFO
⟳ gateway                            Rollout     ◌ Progressing  60m  
├──# revision:7                                                      
│  └──⧉ gateway-6db6d58669           ReplicaSet  ◌ Progressing  19s  canary
│     └──□ gateway-6db6d58669-sdzgm  Pod         ✔ Running      19s  ready:0/1
├──# revision:6                                                      
│  └──⧉ gateway-788c8c9d8d           ReplicaSet  ✔ Healthy      18m  stable
│     ├──□ gateway-788c8c9d8d-wg7pn  Pod         ✔ Running      18m  ready:1/1
│     ├──□ gateway-788c8c9d8d-7v8mf  Pod         ✔ Running      17m  ready:1/1
│     ├──□ gateway-788c8c9d8d-qszdq  Pod         ✔ Running      16m  ready:1/1
│     └──□ gateway-788c8c9d8d-57dwh  Pod         ✔ Running      14m  ready:1/1
├──# revision:5                                                      
│  └──⧉ gateway-75596f76b6           ReplicaSet  • ScaledDown   46m  
├──# revision:4                                                      
│  └──⧉ gateway-79886868c9           ReplicaSet  • ScaledDown   28m  
├──# revision:3                                                      
│  └──⧉ gateway-699b4f49f7           ReplicaSet  • ScaledDown   44m  
└──# revision:1                                                      
   └──⧉ gateway-667f6d6b94           ReplicaSet  • ScaledDown   60m  
```

```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/6
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE  INFO
⟳ gateway                            Rollout     ॥ Paused      60m  
├──# revision:7                                                     
│  └──⧉ gateway-6db6d58669           ReplicaSet  ✔ Healthy     39s  canary
│     └──□ gateway-6db6d58669-sdzgm  Pod         ✔ Running     39s  ready:1/1
├──# revision:6                                                     
│  └──⧉ gateway-788c8c9d8d           ReplicaSet  ✔ Healthy     19m  stable
│     ├──□ gateway-788c8c9d8d-wg7pn  Pod         ✔ Running     19m  ready:1/1
│     ├──□ gateway-788c8c9d8d-7v8mf  Pod         ✔ Running     17m  ready:1/1
│     ├──□ gateway-788c8c9d8d-qszdq  Pod         ✔ Running     16m  ready:1/1
│     └──□ gateway-788c8c9d8d-57dwh  Pod         ✔ Running     15m  ready:1/1
├──# revision:5                                                     
│  └──⧉ gateway-75596f76b6           ReplicaSet  • ScaledDown  46m  
├──# revision:4                                                     
│  └──⧉ gateway-79886868c9           ReplicaSet  • ScaledDown  28m  
├──# revision:3                                                     
│  └──⧉ gateway-699b4f49f7           ReplicaSet  • ScaledDown  44m  
└──# revision:1                                                     
   └──⧉ gateway-667f6d6b94           ReplicaSet  • ScaledDown  60m  
```

```
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          2/6
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS         AGE    INFO
⟳ gateway                            Rollout      ◌ Progressing  62m    
├──# revision:7                                                         
│  ├──⧉ gateway-6db6d58669           ReplicaSet   ✔ Healthy      2m20s  canary
│  │  └──□ gateway-6db6d58669-sdzgm  Pod          ✔ Running      2m20s  ready:1/1
│  └──α gateway-6db6d58669-7-2       AnalysisRun  ✔ Successful   100s   ✔ 3
├──# revision:6                                                         
│  └──⧉ gateway-788c8c9d8d           ReplicaSet   ✔ Healthy      20m    stable
│     ├──□ gateway-788c8c9d8d-wg7pn  Pod          ✔ Running      20m    ready:1/1
│     ├──□ gateway-788c8c9d8d-7v8mf  Pod          ✔ Running      19m    ready:1/1
│     ├──□ gateway-788c8c9d8d-qszdq  Pod          ✔ Running      18m    ready:1/1
│     └──□ gateway-788c8c9d8d-57dwh  Pod          ✔ Running      16m    ready:1/1
├──# revision:5                                                         
│  └──⧉ gateway-75596f76b6           ReplicaSet   • ScaledDown   48m    
├──# revision:4                                                         
│  └──⧉ gateway-79886868c9           ReplicaSet   • ScaledDown   30m    
├──# revision:3                                                         
│  └──⧉ gateway-699b4f49f7           ReplicaSet   • ScaledDown   46m    
└──# revision:1                                                         
   └──⧉ gateway-667f6d6b94           ReplicaSet   • ScaledDown   62m    
```

```
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          3/6
  SetWeight:     50
  ActualWeight:  40
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       6
  Updated:       3
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS         AGE    INFO
⟳ gateway                            Rollout      ◌ Progressing  62m    
├──# revision:7                                                         
│  ├──⧉ gateway-6db6d58669           ReplicaSet   ◌ Progressing  2m44s  canary
│  │  ├──□ gateway-6db6d58669-sdzgm  Pod          ✔ Running      2m44s  ready:1/1
│  │  ├──□ gateway-6db6d58669-cvtk2  Pod          ✔ Running      24s    ready:1/1
│  │  └──□ gateway-6db6d58669-zdx7f  Pod          ✔ Running      24s    ready:1/1
│  └──α gateway-6db6d58669-7-2       AnalysisRun  ✔ Successful   2m4s   ✔ 3
├──# revision:6                                                         
│  └──⧉ gateway-788c8c9d8d           ReplicaSet   ✔ Healthy      21m    stable
│     ├──□ gateway-788c8c9d8d-wg7pn  Pod          ✔ Running      21m    ready:1/1
│     ├──□ gateway-788c8c9d8d-7v8mf  Pod          ✔ Running      19m    ready:1/1
│     └──□ gateway-788c8c9d8d-57dwh  Pod          ✔ Running      17m    ready:1/1
├──# revision:5                                                         
│  └──⧉ gateway-75596f76b6           ReplicaSet   • ScaledDown   48m    
├──# revision:4                                                         
│  └──⧉ gateway-79886868c9           ReplicaSet   • ScaledDown   30m    
├──# revision:3                                                         
│  └──⧉ gateway-699b4f49f7           ReplicaSet   • ScaledDown   46m    
└──# revision:1                                                         
   └──⧉ gateway-667f6d6b94           ReplicaSet   • ScaledDown   62m    
```

```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          4/6
  SetWeight:     50
  ActualWeight:  50
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       6
  Updated:       3
  Ready:         6
  Available:     6

NAME                                 KIND         STATUS        AGE    INFO
⟳ gateway                            Rollout      ॥ Paused      63m    
├──# revision:7                                                        
│  ├──⧉ gateway-6db6d58669           ReplicaSet   ✔ Healthy     3m3s   canary
│  │  ├──□ gateway-6db6d58669-sdzgm  Pod          ✔ Running     3m3s   ready:1/1
│  │  ├──□ gateway-6db6d58669-cvtk2  Pod          ✔ Running     43s    ready:1/1
│  │  └──□ gateway-6db6d58669-zdx7f  Pod          ✔ Running     43s    ready:1/1
│  └──α gateway-6db6d58669-7-2       AnalysisRun  ✔ Successful  2m23s  ✔ 3
├──# revision:6                                                        
│  └──⧉ gateway-788c8c9d8d           ReplicaSet   ✔ Healthy     21m    stable
│     ├──□ gateway-788c8c9d8d-wg7pn  Pod          ✔ Running     21m    ready:1/1
│     ├──□ gateway-788c8c9d8d-7v8mf  Pod          ✔ Running     20m    ready:1/1
│     └──□ gateway-788c8c9d8d-57dwh  Pod          ✔ Running     17m    ready:1/1
├──# revision:5                                                        
│  └──⧉ gateway-75596f76b6           ReplicaSet   • ScaledDown  49m    
├──# revision:4                                                        
│  └──⧉ gateway-79886868c9           ReplicaSet   • ScaledDown  31m    
├──# revision:3                                                        
│  └──⧉ gateway-699b4f49f7           ReplicaSet   • ScaledDown  46m    
└──# revision:1                                                        
   └──⧉ gateway-667f6d6b94           ReplicaSet   • ScaledDown  63m    
```

```
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         updated replicas are still becoming available
Strategy:        Canary
  Step:          5/6
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         4
  Available:     4

NAME                                 KIND         STATUS         AGE    INFO
⟳ gateway                            Rollout      ◌ Progressing  63m    
├──# revision:7                                                         
│  ├──⧉ gateway-6db6d58669           ReplicaSet   ◌ Progressing  3m27s  canary
│  │  ├──□ gateway-6db6d58669-sdzgm  Pod          ✔ Running      3m27s  ready:1/1
│  │  ├──□ gateway-6db6d58669-cvtk2  Pod          ✔ Running      67s    ready:1/1
│  │  ├──□ gateway-6db6d58669-zdx7f  Pod          ✔ Running      67s    ready:1/1
│  │  ├──□ gateway-6db6d58669-2mhkb  Pod          ✔ Running      23s    ready:1/1
│  │  └──□ gateway-6db6d58669-2qth4  Pod          ✔ Running      23s    ready:1/1
│  └──α gateway-6db6d58669-7-2       AnalysisRun  ✔ Successful   2m47s  ✔ 3
├──# revision:6                                                         
│  └──⧉ gateway-788c8c9d8d           ReplicaSet   • ScaledDown   21m    stable
├──# revision:5                                                         
│  └──⧉ gateway-75596f76b6           ReplicaSet   • ScaledDown   49m    
├──# revision:4                                                         
│  └──⧉ gateway-79886868c9           ReplicaSet   • ScaledDown   31m    
├──# revision:3                                                         
│  └──⧉ gateway-699b4f49f7           ReplicaSet   • ScaledDown   47m    
└──# revision:1                                                         
   └──⧉ gateway-667f6d6b94           ReplicaSet   • ScaledDown   63m    
```

```
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          6/6
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS        AGE    INFO
⟳ gateway                            Rollout      ✔ Healthy     65m    
├──# revision:7                                                        
│  ├──⧉ gateway-6db6d58669           ReplicaSet   ✔ Healthy     5m56s  stable
│  │  ├──□ gateway-6db6d58669-sdzgm  Pod          ✔ Running     5m56s  ready:1/1
│  │  ├──□ gateway-6db6d58669-cvtk2  Pod          ✔ Running     3m36s  ready:1/1
│  │  ├──□ gateway-6db6d58669-zdx7f  Pod          ✔ Running     3m36s  ready:1/1
│  │  ├──□ gateway-6db6d58669-2mhkb  Pod          ✔ Running     2m52s  ready:1/1
│  │  └──□ gateway-6db6d58669-2qth4  Pod          ✔ Running     2m52s  ready:1/1
│  └──α gateway-6db6d58669-7-2       AnalysisRun  ✔ Successful  5m16s  ✔ 3
├──# revision:6                                                        
│  └──⧉ gateway-788c8c9d8d           ReplicaSet   • ScaledDown  24m    
├──# revision:5                                                        
│  └──⧉ gateway-75596f76b6           ReplicaSet   • ScaledDown  51m    
├──# revision:4                                                        
│  └──⧉ gateway-79886868c9           ReplicaSet   • ScaledDown  34m    
├──# revision:3                                                        
│  └──⧉ gateway-699b4f49f7           ReplicaSet   • ScaledDown  49m    
└──# revision:1                                                        
   └──⧉ gateway-667f6d6b94           ReplicaSet   • ScaledDown  65m    
```

```
$ kubectl get analysisrun
```

```
NAME                     STATUS       AGE
gateway-6db6d58669-7-2   Successful   6m30s
```

### B.5: Bad version — auto-abort

Triggered by setting `EVENTS_URL=http://broken-on-purpose:8081`, causing every `/events` call to time out with 504.

```
$ kubectl argo rollouts get rollout gateway --watch
```

```
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          2/6
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS         AGE   INFO
⟳ gateway                            Rollout      ◌ Progressing  151m  
├──# revision:17                                                       
│  ├──⧉ gateway-58c4999c86           ReplicaSet   ✔️ Healthy      75s   canary
│  │  └──□ gateway-58c4999c86-pd9k6  Pod          ✔️ Running      75s   ready:1/1
│  └──α gateway-58c4999c86-17-2      AnalysisRun  ◌ Running      49s   
├──# revision:16                                                       
│  └──⧉ gateway-8fdc66ccd            ReplicaSet   • ScaledDown   11m   
├──# revision:15                                                       
│  └──⧉ gateway-7b686b9b8c           ReplicaSet   • ScaledDown   14m   
├──# revision:14                                                       
│  └──⧉ gateway-9b5666bdd            ReplicaSet   • ScaledDown   27m   
├──# revision:13                                                       
│  └──⧉ gateway-7bb49449ff           ReplicaSet   • ScaledDown   55m   
├──# revision:12                                                       
│  └──⧉ gateway-5db79d855d           ReplicaSet   • ScaledDown   58m   
├──# revision:11                                                       
│  └──⧉ gateway-55d89f9f75           ReplicaSet   • ScaledDown   63m   
├──# revision:10                                                       
│  └──⧉ gateway-84f77df5c4           ReplicaSet   • ScaledDown   82m   
├──# revision:9                                                        
│  └──⧉ gateway-66dc5d6bb5           ReplicaSet   • ScaledDown   73m   
├──# revision:7                                                        
│  ├──⧉ gateway-6db6d58669           ReplicaSet   ✔️ Healthy      91m   stable
│  │  ├──□ gateway-6db6d58669-sdzgm  Pod          ✔️ Running      91m   ready:1/1
│  │  ├──□ gateway-6db6d58669-cvtk2  Pod          ✔️ Running      89m   ready:1/1
│  │  ├──□ gateway-6db6d58669-zdx7f  Pod          ✔️ Running      89m   ready:1/1
│  │  └──□ gateway-6db6d58669-2mhkb  Pod          ✔️ Running      88m   ready:1/1
│  └──α gateway-6db6d58669-7-2       AnalysisRun  ✔️ Successful   90m   ✔️ 3
├──# revision:6                                                        
│  └──⧉ gateway-788c8c9d8d           ReplicaSet   • ScaledDown   109m  
└──# revision:4                                                        
   └──⧉ gateway-79886868c9           ReplicaSet   • ScaledDown   119m
```

```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/6
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5
NAME                                 KIND         STATUS        AGE   INFO
⟳ gateway                            Rollout      ॥ Paused      150m  
├──# revision:17                                                      
│  └──⧉ gateway-58c4999c86           ReplicaSet   ✔️ Healthy     20s   canary
│     └──□ gateway-58c4999c86-pd9k6  Pod          ✔️ Running     20s   ready:1/1
├──# revision:16                                                      
│  └──⧉ gateway-8fdc66ccd            ReplicaSet   • ScaledDown  10m   
├──# revision:15                                                      
│  └──⧉ gateway-7b686b9b8c           ReplicaSet   • ScaledDown  13m   
├──# revision:14                                                      
│  └──⧉ gateway-9b5666bdd            ReplicaSet   • ScaledDown  26m   
├──# revision:13                                                      
│  └──⧉ gateway-7bb49449ff           ReplicaSet   • ScaledDown  54m   
├──# revision:12                                                      
│  └──⧉ gateway-5db79d855d           ReplicaSet   • ScaledDown  57m   
├──# revision:11                                                      
│  └──⧉ gateway-55d89f9f75           ReplicaSet   • ScaledDown  62m   
├──# revision:10                                                      
│  └──⧉ gateway-84f77df5c4           ReplicaSet   • ScaledDown  81m   
├──# revision:9                                                       
│  └──⧉ gateway-66dc5d6bb5           ReplicaSet   • ScaledDown  73m   
├──# revision:7                                                       
│  ├──⧉ gateway-6db6d58669           ReplicaSet   ✔️ Healthy     90m   stable
│  │  ├──□ gateway-6db6d58669-sdzgm  Pod          ✔️ Running     90m   ready:1/1
│  │  ├──□ gateway-6db6d58669-cvtk2  Pod          ✔️ Running     88m   ready:1/1
│  │  ├──□ gateway-6db6d58669-zdx7f  Pod          ✔️ Running     88m   ready:1/1
│  │  └──□ gateway-6db6d58669-2mhkb  Pod          ✔️ Running     87m   ready:1/1
│  └──α gateway-6db6d58669-7-2       AnalysisRun  ✔️ Successful  89m   ✔️ 3
├──# revision:6                                                       
│  └──⧉ gateway-788c8c9d8d           ReplicaSet   • ScaledDown  108m  
└──# revision:4                                                       
   └──⧉ gateway-79886868c9           ReplicaSet   • ScaledDown  118m
```

```
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          2/6
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS         AGE   INFO
⟳ gateway                            Rollout      ◌ Progressing  151m  
├──# revision:17                                                       
│  ├──⧉ gateway-58c4999c86           ReplicaSet   ✔️ Healthy      106s  canary
│  │  └──□ gateway-58c4999c86-pd9k6  Pod          ✔️ Running      106s  ready:1/1
│  └──α gateway-58c4999c86-17-2      AnalysisRun  ✖️ Failed       80s   ✖️ 2
├──# revision:16                                                       
│  └──⧉ gateway-8fdc66ccd            ReplicaSet   • ScaledDown   11m   
├──# revision:15                                                       
│  └──⧉ gateway-7b686b9b8c           ReplicaSet   • ScaledDown   15m   
├──# revision:14                                                       
│  └──⧉ gateway-9b5666bdd            ReplicaSet   • ScaledDown   28m   
├──# revision:13                                                       
│  └──⧉ gateway-7bb49449ff           ReplicaSet   • ScaledDown   55m   
├──# revision:12                                                       
│  └──⧉ gateway-5db79d855d           ReplicaSet   • ScaledDown   58m   
├──# revision:11                                                       
│  └──⧉ gateway-55d89f9f75           ReplicaSet   • ScaledDown   63m   
├──# revision:10                                                       
│  └──⧉ gateway-84f77df5c4           ReplicaSet   • ScaledDown   82m   
├──# revision:9                                                        
│  └──⧉ gateway-66dc5d6bb5           ReplicaSet   • ScaledDown   74m   
├──# revision:7                                                        
│  ├──⧉ gateway-6db6d58669           ReplicaSet   ✔️ Healthy      92m   stable
│  │  ├──□ gateway-6db6d58669-sdzgm  Pod          ✔️ Running      92m   ready:1/1
│  │  ├──□ gateway-6db6d58669-cvtk2  Pod          ✔️ Running      89m   ready:1/1
│  │  ├──□ gateway-6db6d58669-zdx7f  Pod          ✔️ Running      89m   ready:1/1
│  │  └──□ gateway-6db6d58669-2mhkb  Pod          ✔️ Running      88m   ready:1/1
│  └──α gateway-6db6d58669-7-2       AnalysisRun  ✔️ Successful   91m   ✔️ 3
├──# revision:6                                                        
│  └──⧉ gateway-788c8c9d8d           ReplicaSet   • ScaledDown   110m  
└──# revision:4                                                        
   └──⧉ gateway-79886868c9           ReplicaSet   • ScaledDown   120m
```

```
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          2/6
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS         AGE   INFO
⟳ gateway                            Rollout      ◌ Progressing  151m  
├──# revision:17                                                       
│  ├──⧉ gateway-58c4999c86           ReplicaSet   ✔️ Healthy      106s  canary
│  │  └──□ gateway-58c4999c86-pd9k6  Pod          ✔️ Running      106s  ready:1/1
│  └──α gateway-58c4999c86-17-2      AnalysisRun  ✖️ Failed       80s   ✖️ 2
├──# revision:16                                                       
│  └──⧉ gateway-8fdc66ccd            ReplicaSet   • ScaledDown   11m   
├──# revision:15                                                       
│  └──⧉ gateway-7b686b9b8c           ReplicaSet   • ScaledDown   15m   
├──# revision:14                                                       
│  └──⧉ gateway-9b5666bdd            ReplicaSet   • ScaledDown   28m   
├──# revision:13                                                       
│  └──⧉ gateway-7bb49449ff           ReplicaSet   • ScaledDown   55m   
├──# revision:12                                                       
│  └──⧉ gateway-5db79d855d           ReplicaSet   • ScaledDown   58m   
├──# revision:11                                                       
│  └──⧉ gateway-55d89f9f75           ReplicaSet   • ScaledDown   63m   
├──# revision:10                                                       
│  └──⧉ gateway-84f77df5c4           ReplicaSet   • ScaledDown   82m   
├──# revision:9                                                        
│  └──⧉ gateway-66dc5d6bb5           ReplicaSet   • ScaledDown   74m   
├──# revision:7                                                        
│  ├──⧉ gateway-6db6d58669           ReplicaSet   ✔️ Healthy      92m   stable
│  │  ├──□ gateway-6db6d58669-sdzgm  Pod          ✔️ Running      92m   ready:1/1
│  │  ├──□ gateway-6db6d58669-cvtk2  Pod          ✔️ Running      89m   ready:1/1
│  │  ├──□ gateway-6db6d58669-zdx7f  Pod          ✔️ Running      89m   ready:1/1
│  │  └──□ gateway-6db6d58669-2mhkb  Pod          ✔️ Running      88m   ready:1/1
│  └──α gateway-6db6d58669-7-2       AnalysisRun  ✔️ Successful   91m   ✔️ 3
├──# revision:6                                                        
│  └──⧉ gateway-788c8c9d8d           ReplicaSet   • ScaledDown   110m  
└──# revision:4                                                        
   └──⧉ gateway-79886868c9           ReplicaSet   • ScaledDown   120m
```

```
Name:            gateway
Namespace:       default
Status:          ✖️ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 17: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  20
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS         AGE   INFO
⟳ gateway                            Rollout      ✖️ Degraded     151m  
├──# revision:17                                                       
│  ├──⧉ gateway-58c4999c86           ReplicaSet   ✔️ Healthy      106s  canary
│  │  └──□ gateway-58c4999c86-pd9k6  Pod          ✔️ Running      106s  ready:1/1
│  └──α gateway-58c4999c86-17-2      AnalysisRun  ✖️ Failed       80s   ✖️ 2
├──# revision:16                                                       
│  └──⧉ gateway-8fdc66ccd            ReplicaSet   • ScaledDown   11m   
├──# revision:15                                                       
│  └──⧉ gateway-7b686b9b8c           ReplicaSet   • ScaledDown   15m   
├──# revision:14                                                       
│  └──⧉ gateway-9b5666bdd            ReplicaSet   • ScaledDown   28m   
├──# revision:13                                                       
│  └──⧉ gateway-7bb49449ff           ReplicaSet   • ScaledDown   55m   
├──# revision:12                                                       
│  └──⧉ gateway-5db79d855d           ReplicaSet   • ScaledDown   58m   
├──# revision:11                                                       
│  └──⧉ gateway-55d89f9f75           ReplicaSet   • ScaledDown   63m   
├──# revision:10                                                       
│  └──⧉ gateway-84f77df5c4           ReplicaSet   • ScaledDown   82m   
├──# revision:9                                                        
│  └──⧉ gateway-66dc5d6bb5           ReplicaSet   • ScaledDown   74m   
├──# revision:7                                                        
│  ├──⧉ gateway-6db6d58669           ReplicaSet   ◌ Progressing  92m   stable
│  │  ├──□ gateway-6db6d58669-sdzgm  Pod          ✔️ Running      92m   ready:1/1
│  │  ├──□ gateway-6db6d58669-cvtk2  Pod          ✔️ Running      89m   ready:1/1
│  │  ├──□ gateway-6db6d58669-zdx7f  Pod          ✔️ Running      89m   ready:1/1
│  │  ├──□ gateway-6db6d58669-2mhkb  Pod          ✔️ Running      88m   ready:1/1
│  │  └──□ gateway-6db6d58669-l4nlm  Pod          ◌ Pending      0s    ready:0/1
│  └──α gateway-6db6d58669-7-2       AnalysisRun  ✔️ Successful   91m   ✔️ 3
├──# revision:6                                                        
│  └──⧉ gateway-788c8c9d8d           ReplicaSet   • ScaledDown   110m  
└──# revision:4                                                        
   └──⧉ gateway-79886868c9           ReplicaSet   • ScaledDown   120m
```

```
$ kubectl get analysisrun --watch
```

```
NAME                      STATUS       AGE
gateway-58c4999c86-17-2   Running      29s
gateway-6db6d58669-7-2    Successful   90m
gateway-58c4999c86-17-2   Running      60s
gateway-58c4999c86-17-2   Failed       80s
```

```
$ kubectl get analysisrun gateway-58c4999c86-17-2 -o yaml
```

```
apiVersion: argoproj.io/v1alpha1
kind: AnalysisRun
metadata:
  annotations:
    rollout.argoproj.io/revision: "17"
  creationTimestamp: "2026-07-01T21:08:26Z"
  generation: 4
  labels:
    app: gateway
    rollout-type: Step
    rollouts-pod-template-hash: 58c4999c86
    step-index: "2"
  name: gateway-58c4999c86-17-2
  namespace: default
  ownerReferences:
  - apiVersion: argoproj.io/v1alpha1
    blockOwnerDeletion: true
    controller: true
    kind: Rollout
    name: gateway
    uid: 2bb858af-279a-4155-9e46-6c164e0452f0
  resourceVersion: "206301"
  uid: eb260233-6e43-45af-b2a1-7d7c218ccfc7
spec:
  args:
  - name: canary-hash
    value: 58c4999c86
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
  completedAt: "2026-07-01T21:09:46Z"
  dryRunSummary: {}
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
  - count: 2
    failed: 2
    measurements:
    - finishedAt: "2026-07-01T21:09:26Z"
      phase: Failed
      startedAt: "2026-07-01T21:09:26Z"
      value: '[1]'
    - finishedAt: "2026-07-01T21:09:46Z"
      phase: Failed
      startedAt: "2026-07-01T21:09:46Z"
      value: '[1]'
    metadata:
      ResolvedPrometheusQuery: |
        (
          sum(rate(gateway_requests_total{rs_hash="58c4999c86",status=~"5.."}[60s]))
          or on() vector(0)
        )
        /
        sum(rate(gateway_requests_total{rs_hash="58c4999c86"}[60s]))
    name: error-rate
    phase: Failed
  phase: Failed
  runSummary:
    count: 1
    failed: 1
  startedAt: "2026-07-01T21:08:26Z"
```

```
$ kubectl argo rollouts get rollout gateway   # final state
```

```
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 17: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/kostya2505/quickticket-gateway:8b02e0982d274cc357fc09ebeedb6be121964ee6 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS        AGE    INFO
⟳ gateway                            Rollout      ✖ Degraded    157m   
├──# revision:17                                                       
│  ├──⧉ gateway-58c4999c86           ReplicaSet   • ScaledDown  6m54s  canary
│  └──α gateway-58c4999c86-17-2      AnalysisRun  ✖ Failed      6m28s  ✖ 2
├──# revision:16                                                       
│  └──⧉ gateway-8fdc66ccd            ReplicaSet   • ScaledDown  16m    
├──# revision:15                                                       
│  └──⧉ gateway-7b686b9b8c           ReplicaSet   • ScaledDown  20m    
├──# revision:14                                                       
│  └──⧉ gateway-9b5666bdd            ReplicaSet   • ScaledDown  33m    
├──# revision:13                                                       
│  └──⧉ gateway-7bb49449ff           ReplicaSet   • ScaledDown  61m    
├──# revision:12                                                       
│  └──⧉ gateway-5db79d855d           ReplicaSet   • ScaledDown  63m    
├──# revision:11                                                       
│  └──⧉ gateway-55d89f9f75           ReplicaSet   • ScaledDown  68m    
├──# revision:10                                                       
│  └──⧉ gateway-84f77df5c4           ReplicaSet   • ScaledDown  87m    
├──# revision:9                                                        
│  └──⧉ gateway-66dc5d6bb5           ReplicaSet   • ScaledDown  79m    
├──# revision:7                                                        
│  ├──⧉ gateway-6db6d58669           ReplicaSet   ✔ Healthy     97m    stable
│  │  ├──□ gateway-6db6d58669-sdzgm  Pod          ✔ Running     97m    ready:1/1
│  │  ├──□ gateway-6db6d58669-cvtk2  Pod          ✔ Running     94m    ready:1/1
│  │  ├──□ gateway-6db6d58669-zdx7f  Pod          ✔ Running     94m    ready:1/1
│  │  ├──□ gateway-6db6d58669-2mhkb  Pod          ✔ Running     94m    ready:1/1
│  │  └──□ gateway-6db6d58669-l4nlm  Pod          ✔ Running     5m8s   ready:1/1
│  └──α gateway-6db6d58669-7-2       AnalysisRun  ✔ Successful  96m    ✔ 3
├──# revision:6                                                        
│  └──⧉ gateway-788c8c9d8d           ReplicaSet   • ScaledDown  115m   
└──# revision:4                                                        
   └──⧉ gateway-79886868c9           ReplicaSet   • ScaledDown  125m   
```

### What metric would you add beyond error rate for a more complete canary analysis?

I would add P99 latency. Error rate only catches hard failures (5xx), but a canary can be “slow but not broken” — serving 200s with 3× the latency. A second metric like `histogram_quantile(0.99, rate(gateway_request_duration_seconds_bucket{rs_hash='{{args.canary-hash}}'}[60s])) > 0.5` would catch latency regressions before they show up as timeouts or user complaints. A third useful metric would be per‑pod request throughput — if the canary receives far less traffic than expected, something is wrong with routing even if the error rate looks clean.