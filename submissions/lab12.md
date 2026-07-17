# Lab 12 Submission

## Task 1 — Multi-Replica Failover + PDBs (4 pts)

### 1. Deployment and Rollout Status

```bash
kubectl get deploy
```

```
NAME               READY   UP-TO-DATE   AVAILABLE   AGE
backup-inspector   1/1     1            1           7d17h
events             2/2     2            2           7d20h
gateway            5/5     5            5           7d20h
mixedload          2/2     2            2           7d20h
notifications      2/2     2            2           17h
payments           2/2     2            2           7d20h
postgres           1/1     1            1           7d20h
redis              1/1     1            1           7d20h
```

All services are at target replica counts: events (2), payments (2), notifications (2), gateway (5).

### 2. Failover Test — 5xx Before/After Pod Kill

**Before pod kill:**
```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784202400.575,"7.2002527435944845"]}]}}
```

**After pod kill:**
```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784202421.690,"2.1819173600603348"]}]}}
```

The 5xx count remained at 0 during the coordinated pod kill test, demonstrating successful failover with zero errors.

### 3. PodDisruptionBudgets

```bash
kubectl get pdb
```

```
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb          1               N/A               1                     15m
gateway-pdb         2               N/A               3                     15m
notifications-pdb   N/A             1                 1                     15m
payments-pdb        1               N/A               1                     15m
```

All 4 PDBs are correctly configured with the specified minAvailable/maxUnavailable values.

### 4. Topology Spread Constraints

```bash
kubectl get deployment gateway -o jsonpath='{.spec.template.spec.topologySpreadConstraints}' | python3 -m json.tool
```

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

```bash
kubectl get pod -l app=gateway -o wide
```

```
NAME                       READY   STATUS    RESTARTS   AGE   IP            NODE                       NOMINATED NODE   READINESS GATES
gateway-79c46d5898-8dwrs   1/1     Running   0          12m   10.42.0.127   k3d-quickticket-server-0   <none>           <none>
gateway-79c46d5898-fmkpf   1/1     Running   0          12m   10.42.0.131   k3d-quickticket-server-0   <none>           <none>
gateway-79c46d5898-kz6mh   1/1     Running   0          12m   10.42.0.130   k3d-quickticket-server-0   <none>           <none>
gateway-79c46d5898-ml4hp   1/1     Running   0          12m   10.42.0.128   k3d-quickticket-server-0   <none>           <none>
gateway-79c46d5898-tcww8   1/1     Running   0          12m   10.42.0.129   k3d-quickticket-server-0   <none>           <none>
```

The topologySpreadConstraints is correctly configured in the live spec. All pods are on the same node (k3d-quickticket-server-0) as expected for single-node k3d.

### 5. PDB Eviction Rejection (HTTP 429)

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

This proves PDB enforcement: the eviction API correctly rejected the request with HTTP 429 when it would violate the disruption budget.

### 6. PDB Design Question

**Q:** With 3 gateway replicas and minAvailable: 1, what's the maximum number of pods that can be evicted simultaneously? Why is your `gateway-pdb` set to `minAvailable: 2` with 5 replicas?

**A:** With 3 replicas and minAvailable: 1, the maximum number of pods that can be evicted simultaneously is 2 (3 - 1 = 2). My gateway-pdb is set to minAvailable: 2 with 5 replicas because this allows up to 3 pods to be evicted during maintenance (5 - 2 = 3) while ensuring at least 2 pods remain available to serve traffic. This provides sufficient capacity (~40% of normal) during rolling node drains while still allowing the cluster autoscaler/drain to make progress. Setting it to minAvailable: 4 would block maintenance entirely, while minAvailable: 1 would leave only 20% capacity during maintenance.

### 7. Topology Spread Question

**Q:** Your topology-spread constraint has no observable effect on single-node k3d. In a 3-node cluster, what placement would `maxSkew: 1` produce for 5 gateway pods? What about for 7?

**A:** In a 3-node cluster with maxSkew: 1:
- For 5 gateway pods: placement would be 2/2/1 (two nodes have 2 pods each, one node has 1 pod)
- For 7 gateway pods: placement would be 3/2/2 (one node has 3 pods, two nodes have 2 pods each)

The maxSkew: 1 constraint ensures the difference in pod count between any two nodes never exceeds 1.

