# Lab 5 GitOps

Prerequisite: a running Lab 4 k3d cluster and the `feature/lab5` branch pushed
to this fork. Run `scripts/bootstrap-lab5.sh` from any directory. ArgoCD is
pinned to v3.3.9; the Application lives outside `k8s/` to avoid self-management.
`k8s/kustomization.yaml` selects only the five application manifests, excluding
the optional Helm chart from Lab 4.

The workflow runs on application/workflow changes on main and feature/lab5,
and builds both amd64 and arm64 images. Pull requests build without publishing
or writing to the repository. Only a successful build of all three services
can advance their immutable full-SHA tags. A path filter excludes generated
manifest commits; GITHUB_TOKEN pushes also do not recursively trigger CI.
A source-diff check prevents an older build overwriting a newer application;
push never uses `--force`.

The three packages in this fork are public, so the verified deployment needs
no pull secret. For a private fork/package, create `ghcr-secret` locally using
a classic PAT with read:packages and add `imagePullSecrets` to each workload.
Never commit the secret. The package owner is the lowercase repository owner,
not the person triggering the workflow.

ArgoCD tracks feature/lab5 to demonstrate the entire loop before PR merge.
After merging, set `spec.source.targetRevision` to main. Automated sync,
pruning and self-healing are enabled. A local `kubectl edit` is therefore
reverted to Git; permanent changes and rollbacks must be committed.

To observe the UI, run `kubectl port-forward -n argocd svc/argocd-server 8443:443`.
Retrieve the initial admin password locally from `argocd-initial-admin-secret`.
For the CLI, use the v3.3.9 binary matching your OS/CPU, then `argocd login`.

Rollback exercise (only in this training cluster): change only the gateway
image tag to `does-not-exist`, commit and push; observe the failed pod and
ArgoCD health. Then `git revert <bad-commit> --no-edit` and push. Capture time
before revert and after ArgoCD is Synced/Healthy. The gateway progress deadline
is 60s for the exercise; RollingUpdate keeps the previous ready pod serving.
Use a larger deadline appropriate to startup time in a production deployment.
