# Lab 9 Submission

I ran this lab on the local `k3d` cluster in Docker. My local Python was `3.9.6`, so I used `alembic==1.16.5` because `1.18.4` from the lab text was not available for this Python version.

## Task 1. Migrations and backup/restore

### 1. `alembic history`

```text
48d652278c2e -> 97a71fcd4c35 (head), add email column to events
<base> -> 48d652278c2e, baseline - pre-existing schema
```

### 2. `events` table after migration

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

### 3. `time alembic upgrade head`

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 48d652278c2e -> 97a71fcd4c35, add email column to events
real 0.23
user 0.17
sys 0.03
```

### 4. Prometheus `5xx last 1min` before and after migration

```text
before: 5xx last 1min: 2.1817983474680624
after:  5xx last 1min: 2.181877692081555
```

The value stayed almost the same. I did not see an extra error spike from the migration itself.

### 5. Backup proof

```text
$ ls -lh /tmp/quickticket.dump
-rw-r--r--@ 1 pavel  wheel   7.1K Jul  4 13:18 /tmp/quickticket.dump

$ file /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
```

```text
;
; Archive created at 2026-07-04 10:18:23 UTC
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

### 6. Row counts before disaster, after drop, and after restore

Before drop:

```text
events_count = 5
orders_count = 50
```

After `DROP TABLE public.orders CASCADE`:

```text
events_count = 5
orders_table = null
/events = 502
/reserve = 500
```

After restore:

```text
events_count = 5
orders_count = 50
/events = 200
/health = 200
```

### 7. RPO answer

With one manual `pg_dump`, the theoretical RPO is the time since the last backup. In my run the restored row counts matched the pre-disaster counts, so the observed RPO gap was `0` rows. To improve this, I would use scheduled backups and WAL-based point-in-time recovery.

## Task 2. Disaster recovery under load

### Timestamps

```text
healthy before disaster: 2026-07-04 13:24:01 MSK
orders before disaster: 50

disaster_at=13:25:03
pod_ready_at=13:25:04
restored_at=13:25:42
verified_app_ready_at=13:26:30
```

### Actual RTO

```text
confirmed RTO = 87 seconds
```

I used the final successful `/events=200` check as the recovery point.

### Actual RPO

```text
orders before disaster = 50
orders after restore   = 50
RPO gap                = 0 rows
```

### Prometheus error-rate around the incident

```text
sum(rate(gateway_requests_total{status=~"5.."}[30s])) = 0.4799568047994855
```

### Why the new Postgres pod was empty

The new pod was empty because `k8s/postgres.yaml` has no PVC. Postgres data was stored on the pod filesystem, so after the pod was deleted the new container started with an empty database directory. I would fix this by adding a PersistentVolumeClaim and mounting it into Postgres. Then the data would survive pod recreation and the recovery time would be much lower.

### One important observation

The pod became `Ready` very fast, but my first `psql` and `pg_restore` calls still failed because Postgres inside the container was not accepting connections yet. So in a real recovery run it is safer to check an actual DB connection, not only the Kubernetes `Ready` condition.