*Emil Nabiullin, e.nabiullin@innopolis.university*
---
# Lab 12 - Bonus: Advanced Kubernetes Resilience

## Repository state

This branch contains:

- scaled `events`, `payments`, and `notifications` manifests
- updated `gateway` Rollout with `preStop`, tighter `readinessProbe`, and `topologySpreadConstraints`
- new `k8s/pdb.yaml`
- one concurrent-index migration and three bonus rename migrations
- final bonus updates in `app/events/main.py` and `app/seed.sql`
- `submissions/lab12.md`

Runtime-only verification notes:

- mixedload used a temporary copy that reserves `event 3`, and `event 3` was raised to `100000` tickets
- Alembic used a temporary config on `localhost:5433` via `kubectl port-forward svc/postgres 5433:5432`
- the live DB had an older schema-equivalent Alembic revision id, so `alembic_version` was aligned to this repo's chain before Lab 12 migrations
- bonus deploy verification used local `events` images and live-only rollout hardening; those runtime tweaks are not part of the repo diff

## Setup

Task 1 was verified from the committed manifests plus a temporary local gateway manifest. Task 2 and the bonus task used the temporary Alembic config on port `5433`.

The mixed load was kept running throughout the lab:

```bash
kubectl apply -f /tmp/emil-lab12-mixedload.yaml
kubectl rollout status deployment/mixedload --timeout=30s
```

The `events` table was prepared for long-running traffic:

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -c \
  "UPDATE events SET total_tickets = 100000 WHERE id = 3;"
```

```text
UPDATE 1
```

## Task 1 - Multi-Replica Failover + PDBs

### Replica counts and failover

`kubectl get deploy` after scaling the three Deployments:

```text
NAME            READY   UP-TO-DATE   AVAILABLE   AGE
events          2/2     2            2           5d4h
payments        2/2     2            2           5d4h
notifications   2/2     2            2           142m
```

`kubectl get rollout gateway`:

```text
NAME      DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
gateway   5         5         5            5           5d2h
```

Prometheus before / after deleting one `gateway` pod and one `events` pod under load:

```text
before: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784154930.525,"0"]}]}}
after:  {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784154942.102,"0"]}]}}
```

### PDBs and topology spread

`kubectl get pdb`:

```text
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb          1               N/A               1                     12m
gateway-pdb         2               N/A               3                     12m
notifications-pdb   N/A             1                 1                     12m
payments-pdb        1               N/A               1                     12m
```

Live topology-spread field from the gateway Rollout:

```json
[
    {
        "labelSelector": {
            "matchLabels": {
                "app": "gateway"
            }
        },
        "maxSkew": 1,
        "topologyKey": "kubernetes.io/hostname",
        "whenUnsatisfiable": "ScheduleAnyway"
    }
]
```

Actual gateway placement on single-node k3d:

```text
NAME                       READY   STATUS    RESTARTS   AGE     IP            NODE
gateway-6d75676895-74nzw   1/1     Running   0          6m46s   10.42.0.130   k3d-quickticket-server-0
gateway-6d75676895-fpl5x   1/1     Running   0          6m6s    10.42.0.134   k3d-quickticket-server-0
gateway-6d75676895-r465m   1/1     Running   0          12s     10.42.0.139   k3d-quickticket-server-0
gateway-6d75676895-vvklw   1/1     Running   0          5m56s   10.42.0.135   k3d-quickticket-server-0
gateway-6d75676895-wlsbl   1/1     Running   0          6m16s   10.42.0.133   k3d-quickticket-server-0
```

### Real eviction-API rejection

After temporarily tightening `events-pdb` to `minAvailable: 2`, `kubectl get pdb events-pdb` showed:

```text
NAME         MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb   2               N/A               0                     12m
```

Single eviction request through the API:

```json
{
    "kind": "Status",
    "apiVersion": "v1",
    "metadata": {},
    "status": "Failure",
    "message": "Cannot evict pod as it would violate the pod's disruption budget.",
    "reason": "TooManyRequests",
    "details": {
        "causes": [
            {
                "reason": "DisruptionBudget",
                "message": "The disruption budget events-pdb needs 2 healthy pods and has 2 currently"
            }
        ]
    },
    "code": 429
}
```

### Answers

With `3` gateway replicas and `minAvailable: 1`, the maximum simultaneous evictions is `2`, because at least one healthy pod must remain. The committed `gateway-pdb` uses `minAvailable: 2` with `5` replicas so that the critical-path gateway keeps at least two live replicas while still allowing up to three maintenance evictions.

For `maxSkew: 1` in a `3`-node cluster, `5` gateway pods would be placed `2/2/1`, and `7` gateway pods would be placed `3/2/2`.

## Task 2 - Graceful Shutdown + Zero-Downtime Migration

### `preStop` and `readinessProbe`

The relevant block from `k8s/gateway.yaml`:

```yaml
spec:
  template:
    spec:
      terminationGracePeriodSeconds: 40
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

