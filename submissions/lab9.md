# Lab 9. Stateful services and DB reliability

**Student:** Kirill Fadeev
**Email:** ki.fadeev@innopolis.university
**Environment:** WSL2 (kernel 6.18.33.2-microsoft-standard-WSL2, x86_64), k3d v5.9.0, k3s v1.35.5+k3s1, kubectl v1.37.0, PostgreSQL 17.11 (postgres:17-alpine), Alembic 1.18.4, SQLAlchemy 2.0.49, psycopg2-binary 2.9.11, gateway is the 5 replica Argo Rollout from Lab 7, in-cluster Prometheus in `monitoring` from Lab 7, load from `labs/lab8/mixedload.yaml` (2 replicas)

All timestamps are local time (UTC+3) unless a line says otherwise. Pod logs and `pg_restore --list` print UTC.

Besides Prometheus, every experiment ran an in-cluster prober: a Python loop inside the `payments` pod (which does not touch Postgres) calling `GET http://gateway:8080/events` every 0.5 s with a 4 s timeout. It shows what a client saw, sample by sample, and it is the reason two Prometheus numbers below get corrected.

## Setup

The database had the seed data already, because in my fork the seed is a ConfigMap mounted into `/docker-entrypoint-initdb.d` (Lab 4 change). Load started with the Lab 8 mixedload.

```plaintext
$ kubectl apply -f labs/lab8/mixedload.yaml
deployment.apps/mixedload created
redis held counter for event 1: 6
19:03:28.906 orders=8 held=10
19:03:34.628 orders=40 held=42
19:03:40.135 orders=50 held=50
19:03:45.671 orders=50 held=50
```

Twelve seconds after the load started, writes stopped at 50 orders. Event 1 has 100 tickets, and `/reserve` computes availability as `total - confirmed orders - event:1:held`. The events service increments `event:1:held` on every reservation and never decrements it on confirmation, so each completed purchase consumes two tickets and event 1 sells out after 50 orders. From then on mixedload only produces `409` on `/reserve` and no new rows. `/events` meanwhile still reports `available: 50` for event 1, because it subtracts only confirmed orders.

> This matters for the whole lab: with the provided load, a backup taken a minute after start already contains every row the system will ever write, so the RPO gap in rows is always zero. Before each disaster test I deleted the `event:1:held` key in Redis so that writes resumed after the backup. That is the only manual change to application state in this lab.

Alembic runs from WSL through `kubectl port-forward svc/postgres 5432:5432`. The WSL distro has no `python3-venv`, so the venv was created with `python3 -m venv --without-pip` and pip from `get-pip.py`, then `pip install alembic==1.18.4 psycopg2-binary==2.9.11 sqlalchemy==2.0.49`.

## Task 1. Migrations and backup/restore

### 9.1 to 9.3: Alembic init, baseline, migration

`alembic init migrations`, then `sqlalchemy.url` in `alembic.ini` points at the port-forward. The baseline revision is empty, and its docstring records where the schema really comes from (the seed ConfigMap). It was stamped onto the existing database:

```plaintext
$ alembic revision -m "baseline - pre-existing schema"
Generating /home/fadeev/SRE-Intro/migrations/versions/77bf50754a91_baseline_pre_existing_schema.py ...  done
$ alembic stamp head
INFO  [alembic.runtime.migration] Running stamp_revision  -> 77bf50754a91
$ alembic current
77bf50754a91 (head)
```

The real migration, `migrations/versions/43b43772c5f3_add_email_column_to_events.py`:

```python
def upgrade() -> None:
    """Upgrade schema."""
    # Nullable with no default: PostgreSQL 11+ only updates the catalog, no table
    # rewrite. It still needs a brief ACCESS EXCLUSIVE lock on events, so the
    # migration must not wait behind a long transaction (see lock_timeout in env.py).
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('events', 'email')
```

`alembic history`:

```plaintext
$ alembic history
77bf50754a91 -> 43b43772c5f3 (head), add email column to events
<base> -> 77bf50754a91, baseline - pre-existing schema
```

### 9.4: Migration under load

Mixedload was running (`mixedload 2/2`), and the gateway saw about 340 requests in the minute before the migration.

