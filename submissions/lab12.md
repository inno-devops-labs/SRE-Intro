# Lab 12 — Advanced Kubernetes Resilience

## Task 1

Task 1 evidence is recorded in `Task1-1-Replica-PDB-Topology-corrected.txt`.

Observed replica state:

```text
events          2/2
payments        2/2
notifications   2/2
gateway         5/5
```

The four PodDisruptionBudgets were present:

```text
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
events-pdb          1                N/A                1
gateway-pdb         2                N/A                3
notifications-pdb   N/A              1                  1
payments-pdb        1                N/A                1
```

The live gateway topology-spread constraint was:

```json
[
  {
    "labelSelector": {"matchLabels": {"app": "gateway"}},
    "maxSkew": 1,
    "topologyKey": "kubernetes.io/hostname",
    "whenUnsatisfiable": "ScheduleAnyway"
  }
]
```

All five gateway Pods were on the single node `k3d-quickticket-server-0`, as recorded in the same evidence file. This is expected for single-node k3d; the constraint is present in the live spec but cannot distribute Pods without additional nodes.

The corrected coordinated gateway/events pod deletion and recovery are recorded in `Task1-2-Failover-before-kill-corrected.txt` and `Task1-2-Failover-after-kill-corrected.txt`. The official global 5xx query returned zero before and after:

```text
before: 0
after:  0
```

The old failed capture is superseded by this corrected run. The replacement Pods were confirmed ready only after the deleted Pod names disappeared; recovery took 22 seconds. This preserved the zero-5xx result but exceeded the handout's approximate five-second recovery target.

The load generator was running during the test, but its `2/2` status was not included in the saved before snapshot; the evidence therefore proves the observed query result and Pod recovery, while the load-state linkage remains a traceability limitation.

The corrected after snapshot shows two Events Pods and five Gateway Pods at `1/1 Running`, and the per-path 5xx query also returned zero.

The tightened PDB showed zero allowed disruptions in `Task1-5-PDB-tightened.txt`. The eviction API returned HTTP 429, recorded in `Task1-5-PDB-Eviction-429.txt`:

```json
{
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

With three gateway replicas and `minAvailable: 1`, at most two Pods can be evicted simultaneously because one healthy Pod must remain. The five-replica `gateway-pdb` uses `minAvailable: 2` so three gateway Pods may be disrupted while two remain available to serve traffic and maintenance can still complete.

On three nodes, `maxSkew: 1` distributes five gateway Pods as `2/2/1` across the nodes. Seven Pods can be distributed as `3/2/2` (or an equivalent permutation).

## Task 2

Task 2 is completed. The live `k8s/gateway.yaml` contains `terminationGracePeriodSeconds: 40`, a 10-second `preStop` sleep, and an HTTP readiness probe on `/health`. The rolling-restart evidence in `Task2-1-Rolling-Restart-before-corrected.txt` and `Task2-1-Rolling-Restart-after-corrected.txt` recorded global 5xx values of `0` before and `0` after; the final Rollout was Healthy at step `6/6` with five ready and available replicas.

The concurrent-index migration uses `autocommit_block()` around `CREATE INDEX CONCURRENTLY` and its downgrade uses the same pattern for `DROP INDEX CONCURRENTLY`. The clean rerun in `Task2-5-Migration-5xx-rerun.txt` recorded an explicit numeric error rate of `0` before and `0` after the migration, the Alembic transition `bf3aa2d35ec3 -> c1f4e8a92b7d`, and the resulting `idx_events_event_date` index. The index definition is also recorded in `Task2-4-Index-Visible.txt`.

Live gateway proof block:

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
  initialDelaySeconds: 5
  periodSeconds: 2
  timeoutSeconds: 3
  failureThreshold: 1
```

Migration proof output:

```text
BEFORE: global 5xx rate = 0
Running upgrade bf3aa2d35ec3 -> c1f4e8a92b7d, index events.event_date concurrently
AFTER: global 5xx rate = 0
INDEX: idx_events_event_date
```

Captured index-visible schema output after the Task 2 migration:

