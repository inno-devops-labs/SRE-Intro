# Lab 7 — Progressive Delivery and Canary Deployments

## Environment

Work was performed on 2026-10-04 in the existing `quickticket` k3d cluster.

```text
Docker: 29.2.1 / 29.2.1
kubectl client: v1.34.1
k3d: v5.9.0 (k3s v1.35.5-k3s1)
Helm: v4.3.0
Argo CD CLI: v3.5.3
kubectl-argo-rollouts: v1.10.0+d90700a (windows/amd64)
```

The Argo Rollouts controller was installed in `argo-rollouts` and was Running. The CRDs `rollouts.argoproj.io`, `analysisruns.argoproj.io`, and `analysistemplates.argoproj.io` were present. The initial official manifest hit the Kubernetes annotation-size limit for two CRDs; a server-side apply created them safely.

## Rollout Manifest and Baseline

`k8s/gateway.yaml` changes the gateway from a Deployment to an `argoproj.io/v1alpha1` Rollout. It keeps the gateway Service selector, GHCR image, pull secret, dependencies, resources, and `/health` probes. It uses five replicas; `APP_VERSION` changes pod-template hash without rebuilding the image.

Argo CD was switched from `feature/lab5` to `feature/lab7` after the first branch push. It synced revision `bd9c2ffed9019852c5ed62507c30bb4324e9954c`; the old Deployment was absent and the Rollout had five Ready pods.

## Manual Canary

Revision 2 (`APP_VERSION=v2-canary`) paused at 20%:

```text
Status: Paused / CanaryPauseStep
SetWeight: 20; ActualWeight: 20
Updated: 1; Ready: 5; Available: 5
stable RS gateway-669f94f69d: 4 pods
canary RS gateway-cbd89f6c8: 1 pod
```

The provided in-cluster load generator was applied. Logs were counted only for the same 30-second interval:

| Pod | Hash | Role | Requests | Share |
|---|---|---|---:|---:|
| gateway-669f94f69d-jr6zj | 669f94f69d | stable | 56 | 18.42% |
| gateway-669f94f69d-lz8xv | 669f94f69d | stable | 60 | 19.74% |
| gateway-669f94f69d-v7m6j | 669f94f69d | stable | 52 | 17.11% |
| gateway-669f94f69d-vtwgj | 669f94f69d | stable | 65 | 21.38% |
| gateway-cbd89f6c8-mld5r | cbd89f6c8 | canary | 71 | 23.36% |

`kubectl argo rollouts promote gateway` promoted the revision. The final result was Healthy with five Ready pods and ActualWeight 100.

## Manual Abort

Candidate revision 3 used only `APP_VERSION=v3-bad`; no application defect was claimed. It paused at 20% with one candidate and four stable pods. `kubectl argo rollouts abort gateway` was run at `2026-10-04T11:41:41.4262349Z`.

```text
11:41:43.1927438Z canary row still present, 4 Ready stable rows
11:41:44.3704959Z candidate row absent, 4 Ready stable rows
11:41:50.2051164Z candidate absent, 5 Ready stable rows
observable abort-to-five-Ready-stable time: 8.779 seconds
```

The Rollout correctly showed `Degraded / RolloutAborted` while the stable revision served all traffic. This is faster than the Lab 5 Git revert observation of about 18.6 seconds because Rollouts scales down the candidate in-cluster, while Git revert also waits for push, Argo CD detection, synchronization, and a replacement pod.

## Multi-Step Canary

The strategy in the final manifest is 20%/60s, 40%/60s, 60%/60s, 80%/30s, then 100%.

| UTC timestamp | Weight | Stable replicas | Canary replicas | Ready pods | Result |
|---|---:|---:|---:|---:|---|
| 11:42:42.941661Z | 20 | 4 | 1 | 5 | Paused, all Ready |
| 11:44:26.003437Z | 40 | 3 | 2 | 5 | Paused, all Ready |
| 11:45:13.691852Z | 60 | 2 | 3 | 5 | Paused, all Ready |
| 11:56:11.070166Z | 80 | 1 | 4 | 5 | Paused, all Ready |

