# Lab 9 - Stateful Services & DB Reliability

## Task 1 - Migrations & Backup/Restore

### 1. Alembic history

Alembic was initialized in `migrations/`. The existing `events` and `orders`
schema was marked as the baseline, then a real migration added a nullable
`email` column to `events`.

```bash
$ .venv/bin/alembic history
debe1e371cd3 -> 6803cfdeb137 (head), add email column to events
<base> -> debe1e371cd3, baseline - pre-existing schema
```

### 2. Migration under load

`mixedload` was running during the migration.

```bash
$ kubectl get deployment mixedload
NAME        READY   UP-TO-DATE   AVAILABLE   AGE
mixedload   2/2     2            2           2m4s
```

Prometheus 5xx baseline before migration:

```bash
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))' \
  | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('5xx last 1min:', r[0]['value'][1] if r else 0)"
5xx last 1min: 0
```

Migration runtime:

```bash
$ /usr/bin/time -p .venv/bin/alembic upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade debe1e371cd3 -> 6803cfdeb137, add email column to events
real 0.34
user 0.27
sys 0.04
```

Schema after migration:

```bash
$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -c '\d events'
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

Prometheus 5xx after migration:

```bash
5xx last 1min: 0
```

The nullable column migration did not increase 5xx errors.

### 3. Backup

```bash
$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  pg_dump -U quickticket -Fc quickticket > /tmp/quickticket.dump

$ ls -lh /tmp/quickticket.dump
-rw-rw-r-- 1 gabdullin gabdullin 7.2K Jul  6 17:45 /tmp/quickticket.dump

$ file /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
```

`pg_restore --list` confirmed that the dump contains the expected objects:

```bash
$ kubectl exec $POD -- pg_restore --list /tmp/backup.dump | head -25
;
; Archive created at 2026-07-06 14:45:41 UTC
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
3444; 0 0 SEQUENCE OWNED BY public events_id_seq quickticket
219; 1259 16397 TABLE public orders quickticket
3279; 2604 16392 DEFAULT public events id quickticket
3437; 0 16411 TABLE DATA public alembic_version quickticket
3435; 0 16389 TABLE DATA public events quickticket
3436; 0 16397 TABLE DATA public orders quickticket
3445; 0 0 SEQUENCE SET public events_id_seq quickticket
```

### 4. Data loss and restore

Counts before disaster:

```bash
--- before drop ---
 events
--------
      5
(1 row)

 orders
--------
     50
(1 row)
```

After dropping `orders`:

```bash
--- drop orders ---
DROP TABLE

--- after drop ---
 events
--------
      5
(1 row)

 orders_table
--------------

(1 row)

--- smoke during drop ---
/events=502
```

After restore:

```bash
--- restore ---

--- after restore ---
 events
--------
      5
(1 row)

 orders
--------
     50
(1 row)

--- smoke after restore ---
/events=200
```

### 5. RPO for single pg_dump

With a single manual `pg_dump`, the RPO is the time since the dump was created.
In this run the dump was fresh, so the observed row gap was `50 - 50 = 0`
orders. In a real setup, every order written after the last dump could be lost.

To improve RPO, I would schedule frequent backups and keep them on persistent
storage. For lower RPO than periodic dumps, I would add WAL archiving or
streaming replication.

---

## Task 2 - Disaster Recovery Under Load

The first Postgres Deployment had no PVC. After killing the pod, the replacement
pod started with an empty database directory.

```bash
--- T0 state ---
 orders_before_disaster
------------------------
                     50
(1 row)

Disaster at 18:54:30
pod "postgres-85ffd4fb9f-v8lcq" force deleted from default namespace
pod/postgres-85ffd4fb9f-x9c9q condition met
New pod Kubernetes Ready 18:54:32
New pod DB accepts SQL 18:54:44

--- new pod relations ---
Did not find any relations.

--- restore from backup ---
Restored 18:54:45

--- restart events ---
deployment.apps/events restarted
deployment "events" successfully rolled out
App fully up 18:54:56

--- after restore counts ---
 events_after_restore
----------------------
                    5
(1 row)

 orders_after_restore
----------------------
                   50
(1 row)

--- smoke ---
/events=200
```

Timestamps:

```text
Disaster at             18:54:30
New pod K8s Ready       18:54:32
New pod DB SQL Ready    18:54:44
Restored                18:54:45
App fully up            18:54:56
```

Actual RTO:

```text
18:54:56 - 18:54:30 = 26 seconds
```

Actual RPO gap:

```text
orders before disaster = 50
orders after restore   = 50
row gap                = 0
```

Prometheus 5xx rate around the incident:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783353301.615,"2.44"]}]}}
```

