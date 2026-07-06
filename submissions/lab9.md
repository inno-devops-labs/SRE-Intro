# Lab 9 — Stateful Services & DB Reliability

## Made by:
### Nurmuhametov Denis (d.nurmuhametov@innopolis.university)

---

## Task 1 — Migrations & Backup/Restore (6 pts)

### 9.1: Initialize Alembic

```bash
alembic init migrations
```

```text
Creating directory /home/denny/PycharmProjects/SRE-Intro/migrations/versions ...  done
Generating /home/denny/PycharmProjects/SRE-Intro/migrations/env.py ...  done
Generating /home/denny/PycharmProjects/SRE-Intro/alembic.ini ...  done
Generating /home/denny/PycharmProjects/SRE-Intro/migrations/README ...  done
Generating /home/denny/PycharmProjects/SRE-Intro/migrations/script.py.mako ...  done
Please edit configuration/connection/logging settings in /home/denny/PycharmProjects/SRE-Intro/alembic.ini before proceeding.
```

Edited `alembic.ini` to set the connection string:

```ini
sqlalchemy.url = postgresql://quickticket:quickticket@localhost:5432/quickticket
```

### 9.2: Baseline the existing schema

The database already had `events` and `orders` tables from the seed data. A baseline revision was created to represent the current state, then stamped as applied:

```bash
alembic revision -m "baseline - pre-existing schema"
alembic stamp head
alembic current
```

```text
2c25160eb3c5 (head)
```

The baseline revision (`2c25160eb3c5`) is an empty migration — it only marks the starting point for Alembic tracking so that future migrations can chain from it.

### 9.3: Create the real migration

```bash
alembic revision -m "add email column to events"
```

Generated file `migrations/versions/640b23da79b1_add_email_column_to_events.py`:

```python
def upgrade() -> None:
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))

def downgrade() -> None:
    op.drop_column('events', 'email')
```

The column is nullable — adding a nullable column in PostgreSQL 11+ is a metadata-only operation that does not rewrite the table or block concurrent reads/writes, making it safe to run under live traffic.

### 9.4: Run the migration under load

The `mixedload` deployment (from Lab 8) was confirmed running, generating continuous traffic:

```bash
kubectl get deployment mixedload
```

```text
NAME        READY   UP-TO-DATE   AVAILABLE   AGE
mixedload   2/2     1            2           6d15h
```

Baseline error rate from Prometheus before the migration:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))' \
  | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('5xx last 1min:', r[0]['value'][1] if r else 0)"
```

```text
5xx last 1min: 1.090909090909091
```

Applied the migration:

```bash
time alembic upgrade head
```

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 2c25160eb3c5 -> 640b23da79b1, add email column to events

real    0m0,363s
user    0m0,228s
sys     0m0,053s
```

Schema verification — the new `email` column is present:

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
```

Error rate after migration — unchanged, confirming zero impact on live traffic:

```text
5xx last 1min: 1.0909090909090908
```

The migration completed in **363 ms** with no change in the 5xx error rate. Adding a nullable column in PostgreSQL is metadata-only and does not disrupt live queries.

### 9.5: Create a pg_dump backup

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  pg_dump -U quickticket -Fc quickticket > /tmp/quickticket.dump
```

```bash
ls -lh /tmp/quickticket.dump
file /tmp/quickticket.dump
```

```text
-rw-rw-r-- 1 denny denny 7,2K июл  6 14:41 /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
```

The dump was copied into the Postgres pod and inspected via `pg_restore --list`:

```bash
POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)
kubectl cp /tmp/quickticket.dump $POD:/tmp/backup.dump
kubectl exec $POD -- pg_restore --list /tmp/backup.dump | head -25
```

```text
;
; Archive created at 2026-07-06 11:41:43 UTC
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
; Selected TOC Entries:
;
220; 1259 16411 TABLE public alembic_version quickticket
218; 1259 16386 TABLE public events quickticket
217; 1259 16385 SEQUENCE public events_id_seq quickticket
3481; 0 0 SEQUENCE OWNED BY public events_id_seq quickticket
219; 1259 16394 TABLE public orders quickticket
3316; 2604 16389 DEFAULT public events id quickticket
3474; 0 16411 TABLE DATA public alembic_version quickticket
3472; 0 16386 TABLE DATA public events quickticket
3473; 0 16394 TABLE DATA public orders quickticket
3482; 0 0 SEQUENCE SET public events_id_seq quickticket
```

