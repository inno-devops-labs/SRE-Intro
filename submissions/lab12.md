# Lab 12 — Advanced Kubernetes Resilience

## Setup and measurement

Starting state: five gateway Rollout replicas, one replica each of events/payments/notifications, PostgreSQL on its existing PVC, and Alembic revision `a68dfd8aa03c`. A PostgreSQL custom-format backup was saved to `/tmp/quickticket-before-lab12.dump` before schema changes. No orders, reservations or existing event records were deleted.

Existing events had accumulated reservations and exhausted apparent availability in earlier labs. Rather than measure mostly 409s, inserted **event 6**, “Lab 12 resilience validation”, with 100,000 tickets, and pointed a copy of Lab 8 mixedload at it. The existing held-counter bookkeeping issue remains outside this lab; the test event has enough capacity for the entire run. The original Lab 8 manifest was preserved.

Two mixedload replicas generated reserve/pay traffic throughout the required experiments. An independent [observer](../labs/lab12/observe.py) also performed list → reserve → pay chains once per second, with no client retries and a new TCP connection for each request. It recorded HTTP and transport failures independently of gateway metrics. It confirmed **316 orders**, with **316 successful list requests, 316 successful reservations, 316 successful payments, zero HTTP errors and zero transport errors** before the separate optional HPA experiment.

Evidence collection is reproducible with [snapshot.py](../labs/lab12/snapshot.py); [reproduction notes](../labs/lab12/README.md) describe the stage order. Prometheus snapshots include `sum(up{job="gateway"})=5` and hundreds of successful payments per minute, so a zero error value is not inferred from absent traffic or missing scrapes. `increase()` values are extrapolated, not exact integer request counts.

```sh
kubectl exec deployment/postgres -- pg_dump -U quickticket -d quickticket -Fc > /tmp/quickticket-before-lab12.dump
kubectl apply -f k8s/events.yaml -f k8s/payments.yaml -f k8s/notifications.yaml
kubectl apply -f k8s/gateway.yaml -f k8s/pdb.yaml
kubectl apply -f labs/lab12/mixedload.yaml
kubectl create configmap lab12-observer --from-file=observe.py=labs/lab12/observe.py
kubectl apply -f labs/lab12/observer.yaml
.venv/bin/python labs/lab12/snapshot.py before-pod-kill
```

The final events manifest references Deploy B. For replay against the old schema, keep the old image until expand completes; do not apply the final image at the beginning. Deployment stages used distinct imported tags `quickticket-events:lab12-a` and `quickticket-events:lab12-b` with `imagePullPolicy: Never`.

## Task 1 — Replicas, PDBs and topology

[Final replica output](lab12-evidence/final-replicas.txt) shows gateway 5/5 and events, payments, notifications each 2/2. The obsolete gateway Deployment from earlier labs remains 0/0; the Rollout owns the five active gateway pods.

[Four PDBs](../k8s/pdb.yaml) and their [observed status](lab12-evidence/pdb.txt):

```text
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
events-pdb          1               N/A               1
gateway-pdb         2               N/A               3
notifications-pdb   N/A             1                 1
payments-pdb        1               N/A               1
```

The gateway pod template contains:

```yaml
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        app: gateway
```

[Live topology field](lab12-evidence/topology.json) and [pod placement](lab12-evidence/placement.txt) confirm the configuration. All pods remain on `k3d-quickticket-server-0`, the only node. `ScheduleAnyway` is a preference, not a guarantee: on three equivalent eligible nodes the intended distribution is **2/2/1 for five pods**, **3/2/2 for seven**. Resource pressure or other scheduler constraints can override this preference. Single-node availability does not demonstrate survival of node failure.

**Failover experiment.** Deleted one gateway pod and one events pod while checkout traffic continued. Graceful termination was applied to internal services as well as the gateway to allow Service endpoint changes to propagate before shutdown. Both replacement pods became Ready; see [pod-kill output](lab12-evidence/pod-kill.txt).

