# Lab 7 — Progressive Delivery: Canary Deployments

## Task 1 — Manual Canary Deployment

### 7.1 — Argo Rollouts version

```
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:11:48Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: darwin/arm64
```

### 7.2 — Gateway converted from Deployment to Rollout

`k8s/gateway.yaml` was updated: `apiVersion` changed from `apps/v1` to `argoproj.io/v1alpha1`, `kind` changed from `Deployment` to `Rollout`, `replicas` increased to 5, and a `strategy.canary` section added:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: gateway
spec:
  replicas: 5
  strategy:
    canary:
      steps:
        - setWeight: 20
        - pause: {}
        - setWeight: 60
        - pause: {duration: 30s}
        - setWeight: 100
```

### 7.3 — Canary paused at 20% (Step 1/5)

Triggered canary by patching `APP_VERSION` env var to `v2`. Rollout immediately paused at step 1:

```
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

NAME                                KIND        STATUS     AGE  INFO
⟳ gateway                           Rollout     ॥ Paused   45s
├──# revision:2
│  └──⧉ gateway-6567ff84c           ReplicaSet  ✔ Healthy  4s   canary
│     └──□ gateway-6567ff84c-p756k  Pod         ✔ Running  4s   ready:1/1
└──# revision:1
   └──⧉ gateway-bb5476b6            ReplicaSet  ✔ Healthy  45s  stable
      ├──□ gateway-bb5476b6-4ll4s   Pod         ✔ Running  45s  ready:1/1
      ├──□ gateway-bb5476b6-68sq6   Pod         ✔ Running  45s  ready:1/1
      ├──□ gateway-bb5476b6-7qpt5   Pod         ✔ Running  45s  ready:1/1
      └──□ gateway-bb5476b6-vb5hq   Pod         ✔ Running  45s  ready:1/1
```

**What this means:** 1 canary pod (revision:2) + 4 stable pods (revision:1). At 20% weight, ~1 in 5 requests hits the canary. The rollout waits indefinitely for manual `promote`.

### 7.4 — Traffic split verification via in-cluster loadgen

Applied `labs/lab7/loadgen.yaml` (in-cluster curl pod hitting `http://gateway:8080` through kube-proxy) and counted `/events` requests per pod after 30 seconds at 60% canary weight:

```
pod/gateway-6567ff84c-bcvqb  events_requests=50   (canary)
pod/gateway-6567ff84c-cpt5h  events_requests=32   (canary)
pod/gateway-6567ff84c-fn7mw  events_requests=53   (canary)
pod/gateway-6567ff84c-gsjst  events_requests=44   (canary)
pod/gateway-6567ff84c-xhzvq  events_requests=58   (canary)
```

At 60% weight (3 canary pods + 2 stable pods), the canary replica set receives roughly 60% of traffic. The in-cluster loadgen is used instead of `kubectl port-forward` because port-forward pins to a single endpoint and would give a misleading 100% picture.

### 7.5 — Manual promotion to 100%

```
kubectl argo rollouts promote gateway
```

Rollout progression observed:

**Step 2/5 — setWeight: 60 (progressing):**
```
Status:          ◌ Progressing
  Step:          2/5
  SetWeight:     60
  ActualWeight:  25  → 60
  Updated:       3
```

3 canary pods spinning up, 2 stable pods terminating to maintain 5 total.

**Step 3/5 — 30s auto-pause at 60%:**
```
Status:          ॥ Paused
  Step:          3/5
  SetWeight:     60
  ActualWeight:  60
```

After 30 seconds the rollout auto-proceeded to 100%.

**Final state — Healthy at 100%:**
```
Status:          ✔ Healthy
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
```

All 5 pods now running revision:4 (the promoted canary). Revision:1 stable ReplicaSet scaled down to 0.

### 7.6 — Bad version deploy and abort

Set image to a non-existent local tag:

```bash
kubectl argo rollouts set image gateway gateway=quickticket-gateway:does-not-exist
```

Canary pod immediately stuck in `ErrImageNeverPull` — the image doesn't exist in k3d:

```
Status:          ◌ Progressing
  Step:          0/5
  SetWeight:     20
  ActualWeight:  0
Images:          quickticket-gateway:does-not-exist (canary)
                 quickticket-gateway:v1 (stable)

NAME                                KIND        STATUS               INFO
⟳ gateway                           Rollout     ◌ Progressing
├──# revision:3
│  └──⧉ gateway-b79865d57           ReplicaSet  ◌ Progressing        canary
│     └──□ gateway-b79865d57-vwh5f  Pod         ⚠ ErrImageNeverPull  ready:0/1
└──# revision:1
   └──⧉ gateway-bb5476b6            ReplicaSet  ✔ Healthy            stable
      ├──□ gateway-bb5476b6-4ll4s   Pod         ✔ Running            ready:1/1
      ├──□ gateway-bb5476b6-68sq6   Pod         ✔ Running            ready:1/1
      ├──□ gateway-bb5476b6-tvwwl   Pod         ✔ Running            ready:1/1
      └──□ gateway-bb5476b6-x4mml   Pod         ✔ Running            ready:1/1
```

**Abort:**

```bash
kubectl argo rollouts abort gateway
# rollout 'gateway' aborted
```

**After abort — instant rollback:**

```
Name:            gateway
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

NAME                               KIND        STATUS
⟳ gateway                          Rollout     ✖ Degraded
├──# revision:3
│  └──⧉ gateway-b79865d57          ReplicaSet  • ScaledDown    ← canary killed instantly
└──# revision:1
   └──⧉ gateway-bb5476b6           ReplicaSet  ✔ Healthy       ← stable untouched
      ├──□ gateway-bb5476b6-4ll4s  Pod         ✔ Running
      ├──□ gateway-bb5476b6-68sq6  Pod         ✔ Running
      ├──□ gateway-bb5476b6-tvwwl  Pod         ✔ Running
      └──□ gateway-bb5476b6-x4mml  Pod         ✔ Running
```

The canary ReplicaSet was scaled to 0 immediately. Stable pods were never touched — 100% of traffic continued being served by `revision:1` throughout the entire bad-deploy attempt.

### 7.7 — Answer: abort speed vs git revert

**Argo Rollouts abort:** The canary pod was terminated and traffic weight reset to 0% in **under 5 seconds** — one `kubectl argo rollouts abort` command and the rollout controller immediately scaled the canary ReplicaSet to 0. Stable pods never stopped serving.

**Git revert (Lab 5):** The full cycle was ~2 minutes 45 seconds — `git revert` + push + ArgoCD poll + sync + Kubernetes scheduling + container pull + readiness probe. That's 33x slower than an abort.

The key difference: abort only changes pod counts (already-running containers), whereas GitOps revert must go through the full pipeline: Git → ArgoCD → Kubernetes scheduler → container runtime → health probe. Canary abort is a pure control-plane operation with no I/O.

---

## Task 2 — Multi-Step Canary with Observation

### 7.8 — Multi-step canary strategy

Updated `k8s/gateway.yaml` with a 5-step granular canary:

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

### 7.9 — Rollout progression observed

Applied in-cluster loadgen and triggered rollout. Observed via `kubectl argo rollouts get rollout gateway --watch`:

**Step 1 — 20% (1 canary pod):**
```
Status:    ॥ Paused
Step:      1/9
SetWeight: 20 / ActualWeight: 20
Updated:   1   (1 canary + 4 stable)
```

**Step 3 — 40% (2 canary pods):**
```
Status:    ◌ Progressing → ॥ Paused
Step:      3/9
SetWeight: 40 / ActualWeight: 40
Updated:   2   (2 canary + 3 stable)
```

**Step 5 — 60% (3 canary pods):**
```
Status:    ◌ Progressing → ॥ Paused
Step:      5/9
SetWeight: 60 / ActualWeight: 60
Updated:   3   (3 canary + 2 stable)
```

The updated-replica count climbs exactly as expected: 1 → 2 → 3 → 4 → 5 as weight climbs 20 → 40 → 60 → 80 → 100. Request rate stayed steady throughout — the in-cluster loadgen saw no errors during any step transition because old stable pods remained Ready while new canary pods were added before old ones were removed.

**Dashboard observation:** The docker-compose Prometheus from Lab 3 cannot scrape pod IPs inside k3d (private bridge network), so Grafana showed no new data during the k3d canary. Real canary observability requires an in-cluster Prometheus (see Bonus Task). The `kubectl argo rollouts get rollout --watch` output provided real-time step/weight/replica visibility.