Rolling restart of the Argo Rollout under load:

```text
before: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784154977.202,"0"]}]}}
after:  {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784155066.402,"0"]}]}}
```

### Concurrent index migration

Migration code:

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

Migration run under live mixedload:

```text
INFO  [alembic.runtime.migration] Running upgrade 95710c8a8e44 -> 486a4f25cc47, index events.event_date concurrently
.venv/bin/alembic -c /tmp/emil-lab12-alembic.ini upgrade 486a4f25cc47  0.53s user 0.09s system 90% cpu 0.680 total
```

Prometheus before / after the migration:

```text
before: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784155220.405,"0"]}]}}
after:  {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784155227.404,"0"]}]}}
```

`\d events` after the migration:

```text
                                        Table "public.events"
    Column     |           Type           | Collation | Nullable |              Default
---------------+--------------------------+-----------+----------+------------------------------------
 id            | integer                  |           | not null | nextval('events_id_seq'::regclass)
 name          | text                     |           | not null |
 venue         | text                     |           | not null |
 event_date    | timestamp with time zone |           | not null |
 total_tickets | integer                  |           | not null |
 price_cents   | integer                  |           | not null |
 email         | character varying(255)   |           |          |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
Referenced by:
    TABLE "orders" CONSTRAINT "orders_event_id_fkey" FOREIGN KEY (event_id) REFERENCES events(id)
```

### Expand-and-contract sketch

1. Migration 1: `ALTER TABLE events ADD COLUMN scheduled_at TIMESTAMPTZ NULL;`
2. Code deploy A: read `COALESCE(scheduled_at, event_date)` and write both columns. In QuickTicket the runtime dual-write is a no-op because the service does not expose a live event-creation path; the only insertion path is `seed.sql`.
3. Migration 2: `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL;` and then `ALTER TABLE events ALTER COLUMN scheduled_at SET NOT NULL;`
4. Code deploy B: read only `scheduled_at` and write only `scheduled_at`
5. Migration 3: `ALTER TABLE events DROP COLUMN event_date;`

### Answers

`CREATE INDEX CONCURRENTLY` matters because on a large table the normal `CREATE INDEX` takes an `ACCESS EXCLUSIVE` lock and blocks queries while the index is built. On a `10M`-row table that can mean minutes of read and write downtime. The concurrent form keeps the service online and is the correct production-safe syntax.

Migration 3 must come strictly after deploy B is fully rolled out because any still-running code that selects or writes `event_date` would immediately fail once that column disappears. Dropping the old column early turns a compatible intermediate state into a hard runtime break.

## Bonus Task - Executed Expand-and-Contract Rename

### Migration bodies

`766eda1b610e_add_events_scheduled_at_column.py` `upgrade()`:

```python
op.add_column(
    "events",
    sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
)
```

`e915100914fb_backfill_events_scheduled_at.py` `upgrade()`:

```python
op.execute(
    "UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL"
)
op.alter_column(
    "events",
    "scheduled_at",
    existing_type=sa.TIMESTAMP(timezone=True),
    nullable=False,
)
```

`0dbb277f0fde_drop_events_event_date.py` `upgrade()`:

```python
op.drop_column("events", "event_date")
```

### Deploy A -> Deploy B diff

The read-path change between the two live `events` deploys was:

```diff
- SELECT e.id, e.name, e.venue,
-        COALESCE(e.scheduled_at, e.event_date) AS event_date,
+ SELECT e.id, e.name, e.venue,
+        e.scheduled_at AS event_date,
         e.total_tickets, e.price_cents,
         COALESCE(SUM(o.quantity), 0) as confirmed
  FROM events e LEFT JOIN orders o ON e.id = o.event_id
- GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
+ GROUP BY e.id ORDER BY e.scheduled_at
```

