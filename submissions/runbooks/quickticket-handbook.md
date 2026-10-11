# QuickTicket SRE Handbook

*For a new team member on their first on-call shift. Reflects the k3d deployment as of 2026-10-11 (Labs 1–10).*

## 1. Architecture

```
                     ┌────────────── k3d cluster "quickticket" (1 node) ───────────────┐
 client ──► Service  │  gateway  (Argo Rollout, 5 pods, :8080)                          │
            gateway ─┼─►  ├── httpx ──► events   (Deployment ×1, :8081) ──┬─► postgres  │
                     │    │                     psycopg2 pool (max 10)    │   (×1, PVC  │
                     │    │                                               │   postgres- │
                     │    │                                               │   data)     │
                     │    │                                               └─► redis (×1)│
                     │    └── httpx ──► payments (Deployment ×1, :8082)    (holds, TTL) │
                     │                                                                  │
                     │  monitoring/prometheus ── scrapes gateway/events/payments /metrics│
                     │  argocd (app "quickticket": repo k8s/ → ns default)              │
                     │  argo-rollouts controller + AnalysisTemplate gateway-error-rate  │
                     │  CronJob postgres-backup (*/5) ──► PVC postgres-backups          │
                     └──────────────────────────────────────────────────────────────────┘
```

- **gateway** is the only public entry point. It owns the circuit breaker for payments, the 5 s upstream timeout (`GATEWAY_TIMEOUT_MS`) and the `gateway_requests_total` / `gateway_request_duration_seconds` metrics that the SLIs are built on.
- **events** serves `GET /events` (from Postgres) and `POST /events/{id}/reserve` (seat holds in Redis). It is a **single pod and the capacity bottleneck** together with Postgres. The healthy ceiling is ~19 RPS (Lab 10).
- **payments** is a fake card processor. Failure and latency are injectable via `PAYMENT_FAILURE_RATE` / `PAYMENT_LATENCY_MS`.
- **Postgres** has `events`, `orders` and `alembic_version`, and the schema is managed by Alembic (`migrations/`). The data lives on PVC `postgres-data` (Lab 9). Before that, every pod restart wiped the DB.
- **SLOs:** availability **99.5 %** non-5xx; latency **95 % < 500 ms** (gateway, excluding `/health`).

## 2. How to deploy (GitOps)

1. Branch from `main`, change code and/or `k8s/*.yaml`, open a PR, get a review, merge.
2. **CI** (`.github/workflows/ci.yml`, ~50 s) builds the 3 images, pushes `ghcr.io/g-0-rg/quickticket-<svc>:<sha>`, and commits `ci: update image tags to <sha>` to `main`.
3. **ArgoCD** (app `quickticket`, path `k8s/`) picks up the new manifests: ≤ 3 min with auto-sync, or by hand. Check with `kubectl get application -n argocd quickticket`.
   > ⚠️ Auto-sync was disabled in Lab 7. Until it's re-enabled, sync explicitly (`argocd app sync quickticket`) or re-enable it: `kubectl patch application quickticket -n argocd --type merge -p '{"spec":{"syncPolicy":{"automated":{}}}}'`.
4. **Argo Rollouts** canaries the gateway: 20 % → 20 s pause → **AnalysisRun** (canary 5xx ratio < 5 %, 3 × 20 s, after 60 s delay) → 50 % → 20 s → 100 %. A failed analysis aborts automatically. Watch it with `kubectl argo rollouts get rollout gateway --watch`.
5. **Rollback:** `kubectl argo rollouts abort gateway` (traffic back on stable in **< 1 s**), **then** `git revert` the bad commit. Otherwise the next sync re-deploys it.
6. **Schema changes:** Alembic only, backwards-compatible first (nullable columns, no `NOT NULL` without a default). `kubectl port-forward svc/postgres 5432:5432 & alembic upgrade head`. Take a `pg_dump` first.
7. **Never `kubectl apply` a manifest from a branch that is behind the cluster.** For example, a `postgres.yaml` without the PVC would silently move the DB back onto ephemeral storage.

## 3. Monitoring: what to check for what

Prometheus: `kubectl port-forward -n monitoring deploy/prometheus 9090` → http://localhost:9090 (Grafana golden-signals dashboard: `docker-compose.monitoring.yaml`, Lab 3).

| Question | Query |
|---|---|
| Are users getting errors? | `sum(rate(gateway_requests_total{status=~"5.."}[1m])) / sum(rate(gateway_requests_total[1m]))` |
| Which endpoint / code? | `sum by (path,status) (rate(gateway_requests_total{status=~"5.."}[1m]))` |
| Is it slow? | `histogram_quantile(0.99, sum by (le,path) (rate(gateway_request_duration_seconds_bucket[5m])))` |
| SLO burn | `(1 - sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m]))) / 0.005` (> 6 = warning) |
| Traffic | `sum(rate(gateway_requests_total{path!="/health"}[1m]))`, normal ≈ 17 RPS, **ceiling ≈ 19 RPS** |
| Is a canary bad? | the same error ratio with `{rs_hash="<canary hash>"}` |
| Saturation (CPU) | `kubectl top pods`, plus throttling: `kubectl exec deploy/postgres -- cat /sys/fs/cgroup/cpu.stat` (`nr_throttled`) |

