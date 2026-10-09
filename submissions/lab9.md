# Lab 9 — Stateful Services & DB Reliability

**Branch:** `feature/lab9`  
**Environment:** k3d `k3d-quickticket`, PostgreSQL 17.11, Kubernetes, Alembic 1.18.4  
**Submission:** `submissions/lab9.md`  
**Tasks:** Task 1 (migrations and backup/restore), Task 2 (disaster recovery), Bonus (PVC and CronJob)

## Task 1 — Migrations & Backup/Restore (6 pts)

### 9.1–9.3 — Alembic initialization and migrations

Alembic was initialized with `alembic init migrations`. The connection in `alembic.ini` used `postgresql://quickticket:quickticket@localhost:15432/quickticket`, forwarded to the in-cluster PostgreSQL service. A baseline revision was generated and stamped against the pre-existing `events` and `orders` tables. A second revision added a nullable `email` field to `events`.

**`alembic history`:**

```text
598c51bd33f3 -> 0f25adc89825 (head), add email column to events
<base> -> 598c51bd33f3, baseline - pre-existing schema
```

**Applied revision (`alembic current`, after upgrade):**

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
0f25adc89825 (head)
```

The second migration's main operations were:

```python
def upgrade() -> None:
    op.add_column(
        'events',
        sa.Column('email', sa.String(255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('events', 'email')
```

### 9.4 — Migration under live load

The `mixedload` Deployment had **2/2 ready replicas**, with both Pods in `Running` state. Prometheus measured the 5xx counter increase over a one-minute window before and after the migration.

| Measurement | Observed result |
|---|---:|
| `mixedload` | 2/2 ready |
| 5xx last 1 minute, before upgrade | 2.1818181818181817 |
| Migration elapsed wall time | **913.91 ms** |
| 5xx last 1 minute, after upgrade | 2.1818181818181817 |
| Applied revision | `0f25adc89825 (head)` |

**`time alembic upgrade head`:**

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 598c51bd33f3 -> 0f25adc89825, add email column to events

________________________________________________________
Executed in  913.91 millis    fish           external
   usr time  711.26 millis    0.00 millis  711.26 millis
   sys time   89.68 millis    2.02 millis   87.66 millis
```

**Schema verification — `psql -c '\d events'`:**

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

**Observation:** no *measured increase* in the one-minute 5xx query. Both observations were nonzero, so this is not proof that there were no errors or that all individual requests were uninterrupted.

### 9.5 — `pg_dump` backup

Backup command:

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  pg_dump -U quickticket -Fc quickticket > /tmp/quickticket.dump
```

**`ls -lh` and `file`:**

```text
-rw-r--r-- 1 sato sato 7.2K Oct  9 02:42 /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
```

The file was copied into the PostgreSQL Pod as `/tmp/backup.dump`. **`pg_restore --list /tmp/backup.dump | head -25`:**

```text
;
; Archive created at 2026-10-08 23:42:44 UTC
;     dbname: quickticket
;     TOC Entries: 18
;     Compression: gzip
;     Dump Version: 1.16-0
;     Format: CUSTOM
;     Integer: 4 bytes
;     Offset: 8 bytes
;     Dumped from database version: 17.11
;     Dumped by pg_dump version: 17.11
;
;
; Selected TOC Entries:
;
220; 1259 16410 TABLE public alembic_version quickticket
218; 1259 16388 TABLE public events quickticket
217; 1259 16387 SEQUENCE public events_id_seq quickticket
3481; 0 0 SEQUENCE OWNED BY public events_id_seq quickticket
219; 1259 16396 TABLE public orders quickticket
3316; 2604 16391 DEFAULT public events id quickticket
3474; 0 16410 TABLE DATA public alembic_version quickticket
3472; 0 16388 TABLE DATA public events quickticket
3473; 0 16396 TABLE DATA public orders quickticket
3482; 0 0 SEQUENCE SET public events_id_seq quickticket
```

The custom-format dump was nonempty and contained schema and table-data entries for `events`, `orders`, and `alembic_version`.

### 9.6 — Simulated data loss and recovery

The `orders` table was deliberately removed with `DROP TABLE orders CASCADE`, and the dump was restored with:

```bash
kubectl exec "$POD" -- pg_restore -U quickticket -d quickticket \
  --clean --if-exists /tmp/backup.dump
```

| Check | `events` | `orders` |
|---|---:|---:|
| Before `DROP TABLE` | 5 | 50 |
| Immediately after `DROP TABLE orders CASCADE` | 5 (table remained) | Table missing (`relation "orders" does not exist`) |
| After `pg_restore` | 5 | 50 |

**Observed SQL and API results:**

```text
DROP TABLE
ERROR:  relation "orders" does not exist
LINE 1: SELECT count(*) FROM orders;
                             ^
/events=502
```

After restoration:

```text
List of relations: alembic_version, events, orders
SELECT count(*) FROM events;  -- 5
SELECT count(*) FROM orders;  -- 50
/events=200
```

The observed API status changed from **502** during the failure to **200** after recovery. The 502 is correlated with the exercise but was not independently root-caused from logs.

### 9.7 — RPO question

A single `pg_dump` provides a recovery point at the **time that dump was taken**, not at the moment of failure. All committed changes after the backup can be lost. For the subsequent pod-disaster test, the dump was created at **02:42:44** local equivalent (23:42:44 UTC) and the Pod was deleted at **02:46:52**, resulting in an approximately **4-minute-8-second potential data-loss window**. The counted orders happened to be unchanged (50 before and 50 after), so **0 lost orders were observed**; this does **not** establish a zero RPO for all data.

Improvements: schedule regular backups (implemented in the bonus at five-minute intervals); use WAL archiving and point-in-time recovery (PITR) to reduce the potential loss window; use verified off-node/off-cluster storage and regularly test restores. A PVC protects against pod replacement but is not a substitute for backups.

---

## Task 2 — Disaster Recovery Under Load (4 pts)

### 9.8 — Forced PostgreSQL pod deletion and restore

`mixedload` remained at **2/2 ready**. With no PostgreSQL PVC at this stage, force-deleting the Pod resulted in a replacement Pod whose database contained no relations (`Did not find any relations.`). A locally preserved dump was copied to the new Pod and restored; the `events` Deployment was restarted, and `/events` returned HTTP 200.

| Stage | Recorded local time |
|---|---|
| Healthy check | 02:46:48 |
| Disaster (`T_KILL`) | **02:46:52** |
| New Pod ready (`T_READY`) | **02:46:57** |
| Database restored (`T_RESTORED`) | **02:48:25** |
| Events Deployment rolled out (`T_APP_READY`) | **02:48:49** |

**Measured exercise RTO:** `02:48:49 − 02:46:52 = 117 seconds` (1 min 57 s). The successful `/events=200` check followed the rollout. Thus 117 seconds measures the time to recorded Deployment readiness; it is not a precisely timestamped first successful user request.

**RPO gap in orders:**

```text
Before forced deletion: 50 orders
After restore:         50 orders
Observed order loss:    0 rows
```

**Prometheus 5xx rate query (`[30s]`) collected after recovery:**

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1791503417.645,"0.08"]}]}}
```

This is a **single 30-second rate sample of 0.08 errors/s**, not a saved time-series curve covering the incident. No historical graph was captured in the supplied terminal output.

**Cause and mitigation:** the Deployment previously had no PVC; PostgreSQL's data directory was stored in the container's ephemeral writable filesystem. Recreating the Pod provisioned an empty database. Mount a PVC at the PostgreSQL data directory and continue taking separate backups (implemented below).

---

## Bonus — Persistent Storage & Automated Backup CronJob (2 pts)

### B.1 — PersistentVolumeClaim

The cluster had default StorageClass `local-path` (`rancher.io/local-path`). The Postgres manifest was modified to include a 1 GiB PVC, `PGDATA` in a subdirectory, and the `Recreate` Deployment strategy.

**Relevant `git diff -- k8s/postgres.yaml`:**

```diff
@@ -4,6 +4,8 @@ metadata:
   name: postgres
 spec:
   replicas: 1
+  strategy:
+    type: Recreate
   selector:
     matchLabels:
       app: postgres
@@ -31,6 +33,27 @@ spec:
               value: "quickticket"
             - name: POSTGRES_PASSWORD
               value: "quickticket"
+            - name: PGDATA
+              value: "/var/lib/postgresql/data/pgdata"
+          volumeMounts:
+            - name: data
+              mountPath: /var/lib/postgresql/data
+      volumes:
+        - name: data
+          persistentVolumeClaim:
+            claimName: postgres-data
+
+---
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
```

**PVC verification:**

```text
NAME            STATUS   CAPACITY   ACCESS MODES   STORAGECLASS
postgres-data   Bound    1Gi        RWO            local-path
```

After first attaching the new empty PVC, the saved pre-PVC dump was restored, recovering `events=5`, `orders=50`, and the nullable `email` column. After **another forced Pod deletion**, the replacement Pod contained all three tables **without** `pg_restore` or `seed.sql`.

**Repeat test timestamps (with PVC):**

| Stage | Recorded local time |
|---|---|
| Pod force-deleted (`T_KILL_PVC`) | **02:57:14** |
| New Pod ready (`T_READY_PVC`) | **02:57:20** |
| First explicit successful API check recorded (`T_APP_READY_PVC`) | **02:59:08** |

**Measured time to PostgreSQL Pod readiness:** **6 seconds**.  
**Time until the operator recorded successful API verification:** **114 seconds**.  
**Restore step:** not required; all 50 orders and 5 events remained available.

The initial smoke-check returned `HTTP 000` (curl connection failure), while later `events`/`gateway` logs already showed successful HTTP 200 requests at approximately **02:58:30**. An `events` Deployment restart was also performed, followed by `HTTP 200` from `kubectl exec deployment/mixedload -- curl ...`. Therefore, 114 seconds is a **conservative operator-observed verification time**, not proof that the service was unavailable for 114 seconds. The database returned to Ready within 6 seconds, without data restoration.

### B.2 — Automated backups with a Kubernetes CronJob

The supplied `labs/lab9/backup-storage.yaml` provisioned the separate `postgres-backups` PVC (1 GiB, `Bound`) and a running `backup-inspector` Deployment. The inspector image lacked `pg_dump`; backup creation was successfully performed by the `postgres:17-alpine` CronJob instead.

**Implemented file: `k8s/backup-cronjob.yaml`**

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
                  set -e

                  TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
                  BACKUP="/backups/quickticket_${TIMESTAMP}.dump"

                  echo "Creating backup: $BACKUP"

                  pg_dump -Fc -f "$BACKUP"

                  echo "Backup completed: $BACKUP"

                  cd /backups

                  ls -1t quickticket_*.dump | tail -n +6 | xargs -r rm -v

                  echo "Backup rotation completed"

              volumeMounts:
                - name: backups
                  mountPath: /backups

          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

**First manual run:**

```text
job.batch/manual-1 condition met
Creating backup: /backups/quickticket_20261009T000140Z.dump
Backup completed: /backups/quickticket_20261009T000140Z.dump
Backup rotation completed
```

The schedule was temporarily suspended during the controlled rotation test, then resumed (`SUSPEND=False`). Six additional manual Jobs (`manual-2` through `manual-7`) each completed successfully.

**`kubectl logs job/manual-7`:**

```text
Creating backup: /backups/quickticket_20261009T000328Z.dump
Backup completed: /backups/quickticket_20261009T000328Z.dump
removed 'quickticket_20261009T000253Z.dump'
Backup rotation completed
```

**`kubectl exec deployment/backup-inspector -- ls -la /backups`:**

```text
total 48
drwxrwxrwx    2 root     root          4096 Oct  9 00:03 .
drwxr-xr-x    1 root     root          4096 Oct  9 00:00 ..
-rw-r--r--    1 root     root          7286 Oct  9 00:03 quickticket_20261009T000301Z.dump
-rw-r--r--    1 root     root          7286 Oct  9 00:03 quickticket_20261009T000308Z.dump
-rw-r--r--    1 root     root          7286 Oct  9 00:03 quickticket_20261009T000315Z.dump
-rw-r--r--    1 root     root          7286 Oct  9 00:03 quickticket_20261009T000321Z.dump
-rw-r--r--    1 root     root          7286 Oct  9 00:03 quickticket_20261009T000328Z.dump
```

**Count verification:**

```text
kubectl exec deployment/backup-inspector -- sh -c \
  'find /backups -maxdepth 1 -name "quickticket_*.dump" -type f | wc -l'
5
```

**Result:** after seven manual runs, exactly **five newest backup dumps** remained, and the `manual-7` log confirmed deletion of an older dump. The CronJob was left active on its `*/5 * * * *` schedule.

---

## Conclusion

- Alembic baseline and nullable `email VARCHAR(255)` migration completed under `mixedload`, with no observed increase in the sampled 5xx count and a **913.91 ms** command duration.
- Custom-format `pg_dump` was validated and successfully restored the `orders` table and working HTTP API after simulated deletion.
- Without PVC, PostgreSQL lost its database upon Pod replacement; the measured readiness-based recovery took **117 seconds**, including manual dump restoration.
- With a PVC, data survived Pod replacement without restoration, and PostgreSQL Pod readiness took **6 seconds**. The later **114-second** API verification timestamp reflects manual investigation rather than a precisely measured downtime period.
- A five-minute CronJob successfully created and rotated backups, leaving exactly **five** files after seven controlled test runs.

**Production considerations:** use Kubernetes Secrets for credentials, a proper StatefulSet or managed PostgreSQL for operational durability, durable off-cluster backup destinations, WAL/PITR for tighter RPO, and continuous synthetic monitoring to measure exact service RTO.
