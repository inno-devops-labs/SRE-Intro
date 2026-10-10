# Lab 9 — Stateful Services & DB Reliability

## Environment

- Date: 2026-07-08
- Cluster: local k3d `quickticket`
- Workload during migration/recovery: `mixedload` deployment, `2/2` available
- Postgres dump used for recovery: `/tmp/quickticket.dump`

## Task 1 — Migrations & Backup/Restore

### Alembic history

```text
7488189f281f -> adcc81d29aa3 (head), add email column to events
<base> -> 7488189f281f, baseline - pre-existing schema
```

`alembic current`:

```text
adcc81d29aa3 (head)
```

### `events` schema after migration

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

### Migration timing under load

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 7488189f281f -> adcc81d29aa3, add email column to events
real 2.29
user 0.63
sys 0.27
```

Prometheus 5xx before migration:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783526676.937,"0"]}]}}
```

Prometheus 5xx after migration:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783526765.162,"0"]}]}}
```

Result: migration added a nullable column under load with no additional 5xx.

### Backup evidence

```text
$ ls -lh /tmp/quickticket.dump
-rw-r--r--@ 1 arsen  wheel   7.1K Jul  8 19:07 /tmp/quickticket.dump

$ file /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
```

`pg_restore --list /tmp/backup.dump` inside the Postgres pod:

```text
; Archive created at 2026-07-08 16:07:35 UTC
;     dbname: quickticket
;     TOC Entries: 18
;     Compression: gzip
;     Dump Version: 1.16-0
;     Format: CUSTOM
;     Dumped from database version: 17.10
;     Dumped by pg_dump version: 17.10
;
; Selected TOC Entries:
220; 1259 16411 TABLE public alembic_version quickticket
218; 1259 16386 TABLE public events quickticket
217; 1259 16385 SEQUENCE public events_id_seq quickticket
219; 1259 16394 TABLE public orders quickticket
3474; 0 16411 TABLE DATA public alembic_version quickticket
3472; 0 16386 TABLE DATA public events quickticket
3473; 0 16394 TABLE DATA public orders quickticket
3320; 2606 16393 CONSTRAINT public events events_pkey quickticket
3322; 2606 16402 CONSTRAINT public orders orders_pkey quickticket
3325; 2606 16403 FK CONSTRAINT public orders orders_event_id_fkey quickticket
```

### Data loss simulation and restore

Before disaster:

```text
 events_before
---------------
             5

 orders_before
---------------
            50
```

After `DROP TABLE orders CASCADE`:

```text
 events_after_drop
-------------------
                 5

 orders_after_drop
-------------------

```

API smoke while broken:

```text
/events=502
/reserve-pay=500
```

After `pg_restore --clean --if-exists`:

```text
 events_after_restore
----------------------
                    5

 orders_after_restore
----------------------
                   50

/events=200
```

### RPO answer for single `pg_dump`

The current RPO is the age of the latest dump. With a single manual `pg_dump`, every row written after that dump is at risk. In this run the restored dump contained 50 orders, so any orders created after `/tmp/quickticket.dump` would have been lost. I would improve this with persistent storage plus automated scheduled backups and retention; for tighter RPO, add continuous WAL archiving or managed PostgreSQL point-in-time recovery.

## Task 2 — Disaster Recovery Under Load

### Timed recovery without PVC

```text
T0=19:10:29
OLD_POD=postgres-578c9b6b97-zrwsd
orders_before_disaster = 50

T_KILL=19:10:29
NEW_POD=postgres-578c9b6b97-p82tf
T_READY=19:10:37

events_new_pod = 5
orders_new_pod = 0

T_RESTORED=19:10:38
T_APP_READY=19:10:50
orders_after_restore = 50

RTO_SECONDS=21
BACKUP_AGE_AT_KILL_SECONDS=0
```

Prometheus error-rate curve around the incident:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783527056.400,"0.355572104616107"]}]}}
```

### RTO/RPO calculation

- Actual RTO: 21 seconds (`19:10:50 - 19:10:29`).
- Orders before disaster: 50.
- Orders after restore: 50.
- RPO gap in restored records: 0 rows for this measured run, because the dump was current for the recorded dataset.
- Risk statement: with only manual dumps, the real RPO is still "time since last successful dump"; any orders after that dump would be lost.

### Why was the new Postgres pod empty?

The original `k8s/postgres.yaml` stored `/var/lib/postgresql/data` on `emptyDir`. Deleting the pod deleted the database files with it. In this repository the init ConfigMap recreated the base schema and seed events on the new pod, so it was not literally relationless, but it lost runtime state: `orders` went from 50 to 0 and the dump restore was required. The failure mode is eliminated by mounting a PersistentVolumeClaim for the Postgres data directory.

## Bonus Task — PVC + Automated Backups

### `k8s/postgres.yaml` diff

