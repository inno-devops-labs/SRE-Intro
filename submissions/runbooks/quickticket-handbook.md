# QuickTicket SRE Handbook

**Student:** Diana Kalugina

**Date:** 2026-10-12

## 1. Architecture

```text
                     clients / load generators
                               |
                     Service gateway :8080
                               |
                    Gateway Rollout x5
                         /             \
                        /               \
             Service events         Service payments
             Events Deployment      Payments Deployment
                 |        |
                 |        +------ Redis
                 |
              Postgres
          PVC postgres-data

        Prometheus scrapes Gateway metrics
        Argo Rollouts manages canary delivery
        Argo CD reconciles Kubernetes manifests
```

Important characteristics:

- Gateway uses five replicas and Argo Rollouts.
- Events uses Postgres and Redis.
- Postgres data is stored on `postgres-data`, added in Lab 9.
- Postgres uses `strategy: Recreate` with the RWO volume.
- Lab 9 recovery improved from 171 s without the PVC to 13 s with the PVC.
- PostgreSQL logical backups run every five minutes and keep the five newest dumps.
- Lab 10 found the current capacity ceiling near 28 RPS, primarily because of Postgres CPU and the growing `/events` aggregation.

## 2. How to Deploy

1. Make the code or manifest change in a feature branch.
2. Run local checks and review the Git diff.
3. Open a PR and merge the intended change into the GitOps source branch.
4. CI builds the service image and updates the deployment reference.
5. Argo CD reconciles the cluster.
6. Gateway changes are delivered with Argo Rollouts canary steps.
7. Prometheus-based analysis checks the new revision before full traffic.

Useful commands:

```bash
kubectl argo rollouts get rollout gateway --watch
kubectl get analysisrun
kubectl get pods -o wide
```

For a failing Gateway canary:

```bash
kubectl argo rollouts abort gateway
```

Lab 7 measured stable-only traffic about 1.21 s after abort and full stable capacity about 9.74 s later.

## 3. Monitoring

Useful Prometheus checks:

```promql
sum(rate(gateway_requests_total{path!="/health"}[5m]))
```

```promql
sum(rate(gateway_requests_total{status=~"5..",path!="/health"}[5m]))
/
sum(rate(gateway_requests_total{path!="/health"}[5m]))
```

```promql
histogram_quantile(
  0.99,
  sum by (le, path) (
    rate(gateway_request_duration_seconds_bucket[2m])
  )
)
```

Lab 8 added this payment latency alert:

```text
GatewayPayP99High
threshold: p99 > 0.6 s
duration: 40 s
```

It moved from pending to firing while payment p99 remained near 0.84 s.

Monitoring still needs:

- blackbox probing through the Gateway Service;
- Postgres CPU / throttling metrics;
- Events pool-exhaustion metrics;
- Redis health metrics;
- Alertmanager routing;
- throughput-drop alerting.

During capacity incidents, also run:

```bash
kubectl top pods -l app=gateway
kubectl top pods -l app=events
kubectl top pods -l app=payments
kubectl top pods -l app=postgres
```

## 4. Incident Response

### First checks

```bash
kubectl get pods -o wide
kubectl argo rollouts get rollout gateway
kubectl get analysisrun
```

Then determine which user path is failing and inspect latency and 5xx by path.

### Common failure patterns

| Symptom | Likely cause | Action |
|---|---|---|
| `/pay` becomes slow but returns 200 | Payments latency | check Payments environment, p99 alert and dependency health |
| Gateway canary produces 5xx | bad release | abort Rollout, restore healthy manifest |
| `/events` returns 502 under load | Postgres saturation / Events pool pressure | reduce load, inspect Postgres CPU, fix query or increase capacity |
| reservations fail while reads still work | Redis unavailable | restore Redis and verify Events health |
| Postgres pod restarts | normal pod replacement with PVC | wait for DB readiness; no restore should be required |
| tables are missing | database storage/restore problem | restore latest valid dump and restart Events connections |

### Escalation

Treat sustained user-path 5xx above 0.5%, confirmed data loss, or an unavailable checkout path as an incident. Record UTC timestamps, actions, and recovery milestones for the postmortem.

## 5. Backup and Restore

### Current protection

- Postgres data: `postgres-data` PVC.
- Backup schedule: every 5 minutes.
- Backup format: `pg_dump -Fc`.
- Retention: five newest dump files.
- Lab 9 measured pod-restart recovery at 13 s with zero lost orders.

Check backup jobs:

```bash
kubectl get cronjob postgres-backup
kubectl get jobs
kubectl exec deployment/backup-inspector -- ls -la /backups
```

### Restore procedure

1. identify the current Postgres pod;
2. copy the newest valid dump into the pod;
3. run `pg_restore --clean --if-exists`;
4. restart Events so stale database connections are replaced;
5. verify `GET /events` returns HTTP 200;
6. verify `events`, `orders`, and `alembic_version`;
7. check that the current Alembic head is `c4f91a2d8e76`.

Commands:

```bash
POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)

kubectl cp /tmp/quickticket.dump "$POD:/tmp/backup.dump"

kubectl exec "$POD" -- \
  pg_restore -U quickticket -d quickticket \
  --clean --if-exists /tmp/backup.dump

kubectl rollout restart deployment/events
kubectl rollout status deployment/events --timeout=30s
```

The manual-dump recovery in Lab 9 took about 171 s and lost 614 orders because the available dump was around two minutes old. With the PVC, a normal Postgres pod replacement took 13 s and lost no orders.

## 6. Capacity Notes

Current healthy load is around 22 RPS. The first clear overload point appears near 28 RPS at 40 Locust users.

At that point:

```text
postgres   ~158m CPU
events      ~84m CPU
payments     ~9m CPU
redis        ~7m CPU
gateway      ~18-34m CPU per pod
```

The first capacity work should be:

1. remove the growing `/events` order aggregation;
2. give Postgres up to 500m CPU;
3. run two Events replicas;
4. separate local liveness from dependency readiness;
5. re-run the same Locust workload and confirm 5xx < 0.5% and p99 < 500 ms.
