# Lab 9 — Stateful Services & DB Reliability

## Task 1 — Migrations & Backup/Restore

### 1. Alembic setup and migration history

Alembic was initialized to manage schema changes for the existing QuickTicket PostgreSQL database. Since the database already contained the `events` and `orders` tables, an empty baseline revision was created and marked as applied using `alembic stamp head`.

The baseline revision was `1536dcffc4c9`. The subsequent revision, `a68dfd8aa03c`, added a nullable `email` column to the `events` table.

```text
1536dcffc4c9 -> a68dfd8aa03c (head), add email column to events
<base> -> 1536dcffc4c9, baseline - pre-existing schema
```

### 2. Schema after migration

The migration added an `email` column with type `VARCHAR(255)` and allowed NULL values. The schema was verified using `psql`.

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

### 3. Migration execution time

The migration was executed while the `mixedload` deployment was running.

Command:

```bash
time alembic upgrade head
```

Output:

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 1536dcffc4c9 -> a68dfd8aa03c, add email column to events
alembic upgrade head  0.19s user 0.05s system 59% cpu 0.402 total
```

The migration completed successfully in 0.402 seconds. The nullable-column addition was a quick schema change and did not require a full table rewrite.

### 4. HTTP 5xx error rate before and after migration

The Prometheus query used was:

```promql
sum(increase(gateway_requests_total{status=~"5.."}[1m]))
```

Recorded measurements:

```text
Before migration: 1.0908694229300753
After migration:  0
Repeated check:   0
```

The metric represents the estimated increase in the HTTP 5xx request counter over the preceding minute. The pre-migration value was non-zero, while the following measurements returned zero.

The results do not indicate an increase in HTTP 5xx errors following the migration. Because the metric uses a one-minute window, the two values describe different time intervals and do not prove that the migration itself reduced the error rate.

### 5. Backup creation and verification

A PostgreSQL custom-format backup was created using `pg_dump -Fc`. The backup was saved locally and inspected using `pg_restore --list`.

```text
-rw-r--r--@ 1 achegaev  wheel   7.1K Oct 12 09:22 /tmp/quickticket.dump

/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
```

The backup archive was successfully inspected. The listing reported 18 TOC entries and included the `alembic_version`, `events`, and `orders` tables, the `events_id_seq` sequence, and the corresponding table data.

```text
; Archive created at 2026-10-12 06:36:32 UTC
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
; Selected TOC Entries:
;
217; 1259 16389 TABLE public alembic_version quickticket
218; 1259 16392 TABLE public events quickticket
219; 1259 16397 SEQUENCE public events_id_seq quickticket
3481; 0 0 SEQUENCE OWNED BY public events_id_seq quickticket
220; 1259 16398 TABLE public orders quickticket
3316; 2604 16405 DEFAULT public events id quickticket
3471; 0 16389 TABLE DATA public alembic_version quickticket
3472; 0 16392 TABLE DATA public events quickticket
3474; 0 16398 TABLE DATA public orders quickticket
3482; 0 0 SEQUENCE SET public events_id_seq quickticket
```

A subsequent backup, `/tmp/quickticket-task1.dump`, was also created and used successfully to restore the database after dropping the `orders` table.


### 6. Simulated data loss and restore

Before dropping the table, the database contained five events and 50 orders.

```text
Before DROP:

events_count
------------
5

orders_count
------------
50
```

The `orders` table was dropped using `DROP TABLE orders CASCADE`. The subsequent `\dt` output showed that `alembic_version` and `events` remained, while `orders` was absent.

```text
After DROP:

Schema | Name            | Type  | Owner
-------+-----------------+-------+-----------
public | alembic_version | table | quickticket
public | events          | table | quickticket
```

The database was restored using the custom-format backup:

```bash
pg_restore -U quickticket -d quickticket \
  --clean --if-exists /tmp/task1.dump
