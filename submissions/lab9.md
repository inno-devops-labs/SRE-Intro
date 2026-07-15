# Lab 9 — Stateful Services & DB Reliability

---

## Task 1 — Migrations & Backup/Restore

### 1. `alembic history` (baseline + email migration)

```
2ec7abf68626 -> 9cc3b392ea06 (head), add email column to events
<base> -> 2ec7abf68626, baseline - pre-existing schema
```

### 2. `\d events` – schema after migration (new `email` column)

```
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

### 3. Migration elapsed time (`time alembic upgrade head`)

```
INFO  [alembic.runtime.migration] Running upgrade 2ec7abf68626 -> 9cc3b392ea06, add email column to events
real    0m0.154s
user    0m0.056s
sys     0m0.041s
```
The nullable column addition ran in under 1 second – a metadata‑only change that does not block reads or writes.

### 4. 5xx errors before and after migration

- **Before:** `5xx last 1min: 0`
- **After:**  `5xx last 1min: 0`

Zero additional errors – the migration was completely transparent to live traffic.

### 5. Backup validity

```text
$ ls -lh /tmp/quickticket.dump
-rw-r--r--  1 root root 7.2K Jul  10 21:44 /tmp/quickticket.dump

$ file /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0

$ kubectl exec deploy/postgres -- pg_restore --list /tmp/backup.dump | head -25
;
; Archive created at 2026-07-10 21:44:35 UTC
;     dbname: quickticket
;     TOC Entries: 18
;     Compression: gzip
;     Dump Version: 1.16-0
;     Format: CUSTOM
...
220; 1259 16407 TABLE public alembic_version quickticket
218; 1259 16387 TABLE public events quickticket
219; 1259 16386 SEQUENCE public events_id_seq  quickticket
```

The dump is a valid PostgreSQL custom‑format archive.

### 6. Row counts – before disaster / after DROP / after restore

| Phase               | events | orders | API /events   |
|---------------------|--------|--------|---------------|
| Before disaster     | 5      | 50     | 200 OK        |
| After `DROP TABLE orders CASCADE` | 5 | — (table gone) | 502 Bad Gateway |
| After `pg_restore`  | 5      | 50     | 200 OK        |

All data fully recovered; no records lost from the backup.

### 7. RPO of a single `pg_dump` and improvements

**Current RPO:** equals the age of the last manual dump. If a disaster occurs just before the next scheduled dump, all writes since the last backup are lost. With a single manual dump, the RPO is effectively unbounded.

**Improvements:**
1. **Automated CronJob (every 5 minutes)** – reduces maximum RPO to ≤ 5 minutes.
2. **Persistent Volume (PVC)** – data survives pod restarts, giving RPO ≈ 0 for pod‑level failures.
3. **Continuous WAL archiving + PITR** – enables recovery to any point in time, approaching zero RPO.

---

## Task 2 — Disaster Recovery Under Load (no PVC)

### Timestamps

```
Disaster (pod killed): 21:50:47
New pod Ready:         21:50:51
Restore complete:      21:50:51
App fully up (events restarted): 21:51:03
```

### RTO

```
Actual RTO = 21:51:03 – 21:50:47 = 16 seconds
```

### RPO gap

- Orders before disaster: `50`
- Orders after restore: `50`
- Record gap: `0` (backup was taken seconds before the kill)

### Prometheus error rate during incident

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total{status=~"5.."}[30s]))'
```
Output: `1.48` (spike to ~1.48 5xx req/s while the database was absent)

### Why was the new pod empty? How to eliminate this failure mode?

The Postgres Deployment had **no PersistentVolumeClaim**. All data lived on the container’s ephemeral filesystem; when the pod was deleted, the entire database was permanently destroyed. The replacement pod started with a fresh, empty `initdb`.

**Fix:** Mount a `PersistentVolumeClaim` on `/var/lib/postgresql/data`. This decouples storage from the pod lifecycle – a new pod remounts the same volume and recovers to the last committed transaction in ~10 seconds with zero data loss (see Bonus Task).

---

## Bonus Task — Persistent Storage + Automated Backup CronJob

### B.1 – Diff of `k8s/postgres.yaml` (PVC added)

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
 ---
 containers:
   env:
+    - name: PGDATA
+      value: /var/lib/postgresql/data/pgdata
+  volumeMounts:
+    - name: data
+      mountPath: /var/lib/postgresql/data
+volumes:
+  - name: data
+    persistentVolumeClaim:
+      claimName: postgres-data
```

### B.1 – RTO with PVC (re‑run of disaster test)

```
Pod kill:       21:51:34
New pod Ready:  21:51:38   (data already present, no pg_restore needed)
```
**RTO with PVC: ~4 seconds** – the pod restart alone restores full service.

### B.2 – `k8s/backup-cronjob.yaml`

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
                  cd /backups
                  filename="quickticket_$(date -u +%Y%m%dT%H%M%SZ).dump"
                  pg_dump -Fc -f "$filename"
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

### B.2 – Rotation proof (manual-7 logs)

```
writing /backups/quickticket_20260710T215334Z.dump
retaining the 5 newest quickticket dumps
removed 'quickticket_20260710T215318Z.dump'
total 40K
-rw-r--r-- … quickticket_20260710T215322Z.dump
-rw-r--r-- … quickticket_20260710T215325Z.dump
-rw-r--r-- … quickticket_20260710T215328Z.dump
-rw-r--r-- … quickticket_20260710T215331Z.dump
-rw-r--r-- … quickticket_20260710T215334Z.dump
```

### B.2 – Final backup listing (exactly 5 files after 7 runs)

```text
$ kubectl exec deployment/backup-inspector -- ls -la /backups
total 48
-rw-r--r-- 1 root root 7297 Jul  10 21:53 quickticket_20260710T215322Z.dump
-rw-r--r-- 1 root root 7297 Jul  10 21:53 quickticket_20260710T215325Z.dump
-rw-r--r-- 1 root root 7297 Jul  10 21:53 quickticket_20260710T215328Z.dump
-rw-r--r-- 1 root root 7297 Jul  10 21:53 quickticket_20260710T215331Z.dump
-rw-r--r-- 1 root root 7297 Jul  10 21:53 quickticket_20260710T215334Z.dump
```

---

## PR Checklist

- [x] Task 1 – Alembic migration under load + pg_dump/pg_restore cycle completed  
- [x] Task 2 – Disaster recovery RTO/RPO measurement  
- [x] Bonus  – PVC added + CronJob backup with rotation verified