# Lab 9 — Stateful Services & DB Reliability
## Task 1 — Migrations & Backup/Restore

- alembic history output showing the two revisions (baseline + email).
```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab9)
$ alembic history
ec1d20aaee22 -> 638e632482d2 (head), add email column to events
<base> -> ec1d20aaee22, baseline - pre-existing schema
(.venv)
```

- \d events output showing the new email column.
```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab9)
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

(.venv)
```

- time alembic upgrade head output (elapsed time — expect <1s for nullable add).
```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab9)
$ time alembic upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade ec1d20aaee22 -> 638e632482d2, add email column to events

real    0m1.746s
user    0m0.000s
sys     0m0.031s
(.venv)
```

- Prometheus 5xx last 1min before and after migration (should both be 0 or unchanged).
```
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))' \
  | python -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('5xx last 1min:', r[0]['value'][1] if r else 0)"
5xx last 1min: 0
(.venv)
```

- ls -lh /tmp/quickticket.dump + pg_restore --list output showing backup is valid.
```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab9)
$ ls -lh /tmp/quickticket.dump
file /tmp/quickticket.dump
-rw-r--r-- 1 axmed 197609 7.2K Jul  8 21:47 /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
(.venv)

axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab9)
$ kubectl exec $POD -- sh -c "pg_restore --list /tmp/backup.dump | head -25"
;
; Archive created at 2026-07-08 18:48:22 UTC
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
218; 1259 16386 TABLE public events quickticket
217; 1259 16385 SEQUENCE public events_id_seq quickticket
3481; 0 0 SEQUENCE OWNED BY public events_id_seq quickticket
219; 1259 16394 TABLE public orders quickticket
3316; 2604 16389 DEFAULT public events id quickticket
3474; 0 16412 TABLE DATA public alembic_version quickticket
3472; 0 16386 TABLE DATA public events quickticket
3473; 0 16394 TABLE DATA public orders quickticket
3482; 0 0 SEQUENCE SET public events_id_seq quickticket
(.venv)

```

- Row counts before disaster / after DROP / after restore for events and orders.

```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab9)
$ kubectl exec $POD -- psql -U quickticket -d quickticket -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
 count
-------
     5
(1 row)

 count
-------
    50
(1 row)

(.venv)


axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab9)
$ kubectl exec $POD -- psql -U quickticket -d quickticket -c 'DROP TABLE orders CASCADE'
DROP TABLE
(.venv)


axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab9)
$ kubectl exec $POD -- psql -U quickticket -d quickticket -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
 count
-------
     5
(1 row)

 count
-------
    50
(1 row)

(.venv)
```

- Answer: "What's the RPO of your current setup (single pg_dump)? How would you improve it? (Hint: Bonus Task.)"

RPO: The Recovery Point Objective is the time between backups. With a single manual pg_dump, the RPO is the interval between when the backup was taken and when the disaster occurred. If the backup was taken 1 hour before the disaster, the RPO is 1 hour.

**How to improve it:**

- Automate backups using a CronJob (Bonus Task) to reduce RPO to minutes

- Use continuous WAL archiving (PostgreSQL streaming replication) for near-zero RPO

- Schedule backups more frequently (e.g., every 5 minutes)

- Store backups in a persistent volume or external storage


## Task 2 — Disaster Recovery Under Load

- Timestamps for the four phases (disaster / new pod ready / restored / app ready).

```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab9)
$ T0=$(date +%H:%M:%S)
echo "healthy at $T0"
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -c 'SELECT count(*) FROM orders'
healthy at 22:23:18
 count
-------
    50
(1 row)

(.venv)


axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab9)
$ kubectl delete pod -l app=postgres --grace-period=0 --force
T_KILL=$(date +%H:%M:%S)
echo "disaster at $T_KILL"
Warning: Immediate deletion does not wait for confirmation that the running resource has been terminated. The resource may continue to run on the cluster indefinitely.
pod "postgres-7c7ffc4b-hzfdf" force deleted from default namespace
disaster at 22:24:51
(.venv)

axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab9)
$ kubectl wait --for=condition=Ready pod -l app=postgres --timeout=60s
T_READY=$(date +%H:%M:%S)
echo "new pod ready at $T_READY"
pod/postgres-7c7ffc4b-8m4vm condition met
new pod ready at 22:25:14
(.venv)

axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab9)
$ NEW_POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)
kubectl exec $NEW_POD -- psql -U quickticket -d quickticket -c '\dt'
Did not find any relations.
(.venv)

...

axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab9)
$ echo "
Disaster at      $T_KILL
New pod ready    $T_READY
Restored         $T_RESTORED
App fully up     $T_APP_READY
"

Disaster at      22:24:51
New pod ready    22:25:14
Restored         22:26:07
App fully up     22:29:16

(.venv)
```
- Actual RTO value in seconds.

Actual RTO = T_APP_READY - T_KILL

- T_KILL: 22:24:51
- T_APP_READY: 22:29:16
- **RTO: 4 minutes 25 seconds (265 seconds)**

- Prometheus error-rate curve around the incident:

```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab9)
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B30s%5D))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783539231.090,"0"]}]}}(.venv)
```

- Answer: "The new Postgres pod was empty. Why? How would you eliminate this failure mode?" (Answer: no PVC — fix it in the Bonus.)


The new Postgres pod was empty because the deployment does not use a PersistentVolumeClaim (PVC). Without PVC, all data is stored on the pod's ephemeral storage, which is deleted when the pod is terminated. When the pod restarts, it starts with a fresh, empty database.