```

After restoration, the tables were present and the row counts were verified again.

| State         | Events rows |   Orders rows |
| ------------- | ----------: | ------------: |
| Before DROP   |           5 |            50 |
| After DROP    |           5 | Table missing |
| After restore |           5 |            50 |

The restore completed successfully. Both tables were recovered with their expected row counts. The successful restore demonstrated that a custom-format PostgreSQL backup could recover the dropped table and its data.


### 7. RPO of a single pg_dump backup

A single `pg_dump` backup provides a recovery point corresponding to the state of the database when the backup snapshot is taken. Transactions committed after that snapshot may be lost if the database must be restored from the backup.

The potential data-loss window therefore depends on the age of the latest usable backup at the time of failure. More frequent automated backups can reduce this window. Continuous WAL archiving and point-in-time recovery can reduce it further.

Persistent storage is also needed to preserve database files across Pod recreation, but persistent storage alone does not replace backups.

---

## Task 2 — Disaster Recovery Under Load

### 1. Recovery procedure

The `mixedload` workload remained running while the PostgreSQL Pod was deleted. Kubernetes created a replacement Pod, but the replacement initially contained no database tables because the original Deployment had no PersistentVolumeClaim.

The database was restored from the previously created custom-format backup, and the `events` deployment was restarted to reconnect to PostgreSQL.

The endpoint `/events` returned HTTP 200 after recovery.

### 2. Recovery timestamps

Recorded phase timestamps:

* Disaster: 2026-10-12 09:24:52 MSK (the separately captured Unix timestamp corresponds to 09:24:46).
* New PostgreSQL Pod ready: 2026-10-12 09:25:28 MSK.
* Database restored: 2026-10-12 09:26:35 MSK.
* Application fully up: 2026-10-12 09:27:13 MSK.

The disaster timestamp used in the numerical RTO calculation was captured immediately before the deletion command. The separately formatted human-readable timestamp was printed six seconds later.

### 3. Actual RTO

The measured recovery phases were:

| Phase                      |        Duration |
| -------------------------- | --------------: |
| PostgreSQL Pod recovery    |      37 seconds |
| Database restore phase     |      72 seconds |
| Application recovery phase |      38 seconds |
| **Actual RTO**             | **147 seconds** |

The actual RTO was calculated from the recorded Unix timestamps:

```text
Actual RTO: 147 seconds
```

The full recovery process included recreating the PostgreSQL Pod, restoring the database from the backup, restarting the application service, and confirming API availability.

### 4. Actual RPO

The backup file timestamp was:

```text
2026-10-12 09:22:27 MSK
```

The elapsed time calculated from the recorded Unix timestamps was:

```text
Backup age at disaster: 139 seconds
```

The database contained 50 orders before the disaster and 50 orders after restoration.

```text
Orders before: 50
Orders after:  50
Orders gap:    0
```

The observed difference in the order count was zero. However, the temporal recovery-point window was approximately 139 seconds, so the unchanged count does not prove that every transaction committed after the backup was preserved.

### 5. Prometheus observations

The HTTP 5xx rate around the incident was queried with:

```promql
sum(rate(gateway_requests_total{status=~"5.."}[30s]))
```

The `query_range` request returned a successful result. The sampled rate was zero before the recovery window, increased during the incident, reached a peak of approximately 6.08 errors per second, and subsequently returned to zero.

Selected sampled values included:

```text
0
0.07999360051195904
2.411634563328322
5.352136752136753
5.96
6.080972955672908
3.8403072245779666
0.16000640025601023
0
0
```

This indicates a temporary increase in server errors during the incident and a subsequent return to zero. The query uses a rolling 30-second rate window, so the metric can remain elevated briefly after application recovery.

### 6. Why was the new PostgreSQL Pod empty?

The original PostgreSQL Deployment did not mount a PersistentVolumeClaim. The database files were stored in the container's ephemeral writable layer. When the Pod was deleted, the replacement Pod initialized a fresh database and the old tables were not available.

A PersistentVolumeClaim mounted at the PostgreSQL data directory would preserve the database files across Pod recreation. Automated backups and continuous WAL archiving would provide additional recovery options.

---

## Bonus Task — Persistent Storage and Automated Backup CronJob

### B.1. Persistent storage for PostgreSQL

A PersistentVolumeClaim named `postgres-data` was added to `k8s/postgres.yaml`. The PostgreSQL container uses the following configuration:

```yaml
env:
  - name: PGDATA
    value: /var/lib/postgresql/data/pgdata
volumeMounts:
  - name: data
    mountPath: /var/lib/postgresql/data
volumes:
  - name: data
    persistentVolumeClaim:
      claimName: postgres-data
```

The PVC was successfully provisioned with the `local-path` StorageClass and reached the `Bound` state. The database was restored to the new volume from `/tmp/quickticket-before-pvc.dump`.

The PVC was verified by deleting and recreating the PostgreSQL Pod without running `pg_restore` after the restart.

Recorded results:

```text
Orders before restart: 50
Orders after restart:  50
```

The API returned HTTP 200 following the application restart.

### B.1.1. RTO comparison with persistent storage

The second disaster-recovery test produced these results:

```text
Disaster:        2026-10-12 09:49:09 MSK
Postgres ready:  2026-10-12 09:49:10 MSK
App ready:       2026-10-12 09:49:39 MSK

