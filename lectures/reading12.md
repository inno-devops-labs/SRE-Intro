# 📖 Reading 12 — Advanced Kubernetes Resilience

> **Self-study material** for Bonus Lab 12. Read before starting the lab.

---

## Why Advanced Resilience Matters

In Labs 1-10 you built a functional SRE setup. But your system has gaps that production workloads need to handle:

- **Single replica** of each service — one pod dies, brief outage
- **No disruption budget** — cluster maintenance kills all pods at once
- **All pods on same node** — node failure takes down everything
- **Database migrations lock the app** — no zero-downtime strategy
- **Ungraceful shutdown** — pods killed mid-request lose in-flight work

This reading covers the K8s features and patterns that address these gaps.

---

## Multi-Replica Deployments

**Problem:** With `replicas: 1`, pod restart = 100% downtime (even if brief).

**Solution:** Run 2+ replicas. The Service load-balances across them. One pod dying means traffic shifts to the surviving pod(s).

```yaml
spec:
  replicas: 3  # Always-available — 1 pod can die, 2 keep serving
```

**Cost:** More pods = more resources. For QuickTicket lab, 2 replicas per service is sufficient.

---

## PodDisruptionBudget (PDB)

**Problem:** Kubernetes maintenance (node drain, cluster upgrade) evicts all pods at once.

**Solution:** PDB tells K8s: "never evict more than N pods at the same time."

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: gateway-pdb
spec:
  minAvailable: 1        # At least 1 pod must always be running
  # OR: maxUnavailable: 1  # At most 1 pod can be evicted at a time
  selector:
    matchLabels:
      app: gateway
```

**How it works:**
- Admin runs `kubectl drain node-1` (for maintenance)
- K8s checks PDB: "gateway-pdb says minAvailable: 1"
- If draining would violate the PDB → K8s waits until a new pod is scheduled elsewhere first
- Result: at least 1 gateway pod is always serving traffic during maintenance

📖 **Read more:** [Kubernetes — PDB](https://kubernetes.io/docs/tasks/run-application/configure-pdb/)

---

## Pod Anti-Affinity

**Problem:** All 3 gateway replicas scheduled on the same node. Node dies → all 3 die.

**Solution:** Anti-affinity rule: "don't put two gateway pods on the same node."

```yaml
spec:
  template:
    spec:
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              podAffinityTerm:
                labelSelector:
                  matchExpressions:
                    - key: app
                      operator: In
                      values: ["gateway"]
                topologyKey: kubernetes.io/hostname
```

**`preferred` vs `required`:**
- `preferred` = try, but don't block scheduling if impossible (use for most cases)
- `required` = enforce strictly, pod stays Pending if no valid node (use only when critical)

For k3d with 1 node: anti-affinity has no effect (only 1 node). In multi-node clusters, it spreads pods.

---

## Graceful Shutdown

**Problem:** K8s sends SIGTERM to the pod, then kills it after `terminationGracePeriodSeconds` (default 30s). If the app doesn't handle SIGTERM, in-flight requests are dropped.

**Solution:** Handle SIGTERM in your application — stop accepting new requests, finish in-flight ones, then exit.

**For FastAPI/Uvicorn:** Uvicorn handles SIGTERM by default — it stops accepting connections and waits for active requests to complete. But your `terminationGracePeriodSeconds` must be long enough for the longest request.

**K8s shutdown sequence:**
1. K8s sends SIGTERM to the pod
2. K8s removes the pod from Service endpoints (stop sending new traffic)
3. Pod has `terminationGracePeriodSeconds` to finish work
4. K8s sends SIGKILL (force kill) after the grace period

**The gotcha:** Steps 1 and 2 happen in parallel, not sequentially. There's a brief window where the pod receives SIGTERM but still gets new traffic. Solution: add a `preStop` hook with a small sleep:

```yaml
lifecycle:
  preStop:
    exec:
      command: ["sh", "-c", "sleep 5"]
```

This ensures the pod is removed from endpoints before it starts shutting down.

📖 **Read more:** [Kubernetes — Container Lifecycle Hooks](https://kubernetes.io/docs/concepts/containers/container-lifecycle-hooks/)

---

## Zero-Downtime Database Migrations

**Problem:** You need to add a NOT NULL column. This requires a table rewrite (locks the table). During the lock, all queries block.

**Solution:** The expand-and-contract pattern from Lecture 9, but executed carefully:

```
Step 1: ADD COLUMN email TEXT              ← nullable, no lock
Step 2: UPDATE ... SET email = '...'       ← backfill in batches
Step 3: ALTER TABLE SET NOT NULL            ← add constraint (brief lock)
Step 4: Deploy code requiring email
```

**Batched backfill** prevents long locks:
```sql
-- Don't: UPDATE events SET email = 'unknown' WHERE email IS NULL;
-- (locks entire table for the duration)

-- Do: batch in chunks of 1000
UPDATE events SET email = 'unknown'
WHERE id IN (SELECT id FROM events WHERE email IS NULL LIMIT 1000);
-- Repeat until done
```

**For the lab:** Alembic supports batched operations via custom migration code.

---

## Rolling Restart Verification

After deploying new pods, how do you know they're actually working?

```bash
# Watch rolling restart
kubectl rollout restart deployment/gateway
kubectl rollout status deployment/gateway --timeout=120s

# Verify all pods are READY (not just Running)
kubectl get pods -l app=gateway -o jsonpath='{range .items[*]}{.metadata.name} {.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}'
```

**Readiness probes** (from Lab 4, Task 2) are essential here — they prevent K8s from sending traffic to pods that aren't ready yet.

---

## Key Concepts Summary

| Pattern | Problem | Solution |
|---------|---------|----------|
| Multi-replica | Single pod = single point of failure | replicas: 2+ |
| PDB | Maintenance evicts all pods | minAvailable: 1 |
| Anti-affinity | All pods on same node | Spread across nodes |
| Graceful shutdown | In-flight requests dropped | Handle SIGTERM + preStop |
| Zero-downtime migration | ALTER TABLE locks | Expand-and-contract + batched backfill |
