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

Install the kubectl plugin:
```bash
curl -LO https://github.com/argoproj/argo-rollouts/releases/latest/download/kubectl-argo-rollouts-linux-amd64
chmod +x kubectl-argo-rollouts-linux-amd64
sudo mv kubectl-argo-rollouts-linux-amd64 /usr/local/bin/kubectl-argo-rollouts
```

Verify: `kubectl argo rollouts version`

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

While the canary is paused at 20%, test that both versions serve traffic:

```bash
kubectl port-forward svc/gateway 3080:8080 &

# Hit the endpoint multiple times — ~20% should come from canary
for i in $(seq 1 20); do
  curl -s http://localhost:3080/health | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])"
done

kill %1 2>/dev/null
```

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

### 7.9: Observe on Grafana

Start the monitoring stack alongside your cluster. Generate traffic with the load generator. Trigger a rollout and watch the golden signals dashboard:

- Does request rate change during canary steps?
- Does latency change as canary percentage increases?
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

### B.1: Create AnalysisTemplate

Create `k8s/analysis-template.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: gateway-error-rate
spec:
  metrics:
    - name: error-rate
      interval: 30s
      count: 3
      successCondition: result[0] < 0.05
      failureLimit: 1
      provider:
        prometheus:
          address: http://prometheus:9090
          query: |
            sum(rate(gateway_requests_total{status=~"5.."}[1m]))
            / sum(rate(gateway_requests_total[1m]))
```

Apply it: `kubectl apply -f k8s/analysis-template.yaml`

### B.2: Add analysis to Rollout

Update `k8s/gateway.yaml` strategy:

```yaml
strategy:
  canary:
    steps:
      - setWeight: 20
      - pause: {duration: 30s}
      - analysis:
          templates:
            - templateName: gateway-error-rate
      - setWeight: 50
      - pause: {duration: 30s}
      - setWeight: 100
```

### B.3: Test with good and bad versions

1. Deploy a good version → watch AnalysisRun succeed → auto-promote to 100%
2. Deploy a bad version (inject `PAYMENT_FAILURE_RATE=0.5`) → watch AnalysisRun fail → auto-abort

```bash
kubectl argo rollouts get rollout gateway --watch
# Should show: AnalysisRun: Running → Successful (good) or Failed (bad)
```

**Paste into `submissions/lab7.md`:**
- Your AnalysisTemplate YAML
- `kubectl argo rollouts get rollout gateway` output showing automated promotion (good version)
- `kubectl argo rollouts get rollout gateway` output showing automated abort (bad version)
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

- **"Rollout not found"** — check CRD installed: `kubectl get crd rollouts.argoproj.io`
- **Only 1 replica** — canary needs multiple replicas to split traffic. Use `replicas: 5`
- **After abort, status is "Degraded"** — this is normal. Run `kubectl argo rollouts retry rollout gateway` to reset
- **AnalysisTemplate can't reach Prometheus** — check the address matches your Prometheus service name and port
- **Canary pods not receiving traffic** — ensure the Service selector matches both stable and canary pod labels

</details>
