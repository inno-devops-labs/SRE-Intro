# Lab 9 — Stateful services and database reliability

Date: 2026-10-12; author: Walkerino. Isolated `k3d-quickticket-lab78`,
Kubernetes v1.33.6-k3s1, PostgreSQL 17, Alembic 1.18.4, SQLAlchemy 2.0.49,
psycopg2 2.9.11 and host Python 3.12. Timestamps below are UTC (Moscow +3h).
The lab's dependency pins work with Python 3.12; system Python 3.9 did not.
No ArgoCD self-heal runs in this experiment cluster.

## Task 1 — Migration under load and restore

[Runner](../scripts/run-lab9.py), [full command/output transcript](evidence/lab9/session.txt),
[measured results](evidence/lab9/results.json). Independent checkout includes
previous-lab infrastructure; prior lab submissions are excluded from this PR.
Two mixedload pods continuously perform listing, reserve and simulated pay
against dedicated stocked event 7808. Six events = five course seeds + that
synthetic event. The data includes earlier exercise orders; it is not a fresh
zero-order database.

```text
$ alembic stamp 0001
$ alembic upgrade head
INFO Running upgrade 0001 -> 0002, Add optional email without backfill or table rewrite.
$ alembic history
0001 -> 0002 (head), Add optional email without backfill or table rewrite.
<base> -> 0001, Baseline of existing app/seed.sql schema; stamp, do not create tables.
```

Whole Alembic CLI elapsed time: **0.300s**, measured with
monotonic time (includes Python startup and connection). `\d events` shows:

```text
email | character varying(255) | | |
```