```text
EVENTS_COLUMNS
  column_name  |        data_type         | is_nullable
---------------+--------------------------+-------------
 id            | integer                  | NO
 name          | text                     | NO
 venue         | text                     | NO
 event_date    | timestamp with time zone | NO
 total_tickets | integer                  | NO
 price_cents   | integer                  | NO
 email         | character varying       | YES

INDEX_DEFINITION
       indexname       | indexdef
-----------------------+------------------------------------------------------------------------------
 idx_events_event_date | CREATE INDEX idx_events_event_date ON public.events USING btree (event_date)
```

Migration code:

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

The expand-and-contract design is:

1. Migration 1 adds nullable `scheduled_at`; existing rows continue using `event_date`.
2. Deploy A reads `COALESCE(scheduled_at, event_date)` and supports both columns.
3. Migration 2 backfills `scheduled_at` and makes it non-nullable.
4. Deploy B reads and orders by `scheduled_at` while retaining the response alias `event_date`.
5. Migration 3 drops `event_date` only after Deploy B has fully rolled out.

`CREATE INDEX CONCURRENTLY` allows PostgreSQL to build the index without the long `ACCESS EXCLUSIVE` lock of a regular index build. Omitting it on a 10-million-row table can block normal reads and writes while the index is built, causing latency and request failures.

Migration 3 must follow the complete Deploy B rollout because any remaining Deploy A Pod still references `event_date`. Dropping that column first would make those Pods fail their SQL queries and return 5xx responses.

## Bonus Task

The live expand-and-contract execution is recorded in `Bonus-1-Baseline-and-Transitions-rerun.txt`, with the zero-delta comparison in `Bonus-4-5xx-Baseline-Final-rerun.txt`. `mixedload` was confirmed at `2/2` before the sequence and remained running while the five transitions were performed. The cumulative gateway 5xx counter was `1487` at the baseline and at every transition: after Migration 1, Deploy A, Migration 2, Deploy B, and Migration 3. The normalized `diff` between the baseline and final files was empty, proving a zero delta.

The three migration bodies are recorded in the repository versions for `a7c92b1d5e4f`, `d4e5f6a7b8c9`, and `e5f6a7b8c9d0`. Deploy A used `COALESCE(e.scheduled_at, e.event_date)` for reads and ordering; Deploy B switched to `e.scheduled_at`. The code difference is recorded in `Bonus-2-Deploy-A-and-B-Diff-rerun.txt`.

The migration upgrade bodies used in the live bonus run were:

```python
# a7c92b1d5e4f — Migration 1
op.add_column(
    "events",
    sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
)

# d4e5f6a7b8c9 — Migration 2
op.execute(
    sa.text(
        "UPDATE events SET scheduled_at = event_date "
        "WHERE scheduled_at IS NULL"
    )
)
op.alter_column(
    "events", "scheduled_at",
    existing_type=sa.DateTime(timezone=True),
    nullable=False,
)

# e5f6a7b8c9d0 — Migration 3
op.drop_column("events", "event_date")
```

Deploy A to Deploy B read-path diff:

```diff
- COALESCE(e.scheduled_at, e.event_date) AS event_date
+ e.scheduled_at AS event_date
- GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
+ GROUP BY e.id ORDER BY e.scheduled_at
```

Schema proof:

```text
BEFORE MIGRATION 1: event_date TIMESTAMPTZ NOT NULL; scheduled_at absent; 5 rows
AFTER MIGRATION 3: scheduled_at TIMESTAMPTZ NOT NULL; event_date absent;
                  5 rows; null scheduled_at=0; revision e5f6a7b8c9d0
```

Complete schema query captured before Migration 1 and after Migration 3:

```text
BEFORE MIGRATION 1
id            | integer                  | NO
name          | text                     | NO
venue         | text                     | NO
total_tickets | integer                  | NO
price_cents   | integer                  | NO
email         | character varying        | YES
event_date    | timestamp with time zone | NO
indexes       | events_pkey, idx_events_event_date

AFTER MIGRATION 3 — \d events
Column        | Type                     | Nullable
--------------+--------------------------+----------
id            | integer                  | not null
name          | text                     | not null
venue         | text                     | not null
total_tickets | integer                  | not null
price_cents   | integer                  | not null
email         | character varying(255)   |
scheduled_at  | timestamp with time zone | not null
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
```

