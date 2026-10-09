# Lab 9 — Stateful Services & DB Reliability

**Student:** Gleb Shvetsov

**GitHub:** `L10nff`

**Working branch:** `feature/lab9`

## Environment and safety controls

The experiment used the QuickTicket k3d cluster and the lightweight in-cluster
Prometheus from Lab 7. Grafana, the operator-managed monitoring stack, and Argo
CD workloads were scaled to zero during the experiment to reduce local resource
usage. Argo CD automated synchronization was disabled before runtime changes.

Only one Gateway replica and one mixed-load replica were required for the
database tests. The load generator was scaled to zero immediately after each
measurement.

The original database contained five events and 4,228 orders. Event 1 already
had more confirmed orders than its original ticket limit, so its capacity was
temporarily raised while generating new checkout traffic. This did not change
the schema behavior being tested.

## Task 1 — Migrations and Backup/Restore

### 1. Alembic initialization and baseline

Alembic was initialized in `migrations/`, and `alembic.ini` points to the
PostgreSQL service through the local port-forward used during the exercise.
The existing schema was represented by an empty baseline revision and stamped
as already applied before the real migration was created.

Commands:

```bash
alembic init migrations
alembic revision -m "baseline - pre-existing schema"
alembic stamp head
alembic revision -m "add email column to events"
```

History:

```text
7a85fcd6631b -> a5400e67ce10 (head), add email column to events
<base> -> 7a85fcd6631b, baseline - pre-existing schema
```

Verbose revision evidence:

```text
Rev: a5400e67ce10 (head)
Parent: 7a85fcd6631b
Path: migrations/versions/a5400e67ce10_add_email_column_to_events.py

    add email column to events

Rev: 7a85fcd6631b
Parent: <base>
Path: migrations/versions/7a85fcd6631b_baseline_pre_existing_schema.py

    baseline - pre-existing schema
```

The migration adds a nullable column, avoiding a rewrite of existing rows:

```python
def upgrade() -> None:
    """Add an optional contact email without rewriting existing rows."""
    op.add_column(
        "events",
        sa.Column("email", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    """Remove the optional contact email."""
    op.drop_column("events", "email")
```

### 2. Migration under live traffic

One `mixedload` replica continuously exercised event listing, reservation, and
payment paths. During the initial 65-second warm-up, the order count increased
from 4,228 to 4,418, proving that write traffic was active.

Timeline and duration:

```text
load_started_utc=2026-10-09T10:24:49Z
migration_test_started_utc=2026-10-09T10:26:16Z
migration_upgrade_started_utc=2026-10-09T10:26:16Z
migration_upgrade_completed_utc=2026-10-09T10:26:17Z
migration_test_completed_utc=2026-10-09T10:26:29Z

real 0.92
user 0.48
sys 0.12
```

Prometheus showed no Gateway 5xx increase:

```text
5xx_last_1min_before: 0
5xx_last_1min_after: 0
```

Traffic continued throughout the migration:

```text
orders_after_warmup=4418
orders_after_migration=4500
```

Alembic current revision after the upgrade:

```text
a5400e67ce10 (head)
```

The resulting PostgreSQL schema included the new column:

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

### 3. Validated pg_dump backup

The custom-format backup was taken while traffic was active:

```text
backup_started_utc=2026-10-09T10:26:43Z
events=5
orders=4537
backup_completed_utc=2026-10-09T10:26:43Z
-rw-r--r--  1 glebshvetsov  wheel  169K Oct  9 13:26 /tmp/quickticket-lab9.dump
/tmp/quickticket-lab9.dump: PostgreSQL custom database dump - v1.16-0
sha256=a80b69228559db1cac0ac724c02bc91d0e483e1d48abc4e75b2e5d8d43966f8a
```

The archive TOC confirmed that schema, data, and Alembic state were included:

```text
; Archive created at 2026-10-09 10:26:43 UTC
;     dbname: quickticket
;     TOC Entries: 18
;     Compression: gzip
;     Dump Version: 1.16-0
;     Format: CUSTOM
;     Dumped from database version: 17.11
;     Dumped by pg_dump version: 17.11
;
220; 1259 24604 TABLE public alembic_version quickticket
218; 1259 16390 TABLE public events quickticket
217; 1259 16389 SEQUENCE public events_id_seq quickticket
3481; 0 0 SEQUENCE OWNED BY public events_id_seq quickticket
219; 1259 16398 TABLE public orders quickticket
3316; 2604 16393 DEFAULT public events id quickticket
3474; 0 24604 TABLE DATA public alembic_version quickticket
3472; 0 16390 TABLE DATA public events quickticket
3473; 0 16398 TABLE DATA public orders quickticket
3482; 0 0 SEQUENCE SET public events_id_seq quickticket
```

