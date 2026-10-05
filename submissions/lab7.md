## Task 1 — Manual Canary Deployment

1. Output of `kubectl argo rollouts version`
```bash
$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:08:11Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: linux/amd64
```

2. Output of `kubectl argo rollouts get rollout gateway` showing Paused at 20% (during canary)
```bash
$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/ya-rav/quickticket-gateway:ec1eff1f8f289b277f31d8c3fef3bf5aad83da6d (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5
NAME                                KIND        STATUS     AGE    INFO
⟳ gateway                           Rollout     ॥ Paused   7m57s
├──# revision:2
│  └──⧉ gateway-7cd5f99b8           ReplicaSet  ✔ Healthy  63s    canary
│     └──□ gateway-7cd5f99b8-64l7v  Pod         ✔ Running  63s    ready:1/1
└──# revision:1
   └──⧉ gateway-64787cc64           ReplicaSet  ✔ Healthy  7m57s  stable
      ├──□ gateway-64787cc64-bldhg  Pod         ✔ Running  7m56s  ready:1/1
      ├──□ gateway-64787cc64-f59jm  Pod         ✔ Running  7m56s  ready:1/1
      ├──□ gateway-64787cc64-mbwrn  Pod         ✔ Running  7m56s  ready:1/1
      └──□ gateway-64787cc64-rl5xf  Pod         ✔ Running  7m56s  ready:1/1
```

3. Output after `promote` — showing progression to 100%
```bash
$ kubectl argo rollouts promote gateway
rollout 'gateway' promoted

$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/ya-rav/quickticket-gateway:ec1eff1f8f289b277f31d8c3fef3bf5aad83da6d (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                KIND        STATUS        AGE    INFO
⟳ gateway                           Rollout     ✔ Healthy     14m
├──# revision:2
│  └──⧉ gateway-7cd5f99b8           ReplicaSet  ✔ Healthy     7m19s  stable
│     ├──□ gateway-7cd5f99b8-64l7v  Pod         ✔ Running     7m19s  ready:1/1
│     ├──□ gateway-7cd5f99b8-8pcm9  Pod         ✔ Running     52s    ready:1/1
│     ├──□ gateway-7cd5f99b8-z6cxp  Pod         ✔ Running     52s    ready:1/1
│     ├──□ gateway-7cd5f99b8-j7jj5  Pod         ✔ Running     11s    ready:1/1
│     └──□ gateway-7cd5f99b8-s4dnz  Pod         ✔ Running     11s    ready:1/1
└──# revision:1
   └──⧉ gateway-64787cc64           ReplicaSet  • ScaledDown  14m
```

4. Output after `abort` — showing instant rollback
```bash
$ kubectl argo rollouts abort gateway
rollout 'gateway' aborted

$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/ya-rav/quickticket-gateway:ec1eff1f8f289b277f31d8c3fef3bf5aad83da6d (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                KIND        STATUS        AGE    INFO
⟳ gateway                           Rollout     ✖ Degraded    17m
├──# revision:3
│  └──⧉ gateway-788dc74449          ReplicaSet  • ScaledDown  103s   canary
├──# revision:2
│  └──⧉ gateway-7cd5f99b8           ReplicaSet  ✔ Healthy     10m    stable
│     ├──□ gateway-7cd5f99b8-64l7v  Pod         ✔ Running     10m    ready:1/1
│     ├──□ gateway-7cd5f99b8-z6cxp  Pod         ✔ Running     4m2s   ready:1/1
│     ├──□ gateway-7cd5f99b8-j7jj5  Pod         ✔ Running     3m21s  ready:1/1
│     ├──□ gateway-7cd5f99b8-s4dnz  Pod         ✔ Running     3m21s  ready:1/1
│     └──□ gateway-7cd5f99b8-7jqcn  Pod         ✔ Running     16s    ready:1/1
└──# revision:1
   └──⧉ gateway-64787cc64           ReplicaSet  • ScaledDown  17m
```

5. Answer: "How long from `abort` to all traffic serving the stable version? Compare with `git revert` rollback from Lab 5."

**Answer:** Rolling back via `abort` takes effect practically immediately (within a few seconds). 
Upon executing abort, the controller sets ActualWeight back to 0, immediately scales down the canary ReplicaSet, and redirects all traffic to the stable ReplicaSet (revision 2), which already has all 5 pods active and ready. Because the stable pods remain warm throughout the canary rollout, reverting requires zero scheduling overhead or image pull delay.

In contrast, the Lab 5 `git revert` rollback is fundamentally slower and heavier because it traverses the entire GitOps pipeline (Git commit → GitHub push → ArgoCD sync interval → Kubernetes reconciliation).

