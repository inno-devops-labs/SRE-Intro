# Lab 5 Report — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup (6 pts)

### 1. Link to your GitHub Actions run (green check)
```text
https://github.com/kage-ops-dev/SRE-Intro/actions/runs/27414130550
```

### 2. Output of gh api user/packages?package_type=container showing pushed images
```bash
$ gh api user/packages?package_type=container --jq '.[].name'
```
```text
quickticket-gateway
quickticket-events
quickticket-payments
```

### 3. Output of argocd app get quickticket showing Synced + Healthy
```bash
$ ./argocd.exe app get quickticket
```
```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/kage-ops-dev/SRE-Intro.git
  Target:           
  Path:             k8s/chart
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (4f7f1cd)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    payments  Synced  Healthy        service/payments configured. Warning: resource services/payments is missing the kubectl.kubernetes.io/last-applied-configuration annotation which is required by  apply.  apply should only be used on resources created declaratively by either  create --save-config or  apply. The missing annotation will be patched automatically.
       Service     default    postgres  Synced  Healthy        service/postgres configured. Warning: resource services/postgres is missing the kubectl.kubernetes.io/last-applied-configuration annotation which is required by  apply.  apply should only be used on resources created declaratively by either  create --save-config or  apply. The missing annotation will be patched automatically.
       Service     default    gateway   Synced  Healthy        service/gateway configured. Warning: resource services/gateway is missing the kubectl.kubernetes.io/last-applied-configuration annotation which is required by  apply.  apply should only be used on resources created declaratively by either  create --save-config or  apply. The missing annotation will be patched automatically.
       Service     default    events    Synced  Healthy        service/events configured. Warning: resource services/events is missing the kubectl.kubernetes.io/last-applied-configuration annotation which is required by  apply.  apply should only be used on resources created declaratively by either  create --save-config or  apply. The missing annotation will be patched automatically.
       Service     default    redis     Synced  Healthy        service/redis configured. Warning: resource services/redis is missing the kubectl.kubernetes.io/last-applied-configuration annotation which is required by  apply.  apply should only be used on resources created declaratively by either  create --save-config or  apply. The missing annotation will be patched automatically.
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments configured. Warning: resource deployments/payments is missing the kubectl.kubernetes.io/last-applied-configuration annotation which is required by  apply.  apply should only be used on resources created declaratively by either  create --save-config or  apply. The missing annotation will be patched automatically.
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis configured. Warning: resource deployments/redis is missing the kubectl.kubernetes.io/last-applied-configuration annotation which is required by  apply.  apply should only be used on resources created declaratively by either  create --save-config or  apply. The missing annotation will be patched automatically.
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured. Warning: resource deployments/gateway is missing the kubectl.kubernetes.io/last-applied-configuration annotation which is required by  apply.  apply should only be used on resources created declaratively by either  create --save-config or  apply. The missing annotation will be patched automatically.
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres configured. Warning: resource deployments/postgres is missing the kubectl.kubernetes.io/last-applied-configuration annotation which is required by  apply.  apply should only be used on resources created declaratively by either  create --save-config or  apply. The missing annotation will be patched automatically.
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events configured. Warning: resource deployments/events is missing the kubectl.kubernetes.io/last-applied-configuration annotation which is required by  apply.  apply should only be used on resources created declaratively by either  create --save-config or  apply. The missing annotation will be patched automatically.
```

### 4. Output proving a Git change was synced (label, annotation, or image tag change visible in cluster)
```bash
$ kubectl get deployment gateway --show-labels
```
```text
NAME      READY   UP-TO-DATE   AVAILABLE   AGE   LABELS
gateway   1/1     1            1           80m   app.kubernetes.io/managed-by=Helm,version=v2
```
```bash
$ kubectl get deployment gateway -o jsonpath="{.metadata.labels.version}"
```
```text
v2
```

### 5. Answer: "What happens if someone manually runs kubectl edit on a resource managed by ArgoCD?"

If a user manually modifies a resource in the cluster using `kubectl edit` (or `kubectl patch`/`apply`), it creates a **configuration drift** between the live state in the cluster and the desired state defined in the Git repository. 

ArgoCD continuously monitors the cluster, detects this drift, and marks the application status as **`OutOfSync`**. What happens next depends on the application's synchronization policy:

1. **If Automated Sync with Self-Heal is ENABLED:**
   ArgoCD will automatically detect the manual change as a deviation from the source of truth (Git) and will immediately trigger a reconciliation loop. Within seconds, ArgoCD will **overwrite and revert** the manual edits, restoring the resource back to the exact configuration specified in the Git repository.

2. **If Automated Sync is enabled but Self-Heal is DISABLED:**
   The manual changes will temporarily persist in the cluster. However, the application will remain in an `OutOfSync` state in the ArgoCD dashboard, highlighting the exact lines that drifted. The manual changes will be completely overwritten the next time a new commit is pushed to the Git repository, or if a manual hard-sync is triggered via the UI/CLI.


## Task 2 — Rollback via GitOps (4 pts)

