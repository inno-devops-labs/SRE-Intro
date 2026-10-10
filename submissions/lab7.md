# Lab 7 — Progressive Delivery: Canary Deployments

## Task 1 — Manual Canary Deployment

- Output of kubectl argo rollouts version
```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab7)
$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:15:27Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: windows/amd64
```
- Output of kubectl argo rollouts get rollout gateway showing Paused at 20% (during canary)

```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/fifidadan/quickticket-gateway:8604770ddddf34d40922d8003f7c931a962b0885 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS     AGE    INFO
⟳ gateway                            Rollout     ॥ Paused   5m54s
├──# revision:2
│  └──⧉ gateway-649dc5d6fc           ReplicaSet  ✔ Healthy  100s   canary
│     └──□ gateway-649dc5d6fc-p5jlf  Pod         ✔ Running  99s    ready:1/1
└──# revision:1
   └──⧉ gateway-6c69c8666b           ReplicaSet  ✔ Healthy  5m54s  stable
      ├──□ gateway-6c69c8666b-f2bfb  Pod         ✔ Running  5m54s  ready:1/1
      ├──□ gateway-6c69c8666b-hcwlh  Pod         ✔ Running  5m54s  ready:1/1
      ├──□ gateway-6c69c8666b-t7qmq  Pod         ✔ Running  5m54s  ready:1/1
      └──□ gateway-6c69c8666b-xqcgj  Pod         ✔ Running  5m54s  ready:1/1
```

- Output after promote — showing progression to 100%
```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab7)
$ kubectl argo rollouts promote gateway
rollout 'gateway' promoted

axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab7)
$ sleep 35
kubectl argo rollouts get rollout gateway

Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/fifidadan/quickticket-gateway:8604770ddddf34d40922d8003f7c931a962b0885 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE   INFO
⟳ gateway                            Rollout     ✔ Healthy     16m
├──# revision:2
│  └──⧉ gateway-649dc5d6fc           ReplicaSet  ✔ Healthy     12m   stable
│     ├──□ gateway-649dc5d6fc-p5jlf  Pod         ✔ Running     12m   ready:1/1
│     ├──□ gateway-649dc5d6fc-csbzg  Pod         ✔ Running     108s  ready:1/1
│     ├──□ gateway-649dc5d6fc-gpblx  Pod         ✔ Running     108s  ready:1/1
│     ├──□ gateway-649dc5d6fc-clvg7  Pod         ✔ Running     66s   ready:1/1
│     └──□ gateway-649dc5d6fc-wdbkl  Pod         ✔ Running     66s   ready:1/1
└──# revision:1
   └──⧉ gateway-6c69c8666b           ReplicaSet  • ScaledDown  16m
```
- Output after abort — showing instant rollback
```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab7)
$ kubectl argo rollouts abort gateway
rollout 'gateway' aborted

axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab7)
$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/fifidadan/quickticket-gateway:8604770ddddf34d40922d8003f7c931a962b0885 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE  INFO
⟳ gateway                            Rollout     ✖ Degraded    58m
├──# revision:3
│  └──⧉ gateway-57d44769bb           ReplicaSet  • ScaledDown  94s  canary
├──# revision:2
│  └──⧉ gateway-649dc5d6fc           ReplicaSet  ✔ Healthy     54m  stable
│     ├──□ gateway-649dc5d6fc-p5jlf  Pod         ✔ Running     54m  ready:1/1
│     ├──□ gateway-649dc5d6fc-gpblx  Pod         ✔ Running     44m  ready:1/1
│     ├──□ gateway-649dc5d6fc-clvg7  Pod         ✔ Running     43m  ready:1/1
│     ├──□ gateway-649dc5d6fc-wdbkl  Pod         ✔ Running     43m  ready:1/1
│     └──□ gateway-649dc5d6fc-c87j4  Pod         ✔ Running     30s  ready:1/1
└──# revision:1
   └──⧉ gateway-6c69c8666b           ReplicaSet  • ScaledDown  58m
```
- Answer: "How long from abort to all traffic serving the stable version? Compare with git revert rollback from Lab 5."

The abort was instantaneous — as soon as I ran `kubectl argo rollouts abort gateway`, the canary pod was terminated and traffic immediately reverted to the stable pods.

Comparison with git revert 

Argo Rollouts abort: ~1-2 seconds — instant rollback to the previous version

git revert: ~2-3 minutes — required pushing a revert commit, waiting for CI, and ArgoCD sync

Argo Rollouts provides a significantly faster rollback mechanism because it doesn't require a new Git commit, CI build, or ArgoCD sync cycle.
  
