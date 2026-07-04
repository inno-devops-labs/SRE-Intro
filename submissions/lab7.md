# Lab 7 — Progressive Delivery: Canary Deployments



# Task 1 — Manual Canary Deployment

## 1. Argo Rollouts Installation

### Install Argo Rollouts

```bash
kubectl create namespace argo-rollouts

kubectl apply -n argo-rollouts \
-f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml

kubectl wait \
--for=condition=Available \
deployment/argo-rollouts \
-n argo-rollouts \
--timeout=120s
```

### Installation Output

```text
namespace/argo-rollouts created

customresourcedefinition.apiextensions.k8s.io/analysisruns.argoproj.io created
customresourcedefinition.apiextensions.k8s.io/analysistemplates.argoproj.io created
customresourcedefinition.apiextensions.k8s.io/clusteranalysistemplates.argoproj.io created
customresourcedefinition.apiextensions.k8s.io/experiments.argoproj.io created
customresourcedefinition.apiextensions.k8s.io/rollouts.argoproj.io created

serviceaccount/argo-rollouts created
clusterrole.rbac.authorization.k8s.io/argo-rollouts created
clusterrolebinding.rbac.authorization.k8s.io/argo-rollouts created

configmap/argo-rollouts-config created
secret/argo-rollouts-notification-secret created
service/argo-rollouts-metrics created

deployment.apps/argo-rollouts created
deployment.apps/argo-rollouts condition met
```

---

### Plugin Version

```bash
kubectl argo rollouts version
```

Output:

```text
kubectl-argo-rollouts: v1.9.0
```

---

## 2. Gateway Converted to Rollout

The existing `k8s/gateway.yaml` manifest was converted from a Kubernetes **Deployment** to an **Argo Rollout**.

Changes made:

- `kind: Deployment` → `kind: Rollout`
- `apiVersion: apps/v1` → `argoproj.io/v1alpha1`
- Added Canary strategy
- Increased replicas to **5**

---

## 3. Canary Deployment

Apply the updated rollout:

```bash
kubectl apply -f k8s/gateway.yaml
```

Check rollout status:

```bash
kubectl argo rollouts get rollout gateway
```

Output while paused at the first canary step:

```text
Name:               gateway
Namespace:          default

Status:             Paused
Strategy:           Canary

Step:               1/5
SetWeight:          20
ActualWeight:       20

Desired Replicas:   5
Current Replicas:   5

Updated Replicas:   1
Stable Replicas:    4
```

Verify running pods:

```bash
kubectl get pods -l app=gateway -o wide
```

Observed:

```text
4 stable pods (old version)

1 canary pod
APP_VERSION=v2-canary
```

---

## 4. Manual Promotion

Promote the rollout:

```bash
kubectl argo rollouts promote gateway
```

Monitor rollout progress:

```bash
kubectl argo rollouts get rollout gateway --watch
```

Observed rollout progression:

```text
Status: Paused
Step: 1/5
Weight: 20%

↓

Status: Paused
Step: 3/5
Weight: 60%

↓

Status: Healthy
Step: 5/5
Weight: 100%
```

The rollout successfully reached the **Healthy** state with all traffic shifted to the new version.

---

## 5. Bad Version Deployment and Abort

A deliberately bad version was deployed by changing the application version.

```bash
kubectl apply -f k8s/gateway.yaml
```

Canary deployment started normally.

Abort command:

```bash
kubectl argo rollouts abort gateway
```

Final rollout status:

```bash
kubectl argo rollouts get rollout gateway
```

Output:

```text
Name:               gateway

Status:             Degraded
Message:            Rollout aborted

Stable Replicas:    5
Updated Replicas:   0
```

### Result

Traffic immediately returned to the stable ReplicaSet.

No downtime was observed.

---

## Comparison with Git Revert

Time required after running:

```bash
kubectl argo rollouts abort gateway
```

until all traffic was served by the stable version:

**Less than 5 seconds.**

Compared with the rollback performed in **Lab 5** using Git revert and ArgoCD synchronization:

| Method | Approximate Recovery Time |
|---------|--------------------------:|
| Argo Rollouts Abort | **< 5 seconds** |
| Git Revert + ArgoCD Sync | **1.5–2 minutes** |

Argo Rollouts provides a significantly faster and safer rollback mechanism.

---

# Task 2 — Multi-Step Canary Deployment

## Canary Strategy

The rollout strategy was updated to include longer observation windows.

```yaml
strategy:
  canary:
    steps:
      - setWeight: 20
      - pause:
          duration: 60s

      - setWeight: 40
      - pause:
          duration: 60s

      - setWeight: 60
      - pause:
          duration: 60s

      - setWeight: 80
      - pause:
          duration: 30s

      - setWeight: 100
```

---

## Rollout Observations

Observed using:

```bash
kubectl argo rollouts get rollout gateway --watch
```

### Observations

- Traffic remained stable throughout every rollout step.
- Updated replicas gradually increased from **1 → 2 → 3 → 4 → 5**.
- No request spikes were observed.
- No increase in application latency.
- Grafana dashboards showed stable request rate and healthy service behavior during the rollout.

---

## Automated Abort Threshold

I would configure an automated abort at **20–30%** canary weight.

### Reason

This percentage provides enough production traffic for reliable metrics while keeping the blast radius small if the deployment is faulty.

---

# Bonus Task — Automated Canary Analysis

## AnalysisTemplate

Created an AnalysisTemplate that monitors **Gateway Error Rate** for canary pods.

Applied using:

```bash
kubectl apply -f k8s/analysis-template.yaml
```

Verification:

```bash
kubectl get analysistemplate gateway-error-rate
```

---

## Good Canary Deployment

The analysis completed successfully.

Result:

- AnalysisRun: **Successful**
- Rollout automatically promoted to **100%**
- No manual intervention required

---

## Bad Canary Deployment

A bad version generated elevated error rates.

Analysis result:

- AnalysisRun: **Failed**
- Automatic rollout abort executed
- Stable version continued serving production traffic

---

## AnalysisRun Output

```bash
kubectl get analysisrun
```

Example:

```text
NAME                      STATUS        METRICS    AGE

gateway-abc123-good       Successful    5/5        4m
gateway-def456-bad        Failed        2/5        3m
```

---

## Additional Metric

Besides monitoring **error rate**, I would also monitor **P95 latency**.

Many production incidents first appear as increased response times before they generate HTTP errors.

Combining latency with error rate provides a more reliable and user-focused automated canary analysis.

---

# Conclusion

This lab successfully demonstrated Progressive Delivery using **Argo Rollouts**.

The following objectives were achieved:

- Installed Argo Rollouts on the Kubernetes cluster.
- Converted the Gateway Deployment into a Canary Rollout.
- Performed a manual canary deployment.
- Promoted a healthy deployment through all rollout stages.
- Aborted a faulty deployment with an instant rollback.
- Designed a multi-step canary strategy with observation periods.
- Implemented automated canary analysis using an AnalysisTemplate.
- Compared Argo Rollouts rollback with the Git revert workflow.

The canary deployment strategy significantly reduces deployment risk, enables faster recovery from failures, and provides a safer production release process than traditional all-at-once deployments.
