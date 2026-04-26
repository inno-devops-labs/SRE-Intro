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
- **No autoscaling** — a traffic spike overwhelms static replicas

This reading covers the K8s features and patterns that address these gaps.

> 💬 *"In production, the question isn't if a pod will die. It's what happens when it does."*

---

## Multi-Replica Deployments

**Problem:** With `replicas: 1`, pod restart = 100% downtime (even if brief).

**Solution:** Run 2+ replicas. The Service load-balances across them. One pod dying means traffic shifts to the surviving pod(s).

```yaml
spec:
  replicas: 3  # Always-available — 1 pod can die, 2 keep serving
```

**Cost:** More pods = more resources. For QuickTicket lab, 2 replicas per service is sufficient.

### When more isn't better

Diminishing returns set in: going from 1 → 2 replicas cuts single-pod-failure downtime from 100% → 0% instantly. Going from 5 → 10 for the same workload just costs money unless you also need more capacity.

**Rule of thumb:** start at `replicas: N+1` where N is your peak load divided by per-pod capacity. The `+1` is your redundancy.

---

## PodDisruptionBudget (PDB)

**Problem:** Kubernetes maintenance (node drain, cluster upgrade, autoscaler downscale) evicts pods. Without a PDB, it can evict **all** of them at once.

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
- If draining would violate the PDB → K8s **waits** until a new pod is scheduled elsewhere first
- Result: at least 1 gateway pod is always serving traffic during maintenance

### `minAvailable` vs `maxUnavailable` — which to use?

| 🏷️ Field | 💡 Use when |
|----------|------------|
| `minAvailable: 2` | You know the absolute minimum capacity |
| `maxUnavailable: 25%` | You know the ratio you can lose |
| Percent form | Scales automatically when replicas change |

> ⚠️ **PDB only protects against voluntary disruptions** (drains, evictions, autoscaler). It does **not** save you from node crashes, kernel OOM, or power failures. For those, you need replicas across nodes (next section).

