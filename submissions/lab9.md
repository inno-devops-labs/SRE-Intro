# Lab 9 — Stateful services and database reliability

## Task 1 — Migrations and backup/restore

### 9.1: Initialize Alembic

I created `alembic.ini` and `migrations/` with Alembic 1.18.4.

### 9.2: Baseline the existing schema

I stamped the existing tables with revision `71d59833c48b`. The baseline revision is empty because the tables already existed.

### 9.3: Add the real migration

Revision `f5d36c02fa4b` adds a nullable `email` column.

```text
$ alembic history
71d59833c48b -> f5d36c02fa4b (head), add email column to events
<base> -> 71d59833c48b, baseline - pre-existing schema

$ alembic current
f5d36c02fa4b (head)
```

### 9.4: Migration during live traffic

`deployment/mixedload` had 2 ready replicas. Prometheus reported `5xx last 1min: 0` before the upgrade and `0` after it. The host measured **1.655 seconds** for the full Alembic command, including Python startup and the connection. The actual SQL step was shorter but I did not time it alone.

```text
Running upgrade 71d59833c48b -> f5d36c02fa4b, add email column to events
Alembic elapsed: 1.655 s

Table "public.events"
Column          Type                    Nullable
id              integer                 not null
name            text                    not null
venue           text                    not null
event_date      timestamp with time zone not null
total_tickets   integer                 not null
price_cents     integer                 not null
email           character varying(255)  yes
```

A nullable column let old rows stay valid. I saw no extra 5xx during this change.

### 9.5: `pg_dump` backup

I made a custom-format dump in the Postgres pod and copied it to [quickticket.dump](evidence/lab9/quickticket.dump). It was **7,278 bytes**. `pg_restore --list` accepted it:

```text
Archive created at 2026-10-12 01:56:56 UTC
dbname: quickticket
TOC Entries: 18
Format: CUSTOM
Dumped from database version: 17.11
TABLE public alembic_version
TABLE public events
TABLE public orders
TABLE DATA public alembic_version
TABLE DATA public events
TABLE DATA public orders
```

### 9.6: Drop and restore

| Check | Events | Orders | API `/events` |
|---|---:|---:|---:|
| Before `DROP TABLE orders CASCADE` | 5 | 50 | Working |
| After DROP | 5 | Table missing | 502 |
| After `pg_restore --clean --if-exists` | 5 | 50 | 200 |

The read API also failed because its query joins `events` with `orders`. The restore brought back both the table and its rows. A single `pg_dump` gives an RPO as large as the time since that dump. If it is one hour old, up to one hour of writes can be lost. A PVC protects data from a normal pod restart; five-minute dumps reduce the backup gap. For a smaller RPO after volume loss, I would add PostgreSQL WAL archiving and point-in-time recovery.

### 9.7: Proof of work

The two revision IDs, column output, elapsed time, Prometheus values, valid dump TOC, three row-count states, and RPO answer are above. The migration files and dump are included in this working tree.

## Task 2 — Disaster recovery under load

### 9.8: Kill Postgres and recover

I created one paid order after the dump, so the count rose from 50 in the dump to 51 before the test. Then I force-deleted the Postgres pod. The new pod had **no tables**. Kubernetes marked the pod Ready before PostgreSQL accepted connections; `pg_restore` at that first Ready signal failed, so I waited for `pg_isready` and retried. This is an important readiness gap.

| Phase | Local time, 12 Oct 2026 |
|---|---|
| Disaster, pod deleted | 04:57:58.370 |
| New pod marked Ready | 04:57:59.395 |
| Database confirmed ready | By about 04:58:24 |
| Dump restored | 04:58:33.493 |
| First confirmed HTTP 200 | 04:59:09.014 |

### 9.9: Calculate RTO and RPO

**Observed RTO:** 70.6 seconds from deletion to the first confirmed good `/events` response. This includes a wait after restore and a restart of `deployment/events`, which had stale DB connections. **Observed RPO:** the dump was about 61 seconds old at deletion. Orders went from **51 before** to **50 after**, so the loss was **1 order**.

Prometheus `sum(rate(gateway_requests_total{status=~"5.."}[30s]))` around the incident (errors per second):

```text
04:57:45  0.04
04:58:00  0.04
04:58:15  1.00
04:58:30  3.77
04:58:45  3.80
04:59:00  0.44
04:59:15  0.00
04:59:30  0.00
```

The new pod was empty because the old Deployment stored Postgres data in its container filesystem. A new pod got a new empty filesystem. A PVC removes that failure mode for a pod restart. It does not replace backups for bad writes, volume loss, or a lost node.

## Bonus Task — PVC and automated backups

### Persistent storage and second recovery test

I added `postgres-data` (1 GiB, ReadWriteOnce), set `PGDATA` to a subdirectory, and mounted the claim at `/var/lib/postgresql/data`. The new PVC started empty, so I restored the valid dump onto it before the second failure test. The relevant diff is:

```diff
+            - name: PGDATA
+              value: /var/lib/postgresql/data/pgdata
+          volumeMounts:
+            - name: data
+              mountPath: /var/lib/postgresql/data
+      volumes:
+        - name: data
+          persistentVolumeClaim:
+            claimName: postgres-data
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

The second pod deletion was at **05:00:11.885**. The new database accepted connections at **05:00:15.085**, and the first confirmed API 200 was at **05:01:14.240**. The observed end-to-end RTO was **62.4 seconds**. Orders were **50 before and 50 after**; the RPO gap was **zero rows**, and I did **not** run `pg_restore` for this restart. The DB itself recovered in 3.2 seconds. The slower API recovery came from stale connections and gateway readiness, so the end-to-end improvement was only 8.2 seconds in this run. These issues need a separate fix.

### Backup CronJob and retention

I applied `labs/lab9/backup-storage.yaml` and wrote `k8s/backup-cronjob.yaml`. This is its content:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: postgres-backup
spec:
  schedule: "*/5 * * * *"
  timeZone: Etc/UTC
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 1
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: backup
              image: postgres:17-alpine
              env:
                - { name: PGHOST, value: postgres }
                - { name: PGUSER, value: quickticket }
                - { name: PGDATABASE, value: quickticket }
                - { name: PGPASSWORD, value: quickticket }
              command: ["/bin/sh", "-ec"]
              args:
                - |
                  cd /backups
                  name="quickticket_$(date -u +%Y%m%dT%H%M%S).dump"
                  pg_dump -Fc -f "$name"
                  echo "created $name"
                  ls -1t quickticket_*.dump | tail -n +6 | xargs -r rm -v
              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

All seven manual Jobs completed. `manual-7` logged:

```text
created quickticket_20261012T020206.dump
removed 'quickticket_20261012T020146.dump'
```

Right after the seven runs, `ls -la /backups` showed exactly these **five** files:

```text
quickticket_20261012T020150.dump  7270 bytes
quickticket_20261012T020154.dump  7270 bytes
quickticket_20261012T020158.dump  7270 bytes
quickticket_20261012T020202.dump  7270 bytes
quickticket_20261012T020206.dump  7270 bytes
```

The normal schedule also ran at 02:05 UTC later and kept the count at five. The CronJob and both PVCs remain in the local cluster for checking.

### Proof of work

The PVC diff, second RTO, full CronJob, `manual-7` rotation log, and five-file listing are above.
