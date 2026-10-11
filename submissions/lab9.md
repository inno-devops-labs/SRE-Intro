# Lab 9 — Stateful Services & DB Reliability

**Branch:** `feature/lab9` (from upstream `main`)
**Cluster:** k3d `quickticket`, single node, arm64; storage class `local-path` (default)
**Postgres:** `postgres:17-alpine` Deployment, **no PVC** at the start of the lab
**Raw terminal output:** `raw.log`, section `=== LAB 9 ===` (every command below is timestamped there). All times UTC, 2026-10-11.

- [x] Task 1 — Alembic migration under load + pg_dump/pg_restore cycle
- [x] Task 2 — disaster recovery RTO/RPO measurement
- [x] Bonus Task — PVC + automated CronJob backup with rotation

---

## 9.0 Things the lab text does not mention

| # | What happened | What I did |
|---|---|---|
| 1 | **`localhost:5432` is a different database.** The docker-compose stack from Labs 1–3 (`app-postgres-1`, `postgres:17-alpine`, same `quickticket/quickticket` credentials) still listens on host port 5432. The spec's `kubectl port-forward svc/postgres 5432:5432` cannot bind, and Alembic pointed at `localhost:5432` would silently migrate the compose DB. Measured: `localhost:5432` → 811 orders, postmaster started 2026-09-28; the k3d DB → 15960 orders, started 2026-10-05. | Port-forward on **15432** (`kubectl port-forward svc/postgres 15432:5432`, run under `timeout`), `alembic.ini` points at `localhost:15432` with a comment explaining why. |
| 2 | Event 1 was sold out again (`total_tickets=100`, 15830 orders, Redis `event:1:held=347`) because of the held-counter leak found in Lab 8. mixedload would only get 409 on `/reserve`, write no new orders, and the RPO measurement in Task 2 would have nothing to lose. | `UPDATE events SET total_tickets=100000000 WHERE id=1` at 13:27:55 (restored to 100 in cleanup). |
| 3 | Host `pg_restore` is 14.15 (Homebrew) and cannot read a PG 17 dump: `pg_restore: error: unsupported version (1.16) in file header`. | All `pg_restore` work runs inside the Postgres pod, as the spec suggests. |
| 4 | The retention one-liner from the spec (`ls -1t quickticket_*.dump \| tail -n +6 \| xargs -r rm`) can delete the **newest** dumps. Tested in `postgres:17-alpine`: 7 files created within the same second → busybox `ls -t` compares whole seconds and breaks ties by name, so `quickticket_…16…` and `…17…` (the two newest) were removed. | The CronJob sorts by the UTC timestamp in the file name instead (`ls -1 … \| sort -r \| tail -n +6`); verified it removes `…11…` and `…12…`. |
| 5 | **My mistake — an accidental restore.** My first 9.6 attempt (13:40:27) kept the `psql` command in a zsh variable. zsh does not word-split it, so every `psql` call, **including the `DROP`**, failed with `command not found`. The `pg_restore --clean --if-exists` line was a direct call, so it **did** run at 13:40:30, against an intact database. The DB went back to the 13:30:30 dump: 0 orders with `created_at` between 13:30:31 and 13:40:30, ≈ **3282 orders lost** (`increase` of `/pay` 200 over that window). These were synthetic mixedload orders, but it is a real, unplanned RPO event: the RPO of a 10-minute-old dump is 10 minutes of writes. | Re-ran 9.6 from a script under `sh`, with a guard that aborts if `psql` does not return `1`. Before the drop I took a **fresh** dump (13:41:27) so the planned drill would not lose another 10 minutes. 9.6 below is that run. |

---

## Task 1 — Migrations & Backup/Restore

### Setup

