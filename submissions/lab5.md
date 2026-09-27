# Lab 5 — CI/CD & GitOps

Date: 2026-09-28

## Task 1 — CI, GHCR, ArgoCD

The `CI` workflow builds `gateway`, `events`, and `payments` for `linux/amd64` and `linux/arm64`, then publishes images tagged with the full Git commit SHA. The first [successful Actions run](https://github.com/kujifined/SRE-Intro/actions/runs/36356308506) built commit `48ab70003fcc8d0b23a89bf048db43a03c11bde1`.

```text
$ gh api 'user/packages?package_type=container&per_page=100' --jq '.[].name'
devops-intro/quicknotes
quickticket-gateway
quickticket-events
quickticket-payments
```

Each QuickTicket package has the exact tag `48ab70003fcc8d0b23a89bf048db43a03c11bde1`, verified through the package versions API:

```text
quickticket-gateway  48ab70003fcc8d0b23a89bf048db43a03c11bde1
quickticket-events   48ab70003fcc8d0b23a89bf048db43a03c11bde1
quickticket-payments 48ab70003fcc8d0b23a89bf048db43a03c11bde1
```

The cluster has a `ghcr-secret` pull secret in `default`; its contents are not included here.

ArgoCD was installed in `argocd` and points its `quickticket` Application at the public fork's `k8s/` directory on `main`. Client-side installation initially hit Kubernetes' annotation size limit for the ApplicationSet CRD, so the same stable installation manifest was applied server-side. The ArgoCD CLI used the existing kubeconfig directly in core mode; no admin password was written to the repository.

After commit `f5b6aa5` updated the three manifests to GHCR SHA images, ArgoCD applied them and all three Deployments rolled out successfully:

```text
$ argocd --core app get quickticket  # relevant status lines
Sync Policy:        Automated
Sync Status:        Synced to  (f5b6aa5)
Health Status:      Healthy

$ kubectl rollout status deployment/gateway
deployment "gateway" successfully rolled out
$ kubectl rollout status deployment/events
deployment "events" successfully rolled out
$ kubectl rollout status deployment/payments
deployment "payments" successfully rolled out

$ curl -fsS http://127.0.0.1:3080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
$ curl -fsS http://127.0.0.1:3080/events | python3 -c 'import json,sys; print("events_count="+str(len(json.load(sys.stdin))))'
events_count=5
```

To prove Git → ArgoCD → Kubernetes synchronization, commit `5a6510a` added `metadata.labels.version: "v2"` to the gateway Deployment manifest. The live resource then reported:

```text
$ argocd --core app get quickticket  # relevant status lines
Sync Policy:        Automated
Sync Status:        Synced to  (5a6510a)
Health Status:      Healthy
$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
v2
```

If someone runs `kubectl edit` on a resource managed by ArgoCD, the live state drifts from Git and ArgoCD reports `OutOfSync`. With this Application's automated sync policy but no `selfHeal`, that live-only edit is not necessarily reverted immediately. A manual sync or a new Git revision restores the declared state; enabling `selfHeal` makes ArgoCD reconcile live-only drift automatically. Git remains the source of desired state.

## Task 2 — GitOps Rollback

Commit `4993019` changed the gateway image to `ghcr.io/kujifined/quickticket-gateway:does-not-exist`. It also set `progressDeadlineSeconds: 60` so the failed rollout reaches `Degraded` promptly. ArgoCD synchronized that revision. The previous healthy Pod stayed available during the rolling update, while the new Pod failed to pull:

```text
$ argocd --core app get quickticket  # relevant status lines
Sync Status:        Synced to  (4993019)
Health Status:      Degraded

$ kubectl get pods -l app=gateway
NAME                       READY   STATUS             RESTARTS   AGE
gateway-5fd549c854-x976b   1/1     Running            0          2m50s
gateway-8455448df5-j672t   0/1     ImagePullBackOff   0          70s
```

The Pod event explicitly reported `failed to resolve reference ... :does-not-exist: not found`. No live Kubernetes object was edited to repair it. I ran `git revert HEAD --no-edit` and pushed the resulting commit:

```text
$ git log --oneline -4
72b7484 Revert "feat(lab5): deploy unavailable gateway image"
4993019 feat(lab5): deploy unavailable gateway image
5a6510a feat(lab5): label gateway deployment v2
f5b6aa5 feat(lab5): deploy SHA-tagged GHCR images

$ argocd --core app get quickticket  # after revert
Sync Status:        Synced to  (72b7484)
Health Status:      Healthy

$ kubectl get pods -l app=gateway
NAME                       READY   STATUS    RESTARTS   AGE
gateway-5fd549c854-x976b   1/1     Running   0          3m26s
```

The revert push finished at `2026-09-27T22:52:10Z`. ArgoCD reached `Synced` and `Healthy` at `2026-09-27T22:52:33Z`: **23 seconds**. The existing healthy Pod remained available; this measures recovery of the desired deployment state, not recovery from a full service outage.

## Bonus — Automated Image Tag Update

Commit `8f68349` added the tag-update and commit steps to `.github/workflows/ci.yml`. The workflow grants `contents: write` and `packages: write`; its build job skips commit messages beginning with `ci:`. On the [successful bonus Actions run](https://github.com/kujifined/SRE-Intro/actions/runs/36356844163), both `Update Kubernetes image tags` and `Commit and push manifest update` completed successfully.

```text
$ git log --oneline -3
f016c33 ci: update image tags to 8f683490648451a95154ddcf2f91f749a53819ad
8f68349 feat(lab5): automatically update image tags after CI
72b7484 Revert "feat(lab5): deploy unavailable gateway image"

$ git show --stat --oneline f016c33
f016c33 ci: update image tags to 8f683490648451a95154ddcf2f91f749a53819ad
 k8s/events.yaml   | 2 +-
 k8s/gateway.yaml  | 2 +-
 k8s/payments.yaml | 2 +-
 3 files changed, 3 insertions(+), 3 deletions(-)
```

The CI-generated commit changed each application image to its corresponding `ghcr.io/kujifined/quickticket-<service>:8f683490648451a95154ddcf2f91f749a53819ad` tag. No subsequent run was created by this `GITHUB_TOKEN` push; the `ci:` guard also prevents a build if such a commit is pushed by another credential. ArgoCD then deployed the three new images automatically:

```text
$ argocd --core app get quickticket  # relevant status lines
Sync Policy:        Automated
Sync Status:        Synced to  (f016c33)
Health Status:      Healthy

$ kubectl rollout status deployment/gateway
deployment "gateway" successfully rolled out
$ kubectl rollout status deployment/events
deployment "events" successfully rolled out
$ kubectl rollout status deployment/payments
deployment "payments" successfully rolled out

$ kubectl get pods
NAME                        READY   STATUS    RESTARTS   AGE
events-6f49cbc98f-zbg9z     1/1     Running   0          18s
gateway-d8486796-k6kgz      1/1     Running   0          18s
payments-586dbcff8f-vhx5x   1/1     Running   0          18s
postgres-745bb5ff86-pnhsd   1/1     Running   0          7d9h
redis-5cbdd4d5ff-ncfts      1/1     Running   0          7d9h
```
