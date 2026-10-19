# Lab 9 Submission

## Task 1 — Alembic migration and backup/restore

### 1. Alembic history

```text
$ alembic history
30d1980bee5c (head) -> baseline - pre-existing schema
```

### 2. `\d events` after migration

```text
Table "public.events"
 Column        | Type                     | Collation | Nullable | Default
---------------+--------------------------+-----------+----------+---------
 id            | integer                 |           | not null | nextval('events_id_seq'::regclass)
 name          | text                     |           | not null |
 venue         | text                     |           | not null |
 event_date    | timestamp with time zone |           | not null |
 total_tickets | integer                 |           | not null |
 price_cents   | integer                 |           | not null |
 email         | character varying(255)  |           |          |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
```

### 3. `time alembic upgrade head`

```text
$ time alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade 30d1980bee5c -> 7ae0d8c4d5f2, add email column to events
real	0m0.182s
user	0m0.060s
sys	0m0.010s
```

### 4. Prometheus 5xx last 1m before/after migration

```text
before: 0
after:  2.1818181818181817
```

### 5. Backup validation (`ls` + `pg_restore --list`)

The CronJob writes dumps to `/backups` on the `postgres-backups` PVC, not to the host's `/tmp`. I copied the latest dump from the inspector pod for local access. The host's `pg_restore` is version 14 and cannot read the PostgreSQL 17 archive format, so I inspected it with the matching PostgreSQL 17 client in a temporary pod.

```text
$ kubectl exec deployment/backup-inspector -- ls -lh /backups
-rw-r--r-- 1 root root 5.3K Oct 12 04:55 quickticket_20261012T045501Z.dump

$ kubectl cp backup-inspector-5944758f5d-zl6jz:/backups/quickticket_20261012T045501Z.dump /tmp/quickticket_latest.dump
$ pg_restore --version
pg_restore (PostgreSQL) 14.24
$ pg_restore --list /tmp/quickticket_latest.dump
pg_restore: error: unsupported version (1.16) in file header

# Validated with PostgreSQL 17.11 pg_restore in a temporary postgres:17-alpine pod:
$ kubectl exec pg-restore-check -- pg_restore --list /tmp/quickticket_latest.dump | head -18
; Archive created at 2026-10-12 04:55:01 UTC
;     dbname: quickticket
;     TOC Entries: 18
;     Compression: gzip
;     Dump Version: 1.16-0
;     Format: CUSTOM
;     Dumped from database version: 17.11
;     Dumped by pg_dump version: 17.11
...
```

### 6. Row counts before drop, after drop, and after recovery

```text
before drop (last valid state):
 events=5
 orders=0

after drop (damaged live database):
 events=5
 orders table missing

after recovery (restored quickticket_20261012T050001Z.dump):
 events=5
 orders=0
```

### 7. RPO answer

The configured RPO target is **up to 5 minutes**, because the backup CronJob runs every five minutes. In the Task 2 run, the selected backup was created at 08:38:08 MSK and the simulated failure began at 08:39:21 MSK, so the time-based RPO was **1 minute 13 seconds**. The observed RPO gap was **0 orders**: there was 1 order before the failure and 1 after recovery. More frequent dumps or continuous WAL archiving/PITR would reduce the time-based recovery window.

---

## Task 2 — Disaster recovery details

The live Task 2 simulation temporarily removed the Postgres volume mount while keeping the actual `postgres-data` PVC intact as a safety copy. The fresh pod therefore initialized an empty database, matching the no-PVC failure mode without deleting the existing persistent volume. I restored the verified dump into that empty pod, confirmed the application, and then reapplied [k8s/postgres.yaml](../k8s/postgres.yaml) to return the cluster to PVC-backed storage.

### 9.8–9.9: No-PVC failure and restore results

