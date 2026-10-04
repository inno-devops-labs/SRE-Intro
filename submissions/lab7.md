# Lab 7 — Progressive Delivery: Canary Deployments

## Environment

The experiments ran on the `k3d-quickticket` context with one Ready k3s node (`v1.35.5+k3s1`). The gateway used the existing GHCR image `ghcr.io/kujifined/quickticket-gateway:8f683490648451a95154ddcf2f91f749a53819ad`. Changing `APP_VERSION` created distinct pod-template revisions; these labels identify the experiments, but the good revisions use the same application image. The bad revisions changed `EVENTS_URL`.

The macOS arm64 kubectl plugin reported:

```text
$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.10.0+d90700a
  BuildDate: 2026-08-27T15:26:09Z
  GitCommit: d90700ae8d71d141561f0c546e19f999bb335cbd
  GitTreeState: clean
  GoVersion: go1.26.7
  Compiler: gc
  Platform: darwin/arm64
```

The first client-side apply of the controller's installation manifest hit Kubernetes' 262144-byte annotation limit for the `analysisruns` and `rollouts` CRDs. Reapplying the official manifest with `kubectl apply --server-side --force-conflicts` installed both. The controller became Available and both CRDs were present.

ArgoCD's `quickticket` Application had automated sync without `selfHeal`. Its source path `k8s` on the default branch was absent at the time of the experiments, so its sync status was Unknown. It did not overwrite the live Rollout during these tests.

## Task 1 — Manual canary

I converted the existing gateway Deployment to a five-replica Rollout, retaining its selector, Service, image pull secret, environment, ports, probes, and resources. The Service selector remained `app: gateway`. After deleting the Deployment, the initial Rollout reached Healthy with five Ready replicas.

The manual strategy used `20% → indefinite pause → 60% → 30-second pause → 100%`. Adding `APP_VERSION=v2` created the first new pod-template revision. At the indefinite pause:

```text
$ kubectl argo rollouts get rollout gateway
Status:          ॥ Paused
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

gateway-6695969d8-4lsrg  Pod  ✔ Running  ready:1/1
gateway-5f9c45776-82m44  Pod  ✔ Running  ready:1/1
gateway-5f9c45776-jvtnk  Pod  ✔ Running  ready:1/1
gateway-5f9c45776-mtgpg  Pod  ✔ Running  ready:1/1
gateway-5f9c45776-w42qd  Pod  ✔ Running  ready:1/1
```

The load generator ran inside the cluster and accessed `service/gateway`. The following counts came from each pod's logs over the same 35-second window:

```text
pod/gateway-5f9c45776-82m44 hash=5f9c45776 events_requests=26
pod/gateway-5f9c45776-jvtnk hash=5f9c45776 events_requests=30
pod/gateway-5f9c45776-mtgpg hash=5f9c45776 events_requests=31
pod/gateway-5f9c45776-w42qd hash=5f9c45776 events_requests=34
pod/gateway-6695969d8-4lsrg hash=6695969d8 events_requests=32
```

Thus, the canary handled 32/153 requests (20.9%) and the four stable pods handled 121/153 (79.1%). These are request counts, so small deviations from the configured pod split are expected.

I ran `kubectl argo rollouts promote gateway`. The controller advanced through the 60% step and its timed pause. The final output was:

```text
rollout 'gateway' promoted
Status:          ✔ Healthy
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
```

The controller's recorded events confirm the intermediate progression:

```text
RolloutStepCompleted  Rollout step 2/5 completed (pause)
RolloutStepCompleted  Rollout step 3/5 completed (setWeight: 60)
RolloutStepCompleted  Rollout step 4/5 completed (pause: 30s)
RolloutStepCompleted  Rollout step 5/5 completed (setWeight: 100)
```

For the bad revision, I set `APP_VERSION=v3-bad` and `EVENTS_URL=http://payments:8082`. The payments service answers `/health` with 200 but has no `/events` route, so the canary stayed Ready while real requests returned 502. A non-resolving `EVENTS_URL` would also make this gateway's readiness probe fail and would not exercise a traffic-serving canary.

```text
$ kubectl logs gateway-5d47d5dcd4-rqht7 --since=20s
INFO:     10.42.0.42:44258 - "GET /events HTTP/1.1" 502 Bad Gateway
INFO:     10.42.0.42:44304 - "GET /events HTTP/1.1" 502 Bad Gateway

$ kubectl argo rollouts get rollout gateway
Status:          ॥ Paused
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
  Updated:       1
  Ready:         5
```

I measured from the abort command to removal of the bad pod from the gateway Service endpoints:

```text
before=2026-10-04T21:48:40Z
endpoints_before=gateway-6695969d8-4lsrg gateway-6695969d8-bkn7t gateway-6695969d8-x74hs gateway-6695969d8-kgq8d gateway-5d47d5dcd4-rqht7
rollout 'gateway' aborted
after=2026-10-04T21:48:42Z elapsed_seconds=1
endpoints_after=gateway-6695969d8-4lsrg gateway-6695969d8-bkn7t gateway-6695969d8-x74hs gateway-6695969d8-kgq8d
```

Immediately afterward, Rollouts reported `Degraded`, `SetWeight: 0`, and `ActualWeight: 0`; the four old stable pods were Ready while the fifth was being recreated. The measured 1 second is the interval until all *eligible Service endpoints* were stable, at one-second polling resolution. It is not a measurement of client connection draining.

