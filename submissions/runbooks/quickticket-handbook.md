# QuickTicket SRE Handbook

> 2-page operational handbook: architecture, deployment, monitoring,
> incident response, backup/restore. Keep this file current — if a step
> here doesn't match reality, fix the file in the same PR.

---

## 1. Architecture

```
                        ┌────────────────────────────────────────────┐
                        │                 k3d cluster                │
 Locust Job ──────────► │  ┌──────────┐    ┌─────────┐  ┌────────┐  │
 (in-cluster,           │  │ gateway  │──► │ events  │  │ redis  │  │
  via kube-proxy        │  │ 5 repl.  │    │ 1 repl. │  │ holds  │  │
  across replicas)      │  └────┬─────┘    └────┬────┘  └────────┘  │
                        │       │               │                   │
  ArgoCD (3-min poll)   │  ┌────▼─────┐    ┌────▼────────────────┐  │
  ◄── k8s/*.yaml ───────┼── │payments │    │      postgres       │  │
  (git repo = source of │  │ 1 repl.  │    │ 1 repl. + PVC 1Gi   │  │
   truth for manifests) │  └──────────┘    │  (events, orders)   │  │
                        │                  └─────────────────────┘  │
                        │  monitoring/: Prometheus (scrapes gateway)│
                        │  argo-rollouts/: Rollout + AnalysisTpl    │
                        └────────────────────────────────────────────┘
```

- **gateway** (Go, 5 replicas, Argo Rollouts canary): public API, `/events`,
  `/events/{id}/reserve`, `/health`; calls events/payments.
- **events** (1 replica): event catalog + ticket inventory; the load
  bottleneck (see lab10 review §7).
- **payments** (1 replica): checkout; fails a configurable rate for chaos drills.
- **redis** (1 replica): ephemeral reservation holds (TTL), no persistence.
- **postgres** (1 replica, PVC 1Gi): `events` + `orders`; schema via Alembic.
- **Prometheus** in `monitoring/` scrapes gateway golden signals;
  **Argo Rollouts** drives canaries with an error-rate AnalysisTemplate
  (60 s delay, 3×20 s checks, failureLimit 1).

## 2. How to Deploy (GitOps)

Source of truth: this repo. CI (`.github/workflows/ci.yml`) builds on push to
`main`, pushes `ghcr.io/kvakz/quickticket-{gateway,events,payments}:<sha>`,
then commits updated image tags back to `k8s/*.yaml`. ArgoCD polls `main`
every 3 min and syncs.

```bash
# 1. Make the change on a branch, open a PR, get it merged to main.
git switch -c feature/my-change && git commit -am "feat: ..." && git push -u origin feature/my-change
#    → merge the PR on GitHub (never push app changes straight to main)

# 2. Watch CI: build + push images + tag-update commit (~2 min).
#    The tag-update commit is prefixed "ci:" so it doesn't trigger a rebuild.

# 3. ArgoCD picks it up within 3 min. For the gateway (canary strategy),
#    the Rollout shifts traffic 14% → 25% → 50% → 65% → 100% and an
#    AnalysisRun gates each step on the 5xx error rate.
kubectl argo rollouts get rollout gateway -w
#    "Healthy" + stable image = deployed. A failing AnalysisRun aborts and
#    holds the old revision — no manual rollback needed.

# 4. If you MUST roll back fast: revert the offending commit on main and push.
git revert <bad-sha> && git push        # ArgoCD syncs the revert in ≤ 3 min
```

Notes: Argo Rollouts install on k3s needs `kubectl apply --server-side`
(CRD annotation size limit). Image tags are content-addressed (`<sha>`), so
"redeploy" = a new tag, not a pull.

## 3. Monitoring

Prometheus UI: `kubectl -n monitoring port-forward svc/prometheus 9090`.
Core queries (all metrics are `gateway_*`, scraped from the gateway pods):

| Question | Query |
|----------|-------|
| Error rate (SLO input, target < 0.5%) | `sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m]))` |
| p95 / p99 latency (SLO: p99 < 500 ms) | `histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le))` |
| Throughput (capacity ceiling ≈ 37 RPS) | `sum(rate(gateway_requests_total[5m]))` |
| Saturation | `kubectl top pods -l app=gateway` (events is the hot one) |

Alerts (Lab 6): **High Error Rate** — 5xx > 5% for 2 m (critical);
**SLO Burn Rate** — budget burning > 6× sustainable for 5 m (warning), webhook
to the `quickticket-alerts` contact point. Known gap: no latency alert yet —
p99 degrades *before* 5xx appears under load (see lab10 review §6).

## 4. Incident Response

**On any page:** assume gateway 5xx until proven otherwise.

1. **Triage (2 min):** run the error-rate and p99 queries above. If 5xx are
   on one endpoint only, check that upstream (`events` for catalog/reserve,
   `payments` for checkout). `kubectl get pods` — look for CrashLoopBackOff,
   high RESTARTS, or a Rollout stuck mid-canary.
2. **Mitigate (fastest first):**
   - Canary went bad → `kubectl argo rollouts abort rollout gateway`
     (auto-reverts to the stable revision in ~seconds).
   - Bad commit already stable → `git revert <sha> && git push` (≤ 3 min to
     run) — do not try to hot-fix forward under an active SLO burn.
   - One wedged pod → `kubectl delete pod <pod>` (Deployment replaces it).
   - payments failing at a configured rate → restart the payments pod.
3. **Escalate** if: SLO burn > 6× for > 15 min, two services failing at once,
   or data loss is suspected (Postgres) → page the course TA / instructor
   with: what fired, what you ran, current error rate.
4. **Post-incident:** write the postmortem in the PR that fixes the root
   cause (what/when/detection/mitigation/root cause/action items). The Lab 6
   postmortem is the template.

## 5. Backup / Restore

**Backup** — `k8s/backup-cronjob.yaml`: CronJob `postgres-backup`, every 5
min, `pg_dump -Fc` (custom/compressed format) to a PVC, keeping the 5 most
recent dumps. Verify a fresh dump:

```bash
kubectl get jobs -l app=postgres-backup --sort-by=.metadata.creationTimestamp | tail -2
kubectl logs job/<latest-backup-job>     # "created /backups/quickticket_<ts>.dump"
```

**Restore** (tested in Lab 9):

```bash
POD=$(kubectl get pod -l app=postgres -o name)
kubectl cp /tmp/quickticket.dump $POD:/tmp/restore.dump
# inspect before touching live data:
kubectl exec $POD -- pg_restore --list /tmp/restore.dump
# restore into a scratch database first:
kubectl exec $POD -- createdb -U quickticket restore_check
kubectl exec $POD -- pg_restore -U quickticket -d restore_check --clean /tmp/restore.dump
# ...verify rows, then point the app at it (or restore into quickticket
# during a maintenance window; stops writes first).
```

Rules: never `pg_restore` over the live DB without a window; the dump is the
only copy of `orders` — if the backup CronJob is down > 30 min, that is
itself a page-worthy incident.