```text
Backup used: lab9-task2-preincident-20261012T053808Z.dump
Backup created: 2026-10-12 08:38:08 MSK

T_KILL (disaster):      2026-10-12 08:39:21 MSK
T_READY (empty pod):    2026-10-12 08:39:36 MSK
T_RESTORED:             2026-10-12 08:40:16 MSK
T_APP_READY (/events 200): 2026-10-12 08:41:23 MSK
Actual RTO (T_APP_READY - T_KILL): 122 seconds

New pod before restore: Did not find any relations.
Orders before disaster: 1
Orders after restore:   1
RPO gap (orders lost):  0
Time-based RPO at disaster: 1 minute 13 seconds

Rows after restore:
events=5
orders=1
```

The new pod was empty because the simulated pod template had no volume mounted at `PGDATA`; its writable filesystem was new and ephemeral. In production, mount the `postgres-data` PVC at `/var/lib/postgresql/data` and use a `PGDATA` subdirectory so the database survives pod replacement.

### Prometheus 5xx-rate curve — no-PVC failure and restore

The following samples are from `sum(rate(gateway_requests_total{status=~"5.."}[30s]))` over the Task 2 incident window. Values are 5xx **requests per second**, not percentages.

| Time (MSK) | Event | 5xx rate (requests/s) |
| --- | --- | ---: |
| 08:38:51 | Before incident | 0.08 |
| 08:39:06 | Before incident | 0.08 |
| 08:39:21 | Postgres pod removed | 0 |
| 08:39:36 | Empty Postgres pod Ready | 0.9511 |
| 08:39:51 | Restore in progress | 1.6158 |
| 08:40:06 | Restore in progress | 0.5513 |
| 08:40:21 | Restore completed; peak rolling rate | 1.7165 |
| 08:40:36 | Events reconnecting | 1.1411 |
| 08:40:51 | Recovery | 0 |
| 08:41:06 | Recovery | 0.1600 |
| 08:41:21 | App probe approaching recovery | 0.1600 |
| 08:41:36 | Gateway `/events` returned 200 | 0 |
| 08:41:51 | Stable after recovery | 0 |

---

## Bonus task — PVC and CronJob

### Updated `k8s/postgres.yaml` diff

```diff
+ apiVersion: v1
+ kind: PersistentVolumeClaim
+ metadata:
+   name: postgres-data
+ spec:
+   accessModes: [ReadWriteOnce]
+   resources:
+     requests:
+       storage: 1Gi
+
 apiVersion: apps/v1
 kind: Deployment
 ...
+           env:
+             - name: PGDATA
+               value: /var/lib/postgresql/data/pgdata
+          volumeMounts:
+            - name: data
+              mountPath: /var/lib/postgresql/data
+      volumes:
+        - name: data
+          persistentVolumeClaim:
+            claimName: postgres-data
```

### PVC-backed recovery retest (Bonus B.1)

After the no-PVC simulation, I reapplied `k8s/postgres.yaml` and restarted Postgres with the bound `postgres-data` claim. No `pg_restore` was needed; the existing volume supplied the database files.

```text
Postgres pod removed:       2026-10-12 08:42:15 MSK
Postgres Ready with PVC:    2026-10-12 08:42:19 MSK
Database recovery time:     4 seconds
Events service Ready:       2026-10-12 08:43:02 MSK
Gateway GET /events:        HTTP 200 after the events rollout
Rows preserved:             events=5, orders=1
PVC:                        postgres-data Bound
```

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
                - -c
                - |
                  set -e
                  mkdir -p /backups
                  timestamp=$(date -u +%Y%m%dT%H%M%SZ)
                  dump_path="/backups/quickticket_${timestamp}.dump"
                  pg_dump -Fc -f "$dump_path" "$PGDATABASE"
                  ls -1t /backups/quickticket_*.dump | tail -n +6 | xargs -r rm -v
              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

### Seven manual runs and retention log

```text
Created `manual-1` through `manual-7` sequentially from the CronJob; all seven jobs completed successfully.
$ kubectl logs job/manual-7
removed '/backups/quickticket_20261012T054556Z.dump'
```

### Backups directory after retention

```text
$ kubectl exec deployment/backup-inspector -- ls -la /backups
quickticket_20261012T054601Z.dump
quickticket_20261012T054606Z.dump
quickticket_20261012T054612Z.dump
quickticket_20261012T054617Z.dump
quickticket_20261012T054622Z.dump
count: 5
```
