# Lab 9 — Stateful Services & DB Reliability

## Environment and precautions

All experiment timestamps are UTC on 2026-10-10 (Moscow is UTC+3). The local `k3d-quickticket` cluster has five gateway Rollout replicas and the Lab 7 in-cluster Prometheus. Mixed checkout load uses the Lab 8 generator with two replicas and dedicated event 6, avoiding exhaustion of demo inventory.

Before testing, PostgreSQL had 6 events and 1870 orders from earlier labs, with no PVC or data volume. I kept this history rather than re-seeding. A full safety dump was saved to `/tmp/lab9-safety.dump` before destructive tests. ArgoCD auto-sync remains disabled as in Labs 7/8 so older fork-main manifests cannot overwrite the local experiment.

## Task 1 — Alembic migration under load

The system Python was 3.9 and could not install the specified Alembic version. Python 3.12.13 worked with the exact requested packages:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r migrations/requirements.txt
kubectl port-forward svc/postgres 15432:5432
kubectl apply -f loadgen/lab8-mixedload.yaml
kubectl rollout status deployment/mixedload --timeout=60s
.venv/bin/alembic stamp 0001_baseline
.venv/bin/alembic current
.venv/bin/alembic history
/usr/bin/time -p .venv/bin/alembic upgrade head
```

Port 15432 avoids interfering with other host databases. `DATABASE_URL` can override the demo URL in `alembic.ini`. The empty baseline represents the existing `app/seed.sql` schema; it is not a fresh-database initializer.

History output:

```text
0001_baseline -> 0002_events_email (head), Add an optional events contact email without changing existing API contracts.
<base> -> 0001_baseline, Baseline the pre-existing schema from app/seed.sql.
```

The second revision adds `events.email`, `String(255)`, nullable, with no default. Downgrade drops that column. Application requests and responses are unchanged. The migration runner sets lock_timeout=3s and statement_timeout=30s. Metadata-only does not mean lock-free: ALTER TABLE normally takes ACCESS EXCLUSIVE, so long transactions can block it. [PostgreSQL 17 ALTER TABLE documentation](https://www.postgresql.org/docs/17/sql-altertable.html).

Measured at 13:58:10–13:58:11:

```text
Running upgrade 0001_baseline -> 0002_events_email
real 0.43
user 0.27
sys 0.07
alembic current: 0002_events_email (head)
```

`psql '\d events'` output:

```text
Column        | Type                     | Nullable | Default
id            | integer                  | not null | nextval('events_id_seq'::regclass)
name          | text                     | not null |
venue         | text                     | not null |
event_date    | timestamp with time zone | not null |
total_tickets | integer                  | not null |
price_cents   | integer                  | not null |
email         | character varying(255)   |          |
Indexes: events_pkey PRIMARY KEY, btree (id)
Referenced by: orders.orders_event_id_fkey
```

Prometheus query and actual values:

```promql
sum(increase(gateway_requests_total{status=~"5.."}[1m])) or vector(0)
```

```text
13:58:10 before migration: [1791640690.947, "0"]
13:58:12 immediate after: [1791640692.073, "0"]
13:59:48 after an additional rate window: [1791640788.996, "0"]
```

Mixedload stayed at two replicas; read, reserve and pay status rates were all HTTP 200. No additional 5xx were observed. Separately, on an isolated restored database, downgrade removed email (column count 0), upgrade restored it (count 1), and a second upgrade was a no-op. Its 2942 orders were preserved. The isolated test database was then removed.

## Task 1 — Backup, DROP and restore

```bash
kubectl exec deployment/postgres -- pg_dump -U quickticket -Fc quickticket > /tmp/quickticket.dump
ls -lh /tmp/quickticket.dump
file /tmp/quickticket.dump
kubectl cp /tmp/quickticket.dump <postgres-pod>:/tmp/backup.dump
kubectl exec deployment/postgres -- pg_restore --list /tmp/backup.dump
```

Observed output:

```text
-rw-r--r--  1 avlaptev wheel 98K Oct 10 16:59 /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
Archive created at 2026-10-10 13:59:49 UTC
dbname: quickticket; TOC Entries: 18; Format: CUSTOM; Compression: gzip
Dumped from/by PostgreSQL 17.11
TABLE public alembic_version
TABLE public events
SEQUENCE public events_id_seq
TABLE public orders
TABLE DATA public alembic_version
TABLE DATA public events
TABLE DATA public orders
SEQUENCE SET public events_id_seq
CONSTRAINT public alembic_version alembic_version_pkc
CONSTRAINT public events events_pkey
CONSTRAINT public orders orders_pkey
FK CONSTRAINT public orders orders_event_id_fkey
```

Restoring this archive to a temporary verification database showed exactly 6 events and 2559 orders. Its latest order timestamp was 13:59:49.093751. This isolates the backup's row count from continued live writes.

```bash
kubectl exec deployment/postgres -- psql -U quickticket -d quickticket -c 'DROP TABLE orders CASCADE'
# Probe /events from the running mixedload pod, then restore:
kubectl exec deployment/postgres -- pg_restore -U quickticket -d quickticket \
  --clean --if-exists --single-transaction --exit-on-error /tmp/backup.dump
