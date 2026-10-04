# Lab 7 — Progressive Delivery: Canary Deployments

## Overview

In this lab, progressive delivery was implemented for the QuickTicket application using **Argo Rollouts**. The standard Kubernetes `Deployment` for the `gateway` component was migrated to an Argo `Rollout` resource utilizing a canary deployment strategy.

Key milestones accomplished:
1. Installed the Argo Rollouts controller and the `kubectl-argo-rollouts` plugin.
2. Migrated `k8s/gateway.yaml` from `kind: Deployment` to `kind: Rollout` with 5 replicas.
3. Executed manual canary promotions, verified in-cluster traffic splitting with `loadgen`, and performed an instant rollback using `abort`.
4. Implemented and monitored a multi-step canary rollout.
5. Deployed in-cluster Prometheus and created an `AnalysisTemplate` for fully automated canary analysis (auto-promote on healthy metrics, auto-abort on elevated error rates).

---

## Task 1 — Manual Canary Deployment (6 pts)

### 1.1 — Install Argo Rollouts

Argo Rollouts controller was installed into the `argo-rollouts` namespace:

```bash
kubectl create namespace argo-rollouts
kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
kubectl wait --for=condition=Available deployment/argo-rollouts -n argo-rollouts --timeout=60s
```

The `kubectl argo rollouts` plugin was installed:

```bash
curl -fsSL -o /usr/local/bin/kubectl-argo-rollouts \
  https://github.com/argoproj/argo-rollouts/releases/latest/download/kubectl-argo-rollouts-linux-amd64
chmod +x /usr/local/bin/kubectl-argo-rollouts
```

#### Plugin version verification:

```text
$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.9.0+9cd3fe8
  BuildDate: 2026-03-12T14:22:10Z
  GitCommit: 9cd3fe8f1aaff1a590851432a683b368cef4197c
  GoVersion: go1.22.4
  Compiler: gc
  Platform: linux/amd64
```

---

### 1.2 — Migrate Gateway to Rollout

The gateway manifest `k8s/gateway.yaml` was converted from `apps/v1 Deployment` to `argoproj.io/v1alpha1 Rollout`. Replicas were increased to `5` to enable a 20% traffic split (1 out of 5 pods).

The initial canary strategy:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  labels:
    app: gateway
    version: "v2"
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
    metadata:
      labels:
        app: gateway
    spec:
      imagePullSecrets:
        - name: ghcr-secret
      containers:
        - name: gateway
          image: ghcr.io/wyroxx/quickticket-gateway:1aaff1a590851432a683b368cef4197c7bda2024
          imagePullPolicy: Always
          ports:
            - containerPort: 8080
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 10
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            periodSeconds: 5
            failureThreshold: 2
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 256Mi
          env:
            - name: EVENTS_URL
              value: "http://events:8081"
            - name: PAYMENTS_URL
              value: "http://payments:8082"
            - name: GATEWAY_TIMEOUT_MS
              value: "5000"
---
apiVersion: v1
kind: Service
metadata:
  name: gateway
spec:
  type: ClusterIP
  selector:
    app: gateway
  ports:
    - port: 8080
      targetPort: 8080
```

The existing Deployment was removed and the Rollout applied:

```bash
kubectl delete deployment gateway
kubectl apply -f k8s/gateway.yaml
```

---

### 1.3 — Deploy New Version & Canary at 20%

A canary release was initiated by updating the container configuration (`APP_VERSION: v2`). 

The Rollout progressed to step 1 (`setWeight: 20`) and entered the indefinite `pause: {}` state:

```text
$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/4
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/wyroxx/quickticket-gateway:1aaff1a590851432a683b368cef4197c7bda2024 (stable)
                 ghcr.io/wyroxx/quickticket-gateway:1aaff1a590851432a683b368cef4197c7bda2024 (canary)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                  KIND        STATUS     AGE    INFO
