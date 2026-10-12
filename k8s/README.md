# QuickTicket — Labs 9/10 prerequisites

These manifests make each submission independently deployable from main.
Immutable application image tags come from Lab 5. Rollout and Prometheus
configuration comes from Lab 7; events readiness keeps DB-only reads available
through a Redis outage, as measured in Lab 8.

```bash
k3d cluster create quickticket-lab78 --image rancher/k3s:v1.33.6-k3s1 --agents 1 --wait
scripts/bootstrap-lab7.sh
```

For the Lab 9 ephemeral-storage comparison on a disposable cluster use
`LAB9_EPHEMERAL=1 scripts/bootstrap-lab7.sh`; the runner then applies the final
PVC manifest after measuring recovery. DB migrations use the separate pinned
`requirements-migrations.txt`. Read submissions/lab9.md before running destructive
drills. The bootstrap does not install ArgoCD/CI from previous independent PRs.
The backup CronJob needs labs/lab9/backup-storage.yaml first. Demonstration
credentials and local-path volumes are for the course environment.
