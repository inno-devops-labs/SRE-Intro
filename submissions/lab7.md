# Lab 7 — Progressive Delivery: Canary Deployments

## 7.1 Argo Rollouts version

Command used:

```bash
/home/ernest/.local/bin/kubectl-argo-rollouts version
```

Output:

```text
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:08:11Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: linux/amd64
```

Controller image:

```text
quay.io/argoproj/argo-rollouts:v1.9.0
```

---

## 7.2 Canary paused at 20%

I converted `k8s/gateway.yaml` from a Kubernetes `Deployment` to an Argo Rollouts `Rollout`. The gateway was configured with `replicas: 5` so that pod-based canary weights are easy to observe.

The canary strategy was configured as:

```text
20% -> manual pause -> 60% -> 30s pause -> 100%
```

After applying the updated manifest with `APP_VERSION=v2`, the Rollout stopped at the first canary pause:

```text
Phase:             Paused
Message:           CanaryPauseStep
Current Step Index: 1
Stable Rs:         66f7cd456b
Updated Replicas:  1
```

ReplicaSet split at this point:

```text
replicaset.apps/gateway-58b8f8d974   DESIRED=1   READY=1   app_version=v2
replicaset.apps/gateway-66f7cd456b   DESIRED=4   READY=4   stable
```

This matches the expected 20% canary split with 5 replicas: 1 updated canary pod and 4 stable pods.

---

## 7.3 Traffic split verification

To verify the traffic distribution, I used the in-cluster load generator instead of `kubectl port-forward`. This is important because port-forwarding can stick to a single endpoint, while an in-cluster client sends traffic through the Kubernetes Service and kube-proxy.

I counted `/events` requests from gateway pod logs:

```text
pod/gateway-58b8f8d974-jl6bw hash=58b8f8d974 app_version=v2 events_requests=80
pod/gateway-66f7cd456b-4586g hash=66f7cd456b app_version=stable events_requests=61
pod/gateway-66f7cd456b-l4lmk hash=66f7cd456b app_version=stable events_requests=89
pod/gateway-66f7cd456b-w4zn7 hash=66f7cd456b app_version=stable events_requests=80
pod/gateway-66f7cd456b-wcf77 hash=66f7cd456b app_version=stable events_requests=90
```

Total requests:

```text
canary_requests = 80
total_requests  = 400
canary_share    = 80 / 400 = 20%
```

The canary received exactly 20% of requests in this sample, which matches the configured `setWeight: 20`.

---

## 7.4 Promote to 100%

The plugin was installed after the first paused capture. For that reason, I used the same status-level effect that `kubectl argo rollouts promote gateway` performs for clearing the pause condition:

```bash
kubectl patch rollout gateway --type=merge --subresource=status -p '{"status":{"pauseConditions":null}}'
```

The equivalent normal command after installing the plugin is:

```bash
kubectl argo rollouts promote gateway
```

Intermediate state after promotion to the next step:

```text
Current Step Index: 2
Updated Replicas:  3
Stable Rs:         66f7cd456b
Message:           more replicas need to be updated
```

Final state after the rollout reached 100%:

```text
Status:          Healthy
Step:            5/5
SetWeight:       100
ActualWeight:    100
Desired:         5
Updated:         5
Ready:           5
Available:       5
Stable RS:       58b8f8d974
```

At this point, the new `v2` ReplicaSet became the stable ReplicaSet.

---

## 7.5 Abort bad version

Next, I changed the candidate version to `APP_VERSION=v3-bad` and applied the manifest. The Rollout again paused at the 20% canary step:

```text
Phase:             Paused
Message:           CanaryPauseStep
Stable Rs:         58b8f8d974
Current Pod Hash:  6b5c59dfd5
Updated Replicas:  1
```

ReplicaSet split before abort:

```text
replicaset.apps/gateway-58b8f8d974   DESIRED=4   READY=4   stable
replicaset.apps/gateway-6b5c59dfd5   DESIRED=1   READY=1   v3-bad
```

The normal command for aborting the rollout is:

```bash
kubectl argo rollouts abort gateway
```

