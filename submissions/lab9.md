# Lab 9

`main` in this repository still had the pre-fix `labs/lab8/mixedload.yaml`, so before starting I applied the same minimal runtime fix as in Lab 8:

- switched the checkout load from `event 1` to `event 3`
- replaced the fragile `sed` extraction with `grep | cut`

## Task 1

I initialized Alembic, created a baseline revision for the pre-existing schema, stamped it, then created the real migration that adds a nullable `email` column to `events`.

`alembic history`:

```text
5784f05e56f7 -> c35d23d6cab6 (head), add email column to events
<base> -> 5784f05e56f7, baseline - pre-existing schema
```

Migration timing under load:

```bash
time .venv/bin/alembic upgrade head
```

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 5784f05e56f7 -> c35d23d6cab6, add email column to events
.venv/bin/alembic upgrade head  0.52s user 0.08s system 87% cpu 0.683 total
```

The new column is present:

```bash
/home/andrey-debian/.local/bin/kubectl exec -i $(/home/andrey-debian/.local/bin/kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -c '\d events'
```

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
```

Prometheus `5xx last 1min` before and after the migration:

```text
before: 5xx last 1min: 2.1818181818181817
after:  5xx last 1min: 2.1818181818181817
```

Those `5xx` came from `/health` during the startup window that was still inside the 1-minute Prometheus range; the migration itself did not add any extra errors.

Backup creation:

```bash
/home/andrey-debian/.local/bin/kubectl exec -i $(/home/andrey-debian/.local/bin/kubectl get pod -l app=postgres -o name) -- \
  pg_dump -U quickticket -Fc quickticket > /tmp/quickticket.dump

ls -lh /tmp/quickticket.dump
file /tmp/quickticket.dump
```

```text
-rw-rw-r-- 1 andrey-debian andrey-debian 15K Jul 10 21:47 /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
```

`pg_restore --list`:

```text
;
; Archive created at 2026-07-10 18:47:43 UTC
;     dbname: quickticket
;     TOC Entries: 18
;     Compression: gzip
;     Dump Version: 1.16-0
;     Format: CUSTOM
; Selected TOC Entries:
;
220; 1259 16412 TABLE public alembic_version quickticket
218; 1259 16386 TABLE public events quickticket
217; 1259 16385 SEQUENCE public events_id_seq quickticket
219; 1259 16394 TABLE public orders quickticket
3474; 0 16412 TABLE DATA public alembic_version quickticket
3472; 0 16386 TABLE DATA public events quickticket
3473; 0 16394 TABLE DATA public orders quickticket
```

Before disaster / after `DROP TABLE` / after restore:

```text
before:
events_count = 5
orders_count = 250

after DROP:
events_count = 5
orders_table = null
/events=502

after restore:
events_count = 5
orders_count = 250
/events=200
```

What is the RPO of the current setup with a single `pg_dump`? How would I improve it?

The RPO is the time since the last backup. If the dump is 1 hour old, then up to 1 hour of writes can be lost. I improved it in the bonus by adding scheduled backups, but for a real system I would go further and add WAL archiving / PITR so recovery is not limited to the last full dump.

## Task 2

I first hit one practical issue: Kubernetes marked the new Postgres pod `Ready` slightly before `psql` inside it accepted connections. For the final measurement below I waited for a successful `SELECT 1`, then continued with restore and app recovery.

Final no-PVC recovery run:

```text
backup_at=21:58:05
orders_at_backup=36
orders_before_disaster=69
disaster_at=21:58:13
new_pod_ready=21:58:28
Did not find any relations.
orders_after_restore=39
restored_at=21:58:30
app_ready=21:58:44
```

RTO and RPO:

- Actual `RTO` = `21:58:44 - 21:58:13 = 31s`
- Time-based `RPO` = `21:58:13 - 21:58:05 = 8s`
- Data gap in rows = `69 - 39 = 30 orders`

Prometheus error-rate sample around the incident:

```bash
/home/andrey-debian/.local/bin/kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B30s%5D))'
```

```text
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783709799.690,"0.04"]}]}}
```

Why was the new Postgres pod empty? How would I eliminate this failure mode?

It was empty because the original Deployment had no persistent volume, so the database lived on ephemeral pod storage. The fix is to mount a PVC for PostgreSQL data so a new pod reuses the same volume instead of starting from an empty filesystem.

## Bonus Task

I added a PVC to `k8s/postgres.yaml` and mounted it on `/var/lib/postgresql/data` with `PGDATA=/var/lib/postgresql/data/pgdata`.

Diff of `k8s/postgres.yaml`:

```diff
diff --git a/k8s/postgres.yaml b/k8s/postgres.yaml
index 1524c5f..0449bb1 100644
--- a/k8s/postgres.yaml
+++ b/k8s/postgres.yaml
@@ -1,3 +1,14 @@
+apiVersion: v1
+kind: PersistentVolumeClaim
+metadata:
+  name: postgres-data
+spec:
+  accessModes:
+    - ReadWriteOnce
+  resources:
+    requests:
+      storage: 1Gi
+---
@@ -24,6 +35,11 @@ spec:
+            - name: PGDATA
+              value: "/var/lib/postgresql/data/pgdata"
+          volumeMounts:
+            - name: data
+              mountPath: /var/lib/postgresql/data
@@ -31,6 +47,10 @@ spec:
+      volumes:
+        - name: data
+          persistentVolumeClaim:
+            claimName: postgres-data
```

PVC re-run of the disaster test:

```text
orders_before_restart=122
disaster_at=22:00:33
new_pod_ready=22:00:37
               List of relations
 Schema |      Name       | Type  |    Owner
--------+-----------------+-------+-------------
 public | alembic_version | table | quickticket
 public | events          | table | quickticket
 public | orders          | table | quickticket

orders_after_pod_restart=122
app_ready=22:00:52
```

With the PVC, the data survived the pod restart and the restore step disappeared. The new `RTO` was `19s`, down from `31s`.

My `k8s/backup-cronjob.yaml`:

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
                  value: "postgres"
                - name: PGUSER
                  value: "quickticket"
                - name: PGDATABASE
                  value: "quickticket"
                - name: PGPASSWORD
                  value: "quickticket"
              command:
                - sh
                - -c
                - |
                  set -eu
                  TS=$(date -u +%Y%m%dT%H%M%SZ)
                  FILE="/backups/quickticket_${TS}.dump"
                  pg_dump -Fc -f "$FILE"
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

`manual-7` log:

```text
removed 'quickticket_20260710T190155Z.dump'
```

Contents of `/backups` after 7 runs:

```text
total 68
drwxrwxrwx    2 root     root          4096 Jul 10 19:02 .
drwxr-xr-x    1 root     root          4096 Jul 10 19:01 ..
-rw-r--r--    1 root     root         12277 Jul 10 19:02 quickticket_20260710T190200Z.dump
-rw-r--r--    1 root     root         12277 Jul 10 19:02 quickticket_20260710T190204Z.dump
-rw-r--r--    1 root     root         12277 Jul 10 19:02 quickticket_20260710T190209Z.dump
-rw-r--r--    1 root     root         12277 Jul 10 19:02 quickticket_20260710T190214Z.dump
-rw-r--r--    1 root     root         12277 Jul 10 19:02 quickticket_20260710T190218Z.dump
```
