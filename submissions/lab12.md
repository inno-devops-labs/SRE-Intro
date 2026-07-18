# Lab 12 Report — High Availability, Graceful Shutdown, and Zero-Downtime Migration

## 1. Overview

This lab focused on improving the production readiness of the QuickTicket system. The work covered two main areas:

1. Improving service availability in Kubernetes.
2. Performing application restarts and database schema changes without introducing service downtime.

The lab used Kubernetes, Argo Rollouts, Prometheus, PostgreSQL, and Alembic.

The final implementation included:

- Multiple replicas for critical services.
- PodDisruptionBudgets.
- Gateway topology spreading.
- Graceful pod termination.
- Fast readiness detection.
- Failover and disruption testing.
- A PostgreSQL concurrent index migration.
- Validation that no new HTTP 5xx errors were introduced.
- An expand-and-contract schema migration design.

---

## 2. Environment and Tools

The following tools and components were used during the lab:

| Tool / Component | Purpose |
|---|---|
| Kubernetes | Running and managing the QuickTicket services |
| Argo Rollouts | Managing the gateway rollout and restart process |
| Prometheus | Checking gateway HTTP 5xx metrics |
| PostgreSQL | QuickTicket relational database |
| Alembic | Managing database migrations |
| `kubectl` | Inspecting and modifying Kubernetes resources |
| `psql` inside the PostgreSQL pod | Inspecting the live database schema |
| Python virtual environment | Running Alembic and PostgreSQL client libraries |

The work was completed on the following branch:

```text
feature/lab12
```

---

# 3. Task 1 — High Availability

## 3.1 Objective

The goal of this task was to make the QuickTicket services more resilient to pod failures, voluntary disruptions, and node-level scheduling issues.

A service with only one pod becomes unavailable when that pod is restarted, deleted, evicted, or scheduled on a failed node. To reduce this risk, multiple replicas and disruption controls were configured.

---

## 3.2 Replica Configuration

The service replica counts were configured as follows:

| Service | Replica Count |
|---|---:|
| Gateway | 5 |
| Events | 2 |
| Payments | 2 |
| Notifications | 2 |

The gateway received five replicas because it is the main entry point for client traffic. The backend services received two replicas each so one instance could remain available if the other instance failed.

The Kubernetes manifests used for this configuration were:

```text
k8s/gateway.yaml
k8s/events.yaml
k8s/payments.yaml
k8s/notifications.yaml
```

---

## 3.3 PodDisruptionBudgets

A new file was created:

```text
k8s/pdb.yaml
```

The PodDisruptionBudget rules were:

| Service | Policy |
|---|---|
| Gateway | `minAvailable: 2` |
| Events | `minAvailable: 1` |
| Payments | `minAvailable: 1` |
| Notifications | `maxUnavailable: 1` |

These policies protect the services during voluntary disruptions such as node draining or manual eviction.

For example, the Events service has two replicas and requires at least one available instance. Kubernetes therefore allows one Events pod to be disrupted, but it blocks an eviction if it would reduce availability below one pod.

---

## 3.4 Gateway Availability Improvements

The gateway Rollout was updated with several availability controls.

### Termination grace period

```text
terminationGracePeriodSeconds: 40
```

This gives the application enough time to finish its shutdown procedure before Kubernetes forcefully terminates the container.

### preStop delay

The gateway received a 10-second preStop delay.

Its purpose is to create time for Kubernetes endpoint changes to propagate before the application process receives SIGTERM. This reduces the chance that new traffic is sent to a pod that is already shutting down.

### Readiness probe

The readiness probe was configured with a short interval and immediate failure detection:

```text
periodSeconds: 2
failureThreshold: 1
```

This allows Kubernetes to remove an unhealthy or terminating gateway pod from active service endpoints quickly.

### Topology spread constraints

Topology spread constraints were added so the gateway pods would be distributed more evenly instead of being concentrated on the same node or topology domain.

This reduces the impact of a node failure.

---

## 3.5 Initial Cluster Validation

The cluster state was inspected before failover testing.

The following commands were used:

```bash
kubectl get pods
kubectl get pods -o wide
kubectl get pdb
kubectl argo rollouts status gateway --timeout=240s
```

These commands were used to verify:

- All service pods were running.
- The expected number of replicas existed.
- Gateway pods were ready.
- PodDisruptionBudgets were active.
- The Argo Rollout was healthy.

The gateway status later confirmed:

```text
Healthy
```

The gateway pod check showed five running and ready pods:

```text
gateway-64f8c55856-2qqkg   1/1   Running
gateway-64f8c55856-4kfkl   1/1   Running
gateway-64f8c55856-6f479   1/1   Running
gateway-64f8c55856-8kxpx   1/1   Running
gateway-64f8c55856-hn45f   1/1   Running
```

This confirmed that the configured gateway replica count was active.

---

## 3.6 Events Service Failover Test

An Events pod was deleted to simulate a pod failure.

The test procedure used Kubernetes commands to:

1. Identify an Events pod.
2. Delete the pod.
3. Observe the remaining replica.
4. Verify that Kubernetes created a replacement pod.
5. Confirm that the service returned to its desired replica count.

This test demonstrated the Kubernetes self-healing mechanism.

The result was successful:

- One Events pod was deleted.
- The remaining Events pod stayed available.
- Kubernetes automatically started a replacement pod.
- The service returned to the expected replica count.

The evidence was saved in:

```text
evidence/lab12/events-failover.txt
```

---

## 3.7 Gateway Failover Test

A gateway pod was deleted to simulate failure of one traffic-serving instance.

The gateway had five replicas, so deleting one pod did not remove the entire entry point.

The test confirmed:

- Traffic capacity remained available through the other gateway replicas.
- Kubernetes created a replacement gateway pod.
- The Argo Rollout returned to a healthy state.
- No persistent service outage occurred.

The evidence was saved in:

```text
evidence/lab12/gateway-failover.txt
```

---

## 3.8 PodDisruptionBudget Eviction Test

The Events service was temporarily reduced to one replica to test the PodDisruptionBudget.

With:

```text
minAvailable: 1
```

and only one running Events pod, evicting that pod would reduce availability to zero.

An eviction attempt was performed, and Kubernetes blocked it.

This was the expected result because the disruption would have violated the PodDisruptionBudget.

The evidence was saved in:

```text
evidence/lab12/pdb-blocked-eviction.txt
```

This test proved that the PodDisruptionBudget was not only present, but actively enforced.

---

## 3.9 Mixed-Load Validation After Failover

After the failover tests, the application was checked under mixed traffic.

The purpose was to verify that the services still handled requests correctly after pod replacement and recovery.

The validation showed:

- Services recovered correctly.
- No persistent 500 or 503 errors were observed.
- The final cluster state was healthy.

The evidence was stored in:

```text
evidence/lab12/mixedload-after-failover.txt
evidence/lab12/final-availability-status.txt
```

---

## 3.10 Task 1 Result

Task 1 was completed successfully.

The final system provided:

- Redundant service replicas.
- Protection from voluntary disruption.
- Automatic pod replacement.
- Improved gateway distribution.
- Continued service availability after pod deletion.
- Verified PDB enforcement.
- No persistent 500 or 503 failures during the validation.

---

# 4. Task 2 — Graceful Shutdown

## 4.1 Objective

The purpose of this task was to verify that the gateway could restart without producing HTTP 5xx errors.

A normal container shutdown can create a short failure window:

1. Kubernetes sends SIGTERM.
2. The application begins shutting down.
3. The pod may still exist in service endpoints temporarily.
4. New requests may continue arriving.
5. Those requests may fail.

The readiness probe and preStop delay were used to reduce this risk.

---

## 4.2 Graceful Shutdown Sequence

The intended shutdown sequence was:

1. A gateway pod begins termination.
2. Kubernetes marks the pod as not ready.
3. The endpoint change propagates.
4. New requests stop being routed to that pod.
5. The preStop delay gives propagation time.
6. SIGTERM reaches the application.
7. In-flight requests are allowed to finish.
8. Kubernetes removes the pod.

The configured 40-second termination grace period provides enough time for:

- The 10-second preStop delay.
- Endpoint propagation.
- Application shutdown.
- In-flight request completion.

---

## 4.3 Gateway Health Verification

Before restarting the gateway, the Rollout and pods were checked:

```bash
kubectl argo rollouts status gateway --timeout=240s
kubectl get pods -l app=gateway
```

The output showed:

```text
Healthy
```