| Observation | Before pod deletion | After pod deletion |
|---|---:|---:|
| Prometheus 5xx increase over 3 minutes | 0 | 0 |
| Observer confirmed checkouts | 83 | 107 |
| Observer HTTP/transport errors | 0 | 0 |

Snapshots: [before](lab12-evidence/before-pod-kill.json), [after](lab12-evidence/after-pod-kill.json). The raw cumulative 5xx sum was 3 in both snapshots: these were historical samples, not new failures. The earlier setup snapshot contained 42 historical failures from prior experiments. Pod replacement makes raw cumulative sums unsuitable for comparing across restarts; interval increases and the independent observer establish the result here.

**Real PDB rejection.** Temporarily set `events-pdb.minAvailable=2`, waited until `observedGeneration` matched and `disruptionsAllowed=0`, then POSTed a policy/v1 Eviction via a temporary kubectl proxy. Restored `minAvailable=1` in a `finally` block. [Actual HTTP response](lab12-evidence/eviction.txt):

```json
{"kind":"Status","apiVersion":"v1","metadata":{},"status":"Failure","message":"Cannot evict pod as it would violate the pod's disruption budget.","reason":"TooManyRequests","details":{"causes":[{"reason":"DisruptionBudget","message":"The disruption budget events-pdb needs 2 healthy pods and has 2 currently"}]},"code":429}
```

The HTTP status is **429**. The top-level reason is `TooManyRequests`; `DisruptionBudget` appears in `details.causes`, rather than the top-level field suggested by the handout.

**PDB design answer.** With three healthy replicas and `minAvailable: 1`, at most **two** voluntary evictions can be allowed concurrently. With five gateway replicas, `minAvailable: 2` allows up to **three** while retaining two serving replicas; their actual capacity still depends on workload. PDBs constrain the Eviction API, not direct `kubectl delete pod`, controller rolling updates, or node crashes. The pod-deletion experiment proves replica failover, while the separate eviction test proves PDB enforcement.

## Task 2 — Graceful shutdown and concurrent index

Gateway configuration:

```yaml
terminationGracePeriodSeconds: 40
containers:
  - name: gateway
    command: ["uvicorn", "main:app"]
    args: ["--host", "0.0.0.0", "--port", "8080", "--timeout-graceful-shutdown", "30"]
    lifecycle:
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

The grace period includes the hook; it does not start after it. Terminating endpoints become not-ready through Kubernetes endpoint handling; the sleep gives that change time to propagate while the app still accepts in-flight traffic. A readiness probe alone is not a synchronous guarantee of routing convergence. See [Kubernetes termination documentation](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/) and [hook timing](https://kubernetes.io/docs/concepts/containers/container-lifecycle-hooks/).

The internal services also use a 10-second preStop, 40-second grace period and quicker readiness checks. Events uses a maximum of **25 DB connections per pod**, replacing a manifest value of 50 (the prior live value was 10): three pods during surge can have 75 connections, leaving room under the usual 100-connection database limit. This is a bounded lab sizing choice, not a universal production capacity recommendation.

Installed the v1.9.0 Rollouts CLI temporarily under `/tmp/lab12-tools`; no global tool installation was needed. Ran:

```sh
/tmp/lab12-tools/kubectl-argo-rollouts-darwin-arm64 restart gateway
/tmp/lab12-tools/kubectl-argo-rollouts-darwin-arm64 status gateway --timeout=240s
```

[Restart output](lab12-evidence/restart.txt) reaches Healthy. [Creation timestamps](lab12-evidence/restart-at.txt) verify that all five pods were replaced after `restartAt`; a stale Healthy status was not accepted as proof. The restart itself replaces pods within the ReplicaSet; the preceding pod-template update passed its canary analysis.

| Experiment | 5xx increase before | 5xx increase after | Observer confirmations |
|---|---:|---:|---:|
| Gateway restart | 0 | 0 | 107 → 184 |
| Concurrent date index | 0 | 0 | 184 → 190 |

Snapshots: [after restart](lab12-evidence/after-restart.json), [after index](lab12-evidence/after-index.json). No observer errors occurred.

Created [revision b12000000001](../migrations/versions/b12000000001_index_events_event_date_concurrently.py) under live load:

```python
def upgrade():
    with op.get_context().autocommit_block():
        op.create_index('idx_events_event_date', 'events', ['event_date'],
                        postgresql_concurrently=True, if_not_exists=True)