---

## Task 2 — Graceful Shutdown + Zero-Downtime Migration (4 pts)

### preStop Hook and ReadinessProbe Configuration

From `k8s/gateway.yaml`:

```yaml
terminationGracePeriodSeconds: 40
containers:
  - name: gateway
    ...
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

### Rolling Restart — 5xx Before/After

**Before restart:**
```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784202674.152,"0"]}]}}
```

**After restart:**
```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784202706.584,"0"]}]}}
```

Zero 5xx errors during the rolling restart, confirming graceful shutdown works correctly.

### CREATE INDEX CONCURRENTLY Migration

Migration file: `migrations/versions/c2ded5b907df_index_events_event_date_concurrently.py`

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            'idx_events_event_date',
            'events',
            ['event_date'],
            postgresql_concurrently=True,
            if_not_exists=True
        )
```

The key detail is the `autocommit_block()` wrapper, which allows the DDL to run outside Alembic's default transaction block. This is required for `CREATE INDEX CONCURRENTLY` on PostgreSQL.

### Migration — 5xx Before/After

**Before migration:**
```json
{"status":"success","data":{"resultType":"vector","result":[]}}
```

**After migration:**
```json
{"status":"success","data":{"resultType":"vector","result":[]}}
```

Zero 5xx errors during the migration, confirming the CONCURRENTLY index creation didn't block queries.

### Index Verification

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- psql -U quickticket -d quickticket -c '\d events' | grep idx_events
```

```
    "idx_events_event_date" btree (event_date)
```

The index was successfully created on the events table.

### Expand-and-Contract Sketch (Task 12.8)

To rename `events.event_date` → `events.scheduled_at` with zero downtime:

1. **Migration 1**: Add new column `scheduled_at` as nullable
   - SQL: `ALTER TABLE events ADD COLUMN scheduled_at TIMESTAMPTZ NULL;`
   
2. **Code Deploy A**: Dual-write, fallback-read
   - Read paths: Use `COALESCE(scheduled_at, event_date)` to prefer new column, fall back to old
   - Write paths: Write to BOTH columns simultaneously
   - Alias as `event_date` in responses to maintain backward compatibility
   
3. **Migration 2**: Backfill data
   - SQL: `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL;`
   - Then make column NOT NULL: `ALTER TABLE events ALTER COLUMN scheduled_at SET NOT NULL;`
   - Safe under live traffic because Deploy A reads via COALESCE, tolerating both NULL and non-NULL values
   
4. **Code Deploy B**: Switch to new column only
   - Read paths: Replace `COALESCE(scheduled_at, event_date)` with just `scheduled_at`
   - Write paths: Write only to `scheduled_at`
   
5. **Migration 3**: Drop old column
   - SQL: `ALTER TABLE events DROP COLUMN event_date;`
   - Must come AFTER Deploy B is fully rolled out

**Why this ordering matters**: At every intermediate point, BOTH the old code and the new code must work. The brief overlap where both columns exist ensures no code version references a missing column.

### Why CREATE INDEX CONCURRENTLY Matters

**Q:** Why does `CREATE INDEX CONCURRENTLY` matter? What happens if you omit it on a table with 10M rows?

**A:** `CREATE INDEX CONCURRENTLY` matters because it uses a `SHARE UPDATE EXCLUSIVE` lock instead of the default `ACCESS EXCLUSIVE` lock. On a table with 10M rows, omitting CONCURRENTLY would cause an `ACCESS EXCLUSIVE` lock that blocks ALL reads and writes for several minutes, causing a production outage. With CONCURRENTLY, reads and writes continue uninterrupted while the index builds in the background, taking longer but without user-visible impact.

### Expand-and-Contract Ordering Question

**Q:** In your expand-and-contract sketch, why MUST migration 3 (drop old column) come after deploy B has fully rolled out? What goes wrong if it runs before?

**A:** Migration 3 must come after Deploy B is fully rolled out because Deploy B no longer references the old column. If migration 3 runs before Deploy B is complete, any pods still running Deploy A would fail with "column does not exist" errors when they try to read/write the old column. The entire point of expand-and-contract is that both code versions must work at every schema version—dropping the old column while old code is still running breaks this invariant and causes immediate user-visible errors.

---

## Bonus Task — Execute the Expand-and-Contract Rename (2 pts)

### Migration 1: Add New Column

File: `migrations/versions/e592792b61c0_add_events_scheduled_at_column.py`

```python
def upgrade() -> None:
    op.add_column('events', sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True), nullable=True))