```plaintext
query: sum(increase(gateway_requests_total{status=~"5.."}[1m]))
BEFORE: 5xx last 1min: 0   all requests last 1min: 340.8048
$ time alembic upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 77bf50754a91 -> 43b43772c5f3, add email column to events

real	0m0.415s
user	0m0.340s
sys	0m0.041s
```

```plaintext
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

The migration ran at 19:04:02.4. My first "after" query was taken too late (93 s after the migration, so its 1 minute window did not contain it), so I re-evaluated the same query at a fixed time 50 s after the migration with the Prometheus `time=` parameter:

```plaintext
evaluated at 19:04:52.397, migration at 19:04:02.397, so [1m] covers it
sum(increase(gateway_requests_total{status=~"5.."}[1m])):
    (empty result: no such series at that time)
sum(increase(gateway_requests_total[1m])):
    {} 806.2
count of 5xx series that exist at all at that moment:
    (empty result: no such series at that time)
probe samples=80 codes={'200': 80} max_latency=95ms
```

5xx before: 0, after: 0. The stronger statement is the last query: at that moment no 5xx series existed at all, so the gateway had not returned a single 5xx since its pods started, migration included. The prober agrees: 80 out of 80 requests got 200, the slowest took 95 ms.

#### Control experiment: the same DDL behind a long transaction

"Metadata-only" says nothing about locking. `ADD COLUMN` still takes an ACCESS EXCLUSIVE lock, only for a very short time. To show when that matters, I held `events` with an ordinary read transaction (`BEGIN; SELECT count(*) FROM events; SELECT pg_sleep(12)`) and 2 s later ran the same kind of ALTER in a second session, inside `BEGIN ... ROLLBACK` so that nothing persisted:

```plaintext
19:05:48.566 who is waiting on locks:
 pid |  wait   | state  |                         query
-----+---------+--------+--------------------------------------------------------
 941 | Timeout | active | SELECT pg_sleep(12)
 957 | Lock    | active | ALTER TABLE events ADD COLUMN lock_probe int
 933 | Lock    | active | SELECT e.id, e.name, e.venue, e.event_date ...
 949 | Lock    | active | SELECT e.id, e.name, e.venue, e.event_date ...
 958 | Lock    | active | SELECT e.id, e.name, e.venue, e.event_date ...

ALTER TABLE
Time: 10075.134 ms (00:10.075)
```

The ALTER waited 10 s for the reader, and while it waited every new `SELECT` on `events` queued behind it, even though a SELECT and the long reader do not conflict with each other. The prober during that window:

```plaintext
  19:05:44.841 200 16ms
  19:05:45.341 ERR:TimeoutError 4002ms
  19:05:49.344 ERR:TimeoutError 4005ms
  19:05:53.349 200 1749ms
  19:05:55.099 200 97ms
```

The same test with `SET lock_timeout = '2s'` in the migration session:

```plaintext
SET
ERROR:  canceling statement due to lock timeout
Time: 2000.918 ms (00:02.001)
--- probe during guarded ALTER (19:06:01.085 .. 19:06:13.417)
probe samples=22 codes={'200': 22} max_latency=1821ms
```

The migration fails after 2 s and can be retried, while traffic stays at 200. I made that the default for every Alembic run: `migrations/env.py` now opens its connection with `connect_args={"options": "-c lock_timeout=2s"}`. To confirm that Alembic picks the guard up (the psql test above sets it by hand), I ran a downgrade while a session held `events`:

```plaintext
19:17:35.257 $ time alembic downgrade -1   (session holding events for 10s)
sqlalchemy.exc.OperationalError: (psycopg2.errors.LockNotAvailable) canceling statement due to lock timeout
[SQL: ALTER TABLE events DROP COLUMN email]
real	0m2.393s
alembic current: 43b43772c5f3 (head)
```

The unguarded ALTER also exposed a gap in the 5xx query from the lab. The gateway returned four `504` on `/events`, but the 1 minute increase evaluated over that window reports zero:

```plaintext
19:05:39.518 before-lockqueue
    (empty result: no such series at that time)
