# Lab 7 - Progressive Delivery: Canary Deployments

**Student:** Diana Kalugina

## Task 1 - Manual Canary Deployment

### 1. Argo Rollouts installation

I installed Argo Rollouts in the `argo-rollouts` namespace and verified both the controller and the kubectl plugin.

```text
kubectl-argo-rollouts: v1.10.0+d90700a
BuildDate: 2026-08-27T15:22:01Z
GitCommit: d90700ae8d71d141561f0c546e19f999bb335cbd
GitTreeState: clean
GoVersion: go1.26.7
Compiler: gc
Platform: linux/amd64
```

Controller status:

```text
NAME                             READY   STATUS    RESTARTS
argo-rollouts-69645d4879-8kmd7   1/1     Running   0
```

Required CRDs were also available:

```text
rollouts.argoproj.io
analysisruns.argoproj.io
analysistemplates.argoproj.io
```

### 2. Gateway conversion

I replaced the existing Gateway Deployment with an Argo Rollout and increased the replica count to five.

For the manual experiment I first used this canary sequence:

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

With five replicas, a 20% step corresponds to one canary Pod.

### 3. Canary paused at 20%

Changing `APP_VERSION` created a new pod-template revision. The rollout stopped at the manual pause as expected:

```text
canary_started=2026-10-05T07:04:12Z
Status: Paused
Message: CanaryPauseStep
Step: 1/5
SetWeight: 20
ActualWeight: 20
Desired: 5
Updated: 1
Ready: 5
Available: 5
```

At this point the rollout had four stable Pods and one canary Pod.

### 4. Traffic split

I used the provided in-cluster load generator so the traffic passed through Kubernetes Service balancing.

A short observation window produced these request counts:

| Pod role | Requests |
|---|---:|
| Stable Pod 1 | 24 |
| Stable Pod 2 | 22 |
| Stable Pod 3 | 27 |
| Stable Pod 4 | 25 |
| Canary Pod | 24 |
| **Total** | **122** |

The canary received `24 / 122 = 19.67%` of the observed requests. This is close to the configured 20% weight.

### 5. Manual promotion

I promoted the rollout with:

```bash
kubectl argo rollouts promote gateway
```

The rollout moved to 60% and then continued automatically after the timed pause.

```text
promotion_started=2026-10-05T07:09:31Z
Status: Progressing
Step: 3/5
SetWeight: 60
ActualWeight: 60
Updated: 3
Ready: 5
```

Final state:

```text
promotion_completed=2026-10-05T07:10:20Z
Status: Healthy
Step: 5/5
SetWeight: 100
ActualWeight: 100
Desired: 5
Updated: 5
Ready: 5
Available: 5
```

The observed promotion took about 49 seconds.

### 6. Bad version and abort

I created a bad canary by changing the dependency configuration:

```text
APP_VERSION=v3-bad
EVENTS_URL=http://broken-on-purpose:8081
GATEWAY_TIMEOUT_MS=2000
```

The canary stayed available for measurement, but requests to `/events` returned a 5xx response.

I then aborted the rollout:

```bash
kubectl argo rollouts abort gateway
```

Observed result:

```text
abort_started=2026-10-05T07:14:47Z
stable_only_ready_endpoints_after=1.21s
full_stable_capacity_after=9.74s

Status: Degraded
Message: RolloutAborted
SetWeight: 0
ActualWeight: 0
Updated: 0
Ready: 5
```

After the abort, ready traffic endpoints belonged only to the stable revision.

### 7. Abort compared with Lab 5 Git revert

My Lab 5 Git revert recovery took approximately 17 seconds. The Rollout abort restored stable-only traffic after about 1.21 seconds, and all five stable replicas were available after about 9.74 seconds.

The Rollout abort was faster because it reused the already running stable ReplicaSet. Git revert recovery also depends on a new Git commit, push, Argo CD synchronization, and Kubernetes reconciliation.

## Task 2 - Multi-Step Canary with Observation

### 1. Final strategy

For the multi-step experiment I used:

```yaml
strategy:
  canary:
    maxSurge: 0
    maxUnavailable: 1
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

### 2. Step observations

I watched the rollout with `kubectl argo rollouts get rollout gateway --watch` while continuous in-cluster traffic was running.

| UTC time | State | Weight | Updated | Ready | Requests/s | 5xx ratio |
|---|---|---:|---:|---:|---:|---:|
| 07:20:14 | Paused | 20% | 1 | 5 | 4.84 | 0% |
| 07:21:47 | Paused | 40% | 2 | 5 | 5.02 | 0.21% |
| 07:23:01 | Paused | 60% | 3 | 5 | 4.93 | 0% |
| 07:24:13 | Paused | 80% | 4 | 5 | 5.11 | 0% |
| 07:24:55 | Healthy | 100% | 5 | 5 | 4.97 | 0% |

Representative rollout output:

```text
Status: Paused     Step: 1/10    SetWeight: 20    ActualWeight: 20
Updated: 1         Ready: 5      Available: 5

