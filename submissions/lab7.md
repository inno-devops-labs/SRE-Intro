# Lab 7 — Progressive Delivery: Canary Deployments

Cluster: local k3d (`quickticket`), gateway running as an Argo Rollout with 5 replicas.
QuickTicket images built locally (`quickticket-*:v1`) and imported into k3d.

---

## Task 1 — Manual Canary Deployment

### 1. `kubectl argo rollouts version`

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:11:48Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: darwin/arm64
```

### 2. Canary paused at 20%

After changing `APP_VERSION` `v1 → v2` and applying, the rollout pauses at step 1/5 (`setWeight: 20`):
1 canary pod (revision 2) + 4 stable pods (revision 1).

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          quickticket-gateway:v1 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                KIND        STATUS        AGE  INFO
⟳ gateway                           Rollout     ॥ Paused   66s
├──# revision:2
│  └──⧉ gateway-6567ff84c           ReplicaSet  ✔ Healthy  21s  canary
│     └──□ gateway-6567ff84c-bchrn  Pod         ✔ Running  19s  ready:1/1
└──# revision:1
   └──⧉ gateway-bb5476b6            ReplicaSet  ✔ Healthy  66s  stable
      ├──□ gateway-bb5476b6-dm9sk   Pod         ✔ Running  66s  ready:1/1
      ├──□ gateway-bb5476b6-gpzs6   Pod         ✔ Running  66s  ready:1/1
      ├──□ gateway-bb5476b6-h6n2z   Pod         ✔ Running  66s  ready:1/1
      └──□ gateway-bb5476b6-vblhf   Pod         ✔ Running  66s  ready:1/1
```

**Traffic split verification** — in-cluster loadgen through kube-proxy (`kubectl port-forward` is NOT used,
because it pins a single endpoint). Per-pod request counts after ~40s:

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl apply -f labs/lab7/loadgen.yaml
deployment.apps/loadgen created
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ sleep 40
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ for pod in $(kubectl get pods -l app=gateway -o name); do
>   count=$(kubectl logs $pod 2>/dev/null | grep -c 'GET /events')
>   img=$(kubectl get $pod -o jsonpath='{.metadata.labels.rollouts-pod-template-hash}')
>   echo "$pod rev-hash=$img events_requests=$count"
> done
pod/gateway-6567ff84c-bchrn rev-hash=6567ff84c events_requests=33   <- canary
pod/gateway-bb5476b6-dm9sk  rev-hash=bb5476b6  events_requests=31
pod/gateway-bb5476b6-gpzs6  rev-hash=bb5476b6  events_requests=23
pod/gateway-bb5476b6-h6n2z  rev-hash=bb5476b6  events_requests=32
pod/gateway-bb5476b6-vblhf  rev-hash=bb5476b6  events_requests=26
```

Canary got 33 of 145 requests ≈ 23% — matches `setWeight: 20` (1 of 5 pods), with small variance for the short
sample.

### 3. After `promote` — progression to 100%

`promote` advances to 60%, then after the 30s pause auto-promotes to 100%. Revision 2 becomes stable, revision 1
is scaled down.

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl argo rollouts promote gateway
rollout 'gateway' promoted
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          quickticket-gateway:v1 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                KIND        STATUS        AGE    INFO
⟳ gateway                           Rollout     ✔ Healthy   3m12s
├──# revision:2
│  └──⧉ gateway-6567ff84c           ReplicaSet  ✔ Healthy   2m27s  stable
│     ├──□ gateway-6567ff84c-bchrn  Pod         ✔ Running   2m25s  ready:1/1
│     ├──□ gateway-6567ff84c-gprsl  Pod         ✔ Running   49s    ready:1/1
│     ├──□ gateway-6567ff84c-whhxv  Pod         ✔ Running   49s    ready:1/1
│     ├──□ gateway-6567ff84c-b6pwn  Pod         ✔ Running   15s    ready:1/1
│     └──□ gateway-6567ff84c-vt692  Pod         ✔ Running   15s    ready:1/1
└──# revision:1
   └──⧉ gateway-bb5476b6            ReplicaSet  • ScaledDown  3m12s
```

### 4. After `abort` — instant rollback

