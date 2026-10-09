# Lab 9 — Stateful Services and DB Reliability

All timestamps below are UTC on 2026-10-09. The experiments ran against the existing `k3d-quickticket` cluster with PostgreSQL 17.11, QuickTicket, and in-cluster Prometheus. The database initially had no relations after the cluster started, so `app/seed.sql` created `events` and `orders` and inserted five events. A separate event 6 with 100,000 tickets kept checkout traffic active; `labs/lab8/mixedload.yaml` was changed to accept an `EVENT_ID` environment variable and was run with `EVENT_ID=6`.

## Task 1 — Alembic migration and backup/restore

### Baseline and migration under load

The local Python environment used Alembic 1.18.4, SQLAlchemy 2.0.49, and psycopg2-binary 2.9.11. PostgreSQL was exposed locally with `kubectl port-forward svc/postgres 5432:5432`. The baseline revision is deliberately empty because `app/seed.sql` had already created the tables. It was stamped **before** the email revision was created:

```text
$ alembic stamp head
Running stamp_revision  -> 0bd68c000e8a
$ alembic current
0bd68c000e8a (head)
$ alembic history
0bd68c000e8a -> ddd061115f64 (head), add email column to events
<base> -> 0bd68c000e8a, baseline pre-existing schema
```

The second revision adds nullable `email VARCHAR(255)` to `events`; its downgrade removes the column. Before applying it, `mixedload` was 2/2 and the database had 230 orders, confirming active checkout traffic. Prometheus returned zero gateway 5xx responses in the preceding minute:

```text
2026-10-09T18:53:59Z
mixedload  2/2
orders: 230
sum(increase(gateway_requests_total{status=~"5.."}[1m])) = 0

$ /usr/bin/time -p .venv/bin/alembic upgrade head
Running upgrade 0bd68c000e8a -> ddd061115f64, add email column to events
real 0.57
user 0.28
sys 0.07

2026-10-09T18:54:00Z
$ alembic current
ddd061115f64 (head)
sum(increase(gateway_requests_total{status=~"5.."}[1m])) = 0
```

`\d events` showed `email | character varying(255) | nullable` alongside the original columns. The 0.57-second elapsed time includes Alembic startup and connection overhead. Adding a nullable column without a default avoids a table rewrite, although PostgreSQL still needs a short exclusive DDL lock. The observed 5xx measurements show no additional errors in this workload; they do not prove that every possible workload is unaffected.

### Custom-format dump and table recovery

A host-side dump was made while traffic was running:

```bash
kubectl exec deployment/postgres -- pg_dump -U quickticket -Fc quickticket > /tmp/quickticket_lab9.dump
```

```text
Archive created at 2026-10-09 18:54:16 UTC
-rw-r--r--  1 kuji  wheel  17K  /tmp/quickticket_lab9.dump
PostgreSQL custom database dump - v1.16-0
TOC Entries: 18; Format: CUSTOM; Dumped from PostgreSQL 17.11
TABLE public alembic_version
TABLE public events
TABLE public orders
TABLE DATA public alembic_version
TABLE DATA public events
TABLE DATA public orders
```

The table-loss test used `DROP TABLE orders CASCADE`. `GET /events` queries `orders` through a join, so the gateway returned HTTP 502 until recovery. The workload was stopped after the failure was observed to keep the restore deterministic, then resumed after recovery.

| Stage | Events | Orders | API observation |
|---|---:|---:|---|
| Before drop, 18:54:37 | 6 | 442 | Running workload |
| After drop | 6 | Table absent from `\dt` | `GET /events`: HTTP 502 |
| After `pg_restore --clean --if-exists --exit-on-error` | 6 | 326 | `GET /events`: HTTP 200 after workload resumed |

The restored count matches the earlier dump snapshot. The 116 orders created between the snapshot and the drop were not in that backup. A single `pg_dump` has no fixed worst-case RPO if backups are not scheduled: all writes since the last dump can be lost. More frequent, verified backups reduce this window; durable storage also prevents this particular pod-replacement loss.

