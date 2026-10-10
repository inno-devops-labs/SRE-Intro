# Lab 9 — Stateful Services & DB Reliability

## Task 1 — Migrations & Backup/Restore

---

## 9.1 Initialize Alembic

### Commands

```bash
alembic init migrations
```

### Output

```
  Creating directory /mnt/c/Users/IP/Desktop/do_slez/second_course/Sum26-SRE/SRE-Intro/migrations ...  done
  Creating directory /mnt/c/Users/IP/Desktop/do_slez/second_course/Sum26-SRE/SRE-Intro/migrations/versions ...  done
  Generating /mnt/c/Users/IP/Desktop/do_slez/second_course/Sum26-SRE/SRE-Intro/alembic.ini ...  done
  Generating /mnt/c/Users/IP/Desktop/do_slez/second_course/Sum26-SRE/SRE-Intro/migrations/env.py ...  done
  Generating /mnt/c/Users/IP/Desktop/do_slez/second_course/Sum26-SRE/SRE-Intro/migrations/README ...  done
  Generating /mnt/c/Users/IP/Desktop/do_slez/second_course/Sum26-SRE/SRE-Intro/migrations/script.py.mako ...  done
  Please edit configuration/connection/logging settings in /mnt/c/Users/IP/Desktop/do_slez/second_course/Sum26-SRE/SRE-Intro/alembic.ini before proceeding.
  ```

### Configuration

Edited `alembic.ini`:

```ini
sqlalchemy.url = postgresql://quickticket:quickticket@localhost:5432/quickticket
```

---

## 9.2 Baseline Existing Schema

### Create baseline revision

Command:

```bash
alembic revision -m "baseline - pre-existing schema"
```

Output:

```text
  Generating /mnt/c/Users/IP/Desktop/do_slez/second_course/Sum26-SRE/SRE-Intro/migrations/versions/0297af066379_baseline_pre_existing_schema.py ...  done
```

### Stamp database

Command:

```bash
alembic stamp head
```

Output:

```text
Handling connection for 5432
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running stamp_revision  -> 0297af066379
```

### Verify current revision

Command:

```bash
alembic current
```

Output:

```text
Handling connection for 5432
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
0297af066379 (head)
```

---

## 9.3 Create Migration

Command:

```bash
alembic revision -m "add email column to events"
```

Migration contents:

```python
def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("email", sa.String(255), nullable=True),
    )

def downgrade() -> None:
    op.drop_column("events", "email")
```

---

## 9.4 Run Migration Under Load

### Verify mixedload

Command:

```bash
kubectl get deployment mixedload
```

Output:

```text
NAME        READY   UP-TO-DATE   AVAILABLE   AGE
mixedload   2/2     2            2           29m
```

---

### Prometheus error rate BEFORE migration

Command:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))' \
  | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('5xx last 1min:', r[0]['value'][1] if r else 0)"
```

Output:

```text
5xx last 1min: 0
```

---

### Apply migration

Command:

```bash
time alembic upgrade head
```

Output:

```text
Handling connection for 5432
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0297af066379 -> bd7982ea9e79, add email column to events
alembic upgrade head  0.50s user 0.26s system 19% cpu 3.807 total
```

---

### Verify schema

Command:

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -c '\d events'
```

Output:

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

---

### Prometheus error rate AFTER migration

Command:

```bash
 kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))' \
  | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('5xx last 1min:', r[0]['value'][1] if r else 0)"
```

Output:

```text
5xx last 1min: 0
```

---

### Analysis

The migration completed successfully while the application was under load. Since PostgreSQL treats adding a nullable column as a metadata-only operation, request processing continued normally. The Prometheus error rate remained unchanged before and after the migration, indicating that the schema update caused no noticeable downtime.

---

## 9.5 Create Backup

### Create pg_dump

