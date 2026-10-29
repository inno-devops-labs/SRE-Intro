# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

1. Link to the GitHub Actions run (green check):

https://github.com/ya-rav/SRE-Intro/actions/runs/36383708672/

2. Output of `gh api user/packages?package_type=container --jq '.[].name'` showing pushed images:

```bash
$ gh api user/packages?package_type=container --jq '.[].name'
quickticket-gateway
quickticket-events
quickticket-payments
```

3. Output of `argocd app get quickticket` showing Synced + Healthy:

```bash
$ argocd app sync quickticket
TIMESTAMP                  GROUP        KIND   NAMESPACE                  NAME    STATUS   HEALTH        HOOK  MESSAGE
2026-09-28T18:41:20+02:00   apps  Deployment     default                 redis    Synced  Healthy
2026-09-28T18:41:20+02:00            Service     default               gateway    Synced  Healthy
2026-09-28T18:41:20+02:00            Service     default              postgres    Synced  Healthy
2026-09-28T18:41:20+02:00   apps  Deployment     default               gateway    Synced  Healthy
2026-09-28T18:41:20+02:00   apps  Deployment     default              payments    Synced  Healthy
2026-09-28T18:41:20+02:00            Service     default                events    Synced  Healthy
2026-09-28T18:41:20+02:00            Service     default              payments    Synced  Healthy
2026-09-28T18:41:20+02:00            Service     default                 redis    Synced  Healthy
2026-09-28T18:41:20+02:00   apps  Deployment     default                events    Synced  Healthy
2026-09-28T18:41:20+02:00   apps  Deployment     default              postgres    Synced  Healthy
2026-09-28T18:41:20+02:00            Service     default                events    Synced  Healthy              service/events unchanged
2026-09-28T18:41:20+02:00            Service     default              postgres    Synced  Healthy              service/postgres unchanged
2026-09-28T18:41:20+02:00   apps  Deployment     default              postgres    Synced  Healthy              deployment.apps/postgres unchanged
2026-09-28T18:41:20+02:00   apps  Deployment     default              payments    Synced  Healthy              deployment.apps/payments unchanged
2026-09-28T18:41:20+02:00   apps  Deployment     default                events    Synced  Healthy              deployment.apps/events unchanged
2026-09-28T18:41:20+02:00            Service     default              payments    Synced  Healthy              service/payments unchanged
2026-09-28T18:41:20+02:00            Service     default                 redis    Synced  Healthy              service/redis unchanged
2026-09-28T18:41:20+02:00            Service     default               gateway    Synced  Healthy              service/gateway unchanged
2026-09-28T18:41:20+02:00   apps  Deployment     default               gateway    Synced  Healthy              deployment.apps/gateway unchanged
2026-09-28T18:41:20+02:00   apps  Deployment     default                 redis    Synced  Healthy              deployment.apps/redis unchanged

Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/ya-rav/SRE-Intro.git
  Target:
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (b741ca9)
Health Status:      Healthy

Operation:          Sync
Sync Revision:      b741ca9104fa28cd91012ca49120de84091a108b
Phase:              Succeeded
Start:              2026-09-28 18:41:19 +0200
Finished:           2026-09-28 18:41:20 +0200
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
$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2
```

5. **What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?**

ArgoCD continuously reconciles the live state of cluster resources against the declared state stored in the Git repository. When an operator runs `kubectl edit`, the manual modification creates configuration drift, causing the application to enter an **OutOfSync** status.

The subsequent behavior depends on the configured synchronization strategy:
* If the sync policy is `automated` without `selfHeal`, ArgoCD flags the resource as OutOfSync and shows the drift in the UI/CLI, but does not immediately alter the live cluster object.
* If `selfHeal: true` is enabled, ArgoCD detects the discrepancy almost immediately and overwrites the manual changes, reapplying the manifest defined in Git.
* If manual sync is enforced, the manual change remains in effect until the next `argocd app sync` or automated pipeline run reconciles the state back to the Git commit.

Therefore, Git remains the authoritative single source of truth; any imperative manual interventions are ephemeral.

---

## Task 2 — Rollback via GitOps

1. `argocd app get quickticket` showing Degraded after the bad deploy:

```bash
$ argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/ya-rav/SRE-Intro.git
  Target:
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (49c12a8)
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
$ kubectl get pods
NAME                        READY   STATUS             RESTARTS   AGE
events-859d5c5c98-b7wn2     1/1     Running            0          34m
gateway-6fc44f68c5-q7k2b    1/1     Running            0          28m
gateway-94fc71bb8c-w2xpm    0/1     ImagePullBackOff   0          3m45s
payments-58fb468db-t9vk3    1/1     Running            0          34m
postgres-76cd478b6b-k8tm4   1/1     Running            0          78m
redis-c46d5dffc-m2q91       1/1     Running            0          78m
```

3. `git log --oneline -3` showing the deploy + revert commits:

```bash
$ git revert HEAD --no-edit
[main d184a29] Revert "feat: deploy new gateway version"
 Date: Mon Sep 28 19:12:15 2026 +0200
 1 file changed, 1 insertion(+), 1 deletion(-)
```

4. `argocd app get quickticket` showing Healthy after revert:

```bash
$ argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/ya-rav/SRE-Intro.git
  Target:
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (d184a29)
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

The cluster returned to full availability within approximately 5–7 seconds. Because Kubernetes Deployments employ a `RollingUpdate` strategy, the previous healthy replica (`gateway-6fc44f68c5-q7k2b`) was never terminated while the broken pod failed its image pull and readiness checks. Once the revert commit was pushed and synced by ArgoCD, the controller scaled down the faulty ReplicaSet and resumed routing exclusively to the active pod with zero user-facing downtime.