Since I was preserving the same capture flow, I used the equivalent status patch:

```bash
kubectl patch rollout gateway --type=merge --subresource=status -p '{"status":{"abort":true}}'
```

Measured rollback timing:

```text
elapsed_ms=284 stable_replicas=4 bad_replicas=1
elapsed_ms=1467 stable_replicas=5 bad_replicas=0
```

Output after abort:

```text
Phase:     Degraded
Message:   RolloutAborted: Rollout aborted update to revision 3
Stable RS: 58b8f8d974

replicaset.apps/gateway-58b8f8d974   DESIRED=5   READY=5
replicaset.apps/gateway-6b5c59dfd5   DESIRED=0   READY=0
```

After verifying the abort behavior, I restored the desired manifest back to `APP_VERSION=v2` so that Git does not keep the intentionally bad version as the desired state.

---

## 7.6 Rollback comparison

Argo Rollouts abort moved all Service traffic back to the stable ReplicaSet in about **1.5 seconds**. The bad canary ReplicaSet was scaled down, and the stable ReplicaSet returned to 5 ready replicas almost immediately.

This is much faster than the Lab 5 Git revert rollback path. A Git revert rollback requires a revert commit, push, CI or image/manifest update, and ArgoCD sync before the cluster converges. In contrast, Argo Rollouts abort is an in-cluster control-plane operation. It avoids the Git/CI polling loop during an active bad canary and limits the blast radius while the rollout is still in progress.

---

# Task 2 — Multi-step canary with observation

## 7.7 Multi-step canary strategy

I updated the Rollout strategy to use a more granular canary rollout. I also kept an automated analysis step after the first observation window.

```yaml
strategy:
  canary:
    steps:
      - setWeight: 20
      - pause:
          duration: 60s
      - analysis:
          templates:
            - templateName: gateway-error-rate
          args:
            - name: canary-hash
              valueFrom:
                podTemplateHashValue: Latest
      - setWeight: 40
      - pause:
          duration: 60s
      - setWeight: 60
      - pause:
          duration: 60s
      - setWeight: 80
      - pause:
          duration: 30s
      - setWeight: 100
```

The rollout increases the canary gradually from 20% to 40%, 60%, 80%, and finally 100%. This gives time to observe the candidate version before sending all traffic to it.

---

## 7.8 Observed rollout steps

Observed state at the 20% step:

```text
Step:          1/10
SetWeight:     20
ActualWeight:  20
Updated:       1
Ready:         5
```

Observed state after the analysis step and promotion to 40%:

```text
Step:          4/10
SetWeight:     40
ActualWeight:  40
Updated:       2
AnalysisRun:   gateway-56d9cbd5c9-6-2 Successful
```

Observed state at the 60% step:

```text
Step:          6/10
SetWeight:     60
ActualWeight:  60
Updated:       3
Ready:         5
```

Final state after full promotion:

```text
Step:            10/10
SetWeight:       100
ActualWeight:    100
Updated:         5
Ready:           5
Status:          Healthy
```

---

## 7.9 Observation during the rollout

Because the Lab 3 host Prometheus/Grafana stack cannot directly scrape k3d pod IPs, I used `kubectl argo rollouts get rollout gateway --watch` together with the in-cluster load generator to observe the rollout behavior.

During the rollout, request traffic stayed steady while the number of updated replicas increased step by step:

```text
1 updated replica  -> 20%
2 updated replicas -> 40%
3 updated replicas -> 60%
5 updated replicas -> 100%
```

This matched the configured canary weights and confirmed that the Rollout was progressing gradually instead of replacing all pods at once.

I would want an automated abort at **20%** if there is a clear increase in 5xx errors. At that point, one canary pod is already receiving real user traffic, which is enough to detect a broken dependency or bad release behavior. At the same time, the blast radius is limited to about one fifth of total traffic.

---

# Bonus — Automated canary analysis

## B.1 In-cluster Prometheus

I installed the in-cluster Prometheus setup for canary analysis:

```text
deployment "prometheus" successfully rolled out
```

