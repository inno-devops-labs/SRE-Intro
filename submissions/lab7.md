# Lab 7 — Progressive Delivery: Canary Deployments

Setup notes:
- ArgoCD auto-sync (Lab 5) was switched to manual (`argocd app set quickticket --sync-policy none`) for this lab, so `kubectl apply` experiments did not fight GitOps.
- Argo Rollouts CRDs needed `kubectl apply --server-side`; client-side apply failed with `metadata.annotations: Too long`.

## Task 1 — Manual Canary

**1. `kubectl argo rollouts version`:**
```
kubectl-argo-rollouts: v1.10.0+d90700a
  Platform: darwin/arm64
```

`k8s/gateway.yaml` converted to `apiVersion: argoproj.io/v1alpha1`, `kind: Rollout`, `replicas: 5`, steps `20 → pause{} → 60 → pause 30s → 100`. New version = `APP_VERSION: "v2"` env var.

**2. Paused at 20%:**
```
Status:          ॥ Paused
Message:         CanaryPauseStep
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Replicas:  Desired: 5  Current: 5  Updated: 1  Ready: 5

├──# revision:2
│  └──⧉ gateway-6d4fcd96d9           ReplicaSet  ✔ Healthy  canary
│     └──□ gateway-6d4fcd96d9-pgv6v  Pod         ✔ Running
└──# revision:1
   └──⧉ gateway-78975cc985           ReplicaSet  ✔ Healthy  stable   (4 pods)
```

Traffic split via in-cluster loadgen (30 s):
```
gateway-6d4fcd96d9-pgv6v  (canary)  events_requests=28
gateway-78975cc985-44694  (stable)  events_requests=25
gateway-78975cc985-8jvnb  (stable)  events_requests=30
gateway-78975cc985-bssfw  (stable)  events_requests=25
gateway-78975cc985-mt6gq  (stable)  events_requests=30
```
Canary got 28/138 = **20.3%**, matching `setWeight: 20`.

**3. After `promote`:**
```
10:46:51 promote
10:46:52  Step: 2/5  ActualWeight: 25
10:46:59  Step: 3/5  ActualWeight: 60
10:47:27  Step: 4/5  ActualWeight: 75
10:47:31  Healthy

Status:  ✔ Healthy   Step: 5/5   ActualWeight: 100
└──⧉ gateway-6d4fcd96d9   ReplicaSet  ✔ Healthy  stable   (5 pods)
```

**4. After `abort`** (`APP_VERSION: "v3-bad"`, paused at 20%):
```
ABORT 10:48:15
canary removed from Service endpoints after 0.3s
canary pod fully gone after 1.5s

Status:   ✖ Degraded
Message:  RolloutAborted: Rollout aborted update to revision 5
  ActualWeight:  0
Replicas: Desired: 5  Current: 5  Updated: 0  Ready: 5  Available: 5
├──# revision:5
│  └──⧉ gateway-6db5c4c486   ReplicaSet  • ScaledDown  canary
├──# revision:4
│  └──⧉ gateway-6d4fcd96d9   ReplicaSet  ✔ Healthy     stable  (5 pods Running)
```

**5. How long from `abort` to all traffic on stable? Compare with `git revert` (Lab 5).**
**0.3 s** until the canary left the Service endpoints, 1.5 s until the pod was gone. The `git revert` rollback in Lab 5 took **304 s**: push → ArgoCD poll → sync. Abort is ~1000x faster because the stable ReplicaSet was never scaled down; abort only removes the canary. It also limited the blast radius to 20% of traffic. `git revert` is still needed afterwards to make Git match reality.

## Task 2 — Multi-Step Canary

**Strategy:**
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

Triggered with `kubectl argo rollouts set image gateway gateway=ghcr.io/meliman1000-7/quickticket-gateway:89ea6d9…` under `labs/lab7/loadgen.yaml`.

**`--watch` output (condensed, consecutive states):**
```
Progressing  Step: 0/9  ActualWeight: 0    Updated: 0
Paused       Step: 1/9  ActualWeight: 20   Updated: 1
Progressing  Step: 2/9  ActualWeight: 25   Updated: 2
Paused       Step: 3/9  ActualWeight: 40   Updated: 2
Progressing  Step: 4/9  ActualWeight: 50   Updated: 3
Paused       Step: 5/9  ActualWeight: 60   Updated: 3
Progressing  Step: 6/9  ActualWeight: 75   Updated: 4
Paused       Step: 7/9  ActualWeight: 80   Updated: 4
Progressing  Step: 8/9  ActualWeight: 100  Updated: 5
Healthy      Step: 9/9  ActualWeight: 100  Updated: 5
```

