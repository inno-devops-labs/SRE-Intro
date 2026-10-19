# QuickTicket migrations

Use Python 3.10+ (the tested environment uses 3.12).

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r migrations/requirements.txt
kubectl port-forward svc/postgres 15432:5432
# Another terminal, for an existing legacy database already stamped in Lab 9:
.venv/bin/alembic upgrade 0003_event_date_index
.venv/bin/alembic current
.venv/bin/alembic history
```

The baseline is empty: it marks the legacy schema, not a fresh-database initializer.
On a verified unversioned **legacy** schema with `event_date`, stamp `0001_baseline`
before upgrading. Never stamp that revision on the final scheduled_at schema.
Do not stamp a database whose schema has not been checked. Restoring the full Lab 9
dump also restores alembic_version, so no stamp or re-seeding is needed afterwards.
DATABASE_URL overrides the demo connection string in alembic.ini.

After Lab 12, `app/seed.sql` bootstraps a **fresh empty** database directly on the
final schema (`scheduled_at`, email, and `idx_events_scheduled_at`). Verify those
objects and the orders table, then stamp `0006_drop_event_date`. Do not replay
the legacy migrations against this already-final seed, or run the seed over a
populated database. The startup seed is not idempotent for its event rows.

Live rename order: upgrade to `0004_add_scheduled_at`, deploy the lab12-a image
(COALESCE fallback reads), upgrade to `0005_backfill_scheduled_at`, deploy lab12-b
(scheduled_at-only reads), wait until **every** events pod uses B, then upgrade
to `0006_drop_event_date`. Do not run `upgrade head` across these deploy gates.
There is no runtime event-date writer in QuickTicket; control external writers
and seed jobs during the transition. The public API retains its existing `date` key.

Adding the nullable email column has no table rewrite, but ALTER TABLE still takes
an ACCESS EXCLUSIVE lock briefly. The online runner sets a 3s lock timeout and a
30s statement timeout at connection scope so they also survive autocommit blocks.
Transactions are per migration; CONCURRENTLY uses an explicit autocommit block.
Retry after investigating blockers; never claim that metadata-only DDL takes no lock.

Downgrading to 0001_baseline drops email and any values in it. Test downgrade/upgrade
against an isolated restored database, not a database containing real email data.

The final contract revision builds a replacement scheduled_at index concurrently
before dropping the legacy column (which also drops its old index). Its downgrade
restores/backfills the old column and recreates the old index; this is schema
compatibility, not a safe production rollback plan by itself. Coordinate writer
versions and verify data parity before rolling application versions backwards.
After interrupted concurrent DDL, inspect `pg_index.indisvalid`; IF NOT EXISTS
does not repair an invalid index.
