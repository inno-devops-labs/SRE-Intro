# Lab 5  CI/CD & GitOps

## Task 1 CI Pipeline + ArgoCD

I created GitHub Actions CI workflow (`.github/workflows/ci.yml`).

The workflow finished successfully:

* build Docker images
* push images to ghcr.io

I installed ArgoCD and created Application `quickticket`.

I tested GitOps workflow.

When I push changes to Git repository, ArgoCD automatically deploy new version.

## Task 2 Rollback via GitOps

### 1. Deploy bad version

I changed image tag in `k8s/gateway.yaml` to wrong tag.

After git push, ArgoCD tried to sync application.

Gateway pod went to `ImagePullBackOff` state.

### 2. Rollback

```bash
git revert HEAD --no-edit
git push origin main
```

ArgoCD automatically rollback changes.

Application returned to Healthy status.

Recovery time was about 1 to 2 minutes after git push.

## Bonus Task

I did not do bonus task because I had some problems with ArgoCD path configuration.

But I understand the idea of automatic image tag updates.

## Final

In this lab I:

* setup CI/CD pipeline with GitHub Actions
* installed ArgoCD
* used GitOps workflow
* tested rollback with git revert

This lab helped me understand how modern deployment and rollback work in DevOps and SRE.
