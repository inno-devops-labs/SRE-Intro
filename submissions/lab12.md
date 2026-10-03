# Lab 12

The cluster was kept under live `mixedload` traffic throughout the resilience checks. The evidence below follows the same compact, proof-first style as the recent submissions in this repository.

## Task 1

The first change was scaling `events`, `payments`, and `notifications` to `2` replicas and applying the new PDBs. The cluster then showed the expected target counts:

```text
deployments
NAME            READY   UP-TO-DATE   AVAILABLE   AGE
events          2/2     2            2           7d
payments        2/2     2            2           7d
notifications   2/2     2            2           121m

rollout
NAME      DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
gateway   5         5         5            5           6d22h
```

`kubectl get pdb` after applying `k8s/pdb.yaml`:

```text
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb          1               N/A               1                     44h
gateway-pdb         2               N/A               3                     44h
notifications-pdb   N/A             1                 1                     44h
payments-pdb        1               N/A               1                     44h
```

The gateway Rollout now has the required topology spread constraint in the live spec:

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

On this single-node `k3d` cluster, the placement still lands all five gateway pods on the same node, which is the expected no-op behavior:

```text
NAME                       READY   STATUS    RESTARTS   AGE     IP           NODE
gateway-79f4458967-kzg7h   1/1     Running   0          4m45s   10.42.0.70   k3d-quickticket-server-0
gateway-79f4458967-mdl47   1/1     Running   0          2m30s   10.42.0.73   k3d-quickticket-server-0
gateway-79f4458967-pzgh5   1/1     Running   0          118s    10.42.0.74   k3d-quickticket-server-0
gateway-79f4458967-sph64   1/1     Running   0          88s     10.42.0.75   k3d-quickticket-server-0
gateway-79f4458967-vf72s   1/1     Running   0          58s     10.42.0.76   k3d-quickticket-server-0
```

For the coordinated pod-kill test under load, the Prometheus `5xx` count stayed at zero before and after the deletions:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784314536.499,"0"]}]}}
```

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784314587.988,"0"]}]}}
```

To prove that the PDB was really enforced, `events-pdb` was temporarily tightened to `minAvailable: 2` and one eviction was sent through the eviction API. The request was rejected with HTTP `429`:

```text
pod=events-6464cd9548-d2bxx
http_code=429
```

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

With `3` gateway replicas and `minAvailable: 1`, the maximum number of pods that can be evicted simultaneously is `2`. `gateway-pdb` uses `minAvailable: 2` with `5` replicas so maintenance can proceed without dropping the gateway to a single surviving pod.

With `maxSkew: 1` on a `3`-node cluster, `5` gateway pods would place as `2/2/1`, and `7` pods would place as `3/2/2`.

## Task 2

The gateway Rollout now contains the required graceful-shutdown block:

```yaml
spec:
  template:
    spec:
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

Under live mixedload, a full `kubectl argo rollouts restart gateway` completed with zero new `5xx` before and after:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784314933.739,"0"]}]}}
```

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784315054.220,"0"]}]}}
```

The `CREATE INDEX CONCURRENTLY` migration uses Alembic’s `autocommit_block` wrapper:

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

It completed under live traffic without adding any `5xx`:

```text
0.342 seconds
```

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784315145.775,"0"]}]}}
```

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784315152.231,"0"]}]}}
```

The new index is visible in `\d events`:

```text
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
```

The zero-downtime rename sketch for `events.event_date -> events.scheduled_at` is:

1. Migration 1: add nullable `scheduled_at`.
2. Deploy A: read `scheduled_at` if present, otherwise fall back to `event_date`; keep external response shape unchanged.
3. Migration 2: backfill `scheduled_at = event_date` where still null, then make `scheduled_at NOT NULL`.
4. Deploy B: read only from `scheduled_at`; keep returning the same JSON field `date`.
5. Migration 3: drop `event_date` only after Deploy B is fully rolled out.

The optional HPA observation was also executed. After applying `k8s/gateway-hpa.yaml` and driving CPU with a high-concurrency Locust Job, the HPA observed CPU above target and raised desired replicas to `12`:

`k8s/gateway-hpa.yaml`:

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

Observed HPA state:

```text
NAME      REFERENCE         TARGETS        MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 90%/70%   5         12        12         2m51s
```

```text
NAME      DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
gateway   12        12        12           12          6d22h
```

`CREATE INDEX CONCURRENTLY` matters because the non-concurrent form can take a strong lock and block production traffic. On a `10M`-row table, omitting `CONCURRENTLY` can turn the index build into a visible outage.

Migration 3 must come only after Deploy B is fully rolled out because any surviving old pod that still references `event_date` would immediately start failing once that column disappeared.

## Bonus Task

The bonus was executed live under mixedload through all five transitions:

1. Migration 1
2. Deploy A
3. Migration 2
4. Deploy B
5. Migration 3

The three migration `upgrade()` bodies are:

```python
def upgrade() -> None:
    op.add_column("events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))
```

```python
def upgrade() -> None:
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)
```

```python
def upgrade() -> None:
    op.drop_column("events", "event_date")