```text
$ kubectl apply -f labs/lab8/mixedload.yaml           # 13:27:55, rollout OK
$ python3 -m venv .venv && .venv/bin/pip install alembic==1.18.4 psycopg2-binary==2.9.11 sqlalchemy==2.0.49
alembic 1.18.4 / psycopg2-binary 2.9.11 / SQLAlchemy 2.0.49
$ kubectl port-forward svc/postgres 15432:5432        # see 9.0 #1
$ connect localhost:15432 → events: 5 orders: 15960 | started: 2026-10-05 11:39:10+00
$ connect localhost:5432  → events: 5 orders: 811   | started: 2026-09-28 07:36:47+00   ← not the cluster
```

### 9.1–9.3 Alembic init, baseline, migration

```text
$ alembic init migrations
$ alembic revision -m "baseline - pre-existing schema"
Generating migrations/versions/f654912bc7e1_baseline_pre_existing_schema.py ...  done
$ alembic stamp head
INFO  [alembic.runtime.migration] Running stamp_revision  -> f654912bc7e1
$ alembic current
f654912bc7e1 (head)
$ alembic revision -m "add email column to events"
Generating migrations/versions/5fcf405ffc77_add_email_column_to_events.py ...  done
```

`migrations/versions/5fcf405ffc77_add_email_column_to_events.py`:

```python
def upgrade() -> None:
    # Adding a nullable column is a metadata-only change in PostgreSQL 11+ —
    # no table rewrite. It still needs a brief ACCESS EXCLUSIVE lock on `events`;
    # lock_timeout makes the migration fail fast instead of queueing behind a
    # long transaction and blocking every reader queued behind it.
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'email')
```

The only addition to the spec's migration is `lock_timeout`. Adding a nullable column does not rewrite the table, but `ALTER TABLE` still takes an ACCESS EXCLUSIVE lock. If a long transaction holds a conflicting lock, the ALTER waits in the lock queue and every new `SELECT` on `events` queues behind it. With `lock_timeout` the migration fails after 3 s instead of stalling all traffic.

### 1. `alembic history`

```text
$ alembic history --verbose
Rev: 5fcf405ffc77 (head)
Parent: f654912bc7e1
Path: migrations/versions/5fcf405ffc77_add_email_column_to_events.py
    add email column to events
    Revision ID: 5fcf405ffc77
    Revises: f654912bc7e1

Rev: f654912bc7e1
Parent: <base>
Path: migrations/versions/f654912bc7e1_baseline_pre_existing_schema.py
    baseline - pre-existing schema
    Revision ID: f654912bc7e1
    Revises:
```

### 2. `\d events`

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

### 3. `time alembic upgrade head` (under mixedload, 2/2 replicas)

```text
$ time alembic upgrade head          # start 13:29:01
INFO  [alembic.runtime.migration] Running upgrade f654912bc7e1 -> 5fcf405ffc77, add email column to events
timeout 60 .venv/bin/alembic upgrade head  0.15s user 0.04s system 61% cpu 0.299 total
# end 13:29:02
$ alembic current
5fcf405ffc77 (head)
```

### 4. Prometheus 5xx before / after

| | Time | `sum(increase(gateway_requests_total{status=~"5.."}[1m]))` (spec query) | same, `path!="/health"` | Traffic |
|---|---|---|---|---|
| before | 13:29:01 | **0** | 0 | 18.18 RPS: `/events` 5.636, `/reserve` 5.545, `/pay` 5.509 (all 200); p99 0.0249 / 0.0941 / 0.0674 s |
| after (+65 s) | 13:30:15 | **0** | 0 | 18.09 RPS: 5.564 / 5.509 / 5.509 (all 200); p99 0.0249 / 0.0950 / 0.0692 s |

Over the 2 minutes that include the migration (`increase[2m]` at 13:30:15), user requests were `/events` 200 = 661.6, `/reserve` 200 = 662.6 and `/pay` 200 = 659.5. Every 5xx series stayed at 0. **The migration added no 5xx**, and p99 did not move.

### 5. pg_dump backup

