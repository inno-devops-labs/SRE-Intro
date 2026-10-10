# Lab 7 Submission - Progressive Delivery: Canary Deployments

Repository: https://github.com/Ars5njo/SRE-Intro
Branch: `feature/lab7`
Date: 2026-07-01
Timezone: Europe/Moscow (UTC+03:00)

## Checklist

- [x] Task 1 done - Argo Rollouts installed, gateway converted to Rollout, canary deployed, promoted, and aborted.
- [x] Task 2 done - multi-step canary strategy applied and observed at 20%, 40%, and 60%.
- [x] Bonus Task done - AnalysisTemplate installed, good canary auto-promoted by successful analysis, bad canary auto-aborted by failed analysis.

## Task 1 - Manual Canary Deployment

### 7.1 Argo Rollouts installation

Controller install:

```text
namespace/argo-rollouts created
deployment.apps/argo-rollouts condition met
```

Plugin version:

```text
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:11:48Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: darwin/arm64
```

CRDs:

```text
NAME                            CREATED AT
rollouts.argoproj.io            2026-07-01T16:34:22Z
analysistemplates.argoproj.io   2026-07-01T16:34:22Z
analysisruns.argoproj.io        2026-07-01T16:34:22Z
```

### 7.2 Gateway converted to Rollout

`k8s/gateway.yaml` now uses:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: gateway
spec:
  replicas: 5
  strategy:
    canary:
      steps:
        - setWeight: 20
        - pause: {duration: 60s}
        - analysis:
            templates:
              - templateName: gateway-error-rate
            args:
              - name: canary-hash
                valueFrom:
                  podTemplateHashValue: Latest
        - setWeight: 40
        - pause: {duration: 60s}
        - setWeight: 60
        - pause: {duration: 60s}
        - setWeight: 80
        - pause: {duration: 30s}
        - setWeight: 100
```

### 7.3 Canary at 20%

Command:

```bash
kubectl argo rollouts set image gateway gateway=quickticket-gateway:v2
kubectl argo rollouts get rollout gateway
```

Output:

```text
Name:            gateway
Namespace:       default
Status:          Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/10
  SetWeight:     20
  ActualWeight:  20
Images:          quickticket-gateway:v1 (stable)
                 quickticket-gateway:v2 (canary)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

revision:2 ReplicaSet gateway-7f58dcf786 Healthy canary, 1 pod ready
revision:1 ReplicaSet gateway-7897d9d64c Healthy stable, 4 pods ready
```

### 7.4 Traffic split

In-cluster loadgen was used, not port-forward:

```bash
kubectl apply -f labs/lab7/loadgen.yaml
sleep 30
```

Per-pod log counts:

```text
pod/gateway-7897d9d64c-r4st6 image=quickticket-gateway:v1 events_requests=29
pod/gateway-7897d9d64c-rqhj7 image=quickticket-gateway:v1 events_requests=43
pod/gateway-7897d9d64c-xhq6h image=quickticket-gateway:v1 events_requests=35
pod/gateway-7897d9d64c-zn4w2 image=quickticket-gateway:v1 events_requests=40
pod/gateway-7f58dcf786-fpdpj image=quickticket-gateway:v2 events_requests=54
```

The short sample was noisy, but the canary received about 27% of observed `/events` requests, close to the expected 20% for a 1-of-5 canary.

### 7.5 Promote to 100%

Manual promote through the multi-step strategy:

```text
rollout 'gateway' promoted
```

Progression proof at 60%:

```text
Name:            gateway
Status:          Paused
Strategy:        Canary
  Step:          6/10
  SetWeight:     60
  ActualWeight:  60
Images:          quickticket-gateway:v1 (stable)
                 quickticket-gateway:v2 (canary)
Replicas:
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         5
  Available:     5
```

Final state:

```text
Name:            gateway
Status:          Healthy
Strategy:        Canary
  Step:          10/10
  SetWeight:     100
  ActualWeight:  100
Images:          quickticket-gateway:v2 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5
```

### 7.6 Bad version and manual abort

Bad canary before abort:

```text
Name:            gateway
Status:          Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/10
  SetWeight:     20
  ActualWeight:  20
Images:          quickticket-gateway:v2 (stable)
                 quickticket-gateway:v3-bad (canary)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5
```

Abort command and timing:

```text
rollout 'gateway' aborted
abort_to_no_bad_pods_seconds=3
```

After abort:

```text
Name:            gateway
Status:          Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/10
  SetWeight:     0
  ActualWeight:  0
Images:          quickticket-gateway:v2 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

