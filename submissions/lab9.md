# Lab 9 — Stateful Services & DB Reliability

## Task 1 — Migrations & Backup/Restore

### 9.1–9.2: Alembic Setup & Baseline

```bash
alembic init migrations
# sqlalchemy.url = postgresql://quickticket:quickticket@localhost:5432/quickticket
alembic revision -m "baseline - pre-existing schema"
alembic stamp head
```

`alembic history`:

```
e2d350f36fe7 -> 44e9e44ac7de (head), add email column to events
<base> -> e2d350f36fe7, baseline - pre-existing schema
```

### 9.3–9.4: Migration Under Load

`\d events` after migration — new `email` column added:

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
```

`time alembic upgrade head`:

```
alembic upgrade head  0.20s user 0.04s system 89% cpu 0.265 total
```

**5xx error rate — before/after (under mixedload traffic):**

```
5xx before: 0
5xx after: 0
```

The migration completed in 265ms with zero additional errors — confirming a nullable column add is a safe, non-blocking, metadata-only change in PostgreSQL.

### 9.5: pg_dump Backup

```
-rw-rw-r-- 1 amina amina 7.2K Jul 11 17:20 /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
```

`pg_restore --list` (valid TOC, 18 entries):

```
; Archive created at 2026-07-11 14:20:18 UTC
;     dbname: quickticket
;     TOC Entries: 18
;     Format: CUSTOM
220; 1259 16412 TABLE public alembic_version quickticket
218; 1259 16390 TABLE public events quickticket
219; 1259 16398 TABLE public orders quickticket
...
```

### 9.6: Data Loss Simulation & Restore

| Stage | events | orders | API `/events` |
|-------|-------:|-------:|---------------|
| Before disaster | 5 | 50 | — |
| After `DROP TABLE orders CASCADE` | 5 | (table dropped) | **502** |
| After `pg_restore` | 5 | 50 | **200** |

Full recovery cycle: DROP → API breaks (502) → pg_restore → API recovers (200), with zero data loss (RPO = 0 for this specific backup point, since the dump was taken immediately before the drop).

### 9.7: Answer — RPO of current setup

**What's the RPO of a single manual `pg_dump`?** The RPO equals the time since the last backup was taken — potentially hours or days if backups are only run manually. In this test, RPO was ~0 because the dump was taken seconds before the disaster. In a real system with manual-only backups, an incident right before the next scheduled backup could lose an entire day of orders. This is fixed in the Bonus Task with an automated CronJob running every 5 minutes, bringing worst-case RPO down to 5 minutes.

---

## Task 2 — Disaster Recovery (No PVC)

### 9.8: Kill Postgres Pod (Before PVC)

```
Before: events=5, orders=50
Kill time: 17:21:10.079
pod "postgres-7c7ffc4b-p9cwn" deleted
New pod Running: 17:21:11 (~1s)
Recovery time (checked): 17:21:23.015
Result: "Did not find any relations." — completely empty database
```

**RTO:** ~13 seconds (pod restart + container ready time) — but the database itself contains ZERO data.

**RPO:** 100% of data lost — all 5 events and 50 orders gone, since the default Postgres Deployment has no PersistentVolumeClaim; all data lived on the pod's ephemeral filesystem.

### Answer — Why was the new pod empty?

The Postgres Deployment had no PVC — `/var/lib/postgresql/data` was backed by the container's writable layer, which is destroyed when the pod is deleted. Kubernetes recreated a fresh container with a fresh, empty filesystem. This is fixed by adding persistent storage (see Bonus Task) so the data directory survives pod recreation.

---

## Bonus Task — Persistent Storage + Automated Backup CronJob

### B.1: PVC Added to Postgres

```diff
+          env:
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

### Re-measured RTO (With PVC)

```
Before kill: events=5 (re-seeded on new PV)
Pod deleted, recreated
Recovery time: 17:23:08.389
Result: events=5 — DATA SURVIVED
```

With the PVC, the pod restart no longer causes data loss. The new pod mounts the same PersistentVolume and finds its data exactly as it was left — RPO drops from "100% loss" to **0% loss** for any pod-restart scenario (RTO stays similar, ~10-15s for pod recreation, but no `pg_restore` step is needed).

### B.2: Automated Backup CronJob

`k8s/backup-cronjob.yaml`:

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
                - sh
                - -c
                - |
                  set -e
                  TS=$(date -u +%Y%m%dT%H%M%SZ)
                  pg_dump -Fc > /backups/quickticket_${TS}.dump
                  echo "Backup written: quickticket_${TS}.dump"
                  cd /backups
                  ls -1t quickticket_*.dump | tail -n +6 | xargs -r rm -v
              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

### Retention Verification

Ran 7 manual backups (`manual-1` through `manual-7`). Logs from `manual-7`:

```
Backup written: quickticket_20260711T142449Z.dump
removed 'quickticket_20260711T142421Z.dump'
```

Final state — exactly 5 files remain after 7 runs:

```
-rw-r--r--    1 root     root          9592 Jul 11 14:24 quickticket_20260711T142427Z.dump
-rw-r--r--    1 root     root          9592 Jul 11 14:24 quickticket_20260711T142432Z.dump
-rw-r--r--    1 root     root          9592 Jul 11 14:24 quickticket_20260711T142438Z.dump
-rw-r--r--    1 root     root          9592 Jul 11 14:24 quickticket_20260711T142443Z.dump
-rw-r--r--    1 root     root          9592 Jul 11 14:24 quickticket_20260711T142449Z.dump
```

Retention logic correctly kept the 5 newest and deleted the 2 oldest (backups from `manual-1` and `manual-2`).
