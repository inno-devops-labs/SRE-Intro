# Lab 12 deployment and migration order

Requires the Lab 11 code included in this branch, Argo Rollouts v1.9, and the supplied in-cluster Prometheus/mixedload manifests. Gateway is a Rollout (5 replicas); events, payments and notifications are Deployments (2 each). `k8s/gateway-hpa.yaml` is optional and deliberately outside kustomization so the graded five-replica test stays fixed.

For an **existing Lab 11 database**, retain its data and seed schema. Install `requirements-labs.txt`, port-forward PostgreSQL to 15432 and run `alembic stamp 0001` only if that database has no version table and already matches `0001`'s existing-schema baseline. Apply `0002` (earlier lab's nullable email) and `0003` (concurrent index). Never stamp over an existing migration history.

Run the rename in this exact order with continuous checkout traffic:

```sh
alembic upgrade 0004
# Build intermediate events source in a temporary project copy.
LAB12_STAGE_DIR=$(mktemp -d)
mkdir -p "$LAB12_STAGE_DIR/app"
cp -R app/events "$LAB12_STAGE_DIR/app/events"
patch -d "$LAB12_STAGE_DIR" -p1 < tools/lab12/events_deploy_a.patch
docker build -t quickticket-events:v12a "$LAB12_STAGE_DIR/app/events"
k3d image import -c quickticket-lab1112 quickticket-events:v12a
OLD_EVENTS_PODS=$(kubectl get pods -l app=events -o name)
kubectl set image deployment/events events=quickticket-events:v12a
kubectl rollout status deployment/events --timeout=120s
for pod in $OLD_EVENTS_PODS; do kubectl wait --for=delete "$pod" --timeout=120s; done
alembic upgrade 0005
# Deploy B is the checked-in final source.
docker build -t quickticket-events:v12b app/events
k3d image import -c quickticket-lab1112 quickticket-events:v12b
OLD_EVENTS_PODS=$(kubectl get pods -l app=events -o name)
kubectl set image deployment/events events=quickticket-events:v12b
kubectl rollout status deployment/events --timeout=120s
for pod in $OLD_EVENTS_PODS; do kubectl wait --for=delete "$pod" --timeout=120s; done
alembic upgrade 0006
```

Wait for deletion of the old pods, including terminating pods: rollout status alone can return while old connections are still draining. The first experiment exposed this race and the explicit gate is required before contract.

Do not run `upgrade head` straight through on an existing live application: the two code deployments are necessary gates. `DATABASE_URL` overrides the local-development URL in alembic.ini. A failed concurrent index build may leave an INVALID index; inspect `pg_index.indisvalid` and drop an invalid index concurrently before retrying (IF NOT EXISTS alone cannot repair it).

For a **fresh database only**, `app/seed.sql` contains the final schema (scheduled_at, email, and the new index). Load it once and `alembic stamp head`; final application code then works immediately. Do not execute historical event_date migrations against that final schema. All manifests reference locally built/imported images (`Never`), including `quickticket-events:v12b`.

```sh
kubectl apply -f k8s/postgres.yaml -f k8s/redis.yaml
kubectl rollout status deployment/postgres
kubectl exec -i deployment/postgres -- psql -U quickticket -d quickticket < app/seed.sql
alembic stamp head
kubectl apply -k k8s
kubectl apply -f labs/lab7/prometheus.yaml -f labs/lab8/mixedload.yaml
```

`tools/lab12_observer.py` runs from an httpx-equipped cluster pod and records read/reserve/pay outcomes. `tools/lab12_metrics.py` snapshots Prometheus query results; compare numeric values, not whole JSON responses containing timestamps. Keep the kubectl Argo Rollouts plugin on PATH; restart with `kubectl argo rollouts restart gateway`.
