# Lab 9 — Stateful Services & DB Reliability

## Setup

The Lab 9 database setup started with an empty Postgres schema, so I re-seeded it with `app/seed.sql` as instructed by the lab.

```text
Did not find any relations.
CREATE TABLE
CREATE TABLE
INSERT 0 5
           List of relations
 Schema |  Name  | Type  |    Owner    
--------+--------+-------+-------------
 public | events | table | quickticket
 public | orders | table | quickticket
(2 rows)

 count 
-------
     5
(1 row)

 count 
-------
    11
(1 row)
```

`mixedload` was already running and continued to generate traffic during the migration and backup/restore work.

```text
NAME        READY   UP-TO-DATE   AVAILABLE   AGE
mixedload   2/2     2            2           5d18h
```

The local Alembic client reached the in-cluster Postgres database through a port-forward.

```text
deps ok
events: 5
```

## Task 1 — Migrations & Backup/Restore

### Alembic history

Alembic was initialized in `migrations/`, configured to use `postgresql://quickticket:quickticket@localhost:5432/quickticket`, and baselined against the existing schema before the real migration was created.

```text
dd57cdeb0403 -> bf3aa2d35ec3 (head), add email column to events
<base> -> dd57cdeb0403, baseline - pre-existing schema
```

### Events schema after migration

The migration added a nullable `email` column to `events`.

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

### Migration elapsed time

The migration ran under active `mixedload` traffic.

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade dd57cdeb0403 -> bf3aa2d35ec3, add email column to events
real 2.15
user 1.54
sys 0.23
```

The nullable-column migration completed successfully. It took `2.15s` in this environment.

### Prometheus 5xx before and after migration

The 5xx counter over the last minute was effectively unchanged before and after the migration.

```text
5xx last 1min before successful migration: 3.2726082738383924
5xx last 1min after successful migration: 3.272667778331275
```

This shows that the migration did not introduce a visible additional 5xx spike in the measured window.

### Backup file and pg_restore list

I created a custom-format `pg_dump` backup and verified it with `file` and `pg_restore --list` inside the Postgres pod.

```text
-rw-rw-r-- 1 lima lima 7.2K Oct 10 08:08 /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
;
; Archive created at 2026-10-10 08:08:20 UTC
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
220; 1259 16410 TABLE public alembic_version quickticket
218; 1259 16388 TABLE public events quickticket
217; 1259 16387 SEQUENCE public events_id_seq quickticket
3481; 0 0 SEQUENCE OWNED BY public events_id_seq quickticket
219; 1259 16396 TABLE public orders quickticket
3316; 2604 16391 DEFAULT public events id quickticket
3474; 0 16410 TABLE DATA public alembic_version quickticket
3472; 0 16388 TABLE DATA public events quickticket
3473; 0 16396 TABLE DATA public orders quickticket
3482; 0 0 SEQUENCE SET public events_id_seq quickticket
```

The backup is valid because `pg_restore --list` can read its table-of-contents and shows the expected schema and data entries.

### Data loss and restore

Before dropping `orders`, the database had 5 events and 50 orders.

```text
--- BEFORE DROP COUNTS ---
 events_count 
--------------
            5
(1 row)

 orders_count 
--------------
           50
(1 row)
```

After `DROP TABLE orders CASCADE`, the `orders` table no longer existed and the API returned a gateway error for `/events`.

```text
--- AFTER DROP TABLES ---
               List of relations
 Schema |      Name       | Type  |    Owner    
--------+-----------------+-------+-------------
 public | alembic_version | table | quickticket
 public | events          | table | quickticket
(2 rows)

--- AFTER DROP COUNTS ATTEMPT ---
ERROR:  relation "orders" does not exist
LINE 1: ...unt FROM events; SELECT count(*) AS orders_count FROM orders
                                                                 ^
 events_count 
--------------
            5
(1 row)

command terminated with exit code 1
after_drop_count_status=1

--- API AFTER DROP ---
/events=502
```

After restoring from `/tmp/backup.dump`, both tables were back and the API returned 200 again.

```text
--- AFTER RESTORE COUNTS ---
 events_count 
