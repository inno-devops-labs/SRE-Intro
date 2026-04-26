# Lab 12 — Bonus: Advanced Kubernetes Resilience

![difficulty](https://img.shields.io/badge/difficulty-advanced-red)
![topic](https://img.shields.io/badge/topic-K8s%20Resilience-blue)
![points](https://img.shields.io/badge/points-10-orange)
![tech](https://img.shields.io/badge/tech-Kubernetes-informational)

> **Goal:** Make QuickTicket resilient to node maintenance and rolling-deploy events using PodDisruptionBudgets, graceful shutdown, and zero-downtime migrations.
> **Deliverable:** A PR from `feature/lab12` with updated `k8s/` manifests, the new `k8s/pdb.yaml`, and `submissions/lab12.md`.

> 📖 **Read first:** [`lectures/reading12.md`](../lectures/reading12.md) — PDB, anti-affinity, graceful shutdown, zero-downtime migration patterns.

---

## Overview

In this lab you:

- Scale events + payments + notifications to 2 replicas (gateway is already a 5-replica Rollout from Lab 7).
- Write `k8s/pdb.yaml` — PodDisruptionBudgets that survive maintenance evictions.
- Add `topologySpreadConstraints` to the gateway Rollout so its replicas spread across nodes (single-node k3d note included).
- Add `preStop` hook + `readinessProbe` to the gateway Rollout so rolling restarts drop zero requests.
- Write an Alembic migration using `CREATE INDEX CONCURRENTLY` and run it under live load.
- Sketch (no code) the **expand-and-contract** pattern for a zero-downtime column rename — the general shape of all production schema changes.
- *(Optional)* a quick HPA observation for completeness.

---

## Project State

**You should have from previous labs:**

- QuickTicket on k3d with 5-replica gateway Rollout (Lab 7) and Postgres on a PVC (Lab 9).
- In-cluster Prometheus (Lab 7 Bonus).
- `labs/lab8/mixedload.yaml` generating checkout traffic throughout the lab.
- An Alembic setup already initialized in Lab 9 (keep the venv + port-forward).

> **If you skipped Lab 9** (it was an optional task), Task 12.7 needs Alembic. Bootstrap in 5 minutes: `python3 -m venv .venv && source .venv/bin/activate && pip install alembic==1.18.4 psycopg2-binary==2.9.11 sqlalchemy==2.0.49 && alembic init migrations && alembic stamp head`. Edit `alembic.ini` `sqlalchemy.url` to `postgresql://quickticket:quickticket@localhost:5432/quickticket` and start `kubectl port-forward svc/postgres 5432:5432 &`.

---

## Setup

Ensure mixedload is running (zero-downtime proofs need live traffic):

```bash
kubectl apply -f labs/lab8/mixedload.yaml
kubectl rollout status deployment/mixedload --timeout=30s
```

Zero 5xx baseline before you start:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
# Expect "0" — if not, let the cluster settle before proceeding.
```

---

## Task 1 — Multi-Replica Failover + PDBs (6 pts)

### 12.1: Scale services to 2 replicas

Edit `k8s/events.yaml`, `k8s/payments.yaml`, `k8s/notifications.yaml`:

```yaml
spec:
  replicas: 2
```

Apply:

```bash
kubectl apply -f k8s/events.yaml -f k8s/payments.yaml -f k8s/notifications.yaml
kubectl get deploy -l 'app in (events,payments,notifications)'
```

You should end up with:

```
events             2/2
notifications      2/2
payments           2/2
```

(gateway already has 5 replicas via the Rollout from Lab 7.)

### 12.2: Failover test — kill pods under load

Record 5xx before / after a coordinated pod kill:

```bash
# before
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'

# kill (note: use the pod NAME not "pod/<name>" — kubectl delete is pedantic)
kubectl delete pod $(kubectl get pod -l app=gateway -o jsonpath='{.items[0].metadata.name}') --wait=false
kubectl delete pod $(kubectl get pod -l app=events  -o jsonpath='{.items[0].metadata.name}') --wait=false

# watch recovery (should be ready within ~5s)
kubectl get pod -l 'app in (gateway,events)' --watch   # Ctrl-C when all 1/1

# after
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))'
```

Expected: 5xx stays at 0. Replacement pods come up within seconds; Service endpoints reroute traffic to the surviving replicas during the gap.

### 12.3: Write `k8s/pdb.yaml`

```yaml
# k8s/pdb.yaml — YOUR TASK
#
# Write 4 PodDisruptionBudgets (one per service) in a single file.
#
# Requirements:
#   gateway-pdb        minAvailable: 2             (5 replicas, tolerate 3 evictions)
#   events-pdb         minAvailable: 1             (2 replicas, tolerate 1 eviction)
#   payments-pdb       minAvailable: 1             (2 replicas, tolerate 1 eviction)
#   notifications-pdb  maxUnavailable: 1           (best-effort — fire-and-forget in Lab 11)
#
#   All use the same selector pattern:
#     selector:
#       matchLabels:
#         app: <service-name>
#
# Why these values:
#   - gateway: 5 replicas, gateway IS the critical path. minAvailable: 2 means we
#     can lose 3 simultaneously during a node drain. Why not minAvailable: 4?
#     Because the cluster autoscaler / drain would block forever — we want to be
#     able to actually replace nodes. 2 keeps enough capacity for ~half of normal
#     RPS while a rolling drain reschedules the rest.
#   - events/payments must have 1 live at all times → minAvailable: 1
#   - notifications is best-effort → maxUnavailable: 1 is a softer bar
#
# Hint: apiVersion policy/v1, kind PodDisruptionBudget. Lecture 12 slide 6.
```

Apply + verify:

```bash
kubectl apply -f k8s/pdb.yaml
kubectl get pdb
# Expect:
# gateway-pdb         2               N/A               3
# events-pdb          1               N/A               1
# payments-pdb        1               N/A               1
# notifications-pdb   N/A             1                 1
```

### 12.4: Add topology spread (single-node note)

Production K8s clusters have multiple nodes; if all your gateway pods land on the same node, you've thrown away the whole point of multi-replica resilience. The standard tool is `topologySpreadConstraints`. Add one to the gateway Rollout pod template:

```yaml
# k8s/gateway.yaml — YOUR TASK (add to spec.template.spec)
#
# topologySpreadConstraints:
#   - maxSkew: 1
#     topologyKey: kubernetes.io/hostname
#     whenUnsatisfiable: ScheduleAnyway     # don't block scheduling on single-node
#     labelSelector:
#       matchLabels:
#         app: gateway
#
# What this says: across hostnames (= nodes in k3d), the difference in
# gateway-pod count between the most-loaded and least-loaded node should
# never exceed 1. So with 5 pods over 3 nodes the placement would be
# 2/2/1, never 4/1/0 or 5/0/0.
#
# whenUnsatisfiable: ScheduleAnyway is the right choice for "preferred but
# not mandatory" — the alternative DoNotSchedule would leave pods Pending
# on a single-node cluster.
```

Apply and observe. **On single-node k3d the constraint has no observable effect** — every pod still lands on the only node:

```bash
kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts status gateway --timeout=240s
kubectl get pod -l app=gateway -o wide
# All 5 pods on the same NODE — that's expected here. The lesson is that
# the YAML is *correct* and ready for a real multi-node cluster. Verify
# the field is in the live spec:
kubectl get rollout gateway -o jsonpath='{.spec.template.spec.topologySpreadConstraints}' | python3 -m json.tool
```

For the same reason `kubectl drain` won't actually demonstrate eviction on this cluster — there's nowhere to reschedule pods. We work around that in 12.5 below.

### 12.5: Prove a PDB actually blocks eviction

`kubectl drain --dry-run=server` *lists* all pods as candidates — that's expected; drain evaluates each pod against its PDB sequentially, not upfront. To see a real PDB rejection you need to (a) tighten a PDB so even one eviction violates it and (b) issue a single eviction via the API.

Try this yourself first. The eviction API lives at `POST /api/v1/namespaces/<ns>/pods/<name>/eviction` with body `{apiVersion: policy/v1, kind: Eviction, metadata: {name, namespace}}`. `kubectl` doesn't have a direct subcommand — you reach it via `kubectl proxy` + `curl`, or via your own client.

If you get stuck:

<details>
<summary>💡 Reference solution</summary>

```bash
# Make events-pdb impossible to satisfy (minAvailable=2 with 2 replicas = zero tolerance)
kubectl patch pdb events-pdb --type=merge -p '{"spec":{"minAvailable":2}}'
kubectl get pdb events-pdb                         # ALLOWED DISRUPTIONS should be 0

# Open a kubectl proxy in the background, remember its PID for clean teardown
kubectl proxy --port=8901 >/tmp/proxy.log 2>&1 &
PROXY_PID=$!
trap 'kill $PROXY_PID 2>/dev/null' EXIT
sleep 2

# Fire one eviction
POD=$(kubectl get pod -l app=events -o jsonpath='{.items[0].metadata.name}')
curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"apiVersion\":\"policy/v1\",\"kind\":\"Eviction\",
       \"metadata\":{\"name\":\"$POD\",\"namespace\":\"default\"}}" \
  http://localhost:8901/api/v1/namespaces/default/pods/$POD/eviction \
  | python3 -m json.tool
# Expect: HTTP 429 with "reason":"DisruptionBudget" and
# "message":"... needs 2 healthy pods and has 2 currently"

# Restore
kubectl patch pdb events-pdb --type=merge -p '{"spec":{"minAvailable":1}}'
```

</details>

### Proof of work (Task 1)

**Commit `k8s/pdb.yaml`, the updated `k8s/{events,payments,notifications}.yaml`, and the updated `k8s/gateway.yaml` (topology-spread constraint) to your fork.**

**Paste into `submissions/lab12.md`:**

1. `kubectl get deploy,rollout` showing all services at their target replica counts.
2. The before/after 5xx count from Prometheus around the pod-kill test (should both be 0).
3. `kubectl get pdb` output.
4. `kubectl get rollout gateway -o jsonpath='{.spec.template.spec.topologySpreadConstraints}'` output showing the constraint is in the live spec, plus `kubectl get pod -l app=gateway -o wide` showing the actual placement.
5. The HTTP 429 JSON body from the tightened-PDB eviction test (proves PDB enforcement).
6. Answer: "With 3 gateway replicas and minAvailable: 1, what's the maximum number of pods that can be evicted simultaneously? Why is your `gateway-pdb` set to `minAvailable: 2` with 5 replicas?"
7. Answer: "Your topology-spread constraint has no observable effect on single-node k3d. In a 3-node cluster, what placement would `maxSkew: 1` produce for 5 gateway pods? What about for 7?"

<details>
<summary>💡 Hints</summary>

- `kubectl delete pod <name>` — do NOT prefix with `pod/` when the resource is already `pod` by position; newer kubectl prints a confusing "no need to specify a resource type as a separate argument" warning but the delete still works. Use `--wait=false` to avoid blocking on grace period.
- `kubectl drain --dry-run=server` on a single-node k3d cluster shows all pods as eviction candidates. That's NOT a PDB failure — drain serializes evictions and respects the PDB one pod at a time. To see a PDB actually reject something, tighten the PDB (as in 12.5) so even one eviction would violate it.
- The eviction API is at `POST /api/v1/namespaces/<ns>/pods/<name>/eviction` with a body of `{apiVersion: policy/v1, kind: Eviction, metadata: {name, namespace}}`. `kubectl eviction` / `kubectl eviction-request` do NOT exist.

</details>

---

## Task 2 — Graceful Shutdown + Zero-Downtime Migration (4 pts)

> ⏭️ Optional.

### 12.6: preStop hook + readinessProbe

Edit `k8s/gateway.yaml` (it's an Argo Rollouts `Rollout`, not a `Deployment`). Add under `spec.template.spec`:

```yaml
      # Give in-flight requests time to finish after SIGTERM (10s preStop + up to 30s drain).
      terminationGracePeriodSeconds: 40
      containers:
        - name: gateway
          ...
          lifecycle:
            # Sleep BEFORE SIGTERM reaches the app. Gives kube-proxy / endpoints
            # controllers time to propagate this pod's NotReady state to every
            # node's iptables, so new traffic stops routing here BEFORE uvicorn
            # shuts down. Without this, there's a ~5-10s window where SIGTERM
            # + incoming traffic overlap and requests get RST.
            preStop:
              exec:
                command: ["sh", "-c", "sleep 10"]
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            periodSeconds: 2
            failureThreshold: 1
```

Apply (it will trigger a canary rollout — the analysis template should pass):

```bash
kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts status gateway --timeout=240s
```

### Rolling restart under load

```bash
# before
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))'

