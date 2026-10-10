# Lab 12 — Advanced Kubernetes Resilience

In this lab I prepared QuickTicket for two kinds of changes that routinely cause production incidents: Kubernetes maintenance and live database migrations. I completed both main tasks, added the optional HPA, and implemented the bonus expand-and-contract rename.

## Task 1 — Multi-Replica Failover and PDBs

I scaled events, payments and notifications to two replicas. The gateway remains a five-replica Argo Rollout. The manifests now describe the complete target state instead of relying on imperative scaling commands.

```text
NAME            READY
events          2/2
payments        2/2
notifications   2/2
```

I added four disruption budgets in `k8s/pdb.yaml`. The critical services always retain useful capacity: gateway keeps at least two pods, events and payments keep one each, while the best-effort notification service permits one unavailable replica.

```text
events-pdb          minAvailable=1   allowedDisruptions=1
gateway-pdb         minAvailable=2
payments-pdb        minAvailable=1   allowedDisruptions=1
notifications-pdb   maxUnavailable=1
```

To prove this was more than valid YAML, I temporarily tightened `events-pdb` to `minAvailable: 2` and sent a real request to the eviction API. Kubernetes rejected it with HTTP 429:

```json
{
  "kind": "Status",
  "apiVersion": "v1",
  "status": "Failure",
  "message": "Cannot evict pod as it would violate the pod's disruption budget.",
  "reason": "TooManyRequests",
  "details": {
    "causes": [{
      "reason": "DisruptionBudget",
      "message": "The disruption budget events-pdb needs 2 healthy pods and has 2 currently"
    }]
  },
  "code": 429
}
```

I restored the budget to `minAvailable: 1` immediately afterwards.

I also deleted one gateway pod and one events pod together while mixedload was running. Prometheus used the same one-minute increase query on both sides of the experiment:

```text
before: sum(increase(gateway_requests_total{status=~"5.."}[1m])) = 0
after:  sum(increase(gateway_requests_total{status=~"5.."}[1m])) = 0

events:  READY 2/2
gateway: DESIRED 5, CURRENT 5, UP-TO-DATE 5, AVAILABLE 5
```

The surviving Service endpoints handled traffic while Kubernetes recreated both pods, with no observed 5xx increase.

With three gateway replicas and `minAvailable: 1`, at most two pods can be voluntarily evicted at once. For the actual five-replica gateway I used `minAvailable: 2`: this still tolerates three simultaneous voluntary disruptions, but preserves enough serving capacity without making a node drain unnecessarily difficult.

The gateway pod template also contains a topology spread constraint on `kubernetes.io/hostname`, with `maxSkew: 1` and `ScheduleAnyway`. On a single-node k3d cluster there is nowhere to spread, but the same manifest on three nodes would place five pods as 2/2/1 and seven pods as 3/2/2.

The constraint in the live Rollout was:

```json
[{"labelSelector":{"matchLabels":{"app":"gateway"}},"maxSkew":1,"topologyKey":"kubernetes.io/hostname","whenUnsatisfiable":"ScheduleAnyway"}]
```

This k3d cluster currently has a server and an agent node. The five updated gateway pods were placed as three on `k3d-quickticket-server-0` and two on `k3d-quickticket-agent-0-0`, which is the expected 3/2 distribution for `maxSkew: 1`.

## Task 2 — Graceful Shutdown and Safe DDL

### Graceful gateway termination

The gateway now has a fast readiness probe and a ten-second preStop delay:

```yaml
terminationGracePeriodSeconds: 40
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

The readiness probe removes an unhealthy or terminating pod from Service endpoints quickly. The preStop delay then gives endpoint updates time to propagate before Uvicorn receives SIGTERM, while the 40-second grace period leaves enough time for both the hook and in-flight requests.

The events Deployment uses the same readiness and termination pattern. This matters for the compatible Deploy A and Deploy B rollouts in the expand-and-contract plan: a newly created events pod does not receive traffic until its database and Redis health checks pass.

I ran the required restart with the Argo Rollouts command rather than treating gateway as a Deployment:

```text
$ kubectl argo rollouts restart gateway
rollout 'gateway' restarts in 0s

