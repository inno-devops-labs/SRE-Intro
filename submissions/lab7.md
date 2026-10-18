# Lab 7 — Progressive Delivery

## 1. Overview

The goal of this laboratory work was to implement progressive delivery for the QuickTicket Gateway using **Argo Rollouts**, perform controlled canary releases, demonstrate rollback/abort behavior for a bad version, and extend the rollout with automated Prometheus-based analysis.

The work was performed on the existing `quickticket` k3d cluster and the `feature/lab7` Git branch.

The final implementation includes:

- Argo Rollouts instead of a standard Kubernetes Deployment for the Gateway;
- 5 Gateway replicas;
- a multi-step canary strategy with explicit pauses;
- `APP_VERSION` used to make rollout revisions visible;
- in-cluster Prometheus;
- Prometheus pod discovery with the Rollout pod-template hash exposed as `rs_hash`;
- an Argo Rollouts `AnalysisTemplate`;
- manual successful and failed `AnalysisRun` demonstrations;
- automatic analysis integrated into the Gateway canary rollout;
- successful promotion to 100% and a final `Healthy` rollout.

---

## 2. Environment

### Kubernetes cluster

The existing k3d cluster was used:

```text
Cluster: quickticket
Status: 1/1 server, running
Kubernetes context: k3d-quickticket
```

The node was verified as ready before starting the lab.

### Argo Rollouts

The `argo-rollouts` namespace was created and the Argo Rollouts controller was installed.

Because the initial client-side CRD installation hit the Kubernetes annotation-size limit, the CRDs were installed with server-side apply:

```bash
kubectl apply --server-side -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
```

The controller reached the `Running` state.

The `kubectl argo rollouts` CLI was also installed and verified:

```text
kubectl-argo-rollouts v1.10.0+d90700a
```

---

# Task 1 — Manual Canary Rollout

## 3. Gateway converted to Argo Rollout

The original Gateway `Deployment` was converted to an Argo Rollouts `Rollout`.

The final Rollout uses five replicas:

```yaml
replicas: 5
```

The Service remains unchanged and continues to select the Gateway pods.

The rollout also keeps the existing:

- GHCR image;
- `imagePullSecrets`;
- environment variables;
- liveness probe;
- readiness probe;
- resource requests and limits.

An `APP_VERSION` environment variable was added so that different rollout revisions can be identified explicitly.

---

## 4. Canary strategy

The manual canary strategy used the following progression:

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

This provides gradual traffic shifting instead of replacing all five replicas at once.

---

## 5. 20% canary state

The first canary test was performed with five replicas.

At 20% canary weight, Argo Rollouts created one canary replica and kept four stable replicas.

The observed rollout state was:

```text
Desired: 5
Canary: 1
Stable: 4
SetWeight: 20
```

The rollout was paused at the canary step as required.

This demonstrated that the Gateway could be updated progressively while the stable revision remained available.

---

## 6. Manual promotion

The rollout was promoted using:

```bash
kubectl argo rollouts promote gateway
```

The rollout then advanced to the next configured weight.

The intermediate rollout state reached:

```text
SetWeight: 60
```

After the configured pause, the rollout was promoted through the remaining steps.

The final successful state was:

```text
Status: Healthy
Strategy: Canary
Step: 10/10
SetWeight: 100
ActualWeight: 100
Desired: 5
Current: 5
Updated: 5
Ready: 5
Available: 5
```

All five Gateway replicas were healthy after promotion.

---

# Task 1 — Bad Version and Abort

## 7. Bad version experiment

A deliberately different Gateway version was deployed by changing:

```yaml
APP_VERSION: "v3"
```

The rollout created a new canary revision.

The new revision entered the canary stage at 20%, while the previous stable revision remained available.

The rollout was then intentionally aborted:

```bash
kubectl argo rollouts abort gateway
```

The resulting rollout state was:

```text
Status: Degraded
```

The previous stable revision remained active with five ready replicas.

This demonstrated the key safety property of a canary deployment: a bad candidate can be stopped before it becomes the stable version.

---

## 8. Recovery after the bad version

The Gateway version was restored to the known-good version:

```yaml
APP_VERSION: "v2"
```

The manifest was applied again and the rollout was returned to a healthy state.

The final cluster state was healthy, with the Gateway running the known-good revision.

---

# Task 2 — Automated Canary Progression

## 9. Automated rollout

For the second part of the laboratory, the canary strategy was extended with explicit timed pauses.

The final progression was:

```text
20% → 60 seconds
40% → 60 seconds
60% → 60 seconds
80% → 30 seconds
100%
```

