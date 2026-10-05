# Lab 9 — Stateful Services & DB Reliability

Liubov Utenysheva, CBS-03

---

## Setup

Verified the seeded state, applied the Lab 8 `mixedload` (full `reserve → pay` checkout chain, so `orders`
keeps growing during the experiments), set up the Python tooling, and bridged the in-cluster Postgres to the
host with a port-forward:

```bash
$ kubectl apply -f labs/lab8/mixedload.yaml
deployment.apps/mixedload created
$ kubectl rollout status deployment/mixedload --timeout=60s
deployment "mixedload" successfully rolled out
$ kubectl get deployment mixedload
NAME        READY   UP-TO-DATE   AVAILABLE   AGE
mixedload   2/2     2            2           44s
```

```bash
$ python3 -m venv .venv
$ .venv/bin/pip install alembic==1.18.4 psycopg2-binary==2.9.11 sqlalchemy==2.0.49
$ kubectl port-forward svc/postgres 5432:5432 &
$ .venv/bin/python3 -c "import psycopg2; c=psycopg2.connect('postgresql://quickticket:quickticket@localhost:5432/quickticket'); cur=c.cursor(); cur.execute('SELECT count(*) FROM events'); print('events:', cur.fetchone()[0])"
events: 5
```

> **Note on cluster state:** the Postgres Deployment had **no** `volumeMounts`/PVC — the DB lived on ephemeral
> pod storage. `k8s/postgres.yaml` at the start of the lab only defined the Deployment and the Service. That is
> exactly the failure mode Task 2 exposes and the Bonus fixes.

---

## Task 1 — Migrations & Backup/Restore (6 pts)

### 9.1–9.2: Initialize Alembic and baseline the existing schema

```bash
$ .venv/bin/alembic init migrations
Creating directory /home/uld16/SRE-Intro/migrations ...  done
Creating directory /home/uld16/SRE-Intro/migrations/versions ...  done
Generating /home/uld16/SRE-Intro/migrations/README ...  done
Generating /home/uld16/SRE-Intro/alembic.ini ...  done
Generating /home/uld16/SRE-Intro/migrations/script.py.mako ...  done
Generating /home/uld16/SRE-Intro/migrations/env.py ...  done
Please edit configuration/connection/logging settings in /home/uld16/SRE-Intro/alembic.ini before proceeding.
```

`alembic.ini` edited: `sqlalchemy.url = postgresql://quickticket:quickticket@localhost:5432/quickticket`

The DB already had `events` and `orders` from `seed.sql`, so I created an empty baseline revision and stamped it
as applied — Alembic now treats the pre-existing schema as "already migrated":

```bash
$ .venv/bin/alembic revision -m "baseline - pre-existing schema"
Generating /home/uld16/SRE-Intro/migrations/versions/8bd0c4115f21_baseline_pre_existing_schema.py ...  done
$ .venv/bin/alembic stamp head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running stamp_revision  -> 8bd0c4115f21
$ .venv/bin/alembic current
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
8bd0c4115f21 (head)
```

### 9.3: The real migration

```bash
$ .venv/bin/alembic revision -m "add email column to events"
Generating /home/uld16/SRE-Intro/migrations/versions/cf66c14f522d_add_email_column_to_events.py ...  done
```

`migrations/versions/cf66c14f522d_add_email_column_to_events.py` (the relevant part):

```python
def upgrade() -> None:
    # Adding a nullable column is a metadata-only change in PostgreSQL 11+ —
    # no table rewrite, no blocking lock on SELECT/INSERT. Safe under load.
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'email')
```

**Proof 1 — `alembic history` (two revisions: baseline + email):**

```text
8bd0c4115f21 -> cf66c14f522d (head), add email column to events
<base> -> 8bd0c4115f21, baseline - pre-existing schema
```

### 9.4: Run the migration under load

`mixedload` (2/2) was running the full checkout flow while the migration was applied.

Baseline 5xx (Prometheus, 1-minute window) **before** the migration:

```bash
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
    'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))' \
  | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('5xx last 1min:', r[0]['value'][1] if r else 0)"
5xx last 1min: 0
```

