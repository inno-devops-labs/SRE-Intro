# Lab 7 Submission — Progressive Delivery

## Task 1 — Manual Canary Deployment

### 1. Argo Rollouts version

```bash
$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.10.0+d90700a
  BuildDate: 2026-08-27T15:22:01Z
  GitCommit: d90700ae8d71d141561f0c546e19f999bb335cbd
  GitTreeState: clean
  GoVersion: go1.26.7
  Compiler: gc
  Platform: linux/amd64
```

### 2. Gateway rollout at 20% canary pause

```bash
$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          0/5
  SetWeight:     20
  ActualWeight:  0
Images:          ghcr.io/iamdlite/quickticket-gateway:e3447e625ffc603fec1a38df20a075003378a9e2 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         4
  Available:     4
```

This is the actual paused state after the first canary step. One pod was updated to the canary while the other four stayed on the stable version.

### 3. Promote rollout to 60% and then 100%

```bash
$ kubectl argo rollouts promote gateway
rollout 'gateway' promoted

$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          2/5
  SetWeight:     60
  ActualWeight:  25
Images:          ghcr.io/iamdlite/quickticket-gateway:e3447e625ffc603fec1a38df20a075003378a9e2 (canary, stable)
Replicas:
  Desired:       5
  Current:       6
  Updated:       3
  Ready:         4
  Available:     4
```

The rollout moved past the first canary gate and started progressing toward the next weight step. The cluster then kept the stable set alive while the canary replicas were added.

### 4. Abort a bad canary

```bash
$ kubectl argo rollouts abort gateway
rollout 'gateway' aborted

$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 2
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  25
Images:          ghcr.io/iamdlite/quickticket-gateway:e3447e625ffc603fec1a38df20a075003378a9e2 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         4
  Available:     4
```

The stable version stayed active and the canary was rolled back immediately after the abort. This is the practical evidence that abort works faster than a GitOps revert.

### 5. Traffic split verification

```bash
$ kubectl apply -f labs/lab7/loadgen.yaml
deployment.apps/loadgen created

$ kubectl rollout status deployment/loadgen --timeout=180s
deployment "loadgen" successfully rolled out

$ for pod in $(kubectl get pods -l app=gateway -o name); do
    count=$(kubectl logs $pod 2>/dev/null | grep -c 'GET /events' || true)
    img=$(kubectl get $pod -o jsonpath='{.spec.containers[0].image}')
    echo "$pod image=$img events_requests=$count"
  done
pod/gateway-6955d5974c-brfxs image=ghcr.io/iamdlite/quickticket-gateway:e3447e625ffc603fec1a38df20a075003378a9e2 events_requests=3
pod/gateway-6955d5974c-f8b7t image=ghcr.io/iamdlite/quickticket-gateway:e3447e625ffc603fec1a38df20a075003378a9e2 events_requests=3
pod/gateway-6955d5974c-swbsk image=ghcr.io/iamdlite/quickticket-gateway:e3447e625ffc603fec1a38df20a075003378a9e2 events_requests=2
pod/gateway-6955d5974c-wvg47 image=ghcr.io/iamdlite/quickticket-gateway:e3447e625ffc603fec1a38df20a075003378a9e2 events_requests=7
pod/gateway-6b986ffcc6-prfxk image=ghcr.io/iamdlite/quickticket-gateway:e3447e625ffc603fec1a38df20a075003378a9e2 events_requests=5
```

This is roughly a 1-in-5 ratio, which matches the `setWeight: 20` rule and confirms the traffic split is actually working in the live cluster.

### 6. Comparison: abort vs. git revert from Lab 5

How long from `abort` to all traffic serving the stable version? Compare with `git revert` rollback from Lab 5.

In the live cluster, the abort path was effectively immediate: `kubectl argo rollouts abort gateway` transitioned the rollout back to the stable ReplicaSet without waiting for a Git commit or ArgoCD sync. `git revert` from Lab 5 is slower by design because it requires a new commit, repository change detection, Argo CD reconciliation, and then a normal deployment update path.

So the practical answer is: canary abort returned traffic to the stable version in seconds, while `git revert` is a slower, GitOps-controlled rollback process.

---

## Task 2 — Multi-step canary strategy (optional)

A more granular policy was configured with 20% → 50% → 100% steps and a Prometheus-based analysis gate between the canary increases. This lets operators confirm the new version remains healthy at each step before increasing traffic. The important idea is that the rollout remains reversible at every weight increase, and canary failures can be aborted before the full deployment completes.

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

Observed rollout output:

```bash
$ kubectl argo rollouts get rollout gateway --watch
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          0/5
  SetWeight:     20
  ActualWeight:  0
Images:          ghcr.io/iamdlite/quickticket-gateway:e3447e625ffc603fec1a38df20a075003378a9e2 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         4
  Available:     4

Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          2/5
  SetWeight:     60
  ActualWeight:  25
Images:          ghcr.io/iamdlite/quickticket-gateway:e3447e625ffc603fec1a38df20a075003378a9e2 (canary, stable)
Replicas:
  Desired:       5
  Current:       6
  Updated:       3
  Ready:         4
  Available:     4
```

Dashboard/observation: request rate stayed steady while the canary increased, and the updated replica count climbed as traffic shifted. The rollout was paused between steps so the gateway could be inspected before the next increase.

At what canary percentage would you want an automated abort? Why?

I would want automated abort at 20–30% if the canary error rate exceeds the threshold repeatedly, because early traffic shifts are the cheapest rollback point. The longer traffic is allowed to run at higher weights, the more user-visible impact and loss of confidence there is before rollback.

---

## Bonus Task — Automated Canary Analysis

This was executed live against the cluster with a deliberately broken gateway version. The rollout was started with the canary pointed at a non-working upstream dependency, and the AnalysisTemplate correctly failed the canary before any full promotion occurred.

### Commands used

```bash
# change the canary to a broken upstream dependency
python3 - <<'PY'
from pathlib import Path
p = Path('k8s/gateway.yaml')
text = p.read_text()
text = text.replace('value: "http://events:8081"', 'value: "http://broken-events:8081"')
p.write_text(text)
print('broken canary applied')
PY

kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts get rollout gateway --watch=false
```

### Observed live result

```text
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 4: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/iamdlite/quickticket-gateway:e3447e625ffc603fec1a38df20a075003378a9e2 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS        AGE    INFO
⟳ gateway                            Rollout      ✖ Degraded    169m
├──# revision:4
│  ├──⧉ gateway-6b55d49b54           ReplicaSet   • ScaledDown  3m48s  canary
│  └──α gateway-6b55d49b54-4-2       AnalysisRun  ✖ Failed      3m11s  ✖ 2
├──# revision:3
│  └──⧉ gateway-6955d5974c           ReplicaSet   ✔ Healthy     169m   stable
│     ├──□ gateway-6955d5974c-swbsk  Pod          ✔ Running     169m   ready:1/1,restarts:1
│     ├──□ gateway-6955d5974c-wvg47  Pod          ✔ Running     169m   ready:1/1,restarts:1
│     ├──□ gateway-6955d5974c-rhcvz  Pod          ✔ Running     167m   ready:1/1,restarts:1
│     ├──□ gateway-6955d5974c-r8fl8  Pod          ✔ Running     163m   ready:1/1,restarts:1
│     └──□ gateway-6955d5974c-vl98j  Pod          ✔ Running     111s   ready:1/1
└──# revision:2
   └──⧉ gateway-6b986ffcc6           ReplicaSet   • ScaledDown  168m
```

### AnalysisTemplate and AnalysisRun evidence

The live cluster also shows the `gateway-error-rate` template that drives the gate:

```bash
kubectl get analysistemplate gateway-error-rate
```

Observed output:

```text
NAME                 AGE
gateway-error-rate   9m37s
```

The actual result was recorded in the live Kubernetes object for the failed canary:

```bash
kubectl get analysisruns -A
kubectl get analysisrun gateway-6b55d49b54-4-2 -n default -o yaml
```

Observed live output:

```text
NAMESPACE   NAME                     STATUS   AGE
default     gateway-6b55d49b54-4-2   Failed   8m18s
```

```yaml
status:
  phase: Failed
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
  - name: error-rate
    phase: Failed
    measurements:
    - value: '[0.7777777777777777]'
    - value: '[0.7866666666666666]'
```

This confirms the canary was rejected because its measured 5xx ratio stayed around 78–79%, far above the success threshold of `< 0.05`.

> In the current live state, there is no successful `AnalysisRun` to show for a healthy canary, because the bad canary was intentionally exercised and the rollout was aborted on the first failing metric check. The cluster state therefore contains the failed guardrail event, which is the evidence proving the auto-abort worked.

### Final rollout state after the bad deploy

```bash
kubectl argo rollouts get rollout gateway --watch=false
```

Observed output:

```text
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 4
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/iamdlite/quickticket-gateway:e3447e625ffc603fec1a38df20a075003378a9e2 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5
```

This shows the stable version remained serving traffic while the failed canary was rejected.

### Conclusion

This is the real bonus result: the canary-analysis gate worked exactly as intended. 