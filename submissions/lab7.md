# Lab 7 — Progressive Delivery: Canary Deployments

**Student:** Gleb Shvetsov

**GitHub:** `L10nff`

**Working branch:** `feature/lab7`

**Date:** 2026-10-04

## Task 1 — Manual Canary Deployment

### 1. Argo Rollouts installation

Argo Rollouts was installed in the `argo-rollouts` namespace. The controller
became Available and the native Apple Silicon kubectl plugin reported:

```text
kubectl-argo-rollouts: v1.9.0+838d4e7
BuildDate: 2026-03-20T21:11:48Z
GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
GitTreeState: clean
GoVersion: go1.24.13
Compiler: gc
Platform: darwin/arm64
```

Controller verification:

```text
NAME                             READY   STATUS    RESTARTS
argo-rollouts-79b89d8856-z625d   1/1     Running   0
```

### 2. Gateway conversion

The Gateway resource was converted from `apps/v1 Deployment` to
`argoproj.io/v1alpha1 Rollout` with five replicas. The initial manual strategy
was:

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

Argo CD automated reconciliation was temporarily disabled during the local
experiment so the `main` branch Deployment could not overwrite the uncommitted
Rollout. The old Deployment was backed up and then replaced by the Rollout.

```text
migration_started=2026-10-04T12:14:14Z
migration_completed=2026-10-04T12:14:26Z
Status: Healthy
Replicas: Desired=5 Current=5 Updated=5 Ready=5 Available=5
```

### 3. Canary paused at 20%

`APP_VERSION=v2` changed the Pod template and created revision 2. The rollout
stopped at the infinite manual pause:

```text
canary_v2_started=2026-10-04T12:15:50Z
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

The Pod distribution was four stable Pods and one canary Pod:

```text
stable hash:  7db8bbbcc6   pods: 4
canary hash:  7fbfff666d   pods: 1   APP_VERSION=v2
```

### 4. Traffic split

Traffic was sent from the provided in-cluster load generator so requests
passed through Kubernetes Service load balancing rather than a sticky
port-forward connection. A common 30-second log interval produced:

| Pod role | Requests |
|---|---:|
| Stable Pod 1 | 28 |
| Stable Pod 2 | 27 |
| Stable Pod 3 | 26 |
| Stable Pod 4 | 19 |
| Canary Pod | 28 |
| **Total** | **128** |

The canary handled `28 / 128 = 21.88%` of requests, close to the configured
20% weight and within normal short-sample variance.

### 5. Manual promotion

Manual promotion moved the rollout to 60%:

```text
promotion_started=2026-10-04T12:21:36Z
Status: Progressing
Step: 3/5
SetWeight: 60
ActualWeight: 60
Updated: 3
Ready: 5
```

After the timed pause it continued automatically to 100%:

```text
promotion_completed=2026-10-04T12:22:27Z
Status: Healthy
Step: 5/5
SetWeight: 100
ActualWeight: 100
Updated: 5
Ready: 5
Available: 5
```

The complete promotion took 51 seconds from the manual command to the observed
Healthy state.

### 6. Bad canary and manual abort

A real failing revision was created with `APP_VERSION=v3-bad` and
`GATEWAY_TIMEOUT_MS=0`. The health endpoint remained suitable for Pod
readiness because its dependency calls use explicit timeouts, while `/events`
used the zero default client timeout and returned a measurable 504:

```text
bad_canary_started=2026-10-04T12:27:34Z
Status: Paused
SetWeight: 20
ActualWeight: 20
Updated: 1
Ready: 5

