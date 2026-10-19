# Lab 12 — Advanced Kubernetes Resilience

## 0. Scope and acceptance map

This report documents the QuickTicket configuration for multi-replica failover,
controlled voluntary disruptions, termination-aware routing, and online database
schema evolution. The focus is on Kubernetes availability properties and the
ordering constraints that keep old and new application versions compatible.

| Part | Implementation | Primary artifacts |
|---|---|---|
| Task 1 (4 pts) | Replicas, disruption budgets, topology spreading | `k8s/{events,payments,notifications,gateway,pdb}.yaml` |
| Task 2 (4 pts) | PreStop and probe tuning, concurrent index, schema migration design | `k8s/gateway.yaml`, `migrations/versions/`, this report |
| Bonus (2 pts) | Expand/contract chain and staged Events releases | `app/events/main.py`, `app/seed.sql`, migrations and stage helper |
| Optional 12.9 | HPA targeting Argo Rollouts | `k8s/gateway-hpa.yaml` |

The report distinguishes the deployment design from cluster measurements.
Actual Kubernetes outputs belong to the evidence sections after executing the
commands below against the target k3d cluster.

### 0.1 Deployment topology

- One logical Gateway entry point, implemented as an Argo Rollouts `Rollout`.
- The Gateway Rollout defines five pods when autoscaling does not change the target.
- Two Events pods, two Payments pods, and two Notifications pods.
- Postgres and Redis are infrastructure dependencies.
- Mixedload represents concurrent HTTP checkout operations.
- Gateway, Events and Payments form the critical synchronous request path.
- Notifications is asynchronous and should not block successful checkout.
- A single-node local k3d setup cannot provide node-level redundancy.

### 0.2 Configuration snapshot

| Property | Applied value | Purpose |
|---|---:|---|
| Gateway replicas | 5 | Canary percentage and spare capacity |
| Events replicas | 2 | Keep one Events endpoint available |
| Payments replicas | 2 | Keep payment processing accessible |
| Notifications replicas | 2 | Reduce asynchronous notification outages |
| Gateway PDB `minAvailable` | 2 | Allow at most three healthy voluntary disruptions |
| Events PDB `minAvailable` | 1 | Protect synchronous event operations |
| Payments PDB `minAvailable` | 1 | Protect checkout operations |
| Notifications PDB `maxUnavailable` | 1 | Best-effort service with one eviction slot |
| Gateway `maxSkew` | 1 | Prefer balanced placement across node hostnames |
| Gateway `whenUnsatisfiable` | `ScheduleAnyway` | Avoid Pending pods on one-node k3d |
| Gateway `preStop` | 11 s | Allow endpoint removal to propagate |
| Gateway grace period | 42 s | Leave time for termination and active requests |
| Gateway readiness period | 2 s | Fast removal of an unready endpoint |
| Gateway readiness failure threshold | 1 | Fail readiness on one failed probe |
| Optional HPA minimum | 5 | Preserve baseline Gateway capacity |
| Optional HPA maximum | 11 | Bound burst scaling |
| Optional HPA CPU target | 72% | Autoscale against requested CPU |

The values related to Kubernetes object identity and service ports are not
renamed: selectors, Services, and client URLs must remain internally consistent.
A PDB's allowed disruption count depends on *currently healthy* pods, not only
on `spec.replicas` in the Deployment or Rollout template.

---

## 1. Task 1 — Multi-replica failover and disruption budgets

### 1.1 Why replicas matter

A single pod is a single application-level failure point. A Deployment with two
Ready replicas can remove or replace one instance while the Service continues
to route to the remaining endpoint. Replicas improve application availability;
placing all replicas on one physical node does not protect against node loss.

A `kubectl delete pod` operation is an involuntary deletion from the PDB point
of view. The ReplicaSet creates a replacement, but a PDB does not block this
delete request. For voluntary maintenance, the Eviction API checks disruption
budgets before allowing the pod to terminate.

### 1.2 Replica manifests

The Events, Payments and Notifications Kubernetes Deployments each declare:

```yaml
spec:
  replicas: 2
```

For Events and Payments, rolling update settings maintain service capacity:

```yaml
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxSurge: 1
    maxUnavailable: 0
```

`maxUnavailable: 0` means the Deployment controller should prepare a healthy
replacement before voluntarily removing an old replica in a rolling update.
This is distinct from a PDB, which applies to eviction through the Eviction API.