**Proof 3 — `time alembic upgrade head`:**

```bash
$ time .venv/bin/alembic upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 8bd0c4115f21 -> cf66c14f522d, add email column to events

real	0m0.343s
user	0m0.268s
sys	0m0.043s
```

0.34 s total — well under the 1 s expectation for a nullable add.

**Proof 2 — `\d events` showing the new column:**

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

5xx re-checked **after** the migration (same 1-minute window query):

```text
5xx last 1min: 0
```

**Proof 4 — 5xx before / after migration:** `0` before, `0` after. The migration was a metadata-only change
(nullable column, PostgreSQL 11+), so live traffic saw no additional errors.

### 9.5: Create a `pg_dump` backup

```bash
$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
    pg_dump -U quickticket -Fc quickticket > /tmp/quickticket.dump
$ ls -lh /tmp/quickticket.dump
-rw-r--r-- 1 uld16 uld16 9.0K Oct  5 13:44 /tmp/quickticket.dump
$ file /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
```

The host has no `pg_restore` client, so I copied the dump into the Postgres pod to inspect the table-of-contents:

```bash
$ kubectl cp /tmp/quickticket.dump $POD:/tmp/backup.dump
$ kubectl exec $POD -- pg_restore --list /tmp/backup.dump | head -25
;
; Archive created at 2026-10-05 10:44:36 UTC
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
220; 1259 16412 TABLE public alembic_version quickticket
218; 1259 16389 TABLE public events quickticket
217; 1259 16388 SEQUENCE public events_id_seq quickticket
3481; 0 0 SEQUENCE OWNED BY public events_id_seq quickticket
219; 1259 16397 TABLE public orders quickticket
3316; 2604 16392 DEFAULT public events id quickticket
3474; 0 16412 TABLE DATA public alembic_version quickticket
3472; 0 16389 TABLE DATA public events quickticket
3473; 0 16397 TABLE DATA public orders quickticket
3482; 0 0 SEQUENCE SET public events_id_seq quickticket
```

**Proof 5 — backup is valid:** 9.0 K custom-format archive with 18 TOC entries: both tables (`events`, `orders`),
the `alembic_version` table, sequences, and their data.

### 9.6: Simulate data loss → restore

**Proof 6 — row counts before / after DROP / after restore:**

Before:

```bash
$ kubectl exec $POD -- psql -U quickticket -d quickticket -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
 count
-------
     5
(1 row)

 count
-------
   100
(1 row)
```

Drop the table (cascades to the `orders_event_id_fkey` FK) and watch the API break:

```bash
$ kubectl exec $POD -- psql -U quickticket -d quickticket -c 'DROP TABLE orders CASCADE'
DROP TABLE
$ kubectl run smoke --image=curlimages/curl:latest --rm -i --restart=Never --quiet \
    --command -- curl -s -o /dev/null -w "/events=%{http_code}\n" http://gateway:8080/events
/events=502
```

After DROP: `events` = 5, `orders` = **table gone** (the gateway returned 502 — the checkout chain was broken).

Restore from the backup:

```bash
$ kubectl exec $POD -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
$ kubectl exec $POD -- psql -U quickticket -d quickticket -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
 count
-------
     5
(1 row)

 count
-------
   100
(1 row)
$ kubectl run smoke --image=curlimages/curl:latest --rm -i --restart=Never --quiet \
    --command -- curl -s -o /dev/null -w "/events=%{http_code}\n" http://gateway:8080/events
/events=200
```

| State | events | orders | `/events` |
|---|---:|---:|---|
| Before disaster | 5 | 100 | 200 |
| After `DROP TABLE orders CASCADE` | 5 | *(gone)* | 502 |
| After `pg_restore --clean --if-exists` | 5 | 100 | 200 |

**Proof 7 — RPO of the current setup (single `pg_dump`):**

