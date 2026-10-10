# Lab 5 — CI/CD & GitOps

## Task 1 — CI Pipeline + ArgoCD Setup

### CI Workflow

CI pipeline builds and pushes 3 images to GHCR:

- quickticket-gateway
- quickticket-events
- quickticket-payments

Each image is tagged with commit SHA.

Link to workflow run:
https://github.com/slickip/SRE-Intro/actions/runs/27878116385

### Verify images are pushed

```bash
gh api user/packages?package_type=container --jq '.[].name'
```

Output:
```
quickticket-gateway
quickticket-events
quickticket-payments
```

### Update K8s manifests to use registry images
Kubernetes manifests were updated to use container images stored in GitHub Container Registry (GHCR) instead of local images.

### Changes made:

- Replaced local Docker images (`quickticket-*`) with GHCR images
- Each service now uses an immutable image tag based on the Git commit SHA
- Updated `imagePullPolicy` to `Always` to ensure the cluster pulls the latest image version
- Added `imagePullSecrets` for services that use private images from GHCR

### Example (before):

```yaml
image: quickticket-gateway:v1
imagePullPolicy: Never
```
### Example (after):
```
image: ghcr.io/slickip/quickticket-gateway:<COMMIT_SHA>
imagePullPolicy: Always
```
### Image pull secret configuration:
```
    spec:
      imagePullSecrets:
        - name: ghcr-secret
```


### Output of argocd app get quickticket showing Synced + Healthy

Command:
```
argocd app get quickticket
```

Output:
```
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/slickip/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (1e11199)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments configured
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events configured
```

### Git Change Sync Proof

Added `version: v2` label to the gateway Deployment pod template and committed the change to the main branch.

ArgoCD detected the Git change and automatically synced it to the Kubernetes cluster.

ArgoCD sync status:
```
apps Deployment default gateway Synced Healthy deployment.apps/gateway configured
```

Verification in the cluster:

```bash
kubectl get deployment gateway -o jsonpath='{.spec.template.metadata.labels.version}'
```

Output:
```
v2
```
This confirms that the Git change was successfully propagated to the Kubernetes cluster through ArgoCD without any manual intervention (kubectl apply or kubectl edit)

### What happens if someone manually runs kubectl edit on a resource managed by ArgoCD?

If someone manually modifies a Kubernetes resource using `kubectl edit`, it creates a configuration drift between the live state in the cluster and the desired state stored in Git. ArgoCD continuously monitors the cluster state and compares it with the Git repository (source of truth). When a difference is detected, the application is marked as `OutOfSync`. If automated sync is enabled, ArgoCD will automatically revert the manual changes and restore the desired state defined in Git during the next reconciliation cycle. If manual sync is used, the application will remain `OutOfSync` until a user manually triggers synchronization. This mechanism ensures that Git remains the single source of truth and prevents manual changes from permanently diverging from the declared infrastructure state.


## Task 2 — Rollback via GitOps

### argocd app get showing Degraded after bad deploy

In file k8s/gateway.yml change 
```
image: ghcr.io/slickip/quickticket-gateway:36df04e3b25e36bd52dc67866240175e4f8cf26b
```
to this

```
image: ghcr.io/slickip/quickticket-gateway:does-not-exist
```

Command:
```
argocd app get quickticket
```

Output:
```
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/slickip/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (daef98a)
Health Status:      Progressing

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH       HOOK  MESSAGE
       Service     default    redis     Synced  Healthy            service/redis unchanged
       Service     default    postgres  Synced  Healthy            service/postgres unchanged
       Service     default    events    Synced  Healthy            service/events unchanged
       Service     default    payments  Synced  Healthy            service/payments unchanged
       Service     default    gateway   Synced  Healthy            service/gateway unchanged
apps   Deployment  default    payments  Synced  Healthy            deployment.apps/payments unchanged
apps   Deployment  default    events    Synced  Healthy            deployment.apps/events unchanged
apps   Deployment  default    postgres  Synced  Healthy            deployment.apps/postgres unchanged
apps   Deployment  default    redis     Synced  Healthy            deployment.apps/redis unchanged
apps   Deployment  default    gateway   Synced  Progressing        deployment.apps/gateway configured
```

Health in status `Progressing` and it never become Ready. The new gateway pod stayed in ImagePullBackOff because Kubernetes could not pull the non-existent image tag

### kubectl get pods showing ImagePullBackOff

Command:
```
kubectl get pods
```
Output:
```
NAME                       READY   STATUS             RESTARTS       AGE
events-75c987f574-nh62g    1/1     Running            0              25m
gateway-799b68db49-q8mk8   1/1     Running            23 (25m ago)   18h
gateway-dcb49cdd-6h89s     0/1     ImagePullBackOff   0              17s
payments-bf56bdbd6-gb88m   1/1     Running            0              25m
postgres-7c7ffc4b-7mfk6    1/1     Running            1 (26m ago)    20h
redis-c46d5dffc-kgmhk      1/1     Running            1 (26m ago)    20h
```

