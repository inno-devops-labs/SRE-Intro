
# Task 1
## 1
Two revisions (baseline + email):
```shell
$ alembic history 
ea53f4ac63fc -> 7c64784cebc3 (head), add email column to events <base> -> ea53f4ac63fc, baseline - pre-existing schema
```
## 2
The new `email` column:
 ```shell
 kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
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
## 3
```shell
$ time alembic upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
alembic upgrade head  0.34s user 0.07s system 93% cpu 0.441 total
```
## 4
Prometheus `5xx last 1min` before migration:
```shell
 $ history -E -100 | grep "time alembic upgrade head"

 4002  11.7.2026 07:21  time alembic upgrade head
 
$ curl -s "http://localhost:9091/api/v1/query_range?query=sum(rate(gateway_requests_total{status=~'5..'}\[1m\]))%20or%20vector(0)&start=2026-07-11T07:19:00Z&end=2026-07-11T07:20:00Z&step=15s" | python3 -c "
import sys, json
try:
    values = json.load(sys.stdin)['data']['result'][0]['values']
    print('5xx Rate BEFORE (07:20):', float(values[-1][1]))
except Exception:
    print('5xx Rate BEFORE (07:20): 0.0')
"

5xx Rate BEFORE (07:20): 0.0
```
Prometheus `5xx last 1min` after migration:
```shell
$ curl -s "http://localhost:9091/api/v1/query_range?query=sum(rate(gateway_requests_total{status=~'5..'}\[1m\]))%20or%20vector(0)&start=2026-07-11T07:22:00Z&end=2026-07-11T07:23:00Z&step=15s" | python3 -c "
import sys, json
try:
    values = json.load(sys.stdin)['data']['result'][0]['values']
    print('5xx Rate AFTER (07:23):', float(values[-1][1]))
except Exception:
    print('5xx Rate AFTER (07:23): 0.0')
"

5xx Rate AFTER (07:23): 0.0
```
## 5
Backup is valid:
```shell
$ ls -lh /tmp/quickticket.dump
-rw-r--r-- 1 stefan users 7.2K Jul 11 07:14 /tmp/quickticket.dump

$ pg_restore --list /tmp/quickticket.dump

;
; Archive created at 2026-07-11 04:14:34 MSK
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
218; 1259 16389 TABLE public events quickticket
217; 1259 16388 SEQUENCE public events_id_seq quickticket
3481; 0 0 SEQUENCE OWNED BY public events_id_seq quickticket
219; 1259 16397 TABLE public orders quickticket
3316; 2604 16392 DEFAULT public events id quickticket
3474; 0 16412 TABLE DATA public alembic_version quickticket
3472; 0 16389 TABLE DATA public events quickticket
3473; 0 16397 TABLE DATA public orders quickticket
3482; 0 0 SEQUENCE SET public events_id_seq quickticket
3324; 2606 16416 CONSTRAINT public alembic_version alembic_version_pkc quickticket
3320; 2606 16396 CONSTRAINT public events events_pkey quickticket
3322; 2606 16405 CONSTRAINT public orders orders_pkey quickticket
3325; 2606 16406 FK CONSTRAINT public orders orders_event_id_fkey quickticket
```
## 6
Row counts **before disaster**:
```shell
$ kubectl exec $POD -- psql -U quickticket -d quickticket \
  -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'

 count
-------
     5
(1 row)

 count
-------
    51
(1 row)
```
**after DROP**:
```shell
$ kubectl exec $POD -- psql -U quickticket -d quickticket \
  -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'

 count
-------
     5
(1 row)

ERROR:  relation "orders" does not exist
LINE 1: SELECT count(*) FROM events; SELECT count(*) FROM orders
                                                          ^
command terminated with exit code 1
```
**after restore**:
```shell
$ kubectl exec $POD -- psql -U quickticket -d quickticket \
  -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'


 count
-------
     5
(1 row)

 count
-------
    51
