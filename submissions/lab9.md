# Lab 9 — Stateful Services & DB Reliability

## Task 1 — Migrations and backup/restore

### Alembic migration under load

Alembic was initialized against the k3d Postgres database. The existing schema was recorded as baseline revision `4ba79a665e06`. Revision `29f225b42ece` added nullable `events.email VARCHAR(255)`.

`alembic history` showed both revisions. `alembic upgrade head` completed in **0.525 seconds** while the Lab 8 mixed workload was running. Prometheus recorded **0 5xx responses in the minute before and after the migration**. The new column was confirmed in `\d events`.

Evidence: [Alembic baseline](lab9-evidence/alembic-baseline.txt), [migration run](lab9-evidence/migration-run.txt), [load baseline](lab9-evidence/load-start.txt).

### Backup and restore test

A custom-format `pg_dump` produced a non-empty 135 KB archive. `pg_restore --list` showed the `events`, `orders`, and `alembic_version` tables and their data.

Immediately before dropping `orders`, the database contained 5 events and 3,793 orders. After `DROP TABLE orders CASCADE`, the table was absent and `/events` returned HTTP 502. Restoring the earlier backup brought the table back; the database then contained 5 events and 3,604 orders, and `/events` returned HTTP 200.

The backup was taken earlier while mixedload was adding orders. The difference between the count before the drop and the restored snapshot shows that writes made after a dump's snapshot can be lost.

Evidence: [backup details](lab9-evidence/backup-before-drop.txt), [archive TOC](lab9-evidence/backup-toc.txt), [drop and restore cycle](lab9-evidence/backup-restore-cycle.txt).

### RPO

A single manual `pg_dump` can only recover data through the time represented by its snapshot. In the disaster test, the pre-disaster count was 4,834 orders and the restored database had 4,641: an observed gap of **193 orders**. The backup was about 42 seconds old when the Postgres pod was deleted. The five-minute CronJob reduces the backup interval, but does not provide continuous point-in-time recovery.

## Task 2 — Disaster recovery under load

The Postgres Deployment initially had no volume mount or PVC. After force-deleting its pod, Kubernetes created a replacement, but the new pod had no database tables. The first restore attempt ran before PostgreSQL was accepting SQL connections and failed. After PostgreSQL started, I restored the backup, restarted Events, and verified the API.

| Phase | Time (Europe/Moscow) | Result |
|---|---|---|
| Postgres pod deletion | 04:59:35.818 | Old pod force-deleted |
| Replacement pod Kubernetes Ready | 04:59:37.522 | PostgreSQL was not yet accepting connections |
| First restore attempt | 04:59:39.004 | Failed: no local PostgreSQL socket |
| Successful restore | Exact time not captured | Completed after PostgreSQL became available |
| Application recovered | 05:06:09.541 | `GET /events` returned HTTP 200 |

The valid measured RTO, from pod deletion to successful API verification, was **394 seconds**. The earlier 16.34-second figure was invalid: its restore had failed, and the health check did not verify database recovery.

Prometheus showed a 5xx rate of 0 before the incident, rising to about 6 responses per second during the outage, then returning to 0 by 05:04:30. The API was verified at 05:06:09.

Evidence: [disaster recovery timeline and counts](lab9-evidence/disaster-recovery.txt), [Prometheus incident curve](lab9-evidence/prometheus-disaster-range.txt).

**Why was the replacement database empty?** Postgres stored data in the pod's ephemeral filesystem. Deleting the pod deleted that storage. A PersistentVolumeClaim keeps the database files outside the pod lifecycle.

## Bonus — Persistent Postgres storage

Added a 1 GiB `postgres-data` PVC using the cluster's `local-path` StorageClass. The Postgres container sets `PGDATA=/var/lib/postgresql/data/pgdata` and mounts the claim at `/var/lib/postgresql/data`. The Deployment uses the `Recreate` strategy to avoid two Postgres pods trying to mount the single-writer claim during an update.

Key manifest changes:

```diff
+apiVersion: v1
+kind: PersistentVolumeClaim
+metadata:
+  name: postgres-data
+spec:
+  accessModes: [ReadWriteOnce]
+  resources:
+    requests:
+      storage: 1Gi
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

The Postgres pod used the PVC at `/var/lib/postgresql/data` and set `PGDATA=/var/lib/postgresql/data/pgdata`.

After restoring the database to the PVC, I deleted the Postgres pod again. The replacement retained the data: 5 events and 6,436 orders. `GET /events` returned HTTP 200. No `pg_restore` was needed. Measured RTO through the final API check was **15.59 seconds**, compared with 394 seconds for backup-based recovery.

Evidence: [PVC restore](lab9-evidence/pvc-restore.txt), [PVC pod restart test](lab9-evidence/pvc-pod-restart.txt).

## Bonus — Automated backup CronJob

Created `k8s/backup-cronjob.yaml`. The CronJob runs every five minutes, creates custom-format dumps with UTC timestamps on the `postgres-backups` PVC, and retains the five newest files.

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
      backoffLimit: 2
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: backup
              image: postgres:17-alpine
              command: ["/bin/sh", "-c"]
              args:
                - |
                  set -eu
                  timestamp=$(date -u +%Y%m%dT%H%M%SZ)
                  filename="quickticket_${timestamp}.dump"
                  pg_dump -Fc -f "/backups/${filename}"
                  echo "Created '${filename}'"

                  cd /backups
                  old_files=$(ls -1t quickticket_*.dump | tail -n +6)
                  if [ -n "$old_files" ]; then
                    printf '%s\n' "$old_files" |
                    while IFS= read -r file; do
                      rm -f -- "$file"
                      echo "removed '${file}'"
                    done
                  fi
              env:
                - name: PGHOST
                  value: postgres
                - name: PGUSER
                  value: quickticket
                - name: PGDATABASE
                  value: quickticket
                - name: PGPASSWORD
                  value: quickticket
              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

Seven manual jobs completed. `manual-6` and `manual-7` logged removal of the oldest dumps. The backup PVC contained exactly five dump files afterward.

Evidence: [CronJob run logs](lab9-evidence/backup-cronjob-runs.txt), [retention check](lab9-evidence/backup-retention.txt).

## Recovery state

Postgres with PVC and the application were healthy after the experiments. `mixedload` was used to generate test traffic.
