# Lab 7 — Progressive Delivery: Canary Deployments

## Made by:
### Nurmuhametov Denis (d.nurmuhametov@innopolis.university)

---

## Task 1 — Manual Canary Deployment (6 pts)

### 7.1: Install Argo Rollouts

```bash
kubectl create namespace argo-rollouts
kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
kubectl wait --for=condition=Available deployment/argo-rollouts -n argo-rollouts --timeout=60s
```

```text
customresourcedefinition.apiextensions.k8s.io/analysisruns.argoproj.io created
customresourcedefinition.apiextensions.k8s.io/analysistemplates.argoproj.io created
customresourcedefinition.apiextensions.k8s.io/clusteranalysistemplates.argoproj.io created
customresourcedefinition.apiextensions.k8s.io/experiments.argoproj.io created
customresourcedefinition.apiextensions.k8s.io/rollouts.argoproj.io created
serviceaccount/argo-rollouts created
clusterrole.rbac.authorization.k8s.io/argo-rollouts created
clusterrole.rbac.authorization.k8s.io/argo-rollouts-aggregate-to-admin created
clusterrole.rbac.authorization.k8s.io/argo-rollouts-aggregate-to-edit created
clusterrole.rbac.authorization.k8s.io/argo-rollouts-aggregate-to-view created
clusterrolebinding.rbac.authorization.k8s.io/argo-rollouts created
configmap/argo-rollouts-config created
secret/argo-rollouts-notification-secret created
service/argo-rollouts-metrics created
deployment.apps/argo-rollouts created
```

Install kubectl plugin:

```bash
# Option A: system-wide (requires sudo)
curl -LO https://github.com/argoproj/argo-rollouts/releases/latest/download/kubectl-argo-rollouts-linux-amd64
chmod +x kubectl-argo-rollouts-linux-amd64
sudo mv kubectl-argo-rollouts-linux-amd64 /usr/local/bin/kubectl-argo-rollouts
```

```bash
kubectl argo rollouts version
```

```text
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:08:11Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: linux/amd64
```

### 7.2: Convert gateway Deployment to Rollout

Edited `k8s/gateway.yaml` — changed `kind: Deployment` to `kind: Rollout`, set `apiVersion: argoproj.io/v1alpha1`, and added canary strategy:

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
  selector:
    matchLabels:
      app: gateway
  template:
```

Applied:

```bash
kubectl delete deployment gateway
kubectl apply -f k8s/gateway.yaml
```

```bash
kubectl argo rollouts get rollout gateway
```

```text
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/mmagia/quickticket-gateway:f1afc7d9d092f319fe06e9b24c40a7696a2c136e (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5
```

### 7.3: Deploy a new version (canary)

Updated `k8s/gateway.yaml` with env var and triggered canary:

```bash
kubectl apply -f k8s/gateway.yaml
```

```bash
kubectl argo rollouts get rollout gateway --watch
```

```text
NAME                                 KIND        STATUS     AGE   INFO
⟳ gateway                            Rollout     ॥ Paused   3m8s
├──# revision:2
│  └──⧉ gateway-55b956d8d            ReplicaSet  ✔ Healthy  113s  canary
│     └──□ gateway-55b956d8d-9vr59   Pod         ✔ Running  111s  ready:1/1
└──# revision:1
   └──⧉ gateway-5df777d848           ReplicaSet  ✔ Healthy  3m8s  stable
      ├──□ gateway-5df777d848-592c6  Pod         ✔ Running  3m8s  ready:1/1
      ├──□ gateway-5df777d848-ch8tg  Pod         ✔ Running  3m8s  ready:1/1
      ├──□ gateway-5df777d848-qgc4w  Pod         ✔ Running  3m8s  ready:1/1
      └──□ gateway-5df777d848-ttcfv  Pod         ✔ Running  3m8s  ready:1/1
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/mmagia/quickticket-gateway:f1afc7d9d092f319fe06e9b24c40a7696a2c136e (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5
```

Canary pod was created — 1 canary (v2) + 4 stable (v3), paused at 20%.

### 7.4: Verify traffic split

Applied in-cluster loadgen and counted requests per pod:

```bash
kubectl apply -f labs/lab7/loadgen.yaml
sleep 30
for pod in $(kubectl get pods -l app=gateway -o name); do
  count=$(kubectl logs $pod 2>/dev/null | grep -c 'GET /events')
  img=$(kubectl get $pod -o jsonpath='{.spec.containers[0].image}')
  echo "$pod image=$img events_requests=$count"
done
kubectl delete -f labs/lab7/loadgen.yaml
```

```text
pod/gateway-55b956d8d-9vr59 image=ghcr.io/mmagia/quickticket-gateway:f1afc7d9d092f319fe06e9b24c40a7696a2c136e events_requests=23
pod/gateway-5df777d848-592c6 image=ghcr.io/mmagia/quickticket-gateway:f1afc7d9d092f319fe06e9b24c40a7696a2c136e events_requests=22
pod/gateway-5df777d848-ch8tg image=ghcr.io/mmagia/quickticket-gateway:f1afc7d9d092f319fe06e9b24c40a7696a2c136e events_requests=22
pod/gateway-5df777d848-qgc4w image=ghcr.io/mmagia/quickticket-gateway:f1afc7d9d092f319fe06e9b24c40a7696a2c136e events_requests=18
pod/gateway-5df777d848-ttcfv image=ghcr.io/mmagia/quickticket-gateway:f1afc7d9d092f319fe06e9b24c40a7696a2c136e events_requests=24
```

Traffic was distributed across pods proportional to canary weight. At 20% canary weight, roughly 1-in-5 requests hit the canary pod.

### 7.5: Promote the canary

```bash
kubectl argo rollouts promote gateway
```

```text
rollout 'gateway' promoted
```

```bash
kubectl argo rollouts get rollout gateway --watch
```
In the next step the canary moved to 60
```text
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          3/5
  SetWeight:     60
  ActualWeight:  60
Images:          ghcr.io/mmagia/quickticket-gateway:f1afc7d9d092f319fe06e9b24c40a7696a2c136e (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         5
  Available:     5
```

Finally, after the 30s pause, it auto-promoted to 100%
```text
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/mmagia/quickticket-gateway:f1afc7d9d092f319fe06e9b24c40a7696a2c136e (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5
```

All 5 pods now running the updated version. Rollout complete.

### 7.6: Deploy a "bad" version and abort

Changed `APP_VERSION` to "v2-bad" to simulate a broken version:

```bash
kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts get rollout gateway --watch
```

```text
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/mmagia/quickticket-gateway:f1afc7d9d092f319fe06e9b24c40a7696a2c136e (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5
```

Aborted the canary and instantly rollback:

```bash
kubectl argo rollouts abort gateway
```


```bash
kubectl argo rollouts get rollout gateway
```


```text
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 2
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/mmagia/quickticket-gateway:f1afc7d9d092f319fe06e9b24c40a7696a2c136e (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5
```

Canary pod was killed instantly. Stable pods continued serving traffic. **Instant rollback** — no CI/CD pipeline needed.


**How long from `abort` to all traffic serving the stable version?**

- **Argo Rollouts abort**: < 5 seconds. The canary pod is terminated immediately, and all traffic routes to stable pods via the Service selector.
- **`git revert` rollback (Lab 5)**: ~5 minutes. Requires CI to rebuild, ArgoCD to detect drift, sync manifests, and recreate pods.

Argo Rollouts provides **instant rollback** because it manages two ReplicaSets (stable + canary) simultaneously. The abort simply scales down the canary ReplicaSet. With `git revert`, the entire pipeline must run end-to-end.

---

## Task 2 — Multi-Step Canary with Observation (4 pts)

### 7.8: Design the strategy

Updated `k8s/gateway.yaml` with a more granular canary:

```yaml
strategy:
  canary:
    steps:
      - setWeight: 20
      - pause: { duration: 60s }
      - setWeight: 40
      - pause: { duration: 60s }
      - setWeight: 60
      - pause: { duration: 60s }
      - setWeight: 80
      - pause: { duration: 30s }
      - setWeight: 100
```

Applied continuous loadgen and triggered the rollout:

```bash
kubectl apply -f labs/lab7/loadgen.yaml
kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts get rollout gateway --watch
```

### 7.9: Observe the rollout

Observed the rollout progressing through 5 weight steps (9 total steps including pauses):

```text
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/9
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/mmagia/quickticket-gateway:f1afc7d9d092f319fe06e9b24c40a7696a2c136e (stable)
                 quickticket-gateway:v2 (canary)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5
```

```text
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          4/9
  SetWeight:     60
  ActualWeight:  50
Images:          ghcr.io/mmagia/quickticket-gateway:f1afc7d9d092f319fe06e9b24c40a7696a2c136e (stable)
                 quickticket-gateway:v2 (canary)
Replicas:
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         4
  Available:     4
```

```text
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          5/9
  SetWeight:     60
  ActualWeight:  60
Images:          ghcr.io/mmagia/quickticket-gateway:f1afc7d9d092f319fe06e9b24c40a7696a2c136e (stable)
                 quickticket-gateway:v2 (canary)
Replicas:
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         5
  Available:     5
```
### Analysis: "Does request rate stay steady across canary steps?"

**Yes.** The overall request rate stays steady across canary steps. The loadgen sends requests at a fixed rate (every 0.2s), so the total number of requests per unit time does not change with canary weight. What changes is the **distribution** of traffic across pods — from ~20% at step 1 to ~80% at step 7.

### "At what canary percentage would you want an automated abort? Why?"

**20-40%.** Early canary steps expose only 1-2 pods to new traffic, making it cheap to detect issues and roll back. By 60%+, a significant portion of users (3+ out of 5 pods) would be affected by a bad version. The sweet spot is after the first pause at 20%: enough traffic to measure error rate, but minimal blast radius if something goes wrong.

---

## Bonus Task — Automated Canary Analysis (2 pts)

### B.1: Install in-cluster Prometheus

```bash
kubectl apply -f labs/lab7/prometheus.yaml
kubectl -n monitoring rollout status deployment/prometheus --timeout=60s
```

```text
deployment "prometheus" successfully rolled out
```

Verified gateway pods are discovered:

```bash
kubectl port-forward -n monitoring svc/prometheus 9091:9090 &
curl -s 'http://localhost:9091/api/v1/targets?state=active' | python3 -c "
import sys,json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(t['labels'].get('pod'), 'rs=', t['labels'].get('rs_hash'), t['health'])"
kill %1 2>/dev/null
```

```text
gateway-95b85fcd9-b2jqm rs= 95b85fcd9 up
gateway-95b85fcd9-5h5jl rs= 95b85fcd9 up
gateway-95b85fcd9-t92bt rs= 95b85fcd9 up
gateway-95b85fcd9-gg6d4 rs= 95b85fcd9 up
gateway-95b85fcd9-6flsn rs= 95b85fcd9 up
```

All 5 gateway pods discovered with `rs_hash` label — Argo Rollouts can distinguish canary from stable.

### B.2: Install the AnalysisTemplate

```bash
kubectl apply -f labs/lab7/analysis-template.yaml
kubectl get analysistemplate gateway-error-rate
```

```text
NAME                 AGE
gateway-error-rate   3s
```

The AnalysisTemplate queries Prometheus for the canary's 5xx error rate:

```promql
(
  sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}",status=~"5.."}[60s]))
  or on() vector(0)
)
/
sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}"}[60s]))
```

Key design choices:
- `or on() vector(0)` on numerator — zero 5xx is a real answer, not an error
- Denominator is strict — no traffic = can't measure = fail-safe abort
- `initialDelay: 60s` — gives Prometheus time to discover and scrape new canary pod

### B.3: Wire analysis into the Rollout strategy

Updated `k8s/gateway.yaml` with analysis step:

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

### B.4: Test — good version auto-promotes

Built and loaded the local `gateway:v3` image, applied the Rollout with analysis step, and watched it auto-promote:

```bash
docker build -t gateway:v3 ./app/gateway
k3d image import -c quickticket gateway:v3
kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts get rollout gateway --watch
```

Canary created at 20%, AnalysisRun started measuring error rate:
```text
Name:            gateway
Status:          ॥ Paused          Step: 1/6  SetWeight: 20
Images:          gateway:v3 (canary) / ghcr.io/...f1afc7d... (stable)

NAME                                 KIND         STATUS         AGE  INFO
⟳ gateway                            Rollout      ◌ Progressing  17m
├──# revision:2
│  ├──⧉ gateway-bd74659b7            ReplicaSet   ✔ Healthy      35s  canary
│  │  └──□ gateway-bd74659b7-sxl79   Pod          ✔ Running      34s  ready:1/1
│  └──α gateway-bd74659b7-2-2        AnalysisRun  ◌ Running      5s
└──# revision:1
   └──⧉ gateway-8669b68b94           ReplicaSet   ✔ Healthy      17m  stable (4 pods)
```

AnalysisRun completed — all 3 measurements showed zero errors → auto-promoted:
```text
NAME                                 KIND         STATUS         AGE    INFO
⟳ gateway                            Rollout      ◌ Progressing  19m
├──# revision:2
│  ├──⧉ gateway-bd74659b7            ReplicaSet   ◌ Progressing  2m15s  canary (3 pods → 5)
│  └──α gateway-bd74659b7-2-2        AnalysisRun  ✔ Successful   105s   ✔ 3
└──# revision:1
   └──⧉ gateway-8669b68b94           ReplicaSet   ✔ Healthy      19m    stable
```

```bash
kubectl get analysisrun gateway-bd74659b7-2-2 -o yaml
```

```yaml
phase: Successful
successful: 3
measurements:
  - value: '[0]'   ✅ Successful  (19:52:24Z)
  - value: '[0]'   ✅ Successful  (19:52:44Z)
  - value: '[0]'   ✅ Successful  (19:53:04Z)
```

```bash
kubectl get analysisrun
```

```text
NAME                    STATUS       AGE
gateway-bd74659b7-2-2   Successful   5m
```

Final state — all 5 pods on `gateway:v3`:
```bash
kubectl argo rollouts get rollout gateway
```

```text
Name:            gateway
Status:          ✔ Healthy
  Step:          6/6   SetWeight: 100   ActualWeight: 100
Images:          gateway:v3 (stable)

NAME                                KIND         STATUS        AGE    INFO
⟳ gateway                           Rollout      ✔ Healthy     22m
├──# revision:2
│  ├──⧉ gateway-bd74659b7           ReplicaSet   ✔ Healthy     4m58s  stable (5 pods)
│  └──α gateway-bd74659b7-2-2       AnalysisRun  ✔ Successful  4m28s  ✔ 3
└──# revision:1
   └──⧉ gateway-8669b68b94          ReplicaSet   • ScaledDown  22m
```

**No human intervention.** AnalysisRun measured 0% error rate → Successful → auto-promoted to 100% → Healthy.

### B.5: Test — bad version auto-aborts

To produce real 5xx on the canary, pointed `EVENTS_URL` to a broken address:

```yaml
env:
  - name: EVENTS_URL
    value: "http://events:9999"         # non-existent port → connection refused
  - name: GATEWAY_TIMEOUT_MS
    value: "2000"
```

**Challenge encountered:** The gateway's `/health` endpoint checks downstream services and returns 503 when `EVENTS_URL` is broken. This prevented the canary pod from becoming Ready (readiness probe failed), which blocked Argo Rollouts from creating an AnalysisRun.

**Workaround:** Temporarily disabled the readinessProbe (changed livenessProbe to `tcpSocket`) to allow the canary pod to become Ready and demonstrate auto-abort.

```bash
kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts get rollout gateway --watch
```

Canary created at 20%, AnalysisRun started:
```text
NAME                                 KIND         STATUS         AGE  INFO
⟳ gateway                            Rollout      ◌ Progressing  62m
├──# revision:3
│  ├──⧉ gateway-77f8b8f9f8           ReplicaSet   ✔ Healthy      24s  canary
│  │  └──□ gateway-77f8b8f9f8-xgpj2  Pod          ✔ Running      23s  ready:1/1
│  └──α gateway-77f8b8f9f8-3-2       AnalysisRun  ◌ Running      1s
├──# revision:2
│  ├──⧉ gateway-bd74659b7            ReplicaSet   ✔ Healthy      45m  stable (4 pods)
│  └──α gateway-bd74659b7-2-2        AnalysisRun  ✔ Successful   44m  ✔ 3
```

AnalysisRun result — 100% error rate on every measurement → auto-abort:

```bash
kubectl get analysisrun gateway-77f8b8f9f8-3-2 -o yaml
```

```yaml
phase: Failed
message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
measurements:
  - value: '[1]'   ✖ Failed  (20:37:03Z)   ← 100% error rate
  - value: '[1]'   ✖ Failed  (20:37:23Z)   ← 100% error rate
failed: 2
failureLimit: 1
```

```bash
kubectl get analysisrun
```

```text
NAME                     STATUS       AGE
gateway-77f8b8f9f8-3-2   Failed        
gateway-bd74659b7-2-2    Successful    
```

State after abort:

```bash
kubectl argo rollouts get rollout gateway
```

```text
Name:            gateway
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3:
                 Step-based analysis phase error/failed:
                 Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  Step:          0/6   SetWeight: 0   ActualWeight: 0
Images:          gateway:v3 (stable)

NAME                                KIND         STATUS        AGE    INFO
⟳ gateway                           Rollout      ✖ Degraded    65m
├──# revision:3
│  ├──⧉ gateway-77f8b8f9f8          ReplicaSet   • ScaledDown  3m41s  canary
│  └──α gateway-77f8b8f9f8-3-2      AnalysisRun  ✖ Failed      3m18s  ✖ 2
├──# revision:2
│  ├──⧉ gateway-bd74659b7           ReplicaSet   ✔ Healthy     48m    stable (5 pods)
│  └──α gateway-bd74659b7-2-2       AnalysisRun  ✔ Successful  47m    ✔ 3
```

The failed canary was scaled down to 0, while the stable ReplicaSet (revision 2) continued serving traffic. **Instant rollback.**

### "What metric would you add beyond error rate for a more complete canary analysis?"

**Latency (p99 request duration).** A canary might return 200 OK but with significantly increased latency (e.g., 5s instead of 200ms). The current AnalysisTemplate only measures error rate — a slow but non-failing version would pass analysis and auto-promote, degrading user experience. Adding a latency metric (e.g., `histogram_quantile(0.99, gateway_request_duration_seconds) < 1.0`) would catch performance regressions alongside error rate.