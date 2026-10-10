# Lab 9 - Stateful Services & DB Reliability

## Goal

Run a versioned schema migration under live traffic, validate a logical PostgreSQL backup and restore cycle, measure recovery objectives, and add persistent storage plus automated backups with retention.

## Environment

- Kubernetes cluster: k3d `quickticket`
- PostgreSQL: `postgres:17-alpine`
- Load: Lab 8 `mixedload` deployment
- Migration tool: Alembic `1.16.5`
- Database state before the lab: `events=5`, `orders=0`

## Task 1 - Migration and Backup/Restore

### Alembic history

```text
8a9b76d6da43 -> 6e6ae2926350 (head), add email column to events
<base> -> 8a9b76d6da43, baseline - pre-existing schema
```

The existing schema was first recorded with a baseline revision. The migration then added a nullable `events.email` column, which is a metadata-only PostgreSQL change and does not require rewriting existing rows.

### Migration under load

```text
mixedload: 2/2 replicas available
5xx before: 0
alembic upgrade head: 0.876s elapsed
5xx after: 0
alembic current: 6e6ae2926350 (head)
```

The resulting schema contained:

```text
email | character varying(255) | nullable
```

The migration completed without an additional 5xx response increase while the checkout load was active.

### Logical backup and restore

The database was backed up in PostgreSQL custom format with `pg_dump -Fc`. Before the simulated loss:

```text
events=5
orders=50
```

After dropping `orders`, the gateway returned:

```text
/events=502
```

The backup was restored with `pg_restore --clean --if-exists`. After restore:

```text
events=5
orders=50
/events=200
```

The first API check during the events deployment restart returned a transient `504`; direct events and gateway checks then returned `200`, and a clean `events` rollout completed successfully.

### RPO answer

With one manual `pg_dump`, the RPO is the time between the backup and the incident. In this run the backup was taken immediately before the destructive test, so the observed record gap was zero. In production, this setup would lose every write after the last dump. More frequent scheduled dumps reduce the window, while WAL archiving/PITR is needed for recovery to an exact point in time.

## Task 2 - Disaster Recovery Without PVC

The original PostgreSQL Deployment used ephemeral Pod storage. The Pod was deleted while the database contained 50 orders:

```text
disaster_at=2026-09-26T19:21:32+03:00
new_postgres_ready_at=2026-09-26T19:21:45+03:00
new Pod: Did not find any relations.
restored_at=2026-09-26T19:22:49+03:00
app_ready_at=2026-09-26T19:23:08+03:00
RTO_seconds=96
```

After restoring the backup and restarting `events`:

```text
events_after_restore=5
orders_after_restore=50
/events=200
```

The new Pod was empty because the Deployment had no PVC. The observed RPO gap was zero records because the backup was taken immediately before the disaster test; the measured RTO was 96 seconds, including restore and application reconnection.

## Bonus - PVC and Automated Backup

### Persistent storage

`k8s/postgres.yaml` now defines:

- `postgres-data` PVC, 1 GiB, `ReadWriteOnce`;
- `PGDATA=/var/lib/postgresql/data/pgdata`;
- a Postgres volume mount at `/var/lib/postgresql/data`.

The fresh PVC was seeded and the Alembic migration was applied. The PVC status became `Bound` and `events.email` was present.

The Postgres restart test produced:

```text
postgres_restart_at=2026-09-26T19:43:14+03:00
postgres_ready_at=2026-09-26T19:43:26+03:00
PVC_RTO_seconds=12
events_after_restart=5
orders_after_restart=0
```

No `pg_restore` was needed after the restart. Compared with the 96-second ephemeral-storage recovery, the PVC reduced recovery to Pod restart time and preserved the data.

### Automated backup CronJob

`k8s/backup-cronjob.yaml` defines a PostgreSQL custom-format backup CronJob with:

- schedule `*/5 * * * *`;
- `concurrencyPolicy: Forbid`;
- `postgres:17-alpine` and PostgreSQL environment variables;
- dumps written to the `postgres-backups` PVC;
- retention of the five newest dumps;
- successful and failed Job history limits of `3`.

The first manual run created a dump successfully. After seven manual runs, the inspector showed exactly five files:

```text
1 /backups/quickticket_20260926T164408Z.dump
2 /backups/quickticket_20260926T164403Z.dump
3 /backups/quickticket_20260926T164357Z.dump
4 /backups/quickticket_20260926T164352Z.dump
5 /backups/quickticket_20260926T164346Z.dump
```

The `manual-7` log proved rotation:

```text
created /backups/quickticket_20260926T164408Z.dump
removed '/backups/quickticket_20260926T164340Z.dump'
```

## Artifacts

- `alembic.ini`
- `migrations/versions/8a9b76d6da43_baseline_pre_existing_schema.py`
- `migrations/versions/6e6ae2926350_add_email_column_to_events.py`
- `k8s/postgres.yaml`
- `k8s/backup-cronjob.yaml`
- `submissions/lab9.md`
- `labs/lab9/backup-storage.yaml` (provided backup PVC and inspector)

Screenshots were not required; CLI output, PostgreSQL schema, Kubernetes status, restore results, and CronJob logs are the primary evidence.

## Checklist

- [x] Task 1 done - Alembic migration under load and pg_dump/pg_restore cycle
- [x] Task 2 done - disaster recovery RTO/RPO measurement
- [x] Bonus Task done - PVC and automated CronJob backup with rotation
- [x] Title is clear (`feat(labN): <topic>` style)
- [x] No secrets or large temporary files committed
- [x] Submission file at `submissions/lab9.md` exists
