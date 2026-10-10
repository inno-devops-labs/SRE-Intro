# Lab 12 — Advanced Kubernetes Resilience

Completed Task 1, Task 2, and the executed expand-and-contract bonus. Optional HPA observation 12.9 was not performed.

Environment: macOS Apple Silicon; single-node k3d quickticket, Kubernetes v1.35.5+k3s1; Argo Rollouts gateway; in-cluster Prometheus; PostgreSQL 17 on the existing PostgreSQL PVC. Live experiments used both replicas of the unchanged Lab 8 mixedload and supplementary read traffic.

## Safety and measurement

ArgoCD automatic synchronization was temporarily disabled before local manifest changes, preventing self-heal from reverting to Lab 11. The original policy was saved for the GitOps handoff. PostgreSQL and Redis deployments were not redeployed for this lab.

Two custom-format pg_dump backups were retained locally, with matching SHA-256 checksums between the pod and Mac. The pre-lab backup was taken before traffic; the pre-contract backup used a consistent snapshot while traffic continued. Its checkpoint counts are not exact dump counts.

```json
{
  "counts_before": {
    "events": 5,
    "orders": 15257,
    "revision": "9a0000000002"
  },
  "counts_after": {
    "events": 5,
    "orders": 15257,
    "revision": "9a0000000002"
  },
  "bytes": 563688,
  "sha256": "dc6ba68bf4abe64dd2b78db216a54bfa41fc71e56360baa663ced079434d406a",
  "pod_sha256": "dc6ba68bf4abe64dd2b78db216a54bfa41fc71e56360baa663ced079434d406a",
  "checksum_verified": true,
  "method": "Custom pg_dump before starting lab traffic; before/after counts checked."
}
```

```json
{
  "bytes": 999791,
  "sha256": "72ca7f2334dbfa56f684fe16514adffa87194df9e92a64d4ef4abd748f6e94cf",
  "pod_sha256": "72ca7f2334dbfa56f684fe16514adffa87194df9e92a64d4ef4abd748f6e94cf",
  "checksum_verified": true,
  "checkpoint_before_dump": {
    "events": 5,
    "orders": 27102,
    "revision": "12a000000003"
  },
  "note": "pg_dump uses a consistent snapshot. Checkout traffic stayed active; the checkpoint counts are not claimed to be exact dump row counts."
}
```

Absent 5xx series were converted to zero using `or on() vector(0)`, and positive request traffic was separately verified. Prometheus `increase` results are estimates and can be fractional. During pod replacement, range queries included retired pod series; an independent checkout client also recorded HTTP failures and network errors. For the index migration and entire bonus sequence, gateway pod UIDs were unchanged and container restart counts remained zero, allowing cumulative counter comparisons. Numerical values were compared, not complete Prometheus responses containing changing timestamps.

## Task 1 — Multi-replica failover, PDBs, topology spread

### Replica counts

```text
NAME                            READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/events          2/2     2            2           13d
deployment.apps/mixedload       2/2     2            2           5m57s
deployment.apps/notifications   2/2     2            2           6h25m
deployment.apps/payments        2/2     2            2           13d
deployment.apps/postgres        1/1     1            1           13d
deployment.apps/redis           1/1     1            1           13d

NAME                          DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
rollout.argoproj.io/gateway   5         5         5            5           6d7h
```

Events, payments, and notifications each have two replicas. Gateway has five. Dependency rolling updates use maxSurge: 1 and maxUnavailable: 0. Events also has preStop sleep 10 and a 40-second termination budget to reduce endpoint-propagation races during its pod-kill and code-deployment tests.

### Baseline and coordinated pod deletion

