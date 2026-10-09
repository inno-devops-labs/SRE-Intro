# Lab 9 — Stateful Services & DB Reliability

## Environment and method

- Local macOS Apple Silicon host; k3d cluster `quickticket`.
- Kubernetes context: `k3d-quickticket`; application namespace: `default`.
- Gateway: Argo Rollouts Rollout with 5 replicas.
- PostgreSQL: `postgres:17-alpine`; observed server/client version 17.11.
- In-cluster Prometheus: `monitoring/deployment/prometheus`.
- Alembic 1.18.4, psycopg2-binary 2.9.11, SQLAlchemy 2.0.49.
- Branch: `feature/lab9`, based on course `upstream/main`.
- All timestamps below are UTC. Host and database timestamps were recorded.
- ArgoCD automatic reconciliation was disabled during the experiments.
- `labs/lab8/mixedload.yaml` supplied two replicas exercising listing,
  reservation and payment. It stayed running during both disaster tests.

On startup, the original PostgreSQL deployment had no PVC and no application
tables. The provided `app/seed.sql` initialized 5 events and 0 orders.
Event 1 capacity was temporarily raised from 100 to 1,000,000 for sustained
checkout traffic. The initial seeded database was backed up separately.

Events was quiesced during database restores to prevent concurrent writes
from invalidating row-count verification. Mixedload stayed running during
this maintenance and therefore observed application failures.

### Initial seeded row counts

```text
events
--------
      5
(1 row)

 orders
--------
      0
(1 row)
```

### Pinned Python environment

```text
alembic==1.18.4
Mako==1.4.3
MarkupSafe==3.0.4
psycopg2-binary==2.9.11
SQLAlchemy==2.0.49
typing_extensions==4.16.0
```

### Traffic baseline

| Timestamp (UTC) | five_xx_last_1m |
|---|---|

| 2026-10-09T19:30:55.978907+00:00 | 0 |

| 2026-10-09T19:31:11.469531+00:00 | 0 |

| 2026-10-09T19:31:27.482592+00:00 | 0 |

| 2026-10-09T19:31:42.778881+00:00 | 0 |

| 2026-10-09T19:31:58.055803+00:00 | 0 |

| 2026-10-09T19:32:13.556923+00:00 | 0 |

| 2026-10-09T19:32:28.846964+00:00 | 0 |



### Baseline health

```json
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

## Task 1 — Migrations and backup/restore

### Alembic initialization and baseline

The baseline revision is intentionally empty because the tables already exist.
It represents the schema created by `app/seed.sql`; it is not a fresh-database
bootstrap migration.

```bash
alembic init migrations
alembic revision --rev-id 9a0000000001 -m "baseline - pre-existing schema"
alembic stamp head
alembic revision --rev-id 9a0000000002 -m "add email column to events"
alembic upgrade head
```

### Baseline stamp output

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running stamp_revision  -> 9a0000000001
```

### Two-revision history

```text
9a0000000001 -> 9a0000000002 (head), Add a nullable email column to events.
<base> -> 9a0000000001, baseline - pre-existing schema
```

### Current revision after migration

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
9a0000000002 (head)
```

### Nullable email migration under live traffic

The migration adds `events.email VARCHAR(255) NULL`.
It avoids a table rewrite, but `ALTER TABLE` still requires an exclusive lock.
A transaction-local 3-second `lock_timeout` bounds waiting for that lock.
Zero observed additional errors in this test is evidence for this execution,
not a guarantee for every table size or workload.

```python
"""Add a nullable email column to events.

Revision ID: 9a0000000002
Revises: 9a0000000001
"""
from alembic import op
import sqlalchemy as sa

revision = "9a0000000002"
down_revision = "9a0000000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No table rewrite, but ALTER TABLE still needs an exclusive lock.
    # Fail promptly rather than waiting indefinitely under live traffic.
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column("events", sa.Column("email", sa.String(255), nullable=True))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.drop_column("events", "email")
```

### Mixedload during migration

```text
NAME        READY   UP-TO-DATE   AVAILABLE   AGE
mixedload   2/2     2            2           5m36s
```

### Migration timestamps

```text
STARTED_AT=2026-10-09T19:36:34Z
FINISHED_AT=2026-10-09T19:36:35Z
```

### Timed upgrade

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 9a0000000001 -> 9a0000000002, Add a nullable email column to events.
real 0,38
user 0,29
sys 0,05
```

