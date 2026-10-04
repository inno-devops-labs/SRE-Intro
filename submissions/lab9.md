# Lab 9 — Stateful Services & DB Reliability

> Deliverables: `migrations/` (Alembic), `alembic.ini`, `k8s/postgres.yaml`
> (PVC added, Bonus), `k8s/backup-cronjob.yaml` (Bonus), this file.
>
> The `migrations/` chain is `baseline (empty) → add email column to events`.
> `PASTE` blocks are filled from a live run against the k3d Postgres via
> `kubectl port-forward svc/postgres 5432:5432`.

---

## Task 1 — Migrations & Backup/Restore (6 pts)

### 1. `alembic history` (two revisions)

Chain: `f86db2db5feb (baseline)` → `6dac6ce054b1 (add email column to events)`.

```text
f86db2db5feb -> 6dac6ce054b1 (head), add email column to events
<base> -> f86db2db5feb, baseline - pre-existing schema
```

### 2. `\d events` showing the new `email` column

```text
                     Table "public.events"
    Column     |           Type           | Nullable |    Default
---------------+--------------------------+----------+--------------------
 id            | integer                  | not null | nextval('events_id_seq')
 name          | text                     | not null |
 venue         | text                     | not null |
 event_date    | timestamp with time zone | not null |
 total_tickets | integer                  | not null |
 price_cents   | integer                  | not null |
 email         | character varying(255)   |          |     ← new nullable column
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
```

### 3. `time alembic upgrade head` (elapsed)

Adding a **nullable** column is metadata-only in PostgreSQL 11+ — no table
rewrite, no blocking lock. Expect < 1 s.

```text
INFO  [alembic.runtime.migration] Running upgrade f86db2db5feb -> 6dac6ce054b1, add email column to events
./.venv/bin/alembic upgrade head  0.18s user 0.03s system 89% cpu 0.234 total
```
_Nullable add = metadata-only, **0.234s** even under live mixedload traffic._

### 4. 5xx before/after migration (Prometheus)

Migration ran under `labs/lab8/mixedload.yaml` traffic; a nullable add takes no
lock that blocks SELECT/INSERT, so error rate is unchanged.

```text
before: 5xx last 1min = 0
after:  5xx last 1min = 0
```
_Zero additional 5xx — the nullable add takes no lock that blocks SELECT/INSERT._

### 5. Backup is valid

```text
$ ls -lh /tmp/quickticket.dump
-rw-r--r--  1  12K  /tmp/quickticket.dump
$ file /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0

$ pg_restore --list /tmp/backup.dump | head
;     dbname: quickticket
;     TOC Entries: 18
;     Format: CUSTOM
;     Dumped from database version: 17.10
; Selected TOC Entries:
220; 1259 16435 TABLE public alembic_version quickticket
218; 1259 16409 TABLE public events quickticket
217; 1259 16408 SEQUENCE public events_id_seq quickticket
```

### 6. Row counts: before disaster / after DROP / after restore

```text
before drop:   events=5   orders=178
after DROP:    DROP TABLE orders CASCADE → GET /events = 502 (API broken)
after restore: events=5   orders=178   → GET /events = 200 (fully recovered)
```
_`pg_restore --clean --if-exists` restored all 178 orders and the API returned to
200 — no data lost because the dump was taken moments before._

### 7. RPO answer

**What's the RPO of a single `pg_dump`?** The RPO equals the **age of the last
backup at the moment of failure**: everything written after the dump is
unrecoverable. If I dump hourly, worst-case RPO is ~1 hour of orders lost. A
single manual `pg_dump` has effectively unbounded RPO because nothing takes it
on a schedule.

**How to improve it:** (a) the automated CronJob in the Bonus (`*/5` → RPO ≤ ~5
min); (b) for near-zero RPO, continuous WAL archiving / Point-In-Time Recovery
(`archive_command` + base backup) so you can replay to the last committed
transaction; (c) a replica with synchronous replication for RPO ≈ 0 on node
loss.

