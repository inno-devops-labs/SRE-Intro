# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

### 1. Link to your Github Actions run
https://github.com/ustkost/SRE-Intro/actions/runs/28254539204/job/83714160043

### 2. Show pushed images
```bash
[ustkost@prime SRE-Intro]$ gh api user/packages?package_type=container --jq '.[].name'
quickticket-gateway
quickticket-events
quickticket-payments
```

### 3. ArgoCD deployment
```bash
[ustkost@prime SRE-Intro]$ argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/ustkost/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (963c2c1)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
```

### 4. Prove the Git change was synced
ArgoCD UI screenshot: https://images.iimg.live/images/epic-moment-2621.webp
Deployment version changed:
```bash
[ustkost@prime SRE-Intro]$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2
```

### 5. What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?
ArgoCD is a GitOps tool and it sees Git as the source of truth. Therefore, after a manual `kubectl edit`, ArgoCD detects a drift on the next sync cycle or manual sync and reverts the manual change to the original Git state.

## Task 2 — Rollback via GitOps

### `argocd app get` after bad deploy
```bash
[ustkost@prime SRE-Intro]$ argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/ustkost/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (d25d2b7)
Health Status:      Progressing

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH       HOOK  MESSAGE
       Service     default    postgres  Synced  Healthy            service/postgres unchanged
       Service     default    gateway   Synced  Healthy            service/gateway unchanged
       Service     default    events    Synced  Healthy            service/events unchanged
       Service     default    payments  Synced  Healthy            service/payments unchanged
       Service     default    redis     Synced  Healthy            service/redis unchanged
apps   Deployment  default    payments  Synced  Healthy            deployment.apps/payments unchanged
apps   Deployment  default    gateway   Synced  Progressing        deployment.apps/gateway configured
apps   Deployment  default    postgres  Synced  Healthy            deployment.apps/postgres unchanged
apps   Deployment  default    redis     Synced  Healthy            deployment.apps/redis unchanged
apps   Deployment  default    events    Synced  Healthy            deployment.apps/events unchanged
```

### `kubectl get pods`
```bash
[ustkost@prime SRE-Intro]$ kubectl get pods
NAME                        READY   STATUS             RESTARTS   AGE
events-69dbb86f4f-g8wwt     1/1     Running            0          48m
gateway-5569b7d65c-p6fgq    0/1     ImagePullBackOff   0          6m36s
gateway-dd8f4ddd-n8f9r      1/1     Running            0          48m
payments-8449db899c-pdzkk   1/1     Running            0          48m
postgres-78489d7f5f-zk5z7   1/1     Running            0          48m
redis-6fcfb5475d-htpcs      1/1     Running            0          48m
```

### git logs 
```bash
[ustkost@prime SRE-Intro]$ git log --oneline -3
3e8bf91 (HEAD -> main, origin/main, origin/HEAD) Revert "feat: deploy new gateway version"
d25d2b7 feat: deploy new gateway version
963c2c1 fix: add version label to gateway
```

### app healthy after rollback
```bash
[ustkost@prime SRE-Intro]$ argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/ustkost/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (3e8bf91)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
```

### How long from git revert + push to pods being healthy again?
Recovery time was approximately 45 seconds (between reverting the commit and seeing the pod running). In that time ArgoCD detected the change, Kubernetes scheduled pod recreation and the pod itself was recreated.
