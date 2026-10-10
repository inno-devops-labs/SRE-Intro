# QuickTicket SRE Handbook

A distilled operations guide for QuickTicket on k3d. Everything below is grounded
in the live cluster and the `k8s/` + `monitoring/` manifests.

## 1. Architecture

```
                 ┌───────────────────────────────────────────────┐
   client ─────► │ gateway  (Rollout, 5 replicas, :8080)         │
                 │   env: EVENTS_URL, PAYMENTS_URL, TIMEOUT=5000 │
                 └───────┬───────────────────────┬───────────────┘
                         │                        │
             ┌───────────▼────────┐    ┌──────────▼──────────┐
             │ events (:8081, ×1) │    │ payments (:8082, ×1)│
             └─────┬────────┬─────┘    └─────────────────────┘
                   │        │
        ┌──────────▼──┐  ┌──▼───────────┐
        │ redis :6379 │  │ postgres :5432│──► PVC postgres-data (1Gi)
        │ (holds)     │  │ (single pod)  │──► CronJob postgres-backup */5
        └─────────────┘  └───────────────┘        └──► PVC postgres-backups (keep 5)

  monitoring ns: prometheus (:9090) scrapes gateway; the Argo Rollouts
  AnalysisTemplate queries it during every canary.
```

- **gateway** — public edge, `Rollout` with 5 replicas; fans out to events + payments; translates downstream failures into HTTP status.
- **events** — reads/reserves; the DB workhorse and busiest pod; **single replica** (SPOF).
- **payments** — charge path only; idle unless `/pay` traffic.
- **redis** — reservation holds (≈300s TTL); **soft-critical**: down ⇒ `/reserve` 5xx.
- **postgres** — single pod, data on the `postgres-data` PVC (survives pod restarts, Lab 9).
- Limits are modest (`50m/200m` CPU on gateway/events/payments/postgres; redis none) — see the Lab 10 review; events throttles at ~90% of its 200m limit under load.

## 2. How to Deploy (GitOps flow)

A new team member ships a change like this:

1. **Commit to `main`.** No manual `docker build`, no `kubectl set image`.
2. **CI (GitHub Actions, `ci.txt`)** builds and pushes 3 images to ghcr:
   `ghcr.io/tulup-404/quickticket-{gateway,events,payments}:<git-sha>`, then commits
   `ci: update image tags to <sha>` back into the manifests (the tag is the source of truth).
3. **GitOps sync — ArgoCD** (`argocd/quickticket` Application from Lab 5: `Sync
   Policy: Automated`, source path `k8s`, ~3-min poll) detects the changed manifest
   in Git and applies it to the cluster. Manual drift is reconciled back to Git.
4. **Progressive delivery — Argo Rollouts** picks up the new gateway image and runs the canary from `k8s/gateway.yaml`:
   `20% → pause 20s → AnalysisRun → 50% → pause 20s → 100%`.
5. **Automated gate — `gateway-error-rate` AnalysisTemplate** (`k8s/analysis-template.yaml`)
   queries Prometheus for the canary's 5xx ratio (3× 20s windows, `initialDelay 60s`).
   If 5xx ≥ 5%, the rollout **auto-aborts** and the last stable ReplicaSet keeps serving —
   no human action, no user-facing failure.
6. **Verify / drive manually if needed:**
   ```bash
   kubectl argo rollouts get rollout gateway          # watch state / analysis
   kubectl argo rollouts promote gateway              # skip a pause (rarely needed)
   kubectl argo rollouts abort gateway && kubectl argo rollouts undo gateway   # emergency
   ```

## 3. Monitoring

In-cluster Prometheus lives in the `monitoring` namespace (`svc/prometheus:9090`). It is the
same Prometheus the canary gate reads. Query it via:

```bash
kubectl exec -n monitoring deploy/prometheus -- \
  wget -qO- 'http://localhost:9090/api/v1/query?query=<PROMQL>'
# or: kubectl port-forward -n monitoring svc/prometheus 9090:9090
```

Golden signals (check these first):

| Signal | Query |
|--------|-------|
| Traffic | `sum(rate(gateway_requests_total[1m]))` |
| Errors (5xx) | `sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m]))` |
| Latency (p95) | `histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))` |
| Saturation | `events_db_pool_size` (DB connections in use, max 10) |

Recorded SLIs (`monitoring/prometheus/rules.yml`): `gateway:sli_availability:ratio_rate5m`
(**SLO 99.5%**), `gateway:sli_latency_500ms:ratio_rate5m`, `gateway:error_budget_burn_rate:ratio_rate5m`.

## 4. Incident Response

**Primary alert:** `QuickTicket High Error Rate` — gateway 5xx > 5% over 5m (fires ~2 min after).

**Triage:**

1. **Isolate the failing path:** `sum(rate(gateway_requests_total{status=~"5.."}[1m])) by (path)`.
   `/reserve/{id}/pay` ⇒ payments; `/reserve` ⇒ redis; all paths ⇒ gateway itself.
2. **Check dependency health:** `curl /health` (in-cluster) and the suspect service's logs.
3. **Common branches (from the Lab 6 postmortem):**
   - *Payments reachable but every charge 500* — `/health` looks OK but logs show 500s on `/charge`; check `PAYMENT_FAILURE_RATE`, restore/roll back payments.
   - *Redis down* — `/reserve` returns 5xx, `/health` = degraded/503; `kubectl scale deploy/redis --replicas=1`.
   - *Bad deploy* — `kubectl argo rollouts abort gateway && kubectl argo rollouts undo gateway`.
   - *Pods restarting under load* — liveness probe collapse (Lab 10); shed load / raise limits; the durable fix is a lightweight `/livez` + `timeoutSeconds`.

**Escalation:** page on-call → if a single downstream (payments/redis) is the cause, mitigate that
service; if deploy-related, abort/undo the rollout; if the DB is impaired, go to §5.

**Known detection gap:** the single 5%/5m alert detected the Lab 6 payments incident only after
**~8 min**. Action item: add multi-window burn-rate alerting (fast ~2%/1m + slow 5%/5m).

## 5. Backup / Restore

**Backups (automated, Lab 9):** `CronJob/postgres-backup` runs every 5 min, `pg_dump -Fc` to
`/backups/quickticket_<ts>.dump` on the `postgres-backups` PVC, retaining the **5 newest** dumps.
Data itself lives on the `postgres-data` PVC, so a Postgres pod restart loses nothing.

```bash
# list available dumps (newest first)
kubectl exec deploy/backup-inspector -- ls -1t /backups
```

**Restore procedure:**

```bash
# 1. pick the dump to restore
DUMP=$(kubectl exec deploy/backup-inspector -- ls -1t /backups | head -1)

# 2. restore into Postgres (clean + recreate objects). Run from a pod with the
#    dump mounted and DB access, or copy the dump into the postgres pod first:
kubectl exec deploy/backup-inspector -- \
  sh -c "PGHOST=postgres PGUSER=quickticket pg_restore --clean --if-exists \
         -d quickticket /backups/$DUMP"

# 3. verify
kubectl exec deploy/postgres -- psql -U quickticket -d quickticket -c '\dt'
```

Recovery time: seconds-to-minutes (restore a ~1Gi-max dump into the same-cluster Postgres).
For a full pod loss, the `postgres-data` PVC re-attaches automatically — restore from a dump
only for logical corruption or accidental data loss.
