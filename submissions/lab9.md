*Emil Nabiullin, e.nabiullin@innopolis.university*
---
# Lab 9 - Stateful Services & DB Reliability

## Repository state

This branch contains the Lab 9 database-reliability work on top of the merged Lab 7 base from `main`:

- `alembic.ini` was added and points to the lab Postgres connection string
- `migrations/` was initialized and now contains the baseline revision plus the `events.email` migration
- `k8s/postgres.yaml` was updated for the bonus PVC-backed Postgres deployment
- `k8s/backup-cronjob.yaml` was added for the bonus automated backup job
- `submissions/lab9.md` was added

The local environment did not initially have `kubectl` or `k3d`, the application images in `k8s/` point to private GHCR tags, and the host already had a local PostgreSQL daemon bound to port `5432`. For the honest local verification run I installed `kubectl` and `k3d` locally, built the three application images from the current repository, imported them into `k3d`, and used a temporary Alembic config that forwarded Postgres to `127.0.0.1:15432`. I did not keep those runtime-only workarounds in the final repo diff.

## Setup

I started from a healthy local k3d cluster, deployed the repository manifests, seeded the database, and enabled the Lab 8 mixed load so the migration and recovery work happened under live traffic:

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket < app/seed.sql

kubectl apply -f labs/lab8/mixedload.yaml
kubectl rollout status deployment/mixedload --timeout=180s
```

```text
CREATE TABLE
CREATE TABLE
INSERT 0 5
deployment.apps/mixedload created
deployment "mixedload" successfully rolled out
```

To keep the write path stable while `mixedload` was continuously buying tickets from event `1`, I increased its ticket pool in the live cluster:

```bash
kubectl exec $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -c \
  "update events set total_tickets = 100000 where id = 1; select id, total_tickets from events where id = 1;"
```

```text
UPDATE 1
 id | total_tickets
----+---------------
  1 |        100000
(1 row)
```

## Task 1 - Migrations and backup/restore

### Alembic initialization and baseline

I initialized Alembic and created the baseline revision for the already existing schema. Because the host `5432` port was occupied by the local PostgreSQL service, I used a temporary runtime config that targeted `127.0.0.1:15432` while keeping the committed `alembic.ini` at the lab-required `localhost:5432`.

Connection check through the port-forward:

```text
events: 5
```

`alembic history` after creating both revisions:

```bash
.venv/bin/alembic -c /tmp/emil-lab9-evidence/alembic-15432.ini history
```

```text
2dcb5d96ea0b -> 95710c8a8e44 (head), add email column to events
<base> -> 2dcb5d96ea0b, baseline - pre-existing schema
```

### Migration under load

Before applying the real migration, I explicitly stamped the database back to the baseline revision and checked the current revision:

```bash
.venv/bin/alembic -c /tmp/emil-lab9-evidence/alembic-15432.ini current
```

```text
2dcb5d96ea0b
```

Prometheus 5xx baseline with `mixedload` active:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))' \
  | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('5xx last 1min:', r[0]['value'][1] if r else 0)"
```

```text
5xx last 1min: 0
```

Migration timing:

```bash
time .venv/bin/alembic -c /tmp/emil-lab9-evidence/alembic-15432.ini upgrade head
```

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 2dcb5d96ea0b -> 95710c8a8e44, add email column to events
.venv/bin/alembic -c /tmp/emil-lab9-evidence/alembic-15432.ini upgrade head  0.22s user 0.03s system 87% cpu 0.290 total
```

Schema after the migration:

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
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
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
Referenced by:
    TABLE "orders" CONSTRAINT "orders_event_id_fkey" FOREIGN KEY (event_id) REFERENCES events(id)
```

Prometheus 5xx after the migration:

```text
5xx last 1min: 0
```

The nullable-column migration completed in under `0.3s` and did not add any 5xx during the 1-minute observation window.

### Backup validation

`pg_dump` backup creation:

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  pg_dump -U quickticket -Fc quickticket > /tmp/quickticket.dump
```

```bash
ls -lh /tmp/quickticket.dump
file /tmp/quickticket.dump
```

```text
-rw-rw-r-- 1 andrey-debian andrey-debian 28K Jul 10 17:54 /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
```

Backup table-of-contents proof:

```bash
kubectl exec $POD -- pg_restore --list /tmp/backup.dump | head -25
```

```text
;
; Archive created at 2026-07-10 14:54:27 UTC
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
220; 1259 16411 TABLE public alembic_version quickticket
218; 1259 16389 TABLE public events quickticket
217; 1259 16388 SEQUENCE public events_id_seq quickticket
3481; 0 0 SEQUENCE OWNED BY public events_id_seq quickticket
219; 1259 16397 TABLE public orders quickticket
3316; 2604 16392 DEFAULT public events id quickticket
3474; 0 16411 TABLE DATA public alembic_version quickticket
3472; 0 16389 TABLE DATA public events quickticket
3473; 0 16397 TABLE DATA public orders quickticket
3482; 0 0 SEQUENCE SET public events_id_seq quickticket
```

### Drop and restore

Counts before the disaster simulation:

```text
 events_count
--------------
            5
(1 row)

 orders_count
--------------
          628
(1 row)
```

I dropped `orders` and checked both the database state and the user-visible behavior:

```text
DROP TABLE
```

```text
 events_count