### 4. DROP TABLE and restore

Counts immediately before the simulated logical data loss:

```text
events=5
orders=4603
```

After `DROP TABLE orders CASCADE`:

```text
events=5
orders_table=NULL
GET /events: HTTP 502
```

Restore command:

```bash
pg_restore -U quickticket -d quickticket \
  --clean --if-exists --exit-on-error /tmp/backup.dump
```

Restored result:

```text
restore_started_utc=2026-10-09T10:27:13Z
restore_completed_utc=2026-10-09T10:27:14Z
events=5
orders=4538
GET /events: HTTP 200
```

The dump contained 4,537 orders. One request that was already in flight crossed
the load-generator scale-down boundary, so the observed post-restore count was
4,538. The important result is that the dropped table, its constraints, its
data, and the API path were restored successfully.

### 5. RPO answer for a single dump

The RPO of a single `pg_dump` is the time between the snapshot and the
disaster. Every committed write after that snapshot may be lost. The measured
disaster below occurred 160 seconds after the dump.

Periodic dumps reduce the maximum window to the backup interval; the bonus
CronJob reduces it to approximately five minutes. For a stricter production
RPO, I would also use continuous WAL archiving with point-in-time recovery and
a replicated PostgreSQL topology. A PVC improves restart availability but is
not a replacement for independent backups.

## Task 2 — Disaster Recovery Under Load

### 1. Scenario preparation

The cluster inherited a `postgres-data` PVC from an earlier lab. To reproduce
the Lab 9 no-PVC failure mode without risking that volume, the runtime
Deployment was temporarily changed to an `emptyDir` after the validated dump
was available. The original PVC was not deleted or modified.

The first Postgres pod created with `emptyDir` confirmed the expected state:

```text
ephemeral_postgres_pod=postgres-5f49b5774f-k8tj7
tables_before_initial_restore:
Did not find any relations.
```

The validated backup was restored once to prepare the experiment, Events was
reconnected, and `mixedload` remained active during the actual pod deletion.

### 2. Disaster timeline

```text
healthy_before_disaster_utc=2026-10-09T10:29:23Z
postgres_pod_before_disaster=postgres-5f49b5774f-k8tj7
orders_before_disaster=4710

disaster_started_utc=2026-10-09T10:29:23Z
new_pod_ready_utc=2026-10-09T10:29:41Z
new_postgres_pod=postgres-5f49b5774f-jjnkr
seconds_to_new_pod_ready=18.89

tables_in_new_ephemeral_pod:
Did not find any relations.

database_restored_utc=2026-10-09T10:29:42Z
application_ready_utc=2026-10-09T10:29:55Z
gateway_health_http_code=200
```

### 3. Measured RTO and RPO

```text
rto_seconds=32.93
backup_completed_utc=2026-10-09T10:26:43Z
rpo_backup_age_seconds=160.0
orders_before_disaster=4710
orders_after_restore=4583
rpo_gap_rows=127
gateway_5xx_rps_30s=0.7600304012160486
```

The backup itself contained 4,537 orders. While the application was recovering,
the continuously running load generator created some new orders after the
restore, so the final comparison at application readiness showed 127 missing
rows rather than the full snapshot delta. This is the actual user-visible row
gap measured at recovery completion.

### 4. Why the new Postgres pod was empty

An `emptyDir` belongs to the lifetime of one pod. Deleting the pod deletes that
storage, and a replacement pod initializes a new PostgreSQL data directory.
Kubernetes recreates the process but cannot recreate the database records.

This failure mode is eliminated by mounting a PersistentVolumeClaim at the
PostgreSQL data directory and setting `PGDATA` to a dedicated subdirectory.
Independent scheduled backups are still necessary for logical corruption,
accidental deletion, or loss of the volume itself.

## Bonus Task — Persistent Storage and Automated Backups

### 1. Persistent Postgres configuration

The committed manifest adds the persistent claim, a dedicated `PGDATA`
subdirectory, and the corresponding mount:

```diff
+apiVersion: v1
+kind: PersistentVolumeClaim
+metadata:
+  name: postgres-data
+spec:
+  accessModes:
+    - ReadWriteOnce
+  storageClassName: local-path
+  resources:
+    requests:
+      storage: 1Gi
---
 spec:
   template:
     spec:
       containers:
         - name: postgres
+          env:
+            - name: PGDATA
+              value: /var/lib/postgresql/data/pgdata
+          volumeMounts:
+            - name: postgres-data
+              mountPath: /var/lib/postgresql/data
+      volumes:
+        - name: postgres-data
+          persistentVolumeClaim:
+            claimName: postgres-data
```

