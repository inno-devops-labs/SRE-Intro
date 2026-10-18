# Lab 9 — Stateful Services & DB Reliability

`mixedload` (~16.7 RPS) ran throughout. Times are UTC, 2026-10-11.

## Task 1 — Migrations & Backup/Restore

**`alembic history`:**
```
83c8377a61dc -> f8fd975e1b76 (head), add email column to events
<base> -> 83c8377a61dc, baseline - pre-existing schema
```

**`\d events`:**
```
    Column     |           Type           | Nullable |              Default
---------------+--------------------------+----------+------------------------------------
 id            | integer                  | not null | nextval('events_id_seq'::regclass)
 name          | text                     | not null |
 venue         | text                     | not null |
 event_date    | timestamp with time zone | not null |
 total_tickets | integer                  | not null |
 price_cents   | integer                  | not null |
 email         | character varying(255)   |          |
```

**`time alembic upgrade head`:**
```
Running upgrade 83c8377a61dc -> f8fd975e1b76, add email column to events
0.20s user 0.05s system 65% cpu 0.392 total
```

**5xx around the migration (15:23:23):**
```
15:23:22  5xx last 1min: 0   rps: 16.75
15:24:22  5xx last 1min: 0   rps: 16.71
```

**Backup (15:24:51):**
```
168K quickticket.dump
quickticket.dump: PostgreSQL custom database dump - v1.16-0

$ pg_restore --list /tmp/backup.dump
; Archive created at 2026-10-11 15:24:51 UTC
;     dbname: quickticket
;     TOC Entries: 18
;     Format: CUSTOM
220; 1259 16423 TABLE public alembic_version quickticket
218; 1259 16390 TABLE public events quickticket
219; 1259 16398 TABLE public orders quickticket
3472; 0 16390 TABLE DATA public events quickticket
3473; 0 16398 TABLE DATA public orders quickticket
3325; 2606 16407 FK CONSTRAINT public orders orders_event_id_fkey quickticket
```

**Row counts:**

| Moment | events | orders | API |
|---|---:|---:|---|
| Before | 5 | 4558 | 200 |
| After `DROP TABLE orders CASCADE` | 5 | relation does not exist | `/events` 502, `/reserve` 500 |
| After `pg_restore` | 5 | 4553 | 200 |

The dump held 4552 orders; 6 written after it were lost, 1 new arrived after the restore.

**RPO of a single `pg_dump`, and how to improve it:**
RPO = age of the last dump, unbounded with one manual dump. Improvements: scheduled dumps (RPO ≤ interval), a PVC (pod restart loses nothing), WAL archiving or a replica (RPO of seconds).

## Task 2 — Disaster Recovery Under Load

```
healthy at 15:25:34, orders: 4762

Disaster at      15:25:34
New pod ready    15:25:35   (+1s)     \dt → Did not find any relations.
Restored         15:25:36   (+2s)
App fully up     15:25:47   (+13s)
orders after restore: 4552
```

- **RTO: 13 s**, of which 11 s was the events restart.
- **RPO: 43 s, 210 orders lost** (4762 → 4552).

**5xx rate (`rate[30s]`):**
```
15:25:30  0.000
15:25:40  0.040
15:25:50  0.160
15:26:00  0.206
15:26:10  0.121
15:26:20  0.000
```

**Why was the new pod empty? How to eliminate it?**
No volume: the data directory lived in the container layer and died with the pod. Fix: a PVC on the data directory (Bonus).

## Bonus — PVC + Backup CronJob

**`k8s/postgres.yaml` diff:**
```diff
   replicas: 1
+  strategy:
+    type: Recreate
 ...
+            - name: PGDATA
+              value: "/var/lib/postgresql/data/pgdata"
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

**Same kill with the PVC:**
```
healthy at 15:27:34, orders: 4991
Disaster at        15:27:35
New pod ready      15:27:36 (+1s)
orders right after restart: 4993
/events non-200 window: 15:27:35 → 15:27:40
```

| | No PVC | With PVC |
|---|---|---|
| RTO | 13 s, manual | ~6 s, automatic |
| Orders lost | 210 | 0 |

**`k8s/backup-cronjob.yaml`:**
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
              env:
                - name: PGHOST
                  value: "postgres"
                - name: PGUSER
                  value: "quickticket"
                - name: PGDATABASE
                  value: "quickticket"
                - name: PGPASSWORD
                  value: "quickticket"
              command: ["sh", "-c"]
              args:
                - |
                  set -e
                  cd /backups
                  FILE="quickticket_$(date -u +%Y%m%dT%H%M%SZ).dump"
                  pg_dump -Fc -f "$FILE.tmp"
                  mv "$FILE.tmp" "$FILE"
                  echo "created $FILE ($(du -h "$FILE" | cut -f1))"
                  ls -1t quickticket_*.dump | tail -n +6 | xargs -r rm -v
                  echo "kept: $(ls -1 quickticket_*.dump | wc -l) dumps"
              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```
The `postgres-backups` PVC is declared in the same file.

**`kubectl logs job/manual-7`:**
```
created quickticket_20261011T152931Z.dump (208.0K)
removed 'quickticket_20261011T152910Z.dump'
kept: 5 dumps
```

**`ls -la /backups` after 7 runs:**
```
-rw-r--r--    1 root     root        207195 Oct 11 15:29 quickticket_20261011T152914Z.dump
-rw-r--r--    1 root     root        208003 Oct 11 15:29 quickticket_20261011T152919Z.dump
-rw-r--r--    1 root     root        208879 Oct 11 15:29 quickticket_20261011T152923Z.dump
-rw-r--r--    1 root     root        209684 Oct 11 15:29 quickticket_20261011T152927Z.dump
-rw-r--r--    1 root     root        210588 Oct 11 15:29 quickticket_20261011T152931Z.dump
```
The scheduled run at 15:30:01 also completed and rotated.
