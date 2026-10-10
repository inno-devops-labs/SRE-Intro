# QuickTicket in k3d — Lab 4

Run from the repository root. These are local lab manifests with demo credentials
and ephemeral PostgreSQL/Redis storage. Deleting the PostgreSQL pod loses its data;
re-run the seed on a **fresh** database. `seed.sql` is not idempotent for event rows.
Persistent storage is covered in a later lab.

## Raw manifests

```bash
k3d cluster create quickticket --image rancher/k3s:v1.33.3-k3s1
# Use a kubectl 1.33 client with this server.
docker build -t quickticket-gateway:v1 app/gateway
docker build -t quickticket-events:v1 app/events
docker build -t quickticket-payments:v1 app/payments
k3d image import quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 -c quickticket
kubectl --context k3d-quickticket apply -f k8s/
for app in postgres redis events payments gateway; do
  kubectl --context k3d-quickticket rollout status deployment/$app --timeout=180s
done
kubectl --context k3d-quickticket exec -i deployment/postgres -- \
  psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql
kubectl --context k3d-quickticket port-forward svc/gateway 3080:8080
```

Use port `3084:8080` instead if the Lab 3 Compose stack is using 3080.
Then request `/events` and `/health` on the forwarded port.
The `events` init containers wait for both dependencies before app startup.

Docker Desktop's containerd image store can produce `content digest ... not found`
when k3d imports multi-platform images ([k3d issue #1538](https://github.com/k3d-io/k3d/issues/1538)).
The tested Apple Silicon workaround is a platform-specific archive:

```bash
docker pull postgres:17-alpine
docker pull redis:7-alpine
docker save --platform linux/arm64 -o /tmp/quickticket-arm64.tar \
  quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 \
  postgres:17-alpine redis:7-alpine
k3d image import /tmp/quickticket-arm64.tar -c quickticket
```

Use `linux/amd64` on an x86 host. Verify the actual rollout: k3d may print a success
line even after an import error.

## Probes and recovery

Readiness calls `/health` on each Python service; gateway/events check dependencies.
Liveness uses a TCP check on the service's own port, so an unavailable database
removes the pod from ready Service endpoints without causing restart cascades.
A TCP probe checks the listener, not complete application correctness; a dedicated
process-only HTTP liveness endpoint would be a stronger future improvement.
Startup probes allow up to 60 seconds for initialization.

```bash
kubectl --context k3d-quickticket get pods -w
# In another terminal:
kubectl --context k3d-quickticket delete pod -l app=gateway
kubectl --context k3d-quickticket delete pod -l app=redis
```

Redis can recover before the next probe. To hold the failure long enough to observe it:

```bash
kubectl --context k3d-quickticket scale deployment/redis --replicas=0
# Observe events 0/1 Ready, then inspect ready: false in EndpointSlice:
kubectl --context k3d-quickticket get endpointslices \
  -l kubernetes.io/service-name=events -o yaml
kubectl --context k3d-quickticket scale deployment/redis --replicas=1
kubectl --context k3d-quickticket rollout status deployment/events --timeout=90s
```

Always restore Redis after the experiment. All containers (including init containers)
have CPU/memory requests and limits.

## Helm bonus

The raw manifests and chart are alternatives with the same fixed service names;
use one release per namespace. Keep raw manifests for subsequent labs.
The following deletes the **lab** database, so seed again after installing the chart:

```bash
helm lint k8s/chart
helm template quickticket k8s/chart
kubectl --context k3d-quickticket delete -f k8s/
helm install quickticket k8s/chart --kube-context k3d-quickticket --wait --timeout 3m
kubectl --context k3d-quickticket exec -i deployment/postgres -- \
  psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql
helm list --kube-context k3d-quickticket
kubectl --context k3d-quickticket get pods
```

Images, pull policies, replicas, environment variables, probes and resources are
configurable in `chart/values.yaml`. For example:

```bash
helm upgrade quickticket k8s/chart --kube-context k3d-quickticket \
  --set gateway.replicas=2 --set-string payments.env.PAYMENT_LATENCY_MS=250 --wait
```

`port` controls the Service/container port declaration; when changing an application's
listen port, also configure its image/command and the upstream URL and probes.
Database credentials in `postgres.env` and `events.env` must match.
Optional kube-prometheus-stack is not installed for this submission; Lab 3 provides
the tested monitoring stack.
