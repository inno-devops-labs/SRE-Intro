# Lab 7 — Progressive Delivery

## Execution status

Experiments were performed on the local `k3d-quickticket` cluster on 2026-10-04 (Europe/Moscow). Evidence timestamps below use UTC. The initial registry TLS timeout was resolved before the live experiments.

## Controller and CLI

The Argo Rollouts CRDs and controller Deployment were installed from the pinned v1.9.1 release. The controller is `1/1 Running` and its Deployment is `Available`.

The macOS arm64 plugin is installed at `/tmp/kubectl-argo-rollouts`:

```text
kubectl-argo-rollouts: v1.9.1+b6bd3bc
BuildDate: 2026-07-17T09:37:17Z
GitCommit: b6bd3bcf8f60d717a98763d26acc983db7f97cb0
GitTreeState: clean
GoVersion: go1.24.13
Compiler: gc
Platform: darwin/arm64
```

## Gateway configuration

`k8s/gateway.yaml` replaces the gateway Deployment with an Argo Rollout. Five replicas provide one-pod increments of 20%. The Service keeps the `app: gateway` selector so stable and canary replicas both receive traffic.

The final strategy combines granular observation and automated analysis:

```yaml
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

The process probes use `/metrics`. The existing `/health` endpoint also checks dependencies and returns 503 when `EVENTS_URL` is broken. Keeping it as readiness would exclude the bad canary from Service traffic and prevent a meaningful 5xx analysis. Keeping it as liveness would restart a functioning process for an external dependency failure. `/health` remains available for dependency diagnosis.

## Task 1 — Manual canary experiment

The manual experiment used 20%, an indefinite pause, 60%, a 30-second pause, and 100%. Changing `APP_VERSION` to `v2` triggered a pod-template revision; this identifies the version in the pod specification, not in an application endpoint.

The load generator ran inside the cluster using `labs/lab7/loadgen.yaml`. Request counts were taken from gateway pod logs over a 30-second window. Service port-forwarding was not used for traffic generation.

### Paused at 20%

```text
Name:            gateway
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

gateway-84c55bf96d   ReplicaSet   Healthy   canary
gateway-96d79c59b    ReplicaSet   Healthy   stable
```

| Pod | Revision | GET /events requests |
|---|---|---:|
| gateway-84c55bf96d-clnhm | Canary | 12 |
| gateway-96d79c59b-8brfw | Stable | 11 |
| gateway-96d79c59b-bgq46 | Stable | 14 |
| gateway-96d79c59b-frhqm | Stable | 10 |
| gateway-96d79c59b-ftf7d | Stable | 15 |

Canary traffic was **12 / 62 = 19.35%**, close to the requested 20%.

### Promotion

`kubectl argo rollouts promote gateway` advanced to 60%, then the timed pause led to 100%:

```text
Status:          Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

gateway-84c55bf96d   ReplicaSet   Healthy      stable
gateway-96d79c59b    ReplicaSet   ScaledDown
```

### Bad canary and abort

The bad revision used `APP_VERSION=v3-bad`, `EVENTS_URL=http://broken-on-purpose:8081` and `GATEWAY_TIMEOUT_MS=2000`. Its Ready pod returned an actual HTTP **502** for `/events` in this gateway image and paused at 20%.

At `2026-10-03T21:35:17.719658Z`, `kubectl argo rollouts abort gateway` was executed. Ready EndpointSlice entries were polled every 0.5 seconds. Stable-only ready endpoints were first observed **0.863 seconds** after starting the abort command.

```text
Status:          Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0

gateway-55989f747c   ReplicaSet   ScaledDown   aborted canary
gateway-84c55bf96d   ReplicaSet   stable
```

Four stable pods were immediately Ready; the fifth replacement was starting. This measures traffic endpoint recovery, not completion of pod scaling. Existing in-flight requests can also finish after endpoint removal.