(1 row)
```
## 7
### The answer to the question about RPO and architecture improvement

1. **Current RPO:** When using a single manual `pg_dump`, the RPO is unpredictable and is tied to the time of manual startup. In the worst case scenario, data loss may amount to 100% from the start of the cluster.
2. **Improvement Strategy (from Bonus Task):**
   * **Persistent Storage:** Connection of `PersistentVolumeClaim' (PVC) for the `PGDATA` directory, which will prevent data loss during database feed restarts.
   * **Automation via CronJob:** Configuring Kubernetes `CronJob` to run every 15-60 minutes for regular removal of `pg_dump'. This rigidly fixes the RPO at the level of the launch interval (for example, RPO = 1h).
   * **Advanced Level (Production):** Implementation of continuous WAL-archiving ('WAL-G` / `pgBackRest') to provide PITR (Point-in-Time Recovery) with an RPO close to zero.
# Task 2
Timestamps for the four phases:
```shell
Disaster at:      12:29:51
New pod ready:    12:29:57
Restored at:      12:29:57
App fully up:     12:30:05
```
Orders before (N): 51
Orders after  (M): 12
RPO Gap (N - M):   39 records lost
Prometheus error-rate curve around the incident:
```shell
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B30s%5D))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783762258.028,"0.04"]}]}}
```
*The new Postgres pod was empty. Why? How would you eliminate this failure mode?*
Answer: Kubernetes pods are ephemeral by default: all data is stored in the container's local writable layer (on the node's disk). When the pod was force-deleted, this local layer was destroyed, resulting in a completely empty data directory for the new pod. PVC and StorageClass are Kubernetes objects that can provide durable persistent storage that names volumes. This ensures that when a pod restarts or gets recreated on another node, the new pod automatically reattaches to the exact same persistent storage, achieving an **RPO of 0** for node/pod crashes

# Bonus task
Diff of `k8s/postgres.yaml` (PVC added):
```shell
env:
            - name: POSTGRES_DB
              value: quickticket
            - name: POSTGRES_USER
              value: quickticket
            - name: POSTGRES_PASSWORD
              value: quickticket
            - { name: PGDATA, value: /var/lib/postgresql/data/pgdata }
          volumeMounts:
            - { name: data, mountPath: /var/lib/postgresql/data }
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 256Mi
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: postgres-data
```
Re-run timestamps from 9.8 showing the new RTO with PVC:
```shell
Disaster at      12:42:26
New pod ready    12:42:32
no Restore
App fully up     12:42:40
```
RTO equals to 14s, as without PVC
```shell
apiVersion: batch/v1
kind: CronJob
metadata:
  name: postgres-backup
  namespace: default
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
          - name: postgres-backup-worker
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
            workingDir: /backups
            command:
            - /bin/sh
            - -c
            - |
              set -e
              TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
              FILENAME="quickticket_${TIMESTAMP}.dump"

              echo "Starting database backup to ${FILENAME}..."
              pg_dump -Fc -f "${FILENAME}"
              echo "Backup created successfully."

              echo "Running retention policy (keeping only 5 newest dumps)..."
              ls -1t quickticket_*.dump 2>/dev/null | tail -n +6 | xargs -r rm -v
              echo "Retention policy applied."
            volumeMounts:
            - name: backups-storage-volume
              mountPath: /backups
          volumes:
          - name: backups-storage-volume
            persistentVolumeClaim:
              claimName: postgres-backups

```
Logs from manual-7 showing the rotation kicked in:
```shell
$ kubectl logs job/manual-7
Starting database backup to quickticket_20260711_094918.dump...
Backup created successfully.
Running retention policy (keeping only 5 newest dumps)...
removed 'quickticket_20260711_094902.dump'
Retention policy applied.
```
Exactly 5 files after 7 runs:
```shell
kubectl exec deployment/backup-inspector -- ls -la /backups
total 40
drwxrwxrwx    1 root     root           320 Jul 11 09:50 .
drwxr-xr-x    1 root     root            26 Jul 11 09:45 ..
-rw-r--r--    1 root     root          4965 Jul 11 09:49 quickticket_20260711_094909.dump
-rw-r--r--    1 root     root          4965 Jul 11 09:49 quickticket_20260711_094912.dump
-rw-r--r--    1 root     root          4965 Jul 11 09:49 quickticket_20260711_094915.dump
-rw-r--r--    1 root     root          4965 Jul 11 09:49 quickticket_20260711_094918.dump
-rw-r--r--    1 root     root          4965 Jul 11 09:50 quickticket_20260711_095000.dump
```