📖 **Read more:** [Kubernetes — PDB](https://kubernetes.io/docs/tasks/run-application/configure-pdb/)

---

## Pod Anti-Affinity and Topology Spread Constraints

**Problem:** All 3 gateway replicas scheduled on the same node. Node dies → all 3 die.

### Classic approach: anti-affinity

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

`preferred` vs `required`:
- `preferred` = try, but don't block scheduling if impossible (use for most cases)
- `required` = enforce strictly, pod stays `Pending` if no valid node (use only when critical)

### Modern approach: Topology Spread Constraints

Since Kubernetes 1.19, `topologySpreadConstraints` is more expressive:

```yaml
topologySpreadConstraints:
  - maxSkew: 1                  # max difference between nodes' pod counts
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway   # or DoNotSchedule
    labelSelector:
      matchLabels:
        app: gateway
```

You can spread across:
- `kubernetes.io/hostname` — across nodes
- `topology.kubernetes.io/zone` — across availability zones
- `topology.kubernetes.io/region` — across regions

> 💡 For multi-AZ production: spread replicas **across zones** so an entire AZ outage doesn't take the service down.

**For k3d with 1 node:** anti-affinity and spread have no effect (only 1 node). In multi-node clusters, they matter a lot.

---

## Graceful Shutdown

**Problem:** K8s sends SIGTERM to the pod, then kills it after `terminationGracePeriodSeconds` (default 30s). If the app doesn't handle SIGTERM, in-flight requests are dropped.

### What the app must do

Handle SIGTERM: stop accepting new requests, finish in-flight ones, then exit cleanly.

**For FastAPI/Uvicorn:** Uvicorn handles SIGTERM by default — it stops accepting new connections and waits for active requests to complete. But your `terminationGracePeriodSeconds` must be long enough for the longest request.

### K8s shutdown sequence

1. 🔁 K8s marks the pod as `Terminating`
2. 📡 K8s sends SIGTERM to the container's PID 1
3. 🌐 K8s removes the pod from **Service endpoints** (stop sending new traffic)
4. ⏳ Pod has `terminationGracePeriodSeconds` to finish work
5. 💀 K8s sends SIGKILL (force kill) after the grace period

### The race condition

Steps 2 and 3 happen in parallel, not sequentially. There's a brief window where the pod receives SIGTERM but still gets new traffic (the endpoints update propagates through kube-proxy asynchronously).

**Solution:** add a `preStop` hook with a small sleep to let endpoints propagate first:

```yaml
lifecycle:
  preStop:
    exec:
      command: ["sh", "-c", "sleep 5"]
terminationGracePeriodSeconds: 45   # 5s preStop + 30s drain + 10s margin
```

This ensures the pod is removed from endpoints before it starts shutting down.

📖 **Read more:** [Kubernetes — Container Lifecycle Hooks](https://kubernetes.io/docs/concepts/containers/container-lifecycle-hooks/)

---

## Probes, Revisited

Three probe types; each answers a different question:

| 💊 Probe | ❓ Question | 💥 On Failure |
|----------|-----------|--------------|
| Startup | "Is the app done booting?" | Don't run other probes yet |
| Liveness | "Is the process wedged?" | **Kill the container** |
| Readiness | "Can this pod serve traffic?" | **Remove from Service endpoints** (no restart) |

### Common traps

1. **Liveness probe that calls the database.** DB blip → all your pods restart → the outage amplifies. Liveness must check only **local** health.
2. **No startup probe on slow-boot apps.** Liveness starts failing during JVM warm-up → pod restarts in a loop.
3. **Readiness too strict.** Pod oscillates in and out of Service — client sees connection resets mid-request.

### Readiness gates

For advanced use, `readinessGates` let an external controller (e.g., a load balancer) add its own readiness signal before the pod receives traffic. Useful for cloud LBs that need to register a backend.

---

## Autoscaling

### Horizontal Pod Autoscaler (HPA)

Scales **replicas** based on metrics:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
spec:
  scaleTargetRef:
    kind: Deployment
    name: gateway
  minReplicas: 2
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target: { type: Utilization, averageUtilization: 70 }
```

**Custom metrics** (requests-per-second, queue depth) need the `metrics.k8s.io` aggregator + Prometheus Adapter / KEDA.

### Cluster Autoscaler / Karpenter

HPA can't add **nodes**. For that:
- **Cluster Autoscaler** — the classic CNCF project; adjusts node pools based on Pending pods
- **Karpenter** (AWS, 2021) — provisions right-sized nodes per pod, faster and cheaper

> 💡 **Fun fact:** Karpenter can provision a new node for a Pending pod in ~60 seconds on AWS. Cluster Autoscaler typically takes 3-5 minutes.

---

## Priorities and Preemption

When the cluster is full, which pod dies?

**PriorityClass** lets you declare relative importance:

```yaml
apiVersion: scheduling.k8s.io/v1
kind: PriorityClass
metadata:
  name: high-priority
value: 1000000
preemptionPolicy: PreemptLowerPriority
```

If a `high-priority` pod can't schedule, K8s evicts lower-priority pods to make room.

**Use cases:** critical control-plane components, system daemons, payments > recommendations.

---

## Zero-Downtime Database Migrations

**Problem:** You need to add a NOT NULL column. This requires a table rewrite (locks the table). During the lock, all queries block.

**Solution:** The expand-and-contract pattern, executed in a precise sequence of 3 migrations + 2 code deploys:

```
1. Migration 1: ADD COLUMN email TEXT (nullable)
2. Code deploy A: write BOTH columns; read COALESCE(new, old)
3. Migration 2: backfill with UPDATE
4. Code deploy B: write+read NEW column only
5. Migration 3: DROP COLUMN old
```

**The critical rule: Deploy B must be fully rolled out before migration 3.** If any old code is still running when you drop the old column, those pods start getting "column does not exist" errors. The whole point of expand-and-contract is that both code versions must work at every schema version.

In practice, wait an extra 5 minutes or check your APM after deploy B completes to ensure zero references to the old column before the drop.

### Batched backfill

```sql
-- Don't: UPDATE events SET email = 'unknown' WHERE email IS NULL;
-- (locks entire table for the duration)

-- Do: batch in chunks of 1000 with short locks between
UPDATE events SET email = 'unknown'
WHERE id IN (SELECT id FROM events WHERE email IS NULL LIMIT 1000);
-- Repeat until affected rows = 0
```

For Alembic on PostgreSQL, wrap `CREATE INDEX CONCURRENTLY` in `with op.get_context().autocommit_block():` so the DDL runs outside a transaction. For MySQL at scale, consider `gh-ost` or `pt-online-schema-change`.

### CREATE INDEX CONCURRENTLY on PostgreSQL

The classic gotcha: `CREATE INDEX` on a big table takes an `ACCESS EXCLUSIVE` lock, blocking reads and writes for *minutes*. On a 5-row test table it doesn't matter, but on 10M rows it's a production outage.

The fix: `postgresql_concurrently=True` in Alembic + the autocommit_block wrapper above. This swaps the lock to `SHARE UPDATE EXCLUSIVE`, which doesn't block reads or writes. Trade-off: it costs more disk I/O and takes longer to run, but users never notice.

Always use CONCURRENTLY in production migrations. The test table is too small to show its value, but the syntax is load-bearing.

> ⚠️ **The killer detail:** each app version must be forward-compatible with both the old and new schema during the migration window. Version N-1 must tolerate the new column; Version N+1 must not require the old one until after contract.

---

## Rolling Restart Verification

After deploying new pods, how do you know they're actually working?

```bash
# Watch rolling restart
kubectl rollout restart deployment/gateway
kubectl rollout status deployment/gateway --timeout=120s

# Verify all pods are READY (not just Running)
kubectl get pods -l app=gateway \
  -o jsonpath='{range .items[*]}{.metadata.name} {.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}'
```

**Readiness probes** (from Lab 4) are essential here — they prevent K8s from sending traffic to pods that aren't ready yet. Without them, rolling updates can briefly serve 502s.

### Timing expectations: Deployments vs Argo Rollouts

A plain `kubectl rollout restart deployment/gateway` on a Deployment takes ~10 seconds: 1 canary pod up, rest follow. But if you're using **Argo Rollouts** (as in Lab 7), the restart is deliberately slower — it runs your canary analysis steps (which might include a 30 s pause or external validation) *before* promoting. This can take 45+ seconds. Both are correct; the difference is your trade-off between speed and safety.

If your `kubectl argo rollouts status` shows `Paused - CanaryPauseStep` that's expected — that's Argo's analysis gate. It will either auto-promote (if your metrics pass) or hang until you manually approve. Check the analysis results:

```bash
kubectl argo rollouts get rollout gateway
kubectl logs deployment/gateway -f  # see if /health is passing
```

Don't force-delete pods thinking the rollout is hung — you'll destroy the analysis and defeat the whole point of Argo.

---

## StatefulSet Resilience

StatefulSets are for stateful pets (Week 9) — their resilience pattern is different from Deployments:

| 🏷️ Feature | 🎯 Purpose |
|-----------|-----------|
| `serviceName` | Stable DNS per pod (`pg-0.pg.ns.svc`) |
| `volumeClaimTemplates` | Each pod gets its own PVC |
| `podManagementPolicy: OrderedReady` | Pods start/stop in order (default) |
| `updateStrategy: RollingUpdate` with `partition` | Canary-style updates |
| `updateStrategy: OnDelete` | Manual control |

**Partitioned updates** let you upgrade `pg-N ... pg-2` while keeping `pg-0` and `pg-1` on the old version — a true canary for stateful workloads.

---

## Eviction Types

Kubernetes evicts pods for several reasons. Each has different implications:

| 🏷️ Eviction type | 💡 Trigger | 🛡️ Protected by |
|-------------------|-----------|-----------------|
| API-initiated eviction | `kubectl drain` | **PDB** respects this |
| Node pressure eviction | node low on memory/disk | PDB is **not** respected |
| Preemption | higher-priority pod needs room | Priority ordering |
| Soft taint eviction | node tainted with `NoExecute` | Tolerations |

> ⚠️ **Key takeaway:** PDBs only protect voluntary disruptions. You still need multi-replica + anti-affinity/spread for hard failures.

### Confirming PDB enforcement

To check that a PDB is actually working, tighten it to an impossible constraint (e.g. `minAvailable == replicas`) and try to evict one pod via the API. You should get HTTP 429 with `reason: DisruptionBudget`. This is how Lab 12 verifies the PDB isn't just sitting there silently.

If you do a `kubectl drain --dry-run=server`, you'll see every pod listed as an eviction candidate — that's normal. Drain evaluates each pod *sequentially* against its PDB; the dry-run just lists who *could* be a candidate if PDBs weren't checked. The real enforcement happens when you fire a single eviction via `POST /api/v1/namespaces/<ns>/pods/<name>/eviction`.

---

## Real Kubernetes Outages to Learn From

| 🗓️ Year | 🏢 Incident | 🎓 Lesson |
|---------|-------------|-----------|
| 2018 | **GitHub** — network partition + MySQL failover | Stateful migrations need chaos testing; retries need bounded concurrency |
| 2021 | **Facebook** — BGP withdrew DNS routes | Don't co-locate recovery tools with the system they recover |
| 2022 | **Slack** — K8s upgrade cascade | Plan control-plane upgrades like data-plane changes |
| 2022 | **Cloudflare** — config deploy + control plane | Canary every config change, not just code |
| 2023 | **Reddit** — K8s 1.23 upgrade miss | Test upgrades in staging **and** have tested rollback |

> 💬 *"The day Kubernetes tricked me is the day I learned the reconciliation loop doesn't care about my feelings."*

---

## Key Concepts Summary

| Pattern | Problem | Solution |
|---------|---------|----------|
| Multi-replica | Single pod = single point of failure | `replicas: 2+` |
| PDB | Maintenance evicts all pods | `minAvailable: 1` / `maxUnavailable: 1` |
| Anti-affinity / Spread | All pods on same node/zone | Topology spread across nodes, zones |
| Graceful shutdown | In-flight requests dropped | Handle SIGTERM + preStop sleep |
| Zero-downtime migration | `ALTER TABLE` locks | Expand-and-contract + batched backfill |
| HPA | Static capacity can't absorb spikes | Horizontal pod autoscaling on CPU/custom |
| Cluster autoscaler | Nodes full | Karpenter / Cluster Autoscaler |
| PriorityClass | Critical pods get evicted first | Set explicit priority |

---

## Key Books & Resources

- *Kubernetes Up & Running* — Hightower, Burns, Beda (3rd ed. 2022) — the standard
- *Production Kubernetes* — Dotson, Yates et al. (O'Reilly, 2021) — hardening guide
- *Programming Kubernetes* — Hausenblas & Schimanski (O'Reilly, 2019) — operators + controllers
- *Kubernetes Patterns* — Ibryam & Huss (O'Reilly, 2nd ed. 2023) — design patterns
- *Site Reliability Engineering* — Google — ch. 21 (handling overload) and ch. 22 (cascading failures)

**Official docs:**
- [K8s — Application Resilience Patterns](https://kubernetes.io/docs/concepts/workloads/pods/disruptions/)
- [K8s — Topology Spread Constraints](https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/)
- [K8s — Container Lifecycle Hooks](https://kubernetes.io/docs/concepts/containers/container-lifecycle-hooks/)
- [Learn K8s — Graceful shutdown deep dive](https://learnk8s.io/graceful-shutdown)

**Talks (free):**
- [Kelsey Hightower — Kubernetes the Hard Way](https://github.com/kelseyhightower/kubernetes-the-hard-way)
- [Lachlan Evenson — Kubernetes Failure Stories](https://k8s.af/) — curated incident list
