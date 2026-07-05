# Lab 9 - Stateful Services & DB Reliability

## Task 1 - Migrations and Backup/Restore

### Alembic history

```text
$ .venv/bin/alembic history
4c99d2b4cc90 -> 348211cb15a0 (head), add email column to events
<base> -> 4c99d2b4cc90, baseline - pre-existing schema
```

### Events schema after migration

```text
$ kubectl exec -i deploy/postgres -- psql -U quickticket -d quickticket -c '\d events'
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

### Timed migration under load

```text
$ /usr/bin/time -p .venv/bin/alembic upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 4c99d2b4cc90 -> 348211cb15a0, add email column to events
real 0.33
user 0.29
sys 0.01
```

The migration was intentionally small and backwards-compatible: it only adds a nullable `email` column. That is why it completed quickly and did not require stopping traffic.

Prometheus 5xx before migration:

```text
{"status":"success","data":{"resultType":"vector","result":[]}}
Parsed value: 5xx last 1min: 0
```

Prometheus 5xx after migration:

```text
5xx last 1min: 0
```

### Backup validation

```text
$ ls -lh /tmp/quickticket.dump
-rw-rw-r-- 1 ernest ernest 7.2K Jul  5 20:48 /tmp/quickticket.dump

$ file /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0

$ kubectl exec postgres-78489d7f5f-8gv2x -- pg_restore --list /tmp/backup.dump | head -25
;
; Archive created at 2026-07-05 17:48:03 UTC
;     dbname: quickticket
;     TOC Entries: 18
;     Compression: gzip
;     Dump Version: 1.16-0
;     Format: CUSTOM
;     Integer: 4 bytes
;     Offset: 8 bytes
;     Dumped from database version: 17.10
;     Dumped by pg_dump version: 17.10
;
;
; Selected TOC Entries:
;
220; 1259 16408 TABLE public alembic_version quickticket
218; 1259 16386 TABLE public events quickticket
217; 1259 16385 SEQUENCE public events_id_seq quickticket
3481; 0 0 SEQUENCE OWNED BY public events_id_seq quickticket
219; 1259 16394 TABLE public orders quickticket
3316; 2604 16389 DEFAULT public events id quickticket
3474; 0 16408 TABLE DATA public alembic_version quickticket
3472; 0 16386 TABLE DATA public events quickticket
3473; 0 16394 TABLE DATA public orders quickticket
3482; 0 0 SEQUENCE SET public events_id_seq quickticket
```

### Data loss and restore

For the restore test I first captured the healthy row counts, then dropped `orders` to simulate an operator mistake or accidental data loss. The `/events` endpoint returned `502` while the schema was broken, which confirmed that the failure was visible to the application.

Before DROP:

```text
 events
--------
      5
(1 row)

 orders
--------
     50
(1 row)
```

After `DROP TABLE orders CASCADE`:

```text
 events
--------
      5
(1 row)

 orders_table
--------------

(1 row)

/events=502
```

After `pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump`:

```text
 events
--------
      5
(1 row)

 orders
--------
     50
(1 row)

/events=200
```

After restoring the dump, both tables were back with the same row counts as before the DROP, and the API returned `200` again.

### RPO answer

With only a single manual `pg_dump`, the RPO is simply the age of that dump. Any rows committed after `/tmp/quickticket.dump` was created would be lost during restore. Scheduled dumps reduce that window, but for a production database I would also use persistent storage and WAL archiving/PITR so recovery can target a much more recent point in time.

## Task 2 - Disaster Recovery Under Load

```text
OLD_POD=postgres-78489d7f5f-dzc56
T0=20:52:13
 orders_before_disaster
------------------------
                     50
(1 row)

healthy at 20:52:13
pod "postgres-78489d7f5f-dzc56" force deleted from default namespace
T_KILL=20:52:14
T_READY_DB_ACCEPTING=20:52:20
NEW_POD=postgres-78489d7f5f-2pm48
Did not find any relations.
T_RESTORED=20:52:20
deployment "events" successfully rolled out
T_APP_READY=20:52:28
 orders_after_restore
----------------------
                   50
(1 row)
```

Actual RTO: `20:52:28 - 20:52:14 = 14 seconds`. This is the full user-facing recovery window: pod replacement, database restore, and restarting the `events` service so it reconnects cleanly.

Actual RPO time: about 4 minutes 11 seconds, from the dump timestamp (`2026-07-05 17:48:03 UTC`, local 20:48:03) to the disaster (`20:52:14`). The row gap was `50 - 50 = 0` for this run because there were no extra committed orders beyond the backup at the measured count. In a busier system, this gap would be the writes accepted after the last successful backup.

Prometheus error-rate query around the incident:

```text
5xx rate 30s: 1.48
```

The new Postgres pod was empty because the original Deployment had no PVC. The database files lived only in the pod filesystem, so deleting the pod deleted the data. Mounting a PersistentVolumeClaim at the Postgres data directory removes this specific failure mode because the replacement pod reuses the same volume.

## Bonus - PVC and Automated Backups

### `k8s/postgres.yaml` diff

```diff
diff --git a/k8s/postgres.yaml b/k8s/postgres.yaml
index e527644..60e1c89 100644
--- a/k8s/postgres.yaml
+++ b/k8s/postgres.yaml
@@ -24,6 +24,11 @@ spec:
               value: "quickticket"
             - name: POSTGRES_PASSWORD
               value: "quickticket"
