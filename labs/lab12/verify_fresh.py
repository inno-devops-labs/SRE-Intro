"""Verify final seed plus final events service against a disposable database."""
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit, urlunsplit
import uuid
import psycopg2
from psycopg2 import sql

admin_url = os.environ['DATABASE_URL']
name = 'lab12_fresh_' + uuid.uuid4().hex[:12]
url = urlunsplit(urlsplit(admin_url)._replace(path='/' + name))
admin = psycopg2.connect(admin_url)
admin.autocommit = True
conn = None
try:
    with admin.cursor() as cur:
        cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    conn = psycopg2.connect(url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(Path('app/seed.sql').read_text())
        cur.execute('SELECT count(*) FROM events WHERE scheduled_at IS NOT NULL')
        assert cur.fetchone()[0] == 5
        cur.execute("SELECT indisvalid FROM pg_index WHERE indexrelid='idx_events_scheduled_at'::regclass")
        assert cur.fetchone()[0]
    subprocess.run(['.venv/bin/alembic','stamp','b12000000004'], env={**os.environ,'DATABASE_URL':url}, check=True)
    check = '''import main
main.startup()
rows = main.list_events()
assert len(rows) == 5
assert all('date' in r and r['date'] for r in rows)
assert main.get_event(1)['date'] == '2026-09-15T09:00:00+00:00'
print('Fresh seed: five events; list/detail date API unchanged; startup successful')
main.db_pool.closeall()
'''
    subprocess.run(['kubectl','exec','deployment/events','--','env',f'DB_NAME={name}','python','-c',check],check=True)
finally:
    if conn is not None:
        conn.close()
    with admin.cursor() as cur:
        cur.execute(sql.SQL('DROP DATABASE IF EXISTS {}').format(sql.Identifier(name)))
    admin.close()
    print('Disposable fresh-install database removed', flush=True)
