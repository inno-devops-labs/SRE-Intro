# Lab 12 --- Resilience, Graceful Shutdown, and Expand-and-Contract Migration

**Project:** SRE-Intro / QuickTicket\
**Branch:** `feature/lab12`\
**Date:** 18 October 2026

## 1. Objectives

This lab improves workload availability during pod disruption and
application rollouts, configures graceful termination, and demonstrates
an expand-and-contract database migration from `events.event_date` to
`events.scheduled_at`.

## 2. Task 1 --- Pod disruption and topology

### 2.1 Replicas

Scaled the following workloads to two replicas:

-   `events`: 2/2 Ready
-   `payments`: 2/2 Ready
-   `notifications`: 2/2 Ready

### 2.2 PodDisruptionBudgets

Created `k8s/pdb.yaml` with PDBs for `gateway`, `events`, `payments`,
and `notifications`.

-   `gateway-pdb`: `minAvailable: 2`
-   `events-pdb`: `minAvailable: 1`
-   `payments-pdb`: `minAvailable: 1`
-   `notifications-pdb`: `maxUnavailable: 1`

To demonstrate PDB enforcement, temporarily increased
`events-pdb.minAvailable` to `2`. Kubernetes reported zero allowed
disruptions. A pod eviction request through the Kubernetes eviction API
returned **HTTP 429 TooManyRequests**, with the message that eviction
would violate the disruption budget. The PDB was then restored to
`minAvailable: 1`.

### 2.3 Topology and disruption test

Added a `topologySpreadConstraints` rule to the gateway pod template,
using `kubernetes.io/hostname`, `maxSkew: 1`, and
`whenUnsatisfiable: ScheduleAnyway`.

Deleted one gateway pod and one events pod during the lab test.
Kubernetes recreated the pods, and the workloads returned to their
desired ready replica counts. The local k3d cluster had a single node,
so actual cross-node spreading could not be demonstrated; the scheduling
constraint is configured for clusters with multiple eligible nodes.

## 3. Task 2 --- Graceful termination and online index migration

### 3.1 Graceful termination

Configured the gateway pod template with:

-   `preStop` hook that sleeps for 10 seconds;
-   `terminationGracePeriodSeconds: 40`;
-   readiness probe for the gateway health endpoint.

Restarted the gateway Rollout while the mixed-load workload was
deployed. Argo Rollouts reported the gateway as **Healthy**, with 5
desired, current, updated, ready, and available replicas.

### 3.2 Concurrent index migration

Added an Alembic migration that creates `idx_events_event_date` using
`CREATE INDEX CONCURRENTLY` inside
`op.get_context().autocommit_block()`. The migration was applied
successfully, and PostgreSQL confirmed that the index existed.

## 4. Bonus --- Expand-and-contract migration

The schema change renames the logical event date field from `event_date`
to `scheduled_at` without removing the old column before the new
application version is deployed.

### 4.1 Expand

Migration `d3daa138c1c3` adds nullable `scheduled_at` to `events`. This
is an additive schema change, so the existing application can continue
using `event_date`.

### 4.2 Deploy A --- Compatibility read

Updated the events service to read
`COALESCE(e.scheduled_at, e.event_date)`, built `quickticket-events:v2`,
imported it into k3d, and rolled out the Deployment successfully. This
version can read rows before and after the backfill.

### 4.3 Backfill

Migration `63b7037f00b4` copies `event_date` into `scheduled_at` for
rows where `scheduled_at` is null.

Verification query:

``` sql
SELECT
  COUNT(*) AS total,
  COUNT(*) FILTER (WHERE scheduled_at IS NULL) AS missing,
  COUNT(*) FILTER (WHERE scheduled_at IS DISTINCT FROM event_date) AS mismatched
FROM events;
```

Observed result:

-   `total = 5`
-   `missing = 0`
-   `mismatched = 0`

All five existing rows were backfilled and matched the original values.

### 4.4 Deploy B --- New column only

Updated the events service to read `scheduled_at` directly, built
`quickticket-events:v3`, imported it into k3d, and updated the
Deployment. Kubernetes reported:

``` text
deployment "events" successfully rolled out
```

Updated `app/seed.sql` to use `scheduled_at` for the table definition
and seed inserts.

### 4.5 Contract

Migration `03465bf07988` drops `event_date`. Its downgrade recreates the
old column, restores values from `scheduled_at`, and then reapplies the
non-null constraint.

After the migration, PostgreSQL showed `scheduled_at` and no longer
showed `event_date`. A final data check returned:

-   `total = 5`
-   `missing = 0`

The `orders.event_id` foreign key remained present.

### 4.6 Health check evidence

After the contract migration, the Prometheus query for the increase in
gateway 5xx responses over the previous three minutes, with a zero
fallback, returned **0**. At that time, `events`, `payments`, and
`notifications` each had 2/2 ready replicas, and Argo Rollouts reported
gateway **Healthy** with 5/5 ready replicas.

**Evidence limitation:** the recorded Prometheus result establishes zero
observed 5xx increase for that final three-minute query window. It does
not, by itself, prove a continuous zero-5xx result across every one of
the five migration/deployment transitions.

## 5. Bonus questions

### 5.1 Which reordered step can cause 5xx errors?

Dropping `event_date` before Deploy B has fully rolled out can cause 5xx
responses. Old application pods may still execute SQL that references
`event_date`, which no longer exists. The contract migration must
therefore wait until all running application instances use
`scheduled_at`.

### 5.2 How should a backfill of 10 million rows be batched?

Process rows in batches of 10,000, with each batch committed as its own
transaction. Track progress using a stable key or a predicate that
selects only rows not yet migrated. Pause briefly between batches (for
example, 50 ms), monitor replication lag, lock waits, database load, and
error rate, and make the operation restartable. A single large
transaction should be avoided because it can retain locks and create
substantial WAL and replication pressure.

### 5.3 Is downgrade safe after Deploy B?

Not automatically. Deploy B expects `scheduled_at`; merely recreating
`event_date` does not make the old application safe unless the values
are restored and any writes made by the new version are represented in
the old column. A safe rollback requires a compatibility window, reverse
synchronization or a verified backfill, and a rollout of the old
application before removing the new column. The contract migration's
downgrade restores `event_date` from `scheduled_at`, but application
rollback must still be coordinated with schema rollback.

## 6. Files changed for Lab 12

-   `k8s/pdb.yaml`
-   `k8s/gateway.yaml`
-   `k8s/events.yaml`
-   `k8s/payments.yaml`
-   `k8s/notifications.yaml`
-   `app/events/main.py`
-   `app/seed.sql`
-   `migrations/versions/75493c93dee7_add_concurrent_index_on_events_event_.py`
-   `migrations/versions/d3daa138c1c3_expand_events_with_scheduled_at.py`
-   `migrations/versions/63b7037f00b4_backfill_events_scheduled_at.py`
-   `migrations/versions/03465bf07988_contract_drop_event_date.py`

## 7. Conclusion

The Kubernetes disruption controls, graceful termination settings,
concurrent index migration, and three-stage expand-and-contract schema
migration were applied. The database contains five rows with
`scheduled_at` populated, the old `event_date` column is removed, and
the final recorded health check returned zero 5xx increase over three
minutes. Before submission, review the Git diff and stage only the Lab
12 files listed above; unrelated Lab 1, Lab 6, and Lab 10 files should
not be included.