### Rollback via git revert

```
git revert HEAD --no-edit
git push origin main
```

### git log --oneline -3 showing the deploy + revert commits

Command:
```
git log --oneline -3 
```

Output:
```
fed6f5b (HEAD -> main, origin/main, origin/HEAD) Revert "feat: deploy new gateway version"
daef98a feat: deploy new gateway version
1e11199 use correct container images for events and payment
```

### argocd app get quickticket


```
argocd app get quickticket
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/slickip/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (fed6f5b)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured
```

Command:
```
kubectl get pods
```
Output:
```
events-75c987f574-nh62g    1/1     Running   0              37m
gateway-799b68db49-q8mk8   1/1     Running   23 (37m ago)   18h
payments-bf56bdbd6-gb88m   1/1     Running   0              37m
postgres-7c7ffc4b-7mfk6    1/1     Running   1 (38m ago)    20h
redis-c46d5dffc-kgmhk      1/1     Running   1 (38m ago)    20h
```

### How long from git revert + push to pods being healthy again?

The application returned to Healthy state in about 1–2 minutes after `git revert` and `git push`. ArgoCD detected the revert commit, synchronized the manifests, and Kubernetes replaced the broken gateway pod with a healthy one

## Bonus Task — Automated Image Tag Update

Added a step to my CI workflow after pushing the images

```
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    if: "!startsWith(github.event.head_commit.message, 'ci:')"
    permissions:
      contents: write
      packages: write

    steps:
      - uses: actions/checkout@v4

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build gateway
        run: |
          docker build -t ghcr.io/${{ github.actor }}/quickticket-gateway:${{ github.sha }} ./app/gateway
          docker push ghcr.io/${{ github.actor }}/quickticket-gateway:${{ github.sha }}

      - name: Build events
        run: |
          docker build -t ghcr.io/${{ github.actor }}/quickticket-events:${{ github.sha }} ./app/events
          docker push ghcr.io/${{ github.actor }}/quickticket-events:${{ github.sha }}

      - name: Build payments
        run: |
          docker build -t ghcr.io/${{ github.actor }}/quickticket-payments:${{ github.sha }} ./app/payments
          docker push ghcr.io/${{ github.actor }}/quickticket-payments:${{ github.sha }}

      - name: Update image tags in manifests
        run: |
          SHA=${{ github.sha }}
          sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/${{ github.actor }}/quickticket-gateway:${SHA}|" k8s/gateway.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-events:.*|image: ghcr.io/${{ github.actor }}/quickticket-events:${SHA}|" k8s/events.yaml
          sed -i "s|image: ghcr.io/.*/quickticket-payments:.*|image: ghcr.io/${{ github.actor }}/quickticket-payments:${SHA}|" k8s/payments.yaml

      - name: Commit and push manifest update
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@github.com"
          git add k8s/
          git diff --cached --quiet || git commit -m "ci: update image tags to ${{ github.sha }}"
          git push
```

Commands:
```
git pull origin main
git log --oneline -5
```
Output:
```
d2ecf3c (HEAD -> main, origin/main, origin/HEAD) ci: update image tags to 05eaf17eab81f8e932ef2cfb331d6879afb71da9
05eaf17 feat: add automated image tag update
c744f8d feat: add automated image tag update
fed6f5b Revert "feat: deploy new gateway version"
daef98a feat: deploy new gateway version
```

### Checks updated image tags

Command:
```
grep "image: ghcr.io" k8s/*.yaml  
```

Output:
```
k8s/events.yaml:          image: ghcr.io/slickip/quickticket-events:05eaf17eab81f8e932ef2cfb331d6879afb71da9
k8s/gateway.yaml:          image: ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9
k8s/payments.yaml:          image: ghcr.io/slickip/quickticket-payments:05eaf17eab81f8e932ef2cfb331d6879afb71da9
```

### ArgoCD synchronized the automatically updated commit

After the CI workflow updated the image tags in the Kubernetes manifests and pushed the manifest commit to GitHub, ArgoCD automatically detected the new commit and synchronized the application.

Command:

```bash
argocd app get quickticket
```

Output:

```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://localhost:8443/applications/quickticket
Source:
- Repo:             https://github.com/slickip/SRE-Intro.git
  Target:           
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to  (d2ecf3c)
Health Status:      Healthy

GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE
       Service     default    redis     Synced  Healthy        service/redis unchanged
       Service     default    payments  Synced  Healthy        service/payments unchanged
       Service     default    events    Synced  Healthy        service/events unchanged
       Service     default    postgres  Synced  Healthy        service/postgres unchanged
       Service     default    gateway   Synced  Healthy        service/gateway unchanged
apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged
apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway unchanged
apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged
apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged
apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged

```

This confirms that ArgoCD detected the Git commit automatically and synchronized the Kubernetes cluster without any manual manifest updates