```text
$ kubectl exec -i postgres-78489d7f5f-89sv2 -- pg_dump -U quickticket -Fc quickticket > quickticket.dump   # 13:30:30
$ ls -lh quickticket.dump
599K quickticket.dump
$ file quickticket.dump
quickticket.dump: PostgreSQL custom database dump - v1.16-0
$ kubectl cp quickticket.dump postgres-78489d7f5f-89sv2:/tmp/backup.dump
$ kubectl exec postgres-78489d7f5f-89sv2 -- pg_restore --list /tmp/backup.dump | head -25
;
; Archive created at 2026-10-11 13:30:30 UTC
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
220; 1259 16433 TABLE public alembic_version quickticket
218; 1259 16389 TABLE public events quickticket
217; 1259 16388 SEQUENCE public events_id_seq quickticket
3481; 0 0 SEQUENCE OWNED BY public events_id_seq quickticket
219; 1259 16397 TABLE public orders quickticket
3316; 2604 16392 DEFAULT public events id quickticket
3474; 0 16433 TABLE DATA public alembic_version quickticket
3472; 0 16389 TABLE DATA public events quickticket
3473; 0 16397 TABLE DATA public orders quickticket
3482; 0 0 SEQUENCE SET public events_id_seq quickticket
```

The dump file is kept in the session scratch directory rather than `/tmp`, and it is not committed.

### 9.6 Data loss → restore

The run uses the fresh dump taken at 13:41:27 (630 290 bytes; TOC has TABLE DATA for `alembic_version`, `events`, `orders`), see 9.0 #5. mixedload 2/2 ran throughout.

```text
[13:41:29] BEFORE:        events=5 orders=16978
$ DROP TABLE orders CASCADE                         # 13:41:29
DROP TABLE
[13:41:29] AFTER DROP:    events=5 orders=ERROR:  relation "orders" does not exist
$ kubectl run smoke … curl /events, POST /events/1/reserve      # 13:41:29
/events=502
/events/1/reserve=500
$ kubectl exec postgres-78489d7f5f-89sv2 -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
pg_restore rc=0                                     # 13:41:32 → 13:41:33
[13:41:33] AFTER RESTORE: events=5 orders=16976 alembic=5fcf405ffc77 email_col=1
$ kubectl run smoke …                                # 13:41:33
/events=200
/events/1/reserve=200
```

### 6. Row counts

| | events | orders |
|---|---|---|
| before disaster (13:41:29) | 5 | 16978 |
| after `DROP TABLE orders CASCADE` (13:41:29) | 5 | — (`relation "orders" does not exist`) |
| after `pg_restore` (13:41:33) | 5 | 16976 |

- 2 orders confirmed between the dump (13:41:27) and the drop (13:41:29) are gone. That is the RPO of this drill.
- Orders attempted during the 4 s without the table also failed: user 5xx/s (`rate[15s]`) was 0.5 at 13:41:35, 1.9 at 13:41:40, 1.4 at 13:41:45 and 0 from 13:41:50.
- `increase[60s]` at 13:42:10 shows `/events` 502 = 20.7 (`/events` joins `orders`). The `/reserve` 500s seen by the smoke test do not appear in Prometheus at all, because of the gateway error-handler bug found in Lab 8 Task 2.
- Restore also brought back the schema state: `alembic_version` = `5fcf405ffc77` and the `email` column, because the dump was taken after the migration.

### 7. RPO of the current setup

**RPO = the age of the last `pg_dump` at the moment of the disaster.** A single manual dump has no upper bound: everything committed after it is lost. It is not theoretical here. The accidental restore in 9.0 #5 used a 10-minute-old dump and lost ≈3282 orders, while the drill above, with a 2-second-old dump, lost 2. Two things would improve it:

- **Scheduled dumps** (Bonus CronJob every 5 min, keep 5). This caps the RPO at ≤ 5 min plus dump time, and the RTO no longer depends on a laptop file.
- **A PVC.** Without it, *any* pod restart turns into a restore-from-dump with the same RPO (Task 2).

For an RPO of seconds, logical dumps are the wrong tool. That needs continuous WAL archiving / PITR (e.g. pgBackRest, WAL-G) or a streaming replica.

---

## Task 2 — Disaster Recovery Under Load

### 9.8 Kill Postgres and recover

Procedure as in the spec, scripted (`sh`, everything under `timeout`). Backup = the dump from **13:41:27**. mixedload 2/2 ran the whole time. A separate poller recorded the postgres/events pods, gateway Ready count and a real `GET /events` through the gateway Service (from the Prometheus pod, `wget -T 3`) every ~2–3 s.

```text
T0 13:43:48 healthy: orders=17720 last_order=2026-10-11 13:43:48.164448+00 (old pod postgres-78489d7f5f-89sv2)
$ kubectl delete pod -l app=postgres --grace-period=0 --force
pod "postgres-78489d7f5f-89sv2" force deleted from default namespace
T_KILL 13:43:52
new pod object postgres-78489d7f5f-8mxzw seen at 13:43:54
pod/postgres-78489d7f5f-8mxzw condition met
T_READY 13:43:54
T_PG_ACCEPTING 13:43:58                       ← pg_isready said yes
$ kubectl exec postgres-78489d7f5f-8mxzw -- psql -c '\dt'
psql: error: … FATAL:  database "quickticket" does not exist
$ pg_restore --clean --if-exists
pg_restore: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed: No such file or directory
T_RESTORED 13:43:58                           ← restore FAILED
$ kubectl rollout restart deployment/events → T_APP_READY 13:44:07   (events Ready on an empty DB)
```

**The first restore failed, and the cause is in the postgres log of the new pod.** The `postgres:17-alpine` entrypoint initialises an empty data directory with a *temporary* server:

```text
13:43:58.077 [59]  database system is ready to accept connections     ← temporary init server
13:43:58.478 [59]  received fast shutdown request
13:43:58.781       PostgreSQL init process complete; ready for start up.
13:43:58.909 [1]   database system is ready to accept connections     ← the real server
```

`pg_isready` hit the temporary server, so my "wait until Postgres accepts connections" passed ~0.4 s before it was shut down. `pg_restore` landed in the restart window. My script then waited for the poller to see `/events` 200, which never came, and gave up at ~13:46:3x. Manual recovery:

```text
$ psql -c 'SELECT 1' → 1 ; \dt → Did not find any relations.        # 13:46:56
$ pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump   # 13:46:56 → 13:46:57 (rc 0)
T_RESTORED 13:46:57    orders=16974
$ kubectl rollout restart deployment/events → rollout OK
T_APP_READY 13:47:01
13:47:04 /events=download timed out
13:47:06 /events=200  (then 200, 200)
```

User-visible timeline (poller, state changes only):

| Time | postgres | events | gateway Ready | `GET /events` |
|---|---|---|---|---|
| 13:43:48 | old pod Running | Ready | 5/5 | 200 |
| 13:44:01 | **new pod Running (empty DB)** | Ready | 5/5 | timed out |
| 13:44:04 | Running | old NotReady, new pod starting | **0/5** | connection refused |
| 13:44:10 | Running | new pod Ready | 0/5 | 502 |
| 13:44:18 | Running | Ready | **5/5** | **502** — everything is "healthy" but there are no tables |
| 13:47:06 | Running (restored) | Ready (restarted) | 5/5 | **200** |

Prometheus, `rate[30s]` every 15 s, user 5xx/s: 0.0 (13:43:45) → 0.08 (13:44:00) → 1.08 → 4.46 → **6.4–6.9 from 13:44:45 to 13:47:00** (that equals the entire user RPS: 100 % errors) → 2.6 (13:47:15) → 0.16 (13:47:30) → 0.0 (13:47:45). Total user RPS fell from 16.8 to ~6.6 in the same period, because the clients got fast 502s instead of slow successes.