## Task 2 — Multi-Step Canary with Observation
- Your multi-step canary strategy YAML
```
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: gateway
  labels:
    app: gateway
    version: "v4"
spec:
  replicas: 5
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
  selector:
    matchLabels:
      app: gateway
  template:
    metadata:
      labels:
        app: gateway
    spec:
      imagePullSecrets:
        - name: ghcr-secret
      containers:
        - name: gateway
          image: ghcr.io/fifidadan/quickticket-gateway:8604770ddddf34d40922d8003f7c931a962b0885
          imagePullPolicy: Always
          ports:
            - containerPort: 8080
          env:
            - name: EVENTS_URL
              value: "http://events:8081"
            - name: PAYMENTS_URL
              value: "http://payments:8082"
            - name: GATEWAY_TIMEOUT_MS
              value: "5000"
            - name: APP_VERSION
              value: "v4"
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 10
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            periodSeconds: 5
            failureThreshold: 2
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 256Mi
---
apiVersion: v1
kind: Service
metadata:
  name: gateway
spec:
  selector:
    app: gateway
  ports:
    - port: 8080
      targetPort: 8080
  type: ClusterIP
```
- Output of kubectl argo rollouts get rollout gateway --watch showing at least 3 steps
```
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/fifidadan/quickticket-gateway:8604770ddddf34d40922d8003f7c931a962b0885 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE  INFO
⟳ gateway                            Rollout     ॥ Paused      57m
├──# revision:3
│  └──⧉ gateway-57d44769bb           ReplicaSet  ✔ Healthy     29s  canary
│     └──□ gateway-57d44769bb-kzjq8  Pod         ✔ Running     28s  ready:1/1
├──# revision:2
│  └──⧉ gateway-649dc5d6fc           ReplicaSet  ✔ Healthy     53m  stable
│     ├──□ gateway-649dc5d6fc-p5jlf  Pod         ✔ Running     53m  ready:1/1
│     ├──□ gateway-649dc5d6fc-gpblx  Pod         ✔ Running     42m  ready:1/1
│     ├──□ gateway-649dc5d6fc-clvg7  Pod         ✔ Running     42m  ready:1/1
│     └──□ gateway-649dc5d6fc-wdbkl  Pod         ✔ Running     42m  ready:1/1
└──# revision:1
   └──⧉ gateway-6c69c8666b           ReplicaSet  • ScaledDown  57m

Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          2/9
  SetWeight:     40
  ActualWeight:  50
Images:          ghcr.io/fifidadan/quickticket-gateway:8604770ddddf34d40922d8003f7c931a962b0885 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       2
  Ready:         4
  Available:     4

NAME                                 KIND        STATUS         AGE   INFO
⟳ gateway                            Rollout     ◌ Progressing  115m
├──# revision:4
│  └──⧉ gateway-69c9d447bc           ReplicaSet  ✔ Healthy      85s   canary
│     ├──□ gateway-69c9d447bc-tb2hc  Pod         ✔ Running      84s   ready:1/1
│     └──□ gateway-69c9d447bc-kff6g  Pod         ✔ Running      10s   ready:1/1
├──# revision:3
│  └──⧉ gateway-57d44769bb           ReplicaSet  • ScaledDown   58m
├──# revision:2
│  └──⧉ gateway-649dc5d6fc           ReplicaSet  ✔ Healthy      111m  stable
│     ├──□ gateway-649dc5d6fc-gpblx  Pod         ✔ Running      100m  ready:1/1
│     ├──□ gateway-649dc5d6fc-clvg7  Pod         ✔ Running      99m   ready:1/1
│     └──□ gateway-649dc5d6fc-wdbkl  Pod         ✔ Running      99m   ready:1/1
└──# revision:1
   └──⧉ gateway-6c69c8666b           ReplicaSet  • ScaledDown   115m
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          3/9
  SetWeight:     40
  ActualWeight:  40
Images:          ghcr.io/fifidadan/quickticket-gateway:8604770ddddf34d40922d8003f7c931a962b0885 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       2
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE   INFO
⟳ gateway                            Rollout     ॥ Paused      115m
├──# revision:4
│  └──⧉ gateway-69c9d447bc           ReplicaSet  ✔ Healthy     90s   canary
│     ├──□ gateway-69c9d447bc-tb2hc  Pod         ✔ Running     89s   ready:1/1
│     └──□ gateway-69c9d447bc-kff6g  Pod         ✔ Running     15s   ready:1/1
├──# revision:3
│  └──⧉ gateway-57d44769bb           ReplicaSet  • ScaledDown  58m
├──# revision:2
│  └──⧉ gateway-649dc5d6fc           ReplicaSet  ✔ Healthy     111m  stable
│     ├──□ gateway-649dc5d6fc-gpblx  Pod         ✔ Running     100m  ready:1/1
│     ├──□ gateway-649dc5d6fc-clvg7  Pod         ✔ Running     99m   ready:1/1
│     └──□ gateway-649dc5d6fc-wdbkl  Pod         ✔ Running     99m   ready:1/1
└──# revision:1
   └──⧉ gateway-6c69c8666b           ReplicaSet  • ScaledDown  115m

Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          5/9
  SetWeight:     60
  ActualWeight:  60
Images:          ghcr.io/fifidadan/quickticket-gateway:8604770ddddf34d40922d8003f7c931a962b0885 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ॥ Paused      116m
├──# revision:4
│  └──⧉ gateway-69c9d447bc           ReplicaSet  ✔ Healthy     2m44s  canary
│     ├──□ gateway-69c9d447bc-tb2hc  Pod         ✔ Running     2m43s  ready:1/1
│     ├──□ gateway-69c9d447bc-kff6g  Pod         ✔ Running     89s    ready:1/1
│     └──□ gateway-69c9d447bc-6mzm9  Pod         ✔ Running     17s    ready:1/1
├──# revision:3
│  └──⧉ gateway-57d44769bb           ReplicaSet  • ScaledDown  59m
├──# revision:2
│  └──⧉ gateway-649dc5d6fc           ReplicaSet  ✔ Healthy     112m   stable
│     ├──□ gateway-649dc5d6fc-gpblx  Pod         ✔ Running     101m   ready:1/1
│     └──□ gateway-649dc5d6fc-clvg7  Pod         ✔ Running     101m   ready:1/1
└──# revision:1
   └──⧉ gateway-6c69c8666b           ReplicaSet  • ScaledDown  116m
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          7/9
  SetWeight:     80
  ActualWeight:  80
Images:          ghcr.io/fifidadan/quickticket-gateway:8604770ddddf34d40922d8003f7c931a962b0885 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       4
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ॥ Paused      117m
├──# revision:4
│  └──⧉ gateway-69c9d447bc           ReplicaSet  ✔ Healthy     3m52s  canary
│     ├──□ gateway-69c9d447bc-tb2hc  Pod         ✔ Running     3m51s  ready:1/1
│     ├──□ gateway-69c9d447bc-kff6g  Pod         ✔ Running     2m37s  ready:1/1
│     ├──□ gateway-69c9d447bc-6mzm9  Pod         ✔ Running     85s    ready:1/1
│     └──□ gateway-69c9d447bc-tw5tk  Pod         ✔ Running     12s    ready:1/1
├──# revision:3
│  └──⧉ gateway-57d44769bb           ReplicaSet  • ScaledDown  60m
├──# revision:2
│  └──⧉ gateway-649dc5d6fc           ReplicaSet  ✔ Healthy     113m   stable
│     └──□ gateway-649dc5d6fc-gpblx  Pod         ✔ Running     102m   ready:1/1
└──# revision:1
   └──⧉ gateway-6c69c8666b           ReplicaSet  • ScaledDown  117m
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          9/9
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/fifidadan/quickticket-gateway:8604770ddddf34d40922d8003f7c931a962b0885 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ✔ Healthy     118m
├──# revision:4
│  └──⧉ gateway-69c9d447bc           ReplicaSet  ✔ Healthy     4m40s  stable
│     ├──□ gateway-69c9d447bc-tb2hc  Pod         ✔ Running     4m39s  ready:1/1
│     ├──□ gateway-69c9d447bc-kff6g  Pod         ✔ Running     3m25s  ready:1/1
│     ├──□ gateway-69c9d447bc-6mzm9  Pod         ✔ Running     2m13s  ready:1/1
│     ├──□ gateway-69c9d447bc-tw5tk  Pod         ✔ Running     60s    ready:1/1
│     └──□ gateway-69c9d447bc-lbvvt  Pod         ✔ Running     17s    ready:1/1
├──# revision:3
│  └──⧉ gateway-57d44769bb           ReplicaSet  • ScaledDown  61m
├──# revision:2
│  └──⧉ gateway-649dc5d6fc           ReplicaSet  • ScaledDown  114m
└──# revision:1
   └──⧉ gateway-6c69c8666b           ReplicaSet  • ScaledDown  118m

```
- Dashboard observation during the rollout

  
| Step | SetWeight | ActualWeight | Canary Pods | Stable Pods | Status |
|:----:|:---------:|:-----------:|:-----------:|:-----------:|:------:|
| 1 | 20% | 20% | 1 | 4 | Paused (60s) |
| 2 | 40% | 40% | 2 | 3 | Progressing |
| 3 | 40% | 40% | 2 | 3 | Paused (60s) |
| 4 | 60% | 60% | 3 | 2 | Paused (60s) |
| 5 | 80% | 80% | 4 | 1 | Paused (30s) |
| 6 | 100% | 100% | 5 | 0 | Healthy |




