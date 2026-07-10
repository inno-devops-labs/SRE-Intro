## Task 1 — Migrations & Backup/Restore

1. `alembic history` output showing the two revisions (baseline + email).
```commandline
$ alembic history
c776243946eb -> ccbd88748e8a (head), add email column to events
<base> -> c776243946eb, baseline - pre-existing schema
(.venv) 
```
2. `\d events` output showing the new `email` column.
```commandline
timab@LAPTOP-CVMUCS3O MINGW64 ~/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro (main)
$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- psql -U quickticket -d quickticket -c '\d events'
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

(.venv) 
```
3. `time alembic upgrade head` output (elapsed time — expect <1s for nullable add).
```commandline
timab@LAPTOP-CVMUCS3O MINGW64 ~/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro (main)
$ time alembic upgrade head
Handling connection for 5432
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade c776243946eb -> ccbd88748e8a, add email column to events

real    0m1.013s
user    0m0.015s
sys     0m0.000s
(.venv) 
```
4. Prometheus `5xx last 1min` before and after migration (should both be 0 or unchanged).

Before:
```commandline
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))' | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('5xx last 1min:', r[0]['value'][1] if r else 0)"
5xx last 1min: 1.0909685982871793
(.venv) 
```
After:
```commandline
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))' | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('5xx last 1min:', r[0]['value'][1] if r else 0)"
5xx last 1min: 1.0909685982871793
(.venv)
```

