# QuickTicket SRE Handbook

Two pages for a new team member. Everything here was measured on the k3d setup in Labs 4–10. Numbers refer to that environment.

## 1. Architecture

```text
            clients (in-cluster: mixedload, Locust)
                          │
                 Service gateway :8080  (kube-proxy, 5 endpoints)
                          │
        ┌──── Argo Rollout "gateway" ×5 (FastAPI, canary) ────┐
        │                                                     │
   Service events :8081                             Service payments :8082
   Deployment events ×1 ──► Service postgres :5432  Deployment payments ×1
        │                   Deployment postgres ×1 (PVC postgres-data, Recreate)
        └──► Service redis :6379
             Deployment redis ×1 (no probes, no persistence)

   monitoring/prometheus ── scrapes each gateway pod (pod IP, every 5 s)
   argocd/quickticket    ── syncs k8s/ from fork main (automated, no selfHeal)
   argo-rollouts         ── runs canary steps + AnalysisTemplate gateway-error-rate
```

- **Read path:** `GET /events` → gateway → events → Postgres (`events LEFT JOIN orders`, O(orders), the capacity limit: ~28 RPS today).
- **Checkout:** `POST /events/{id}/reserve` (Postgres + Redis hold, TTL 300 s) → `POST /reserve/{id}/pay` → payments `/charge` → events confirms the order.
- **Health:** gateway `/health` = events **and** payments healthy. events `/health` = Postgres **and** Redis. Both feed readiness *and* liveness. A dependency failure therefore cascades into a site outage with restarts (Lab 8, Lab 10).
- **Limits:** every container 200m CPU. Postgres hits that first.

## 2. How to deploy (GitOps)

1. Change code under `app/<service>/` or manifests under `k8s/`. Open a PR and merge to **`main` of the fork**: that is ArgoCD's source.
2. CI (`.github/workflows`, 34–98 s) builds multi-arch images `ghcr.io/r3v1k/quickticket-<svc>:<sha>`, and a bot commit bumps the tags in `k8s/` (`ci: update image tags … [skip ci]`).
3. ArgoCD (`quickticket`, `automated: {}`) syncs within its 3-min poll. Push → synced was **92 s** for the full pipeline.
4. **gateway** goes out as a canary: `setWeight 20` → pause 20 s → **analysis** `gateway-error-rate` (canary 5xx ratio < 5 %, 3 × 20 s after a 60 s delay, 1 failure allowed) → 50 % → pause → 100 %. A failing analysis aborts by itself (measured: 104 s after the canary pod appeared).
5. Watch it: `kubectl argo rollouts get rollout gateway --watch`. Emergency brake: `kubectl argo rollouts abort gateway` takes traffic off the canary in **< 1 s**. Then `git revert` the change; the revert takes **~3.5 min** through GitOps, otherwise the next sync re-applies it.

⚠️ **Before merging anything to `main`**, check that `main` already contains the gateway **Rollout** and the Postgres **PVC**. Today it does not (they live in the lab PRs). A sync from today's `main` would create a second `app=gateway` Deployment and drop the Postgres volume.

## 3. Monitoring

In-cluster Prometheus: `kubectl -n monitoring port-forward svc/prometheus 9091:9090` (UI), or with no port-forward: `kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=…'`.

| Question | Query |
|---|---|
| Traffic | `sum by (path) (rate(gateway_requests_total{path!="/health"}[1m]))` |
| Errors (SLO 99.5 % non-5xx) | `sum(rate(gateway_requests_total{status=~"5..",path!="/health"}[5m])) / sum(rate(gateway_requests_total{path!="/health"}[5m]))` |
| Latency (SLO 95 % < 500 ms) | `histogram_quantile(0.99, sum by (le,path) (rate(gateway_request_duration_seconds_bucket[1m])))` |
| Canary vs stable | same queries `by (rs_hash)` |
| Saturation | `kubectl top pods`; Postgres throttling: `kubectl exec deploy/postgres -- cat /sys/fs/cgroup/cpu.stat` |

