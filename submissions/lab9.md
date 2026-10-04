# Lab 9 — Database Reliability, Migrations and Backups

## Environment and baseline

This work used the existing `quickticket` k3d cluster. The gateway was a five-pod Rollout and the in-cluster Prometheus pod was running. At the start, PostgreSQL had no PVC, no `volumeMounts`, and no `PGDATA` override, so its initial storage was ephemeral.

```text
Docker version 29.2.1, build a5c7197
kubectl client v1.34.1
k3d v5.9.0 (k3s v1.35.5-k3s1)
Helm v4.3.0

initial row counts: events=5, orders=4634
```

`labs/lab8/mixedload.yaml` was applied for the migration, backup and recovery exercises. It performs `/events`, reservation, and payment calls. The event inventory had already been expanded to 10,000 tickets in the preceding lab.

## Alembic migration

The committed `alembic.ini` deliberately has an empty URL. `migrations/env.py` requires `DATABASE_URL` from the process environment, so no database credential is stored in Git.

Two revisions were created:

```text
<base> -> 9a0001, baseline - pre-existing QuickTicket schema
9a0001 -> 9a0002 (head), add nullable email column to events
```

I stamped the existing schema at the baseline first, then upgraded to the email revision. I did not stamp `head`.

```text
before: alembic current = 9a0001
migration_start_utc=2026-10-04T19:12:05.8402531Z
migration_end_utc=2026-10-04T19:12:07.3038705Z
migration_elapsed_ms=1449.688
after: alembic current = 9a0002 (head)
```

The resulting `events.email` column is `character varying(255)` and nullable. Prometheus returned `0` for the observed gateway 5xx-rate query immediately before the migration. The migration was run while mixedload was active and the complete checkout was successful afterwards. Adding a nullable column completed quickly here, but `ALTER TABLE` can still take a lock; this observation is not a guarantee of lock-free migrations.

### Repeat measurement: post-migration 5xx

The original report did not retain a post-migration 5xx sample, so I ran a separate, reversible measurement. A temporary in-cluster pod made 450 real `GET /events` requests through the gateway while the database was downgraded to `9a0001` and upgraded again to `9a0002`.

```text
repeat_migration_start_utc=2026-10-04T19:56:55.9453646Z  downgrade exit=0
repeat_migration_upgrade_start_utc=2026-10-04T19:56:57.4483452Z  upgrade exit=0
repeat_migration_end_utc=2026-10-04T19:56:58.9266326Z
alembic current=9a0002 (head)
traffic output: GET_events_200=450
```

After the traffic window, Prometheus returned this real post-migration result:

```text
post_repeat_migration_5xx_query_utc=2026-10-04T19:58:27.2917954Z
sum(rate(gateway_requests_total{status=~"5.."}[1m])) = 0
```

## Verified external backup

Before destructive work I created a PostgreSQL custom-format dump inside the pod and copied it to `C:\Users\Я\AppData\Local\Temp`, outside the pod and outside this repository.

```text
backup_start_utc=2026-10-04T19:12:51.3099467Z
backup_end_utc=2026-10-04T19:12:53.0775901Z
host file: lab9-pre-drop.dump
size_bytes=209554
SHA256=D7868D9EC64717B551417B0F67F74EA75E6745BEF81CE0E7F7F01E6EC5424BC9
```

`pg_restore --list` identified a PostgreSQL 17.11 custom archive with TOC entries for `alembic_version`, `events`, and `orders`. I restored it into a separate verification database before the destructive test:

```text
verified backup: events=5, orders=5526, alembic=9a0002
```

The initial count taken just before backup was 5525 orders. The one-row difference is expected with active writers; the verification database is the snapshot-specific count.

## Drop and restore exercise

```text
drop_before_utc=2026-10-04T19:14:13.8397222Z
before DROP: events=5, orders=5984
DROP TABLE orders CASCADE
drop_after_utc=2026-10-04T19:14:15.3581738Z
to_regclass('orders') = null
```

Mixedload was still active immediately after the drop. These were actual gateway observations:

```text
GET /events     HTTP 502  {"detail":"Events service unavailable"}
POST reserve    HTTP 500  Internal Server Error
```

I then scaled mixedload to zero for an exact restore boundary. The external dump was copied back into the pod and restored with `pg_restore --clean --if-exists`.

```text
restore_start_utc=2026-10-04T19:14:25.3641206Z
restore_end_utc=2026-10-04T19:14:26.3097015Z
schema: events.email character varying(255)
alembic=9a0002
```

The observed order count after restore was 5528 because some requests completed around the workload stop/restore boundary. It is therefore not compared as an exact snapshot count; the separately restored verification database above is the exact evidence for the backup contents. Mixedload was reapplied and a checkout succeeded afterwards:

```json
{"order_id":"40e5d786-4af8-4eae-ac55-891b7c41bfe1","status":"confirmed"}
```

## Task 2 — disaster recovery under load

For a separate disaster test I created and verified a new dump outside the pod while mixedload was running.