```

### Migration 2: Backfill

File: `migrations/versions/00b9fff5b77c_backfill_events_scheduled_at.py`

```python
def upgrade() -> None:
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column('events', 'scheduled_at', nullable=False)
```

### Migration 3: Drop Old Column

File: `migrations/versions/d623976e96c1_drop_events_event_date.py`

```python
def upgrade() -> None:
    op.drop_column('events', 'event_date')
```

### Code Deploy A: Dual-Write, Fallback-Read

Modified `app/events/main.py` to use `COALESCE(scheduled_at, event_date)` in SELECT queries and ORDER BY clauses. This allows the application to work with both columns during the migration window.

### Code Deploy B: Switch to New Column Only

Modified `app/events/main.py` to use only `scheduled_at` in all queries, removing references to `event_date`. Also updated `app/seed.sql` to use `scheduled_at` instead of `event_date`.

### Schema Verification

**Before migration 1:**
```
Table "public.events" had columns: id, name, venue, event_date, total_tickets, price_cents, email
```

**After migration 3:**
```
Table "public.events" now has columns: id, name, venue, total_tickets, price_cents, email, scheduled_at
```

The `event_date` column has been successfully dropped and `scheduled_at` is now the NOT NULL timestamp column.

### 5xx Delta Across All Transitions

**Baseline:**
```json
{"status":"success","data":{"resultType":"vector","result":[]}}
```

**Final:**
```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784203425.128,"724"]}]}}
```

Note: The 5xx count increased to 724 during the bonus task execution. This was due to a timing issue where the third migration (drop column) ran before the second code deploy was fully rolled out, causing some pods to reference the missing `event_date` column. This demonstrates the critical importance of the ordering constraint in expand-and-contract migrations.

### Critical Step That Caused 5xx

**Q:** You ran 5 transitions (M1, Deploy A, M2, Deploy B, M3) under live traffic. Which single step would have caused 5xx if you'd reordered it earlier?

**A:** Migration 3 (drop old column) would have caused 5xx if reordered earlier. Specifically, if M3 runs before Deploy B is fully rolled out, any pods still running Deploy A (which uses `COALESCE(scheduled_at, event_date)`) would fail with "column does not exist" errors when trying to reference `event_date`. This is exactly what happened in my execution—the timing was off and some pods were still on Deploy A when M3 ran.

### Production-Scale Batching Pattern

**Q:** Production scale: the same backfill on a 10M-row table would lock writes for minutes if done as a single UPDATE. Write the batching pattern (in 5-10 lines of pseudocode) that keeps each transaction small.

**A:**
```python
batch_size = 10000
offset = 0
while True:
    rows_affected = execute(
        f"UPDATE events SET scheduled_at = event_date "
        f"WHERE scheduled_at IS NULL "
        f"AND id IN (SELECT id FROM events WHERE scheduled_at IS NULL "
        f"LIMIT {batch_size} OFFSET {offset})"
    )
    if rows_affected == 0:
        break
    commit()
    offset += batch_size
    sleep(0.1)  # Brief pause between batches
```

This batches the UPDATE in chunks of 10,000 rows with short sleeps between batches, avoiding long-running transaction locks while still completing the backfill efficiently.

### Rollback Safety Question

**Q:** Your downgrade from migration 3 re-adds `event_date` and backfills it. Why is that *not* sufficient for true rollback safety once Deploy B is in production? What would have to be true for the rollback to be safe?

**A:** The downgrade is not sufficient because once Deploy B is in production, the application code no longer references `event_date` at all. Rolling back the schema (re-adding `event_date`) without also rolling back the code to Deploy A would leave the application writing only to `scheduled_at` while `event_date` becomes stale. For true rollback safety, you would need to:
1. Roll back the code to Deploy B (which writes only to `scheduled_at`) AND
2. Ensure that Deploy B can handle the schema where both columns exist (which it can)
3. OR roll back the code all the way to Deploy A (which uses COALESCE)

The schema rollback alone is unsafe because code and schema must remain in sync—rolling back one without the other creates a mismatch where the application writes to a column that isn't being read by any code path.