```

The runtime code change between Deploy A and Deploy B was the expected switch from compatibility reads to the new column only:

```diff
--- Deploy A
+++ Deploy B
@@
-            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date), e.total_tickets, e.price_cents,
+            SELECT e.id, e.name, e.venue, e.scheduled_at, e.total_tickets, e.price_cents,
                    COALESCE(SUM(o.quantity), 0) as confirmed
             FROM events e LEFT JOIN orders o ON e.id = o.event_id
-            GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
+            GROUP BY e.id ORDER BY e.scheduled_at
@@
-            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date), e.total_tickets, e.price_cents,
+            SELECT e.id, e.name, e.venue, e.scheduled_at, e.total_tickets, e.price_cents,
```

There was no runtime dual-write path to change in QuickTicket, because the service does not expose a live event-creation endpoint. The only write-side schema update needed in the repository was the boot-time `app/seed.sql`, which now uses `scheduled_at`.

Schema before Migration 1:

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
```

Schema after Migration 3:

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
```

The backfill after Migration 2 confirmed that every row received the copied value:

```text
 id |       event_date       |      scheduled_at
----+------------------------+------------------------
  1 | 2026-09-15 09:00:00+00 | 2026-09-15 09:00:00+00
  2 | 2026-10-01 18:00:00+00 | 2026-10-01 18:00:00+00
  3 | 2026-11-20 10:00:00+00 | 2026-11-20 10:00:00+00
  4 | 2026-09-22 14:00:00+00 | 2026-09-22 14:00:00+00
  5 | 2026-10-10 10:00:00+00 | 2026-10-10 10:00:00+00
```

The cumulative `5xx` counter stayed at `0` from the baseline through the final state. Because the Prometheus API response includes a changing timestamp, the numeric values were normalized before running `diff`:

```text
baseline value:
0

final value:
0
```

```text
$ diff /tmp/lab12-evidence/task3_5xx_baseline_value.txt /tmp/lab12-evidence/task3_5xx_final_value.txt
# no output
```

The single step that would have caused `5xx` if moved earlier is Migration 3. Dropping `event_date` before Deploy B had fully replaced every compatible reader would have broken any remaining old code path immediately.

On a `10M`-row table, the backfill should be batched instead of one large `UPDATE`. Batching pattern:

```text
last_id = 0
loop:
  UPDATE events
  SET scheduled_at = event_date
  WHERE scheduled_at IS NULL
    AND id > last_id
  ORDER BY id
  LIMIT 10000
  commit
  sleep a short interval
  advance last_id to the last updated row
  stop when no rows remain
```

The downgrade from Migration 3 is not sufficient for true rollback safety once Deploy B is live in production because schema rollback alone is not enough. A safe rollback would require all live readers and writers to be moved back to a code version that is still compatible with the restored old column before or together with the schema rollback.