The rollout was therefore able to progress automatically without requiring manual promotion at every stage.

The final rollout reached:

```text
Status: Healthy
SetWeight: 100
ActualWeight: 100
Desired: 5
Current: 5
Updated: 5
Ready: 5
Available: 5
```

This demonstrated a controlled progressive-delivery workflow with several observation windows before full promotion.

---

# Bonus — In-Cluster Prometheus

## 10. Prometheus deployment

Prometheus was deployed inside the Kubernetes cluster using the provided Lab 7 configuration:

```text
labs/lab7/prometheus.yaml
```

The Prometheus image used was:

```text
prom/prometheus:v3.11.2
```

The image was imported into the k3d cluster so that the Prometheus pod could start successfully.

The Prometheus pod reached:

```text
Running
```

and the Prometheus rollout completed successfully.

---

## 11. Prometheus access

Prometheus was exposed locally using Kubernetes port forwarding.

The API was queried successfully:

```bash
curl -s 'http://localhost:9090/api/v1/query?query=up' | python3 -m json.tool
```

The `up` metric returned successfully.

The Gateway-specific query was also verified:

```bash
curl -s 'http://localhost:9090/api/v1/query?query=up%7Bjob%3D%22gateway%22%7D' | python3 -m json.tool
```

This confirmed that Prometheus was collecting Gateway metrics from the Kubernetes cluster.

---

# Bonus — Rollout Pod Hash and Canary Metrics

## 12. `rs_hash` labels

The Prometheus target data was inspected to verify that Gateway pods were discoverable individually.

The observed targets included:

```text
gateway-b7656b6b8-bfjhf   rs_hash=b7656b6b8   up
gateway-b7656b6b8-lhm2s   rs_hash=b7656b6b8   up
gateway-9d7fff67b-5h65b   rs_hash=None       up
gateway-b7656b6b8-lhblr   rs_hash=b7656b6b8   up
gateway-b7656b6b8-f9rvg   rs_hash=b7656b6b8   up
gateway-b7656b6b8-frb2l   rs_hash=b7656b6b8   up
gateway-9d7fff67b-5bvfw   rs_hash=None       up
```

This demonstrated that Prometheus could distinguish the current Rollout revision using the pod-template hash.

The `rs_hash` label is important for canary analysis because PromQL can restrict measurements to the current canary revision instead of mixing canary and stable traffic.

---

# Bonus — AnalysisTemplate

## 13. Prometheus-based AnalysisTemplate

An Argo Rollouts `AnalysisTemplate` named:

```text
gateway-success-rate
```

was initially created and successfully applied to the cluster.

The template checks that the Gateway is up using the in-cluster Prometheus service:

```text
http://prometheus.monitoring.svc.cluster.local:9090
```

The successful analysis condition was:

```yaml
successCondition: result[0] > 0
```

The metric was evaluated three times with a 10-second interval.

The final analysis template used:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: gateway-success-rate
spec:
  metrics:
    - name: gateway-up
      interval: 10s
      count: 3
      successCondition: result[0] > 0
      failureLimit: 1
      provider:
        prometheus:
          address: http://prometheus.monitoring.svc.cluster.local:9090
          query: |
            sum(up{job="gateway"}) > 0
```

The manifest was validated with a server-side dry run and applied successfully.

---

# 14. Successful AnalysisRun

A manual AnalysisRun was created from the same Prometheus check.

The AnalysisRun completed with:

```text
Status: Successful
Measurements: 3
```

The three measurements confirmed that the Gateway was available throughout the analysis window.

This demonstrated a successful automated analysis result before integrating the analysis into the rollout.

---

# 15. Failed AnalysisRun

A separate negative test was performed using a Prometheus query that returned zero:

```promql
vector(0)
```

For this test the failure condition was configured so that a result below the expected healthy value caused the AnalysisRun to fail.

The observed result was:

```text
AnalysisRun: gateway-failed-4h958
Status: Failed
```

The measurement showed:

```text
value: [0]
phase: Failed
```

This verified that the analysis mechanism could detect an unhealthy Prometheus result rather than only demonstrating a successful path.

---

# Bonus — Automatic Analysis in the Rollout

## 16. Analysis integrated into the canary strategy

The successful analysis template was then connected directly to the Gateway Rollout.

The canary strategy contains:

```yaml
- setWeight: 20
- analysis:
    templates:
      - templateName: gateway-success-rate
