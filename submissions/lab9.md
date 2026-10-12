## Task 1 — Migrations & Backup/Restore

1. `alembic history` output showing the two revisions (baseline + email).
```bash
$ alembic history
c776243946eb -> ccbd88748e8a (head), add email column to events
<base> -> c776243946eb, baseline - pre-existing schema
```

2. `\d events` output showing the new `email` column.
```bash
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
```

3. `time alembic upgrade head` output (elapsed time — expect <1s for nullable add).
```bash
$ time alembic upgrade head
Handling connection for 5432
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade c776243946eb -> ccbd88748e8a, add email column to events

real    0m0.942s
user    0m0.015s
sys     0m0.000s
```

4. Prometheus `5xx last 1min` before and after migration (should both be 0 or unchanged).

Before:
```bash
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))' | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('5xx last 1min:', r[0]['value'][1] if r else 0)"
5xx last 1min: 0
```
After:
```bash
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))' | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('5xx last 1min:', r[0]['value'][1] if r else 0)"
5xx last 1min: 0
```
The error rate remained steady at 0; adding a nullable column executed as a metadata-only catalog update without locking ongoing traffic.

5. `ls -lh /tmp/quickticket.dump` + `pg_restore --list` output showing backup is valid.
```bash
$ ls -lh /tmp/quickticket.dump
-rw-r--r-- 1 runner 197609 7.2K Oct 11 14:25 /tmp/quickticket.dump

$ kubectl exec $POD -- pg_restore --list /tmp/backup.dump | head -25
;
; Archive created at 2026-10-11 14:25:48 UTC
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
```

6. Row counts **before disaster / after DROP / after restore** for events and orders.

| Phase | events | orders |
|-------|-------:|-------:|
| Before disaster | 10 | 50 |
| After `DROP TABLE orders CASCADE` | 10 | — (table dropped, `relation "orders" does not exist`) |
| After `pg_restore --clean --if-exists` | 10 | 50 |

```bash
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

$ kubectl run smoke --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- curl -s -o /dev/null -w "/events=%{http_code}\n" http://gateway:8080/events
/events=502

$ kubectl exec $POD -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump

$ kubectl exec $POD -- psql -U quickticket -d quickticket -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
 count 
-------
    10
(1 row)
 count 
-------
    50
(1 row)

$ kubectl run smoke --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- curl -s -o /dev/null -w "/events=%{http_code}\n" http://gateway:8080/events
/events=200
```
The `orders` relation was restored with all 50 entries, returning API responses from 502 Bad Gateway to 200 OK.

7. **What's the RPO of your current setup (single `pg_dump`)? How would you improve it?**

The current RPO is directly determined by the **elapsed duration since the last `pg_dump` execution**. Any writes committed after the dump snapshot are permanently lost upon a disaster. With manual dumps, this translates to hours of potential data loss.

To improve it:
- **Automated periodic backups:** Implementing a Kubernetes `CronJob` running every 5 minutes restricts the RPO bound to $\le 5$ minutes.
- **Continuous Write-Ahead Logging (WAL) archiving & PITR:** Shipping WAL segments to external object storage enables Point-In-Time Recovery to the second.
- **Streaming replication:** Maintaining a live standby replica provides near-zero RPO failover for hardware faults.

---

## Task 2 — Disaster Recovery Under Load

Full disaster → recovery cycle, force-killing Postgres while `mixedload` keeps running.

**Timestamps for the four phases:**

```bash
$ kubectl delete pod -l app=postgres --grace-period=0 --force
pod "postgres-76cd478b6b-k8tm4" force deleted
$ kubectl exec $NEW_POD -- psql -U quickticket -d quickticket -c '\dt'
Did not find any relations.

Disaster at      15:59:56
New pod ready    16:00:09
Restored         16:00:12
App fully up     16:00:26
```

**Actual RTO** = `App fully up − Disaster` = `16:00:26 − 15:59:56` = **30 seconds**.

**Orders count before disaster vs after restore (RPO gap):**

```bash
=== baseline orders (before disaster): ===
 count 
-------
    50
=== orders after restore: ===
 count 
-------
    50
```
Row gap this run = **0 rows** (traffic did not record new finalized orders during the short observation slice). However, true RPO exposure spans the **delta between backup snapshot creation (14:25) and disaster (15:59) — approximately 94 minutes** of potential data loss.

**Prometheus error-rate around the incident:**

```bash
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
    'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B30s%5D))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1791734426,"1.9349854715307564"]}]}}
```
(~1.93 5xx/s while PostgreSQL was unavailable and client connection pools held dead handles).

**"The new Postgres pod was empty. Why? How would you eliminate this failure mode?"**

The default PostgreSQL Deployment relies on **ephemeral pod container storage**. When the pod was force-deleted, its container filesystem was discarded. To eliminate this issue, PostgreSQL must mount a **PersistentVolumeClaim (PVC)** on `/var/lib/postgresql/data`, guaranteeing that underlying storage survives pod rescheduling and automatically reattaches upon restart without manual `pg_restore`.

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
+    type: Recreate
@@ env
+            - name: PGDATA
+              value: /var/lib/postgresql/data/pgdata
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

```bash
===== counts BEFORE kill (PVC test): =====
 events: 5    orders: 25

$ kubectl delete pod -l app=postgres --grace-period=0 --force
pod "postgres-7459775f5-b8rqp" force deleted

===== new pod postgres-7459775f5-m7xka — data INTACT (no restore): =====
 Schema |  Name  | Type  |    Owner    
--------+--------+-------+-------------
 public | events | table | quickticket
 public | orders | table | quickticket
 events: 5    orders: 25

Disaster at    16:03:39
New pod ready  16:03:40   (data already present — no pg_restore)
App fully up   16:03:51
```

**RTO with PVC = `16:03:51 − 16:03:39` = 12 seconds** (the database container became ready in ~1 s; the rest was the `events` pool reconnect rollout).

| Setup | RTO | Data Loss (RPO) | Restore Step |
|---|---:|---|---|
| Task 2 (no PVC) | 30 s | Snapshot age delta | `pg_restore` required |
| Bonus (PVC) | 12 s | **0 rows** | Volume auto-reattached |

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

```bash
$ kubectl logs job/manual-7
created /backups/quickticket_20261011T160613Z.dump
removed '/backups/quickticket_20261011T160553Z.dump'
```

**`/backups` after 7 runs — exactly 5 files remain:**

```bash
$ kubectl exec deployment/backup-inspector -- ls -la /backups
total 48
drwxrwxrwx 2 root root 4096 Oct 11 16:06 .
drwxr-xr-x 1 root root 4096 Oct 11 16:05 ..
-rw-r--r-- 1 root root 5441 Oct 11 16:05 quickticket_20261011T160557Z.dump
-rw-r--r-- 1 root root 5441 Oct 11 16:06 quickticket_20261011T160601Z.dump
-rw-r--r-- 1 root root 5441 Oct 11 16:06 quickticket_20261011T160606Z.dump
-rw-r--r-- 1 root root 5441 Oct 11 16:06 quickticket_20261011T160610Z.dump
-rw-r--r-- 1 root root 5441 Oct 11 16:06 quickticket_20261011T160613Z.dump
```
Seven manual triggers executed; the 2 oldest backups were pruned, retaining exactly the 5 newest files.