The final Events manifest points to `quickticket-events:lab12-b`, which is the
last application version of the bonus schema migration. Do **not** deploy this
image before the earlier migration steps have run. During the initial Task 1
checks, scale the currently running Events Deployment without swapping its
image to the final release.

### 1.3 Baseline commands

```bash
kubectl apply -f labs/lab8/mixedload.yaml
kubectl rollout status deployment/mixedload --timeout=90s
kubectl get deployments
kubectl get rollout gateway
kubectl get pods -o wide
kubectl get endpoints events payments notifications gateway
```

Record the output of each command, especially the `READY`, `AVAILABLE`, and
`NODE` columns. If a workload is not already healthy, stop and investigate
before trying to interpret the failover test.

### 1.4 PDB definitions

The combined `k8s/pdb.yaml` contains four `policy/v1` resources:

| PDB | Rule | Healthy pods required | Allowed evictions (all replicas Ready) |
|---|---|---:|---:|
| `gateway-pdb` | `minAvailable: 2` | 2 | 3 out of 5 |
| `events-pdb` | `minAvailable: 1` | 1 | 1 out of 2 |
| `payments-pdb` | `minAvailable: 1` | 1 | 1 out of 2 |
| `notifications-pdb` | `maxUnavailable: 1` | 1 remains when desired = 2 | 1 out of 2 |

A voluntary disruption does not include a spontaneous application crash. When
one Events instance fails, the Service may still have one endpoint, but the
`events-pdb` disruption allowance should drop to zero until replacement
capacity becomes healthy again.

### 1.5 Applying and inspecting budgets

```bash
kubectl apply -f k8s/pdb.yaml
kubectl get pdb
kubectl describe pdb gateway-pdb
kubectl describe pdb events-pdb
kubectl describe pdb payments-pdb
kubectl describe pdb notifications-pdb
```

With all configured pods Ready and PDB status updated by the controllers,
`ALLOWED DISRUPTIONS` is 3, 1, 1, 1 respectively. If a displayed value is smaller, inspect Ready pod count
and PDB selectors before concluding the configuration is broken.

### 1.6 Failure injection under live traffic