Bad canary /events response:
{"detail":"Events service timeout"}
http_code=504
```

The abort began at `12:31:11.318Z`. EndpointSlice observations showed:

```text
elapsed_seconds=1.14  ready_endpoints=5  stable_endpoints=4
elapsed_seconds=3.35  ready_endpoints=4  stable_endpoints=4
elapsed_seconds=12.13 ready_endpoints=5  stable_endpoints=5
```

The bad endpoint was no longer among Ready endpoints after 3.35 seconds. Full
five-Pod stable capacity was restored after 12.13 seconds:

```text
Status: Degraded
Message: RolloutAborted: Rollout aborted update to revision 3
SetWeight: 0
ActualWeight: 0
Updated: 0
Ready: 5

post_abort_service_requests: 20
post_abort_http_200: 20
post_abort_other_responses: []
```

#### Abort versus the Lab 5 Git revert

In Lab 5, the Git revert push completed at `17:03:57Z`; Argo CD was observed
Healthy 146 seconds later and `/health` returned 200 after 147 seconds. In this
lab, all Ready traffic endpoints were stable after 3.35 seconds and full
five-Pod stable capacity returned after 12.13 seconds.

Using the conservative full-capacity measurement, the Rollout abort was about
`147 / 12.13 = 12.1` times faster than the observed Git-revert recovery. It
also required no new Git commit, remote push, Argo CD polling, image pull, or
replacement of the already healthy stable replicas.

## Task 2 — Multi-Step Canary with Observation

### 1. Strategy

The more granular strategy was:

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

### 2. Step observations

The rollout of `APP_VERSION=v4-multistep` was observed through four timed
pauses. Representative output was:

```text
2026-10-04T13:05:05Z phase=Paused step=1 weight=20 updated=1 ready=5
  scraped pods: stable=4 canary=1 events_rps=0.810 error_percent=0

2026-10-04T13:06:41Z phase=Paused step=3 weight=40 updated=2 ready=5
  scraped pods: stable=3 canary=2 events_rps=0.690 error_percent=0

2026-10-04T13:07:52Z phase=Paused step=5 weight=60 updated=3 ready=5
  scraped pods: stable=2 canary=3 events_rps=0.931 error_percent=0

2026-10-04T13:09:04Z phase=Paused step=7 weight=80 updated=4 ready=5
  scraped pods: stable=1 canary=4 events_rps=0.853 error_percent=0

Final verification before the Bonus run:
phase=Healthy step=9 updated=5 ready=5 weight=100
```

The updated-replica count therefore followed the intended sequence
`1 → 2 → 3 → 4 → 5`.

### 3. Dashboard observation

An in-cluster Prometheus scraped every Gateway Pod using the
`rollouts-pod-template-hash` label exposed as `rs_hash`. Grafana used a
dedicated `Lab 7 In-cluster Prometheus` datasource and the dashboard
`QuickTicket — Lab 7 Canary`.

During the canary:

- `/events` request rate remained approximately 0.7–0.95 requests per second.
- The chart displayed both revision hashes, `7fbfff666d` and `74d7cff5bd`.
- Scraped Pod counts changed from 4/1 to 3/2, 2/3, 1/4, and finally 0/5.
- The new revision stayed at 0% 5xx throughout the measured steps.

An earlier attempt caused transient local k3d resource pressure and health
probe failures across Gateway, Events, and Payments. The services later passed
10/10 baseline requests. For the valid run, redundant monitoring components
were scaled down and the continuous load was limited to roughly one request
per second. This kept the observation meaningful without hiding the earlier
local-capacity issue.

### 4. Automated-abort threshold

I would begin automated analysis at 20%, then abort at that same stage if the
canary error rate or latency violates its limit for the required consecutive
measurements. With five replicas, 20% exposes only one canary Pod, limiting the
blast radius while still sending real Service traffic to the new revision.
The analysis must also treat missing traffic as an error rather than promoting
an unmeasured canary.

## Bonus Task — Automated Canary Analysis

### 1. Prometheus discovery and AnalysisTemplate

The provided in-cluster Prometheus discovered all five Gateway Pods with their
revision hashes:

```text
gateway_scrape_up_count: 5
prometheus_target: pod=gateway-... rs_hash=7fbfff666d health=up
```

The template was installed successfully:

```text
NAME                 AGE
gateway-error-rate   0s
```

Its query scopes both numerator and denominator to the current canary hash.
The numerator uses `or on() vector(0)` so zero 5xx responses is a valid zero,
while the denominator remains strict so a canary with no traffic cannot be
promoted blindly.

The final Rollout strategy passes `podTemplateHashValue: Latest` to the
template:

```yaml
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