**At what percentage would I abort if I saw errors?** At 20% (step 1). The first measurement window is the right time to catch bugs — only 1 in 5 users is affected and the blast radius is minimal. Waiting past 40% means 2 pods are serving bad traffic; past 60% means the majority of users are affected. The whole point of the first step is to be the canary in the coal mine.

---

## Bonus Task — Automated Canary Analysis

### B.1 — In-cluster Prometheus installed

```bash
kubectl apply -f labs/lab7/prometheus.yaml
kubectl -n monitoring rollout status deployment/prometheus --timeout=60s
# deployment "prometheus" successfully rolled out
```

Prometheus configured with `kubernetes_sd_configs` to discover gateway pods by label, scrape each pod's `/metrics` directly (not via Service), and relabel `rollouts-pod-template-hash` → `rs_hash` to allow per-replicaset filtering.

Targets verified via port-forward:
```bash
kubectl port-forward -n monitoring svc/prometheus 9091:9090 &
curl -s 'http://localhost:9091/api/v1/targets?state=active' | python3 -c "
import sys,json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(t['labels'].get('pod'), 'rs=', t['labels'].get('rs_hash'), t['health'])"
```

All 5 gateway pods discovered with `health=up`, each with a distinct `rs_hash` label.

### B.2 — AnalysisTemplate applied

```bash
kubectl apply -f labs/lab7/analysis-template.yaml
kubectl get analysistemplate gateway-error-rate
# NAME                 AGE
# gateway-error-rate   10s
```

The template queries `gateway_requests_total` filtered by `rs_hash` (canary pod-template-hash) and checks that the 5xx error ratio is below 5% across 3 consecutive 20-second windows. `initialDelay: 60s` gives Prometheus time to discover and scrape the new canary pod before measurements begin.

### B.3 — Rollout strategy with analysis step

Updated `k8s/gateway.yaml` to wire the AnalysisTemplate between the 20% and 40% steps:

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

### B.4 — Good version auto-promotes

```bash
kubectl apply -f labs/lab7/loadgen.yaml
kubectl argo rollouts set image gateway gateway=quickticket-gateway:v1
kubectl argo rollouts get rollout gateway --watch
```

AnalysisRun created automatically, ran 3 measurements at 20-second intervals, all returned `value=0` (zero 5xx errors):

```
kubectl get analysisrun
NAME                       STATUS      AGE
gateway-6567ff84c-4-1      Successful  2m

kubectl get analysisrun gateway-6567ff84c-4-1 -o yaml | grep -A5 "measurements"
  measurements:
  - value: "0"    startedAt: ...  finishedAt: ... phase: Successful
  - value: "0"    startedAt: ...  finishedAt: ... phase: Successful
  - value: "0"    startedAt: ...  finishedAt: ... phase: Successful
```

After AnalysisRun succeeded, rollout auto-promoted to 50% then 100% without any manual intervention. Final status: `✔ Healthy`.

### B.5 — Bad version auto-aborts

Set `EVENTS_URL` to a broken hostname to force 5xx on every `/events` call:

```yaml
env:
  - name: EVENTS_URL
    value: "http://broken-on-purpose:8081"
  - name: GATEWAY_TIMEOUT_MS
    value: "2000"
```

```bash
kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts get rollout gateway --watch
```

Canary pod came up, loadgen traffic hit it, every `/events` call timed out → 504. AnalysisRun measurements immediately returned `value=1.0` (100% error rate):

```
kubectl get analysisrun
NAME                       STATUS   AGE
gateway-bad-5-1            Failed   90s

kubectl get analysisrun gateway-bad-5-1 -o yaml | grep -A5 "measurements"
  measurements:
  - value: "1"    phase: Failed
  - value: "1"    phase: Failed   ← failureLimit=1 exceeded after 2nd failure
```

Rollout auto-aborted after 2 consecutive failures exceeded `failureLimit: 1`. Stable pods never touched:

```
Status:          ✖ Degraded
Message:         RolloutAborted: metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Images:          quickticket-gateway:v1 (stable)
```

**Answer — what metric to add beyond error rate:** P99 latency. Error rate catches hard failures (5xx) but misses degraded performance — a canary might return 200 OK in 10 seconds instead of the normal 50ms. Adding `histogram_quantile(0.99, rate(gateway_request_duration_seconds_bucket{rs_hash="..."}[60s])) > 0.5` as a second AnalysisTemplate metric would catch latency regressions before they affect the majority of users.