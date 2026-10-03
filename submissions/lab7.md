# Lab 7 — Progressive Delivery: Canary Deployments

## Task 1 — Manual Canary Deployment

### 7.1 Argo Rollouts installation

Argo Rollouts was installed in the `argo-rollouts` namespace.

The regular client-side `kubectl apply` failed for two large CRDs because the
`kubectl.kubernetes.io/last-applied-configuration` annotation exceeded the
Kubernetes annotation size limit. The installation was completed successfully
using server-side apply:

```bash
kubectl apply --server-side \
  -n argo-rollouts \
  -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
```

Controller readiness check:

```text
deployment.apps/argo-rollouts condition met
```

Kubectl plugin version:

```text
kubectl-argo-rollouts: v1.10.0+d90700a
  BuildDate: 2026-08-27T15:22:01Z
  GitCommit: d90700ae8d71d141561f0c546e19f999bb335cbd
  GitTreeState: clean
  GoVersion: go1.26.7
  Compiler: gc
  Platform: linux/amd64
```

### 7.2 Convert gateway Deployment to Rollout

The gateway workload was converted from a standard Kubernetes `Deployment`
to an Argo Rollouts `Rollout` using five replicas.

Initial manual-canary strategy:

```yaml
strategy:
  canary:
    steps:
      - setWeight: 20
      - pause: {}
      - setWeight: 60
      - pause: {duration: 30s}
      - setWeight: 100
```

### 7.3 Canary at 20%

After changing the pod template to trigger a new revision, the rollout paused
at 20% as expected:

```text
Name:            gateway
Namespace:       default
Status:          Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5
```

There was one canary pod and four stable pods.

### 7.4 Traffic split verification

The in-cluster load generator was used so traffic passed through the Kubernetes
Service rather than `kubectl port-forward`.

Observed request counts:

```text
gateway-55df7bb878-bhmbc events_requests=61
gateway-55df7bb878-cwswr events_requests=60
gateway-55df7bb878-jwfb7 events_requests=61
gateway-55df7bb878-t5fdc events_requests=58
gateway-65978c546b-x555j events_requests=55
```

Total requests:

```text
61 + 60 + 61 + 58 + 55 = 295
```

Canary share:

```text
55 / 295 ≈ 18.6%
```

The observed result is close to the configured 20% canary weight.

### 7.5 Manual promotion

After:

```bash
kubectl argo rollouts promote gateway
```

the rollout moved to the next stage:

```text
Status:          Paused
Step:            3/5
SetWeight:       60
ActualWeight:    60
Updated:         3
Ready:           5
Available:       5
```

After the timed pause, the rollout automatically completed:

```text
Status:          Healthy
Step:            5/5
SetWeight:       100
ActualWeight:    100
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5
```

The new revision became stable and the old ReplicaSet was scaled down.

### 7.6 Manual abort of a bad canary

A new revision was started and paused at 20%:

```text
Status:          Paused
Step:            1/5
SetWeight:       20
ActualWeight:    20
Updated:         1
Ready:           5
Available:       5
```

The canary was then aborted:

```bash
kubectl argo rollouts abort gateway
```

Result:

```text
Status:          Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5
```

The canary ReplicaSet was scaled down and the previous stable ReplicaSet
remained healthy.

### 7.7 Abort vs Git revert

From the `abort` command to all traffic being served by the stable revision,
the rollback took only a few seconds. Argo Rollouts immediately removed the
canary from service and restored the stable replica count.

This was faster than the Git revert rollback used in Lab 5. A Git revert
requires creating and pushing a revert commit, waiting for GitOps
synchronization, and then waiting for Kubernetes to reconcile the deployment.
Argo Rollouts can abort directly from the currently running rollout state.

---

## Task 2 — Multi-Step Canary with Observation

### 7.8 Multi-step strategy

The rollout strategy was changed to:

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

### 7.9 Rollout observation

The in-cluster load generator remained running while the rollout progressed.

Observed stages:

```text
20%  -> Updated: 1
40%  -> Updated: 2
60%  -> Updated: 3
80%  -> Updated: 4
100% -> Updated: 5
```

The detailed progression was:

```text
Step 1/9
SetWeight: 20
ActualWeight: 20
Updated: 1
```

```text
Step 3/9
SetWeight: 40
ActualWeight: 40
Updated: 2
```

```text
Step 5/9
SetWeight: 60
ActualWeight: 60
Updated: 3
```

