# QuickTicket SRE Handbook

## Architecture

```text
client / loadgen
      |
      v
gateway Rollout, 5 replicas, canary controlled by Argo Rollouts
      |
      +--> events Deployment
      |       |
      |       +--> Postgres, PVC postgres-data
      |       |
      |       +--> Redis, reservation holds
      |
      +--> payments Deployment

Prometheus scrapes gateway, events, and payments.
Argo Rollouts AnalysisTemplate queries Prometheus during canaries.
Postgres backup CronJob writes custom-format dumps to postgres-backups PVC.
```

- User traffic enters through the `gateway` Service.
- `gateway` calls `events` for listing and reservation, and `payments` for checkout.
- `events` depends on Postgres for event/order data and Redis for temporary reservation holds.
- `gateway` is deployed as an Argo Rollouts `Rollout` with canary steps and automated analysis.

## How to Deploy

Normal GitOps flow:

```bash
git switch -c feature/my-change
# edit app, k8s, monitoring, or submissions files
git add .
git commit -m "feat: describe change"
git push -u origin feature/my-change
```

Open a PR to `main`. After merge, GitHub Actions builds `gateway`, `events`, and `payments`, pushes SHA-tagged GHCR images, updates Kubernetes manifests, and pushes the manifest update commit. ArgoCD watches the `k8s/` directory and syncs the cluster.

Manual cluster checks:

```bash
kubectl get pods,svc
kubectl get rollout gateway
kubectl argo rollouts get rollout gateway
kubectl get analysistemplate gateway-error-rate
```

Canary safety check:

```bash
kubectl argo rollouts get rollout gateway --watch
kubectl get analysisrun
```

Expected steady state: five ready gateway pods, one ready pod each for `events`, `payments`, `redis`, and `postgres`, and `rollout/gateway` in `Healthy` phase.

## Monitoring

Prometheus runs in the `monitoring` namespace.

```bash
kubectl -n monitoring port-forward svc/prometheus 9091:9090
```

Core queries:

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m]))
/
sum(rate(gateway_requests_total[5m]))
```

```promql
histogram_quantile(
  0.99,
  sum by (le, path) (rate(gateway_request_duration_seconds_bucket[5m]))
)
```

```promql
gateway:sli_availability:ratio_rate5m
gateway:sli_latency_500ms:ratio_rate5m
gateway:error_budget_burn_rate:ratio_rate5m
```

During a rollout, check canary-specific errors through the `gateway-error-rate` AnalysisTemplate. During incidents, look at error rate, p99 latency, traffic volume, and target health together.

Monitoring gaps to close next:

- page on p99 latency for checkout, not only 5xx;
- alert when gateway endpoints drop to zero;
- separate 409 inventory conflicts from 5xx failures;
- add Redis, Postgres, and payment-specific dependency alerts.

## Incident Response

High error-rate alert:

- Fires when gateway 5xx rate is above the configured threshold for the pending period.
- First check whether the issue is global or path-specific.
- Keep exact timestamps for detection, diagnosis, mitigation, and recovery.

Fast triage:

```bash
kubectl get pods,svc,rollout
kubectl logs deploy/gateway --tail=80
kubectl logs deploy/events --tail=80
kubectl logs deploy/payments --tail=80
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total{status=~"5.."}[5m]))/sum(rate(gateway_requests_total[5m]))'
```

Common causes:

| Symptom | Likely cause | Mitigation |
| --- | --- | --- |
| `/reserve/{id}/pay` 503s | payments failure injection or payment outage | restore `PAYMENT_FAILURE_RATE=0.0`, restart payments |
| `/events` 502s | events unavailable, DB issue, or events pool pressure | inspect events logs, Postgres health, Redis health; restart events if pool handles are stale |
| gateway has no endpoints | readiness too tightly coupled to dependency health | restore dependency, then decouple readiness from dependency health in the next fix |
| p99 high but errors low | slow dependency | use latency SLO alert and dependency-specific logs |

Escalate if the service is not back inside 10 minutes. Attach Prometheus query output, pod status, gateway/events/payments logs, and the mitigation commands already tried.

## Backup and Restore

Postgres uses `postgres-data` PVC. Backups are created by `postgres-backup` CronJob every 5 minutes and stored on `postgres-backups` PVC. Retention keeps the five newest dumps.

Check backups:

```bash
kubectl get cronjob postgres-backup
kubectl get pvc postgres-data postgres-backups
kubectl exec deployment/backup-inspector -- ls -la /backups
```

Create an on-demand backup:

```bash
kubectl create job --from=cronjob/postgres-backup manual-backup
kubectl logs job/manual-backup
```

Restore from a dump:

```bash
POD=$(kubectl get pod -l app=postgres -o jsonpath='{.items[0].metadata.name}')
kubectl cp /tmp/quickticket.dump "$POD":/tmp/backup.dump
kubectl exec "$POD" -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
kubectl rollout restart deployment/events
kubectl rollout status deployment/events --timeout=60s
```

After restore, verify:

```bash
kubectl exec "$POD" -- psql -U quickticket -d quickticket -c '\dt'
kubectl exec "$POD" -- psql -U quickticket -d quickticket -c 'SELECT count(*) FROM events;'
kubectl exec "$POD" -- psql -U quickticket -d quickticket -c 'SELECT count(*) FROM orders;'
```

Current measured recovery notes:

- Without PVC, Postgres pod loss required restore from dump.
- With PVC, a Postgres pod restart kept data and app RTO was about 26 seconds.
- RPO is bounded by backup interval; with the current 5-minute CronJob, worst-case RPO is about 5 minutes.
