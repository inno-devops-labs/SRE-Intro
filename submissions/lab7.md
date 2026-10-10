# Lab 7 — Progressive Delivery: Canary Deployments

## Task 1 — Manual Canary Deployment

```bash
[ustkost@prime SRE-Intro]$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:08:11Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: linux/amd64
```

### Before promotion (20%)
```bash
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/ustkost/quickticket-gateway:2a69b1c5f9796ee47f540122048f9626f9300ec0 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS     AGE    INFO
⟳ gateway                            Rollout     ॥ Paused   7m19s  
├──# revision:2                                                    
│  └──⧉ gateway-684444b5ff           ReplicaSet  ✔ Healthy  32s    canary
│     └──□ gateway-684444b5ff-7wql9  Pod         ✔ Running  31s    ready:1/1
└──# revision:1                                                    
   └──⧉ gateway-775fcd7d47           ReplicaSet  ✔ Healthy  7m19s  stable
      ├──□ gateway-775fcd7d47-2zksf  Pod         ✔ Running  7m19s  ready:1/1
      ├──□ gateway-775fcd7d47-4gpjg  Pod         ✔ Running  7m19s  ready:1/1
      ├──□ gateway-775fcd7d47-d8jsx  Pod         ✔ Running  7m19s  ready:1/1
      └──□ gateway-775fcd7d47-tqpzn  Pod         ✔ Running  7m19s  ready:1/1
```

### After promotion (60%)
```bash
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          3/5
  SetWeight:     60
  ActualWeight:  60
Images:          ghcr.io/ustkost/quickticket-gateway:2a69b1c5f9796ee47f540122048f9626f9300ec0 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS     AGE    INFO
⟳ gateway                            Rollout     ॥ Paused   15m    
├──# revision:2                                                    
│  └──⧉ gateway-684444b5ff           ReplicaSet  ✔ Healthy  8m40s  canary
│     ├──□ gateway-684444b5ff-7wql9  Pod         ✔ Running  8m39s  ready:1/1
│     ├──□ gateway-684444b5ff-r54nf  Pod         ✔ Running  19s    ready:1/1
│     └──□ gateway-684444b5ff-tr7b2  Pod         ✔ Running  19s    ready:1/1
└──# revision:1                                                    
   └──⧉ gateway-775fcd7d47           ReplicaSet  ✔ Healthy  15m    stable
      ├──□ gateway-775fcd7d47-d8jsx  Pod         ✔ Running  15m    ready:1/1
      └──□ gateway-775fcd7d47-tqpzn  Pod         ✔ Running  15m    ready:1/1
```

### After promotion (100%)
```bash
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/ustkost/quickticket-gateway:2a69b1c5f9796ee47f540122048f9626f9300ec0 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ✔ Healthy     16m    
├──# revision:2                                                       
│  └──⧉ gateway-684444b5ff           ReplicaSet  ✔ Healthy     10m    stable
│     ├──□ gateway-684444b5ff-7wql9  Pod         ✔ Running     9m59s  ready:1/1
│     ├──□ gateway-684444b5ff-r54nf  Pod         ✔ Running     99s    ready:1/1
│     ├──□ gateway-684444b5ff-tr7b2  Pod         ✔ Running     99s    ready:1/1
│     ├──□ gateway-684444b5ff-bmrgp  Pod         ✔ Running     57s    ready:1/1
│     └──□ gateway-684444b5ff-wp6lc  Pod         ✔ Running     57s    ready:1/1
└──# revision:1                                                       
   └──⧉ gateway-775fcd7d47           ReplicaSet  • ScaledDown  16m    
```

### After abort
```
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/ustkost/quickticket-gateway:2a69b1c5f9796ee47f540122048f9626f9300ec0 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE  INFO
⟳ gateway                            Rollout     ✖ Degraded    34m  
├──# revision:3                                                     
│  └──⧉ gateway-7598dd5fc4           ReplicaSet  • ScaledDown  12m  canary
├──# revision:2                                                     
│  └──⧉ gateway-684444b5ff           ReplicaSet  ✔ Healthy     27m  stable
│     ├──□ gateway-684444b5ff-7wql9  Pod         ✔ Running     27m  ready:1/1
│     ├──□ gateway-684444b5ff-r54nf  Pod         ✔ Running     19m  ready:1/1
│     ├──□ gateway-684444b5ff-bmrgp  Pod         ✔ Running     18m  ready:1/1
│     ├──□ gateway-684444b5ff-wp6lc  Pod         ✔ Running     18m  ready:1/1
│     └──□ gateway-684444b5ff-lgkbf  Pod         ✔ Running     17s  ready:1/1
└──# revision:1                                                     
   └──⧉ gateway-775fcd7d47           ReplicaSet  • ScaledDown  34m  
```