```text
disaster_backup_start_utc=2026-10-04T19:15:30.6858242Z
disaster_backup_end_utc=2026-10-04T19:15:31.7757929Z
host file: lab9-disaster.dump
size_bytes=222524
verified backup: orders=5878, alembic=9a0002
orders before disaster=6114
T_KILL=2026-10-04T19:16:52.1262771Z
```

The old ephemeral PostgreSQL pod was deleted while mixedload continued. A replacement pod was listed quickly, but it was not yet accepting database connections:

```text
replacement seen: 2026-10-04T19:16:54.6579155Z
Kubernetes Ready state observed: 2026-10-04T19:16:54.8467765Z
first pg_restore attempt: failed because the local PostgreSQL socket was not ready
postgres accepting connections: 2026-10-04T19:17:37.9531660Z
restore retry start: 2026-10-04T19:17:37.9581652Z
restore retry complete: 2026-10-04T19:17:39.3092007Z
checkout recovered: 2026-10-04T19:17:49.5748702Z
```

Events was restarted after restore so that its pool used the new database process. The observable RTO, from `T_KILL` to a confirmed checkout, was **57.449 seconds**. During the incident the Prometheus 30-second gateway 5xx-rate query returned `3.4401568078084352`; a later `4.120032001280051` result still contained the outage in its rolling window. Gateway metrics show only requests that reached gateway, so they do not prove that no transport-level requests were lost.

The backup was 80.350 seconds old at `T_KILL`. The snapshot had 5,878 orders and the live database had 6,114 before deletion: a proven post-backup gap of 236 orders. Exact lost-record count cannot be derived from the later live count because writers remained active during recovery; snapshot age and the proven pre-disaster gap are reported separately.

## Bonus A — PVC-backed PostgreSQL

`k8s/postgres.yaml` now creates `postgres-data` (1Gi, `ReadWriteOnce`) and mounts it at `/var/lib/postgresql/data`. `PGDATA` is `/var/lib/postgresql/data/pgdata`, and the single-instance deployment uses `strategy: Recreate` so two PostgreSQL processes cannot use the same directory.

Before changing storage, a fresh verified dump was made:

```text
pvc_backup_start_utc=2026-10-04T19:19:49.7349679Z
pvc_backup_end_utc=2026-10-04T19:19:50.7034499Z
size_bytes=245646
SHA256=BC9E1D7EC474DEB2CDBF4CB742E5AB641CAADF96B6CA979F1D1102167B0F9B60
```

The PVC became Bound and this latest dump was restored into the fresh PVC-backed database:

```text
postgres-data   Bound   1Gi   RWO   local-path
pvc_restore_start_utc=2026-10-04T19:21:01.1293020Z
pvc_restore_end_utc=2026-10-04T19:21:01.9407040Z
```

Under the same active mixedload, I deleted the PVC-backed PostgreSQL pod:

```text
delete_start_utc=2026-10-04T19:21:58.7850163Z
old_pod=postgres-749857fcc6-k8qgz
replacement_seen_utc=2026-10-04T19:22:00.6660190Z
replacement_pod=postgres-749857fcc6-qjlhc
postgres_accepting_utc=2026-10-04T19:22:00.9552249Z
gateway health/events verified=2026-10-04T19:22:12.5210366Z
```

The replacement accepted PostgreSQL connections 2.170 seconds after deletion; health was verified 13.736 seconds after deletion, with no `pg_restore` required. The current post-restart order count was 6507 and the restored Alembic revision remained `9a0002`. A final real checkout using a different event with available inventory also succeeded:

```text
checkout_utc=2026-10-04T19:28:05.6767443Z
reserve HTTP 200: reservation_id=6b15c5ef-9e40-470a-9763-18e07f1a9dd0
pay HTTP 200: order_id=6b15c5ef-9e40-470a-9763-18e07f1a9dd0, status=confirmed
```

PVC storage made a pod restart recover without external restore. It does not replace backups and does not protect against loss of the PVC or node.

### Repeat measurement: PVC recovery through checkout

The first PVC test recorded database readiness and gateway health, but not a checkout immediately on that recovery path. I therefore repeated the PVC-backed pod deletion and measured through a confirmed order. The initial `kubectl exec` attempt landed while the replacement container was still being created; the recorded RTO includes that startup time and the required events pool restart.

```text
pvc_repeat_kill_utc=2026-10-04T19:59:08.1726237Z
old_pod=postgres-749857fcc6-qjlhc
orders_before=6509
replacement_seen_utc=2026-10-04T19:59:08.3949824Z
postgres_accepting_utc=2026-10-04T19:59:29.7047740Z
events_pool_restart_start_utc=2026-10-04T19:59:29.7502085Z
events_rollout_ready_utc=2026-10-04T19:59:38.9364964Z
pvc_repeat_checkout_utc=2026-10-04T19:59:40.3027687Z
RTO to successful checkout=32130.145 ms (32.130 s)
```

The reservation `74592da4-954d-4bd7-bf15-7726d9aa2623` for event 5 returned HTTP 200, followed by an HTTP 200 confirmed payment. The order count was 6510 after recovery, so the previously stored data remained present and the new order was written without restoring a dump.

