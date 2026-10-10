# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

1. Link to the GitHub Actions run (green check):

https://github.com/Tulup-404/SRE-Intro/commit/fa652c94be12cc4516e0b9973e4599014282aa09

2. Output of `gh api user/packages?package_type=container --jq '.[].name'` showing pushed images: 

```bash
PS C:\Users\timab\OneDrive\Документы\B24-SD-01\Sum26\SRE\SRE-Intro> gh api user/packages?package_type=container --jq '.[].name'
quickticket-gateway
quickticket-events
quickticket-payments
```

3. Output of `argocd app get quickticket` showing Synced + Healthy:

```bash
PS C:\Users\timab\OneDrive\Документы\B24-SD-01\Sum26\SRE\SRE-Intro> argocd app sync quickticket                                                                                     
TIMESTAMP                  GROUP        KIND   NAMESPACE                  NAME    STATUS   HEALTH        HOOK  MESSAGE
2026-06-26T19:33:40+03:00   apps  Deployment     default                 redis    Synced  Healthy
2026-06-26T19:33:40+03:00            Service     default               gateway    Synced  Healthy
2026-06-26T19:33:40+03:00            Service     default              postgres    Synced  Healthy
2026-06-26T19:33:40+03:00   apps  Deployment     default               gateway    Synced  Healthy
2026-06-26T19:33:40+03:00   apps  Deployment     default              payments    Synced  Healthy
2026-06-26T19:33:40+03:00            Service     default                events    Synced  Healthy
2026-06-26T19:33:40+03:00            Service     default              payments    Synced  Healthy
2026-06-26T19:33:40+03:00            Service     default                 redis    Synced  Healthy
2026-06-26T19:33:40+03:00   apps  Deployment     default                events    Synced  Healthy
2026-06-26T19:33:40+03:00   apps  Deployment     default              postgres    Synced  Healthy
2026-06-26T19:33:40+03:00            Service     default                events    Synced  Healthy              service/events unchanged
2026-06-26T19:33:40+03:00            Service     default              postgres    Synced  Healthy              service/postgres unchanged
2026-06-26T19:33:40+03:00   apps  Deployment     default              postgres    Synced  Healthy              deployment.apps/postgres unchanged
2026-06-26T19:33:40+03:00   apps  Deployment     default              payments    Synced  Healthy              deployment.apps/payments unchanged
2026-06-26T19:33:40+03:00   apps  Deployment     default                events    Synced  Healthy              deployment.apps/events unchanged
2026-06-26T19:33:40+03:00            Service     default              payments    Synced  Healthy              service/payments unchanged
2026-06-26T19:33:40+03:00            Service     default                 redis    Synced  Healthy              service/redis unchanged
2026-06-26T19:33:40+03:00            Service     default               gateway    Synced  Healthy              service/gateway unchanged
2026-06-26T19:33:40+03:00   apps  Deployment     default               gateway    Synced  Healthy              deployment.apps/gateway unchanged
2026-06-26T19:33:40+03:00   apps  Deployment     default                 redis    Synced  Healthy              deployment.apps/redis unchanged

Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/Tulup-404/SRE-Intro.git
  Target:
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (ef3d15e)
Health Status:      Healthy

Operation:          Sync
Sync Revision:      ef3d15ea11b9c14bf33f05ac5fcdf26afcee5514
Phase:              Succeeded
Start:              2026-06-26 19:33:39 +0300 MSK
Finished:           2026-06-26 19:33:40 +0300 MSK
Duration:           1s
Message:            successfully synced (all tasks run)

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
```

4. Proof a Git change was synced to the cluster (version label on gateway):

```bash
PS C:\Users\timab\OneDrive\Документы\B24-SD-01\Sum26\SRE\SRE-Intro> kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2
```

5. **What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?**

ArgoCD continuously compares the actual cluster state against the desired state in Git.
A manual change made via `kubectl edit` introduces "drift": the application moves to the
**OutOfSync** status (Git and the cluster diverge). What happens next depends on the sync
policy:
- with `automated` **without** `selfHeal`, ArgoCD only reports OutOfSync but does not revert
  the change;