Zero-delta proof:

```text
BASELINE: 5xx_total=1487
FINAL:    5xx_total=1487
DIFF:     empty
```

Before Migration 1, `events` contained a non-null `event_date` column and no `scheduled_at`. After Migration 3, it contained non-null `scheduled_at`, no `event_date`, five rows, and zero null `scheduled_at` values. The schema evidence is in `Bonus-3-Schema-Before-After-rerun.txt`.

The single reordering that would cause 5xx is dropping `event_date` before Deploy B has fully rolled out, because old Pods still issue queries that reference the removed column.

For a 10-million-row backfill, keep each transaction bounded:

```text
last_id = 0
while true:
    begin transaction
    updated = UPDATE events
              SET scheduled_at = event_date
              WHERE id > last_id AND id <= last_id + 10000
                AND scheduled_at IS NULL
    commit transaction
    if updated == 0:
        break
    last_id = last_id + 10000
    sleep briefly
```

The downgrade that re-adds `event_date` is not sufficient for safe production rollback once Deploy B is live. Rollback is safe only after traffic moves to code compatible with the restored schema, every active Pod is verified, and both columns remain synchronized during the transition.

### Task 2 evidence outputs

```text
Task2-5-Migration-5xx-rerun.txt
- explicit numeric 0 before and after the concurrent-index migration
- Alembic revision transition and resulting index
```

```text
Task2-3-Concurrent-Index-Code.txt
- autocommit_block() wrapper for CREATE/DROP INDEX CONCURRENTLY
```

### Bonus evidence outputs

```text
Bonus-1-Baseline-and-Transitions-rerun.txt
- mixedload baseline and all five live transitions
- cumulative 5xx counter remains 1487
```

```text
Bonus-4-5xx-Baseline-Final-rerun.txt
- baseline 1487
- final 1487
- empty diff
```

```text
Bonus-2-Deploy-A-and-B-Diff-rerun.txt
- COALESCE fallback in Deploy A
- scheduled_at-only reads in Deploy B
```

```text
Bonus-3-Schema-Before-After-rerun.txt
- event_date before Migration 1
- scheduled_at NOT NULL and event_date absent after Migration 3
```


## Optional HPA

The HPA manifest in `k8s/gateway-hpa.yaml` targeted the gateway Rollout with `minReplicas: 5`, `maxReplicas: 12`, and a CPU target of 70%.

The observation in `Optional-12-9-HPA-Under-Load.txt` showed CPU above target and the gateway scaling from 5 to 12 replicas. The final HPA state was `cpu: 84%/70%`, `REPLICAS 12`, with all 12 gateway Pods available. The HPA capture does not include a per-Pod placement listing; the single-node limitation is directly evidenced by the Task 1 placement capture and therefore this observation does not demonstrate multi-node elasticity. No screenshot was captured, so the optional screenshot-specific proof is incomplete.

The Locust run was unsuccessful: 1,062 of 1,204 requests failed (88.21%), including 503, 502, 504, connection-refused, and reset errors. The HPA scaling observation is recorded, but the load test did not succeed and single-node placement does not demonstrate multi-node elasticity.


## Embedded proof outputs

### Task 1 raw outputs

```text
[Task1-1-Replica-PDB-Topology-corrected.txt]
=== UTC ===
2026-10-16 07:35:07 UTC
=== DEPLOYMENTS ===
NAME            READY   UP-TO-DATE   AVAILABLE   AGE
events          2/2     2            2           19d
payments        2/2     2            2           19d
notifications   2/2     2            2           35h
=== GATEWAY ROLLOUT ===
Status:          ✔ Healthy
Step:            6/6
Desired:         5
Current:         5
Updated:         5
Ready:           5
Available:       5
=== PDB ===
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb          1               N/A               1                     33h
gateway-pdb         2               N/A               3                     33h
notifications-pdb   N/A             1                 1                     33h
payments-pdb        1               N/A               1                     33h
=== TOPOLOGY SPEC ===
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
=== GATEWAY PLACEMENT ===
NAME                      READY   STATUS    RESTARTS   AGE     IP           NODE                       NOMINATED NODE   READINESS GATES
gateway-698cf6b6c8-2pbv7   1/1     Running   0          18h   10.42.0.141   k3d-quickticket-server-0   <none>           <none>
gateway-698cf6b6c8-bgx74   1/1     Running   0          18h   10.42.0.140   k3d-quickticket-server-0   <none>           <none>
gateway-698cf6b6c8-cwhh5   1/1     Running   0          18h   10.42.0.142   k3d-quickticket-server-0   <none>           <none>
gateway-698cf6b6c8-tqrh9   1/1     Running   0          18h   10.42.0.144   k3d-quickticket-server-0   <none>           <none>
gateway-698cf6b6c8-xzgtz   1/1     Running   0          18h   10.42.0.143   k3d-quickticket-server-0   <none>           <none>
```