```

The analysis was therefore executed automatically during a real canary rollout.

A new Gateway revision was deployed with:

```yaml
APP_VERSION: "v2-analysis"
```

The rollout automatically started an AnalysisRun.

The observed rollout state included:

```text
Step: 4/10
SetWeight: 40
ActualWeight: 40
Canary Revision: 6
AnalysisRun: gateway-6f458bc79-6-1
Status: Successful
Measurements: 3
```

The successful AnalysisRun allowed the rollout to continue.

The rollout was then promoted through the remaining stages.

---

## 17. Final automated-analysis result

The final state after the automated analysis and canary progression was:

```text
Status: Healthy
Strategy: Canary
Step: 10/10
SetWeight: 100
ActualWeight: 100

Desired: 5
Current: 5
Updated: 5
Ready: 5
Available: 5

Revision: 6
gateway-6f458bc79 stable

AnalysisRun:
gateway-6f458bc79-6-1
Status: Successful
Measurements: 3
```

Therefore the final rollout reached 100% only after the configured analysis completed successfully.

---

# 18. Reliability / progressive-delivery analysis

The laboratory demonstrates three different safety mechanisms.

### Manual canary

The first rollout exposed only a small percentage of the new revision before promotion. This reduced the blast radius and allowed the rollout to be stopped manually.

### Timed automated progression

The second strategy introduced observation windows between traffic-shifting steps:

```text
20% → 40% → 60% → 80% → 100%
```

with explicit pauses. This prevents an update from immediately reaching all replicas.

### Automated health analysis

The AnalysisTemplate adds a machine-checkable gate based on Prometheus. Instead of relying only on a human watching the rollout, Argo Rollouts can evaluate a metric and stop the rollout when the configured condition is not satisfied.

The failed AnalysisRun test also demonstrated that the analysis mechanism has a real failure path.

---

# 19. Evidence summary

| Requirement | Evidence |
|---|---|
| Argo Rollouts installed | `argo-rollouts` controller reached `Running` |
| Gateway uses Rollout | `k8s/gateway.yaml` changed from `Deployment` to `Rollout` |
| 5 replicas | `Desired/Current/Ready/Available = 5` |
| Canary rollout | 20%, 40%, 60%, 80%, 100% progression |
| Manual promotion | `kubectl argo rollouts promote gateway` |
| Bad version | `APP_VERSION=v3` canary revision |
| Abort | Rollout reached `Degraded` after `kubectl argo rollouts abort gateway` |
| Stable recovery | Known-good revision restored and rollout became healthy |
| Automated pauses | 60s / 60s / 60s / 30s pauses configured |
| In-cluster Prometheus | `prometheus` pod reached `Running` in `monitoring` |
| Prometheus query | `up` and `up{job="gateway"}` returned successfully |
| Rollout hash | Gateway targets exposed `rs_hash` values |
| Successful AnalysisRun | `gateway-success-47nhb` → `Successful` |
| Failed AnalysisRun | `gateway-failed-4h958` → `Failed` |
| Automatic analysis | Rollout created `gateway-6f458bc79-6-1` |
| Automatic analysis result | AnalysisRun → `Successful`, 3 measurements |
| Final state | Rollout `Healthy`, 100%, 5/5 ready |

---

# 20. Files produced

The main student-produced files for Lab 7 are:

```text
k8s/gateway.yaml
k8s/gateway-analysis.yaml
```

The provided Lab 7 infrastructure assets used during the bonus work were:

```text
labs/lab7/prometheus.yaml
labs/lab7/analysis-template.yaml
labs/lab7/loadgen.yaml
```

---

# 21. Git status and branch

The work was performed on:

```text
feature/lab7
```

The final student changes before submission were:

```text
modified:   k8s/gateway.yaml
untracked:  k8s/gateway-analysis.yaml
```

The submission report is:

```text
submissions/lab7.md
```

---

# 22. Conclusion

Lab 7 successfully implemented progressive delivery for the QuickTicket Gateway.

The Gateway was converted to an Argo Rollout with five replicas and a gradual canary strategy. Both successful and bad-version scenarios were tested. The bad version was aborted while the previous stable revision remained available, demonstrating a controlled rollback mechanism.

The canary was then extended with timed automatic progression. In-cluster Prometheus was deployed and verified, and Rollout pod-template hashes were exposed as `rs_hash` labels so that metrics could be associated with individual revisions.

Finally, Prometheus-based Argo Rollouts analysis was demonstrated in both successful and failed scenarios and then integrated into the actual canary rollout. The final rollout completed the analysis successfully and reached:

```text
Healthy
100%
5/5 Ready
```

This provides a complete progressive-delivery workflow with gradual traffic shifting, observation windows, automated health validation, and a demonstrated failure/abort path.