19:06:01.085 after-unguarded-ALTER
    {'path': '/events', 'status': '504'} 4.0
what increase() reports over the unguarded ALTER, evaluated 20s after it:
    {'path': '/events', 'status': '504'} 0.0
```

> Whether a migration is safe depends on the lock queue as well as on the DDL. A "metadata-only" ALTER caused 4 timeouts because it waited behind a 12 s read, and `lock_timeout` turned the same situation into a failed, retryable migration with zero user impact. The `5xx last 1min` check from the lab could not have caught this: the `504` series was created by this very incident, and `increase()` cannot see the first increment of a series that did not exist before. The "0 additional 5xx" claim above holds because the raw counter shows that no 5xx series existed at all.

### 9.5: pg_dump backup

```plaintext
19:06:25.019 orders just before dump: 50
19:06:26.112 orders just after dump: 50
$ ls -lh /tmp/quickticket.dump
-rw-r--r-- 1 fadeev fadeev 7.2K Oct 11 19:06 /tmp/quickticket.dump
$ file /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
$ kubectl exec $POD -- pg_restore --list /tmp/backup.dump | head -25
;
; Archive created at 2026-10-11 16:06:25 UTC
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
orders rows inside the dump: 50
```

The TOC lists all three tables with data, `alembic_version` included, so the dump carries the schema version along with the rows. I counted the `orders` rows inside the archive (`pg_restore --data-only -t orders`) instead of trusting the file size: 50.

### 9.6: Data loss and restore

| Moment | events | orders | `GET /events` |
|---|---:|---:|---|
| Before disaster (19:06:30) | 5 | 50 | 200 |
| After `DROP TABLE orders CASCADE` | 5 | `ERROR: relation "orders" does not exist` | 502 |
| After `pg_restore --clean --if-exists` | 5 | 50 | 200 |

```plaintext
19:06:33.020 DROP
DROP TABLE
$ kubectl run smoke-drop --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- curl -s -o /dev/null -w /events=%{http_code}\n http://gateway:8080/events
/events=502
19:06:50.969 RESTORE
pg_restore exit=0
restore took 0.4s; table missing for 18.3s
$ kubectl run smoke-restore ...
/events=200
probe samples=150 codes={'200': 114, '502': 36} max_latency=75ms
  outage 19:06:33.663 -> 19:06:51.169  36 samples  codes={'502': 36}
```

The `502` comes from the gateway: events fails the `JOIN orders` in `/events` with a 500, and the gateway maps that to 502. The read path broke as well as the write path, because the event list joins `orders` to compute availability. The outage lasted exactly as long as the table was missing (17.5 s by the prober, 18.3 s by the script); `pg_restore` itself took 0.4 s. No events restart was needed: the database process stayed up, so the pooled connections stayed valid. Raw counter: 146 `502` on `/events`, while the 1 minute increase evaluated right after the restore showed 138.6.

### 9.7: RPO answer

**What's the RPO of the current setup (single `pg_dump`)?** The time since that one dump, and it grows without bound: every row written after 19:06:25 exists only in the live database. In Task 2 the dump was 12 minutes old (721.9 s) when Postgres died, and the 26 orders written after it were lost. In Task 1 the gap happened to be zero only because event 1 had sold out, so nothing was written after the dump. Without a PVC there is a second, worse failure mode: any pod or container restart replaces the data directory, so the effective RPO is "since the last dump" even without a disaster, and "everything" if nobody took a dump.

**How to improve it.** First, keep the data independent of the pod with a PVC (bonus B.1), so a restart is no longer a data loss event at all. Second, take dumps on a schedule (bonus B.2): a `*/5` CronJob bounds the logical-backup RPO at about 5 minutes. Third, for an RPO of seconds you need continuous WAL archiving with point-in-time recovery (for example pgBackRest or WAL-G, or an operator like CloudNativePG that does it), because `pg_dump` is a snapshot and cannot capture what happens between snapshots. Fourth, test restores on a schedule, because an untested backup only tells you that a file exists.

> At this size the dump took about a second and the restore 0.4 s, so restore speed does not limit recovery here; the age of the dump does. The backup schedule fixes the RPO in advance, and a faster restore procedure cannot bring back rows that were never dumped.

## Task 2. Disaster recovery under load

### 9.8: Kill Postgres and recover

Before the test: `alembic current` = `43b43772c5f3 (head)`, `email` column present, 50 orders. Event 1 was sold out (`/reserve` sees `100 - 50 - 50 = 0`), so I deleted `event:1:held` and writes resumed:

```plaintext
orders=50 redis event:1:held=50  /events says ev1 available=50  /reserve sees 100-orders-held=0
$ kubectl exec deploy/redis -- redis-cli DEL event:1:held
1
19:17:57.350 orders=52
19:18:01.647 orders=76
19:18:06.028 orders=76
healthy at 19:18:26.004, orders=76
$ kubectl delete pod -l app=postgres --grace-period=0 --force
pod "postgres-7667ddb566-sjqw7" force deleted from default namespace
$ kubectl wait --for=condition=Ready pod -l app=postgres --timeout=60s
error: no matching resources found
```

The `kubectl wait` from the lab fails right after a force delete: the old pod object is already gone and the ReplicaSet has not created the new one yet, so the selector matches nothing. The script waits instead in a loop until a pod with a different name answers `pg_isready -h 127.0.0.1`.

What the new pod contained:

```plaintext
           List of relations
 Schema |  Name  | Type  |    Owner