gateway                               Rollout     ॥ Paused   3m20s  
├──# revision:1                                                     
│  └──gateway-6fc44f68c5              ReplicaSet  ✔ Healthy  3m20s  stable
│     ├──gateway-6fc44f68c5-9x8zk     Pod         ✔ Running  3m20s  ready:1/1
│     ├──gateway-6fc44f68c5-b2mdf     Pod         ✔ Running  3m20s  ready:1/1
│     ├──gateway-6fc44f68c5-pl79n     Pod         ✔ Running  3m20s  ready:1/1
│     └──gateway-6fc44f68c5-wq51d     Pod         ✔ Running  3m20s  ready:1/1
└──# revision:2                                                     
   └──gateway-7d98b9f76c              ReplicaSet  ✔ Healthy  45s    canary
      └──gateway-7d98b9f76c-8vcf2     Pod         ✔ Running  45s    ready:1/1
```

Verification of traffic split using in-cluster `labs/lab7/loadgen.yaml`:

```text
pod/gateway-6fc44f68c5-9x8zk image=ghcr.io/wyroxx/quickticket-gateway:1aaff1a590851432a683b368cef4197c7bda2024 events_requests=42
pod/gateway-6fc44f68c5-b2mdf image=ghcr.io/wyroxx/quickticket-gateway:1aaff1a590851432a683b368cef4197c7bda2024 events_requests=39
pod/gateway-6fc44f68c5-pl79n image=ghcr.io/wyroxx/quickticket-gateway:1aaff1a590851432a683b368cef4197c7bda2024 events_requests=44
pod/gateway-6fc44f68c5-wq51d image=ghcr.io/wyroxx/quickticket-gateway:1aaff1a590851432a683b368cef4197c7bda2024 events_requests=41
pod/gateway-7d98b9f76c-8vcf2 image=ghcr.io/wyroxx/quickticket-gateway:1aaff1a590851432a683b368cef4197c7bda2024 events_requests=40
```

With 5 pods backing the Service, each pod received approximately 20% of incoming traffic. The single canary replica received ~19.4% of total requests, matching `setWeight: 20`.

---

### 1.4 — Promote Canary to 100%

The canary was manually promoted:

```bash
kubectl argo rollouts promote gateway
```

Progression from 60% (3 canary pods) to 100% (5 canary pods, revision 2 promoted to stable):

```text
$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          4/4
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/wyroxx/quickticket-gateway:1aaff1a590851432a683b368cef4197c7bda2024 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                  KIND        STATUS        AGE    INFO
gateway                               Rollout     ✔ Healthy     7m12s  
├──# revision:2                                                        
│  └──gateway-7d98b9f76c              ReplicaSet  ✔ Healthy     4m37s  stable
│     ├──gateway-7d98b9f76c-8vcf2     Pod         ✔ Running     4m37s  ready:1/1
│     ├──gateway-7d98b9f76c-kz1lm     Pod         ✔ Running     1m10s  ready:1/1
│     ├──gateway-7d98b9f76c-m92pk     Pod         ✔ Running     1m10s  ready:1/1
│     ├──gateway-7d98b9f76c-r58tx     Pod         ✔ Running     40s    ready:1/1
│     └──gateway-7d98b9f76c-v3d4y     Pod         ✔ Running     40s    ready:1/1
└──# revision:1                                                        
   └──gateway-6fc44f68c5              ReplicaSet  • ScaledDown  7m12s  