The in-cluster load generator remained active, so request traffic continued while Rollout changed ReplicaSet sizes. I would run an automated abort at the first 20% analysis stage: it limits blast radius to one pod while still providing enough traffic for a measurement.

### Request-rate evidence during the new multi-step rollout

The original early steps happened before the in-cluster Prometheus pod was started, so they cannot provide a retrospective rate. I therefore started one additional healthy revision (`v7-request-rate-evidence`) through Git and Argo CD, kept the supplied in-cluster load generator running, and queried Prometheus while the new revision was at three real canary stages. The query was `sum by (rs_hash) (rate(gateway_requests_total[30s]))`. `6456b98586` was stable and `55984f6bff` was the canary in this run.

```text
$ kubectl get rollout gateway -o jsonpath="phase={.status.phase} step={.status.currentStepIndex} canary={.status.updatedReplicas} stableRS={.status.stableRS} ready={.status.readyReplicas}"
phase=Progressing step=2 canary=1 stableRS=6456b98586 ready=5

timestamp_utc=2026-10-04T12:16:41.1052558Z
promql=sum by (rs_hash) (rate(gateway_requests_total[30s]))
rs_hash=6456b98586 rate_rps=7.960646458501719
rs_hash=55984f6bff rate_rps=1.9602352282273872
```

```text
$ Prometheus instant query at 2026-10-04T12:18:30Z
promql=sum by (rs_hash) (rate(gateway_requests_total[30s]))
rs_hash=6456b98586 rate_rps=6.720352020737362
rs_hash=55984f6bff rate_rps=3.2247392473204095
```

```text
timestamp_utc=2026-10-04T12:19:43.1295262Z
$ kubectl get rollout gateway -o jsonpath="phase={.status.phase} step={.status.currentStepIndex} canary={.status.updatedReplicas} stableRS={.status.stableRS} ready={.status.readyReplicas}"
phase=Paused step=6 canary=3 stableRS=6456b98586 ready=5
promql=sum by (rs_hash) (rate(gateway_requests_total[30s]))
rs_hash=6456b98586 rate_rps=4.080326426114089
rs_hash=55984f6bff rate_rps=5.542615139397661
```

The first snapshot has one canary pod (20%), the second was taken during the two-canary-pod 40% pause, and the third has three canary pods (60%). The request rate moved towards the canary as its replica count increased; the variation is expected because the load generator creates independent requests and the values are 30-second rates.

### Raw Rollout and AnalysisRun outputs

The following command output was collected after the added healthy revision automatically promoted. It also shows that all earlier candidate ReplicaSets, including the manually aborted revision `gateway-779db4dd9b`, were scaled down.

```text
$ kubectl get rollout gateway -o wide
NAME      DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
gateway   5         5         5            5           47m

$ kubectl get rs -l app=gateway
NAME                 DESIRED   CURRENT   READY   AGE
gateway-55984f6bff   5         5         5       6m45s
gateway-6456b98586   0         0         0       20m
gateway-669f94f69d   0         0         0       47m
gateway-69957fc8bb   0         0         0       24m
gateway-74d8846df    0         0         0       34m
gateway-779db4dd9b   0         0         0       40m
gateway-7cff4c48b9   0         0         0       39m
gateway-cbd89f6c8    0         0         0       44m
```

For the earlier manual abort, the actual Rollout status immediately after `kubectl argo rollouts abort gateway` was `Degraded`, message `RolloutAborted: Rollout aborted update to revision 3`, `ActualWeight: 0`, `Updated: 0`, and `Ready: 5`. The timestamped pod polling output in the Manual Abort section shows when the candidate disappeared and all five stable pods were Ready. The original manual promotion completed with `Status: Healthy`, `Step: 5/5`, `ActualWeight: 100`, and five Ready pods.