The shell reported `real 0,38`, i.e. 0.38 seconds.
The comma is the host locale's decimal separator.

### Schema showing email

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
Referenced by:
    TABLE "orders" CONSTRAINT "orders_event_id_fkey" FOREIGN KEY (event_id) REFERENCES events(id)
```

### Schema and revision assertions

```text
events.email: ('character varying', 255, 'YES')
alembic_version: 9a0000000002
```

### Prometheus immediately before migration

```json
{"timestamp": "2026-10-09T19:36:33.826373+00:00", "rps_by_path": [{"metric": {"path": "/events"}, "value": [1791574594.5, "5.400010910731818"]}, {"metric": {"path": "/events/{id}/reserve"}, "value": [1791574594.5, "5.381835043107481"]}, {"metric": {"path": "/reserve/{id}/pay"}, "value": [1791574594.5, "5.454565952276531"]}, {"metric": {"path": "/health"}, "value": [1791574594.5, "0"]}], "five_xx_last_1m": []}
```

### Prometheus observations after migration

| Timestamp (UTC) | five_xx_last_1m |
|---|---|

| 2026-10-09T19:36:36.002698+00:00 | empty vector |

| 2026-10-09T19:36:51.207711+00:00 | empty vector |

| 2026-10-09T19:37:06.478319+00:00 | empty vector |

| 2026-10-09T19:37:21.805559+00:00 | empty vector |

| 2026-10-09T19:37:37.584304+00:00 | empty vector |

| 2026-10-09T19:37:53.584981+00:00 | empty vector |



The initial traffic baseline reported numeric zero 5xx.
Immediately before and after migration, the filtered 5xx query returned an
empty vector: no matching 5xx series were available. This is preserved as
raw evidence rather than silently presented as a numeric zero.
Traffic remained present on all three checkout paths, and no additional
gateway 5xx was observed by these samples.

Observation query:

```promql
sum(increase(gateway_requests_total{status=~"5.."}[1m]))
```

### Consistent custom-format backup

The backup was created while traffic was running. A host psycopg2
repeatable-read transaction exported a PostgreSQL snapshot; `pg_dump -Fc`
used `--snapshot` while that transaction stayed open. Counts below therefore
describe the backup snapshot, rather than a later live database sample.

```bash
# Snapshot exported using SELECT pg_export_snapshot()
kubectl --context k3d-quickticket -n default exec deployment/postgres -- pg_dump -U quickticket -d quickticket -Fc --snapshot <exported-snapshot> > lab9-evidence/quickticket-task1.dump

kubectl exec deployment/postgres -- pg_restore --list /tmp/lab9-task1.dump
```

### Backup snapshot and exact counts

```json
{
  "snapshot_at": "2026-10-09T19:40:43.049426+00:00",
  "backup_finished_at": "2026-10-09T19:40:44.087296+00:00",
  "events": 5,
  "orders": 3246,
  "bytes": 123999,
  "dump_seconds": 1.034,
  "method": "pg_dump -Fc using an exported repeatable-read snapshot"
}
```

### Backup size

```text
-rw-r--r--  1 esqavator  staff   121K  9 окт.  22:40 lab9-evidence/quickticket-task1.dump
```

### Backup format

```text
lab9-evidence/quickticket-task1.dump: PostgreSQL custom database dump - v1.16-0
```

### Backup TOC

```text
;
; Archive created at 2026-10-09 19:40:43 UTC
;     dbname: quickticket
;     TOC Entries: 18
;     Compression: gzip
;     Dump Version: 1.16-0
;     Format: CUSTOM
;     Integer: 4 bytes
;     Offset: 8 bytes
;     Dumped from database version: 17.11
;     Dumped by pg_dump version: 17.11
;
;
; Selected TOC Entries:
;
220; 1259 16417 TABLE public alembic_version quickticket
218; 1259 16389 TABLE public events quickticket
217; 1259 16388 SEQUENCE public events_id_seq quickticket
3481; 0 0 SEQUENCE OWNED BY public events_id_seq quickticket
219; 1259 16397 TABLE public orders quickticket
3316; 2604 16392 DEFAULT public events id quickticket
3474; 0 16417 TABLE DATA public alembic_version quickticket
3472; 0 16389 TABLE DATA public events quickticket
3473; 0 16397 TABLE DATA public orders quickticket
3482; 0 0 SEQUENCE SET public events_id_seq quickticket
3324; 2606 16421 CONSTRAINT public alembic_version alembic_version_pkc quickticket
3320; 2606 16396 CONSTRAINT public events events_pkey quickticket
3322; 2606 16405 CONSTRAINT public orders orders_pkey quickticket
3325; 2606 16406 FK CONSTRAINT public orders orders_event_id_fkey quickticket
```

### DROP TABLE and verified restore

```bash
kubectl exec deployment/postgres -- psql -U quickticket -d quickticket -c 'DROP TABLE orders CASCADE'

