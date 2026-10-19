# Lab 9 — Stateful Services & DB Reliability

## Task 1 — Migrations & Backup/Restore

### Alembic History

The existing QuickTicket database schema was first registered as the Alembic
baseline.

```text
a6ed12a2f729 -> f75b6999664a (head), add email column to events
<base> -> a6ed12a2f729, baseline - pre-existing schema
```

After applying the migration, the current revision was:

```text
f75b6999664a (head)
```

The two revisions were:

```text
a6ed12a2f729 - baseline - pre-existing schema
f75b6999664a - add email column to events
```

---

### Migration Under Load

The Lab 8 `mixedload` deployment was running during the database migration.

```text
NAME        READY   UP-TO-DATE   AVAILABLE
mixedload   2/2     2            2
```

Before applying the migration, the gateway error count was:

```text
5xx last 1min: 0
```

The migration was applied using:

```text
alembic upgrade head
```

The measured execution time was:

```text
real    0m0.237s
user    0m0.165s
sys     0m0.047s
```

After the migration, the gateway error count was still:

```text
5xx last 1min: 0
```

Therefore, adding the nullable column did not introduce any additional 5xx
responses under live traffic.

---

### Events Schema After Migration

The resulting `events` table was:

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
```

The migration successfully added:

```text
email | character varying(255) | nullable
```

Using a nullable column allowed the schema change to complete very quickly
without requiring the existing rows to contain a value for the new field.

---

### PostgreSQL Backup

A custom-format PostgreSQL backup was created using `pg_dump`.

```text
-rw-r--r-- 1 minimizec minimizec 7.2K Oct 12 06:11 /tmp/quickticket.dump
```

The host identified the file as:

```text
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
```

The archive was inspected with `pg_restore --list`.

```text
;
; Archive created at 2026-10-12 03:11:25 UTC
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
220; 1259 16411 TABLE public alembic_version quickticket
218; 1259 16386 TABLE public events quickticket
217; 1259 16385 SEQUENCE public events_id_seq quickticket
3481; 0 0 SEQUENCE OWNED BY public events_id_seq quickticket
219; 1259 16394 TABLE public orders quickticket
3316; 2604 16389 DEFAULT public events id quickticket
3474; 0 16411 TABLE DATA public alembic_version quickticket
3472; 0 16386 TABLE DATA public events quickticket
3473; 0 16394 TABLE DATA public orders quickticket
3482; 0 0 SEQUENCE SET public events_id_seq quickticket
```

This verified that the backup contained the database schema, Alembic revision
information, and table data.

---

### Simulated Data Loss

Before the simulated failure, the database contained:

```text
events
--------
5

orders
--------
50
```

The `orders` table was then deliberately removed using:

```sql
DROP TABLE orders CASCADE;
```

After the drop, only the following tables remained:

```text
               List of relations
 Schema |      Name       | Type  |    Owner
--------+-----------------+-------+-------------
 public | alembic_version | table | quickticket
 public | events          | table | quickticket
```

The application immediately showed a user-visible failure:

```text
/events=502
```

This demonstrated the effect of database object loss on the running service.

---

### Database Restore

The backup was restored with:

```text
pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
```

After the restore, the row counts were:

```text
events
--------
5

orders
--------
50
```

The migrated schema was also restored successfully:

```text
email | character varying(255)
```

The application recovered:

```text
/events=200
```

The complete backup therefore restored both the table data and the Alembic
managed schema.

---

### Row Counts Through the Recovery Test

| Stage | Events | Orders |
|---|---:|---:|
| Before disaster | 5 | 50 |
| After `DROP TABLE orders` | 5 | Table missing |
| After restore | 5 | 50 |

---

### Current RPO

With only a manually created `pg_dump`, the Recovery Point Objective depends on
the age of the latest backup.

Any successful writes made after the most recent backup can be lost if the
database must be restored from that dump.

For example, if a backup is one hour old, up to one hour of successful database
writes may be lost.

The RPO can be improved by:

- creating backups automatically at short intervals;
- retaining several recent backups;
- storing backups independently of the PostgreSQL pod;
- using persistent storage;
- and, in a production environment, using PostgreSQL WAL archiving or
  point-in-time recovery.

The bonus task addresses the first four of these improvements.

---

# Task 2 — Disaster Recovery Under Load

## Pre-Disaster State

The `mixedload` deployment remained active during the recovery test.

Immediately before the Postgres failure:

```text
Healthy at: 2026-10-12T06:16:06+03:00