Check a recent 5xx increase. Query Prometheus through its existing
in-cluster Deployment:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
```

In another terminal, begin the independent client-side probe:

```bash
kubectl port-forward svc/gateway 8080:8080
```

In a third terminal:

```bash
python3 scripts/lab12_probe.py --url http://localhost:8080/events --seconds 70 --interval 0.15 > /tmp/lab12-pod-failover-probe.json
```

During the probe, delete one Events and one Gateway pod:

```bash
kubectl delete pod "$(kubectl get pod -l app=events -o jsonpath='{.items[0].metadata.name}')" --wait=false
kubectl delete pod "$(kubectl get pod -l app=gateway -o jsonpath='{.items[0].metadata.name}')" --wait=false
kubectl get pods -l 'app in (events,gateway)' -o wide
```

Read the probe output and the Prometheus query after the replacement pods
become Ready. The API-level counter observes responses produced by Gateway;
the client-side probe also detects refused connections and timeouts that never
reached Gateway. Therefore, a zero Gateway 5xx counter by itself does not prove
zero interruption as seen by the client.

### 1.7 Topology spread

Gateway uses one soft topology constraint:

```yaml
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        app: gateway
```

The `kubernetes.io/hostname` key groups pods by node. `maxSkew: 1` expresses
a scheduler preference to keep the difference between per-node pod counts
within one. `ScheduleAnyway` does not give a strict placement guarantee, but
it improves scheduling decisions without preventing a single-node cluster from
starting all five Gateway pods.

With three eligible nodes, five evenly distributed pods have counts `2/2/1`.
With seven pods, the corresponding balanced placement is `3/2/2`. On a single
node, all five reside together: this is expected and does not invalidate the
manifest or prove actual node-failure resilience.

### 1.8 Topology validation

```bash
kubectl get rollout gateway -o jsonpath='{.spec.template.spec.topologySpreadConstraints}' | python3 -m json.tool
kubectl get pod -l app=gateway -o wide
kubectl get nodes -o wide
```

### 1.9 Force a PDB rejection through the Eviction API

The PDB demonstration temporarily requires both Events pods to remain Ready,
which makes the first eviction impossible:

```bash
bash scripts/lab12_eviction.sh
```

The script performs these operations in order:

1. Patch `events-pdb` to `minAvailable: 2`.
2. Start `kubectl proxy` on loopback port 8901.
3. Read one real Events pod name.
4. POST a `policy/v1` Eviction object for that pod.
5. Print the HTTP status line and JSON response.
6. Restore `events-pdb` to `minAvailable: 1` on exit.
7. Stop the local proxy process.

For a healthy 2/2 Deployment, HTTP 429 with a `DisruptionBudget` detail means
the eviction was rejected correctly. The top-level Status reason may be
`TooManyRequests`; the detail entry supplies `DisruptionBudget`. This
expected response is fundamentally different from the HTTP 429 returned by a
Gateway application-level rate limiter.

### 1.10 Task 1 design questions

**Question: Three Gateway replicas with `minAvailable: 1`: how many may be
voluntarily evicted at once?**

Two, when all three are currently healthy. A minimum of one must remain,
so the nominal voluntary disruption allowance is `3 - 1 = 2`. If a replica
is already NotReady, the budget becomes stricter.

**Question: Why keep two Gateway replicas from a five-replica Rollout?**

Gateway is on the synchronous request path. Keeping two limits the immediate
capacity loss while allowing maintenance controllers to move more than one
pod at a time. A `minAvailable: 4` budget may unnecessarily block a drain when
one instance is already NotReady or under canary analysis.

**Question: How are five or seven pods placed on three nodes?**

Balanced distribution across three nodes is `2/2/1` for five pods and
`3/2/2` for seven pods. These distributions are not guaranteed by a soft
constraint; scheduler scoring and resource availability still matter.

---

## 2. Task 2 — Graceful shutdown and non-blocking DDL

### 2.1 Pod termination path

When Kubernetes begins pod deletion, its EndpointSlice state changes and
routing controllers need time to propagate the update. Without a deliberate
termination buffer, an application may close its socket before every upstream
client stops sending requests to that endpoint.

A `preStop` lifecycle hook delays the SIGTERM to the application. With an
11-second sleep, Kubernetes has a window to remove the terminating endpoint
from traffic distribution while the existing HTTP process still runs.

The grace period is 42 seconds. It includes the hook and leaves additional
time for active requests and the container process to stop. Long in-flight
requests might need further tuning in a real production deployment.

### 2.2 Gateway termination configuration

```yaml
terminationGracePeriodSeconds: 42
containers:
  - name: gateway
    lifecycle:
      preStop:
        exec:
          command: ["sh", "-c", "sleep 11"]
    readinessProbe:
      httpGet:
        path: /health
        port: 8080
      periodSeconds: 2
      failureThreshold: 1
      timeoutSeconds: 2
```

The actual complete configuration lives in `k8s/gateway.yaml`. Readiness
and liveness are separate concepts: a temporary failed readiness check removes
a pod from endpoints without automatically killing it, whereas repeated
liveness failures may restart the container.

### 2.3 Deploy and observe the Rollout

```bash
kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts status gateway --timeout=240s
kubectl get rollout gateway
kubectl get pods -l app=gateway -o wide
```

This is an Argo Rollouts `Rollout`, not a Kubernetes Deployment. A command like
`kubectl rollout restart deployment/gateway` targets the wrong resource kind.

### 2.4 Rolling restart under traffic

Make sure `mixedload` and a direct HTTP prober are both running. Save the
Gateway 5xx counter before the restart and repeat afterward:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))'
kubectl argo rollouts restart gateway
kubectl argo rollouts status gateway --timeout=240s
sleep 10
kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
```

A wider time window naturally includes more requests than a narrow window,
so values from `[1m]` and `[3m]` should not be treated as directly identical.
Record the duration and the interval for each query. For a stricter before/
after comparison, use an absolute counter on a stable target or calculate a
consistent rate over the same duration and scope.

### 2.5 Online index requirement

A normal PostgreSQL `CREATE INDEX` permits reads while blocking concurrent
writes. A concurrent index build uses lighter locking so writes can continue,
although it usually requires more scanning and additional waits for old
transactions. In a write-heavy system this is preferable to locking checkout
updates behind a long schema operation.

The Alembic operation uses:

```python
with op.get_context().autocommit_block():
    op.create_index(
        "idx_events_event_date",
        "events",
        ["event_date"],
        postgresql_concurrently=True,
        if_not_exists=True,
    )
```