```json
{
  "gateway_5xx_increase_3m": 0.0,
  "gateway_non_health_requests_increase_3m": 2854.3329263653163,
  "gateway_scrape_targets_up": 5.0,
  "raw_5xx_query": {
    "status": "success",
    "data": {
      "resultType": "vector",
      "result": [
        {
          "metric": {},
          "value": [
            1791656662.073,
            "0"
          ]
        }
      ]
    }
  },
  "raw_traffic_query": {
    "status": "success",
    "data": {
      "resultType": "vector",
      "result": [
        {
          "metric": {},
          "value": [
            1791656662.132,
            "2854.3329263653163"
          ]
        }
      ]
    }
  },
  "raw_targets_query": {
    "status": "success",
    "data": {
      "resultType": "vector",
      "result": [
        {
          "metric": {},
          "value": [
            1791656662.191,
            "5"
          ]
        }
      ]
    }
  }
}
```

```json
{
  "before_prometheus_5xx_increase_3m": 0.0,
  "after_prometheus_5xx_increase_5m": 0.0,
  "sampled_5xx_counters_including_retired_pods": 0.0,
  "non_health_requests_increase_5m": 6318.6631311397905,
  "replacement_ready": {
    "gateway": {
      "seconds_after_delete": 9.073,
      "pod": "gateway-7d4654cb8c-97rl7"
    },
    "events": {
      "seconds_after_delete": 11.253,
      "pod": "events-f6d664db9-fhqsg"
    }
  },
  "observer": {
    "duration_seconds": 120,
    "checkout_ok": 223,
    "checkout_fail": 0,
    "status_counts": {
      "200": 669
    },
    "http_5xx": 0,
    "network_errors": 0
  }
}
```

```text
NAME                       READY   STATUS    RESTARTS   AGE     IP            NODE                       NOMINATED NODE   READINESS GATES
events-f6d664db9-csj6q     1/1     Running   0          10m     10.42.0.199   k3d-quickticket-server-0   <none>           <none>
events-f6d664db9-fhqsg     1/1     Running   0          2m      10.42.0.212   k3d-quickticket-server-0   <none>           <none>
gateway-7d4654cb8c-94qbj   1/1     Running   0          7m54s   10.42.0.207   k3d-quickticket-server-0   <none>           <none>
gateway-7d4654cb8c-97rl7   1/1     Running   0          2m      10.42.0.211   k3d-quickticket-server-0   <none>           <none>
gateway-7d4654cb8c-cfj68   1/1     Running   0          7m16s   10.42.0.210   k3d-quickticket-server-0   <none>           <none>
gateway-7d4654cb8c-jjv6c   1/1     Running   0          10m     10.42.0.206   k3d-quickticket-server-0   <none>           <none>
gateway-7d4654cb8c-rpdvp   1/1     Running   0          7m22s   10.42.0.209   k3d-quickticket-server-0   <none>           <none>
```

One gateway pod and one events pod were deleted under active checkout traffic. Replacement readiness was observed after 9.073 seconds and 11.253 seconds respectively, rather than the lab's approximate five seconds. The independent observer completed 223 checkouts, with all 669 requests returning 200; 5xx and network errors were zero.

### Four PodDisruptionBudgets

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: gateway-pdb
  namespace: default
spec:
  minAvailable: 2
  selector:
    matchLabels:
      app: gateway
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: events-pdb
  namespace: default
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: events
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: payments-pdb
  namespace: default
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: payments
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: notifications-pdb
  namespace: default
spec:
  maxUnavailable: 1
  selector:
    matchLabels:
      app: notifications