and five ready gateway pods.

This ensured the restart test started from a healthy state.

---

## 4.4 Prometheus Check Before Restart

The gateway HTTP 5xx metric was queried from Prometheus before restarting the Rollout:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))'
```

The response was:

```json
{"status":"success","data":{"resultType":"vector","result":[]}}
```

An empty result means Prometheus found no matching 5xx increase in the selected time window.

The value was interpreted as:

```text
before: 0
```

The result was saved in:

```text
evidence/lab12/graceful-restart-5xx-before.json
```

---

## 4.5 Gateway Rolling Restart

The gateway was restarted using the Argo Rollouts command:

```bash
kubectl argo rollouts restart gateway
```

The command confirmed:

```text
rollout 'gateway' restarts in 0s
```

The rollout status was monitored using:

```bash
kubectl argo rollouts status gateway --timeout=240s
```

The rollout moved through:

```text
Progressing - rollout is restarting
```

and finally returned:

```text
Healthy
```

This confirmed that all gateway replicas restarted successfully.

---

## 4.6 Prometheus Check After Restart

After the restart completed, the system was allowed to settle:

```bash
sleep 10
```

The three-minute 5xx increase was then queried:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
```

The response was again:

```json
{"status":"success","data":{"resultType":"vector","result":[]}}
```

This was interpreted as:

```text
after: 0
```

The result was saved in:

```text
evidence/lab12/graceful-restart-5xx-after.json
```

---

## 4.7 Restart Result

The final comparison was:

| Metric | Result |
|---|---:|
| Gateway 5xx before restart | 0 |
| Gateway 5xx after restart | 0 |

This confirmed that the gateway rolling restart introduced no new HTTP 5xx errors.

The graceful shutdown configuration worked as intended.

---

# 5. Zero-Downtime PostgreSQL Migration

## 5.1 Objective

The goal was to create an index on:

```text
events(event_date)
```

without blocking normal database traffic.

A normal PostgreSQL index creation may block writes while the index is built. On a production table with millions of rows, this can cause:

- Request delays.
- Lock contention.
- Timeouts.
- Failed requests.
- Application downtime.

PostgreSQL supports:

```text
CREATE INDEX CONCURRENTLY
```

which allows reads and writes to continue while the index is being created.

---

## 5.2 Alembic Setup

Alembic configuration and migrations were reused from Lab 9.

The available migration chain was checked with:

```bash
alembic history
```

The output was:

```text
80e41ae35bfd -> a06ff4ace509 (head), index events.event_date concurrently
71bb81f90644 -> 80e41ae35bfd, add email column to events
<base> -> 71bb81f90644, baseline - pre-existing schema
```

This showed a valid linear migration chain:

1. Baseline migration.
2. Add the `email` column.
3. Add the concurrent index.

---

## 5.3 Database Connection Verification

The local machine did not have the `psql` command installed.

Instead of installing another client, the PostgreSQL client inside the running database pod was used.

The database pod was identified with:

```bash
POSTGRES_POD=$(kubectl get pod -l app=postgres \
  -o jsonpath='{.items[0].metadata.name}')
```

The selected pod was:

```text
postgres-68466c5ccd-gcq7v
```

The schema was inspected with:

```bash
kubectl exec "$POSTGRES_POD" -- \
  psql -U quickticket -d quickticket -c '\d events'
```

Before the migration, the Events table contained:

```text
id
name
venue
event_date
total_tickets
price_cents
```

The only index was:

```text
events_pkey
```

This confirmed that the new `event_date` index did not exist yet.

The initial schema was saved in:

```text
evidence/lab12/events-schema-before-index.txt
```

---

## 5.4 Alembic Database Connectivity

A PostgreSQL port-forward was used because `alembic.ini` connects to:

```text
localhost:5432
```

The port-forward command was:

```bash
kubectl port-forward svc/postgres 5432:5432
```

Database connectivity was verified from Python using `psycopg2`.

The query returned:

```text
('quickticket', 'quickticket')
```

This confirmed:

- The database name was `quickticket`.
- The connected database user was `quickticket`.
- Alembic could reach the PostgreSQL service.

---

## 5.5 Migration Creation

The current Alembic state was inspected:

```bash
alembic current
alembic heads
```

Before applying the migration, `alembic heads` showed:

```text
80e41ae35bfd (head)
```

A new revision was created:

```bash
alembic revision -m "index events.event_date concurrently"
```

Alembic generated:

```text
migrations/versions/a06ff4ace509_index_events_event_date_concurrently.py
```

The migration was configured to:

- Create the index concurrently.
- Run outside Alembic's normal transaction block.
- Avoid failure if the index already exists.
- Support a reversible downgrade.

The index name was:

```text
idx_events_event_date
```

---

## 5.6 Why an Autocommit Block Was Required

Alembic normally runs PostgreSQL migrations inside a transaction.

PostgreSQL does not allow:

```text
CREATE INDEX CONCURRENTLY
```

inside a transaction.

Without an autocommit block, PostgreSQL would reject the operation with an error similar to:

```text
CREATE INDEX CONCURRENTLY cannot run inside a transaction block
```

Therefore, the migration used Alembic's autocommit mechanism so PostgreSQL could execute the concurrent index operation correctly.

---

## 5.7 HTTP 5xx Check Before Migration

Before applying the migration, the cumulative gateway 5xx metric was saved:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(gateway_requests_total%7Bstatus%3D~%225..%22%7D)' \
  > evidence/lab12/migration-5xx-before.json
```

This file represented the 5xx counter immediately before the schema change.

---

## 5.8 Applying the Migration

The migration was applied with:

```bash
time alembic upgrade head
```

The recorded output was:

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 71bb81f90644, baseline - pre-existing schema
INFO  [alembic.runtime.migration] Running upgrade 71bb81f90644 -> 80e41ae35bfd, add email column to events
INFO  [alembic.runtime.migration] Running upgrade 80e41ae35bfd -> a06ff4ace509, index events.event_date concurrently
```

The execution time was:

```text
0.279 seconds total
```

The output was stored in:

```text
evidence/lab12/alembic-concurrent-index.txt
```

The migration completed quickly because the Events table contained only a small number of rows.

The important result was not the duration itself, but the use of the production-safe concurrent index syntax.

---

## 5.9 Migration Revision Verification

The current revision was checked using:

```bash
alembic current
```

The result was:

```text
a06ff4ace509 (head)
```

This confirmed that all migrations were successfully applied.

---

## 5.10 Database Schema Verification

The Events schema was inspected again:

```bash
kubectl exec "$POSTGRES_POD" -- \
  psql -U quickticket -d quickticket -c '\d events'
```

After the migration, the table contained the additional `email` column and the new index:

```text
email character varying(255)
```

and:

```text
"idx_events_event_date" btree (event_date)
```

The output was saved in:

```text
evidence/lab12/events-schema-after-index.txt
```

The index was also verified directly with:

```bash
grep idx_events evidence/lab12/events-schema-after-index.txt
```

The result was:

```text
"idx_events_event_date" btree (event_date)
```

This proved that the concurrent index was successfully created.

---

## 5.11 HTTP 5xx Check After Migration

After the migration, the system waited briefly:

```bash
sleep 5
```

The gateway 5xx counter was captured again:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(gateway_requests_total%7Bstatus%3D~%225..%22%7D)' \
  > evidence/lab12/migration-5xx-after.json
```

The before and after metric files were compared:

```bash
diff \
  evidence/lab12/migration-5xx-before.json \
  evidence/lab12/migration-5xx-after.json