The primary architectural difference is **durability versus immediacy**:
* `abort` is a direct imperative instruction to the Argo Rollouts controller in the cluster. It provides an immediate circuit breaker for production issues, but introduces configuration drift between the cluster and Git. On the next automated ArgoCD reconciliation, the canary could be retried unless paused.
* `git revert` operates slower due to pipeline latency, but modifies the declarative single source of truth in Git, ensuring the rollback is durable and consistent across future sync cycles.

## Task 2 — Multi-Step Canary with Observation

1. Multi-step canary strategy YAML (`k8s/gateway.yaml`):

```yaml
strategy:
  canary:
    steps:
      - setWeight: 20
      - pause: { duration: 60s }    # Observe for 1 min
      - setWeight: 40
      - pause: { duration: 60s }
      - setWeight: 60
      - pause: { duration: 60s }
      - setWeight: 80
      - pause: { duration: 30s }
      - setWeight: 100
```

2. Output of `kubectl argo rollouts get rollout gateway --watch` across 3 steps (20% → 40% → 60%):

```bash
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/9
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/ya-rav/quickticket-gateway:ec1eff1f8f289b277f31d8c3fef3bf5aad83da6d (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE  INFO
⟳ gateway                            Rollout     ॥ Paused      93m
├──# revision:5
│  └──⧉ gateway-796d5bb8fd           ReplicaSet  ✔ Healthy     43s  canary
│     └──□ gateway-796d5bb8fd-xvxgp  Pod         ✔ Running     40s  ready:1/1
├──# revision:4
│  └──⧉ gateway-7cb567cbc            ReplicaSet  ✔ Healthy     44m  stable
│     ├──□ gateway-7cb567cbc-k5mkk   Pod         ✔ Running     44m  ready:1/1
│     ├──□ gateway-7cb567cbc-6n4dg   Pod         ✔ Running     43m  ready:1/1
│     ├──□ gateway-7cb567cbc-f9sqf   Pod         ✔ Running     41m  ready:1/1
│     └──□ gateway-7cb567cbc-lnn29   Pod         ✔ Running     40m  ready:1/1
├──# revision:3
│  └──⧉ gateway-788dc74449           ReplicaSet  • ScaledDown  77m
├──# revision:2
│  └──⧉ gateway-7cd5f99b8            ReplicaSet  • ScaledDown  86m
└──# revision:1
   └──⧉ gateway-64787cc64            ReplicaSet  • ScaledDown  93m


Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          3/9
  SetWeight:     40
  ActualWeight:  40
Images:          ghcr.io/ya-rav/quickticket-gateway:ec1eff1f8f289b277f31d8c3fef3bf5aad83da6d (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       2
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE  INFO
⟳ gateway                            Rollout     ॥ Paused      94m
├──# revision:5
│  └──⧉ gateway-796d5bb8fd           ReplicaSet  ✔ Healthy     94s  canary
│     ├──□ gateway-796d5bb8fd-xvxgp  Pod         ✔ Running     91s  ready:1/1
│     └──□ gateway-796d5bb8fd-jmxrl  Pod         ✔ Running     19s  ready:1/1
├──# revision:4
│  └──⧉ gateway-7cb567cbc            ReplicaSet  ✔ Healthy     45m  stable
│     ├──□ gateway-7cb567cbc-k5mkk   Pod         ✔ Running     45m  ready:1/1
│     ├──□ gateway-7cb567cbc-6n4dg   Pod         ✔ Running     44m  ready:1/1
│     └──□ gateway-7cb567cbc-f9sqf   Pod         ✔ Running     41m  ready:1/1
├──# revision:3
│  └──⧉ gateway-788dc74449           ReplicaSet  • ScaledDown  78m
├──# revision:2
│  └──⧉ gateway-7cd5f99b8            ReplicaSet  • ScaledDown  87m
└──# revision:1
   └──⧉ gateway-64787cc64            ReplicaSet  • ScaledDown  94m


Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          5/9
  SetWeight:     60
  ActualWeight:  60
Images:          ghcr.io/ya-rav/quickticket-gateway:ec1eff1f8f289b277f31d8c3fef3bf5aad83da6d (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE   INFO
⟳ gateway                            Rollout     ॥ Paused      96m
├──# revision:5
│  └──⧉ gateway-796d5bb8fd           ReplicaSet  ✔ Healthy     3m7s  canary
│     ├──□ gateway-796d5bb8fd-xvxgp  Pod         ✔ Running     3m4s  ready:1/1
│     ├──□ gateway-796d5bb8fd-jmxrl  Pod         ✔ Running     112s  ready:1/1
│     └──□ gateway-796d5bb8fd-qqd8q  Pod         ✔ Running     41s   ready:1/1
├──# revision:4
│  └──⧉ gateway-7cb567cbc            ReplicaSet  ✔ Healthy     47m   stable
│     ├──□ gateway-7cb567cbc-k5mkk   Pod         ✔ Running     47m   ready:1/1
│     └──□ gateway-7cb567cbc-6n4dg   Pod         ✔ Running     45m   ready:1/1
├──# revision:3
│  └──⧉ gateway-788dc74449           ReplicaSet  • ScaledDown  80m
├──# revision:2
│  └──⧉ gateway-7cd5f99b8            ReplicaSet  • ScaledDown  89m
└──# revision:1
   └──⧉ gateway-64787cc64            ReplicaSet  • ScaledDown  96m
```

