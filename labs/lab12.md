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
- Add `preStop` hook + `readinessProbe` to the gateway Rollout so rolling restarts drop zero requests.
- Write an Alembic migration using `CREATE INDEX CONCURRENTLY` and run it under live load.

---

## Project State

**You should have from previous labs:**

- QuickTicket on k3d with 5-replica gateway Rollout (Lab 7) and Postgres on a PVC (Lab 9).
- In-cluster Prometheus (Lab 7 Bonus).
- `labs/lab8/mixedload.yaml` generating checkout traffic throughout the lab.
- An Alembic setup already initialized in Lab 9 (keep the venv + port-forward).

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
# Why different values:
#   - gateway is on the critical path → high minAvailable
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

### 12.4: Prove a PDB actually blocks eviction

The `kubectl drain --dry-run=server` output *lists* all pods as candidates (that's expected — drain evaluates each pod against PDBs in sequence, not upfront). To see a real PDB rejection, tighten the budget until it's impossible to satisfy and fire a single eviction:

```bash
# Make events-pdb impossible to satisfy (minAvailable=2 with 2 replicas = zero tolerance)
kubectl patch pdb events-pdb --type=merge -p '{"spec":{"minAvailable":2}}'
kubectl get pdb events-pdb                         # ALLOWED DISRUPTIONS should be 0

# Issue an Eviction via the API (kubectl doesn't have a direct eviction subcommand)
POD=$(kubectl get pod -l app=events -o jsonpath='{.items[0].metadata.name}')
kubectl proxy --port=8901 &
sleep 2
curl -X POST -H 'Content-Type: application/json' \
  -d "{\"apiVersion\":\"policy/v1\",\"kind\":\"Eviction\",
       \"metadata\":{\"name\":\"$POD\",\"namespace\":\"default\"}}" \
  http://localhost:8901/api/v1/namespaces/default/pods/$POD/eviction
# Expect: HTTP 429 TooManyRequests with "reason":"DisruptionBudget" and
# "message":"... needs 2 healthy pods and has 2 currently"

# Restore the PDB
kubectl patch pdb events-pdb --type=merge -p '{"spec":{"minAvailable":1}}'
kill %1 2>/dev/null      # stop the kubectl proxy
```

### Proof of work (Task 1)

**Commit `k8s/pdb.yaml` and the updated `k8s/{events,payments,notifications}.yaml` to your fork.**

**Paste into `submissions/lab12.md`:**

1. `kubectl get deploy,rollout` showing all services at their target replica counts.
2. The before/after 5xx count from Prometheus around the pod-kill test (should both be 0).
3. `kubectl get pdb` output.
4. The HTTP 429 JSON body from the tightened-PDB eviction test (proves PDB enforcement).
5. Answer: "With 3 gateway replicas and minAvailable: 1, what's the maximum number of pods that can be evicted simultaneously? Why is your `gateway-pdb` set to `minAvailable: 2` with 5 replicas?"

<details>
<summary>💡 Hints</summary>

- `kubectl delete pod <name>` — do NOT prefix with `pod/` when the resource is already `pod` by position; newer kubectl prints a confusing "no need to specify a resource type as a separate argument" warning but the delete still works. Use `--wait=false` to avoid blocking on grace period.
- `kubectl drain --dry-run=server` on a single-node k3d cluster shows all pods as eviction candidates. That's NOT a PDB failure — drain serializes evictions and respects the PDB one pod at a time. To see a PDB actually reject something, tighten the PDB (as 12.4) so even one eviction would violate it.
- The eviction API is at `POST /api/v1/namespaces/<ns>/pods/<name>/eviction` with a body of `{apiVersion: policy/v1, kind: Eviction, metadata: {name, namespace}}`. `kubectl eviction` / `kubectl eviction-request` do NOT exist.

</details>

---

## Task 2 — Graceful Shutdown + Zero-Downtime Migration (4 pts)

> ⏭️ Optional.

### 12.5: preStop hook + readinessProbe

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

### 12.6: `CREATE INDEX CONCURRENTLY` migration

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

### Proof of work (Task 2)

**Paste into `submissions/lab12.md`:**

- The `preStop` / `readinessProbe` block as it appears in your `k8s/gateway.yaml`.
- 5xx count before / after the rolling restart (both should be 0).
- Your migration code (the autocommit_block wrapper is the key detail).
- 5xx count before / after the migration (both should be 0).
- `\d events` output showing the new `idx_events_event_date` index.
- Answer: "Why does `CREATE INDEX CONCURRENTLY` matter? What happens if you omit it on a table with 10M rows?"

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
- [x] Task 1 done — multi-replica failover + 4 PDBs + real eviction block
- [ ] Task 2 done — preStop + zero-error rolling restart + CONCURRENTLY migration
```

> 📝 **No "Bonus Task" in this lab.** Lab 12 is itself a bonus lab — Task 1 + Task 2 *are* the challenge. The lab's full 10 pts contribute toward your bonus-labs grade weight (see the course README).

---

## Acceptance Criteria

### Task 1 (6 pts)
- ✅ events / payments / notifications scaled to 2 replicas; manifests updated.
- ✅ Zero 5xx from Prometheus during coordinated pod-kill under mixedload.
- ✅ `k8s/pdb.yaml` with 4 PDBs; `kubectl get pdb` shows correct `ALLOWED DISRUPTIONS`.
- ✅ HTTP 429 eviction rejection captured with `reason: DisruptionBudget`.

### Task 2 (4 pts)
- ✅ `preStop` + `readinessProbe` in gateway Rollout pod template.
- ✅ Zero 5xx during `kubectl argo rollouts restart gateway` under mixedload.
- ✅ Migration uses `CONCURRENTLY` with the `autocommit_block` wrapper.
- ✅ Zero 5xx during migration.
- ✅ New index visible in `\d events`.

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — Multi-replica + PDB | **6** | Pods scaled, zero errors on kill, PDBs configured, real API-level rejection captured |
| **Task 2** — Graceful shutdown + migration | **4** | preStop + probes wired, zero-error rolling restart, CONCURRENTLY migration under load |
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
- **Single-node k3d can't drain** — there's nowhere to reschedule the evicted pods. Drain dry-runs work; real drains hang. This is an artifact of the lab environment, not a lesson — in real clusters `kubectl drain` is the standard way to take a node out of service.
- **`--wait=false` on delete** — without it, `kubectl delete pod` blocks until the `terminationGracePeriodSeconds` expires (could be 40s per pod). With multiple deletes in a test script, this adds up fast.

</details>