### 9.9 RTO and RPO

| Phase | Time | t from disaster |
|---|---|---|
| Disaster (`delete --force`) | 13:43:52 | 0 |
| New pod Ready (no readiness probe → "container started") | 13:43:54 | 2 s |
| Postgres really accepting (real server, from its log) | 13:43:58.909 | 7 s |
| Restored (second, successful `pg_restore`) | 13:46:57 | 185 s |
| App ready (`events` restarted) | 13:47:01 | 189 s |
| First `GET /events` 200 | 13:47:06 | **194 s** |

- **Actual RTO = 194 s** (T_KILL → first successful `/events`). The restore and restart themselves took 5 s. ~3 minutes were lost because the readiness signals I relied on lied: pod `Ready` at +2 s, `pg_isready` at +6 s, and events + gateway `Ready 5/5` at +26 s, all on a database without tables.
- **RPO gap:** 17720 orders at T0 (13:43:48) → **16974** orders created before the disaster after the restore. **746 orders lost**. The newest restored order is from 13:41:27.756, i.e. RPO ≈ **2 min 25 s** of writes (the age of the dump).

### Why was the new Postgres pod empty? How to eliminate this failure mode?

The postgres Deployment has **no volume**. PGDATA lives in the container's writable layer, which is deleted with the pod. The replacement pod starts with an empty data directory, and the entrypoint runs `initdb` and creates an empty `quickticket` database. Kubernetes did its job (a new pod 2 s later); the data was never Kubernetes-managed. To eliminate it:

1. Put PGDATA on a **PersistentVolumeClaim**, so a pod restart is just a restart (Bonus B.1).
2. Use `strategy: Recreate` so two postmasters never share the RWO volume.
3. Keep **scheduled dumps** for the failures a PVC does not cover (DROP, corruption, volume loss).
4. Make health checks reflect the data, not just a TCP connection. Here `SELECT 1` passed on an empty DB, and `pg_isready` passed against the init server. A readiness probe that queries a real table, and a restore procedure that waits for the real server (e.g. retry `psql -c 'SELECT 1'` until the postmaster PID is 1, or check for `PostgreSQL init process complete`), would have saved 3 minutes here.

---

## Bonus Task — Persistent Storage + Automated Backup CronJob

### B.1 PVC for Postgres

`k8s/postgres.yaml` is not on upstream `main`. As in Lab 7, the first commit of this branch promotes the fork-`main` version unchanged, so the next commit shows only the real change:

```diff
@@ -1,3 +1,13 @@
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
@@ -6,6 +16,10 @@
     app: postgres
 spec:
   replicas: 1
+  # RWO volume: the old pod must be gone before the new one mounts the same
+  # PGDATA, otherwise a RollingUpdate briefly runs two postmasters on one dir.
+  strategy:
+    type: Recreate
   selector:
     matchLabels:
       app: postgres
@@ -24,6 +38,11 @@
               value: "quickticket"
             - name: POSTGRES_PASSWORD
               value: "quickticket"
+            - name: PGDATA                      # subdir — avoid lost+found at the mount root
+              value: /var/lib/postgresql/data/pgdata
+          volumeMounts:
+            - name: data
+              mountPath: /var/lib/postgresql/data
           ports:
             - containerPort: 5432
           resources:
@@ -33,6 +52,10 @@
             limits:
               cpu: 200m
               memory: 256Mi
+      volumes:
+        - name: data
+          persistentVolumeClaim:
+            claimName: postgres-data
 ---
 apiVersion: v1
 kind: Service
```

Beyond the spec: **`strategy: Recreate`**. With the default `RollingUpdate` (maxSurge 25 % → 1), the new pod starts *before* the old one stops, and both would mount the same RWO `local-path` volume on the single node, i.e. two postmasters on one PGDATA.