The RPO equals the age of the last dump: everything committed after the `pg_dump` ran is lost on recovery. With a
manual, one-off dump that is unbounded (minutes to hours to days, depending on when someone last ran it) — in my
run the dump was ~46 s old when the disaster hit, so the practical RPO was under a minute, but nothing enforces
that. To improve it: (1) schedule dumps automatically — the Bonus CronJob caps RPO at the schedule interval
(5 minutes); (2) for a true low-RPO, add continuous WAL archiving (`archive_command` / `wal-g` or
`pg_basebackup` + PITR) so recovery can replay transactions up to the second before the failure, making RPO
approach 0.

---

## Task 2 — Disaster Recovery Under Load (4 pts)

`mixedload` kept running the whole time. Wall-clock timestamps:

```bash
$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
    psql -U quickticket -d quickticket -c 'SELECT count(*) FROM orders'
 count
-------
   100
(1 row)
healthy at 13:45:21

$ kubectl delete pod -l app=postgres --grace-period=0 --force
Warning: Immediate deletion does not wait for confirmation that the running resource has been terminated. The resource may continue to run on the cluster indefinitely.
pod "postgres-85d6f95ff6-w4p48" force deleted
# disaster at 13:45:22

$ kubectl wait --for=condition=Ready pod -l app=postgres --timeout=60s
pod/postgres-85d6f95ff6-52fs5 condition met
# new pod ready at 13:45:30

$ kubectl exec postgres-85d6f95ff6-52fs5 -- psql -U quickticket -d quickticket -c '\dt'
Did not find any relations.

$ kubectl cp /tmp/quickticket.dump postgres-85d6f95ff6-52fs5:/tmp/backup.dump
$ kubectl exec postgres-85d6f95ff6-52fs5 -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
# restored at 13:45:31

$ kubectl rollout restart deployment/events
deployment.apps/events restarted
$ kubectl rollout status deployment/events --timeout=30s
deployment "events" successfully rolled out
# app fully up at 13:45:41
```

**Timestamps for the four phases:**

| Phase | Time |
|---|---|
| Disaster (pod force-deleted) | 13:45:22 |
| New pod ready | 13:45:30 |
| Restored from `pg_dump` | 13:45:31 |
| App fully up (events rolled out) | 13:45:41 |

**Actual RTO = `T_APP_READY − T_KILL` = 13:45:41 − 13:45:22 = 19 seconds.**

**RPO gap in records:** orders before disaster = 100, orders after restore = 100 → **0 rows lost**. The backup
was taken at 13:44:36, 46 s before the kill, and `mixedload` had not committed a new order in that window (the
count stayed at 100), so this particular run happened to have an empty RPO gap — but the exposure was real: any
order written in those 46 s would have been gone.

**Prometheus 5xx rate around the incident** (`sum(rate(gateway_requests_total{status=~"5.."}[30s]))`, 15 s steps):

```text
13:42:12 0.0
13:42:27 0.0
13:42:42 0.0
13:42:57 0.0
13:43:12 0.04
13:43:27 0.04
13:43:42 0.0
13:43:57 0.0
13:44:12 0.0
13:44:27 0.0
13:44:42 0.0
13:44:57 1.505
13:45:12 3.882
13:45:27 1.4
13:45:42 1.44
13:45:57 1.0
13:46:12 0.04
```

The spike peaks at ~3.9 req/s of 5xx right around the kill (13:45:22) and decays to baseline (0.04) by 13:46:12,
consistent with the 19 s outage plus connection-pool reconnection. (The small bump at 13:44:57 is the Task 1
`DROP TABLE` window.)

**Why was the new Postgres pod empty? How would you eliminate this failure mode?**

The Deployment defined no `volumeMounts`/`PersistentVolumeClaim`, so the database files lived in the container's
writable layer — ephemeral storage that is discarded with the pod. When the pod was force-deleted, Kubernetes
scheduled a fresh container from the image, the `postgres` entrypoint saw no existing data directory and
initialized a **brand-new empty cluster** (`Did not find any relations`). The fix is exactly the Bonus Task:
attach a PVC to the data directory (with `PGDATA` pointed at a subdirectory to dodge `lost+found`), so the data
survives pod deletion and recovery degrades to a plain pod restart.

---