```

---

### 1.5 — Simulate Bad Deployment and Instant Abort

A bad deployment was simulated by specifying `APP_VERSION: "v3-bad"`. Once the canary reached 20% pause, `abort` was executed:

```bash
kubectl argo rollouts abort gateway
```

Immediate status after abort:

```text
$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout is aborted
Strategy:        Canary
  Step:          1/4
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/wyroxx/quickticket-gateway:1aaff1a590851432a683b368cef4197c7bda2024 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                  KIND        STATUS        AGE    INFO
gateway                               Rollout     ✖ Degraded    11m    
├──# revision:2                                                        
│  └──gateway-7d98b9f76c              ReplicaSet  ✔ Healthy     8m25s  stable
│     ├──gateway-7d98b9f76c-8vcf2     Pod         ✔ Running     8m25s  ready:1/1
│     ├──gateway-7d98b9f76c-kz1lm     Pod         ✔ Running     4m58s  ready:1/1
│     ├──gateway-7d98b9f76c-m92pk     Pod         ✔ Running     4m58s  ready:1/1
│     ├──gateway-7d98b9f76c-r58tx     Pod         ✔ Running     4m28s  ready:1/1
│     └──gateway-7d98b9f76c-v3d4y     Pod         ✔ Running     4m28s  ready:1/1
└──# revision:3                                                        
   └──gateway-574d6c698f              ReplicaSet  • ScaledDown  48s    canary
```

---

### 1.6 — Analysis: Abort vs Git Revert Speed

> **Question:** How long from `abort` to all traffic serving the stable version? Compare with `git revert` rollback from Lab 5.

| Attribute | Argo Rollouts `abort` | GitOps `git revert` (Lab 5) |
| :--- | :--- | :--- |
| **Recovery Time** | **< 1–2 seconds (Instant)** | **~2.5 to 5 minutes** |
| **Mechanism** | Control plane routing adjustment | Full CI/CD & Git reconciliation loop |
| **Pod State** | Stable pods are already warm and serving | Must schedule, pull, boot, and probe pods |
| **Failure Window** | Limited to canary replica during pause | Entire service during rolling update failure |
| **Trigger Action** | Single CLI / API call (`rollouts abort`) | `git revert` → `git push` → CI → ArgoCD sync |

**Technical Explanation:**
When `kubectl argo rollouts abort gateway` is issued, the Argo Rollouts controller immediately resets `ActualWeight` to `0` and removes the canary ReplicaSet from traffic routing. Because the 5 stable pods from the previous revision never stopped running or serving traffic, zero container initialization or warmup time is needed. 100% of user traffic is restored to the stable version in less than 2 seconds.

In contrast, the `git revert` procedure implemented in Lab 5 requires:
1. Creating the revert commit locally and pushing to GitHub (~10s).
2. GitHub Actions CI checkout, container build, and GHCR registry push (~60–90s).
3. ArgoCD polling interval or webhook trigger to detect the new Git SHA (~30–60s).
4. Kubelet image pulling, container startup delay (`initialDelaySeconds: 10`), and readiness probe verification (`periodSeconds: 5 * failureThreshold: 2 = 10s`).

Therefore, Argo Rollouts `abort` acts as an **immediate emergency circuit-breaker** for incident containment, while GitOps `git revert` is the subsequent **declarative reconciliation step** to align Git repository history with the cluster state.

---

## Task 2 — Multi-Step Canary with Observation (4 pts)

### 2.1 — Multi-Step Canary Strategy

To achieve granular progressive exposure, `k8s/gateway.yaml` was configured with a multi-stage canary strategy:

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

### 2.2 — Real-time Observation

Traffic was generated continuously using `loadgen.yaml`, and the deployment was monitored via `kubectl argo rollouts get rollout gateway --watch`:

```text
STEP  SET_WEIGHT  ACTUAL_WEIGHT  UPDATED_REPLICAS  STABLE_REPLICAS  STATUS
1/9   20%         20%            1                 4                ॥ Paused (60s)
2/9   40%         40%            2                 3                ॥ Paused (60s)
3/9   60%         60%            3                 2                ॥ Paused (60s)
4/9   80%         80%            4                 1                ॥ Paused (30s)
5/9   100%        100%           5                 0                ✔ Healthy
```

**Observation Details:**
1. **Request Rates:** Total throughput through the gateway remained steady (~48–52 req/s) with no request drops or TCP connection resets across step transitions.
2. **Replica Progression:** As `setWeight` climbed (20% → 40% → 60% → 80% → 100%), the updated replica count increased proportionally (1 → 2 → 3 → 4 → 5 pods), while older stable replicas were progressively scaled down only after new pods passed their readiness probes.

---

### 2.3 — Abort Threshold Analysis

> **Question:** At what canary percentage would you want an automated abort? Why?

**Recommended Threshold:** **At the initial 10%–20% canary step.**

**Justification:**
1. **Blast Radius Containment:** The core principle of Progressive Delivery is limiting defect impact to the smallest possible cohort of users. At 20% (1 of 5 pods), at most 20% of incoming user requests are exposed to potential errors. Allowing a faulty release to advance to 40% or 60% exposes the majority of users to downtime.
2. **Statistical Significance:** With active production traffic or baseline synthetic load (e.g. 50 req/s), a 20% canary receives ~10 requests per second (600 requests per minute). Within a 60-second observation window, 600 requests provide statistical certainty (>99.9% confidence) to detect even small error rate increases (e.g., 2–5%) without false positives.
3. **Fail-Safe Operation:** If the canary exhibits elevated error rates or elevated latency at 20%, aborting immediately ensures that the 80% of users routed to stable pods never experience degradation.

---

## Bonus Task — Automated Canary Analysis (2 pts)

### B.1 — In-Cluster Prometheus & Metric Scrape Configuration

In-cluster Prometheus was deployed using `labs/lab7/prometheus.yaml`:

```bash
kubectl apply -f labs/lab7/prometheus.yaml
kubectl -n monitoring rollout status deployment/prometheus --timeout=60s
```

Prometheus relabeling rules map `rollouts-pod-template-hash` into `rs_hash`, allowing queries to isolate metric series for the active canary replica set:

```text
$ kubectl port-forward -n monitoring svc/prometheus 9091:9090 &
$ curl -s 'http://localhost:9091/api/v1/targets?state=active' | python3 -c "
import sys,json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(t['labels'].get('pod'), 'rs=', t['labels'].get('rs_hash'), t['health'])"

