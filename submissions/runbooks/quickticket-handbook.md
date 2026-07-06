# QuickTicket SRE Handbook

## 1. Architecture

```
                              ┌──────────────────────────────────────────────┐
                              │              Gateway (:3080)                │
                              │     FastAPI · 5 replicas · Argo Rollout     │
                              │     Circuit breaker: payments (503 on fail) │
                              └────┬─────────────────────┬──────────────────┘
                                   │                     │
                           ┌───────▼──────────┐  ┌───────▼──────────┐
                           │    Events (:8081) │  │  Payments (:8082) │
                           │     FastAPI       │  │    FastAPI        │
                           │     1 replica     │  │    1 replica      │
                           │   DB_MAX_CONNS=10 │  │ Fault injection:  │
                           └────┬──────────┬───┘  │ FAILURE_RATE,     │
                                │          │      │ LATENCY_MS        │
                          ┌─────▼────┐ ┌───▼──┐  └───────────────────┘
                          │ Postgres │ │ Redis │
                          │ :5432    │ │ :6379 │
                          │ 17-alpine│ │ 7-alp.│
                          │ + PVC    │ │       │
                          └──────────┘ └───────┘
```

| Service | Port | Replicas | Dependencies | Key env vars |
|---------|:----:|:--------:|-------------|-------------|
| gateway | 3080 | 5 | events, payments | `GATEWAY_TIMEOUT_MS=3000` |
| events | 8081 | 1 | postgres, redis | `DB_MAX_CONNS=10` |
| payments | 8082 | 1 | (none) | `PAYMENT_FAILURE_RATE=0.0`, `PAYMENT_LATENCY_MS=10` |
| postgres | 5432 | 1 | PVC (1Gi) | `PGDATA`, `POSTGRES_PASSWORD=quickticket` |
| redis | 6379 | 1 | (none) | |

**Traffic composition:** 70% `GET /events` / 20% `POST /events/{id}/reserve` / 10% `POST /reserve/{id}/pay`

**Data model:** 5 seed events (100–500 tickets each). Reservations stored in Redis (TTL 300s). Orders in Postgres.

---

## 2. How to Deploy (GitOps)

### Deployment flow

```
git push → GitHub Actions CI → build & push images to ghcr.io → auto-update k8s manifests →
→ ArgoCD sync (poll 3 min) → Argo Rollout canary (20% → 50% → 100%) → AnalysisRun validates
```

### CI pipeline (`.github/workflows/ci.yml`)

Triggered on push to `main` (skips `ci:` commits to prevent loops):

1. Build gateway → `ghcr.io/<actor>/quickticket-gateway:<sha>`
2. Build events → `ghcr.io/<actor>/quickticket-events:<sha>`
3. Build payments → `ghcr.io/<actor>/quickticket-payments:<sha>`
4. `sed` updates image tags in `k8s/*.yaml`
5. Auto-commit `ci: update image tags to <sha>`

### Argo Rollout canary strategy

```text
Step 1: setWeight 20% → pause 20s
Step 2: analysis (5xx < 5% for 60s) → promote or abort
Step 3: setWeight 50% → pause 20s
Step 4: analysis (5xx < 5% for 60s) → promote or abort
Step 5: setWeight 100%
```

### Rollback

```bash
kubectl argo rollouts abort gateway
kubectl argo rollouts promote gateway     # force 100% to previous stable
```

### Start the full stack

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
```

Or on Kubernetes:

```bash
kubectl apply -f k8s/                    # apply all manifests
kubectl apply -f k8s/chart/              # or deploy via Helm chart
```

### DORA metrics (from Lab 10)

| Metric | Value | DORA Elite |
|--------|-------|:----------:|
| Deployment Frequency | ~1/day | On-demand |
| Lead Time | ~8-13 min | <1 day ✅ |
| Change Failure Rate | 33% | 0-15% ❌ |
| MTTR | ~13s | <1 hour ✅ |

---

## 3. Monitoring

### Golden-signals dashboard (Grafana)

Open Grafana at `http://localhost:3000` (admin/admin), dashboard **QuickTicket — Golden Signals** (`quickticket-golden-signals`).