The `autocommit_block()` is required because PostgreSQL prohibits
`CREATE INDEX CONCURRENTLY` inside a transaction block. The downgrade also
uses a concurrent index drop in an autocommit block.

The index is built on `event_date` while the old column still exists. The
bonus later adds a separate index on `scheduled_at` and eventually removes
`event_date`, at which point its old index disappears as well.

### 2.6 Migration database connection

Kali must connect to the Kubernetes PostgreSQL Service. A host-side port
forward uses 15432 to avoid collisions with any local database:

```bash
kubectl port-forward svc/postgres 15432:5432
```

In another Kali terminal:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install alembic==1.18.4 psycopg2-binary==2.9.11 sqlalchemy==2.0.49
alembic history
alembic current
```

Do not blindly stamp an already-versioned database. If no Alembic version
table exists and inspection confirms that `events` and `orders` already exist
from `app/seed.sql`, the supplied baseline can be stamped:

```bash
alembic stamp c12a00000001
```

### 2.7 Run *only* the index revision

```bash
alembic upgrade c12a00000002
alembic current
kubectl exec -i "$(kubectl get pod -l app=postgres -o name)" -- psql -U quickticket -d quickticket -c '\d events'
```

Do not run `alembic upgrade head` yet. The migration chain intentionally
contains later revisions that remove a column still used by old Events pods.

### 2.8 Verify index health

The PostgreSQL catalog tracks indexes that were created but are not valid.
If a concurrent index operation fails after making its catalog entry, merely
rerunning with `IF NOT EXISTS` may not repair the invalid index.

Use the following inspection query:

```sql
SELECT indexrelid::regclass AS index_name, indisvalid, indisready
FROM pg_index
WHERE indrelid = 'public.events'::regclass;
```

For a successfully created index, `indisvalid` and `indisready` should be true.
An invalid index requires explicit investigation and usually a concurrent drop
before rebuilding.

### 2.9 Why a tiny lab table is not a performance benchmark

A five-row demonstration table may build an index nearly instantaneously.
That says nothing about the blocking behavior of a ten-million-row table,
where lock duration, I/O, concurrent writes and slow transactions become
significant. The lab verifies *correct operational syntax and ordering*;
large-scale latency claims require measurements on a representative dataset.

---

## 3. Expand-and-contract design

### 3.1 Compatibility objective

The logical change is `events.event_date` to `events.scheduled_at`. A direct
rename or deletion creates a compatibility boundary: old application instances
still reference the previous name. Because Kubernetes rolling updates run two
application versions simultaneously for some time, the schema must support
both versions until the old one has exited.

### 3.2 Five-stage plan

**Migration 1: expand.** Add `scheduled_at TIMESTAMPTZ NULL` without
removing `event_date`. Create a new concurrent index on `scheduled_at`.
The original Events service continues reading the old column.

**Code deploy A: compatibility read.** Switch Events queries to
`COALESCE(e.scheduled_at, e.event_date)`. This lets unfilled legacy rows and
filled new rows return the same API date. If runtime event creation existed,
Deploy A would write to both columns; the current QuickTicket Events API
only reads event rows and reserves tickets, so there is no such insertion
endpoint. Event insertion is performed by the initial seed script.

**Migration 2: backfill.** Execute
`UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL`.
Then make `scheduled_at` non-nullable after verifying it was populated.
The fallback query continues working before and after the backfill.

**Code deploy B: contract-ready application.** Read only
`e.scheduled_at` and maintain the public JSON response key `date`. Verify
all Events pods have been replaced and old ReplicaSet pods no longer serve.

**Migration 3: contract.** Drop `event_date`, since no live code needs it.
Preserve the `scheduled_at` index. Update the fresh-database seed schema to
create and populate only the final column.

### 3.3 Safety invariants

- At every step, at least the *currently deployed* application can query events.
- Deploy A is introduced only after the new column exists.
- Migration 2 runs only after compatible reads have been deployed.
- Deploy B completes before the old column is dropped.
- The public `/events` JSON contract keeps a stable `date` field.
- The seed file describes the terminal schema for newly created databases.
- Every step has a clearly identified rollback boundary and a validation gate.

### 3.4 Why backfill should be batched at production scale

On a table with millions of rows, one huge `UPDATE` creates long-lived row
locks, many WAL records and replication pressure. A small table can use one
statement, but a production backfill should use batches with commits between
them. The batch-processing algorithm is:

```text
last_id = 0
repeat:
    open transaction
    rows = SELECT id FROM events
           WHERE id > last_id AND scheduled_at IS NULL
           ORDER BY id LIMIT 5000 FOR UPDATE SKIP LOCKED
    if rows empty: commit and stop
    UPDATE events SET scheduled_at = event_date WHERE id IN rows
    last_id = maximum id processed
    commit transaction
    sleep a short interval