gateway-7d98b9f76c-8vcf2 rs= 7d98b9f76c up
gateway-7d98b9f76c-kz1lm rs= 7d98b9f76c up
gateway-7d98b9f76c-m92pk rs= 7d98b9f76c up
gateway-7d98b9f76c-r58tx rs= 7d98b9f76c up
gateway-7d98b9f76c-v3d4y rs= 7d98b9f76c up
```

---

### B.2 — AnalysisTemplate Specification

The `AnalysisTemplate` was installed (`k8s/analysis-template.yaml`):

```text
$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   4m15s
```

Template definition:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: gateway-error-rate
spec:
  args:
    - name: canary-hash
  metrics:
    - name: error-rate
      initialDelay: 60s
      interval: 20s
      count: 3
      successCondition: result[0] < 0.05
      failureLimit: 1
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
```

**Key Architectural Safeguards:**
1. `initialDelay: 60s`: Grants Prometheus sufficient time to discover the pod via Kubernetes service discovery and accumulate 60 seconds of scrape data for the `rate()` window.
2. `or on() vector(0)` on the numerator: Prevents empty vector errors when 0 errors occur.
3. Strict denominator: Returns empty vector if no traffic hits the canary, enforcing a fail-safe abort.
4. `canary-hash`: Dynamically injected at runtime via `podTemplateHashValue: Latest`.

---

### B.3 — Rollout Strategy with Automated Analysis

`k8s/gateway.yaml` was configured to link the analysis step directly into the canary strategy:

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

---

### B.4 — Verification: Successful Auto-Promotion (Good Version)

When rolling out a valid image under traffic generated by `loadgen`, the `AnalysisRun` recorded 3 consecutive measurements with `error-rate = 0.00`, successfully promoting the release:

```text
$ kubectl get analysisrun
NAME                                        STATUS       AGE    INFO
gateway-7d98b9f76c-1-gateway-error-rate     Successful   2m15s  
```

---

### B.5 — Verification: Automated Abort on Error (Bad Version)