revision:3 ReplicaSet gateway-66bc7fc8ff ScaledDown canary
revision:2 ReplicaSet gateway-7f58dcf786 Healthy stable, 5 pods ready
```

Abort vs Git revert from Lab 5:

Argo Rollouts abort removed the bad canary from service in about 3 seconds and left the stable ReplicaSet serving. A Git revert rollback is slower because it requires commit/revert creation, CI, image build/push, ArgoCD sync, and Kubernetes rollout time. Abort is the better first-response mitigation for a canary that is still in progress; Git revert is still useful to remove the bad change from source control.

## Task 2 - Multi-Step Canary with Observation

Strategy used in `k8s/gateway.yaml`:

```yaml
strategy:
  canary:
    steps:
      - setWeight: 20
      - pause: {duration: 60s}
      - analysis:
          templates:
            - templateName: gateway-error-rate
          args:
            - name: canary-hash
              valueFrom:
                podTemplateHashValue: Latest
      - setWeight: 40
      - pause: {duration: 60s}
      - setWeight: 60
      - pause: {duration: 60s}
      - setWeight: 80
      - pause: {duration: 30s}
      - setWeight: 100
```

Observed steps:

```text
20%: Updated=1, Ready=5, ActualWeight=20
40%: Updated=2, Ready=5, ActualWeight=40
60%: Updated=3, Ready=5, ActualWeight=60
100%: Updated=5, Ready=5, ActualWeight=100, Status=Healthy
```

Dashboard observation:

The host Docker Compose Prometheus/Grafana from Lab 3 cannot scrape k3d pod IPs, as noted in the lab. I used `kubectl argo rollouts get rollout gateway` for step/replica observation and the in-cluster Prometheus from the bonus task for canary metrics. Request traffic stayed steady enough for Prometheus analysis to collect three successful `[0]` error-rate measurements during the good canary.

Automated abort threshold:

I would auto-abort at 20% if canary error rate is above 5% for two consecutive analysis intervals. At 20%, the blast radius is limited to one of five pods, but there is already enough traffic to detect clear 5xx regression. Waiting until 60% or 80% would expose too many users to a bad release.

## Bonus Task - Automated Canary Analysis

### B.1 In-cluster Prometheus

Prometheus was installed from `labs/lab7/prometheus.yaml`:

```text
namespace/monitoring created
deployment.apps/prometheus created
service/prometheus created
prometheus-859dd77858-6s7dh   1/1   Running
```

### B.2 AnalysisTemplate

`k8s/analysis-template.yaml` was added and applied.

```text
NAME                 AGE
gateway-error-rate   17m
```

### B.4 Good version auto-promotes

Successful AnalysisRun:

```text
NAME                     STATUS       AGE
gateway-7f58dcf786-2-2   Successful   2m15s
```

Measurements:

```text
Metric: error-rate
Consecutive Success: 3
Measurements:
  Value: [0] Phase: Successful
  Value: [0] Phase: Successful
  Value: [0] Phase: Successful
```

After successful analysis, the rollout continued from 20% to 40%, 60%, 80%, and then 100%, ending Healthy with `quickticket-gateway:v2` as stable.

### B.5 Bad version auto-aborts

Runtime note: the current gateway `/health` endpoint checks `EVENTS_URL`, so a broken `EVENTS_URL` also fails readiness. To demonstrate the lab's intended "pod is Ready, but `/events` returns 5xx" behavior, the live test patched only the runtime Rollout probes to `/metrics`; the repository manifest remains unchanged.

Failed AnalysisRun list:

```text
NAME                     STATUS       AGE
gateway-5bbbfc7558-5-2   Failed       2m21s
gateway-7f58dcf786-2-2   Successful   12m
```

Failed AnalysisRun measurements:

```yaml
status:
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
    - count: 2
      failed: 2
      measurements:
        - phase: Failed
          value: '[1]'
        - phase: Failed
          value: '[1]'
      name: error-rate
      phase: Failed
  phase: Failed
```

Final Rollout after automated abort:

```text
Name:            gateway
Status:          Degraded
Message:         RolloutAborted: Rollout aborted update to revision 5: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/10
  SetWeight:     0
  ActualWeight:  0
Images:          quickticket-gateway:v2 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

revision:5 ReplicaSet gateway-5bbbfc7558 ScaledDown canary
revision:2 ReplicaSet gateway-7f58dcf786 Healthy stable, 5 pods ready
```

Additional metric to add:

I would add latency, for example p95 or p99 `gateway_request_duration_seconds` for only the canary hash. Error rate alone catches hard failures, but latency catches slow canaries before they become outright 5xx errors.