QuickTicket does not expose a runtime event-create endpoint, so Deploy A only changed reads. The only event insertion path is `app/seed.sql`, and it was updated for the final schema before Deploy B.

### Schema before M1 and after M3

`\d events` before migration 1:

```text
                                        Table "public.events"
    Column     |           Type           | Collation | Nullable |              Default
---------------+--------------------------+-----------+----------+------------------------------------
 id            | integer                  |           | not null | nextval('events_id_seq'::regclass)
 name          | text                     |           | not null |
 venue         | text                     |           | not null |
 event_date    | timestamp with time zone |           | not null |
 total_tickets | integer                  |           | not null |
 price_cents   | integer                  |           | not null |
 email         | character varying(255)   |           |          |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
Referenced by:
    TABLE "orders" CONSTRAINT "orders_event_id_fkey" FOREIGN KEY (event_id) REFERENCES events(id)
```

### Live transition sequence

The bonus task was executed on the live cluster under continuous mixedload traffic:

```text
baseline:          {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784155307.646,"0"]}]}}
after M1:          {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784155313.494,"0"]}]}}

before Deploy A:   {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784155917.145,"0"]}]}}
after Deploy A:    {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784155959.204,"0"]}]}}

before M2:         {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784155989.358,"0"]}]}}
after M2:          {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784155995.383,"0"]}]}}

before Deploy B:   {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784156082.156,"0"]}]}}
after Deploy B:    {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784156123.115,"0"]}]}}

before M3:         {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784156147.397,"0"]}]}}
after M3:          {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784156153.413,"0"]}]}}
```

The three migration executions were:

```text
INFO  [alembic.runtime.migration] Running upgrade 486a4f25cc47 -> 766eda1b610e, add events.scheduled_at column
INFO  [alembic.runtime.migration] Running upgrade 766eda1b610e -> e915100914fb, backfill events.scheduled_at
INFO  [alembic.runtime.migration] Running upgrade e915100914fb -> 0dbb277f0fde, drop events.event_date
```

The two live code deploys used local images:

```text
Deploy A image: quickticket-events:lab12a2
Deploy B image: quickticket-events:lab12b
```

### Final schema

Final `\d events` after `M3`:

```text
                                        Table "public.events"
    Column     |           Type           | Collation | Nullable |              Default
---------------+--------------------------+-----------+----------+------------------------------------
 id            | integer                  |           | not null | nextval('events_id_seq'::regclass)
 name          | text                     |           | not null |
 venue         | text                     |           | not null |
 total_tickets | integer                  |           | not null |
 price_cents   | integer                  |           | not null |
 email         | character varying(255)   |           |          |
 scheduled_at  | timestamp with time zone |           | not null |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
Referenced by:
    TABLE "orders" CONSTRAINT "orders_event_id_fkey" FOREIGN KEY (event_id) REFERENCES events(id)
```

Normalized baseline/final 5xx comparison:

```text
baseline=0
final=0
diff /tmp/5xx.baseline /tmp/5xx.final -> identical after normalization to the numeric value only
```

### Answers

The one step that would have caused 5xx if moved earlier is `M3` (`DROP COLUMN event_date`). Before Deploy B is fully rolled out, any remaining Deploy A pod still references `event_date` in `COALESCE(...)`, so `/events` would break immediately.

Batching pattern for a 10M-row backfill:

```text
last_id = 0
loop:
  rows = SELECT id FROM events WHERE id > last_id AND scheduled_at IS NULL ORDER BY id LIMIT 10000
  if rows is empty: break
  hi = rows[-1].id
  BEGIN
    UPDATE events
    SET scheduled_at = event_date
    WHERE id > last_id AND id <= hi AND scheduled_at IS NULL
  COMMIT
  last_id = hi
  sleep(0.1)
```

The downgrade from migration 3 is not enough for true rollback safety once Deploy B is live because schema shape alone is not enough. Rollback is safe only if the application fleet is also rolled back in a compatible sequence and no pods are still running code that expects the other column contract.

This confirms the final bonus state: `scheduled_at` is `NOT NULL`, `event_date` is gone, and the migration head is `0dbb277f0fde`.
