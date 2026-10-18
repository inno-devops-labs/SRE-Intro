"""Exercise migrations on a disposable DB, never downgrade the live application.

Run with DATABASE_URL pointing at the port-forwarded admin database. The role
must be allowed to create databases. Only this script's unique DB is removed.
"""
import os
from pathlib import Path
import subprocess
import uuid
from urllib.parse import urlsplit, urlunsplit
import psycopg2
from psycopg2 import sql

admin_url = os.environ['DATABASE_URL']
parts = urlsplit(admin_url)
name = 'lab12_verify_' + uuid.uuid4().hex[:12]
url = urlunsplit(parts._replace(path='/' + name))
admin = psycopg2.connect(admin_url)
admin.autocommit = True
conn = None
try:
    with admin.cursor() as cur:
        cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    env = {**os.environ, 'DATABASE_URL': url}
    def alembic(*args):
        subprocess.run(['.venv/bin/alembic', *args], env=env, check=True)
    conn = psycopg2.connect(url)
    conn.autocommit = True
    # Historical bootstrap: baseline revision intentionally contains no DDL.
    seed = subprocess.check_output(['git','show','e49a73b:app/seed.sql'], text=True)
    with conn.cursor() as cur:
        cur.execute(seed)
    alembic('stamp', '1536dcffc4c9')
    alembic('upgrade', 'head')
    with conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='events'")
        columns = {r[0] for r in cur.fetchall()}
        assert 'scheduled_at' in columns and 'event_date' not in columns
        cur.execute('SELECT count(*) FROM events WHERE scheduled_at IS NOT NULL')
        assert cur.fetchone()[0] == 5
        cur.execute("SELECT indisvalid FROM pg_index WHERE indexrelid='idx_events_scheduled_at'::regclass")
        assert cur.fetchone()[0]
    print('UPGRADE: final column, all five dates and valid index verified', flush=True)
    alembic('downgrade', 'a68dfd8aa03c')
    with conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM events WHERE event_date IS NOT NULL')
        assert cur.fetchone()[0] == 5
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='events' AND column_name='scheduled_at'")
        assert cur.fetchone() is None
    print('DOWNGRADE: original dates restored; new column removed', flush=True)
    alembic('upgrade', 'head')
    print('RE-UPGRADE: all four migrations succeeded again', flush=True)
finally:
    if conn is not None:
        conn.close()
    with admin.cursor() as cur:
        cur.execute(sql.SQL('DROP DATABASE IF EXISTS {}').format(sql.Identifier(name)))
    admin.close()
    print('Disposable verification database removed', flush=True)
