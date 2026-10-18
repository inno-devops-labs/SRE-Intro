"""Compare a fresh seed to a database upgraded through every migration.

Only stamp the tested fresh schema after columns, constraints and indexes match.
This tool never runs against the working quickticket database.
"""
import json
import os

from alembic import command
from alembic.config import Config
import psycopg2
from sqlalchemy import URL


def signature(database):
    with psycopg2.connect(host=os.environ['PGHOST'],user=os.environ['PGUSER'],password=os.environ['PGPASSWORD'],dbname=database) as conn:
        with conn.cursor() as cur:
            data={}
            for label, sql in [
                ('columns',"SELECT table_name,column_name,data_type,character_maximum_length,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' AND table_name IN ('events','orders') ORDER BY table_name,column_name"),
                ('indexes',"SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename IN ('events','orders') ORDER BY tablename,indexname"),
                ('constraints',"SELECT conrelid::regclass::text,conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE connamespace='public'::regnamespace AND conrelid IN ('events'::regclass,'orders'::regclass) ORDER BY conrelid::regclass::text,conname")]:
                cur.execute(sql);data[label]=cur.fetchall()
            return data


fresh=signature('lab12_fresh')
upgraded=signature('lab12_verify')
if fresh!=upgraded:
    print(json.dumps({'match':False,'fresh':fresh,'upgraded':upgraded},default=str))
    raise SystemExit('Do not stamp: fresh schema differs from the fully migrated schema')
os.environ['DATABASE_URL']=URL.create('postgresql+psycopg2',username=os.environ['PGUSER'],password=os.environ['PGPASSWORD'],host=os.environ['PGHOST'],database='lab12_fresh').render_as_string(hide_password=False)
os.chdir('/work')
command.stamp(Config('/work/alembic.ini'),'12a0005')
print(json.dumps({'match':True,'verified_components':list(fresh),'fresh_database':'lab12_fresh','stamped_revision':'12a0005'}))
