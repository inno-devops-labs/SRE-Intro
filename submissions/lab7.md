# Lab 7 — Progressive Delivery

## 7.1 Argo Rollouts

Argo Rollouts was installed in the `argo-rollouts` namespace. The official
manifest was applied with server-side apply because the CRD annotation exceeded
the normal client-side apply limit. The controller became available.

```text
deployment.apps/argo-rollouts condition met
```

```text
$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.10.0
kubectl: v1.35.5

$ kubectl get deployment -n argo-rollouts
NAME            READY   UP-TO-DATE   AVAILABLE
argo-rollouts   1/1     1            1
```

## 7.2–7.6 Manual canary

`k8s/gateway.yaml` is now a `Rollout` with five replicas. The Service selector
remains `app: gateway`, so it selects both stable and canary pods.

Changing `APP_VERSION` from `v1` to `v2` paused the Rollout at 20%. There was
one new pod and four stable pods.

```text
phase: Paused
currentStepIndex: 1
message: CanaryPauseStep
stableRS: 68f84bb847
updatedReplicas: 1

NAME                 DESIRED   CURRENT   READY
gateway-58968bb598   1         1         1
gateway-68f84bb847   4         4         4
```

The canary was promoted to 60%, then completed automatically.

```text
# while moving to the next step
phase: Progressing
currentStepIndex: 2
updatedReplicas: 3
stableRS: 68f84bb847

# completed
phase: Healthy
currentStepIndex: 5
stableRS: 58968bb598
updatedReplicas: 5
availableReplicas: 5
```

For the bad release, `APP_VERSION` was changed to `v3-bad`. It again paused
with one canary pod, then was aborted. The stable ReplicaSet stayed available
and the controller returned the expected degraded state.

```text
phase: Degraded
message: 'RolloutAborted: Rollout aborted update to revision 3'
stableRS: 58968bb598

NAME                       READY   STATUS
gateway-58968bb598-954nj   1/1     Running
gateway-58968bb598-dgltk   1/1     Running
gateway-58968bb598-g7nz2   1/1     Running
gateway-58968bb598-hk66l   1/1     Running
gateway-58968bb598-jdwh4   1/1     Running
```

**Abort answer.** The controller started scaling the canary down immediately;
the stable pods were already serving traffic, so there was no waiting for a
new stable deployment. This was about seconds in this test. A `git revert`
rollback from Lab 5 is slower because Git, Argo CD sync, scheduling, image
pulling, readiness checks, and a normal rollout must all happen first.

## 7.8–7.9 Multi-step observation

The multi-step canary strategy was:

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

```text
$ kubectl argo rollouts get rollout gateway --watch
Status: Progressing   Step: 1/9   Actual Weight: 20   Updated: 1   Available: 5
Status: Progressing   Step: 3/9   Actual Weight: 40   Updated: 2   Available: 5
Status: Progressing   Step: 5/9   Actual Weight: 60   Updated: 3   Available: 5
Status: Progressing   Step: 7/9   Actual Weight: 80   Updated: 4   Available: 5
Status: Healthy       Step: 9/9   Actual Weight: 100  Updated: 5   Available: 5
```

The host Grafana from Lab 3 cannot reach k3d pod addresses. The Rollout view
and in-cluster Prometheus were used for observation.

I would use an automated abort at **20%** when the canary has more than 5%
5xx errors. A small group of users sees the problem, but most traffic is still
on the stable version.

## Bonus — automated analysis

The in-cluster Prometheus and `gateway-error-rate` AnalysisTemplate were
applied.

```text
kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   0s
```

The final `k8s/gateway.yaml` has the analysis step after 20% and passes the
latest pod-template hash as `canary-hash`. The good canary produced a successful
AnalysisRun:

```text
NAME                     STATUS       AGE
gateway-68f84bb847-4-2   Successful   5m24s
```

For the bad test, `EVENTS_URL=http://broken-on-purpose:8081` was used. The
test pod probes used `/metrics` so the canary remained measurable. The bad run
reached the analysis step and Prometheus saw an error ratio of `0.0813`
(more than the 5% limit). The final bad AnalysisRun failed and the Rollout
aborted, while its stable ReplicaSet stayed in service:

```text
NAME                     STATUS
gateway-68f84bb847-4-2   Successful
gateway-69f769c84b-6-2   Failed

Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
value: '[1]'
value: '[1]'
phase: Degraded
message: Rollout aborted update to revision 6
stableRS: 68f84bb847
```

For a more complete canary analysis I would add `p99 latency per path`.
Error rate can be zero while a payment request is too slow for users.