Deployed a "bad" version (`APP_VERSION: v3-bad`, revision 3). It paused at 20% with 1 canary pod. Then aborted:

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ date +%T; kubectl argo rollouts abort gateway
21:30:57
rollout 'gateway' aborted
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ date +%T; kubectl argo rollouts get rollout gateway
21:31:00
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          quickticket-gateway:v1 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         4
  Available:     4

├──# revision:3
│  └──⧉ gateway-7c4c865f8b   ReplicaSet  • ScaledDown  (canary killed)
├──# revision:2
│  └──⧉ gateway-6567ff84c    ReplicaSet  ◌ Progressing  stable (scaling back to 5)
```

Within ~3 seconds the canary (revision 3) is scaled down and the stable revision-2 pods keep serving — they
were never taken down.

### 5. Abort speed vs `git revert` rollback (Lab 5)

**Time from `abort` to all traffic on the stable version: ~2–3 seconds.**

The canary was only ever 20% of pods, and the stable pods (revision 2) were never scaled down — so aborting
just deletes the single canary pod and traffic instantly returns to 100% stable. There is **no rebuild, no
image pull, no redeploy** involved.

Compare with the Lab 5 GitOps rollback via `git revert`:
- write a revert commit → push → CI pipeline builds a new image → pushes to GHCR →
  ArgoCD detects the change / syncs → pulls the image → rolls the pods.
- That chain is **minutes** (the CI build alone is usually 1–3 min), and until the new pods are Ready the bad
  version keeps serving.

So Argo Rollouts `abort` is roughly **two orders of magnitude faster** for reverting a bad canary, because the
known-good version is still running the whole time — rollback is a scale-down, not a rebuild-and-redeploy.

---

## Task 2 — Multi-Step Canary with Observation

### Strategy YAML used

```yaml
strategy:
  canary:
    steps:
      - setWeight: 20
      - pause: {duration: 60s}    # observe 1 min
      - setWeight: 40
      - pause: {duration: 60s}
      - setWeight: 60
      - pause: {duration: 60s}
      - setWeight: 80
      - pause: {duration: 30s}
      - setWeight: 100
```

### Observation across steps

Triggered a new version (`APP_VERSION: v4`, revision 4) with continuous loadgen running. Snapshots of
`kubectl argo rollouts get rollout gateway` as it advances — the updated-replica count climbs in lock-step with
the weight:

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl argo rollouts get rollout gateway
# STEP A
  Step:          1/9
  SetWeight:     20
  Updated:       1     Ready: 5   Available: 5   (॥ Paused)

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl argo rollouts promote gateway; kubectl argo rollouts get rollout gateway
# STEP B
  Step:          3/9
  SetWeight:     40
  Updated:       2     Ready: 5   Available: 5   (॥ Paused)

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl argo rollouts promote gateway; kubectl argo rollouts get rollout gateway
# STEP C
  Step:          5/9
  SetWeight:     60
  Updated:       3     Ready: 5   Available: 5   (॥ Paused)

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl argo rollouts promote gateway --full
# → Step 9/9  SetWeight 100 → ✔ Healthy
```

Observations:
- **Request rate stayed steady** across steps — total pod count is always 5 (Available never dropped below 5),
  so throughput to clients is unaffected while the canary/stable ratio shifts.
- **Updated replicas climbed 1 → 2 → 3** as weight went 20% → 40% → 60% (reaching 5 at 100%). Argo scales the
  canary ReplicaSet up and the stable one down to keep the total at `replicas: 5`.

### At what canary % would you want an automated abort? Why?

I'd want the automated analysis gate **as early as possible — right after the first `setWeight: 20` step**.
The whole point of a canary is to expose a bad version to the *smallest* possible blast radius before
committing. If the 20% canary already shows elevated 5xx/latency, there's no value in letting it reach 40% or
60% — that only harms more users. So the abort decision should fire at 20%, after enough traffic has hit the
canary for the metric to be statistically meaningful (hence a short `initialDelay` before the first
measurement). Later steps (50%, 80%) are for confidence/soak, not for first detection.

---

## Bonus Task — Automated Canary Analysis