--------------
            5
(1 row)

 orders_count 
--------------
           50
(1 row)

--- API AFTER RESTORE ---
/events=200
```

### RPO of the current setup

With a single `pg_dump`, the RPO is the time between the backup and the disaster. Any orders written after the dump but before the failure would be outside the backup and could be lost during restore.

In this Task 1 restore, the backup contained 50 orders and the restored database also contained 50 orders, so the observed row gap for this specific restore was 0 orders. To improve the RPO, I would automate frequent backups and, for a production system, use persistent storage plus point-in-time recovery with WAL archiving.

## Task 2 — Disaster Recovery Under Load

### Disaster and recovery timeline

`mixedload` stayed running during the disaster recovery experiment. Before deleting the Postgres pod, the database had 50 orders.

```text
--- BACKUP INFO ---
Backup time      08:08:25
--- BEFORE DISASTER ---
 orders_before 
---------------
            50
(1 row)

healthy at 08:18:17
```

I then force-deleted the Postgres pod and waited for Kubernetes to create a replacement pod.

```text
--- DELETE POSTGRES POD ---
Warning: Immediate deletion does not wait for confirmation that the running resource has been terminated. The resource may continue to run on the cluster indefinitely.
pod "postgres-78489d7f5f-b4tz8" force deleted from default namespace
--- WAIT FOR NEW POSTGRES POD READY ---
pod/postgres-78489d7f5f-5sltz condition met
NEW_POD=postgres-78489d7f5f-5sltz
```

The new Postgres pod was empty.

```text
--- NEW POD TABLES ---
Did not find any relations.
```

I restored the `/tmp/quickticket.dump` backup into the new pod, restarted the `events` deployment to reconnect its database pool, and verified the recovered counts.

```text
--- RESTORE FROM BACKUP ---
--- RESTART EVENTS ---
deployment.apps/events restarted
Waiting for deployment "events" rollout to finish: 1 old replicas are pending termination...
Waiting for deployment "events" rollout to finish: 1 old replicas are pending termination...
deployment "events" successfully rolled out
--- AFTER RESTORE COUNTS ---
 events_after 
--------------
            5
(1 row)

 orders_after 
--------------
           50
(1 row)
```

The measured timeline was:

```text
--- TIMELINE ---
Backup time      08:08:25
Healthy at       08:18:17
Disaster at      08:18:20
New pod ready    08:18:23
Restored         08:19:00
App fully up     08:19:16
RTO_SECONDS=56
POD_READY_SECONDS=3
RESTORE_SECONDS_FROM_KILL=40
RPO_SECONDS=595
```

Actual RTO was `56s`, measured from `08:18:20` when the Postgres pod was deleted to `08:19:16` when the `events` deployment was rolled out and ready again.

Actual RPO as a time window was `595s`, because the backup used for restore was created at `08:08:25` and the disaster happened at `08:18:20`. The observed row gap was `0` orders in this run, because the database had `50` orders before the disaster and `50` orders after restore.

### Prometheus error rate around the incident

The Prometheus 30-second 5xx rate query around the incident returned:

```text
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1791620359.658,"0.7200384425258507"]}]}}
```

This confirms that the database disaster caused visible gateway 5xx errors while the application was recovering.

### Why the new Postgres pod was empty

The replacement Postgres pod was empty because the default `k8s/postgres.yaml` deployment did not mount a PersistentVolumeClaim. Its database files lived on ephemeral pod storage. When Kubernetes replaced the pod, the new container started with a fresh data directory and no `events`, `orders`, or `alembic_version` tables.

To eliminate this failure mode, Postgres needs persistent storage mounted at its data directory. A PVC lets a replacement pod attach the same database volume instead of starting from an empty filesystem. The Bonus Task implements that fix and re-measures the recovery behavior.

## Bonus Task — Persistent Storage + Automated Backup CronJob

### Postgres PVC change

I added persistent storage to Postgres by mounting a `postgres-data` PersistentVolumeClaim at `/var/lib/postgresql/data` and setting `PGDATA` to a subdirectory inside the mounted volume.

```diff
diff --git a/k8s/postgres.yaml b/k8s/postgres.yaml
index 232cbf3..82fd475 100644
--- a/k8s/postgres.yaml
+++ b/k8s/postgres.yaml
@@ -72,6 +72,19 @@ spec:
             - name: POSTGRES_PASSWORD
               value: "quickticket"
 
