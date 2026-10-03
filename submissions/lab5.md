# Lab 5 — CI/CD with GitHub Actions and Argo CD

## Overview

The goal of this laboratory work was to build a CI/CD pipeline for QuickTicket, publish container images to GitHub Container Registry (GHCR), deploy the application to Kubernetes, and manage the deployment using Argo CD.

## 5.1 — GitHub Actions CI workflow

A GitHub Actions workflow was created at `.github/workflows/ci.yml`.

The workflow is triggered by pushes to `main`. It checks out the repository, logs in to GHCR using `GITHUB_TOKEN`, builds the `gateway`, `events`, and `payments` images, and pushes them to GHCR.

Images are tagged with the Git commit SHA:

```text
ghcr.io/silviafedorovskaya/quickticket-gateway:${{ github.sha }}
ghcr.io/silviafedorovskaya/quickticket-events:${{ github.sha }}
ghcr.io/silviafedorovskaya/quickticket-payments:${{ github.sha }}
```

The workflow completed successfully. The verified CI commit was:

```text
2d1a803a0cf9dc3c7ec64bb2523e5bd385a53861
```

## 5.2 — Publishing images to GHCR

Three QuickTicket packages were successfully published to GitHub Container Registry:

```text
ghcr.io/silviafedorovskaya/quickticket-gateway
ghcr.io/silviafedorovskaya/quickticket-events
ghcr.io/silviafedorovskaya/quickticket-payments
```

The GitHub Packages page showed the three packages:

- `quickticket-gateway`
- `quickticket-events`
- `quickticket-payments`

## 5.3 — Kubernetes manifests and private GHCR images

The Kubernetes manifests were changed from local images to GHCR images.

For example, the gateway Deployment uses:

```yaml
image: ghcr.io/silviafedorovskaya/quickticket-gateway:2d1a803a0cf9dc3c7ec64bb2523e5bd385a53861
imagePullPolicy: IfNotPresent
```

The Events and Payments Deployments were updated in the same way.

A Kubernetes registry secret was created:

```text
ghcr-secret
```

with type:

```text
kubernetes.io/dockerconfigjson
```

The secret was added to all three Deployments:

```yaml
imagePullSecrets:
  - name: ghcr-secret
```

Initially, the gateway pod reported `ImagePullBackOff` because GHCR authentication returned `403 Forbidden`. The secret was recreated and the failed pod was deleted. Kubernetes then successfully pulled the image and started the gateway:

```text
gateway-9d7fff67b-5h65b    1/1    Running
```

## 5.4 — Installing Argo CD

The `argocd` namespace was created:

```bash
kubectl create namespace argocd
```

Argo CD was installed using:

```bash
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```

One `applicationsets.argoproj.io` CRD initially produced an annotation-size warning, but the required Argo CD components started successfully.

The final pod check showed all seven Argo CD components in `1/1 Running` state.

The Argo CD server was exposed locally with:

```bash
kubectl port-forward svc/argocd-server -n argocd 8080:443
```

and accessed at `https://localhost:8080`.

## 5.5 — Creating the Argo CD Application

An Argo CD Application named `quickticket` was created with:

| Parameter | Value |
|---|---|
| Application Name | `quickticket` |
| Project | `default` |
| Repository | `https://github.com/SilviaFedorovskaya/SRE-Intro.git` |
| Revision | `main` |
| Path | `k8s` |
| Destination | `in-cluster` |
| Namespace | `default` |
| Sync Policy | Manual |

The application was initially `OutOfSync`, as expected with manual synchronization. After synchronization, it became:

```text
Healthy
Synced
```

This confirmed that Argo CD successfully applied the Kubernetes manifests from Git.

## 5.6 — Git change and Argo CD synchronization

To demonstrate GitOps behavior, the gateway Deployment was changed from:

```yaml
replicas: 1
```

to:

```yaml
replicas: 2
```

The change was committed as:

```text
bef2ab7 Scale gateway to two replicas
```

and pushed to `main`.

After refreshing Argo CD, the application showed:

```text
Healthy
OutOfSync
```

This demonstrated that Argo CD detected a difference between the desired state in Git and the live Kubernetes state.

The application was then synchronized through Argo CD. The final status became:

```text
Healthy
Synced
```

The Kubernetes cluster was verified with:

```bash
kubectl get pods -n default | grep gateway
```

The result showed two running gateway replicas:

```text
gateway-9d7fff67b-5bvfw    1/1    Running
gateway-9d7fff67b-5h65b    1/1    Running
```

Therefore the change was successfully propagated through:

```text
GitHub → Argo CD → Kubernetes
```

## 5.7 — GitOps behavior and `kubectl edit`

Argo CD compares the desired state stored in Git with the live state in Kubernetes.

If a Deployment is manually changed with:

```bash
kubectl edit deployment gateway
```

the live Kubernetes state can temporarily differ from the state stored in Git. With manual synchronization enabled, Argo CD detects the difference and marks the Application as `OutOfSync`.

The manually edited value is not the source of truth. Git remains the desired state. When synchronization is performed, Argo CD applies the manifests from Git and restores the Kubernetes resources to the state described in the repository.

Therefore, a direct `kubectl edit` is not a persistent GitOps change. A permanent change should be made in Git and then synchronized by Argo CD.

## Evidence / verification summary

### GitHub Actions

- CI workflow created: `.github/workflows/ci.yml`
- Trigger: push to `main`
- Gateway image built and pushed
- Events image built and pushed
- Payments image built and pushed
- Workflow completed successfully

### GHCR

Published packages:

```text
quickticket-gateway
quickticket-events
quickticket-payments
```

### Kubernetes

The QuickTicket application was successfully deployed to the local k3d Kubernetes cluster.

The private gateway image was successfully pulled from GHCR using `ghcr-secret`.

### Argo CD

Application:

```text
quickticket
```

Final state:

```text
Healthy
Synced
```

### GitOps demonstration

Git change:

```text
replicas: 1 → replicas: 2
```

Commit:

```text
bef2ab7 Scale gateway to two replicas
```

Final Kubernetes state:

```text
2 gateway pods
1/1 Running
```

## Conclusion

The QuickTicket CI/CD pipeline was successfully configured.

GitHub Actions builds and publishes the three QuickTicket container images to GHCR. Kubernetes authenticates to the private registry using an image pull secret. Argo CD manages the Kubernetes manifests from the Git repository.

The GitOps workflow was verified by changing the gateway replica count in Git, observing the `OutOfSync` state in Argo CD, and then synchronizing the application. After synchronization, Kubernetes contained two running gateway replicas and Argo CD reported the application as `Healthy` and `Synced`.

The resulting deployment flow is:

```text
Git push
   ↓
GitHub Actions
   ↓
GHCR images
   ↓
Git repository / k8s manifests
   ↓
Argo CD
   ↓
Kubernetes
```