orders_before_disaster
------------------------
50
```

---

## Postgres Failure

The Postgres pod was force deleted at:

```text
2026-10-12T06:16:09+03:00
```

Kubernetes created a replacement pod, which became Ready at:

```text
2026-10-12T06:16:14+03:00
```

The Postgres pod restart therefore took approximately:

```text
5 seconds
```

---

## Empty Replacement Database

The replacement Postgres pod contained no database tables:

```text
Did not find any relations.
```

This happened because the original Postgres Deployment did not have a
PersistentVolumeClaim.

Its database files were stored only in the filesystem of the Postgres pod.

When that pod was deleted, the database files were deleted with it. The
replacement pod therefore started with a new empty PostgreSQL data directory.

Kubernetes successfully restored the Postgres process, but it could not restore
the state of the database.

---

## Database Recovery

The existing backup was copied into the replacement Postgres pod and restored.

Database restoration completed at:

```text
2026-10-12T06:16:22+03:00
```

The restore step took approximately:

```text
8 seconds
```

The events deployment was then restarted so its database connection pool would
establish fresh connections to the replacement PostgreSQL instance.

The application became fully ready at:

```text
2026-10-12T06:16:28+03:00
```

---

## Recovery Timeline

```text
Healthy before: 2026-10-12T06:16:06+03:00
Disaster:       2026-10-12T06:16:09+03:00
New pod ready:  2026-10-12T06:16:14+03:00
DB restored:    2026-10-12T06:16:22+03:00
App ready:      2026-10-12T06:16:28+03:00
```

Measured recovery stages:

```text
Pod restart time: 5 seconds
Restore time:     8 seconds
Actual RTO:       19 seconds
```

The measured Recovery Time Objective was therefore:

```text
RTO = 19 seconds
```

---

## RPO Measurement

Immediately before the disaster:

```text
orders_before_disaster = 50
```

After restoring the backup:

```text
orders_after_restore = 50
```

The observed record loss was therefore:

```text
50 - 50 = 0 orders
```

For this experiment, the observed RPO gap was zero rows.

However, the backup was created at approximately:

```text
2026-10-12T06:11:25+03:00
```

and the disaster occurred at:

```text
2026-10-12T06:16:09+03:00
```

The backup was therefore approximately 4 minutes 44 seconds old.

This means that the potential time-based RPO window was approximately 4 minutes
44 seconds. Any writes created after the backup could have been lost.

The fact that the measured order count happened to remain 50 does not eliminate
the underlying backup-age risk.

---

## Error Rate During Recovery

Prometheus reported:

```text
5xx rate: 1.3601184115212033
```

using the 30-second gateway error-rate window.

After recovery, the gateway returned:

```json
{
  "status": "healthy",
  "checks": {
    "events": "ok",
    "payments": "ok",
    "circuit_payments": "CLOSED"
  }
}
```

---

## Why Was the New Postgres Pod Empty?

The original Postgres Deployment used ephemeral pod storage.

Deleting the pod also deleted the filesystem containing PostgreSQL's data
directory.

The replacement pod therefore had no access to the previous database state.

This failure mode can be eliminated by mounting a Kubernetes
PersistentVolumeClaim at PostgreSQL's data directory.

The PersistentVolume exists independently of an individual Postgres pod, so a
replacement pod can mount the same storage and immediately access the existing
database.

---

# Bonus Task — Persistent Storage and Automated Backups

## PostgreSQL PersistentVolumeClaim

A 1 GiB `ReadWriteOnce` PersistentVolumeClaim named `postgres-data` was added.

The PVC successfully bound:

```text
NAME            STATUS   CAPACITY   ACCESS MODES   STORAGECLASS
postgres-data   Bound    1Gi        RWO            local-path
```

PostgreSQL was configured with:

```text
PGDATA=/var/lib/postgresql/data/pgdata
```

and the persistent volume was mounted at:

```text
/var/lib/postgresql/data
```

The database was seeded once after creating the new volume.

Initial contents:

```text
events: 5
orders: 0
```

After live traffic had been running:

```text
events: 5
orders: 25
```

---

## PVC Persistence Test

Before deleting the Postgres pod:

```text
2026-10-12T06:20:02+03:00