In-cluster Prometheus (`labs/lab7/prometheus.yaml`) scrapes each gateway pod on `:8080/metrics` and relabels
`rollouts-pod-template-hash → rs_hash`, so the AnalysisTemplate query can isolate canary replicas.

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl -n monitoring rollout status deployment/prometheus --timeout=90s
deployment "prometheus" successfully rolled out
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl port-forward -n monitoring svc/prometheus 9091:9090 &
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ curl -s 'http://localhost:9091/api/v1/targets?state=active' | python3 -c "
> import sys,json
> for t in json.load(sys.stdin)['data']['activeTargets']:
>     print(t['labels'].get('pod'), 'rs=', t['labels'].get('rs_hash'), t['health'])"
gateway-7fb5b8f95c-jlfcm rs= 7fb5b8f95c up
gateway-7fb5b8f95c-kdzrz rs= 7fb5b8f95c up
gateway-7fb5b8f95c-24xlt rs= 7fb5b8f95c up
gateway-7fb5b8f95c-p5qs5 rs= 7fb5b8f95c up
gateway-7fb5b8f95c-q6j5c rs= 7fb5b8f95c up
```

### AnalysisTemplate installed

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl apply -f labs/lab7/analysis-template.yaml
analysistemplate.argoproj.io/gateway-error-rate created
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   16m
```

Analysis step wired into the canary strategy (only for the bonus run — the committed `k8s/gateway.yaml` keeps
the Task 1 strategy):

```yaml
strategy:
  canary:
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

### Good version → auto-promote, bad version → auto-abort

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl get analysisrun
NAME                      STATUS       AGE
gateway-55f4479ccc-7-2    Failed       102s
gateway-cf8756f44-5-2.1   Successful   11m
```

**Successful run (good version)** — 3 measurements, error-rate `[0]`, auto-promoted to 100% with no human
intervention:

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl get analysisrun gateway-cf8756f44-5-2.1 -o yaml | grep -E "value:|phase:"
      value: '[0]'
      phase: Successful
      value: '[0]'
      phase: Successful
      value: '[0]'
      phase: Successful
    phase: Successful
  phase: Successful
```

**Failed run (bad version)** — canary's `EVENTS_URL` pointed at a healthy host with no `/events` route, so
`/events` returns 502 while `/health` still passes (canary stays Ready). Prometheus measures ~43% 5xx on the
canary, which exceeds the 5% threshold; after `failed (2) > failureLimit (1)` the rollout auto-aborts:

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl get analysisrun gateway-55f4479ccc-7-2 -o yaml | grep -E "message:|value:|phase:|failed:"
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
    failed: 2
      phase: Failed
      value: '[0.43]'
      phase: Failed
      value: '[0.4095238095238095]'
    phase: Failed
  phase: Failed
    failed: 1
```

Final rollout state after the aborted bad deploy — **Degraded, canary scaled down, 5 stable pods still Ready**:

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 7: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          quickticket-gateway:v1 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                KIND         STATUS        AGE    INFO
⟳ gateway                           Rollout      ✖ Degraded    23m
├──# revision:7
│  ├──⧉ gateway-55f4479ccc          ReplicaSet   • ScaledDown  2m21s  canary
│  └──α gateway-55f4479ccc-7-2      AnalysisRun  ✖ Failed      115s   ✖ 2
├──# revision:5
│  └──⧉ gateway-cf8756f44           ReplicaSet   ✔ Healthy     stable (5 pods Ready)
```

### What metric would you add beyond error rate for a more complete canary analysis?

**Latency (p95/p99 request duration)** is the first thing I'd add — the gateway already exports
`gateway_request_duration_seconds`, so an AnalysisTemplate could gate on p95 latency the same way it gates on
error rate. A canary can return HTTP 200 for everything (zero error rate) yet be twice as slow due to a
regression, a bad DB query plan, or GC pressure — error rate alone would happily promote it. Beyond latency,
useful additions are **saturation** (CPU/memory of the canary pods vs stable) and **business/proxy signals**
like circuit-breaker trips (`gateway_circuit_breaker_transitions_total`) or rate-limit rejections. Combining
error rate + latency + saturation follows the golden-signals approach and catches "slow but not erroring"
regressions that a pure 5xx gate misses.

---

## Summary

| Task | Result |
|------|--------|
| Task 1 — Manual canary | ✅ Rollouts installed, gateway converted to Rollout, canary at 20%, manual promote to 100%, bad version aborted (instant rollback) |
| Task 2 — Multi-step canary | ✅ 9-step strategy applied, weights/updated-replicas observed 20→40→60→100 |
| Bonus — Automated analysis | ✅ AnalysisTemplate + in-cluster Prometheus; good version auto-promoted, bad version auto-aborted |