--------+--------+-------+-------------
 public | events | table | quickticket
 public | orders | table | quickticket
(2 rows)

 events
--------
      5

 orders
--------
      0

    Column     |           Type           | Collation | Nullable |              Default
ERROR:  relation "alembic_version" does not exist
$ alembic current   (against the new pod)
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
postgres log of the new pod (initdb ran again):
initdb: warning: enabling "trust" authentication for local connections
/usr/local/bin/docker-entrypoint.sh: running /docker-entrypoint-initdb.d/01-seed.sql
2026-10-11 16:18:35.145 UTC [1] LOG:  database system is ready to accept connections
```

The lab expects the tables to be gone. In my fork they came back: the seed ConfigMap is mounted into the initdb directory, so a new pod runs `initdb` on an empty data directory and then the seed. The result looks like a healthy database: both tables exist, 5 events are there, `/events` would return 200. But all 76 orders are gone, the `email` column is gone, and `alembic_version` is gone, so `alembic current` prints no revision at all.

Restore and reconnect:

```plaintext
$ kubectl cp /tmp/quickticket.dump $NEW_POD:/tmp/backup.dump
$ kubectl exec $NEW_POD -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
pg_restore exit=0
 events
--------
      5
(1 row)

 orders
--------
     50
(1 row)
$ kubectl rollout restart deployment/events
$ kubectl rollout status deployment/events --timeout=60s
deployment "events" successfully rolled out
```

### 9.9: RTO and RPO

```plaintext
Disaster at      19:18:26.917
New pod ready    19:18:36.305   (+9.4s)
Restored         19:18:39.468   (+12.6s)
App fully up     19:18:51.351   (+24.4s)