```

```text
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb          1               N/A               1                     12m
gateway-pdb         2               N/A               3                     12m
notifications-pdb   N/A             1                 1                     12m
payments-pdb        1               N/A               1                     12m
```

### Topology spread configuration and actual placement

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

```text
NAME                       READY   STATUS    RESTARTS   AGE     IP            NODE                       NOMINATED NODE   READINESS GATES
gateway-7d4654cb8c-8sfvr   1/1     Running   0          3m41s   10.42.0.208   k3d-quickticket-server-0   <none>           <none>
gateway-7d4654cb8c-94qbj   1/1     Running   0          3m47s   10.42.0.207   k3d-quickticket-server-0   <none>           <none>
gateway-7d4654cb8c-cfj68   1/1     Running   0          3m9s    10.42.0.210   k3d-quickticket-server-0   <none>           <none>
gateway-7d4654cb8c-jjv6c   1/1     Running   0          5m54s   10.42.0.206   k3d-quickticket-server-0   <none>           <none>
gateway-7d4654cb8c-rpdvp   1/1     Running   0          3m15s   10.42.0.209   k3d-quickticket-server-0   <none>           <none>
```

All gateway pods run on the only k3d node. ScheduleAnyway is a soft placement preference: the scheduler tries to reduce skew but does not enforce a hard placement guarantee.

### Real eviction API rejection

events-pdb was temporarily tightened to minAvailable: 2. After its controller reported zero allowed disruptions, POST `/api/v1/namespaces/default/pods/<name>/eviction` was issued through a loopback kubectl proxy. The original pod UID remained present without a deletion timestamp. The PDB was restored to minAvailable: 1 and the proxy stopped in cleanup.

```json
{
  "http_status": 429,
  "pod": "events-f6d664db9-csj6q",
  "body": {
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
}
```

The actual response has top-level reason `TooManyRequests`; `details.causes[0].reason` is `DisruptionBudget`. Direct pod deletion in the failover test bypasses PDB protection; the separate eviction test proves its enforcement.

### Design answers

With three healthy gateway replicas and minAvailable: 1, at most two can be voluntarily evicted simultaneously. Our five-replica gateway uses minAvailable: 2, allowing up to three voluntary disruptions while retaining two ready replicas. This balances availability and maintenance progress; it does not guarantee a particular throughput or protect against node crashes.

For three eligible nodes, balanced placement is 2/2/1 for five pods and 3/2/2 for seven. These are the preferred outcomes with maxSkew: 1; ScheduleAnyway can permit a less balanced placement when other scheduling constraints intervene.

## Task 2 — Graceful restart and concurrent index migration

### Gateway shutdown and readiness

```json
{
  "terminationGracePeriodSeconds": 40,
  "lifecycle": {
    "preStop": {
      "exec": {
        "command": [
          "sh",
          "-c",
          "sleep 10"
        ]
      }
    }
  },
  "readinessProbe": {
    "failureThreshold": 1,
    "httpGet": {
      "path": "/health",
      "port": 8080
    },
    "initialDelaySeconds": 5,
    "periodSeconds": 2,
    "timeoutSeconds": 3
  },
  "command": [
    "uvicorn"
  ],
  "args": [
    "main:app",
    "--host",
    "0.0.0.0",
    "--port",
    "8080",
    "--timeout-graceful-shutdown",
    "30"
  ]
}
```

The pod termination budget is 40 seconds: a 10-second preStop delay followed by up to 30 seconds of Uvicorn graceful shutdown, explicitly configured with --timeout-graceful-shutdown 30. Liveness remains on /metrics; readiness uses /health with periodSeconds: 2 and failureThreshold: 1. Terminating endpoints are withdrawn by Kubernetes; the preStop delay gives routing updates time to propagate before SIGTERM.

### Rolling restart under load

```json
{
  "restart_duration_seconds": 69.523,
  "all_five_original_pods_replaced": true,
  "before_5xx_increase_1m": 0.0,
  "after_query_window_seconds": 180,
  "after_5xx_increase": 0.0,
  "sampled_5xx_counters_including_retired_pods": 0.0,
  "non_health_requests_increase": 3973.4912460226765,
  "observer": {
    "duration_seconds": 104.927,
    "checkout_ok": 193,
    "checkout_fail": 0,
    "status_counts": {
      "200": 579
    },
    "http_5xx": 0,
    "network_errors": 0
  }
}
```

```text
NAME                       READY   STATUS    RESTARTS   AGE   IP            NODE                       NOMINATED NODE   READINESS GATES
gateway-7d4654cb8c-7g84x   1/1     Running   0          65s   10.42.0.217   k3d-quickticket-server-0   <none>           <none>
gateway-7d4654cb8c-n8ggx   1/1     Running   0          83s   10.42.0.214   k3d-quickticket-server-0   <none>           <none>
gateway-7d4654cb8c-nqbc5   1/1     Running   0          71s   10.42.0.216   k3d-quickticket-server-0   <none>           <none>
gateway-7d4654cb8c-pp9n9   1/1     Running   0          77s   10.42.0.215   k3d-quickticket-server-0   <none>           <none>
gateway-7d4654cb8c-x8mcx   1/1     Running   0          89s   10.42.0.213   k3d-quickticket-server-0   <none>           <none>
```

The restart was requested by setting Rollout spec.restartAt, the same mechanism used by the Argo restart command. All five original pod UIDs were replaced. Completion was checked by pod identity and readiness, not merely an initially Healthy rollout phase. Restart finished in 69.523 seconds. The observer completed 193 checkouts and 579 HTTP 200 requests, without 5xx or network failures.

Unlike a pod-template update, an Argo restart does not rerun the canary steps. It evicts and recreates pods and respects PDBs. [Argo restart documentation](https://argo-rollouts.readthedocs.io/en/stable/features/restart/).

### CREATE INDEX CONCURRENTLY

```python
"""Build the events date index without blocking ordinary writes.

Revision ID: 12a000000001
Revises: 9a0000000002
"""
from alembic import op

revision = "12a000000001"
down_revision = "9a0000000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
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

```json
{
  "alembic_upgrade_duration_seconds": 0.434,
  "database": {
    "revision": "12a000000001",
    "events": 5,
    "index": {
      "valid": true,
      "ready": true,
      "definition": "CREATE INDEX idx_events_event_date ON public.events USING btree (event_date)"
    }
  },
  "gateway_pods_unchanged": true,
  "5xx_before": 0.0,
  "5xx_after": 0.0,
  "5xx_delta": 0.0,
  "non_health_requests_increase_1m": 1308.0,
  "observer": {
    "duration_seconds": 36.331,
    "checkout_ok": 67,
    "checkout_fail": 0,
    "status_counts": {
      "200": 201
    },
    "http_5xx": 0,
    "network_errors": 0
  }
}
```

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

Normalized numerical 5xx comparison:

```text
before: 0
after:  0
diff: no changes
```

Alembic upgrade took 0.434 seconds including client startup. The events table has five rows, so this does not demonstrate a large-table performance difference. Both indisvalid and indisready were verified. The observer completed 67 checkouts without errors.

Why CONCURRENTLY matters: ordinary CREATE INDEX blocks table writes during the build, but still permits reads. On a ten-million-row table this can stall checkout writes for a long time. CONCURRENTLY permits ordinary inserts, updates, and deletes while building, at the cost of additional scans and work. It must run outside a transaction, hence autocommit_block. IF NOT EXISTS alone does not prove an existing index is valid or has the correct definition. [PostgreSQL documentation](https://www.postgresql.org/docs/17/sql-createindex.html).

### Expand-and-contract design: three migrations and two deploys

1. Migration 1: add nullable scheduled_at without removing event_date.
2. Deploy A: read COALESCE(scheduled_at, event_date); where runtime date writes exist, write both columns. Fully roll out compatible writers before enforcing new constraints.
3. Migration 2: backfill scheduled_at where it is NULL, verify completion, then make it NOT NULL. For a large table, use short batches and separately plan constraint validation and lock acquisition.
4. Deploy B: read and write only scheduled_at; preserve the API response shape. Wait until every Deploy A pod is gone.
5. Migration 3: drop event_date only after Deploy B has fully rolled out.

Migration 3 must follow Deploy B: a remaining Deploy A pod would still reference event_date in COALESCE and fail after the column disappears.

## Bonus — Executed expand-and-contract

The preceding five transitions were executed in order under continuous mixedload. QuickTicket has no runtime INSERT/UPDATE of event dates, so the Deploy A dual-write change was not applicable. Its two SELECTs and ORDER BY used COALESCE. Deploy B reads scheduled_at only and retains the existing JSON date field.

```json
{
  "transitions": [
    {
      "phase": "bonus-m1",
      "5xx_delta": 0.0,
      "checkout_ok": 59,
      "checkout_fail": 0,
      "network_errors": 0
    },
    {
      "phase": "bonus-deploy-a",
      "5xx_delta": 0.0,
      "checkout_ok": 106,
      "checkout_fail": 0,
      "network_errors": 0
    },
    {
      "phase": "bonus-m2",
      "5xx_delta": 0.0,
      "checkout_ok": 58,
      "checkout_fail": 0,
      "network_errors": 0
    },
    {
      "phase": "bonus-deploy-b",
      "5xx_delta": 0.0,
      "checkout_ok": 107,
      "checkout_fail": 0,
      "network_errors": 0
    },
    {
      "phase": "bonus-m3",
      "5xx_delta": 0.0,
      "checkout_ok": 57,
      "checkout_fail": 0,
      "network_errors": 0
    }
  ],
  "observer_total_checkout_ok": 387,
  "observer_total_checkout_fail": 0,
  "baseline_5xx": 0.0,
  "final_5xx": 0.0,
  "final_revision": "12a000000004"
}
```

### Migration 1 — expand

```python
"""Expand events with the nullable scheduled_at column.

Revision ID: 12a000000002
Revises: 12a000000001
"""
from alembic import op
import sqlalchemy as sa

revision = "12a000000002"
down_revision = "12a000000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.drop_column("events", "scheduled_at")
```

### Migration 2 — backfill and NOT NULL

```python
"""Backfill scheduled_at, then require a date.

Revision ID: 12a000000003
Revises: 12a000000002
"""
from alembic import op
import sqlalchemy as sa

revision = "12a000000003"
down_revision = "12a000000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.execute(
        "UPDATE events SET scheduled_at = event_date "
        "WHERE scheduled_at IS NULL"
    )
    op.alter_column(
        "events",
        "scheduled_at",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=False,
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.alter_column(
        "events",
        "scheduled_at",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=True,
    )
```

### Migration 3 — contract

```python
"""Contract events to scheduled_at after Deploy B is fully rolled out.

Revision ID: 12a000000004
Revises: 12a000000003
"""
from alembic import op
import sqlalchemy as sa

revision = "12a000000004"
down_revision = "12a000000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep date indexing after the old column and its index are removed.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_scheduled_at",
            "events",
            ["scheduled_at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.drop_column("events", "event_date")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column(
        "events",
        sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column(
        "events",
        "event_date",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=False,
    )
    # Restore the index required by the preceding migration revision.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.drop_index(
            "idx_events_scheduled_at",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
```

Contract also builds idx_events_scheduled_at concurrently before dropping the old column. PostgreSQL removes idx_events_event_date with that column. Downgrade recreates the old column and its old index.

### Code changes and verified deployments

```diff
--- Deploy A/app/events/main.py
+++ Deploy B/app/events/main.py
@@ -141,10 +141,10 @@
     try:
         cur = conn.cursor()
         cur.execute("""
-            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date, e.total_tickets, e.price_cents,
+            SELECT e.id, e.name, e.venue, e.scheduled_at AS event_date, e.total_tickets, e.price_cents,
                    COALESCE(SUM(o.quantity), 0) as confirmed
             FROM events e LEFT JOIN orders o ON e.id = o.event_id
-            GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
+            GROUP BY e.id ORDER BY e.scheduled_at
         """)
         rows = cur.fetchall()
         cur.close()
@@ -166,7 +166,7 @@
     try:
         cur = conn.cursor()
         cur.execute("""
-            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date, e.total_tickets, e.price_cents,
+            SELECT e.id, e.name, e.venue, e.scheduled_at AS event_date, e.total_tickets, e.price_cents,
                    COALESCE(SUM(o.quantity), 0) as confirmed
             FROM events e LEFT JOIN orders o ON e.id = o.event_id
             WHERE e.id = %s GROUP BY e.id
```

```json
[
  {
    "pod": "events-5c4bb85689-2ttfp",
    "uid": "b997ba8e-b145-4e6e-be25-0eec9e0f7ee0",
    "reported_image": "docker.io/library/quickticket-events:lab12-expand-v1",
    "image_id": "sha256:bd83a644123678bdfa886055ef75027dbc38bb9974fb55563de0a1101447a07b",
    "code_sha256": "00ecabe0c51e941d8804697447ed910a8f7b8d8d840fa62c2edb3ed19940361c",
    "matches_local_code": true
  },
  {
    "pod": "events-5c4bb85689-fwqvg",
    "uid": "7241872b-4b29-456b-9ff7-9b194cf5e3be",
    "reported_image": "docker.io/library/quickticket-events:lab12-expand-v1",
    "image_id": "sha256:bd83a644123678bdfa886055ef75027dbc38bb9974fb55563de0a1101447a07b",
    "code_sha256": "00ecabe0c51e941d8804697447ed910a8f7b8d8d840fa62c2edb3ed19940361c",
    "matches_local_code": true
  }
]
```

```json
[
  {
    "pod": "events-8986b66f6-6xw7k",
    "uid": "fbf640e2-77c2-4108-94f2-c6313454bf38",
    "reported_image": "docker.io/library/quickticket-events:lab12-scheduled-v1",
    "image_id": "sha256:f1fd556fdd9e5035e4958bb94f7770f976beab617f29ff1824c72c17e02ac5a0",
    "code_sha256": "0e617d0e0960dc7f3e9fdf2797481f5ffeb9824b59f9676654b257a55966211d",
    "matches_local_code": true
  },
  {
    "pod": "events-8986b66f6-b9ddd",
    "uid": "a5ed8246-d998-412d-b77f-7300abb4c71e",
    "reported_image": "docker.io/library/quickticket-events:lab12-scheduled-v1",
    "image_id": "sha256:f1fd556fdd9e5035e4958bb94f7770f976beab617f29ff1824c72c17e02ac5a0",
    "code_sha256": "0e617d0e0960dc7f3e9fdf2797481f5ffeb9824b59f9676654b257a55966211d",
    "matches_local_code": true
  }
]
```

Distinct image tags were built and imported before each deployment. Both live pods' /app/main.py SHA-256 hashes matched the corresponding local code. Deploy A API dates and Deploy B API dates were identical.

### Schema before and after

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

```text
 id |       event_date       |      scheduled_at
----+------------------------+------------------------
  1 | 2026-09-15 09:00:00+00 | 2026-09-15 09:00:00+00
  2 | 2026-10-01 18:00:00+00 | 2026-10-01 18:00:00+00
  3 | 2026-11-20 10:00:00+00 | 2026-11-20 10:00:00+00
  4 | 2026-09-22 14:00:00+00 | 2026-09-22 14:00:00+00
  5 | 2026-10-10 10:00:00+00 | 2026-10-10 10:00:00+00
(5 rows)
```

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
    "idx_events_scheduled_at" btree (scheduled_at)
Referenced by:
    TABLE "orders" CONSTRAINT "orders_event_id_fkey" FOREIGN KEY (event_id) REFERENCES events(id)
```

### Zero-error comparison across all five transitions

```text
baseline: 0
final:    0
diff lab12-evidence/bonus-5xx.baseline.txt lab12-evidence/bonus-5xx.final.txt
(no output; exit status 0)
```

Gateway identities remained unchanged throughout the bonus sequence. All five transition observers reported zero HTTP 5xx, zero network errors, and zero failed checkouts; their combined successful checkout count was 387.

### Bonus design answers

The step that would cause 5xx if moved before its prerequisite is Migration 3: dropping event_date while Deploy A or the legacy code still references it. Deploy A also requires Migration 1, and Deploy B requires completed backfill; the tested order satisfied all prerequisites.

A production backfill should keep transactions small. UPDATE locks affected rows, rather than taking an exclusive lock on every row in the table; a huge single transaction still causes contention, WAL volume, replication lag, and long-lived snapshots.

```text
last_id = 0
while True:
    begin_transaction()
    rows = select_next_ids_after(last_id, limit=10000)
    if not rows:
        commit(); break
    update_scheduled_at_from_event_date(rows, only_if_null=True)
    commit()
    last_id = max(rows)
    sleep(0.05)
# Dual-write or a compatibility trigger must cover concurrent changes.
```

Contract downgrade is not sufficient for safe production rollback: it copies the current values once, while Deploy B writers would then continue updating only scheduled_at. The restored old column could become stale, and a new-only INSERT could violate its NOT NULL constraint. Rollback requires a coordinated compatible-writer/trigger strategy, backfill and validation, compatible constraints, and correct deployment order. Every old-code reader must see current data before it is reintroduced. In this lab no runtime date writers exist, but that does not establish safety for a production service with such writers.

## Migration and fresh-bootstrap verification

```json
{
  "legacy_seed_full_upgrade": "passed",
  "full_downgrade_restores_legacy_events_schema": "passed",
  "reupgrade_matches_first_upgrade": "passed",
  "final_seed_matches_migrated_events_schema": "passed",
  "fresh_seed_stamp_then_upgrade_head": "passed",
  "test_database_events": 5,
  "test_database_orders": 0,
  "final_revision": "12a000000004",
  "final_schema": {
    "columns": [
      [
        "email",
        "character varying",
        "YES",
        null
      ],
      [
        "id",
        "integer",
        "NO",
        "nextval('events_id_seq'::regclass)"
      ],
      [
        "name",
        "text",
        "NO",
        null
      ],
      [
        "price_cents",
        "integer",
        "NO",
        null
      ],
      [
        "scheduled_at",
        "timestamp with time zone",
        "NO",
        null
      ],
      [
        "total_tickets",
        "integer",
        "NO",
        null
      ],
      [
        "venue",
        "text",
        "NO",
        null
      ]
    ],
    "indexes": [
      [
        "events_pkey",
        true,
        true,
        "CREATE UNIQUE INDEX events_pkey ON public.events USING btree (id)"
      ],
      [
        "idx_events_scheduled_at",
        true,
        true,
        "CREATE INDEX idx_events_scheduled_at ON public.events USING btree (scheduled_at)"
      ]
    ],
    "events": 5,
    "orders": 0,
    "revision": "12a000000004"
  },
  "live_database_was_not_downgraded": true,
  "live_before": {
    "events": 5,
    "orders": 27988,
    "revision": "12a000000004"
  },
  "live_after": {
    "events": 5,
    "orders": 28006,
    "revision": "12a000000004"
  },
  "temporary_databases_removed": true
}
```

Two isolated temporary databases were created and removed. The legacy seed database passed upgrade to head, downgrade to base, and re-upgrade. The final seed database matched the migrated Events schema and indexes before stamping head; upgrade head then performed no changes. The live quickticket database was never downgraded.

app/seed.sql initializes the final schema for a new empty database, including nullable email and idx_events_scheduled_at. It must not be applied to a populated legacy database. Existing databases follow the staged migration/deployment procedure; fresh databases may stamp head only after schema verification. See migrations/README.

## Final state and cleanup

```json
{
  "status": "healthy",
  "checks": {
    "events": "ok",
    "payments": "ok",
    "notifications": "ok",
    "circuit_payments": "CLOSED"
  }
}
```

```json
{
  "events": 5,
  "orders": 28848,
  "revision": "12a000000004"
}
```

```text
NAME            STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS   VOLUMEATTRIBUTESCLASS   AGE
postgres-data   Bound    pvc-0cdcf641-8447-489b-9064-467e7bed5607   1Gi        RWO            local-path     <unset>                 23h
```

The original PostgreSQL PVC remains Bound to the same volume. Existing orders were retained and live checkout traffic added new orders. Test mixedload and lab12-probe were removed after evidence collection; temporary proxies, port-forwards, and test databases were cleaned up. Database backups and raw experiment evidence remain local and are excluded from Git. At evidence collection, ArgoCD auto-sync was temporarily disabled pending the pushed Lab 12 branch and verified GitOps handoff.

HPA 12.9 was not performed. Single-node k3d cannot demonstrate node-level availability or actual cross-node spreading.
