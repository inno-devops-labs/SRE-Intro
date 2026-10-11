# QuickTicket SRE Handbook

> Condensed reference for anyone picking up QuickTicket cold. Written as Lab 10's Bonus Task
> (Option B), pulled together from Labs 1-10's own submissions instead of written from scratch.
> Every number and command below was actually run at some point in this course, and is cited to
> the submission that produced it.

## Architecture

```
                         ┌─────────────┐
   client ──HTTP──▶ Service gateway ─┼─▶ Rollout gateway (5 pods, canary)
                         │  :8080      │    tcpSocket liveness · httpGet /metrics readiness
                         └──────┬──────┘
                                │  proxies to
                 ┌──────────────┼──────────────┐
                 ▼                              ▼
        Service events :8081            Service payments :8082
        Deployment events (1 pod)       Deployment payments (1 pod)
        httpGet /events readiness       httpGet /health (deep) readiness+liveness
        tcpSocket liveness                       │
                 │                               │
        ┌────────┴────────┐                      │
        ▼                 ▼                      ▼
   Postgres (1 pod,   Redis (1 pod,        (calls out to nothing —
   PVC, Alembic-       event:*:held        payments is the fault-
   managed schema)     counters + TTL      injection surface:
                        reservations,      PAYMENT_FAILURE_RATE,
                        cleared by the     PAYMENT_LATENCY_MS)
                        `reaper` sidecar
                        every 60s)
```

- **Only `gateway` is an Argo Rollout** (`argoproj.io/v1alpha1`, canary strategy: 20% → analysis
  gate (`AnalysisTemplate gateway-error-rate` against Prometheus) → 50% → 100%). `events`,
  `payments`, `postgres`, `redis` are plain Deployments. `kubectl get deployment gateway` →
  `NotFound`; use `kubectl get rollout gateway` / `kubectl argo rollouts get rollout gateway`.
- **GitOps:** ArgoCD watches `path: k8s` on `main` of an **in-cluster** git server
  (`gitserver.gitops-src.svc.cluster.local:8080/quickticket.git`), not GitHub. This fork was never
  pushed to origin. `selfHeal: false`.
- **Monitoring:** in-cluster Prometheus + Grafana in namespace `monitoring`, golden-signals
  dashboard, two alert rules (error-rate only, see Monitoring section below).
- **Known architectural bug, not yet fixed (Lab 1 / Lab 8):** `gateway` calls `POST /charge`
  *before* `POST /reservations/{id}/confirm`. A payments outage or slow dependency can take a
  customer's money with zero orders created. Measured in Lab 8: 44.9 successful charges, 0 orders,
  in 2 minutes at `PAYMENT_LATENCY_MS=6000`.

## How to deploy

The exact flow a new team member follows to ship a `k8s/` change (from
`submissions/lab5.md` / `submissions/lab7.md`):

```bash
# 1. edit the manifest
$EDITOR k8s/gateway.yaml   # or events.yaml, payments.yaml, etc.

# 2. commit on your feature branch, merge to main as this repo already does per-lab

# 3. push to the in-cluster git remote (NOT origin/GitHub — this fork doesn't sync there)
kubectl port-forward -n gitops-src svc/gitserver 9418:8080 &
git remote add cluster http://127.0.0.1:9418/quickticket.git   # first time only
git push cluster HEAD:refs/heads/main

# 4. ArgoCD polls every ~180s and picks it up automatically, or force it now:
kubectl annotate application quickticket -n argocd argocd.argoproj.io/refresh=normal --overwrite
kubectl get application quickticket -n argocd -w    # watch for Synced / Healthy

# 5. if the change touches `gateway`, it automatically goes through the canary gate —
#    watch it without babysitting the TUI:
kubectl argo rollouts get rollout gateway
kubectl wait --for=jsonpath='{.status.phase}'=Successful \
  analysisrun/$(kubectl get analysisrun -o jsonpath='{.items[-1:].metadata.name}') --timeout=180s
```