Command:

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  pg_dump -U quickticket -Fc quickticket > /tmp/quickticket.dump
```

---

### Verify backup file

Command:

```bash
ls -lh /tmp/quickticket.dump
```

Output:

```text
-rw-r--r-- 1 slickip slickip 7.2K Jul  3 18:23 /tmp/quickticket.dump
```

Command:

```bash
file /tmp/quickticket.dump
```

Output:

```text
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
```

---

### Verify the contents (requires running pg_restore --list inside the Postgres pod — the host doesn't have the client):

Command:

```bash
POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)
kubectl cp /tmp/quickticket.dump $POD:/tmp/backup.dump
kubectl exec $POD -- pg_restore --list /tmp/backup.dump | head -25
```

Output:

```text
;
; Archive created at 2026-07-03 15:23:52 UTC
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
218; 1259 16390 TABLE public events quickticket
217; 1259 16389 SEQUENCE public events_id_seq quickticket
3481; 0 0 SEQUENCE OWNED BY public events_id_seq quickticket
219; 1259 16398 TABLE public orders quickticket
3316; 2604 16393 DEFAULT public events id quickticket
3474; 0 16412 TABLE DATA public alembic_version quickticket
3472; 0 16390 TABLE DATA public events quickticket
3473; 0 16398 TABLE DATA public orders quickticket
3482; 0 0 SEQUENCE SET public events_id_seq quickticket
```

---

### Analysis

The backup was successfully created with `pg_dump` in PostgreSQL custom format. The resulting dump file is small because the lab database contains only seed data and a limited number of generated orders. The `file` command confirms that `/tmp/quickticket.dump` is a valid PostgreSQL custom database dump

The `pg_restore --list` output confirms that the backup contains the expected database objects: `events`, `orders`, `alembic_version`, the `events_id_seq` sequence, table data, and sequence state. This means the backup includes both schema and data and can be used for recovery in the next step

---

## 9.6 Simulate Data Loss and Restore

### Record row counts before disaster

Command:

```bash
POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)

kubectl exec $POD -- psql -U quickticket -d quickticket \
  -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
```

Output:

```text
 count 
-------
     5
(1 row)

 count 
-------
    50
(1 row)
```

---

### Drop the `orders` table

Command:

```bash
kubectl exec $POD -- \
  psql -U quickticket -d quickticket \
  -c 'DROP TABLE orders CASCADE'
```

Output:

```text
DROP TABLE
```

### Row counts after DROP

After dropping the `orders` table, the `orders` count is not available because the table no longer exists.

---

### Verify API failure

Command:

```bash
kubectl run smoke --image=curlimages/curl:latest --rm -i --restart=Never --quiet \
  --command -- curl -s -o /dev/null -w "/events=%{http_code}\n" http://gateway:8080/events
```

Output:

```text
/events=502
```

---

### Restore the database

Command:

```bash
kubectl exec $POD -- \
  pg_restore -U quickticket -d quickticket \
  --clean --if-exists /tmp/backup.dump
```

---

### Verify restored data

Command:

```bash
kubectl exec $POD -- psql -U quickticket -d quickticket \
  -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
```

Output:

```text
 count 
-------
     5
(1 row)

 count 
-------
    50
(1 row)
```

---

### Verify API recovery

Command:

```bash
kubectl run smoke --image=curlimages/curl:latest --rm -i --restart=Never --quiet \
  --command -- curl -s -o /dev/null -w "/events=%{http_code}\n" http://gateway:8080/events
```

Output:

```text
/events=200
```

---

### Analysis

Dropping the `orders` table immediately broke the application because the checkout workflow depends on this table. The backup created with `pg_dump` was successfully restored using `pg_restore`, recovering both the database schema and its contents. After the restore completed, the row counts matched the pre-disaster state and the API became available again, demonstrating that the backup could be used for successful disaster recovery

## What is the RPO of the current setup?

With a single manual `pg_dump`, the Recovery Point Objective (RPO) is equal to the time elapsed since the most recent backup. Any changes made after that backup would be lost if the database failed before another backup was created.

To improve the RPO, continuous WAL archiving together with Point-in-Time Recovery (PITR) should be implemented. This would allow restoring the database to a point very close to the failure time instead of only to the last full backup

### Alembic history

Command:

```bash
alembic history
```

Output:

```
0297af066379 -> bd7982ea9e79 (head), add email column to events
<base> -> 0297af066379, baseline - pre-existing schema
```

----

# Task 2 — Disaster Recovery Under Load (Optional)

## 9.8 Kill Postgres and Recover

### Record initial state

Command:

```bash
T0=$(date +%H:%M:%S)
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -c 'SELECT count(*) FROM orders'
echo "healthy at $T0"
```

Output:

```text
 count 