# restart — NOTE this is an Argo Rollout, not a Deployment
kubectl argo rollouts restart gateway
kubectl argo rollouts status gateway --timeout=240s

# after (wait 10s for the metric window to settle)
sleep 10
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
```

Expected: both queries return 0. If the restart produced 5xx, either the `preStop` sleep is too short or the readinessProbe didn't propagate in time.

> ⚠️ **Gotcha:** `kubectl rollout restart deployment/gateway` fails with *"deployment.apps gateway not found"* — gateway is `rollout.argoproj.io`, not `deployment.apps`. Use `kubectl argo rollouts restart gateway`.

### 12.7: `CREATE INDEX CONCURRENTLY` migration

Create a new Alembic migration (you already have Alembic set up from Lab 9):

```bash
source .venv/bin/activate
alembic revision -m "index events.event_date concurrently"
```

Edit the generated file:

```python
# migrations/versions/XXXX_index_events_event_date_concurrently.py
#
# YOUR TASK: fill in upgrade() and downgrade() such that:
#   - Adds an index on events(event_date) using CONCURRENTLY
#   - Is reversible (downgrade drops the index)
#   - Runs OUTSIDE Alembic's default transaction block (see gotcha below)
#
# Requirements for upgrade():
#   - op.create_index(..., postgresql_concurrently=True, if_not_exists=True)
#   - wrap in `with op.get_context().autocommit_block():`
#
# Hints:
#   - Without the autocommit_block wrapper, Postgres rejects the DDL with
#       ActiveSqlTransaction: CREATE INDEX CONCURRENTLY cannot run inside a transaction
#     because Alembic defaults to transactional DDL.
#   - `if_not_exists=True` keeps the migration re-runnable in case it's
#     interrupted. `if_exists=True` on downgrade is the mirror.
```

Run under live mixedload traffic:

```bash
# before
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(gateway_requests_total%7Bstatus%3D~%225..%22%7D)' \
  > /tmp/5xx.before

