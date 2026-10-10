# Lab 7 - Progressive Delivery with Canary Deployments

## Goal

Replace the all-at-once gateway Deployment rollout with controlled progressive
delivery. The canary must limit blast radius, pause between traffic steps, support
manual promotion and abort, and use Prometheus-based automated analysis to stop a
bad release before it reaches all replicas.

## Task 1 - Argo Rollouts and Manual Canary Control

### Problem and solution

The previous gateway resource was a standard Deployment. A Deployment replaces
pods according to its rollout strategy, but it does not provide an explicit
traffic checkpoint where an operator can inspect the canary and abort it before
the full replica set is updated.

The gateway is now an Argo Rollout with five replicas and a canary strategy. The
service continues selecting `app: gateway`; Argo Rollouts controls the stable and
canary ReplicaSets behind that service.

### Installation and version

```text
kubectl argo rollouts version
kubectl-argo-rollouts: v1.10.0+d90700a
Platform: darwin/arm64
```

The controller was installed in the `argo-rollouts` namespace. Because the local
k3d runtime could not verify the corporate TLS certificate for quay.io, the
controller image was imported into k3d and configured with
`imagePullPolicy: IfNotPresent`.

### Rollout strategy

The committed gateway strategy is:

```yaml
replicas: 5
strategy:
  canary:
    steps:
      - setWeight: 20
      - pause:
          duration: 60s
      - analysis:
          templates:
            - templateName: gateway-error-rate
          args:
            - name: canary-hash
              valueFrom:
                podTemplateHashValue: Latest
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

With five replicas, the requested weights are approximate at the pod level. For
example, `20%` means one canary pod out of five, while `40%` means two canary pods.
The rollout status reports the actual weight after the new pods become ready.

### Good canary: manual promotion

The first canary paused at 20%:

```text
Status: Paused
Step: 1/5
SetWeight: 20
ActualWeight: 20
Updated: 1
Ready: 5
```

The in-cluster load generator produced the following request distribution:

```text
stable pods: 61 + 42 + 47 + 61 = 211 requests
canary pod: 45 requests
total: 256 requests
canary share: 45/256 = 17.6%
```

After manual promotion and readiness checks, the rollout reached 60%:

```text
Status: Paused
Step: 3/5
SetWeight: 60
ActualWeight: 60
Updated: 3
Ready: 5
Available: 5
```

After the timed pause, it completed successfully:

```text
Status: Healthy
Step: 5/5
SetWeight: 100
ActualWeight: 100
Updated: 5
Ready: 5
Available: 5
```

### Bad canary: manual abort

For the negative test, the canary was configured with an intentionally invalid
`EVENTS_URL`. The readiness probe was kept valid so that the canary could become
ready and the rollout controller could evaluate it. The rollout paused at 20%:

```text
canary hash: 5cf58c8d8
stable hash: dd8dcb9f8
SetWeight: 20
ActualWeight: 20
```

The rollout was aborted with:

```text
kubectl argo rollouts abort gateway
abort_command_duration=1s
Status: Degraded
Message: RolloutAborted
```

The canary ReplicaSet was scaled down and the stable revision remained serving
traffic. This is faster and more targeted than reverting Git and waiting for the
GitOps controller to reconcile. In the previous Lab 5 test, Git revert recovery
took approximately 30 seconds after ArgoCD refresh.

## Task 2 - Multi-step Canary

The same rollout was tested with the explicit sequence `20 -> 40 -> 60 -> 80 ->
100`. The observed checkpoints were:

```text
Step 1/9  SetWeight: 20  ActualWeight: 20  Updated: 1  Ready: 5
Step 3/9  SetWeight: 40  ActualWeight: 40  Updated: 2  Ready: 5
Step 6/9  SetWeight: 60  ActualWeight: 60  Updated: 3  Ready: 5
Step 7/9  SetWeight: 80  ActualWeight: 75  Updated: 4
Step 9/9  SetWeight: 100 ActualWeight: 100 Updated: 5 Ready: 5
Status: Healthy
```

The temporary in-cluster load generator was used because the host Prometheus from
the Docker Compose stack cannot reliably scrape pod addresses inside the k3d
network. Rollout status and request distribution provided direct evidence of the
traffic checkpoints. In production, the same checkpoints should be combined with
dashboard metrics and an explicit SLO gate.

An abort threshold of 20% is appropriate for this five-replica local setup: one
canary pod gives a measurable signal while limiting the affected capacity to one
replica. The controller should abort before increasing to 40% when the canary
shows sustained errors, failed readiness, or an unsuccessful analysis.

## Bonus - Automated Canary Analysis

### Prometheus and AnalysisTemplate

The provided in-cluster Prometheus manifest was applied in the `monitoring`
namespace. Its active targets included all five gateway pods and exposed the
`rs_hash` label needed to distinguish stable and canary traffic:

```text
gateway-6f976d4d4c-cj7h8 rs=6f976d4d4c up
gateway-6f976d4d4c-tnq4j rs=6f976d4d4c up
gateway-6f976d4d4c-p5qsv rs=6f976d4d4c up
gateway-6f976d4d4c-2zvtj rs=6f976d4d4c up
gateway-6f976d4d4c-7gv9b rs=6f976d4d4c up
```

`k8s/analysis-template.yaml` measures the canary 5xx rate for the supplied
ReplicaSet hash. It waits 60 seconds for traffic, takes three measurements every
20 seconds, and succeeds only when the error rate is below 5%. The numerator uses
`or on() vector(0)` so zero errors evaluate as zero; the denominator remains strict
so that no canary traffic is treated as a failed signal instead of a false success.

### Successful automated analysis

The first analysis attempt failed because the existing PostgreSQL pod had no
application tables. The AnalysisRun correctly detected the resulting gateway
errors:

```text
gateway-6457c9cd56-6-2 Failed
values: [0.6176470588235294], [0.591549295774648]
message: failed (2) > failureLimit (1)
```

The schema was restored with `app/seed.sql`, and the gateway health and events
endpoints were verified. A retry then passed:

```text
gateway-6457c9cd56-6-2.1 Successful
message: empty
measurements: 3
```

The successful analysis automatically allowed the rollout to continue. The final
state was:

```text
Status: Healthy
Step: 10/10
SetWeight: 100
ActualWeight: 100
Updated: 5
Ready: 5
Available: 5
```

### Failed automated analysis

The negative test used the broken events URL again. The canary AnalysisRun failed
and Argo Rollouts aborted the update automatically:

```text
gateway-59b8b89dcf-7-2 Failed
Metric "error-rate" assessed Failed
failed (2) > failureLimit (1)
Status: Degraded
Message: RolloutAborted
```

The bad ReplicaSet was scaled down and the previous stable revision remained
healthy. After restoring the valid configuration, a new AnalysisRun completed
successfully and the rollout reached `Healthy` with all five replicas available.

The most useful additional analysis metric would be p95/p99 request latency,
because a canary can keep its error rate low while becoming materially slower.
Resource saturation, dependency latency, and circuit-breaker state would be useful
secondary metrics.

## Conclusion

Argo Rollouts reduced the release blast radius from all gateway replicas to one
canary pod, provided explicit promotion checkpoints, and completed an abort in
approximately one second. Prometheus AnalysisRuns added an automated safety gate:
the bad canary was rejected while the stable revision continued serving traffic.

## Artifacts

- `k8s/gateway.yaml` - Argo Rollout, canary steps, probes, and service
- `k8s/analysis-template.yaml` - Prometheus error-rate AnalysisTemplate
- `submissions/lab7.md` - CLI evidence and analysis
- Screenshots: none; CLI output is the primary evidence

## Checklist

- [x] Task 1 done - Argo Rollouts installed, canary deployed, promoted, and aborted
- [x] Task 2 done - multi-step canary tested through 20/40/60/80/100
- [x] Bonus Task done - automated canary analysis passed and failed as expected
- [x] Title is clear (`feat(labN): <topic>` style)
- [x] No secrets or large temporary files committed
- [x] Submission file at `submissions/lab7.md` exists