-------
    50
(1 row)
healthy at 18:47:52
```

---

### Force-delete the Postgres pod

Command:

```bash
kubectl delete pod -l app=postgres --grace-period=0 --force
T_KILL=$(date +%H:%M:%S)
```

Output:

```text
Warning: Immediate deletion does not wait for confirmation that the running resource has been terminated. The resource may continue to run on the cluster indefinitely.
pod "postgres-7c7ffc4b-7mfk6" force deleted
```

---

### Wait for the new pod

Command:

```bash
kubectl wait --for=condition=Ready pod -l app=postgres --timeout=60s
T_READY=$(date +%H:%M:%S)
```

Output:

```text
pod/postgres-7c7ffc4b-plmlk condition met
```

---

### Verify database contents

Command:

```bash
NEW_POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)

kubectl exec $NEW_POD -- \
  psql -U quickticket -d quickticket -c '\dt'
```

Output:

```text
Did not find any relations
```

---

### Restore the backup

Command:

```bash
kubectl cp /tmp/quickticket.dump $NEW_POD:/tmp/backup.dump

kubectl exec $NEW_POD -- \
  pg_restore -U quickticket -d quickticket \
  --clean --if-exists /tmp/backup.dump

T_RESTORED=$(date +%H:%M:%S)
```

---

### Restart the events service

Command:

```bash
kubectl rollout restart deployment/events

kubectl rollout status deployment/events --timeout=30s

T_APP_READY=$(date +%H:%M:%S)
```

Output:

```text
Waiting for deployment "events" rollout to finish: 1 old replicas are pending termination...
Waiting for deployment "events" rollout to finish: 1 old replicas are pending termination...
deployment "events" successfully rolled out
```

---

### Recovery timestamps

Command:

```bash
echo "
Disaster at      $T_KILL
New pod ready    $T_READY
Restored         $T_RESTORED
App fully up     $T_APP_READY
"
```

Output:

```text
Disaster at      18:48:28
New pod ready    18:48:45
Restored         18:49:38
App fully up     18:50:14
```

---

### Analysis

Forcefully deleting the Postgres pod simulated a complete database failure while the application continued to receive traffic from the `mixedload` deployment. Kubernetes successfully created a replacement pod, but the database was completely empty because the Postgres deployment stored its data on ephemeral container storage instead of a PersistentVolume. Restoring the previously created `pg_dump` backup recreated the database schema and data. After restarting the `events` deployment to re-establish database connections, the application returned to normal operation

---

## 9.9 Calculate RTO and RPO

### Recovery Time Objective (RTO)
| Phase | Time |
|-------|------|
| Disaster | 18:48:28 |
| New pod ready | 18:48:45 |
| Database restored | 18:49:38 |
| Application ready | 18:50:14 |

**Actual RTO:**

```text
RTO = 106 seconds (1 minute 46 seconds)
```

---

### Recovery Point Objective (RPO)

| Stage | Orders |
|-------|-------:|
| Before disaster | 50 |
| After restore | 50 |
| Lost orders | 0 |

**Actual RPO:**

```text
backup at 18:23:52, disaster at 18:48:28
24 minutes 36 seconds / 1476 seconds
```

**Quantify lost:**

```text
50 - 50 = 0
```

---

### Prometheus error rate during the incident

Command:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B30s%5D))'
```

Output:

```text
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783094933.814,"0"]}]}}% 
```

---

### Analysis

The measured Recovery Time Objective (RTO) was 106 seconds, which includes the time required to recreate the Postgres pod, restore the backup, and restart the dependent application service. The Recovery Point Objective (RPO) was 24 minutes and 36 seconds, corresponding to the time elapsed between creating the backup and the simulated failure. Although the theoretical RPO was over 24 minutes, no application data was lost in this experiment because the restored backup already contained all 50 orders that existed before the disaster (RPO gap = 0 records). The Prometheus query reported a 5xx error rate of 0 after recovery, confirming that the application successfully returned to normal operation.