+            # PGDATA tells PostgreSQL to store its database files in a subdirectory
+            # inside the mounted volume. This avoids conflicts with filesystem
+            # directories such as lost+found that can exist at the root of a volume.
+            - name: PGDATA
+              value: "/var/lib/postgresql/data/pgdata"
+
+          # volumeMounts attaches persistent storage to the PostgreSQL container.
+          # Without this mount, a recreated Pod starts with an empty database.
+          volumeMounts:
+            # Mount the postgres-data PVC at the standard PostgreSQL data root.
+            - name: data
+              mountPath: /var/lib/postgresql/data
+
           # resources declares the requested and maximum CPU/memory for PostgreSQL.
@@ -83,9 +96,39 @@ spec:
               cpu: 200m
               memory: 256Mi
 
+      # volumes declares storage sources available to containers in this Pod.
+      volumes:
+        # The data volume is backed by the postgres-data PersistentVolumeClaim.
+        - name: data
+          persistentVolumeClaim:
+            claimName: postgres-data
+
+---
+
+# This second YAML document defines persistent storage for PostgreSQL.
+# A PersistentVolumeClaim asks Kubernetes for durable storage that can be
+# reattached when the Postgres Pod is recreated.
+apiVersion: v1
+
+# PersistentVolumeClaim is the Kubernetes resource used by Pods to request storage.
+kind: PersistentVolumeClaim
+
+metadata:
+  # This name is referenced by the Deployment volume above.
+  name: postgres-data
+
+spec:
+  # ReadWriteOnce means one node can mount the volume for read/write access.
+  accessModes: [ReadWriteOnce]
+
+  resources:
+    requests:
+      # 1Gi is enough for this lab's small QuickTicket database.
+      storage: 1Gi
+
 ---
 
-# This second YAML document defines the Service for PostgreSQL.
+# This third YAML document defines the Service for PostgreSQL.
```

After applying the manifest, Kubernetes created and bound the PVC.

```text
deployment.apps/postgres configured
persistentvolumeclaim/postgres-data created
service/postgres configured
deployment "postgres" successfully rolled out
NAME            STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS   VOLUMEATTRIBUTESCLASS   AGE
postgres-data   Bound    pvc-bd95b97e-f38f-4339-a8d0-dba3756063ba   1Gi        RWO            local-path     <unset>                 14s
```

The new persistent volume was empty, so I re-seeded it once.

```text
POD=postgres-68466c5ccd-7nssb
postgres SQL ready after 7s
CREATE TABLE
CREATE TABLE
INSERT 0 5
 events_count 
--------------
            5
(1 row)

 orders_count 
--------------
            0
(1 row)
```

### RTO re-measurement with PVC

I repeated the Postgres pod deletion after adding the PVC. This time the replacement pod found the existing tables and did not need `pg_restore`.

```text
--- PVC BEFORE DISASTER ---
 events_before 
---------------
             5
(1 row)

 orders_before 
---------------
            26
(1 row)

healthy at 08:22:18
--- DELETE POSTGRES POD WITH PVC ---
Warning: Immediate deletion does not wait for confirmation that the running resource has been terminated. The resource may continue to run on the cluster indefinitely.
pod "postgres-68466c5ccd-7nssb" force deleted from default namespace
--- WAIT FOR NEW POSTGRES POD READY WITH PVC ---
pod/postgres-68466c5ccd-qvm4d condition met
NEW_POD=postgres-68466c5ccd-qvm4d
--- PVC NEW POD TABLES AND COUNTS ---
           List of relations
 Schema |  Name  | Type  |    Owner    
--------+--------+-------+-------------
 public | events | table | quickticket
 public | orders | table | quickticket
(2 rows)

 events_after_restart 
----------------------
                    5
(1 row)

 orders_after_restart 