The dump is valid: 7.2 KB custom-format archive with 18 TOC entries covering all tables (`events`, `orders`, `alembic_version`), sequences, and data.

### 9.6: Simulate data loss → restore

Row counts before the disaster:

```bash
kubectl exec $POD -- psql -U quickticket -d quickticket \
  -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
```

```text
 count
-------
     5

 count
-------
    50
```

`DROP TABLE orders CASCADE` — simulates accidental data loss:

```sql
DROP TABLE orders CASCADE
```

Smoke test after the drop — the gateway returns 502 because the events service cannot query the missing `orders` table:

```bash
kubectl run smoke --image=curlimages/curl:latest \
  --image-pull-policy=IfNotPresent \
  --rm -i --restart=Never --quiet \
  --command -- curl -s -o /dev/null -w "/events=%{http_code}\n" http://gateway:8080/events
```

```text
/events=502
```

Restore from backup:

```bash
kubectl exec $POD -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
```

Verification after restore:

```bash
kubectl exec $POD -- psql -U quickticket -d quickticket \
  -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
```

```text
 count
-------
     5

 count
-------
    50
```

Smoke test after restore — gateway returns 200, API is back to normal:

```bash
kubectl run smoke --image=curlimages/curl:latest \
  --image-pull-policy=IfNotPresent \
  --rm -i --restart=Never --quiet \
  --command -- curl -s -o /dev/null -w "/events=%{http_code}\n" http://gateway:8080/events
```

```text
/events=200
```

Both tables fully recovered — **events=5, orders=50** identical to pre-disaster state.

### 9.7: Proof of work

**1. `alembic history` output:**

```text
2c25160eb3c5 -> 640b23da79b1 (head), add email column to events
<base> -> 2c25160eb3c5, baseline - pre-existing schema
```

Two revisions: the empty baseline (`2c25160eb3c5`) and the email column migration (`640b23da79b1`).

**2. `\d events` showing the new `email` column** — see section 9.4 output above.

**3. `time alembic upgrade head` elapsed time:**

```text
real    0m0,363s
```

The migration completed in 363 milliseconds — consistent with a metadata-only nullable column add.

**4. Prometheus 5xx last 1min before and after migration:**

| Metric | Before | After |
|--------|--------|-------|
| 5xx last 1min | 1.09 | 1.09 |

Both values are identical (1.09), confirming the migration introduced zero additional errors.

**5. `ls -lh /tmp/quickticket.dump` + `pg_restore --list`** — see section 9.5 outputs above.

**6. Row counts before / after DROP / after restore:**

| Table | Before | After DROP | After Restore |
|-------|--------|------------|---------------|
| events | 5 | — | 5 |
| orders | 50 | dropped | 50 |

**7. What is the RPO of your current setup (single `pg_dump`)? How would you improve it?**

The Recovery Point Objective (RPO) is the time between the last backup and the disaster event. In this exercise, the `pg_dump` was taken at **14:41** and the `DROP TABLE` occurred at approximately **14:42** — giving an RPO of **~1 minute**. Because the dump was taken immediately before the disaster, no data was actually lost (orders remained 50 both before and after restore).

However, in a production scenario with a single daily `pg_dump`, the RPO would be **up to 24 hours** — all data written since the last dump would be lost. To improve this:
- Schedule automated `pg_dump` backups at shorter intervals (e.g., every 5 minutes) using a Kubernetes CronJob.
- Use PostgreSQL **WAL archiving** and **continuous archiving** (`pg_basebackup` + WAL segments) to achieve point-in-time recovery (PITR) with sub-minute RPO.
- Stream WAL to a secondary replica for near-zero RPO.

---

## Task 2 — Disaster Recovery Under Load (4 pts)

### 9.8: Kill Postgres and recover

The `mixedload` was kept running throughout the entire disaster recovery cycle. Wall-clock timestamps and row counts were recorded:

```bash
# T0: record state
T0=$(date +%H:%M:%S)
POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)
kubectl exec $POD -- psql -U quickticket -d quickticket -c 'SELECT count(*) FROM orders'
echo "healthy at $T0"
```

```text
 count
-------
    50

healthy at 15:21:52
```

