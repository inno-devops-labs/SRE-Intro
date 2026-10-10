# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline and ArgoCD Setup (6 pts)

### 1.1 GitHub Actions CI Pipeline
Created `.github/workflows/ci.yml` from scratch. The pipeline triggers on push to `main`, builds Docker images for all services and pushes them to GitHub Container Registry (`ghcr.io`).

**Link to successful workflow run:**  
`https://github.com/foidkiller/SRE-Intro/actions/runs/...` *(insert actual link)*

### 1.2 Published Images
```bash
gh api user/packages?package_type=container --jq '.[].name'
```
Output:
```text
quickticket-gateway
quickticket-events
quickticket-notifications
quickticket-payments
```
All four images were successfully built and pushed.
###1.3 Updated Kubernetes Manifests
Updated all files in k8s/ directory:

Changed image source to ghcr.io/foidkiller/...:${{ github.sha }}
Set imagePullPolicy: Always
Added imagePullSecrets

Example (gateway.yaml):
```YAML
image: ghcr.io/foidkiller/quickticket-gateway:cd0f334ab4a478bdf55b86b2dd3c95c723e07140
imagePullPolicy: Always
```
###1.4 ArgoCD Setup
```Bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```
Created ArgoCD Application:
```Bash
argocd app create quickticket \
  --repo https://github.com/foidkiller/SRE-Intro.git \
  --path k8s \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace default \
  --sync-policy automated \
  --auto-prune \
  --self-heal \
  --upsert
```
Current Application Status:
```Bash
argocd app get quickticket
```
Output:
```text
Name:            quickticket
Project:         default
Server:          https://kubernetes.default.svc
Namespace:       default
URL:             https://localhost:8443/applications/quickticket
Source:
- Repo:          https://github.com/foidkiller/SRE-Intro.git
  Path:          k8s
SyncWindow:      Sync Allowed
Sync Policy:     Automated (Prune)
Sync Status:     Synced
Health Status:   Healthy
```
1.5 GitOps Loop Verification
Made a change in k8s/gateway.yaml (added label version: v2), committed and pushed. ArgoCD automatically synchronized the change.
Verification:
```Bash
kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
```
Result: v2
Answer: If someone manually edits a resource managed by ArgoCD (kubectl edit), ArgoCD will detect the drift and automatically revert the change back to the state defined in Git (due to self-healing).
## Task 2 — Rollback via GitOps (4 pts)
### 2.1 Broken Deployment
Intentionally changed the gateway image tag to a non-existent version and pushed the change.
Observed:
```Bash
argocd app get quickticket
kubectl get pods -l app=gateway

Sync Status: Unknown / Degraded
Pods status: ImagePullBackOff
```
###2.2 Rollback
```Bash
git revert HEAD --no-edit
git push origin main
```
ArgoCD automatically synced the previous working version.
Recovery time: ~1 minute 45 seconds after push.
Git history:
```Bash
git log --oneline -3
```
##Bonus Task — Automated Image Tag Update (2 pts)
Extended the CI workflow to automatically update image tags in all k8s/*.yaml files after building, commit the changes, and push them. Added protection against infinite CI trigger loops.
The complete GitOps cycle is now fully automated:

Developer pushes code
CI builds new images
CI updates manifests with new SHA tags
CI pushes updated manifests
ArgoCD automatically deploys the new version

Conclusion
In this laboratory I implemented a complete modern CI/CD + GitOps pipeline for the QuickTicket project.
Key achievements:

Automated container image building and publishing via GitHub Actions
Full declarative deployment using ArgoCD
Automatic synchronization and self-healing
Safe rollback through Git
Automated manifest update (bonus)

This setup significantly improves deployment reliability, traceability, and speed compared to manual methods.