**Gotchas that will bite:**
- `selfHeal: false` protects a *drifted* resource, not a *deleted* one. ArgoCD resurrects
  anything you `kubectl delete` within ~90s. Delete the file from `k8s/` and push instead of
  running `kubectl delete` directly, if you need something to stay gone.
- An imperative `kubectl set env`/`kubectl patch` fix is only a stop-gap. The next sync of
  *any* revision re-applies whatever git says. Close every incident with a git commit, verified
  `Synced` on the fixing revision (Lab 6's postmortem finding, learned the hard way).
- `k8s/gateway.yaml`'s `scaleTargetRef` for any HPA must be
  `{apiVersion: argoproj.io/v1alpha1, kind: Rollout, name: gateway}`. Pointing at `Deployment`
  gets `FailedGetScale`.

## Monitoring

| What | Where | Query pattern |
|---|---|---|
| Golden signals dashboard | Grafana `http://localhost:3000/d/quickticket-golden-signals` (`kubectl port-forward -n monitoring svc/grafana 3000:3000`) | rate/error/duration panels per service |
| Live error rate by path | Prometheus `http://localhost:9090` (`kubectl port-forward -n monitoring svc/prometheus 9090:9090`) | `sum by (status,path) (rate(gateway_requests_total[5m]))`: identifies blast radius in one query |
| Payments fault-injection state | `curl gateway:8080/health` (via port-forward) or `payments:8082/health` directly | body echoes `failure_rate` / `latency_ms` live |
| Deep dependency status | `gateway /health` | `checks.events` / `checks.payments` = `ok` \| `degraded` \| `down` |
| Charge/order divergence (no alert exists yet, check manually) | Prometheus | `payments_charges_total{result="success"}` vs. events' order count: a growing gap with no alert is the Lab 8-identified blind spot |

**Alerting gaps, known and unfixed (Lab 10 finding):** both existing Grafana-managed alerts key
off `gateway_requests_total{status=~"5.."}` only, a **High Error Rate** (>5% for 2m, critical)
and an **SLO Burn Rate** (30m window, warning). **There is no latency alert.** Lab 10's own load
test showed p99 latency climbing from ~10ms to 430ms while still under the 5% error threshold,
one load step before the system fell off a cliff into 12%+ 5xx. Add a latency-burn rule next to
the existing two before trusting this dashboard to catch a dependency that's slow but not failing
yet.

## Incident response (distilled from Lab 6)

**Runbook: QuickTicket High Error Rate**: severity critical, fires on
`sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) > 5%`
for 2 minutes.

1. **Confirm it's real:** `sum by (status,path) (rate(gateway_requests_total[5m]))` in Prometheus.
   Which path is failing tells you the blast radius (`/reserve/{id}/pay` only ⇒ payments;
   everything ⇒ a shared dependency).
2. **Which service:** `curl gateway:8080/health`. `down` = pod gone, `degraded` = pod up but its
   own dependency is broken, **all `ok` while 5xx continue** = the failure is inside a path
   `/health` doesn't exercise (e.g. injected charge failures, `/health` never calls `/charge`).
3. **Check dependencies directly:** `kubectl exec deploy/gateway -- python -c "import httpx;
   print(httpx.get('http://payments:8082/health').text)"`. A non-zero `failure_rate` in the body
   is the smoking gun for an injected drill; a real incident won't have one.
4. **Logs:** `kubectl logs deploy/{gateway,payments,events} --tail=30 --since=5m`. Look for
   `Payment failed (injected)`, `payments unreachable`, `pool exhausted`, `Redis unavailable`.
5. **Mitigate through git** (see How to Deploy above), not a raw `kubectl set env`, unless it's
   a genuine break-glass, in which case suspend automated sync first
   (`kubectl patch application quickticket -n argocd --type=merge -p '{"spec":{"syncPolicy":{"automated":null}}}'`),
   fix imperatively, then land the same fix in git and restore the sync policy.
6. **Confirm recovery on the metric** (1-minute window, not the alert's 5-minute one, to avoid
   waiting out the lag) and with a real end-to-end request, not just "pod is Running."

**Escalation:** 10 minutes without root cause, or error rate > 25% → escalate to on-call lead,
declare SEV-2. If `SLO Burn Rate` is also firing, freeze further deploys until the postmortem.

**Common causes table** (from `submissions/lab6.md`):

| Symptom | Identify | Fix |
|---|---|---|
| Payments pod down/crashlooping | `0/1` or CrashLoopBackOff; `/health` → `payments: down` | `kubectl rollout restart deploy/payments` |
| Payments injected failure rate | pods Healthy, `/health` shows `failure_rate > 0` | set `PAYMENT_FAILURE_RATE=0.0` via git |
| Payments slow → gateway timeouts | 5xx are **504**, p99 spikes to ≈ `GATEWAY_TIMEOUT_MS` | set `PAYMENT_LATENCY_MS=0` via git |
| Events pod down | `/health` → `events: down`; 5xx on `/events` and `/reserve` | `kubectl rollout restart deploy/events` |
| Events CPU-saturated under load (Lab 10 finding, not a "down" symptom, a slow one) | `kubectl top pods -l app=events` pinned near its 200m limit; 502/503/504 from `gateway`, `events` itself still Running | scale `events` replicas (see `submissions/lab10.md` §Task 2): this is a capacity problem, restarting the pod does not fix it |
| DB connection pool exhausted | events logs `connection pool exhausted` | raise `DB_MAX_CONNS`, restart events |
| Postgres/Redis down | `/health` → `postgres`/`redis: down`; everything 5xx | `kubectl rollout restart deploy/postgres` (or `redis`) |

## Backup / restore (condensed from Lab 9)

- **A PVC is not a backup.** It survives pod restarts, not volume deletion. Measured cost of
  finding this out the hard way: deleting the PVC lost **1271 orders** (only backup on disk was
  5m47s old).
- **Scheduled logical backups:** `k8s/backup-cronjob.yaml`, every 5 minutes
  (`schedule: "*/5 * * * *"`), `pg_dump -Fc` to a PVC (`postgres-backups`, applied separately via
  `labs/lab9/backup-storage.yaml`), atomic write (`.part` file renamed after success), keeps the
  last 5 dumps. This bounds RPO to **≤5 minutes** (mean ≈2.5 min) instead of "whenever a human
  last remembered to run `pg_dump`."
- **Restore:**
  ```bash
  kubectl cp <latest-dump> $POD:/tmp/backup.dump
  kubectl exec $POD -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
  ```
  Measured: sub-second for a database this size.
- **Measured recovery numbers, for calibrating expectations** (from `submissions/lab9.md`,
  `date -u +%H:%M:%S.%2N` timestamped drills):

  | Drill | RTO | RPO |
  |---|---|---|
  | Pod killed, PVC intact | 75.23s (10x slower than the pod restart itself: the `events` connection pool hung ~68s *after* Postgres was already Ready in 7.4s) | 0 rows |
  | Schema/data corrupted, restore from dump | 15.64s (Bonus, streamlined) | depends on dump age |
  | PVC destroyed entirely | 27.08s | 1271 rows (age of the backup at disaster time, 5m47s) |

  **Key lesson: a PVC removes RPO risk but not RTO risk.** Postgres itself came back in ~7s, but
  the *application* took 75s to be fully healthy again because of a hung connection pool
  downstream. RTO is an application-level measurement, not just "is the database pod Running."
- **Next step not yet done:** continuous archiving (PITR) would get RPO down to seconds, and
  streaming replication to a standby would get RTO from tens of seconds down to a few (a
  promotion, not a cold restore).

---
*Written for Lab 10. Every number above traces to a submission from Labs 1, 6, 7, 8, 9 or this
lab's own load tests: see the cross-referenced files for full proof-of-work.*