### How long from abort to all traffic serving the stable version? Compare with git revert rollback from Lab 5
Aborting an Argo Rollout is the fastest response to a failed canary deployment because it instantly stops sending traffic to the new version. Reverting the Git commit is still important to restore the intended configuration, but it is a slower process since it depends on the GitOps pipeline

## Task 2 — Multi-Step Canary with Observation

### Strategy yaml

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

### Step 3

```bash
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          3/9
  SetWeight:     40
  ActualWeight:  40
Images:          ghcr.io/ustkost/quickticket-gateway:2a69b1c5f9796ee47f540122048f9626f9300ec0 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       2
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE  INFO
⟳ gateway                            Rollout     ॥ Paused      48m  
├──# revision:3                                                     
│  └──⧉ gateway-7598dd5fc4           ReplicaSet  ✔ Healthy     26m  canary
│     ├──□ gateway-7598dd5fc4-rbml2  Pod         ✔ Running     94s  ready:1/1
│     └──□ gateway-7598dd5fc4-hqs2c  Pod         ✔ Running     23s  ready:1/1
├──# revision:2                                                     
│  └──⧉ gateway-684444b5ff           ReplicaSet  ✔ Healthy     41m  stable
│     ├──□ gateway-684444b5ff-7wql9  Pod         ✔ Running     41m  ready:1/1
│     ├──□ gateway-684444b5ff-r54nf  Pod         ✔ Running     33m  ready:1/1
│     └──□ gateway-684444b5ff-bmrgp  Pod         ✔ Running     32m  ready:1/1
└──# revision:1                                                     
   └──⧉ gateway-775fcd7d47           ReplicaSet  • ScaledDown  48m  
```

### Step 4

```bash
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          5/9
  SetWeight:     60
  ActualWeight:  60
Images:          ghcr.io/ustkost/quickticket-gateway:2a69b1c5f9796ee47f540122048f9626f9300ec0 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ॥ Paused      49m    
├──# revision:3                                                       
│  └──⧉ gateway-7598dd5fc4           ReplicaSet  ✔ Healthy     27m    canary
│     ├──□ gateway-7598dd5fc4-rbml2  Pod         ✔ Running     2m34s  ready:1/1
│     ├──□ gateway-7598dd5fc4-hqs2c  Pod         ✔ Running     83s    ready:1/1
│     └──□ gateway-7598dd5fc4-cddwc  Pod         ✔ Running     11s    ready:1/1
├──# revision:2                                                       
│  └──⧉ gateway-684444b5ff           ReplicaSet  ✔ Healthy     42m    stable
│     ├──□ gateway-684444b5ff-7wql9  Pod         ✔ Running     42m    ready:1/1
│     └──□ gateway-684444b5ff-bmrgp  Pod         ✔ Running     33m    ready:1/1
└──# revision:1                                                       
   └──⧉ gateway-775fcd7d47           ReplicaSet  • ScaledDown  49m    
```

### Step 5

```bash
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          5/9
  SetWeight:     60
  ActualWeight:  60
Images:          ghcr.io/ustkost/quickticket-gateway:2a69b1c5f9796ee47f540122048f9626f9300ec0 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ॥ Paused      49m    
├──# revision:3                                                       
│  └──⧉ gateway-7598dd5fc4           ReplicaSet  ✔ Healthy     28m    canary
│     ├──□ gateway-7598dd5fc4-rbml2  Pod         ✔ Running     3m16s  ready:1/1
│     ├──□ gateway-7598dd5fc4-hqs2c  Pod         ✔ Running     2m5s   ready:1/1
│     └──□ gateway-7598dd5fc4-cddwc  Pod         ✔ Running     53s    ready:1/1
├──# revision:2                                                       
│  └──⧉ gateway-684444b5ff           ReplicaSet  ✔ Healthy     43m    stable
│     ├──□ gateway-684444b5ff-7wql9  Pod         ✔ Running     43m    ready:1/1
│     └──□ gateway-684444b5ff-bmrgp  Pod         ✔ Running     34m    ready:1/1
└──# revision:1                                                       
   └──⧉ gateway-775fcd7d47           ReplicaSet  • ScaledDown  49m    
```

### Observations
- As weight increased, new version replica count climbed: 1 -> 2 -> 3 -> 4 -> 5 (max)
- No error spike - good version
- Across different steps request rate was consistent and stable

### At what canary percentage would you want an automated abort? Why?
I would configure the automated abort threshold at the 20% traffic step. At that stage, there is usually enough traffic to detect issues while limiting user impact. Aborting early reduces the blast radius and allows a much faster recovery than waiting until higher traffic percentages, where more users would already be affected


