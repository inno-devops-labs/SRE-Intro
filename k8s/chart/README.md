# quickticket Helm chart

Lab 4 bonus task. This is a packaged, value-driven version of the raw manifests in
[`k8s/`](../).

> **The raw manifests in `k8s/*.yaml` are still the source of truth for the course.**
> Labs 5–12 edit those files directly, so if you change them, this chart may drift.
>
> `kubectl apply -f k8s/` is **not** recursive, so this `k8s/chart/` subdirectory
> never gets picked up by it and the two can't collide.

## Install

Images must already be in the cluster (`imagePullPolicy: Never` by default):

```bash
docker build -t quickticket-gateway:v1 app/gateway
docker build -t quickticket-events:v1 app/events
docker build -t quickticket-payments:v1 app/payments
k3d image import quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 -c quickticket
```

The chart deploys the same object names as the raw manifests, so remove those
first, or install into a separate namespace:

```bash
kubectl delete -f k8s/
helm install quickticket k8s/chart/
kubectl get pods
helm list
```

Reach the gateway:

```bash
kubectl port-forward svc/gateway 3080:8080
curl -s localhost:3080/events | python3 -m json.tool
```

## Common overrides

```bash
# Scale the gateway
helm upgrade quickticket k8s/chart/ --set gateway.replicas=3

# Inject payment failures (Lab 1 style fault injection)
helm upgrade quickticket k8s/chart/ \
  --set payments.failureRate=0.3 --set payments.latencyMs=800

# Use an external database, drop the bundled one
helm upgrade quickticket k8s/chart/ \
  --set postgres.enabled=false --set events.db.host=my-rds.example.com

# Turn probes off for debugging
helm upgrade quickticket k8s/chart/ --set probes.enabled=false
```

See [`values.yaml`](values.yaml) for the full list.

## Uninstall / go back to the raw manifests

```bash
helm uninstall quickticket
kubectl apply -f k8s/
```

Note that `helm uninstall` also removes `PersistentVolumeClaim/postgres-data`,
so the database gets wiped and re-seeded from the ConfigMap on the next install.

## Notes

- `files/seed.sql` is a copy of `app/seed.sql`, rendered into a ConfigMap and
  mounted at `/docker-entrypoint-initdb.d/`. Helm can't read files outside the
  chart directory, hence the copy. Keep the two in sync.
- Liveness probes are TCP, not `httpGet /health`, on purpose. See the Task 2
  write-up in [`submissions/lab4.md`](../../submissions/lab4.md).