--------------
            5
(1 row)

 orders_table
--------------

(1 row)
```

```text
/events=502
reserve=500
```

After restoring from the custom-format backup:

```text
 events_count
--------------
            5
(1 row)

 orders_count
--------------
          711
(1 row)
```

```text
/events=200
reserve=200
```

The post-restore `orders` count is higher than the pre-drop count because `mixedload` continued to run and resumed creating new orders immediately after the recovery.

### RPO answer

With a single manual `pg_dump`, the practical RPO is the time since that dump was taken. Any writes between the dump timestamp and the failure are lost. In the Task 2 disaster run below, that gap became visible as missing `orders` rows after restore. I would improve this by taking backups automatically on a schedule and, for tighter recovery objectives, by adding persistent storage plus more frequent backups or WAL-based PITR instead of relying on a single manual dump.

## Task 2 - Disaster recovery under load

I kept `mixedload` running, force-deleted the Postgres pod, waited for the replacement pod, restored the dump into the empty database, and restarted `events` so it would reconnect cleanly.

Timestamps:

```text
before_epoch=1783695393 before_time=17:56:33
kill_epoch=1783695417 kill_time=17:56:57
ready_epoch=1783695425 ready_time=17:57:05
restored_epoch=1783695446 restored_time=17:57:26
app_ready_epoch=1783695469 app_ready_time=17:57:49
```

When the new pod came up, the database was empty:

```text
Did not find any relations.
```

Order counts around the incident:

```text
 orders_before_disaster
------------------------
                    911
(1 row)
```

```text
 orders_after_restore_before_restart
-------------------------------------
                                 630
(1 row)
```

Actual RTO:

- `17:57:49 - 17:56:57 = 52 seconds`

Actual RPO gap:

- `911 - 630 = 281 orders`

Prometheus error-rate query around the incident:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B30s%5D))'
```

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783695480.665,"0"]}]}}
```

The new Postgres pod was empty because the original deployment stored its database files on the container filesystem with no `PersistentVolumeClaim`. Deleting the pod deleted that writable layer and the replacement pod started with a fresh empty data directory. The fix is to mount a PVC for Postgres data so pod restarts reuse the same storage.

## Bonus - PVC and automated backup CronJob

### PVC diff for `k8s/postgres.yaml`

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
 apiVersion: apps/v1
 kind: Deployment
 metadata:
@@ -24,6 +35,11 @@ spec:
               value: "quickticket"
             - name: POSTGRES_PASSWORD
               value: "quickticket"
+            - name: PGDATA
+              value: "/var/lib/postgresql/data/pgdata"
+          volumeMounts:
+            - name: data
+              mountPath: /var/lib/postgresql/data
           resources:
             requests:
               cpu: 50m
@@ -31,6 +47,10 @@ spec:
             limits:
               cpu: 200m
               memory: 256Mi
+      volumes:
+        - name: data
+          persistentVolumeClaim:
+            claimName: postgres-data
 ---
 apiVersion: v1
 kind: Service
```

### Re-run of the disaster test with PVC

After applying the PVC-backed Postgres manifest, reseeding, and restarting `events`, I repeated the pod-kill test. This time the replacement Postgres pod came back with its data still present, so no `pg_restore` step was needed.

Timestamps:

```text
bonus_before_epoch=1783695550 bonus_before_time=17:59:10
bonus_kill_epoch=1783695558 bonus_kill_time=17:59:18
bonus_ready_epoch=1783695567 bonus_ready_time=17:59:27
bonus_app_ready_epoch=1783695583 bonus_app_ready_time=17:59:43
```

Counts around the PVC-backed restart:

```text
 orders_before_pvc_kill
------------------------
                     61
(1 row)
```

```text
 orders_after_pvc_restart_before_events_restart
------------------------------------------------
                                             96
(1 row)
```

Improved RTO:

- `17:59:43 - 17:59:18 = 25 seconds`

That is roughly half of the earlier `52s` RTO because the restore step disappeared and only pod replacement plus `events` reconnect remained.

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
                - /bin/sh
                - -c
                - |
                  set -eu
                  ts=$(date -u +%Y%m%dT%H%M%SZ)
                  pg_dump -Fc -f "/backups/quickticket_${ts}.dump"
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

`manual-7` logs showing retention:

```text
removed 'quickticket_20260710T150028Z.dump'
```

`/backups` listing after seven manual runs:

```text
total 112
drwxrwxrwx    2 root     root          4096 Jul 10 15:00 .
drwxr-xr-x    1 root     root          4096 Jul 10 15:00 ..
-rw-r--r--    1 root     root         17701 Jul 10 15:00 quickticket_20260710T150031Z.dump
-rw-r--r--    1 root     root         18441 Jul 10 15:00 quickticket_20260710T150034Z.dump
-rw-r--r--    1 root     root         19176 Jul 10 15:00 quickticket_20260710T150038Z.dump
-rw-r--r--    1 root     root         19915 Jul 10 15:00 quickticket_20260710T150041Z.dump
-rw-r--r--    1 root     root         20509 Jul 10 15:00 quickticket_20260710T150044Z.dump
```

This confirms that after seven runs the CronJob kept exactly the five newest dumps and deleted the oldest one.