| Panel | Description | PromQL |
|-------|-----------|--------|
| Request Rate | RPS by endpoint | `sum(rate(gateway_requests_total[1m])) by (path)` |
| Error Rate | 5xx as % of total | `sum(rate(gateway_requests_total{status=~"5.."}[1m])) / sum(rate(gateway_requests_total[1m])) * 100` |
| Service Health | Up/down status | `up` |

### Key PromQL queries

```promql
# Error rate (last 5 min)
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100

# p99 latency
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le))

# RPS per endpoint
sum(rate(gateway_requests_total[1m])) by (path)

# Per-pod CPU
kubectl top pods
```

### Alert rules (Grafana-managed)

| Alert | Severity | Condition | For | Action |
|-------|:--------:|-----------|:---:|--------|
| QuickTicket High Error Rate | critical | 5xx > 5% | 2m | Webhook → Telegram/Slack |
| QuickTicket SLO Burn Rate | warning | SLO burn > 6× | 5m | Webhook → Telegram/Slack |

**SLO:** 99.5% availability (error budget = 0.5% 5xx). See `submissions/lab6.md` for full alert configuration.

---

## 4. Incident Response

### Triage (30 seconds)

```bash
# 1. Health check
curl -s http://localhost:3080/health | python3 -m json.tool

# 2. Find failing service
curl -s http://localhost:8081/health          # events
curl -s http://localhost:8082/health          # payments
docker compose logs <service> --tail=30 --since=5m

# 3. Check resource usage
kubectl top pods
```

### Runbook: High 5xx Error Rate

**Diagnosis:**
- `/health` returns `degraded` or one service `down`
- Gateway returns 502 (events/DB problem) or 503 (payments circuit breaker)

**Resolution:**

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `/events` → 502 | Events DB pool exhausted | `kubectl rollout restart deployment/events` |
| `/health` → 503 | Payments circuit breaker | `kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0` |
| `POST /reserve` → 500 | Postgres overloaded | Check `kubectl top pods`, restart postgres if needed |
| Health check → `down` | Redis unreachable | `kubectl rollout restart deployment/redis events` |

**Verification:** Grafana → Normal (green), `/health` → `healthy`, error rate < 5%

### Runbook: Redis Down

**Diagnosis:** `/health` → `events: down (redis)`. `redis-cli PING` fails.

**Resolution:**
```bash
kubectl rollout restart deployment/redis
kubectl rollout status deployment/redis
kubectl rollout restart deployment/events    # reconnect
```

### Runbook: Postgres Data Loss

**Diagnosis:** Events return 500, tables missing.

**Resolution:**
```bash
POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)
kubectl cp /tmp/quickticket.dump $POD:/tmp/
kubectl exec $POD -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/quickticket.dump
kubectl rollout restart deployment/events
```

### Escalation

| Time | Action |
|:----:|--------|
| 0 min | On-call responds, triage |
| 10 min | Escalate to instructor/TA if unresolved |
| 20 min | Announce incident in course channel |
| 60 min | Full postmortem if SLO violated |

**Contacts:** Instructor (TBD), TA (TBD), `#sre-course` channel.

---

## 5. Backup & Restore

### Automated backup (CronJob)

A CronJob runs every 5 minutes, creating a `pg_dump -Fc` backup. Retention: 5 newest dumps.

```bash
kubectl get cronjob postgres-backup
kubectl create job --from=cronjob/postgres-backup manual-backup   # run on demand
kubectl exec deployment/backup-inspector -- ls -la /backups/      # list backups
```

### Manual backup

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  pg_dump -U quickticket -Fc quickticket > /tmp/quickticket.dump
```

### Restore

```bash
POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)
kubectl cp /tmp/quickticket.dump $POD:/tmp/backup.dump
kubectl exec $POD -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
kubectl rollout restart deployment/events
```

### RTO and RPO

| Scenario | RTO | RPO | Notes |
|----------|:---:|:---:|-------|
| Pod restart (PVC) | ~8s | — | PVC survives pod death |
| Disaster recovery (PVC + backup) | ~8s + restore time | ~5 min | Restore from CronJob backup |
| Disaster recovery (no PVC) | ~19s | ~41 min | Full pg_restore from manual backup |

**Key principle:** The PVC from Lab 9 Bonus eliminates data loss on pod restarts. The CronJob provides point-in-time recovery with 5-minute granularity.

---