def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index('idx_events_event_date', table_name='events',
                      postgresql_concurrently=True, if_exists=True)
```

```sh
kubectl port-forward svc/postgres 15432:5432
export DATABASE_URL=postgresql://quickticket:quickticket@localhost:15432/quickticket
.venv/bin/alembic upgrade b12000000001
```

[Schema/index evidence](lab12-evidence/schema-index.txt) includes `idx_events_event_date` and `indisvalid=t`. Later contract drops the old column and its dependent index; the final schema retains a separately created concurrent index on `scheduled_at`. This preserves indexing rather than silently losing it during the rename.

**Why CONCURRENTLY?** A normal index build blocks writes while it builds; on millions of rows that can create a substantial write outage. Contrary to the handout's wording, ordinary CREATE INDEX does **not** block normal SELECTs: it takes a SHARE lock rather than ACCESS EXCLUSIVE. CONCURRENTLY permits ordinary reads and writes but costs more work and can wait for transactions. An interrupted build can leave an invalid index; `IF NOT EXISTS` does not repair or validate it. The run explicitly checked validity. [PostgreSQL 17 reference](https://www.postgresql.org/docs/17/sql-createindex.html).

## Bonus — Executed expand-and-contract

The design sketch was executed in this order, without stopping mixedload:

1. **Migration 1 / expand (`b12000000002`):** add nullable `scheduled_at TIMESTAMPTZ`. Old code continues reading `event_date`.
2. **Deploy A:** read `COALESCE(scheduled_at, event_date)` and order by the same expression. The two SELECTs retain the internal `event_date` alias; external JSON retains **`date`**. QuickTicket has no runtime event-date INSERT/UPDATE route, so dual-writing is not applicable here. If writers existed, they would need an atomic dual-write or trigger before backfill.
3. **Migration 2 / backfill (`b12000000003`):** fill only NULL new values, enforce NOT NULL, and create the new date index concurrently. All six rows had matching old/new timestamps: [backfill evidence](lab12-evidence/backfill.txt).
4. **Deploy B:** read only `scheduled_at`, preserving the external `date` field. Wait for both B pods to be Ready **and for all A pods to be deleted**, including termination grace time. [Deploy output](lab12-evidence/deploy-b.txt) and snapshot record the actual B image.
5. **Migration 3 / contract (`b12000000004`):** drop `event_date` only after step 4. Update the seed's table definition and INSERT columns to `scheduled_at`, include the earlier email column and final date index for fresh bootstrap.

The patches preserve the actual application transitions: [old → A](../labs/lab12/deploy-a.patch) and [A → B](../labs/lab12/deploy-a-to-b.patch). No runtime date writer was invented solely to satisfy a dual-write example. The final [events source](../app/events/main.py) and [seed](../app/seed.sql) agree on the new column.

| Snapshot | Observer confirmed orders (cumulative) | Prometheus cumulative 5xx | Observer errors |
|---|---:|---:|---:|
| Before expand (after index) | 190 | 0 | 0 |
| After expand | 197 | 0 | 0 |
| After Deploy A | 231 | 0 | 0 |
| After backfill | 238 | 0 | 0 |
| After Deploy B | 276 | 0 | 0 |
| After contract | 283 | 0 | 0 |
| Final normal-load check | 316 | 0 | 0 |

All snapshots are in [lab12-evidence](lab12-evidence/), named `after-*.json` and `core-final.json`. Gateway pods remained unchanged throughout these five migration transitions, so cumulative 5xx sums are directly comparable here. That is distinct from the earlier gateway-restart experiment.

Extracted numeric Prometheus values, rather than comparing whole responses containing changing timestamps:

```sh
cat submissions/lab12-evidence/5xx.baseline
# 0
cat submissions/lab12-evidence/5xx.final
# 0
diff submissions/lab12-evidence/5xx.baseline submissions/lab12-evidence/5xx.final
# no output, exit status 0
```

The independent observer completed **93 successful checkouts during the five-step sequence** (190 → 283); mixedload concurrently supplied additional successful traffic. [Before schema](lab12-evidence/schema-before.txt) has `event_date NOT NULL`; [final schema](lab12-evidence/schema-after.txt) has **no event_date**, **scheduled_at TIMESTAMPTZ NOT NULL**, and a valid `idx_events_scheduled_at`. [API evidence](lab12-evidence/final-api.txt) verifies list and detail responses still contain `date`.

### Migration code

See the four migration files under [migrations/versions](../migrations/versions/). Upgrade bodies for the three rename steps:

```python
# b12000000002: expand
op.execute("SET LOCAL lock_timeout = '3s'")
op.add_column('events', sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True), nullable=True))