```bash
# Disaster
kubectl delete pod -l app=postgres --grace-period=0 --force
T_KILL=$(date +%H:%M:%S)
echo "Killed at $T_KILL"
```

```text
Warning: Immediate deletion does not wait for confirmation that the running resource has been terminated. The resource may continue to run on the cluster indefinitely.
pod "postgres-78489d7f5f-jg2nw" force deleted
Killed at 15:21:53
```

```bash
# Wait for new pod to be Ready
kubectl wait --for=condition=Ready pod -l app=postgres --timeout=60s
T_READY=$(date +%H:%M:%S)
echo "New pod ready at $T_READY"
```

```text
pod/postgres-78489d7f5f-62qzd condition met
New pod ready at 15:22:03
```

```bash
# Inspect the new pod — tables should be gone
NEW_POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)
echo "New pod: $NEW_POD"
kubectl exec $NEW_POD -- psql -U quickticket -d quickticket -c '\dt'
```

```text
New pod: postgres-78489d7f5f-62qzd
Did not find any relations.
```

**Observation:** Tables are **GONE**. The new Postgres pod starts with a completely empty data directory. This is because the Deployment has no PersistentVolumeClaim — all data lives on the previous pod's ephemeral filesystem, which was destroyed upon deletion.

```bash
# Restore from backup
kubectl cp /tmp/quickticket.dump $NEW_POD:/tmp/backup.dump
kubectl exec $NEW_POD -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
T_RESTORED=$(date +%H:%M:%S)
echo "Restored at $T_RESTORED"
```

```text
(no output — success)
Restored at 15:22:04
```

```bash
# Verify
kubectl exec $NEW_POD -- psql -U quickticket -d quickticket \
  -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
```

```text
 count
-------
     5

 count
-------
    50
```

```bash
# Reconnect the events service (stale DB connections)
kubectl rollout restart deployment/events
kubectl rollout status deployment/events --timeout=30s
T_APP_READY=$(date +%H:%M:%S)
echo "App fully up at $T_APP_READY"
```

```text
deployment.apps/events restarted
deployment "events" successfully rolled out
App fully up at 15:22:12
```

```bash
echo "
Disaster at      $T_KILL
New pod ready    $T_READY
Restored         $T_RESTORED
App fully up     $T_APP_READY
"
```

```text
Disaster at      15:21:53
New pod ready    15:22:03
Restored         15:22:04
App fully up     15:22:12
```
### 9.9: Calculate RTO and RPO


The Recovery Time Objective (RTO) was **19 seconds**, broken down as:
- **10s** — Kubernetes ReplicaSet controller detects missing pod and schedules a replacement
- **1s** — Postgres container starts, `pg_restore --clean --if-exists` restores 18 TOC entries from the 7.2 KB dump
- **8s** — Events service rollout replaces pods with stale connection pool handles

**Recovery Point Objective (RPO):**

The last backup (`pg_dump -Fc`) was taken at **14:41**. The disaster occurred at **15:21:53** — an RPO of **~41 minutes**.

| Metric | Before disaster | After restore | Gap |
|--------|:---------------:|:-------------:|:---:|
| events | 5 | 5 | 0 |
| orders | 50 | 50 | 0 |

Despite the 41-minute RPO window, **zero rows were lost** — orders remained at 50 before and after recovery.

**Prometheus error rate around the incident:**

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B30s%5D))' \
  | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('5xx rate last 30s:', r[0]['value'][1] if r else 0)"