3. Dashboard / rollout observation:

Traffic throughput remained steady across each step of the rollout. The `Ready` and `Available` counts remained at 5 replicas throughout the whole process, ensuring no dropped user requests while shifting weights. The `Updated` replica count increased strictly according to the defined canary weights: 1 pod at 20%, 2 pods at 40%, 3 pods at 60% (progressing to 4 at 80% and all 5 at 100%). Each configured `pause` step held its traffic allocation for the full time window, giving a dedicated observation period to verify metrics before promoting further. Throughout the procedure, the stable ReplicaSet (revision 4) remained healthy and ready to serve 100% of traffic immediately if aborted.

4. Answer: "At what canary percentage would you want an automated abort? Why?"

An automated abort should be triggered at the **earliest step — 20%** (the smallest weight that provides a statistically valid sample of traffic for error rates and latency). The purpose of a canary deployment is minimizing the blast radius and preventing customer impact. At 20%, only 1 out of 5 pods runs the new version, isolating failures to a minor fraction of traffic while keeping the stable revision running at full capacity for instant fallback. Waiting until 60–80% means the majority of active users are already exposed to errors, defeating the proactive purpose of canary testing.

## Bonus Task — Automated Canary Analysis

An `AnalysisTemplate` queries the cluster Prometheus for the canary-specific 5xx error ratio (`gateway_requests_total{rs_hash=...,status=~"5.."}`) during deployment, scoped to canary pods using the ReplicaSet hash (`rs_hash`). A healthy version auto-promotes; a regression triggers an automated abort. Below are runs for `gateway-5b7d9d89f6-9-2` (**Successful**, healthy canary → auto-promoted) and `gateway-756d5575-12-2` (**Failed**, faulty canary → auto-aborted). `gateway-6ddcb559fc-8-2` represents an earlier failed trial.

- `kubectl get analysistemplate gateway-error-rate` output
```bash
$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   18s
```

- `kubectl get analysisrun` output showing **Successful** run (good canary) and **Failed** run (bad canary)
```bash
$ kubectl get analysisrun
NAME                     STATUS       AGE
gateway-5b7d9d89f6-9-2   Successful   29m
gateway-6ddcb559fc-8-2   Failed       37m
gateway-756d5575-12-2    Failed       9m10s
```

- `kubectl get analysisrun <failed-name> -o yaml` showing the measurement values = `[1]`
```yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisRun
metadata:
  annotations:
    rollout.argoproj.io/revision: "12"
  creationTimestamp: "2026-10-04T18:57:42Z"
  generation: 4
  labels:
    app: gateway
    rollout-type: Step
    rollouts-pod-template-hash: 756d5575
    step-index: "2"
  name: gateway-756d5575-12-2
  namespace: default
  ownerReferences:
  - apiVersion: argoproj.io/v1alpha1
    blockOwnerDeletion: true
    controller: true
    kind: Rollout
    name: gateway
    uid: c7465f58-2d02-4161-b7a4-83fb583b6a79
  resourceVersion: "11911"
  uid: a92e9a0c-ed36-40e2-b2f0-7edb3fe59e30
spec:
  args:
  - name: canary-hash
    value: 756d5575
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
  completedAt: "2026-10-04T18:59:02Z"
  dryRunSummary: {}
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
  - count: 2
    failed: 2
    measurements:
    - finishedAt: "2026-10-04T18:58:42Z"
      phase: Failed
      startedAt: "2026-10-04T18:58:42Z"
      value: '[0.3867924528301887]'
    - finishedAt: "2026-10-04T18:59:02Z"
      phase: Failed
      startedAt: "2026-10-04T18:59:02Z"
      value: '[0.4158415841584159]'
    metadata:
      ResolvedPrometheusQuery: |
        (
          sum(rate(gateway_requests_total{rs_hash="756d5575",status=~"5.."}[60s]))
          or on() vector(0)
        )
        /
        sum(rate(gateway_requests_total{rs_hash="756d5575"}[60s]))
    name: error-rate
    phase: Failed
  phase: Failed
  runSummary:
    count: 1
    failed: 1
  startedAt: "2026-10-04T18:57:42Z"
```