Lab 5's measured Git revert-to-Healthy recovery was **17.01 seconds**, recorded in commit `1cbd746`. Abort was faster here because it directly reused running stable replicas, while GitOps rollback required a push and ArgoCD synchronization. These measurements have different endpoints and are specific to the local experiments.

## Task 2 — Multi-step observation

The final good revision uses `APP_VERSION=v4`. The rollout was observed with `kubectl argo rollouts get rollout gateway --watch`, and Prometheus queries recorded request rate and 5xx ratio at each stage.

An in-cluster Grafana 11.4.0 instance was connected to `http://prometheus.monitoring.svc.cluster.local:9090`. The existing QuickTicket Golden Signals dashboard was imported, and Request Rate, Error Rate and Request Latency were observed. Compose Prometheus was not used to scrape k3d pods.

The dashboard initially exposed missing `events` and `orders` tables after a local PostgreSQL restart. The supplied `app/seed.sql` restored both tables and inserted five events; `/events` then returned 200. The healthy multi-step experiment started after this repair.

Database Pool Saturation and the recording-rule-based Availability SLO panels had no data in this gateway-only Prometheus setup and were not used as evidence.

I would run automated analysis at 20% and abort before increasing exposure if canary 5xx exceeds 5%. This limits the initial impact to about one fifth of traffic while providing real requests for evaluation. Replica weighting is approximate, so short request samples can differ from the configured percentage.

Examples from the live watch and simultaneous metric queries:

| UTC timestamp | State / step | Canary weight | Updated replicas | Ready replicas | Requests/s | 5xx ratio |
|---|---|---:|---:|---:|---:|---:|
| 21:36:16 | Paused, 1/10 | 20% | 1 | 5 | 4.98 | 0% |
| 21:39:59 | Paused, 4/10 | 40% | 2 | 5 | 5.08 | 0.365% |
| 21:41:19 | Paused, 6/10 | 60% | 3 | 5 | 4.56 | 0% |
| 21:42:50 | Paused, 8/10 | 80% | 4 | 5 | 5.10 | 0% |
| 21:43:31 | Healthy, 10/10 | 100% | 5 | 5 | 5.04 | 0% |

Selected fields from `kubectl argo rollouts get rollout gateway --watch`:

```text
Status: Paused     Step: 1/10    SetWeight: 20    ActualWeight: 20
Updated: 1        Ready: 5      Available: 5

Status: Paused     Step: 4/10    SetWeight: 40    ActualWeight: 40
Updated: 2        Ready: 5      Available: 5

Status: Paused     Step: 6/10    SetWeight: 60    ActualWeight: 60
Updated: 3        Ready: 5      Available: 5

Status: Paused     Step: 8/10    SetWeight: 80    ActualWeight: 80
Updated: 4        Ready: 5      Available: 5

Status: Healthy    Step: 10/10   SetWeight: 100   ActualWeight: 100
Updated: 5        Ready: 5      Available: 5
```

Traffic stayed approximately steady while the updated-replica count increased. A brief aggregate 5xx spike below 0.4% was observed at 40%; it returned to zero before the 60% observation. The first-step canary analysis had already completed with zero errors. With `maxSurge: 0` and `maxUnavailable: 1`, replacement transitions temporarily have four Ready replicas; steady paused stages have five.

## Bonus — Automated analysis

`k8s/analysis-template.yaml` scopes its query to `{{args.canary-hash}}`, supplied by the Rollout's latest ReplicaSet hash. The supplied in-cluster Prometheus manifest maps `rollouts-pod-template-hash` to `rs_hash`.

```text
$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   6m11s
```

Prometheus target discovery showed all five gateway pods as `up`, with both stable and canary `rs_hash` labels. The Service selector remains `app: gateway`, so both ReplicaSets receive traffic.

- Initial delay: 60 seconds for discovery and scrapes.
- Measurement interval: 20 seconds; count: 3.
- Success: a single measured error ratio below 5%.
- Failure: an empty result, NaN or error ratio at least 5%.
- Failure limit: 1; the second failed measurement aborts.
- Consecutive provider error limit: 2.