Pod recovery:         1 second
Actual RTO with PVC: 30 seconds

Orders before: 50
Orders after:  50
```

The RTO decreased from 147 seconds without persistent storage to 30 seconds with a PVC, an improvement of 117 seconds, or approximately 79.6%.

The PostgreSQL Pod became ready after one second. The remaining recovery interval included restarting the `events` deployment and verifying application readiness. No database restore operation was needed after the Pod recreation.

### B.2. Automated backup CronJob

The `labs/lab9/backup-storage.yaml` manifest was applied successfully. The `postgres-backups` PVC reached the `Bound` state, and the `backup-inspector` deployment became available.

The following CronJob was created in `k8s/backup-cronjob.yaml`:

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
              command: ["/bin/sh", "-c"]
              args:
                - |
                  set -eu

                  TS="$(date -u +%Y%m%dT%H%M%SZ)"
                  OUT="/backups/quickticket_${TS}.dump"
                  n=1

                  while [ -e "$OUT" ]; do
                    OUT="/backups/quickticket_${TS}_${n}.dump"
                    n=$((n + 1))
                  done

                  echo "Creating backup: $OUT"
                  pg_dump -Fc -f "$OUT"
                  test -s "$OUT"
                  echo "Backup created: $OUT"

                  cd /backups
                  echo "Applying retention: keep newest 5 dumps"

                  ls -1t quickticket_*.dump |
                    tail -n +6 |
                    while IFS= read -r old; do
                      [ -n "$old" ] || continue
                      rm -v -- "$old"
                    done

                  echo "Remaining backups:"
                  ls -lh quickticket_*.dump

              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

The CronJob runs every five minutes, creates custom-format PostgreSQL backups, uses the Kubernetes Service hostname `postgres`, prevents overlapping scheduled jobs, and retains only the five newest dump files.

### B.2.1. Manual execution and retention verification

Seven manual Jobs were executed successfully:

```bash
kubectl create job --from=cronjob/postgres-backup manual-1
```

Additional Jobs named `manual-2` through `manual-7` were created and waited on until completion.

The logs from `manual-7` included:

```text
Creating backup: /backups/quickticket_20261012T064620Z.dump
Backup created: /backups/quickticket_20261012T064620Z.dump
Applying retention: keep newest 5 dumps
removed 'quickticket_20261012T064605Z.dump'
Remaining backups:
-rw-r--r-- 1 root root 7.1K Oct 12 06:46 quickticket_20261012T064608Z.dump
-rw-r--r-- 1 root root 7.1K Oct 12 06:46 quickticket_20261012T064611Z.dump
-rw-r--r-- 1 root root 7.1K Oct 12 06:46 quickticket_20261012T064614Z.dump
-rw-r--r-- 1 root root 7.1K Oct 12 06:46 quickticket_20261012T064617Z.dump
-rw-r--r-- 1 root root 7.1K Oct 12 06:46 quickticket_20261012T064620Z.dump
```

The final directory listing showed five backup files:

```text
total 48
drwxrwxrwx 2 root root 4096 Oct 12 06:46 .
drwxr-xr-x 1 root root 4096 Oct 12 06:44 ..
-rw-r--r-- 1 root root 7278 Oct 12 06:46 quickticket_20261012T064608Z.dump
-rw-r--r-- 1 root root 7278 Oct 12 06:46 quickticket_20261012T064611Z.dump
-rw-r--r-- 1 root root 7278 Oct 12 06:46 quickticket_20261012T064614Z.dump
-rw-r--r-- 1 root root 7278 Oct 12 06:46 quickticket_20261012T064617Z.dump
-rw-r--r-- 1 root root 7278 Oct 12 06:46 quickticket_20261012T064620Z.dump
```

The count command confirmed the retention requirement:

```text
5
```

Seven manual backup Jobs completed successfully, and the retention logic removed older dumps so that exactly five `quickticket_*.dump` files remained on the `postgres-backups` PVC.

---

## Conclusion

This lab demonstrated schema migration with Alembic under live traffic, backup creation and restoration with PostgreSQL custom-format dumps, and disaster recovery with measured RTO and RPO.

Without a PVC, the PostgreSQL Pod restart required database restoration and produced an RTO of 147 seconds. After persistent storage was added, the Pod restart preserved the database and the measured RTO dropped to 30 seconds. The automated CronJob was also verified to create backups on a five-minute schedule and retain exactly the five newest dumps.
