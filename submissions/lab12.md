# Lab 12 - Availability Engineering and Zero-Downtime Migration

## Task 1 - Multi-replica failover, PDBs, and topology spread

### Replica scaling

The dependent services were scaled to two replicas while the gateway remained at five replicas.

```text
events          2/2
payments        2/2
notifications   2/2
gateway         5/5
```

### Coordinated pod failure test

Mixed load was active while one gateway pod and one events pod were deleted. The replacement pods became Ready in about 8 seconds.

```text
Before failure: gateway 5xx over 3 minutes = 0
After failure:  gateway 5xx over 1 minute  = 0
```

### PodDisruptionBudgets

`k8s/pdb.yaml` defines:

```text
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
events-pdb          1               N/A               1
gateway-pdb         2               N/A               3
notifications-pdb   N/A             1                 1
payments-pdb        1               N/A               1
```

For the eviction test, `events-pdb` was temporarily changed to `minAvailable: 2`. With two healthy Events pods, `ALLOWED DISRUPTIONS` became 0. A Kubernetes eviction API request returned:

```text
HTTP_STATUS=429
reason: TooManyRequests
cause: DisruptionBudget
message: Cannot evict pod as it would violate the pod's disruption budget.
```

The PDB was then restored to `minAvailable: 1`.

### Gateway topology spread

The live gateway Rollout contains:

```yaml
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        app: gateway
```

The local k3d cluster has one node, so all gateway pods are placed on the same node. The constraint is still present in the live Rollout and would influence placement on a multi-node cluster.

### Design questions

With three gateway replicas and `minAvailable: 1`, up to two replicas may be voluntarily disrupted because at least one must remain available.

With the actual five gateway replicas and `minAvailable: 2`, up to three may be disrupted while two remain available. This preserves serving capacity while still allowing maintenance and node drains to progress.

With three nodes and `maxSkew: 1`, five replicas would ideally distribute as `2/2/1`; seven replicas as `3/2/2`.

---

## Task 2 - Graceful shutdown and concurrent index migration

### Gateway graceful shutdown

The gateway Rollout was updated with:

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

The live Rollout confirmed these values. A gateway restart was then performed under mixed load.

```text
Before restart 5xx: []
Rollout status: Healthy
After restart 5xx: []
```

No 5xx series was present before or after the restart.

### Concurrent index migration

Migration: `398e147b49ca_index_events_event_date_concurrently.py`

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
```

Execution:

```text
Running upgrade f75b6999664a -> 398e147b49ca
real 0m0.253s
```

Schema verification:

```text
"idx_events_event_date" btree (event_date)
```

`diff /tmp/5xx.before /tmp/5xx.after` produced no output, so the migration added no observed 5xx responses.

### Why CREATE INDEX CONCURRENTLY matters

`CREATE INDEX CONCURRENTLY` allows PostgreSQL to build the index without
blocking normal reads and writes for the duration of the index build.

Without `CONCURRENTLY`, creating an index on a large production table can take
a lock that blocks writes while the index is being built. On a table with
around 10 million rows, index construction could take minutes, so application
requests that need the table could queue, time out, or fail.

Using `CONCURRENTLY` takes longer internally and requires running outside
Alembic's normal transaction block, but it keeps the table available to live
traffic. That is why the migration uses `autocommit_block()` together with
`postgresql_concurrently=True`.

### Expand-and-contract design

The safe sequence is:

```text
M1: Add nullable scheduled_at.
Deploy A: Read COALESCE(scheduled_at, event_date).
M2: Backfill scheduled_at, then set it NOT NULL.
Deploy B: Read scheduled_at only.
M3: Drop event_date only after Deploy B is fully rolled out.
```

This ordering keeps every deployed application version compatible with the schema it may encounter.

---

## Bonus - Live expand-and-contract execution

All five transitions were executed while mixedload was running:

```text
1. M1       a20077681b92 - add nullable scheduled_at
2. Deploy A               - compatibility reads
3. M2       fc9910bf4fbb - backfill + NOT NULL
4. Deploy B               - scheduled_at only
5. M3       151391dc59cf - drop event_date
```

### Migration 1

```python
def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "scheduled_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
```

The column starts nullable because existing rows do not initially contain a `scheduled_at` value.

### Deploy A

Deploy A reads both schema states safely:

```sql
COALESCE(e.scheduled_at, e.event_date) AS event_date
```

and orders by:

```sql
ORDER BY COALESCE(e.scheduled_at, e.event_date)
```

QuickTicket has no runtime Events endpoint that writes an event date, so application dual-writing was not applicable here.

### Migration 2

```python
def upgrade() -> None:
    op.execute(
        "UPDATE events "
        "SET scheduled_at = event_date "
        "WHERE scheduled_at IS NULL"
    )
    op.alter_column(
        "events",
        "scheduled_at",
        nullable=False,
    )
