# Lab 5 — CI/CD & GitOps
## Proof of work by Viktoriya Yurina

### Task 1 — CI Pipeline + ArgoCD Setup
#### Link to your GitHub Actions run (green check)
https://github.com/shersh-is/SRE-Intro/actions/runs/27945247999 \
https://github.com/shersh-is/SRE-Intro/actions/runs/27948739563 \
https://github.com/shersh-is/SRE-Intro/actions/runs/27949527247 \
etc...

#### Output of gh api user/packages?package_type=container showing pushed images
```
┌──(shersh㉿kali)-[~]
└─$ gh api user/packages?package_type=container --jq '.[].name'
quickticket-gateway
quickticket-events
quickticket-payments
```

#### Output of argocd app get quickticket showing Synced + Healthy
```
┌──(shersh㉿kali)-[~/SRE-Intro]
└─$ argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/shersh-is/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (8204d58)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    payments  Synced  Healthy        service/payments configured
       Service     default    gateway   Synced  Healthy        service/gateway configured
       Service     default    events    Synced  Healthy        service/events configured
       Service     default    postgres  Synced  Healthy        service/postgres configured
       Service     default    redis     Synced  Healthy        service/redis configured
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis configured
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres configured
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments configured
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events configured
```

#### Output proving a Git change was synced (label, annotation, or image tag change visible in cluster)
```
┌──(shersh㉿kali)-[~/SRE-Intro]
└─$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2
```      
#### Answer: "What happens if someone manually runs kubectl edit on a resource managed by ArgoCD?"
If someone manually runs kubectl edit on a resource managed by ArgoCD, the change creates a drift between the live Kubernetes state and the desired state stored in Git. ArgoCD detects this difference during its next reconciliation cycle and marks the application as OutOfSync. If automatic sync is enabled, ArgoCD will automatically revert the manual changes and restore the configuration from Git. If automatic sync is disabled, the application remains OutOfSync until a manual sync is performed.

### Task 2 — Rollback via GitOps
#### argocd app get showing Degraded after bad deploy
```
┌──(shersh㉿kali)-[~/SRE-Intro]
└─$ argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/shersh-is/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (925d838)
Health Status:      Progressing

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH       HOOK  MESSAGE
       Service     default    events    Synced  Healthy            service/events unchanged
       Service     default    redis     Synced  Healthy            service/redis unchanged
       Service     default    gateway   Synced  Healthy            service/gateway unchanged
       Service     default    postgres  Synced  Healthy            service/postgres unchanged
       Service     default    payments  Synced  Healthy            service/payments unchanged
apps   Deployment  default    postgres  Synced  Healthy            deployment.apps/postgres unchanged
apps   Deployment  default    events    Synced  Healthy            deployment.apps/events unchanged
apps   Deployment  default    payments  Synced  Healthy            deployment.apps/payments unchanged
apps   Deployment  default    redis     Synced  Healthy            deployment.apps/redis unchanged
apps   Deployment  default    gateway   Synced  Progressing        deployment.apps/gateway configured
```

#### kubectl get pods showing ImagePullBackOff
```
┌──(shersh㉿kali)-[~/SRE-Intro]
└─$ kubectl get pods   
NAME                       READY   STATUS         RESTARTS   AGE
events-74d69c9484-z9r2j    1/1     Running        0          25m
gateway-5c7f86dbb5-4dsnc   0/1     ErrImagePull   0          3m27s
gateway-69f6dcf7c-qhl6q    1/1     Running        0          21m
payments-9dd587d-8j2xt     1/1     Running        0          25m
postgres-7c7ffc4b-4jgq4    1/1     Running        0          5d1h
redis-c46d5dffc-5tp8d      1/1     Running        0          5d1h
```

#### git log --oneline -3 showing the deploy + revert commits
```
┌──(shersh㉿kali)-[~/SRE-Intro]
└─$ git log --oneline -3
9f4c144 (HEAD -> main, origin/main, origin/HEAD) Revert "feat: deploy new (bad) gateway version"
925d838 feat: deploy new (bad) gateway version
da24243 feat: add version label to gateway
```

#### argocd app get showing Healthy after revert
```
┌──(shersh㉿kali)-[~/SRE-Intro]
└─$ argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/shersh-is/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (9f4c144)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured

┌──(shersh㉿kali)-[~/SRE-Intro]
└─$ kubectl get pods          
NAME                      READY   STATUS    RESTARTS   AGE
events-74d69c9484-z9r2j   1/1     Running   0          27m
gateway-69f6dcf7c-qhl6q   1/1     Running   0          23m
payments-9dd587d-8j2xt    1/1     Running   0          27m
postgres-7c7ffc4b-4jgq4   1/1     Running   0          5d1h
redis-c46d5dffc-5tp8d     1/1     Running   0          5d1h
```

#### Answer: "How long from git revert + push to pods being healthy again?"
It took about 7 minutes from git revert and git push until all pods became healthy again. This time included GitHub Actions building and pushing new images, ArgoCD detecting the Git change, syncing the application, and Kubernetes rolling out the updated pods.


### Bonus Task — Automated Image Tag Update
#### Updated workflow file showing auto-tag update
```
┌──(shersh㉿kali)-[~/SRE-Intro]
└─$ git diff                    
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
index 8c2db3f..c74f6b6 100644
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -7,6 +7,7 @@ on:
 
 jobs:
   build:
+    if: "!startsWith(github.event.head_commit.message, 'ci:')"
     runs-on: ubuntu-latest
 
     permissions:
@@ -36,3 +37,18 @@ jobs:
         run: |
           docker build -t ghcr.io/shersh-is/quickticket-payments:${{ github.sha }} ./app/payments
           docker push ghcr.io/shersh-is/quickticket-payments:${{ github.sha }}
+
+      - name: Update image tags in manifests
+        run: |
+          SHA=${{ github.sha }}
+          sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/${{ github.actor }}/quickticket-gateway:${SHA}|" k8s/gateway.yml
+          sed -i "s|image: ghcr.io/.*/quickticket-events:.*|image: ghcr.io/${{ github.actor }}/quickticket-events:${SHA}|" k8s/events.yml
+          sed -i "s|image: ghcr.io/.*/quickticket-payments:.*|image: ghcr.io/${{ github.actor }}/quickticket-payments:${SHA}|" k8s/payments.yml
+
+      - name: Commit and push manifest update
+        run: |
+          git config user.name "github-actions"
+          git config user.email "github-actions@github.com"
+          git add k8s/
+          git diff --cached --quiet || git commit -m "ci: update image tags to ${{ github.sha }}"
+          git push
```

#### Git log showing: code commit → CI tag-update commit
```
┌──(shersh㉿kali)-[~/SRE-Intro]
└─$ git log --oneline -3
1b8fcc3 (HEAD -> main, origin/main, origin/HEAD) ci: edit CI pipeline for QuickTicket
9f4c144 Revert "feat: deploy new (bad) gateway version"
925d838 feat: deploy new (bad) gateway version
```

#### ArgoCD syncing the auto-updated tag without manual intervention
```
┌──(shersh㉿kali)-[~/SRE-Intro]
└─$ argocd app get quickticket                          
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/shersh-is/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (1b8fcc3)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
```
