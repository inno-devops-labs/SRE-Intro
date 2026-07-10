# Lab 7 Submission

## Task 1. Manual canary deployment with Argo Rollouts

### 1. Argo Rollouts version

```text
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:11:48Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: darwin/arm64
```

### 2. Gateway rollout paused at 20%

I changed `k8s/gateway.yaml` from `Deployment` to `Rollout`, set `replicas: 5`, and used canary steps `20 -> pause -> 60 -> pause 30s -> 100`.

After applying a new pod template version, the rollout paused at 20% exactly as expected:

```text
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/shnupel/quickticket-gateway:c0b57674d19ee81195b6993de20947403fd012ee (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS     AGE  INFO
⟳ gateway                            Rollout     ॥ Paused   64s
├──# revision:2
│  └──⧉ gateway-5758b8d586           ReplicaSet  ✔ Healthy  35s  canary
│     └──□ gateway-5758b8d586-5ft9r  Pod         ✔ Running  35s  ready:1/1
└──# revision:1
   └──⧉ gateway-6ff5c46bb7           ReplicaSet  ✔ Healthy  64s  stable
      ├──□ gateway-6ff5c46bb7-9dcf4  Pod         ✔ Running  64s  ready:1/1
      ├──□ gateway-6ff5c46bb7-g28h6  Pod         ✔ Running  64s  ready:1/1
      ├──□ gateway-6ff5c46bb7-qz8kf  Pod         ✔ Running  64s  ready:1/1
      └──□ gateway-6ff5c46bb7-sdpdb  Pod         ✔ Running  64s  ready:1/1
```

### 3. Traffic split check

I used the provided in-cluster `labs/lab7/loadgen.yaml` and checked recent logs for each gateway pod:

```text
pod/gateway-5758b8d586-5ft9r events_requests_last20s=10
pod/gateway-6ff5c46bb7-9dcf4 events_requests_last20s=16
pod/gateway-6ff5c46bb7-g28h6 events_requests_last20s=11
pod/gateway-6ff5c46bb7-qz8kf events_requests_last20s=10
pod/gateway-6ff5c46bb7-sdpdb events_requests_last20s=7
```

The split is not mathematically perfect on a short sample, but traffic clearly went to all 5 pods, including the single canary pod.

### 4. Promote to 100%

First I ran:

```bash
kubectl argo rollouts promote gateway
```

Right after promote the rollout moved to the 60% step:

```text
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          3/5
  SetWeight:     60
  ActualWeight:  60
Images:          ghcr.io/shnupel/quickticket-gateway:c0b57674d19ee81195b6993de20947403fd012ee (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         5
  Available:     5
```

Then `kubectl argo rollouts status gateway --timeout=240s` showed:

```text
Paused - CanaryPauseStep
Progressing - more replicas need to be updated
Progressing - updated replicas are still becoming available
Progressing - old replicas are pending termination
Progressing - updated replicas are still becoming available
Progressing - waiting for all steps to complete
Healthy
```

Final state after promotion:

```text
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/shnupel/quickticket-gateway:c0b57674d19ee81195b6993de20947403fd012ee (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5
```

### 5. Bad version and abort

For the bad version I changed only environment variables in the pod template:

- `APP_VERSION=v3-bad`
- `GATEWAY_TIMEOUT_MS=1`

This kept the pod `Ready`, but made the canary version bad for real traffic.

State before abort:

```text
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/shnupel/quickticket-gateway:c0b57674d19ee81195b6993de20947403fd012ee (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ॥ Paused      6m3s
├──# revision:3
│  └──⧉ gateway-67875c5dd8           ReplicaSet  ✔ Healthy     13s    canary
│     └──□ gateway-67875c5dd8-bplf2  Pod         ✔ Running     13s    ready:1/1
├──# revision:2
│  └──⧉ gateway-5758b8d586           ReplicaSet  ✔ Healthy     5m34s  stable
│     ├──□ gateway-5758b8d586-5ft9r  Pod         ✔ Running     5m34s  ready:1/1
│     ├──□ gateway-5758b8d586-dl8cr  Pod         ✔ Running     79s    ready:1/1
│     ├──□ gateway-5758b8d586-hnmkz  Pod         ✔ Running     79s    ready:1/1
│     └──□ gateway-5758b8d586-z7qqv  Pod         ✔ Running     43s    ready:1/1
└──# revision:1
   └──⧉ gateway-6ff5c46bb7           ReplicaSet  • ScaledDown  6m3s
```

Then I ran:

```bash
kubectl argo rollouts abort gateway
```

Measured rollback time:

```text
abort_to_all_stable_ms=2495
```

State after abort:

```text
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/shnupel/quickticket-gateway:c0b57674d19ee81195b6993de20947403fd012ee (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5
```

### 6. How long did rollback take? Compare with Lab 5 

From `abort` to all traffic going back to the stable version it took about **2.5 seconds**.

This was much faster than rollback with `git revert` in Lab 5. In Lab 5 I had to change Git state, wait for ArgoCD sync, and then wait for Kubernetes rollout. Here the stable ReplicaSet was already running, so Argo Rollouts just stopped the canary and returned traffic to the old stable pods almost immediately.
