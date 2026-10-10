# Lab 7 — Progressive Delivery: Canary Deployments

## Task 1 — Manual Canary Deployment (6 pts)

### 7.1 — Install Argo Rollouts

```
$ kubectl create namespace argo-rollouts
namespace/argo-rollouts created

$ kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
customresourcedefinition.apiextensions.k8s.io/rollouts.argoproj.io created
deployment.apps/argo-rollouts created
...

$ kubectl wait --for=condition=Available deployment/argo-rollouts -n argo-rollouts --timeout=60s
deployment.apps/argo-rollouts condition met

$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:08:11Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: linux/amd64
```

Plugin installed to `~/.local/bin/kubectl-argo-rollouts` (Option B — no sudo).

### 7.2 — Convert gateway Deployment to Rollout

Changed `k8s/gateway.yaml`: `kind: Deployment` → `kind: Rollout`, `apiVersion: argoproj.io/v1alpha1`, added canary strategy with `replicas: 5`.

```
$ kubectl delete deployment gateway
deployment.apps "gateway" deleted

$ kubectl apply -f k8s/gateway.yaml
rollout.argoproj.io/gateway created
service/gateway configured

$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
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

Five stable pods running before triggering the first canary.

### 7.3 — Deploy a new version (canary)

Changed `APP_VERSION` from `v1` to `v2` and applied the manifest.

```
$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/abeb021/quickticket-gateway:latest (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS     AGE    INFO
⟳ gateway                            Rollout     ॥ Paused   2m29s
├──# revision:2
│  └──⧉ gateway-7ffb657d76           ReplicaSet  ✔ Healthy  2m19s  canary
│     └──□ gateway-7ffb657d76-g7bq4  Pod         ✔ Running  2m19s  ready:1/1
└──# revision:1
   └──⧉ gateway-7f68854db5           ReplicaSet  ✔ Healthy  2m29s  stable
      ├──□ gateway-7f68854db5-7xnxw  Pod         ✔ Running  2m28s  ready:1/1
      ├──□ gateway-7f68854db5-9ldsp  Pod         ✔ Running  2m28s  ready:1/1
      ├──□ gateway-7f68854db5-fxcqt  Pod         ✔ Running  2m28s  ready:1/1
      └──□ gateway-7f68854db5-kjz8g  Pod         ✔ Running  2m28s  ready:1/1
```

Paused at step 1 — 1 canary pod + 4 stable pods, `ActualWeight: 20`.

### 7.4 — Verify traffic split

Used in-cluster loadgen (not port-forward — port-forward sticks to one pod and hides the real kube-proxy split).

```
$ kubectl apply -f labs/lab7/loadgen.yaml
deployment.apps/loadgen created

$ sleep 30
$ for pod in $(kubectl get pods -l app=gateway -o name); do
    count=$(kubectl logs $pod 2>/dev/null | grep -c 'GET /events')
    img=$(kubectl get $pod -o jsonpath='{.spec.containers[0].image}')
    echo "$pod image=$img events_requests=$count"
  done
pod/gateway-7f68854db5-7xnxw image=ghcr.io/abeb021/quickticket-gateway:latest events_requests=15
pod/gateway-7f68854db5-9ldsp image=ghcr.io/abeb021/quickticket-gateway:latest events_requests=21
pod/gateway-7f68854db5-fxcqt image=ghcr.io/abeb021/quickticket-gateway:latest events_requests=10
pod/gateway-7f68854db5-kjz8g image=ghcr.io/abeb021/quickticket-gateway:latest events_requests=27
pod/gateway-7ffb657d76-g7bq4 image=ghcr.io/abeb021/quickticket-gateway:latest events_requests=22

$ kubectl delete -f labs/lab7/loadgen.yaml
deployment.apps "loadgen" deleted
```

Canary pod received ~22 requests vs 10–27 on each stable pod — roughly 1-in-5, matching `setWeight: 20` with normal variance on a 30s sample.

### 7.5 — Promote the canary

```
$ kubectl argo rollouts promote gateway
rollout 'gateway' promoted

# After 30s auto-pause at 60%, rollout auto-promoted to 100%:
$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/abeb021/quickticket-gateway:latest (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5
```

Manual `promote` moved 20% → 60%; the timed pause at step 3 auto-proceeded to 100% without another manual command.

### 7.6 — Deploy a "bad" version and abort

Changed `APP_VERSION` to `v3-bad`, canary paused at 20%, then aborted:

```
$ kubectl argo rollouts abort gateway
rollout 'gateway' aborted

$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/abeb021/quickticket-gateway:latest (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         4
  Available:     4

NAME                                 KIND        STATUS         AGE    INFO
⟳ gateway                            Rollout     ✖ Degraded     6m22s
├──# revision:3
│  └──⧉ gateway-b86f5fd5c            ReplicaSet  • ScaledDown   2m16s  canary
│     └──□ gateway-b86f5fd5c-p8v7m   Pod         ◌ Terminating  2m16s  ready:0/1
├──# revision:2
│  └──⧉ gateway-7ffb657d76           ReplicaSet  ✔ Healthy      6m12s  stable
│     ├──□ gateway-7ffb657d76-g7bq4  Pod         ✔ Running      6m12s  ready:1/1
│     ├──□ gateway-7ffb657d76-l9tvs  Pod         ✔ Running      3m5s   ready:1/1
│     ├──□ gateway-7ffb657d76-r68xt  Pod         ✔ Running      3m5s   ready:1/1
│     └──□ gateway-7ffb657d76-ntd4s  Pod         ✔ Running      2m25s  ready:1/1
```

Canary pod terminated immediately; stable v2 pods kept serving.

### 7.7 — Abort vs git revert speed comparison

| Rollback method | Time to stable traffic | What happens |
|-----------------|------------------------|--------------|
| `kubectl argo rollouts abort` | **~2–3 seconds** | Canary ReplicaSet scaled down; stable pods never stopped; kube-proxy stops routing to canary immediately |
| `git revert` + ArgoCD (Lab 5) | **~2–5 minutes** | Commit revert → push → ArgoCD detects change → sync → terminate all pods → start new ones |

`abort` returned in ~0.13s; canary pod entered `Terminating` within seconds. Argo Rollouts abort is orders of magnitude faster because stable pods stay running and no GitOps cycle is needed — only the canary ReplicaSet is killed.

---

## Task 2 — Multi-Step Canary with Observation (4 pts)

### 7.8 — Multi-step canary strategy

Applied to the cluster for Task 2 observation:

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

### 7.9 — Rollout observation

```
$ kubectl apply -f labs/lab7/loadgen.yaml
$ kubectl argo rollouts set image gateway gateway=quickticket-gateway:v2
$ kubectl argo rollouts get rollout gateway --watch
```

Snapshots during the rollout:

| Step | SetWeight | Updated replicas | Status |
|------|-----------|------------------|--------|
| 1/9 | 20 | 1 | Paused |
| 2/9 | 40 | 2 | Progressing |
| 5/9 | 60 | 3 | Paused |
| 7/9 | 80 | 4 | Paused |
| 9/9 | 100 | 5 | Healthy |

```
# Step 1 — 20%
Status:          ॥ Paused
  Step:          1/9
  SetWeight:     20
  ActualWeight:  20
  Updated:       1

# Step 5 — 60%
Status:          ॥ Paused
  Step:          5/9
  SetWeight:     60
  ActualWeight:  60
  Updated:       3

# Step 9 — 100%
Status:          ✔ Healthy
  Step:          9/9
  SetWeight:     100
  Updated:       5
Images:          quickticket-gateway:v2 (stable)
```

**Dashboard observation:** docker-compose Grafana from Lab 3 cannot scrape k3d pod IPs (bridge network). Used `kubectl argo rollouts get rollout gateway --watch` instead. Request rate from loadgen stayed steady across steps — only the stable/canary pod mix changed. `Updated` count climbed 1 → 2 → 3 → 4 → 5 in sync with weight increases.

**At what canary percentage would you want an automated abort? Why?**

**20–40%.** At 20% with 5 replicas, one canary pod receives real production traffic — enough for Prometheus error-rate analysis. Aborting at 60%+ means more users hit a bad version before detection. The 60s pauses at each step give time to observe metrics before proceeding.

---

## Bonus Task — Automated Canary Analysis (2 pts)

### B.1 — In-cluster Prometheus

```
$ kubectl apply -f labs/lab7/prometheus.yaml
namespace/monitoring created
deployment.apps/prometheus created

$ kubectl -n monitoring rollout status deployment/prometheus --timeout=60s
deployment "prometheus" successfully rolled out

$ kubectl apply -f labs/lab7/analysis-template.yaml
analysistemplate.argoproj.io/gateway-error-rate created

$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   47m
```

All 5 gateway pods discovered by Prometheus with `rs_hash` label (from `rollouts-pod-template-hash` relabel rule).

### B.2 — Analysis wired into Rollout

Final `k8s/gateway.yaml` strategy (also in fork):

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

```
$ kubectl apply -f labs/lab7/loadgen.yaml
$ kubectl argo rollouts set image gateway gateway=quickticket-gateway:v6-good

$ kubectl argo rollouts get rollout gateway
Status:          ✔ Healthy
  Step:          6/6
  SetWeight:     100
Images:          quickticket-gateway:v6-good (stable)

$ kubectl get analysisrun
NAME                      STATUS       AGE
gateway-5f766558fb-14-2   Successful   2m23s
```

Measurements — all zero errors, 3 consecutive windows:

```
value: '[0]'
value: '[0]'
value: '[0]'
```

AnalysisRun `Successful` → auto-promoted to 100%. No manual `promote` needed.

### B.5 — Bad version auto-aborts

Canary received traffic with elevated 5xx on `/events` (upstream returning errors). Analysis detected error rate above 5% threshold:

```
$ kubectl get analysisrun gateway-64bc697847-6-2 -o yaml
phase: Failed
measurements:
  value: '[0.4716981132075472]'
  value: '[0.48598130841121495]'
```

Error rate ~47% > 5% threshold → rollout auto-aborted:

```
$ kubectl argo rollouts get rollout gateway
Status:          ✖ Degraded
Message:         RolloutAborted: ... Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  Step:          0/6
  SetWeight:     0
  Updated:       0
```

Stable pods untouched; canary ReplicaSet scaled down.

> **Note on `EVENTS_URL=broken-on-purpose`:** the lab's broken-URL approach also makes `/health` return 503 (gateway checks `EVENTS_URL/health`), causing canary CrashLoopBackOff before analysis runs. Auto-abort was demonstrated via high `/events` error rate when upstream returned 502 — same AnalysisTemplate path, same abort behavior.

### B.6 — What metric would you add beyond error rate?

**p99 latency** from `gateway_request_duration_seconds`. Error rate catches hard 5xx failures; latency catches slow regressions (DB timeouts, upstream slowness) before they become errors. A canary that is "error-free but 10× slower" would pass error-rate analysis but fail an SLO — combining both gives fuller progressive-delivery confidence.
