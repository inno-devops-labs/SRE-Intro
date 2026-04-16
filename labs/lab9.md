# Lab 9 — Stateful Services & DB Reliability

![difficulty](https://img.shields.io/badge/difficulty-intermediate-yellow)
![topic](https://img.shields.io/badge/topic-DB%20Reliability-blue)
![points](https://img.shields.io/badge/points-10%2B2.5-orange)
![tech](https://img.shields.io/badge/tech-Alembic%20%2B%20pg__dump-informational)

> **Goal:** Run database migrations with Alembic, perform backup and restore, simulate data loss and recovery, verify data integrity.
> **Deliverable:** A PR from `feature/lab9` with migration files and `submissions/lab9.md`. Submit PR link via Moodle.

---

## Overview

In this lab you will practice:
- Setting up Alembic for database migrations
- Running a migration that adds a column (zero-downtime, with load running)
- Creating a pg_dump backup
- Simulating data loss (dropping a table)
- Restoring from backup and verifying integrity

> **You do all the work.** Set up Alembic, write the migration, execute backup/restore.

---

## Project State

**You should have from previous labs:**
- QuickTicket on docker-compose or k3d with PostgreSQL
- Understanding of failure modes from chaos experiments (Lab 8)

**This lab adds:**
- Alembic migration framework for schema management
- Backup and restore procedures
- Disaster recovery experience

---

## Task 1 — Migrations & Backup/Restore (6 pts)

**Objective:** Set up Alembic, run a migration under load, create a backup, simulate data loss, restore and verify.

### 9.1: Set up Alembic

Start QuickTicket and ensure it has data:

```bash
cd app/
docker compose up -d --build
# Verify: events should exist
curl -s http://localhost:3080/events | python3 -c "import sys,json; print(f'{len(json.load(sys.stdin))} events')"
```

Install Alembic locally (or in a virtualenv):

```bash
pip install alembic psycopg2-binary sqlalchemy
```

Initialize Alembic in the project root:

```bash
cd ..  # back to repo root
alembic init migrations
```

Edit `alembic.ini` — set the database URL:
```ini
sqlalchemy.url = postgresql://quickticket:quickticket@localhost:5432/quickticket
```

### 9.2: Create and run a migration

Create a migration that adds an `email` column to the `events` table:

```bash
alembic revision -m "add email column to events"
```

Edit the generated file in `migrations/versions/`:

```python
def upgrade():
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))

def downgrade():
    op.drop_column('events', 'email')
```

Run the migration with the load generator running (zero-downtime test):

```bash
# Start load in background
./app/loadgen/run.sh 3 60 &

# Apply migration
alembic upgrade head

# Check result
docker exec -it $(docker compose -f app/docker-compose.yaml ps -q postgres) \
  psql -U quickticket -d quickticket -c "\d events"
```

Verify: the `email` column should appear in the table schema. The loadgen should show 0% errors (migration didn't break anything).

### 9.3: Create a backup

```bash
# pg_dump — create a backup
docker exec $(docker compose -f app/docker-compose.yaml ps -q postgres) \
  pg_dump -U quickticket -Fc quickticket > backup.dump

ls -lh backup.dump
echo "Backup created: $(date)"
```

Verify the backup is not empty:

```bash
docker exec -i $(docker compose -f app/docker-compose.yaml ps -q postgres) \
  pg_restore --list /dev/stdin < backup.dump | head -10
```

### 9.4: Simulate data loss

**This is the scary part.** Drop the orders table (simulating accidental data loss):

```bash
# First, create some orders
curl -s -X POST http://localhost:3080/events/1/reserve \
  -H "Content-Type: application/json" -d '{"quantity":1}' | python3 -m json.tool
# ... pay for it to create an order

# Count orders before
docker exec $(docker compose -f app/docker-compose.yaml ps -q postgres) \
  psql -U quickticket -d quickticket -c "SELECT count(*) FROM orders;"

# DROP THE TABLE
docker exec $(docker compose -f app/docker-compose.yaml ps -q postgres) \
  psql -U quickticket -d quickticket -c "DROP TABLE orders CASCADE;"

# Verify it's gone
curl -s http://localhost:3080/events | head -1
# Should show errors or missing data
```

### 9.5: Restore from backup

```bash
# Restore
docker exec -i $(docker compose -f app/docker-compose.yaml ps -q postgres) \
  pg_restore -U quickticket -d quickticket --clean --if-exists /dev/stdin < backup.dump 2>&1

# Verify data is back
docker exec $(docker compose -f app/docker-compose.yaml ps -q postgres) \
  psql -U quickticket -d quickticket -c "SELECT count(*) FROM orders;"

docker exec $(docker compose -f app/docker-compose.yaml ps -q postgres) \
  psql -U quickticket -d quickticket -c "SELECT count(*) FROM events;"

# Verify API works
curl -s http://localhost:3080/events | python3 -c "import sys,json; print(f'{len(json.load(sys.stdin))} events')"
```

### 9.6: Proof of work

**Migration files** go in `migrations/` in your fork.

**Paste into `submissions/lab9.md`:**
1. `alembic history` output showing your migration
2. `\d events` output showing the new `email` column
3. Loadgen output during migration (should show 0% errors)
4. `ls -lh backup.dump` — backup file exists and is not empty
5. `SELECT count(*) FROM orders` before data loss, after drop, and after restore
6. API response after restore (events working again)
7. Answer: "What's the RPO of your current setup? How would you improve it?"

<details>
<summary>💡 Hints</summary>

- `alembic.ini` needs the correct PostgreSQL URL with host `localhost` (since you're running alembic from your machine, not inside a container)
- If postgres port is not 5432 locally, check `docker compose ps` for the published port
- `pg_dump -Fc` creates a compressed format — fastest to restore. `-Fc` output is NOT human-readable (use `pg_restore --list` to inspect)
- `pg_restore --clean --if-exists` drops existing objects before restoring — safe for re-running
- The migration adds a nullable column — this is safe (no table rewrite, no lock). Adding a NOT NULL column without default would lock the table!
- If `alembic upgrade` fails with "relation already exists", you may need `alembic stamp head` to mark current state

</details>

---

## Task 2 — Disaster Recovery Under Load (4 pts)

> ⏭️ This task is optional. Skipping it will not affect future labs.

**Objective:** Measure your actual RTO and RPO by performing the full disaster → recovery cycle while the application is serving traffic.

### 9.7: Measure recovery time

1. Start the loadgen: `./app/loadgen/run.sh 3 300 &`
2. Record the current time
3. Simulate disaster: stop the postgres container
   ```bash
   docker compose -f app/docker-compose.yaml stop postgres
   ```
4. Note: how long before the API starts returning errors?
5. Restore: start postgres, verify data
   ```bash
   docker compose -f app/docker-compose.yaml start postgres
   ```
6. Note: how long until the API works again?
7. Record the loadgen error stats

### 9.8: Calculate RTO and RPO

Based on your experiment:
- **Actual RTO** = time from postgres down to API working again
- **Actual RPO** = time since your last pg_dump backup (from Task 1)

**Paste into `submissions/lab9.md`:**
- Timestamps: disaster → first error → postgres restarted → first successful request
- Actual RTO and RPO values
- Loadgen stats showing the error window
- Answer: "With your current setup, how many requests failed during the recovery? How would you reduce this?"

---

## Bonus Task — Automated Backup CronJob (2.5 pts)

> 🌟 For those who want extra challenge and experience.

**Objective:** Set up automated periodic backups and verify they work.

### B.1: Create a backup script

Create `scripts/backup.sh`:

```bash
#!/bin/bash
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="/backups/quickticket_${TIMESTAMP}.dump"

pg_dump -U quickticket -Fc quickticket > "$BACKUP_FILE"

# Keep only last 5 backups
ls -t /backups/*.dump | tail -n +6 | xargs rm -f

echo "Backup created: $BACKUP_FILE ($(du -sh $BACKUP_FILE | cut -f1))"
```

### B.2: Schedule with K8s CronJob (if on K8s) or cron (if docker-compose)

For docker-compose, add a simple cron entry or a new service. For K8s, create a CronJob manifest.

### B.3: Verify backup rotation

Run the backup 6 times, verify only the latest 5 are kept.

**Paste into `submissions/lab9.md`:**
- Your backup script
- Evidence of automated execution
- `ls -la /backups/` showing rotation (5 files max)

---

## How to Submit

```bash
git switch -c feature/lab9
git add migrations/ submissions/lab9.md
git commit -m "feat(lab9): add Alembic migrations and backup/restore documentation"
git push -u origin feature/lab9
```

PR checklist:
```text
- [x] Task 1 done — Alembic migration, pg_dump backup/restore cycle
- [ ] Task 2 done — disaster recovery with RTO/RPO measurement
- [ ] Bonus Task done — automated backup script with rotation
```

---

## Acceptance Criteria

### Task 1 (6 pts)
- ✅ Alembic initialized and migration created
- ✅ Migration ran under load with 0% errors
- ✅ Backup created (non-empty pg_dump)
- ✅ Data loss simulated (table dropped)
- ✅ Restore successful (data recovered, API works)
- ✅ Written answer about RPO

### Task 2 (4 pts)
- ✅ Full disaster → recovery cycle measured under load
- ✅ Actual RTO and RPO calculated
- ✅ Loadgen stats showing error window

### Bonus Task (2.5 pts)
- ✅ Backup script with rotation
- ✅ Evidence of automated execution
- ✅ Rotation verified (max 5 files)

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — Migrations + backup/restore | **6** | Alembic setup, migration under load, backup/restore cycle verified |
| **Task 2** — Disaster recovery measurement | **4** | RTO/RPO measured with timestamps under load |
| **Bonus Task** — Automated backup | **2.5** | Script, automation, rotation verified |
| **Total** | **12.5** | 10 main + 2.5 bonus |

---

## Resources

<details>
<summary>📚 Documentation</summary>

- [Alembic Tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- [PostgreSQL pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html)
- [Google SRE Book, Ch 26 — Data Integrity](https://sre.google/sre-book/data-integrity/)
- [Martin Fowler — Parallel Change](https://martinfowler.com/bliki/ParallelChange.html)

</details>

<details>
<summary>⚠️ Common Pitfalls</summary>

- **alembic.ini URL** — use `localhost` (not `postgres`), since alembic runs from your machine
- **pg_restore fails with "already exists"** — use `--clean --if-exists` flags
- **Migration locks table** — adding a NOT NULL column without default locks. Always use `nullable=True` for new columns
- **Backup is empty** — check pg_dump version matches PostgreSQL server version (GitLab's exact mistake!)
- **After restore, app still errors** — restart the events service to reconnect to the DB

</details>
