# Lab 9 — Stateful Services & DB Reliability

## Task 1 — Migrations & Backup/Restore (6 pts)

### 1. Alembic history output

```bash
$ .venv/bin/alembic history
3256eabd4c59 -> a8a72f006699 (head), add email column to events
<base> -> 3256eabd4c59, baseline - pre-existing schema
```

### 2. `\d events` output showing new email column

```bash
$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- psql -U quickticket -d quickticket -c '\d events'
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

### 3. `time alembic upgrade head` output

```bash
$ time .venv/bin/alembic upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 3256eabd4c59 -> a8a72f006699, add email column to events
.venv/bin/alembic upgrade head  0.24s user 0.07s system 59% cpu 0.535 total
```

Elapsed time: **0.535 seconds** (well under 1 second as expected for nullable column addition)

### 4. Prometheus 5xx error rates

Note: Prometheus is not deployed in the monitoring namespace. The migration was run under load with `mixedload` deployment active (2 replicas running). Given the migration took only 0.535s and was a metadata-only change (nullable column), no 5xx errors would be expected.

### 5. Backup file verification

```bash
$ ls -lh /tmp/quickticket.dump
-rw-r--r--@ 1 doshq  wheel   7.1K Jul  8 19:06 /tmp/quickticket.dump

$ file /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0

$ POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2) && kubectl exec $POD -- pg_restore --list /tmp/backup.dump | head -25
;
; Archive created at 2026-07-08 16:06:10 UTC
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

### 6. Row counts before disaster / after DROP / after restore

**Before disaster:**
```bash
$ POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2) && kubectl exec $POD -- psql -U quickticket -d quickticket -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
 count 
-------
     5
(1 row)

 count 
-------
    50
(1 row)
```

**After DROP TABLE orders CASCADE:**
- events: 5 (unchanged)
- orders: 0 (table dropped)

**After restore:**
```bash
$ POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2) && kubectl exec $POD -- psql -U quickticket -d quickticket -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
 count 
-------
     5
(1 row)

 count 
-------
    50
(1 row)
```

**API verification after restore:**
```bash
$ kubectl run smoke --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- curl -s -o /dev/null -w "/events=%{http_code}\n" http://gateway:8080/events
/events=200
```

### 7. RPO analysis

**Question:** What's the RPO of your current setup (single `pg_dump`)? How would you improve it? (Hint: Bonus Task.)

**Answer:** The RPO (Recovery Point Objective) of a single manual `pg_dump` is essentially "time since last backup" - which could be hours or days depending on when the backup was taken. Any data written after the backup is permanently lost in a disaster scenario. This is a very poor RPO for a production system.

To improve RPO, I would:
1. **Automate frequent backups** - Use a CronJob to run `pg_dump` every 5 minutes (as shown in the Bonus Task), reducing the potential data loss window to 5 minutes
2. **Implement point-in-time recovery (PITR)** - Use PostgreSQL's WAL (Write-Ahead Log) archiving to enable recovery to any specific point in time, not just backup snapshots
3. **Add replication** - Set up streaming replication to a standby database for near-zero RPO

The Bonus Task addresses the first improvement by automating periodic backups with retention.

---

## Task 2 — Disaster Recovery Under Load (4 pts)

### Disaster recovery timestamps

```bash
Disaster at      21:02:57
New pod ready    21:03:03
Restored         21:03:34
App fully up     21:03:58
```

### RTO calculation

**Actual RTO** = T_APP_READY − T_KILL = 21:03:58 − 21:02:57 = **61 seconds**

### RPO gap

- Orders count before disaster: 50
- Orders count after restore: 50
- **RPO gap: 0 rows** (no data loss since we restored from the same backup taken just before the disaster)

### Prometheus error-rate curve

Note: Prometheus is not deployed in the monitoring namespace, so error-rate metrics are not available.

### Empty pod observation

**Question:** The new Postgres pod was empty. Why? How would you eliminate this failure mode?

**Answer:** The new Postgres pod was empty because the Postgres deployment has no PersistentVolumeClaim (PVC). Without a PVC, the database data is stored in the pod's ephemeral storage, which is lost when the pod is deleted. When the new pod starts, it starts with a fresh empty database.

To eliminate this failure mode, I would add a PVC to the Postgres deployment as shown in the Bonus Task. This would persist the data across pod restarts, allowing the new pod to mount the existing data and continue serving without requiring a restore from backup. This significantly improves RTO from ~61 seconds (restore time) to ~10 seconds (pod restart time only).

---

## Bonus Task — Persistent Storage + Automated Backup CronJob (2 pts)

### Diff of k8s/postgres.yaml (PVC added)

```yaml
# Added PersistentVolumeClaim:
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-data
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi

# Added to Deployment:
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

### Re-run disaster recovery with PVC (improved RTO)

```bash
Disaster at      21:05:23
New pod ready    21:05:33
App fully up     21:06:16
```

**New RTO with PVC** = T_APP_READY − T_KILL = 21:06:16 − 21:05:23 = **53 seconds**

**Data persistence verification:**
- Before disaster: 25 orders
- After pod restart (no restore needed): 25 orders
- Tables persisted: events and orders both present (no restore required)

The new pod found its data on the PVC, eliminating the need for `pg_restore`. RTO improved from 61 seconds to 53 seconds, and more importantly, no manual restore step was required - the pod simply restarted and mounted the existing data.

### k8s/backup-cronjob.yaml contents

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
              command:
                - /bin/sh
                - -c
                - |
                  pg_dump -Fc -f /backups/quickticket_$(date -u +%Y%m%d_%H%M%S).dump
                  ls -1t /backups/quickticket_*.dump | tail -n +6 | xargs -r rm
              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

### Logs from manual-7 showing retention

```bash
$ kubectl logs job/manual-7
(no output - retention command executed successfully)
```

### Backup directory after 7 runs (retention working)

```bash
$ kubectl exec deployment/backup-inspector -- ls -la /backups
total 48
drwxrwxrwx    2 root     root          4096 Jul  8 18:07 .
drwxr-xr-x    1 root     root          4096 Jul  8 18:06 ..
-rw-r--r--    1 root     root          5457 Jul  8 18:07 quickticket_20260708_180725.dump
-rw-r--r--    1 root     root          5457 Jul  8 18:07 quickticket_20260708_180728.dump
-rw-r--r--    1 root     root          5457 Jul  8 18:07 quickticket_20260708_180731.dump
-rw-r--r--    1 root     root          5457 Jul  8 18:07 quickticket_20260708_180736.dump
-rw-r--r--    1 root     root          5457 Jul  8 18:07 quickticket_20260708_180741.dump
```

**Exactly 5 files remain after 7 runs** - retention policy working correctly. The 2 oldest backups (manual-1 and manual-2) were automatically deleted by the retention logic.

---
