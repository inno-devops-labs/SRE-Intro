# QuickTicket SRE Handbook

A one-stop reference for operating QuickTicket. Companion to the Lab 10 reliability
review.

## 1. Architecture

```
              (in-cluster clients / kube-proxy)
                         │
                    ┌────▼─────┐   Rollout, 5 replicas, canary strategy
                    │ gateway  │   :8080  /events /reserve /pay /health /metrics
                    └──┬────┬──┘
             ┌─────────┘    └──────────┐
        ┌────▼────┐               ┌────▼─────┐
        │ events  │  Deployment   │ payments │  Deployment (fault-injectable:
        │ :8081   │               │ :8082    │   PAYMENT_FAILURE_RATE / _LATENCY_MS)
        └──┬───┬──┘               └──────────┘
      ┌────▼┐ ┌▼─────┐
      │ pg  │ │redis │   postgres = Deployment + PVC (Lab 9); redis = reservation holds
      └─────┘ └──────┘
   Observability: in-cluster Prometheus (ns `monitoring`) scrapes gateway pods with an
   `rs_hash` label so canary vs stable can be told apart. Argo Rollouts controls gateway.
```
- **Reads** (`GET /events`) → gateway → events → Postgres (no Redis).
- **Reserve** (`POST /events/{id}/reserve`) → events → Redis hold (TTL) + Postgres.
- **Pay** (`POST /reserve/{id}/pay`) → payments (charge) → events (confirm order).
- **Known bottleneck:** `events` is a single replica capped at 200m CPU (see review §7).

## 2. How to Deploy (GitOps flow)

1. Branch `feature/labN` off `main`; make the change (app or `k8s/*.yaml`).
2. Push → **GitHub Actions CI** builds+pushes 3 images to `ghcr.io` tagged with the
   commit SHA, then commits the new tags back into `k8s/` (`ci:` guard prevents a loop).
3. **ArgoCD** (`quickticket` app) polls Git (≤3 min) and syncs `k8s/` to the cluster.
4. For the gateway, the sync updates the **Argo Rollout**, which runs a **canary**:
   20% → analysis (Prometheus error-rate) → auto-promote if healthy, auto-abort if 5xx
   spikes. Watch: `kubectl argo rollouts get rollout gateway --watch`.
5. Manual controls: `kubectl argo rollouts promote|abort gateway`.
> ghcr images must be **public** (packages settings) or a `read:packages` PAT secret;
> the `gh` OAuth token lacks package scopes.

## 3. Monitoring — what to check

Port-forward Prometheus: `kubectl port-forward -n monitoring svc/prometheus 9091:9090`.

| Question | Query |
|----------|-------|
| Error rate (SLO) | `sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m]))` |
| p99 latency / path | `histogram_quantile(0.99, sum by (le,path)(rate(gateway_request_duration_seconds_bucket[5m])))` |
| Traffic per path | `sum by (path)(rate(gateway_requests_total[1m]))` |
| Canary vs stable | filter by `rs_hash="<pod-template-hash>"` |
| Saturation | `kubectl top pods -l app=events` (watch for CPU pinned at limit) |

**Alerts (Grafana, Lab 6):** `High Error Rate` (5xx > threshold, critical) and
`SLO Burn Rate` (warning). **Gap to close:** add a **p99-latency** and an **events
CPU-saturation** alert — error-rate alone misses slow-but-200 degradation.

## 4. Incident Response

**Elevated gateway 5xx:**
1. `curl -s http://localhost:3080/health | python3 -m json.tool` — which dependency is
   `down`/`degraded`?
2. Check the culprit directly (`:8082/health` payments, `:8081/health` events) and
   `sum by (status)(rate(gateway_requests_total[2m]))` (502/504 = downstream, 500 = gateway).
3. Common causes & fixes:
   | Symptom | Cause | Fix |
   |---------|-------|-----|
   | 502 on `/pay`, payments down | payments outage | `kubectl scale deploy/payments --replicas=1` |
   | 502 on everything, events NotReady | events/Redis down → readiness cascade | restore Redis; use shallow readiness |
   | 504 on `/pay`, health OK | payments slow (> timeout) | check `PAYMENT_LATENCY_MS`; add breaker |
   | Rising 5xx during a deploy | bad canary | `kubectl argo rollouts abort gateway` (instant) |
4. **Escalate** if unresolved in 10 min → on-call SRE / course TA (@Naghme98).

## 5. Backup & Restore (from Lab 9)

- **Automated backups:** `postgres-backup` CronJob runs `pg_dump -Fc` every 5 min to the
  `postgres-backups` PVC, keeping the 5 newest dumps.
- **Manual backup:** `kubectl exec <pg-pod> -- pg_dump -U quickticket -Fc quickticket > dump`.
- **Restore:** `kubectl cp dump <pg-pod>:/tmp/b.dump && kubectl exec <pg-pod> -- pg_restore
  -U quickticket -d quickticket --clean --if-exists /tmp/b.dump`, then
  `kubectl rollout restart deployment/events` (drop stale connections).
- **Disaster (pod loss):** data now survives on the **PVC** (RTO ≈ pod restart, ~3s, no
  restore). Without the PVC the pod comes back empty (RTO ≈ pg_restore + reseed, ~11s,
  and you lose everything since the last dump — RPO = dump age).
