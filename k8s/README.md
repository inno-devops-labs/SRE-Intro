# Lab 11 deployment

Install Argo Rollouts v1.9 and apply `labs/lab7/prometheus.yaml` first.
Build gateway, events, payments and notifications as `quickticket-<service>:v1`; import all four into k3d.
Apply postgres + redis, wait for postgres, then load the initial schema once:

```sh
kubectl apply -f k8s/postgres.yaml -f k8s/redis.yaml
kubectl rollout status deployment/postgres
kubectl exec -i deployment/postgres -- psql -U quickticket -d quickticket < app/seed.sql
kubectl apply -k k8s
kubectl apply -f labs/lab7/prometheus.yaml -f labs/lab8/mixedload.yaml
```

Use a fresh database for the seed command; it is not an idempotent migration.
The local-image manifests intentionally use imagePullPolicy Never.
The gateway uses an Argo Rollout, not a Deployment.
Tests: `python -m unittest discover -s tests -v`.
Run `tools/lab11_probe.py` in a cluster pod with httpx installed.