Because the dedicated `pgdata` subdirectory was new, the validated dump was
restored into it once. Subsequent pod recreation required no restore.

### 2. RTO after adding persistent storage

```text
pvc_disaster_started_utc=2026-10-09T10:31:47Z
postgres_pod_before_restart=postgres-7ddf6b786-kkkzg
events_before_restart=5
orders_before_restart=4538

pvc_new_pod_ready_utc=2026-10-09T10:31:50Z
postgres_pod_after_restart=postgres-7ddf6b786-lgp8c
pvc_pod_ready_seconds=2.82
events_after_restart=5
orders_after_restart=4538

pvc_application_ready_utc=2026-10-09T10:31:52Z
pvc_rto_seconds=4.79
rpo_gap_rows=0
pg_restore_required=no
events_restart_required=no
pvc_disaster_result=success
```

The measured RTO improved from 32.93 seconds to 4.79 seconds, an improvement of
approximately 85%. All 4,538 orders survived, the RPO gap was zero, and neither
`pg_restore` nor an Events restart was required.

### 3. Automated backup CronJob

The complete committed CronJob is:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: postgres-backup
spec:
  schedule: "*/5 * * * *"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: pg-dump
              image: postgres:17-alpine
              imagePullPolicy: IfNotPresent
              env:
                - name: PGHOST
                  value: postgres
                - name: PGUSER
                  value: quickticket
                - name: PGDATABASE
                  value: quickticket
                - name: PGPASSWORD
                  value: quickticket
              command:
                - /bin/sh
                - -ec
              args:
                - |
                  cd /backups
                  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
                  output="quickticket_${timestamp}.dump"
                  pg_dump -Fc -f "$output"
                  echo "created $output"

                  ls -1t quickticket_*.dump | tail -n +6 | while IFS= read -r old; do
                    [ -n "$old" ] || continue
                    rm "$old"
                    echo "removed $old"
                  done

                  retained="$(find . -maxdepth 1 -name 'quickticket_*.dump' | wc -l | tr -d ' ')"
                  echo "retained_count=$retained"
              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

### 4. Seven-run retention verification

Jobs were created serially from the CronJob so each backup completed before the
next began. The sixth run removed the first dump, and the seventh removed the
second dump.

`lab9-manual-7` logs:

```text
created quickticket_20261009T103301Z.dump
removed quickticket_20261009T103243Z.dump
retained_count=5
```

Final backup directory:

```text
total 868K
drwxrwxrwx  2 root root   4.0K Oct  9 10:33 .
drwxr-xr-x  1 root root   4.0K Oct  9 10:32 ..
-rw-r--r--  1 root root 169.4K Oct  9 10:32 quickticket_20261009T103247Z.dump
-rw-r--r--  1 root root 169.4K Oct  9 10:32 quickticket_20261009T103251Z.dump
-rw-r--r--  1 root root 169.4K Oct  9 10:32 quickticket_20261009T103254Z.dump
-rw-r--r--  1 root root 169.4K Oct  9 10:32 quickticket_20261009T103258Z.dump
-rw-r--r--  1 root root 169.4K Oct  9 10:33 quickticket_20261009T103301Z.dump
```

Verification result:

```text
final_backup_file_count=5
cronjob_retention_result=success
```

The trade-off is additional persistent storage and backup-management overhead
in exchange for a bounded backup window and substantially faster pod recovery.

## Final verification

- Two Alembic revisions exist: baseline and nullable email migration.
- The migration completed under write traffic in 0.92 seconds.
- Gateway 5xx count remained zero during the migration.
- The custom-format dump is non-empty and has a valid `pg_restore` TOC.
- Dropping `orders` broke the API, and restoring the dump returned it to HTTP 200.
- The no-PVC scenario produced an empty replacement database and a 32.93-second RTO.
- Persistent storage reduced the measured RTO to 4.79 seconds with zero lost rows.
- Seven backup jobs left exactly the five newest dump files.

## Conclusion

The lab demonstrated that a safe nullable migration can run under live traffic
without increasing errors, while process recreation alone cannot protect
stateful data. A validated logical backup provided recoverability but exposed a
non-zero RPO and a longer manual RTO. Persistent storage removed the pod-level
data-loss failure mode, and the scheduled custom-format backups added an
independent recovery path with bounded retention.