## Task 2 — Pod disaster without persistent storage

The original PostgreSQL Deployment had no `volumeMounts` or PVC. The verified dump remained on the host. `mixedload` stayed at 2/2 during the experiment.

| Phase | UTC time | Observation |
|---|---|---|
| Last pre-disaster count | 18:55:32 | 706 orders; `GET /events` HTTP 200 |
| Forced pod deletion started | 18:55:43 | `postgres-745bb5ff86-pnhsd` deleted |
| Replacement pod reported Ready | 18:55:45 | `postgres-745bb5ff86-69lwb`; socket was not yet accepting connections |
| Database accepted connections | 18:56:01 | `\dt`: no relations; API probe had returned HTTP 504 |
| Dump restore committed | 18:56:02 | 6 events, 326 orders, Alembic revision restored |
| First successful API probe | 18:56:02 | `GET /events`: HTTP 200 |

**Observed RTO:** 18:56:02 − 18:55:43 = **19 seconds**, measured to an API response rather than pod readiness. The original pod had no database readiness probe; its Kubernetes Ready transition preceded actual database availability.

The dump was created at 18:54:16, 87 seconds before pod deletion. This is the observed **time RPO** for the restore point. At least **380 orders** were lost relative to the last count (706 − 326). More orders may have been written between the 18:55:32 count and the 18:55:43 deletion; that unmeasured interval makes 380 a lower bound, not an exact total at the instant of failure. A post-restore query restricted to `created_at <= '2026-10-09T18:55:43Z'` returned 326.

Prometheus range query: `sum(rate(gateway_requests_total{status=~"5.."}[30s]))`, sampled every five seconds around the incident:

| UTC | Gateway 5xx/s |
|---|---:|
| 18:55:40 | 0 |
| 18:55:50 | 0.2000 |
| 18:56:00 | 1.0400 |
| 18:56:05 | 1.2400 |
| 18:56:20 | 0.7191 |
| 18:56:30 | 0 |

The rate remains elevated briefly after service recovery because the query uses a rolling 30-second window. The replacement pod was empty because the original PostgreSQL data directory lived in ephemeral container storage. Recreating that pod did not retain its database files.

## Bonus — PVC and automated backups

### Persistent PostgreSQL data

`k8s/postgres.yaml` adds a 1 Gi `ReadWriteOnce` claim named `postgres-data`, mounts it at `/var/lib/postgresql/data`, and sets `PGDATA=/var/lib/postgresql/data/pgdata`. The Deployment uses `Recreate` so old and new PostgreSQL processes do not serve the same RWO volume concurrently. A `pg_isready` readiness probe prevents a newly initialized pod from reporting Ready before PostgreSQL accepts connections.

The first PVC was empty, so adding the claim alone did not preserve the old database. Checkout traffic was paused, a fresh host-side custom dump was created, and the PVC Deployment was applied. The new pod showed `Did not find any relations.` before restore. The dump restored six events, the `email` column, Alembic revision `ddd061115f64`, and 724 orders. The pre-dump count was 722; two in-flight writes entered the dump before the workload fully stopped. The API then returned HTTP 200.

The second pod-deletion test required **no `pg_restore`**:

| Phase | UTC time | Observation |
|---|---|---|
| Precheck | 18:58:28 | 814 orders; order `f8c0ff38-ca84-4517-905a-e16d913fc561` present |
| Forced pod deletion started | 18:58:38 | PVC-backed pod deleted |
| Replacement pod Ready | 18:58:39 | `postgres-6fb69f6c48-vs9h5` |
| First successful API probe | 18:58:53 | `GET /events`: HTTP 200 |
| Postcheck | 18:58:53 | 874 orders, same order still present, Alembic revision intact |