## Bonus Task — Persistent Storage + Automated Backup CronJob (2 pts)

### B.1: PVC added to Postgres

**Diff of `k8s/postgres.yaml`:**

```diff
+---
+apiVersion: v1
+kind: PersistentVolumeClaim
+metadata:
+  name: postgres-data
+  labels:
+    app: postgres
+spec:
+  accessModes: [ReadWriteOnce]
+  resources:
+    requests:
+      storage: 1Gi
+---
 apiVersion: apps/v1
 kind: Deployment
 metadata:
@@ -24,6 +37,9 @@ spec:
               value: "quickticket"
             - name: POSTGRES_PASSWORD
               value: "quickticket"
+            # subdir — avoid lost+found
+            - name: PGDATA
+              value: "/var/lib/postgresql/data/pgdata"
           ports:
             - containerPort: 5432
           resources:
@@ -39,6 +55,13 @@ spec:
             initialDelaySeconds: 5
             periodSeconds: 5
             failureThreshold: 5
+          volumeMounts:
+            - name: data
+              mountPath: /var/lib/postgresql/data
+      volumes:
+        - name: data
+          persistentVolumeClaim:
+            claimName: postgres-data
 ---
 apiVersion: v1
 kind: Service
```

Applied, waited for the rollout, and re-seeded the fresh PV once:

```bash
$ kubectl apply -f k8s/postgres.yaml
persistentvolumeclaim/postgres-data created
deployment.apps/postgres configured
service/postgres configured
$ kubectl rollout status deployment/postgres --timeout=90s
deployment "postgres" successfully rolled out
$ kubectl get pvc postgres-data
NAME            STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS   VOLUMEATTRIBUTESCLASS   AGE
postgres-data   Bound    pvc-cb1be384-b19f-4ab7-8c7c-fe636288e298   1Gi        RWO            local-path     <unset>                 22s
$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
    psql -U quickticket -d quickticket < app/seed.sql
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

### B.1: Disaster test re-run (with PVC)

Same script as 9.8, `mixedload` still running:

```bash
$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
    psql -U quickticket -d quickticket -c 'SELECT count(*) FROM orders'
 count
-------
    50
(1 row)
healthy at 13:49:53

$ kubectl delete pod -l app=postgres --grace-period=0 --force
pod "postgres-6d6dc59cb9-7v2f7" force deleted
# disaster at 13:49:53

$ kubectl wait --for=condition=Ready pod -l app=postgres --timeout=60s
pod/postgres-6d6dc59cb9-csdnb condition met
# new pod ready at 13:50:01

$ kubectl exec postgres-6d6dc59cb9-csdnb -- psql -U quickticket -d quickticket -c '\dt' -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
               List of relations
 Schema |      Name       | Type  |    Owner
--------+-----------------+-------+---------
 public | alembic_version | table | quickticket
 public | events          | table | quickticket
 public | orders          | table | quickticket
(3 rows)

 count
-------
     5
(1 row)

 count
-------
    50
(1 row)
# data verified at 13:50:02 — no pg_restore needed