**Observation.** Compose Grafana can't scrape k3d pods, so the in-cluster Prometheus (`labs/lab7/prometheus.yaml`) was used, with per-ReplicaSet RPS via `sum by (rs_hash)(rate(gateway_requests_total[30s]))`:
```
time      step  weight upd | stable(6d4f)  canary(65bb) | total  5xx
10:50:31  0/9    0     0   |   10.36          -         | 10.36  0
10:51:04  1/9   20     1   |    8.04         1.93       |  9.98  0
10:52:25  3/9   40     2   |    6.00         4.08       | 10.24  0
10:53:28  5/9   60     3   |    4.60         5.72       | 10.32  0
10:54:15  7/9   80     4   |    2.00         7.91       |  9.92  0
10:54:31  9/9  100     5   |    1.73         8.35       | 10.07  0   (stable rate decaying)
```
- Total RPS stayed flat at ~10; no request was lost between steps.
- Updated replicas climbed 1 → 2 → 3 → 4 → 5. Weights in between (25, 50, 75) are transitional states while pods scale.
- Canary share followed weight closely (19% / 40% / 55% / 80%).
- Full rollout took 4 min.

**At what canary percentage would you want an automated abort? Why?**
At **20%**, the first step. The goal is to catch a bad version while the fewest users see it. 20% of 5 replicas is the smallest split that still gets enough traffic (~2 req/s) to measure an error rate within a minute. Waiting for 40–60% doubles or triples the blast radius and gives almost no extra statistical confidence for a binary "broken vs fine" decision. Later steps should keep analysis running for slow-burn issues like latency and saturation.

## Bonus — Automated Canary Analysis

Prometheus targets with `rs_hash`:
```
gateway-6d4fcd96d9-9z24c rs= 6d4fcd96d9 up
gateway-6d4fcd96d9-kn9rm rs= 6d4fcd96d9 up
... (5/5 up)
```

```
$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   0s
```
The template is copied to `k8s/analysis-template.yaml`. Strategy: `20 → pause 20s → analysis(canary-hash=Latest) → 50 → pause 20s → 100`.

**Finding — the bad canary never reached analysis at first.** With `EVENTS_URL=http://broken-on-purpose:8081`, the canary pod went into `CrashLoopBackOff` at step 0. Both gateway probes hit `/health`, which returns 503 when events is unreachable, so liveness killed the pod and it never received traffic. This is the same anti-pattern found in Lab 4, now on gateway. It is worse here: during a real events outage, all 5 gateway pods would go unready, and users would get connection errors instead of a JSON 503. Fix: both probes now hit `/metrics`, which only proves the process is alive. Dependency failures are left to canary analysis and alerting.

**Analysis runs:**
```
$ kubectl get analysisrun
NAME                      STATUS
gateway-6d4fcd96d9-8-2    Successful   ← good version (probes on /health)
gateway-6dcd8798dc-10-2   Successful   ← good version (probes on /metrics)
gateway-78b7659895-11-2   Failed       ← bad EVENTS_URL
```

Good version: AnalysisRun `Running` → 3 measurements `value=[0]` (10:56:42, 10:57:02, 10:57:22) → `Successful` → auto-promote 25 → 40 → 50 → 75 → 100 → `Healthy`, no human input.

**Failed run (`kubectl get analysisrun gateway-78b7659895-11-2 -o yaml`):**
```yaml
status:
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
  - count: 2
    failed: 2
    measurements:
    - finishedAt: "2026-10-04T11:09:36Z"
      phase: Failed
      value: '[1]'
    - finishedAt: "2026-10-04T11:09:56Z"
      phase: Failed
      value: '[1]'
    metadata:
      ResolvedPrometheusQuery: |
        (
          sum(rate(gateway_requests_total{rs_hash="78b7659895",status=~"5.."}[60s]))
          or on() vector(0)
        )
        /
        sum(rate(gateway_requests_total{rs_hash="78b7659895"}[60s]))
    name: error-rate
    phase: Failed
  phase: Failed
```

**Rollout after the auto-abort:**
```
Status:   ✖ Degraded
Message:  RolloutAborted: Rollout aborted update to revision 11: Step-based analysis phase error/failed:
          Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  ActualWeight:  0
Replicas: Desired: 5  Current: 5  Updated: 0  Ready: 5  Available: 5
├──# revision:11
│  ├──⧉ gateway-78b7659895       ReplicaSet   • ScaledDown  canary
│  └──α gateway-78b7659895-11-2  AnalysisRun  ✖ Failed      ✖ 2
├──# revision:10
│  ├──⧉ gateway-6dcd8798dc       ReplicaSet   stable  (5 pods Running)
```
Timeline: apply 11:08:09 → canary at 20% 11:08:19 → analysis started 11:08:36 → abort 11:09:56. Stable pods were never touched. Re-applying the good manifest returned the Rollout to `Healthy` instantly, because the spec matched stable.

**What metric would you add beyond error rate?**
**Canary p99 latency vs stable**, e.g. `histogram_quantile(0.99, sum by (le) (rate(gateway_request_duration_seconds_bucket{rs_hash="<canary>"}[60s])))` must stay under 1.2× stable's p99. A version can return 200s and still be slow: a bad query, a missing index, a smaller pool. Error rate is blind to that. Saturation (CPU/memory of canary pods) would be the next one.
