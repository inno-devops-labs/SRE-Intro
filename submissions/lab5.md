# Lab 5 report
## Task1 

**`.github/workflows/ci.yml`** and updated `k8s/` manifests go in your fork.

**Paste into `submissions/lab5.md`:**
1. Link to your GitHub Actions run (green check)
https://github.com/vIadimirsoIovev/SRE-Intro/actions/runs/28670048985
2. Output of `gh api user/packages?package_type=container` showing pushed images
```
go-hello-world
quickticket-gateway
quickticket-events
quickticket-payments
```
3. Output of `argocd app get quickticket` showing Synced + Healthy
```
user@MacBook-Air sre-intro % argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/viadimirsoiovev/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (9ced283)
Health Status:      Progressing

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH       HOOK  MESSAGE
       Service     default    postgres  Synced  Healthy            service/postgres configured
       Service     default    payments  Synced  Healthy            service/payments configured
       Service     default    redis     Synced  Healthy            service/redis configured
       Service     default    gateway   Synced  Healthy            service/gateway configured
       Service     default    events    Synced  Healthy            service/events configured
apps   Deployment  default    payments  Synced  Progressing        deployment.apps/payments configured
apps   Deployment  default    gateway   Synced  Progressing        deployment.apps/gateway configured
apps   Deployment  default    postgres  Synced  Healthy            deployment.apps/postgres configured
apps   Deployment  default    events    Synced  Progressing        deployment.apps/events configured
apps   Deployment  default    redis     Synced  Healthy            deployment.apps/redis configured
```
4. Output proving a Git change was synced (label, annotation, or image tag change visible in cluster)
```
kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2
```
5. Answer: "What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?"
```
If someone manually runs kubectl edit on a resource managed by ArgoCD, ArgoCD will detect the configuration drift. If the Application has automated sync enabled (--sync-policy automated), ArgoCD will automatically revert the manual change to match the desired state defined in Git (within the next sync cycle, typically 3 minutes). If automated sync is not enabled, the resource will be marked as OutOfSync in the ArgoCD UI and CLI, and it will remain changed until a manual sync is triggered (argocd app sync). In both cases, the manual change is not permanent and will be overwritten by ArgoCD to restore the Git-defined state
```
## Task 2
**Paste into `submissions/lab5.md`:**
- `argocd app get` showing Degraded after bad deploy
```
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/viadimirsoiovev/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (abe4cf5)
Health Status:      Degraded

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH       HOOK  MESSAGE
       Service     default    events    Synced  Healthy            service/events unchanged
       Service     default    redis     Synced  Healthy            service/redis unchanged
       Service     default    postgres  Synced  Healthy            service/postgres unchanged
       Service     default    payments  Synced  Healthy            service/payments unchanged
       Service     default    gateway   Synced  Healthy            service/gateway unchanged
apps   Deployment  default    payments  Synced  Degraded           deployment.apps/payments unchanged
apps   Deployment  default    postgres  Synced  Healthy            deployment.apps/postgres unchanged
apps   Deployment  default    events    Synced  Degraded           deployment.apps/events unchanged
apps   Deployment  default    redis     Synced  Healthy            deployment.apps/redis unchanged
apps   Deployment  default    gateway   Synced  Progressing        deployment.apps/gateway configured
```
- `kubectl get pods` showing ImagePullBackOff
```
NAME                        READY   STATUS             RESTARTS   AGE
events-5b75c4b498-bmlg8     1/1     Running            0          8h
events-687cf78b79-dhkqp     0/1     ImagePullBackOff   0          40m
gateway-54b9b8b8c7-tb6ml    1/1     Running            0          7h51m
gateway-67d8bff7bf-rnbb9    0/1     ErrImagePull       0          10s
payments-6899db845-2cp95    0/1     ImagePullBackOff   0          40m
payments-c67d96686-x2zpc    1/1     Running            0          8h
postgres-657d459d58-422pn   1/1     Running            0          8h
redis-64d9775897-sjtrf      1/1     Running            0          7h44m
```
- `git log --oneline -3` showing the deploy + revert commits
```
abe4cf5 (HEAD -> main, origin/main, origin/HEAD) Merge pull request #12 from vIadimirsoIovev/feature/lab5
516a159 (origin/feature/lab5, feature/lab5) feat: deploy new gateway version
7a119ff Merge pull request #11 from vIadimirsoIovev/feature/lab5

28a9126 (HEAD -> main, origin/main) Revert "feat: deploy new gateway version"
abe4cf5 Merge pull request #12 from vIadimirsoIovev/feature/lab5
516a159 feat: deploy new gateway version
```
- `argocd app get` showing Healthy after revert
```
user@MacBook-Air sre-intro % argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/viadimirsoiovev/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (110063e)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments configured
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events configured
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
```
- Answer: "How long from `git revert` + push to pods being healthy again?"
about 2 mins