$ kubectl argo rollouts status gateway --timeout=240s
Progressing - rollout is restarting
Healthy
```

Prometheus remained clean throughout the restart:

```text
before: sum(increase(gateway_requests_total{status=~"5.."}[1m])) = 0
after:  sum(increase(gateway_requests_total{status=~"5.."}[3m])) = 0
```

### Concurrent index migration

The first Alembic revision creates `idx_events_event_date` with the two details that matter in PostgreSQL: `postgresql_concurrently=True` and an Alembic `autocommit_block()`.

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

The reverse migration drops it concurrently and uses `if_exists=True`.

I recreated the index under live traffic and captured it before the later contract migration removed the legacy column:

```text
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)

before: sum(increase(gateway_requests_total{status=~"5.."}[1m])) = 0
after:  sum(increase(gateway_requests_total{status=~"5.."}[1m])) = 0
```

`CREATE INDEX CONCURRENTLY` matters because a normal index build on a large table blocks writes while PostgreSQL builds the index. On a table with ten million rows that lock can remain for minutes, causing request queues and timeouts. The concurrent form performs more work and may take longer overall, but allows normal reads and writes to continue. The autocommit block is required because PostgreSQL refuses concurrent index creation inside Alembic's default transaction.

## Bonus Task — Expand-and-Contract Rename

I implemented the rename from `events.event_date` to `events.scheduled_at` as three small migrations separated by two code deployments.

1. Migration 1 adds nullable `scheduled_at`. This is a cheap expand operation and does not invalidate old code.
2. Deploy A reads `COALESCE(scheduled_at, event_date)` and keeps the external response shape unchanged. QuickTicket has no runtime event INSERT path, so there was no application dual-write call site to change.
3. Migration 2 backfills only NULL rows and then makes `scheduled_at` NOT NULL.
4. Deploy B reads only `scheduled_at`. I also updated `app/seed.sql`, so a fresh database starts with the new schema.
5. Migration 3 drops `event_date` only after every Deploy A pod has disappeared.

The relevant Deploy A → Deploy B change is deliberately small:

```diff
- SELECT ..., COALESCE(e.scheduled_at, e.event_date) AS event_date, ...
+ SELECT ..., e.scheduled_at, ...

- GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
+ GROUP BY e.id ORDER BY e.scheduled_at
```

The final database schema is:

```text
Column          Type                       Nullable
id              integer                    not null
name            text                       not null
venue           text                       not null
total_tickets   integer                    not null
price_cents     integer                    not null
scheduled_at    timestamp with time zone   not null
```

There is no `event_date` column in the final schema, and the events service and seed file now use `scheduled_at` exclusively.

The step that must never be moved earlier is Migration 3. Deploy A still mentions `event_date` inside `COALESCE`; dropping that column while even one Deploy A pod remains would make every request routed to that pod fail with an undefined-column error. Contract is safe only after Deploy B has fully rolled out.

For a ten-million-row production table I would not backfill in one transaction. I would use bounded batches with commits between them:

```text
repeat:
    begin transaction
    select up to 10,000 ids where scheduled_at is null
    update only those ids from event_date
    commit
    if no rows were updated: stop
    sleep briefly and observe DB latency/replication lag
```

Migration 3's downgrade reconstructs `event_date`, but that alone is not true application rollback safety. Once Deploy B is live, it writes only the new column and expects it to exist. A safe rollback requires the restored old column to be fully backfilled and kept in sync, plus an application version compatible with both columns. The schema must be restored before old application pods receive traffic.

## Optional HPA

I added `k8s/gateway-hpa.yaml` targeting the Rollout directly. It keeps at least five gateway replicas, may scale to twelve, and targets 70% average CPU utilization. I also added CPU and memory requests to the gateway because HPA cannot calculate utilization without resource requests.

```text
NAME      REFERENCE         TARGETS        MINPODS   MAXPODS   REPLICAS
gateway   Rollout/gateway   cpu: 15%/70%   5         12        5
```

The live target confirms that metrics-server can calculate utilization from the CPU request declared on the gateway container.

## Final verification

The repository includes automated checks for replica counts, all four PDB contracts, graceful shutdown settings, topology spread, HPA configuration, the migration chain and the final source/seed schema. The final run completed with `9 passed`; Python compilation, Docker Compose validation, server-side Kubernetes dry-run and Alembic's single-head check also passed.

All temporary PDB changes were restored. The live Alembic revision is `1204`; the final database has `scheduled_at TIMESTAMPTZ NOT NULL`, the legacy column is gone, and events, payments and notifications are configured for two replicas.
