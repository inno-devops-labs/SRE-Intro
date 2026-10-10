# Lab 9 — Stateful Services & DB Reliability

## Task 1 — Migrations & Backup/Restore

```bash
(.venv) [ustkost@prime SRE-Intro]$ alembic history
e3bbd9095744 -> 47c11b80dd7b (head), add email column to events
<base> -> e3bbd9095744, baseline - pre-existing schema
```

```bash
(.venv) [ustkost@prime SRE-Intro]$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
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

```bash
(.venv) [ustkost@prime SRE-Intro]$ time alembic upgrade head
Handling connection for 5432
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade e3bbd9095744 -> 47c11b80dd7b, add email column to events

real	0m0.316s
user	0m0.271s
sys	0m0.024s
```

### Before migration:
```bash
(.venv) [ustkost@prime SRE-Intro]$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))' \
  | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('5xx last 1min:', r[0]['value'][1] if r else 0)"
5xx last 1min: 0
```

# After migration:
```bash
(.venv) [ustkost@prime SRE-Intro]$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))' \
  | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('5xx last 1min:', r[0]['value'][1] if r else 0)"
5xx last 1min: 0
```

```bash
(.venv) [ustkost@prime SRE-Intro]$ ls -lh /tmp/quickticket.dump
-rw-r--r-- 1 ustkost ustkost 7.2K Jul 10 00:14 /tmp/quickticket.dump
(.venv) [ustkost@prime SRE-Intro]$ kubectl exec $POD -- pg_restore --list /tmp/backup.dump | head -25
;
; Archive created at 2026-07-09 21:14:05 UTC
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

```bash
(.venv) [ustkost@prime SRE-Intro]$ kubectl exec $POD -- psql -U quickticket -d quickticket \
  -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
 count 
-------
     5
(1 row)

 count 
-------
    50
(1 row)

(.venv) [ustkost@prime SRE-Intro]$ kubectl exec $POD -- psql -U quickticket -d quickticket -c 'DROP TABLE orders CASCADE'
DROP TABLE

(.venv) [ustkost@prime SRE-Intro]$ kubectl exec $POD -- psql -U quickticket -d quickticket   -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
ERROR:  relation "orders" does not exist
LINE 1: SELECT count(*) FROM events; SELECT count(*) FROM orders
                                                          ^
 count 
-------
     5
(1 row)

command terminated with exit code 1
```

### What's the RPO of your current setup (single `pg_dump`)? How would you improve it?
Current RPO is the time since the last `pg_dump`, because we are just doing a single manual backup. To improve it, we can introduce cron jobs to run `pg_dump` automatically on some schedule, for example every hour (thus RPO would be 1 hour). To improve even further, we can add WAL archiving.

## Task 2 — Disaster Recovery Under Load

```bash
(.venv) [ustkost@prime SRE-Intro]$ echo "
Disaster at      $T_KILL
New pod ready    $T_READY
Restored         $T_RESTORED
App fully up     $T_APP_READY
"

Disaster at      00:29:47
New pod ready    00:29:56
Restored         00:30:15
App fully up     00:30:33
```

### Actual RTO: 30:33 - 29:47 = 46 seconds

### RPO gap: 50 orders before disaster, 50 orders after - RPO gap is zero

### Error rate: ~0.71 req/s

### The new Postgres pod was empty. Why? How would you eliminate this failure mode?
Postgres deployment had no PVC (PersistentVolumeClaim). Data only lived inside the pod. To fix this, we can mount a PVC on `/var/lib/postgresql/data`. Then all new pods will see the data from the previous ones (this is like Docker Compose volumes).
