# QuickTicket schema recovery after Lab 12

The current Events image reads `scheduled_at`; API responses still call it `date`.
Historical Lab 9 results describe the old `event_date` schema and remain unchanged.

## Upgrade an existing database

Verify the target database, current Alembic revision, image IDs and available
inventory first. Create a custom-format dump, copy it outside the database pod,
verify its SHA256/TOC and restore it into a separate test database. Never overwrite
the working database merely to test a backup.

Apply `12a0001` (concurrent old-column index), then `12a0002` (nullable expand).
Deploy A image `quickticket-events:lab12-a-4f0dd1dfda5e`, which reads through
COALESCE in both queries and list ordering. Apply `12a0003` (backfill, NOT NULL).
Deploy B `quickticket-events:lab12-b-a0bc4751a840` and wait for **all** A/legacy
pods, including terminating pods, to disappear. Check active readers/writers,
backup validity and checkout again before `12a0004` drops `event_date`.
Apply `12a0005` for the concurrent replacement index. Use exact revision IDs,
not one `upgrade head` across the live compatibility transitions.

Both local immutable images must be built from their recorded source commits
and imported into each k3d node; `imagePullPolicy: Never` is not portable to a
different cluster without this step. Do not reuse the same tag for new contents.

## Fresh database

Use `app/seed.sql` **only on an empty database**. It creates the final schema,
including nullable email and the scheduled_at index. Its seed INSERT is not a
safe deduplication mechanism on an already populated database.

Do not run old event_date migrations against this fresh schema. Compare all
columns/defaults/nullability, constraints and indexes to a separate database
upgraded through the complete migration chain. `scripts/lab12/bootstrap_check.py`
demonstrates the comparison for the explicitly named temporary databases and
only stamps `12a0005` after equality; it must not be blindly repointed at production.

## Restore or rollback

Stop writers for a coordinated restore. Confirm target DB, backup timestamp,
TOC and a successful isolated restore before using `pg_restore --clean --if-exists`.
Match the application to the schema in the dump: an old dump needs its compatible
old readers and the staged upgrade above, not an immediate Deploy B restart.
Preserve the PostgreSQL and backup PVCs; PVC survival is not a substitute for
off-node backups or a tested recovery procedure.

Test downgrades in a separate database first. M3 downgrade reconstructs the old
date from scheduled_at, but rollback is safe only if every writer maintains the
required fields, data meanings remain compatible and old/new consumers agree.
After any recovery verify Alembic, index validity, all pod image IDs, health,
reserve/pay/confirmed order and notification. Restore ArgoCD automation only
after Git and the intended schema/application state agree.