## Bonus B — backup CronJob and retention

The tracked `k8s/backup-cronjob.yaml` defines a `batch/v1` CronJob with `schedule: "*/5 * * * *"`, `concurrencyPolicy: Forbid`, `OnFailure`, and history limits of three completed and three failed Jobs. It writes a custom dump to a temporary file, renames it only after `pg_dump` succeeds, then removes only completed dumps beyond the newest five.

The password is supplied by the non-tracked `postgres-backup-credentials` cluster Secret; no credential is in the manifest or report. After the retention test, the tracked CronJob was changed to `suspend: false` and reconciled by Argo CD.

Seven sequential manual Jobs were created from the CronJob:

```text
manual-1  2026-10-04T19:23:48.3688336Z -> 19:23:52.6749701Z  succeeded=1
manual-2  2026-10-04T19:23:53.7860435Z -> 19:23:58.7042065Z  succeeded=1
manual-3  2026-10-04T19:23:59.8110902Z -> 19:24:03.7318871Z  succeeded=1
manual-4  2026-10-04T19:24:04.8414837Z -> 19:24:08.7524446Z  succeeded=1
manual-5  2026-10-04T19:24:09.8709431Z -> 19:24:13.8005304Z  succeeded=1
manual-6  completed successfully
manual-7  completed successfully
```

The CronJob history limit later retained the latest three Job objects, while the logs from `manual-7` proved retention deleted an older completed dump:

```text
created /backups/quickticket_20261004T192420Z_lab9-backup-manual-7-n99cd.dump
deleting /backups/quickticket_20261004T192355Z_lab9-backup-manual-2-m8hsm.dump
```

The inspector listed exactly five 245646-byte dumps, from manual-3 through manual-7. `pg_restore --list` on `quickticket_20261004T192400Z_lab9-backup-manual-3-qrbjg.dump` reported a valid PostgreSQL 17.11 custom archive.

### Scheduled backup verification

Argo CD applied the enabled schedule at `2026-10-04T20:00:36.3463581Z`. The CronJob then created a non-manual Job; its owner was `CronJob/quickticket-backup`.

```text
suspend=false
lastScheduleTime=2026-10-04T20:00:00Z
lastSuccessfulTime=2026-10-04T20:00:37Z
scheduled_job=quickticket-backup-29852400
succeeded=1
completion=2026-10-04T20:00:37Z
```

Its logs prove that it created a dump and maintained the five-dump retention target:

```text
created /backups/quickticket_20261004T200034Z_quickticket-backup-29852400-kzlfq.dump
deleting /backups/quickticket_20261004T192400Z_lab9-backup-manual-3-qrbjg.dump
```

The inspector then reported `5` retained dump files.

## Final recovery and GitOps state

Mixedload was removed before the final Argo CD sync, and the old Lab 7 load generator is absent. Argo CD was pointed at `feature/lab9` and automation was temporarily disabled during manual data experiments. It was restored after the branch was pushed:

```text
2026-10-04T19:31:37.5478543Z
targetRevision=feature/lab9
automated.prune=True, automated.selfHeal=True
sync=Synced, health=Healthy
revision=6c675c360bdafdb75511323b8fa58bba407e477a
```

Final direct health evidence before that sync:

```text
final_health_utc=2026-10-04T19:28:28.2208577Z
gateway  HTTP 200  {"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
events   HTTP 200  {"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
payments HTTP 200  {"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

The final post-GitOps check at `2026-10-04T19:32:43.0196094Z` returned the same three HTTP 200 health responses, including `failure_rate: 0.0` and `latency_ms: 0`.

After that GitOps verification, the five-pod gateway Rollout remained available and one last checkout succeeded at `2026-10-04T19:33:55.5187013Z`: reservation `3c7aad5d-37c4-4a6e-9eed-e154b304b81f` for event 5 returned HTTP 200, and its payment confirmation returned HTTP 200.

After these supplemental measurements, the final health check at `2026-10-04T20:02:22.3097890Z` returned HTTP 200 for gateway, events, and payments. Payments reported `failure_rate: 0.0` and `latency_ms: 0`; Argo CD was `Synced/Healthy` at revision `a07207840539559edbc421ba92ae50a37137f665`.

## Limitations and acceptance checklist

- [x] Baseline, Alembic baseline stamp, nullable email migration, and downgrade-capable revision created.
- [x] Custom backups copied outside PostgreSQL pods and validated before destructive tests.
- [x] `orders` drop and restore tested; application behavior was observed on the write path.
- [x] Ephemeral disaster recovery was measured under mixedload with RTO/RPO evidence.
- [x] PostgreSQL PVC migration and pod-delete comparison completed.
- [x] Seven manual CronJob backups, five-file retention, and archive validation completed.
- [x] No dumps, virtual environment, credentials, or user `.idea` files were added to Git.

The exact number of orders lost after the disaster snapshot cannot be reconstructed because writers were deliberately left active across recovery. That limitation is why the report distinguishes the verified backup count, backup age, and pre-disaster post-backup gap.