- Answer: "At what canary percentage would you want an automated abort? Why?"

I would set an automated abort at 20% or 40%.

At 20%, only a small portion of traffic is affected, so if errors are detected, we can abort early with minimal user impact. This is the safest approach — catch problems before they reach a wider audience. The 60% and 80% steps are riskier because they affect the majority of users.

## Bonus Task — Automated Canary Analysis
- kubectl get analysistemplate gateway-error-rate output
```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab7)
$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   9s
```
- kubectl get analysisrun output showing Successful run (good canary) and Failed run (bad canary)
```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab7)
$ kubectl get analysisrun
NAME                     STATUS       AGE
gateway-65578565d6-5-2   Failed       16m
gateway-7c5778d94f-6-2   Successful   3m35s
```

- kubectl get analysisrun <failed-name> -o yaml showing the measurement values = [1]

```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab7)
$ kubectl get analysisrun gateway-65578565d6-5-2 -o yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisRun
metadata:
  annotations:
    rollout.argoproj.io/revision: "5"
  creationTimestamp: "2026-06-30T12:55:14Z"
  generation: 4
  labels:
    app: gateway
    rollout-type: Step
    rollouts-pod-template-hash: 65578565d6
    step-index: "2"
  name: gateway-65578565d6-5-2
  namespace: default
  ownerReferences:
  - apiVersion: argoproj.io/v1alpha1
    blockOwnerDeletion: true
    controller: true
    kind: Rollout
    name: gateway
    uid: 81a40f9f-d983-488d-870f-818a0edd1445
  resourceVersion: "102485"
  uid: 47444160-152c-4a63-9774-59a3bd0062fb
spec:
  args:
  - name: canary-hash
    value: 65578565d6
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
  completedAt: "2026-06-30T12:56:34Z"
  dryRunSummary: {}
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
  - count: 2
    failed: 2
    measurements:
    - finishedAt: "2026-06-30T12:56:14Z"
      phase: Failed
      startedAt: "2026-06-30T12:56:14Z"
      value: '[0.0625]'
    - finishedAt: "2026-06-30T12:56:34Z"
      phase: Failed
      startedAt: "2026-06-30T12:56:34Z"
      value: '[0.0625]'
    metadata:
      ResolvedPrometheusQuery: |
        (
          sum(rate(gateway_requests_total{rs_hash="65578565d6",status=~"5.."}[60s]))
          or on() vector(0)
        )
        /
        sum(rate(gateway_requests_total{rs_hash="65578565d6"}[60s]))
    name: error-rate
    phase: Failed
  phase: Failed
  runSummary:
    count: 1
    failed: 1
  startedAt: "2026-06-30T12:55:14Z"

```

