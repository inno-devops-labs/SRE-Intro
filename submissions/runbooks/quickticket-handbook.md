# QuickTicket SRE Handbook

## 1. Architecture

```text
User / Load Generator
        |
        v
+----------------+
| Gateway        | 5 replicas, Argo Rollout
| :8080          |
+----------------+
   |          |
   v          v
+--------+  +----------+
| Events |  | Payments |
| :8081  |  | :8082    |
+--------+  +----------+
   |
   v
+----------+      +--------+
| Postgres |      | Redis  |
| PVC      |      | Holds  |
+----------+      +--------+

Monitoring:
Prometheus -> Grafana alerts/dashboards
ArgoCD -> GitOps sync
Argo Rollouts -> canary + AnalysisRun
```

QuickTicket is a small ticketing system deployed on Kubernetes. The `gateway` service is the public entry point and routes requests to `events` and `payments`. The `events` service stores event and order data in PostgreSQL. Redis is used for temporary reservation holds. Prometheus collects metrics, Grafana provides dashboards and alerts, ArgoCD handles GitOps deployment, and Argo Rollouts manages canary releases.

---

## 2. How to Deploy

The deployment flow is GitOps-based:

1. Make code or manifest changes locally.
2. Commit and push to the repository.
3. CI builds and publishes container images.
4. Kubernetes manifests are updated with the new image tag.
5. ArgoCD detects the Git change and syncs it into the cluster.
6. Argo Rollouts gradually updates the gateway using canary strategy.
7. AnalysisRun validates the rollout using Prometheus metrics.

Useful commands:

```bash
kubectl get pods,svc
kubectl get rollouts
kubectl argo rollouts get rollout gateway
kubectl get analysisrun
```

If a rollout fails:

```bash
kubectl argo rollouts abort gateway
kubectl argo rollouts promote gateway
kubectl describe rollout gateway
```

A normal deployment should end with all pods Ready and the gateway rollout showing all replicas available.

---

## 3. Monitoring

The main reliability signals are the golden signals:

- Traffic: request rate through the gateway.
- Errors: 5xx responses from the gateway.
- Latency: p95 and p99 request latency.
- Saturation: CPU and memory usage of gateway, events, payments, Redis, and PostgreSQL.

Important Prometheus queries:

```promql
sum(rate(gateway_requests_total[5m]))
```

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m]))
/
sum(rate(gateway_requests_total[5m]))
* 100
```

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

Important Kubernetes checks:

```bash
kubectl get pods
kubectl top pods -l app=gateway
kubectl top pods -l app=events
kubectl top pods -l app=payments
kubectl logs deployment/events --tail=50
kubectl logs rollout/gateway --tail=50
```

Key alerts:

- High Error Rate: gateway 5xx rate above 5% for 2 minutes.
- SLO Burn Rate: error budget burn rate above 6x.
- Pod readiness failures.
- Repeated liveness probe failures.

---

## 4. Incident Response

### Runbook: QuickTicket High Error Rate

Alert fires when gateway 5xx error rate is above 5% for 2 minutes.

#### Diagnosis

Check gateway health:

```bash
kubectl run smoke --image=curlimages/curl:latest --rm -i --restart=Never --quiet \
  --command -- curl -s http://gateway:8080/health
```

Check pods:

```bash
kubectl get pods
kubectl describe pod -l app=gateway
kubectl describe pod -l app=events
kubectl describe pod -l app=payments
```

Check logs:

```bash
kubectl logs -l app=gateway --tail=50
kubectl logs -l app=events --tail=50
kubectl logs -l app=payments --tail=50
```

Check recent rollout state:

```bash
kubectl get rollouts
kubectl argo rollouts get rollout gateway
kubectl get analysisrun
```

#### Common Causes

| Cause | How to Identify | Fix |
|------|-----------------|-----|
| Payments service failing | Payment requests return 5xx, payments logs show errors | Restart payments or restore correct env config |
| Events service failing | `/events` returns 5xx, events logs show DB/API errors | Restart events and check PostgreSQL |
| Bad canary release | AnalysisRun failed or rollout degraded | Abort rollout and return to stable ReplicaSet |
| PostgreSQL unavailable | Events logs show DB connection errors | Check Postgres pod, restore from backup if needed |
| Redis unavailable | Reservation endpoints fail | Restart Redis and clear stale holds if necessary |

#### Recovery Actions

Restart affected service:

```bash
kubectl rollout restart deployment/events
kubectl rollout restart deployment/payments
```

Abort bad gateway rollout:

```bash
kubectl argo rollouts abort gateway
```

Verify recovery:

```bash
kubectl get pods
kubectl get rollouts
kubectl run smoke --image=curlimages/curl:latest --rm -i --restart=Never --quiet \
  --command -- curl -s -o /dev/null -w "%{http_code}\n" http://gateway:8080/events
```

#### Escalation

If the service is not recovered within 10 minutes, escalate to the instructor or TA with:

- Current alert status.
- Affected endpoint.
- Recent rollout status.
- Last 50 lines of logs from the failing service.
- Recovery actions already tried.

---

## 5. Backup and Restore

PostgreSQL stores QuickTicket state. It should use a PersistentVolumeClaim so data survives pod restarts.

Check database state:

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -c '\dt'
```

Create backup:

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  pg_dump -U quickticket -Fc quickticket > /tmp/quickticket.dump
```

Verify backup:

```bash
ls -lh /tmp/quickticket.dump
file /tmp/quickticket.dump
```

Copy backup into Postgres pod:

```bash
POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)
kubectl cp /tmp/quickticket.dump $POD:/tmp/backup.dump
```

Restore backup:

```bash
kubectl exec $POD -- \
  pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
```

Verify restored data:

```bash
kubectl exec $POD -- psql -U quickticket -d quickticket \
  -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders;'
```

Restart events if it has stale DB connections:

```bash
kubectl rollout restart deployment/events
kubectl rollout status deployment/events --timeout=30s
```

### RTO and RPO

- RTO is the time from database failure to application recovery.
- RPO is the time between the latest successful backup and the failure.
- With only manual `pg_dump`, RPO can be large.
- With PVC and automated backup CronJob, RTO decreases because pod restarts no longer erase data, and RPO improves because backups are created regularly.

Automated backups should run as a Kubernetes CronJob every 5 minutes and keep only the latest 5 dumps.