### 2. Good canary auto-promotion

The good canary started at `13:17:30Z`. After discovery and the required rate
window, all three measurements were zero:

```text
analysis: gateway-9dfff959b-6-2
phase: Successful
values: [0], [0], [0]
good_canary_completed_utc: 2026-10-04T13:20:33Z
```

The AnalysisRun succeeded and the rollout continued to 50% and then 100%
without a manual promotion.

### 3. Bad canary automatic abort

The bad canary used `APP_VERSION=v6-analysis-bad` and
`GATEWAY_TIMEOUT_MS=0`, producing real `/events` 504 responses while keeping
the Pod available for measurable canary traffic. It started at `13:20:34Z`.

```text
NAME                     STATUS       AGE
gateway-769fbf8874-7-2   Failed       84s
gateway-9dfff959b-6-2    Successful   4m23s
```

The failed AnalysisRun contained these measurements:

```yaml
status:
  message: 'Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)'
  metricResults:
    - count: 2
      failed: 2
      measurements:
        - phase: Failed
          value: '[0.3030303030303031]'
        - phase: Failed
          value: '[0.29411764705882354]'
      phase: Failed
  phase: Failed
```

Both measurements exceeded the 5% limit, so the rollout automatically aborted:

```text
Status: Degraded
Message: RolloutAborted: Step-based analysis phase error/failed
SetWeight: 0
ActualWeight: 0
Stable hash: 9dfff959b
Bad canary hash: 769fbf8874 (ScaledDown)
Ready stable replicas: 5
post_auto_abort_http_200: 10/10
```

The good manifest was reapplied after capturing the required Degraded state:

```text
Status: Healthy
Step: 6/6
SetWeight: 100
ActualWeight: 100
Desired: 5
Updated: 5
Ready: 5
Available: 5
bonus_result: good=Successful bad=Failed auto_abort_verified recovery=Healthy
```

### 4. Additional metric

Beyond error rate, I would add the canary's p95 request latency from
`gateway_request_duration_seconds_bucket`, scoped by `rs_hash`. A version can
return only successful responses yet still be unusably slow. Error rate and
tail latency together cover both correctness and user-visible performance. A
minimum request-volume condition should remain mandatory so neither metric can
approve an untested canary.

## Final Verification

- Argo Rollouts plugin and controller: installed and running.
- Gateway: converted to a five-replica Rollout.
- Manual canary: paused at 20%, promoted through 60% to 100%.
- Traffic split: observed at 21.88% for the 20% canary.
- Manual bad canary: returned HTTP 504 and was aborted.
- Manual abort: all traffic endpoints stable after 3.35 seconds; full capacity after 12.13 seconds.
- Multi-step canary: 20%, 40%, 60%, 80%, and 100% observed.
- Good automated analysis: `Successful`, three zero measurements.
- Bad automated analysis: `Failed`, automatic abort, stable replicas preserved.
- Final live state: `Healthy`, 100%, 5/5 Ready and Updated.
- Load generator: removed after every experiment.
- No credentials, webhook URLs, or tokens are included in committed files.

## Conclusion

Lab 7 demonstrated progressively safer delivery at three levels. Manual
canary promotion limited initial exposure and made rollback much faster than a
Git revert. The multi-step strategy showed predictable replica movement while
request rate stayed steady. Automated analysis then promoted a good version
from three zero-error measurements and rejected a bad version after two
measurements above the threshold, preserving stable service availability
throughout the abort.
