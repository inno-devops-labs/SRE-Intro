"""Use the existing Alembic setup; derive credentials from the runner environment."""
import argparse
from datetime import datetime, timezone
import json
import os
import time

from alembic import command
from alembic.config import Config
from sqlalchemy import URL

p=argparse.ArgumentParser()
p.add_argument('action', choices=['upgrade','downgrade','current','history','heads'])
p.add_argument('revision', nargs='?', default='head')
p.add_argument('--database', default='quickticket')
args=p.parse_args()
if args.database != 'quickticket' and not args.database.startswith('lab12_'):
    raise ValueError('Only the lab application or lab12_ temporary databases are allowed')
os.environ['DATABASE_URL']=URL.create('postgresql+psycopg2',username=os.environ['PGUSER'],password=os.environ['PGPASSWORD'],host=os.environ['PGHOST'],database=args.database).render_as_string(hide_password=False)
os.chdir('/work')
config=Config('/work/alembic.ini')
start=datetime.now(timezone.utc).isoformat()
timer=time.perf_counter()
if args.action in ('upgrade','downgrade'):
    getattr(command,args.action)(config,args.revision)
else:
    getattr(command,args.action)(config)
print(json.dumps(dict(action=args.action,revision=args.revision,database=args.database,start=start,end=datetime.now(timezone.utc).isoformat(),elapsed_ms=round((time.perf_counter()-timer)*1000,3))))
