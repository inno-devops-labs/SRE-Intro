# Lab 9 — Stateful Services & DB Reliability

Environment: local k3d cluster `quickticket`. Postgres + gateway (Argo Rollouts, 5 replicas) + in-cluster
Prometheus. Alembic runs from the host against Postgres via `kubectl port-forward svc/postgres 5432:5432`.
Load from `labs/lab8/mixedload.yaml` throughout.

---

## Task 1 — Migrations & Backup/Restore

### 1. `alembic history` (two revisions: baseline + email)

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ alembic history
8da3f9006d53 -> b8891ec9e21a (head), add email column to events
<base> -> 8da3f9006d53, baseline - pre-existing schema
```

Baseline was stamped (not run) so Alembic treats the seed-created schema as the starting point:

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ alembic stamp head
INFO  [alembic.runtime.migration] Running stamp_revision  -> 8da3f9006d53
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ alembic current
8da3f9006d53 (head)
```

### 2. `\d events` — new `email` column present

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- psql -U quickticket -d quickticket -c '\d events'
                                        Table "public.events"
    Column     |           Type           | Nullable |              Default
---------------+--------------------------+----------+------------------------------------
 id            | integer                  | not null | nextval('events_id_seq'::regclass)
 name          | text                     | not null |
 venue         | text                     | not null |
 event_date    | timestamp with time zone | not null |
 total_tickets | integer                  | not null |
 price_cents   | integer                  | not null |
 email         | character varying(255)   |          |            <-- added by migration
```

### 3. `time alembic upgrade head` — nullable add is instant

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ time alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade 8da3f9006d53 -> b8891ec9e21a, add email column to events

real    0m0.216s
```

Adding a **nullable** column is a metadata-only change in PostgreSQL 11+ — no table rewrite, no blocking lock —
so it is safe to run under live traffic.

### 4. Prometheus 5xx before vs after the migration (unchanged)

```console
# BEFORE
5xx last 1min: 0
# AFTER
5xx last 1min: 0
```

Zero additional 5xx — the migration was invisible to the running `mixedload` traffic.

### 5. Backup is valid

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- pg_dump -U quickticket -Fc quickticket > /tmp/quickticket.dump
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ ls -lh /tmp/quickticket.dump
-rw-r--r--@ 1 rolanmulukin  wheel   7.1K Jul  9 16:47 /tmp/quickticket.dump
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ file /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec $POD -- pg_restore --list /tmp/backup.dump
218; 1259 16386 TABLE public events quickticket
219; 1259 16394 TABLE public orders quickticket
220; 1259 16408 TABLE public alembic_version quickticket
3472; 0 16386 TABLE DATA public events quickticket
3473; 0 16394 TABLE DATA public orders quickticket
3320; 2606 16393 CONSTRAINT public events events_pkey quickticket
3325; 2606 16412 FK CONSTRAINT public orders orders_event_id_fkey quickticket
```

### 6. Data loss → restore (row counts before / after drop / after restore)

```console
# BEFORE disaster
 events = 5 | orders = 50

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec $POD -- psql ... -c 'DROP TABLE orders CASCADE'
DROP TABLE
# API breaks (events list joins orders):
/events=502

MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec $POD -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
# AFTER restore
 events = 5 | orders = 50
# API healthy again (after events pool reconnect):
/events=200
```

### 7. RPO answer

**What's the RPO of a single `pg_dump`?** The RPO equals the **age of the last dump** — every write committed
between the dump and the failure is unrecoverable. With one manual dump taken ~3 minutes before the incident,
the RPO exposure was up to ~3 minutes of orders; in a "daily dump" setup it would be up to 24 hours. To improve
it I'd (a) run automated dumps on a short schedule (the Bonus CronJob, RPO → ≤5 min) and (b) for a real
production RPO of seconds, enable Postgres **WAL archiving / point-in-time recovery** or streaming replication
so committed transactions are shipped continuously rather than snapshotted periodically.

---

## Task 2 — Disaster Recovery Under Load (no PVC yet)

Killed the Postgres pod with `--grace-period=0 --force` while `mixedload` kept running.

```console
Disaster (T_KILL)   ~16:49:22
New pod Ready        16:49:39
Restored (pg_restore) 16:50:02
App fully up          16:50:05
```

- **Actual RTO = T_APP_READY − T_KILL ≈ 43 seconds** (pod reschedule + `kubectl cp` + `pg_restore` + events
  pool reconnect).
- **RPO gap:** orders before disaster = 50, orders after restore = 50 → **0 rows lost this run**, because the
  event inventory was already exhausted (reserves returning 409, so no new `orders` were committed after the
  backup). The RPO *exposure* is still the full age of the dump — a run with live order writes would have lost
  everything committed since the backup.
- Error-rate around the incident: **~192 5xx accumulated over the 3-minute window** spanning the outage
  (`sum(increase(gateway_requests_total{status=~"5.."}[3m]))`).

**The new Postgres pod came up empty — why?**

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec $NEW_POD -- psql ... -c '\dt'
Did not find any relations.
```

