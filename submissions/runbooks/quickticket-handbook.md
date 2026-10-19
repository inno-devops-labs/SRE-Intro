# QuickTicket SRE Handbook

## Architecture

```text
                         ┌─────────────────┐
                         │     Clients     │
                         └────────┬────────┘
                                  │
                                  ▼
                     ┌────────────────────────┐
                     │ Gateway Service        │
                     │ Argo Rollout           │
                     │ 5 replicas             │
                     └─────────┬──────────────┘
                               │
                  ┌────────────┴────────────┐
                  │                         │
                  ▼                         ▼
          ┌───────────────┐         ┌───────────────┐
          │ Events        │         │ Payments      │
          │ Service       │         │ Service       │
          └───────┬───────┘         └───────────────┘
                  │
          ┌───────┴────────┐
          │                │
          ▼                ▼
    ┌────────────┐    ┌────────────┐
    │ PostgreSQL │    │ Redis      │
    │ PVC-backed │    │ Holds      │
    └────────────┘    └────────────┘

          Metrics from application services
                       │
                       ▼
                 ┌────────────┐
                 │ Prometheus │
                 └────────────┘
```

Key properties:

- Gateway is deployed using Argo Rollouts.
- Kubernetes Services provide service discovery and load balancing.
- Events owns event and reservation operations.
- Payments handles payment processing.
- PostgreSQL stores durable application data.
- PostgreSQL uses a PersistentVolumeClaim.
- Redis stores temporary reservation state.
- Prometheus collects service and gateway metrics.
- Git is the desired-state source for GitOps deployment.

---

## How to Deploy

QuickTicket uses a GitOps deployment flow.

### Normal Flow

1. Make the application or Kubernetes manifest change.
2. Commit the change to Git.
3. Push the branch.
4. GitHub Actions builds the service image.
5. The image is pushed to GHCR using an immutable commit-SHA tag.
6. The Kubernetes manifest references the desired image.
7. ArgoCD detects the Git change.
8. ArgoCD synchronizes the cluster.
9. Argo Rollouts progressively deploys gateway changes.
10. Verify rollout health before promotion.

Useful commands:

```bash
git status
git add .
git commit -m "description"
git push

kubectl get pods
kubectl get rollout gateway
kubectl argo rollouts get rollout gateway
```

Do not use `kubectl edit` as the normal deployment process.

A direct cluster edit creates configuration drift because Git remains the
desired source of truth.

---

## Monitoring

### Basic Health

```bash
kubectl get pods
kubectl get rollout gateway
```

Gateway health:

```bash
kubectl run health-check \
  --image=curlimages/curl:latest \
  --rm -i \
  --restart=Never \
  --quiet \
  --command -- \
  curl -s http://gateway:8080/health
```

### Request Rate

PromQL:

```promql
sum(rate(gateway_requests_total[5m]))
```

### 5xx Error Rate

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m]))
/
sum(rate(gateway_requests_total[5m]))
```

### p99 Latency

```promql
histogram_quantile(
  0.99,
  sum by (le) (
    rate(gateway_request_duration_seconds_bucket[5m])
  )
)
```

### Availability SLO

Target:

```text
99.5% non-5xx requests over 7 days
```

### Latency SLO

Target:

```text
95% of gateway requests below 500 ms
```

### Capacity Warning Signs

Investigate when:

```text
5xx > 0.5%
p99 > 500 ms
Postgres CPU > 80%
events CPU approaches its limit
database connections approach pool capacity
```

Do not treat HTTP 409 reservation conflicts as infrastructure failures.
They indicate ticket inventory contention.

---

## Incident Response

### 1. Confirm Impact

Check:

```bash
kubectl get pods
kubectl get rollout gateway
```

Check the gateway health endpoint and Prometheus golden signals:

- traffic;
- errors;
- latency;
- saturation.

### 2. Identify the Failing Path

Split metrics by:

- endpoint;
- HTTP status;
- dependency.

For example, widespread `/events` 502 responses suggest that gateway-to-events
or events-to-database processing is failing.

### 3. Check Recent Changes

```bash
git log --oneline -10
kubectl argo rollouts get rollout gateway
```

If a canary caused the incident, abort it:

```bash
kubectl argo rollouts abort gateway
```

Previous testing showed automated rollback can restore stable traffic much
faster than waiting for a manual Git revert.

### 4. GitOps Rollback

If the desired state in Git is bad:

```bash
git revert <bad-commit>
git push
```

Allow ArgoCD to synchronize the restored state.

### 5. Verify Recovery

Check:

```bash
kubectl get pods
kubectl get rollout gateway
```

Then confirm:

- health endpoint is healthy;
- 5xx rate has returned to normal;
- latency has recovered;
- dependency saturation has fallen.

### Escalation

Escalate when:

- availability remains below the SLO;
- rollback does not recover the service;
- persistent data may be lost;
- PostgreSQL cannot start or attach its volume;
- the incident affects multiple critical dependencies.

---

## Backup and Restore

PostgreSQL uses a PersistentVolumeClaim, so ordinary pod replacement should not
lose database data.

A backup mechanism also creates PostgreSQL custom-format dumps.

### Manual Backup

```bash
POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)

kubectl exec -i "$POD" -- \
  pg_dump -U quickticket -Fc quickticket \
  > /tmp/quickticket.dump
```

Verify:

```bash
ls -lh /tmp/quickticket.dump
file /tmp/quickticket.dump
```

### Restore

Copy the dump into the Postgres pod:

```bash
POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)

kubectl cp /tmp/quickticket.dump \
  "$POD:/tmp/backup.dump"
```

Restore:

```bash
kubectl exec "$POD" -- \
  pg_restore \
  -U quickticket \
  -d quickticket \
  --clean \
  --if-exists \
  /tmp/backup.dump
```

If the events service holds stale database connections:

```bash
kubectl rollout restart deployment/events
kubectl rollout status deployment/events --timeout=30s
```

### Automated Backups

The `postgres-backup` CronJob runs every five minutes.

```bash
kubectl get cronjob postgres-backup
```

The retention policy keeps the five newest backups.

Inspect backup files using:

```bash
kubectl exec deployment/backup-inspector -- \
  ls -lah /backups
```

### Recovery Principles

- PVC protects against ordinary Postgres pod replacement.
- Backups protect against logical corruption or destructive database changes.
- A backup is only useful if restoration has been tested.
- RPO depends on backup frequency.
- RTO includes the complete period until the application, not merely the
  database process, is healthy again.
