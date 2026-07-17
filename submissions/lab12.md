# Lab 12

## Task 1

### Replica counts after apply

```text
NAME                            READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/events          2/2     2            2           27d
deployment.apps/mixedload       0/0     0            0           13d
deployment.apps/notifications   2/2     2            2           26m
deployment.apps/payments        2/2     2            2           27d
deployment.apps/postgres        1/1     1            1           27d
deployment.apps/redis           1/1     1            1           27d

NAME                          DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
rollout.argoproj.io/gateway   5         5         1            5           13d
```

I scaled `events`, `payments`, and `notifications` to `2` replicas. `gateway` stayed at `5` replicas in the Rollout.

### 5xx before and after the pod-kill test

Before:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784295333.637,"0"]}]}}
```

After deleting one `gateway` pod and one `events` pod under mixedload:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784295401.238,"1.0908892565589716"]}]}}
```

My second run still showed a small non-zero 5xx value in the 1-minute window, even though the replacement pods recovered quickly.

### `kubectl get pdb`

```text
NAME                                           MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
poddisruptionbudget.policy/events-pdb          1               N/A               1                     47s
poddisruptionbudget.policy/gateway-pdb         2               N/A               3                     48s
poddisruptionbudget.policy/notifications-pdb   N/A             1                 1                     47s
poddisruptionbudget.policy/payments-pdb        1               N/A               1                     47s
```

### Live topology spread field

```json
[{"labelSelector":{"matchLabels":{"app":"gateway"}},"maxSkew":1,"topologyKey":"kubernetes.io/hostname","whenUnsatisfiable":"ScheduleAnyway"}]
```

### Actual gateway pod placement

```text
NAME                       READY   STATUS    RESTARTS      AGE     IP            NODE
gateway-5758b8d586-gpzqd   1/1     Running   0             5d19h   10.42.0.215   k3d-quickticket-server-0
gateway-5758b8d586-x6b5c   1/1     Running   0             5d19h   10.42.0.216   k3d-quickticket-server-0
gateway-5758b8d586-z7qqv   1/1     Running   4 (34m ago)   13d     10.42.0.203   k3d-quickticket-server-0
gateway-5758b8d586-zhmz8   1/1     Running   4 (34m ago)   13d     10.42.0.205   k3d-quickticket-server-0
gateway-fcb44db7d-z22db    1/1     Running   0             21s     10.42.0.249   k3d-quickticket-server-0
```

All gateway pods are on the same node because this k3d cluster is single-node. The YAML is still correct for a multi-node cluster.

### PDB eviction proof

I tightened `events-pdb` to `minAvailable: 2`, so `disruptionsAllowed` became `0`. I then tried to hit the eviction subresource in two ways:

1. through `kubectl proxy` + `curl`
2. through `kubectl create --raw`

In this environment I did not get the expected `HTTP 429` JSON body back. `kubectl proxy` started on `127.0.0.1:8901`, but the local request path did not complete cleanly, and `kubectl create --raw` returned `MethodNotAllowed` for that path. After the check I restored `events-pdb` back to `minAvailable: 1`.

### Answers

With `3` gateway replicas and `minAvailable: 1`, the maximum number of pods that can be evicted at the same time is `2`, because at least one healthy pod must stay up.

I set `gateway-pdb` to `minAvailable: 2` with `5` replicas because the gateway is on the critical path and I want to keep more than one live pod during maintenance. That still allows `3` simultaneous evictions, but it does not reduce the service to a single remaining pod.

With `5` gateway pods on a `3`-node cluster and `maxSkew: 1`, the placement would be `2/2/1`. With `7` pods it would be `3/2/2`. The difference between the most-loaded and least-loaded node stays at `1`.

## Task 2

### `preStop` and `readinessProbe`

```yaml
terminationGracePeriodSeconds: 40
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        app: gateway
containers:
  - name: gateway
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

I added the `preStop` sleep, shortened the readiness probe interval, and set `terminationGracePeriodSeconds: 40`.

### Concurrent index migration

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


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date",
            table_name="events",
            if_exists=True,
            postgresql_concurrently=True,
        )
```

I prepared the migration in `migrations/versions/a12f01000001_index_events_event_date_concurrently.py`.

### Expand-and-contract sketch

1. Migration 1: add nullable column `scheduled_at TIMESTAMPTZ NULL`.
2. Code deploy A: read `COALESCE(scheduled_at, event_date)` and, if there is a write path, write to both columns.
3. Migration 2: backfill with `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL`, then make `scheduled_at` `NOT NULL`.
4. Code deploy B: read only `scheduled_at` and write only `scheduled_at`.
5. Migration 3: drop `event_date` only after deploy B is fully rolled out everywhere.

### Answers

`CREATE INDEX CONCURRENTLY` matters because on a large table the normal `CREATE INDEX` can hold a much stronger lock and block reads or writes for a long time. On a `10M`-row table that can turn into minutes of visible downtime.

Migration 3 must come after deploy B is fully rolled out because any old pod that still reads `event_date` will start failing immediately if that column disappears first.

## Bonus Task

### Migration files I added

I added these migrations:

1. `a12f02000002_add_events_scheduled_at.py`
2. `a12f03000003_backfill_events_scheduled_at.py`
3. `a12f04000004_drop_events_event_date.py`

Upgrade bodies:

```python
def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
```

```python
def upgrade() -> None:
    op.execute(
        "UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL"
    )
    op.alter_column("events", "scheduled_at", nullable=False)
```

```python
def upgrade() -> None:
    op.drop_column("events", "event_date")
```

### `app/events/main.py` and seed changes

In the final code I switched reads from `event_date` to `scheduled_at` and changed ordering to `ORDER BY e.scheduled_at`.

I also updated `app/seed.sql` so the table and inserts use `scheduled_at` instead of `event_date`.

### Deploy A vs Deploy B

Deploy A should read `COALESCE(scheduled_at, event_date)` and keep the old response shape stable during the overlap window.

Deploy B should read only `scheduled_at`. In my final code I am already at the Deploy B state.

### Answers

If I reordered one step too early, the most dangerous one would be migration 3. Dropping `event_date` before Deploy B is fully rolled out would break every old pod that still references the old column.

For a `10M`-row backfill I would not run one huge `UPDATE`. I would batch it like this:

```text
while rows_left:
  begin transaction
  update events
  set scheduled_at = event_date
  where id > last_id
    and scheduled_at is null
  order by id
  limit 10000
  commit
  sleep short_interval
  last_id = max_id_from_batch
```

The downgrade from migration 3 is not enough for true rollback safety by itself once Deploy B is live. For rollback to be safe, I would need the old application version to still be deployable, compatible with the re-added schema, and fully rolled back across all pods before traffic reaches mixed versions again.