RTO (T_APP_READY - T_KILL) = 24.4 s
RPO (time) = T_KILL - T_BACKUP = 721.9 s
orders: at backup=50, before disaster N=76, after restore M=50, lost N-M=26
```

| Metric | Value |
|---|---|
| Actual RTO (`T_APP_READY - T_KILL`) | **24.4 s** |
| User-visible outage by the prober (kill to first 200) | 25.5 s |
| RPO in time (backup at 19:06:25, kill at 19:18:26) | 721.9 s |
| RPO in rows: orders before disaster vs after restore | 76 vs 50, **26 orders lost** |

The 24.4 s is a scripted runbook executed back to back; a person typing the same `kubectl cp`, `pg_restore` and restart commands would take minutes. What the prober saw:

```plaintext
19:18:26.876 200 8ms events=5 ev1_available=24
19:18:27.376 ERR:TimeoutError 4004ms
19:18:31.380 ERR:TimeoutError 4006ms
19:18:35.386 ERR:TimeoutError 4005ms
19:18:39.392 ERR:TimeoutError 4004ms
19:18:43.396 ERR:TimeoutError 4004ms
19:18:47.400 ERR:TimeoutError 4004ms
19:18:51.404 502 5ms
19:18:51.904 502 4ms
19:18:52.404 200 12ms events=5 ev1_available=50
```

The restore finished at 19:18:39.5, yet requests kept hanging until the events restart at 19:18:51. The database was back, but events kept its pooled connections to the dead pod's IP, and requests on them hung instead of failing. The gateway's 5 s timeout and the prober's 4 s timeout then turned every request into a slow failure. Note also `ev1_available=50` right after recovery: the restored orders count (50) is what the API advertises, while 26 tickets that customers paid for no longer exist in the database.

Prometheus around the incident, `sum(rate(gateway_requests_total{status=~"5.."}[30s]))`:

```plaintext
19:18:21    0.000
19:18:26    0.000
19:18:31    0.080
19:18:36    0.080
19:18:41    0.200
19:18:46    0.240
19:18:51    0.320
19:18:56    0.375
19:19:01    0.504
19:19:06    0.372
19:19:11    0.324
19:19:16    0.240
19:19:21    0.120
19:19:26    0.000
5xx by path over the incident (increase):
    {'path': '/events', 'status': '504'} 9.3
    {'path': '/events', 'status': '502'} 4.1
    {'path': '/events/{id}/reserve', 'status': '504'} 1.0
raw 5xx counters now (series born during the incident lose their first increment in increase()):
    {'path': '/events', 'status': '504'} 16.0
    {'path': '/events', 'status': '502'} 150.0
    {'path': '/events/{id}/reserve', 'status': '504'} 4.0
```

The peak error rate was half a request per second during a 25 s total outage. The service was failing slowly (5 s gateway timeouts), and mixedload is a synchronous loop, so throughput collapsed together with availability, which kept the error count small. The raw counters show 12 new `504` and 4 new `502` on `/events` against the Task 1 values (4 and 146), plus 4 `504` on `/reserve`, where `increase()` reports 1.0 because the series was created during the incident.

**Why was the new Postgres pod empty, and how to eliminate it?** The Deployment has no volume for `/var/lib/postgresql/data`. I checked the container's mounts on the node (`crictl inspect`): only the seed ConfigMap, the service account token, `/etc/hosts` and the termination log are mounted, so the data directory lives in the container's own filesystem and disappears with the container. In my setup it even came back looking healthy, because initdb plus the seed rebuilt an empty schema. The fix is a PersistentVolumeClaim mounted on the data directory, so the data outlives any single pod. In this cluster the same failure happened silently every time the WSL VM stopped: the postgres pod had 11 restarts when this lab started, and each one reset the database to the seed.

> When storage is not persistent, a restart is a data loss event, and the seed ConfigMap made that loss invisible. Health checks passed, `/events` returned 200, and the only signals were a zero orders count and an empty `alembic current`. Recovery also took two separate steps: restoring the data fixed the database, and a restart of events was still needed to fix the application's connections to it.

## Bonus. Persistent storage and automated backups

### B.1: PVC for Postgres

```diff
@@ -41,6 +41,23 @@ data:
         ('Kubernetes Deep Dive', 'Auditorium B', '2026-10-10 10:00:00+00', 80, 8000)
     ON CONFLICT DO NOTHING;
 ---
+# Lab 9 bonus: the data directory lives on a PersistentVolume. Before this the
+# database sat in the container's writable layer, so every new container (pod
+# deletion, node restart, even a container restart) started from an empty
+# directory and the seed ConfigMap silently rebuilt the schema with 5 events
+# and no orders.
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
@@ -49,6 +66,11 @@ metadata:
     app: postgres
 spec:
   replicas: 1
+  # RollingUpdate would start the new pod while the old one still runs, and on a
+  # single node ReadWriteOnce does not stop both from opening the same PGDATA.
+  # Recreate stops the old postmaster first.
+  strategy:
+    type: Recreate
   selector:
     matchLabels:
       app: postgres
@@ -70,6 +92,9 @@ spec:
               value: "quickticket"
             - name: POSTGRES_PASSWORD
               value: "quickticket"
