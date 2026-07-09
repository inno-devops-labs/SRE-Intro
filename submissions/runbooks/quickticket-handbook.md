# QuickTicket — SRE Handbook

A one-stop operations reference for QuickTicket. Assumes the k3d cluster + GitOps setup from Labs 4–9.

## Architecture

```
            ┌───────────────┐
  client ─▶ │  gateway (×5) │  Argo Rollouts canary, :8080, exposes /metrics
            └──────┬────────┘
        ┌──────────┼───────────┐
        ▼          ▼           ▼
   ┌─────────┐ ┌──────────┐ (notifications — future)
   │ events  │ │ payments │
   │  :8081  │ │  :8082   │
   └────┬────┘ └──────────┘
   ┌────┴─────┬──────────┐
   ▼          ▼          ▼
┌────────┐ ┌───────┐  reserve-holds
│postgres│ │ redis │  in Redis
│  +PVC  │ └───────┘
└────────┘
```

- **gateway** — API edge; retries, circuit breaker to payments, rate limiter, Prometheus metrics
  (`gateway_requests_total`, `gateway_request_duration_seconds`). Runs as an **Argo Rollouts** Rollout (5
  replicas, canary strategy).
- **events** — catalog + reservations; owns the `events`/`orders` tables; reads reservation holds from Redis
  (⚠️ hard dependency, see Incident Response).
- **payments** — charge simulation; supports `PAYMENT_FAILURE_RATE` / `PAYMENT_LATENCY_MS` fault injection.
- **postgres** — source of truth, on a **PVC** (data survives pod restarts). **redis** — reservation holds.

## How to Deploy (GitOps flow)

1. Branch, change code/manifests, open a PR to `main`.
2. Merge → **CI** builds the image, pushes to GHCR, and bumps the image tag in `k8s/`.
3. **ArgoCD** (3-min poll) detects the manifest change and syncs it to the cluster.
4. For the gateway, Argo Rollouts runs the **canary**: 20 % → analysis → promote. A good version auto-promotes;
   a bad one (error-rate > 5 %) **auto-aborts** and stays on the stable ReplicaSet.
   - Manual controls: `kubectl argo rollouts get rollout gateway --watch`, `… promote gateway`,
     `… abort gateway`.
- **Fast rollback:** `kubectl argo rollouts abort gateway` (≈ 2–3 s — stable pods never went away). Prefer this
  over `git revert` (≈ 3 min through CI+ArgoCD) for a bad canary.

## Monitoring — what to check for what

In-cluster Prometheus (`monitoring` namespace). Port-forward the UI: `kubectl port-forward -n monitoring
svc/prometheus 9091:9090`.

| Question | Query |
|----------|-------|
| Throughput (RPS) | `sum(rate(gateway_requests_total[1m]))` |
| Error rate (5xx) | `sum(rate(gateway_requests_total{status=~"5.."}[1m])) / sum(rate(gateway_requests_total[1m]))` |
| Latency p99 per path | `histogram_quantile(0.99, sum by (le,path) (rate(gateway_request_duration_seconds_bucket[1m])))` |
| Per-pod distribution | `sum by (pod) (rate(gateway_requests_total[1m]))` |
| Saturation | `kubectl top pods -l app=events` (events is the bottleneck) |

**Golden signals:** watch error rate **and latency** (a slow-but-200 dependency won't raise the error rate —
this bit us in Lab 8). Alert on p99 latency, container CPU nearing limit, and backup freshness.

## Incident Response (distilled)

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| Spike in 5xx right after a deploy | Bad new version | `kubectl argo rollouts abort gateway` (instant rollback) |
| `/pay` slow / 504, reads fine | payments degraded/slow | Confirm with p99 per path; a tight gateway timeout fails fast; circuit breaker sheds load |
| `/events` **and** `/reserve` failing, `/health` = degraded | **Redis down** (cascades to the read path) | `kubectl scale deployment/redis --replicas=1`; restore holds are ephemeral |
| 502s under high load, events CPU ~limit | events capacity ceiling (~100 users / ~74 RPS) | Scale events replicas + raise CPU limit |
| API 5xx after DB recovery | events pool holds stale connections | `kubectl rollout restart deployment/events` |

Escalation: page on error-rate > 0.5 % for 5 min or p99 > 500 ms for 5 min; DB data-loss events are always P1.

## Backup / Restore (condensed, Lab 9)

- **Automated:** `postgres-backup` CronJob runs `pg_dump -Fc` every 5 min to the `postgres-backups` PVC,
  keeping the 5 newest dumps (RPO ≤ 5 min).
- **Manual backup:** `kubectl exec $(kubectl get pod -l app=postgres -o name) -- pg_dump -U quickticket -Fc quickticket > backup.dump`
- **Restore:**
  ```
  kubectl cp backup.dump <postgres-pod>:/tmp/backup.dump
  kubectl exec <postgres-pod> -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
  kubectl rollout restart deployment/events   # drop stale DB connections
  ```
- **RTO:** ~10 s for a pod loss (data on PVC, no restore); ~43 s if a full `pg_restore` is needed.
- **DR note:** the PVC protects against pod loss, **not** against PVC/node loss — the off-volume CronJob dumps
  are the second line of defence.
