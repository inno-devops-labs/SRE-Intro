# Lab 7 — Progressive Delivery

## Setup

Tested on 5 October 2026; timestamps below are UTC. The existing `quickticket` k3d cluster and Lab 5 images were reused. After the cluster restart, the database had no application tables, so the provided `app/seed.sql` was loaded and a successful `/events` response was verified before the measurements below. The dependency manifests in `k8s/` are carried forward from Lab 5 so ArgoCD can manage a complete application from this branch.

Argo Rollouts v1.10.0 was installed with server-side apply (the CRDs exceed the client-side annotation limit). The kubectl plugin was kept in the lab's local tools directory. In-cluster Prometheus uses the supplied `labs/lab7/prometheus.yaml`.

Gateway now uses `kind: Rollout`, five replicas, and an `app: gateway` Service selector. `maxSurge: 0` and `maxUnavailable: 1` keep the experiment to five pods, allowing four ready pods briefly during replacement. New revisions are triggered with `APP_VERSION`; the working image stays unchanged.

Gateway probes use `/openapi.json` to check the gateway process. `/health` remains the diagnostic endpoint for dependency health. Otherwise a broken dependency would remove the canary from Service endpoints before its request errors could be analyzed.

Version output:

```text
kubectl-argo-rollouts: v1.10.0+d90700a
  BuildDate: 2026-08-27T15:22:01Z
  GitCommit: d90700ae8d71d141561f0c546e19f999bb335cbd
  GitTreeState: clean
  GoVersion: go1.26.7
  Compiler: gc
  Platform: linux/amd64
```

## Task 1 — Manual canary

The initial strategy was `20% → manual pause → 60% → 30s pause → 100%`.

Paused at 20% (`kubectl argo rollouts get rollout gateway`):

```text
2026-10-05T06:38:45.326065+00:00
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/nikitadev-work/quickticket-gateway:85be3a96489cd32d3f045e706510161bd446da04 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5
```

The provided in-cluster loadgen accessed the Service, rather than a port-forward to one pod. Over a 35-second sample, `/events` access-log counts were:

| Pod | Version | Requests |
|---|---|---:|
| `gateway-55f479b8f5-8sqsl` | stable | 30 |
| `gateway-55f479b8f5-glt9q` | stable | 32 |
| `gateway-55f479b8f5-s8bvh` | stable | 27 |
| `gateway-55f479b8f5-sh689` | stable | 33 |
| `gateway-b795cfcd4-bfsdc` | canary | 32 |

The canary received **32/154 = 20.8%**, close to the requested 20% in a short sample. This is replica-based balancing, not an exact per-request traffic-router quota.

After `kubectl argo rollouts promote gateway`, the 60% step progressed to 100% automatically:

```text
2026-10-05T06:39:33.253760+00:00
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          2/5
  SetWeight:     60
  ActualWeight:  50
Images:          ghcr.io/nikitadev-work/quickticket-gateway:85be3a96489cd32d3f045e706510161bd446da04 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         4
  Available:     4

2026-10-05T06:40:28.177965+00:00
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/nikitadev-work/quickticket-gateway:85be3a96489cd32d3f045e706510161bd446da04 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5
```

For the bad revision, `APP_VERSION=v3-bad` and `EVENTS_URL=http://127.0.0.1:9` produced real request failures. After it paused at 20%, `kubectl argo rollouts abort gateway` removed the bad canary:

```text
2026-10-05T06:41:00.435214+00:00
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 8
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/nikitadev-work/quickticket-gateway:85be3a96489cd32d3f045e706510161bd446da04 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         4
  Available:     4
```

**Abort versus Lab 5 git revert:** 1.28 seconds elapsed from starting abort until all ready Service endpoints belonged to the stable revision. Four stable pods served traffic while the fifth recovered. The good configuration was reapplied and the Rollout became Healthy with five ready pods. This endpoint check measures eligibility for new requests, not completion of every in-flight request.

Lab 5 took **7.21 seconds** from starting `git revert` (5.08 seconds after push), including a requested ArgoCD refresh. Abort was faster in this test because it acted on the running ReplicaSets without waiting for a Git push and sync. The known-good manifest still needs to be restored in Git.

## Task 2 — Multi-step observation

Tested strategy:

```yaml
canary:
  maxSurge: 0
  maxUnavailable: 1
  steps:
  - setWeight: 20
  - pause:
      duration: 60s
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

I used the live Rollouts view and in-cluster Prometheus, as allowed by the Lab 7 observation note. The host Compose Grafana was not used to infer Kubernetes pod metrics.

Rollout snapshots recorded alongside `kubectl argo rollouts get rollout gateway --watch`:

```text
Status:          ॥ Paused
  Step:          1/9
  SetWeight:     20
  ActualWeight:  20
  Updated:       1
  Ready:         5