Status: Paused     Step: 4/10    SetWeight: 40    ActualWeight: 40
Updated: 2         Ready: 5      Available: 5

Status: Paused     Step: 6/10    SetWeight: 60    ActualWeight: 60
Updated: 3         Ready: 5      Available: 5

Status: Paused     Step: 8/10    SetWeight: 80    ActualWeight: 80
Updated: 4         Ready: 5      Available: 5

Status: Healthy    Step: 10/10   SetWeight: 100   ActualWeight: 100
Updated: 5         Ready: 5      Available: 5
```

The updated replica count followed the expected `1 -> 2 -> 3 -> 4 -> 5` sequence.

### 3. Observation and abort threshold

The request rate stayed close to five requests per second during the rollout. A small temporary error increase was visible around the 40% stage, but it returned to zero before the next observation point.

I would start automated analysis at 20% and abort before increasing exposure if the canary error rate remains above the configured limit. This keeps the initial blast radius small while still providing real traffic for analysis.

## Bonus - Automated Canary Analysis

### 1. AnalysisTemplate

I created `k8s/analysis-template.yaml` and passed the newest ReplicaSet hash through `podTemplateHashValue: Latest`.

```text
$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   7m18s
```

The analysis uses:

- 60 seconds initial delay
- 20 seconds between measurements
- 3 measurements
- success below 5% error rate
- failure limit of 1
- fail-safe behavior when the canary receives no measurable traffic

The numerator treats a missing 5xx series as zero errors, while the denominator remains strict.

### 2. Good canary auto-promotion

The good canary completed all measurements successfully:

```text
NAME                       STATUS
gateway-6c8d7d5d7b-6-2     Successful
```

Measurements:

```text
2026-10-05T07:29:44Z  Successful  [0]
2026-10-05T07:30:04Z  Successful  [0]
2026-10-05T07:30:24Z  Successful  [0]
```

After the successful AnalysisRun, the rollout continued without manual promotion and reached 100%.

### 3. Bad canary automatic abort

The next revision used the broken `EVENTS_URL` configuration. The canary produced real 5xx responses.

```text
$ kubectl get analysisrun
NAME                       STATUS
gateway-75f8d4f79c-7-2     Failed
gateway-6c8d7d5d7b-6-2     Successful
```

Relevant measurements:

```yaml
status:
  message: 'Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)'
  metricResults:
    - count: 2
      failed: 2
      measurements:
        - phase: Failed
          value: '[0.274]'
        - phase: Failed
          value: '[0.301]'
      phase: Failed
  phase: Failed
```

Argo Rollouts automatically stopped the bad revision:

```text
Status: Degraded
Message: RolloutAborted: Step-based analysis phase error/failed
SetWeight: 0
ActualWeight: 0
Updated: 0
Ready: 5
```

I restored the healthy manifest after capturing the failed analysis state.

Final verification:

```text
Status: Healthy
SetWeight: 100
ActualWeight: 100
Desired: 5
Updated: 5
Ready: 5
Available: 5
```

### 4. Additional metric

Beyond error rate, I would add p95 request latency for the canary revision. A deployment can return successful responses while becoming much slower. I would also require a minimum request volume before treating an analysis as successful.

## Final Verification

- Argo Rollouts plugin `v1.10.0` installed
- Argo Rollouts controller running
- Gateway converted to a five-replica Rollout
- 20% manual canary observed
- Traffic split observed close to 20%
- Manual promotion completed to 100%
- Bad canary aborted
- 20%, 40%, 60%, 80%, and 100% stages observed
- Good automated analysis succeeded
- Bad automated analysis failed and triggered automatic abort
- Final Gateway state Healthy with 5/5 Ready
- No credentials or tokens are stored in the submitted files

## Conclusion

The lab demonstrated both manual and automated progressive delivery with Argo Rollouts. Manual canary deployment limited the initial impact of a new version and provided a faster rollback path than the Git revert workflow from Lab 5. The multi-step rollout showed controlled replica replacement, and Prometheus-based analysis automatically promoted a healthy canary while rejecting a bad one before full rollout.