```text
[Task1-2-Failover-before-kill-corrected.txt]
=== UTC BASELINE === 2026-10-15 12:42:58 UTC
gateway=gateway-698cf6b6c8-d5ws9 events=events-85d957cfc-sxktc
=== GLOBAL_5XX_BEFORE_3M ===
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1792068179.334,"0"]}]}}
pod "gateway-698cf6b6c8-d5ws9" deleted from default namespace
pod "events-85d957cfc-sxktc" deleted from default namespace
```

```text
[Task1-2-Failover-after-kill-corrected.txt]
=== UTC READY === 2026-10-15 12:43:20 UTC
RECOVERY_SECONDS=22
NAME                       READY   STATUS    RESTARTS      AGE     IP            NODE                       NOMINATED NODE   READINESS GATES
events-85d957cfc-6zgmt     1/1     Running   0             21s     10.42.0.139   k3d-quickticket-server-0   <none>           <none>
events-85d957cfc-x42bq     1/1     Running   0             13h     10.42.0.130   k3d-quickticket-server-0   <none>           <none>
gateway-698cf6b6c8-98vk9   1/1     Running   0             21s     10.42.0.138   k3d-quickticket-server-0   <none>           <none>
gateway-698cf6b6c8-dzs2z   1/1     Running   0             4m37s   10.42.0.135   k3d-quickticket-server-0   <none>           <none>
gateway-698cf6b6c8-gbvsm   1/1     Running   1 (14h ago)   14h     10.42.0.115   k3d-quickticket-server-0   <none>           <none>
gateway-698cf6b6c8-lj6ns   1/1     Running   0             14h     10.42.0.118   k3d-quickticket-server-0   <none>           <none>
gateway-698cf6b6c8-plf9j   1/1     Running   0             14h     10.42.0.119   k3d-quickticket-server-0   <none>           <none>
=== GLOBAL_5XX_AFTER_1M ===
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1792068202.602,"0"]}]}}
=== BY_PATH_STATUS_AFTER_1M ===
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"path":"/health","status":"503"},"value":[1792068203.985,"0"]},{"metric":{"path":"/events/{id}/reserve","status":"504"},"value":[1792068203.985,"0"]},{"metric":{"path":"/events","status":"502"},"value":[1792068203.985,"0"]},{"metric":{"path":"/events/{id}/reserve","status":"502"},"value":[1792068203.985,"0"]}]}}
```

```text
[Task1-5-PDB-Eviction-429.txt]
HTTP/1.1 429 Too Many Requests
Audit-Id: 061fa111-043a-4a49-8c3c-3784ee1240f2
Cache-Control: no-cache, private
Content-Length: 404
Content-Type: application/json
Date: Wed, 14 Oct 2026 21:59:38 GMT
X-Kubernetes-Pf-Flowschema-Uid: 2154fdcf-ac9e-4f05-b6fe-7cc633f80f4e
X-Kubernetes-Pf-Prioritylevel-Uid: 014ed8f0-e252-4ba0-9921-84b662b292a7

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
}```

### Task 2 and bonus raw outputs

The complete verified outputs are kept in the evidence files named in the sections above. The key values are:

```text
Task2-5-Migration-5xx-rerun.txt
before error rate: 0
after error rate: 0
Alembic: bf3aa2d35ec3 -> c1f4e8a92b7d
index: idx_events_event_date
```