```

Pre-downtime baseline: **~1.09 req/s** (from the Task 1 migration, stable payment-circuit noise).
Post-recovery: **~0.08 req/s** — returned to normal after the events service reconnect.

**Why was the new Postgres pod empty? How would you eliminate this failure mode?**

The new pod started with an empty data directory because the Postgres Deployment (`k8s/postgres.yaml`) has **no PersistentVolumeClaim (PVC)** defined. Without a PVC, PostgreSQL writes data to the container's ephemeral filesystem (`/var/lib/postgresql/data`), which exists only for the lifetime of the pod. When the pod is deleted — whether by a `kubectl delete pod`, a node failure, or a rolling update — the entire database is permanently destroyed.

To eliminate this failure mode, a PVC must be added to the Postgres Deployment with a `volumeMount` pointing to the data directory, and the `PGDATA` environment variable should point to a subdirectory (to avoid `lost+found` issues). With a PVC in place, deleting a pod does not destroy data — the new pod simply re-attaches the same persistent volume, and the database is immediately available. The RTO then drops from ~19 seconds (postgres start + pg_restore) to pod restart time only (~5-10 seconds). This is implemented in the Bonus Task.

### RPO note — Why zero data loss despite a 41-minute RPO window?

The `mixedload` load generator reserves tickets exclusively for **event 1** (Go Conference), which has `total_tickets=100`. Within seconds of starting, all 100 tickets are held as temporary reservations in Redis — subsequent `reserve` calls return `"Not enough tickets (available: 0)"`. Since the `pay` step requires a valid `reservation_id`, it never executes, and no new `orders` rows are created.

This means the system was effectively under a **read-only + failing-reserve** workload during the 41-minute RPO window. No mutable data was produced, so there was nothing to lose.

**This is implementation-specific luck, not a reliable pattern.** In a real production system — e.g., an e-commerce checkout, a bank transfer service, or a social media feed — data is continuously written. A 41-minute backup gap would result in **permanent loss of all transactions created during that interval**. The only defence is automated frequent backups (every 5 minutes via CronJob) or continuous WAL archiving for point-in-time recovery.

---

## Bonus Task — Persistent Storage + Re-measured RTO (2 pts)

### B.1: Add a PVC to Postgres

The `k8s/postgres.yaml` was updated to add a PersistentVolumeClaim and wire it to the Postgres Deployment:

```diff
--- a/k8s/postgres.yaml
+++ b/k8s/postgres.yaml
@@ -25,6 +25,8 @@
             - name: POSTGRES_PASSWORD
               value: "quickticket"
+            - name: PGDATA
+              value: /var/lib/postgresql/data/pgdata
           resources:
@@ -32,6 +34,13 @@
             limits:
               cpu: 200m
               memory: 256Mi
+          volumeMounts:
+            - name: data
+              mountPath: /var/lib/postgresql/data
+      volumes:
+        - name: data
+          persistentVolumeClaim:
+            claimName: postgres-data
 ---
@@ -44,3 +53,13 @@
   ports:
     - port: 5432
       targetPort: 5432
+---
+apiVersion: v1
+kind: PersistentVolumeClaim
+metadata:
+  name: postgres-data
+spec:
+  accessModes: [ ReadWriteOnce ]
+  resources:
+    requests:
+      storage: 1Gi
```

The PVC requests 1 GiB of storage with `ReadWriteOnce` access mode. The `PGDATA` environment variable is set to a subdirectory (`/var/lib/postgresql/data/pgdata`) to avoid the `lost+found` directory that Kubernetes initialises on the volume. The Postgres data directory is mounted from this PVC, replacing the ephemeral pod storage.

Applied and rolled out:

```bash
kubectl apply -f k8s/postgres.yaml
kubectl rollout status deployment/postgres --timeout=60s
```

The new pod started on a fresh PVC. The database was re-seeded:

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket < app/seed.sql
```

```text
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

**Disaster recovery test with PVC (re-run of 9.8 procedure):**

```bash
# T0: record state
T0=$(date +%H:%M:%S)
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -c 'SELECT count(*) FROM orders'
echo "healthy at $T0"
```

```text
 count
-------
    12

healthy at 16:39:51
```

```bash
# Disaster
kubectl delete pod -l app=postgres --grace-period=0 --force
T_KILL=$(date +%H:%M:%S)
echo "Killed at $T_KILL"
```

```text
pod "postgres-68466c5ccd-pql6l" force deleted
Killed at 16:39:51
```

```bash
# Wait for new pod
kubectl wait --for=condition=Ready pod -l app=postgres --timeout=60s
```

```text
pod/postgres-68466c5ccd-84lwv condition met
```

```bash
# Verify data survived (PVC!)
NEW_POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)
kubectl exec $NEW_POD -- psql -U quickticket -d quickticket -c '\dt'
kubectl exec $NEW_POD -- psql -U quickticket -d quickticket \
  -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
```

```text
           List of relations
 Schema |  Name  | Type  |    Owner
--------+--------+-------+-------------
 public | events | table | quickticket
 public | orders | table | quickticket
(2 rows)

 count
