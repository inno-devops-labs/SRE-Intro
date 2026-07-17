# Lab 5

## Task 1

I configured the CI workflow to build `gateway`, `events`, and `payments`, push the images to GHCR, and update the Kubernetes manifests to use commit SHA tags. The workflow also skips recursive runs for auto-generated `ci:` commits.

Successful workflow run for the baseline deployment:

```text
CI
commit: 5747d74
status: completed
conclusion: success
url: https://github.com/KroJIak/SRE-Intro/actions/runs/28276292692
```

Published image tags can be verified through the GHCR registry API:

```bash
for service in gateway events payments; do
  token=$(curl -fsSL "https://ghcr.io/token?service=ghcr.io&scope=repository:krojiak/quickticket-$service:pull" | jq -r .token)
  curl -fsSL -H "Authorization: Bearer $token" \
    "https://ghcr.io/v2/krojiak/quickticket-$service/tags/list" |
    jq -r '.name + ": " + (.tags | join(", "))'
done
```

```text
krojiak/quickticket-gateway: 83fbcb0bdfae09a0014b78ff2d44b7ec10483bc7, 5747d748b19bbe05fb56ba85af4506d1f4cfb22e, 3670b8cdbf9b72978f6abce8d32df6c8f4b0fdae, 6e23bf79c726ab9e6e73ffe02b70caabef9b6c2e
krojiak/quickticket-events: 83fbcb0bdfae09a0014b78ff2d44b7ec10483bc7, 5747d748b19bbe05fb56ba85af4506d1f4cfb22e, 3670b8cdbf9b72978f6abce8d32df6c8f4b0fdae, 6e23bf79c726ab9e6e73ffe02b70caabef9b6c2e
krojiak/quickticket-payments: 83fbcb0bdfae09a0014b78ff2d44b7ec10483bc7, 5747d748b19bbe05fb56ba85af4506d1f4cfb22e, 3670b8cdbf9b72978f6abce8d32df6c8f4b0fdae, 6e23bf79c726ab9e6e73ffe02b70caabef9b6c2e
```

After the workflow updated the manifests, ArgoCD synced the application and the cluster switched to GHCR images. I also added a visible label change to `gateway` (`version: "v2"`).

```bash
docker exec k3d-quickticket-server-0 kubectl get application quickticket -n argocd \
  -o jsonpath='{.status.sync.status} {.status.health.status}{"\n"}'
docker exec k3d-quickticket-server-0 kubectl get deploy gateway events payments \
  -o jsonpath='{range .items[*]}{.metadata.name}{" image="}{.spec.template.spec.containers[0].image}{" version="}{.metadata.labels.version}{"\n"}{end}'
```

```text
Synced Healthy
gateway image=ghcr.io/krojiak/quickticket-gateway:83fbcb0bdfae09a0014b78ff2d44b7ec10483bc7 version=v2
events image=ghcr.io/krojiak/quickticket-events:83fbcb0bdfae09a0014b78ff2d44b7ec10483bc7 version=
payments image=ghcr.io/krojiak/quickticket-payments:83fbcb0bdfae09a0014b78ff2d44b7ec10483bc7 version=
```

## Task 2

To test rollback, I pushed a commit that changed the `gateway` image to a non-existing tag:

```text
aa426bd ci: break gateway image for rollback test
```

ArgoCD synced the bad manifest and the application became degraded because the new `gateway` pod could not pull the image:

```bash
docker exec k3d-quickticket-server-0 kubectl get application quickticket -n argocd \
  -o jsonpath='{.metadata.name} {.status.sync.status} {.status.health.status}{"\n"}'
docker exec k3d-quickticket-server-0 kubectl get pods \
  -l app=gateway \
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.containerStatuses[0].state.waiting.reason}{"\n"}{end}'
docker exec k3d-quickticket-server-0 kubectl get deploy gateway \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
```

```text
quickticket OutOfSync Degraded
gateway-575d6cf767-7f64f ErrImagePull
ghcr.io/krojiak/quickticket-gateway:does-not-exist
```

I rolled the change back with `git revert`:

```text
9e18fd1 Revert "ci: break gateway image for rollback test"
```

After the revert, ArgoCD returned to a healthy state and `gateway` started from a valid image again:

```bash
docker exec k3d-quickticket-server-0 kubectl get application quickticket -n argocd \
  -o jsonpath='{.status.sync.status} {.status.health.status}{"\n"}'
docker exec k3d-quickticket-server-0 kubectl get deploy gateway \
  -o jsonpath='{.metadata.name}{" "}{.spec.template.spec.containers[0].image}{" "}{.status.readyReplicas}{"/"}{.status.replicas}{"\n"}'
```

```text
Synced Healthy
gateway ghcr.io/krojiak/quickticket-gateway:3670b8cdbf9b72978f6abce8d32df6c8f4b0fdae 1/1
```

## Bonus Task

The workflow contains an `update-manifests` job that writes the new SHA tags back into the manifests and creates an auto-generated commit. The recursion is prevented with a condition that skips runs when the head commit message starts with `ci:`.

The branch history shows both auto-generated commits:

```bash
git log --oneline -5
```

```text
83fbcb0 ci: update image tags to 9e18fd1b9bb014c6ef83500ec2d0b211ab2f7991
9e18fd1 Revert "ci: break gateway image for rollback test"
aa426bd ci: break gateway image for rollback test
3670b8c ci: update image tags to 5747d748b19bbe05fb56ba85af4506d1f4cfb22e
5747d74 feat: switch lab5 manifests to ghcr deployment
```

The second workflow run that followed the revert also completed successfully:

```text
CI
commit: 9e18fd1
status: completed
conclusion: success
url: https://github.com/KroJIak/SRE-Intro/actions/runs/28277332647
```