events_before
---------------
5

orders_before
---------------
25
```

The Postgres pod was then force deleted.

The replacement pod started with the same database data already present:

```text
events_after
--------------
5

orders_after
--------------
25
```

No `pg_restore` operation was required.

This proved that the database state survived the deletion of the Postgres pod
because it was stored on the PersistentVolume rather than the pod filesystem.

---

## Initial PVC Recovery Test

The first PVC-backed test produced:

```text
Disaster:        2026-10-12T06:20:14+03:00
Postgres ready:  2026-10-12T06:20:18+03:00
Application up:  2026-10-12T06:20:38+03:00

Postgres restart: 4 seconds
PVC-backed RTO:   24 seconds
```

However, this measurement included an unnecessary manual restart of the events
deployment.

A second clean test was therefore performed without manually restarting the
application.

---

## Clean PVC Recovery Test

The second test measured recovery without `pg_restore` and without manually
restarting the events service.

```text
Disaster:       2026-10-12T06:21:49+03:00
Postgres ready: 2026-10-12T06:21:52+03:00
App healthy:    2026-10-12T06:21:59+03:00
```

Measured recovery:

```text
Postgres restart: 3 seconds
PVC-backed RTO:   10 seconds
```

The replacement Postgres pod retained:

```text
events: 5
orders: 25
```

The existing application recovered automatically after PostgreSQL became
available again.

---

## RTO Comparison

| Configuration | RTO | Manual Restore Required |
|---|---:|---|
| Ephemeral PostgreSQL storage | 19 seconds | Yes |
| PVC-backed PostgreSQL storage | 10 seconds | No |

The PVC reduced measured RTO from 19 seconds to 10 seconds.

This is a reduction of approximately:

```text
(19 - 10) / 19 × 100 ≈ 47%
```

More importantly, database state survived the pod replacement and no manual
restore operation was required.

---

## Automated Backup CronJob

A separate backup PersistentVolumeClaim was used for storing database dump
files independently of the Postgres data volume.

A Kubernetes CronJob named `postgres-backup` was created.

Its configuration was:

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
            - name: postgres-backup
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
                - -c
                - |
                  set -e

                  TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
                  FILE="/backups/quickticket_${TIMESTAMP}.dump"

                  echo "Creating backup: $FILE"

                  pg_dump -Fc -f "$FILE"

                  echo "Backup created:"
                  ls -lh "$FILE"

                  echo "Applying retention policy..."

                  cd /backups

                  ls -1t quickticket_*.dump \
                    | tail -n +6 \
                    | xargs -r rm -v

                  echo "Backups retained:"
                  ls -1t quickticket_*.dump

              volumeMounts:
                - name: backups
                  mountPath: /backups

          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

The CronJob schedule was verified as:

```text
NAME              SCHEDULE      SUSPEND
postgres-backup   */5 * * * *   False
```

The backup therefore runs automatically every five minutes.

`concurrencyPolicy: Forbid` prevents multiple backups from running
simultaneously if one execution takes longer than the scheduling period.

---

## First Manual Backup

The first manually triggered backup completed successfully.

```text
Creating backup: /backups/quickticket_20261012T032357Z.dump