In Lab 5, the recorded interval from revert push to ArgoCD Synced/Healthy was 23 seconds. The manual Argo abort was faster here because the stable ReplicaSet was already running; Git revert requires a commit, push, GitOps reconciliation, and synchronization. The two timings start at different points (abort command versus completed revert push), so they are operational comparisons rather than identical end-to-end measurements.

## Task 2 — Multi-step canary

After restoring the working `EVENTS_URL`, I used this temporary strategy and created revision `APP_VERSION=v4-good`:

```yaml
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

These values were transcribed from timestamped `kubectl argo rollouts get rollout gateway` outputs during the rollout:

| UTC time | Status | Step | SetWeight | ActualWeight | Updated | Ready |
|---|---|---:|---:|---:|---:|---:|
| 21:49:30 | Paused | 1/9 | 20 | 20 | 1 | 5 |
| 21:50:19 | Progressing | 2/9 | 40 | 25 | 2 | 4 |
| 21:51:27 | Progressing | 4/9 | 60 | 50 | 3 | 4 |
| 21:52:32 | Progressing | 6/9 | 80 | 75 | 4 | 4 |
| 21:53:05 | Progressing | 8/9 | 100 | 100 | 4 | 4 |

The intermediate `ActualWeight` values reflect pods starting and readiness changing during each step. Separate observations at the 40%, 60%, and 80% pauses showed `ActualWeight` equal to `SetWeight`, with 2, 3, and 4 updated pods respectively, all five Ready. The final status was Healthy, `Updated: 5`, `Ready: 5`, and `ActualWeight: 100`.

The Lab 3 host-side Grafana stack cannot scrape pod IPs inside k3d. I observed the stages with Rollouts' watch output and checked the in-cluster Prometheus metrics. At the settled 40% stage, the `/events` request rates over a one-minute window were 2.78 requests/s on stable hash `6695969d8` and 1.38 requests/s on canary hash `6fff896cc8`. At 60% they were 2.06 and 2.19 requests/s; at 80%, 1.69 and 2.70 requests/s. The load generator kept traffic flowing at each stage. No Grafana screenshot was taken.

I would abort automatically at the first 20% step if the canary error rate breached the threshold. With five replicas and pod-based routing, 20% is one pod and the smallest nonzero step; it limits exposure while still producing a measurable signal.

## Bonus — Automated canary analysis

I installed the provided in-cluster Prometheus and committed `k8s/analysis-template.yaml`. The template queries `gateway_requests_total` only for the latest canary's `rs_hash`. The numerator treats absent 5xx series as zero; the denominator remains strict so an unobserved canary cannot pass as healthy. The 60-second initial delay allows Prometheus discovery and a usable rate window. With `failureLimit: 1`, two failed measurements cause failure.

```text
$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   0s

$ curl 'http://localhost:9091/api/v1/targets?state=active'  # selected fields
gateway-6695969d8-bkn7t rs=6695969d8 up
gateway-6695969d8-kgq8d rs=6695969d8 up
gateway-6695969d8-4lsrg rs=6695969d8 up
gateway-6fff896cc8-jd46l rs=6fff896cc8 up
gateway-6fff896cc8-clt9n rs=6fff896cc8 up
```

The final strategy in `k8s/gateway.yaml` is `20% → 20-second pause → analysis → 50% → 20-second pause → 100%`. The analysis step passes `podTemplateHashValue: Latest` as `canary-hash`.

### Good revision

For `APP_VERSION=v5-good`, no manual promote was issued. The AnalysisRun succeeded and the Rollout advanced to Healthy at 100%:

```text
$ kubectl get analysisrun
NAME                     STATUS       AGE
gateway-585c8c7867-5-2   Successful   2m16s

$ kubectl get analysisrun gateway-585c8c7867-5-2 -o yaml  # status excerpt
measurements:
- phase: Successful
  value: '[0]'
- phase: Successful
  value: '[0]'
- phase: Successful
  value: '[0]'
phase: Successful

$ kubectl argo rollouts get rollout gateway  # final status excerpt
Status:          ✔ Healthy
  Step:          6/6
  SetWeight:     100
  ActualWeight:  100
  Updated:       5
  Ready:         5
```

### Bad revision

For `APP_VERSION=v6-bad`, the canary again used `EVENTS_URL=http://payments:8082`. Its pod was Ready, `/health` returned 200, and `/events` returned 502. Prometheus reported these two canary-only error ratios:

```text
$ kubectl get analysisrun
NAME                     STATUS       AGE
gateway-585c8c7867-5-2   Successful   4m33s
gateway-6ff5565cf9-6-2   Failed       82s

$ kubectl get analysisrun gateway-6ff5565cf9-6-2 -o yaml  # status excerpt
message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
metricResults:
- count: 2
  failed: 2
  measurements:
  - phase: Failed
    value: '[0.4385964912280701]'
  - phase: Failed
    value: '[0.42727272727272725]'
  phase: Failed
phase: Failed
```

The error ratio is below 1 because the same canary also served successful health requests. It exceeds the configured 5% threshold by a wide margin.

```text
$ kubectl argo rollouts get rollout gateway  # immediately after analysis
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 6: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
  Updated:       0
```

After abort, `service/gateway` endpoints contained only stable hash `585c8c7867` pods. I restored the working `EVENTS_URL` and `APP_VERSION=v5-good`, applied the final manifest, and removed loadgen. The final live Rollout is Healthy with five Ready replicas. Both final YAML files passed `kubectl apply --dry-run=server`.

For fuller canary analysis I would add p95 or p99 request latency using the existing `gateway_request_duration_seconds` histogram. A release can keep 5xx low while making successful requests unacceptably slow.