## The new Postgres pod was empty. Why? How would you eliminate this failure mode?

The new Postgres pod was empty because the PostgreSQL deployment did not use a PersistentVolumeClaim (PVC). All database files were stored on the pod's ephemeral filesystem, which is destroyed when the pod is deleted. As a result, recreating the pod started PostgreSQL with a fresh, empty data directory.

This failure mode can be eliminated by attaching a PersistentVolumeClaim to the Postgres deployment. With persistent storage, the database files survive pod restarts and recreations, allowing a new pod to reuse the existing data instead of starting with an empty database. This is implemented in the Bonus task.

# Bonus Task — Persistent Storage + Automated Backup CronJob

## B.1 Add Persistent Storage to Postgres

### Updated `k8s/postgres.yaml`

Added a PersistentVolumeClaim:

```yaml
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

Updated Postgres container:

```yaml
containers:
  - name: postgres
    image: postgres:17-alpine
    env:
      - { name: POSTGRES_DB, value: quickticket }
      - { name: POSTGRES_USER, value: quickticket }
      - { name: POSTGRES_PASSWORD, value: quickticket }
      - { name: PGDATA, value: /var/lib/postgresql/data/pgdata }  # subdir — avoid lost+found
    volumeMounts:
      - { name: data, mountPath: /var/lib/postgresql/data }
volumes:
  - name: data
    persistentVolumeClaim:
      claimName: postgres-data
```

git diff:
```bash
diff --git a/k8s/postgres.yaml b/k8s/postgres.yaml
index bf83d6a..942a99f 100644
--- a/k8s/postgres.yaml
+++ b/k8s/postgres.yaml
@@ -17,15 +17,29 @@ spec:
       containers:
         - name: postgres
           image: postgres:17-alpine
-          ports:
-            - containerPort: 5432
           env:
-            - name: POSTGRES_DB
-              value: "quickticket"
-            - name: POSTGRES_USER
-              value: "quickticket"
-            - name: POSTGRES_PASSWORD
-              value: "quickticket"
+            - { name: POSTGRES_DB, value: quickticket }^M
+            - { name: POSTGRES_USER, value: quickticket }^M
+            - { name: POSTGRES_PASSWORD, value: quickticket }^M
+            - { name: PGDATA, value: /var/lib/postgresql/data/pgdata }  # subdir — avoid lost+found^M
+          volumeMounts:^M
+            - { name: data, mountPath: /var/lib/postgresql/data }^M
+      volumes:^M
+        - name: data^M
+          persistentVolumeClaim:^M
+            claimName: postgres-data^M
+^M
+---^M
+apiVersion: v1^M
+kind: PersistentVolumeClaim^M
+metadata:^M
+  name: postgres-data^M
+spec:^M
+  accessModes: [ReadWriteOnce]^M
+  resources:^M
+    requests:^M
+      storage: 1Gi^M
+      ^M
 ---
 apiVersion: v1
 kind: Service
@@ -36,5 +50,5 @@ spec:
     app: postgres
   ports:
     - port: 5432
-      targetPort: 5432
+      targetPort: 5432  ^M
   type: ClusterIP
\ No newline at end of file
```
---

### Apply Postgres manifest

Command:

```bash
kubectl apply -f k8s/postgres.yaml
kubectl rollout status deployment/postgres --timeout=60s
```

Output:

```text
deployment.apps/postgres configured
persistentvolumeclaim/postgres-data unchanged
service/postgres unchanged
deployment "postgres" successfully rolled out
```

---

### Re-seed database once

Command:

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket < app/seed.sql
```

Output:

```text
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

---

### Verify PVC

Command:

```bash
kubectl get pvc postgres-data
```

Output:

```text
NAME            STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS   VOLUMEATTRIBUTESCLASS   AGE
postgres-data   Bound    pvc-c6228146-c3b4-47f5-a2e1-af835172b569   1Gi        RWO            local-path     <unset>                 3m40s
```

---

### Re-run disaster test from 9.8 with PVC

### Record initial state

Command:

```bash
T0=$(date +%H:%M:%S)
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -c 'SELECT count(*) FROM orders'
echo "healthy at $T0"
```

Output:

```text
 count 