**Known blind spots. Don't trust a green dashboard blindly:**
- 500s from `/reserve` with a non-JSON body crash the gateway handler and are **not counted** (17–26 % of failures under overload). Check `kubectl logs -l app=gateway | grep -c "Exception in ASGI"`.
- Prometheus has no PVC, so history resets on every cluster restart.
- There is no latency alert, and no pod-restart or "zero endpoints" alert.

## 4. Incident response

**Alerts (Lab 6):** *High Error Rate*: 5xx > 1 % for 2 m (critical). *SLO Burn Rate*: > 6× for 5 m (warning). Check that a notification actually arrived. Rules without an explicit receiver silently go nowhere (Lab 6 bug).

**First 5 minutes:**
1. Acknowledge, open a channel, write down the start time. One person is incident commander, one investigates.
2. **Scope:** error ratio by `path,status` (§3). Is it all endpoints (→ events/Postgres/probes) or only `/pay` (→ payments)?
3. **Pods:** `kubectl get pods -o wide` (restarts? `0/1`?) and `kubectl get endpoints events gateway` (empty = total outage).
4. **Dependencies:** `kubectl exec $(kubectl get pod -l app=gateway -o name | head -1) -- python3 -c "import urllib.request as u;print(u.urlopen('http://localhost:8080/health').read())"` → `events/payments: ok|degraded|down`, circuit-breaker state.
5. **Did something just change?** `kubectl argo rollouts get rollout gateway` and `kubectl rollout history deploy/<svc>`. **If yes → abort/rollback first, debug later.**

**Known failure modes → mitigation:**

| Symptom | Likely cause | Mitigate |
|---|---|---|
| Canary rollout in progress + rising 5xx | bad version | `kubectl argo rollouts abort gateway`, then `git revert` |
| `/events` 502 + events logs `relation "events" does not exist` | DB empty (lost PV / new cluster) | restore from backup (§5) |
| events logs `PoolError: connection pool exhausted`, Postgres CPU near 200m | overload above ~19 RPS | shed load; scale `events`; raise Postgres CPU (Lab 10: 1 CPU + events ×3 → 74 RPS clean) |
| Everything 5xx, pods restart-looping, Redis down | probe cascade (Lab 8) | bring Redis back first; pods recover by themselves |
| `/pay` 504 or 500, rest OK | payments slow/failing; circuit open | check `PAYMENT_*` env and payments logs; **504 ≠ not charged** (Lab 8: 33 orphan charges), so reconcile |
| events `0/1` right after a Postgres restart | stale pool connections | wait ~20 s (the pool self-heals), then `kubectl rollout restart deploy/events` |

**Escalation:** not mitigated in **10 min**, or data loss suspected → page the secondary on-call/service owner (course: TA). Any SLO-burning incident → **blameless postmortem within 48 h** (template: Lab 6), with action items that have owners.

## 5. Backup / restore (Lab 9, condensed)

- **Automatic:** CronJob `postgres-backup` runs `pg_dump -Fc` every 5 min → PVC `postgres-backups`, keeps the 5 newest (**RPO ≤ 5 min, window 25 min**). Check it: `kubectl get cronjob postgres-backup` (LAST SCHEDULE < 5 min) and `kubectl exec deploy/backup-inspector -- ls -la /backups`.
- **Manual, before risky changes:** `kubectl create job --from=cronjob/postgres-backup pre-change-$(date +%s)`.
- **Pod died, PV intact:** do nothing. Postgres restarts on its PVC in ~2 s (readinessProbe `pg_isready`), and `events` heals in ~20 s. **RPO 0.**
- **Data lost (DROP, corruption, empty PV) → restore:**
  ```bash
  F=$(kubectl exec deploy/backup-inspector -- sh -c 'ls -1t /backups/*.dump | head -1')
  kubectl exec deploy/backup-inspector -- cat "$F" > latest.dump
  POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)
  kubectl wait --for=condition=Ready pod/$POD --timeout=120s        # real readiness (pg_isready)
  kubectl cp latest.dump $POD:/tmp/restore.dump
  kubectl exec $POD -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/restore.dump \
    || echo "RESTORE FAILED, stop here and escalate"   # Lab 9: never continue past a failed restore
  kubectl exec $POD -- psql -U quickticket -d quickticket -c 'SELECT count(*) FROM orders' -c 'SELECT * FROM alembic_version'
  ```
  Then smoke-test `/events`, and record the RTO and the number of orders lost (rows written after the dump time).
- **Measured:** no PVC + manual dump → RTO 70.8 s, 1102 orders lost. PVC → RTO ≈ 20 s, 0 lost. DROP + restore from fresh dump → ~6 s, 63 orders lost.
- **Gap:** data and backups are on the same node/disk (`local-path`). Off-node copies (object storage) are still an open action item.