# Observe GET /events through the gateway.
# Quiesce Events while mixedload remains running.
kubectl scale deployment/events --replicas=0

kubectl exec deployment/postgres -- pg_restore -U quickticket -d quickticket --clean --if-exists --exit-on-error --single-transaction /tmp/lab9-task1.dump

# Verify counts before enabling application writes.
kubectl scale deployment/events --replicas=1
kubectl rollout status deployment/events --timeout=90s
```

| State | Events | Orders |
|---|---:|---:|
| Backup snapshot | 5 | 3246 |
| Live sample before DROP | 5 | 3260 |
| After DROP | 5 | Table absent |
| Restored, before application writes | 5 | 3246 |

The live database had more orders than the snapshot because checkout continued
after backup creation. The restored counts exactly matched the snapshot.

```json
{
  "drop_started_at": "2026-10-09T19:40:45.594265+00:00",
  "broken_api": {
    "timestamp": "2026-10-09T19:40:46.035336+00:00",
    "path": "/events",
    "curl_exit": 0,
    "response": "{\"detail\":\"Events service unavailable\"}\nHTTP_STATUS=502 TOTAL_SECONDS=0.007609\n",
    "stderr": ""
  },
  "restore_started_at": "2026-10-09T19:40:47.593882+00:00",
  "restored_at": "2026-10-09T19:40:48.656221+00:00",
  "after_restore_before_writes": {
    "timestamp": "2026-10-09T19:40:49.497800+00:00",
    "events": 5,
    "orders_table_exists": true,
    "orders": 3246
  },
  "app_ready_at": "2026-10-09T19:41:01.237214+00:00",
  "recovered_health": {
    "timestamp": "2026-10-09T19:41:01.236913+00:00",
    "path": "/health",
    "curl_exit": 0,
    "response": "{\"status\":\"healthy\",\"checks\":{\"events\":\"ok\",\"payments\":\"ok\",\"circuit_payments\":\"CLOSED\"}}\nHTTP_STATUS=200 TOTAL_SECONDS=0.113946\n",
    "stderr": ""
  }
}
```

GET `/events` returned 502 after DROP and 200 after restore.
Alembic revision `9a0000000002` was preserved.
The difference between the live pre-DROP sample and backup was
14 orders.

### RPO with a single dump

A single dump recovers only the state visible in its snapshot.
Its recovery-point age grows until a new successful backup is taken;
writes committed after the snapshot are absent after restoring it.
Periodic verified backups reduce the gap. WAL archiving and tested
point-in-time recovery would allow finer recovery points.
Backup storage should also be copied outside the cluster's failure domain.

### Recovery window after DROP

| Timestamp (UTC) | five_xx_last_1m |
|---|---|

| 2026-10-09T19:41:01.413069+00:00 | 64.10121270872912 |

| 2026-10-09T19:41:16.714915+00:00 | 56.34818854912408 |

| 2026-10-09T19:41:32.578847+00:00 | 52.76507143063395 |

| 2026-10-09T19:41:48.476341+00:00 | 46.948889485989476 |

| 2026-10-09T19:42:03.733801+00:00 | 0 |

| 2026-10-09T19:42:19.008640+00:00 | 0 |



## Task 2 — Disaster recovery under load

### Method

Mixedload remained running. The original ephemeral PostgreSQL pod was
force-deleted. The replacement pod became Ready, but its public schema had
zero tables. A new process alone was therefore insufficient to restore service.

Recovery used the host backup, then recreated Events' connection pool.
Writes were quiesced while restoring and counting the recovered rows.

```bash
kubectl delete pod <old-postgres-pod> --grace-period=0 --force --wait=false
kubectl exec <new-postgres-pod> -- psql -U quickticket -d quickticket -c '\dt'
kubectl scale deployment/events --replicas=0
kubectl cp lab9-evidence/quickticket-task1.dump <new-postgres-pod>:/tmp/lab9-recovery.dump
kubectl exec <new-postgres-pod> -- sha256sum /tmp/lab9-recovery.dump
kubectl exec <new-postgres-pod> -- pg_restore -U quickticket -d quickticket --clean --if-exists --exit-on-error --single-transaction /tmp/lab9-recovery.dump
kubectl scale deployment/events --replicas=1
```

### Recorded phases

| Phase | Timestamp (UTC) |
|---|---|
| Disaster invocation | 2026-10-09T19:44:25.996553+00:00 |
| Replacement pod Ready | 2026-10-09T19:44:33.562687+00:00 |
| Database restore completed | 2026-10-09T19:47:55.211825+00:00 |
| Health, listing and checkout verified | 2026-10-09T19:48:04.864862+00:00 |

- New pod Ready: **7.566 seconds**.
- Actual application RTO: **218.868 seconds**.
- Backup snapshot age at disaster: **222.947 seconds**.
- Pre-disaster sample: **4324 orders**.
- Restored snapshot: **3246 orders**.
- Record gap against that sample: **1078 orders**.

The count sample preceded deletion; any later committed orders before deletion
are not included in this record-gap calculation.

### Failed transfer and recovery retry

The first restore failed with `could not read from input file: end of file`.
The streamed pod copy contained 98,304 bytes, while the host backup contained
123,999 bytes. Recovery was interrupted for diagnosis. The backup was copied
again using `kubectl cp`; matching host/pod SHA-256 and a valid TOC were checked
before retrying restore.

The reported RTO includes this failed transfer and manual response time.
It is not the duration of `pg_restore` alone.

### Full disaster summary including integrity checks

```json
{
  "old_pod": "postgres-f5754f487-mhjww",
  "old_uid": "a42c0e06-caa6-4c36-bfb5-31688a57c78b",
  "before_disaster": {
    "events": 5,
    "orders": 4324,
    "database_time": "2026-10-09T19:44:19.792542+00:00"
  },
  "before_count_observed_at": "2026-10-09T19:44:19.814240+00:00",
  "disaster_at": "2026-10-09T19:44:25.996553+00:00",
  "delete_command_finished_at": "2026-10-09T19:44:26.131342+00:00",
  "new_pod": "postgres-f5754f487-r677d",
  "new_pod_ready_at": "2026-10-09T19:44:33.562687+00:00",
  "new_pod_ready_seconds": 7.566,
  "new_database_tables": "",
  "new_database_public_table_count": 0,
  "broken_api": {
    "timestamp": "2026-10-09T19:44:36.999528+00:00",
    "path": "/events",
    "curl_exit": 0,
    "response": "{\"detail\":\"Events service unavailable\"}\nHTTP_STATUS=502 TOTAL_SECONDS=3.097914\n",
    "stderr": ""
  },
  "restore_started_at": "2026-10-09T19:44:39.094770+00:00",
  "restore_retry_at": "2026-10-09T19:47:54.531306+00:00",
  "first_restore_error": "pg_restore: could not read from input file: end of file",
  "remote_file_before_retry": "-rw-r--r--    1 root     root         98304 Oct  9 19:44 /tmp/lab9-recovery.dump\nd41bfd2695d04f743cbd45649e1384bb82484e91fdb5d85cf35ad5bdcf3b6ae7  /tmp/lab9-recovery.dump\n",
  "backup_integrity": {
    "host_bytes": 123999,
    "host_sha256": "6d26803699433132c5d2711d89b3649ac5f3e187c7afb80042ccd88bdeb412f3",
    "pod_sha256": "6d26803699433132c5d2711d89b3649ac5f3e187c7afb80042ccd88bdeb412f3",
    "verified_at": "2026-10-09T19:47:54.843432+00:00"
  },
  "restored_at": "2026-10-09T19:47:55.211825+00:00",
  "after_restore_before_writes": {
    "events": 5,
    "orders": 3246,
    "alembic_revision": "9a0000000002"
  },
  "orders_lost_from_pre_disaster_sample": 1078,
  "backup_age_at_disaster_seconds": 222.947,
  "app_ready_at": "2026-10-09T19:48:04.864862+00:00",
  "rto_seconds": 218.868,
  "measurement_scope": "Observed recovery includes the failed backup transfer, manual diagnosis and retry. Order loss uses the pre-disaster count sample; subsequent writes before deletion are not included."
}
```

### Error-rate samples around the incident

```promql
sum(rate(gateway_requests_total{status=~"5.."}[30s]))
```

| Timestamp (UTC) | five_xx_rps_30s | total_rps_30s |
|---|---|---|

| 2026-10-09T19:44:19.995735+00:00 | 0 | 15.523860173816926 |

| 2026-10-09T19:44:25.635680+00:00 | 0 | 15.324021879655746 |

| 2026-10-09T19:44:31.485028+00:00 | 0.04001120313687833 | 13.5637514456697 |

| 2026-10-09T19:44:36.836601+00:00 | 0.08002240627375665 | 10.483052104144798 |



These are 5xx requests per second and total requests per second,
not an error ratio. Sampling stopped when the first restore failed.
The table has a coverage gap during manual recovery; no values are
interpolated across it.

### Samples after the recovery retry

| Timestamp (UTC) | five_xx_last_1m |
|---|---|

| 2026-10-09T19:48:04.918402+00:00 | 255.29002435591119 |

| 2026-10-09T19:48:20.570366+00:00 | 175.63449920745242 |

| 2026-10-09T19:48:35.854310+00:00 | 106.90944798365253 |

| 2026-10-09T19:48:51.191533+00:00 | 41.45260174737318 |

| 2026-10-09T19:49:06.570517+00:00 | 0 |

| 2026-10-09T19:49:21.878115+00:00 | 0 |



### Why the replacement database was empty

The original deployment had no data-volume mount or PVC.
PostgreSQL data lived in the container filesystem, so a replacement pod
initialized a fresh database. A PVC mounted at the PostgreSQL data directory
eliminates this pod-replacement data-loss mode.

A local-path PVC still shares the local cluster/node failure domain.
It does not replace independent backups or provide PostgreSQL high availability.

## Bonus — Persistent storage and automated backups

### PVC and safe deployment strategy

`postgres-data` requests 1 GiB with ReadWriteOnce access.
`PGDATA` points to a subdirectory of the mounted volume.
The single-instance deployment uses `Recreate` to avoid overlapping old/new
PostgreSQL pods writing the same data directory during a deployment update.

Before changing storage, a fresh full backup was taken with Events quiesced.
The backup was verified and restored onto the fresh PVC. Existing orders,
the email column and Alembic version were preserved rather than replaced
with seed data.

### Fresh backup before storage transfer

```json
{
  "events": 5,
  "orders": 4571,
  "revision": "9a0000000002",
  "backup_finished_at": "2026-10-09T19:52:07.208503+00:00",
  "bytes": 172029,
  "sha256": "362a043ba1daeeb011cb84381e2683be292b0151f5809a75b0964e15554a4f80",
  "method": "Full backup while Events is scaled to zero"
}
```

### PostgreSQL manifest diff

```diff
--- lab9-evidence/36-postgres-original.yaml	2026-10-09 22:52:04
+++ k8s/postgres.yaml	2026-10-09 22:52:08
@@ -1,3 +1,14 @@
+apiVersion: v1
+kind: PersistentVolumeClaim
+metadata:
+  name: postgres-data
+spec:
+  accessModes:
+    - ReadWriteOnce
+  resources:
+    requests:
+      storage: 1Gi
+---
 apiVersion: apps/v1
 kind: Deployment
 metadata:
@@ -6,6 +17,8 @@
     app: postgres
 spec:
   replicas: 1
+  strategy:
+    type: Recreate
   selector:
     matchLabels:
       app: postgres
@@ -22,29 +35,26 @@
               containerPort: 5432
           env:
             - name: POSTGRES_DB
-              value: "quickticket"
+              value: quickticket
             - name: POSTGRES_USER
-              value: "quickticket"
+              value: quickticket
             - name: POSTGRES_PASSWORD
-              value: "quickticket"
+              value: quickticket
+            - name: PGDATA
+              value: /var/lib/postgresql/data/pgdata
+          volumeMounts:
+            - name: data
+              mountPath: /var/lib/postgresql/data
           readinessProbe:
             exec:
-              command:
-                - sh
-                - -c
-                - pg_isready -U quickticket -d quickticket
+              command: ["sh", "-c", "pg_isready -U quickticket -d quickticket"]
             initialDelaySeconds: 5
             periodSeconds: 5
-            failureThreshold: 3
           livenessProbe:
             exec:
-              command:
-                - sh
-                - -c
-                - pg_isready -U quickticket -d quickticket
+              command: ["sh", "-c", "pg_isready -U quickticket -d quickticket"]
             initialDelaySeconds: 15
             periodSeconds: 10