Status:          ॥ Paused
  Step:          3/9
  SetWeight:     40
  ActualWeight:  40
  Updated:       2
  Ready:         5

Status:          ॥ Paused
  Step:          5/9
  SetWeight:     60
  ActualWeight:  60
  Updated:       3
  Ready:         5

Status:          ॥ Paused
  Step:          7/9
  SetWeight:     80
  ActualWeight:  80
  Updated:       4
  Ready:         5

Status:          ✔ Healthy
  Step:          9/9
  SetWeight:     100
  ActualWeight:  100
  Updated:       5
  Ready:         5
```

Prometheus samples during the pauses:

| UTC | Weight | Updated / ready | `/events` requests/s | 5xx ratio |
|---|---:|---|---:|---:|
| 06:41:42 | 20% | 1 / 5 | 3.98 | 0.000 |
| 06:42:49 | 40% | 2 / 5 | 4.05 | 0.000 |
| 06:43:57 | 60% | 3 / 5 | 4.02 | 0.000 |
| 06:45:07 | 80% | 4 / 5 | 4.10 | 0.000 |

Queries: `sum(rate(gateway_requests_total{path="/events"}[30s]))` and `(sum(rate(gateway_requests_total{path="/events",status=~"5.."}[30s])) or vector(0)) / sum(rate(gateway_requests_total{path="/events"}[30s]))`.

The updated count increased 1 → 2 → 3 → 4 → 5 and the rollout completed Healthy. **I would automatically abort at 20%** if the canary error ratio exceeds 5%: detect a regression before exposing more users. A lower-traffic canary needs enough observation time before deciding.

## Bonus — Automatic analysis

The final manifest adds an analysis gate at 20%, then proceeds through 60% to 100%. The [AnalysisTemplate](../k8s/analysis-template.yaml) checks only the canary hash and `/events` requests, so successful probes cannot dilute application errors. It waits 60 seconds, takes three measurements 20 seconds apart, and requires a ratio below 0.05. With `failureLimit: 1`, the second failed measurement aborts the rollout.

The numerator uses a zero fallback when no 5xx series exists. The denominator remains strict: absent traffic must not be treated as a successful canary.

```text
NAME                 AGE
gateway-error-rate   5m23s

NAME                      STATUS       AGE
gateway-5fb7f75577-11-2   Successful   4m53s
gateway-75cd79cd4-12-2    Failed       96s
```

Analysis results (status excerpts):

```yaml
name: gateway-5fb7f75577-11-2
phase: Successful
metricResults:
- measurements:
  - finishedAt: '2026-10-05T06:47:22Z'
    phase: Successful
    startedAt: '2026-10-05T06:47:22Z'
    value: '[0]'
  - finishedAt: '2026-10-05T06:47:42Z'
    phase: Successful
    startedAt: '2026-10-05T06:47:42Z'
    value: '[0]'
  - finishedAt: '2026-10-05T06:48:02Z'
    phase: Successful
    startedAt: '2026-10-05T06:48:02Z'
    value: '[0]'
  name: error-rate
  phase: Successful
  successful: 3
name: gateway-75cd79cd4-12-2
phase: Failed
metricResults:
- failed: 2
  measurements:
  - finishedAt: '2026-10-05T06:50:39Z'
    phase: Failed
    startedAt: '2026-10-05T06:50:39Z'
    value: '[1]'
  - finishedAt: '2026-10-05T06:50:59Z'
    phase: Failed
    startedAt: '2026-10-05T06:50:59Z'
    value: '[1]'
  name: error-rate
  phase: Failed
```

Good canary (`APP_VERSION=v5-auto`) promoted without a manual promote. Bad canary (`APP_VERSION=v6-bad`, `EVENTS_URL=http://127.0.0.1:9`) produced real HTTP 502 responses and automatically aborted.

After the automatic abort:

```text
2026-10-05T06:51:13.576671+00:00
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 12: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/nikitadev-work/quickticket-gateway:85be3a96489cd32d3f045e706510161bd446da04 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5
```

**Additional metric:** canary p99 request latency, compared with stable pods. Slow successful responses can violate the latency SLO without increasing the error ratio.

## Final state

The good manifest was restored, the Rollout is Healthy with five ready replicas, and the loadgen was removed. ArgoCD manages the complete `k8s/` directory from `feature/lab7` with self-healing enabled. Prometheus and Argo Rollouts remain available for Lab 8.