### 6. argocd app get showing Degraded after bad deploy
```bash
$ ./argocd.exe app get quickticket
```
```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/kage-ops-dev/SRE-Intro.git
  Target:           
  Path:             k8s/chart
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (e0ece6d)
Health Status:      Progressing

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH       HOOK  MESSAGE
       Service     default    events    Synced  Healthy            service/events unchanged
       Service     default    payments  Synced  Healthy            service/payments unchanged
       Service     default    gateway   Synced  Healthy            service/gateway unchanged
       Service     default    redis     Synced  Healthy            service/redis unchanged
       Service     default    postgres  Synced  Healthy            service/postgres unchanged
apps   Deployment  default    redis     Synced  Healthy            deployment.apps/redis unchanged
apps   Deployment  default    payments  Synced  Healthy            deployment.apps/payments unchanged
apps   Deployment  default    events    Synced  Healthy            deployment.apps/events unchanged
apps   Deployment  default    postgres  Synced  Healthy            deployment.apps/postgres unchanged
apps   Deployment  default    gateway   Synced  Progressing        deployment.apps/gateway configured
```

### 7. kubectl get pods showing ImagePullBackOff
```bash
$ kubectl get pods
```
```text
NAME                                                     READY   STATUS             RESTARTS   AGE
alertmanager-monitoring-kube-prometheus-alertmanager-0   2/2     Running            0          84m
events-5879b6b6c4-b7xsj                                  1/1     Running            0          15m
gateway-5b6cc4cf47-slt82                                 0/1     ImagePullBackOff   0          20s
gateway-8887bdb69-znmks                                  1/1     Running            0          6m59s
monitoring-grafana-c4d66cf58-dnczs                       3/3     Running            0          85m
monitoring-kube-prometheus-operator-69c79c5d78-76blq     1/1     Running            0          85m
monitoring-kube-state-metrics-868694bc4b-6p5sx           1/1     Running            0          85m
monitoring-prometheus-node-exporter-r9mfr                1/1     Running            0          85m
payments-7745675fbb-qbjvd                                1/1     Running            0          15m
postgres-78489d7f5f-crrn7                                1/1     Running            0          86m
prometheus-monitoring-kube-prometheus-prometheus-0       2/2     Running            0          84m
redis-6fcfb5475d-z94mb                                   1/1     Running            0          86m
```

### 8. git log --oneline -3 showing the deploy + revert commits\
```bash
$ git log --oneline -3
```
```text
c1f06e7 (HEAD -> main, origin/main, origin/HEAD) Revert "feat: deploy new gateway version"
e0ece6d feat: deploy new gateway version
2752bf6 fix: move version label to top-level metadata
```

### 9. argocd app get showing Healthy after revert
```bash
$ ./argocd.exe app get quickticket
```
```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/kage-ops-dev/SRE-Intro.git
  Target:           
  Path:             k8s/chart
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (c1f06e7)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    events    Synced  Healthy        service/events unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
```

### 10. Answer: "How long from git revert + push to pods being healthy again?"
The time it takes for the pods to return to a healthy state depends on how the synchronization is triggered:

1. **Automated Polling (Default Behavior):** By default, ArgoCD polls the Git repository every **3 minutes** (180 seconds) to detect changes. If left completely automated without manual intervention or Git webhooks, it can take up to 3 minutes for ArgoCD to notice the `git revert` push and initiate the sync.

2. **Manual Sync / Webhook:** When forcing the synchronization manually via the command `./argocd.exe app sync quickticket` (or if a GitHub webhook is configured), ArgoCD detects the new commit **immediately**. 

Once ArgoCD triggers the sync, it takes Kubernetes about **10 to 15 seconds** to terminate the broken pods, apply the restored deployment configuration, pull the correct container images from `ghcr.io`, and bring the gateway pods back to the `Running` (`1/1 READY`) status.


## Bonus Task — Automated Image Tag Update (2 pts)

### 11. Updated workflow file showing auto-tag update
Relevant snippet from `.github/workflows/ci.yml`:
```yaml
    if: "!startsWith(github.event.head_commit.message, 'ci:')"
    permissions:
      packages: write
      contents: write
    steps:
      # ... build and push steps ...
      - name: Update image tags in manifests
        run: |
          SHA=${{ github.sha }}
          sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/${{ github.actor }}/quickticket-gateway:${SHA}|" k8s/chart/templates/gateway.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-events:.*|image: ghcr.io/${{ github.actor }}/quickticket-events:${SHA}|" k8s/chart/templates/events.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-payments:.*|image: ghcr.io/${{ github.actor }}/quickticket-payments:${SHA}|" k8s/chart/templates/payments.yaml

      - name: Commit and push manifest update
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@github.com"
          git add -f k8s/
          git diff --cached --quiet || git commit -m "ci: update image tags to ${{ github.sha }}"
          git push
```


### 12. Git log showing: code commit → CI tag-update commit
```bash
$ git log --oneline -3
```
```text
c9934c1 (HEAD -> main, origin/main, origin/HEAD) ci: update image tags to 9aabaf188cfc01efb314e2f09ddf1aaa912f445a
9aabaf1 feat: use force flag for k8s directory in workflow
174c62d feat: add automated image tag updates to CI
```

### 13. ArgoCD syncing the auto-updated tag without manual intervention
```bash
$ ./argocd.exe app get quickticket
```
```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/kage-ops-dev/SRE-Intro.git
  Target:           
  Path:             k8s/chart
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (c9934c1)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events configured
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments configured
```