+            # subdirectory of the mount, so initdb never sees lost+found
+            - name: PGDATA
+              value: /var/lib/postgresql/data/pgdata
           readinessProbe:
             exec:
               command: ["pg_isready", "-h", "127.0.0.1", "-U", "quickticket"]
@@ -79,6 +104,8 @@ spec:
             periodSeconds: 10
             failureThreshold: 3
           volumeMounts:
+            - name: data
+              mountPath: /var/lib/postgresql/data
             - name: seed
               mountPath: /docker-entrypoint-initdb.d
               readOnly: true
@@ -91,6 +118,9 @@ spec:
               cpu: 200m
               memory: 256Mi
       volumes:
+        - name: data
+          persistentVolumeClaim:
+            claimName: postgres-data
         - name: seed
           configMap:
             name: postgres-seed
```

Besides the lab's PVC and `PGDATA`, I added `strategy: Recreate`. ReadWriteOnce restricts a volume to one node, not to one pod, so on this single-node cluster the default RollingUpdate could start the new postmaster on the same data directory while the old one still runs.

Applying it replaces the pod, and with no volume until now that again means an empty data directory. So I took a dump first and loaded it into the new volume. "Re-seed once" was not needed: initdb plus the seed ConfigMap ran on the fresh PV automatically.

```plaintext
$ kubectl get pvc postgres-data
NAME            STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS   AGE
postgres-data   Bound    pvc-cab5d614-7d81-4b85-89df-3d5049df3447   1Gi        RWO            local-path     19s
PGDATA=/var/lib/postgresql/data/pgdata
--- fresh PV: initdb + seed ConfigMap ran once
 events
--------
      5
(1 row)

 orders
--------
      0
(1 row)

pg_restore exit=0
 events
--------
      5
(1 row)

 orders
--------
     62
(1 row)
$ alembic current
43b43772c5f3 (head)
```

The 9.8 disaster test repeated on the PVC:

```plaintext
healthy at 19:23:24.862, orders=80
$ kubectl delete pod -l app=postgres --grace-period=0 --force
pod "postgres-5969c66b7-vjq5n" force deleted from default namespace
old pod postgres-5969c66b7-vjq5n, new pod postgres-5969c66b7-z4xh4
 events
--------
      5
(1 row)

 orders
--------
     80
(1 row)
 version_num
--------------
 43b43772c5f3
 email         | character varying(255)   |           |          |
postgres log of the new pod (no initdb, WAL crash recovery instead):
PostgreSQL Database directory appears to contain a database; Skipping initialization
2026-10-11 16:23:26.778 UTC [1] LOG:  database system is ready to accept connections

Disaster at      19:23:25.740
New pod ready    19:23:28.013   (+2.3s)
Restored         not needed (data on the PV)
App fully up     19:23:49.713   (+24.0s, self-healed, no restart)

