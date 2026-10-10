# QuickTicket migrations

Use Python 3.10+ (the tested environment uses 3.12).

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r migrations/requirements.txt
kubectl port-forward svc/postgres 15432:5432
# Another terminal, after verifying events/orders from app/seed.sql exist:
.venv/bin/alembic stamp 0001_baseline
.venv/bin/alembic upgrade head
.venv/bin/alembic current
.venv/bin/alembic history
```

The baseline is empty: it marks an existing schema, not a fresh-database initializer.
Do not stamp a database whose schema has not been checked. Restoring the full Lab 9
dump also restores alembic_version, so no stamp or re-seeding is needed afterwards.
DATABASE_URL overrides the demo connection string in alembic.ini.

Adding the nullable email column has no table rewrite, but ALTER TABLE still takes
an ACCESS EXCLUSIVE lock briefly. The online runner sets a 3s lock timeout and a
30s statement timeout so a migration fails rather than waiting indefinitely.
Retry after investigating blockers; never claim that metadata-only DDL takes no lock.

Downgrading to 0001_baseline drops email and any values in it. Test downgrade/upgrade
against an isolated restored database, not a database containing real email data.