# b12000000003: backfill
op.execute("SET LOCAL lock_timeout = '3s'")
op.execute('UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL')
op.alter_column('events', 'scheduled_at', nullable=False)
with op.get_context().autocommit_block():
    op.create_index('idx_events_scheduled_at', 'events', ['scheduled_at'],
                    postgresql_concurrently=True, if_not_exists=True)

# b12000000004: contract
op.execute("SET LOCAL lock_timeout = '3s'")
op.drop_column('events', 'event_date')
```

Alembic's environment uses `transaction_per_migration=True`, and supports a `DATABASE_URL` override for the port-forward and isolated verification databases. Revision boundaries are explicit: no live `upgrade head` was run ahead of the necessary application deployments. The short lock timeout makes DDL fail instead of waiting indefinitely; on failure the deployment sequence must stop and be investigated.

**Which reordered step would break requests?** Contract (Migration 3) before Deploy B finishes. Even Deploy A's COALESCE still names `event_date`; SQL parsing fails if that column is gone, regardless of the new column's contents. Deploying A before expansion would also fail because `scheduled_at` would not exist yet.

**Why is the backfill safe here?** There are six rows and no concurrent event-date writers. COALESCE handles both pre- and post-backfill rows, and the WHERE clause avoids overwriting populated new values. In a production system, that WHERE clause alone is not a concurrency protocol: old-only writers must be drained or synchronized, and constraints must allow all overlapping writer versions. In particular, Deploy B cannot insert only scheduled_at while an old event_date NOT NULL constraint remains unless a trigger/default/constraint transition supplies compatibility.

**Production batching pattern (8 lines):**

```text
repeat:
    begin transaction
    ids = SELECT id FROM events WHERE scheduled_at IS NULL
          ORDER BY id LIMIT 10000 FOR UPDATE SKIP LOCKED
    UPDATE events SET scheduled_at = event_date WHERE id IN ids AND scheduled_at IS NULL
    commit
    if ids is empty: exit only after a final NULL check and writer-convergence check
    sleep briefly; monitor lock wait, replication lag and request latency