RTO with PVC = 24.0 s   (Task 2 without PVC: 24.4 s)
orders before kill N=80, after M=80, lost 0
```

| | Task 2, no PVC | Bonus, PVC |
|---|---:|---:|
| Postgres accepting connections | +9.4 s | **+2.3 s** |
| Data usable (after restore) | +12.6 s | **+2.3 s**, no restore |
| Manual steps | `kubectl cp`, `pg_restore`, restart events | none |
| Orders lost | 26 of 76 | **0 of 80** |
| Schema version after recovery | lost until restore | `43b43772c5f3` kept |
| End-to-end, script (`T_APP_READY - T_KILL`) | 24.4 s | 24.0 s |
| End-to-end, prober (kill to first 200) | 25.5 s | 23.4 s |

Database recovery went from 12.6 s with a restore to 2.3 s without one, and the data loss went from 26 orders to zero. The end-to-end number barely moved, and the prober shows why:

```plaintext
19:23:25.742 502 6ms
19:23:26.242 ERR:TimeoutError 4002ms
19:23:30.244 ERR:TimeoutError 4005ms
19:23:34.249 ERR:TimeoutError 4002ms
19:23:38.252 ERR:TimeoutError 4005ms
19:23:42.258 ERR:TimeoutError 4005ms
19:23:46.263 502 2858ms
19:23:49.121 200 170ms
```

Postgres was ready at 19:23:28, but the events service kept hanging on its pooled connections to the old pod for about 20 more seconds, until they failed and the pool opened new ones. This time it recovered by itself without a restart. Comparing the two runs, the same ~20 s of stale connections defined the end of the outage in Task 2 too; there the restart happened to end it at about the same moment.

A control run with the normal rollout path (`kubectl rollout restart deployment/postgres`, Recreate strategy) also kept the data: `orders before rollout=80 after=80`.

> The PVC fixed the database: recovery takes 2.3 s instead of 12.6 s, with no restore step and zero rows lost. The user-facing RTO is now set by the client, at about 20 s for events' connection pool to give up on a dead IP. The next improvement belongs in the app: validate connections before use or set short TCP keepalive/user timeouts, so that a Postgres restart costs seconds end to end as well.

### B.2: Backup CronJob with retention

`k8s/backup-cronjob.yaml`:

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
  # A run that could not start within 2 minutes is skipped, not piled up.
  startingDeadlineSeconds: 120
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 2
      activeDeadlineSeconds: 300
      template:
        metadata:
          labels:
            app: postgres-backup
        spec:
          restartPolicy: OnFailure
          containers:
            - name: pg-dump
              image: postgres:17-alpine
              imagePullPolicy: IfNotPresent
              env:
                - { name: PGHOST, value: postgres }
                - { name: PGUSER, value: quickticket }
                - { name: PGDATABASE, value: quickticket }
                - { name: PGPASSWORD, value: quickticket }
              command: ["/bin/sh", "-c"]
              args:
                - |
                  set -eu
                  cd /backups
                  name="quickticket_$(date -u +%Y%m%dT%H%M%SZ).dump"
                  # Dump to a temporary name and rename only after pg_dump
                  # succeeded and the archive lists its tables: a failed or
                  # truncated dump never counts as one of the 5 kept files.
                  pg_dump -Fc -f "$name.partial"
                  tables=$(pg_restore --list "$name.partial" | grep -c 'TABLE DATA')
                  [ "$tables" -gt 0 ] || { echo "dump has no table data"; exit 1; }
                  mv "$name.partial" "$name"
                  echo "wrote $name ($(du -h "$name" | cut -f1), $tables tables with data)"
                  # Retention: keep the 5 newest. File names carry a UTC
                  # timestamp, so newest-first is a reverse name sort and does
                  # not depend on mtimes, which a copy or restore can change.
                  ls -1 quickticket_*.dump | sort -r | tail -n +6 | xargs -r rm -v
                  rm -f ./*.partial
                  echo "kept: $(ls -1 quickticket_*.dump | wc -l) dumps"
              volumeMounts:
                - name: backups
                  mountPath: /backups
              resources:
                requests:
                  cpu: 50m
                  memory: 64Mi
                limits:
                  cpu: 200m
                  memory: 256Mi
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

Two choices differ from the hints in the lab. Retention sorts by file name instead of `ls -t`: the name is the backup's timestamp, while mtime changes whenever a file is copied back or restored from elsewhere. And a dump only gets its final name after `pg_restore --list` confirms that it contains table data, so a failed `pg_dump` cannot push a good dump out of the 5 kept files.

Seven manual runs:

```plaintext
$ kubectl logs job/manual-1
wrote quickticket_20261011T162617Z.dump (12.0K, 3 tables with data)
kept: 1 dumps
...
  dumps on the PVC: 4   (after manual-4)
  dumps on the PVC: 5   (after manual-5)
  dumps on the PVC: 5   (after manual-6)
  dumps on the PVC: 5   (after manual-7)