Missing 5xx series have a zero fallback; the denominator has no fallback. Missing traffic must not silently pass as a healthy canary.

The good revision's AnalysisRun `gateway-79795445b6-4-2` completed `Successful` without manual promotion:

```text
2026-10-03T21:38:04Z  Successful  [0]
2026-10-03T21:38:24Z  Successful  [0]
2026-10-03T21:38:44Z  Successful  [0]
```

An independent no-traffic check queried a nonexistent ReplicaSet hash with the same query and conditions. `gateway-no-traffic-check` returned `[]` and completed `Failed`, confirming the fail-safe behavior.

### Automatic abort of a bad canary

After the good version reached 100%, a new revision used `APP_VERSION=v5-bad`, `EVENTS_URL=http://broken-on-purpose:8081` and a 2000ms gateway timeout. No manual abort was issued. The first 20% step sent real requests to the bad canary, and the controller stopped the rollout when its analysis failed:

```text
$ kubectl get analysisrun
NAME                       STATUS
gateway-5966c95bb7-5-2     Failed
gateway-79795445b6-4-2     Successful
gateway-no-traffic-check   Failed
```

Relevant fields from `kubectl get analysisrun gateway-5966c95bb7-5-2 -o yaml`:

```yaml
status:
  completedAt: "2026-10-03T21:46:35Z"
  message: 'Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)'
  metricResults:
    - count: 2
      failed: 2
      measurements:
        - finishedAt: "2026-10-03T21:46:15Z"
          phase: Failed
          startedAt: "2026-10-03T21:46:15Z"
          value: '[1]'
        - finishedAt: "2026-10-03T21:46:35Z"
          phase: Failed
          startedAt: "2026-10-03T21:46:35Z"
          value: '[1]'
      name: error-rate
      phase: Failed
  phase: Failed
  startedAt: "2026-10-03T21:45:15Z"
```

Rollout output after the failed analysis:

```text
Status:          Degraded
Message:         RolloutAborted: Rollout aborted update to revision 5:
                 Step-based analysis phase error/failed:
                 Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/10
  SetWeight:     0
  ActualWeight:  0
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         4
  Available:     4

gateway-5966c95bb7   ReplicaSet   ScaledDown   aborted canary
gateway-79795445b6   ReplicaSet   stable
gateway-5966c95bb7-5-2   AnalysisRun   Failed
gateway-79795445b6-4-2   AnalysisRun   Successful
```

Ready EndpointSlices contained only `gateway-79795445b6-*` stable pods. The fifth stable replacement was starting. The healthy manifest was reapplied after capturing the aborted state; the load generator was removed after recovery verification.

Beyond error rate, I would add canary p95 latency relative to stable, plus a minimum request-count requirement. A slow version can return 200 while harming users, and low traffic can produce an unreliable success ratio.

## Validation

```text
kubectl apply --dry-run=server -f k8s/gateway.yaml
rollout.argoproj.io/gateway created (server dry run)
service/gateway configured (server dry run)

kubectl apply --dry-run=server -f k8s/analysis-template.yaml
analysistemplate.argoproj.io/gateway-error-rate created (server dry run)

kubectl apply --dry-run=server -f k8s/
passed

kubectl argo rollouts lint -f k8s/gateway.yaml
passed

promtool check config /etc/prometheus/prometheus.yml
SUCCESS

git diff --check
passed

Final gateway Rollout:
Healthy; Step 10/10; ActualWeight 100; Desired 5; Updated 5; Ready 5

GET /events: 200
GET /health: healthy; events=ok; payments=ok
```

The original gateway Deployment was replaced with the Rollout. ArgoCD auto-sync remains disabled during local experiments because the Git source still contains the old Deployment. Re-enable it after the Rollout and AnalysisTemplate are merged into the fork's `main`. No commit or push has been performed for Lab 7.