```

The algorithm advances by primary key. Rows inserted behind the
cursor during backfill require a final catch-up pass before enforcing
`NOT NULL`. Monitoring replication lag and transaction duration is also
important on a real cluster.

### 3.5 Why the last migration must follow Deploy B

If any pod still references `e.event_date`, dropping the column makes that
pod's SELECT fail. Even one old instance can cause a user-visible intermittent
failure, because the Service continues load balancing among its Ready pods.
A successful `kubectl rollout status deployment/events` and inspection of the
active ReplicaSet are required before proceeding with destructive schema DDL.

---

## 4. Bonus — Staged live schema transition

### 4.1 Included revision chain

| Revision | Operation | Target schema |
|---|---|---|
| `c12a00000001` | Existing schema baseline | `event_date` exists |
| `c12a00000002` | Concurrent index on `event_date` | Original column indexed |
| `c12a00000003` | Add nullable `scheduled_at` and its index | Both columns |
| `c12a00000004` | Copy values and enforce NOT NULL | Both populated |
| `c12a00000005` | Drop `event_date` | Only `scheduled_at` |

The first revision is intentionally empty because the existing database was
initialized independently from Alembic. Stamping this revision is a bookkeeping
operation, not an instruction to recreate live production data.

### 4.2 Initial schema check

```bash
kubectl exec -i "$(kubectl get pod -l app=postgres -o name)" -- psql -U quickticket -d quickticket -c '\d events'
alembic current
```

### 4.3 First migration, new nullable column

```bash
alembic upgrade c12a00000003
alembic current
```

`scheduled_at` initially permits NULL. The original Events code must remain
running until this step finishes. It has no reference to the new column.

### 4.4 Deploy A with fallback reading

The committed `app/events/main.py` is the final Deploy B version. The helper
can temporarily convert the SQL reads for Deploy A:

```bash
python3 scripts/lab12_stage.py A
python3 -m py_compile app/events/main.py
docker build -t quickticket-events:lab12-a ./app/events
k3d image import -c quickticket quickticket-events:lab12-a
kubectl set image deployment/events events=quickticket-events:lab12-a
kubectl rollout status deployment/events --timeout=180s
```

The compatibility query now chooses `COALESCE(e.scheduled_at, e.event_date)`.
For a row with NULL `scheduled_at`, the old date remains available. The helper
can be run again safely: it normalizes the expression before inserting the
fallback version.

### 4.5 Backfill migration and NOT NULL

```bash
alembic upgrade c12a00000004
kubectl exec -i "$(kubectl get pod -l app=postgres -o name)" -- psql -U quickticket -d quickticket -c 'SELECT id, event_date, scheduled_at FROM events ORDER BY id LIMIT 5;'
```

The upgrade is idempotent with respect to existing non-NULL values because of
`WHERE scheduled_at IS NULL`. The schema validation must show that the new
column is populated for every row and has a `NOT NULL` constraint.

### 4.6 Deploy B with only the new column

```bash
python3 scripts/lab12_stage.py B
python3 -m py_compile app/events/main.py
docker build -t quickticket-events:lab12-b ./app/events
k3d image import -c quickticket quickticket-events:lab12-b
kubectl set image deployment/events events=quickticket-events:lab12-b
kubectl rollout status deployment/events --timeout=180s
kubectl get pods -l app=events -o wide
```

`main.py` returns to the final committed source after switching to B. This
version never uses `event_date` as a database column, although the SQL aliases
and JSON keys keep the API backward-compatible for existing clients.

### 4.7 Destructive contract migration

Before dropping anything, confirm the Events Deployment is fully updated and
there are no old version-A pods still serving. Then:

```bash
alembic upgrade c12a00000005
alembic current
kubectl exec -i "$(kubectl get pod -l app=postgres -o name)" -- psql -U quickticket -d quickticket -c '\d events'
```

### 4.8 Track 5xx across all five transitions

In addition to the Prometheus counter, run the independent request probe
throughout the migration. Take one absolute metric snapshot before and one
after, using the same query scope:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=sum(gateway_requests_total%7Bstatus%3D~%225..%22%7D)' > /tmp/lab12-5xx-baseline.json
```