The new Postgres pod was empty because the original Deployment stored data only
inside the pod filesystem. Deleting the pod deleted the database files. This
failure mode is eliminated by mounting a PersistentVolumeClaim on the Postgres
data directory.

---

## Bonus Task - Persistent Storage + Automated Backup CronJob

### 1. Postgres PVC diff

```diff
diff --git a/k8s/postgres.yaml b/k8s/postgres.yaml
index 3bd83f3..a2eb145 100644
--- a/k8s/postgres.yaml
+++ b/k8s/postgres.yaml
@@ -24,6 +24,11 @@ spec:
               value: quickticket
             - name: POSTGRES_PASSWORD
               value: quickticket
+            - name: PGDATA
+              value: /var/lib/postgresql/data/pgdata
+          volumeMounts:
+            - name: data
+              mountPath: /var/lib/postgresql/data
           resources:
             requests:
               cpu: 50m
@@ -31,6 +36,20 @@ spec:
             limits:
               cpu: 200m
               memory: 256Mi
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
 ---
 apiVersion: v1
 kind: Service
```

PVCs were bound:

```bash
$ kubectl get pvc postgres-data postgres-backups
NAME               STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS
postgres-data      Bound    pvc-e6d7296c-4edd-4df8-bfec-4d1e4d18229d   1Gi        RWO            standard
postgres-backups   Bound    pvc-70180120-c8b6-4f65-9115-b8b089c2a535   1Gi        RWO            standard
```

### 2. RTO after adding PVC

After adding the PVC, deleting the Postgres pod no longer deleted the database
tables or rows. No `pg_restore` step was needed.

```bash
--- PVC T0 state ---
 events_before_pvc_restart
---------------------------
                         5
(1 row)

 orders_before_pvc_restart
---------------------------
                        25
(1 row)

Disaster at 18:57:10
pod "postgres-6fc5585b5b-j96rm" force deleted from default namespace
pod/postgres-6fc5585b5b-zsl26 condition met
New pod Kubernetes Ready 18:57:12
New pod DB SQL Ready 18:57:13

--- relations survived ---
               List of relations
 Schema |      Name       | Type  |    Owner
--------+-----------------+-------+-------------
 public | alembic_version | table | quickticket
 public | events          | table | quickticket
 public | orders          | table | quickticket
(3 rows)

--- counts survived ---
 events_after_pvc_restart
--------------------------
                        5
(1 row)

 orders_after_pvc_restart
--------------------------
                       25
(1 row)

--- restart events ---
deployment.apps/events restarted
deployment "events" successfully rolled out
App fully up 18:57:24

--- smoke ---
/events=200
```

Timestamps:

```text
Disaster at             18:57:10
New pod K8s Ready       18:57:12
New pod DB SQL Ready    18:57:13
App fully up            18:57:24
```

Actual RTO with PVC:

```text
18:57:24 - 18:57:10 = 14 seconds
```

RTO improved from `26s` to `14s`, and the recovery no longer required copying a
dump or running `pg_restore`.

### 3. Backup CronJob

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
              imagePullPolicy: IfNotPresent
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
                  set -eu
                  cd /backups
                  file="quickticket_$(date -u +%Y%m%dT%H%M%SZ).dump"
                  pg_dump -Fc -f "$file"
                  ls -1t quickticket_*.dump | tail -n +6 | xargs -r rm -v
                  ls -lh "$file"
              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

Manual first run:

```bash
$ kubectl logs job/manual-1
-rw-r--r--    1 root     root        6.2K Jul  6 15:57 quickticket_20260706T155742Z.dump
```

After 7 manual runs, `manual-7` removed an older dump:

```bash
$ kubectl logs job/manual-7
removed 'quickticket_20260706T155747Z.dump'
-rw-r--r--    1 root     root        6.2K Jul  6 15:58 quickticket_20260706T155812Z.dump
```

Exactly 5 newest backups remained:

```bash
$ kubectl exec deployment/backup-inspector -- ls -la /backups
total 48
drwxrwxrwx    2 root     root          4096 Jul  6 15:58 .
drwxr-xr-x    1 root     root          4096 Jul  6 15:56 ..
-rw-r--r--    1 root     root          6371 Jul  6 15:57 quickticket_20260706T155752Z.dump
-rw-r--r--    1 root     root          6371 Jul  6 15:57 quickticket_20260706T155756Z.dump
-rw-r--r--    1 root     root          6371 Jul  6 15:58 quickticket_20260706T155802Z.dump
-rw-r--r--    1 root     root          6371 Jul  6 15:58 quickticket_20260706T155807Z.dump
-rw-r--r--    1 root     root          6371 Jul  6 15:58 quickticket_20260706T155812Z.dump
```
