## Task 1 — Manual Canary Deployment

1. Output of `kubectl argo rollouts version`
```commandline
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:08:11Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: linux/amd64
```
2. Output of `kubectl argo rollouts get rollout gateway` showing Paused at 20% (during canary)
```commandline
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/tulup-404/quickticket-gateway:ec1eff1f8f289b277f31d8c3fef3bf5aad83da6d (canary, stable)
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
```commandline
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ kubectl argo rollouts promote gateway
rollout 'gateway' promoted

Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/tulup-404/quickticket-gateway:ec1eff1f8f289b277f31d8c3fef3bf5aad83da6d (stable)
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
```commandline
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ kubectl argo rollouts abort gateway
rollout 'gateway' aborted

timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/tulup-404/quickticket-gateway:ec1eff1f8f289b277f31d8c3fef3bf5aad83da6d (stable)
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

**Answer:** Rollback via abort is effectively instant (a few seconds). 
As shown above, right after abort the rollout went to ActualWeight: 0, 
the canary ReplicaSet was already ScaledDown, and the stable one 
stayed Healthy with all 5 pods ready. The stable ReplicaSet is kept running 
the whole canary, so abort just shifts 100% of traffic back to it — 
no new pods to schedule.

The Lab 5 `git revert` rollback was also quick but heavier: it goes through 
the full GitOps loop (commit → push → ArgoCD sync → k8s reconcile).

The key difference is durability and source of truth. `abort` is a direct 
command to the Rollout controller — an immediate in-cluster undo — but it 
does NOT change Git. The cluster now diverges from the desired state in the 
repo, and on the next ArgoCD sync the canary will be rolled out again. 
So abort is ideal for instantly stopping a bad rollout, but it's temporary. 
`git revert` is slower because of pipeline latency, but it changes the 
source of truth, so the rollback is durable and the cluster reconverges 
with Git.

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

```commandline
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/9
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/tulup-404/quickticket-gateway:ec1eff1f8f289b277f31d8c3fef3bf5aad83da6d (canary, stable)
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
Images:          ghcr.io/tulup-404/quickticket-gateway:ec1eff1f8f289b277f31d8c3fef3bf5aad83da6d (canary, stable)
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
Images:          ghcr.io/tulup-404/quickticket-gateway:ec1eff1f8f289b277f31d8c3fef3bf5aad83da6d (canary, stable)
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

The request rate stayed steady across all canary steps — `Ready` and `Available`
held at 5 pods the whole time, so no traffic was dropped as the weight increased.
The `Updated` replica count climbed exactly in step with the weight: 1 pod at 20%,
2 pods at 40%, 3 pods at 60% (and would reach 4 at 80% and all 5 at 100%). Each
`pause` step held its weight for the full duration before advancing, giving a
window to inspect the canary before more traffic shifted onto it. Throughout, the
stable ReplicaSet (revision 4) stayed Healthy, so an abort at any step would have
returned all traffic to it instantly.

4. Answer: "At what canary percentage would you want an automated abort? Why?"

I would wire the automated abort at the **first step — 20%** (the smallest weight
that still carries enough traffic to give a reliable error-rate/latency signal).
The whole point of a canary is to catch a bad version while it affects the fewest
users, so the abort gate should sit as early as the metrics are trustworthy. At
20% only ~1 of 5 pods serves the new version, so aborting there limits the blast
radius to a fraction of requests, and the stable ReplicaSet is still fully up for
an instant rollback. Waiting until 60–80% means the bad version is already serving
most of the traffic — by then an "automated abort" is damage control, not
prevention.

## Bonus Task — Automated Canary Analysis

An `AnalysisTemplate` queries in-cluster Prometheus for the canary's 5xx ratio
(`gateway_requests_total{rs_hash=...,status=~"5.."}`) during the rollout, scoped
to the canary replicas via `rs_hash`. A good version auto-promotes; a bad one
auto-aborts. The runs below: `gateway-5b7d9d89f6-9-2` (**Successful**, good
canary → auto-promote) and `gateway-756d5575-12-2` (**Failed**, bad canary →
auto-abort). `gateway-6ddcb559fc-8-2` is an earlier failed run.

- `kubectl get analysistemplate gateway-error-rate` output
```commandline
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   7s
```
- `kubectl get analysisrun` output showing **Successful** run (good canary) and **Failed** run (bad canary)
```commandline
timab@LAPTOP-CVMUCS3O:/mnt/c/Users/timab/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro$ kubectl get analysisrun
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
  creationTimestamp: "2026-07-02T18:57:42Z"
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
  completedAt: "2026-07-02T18:59:02Z"
  dryRunSummary: {}
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
  - count: 2
    failed: 2
    measurements:
    - finishedAt: "2026-07-02T18:58:42Z"
      phase: Failed
      startedAt: "2026-07-02T18:58:42Z"
      value: '[0.3867924528301887]'
    - finishedAt: "2026-07-02T18:59:02Z"
      phase: Failed
      startedAt: "2026-07-02T18:59:02Z"
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
  startedAt: "2026-07-02T18:57:42Z"
```
> Note on the measured values (`~0.39` and `~0.42`, both well above the `0.05`
> threshold): the loadgen alternates `GET /events` and `GET /health`. On the bad
> canary `/events` returns **502** while `/health` still returns **200**, so
> roughly half of the canary's requests are 5xx — hence a ratio near `0.4` rather
> than a clean `1.0`. It is still comfortably over the 5% success threshold, so
> `failed (2) > failureLimit (1)` trips and the rollout auto-aborts.

- Final `kubectl argo rollouts get rollout gateway` after the aborted bad deploy (Degraded, stable pods running)
```commandline
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 12: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/tulup-404/quickticket-gateway:ec1eff1f8f289b277f31d8c3fef3bf5aad83da6d (stable)
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

**Answer:** Beyond 5xx error rate, the single most valuable metric to add is
**request latency (p95/p99)**.

Error rate alone has a blind spot: a canary can return `200 OK` on every request
yet be badly degraded — slow DB queries, lock contention, an N+1 query, or GC
pauses from a memory leak. Error rate stays at 0 while real users hit timeouts and
a sluggish UI, so the canary would falsely pass and get auto-promoted. A latency
check catches exactly this class of regression.

Concretely, I'd add a second metric to the AnalysisTemplate using the histogram
the gateway already exports (`gateway_request_duration_seconds`), scoped to the
canary via `rs_hash`:

```promql
histogram_quantile(0.99,
  sum(rate(gateway_request_duration_seconds_bucket{rs_hash="{{args.canary-hash}}"}[60s])) by (le)
)
```

with a `successCondition` like `result[0] < 0.3` (p99 under 300 ms). The rollout
then auto-aborts if the canary is either erroring **or** too slow.

Good runners-up for an even more complete analysis:
- **Saturation** — CPU/memory of the canary pods (catches leaks and resource exhaustion before they turn into errors).
- **Throughput / traffic** — requests/sec the canary serves vs stable at the same weight; a sudden drop signals the canary is silently dropping or not accepting connections.

Together these cover the classic **golden signals** (latency, traffic, errors, saturation) instead of relying on errors alone.
