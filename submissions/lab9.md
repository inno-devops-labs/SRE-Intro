# Lab 9 — Stateful Services & DB Reliability

**Author:** Anton Bugaev  
**Date:** 2026-07-04  
**Cluster:** k3d `quickticket`

---

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install alembic==1.18.4 psycopg2-binary==2.9.11 sqlalchemy==2.0.49
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket < app/seed.sql
kubectl apply -f labs/lab8/mixedload.yaml
kubectl port-forward svc/postgres 15432:5432
```

Port `5432` was already busy locally, so Alembic used `localhost:15432`.

---

## Task 1 — Alembic Migration and Backup/Restore

### Alembic history

```text
e00e25ace275 -> 4cb7a9e36b09 (head), add email column to events
<base> -> e00e25ace275, baseline - pre-existing schema
```

### Migration under load

`mixedload` was running during the migration:

```text
NAME        READY   UP-TO-DATE   AVAILABLE
mixedload   2/2     2            2
```

5xx before migration:

```text
5xx last 1min before: 1.0925976509150506
```

Migration command and timing:

```text
INFO  [alembic.runtime.migration] Running upgrade e00e25ace275 -> 4cb7a9e36b09, add email column to events
real 0.37
user 0.19
sys 0.04
```

Schema after migration:

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
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
Referenced by:
    TABLE "orders" CONSTRAINT "orders_event_id_fkey" FOREIGN KEY (event_id) REFERENCES events(id)
```

5xx after migration:

```text
5xx last 1min after: 2.1818181818181817
```

The migration did not create a visible new failure mode. The non-zero 5xx was already present from `mixedload` checkout contention and background failures.

### Backup proof

```text
-rw-r--r--@ 1 an11y  wheel   7.1K Jul  4 19:08 /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
```

`pg_restore --list` excerpt:

```text
; Archive created at 2026-07-04 16:08:52 UTC
;     dbname: quickticket
;     TOC Entries: 18
;     Compression: gzip
;     Dump Version: 1.16-0
;     Format: CUSTOM
;     Dumped from database version: 17.10
;     Dumped by pg_dump version: 17.10
;
220; 1259 16411 TABLE public alembic_version quickticket
218; 1259 16389 TABLE public events quickticket
217; 1259 16388 SEQUENCE public events_id_seq quickticket
219; 1259 16397 TABLE public orders quickticket
3472; 0 16389 TABLE DATA public events quickticket
3473; 0 16397 TABLE DATA public orders quickticket
```

### Data loss and restore

Before DROP:

```text
 events
--------
      5

 orders
--------
     50
```

After `DROP TABLE orders CASCADE`:

```text
 events
--------
      5

 orders_table
--------------

/events=502
```

After `pg_restore --clean --if-exists`:

```text
 events
--------
      5

 orders
--------
     50

/events=200
```

### RPO answer

With a single manual `pg_dump`, RPO is the time since the last dump. In this run the restore used a fresh dump, so the measured row gap was `0`, but any orders created after that dump would be lost. I would improve this with persistent storage plus scheduled backups and, for stronger RPO, WAL archiving or managed Postgres point-in-time recovery.

---

## Task 2 — Disaster Recovery Under Load

### No-PVC disaster test

Fresh backup before disaster:

```text
orders_before=50
healthy_at=19:12:11
```

After force-deleting the Postgres pod:

```text
pod/postgres-85ffd4fb9f-jj57x condition met
tables_after_restart:
Did not find any relations.
```

Recovery timeline:

```text
disaster_at=19:12:11
pod_ready=19:12:12
postgres_ready=19:12:16
restored=19:12:16
app_ready=19:12:24
rto_seconds=13
restore_seconds=5
orders_after=50
rpo_gap_rows=0
```

Prometheus snapshot after the incident:

```text
5xx rate 30s after DR: 0.639993603072082
```

### Observation

The new Postgres pod was empty because the original Deployment stored data only in the container filesystem. When Kubernetes replaced the pod, the old filesystem disappeared with it. This failure mode is eliminated by mounting a PersistentVolumeClaim for the Postgres data directory.

---

## Bonus Task — PVC and Automated Backups

### Postgres PVC diff

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

PVC status:

```text
postgres-data   Bound    pvc-ac816f00-d399-4fbb-b08b-489d09902c37   1Gi   RWO   local-path
```

### RTO after PVC

After restoring the dump once into the new PVC-backed Postgres, I repeated the pod-kill test without running `pg_restore`.

Tables survived the restart:

```text
               List of relations
 Schema |      Name       | Type  |    Owner
--------+-----------------+-------+-------------
 public | alembic_version | table | quickticket
 public | events          | table | quickticket
 public | orders          | table | quickticket
```

Recovery timeline:

```text
pvc_disaster_at=19:15:21
pvc_pod_ready=19:15:22
pvc_postgres_ready=19:15:22
pvc_app_ready=19:15:28
pvc_rto_seconds=7
pvc_orders_before=50
pvc_orders_after=50
```

RTO improved from **13 s** with manual restore to **7 s** with PVC-backed storage, and the restore step was no longer needed.

### Backup CronJob

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
                  dump="quickticket_$(date -u +%Y%m%dT%H%M%SZ).dump"
                  pg_dump -Fc -f "$dump"
                  ls -1t quickticket_*.dump | tail -n +6 | xargs -r rm -v
                  ls -lh "$dump"
              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

Retention proof after 7 manual runs:

```text
manual-7 logs:
removed 'quickticket_20260704T161603Z.dump'
-rw-r--r--    1 root     root        7.1K Jul  4 16:16 quickticket_20260704T161623Z.dump
```

Exactly 5 backup files remained:

```text
total 48
drwxrwxrwx    2 root     root          4096 Jul  4 16:16 .
drwxr-xr-x    1 root     root          4096 Jul  4 16:15 ..
-rw-r--r--    1 root     root          7274 Jul  4 16:16 quickticket_20260704T161607Z.dump
-rw-r--r--    1 root     root          7274 Jul  4 16:16 quickticket_20260704T161611Z.dump
-rw-r--r--    1 root     root          7274 Jul  4 16:16 quickticket_20260704T161615Z.dump
-rw-r--r--    1 root     root          7274 Jul  4 16:16 quickticket_20260704T161619Z.dump
-rw-r--r--    1 root     root          7274 Jul  4 16:16 quickticket_20260704T161623Z.dump
```

---

## Verification checklist

- [x] Task 1 done — Alembic baseline + nullable migration under load
- [x] Task 1 done — `pg_dump` backup and `pg_restore` recovery verified
- [x] Task 2 done — Postgres pod disaster, RTO/RPO measured
- [x] Bonus Task done — PVC added, RTO re-measured
- [x] Bonus Task done — CronJob backup and 5-file retention verified
