# Lab 7 — Progressive Delivery: Canary Deployments

## Task 1 — Manual Canary Deployment

### 7.1 Argo Rollouts installation

Argo Rollouts controller is installed in the `argo-rollouts` namespace.

```bash
$ kubectl -n argo-rollouts get deploy argo-rollouts -o wide
NAME            READY   UP-TO-DATE   AVAILABLE   AGE   CONTAINERS      IMAGES                                  SELECTOR
argo-rollouts   1/1     1            1           33m   argo-rollouts   quay.io/argoproj/argo-rollouts:v1.9.0   app.kubernetes.io/name=argo-rollouts
```

The installed controller version is `v1.9.0`.

### 7.2 Gateway converted to Rollout

`k8s/gateway.yaml` was converted from `Deployment` to `argoproj.io/v1alpha1` `Rollout`.

Final important fields:

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

### 7.3 Canary at 20%

During the manual canary, the rollout created one canary pod out of five replicas.

```bash
$ kubectl get rs -l app=gateway -o wide --show-labels
NAME                 DESIRED   CURRENT   READY   CONTAINERS   IMAGES                        LABELS
gateway-75dd57c644   4         4         4       gateway      quickticket-gateway:v1        app=gateway,rollouts-pod-template-hash=75dd57c644
gateway-6498d5dd8d   1         1         1       gateway      quickticket-gateway:v2        app=gateway,rollouts-pod-template-hash=6498d5dd8d
```

Observed rollout state at this point:

```text
phase: Paused
currentStepIndex: 1
currentPodHash: 6498d5dd8d
stableRS: 75dd57c644
replicas: 5
updatedReplicas: 1
readyReplicas: 5
```

Traffic was generated from inside the cluster with `labs/lab7/loadgen.yaml`, not by port-forwarding the Service.

### 7.4 Promote canary

After promotion, the rollout advanced through the next weights and completed at 100%.

```text
setWeight: 20 -> pause
promote
setWeight: 60 -> timed pause
setWeight: 100
phase: Healthy
updatedReplicas: 5
readyReplicas: 5
```

Final healthy state after promoting a good version:

```bash
$ kubectl get rollout gateway -o jsonpath='phase={.status.phase} currentStepIndex={.status.currentStepIndex} stableRS={.status.stableRS} updated={.status.updatedReplicas} ready={.status.readyReplicas} currentPodHash={.status.currentPodHash}{"\n"}'
phase=Healthy currentStepIndex=6 stableRS=7dbd865f6b updated=5 ready=5 currentPodHash=7dbd865f6b
```

### 7.5 Bad version abort

A bad version was deployed and aborted during the canary. The canary ReplicaSet was scaled down to zero and the previous stable ReplicaSet stayed at 5 replicas.

```bash
$ kubectl get rs -l app=gateway -o wide --show-labels
NAME                 DESIRED   CURRENT   READY   AGE     CONTAINERS   IMAGES                        LABELS
gateway-75d7dccf97   0         0         0       17m     gateway      quickticket-gateway:v3-bad    app=gateway,rollouts-pod-template-hash=75d7dccf97
gateway-7dbd865f6b   5         5         5       19m     gateway      quickticket-gateway:v3-good   app=gateway,rollouts-pod-template-hash=7dbd865f6b
```

Observed aborted rollout state:

```yaml
status:
  abort: true
  abortedAt: "2026-06-27T12:59:17Z"
  phase: Degraded
  message: 'RolloutAborted: Rollout aborted update to revision 6: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)'
  readyReplicas: 5
  replicas: 5
  stableRS: 7dbd865f6b
```

Answer: from abort to all traffic serving the stable version was about 5 seconds. The abort happened at `12:59:17Z`, and the rollout had minimum availability again at `12:59:22Z`. This is much faster than the Lab 5 `git revert` rollback path, because Git revert needs commit, push, ArgoCD sync/reconcile, and Kubernetes rollout time.

## Task 2 — Multi-Step Canary with Observation

The multi-step strategy tested before wiring automated analysis was:

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

Observed progression:

```text
20%  -> updated replicas: 1/5, stable replicas: 4/5
40%  -> updated replicas: 2/5, stable replicas: 3/5
60%  -> updated replicas: 3/5, stable replicas: 2/5
80%  -> updated replicas: 4/5, stable replicas: 1/5
100% -> updated replicas: 5/5, phase: Healthy
```

Dashboard / metrics observation: request volume stayed continuous during the rollout because the in-cluster `loadgen` kept sending traffic through the `gateway` Service. The important observation was that replica movement matched the canary weights: 1, 2, 3, 4, then 5 updated pods.

Answer: I would configure automated abort at 20% for critical errors above 5%. With five replicas, 20% already gives one real canary pod receiving real service traffic, so it is enough to catch a bad release while the blast radius is still small.

## Bonus Task — Automated Canary Analysis

### AnalysisTemplate

`k8s/analysis-template.yaml` was added and applied.

```bash
$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   20m
```

Template metric:

```yaml
metrics:
  - name: error-rate
    initialDelay: 60s
    interval: 20s
    count: 3
    successCondition: result[0] < 0.05
    failureLimit: 1
    provider:
      prometheus:
        address: http://prometheus.monitoring.svc.cluster.local:9090
```

### Prometheus

In-cluster Prometheus was installed for the analysis query.

```bash
$ kubectl -n monitoring get pods -o wide
NAME                          READY   STATUS    RESTARTS   AGE   IP            NODE
prometheus-7f67fd8d74-qqkmb   1/1     Running   0          10m   10.244.0.44   sre-control-plane
```

### Successful analysis

A good analysis run against the healthy gateway hash completed successfully with three zero-error measurements.

```bash
$ kubectl get analysisrun
NAME                    STATUS       AGE
gateway-566b6b548-6-2   Failed       13m
gateway-good-manual     Successful   42s
```

Successful measurement values:

```yaml
name: gateway-good-manual
phase: Successful
metricResults:
  - name: error-rate
    consecutiveSuccess: 3
    measurements:
      - phase: Successful
        value: '[0]'
      - phase: Successful
        value: '[0]'
      - phase: Successful
        value: '[0]'
```

### Failed analysis and auto-abort

The bad canary failed analysis and the rollout was automatically aborted.

```yaml
name: gateway-566b6b548-6-2
phase: Failed
message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
metricResults:
  - name: error-rate
    phase: Failed
    failed: 2
    measurements:
      - phase: Failed
        value: '[0.058823529411764705]'
      - phase: Failed
        value: '[0.058823529411764705]'
```

Final rollout after restoring the stable version:

```yaml
status:
  phase: Healthy
  readyReplicas: 5
  replicas: 5
  stableRS: 7dbd865f6b
  updatedReplicas: 5
  currentPodHash: 7dbd865f6b
```

Answer: beyond error rate, I would add latency, especially p95 or p99 request duration for `/events` and `/payments`. A canary can return HTTP 200 while still being too slow, and latency regression is exactly the kind of user-visible problem that error-rate-only analysis can miss.