Switching storage is itself an outage, so I measured it. Instead of the spec's "re-seed once" I restored a **fresh dump** taken right before the switch. `seed.sql` would have wiped every order, the `email` column and `alembic_version`, leaving the migration history out of sync with the DB.

```text
$ pg_dump -Fc (pre-PVC)               13:48:40–13:48:41   orders at dump: 17534
$ kubectl apply -f k8s/postgres.yaml   13:48:41   persistentvolumeclaim/postgres-data created, deployment.apps/postgres configured
deployment "postgres" successfully rolled out   13:48:50
13:48:53.593 [42] database system is ready to accept connections      ← init server on the fresh PV
PostgreSQL init process complete; ready for start up.
13:48:54.107 [1]  database system is ready to accept connections      ← real server (waited for this, see Task 2)
$ kubectl get pvc postgres-data → Bound  pvc-279901fc-…  1Gi  RWO  local-path
$ PGDATA=/var/lib/postgresql/data/pgdata ; ls /var/lib/postgresql/data → pgdata
$ pg_restore --clean --if-exists pre_pvc.dump   rc=0   13:48:55
$ kubectl rollout restart deployment/events → 13:49:00
13:49:01 /events=200
AFTER: orders=17545 alembic=5fcf405ffc77 email_col=1
```

The storage migration caused ~20 s of downtime (13:48:41 → 13:49:01). Orders written between the dump and the old pod's shutdown (a few seconds) were lost. A zero-loss migration would need a replica or a write freeze.

### B.1 Re-run of 9.8 with the PVC

Same script as Task 2 (`kubectl delete pod -l app=postgres --grace-period=0 --force`, wait, `\dt`, `rollout restart deployment/events`, same poller), minus the restore step:

```text
T0 13:50:01 healthy: orders=17855 last_order=2026-10-11 13:50:01.624099+00 (old pod postgres-68466c5ccd-mhbfc)
$ kubectl delete pod -l app=postgres --grace-period=0 --force
T_KILL 13:50:05
new pod object postgres-68466c5ccd-6mzfw seen at 13:50:05
T_READY 13:50:07
$ kubectl exec postgres-68466c5ccd-6mzfw -- psql -c '\dt'
 public | alembic_version | table | quickticket
 public | events          | table | quickticket
 public | orders          | table | quickticket
orders on new pod right now: 17877
$ kubectl rollout restart deployment/events → T_APP_READY 13:50:16
postgres log: "PostgreSQL Database directory appears to contain a database; Skipping initialization"
              13:50:06.559 database system was shut down at 2026-10-11 13:50:05 UTC
              13:50:06.651 database system is ready to accept connections
poller: 13:50:10 /events=download timed out … 13:50:18 /events=200 (gw 0/5→4/5→5/5 by 13:50:22)
orders created before T_KILL, after recovery: 17873  (17855 at T0 + 18 created 13:50:01.6–13:50:05 = 17873)
```

| | Task 2: no PVC, restore from dump | Bonus: PVC, pod restart only |
|---|---|---|
| Disaster | 13:43:52 | 13:50:05 |
| New pod Ready | 13:43:54 (+2 s) | 13:50:07 (+2 s) |
| Data on the new pod | none (`database "quickticket" does not exist`) | all 3 tables, 17877 orders |
| Restore needed | yes (first attempt failed, second at 13:46:57) | **no** |
| App ready (`events` restarted) | 13:47:01 | 13:50:16 |
| First `/events` 200 (**RTO**) | 13:47:06 → **194 s** | 13:50:18 → **13 s** |
| Orders lost (**RPO**) | **746** (≈2 min 25 s, age of the dump) | **0** |

The old postmaster logged a clean shutdown at 13:50:05, and the new one started at 13:50:06 with no crash recovery, so the force-delete did not overlap two servers here. That is luck of timing: `--grace-period=0 --force` removes the API object without waiting for the kubelet. On a stateful pod that is exactly the action the Kubernetes docs warn against, and I would not use it outside a drill.