```diff
diff --git a/k8s/postgres.yaml b/k8s/postgres.yaml
index 5c010c6..c9b6613 100644
--- a/k8s/postgres.yaml
+++ b/k8s/postgres.yaml
@@ -36,6 +36,18 @@ data:
     ON CONFLICT DO NOTHING;
 ---
 apiVersion: v1
+kind: PersistentVolumeClaim
+metadata:
+  name: postgres-data
+  labels:
+    app: postgres
+spec:
+  accessModes: [ReadWriteOnce]
+  resources:
+    requests:
+      storage: 1Gi
+---
+apiVersion: v1
 kind: Service
 metadata:
   name: postgres
@@ -79,6 +91,8 @@ spec:
               value: quickticket
             - name: POSTGRES_PASSWORD
               value: quickticket
+            - name: PGDATA
+              value: /var/lib/postgresql/data/pgdata
@@ -104,7 +118,8 @@ spec:
               subPath: 01-seed.sql
       volumes:
         - name: postgres-data
-          emptyDir: {}
+          persistentVolumeClaim:
+            claimName: postgres-data
```

### Re-measured RTO with PVC

Full app-ready run with PVC:

```text
PVC_T0=19:12:57
orders_before_pvc_restart = 50
PVC_T_KILL=19:12:58
PVC_T_READY=19:13:05
events_after_pvc_restart = 5
orders_after_pvc_restart = 50
PVC_T_APP_READY=19:13:18
PVC_RTO_SECONDS=20
```

Pod-restart-only run with PVC, no restore and no events rollout restart:

```text
PVC_FAST_T_KILL=19:13:45
PVC_FAST_T_READY=19:13:53
orders_after_pvc_fast_restart = 50
PVC_FAST_HTTP=200
PVC_FAST_T_APP_READY=19:13:57
PVC_FAST_RTO_SECONDS=12
```

Result: data survived the Postgres pod restart, and recovery no longer required `pg_restore`.

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
                - sh
                - -ec
                - |
                  cd /backups
                  ts="$(date -u +%Y%m%dT%H%M%SZ)"
                  dump="quickticket_${ts}.dump"
                  pg_dump -Fc -f "$dump"
                  echo "created $dump"
                  echo "before retention:"
                  ls -1t quickticket_*.dump
                  old="$(ls -1t quickticket_*.dump | tail -n +6 || true)"
                  if [ -n "$old" ]; then
                    echo "$old" | xargs rm -v
                  else
                    echo "nothing to remove"
                  fi
                  echo "after retention:"
                  ls -1t quickticket_*.dump
              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

### CronJob run and retention proof

`manual-7` logs:

```text
created quickticket_20260708T161451Z.dump
before retention:
quickticket_20260708T161451Z.dump
quickticket_20260708T161446Z.dump
quickticket_20260708T161442Z.dump
quickticket_20260708T161438Z.dump
quickticket_20260708T161434Z.dump
quickticket_20260708T161430Z.dump
removed 'quickticket_20260708T161430Z.dump'
after retention:
quickticket_20260708T161451Z.dump
quickticket_20260708T161446Z.dump
quickticket_20260708T161442Z.dump
quickticket_20260708T161438Z.dump
quickticket_20260708T161434Z.dump
```

`kubectl exec deployment/backup-inspector -- ls -la /backups`:

```text
total 48
drwxrwxrwx    2 root     root          4096 Jul  8 16:14 .
drwxr-xr-x    1 root     root          4096 Jul  8 16:14 ..
-rw-r--r--    1 root     root          7266 Jul  8 16:14 quickticket_20260708T161434Z.dump
-rw-r--r--    1 root     root          7266 Jul  8 16:14 quickticket_20260708T161438Z.dump
-rw-r--r--    1 root     root          7266 Jul  8 16:14 quickticket_20260708T161442Z.dump
-rw-r--r--    1 root     root          7266 Jul  8 16:14 quickticket_20260708T161446Z.dump
-rw-r--r--    1 root     root          7266 Jul  8 16:14 quickticket_20260708T161451Z.dump
```

## PR Checklist

- [x] Task 1 done — Alembic migration under load + `pg_dump`/`pg_restore` cycle
- [x] Task 2 done — disaster recovery RTO/RPO measurement
- [x] Bonus Task done — PVC + automated CronJob backup with rotation

## Acceptance Criteria Checklist

- [x] Alembic initialized, baseline stamped, migration applied.
- [x] Migration ran under load with 0 additional 5xx.
- [x] Non-empty `pg_dump` backup created and valid TOC shown in `pg_restore --list`.
- [x] Data loss simulated with `DROP TABLE orders CASCADE` and recovery shown with restore to API 200.
- [x] Row counts shown for before disaster, after drop, and after restore.
- [x] RPO answer included.
- [x] Full disaster recovery cycle timed with wall-clock timestamps.
- [x] Actual RTO in seconds and RPO gap in rows included.
- [x] Observation included for no-PVC data loss.
- [x] PVC added to Postgres and data survives pod restart.
- [x] RTO re-measured with PVC and no restore required.
- [x] Student-written CronJob runs `pg_dump` every 5 minutes.
- [x] Retention works: 7 runs left exactly 5 files and `manual-7` shows removal.