```text
$ kubectl get analysisrun -o wide
NAME                      STATUS       AGE
gateway-55984f6bff-8-2    Successful   5m36s
gateway-6456b98586-7-2    Successful   19m
gateway-69957fc8bb-6-2    Failed       23m
gateway-74d8846df-5-2     Failed       33m
gateway-74d8846df-5-2.1   Successful   30m
```

The relevant failed-run YAML below is raw output for the automatic bad-canary abort. It records the latest canary hash, the resolved Prometheus query, the two failed measurements, and the controller's failure message.

```yaml
$ kubectl get analysisrun gateway-69957fc8bb-6-2 -o yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisRun
metadata:
  annotations:
    rollout.argoproj.io/revision: "6"
  creationTimestamp: "2026-10-04T11:58:37Z"
  generation: 4
  labels:
    app: gateway
    rollout-type: Step
    rollouts-pod-template-hash: 69957fc8bb
    step-index: "2"
  name: gateway-69957fc8bb-6-2
  namespace: default
spec:
  args:
  - name: canary-hash
    value: 69957fc8bb
  metrics:
  - count: 3
    failureLimit: 1
    initialDelay: 60s
    interval: 20s
    name: error-rate
    provider:
      prometheus:
        address: http://prometheus.monitoring.svc.cluster.local:9090
        query: |
          (
            sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}",status=~"5.."}[60s]))
            or on() vector(0)
          )
          /
          sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}"}[60s]))
    successCondition: result[0] < 0.05
status:
  completedAt: "2026-10-04T11:59:57Z"
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
  - count: 2
    failed: 2
    measurements:
    - finishedAt: "2026-10-04T11:59:37Z"
      phase: Failed
      startedAt: "2026-10-04T11:59:37Z"
      value: '[1]'
    - finishedAt: "2026-10-04T11:59:57Z"
      phase: Failed
      startedAt: "2026-10-04T11:59:57Z"
      value: '[1]'
    metadata:
      ResolvedPrometheusQuery: |
        (
          sum(rate(gateway_requests_total{rs_hash="69957fc8bb",status=~"5.."}[60s]))
          or on() vector(0)
        )
        /
        sum(rate(gateway_requests_total{rs_hash="69957fc8bb"}[60s]))
    name: error-rate
    phase: Failed
  phase: Failed
  startedAt: "2026-10-04T11:58:37Z"
```

## Prometheus Analysis Bonus

The supplied `labs/lab7/prometheus.yaml` and `analysis-template.yaml` were applied. In-cluster Prometheus was Running and discovered each gateway pod with the relabeled `rs_hash`:

```text
gateway-7cff4c48b9-lj9fl  rs_hash=7cff4c48b9  health=up
gateway-7cff4c48b9-zr7cs  rs_hash=7cff4c48b9  health=up
gateway-7cff4c48b9-jvgqd  rs_hash=7cff4c48b9  health=up
gateway-7cff4c48b9-zwj7v  rs_hash=7cff4c48b9  health=up
gateway-7cff4c48b9-29qbk  rs_hash=7cff4c48b9  health=up
sum(rate(gateway_requests_total[30s])) = 0.6388933333333333
```

The AnalysisTemplate `gateway-error-rate` filters the canary by `{{args.canary-hash}}`, has `initialDelay: 60s`, three 20-second measurements, success condition `< 0.05`, and failure limit 1.

### Successful AnalysisRun

The first attempted good analysis failed because the cluster restart had left PostgreSQL without the `events` table; logs showed `relation "events" does not exist`. I seeded the existing database using `app/seed.sql` (`INSERT 0 5`) and retried the same good revision. The retried AnalysisRun was successful:

```text
name: gateway-74d8846df-5-2.1
startedAt: 2026-10-04T11:51:52Z
completedAt: 2026-10-04T11:53:32Z
phase: Successful
measurements: [0] at 11:52:52Z, [0] at 11:53:12Z, [0] at 11:53:32Z
```

No manual promotion was used. It automatically advanced through 40%, 60%, 80%, and finally reached Healthy at 100% with five Ready pods.

### Failed AnalysisRun and Automatic Abort

Bad revision 6 used `EVENTS_URL=http://broken-on-purpose:8081` and timeout 2000ms. Only for this controlled candidate, liveness and readiness used `/metrics`; otherwise `/health` would prevent it becoming Ready and Prometheus could not measure it. The stable revision retained normal settings.

The canary received real loadgen traffic and logged real failures:

```text
events service error: [Errno -2] Name or service not known
GET /events HTTP/1.1 502 Bad Gateway
```

```text
name: gateway-69957fc8bb-6-2
startedAt: 2026-10-04T11:58:37Z
completedAt: 2026-10-04T11:59:57Z
phase: Failed
measurements: [1] at 11:59:37Z, [1] at 11:59:57Z
message: failed (2) > failureLimit (1)
```

No manual abort command was issued. Rollout automatically changed to `Degraded / RolloutAborted`, `ActualWeight: 0`, and the bad ReplicaSet scaled down.

Beyond error rate, latency p95 and dependency-health availability would make the analysis more complete.

## Recovery

Git was restored to `APP_VERSION=v7-recovered`, `EVENTS_URL=http://events:8081`, `PAYMENTS_URL=http://payments:8082`, timeout 5000, and `/health` for both probes. The recovery AnalysisRun `gateway-6456b98586-7-2` completed Successfully at `2026-10-04T12:04:23Z` with three `[0]` measurements. It was then fully promoted; the Rollout was Healthy at ActualWeight 100 with exactly five Ready gateway pods, and Argo CD was `Synced Healthy` at revision `c0f161e066c70c7192de7eddd798eb59915d732f`.

The load generator was deleted. In-cluster Prometheus remains Running for Lab 8. Final gateway checks through a temporary port-forward were real:

```text
health={"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
events_count=5
reservation_id=5249d48a-94a9-4662-9d3e-ea0ff010f6bc
payment={"order_id":"5249d48a-94a9-4662-9d3e-ea0ff010f6bc","event_id":3,"quantity":1,"total_cents":15000,"status":"confirmed"}
```

### Final state after request-rate evidence rollout

The later request-rate evidence rollout replaced the historical recovery revision. The current desired Git state is `APP_VERSION=v7-request-rate-evidence`; it keeps the normal dependency URLs, timeout `5000`, and `/health` for both probes. The temporary load generator used for that last rollout was deleted after it reached Healthy.

```text
$ kubectl get rollout gateway -o jsonpath="app_version={.spec.template.spec.containers[0].env[0].value} phase={.status.phase} ready={.status.readyReplicas} available={.status.availableReplicas}"
app_version=v7-request-rate-evidence phase=Healthy ready=5 available=5

$ kubectl get application quickticket -n argocd -o jsonpath="sync={.status.sync.status} health={.status.health.status} revision={.status.sync.revision}"
sync=Synced health=Healthy revision=293c54e6579b51be5b2281a755909ce71d2bb463

$ kubectl get pods -l app=loadgen
No resources found in default namespace.

gateway_http_status=200
gateway_body={"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
events={"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
payments={"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

## Acceptance Checklist

- [x] Argo Rollouts controller and Windows plugin installed
- [x] Gateway converted to a five-replica canary Rollout
- [x] Manual 20% canary, traffic evidence, promotion, and abort completed
- [x] Multi-step 20/40/60/80/100 strategy observed
- [x] Prometheus AnalysisTemplate installed and queried
- [x] Good AnalysisRun succeeded and auto-promoted
- [x] Bad AnalysisRun failed and automatically aborted