```

A production NOT NULL change should use a NOT VALID check, validate it separately, then perform the brief metadata transition where supported. One giant UPDATE takes row locks on modified rows and can block conflicting writes, rather than universally locking every table write. The tiny lab intentionally uses one transaction.

**Why is downgrade alone not a safe production rollback?** Contract's downgrade re-adds event_date, copies dates back and recreates its index. That restores a schema snapshot, not continuing synchronization: new-only writers can make the restored old column stale, or fail its NOT NULL constraint. Safe rollback requires a verified backup, a compatible writer strategy (dual-write/trigger or a controlled write pause), reconciliation of concurrent updates, and a code rollback only after its required columns and constraints are ready. This lab has no runtime date writers, but that does not make the generic production rollback safe.

## Verification and review

- [Migration round trip](lab12-evidence/migration-roundtrip.txt): upgraded a disposable database from the old seed through all four revisions, checked dates and valid index, downgraded to Lab 9, verified restored dates, then upgraded again. Temporary database removed. The live database was never downgraded.
- [Fresh installation](lab12-evidence/fresh-install.txt): loaded the final seed into another disposable database, checked five seeded dates and index validity, stamped the verified final schema, and called startup/list/detail using the actual Deploy B service code against that database. It succeeded; temporary database removed.
- A fresh seed already represents the final schema and must be stamped at `b12000000004`; historical migrations must not be replayed over that final seed. Existing databases follow the staged migrations instead.
- Reviewed stage ordering, preserved API response shape, reversible schema changes, new-index retention, connection capacity across surge, PDB restoration and test cleanup. No new unit-test suite was added; verification targets the real PostgreSQL/Kubernetes behavior required by this lab.
- Limits: one node cannot demonstrate node-loss resilience; zero observed errors is a result for this workload, not a guarantee under arbitrary load. Existing inventory-counter issues, best-effort notifications and mock-payment idempotency remain outside this lab.

## Optional HPA observation (12.9)

Applied [gateway-hpa.yaml](../k8s/gateway-hpa.yaml) targeting `argoproj.io/v1alpha1 Rollout/gateway`, with min=5, max=12, CPU utilization target=70%. Ran the Lab 10 task mix with 200 users, spawn rate 20/s, duration 120s; its reservations targeted event 6. This experiment ran **after** final normal-load/migration evidence was saved and the independent observer was stopped.

[Actual HPA samples](lab12-evidence/hpa-observation.txt):

```text
2026-10-18T10:40:17Z  cpu: 93%/70%   replicas: 5
2026-10-18T10:40:33Z  cpu: 101%/70%  replicas: 7
2026-10-18T10:41:28Z  cpu: 76%/70%   replicas: 8
2026-10-18T10:41:33Z  cpu: 67%/70%   replicas: 8
```

CPU percentages are relative to the configured 50m request, so values above 100% are valid. All replicas still use the same single k3d node: this demonstrates HPA decisions, not additional node capacity.

[Locust output](lab12-evidence/hpa-locust.txt) reports 18,432 requests, 153.79 requests/s, and 7,847 failures. **All reported failures were HTTP 429** from the Lab 11 rate limiter (7,495 event reads and 352 reservations); no 5xx or transport errors were listed. Locust therefore exited 1 and Kubernetes marked its Job Failed. This is recorded as expected overload rejection, not a successful zero-error capacity test. HPA scaled 5 → 7 → 8 despite the limiter.

After recording [final HPA state](lab12-evidence/hpa-final.yaml), deleted the temporary HPA, Locust Job and ConfigMap and restored gateway replicas to five. The optional HPA manifest remains in the repository for deliberate reuse; it is not left active.

## Final state

[Cleanup evidence](lab12-evidence/cleanup.txt) records the restored five-replica gateway, two replicas of each downstream, original PDB budgets, healthy API and zero fault-injection settings. Restored the original Lab 8 mixedload manifest (two replicas); the Lab 12 variant is retained solely for reproduction. The dedicated test event and its orders remain in the database. Observer resources and temporary verification databases were removed. No commit, staging or push was performed for Lab 12.

Validation commands also passed:

```sh
.venv/bin/python -m compileall -q app/events migrations labs/lab12
kubectl apply --dry-run=server -f k8s/events.yaml -f k8s/payments.yaml \
  -f k8s/notifications.yaml -f k8s/gateway.yaml -f k8s/pdb.yaml -f k8s/gateway-hpa.yaml
git diff --check
```

Server validation output: [manifest-validation.txt](lab12-evidence/manifest-validation.txt). Final Alembic head and actual PostgreSQL max_connections=100: [db-final-state.txt](lab12-evidence/db-final-state.txt).