$ kubectl logs job/manual-7
wrote quickticket_20261011T162650Z.dump (12.0K, 3 tables with data)
removed 'quickticket_20261011T162623Z.dump'
kept: 5 dumps
$ kubectl exec deployment/backup-inspector -- ls -la /backups
total 68
drwxrwxrwx    2 root     root          4096 Oct 11 16:26 .
drwxr-xr-x    1 root     root          4096 Oct 11 16:26 ..
-rw-r--r--    1 root     root          8387 Oct 11 16:26 quickticket_20261011T162628Z.dump
-rw-r--r--    1 root     root          8387 Oct 11 16:26 quickticket_20261011T162634Z.dump
-rw-r--r--    1 root     root          8387 Oct 11 16:26 quickticket_20261011T162640Z.dump
-rw-r--r--    1 root     root          8387 Oct 11 16:26 quickticket_20261011T162645Z.dump
-rw-r--r--    1 root     root          8387 Oct 11 16:26 quickticket_20261011T162650Z.dump
retention ok: exactly 5 dumps
```

The schedule itself, by waiting for the next `*/5` tick (times in UTC):

```plaintext
lastScheduleTime before: <none>  now: 16:26:59Z
scheduled run at 2026-10-11T16:30:00Z
$ kubectl logs job/postgres-backup-29862270
wrote quickticket_20261011T163000Z.dump (12.0K, 3 tables with data)
removed 'quickticket_20261011T162628Z.dump'
kept: 5 dumps
$ kubectl get jobs
NAME                       STATUS     COMPLETIONS   DURATION   AGE
manual-6                   Complete   1/1           4s         3m21s
manual-7                   Complete   1/1           4s         3m16s
postgres-backup-29862270   Complete   1/1           3s         5s
```

Jobs `manual-1` to `manual-5` are no longer listed. A Job created with `kubectl create job --from=cronjob/...` gets an ownerReference to the CronJob, so it counts against `successfulJobsHistoryLimit: 3` just like a scheduled run.

Finally, I restored the newest CronJob dump into a scratch database and compared it with the live one:

```plaintext
newest: /backups/quickticket_20261011T163000Z.dump
restore exit=0
live quickticket:            events=5 orders=80 alembic=43b43772c5f3
restored quickticket_verify: events=5 orders=80 alembic=43b43772c5f3
email column in restored copy: 1
```

> With the CronJob, the worst case for a logical backup goes from "whenever someone last ran pg_dump" to 5 minutes. The PVC does a different job: it protects against pod loss, while the dumps protect against `DROP TABLE` and similar mistakes, which a PVC replicates faithfully. One weak point remains: the backups sit on a local-path volume on the same node as the database, so losing the node loses both. Off-node storage (object storage) is the next step.

## Results

| Item | Result |
|---|---|
| Alembic baseline + migration | `77bf50754a91` stamped, `43b43772c5f3` applied in 0.415 s |
| 5xx during migration | 0 before, 0 after; no 5xx series existed; prober 80/80 OK |
| Unguarded ALTER behind a 12 s reader | waited 10.1 s, 3 SELECTs queued, 4 gateway 504 |
| Same ALTER with `lock_timeout=2s` | failed at 2.0 s, prober 22/22 OK; guard now in `env.py` |
| `pg_dump` | 7.2K, 18 TOC entries, 3 tables with data, 50 orders inside |
| DROP `orders` → restore | 502 for 17.5 s, restore 0.4 s, counts 5/50 → error → 5/50 |
| Task 2 RTO (no PVC) | 24.4 s (prober: 25.5 s) |
| Task 2 RPO | 721.9 s, 26 of 76 orders lost |
| Bonus RTO (PVC) | DB 2.3 s, end-to-end 24.0 s (prober: 23.4 s), 0 orders lost |
| CronJob retention | 7 manual runs → 5 dumps, `removed '...'` in logs, scheduled run at 16:30Z |
| Restore test of a CronJob dump | identical counts and revision |

The PVC and the CronJob fixed the data side. A Postgres restart now costs 2.3 s and no rows instead of 12.6 s and 26 orders, and the newest dump is never older than 5 minutes, which a real restore confirmed. Several checks along the way gave a green answer while users saw errors. The nullable migration was harmless on its own but blocked reads once its lock request had to wait. The lab's `5xx last 1min` query read zero during incidents whose 504 series were created by those incidents, so I report raw counters next to it. After the first crash, Postgres came back from the seed with a passing readiness probe and without its orders or schema version. What still costs about 20 s is the events service holding pooled connections to a pod that no longer exists. That is an application fix, and it is the next one I would make.