After the last migration:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=sum(gateway_requests_total%7Bstatus%3D~%225..%22%7D)' > /tmp/lab12-5xx-final.json
cat /tmp/lab12-5xx-baseline.json
cat /tmp/lab12-5xx-final.json
```

`gateway_requests_total` can be partitioned by pod and reset when pods are
replaced. For robust conclusions about restarts, use `increase()` over
consistent time windows, plus the client-side prober. Distinguish HTTP 429
(from rate limiting) from server errors and network transport failures.

### 4.9 Dangerous reorderings

Dropping `event_date` before Deploy B finishes is the clearest way to cause
errors: old code still accesses a missing column. Deploying A before the
expand migration is also unsafe because A references a column that has not yet
been created. Enforcing `NOT NULL` before backfill would reject the migration
or leave legacy records inconsistent.

### 4.10 Rollback interpretation

Re-adding an old column and copying data during a downgrade restores a schema
shape, but it is not automatically a complete production rollback. If Deploy B
continues writing only `scheduled_at` after downgrading, the values in the
recreated `event_date` column can become stale. A safe rollback requires a
compatible application release, synchronized writes during transition,
validation of the old column and explicit coordination of schema + application
rollbacks. Treat irreversible operations as release gates.

### 4.11 Fresh database bootstrap

`app/seed.sql` now creates and populates `scheduled_at`. This file is meant
for **new** installations at the terminal schema. Do not execute it against an
existing migrated database: it is a bootstrap seed, not a safe data migration
or an idempotent production fixture-management system.

---

## 5. Optional HPA observation

### 5.1 HPA definition

`k8s/gateway-hpa.yaml` points to the Argo Rollouts scale subresource:

```yaml
scaleTargetRef:
  apiVersion: argoproj.io/v1alpha1
  kind: Rollout
  name: gateway
