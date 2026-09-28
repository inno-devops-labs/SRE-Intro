# Lab 5 — CI/CD & GitOps

Test date: 28 September 2026. The existing QuickTicket k3d cluster was used with Argo CD v3.5.3.

## Task 1 — CI and ArgoCD

[CI workflow](../.github/workflows/ci.yml) builds gateway, events and payments, pushes SHA-tagged images to GHCR, and commits updated image tags. It runs on pushes to the fork's `main` that change application code or the workflow.

Successful GitHub Actions runs:

- [Initial build and image publication](https://github.com/nikitadev-work/SRE-Intro/actions/runs/36395018724)
- [API version change and automatic tag update](https://github.com/nikitadev-work/SRE-Intro/actions/runs/36395386978)

Package names from `gh api user/packages?package_type=container`:

```text
quickticket-gateway
quickticket-events
quickticket-payments
```

The application Deployments use GHCR images with `imagePullPolicy: Always` and `ghcr-secret`. The pull secret was created in Kubernetes using a credential with `read:packages`; no credentials are stored in Git.

The [ArgoCD Application](../argocd/application.yaml) watches `main`, path `k8s`, and deploys to the `default` namespace. Automated sync and self-healing are enabled.

`argocd app get quickticket` (excerpt; CLI uses Kubernetes credentials through `--core`):

```text
Name:               argocd/quickticket
Project:            default
Server:             https://kubernetes.default.svc
Namespace:          default
Source:
- Repo:             https://github.com/nikitadev-work/SRE-Intro.git
  Target:           main
  Path:             k8s
SyncWindow:         Sync Allowed
Sync Policy:        Automated
Sync Status:        Synced to main (6b897ee)
Health Status:      Healthy
```

A Git commit added the gateway Deployment label `version: v2`. The live value of `.metadata.labels.version` was:

```text
v2
```

**What happens after `kubectl edit`?** ArgoCD detects a difference from Git and marks the application OutOfSync. With self-healing enabled, it restores the Git configuration automatically. Without self-healing, a live-only edit is not automatically reverted until a sync occurs. Lasting changes should be committed to Git.

## Task 2 — Rollback through Git

The gateway image tag was changed to `does-not-exist` and pushed. `progressDeadlineSeconds: 60` makes the failed rollout become Degraded after about a minute.

ArgoCD after the bad deployment:

```text
Name:               argocd/quickticket
Sync Status:        Synced to main (378695d)
Health Status:      Degraded
```

Pods during the failure:

```text
NAME                        READY   STATUS             RESTARTS   AGE
events-f64d95bb6-m8sct      1/1     Running            0          2m20s
gateway-6c5d764955-p225p    0/1     ImagePullBackOff   0          88s
gateway-85f55d6c6c-g4zcw    1/1     Running            0          2m20s
payments-cd749857b-t5njd    1/1     Running            0          2m20s
postgres-78489d7f5f-cxs7x   1/1     Running            0          7d
redis-6fcfb5475d-2wthw      1/1     Running            0          7d
```

The new gateway pod could not pull the missing image. The previous healthy pod kept serving during the rolling update.

The change was undone with `git revert`, then pushed. Commit history (`git log --oneline -3`):

```text
579fb19 Revert "feat: deploy gateway with a non-existent image tag"
378695d feat: deploy gateway with a non-existent image tag
053bdb8 ci: update image tags to 85be3a96489cd32d3f045e706510161bd446da04
```

ArgoCD after the revert:

```text
Name:               argocd/quickticket
Sync Status:        Synced to main (579fb19)
Health Status:      Healthy
```

Recovery took **7.21 seconds from starting `git revert`**, or **5.08 seconds after the push completed**, until ArgoCD was Synced/Healthy and only the ready gateway pod remained. After the bad-tag push and the revert push, a repository refresh was requested; ArgoCD performed the sync automatically. No workload rollback was performed with kubectl.

## Bonus — Automatic image tag updates

The gateway API version was changed from `1.0.0` to `1.0.1`. CI built all three images and pushed a separate manifest-update commit:

```text
053bdb8 ci: update image tags to 85be3a96489cd32d3f045e706510161bd446da04
85be3a9 feat: bump gateway API version to 1.0.1
```

The workflow skips commits starting with `ci:` and only rebuilds for changes under `app/` or the workflow file. Manifest-update commits therefore do not cause a build loop.

For this test, no manual sync or repository refresh was requested. ArgoCD detected the CI commit through its normal polling:

```text
Name:               argocd/quickticket
Sync Status:        Synced to main (053bdb8)
Health Status:      Healthy
```

Live Deployment images after the automatic sync:

```text
NAME       IMAGE
gateway    ghcr.io/nikitadev-work/quickticket-gateway:85be3a96489cd32d3f045e706510161bd446da04
events     ghcr.io/nikitadev-work/quickticket-events:85be3a96489cd32d3f045e706510161bd446da04
payments   ghcr.io/nikitadev-work/quickticket-payments:85be3a96489cd32d3f045e706510161bd446da04
```

Through port-forward, `/openapi.json` reported API version `1.0.1`, `/events` returned all five events, and `/health` returned `healthy`. The cluster was left on the working image tags after the rollback test.