- with `automated` and `selfHeal: true`, ArgoCD automatically restores the resource to the
  state defined in Git, overwriting the manual change;
- with a manual policy, the change persists until the next `argocd app sync`, which also
  overwrites it with the values from Git.

Conclusion: Git is the single source of truth — manual edits are temporary and will be
overwritten.

---

## Task 2 — Rollback via GitOps

1. `argocd app get quickticket` showing Degraded after the bad deploy:

```bash
PS C:\Users\timab\OneDrive\Документы\B24-SD-01\Sum26\SRE\SRE-Intro> argocd app get quickticket                      
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/Tulup-404/SRE-Intro.git
  Target:
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (263a9c5)
Health Status:      Progressing

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH       HOOK  MESSAGE
       Service     default    events    Synced  Healthy            service/events unchanged
       Service     default    redis     Synced  Healthy            service/redis unchanged
       Service     default    gateway   Synced  Healthy            service/gateway unchanged
       Service     default    postgres  Synced  Healthy            service/postgres unchanged
       Service     default    payments  Synced  Healthy            service/payments unchanged
apps   Deployment  default    events    Synced  Healthy            deployment.apps/events unchanged
apps   Deployment  default    postgres  Synced  Healthy            deployment.apps/postgres unchanged
apps   Deployment  default    payments  Synced  Healthy            deployment.apps/payments unchanged
apps   Deployment  default    redis     Synced  Healthy            deployment.apps/redis unchanged
apps   Deployment  default    gateway   Synced  Progressing        deployment.apps/gateway configured
```

2. `kubectl get pods` showing ImagePullBackOff:

```bash
 kubectl get pods
NAME                        READY   STATUS             RESTARTS   AGE
events-74bbf8c78-5qc8b      1/1     Running            0          25m
gateway-75894cf8ff-7ppbs    1/1     Running            0          20m
gateway-857cdbbc4c-h69tj    0/1     ImagePullBackOff   0          4m10s
payments-6c7897fc4b-wknm6   1/1     Running            0          25m
postgres-76cd478b6b-lpgsp   1/1     Running            0          66m
redis-c46d5dffc-t4q5g       1/1     Running            0          66m
```

3. `git log --oneline -3` showing the deploy + revert commits:

```bash
PS C:\Users\timab\OneDrive\Документы\B24-SD-01\Sum26\SRE\SRE-Intro> git revert HEAD --no-edit
[main 5d9e3b3] Revert "feat: deploy new gateway version"
 Date: Fri Jun 26 19:59:40 2026 +0300
 1 file changed, 1 insertion(+), 1 deletion(-)
 ```

4. `argocd app get quickticket` showing Healthy after revert:

```bash
PS C:\Users\timab\OneDrive\Документы\B24-SD-01\Sum26\SRE\SRE-Intro> argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/Tulup-404/SRE-Intro.git
  Target:
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (5d9e3b3)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
```

5. **How long from `git revert` + push to pods being healthy again?**

The rollback was almost instantaneous (~ 5 seconds), because the readiness test + RollingUpdate strategy did not allow the beaten deployment to remove the old desktop altogether. The service was not idle for a second; 
   The "broken" pod never passed readiness, so the old one continued to serve traffic. With git revert, Kubernetes simply deleted the unfinished ReplicaSet without creating new pods or
pumping out the image — hence the instant recovery.
---

## Bonus Task — Automated Image Tag Update

1. Updated workflow file showing auto-tag update — см. `.github/workflows/ci.yml`
   (шаги `Update image tags in manifests` и `Commit and push manifest update`,
   плюс фильтр `if: "!startsWith(github.event.head_commit.message, 'ci:')"`
   для защиты от бесконечного цикла CI).

2. Git log showing: code commit → CI tag-update commit:

```bash
<!-- вставить вывод git log --oneline -5; ожидается:
<sha> ci: update image tags to <sha>   (коммит от github-actions)
<sha> <ваш код-коммит>
-->
```

3. ArgoCD syncing the auto-updated tag without manual intervention:

```bash
<!-- вставить argocd app get quickticket после авто-коммита тегов;
показать, что образы обновились до нового SHA без ручного sync -->
```
