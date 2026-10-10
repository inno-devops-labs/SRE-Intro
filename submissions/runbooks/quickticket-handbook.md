# QuickTicket SRE Handbook

## Architecture

```text
Client / Locust
      |
      v
gateway Service -> 5 gateway replicas / Argo Rollout
      |
      +--> events Service -> PostgreSQL + Redis
      |
      +--> payments Service
      |
      +--> Prometheus -> Grafana / Argo Rollouts analysis
```

- Gateway is stateless and can be replaced or scaled horizontally.
- Events owns event inventory, reservations, and orders.
- PostgreSQL is the system of record and uses a PVC plus scheduled custom-format dumps.
- Redis stores temporary reservation holds and is not the source of truth.
- Locust runs inside the cluster so traffic is distributed through the gateway Service.

## How to Deploy

1. Create a feature branch and commit the application or manifest change.
2. Push the branch; GitHub Actions builds and publishes immutable GHCR image tags.
3. ArgoCD observes the configured Git revision and renders the `k8s/` manifests.
4. Argo Rollouts shifts gateway traffic through the canary weights and runs the Prometheus error-rate AnalysisTemplate.
5. Confirm `Synced/Healthy`, Pod readiness, and the gateway smoke check.
6. For a failed change, revert the Git commit and refresh ArgoCD; do not hot-edit the cluster as the permanent fix.

## Monitoring

Start with the four golden signals:

```promql
sum(rate(gateway_requests_total[5m]))
sum(rate(gateway_requests_total{status=~"5.."}[5m]))
histogram_quantile(0.99, sum by (le, path) (rate(gateway_request_duration_seconds_bucket[5m])))
up{job="gateway"}
```

Interpret signals separately:

- 5xx indicates service failure; 409 on reservation indicates expected inventory contention.
- p95/p99 latency can violate the SLO before 5xx increases.
- `events_db_pool_size` and database health should be present when investigating connection saturation.
- Check the newest backup age and periodically perform a restore drill.

## Incident Response

1. Confirm the symptom with `/health`, gateway 5xx rate, and p99 by path.
2. Check `kubectl get pods`, `kubectl get events`, and recent deployment/rollout status.
3. Identify the dependency: events/PostgreSQL, Redis, or payments.
4. Stop or reduce load if it is amplifying the incident; preserve timestamps and Prometheus evidence.
5. Mitigate with the smallest reversible action: restart a bad stateless Pod, scale a service, or revert the Git change.
6. Verify recovery with API smoke tests, Pod readiness, and error/latency recovery.
7. Record impact, timeline, root cause, recovery time, and follow-up automation in a blameless review.

## Backup and Restore

PostgreSQL uses the `postgres-data` PVC for restart durability. The `postgres-backup` CronJob writes `pg_dump -Fc` files to the `postgres-backups` PVC and retains the five newest dumps.

```bash
POD=$(kubectl get pod -l app=postgres -o jsonpath='{.items[0].metadata.name}')
kubectl exec "$POD" -- pg_dump -U quickticket -Fc quickticket > /tmp/quickticket.dump
kubectl exec "$POD" -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
kubectl exec "$POD" -- psql -U quickticket -d quickticket -c '\dt'
```

- PVC restart recovery was measured at 12 seconds in Lab 9.
- Recovery without PVC required restore and application reconnect and measured 96 seconds.
- A logical dump defines the current RPO as the time since the latest successful backup; PITR/WAL archiving would reduce the production RPO further.