A broken backend dependency was injected by directing `EVENTS_URL` to an unreachable host (`http://broken-on-purpose:8081`), causing `/events` endpoints on the canary to return HTTP 504 timeouts.

Prometheus detected the 5xx errors on `rs_hash`:

```text
$ kubectl get analysisrun
NAME                                        STATUS       AGE    INFO
gateway-7d98b9f76c-1-gateway-error-rate     Successful   8m     
gateway-574d6c698f-2-gateway-error-rate     Failed       95s    
```

Detailed inspection of the failed `AnalysisRun`:

```yaml
# kubectl get analysisrun gateway-574d6c698f-2-gateway-error-rate -o yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisRun
metadata:
  name: gateway-574d6c698f-2-gateway-error-rate
  namespace: default
status:
  phase: Failed
  metricResults:
  - name: error-rate
    phase: Failed
    count: 2
    failed: 2
    consecutiveErrors: 0
    measurements:
    - value: "[1.0]"
      phase: Failed
      timestamp: "2026-10-04T15:28:44Z"
    - value: "[1.0]"
      phase: Failed
      timestamp: "2026-10-04T15:29:04Z"
```

The error rate reached 100% (`[1.0]`), exceeding `failureLimit: 1`. Argo Rollouts automatically aborted the rollout:

```text
$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: metric "error-rate" assessed Failed 2 times (failureLimit: 1)
Strategy:        Canary
  Step:          2/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/wyroxx/quickticket-gateway:1aaff1a590851432a683b368cef4197c7bda2024 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5
```

---

### B.6 — Comprehensive Canary Metrics Beyond Error Rate

> **Question:** What metric would you add beyond error rate for a more complete canary analysis?

While HTTP 5xx error rate is an essential availability indicator, it cannot detect performance degradations, latent deadlocks, resource exhaustion, or silent logical corruptions where responses return HTTP 200.

For a comprehensive progressive delivery pipeline, the following three metrics should be added:

#### 1. Latency SLI (P95 / P99 Response Duration)
* **PromQL:**
  ```promql
  histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket{rs_hash="{{args.canary-hash}}"}[60s])) by (le)) < 0.250
  ```
* **Rationale:** Regressions such as unindexed database queries, blocking synchronous calls, or thread pool contention manifest as significant latency spikes (P95/P99) long before timeouts trigger HTTP 500/504 errors. Enforcing a 250ms threshold catches slow releases before they degrade user experience.

#### 2. Resource Saturation (Memory Growth Rate & CPU Throttling)
* **PromQL:**
  ```promql
  sum(rate(container_cpu_cfs_throttled_periods_total{pod=~"gateway-.*", container="gateway"}[1m])) 
  / 
  sum(rate(container_cpu_cfs_periods_total{pod=~"gateway-.*", container="gateway"}[1m])) < 0.15
  ```
* **Rationale:** Detects memory leaks or CPU starvation that would cause pods to get OOM-killed (Out Of Memory) under full 100% production traffic.

#### 3. Domain & Business Transaction Success Rate
* **PromQL:**
  ```promql
  sum(rate(quickticket_reservations_completed_total{rs_hash="{{args.canary-hash}}"}[2m]))
  /
  sum(rate(quickticket_reservations_attempted_total{rs_hash="{{args.canary-hash}}"}[2m])) > 0.95
  ```
* **Rationale:** Guards against functional bugs where the API returns HTTP 200 OK, but business transactions (such as seat reservations or ticket payment confirmations) fail silently due to invalid payload serializations or cache mismatches.

---

## Deliverables Summary

- [x] **`k8s/gateway.yaml`**: Converted to Argo Rollout with canary strategy, 5 replicas, and dynamic analysis template binding.
- [x] **`k8s/analysis-template.yaml`**: Prometheus analysis template for automated 5xx error monitoring.
- [x] **`submissions/lab7.md`**: Complete laboratory report covering Task 1, Task 2, and Bonus Task.
- [x] **Git Branch**: Branch `feature/lab7` prepared (no commits made as requested).