minReplicas: 5
maxReplicas: 11
```

CPU utilization is relative to the container's `resources.requests.cpu`, not
to its CPU limit. The target is 72% requested CPU utilization. The scale-down
stabilization window is 180 seconds to reduce oscillation after a short burst.

### 5.2 HPA prerequisites and observation commands

```bash
kubectl top pods -l app=gateway
kubectl get rollout gateway -o jsonpath='{.spec.template.spec.containers[0].resources}'
kubectl apply -f k8s/gateway-hpa.yaml
kubectl get hpa gateway -w
```

`kubectl top pods` requires metrics-server. If the metrics API is unavailable,
the HPA resource can exist but CPU utilization targets may display unknown
values and no scaling decision can be verified. On one-node k3d, new replicas
still land on the same node, so the HPA demonstrates control-loop behavior
rather than real compute elasticity.

---

## 6. Interpretation of the monitoring metrics

### 6.1 `gateway_requests_total`

This Prometheus Counter records HTTP requests handled by the Gateway and
labels them by status. A counter only increases within one running process.
On restart it begins again from zero. Therefore, the correct expression for
traffic errors across process restarts generally uses `rate()` or `increase()`.

PromQL for HTTP 5xx requests:

```promql
sum(increase(gateway_requests_total{status=~"5.."}[3m]))
```

This calculates the counter increase for Gateway-generated 500–599 responses
over a three-minute window. It does **not** count TCP connection failures that never
reached any Gateway process. This explains the need for the external prober.

### 6.2 HTTP statuses and their roles

| Status group | Interpretation | Relevant scenario |
|---|---|---|
| `2xx` | Successful application response | Normal Gateway service |
| `429` | Client throttling or PDB eviction rejection | Distinguish source by API |
| `5xx` | Server-side HTTP failure | Upstream failover or migration errors |
| No HTTP status | Connection failure, DNS or timeout | Termination gap before a response |

The Eviction API returns an HTTP 429 with a Kubernetes `Status` JSON document;
this is not evidence that Gateway throttled user requests. The source and
HTTP endpoint must be recorded with the status code.

### 6.3 Probe latency percentiles

The included Python prober prints p50, p95 and p99 latencies based on completed
attempt durations. The p95 value is the 95th percentile: 95% of observed
attempts finish at or below that latency, while the slowest 5% are above it.
Percentiles are not additive and cannot be averaged naively across pods.

### 6.4 Availability and throughput interpretation

A period with no HTTP 5xx does not prove zero downtime if clients see socket
connection errors. Conversely, a few expected HTTP 429 responses from the
application rate limiter do not mean the pods are down. Report both
successful/failed responses and the client-side transport failures, and state
the request volume during the observation interval.

### 6.5 Operational limitations

- Single-node k3d does not prove a multi-node failure tolerance claim.
- Pod termination timing varies with node resources and endpoint propagation.
- Prometheus scrape frequency may hide short-lived conditions.
- An empty Prometheus vector must be interpreted carefully, not blindly as a
  measured numeric zero for every possible client-facing failure.
- Building an index on a few rows does not benchmark production write blocking.
- HPA requires the metrics API to return CPU readings.
- Alembic migration versions should not be stamped without checking schema.
- Final-image references require images to exist in the k3d image store.

---

## 7. Static validation and reproducibility

The repository contains `tests/test_lab12_static.py` to check YAML object
structure, matching replica counts, PDB rules, topology-spread configuration,
Gateway shutdown settings, the migration chain, and final schema references.

Run locally with:

```bash
python3 -m pip install pytest PyYAML
python3 -m pytest -q tests/test_lab12_static.py
python3 -m py_compile app/events/main.py
python3 -m py_compile migrations/env.py
python3 -m py_compile migrations/versions/*.py
```

Static tests cannot replace a live execution of the load and eviction tests.
The most important live acceptance signals are client-side connection results,
PDB HTTP 429 on the Eviction API, successful Rollout status, and the final
schema columns after each intended revision.

### 7.1 Artifact inventory

- `k8s/pdb.yaml`: four PodDisruptionBudget resources.
- `k8s/events.yaml`: two replicas, rolling update, final Events image.
- `k8s/payments.yaml`: two replicas and readiness checks.
- `k8s/notifications.yaml`: two replicas preserving Lab 11 notification API.
- `k8s/gateway.yaml`: topology preference and termination-safe Rollout.
- `k8s/gateway-hpa.yaml`: optional CPU autoscaling control.
- `alembic.ini`, `migrations/env.py`: Alembic environment.
- `migrations/versions/`: ordered schema upgrade and downgrade logic.
- `app/events/main.py`: final query implementation using `scheduled_at`.
- `app/seed.sql`: bootstrap SQL for the final schema.
- `scripts/lab12_stage.py`: intermediate Deploy A and final Deploy B switch.
- `scripts/lab12_probe.py`: independent client request observation.
- `scripts/lab12_eviction.sh`: temporary strict-PDB rejection test.
- `tests/test_lab12_static.py`: fast configuration checks.

### 7.2 Completion checklist

- [x] Declare required replica counts in manifests.
- [x] Define all four PodDisruptionBudgets.
- [x] Add Gateway topology spread preference.
- [x] Add Gateway preStop and readiness configuration.
- [x] Implement the concurrent index migration and downgrade.
- [x] Document the expand-and-contract sequence.
- [x] Implement three staged schema revisions for the bonus.
- [x] Provide final Events query code and an intermediate Deploy A generator.
- [x] Update the seed schema for fresh databases.
- [x] Supply optional HPA manifest and test tooling.
- [ ] Attach live `kubectl get deploy,rollout` and PDB status output.
- [ ] Attach actual Prometheus and client-side probe results.
- [ ] Attach HTTP 429 Eviction API response.
- [ ] Attach rolling restart output and final migration schema evidence.
- [ ] Attach HPA observation output if optional HPA was tested.

---

## 8. Final technical conclusion

The manifests establish the intended resilience properties: spare replica
capacity, explicit voluntary-disruption policies, node-aware placement, and a
termination window that reduces the risk of upstream connections reaching a
closing server. The migration chain separates a live database rename into
small compatibility-preserving steps and ensures the original column is not
removed until the replacement application has completely rolled out.

The design deliberately separates configuration correctness from measured
availability. The static tests validate the artifact structure. To conclude
that the rollout and migration experienced zero user-facing interruption, the
included live checks must be executed and their actual outputs recorded.
