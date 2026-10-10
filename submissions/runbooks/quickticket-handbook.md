# QuickTicket SRE Handbook

## Architecture

```text
client -> gateway:8080 -> events:8081 -> Postgres
                      \-> payments:8082
events -> Redis
Prometheus scrapes service metrics
ArgoCD/Rollouts manage progressive delivery
```

- `gateway` is the public API entry point and fans out to events and payments.
- `events` owns ticket inventory, reservations, and order persistence.
- `payments` is stateless and simulates payment processing.
- Postgres is the durable database; Redis stores short-lived reservation holds.
- Prometheus and Grafana provide golden-signal monitoring.

## How to Deploy

1. Build service images and push them to the registry.
2. Update Kubernetes image tags in the GitOps repository.
3. Push the change to the tracked branch.
4. Let ArgoCD sync the manifests into the cluster.
5. For gateway changes, use Argo Rollouts canary analysis before full promotion.
6. Verify:

```bash
kubectl get pods,svc
kubectl get rollout gateway
kubectl get analysisrun
kubectl logs deploy/gateway --tail=50
```

Rollback options:

```bash
kubectl argo rollouts abort gateway
git revert <bad_commit>
```

## Monitoring

Start with these checks:

```bash
kubectl get pods
kubectl top pods
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%5B5m%5D))'
```

Important signals:

- Gateway 5xx rate: user-visible failure.
- Gateway latency p95/p99: early sign of overload.
- Events error logs: DB pool exhaustion and reservation errors.
- Postgres connection count and query latency: likely bottleneck at high load.
- Redis health and reservation hold count: distinguishes real capacity from stale holds.

During load tests, keep 409 conflicts separate from 5xx. A 409 means inventory was exhausted; a 5xx means the system failed.

## Incident Response

1. Confirm impact:

```bash
kubectl get pods
kubectl logs deploy/gateway --tail=100
kubectl logs deploy/events --tail=100
```

2. Check whether the failure is application, dependency, or rollout related:

```bash
kubectl get rollout gateway
kubectl get analysisrun
kubectl top pods
```

3. If a canary caused it, abort:

```bash
kubectl argo rollouts abort gateway
```

4. If events is returning 500s under load, check for DB pool exhaustion and reduce load or scale only after fixing DB connection limits/backpressure.

5. After mitigation, write down the timeline, customer impact, root cause, and follow-up actions.

## Backup and Restore

Manual dump:

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  pg_dump -U quickticket -Fc quickticket > /tmp/quickticket.dump
```

Validate:

```bash
POD=$(kubectl get pod -l app=postgres -o jsonpath='{.items[0].metadata.name}')
kubectl cp /tmp/quickticket.dump "$POD":/tmp/backup.dump
kubectl exec "$POD" -- pg_restore --list /tmp/backup.dump | head -25
```

Restore:

```bash
kubectl exec "$POD" -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
kubectl rollout restart deployment/events
kubectl rollout status deployment/events --timeout=60s
```

Production improvement:

- Keep Postgres on persistent storage.
- Automate scheduled dumps.
- Add WAL archiving/PITR for lower RPO.
- Regularly test restore, not only backup creation.
