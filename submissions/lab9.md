# Lab 9 — Stateful Services & DB Reliability — Submission

**Student:** jakefish18
**Repo:** https://github.com/jakefish18/SRE-Intro
**Branch:** `feature/lab9`

PR checklist:
```text
- [x] Task 1 done — Alembic migration under load + pg_dump/pg_restore cycle
- [x] Task 2 done — disaster recovery RTO/RPO measurement
- [x] Bonus Task done — PVC + automated CronJob backup with rotation
```

> **Setup notes:** QuickTicket on k3d — `postgres` Deployment (initially **no PVC**),
> `gateway` Rollout, `events`/`payments`/`redis` Deployments, in-cluster Prometheus.
> Alembic runs on the host (Python venv, `alembic 1.16.5` — the newest that supports
> the host's Python 3.9) against the k3d Postgres via a port-forward. The host's port
> **5432 was already taken by a local Docker Postgres**, so the port-forward maps
> **5433 → 5432** and `alembic.ini` uses `localhost:5433` (the committed file uses the
> lab-standard `5432`). `pg_dump`/`pg_restore` run **inside the pod** via `kubectl exec`
> (the host has no Postgres client). Traffic from `labs/lab8/mixedload.yaml`.

---

## Task 1 — Migrations & Backup/Restore

### 1. `alembic history` — two revisions (baseline + email)
```
538b3ff59ef9 -> 56780f89edf0 (head), add email column to events
<base> -> 538b3ff59ef9, baseline - pre-existing schema
```
Baseline was created empty and `alembic stamp head`-ed to mark the pre-existing
`seed.sql` schema as already applied (`alembic current` → `538b3ff59ef9 (head)`).

### 2. `\d events` — new `email` column
```
    Column     |           Type           | Nullable |              Default
---------------+--------------------------+----------+------------------------------------
 id            | integer                  | not null | nextval('events_id_seq'::regclass)
 name          | text                     | not null |
 venue         | text                     | not null |
 event_date    | timestamp with time zone | not null |
 total_tickets | integer                  | not null |
 price_cents   | integer                  | not null |
 email         | character varying(255)   |          |            ← added, NULLABLE
```

### 3. `time alembic upgrade head`
```
INFO  [alembic.runtime.migration] Running upgrade 538b3ff59ef9 -> 56780f89edf0, add email column to events
.venv/bin/alembic upgrade head  0.15s user 0.03s system 85% cpu  0.213 total
```
Elapsed **0.213s** — a nullable `ADD COLUMN` is metadata-only in PostgreSQL 11+ (no
table rewrite, no blocking lock), so it is instantaneous and safe under load.

### 4. Prometheus `5xx last 1min` — before vs after migration
```
5xx last 1min BEFORE migration: 0.0
5xx last 1min AFTER  migration: 0.0
```
**Zero additional 5xx** — the migration was fully zero-downtime while `mixedload`
drove live traffic through `/events` and `/reserve`.

### 5. Backup is valid
```
ls -lh /tmp/quickticket.dump  ->  21K
file:  PostgreSQL custom database dump - v1.16-0

pg_restore --list (TOC excerpt):
  TABLE public alembic_version | TABLE public events | TABLE public orders
  TABLE DATA public events | TABLE DATA public orders | TABLE DATA public alembic_version
  CONSTRAINT events_pkey | CONSTRAINT orders_pkey | FK CONSTRAINT orders_event_id_fkey
```

### 6. Row counts — before disaster / after DROP / after restore
```
                events   orders
BEFORE disaster    5      432       (pg_dump captured orders=432)
AFTER DROP orders  5      —  (table gone);  API smoke: /events = 502
AFTER pg_restore   5      432    ;           API smoke: /events = 200
```
`DROP TABLE orders CASCADE` broke the API (`/events` → 502, the events service query
touches `orders` for availability); `pg_restore --clean --if-exists` + a
`kubectl rollout restart deployment/events` (to drop stale DB connections) restored
both data and service.

### 7. RPO of a single `pg_dump`? How to improve?

**A single manual `pg_dump` gives an unbounded / stale RPO** — your recovery point is
frozen at *whenever you last ran the dump*. Any write after it (every confirmed order)
is **permanently lost** on a disaster; if the dump is a day old, RPO = 1 day. In this
run the orders count happened to be unchanged (432 → 432) only because `mixedload`'s
reserves were hitting a sold-out event (all 409s, no new confirmed orders) between
backup and drop; under real checkout volume the gap would be every order in that window.

**To improve:** (1) automate frequent dumps so RPO ≤ the interval — the **Bonus
CronJob runs every 5 min → RPO ≤ 5 min**; (2) for near-zero RPO, use **continuous WAL
archiving / point-in-time recovery** or a **streaming replica**, so you can recover to
seconds before the incident instead of the last snapshot.

---

## Task 2 — Disaster Recovery Under Load

**Timestamps (four phases)** — force-deleted the Postgres pod under live load.
(The Postgres Deployment has **no readiness probe**, so `kubectl wait --for=condition=Ready`
returns as soon as the container starts, *before* Postgres accepts connections — I gate
on `pg_isready` instead for accurate numbers.)
```
Disaster (pod force-deleted)       T_KILL      (20:44:17Z)
New pod ACCEPTING connections      +4s
Restore (pg_restore) complete      +4s
App fully up (events reconnected)  +11s
```

**Actual RTO: 11s** (kill → app fully serving). The DB pod itself was back in ~4s, but
recovery *required* a `pg_restore` + `kubectl rollout restart deployment/events`.

**RPO gap (orders before vs after restore):** before disaster **432**, after restore
**432** → **0 records lost** *in this run* — but only because `mixedload` was hitting a
sold-out event (all 409s, no new confirmed orders) between the backup and the disaster.
The RPO *time* is still "age of the last `pg_dump`"; under real checkout volume every
order written after the backup would be gone.

**Error-rate around the incident:**
```
sum(rate(gateway_requests_total{status=~"5.."}[30s])) = 1.6 /s   (spike during the DB gap)
```

**Why was the new Postgres pod empty? How to eliminate this failure mode?** The new
pod reported **"Did not find any relations"** — the Postgres Deployment had **no
PersistentVolumeClaim**, so its data lived on the pod's *ephemeral* filesystem. Deleting
the pod threw the data away, and the fresh pod ran `initdb` into an empty directory. The
fix is to back the data directory with a **PVC** so it outlives the pod — implemented in
the Bonus below (after which the same disaster loses nothing).

---

## Bonus Task — Persistent Storage + Automated Backup CronJob

### B.1 — PVC added to Postgres (diff)
```diff
 env:
   - { name: POSTGRES_PASSWORD, value: "quickticket" }
+  - { name: PGDATA, value: "/var/lib/postgresql/data/pgdata" }  # subdir avoids lost+found
 ...
   limits: { cpu: 200m, memory: 256Mi }
+  volumeMounts:
+    - name: data
+      mountPath: /var/lib/postgresql/data
+ volumes:                     # (pod spec)
+   - name: data
+     persistentVolumeClaim:
+       claimName: postgres-data
+---
+apiVersion: v1
+kind: PersistentVolumeClaim
+metadata: { name: postgres-data }
+spec:
+  accessModes: [ReadWriteOnce]
+  resources: { requests: { storage: 1Gi } }
```

**Re-measured RTO with PVC (no `pg_restore` needed):**
```
Re-seeded fresh PV: events=5 orders=0
DISASTER (force-delete pod) -> new pod ACCEPTING connections 3s after kill
new pod tables:  events + orders PRESENT (data survived via the PVC)
data after restart: events=5 orders=0   ← nothing lost, NO restore run
```
The DB was fully recovered **with all data intact in ~3s** and **zero recovery steps**
(no `pg_restore`, no re-seed, no RPO gap). Contrast with Task 2 (no PVC): the pod came
back equally fast but **empty**, forcing a `pg_restore` + re-seed and losing everything
since the last backup. (Total wall-clock in both was ~11–13s because it includes the
`rollout restart events` to refresh the connection pool — an app-layer step common to
both; the *DB* recovery itself dropped from "restore from backup" to "just remount the
volume".)

### B.2 — Backup CronJob (`k8s/backup-cronjob.yaml`)
```yaml
apiVersion: batch/v1
kind: CronJob
metadata: { name: postgres-backup }
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
            - name: backup
              image: postgres:17-alpine
              env:
                - { name: PGHOST, value: postgres }
                - { name: PGUSER, value: quickticket }
                - { name: PGDATABASE, value: quickticket }
                - { name: PGPASSWORD, value: quickticket }
              command: ["/bin/sh","-c"]
              args:
                - |
                  set -eu; cd /backups
                  OUT="quickticket_$(date -u +%Y%m%dT%H%M%SZ).dump"
                  pg_dump -Fc -f "$OUT"
                  # retention: keep the 5 newest, delete the rest
                  ls -1t quickticket_*.dump | tail -n +6 | xargs -r rm -v
              volumeMounts: [{ name: backups, mountPath: /backups }]
          volumes:
            - name: backups
              persistentVolumeClaim: { claimName: postgres-backups }
```

**Retention proof — `manual-7` log + `/backups` listing (exactly 5 after 7 runs):**
```
$ kubectl logs job/manual-7
creating quickticket_20260708T204755Z.dump
backup complete: 5.7K quickticket_20260708T204755Z.dump
removed 'quickticket_20260708T204740Z.dump'      ← retention kicked in
retained: 5 dumps

$ kubectl exec deployment/backup-inspector -- ls -la /backups
-rw-r--r-- 1 root root 5853 quickticket_20260708T204744Z.dump
-rw-r--r-- 1 root root 5853 quickticket_20260708T204746Z.dump
-rw-r--r-- 1 root root 5853 quickticket_20260708T204749Z.dump
-rw-r--r-- 1 root root 5853 quickticket_20260708T204752Z.dump
-rw-r--r-- 1 root root 5853 quickticket_20260708T204755Z.dump
count: 5      ← 7 runs → exactly the 5 newest remain
```