Because the Postgres Deployment had **no PersistentVolumeClaim** — the database lived on the pod's ephemeral
container filesystem, so deleting the pod destroyed all data. To eliminate this failure mode entirely, attach a
PVC so the data directory survives pod restarts (done in the Bonus).

---

## Bonus Task — Persistent Storage + Automated Backup CronJob

### B.1 — PVC added to Postgres, data now survives pod restart

`k8s/postgres.yaml` diff (added on top of the plain Deployment):

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
+    type: Recreate          # RWO volume can't be mounted by two pods at once
   ...
     containers:
       - name: postgres
         image: postgres:17-alpine
         env:
           ...
+          - name: PGDATA
+            value: /var/lib/postgresql/data/pgdata   # subdir — avoid lost+found
+        volumeMounts:
+          - name: data
+            mountPath: /var/lib/postgresql/data
+      volumes:
+        - name: data
+          persistentVolumeClaim:
+            claimName: postgres-data
```

**Re-run of the disaster test — this time the data is on the PV, so no restore step is needed:**

```console
orders before: 24
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl delete pod -l app=postgres --grace-period=0 --force   # 16:52:13
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl wait --for=condition=Ready pod -l app=postgres         # 16:52:23
# data survived, no pg_restore:
 events = 5 | orders = 24
```

**New RTO ≈ 10 seconds** (pod reschedule only) vs **~43 s** with the restore step — and the **RPO gap is now 0**
(the volume keeps every committed transaction across the restart). RTO improved ~4× and the "empty pod" failure
mode is gone.

### B.2 — Automated backup CronJob with rotation

`k8s/backup-cronjob.yaml` — runs `pg_dump -Fc` every 5 min to the `postgres-backups` PVC, keeps the 5 newest
dumps, `concurrencyPolicy: Forbid`, history limits 3/3. Retention one-liner:
`ls -1t quickticket_*.dump | tail -n +6 | xargs -r rm -v`.

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl create job --from=cronjob/postgres-backup manual-1
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl logs job/manual-1
dumping to /backups/quickticket_20260709T135318Z.dump
backup done: -rw-r--r-- 1 root root 5.3K /backups/quickticket_20260709T135318Z.dump
```

Ran 7 backups total, then checked retention — **manual-7 log shows the rotation deleting the oldest**:

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl logs job/manual-7
dumping to /backups/quickticket_20260709T135357Z.dump
backup done: -rw-r--r-- 1 root root 5.3K /backups/quickticket_20260709T135357Z.dump
removed 'quickticket_20260709T135336Z.dump'
```

**Exactly 5 files remain after 7 runs:**

```console
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec deployment/backup-inspector -- ls -1 /backups
quickticket_20260709T135340Z.dump
quickticket_20260709T135344Z.dump
quickticket_20260709T135348Z.dump
quickticket_20260709T135353Z.dump
quickticket_20260709T135357Z.dump
MacBook-Pro-pon4ik:SRE-Intro rolanmulukin$ kubectl exec deployment/backup-inspector -- sh -c 'ls -1 /backups/quickticket_*.dump | wc -l'
5
```

---

## Summary

| Task | Result |
|------|--------|
| Task 1 — Migration + backup/restore | ✅ Alembic baseline+email migration ran in 0.216s under load with **0** new 5xx; valid `pg_dump`; DROP→restore recovered 5 events / 50 orders (`/events` 502→200) |
| Task 2 — Disaster recovery (no PVC) | ✅ RTO ≈ **43s**, new pod came up **empty** (no PVC), restored from dump; ~192 5xx during the outage |
| Bonus — PVC + backup CronJob | ✅ PVC → data survives restart, RTO ≈ **10s** (RPO gap 0); CronJob backs up every 5 min, retention keeps exactly 5 of 7 dumps |
