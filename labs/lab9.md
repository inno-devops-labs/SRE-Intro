# Lab 9 — Stateful Services & DB Reliability

![difficulty](https://img.shields.io/badge/difficulty-intermediate-yellow)
![topic](https://img.shields.io/badge/topic-DB%20Reliability-blue)
![points](https://img.shields.io/badge/points-10%2B2.5-orange)
![tech](https://img.shields.io/badge/tech-Alembic%20%2B%20pg__dump-informational)

> **Goal:** Run database migrations with Alembic under load, perform `pg_dump` backup + `pg_restore` recovery, measure RTO/RPO, and automate periodic backups with a Kubernetes CronJob.
> **Deliverable:** A PR from `feature/lab9` with `migrations/` directory and `submissions/lab9.md`. Submit PR link via Moodle.

---

## Overview

In this lab you will practice:

- Setting up Alembic for database migrations against the k3d Postgres
- Running a nullable-column migration under live traffic (zero-downtime)
- Creating a `pg_dump` backup and restoring from it after a DROP TABLE
- Measuring actual RTO/RPO by killing the Postgres pod
- Scheduling automated backups with a Kubernetes CronJob + retention

> **You do all the work.** Set up Alembic, write the migration, execute backup/restore, add automation.

---

## Project State

**You should have from previous labs:**
- QuickTicket on **k3d** (from Lab 4 onward) — postgres deployment, gateway Rollout (from Lab 7)
- In-cluster Prometheus in the `monitoring` namespace (from Lab 7 bonus)
- Seed data loaded: `events` (5 rows) and `orders` (empty) — see `app/seed.sql`

**This lab adds:**
- Alembic migration framework for schema management
- `pg_dump`/`pg_restore` backup and restore procedures
- Disaster recovery experience with real RTO/RPO numbers
- Automated backup CronJob with rotation

> ⚠️ **Before you start:** check the postgres deployment (`k8s/postgres.yaml`). If it has no `volumeMounts` + `PersistentVolumeClaim`, the DB lives on ephemeral pod storage — **any pod restart erases everything**. You will experience this firsthand in Task 2. The Bonus Task has you add a PVC.

---

## Setup

### Seed / verify data exists

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -c '\dt'

# If "Did not find any relations", re-seed:
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket < app/seed.sql
```

### Generate traffic

Apply the Lab 8 mixedload (exercises the full checkout flow so `orders` gets rows and you have realistic traffic for the migration test):

```bash
kubectl apply -f labs/lab8/mixedload.yaml
```

### Set up Python / Alembic

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install alembic==1.18.4 psycopg2-binary==2.9.11 sqlalchemy==2.0.49
```

### Port-forward Postgres for Alembic

Alembic runs from your host, Postgres is in the cluster — bridge them with a port-forward:

```bash
kubectl port-forward svc/postgres 5432:5432 &
# Verify
.venv/bin/python3 -c "import psycopg2; c=psycopg2.connect('postgresql://quickticket:quickticket@localhost:5432/quickticket'); cur=c.cursor(); cur.execute('SELECT count(*) FROM events'); print('events:', cur.fetchone()[0])"
```

---

## Task 1 — Migrations & Backup/Restore (6 pts)

**Objective:** Set up Alembic, run a migration under load, create a backup, simulate data loss, restore and verify.

### 9.1: Initialize Alembic

```bash
alembic init migrations
```

Edit `alembic.ini`:

```ini
sqlalchemy.url = postgresql://quickticket:quickticket@localhost:5432/quickticket
```

### 9.2: Baseline the existing schema

The DB already has tables (`events`, `orders`) from `seed.sql`. Tell Alembic "this state is the baseline":

```bash
# Create an empty revision to represent current state
alembic revision -m "baseline - pre-existing schema"
# Mark it as already applied
alembic stamp head
alembic current    # should show <hash> (head)
```

### 9.3: Create the real migration

```bash
alembic revision -m "add email column to events"
```

Edit the generated file in `migrations/versions/*_add_email_column_to_events.py`:

```python
def upgrade() -> None:
    # Adding a nullable column is a metadata-only change in PostgreSQL 11+ —
    # no table rewrite, no blocking lock on SELECT/INSERT. Safe under load.
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'email')
```

### 9.4: Run the migration under load

Confirm `mixedload` is still running (traffic hitting `/events`, `/reserve`, `/pay`):

```bash
kubectl get deployment mixedload
```

Record baseline error rate from Prometheus:

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))' \
  | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('5xx last 1min:', r[0]['value'][1] if r else 0)"
```

Apply the migration:

```bash
time alembic upgrade head
```

Verify the schema and re-check error rate (should be unchanged):

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -c '\d events'
```

### 9.5: Create a pg_dump backup

```bash
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  pg_dump -U quickticket -Fc quickticket > /tmp/quickticket.dump

ls -lh /tmp/quickticket.dump
file /tmp/quickticket.dump
```

Verify the contents (requires running `pg_restore --list` inside the Postgres pod — the host doesn't have the client):

```bash
POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)
kubectl cp /tmp/quickticket.dump $POD:/tmp/backup.dump
kubectl exec $POD -- pg_restore --list /tmp/backup.dump | head -25
```

### 9.6: Simulate data loss → restore

Record current counts, drop the `orders` table (cascading — will also trigger API failures), then restore:

```bash
POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)

# Before
kubectl exec $POD -- psql -U quickticket -d quickticket \
  -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'

# Drop
kubectl exec $POD -- psql -U quickticket -d quickticket -c 'DROP TABLE orders CASCADE'

# Observe API breakage
kubectl run smoke --image=curlimages/curl:latest --rm -i --restart=Never --quiet \
  --command -- curl -s -o /dev/null -w "/events=%{http_code}\n" http://gateway:8080/events

# Restore
kubectl exec $POD -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump

# Verify
kubectl exec $POD -- psql -U quickticket -d quickticket \
  -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'

kubectl run smoke --image=curlimages/curl:latest --rm -i --restart=Never --quiet \
  --command -- curl -s -o /dev/null -w "/events=%{http_code}\n" http://gateway:8080/events
```

### 9.7: Proof of work

**Commit the `migrations/` directory** to your fork.

**Paste into `submissions/lab9.md`:**

1. `alembic history` output showing the two revisions (baseline + email).
2. `\d events` output showing the new `email` column.
3. `time alembic upgrade head` output (elapsed time — expect <1s for nullable add).
4. Prometheus `5xx last 1min` before and after migration (should both be 0 or unchanged).
5. `ls -lh /tmp/quickticket.dump` + `pg_restore --list` output showing backup is valid.
6. Row counts **before disaster / after DROP / after restore** for events and orders.
7. Answer: "What's the RPO of your current setup (single `pg_dump`)? How would you improve it? (Hint: Bonus Task.)"

<details>
<summary>💡 Hints</summary>

- `alembic stamp head` tells Alembic "treat the current state as matching this revision" — use it to baseline an existing DB.
- Adding a **nullable** column is safe under load (metadata-only in PostgreSQL 11+). Adding a **NOT NULL** column without a default rewrites the whole table and locks it — a famous outage cause.
- The `-Fc` custom format is NOT human-readable. Use `pg_restore --list` to inspect contents.
- `pg_restore --clean --if-exists` drops existing objects before restoring — safe to re-run.
- Your local machine likely doesn't have `pg_restore` installed. Run it inside the Postgres pod via `kubectl exec`.
- Don't worry about the `alembic_version` table in your backup — it's how Alembic tracks which migrations are applied.

</details>

---

## Task 2 — Disaster Recovery Under Load (4 pts)

> ⏭️ This task is optional. Skipping it will not affect future labs.

**Objective:** Measure your actual RTO and RPO by killing Postgres and recovering from your backup. Then confront the real reason recovery is painful — the Postgres deployment has no persistent storage.

### 9.8: Kill Postgres and recover

Keep `mixedload` running the whole time. Record wall-clock timestamps:

```bash
# T0: record state
T0=$(date +%H:%M:%S)
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -c 'SELECT count(*) FROM orders'
echo "healthy at $T0"

# Disaster
kubectl delete pod -l app=postgres --grace-period=0 --force
T_KILL=$(date +%H:%M:%S)

# Wait for new pod to be Ready
kubectl wait --for=condition=Ready pod -l app=postgres --timeout=60s
T_READY=$(date +%H:%M:%S)

# Inspect the new pod's data
NEW_POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)
kubectl exec $NEW_POD -- psql -U quickticket -d quickticket -c '\dt'
# Observe: tables are GONE because there's no PVC!

# Restore from backup
kubectl cp /tmp/quickticket.dump $NEW_POD:/tmp/backup.dump
kubectl exec $NEW_POD -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
T_RESTORED=$(date +%H:%M:%S)

# Reconnect the events service (stale DB connections)
kubectl rollout restart deployment/events
kubectl rollout status deployment/events --timeout=30s
T_APP_READY=$(date +%H:%M:%S)

echo "
Disaster at      $T_KILL
New pod ready    $T_READY
Restored         $T_RESTORED
App fully up     $T_APP_READY
"
```

### 9.9: Calculate RTO and RPO

- **Actual RTO** = `T_APP_READY − T_KILL` (the total outage window)
- **Actual RPO** = time since your `pg_dump` backup. If the backup was taken 1 hour ago, RPO = 1 hour (everything written after the backup is **lost**).

Quantify what was lost: before disaster you had N orders. After restore you have M. Where N − M is the RPO gap in records.

**Paste into `submissions/lab9.md`:**

- Timestamps for the four phases (disaster / new pod ready / restored / app ready).
- Actual RTO value in seconds.
- Orders count before disaster vs after restore (RPO gap).
- Prometheus error-rate curve around the incident:
  ```bash
  kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
    'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B30s%5D))'
  ```
- Answer: "The new Postgres pod was empty. Why? How would you eliminate this failure mode?" (Answer: no PVC — fix it in the Bonus.)

---

## Bonus Task — Persistent Storage + Automated Backup CronJob (2.5 pts)

> 🌟 For those who want extra challenge and experience.

**Objective:** Add persistent storage to Postgres and automate periodic backups with rotation. Re-measure RTO after these changes.

### B.1: Add a PVC to Postgres

Edit `k8s/postgres.yaml`. Add a PersistentVolumeClaim and mount it on the data directory:

```yaml
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-data
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 1Gi
```

And in the Deployment pod spec:

```yaml
containers:
  - name: postgres
    image: postgres:17-alpine
    env:
      - { name: POSTGRES_DB, value: quickticket }
      - { name: POSTGRES_USER, value: quickticket }
      - { name: POSTGRES_PASSWORD, value: quickticket }
      - { name: PGDATA, value: /var/lib/postgresql/data/pgdata }  # subdir — avoid lost+found
    volumeMounts:
      - { name: data, mountPath: /var/lib/postgresql/data }
volumes:
  - name: data
    persistentVolumeClaim:
      claimName: postgres-data
```

Apply:

```bash
kubectl apply -f k8s/postgres.yaml
kubectl rollout status deployment/postgres --timeout=60s
# Re-seed once (fresh PV)
kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket < app/seed.sql
```

Now repeat the disaster test from 9.8 — this time the new pod should find its data on the PV, and the RTO drops from "minute of pg_restore" to "pod restart time (~10s)".

### B.2: Automated backup CronJob (you write this one)

The *storage* for backups is plumbing — apply the provided file:

```bash
kubectl apply -f labs/lab9/backup-storage.yaml
kubectl rollout status deployment/backup-inspector --timeout=30s
kubectl get pvc postgres-backups
```

That gives you a `postgres-backups` PVC and a `backup-inspector` pod you can `kubectl exec` into.

The *skill* is the CronJob itself — **you write it**. Create `k8s/backup-cronjob.yaml`:

```yaml
# YOUR TASK: Write a CronJob that backs up the quickticket DB on a schedule.
#
# Requirements:
#   - Runs every 5 minutes (schedule: "*/5 * * * *")
#   - concurrencyPolicy: Forbid   (don't stack jobs if one runs long)
#   - Image: postgres:17-alpine   (has pg_dump)
#   - Env: PGHOST=postgres, PGUSER=quickticket, PGDATABASE=quickticket,
#          PGPASSWORD=quickticket  (Kubernetes Service DNS gives postgres its hostname)
#   - Writes dumps to /backups/quickticket_<UTC-timestamp>.dump  (-Fc format)
#   - Retention: keep only the 5 newest dumps, delete older ones
#   - Mounts the postgres-backups PVC from backup-storage.yaml on /backups
#   - successfulJobsHistoryLimit: 3, failedJobsHistoryLimit: 3  (don't fill etcd)
#
# Hints:
#   - Lecture 9 shows the CronJob structure; the `batch/v1` API has schedule +
#     jobTemplate, which wraps a PodSpec like a normal Deployment.
#   - The idiomatic retention one-liner inside the container's `command`:
#       ls -1t quickticket_*.dump | tail -n +6 | xargs -r rm
#     (tail -n +6 emits everything from line 6 onward — i.e. beyond the 5 newest)
#   - Remember `restartPolicy: OnFailure` on the Job template (Jobs require it).
#   - Test pg_dump works first — kubectl exec into backup-inspector and run
#     `apk add postgresql17-client && pg_dump -h postgres ...` to iterate fast.
```

Apply your manifest and trigger a run manually (don't wait 5 minutes for the schedule):

```bash
kubectl apply -f k8s/backup-cronjob.yaml
kubectl create job --from=cronjob/postgres-backup manual-1
kubectl wait --for=condition=Complete job/manual-1 --timeout=60s
kubectl logs job/manual-1
```

Verify retention by running 7 backups and confirming only the 5 newest remain:

```bash
for i in 2 3 4 5 6 7; do
  kubectl create job --from=cronjob/postgres-backup manual-$i
  kubectl wait --for=condition=Complete job/manual-$i --timeout=30s
done

kubectl exec deployment/backup-inspector -- ls -la /backups
```

### B.3: Proof of work

**Commit your `k8s/backup-cronjob.yaml` and updated `k8s/postgres.yaml`** to your fork.

**Paste into `submissions/lab9.md`:**

- Diff of `k8s/postgres.yaml` (PVC added).
- Re-run timestamps from 9.8 showing the new RTO with PVC (pod-restart-only, no `pg_restore` needed).
- Your `k8s/backup-cronjob.yaml` contents.
- Logs from `manual-7` showing the rotation kicked in (`removed '…_…dump'`).
- Output of `kubectl exec deployment/backup-inspector -- ls -la /backups` showing exactly 5 files after 7 runs.

---

## Cleanup

```bash
kubectl delete -f labs/lab8/mixedload.yaml
kubectl delete -f k8s/backup-cronjob.yaml        # your CronJob
kubectl delete -f labs/lab9/backup-storage.yaml  # PVC + inspector
pkill -f "port-forward.*5432" || true
```

---

## How to Submit

```bash
git switch -c feature/lab9
git add migrations/ k8s/postgres.yaml submissions/lab9.md
git commit -m "feat(lab9): add Alembic migrations and DB reliability submission"
git push -u origin feature/lab9
```

PR checklist:

```text
- [x] Task 1 done — Alembic migration under load + pg_dump/pg_restore cycle
- [ ] Task 2 done — disaster recovery RTO/RPO measurement
- [ ] Bonus Task done — PVC + automated CronJob backup with rotation
```

---

## Acceptance Criteria

### Task 1 (6 pts)
- ✅ Alembic initialized, baseline stamped, migration applied.
- ✅ Migration ran under load with 0 additional 5xx.
- ✅ Non-empty `pg_dump` backup created (valid TOC in `pg_restore --list`).
- ✅ Data loss simulated (DROP TABLE) and recovery shown (restore → API 200).
- ✅ Row counts shown for before / after drop / after restore.
- ✅ Written answer about RPO.

### Task 2 (4 pts)
- ✅ Full disaster → recovery cycle timed with wall-clock timestamps.
- ✅ Actual RTO in seconds and RPO gap in rows.
- ✅ Written observation that the new Postgres pod is empty (no PVC).

### Bonus Task (2.5 pts)
- ✅ PVC added to Postgres, data survives pod restart.
- ✅ Re-measured RTO is noticeably faster (no restore step needed).
- ✅ Student-written CronJob (`k8s/backup-cronjob.yaml`) runs pg_dump on schedule.
- ✅ Retention works: 7 runs → exactly 5 files remain; retention log visible.

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — Migrations + backup/restore | **6** | Alembic setup, migration under load, backup/restore cycle verified |
| **Task 2** — Disaster recovery | **4** | RTO/RPO measured with timestamps + no-PVC observation |
| **Bonus Task** — PVC + automated backup | **2.5** | PVC added, RTO improved, student-written CronJob with rotation verified |
| **Total** | **12.5** | 10 main + 2.5 bonus |

---

## Resources

<details>
<summary>📚 Documentation</summary>

- [Alembic Tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- [PostgreSQL pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html)
- [Kubernetes CronJob](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/)
- [Google SRE Book, Ch 26 — Data Integrity](https://sre.google/sre-book/data-integrity/)
- [Martin Fowler — Parallel Change](https://martinfowler.com/bliki/ParallelChange.html)

</details>

<details>
<summary>⚠️ Common Pitfalls</summary>

- **`pg_restore` not installed locally.** Don't try to run it on the host; use `kubectl exec` into the postgres pod (or `kubectl cp` the dump file in first).
- **`alembic upgrade` errors with "table already exists".** You forgot to `alembic stamp head` on the baseline. Stamp, then upgrade.
- **Port-forward drops.** `kubectl port-forward` needs to stay alive. Run it in a separate terminal with `kubectl port-forward svc/postgres 5432:5432` (no `&`).
- **Migration adds NOT NULL column without default.** That's a table rewrite + exclusive lock — will block all traffic on a big table. Use `nullable=True` for the lab.
- **Postgres pod recreated → data gone.** The default Postgres Deployment has no PVC. See Bonus Task. This is THE top cause of real-world stateful-service outages in K8s novice setups.
- **`mixedload` not running → no 5xx baseline.** Chaos and migration observations both need traffic. Apply `labs/lab8/mixedload.yaml`.
- **Events service serves stale errors after DB recovery.** Its connection pool caches broken handles. `kubectl rollout restart deployment/events` forces reconnect.

</details>
