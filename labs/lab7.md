# Lab 7 — Progressive Delivery: Canary Deployments

![difficulty](https://img.shields.io/badge/difficulty-intermediate-yellow)
![topic](https://img.shields.io/badge/topic-Canary%20Deployments-blue)
![points](https://img.shields.io/badge/points-10%2B2-orange)
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

While the canary is paused at 20%, verify requests are actually split across pods.

> ⚠️ **Do not use `kubectl port-forward svc/gateway` for this.** Port-forward picks ONE endpoint at session start and sticks to it — you'll see 100% of requests hit a single pod and wrongly conclude the canary is broken. To observe the real split, traffic must go through `kube-proxy`, which only happens for in-cluster clients.

Apply the provided in-cluster loadgen and count requests per pod from their logs:

```bash
kubectl apply -f labs/lab7/loadgen.yaml

# Let it run for ~30 seconds, then count per-pod requests
sleep 30
for pod in $(kubectl get pods -l app=gateway -o name); do
  count=$(kubectl logs $pod 2>/dev/null | grep -c 'GET /events')
  img=$(kubectl get $pod -o jsonpath='{.spec.containers[0].image}')
  echo "$pod image=$img events_requests=$count"
done

# Stop loadgen when done observing
kubectl delete -f labs/lab7/loadgen.yaml
```

Expect roughly 1-in-5 requests hitting the canary pod (with small variance for a short sample). That matches `setWeight: 20`.

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

### 7.9: Observe the rollout

> 💡 The docker-compose Prometheus/Grafana from Lab 3 **cannot scrape pods inside k3d** — k3d pods have private IPs in a bridge network the host can't reach. Use `kubectl argo rollouts get rollout gateway --watch` for real-time step/weight/replica observation. (The Bonus Task deploys an in-cluster Prometheus if you want richer metrics during analysis.)

Apply the provided loadgen for continuous traffic, then trigger the rollout:

```bash
kubectl apply -f labs/lab7/loadgen.yaml
kubectl argo rollouts set image gateway gateway=<your-new-image>

# In another terminal:
kubectl argo rollouts get rollout gateway --watch
```

Observe across steps:
- Does request rate stay steady across canary steps?
- Does the updated-replica count climb 1 → 2 → 3 → 4 → 5 as weight climbs?
- At which step would you abort if you saw elevated errors?

Clean up: `kubectl delete -f labs/lab7/loadgen.yaml`.

**Paste into `submissions/lab7.md`:**
- Your multi-step canary strategy YAML
- Output of `kubectl argo rollouts get rollout gateway --watch` showing at least 3 steps
- Dashboard observation during the rollout
- Answer: "At what canary percentage would you want an automated abort? Why?"

---

## Bonus Task — Automated Canary Analysis (2 pts)

> 🌟 For those who want extra challenge and experience.

**Objective:** Create an AnalysisTemplate that queries Prometheus during canary, auto-promoting good versions and auto-aborting bad ones.

### B.1: Install in-cluster Prometheus

```bash
kubectl apply -f labs/lab7/prometheus.yaml
kubectl -n monitoring rollout status deployment/prometheus --timeout=60s
```

Verify the gateway pods are discovered with their `rs_hash` label (this label is the key mechanism that lets the AnalysisTemplate distinguish canary from stable):

```bash
kubectl port-forward -n monitoring svc/prometheus 9091:9090 &
curl -s 'http://localhost:9091/api/v1/targets?state=active' | python3 -c "
import sys,json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(t['labels'].get('pod'), 'rs=', t['labels'].get('rs_hash'), t['health'])"
kill %1 2>/dev/null
```

Each of the 5 gateway pods should appear with `health=up`.

> 📂 Read [`labs/lab7/prometheus.yaml`](./lab7/prometheus.yaml) — the comments explain what each relabel rule does and why `rollouts-pod-template-hash → rs_hash` is required for canary analysis to work.

### B.2: Install the AnalysisTemplate

```bash
kubectl apply -f labs/lab7/analysis-template.yaml
kubectl get analysistemplate gateway-error-rate
```

> 📂 Read [`labs/lab7/analysis-template.yaml`](./lab7/analysis-template.yaml) — before moving on, understand these four design choices from the comments:
> 1. **Why `initialDelay: 60s`** (Prometheus needs time to discover and scrape a new canary pod).
> 2. **Why `or on() vector(0)` on the numerator** (zero 5xx is a real answer, not an error).
> 3. **Why the denominator stays strict** (no traffic = can't measure = fail-safe abort).
> 4. **Why `{{args.canary-hash}}`** scopes the query to only canary replicas.

### B.3: Wire analysis into the Rollout strategy

Add an `analysis` step between weights. The `args` block passes the current canary's pod-template-hash into the template at runtime:

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

Apply the updated Rollout: `kubectl apply -f k8s/gateway.yaml`.

### B.4: Test — good version auto-promotes

```bash
# Continuous traffic so the analysis has something to measure
kubectl apply -f labs/lab7/loadgen.yaml

# Tag a new image and trigger the rollout
docker tag quickticket-gateway:v1 quickticket-gateway:v2
k3d image import -c quickticket quickticket-gateway:v2
kubectl argo rollouts set image gateway gateway=quickticket-gateway:v2

# Watch progress
kubectl argo rollouts get rollout gateway --watch
```

Expect: Paused → AnalysisRun `Running` → 3 measurements with `value=[0]` → AnalysisRun `Successful` → auto-promote to 100% → `Healthy`. No human intervention.

### B.5: Test — bad version auto-aborts

To produce real 5xx on the canary (rather than just an image that won't start), point the canary's `EVENTS_URL` to a name that doesn't resolve. Edit `k8s/gateway.yaml` env for the gateway container:

```yaml
env:
  - name: EVENTS_URL
    value: "http://broken-on-purpose:8081"   # every /events call → 504
  - name: GATEWAY_TIMEOUT_MS
    value: "2000"
```

```bash
kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts get rollout gateway --watch
```

Expect: canary pod comes up → serves traffic via Service → /events calls time out → Prometheus sees 5xx → AnalysisRun measurements = `[1.0]` → `failed (2) > failureLimit (1)` → rollout auto-aborts (`Degraded`). Stable pods untouched.

Inspect the analysis runs:

```bash
kubectl get analysisrun
kubectl get analysisrun <name-from-above> -o yaml | less
```

Remember to revert `EVENTS_URL` afterwards: `kubectl apply -f k8s/gateway.yaml` with the original value, then `kubectl argo rollouts retry rollout gateway`.

### B.6: Cleanup

```bash
kubectl delete -f labs/lab7/loadgen.yaml
```

**Paste into `submissions/lab7.md`:**
- `kubectl get analysistemplate gateway-error-rate` output
- `kubectl get analysisrun` output showing **Successful** run (good canary) and **Failed** run (bad canary)
- `kubectl get analysisrun <failed-name> -o yaml` showing the measurement values = `[1]`
- Final `kubectl argo rollouts get rollout gateway` after the aborted bad deploy (Degraded, stable pods running)
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

### Bonus Task (2 pts)
- ✅ AnalysisTemplate created with Prometheus query
- ✅ Auto-promote on good version
- ✅ Auto-abort on bad version

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — Manual canary | **6** | Argo Rollouts installed, Rollout created, promote + abort demonstrated |
| **Task 2** — Multi-step with observation | **4** | Multi-step strategy, Grafana observation, analysis of abort threshold |
| **Bonus Task** — Automated analysis | **2** | AnalysisTemplate, auto-promote + auto-abort demonstrated |
| **Total** | **12** | 10 main + 2 bonus |

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

- **"Rollout not found"** — CRD not installed: `kubectl get crd rollouts.argoproj.io`.
- **Only 1 replica** — canary needs multiple replicas to split traffic. Use `replicas: 5`.
- **After `abort`, status is "Degraded"** — this is normal. To redeploy, first set the image back to a good tag (`kubectl argo rollouts set image ...`) then `kubectl argo rollouts retry rollout gateway`. A bare `retry` will re-attempt the bad image.
- **Canary pod `ErrImageNeverPull`** — if you're using locally built images with `imagePullPolicy: Never`, re-import them after rebuilding: `k3d image import -c quickticket <image:tag>`.
- **k3s image tag 404 on cluster create** — `rancher/k3s:v1.33.11-k3s1` doesn't exist as a stable tag (only `-rc1`/`-rc2`). Use `rancher/k3s:v1.33.10-k3s1` (or match your kubectl client minor version).
- **Canary pods not receiving traffic** — ensure the Service selector matches both stable and canary pods (use just `app: gateway`, not a pod-template-hash).

</details>