### B.2 Backup CronJob

`k8s/backup-cronjob.yaml`:

```yaml
# Periodic logical backup of the quickticket DB (Lab 9 Bonus B.2).
#
# Every 5 minutes: pg_dump -Fc into the postgres-backups PVC
# (labs/lab9/backup-storage.yaml), then keep only the 5 newest dumps.
#
# Apply with:   kubectl apply -f k8s/backup-cronjob.yaml
# Run now:      kubectl create job --from=cronjob/postgres-backup manual-1
# Inspect:      kubectl exec deployment/backup-inspector -- ls -la /backups
apiVersion: batch/v1
kind: CronJob
metadata:
  name: postgres-backup
spec:
  schedule: "*/5 * * * *"
  concurrencyPolicy: Forbid          # never stack two dumps if one runs long
  startingDeadlineSeconds: 120       # a run missed by >2 min is skipped, not replayed
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 2
      activeDeadlineSeconds: 240     # a hung dump must not block the next slot
      template:
        metadata:
          labels:
            app: postgres-backup
        spec:
          restartPolicy: OnFailure
          containers:
            - name: pg-dump
              image: postgres:17-alpine   # same major as the server, so pg_dump can read it
              env:
                - name: PGHOST
                  value: "postgres"
                - name: PGUSER
                  value: "quickticket"
                - name: PGDATABASE
                  value: "quickticket"
                - name: PGPASSWORD
                  value: "quickticket"
              command: ["/bin/sh", "-c"]
              args:
                - |
                  set -eu
                  cd /backups
                  TS=$(date -u +%Y%m%dT%H%M%SZ)
                  # Dump to a temp name first: a failed or partial dump must never
                  # count as "newest" and push a good one out of retention.
                  pg_dump -Fc -f "quickticket_${TS}.dump.partial"
                  mv "quickticket_${TS}.dump.partial" "quickticket_${TS}.dump"
                  echo "created quickticket_${TS}.dump ($(wc -c < "quickticket_${TS}.dump") bytes)"
                  # Retention: keep the 5 newest. Sort by the UTC timestamp in the
                  # name, not by mtime: busybox `ls -t` compares whole seconds and
                  # breaks ties by name, which can rotate out the newest dumps.
                  ls -1 quickticket_*.dump | sort -r | tail -n +6 | xargs -r rm -v
                  rm -f ./*.partial
                  echo "kept $(ls -1 quickticket_*.dump | wc -l) dumps:"
                  ls -1 quickticket_*.dump | sort -r
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

Design choices beyond the requirements: dump to `*.partial` then `mv`, so a failed dump never becomes the "newest" file and pushes a good one out of retention. Retention sorts by the UTC timestamp in the name, because the spec's `ls -1t` variant deleted the newest files in my test (9.0 #4). `startingDeadlineSeconds` and `activeDeadlineSeconds` keep a hung or missed run from blocking the next slot.

```text
$ kubectl apply -f labs/lab9/backup-storage.yaml
persistentvolumeclaim/postgres-backups created
deployment.apps/backup-inspector created
$ kubectl get pvc postgres-backups → Bound  pvc-db7bcd5c-…  1Gi  RWO  local-path
$ kubectl apply -f k8s/backup-cronjob.yaml
cronjob.batch/postgres-backup created   (*/5 * * * *)
$ kubectl create job --from=cronjob/postgres-backup manual-1   # 13:51:04
$ kubectl logs job/manual-1
created quickticket_20261011T135105Z.dump (667925 bytes)
kept 1 dumps:
quickticket_20261011T135105Z.dump
# manual-2 … manual-7 at 13:51:15, :18, :21, :24, :28, :31 — each Complete
$ kubectl logs job/manual-7
created quickticket_20261011T135131Z.dump (673372 bytes)
removed 'quickticket_20261011T135116Z.dump'
kept 5 dumps:
quickticket_20261011T135131Z.dump
quickticket_20261011T135128Z.dump
quickticket_20261011T135125Z.dump
quickticket_20261011T135122Z.dump
quickticket_20261011T135119Z.dump
```

(`manual-6` logged `removed 'quickticket_20261011T135105Z.dump'`, the first dump.)

```text
$ kubectl exec deployment/backup-inspector -- ls -la /backups      # 13:51:34
total 3300
drwxrwxrwx    2 root     root          4096 Oct 11 13:51 .
drwxr-xr-x    1 root     root          4096 Oct 11 13:51 ..
-rw-r--r--    1 root     root        670831 Oct 11 13:51 quickticket_20261011T135119Z.dump
-rw-r--r--    1 root     root        671480 Oct 11 13:51 quickticket_20261011T135122Z.dump
-rw-r--r--    1 root     root        672065 Oct 11 13:51 quickticket_20261011T135125Z.dump
-rw-r--r--    1 root     root        672722 Oct 11 13:51 quickticket_20261011T135128Z.dump
-rw-r--r--    1 root     root        673372 Oct 11 13:51 quickticket_20261011T135131Z.dump
count: 5
```

`kubectl get jobs` afterwards lists only `manual-5`, `manual-6` and `manual-7`. Jobs created with `--from=cronjob` get an ownerReference to the CronJob, so `successfulJobsHistoryLimit: 3` garbage-collects manual runs too.

**Restore test** (a backup that was never restored is not a backup): copied `quickticket_20261011T135131Z.dump` out of the backup PVC into the postgres pod, `createdb restore_test && pg_restore -d restore_test` → rc 0: `events|5`, `orders|18287`, `alembic 5fcf405ffc77`, newest order `13:51:31.629589+00`. Then `dropdb restore_test`.

The schedule also fires on its own (no manual trigger):

```text
$ kubectl get cronjob postgres-backup         # 13:55:04
postgres-backup   */5 * * * *   <none>     False     0        4s              4m
$ kubectl logs job/postgres-backup-29862115
created quickticket_20261011T135501Z.dump (715534 bytes)
removed 'quickticket_20261011T135119Z.dump'
kept 5 dumps:
quickticket_20261011T135501Z.dump
quickticket_20261011T135131Z.dump
quickticket_20261011T135128Z.dump
quickticket_20261011T135125Z.dump
quickticket_20261011T135122Z.dump
```

With this schedule the worst-case RPO for logical backups is ≤ 5 min + dump time (~1 s). With the PVC, a pod restart no longer needs them at all (RPO 0, RTO 13 s above).

---

## Cleanup

| Step | Result (13:55:25–13:55:59Z) |
|---|---|
| `kubectl delete -f labs/lab8/mixedload.yaml` | deleted |
| `kubectl delete -f k8s/backup-cronjob.yaml` | deleted |
| `kubectl delete -f labs/lab9/backup-storage.yaml` | `postgres-backups` PVC and `backup-inspector` deleted |
| port-forward | closed after Task 1 (13:31); `pkill` found none |
| `events.total_tickets` for id 1 | back to 100 |
| gateway / events / payments / redis specs | identical to the pre-lab reference (events only got the `restartedAt` annotation from the spec's `rollout restart`) |
| postgres | **kept on the PVC** (`postgres-data`, Bound): the only spec difference from the pre-lab reference is the `PGDATA` env + volume mount; DB has 19735 orders, `alembic_version` `5fcf405ffc77` |
| ArgoCD `quickticket` | unchanged, `automated: {}`, OutOfSync, last sync 2026-09-28. **Note:** fork `main` still has the PVC-less `k8s/postgres.yaml`. Any sync from it would drop the volume mount, and the next postgres pod would start empty again. |
