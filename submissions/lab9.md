# Lab 9 — Stateful Services & DB Reliability
**Student:** Valerii Tiniakov
**Group:** B24-SD-03

## Task 1 — Migrations & Backup/Restore (6 pts)

### 9.7: Proof of Work

**1. Alembic history:**
```bash
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab9)
$alembic history
fe19828ebd08 -> 2346b9fa9878 (head), add email column to events
<base> -> fe19828ebd08, baseline - pre-existing schema
(.venv)


```

**2. Schema verification (\d events):**
```text
Table "public.events"
    Column     |          Type          | Collation | Nullable |               Default
---------------+------------------------+-----------+----------+------------------------------------
 id            | integer                |           | not null | nextval('events_id_seq'::regclass)
 name          | text                   |           | not null |
 venue         | text                   |           | not null |
 event_date    | timestamp with time zone |         | not null |
 total_tickets | integer                |           | not null |
 price_cents   | integer                |           | not null |
 email         | character varying(255) |           |          |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
Referenced by:
    TABLE "orders" CONSTRAINT "orders_event_id_fkey" FOREIGN KEY (event_id) REFERENCES events(id)

```

**3. Migration execution time:**
```bash
real    0m0.709s
user    0m0.000s
sys     0m0.015s
```

**4. Prometheus 5xx rate (Before vs After):**
```text
Before migration: 5xx last 1min: 0
After migration: 5xx last 1min: 0
```

**5. Backup verification:**
```bash
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab9)
$ ls -lh quickticket.dump
-rw-r--r-- 1 valer 197609 7.1K Jul 10 21:34 quickticket.dump
valer@VTLaptop MINGW64 ~/OneDrive/Рабочий стол/SRE-Intro (feature/lab9)
$ kubectl exec $POD -- pg_restore --list //tmp/backup.dump | head -25
;
; Archive created at 2026-07-10 18:34:48 UTC
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
220; 1259 16412 TABLE public alembic_version quickticket
218; 1259 16386 TABLE public events quickticket
217; 1259 16385 SEQUENCE public events_id_seq quickticket
3481; 0 0 SEQUENCE OWNED BY public events_id_seq quickticket
219; 1259 16394 TABLE public orders quickticket
3316; 2604 16389 DEFAULT public events id quickticket
3474; 0 16412 TABLE DATA public alembic_version quickticket
3472; 0 16386 TABLE DATA public events quickticket
3473; 0 16394 TABLE DATA public orders quickticket
3482; 0 0 SEQUENCE SET public events_id_seq quickticket
(.venv)

```

**6. Row counts (Before / After Drop / After Restore):**
```text
Before disaster: events = 5, orders = 50
After DROP: API fails, table doesn't exist
After restore: events = 5, orders = 50
```

**7. RPO Analysis:**
**Answer:** 
Currently, our RPO (Recovery Point Objective) is 24 hours, as we perform manual pg_dump backups once a day. This means that in the event of a failure, we would lose all data (orders, tickets) generated since the last backup. To improve this, we need to automate backups using a CronJob (to run them, for example, every 5–10 minutes) or set up continuous WAL log archiving, which would bring our RPO down to near zero.

---

## Task 2 — Disaster Recovery Under Load (4 pts)

### 9.9: RTO and RPO Calculation

**1. Timestamps:**
```text
Disaster at:      21:42:52
New pod ready:    21:43:59
Restored:         21:44:43
App fully up:     21:45:05
```

**2. Actual RTO:**
133 seconds (2m 13s)

**3. RPO Gap:**
```text
Orders before disaster: 50
Orders after restore: 50
Lost orders (RPO gap): 0
```

**4. Prometheus error-rate curve:**
```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783709124.404,"0.40002240140809725"]}]}}
```

**5. Root Cause Analysis:**
**Answer:** The new pod started with an empty database because a PersistentVolumeClaim (PVC) was not configured in k8s/postgres.yaml. The database was using the pod's ephemeral storage, which is completely wiped upon termination. To eliminate this vulnerability, we need to add a PVC and mount it to the PostgreSQL data directory, which we will do in the bonus task.

---

## Bonus Task — Persistent Storage + Automated Backup CronJob (2 pts)

### B.3: Proof of work

**1. k8s/postgres.yaml Diff (PVC added):**
```yaml
      containers:
        - name: postgres
          volumeMounts:
            - { name: data, mountPath: /var/lib/postgresql/data }
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: postgres-data
# ...
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-data
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 1Gi
```

**2. Re-measured RTO (with PVC):**
```text
Disaster at: 21:50:23
New pod ready (data intact): 21:50:40
App fully up: 21:51:46
New RTO: 83 seconds (data persisted automatically)
```

**3. k8s/backup-cronjob.yaml Contents:**
```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: backup-data
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 1Gi
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: pg-backup
spec:
  schedule: "*/5 * * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: backup
            image: postgres:17-alpine
            env:
            - name: PGPASSWORD
              value: quickticket
            command:
            - /bin/sh
            - -c
            - |
              echo "Starting backup..."
              pg_dump -h postgres -U quickticket -Fc quickticket > /backup/backup-$(date +%s).dump
              echo "Rotating old backups (keeping last 5)..."
              ls -t /backup/*.dump | tail -n +6 | xargs -r rm -f
              echo "Current backup directory contents:"
              ls -la /backup
            volumeMounts:
            - name: backup-storage
              mountPath: /backup
          restartPolicy: OnFailure
          volumes:
          - name: backup-storage
            persistentVolumeClaim:
              claimName: backup-data
```

**4. Logs from manual-7 (showing rotation):**
```text
Starting backup...
Rotating old backups (keeping last 5)...
Current backup directory contents:
total 68
drwxrwxrwx    2 root     root          4096 Jul 10 19:00 .
drwxr-xr-x    1 root     root          4096 Jul 10 19:00 ..
-rw-r--r--    1 root     root          9597 Jul 10 19:00 backup-1783710007.dump
-rw-r--r--    1 root     root          9597 Jul 10 19:00 backup-1783710012.dump
-rw-r--r--    1 root     root          9597 Jul 10 19:00 backup-1783710017.dump
-rw-r--r--    1 root     root          9597 Jul 10 19:00 backup-1783710022.dump
-rw-r--r--    1 root     root          9597 Jul 10 19:00 backup-1783710028.dump
```

**5. Backup directory contents (ls -la):**
```text
total 68
drwxrwxrwx    2 root     root          4096 Jul 10 19:00 .
drwxr-xr-x    1 root     root          4096 Jul 10 19:01 ..
-rw-r--r--    1 root     root          9597 Jul 10 19:00 backup-1783710007.dump
-rw-r--r--    1 root     root          9597 Jul 10 19:00 backup-1783710012.dump
-rw-r--r--    1 root     root          9597 Jul 10 19:00 backup-1783710017.dump
-rw-r--r--    1 root     root          9597 Jul 10 19:00 backup-1783710022.dump
-rw-r--r--    1 root     root          9597 Jul 10 19:00 backup-1783710028.dump
```