$ kubectl rollout restart deployment/events
deployment.apps/events restarted
$ kubectl rollout status deployment/events --timeout=30s
deployment "events" successfully rolled out
# app fully up at 13:50:10
```

| Phase | Without PVC (Task 2) | With PVC (Bonus) |
|---|---|---|
| New pod ready | 13:45:30 (8 s) | 13:50:01 (8 s) |
| Data recovery | `pg_restore` required (13:45:31) | **data already there** (13:50:02) |
| App fully up | 13:45:41 | 13:50:10 |
| **RTO** | **19 s** | **17 s** |
| **RPO** | up to backup age (46 s here) | **0 — nothing lost** |

The new pod found all its data on the PV (`events`=5, `orders`=50 — exactly what was there before the kill). The
`pg_restore` step is gone; recovery is now just pod restart + application reconnect, and no data is lost at all.

### B.2: Automated backup CronJob

Storage plumbing applied:

```bash
$ kubectl apply -f labs/lab9/backup-storage.yaml
persistentvolumeclaim/postgres-backups created
deployment.apps/backup-inspector created
$ kubectl rollout status deployment/backup-inspector --timeout=30s
deployment "backup-inspector" successfully rolled out
$ kubectl get pvc postgres-backups
NAME               STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS   VOLUMEATTRIBUTESCLASS   AGE
postgres-backups   Bound    pvc-d875af38-f6c7-473d-8ac9-4b63a0387136   1Gi        RWO            local-path     <unset>                 8s
```

**My `k8s/backup-cronjob.yaml`:**

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: postgres-backup
  labels:
    app: postgres-backup
spec:
  schedule: "*/5 * * * *"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 2
      template:
        metadata:
          labels:
            app: postgres-backup
        spec:
          containers:
            - name: pg-dump
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
                  TS=$(date -u +%Y%m%dT%H%M%SZ)
                  pg_dump -Fc > "/backups/quickticket_${TS}.dump"
                  echo "created /backups/quickticket_${TS}.dump"
                  ls -1t /backups/quickticket_*.dump | tail -n +6 | while read -r f; do
                    rm "$f" && echo "removed '$f'"
                  done
              volumeMounts:
                - name: backups
                  mountPath: /backups
          restartPolicy: OnFailure
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

Triggered runs manually (7 total) instead of waiting for the 5-minute schedule:

```bash
$ kubectl apply -f k8s/backup-cronjob.yaml
cronjob.batch/postgres-backup created
$ kubectl create job --from=cronjob/postgres-backup manual-1
job.batch/manual-1 created
$ kubectl wait --for=condition=Complete job/manual-1 --timeout=60s
job.batch/manual-1 condition met
$ kubectl logs job/manual-1
created /backups/quickticket_20261005T105050Z.dump

$ for i in 2 3 4 5 6 7; do
    kubectl create job --from=cronjob/postgres-backup manual-$i
    kubectl wait --for=condition=Complete job/manual-$i --timeout=60s
  done
job.batch/manual-2 created / job.batch/manual-2 condition met
job.batch/manual-3 created / job.batch/manual-3 condition met
job.batch/manual-4 created / job.batch/manual-4 condition met
job.batch/manual-5 created / job.batch/manual-5 condition met
job.batch/manual-6 created / job.batch/manual-6 condition met
job.batch/manual-7 created / job.batch/manual-7 condition met
```

**`manual-7` log — rotation kicked in:**

```text
created /backups/quickticket_20261005T105127Z.dump
removed '/backups/quickticket_20261005T105100Z.dump'
```

**`/backups` after 7 runs — exactly 5 files remain:**

```bash
$ kubectl exec deployment/backup-inspector -- ls -la /backups
total 88
drwxrwxrwx    2 root     root         4096 Oct  5 10:51 .
drwxr-xr-x    1 root     root         4096 Oct  5 10:50 ..
-rw-r--r--    1 root     root        12324 Oct  5 10:51 quickticket_20261005T105105Z.dump
-rw-r--r--    1 root     root        12324 Oct  5 10:51 quickticket_20261005T105111Z.dump
-rw-r--r--    1 root     root        12324 Oct  5 10:51 quickticket_20261005T105116Z.dump
-rw-r--r--    1 root     root        12324 Oct  5 10:51 quickticket_20261005T105121Z.dump
-rw-r--r--    1 root     root        12324 Oct  5 10:51 quickticket_20261005T105127Z.dump
```

7 runs → 5 dumps kept, oldest removed on every run beyond the 5th. With the `*/5 * * * *` schedule the RPO is now
capped at 5 minutes, enforced by the cluster instead of by memory.

---

## Cleanup

```bash
$ kubectl delete -f labs/lab8/mixedload.yaml
deployment.apps "mixedload" deleted
$ kubectl delete -f k8s/backup-cronjob.yaml
cronjob.batch "postgres-backup" deleted
$ kubectl delete -f labs/lab9/backup-storage.yaml
persistentvolumeclaim "postgres-backups" deleted
deployment.apps "backup-inspector" deleted
$ pkill -f "port-forward.*5432" || true
```