Known blind spots: there are **no alert rules in the in-cluster Prometheus**. `up` stays 1 while pods are NotReady. `/reserve` 500s caused by events errors are not counted (gateway handler bug). Throughput drops and slow-but-successful requests page nobody. Gateway `/health` 503s at idle are noise. Always look at user paths (`path!="/health"`).

## 4. Incident response

**Triage (first 5 minutes):**
1. `kubectl get pods -o wide` and `kubectl get rollout gateway`. Are pods NotReady, restarting, or in CrashLoop back-off?
2. `kubectl exec -n monitoring deployment/prometheus -- wget -qO- http://gateway.default:8080/health`. Which dependency does the gateway call `down`/`degraded`?
3. Errors and latency **by path** (queries above). Only `/pay` → payments. All paths → events/Postgres/Redis.
4. Count the failures, don't read the tail: `kubectl logs deploy/events --since=5m | grep -c PoolError`, `… | grep -i redis`.
5. Recent change? `kubectl argo rollouts get rollout gateway`, ArgoCD `status.history`, `kubectl get rs`.

**Known failure modes → fixes:**

| Symptom | Likely cause | Action |
|---|---|---|
| all paths `000`/connection refused, gateway 0/5 Ready | events NotReady (Redis or Postgres down) → transitive gateway `/health` | restore the dependency; events restarts itself via liveness (back-off up to ~2 min) |
| `/events` 502 + events `PoolError` under load | Postgres CPU-throttled at 200m, pool exhausted | shed load / raise the Postgres limit; long-term: fix the `/events` aggregate |
| `/pay` slow or 504 at 5.0 s, 0 errors at 2 s | payments latency | check payments env (`PAYMENT_LATENCY_MS`); **reconcile charges vs orders** after any 504s |
| new canary erroring | bad release | `kubectl argo rollouts abort gateway`, then `git revert` |
| events up, `/events` 502, tables missing | Postgres lost its data | §5 restore |

**Escalation:** on-call (author) → if user-facing 5xx > 0.5 % for 5 min or any data loss: declare an incident, keep a timeline (UTC), write a blameless postmortem within 2 days (Lab 6 template).

## 5. Backup / restore

- **Automatic:** CronJob `postgres-backup` (`k8s/backup-cronjob.yaml`) runs every 5 min: `pg_dump -Fc` → `/backups/quickticket_<UTC>.dump` on PVC `postgres-backups`, keeps the 5 newest (sorted by name), `concurrencyPolicy: Forbid`. Worst-case RPO ≤ 5 min.
- **Durability:** Postgres data is on PVC `postgres-data`. A pod restart needs no restore (RTO 13 s, RPO 0, measured).
- **Manual backup:** `kubectl exec deploy/postgres -- pg_dump -U quickticket -Fc quickticket > qt.dump`. Use the pod's `pg_restore`: host clients older than 17 cannot read PG 17 dumps.

**Restore** (DB lost or table dropped):
1. `NEW=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)`
2. Wait for the **real** server. On an empty volume the entrypoint runs a temporary init server that `pg_isready` also accepts. Wait for `PostgreSQL init process complete` in `kubectl logs $NEW` (fresh volume), then `psql -c 'SELECT 1'`.
3. Copy the newest dump: `kubectl cp <dump> $NEW:/tmp/backup.dump` (from the `backup-inspector` pod or your laptop).
4. `kubectl exec $NEW -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump`
5. `kubectl rollout restart deployment/events` (stale pool connections), then verify that `GET /events` returns **200**. Do not trust readiness alone: events and gateway were "Ready 5/5" on a DB without tables.
6. Check `SELECT version_num FROM alembic_version` matches `alembic heads` (migrations live in `migrations/`, `alembic.ini` uses a port-forward on 15432 because host 5432 belongs to the docker-compose Postgres).

Never `kubectl delete pod --force` a database pod outside a drill. It removes the object without waiting for the old postmaster to stop.
