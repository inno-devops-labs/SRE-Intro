# Lab 7 — Progressive Delivery: Canary Deployments

![difficulty](https://img.shields.io/badge/difficulty-intermediate-yellow)
![topic](https://img.shields.io/badge/topic-Canary%20Deployments-blue)
![points](https://img.shields.io/badge/points-10%2B2.5-orange)
![tech](https://img.shields.io/badge/tech-Argo%20Rollouts-informational)

> **Goal:** Install Argo Rollouts, convert the gateway Deployment to a canary Rollout, deploy good and bad versions, and experience manual promotion and abort.
> **Deliverable:** A PR from `feature/lab7` with updated `k8s/gateway.yaml` (Rollout) and `submissions/lab7.md`. Submit PR link via Moodle.

---

## Overview

In this lab you will practice:
- Installing Argo Rollouts on your k3d cluster
- Converting a Kubernetes Deployment to an Argo Rollout with canary strategy
- Executing a canary deployment with step-by-step promotion
- Deploying a "bad" version and aborting the canary
- Watching traffic splitting in real-time

> **You modify your existing gateway manifest.** The change is minimal: `kind: Deployment` → `kind: Rollout` + a `strategy` section.

---

## Project State

**You should have from previous labs:**
- k3d cluster with QuickTicket deployed (from Lab 4-5)
- ArgoCD managing deployments (from Lab 5)
- Monitoring stack with Grafana (from Lab 3)

**This lab adds:**
- Argo Rollouts controller for progressive delivery
- Gateway converted from Deployment to canary Rollout

---

## Task 1 — Manual Canary Deployment (6 pts)

**Objective:** Install Argo Rollouts, convert gateway to a canary Rollout, deploy a new version with canary, manually promote and abort.

### 7.1: Install Argo Rollouts

```bash
kubectl create namespace argo-rollouts
kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml

# Wait for controller
kubectl wait --for=condition=Available deployment/argo-rollouts -n argo-rollouts --timeout=60s
```

Install the kubectl plugin. If you don't want to use `sudo`, install to `~/.local/bin` instead:

```bash
# Option A: system-wide (requires sudo)
curl -LO https://github.com/argoproj/argo-rollouts/releases/latest/download/kubectl-argo-rollouts-linux-amd64
chmod +x kubectl-argo-rollouts-linux-amd64
sudo mv kubectl-argo-rollouts-linux-amd64 /usr/local/bin/kubectl-argo-rollouts

# Option B: per-user (no sudo)
mkdir -p ~/.local/bin
curl -fsSL -o ~/.local/bin/kubectl-argo-rollouts \
  https://github.com/argoproj/argo-rollouts/releases/latest/download/kubectl-argo-rollouts-linux-amd64
chmod +x ~/.local/bin/kubectl-argo-rollouts
echo 'export PATH=~/.local/bin:$PATH' >> ~/.bashrc   # or ~/.zshrc
export PATH=~/.local/bin:$PATH
```

Verify: `kubectl argo rollouts version` (expect `v1.9.0` or later).

### 7.2: Convert gateway Deployment to Rollout

Edit `k8s/gateway.yaml`. Two changes:

1. Change `kind: Deployment` to `kind: Rollout`
2. Add `apiVersion: argoproj.io/v1alpha1` (instead of `apps/v1`)
3. Add a `strategy` section under `spec`:

```yaml
apiVersion: argoproj.io/v1alpha1     # ← Changed
kind: Rollout                         # ← Changed
metadata:
  name: gateway
spec:
  replicas: 5                         # ← Need 5 for meaningful canary splits
  strategy:                           # ← NEW: canary strategy
    canary:
      steps:
        - setWeight: 20               # 1 of 5 pods = canary
        - pause: {}                   # Wait for manual promotion
        - setWeight: 60               # 3 of 5 pods = canary
        - pause: {duration: 30s}      # Auto-proceed after 30s
        - setWeight: 100              # Full rollout
  selector:
    matchLabels:
      app: gateway
  template:
    # ... rest stays the same as before
```

Delete the old Deployment and apply the new Rollout:

```bash
kubectl delete deployment gateway
kubectl apply -f k8s/gateway.yaml
```

Verify:
```bash
kubectl argo rollouts get rollout gateway
```

### 7.3: Deploy a new version (canary)

Your current gateway image is the "stable" version. To trigger a canary, change the image tag. You can add a simple environment variable to make versions distinguishable:

```bash
# Edit k8s/gateway.yaml — add or change an env var to simulate a new version:
# env:
#   - name: APP_VERSION
#     value: "v2"

kubectl apply -f k8s/gateway.yaml
```

Now watch the canary in real-time:

```bash
kubectl argo rollouts get rollout gateway --watch
```

You should see:
- Status: **Paused** at step 1 (setWeight: 20)
- 4 stable pods (old version) + 1 canary pod (new version)
- ActualWeight: 20

### 7.4: Verify traffic split

While the canary is paused at 20%, test that both versions serve traffic.

> ⚠️ **Gotcha:** `kubectl port-forward svc/gateway` does NOT load-balance. It picks ONE pod at the start of the session and sticks to it. You'll see 100% of your requests go to a single pod and think the canary isn't working. To see the real traffic split, run curl **from inside the cluster** so the request goes through `kube-proxy`:

```bash
# Run a one-shot curl pod inside the cluster (goes through Service → kube-proxy)
kubectl run curltest --image=curlimages/curl:latest --rm -i --restart=Never --command -- \
  sh -c 'for i in $(seq 1 50); do curl -s http://gateway:8080/events -o /dev/null; done; echo done'

# Count requests per pod (subtract any pre-existing count first)
for pod in $(kubectl get pods -l app=gateway -o name); do
  count=$(kubectl logs $pod 2>/dev/null | grep -c 'GET /events')
  img=$(kubectl get $pod -o jsonpath='{.spec.containers[0].image}')
  echo "$pod image=$img events_requests=$count"
done
```

You should see roughly 1-in-5 requests going to the canary pod (±variance for a 50-sample). That matches `setWeight: 20`.

### 7.5: Promote the canary

```bash
# Promote to next step (60%)
kubectl argo rollouts promote gateway

# Watch it progress
kubectl argo rollouts get rollout gateway --watch
# After 30s pause, it auto-promotes to 100%
```

Wait until status shows **Healthy** — full rollout complete.

### 7.6: Deploy a "bad" version and abort

Now simulate deploying a broken version. Change the image to something bad or add a failing env var:

```bash
# Edit k8s/gateway.yaml — change APP_VERSION to "v3-bad" or similar
kubectl apply -f k8s/gateway.yaml

# Watch canary start
kubectl argo rollouts get rollout gateway --watch
# Status: Paused at 20% — 1 canary pod running the "bad" version
```

Now abort:

```bash
kubectl argo rollouts abort gateway
```

Watch: canary pod is killed, stable pods stay. **Instant rollback.**

```bash
# Verify stable version is still serving
kubectl argo rollouts get rollout gateway
# Status: Degraded (aborted), but stable pods are serving traffic
```

### 7.7: Proof of work

**Updated `k8s/gateway.yaml`** (Rollout) goes in your fork.

**Paste into `submissions/lab7.md`:**
1. Output of `kubectl argo rollouts version`
2. Output of `kubectl argo rollouts get rollout gateway` showing Paused at 20% (during canary)
3. Output after `promote` — showing progression to 100%
4. Output after `abort` — showing instant rollback
5. Answer: "How long from `abort` to all traffic serving the stable version? Compare with `git revert` rollback from Lab 5."

<details>
<summary>💡 Hints</summary>

- If `kubectl argo rollouts get rollout gateway` shows "not found", make sure you applied the Rollout (not a Deployment) and the CRD is installed
- `replicas: 5` is needed for meaningful canary splits (20% = 1 pod). With `replicas: 1` you can only do 0% or 100%
- After `abort`, the rollout shows "Degraded". To retry: `kubectl argo rollouts retry rollout gateway`
- If using ArgoCD: it sees the Rollout CRD natively. You may need to sync after applying changes.
- The `pause: {}` (no duration) means **infinite pause** — it waits forever until you run `promote`

</details>

---

## Task 2 — Multi-Step Canary with Observation (4 pts)

> ⏭️ This task is optional. Skipping it will not affect future labs.

**Objective:** Design a multi-step canary strategy and observe each step on the Grafana dashboard.

### 7.8: Design your strategy

Update `k8s/gateway.yaml` with a more granular canary:

```yaml
strategy:
  canary:
    steps:
      - setWeight: 20
      - pause: {duration: 60s}    # Observe for 1 min
      - setWeight: 40
      - pause: {duration: 60s}
      - setWeight: 60
      - pause: {duration: 60s}
      - setWeight: 80
      - pause: {duration: 30s}
      - setWeight: 100
```

### 7.9: Observe on Grafana (or Argo Rollouts CLI)

> ⚠️ **Note:** The docker-compose Prometheus from Lab 3 **cannot scrape pods inside your k3d cluster** — they have private IPs in the k3d bridge network that the host can't reach. Two options:
> 1. **Easier:** use `kubectl argo rollouts get rollout gateway --watch` for real-time step + weight + replica observation. It's purpose-built for this.
> 2. **Harder (required for the bonus):** deploy a minimal in-cluster Prometheus. See Bonus Task prerequisite.

Generate traffic (in-cluster so kube-proxy load-balances):

```bash
kubectl create deployment loadgen --image=curlimages/curl:latest -- sh -c \
  'while true; do curl -s http://gateway:8080/events > /dev/null; sleep 0.2; done'
```

Trigger a rollout (change `APP_VERSION` or the image tag) and observe:

- Does request rate stay steady across canary steps?
- Does p99 latency change as canary percentage increases?
- At which step would you abort if you saw elevated errors?

**Paste into `submissions/lab7.md`:**
- Your multi-step canary strategy YAML
- Output of `kubectl argo rollouts get rollout gateway --watch` showing at least 3 steps
- Dashboard observation during the rollout
- Answer: "At what canary percentage would you want an automated abort? Why?"

---

## Bonus Task — Automated Canary Analysis (2.5 pts)

> 🌟 For those who want extra challenge and experience.

**Objective:** Create an AnalysisTemplate that queries Prometheus during canary, auto-promoting good versions and auto-aborting bad ones.

### B.0: Prerequisite — in-cluster Prometheus

You need a Prometheus that can scrape your gateway pods from **inside** the cluster (docker-compose Prometheus from Lab 3 can't — different network). Create a minimal one in a `monitoring` namespace with pod service discovery:

```yaml
# prometheus-minimal.yaml — apply with: kubectl apply -f prometheus-minimal.yaml
apiVersion: v1
kind: Namespace
metadata: { name: monitoring }
---
apiVersion: v1
kind: ServiceAccount
metadata: { name: prometheus, namespace: monitoring }
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: { name: prometheus }
rules:
  - apiGroups: [""]
    resources: [pods, services, endpoints]
    verbs: [get, list, watch]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata: { name: prometheus }
roleRef: { apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: prometheus }
subjects: [{ kind: ServiceAccount, name: prometheus, namespace: monitoring }]
---
apiVersion: v1
kind: ConfigMap
metadata: { name: prometheus-config, namespace: monitoring }
data:
  prometheus.yml: |
    global: { scrape_interval: 5s, evaluation_interval: 5s }
    scrape_configs:
      - job_name: gateway
        kubernetes_sd_configs: [{ role: pod, namespaces: { names: [default] } }]
        relabel_configs:
          - source_labels: [__meta_kubernetes_pod_label_app]
            regex: gateway
            action: keep
          - source_labels: [__meta_kubernetes_pod_ip]
            regex: (.+)
            target_label: __address__
            replacement: ${1}:8080
          - source_labels: [__meta_kubernetes_pod_name]
            target_label: pod
          # CRITICAL: copy the rollouts-pod-template-hash label so the
          # AnalysisTemplate can filter canary vs stable.
          - source_labels: [__meta_kubernetes_pod_label_rollouts_pod_template_hash]
            target_label: rs_hash
---
apiVersion: apps/v1
kind: Deployment
metadata: { name: prometheus, namespace: monitoring }
spec:
  replicas: 1
  selector: { matchLabels: { app: prometheus } }
  template:
    metadata: { labels: { app: prometheus } }
    spec:
      serviceAccountName: prometheus
      containers:
        - name: prometheus
          image: prom/prometheus:v3.11.2
          args:
            - --config.file=/etc/prometheus/prometheus.yml
            - --storage.tsdb.path=/prometheus
            - --web.listen-address=:9090
          ports: [{ containerPort: 9090 }]
          volumeMounts: [{ name: config, mountPath: /etc/prometheus }]
      volumes: [{ name: config, configMap: { name: prometheus-config } }]
---
apiVersion: v1
kind: Service
metadata: { name: prometheus, namespace: monitoring }
spec:
  selector: { app: prometheus }
  ports: [{ port: 9090, targetPort: 9090 }]
```

Verify Prometheus sees all gateway pods (including canary when one exists) with their `rs_hash` label:

```bash
kubectl port-forward -n monitoring svc/prometheus 9091:9090 &
curl -s 'http://localhost:9091/api/v1/targets?state=active' | python3 -c "
import sys,json;
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(t['labels'].get('pod'), 'rs=', t['labels'].get('rs_hash'), t['health'])"
```

### B.1: Create AnalysisTemplate

Create `k8s/analysis-template.yaml`:

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
      # initialDelay gives Prometheus time to discover + scrape the new canary
      # pod (K8s SD interval ≈ 10s, scrape_interval 5s, [60s] rate window below).
      # Without this, the first 2-3 measurements return empty → consecutiveErrors
      # > limit → false abort. This is non-obvious and costs students 30+ min.
      initialDelay: 60s
      interval: 20s
      count: 3
      successCondition: result[0] < 0.05
      failureLimit: 1
      provider:
        prometheus:
          address: http://prometheus.monitoring.svc.cluster.local:9090
          # IMPORTANT — handling "no matching series":
          #
          #   NUMERATOR uses `or on() vector(0)` because "zero 5xx responses"
          #   is a real answer. Without the fallback, if the canary has no
          #   errors yet, the query returns an empty vector and Argo Rollouts
          #   panics: "reflect: slice index out of range".
          #
          #   DENOMINATOR is left STRICT (no fallback). If the canary gets
          #   zero traffic at all, the division returns empty → analysis
          #   errors → rollout aborts. This is fail-safe: a canary you can't
          #   measure is a canary you can't trust. Do NOT add `or vector(0)`
          #   here or you'll silently mask "no traffic" as "zero errors".
          query: |
            (
              sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}",status=~"5.."}[60s]))
              or on() vector(0)
            )
            /
            sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}"}[60s]))
```

Apply it: `kubectl apply -f k8s/analysis-template.yaml`.

### B.2: Add analysis to Rollout

Update `k8s/gateway.yaml` strategy. Note the `args` block — it passes the current canary's pod-template-hash into the template:

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

You also need **continuous traffic** during the analysis so Prometheus has something to measure. Keep a loadgen pod running:

```bash
kubectl create deployment loadgen --image=curlimages/curl:latest -- sh -c \
  'while true; do curl -s http://gateway:8080/events > /dev/null; sleep 0.2; done'
```

### B.3: Test with good and bad versions

1. **Good version:** deploy a fresh image tag → AnalysisRun runs 3 measurements → all `value=[0]` → auto-promotes to 100%.
2. **Bad version:** set an env var that breaks the canary — e.g. point the canary's `EVENTS_URL` to a non-existent DNS name so all `/events` requests 504 with `GATEWAY_TIMEOUT_MS`:
   ```yaml
   env:
     - name: EVENTS_URL
       value: "http://broken-on-purpose:8081"    # canary only; stable keeps the real URL
     - name: GATEWAY_TIMEOUT_MS
       value: "2000"
   ```
   AnalysisRun will see `value=[1]` (100% error rate on canary) → `failed (2) > failureLimit (1)` → auto-abort.

```bash
kubectl argo rollouts get rollout gateway --watch
kubectl get analysisrun
kubectl get analysisrun <name> -o yaml   # to inspect measurements
```

**Paste into `submissions/lab7.md`:**
- Your AnalysisTemplate YAML (including the comments explaining `or on() vector(0)` / strict denominator / `initialDelay`)
- Output showing AnalysisRun **Successful** for a good canary (with measurement values)
- Output showing AnalysisRun **Failed** for a bad canary (with measurement values) + rollout in `Degraded` state
- Answer: "What metric would you add beyond error rate for a more complete canary analysis?"

---

## How to Submit

```bash
git switch -c feature/lab7
git add k8s/gateway.yaml k8s/analysis-template.yaml submissions/lab7.md
git commit -m "feat(lab7): add canary rollout for gateway"
git push -u origin feature/lab7
```

PR checklist:
```text
- [x] Task 1 done — Argo Rollouts installed, canary deployed, promoted + aborted
- [ ] Task 2 done — multi-step canary with Grafana observation
- [ ] Bonus Task done — automated canary analysis with Prometheus
```

---

## Acceptance Criteria

### Task 1 (6 pts)
- ✅ Argo Rollouts installed (`version` output)
- ✅ Gateway converted to Rollout (manifest in `k8s/`)
- ✅ Canary at 20% shown (Paused status)
- ✅ Manual promotion to 100%
- ✅ Bad version aborted (instant rollback)
- ✅ Written comparison of abort vs git revert speed

### Task 2 (4 pts)
- ✅ Multi-step canary strategy designed and applied
- ✅ Steps observed via `--watch`
- ✅ Dashboard observation during rollout

### Bonus Task (2.5 pts)
- ✅ AnalysisTemplate created with Prometheus query
- ✅ Auto-promote on good version
- ✅ Auto-abort on bad version

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — Manual canary | **6** | Argo Rollouts installed, Rollout created, promote + abort demonstrated |
| **Task 2** — Multi-step with observation | **4** | Multi-step strategy, Grafana observation, analysis of abort threshold |
| **Bonus Task** — Automated analysis | **2.5** | AnalysisTemplate, auto-promote + auto-abort demonstrated |
| **Total** | **12.5** | 10 main + 2.5 bonus |

---

## Resources

<details>
<summary>📚 Documentation</summary>

- [Argo Rollouts — Getting Started](https://argoproj.github.io/argo-rollouts/getting-started/)
- [Argo Rollouts — Canary Strategy](https://argoproj.github.io/argo-rollouts/features/canary/)
- [Argo Rollouts — Analysis & Progressive Delivery](https://argoproj.github.io/argo-rollouts/features/analysis/)
- [Argo Rollouts — kubectl plugin](https://argoproj.github.io/argo-rollouts/features/kubectl-plugin/)

</details>

<details>
<summary>⚠️ Common Pitfalls</summary>

- **`kubectl port-forward svc/X` does NOT load-balance.** It picks one pod at the start and sticks to it — you'll see 100% of requests going to a single pod and think the canary is broken. Use an in-cluster curl pod instead (see 7.4).
- **"Rollout not found"** — check CRD installed: `kubectl get crd rollouts.argoproj.io`.
- **Only 1 replica** — canary needs multiple replicas to split traffic. Use `replicas: 5`.
- **After abort, status is "Degraded"** — this is normal. Set the image back to the good tag, then run `kubectl argo rollouts retry rollout gateway`. Just `retry` alone will re-attempt the bad image.
- **AnalysisTemplate → `reflect: slice index out of range`** — the canary has no 5xx series yet, so the numerator returns empty. Fix: add `or on() vector(0)` on the numerator (see B.1 comments). Do NOT put it on the denominator too.
- **AnalysisRun errors out with `consecutiveErrors (5) > consecutiveErrorLimit (4)`** — Prometheus hasn't scraped the canary pod yet. Add `initialDelay: 60s` to the metric (see B.1).
- **AnalysisTemplate can't reach Prometheus** — use the fully-qualified Service DNS: `http://prometheus.monitoring.svc.cluster.local:9090`, not `http://prometheus:9090` (that only works if caller + Prometheus are in the same namespace).
- **Canary pods not in Prometheus** — the relabel `__meta_kubernetes_pod_label_rollouts_pod_template_hash` → `rs_hash` is required for the AnalysisTemplate query to filter canary vs stable. Missing this = "no series" → analysis errors forever.
- **`podTemplateHashValue: Latest`** passes the current canary's RS hash into the template args. If you skip it, your query has an unresolved `{{args.canary-hash}}` placeholder and matches nothing.
- **k3s image tag 404** — `rancher/k3s:v1.33.11-k3s1` doesn't exist as a stable tag (only `-rc1` / `-rc2`). Use `rancher/k3s:v1.33.10-k3s1` (or match your kubectl client minor version).
- **Canary pods not receiving traffic** — ensure the Service selector matches both stable and canary pod labels (use just `app: gateway`, not a pod-template-hash).

</details>