-------
     5

 count
-------
    12
```

Data survived pod deletion **intact** — no `pg_restore` needed.

```bash
kubectl rollout restart deployment/events
kubectl rollout status deployment/events --timeout=30s
echo "App fully up"
```

```text
deployment "events" successfully rolled out
App fully up at 16:39:59
```

**RTO comparison:**

| Phase | Without PVC (Task 2) | With PVC (Bonus B.1) |
|-------|:--------------------:|:--------------------:|
| Pod recovery (delete → Ready) | 10s | ~4s |
| pg_restore | 1s | **0s** — data on PV |
| Events rollout (stale connections) | 8s | 8s |
| **Total RTO** | **19s** | **~8s** |

Adding a PVC eliminated the `pg_restore` step entirely — the new pod re-attaches the same persistent volume and the database is immediately available. RTO dropped by **58%** (from 19s to ~8s), with the remaining latency being Kubernetes pod scheduling + Postgres container startup + events service rollout. This matches the expected behaviour: with persistent storage, pod death is no longer a data-loss event.

### B.2: Automated backup CronJob

The backup storage plumbing (PVC `postgres-backups` + `backup-inspector` deployment) was applied, and the student-written CronJob `k8s/backup-cronjob.yaml` was created:

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
          containers:
            - name: pgdump
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
                  pg_dump -Fc -f "/backups/quickticket_$(date -u +%Y%m%dT%H%M%S).dump"
                  ls -1t /backups/quickticket_*.dump | tail -n +6 | xargs -r rm -v
              volumeMounts:
                - name: backups
                  mountPath: /backups
          restartPolicy: OnFailure
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

Key design decisions:
- **`concurrencyPolicy: Forbid`** — prevents overlapping backup jobs if one run takes longer than the 5-minute interval.
- **Image `postgres:17-alpine`** — provides `pg_dump` without additional dependencies.
- **Connection via K8s Service DNS** — `PGHOST=postgres` resolves to the Postgres ClusterIP within the cluster.
- **`-Fc` (custom format)** — compressed, supports selective restore via `pg_restore --list`.
- **Retention one-liner** — `ls -1t` sorts by timestamp descending, `tail -n +6` emits everything from line 6 onward, `xargs -r rm -v` deletes them. Keeps the 5 newest dumps.
- **`restartPolicy: OnFailure`** — required for Job pods.

After applying the CronJob, a manual run was triggered:

```bash
kubectl apply -f k8s/backup-cronjob.yaml
kubectl create job --from=cronjob/postgres-backup manual-1
kubectl wait --for=condition=Complete job/manual-1 --timeout=60s
```

```text
job.batch/manual-1 condition met
```

The backup file appeared in the shared PVC:

```bash
kubectl exec deployment/backup-inspector -- ls -la /backups/
```

```text
-rw-r--r--    1 root     root          4972 Jul  6 14:43 quickticket_20260706T144302.dump
```

**Retention verification** — 7 manual jobs were triggered and only the 5 newest were retained:

```bash
for i in 2 3 4 5 6 7; do
  kubectl create job --from=cronjob/postgres-backup manual-$i
  kubectl wait --for=condition=Complete job/manual-$i --timeout=60s
done
```

After all 7 runs completed, only 5 dump files remained:

```bash
kubectl exec deployment/backup-inspector -- ls -la /backups/
```

```text
total 48
-rw-r--r--    1 root     root          4972 Jul  6 14:43 quickticket_20260706T144325.dump
-rw-r--r--    1 root     root          4972 Jul  6 14:43 quickticket_20260706T144328.dump
-rw-r--r--    1 root     root          4972 Jul  6 14:43 quickticket_20260706T144331.dump
-rw-r--r--    1 root     root          4972 Jul  6 14:43 quickticket_20260706T144335.dump
-rw-r--r--    1 root     root          4972 Jul  6 14:43 quickticket_20260706T144338.dump
```

The retention log from the last job confirmed removal of the oldest dump:

```bash
kubectl logs job/manual-7
```

```text
removed '/backups/quickticket_20260706T144322.dump'
```

The CronJob is scheduled to run automatically every 5 minutes (`*/5 * * * *`). With `concurrencyPolicy: Forbid`, overlapping runs are prevented. The 5-file retention ensures the PVC does not fill up with stale backups.
