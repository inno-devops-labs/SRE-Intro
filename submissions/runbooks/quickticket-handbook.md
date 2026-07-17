# QuickTicket SRE Handbook

## Architecture

```
Client → gateway (Rollout, 5 replicas, canary strategy)
           ├─→ events (Deployment, 1 replica) ──→ postgres (PVC-backed) + redis
           └─→ payments (Deployment, 1 replica)
```

- **gateway**: API router. Deployed as an Argo Rollout with canary strategy (20% → 60% → 100%). Circuit breaker + retry scaffolding present but not fully implemented (Lab 11 TODO).
- **events**: Manages event listings and ticket reservations. Uses Postgres for durable data, Redis for TTL-based reservation holds.
- **payments**: Stateless charge processor. Supports fault injection via `PAYMENT_FAILURE_RATE` / `PAYMENT_LATENCY_MS` env vars — useful for chaos testing.
- **postgres**: Backed by a 1Gi PVC (`postgres-data`) — survives pod restarts. Automated backups via CronJob every 5 minutes, 5-backup rotation, stored on a separate `postgres-backups` PVC.
- **Monitoring**: Prometheus scrapes all 3 services' `/metrics` endpoints (in-cluster, `monitoring` namespace). No in-cluster Grafana — dashboards live in docker-compose only (gap noted below).
- **CI/CD**: GitHub Actions builds + pushes images to ghcr.io on every push to main, auto-updates K8s manifests with the new SHA tag. ArgoCD (when network-reachable) syncs from Git automatically.

## How to Deploy

1. Push code to `main` on your fork.
2. CI (`​.github/workflows/ci.yml`) builds all 3 images, pushes to `ghcr.io/<username>/quickticket-*:<sha>`, and commits the updated image tags back to `k8s/*.yaml`.
3. ArgoCD (polling every 3 min, or `argocd app sync quickticket` for instant) detects the Git change and applies it to the cluster.
4. For the `gateway` Rollout specifically: new image triggers a canary — 20% traffic for 20s, then (if AnalysisTemplate passes) 50%, then 100%. Auto-aborts on elevated error rate.
5. To manually intervene: `kubectl argo rollouts promote gateway` / `kubectl argo rollouts abort gateway`.

**Manual deploy (no CI/CD, for quick local testing):**
```bash
docker build -t quickticket-<service>:v1 ./app/<service>
k3d image import quickticket-<service>:v1 -c quickticket
kubectl apply -f k8s/<service>.yaml
```

## Monitoring

| What to check | Where | Query / Path |
|---|---|---|
| Golden signals (docker-compose only) | Grafana `localhost:3001` | "QuickTicket — Golden Signals" dashboard |
| In-cluster metrics (k3d) | `kubectl port-forward -n monitoring svc/prometheus 9091:9090` | Prometheus UI or `curl localhost:9091/api/v1/query` |
| Request rate | Prometheus | `sum(rate(gateway_requests_total[1m]))` |
| Error rate | Prometheus | `sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m]))` |
| p99 latency per path | Prometheus | `histogram_quantile(0.99, sum by (le,path) (rate(gateway_request_duration_seconds_bucket[1m])))` |
| SLO availability | Prometheus recording rule | `gateway:sli_availability:ratio_rate5m` |
| Pod resource usage | `kubectl top pods -l app=<service>` | metrics-server (preinstalled in k3d) |

**Known gap:** No latency-based alert exists — only error-rate alerts. A slow-but-succeeding dependency (e.g., Lab 8's payment-latency experiment) produces near-zero error rate and would not page anyone. Add a `p99 > 500ms for 2m` alert rule.

## Incident Response

1. **Check overall health:** `curl http://gateway:8080/health` (in-cluster) — shows per-dependency status (`events`, `payments`, circuit breaker state).
2. **Identify the failing component** from the health check or from which golden signal fired (error rate vs latency vs saturation).
3. **Check logs:** `kubectl logs -l app=<service> --tail=50` or `docker compose logs <service> --tail=20 --since=5m` (compose setup).
4. **Common fixes:**
   - Service down → `kubectl rollout restart deployment/<service>` (or `docker compose start <service>`)
   - Bad canary deploy → `kubectl argo rollouts abort gateway` (instant rollback, <1s)
   - Bad manifest deploy → `git revert HEAD && git push` (ArgoCD syncs the revert, ~3-5 min)
   - DB connection issues → check `DB_MAX_CONNS`, consider `kubectl rollout restart deployment/events` to clear stale pooled connections
5. **Escalation:** if unresolved in 10 minutes, escalate to instructor/TA (student-project context) or on-call lead (production context).

## Backup / Restore

**Automated:** CronJob `postgres-backup` runs every 5 minutes, writes `pg_dump -Fc` output to the `postgres-backups` PVC, retains the 5 newest dumps (older ones auto-deleted).

**Manual backup:**
```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- pg_dump -U quickticket -Fc quickticket > backup.dump
```

**Restore procedure:**
```bash
POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)
kubectl cp backup.dump $POD:/tmp/backup.dump
kubectl exec $POD -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
```

**RTO/RPO summary:**
- With PVC (current state): pod restart alone preserves data — RTO ~10-15s, RPO = 0 for any pod-level failure.
- Full disaster (PVC lost / corrupted): RTO = time to `pg_restore` from latest dump (seconds for this DB size), RPO = up to 5 minutes (CronJob interval) worst case.
- Before the PVC fix: RTO was the same ~10-15s, but RPO was 100% — every pod restart wiped all data. This was the single highest-impact reliability fix made across all 10 labs.