-------
    25
(1 row)
```

---

### Force-delete Postgres pod

Command:

```bash
kubectl delete pod -l app=postgres --grace-period=0 --force
T_KILL=$(date +%H:%M:%S)
```

Output:

```text
Warning: Immediate deletion does not wait for confirmation that the running resource has been terminated. The resource may continue to run on the cluster indefinitely.
pod "postgres-7649d6985b-rkbmw" force deleted
```

---

### Wait for new pod

Command:

```bash
kubectl wait --for=condition=Ready pod -l app=postgres --timeout=60s
T_READY=$(date +%H:%M:%S)
```

Output:

```text
pod/postgres-7649d6985b-s9tpv condition met
```

---

### Verify data persisted

Command:

```bash
NEW_POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)

kubectl exec $NEW_POD -- \
  psql -U quickticket -d quickticket -c '\dt'

kubectl exec $NEW_POD -- \
  psql -U quickticket -d quickticket -c 'SELECT count(*) FROM orders'
```

Output:

```text
           List of relations
 Schema |  Name  | Type  |    Owner    
--------+--------+-------+-------------
 public | events | table | quickticket
 public | orders | table | quickticket
(2 rows)

 count 
-------
    25
(1 row)
```

---

### Recovery timestamps

Command:

```bash
T_APP_READY=$(date +%H:%M:%S)

echo "
Disaster at      $T_KILL
New pod ready    $T_READY
App fully up     $T_APP_READY
"
```

Output:

```text
Disaster at      19:28:50
New pod ready    19:29:10
App fully up     19:29:38
```

### Recovery Time Objective after PVC

```text
RTO = 48 seconds
```
---

### Analysis

```md
After adding a PersistentVolumeClaim to the Postgres deployment, deleting the Postgres pod no longer caused data loss. The replacement pod reused the same persistent volume and found the existing database schema and data. Unlike the previous disaster test, no `pg_restore` step was required. The new RTO was reduced to approximately the pod restart time, because recovery only required Kubernetes to recreate the pod and attach the existing volume.
```

---

## B.2 Automated Backup CronJob

### Apply backup storage

Command:

```bash
kubectl apply -f labs/lab9/backup-storage.yaml
kubectl rollout status deployment/backup-inspector --timeout=30s
kubectl get pvc postgres-backups
```

Output:

```text
(.venv) ➜  SRE-Intro git:(feature/lab8) ✗ kubectl apply -f labs/lab9/backup-storage.yaml
persistentvolumeclaim/postgres-backups created
deployment.apps/backup-inspector created
(.venv) ➜  SRE-Intro git:(feature/lab8) ✗ kubectl rollout status deployment/backup-inspector --timeout=30s
Waiting for deployment "backup-inspector" rollout to finish: 0 of 1 updated replicas are available...
deployment "backup-inspector" successfully rolled out
(.venv) ➜  SRE-Intro git:(feature/lab8) ✗ kubectl get pvc postgres-backups
NAME               STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS   VOLUMEATTRIBUTESCLASS   AGE
postgres-backups   Bound    pvc-a0f3efa7-08e6-40d6-b862-14d30c4bdd1a   1Gi        RWO            local-path     <unset>                 8m58s
```

---

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
            - name: postgres-backup
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
                  TS=$(date -u +%Y%m%dT%H%M%SZ)
                  FILE="/backups/quickticket_${TS}.dump"

                  echo "Creating backup: ${FILE}"
                  pg_dump -Fc -f "${FILE}"

                  echo "Applying retention policy: keep 5 newest backups"
                  cd /backups
                  ls -1t quickticket_*.dump | tail -n +6 | xargs -r rm -v

                  echo "Remaining backups:"
                  ls -lh /backups
              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

---

### Apply CronJob

Command:

```bash
kubectl apply -f k8s/backup-cronjob.yaml
```

Output:

```text
cronjob.batch/postgres-backup created
```

---

### Trigger first manual backup

Command:

```bash
kubectl create job --from=cronjob/postgres-backup manual-1
kubectl wait --for=condition=Complete job/manual-1 --timeout=60s
kubectl logs job/manual-1
```

Output:

```text
kubectl logs job/manual-1
job.batch/manual-1 created
job.batch/manual-1 condition met
Creating backup: /backups/quickticket_20260703T163647Z.dump
Applying retention policy: keep 5 newest backups
Remaining backups:
total 8K     
-rw-r--r--    1 root     root        5.3K Jul  3 16:36 quickticket_20260703T163647Z.dump
```

---

### Verify retention after 7 backups

Command:

```bash
for i in 2 3 4 5 6 7; do
  kubectl create job --from=cronjob/postgres-backup manual-$i
  kubectl wait --for=condition=Complete job/manual-$i --timeout=30s