```

Verification showed all five rows had `scheduled_at == event_date`, zero rows had `scheduled_at IS NULL`, and the column became `NOT NULL`.

### Deploy B

Deploy B removed the fallback:

```diff
- COALESCE(e.scheduled_at, e.event_date) AS event_date
+ e.scheduled_at AS event_date

- ORDER BY COALESCE(e.scheduled_at, e.event_date)
+ ORDER BY e.scheduled_at
```

The alias `AS event_date` is retained only to preserve the existing external API response shape. The database read itself uses only `scheduled_at`.

`app/seed.sql` was also changed to use `scheduled_at`, so a fresh database boots against the final schema.

### Migration 3

```python
def upgrade() -> None:
    op.drop_column("events", "event_date")


def downgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "event_date",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE events "
        "SET event_date = scheduled_at"
    )
    op.alter_column(
        "events",
        "event_date",
        nullable=False,
    )
```

### Final schema

The final `events` table contains:

```text
id            | integer                  | not null
name          | text                     | not null
venue         | text                     | not null
total_tickets | integer                  | not null
price_cents   | integer                  | not null
email         | character varying(255)   | nullable
scheduled_at  | timestamp with time zone | not null
```

`event_date` is gone. `/events` continued to return all five events after the contract migration.

### Zero-5xx evidence

A preliminary Events rollout revealed a connection-draining problem, so the Events Deployment was hardened before starting the controlled five-transition run:

```yaml
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxUnavailable: 0
    maxSurge: 1

terminationGracePeriodSeconds: 20

lifecycle:
  preStop:
    exec:
      command: ["sh", "-c", "sleep 10"]
```

The controlled run then produced:

```text
Baseline: 5
Final:    5
Diff:     no output
```

Therefore the five-transition sequence added exactly `0` new 5xx responses.

### Bonus design question 1 - ordering

The dangerous reordered step is Migration 3. If `event_date` were dropped before Deploy B had fully replaced Deploy A, any Deploy A pod would still execute `COALESCE(scheduled_at, event_date)`. That query would reference a missing column and cause `/events` failures.

### Bonus design question 2 - production backfill

For a 10-million-row table, the backfill should be processed in small transactions instead of one giant `UPDATE`:

```text
last_id = 0
loop:
    begin transaction
    update next 10,000 rows
        where id > last_id
          and scheduled_at is null
    record highest processed id
    commit
    if no rows updated: break
    sleep briefly
```

This keeps transactions and locks short and reduces disruption to normal traffic.

### Bonus design question 3 - rollback safety

Migration 3's downgrade is not sufficient by itself for a true production rollback once Deploy B is live. Re-adding and backfilling `event_date` once does not guarantee it stays synchronized while Deploy B continues to write only `scheduled_at`.

For a safe rollback, the schema and application must remain mutually compatible during the rollback window. A compatibility version should reintroduce the old column, backfill it, and dual-write both fields before the old application version is restored. Alternatively, writes would need to be quiesced and the rollback coordinated so no unsynchronized writes occur.
