# Lab 12 — Bonus: Advanced Kubernetes Resilience

![difficulty](https://img.shields.io/badge/difficulty-advanced-red)
![topic](https://img.shields.io/badge/topic-K8s%20Resilience-blue)
![points](https://img.shields.io/badge/points-10%2B2.5-orange)
![tech](https://img.shields.io/badge/tech-Kubernetes-informational)

> **Goal:** Make QuickTicket resilient to node failures, maintenance events, and deployment issues using advanced K8s features.
> **Deliverable:** A PR from `feature/lab12` with updated manifests and `submissions/lab12.md`. Submit PR link via Moodle.

> 📖 **Read first:** `lectures/reading12.md` — covers PDB, anti-affinity, graceful shutdown, zero-downtime migrations.

---

## Overview

In this lab you will:
- Scale to multi-replica deployments and test failover
- Add PodDisruptionBudgets to survive cluster maintenance
- Implement graceful shutdown with preStop hooks
- Run a zero-downtime database migration under load
- Verify rolling restart doesn't drop requests

---

## Task 1 — Multi-Replica Failover + PDB (6 pts)

**Objective:** Scale services to multiple replicas, add PodDisruptionBudgets, and verify the system survives pod disruptions with zero downtime.

### 12.1: Scale to multiple replicas

Update your K8s manifests to run 3 replicas for gateway, 2 for events, 2 for payments:

```yaml
# k8s/gateway.yaml
spec:
  replicas: 3
```

Apply and verify:

```bash
kubectl apply -f k8s/
kubectl get pods -l app=gateway
# Should show 3 gateway pods
```

Start load generator and verify all replicas receive traffic:

```bash
./app/loadgen/run.sh 5 120 &

# Watch which pods serve requests (check logs from each)
for pod in $(kubectl get pods -l app=gateway -o name); do
  echo "--- $pod ---"
  kubectl logs $pod --tail=3 | tail -1
done
```

### 12.2: Test failover

With load running, kill one gateway pod:

```bash
kubectl delete pod $(kubectl get pod -l app=gateway -o name | head -1)
```

**Observe:**
- Did the loadgen show any errors? (should be 0 with 3 replicas)
- How fast did K8s create a replacement?
- Compare with Lab 1 (1 replica) — how many errors then?

### 12.3: Add PodDisruptionBudgets

Create `k8s/pdb.yaml`:

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: gateway-pdb
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: gateway
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: events-pdb
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: events
```

Apply and test:

```bash
kubectl apply -f k8s/pdb.yaml

# Simulate maintenance — try to drain the node
kubectl drain $(kubectl get nodes -o name | head -1) --ignore-daemonsets --delete-emptydir-data --dry-run=client
```

The `--dry-run` shows what WOULD happen. With PDB, K8s ensures at least 1 gateway pod survives.

### 12.4: Proof of work

**PDB and updated manifests** go in `k8s/`.

**Paste into `submissions/lab12.md`:**
1. `kubectl get pods` showing multi-replica deployments
2. Loadgen output during pod deletion — 0 errors with 3 replicas
3. `kubectl get pdb` output
4. Drain dry-run output showing PDB respected
5. Answer: "With 3 gateway replicas and minAvailable: 1 PDB, what's the maximum number of pods that can be evicted simultaneously?"

---

## Task 2 — Graceful Shutdown + Zero-Downtime Migration (4 pts)

> ⏭️ This task is optional.

**Objective:** Ensure pods shut down gracefully and database migrations don't cause downtime.

### 12.5: Add preStop hooks

Update gateway deployment:

```yaml
lifecycle:
  preStop:
    exec:
      command: ["sh", "-c", "sleep 5"]
```

This ensures the pod is removed from Service endpoints before it starts shutting down.

Test with rolling restart under load:

```bash
./app/loadgen/run.sh 5 120 &
kubectl rollout restart deployment/gateway
kubectl rollout status deployment/gateway --timeout=120s
```

Did the loadgen show any errors during the rolling restart?

### 12.6: Zero-downtime migration under load

Create an Alembic migration that adds an index (a realistic production operation):

```python
def upgrade():
    op.create_index('idx_events_date', 'events', ['event_date'],
                     postgresql_concurrently=True)

def downgrade():
    op.drop_index('idx_events_date')
```

Run it while the load generator is active:

```bash
./app/loadgen/run.sh 5 60 &
alembic upgrade head
```

Verify: 0% errors in loadgen during migration.

**Paste into `submissions/lab12.md`:**
- preStop hook in manifest
- Loadgen output during rolling restart (should be 0 errors)
- Migration code
- Loadgen output during migration (should be 0 errors)
- Answer: "Why does `CREATE INDEX CONCURRENTLY` matter? What happens without it?"

---

## Bonus Task — Production Readiness Checklist (2.5 pts)

> 🌟 For those who want extra challenge.

**Objective:** Create a production readiness checklist for QuickTicket — the document you'd review before going live.

Write a checklist covering:

```markdown
# QuickTicket Production Readiness Checklist

## Reliability
- [ ] All services have 2+ replicas
- [ ] PodDisruptionBudgets configured
- [ ] Liveness and readiness probes on all services
- [ ] Resource requests and limits set
- [ ] Graceful shutdown (preStop hooks)

## Observability
- [ ] Golden signals dashboard (latency, traffic, errors, saturation)
- [ ] SLOs defined with recording rules
- [ ] SLO-based alerting configured
- [ ] Runbooks for each alert
- [ ] Structured logging across all services

## Deployment
- [ ] CI/CD pipeline with automated tests
- [ ] ArgoCD GitOps — no manual kubectl
- [ ] Canary deployment strategy
- [ ] Rollback tested and documented

## Data
- [ ] Automated backups with tested restore
- [ ] PersistentVolumeClaim for PostgreSQL
- [ ] Migration strategy (expand-and-contract)
- [ ] RTO and RPO defined

## Incident Response
- [ ] Incident response process documented
- [ ] On-call rotation defined
- [ ] Postmortem template and process
- [ ] Contact escalation path
```

For each item, mark ✅ (done in your labs) or ❌ (gap), and explain what's missing.

**Paste into `submissions/lab12.md`:**
- Your completed checklist with ✅/❌ for each item
- For each ❌: what would you need to do to check it off?

---

## How to Submit

```bash
git switch -c feature/lab12
git add k8s/ submissions/lab12.md
git commit -m "feat(lab12): add multi-replica, PDB, graceful shutdown"
git push -u origin feature/lab12
```

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — Multi-replica + PDB | **6** | 3 replicas, zero errors on pod kill, PDB configured |
| **Task 2** — Graceful shutdown + migration | **4** | preStop hooks, zero-error rolling restart, zero-error migration |
| **Bonus Task** — Production readiness checklist | **2.5** | Comprehensive checklist with gap analysis |
| **Total** | **12.5** | 10 main + 2.5 bonus |