```

The command produced no output.

For `diff`, no output means the files were identical.

Therefore:

```text
No additional gateway HTTP 5xx responses occurred during the migration.
```

---

## 5.12 Migration Result

The migration test was successful.

| Validation | Result |
|---|---|
| Migration reached Alembic head | Yes |
| Final revision | `a06ff4ace509` |
| Index created | `idx_events_event_date` |
| Index type | B-tree |
| Indexed column | `event_date` |
| Migration duration | ~0.279 seconds |
| Additional gateway 5xx errors | 0 |

---

# 6. Expand-and-Contract Migration Design

## 6.1 Scenario

A future schema change may require renaming:

```text
event_date
```

to:

```text
scheduled_at
```

Directly renaming or dropping the old column in one step is unsafe during a rolling deployment.

During a rollout, old and new application versions may run at the same time.

If the database schema changes before all old pods are removed, older pods may fail because they still expect `event_date`.

The safe approach is expand-and-contract.

---

## 6.2 Phase 1 — Expand

A new nullable column is added:

```text
scheduled_at
```

The old column remains available.

At this point:

- Old application versions continue using `event_date`.
- New application versions can begin supporting `scheduled_at`.
- The schema is backward compatible.

---

## 6.3 Deploy A — Compatibility Version

A compatibility application version is deployed.

This version:

- Writes to both `event_date` and `scheduled_at`.
- Reads `scheduled_at` when available.
- Falls back to `event_date` for older rows.

This allows old and new pods to run together safely.

No old application instance is broken because `event_date` still exists.

---

## 6.4 Phase 2 — Backfill

Existing rows are copied from the old column to the new column.

Only rows where `scheduled_at` is still empty need to be updated.

This design is useful because:

- The operation can be restarted.
- Existing new values are not overwritten.
- Deploy A already dual-writes new data.
- The backfill can run while the application remains online.

After the backfill, the system verifies that no rows have a null `scheduled_at`.

The new column can then be made required.

---

## 6.5 Deploy B — New Column Only

A second application deployment is performed.

Deploy B:

- Reads only `scheduled_at`.
- Writes only `scheduled_at`.
- No longer depends on `event_date`.

The deployment must fully finish before removing the old column.

---

## 6.6 Phase 3 — Contract

After confirming that all Deploy B pods are running and no old pods remain, the legacy column can be removed:

```text
event_date
```

This is the contract phase.

At this point:

- The application uses only `scheduled_at`.
- The old schema is no longer needed.
- Removing `event_date` is safe.

---

## 6.7 Why the Order Matters

Dropping `event_date` too early may cause older application pods to fail with database errors.

The safe order is:

1. Add the new column.
2. Deploy a dual-compatible application.
3. Backfill old records.
4. Deploy the new-column-only application.
5. Remove the old column.

This sequence maintains compatibility throughout the rollout.

---

# 7. Evidence Files

The following evidence files were collected:

```text
evidence/lab12/initial-status.txt
evidence/lab12/pod-distribution.txt
evidence/lab12/events-failover.txt
evidence/lab12/gateway-failover.txt
evidence/lab12/pdb-blocked-eviction.txt
evidence/lab12/final-availability-status.txt
evidence/lab12/mixedload-after-failover.txt
evidence/lab12/graceful-restart-5xx-before.json
evidence/lab12/graceful-restart-5xx-after.json
evidence/lab12/migration-5xx-before.json
evidence/lab12/migration-5xx-after.json
evidence/lab12/events-schema-before-index.txt
evidence/lab12/events-schema-after-index.txt
evidence/lab12/alembic-concurrent-index.txt
```

These files document the initial state, failover tests, disruption protection, restart metrics, migration output, and final database schema.

---

# 8. Final Results

| Area | Final Result |
|---|---|
| Gateway replicas | 5 |
| Events replicas | 2 |
| Payments replicas | 2 |
| Notifications replicas | 2 |
| PodDisruptionBudgets | Configured and enforced |
| Gateway failover | Successful |
| Events failover | Successful |
| PDB blocked unsafe eviction | Yes |
| Rolling restart completed | Yes |
| Gateway 5xx during restart | 0 |
| Concurrent migration completed | Yes |
| Alembic final revision | `a06ff4ace509` |
| New database index | `idx_events_event_date` |
| Gateway 5xx during migration | 0 |

---

# 9. Conclusion

Lab 12 was completed successfully.

The QuickTicket system was improved with multiple service replicas, PodDisruptionBudgets, topology spreading, fast readiness checks, and graceful shutdown behavior.

The failover tests showed that Kubernetes automatically replaced deleted pods while the remaining replicas kept the services available.

The PodDisruptionBudget test confirmed that Kubernetes blocked an eviction that would have reduced the Events service below its required availability level.

The gateway rolling restart completed successfully and Prometheus showed zero HTTP 5xx errors before and after the restart.

The PostgreSQL migration also completed successfully. Alembic reached revision `a06ff4ace509`, the `idx_events_event_date` index was created, and the gateway 5xx counter did not increase during the migration.

The lab demonstrated that availability and schema changes should be designed together. Replicas and disruption controls protect the application layer, while graceful shutdown and production-safe database migrations reduce failures during deployment and maintenance operations.