The value didn't change after migration.
5. `ls -lh /tmp/quickticket.dump` + `pg_restore --list` output showing backup is valid.
```commandline
timab@LAPTOP-CVMUCS3O MINGW64 ~/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro (main)
$ ls -lh /tmp/quickticket.dump
-rw-r--r-- 1 timab 197609 7.2K Jul 10 02:55 /tmp/quickticket.dump
(.venv) 

timab@LAPTOP-CVMUCS3O MINGW64 ~/OneDrive/Документы/B24-SD-01/Sum26/SRE/SRE-Intro (main)
$ MSYS_NO_PATHCONV=1 kubectl exec $POD -- pg_restore --list /tmp/backup.dump | head -25
;
; Archive created at 2026-07-09 23:55:48 UTC
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
(.venv) 
```
*Note:* the command `kubectl exec $POD -- pg_restore --list /tmp/backup.dump | head -25` was modified to `$ MSYS_NO_PATHCONV=1 kubectl exec $POD -- pg_restore --list /tmp/backup.dump | head -25`
due to **Windows** system specificity (the command didn't work in its previous format).
6. Row counts **before disaster / after DROP / after restore** for events and orders.

| Phase | events | orders |
|-------|-------:|-------:|
| Before disaster          | 10 | 50 |
| After `DROP TABLE orders CASCADE` | 10 | — (table dropped, `relation "orders" does not exist`) |
| After `pg_restore --clean --if-exists` | 10 | 50 |

```commandline
$ kubectl exec $POD -- psql -U quickticket -d quickticket -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
 count
-------
    10
(1 row)
 count
-------
    50
(1 row)

$ kubectl exec $POD -- psql -U quickticket -d quickticket -c 'DROP TABLE orders CASCADE'
DROP TABLE

$ kubectl run smoke ... -- curl -s -o /dev/null -w "/events=%{http_code}\n" http://gateway:8080/events
/events=502          # API broken — events service can't read orders

$ MSYS_NO_PATHCONV=1 kubectl exec $POD -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump

$ kubectl exec $POD -- psql -U quickticket -d quickticket -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
 count
-------
    10
(1 row)
 count
-------
    50
(1 row)

$ kubectl run smoke ... -- curl -s -o /dev/null -w "/events=%{http_code}\n" http://gateway:8080/events
/events=200          # API healthy again after restore
```

The `orders` table (50 rows) was fully recovered and the API returned to `200`.

7. **What's the RPO of your current setup (single `pg_dump`)? How would you improve it?**

The RPO equals the **age of the most recent dump** — every write between the last `pg_dump` and the disaster is unrecoverable. With a single manual dump this can be hours of lost `orders`. To improve it:
- **Schedule frequent automated dumps** (a Kubernetes CronJob every 5 min → RPO ≤ 5 min)
- For a much lower RPO: **continuous WAL archiving + Point-In-Time-Recovery (PITR)** or **streaming replication to a hot standby**, which bring RPO down to seconds.

---

## Task 2 — Disaster Recovery Under Load

Full disaster → recovery cycle, force-killing Postgres while `mixedload` keeps running.

**Timestamps for the four phases:**

```commandline
$ kubectl delete pod -l app=postgres --grace-period=0 --force
pod "postgres-76cd478b6b-vghsk" force deleted
$ kubectl exec $NEW_POD -- psql -U quickticket -d quickticket -c '\dt'
Did not find any relations.        # new pod is EMPTY — no PVC

Disaster at      03:59:56
New pod ready    04:00:09
Restored         04:00:12
App fully up     04:00:26
```

**Actual RTO** = `App fully up − Disaster` = `04:00:26 − 03:59:56` = **30 seconds**.

**Orders count before disaster vs after restore (RPO gap):**

```commandline
=== baseline orders (before disaster): ===
 count
-------
    50
=== orders after restore: ===
 count
-------
    50
```

Row gap this run = **0 rows** (the workload was not committing net-new `orders` during the backup window). The RPO is still bounded by the **age of the backup** (dump taken at 02:55, disaster at 04:00 → ~65 min of exposure): any writes in that window would have been lost.

**Prometheus error-rate around the incident:**

```commandline
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
    'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B30s%5D))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783645228.544,"1.9349854715307564"]}]}}
# ~1.93 5xx/s while Postgres was down and the events pool held broken connections
```

**"The new Postgres pod was empty. Why? How would you eliminate this failure mode?"**

The Postgres Deployment has **no PersistentVolumeClaim** — the data directory lives on the pod's ephemeral filesystem, so force-deleting the pod destroyed the entire database. Eliminate it by mounting a **PVC** on `/var/lib/postgresql/data` so data survives pod restarts (done in the Bonus Task) — the new pod then reattaches the same volume and comes up with all data intact, no `pg_restore` needed.

---

## Bonus Task — Persistent Storage + Automated Backup CronJob

### B.1 — PVC on Postgres

**Diff of `k8s/postgres.yaml` (PVC added):**

```diff
+apiVersion: v1
+kind: PersistentVolumeClaim
+metadata:
+  name: postgres-data
+spec:
+  accessModes: [ReadWriteOnce]
+  resources:
+    requests:
+      storage: 1Gi
+---
 apiVersion: apps/v1
 kind: Deployment
 metadata:
   name: postgres
 spec:
   replicas: 1
+  strategy:
+    type: Recreate  # RWO volume can't be mounted by two pods at once
@@ env
+            - name: PGDATA
+              value: /var/lib/postgresql/data/pgdata  # subdir — avoid lost+found
@@ container
+          volumeMounts:
+            - name: data
+              mountPath: /var/lib/postgresql/data
+      volumes:
+        - name: data
+          persistentVolumeClaim:
+            claimName: postgres-data
```

**Re-run of 9.8 with the PVC — data survives the pod kill, no `pg_restore` needed:**

```commandline
===== counts BEFORE kill (PVC test): =====
 events: 5    orders: 25

$ kubectl delete pod -l app=postgres --grace-period=0 --force
pod "postgres-7459775f5-fsznv" force deleted

===== new pod postgres-7459775f5-dwq4k — data INTACT (no restore): =====
 Schema |  Name  | Type  |    Owner
--------+--------+-------+-------------
 public | events | table | quickticket
 public | orders | table | quickticket
 events: 5    orders: 25          # identical — nothing lost

Disaster at    04:03:39
New pod ready  04:03:40   (data already present — no pg_restore)
App fully up   04:03:51
```

**RTO with PVC = `04:03:51 − 04:03:39` = 12 s** (the DB pod itself was Ready with data in ~1 s; the remaining time is the `events` connection-pool restart). Compared to Task 2:

| | RTO | Data loss (RPO) | Restore step |
|---|---:|---|---|
| Task 2 (no PVC) | 30 s | back to backup age | `pg_restore` required |
| Bonus (PVC)     | 12 s | **0 rows** | none — volume reattached |

### B.2 — Automated backup CronJob

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
      backoffLimit: 1
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
                  TS=$(date -u +%Y%m%dT%H%M%SZ)
                  OUT="/backups/quickticket_${TS}.dump"
                  pg_dump -Fc -f "$OUT"
                  echo "created $OUT"
                  # Retention: keep only the 5 newest dumps, delete the rest.
                  ls -1t /backups/quickticket_*.dump | tail -n +6 | while read -r f; do
                    rm -f "$f" && echo "removed '$f'"
                  done
              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

**`manual-7` logs — rotation kicked in:**

```commandline
$ kubectl logs job/manual-7
created /backups/quickticket_20260710T010613Z.dump
removed '/backups/quickticket_20260710T010553Z.dump'
```

**`/backups` after 7 runs — exactly 5 files remain:**

```commandline
$ kubectl exec deployment/backup-inspector -- ls -la /backups
total 48
drwxrwxrwx 2 root root 4096 Jul 10 01:06 .
drwxr-xr-x 1 root root 4096 Jul 10 01:05 ..
-rw-r--r-- 1 root root 5441 Jul 10 01:05 quickticket_20260710T010557Z.dump
-rw-r--r-- 1 root root 5441 Jul 10 01:06 quickticket_20260710T010601Z.dump
-rw-r--r-- 1 root root 5441 Jul 10 01:06 quickticket_20260710T010606Z.dump
-rw-r--r-- 1 root root 5441 Jul 10 01:06 quickticket_20260710T010610Z.dump
-rw-r--r-- 1 root root 5441 Jul 10 01:06 quickticket_20260710T010613Z.dump
```

7 runs → the 2 oldest dumps were deleted, leaving the 5 newest. Retention works.