Prometheus `sum(increase(gateway_requests_total{status=~"5.."}[1m]))`:
**0 before, 0 after**. Raw responses: [before](evidence/lab9/migration-before.json),
[after](evidence/lab9/migration-after.json). Both are successful, nonempty query
results. Mixedload was running throughout; 35s separates snapshots, so the 1m
windows overlap. This short observation does not establish long-term availability.
Adding a nullable column avoids a table rewrite, but ALTER TABLE still acquires
ACCESS EXCLUSIVE briefly. The migration bounds lock waiting to 2s; retry after
investigating long transactions rather than allowing unbounded queuing.
[PostgreSQL lock semantics](https://www.postgresql.org/docs/17/sql-altertable.html).

Backup: `pg_dump -U quickticket -Fc quickticket`, **56,288 bytes**,
file identifies PostgreSQL custom format. `pg_restore --list` reports 18 TOC
entries, including events, orders, sequences, constraints and alembic_version.
The transcript contains `ls -lh`, `file` and the full TOC. Archive copied via
`kubectl cp`, restored with `--exit-on-error --clean --if-exists`.

| Phase | Events | Orders |
|---|---:|---:|
| Exact archive snapshot (restored separately for verification) | 6 | 1388 |
| Immediately before DROP | 6 | 1402 |
| After DROP TABLE orders CASCADE | 6 | table absent |
| After restore, live observation | 6 | 1390 |

The live post-dump count was 1390; it is not the snapshot's
count because writers continued. [Isolated archive count](evidence/lab9/archive-counts.txt)
confirms 1388 orders. Requests after restore can add new rows;
this explains the live observation rather than claiming those writes were backed up.
After DROP, `/events` returned **502** (the query joins orders). After restoring
and restarting events to clear stale/aborted pooled connections, `/events`
returned 200, verified by the runner before proceeding.

**RPO:** a single dump loses everything committed after its consistent snapshot.
Here the backup-start timestamp precedes disaster by
**20.014s**; snapshot acquisition is shortly after
that timestamp. No fixed future RPO is guaranteed with a one-off dump. Scheduled
backups bound nominal age to about five minutes plus execution/delay; monitor
last successful completion. WAL archiving and PITR with external storage reduce
loss further, while restore drills verify recoverability.

## Task 2 — Pod loss under checkout traffic

| Phase | UTC timestamp |
|---|---|
| Disaster initiated | 2026-10-12T07:31:37.277268+00:00 |
| Replacement DB ready | 2026-10-12T07:31:44.124325+00:00 |
| Archive restored | 2026-10-12T07:31:44.516390+00:00 |
| Events restarted and API 200 | 2026-10-12T07:31:49.256641+00:00 |

**Observed RTO: 11.980s**, including CLI and readiness observation.
Before disaster **1537 orders**; archive **1388**:
**149 previously committed orders lost**. Live count after recovery
was 1441 because resumed mixedload added orders; subtracting it from
the pre-disaster count would underestimate loss. Empty replacement output was
`|` for `to_regclass(events), to_regclass(orders)`: both NULL. The old manifest
used emptyDir, whose lifetime is the pod's lifetime.

[Post-disaster Prometheus query](evidence/lab9/disaster-after.json) records the
incident error increase. [Error-rate curve](evidence/lab9/disaster-error-range.json) supplements this
single query, which alone is not a time curve. Removing the pod destroys its
emptyDir; mount a durable PVC to eliminate that particular failure mode.

## Bonus — PVC and automated backups

[Postgres manifest](../k8s/postgres.yaml) replaces emptyDir with the 1Gi
`postgres-data` ReadWriteOnce claim; PGDATA is a subdirectory to avoid filesystem
root contents. Recreate strategy avoids two PostgreSQL processes on one volume.
A fresh PVC was populated from the verified dump before the second drill.

| PVC drill | Observed |
|---|---|
| Disaster | 2026-10-12T07:32:13.210671+00:00 |
| API recovered | 2026-10-12T07:32:23.081662+00:00 |
| RTO | 9.872s |
| Events before / after | 6 / 6 |
| Orders before / after | 1388 / 1388 |

No pg_restore was needed after the PVC-backed restart. RTO improved only modestly
(11.98s → 9.87s) because this small archive
restores quickly; do not extrapolate a minute-long baseline that was not measured.
The improvement is chiefly elimination of data loss and manual restore.

[Student-written CronJob](../k8s/backup-cronjob.yaml): every five minutes,
Forbid overlap, postgres:17-alpine, required PG env, UTC filename, custom format,
backup PVC, OnFailure and history limits 3/3. A partial dump is validated before
atomic rename, then only the five newest complete archives are retained.
Seven sequential manual Jobs completed. The final Job logged:

```text
created quickticket_20261012T073250Z.dump
removed 'quickticket_20261012T073234Z.dump'
```

Inspector listing showed **exactly five dumps** with timestamps 073237, 073241,
073244, 073247 and 073250; each 56,288 bytes. Full listing and Job logs are in
[session.txt](evidence/lab9/session.txt). Local-path PVCs protect against pod loss,
not host/disk loss; off-cluster backup storage is still needed.

## Reproduce and validation

Use a disposable cluster. Bootstrap application/monitoring with
`LAB9_EPHEMERAL=1 scripts/bootstrap-lab7.sh` to start the nonpersistent drill;
the final committed manifest is deliberately persistent., seed once, create the Python environment from
`requirements-migrations.txt`, and port-forward DB 15432, Prometheus 19090,
gateway 18080. Gateway forward is for smoke probes only; workload is in-cluster.
Set DATABASE_URL to override the demo Alembic connection. Run the lab runner
with your environment's Python, it resolves Alembic from the same environment (or ALEMBIC_BIN).
Repeated runs require a deliberate baseline reset of this disposable database.

The first attempt's stdin archive transfer was truncated after pod replacement;
[original failed attempt](evidence/lab9/first-attempt.txt) is retained. Switched to
kubectl cp and repeated the complete drill successfully. Do not accept a TOC
check alone: full `pg_restore --exit-on-error` and row verification also matter.

- [x] Task 1: baseline, nullable migration under load, verified backup and restore.
- [x] Task 2: ephemeral pod disaster, real RTO and snapshot RPO accounting.
- [x] Bonus: PVC survival, timed restart, seven Jobs and five-file retention.