```

| Stage | UTC | Events | Orders | GET /events |
|---|---|---:|---:|---|
| Before DROP | 14:00:19 | 6 | 2650 | Serving before injection |
| After DROP | 14:00:19 | 6 | Table absent (`to_regclass` NULL) | 502, Events service unavailable |
| After full restore | 14:00:37 | 6 | 2559 | 200, 0.116828s |

The restore also restored `0002_events_email` in alembic_version. Restoring a historical snapshot intentionally rolls back writes after that snapshot; the 91-order gap here is not a failed restore. The original earlier-lab history remains included. Mixedload stayed running during DROP and restore.

**RPO:** A single pg_dump loses writes after its consistent snapshot. RPO is its age at the disaster, not zero merely because the file is valid. Improve it with monitored periodic off-cluster backups, restore drills, and base backups plus WAL archiving/PITR for finer recovery points. PVC protects against pod recreation, not accidental DROP or loss of the storage node. [PostgreSQL PITR documentation](https://www.postgresql.org/docs/17/continuous-archiving.html).

## Task 2 — Recovery without persistent storage

Mixedload remained at two replicas throughout. I deleted the exact inspected PostgreSQL pod with a one-second grace period, rather than force-deleting an object while its old process might still run. The same deletion command is used for the PVC tests.

```bash
kubectl delete pod <inspected-postgres-pod> --grace-period=1 --wait=true
# Find the replacement by its new UID and wait until Ready.
kubectl exec <new-pod> -- psql -U quickticket -d quickticket -c '\dt'
kubectl cp /tmp/quickticket.dump <new-pod>:/tmp/backup.dump
kubectl exec <new-pod> -- pg_restore -U quickticket -d quickticket \
  --clean --if-exists --single-transaction --exit-on-error /tmp/backup.dump
kubectl rollout restart deployment/events
kubectl rollout status deployment/events --timeout=90s
# Verify /events, /health, reserve, and pay through gateway.
```

| Phase | UTC | Evidence |
|---|---|---|
| Before disaster | 14:01:28.279730 | 6 events, 2716 orders |
| Disaster requested | 14:01:28.280122 | Deleted postgres-745cf6f696-vjfvd |
| Replacement Ready and inspected | 14:01:36.575128 | postgres-745cf6f696-bl5m6; no public tables |
| Dump restored | 14:01:37.574795 | 6 events, 2559 orders |
| Events rollout complete | 14:02:09.271819 | Fresh DB connections |
| Full checkout verified | 14:02:18.685979 | Read/health/reserve/pay all 200; confirmed order |

**Verified recovery RTO: 50.406 seconds**, measured with a monotonic clock from the delete request through successful full checkout. Pod readiness alone was insufficient. This is the measured runbook-completion time; it does not mean every request failed continuously for 50 seconds. Some traffic recovered earlier after the restore, while the procedure still waited for events rollout and control requests.

**RPO:** Backup age at the delete request was approximately 99 seconds. Captured orders count gap: 2716 − 2559 = **157 records**. A synthetic `lab9-rpo-marker` inserted after the backup was present before the test and absent after restore. In-flight writes between the count and shutdown can make the actual loss slightly larger than this captured-count gap.

Prometheus completed-response error curve:

```promql
sum(rate(gateway_requests_total{status=~"5.."}[30s])) or vector(0)
```

| UTC | 5xx requests/s, 30s window |
|---|---:|
| 14:01:28 | 0 |
| 14:01:48 | 0.081490 |
| 14:02:08 | 0 |
| 14:02:29 | 0.081443 |
| 14:02:59 | 0 |
| 14:03:58 | 0 |

The 1-minute ratio initially retained the tail of the preceding DROP test; the 30-second baseline was zero. Client timeouts and in-flight requests are not all represented by a completed-response counter. Raw samples are retained in `lab9-metrics.jsonl`; do not infer a constant outage from one aggregate rate.

**Why the new pod was empty:** The original Deployment used container-local storage, with no data volume. The replacement initialized a new database. Restoring a dump recovered the schema/data but lost writes after the snapshot.

## Bonus — Persistent data and backup automation

`k8s/postgres.yaml` now mounts a 1Gi ReadWriteOnce PVC, `postgres-data`, on `/var/lib/postgresql/data`, with PGDATA pointing to its `pgdata` subdirectory. Deployment strategy is Recreate so a Deployment update does not start two database processes on the same data directory. This remains a single-instance database, not HA.

```diff
 spec:
+  strategy:
+    type: Recreate
   containers:
     - name: postgres
+      env: # in addition to existing POSTGRES_* values
+        - name: PGDATA
+          value: /var/lib/postgresql/data/pgdata
+      volumeMounts:
+        - name: data
+          mountPath: /var/lib/postgresql/data
+  volumes:
+    - name: data
+      persistentVolumeClaim:
+        claimName: postgres-data
+---
+apiVersion: v1
+kind: PersistentVolumeClaim
+metadata:
+  name: postgres-data
+spec:
+  accessModes: [ReadWriteOnce]
+  resources:
+    requests:
+      storage: 1Gi
```

For the cutover only, mixedload was stopped and allowed to terminate. A fresh full dump was taken before applying the PVC manifest and restored onto the fresh volume; **6 events and 2942 orders before and after**, including the migration state. I did not seed over the existing history. Load was then resumed at two replicas.

### First PVC trial: storage recovery is not application recovery

At 14:06:32.028164 the PVC-backed pod was deleted under load. At 14:06:34.717314 the replacement was Ready and had events/orders/alembic_version. At 14:06:35.881433, all **3228 captured order IDs** were still present; missing IDs = 0.

However, without an events restart, full checkout was only verified at 14:07:56.302745: **RTO 84.231 seconds**. Events remained process-Ready but stale database connections delayed requests. Peak sampled 5xx rate was 2.474827 requests/s. PVC fixed data loss, but did not by itself fix the connection-recovery delay. This slower result is retained, not discarded as if the first test succeeded quickly.

A second PVC trial uses the explicit events-restart step from the original recovery runbook, with no dump restore. Its measured result is recorded below.

### PVC re-test with the recovery runbook

The fresh 1-minute 5xx baseline at 14:12:28 was zero. Two mixedload replicas remained running throughout this re-test.

| Phase | UTC | Evidence |
|---|---|---|
| Before disaster | 14:12:29.526391 | 6 events, 4034 orders in count query |
| Disaster requested | 14:12:29.526684 | Deleted postgres-c8778f757-jbkz7, same 1s grace |
| Replacement Ready | 14:12:31.912123 | postgres-c8778f757-tkdxl; Ready after 2.385s |
| Existing data checked | 14:12:32.817237 | All 4035 captured IDs still present; 0 missing |
| Events rollout complete | 14:13:03.894732 | Explicit reconnection, same step as no-PVC test |
| Full checkout verified | 14:13:06.361728 | Read/health/reserve/pay 200; confirmed order |

**Verified RTO: 36.835 seconds**, versus 50.406 without PVC (~27% lower in these runs). **Observed RPO: zero captured order IDs lost**. The count query and ID capture are separate statements under live writes; one additional order committed between them, explaining 4034 versus 4035. No re-seed, migration, or pg_restore was required after the pod restart.

This is an end-to-end runbook comparison, not a statistically controlled storage benchmark. PVC eliminated empty-database initialization and dump restore; both compared runbooks explicitly restart events. The 84.231s automatic-reconnect trial demonstrates why just declaring the pod Ready would give a misleading application RTO. The two earlier Ready/inspection log records were emitted after querying relations; the repeat's Ready timestamp is captured before that inspection.

### CronJob and retention

The student-written `k8s/backup-cronjob.yaml` runs every five minutes, uses Forbid concurrency, PostgreSQL 17 clients, the provided postgres-backups PVC, and success/failure history limits of three. It writes a custom-format dump to a `.partial` path, validates the TOC, then renames it before rotating the five newest completed dumps. Failed/partial output does not count as a completed backup. Demo credentials match this course; production should use Secrets and a restricted backup role.

```bash
kubectl apply -f labs/lab9/backup-storage.yaml
kubectl apply -f k8s/backup-cronjob.yaml
# Suspend scheduled runs only while testing exactly seven manual runs:
kubectl patch cronjob postgres-backup --type=merge -p '{"spec":{"suspend":true}}'
for i in 1 2 3 4 5 6 7; do
  kubectl create job --from=cronjob/postgres-backup manual-$i
  kubectl wait --for=condition=Complete job/manual-$i --timeout=90s
