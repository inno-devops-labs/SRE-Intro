"""Exercise real PostgreSQL schema transitions in disposable databases.

Usage: DATABASE_URL=postgresql://... .venv/bin/python scripts/check_lab12_migrations.py
Requires CREATE DATABASE permission. Never migrates the database in DATABASE_URL.
"""
import os
from pathlib import Path
import subprocess
import sys
import uuid

import psycopg2
from psycopg2 import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_A_REF = "9815c67"
ADMIN_DSN = os.environ["DATABASE_URL"]
ADMIN = psycopg2.connect(ADMIN_DSN)
ADMIN.autocommit = True


def git_file(ref, path):
    return subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT, text=True)


def migrate(dsn, direction, revision):
    env = dict(os.environ, DATABASE_URL=dsn)
    subprocess.run([sys.executable, "-m", "alembic", direction, revision],
                   cwd=ROOT, env=env, check=True)


def check_api(dsn, source):
    # Import each historical application in a fresh interpreter/metrics registry.
    code = '''import json,sys
import psycopg2.pool
scope = {"__name__": "events_validation"}
exec(compile(sys.stdin.read(), "main.py", "exec"), scope)
pool = psycopg2.pool.ThreadedConnectionPool(1, 3, sys.argv[1])
scope["db_pool"] = pool
try:
    rows = scope["list_events"]()
    assert len(rows) == 5, rows
    assert all("date" in row for row in rows), rows
    assert scope["get_event"](1)["date"] == "2026-09-15T09:00:00+00:00"
    print(json.dumps({"events": len(rows), "event_1_date": scope["get_event"](1)["date"]}))
finally:
    pool.closeall()
'''
    subprocess.run([sys.executable, "-c", code, dsn], input=source, text=True, check=True)


def validate(name, fresh=False):
    with ADMIN.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {} template template0").format(sql.Identifier(name)))
    dsn = make_url(ADMIN_DSN).set(database=name).render_as_string(hide_password=False)
    try:
        legacy = git_file("cf2ad63", "app/events/main.py")
        deploy_a = git_file(DEPLOY_A_REF, "app/events/main.py")
        deploy_b = (ROOT / "app/events/main.py").read_text()
        seed = (ROOT / "app/seed.sql").read_text() if fresh else git_file("cf2ad63", "app/seed.sql")
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(seed)
        if fresh:
            migrate(dsn, "stamp", "head")
            check_api(dsn, deploy_b)
            print("PASS fresh final seed + stamped head + Deploy B")
            return
        migrate(dsn, "upgrade", "ddd061115f64")
        check_api(dsn, legacy)
        migrate(dsn, "upgrade", "12a_index_event_date")
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT indisvalid FROM pg_index WHERE indexrelid='idx_events_event_date'::regclass")
            assert cur.fetchone() == (True,)
        migrate(dsn, "downgrade", "ddd061115f64")
        migrate(dsn, "upgrade", "12a_index_event_date")
        migrate(dsn, "upgrade", "12b_add_scheduled_at")
        check_api(dsn, legacy)
        check_api(dsn, deploy_a)  # Every scheduled_at is NULL: exercises fallback.
        migrate(dsn, "upgrade", "12c_backfill_scheduled_at")
        check_api(dsn, deploy_a)
        check_api(dsn, deploy_b)
        migrate(dsn, "upgrade", "12d_drop_event_date")
        check_api(dsn, deploy_b)
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name='events'")
            columns = dict(cur.fetchall())
            assert "event_date" not in columns and columns["scheduled_at"] == "NO"
        migrate(dsn, "downgrade", "12c_backfill_scheduled_at")
        check_api(dsn, deploy_a)
        check_api(dsn, deploy_b)
        migrate(dsn, "downgrade", "12b_add_scheduled_at")
        check_api(dsn, deploy_a)
        migrate(dsn, "downgrade", "ddd061115f64")
        check_api(dsn, legacy)
        migrate(dsn, "upgrade", "head")
        check_api(dsn, deploy_b)
        print("PASS legacy -> all five transitions -> downgrades -> re-upgrade")
    finally:
        with ADMIN.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


try:
    suffix = uuid.uuid4().hex[:10]
    validate(f"lab12_check_{suffix}")
    validate(f"lab12_fresh_{suffix}", fresh=True)
finally:
    ADMIN.close()