Backup created:
-rw-r--r-- 1 root root 5.3K Oct 12 03:23 /backups/quickticket_20261012T032357Z.dump

Applying retention policy...

Backups retained:
quickticket_20261012T032357Z.dump
```

The file was also visible from the backup-inspector deployment:

```text
-rw-r--r-- 1 root root 5.3K Oct 12 03:23 quickticket_20261012T032357Z.dump
```

---

## Backup Retention Test

Seven manual backup jobs were executed to verify retention.

The retention logic was:

```text
ls -1t quickticket_*.dump | tail -n +6 | xargs -r rm -v
```

This keeps the five newest files and removes anything older.

The seventh run showed the retention process removing an older backup:

```text
Creating backup: /backups/quickticket_20261012T032503Z.dump

Backup created:
-rw-r--r-- 1 root root 5.3K Oct 12 03:25 /backups/quickticket_20261012T032503Z.dump

Applying retention policy...

removed 'quickticket_20261012T032435Z.dump'

Backups retained:
quickticket_20261012T032503Z.dump
quickticket_20261012T032457Z.dump
quickticket_20261012T032452Z.dump
quickticket_20261012T032446Z.dump
quickticket_20261012T032441Z.dump
```

After all seven runs, the final backup count was:

```text
Backup count:
5
```

The retained files were:

```text
/backups/quickticket_20261012T032503Z.dump
/backups/quickticket_20261012T032457Z.dump
/backups/quickticket_20261012T032452Z.dump
/backups/quickticket_20261012T032446Z.dump
/backups/quickticket_20261012T032441Z.dump
```

This confirms that the retention mechanism worked correctly:

```text
7 backup runs -> exactly 5 backups retained
```

---

# Final Results

## Task 1 — Migrations and Backup/Restore

- Alembic initialized.
- Existing database schema baselined.
- Baseline revision stamped.
- Nullable `email` migration created.
- Migration executed under live traffic.
- Migration completed in approximately 0.237 seconds.
- Gateway 5xx count remained 0 before and after migration.
- Valid PostgreSQL custom-format backup created.
- Backup contents verified with `pg_restore --list`.
- `orders` table deliberately removed.
- Application failure observed after data loss.
- Database successfully restored.
- Application recovered to HTTP 200.
- RPO implications documented.

## Task 2 — Disaster Recovery

- Postgres deliberately killed under load.
- Replacement pod started in approximately 5 seconds.
- Replacement database was empty because no PVC existed.
- Database restored manually from `pg_dump`.
- Events service reconnected.
- Measured full RTO was 19 seconds.
- Orders before disaster: 50.
- Orders after restore: 50.
- Observed RPO gap: 0 rows.
- Potential time-based RPO window: approximately 4 minutes 44 seconds.
- Prometheus captured elevated 5xx traffic during recovery.

## Bonus — Persistent Storage and Automated Backup

- PostgreSQL PersistentVolumeClaim added.
- Database survived Postgres pod deletion.
- No `pg_restore` required with PVC.
- Clean PVC-backed RTO measured at 10 seconds.
- RTO improved from 19 seconds to 10 seconds.
- Separate backup PVC configured.
- Student-written CronJob performs a backup every five minutes.
- `concurrencyPolicy: Forbid` configured.
- Custom-format timestamped backups created.
- Seven backup runs tested.
- Retention deleted older backups.
- Exactly five newest backups remained.

## Conclusion

The experiments demonstrated the difference between process availability and
state durability.

Without persistent storage, Kubernetes could quickly recreate the Postgres pod,
but the database itself was lost and required manual restoration from backup.

Adding a PersistentVolumeClaim allowed the database to survive pod replacement,
reduced measured recovery time, and removed the manual restore requirement for
ordinary pod failures.

The automated backup CronJob provides an additional recovery mechanism by
creating frequent independent PostgreSQL backups and retaining the five most
recent copies.