```text
Step 7/9
SetWeight: 80
ActualWeight: 80
Updated: 4
```

```text
Status: Healthy
Step: 9/9
SetWeight: 100
ActualWeight: 100
Updated: 5
Ready: 5
Available: 5
```

The updated-replica count therefore increased exactly as expected:

```text
1 -> 2 -> 3 -> 4 -> 5
```

The Docker Compose Prometheus/Grafana stack from Lab 3 cannot directly scrape
the k3d pod network, so the rollout progression was observed using
`kubectl argo rollouts get rollout gateway --watch`.

### Automated abort threshold

I would enable automated abort starting at 20% canary traffic. At this stage,
the new version receives enough real traffic to detect elevated error rates or
latency, while 80% of the traffic is still handled by the stable version.
This limits the impact of a faulty release while still providing useful
validation data.

---

## Bonus Task — Automated Canary Analysis

### B.1 In-cluster Prometheus

Prometheus was deployed inside the Kubernetes cluster:

```text
deployment "prometheus" successfully rolled out
```

All five gateway pods were discovered and had the expected ReplicaSet hash:

```text
gateway-869d4f4b64-52qb5 rs= 869d4f4b64 up
gateway-869d4f4b64-v7fxw rs= 869d4f4b64 up
gateway-869d4f4b64-n8dqq rs= 869d4f4b64 up
gateway-869d4f4b64-476qd rs= 869d4f4b64 up
gateway-869d4f4b64-twmsj rs= 869d4f4b64 up
```

The `rs_hash` label allows the analysis query to select metrics from the
current canary ReplicaSet only.

### B.2 AnalysisTemplate

The analysis template was installed:

```text
NAME                 AGE
gateway-error-rate   5s
```

The template uses:

- `initialDelay: 60s` to give Prometheus time to discover and scrape the canary;
- `or on() vector(0)` for the 5xx numerator so zero errors is represented as zero;
- a strict denominator so a canary with no measurable traffic is not promoted;
- `canary-hash` to scope the Prometheus query to the canary ReplicaSet.

### B.3 Analysis step

The rollout strategy was wired to the AnalysisTemplate:

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

### B.4 Good canary auto-promotion

For the good version, the AnalysisRun completed successfully and the rollout
continued automatically without manual promotion.

Observed behavior:

```text
20% canary
-> AnalysisRun Running
-> AnalysisRun Successful
-> rollout continued
-> 100%
-> Healthy
```

Successful AnalysisRun example:

```text
gateway-695f674c8f-7-2    Successful
```

The new revision was promoted to stable automatically.

### B.5 Bad canary auto-abort

A bad canary was configured with an invalid events endpoint so `/events`
requests returned 5xx responses. The canary remained available long enough for
Prometheus to collect request metrics.

Analysis runs:

```text
NAME                      STATUS       AGE
gateway-6856748444-10-2   Failed       4m50s
gateway-695f674c8f-7-2    Successful   24m
gateway-86b9dcb644-9-2    Error        9m25s
gateway-f8664d5ff-5-2     Successful   33m
```

The successful bad-canary detection was:

```text
gateway-6856748444-10-2   Failed
```

Relevant AnalysisRun configuration:

```yaml
spec:
  args:
    - name: canary-hash
      value: "6856748444"
  metrics:
    - count: 3
      failureLimit: 1
      initialDelay: 60s
      interval: 20s
      name: error-rate
      successCondition: result[0] < 0.05
```

Prometheus measured the following canary error ratios:

```yaml
measurements:
  - phase: Failed
    value: '[0.4074074074074074]'
  - phase: Failed
    value: '[0.445945945945946]'
```

These correspond to approximately:

```text
40.7% error rate
44.6% error rate
```

Both values were well above the allowed 5% threshold.

Final AnalysisRun result:

```text
Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
phase: Failed
```

Argo Rollouts automatically aborted the deployment, scaled down the canary,
and kept the previous stable revision serving traffic:

```text
Status:          Degraded
SetWeight:       0
ActualWeight:    0
Updated:         0
Ready:           5
Available:       5
```

This demonstrated automatic rollback based on real Prometheus metrics rather
than manual intervention.

### B.6 Cleanup

The load generator was removed after testing:

```bash
kubectl delete -f labs/lab7/loadgen.yaml
```

The temporary broken `EVENTS_URL` was reverted to the normal events service
address after the bad-canary test.
