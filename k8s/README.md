# QuickTicket — Lab 7

Prerequisites are the raw Kubernetes manifests from Lab 5, with immutable GHCR
image tags. PostgreSQL uses ephemeral storage and demo credentials; keep this
in the isolated training cluster. Never reseed an existing events table.

```bash
k3d cluster create quickticket-lab78 --image rancher/k3s:v1.33.6-k3s1 --agents 1 --wait
# Install the v1.9.0 kubectl-argo-rollouts plugin for your OS/architecture.
# https://argoproj.github.io/argo-rollouts/installation/
./scripts/bootstrap-lab7.sh
kubectl apply -f monitoring/lab7/grafana.yaml
kubectl port-forward -n monitoring svc/grafana 3000:3000
```

Grafana dashboard: http://localhost:3000/d/quickticket-lab7 (anonymous Viewer
inside this training cluster). Its datasource is the in-cluster Prometheus.
Readiness on gateway checks its TCP listener so dependency failures remain
measurable; `/health` still reports their actual degraded state. Events keeps
its original dependency readiness for the Lab 8 baseline.

Final gateway strategy uses automated analysis; manual and granular strategies
are recorded in the Lab 7 report and experiment runner. Install Python PyYAML,
then run `python scripts/run-lab7.py` with `~/.local/bin` on PATH. The script
changes the manifest while exercising revisions and restores the healthy one.

ArgoCD is intentionally absent from the isolated experiment cluster: self-heal
would undo `kubectl` fault injections. For GitOps deployment, first commit the
Rollout conversion and install its CRDs, then sync the Application. Remove the
old gateway Deployment during the migration; do not run both controllers with
the same Service selector. Prometheus/Rollouts should be installed independently
of the Application's `k8s` source path.