time alembic upgrade head

# verify the index was created
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -c '\d events' | grep idx_events

# after
sleep 5
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(gateway_requests_total%7Bstatus%3D~%225..%22%7D)' \
  > /tmp/5xx.after

diff /tmp/5xx.before /tmp/5xx.after   # should show no change
```

> 💡 **Why the migration finishes in milliseconds.** The `events` table has 5 rows. `CREATE INDEX` (with or without CONCURRENTLY) is essentially instant. The point of CONCURRENTLY is invisible at this scale — but in a production system on a 10M-row table, the non-concurrent version takes an `ACCESS EXCLUSIVE` lock for **minutes** (every query blocks), while CONCURRENTLY takes a milder `SHARE UPDATE EXCLUSIVE` lock that doesn't block reads or writes. You're learning the *right syntax* now so you don't need to learn it during an outage.

### 12.8: Sketch an expand-and-contract rename (design only)

`CREATE INDEX CONCURRENTLY` is one specific zero-downtime DDL. The general pattern for changing a schema without downtime is **expand-and-contract**: deploy a sequence of small, individually-reversible changes such that the application is never broken at any intermediate state.

**Your task:** sketch the migrations + code deploys to rename `events.event_date` → `events.scheduled_at` with zero downtime. Don't implement — just write down the steps in `submissions/lab12.md`. The right answer is **3 migrations + 2 code deploys**, interleaved.

Useful frame:

```
At every intermediate point, BOTH the old code and the new code must work.
That means a brief overlap where the column has BOTH names.
```

Hints:

- Migration 1: add new column `scheduled_at` (nullable). What's the SQL?
- Code deploy A: write to BOTH columns; read from `scheduled_at` if non-NULL else fall back to `event_date`.
- Migration 2: backfill — `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL`. Why is this safe even with live traffic?
- Code deploy B: write only to `scheduled_at`; read only from `scheduled_at`.
- Migration 3: drop `event_date`. Why must this come AFTER deploy B is fully rolled out, never before?

> 💡 The same pattern applies to renaming columns in tables you don't own (Stripe API fields, third-party DB schemas) — anywhere you can't atomically swap a name across all clients. Memorize the shape: **add → dual-write → backfill → switch read → drop old**.

### 12.9: Optional — quick HPA observation

Reading 12 covers HPA + Karpenter; we haven't applied either in this course because k3d is single-node. As a quick demonstration only, write a `HorizontalPodAutoscaler` for the gateway Rollout (HPA works on any scalable resource):

```yaml
# k8s/gateway-hpa.yaml — YOUR TASK
#
# Requirements:
#   apiVersion: autoscaling/v2
#   kind: HorizontalPodAutoscaler
#   spec:
#     scaleTargetRef: { apiVersion: argoproj.io/v1alpha1, kind: Rollout, name: gateway }
#     minReplicas: 5    (don't drop below the Lab 7 base)
#     maxReplicas: 12
#     metrics:
#       - type: Resource
#         resource:
#           name: cpu
#           target: { type: Utilization, averageUtilization: 70 }
```

Apply, then drive CPU up with a Lab 10 Locust Job at high concurrency:

```bash
kubectl apply -f k8s/gateway-hpa.yaml