done
kubectl logs job/manual-7
kubectl exec deployment/backup-inspector -- ls -la /backups
kubectl patch cronjob postgres-backup --type=merge -p '{"spec":{"suspend":false}}'
```

Actual manual-7 output:

```text
Created /backups/quickticket_20261010T140507Z.dump
removed 'quickticket_20261010T140449Z.dump'
Retained 5 dumps
```

Exactly five dump files after seven successful manual jobs:

```text
-rw------- 1 root root 114644 Oct 10 14:04 quickticket_20261010T140453Z.dump
-rw------- 1 root root 114717 Oct 10 14:04 quickticket_20261010T140457Z.dump
-rw------- 1 root root 115012 Oct 10 14:05 quickticket_20261010T140500Z.dump
-rw------- 1 root root 115375 Oct 10 14:05 quickticket_20261010T140503Z.dump
-rw------- 1 root root 115779 Oct 10 14:05 quickticket_20261010T140507Z.dump
```

After unsuspending, the controller also completed a scheduled catch-up job for the missed 14:05 run. Backups on this local-path PVC are still in the same cluster/storage failure domain. They reduce snapshot age if jobs succeed, but do not guarantee a five-minute RPO during backup failure or node loss; monitor age and copy backups off-cluster.

The next on-time scheduled job, `postgres-backup-29860690`, also completed. Its annotation was `batch.kubernetes.io/cronjob-scheduled-timestamp: 2026-10-10T14:10:00Z` and its logs showed:

```text
Created /backups/quickticket_20261010T141000Z.dump
removed 'quickticket_20261010T140457Z.dump'
Retained 5 dumps
```

That actual scheduled dump was restored into another isolated database: 6 events, 3579 orders, `0002_events_email`, and one email column. The test database was then removed. `k8s/postgres-backup-storage.yaml` mirrors the provided backup PVC under the GitOps source path, so a future sync of `k8s/` can create the CronJob's storage dependency too; the inspector remains a lab-only helper.

CronJob manifest used for the tests:

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
      backoffLimit: 2
      activeDeadlineSeconds: 240
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
              command: [sh, -ec]
              args:
                - |
                  umask 077
                  stamp=$(date -u +%Y%m%dT%H%M%SZ)
                  dump="/backups/quickticket_${stamp}.dump"
                  partial="${dump}.partial"
                  [ ! -e "$dump" ] || { echo "Backup name collision: $dump" >&2; exit 1; }
                  trap 'rm -f -- "$partial"' EXIT
                  pg_dump -Fc -f "$partial"
                  pg_restore --list "$partial" > /dev/null
                  mv "$partial" "$dump"
                  echo "Created $dump"
                  cd /backups
                  ls -1t quickticket_*.dump | tail -n +6 |
                    while IFS= read -r stale; do
                      rm -v -- "$stale"
                    done
                  echo "Retained $(ls -1 quickticket_*.dump | wc -l) dumps"
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

## Evidence, validation and cleanup

`lab9-metrics.jsonl` contains 97 timestamped Prometheus snapshots, preserving API result arrays and values. `lab9-recovery.jsonl` contains 19 recovery events across all three trials, including the slower automatic-reconnect result. Observation phases can include recovery as well as injection; timestamps above define each failure interval.

Validation passed:

- Migration upgrade under live checkout traffic, 0.43s elapsed and no added 5xx.
- Downgrade → upgrade → repeated upgrade on an isolated restored database, preserving its orders.
- Real archive TOC inspection and restores of both the manual dump and the scheduled-job dump.
- Server-side dry-runs of PostgreSQL/PVC, backup PVC and CronJob manifests.
- Python syntax validation, Alembic history/current and diff whitespace checks.

At 14:14:28, the final live-load Prometheus window had 5xx increase=0 and user error ratio=0. All faults were restored and checkout was verified before removing load. Final PostgreSQL state after writers stopped: **6 events, 4479 orders, revision 0002_events_email**, on the retained postgres-data PVC.

Cleanup removed mixedload, the CronJob and its Jobs, backup-inspector, and the temporary postgres-backups PVC. The five remaining scheduled/manual test dumps were copied to `/tmp/lab9-backups.P1cQnN/` before deleting that backup volume; a fresh final full dump is `/tmp/lab9-final.dump`. The earlier safety dump and disaster-test dump remain on the host too. These `/tmp` copies are recoverable local artifacts, not durable off-site backup storage. The Lab 9 PostgreSQL port-forward was stopped by its exact verified process identity; no unrelated database or process was stopped.

The PostgreSQL data PVC, gateway Rollout and Lab 7 monitoring are left running. ArgoCD auto-sync is still disabled until the intended cumulative manifests are merged into the fork's main. To enable backup automation again, apply `k8s/postgres-backup-storage.yaml` followed by `k8s/backup-cronjob.yaml`; the inspector is not required for normal scheduled backups.

Moodle submission is a separate final step.

## Checklist

- [x] Task 1: Alembic baseline/migration under load, valid backup, DROP/restore, counts and RPO answer.
- [x] Task 2: timed disaster recovery, empty replacement DB, measured RTO/RPO and error curve.
- [x] Bonus: persistent storage, data-survival proof, faster verified runbook recovery, manual and scheduled backups, five-file retention.
- [x] Cleanup and restored application state verified.