```text
Bonus-1-Baseline-and-Transitions-rerun.txt
baseline: 1487
migration 1: 1487
Deploy A: 1487
migration 2: 1487
Deploy B: 1487
migration 3: 1487
```

```text
Bonus-4-5xx-Baseline-Final-rerun.txt
diff: empty
result: PASS_ZERO_DELTA
```

### Optional HPA manifest and observation

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: gateway
spec:
  scaleTargetRef:
    apiVersion: argoproj.io/v1alpha1
    kind: Rollout
    name: gateway
  minReplicas: 5
  maxReplicas: 12
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

```text
[Optional-12-9-HPA-Under-Load.txt]
2026-10-14 22:20:44 UTC
HPA_DURING_LOAD
NAME      REFERENCE         TARGETS              MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: <unknown>/70%   5         12        0          2s
NAME           STATUS    COMPLETIONS   DURATION   AGE   CONTAINERS   IMAGES                   SELECTOR
load-hpa-200   Running   0/1           1s         1s    locust       locustio/locust:2.43.4   batch.kubernetes.io/controller-uid=2d414d71-6fe8-4974-8c7e-9f4b0356919a
NAME      REFERENCE         TARGETS              MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: <unknown>/70%   5         12        0          14s
NAME           STATUS    COMPLETIONS   DURATION   AGE   CONTAINERS   IMAGES                   SELECTOR
load-hpa-200   Running   0/1           15s        15s   locust       locustio/locust:2.43.4   batch.kubernetes.io/controller-uid=2d414d71-6fe8-4974-8c7e-9f4b0356919a
NAME      REFERENCE         TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 116%/70%   5         12        5          29s
NAME           STATUS    COMPLETIONS   DURATION   AGE   CONTAINERS   IMAGES                   SELECTOR
load-hpa-200   Running   0/1           31s        31s   locust       locustio/locust:2.43.4   batch.kubernetes.io/controller-uid=2d414d71-6fe8-4974-8c7e-9f4b0356919a
NAME      REFERENCE         TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 162%/70%   5         12        9          44s
NAME           STATUS    COMPLETIONS   DURATION   AGE   CONTAINERS   IMAGES                   SELECTOR
load-hpa-200   Running   0/1           44s        44s   locust       locustio/locust:2.43.4   batch.kubernetes.io/controller-uid=2d414d71-6fe8-4974-8c7e-9f4b0356919a
NAME      REFERENCE         TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 108%/70%   5         12        12         57s
NAME           STATUS    COMPLETIONS   DURATION   AGE   CONTAINERS   IMAGES                   SELECTOR
load-hpa-200   Running   0/1           57s        57s   locust       locustio/locust:2.43.4   batch.kubernetes.io/controller-uid=2d414d71-6fe8-4974-8c7e-9f4b0356919a
NAME      REFERENCE         TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 148%/70%   5         12        12         69s
NAME           STATUS    COMPLETIONS   DURATION   AGE   CONTAINERS   IMAGES                   SELECTOR
load-hpa-200   Running   0/1           69s        69s   locust       locustio/locust:2.43.4   batch.kubernetes.io/controller-uid=2d414d71-6fe8-4974-8c7e-9f4b0356919a
NAME      REFERENCE         TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 148%/70%   5         12        12         82s
NAME           STATUS    COMPLETIONS   DURATION   AGE   CONTAINERS   IMAGES                   SELECTOR
load-hpa-200   Running   0/1           83s        83s   locust       locustio/locust:2.43.4   batch.kubernetes.io/controller-uid=2d414d71-6fe8-4974-8c7e-9f4b0356919a
NAME      REFERENCE         TARGETS        MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 83%/70%   5         12        12         98s
NAME           STATUS    COMPLETIONS   DURATION   AGE    CONTAINERS   IMAGES                   SELECTOR
load-hpa-200   Running   0/1           100s       100s   locust       locustio/locust:2.43.4   batch.kubernetes.io/controller-uid=2d414d71-6fe8-4974-8c7e-9f4b0356919a
NAME      REFERENCE         TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 169%/70%   5         12        12         113s
NAME           STATUS    COMPLETIONS   DURATION   AGE    CONTAINERS   IMAGES                   SELECTOR
load-hpa-200   Running   0/1           114s       114s   locust       locustio/locust:2.43.4   batch.kubernetes.io/controller-uid=2d414d71-6fe8-4974-8c7e-9f4b0356919a
NAME      REFERENCE         TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 150%/70%   5         12        12         2m7s
NAME           STATUS    COMPLETIONS   DURATION   AGE    CONTAINERS   IMAGES                   SELECTOR
load-hpa-200   Running   0/1           2m8s       2m8s   locust       locustio/locust:2.43.4   batch.kubernetes.io/controller-uid=2d414d71-6fe8-4974-8c7e-9f4b0356919a
NAME      REFERENCE         TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 131%/70%   5         12        12         2m20s
NAME           STATUS    COMPLETIONS   DURATION   AGE     CONTAINERS   IMAGES                   SELECTOR
load-hpa-200   Running   0/1           2m21s      2m21s   locust       locustio/locust:2.43.4   batch.kubernetes.io/controller-uid=2d414d71-6fe8-4974-8c7e-9f4b0356919a
NAME      REFERENCE         TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 131%/70%   5         12        12         2m34s
NAME           STATUS    COMPLETIONS   DURATION   AGE     CONTAINERS   IMAGES                   SELECTOR
load-hpa-200   Running   0/1           2m36s      2m36s   locust       locustio/locust:2.43.4   batch.kubernetes.io/controller-uid=2d414d71-6fe8-4974-8c7e-9f4b0356919a
NAME      REFERENCE         TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 106%/70%   5         12        12         2m50s
NAME           STATUS   COMPLETIONS   DURATION   AGE     CONTAINERS   IMAGES                   SELECTOR
load-hpa-200   Failed   0/1           2m51s      2m51s   locust       locustio/locust:2.43.4   batch.kubernetes.io/controller-uid=2d414d71-6fe8-4974-8c7e-9f4b0356919a
NAME      REFERENCE         TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 106%/70%   5         12        12         3m4s
NAME           STATUS   COMPLETIONS   DURATION   AGE    CONTAINERS   IMAGES                   SELECTOR
load-hpa-200   Failed   0/1           3m5s       3m5s   locust       locustio/locust:2.43.4   batch.kubernetes.io/controller-uid=2d414d71-6fe8-4974-8c7e-9f4b0356919a
NAME      REFERENCE         TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 113%/70%   5         12        12         3m18s
NAME           STATUS   COMPLETIONS   DURATION   AGE     CONTAINERS   IMAGES                   SELECTOR
load-hpa-200   Failed   0/1           3m19s      3m19s   locust       locustio/locust:2.43.4   batch.kubernetes.io/controller-uid=2d414d71-6fe8-4974-8c7e-9f4b0356919a
NAME      REFERENCE         TARGETS        MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 84%/70%   5         12        12         3m32s
   locust       locustio/locust:2.43.4   batch.kubernetes.io/controller-uid=2d414d71-6fe8-4974-8c7e-9f4b0356919a
FINAL_LOCUST_LOG
[2026-10-14 22:20:53,474] load-hpa-200-fz9rr/INFO/locust.main: Starting Locust 2.43.4
[2026-10-14 22:20:53,475] load-hpa-200-fz9rr/INFO/locust.main: Run time limit set to 120 seconds
[2026-10-14 22:20:53,477] load-hpa-200-fz9rr/INFO/locust.runners: Ramping to 200 users at a rate of 20.00 per second
[2026-10-14 22:21:06,871] load-hpa-200-fz9rr/INFO/locust.runners: All users spawned: {"QuickTicketUser": 200} (200 total users)
[2026-10-14 22:23:00,366] load-hpa-200-fz9rr/INFO/locust.main: --run-time limit reached, shutting down
[2026-10-14 22:23:11,473] load-hpa-200-fz9rr/INFO/locust.main: Shutting down (exit code 1)
Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
GET      /events                                                                          799  663(82.98%) |  16132      28   46403  13000 |    5.79        4.81
POST     /events/3/reserve                                                                211 211(100.00%) |  16195     113   43364  12000 |    1.53        1.53
POST     /events/5/reserve                                                                 62  62(100.00%) |  18447     246   45368  13000 |    0.45        0.45
GET      /health                                                                          132  126(95.45%) |  17308     365   59254  11000 |    0.96        0.91
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                      1204 1062(88.21%) |  16391      28   59254  12000 |    8.73        7.70

Response time percentiles (approximated)
Type     Name                                                                                  50%    66%    75%    80%    90%    95%    98%    99%  99.9% 99.99%   100% # reqs
--------|--------------------------------------------------------------------------------|--------|------|------|------|------|------|------|------|------|------|------|------
GET      /events                                                                             13000  19000  27000  29000  38000  42000  42000  43000  46000  46000  46000    799
POST     /events/3/reserve                                                                   12000  17000  28000  31000  37000  41000  43000  43000  43000  43000  43000    211
POST     /events/5/reserve                                                                   14000  25000  29000  32000  39000  43000  43000  45000  45000  45000  45000     62
GET      /health                                                                             12000  19000  26000  36000  44000  49000  56000  56000  59000  59000  59000    132
--------|--------------------------------------------------------------------------------|--------|------|------|------|------|------|------|------|------|------|------|------
         Aggregated                                                                          12000  19000  27000  30000  38000  42000  43000  46000  56000  59000  59000   1204

Error report
# occurrences      Error
------------------|---------------------------------------------------------------------------------------------------------------------------------------------
19                 POST /events/3/reserve: HTTPError('409 Client Error: Conflict for url: /events/3/reserve')
107                GET /health: HTTPError('503 Server Error: Service Unavailable for url: /health')
156                GET /events: HTTPError('502 Server Error: Bad Gateway for url: /events')
32                 POST /events/3/reserve: HTTPError('500 Server Error: Internal Server Error for url: /events/3/reserve')
386                GET /events: HTTPError('504 Server Error: Gateway Timeout for url: /events')
119                POST /events/3/reserve: HTTPError('504 Server Error: Gateway Timeout for url: /events/3/reserve')
12                 POST /events/5/reserve: HTTPError('500 Server Error: Internal Server Error for url: /events/5/reserve')
5                  POST /events/5/reserve: HTTPError('502 Server Error: Bad Gateway for url: /events/5/reserve')
10                 POST /events/3/reserve: HTTPError('502 Server Error: Bad Gateway for url: /events/3/reserve')
43                 GET /events: ConnectionRefusedError(111, 'Connection refused')
9                  POST /events/3/reserve: ConnectionRefusedError(111, 'Connection refused')
4                  GET /health: ConnectionRefusedError(111, 'Connection refused')
60                 GET /events: RemoteDisconnected('Remote end closed connection without response')
10                 GET /health: RemoteDisconnected('Remote end closed connection without response')
18                 GET /events: ConnectionResetError(104, 'Connection reset by peer')
1                  POST /events/5/reserve: ConnectionRefusedError(111, 'Connection refused')
15                 POST /events/3/reserve: RemoteDisconnected('Remote end closed connection without response')
30                 POST /events/5/reserve: HTTPError('504 Server Error: Gateway Timeout for url: /events/5/reserve')
5                  GET /health: ConnectionResetError(104, 'Connection reset by peer')
9                  POST /events/5/reserve: HTTPError('409 Client Error: Conflict for url: /events/5/reserve')
7                  POST /events/3/reserve: ConnectionResetError(104, 'Connection reset by peer')
4                  POST /events/5/reserve: RemoteDisconnected('Remote end closed connection without response')
1                  POST /events/5/reserve: ConnectionResetError(104, 'Connection reset by peer')
------------------|---------------------------------------------------------------------------------------------------------------------------------------------

FINAL_HPA
NAME      REFERENCE         TARGETS        MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 84%/70%   5         12        12         3m29s
FINAL_ROLLOUT
NAME      DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
gateway   12        12        12           12          10d
2026-10-14 22:24:15 UTC
NAME      REFERENCE         TARGETS        MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 81%/70%   5         12        12         3m35s
2026-10-14 22:24:31 UTC
2026-10-14 22:24:53 UTC
2026-10-14 22:25:08 UTC
```