+            - name: PGDATA
+              value: "/var/lib/postgresql/data/pgdata"
+          volumeMounts:
+            - name: data
+              mountPath: /var/lib/postgresql/data
           resources:
             requests:
               cpu: 50m
@@ -31,6 +36,10 @@ spec:
             limits:
               cpu: 200m
               memory: 256Mi
+      volumes:
+        - name: data
+          persistentVolumeClaim:
+            claimName: postgres-data

 ---
 apiVersion: v1
@@ -43,4 +52,14 @@ spec:
     app: postgres
   ports:
     - port: 5432
-      targetPort: 5432
\ No newline at end of file
+      targetPort: 5432
+---
+apiVersion: v1
+kind: PersistentVolumeClaim
+metadata:
+  name: postgres-data
+spec:
+  accessModes: [ReadWriteOnce]
+  resources:
+    requests:
+      storage: 1Gi
```

### RTO with PVC

```text
OLD_POD=postgres-68466c5ccd-x9vt5
T0=20:54:22
 orders_before_pvc_restart
---------------------------
                        50
(1 row)

pod "postgres-68466c5ccd-x9vt5" force deleted from default namespace
T_KILL=20:54:22
T_READY_DB_ACCEPTING=20:54:23
NEW_POD=postgres-68466c5ccd-pbglm
               List of relations
 Schema |      Name       | Type  |    Owner
--------+-----------------+-------+-------------
 public | alembic_version | table | quickticket
 public | events          | table | quickticket
 public | orders          | table | quickticket
(3 rows)

 orders_after_pvc_restart
--------------------------
                       50
(1 row)

deployment "events" successfully rolled out
T_APP_READY=20:54:32
```

PVC RTO: `20:54:32 - 20:54:22 = 10 seconds`. The important difference is that recovery no longer depended on copying and restoring a dump. The replacement pod started with the existing database files already present on the persistent volume.

### `k8s/backup-cronjob.yaml`

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
                - |
                  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
                  dump="/backups/quickticket_${timestamp}.dump"
                  echo "writing ${dump}"
                  pg_dump -Fc -f "${dump}"
                  cd /backups
                  echo "retaining the 5 newest quickticket dumps"
                  ls -1t quickticket_*.dump | tail -n +6 | xargs -r rm -v
                  ls -lh /backups
              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

### Manual backup and retention proof

I triggered seven manual jobs from the CronJob instead of waiting for the five-minute schedule. The last job removed the oldest dump, which proves the retention rule keeps the newest five backups only.

`manual-1`:

```text
job.batch/manual-1 created
job.batch/manual-1 condition met
writing /backups/quickticket_20260705T175447Z.dump
retaining the 5 newest quickticket dumps
total 8K
-rw-r--r--    1 root     root        7.1K Jul  5 17:54 quickticket_20260705T175447Z.dump
```

`manual-7`:

```text
job.batch/manual-7 condition met
writing /backups/quickticket_20260705T175520Z.dump
retaining the 5 newest quickticket dumps
removed 'quickticket_20260705T175505Z.dump'
total 40K
-rw-r--r--    1 root     root        7.1K Jul  5 17:55 quickticket_20260705T175508Z.dump
-rw-r--r--    1 root     root        7.1K Jul  5 17:55 quickticket_20260705T175511Z.dump
-rw-r--r--    1 root     root        7.1K Jul  5 17:55 quickticket_20260705T175514Z.dump
-rw-r--r--    1 root     root        7.1K Jul  5 17:55 quickticket_20260705T175517Z.dump
-rw-r--r--    1 root     root        7.1K Jul  5 17:55 quickticket_20260705T175520Z.dump
```

Final `/backups` listing:

```text
$ kubectl exec deployment/backup-inspector -- ls -la /backups
total 48
drwxrwxrwx    2 root     root          4096 Jul  5 17:55 .
drwxr-xr-x    1 root     root          4096 Jul  5 17:53 ..
-rw-r--r--    1 root     root          7297 Jul  5 17:55 quickticket_20260705T175508Z.dump
-rw-r--r--    1 root     root          7297 Jul  5 17:55 quickticket_20260705T175511Z.dump
-rw-r--r--    1 root     root          7297 Jul  5 17:55 quickticket_20260705T175514Z.dump
-rw-r--r--    1 root     root          7297 Jul  5 17:55 quickticket_20260705T175517Z.dump
-rw-r--r--    1 root     root          7297 Jul  5 17:55 quickticket_20260705T175520Z.dump
```
