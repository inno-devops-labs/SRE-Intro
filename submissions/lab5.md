# Lab 5 — CI/CD and GitOps

## Environment

Docker Desktop `29.2.1`, kubectl client `v1.34.1`, k3d `v5.9.0`, Helm `v4.3.0`, and ArgoCD CLI `v3.5.3` were used. The existing `quickticket` k3d node was `Ready`.

## CI and GHCR

The CI workflow is `.github/workflows/ci.yml`. It runs on `main` and `feature/lab5`, supports `workflow_dispatch`, ignores `submissions/**` changes, pushes the three lower-case GHCR image names, updates only the raw manifests, and uses `[skip ci]` on the bot commit.

The first green run was [run 36228804199](https://github.com/GeorgyPyanov/SRE-Intro/actions/runs/36228804199), for source commit `2add944233cb83b707186981cfcb69fb197d8678`; it completed successfully at `2026-09-26T08:07:35Z`.

The corrected tag-update run was [run 36228949069](https://github.com/GeorgyPyanov/SRE-Intro/actions/runs/36228949069), for `118dcc4ec592eadeb35bf0b77b087570d756d176`; it completed successfully at `2026-09-26T08:10:40Z` and created bot commit:

```text
00dcd6c ci(images): update image tags to 118dcc4ec592eadeb35bf0b77b087570d756d176 [skip ci]
```

The images were verified by real unauthenticated pulls, so the public packages did not require a PAT pull secret:

```text
ghcr.io/georgypyanov/quickticket-gateway:118dcc4ec592eadeb35bf0b77b087570d756d176
sha256:11f1411877db2c4c6adf3ddf4a015dac8fddc8a59d30b9ad526e4f67dcc1be3c
ghcr.io/georgypyanov/quickticket-events:118dcc4ec592eadeb35bf0b77b087570d756d176
sha256:6cf4fe915fce27eebc3d63cda4bf81250db7201cf5672b12744c60c2e8804fb4
ghcr.io/georgypyanov/quickticket-payments:118dcc4ec592eadeb35bf0b77b087570d756d176
sha256:820154177be201369a14f212ab067e1b3222996b805f2152caaba853a542a9f4
```

`GHCR_PAT` was not present and no token was stored in Git, Markdown, or Kubernetes. The manifests contain the requested `imagePullSecrets` reference; kubelet warned that public-image secret `ghcr-secret` did not exist but pulled all three public images successfully.

## ArgoCD Deployment

The Lab 4 Helm release `quickticket` was recorded as `deployed`, then uninstalled immediately before ArgoCD Application creation. The monitoring namespace was preserved. ArgoCD was installed in namespace `argocd`; the optional `applicationsets` CRD reported a client-side annotation-size error, but the required `applications.argoproj.io` CRD and all core server components were created and `argocd-server` became Available.

Application source is `https://github.com/GeorgyPyanov/SRE-Intro.git`, revision `feature/lab5`, path `k8s`, destination namespace `default`, with automated prune and self-heal. At the first registry deployment the Application was:

```text
Synced Healthy 00dcd6cf464983ec3e6ad6a6e39f95e8b5b9d9cf
```

Post-Argo PostgreSQL seed result:

```text
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

Kubernetes events confirmed real GHCR downloads for gateway, events, and payments, all with tag `118dcc4…`.

## GitOps Label and Drift

Commit `fd2b50100dbee5286b1a21397025d70fe50e85ae` added the visible gateway label. Its CI run created bot commit `c826718`, and an ArgoCD sync deployed image `ghcr.io/georgypyanov/quickticket-gateway:fd2b50100dbee5286b1a21397025d70fe50e85ae` with:

```text
kubectl get deployment gateway -o jsonpath=...
v2 ghcr.io/georgypyanov/quickticket-gateway:fd2b50100dbee5286b1a21397025d70fe50e85ae
```

For drift testing, the label was manually changed and ArgoCD self-heal restored Git state:

```text
2026-09-26T11:19:52.1986726+03:00  version=manual
2026-09-26T11:20:07.5242388+03:00  version=v2
Synced Healthy 69e126120e46507295e39d283540cdd40c813fca
```

With `selfHeal=true`, a manual `kubectl edit` or label change creates drift and ArgoCD returns the resource to Git state. Without self-heal, the resource can remain OutOfSync until a sync is triggered.

## Bad Deploy and Git Revert

The bad commit used `[skip ci]` so the workflow could not repair it automatically:

```text
ddf4509 test(lab5): deploy nonexistent gateway image [skip ci]
BAD_PUSH=2026-09-26T11:18:15.8635004+03:00
```

ArgoCD sync started at `11:18:40+03:00`. The resulting gateway pod was `0/1 ErrImagePull`; describe output showed:

```text
Image: ghcr.io/georgypyanov/quickticket-gateway:does-not-exist
Reason: ErrImagePull
Error: ImagePullBackOff
failed to resolve reference ... does-not-exist: not found
```

Rollback was only through Git:

```text
69e1261 Revert "test(lab5): deploy nonexistent gateway image [skip ci]"
REVERT_PUSH=2026-09-26T11:19:07.3841938+03:00
```

ArgoCD sync finished at `11:19:26+03:00` with revision `69e126120e46507295e39d283540cdd40c813fca`, `Synced`, and `Healthy`. The observable recovery from revert push to completed healthy sync was about `18.6 seconds`.

## Final State

The current Application is `Synced Healthy` at revision `69e1261…`; the bad tag is absent after the revert. The next CI bot commit is deliberately skipped by its exact `[skip ci]` marker, so it did not start an infinite loop. Public GHCR images are deployed, the managed gateway label is `v2`, and payments image configuration retains failure rate `0.0` and latency `0`.