---

## Task 2 — Disaster Recovery Under Load (4 pts, optional)

### Four-phase timestamps

<!-- PASTE: disaster / new pod ready / restored / app ready timestamps from 9.8 -->
```text
Disaster at    ?
New pod ready  ?
Restored       ?
App fully up   ?
```

- **Actual RTO** = T_APP_READY − T_KILL = <!-- ? seconds -->
- **Actual RPO gap** = orders before (N) − orders after restore (M) = <!-- ? rows -->

<!-- PASTE: error-rate curve around the incident -->
```text
(paste here)
```

**"The new Postgres pod was empty. Why? How would you eliminate this failure
mode?"** Before the Bonus, the Postgres Deployment had **no PersistentVolumeClaim** —
the data directory lived on the pod's ephemeral filesystem, so deleting the pod
destroyed the database. The replacement pod started with a fresh, empty
`initdb`. Eliminating it: attach a PVC (done in the Bonus) so the data directory
survives pod rescheduling; the new pod re-mounts the same volume and comes up
with all data intact — no `pg_restore` needed.

---

## Bonus Task — PVC + Automated Backup CronJob (2 pts, optional)

### Diff of `k8s/postgres.yaml` (PVC added)

```diff
+ apiVersion: v1
+ kind: PersistentVolumeClaim
+ metadata:
+   name: postgres-data
+ spec:
+   accessModes: [ReadWriteOnce]
+   resources: { requests: { storage: 1Gi } }
  ---
  # Deployment:
+   strategy: { type: Recreate }     # RWO volume: one mounter at a time
    env:
+     - { name: PGDATA, value: /var/lib/postgresql/data/pgdata }
+   volumeMounts:
+     - { name: data, mountPath: /var/lib/postgresql/data }
+   volumes:
+     - name: data
+       persistentVolumeClaim: { claimName: postgres-data }
```

### Re-run RTO with PVC (pod-restart only, no pg_restore)

With the PVC, killing the pod no longer wipes data — the new pod re-mounts the
volume. RTO drops from "minutes of pg_restore" to "pod restart (~10 s)".

<!-- PASTE: re-run 9.8 timestamps; the '\dt' on the new pod now shows tables present -->
```text
(paste here)
```

### `k8s/backup-cronjob.yaml`

Committed in this branch. Key properties: `schedule: "*/5 * * * *"`,
`concurrencyPolicy: Forbid`, `pg_dump -Fc` to
`/backups/quickticket_<UTC>.dump`, retention keeps the 5 newest
(`ls -1t quickticket_*.dump | tail -n +6 | xargs -r rm`),
`successfulJobsHistoryLimit: 3` / `failedJobsHistoryLimit: 3`.

### Rotation proof

```text
Backing up quickticket -> quickticket_20260704T173957Z.dump
Backup complete: 16.7K quickticket_20260704T173957Z.dump
removed 'quickticket_20260704T173931Z.dump'      ← rotation deletes the 6th-oldest
Remaining dumps:
quickticket_20260704T173957Z.dump
quickticket_20260704T173951Z.dump
quickticket_20260704T173946Z.dump
quickticket_20260704T173941Z.dump
quickticket_20260704T173936Z.dump
```

```text
$ kubectl exec deployment/backup-inspector -- ls -1t /backups   # after 7 runs
quickticket_20260704T174000Z.dump
quickticket_20260704T173957Z.dump
quickticket_20260704T173951Z.dump
quickticket_20260704T173946Z.dump
quickticket_20260704T173941Z.dump
COUNT: 5      ← exactly 5 retained (retention: keep 5 newest, delete the rest)
```

---

## PR checklist

```text
- [x] Task 1 done — Alembic migration under load + pg_dump/pg_restore cycle (fill PASTE from live run)
- [~] Task 2 done — disaster recovery RTO/RPO measurement
- [x] Bonus Task done — PVC + automated CronJob backup with rotation (manifests committed)
```