-            failureThreshold: 3
           resources:
             requests:
               cpu: 50m
@@ -52,6 +62,10 @@
             limits:
               cpu: 200m
               memory: 256Mi
+      volumes:
+        - name: data
+          persistentVolumeClaim:
+            claimName: postgres-data
 ---
 apiVersion: v1
 kind: Service
@@ -60,7 +74,6 @@
   labels:
     app: postgres
 spec:
-  type: ClusterIP
   selector:
     app: postgres
   ports:
```

### Bound data PVC

```text
NAME            STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS   VOLUMEATTRIBUTESCLASS   AGE
postgres-data   Bound    pvc-0cdcf641-8447-489b-9064-467e7bed5607   1Gi        RWO            local-path     <unset>                 16s
```

### Verified restore onto PVC

```text
{
  "events": 5,
  "orders": 4571,
  "revision": "9a0000000002"
}
Counts and Alembic revision preserved exactly.
```

### Pod replacement with persistent data

```bash
kubectl delete pod <postgres-pod> --wait=false
# Observe a replacement UID becoming Ready.
# Verify all previously sampled order IDs remain.
kubectl rollout restart deployment/events
kubectl rollout status deployment/events --timeout=90s
# Verify health, listing, reservation and payment through gateway.
```

### PVC restart measurements

```json
{
  "before_orders_sample_at": "2026-10-09T20:05:19.518300+00:00",
  "orders_before": 8756,
  "events_before": 5,
  "old_pod": "postgres-698c969578-td8sg",
  "old_uid": "d271771f-8a68-4c67-af1b-9b646e90a12a",
  "deletion_method": "Normal pod deletion; graceful termination, no force deletion",
  "disaster_at": "2026-10-09T20:05:19.866415+00:00",
  "new_pod": "postgres-698c969578-cskn4",
  "new_pod_ready_at": "2026-10-09T20:05:31.450548+00:00",
  "new_pod_ready_seconds": 11.584,
  "orders_after_db_ready": 8758,
  "preexisting_orders_missing": 0,
  "events_after": 5,
  "alembic_revision": "9a0000000002",
  "restore_used": false,
  "app_ready_at": "2026-10-09T20:05:41.004705+00:00",
  "rto_seconds": 21.138,
  "previous_observed_rto_seconds": 218.868,
  "comparison_limits": "Previous RTO includes a failed backup transfer and manual retry. The PVC test uses graceful deletion rather than force deletion. This comparison demonstrates the observed recovery procedures, not an isolated performance benchmark of storage."
}
```

| Measurement | Ephemeral storage recovery | PVC recovery |
|---|---:|---:|
| Replacement pod Ready | 7.566 s | 11.584 s |
| Application RTO | 218.868 s | 21.138 s |
| pg_restore required | Yes | No |
| Previously sampled orders missing | 1078 | 0 |

The PVC test sampled 8756 order IDs before deletion.
After the new database pod was Ready there were 8758 orders,
and every previously sampled ID still existed.

The ephemeral test used force deletion and included a failed transfer/manual
retry. The PVC test used graceful deletion. These observed procedures show
the value of persistence, but are not an isolated storage performance benchmark.

### PVC test error-rate samples

| Timestamp (UTC) | five_xx_rps_30s | total_rps_30s |
|---|---|---|

| 2026-10-09T20:05:19.672715+00:00 | 0 | 16.319622415103396 |

| 2026-10-09T20:05:20.014877+00:00 | 0 | 16.319622415103396 |

| 2026-10-09T20:05:22.444990+00:00 | 0.08 | 15.120000000000001 |

| 2026-10-09T20:05:25.637813+00:00 | 0.08000320025602048 | 14.080678495246175 |

| 2026-10-09T20:05:28.378820+00:00 | 0.08000160032001792 | 11.319796828096475 |

| 2026-10-09T20:05:31.953221+00:00 | 0.1599968001279949 | 10.039678412863486 |

| 2026-10-09T20:05:41.005653+00:00 | 2.0800192019200305 | 5.558129272554758 |

| 2026-10-09T20:05:56.661497+00:00 | 2.120100805312315 | 12.120576030337796 |

| 2026-10-09T20:06:12.601702+00:00 | 0 | 16.360496029185914 |

| 2026-10-09T20:06:28.477915+00:00 | 0 | 16.3592784534359 |

| 2026-10-09T20:06:43.837377+00:00 | 0 | 16.15988800447982 |

| 2026-10-09T20:06:59.167722+00:00 | 0 | 16.560283264197675 |



### Student-written backup CronJob

The CronJob runs every five minutes in UTC with `concurrencyPolicy: Forbid`.
It creates custom-format dumps, validates each TOC, publishes the completed
file, and only then deletes backups beyond the five newest.

The seven manual jobs were executed sequentially while the schedule was
suspended to prevent scheduled jobs from affecting the retention test.
The schedule was enabled afterward.

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: postgres-backup
spec:
  schedule: "*/5 * * * *"
  timeZone: Etc/UTC
  suspend: false
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 2
      activeDeadlineSeconds: 120
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: backup
              image: postgres:17-alpine
              env:
                - name: PGHOST
                  value: postgres
                - name: PGUSER
                  value: quickticket
                - name: PGDATABASE
                  value: quickticket
                - name: PGPASSWORD
                  value: quickticket
                - name: PGCONNECT_TIMEOUT
                  value: "10"
              command:
                - sh
                - -ec
                - |
                  cd /backups
                  stamp=$(date -u +%Y%m%dT%H%M%SZ)
                  final="quickticket_${stamp}.dump"
                  temporary=".${final}.${HOSTNAME}.partial"
                  trap 'rm -f "$temporary"' EXIT HUP INT TERM

                  # Refuse to overwrite another backup with the same timestamp.
                  test ! -e "$final"
                  echo "Backup started at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
                  pg_dump -Fc -f "$temporary"
                  test -s "$temporary"
                  pg_restore --list "$temporary" > /dev/null
                  mv "$temporary" "$final"
                  echo "Published $final ($(wc -c < "$final") bytes)"

                  # Publish a valid dump before deleting any older backups.
                  ls -1t quickticket_*.dump | tail -n +6 |
                    while IFS= read -r old; do
                      rm -v -- "$old"
                    done
                  echo "Retained backups:"
                  ls -1t quickticket_*.dump
              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

### Manual run 7 and retention removal log

```text
RUN_STARTED_AT=2026-10-09T20:12:32Z
job.batch/lab9-manual-7 created
job.batch/lab9-manual-7 condition met
Backup started at 2026-10-09T20:12:32Z
Published quickticket_20261009T201232Z.dump (408054 bytes)
removed 'quickticket_20261009T201208Z.dump'
Retained backups:
quickticket_20261009T201232Z.dump
quickticket_20261009T201228Z.dump
quickticket_20261009T201223Z.dump
quickticket_20261009T201219Z.dump
quickticket_20261009T201214Z.dump
```

### Exactly five files after seven manual runs

```text
total 2000
drwxrwxrwx    2 root     root          4096 Oct  9 20:12 .
drwxr-xr-x    1 root     root          4096 Oct  9 20:12 ..
-rw-r--r--    1 root     root        404258 Oct  9 20:12 quickticket_20261009T201214Z.dump
-rw-r--r--    1 root     root        405305 Oct  9 20:12 quickticket_20261009T201219Z.dump
-rw-r--r--    1 root     root        406283 Oct  9 20:12 quickticket_20261009T201223Z.dump
-rw-r--r--    1 root     root        407223 Oct  9 20:12 quickticket_20261009T201228Z.dump
-rw-r--r--    1 root     root        408054 Oct  9 20:12 quickticket_20261009T201232Z.dump
```

### Retention verification

```json
{
  "verified_at": "2026-10-09T20:12:37.641522+00:00",
  "manual_runs": 7,
  "published_files": [
    "quickticket_20261009T201203Z.dump",
    "quickticket_20261009T201208Z.dump",
    "quickticket_20261009T201214Z.dump",
    "quickticket_20261009T201219Z.dump",
    "quickticket_20261009T201223Z.dump",
    "quickticket_20261009T201228Z.dump",
    "quickticket_20261009T201232Z.dump"
  ],
  "retained_files": [
    "quickticket_20261009T201214Z.dump",
    "quickticket_20261009T201219Z.dump",
    "quickticket_20261009T201223Z.dump",
    "quickticket_20261009T201228Z.dump",
    "quickticket_20261009T201232Z.dump"
  ],
  "retained_count": 5,
  "exactly_last_five_retained": true,
  "manual_7_removal_logged": true
}
```

A later dump, `quickticket_20261009T201501Z.dump`, was also found during
cleanup and copied to the host with matching SHA-256. This is separate from
the exactly-five-files assertion immediately after the seven manual runs.

### Latest backup saved before cleanup

```json
{
  "source_file": "/backups/quickticket_20261009T201501Z.dump",
  "host_file": "lab9-evidence/quickticket-latest-cronjob.dump",
  "bytes": 437890,
  "sha256": "0da167ad249b4e8a7d6aa837c6716a9d3a2cf35a14664228d5681a39fad872a9",
  "checksum_verified": true
}
```

### Tradeoffs

The PVC preserves data across pod replacement, but the database remains a
single instance and local storage remains vulnerable to node/cluster loss.
Five-minute dumps limit recovery points to successful snapshots and do not
provide point-in-time recovery. Keeping five files bounds storage use, but
offers a short history; delayed detection of corruption can exhaust it.
TOC validation verifies archive readability, while full restore drills remain
necessary to verify recovery.

## Cleanup and final state

### Verified cleanup and application state

```json
{
  "timestamp": "2026-10-09T20:16:07.132693+00:00",
  "database": {
    "events": 5,
    "orders": 11999,
    "alembic_revision": "9a0000000002",
    "event1_test_inventory": 1000000,
    "email_nullable": "YES"
  },
  "health": {
    "status": "healthy",
    "checks": {
      "events": "ok",
      "payments": "ok",
      "circuit_payments": "CLOSED"
    }
  },
  "postgres_data_pvc_retained": true,
  "experiment_resources_removed": true,
  "host_backups_retained": true,
  "argocd_auto_sync": "Still disabled pending Lab 9 push and source update",
  "inventory_note": "Event 1 retains the 1,000,000-ticket load-test fixture. Existing orders and the migrated schema are preserved."
}
```

Mixedload, backup CronJob, manual jobs, backup-inspector and its backup PVC
were removed. Host dump files were retained outside the submission.
The PostgreSQL data PVC was deliberately retained.

Event 1 keeps the 1,000,000-ticket test fixture so the preserved orders do
not exhaust the original 100-ticket inventory. The final database retains
the migrated schema and experiment-generated orders.

The PostgreSQL port-forward was stopped. ArgoCD auto-sync remains disabled
until the pushed Lab 9 branch can supply the persistent PostgreSQL manifest.
The intended GitOps handoff uses `feature/lab9`, path `k8s`, and excludes
`backup-cronjob.yaml` from automatic sync because its temporary backup PVC
was removed during cleanup. To run backups again, apply
`labs/lab9/backup-storage.yaml` before `k8s/backup-cronjob.yaml`.

Supporting QuickTicket manifests were copied unchanged from Lab 7 so that
the Lab 9 GitOps source remains a complete application configuration.
Previous lab reports and binary backups are not part of this submission.

## Completion

- [x] Task 1 — Alembic migration under load and verified backup/restore.
- [x] Task 2 — timed disaster recovery, observed empty database and RPO gap.
- [x] Bonus — persistent data, repeat recovery test and seven-run retention proof.