----------------------
                   26
(1 row)

--- API CHECK AFTER PVC RESTART ---
/events=200
SMOKE_STATUS=0
--- PVC TIMELINE ---
Healthy at       08:22:18
Disaster at      08:22:20
New pod ready    08:22:24
SQL ready        08:22:27
App check        08:22:35
POD_READY_SECONDS=4
SQL_READY_SECONDS=7
APP_CHECK_SECONDS=15
```

With PVC, SQL was ready after `7s` and the application check returned `/events=200` after `15s`. This was faster than the Task 2 recovery, where the app was fully up after `56s` and required a `pg_restore` plus `events` rollout restart.

### Backup CronJob manifest

The backup CronJob runs every 5 minutes, forbids overlapping jobs, writes custom-format dumps to `/backups`, and keeps only the five newest dumps.

```yaml
# This CronJob creates periodic PostgreSQL backups for QuickTicket.
# It writes custom-format pg_dump files to the postgres-backups PVC and keeps
# only the five newest dumps so backup storage does not grow without bound.
apiVersion: batch/v1
kind: CronJob
metadata:
  name: postgres-backup
spec:
  # Run every five minutes, as required by Lab 9.
  schedule: "*/5 * * * *"

  # Do not start a new backup if the previous backup job is still running.
  concurrencyPolicy: Forbid

  # Keep a small amount of Job history so Kubernetes metadata does not grow forever.
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
                - sh
                - -ec
                - |
                  cd /backups
                  ts=$(date -u +%Y%m%dT%H%M%SZ)
                  dump="quickticket_${ts}.dump"
                  echo "creating ${dump}"
                  pg_dump -Fc -f "${dump}"
                  echo "backup files before retention:"
                  ls -1t quickticket_*.dump
                  old_files=$(ls -1t quickticket_*.dump | tail -n +6 || true)
                  if [ -n "${old_files}" ]; then
                    echo "removing old backups:"
                    echo "${old_files}" | xargs -r rm -v
                  else
                    echo "no old backups to remove"
                  fi
                  echo "backup files after retention:"
                  ls -1t quickticket_*.dump
              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

### Backup rotation proof

The first manual backup succeeded.

```text
cronjob.batch/postgres-backup created
job.batch/manual-1 created
job.batch/manual-1 condition met
creating quickticket_20261010T082330Z.dump
backup files before retention:
quickticket_20261010T082330Z.dump
no old backups to remove
backup files after retention:
quickticket_20261010T082330Z.dump
```

After manual-1 through manual-7, the `manual-7` log showed retention deleting the oldest backup.

```text
--- manual-7 logs ---
creating quickticket_20261010T082434Z.dump
backup files before retention:
quickticket_20261010T082434Z.dump
quickticket_20261010T082426Z.dump
quickticket_20261010T082418Z.dump
quickticket_20261010T082410Z.dump
quickticket_20261010T082401Z.dump
quickticket_20261010T082351Z.dump
removing old backups:
removed 'quickticket_20261010T082351Z.dump'
backup files after retention:
quickticket_20261010T082434Z.dump
quickticket_20261010T082426Z.dump
quickticket_20261010T082418Z.dump
quickticket_20261010T082410Z.dump
quickticket_20261010T082401Z.dump
```

The final `/backups` listing contained exactly five dump files.

```text
--- backups listing ---
total 48
drwxrwxrwx    2 root     root          4096 Oct 10 08:24 .
drwxr-xr-x    1 root     root          4096 Oct 10 08:23 ..
-rw-r--r--    1 root     root          5482 Oct 10 08:24 quickticket_20261010T082401Z.dump
-rw-r--r--    1 root     root          5482 Oct 10 08:24 quickticket_20261010T082410Z.dump
-rw-r--r--    1 root     root          5482 Oct 10 08:24 quickticket_20261010T082418Z.dump
-rw-r--r--    1 root     root          5482 Oct 10 08:24 quickticket_20261010T082426Z.dump
-rw-r--r--    1 root     root          5482 Oct 10 08:24 quickticket_20261010T082434Z.dump
--- backup file count ---
5
```