done
```

Output:

```text
job.batch/manual-2 created
job.batch/manual-2 condition met
job.batch/manual-3 created
job.batch/manual-3 condition met
job.batch/manual-4 created
job.batch/manual-4 condition met
job.batch/manual-5 created
job.batch/manual-5 condition met
job.batch/manual-6 created
job.batch/manual-6 condition met
job.batch/manual-7 created
job.batch/manual-7 condition met
```

---

### Logs from manual-7

Command:

```bash
kubectl logs job/manual-7
```

Output:

```text
Creating backup: /backups/quickticket_20260703T163731Z.dump
Applying retention policy: keep 5 newest backups
removed 'quickticket_20260703T163716Z.dump'
Remaining backups:
total 40K    
-rw-r--r--    1 root     root        5.3K Jul  3 16:37 quickticket_20260703T163719Z.dump
-rw-r--r--    1 root     root        5.3K Jul  3 16:37 quickticket_20260703T163722Z.dump
-rw-r--r--    1 root     root        5.3K Jul  3 16:37 quickticket_20260703T163725Z.dump
-rw-r--r--    1 root     root        5.3K Jul  3 16:37 quickticket_20260703T163728Z.dump
-rw-r--r--    1 root     root        5.3K Jul  3 16:37 quickticket_20260703T163731Z.dump
```

---

### Verify only 5 newest backups remain

Command:

```bash
kubectl exec deployment/backup-inspector -- ls -la /backups
```

Output:

```text
total 48
drwxrwxrwx    2 root     root          4096 Jul  3 16:37 .
drwxr-xr-x    1 root     root          4096 Jul  3 16:27 ..
-rw-r--r--    1 root     root          5439 Jul  3 16:37 quickticket_20260703T163719Z.dump
-rw-r--r--    1 root     root          5439 Jul  3 16:37 quickticket_20260703T163722Z.dump
-rw-r--r--    1 root     root          5439 Jul  3 16:37 quickticket_20260703T163725Z.dump
-rw-r--r--    1 root     root          5439 Jul  3 16:37 quickticket_20260703T163728Z.dump
-rw-r--r--    1 root     root          5439 Jul  3 16:37 quickticket_20260703T163731Z.dump
```

---

### Analysis

```md
The backup CronJob runs every five minutes and uses the `postgres:17-alpine` image, which includes `pg_dump`. Each run creates a compressed custom-format dump named with a UTC timestamp and stores it on the `postgres-backups` PVC. The CronJob uses `concurrencyPolicy: Forbid`, so Kubernetes will not start a new backup job if the previous one is still running.

The retention command keeps only the five newest backup files and deletes older dumps. After running seven manual jobs, the backup directory contained exactly five dump files, confirming that backup rotation worked correctly.
```

---

## Bonus Answer

```md
Adding a PVC eliminates the main failure mode from Task 2. Previously, the Postgres pod used ephemeral storage, so deleting the pod deleted the database files as well. With a PersistentVolumeClaim, the database files are stored outside the pod lifecycle. When Kubernetes recreates the pod, the new pod mounts the same volume and continues using the existing data directory.

The automated CronJob improves recovery reliability by creating regular database backups and rotating old backups. This reduces the RPO from "time since the last manual backup" to at most the configured backup interval, which is five minutes in this lab.
```