# Re-use the Locust runner from Lab 10 (you have labs/lab10/locust-runner.yaml).
# Edit the Job to -u 200 -r 20 -t 120s (or copy the manifest and rename to load-hpa).
# In a separate terminal:
kubectl get hpa gateway -w
```

Watch the `TARGETS` column climb past 70%, and `REPLICAS` step up toward `maxReplicas`. On single-node k3d the new pods schedule on the same node so it's not real elasticity — but you'll see the HPA controller making decisions, which is the point. Resource requests must be set on the gateway container for HPA to compute utilization (lab 4 added them already; double-check `kubectl get rollout gateway -o jsonpath='{.spec.template.spec.containers[0].resources}'`).

Skip this section if you're short on time — it's a small extra observation, not a graded component.

### Proof of work (Task 2)

**Paste into `submissions/lab12.md`:**

- The `preStop` / `readinessProbe` block as it appears in your `k8s/gateway.yaml`.
- 5xx count before / after the rolling restart (both should be 0).
- Your migration code (the autocommit_block wrapper is the key detail).
- 5xx count before / after the migration (both should be 0).
- `\d events` output showing the new `idx_events_event_date` index.
- The 3-migration + 2-deploy expand-and-contract sketch from 12.8 (write it as a numbered list, no code required).
- (Optional, if you did 12.9) Your HPA YAML and a screenshot of `kubectl get hpa` showing CPU utilization climbing under load.
- Answer: "Why does `CREATE INDEX CONCURRENTLY` matter? What happens if you omit it on a table with 10M rows?"
- Answer (from 12.8): "In your expand-and-contract sketch, why MUST migration 3 (drop old column) come after deploy B has fully rolled out? What goes wrong if it runs before?"

---

## How to Submit

```bash
git switch -c feature/lab12
git add k8s/pdb.yaml k8s/gateway.yaml k8s/events.yaml k8s/payments.yaml k8s/notifications.yaml migrations/ submissions/lab12.md
git commit -m "feat(lab12): PDBs, preStop, and zero-downtime migration"
git push -u origin feature/lab12
```

PR checklist:

```text
- [x] Task 1 done — multi-replica failover + 4 PDBs + topology spread + real eviction-API block
- [ ] Task 2 done — preStop + zero-error rolling restart + CONCURRENTLY migration + expand-and-contract sketch
- [ ] (Optional) 12.9 HPA observation
```

> 📝 **No "Bonus Task" in this lab.** Lab 12 is itself a bonus lab — Task 1 + Task 2 *are* the challenge. The lab's full 10 pts contribute toward your bonus-labs grade weight (see the course README).

---

## Acceptance Criteria

### Task 1 (6 pts)
- ✅ events / payments / notifications scaled to 2 replicas; manifests updated.
- ✅ Zero 5xx from Prometheus during coordinated pod-kill under mixedload.
- ✅ `k8s/pdb.yaml` with 4 PDBs; `kubectl get pdb` shows correct `ALLOWED DISRUPTIONS`.
- ✅ `topologySpreadConstraints` added to gateway Rollout (effect not visible on single-node k3d, but YAML correct and live in spec).
- ✅ HTTP 429 eviction rejection captured with `reason: DisruptionBudget`.

### Task 2 (4 pts)
- ✅ `preStop` + `readinessProbe` in gateway Rollout pod template.
- ✅ Zero 5xx during `kubectl argo rollouts restart gateway` under mixedload.
- ✅ Migration uses `CONCURRENTLY` with the `autocommit_block` wrapper.
- ✅ Zero 5xx during migration.
- ✅ New index visible in `\d events`.
- ✅ Expand-and-contract sketch in submissions/lab12.md (3 migrations + 2 code deploys, with rationale for the ordering).

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — Multi-replica + PDB + topology spread | **6** | Pods scaled, zero errors on kill, PDBs configured + topologySpreadConstraints in spec, real API-level eviction rejection captured |
| **Task 2** — Graceful shutdown + zero-downtime migration | **4** | preStop + probes wired, zero-error rolling restart, CONCURRENTLY migration under load, expand-and-contract sketch |
| **Total** | **10** | Task 1 + Task 2 |

---

## Resources

<details>
<summary>📚 Documentation</summary>

- [Reading 12](../lectures/reading12.md) — the patterns, with real outage examples.
- [K8s — PDB](https://kubernetes.io/docs/tasks/run-application/configure-pdb/)
- [K8s — Container Lifecycle Hooks](https://kubernetes.io/docs/concepts/containers/container-lifecycle-hooks/)
- [K8s — Pod Termination](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination)
- [PostgreSQL — Building Indexes Concurrently](https://www.postgresql.org/docs/current/sql-createindex.html#SQL-CREATEINDEX-CONCURRENTLY)
- [Alembic — Batched / Non-transactional Operations](https://alembic.sqlalchemy.org/en/latest/cookbook.html#run-alembic-operation-objects-directly-as-in-autogenerate-directive)

</details>

<details>
<summary>⚠️ Common Pitfalls</summary>

- **`kubectl rollout restart deployment/gateway` errors** — gateway is an Argo Rollouts `Rollout`, not a `Deployment`. Use `kubectl argo rollouts restart gateway`.
- **`kubectl drain --dry-run=server` lists every pod** — that's expected; drain evaluates each against its PDB in sequence. To see a real PDB rejection, tighten the PDB to `minAvailable == replicas` and issue a single eviction via the API (see 12.4).
- **`kubectl eviction` doesn't exist** — eviction is a subresource on `pods`. Use the API directly: POST `/api/v1/namespaces/<ns>/pods/<name>/eviction`.
- **`CREATE INDEX CONCURRENTLY cannot run inside a transaction block`** — Alembic wraps migrations in a transaction by default. Fix: `with op.get_context().autocommit_block():` around the DDL call.
- **preStop alone is not enough** — need BOTH preStop (blocks SIGTERM→SIGKILL window) AND a `readinessProbe` that fails quickly (kube-proxy removes the endpoint within ~2s). Without the probe, preStop sleep is wasted because the pod is still in endpoints.
- **`terminationGracePeriodSeconds` must cover preStop + in-flight request drain** — we use 40s (10s preStop + up to 30s uvicorn drain). A 30s grace period is NOT enough if preStop is already 10s.
- **Single-node k3d can't drain or spread** — there's nowhere to reschedule evicted pods (drain dry-runs work; real drains hang) and `topologySpreadConstraints` has no observable effect (every pod lands on the only node). Both are artifacts of the lab environment. In a real multi-node cluster `kubectl drain` is the standard way to take a node out of service, and topology spread is what makes that drain safe.
- **`kubectl proxy` cleanup** — `kill %1` depends on the proxy being shell job 1, which breaks if you have other backgrounded commands. Use `PROXY_PID=$!` and `trap 'kill $PROXY_PID' EXIT` in scripts (see the 12.5 reference solution).
- **`--wait=false` on delete** — without it, `kubectl delete pod` blocks until the `terminationGracePeriodSeconds` expires (could be 40s per pod). With multiple deletes in a test script, this adds up fast.

</details>
