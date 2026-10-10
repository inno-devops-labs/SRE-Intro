# Lab 12 — Advanced Kubernetes Resilience

## Goal

Improve QuickTicket resilience during maintenance, rolling restarts, and live database schema changes using PodDisruptionBudgets, graceful shutdown, a concurrent index migration, and an expand-and-contract column migration.

## Changes

- Added `k8s/pdb.yaml` with four PodDisruptionBudgets for gateway, events, payments, and notifications.
- Scaled events, payments, and notifications to two replicas with `maxUnavailable: 0` rolling updates.
- Added gateway topology spread, `preStop: sleep 10`, a 40-second termination grace period, and faster readiness probing.
- Added an Alembic `CREATE INDEX CONCURRENTLY` migration with `autocommit_block()`.
- Executed the `event_date` to `scheduled_at` expand-and-contract migration:
  - add nullable `scheduled_at`;
  - Deploy A with fallback reads from `scheduled_at` to `event_date`;
  - backfill and make `scheduled_at` non-null;
  - Deploy B reading only `scheduled_at`;
  - drop `event_date`.
- Updated the seed schema for a fresh installation using `scheduled_at`.

## Testing

### Multi-replica and graceful rollout

The services rolled out successfully with two replicas for events, payments, and notifications. Gateway was restarted with `kubectl argo rollouts restart gateway` while mixedload was running.

Fresh Prometheus result before the gateway restart:

```text
sum(increase(gateway_requests_total{status=~"5.."}[5m])) = 0
```

After the restart the same query returned no result series, meaning that no new 5xx samples were recorded in the window. All gateway pods remained `1/1 Running`.

The gateway pod template contains:

```yaml
terminationGracePeriodSeconds: 40
lifecycle:
  preStop:
    exec:
      command: ["sh", "-c", "sleep 10"]
readinessProbe:
  periodSeconds: 2
  failureThreshold: 1
```

### PDB enforcement

For the eviction test, `events-pdb` was temporarily tightened to `minAvailable: 2` while two events pods were running. The Kubernetes eviction API rejected the eviction:

```text
Error from server (TooManyRequests): Cannot evict pod as it would violate the pod's disruption budget.
```

The PDB was restored to `minAvailable: 1` and reported one allowed disruption:

```text
NAME         MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
events-pdb   1                N/A               1
```

With three gateway replicas and `minAvailable: 1`, at most two gateway pods may be unavailable simultaneously. This repository keeps `gateway-pdb` at `minAvailable: 2` with five replicas so that at least two gateway instances remain available during voluntary disruptions.

The topology constraint uses `maxSkew: 1` on `kubernetes.io/hostname`. The local k3d cluster has one node, so all pods necessarily share that node and the constraint has no visible placement effect. In a three-node cluster, five pods would be distributed as evenly as possible, for example `2/2/1`; seven pods would be distributed `3/2/2` or another placement with a maximum difference of one.

Live cluster evidence:

```text
events             2/2  Available 2
notifications      2/2  Available 2
payments           2/2  Available 2
gateway Rollout    5/5  Available 5

events-pdb          minAvailable=1  allowed=1
gateway-pdb         minAvailable=2  allowed=3
notifications-pdb  maxUnavailable=1 allowed=1
payments-pdb        minAvailable=1  allowed=1
```

The live Rollout contains `maxSkew=1`, `topologyKey=kubernetes.io/hostname`, and `whenUnsatisfiable=ScheduleAnyway`. All five gateway pods are running on the single k3d node `k3d-quickticket-server-0`, which is expected for this one-node test cluster.

### Concurrent index migration

Migration `b7c4d8e91f20` uses PostgreSQL concurrent index creation outside Alembic's default transaction:

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            unique=False,
            if_not_exists=True,
            postgresql_concurrently=True,
        )
```

The migration reached `c1d2e3f4a5b6 (head)` and `\d events` showed `idx_events_event_date` without blocking application traffic. `CONCURRENTLY` matters because a regular index build on a large table takes a stronger lock and can block writes for minutes; concurrent creation allows normal reads and writes while the index is built, at the cost of additional I/O and a longer build.

### Expand-and-contract execution

The executed migration chain was:

1. **Migration 1, expand:** add nullable `scheduled_at` while keeping `event_date`.
2. **Deploy A:** read `COALESCE(scheduled_at, event_date)` and keep both schema versions compatible during the transition.
3. **Migration 2, backfill:** set `scheduled_at = event_date` for existing rows, then enforce `scheduled_at NOT NULL`.
4. **Deploy B:** read and order by `scheduled_at`; the events image was rolled out as `quickticket-events:v3`.
5. **Migration 3, contract:** drop `event_date` only after Deploy B completed.

Before Migration 1, `events` contained five rows and no populated `scheduled_at` values. After Migration 2, all five rows had `scheduled_at` equal to the previous `event_date`. After Migration 3, `\d events` showed `scheduled_at TIMESTAMPTZ NOT NULL` and no `event_date` column. The `/events` smoke test continued returning all five events.

Migration 3 must come after Deploy B is fully rolled out. If the old code were still running when `event_date` was dropped, those pods would issue queries against a missing column and return database errors. The same ordering rule applies to rollback: re-adding the column is not enough if production code still writes only the new column; rollback is safe only when the old column is restored, backfilled, and the old-compatible application version is deployed before the new column is removed.

At production scale, the backfill should be batched rather than one large transaction:

```text
repeat:
  select a bounded batch of primary keys where scheduled_at is null
  begin transaction
  update only that batch from event_date
  commit quickly
  sleep briefly if lock/replication pressure is high
until no rows remain
```

The original absolute 5xx counter was not a clean Lab 12 baseline: it already contained errors from earlier Lab 11 experiments and rollout probes. Therefore the historical snapshot changed from `2` to `5`, but this is not attributed to the migration. The clean Prometheus window immediately before the gateway restart reported `0` new 5xx, and the post-restart query reported no 5xx series. The diagnostic query over the recent window and recent gateway/events logs were also clean.

## Artifacts & Screenshots

- Submission file: `submissions/lab12.md`
- Kubernetes manifests: `k8s/pdb.yaml`, `k8s/gateway.yaml`, `k8s/events.yaml`, `k8s/payments.yaml`, `k8s/notifications.yaml`
- Alembic migrations: `migrations/versions/`
- Application changes: `app/events/main.py`, `app/seed.sql`
- Screenshots: none

- [x] Title is clear (`feat(labN): <topic>` style)
- [x] No secrets/large temp files committed
- [x] Submission file at `submissions/lab12.md` exists