**PVC RTO:** 15 seconds to the API response, four seconds shorter than the no-PVC test (19 seconds). There was no restore step and no loss of the checked order. The order count increased because `mixedload` kept writing after recovery. This is a single-node k3d `local-path` PVC: it protects against a pod replacement on this node, not against loss of the node or its underlying disk.

### Scheduled custom-format backups and retention

The supplied `labs/lab9/backup-storage.yaml` created a Bound 1 Gi `postgres-backups` PVC and a Ready `backup-inspector` Deployment. `k8s/backup-cronjob.yaml` is the scheduled backup definition:

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
              command:
                - /bin/sh
                - -ec
                - |
                  umask 077
                  timestamp=$(date -u +%Y%m%dT%H%M%SZ)
                  while [ -e "/backups/quickticket_${timestamp}.dump" ]; do
                    sleep 1
                    timestamp=$(date -u +%Y%m%dT%H%M%SZ)
                  done
                  final="/backups/quickticket_${timestamp}.dump"
                  temporary="${final}.tmp"
                  trap 'rm -f "$temporary"' EXIT
                  pg_dump -Fc -f "$temporary"
                  mv "$temporary" "$final"
                  echo "created $final"
                  ls -1t /backups/quickticket_*.dump | tail -n +6 | while IFS= read -r old; do
                    rm -- "$old"
                    echo "removed $old"
                  done
              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

Seven sequential jobs were created with `kubectl create job --from=cronjob/postgres-backup manual-N` and each reached `Complete`. The scheduled run was temporarily suspended during this exact-count test, then re-enabled. File counts after jobs 1–7 were **1, 2, 3, 4, 5, 5, 5**. Rotation logs included:

```text
manual-6: created /backups/quickticket_20261009T185944Z.dump
manual-6: removed /backups/quickticket_20261009T185926Z.dump
manual-7: created /backups/quickticket_20261009T185948Z.dump
manual-7: removed /backups/quickticket_20261009T185930Z.dump
```

At 18:59:51, `kubectl exec deployment/backup-inspector -- ls -la /backups` showed exactly these five dumps:

```text
quickticket_20261009T185934Z.dump  45641 bytes
quickticket_20261009T185938Z.dump  46359 bytes
quickticket_20261009T185941Z.dump  47010 bytes
quickticket_20261009T185944Z.dump  47679 bytes
quickticket_20261009T185948Z.dump  48339 bytes
```

The newest dump began with the custom-format magic bytes `PGDMP`. After the schedule was enabled, the 19:00 UTC job completed, created `quickticket_20261009T190018Z.dump`, removed the oldest remaining dump, and left five files. `pg_restore --list` read that scheduled dump successfully and listed 18 TOC entries, including the `alembic_version`, `events`, and `orders` tables and their data. The final database check showed six events, 1,508 orders, and Alembic revision `ddd061115f64`; the test load Deployment was removed.

With successful backups every five minutes, the normal backup-only RPO is approximately five minutes plus scheduling and backup duration. The backup PVC uses the same local cluster storage, so a node or disk loss could affect both data and backups. Off-node copies and periodic restore drills would address that remaining risk.

## Reproduction and environment note

```bash
python3 -m venv .venv
.venv/bin/python -m pip install alembic==1.18.4 psycopg2-binary==2.9.11 sqlalchemy==2.0.49
kubectl port-forward svc/postgres 5432:5432
.venv/bin/alembic history
.venv/bin/alembic current
kubectl apply -f k8s/postgres.yaml
kubectl apply -f labs/lab9/backup-storage.yaml
kubectl apply -f k8s/backup-cronjob.yaml
```

For a new database, create the `events` and `orders` schema from `app/seed.sql`, stamp the baseline revision `0bd68c000e8a`, then upgrade to head. Do not stamp head against a database that lacks the `email` column. The existing ArgoCD Application currently reports a comparison error because its remote source branch lacks a `k8s` path; the local manifest changes were applied directly to the cluster for this experiment.