Prometheus target discovery showed gateway pods with the `rs_hash` label:

```text
pod=gateway-58b8f8d974-jl6bw rs_hash=58b8f8d974 health=up
pod=gateway-58b8f8d974-xl85c rs_hash=58b8f8d974 health=up
pod=gateway-58b8f8d974-66pwn rs_hash=58b8f8d974 health=up
pod=gateway-58b8f8d974-n6lbm rs_hash=58b8f8d974 health=up
pod=gateway-58b8f8d974-thcql rs_hash=58b8f8d974 health=up
```

The `rs_hash` label is important because it allows the analysis query to distinguish canary pods from stable pods.

---

## B.2 AnalysisTemplate

I installed the `AnalysisTemplate` for gateway error-rate checks:

```text
NAME                 AGE
gateway-error-rate   28s
```

The template was wired into the Rollout strategy with the current canary pod-template-hash:

```yaml
analysis:
  templates:
    - templateName: gateway-error-rate
  args:
    - name: canary-hash
      valueFrom:
        podTemplateHashValue: Latest
```

This allows the Prometheus query to evaluate only the latest canary ReplicaSet instead of mixing stable and canary metrics.

---

## B.3 Good canary auto-promoted

For the good canary, the AnalysisRun succeeded and the rollout continued automatically:

```text
NAME                     STATUS       AGE
gateway-56d9cbd5c9-6-2   Successful   5m9s
gateway-d578f4fbb-5-2    Failed       9m18s
```

Successful AnalysisRun measurements:

```text
name: gateway-56d9cbd5c9-6-2
phase: Successful
measurements:
  - value: '[0]'
    phase: Successful
  - value: '[0]'
    phase: Successful
  - value: '[0]'
    phase: Successful
```

The good canary had no measured 5xx error-rate increase, so the analysis passed and the Rollout continued to full promotion.

---

## B.4 Bad canary auto-aborted

To create a real failing canary, I pointed the canary `EVENTS_URL` to an intentionally broken service name:

```text
EVENTS_URL=http://broken-on-purpose:8081
```

This made `/events` calls fail for the canary while keeping the stable ReplicaSet healthy.

The Rollout was automatically aborted after the analysis failed:

```text
Status:          Degraded
Message:         RolloutAborted: Rollout aborted update to revision 8:
                 Step-based analysis phase error/failed:
                 Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
SetWeight:       0
ActualWeight:    0
```

Failed AnalysisRun list:

```text
NAME                     STATUS       AGE
gateway-56d9cbd5c9-6-2   Successful   9m15s
gateway-8c4bddcbb-8-2    Failed       88s
gateway-d578f4fbb-5-2    Failed       13m
```

Failed AnalysisRun measurements:

```text
name: gateway-8c4bddcbb-8-2
phase: Failed
message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
measurements:
  - value: '[1]'
    phase: Failed
  - value: '[1]'
    phase: Failed
```

State immediately after the automated abort:

```text
Status:        Degraded
SetWeight:     0
ActualWeight:  0

revision:7 gateway-56d9cbd5c9 ReplicaSet Healthy stable
revision:8 gateway-8c4bddcbb  ReplicaSet ScaledDown
```

The failed canary was scaled down, while the stable ReplicaSet continued serving traffic.

---

## B.5 Final restored rollout

After reverting the manifest back to the good stable template, the Rollout returned to a healthy state:

```text
Name:            gateway
Status:          Healthy
Strategy:        Canary
Step:            10/10
SetWeight:       100
ActualWeight:    100
Desired:         5
Updated:         5
Ready:           5
Available:       5

revision:9 gateway-56d9cbd5c9 ReplicaSet Healthy stable
revision:8 gateway-8c4bddcbb  ReplicaSet ScaledDown
```

---

## B.6 Additional metric for canary analysis

Beyond error rate, I would add **p95 or p99 request latency** for the canary, scoped by `rs_hash`.

A canary can have a low 5xx error rate while still being too slow to satisfy the user-facing SLO. Latency-based analysis would catch performance regressions that are not visible from error-rate metrics alone.