> Note on the measured values (`~0.39` and `~0.42`, both well above the `0.05` threshold): the loadgen alternates between `GET /events` and `GET /health`. On the degraded canary, `/events` responds with **502** while `/health` still returns **200**, resulting in roughly half of the canary's requests failing with 5xx status codes. It clearly exceeds the 5% threshold, tripping `failed (2) > failureLimit (1)` and auto-aborting the rollout.

- Final `kubectl argo rollouts get rollout gateway` after the aborted bad deploy (Degraded, stable pods running)
```bash
$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 12: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/ya-rav/quickticket-gateway:ec1eff1f8f289b277f31d8c3fef3bf5aad83da6d (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         4
  Available:     4

NAME                                 KIND         STATUS               AGE   INFO
⟳ gateway                            Rollout      ✖ Degraded           158m
├──# revision:12
│  ├──⧉ gateway-756d5575             ReplicaSet   • ScaledDown         115s  canary
│  └──α gateway-756d5575-12-2        AnalysisRun  ✖ Failed             84s   ✖ 2
├──# revision:11
│  └──⧉ gateway-5b7d9d89f6           ReplicaSet   ◌ Progressing        21m   stable
│     ├──□ gateway-5b7d9d89f6-4hcrm  Pod          ✔ Running            21m   ready:1/1
│     ├──□ gateway-5b7d9d89f6-826fq  Pod          ✔ Running            19m   ready:1/1
│     ├──□ gateway-5b7d9d89f6-4qfkr  Pod          ✔ Running            19m   ready:1/1
│     ├──□ gateway-5b7d9d89f6-mklm8  Pod          ✔ Running            19m   ready:1/1
│     └──□ gateway-5b7d9d89f6-f7m76  Pod          ◌ ContainerCreating  2s    ready:0/1
├──# revision:10
│  └──⧉ gateway-7cbff76f68           ReplicaSet   • ScaledDown         16m
├──# revision:9
│  └──α gateway-5b7d9d89f6-9-2       AnalysisRun  ✔ Successful         21m   ✔ 3
├──# revision:8
│  ├──⧉ gateway-6ddcb559fc           ReplicaSet   • ScaledDown         30m
│  └──α gateway-6ddcb559fc-8-2       AnalysisRun  ✖ Failed             30m   ✖ 2
├──# revision:7
│  └──⧉ gateway-796d5bb8fd           ReplicaSet   • ScaledDown         65m
├──# revision:6
│  └──⧉ gateway-56dbc6f6fd           ReplicaSet   • ScaledDown         40m
├──# revision:4
│  └──⧉ gateway-7cb567cbc            ReplicaSet   • ScaledDown         109m
├──# revision:3
│  └──⧉ gateway-788dc74449           ReplicaSet   • ScaledDown         142m
├──# revision:2
│  └──⧉ gateway-7cd5f99b8            ReplicaSet   • ScaledDown         151m
└──# revision:1
   └──⧉ gateway-64787cc64            ReplicaSet   • ScaledDown         158m
```

- Answer: "What metric would you add beyond error rate for a more complete canary analysis?"

**Answer:** Beyond tracking the 5xx error rate, the single most critical metric to add is **request latency (p95 and p99 percentiles)**.

Evaluating error rates alone introduces a major blind spot: a new deployment could return `200 OK` on every request while suffering from critical latency regressions (such as database deadlocks, unindexed queries, or garbage collection spikes). In that situation, the error rate remains 0%, causing an unhealthy canary to pass analysis and be promoted to 100% of users.

In practice, this is implemented by configuring an additional metric in the `AnalysisTemplate` querying `gateway_request_duration_seconds_bucket`, scoped to the canary replicas:

```promql
histogram_quantile(0.99,
  sum(rate(gateway_request_duration_seconds_bucket{rs_hash="{{args.canary-hash}}"}[60s])) by (le)
)
```

With an evaluation constraint like `successCondition: result[0] < 0.3` (p99 latency below 300 ms), the rollout automatically aborts if the canary introduces errors **or** becomes too slow.

Other valuable signals for an even more robust rollout include:
- **Saturation** — tracking CPU and memory consumption of the canary pods to detect resource exhaustion before errors occur.
- **Traffic throughput** — ensuring requests/sec through the canary match the configured canary weight, catching silent drops or connection rejects.