- Final kubectl argo rollouts get rollout gateway after the aborted bad deploy (Degraded, stable pods running)

```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab7)
$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 5: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/fifidadan/quickticket-gateway:8604770ddddf34d40922d8003f7c931a962b0885 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS        AGE    INFO
⟳ gateway                            Rollout      ✖ Degraded    160m
├──# revision:5
│  ├──⧉ gateway-65578565d6           ReplicaSet   • ScaledDown  2m32s  canary
│  └──α gateway-65578565d6-5-2       AnalysisRun  ✖ Failed      119s   ✖ 2
├──# revision:4
│  └──⧉ gateway-69c9d447bc           ReplicaSet   ✔ Healthy     46m    stable
│     ├──□ gateway-69c9d447bc-tb2hc  Pod          ✔ Running     46m    ready:1/1
│     ├──□ gateway-69c9d447bc-kff6g  Pod          ✔ Running     44m    ready:1/1
│     ├──□ gateway-69c9d447bc-tw5tk  Pod          ✔ Running     42m    ready:1/1
│     ├──□ gateway-69c9d447bc-lbvvt  Pod          ✔ Running     41m    ready:1/1
│     └──□ gateway-69c9d447bc-gp94g  Pod          ✔ Running     37s    ready:1/1
├──# revision:3
│  └──⧉ gateway-57d44769bb           ReplicaSet   • ScaledDown  102m
├──# revision:2
│  └──⧉ gateway-649dc5d6fc           ReplicaSet   • ScaledDown  155m
└──# revision:1
   └──⧉ gateway-6c69c8666b           ReplicaSet   • ScaledDown  160m

```

- Answer: "What metric would you add beyond error rate for a more complete canary analysis?"

I would add latency (p99) as a metric for canary analysis.

A canary could have a low error rate but still be slower than the stable version (e.g., due to performance regressions or inefficient database queries). By monitoring latency percentiles, we can detect performance degradation before users experience it.
