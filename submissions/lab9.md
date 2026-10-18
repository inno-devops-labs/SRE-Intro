# Lab 9 --- PostgreSQL Migrations, Recovery and Backups

## Environment

-   Kubernetes cluster: k3d, namespace `default`
-   Database: PostgreSQL 17 (`quickticket`)
-   Application components: `gateway`, `events`, `payments`
-   Monitoring: Prometheus in namespace `monitoring`
-   Working branch: `feature/lab9`

## Task 1 --- Schema migration and database recovery

### Alembic migrations

Initialized Alembic and added a baseline revision for the existing
schema:

-   `migrations/versions/8f41e8ca0ca6_baseline_pre_existing_schema.py`
-   `migrations/versions/7e0f8d34fcbe_add_email_column_to_events.py`

The baseline was stamped onto the existing database. The second
migration adds a nullable `email VARCHAR(255)` column to `events`; its
downgrade removes the column.

Command used:

``` bash
.venv/bin/alembic upgrade head
```

The migration completed successfully in approximately **0.498 seconds**.
The column was verified with `\d events`. The mixed-load deployment had
**2/2 ready replicas** during the migration test.

### Backup, failure injection and recovery

Created a PostgreSQL custom-format dump using `pg_dump -Fc`.

-   Backup file: `/tmp/quickticket.dump`
-   File size: approximately **7.1 KiB**
-   Verified the dump contents using `pg_restore --list`.
-   Counts before the destructive test: **5 events and 50 orders**.

Dropped the `orders` table to simulate a database failure. The gateway
`/events` smoke test returned **HTTP 502** while the database schema was
incomplete.

Restored the dump with `pg_restore --clean --if-exists`. Afterwards:

  Check                          Result
  -------------------------- ----------
  Events after restore                5
  Orders after restore               50
  `/events` before restore     HTTP 502
  `/events` after restore      HTTP 200

The restore recovered both tables and their data.

### Prometheus observation

A later Prometheus query for the rate of gateway 5xx responses over a
one-minute window returned approximately **0.03636 errors/second**. This
was a point-in-time sample, not a complete incident time series, and
does not by itself establish causation.

### RPO discussion

The dump contained the 5 events and 50 orders observed before the
destructive test, and those records were restored. The practical RPO
depends on the age of the latest valid backup at the time of failure.
Periodic backups can lose changes made after the most recent backup.

## Task 2 --- PostgreSQL pod deletion without persistent storage

Before persistent storage was configured, the PostgreSQL pod was
force-deleted to simulate pod loss.

-   Recorded incident start time: **09:54:57** (terminal time; timezone
    was not separately recorded).
-   Kubernetes created a replacement PostgreSQL pod and it became Ready.
-   The replacement database had no application tables; `\dt` reported
    no relations.
-   Restored the custom-format dump.
-   Verified **5 events and 50 orders** after recovery.
-   Restarted the `events` deployment and verified that its rollout
    completed successfully.

This demonstrated that a replacement pod using ephemeral storage starts
without the previous database files. The backup allowed the database to
be recovered.

**Timing limitation:** Exact timestamps for replacement pod readiness,
restore completion, and application recovery were not captured
individually during the first no-PVC incident. Therefore, an exact
numerical RTO cannot be claimed from the recorded evidence. The incident
start was recorded as `09:54:57`; recovery was completed successfully.

The backup was available from before the incident, and the original 5
events and 50 orders were restored. The exact RPO interval cannot be
calculated because the backup and recovery timestamps were not all
recorded on a common clock.

## Bonus --- Persistent storage and automated backups

### PostgreSQL PVC

Updated `k8s/postgres.yaml` to define a `postgres-data`
PersistentVolumeClaim (1 GiB, ReadWriteOnce), mounted at
`/var/lib/postgresql/data`, with `PGDATA` set to
`/var/lib/postgresql/data/pgdata`.

After restoring the database onto the PVC, deleted the PostgreSQL pod
and waited for its replacement to become Ready.

  Check                            Result
  ------------------------------ --------
  Events after pod replacement          5
  Orders after pod replacement         50

The records remained available without running `pg_restore` again. This
confirms that the PVC persisted the database files across pod
replacement.

### Automated backup CronJob

Added `k8s/backup-cronjob.yaml` with:

-   Schedule: `*/5 * * * *`
-   Image: `postgres:17-alpine`
-   Custom-format `pg_dump` backups
-   Backup path: `/backups/quickticket_<UTC timestamp>.dump`
-   Shared PVC: `postgres-backups`
-   `concurrencyPolicy: Forbid`
-   Successful and failed Job history limits: 3 each
-   Rotation that removes dumps older than the five most recent files

Applied `labs/lab9/backup-storage.yaml`. The `postgres-backups` PVC
reached `Bound`, and `backup-inspector` became Ready.

Ran seven manual Jobs based on the CronJob. All seven completed
successfully. Logs confirmed backup creation and removal of old files.
The backup inspector showed exactly five remaining dumps:

-   `quickticket_20261012T070109Z.dump`
-   `quickticket_20261012T070113Z.dump`
-   `quickticket_20261012T070116Z.dump`
-   `quickticket_20261012T070121Z.dump`
-   `quickticket_20261012T070125Z.dump`

The two oldest dumps were removed by the rotation logic.

## Files added or changed

-   `alembic.ini`
-   `migrations/versions/8f41e8ca0ca6_baseline_pre_existing_schema.py`
-   `migrations/versions/7e0f8d34fcbe_add_email_column_to_events.py`
-   `k8s/postgres.yaml`
-   `k8s/backup-cronjob.yaml`
-   `submissions/lab9.md`

## Conclusion

The schema migration completed successfully under mixed load. A
destructive database test demonstrated the need for backups, and
restoring the custom-format dump recovered both application tables and
their records. Without a PVC, deleting the PostgreSQL pod resulted in an
empty replacement database. With a PVC, the 5 events and 50 orders
survived pod replacement. The scheduled backup CronJob successfully
generated dumps and retained exactly the five newest files after seven
successful runs.

## Submission checklist

-   [ ] Review and commit the migration, Kubernetes manifests, and
    report.
-   [ ] Push branch `feature/lab9`.
-   [ ] Open a pull request from `feature/lab9` and include its URL in
    the course submission.
